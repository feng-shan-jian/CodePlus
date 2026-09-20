"""Preparation owns its worker/session, never the shared service's lifetime."""

import asyncio
from dataclasses import replace
import json
import threading

import pytest

from codeplus.config import KnowledgeConfig
from codeplus.knowledge.citations import KnowledgeContext
from codeplus.knowledge.runtime import ManagedRuntime, compose_path
from codeplus.knowledge.service import KnowledgeService
from test_knowledge_service import MemoryStore, TinyEmbedding


def test_prepare_reports_model_loading_only_on_first_entry(tmp_path):
    service = KnowledgeService(KnowledgeConfig(enabled=True, data_dir=str(tmp_path)))
    service._store, service.embedding = MemoryStore(), TinyEmbedding()
    try:
        first, repeated = [], []
        service.prepare(first.append)
        service.prepare(repeated.append)
        assert len(first) == 3 and first[1].startswith("正在加载模型")
        assert repeated == ["正在连接服务", "已就绪"]
    finally:
        service.close()


@pytest.mark.asyncio
async def test_prepare_failure_retry_shared_waiter_and_close(tmp_path, monkeypatch):
    context = KnowledgeContext(KnowledgeConfig(enabled=True, data_dir=str(tmp_path)))
    service = context.service
    service._store = MemoryStore()
    entered, release = threading.Event(), threading.Event()
    loads = []

    class Embedding(TinyEmbedding):
        @property
        def tokenizer(self):
            loads.append(1)
            if len(loads) == 1:
                raise OSError("download unavailable")
            entered.set()
            assert release.wait(5)
            return super().tokenizer

    service.embedding = Embedding()
    with pytest.raises(RuntimeError, match="/knowledge prepare"):
        await context.prepare()
    progress = []
    first = asyncio.create_task(context.prepare(progress.append))
    assert await asyncio.to_thread(entered.wait, 5)
    second = asyncio.create_task(context.prepare())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    closed = asyncio.create_task(context.aclose())
    await asyncio.sleep(0.01)
    assert not closed.done() and context.preparing
    release.set()
    await asyncio.gather(second, closed)
    assert len(loads) == 2 and progress[-1] == "已就绪"
    assert service._closed
    with pytest.raises(RuntimeError, match="closed"):
        await context.prepare()


def test_external_unavailable_never_starts_compose(tmp_path, monkeypatch):
    service = KnowledgeService(KnowledgeConfig(enabled=True, data_dir=str(tmp_path)))
    def unavailable():
        raise OSError("offline")
    monkeypatch.setattr(service, "_connect", unavailable)
    monkeypatch.setattr(ManagedRuntime, "hold", lambda self: pytest.fail("external service must not launch WSL"))
    try:
        with pytest.raises(RuntimeError, match="managed_local"):
            service.prepare()
        service.config = replace(service.config, managed_local=True, milvus_uri="http://example.org:19530")
        with pytest.raises(ValueError, match="外部 Milvus"):
            service.prepare()
        service.config = replace(service.config, milvus_uri="http://127.0.0.1:19530")
        def missing_dependency():
            raise ModuleNotFoundError("pymilvus")
        monkeypatch.setattr(service, "_connect", missing_dependency)
        with pytest.raises(RuntimeError, match="uv sync --extra knowledge"):
            service.prepare()
    finally:
        service.close()


@pytest.mark.parametrize("image", ["unrelated/image:latest", "milvusdb/milvus:v3.0.1"])
def test_managed_identity_and_start_uses_compose_healthcheck(tmp_path, monkeypatch, image):
    runtime = ManagedRuntime()
    calls = []
    monkeypatch.setattr(runtime, "hold", lambda: None)
    monkeypatch.setattr("codeplus.knowledge.runtime.gettempdir", lambda: str(tmp_path))
    container = {"Config": {"Image": image, "Labels": {
        "com.docker.compose.project": "codeplus-knowledge", "com.docker.compose.service": "standalone"}},
        "HostConfig": {"PortBindings": {"19530/tcp": [{"HostIp": "127.0.0.1", "HostPort": "19530"}]}}}
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["docker", "ps"]:
            return "id"
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([container])
        return str(compose_path())
    monkeypatch.setattr(runtime, "_run", run)
    attempts = []
    def connect():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("not started")
    progress = []
    if image.startswith("unrelated"):
        with pytest.raises(RuntimeError, match="身份不匹配"):
            runtime.start(progress.append, connect)
        assert not any("up" in command for command in calls)
    else:
        runtime.start(progress.append, connect)
        start = next(command for command in calls if "up" in command)
        assert "--no-recreate" in start and "--wait" in start
        assert progress == ["正在启动服务"] and len(attempts) == 2

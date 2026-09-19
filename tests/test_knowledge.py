"""Opt-in real services; absent opt-in is reported as skipped, never a pass."""

import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest


def test_disabled_knowledge_has_no_optional_imports_or_network(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "providers:\n  - {name: test, protocol: openai, base_url: 'http://localhost', model: test}\n",
        encoding="utf-8",
    )
    # Fresh interpreter also catches eager imports hidden by pytest's module cache.
    subprocess.run([sys.executable, "-c", """
import builtins, socket, sys
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    assert name.split('.')[0] not in {'torch', 'transformers', 'pymilvus', 'huggingface_hub'}, name
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
def reject_network(*args, **kwargs):
    raise AssertionError('Unexpected network connection')
socket.socket.connect = reject_network
from pathlib import Path
from codeplus.config import load_config
from codeplus.knowledge.embedding import LocalEmbedding
import codeplus.__main__
config = load_config(Path(sys.argv[1]))
assert not config.knowledge.enabled
try:
    LocalEmbedding(config.knowledge)
except ValueError as exc:
    assert 'disabled' in str(exc)
else:
    raise AssertionError('Disabled embedding was accepted')
""", str(config)], check=True, timeout=30)


@pytest.mark.skipif(not os.getenv("CODEPLUS_TEST_MILVUS_URI"), reason="real Milvus not requested")
def test_milvus_flat_cosine_and_persistence():
    from pymilvus import DataType, MilvusClient, __version__

    uri = os.environ["CODEPLUS_TEST_MILVUS_URI"]
    restart = os.getenv("CODEPLUS_TEST_MILVUS_RESTART") == "1"
    if restart:
        assert uri == "http://127.0.0.1:19530", "Restart test is only for the local Compose project"
    client = MilvusClient(uri=uri, timeout=20)
    name = f"codeplus_test_{uuid.uuid4().hex}"
    try:
        print(f"Milvus server={client.get_server_version()} SDK={__version__} collection={name}")
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=3)
        index = client.prepare_index_params()
        index.add_index("dense", index_type="FLAT", metric_type="COSINE")
        client.create_collection(name, schema=schema, index_params=index, consistency_level="Strong")
        client.insert(name, [
            {"id": 1, "dense": [1.0, 0.0, 0.0]},
            {"id": 2, "dense": [0.6, 0.8, 0.0]},
            {"id": 3, "dense": [0.0, 1.0, 0.0]},
        ])
        client.flush(name)
        for phase in ("before", "after") if restart else ("before",):
            if phase == "after":
                client.close()
                compose_file = Path(__file__).resolve().parents[1] / "deployment/knowledge/compose.yaml"
                linux_path = subprocess.check_output(
                    ["wsl", "-d", "Ubuntu-24.04", "--", "wslpath", "-a", compose_file.as_posix()],
                    text=True,
                ).strip()
                command = ["wsl", "-d", "Ubuntu-24.04", "--", "docker", "compose", "-f", linux_path]
                subprocess.run([*command, "stop"], check=True, timeout=120)
                subprocess.run([*command, "up", "-d", "--wait", "--wait-timeout", "240"], check=True, timeout=300)
                client = MilvusClient(uri=uri, timeout=20)
            client.load_collection(name)
            hits = client.search(name, [[1.0, 0.0, 0.0]], anns_field="dense", limit=3,
                                 search_params={"metric_type": "COSINE"})[0]
            assert [hit["id"] for hit in hits] == [1, 2, 3]
            assert [hit["distance"] for hit in hits] == pytest.approx([1.0, 0.6, 0.0], abs=1e-5)
            assert len(client.query(name, filter="id >= 0", output_fields=["id"])) == 3
            print(f"{phase} restart: ids={[h['id'] for h in hits]}, scores={[h['distance'] for h in hits]}")
    finally:
        try:
            if client.has_collection(name):
                client.drop_collection(name)
            assert not client.has_collection(name)
        finally:
            client.close()


@pytest.mark.skipif(os.getenv("CODEPLUS_TEST_EMBEDDING") != "1", reason="real Qwen model not requested")
def test_qwen_chinese_embedding():
    from codeplus.config import KnowledgeConfig
    from codeplus.knowledge.embedding import LocalEmbedding

    encoder = LocalEmbedding(KnowledgeConfig(enabled=True))
    assert encoder._model is None
    started = time.perf_counter()
    encoder._load()
    load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    vectors = encoder.encode_documents([
        "员工出差后需要提交发票和差旅申请单，财务审核通过后报销。",
        "番茄喜欢充足的阳光，种植时应保持土壤湿润并定期施肥。",
    ])
    query = encoder.encode_query("出差费用报销需要提供哪些材料？")
    for vector in [*vectors, query]:
        assert len(vector) == 1024
        assert all(math.isfinite(value) for value in vector)
        assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0, abs=1e-5)
    scores = [sum(a * b for a, b in zip(query, vector)) for vector in vectors]
    assert scores[0] > scores[1]
    print(f"Qwen load={load_seconds:.3f}s encode={time.perf_counter() - started:.3f}s cosine={scores}")
    encoder.config.max_input_tokens = 1
    with pytest.raises(ValueError, match="exceeds max_input_tokens"):
        encoder.encode_documents(["这段中文不能静默截断后再生成向量。"])
    del encoder
    with pytest.raises(ValueError, match="embedding_dimension does not match"):
        LocalEmbedding(KnowledgeConfig(enabled=True, embedding_dimension=768)).encode_query("出差报销")

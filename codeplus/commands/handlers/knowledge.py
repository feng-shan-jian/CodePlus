from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import url2pathname

from codeplus.commands.registry import Command, CommandContext, CommandType


USAGE = '/knowledge prepare | create <name> | use <id> | import "<path>" | status | sources | reimport <doc_id> | remove <doc_id> | retry | off | open <citation_id> [offset]'


def _source_path(uri: str) -> Path:
    source = urlsplit(uri)
    if source.scheme != "file" or source.query or source.fragment:
        raise ValueError("文档未保存有效的本地源路径；请使用 /knowledge import <path>")
    path = source.path
    if source.netloc and source.netloc.lower() != "localhost":
        path = f"//{source.netloc}{path}"
    result = Path(url2pathname(path))
    if not result.is_absolute():
        raise ValueError("文档源路径不是绝对路径；请使用 /knowledge import <path>")
    return result


def _import_failure(service, kb_id: str, file: Path, exc: Exception) -> dict:
    failure = {"path": str(file), "reason": str(exc) or type(exc).__name__, "kind": "import"}
    try:
        status = service.status(kb_id)
        pending = [doc for doc in status["documents"] if doc.get("pending_operation")]
        if any(_source_path(doc["source_uri"]) == file.resolve() for doc in pending):
            failure["kind"] = "retry"
        elif pending or status.get("pending_operation") or status["state"] != "READY":
            failure["reason"] += "；请先 /knowledge retry 恢复知识库，再重新导入此文件"
    except Exception as status_error:
        failure["reason"] += f"；无法确认恢复状态：{status_error}，请先 /knowledge status 核对"
    return failure


async def _import_files(ctx: CommandContext, service, kb_id: str, files: list[Path]) -> None:
    progress = {"kb_id": kb_id, "processed": 0, "total": len(files),
                "succeeded": 0, "unchanged": 0, "failed": 0, "failures": [],
                "completed_path": None, "outcome": None}
    callback = ctx.config.get("knowledge_progress")

    def report():
        if callback is not None:
            # Consumers may retain snapshots for recovery; never expose mutable counters.
            callback({**progress, "failures": [dict(failure) for failure in progress["failures"]]})

    ctx.ui.add_system_message(f"开始导入，共 {len(files)} 个文件。")
    report()
    for file in files:
        try:
            result = await asyncio.to_thread(service.import_document, kb_id, file)
        except Exception as exc:
            failure = await asyncio.to_thread(_import_failure, service, kb_id, file, exc)
            progress["failures"].append(failure)
            outcome = "failed"
        else:
            outcome = "unchanged" if result["unchanged"] else "succeeded"
        progress[outcome] += 1
        progress["processed"] += 1
        progress.update(completed_path=str(file), outcome=outcome)
        report()

    summary = (f"导入完成：已处理 {progress['processed']}/{progress['total']}，"
               f"成功 {progress['succeeded']}，未变化 {progress['unchanged']}，失败 {progress['failed']}。")
    for failure in progress["failures"]:
        recovery = "/knowledge retry" if failure["kind"] == "retry" else f'/knowledge import "{failure["path"]}"'
        summary += f"\n导入失败：{failure['path']}：{failure['reason']}\n恢复：{recovery}"
    ctx.ui.add_system_message(summary)


async def handle_knowledge(ctx: CommandContext) -> None:
    knowledge = ctx.config.get("knowledge")
    if knowledge is None:
        ctx.ui.add_system_message("此入口尚未接入 knowledge")
        return
    parts = ctx.args.strip().split(None, 1)
    sub = parts[0] if parts else ""
    # The remainder is one name/path, not POSIX shell words: preserve Windows backslashes.
    arg = parts[1].strip() if len(parts) > 1 else ""
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
        arg = arg[1:-1]
    if sub == "off":
        ctx.config["set_knowledge_binding"](None)
        ctx.ui.add_system_message("知识库模式已关闭，已清空当前回答上下文；历史记录仍可恢复/核对。")
        return
    if sub == "open":
        match = re.fullmatch(r"\[?(K:([a-f0-9]{32}):([a-f0-9]{64}))\]?(?:\s+(\d+))?", arg)
        if not match:
            raise ValueError("用法: /knowledge open K:<kb_id>:<chunk_id> [offset]")
        sources = await asyncio.to_thread(knowledge.service.source_context, match[2], match[3])
        offset = int(match[4] or 0)
        for source in sources:
            text = source["text"]
            start = offset if source["id"] == match[3] else 0
            if start >= len(text) and start:
                raise ValueError("offset 超过片段末尾")
            source.update(text=text[start:start + 6000], offset=start,
                          next_offset=start + 6000 if len(text) > start + 6000 else None,
                          truncated=len(text) > start + 6000)
            source["citation_id"] = f"K:{match[2]}:{source['id']}"
        ctx.ui.add_system_message(json.dumps({"historical_sources": sources}, ensure_ascii=False, indent=2))
        return
    if sub in {"", "prepare"}:
        menu = ctx.config.get("show_knowledge_menu") if not sub else None
        try:
            await knowledge.prepare(ctx.ui.add_system_message)
        finally:
            if menu is not None:
                menu()
        if not sub and menu is None:
            ctx.ui.add_system_message(USAGE)
        return
    if sub == "create" and arg:
        await knowledge.prepare(ctx.ui.add_system_message)
        result = await asyncio.to_thread(knowledge.service.create, arg)
        ctx.config["set_knowledge_binding"]({"kb_id": result["id"], "top_k": min(10, knowledge.config.top_k)})
        ctx.ui.add_system_message(f"已创建并选中知识库 {result['name']}: {result['id']}")
        return
    if sub == "use" and arg:
        status = await asyncio.to_thread(knowledge.service.status, arg)
        ctx.config["set_knowledge_binding"]({"kb_id": status["id"], "top_k": min(10, knowledge.config.top_k)})
        ctx.ui.add_system_message(f"已选中知识库 {status['name']}: {status['id']} ({status['state']})")
        await knowledge.prepare(ctx.ui.add_system_message)
        return
    if sub not in {"import", "status", "sources", "reimport", "remove", "retry"}:
        ctx.ui.add_system_message(USAGE)
        return
    if not knowledge.binding:
        raise ValueError("请先 /knowledge create <name> 或 /knowledge use <id>")
    kb_id = knowledge.binding["kb_id"]
    if sub in {"import", "reimport", "remove", "retry"}:
        await knowledge.prepare(ctx.ui.add_system_message)
    if sub == "import" and arg:
        path = Path(arg).expanduser()
        def collect_files():
            source = path.resolve()
            return sorted(p for p in source.rglob("*") if p.suffix.lower() in {".md", ".pdf", ".docx"} and p.is_file()) if source.is_dir() else [source]
        files = await asyncio.to_thread(collect_files)
        if not files:
            raise ValueError("目录内没有 Markdown/PDF/DOCX 文件")
        await _import_files(ctx, knowledge.service, kb_id, files)
        return
    if sub == "reimport" and arg:
        status = await asyncio.to_thread(knowledge.service.status, kb_id)
        document = next((doc for doc in status["documents"] if doc["id"] == arg), None)
        if document is None:
            raise ValueError(f"当前知识库中没有此文档：{arg}")
        if document.get("pending_operation"):
            raise ValueError("此文档有待恢复操作，请先 /knowledge retry")
        await _import_files(ctx, knowledge.service, kb_id, [_source_path(document["source_uri"])])
        return
    if sub in {"status", "sources"}:
        result = await asyncio.to_thread(knowledge.service.status, kb_id)
        if sub == "sources":
            result = result["documents"]
    elif sub == "remove" and arg:
        result = await asyncio.to_thread(knowledge.service.remove, kb_id, arg)
    elif sub == "retry":
        result = await asyncio.to_thread(knowledge.service.retry, kb_id)
    else:
        ctx.ui.add_system_message(USAGE)
        return
    ctx.ui.add_system_message(json.dumps(result, ensure_ascii=False, indent=2))


KNOWLEDGE_COMMAND = Command(name="knowledge", description="知识库、资料问答与引用", usage=USAGE,
                            type=CommandType.LOCAL, handler=handle_knowledge)

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re

from codeplus.commands.registry import Command, CommandContext, CommandType


USAGE = '/knowledge create <name> | use <id> | import "<path>" | status | sources | remove <doc_id> | retry | off | open <citation_id> [offset]'


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
    if sub == "create" and arg:
        result = await asyncio.to_thread(knowledge.service.create, arg)
        ctx.config["set_knowledge_binding"]({"kb_id": result["id"], "top_k": min(10, knowledge.config.top_k)})
        ctx.ui.add_system_message(f"已创建并选中知识库 {result['name']}: {result['id']}")
        return
    if sub == "use" and arg:
        status = await asyncio.to_thread(knowledge.service.status, arg)
        ctx.config["set_knowledge_binding"]({"kb_id": status["id"], "top_k": min(10, knowledge.config.top_k)})
        ctx.ui.add_system_message(f"已选中知识库 {status['name']}: {status['id']} ({status['state']})")
        return
    if sub not in {"import", "status", "sources", "remove", "retry"}:
        ctx.ui.add_system_message(USAGE)
        return
    if not knowledge.binding:
        raise ValueError("请先 /knowledge create <name> 或 /knowledge use <id>")
    kb_id = knowledge.binding["kb_id"]
    if sub == "import" and arg:
        path = Path(arg).expanduser()
        def collect_files():
            return sorted(p for p in path.rglob("*") if p.suffix.lower() in {".md", ".pdf", ".docx"} and p.is_file()) if path.is_dir() else [path]
        files = await asyncio.to_thread(collect_files)
        if not files:
            raise ValueError("目录内没有 Markdown/PDF/DOCX 文件")
        for i, file in enumerate(files, 1):
            ctx.ui.add_system_message(f"导入 {i}/{len(files)}: {file}")
            try:
                result = await asyncio.to_thread(knowledge.service.import_document, kb_id, file)
                ctx.ui.add_system_message(f"{i}/{len(files)} {'未变化' if result['unchanged'] else '导入完成'}: {file.name}")
            except Exception as exc:
                ctx.ui.add_system_message(f"{i}/{len(files)} 导入失败: {file.name}: {exc}")
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

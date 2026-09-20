"""Read-only command candidates; execution and recovery stay in the handler/service."""

from urllib.parse import unquote, urlsplit

from rich.markup import escape


PREFIX = "/knowledge "
KEYS = "↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 返回/取消"


def source_name(document: dict) -> str:
    return unquote(urlsplit(document["source_uri"]).path).rsplit("/", 1)[-1]


def document_label(document: dict) -> str:
    state = "已移除" if document.get("removed") else document["state"]
    if document.get("pending_operation"):
        state += " · 待重试 " + document["pending_operation"]
    return f"{source_name(document)} · {state} · 更新 {document.get('updated_at') or '未记录'}"


def candidates(text: str, libraries: list[dict], status: dict | None,
               failures: list[dict], document_id: str | None = None):
    """Return (display/value pairs, picker kind, hint) for the current input."""
    command = text[len(PREFIX):]
    sub, _, query = command.partition(" ")
    documents = status.get("documents", []) if status else []
    query = query.strip().casefold()
    if document_id and command.strip() == f"sources {document_id}":
        document = next((d for d in documents if d["id"] == document_id), None)
        if document:
            actions = [("重新导入原路径", f"reimport {document_id}")]
            if not document.get("removed"):
                actions.append(("移除资料", f"remove {document_id}"))
            if document.get("pending_operation"):
                actions.insert(0, ("重试待完成写入", "retry"))
            return [(escape(f"{label} · {PREFIX}{value}"), PREFIX + value)
                    for label, value in actions], "knowledge-actions", document_label(document) + " · " + KEYS
    if sub == "use" and " " in command:
        pairs = [(escape(f"{library['name']} · {library['state']} · {library['document_count']} 份 · {library['id']}"),
                  PREFIX + "use " + library["id"])
                 for library in libraries
                 if query in library["name"].casefold() or query in library["id"]]
        return pairs, "knowledge", "按库名筛选；Enter 才切换 · " + KEYS if pairs else "没有匹配的库 · Esc 返回创建"
    if sub in {"sources", "remove", "reimport"} and " " in command:
        pairs = [(escape(document_label(d)), d["id"] if sub == "sources" else PREFIX + sub + " " + d["id"])
                 for d in documents if query in source_name(d).casefold() or query in d["id"]]
        return pairs, "knowledge-source" if sub == "sources" else "knowledge", (
            "选择资料查看操作 · " + KEYS if pairs else "没有匹配的资料 · /knowledge import <路径> · Esc 返回")
    if sub == "create" and " " in command:
        return [], "knowledge", "输入库名，Enter 创建并选中 · Esc 返回"
    if sub == "import" and " " in command:
        return [], "knowledge-path", "输入文件或目录路径（支持空格和引号）· Tab 补全 · Enter 导入 · Esc 返回"
    actions = [
        ("create", "创建知识库"), ("use", "按名字选择知识库"),
        ("import", "导入文件或目录"), ("sources", "选择资料与操作"),
        ("status", "查看当前库状态"), ("retry", "重试待完成写入"),
        ("prepare", "连接服务 / 重试环境准备"), ("off", "停用知识库模式"),
    ]
    pairs = [(escape(f"{label} · {PREFIX}{value}"), PREFIX + value)
             for value, label in actions if command.casefold() in value or command in label]
    for failure in failures:
        if failure["kind"] == "import":
            value = PREFIX + 'import "' + failure["path"] + '"'
            label = f"重新导入失败路径 {failure['path']} · {failure['reason']}"
            if command.casefold() in label.casefold():
                pairs.append((escape(label), value))
    return pairs, "knowledge", KEYS

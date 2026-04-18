from __future__ import annotations

import json
import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Any

from codeplus.conversation import ConversationManager, Message, ToolResultBlock, ToolUseBlock

SESSIONS_DIR = ".codeplus/sessions"
DEFAULT_MAX_AGE_DAYS = 30
TITLE_MAX_LENGTH = 50

SESSION_SUMMARY_PROMPT = (
    "你是一个对话摘要助手。请根据下面的对话内容，用一句话总结这个会话的主要内容。"
    "只输出摘要文本，不要加任何前缀或标点符号外的修饰。不要调用任何工具。"
)


# ---------------------------------------------------------------------------
# SessionRecord
# ---------------------------------------------------------------------------


# 压缩边界记录的类型标记。普通对话消息不带 type，靠 role 区分 user / assistant。
TYPE_COMPACT_BOUNDARY = "compact_boundary"


def _tool_uses_to_dicts(message: Message) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tu in message.tool_uses or []:
        d: dict[str, Any] = {"tool_use_id": tu.tool_use_id, "tool_name": tu.tool_name}
        # arguments 为空时整个键省略，纯文本消息落盘不带多余字段
        if tu.arguments:
            d["arguments"] = tu.arguments
        out.append(d)
    return out


def _tool_results_to_dicts(message: Message) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tr in message.tool_results or []:
        d: dict[str, Any] = {"tool_use_id": tr.tool_use_id, "content": tr.content}
        # is_error 为 False 时省略，成功的工具结果不留 "is_error":false 这种噪音
        if tr.is_error:
            d["is_error"] = tr.is_error
        out.append(d)
    return out


@dataclass
class SessionRecord:
    """一条落盘的会话记录。

    工具块以与协议无关的内部表示内联存储（tool_use_id / tool_name / arguments），
    换 provider 恢复会话时也能还原；两者为空时不写入 JSON。普通消息 type 为 None，
    靠 role 区分；type 为 "compact_boundary" 时 content 是压缩边界的结构化载荷。
    """

    role: str
    content: Any
    timestamp: datetime
    type: str | None = None
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def is_compact_boundary(self) -> bool:
        return self.type == TYPE_COMPACT_BOUNDARY

    def to_jsonl(self) -> str:
        data: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
            "ts": int(self.timestamp.timestamp()),
        }
        if self.type:
            data["type"] = self.type
        if self.tool_uses:
            data["tool_uses"] = self.tool_uses
        if self.tool_results:
            data["tool_results"] = self.tool_results
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_jsonl(cls, line: str) -> SessionRecord | None:
        raise NotImplementedError("Implementation pending")

    @classmethod
    def from_message(cls, message: Message) -> list[SessionRecord]:
        # 一条 Message 对应一条记录，工具块内联其中；思考块不落盘：它的
        # signature 只在同一轮工具循环内需要回传，跨会话恢复用不上。
        return [
            cls(
                role=message.role,
                content=message.content,
                timestamp=datetime.now(timezone.utc),
                tool_uses=_tool_uses_to_dicts(message),
                tool_results=_tool_results_to_dicts(message),
            )
        ]

    def to_message(self) -> Message:
        raise NotImplementedError("Implementation pending")


# ---------------------------------------------------------------------------
# Compact boundary 载荷（摘要 + 内联的 keep 尾部）
# ---------------------------------------------------------------------------


def _message_to_keep_dict(message: Message) -> dict[str, Any]:
    """将一条保留的尾巴消息序列化成压缩边界里内联的 dict，格式与普通落盘记录一致。"""
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    tool_uses = _tool_uses_to_dicts(message)
    tool_results = _tool_results_to_dicts(message)
    if tool_uses:
        data["tool_uses"] = tool_uses
    if tool_results:
        data["tool_results"] = tool_results
    return data


def make_compact_boundary(summary: str, keep: list[Message]) -> SessionRecord:
    raise NotImplementedError("Implementation pending")


def parse_compact_boundary(record: SessionRecord) -> tuple[str, list[Message]]:
    raise NotImplementedError("Implementation pending")


# ---------------------------------------------------------------------------
# Record ↔ Message 转换
# ---------------------------------------------------------------------------


# 压缩摘要在恢复会话时作为一条 user 消息重放，前缀说明这段是早期对话的浓缩。
RESUME_SUMMARY_PREFIX = (
    "本次会话延续自之前的对话，因上下文空间不足进行了压缩。以下是早期对话的摘要：\n\n"
)


def records_to_messages(records: list[SessionRecord]) -> list[Message]:
    raise NotImplementedError("Implementation pending")


# ---------------------------------------------------------------------------
# SessionMeta
# ---------------------------------------------------------------------------


@dataclass
class SessionMeta:
    id: str
    title: str = ""
    summary: str = ""
    message_count: int = 0
    total_tokens: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def save(self, path: Path) -> None:
        data = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> SessionMeta | None:
        raise NotImplementedError("Implementation pending")


# ---------------------------------------------------------------------------
# Session（活跃会话句柄）
# ---------------------------------------------------------------------------


class Session:
    def __init__(
        self,
        session_id: str,
        file: IO[str],
        meta: SessionMeta,
        sessions_dir: Path,
    ) -> None:
        self.session_id = session_id
        self._file = file
        self.meta = meta
        self._sessions_dir = sessions_dir

    def append(self, message: Message) -> None:
        records = SessionRecord.from_message(message)
        for record in records:
            self._file.write(record.to_jsonl() + "\n")
        self._file.flush()

        self.meta.message_count += 1
        self.meta.last_active = datetime.now(timezone.utc)

        if not self.meta.title and message.role == "user" and message.content:
            self.meta.title = message.content[:TITLE_MAX_LENGTH]

        self.meta.save(self._sessions_dir / f"{self.session_id}.meta")

    def append_record(self, record: SessionRecord) -> None:
        """追加一条原始 SessionRecord（例如 compact_boundary 标记）。

        与 append() 不同，此方法不会更新 message_count/title——boundary 是
        结构性标记而非对话轮次。last_active 仍会更新，以保证 session 按最近
        使用排序。
        """
        self._file.write(record.to_jsonl() + "\n")
        self._file.flush()
        self.meta.last_active = datetime.now(timezone.utc)
        self.meta.save(self._sessions_dir / f"{self.session_id}.meta")


    def close(self) -> None:
        if self._file and not self._file.closed:
            self._file.flush()
            self._file.close()


# ---------------------------------------------------------------------------
# ResumeResult
# ---------------------------------------------------------------------------


@dataclass
class ResumeResult:
    session: Session
    messages: list[Message]
    last_active: datetime


# ---------------------------------------------------------------------------
# Session 摘要生成
# ---------------------------------------------------------------------------


async def generate_session_summary(
    client: Any, conversation: ConversationManager, protocol: str
) -> str:
    raise NotImplementedError("Implementation pending")


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


def _generate_session_id() -> str:
    now = datetime.now()
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"session_{now.strftime('%Y%m%d_%H%M%S')}_{suffix}"


class SessionManager:
    def __init__(self, work_dir: str) -> None:
        self._sessions_dir = Path(work_dir) / SESSIONS_DIR
        self._sessions_dir.mkdir(parents=True, exist_ok=True)


    def create(self) -> Session:
        session_id = _generate_session_id()
        jsonl_path = self._sessions_dir / f"{session_id}.jsonl"
        meta = SessionMeta(id=session_id)
        meta.save(self._sessions_dir / f"{session_id}.meta")

        file = open(jsonl_path, "a", encoding="utf-8")  # noqa: SIM115
        return Session(
            session_id=session_id,
            file=file,
            meta=meta,
            sessions_dir=self._sessions_dir,
        )


    def list(self) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for meta_path in self._sessions_dir.glob("*.meta"):
            meta = SessionMeta.load(meta_path)
            if meta is not None:
                metas.append(meta)
        metas.sort(key=lambda m: m.last_active, reverse=True)
        return metas

    def resume(self, session_id: str) -> ResumeResult | None:
        raise NotImplementedError("Implementation pending")

    def delete(self, session_id: str) -> bool:
        jsonl_path = self._sessions_dir / f"{session_id}.jsonl"
        meta_path = self._sessions_dir / f"{session_id}.meta"

        deleted = False
        if jsonl_path.exists():
            jsonl_path.unlink()
            deleted = True
        if meta_path.exists():
            meta_path.unlink()
            deleted = True
        return deleted

    def cleanup(self, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        removed = 0

        for meta_path in list(self._sessions_dir.glob("*.meta")):
            meta = SessionMeta.load(meta_path)
            if meta is not None and meta.last_active < cutoff:
                self.delete(meta.id)
                removed += 1

        return removed

"""会话 JSONL 写入器 —— 实时追加对话记录到 conversation.jsonl。"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import IO

from csycode.llm import Message


@dataclass
class Entry:
    """一条 JSONL 记录的 Python 表示。"""

    role: str
    content: str
    ts: float
    model: str = ""
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None


class Writer:
    """会话 JSONL 写入器。

    以追加模式打开 conversation.jsonl，每条消息写一行 JSON。
    同时维护 session.meta 文件用于快速列表展示（对齐 mewcode SessionMeta）。
    线程安全：内部使用 threading.Lock 保护写操作。
    """

    def __init__(self, session_dir: str) -> None:
        os.makedirs(session_dir, exist_ok=True)
        jsonl_path = os.path.join(session_dir, "conversation.jsonl")
        self._file: IO[str] = open(jsonl_path, "a", encoding="utf-8")  # noqa: SIM115
        self._lock = threading.Lock()
        self.path: str = os.path.abspath(jsonl_path)  # ch10: 绝对路径
        self._session_dir: str = session_dir
        self._message_count: int = 0
        self._first_title: str = ""
        self._model: str = ""
        self._session_id: str = os.path.basename(session_dir.rstrip("/\\"))

    @classmethod
    def open_existing(cls, session_dir: str) -> "Writer":
        """打开已有会话的 JSONL 文件（不创建目录）。"""
        jsonl_path = os.path.join(session_dir, "conversation.jsonl")
        writer = object.__new__(cls)
        writer._file = open(jsonl_path, "a", encoding="utf-8")  # noqa: SIM115
        writer._lock = threading.Lock()
        writer.path = os.path.abspath(jsonl_path)  # ch10
        writer._session_dir = session_dir
        writer._message_count = 0
        writer._first_title = ""
        writer._model = ""
        writer._session_id = os.path.basename(session_dir.rstrip("/\\"))
        return writer

    def close(self) -> None:
        """关闭文件句柄，最后一次刷新 .meta。"""
        with self._lock:
            if self._file and not self._file.closed:
                self._file.flush()
                self._file.close()
        # 最后一次写 .meta
        self._flush_meta()

    def __enter__(self) -> "Writer":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ── 公开写入接口 ──────────────────────────────────────────────

    def append(self, msg: Message, model: str = "", is_first: bool = False) -> None:
        """将单条消息追加写入 JSONL。

        Args:
            msg: 要写入的消息。
            model: 模型名（仅 is_first=True 时写入）。
            is_first: 是否为会话首条消息。
        """
        entry = self._message_to_entry(msg, model if is_first else "")
        self._write_entry(entry)

        # 更新元数据跟踪
        self._message_count += 1
        if model and not self._model:
            self._model = model
        if msg.role == "user" and not self._first_title:
            self._first_title = msg.content[:50]

        # 每 10 条消息更新一次 .meta（避免每次都写盘）
        if self._message_count % 10 == 0:
            self._flush_meta()

    def write_compact_marker(self) -> None:
        """写入一条压缩标记行。"""
        entry = {"type": "compact", "ts": time.time()}
        with self._lock:
            self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())

    def append_all(self, msgs: list[Message]) -> None:
        """批量追加消息（例如 replace_history 后的全量重写标记）。"""
        for msg in msgs:
            self.append(msg)

    # ── 回调适配 ──────────────────────────────────────────────────

    def on_append(self, msg: Message) -> None:
        """Conversation.add_* 的回调：追加单条消息。"""
        self.append(msg)

    def on_replace(self, msgs: list[Message]) -> None:
        """Conversation.replace_history 的回调：写 compact marker + 追加新消息。"""
        self.write_compact_marker()
        self.append_all(msgs)

    # ── 元数据维护 ──────────────────────────────────────────────────

    def _flush_meta(self) -> None:
        """将当前跟踪的元数据写入 session.meta 文件。"""
        if not self._session_dir:
            return
        from csycode.session.meta import update_meta

        update_meta(
            self._session_dir,
            title=self._first_title or None,
            model=self._model or None,
            message_count=self._message_count,
            session_id=self._session_id,
        )

    def record_tokens(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """累计 token 用量到 .meta（由 Agent 在每轮结束后调用）。"""
        if not self._session_dir:
            return
        from csycode.session.meta import update_meta

        update_meta(
            self._session_dir,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id=self._session_id,
        )

    # ── 内部方法 ──────────────────────────────────────────────────

    def _message_to_entry(self, msg: Message, model: str = "") -> Entry:
        """将 Message 转换为 JSONL Entry。"""
        entry = Entry(
            role=msg.role,
            content=msg.content,
            ts=time.time(),
            model=model,
        )
        if msg.tool_calls:
            entry.tool_calls = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in msg.tool_calls
            ]
        return entry

    def _write_entry(self, entry: Entry) -> None:
        """加锁写入并 fsync。"""
        data: dict = {"role": entry.role, "content": entry.content, "ts": entry.ts}
        if entry.model:
            data["model"] = entry.model
        if entry.tool_calls:
            data["tool_calls"] = entry.tool_calls
        if entry.tool_results:
            data["tool_results"] = entry.tool_results

        with self._lock:
            self._file.write(json.dumps(data, ensure_ascii=False) + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())

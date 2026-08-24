"""Mailbox 邮箱系统 —— 基于文件的跨进程消息通信。

每个 agent 的收件箱存储为 ``{agent_id}.json``，
带有 ``{agent_id}.json.lock`` 文件锁保护并发写入。

对齐 mewcode teams/mailbox.py 的 Mailbox 实现。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from csycode.team.filelock import acquire_lock
from csycode.team.mailbox.message import Message, MessageType, create_message

__all__ = ["Box", "Message", "MessageType", "create_message"]


class Box:
    """文件邮箱 —— 每个 agent 一个 JSON 收件箱。

    线程/协程/跨进程安全（通过文件锁）。
    """

    def __init__(self, dir_: str | Path) -> None:
        """初始化邮箱。

        Args:
            dir_: mailbox 目录路径（如 ``<team_config_dir>/mailbox/``）。
        """
        self._dir = Path(dir_)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── 路径辅助 ──────────────────────────────────────────────────

    def _inbox_path(self, agent_id: str) -> Path:
        return self._dir / f"{agent_id}.json"

    def _lock_path(self, agent_id: str) -> Path:
        return self._dir / f"{agent_id}.json.lock"

    # ── 读写底层 ──────────────────────────────────────────────────

    def _read_inbox(self, agent_id: str) -> list[Message]:
        """读取收件箱全部消息（不加锁）。"""
        path = self._inbox_path(agent_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [Message.from_dict(item) for item in data]
            # 兼容旧格式 {"messages": [...]}
            if isinstance(data, dict) and "messages" in data:
                return [Message.from_dict(item) for item in data["messages"]]
            return []
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    def _write_inbox(self, agent_id: str, messages: list[Message]) -> None:
        """写入收件箱全部消息（不加锁）。"""
        path = self._inbox_path(agent_id)
        data = json.dumps(
            [m.to_dict() for m in messages],
            ensure_ascii=False,
            indent=2,
        )
        path.write_text(data, encoding="utf-8")

    # ── 锁内操作 ──────────────────────────────────────────────────

    async def _with_lock(
        self,
        agent_id: str,
        fn: Callable[[list[Message]], list[Message]],
    ) -> None:
        """获取文件锁，读入消息，应用 fn，写回。"""
        lock_path = self._lock_path(agent_id)
        async with acquire_lock(lock_path):
            messages = self._read_inbox(agent_id)
            messages = fn(messages)
            self._write_inbox(agent_id, messages)

    # ── 公开 API ──────────────────────────────────────────────────

    async def write(self, agent_id: str, msg: Message) -> None:
        """追加一条消息到收件箱（线程/协程安全）。

        Args:
            agent_id: 收件人 agent_id。
            msg: 要追加的消息。
        """
        def _append(msgs: list[Message]) -> list[Message]:
            msg.read = False
            if msg.timestamp == 0.0:
                import time
                msg.timestamp = time.time()
            msgs.append(msg)
            return msgs

        await self._with_lock(agent_id, _append)

    async def read(self, agent_id: str) -> list[Message]:
        """读取全部消息（不标记已读）。"""
        return self._read_inbox(agent_id)

    async def read_unread(self, agent_id: str) -> tuple[list[int], list[Message]]:
        """读取未读消息，返回 (索引列表, 消息列表)。

        Args:
            agent_id: 收件人 agent_id。

        Returns:
            (未读消息在原数组中的索引, 未读消息列表)
        """
        messages = self._read_inbox(agent_id)
        indices: list[int] = []
        unread: list[Message] = []
        for i, m in enumerate(messages):
            if not m.read:
                indices.append(i)
                unread.append(m)
        return indices, unread

    async def mark_read(self, agent_id: str, indices: list[int]) -> None:
        """将指定索引的消息标记为已读。

        Args:
            agent_id: 收件人 agent_id。
            indices: 要标记为已读的消息索引列表。
        """
        if not indices:
            return
        idx_set = set(indices)

        def _mark(msgs: list[Message]) -> list[Message]:
            for i in idx_set:
                if i < len(msgs):
                    msgs[i].read = True
            return msgs

        await self._with_lock(agent_id, _mark)

    async def consume(self, agent_id: str) -> list[Message]:
        """读取并标记所有未读消息为已读（原子操作）。

        Returns:
            未读消息列表（已标记为已读）。
        """
        result: list[Message] = []

        def _consume(msgs: list[Message]) -> list[Message]:
            for m in msgs:
                if not m.read:
                    result.append(m)
                    m.read = True
            return msgs

        await self._with_lock(agent_id, _consume)
        return result

    async def broadcast(
        self,
        agent_ids: list[str],
        msg: Message,
        exclude: str = "",
    ) -> None:
        """向多个 agent 广播同一条消息。

        Args:
            agent_ids: 接收者 agent_id 列表。
            msg: 消息模板（会为每个接收者创建独立副本）。
            exclude: 排除的 agent_id。
        """
        for aid in agent_ids:
            if aid == exclude:
                continue
            await self.write(aid, msg)

    async def cleanup(self, agent_id: str) -> None:
        """删除某个 agent 的收件箱和锁文件。"""
        self._inbox_path(agent_id).unlink(missing_ok=True)
        self._lock_path(agent_id).unlink(missing_ok=True)

    async def cleanup_all(self) -> None:
        """删除 mailbox 目录下所有文件。"""
        if not self._dir.exists():
            return
        for f in self._dir.iterdir():
            f.unlink(missing_ok=True)

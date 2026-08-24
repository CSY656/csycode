"""Mailbox 消息类型定义。

对齐 mewcode teams/mailbox.py 的 MailboxMessage。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """消息类型枚举。"""
    TEXT = "text"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"


@dataclass
class Message:
    """一条邮箱消息。

    Attributes:
        id: 唯一标识（12 位 hex）。
        from_: 发送者（json key "from"）。
        to: 接收者。
        type: 消息类型。
        summary: 5-10 词摘要。
        content: 消息正文。
        payload: 结构化载荷（如 plan_approval_response 的 {approve, feedback}）。
        timestamp: Unix 时间戳。
        read: 是否已读。
    """
    from_: str  # json key "from"
    to: str
    type: MessageType = MessageType.TEXT
    summary: str = ""
    content: str = ""
    payload: dict[str, Any] | None = None
    timestamp: float = 0.0
    read: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_,
            "to": self.to,
            "type": str(self.type),
            "summary": self.summary,
            "content": self.content,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "read": self.read,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        msg_type = MessageType.TEXT
        try:
            msg_type = MessageType(data.get("type", "text"))
        except ValueError:
            pass
        return cls(
            from_=data.get("from", ""),
            to=data.get("to", ""),
            type=msg_type,
            summary=data.get("summary", ""),
            content=data.get("content", ""),
            payload=data.get("payload"),
            timestamp=data.get("timestamp", 0.0),
            read=data.get("read", False),
        )


def create_message(
    from_agent: str,
    to_agent: str,
    content: str = "",
    summary: str = "",
    message_type: MessageType = MessageType.TEXT,
    payload: dict[str, Any] | None = None,
) -> Message:
    """工厂函数：创建一条带自动生成 id 和时间戳的消息。

    Args:
        from_agent: 发送者。
        to_agent: 接收者。
        content: 消息正文。
        summary: 摘要。
        message_type: 消息类型。
        payload: 结构化载荷。

    Returns:
        新 Message 实例。
    """
    return Message(
        from_=from_agent,
        to=to_agent,
        type=message_type,
        summary=summary,
        content=content,
        payload=payload,
        timestamp=time.time(),
    )

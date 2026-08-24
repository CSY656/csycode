"""命令系统核心类型定义 —— Kind 枚举、Command dataclass、Handler 类型别名。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .ui import UI

Handler = Callable[["UI", str], Awaitable[None]]
"""命令处理函数签名: async def handler(ui: UI, args: str = "") -> None"""


class Kind(Enum):
    """命令执行类型。

    LOCAL  — 纯本地：只打印信息，不改 App 状态，不进对话历史。
    UI    — 影响界面：改 App 状态/模式/会话，不进对话历史。
    PROMPT — 提示词：注入 user 消息 + 触发 LLM 回合，进对话历史。
    """

    LOCAL = "local"
    UI = "ui"
    PROMPT = "prompt"


@dataclass(slots=True)
class Command:
    """一条注册命令。

    Attributes:
        name: 命令名（不带 "/" 前缀，全小写，唯一）。
        description: 一句话描述，用于 /help 与补全菜单。
        kind: 执行类型。
        handler: 异步处理函数，签名为 async def(UI) -> None。
        aliases: 别名列表（不带 "/" 前缀，全小写，全局唯一含 name）。
        hidden: True 时不出现在 /help 和补全菜单中，但 dispatcher 仍可命中。
    """

    name: str
    description: str
    kind: Kind
    handler: Handler
    aliases: list[str] = field(default_factory=list)
    hidden: bool = False

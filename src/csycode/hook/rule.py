"""Hook 规则数据结构定义。

ch12: Rule / Condition / Action / Payload 等核心 dataclass。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from csycode.permission.matcher import Matcher

# ── 枚举 ──────────────────────────────────────────────────────────────────


class CombineMode(str, enum.Enum):
    """条件组合模式：all_of（全部满足）或 any_of（任一满足）。"""
    ALL_OF = "all_of"
    ANY_OF = "any_of"


class ActionType(str, enum.Enum):
    """动作类型枚举。"""
    SHELL = "shell"
    PROMPT = "prompt"
    HTTP = "http"
    SUBAGENT = "subagent"


# ── 条件 ──────────────────────────────────────────────────────────────────


@dataclass
class AtomCondition:
    """单个原子条件：字段路径 + 匹配器。

    Attributes:
        field: payload 字段路径（如 "tool_input.path"）。
        matcher: 匹配器实例（复用 permission.Matcher）。
    """
    field: str
    matcher: Matcher


@dataclass
class Condition:
    """组合条件：all_of 或 any_of 之一，不可混用。

    Attributes:
        mode: 组合模式（CombineMode.ALL_OF / ANY_OF）。
        atoms: 原子条件列表。
    """
    mode: CombineMode
    atoms: list[AtomCondition]


# ── 动作 ──────────────────────────────────────────────────────────────────


@dataclass
class ShellAction:
    """shell 动作：执行命令（sh -c），通过 stdin 传入 payload JSON。"""
    command: str


@dataclass
class PromptAction:
    """prompt 动作：注入文本到 reminder 区。"""
    text: str


@dataclass
class HttpAction:
    """http 动作：发送 HTTP 请求。"""
    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None  # 模板字符串，None 表示用 payload JSON


@dataclass
class SubagentAction:
    """subagent 动作：启动子 Agent（ch12 占位）。"""
    agent_name: str
    prompt: str


@dataclass
class Action:
    """Hook 动作对象。

    Attributes:
        type: 动作类型。
        shell / prompt / http / subagent: 各类型的专用字段（按 type 取用）。
    """
    type: ActionType
    shell: ShellAction | None = None
    prompt: PromptAction | None = None
    http: HttpAction | None = None
    subagent: SubagentAction | None = None


# ── Hook 规则 ─────────────────────────────────────────────────────────────


@dataclass
class HookRule:
    """单条 Hook 规则（对齐 mewcode Hook dataclass）。

    Attributes:
        name: 唯一标识名（用于日志、only_once 跟踪、冲突检测）。
        event: 触发事件。
        action: 动作对象。
        condition: 条件表达式（None 表示无条件触发）。
        only_once: 会话内只跑一次。
        asyncio_mode: 是否后台异步执行（对应 YAML 的 async 字段，
                      避免与 Python 关键字冲突）。
        timeout_s: 命令 / HTTP 最大执行时长（秒），默认 30.0。
        source: 来源文件路径（供 /hooks 显示）。
    """
    name: str
    event: "Event"
    action: Action
    condition: Condition | None = None
    only_once: bool = False
    asyncio_mode: bool = False
    timeout_s: float = 30.0
    source: str = ""


# ── 通用类型 ──────────────────────────────────────────────────────────────

# Payload 是事件分派时携带的上下文数据
Payload = dict[str, Any]


# 延迟导入 Event 避免循环
from csycode.hook.event import Event  # noqa: E402

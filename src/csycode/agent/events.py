"""Agent Loop 事件类型定义。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass
class TextDelta:
    """LLM 生成的文本片段。"""

    text: str


@dataclass
class ToolUseEvent:
    """LLM 流式输出期间收到的工具调用请求（对齐 mewcode ToolUseEvent）。

    此事件在 LLM 流期间产出，允许 Agent 立即启动工具执行，
    无需等待整个 LLM 响应完成。
    """

    tool_name: str
    tool_id: str
    arguments: dict[str, Any]


@dataclass
class ToolCallStart:
    """开始执行一个工具调用。"""

    tool_name: str
    tool_args: dict[str, Any]
    index: int
    total: int


@dataclass
class ToolCallEnd:
    """工具执行完成。"""

    tool_name: str
    success: bool
    content: str  # 存盘/截断后的内容（写入 conversation）
    index: int
    error: str | None = None
    original_output: str = ""  # 原始完整输出（供 TUI 展示）
    exit_plan_mode: bool = False
    blocked_by_plan_mode: bool = False
    show_result_to_user: bool = True
    # ch08: 工具结果是否因超大而被落盘替换
    offloaded: bool = False
    offload_path: str = ""


@dataclass
class TokenUsage:
    """一轮 LLM 调用的 token 统计。"""

    input_tokens: int
    output_tokens: int
    round_num: int
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class LoopProgress:
    """循环进度信息。"""

    round_num: int
    max_rounds: int
    status: str  # "thinking" | "executing" | "done"


@dataclass
class LoopEnd:
    """循环终止事件。"""

    reason: str
    final_text: str
    total_rounds: int
    total_input_tokens: int
    total_output_tokens: int
    error_msg: str = ""  # stream_error 时的具体错误信息


@dataclass
class ApprovalRequest:
    """人在回路待批准事件。"""

    name: str
    args: str
    reason: str
    respond: asyncio.Future


# ── ch08: 压缩事件 ──────────────────────────────────────────────────


class CompactPhase(Enum):
    """压缩生命周期阶段。"""

    BEFORE_AUTO = "before_auto"
    AFTER_AUTO = "after_auto"
    BEFORE_EMERGENCY = "before_emergency"
    AFTER_EMERGENCY = "after_emergency"


@dataclass
class CompactNotification:
    """上下文压缩通知（自动/手动/紧急）。

    包含阶段信息以支持 TUI 在不同阶段展示不同文案。
    """

    before_tokens: int
    after_tokens: int = 0
    message: str = ""
    error: str = ""
    phase: CompactPhase | None = None  # 压缩阶段（自动/紧急路径设，手动路径不设）


# ── 联合类型 ────────────────────────────────────────────────────────

AgentEvent = (
    TextDelta
    | ToolUseEvent
    | ToolCallStart
    | ToolCallEnd
    | TokenUsage
    | LoopProgress
    | LoopEnd
    | ApprovalRequest
    | CompactNotification
)

"""LLM protocol layer — protocol-agnostic types, dataclasses, and provider factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable

from csycode.config import ProviderConfig


# ── 哨兵异常 ─────────────────────────────────────────────────────────────


class PromptTooLongError(Exception):
    """Provider 上报上下文超出窗口时统一抛出的哨兵异常。

    不同 provider 抛出的具体异常结构差异大（anthropic.BadRequestError vs
    openai.BadRequestError），统一成单一异常后 agent 主循环只需一处判断。
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(Exception):
    """API 认证失败（API key 无效或过期）。"""

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class RateLimitError(Exception):
    """API 速率限制（429 / rate_limit_error）。

    Attributes:
        retry_after: 建议重试等待秒数（来自 Retry-After 头，可能为 None）。
    """

    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class NetworkError(Exception):
    """网络连接错误（DNS / TCP / TLS / 超时）。"""

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class LLMError(Exception):
    """通用 LLM 调用错误（非 PTL / 非认证 / 非限流 / 非网络的其他错误）。"""

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


# ── 核心数据类型 ─────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """从 LLM 流式响应中解析出的工具调用请求。

    Attributes:
        id: 工具调用唯一标识（Anthropic 的 tool_use.id / OpenAI 的 tool_call_id）。
        name: 工具名称。
        arguments: 已解析的参数字典。
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """A single message in the conversation history.

    Attributes:
        role: 消息角色。
        content: 消息文本内容。
        tool_calls: 当 assistant 发起工具调用时，包含调用的工具列表。
        tool_call_id: 当此消息是工具执行结果时，对应工具调用的 ID。
        _supplement_tag: 补充消息的标签（如 "plan-mode-reminder"），
                         用于标记和清理临时注入的消息。
    """

    role: Literal["user", "assistant"]
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    _supplement_tag: str | None = None


@dataclass
class StreamEvent:
    """A single event from a streaming LLM response.

    对齐 mewcode StreamEvent：支持流期间的细粒度工具调用事件，
    使 Agent 可以在 LLM 还在输出时就启动工具执行。

    Attributes:
        text: A text delta chunk (may be empty).
        partial_json: 工具参数 JSON 片段（input_json_delta，流期间）。
        tool_name: 工具名（tool_start / tool_complete 时填充）。
        tool_id: 工具调用 ID（tool_start 时分配，tool_complete 时复用）。
        arguments: 完整解析后的工具参数（仅 tool_complete 时有效）。
        tool_calls: 当 done=True 且模型请求了工具调用时，包含完整的工具调用列表。
        done: True if the stream has completed normally (stream_end).
        err: An exception if the stream errored.
        usage: Token 用量（含缓存写/读），仅在 done=True 时有效。
        stop_reason: API 返回的结束原因（如 "stop", "length", "tool_calls"）。
        event_type: 事件类型 —— "text" | "tool_start" | "tool_delta" | "tool_complete" | "done"。
    """

    text: str = ""
    partial_json: str = ""
    tool_name: str = ""
    tool_id: str = ""
    arguments: dict | None = None
    tool_calls: list[ToolCall] | None = None
    done: bool = False
    err: Exception | None = None
    usage: Usage | None = None
    stop_reason: str = ""
    event_type: str = "text"


# ── 缓存用量 ─────────────────────────────────────────────────────────────


@dataclass
class Usage:
    """单次 LLM 调用的 token 用量统计（含缓存字段）。

    Attributes:
        input_tokens: 输入 token 数。
        output_tokens: 输出 token 数。
        cache_write: Anthropic: cache_creation_input_tokens；OpenAI: 恒为 0。
        cache_read: Anthropic: cache_read_input_tokens；OpenAI: prompt_tokens_details.cached_tokens。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0


# ── Request / System（ch05 新增） ────────────────────────────────────────


@dataclass
class System:
    """分离的系统提示：稳定段（可缓存） + 环境段（不缓存）。

    Attributes:
        stable: 模块化装配的稳定系统提示（工具定义随 tools 一并进缓存前缀）。
        environment: 环境信息段（工作目录、平台、日期等），不缓存。
    """

    stable: str = ""
    environment: str = ""


@dataclass
class Request:
    """一次 LLM 流式调用的完整入参。

    Attributes:
        messages: 持久对话历史（不含本轮 reminder）。
        tools: 本轮工具定义列表（协议原生格式）。
        system: 分离的系统提示（稳定段 + 环境段）。
        reminder: 本轮 system-reminder 内容（已含标签；空字符串 = 不注入）。
    """

    messages: list[Message] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    system: System = field(default_factory=System)
    reminder: str = ""


# ── ToolDefinition（协议原生格式） ────────────────────────────────────────

# Anthropic: {"name": str, "description": str, "input_schema": dict}
# OpenAI:    {"type": "function", "function": {"name":..., "description":..., "parameters":...}}
ToolDefinition = dict[str, Any]


# ── Provider 协议 ────────────────────────────────────────────────────────


@runtime_checkable
class Provider(Protocol):
    """Protocol-agnostic interface for an LLM provider."""

    @property
    def name(self) -> str:
        """Human-readable provider name (shown in status bar)."""
        ...

    @property
    def model(self) -> str:
        """Model identifier (shown in status bar)."""
        ...

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        """Stream a response from the LLM.

        The implementation assembles protocol-specific requests from the
        Request dataclass: separates cache channel (stable system block) from
        message channel; safely weaves reminder into the message stream.

        Args:
            req: Complete request with messages, tools, system blocks, and reminder.

        Yields:
            StreamEvent instances: text deltas, a final done event (possibly
            with tool_calls), or an error event.
        """
        ...


# ── Provider 工厂 ────────────────────────────────────────────────────────


def new_provider(cfg: ProviderConfig) -> Provider:
    """Create a Provider instance from configuration.

    Args:
        cfg: A validated provider configuration.

    Returns:
        A Provider implementation matching the configured protocol.

    Raises:
        ValueError: If the protocol is not recognized.
    """
    if cfg.protocol == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    elif cfg.protocol in ("openai", "openai-compat"):
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)
    else:
        raise ValueError(f"Unknown protocol: {cfg.protocol}")

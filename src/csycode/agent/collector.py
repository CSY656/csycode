"""流式收集器模块。

StreamCollector 包装 Provider.stream()，实现双路处理：
- 实时 yield TextDelta / ToolUseEvent 事件给界面
- 同时累积完整文本和工具调用信息
- 流结束后返回 StreamResult 供循环控制判断下一步
- 捕获 provider 产出的 Usage（含缓存用量）

对齐 mewcode StreamCollector：增量构建 response，流期间产出 ToolUseEvent，
使 Agent 可以在 LLM 还在输出时就启动工具执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator

from csycode.llm import Provider, Request, ToolCall, Usage

from .events import AgentEvent, LoopEnd, TextDelta, ToolUseEvent


@dataclass
class LLMResponse:
    """增量构建的 LLM 响应（对齐 mewcode LLMResponse）。"""

    text: str = ""
    tool_calls: list[ToolUseEvent] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


@dataclass
class StreamResult:
    """流式收集器的最终产物（向后兼容）。"""

    text: str  # 累积的完整文本（不含工具调用参数 JSON）
    tool_calls: list[ToolCall]  # 解析出的工具调用（可能为空）
    usage: Usage | None = None  # provider 产出的用量（含缓存字段）
    error: Exception | None = None  # stream 过程中捕获的异常（含 PTL）
    stop_reason: str = ""  # API 返回的结束原因（"stop", "length", "tool_calls"）


class StreamCollector:
    """流式收集器：一边推文本/工具事件给界面，一边攒完整响应用于判断。

    对齐 mewcode：collect() 增量构建 self.response，
    流结束时通过 last_result 暴露最终状态。
    """

    def __init__(self, provider: Provider) -> None:
        self._provider = provider
        self.last_result: StreamResult | None = None
        self.response = LLMResponse()  # 增量构建（对齐 mewcode）

    async def collect(self, req: Request) -> AsyncIterator[AgentEvent]:
        """流式收集主入口。

        调用 Provider.stream(req)，实时 yield TextDelta / ToolUseEvent，
        流结束后通过 ``last_result`` 属性暴露 StreamResult。

        若流出错则 yield LoopEnd(stream_error)。
        PTL 错误不终止循环，存储在 last_result.error 中供 Agent 紧急压缩。
        """
        from csycode.llm import PromptTooLongError

        self.response = LLMResponse()
        stream_error: Exception | None = None

        try:
            async for ev in self._provider.stream(req):
                if ev.err is not None:
                    if isinstance(ev.err, PromptTooLongError):
                        stream_error = ev.err
                        break
                    yield LoopEnd(
                        reason="stream_error",
                        final_text=self.response.text,
                        total_rounds=0,
                        total_input_tokens=0,
                        total_output_tokens=0,
                        error_msg=str(ev.err),
                    )
                    return

                # 文本增量
                if ev.text:
                    self.response.text += ev.text
                    yield TextDelta(text=ev.text)

                # 工具调用完成（流期间）—— 对齐 mewcode ToolUseEvent
                if ev.event_type == "tool_complete":
                    tue = ToolUseEvent(
                        tool_name=ev.tool_name,
                        tool_id=ev.tool_id,
                        arguments=ev.arguments or {},
                    )
                    self.response.tool_calls.append(tue)
                    yield tue

                # 流正常结束
                if ev.done:
                    self.response.stop_reason = ev.stop_reason
                    if ev.usage is not None:
                        self.response.input_tokens = ev.usage.input_tokens
                        self.response.output_tokens = ev.usage.output_tokens
                        self.response.cache_read = ev.usage.cache_read
                        self.response.cache_creation = ev.usage.cache_write
                    # 向后兼容：旧 provider 在 done 事件中传递 tool_calls
                    # （新 provider 在流期间通过 event_type="tool_complete" 传递）
                    if ev.tool_calls and not self.response.tool_calls:
                        for tc in ev.tool_calls:
                            tue = ToolUseEvent(
                                tool_name=tc.name,
                                tool_id=tc.id,
                                arguments=tc.arguments,
                            )
                            self.response.tool_calls.append(tue)
                            yield tue

        except Exception as e:
            from csycode.llm import PromptTooLongError

            if isinstance(e, PromptTooLongError):
                stream_error = e
            else:
                yield LoopEnd(
                    reason="stream_error",
                    final_text=self.response.text,
                    total_rounds=0,
                    total_input_tokens=0,
                    total_output_tokens=0,
                    error_msg=str(e),
                )
                return

        # 构造最终结果（向后兼容 — 从 LLMResponse 映射到 ToolCall 列表）
        parsed_tool_calls: list[ToolCall] = []
        for tue in self.response.tool_calls:
            parsed_tool_calls.append(
                ToolCall(
                    id=tue.tool_id,
                    name=tue.tool_name,
                    arguments=tue.arguments,
                )
            )

        self.last_result = StreamResult(
            text=self.response.text,
            tool_calls=parsed_tool_calls,
            usage=Usage(
                input_tokens=self.response.input_tokens,
                output_tokens=self.response.output_tokens,
                cache_write=self.response.cache_creation,
                cache_read=self.response.cache_read,
            ) if self.response.input_tokens > 0 or self.response.output_tokens > 0 else None,
            error=stream_error,
            stop_reason=self.response.stop_reason,
        )

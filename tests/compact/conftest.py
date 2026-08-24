"""compact 包测试共享 fixture。"""

from __future__ import annotations

import pytest

from csycode.compact.const import (
    AUTO_SAFETY_MARGIN,
    MANUAL_SAFETY_MARGIN,
    SUMMARY_RESERVE,
)
from csycode.llm import Message, StreamEvent, ToolCall, Usage


# ── fake_provider helper ────────────────────────────────────────────────


class FakeProvider:
    """脚本化驱动：按调用次数 yield 不同事件序列。

    用法：
        provider = FakeProvider([
            [StreamEvent(text="..."), StreamEvent(usage=Usage(input_tokens=500)), StreamEvent(done=True)],
            [StreamEvent(err=PromptTooLongError()), ...],
        ])
    """

    def __init__(self, scripts: list[list[StreamEvent]] | None = None):
        self.scripts = scripts or []
        self.call_count = 0
        self.model = "test-model"

    async def stream(self, req):
        """直接 yield 脚本中的事件。"""
        if self.call_count < len(self.scripts):
            script = self.scripts[self.call_count]
        else:
            script = self.scripts[-1] if self.scripts else []
        self.call_count += 1

        for ev in script:
            yield ev


# ── 测试用 Message 工厂 ─────────────────────────────────────────────────


def make_user_msg(content: str) -> Message:
    return Message(role="user", content=content)


def make_assistant_msg(content: str = "", tool_calls: list[ToolCall] | None = None) -> Message:
    return Message(role="assistant", content=content, tool_calls=tool_calls)


def make_tool_result(tool_call_id: str, content: str) -> Message:
    return Message(role="user", content=content, tool_call_id=tool_call_id)


def make_tool_call(id_: str, name: str, arguments: dict | None = None) -> ToolCall:
    return ToolCall(id=id_, name=name, arguments=arguments or {})


# ── 阈值计算 helper ─────────────────────────────────────────────────────


def auto_threshold(context_window: int) -> int:
    return context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN


def manual_threshold(context_window: int) -> int:
    return context_window - SUMMARY_RESERVE - MANUAL_SAFETY_MARGIN

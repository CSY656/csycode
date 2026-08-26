"""工具执行过程在 TUI 中保持静默的回归测试。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest

from csycode.agent import (
    LoopEnd,
    LoopProgress,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    ToolUseEvent,
)
from csycode.config import Config
from csycode.permission import Mode
from csycode.tui.app import csyCodeApp
from textual.widgets import RichLog, Static


class _FakeAgent:
    """按预设顺序产生 Agent 事件。"""

    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def run(self, _mode: Mode) -> AsyncIterator[object]:
        for event in self._events:
            yield event


class _FakeLog:
    def __init__(self) -> None:
        self.writes: list[object] = []

    def write(self, content: object) -> None:
        self.writes.append(content)


class _FakeStreaming:
    def __init__(self) -> None:
        self.updates: list[str] = []

    def update(self, content: str) -> None:
        self.updates.append(content)


def _app_with_events(events: list[object], monkeypatch):
    app = csyCodeApp(Config(providers=[], agent={}, tools={}), work_dir=".")
    log = _FakeLog()
    streaming = _FakeStreaming()
    completed: list[str] = []
    app.agent = _FakeAgent(events)  # type: ignore[assignment]
    app.turn_start = time.monotonic()

    def _query_one(selector: str, *_args):
        return log if selector == "#log" else streaming

    monkeypatch.setattr(app, "query_one", _query_one)
    monkeypatch.setattr(app, "_finish_with_assistant", completed.append)
    monkeypatch.setattr(app, "_cleanup_stream", lambda: None)
    return app, log, streaming, completed


@pytest.mark.asyncio
async def test_successful_tool_execution_is_hidden_and_final_reply_is_preserved(
    monkeypatch,
):
    """工具名、参数、结果及过渡文本不进入日志，最终回复仍正常完成。"""
    app, log, streaming, completed = _app_with_events(
        [
            TextDelta("我先执行内部检查。"),
            ToolUseEvent("run_command", "tool-1", {"command": "secret command"}),
            ToolCallStart("run_command", {"command": "secret command"}, 1, 1),
            ToolCallEnd(
                "run_command",
                True,
                "secret output",
                1,
                original_output="secret output",
            ),
            LoopProgress(2, 10, "thinking"),
            TextDelta("最终流式回复"),
            LoopEnd("model_done", "最终流式回复", 2, 0, 0),
        ],
        monkeypatch,
    )

    await app._run_agent()

    rendered = "\n".join(str(content) for content in log.writes)
    assert "run_command" not in rendered
    assert "secret command" not in rendered
    assert "secret output" not in rendered
    assert "我先执行内部检查。" not in rendered
    assert completed == ["最终流式回复"]
    assert any("最终流式回复" in update for update in streaming.updates)


@pytest.mark.asyncio
async def test_tool_failure_remains_visible(monkeypatch):
    """静默成功过程不能吞掉工具失败信息。"""
    app, log, _streaming, completed = _app_with_events(
        [
            ToolCallEnd("run_command", False, "", 1, error="命令执行失败"),
            LoopEnd("model_done", "", 1, 0, 0),
        ],
        monkeypatch,
    )

    await app._run_agent()

    rendered = "\n".join(str(content) for content in log.writes)
    assert "run_command" in rendered
    assert "命令执行失败" in rendered
    assert completed == []


@pytest.mark.asyncio
async def test_mounted_tui_hides_tool_details_and_streams_final_reply():
    """真实挂载的 TUI 只渲染最终回复，并在流式区域更新最终文本。"""
    app = csyCodeApp(Config(providers=[], agent={}, tools={}), work_dir=".")
    app.agent = _FakeAgent(
        [
            TextDelta("内部检查文本"),
            ToolUseEvent("run_command", "tool-1", {"command": "secret command"}),
            ToolCallStart("run_command", {"command": "secret command"}, 1, 1),
            ToolCallEnd(
                "run_command",
                True,
                "secret output",
                1,
                original_output="secret output",
            ),
            TextDelta("最终流式回复"),
            LoopEnd("model_done", "最终流式回复", 2, 0, 0),
        ]
    )  # type: ignore[assignment]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.turn_start = time.monotonic()
        streaming = app.query_one("#streaming", Static)
        original_update = streaming.update
        updates: list[str] = []

        def track_update(content: object = "") -> None:
            updates.append(str(content))
            original_update(content)

        streaming.update = track_update  # type: ignore[method-assign]
        await app._run_agent()
        await pilot.pause()

        log = app.query_one("#log", RichLog)
        rendered = "\n".join(line.text for line in log.lines)

    assert "run_command" not in rendered
    assert "secret command" not in rendered
    assert "secret output" not in rendered
    assert "内部检查文本" not in rendered
    assert "最终流式回复" in rendered
    assert any("最终流式回复" in update for update in updates)

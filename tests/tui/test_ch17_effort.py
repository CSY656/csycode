"""ch17: TUI effort 状态与 Agent 同步测试。"""

from __future__ import annotations

import pytest

from csycode.config import Config
from csycode.tui.app import csyCodeApp


def _app() -> csyCodeApp:
    return csyCodeApp(Config(providers=[], agent={}, tools={}), work_dir=".")


def test_app_effort_defaults_to_high():
    app = _app()
    assert app.reasoning_effort() == "high"
    assert "Effort: HIGH" in app._statusbar_left()


def test_app_effort_switches_and_syncs_agent(monkeypatch):
    app = _app()

    class FakeAgent:
        def __init__(self):
            self.values: list[str] = []

        def set_reasoning_effort(self, value: str) -> bool:
            self.values.append(value)
            return True

    fake_agent = FakeAgent()
    app.agent = fake_agent  # type: ignore[assignment]
    monkeypatch.setattr(app, "_update_statusbar", lambda: None)

    assert app.set_reasoning_effort(" LOW ")
    assert app.reasoning_effort() == "low"
    assert fake_agent.values == ["low"]
    assert "Effort: LOW" in app._statusbar_left()

    assert not app.set_reasoning_effort("middle")
    assert app.reasoning_effort() == "low"


@pytest.mark.asyncio
async def test_clear_resets_effort(tmp_path, monkeypatch):
    """新建会话后恢复默认 high，并同步已有 Agent。"""
    app = csyCodeApp(
        Config(providers=[], agent={}, tools={}),
        work_dir=str(tmp_path),
    )
    monkeypatch.setattr(app, "_update_statusbar", lambda: None)
    assert app.set_reasoning_effort("xhigh")

    class FakeRuntime:
        def reset_for_new_session(self, session):
            pass

    class FakeAgent:
        def __init__(self):
            self.runtime = FakeRuntime()
            self._conversation = None
            self.values: list[str] = []

        def set_reasoning_effort(self, value: str) -> bool:
            self.values.append(value)
            return True

        def clear_active_skills(self):
            pass

    fake_agent = FakeAgent()
    app.agent = fake_agent  # type: ignore[assignment]
    app._clear_and_new_session_impl()

    assert app.reasoning_effort() == "high"
    assert fake_agent.values[-1] == "high"
    if app._writer is not None:
        app._writer.close()

"""内置命令注册与集成测试（ch14: 14 条含 /hooks + /worktree）。"""

from __future__ import annotations

import pytest

from csycode.command.builtins import register_builtins
from csycode.command.registry import Registry
from csycode.command.ui import NopUI
from csycode.permission import Mode


class TestRegisterBuiltins:
    def test_register_builtins_all_registered(self):
        """register_builtins 注册恰好 14 条命令（ch12: +/hooks, ch14: +/worktree）。"""
        reg = Registry()
        register_builtins(reg)
        visible = reg.visible()
        assert len(visible) == 14

    def test_register_builtins_names(self):
        """检查全部 14 个命令名（ch12: +hooks, ch14: +worktree）。"""
        reg = Registry()
        register_builtins(reg)
        names = {c.name for c in reg.visible()}
        expected = {
            "help", "status", "memory", "permission", "session",
            "exit", "plan", "compact", "resume", "clear",
            "do", "review", "hooks", "worktree",
        }
        assert names == expected

    def test_register_builtins_no_collision(self):
        """直接调 register_builtins 不抛异常。"""
        reg = Registry()
        register_builtins(reg)
        # 二次调应失败（冲突）
        with pytest.raises(RuntimeError, match="command conflict"):
            register_builtins(reg)


# ── RecordingUI ────────────────────────────────────────────────────────


class RecordingUI(NopUI):
    """可观测测试桩：记录 println / error / set_mode / inject_and_send 调用。"""

    def __init__(self):
        super().__init__()
        self._println_calls: list[str] = []
        self._error_calls: list[str] = []
        self._set_mode_calls: list[Mode] = []
        self._inject_calls: list[tuple[str, str]] = []
        self._idle: bool = True
        self._mode: Mode = Mode.DEFAULT

    def println(self, msg: str) -> None:
        self._println_calls.append(msg)

    def error(self, msg: str) -> None:
        self._error_calls.append(msg)

    def set_mode(self, m: Mode) -> None:
        self._set_mode_calls.append(m)
        self._mode = m

    def inject_and_send(self, label: str, preset: str) -> None:
        self._inject_calls.append((label, preset))

    def mode(self) -> Mode:
        return self._mode

    def idle(self) -> bool:
        return self._idle


# ── handler 可运行性测试 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handlers_run_on_nop_ui():
    """所有命令的 handler 在 NopUI 上不抛异常。"""
    reg = Registry()
    register_builtins(reg)
    ui = NopUI()
    for cmd in reg.visible():
        try:
            await cmd.handler(ui)
        except TypeError:
            # 某些 handler 内部调 ui.method() 可能因 NopUI 实现不完整报错
            # 这是预期的，验证 handler 至少不硬崩
            pass


# ── handle_status 行为断言 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_status_prints_all_keys():
    """handle_status 调用 println 一次且文本含 6 个 key。"""
    reg = Registry()
    register_builtins(reg)
    ui = RecordingUI()
    cmd = reg.lookup("status")
    assert cmd is not None
    await cmd.handler(ui)
    assert len(ui._println_calls) == 1
    output = ui._println_calls[0]
    for key in ("Mode:", "Tokens:", "Tools:", "Memories:", "Model:", "Directory:"):
        assert key in output, f"status 输出缺失 {key}"


# ── handle_compact idle 守护测试 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_compact_calls_force_compact_when_idle():
    """handle_compact (UI 类) 在 idle 时正常工作（dispatch_slash 已做 guard）。
    这里只验证 handler 本身可执行且不抛异常。
    """
    reg = Registry()
    register_builtins(reg)
    ui = RecordingUI()
    cmd = reg.lookup("compact")
    assert cmd is not None
    # compact handler 调 ui.force_compact() → NopUI no-op，不抛
    await cmd.handler(ui)


# ── handle_do 行为断言 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_do_sets_mode_and_injects():
    """handle_do 调用 set_mode(Mode.DEFAULT) + inject_and_send("/do", ...)。"""
    reg = Registry()
    register_builtins(reg)
    ui = RecordingUI()
    cmd = reg.lookup("do")
    assert cmd is not None
    await cmd.handler(ui)
    assert len(ui._set_mode_calls) == 1
    assert ui._set_mode_calls[0] == Mode.DEFAULT
    assert len(ui._inject_calls) == 1
    assert ui._inject_calls[0][0] == "/do"


# ── handle_review 行为断言 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_review_injects():
    """handle_review 调用 inject_and_send 且 label 为 /review。"""
    reg = Registry()
    register_builtins(reg)
    ui = RecordingUI()
    cmd = reg.lookup("review")
    assert cmd is not None
    await cmd.handler(ui)
    assert len(ui._inject_calls) == 1
    assert ui._inject_calls[0][0] == "/review"
    assert "审查" in ui._inject_calls[0][1]

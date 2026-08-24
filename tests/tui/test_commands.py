"""TUI 命令分发与格式化函数测试。

这些测试只验证命令路由和文案格式化逻辑，不需要启动 Textual App。
"""

from __future__ import annotations

import pytest

from csycode.agent.events import CompactPhase
from csycode.tui.commands import BUILTIN_COMMANDS, dispatch_command, format_compact_notice


# ── dispatch_command 路由测试 ────────────────────────────────────────


class TestDispatchCommand:
    def test_slash_compact_routes_to_handler(self):
        """输入 /compact → 返回 handler，is_cmd=True。"""
        handler, is_cmd = dispatch_command("/compact")
        assert is_cmd is True
        assert handler is not None

    def test_slash_exit_routes_to_handler(self):
        handler, is_cmd = dispatch_command("/exit")
        assert is_cmd is True
        assert handler is not None

    def test_slash_plan_routes_to_handler(self):
        handler, is_cmd = dispatch_command("/plan")
        assert is_cmd is True
        assert handler is not None

    def test_slash_do_routes_to_handler(self):
        handler, is_cmd = dispatch_command("/do")
        assert is_cmd is True
        assert handler is not None

    def test_unknown_slash_command_friendly(self):
        """未注册的 / 命令 → 走兜底处理器。"""
        handler, is_cmd = dispatch_command("/unknown")
        assert is_cmd is True
        assert handler is not None
        # 不是 None，是 _unknown_command

    def test_non_slash_text_not_command(self):
        """非 / 开头文本 → is_cmd=False。"""
        handler, is_cmd = dispatch_command("hello world")
        assert is_cmd is False
        assert handler is None


# ── 注册表内容测试 ───────────────────────────────────────────────────


class TestBuiltinCommands:
    def test_all_four_commands_registered(self):
        assert "/exit" in BUILTIN_COMMANDS
        assert "/plan" in BUILTIN_COMMANDS
        assert "/do" in BUILTIN_COMMANDS
        assert "/compact" in BUILTIN_COMMANDS

    def test_compact_handler_is_callable(self):
        handler = BUILTIN_COMMANDS["/compact"]
        import asyncio
        assert asyncio.iscoroutinefunction(handler)


# ── format_compact_notice 文案测试 ───────────────────────────────────


class TestFormatCompactNotice:
    def test_before_auto(self):
        result = format_compact_notice(phase=CompactPhase.BEFORE_AUTO)
        assert result == "正在压缩上下文..."

    def test_before_emergency(self):
        result = format_compact_notice(phase=CompactPhase.BEFORE_EMERGENCY)
        assert result == "上下文撞墙，自动压缩中..."

    def test_after_auto_success(self):
        result = format_compact_notice(
            phase=CompactPhase.AFTER_AUTO, before=167000, after=12000,
        )
        assert "已压缩" in result
        assert "167,000" in result
        assert "12,000" in result

    def test_after_auto_error(self):
        result = format_compact_notice(
            phase=CompactPhase.AFTER_AUTO, err="摘要失败",
        )
        assert "压缩失败" in result
        assert "摘要失败" in result

    def test_manual_path_no_phase(self):
        """手动路径 phase=None 使用通用逻辑。"""
        result = format_compact_notice(before=100000, after=5000)
        assert "已压缩" in result

    def test_error_no_phase(self):
        result = format_compact_notice(err="something went wrong")
        assert "压缩失败" in result
        assert "something went wrong" in result

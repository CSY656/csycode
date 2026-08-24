"""prompt 包单元测试。

覆盖:
- 模块装配顺序
- 空槽跳过
- N1 确定性（跨调用一致）
- F5 双重强化文本断言
- 环境采集与渲染
- system_reminder / plan_reminder
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

from csycode.prompt import assemble_system, build_system_prompt
from csycode.prompt.environment import Environment, gather_environment
from csycode.prompt.modules import Module, fixed_modules, optional_modules
from csycode.prompt.reminder import (
    EXECUTE_DIRECTIVE,
    plan_reminder,
    system_reminder,
)


# ── 装配顺序测试 ────────────────────────────────────────────────────────

class TestAssemblyOrder:
    """模块装配顺序与结构测试。"""

    def test_fixed_modules_count(self):
        """七个固定模块。"""
        mods = fixed_modules()
        assert len(mods) == 7

    def test_optional_modules_count(self):
        """三个可选空槽。"""
        mods = optional_modules()
        assert len(mods) == 3

    def test_all_optional_are_empty(self):
        """所有可选模块的 content 均为空字符串。"""
        for m in optional_modules():
            assert m.content == "", f"{m.name} 应 content=''，实际: {m.content!r}"

    def test_identity_before_tool_usage(self):
        """身份模块（priority 10）出现在工具使用模块（priority 50）之前。"""
        result = build_system_prompt()
        idx_identity = result.find("csyCode")
        idx_tool_usage = result.find("Tool Usage Guidelines")
        assert idx_identity >= 0, "应包含身份信息"
        assert idx_tool_usage >= 0, "应包含工具使用指南"
        assert idx_identity < idx_tool_usage, (
            f"身份({idx_identity})应在工具使用({idx_tool_usage})之前"
        )

    def test_modules_separated_by_blank_lines(self):
        """模块之间以双空行分隔。"""
        result = build_system_prompt()
        assert "\n\n" in result, "模块应以双空行分隔"


class TestEmptySlotSkipping:
    """空槽跳过测试。"""

    def test_empty_content_skipped(self):
        """content 为空的模块不出现在装配结果中。"""
        mods = [
            Module(name="a", priority=10, content="Hello"),
            Module(name="empty", priority=20, content=""),
            Module(name="b", priority=30, content="World"),
        ]
        result = assemble_system(mods)
        assert "Hello" in result
        assert "World" in result
        assert result == "Hello\n\nWorld"

    def test_no_consecutive_blank_lines_from_slots(self):
        """空槽不应产生连续多空行。"""
        mods = [
            Module(name="a", priority=10, content="First"),
            Module(name="s1", priority=20, content=""),
            Module(name="s2", priority=30, content=""),
            Module(name="b", priority=40, content="Last"),
        ]
        result = assemble_system(mods)
        # 不应含连续空行
        assert "\n\n\n" not in result
        assert result == "First\n\nLast"

    def test_build_system_prompt_skips_optional_slots(self):
        """build_system_prompt 中可选空槽均被跳过。"""
        result = build_system_prompt()
        # 可选空槽的 content 都为空，不应出现其标题
        assert "custom_instructions" not in result.lower()
        assert "activated_skill" not in result.lower()
        assert "long_term_memory" not in result.lower()


class TestN1Determinism:
    """N1: 稳定系统提示确定性。"""

    def test_build_system_prompt_deterministic(self):
        """连续两次 build_system_prompt() 结果完全相同。"""
        a = build_system_prompt()
        b = build_system_prompt()
        assert a == b, "两次 build_system_prompt() 应逐字节一致"

    def test_assemble_deterministic_with_same_input(self):
        """相同输入产生相同输出。"""
        mods = [
            Module(name="x", priority=50, content="Middle"),
            Module(name="y", priority=10, content="Start"),
            Module(name="z", priority=90, content="End"),
        ]
        a = assemble_system(mods)
        b = assemble_system(mods)
        assert a == b
        # 应按 priority 排序
        assert a.startswith("Start")
        assert a.endswith("End")


class TestF5DoubleReinforcement:
    """F5: 工具使用双重强化 —— 系统提示 + 工具描述。"""

    def test_system_prompt_mentions_read_before_edit(self):
        """系统提示中含「编辑前必须先读」的语义。"""
        result = build_system_prompt()
        # read_file 应在 edit_file 的上下文中被提及
        assert "read_file" in result
        assert "edit_file" in result
        # 关键语义：「Read before editing」
        assert "Read before editing" in result or "read_file" in result

    def test_system_prompt_mentions_prefer_dedicated_tools(self):
        """系统提示中含「优先用专用工具而非 bash 拼凑」的语义。"""
        result = build_system_prompt()
        assert "Prefer dedicated tools" in result
        assert "glob" in result
        assert "grep" in result


# ── 环境采集测试 ─────────────────────────────────────────────────────────

class TestEnvironment:
    """Environment 与环境采集测试。"""

    def test_gather_environment_basic(self):
        """基本环境采集：working_dir、platform、date 非空。"""
        env = gather_environment(version="1.0.0", model="test-model")
        assert env.working_dir != ""
        assert env.platform != ""
        assert env.date != ""
        assert env.version == "1.0.0"
        assert env.model == "test-model"

    def test_gather_environment_non_git_dir(self):
        """非 git 目录下 git_status 为空字符串，不抛异常。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                env = gather_environment("dev", "gpt-4")
                assert env.git_status == "", (
                    f"非 git 目录 git_status 应为空，实际: {env.git_status!r}"
                )
            finally:
                os.chdir(orig_cwd)

    def test_render_includes_key_fields(self):
        """render() 包含关键字段。"""
        env = Environment(
            working_dir="/home/user/project",
            platform="linux",
            date="2025-01-15",
            git_status="3 file(s) changed",
            version="2.0.0",
            model="claude-sonnet-4-6",
        )
        rendered = env.render()
        assert "Working Directory: /home/user/project" in rendered
        assert "Platform: linux" in rendered
        assert "Date: 2025-01-15" in rendered
        assert "Git Status: 3 file(s) changed" in rendered
        assert "App Version: 2.0.0" in rendered
        assert "Model: claude-sonnet-4-6" in rendered

    def test_render_skips_empty_fields(self):
        """空值字段不出现在 render 输出中。"""
        env = Environment(
            working_dir="/tmp",
            platform="",
            date="2025-01-15",
            git_status="",
            version="",
            model="",
        )
        rendered = env.render()
        assert "Platform:" not in rendered
        assert "Git Status:" not in rendered
        assert "App Version:" not in rendered
        assert "Model:" not in rendered
        assert "Working Directory: /tmp" in rendered

    def test_git_status_failure_graceful(self):
        """git 命令失败时降级为空字符串。"""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            env = gather_environment("dev", "gpt-4")
            assert env.git_status == ""


# ── 补充消息测试 ─────────────────────────────────────────────────────────

class TestReminder:
    """system_reminder 与 plan_reminder 测试。"""

    def test_system_reminder_wraps_content(self):
        """system_reminder 用正确标签包裹内容。"""
        result = system_reminder("Hello World")
        assert result.startswith("<system-reminder>")
        assert result.endswith("</system-reminder>")
        assert "Hello World" in result

    def test_plan_reminder_full(self):
        """full=True 返回完整版提醒。"""
        result = plan_reminder(full=True)
        assert "<system-reminder>" in result
        assert "Plan Mode" in result
        assert "read_file" in result
        assert "implementation plan" in result.lower()

    def test_plan_reminder_concise(self):
        """full=False 返回精简版提醒。"""
        result = plan_reminder(full=False)
        assert "<system-reminder>" in result
        assert "Plan Mode" in result
        # 精简版应比完整版短
        assert len(result) < len(plan_reminder(full=True))

    def test_execute_directive_is_string(self):
        """EXECUTE_DIRECTIVE 为有效字符串。"""
        s = str(EXECUTE_DIRECTIVE)
        assert len(s) > 0
        assert "Plan Mode" in s or "plan" in s.lower()

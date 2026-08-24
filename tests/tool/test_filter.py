"""T9: 工具过滤多层防线测试。"""

import pytest
from csycode.tool.filter import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
    FilterParams,
    _is_mcp_or_skill,
    apply_agent_tool_filter,
)


class TestConstants:
    """常量存在性测试。"""

    def test_all_agent_disallowed_not_empty(self):
        assert len(ALL_AGENT_DISALLOWED_TOOLS) > 0
        assert "Agent" in ALL_AGENT_DISALLOWED_TOOLS

    def test_async_agent_allowed_not_empty(self):
        assert len(ASYNC_AGENT_ALLOWED_TOOLS) > 0
        assert "read_file" in ASYNC_AGENT_ALLOWED_TOOLS


class TestIsMcpOrSkill:
    """_is_mcp_or_skill 测试。"""

    def test_mcp_tool(self):
        assert _is_mcp_or_skill("mcp__filesystem__read") is True

    def test_skill_tool(self):
        assert _is_mcp_or_skill("skill__my-skill") is True

    def test_normal_tool(self):
        assert _is_mcp_or_skill("read_file") is False
        assert _is_mcp_or_skill("bash") is False


class TestApplyFilter:
    """apply_agent_tool_filter 层叠测试。"""

    ALL_TOOLS = [
        "read_file", "write_file", "edit_file",
        "glob", "grep", "run_command",
        "ask_user_question", "exit_plan_mode",
        "Agent", "TaskOutput", "ExitPlanMode",
        "mcp__server__tool",
    ]

    def test_default_no_background(self):
        """默认：去全局禁止，保留其他。"""
        params = FilterParams(all=self.ALL_TOOLS[:], background=False)
        result = apply_agent_tool_filter(params)
        assert "Agent" not in result
        assert "TaskOutput" not in result
        assert "read_file" in result
        assert "mcp__server__tool" in result  # MCP 始终放行

    def test_background_whitelist(self):
        """后台模式：与 ASYNC_AGENT_ALLOWED_TOOLS 取交集。"""
        params = FilterParams(all=self.ALL_TOOLS[:], background=True)
        result = apply_agent_tool_filter(params)
        assert "read_file" in result
        assert "write_file" in result
        assert "glob" in result
        assert "grep" in result
        assert "run_command" in result
        # Agent 被全局禁止
        assert "Agent" not in result
        # ask_user_question 不在后台白名单
        assert "ask_user_question" not in result
        # MCP 工具始终放行
        assert "mcp__server__tool" in result

    def test_disallowed_blacklist(self):
        """定义黑名单：去掉 disallowed。"""
        params = FilterParams(
            all=self.ALL_TOOLS[:],
            disallowed=["run_command", "write_file"],
        )
        result = apply_agent_tool_filter(params)
        assert "write_file" not in result
        assert "run_command" not in result
        assert "read_file" in result

    def test_allowed_whitelist(self):
        """定义白名单：仅保留 allowed。"""
        params = FilterParams(
            all=self.ALL_TOOLS[:],
            allowed=["read_file", "glob"],
        )
        result = apply_agent_tool_filter(params)
        assert "read_file" in result
        assert "glob" in result
        assert "grep" not in result
        assert "write_file" not in result
        # MCP 仍在（始终放行）
        assert "mcp__server__tool" in result

    def test_blacklist_and_whitelist(self):
        """黑白名单组合：白名单先收窄，黑名单再排除。"""
        params = FilterParams(
            all=self.ALL_TOOLS[:],
            allowed=["read_file", "write_file", "glob", "run_command"],
            disallowed=["write_file"],
        )
        result = apply_agent_tool_filter(params)
        assert "read_file" in result
        assert "write_file" not in result  # 被黑名单排除
        assert "glob" in result

    def test_custom_agent_extra_disallowed(self):
        """自定义 Agent（source >= 1）额外禁用 CUSTOM_AGENT_DISALLOWED_TOOLS。"""
        params = FilterParams(all=self.ALL_TOOLS[:], source=1)  # USER
        result = apply_agent_tool_filter(params)
        # 全局禁止已处理，自定义禁止与全局禁止集相同（本期一致）
        assert "Agent" not in result
        assert "TaskOutput" not in result

    def test_mcp_always_preserved(self):
        """MCP 工具在任何过滤组合下始终保留。"""
        params = FilterParams(
            all=self.ALL_TOOLS[:],
            background=True,
            allowed=["read_file"],  # 极窄白名单
        )
        result = apply_agent_tool_filter(params)
        assert "mcp__server__tool" in result

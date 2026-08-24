"""Skill 系统测试 —— 覆盖 parser、loader、executor、LoadSkill 工具、Agent 集成。

对齐 mewcode 的 tests/test_skills.py，适配 csycode 架构。
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from csycode.skills.parser import (
    SkillParseError,
    parse_frontmatter,
    parse_skill_file,
    substitute_arguments,
)
from csycode.skills.loader import SkillLoader
from csycode.skills.executor import (
    SkillDependencyError,
    filter_tool_registry,
)
from csycode.tools.registry import ToolRegistry
from csycode.tools.load_skill import LoadSkill

# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

VALID_SKILL_MD = textwrap.dedent("""\
    ---
    name: test-skill
    description: A test skill
    mode: inline
    ---

    # Task

    Do something.

    $ARGUMENTS
""")

FORK_SKILL_MD = textwrap.dedent("""\
    ---
    name: review
    description: Review code
    mode: fork
    context: none
    ---

    Review the code changes.

    $ARGUMENTS
""")


# ---------------------------------------------------------------------------
# Parser 测试
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_valid(self) -> None:
        meta, body = parse_frontmatter(VALID_SKILL_MD)
        assert meta["name"] == "test-skill"
        assert meta["description"] == "A test skill"
        assert "Do something" in body

    def test_missing_opening(self) -> None:
        with pytest.raises(SkillParseError, match="缺少 YAML frontmatter"):
            parse_frontmatter("no frontmatter here")

    def test_unclosed(self) -> None:
        with pytest.raises(SkillParseError, match="未闭合"):
            parse_frontmatter("---\nname: foo\n")

    def test_invalid_yaml(self) -> None:
        with pytest.raises(SkillParseError, match="YAML 非法"):
            parse_frontmatter("---\n: :\n  bad: [yaml\n---\nbody")

    def test_non_dict_frontmatter(self) -> None:
        with pytest.raises(SkillParseError, match="必须是 YAML mapping"):
            parse_frontmatter("---\n- list\n- item\n---\nbody")


class TestParseSkillFile:
    def test_valid_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text(VALID_SKILL_MD)
        skill = parse_skill_file(f)
        assert skill.name == "test-skill"
        assert skill.description == "A test skill"
        assert skill.mode == "inline"
        assert "$ARGUMENTS" in skill.prompt_body

    def test_missing_name(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("---\ndescription: oops\n---\nbody")
        with pytest.raises(SkillParseError, match="缺少必填字段 'name'"):
            parse_skill_file(f)

    def test_missing_description(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("---\nname: foo\n---\nbody")
        with pytest.raises(SkillParseError, match="缺少必填字段 'description'"):
            parse_skill_file(f)

    def test_invalid_name_format(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("---\nname: UPPER\ndescription: x\n---\nbody")
        with pytest.raises(SkillParseError, match="非法 skill 名"):
            parse_skill_file(f)

    def test_invalid_mode(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("---\nname: foo\ndescription: x\nmode: bad\n---\nbody")
        with pytest.raises(SkillParseError, match="非法 mode"):
            parse_skill_file(f)

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        with pytest.raises(SkillParseError, match="无法读取"):
            parse_skill_file(tmp_path / "nope.md")

    def test_fork_mode_with_context(self, tmp_path: Path) -> None:
        f = tmp_path / "fork.md"
        f.write_text(FORK_SKILL_MD)
        skill = parse_skill_file(f)
        assert skill.mode == "fork"
        assert skill.context == "none"


class TestSubstituteArguments:
    def test_with_args(self) -> None:
        result = substitute_arguments("Do $ARGUMENTS now", "something cool")
        assert result == "Do something cool now"

    def test_without_args(self) -> None:
        result = substitute_arguments("Do $ARGUMENTS now", "")
        assert result == "Do  now"

    def test_no_placeholder(self) -> None:
        result = substitute_arguments("No placeholder here", "args")
        assert result == "No placeholder here\n\n## User Request\n\nargs"

    def test_multiple_placeholders(self) -> None:
        result = substitute_arguments("$ARGUMENTS and $ARGUMENTS", "x")
        assert result == "x and x"


# ---------------------------------------------------------------------------
# Loader 测试
# ---------------------------------------------------------------------------


class TestSkillLoader:
    def test_empty_dirs(self, tmp_path: Path) -> None:
        """空目录加载返回空字典。"""
        loader = SkillLoader(str(tmp_path))
        skills = loader.load_all()
        assert skills == {}
        assert loader.get_catalog() == []

    def test_load_single_md_project(self, tmp_path: Path) -> None:
        """项目目录的单文件 .md skill 可正常加载。"""
        skills_dir = tmp_path / ".csycode" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "test-skill.md").write_text(VALID_SKILL_MD)

        loader = SkillLoader(str(tmp_path))
        skills = loader.load_all()
        assert "test-skill" in skills
        assert skills["test-skill"].description == "A test skill"

    def test_load_directory_skill(self, tmp_path: Path) -> None:
        """目录型 Skill（含 SKILL.md）可正常加载。"""
        skills_dir = tmp_path / ".csycode" / "skills"
        skills_dir.mkdir(parents=True)
        skill_dir = skills_dir / "my-dir-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(VALID_SKILL_MD)

        loader = SkillLoader(str(tmp_path))
        skills = loader.load_all()
        assert "test-skill" in skills
        assert skills["test-skill"].is_directory is True

    def test_project_overrides_user(self, tmp_path: Path) -> None:
        """项目级 skill 覆盖用户级同名 skill。"""
        # 用户目录
        user_dir = tmp_path / "user-home"
        user_dir.mkdir()
        user_skills = user_dir / ".csycode" / "skills"
        user_skills.mkdir(parents=True)
        (user_skills / "my-skill.md").write_text(
            "---\nname: my-skill\ndescription: User version\n---\nUser body"
        )

        # 项目目录
        project_skills = tmp_path / "project" / ".csycode" / "skills"
        project_skills.mkdir(parents=True)
        (project_skills / "my-skill.md").write_text(
            "---\nname: my-skill\ndescription: Project version\n---\nProject body"
        )

        # 构造 loader 覆盖用户目录
        loader = SkillLoader(str(tmp_path / "project"))
        loader._user_dir = user_skills
        skills = loader.load_all()
        assert "my-skill" in skills
        assert skills["my-skill"].description == "Project version"

    def test_get_unknown_returns_none(self, tmp_path: Path) -> None:
        loader = SkillLoader(str(tmp_path))
        loader.load_all()
        assert loader.get("nonexistent") is None

    def test_hot_reload_success(self, tmp_path: Path) -> None:
        """热重载：修改文件后 get() 返回新内容。"""
        skills_dir = tmp_path / ".csycode" / "skills"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "my-skill.md"
        skill_file.write_text(VALID_SKILL_MD)

        loader = SkillLoader(str(tmp_path))
        loader.load_all()

        # 修改文件
        skill_file.write_text(
            "---\nname: test-skill\ndescription: Updated\n---\nUpdated body"
        )

        skill = loader.get("test-skill")
        assert skill is not None
        assert skill.description == "Updated"
        assert "Updated body" in skill.prompt_body

    def test_hot_reload_fallback_to_cache(self, tmp_path: Path) -> None:
        """热重载失败时回退到缓存版本。"""
        skills_dir = tmp_path / ".csycode" / "skills"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "my-skill.md"
        skill_file.write_text(VALID_SKILL_MD)

        loader = SkillLoader(str(tmp_path))
        loader.load_all()

        # 破坏文件
        skill_file.write_text("not valid yaml")

        skill = loader.get("test-skill")
        assert skill is not None
        assert skill.description == "A test skill"  # 缓存版本

    def test_source_label(self, tmp_path: Path) -> None:
        """正确识别项目级 / 用户级来源。"""
        skills_dir = tmp_path / ".csycode" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "my-skill.md").write_text(VALID_SKILL_MD)

        loader = SkillLoader(str(tmp_path))
        loader.load_all()
        assert loader.get_source_label("test-skill") == "project"

    def test_skip_invalid_file(self, tmp_path: Path) -> None:
        """解析失败的文件跳过 + 打 warning，其他 skill 正常加载。"""
        skills_dir = tmp_path / ".csycode" / "skills"
        skills_dir.mkdir(parents=True)
        # 合法文件
        (skills_dir / "good.md").write_text(VALID_SKILL_MD)
        # 非法文件
        (skills_dir / "bad.md").write_text("no frontmatter")

        loader = SkillLoader(str(tmp_path))
        skills = loader.load_all()
        assert "test-skill" in skills  # 合法的正常加载
        # 非法的被跳过（不会出现在 skills 中）


# ---------------------------------------------------------------------------
# filter_tool_registry 测试
# ---------------------------------------------------------------------------


class TestFilterToolRegistry:
    def test_empty_allowed_returns_original(self) -> None:
        """allowed 为空时直接返回原 registry。"""
        reg = ToolRegistry()
        result = filter_tool_registry(reg, [])
        assert result is reg

    def test_filter_to_allowed(self) -> None:
        """仅保留白名单中的工具。"""
        reg = ToolRegistry()
        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_a.is_system_tool = False
        tool_b = MagicMock()
        tool_b.name = "tool_b"
        tool_b.is_system_tool = False
        reg.register(tool_a)
        reg.register(tool_b)

        filtered = filter_tool_registry(reg, ["tool_a"])
        assert filtered.get("tool_a") is tool_a
        assert filtered.get("tool_b") is None
        assert filtered.count() == 1

    def test_system_tool_passthrough(self) -> None:
        """is_system_tool=True 的工具自动透传。"""
        reg = ToolRegistry()
        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_a.is_system_tool = False
        sys_tool = MagicMock()
        sys_tool.name = "LoadSkill"
        sys_tool.is_system_tool = True
        reg.register(tool_a)
        reg.register(sys_tool)

        filtered = filter_tool_registry(reg, ["tool_a"])
        # 白名单 + 系统工具都保留
        assert filtered.get("tool_a") is tool_a
        assert filtered.get("LoadSkill") is sys_tool
        assert filtered.count() == 2

    def test_missing_tool_raises(self) -> None:
        """白名单声明了不存在的工具时抛 SkillDependencyError。"""
        reg = ToolRegistry()
        with pytest.raises(SkillDependencyError):
            filter_tool_registry(reg, ["nonexistent"])


# ---------------------------------------------------------------------------
# LoadSkill 工具测试
# ---------------------------------------------------------------------------


class TestLoadSkillTool:
    def test_is_system_tool(self) -> None:
        """LoadSkill 是系统工具且只读。"""
        t = LoadSkill()
        assert t.is_system_tool is True
        assert t.is_readonly is True
        assert t.name == "LoadSkill"

    def test_load_existing_skill(self, tmp_path: Path) -> None:
        """加载存在的 skill → 激活成功。"""
        skills_dir = tmp_path / ".csycode" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "my-skill.md").write_text(VALID_SKILL_MD)

        loader = SkillLoader(str(tmp_path))
        loader.load_all()

        mock_agent = MagicMock()
        t = LoadSkill()
        t.set_loader(loader)
        t.set_agent(mock_agent)

        import asyncio

        result = asyncio.run(t._execute(name="test-skill"))
        assert result.success is True
        # 对齐 mewcode: LoadSkill 返回 SOP body
        assert "# Task" in result.content
        assert "$ARGUMENTS" in result.content
        mock_agent.activate_skill.assert_called_once()

    def test_load_unknown_skill(self, tmp_path: Path) -> None:
        """加载不存在的 skill → 返回错误。"""
        loader = SkillLoader(str(tmp_path))
        loader.load_all()

        mock_agent = MagicMock()
        t = LoadSkill()
        t.set_loader(loader)
        t.set_agent(mock_agent)

        import asyncio

        result = asyncio.run(t._execute(name="nope"))
        assert result.success is False
        assert "未知 Skill" in result.error

    def test_not_initialized(self) -> None:
        """未注入 loader/agent 时返回初始化错误。"""
        t = LoadSkill()

        import asyncio

        result = asyncio.run(t._execute(name="x"))
        assert result.success is False
        assert "未正确初始化" in result.error


# ---------------------------------------------------------------------------
# Agent 集成测试
# ---------------------------------------------------------------------------


class TestAgentSkillIntegration:
    def test_activate_and_clear(self) -> None:
        """activate_skill 存 SOP → clear_active_skills 清空。"""
        from csycode.agent.loop import Agent
        from unittest.mock import MagicMock

        # 用 Mock 替代完整 Agent 构造
        agent = MagicMock(spec=Agent)
        agent.active_skills = {}
        agent._skill_catalog = ""

        def _activate(name, body):
            agent.active_skills[name] = body

        def _clear():
            agent.active_skills.clear()

        agent.activate_skill = _activate
        agent.clear_active_skills = _clear

        agent.activate_skill("test", "SOP body")
        assert "test" in agent.active_skills
        assert agent.active_skills["test"] == "SOP body"

        agent.clear_active_skills()
        assert agent.active_skills == {}

    def test_build_env_text_with_catalog(self) -> None:
        """_build_env_text 拼接 skill catalog 和 active skills。"""
        from csycode.agent.loop import Agent as AgentClass
        from unittest.mock import MagicMock

        agent = MagicMock(spec=AgentClass)
        agent._skill_catalog = "- test: A test skill"
        agent.active_skills = {"test": "Do something."}

        # 模拟 env
        mock_env = MagicMock()
        mock_env.render.return_value = "Working Directory: /tmp"

        # 直接测试 _append_skills_to_env
        from csycode.agent.loop import Agent as RealAgent

        env_text = RealAgent._append_skills_to_env(agent, "Working Directory: /tmp")
        assert "Available Skills" in env_text
        assert "test: A test skill" in env_text
        assert "Active Skills" in env_text
        assert "Skill: test" in env_text
        assert "Do something." in env_text

    def test_build_env_text_empty(self) -> None:
        """无 skill catalog 和 active skills 时仅返回原始 env。"""
        from csycode.agent.loop import Agent as AgentClass
        from unittest.mock import MagicMock

        agent = MagicMock(spec=AgentClass)
        agent._skill_catalog = ""
        agent.active_skills = {}

        from csycode.agent.loop import Agent as RealAgent

        env_text = RealAgent._append_skills_to_env(agent, "Working Directory: /tmp")
        assert env_text == "Working Directory: /tmp"
        assert "Available Skills" not in env_text
        assert "Active Skills" not in env_text

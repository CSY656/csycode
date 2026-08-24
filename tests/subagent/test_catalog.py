"""T7: Catalog 多来源加载与覆盖测试。"""

import pytest
from csycode.subagent.definition import Definition, Source
from csycode.subagent.catalog import Catalog, load_catalog
from csycode.subagent.embed import builtin_definitions


class TestBuiltinDefinitions:
    """内置定义测试。"""

    def test_three_builtins(self):
        """内置定义返回 3 个。"""
        defs = builtin_definitions()
        assert len(defs) == 3
        names = {d.name for d in defs}
        assert "general-purpose" in names
        assert "Explore" in names
        assert "Plan" in names

    def test_builtins_have_system_prompt(self):
        """每个内置定义都有 system_prompt。"""
        for d in builtin_definitions():
            assert d.system_prompt, f"{d.name} 缺少 system_prompt"
            assert d.description, f"{d.name} 缺少 description"


class TestCatalogResolve:
    """Catalog.resolve 测试。"""

    def test_resolve_builtin(self):
        """resolve 内置定义。"""
        c = Catalog()
        c._add_all(builtin_definitions(), Source.BUILTIN)

        d = c.resolve("Explore")
        assert d is not None
        assert d.name == "Explore"
        assert d.source == Source.BUILTIN

    def test_resolve_missing(self):
        """resolve 不存在的定义返回 None。"""
        c = Catalog()
        c._add_all(builtin_definitions(), Source.BUILTIN)
        assert c.resolve("nonexistent") is None

    def test_fork_definition(self):
        """fork_definition 返回 is_fork() True。"""
        c = Catalog()
        fd = c.fork_definition()
        assert fd.is_fork()
        assert fd.name == "__fork__"
        assert fd.model == "inherit"


class TestCatalogOverride:
    """三层覆盖优先级测试。"""

    def test_project_overrides_builtin(self, tmp_path):
        """项目级覆盖内置。"""
        c = Catalog()
        c._add_all(builtin_definitions(), Source.BUILTIN)

        # 项目级覆盖 Explore
        project_def = Definition(
            name="Explore",
            description="Project-level Explore",
            system_prompt="Project explore body",
            source=Source.PROJECT,
        )
        c._add_all([project_def], Source.PROJECT)

        d = c.resolve("Explore")
        assert d is not None
        assert d.source == Source.PROJECT
        assert d.system_prompt == "Project explore body"

    def test_project_overrides_user_and_builtin(self):
        """项目级 > 用户级 > 内置级（后加载覆盖前加载）。"""
        c = Catalog()
        c._add_all(builtin_definitions(), Source.BUILTIN)

        # 用户级覆盖（后加）
        user_def = Definition(
            name="Explore",
            description="User Explore",
            system_prompt="User explore body",
            source=Source.USER,
        )
        c._add_all([user_def], Source.USER)

        # 项目级覆盖（最后加，优先级最高）
        project_def = Definition(
            name="Explore",
            description="Project Explore",
            system_prompt="Project explore body",
            source=Source.PROJECT,
        )
        c._add_all([project_def], Source.PROJECT)

        d = c.resolve("Explore")
        assert d is not None
        assert d.source == Source.PROJECT
        assert d.system_prompt == "Project explore body"

    def test_list_all_sorted(self):
        """list_all 返回按 name 排序的列表。"""
        c = Catalog()
        c._add_all(builtin_definitions(), Source.BUILTIN)
        all_defs = c.list_all()
        names = [d.name for d in all_defs]
        assert names == sorted(names)

    def test_list_by_source(self):
        """list_by_source 返回指定来源的定义。"""
        c = Catalog()
        c._add_all(builtin_definitions(), Source.BUILTIN)
        builtins = c.list_by_source(Source.BUILTIN)
        assert len(builtins) == 3
        projects = c.list_by_source(Source.PROJECT)
        assert len(projects) == 0


class TestLoadCatalog:
    """load_catalog 集成测试。"""

    def test_loads_builtins(self):
        """load_catalog 至少加载内置定义。"""
        c = load_catalog(".")
        assert len(c.list_all()) >= 3

    def test_loads_project_agents(self, tmp_path, monkeypatch):
        """加载项目级 .csycode/agents/*.md。"""
        agents_dir = tmp_path / ".csycode" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "my-agent.md").write_text("""---
name: my-agent
description: Custom project agent
---

Custom body.
""", encoding="utf-8")

        # 假造 HOME 避免读取真实用户级 agents
        monkeypatch.setenv("HOME", str(tmp_path))

        c = load_catalog(str(tmp_path))
        d = c.resolve("my-agent")
        assert d is not None
        assert d.name == "my-agent"
        assert d.source == Source.PROJECT
        assert d.system_prompt == "Custom body."

    def test_skips_invalid_files(self, tmp_path, monkeypatch, capsys):
        """加载时跳过格式错误的文件并打印 stderr 警告。"""
        agents_dir = tmp_path / ".csycode" / "agents"
        agents_dir.mkdir(parents=True)
        # 写一个非法文件
        (agents_dir / "bad.md").write_text("No frontmatter here", encoding="utf-8")

        monkeypatch.setenv("HOME", str(tmp_path))

        c = load_catalog(str(tmp_path))
        # bad.md 被跳过，内置定义仍正常
        d = c.resolve("bad")
        assert d is None
        captured = capsys.readouterr()
        assert "跳过" in captured.err or "bad" in captured.err

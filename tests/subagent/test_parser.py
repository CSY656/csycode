"""T3: subagent 解析器测试。"""

import pytest
from csycode.subagent.definition import Definition, Source
from csycode.subagent.parser import (
    AgentParseError,
    parse_definition,
    parse_file,
)


class TestParseDefinition:
    """parse_definition 单元测试。"""

    def test_minimal_valid(self):
        """最小合法 frontmatter。"""
        data = """---
name: test-agent
description: A test agent
---

Hello world
"""
        d = parse_definition(data, "test.md", Source.PROJECT)
        assert d.name == "test-agent"
        assert d.description == "A test agent"
        assert d.system_prompt == "Hello world"
        assert d.source == Source.PROJECT
        assert d.model == "inherit"
        assert d.max_turns == 0
        assert d.permission_mode == "default"
        assert d.dont_ask is False
        assert d.background is False

    def test_full_frontmatter(self):
        """完整 frontmatter 所有字段。"""
        data = """---
name: full-agent
description: Full featured agent
tools:
  - read_file
  - glob
disallowedTools:
  - write_file
  - bash
model: haiku
maxTurns: 10
permissionMode: acceptEdits
background: true
---

System prompt body here.
"""
        d = parse_definition(data)
        assert d.name == "full-agent"
        assert d.description == "Full featured agent"
        assert d.tools == ["read_file", "glob"]
        assert d.disallowed_tools == ["write_file", "bash"]
        assert d.model == "haiku"
        assert d.max_turns == 10
        assert d.permission_mode == "acceptEdits"
        assert d.background is True
        assert d.system_prompt == "System prompt body here."

    def test_dont_ask_mode(self):
        """permissionMode=dontAsk → dont_ask=True, permission_mode=default。"""
        data = """---
name: auto
description: Auto approve agent
permissionMode: dontAsk
---

body
"""
        d = parse_definition(data)
        assert d.dont_ask is True
        assert d.permission_mode == "default"

    def test_missing_name_raises(self):
        """缺少 name → AgentParseError。"""
        data = """---
description: No name
---

body
"""
        with pytest.raises(AgentParseError, match="缺少必填字段 'name'"):
            parse_definition(data)

    def test_missing_description_raises(self):
        """缺少 description → AgentParseError。"""
        data = """---
name: no-desc
---

body
"""
        with pytest.raises(AgentParseError, match="缺少必填字段 'description'"):
            parse_definition(data)

    def test_invalid_name_raises(self):
        """非法 name → AgentParseError。"""
        data = """---
name: "invalid name with spaces"
description: bad name
---

body
"""
        with pytest.raises(AgentParseError, match="非法 Agent 名"):
            parse_definition(data)

    def test_unclosed_frontmatter_raises(self):
        """未闭合 frontmatter → AgentParseError。"""
        data = """---
name: test
description: test

No closing ---
"""
        with pytest.raises(AgentParseError, match="未闭合|YAML 非法"):
            parse_definition(data)

    def test_missing_frontmatter_raises(self):
        """缺少 frontmatter → AgentParseError。"""
        data = "Just plain text, no ---"
        with pytest.raises(AgentParseError, match="缺少 YAML frontmatter"):
            parse_definition(data)

    def test_unknown_model_fallback(self, capsys):
        """非法 model 值 stderr 警告并 fallback 到 'inherit'。"""
        data = """---
name: test
description: test
model: gpt-4
---

body
"""
        d = parse_definition(data)
        assert d.model == "inherit"
        captured = capsys.readouterr()
        assert "gpt-4" in captured.err

    def test_unknown_permission_mode_warns(self, capsys):
        """非法 permissionMode stderr 警告。"""
        data = """---
name: test
description: test
permissionMode: weirdMode
---

body
"""
        d = parse_definition(data)
        # 非法值打印警告但不阻断
        captured = capsys.readouterr()
        assert "weirdMode" in captured.err
        assert d.permission_mode == "default"

    def test_body_extraction(self):
        """验证 body 段（去 frontmatter 后）被完整取到 system_prompt。"""
        data = """---
name: test
description: test
---

Line 1
Line 2

Line 4
"""
        d = parse_definition(data)
        assert "Line 1" in d.system_prompt
        assert "Line 4" in d.system_prompt

    def test_is_fork(self):
        """__fork__ 名 → is_fork() True。"""
        d = Definition(name="__fork__", description="fork")
        assert d.is_fork() is True

        d2 = Definition(name="Explore", description="explore")
        assert d2.is_fork() is False


class TestParseFile:
    """parse_file 测试。"""

    def test_reads_md_file(self, tmp_path):
        """从临时 .md 文件解析 Definition。"""
        p = tmp_path / "test.md"
        p.write_text("""---
name: file-agent
description: From file
---

File body
""", encoding="utf-8")

        d = parse_file(p, Source.USER)
        assert d.name == "file-agent"
        assert d.description == "From file"
        assert d.system_prompt == "File body"
        assert d.source == Source.USER
        assert d.file_path is not None

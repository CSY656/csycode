"""权限引擎集成测试（T5/T6/T7）。"""

import os
import json
import tempfile
from pathlib import Path

import pytest
import yaml

from csycode.llm import ToolCall
from csycode.permission import (
    Category,
    Decision,
    Mode,
    Outcome,
    friendly_name,
    categorize,
    extract_target,
    new_engine,
    parse_mode,
)
from csycode.permission.engine import mode_fallback
from csycode.permission.settings import (
    SettingsError,
    load_settings,
    to_rule_set,
)


# ── Mode / Parse ──────────────────────────────────────────────────────

class TestMode:
    """模式枚举测试。"""

    def test_four_modes(self):
        """四档模式存在且唯一。"""
        assert len(list(Mode)) == 4
        assert Mode.DEFAULT == 0
        assert Mode.ACCEPT_EDITS == 1
        assert Mode.PLAN == 2
        assert Mode.BYPASS == 3

    def test_mode_str(self):
        """Mode.__str__ 返回正确的字符串。"""
        assert str(Mode.DEFAULT) == "default"
        assert str(Mode.ACCEPT_EDITS) == "acceptEdits"
        assert str(Mode.PLAN) == "plan"
        assert str(Mode.BYPASS) == "bypassPermissions"


class TestParseMode:
    """模式名解析测试。"""

    @pytest.mark.parametrize("name, expected", [
        ("default", Mode.DEFAULT),
        ("DEFAULT", Mode.DEFAULT),
        ("Default", Mode.DEFAULT),
        ("acceptEdits", Mode.ACCEPT_EDITS),
        ("plan", Mode.PLAN),
        ("PLAN", Mode.PLAN),
        ("bypassPermissions", Mode.BYPASS),
        ("bypass", Mode.BYPASS),
    ])
    def test_valid_modes(self, name, expected):
        """有效模式名返回正确 Mode 和 True。"""
        m, ok = parse_mode(name)
        assert m == expected
        assert ok is True

    def test_unknown_mode_returns_default(self):
        """未知模式返回 DEFAULT 和 False。"""
        m, ok = parse_mode("xyz")
        assert m == Mode.DEFAULT
        assert ok is False


# ── Friendly Name / Categorize / Extract Target ────────────────────────

class TestFriendlyName:
    """友好名映射测试。"""

    @pytest.mark.parametrize("internal, expected", [
        ("bash", "Bash"),
        ("read_file", "Read"),
        ("write_file", "Write"),
        ("edit_file", "Edit"),
        ("glob", "Glob"),
        ("grep", "Grep"),
    ])
    def test_known_tools(self, internal, expected):
        """已知工具正确映射。"""
        assert friendly_name(internal) == expected

    def test_unknown_tool_passthrough(self):
        """未知工具原样返回。"""
        assert friendly_name("unknown_tool") == "unknown_tool"


class TestCategorize:
    """类别判定测试。"""

    def test_read_only_always_read(self):
        """read_only=True 优先，无论工具名。"""
        assert categorize("bash", True) == Category.READ
        assert categorize("write_file", True) == Category.READ
        assert categorize("unknown", True) == Category.READ

    def test_write_tools(self):
        """write_file / edit_file → WRITE。"""
        assert categorize("write_file", False) == Category.WRITE
        assert categorize("edit_file", False) == Category.WRITE

    def test_others_exec(self):
        """其余（含 bash 和未知工具）→ EXEC。"""
        assert categorize("bash", False) == Category.EXEC
        assert categorize("unknown_tool", False) == Category.EXEC


class TestExtractTarget:
    """目标提取测试。"""

    def test_read_file_extracts_file_path(self):
        """read_file 取 file_path。"""
        tc = ToolCall(id="1", name="read_file", arguments={"file_path": "README.md"})
        target, is_file, ok = extract_target(tc)
        assert target == "README.md"
        assert is_file is True
        assert ok is True

    def test_write_file_extracts_file_path(self):
        """write_file 取 file_path。"""
        tc = ToolCall(id="2", name="write_file",
                      arguments={"file_path": "test.txt", "content": "hi"})
        target, is_file, ok = extract_target(tc)
        assert target == "test.txt"
        assert is_file is True
        assert ok is True

    def test_glob_extracts_path_defaults_to_dot(self):
        """glob 取 path，空则默认 '.'。"""
        tc = ToolCall(id="3", name="glob", arguments={"pattern": "*.py"})
        target, is_file, ok = extract_target(tc)
        assert target == "."
        assert is_file is True
        assert ok is True

    def test_grep_extracts_path(self):
        """grep 取 path。"""
        tc = ToolCall(id="4", name="grep",
                      arguments={"pattern": "foo", "path": "src/"})
        target, is_file, ok = extract_target(tc)
        assert target == "src/"
        assert is_file is True
        assert ok is True

    def test_bash_extracts_command(self):
        """bash 取 command。"""
        tc = ToolCall(id="5", name="bash", arguments={"command": "git status"})
        target, is_file, ok = extract_target(tc)
        assert target == "git status"
        assert is_file is False
        assert ok is True

    def test_bash_empty_command_not_ok(self):
        """bash 缺 command → ok=False。"""
        tc = ToolCall(id="6", name="bash", arguments={})
        _, _, ok = extract_target(tc)
        assert ok is False

    def test_file_tool_missing_path_not_ok(self):
        """文件工具缺 file_path → ok=False。"""
        tc = ToolCall(id="7", name="read_file", arguments={})
        _, _, ok = extract_target(tc)
        assert ok is False

    def test_unknown_tool(self):
        """未知工具返回空。"""
        tc = ToolCall(id="8", name="magic_tool", arguments={"x": 1})
        target, is_file, ok = extract_target(tc)
        assert target == ""
        assert is_file is False
        assert ok is False

    def test_input_as_json_string(self):
        """ToolCall.arguments 为 JSON 字符串时正确解析。"""
        tc = ToolCall(id="9", name="read_file",
                      arguments=json.dumps({"file_path": "config.yaml"}))
        target, is_file, ok = extract_target(tc)
        assert target == "config.yaml"
        assert ok is True


# ── Mode Fallback Matrix ──────────────────────────────────────────────

class TestModeFallback:
    """模式兜底矩阵测试（F5）。"""

    def test_read_always_allow(self):
        """只读永远放行。"""
        for mode in Mode:
            assert mode_fallback(mode, Category.READ) == Decision.ALLOW

    def test_bypass_allows_all(self):
        """bypass 全放行。"""
        for cat in Category:
            assert mode_fallback(Mode.BYPASS, cat) == Decision.ALLOW

    def test_accept_edits_allows_write(self):
        """acceptEdits 放行写操作。"""
        assert mode_fallback(Mode.ACCEPT_EDITS, Category.WRITE) == Decision.ALLOW

    def test_accept_edits_asks_exec(self):
        """acceptEdits 命令执行仍需确认。"""
        assert mode_fallback(Mode.ACCEPT_EDITS, Category.EXEC) == Decision.ASK

    def test_default_asks_write_and_exec(self):
        """default 模式下写和命令执行需确认。"""
        assert mode_fallback(Mode.DEFAULT, Category.WRITE) == Decision.ASK
        assert mode_fallback(Mode.DEFAULT, Category.EXEC) == Decision.ASK

    def test_plan_asks_write_and_exec(self):
        """plan 模式下写和命令执行也需确认（防御兜底）。"""
        assert mode_fallback(Mode.PLAN, Category.WRITE) == Decision.ASK
        assert mode_fallback(Mode.PLAN, Category.EXEC) == Decision.ASK

    def test_fallback_never_denies(self):
        """模式兜底永不产 Deny。"""
        for mode in Mode:
            for cat in Category:
                assert mode_fallback(mode, cat) != Decision.DENY


# ── Settings Loading ──────────────────────────────────────────────────

class TestSettings:
    """配置加载测试。"""

    def test_load_missing_file(self, tmp_path):
        """缺失文件返回空 Settings 不抛异常。"""
        path = str(tmp_path / "nonexistent.yaml")
        s = load_settings(path)
        assert s.default_mode == ""
        assert s.permissions.allow == []
        assert s.permissions.deny == []

    def test_load_valid_yaml(self, tmp_path):
        """正常加载。"""
        path = str(tmp_path / "settings.yaml")
        Path(path).write_text("""
default_mode: acceptEdits
permissions:
  allow:
    - "Bash(git *)"
    - "Read"
  deny:
    - "Bash(rm *)"
""")
        s = load_settings(path)
        assert s.default_mode == "acceptEdits"
        assert len(s.permissions.allow) == 2
        assert len(s.permissions.deny) == 1

    def test_load_invalid_yaml_raises(self, tmp_path):
        """非法 YAML 抛 SettingsError。"""
        path = str(tmp_path / "bad.yaml")
        Path(path).write_text("{invalid: [yaml: yes")
        with pytest.raises(SettingsError):
            load_settings(path)

    def test_to_rule_set_skips_invalid(self, tmp_path):
        """to_rule_set 跳过非法条目。"""
        path = str(tmp_path / "s.yaml")
        Path(path).write_text("""
permissions:
  allow:
    - "Bash(git *)"
    - "not(valid"
    - "Read"
  deny:
    - "bad(rule"
""")
        s = load_settings(path)
        rs = to_rule_set(s)
        # 只有 2 条合法 allow 规则（"not(valid" 解析失败被跳过）
        assert len(rs.allow) == 2
        # 1 条 deny 规则被跳过
        assert len(rs.deny) == 0


# ── Engine Pipeline ──────────────────────────────────────────────────

class TestEnginePipeline:
    """引擎前四层流水线测试。"""

    @pytest.fixture
    def engine(self, tmp_path):
        """创建引擎实例（基于临时目录）。"""
        e, err = new_engine(str(tmp_path))
        assert err is None
        return e

    def test_read_always_allowed(self, engine):
        """只读工具 ALLOW。"""
        tc = ToolCall(id="1", name="read_file", arguments={"file_path": "test.py"})
        d, r = engine.check(Mode.DEFAULT, tc, True)
        assert d == Decision.ALLOW
        assert r == ""

    def test_write_default_asks(self, engine):
        """default 下写文件 ASK。"""
        tc = ToolCall(id="2", name="write_file",
                      arguments={"file_path": "test.txt", "content": "hi"})
        d, r = engine.check(Mode.DEFAULT, tc, False)
        assert d == Decision.ASK

    def test_write_bypass_allows(self, engine):
        """bypass 下写文件 ALLOW。"""
        tc = ToolCall(id="3", name="write_file",
                      arguments={"file_path": "test.txt", "content": "hi"})
        d, r = engine.check(Mode.BYPASS, tc, False)
        assert d == Decision.ALLOW

    def test_blacklist_blocks_even_bypass(self, engine):
        """黑名单在 bypass 下仍然生效。"""
        tc = ToolCall(id="4", name="bash",
                      arguments={"command": "rm -rf / --no-preserve-root"})
        d, r = engine.check(Mode.BYPASS, tc, False)
        assert d == Decision.DENY
        assert "黑名单" in r

    def test_sandbox_blocks_outside_path(self, engine):
        """沙箱拦截项目外路径。"""
        tc = ToolCall(id="5", name="read_file",
                      arguments={"file_path": "/etc/passwd"})
        d, r = engine.check(Mode.DEFAULT, tc, True)
        assert d == Decision.DENY
        assert "项目目录之外" in r

    def test_sandbox_blocks_parse_failure(self, engine):
        """不可解析的文件路径 DENY。"""
        tc = ToolCall(id="6", name="write_file",
                      arguments={"content": "hi"})  # 缺 file_path
        d, r = engine.check(Mode.DEFAULT, tc, False)
        assert d == Decision.DENY
        assert "无法解析" in r

    def test_blacklist_bypasses_non_exec(self, engine):
        """非 EXEC 工具不被黑名单误拦。"""
        # read_file 即使路径含 "rm"，也不应命中黑名单
        tc = ToolCall(id="7", name="read_file",
                      arguments={"file_path": "rm_-rf_test.txt"})
        d, r = engine.check(Mode.DEFAULT, tc, True)
        assert d == Decision.ALLOW  # 只读，直接放行

    def test_bash_not_checked_by_sandbox(self, engine):
        """bash 不被沙箱误拦（跳到后续层）。安全命令走白名单直接放行。"""
        tc = ToolCall(id="8", name="bash",
                      arguments={"command": "ls /etc"})
        d, r = engine.check(Mode.DEFAULT, tc, False)
        # "ls /etc" 是安全命令白名单成员，应直接 ALLOW
        assert d == Decision.ALLOW

    def test_rule_deny_overrides_mode(self, engine, tmp_path):
        """deny 规则命中直接 DENY，不进模式矩阵。"""
        from csycode.permission.matcher import compile_matcher
        engine.local.deny.append(
            __import__("csycode.permission.rule", fromlist=["Rule"]).Rule(
                tool="Read", matcher=compile_matcher("secret.txt", is_command=False),
                raw="secret.txt", allow=False
            )
        )
        tc = ToolCall(id="9", name="read_file",
                      arguments={"file_path": "secret.txt"})
        d, r = engine.check(Mode.DEFAULT, tc, True)
        assert d == Decision.DENY
        assert "deny 规则" in r

    def test_rule_allow_bypasses_mode(self, engine):
        """allow 规则命中直接 ALLOW，不进模式矩阵。"""
        from csycode.permission.matcher import compile_matcher
        engine.local.allow.append(
            __import__("csycode.permission.rule", fromlist=["Rule"]).Rule(
                tool="Bash", matcher=compile_matcher("git *", is_command=True),
                raw="git *", allow=True
            )
        )
        tc = ToolCall(id="10", name="bash",
                      arguments={"command": "git status"})
        d, r = engine.check(Mode.DEFAULT, tc, False)
        assert d == Decision.ALLOW  # allow 规则直接放行

    def test_local_overrides_project(self, engine):
        """本地规则优先于项目规则。"""
        from csycode.permission.matcher import compile_matcher
        # 项目层 deny，本地层 allow
        engine.project.deny.append(
            __import__("csycode.permission.rule", fromlist=["Rule"]).Rule(
                tool="Bash", matcher=compile_matcher("npm *", is_command=True),
                raw="npm *", allow=False
            )
        )
        engine.local.allow.append(
            __import__("csycode.permission.rule", fromlist=["Rule"]).Rule(
                tool="Bash", matcher=compile_matcher("npm *", is_command=True),
                raw="npm *", allow=True
            )
        )
        tc = ToolCall(id="11", name="bash",
                      arguments={"command": "npm install"})
        d, r = engine.check(Mode.DEFAULT, tc, False)
        # local 优先 → ALLOW
        assert d == Decision.ALLOW


# ── Engine Construction ──────────────────────────────────────────────

class TestNewEngine:
    """引擎构造测试。"""

    def test_resolve_root_failure_returns_engine(self):
        """resolve_root 失败仍返回非 None 引擎。"""
        e, err = new_engine(r"Z:\nonexistent\path")
        assert e is not None
        assert err is not None

    def test_config_file_loads(self, tmp_path):
        """项目内 settings.yaml 被加载并影响 start_mode。"""
        csycode_dir = tmp_path / ".csycode"
        csycode_dir.mkdir()
        (csycode_dir / "settings.yaml").write_text("default_mode: acceptEdits")
        e, err = new_engine(str(tmp_path))
        assert err is None
        assert e.start_mode == Mode.ACCEPT_EDITS

    def test_local_overrides_project_start_mode(self, tmp_path):
        """local 层的 default_mode 优先于 project 层。"""
        csycode_dir = tmp_path / ".csycode"
        csycode_dir.mkdir()
        (csycode_dir / "settings.yaml").write_text("default_mode: acceptEdits")
        (csycode_dir / "settings.local.yaml").write_text("default_mode: plan")
        e, err = new_engine(str(tmp_path))
        assert err is None
        assert e.start_mode == Mode.PLAN

    def test_bad_yaml_degrades_gracefully(self, tmp_path):
        """非法 YAML 不导致引擎构造失败。"""
        csycode_dir = tmp_path / ".csycode"
        csycode_dir.mkdir()
        (csycode_dir / "settings.yaml").write_text("{bad yaml !!!")
        # 不应抛异常
        e, err = new_engine(str(tmp_path))
        assert e is not None
        # start_mode 应为默认
        assert e.start_mode == Mode.DEFAULT


# ── Persist ──────────────────────────────────────────────────────────

class TestPersist:
    """永久规则写入测试。"""

    def test_persist_writes_file(self, tmp_path):
        """persist_local_allow 写入本地层文件。"""
        csycode_dir = tmp_path / ".csycode"
        csycode_dir.mkdir()
        e, err = new_engine(str(tmp_path))
        assert err is None

        tc = ToolCall(id="12", name="write_file",
                      arguments={"file_path": "hello.txt", "content": "world"})
        e.persist_local_allow(tc)

        # 文件应存在
        local_path = str(csycode_dir / "settings.local.yaml")
        assert os.path.exists(local_path)

        # 文件应包含 allow 条目
        content = Path(local_path).read_text()
        assert "Write(hello.txt)" in content or "hello.txt" in content

        # 重新加载后同调用应 ALLOW
        e2, _ = new_engine(str(tmp_path))
        d, _ = e2.check(Mode.DEFAULT, tc, False)
        assert d == Decision.ALLOW

    def test_persist_dedup(self, tmp_path):
        """重复 persist 不产生重复条目。"""
        csycode_dir = tmp_path / ".csycode"
        csycode_dir.mkdir()
        e, err = new_engine(str(tmp_path))
        assert err is None

        tc = ToolCall(id="13", name="bash",
                      arguments={"command": "git status"})
        e.persist_local_allow(tc)
        e.persist_local_allow(tc)  # 重复

        content = Path(csycode_dir / "settings.local.yaml").read_text()
        # git status 只出现一次
        assert content.count("git status") == 1


# ── Session Allow ──────────────────────────────────────────────────────

class TestSessionAllow:
    """会话级缓存测试 —— 修复「相同命令重复触发 HITL」的 bug。"""

    @pytest.fixture
    def engine(self, tmp_path):
        """创建引擎实例（基于临时目录）。"""
        e, err = new_engine(str(tmp_path))
        assert err is None
        return e

    def test_session_allow_tc_same_command_no_reask(self, engine):
        """核心 bug 场景：ALLOW_ONCE 后，相同命令不再触发 HITL。

        修复前：_session_allowed 永远为空，同一命令每次都 ASK。
        修复后：session_allow_tc 将 key 加入 _session_allowed，第二次 check 返回 ALLOW。

        注意：git status 在安全命令白名单中会直接 ALLOW，所以用 pytest
        （不在白名单中的命令）来测试。
        """
        tc = ToolCall(id="s1", name="bash",
                      arguments={"command": "pytest"})

        # 第一次：DEFAULT 模式下非安全命令 → ASK（需 HITL）
        d1, _ = engine.check(Mode.DEFAULT, tc, False)
        assert d1 == Decision.ASK

        # 用户选 ALLOW_ONCE → 调用 session_allow_tc
        engine.session_allow_tc(tc)

        # 第二次：同一命令 → ALLOW（不再弹窗）
        d2, _ = engine.check(Mode.DEFAULT, tc, False)
        assert d2 == Decision.ALLOW

    def test_session_allow_tc_different_command_still_asks(self, engine):
        """不同命令不受会话缓存影响，仍需 HITL。"""
        tc1 = ToolCall(id="s2", name="bash",
                       arguments={"command": "pytest"})
        tc2 = ToolCall(id="s3", name="bash",
                       arguments={"command": "npm test"})

        # 缓存 tc1
        engine.session_allow_tc(tc1)

        # tc1 → ALLOW
        d1, _ = engine.check(Mode.DEFAULT, tc1, False)
        assert d1 == Decision.ALLOW

        # tc2 → 仍是 ASK（不同命令）
        d2, _ = engine.check(Mode.DEFAULT, tc2, False)
        assert d2 == Decision.ASK

    def test_session_allow_tc_write_file_cached(self, engine):
        """文件写入操作也支持会话级缓存。"""
        tc = ToolCall(id="s4", name="write_file",
                      arguments={"file_path": "test.txt", "content": "hello"})

        # 第一次：DEFAULT + WRITE → ASK
        d1, _ = engine.check(Mode.DEFAULT, tc, False)
        assert d1 == Decision.ASK

        # 缓存后 → ALLOW
        engine.session_allow_tc(tc)
        d2, _ = engine.check(Mode.DEFAULT, tc, False)
        assert d2 == Decision.ALLOW

    def test_session_allow_empty_target_not_cached(self, engine):
        """空 target 不会被缓存（bash 缺 command 的场景）。"""
        tc = ToolCall(id="s5", name="bash", arguments={})
        # extract_target → ok=False, target=""
        engine.session_allow_tc(tc)

        # 空 target 不应加入缓存
        assert engine._session_allowed is None or len(engine._session_allowed) == 0

    def test_session_allow_persist_dual_write(self, engine):
        """ALLOW_FOREVER 的双写策略：session_allow + persist_local_allow 同时生效。"""
        tc = ToolCall(id="s6", name="bash",
                      arguments={"command": "npm run build"})

        # 第一次 → ASK
        d1, _ = engine.check(Mode.DEFAULT, tc, False)
        assert d1 == Decision.ASK

        # 双写：session_allow_tc（立即生效）+ persist_local_allow（跨会话）
        engine.session_allow_tc(tc)
        engine.persist_local_allow(tc)

        # 第二次 → ALLOW（session_allow 命中）
        d2, _ = engine.check(Mode.DEFAULT, tc, False)
        assert d2 == Decision.ALLOW

        # 重新构造引擎（模拟跨会话）→ session 缓存清空，但 persist 规则仍在
        e2, _ = new_engine(engine.root)
        d3, _ = e2.check(Mode.DEFAULT, tc, False)
        assert d3 == Decision.ALLOW  # persist 规则命中

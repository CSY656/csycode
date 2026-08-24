"""T2: permission.Matcher 单元测试 —— 四种类型 × 边界条件。"""

import re

import pytest

from csycode.permission.matcher import (
    ExactMatcher,
    GlobMatcher,
    Matcher,
    NotMatcher,
    RegexMatcher,
    compile_matcher,
    _match_command_glob,
    _match_path_glob,
)


# ── ExactMatcher ────────────────────────────────────────────────────────


class TestExactMatcher:
    def test_match_exact(self):
        m = ExactMatcher("git status")
        assert m.match("git status") is True
        assert m.match("git status -s") is False
        assert m.match("GIT STATUS") is False
        assert m.match("") is False

    def test_str(self):
        m = ExactMatcher("hello")
        assert str(m) == "=hello"


# ── GlobMatcher (command mode) ──────────────────────────────────────────


class TestGlobMatcherCommand:
    def test_match_star(self):
        m = GlobMatcher("git *", is_command=True)
        assert m.match("git status") is True
        assert m.match("git log --oneline") is True
        assert m.match("npm install") is False
        assert m.match("git") is False

    def test_match_empty_pattern(self):
        m = GlobMatcher("", is_command=True)
        assert m.match("anything") is True
        assert m.match("") is True

    def test_match_double_star(self):
        m = GlobMatcher("git **", is_command=True)
        assert m.match("git status") is True
        assert m.match("git log --oneline") is True

    def test_str(self):
        m = GlobMatcher("git *", is_command=True)
        assert str(m) == "git *"


# ── GlobMatcher (path mode) ─────────────────────────────────────────────


class TestGlobMatcherPath:
    def test_match_single_star(self):
        m = GlobMatcher("*.py", is_command=False)
        assert m.match("main.py") is True
        assert m.match("test.py") is True
        assert m.match("main.js") is False
        assert m.match("src/main.py") is False  # * 不跨 /

    def test_match_double_star(self):
        m = GlobMatcher("src/**/*.py", is_command=False)
        assert m.match("src/main.py") is True
        assert m.match("src/sub/main.py") is True
        assert m.match("src/a/b/c/main.py") is True
        assert m.match("tests/main.py") is False

    def test_match_double_star_middle(self):
        """中段 ** 匹配零或多级目录。"""
        m = GlobMatcher("src/**/main.py", is_command=False)
        assert m.match("src/main.py") is True
        assert m.match("src/sub/main.py") is True

    def test_match_empty_pattern(self):
        m = GlobMatcher("", is_command=False)
        assert m.match("anything") is True

    def test_match_path_normalization(self):
        """Windows 反斜杠自动转换为正斜杠。"""
        m = GlobMatcher("src/*.py", is_command=False)
        assert m.match("src\\main.py") is True

    def test_str(self):
        m = GlobMatcher("**/*.py", is_command=False)
        assert str(m) == "**/*.py"


# ── RegexMatcher ────────────────────────────────────────────────────────


class TestRegexMatcher:
    def test_match_regex(self):
        m = RegexMatcher(r"^npm (install|test)$", re.compile(r"^npm (install|test)$"))
        assert m.match("npm install") is True
        assert m.match("npm test") is True
        assert m.match("npm run dev") is False

    def test_match_search_mode(self):
        """regex matcher 用 search 而非 fullmatch。"""
        m = RegexMatcher(r"delete", re.compile(r"delete", re.IGNORECASE))
        assert m.match("please DELETE that file") is True
        assert m.match("create the file") is False

    def test_str(self):
        m = RegexMatcher(r"^rm", re.compile(r"^rm"))
        assert str(m) == "~^rm"


# ── NotMatcher ──────────────────────────────────────────────────────────


class TestNotMatcher:
    def test_not_exact(self):
        """!=value 不命中 value、命中其它。"""
        m = NotMatcher(ExactMatcher("foo"))
        assert m.match("foo") is False
        assert m.match("bar") is True

    def test_not_regex(self):
        """!~^rm 命中不以 rm 起头的。"""
        inner = RegexMatcher(r"^rm", re.compile(r"^rm"))
        m = NotMatcher(inner)
        assert m.match("ls -lh") is True  # 不以 rm 起头 → 命中
        assert m.match("rm -rf .") is False  # 以 rm 起头 → 不命中
        assert m.match("rmdir foo") is False

    def test_not_glob(self):
        """!git * 命中不以 git 起头的命令。"""
        inner = GlobMatcher("git *", is_command=True)
        m = NotMatcher(inner)
        assert m.match("npm install") is True
        assert m.match("git status") is False

    def test_nested_not(self):
        """!!=foo 等价 =foo（双重否定）。"""
        m = NotMatcher(NotMatcher(ExactMatcher("foo")))
        assert m.match("foo") is True
        assert m.match("bar") is False

    def test_str(self):
        m = NotMatcher(ExactMatcher("foo"))
        assert str(m) == "!=foo"


# ── compile_matcher 工厂函数 ────────────────────────────────────────────


class TestCompileMatcher:
    @pytest.mark.parametrize(
        "pattern, is_command, expected_type, expected_str",
        [
            # 精确
            ("=git status", False, ExactMatcher, "=git status"),
            # 正则
            ("~^rm", False, RegexMatcher, "~^rm"),
            # 反向精确
            ("!=foo", False, NotMatcher, "!=foo"),
            # 反向正则
            ("!~^rm", False, NotMatcher, "!~^rm"),
            # 反向 glob
            ("!git *", True, NotMatcher, "!git *"),
            # 缺省 glob (command)
            ("git *", True, GlobMatcher, "git *"),
            # 缺省 glob (path)
            ("**/*.py", False, GlobMatcher, "**/*.py"),
        ],
        ids=[
            "exact",
            "regex",
            "not-exact",
            "not-regex",
            "not-glob",
            "glob-command",
            "glob-path",
        ],
    )
    def test_compile(self, pattern, is_command, expected_type, expected_str):
        m = compile_matcher(pattern, is_command=is_command)
        assert isinstance(m, expected_type)
        assert str(m) == expected_str

    def test_compile_empty_raises(self):
        with pytest.raises(ValueError, match="empty matcher pattern"):
            compile_matcher("", is_command=False)

    def test_compile_invalid_regex_raises(self):
        with pytest.raises(ValueError, match="invalid regex"):
            compile_matcher("~[invalid", is_command=False)

    def test_compile_empty_regex_raises(self):
        with pytest.raises(ValueError, match="empty regex pattern"):
            compile_matcher("~", is_command=False)

    def test_compile_empty_not_raises(self):
        with pytest.raises(ValueError, match="empty not pattern"):
            compile_matcher("!", is_command=False)

    def test_backward_compat_glob(self):
        """无前缀的 glob 语法向后兼容现有权限规则。"""
        m = compile_matcher("git *", is_command=True)
        assert isinstance(m, GlobMatcher)
        assert m.match("git status") is True


# ── compile_matcher 工厂函数边界 ──────────────────────────────────────


class TestCompileMatcherEdgeCases:
    def test_equals_in_middle_not_prefix(self):
        """= 仅在开头是前缀，中间的 = 是字面。"""
        m = compile_matcher("a=b", is_command=False)
        assert isinstance(m, GlobMatcher)
        assert m.match("a=b") is True

    def test_tilde_in_middle_not_prefix(self):
        """~ 仅在开头是前缀。"""
        m = compile_matcher("a~b", is_command=False)
        assert isinstance(m, GlobMatcher)
        assert m.match("a~b") is True

    def test_bang_in_middle_not_prefix(self):
        """! 仅在开头是前缀。"""
        m = compile_matcher("a!b", is_command=False)
        assert isinstance(m, GlobMatcher)
        assert m.match("a!b") is True

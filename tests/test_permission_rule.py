"""权限规则与匹配单元测试（T4）。

ch12: 适配新的 Rule 结构（matcher + raw 替代 pattern）。
"""

import pytest

from csycode.permission import Decision
from csycode.permission.matcher import GlobMatcher, compile_matcher
from csycode.permission.rule import (
    Rule,
    RuleSet,
    match_pattern,
    match_rule,
    parse_rule,
)


# ── 辅助函数：快速构造 glob 类型的 Rule ──────────────────────────────


def _make_rule(tool: str, pattern: str = "", *, allow: bool = True) -> Rule:
    """用 glob 模式快速构造 Rule（向后兼容旧测试）。"""
    if not pattern:
        return Rule(tool=tool, matcher=None, raw="", allow=allow)
    is_command = tool == "Bash"
    matcher = compile_matcher(pattern, is_command=is_command)
    return Rule(tool=tool, matcher=matcher, raw=pattern, allow=allow)


class TestParseRule:
    """规则解析测试。"""

    @pytest.mark.parametrize("raw, expected_tool", [
        ("Bash(git *)", "Bash"),
        ("Bash(git status)", "Bash"),
        ("Read", "Read"),
        ("Write(src/**)", "Write"),
        ("Edit", "Edit"),
        ("Glob", "Glob"),
        ("Grep", "Grep"),
        ("Bash()", "Bash"),  # 空括号 = 匹配空命令
    ])
    def test_valid_rules(self, raw, expected_tool):
        """有效规则解析成功。"""
        rule, err = parse_rule(raw)
        assert err is None, f"unexpected error: {err}"
        assert rule is not None
        assert rule.tool == expected_tool
        # 无括号规则 matcher=None（全匹配）
        if "(" not in raw:
            assert rule.matcher is None
        # 空括号规则 matcher=None（全匹配）
        if raw.endswith("()"):
            assert rule.matcher is None

    def test_valid_new_syntax(self):
        """ch12 新语法：精确 / 正则 / 反向。"""
        # 精确
        rule, err = parse_rule("Bash(=git status)")
        assert err is None
        assert rule is not None
        assert rule.matcher is not None
        assert rule.matcher.match("git status") is True
        assert rule.matcher.match("git status -s") is False

        # 正则
        rule, err = parse_rule("Bash(~^npm (install|test)$)")
        assert err is None
        assert rule is not None
        assert rule.matcher.match("npm install") is True
        assert rule.matcher.match("npm run dev") is False

        # 反向
        rule, err = parse_rule("Bash(!~^rm)")
        assert err is None
        assert rule is not None
        assert rule.matcher.match("ls -lh") is True
        assert rule.matcher.match("rm -rf .") is False

    @pytest.mark.parametrize("raw", [
        "",
        "   ",
        "Bash(git status",  # 括号不配对
        "(git status)",     # 无工具名
    ])
    def test_invalid_rules(self, raw):
        """无效规则返回 error string。"""
        _, err = parse_rule(raw)
        assert err is not None

    def test_regex_compile_error(self):
        """正则编译失败返回 error。"""
        _, err = parse_rule("Bash(~[invalid)")
        assert err is not None
        assert "invalid regex" in err.lower() or "parse failed" in err.lower()


class TestMatchPatternCommand:
    """命令 glob 匹配测试（向后兼容的 match_pattern）。"""

    def test_full_match(self):
        """无通配时精确匹配。"""
        assert match_pattern("git status", "git status", is_command=True)

    def test_star_matches_any(self):
        """* 匹配任意字符（含空格）。"""
        assert match_pattern("git *", "git status", is_command=True)
        assert match_pattern("git *", "git push origin main", is_command=True)

    def test_star_not_match_different_prefix(self):
        """* 不跨前缀匹配。"""
        assert not match_pattern("git *", "npm install", is_command=True)

    def test_empty_pattern_matches_all(self):
        """空模式恒匹配。"""
        assert match_pattern("", "anything", is_command=True)
        assert match_pattern("", "/some/path", is_command=False)


class TestMatchPatternPath:
    """路径 glob 匹配测试。"""

    def test_exact_path(self):
        """精确路径匹配。"""
        assert match_pattern("src/main.py", "src/main.py", is_command=False)

    def test_star_in_segment(self):
        """* 匹配段内字符。"""
        assert match_pattern("src/*.py", "src/main.py", is_command=False)
        assert match_pattern("src/*.py", "src/utils.py", is_command=False)
        assert not match_pattern("src/*.py", "src/sub/main.py", is_command=False)

    def test_double_star_cross_segment(self):
        """** 跨段匹配。"""
        assert match_pattern("src/**", "src/a.py", is_command=False)
        assert match_pattern("src/**", "src/a/b.py", is_command=False)
        assert match_pattern("src/**", "src/a/b/c/d.py", is_command=False)
        assert not match_pattern("src/**", "docs/x.py", is_command=False)

    def test_mixed_star_pattern(self):
        """混合 * 和 **。"""
        assert match_pattern("src/**/*.py", "src/main.py", is_command=False)
        assert match_pattern("src/**/*.py", "src/sub/main.py", is_command=False)
        assert not match_pattern("src/**/*.py", "src/main.txt", is_command=False)

    def test_normalized_path(self):
        r"""路径中的 \ 被规范化为 /。"""
        assert match_pattern("src/main.py", "src\\main.py", is_command=False)


class TestMatchRule:
    """match_rule 函数测试（新 Matcher 系统）。"""

    def test_none_matcher_matches_all(self):
        """matcher=None 表示全匹配。"""
        r = Rule(tool="Bash", matcher=None, raw="", allow=True)
        assert match_rule(r, "anything") is True

    def test_glob_matcher(self):
        r = _make_rule("Bash", "git *")
        assert match_rule(r, "git status") is True
        assert match_rule(r, "npm install") is False

    def test_exact_matcher(self):
        r = _make_rule("Bash", "=git status")
        assert match_rule(r, "git status") is True
        assert match_rule(r, "git status -s") is False


class TestRuleSet:
    """规则集匹配测试。"""

    def test_deny_priority_over_allow(self):
        """同层 deny 命中优先于 allow。"""
        rs = RuleSet(
            allow=[_make_rule("Bash", "git *")],
            deny=[_make_rule("Bash", "git push", allow=False)],
        )
        decision, hit = rs.match("Bash", "git push")
        assert decision == Decision.DENY
        assert hit is True

    def test_allow_hit(self):
        """allow 命中返回 ALLOW。"""
        rs = RuleSet(
            allow=[_make_rule("Bash", "git *")],
        )
        decision, hit = rs.match("Bash", "git status")
        assert decision == Decision.ALLOW
        assert hit is True

    def test_no_match(self):
        """不命中返回 (ALLOW, False)。"""
        rs = RuleSet(
            allow=[_make_rule("Bash", "git *")],
        )
        decision, hit = rs.match("Write", "test.txt")
        assert decision == Decision.ALLOW
        assert hit is False

    def test_empty_ruleset(self):
        """空规则集不命中。"""
        rs = RuleSet()
        decision, hit = rs.match("Bash", "anything")
        assert decision == Decision.ALLOW
        assert hit is False

    def test_new_syntax_exact_in_ruleset(self):
        """新精确语法在 RuleSet 中工作。"""
        rs = RuleSet(
            allow=[_make_rule("Bash", "=git status")],
        )
        decision, hit = rs.match("Bash", "git status")
        assert decision == Decision.ALLOW
        assert hit is True

        # 不命中
        decision, hit = rs.match("Bash", "git status -s")
        assert hit is False

"""权限规则定义 —— Rule/RuleSet 数据类、规则解析与匹配。

ch12: 将匹配语法从单一通配扩展为四种类型（exact / glob / regex / not）。
规则格式: Tool(prefix+pattern) 或 Tool，其中:
  - Tool: 友好名（Bash / Read / Write / Edit / Glob / Grep）
  - prefix: 可选类型前缀（= 精确 / ~ 正则 / ! 反向 / 无=glob）
  - 无 pattern 的规则（如 "Read"）匹配该工具的全部调用

向后兼容:
  - 旧写法 "Bash(git *)" 无前缀 → glob 类型，行为不变
  - 新写法 "Bash(=git status)" 精确、"Bash(~^npm.*)" 正则、"Bash(!~^rm)" 反向正则
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .matcher import Matcher, compile_matcher

if TYPE_CHECKING:
    from . import Decision


@dataclass
class Rule:
    """单条权限规则。

    Attributes:
        tool: 工具友好名（Bash/Read/Write/Edit/Glob/Grep）。
        matcher: 匹配器实例（None 表示匹配该工具全部调用）。
        raw: 原始模式描述串（供错误日志与调试）。
        allow: True 表示 allow 规则，False 表示 deny 规则。
    """

    tool: str
    matcher: Matcher | None
    raw: str
    allow: bool


@dataclass
class RuleSet:
    """一组规则（allow + deny 两列）。

    match() 方法按「先 deny 后 allow」顺序判定：
    先遍历 deny 规则，命中则返回 DENY；
    再遍历 allow 规则，命中则返回 ALLOW；
    均不命中返回 (ALLOW, False)（第二个 False 表示未命中）。
    """

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match(self, friendly: str, target: str) -> "tuple[Decision, bool]":
        """规则匹配判定。

        Args:
            friendly: 工具友好名（Bash / Read / ...）。
            target: 目标字符串（命令串或文件路径）。

        Returns:
            (Decision, 是否命中): deny 优先，allow 次之，均不命中返回 (ALLOW, False)。
        """
        from . import Decision

        # 先 deny（deny 优先更安全）
        for rule in self.deny:
            if rule.tool == friendly and match_rule(rule, target):
                return (Decision.DENY, True)

        # 再 allow
        for rule in self.allow:
            if rule.tool == friendly and match_rule(rule, target):
                return (Decision.ALLOW, True)

        return (Decision.ALLOW, False)


# ── 规则匹配 ────────────────────────────────────────────────────────────


def match_rule(rule: Rule, target: str) -> bool:
    """判定一条规则是否匹配目标字符串。

    matcher 为 None 表示"该工具全匹配"。
    否则委托给 matcher.match(target)。
    """
    if rule.matcher is None:
        return True
    return rule.matcher.match(target)


# ── 规则解析 ────────────────────────────────────────────────────────────


def parse_rule(s: str) -> "tuple[Rule | None, str | None]":
    """从字符串解析一条规则。

    格式：Tool 或 Tool(prefix+pattern)。
    前缀规则:
      =value → 精确匹配
      ~regex → 正则匹配
      !inner → 反向匹配（inner 自身按规则解析）
      无前缀 → glob 匹配（向后兼容）

    Bash 工具的模式 is_command=True（整串通配），
    其余工具的模式 is_command=False（路径按段匹配）。

    Args:
        s: 规则字符串，如 "Bash(git *)"、"Bash(=git status)"、
           "Write(~.*\\.py)"、"Bash(!~^rm)"。

    Returns:
        (Rule, None) 如果解析成功，(None, error_str) 否则。
    """
    s = s.strip()
    if not s:
        return (None, "empty rule string")

    # 检查括号配对
    open_paren = s.find("(")
    if open_paren == -1:
        # 无括号：整个字符串就是工具名，全匹配
        return (Rule(tool=s, matcher=None, raw=s, allow=True), None)

    # 最后字符必须是 ")"
    if not s.endswith(")"):
        return (None, f"rule {s!r}: missing closing parenthesis")

    tool = s[:open_paren].strip()
    pattern = s[open_paren + 1 : -1]

    if not tool:
        return (None, f"rule {s!r}: empty tool name")

    # 空 pattern → 全匹配
    if not pattern:
        return (Rule(tool=tool, matcher=None, raw=s, allow=True), None)

    # 编译 matcher
    is_command = tool == "Bash"
    try:
        matcher = compile_matcher(pattern, is_command=is_command)
    except ValueError as e:
        return (None, f"rule {s!r} parse failed: {e}")

    return (Rule(tool=tool, matcher=matcher, raw=pattern, allow=True), None)


# ── 向后兼容的 match_pattern 函数 ────────────────────────────────────────
# 保留原有函数签名供 engine.py 的 _find_matching_rule 和 persist.py 使用。
# 内部委托给 GlobMatcher（等价原有行为）。


def match_pattern(pattern: str, target: str, *, is_command: bool = False) -> bool:
    """将规则模式与目标字符串做 glob 匹配（向后兼容）。

    ch12: 此函数保留供旧代码使用，新代码应使用 Matcher 系统。

    Args:
        pattern: 规则模式（"" 匹配任意目标）。
        target: 目标字符串。
        is_command: True 按命令 glob 匹配（* 跨空格），
                    False 按文件路径按段匹配。

    Returns:
        True 如果匹配成功。
    """
    if pattern == "":
        return True
    m = GlobMatcher(pattern, is_command=is_command)
    return m.match(target)


# 从 matcher 模块导入 GlobMatcher（避免循环引用）
from .matcher import GlobMatcher  # noqa: E402

"""权限匹配器模块 —— Matcher Protocol 与四种匹配类型实现。

提供:
  - Matcher Protocol: 统一匹配接口 (match + __str__)
  - ExactMatcher: 精确字符串匹配(=value)
  - GlobMatcher: glob 通配匹配 (value / * / **)
  - RegexMatcher: 正则匹配 (~regex)
  - NotMatcher: 反向匹配 (!inner)
  - compile_matcher: 工厂函数，根据前缀解析匹配类型

ch12: 将 ch08 的单一通配匹配扩展为四种匹配类型，供 Hook 条件
表达式与扩展后的权限规则共用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


class Matcher(Protocol):
    """规则匹配的统一接口。

    四种实现: ExactMatcher / GlobMatcher / RegexMatcher / NotMatcher。
    所有实现均为 frozen dataclass，不可变。
    """

    def match(self, s: str) -> bool:
        """判定目标字符串是否匹配本规则。"""
        ...

    def __str__(self) -> str:
        """返回匹配器的字符串表示（调试 / /hooks 输出用）。"""
        ...


# ── 四种匹配器实现 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExactMatcher:
    """精确匹配：整串相等。

    例如 =git status 只匹配 "git status"，不匹配 "git status -s"。
    """

    value: str

    def match(self, s: str) -> bool:
        return s == self.value

    def __str__(self) -> str:
        return f"={self.value}"


@dataclass(frozen=True)
class GlobMatcher:
    """glob 通配匹配：* 和 ** 通配。

    Attributes:
        pattern: glob 模式串。
        is_command: True 时走命令匹配（* 匹配任意字符含空格），
                    False 时走路径匹配（按 / 分段）。
    """

    pattern: str
    is_command: bool = False

    def match(self, s: str) -> bool:
        if self.is_command:
            return _match_command_glob(self.pattern, s)
        return _match_path_glob(self.pattern, s)

    def __str__(self) -> str:
        return self.pattern


@dataclass(frozen=True)
class RegexMatcher:
    """正则匹配：用 re.Pattern.search 判定。

    Attributes:
        src: 原始正则字符串（供调试显示）。
        compiled: 编译后的 re.Pattern。
    """

    src: str
    compiled: re.Pattern[str]

    def match(self, s: str) -> bool:
        return self.compiled.search(s) is not None

    def __str__(self) -> str:
        return f"~{self.src}"


@dataclass(frozen=True)
class NotMatcher:
    """反向匹配：对 inner matcher 的结果取反。

    例如 !=value 等价于 NotMatcher(ExactMatcher(value))。
    嵌套支持：!~regex、!glob 等。
    """

    inner: Matcher

    def match(self, s: str) -> bool:
        return not self.inner.match(s)

    def __str__(self) -> str:
        return f"!{self.inner}"


# ── 工厂函数 ────────────────────────────────────────────────────────────


def compile_matcher(pattern: str, *, is_command: bool = False) -> Matcher:
    """解析单条匹配描述串，返回对应 Matcher。

    描述串规则（单字符前缀）:
      "=value"  → ExactMatcher(value)
      "~regex"  → RegexMatcher(regex, compiled)
      "!inner"  → NotMatcher(compile_matcher(inner))
      "value"   → GlobMatcher(pattern)  （无前缀，缺省 glob）

    Args:
        pattern: 匹配描述串。
        is_command: glob 时传入 GlobMatcher.is_command。

    Returns:
        对应的 Matcher 实例。

    Raises:
        ValueError: pattern 为空或正则编译失败。
    """
    if not pattern:
        raise ValueError("empty matcher pattern")

    head, rest = pattern[0], pattern[1:]

    if head == "=":
        return ExactMatcher(rest)

    if head == "~":
        if not rest:
            raise ValueError("empty regex pattern")
        try:
            compiled = re.compile(rest)
        except re.error as e:
            raise ValueError(f"invalid regex: {e}") from e
        return RegexMatcher(rest, compiled)

    if head == "!":
        if not rest:
            raise ValueError("empty not pattern")
        inner = compile_matcher(rest, is_command=is_command)
        return NotMatcher(inner)

    # 缺省：glob
    return GlobMatcher(pattern, is_command=is_command)


# ── glob 匹配实现（从 rule.py 迁移，保持原有语义）───────────────────────


def _compile_glob_for_command(pattern: str) -> re.Pattern:
    """将命令 glob 编译为正则（* 匹配任意字符含空格，** 等价 *）。"""
    escaped = re.escape(pattern)
    # 先处理 \*\*（被 re.escape 转义），再处理 \*
    escaped = escaped.replace(r"\*\*", r".*")
    escaped = escaped.replace(r"\*", r".*")
    return re.compile("^" + escaped + "$")


def _compile_glob_for_path(pattern: str) -> re.Pattern:
    """将路径 glob 编译为正则（* 段内，** 跨段）。

      - 按 / 分割 pattern
      - * → [^/]*（匹配一段内零或多字符，不含 /）
      - ** 末段 → .*（匹配任意内容）
      - ** 中段 → (?:.*/)?（匹配零或多级目录段）
      - ** 后的下一段：不加 /，直接拼接
    """
    if pattern == "":
        return re.compile("^.*$")

    parts = pattern.split("/")
    result = ""
    skip_next_slash = False

    for i, part in enumerate(parts):
        if part == "**":
            if not result:
                result = r".*"
            elif i == len(parts) - 1:
                # 末段 **：匹配任意内容
                result += r"/.*"
            else:
                # 中段 **：后面直接拼接下一段
                result += r"/(?:.*/)?"
                skip_next_slash = True
        else:
            # 将段内的 * 替换为 [^/]*（不跨段）
            escaped = re.escape(part)
            escaped = escaped.replace(r"\*", r"[^/]*")
            if skip_next_slash:
                result += escaped
                skip_next_slash = False
            elif not result:
                result = escaped
            else:
                result += "/" + escaped

    return re.compile("^" + result + "$")


def _match_command_glob(pattern: str, target: str) -> bool:
    """命令 glob 匹配：* 匹配任意字符（含空格），** 等价 *。"""
    if pattern == "":
        return True
    return bool(_compile_glob_for_command(pattern).search(target))


def _match_path_glob(pattern: str, target: str) -> bool:
    """路径 glob 匹配：* 段内、** 跨段。"""
    if pattern == "":
        return True
    # 规范化 target：统一用 /，去除前导 ./
    normalized = target.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return bool(_compile_glob_for_path(pattern).search(normalized))

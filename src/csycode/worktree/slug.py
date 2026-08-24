"""Worktree slug 名称校验与转换。

对齐 mewcode slug.py:
- validate_slug: 返回错误字符串或 None（不抛异常，交由调用方决定）
- flatten_slug: '/' → '+'
"""

import re

_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_MAX_LENGTH = 64


def validate_slug(name: str) -> str | None:
    """校验 worktree slug 名称。

    对齐 mewcode: 有效时返回 None，无效时返回具体错误信息。
    合法示例: "alice", "team/alice", "v1.0", "a_b"
    非法示例: "", "..", "../etc", "a//b", "/x", "a/", "a b"

    Returns:
        None 表示合法，否则为错误描述字符串。
    """
    if not name:
        return "slug 不能为空"
    if len(name) > _MAX_LENGTH:
        return f"slug 长度超过 {_MAX_LENGTH} 字符限制（当前 {len(name)} 字符）"
    if name.startswith("/"):
        return "slug 不能以 '/' 开头"
    if name.endswith("/"):
        return "slug 不能以 '/' 结尾"
    if "//" in name:
        return "slug 不能包含连续的 '//'"

    segments = name.split("/")
    for i, seg in enumerate(segments):
        if not seg:
            return f"slug 第 {i+1} 段为空"
        if seg in (".", ".."):
            return f"slug 第 {i+1} 段 '{seg}' 不允许使用 '.' 或 '..'"
        if not _SEGMENT_RE.match(seg):
            return (
                f"slug 第 {i+1} 段 '{seg}' 包含非法字符，"
                f"只允许 [a-zA-Z0-9._-]"
            )

    return None


def flatten_slug(name: str) -> str:
    """将嵌套 slug 的 '/' 替换为 '+'，避免 Git 分支 D/F 冲突。

    对齐 mewcode flatten_slug。
    """
    return name.replace("/", "+")


# 向后兼容别名
flat_slug = flatten_slug

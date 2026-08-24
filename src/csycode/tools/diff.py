"""Diff 生成工具 —— 对比编辑前后的文件内容。

对齐 mewcode tools/diff.py。

用于 EditFile 工具执行后向用户展示变更摘要。
算法利用"编辑只改动中间一小段"的特点，
从两端找公共前缀/后缀行，比 Myers diff 更轻量。
"""

from __future__ import annotations

from dataclasses import dataclass

# 差异前后展示的上下文行数
_CONTEXT_LINES = 3
# 防止超大 diff 拖垮渲染和上下文
_MAX_DIFF_LINES = 200


@dataclass
class DiffResult:
    """Diff 结果。"""
    text: str       # 格式化后的 diff 文本（带行号）
    additions: int  # 新增行数
    removals: int   # 删除行数


def build_diff(old_content: str, new_content: str) -> DiffResult:
    """对比编辑前后的文件内容，生成带行号的 diff。

    算法：
    1. 从开头找公共前缀行
    2. 从结尾找公共后缀行
    3. 中间部分作为删除行 + 新增行
    4. 前后各展示 _CONTEXT_LINES 行上下文

    Args:
        old_content: 编辑前文件内容。
        new_content: 编辑后文件内容。

    Returns:
        DiffResult 含格式化 diff 文本和增删行数。
    """
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")

    # 公共前缀
    prefix_len = 0
    max_prefix = min(len(old_lines), len(new_lines))
    while prefix_len < max_prefix and old_lines[prefix_len] == new_lines[prefix_len]:
        prefix_len += 1

    # 公共后缀
    suffix_len = 0
    max_suffix = max_prefix - prefix_len
    while (
        suffix_len < max_suffix
        and old_lines[len(old_lines) - 1 - suffix_len]
        == new_lines[len(new_lines) - 1 - suffix_len]
    ):
        suffix_len += 1

    # 差异区域
    removed_lines = old_lines[prefix_len : len(old_lines) - suffix_len]
    added_lines = new_lines[prefix_len : len(new_lines) - suffix_len]

    # 上下文
    context_start = max(0, prefix_len - _CONTEXT_LINES)
    context_before = old_lines[context_start:prefix_len]
    context_end = min(len(old_lines), len(old_lines) - suffix_len + _CONTEXT_LINES)
    context_after = old_lines[len(old_lines) - suffix_len : context_end]

    # 格式化输出
    out: list[str] = []
    old_line_no = context_start + 1
    new_line_no = context_start + 1
    truncated = False

    def push(prefix: str, line_no: int, content: str) -> None:
        nonlocal truncated
        if len(out) >= _MAX_DIFF_LINES:
            truncated = True
            return
        out.append(f"{prefix} {line_no:>4}  {content}")

    for line in context_before:
        push(" ", old_line_no, line)
        old_line_no += 1
        new_line_no += 1
    for line in removed_lines:
        push("-", old_line_no, line)
        old_line_no += 1
    for line in added_lines:
        push("+", new_line_no, line)
        new_line_no += 1
    for line in context_after:
        push(" ", old_line_no, line)
        old_line_no += 1
        new_line_no += 1

    if truncated:
        out.append(f"  … (diff truncated at {_MAX_DIFF_LINES} lines)")

    return DiffResult(
        text="\n".join(out),
        additions=len(added_lines),
        removals=len(removed_lines),
    )

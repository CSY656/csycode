"""Hook 条件匹配器 —— 字段路径求值与条件组合。

ch12: 复用 permission.Matcher，提供 payload 字段路径取值和条件组合判定。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .rule import Condition, Payload


def get_by_path(payload: "Payload", path: str) -> str:
    """从 payload 中按 . 分隔的字段路径递归取值。

    路径不存在或中途遇 None / 非 dict 时返回空字符串。
    非字符串值自动转换为字符串：bool/int/float → str()，嵌套对象 → json.dumps。

    Args:
        payload: 事件 payload 字典。
        path: . 分隔的字段路径（如 "tool_input.path"、"event"）。

    Returns:
        字段值的字符串表示，不存在时返回 ""。
    """
    if not path:
        return ""

    parts = path.split(".")
    current: object = payload

    for part in parts:
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
        if current is None:
            return ""

    # 转换最终值为字符串
    if isinstance(current, str):
        return current
    if isinstance(current, bool):
        return str(current)
    if isinstance(current, (int, float)):
        return str(current)
    if isinstance(current, (dict, list)):
        return json.dumps(current, sort_keys=True, ensure_ascii=False)

    return str(current)


def eval_condition(cond: "Condition | None", payload: "Payload") -> bool:
    """对 payload 求值条件表达式。

    Args:
        cond: 条件对象（None 表示无条件 → True）。
        payload: 事件 payload。

    Returns:
        True 如果条件满足。
    """
    if cond is None:
        return True

    if not cond.atoms:
        return True

    from .rule import CombineMode

    for atom in cond.atoms:
        field_val = get_by_path(payload, atom.field)
        matched = atom.matcher.match(field_val)
        if cond.mode == CombineMode.ALL_OF and not matched:
            return False
        if cond.mode == CombineMode.ANY_OF and matched:
            return True

    # ALL_OF 模式下全部通过 → True，ANY_OF 模式下无一通过 → False
    return cond.mode == CombineMode.ALL_OF

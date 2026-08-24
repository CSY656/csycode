"""内置 Agent 定义加载 —— 对齐 mewcode agents/loader.py 的 _load_builtins。

使用 importlib.resources 读取随包发布的 builtin/*.md 文件，
解析为 Definition 列表。内置定义加载失败视为代码 bug（raise）。
"""

from __future__ import annotations

import sys
from importlib.resources import files

from .definition import Definition, Source
from .parser import parse_definition


def builtin_definitions() -> list[Definition]:
    """加载所有内置 Agent 定义。

    Returns:
        按 name 升序排列的 Definition 列表。

    Raises:
        RuntimeError: 内置包不可用（代码 bug）。
    """
    results: list[Definition] = []

    try:
        pkg = files("csycode.subagent.builtin")
    except (ModuleNotFoundError, TypeError) as e:
        raise RuntimeError(
            "无法加载内置 Agent 定义包 csycode.subagent.builtin"
        ) from e

    for item in sorted(pkg.iterdir(), key=lambda x: x.name):
        if not item.name.endswith(".md"):
            continue
        try:
            raw = item.read_text(encoding="utf-8")
            defi = parse_definition(raw, f"builtin:{item.name}", Source.BUILTIN)
            results.append(defi)
        except Exception as e:
            # 内置定义解析失败是代码 bug，直接 raise
            raise RuntimeError(
                f"内置 Agent 定义 {item.name} 解析失败: {e}"
            ) from e

    if not results:
        print(
            "subagent: 未找到任何内置 Agent 定义（builtin/ 目录为空）",
            file=sys.stderr,
        )

    return results

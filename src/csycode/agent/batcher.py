"""工具分批执行模块。

ToolBatcher 将工具调用列表按安全性分类：
- 安全工具（只读类）：并发执行
- 副作用工具（写入/执行类）：串行执行

安全性优先通过工具 is_readonly 属性判定，回退到名称模式匹配。
执行层 Plan Mode 拦截：被拦截的工具返回 blocked_by_plan_mode=True。
"""

from __future__ import annotations

import asyncio
from enum import Enum, auto
from typing import TYPE_CHECKING, AsyncIterator

from csycode.llm import ToolCall
from csycode.tools.registry import ToolRegistry

from .events import AgentEvent, ToolCallEnd, ToolCallStart

if TYPE_CHECKING:
    from .plan_mode import PlanModeFilter


class SafetyLabel(Enum):
    """工具安全性标签。"""

    SAFE = auto()  # 只读，可并发
    SIDE_EFFECT = auto()  # 写入/执行，需串行


# 安全工具名称模式：匹配这些前缀或完全匹配的工具标记为 SAFE
# 下划线形式（如 read_file → "read_"）和无下划线形式（如 glob → "glob"）都支持
SAFE_PATTERNS: tuple[str, ...] = (
    "read_",
    "search_",
    "glob",
    "grep",
    "list_",
    "find_",
)

# 副作用工具名称模式：匹配这些前缀或完全匹配的工具标记为 SIDE_EFFECT
SIDE_EFFECT_PATTERNS: tuple[str, ...] = (
    "write_",
    "edit_",
    "command_",
    "delete_",
    "run_",
    "exec_",
)


def classify_tool(tool_name: str, registry: ToolRegistry | None = None) -> SafetyLabel:
    """根据工具属性或名称模式判定安全级别。

    优先使用工具的 is_readonly 属性（如果 registry 提供），
    回退到名称模式匹配。

    匹配规则：先检查 is_readonly 属性，再检查 SIDE_EFFECT_PATTERNS，
    最后检查 SAFE_PATTERNS。都不匹配时默认视为 SIDE_EFFECT（保守策略）。
    """
    # 优先使用工具的 is_readonly 属性
    if registry is not None:
        tool = registry.get(tool_name)
        if tool is not None:
            if tool.is_readonly:
                return SafetyLabel.SAFE
            return SafetyLabel.SIDE_EFFECT

    # 回退到名称模式匹配
    for pattern in SIDE_EFFECT_PATTERNS:
        if tool_name.startswith(pattern) or tool_name == pattern:
            return SafetyLabel.SIDE_EFFECT
    for pattern in SAFE_PATTERNS:
        if tool_name.startswith(pattern) or tool_name == pattern:
            return SafetyLabel.SAFE
    # 默认当作副作用工具，安全优先
    return SafetyLabel.SIDE_EFFECT


class ToolBatcher:
    """工具分批执行器。

    将工具调用列表按安全/副作用分组：
    1. Plan Mode 拦截（若启用）
    2. 所有安全工具并发执行（asyncio.gather）
    3. 所有副作用工具按顺序逐个执行
    每一步都产出对应的 ToolCallStart / ToolCallEnd 事件。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        plan_mode_filter: "PlanModeFilter | None" = None,
    ) -> None:
        self._registry = registry
        self._plan_mode = plan_mode_filter

    async def execute(
        self,
        tool_calls: list[ToolCall],
    ) -> AsyncIterator[AgentEvent]:
        """分批执行工具调用。

        Yields:
            ToolCallStart: 每个工具开始执行前
            ToolCallEnd: 每个工具执行完成后
        """
        if not tool_calls:
            return

        total = len(tool_calls)

        # 0. Plan Mode 拦截：检查每个工具是否允许执行
        allowed_calls: list[tuple[int, ToolCall]] = []
        for i, tc in enumerate(tool_calls):
            if self._plan_mode is not None and not self._plan_mode.is_tool_allowed(
                tc.name
            ):
                # 被 Plan Mode 拦截 → 返回阻断结果，不执行
                yield ToolCallStart(
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                    index=i,
                    total=total,
                )
                yield ToolCallEnd(
                    tool_name=tc.name,
                    success=False,
                    content="",
                    original_output="",
                    error=(
                        f"⛔ Plan Mode 拦截: 工具 '{tc.name}' 在计划模式下不可用。"
                        f"当前只能使用只读工具（read_file、glob、grep）、"
                        f"ask_user_question 和 exit_plan_mode。"
                    ),
                    index=i,
                    blocked_by_plan_mode=True,
                )
                continue
            allowed_calls.append((i, tc))

        if not allowed_calls:
            return

        # 1. 分类（使用 is_readonly 属性优先）
        safe_calls: list[tuple[int, ToolCall]] = []
        side_effect_calls: list[tuple[int, ToolCall]] = []
        for i, tc in allowed_calls:
            label = classify_tool(tc.name, self._registry)
            if label == SafetyLabel.SAFE:
                safe_calls.append((i, tc))
            else:
                side_effect_calls.append((i, tc))

        # 2. 并发执行安全工具
        if safe_calls:
            for i, tc in safe_calls:
                yield ToolCallStart(
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                    index=i,
                    total=total,
                )

            async def _execute_one(i: int, tc: ToolCall) -> ToolCallEnd:
                """执行单个工具并返回 ToolCallEnd 事件。"""
                tool = self._registry.get(tc.name)
                if tool is None:
                    return ToolCallEnd(
                        tool_name=tc.name,
                        success=False,
                        content="",
                        error=f"未知工具: '{tc.name}'",
                        index=i,
                    )
                try:
                    result = await tool.execute(**tc.arguments)
                    return ToolCallEnd(
                        tool_name=tc.name,
                        success=result.success,
                        content=result.content,
                        error=result.error,
                        index=i,
                        exit_plan_mode=result.exit_plan_mode,
                        blocked_by_plan_mode=result.blocked_by_plan_mode,
                        show_result_to_user=tool.show_result_to_user,
                    )
                except Exception as e:
                    return ToolCallEnd(
                        tool_name=tc.name,
                        success=False,
                        content="",
                        error=f"工具执行异常: {e}",
                        index=i,
                    )

            # 并发执行所有安全工具
            safe_results = await asyncio.gather(
                *[_execute_one(i, tc) for i, tc in safe_calls],
            )
            for result in safe_results:
                yield result

        # 3. 串行执行副作用工具
        for i, tc in side_effect_calls:
            yield ToolCallStart(
                tool_name=tc.name,
                tool_args=tc.arguments,
                index=i,
                total=total,
            )

            tool = self._registry.get(tc.name)
            if tool is None:
                yield ToolCallEnd(
                    tool_name=tc.name,
                    success=False,
                    content="",
                    error=f"未知工具: '{tc.name}'",
                    index=i,
                )
                continue

            try:
                result = await tool.execute(**tc.arguments)
                yield ToolCallEnd(
                    tool_name=tc.name,
                    success=result.success,
                    content=result.content,
                    error=result.error,
                    index=i,
                    exit_plan_mode=result.exit_plan_mode,
                    blocked_by_plan_mode=result.blocked_by_plan_mode,
                    show_result_to_user=tool.show_result_to_user,
                )
            except Exception as e:
                yield ToolCallEnd(
                    tool_name=tc.name,
                    success=False,
                    content="",
                    error=f"工具执行异常: {e}",
                    index=i,
                )

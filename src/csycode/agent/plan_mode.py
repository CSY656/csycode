"""Plan Mode 两段式工具过滤器。

/plan 阶段：只暴露只读工具 + 特殊允许工具（AskUserQuestion, ExitPlanMode）
/do 阶段：恢复全部工具，模型可以自由执行

双层防护：
  1. API 层 — 过滤传给 LLM 的工具列表（LLM 看不到写工具）
  2. 执行层 — ToolBatcher 每次执行前调用 is_tool_allowed() 拦截
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.tools.registry import ToolRegistry


class PlanModeFilter:
    """Plan Mode 工具过滤器。

    /plan 时进入计划模式：
      - API 层：只暴露 is_readonly=True 或 allowed_in_plan_mode=True 的工具
      - 执行层：is_tool_allowed() 做二次校验，拦截任何非允许工具的调用

    /do 时恢复到完整工具集。

    用法:
        pmf = PlanModeFilter(registry)

        # 用户输入 /plan
        pmf.enter_plan_mode()
        tools = pmf.get_active_tools("openai")
        # → 只读工具 + AskUserQuestion + ExitPlanMode

        # 用户输入 /do
        pmf.enter_do_mode()
        tools = pmf.get_active_tools("openai")
        # → 全部工具
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._in_plan_mode: bool = False

    # ── 属性 ──────────────────────────────────────────────

    @property
    def is_plan_mode(self) -> bool:
        """当前是否处于计划模式。"""
        return self._in_plan_mode

    # ── 模式切换 ──────────────────────────────────────────

    def enter_plan_mode(self) -> None:
        """进入计划模式：仅保留只读工具 + 特殊允许工具。"""
        self._in_plan_mode = True

    def enter_do_mode(self) -> None:
        """退出计划模式：恢复全部工具。"""
        self._in_plan_mode = False

    # ── API 层过滤：控制 LLM 能看到哪些工具 ──────────────────

    def get_active_tools(self, protocol: str) -> list[dict]:
        """返回当前活跃工具的定义列表（协议原生格式）。

        计划模式下返回：只读工具 + allowed_in_plan_mode 工具
        执行模式下返回：全部工具。

        Args:
            protocol: LLM 协议， "anthropic" 或 "openai"。
        """
        if self._in_plan_mode:
            tools = [
                t
                for t in self._registry.list_all()
                if t.is_readonly or t.allowed_in_plan_mode
            ]
        else:
            tools = self._registry.list_all()

        if protocol in ("openai", "openai-compat"):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        # Anthropic 格式（默认）
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    def get_active_tool_names(self) -> list[str]:
        """返回当前活跃工具的名称列表。"""
        if self._in_plan_mode:
            return [
                t.name
                for t in self._registry.list_all()
                if t.is_readonly or t.allowed_in_plan_mode
            ]
        return [t.name for t in self._registry.list_all()]

    # ── 执行层拦截：二次校验，防止 LLM 幻觉绕过 API 层 ──────

    def is_tool_allowed(self, tool_name: str) -> bool:
        """执行层守卫：检查工具是否可以在当前模式执行。

        在 ToolBatcher 执行每个工具前调用。
        非计划模式：全部允许。
        计划模式：只允许 is_readonly 或 allowed_in_plan_mode 的工具。

        Returns:
            True 如果允许执行，False 如果应被拦截。
        """
        if not self._in_plan_mode:
            return True

        tool = self._registry.get(tool_name)
        if tool is None:
            return False  # 未知工具一律拦截

        return tool.is_readonly or tool.allowed_in_plan_mode

"""Hook 运行时引擎 —— 事件分派、only_once 集合、动作执行器协调。

ch12: 持有已加载的 HookRule 列表，在事件 emit 时执行匹配和分派。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .event import Event, is_blocking
from .executor import Executor
from .matcher import eval_condition

if TYPE_CHECKING:
    from .rule import HookRule, Payload


@dataclass
class DispatchResult:
    """单次事件分派的结果。

    Attributes:
        blocked: 是否被拦截。
        reason: 拦截原因（blocked=True 时）。
        blocking_hook_name: 触发拦截的 hook 名称。
        injected_prompts: 本次收集的 prompt 文本列表。
    """
    blocked: bool = False
    reason: str = ""
    blocking_hook_name: str = ""
    injected_prompts: list[str] = field(default_factory=list)


class Engine:
    """Hook 运行时引擎。

    Attributes:
        _rules: 已加载的 HookRule 列表（按加载顺序）。
        _sources: 加载来源文件路径列表。
        _once_fired: only_once 已触发的 hook name 集合。
        _lock: 保护 _once_fired 的异步锁。
        _executor: 动作执行器实例。
    """

    def __init__(self, rules: list["HookRule"], sources: list[str]) -> None:
        self._rules = rules
        self._sources = sources
        self._once_fired: set[str] = set()
        self._lock = asyncio.Lock()
        self._executor = Executor()

    # ── 公开属性 ─────────────────────────────────────────────────────

    @property
    def sources(self) -> list[str]:
        """返回加载来源文件路径列表的副本。"""
        return list(self._sources)

    @property
    def rules(self) -> list["HookRule"]:
        """返回已加载规则的副本。"""
        return list(self._rules)

    # ── dispatch ──────────────────────────────────────────────────────

    async def dispatch(
        self, event: Event, payload: "Payload"
    ) -> DispatchResult:
        """对事件进行分派，串行执行所有匹配的 hook。

        流程:
          1. 过滤匹配 event 的 rule
          2. 跳过已触发的 only_once rule
          3. 求值 if 条件
          4. 执行动作（async 起后台 task，同步等结果）
          5. 收集 injected_prompts 和 blocked 判定

        Args:
            event: 触发事件。
            payload: 事件上下文数据。

        Returns:
            DispatchResult。
        """
        result = DispatchResult()

        for rule in self._rules:
            if rule.event != event:
                continue

            # only_once 检查
            async with self._lock:
                if rule.only_once and rule.name in self._once_fired:
                    continue

            # 条件求值
            if not eval_condition(rule.condition, payload):
                continue

            # async hook: 起 task 后立即继续
            if rule.asyncio_mode:
                asyncio.create_task(
                    self._executor.run(rule, payload, blocking=False)
                )
                if rule.only_once:
                    async with self._lock:
                        self._once_fired.add(rule.name)
                continue

            # 同步执行
            outcome = await self._executor.run(
                rule, payload, blocking=is_blocking(event)
            )

            # 处理失败
            if outcome.err is not None:
                print(
                    f"[hook {rule.name}] {event.value} failed: {outcome.err}",
                    file=sys.stderr,
                )
                continue

            # 收集 prompt
            if outcome.prompt:
                result.injected_prompts.append(outcome.prompt)

            # 标记 only_once
            if rule.only_once:
                async with self._lock:
                    self._once_fired.add(rule.name)

            # 拦截判定
            if outcome.blocked and is_blocking(event):
                result.blocked = True
                result.reason = outcome.reason
                result.blocking_hook_name = rule.name
                break  # 拦截后不再执行后续 hook

        return result

    # ── 生命周期 ──────────────────────────────────────────────────────

    async def reset_for_new_session(self) -> None:
        """清空 only_once 集合（/clear 或 /resume 时调用）。"""
        async with self._lock:
            self._once_fired.clear()

    async def close(self) -> None:
        """关闭引擎（清理 HTTP 客户端等）。"""
        await self._executor.close()

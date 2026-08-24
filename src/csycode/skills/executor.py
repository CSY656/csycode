"""Skill 执行器 —— inline / fork 分发、工具白名单过滤。

对齐 mewcode 的 skills/executor.py，负责:
- filter_tool_registry: 按 allowed_tools 白名单过滤工具集
- execute_inline: 渲染 body → activate_skill 钉到 env
- execute_fork: 独立 Conversation + 子 Agent → 回流结果
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from csycode.conversation import Conversation
from csycode.llm import Message
from csycode.permission import Mode
from csycode.skills.parser import SkillDef, substitute_arguments
from csycode.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from csycode.agent.loop import Agent

log = logging.getLogger(__name__)

# ── 异常 ──────────────────────────────────────────────────────────


class SkillDependencyError(Exception):
    """allowed_tools 中声明了不存在的工具时抛出。"""

    pass


# ── 工具白名单过滤 ───────────────────────────────────────────────


def filter_tool_registry(
    registry: ToolRegistry,
    allowed: list[str],
) -> ToolRegistry:
    """按 allowed_tools 白名单过滤工具注册中心。

    - allowed 为空 → 返回原 registry（不限制）
    - 白名单缺工具 → raise SkillDependencyError
    - is_system_tool=True 的工具自动透传

    Args:
        registry: 原始工具注册中心。
        allowed: 允许使用的工具名称列表。

    Returns:
        新的 ToolRegistry（仅含白名单 + 系统工具）。

    Raises:
        SkillDependencyError: 白名单中有不存在的工具。
    """
    if not allowed:
        return registry

    # 检查声明的工具是否存在
    for tool_name in allowed:
        if registry.get(tool_name) is None:
            raise SkillDependencyError(
                f"Skill 声明的工具 '{tool_name}' 在 ToolRegistry 中未找到"
            )

    filtered = ToolRegistry()
    for tool in registry.list_all():
        if tool.name in allowed or tool.is_system_tool:
            filtered.register(tool)

    return filtered


# ── 常量 ──────────────────────────────────────────────────────────

FORK_RECENT_COUNT = 5


# ── SkillExecutor ─────────────────────────────────────────────────


class SkillExecutor:
    """Skill 执行器，负责 inline 和 fork 两种模式的分发与执行。"""

    def __init__(
        self,
        agent: "Agent",
    ) -> None:
        """初始化执行器。

        Args:
            agent: 主 Agent 实例（用于 inline 激活 + fork 时获取配置）。
        """
        self._agent = agent

    # ── inline ────────────────────────────────────────────────────

    def execute_inline(self, skill: SkillDef, args: str) -> None:
        """inline 模式：渲染 body → 调用 agent.activate_skill 钉到 env。

        Args:
            skill: 已解析的 SkillDef。
            args: 用户参数（替换 $ARGUMENTS 用）。
        """
        prompt = substitute_arguments(skill.prompt_body, args)
        self._agent.activate_skill(skill.name, prompt)
        # 记录 skill invocation 到 recovery_state（对齐 mewcode）
        if getattr(self._agent, "recovery", None) is not None:
            self._agent.recovery.record_skill_invocation(skill.name, prompt)

    # ── fork ──────────────────────────────────────────────────────

    async def execute_fork(
        self,
        skill: SkillDef,
        args: str,
        tool_registry: ToolRegistry,
    ) -> str:
        """fork 模式：独立子 Agent 隔离执行后回流结果。

        Args:
            skill: 已解析的 SkillDef。
            args: 用户参数（替换 $ARGUMENTS 用）。
            tool_registry: 主 Agent 的工具注册中心（用于过滤）。

        Returns:
            子 Agent 的最终输出文本。出错时返回错误描述字符串。
        """
        prompt = substitute_arguments(skill.prompt_body, args)
        # 记录 skill invocation 到 recovery_state（对齐 mewcode）
        if getattr(self._agent, "recovery", None) is not None:
            self._agent.recovery.record_skill_invocation(
                skill.name, skill.prompt_body
            )

        # ── 1. 独立 Conversation ──
        fork_conv = Conversation()

        # ── 2. 按 context 模式装填历史 ──
        context_messages = self._build_fork_context(skill.context)
        for msg in context_messages:
            if msg.role == "user":
                fork_conv.add_user(msg.content)
            elif msg.role == "assistant":
                fork_conv.add_assistant(msg.content)

        fork_conv.add_user(prompt)

        # ── 3. 过滤工具集 ──
        try:
            fork_registry = filter_tool_registry(tool_registry, skill.allowed_tools)
        except SkillDependencyError as e:
            return f"[skill {skill.name} 失败: {e}]"

        # ── 4. 构造子 Agent（权限检查器设为 None，对齐 mewcode）──
        from csycode.agent.loop import Agent as AgentClass
        from csycode.agent.events import LoopEnd

        fork_agent = AgentClass(
            provider=self._agent._provider,
            tool_registry=fork_registry,
            conversation=fork_conv,
            config=self._agent._config,
            version=self._agent._version,
            engine=None,  # 无权限检查（skill 内部操作信任）
            plan_mode_filter=None,
            context_window=self._agent.context_window,
            work_dir=self._agent.runtime.session.workspace,
        )

        # ── 5. 运行子 Agent 并收集文本 ──
        result_parts: list[str] = []
        try:
            async for event in fork_agent.run(Mode.DEFAULT):
                from csycode.agent.events import (
                    TextDelta,
                    LoopEnd,
                )

                if isinstance(event, TextDelta):
                    result_parts.append(event.text)
                elif isinstance(event, LoopEnd):
                    break
        except asyncio.CancelledError:
            result_parts.append(f"\n[skill {skill.name} 被取消]")
        except Exception as e:
            log.warning("fork skill '%s' 执行异常: %s", skill.name, e)
            return f"[skill {skill.name} 失败: {e}]"

        return "".join(result_parts)

    # ── fork 上下文构造 ───────────────────────────────────────────

    def _build_fork_context(self, mode: str) -> list[Message]:
        """按 mode 从主 Agent 对话历史中提取上下文消息。

        对齐 mewcode：使用 hasattr 安全访问 _conversation。

        Args:
            mode: "none" | "recent" | "full"

        Returns:
            Message 列表（仅 user/assistant 角色，不含 tool 结果）。
        """
        if mode == "none":
            return []

        # 安全访问对话历史（对齐 mewcode hasattr 模式）
        if not hasattr(self._agent, "_conversation"):
            return []
        main_conv = self._agent._conversation
        if main_conv is None:
            return []

        history = main_conv.messages()
        if not history:
            return []

        # 过滤出有内容的 user/assistant 消息（排除 tool 结果）
        content_messages = [
            m
            for m in history
            if m.content
            and not m.tool_call_id  # tool 结果消息（role=user, 含 tool_call_id）
            and m.role in ("user", "assistant")
        ]

        if mode == "recent":
            return content_messages[-FORK_RECENT_COUNT:]

        if mode == "full":
            if not content_messages:
                return []
            summary_parts = []
            for m in content_messages:
                prefix = "User" if m.role == "user" else "Assistant"
                text = m.content[:200]
                if len(m.content) > 200:
                    text += "..."
                summary_parts.append(f"{prefix}: {text}")
            summary = "## Previous conversation summary\n\n" + "\n\n".join(
                summary_parts
            )
            return [Message(role="user", content=summary)]

        return []

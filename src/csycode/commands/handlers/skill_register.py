"""Skill 命令注册器 —— 将 Catalog 中的每个 Skill 注册为 / 命令。

对齐 mewcode 的 commands/handlers/skill_register.py，适配 csycode 的
Command / Kind / UI 协议模式。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from csycode.command.command import Command, Kind

if TYPE_CHECKING:
    from csycode.command.ui import UI
    from csycode.skills.executor import SkillExecutor
    from csycode.skills.loader import SkillLoader
    from csycode.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

# ── 模块级状态 ──────────────────────────────────────────────────

_REGISTERED_SKILL_NAMES: set[str] = set()
"""本次会话已注册为命令的 skill 名称集合。"""


# ── 公共入口 ─────────────────────────────────────────────────────


def register_skill_commands(
    cmd_registry,
    loader: "SkillLoader",
    executor: "SkillExecutor | None",
    tool_registry: "ToolRegistry | None" = None,
) -> None:
    """将 Catalog 中每个 Skill 注册为 Kind.PROMPT 命令。

    每个 Skill 得到一个 `/<name>` 斜杠命令，描述末尾标注 `[skill]`。
    重复调用时先清理旧命令。

    Args:
        cmd_registry: csycode command.Registry 实例。
        loader: Skill 加载器。
        executor: Skill 执行器。
        tool_registry: 工具注册中心（fork 模式过滤用）。
    """
    # ── 1. 清理旧注册 ──
    for name in list(_REGISTERED_SKILL_NAMES):
        if name in cmd_registry._by_name:
            del cmd_registry._by_name[name]
        cmd_registry._visible = [
            c for c in cmd_registry._visible if c.name != name
        ]
        _REGISTERED_SKILL_NAMES.discard(name)

    # ── 2. 注册新命令 ──
    for skill_name, skill_desc in loader.get_catalog():
        # 不与已有命令冲突
        if cmd_registry.lookup(skill_name) is not None:
            log.warning(
                "Skill 命令 '/%s' 与已有命令冲突，跳过", skill_name,
            )
            continue

        handler = _make_skill_handler(
            skill_name, loader, executor, tool_registry
        )

        cmd = Command(
            name=skill_name,
            description=f"{skill_desc} [skill]",
            kind=Kind.PROMPT,
            handler=handler,
            hidden=False,
        )

        try:
            cmd_registry.register(cmd)
            _REGISTERED_SKILL_NAMES.add(skill_name)
        except RuntimeError as e:
            log.warning("无法注册 skill 命令 '%s': %s", skill_name, e)


# ── handler 构造 ────────────────────────────────────────────────


def _make_skill_handler(
    name: str,
    loader: "SkillLoader",
    executor: "SkillExecutor | None",
    tool_registry: "ToolRegistry | None",
):
    """为单个 Skill 创建命令 handler 闭包。

    inline skill: 调 execute_inline → activate_skill → 重新触发 Agent loop。
    fork skill: 异步 execute_fork → 结果写回对话。

    对齐 mewcode: args 参数传递给 execute_inline / execute_fork，
    使得 skill 中的 $ARGUMENTS 占位符被正确替换。
    """

    async def handler(ui: "UI", args: str = "") -> None:
        if executor is None:
            ui.error("Skill 执行器未初始化")
            return

        skill = loader.get(name)
        if skill is None:
            ui.error(f"未找到 Skill：{name}")
            return

        if skill.mode == "fork":
            # fork 模式：后台异步执行，结果写回对话
            ui.println(f"⏳ 正在运行 {name} skill...")

            async def _run_fork() -> None:
                try:
                    result = await executor.execute_fork(
                        skill, args, tool_registry or _dummy_registry()
                    )
                    # 将结果注入对话
                    ui.inject_and_send(
                        f"/{name}",
                        f"[{name} skill 结果]\n\n{result}",
                    )
                except Exception as e:
                    ui.error(f"Skill {name} 执行失败: {e}")

            asyncio.create_task(_run_fork())
        else:
            # inline 模式：钉 SOP 到 env → 重新触发 Agent loop
            executor.execute_inline(skill, args)
            ui.println(f"skill({name}) 已加载")
            # 用 args（如果有）作为触发消息；否则用 /<name>
            trigger = args if args else f"/{name}"
            ui.inject_and_send(trigger, trigger)

    return handler


def _dummy_registry():
    """当 tool_registry 为 None 时返回一个空的 ToolRegistry。"""
    from csycode.tools.registry import ToolRegistry

    return ToolRegistry()

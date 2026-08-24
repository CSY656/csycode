"""/skill 管理命令 handler —— list / info / reload。

对齐 mewcode 的 commands/handlers/skill.py：
- _handle_reload 直接重建 skill catalog 并设置到 agent
- 使用 agent 引用代替 on_reload 回调
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.agent.loop import Agent
    from csycode.command.ui import UI
    from csycode.skills.loader import SkillLoader
    from csycode.skills.executor import SkillExecutor


def make_skill_handler(
    loader: "SkillLoader",
    executor: "SkillExecutor | None" = None,
    cmd_registry=None,
    agent: "Agent | None" = None,
    tool_registry=None,
):
    """创建 /skill 命令的 handler 闭包。

    Args:
        loader: Skill 加载器。
        executor: Skill 执行器（reload 后重新注册命令时需要）。
        cmd_registry: 命令注册中心（reload 后重新注册用）。
        agent: Agent 实例（reload 后刷新 skill catalog 用）。
        tool_registry: 工具注册中心（fork 模式过滤用）。
    """

    async def handle_skill(ui: "UI", args: str = "") -> None:
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else "list"
        sub_args = parts[1] if len(parts) > 1 else ""

        if subcmd == "list":
            _print_skill_list(ui, loader)
        elif subcmd == "info":
            _print_skill_info(ui, loader, sub_args)
        elif subcmd == "reload":
            await _handle_reload(
                ui, loader, executor, cmd_registry, agent, tool_registry
            )
        else:
            ui.error(
                f"未知子命令：{subcmd}\n"
                "用法：/skill list | /skill info <name> | /skill reload"
            )

    return handle_skill


def _print_skill_list(ui: "UI", loader: "SkillLoader") -> None:
    """输出已加载的 Skill 列表。"""
    catalog = loader.get_catalog()
    if not catalog:
        ui.println("（没有已加载的 Skill）")
        return

    lines = ["已加载的 Skill："]
    for name, desc in catalog:
        source = loader.get_source_label(name)
        lines.append(f"  {name:<20} {desc}  [{source}]")
    ui.println("\n".join(lines))


def _print_skill_info(ui: "UI", loader: "SkillLoader", name: str) -> None:
    """输出单个 Skill 的详细元数据（对齐 mewcode _handle_info）。"""
    if not name:
        ui.error("用法：/skill info <name>")
        return

    skill = loader.get(name)
    if skill is None:
        ui.error(f"未找到 Skill：{name}")
        return

    source = loader.get_source_label(name)
    lines = [
        f"Skill: {skill.name}",
        f"Description: {skill.description}",
        f"Mode: {skill.mode}",
        f"Context: {skill.context}",
        f"Model: {skill.model or '(default)'}",
        f"Allowed Tools: {', '.join(skill.allowed_tools) if skill.allowed_tools else '(all)'}",
        f"Source: {source}",
        f"Path: {skill.source_path or '(builtin)'}",
        f"Directory: {skill.is_directory}",
    ]
    ui.println("\n".join(lines))


async def _handle_reload(
    ui: "UI",
    loader: "SkillLoader",
    executor: "SkillExecutor | None",
    cmd_registry,
    agent: "Agent | None",
    tool_registry,
) -> None:
    """重新加载 Skill 并刷新命令注册与 Agent catalog（对齐 mewcode _handle_reload）。

    直接操作 agent 重建 skill catalog，不再依赖外部 on_reload 回调。
    """
    skills = loader.reload()

    # 重新注册 skill 命令
    if cmd_registry is not None:
        from csycode.commands.handlers.skill_register import (
            register_skill_commands,
        )

        register_skill_commands(
            cmd_registry,
            loader,
            executor,
            tool_registry,
        )

    # 直接刷新 agent 的 skill catalog（对齐 mewcode）
    if agent is not None:
        catalog = loader.get_catalog()
        if catalog:
            lines = ["你可以使用以下 Skills：", ""]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append(
                "如果用户请求匹配某个 Skill 的描述，调用 LoadSkill 工具激活它。"
            )
            agent.set_skill_catalog("\n".join(lines))
        else:
            agent.set_skill_catalog("")

    ui.println(f"已重新加载 {len(skills)} 个 Skill")

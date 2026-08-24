"""/team 系列 slash 命令 —— ch15。

提供 /team list / info / delete / kill 四个本地命令。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from csycode.command.command import Command, Kind

if TYPE_CHECKING:
    from csycode.team.manager import Manager


def _shorthand(name: str, max_len: int = 8) -> str:
    """截断过长的 agent_id 用于显示。"""
    if len(name) <= max_len + 2:
        return name
    return name[:max_len] + ".."


def register_team_commands(
    cmd_registry,
    team_mgr: Manager,
) -> None:
    """注册 /team 系列命令到命令注册表。"""

    # ── /team list ────────────────────────────────────────────────
    async def _team_list(ui, args: str) -> None:
        teams = team_mgr.list_()
        if not teams:
            ui.println("(没有活跃的 Team，使用 TeamCreate 工具创建一个)")
            return

        lines = ["Team 列表:"]
        for t in teams:
            active = len([m for m in t.members if m.is_active is not False])
            total = len(t.members)
            lines.append(
                f"  {t.sanitized_name}  |  {t.backend.value}  |  "
                f"{total} 成员 ({active} 活跃)"
            )
        ui.println("\n".join(lines))

    cmd_registry.register(Command(
        name="team list",
        description="列出所有 Team",
        kind=Kind.LOCAL,
        handler=_team_list,
    ))

    # ── /team info [name] ─────────────────────────────────────────
    async def _team_info(ui, args: str) -> None:
        name = args.strip()
        if not name:
            # 没有指定名称：如果只有一个 Team 则显示它，否则列出
            teams = team_mgr.list_()
            if not teams:
                ui.println("(没有活跃的 Team)")
                return
            if len(teams) == 1:
                name = teams[0].sanitized_name
            else:
                ui.println("请指定 Team 名称: /team info <name>")
                names = ", ".join(t.sanitized_name for t in teams)
                ui.println(f"可用的 Team: {names}")
                return

        team = team_mgr.get(name)
        if team is None:
            ui.error(f"Team '{name}' 不存在。输入 /team list 查看所有 Team")
            return

        lines = [
            f"Team: {team.name} (sanitized: {team.sanitized_name})",
            f"Lead: {team.lead_agent_id}",
            f"Backend: {team.backend.value}",
            f"描述: {team.description or '(无)'}",
            f"配置: {team.config_path}",
            f"创建: {team.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "成员:",
        ]
        for m in team.members:
            status = "活跃" if m.is_active is not False else "空闲"
            lines.append(
                f"  {m.name}: agent={_shorthand(m.agent_id)}, "
                f"backend={m.backend_type.value}, "
                f"状态={status}"
            )
            if m.worktree_path:
                lines.append(f"    worktree: {m.worktree_path}")
            if m.pane_id:
                lines.append(f"    pane: {m.pane_id}")
        ui.println("\n".join(lines))

    cmd_registry.register(Command(
        name="team info",
        description="查看 Team 详情",
        kind=Kind.LOCAL,
        handler=_team_info,
    ))

    # ── /team delete <name> [--force] ─────────────────────────────
    async def _team_delete(ui, args: str) -> None:
        parts = args.strip().split()
        force = "--force" in parts
        name = " ".join(p for p in parts if p != "--force").strip()
        if not name:
            teams = team_mgr.list_()
            if teams:
                names = ", ".join(t.sanitized_name for t in teams)
                ui.error("请指定要删除的 Team: /team delete <name> [--force]")
                ui.println(f"可用的 Team: {names}")
            else:
                ui.println("(没有可删除的 Team)")
            return

        try:
            await team_mgr.delete(name, force=force)
            ui.println(f"Team '{name}' 已删除")
        except Exception as e:
            ui.error(f"删除失败: {e}")

    cmd_registry.register(Command(
        name="team delete",
        description="删除 Team",
        kind=Kind.LOCAL,
        handler=_team_delete,
    ))

    # ── /team kill <member> ───────────────────────────────────────
    async def _team_kill(ui, args: str) -> None:
        member_name = args.strip()
        if not member_name:
            # 列出所有 Team 的所有非 lead 成员
            all_members: list[str] = []
            for team in team_mgr.list_():
                for m in team.members:
                    if m.name != "lead":
                        all_members.append(f"{m.name} (team={team.sanitized_name})")
            if all_members:
                ui.error("用法: /team kill <member>")
                ui.println(f"可 kill 的成员: {', '.join(all_members)}")
            else:
                ui.println("(没有可 kill 的队员)")
            return

        found = False
        for team in team_mgr.list_():
            member = team.member_by_name(member_name)
            if member is None:
                continue

            try:
                from csycode.team.backend import new_backend
                bk = new_backend(member.backend_type, task_mgr=team_mgr.task_mgr)
                await bk.kill(member.pane_id, member.agent_id)
            except Exception as e:
                ui.error(f"kill 后端失败: {e}")
                return

            await team_mgr.remove_member(team, member_name)
            ui.println(f"成员 '{member_name}' 已从 Team '{team.sanitized_name}' 移除")
            found = True
            break

        if not found:
            ui.error(f"找不到成员 '{member_name}'。输入 /team info 查看所有成员")

    cmd_registry.register(Command(
        name="team kill",
        description="强制终止队员",
        kind=Kind.LOCAL,
        handler=_team_kill,
    ))

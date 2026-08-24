"""/worktree 命令 handler —— Worktree 手动管理入口。

子命令:
  /worktree create <slug>      创建 Worktree
  /worktree list               列出所有 Worktree
  /worktree enter <slug>       进入 Worktree（后续工具调用使用 Worktree 路径）
  /worktree exit [--remove] [--discard]  退出当前 Worktree
  /worktree remove <slug> [--discard]    删除指定 Worktree
"""

from __future__ import annotations

from csycode.command.ui import UI


async def handle_worktree(ui: UI, args: str) -> None:
    """解析子命令并分发到 worktree_accessor 方法。"""
    accessor = ui.worktree_accessor()
    if accessor is None:
        ui.error("Worktree 功能未启用（可能不在 Git 仓库中）")
        return

    parts = args.strip().split()
    if not parts:
        ui.println("用法: /worktree <create|list|enter|exit|remove> [...]")
        return

    sub = parts[0].lower()
    rest = parts[1:]

    if sub == "create":
        await _cmd_create(ui, accessor, rest)
    elif sub == "list":
        await _cmd_list(ui, accessor)
    elif sub == "enter":
        await _cmd_enter(ui, accessor, rest)
    elif sub == "exit":
        await _cmd_exit(ui, accessor, rest)
    elif sub == "remove":
        await _cmd_remove(ui, accessor, rest)
    else:
        ui.error(f"未知子命令: {sub}。可用: create | list | enter | exit | remove")


async def _cmd_create(ui: UI, accessor, rest: list[str]) -> None:
    if not rest:
        ui.error("用法: /worktree create <slug>")
        return
    slug = rest[0]
    try:
        path, branch = await accessor.create(slug)
        ui.println(f"Worktree 已创建: {path} (分支 {branch})")
    except ValueError as e:
        ui.error(str(e))
    except Exception as e:
        ui.error(f"创建 Worktree 失败: {e}")


async def _cmd_list(ui: UI, accessor) -> None:
    items = accessor.list()
    if not items:
        ui.println("（无活跃 Worktree）")
        return
    for item in items:
        active_mark = " *" if item.active else "  "
        manual_mark = " [手动]" if item.manual else " [自动]"
        ui.println(
            f"{active_mark} {item.name:<24} {item.path}  ({item.branch}){manual_mark}"
        )


async def _cmd_enter(ui: UI, accessor, rest: list[str]) -> None:
    if not rest:
        ui.error("用法: /worktree enter <slug>")
        return
    slug = rest[0]
    try:
        await accessor.enter(slug)
        ui.println(f"已进入 Worktree: {slug}")
    except ValueError as e:
        ui.error(str(e))
    except Exception as e:
        ui.error(f"进入 Worktree 失败: {e}")


async def _cmd_exit(ui: UI, accessor, rest: list[str]) -> None:
    remove = "--remove" in rest
    discard = "--discard" in rest
    action = "remove" if remove else "keep"
    try:
        removed = await accessor.exit(action, discard)
        if removed:
            ui.println("已退出并删除 Worktree")
        else:
            ui.println("已退出 Worktree（目录保留）")
    except ValueError as e:
        ui.error(str(e))
    except Exception as e:
        ui.error(f"退出 Worktree 失败: {e}")


async def _cmd_remove(ui: UI, accessor, rest: list[str]) -> None:
    if not rest:
        ui.error("用法: /worktree remove <slug> [--discard]")
        return
    slug = rest[0]
    discard = "--discard" in rest
    try:
        await accessor.remove(slug, discard)
        ui.println(f"Worktree 已删除: {slug}")
    except ValueError as e:
        ui.error(str(e))
    except Exception as e:
        ui.error(f"删除 Worktree 失败: {e}")

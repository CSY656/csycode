"""/exit /plan /compact /resume /clear 影响界面命令 handler。"""

from __future__ import annotations

from csycode.permission import Mode


async def handle_exit(ui) -> None:
    """关闭 TUI 进程。"""
    ui.quit()


async def handle_plan(ui) -> None:
    """切换到 PLAN 权限模式。"""
    ui.set_mode(Mode.PLAN)
    ui.println("已切换到 PLAN 模式")


async def handle_compact(ui) -> None:
    """手动触发上下文压缩。idle 守护由 dispatch_slash 统一处理。"""
    ui.force_compact()


async def handle_resume(ui) -> None:
    """打开历史会话恢复列表。idle 守护由 dispatch_slash 统一处理。"""
    ui.open_resume_menu()


async def handle_clear(ui) -> None:
    """关闭当前会话、开新会话、清空对话。"""
    ui.clear_and_new_session()
    ui.println("已清空当前会话，开启新 session")

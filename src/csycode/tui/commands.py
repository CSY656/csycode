"""TUI 内置命令注册与分发。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from csycode.agent.events import CompactPhase
from csycode.permission import Mode
from csycode.prompt import EXECUTE_DIRECTIVE

if TYPE_CHECKING:
    from .app import csyCodeApp

CommandHandler = Callable[["csyCodeApp"], Awaitable[None]]

BUILTIN_COMMANDS: dict[str, CommandHandler] = {}


# ── 压缩通知格式化（自动/手动/紧急共用）─────────────────────────────


def format_compact_notice(
    phase: CompactPhase | None = None,
    before: int = 0,
    after: int = 0,
    err: str = "",
) -> str:
    """按压缩阶段返回统一的系统提示文案。

    自动 / 紧急 / 手动三条路径共用此函数，保证文案风格一致。
    """
    if phase == CompactPhase.BEFORE_AUTO:
        return "正在压缩上下文..."
    elif phase == CompactPhase.BEFORE_EMERGENCY:
        return "上下文撞墙，自动压缩中..."
    elif phase in (CompactPhase.AFTER_AUTO, CompactPhase.AFTER_EMERGENCY):
        if err:
            return f"压缩失败：{err}"
        return f"已压缩，token 从 {before:,} 降至 {after:,}"
    else:
        # 手动路径（phase is None）或其他情况
        if err:
            return f"压缩失败：{err}"
        if before > 0 and after > 0:
            return f"已压缩，token 从 {before:,} 降至 {after:,}"
        return "当前无需压缩"


def _register(cmd: str) -> Callable[[CommandHandler], CommandHandler]:
    def decorator(handler: CommandHandler) -> CommandHandler:
        BUILTIN_COMMANDS[cmd] = handler
        return handler

    return decorator


def dispatch_command(input_: str) -> tuple[CommandHandler | None, bool]:
    text = input_.strip()
    if not text.startswith("/"):
        return None, False

    parts = text.split(maxsplit=1)
    cmd = parts[0]

    handler = BUILTIN_COMMANDS.get(cmd)
    if handler is not None:
        return handler, True

    return _unknown_command, True


async def _unknown_command(app: "csyCodeApp") -> None:
    from rich.text import Text

    log = app.query_one("#log", RichLog)  # noqa: F821
    available = " ".join(sorted(BUILTIN_COMMANDS.keys()))
    log.write(Text(f"未知命令，可用命令: {available}", style="bold yellow"))


# ── /exit ──


@_register("/exit")
async def handle_exit(app: "csyCodeApp") -> None:
    await app.action_quit()


# ── /plan ──


@_register("/plan")
async def handle_plan(app: "csyCodeApp") -> None:
    from rich.text import Text

    app._mode = Mode.PLAN
    if app._plan_mode_filter is not None:
        app._plan_mode_filter.enter_plan_mode()
    app._update_mode_label()
    app._update_statusbar()
    log = app.query_one("#log", RichLog)  # noqa: F821
    log.write(
        Text(
            "● Plan Mode — 仅使用只读工具，模型将产出计划而非修改代码",
            style="bold blue",
        )
    )


# ── /do ──


@_register("/do")
async def handle_do(app: "csyCodeApp") -> None:
    from rich.text import Text

    app._mode = Mode.DEFAULT
    if app._plan_mode_filter is not None:
        app._plan_mode_filter.enter_do_mode()
    app._update_mode_label()
    app._update_statusbar()
    log = app.query_one("#log", RichLog)  # noqa: F821
    log.write(Text("● Normal Mode — 已恢复全部工具", style="bold blue"))
    app.conv.add_user(str(EXECUTE_DIRECTIVE))


# ── /compact ──


@_register("/compact")
async def handle_compact(app: "csyCodeApp") -> None:
    from rich.text import Text

    if app.agent is None:
        log = app.query_one("#log", RichLog)  # noqa: F821
        log.write(Text("Agent 未初始化", style="bold yellow"))
        return

    log = app.query_one("#log", RichLog)  # noqa: F821
    log.write(Text("正在压缩上下文...", style="bold blue"))

    try:
        result = await app.agent.manual_compact()
        if result is not None:
            before, after = result
            notice = format_compact_notice(before=before, after=after)
            log.write(Text(f"📦 {notice}", style="bold green"))
        else:
            log.write(Text("当前无需压缩（未达阈值或前缀太小）", style="bold yellow"))
    except Exception as e:
        notice = format_compact_notice(err=str(e))
        log.write(Text(f"⚠ {notice}", style="bold red"))


# ── /resume ──


@_register("/resume")
async def handle_resume(app: "csyCodeApp") -> None:
    """进入会话恢复选择列表。"""
    from rich.text import Text

    if app.state != app.state.__class__.IDLE:
        log = app.query_one("#log", RichLog)  # noqa: F821
        log.write(Text("请等待当前任务完成后再恢复会话", style="bold yellow"))
        return

    app.begin_resume()

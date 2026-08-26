"""一次性注册内置命令到 Registry。

对齐 mewcode: /session, /memory, /permission 支持子命令，
通过可选依赖注入 engine / mem_mgr / sessions_dir。
"""

from __future__ import annotations

from .builtin_local import (
    make_help_handler,
    make_memory_handler,
    make_permission_handler,
    make_session_handler,
    handle_effort,
    handle_status,
)
from .builtin_prompt import handle_do, handle_review
from .builtin_ui import (
    handle_clear,
    handle_compact,
    handle_exit,
    handle_plan,
    handle_resume,
)
from .command import Command, Kind
from .registry import Registry


def register_builtins(
    reg: Registry,
    *,
    engine=None,         # Permission Engine
    mem_mgr=None,        # Memory Manager
    sessions_dir: str = "",  # sessions 目录路径
) -> None:
    """按字典序注册内置命令。

    Args:
        reg: 命令注册中心。
        engine: 权限引擎（可选，用于 /permission rules）。
        mem_mgr: 记忆管理器（可选，用于 /memory list）。
        sessions_dir: 会话存档目录（可选，用于 /session list）。
    """

    # ── 纯本地类 ──────────────────────────────────────────────────

    reg.register(
        Command(
            name="help",
            description="显示帮助信息",
            kind=Kind.LOCAL,
            handler=make_help_handler(reg),
        )
    )

    reg.register(
        Command(
            name="memory",
            description="显示已加载的记忆文件 [/memory list]",
            kind=Kind.LOCAL,
            handler=make_memory_handler(mem_mgr=mem_mgr),
        )
    )

    reg.register(
        Command(
            name="permission",
            description="显示权限状态 [/permission mode | rules]",
            kind=Kind.LOCAL,
            handler=make_permission_handler(engine=engine),
        )
    )

    reg.register(
        Command(
            name="session",
            description="显示会话信息 [/session | /session list]",
            kind=Kind.LOCAL,
            handler=make_session_handler(sessions_dir=sessions_dir),
        )
    )

    reg.register(
        Command(
            name="status",
            description="显示当前运行状态",
            kind=Kind.LOCAL,
            handler=handle_status,
        )
    )

    reg.register(
        Command(
            name="effort",
            description="切换思考强度 [/effort low|medium|high|xhigh]",
            kind=Kind.LOCAL,
            handler=handle_effort,
        )
    )

    # ── 影响界面类 ────────────────────────────────────────────────

    reg.register(
        Command(
            name="clear",
            description="清空当前会话并开启新会话",
            kind=Kind.UI,
            handler=handle_clear,
        )
    )

    reg.register(
        Command(
            name="compact",
            description="手动压缩上下文",
            kind=Kind.UI,
            handler=handle_compact,
        )
    )

    reg.register(
        Command(
            name="exit",
            description="退出程序",
            kind=Kind.UI,
            handler=handle_exit,
        )
    )

    reg.register(
        Command(
            name="plan",
            description="切换到计划模式",
            kind=Kind.UI,
            handler=handle_plan,
        )
    )

    reg.register(
        Command(
            name="resume",
            description="恢复历史会话",
            kind=Kind.UI,
            handler=handle_resume,
        )
    )

    # ── 提示词类 ──────────────────────────────────────────────────

    reg.register(
        Command(
            name="do",
            description="退出计划模式并开始执行",
            kind=Kind.PROMPT,
            handler=handle_do,
        )
    )

    reg.register(
        Command(
            name="review",
            description="审查当前代码变更",
            kind=Kind.PROMPT,
            handler=handle_review,
        )
    )

    # ── ch12: Hook 管理 ──

    from csycode.tui.hooks import handle_hooks

    reg.register(
        Command(
            name="hooks",
            description="列出已加载的 hook 规则",
            kind=Kind.LOCAL,
            handler=handle_hooks,
        )
    )

    # ── ch14: Worktree 管理 ──

    from csycode.command.builtin_worktree import handle_worktree

    reg.register(
        Command(
            name="worktree",
            description="管理 Git Worktree 隔离副本",
            kind=Kind.LOCAL,
            handler=handle_worktree,
        )
    )

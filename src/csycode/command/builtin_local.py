"""/help /status /memory /permission /session 纯本地命令 handler。

对齐 mewcode: /session, /memory, /permission 支持子命令，
通过工厂函数注入外部依赖（session/memory/engine）。
"""

from __future__ import annotations

from csycode.command.command import Handler
from csycode.command.registry import Registry


# ── /help ─────────────────────────────────────────────────────────────


def make_help_handler(reg: Registry) -> Handler:
    """构造 /help 的 handler 闭包，捕获 reg 以便查询可见命令列表。"""

    async def _handler(ui) -> None:
        commands = reg.visible()
        if not commands:
            ui.println("没有可用命令")
            return

        max_name_len = max(len(c.name) for c in commands)
        w = max_name_len + 4  # 额外内边距
        lines = [f"/{c.name.ljust(w)}{c.description}" for c in commands]
        ui.println("\n".join(lines))

    return _handler


# ── /status ───────────────────────────────────────────────────────────


async def handle_status(ui) -> None:
    """输出当前运行时 6 项状态信息。"""
    lines = [
        "csyCode Status",
        "",
        f"{'Mode:':<12}{str(ui.mode())}",
        f"{'Tokens:':<12}{ui.usage_in()} in / {ui.usage_out()} out",
        f"{'Tools:':<12}{ui.tool_count()} enabled",
        f"{'Memories:':<12}{len(ui.memory_files())} files",
        f"{'Model:':<12}{ui.model_name()}",
        f"{'Directory:':<12}{ui.cwd()}",
    ]
    ui.println("\n".join(lines))


# ── /memory ───────────────────────────────────────────────────────────


def make_memory_handler(
    mem_mgr=None,  # MemoryManager (optional)
) -> Handler:
    """构造 /memory handler，支持 list / 默认列出。"""

    async def _handler(ui, args: str = "") -> None:
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else "list"

        if subcmd == "list" or not subcmd:
            _handle_memory_list(ui, mem_mgr)
        else:
            ui.error(
                f"未知子命令：{subcmd}\n用法：/memory [list]"
            )

    return _handler


def _handle_memory_list(ui, mem_mgr=None) -> None:
    """列出已加载的记忆文件及类型/描述（对齐 mewcode get_display_text）。"""
    # 优先使用 MemoryManager 获取详细信息
    if mem_mgr is not None:
        try:
            proj_files, user_files = mem_mgr.list_files()
            if not proj_files and not user_files:
                ui.println("无已加载的记忆文件")
                return

            lines = ["## 项目记忆"]
            if proj_files:
                for f in proj_files:
                    lines.append(f"  📄 {f}")
            else:
                lines.append("  (无)")

            lines.append("")
            lines.append("## 用户记忆")
            if user_files:
                for f in user_files:
                    lines.append(f"  📄 {f}")
            else:
                lines.append("  (无)")

            ui.println("\n".join(lines))
            return
        except Exception:
            pass

    # 回退：仅列出文件名
    files = ui.memory_files()
    if not files:
        ui.println("无已加载的记忆文件")
        return
    for f in files:
        ui.println(f"  📄 {f}")


# ── /permission ───────────────────────────────────────────────────────


def make_permission_handler(
    engine=None,  # Engine (optional)
) -> Handler:
    """构造 /permission handler，支持 mode / rules 子命令。"""

    async def _handler(ui, args: str = "") -> None:
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else "mode"

        if subcmd == "mode" or not subcmd:
            _handle_mode(ui)
        elif subcmd == "rules":
            _handle_rules(ui, engine)
        else:
            ui.error(
                f"未知子命令：{subcmd}\n用法：/permission [mode | rules]"
            )

    return _handler


def _handle_mode(ui) -> None:
    """输出当前权限模式名。"""
    mode_map = {1: "DEFAULT", 2: "ACCEPT_EDITS", 3: "PLAN", 4: "BYPASS"}
    mode_val = ui.mode()
    mode_name = mode_map.get(int(mode_val) if hasattr(mode_val, '__int__') else mode_val, str(mode_val))
    ui.println(f"权限模式: {mode_name}")


def _handle_rules(ui, engine=None) -> None:
    """列出当前加载的权限规则。"""
    if engine is None:
        ui.println("权限引擎未初始化")
        return

    lines = ["## 权限规则"]
    for label, rule_set in [
        ("用户级 (user)", engine.user),
        ("项目级 (project)", engine.project),
        ("本地级 (local)", engine.local),
    ]:
        lines.append(f"\n### {label}")
        allow_rules = getattr(rule_set, 'allow', [])
        deny_rules = getattr(rule_set, 'deny', [])
        if allow_rules:
            lines.append("  Allow:")
            for r in allow_rules:
                pattern = f"({r.pattern})" if r.pattern else ""
                lines.append(f"    ✓ {r.tool}{pattern}")
        if deny_rules:
            lines.append("  Deny:")
            for r in deny_rules:
                pattern = f"({r.pattern})" if r.pattern else ""
                lines.append(f"    ✗ {r.tool}{pattern}")
        if not allow_rules and not deny_rules:
            lines.append("  (无)")

    ui.println("\n".join(lines))


# ── /session ──────────────────────────────────────────────────────────


def make_session_handler(
    session_writer=None,  # Writer (optional, for current session info)
    sessions_dir: str = "",  # sessions 目录路径
) -> Handler:
    """构造 /session handler，支持 list / 默认（当前会话）。"""

    async def _handler(ui, args: str = "") -> None:
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else ""

        if subcmd == "list":
            _handle_session_list(ui, sessions_dir)
        else:
            _handle_session_current(ui)

    return _handler


def _handle_session_current(ui) -> None:
    """输出当前会话 ID 与存档路径。"""
    ui.println(f"Session: {ui.session_id()}\nPath: {ui.session_path()}")


def _handle_session_list(ui, sessions_dir: str) -> None:
    """列出最近的会话存档。"""
    if not sessions_dir:
        ui.println("会话目录未配置")
        return

    try:
        from csycode.session.list import list_sessions

        sessions = list_sessions(sessions_dir)
        if not sessions:
            ui.println("（没有历史会话）")
            return

        current_id = ui.session_id()
        lines = ["## 历史会话"]
        for i, s in enumerate(sessions[:10]):
            marker = " ← 当前" if s.id == current_id else ""
            title = s.title or "(无标题)"
            if len(title) > 60:
                title = title[:57] + "..."
            lines.append(
                f"  [{i+1}] {s.id}  {title}{marker}"
            )
            if s.total_input_tokens > 0 or s.total_output_tokens > 0:
                lines.append(
                    f"      消息: {s.message_count}  |  "
                    f"Token: {s.total_input_tokens} in / {s.total_output_tokens} out  |  "
                    f"模型: {s.model or '?'}"
                )
            else:
                lines.append(
                    f"      消息: {s.message_count}  |  模型: {s.model or '?'}"
                )

        ui.println("\n".join(lines))
    except Exception as e:
        ui.error(f"无法列出会话: {e}")

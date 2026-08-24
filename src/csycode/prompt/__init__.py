"""系统提示装配、环境采集、补充消息构造与 ASCII art banner。

对外接口：
- build_system_prompt() → 稳定系统提示（可缓存）
- gather_environment() → 环境信息（不缓存）
- plan_reminder() / system_reminder() → 补充指令包裹
- render_banner() → 启动横幅
"""

from __future__ import annotations

from .modules import Module, fixed_modules, optional_modules

# ── 系统提示装配 ────────────────────────────────────────────────────────


def assemble_system(mods: list[Module]) -> str:
    """按 priority 升序、跳过空 content、以 ``\\n\\n`` 连接模块。

    Args:
        mods: 要装配的模块列表（顺序无关，内部按 priority 排序）。

    Returns:
        装配好的系统提示字符串，跨轮逐字节一致（N1）。
    """
    sorted_mods = sorted(mods, key=lambda m: m.priority)
    parts = [m.content for m in sorted_mods if m.content]
    return "\n\n".join(parts)


def build_system_prompt() -> str:
    """装配稳定系统提示：固定模块 + 可选空槽（当前为空，装配时跳过）。

    ch09: instructions 和 memory 通过 Conversation.inject_long_term_memory()
    作为 <system-reminder> 注入，保持 system prompt 稳定可缓存。
    """
    return assemble_system(fixed_modules() + optional_modules())


# ── 环境采集与补充消息（延迟导入避免循环依赖） ──────────────────────────

# 这些符号通过包顶层重导出，实际实现在子模块中。
# 延迟导入在函数首次调用时触发，不增加模块加载开销。


def gather_environment(version: str, model: str):
    """采集当前运行时环境信息（延迟导入）。"""
    from .environment import gather_environment as _impl

    return _impl(version, model)


def system_reminder(body: str) -> str:
    """用 ``<system-reminder>…</system-reminder>`` 包裹 body（延迟导入）。"""
    from .reminder import system_reminder as _impl

    return _impl(body)


def plan_reminder(full: bool) -> str:
    """返回包好标签的规划模式提醒（延迟导入）。"""
    from .reminder import plan_reminder as _impl

    return _impl(full)


# EXECUTE_DIRECTIVE 在 reminder.py 中定义，此处重导出
def _get_execute_directive() -> str:
    from .reminder import EXECUTE_DIRECTIVE

    return EXECUTE_DIRECTIVE


# 模块级懒加载属性
class _LazyDirective:
    """延迟加载 EXECUTE_DIRECTIVE，避免循环导入。"""

    _value: str | None = None

    def __str__(self) -> str:
        if self._value is None:
            from .reminder import EXECUTE_DIRECTIVE

            self._value = EXECUTE_DIRECTIVE
        return self._value

    def __repr__(self) -> str:
        return repr(str(self))


EXECUTE_DIRECTIVE = _LazyDirective()

# ── Banner ───────────────────────────────────────────────────────────────

CAT_BANNER: str = r"""
   /\_/\
  ( o.o )
   > ^ <
"""

READY_HINT: str = "已就绪，输入 /help 查看可用命令。"


def render_banner(
    version: str, cwd: str, tool_count: int = 0, readonly_count: int = 0
) -> str:
    """Render the startup banner with cat, version, working directory, commands, and shortcuts.

    Args:
        version: Application version string.
        cwd: Current working directory path.
        tool_count: Total number of registered tools.
        readonly_count: Number of read-only tools.

    Returns:
        A formatted banner string.
    """
    tool_line = (
        f"  工具 ({tool_count} 个, {readonly_count} 只读)" if tool_count > 0 else ""
    )
    return (
        f"{CAT_BANNER}\n"
        f"  csyCode v{version} — 终端 AI 编程助手\n"
        f"  {cwd}\n"
        f"{tool_line}\n"
        f"  输入 /help 查看可用命令\n"
        f"  Enter 发送 · Ctrl+J 换行 · Ctrl+C 中断 · Ctrl+D 退出\n"
    )

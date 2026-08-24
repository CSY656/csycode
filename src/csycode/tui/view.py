"""TUI rendering helpers — user blocks, assistant blocks, tool results, help, etc."""

from __future__ import annotations

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text


def user_block(text: str) -> Text:
    """Render a user message block for RichLog.

    Args:
        text: The user's input text.

    Returns:
        A styled Text renderable.
    """
    return Text(f"● {text}", style="bold")


def assistant_block(reply: str) -> Markdown:
    """Render the final assistant reply as markdown.

    Args:
        reply: The complete assistant response text.

    Returns:
        A Markdown renderable for RichLog.
    """
    return Markdown(reply)


def error_block(err: Exception) -> Text:
    """Render an error message block for RichLog.

    Args:
        err: The exception to display.

    Returns:
        A red-styled Text renderable.
    """
    return Text(f"● Error: {err}", style="bold red")


def streaming_text(cur_reply: str, elapsed: float) -> str:
    """Render the streaming indicator with elapsed time.

    Args:
        cur_reply: The accumulated reply text so far.
        elapsed: Elapsed seconds since the request was sent.

    Returns:
        A string to display in the streaming area.
    """
    content = cur_reply if cur_reply else ""
    return f"{content}\n⏳ 思考中… ({int(elapsed)}s)"


def streaming_done(elapsed: float) -> str:
    """Render the completion indicator.

    Args:
        elapsed: Total elapsed seconds.

    Returns:
        A completion string.
    """
    return f"✓ 完成 ({elapsed:.1f}s)"


# ── 工具调用状态渲染 ──────────────────────────────────────────────


def _truncate(value: object, max_len: int = 40) -> str:
    """截断过长的参数值显示。"""
    s = str(value)
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


def tool_call_block(name: str, args: dict) -> Text:
    """渲染工具调用的状态行。

    Args:
        name: 工具名称。
        args: 调用参数字典。

    Returns:
        黄色样式的工具调用标识文本，如 "🔧 read_file(config.py)"。
    """
    args_str = ", ".join(f"{k}={_truncate(v)}" for k, v in args.items())
    return Text(f"🔧 {name}({args_str})", style="bold yellow")


def tool_result_block(name: str, success: bool) -> Text:
    """渲染工具执行结果状态行。

    Args:
        name: 工具名称。
        success: 是否执行成功。

    Returns:
        成功时绿色 "  ✓ name 完成"，失败时红色 "  ✗ name 失败"。
    """
    if success:
        return Text(f"  ✓ {name} 完成", style="green")
    else:
        return Text(f"  ✗ {name} 失败", style="bold red")


def tool_result_panel(name: str, content: str) -> Panel:
    """Render tool result content in a Panel, truncated if too long.

    Similar to the reference project's rich Panel display for tool results.

    Args:
        name: 工具名称。
        content: 工具返回的内容文本。

    Returns:
        A Rich Panel renderable.
    """
    display = content
    if len(content) > 8000:
        display = content[:8000] + f"\n... (共 {len(content)} 字符，已截断)"
    return Panel(display, title=f"📋 {name} 结果", border_style="green")


def tool_error_block(name: str, error: str) -> Text:
    """Render tool execution error details.

    Args:
        name: 工具名称。
        error: 错误描述文本。

    Returns:
        红色错误详情文本。
    """
    return Text(f"  ✗ {name}: {error}", style="red")


# ── 轮次与模式提示 ──────────────────────────────────────────────


def turn_separator(turn: int) -> Text:
    """Render a turn separator line for multi-turn agent loops.

    Args:
        turn: 当前轮次编号。

    Returns:
        Dimmed separator text like "── 第 2 轮 ──".
    """
    return Text(f"── 第 {turn} 轮 ──", style="dim")


def plan_mode_banner(message: str) -> Text:
    """Render Plan/Do Mode switch tip.

    Args:
        message: 切换提示文本。

    Returns:
        蓝色加粗提示文本。
    """
    return Text(f"● {message}", style="bold blue")


# ── 人在回路待批准块渲染（ch06）─────────────────────────────────


def approval_block(req, cursor: int = 0) -> Text:
    """渲染人在回路待批准块。

    包含：
      ● 工具名(参数预览)
        触发原因（灰字）
        是否继续?
      1. 允许本次
      2. 永久允许（写入本地配置）
      3. 拒绝本次
      ↑↓ 选择 · 回车确认 · Esc 取消

    Args:
        req: ApprovalRequest 实例（含 name, args, reason）。
        cursor: 当前光标位置（0=允许本次, 1=永久允许, 2=拒绝本次）。

    Returns:
        富文本 Text 渲染件。
    """
    result = Text()
    # 标题行
    result.append(f"● {req.name}({req.args})", style="bold yellow")
    result.append("\n")
    # 触发原因
    result.append(f"  {req.reason}", style="dim")
    result.append("\n")
    # 提示
    result.append("  是否继续?", style="bold")
    result.append("\n\n")

    # 三选一菜单
    menu_items = [
        "1. 允许本次",
        "2. 永久允许（写入本地配置）",
        "3. 拒绝本次",
    ]
    for i, item in enumerate(menu_items):
        if i == cursor:
            result.append(f"  > {item}", style="bold cyan")
        else:
            result.append(f"    {item}", style="")
        result.append("\n")

    # 底部提示
    result.append("\n")
    result.append("  ↑↓ 选择 · 回车确认 · Esc 取消", style="dim")
    return result


# ── /help 内容构建 ──────────────────────────────────────────────


def help_content(
    readonly_tools: list[str],
    side_effect_tools: list[str],
) -> str:
    """Build the help text for the /help command.

    Args:
        readonly_tools: List of read-only tool names.
        side_effect_tools: List of side-effect tool names.

    Returns:
        A markdown string to display in RichLog.
    """
    lines = [
        "# 帮助",
        "",
        "## 命令",
        "",
        "| 命令 | 说明 |",
        "|------|------|",
        "| `/exit` | 退出程序 |",
        "| `/clear` | 清空对话历史 |",
        "| `/compact` | 手动压缩上下文（触发 LLM 摘要） |",
        "| `/plan` | 进入 Plan Mode（仅只读工具） |",
        "| `/do` | 退出 Plan Mode（恢复全部工具） |",
        "| `/help` | 显示本帮助 |",
        "",
        f"## 只读工具（{len(readonly_tools)}）",
        "",
        ", ".join(readonly_tools) if readonly_tools else "（无）",
        "",
        f"## 写入工具（{len(side_effect_tools)}）",
        "",
        ", ".join(side_effect_tools) if side_effect_tools else "（无）",
        "",
        "## 快捷键",
        "",
        "| 快捷键 | 说明 |",
        "|--------|------|",
        "| `Enter` | 发送消息 |",
        "| `Ctrl+J` | 插入换行（所有终端通用） |",
        "| `Shift+Enter` / `Ctrl+Enter` | 插入换行（需终端支持 Kitty 协议） |",
        "| `Ctrl+C` | 中断 Agent |",
        "| `Ctrl+D` | 退出程序 |",
        "| `Shift+Tab` / `Ctrl+P` | 切换 Plan / Do 模式 |",
        "| `Ctrl+↑` / `Ctrl+↓` | 输入历史导航 |",
        "| `Ctrl+L` | 清屏 |",
    ]
    return "\n".join(lines)


# ── ch10: 补全菜单 notice 块 ──────────────────────────────────────


def notice_block(msg: str) -> str:
    """将纯文本包装为蓝色提示块。"""
    return msg


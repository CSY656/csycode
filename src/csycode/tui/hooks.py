"""/hooks 命令 handler —— 列出已加载的 Hook 规则。

ch12: 按 event 分组展示，每条一行含 name / event / action.type / flags。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from csycode.hook.event import Event
from csycode.hook.rule import HookRule

if TYPE_CHECKING:
    from csycode.command.ui import UI


async def handle_hooks(ui: "UI", _args: str = "") -> None:
    """列出当前已加载的所有 hook 规则。

    输出按 event 分组，每条一行:
      <name>  <event>  <action.type>  [once] [async]
    末尾显示加载来源文件。
    """
    # csyCodeApp 作为 UI 实现，持有 hook_engine 属性
    hook_engine = getattr(ui, "hook_engine", None)

    if hook_engine is None:
        await ui.println("No hooks loaded.")
        return

    rules: list[HookRule] = hook_engine.rules
    sources: list[str] = hook_engine.sources

    if not rules:
        await ui.println("No hooks loaded.")
        return

    # 按 event 分组（保留原始加载顺序）
    grouped: dict[str, list[HookRule]] = {}
    for r in rules:
        key = r.event.value if isinstance(r.event, Event) else str(r.event)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(r)

    lines: list[str] = []
    for event_name in grouped:
        lines.append(f"[{event_name}]")
        for r in grouped[event_name]:
            flags = []
            if r.only_once:
                flags.append("once")
            if r.asyncio_mode:
                flags.append("async")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"  {r.name}  {r.event.value}  {r.action.type.value}{flag_str}")

    if sources:
        lines.append(f"\nLoaded from: {', '.join(sources)}")

    for line in lines:
        await ui.println(line)

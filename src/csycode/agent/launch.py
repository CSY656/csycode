"""Fork 启动公共函数 —— 对齐 mewcode agent_tool.py 中的 wiring。

供 tui/skill_fork.py 改造使用，统一走 SubAgent 底座的 Agent 构造与
run_to_completion 执行路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from csycode.conversation import Conversation
    from csycode.tools.registry import ToolRegistry
    from csycode.permission import Engine


@dataclass
class ForkLaunchOpts:
    """Fork 启动选项。"""
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    conv: "Conversation | None" = None  # 已装填的子对话
    system_prompt: str = ""
    provider: Any = None
    registry: "ToolRegistry | None" = None
    engine: "Engine | None" = None
    version: str = ""
    hook_engine: Any = None
    context_window: int = 200000


async def launch_fork(
    opts: ForkLaunchOpts,
    task: str = "",
) -> str:
    """启动一个 Fork 子 Agent 并同步等待返回最终文本。

    Args:
        opts: Fork 启动选项。
        task: 任务文本（若 conv 未装填则追加）。

    Returns:
        子 Agent 最后一条 assistant 文本。
    """
    from csycode.agent.loop import Agent
    from csycode.conversation import Conversation
    from csycode.config import AgentConfig

    conv = opts.conv if opts.conv is not None else Conversation()

    if opts.provider is None:
        raise ValueError("provider is required for fork launch")

    sub_agent = Agent(
        provider=opts.provider,
        tool_registry=opts.registry,
        conversation=conv,
        config=AgentConfig(max_iterations=25),
        version=opts.version,
        engine=opts.engine,
        context_window=opts.context_window,
        system_prompt=opts.system_prompt,
        max_turns=0,
        permission_mode=None,
        dont_ask=False,
        hook_engine=opts.hook_engine,
    )
    sub_agent.is_sub_agent = True

    return await sub_agent.run_to_completion(conv, task)

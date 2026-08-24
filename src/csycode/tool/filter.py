"""工具过滤多层防线 —— 对齐 mewcode agents/tool_filter.py。

定义所有子 Agent 工具过滤的常量与 apply_agent_tool_filter 函数。
按 spec F30 顺序执行五层过滤，确保子 Agent 的工具集受控。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── 全局禁用工具列表 ────────────────────────────────────────────
# 任何子 Agent（定义式、Fork）永远不能用的工具名。
# 对齐 mewcode ALL_AGENT_DISALLOWED_TOOLS，按 CsyCode 工具名命名。

ALL_AGENT_DISALLOWED_TOOLS: list[str] = [
    "Agent",
    "TaskOutput",
    "ExitPlanMode",
    "EnterPlanMode",
    "AskUserQuestion",
    "TaskStop",
    "Workflow",
    # ch15: 协作工具默认禁止子 Agent 使用，仅 teammate=True 时放行
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
]

# 自定义（user / project / plugin 来源）Agent 比内置 Agent 多禁用的工具。
# 本期与全局禁止列表一致（对齐 mewcode）。
CUSTOM_AGENT_DISALLOWED_TOOLS: list[str] = [
    "Agent",
    "TaskOutput",
    "ExitPlanMode",
    "EnterPlanMode",
    "AskUserQuestion",
    "TaskStop",
    "Workflow",
    # ch15: 同上
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
]

# 后台 Agent 工具白名单（对齐 mewcode ASYNC_AGENT_ALLOWED_TOOLS）。
# 不含 Agent / TaskList / TaskGet / TaskStop / SendMessage 等任何元工具。
ASYNC_AGENT_ALLOWED_TOOLS: list[str] = [
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "run_command",
    "load_skill",
    "install_skill",
]

# ── ch15: Team 协作工具 ──────────────────────────────────────────

# 队员额外可用的协作工具（主 Agent 不可见，需 teammate=True 才加入）
TEAMMATE_EXTRA_TOOLS: list[str] = [
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
]

# 所有 Team 管理工具（主 Agent 始终可见）
TEAM_MANAGEMENT_TOOLS: list[str] = [
    "TeamCreate",
    "TeamDelete",
]


# ── 辅助判断 ──────────────────────────────────────────────────────


def _is_mcp_or_skill(name: str) -> bool:
    """判断工具名是否属于 MCP 或 Skill 工具（按命名约定）。

    MCP 工具以 "mcp__" 起头，Skill 工具以 "skill__" 起头或
    由 load_skill 动态注册。
    """
    return name.startswith("mcp__") or name.startswith("skill__")


# ── FilterParams ──────────────────────────────────────────────────


@dataclass
class FilterParams:
    """工具过滤的输入参数。

    Attributes:
        all: registry 的全部工具名列表（按注册顺序）。
        source: 定义来源的整数值（对齐 subagent.Source 枚举）。
        background: 是否后台运行。
        allowed: Agent 定义 tools 白名单，空列表表示不收窄。
        disallowed: Agent 定义 disallowedTools 黑名单。
    """

    all: list[str]
    source: int = 0
    background: bool = False
    allowed: list[str] = field(default_factory=list)
    disallowed: list[str] = field(default_factory=list)
    teammate: bool = False  # ch15: 是否队员上下文


def apply_agent_tool_filter(params: FilterParams) -> list[str]:
    """按 spec F30 顺序应用五层工具过滤。

    过滤顺序：
    1. 起点 = 全部工具名
    2. 全局禁止：去掉 ALL_AGENT_DISALLOWED_TOOLS
    3. 自定义禁止：若非内置（source >= USER），再去掉 CUSTOM_AGENT_DISALLOWED_TOOLS
    4. 后台白名单：若后台，与 ASYNC_AGENT_ALLOWED_TOOLS + MCP/Skill 工具取交集
    5. 定义黑名单：去掉 disallowed
    6. 定义白名单：若 allowed 非空，取交集

    Args:
        params: FilterParams 实例。

    Returns:
        过滤后的工具名列表。
    """
    # 第 0 层：MCP / Skill 工具始终放行，先分离出来
    mcp_skill = [n for n in params.all if _is_mcp_or_skill(n)]
    names = [n for n in params.all if not _is_mcp_or_skill(n)]

    # 第 1 层：全局禁止
    disallowed_global = set(ALL_AGENT_DISALLOWED_TOOLS)
    names = [n for n in names if n not in disallowed_global]

    # 第 2 层：自定义 Agent 额外限制（source >= USER = 1）
    if params.source >= 1:
        disallowed_custom = set(CUSTOM_AGENT_DISALLOWED_TOOLS)
        names = [n for n in names if n not in disallowed_custom]

    # 第 3 层：后台白名单
    if params.background:
        bg_allowed = set(ASYNC_AGENT_ALLOWED_TOOLS)
        names = [n for n in names if n in bg_allowed]

    # 第 4 层：定义黑名单
    if params.disallowed:
        disallowed_set = set(params.disallowed)
        names = [n for n in names if n not in disallowed_set]

    # 第 5 层：定义白名单
    if params.allowed:
        allowed_set = set(params.allowed)
        names = [n for n in names if n in allowed_set]

    # ch15: 队员上下文 — 把协作工具加回允许列表
    if params.teammate:
        teammate_set = set(TEAMMATE_EXTRA_TOOLS)
        names = list(teammate_set) + names

    # 合并 MCP / Skill 工具（始终放行）
    result = mcp_skill + names
    return result

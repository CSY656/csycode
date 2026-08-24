"""队员专属工具白名单常量。

对齐 mewcode agents/tool_filter.py 的 IN_PROCESS_TEAMMATE_ALLOWED_TOOLS。
"""

from __future__ import annotations

# 队员额外可用的协作工具（主 Agent 不可见）
TEAMMATE_EXTRA_TOOLS: list[str] = [
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
]

# 所有 Team 相关工具（含 TeamCreate/TeamDelete，主 Agent 始终可见）
ALL_TEAM_TOOLS: list[str] = [
    "TeamCreate",
    "TeamDelete",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
]

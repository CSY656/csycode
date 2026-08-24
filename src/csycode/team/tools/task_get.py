"""TaskGet 工具 —— 查询任务详情。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult
from csycode.agent.team_hook import TEAMMATE_CTX_KEY

if TYPE_CHECKING:
    from csycode.team.manager import Manager


class TaskGetTool(Tool):
    """查询任务详情的工具。"""

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TaskGet"

    @property
    def description(self) -> str:
        return "查询指定任务的详细信息。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（如 task_a1b2c3）",
                },
            },
            "required": ["task_id"],
        }

    is_readonly: bool = True
    is_concurrency_safe: bool = True
    is_system_tool: bool = True

    async def _execute(self, **kwargs) -> ToolResult:
        task_id = str(kwargs.get("task_id", ""))

        if not task_id:
            return ToolResult(success=False, content="", error="task_id 不能为空")

        team_name = self._get_team_from_ctx(kwargs)
        if team_name is None:
            return ToolResult(success=False, content="", error="不在 Team 上下文中")

        store = self._mgr.get_task_store(team_name)
        if store is None:
            return ToolResult(success=False, content="", error=f"Team '{team_name}' 的任务存储不存在")

        task = await store.get(task_id)
        if task is None:
            return ToolResult(success=False, content="", error=f"任务 '{task_id}' 不存在")

        return ToolResult(
            success=True,
            content=json.dumps(task.to_dict(), ensure_ascii=False),
        )

    def _get_team_from_ctx(self, kwargs: dict) -> str | None:
        ctx = kwargs.get("_ctx", {})
        tc = ctx.get(TEAMMATE_CTX_KEY) if isinstance(ctx, dict) else None
        if tc:
            return tc.team_name
        teams = self._mgr.list_()
        if teams:
            return teams[0].sanitized_name
        return None

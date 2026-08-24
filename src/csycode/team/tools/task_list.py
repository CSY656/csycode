"""TaskList 工具 —— 列出 Team 任务。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult
from csycode.agent.team_hook import TEAMMATE_CTX_KEY
from csycode.team.tasks import Filter, Status

if TYPE_CHECKING:
    from csycode.team.manager import Manager


class TaskListTool(Tool):
    """列出 Team 任务的工具。"""

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TaskList"

    @property
    def description(self) -> str:
        return (
            "列出 Team 中的任务。可按状态过滤（pending/in_progress/completed/blocked）。"
            "每个任务附带 is_ready 字段表示其依赖是否已满足。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "按状态过滤: pending / in_progress / completed / blocked",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                },
            },
        }

    is_readonly: bool = True
    is_concurrency_safe: bool = True
    is_system_tool: bool = True

    async def _execute(self, **kwargs) -> ToolResult:
        status_str = str(kwargs.get("status", ""))

        team_name = self._get_team_from_ctx(kwargs)
        if team_name is None:
            return ToolResult(success=False, content="", error="不在 Team 上下文中")

        store = self._mgr.get_task_store(team_name)
        if store is None:
            return ToolResult(success=False, content="", error=f"Team '{team_name}' 的任务存储不存在")

        filter_ = None
        if status_str:
            try:
                filter_ = Filter(status=Status(status_str))
            except ValueError:
                return ToolResult(success=False, content="", error=f"无效状态: {status_str}")

        tasks = await store.list_(filter_)
        return ToolResult(
            success=True,
            content=json.dumps(tasks, ensure_ascii=False),
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

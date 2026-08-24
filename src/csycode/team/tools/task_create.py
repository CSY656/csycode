"""TaskCreate 工具 —— 在 Team 共享任务列表中创建任务。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult
from csycode.agent.team_hook import TEAMMATE_CTX_KEY

if TYPE_CHECKING:
    from csycode.team.manager import Manager


class TaskCreateTool(Tool):
    """创建共享任务的工具。"""

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TaskCreate"

    @property
    def description(self) -> str:
        return (
            "在 Team 共享任务列表中创建一个新任务。"
            "可以指定负责人、依赖关系（blocked_by）。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "任务标题",
                },
                "description": {
                    "type": "string",
                    "description": "任务描述（可选）",
                },
                "assignee": {
                    "type": "string",
                    "description": "负责人（队员名，可选）",
                },
                "blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "依赖的任务 ID 列表（可选）",
                },
            },
            "required": ["title"],
        }

    is_readonly: bool = False
    is_concurrency_safe: bool = False
    is_system_tool: bool = True

    async def _execute(self, **kwargs) -> ToolResult:
        title = str(kwargs.get("title", ""))
        description = str(kwargs.get("description", ""))
        assignee = str(kwargs.get("assignee", ""))
        blocked_by = kwargs.get("blocked_by", []) or []

        if not title:
            return ToolResult(success=False, content="", error="title 不能为空")

        # 从 ctx 中取当前 Team
        team_name = self._get_team_from_ctx(kwargs)
        if team_name is None:
            return ToolResult(success=False, content="", error="不在 Team 上下文中")

        store = self._mgr.get_task_store(team_name)
        if store is None:
            return ToolResult(success=False, content="", error=f"Team '{team_name}' 的任务存储不存在")

        try:
            task = await store.create(
                title=title,
                description=description,
                assignee=assignee,
                blocked_by=list(blocked_by),
                created_by=assignee or "lead",
            )
            return ToolResult(
                success=True,
                content=json.dumps(task.to_dict(), ensure_ascii=False),
            )
        except Exception as e:
            return ToolResult(success=False, content="", error=f"创建任务失败: {e}")

    def _get_team_from_ctx(self, kwargs: dict) -> str | None:
        """从执行上下文中提取 Team 名称。"""
        ctx = kwargs.get("_ctx", {})
        tc = ctx.get(TEAMMATE_CTX_KEY) if isinstance(ctx, dict) else None
        if tc:
            return tc.team_name
        # 主 Agent 调用：从活跃 Team 取
        teams = self._mgr.list_()
        if teams:
            return teams[0].sanitized_name
        return None

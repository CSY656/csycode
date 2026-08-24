"""TaskUpdate 工具 —— 更新任务状态和依赖关系。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult
from csycode.agent.team_hook import TEAMMATE_CTX_KEY
from csycode.team.tasks import Patch, Status

if TYPE_CHECKING:
    from csycode.team.manager import Manager


class TaskUpdateTool(Tool):
    """更新共享任务的工具。"""

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TaskUpdate"

    @property
    def description(self) -> str:
        return (
            "更新任务。支持修改标题、描述、状态、负责人、依赖关系。"
            "add_blocked_by 会自动维护双向依赖。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID",
                },
                "title": {
                    "type": "string",
                    "description": "新标题（可选）",
                },
                "description": {
                    "type": "string",
                    "description": "新描述（可选）",
                },
                "status": {
                    "type": "string",
                    "description": "新状态",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                },
                "assignee": {
                    "type": "string",
                    "description": "新负责人（可选）",
                },
                "add_blocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "添加「当前任务 block 这些任务」的关系",
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "添加「这些任务 block 当前任务」的依赖",
                },
                "remove_blocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "移除 blocks 关系",
                },
                "remove_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "移除 blocked_by 依赖",
                },
            },
            "required": ["task_id"],
        }

    is_readonly: bool = False
    is_concurrency_safe: bool = False
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

        # 构造 Patch
        patch = Patch()
        if "title" in kwargs:
            patch.title = str(kwargs["title"])
        if "description" in kwargs:
            patch.description = str(kwargs["description"])
        if "status" in kwargs:
            try:
                patch.status = Status(str(kwargs["status"]))
            except ValueError:
                return ToolResult(success=False, content="", error=f"无效状态: {kwargs['status']}")
        if "assignee" in kwargs:
            patch.assignee = str(kwargs["assignee"])
        if "add_blocks" in kwargs:
            patch.add_blocks = list(kwargs["add_blocks"] or [])
        if "add_blocked_by" in kwargs:
            patch.add_blocked_by = list(kwargs["add_blocked_by"] or [])
        if "remove_blocks" in kwargs:
            patch.remove_blocks = list(kwargs["remove_blocks"] or [])
        if "remove_blocked_by" in kwargs:
            patch.remove_blocked_by = list(kwargs["remove_blocked_by"] or [])

        try:
            ok = await store.update(task_id, patch)
            if not ok:
                return ToolResult(success=False, content="", error=f"任务 '{task_id}' 不存在")
            task = await store.get(task_id)
            return ToolResult(
                success=True,
                content=json.dumps(task.to_dict() if task else {}, ensure_ascii=False),
            )
        except Exception as e:
            return ToolResult(success=False, content="", error=f"更新任务失败: {e}")

    def _get_team_from_ctx(self, kwargs: dict) -> str | None:
        ctx = kwargs.get("_ctx", {})
        tc = ctx.get(TEAMMATE_CTX_KEY) if isinstance(ctx, dict) else None
        if tc:
            return tc.team_name
        teams = self._mgr.list_()
        if teams:
            return teams[0].sanitized_name
        return None

"""4 个后台任务工具 —— 对齐 mewcode tools/task_*.py。

TaskList / TaskGet / TaskStop / SendMessage。
主 Agent 通过这些工具查询和操控后台子 Agent。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from csycode.task.manager import Manager as TaskManager

log = logging.getLogger(__name__)


# ── TaskList ──────────────────────────────────────────────────────


class TaskListTool(Tool):
    """列出所有后台任务。"""

    def __init__(self, task_mgr: "TaskManager") -> None:
        self._mgr = task_mgr

    @property
    def name(self) -> str:
        return "TaskList"

    @property
    def description(self) -> str:
        return "列出当前所有后台子 Agent 任务，含 id、name、status、tool_count、last_activity。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    is_readonly: bool = True
    is_system_tool: bool = True

    async def _execute(self, **kwargs) -> ToolResult:
        tasks = self._mgr.list_all()
        items = [
            {
                "id": t.id,
                "name": t.name,
                "status": str(t.status),
                "tool_count": t.tool_count,
                "last_activity": t.last_activity,
            }
            for t in tasks
        ]
        return ToolResult(
            success=True,
            content=json.dumps(items, ensure_ascii=False, indent=2),
        )


# ── TaskGet ───────────────────────────────────────────────────────


class TaskGetTool(Tool):
    """获取指定任务详情。"""

    def __init__(self, task_mgr: "TaskManager") -> None:
        self._mgr = task_mgr

    @property
    def name(self) -> str:
        return "TaskGet"

    @property
    def description(self) -> str:
        return "获取指定后台任务的完整状态，含 result / status / tokens 等。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（来自 TaskList 或 Agent 工具返回值）",
                },
            },
            "required": ["task_id"],
        }

    is_readonly: bool = True
    is_system_tool: bool = True

    async def _execute(self, task_id: str = "", **kwargs) -> ToolResult:
        t = self._mgr.get(task_id)
        if t is None:
            return ToolResult(
                success=False,
                content="",
                error=f"未找到任务: {task_id}",
                error_type="not_found",
            )

        info = {
            "id": t.id,
            "name": t.name,
            "status": str(t.status),
            "task": t.task[:500] if t.task else "",
            "result": t.result[:5000] if t.result else "",
            "tool_count": t.tool_count,
            "last_activity": t.last_activity,
            "input_tokens": t.input_tokens,
            "output_tokens": t.output_tokens,
        }
        if t.err:
            info["error"] = str(t.err)

        return ToolResult(
            success=True,
            content=json.dumps(info, ensure_ascii=False, indent=2),
        )


# ── TaskStop ──────────────────────────────────────────────────────


class TaskStopTool(Tool):
    """取消运行中的任务。"""

    def __init__(self, task_mgr: "TaskManager") -> None:
        self._mgr = task_mgr

    @property
    def name(self) -> str:
        return "TaskStop"

    @property
    def description(self) -> str:
        return "取消一个正在运行的后台子 Agent 任务。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "要取消的任务 ID",
                },
            },
            "required": ["task_id"],
        }

    is_readonly: bool = False
    is_system_tool: bool = True

    async def _execute(self, task_id: str = "", **kwargs) -> ToolResult:
        ok = await self._mgr.stop(task_id)
        if not ok:
            return ToolResult(
                success=False,
                content="",
                error=f"无法取消任务 {task_id}（可能不存在或已终止）",
            )
        return ToolResult(
            success=True,
            content=json.dumps({"status": "cancellation_requested"}, ensure_ascii=False),
        )


# ── SendMessage ───────────────────────────────────────────────────


class SendMessageTool(Tool):
    """向已完成的 Agent 续派任务。"""

    def __init__(self, task_mgr: "TaskManager") -> None:
        self._mgr = task_mgr

    @property
    def name(self) -> str:
        return "SendMessage"

    @property
    def description(self) -> str:
        return (
            "向一个已完成的后台子 Agent 续派新任务。"
            "Agent 保留之前的对话上下文。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "目标 Agent 的 name（来自 Agent 工具的 name 参数）",
                },
                "message": {
                    "type": "string",
                    "description": "新任务文本",
                },
            },
            "required": ["name", "message"],
        }

    is_readonly: bool = False
    is_system_tool: bool = True

    async def _execute(self, name: str = "", message: str = "", **kwargs) -> ToolResult:
        try:
            task_id = await self._mgr.send_message(name, message)
            return ToolResult(
                success=True,
                content=json.dumps(
                    {"task_id": task_id, "status": "resumed"}, ensure_ascii=False
                ),
            )
        except ValueError as e:
            return ToolResult(
                success=False,
                content="",
                error=str(e),
            )

"""TeamCreate 工具 —— 创建新 Team。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from csycode.team.manager import Manager


class TeamCreateTool(Tool):
    """创建新 Team 的工具。

    主 Agent 调用此工具创建 Team，Leader 自动成为第一个成员。
    """

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TeamCreate"

    @property
    def description(self) -> str:
        return (
            "创建一个新的 Agent Team。创建后你将成为 Team Lead，"
            "可以用 Agent 工具向 Team 中添加队员。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "团队名称（会经 sanitize 处理为路径安全名称）",
                },
                "description": {
                    "type": "string",
                    "description": "团队描述（可选）",
                },
            },
            "required": ["team_name"],
        }

    is_readonly: bool = False
    is_concurrency_safe: bool = False
    is_system_tool: bool = True

    async def _execute(self, **kwargs) -> ToolResult:
        team_name = str(kwargs.get("team_name", ""))
        description = str(kwargs.get("description", ""))

        if not team_name:
            return ToolResult(success=False, content="", error="team_name 不能为空")

        try:
            team = await self._mgr.create(team_name, description)
            result = {
                "team_name": team.sanitized_name,
                "original_name": team.name,
                "backend": str(team.backend),
                "config_path": team.config_path,
            }
            return ToolResult(
                success=True,
                content=json.dumps(result, ensure_ascii=False),
            )
        except ValueError as e:
            return ToolResult(success=False, content="", error=str(e))
        except Exception as e:
            return ToolResult(success=False, content="", error=f"创建 Team 失败: {e}")

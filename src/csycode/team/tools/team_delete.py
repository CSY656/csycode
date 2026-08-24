"""TeamDelete 工具 —— 删除 Team。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult
from csycode.team.types import TeamNotFoundError, TeamHasActiveMembersError

if TYPE_CHECKING:
    from csycode.team.manager import Manager


class TeamDeleteTool(Tool):
    """删除 Team 的工具。"""

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TeamDelete"

    @property
    def description(self) -> str:
        return (
            "删除一个 Team。默认拒绝删除有活跃成员的 Team，"
            "设 force=true 可强制删除。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "要删除的团队名称",
                },
                "force": {
                    "type": "boolean",
                    "description": "是否强制删除（忽略活跃成员检查）",
                },
            },
            "required": ["team_name"],
        }

    is_readonly: bool = False
    is_concurrency_safe: bool = False
    is_system_tool: bool = True

    async def _execute(self, **kwargs) -> ToolResult:
        team_name = str(kwargs.get("team_name", ""))
        force = bool(kwargs.get("force", False))

        if not team_name:
            return ToolResult(success=False, content="", error="team_name 不能为空")

        try:
            await self._mgr.delete(team_name, force=force)
            return ToolResult(
                success=True,
                content=f"Team '{team_name}' 已删除",
            )
        except TeamNotFoundError as e:
            return ToolResult(success=False, content="", error=str(e))
        except TeamHasActiveMembersError as e:
            return ToolResult(success=False, content="", error=str(e))
        except Exception as e:
            return ToolResult(success=False, content="", error=f"删除 Team 失败: {e}")

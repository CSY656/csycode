"""SendMessage 工具 —— 向队员发送消息。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult
from csycode.agent.team_hook import TEAMMATE_CTX_KEY
from csycode.team.mailbox import create_message, MessageType
from csycode.team.types import BackendType

if TYPE_CHECKING:
    from csycode.team.manager import Manager


class SendMessageTool(Tool):
    """向队员发送消息的协作工具。"""

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "SendMessage"

    @property
    def description(self) -> str:
        return (
            "向 Team 中的队员发送消息。支持文本消息、Plan 审批、"
            "优雅关闭协商。to='*' 可广播给所有队员。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "接收者：队员名 / agent_id / '*' 广播",
                },
                "summary": {
                    "type": "string",
                    "description": "5-10 词的消息摘要",
                },
                "message": {
                    "type": "string",
                    "description": "消息正文",
                },
                "type": {
                    "type": "string",
                    "description": "消息类型",
                    "enum": ["text", "shutdown_request", "shutdown_response", "plan_approval_response"],
                },
                "payload": {
                    "type": "object",
                    "description": "结构化载荷（plan_approval_response 的 approve/feedback 等）",
                },
            },
            "required": ["to", "summary"],
        }

    is_readonly: bool = False
    is_concurrency_safe: bool = False
    is_system_tool: bool = True

    async def _execute(self, **kwargs) -> ToolResult:
        to = str(kwargs.get("to", ""))
        summary = str(kwargs.get("summary", ""))
        message = str(kwargs.get("message", ""))
        msg_type_str = str(kwargs.get("type", "text"))
        payload = kwargs.get("payload") or {}

        if not to:
            return ToolResult(success=False, content="", error="to 不能为空")

        # 解析消息类型
        try:
            msg_type = MessageType(msg_type_str)
        except ValueError:
            msg_type = MessageType.TEXT

        # 确定调用者所属 Team
        team = self._get_team_from_ctx(kwargs)
        if team is None:
            return ToolResult(success=False, content="", error="不在 Team 上下文中")

        # 校验消息权限
        from_agent = self._get_caller_agent_id(kwargs)

        if msg_type == MessageType.PLAN_APPROVAL_RESPONSE:
            if from_agent != team.lead_agent_id and from_agent != "lead":
                return ToolResult(
                    success=False, content="",
                    error="只有 Lead 可以发送 plan_approval_response",
                )

        # 解析目标
        if to == "*":
            # 广播：发给除发件人外的所有成员
            target_agent_ids = [
                m.agent_id for m in team.members
                if m.agent_id != from_agent
            ]
        else:
            # 按 name 或 agent_id 解析
            agent_id = self._mgr.registry.resolve(to)
            if agent_id is None:
                return ToolResult(
                    success=False, content="",
                    error=f"找不到目标: {to}",
                )
            target_agent_ids = [agent_id]

        if not target_agent_ids:
            return ToolResult(
                success=True,
                content=json.dumps({"delivered_to": [], "note": "没有目标接收者"}, ensure_ascii=False),
            )

        # 获取 mailbox
        mailbox = self._mgr.get_mailbox(team.sanitized_name)
        if mailbox is None:
            return ToolResult(success=False, content="", error=f"Team '{team.name}' 的邮箱不存在")

        delivered = []
        for aid in target_agent_ids:
            msg = create_message(
                from_agent=from_agent,
                to_agent=aid,
                content=message,
                summary=summary,
                message_type=msg_type,
                payload=payload if isinstance(payload, dict) else {},
            )
            await mailbox.write(aid, msg)
            delivered.append(aid)

            # Pane 后端：唤醒目标
            member = team.member_by_agent_id(aid)
            if member and member.backend_type != BackendType.IN_PROCESS and member.pane_id:
                try:
                    from csycode.team.backend import new_backend
                    bk = new_backend(member.backend_type, task_mgr=self._mgr.task_mgr)
                    await bk.wake(member.pane_id, aid)
                except Exception:
                    pass

            # in-process 后端且目标已 stop：触发续派
            if member and member.backend_type == BackendType.IN_PROCESS:
                if self._mgr.task_mgr:
                    bt = self._mgr.task_mgr.get(aid)
                    if bt and bt.status.value >= 1:  # COMPLETED/FAILED/CANCELLED
                        try:
                            # 恢复活跃状态
                            await self._mgr.set_member_active(team, member.name, True)
                            # 续派
                            await self._mgr.task_mgr.send_message(member.name, message)
                        except Exception:
                            pass

        import time
        return ToolResult(
            success=True,
            content=json.dumps({
                "delivered_to": delivered,
                "timestamp": time.time(),
            }, ensure_ascii=False),
        )

    def _get_team_from_ctx(self, kwargs: dict):
        """从上下文中获取 Team。"""
        ctx = kwargs.get("_ctx", {})
        tc = ctx.get(TEAMMATE_CTX_KEY) if isinstance(ctx, dict) else None
        if tc:
            return self._mgr.get(tc.team_name)
        # 主 Agent：取第一个活跃 Team
        teams = self._mgr.list_()
        return teams[0] if teams else None

    def _get_caller_agent_id(self, kwargs: dict) -> str:
        """获取调用者的 agent_id。"""
        ctx = kwargs.get("_ctx", {})
        tc = ctx.get(TEAMMATE_CTX_KEY) if isinstance(ctx, dict) else None
        if tc:
            return tc.agent_id
        return "lead"

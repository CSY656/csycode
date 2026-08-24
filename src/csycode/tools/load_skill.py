"""LoadSkill 工具 —— 允许 LLM 按需激活 Skill 的 SOP。

对齐 mewcode 的 tools/load_skill.py，是实现「意图识别自动触发」的关键：
Agent 在对话中判断用户意图匹配某个 Skill 的描述时，
调用 LoadSkill({name: "..."}) 把该 Skill 的完整 SOP 钉到 env context。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from csycode.agent.loop import Agent
    from csycode.skills.loader import SkillLoader


class LoadSkill(Tool):
    """按需加载并激活一个 Skill。

    系统工具（is_system_tool=True），不受 Skill 白名单过滤影响。
    read_only=True —— 只修改 Agent 内存状态，不写盘不连网。
    """

    def __init__(self) -> None:
        self._loader: "SkillLoader | None" = None
        self._agent: "Agent | None" = None

    # ── 注入器 ──────────────────────────────────────────────────

    def set_loader(self, loader: "SkillLoader") -> None:
        """注入 SkillLoader 实例。"""
        self._loader = loader

    def set_agent(self, agent: "Agent") -> None:
        """注入 Agent 实例。"""
        self._agent = agent

    # ── Tool 协议 ───────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "LoadSkill"

    @property
    def description(self) -> str:
        return (
            "加载并激活一个 Skill，将其完整 SOP 绑定到环境上下文。"
            "当用户请求匹配某个 Skill 的描述时调用此工具。"
            "参数: name (str) —— 要加载的 Skill 名称。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要激活的 Skill 名称",
                },
            },
            "required": ["name"],
        }

    is_readonly: bool = True
    is_system_tool: bool = True
    show_result_to_user: bool = True
    """LoadSkill 的结果（SOP body）需要返回给 LLM，故设为 True（对齐 mewcode）。"""

    # ── 执行 ────────────────────────────────────────────────────

    async def _execute(self, **kwargs) -> ToolResult:
        """执行 LoadSkill 工具。

        Args:
            name: Skill 名称。

        Returns:
            ToolResult: 成功时含确认消息，失败时含错误信息。
        """
        name = kwargs.get("name", "")

        if self._loader is None or self._agent is None:
            return ToolResult(
                success=False,
                content="",
                error="LoadSkill 未正确初始化（缺少 loader 或 agent）",
                error_type="config_error",
            )

        skill = self._loader.get(name)
        if skill is None:
            # 列出可用 Skill 帮助 LLM 纠正
            available = ", ".join(
                n for n, _ in self._loader.get_catalog()
            )
            return ToolResult(
                success=False,
                content="",
                error=(
                    f"未知 Skill: '{name}'。"
                    f"可用 Skill: {available}"
                ),
                error_type="not_found",
            )

        # 激活 Skill —— 把 SOP 钉到 env context
        self._agent.activate_skill(skill.name, skill.prompt_body)

        # 返回完整 SOP body，对齐 mewcode（让 LLM 看到 SOP 内容）
        header = f"# Skill: {skill.name}\n\n"
        return ToolResult(
            success=True,
            content=header + skill.prompt_body,
        )

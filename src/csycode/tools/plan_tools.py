"""Plan Mode 专用工具：AskUserQuestion, ExitPlanMode。

AskUserQuestion — 允许 LLM 在计划阶段向用户提问，澄清需求
ExitPlanMode  — 写入计划文件，退出计划模式，恢复全部工具
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from .base import Tool, ToolResult

# question_handler 类型: (question, options, multi_select) -> answer
QuestionHandler = Callable[[str, list[str] | None, bool], Awaitable[str]]


class AskUserQuestion(Tool):
    """向用户提问，获取澄清或确认。

    Plan Mode 下模型需要理解需求、确认方案，但无法和用户对话。
    此工具让模型可以问用户问题，用户回答后，答案作为工具结果返回给模型。

    TUI 集成:
        通过 set_question_handler() 注入异步回调，替代同步 input()。
        TUI 回调内部用 asyncio.Future 桥接，不会阻塞事件循环。
    """

    is_readonly: bool = True
    allowed_in_plan_mode: bool = True
    timeout: float = 120.0  # 等待用户输入可较长

    def __init__(self) -> None:
        super().__init__()
        self._question_handler: QuestionHandler | None = None

    def set_question_handler(self, handler: QuestionHandler) -> None:
        """注入异步提问回调（由 TUI 调用）。

        Args:
            handler: async callable(question, options, multi_select) -> answer_str。
                     由 TUI 实现：显示问题 → 等待用户输入 → 返回答案。
        """
        self._question_handler = handler

    @property
    def name(self) -> str:
        return "ask_user_question"

    @property
    def description(self) -> str:
        return (
            "向用户提问以澄清需求或确认方案。"
            "当你不确定用户想要什么，或者需要在多个方案中选择时使用。"
            "可以提供一个或多个选项让用户选择，也可以让用户自由回答。"
            "用户的选择或回答会作为工具结果返回给你。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要问用户的问题，应清晰具体",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选的选项列表，用户从中选择一个。如果不提供，用户自由回答。",
                },
                "multi_select": {
                    "type": "boolean",
                    "description": "是否允许多选（仅当提供了 options 时有效），默认 false",
                },
            },
            "required": ["question"],
        }

    async def _execute(
        self,
        question: str,
        options: list[str] | None = None,
        multi_select: bool = False,
    ) -> ToolResult:
        try:
            if self._question_handler is not None:
                # ── 异步路径：通过 TUI 回调获取输入，不阻塞事件循环 ──
                try:
                    answer = await self._question_handler(
                        question, options, multi_select
                    )
                except asyncio.CancelledError:
                    return ToolResult(
                        success=True,
                        content="用户未提供回答（超时或取消）。",
                    )

                if not answer:
                    return ToolResult(
                        success=True,
                        content="用户未提供回答（跳过此问题）。",
                    )

                return ToolResult(
                    success=True,
                    content=f"用户回答: {answer}",
                )

            # ── 同步回退路径：用于非 TUI 环境（测试 / 命令行直接调用） ──
            return await self._execute_sync_fallback(question, options, multi_select)

        except Exception as e:
            return ToolResult(
                success=False,
                content="",
                error=f"提问异常: {type(e).__name__}: {e}",
                error_type="exec_error",
            )

    async def _execute_sync_fallback(
        self,
        question: str,
        options: list[str] | None,
        multi_select: bool,
    ) -> ToolResult:
        """同步回退：使用 input() 直接读取 stdin（非 TUI 环境用）。"""
        lines = [
            "",
            "─" * 50,
            f"❓ {question}",
            "",
        ]

        if options:
            for i, opt in enumerate(options, 1):
                lines.append(f"  [{i}] {opt}")
            lines.append("")
            if multi_select:
                lines.append("输入选项编号（多选用逗号分隔，如 1,3）：")
            else:
                lines.append("输入选项编号：")
        else:
            lines.append("输入你的回答：")

        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()

        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""

        if not answer:
            return ToolResult(
                success=True,
                content="用户未提供回答（跳过此问题）。",
            )

        # 如果提供了选项，尝试解析
        if options:
            try:
                if multi_select:
                    indices = [int(x.strip()) - 1 for x in answer.split(",")]
                    selected = [options[i] for i in indices if 0 <= i < len(options)]
                    if selected:
                        answer = "、".join(selected)
                    else:
                        answer = f"无效选择: {answer}"
                else:
                    idx = int(answer.strip()) - 1
                    if 0 <= idx < len(options):
                        answer = options[idx]
                    else:
                        answer = f"无效选择: {answer}"
            except (ValueError, IndexError):
                pass

        return ToolResult(
            success=True,
            content=f"用户回答: {answer}",
        )


class ExitPlanMode(Tool):
    """写入计划文件并退出计划模式。

    在 Plan Mode 中调研完毕后，调用此工具：
    1. 将计划写入 .csycode/plans/ 目录下的 markdown 文件
    2. 退出计划模式，恢复全部工具
    3. Agent 循环停止，等待用户审阅计划后继续
    """

    is_readonly: bool = False  # 会写入文件
    allowed_in_plan_mode: bool = True  # Plan Mode 下唯一允许写入的工具
    timeout: float = 10.0

    @property
    def name(self) -> str:
        return "exit_plan_mode"

    @property
    def description(self) -> str:
        return (
            "结束计划模式：将你的实施计划写入 markdown 文件，并退出计划模式恢复全部工具。"
            "调用此工具后，Agent 会停止，等待用户审阅计划。"
            "用户确认后可以用全部工具（write_file、edit_file、run_command 等）执行计划。"
            "plan_content 应该是完整的实施计划，包含："
            "- 方案概述\n- 需要修改的文件列表\n- 具体步骤\n- 注意事项"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "plan_content": {
                    "type": "string",
                    "description": (
                        "完整的实施计划（markdown 格式）。应包含方案概述、"
                        "文件修改列表、具体步骤、注意事项等。"
                    ),
                },
            },
            "required": ["plan_content"],
        }

    async def _execute(self, plan_content: str) -> ToolResult:
        try:
            # 计划文件放在 .csycode/plans/ 目录
            plans_dir = Path.cwd() / ".csycode" / "plans"
            plans_dir.mkdir(parents=True, exist_ok=True)

            # 用时间戳命名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            plan_path = plans_dir / f"plan_{timestamp}.md"

            # 写入计划文件
            plan_path.write_text(plan_content, encoding="utf-8")

            return ToolResult(
                success=True,
                content=(
                    f"计划已写入: {plan_path}\n\n"
                    f"--- 计划内容预览（前 500 字符）---\n"
                    f"{plan_content[:500]}\n"
                    f"--- 计划结束 ---\n\n"
                    f"已退出 Plan Mode，Agent 将停止。"
                    f"请审阅计划文件，确认后可继续执行。"
                ),
                exit_plan_mode=True,  # 关键：通知 AgentLoop 退出计划模式
            )

        except PermissionError as e:
            return ToolResult(
                success=False,
                content="",
                error=f"权限不足，无法写入计划文件: {e}",
                error_type="security",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                content="",
                error=f"写入计划文件异常: {type(e).__name__}: {e}",
                error_type="exec_error",
            )

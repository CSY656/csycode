"""工具系统的基类与核心数据类型。

定义 ToolResult（工具执行结果）和 Tool（工具抽象基类）。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolResult:
    """工具执行的返回值，无论成功或失败都用此结构。

    Attributes:
        success: 工具是否成功执行。
        content: 成功时为工具输出文本，失败时为错误描述。
        error: 失败时的具体错误信息。
        error_type: 错误分类标签，如 "timeout"、"security"、"not_found" 等。
        exit_plan_mode: ExitPlanMode 工具调用后设为 True，通知 AgentLoop 退出计划模式。
        blocked_by_plan_mode: 工具被 Plan Mode 拦截时设为 True。
    """

    success: bool
    content: str
    error: str | None = None
    error_type: str | None = None
    exit_plan_mode: bool = False
    blocked_by_plan_mode: bool = False


class Tool(ABC):
    """工具的抽象基类。

    子类只需实现 name、description、parameters 属性和 _execute() 方法。
    基类在 execute() 中自动提供超时控制和异常捕获。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称，用作注册中心和 LLM tool_use 的标识。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具的自然语言描述，随工具定义发给 LLM。"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """工具的输入参数 JSON Schema。

        格式示例:
        {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
            },
            "required": ["file_path"],
        }
        """
        ...

    is_readonly: bool = False
    """是否为只读工具。只读工具不会修改文件系统或执行命令，默认 False。"""

    is_concurrency_safe: bool = False
    """是否为并发安全工具。并发安全的工具可与其他工具并发执行。
    只读工具通常是并发安全的。默认 False。
    """

    is_system_tool: bool = False
    """是否为系统工具。

    系统工具在 Skill 执行时的工具白名单过滤中自动透传，
    确保 LoadSkill 等核心工具在 skill 执行期仍可用以支持嵌套调用。
    默认 False。
    """

    should_defer: bool = False
    """是否延迟加载（deferred tool）。

    deferred 工具的 schema 默认不随 tools 列表发给 LLM，
    需通过 ToolSearch 工具发现后才纳入。用于减少初始 tool schema token 消耗。
    默认 False。
    """

    allowed_in_plan_mode: bool = False
    """是否在 Plan Mode 下也允许使用此工具。

    默认 False。AskUserQuestion 和 ExitPlanMode 等 Plan Mode 专用工具应设为 True。
    Plan Mode 下：is_readonly 或 allowed_in_plan_mode 为 True 的工具才可用。
    """

    timeout: float = 10.0
    """工具执行的超时秒数，子类可在类定义中覆盖，或由工厂在实例上覆盖。"""

    show_result_to_user: bool = True
    """工具执行结果是否展示给用户。

    默认 True。对于内部信息收集工具（如 read_file、glob、grep），
    设为 False 以隐藏原始文件内容和搜索结果，只向用户展示成功/失败状态行。
    注意：content 仍然会传给 LLM，只影响 TUI 展示。
    """

    async def execute(self, **kwargs) -> ToolResult:
        """执行工具，自动包装超时和异常捕获。

        Returns:
            ToolResult: 执行结果。超时和异常不会抛出，而是包成 ToolResult 返回。
        """
        try:
            return await asyncio.wait_for(
                self._execute(**kwargs),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                content="",
                error=f"工具 '{self.name}' 执行超时（{self.timeout} 秒）",
                error_type="timeout",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                content="",
                error=f"工具 '{self.name}' 执行异常: {e}",
                error_type="exec_error",
            )

    @abstractmethod
    async def _execute(self, **kwargs) -> ToolResult:
        """子类实现具体的工具逻辑。参数由子类自行声明和解析。"""
        ...

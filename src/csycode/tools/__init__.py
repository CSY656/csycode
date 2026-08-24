"""工具系统包。

提供 Tool 基类、ToolResult、八个核心工具实现、注册中心和路径沙箱。
通过 create_default_registry() 可快速创建包含所有默认工具的注册中心。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.config import ToolConfig

from .base import Tool, ToolResult
from .command_tool import RunCommandTool
from .file_state_cache import FileStateCache
from .file_tools import EditFileTool, ReadFileTool, WriteFileTool
from .install_skill import InstallSkillTool
from .plan_tools import AskUserQuestion, ExitPlanMode
from .registry import ToolRegistry
from .sandbox import PathValidator, SecurityViolation
from .search_tools import GlobTool, GrepTool

__all__ = [
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "PathValidator",
    "SecurityViolation",
    "FileStateCache",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "GlobTool",
    "GrepTool",
    "RunCommandTool",
    "AskUserQuestion",
    "ExitPlanMode",
    "InstallSkillTool",
    "create_default_registry",
]

# 默认工具超时值
_DEFAULT_TIMEOUTS: dict[str, float] = {
    "read_file": 10.0,
    "write_file": 10.0,
    "edit_file": 10.0,
    "run_command": 120.0,
    "glob": 30.0,
    "grep": 60.0,
    "ask_user_question": 120.0,  # 等待用户回答可较长
    "exit_plan_mode": 10.0,
}


def _create_tool(
    tool_cls: type[Tool],
    tool_config: "ToolConfig | None",
    default_timeout: float,
    project_root: str | None = None,
) -> Tool:
    """创建工具实例并应用配置中的超时值（若有）。"""
    # 对支持 project_root 的工具类传入项目根目录
    import inspect
    if "project_root" in inspect.signature(tool_cls).parameters:
        tool = tool_cls(project_root=project_root)
    else:
        tool = tool_cls()
    if tool_config and tool.name in tool_config.timeouts:
        tool.timeout = float(tool_config.timeouts[tool.name])
    return tool


def create_default_registry(
    tool_config: "ToolConfig | None" = None,
    project_root: str | None = None,
) -> ToolRegistry:
    """创建包含全部八个核心工具的注册中心。

    Args:
        tool_config: 可选的工具配置，用于覆盖默认超时值。
                     若为 None 则使用默认值。
        project_root: 项目根目录，用于文件工具沙箱校验。
                      若为 None 则使用当前工作目录。

    Returns:
        注册了八个核心工具（read_file、write_file、edit_file、
        glob、grep、run_command、ask_user_question、exit_plan_mode）
        的 ToolRegistry 实例。
    """
    registry = ToolRegistry()

    # 八个核心工具——工具名与超时 key 一致
    tool_classes: list[tuple[type[Tool], float]] = [
        (ReadFileTool, _DEFAULT_TIMEOUTS["read_file"]),
        (WriteFileTool, _DEFAULT_TIMEOUTS["write_file"]),
        (EditFileTool, _DEFAULT_TIMEOUTS["edit_file"]),
        (GlobTool, _DEFAULT_TIMEOUTS["glob"]),
        (GrepTool, _DEFAULT_TIMEOUTS["grep"]),
        (RunCommandTool, _DEFAULT_TIMEOUTS["run_command"]),
        # Plan Mode 专用工具
        (AskUserQuestion, _DEFAULT_TIMEOUTS["ask_user_question"]),
        (ExitPlanMode, _DEFAULT_TIMEOUTS["exit_plan_mode"]),
    ]

    for tool_cls, default_timeout in tool_classes:
        tool = _create_tool(tool_cls, tool_config, default_timeout, project_root)
        registry.register(tool)

    return registry

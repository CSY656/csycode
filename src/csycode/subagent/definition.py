"""SubAgent 角色定义与来源枚举 —— 对齐 mewcode agents/parser.py 的 AgentDef。

Definition 是一个 dataclass，承载从 Markdown+YAML frontmatter 解析出的
Agent 角色所有字段。每个字段对应 ch13 spec F4 的 frontmatter 项。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path


class Source(IntEnum):
    """定义来源，数字越大优先级越高。"""

    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3  # 占位，本期不实现

    def __str__(self) -> str:
        _map = {0: "builtin", 1: "user", 2: "project", 3: "plugin"}
        return _map.get(int(self), "unknown")


@dataclass
class Definition:
    """一个 Agent 角色的完整定义，从 Markdown+YAML frontmatter 解析。

    Attributes:
        name: 角色名（frontmatter.name），小写字母/数字/连字符，长度 1-32。
        description: 一句话描述（frontmatter.description），
                     用于 Agent 工具的 subagent_type 文档与 UI 列表。
        tools: 工具白名单（frontmatter.tools），空列表表示不收窄。
        disallowed_tools: 工具黑名单（frontmatter.disallowedTools）。
        model: 模型覆盖，inherit / haiku / sonnet / opus。
        max_turns: 最大迭代轮数，0 表示沿用全局默认。
        permission_mode: 权限模式字符串，如 "default" / "acceptEdits" /
                         "dontAsk" / "bypassPermissions"。
        dont_ask: 是否启用 "绕过 Ask" 的子 Agent 兜底模式。
                  当 permission_mode 为 "dontAsk" 时设为 True。
        background: 强制后台运行，缺省 False。
        system_prompt: Markdown body（去 frontmatter 后的全文）。
        file_path: 定义文件绝对路径（用于调试和热重载）。
        source: 来源层级。
    """

    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: str = "inherit"
    max_turns: int = 0
    permission_mode: str = "default"
    dont_ask: bool = False
    background: bool = False
    isolation: str = ""  # "" | "worktree"
    system_prompt: str = ""
    file_path: Path | None = None
    source: Source = Source.BUILTIN

    def is_fork(self) -> bool:
        """是否为 Fork 路径的临时 Definition。"""
        return self.name == "__fork__"

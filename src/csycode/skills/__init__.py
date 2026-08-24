"""Skill 系统包 —— 可复用 SOP 的加载、解析、执行与管理。

提供 SkillDef 数据结构、两级目录扫描、热重载、inline/fork 执行模式、
工具白名单过滤、$ARGUMENTS 渲染、远程安装等核心能力。
"""

from __future__ import annotations

from .parser import SkillDef, SkillParseError, parse_skill_file, substitute_arguments
from .loader import PROJECT_SKILLS_DIR, USER_SKILLS_DIR, SkillLoader
from .executor import SkillDependencyError, SkillExecutor, filter_tool_registry
from .install import (
    InstallReport,
    SkillSource,
    install_skill,
    parse_skill_url,
    user_skills_root,
)

__all__ = [
    "InstallReport",
    "SkillDef",
    "SkillDependencyError",
    "SkillExecutor",
    "SkillLoader",
    "SkillParseError",
    "SkillSource",
    "PROJECT_SKILLS_DIR",
    "USER_SKILLS_DIR",
    "filter_tool_registry",
    "install_skill",
    "parse_skill_file",
    "parse_skill_url",
    "substitute_arguments",
    "user_skills_root",
]

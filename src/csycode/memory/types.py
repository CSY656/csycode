"""记忆子系统的数据类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NoteType(str, Enum):
    """记忆笔记的四种类型。"""

    USER = "user"          # 用户角色、偏好、知识背景
    FEEDBACK = "feedback"  # 用户的纠正和确认
    PROJECT = "project"    # 项目知识、决策、进展
    REFERENCE = "reference"  # 外部资源链接


# user/feedback → 用户级（~/.csycode/memory/）
# project/reference → 项目级（.csycode/memory/）
_USER_LEVEL_TYPES = {NoteType.USER, NoteType.FEEDBACK}
_PROJECT_LEVEL_TYPES = {NoteType.PROJECT, NoteType.REFERENCE}


def is_user_level(t: NoteType) -> bool:
    return t in _USER_LEVEL_TYPES


def is_project_level(t: NoteType) -> bool:
    return t in _PROJECT_LEVEL_TYPES


@dataclass
class Note:
    """一条记忆笔记的完整内容。"""

    name: str           # 短横线命名 slug
    description: str    # 一行描述
    type: NoteType
    content: str        # 正文内容
    created: str = ""   # ISO 时间戳
    updated: str = ""   # ISO 时间戳


@dataclass
class UpdateAction:
    """一次记忆更新操作。"""

    action: str         # "create" | "update" | "delete"
    level: str          # "project" | "user"
    note: Note | None = None  # create/update 时必填，delete 时 name 字段用于定位

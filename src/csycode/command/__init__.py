"""csycode 命令系统包 —— 类型定义、Registry、dispatch、UI Protocol、内置命令。"""

from .command import Command, Handler, Kind
from .dispatch import parse
from .registry import Registry
from .ui import NopUI, UI
from .builtins import register_builtins

__all__ = [
    "Command",
    "Handler",
    "Kind",
    "NopUI",
    "Registry",
    "UI",
    "parse",
    "register_builtins",
]

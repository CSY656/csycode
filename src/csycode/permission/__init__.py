"""权限系统模块 —— 五层防御体系的类型定义与公共接口。

五层防御：
  1. 黑名单（不可配）：危险命令正则拦截
  2. 沙箱：文件路径围栏（项目根 + 符号链接解析）
  3. 规则引擎：三级规则匹配（user/project/local）
  4. 模式兜底矩阵：Mode x Category → Allow/Ask
  5. 人在回路：ApprovalRequest 事件 → 用户三选一

对外暴露:
  - 枚举: Mode / Decision / Category / Outcome
  - 引擎: Engine / new_engine
  - 规则写入: persist_local_allow
  - 异常: ApprovalError
"""

from __future__ import annotations

from enum import IntEnum


class Mode(IntEnum):
    """权限模式（四档统一轴）。

    按宽松程度递增:
      DEFAULT       — 只读 Allow / 文件写 Ask / 命令执行 Ask
      ACCEPT_EDITS  — 文件写 Allow / 命令执行 Ask
      PLAN          — 仅只读工具可见；矩阵同 DEFAULT 作防御兜底
      BYPASS        — 全 Allow（黑名单/沙箱仍拦）
    """

    DEFAULT = 0
    ACCEPT_EDITS = 1
    PLAN = 2
    BYPASS = 3

    def __str__(self) -> str:
        """返回 Claude Code 兼容的模式名字符串。"""
        _map = {
            Mode.DEFAULT: "default",
            Mode.ACCEPT_EDITS: "acceptEdits",
            Mode.PLAN: "plan",
            Mode.BYPASS: "bypassPermissions",
        }
        return _map[self]


def parse_mode(s: str) -> tuple[Mode, bool]:
    """大小写不敏感识别四档模式名。

    Args:
        s: 模式名字符串，如 "default"、"acceptEdits"、"plan"、"bypassPermissions"。

    Returns:
        (Mode, 是否成功识别)。未知名称返回 (Mode.DEFAULT, False)。
    """
    lower = s.strip().lower()
    _map: dict[str, Mode] = {
        "default": Mode.DEFAULT,
        "acceptedits": Mode.ACCEPT_EDITS,
        "plan": Mode.PLAN,
        "bypasspermissions": Mode.BYPASS,
        # 兼容中文/下划线变体
        "accept_edits": Mode.ACCEPT_EDITS,
        "bypass_permissions": Mode.BYPASS,
        "bypass": Mode.BYPASS,
    }
    if lower in _map:
        return (_map[lower], True)
    return (Mode.DEFAULT, False)


class Decision(IntEnum):
    """权限判定结果。"""

    ALLOW = 0  # 放行，允许执行
    DENY = 1  # 拒绝，不执行
    ASK = 2  # 需确认（信号，触发第五层人在回路）


class Category(IntEnum):
    """工具操作的类别（用于模式兜底矩阵）。"""

    READ = 0  # 只读（read_file / glob / grep）
    WRITE = 1  # 文件写入（write_file / edit_file）
    EXEC = 2  # 命令执行（bash 及未知工具）


class Outcome(IntEnum):
    """人在回路三选一结果。"""

    DENY_ONCE = 0  # 拒绝本次
    ALLOW_ONCE = 1  # 允许本次（+会话级缓存，同命令不再弹窗）
    ALLOW_FOREVER = 2  # 永久允许（+会话级缓存 +写本地层文件）


class ApprovalError(Exception):
    """权限批准相关异常（如人在回路超时、Future 已失效等）。"""

    pass


# 延迟导入，避免循环依赖。Engine、new_engine、persist_local_allow
# 在子模块中定义，由 __init__.py 重导出。
def __getattr__(name: str):
    """延迟导入子模块中的类/函数，避免循环依赖。"""
    if name == "Engine":
        from .engine import Engine

        return Engine
    if name == "new_engine":
        from .engine import new_engine

        return new_engine
    if name == "persist_local_allow":
        from .persist import persist_local_allow

        return persist_local_allow
    if name == "Rule":
        from .rule import Rule

        return Rule
    if name == "RuleSet":
        from .rule import RuleSet

        return RuleSet
    if name == "Settings":
        from .settings import Settings

        return Settings
    if name == "friendly_name":
        from .settings import friendly_name

        return friendly_name
    if name == "categorize":
        from .settings import categorize

        return categorize
    if name == "extract_target":
        from .settings import extract_target

        return extract_target
    raise AttributeError(f"module 'csycode.permission' has no attribute {name!r}")


__all__ = [
    # 枚举
    "Mode",
    "Decision",
    "Category",
    "Outcome",
    # 引擎
    "Engine",
    "new_engine",
    # 规则
    "Rule",
    "RuleSet",
    # 配置
    "Settings",
    # 工具函数
    "parse_mode",
    "friendly_name",
    "categorize",
    "extract_target",
    "persist_local_allow",
    # 异常
    "ApprovalError",
]

"""权限配置加载与工具调用解析。

提供:
  - Settings / PermissionsBlock 数据类（YAML 文件结构，F4）
  - load_settings: 加载 YAML 配置文件
  - to_rule_set: 将 Settings 转为 RuleSet 用于引擎
  - friendly_name: 内部名 → 友好名映射
  - categorize: 内部名 + read_only → Category 判定
  - extract_target: 从 ToolCall 中提取目标字符串供沙箱/规则匹配
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from csycode.llm import ToolCall
    from . import Category, RuleSet


class SettingsError(Exception):
    """配置文件格式错误（非致命，调用方降级即可）。"""

    pass


@dataclass
class PermissionsBlock:
    """单层配置中的权限规则块。"""

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class Settings:
    """单个 YAML 文件的权限配置结构。"""

    default_mode: str = ""
    permissions: PermissionsBlock = field(default_factory=PermissionsBlock)


# ── 配置加载 ────────────────────────────────────────────────────────────


def load_settings(path: str) -> Settings:
    """从 YAML 文件加载权限配置。

    Args:
        path: YAML 文件路径。

    Returns:
        Settings 实例。文件不存在 → 空 Settings；解析失败 → 抛 SettingsError。
    """
    p = Path(path)
    if not p.is_file():
        return Settings()

    try:
        raw = p.read_text(encoding="utf-8")
    except (OSError, PermissionError):
        return Settings()

    if not raw.strip():
        return Settings()

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise SettingsError(f"YAML 解析失败 ({path}): {e}") from e

    if data is None:
        return Settings()

    if not isinstance(data, dict):
        raise SettingsError(f"配置文件顶层应为字典 ({path})")

    default_mode = str(data.get("default_mode", "")).strip()
    permissions_raw = data.get("permissions", {})
    if not isinstance(permissions_raw, dict):
        permissions_raw = {}

    allow = permissions_raw.get("allow", [])
    deny = permissions_raw.get("deny", [])

    if not isinstance(allow, list):
        allow = []
    if not isinstance(deny, list):
        deny = []

    return Settings(
        default_mode=default_mode,
        permissions=PermissionsBlock(
            allow=[str(a).strip() for a in allow if a],
            deny=[str(d).strip() for d in deny if d],
        ),
    )


def to_rule_set(s: Settings) -> "RuleSet":
    """将 Settings 中的 allow/deny 字符串列表解析为 RuleSet。

    ch12: parse_rule 失败时 stderr 输出错误日志并跳过该条。
    非法条目跳过并降级（N5），不抛异常。

    Args:
        s: 权限配置。

    Returns:
        RuleSet 实例（allow 条 allow=True，deny 条 allow=False）。
    """
    from .rule import RuleSet, parse_rule  # 延迟导入避免循环

    rs = RuleSet()

    for raw in s.permissions.allow:
        raw = raw.strip()
        if not raw:
            continue
        rule, err = parse_rule(raw)
        if err is not None:
            print(f"rule {raw!r} parse failed: {err}", file=sys.stderr)
            continue
        if rule is not None and rule.tool:
            rule.allow = True
            rs.allow.append(rule)

    for raw in s.permissions.deny:
        raw = raw.strip()
        if not raw:
            continue
        rule, err = parse_rule(raw)
        if err is not None:
            print(f"rule {raw!r} parse failed: {err}", file=sys.stderr)
            continue
        if rule is not None and rule.tool:
            rule.allow = False
            rs.deny.append(rule)

    return rs


# ── 友好名映射 ──────────────────────────────────────────────────────────

# 内部名 → 友好名
_FRIENDLY_NAMES: dict[str, str] = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "glob": "Glob",
    "grep": "Grep",
}


def friendly_name(internal: str) -> str:
    """将内部工具名映射为友好名。

    Args:
        internal: 内部工具名（如 "read_file"、"bash"）。

    Returns:
        友好名（如 "Read"、"Bash"）。未知名原样返回。
    """
    return _FRIENDLY_NAMES.get(internal, internal)


# ── 类别判定 ────────────────────────────────────────────────────────────


def categorize(internal: str, read_only: bool) -> "Category":
    """根据工具内部名和只读标志判定操作类别。

    判定表：
      read_only == True          → Category.READ（优先，见 plan/N7）
      write_file / edit_file     → Category.WRITE
      其余（含 bash、未知工具）  → Category.EXEC（N7 最严）

    Args:
        internal: 内部工具名。
        read_only: 是否标记为只读（等价 registry.is_readonly 的结果）。

    Returns:
        Category 值。
    """
    from . import Category

    if read_only:
        return Category.READ

    if internal in ("write_file", "edit_file"):
        return Category.WRITE

    # 其余（bash 及未知工具）归为 EXEC（最严）
    return Category.EXEC


# ── 目标提取 ────────────────────────────────────────────────────────────


def _parse_input(call_input: Any) -> dict[str, Any]:
    """将 ToolCall.input 统一解析为 dict。

    call_input 可能是 str（JSON 字符串）或 dict。
    """
    if isinstance(call_input, dict):
        return call_input
    if isinstance(call_input, str):
        try:
            obj = json.loads(call_input)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def extract_target(call: "ToolCall") -> "tuple[str, bool, bool]":
    """从 ToolCall 中提取目标字符串供沙箱/规则匹配。

    Args:
        call: 工具调用（含 name 和 arguments）。

    Returns:
        (target, is_file, ok):
          - target: 提取的目标字符串（路径或命令）
          - is_file: 是否是文件类操作
          - is_file: 是否是文件类操作
          - ok: 解析是否成功（False 表示 JSON 不可解析或缺必填字段）
    """
    name = call.name
    args = _parse_input(call.arguments)

    # 文件类工具：取 file_path 参数
    if name in ("read_file", "write_file", "edit_file"):
        path_val = args.get("file_path", args.get("path", ""))
        if not isinstance(path_val, str) or not path_val.strip():
            return ("", True, False)  # 缺必填字段 path → 不可解析
        return (path_val.strip(), True, True)

    # 搜索工具：取 path 参数（搜索根目录），空则默认 "."
    if name in ("glob", "grep"):
        path_val = args.get("path", "")
        if path_val is None:
            path_val = ""
        if not isinstance(path_val, str):
            path_val = str(path_val)
        target = path_val.strip() if path_val.strip() else "."
        return (target, True, True)

    # 命令执行：取 command 参数
    if name == "bash":
        cmd = args.get("command", "")
        if cmd is None:
            cmd = ""
        if not isinstance(cmd, str):
            cmd = str(cmd)
        ok = bool(cmd.strip())  # 空命令 → ok=False（见 plan: bash 缺 command → Ask）
        return (cmd.strip(), False, ok)

    # 未知工具
    return ("", False, False)

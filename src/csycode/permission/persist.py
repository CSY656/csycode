"""规则持久化 —— 人在回路「永久允许」写入本地层配置。

提供:
  - rule_for: 从 ToolCall 生成精确 Rule（无通配，防止泛化误放行）
  - persist_local_allow: 将永久允许规则写入本地层文件 + 内存同步
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .settings import extract_target, friendly_name, load_settings

if TYPE_CHECKING:
    from csycode.llm import ToolCall
    from .engine import Engine
    from .rule import Rule


def _escape_glob(s: str) -> str:
    """转义字面 glob 元字符（* ? [ ]），防止规则被意外泛化。

    例如 "git commit -m 'fix * bug'" → "git commit -m 'fix \\* bug'"
    """
    for ch in ("*", "?", "[", "]"):
        s = s.replace(ch, f"\\{ch}")
    return s


def rule_for(call: "ToolCall", root: str) -> "tuple[Rule, str, bool]":
    """从 ToolCall 生成精确匹配规则。

    规则不含通配，只精确匹配当前调用的参数。
    命令串中的 glob 元字符会被转义。

    Args:
        call: 工具调用。
        root: 项目根（用于计算相对路径）。

    Returns:
        (Rule, yaml_str, ok):
          - Rule: 内存中的 Rule 对象
          - yaml_str: 写入 YAML 文件的字符串形式（如 "Bash(git status)"）
          - ok: 是否成功生成（解析失败/未知工具 → False）
    """
    from .rule import Rule
    from .matcher import compile_matcher

    target, is_file, ok = extract_target(call)
    fname = friendly_name(call.name)

    if not ok and call.name not in ("bash",):
        # 文件类工具解析失败 → 无法生成规则
        return (Rule("", None, "", False), "", False)

    if call.name == "bash":
        if not target:
            return (Rule("", None, "", False), "", False)
        escaped = _escape_glob(target)
        rule_str = f"{fname}({escaped})"
        # 持久化规则始终用 glob 类型（转义后的精确匹配 = glob 模式）
        matcher = compile_matcher(escaped, is_command=True)
        return (Rule(tool=fname, matcher=matcher, raw=escaped, allow=True), rule_str, True)

    if is_file and target:
        # 计算相对 root 的路径（使用 / 分隔符）
        try:
            rel = os.path.relpath(
                os.path.join(root, target) if not os.path.isabs(target) else target,
                root,
            ).replace("\\", "/")
        except (ValueError, OSError):
            rel = target.replace("\\", "/")

        rule_str = f"{fname}({rel})"
        matcher = compile_matcher(rel, is_command=False)
        return (Rule(tool=fname, matcher=matcher, raw=rel, allow=True), rule_str, True)

    # 未知工具 → 尽力而为
    if target:
        rule_str = f"{fname}({target})"
        matcher = compile_matcher(target, is_command=False)
        return (Rule(tool=fname, matcher=matcher, raw=target, allow=True), rule_str, True)

    return (Rule("", None, "", False), "", False)


def persist_local_allow(engine: "Engine", call: "ToolCall") -> None:
    """永久放行：将精确 allow 规则写入本地层配置文件并同步内存。

    流程：
      1. 从 ToolCall 生成精确规则
      2. 加载本地层 YAML（缺失则空）
      3. 追加规则字符串到 permissions.allow（去重）
      4. 写回文件（自动创建父目录）
      5. 将规则并入 engine.local.allow（内存同步）

    Args:
        engine: 权限引擎。
        call: 被永久放行的工具调用。

    Raises:
        OSError: 文件写入失败时向上抛（由调用方 agent 捕获并记日志）。
    """

    rule, rule_str, ok = rule_for(call, engine.root)
    if not ok or not rule_str:
        return

    # 加载现有本地层配置
    try:
        settings = load_settings(engine.local_path)
    except Exception:
        from .settings import Settings

        settings = Settings()

    # 去重
    if rule_str in settings.permissions.allow:
        return  # 已存在，无需重复写入

    # 追加
    settings.permissions.allow.append(rule_str)

    # 确保目录存在
    local_dir = Path(engine.local_path).parent
    local_dir.mkdir(parents=True, exist_ok=True)

    # 写回
    data: dict = {
        "permissions": {
            "allow": settings.permissions.allow,
            "deny": settings.permissions.deny,
        },
    }
    if settings.default_mode:
        data["default_mode"] = settings.default_mode

    content = yaml.safe_dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    Path(engine.local_path).write_text(content, encoding="utf-8")

    # 同步内存：并入 engine.local.allow
    if not isinstance(engine.local.allow, list):
        engine.local.allow = list(engine.local.allow)
    engine.local.allow.append(rule)

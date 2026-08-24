"""SubAgent 定义解析器 —— 对齐 mewcode agents/parser.py。

从 Markdown+YAML frontmatter 文件解析出 Definition。
复用 csycode.skills.parser 的 parse_frontmatter 逻辑（独立实现一份，
避免循环依赖）。
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import yaml

from .definition import Definition, Source

log = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────

# 角色名正则：大小写字母/数字/连字符/下划线，与 mewcode 一致
AGENT_NAME_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9\-_]{0,31}$")

# 已知的 model 值（其他值也接受但做 fallback）
VALID_MODELS = {"inherit", "haiku", "sonnet", "opus"}

# 合法的 permissionMode 值（对齐 mewcode）
VALID_PERMISSION_MODES = {"default", "acceptEdits", "bypassPermissions", "dontAsk", "plan"}


# ── 异常 ──────────────────────────────────────────────────────────


class AgentParseError(Exception):
    """Agent 定义文件解析失败时抛出。"""
    pass


# ── frontmatter 解析 ─────────────────────────────────────────────


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """从原始文本分离 YAML frontmatter 与 body。

    与 skills/parser.py 逻辑一致，独立实现避免循环依赖。
    """
    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        raise AgentParseError("缺少 YAML frontmatter（必须以 --- 开头）")

    end = stripped.find("---", 3)
    if end == -1:
        raise AgentParseError("未闭合的 YAML frontmatter（缺少结尾 ---）")

    yaml_block = stripped[3:end]
    body = stripped[end + 3:].lstrip("\n").rstrip()

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        # YAML 解析失败可能是未闭合 frontmatter（body 内容被当作 YAML）
        check = raw.lstrip()
        if check.startswith("---"):
            second = check.find("---", 3)
            if second == -1:
                raise AgentParseError("未闭合的 YAML frontmatter（缺少结尾 ---）")
        raise AgentParseError("frontmatter 中的 YAML 非法") from None

    if not isinstance(meta, dict):
        raise AgentParseError("frontmatter 必须是 YAML mapping")

    return meta, body


# ── 校验 ──────────────────────────────────────────────────────────


def _validate_meta(meta: dict, source: str = "") -> None:
    """校验 frontmatter 字典的必填字段与合法取值。

    Raises:
        AgentParseError: 缺少 name / description / 格式非法。
    """
    ctx = f" in {source}" if source else ""

    if "name" not in meta:
        raise AgentParseError(f"缺少必填字段 'name'{ctx}")
    if "description" not in meta:
        raise AgentParseError(f"缺少必填字段 'description'{ctx}")

    name = str(meta["name"])
    if not AGENT_NAME_REGEX.match(name):
        raise AgentParseError(
            f"非法 Agent 名 '{name}'{ctx}: "
            "必须是大小写字母、数字、连字符和下划线，以字母开头，最多 32 字符"
        )

    # model 字段：已知非法值 fallback 到 "inherit" 并警告，其它直通
    model = str(meta.get("model", "inherit")).strip()
    if model.lower() == "inherit":
        model = "inherit"
        meta["model"] = "inherit"
    elif model not in VALID_MODELS:
        # 第三方模型名（如 "glm-5.1"）直通，但打印警告
        if model:
            print(
                f"subagent{ctx}: unknown model '{model}', keeping as-is"
                f" (actual availability depends on provider)",
                file=sys.stderr,
            )

    # permissionMode：非法值 fallback 到 "default" 并警告
    pm = str(meta.get("permissionMode", "default")).strip()
    if pm and pm not in VALID_PERMISSION_MODES:
        print(
            f"subagent{ctx}: unknown permissionMode '{pm}', defaulting to 'default'",
            file=sys.stderr,
        )
        pm = "default"
        meta["permissionMode"] = "default"

    # maxTurns
    max_turns = meta.get("maxTurns")
    if max_turns is not None:
        if not isinstance(max_turns, int) or max_turns <= 0:
            raise AgentParseError(
                f"非法 maxTurns '{max_turns}'{ctx}: 必须是正整数"
            )


# ── 主解析函数 ───────────────────────────────────────────────────


def parse_definition(
    data: str,
    file_path: str = "",
    source: Source = Source.BUILTIN,
) -> Definition:
    """从原始文本解析 Definition。

    Args:
        data: .md 文件的完整 UTF-8 文本内容。
        file_path: 用于错误信息展示的文件路径。
        source: 定义来源层级。

    Returns:
        Definition 实例。

    Raises:
        AgentParseError: 解析/校验失败。
    """
    meta, body = _parse_frontmatter(data)
    _validate_meta(meta, file_path)

    # 提取字段
    name = str(meta["name"]).strip()
    description = str(meta["description"]).strip()

    tools = list(meta.get("tools") or [])
    if not isinstance(tools, list):
        tools = []
    tools = [str(t) for t in tools]

    disallowed_tools = list(meta.get("disallowedTools") or [])
    if not isinstance(disallowed_tools, list):
        disallowed_tools = []
    disallowed_tools = [str(t) for t in disallowed_tools]

    # model：先取已知合法值，否则 fallback
    model = str(meta.get("model", "inherit")).strip()
    if model.lower() == "inherit":
        model = "inherit"
    elif model not in VALID_MODELS:
        # 不认识的 model 一律 fallback 到 inherit
        if model:
            print(
                f"subagent{':' + file_path if file_path else ''}: "
                f"unknown model '{model}', defaulting to 'inherit'",
                file=sys.stderr,
            )
        model = "inherit"

    max_turns = int(meta.get("maxTurns") or 0)

    # permission_mode（对齐 mewcode：直接用字符串）
    permission_mode = str(meta.get("permissionMode", "default")).strip()
    if not permission_mode or permission_mode not in VALID_PERMISSION_MODES:
        if permission_mode and permission_mode not in VALID_PERMISSION_MODES:
            print(
                f"subagent{':' + file_path if file_path else ''}: "
                f"unknown permissionMode '{permission_mode}', defaulting to 'default'",
                file=sys.stderr,
            )
        permission_mode = "default"

    # dontAsk 识别
    dont_ask = permission_mode == "dontAsk"
    if dont_ask:
        permission_mode = "default"  # dontAsk 转为 don't ask 标志 + default 模式

    background = bool(meta.get("background", False))

    # isolation 字段: "" | "worktree"
    isolation = str(meta.get("isolation", "")).strip()
    if isolation and isolation not in ("worktree",):
        print(
            f"subagent{':' + file_path if file_path else ''}: "
            f"unknown isolation '{isolation}', defaulting to ''",
            file=sys.stderr,
        )
        isolation = ""

    return Definition(
        name=name,
        description=description,
        tools=tools,
        disallowed_tools=disallowed_tools,
        model=model,
        max_turns=max_turns,
        permission_mode=permission_mode,
        dont_ask=dont_ask,
        background=background,
        isolation=isolation,
        system_prompt=body,
        file_path=Path(file_path) if file_path else None,
        source=source,
    )


def parse_file(path: str | Path, source: Source) -> Definition:
    """从 .md 文件路径解析 Definition。

    Args:
        path: Agent 定义文件的路径。
        source: 来源层级。

    Returns:
        Definition 实例。

    Raises:
        AgentParseError: 读取/解析失败。
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        raise AgentParseError(f"无法读取 Agent 文件 {path}: {e}") from e

    return parse_definition(raw, str(path), source)

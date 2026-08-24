"""Skill 解析器 —— SkillDef 数据结构、frontmatter 解析与参数替换。

对齐 mewcode 的 skills/parser.py，负责:
- 解析 SKILL.md 的 YAML frontmatter + Markdown body
- 校验必填字段与合法取值
- $ARGUMENTS 占位符替换
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

log = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────

VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")
VALID_MODES = {"inline", "fork"}
VALID_CONTEXTS = {"full", "recent", "none"}


# ── 异常 ──────────────────────────────────────────────────────────


class SkillParseError(Exception):
    """Skill 文件解析失败时抛出。"""

    pass


# ── 数据结构 ──────────────────────────────────────────────────────


@dataclass
class SkillDef:
    """一个已解析的 Skill 定义。

    Attributes:
        name: 唯一标识名（小写字母/数字/连字符）。
        description: 一句话描述，用于 catalog 列表与 LLM 选 skill。
        prompt_body: SKILL.md 去 frontmatter 后的正文（SOP）。
        allowed_tools: 工具白名单列表，空列表表示不限制。
        mode: 执行模式 —— inline（主对话内执行）或 fork（隔离子 Agent）。
        model: 可选覆盖模型 ID。
        context: fork 模式下的上下文携带策略 —— full / recent / none。
        source_path: 磁盘文件路径（用于热重载）。
        is_directory: 是否为目录型 Skill。
    """

    name: str
    description: str
    prompt_body: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    mode: Literal["inline", "fork"] = "inline"
    model: str | None = None
    context: Literal["full", "recent", "none"] = "full"
    source_path: Path | None = None
    is_directory: bool = False


# ── frontmatter 解析 ─────────────────────────────────────────────


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """从原始文本分离 YAML frontmatter 与 body。

    Args:
        raw: SKILL.md 的完整内容。

    Returns:
        (meta_dict, body_str) —— meta 是从 frontmatter 解析的字典，
        body 是 frontmatter 之后的 Markdown 文本。

    Raises:
        SkillParseError: 缺少开头 --- / 未闭合 / YAML 非法 / 不是 mapping。
    """
    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        raise SkillParseError("缺少 YAML frontmatter（必须以 --- 开头）")

    end = stripped.find("---", 3)
    if end == -1:
        raise SkillParseError("未闭合的 YAML frontmatter（缺少结尾 ---）")

    yaml_block = stripped[3:end]
    body = stripped[end + 3 :].lstrip("\n")

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        raise SkillParseError(f"frontmatter 中的 YAML 非法: {e}") from e

    if not isinstance(meta, dict):
        raise SkillParseError("frontmatter 必须是 YAML mapping")

    return meta, body


# ── 校验 ──────────────────────────────────────────────────────────


def _validate_meta(meta: dict, source: str = "") -> None:
    """校验 frontmatter 字典的必填字段与合法取值。

    Raises:
        SkillParseError: 缺少 name / description / 格式非法 / mode/context 非法。
    """
    ctx = f" in {source}" if source else ""

    if "name" not in meta:
        raise SkillParseError(f"缺少必填字段 'name'{ctx}")
    if "description" not in meta:
        raise SkillParseError(f"缺少必填字段 'description'{ctx}")

    name = meta["name"]
    if not isinstance(name, str) or not VALID_NAME_RE.match(name):
        raise SkillParseError(
            f"非法 skill 名 '{name}'{ctx}: "
            "必须是小写字母、数字和连字符，以字母开头"
        )

    mode = meta.get("mode", "inline")
    if mode not in VALID_MODES:
        raise SkillParseError(
            f"非法 mode '{mode}'{ctx}: 必须是 {VALID_MODES} 之一"
        )

    context = meta.get("context", "full")
    if context not in VALID_CONTEXTS:
        raise SkillParseError(
            f"非法 context '{context}'{ctx}: 必须是 {VALID_CONTEXTS} 之一"
        )


# ── 文件解析 ──────────────────────────────────────────────────────


def parse_skill_file(path: Path) -> SkillDef:
    """从 .md 文件解析一个 SkillDef。

    Args:
        path: SKILL.md（或任何 .md skill 文件）的路径。

    Returns:
        SkillDef 实例。

    Raises:
        SkillParseError: 读取失败 / 解析失败 / 校验失败。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SkillParseError(f"无法读取 skill 文件 {path}: {e}") from e

    meta, body = parse_frontmatter(raw)
    _validate_meta(meta, str(path))

    return SkillDef(
        name=meta["name"],
        description=meta["description"],
        prompt_body=body,
        allowed_tools=meta.get("allowed_tools", []),
        mode=meta.get("mode", "inline"),
        model=meta.get("model"),
        context=meta.get("context", "full"),
        source_path=path,
        is_directory=False,
    )


# ── 参数替换 ──────────────────────────────────────────────────────


def substitute_arguments(prompt_body: str, args: str) -> str:
    """将 $ARGUMENTS 占位符替换为用户参数。

    若 prompt_body 中不含 $ARGUMENTS 且 args 非空，
    则将用户请求追加到末尾（append fallback）。
    """
    if "$ARGUMENTS" in prompt_body:
        return prompt_body.replace("$ARGUMENTS", args)
    # 无占位符时的 append fallback
    if args.strip():
        return prompt_body + "\n\n## User Request\n\n" + args
    return prompt_body

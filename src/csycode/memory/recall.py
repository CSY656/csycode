"""记忆召回 —— 扫描记忆文件、按查询选择相关记忆、渲染为 system-reminder。

对齐 mewcode 的 memory/recall.py：find_relevant_memories + render_reminder。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_MEMORY_FILES = 200
FRONTMATTER_MAX_LINES = 30
ENTRYPOINT_NAME = "MEMORY.md"
VALID_TYPES = {"user", "feedback", "project", "reference"}

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

SELECTOR_SYSTEM_PROMPT = (
    "You are selecting memories that will be useful to the coding agent as it processes "
    "a user's query. You will be given the user's query and a list of available "
    "memory files with their filenames and descriptions.\n\n"
    "Return a list of filenames for the memories that will clearly be useful to "
    "the agent as it processes the user's query (up to 5). Only include memories "
    "that you are certain will be helpful based on their name and description.\n"
    "- If you are unsure if a memory will be useful in processing the user's "
    "query, then do not include it in your list. Be selective and discerning.\n"
    "- If there are no memories in the list that would clearly be useful, feel "
    "free to return an empty list.\n"
    "- If a list of recently-used tools is provided, do not select memories "
    "that are usage reference or API documentation for those tools (the agent is "
    "already exercising them). DO still select memories containing warnings, "
    "gotchas, or known issues about those tools — active use is exactly when "
    "those matter.\n\n"
    'Respond with valid JSON only, no markdown, in this exact shape: '
    '{"selected_memories": ["filename1.md", "filename2.md"]}'
)

SelectorFn = Callable[[str, str], Awaitable[str]]


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------


@dataclass
class MemoryHeader:
    """记忆文件元信息。"""

    filename: str       # 相对于 memory_dir 的路径
    file_path: str      # 绝对路径
    scope: str          # "user" | "project"
    mtime_ms: int       # 修改时间（毫秒 Unix 时间戳）
    description: str    # frontmatter 描述
    type: str           # frontmatter 类型


@dataclass
class RelevantMemory:
    """一条被选中的相关记忆。"""

    path: str
    mtime_ms: int


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------


def _memory_age_days(mtime_ms: int) -> int:
    d = (int(time.time() * 1000) - mtime_ms) // 86_400_000
    return max(d, 0)


def _memory_age(mtime_ms: int) -> str:
    d = _memory_age_days(mtime_ms)
    if d == 0:
        return "today"
    if d == 1:
        return "yesterday"
    return "%d days ago" % d


def _memory_freshness_text(mtime_ms: int) -> str:
    d = _memory_age_days(mtime_ms)
    if d <= 1:
        return ""
    return (
        "This memory is %d days old. "
        "Memories are point-in-time observations, not live state — "
        "claims about code behavior or file:line citations may be outdated. "
        "Verify against current code before asserting as fact." % d
    )


# ---------------------------------------------------------------------------
# Frontmatter 解析
# ---------------------------------------------------------------------------


def parse_frontmatter(content: str) -> dict[str, str]:
    """从 YAML-ish frontmatter 中提取 name/description/type。

    只读取三个已知字段，未知字段忽略。无 frontmatter 返回空字段。
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {"name": "", "description": "", "type": ""}

    result: dict[str, str] = {"name": "", "description": "", "type": ""}
    for line in m.group(1).split("\n"):
        colon = line.find(":")
        if colon < 0:
            continue
        key = line[:colon].strip()
        val = line[colon + 1:].strip()
        if len(val) >= 2 and (
            (val.startswith('"') and val.endswith('"'))
            or (val.startswith("'") and val.endswith("'"))
        ):
            val = val[1:-1]
        if key == "name":
            result["name"] = val
        elif key == "description":
            result["description"] = val
        elif key == "type":
            if val in VALID_TYPES:
                result["type"] = val
    return result


# ---------------------------------------------------------------------------
# 文件扫描
# ---------------------------------------------------------------------------


def scan_memory_files(memory_dir: Path, scope: str) -> list[MemoryHeader]:
    """扫描 memory_dir 下所有 .md 文件（排除 MEMORY.md），解析 frontmatter。

    返回按修改时间倒序排列的列表，最多 MAX_MEMORY_FILES 条。
    """
    if not memory_dir.is_dir():
        return []

    md_files: list[Path] = []
    try:
        for fp in memory_dir.rglob("*.md"):
            if fp.is_file() and fp.name != ENTRYPOINT_NAME:
                md_files.append(fp)
    except OSError:
        return []

    results: list[MemoryHeader] = []
    for fp in md_files:
        hdr = _read_memory_header(fp, memory_dir, scope)
        if hdr is not None:
            results.append(hdr)

    results.sort(key=lambda h: h.mtime_ms, reverse=True)
    if len(results) > MAX_MEMORY_FILES:
        results = results[:MAX_MEMORY_FILES]
    return results


def _read_memory_header(
    file_path: Path, memory_dir: Path, scope: str
) -> MemoryHeader | None:
    try:
        mtime_ms = int(file_path.stat().st_mtime * 1000)
    except OSError:
        return None

    try:
        lines: list[str] = []
        with file_path.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= FRONTMATTER_MAX_LINES:
                    break
                lines.append(line)
        content = "".join(lines)
    except OSError:
        return None

    fm = parse_frontmatter(content)
    try:
        rel = str(file_path.relative_to(memory_dir))
    except ValueError:
        rel = file_path.name

    return MemoryHeader(
        filename=rel,
        file_path=str(file_path.resolve()),
        scope=scope,
        mtime_ms=mtime_ms,
        description=fm["description"],
        type=fm["type"],
    )


# ---------------------------------------------------------------------------
# Manifest 格式化
# ---------------------------------------------------------------------------


def format_memory_manifest(memories: list[MemoryHeader]) -> str:
    """格式化记忆清单供选择器 prompt 使用。"""
    if not memories:
        return ""
    lines: list[str] = []
    for m in memories:
        scope_tag = "[%s-scope] " % m.scope if m.scope else ""
        type_tag = "[%s] " % m.type if m.type else ""
        ts = datetime.fromtimestamp(
            m.mtime_ms / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S")
        path = m.file_path if m.file_path else m.filename
        if m.description:
            lines.append("- %s%s%s (%s): %s" % (scope_tag, type_tag, path, ts, m.description))
        else:
            lines.append("- %s%s%s (%s)" % (scope_tag, type_tag, path, ts))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 查找相关记忆
# ---------------------------------------------------------------------------


async def find_relevant_memories(
    query: str,
    user_mem_dir: Path | None,
    project_mem_dir: Path | None,
    recent_tools: list[str] | None,
    already_surfaced: set[str] | None,
    selector: SelectorFn,
) -> list[RelevantMemory]:
    """扫描两个目录，过滤已展示过的，调用选择器选出最多 5 条相关记忆。

    Args:
        query: 用户当前查询文本。
        user_mem_dir: 用户级记忆目录路径。
        project_mem_dir: 项目级记忆目录路径。
        recent_tools: 最近使用的工具名列表（用于过滤工具参考类记忆）。
        already_surfaced: 已在本轮展示过的文件路径集合。
        selector: 选择器函数 (system_prompt, user_message) -> response_text。

    Returns:
        选中的相关记忆列表。选择器失败时返回空列表（静默降级）。
    """
    all_headers: list[MemoryHeader] = []
    if user_mem_dir is not None:
        all_headers.extend(scan_memory_files(user_mem_dir, "user"))
    if project_mem_dir is not None:
        all_headers.extend(scan_memory_files(project_mem_dir, "project"))

    surfaced = already_surfaced or set()
    candidates = [m for m in all_headers if m.file_path not in surfaced]
    if not candidates:
        return []

    selected_filenames = await _select_relevant_memories(
        query, candidates, recent_tools, selector
    )

    by_key: dict[str, MemoryHeader] = {}
    for m in candidates:
        by_key[m.file_path] = m
        by_key.setdefault(m.filename, m)

    result: list[RelevantMemory] = []
    for fn in selected_filenames:
        m = by_key.get(fn)
        if m is not None:
            result.append(RelevantMemory(path=m.file_path, mtime_ms=m.mtime_ms))
    return result


async def _select_relevant_memories(
    query: str,
    memories: list[MemoryHeader],
    recent_tools: list[str] | None,
    selector: SelectorFn,
) -> list[str]:
    """格式化清单 → 调选择器 → 解析 JSON 返回文件名列表。"""
    valid_filenames = {m.filename for m in memories}
    manifest = format_memory_manifest(memories)

    tools_section = ""
    if recent_tools:
        tools_section = "\n\nRecently used tools: " + ", ".join(recent_tools)

    user_message = "Query: %s\n\nAvailable memories:\n%s%s" % (
        query, manifest, tools_section
    )

    try:
        raw = await selector(SELECTOR_SYSTEM_PROMPT, user_message)
    except Exception:
        return []

    clean = _extract_json_object(raw)
    if not clean:
        return []

    try:
        parsed = json.loads(clean)
        arr = parsed.get("selected_memories", [])
        if not isinstance(arr, list):
            return []
        return [f for f in arr if isinstance(f, str) and f in valid_filenames]
    except (json.JSONDecodeError, AttributeError):
        return []


def _extract_json_object(raw: str) -> str:
    """从 LLM 返回文本中提取第一个 {…} JSON 对象。"""
    trimmed = raw.strip()
    if trimmed.startswith("{"):
        return trimmed
    start = trimmed.find("{")
    if start < 0:
        return ""
    end = trimmed.rfind("}")
    if end < start:
        return ""
    return trimmed[start : end + 1]


# ---------------------------------------------------------------------------
# Reminder 渲染
# ---------------------------------------------------------------------------


def render_reminder(memories: list[RelevantMemory]) -> str:
    """读每条选中记忆的完整内容，格式化为 system-reminder 正文。

    包含 freshness 警告：超过 1 天的记忆标注可能过时。
    """
    if not memories:
        return ""

    parts: list[str] = []
    parts.append(
        "The following relevant memories from prior conversations may help:\n"
    )
    for mem in memories:
        try:
            content = Path(mem.path).read_text(encoding="utf-8")
        except OSError:
            continue
        basename = Path(mem.path).name
        parts.append("## Memory: %s (saved %s)\n" % (basename, _memory_age(mem.mtime_ms)))
        note = _memory_freshness_text(mem.mtime_ms)
        if note:
            parts.append(note + "\n")
        parts.append(content + "\n\n---\n")
    return "\n".join(parts)

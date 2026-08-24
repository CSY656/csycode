"""三段恢复模块。

在 LLM 摘要完成后，构造恢复内容附加在摘要之后：
  1. 最近读过的文件快照
  2. 当前可用工具列表
  3. 边界提示消息
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

from .const import (
    ESTIMATE_CHARS_PER_TOKEN,
    RECOVERY_FILE_LIMIT,
    RECOVERY_TOKENS_PER_FILE,
)

if TYPE_CHECKING:
    from csycode.llm import ToolDefinition

    from .state import FileReadRecord


# ── 边界提示 ─────────────────────────────────────────────────────────────

BOUNDARY_NOTICE: str = """\
## 边界提示
需要文件原文、错误原文、用户原话时，请使用文件读取工具重新读取对应路径，不要依据摘要内容做猜测。
如果被替换的工具结果需要查看完整内容，请用文件读取工具读取保存路径下的文件。"""


# ── 单文件块渲染 ─────────────────────────────────────────────────────────


def render_file_block(rec: FileReadRecord) -> str:
    """渲染单个文件快照：路径 / 时间戳 / 内容片段（必要时截断）。

    单个文件超过 RECOVERY_TOKENS_PER_FILE token 时保留头部对应字符片段，
    截掉尾部多余内容，尾部追加 (content truncated) 标注。
    """
    char_limit = int(RECOVERY_TOKENS_PER_FILE * ESTIMATE_CHARS_PER_TOKEN)
    content = rec.content
    truncated = False
    if len(content) > char_limit:
        content = content[:char_limit]
        truncated = True

    buf = io.StringIO()
    buf.write(f"### {rec.path}\n")
    buf.write(f"[read at] {rec.timestamp.isoformat()}\n")
    buf.write(content)
    if truncated:
        buf.write("\n(content truncated)")
    buf.write("\n")
    return buf.getvalue()


# ── 工具列表块渲染 ───────────────────────────────────────────────────────


def render_tools_block(defs: list[ToolDefinition]) -> str:
    """渲染工具列表：每行一个工具名 + 用途 + 参数 schema 摘要。"""
    buf = io.StringIO()
    for d in defs:
        name = d.get("name", d.get("function", {}).get("name", "unknown"))
        desc = d.get("description", d.get("function", {}).get("description", ""))
        schema = d.get("input_schema", d.get("function", {}).get("parameters", {}))
        schema_str = json.dumps(schema, separators=(",", ":"), ensure_ascii=False)
        buf.write(f"- {name}: {desc}\n")
        buf.write(f"  schema: {schema_str}\n")
    return buf.getvalue()


# ── 三段拼接 ─────────────────────────────────────────────────────────────


def build_recovery_attachment(
    snapshot: list[FileReadRecord],
    tool_defs: list[ToolDefinition],
) -> str:
    """构造摘要后的"恢复三段"内容。

    调用方必须先在 run_summary 入口拍一次快照，把快照而非 RecoveryState
    传入本函数，避免恢复段渲染期间另一个 task 通过 record_file 改变状态。

    Args:
        snapshot: 文件读取记录快照（已按时间戳倒序）。
        tool_defs: 工具定义列表（与 stream 调用同一列表引用）。

    Returns:
        纯文本恢复内容字符串。
    """
    buf = io.StringIO()

    # 第 1 段：最近读过的文件
    buf.write("## 最近读过的文件\n")
    recent_files = snapshot[:RECOVERY_FILE_LIMIT]
    if recent_files:
        for rec in recent_files:
            buf.write(render_file_block(rec))
    else:
        buf.write("(无)\n")

    # 第 2 段：当前可用工具
    buf.write("\n## 当前可用工具\n")
    if tool_defs:
        buf.write(render_tools_block(tool_defs))
    else:
        buf.write("(无)\n")

    # 第 3 段：边界提示
    buf.write("\n")
    buf.write(BOUNDARY_NOTICE)

    return buf.getvalue()

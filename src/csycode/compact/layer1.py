"""第 1 层预防性压缩：单结果与聚合落盘 + 决策冻结。

在每一轮 LLM 请求发出之前，对 Conversation 中的工具结果做幂等的
"超阈值落盘 + 字符串替换"，并把替换决策冻结在会话级账本里。

csycode 中，一轮工具调用的结果通过 Conversation.add_tool_results
以连续多条 user 消息（tool_call_id 不为 None）的形式追加到对话历史。
连续的 tool_result 消息组成一个"批次"，聚合阈值以批次为单位计算。
"""

from __future__ import annotations

import copy
import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .const import (
    MESSAGE_AGGREGATE_LIMIT,
    PREVIEW_HEAD_BYTES,
    PREVIEW_HEAD_LINES,
    SINGLE_RESULT_LIMIT,
)

if TYPE_CHECKING:
    from csycode.llm import Message

    from .state import ContentReplacementState, SessionContext

_logger = logging.getLogger(__name__)


# ── 单条工具结果落盘 ─────────────────────────────────────────────────────


def spill_single(session: SessionContext, tool_use_id: str, content: str) -> None:
    """把单条 tool_result 内容写入 spill_dir/<tool_use_id>。

    幂等：文件已存在则不重写、不报错。失败抛 OSError 由上层捕获。
    """
    path = Path(session.spill_dir) / tool_use_id
    if path.exists():
        return
    path.write_bytes(content.encode("utf-8"))


# ── 预览体构造 ───────────────────────────────────────────────────────────


def _head_preview(content: str) -> str:
    """取内容的前若干行/字节作为预览头部。

    先按 \\n 分成最多 PREVIEW_HEAD_LINES 行，再按 PREVIEW_HEAD_BYTES
    字节二次裁剪，二者择短。
    """
    lines = content.splitlines(keepends=True)
    if len(lines) > PREVIEW_HEAD_LINES:
        lines = lines[:PREVIEW_HEAD_LINES]
    head = "".join(lines)
    # 字节级二次截断，注意 UTF-8 边界对齐
    encoded = head.encode("utf-8")
    if len(encoded) > PREVIEW_HEAD_BYTES:
        truncated = encoded[:PREVIEW_HEAD_BYTES]
        head = truncated.decode("utf-8", errors="ignore")
    return head


def build_preview(original_bytes: int, head: str, spill_path: str) -> str:
    """构造替换体字符串。

    包含四项信息：
      ① 原始字节数元信息
      ② 头部预览
      ③ 落盘文件路径
      ④ 重读提示

    调用时机：只在 offload_and_snip 内首次决策为替换的瞬间调用一次；
    之后所有轮次都必须通过 state.decide_once 复用 _replacements[id]。
    """
    buf = io.StringIO()
    buf.write(f"[content offloaded] original size: {original_bytes} bytes\n")
    buf.write(f"[saved to] {spill_path}\n")
    buf.write("[head preview]\n")
    buf.write(head)
    if not head.endswith("\n"):
        buf.write("\n")
    buf.write(
        "完整内容已保存到上述路径，如需查看请用文件读取工具读取该路径，"
        "不要凭头部预览猜测全文"
    )
    return buf.getvalue()


# ── offload_and_snip 主体 ─────────────────────────────────────────────────


def _process_batch(
    batch_indices: list[int],
    msgs: list[Message],
    state: ContentReplacementState,
    session: SessionContext,
) -> list[Message]:
    """处理一个批次的 tool_result 消息组。

    规则（F1 + F2 合并扫描）：
      1. 先遍历批次中每条 tool_result，对已 Seen 的 id 通过 decide_once
         拿存量结果；未 Seen 的进入候选列表。
      2. 把候选列表按字节倒序排序。
      3. 按倒序处理每个候选：
         a. 单条 > SINGLE_RESULT_LIMIT → 必须落盘。
         b. 否则若剩余聚合字节 > MESSAGE_AGGREGATE_LIMIT → 继续落盘。
         c. 直到剩余聚合 ≤ MESSAGE_AGGREGATE_LIMIT 停手。
      4. 落盘 → 改写 content → 写账本 通过 decide_once 在临界区内完成。

    Args:
        batch_indices: 批次中消息在 msgs 中的索引列表。
        msgs: 原始消息列表（用于读取）。
        state: 替换决策账本。
        session: 会话上下文。

    Returns:
        处理后的消息列表（仅含本批次的消息）。
    """
    # 第一步：分离已决策和未决策
    kept_results: dict[int, str] = {}  # idx → new_content
    candidates: list[tuple[int, str, int]] = []  # (idx, tool_use_id, content_bytes)

    for idx in batch_indices:
        msg = msgs[idx]
        tool_use_id = msg.tool_call_id or ""
        original = msg.content

        # 先探测是否已 Seen
        if tool_use_id in state._seen_ids:
            kept_results[idx] = state.decide_once(
                tool_use_id, original, lambda: ("kept", "")
            )
            continue

        content_bytes = len(original.encode("utf-8"))
        candidates.append((idx, tool_use_id, content_bytes))

    # 第二步：按字节倒序排序
    candidates.sort(key=lambda x: x[2], reverse=True)

    # 第三步：计算初始聚合（仅未落盘的候选）
    current_aggregate = sum(c[2] for c in candidates)

    # 第四步：按倒序遍历，决策落盘
    for idx, tool_use_id, content_bytes in candidates:
        msg = msgs[idx]
        original = msg.content
        must_spill = content_bytes > SINGLE_RESULT_LIMIT

        # 防无限递归：若内容来自 spill 目录的读回，跳过落盘
        if _is_spill_readback(original, session.spill_dir):
            kept_results[idx] = state.decide_once(
                tool_use_id, original, lambda: ("kept", "")
            )
            current_aggregate -= content_bytes
            continue

        if not must_spill and current_aggregate <= MESSAGE_AGGREGATE_LIMIT:
            # 剩余聚合不超标，保留原文
            kept_results[idx] = state.decide_once(
                tool_use_id, original, lambda: ("kept", "")
            )
            continue

        def _decide() -> tuple[str, str]:
            try:
                spill_single(session, tool_use_id, original)
            except OSError:
                _logger.warning("落盘失败 tool_use_id=%s，降级为保留原文", tool_use_id)
                return ("skip", "")
            spill_path = str(Path(session.spill_dir) / tool_use_id)
            preview = build_preview(
                content_bytes,
                _head_preview(original),
                spill_path,
            )
            return ("replaced", preview)

        new_content = state.decide_once(tool_use_id, original, _decide)
        kept_results[idx] = new_content
        current_aggregate -= content_bytes

    # 第五步：按原索引顺序组装结果
    result: list[Message] = []
    for idx in batch_indices:
        if idx in kept_results:
            new_msg = copy.deepcopy(msgs[idx])
            new_msg.content = kept_results[idx]
            result.append(new_msg)
        else:
            result.append(copy.deepcopy(msgs[idx]))

    return result


def _is_spill_readback(content: str, spill_dir: str) -> bool:
    """检测工具结果是否来自读取 spill 目录中的文件。

    对齐 mewcode _is_spill_readback：当模型读取了已落盘的 spill 文件后，
    其工具结果中会包含 spill 目录路径引用。此时不应再次落盘，
    否则会导致无限递归（落盘 → 读回 → 再次落盘 → ...）。
    """
    # 快速检查：内容中是否包含 spill 目录路径
    if spill_dir in content:
        return True
    # 也检查标准化后的路径（处理反斜杠 / 正斜杠差异）
    normalized_spill = spill_dir.replace("\\", "/")
    if normalized_spill in content.replace("\\", "/"):
        return True
    return False


def offload_and_snip(
    msgs: list[Message],
    state: ContentReplacementState,
    session: SessionContext,
) -> list[Message]:
    """遍历 msgs，对连续的 tool_result 消息批次做压缩处理。

    返回新的 list[Message]，纯函数风格，不修改入参。
    """
    out: list[Message] = []
    i = 0
    n = len(msgs)

    while i < n:
        msg = msgs[i]

        # 非 tool_result 消息直接透传
        if msg.tool_call_id is None:
            out.append(copy.deepcopy(msg))
            i += 1
            continue

        # 收集连续的 tool_result 消息（同批次）
        batch_indices: list[int] = []
        while i < n and msgs[i].tool_call_id is not None:
            batch_indices.append(i)
            i += 1

        # 处理这个批次
        batch_out = _process_batch(batch_indices, msgs, state, session)
        out.extend(batch_out)

    return out

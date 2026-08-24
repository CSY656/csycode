"""第 2 层 LLM 摘要 + 恢复。

auto_compact 是主编排器：先 apply budget → 再 compute keep_start →
summarize prefix → build recovery → replace_history。
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

from csycode.llm import Message, PromptTooLongError

from .const import (
    MANUAL_SAFETY_MARGIN,
    PTL_DROP_PERCENTAGE,
    PTL_RETRY_LIMIT,
    RECENT_KEEP_MESSAGES,
    RECENT_KEEP_TOKENS,
    SUMMARY_RESERVE,
    AUTO_SAFETY_MARGIN,
)
from .recovery import build_recovery_attachment
from .summary_prompt import build_summary_prompt, extract_summary
from .token import estimate_tokens, message_chars

if TYPE_CHECKING:
    from csycode.conversation import Conversation
    from csycode.llm import Provider, ToolDefinition

    from .state import (
        CompactCircuitBreaker,
        ContentReplacementState,
        RecoveryState,
        SessionContext,
    )

_logger = logging.getLogger(__name__)

# 近期原文保留最大 token 上限（防单条超大消息吞掉整个窗口）
KEEP_MAX_TOKENS = 40_000
# 前缀 token 数低于此阈值不压缩（"压了个寂寞"）
MIN_SUMMARIZE_PREFIX_TOKENS = 2_000

# ── 阈值计算 ─────────────────────────────────────────────────────────────


def compute_compact_threshold(context_window: int, manual: bool = False) -> int:
    """计算压缩触发阈值。"""
    effective = context_window - SUMMARY_RESERVE
    margin = MANUAL_SAFETY_MARGIN if manual else AUTO_SAFETY_MARGIN
    return effective - margin


def should_auto_compact(last_input_tokens: int, context_window: int) -> bool:
    """判断当前 token 是否达到自动压缩阈值。"""
    return last_input_tokens >= compute_compact_threshold(context_window)


# ── 近期原文裁剪（对齐 mewcode 的 _compute_keep_start_index）───────────


def _compute_keep_start_index(messages: list[Message]) -> int:
    """决定压缩时尾部要原样保留多少条消息。

    从尾部向头部遍历，逐条累加 token 估算值。条件：
    - token 下限：累计达到 KEEP_RECENT_TOKENS 或
    - 条数下限：累计达到 RECENT_KEEP_MESSAGES
    - 二者择宽（任一满足即停）
    - 硬上限：纳入下一条会超 KEEP_MAX_TOKENS 时停止
    """
    n = len(messages)
    if n == 0:
        return 0

    kept_tokens = 0
    kept_count = 0
    keep_start = n

    for i in range(n - 1, -1, -1):
        tok = message_chars([messages[i]]) // 3  # 粗略估算

        if kept_count > 0 and kept_tokens + tok > KEEP_MAX_TOKENS:
            break

        kept_tokens += tok
        kept_count += 1
        keep_start = i

        if kept_tokens >= RECENT_KEEP_TOKENS or kept_count >= RECENT_KEEP_MESSAGES:
            break

    return _align_keep_start_to_tool_pair(messages, keep_start)


def _align_keep_start_to_tool_pair(messages: list[Message], keep_start: int) -> int:
    """把 keep_start 往前挪，确保不保留孤立的 tool_result。

    如果 keep_start 落在 tool_result 消息上（tool_call_id 不为 None），
    向前推到它前面那条带 tool_calls 的 assistant 消息处。
    """
    while 0 < keep_start < len(messages):
        msg = messages[keep_start]
        if msg.tool_call_id is not None:
            prev = messages[keep_start - 1]
            if prev.role == "assistant" and prev.tool_calls:
                keep_start -= 1
                continue
        break
    return keep_start


def _prefix_too_small_to_compact(prefix: list[Message]) -> bool:
    """摘要前缀太小不需要压缩。"""
    if not prefix:
        return True
    return estimate_tokens(0, prefix, 0) < MIN_SUMMARIZE_PREFIX_TOKENS


# ── 按用户轮次分组（PTL 重试用）─────────────────────────────────────────


def _group_messages_by_turn(messages: list[Message]) -> list[list[Message]]:
    """按完整轮次分组。每遇到普通 user 消息（非 tool_result）开新组。"""
    groups: list[list[Message]] = []
    current: list[Message] = []
    for msg in messages:
        if msg.role == "user" and msg.tool_call_id is None:
            if current:
                groups.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        groups.append(current)
    return groups


# ── 单次摘要请求 ─────────────────────────────────────────────────────────


async def _summarize_once(
    provider: Provider,
    conv_for_summary: Conversation,
    model: str,
    tool_schemas: list[dict] | None = None,
) -> str:
    """发一次摘要请求，返回解析后的摘要文本。"""
    from csycode.llm import Request

    msgs = conv_for_summary.messages()
    prompt_msgs = build_summary_prompt(msgs)
    req = Request(messages=prompt_msgs, tools=[])  # 无工具

    text_buf: list[str] = []
    async for ev in provider.stream(req):
        if ev.err is not None:
            raise ev.err
        if ev.text:
            text_buf.append(ev.text)

    return extract_summary("".join(text_buf))


# ── PTL 重试 ─────────────────────────────────────────────────────────────


async def _ptl_retry(
    provider: Provider,
    conv_for_summary: Conversation,
    model: str,
    first_err: Exception,
) -> str:
    """摘要请求自身撞 PTL 时的丢消息组重试策略。"""
    groups = _group_messages_by_turn(conv_for_summary.messages())

    # 去掉摘要 prompt 部分（第一条 user + conversation）
    # 保留：groups 是纯对话部分
    attempt = 0

    while True:
        attempt += 1

        if attempt <= PTL_RETRY_LIMIT:
            drop = 1
        else:
            drop = math.ceil(len(groups) * PTL_DROP_PERCENTAGE)
            drop = max(drop, 1)

        groups = groups[drop:]

        if len(groups) == 0:
            raise first_err

        # 重建 conversation（只含保留的消息）
        flat_msgs: list[Message] = [m for g in groups for m in g]
        conv_for_summary.replace_history(flat_msgs)

        try:
            return await _summarize_once(provider, conv_for_summary, model)
        except PromptTooLongError as e:
            first_err = e
            continue


# ── auto_compact（主编排器，对齐 mewcode）──────────────────────────────


async def auto_compact(
    conversation: Conversation,
    provider: Provider,
    model: str,
    context_window: int,
    replacement: ContentReplacementState,
    recovery: RecoveryState,
    auto_tracking: CompactCircuitBreaker,
    session: SessionContext,
    tool_defs: list[ToolDefinition],
    manual: bool = False,
    budget_messages: list[Message] | None = None,
) -> tuple[int, int] | None:
    """主编排器：阈值判断 → 计算 keep_start → 摘要 prefix → replace_history。

    对齐 mewcode 的 auto_compact：
    1. 判断阈值
    2. 用 budget_messages（已做 layer1）计算 keep_start
    3. 摘要 prefix + 保留 tail
    4. replace_history
    5. 清理落盘目录
    6. 更新熔断器

    Returns:
        (before_tokens, after_tokens) 或 None（不需要压缩时）。
    """
    current = conversation.current_tokens()
    before_tokens = current

    if not manual:
        soft_threshold = compute_compact_threshold(context_window, manual=False)

        if current < soft_threshold:
            return None

        hard_threshold = compute_compact_threshold(context_window, manual=True)
        if current >= hard_threshold:
            # 强制压缩路径：绕过熔断器
            pass
        else:
            if auto_tracking.tripped():
                _logger.warning("自动压缩已熔断（连续失败 %d 次）", 3)
                return None

    # ── Step 1: 使用调用方传入的 budget_messages ──────────────────
    effective_history = budget_messages if budget_messages else conversation.messages()

    # ── Step 2: 计算 keep_start ────────────────────────────────
    keep_start = _compute_keep_start_index(effective_history)
    to_summarize = effective_history[:keep_start]
    keep_tail = effective_history[keep_start:]

    # ── Step 3: 前缀太小不压缩 ──────────────────────────────────
    if keep_start <= 0 or _prefix_too_small_to_compact(to_summarize):
        return None

    # ── Step 4: 构造摘要对话 ────────────────────────────────────
    from csycode.conversation import Conversation as Conv

    summary_conv = Conv()
    # 第一条：摘要指令
    from .summary_prompt import SUMMARY_INSTRUCTION

    summary_conv._messages.append(Message(role="user", content=SUMMARY_INSTRUCTION))
    for msg in to_summarize:
        summary_conv._messages.append(msg)
    summary_conv._messages.append(
        Message(
            role="user",
            content="Please provide your summary of the conversation above now. "
            "REMINDER: Do NOT call any tools — respond with plain text only.",
        )
    )

    # ── Step 5: 发起摘要请求 ────────────────────────────────────
    llm_output: str | None = None

    try:
        llm_output = await _summarize_once(provider, summary_conv, model)
    except PromptTooLongError as e:
        try:
            llm_output = await _ptl_retry(provider, summary_conv, model, e)
        except Exception:
            if not manual:
                auto_tracking.record_failure()
            raise
    except Exception:
        if not manual:
            auto_tracking.record_failure()
        raise

    if llm_output is None:
        if not manual:
            auto_tracking.record_failure()
        raise RuntimeError("摘要生成失败：多次重试后仍超出上下文限制")

    # ── Step 6: 解析摘要 + 构造恢复段 ───────────────────────────
    summary = extract_summary(llm_output)
    attachment = build_recovery_attachment(recovery.snapshot(), tool_defs)

    # ── Step 7: 重建对话 ────────────────────────────────────────
    content = (
        "本次会话延续自之前的对话，因上下文空间不足进行了压缩。"
        "以下是早期对话的摘要：\n\n" + summary
    )
    if keep_tail:
        content += "\n\n近期消息已原样保留。"
    if attachment:
        content += "\n\n---\n\n" + attachment

    new_messages: list[Message] = [Message(role="user", content=content)]

    # role 衔接：若 tail 首条是 user，插入 assistant 占位
    if keep_tail and keep_tail[0].role == "user":
        new_messages.append(
            Message(
                role="assistant",
                content="（已加载上下文摘要与恢复信息。请继续。）",
            )
        )

    new_messages.extend(keep_tail)

    # ── Step 8: 替换历史 + 清理 ─────────────────────────────────
    conversation.replace_history(new_messages)
    _cleanup_spill_dir(session)

    after_tokens = conversation.current_tokens()

    if not manual:
        auto_tracking.record_success()

    _logger.info(
        "压缩完成：token %d → %d（触发方式: %s）",
        before_tokens,
        after_tokens,
        "manual" if manual else "auto",
    )

    return before_tokens, after_tokens


def _cleanup_spill_dir(session: SessionContext) -> None:
    """清理落盘目录中已不再被引用的文件。"""
    spill = Path(session.spill_dir)
    if spill.exists():
        for f in spill.iterdir():
            try:
                f.unlink()
            except OSError:
                pass


# ── group_by_user_turn（供外部使用）────────────────────────────────────


def group_by_user_turn(msgs: list[Message]) -> list[list[Message]]:
    """按用户轮次分组。"""
    return _group_messages_by_turn(msgs)


# ── pick_recent_tail（供外部使用）─────────────────────────────────────


def pick_recent_tail(msgs: list[Message]) -> list[Message]:
    """返回近期原文尾部。"""
    start = _compute_keep_start_index(msgs)
    if start <= 0:
        return list(msgs)
    return list(msgs[start:])

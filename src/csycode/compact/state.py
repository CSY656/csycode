"""上下文管理的长生命周期状态容器。

包含:
  - SessionContext: 会话生命周期信息
  - ContentReplacementState: 工具结果替换决策账本
  - CompactCircuitBreaker: 自动摘要熔断器
  - FileReadRecord / RecoveryState: 最近读过的文件追踪
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES

_logger = logging.getLogger(__name__)


# ── SessionContext ────────────────────────────────────────────────────────


def _new_session_id() -> str:
    """生成一次性的会话 id，格式为 YYYYMMDD-HHMMSS-<16hex>。

    使用 8 字节（16 hex 字符）随机后缀，碰撞概率约 2^-64，
    远低于原来 2 字节（4 hex 字符）的 2^-16。
    """
    try:
        hex_str = secrets.token_hex(8)
    except Exception:
        import random
        import time as _time

        _logger.warning("secrets.token_hex 失败，降级到 random")
        # 用时间戳 + os.urandom 混合种子，避免同一秒内多进程产生相同的伪随机序列
        try:
            seed_bytes = os.urandom(16)
        except Exception:
            seed_bytes = str(_time.time()).encode()
        hex_str = random.Random(seed_bytes).randbytes(8).hex()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{hex_str}"


def parse_session_time(session_id: str) -> datetime | None:
    """从 session ID 前 15 位解析 YYYYMMDD-HHMMSS 时间戳。

    返回 UTC naive datetime，解析失败返回 None。
    """
    try:
        prefix = session_id[:15]  # "YYYYMMDD-HHMMSS"
        return datetime.strptime(prefix, "%Y%m%d-%H%M%S")
    except (ValueError, IndexError):
        return None


@dataclass
class SessionContext:
    """会话生命周期信息。session_id 进程启动时一次性生成。"""

    session_id: str
    spill_dir: str
    session_dir: str = ""  # ch09: <workspace>/.csycode/sessions/<session_id>


def new_session_context(workspace: str) -> SessionContext:
    """创建新的会话上下文。

    在 .csycode/sessions/<session_id>/ 下创建目录结构。
    若目录已存在（ID 碰撞），自动重试一次，最多尝试 3 次。
    """
    max_retries = 3
    last_error: Exception | None = None

    for _ in range(max_retries):
        session_id = _new_session_id()
        session_dir = str(Path(workspace) / ".csycode" / "sessions" / session_id)
        spill_dir = os.path.join(session_dir, "tool-results")
        try:
            Path(spill_dir).mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            last_error = FileExistsError(
                f"会话 ID 碰撞: {session_id} 已存在，重试中"
            )
            _logger.warning(str(last_error))
            continue
        return SessionContext(
            session_id=session_id, spill_dir=spill_dir, session_dir=session_dir
        )

    raise RuntimeError(
        f"无法创建唯一会话 ID（{max_retries} 次重试后仍碰撞）"
    ) from last_error


def open_session_context(workspace: str, session_id: str) -> SessionContext:
    """打开已有会话的上下文（不创建目录）。

    Raises:
        FileNotFoundError: 会话目录不存在。
    """
    session_dir = str(Path(workspace) / ".csycode" / "sessions" / session_id)
    if not Path(session_dir).is_dir():
        raise FileNotFoundError(f"会话目录不存在: {session_dir}")
    spill_dir = os.path.join(session_dir, "tool-results")
    return SessionContext(
        session_id=session_id, spill_dir=spill_dir, session_dir=session_dir
    )


# ── ContentReplacementState ───────────────────────────────────────────────


class ContentReplacementState:
    """会话级的"工具结果替换决策账本"。

    _seen_ids 记录已经决策过的 tool_use_id，无论决策是替换还是保留原文。
    _replacements 只保存"决定替换"那一支的预览字符串，键是 tool_use_id。
    同一个 tool_use_id 一旦进入 _seen_ids 就再也不会被重新评估，保证 prompt cache 稳定。

    并发安全：Python asyncio 单线程事件循环保证串行，无需显式锁。
    对外只暴露一个高层方法 decide_once 让调用方传入决策回调，
    由本类型内部保证"查账本 → 决策 → 写账本"的原子性。
    """

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        self._replacements: dict[str, str] = {}

    def decide_once(
        self,
        tool_use_id: str,
        original: str,
        decide: Callable[[], tuple[str, str]],
    ) -> str:
        """一次性完成"查账本 → 决策 → 写账本"原子操作。

        若 id 已 Seen：直接返回账本中存量结果（kept 返回原 content，
        replaced 返回 _replacements[id]）。

        若 id 未 Seen：调 decide() 回调（仍持锁）：
          - 回调返回 ("kept", _)：写 _seen_ids，不写 _replacements；返回原 content。
          - 回调返回 ("replaced", preview)：写 _seen_ids + _replacements；返回 preview。
          - 回调返回 ("skip", _)：既不写 _seen_ids 也不写 _replacements；返回原
            content（下一轮重试）。

        Args:
            tool_use_id: 工具调用的唯一标识。
            original: 原始工具结果内容。
            decide: 决策回调，返回 (decision, preview) 元组。

        Returns:
            根据决策返回原始内容或预览字符串。
        """
        # 已 Seen：直接返回存量结果
        if tool_use_id in self._seen_ids:
            return self._replacements.get(tool_use_id, original)

        # 未 Seen：调回调做决策
        decision, preview = decide()

        if decision == "kept":
            self._seen_ids.add(tool_use_id)
            return original
        elif decision == "replaced":
            self._seen_ids.add(tool_use_id)
            self._replacements[tool_use_id] = preview
            return preview
        else:  # "skip"
            return original


# ── CompactCircuitBreaker ─────────────────────────────────────────────────


class CompactCircuitBreaker:
    """自动摘要连续失败熔断器。

    手动 / 紧急压缩路径不读这个类。
    并发安全：Python asyncio 单线程事件循环保证串行，无需显式锁。
    """

    def __init__(self) -> None:
        self._consecutive_failures = 0

    def record_success(self) -> None:
        """记录一次成功，连续失败计数清零。"""
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """记录一次失败，连续失败计数 +1。"""
        self._consecutive_failures += 1

    def tripped(self) -> bool:
        """熔断是否触发（连续失败 >= 阈值）。"""
        return self._consecutive_failures >= MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


# ── RecoveryState ─────────────────────────────────────────────────────────


@dataclass
class FileReadRecord:
    """单次文件读取记录。"""

    path: str  # 文件绝对路径
    content: str  # 不带行号前缀的纯净字节内容
    timestamp: datetime  # 最后一次成功读取的时间


class RecoveryState:
    """Agent 主循环写、compact 摘要时读的文件追踪状态。

    _files 的键是文件绝对路径，避免相对路径在不同 cwd 下错乱。
    并发安全：Python asyncio 单线程事件循环保证串行，无需显式锁。
    """

    def __init__(self) -> None:
        self._files: dict[str, FileReadRecord] = {}
        self._skills: list[SkillInvocationRecord] = []

    def record_file(self, path: str, content: str) -> None:
        """记录一次成功的文件读取。

        Args:
            path: 文件路径（如果不是绝对路径，会自动 resolve）。
            content: 文件内容的纯净字节（不带行号前缀）。
        """
        # 确保使用绝对路径
        abs_path = str(Path(path).resolve())
        self._files[abs_path] = FileReadRecord(
            path=abs_path,
            content=content,
            timestamp=datetime.now(timezone.utc),
        )

    def record_skill_invocation(self, name: str, prompt_body: str) -> None:
        """记录一次 Skill 调用（对齐 mewcode recovery_state.record_skill_invocation）。"""
        self._skills.append(SkillInvocationRecord(
            name=name,
            prompt_body=prompt_body,
            timestamp=datetime.now(timezone.utc),
        ))

    def snapshot(self) -> list[FileReadRecord]:
        """返回按 timestamp 倒序排序的拷贝列表。

        返回的是浅拷贝列表，FileReadRecord 字段都是不可变类型，
        调用方不会影响内部状态。
        """
        records = list(self._files.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records

    def skill_snapshot(self) -> list[SkillInvocationRecord]:
        """返回已调用的 Skill 记录列表（按 timestamp 倒序）。"""
        return sorted(self._skills, key=lambda r: r.timestamp, reverse=True)


@dataclass
class SkillInvocationRecord:
    """单次 Skill 调用记录。"""
    name: str
    prompt_body: str
    timestamp: datetime

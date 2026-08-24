"""权限引擎 —— 前四层防御流水线的核心实现。

提供:
  - Engine 数据类（持有 root、黑名单、三级规则集、本地层路径、启动模式）
  - new_engine: 构造引擎，加载三层配置，确定启动模式
  - Engine.check: 前四层短路流水线（黑名单→沙箱→规则→模式兜底）
  - mode_fallback: 模式 × 类别 → Allow/Ask 矩阵
  - start_mode: 返回引擎的启动默认模式

第五层（人在回路）由 agent 模块在 Ask 信号后驱动，不在 Engine 中实现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .blacklist import hits_blacklist
from .sandbox import resolve_root, sandbox_ok
from .settings import (
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)

if TYPE_CHECKING:
    from csycode.llm import ToolCall
    from . import Category, Decision, Mode
    from .rule import RuleSet
    from .settings import Settings


@dataclass
class Engine:
    """权限引擎 —— 持有所有判定所需的状态。

    Attributes:
        root: 项目根（绝对路径，已解析符号链接）。
        blacklist: 编译好的危险命令正则列表（不可配，N1）。
        user: 用户级规则集（~/.csycode/settings.yaml）。
        project: 项目级规则集（<root>/.csycode/settings.yaml）。
        local: 本地级规则集（<root>/.csycode/settings.local.yaml）。
        local_path: 本地层持久化文件路径。
        start_mode: 启动默认模式（从配置中解析，无则 DEFAULT）。
        _session_allowed: 会话级内存放行集合（对齐 mewcode _session_allowed）。
            用户选"本次会话不再询问"时将 (tool_name, target) 加入此集合，
            后续相同操作直接放行，不持久化到磁盘。
    """

    root: str
    blacklist: list[re.Pattern]
    user: "RuleSet"
    project: "RuleSet"
    local: "RuleSet"
    local_path: str
    start_mode: "Mode"
    _session_allowed: set[tuple[str, str]] | None = None

    def session_allow(self, tool_name: str, target: str) -> None:
        """将 (tool_name, target) 加入会话级放行集合。"""
        if self._session_allowed is None:
            self._session_allowed = set()
        self._session_allowed.add((tool_name, target))

    def session_allow_tc(self, call: "ToolCall") -> None:
        """从 ToolCall 提取 key 并加入会话级放行集合（对齐 mewcode add_session_allow）。

        将 (friendly_name, extract_target) 作为 key 存入 _session_allowed，
        与 _check_impl 第 ③.5 层的查询 key 一致，确保后续相同操作直接放行。

        Args:
            call: 被允许的工具调用。
        """
        friendly = friendly_name(call.name)
        target, _is_file, _ok = extract_target(call)
        if target:
            self.session_allow(friendly, target)

    def is_session_allowed(self, tool_name: str, target: str) -> bool:
        """检查 (tool_name, target) 是否已在会话级放行集合中。"""
        if self._session_allowed is None:
            return False
        return (tool_name, target) in self._session_allowed


# ── 引擎构造 ────────────────────────────────────────────────────────────


def new_engine(root: str) -> "tuple[Engine, Exception | None]":
    """构造权限引擎，加载三层配置。

    构造过程：
      1. 解析项目根（resolve_root）
      2. 加载三层配置：user → project → local
      3. 确定启动默认模式（local > project > user > DEFAULT）
      4. 组装 Engine 实例

    容错设计（N5）：
      - resolve_root 失败：仍返回非 None 引擎（root 退化为传入值、四层规则空、
        start_mode=DEFAULT）+ err
      - 配置文件缺失/格式错误：降级跳过该文件（视为空），不抛致命异常

    Args:
        root: 项目根路径字符串。

    Returns:
        (Engine, err): Engine 一定非 None；err 仅在 resolve_root 失败时非 None。
    """
    from . import Mode

    # 1. 解析项目根
    resolved_root: str
    fatal_err: Exception | None = None
    try:
        resolved_root = resolve_root(root)
    except Exception as e:
        resolved_root = str(Path(root).expanduser().absolute())
        fatal_err = e

    # 2. 加载三层配置
    home = Path.home()
    user_path = str(home / ".csycode" / "settings.yaml")
    project_path = str(Path(resolved_root) / ".csycode" / "settings.yaml")
    local_path = str(Path(resolved_root) / ".csycode" / "settings.local.yaml")

    user_settings = _load_or_empty(user_path)
    project_settings = _load_or_empty(project_path)
    local_settings = _load_or_empty(local_path)

    user_rs = to_rule_set(user_settings)
    project_rs = to_rule_set(project_settings)
    local_rs = to_rule_set(local_settings)

    # 3. 确定启动默认模式：local > project > user > DEFAULT
    start_mode_val = Mode.DEFAULT
    for s in (local_settings, project_settings, user_settings):
        if s.default_mode:
            m, ok = _parse_mode_for_engine(s.default_mode)
            if ok:
                start_mode_val = m
                break  # 按优先级取第一个有效的（local > project > user）

    # 4. 组装引擎
    from .blacklist import _BLACKLIST  # 编译好的黑名单

    engine = Engine(
        root=resolved_root,
        blacklist=list(_BLACKLIST),
        user=user_rs,
        project=project_rs,
        local=local_rs,
        local_path=local_path,
        start_mode=start_mode_val,
    )

    return (engine, fatal_err)


def _load_or_empty(path: str) -> "Settings":
    """加载单个层级配置，失败时降级为空 Settings（不抛异常）。"""
    try:
        return load_settings(path)
    except SettingsError:
        from .settings import Settings

        return Settings()


def _parse_mode_for_engine(s: str) -> "tuple[Mode, bool]":
    """引擎内部使用的模式名解析（延迟导入 Mode 避免循环）。"""
    from . import parse_mode

    return parse_mode(s)


# ── 模式兜底矩阵 ────────────────────────────────────────────────────────


def mode_fallback(mode: "Mode", cat: "Category") -> "Decision":
    """模式兜底矩阵（F5）。

    矩阵：
      Category.READ                    → ALLOW（只读永远放行）
      Mode.BYPASS                      → ALLOW（bypass 全放行）
      Mode.ACCEPT_EDITS + Category.WRITE → ALLOW（接受编辑 = 写放行）
      其余（DEFAULT/PLAN + WRITE/EXEC、ACCEPT_EDITS + EXEC）→ ASK

    注意：本函数**只产 Allow/Ask**，不产 Deny。
    Deny 仅来自黑名单、沙箱或 deny 规则。

    Args:
        mode: 当前权限模式。
        cat: 操作类别。

    Returns:
        Decision.ALLOW 或 Decision.ASK。
    """
    from . import Category, Decision, Mode

    # 只读永远放行
    if cat == Category.READ:
        return Decision.ALLOW

    # BYPASS 模式全放行
    if mode == Mode.BYPASS:
        return Decision.ALLOW

    # ACCEPT_EDITS 模式下写操作放行
    if mode == Mode.ACCEPT_EDITS and cat == Category.WRITE:
        return Decision.ALLOW

    # 其余 → 需确认（DEFAULT/PLAN + WRITE/EXEC、ACCEPT_EDITS + EXEC）
    return Decision.ASK


# ── 前四层判定流水线 ────────────────────────────────────────────────────


def _check_impl(
    engine: "Engine", mode: "Mode", call: "ToolCall", read_only: bool
) -> "tuple[Decision, str]":
    """前四层权限判定（短路流水线）—— 实现体。

    作为 Engine.check 方法和模块级 check 函数的共享实现。
    """
    from . import Category, Decision

    # 预处理
    cat = categorize(call.name, read_only)
    friendly = friendly_name(call.name)
    target, is_file, ok = extract_target(call)

    # 防御：extract_target 可能返回 None target（无参工具调用）
    if target is None:
        target = ""

    # ── ① 黑名单 ──────────────────────────────────────
    if cat == Category.EXEC and target != "" and hits_blacklist(target):
        # 截断过长命令片段
        display = target[:80] + "…" if len(target) > 80 else target
        return (Decision.DENY, f"命中危险命令黑名单：{display}")

    # ── ② 安全命令白名单 ──────────────────────────────
    # 在黑名单之后检查：如果命令只读且无 shell 元字符，直接放行。
    # 注意：BYPASS 模式下黑名单仍生效（已在上一层拦截），但白名单可
    # 减少 DEFAULT/PLAN 模式下对安全命令（ls、cat、git status 等）的弹窗。
    from .blacklist import is_safe_command

    if cat == Category.EXEC and target != "" and is_safe_command(target):
        return (Decision.ALLOW, f"安全命令（白名单）: {target[:60]}")

    # ── ③ 沙箱 ────────────────────────────────────────
    if is_file:
        if not ok:
            return (Decision.DENY, "无法解析文件路径参数，安全拒绝")
        if not sandbox_ok(engine, target):
            return (Decision.DENY, f"路径在项目目录之外：{target}")

    # ── ③ 规则引擎（local → project → user）────────────
    # 每层: match(friendly, target)，命中即返回
    for layer_name, rule_set in [
        ("local", engine.local),
        ("project", engine.project),
        ("user", engine.user),
    ]:
        decision, hit = rule_set.match(friendly, target)
        if hit:
            if decision == Decision.DENY:
                # 找具体是哪条 deny 规则命中
                rule_desc = _find_matching_rule(rule_set, friendly, target, deny=True)
                return (Decision.DENY, f"匹配 {layer_name} deny 规则：{rule_desc}")
            else:
                # Allow 命中，直接放行（不提示）
                return (Decision.ALLOW, "")

    # ── ③.5 会话级内存放行（对齐 mewcode _session_allowed）──
    # 用户在 HITL 中选"本次会话不再询问"后，后续相同操作直接放行。
    if engine.is_session_allowed(friendly, target):
        return (Decision.ALLOW, "")

    # ── ④ 模式兜底矩阵 ──────────────────────────────────
    fallback = mode_fallback(mode, cat)
    if fallback == Decision.ASK:
        mode_str = str(mode)
        cat_str = {0: "只读", 1: "文件写入", 2: "命令执行"}.get(int(cat), "未知")
        reason = f"{mode_str} 模式下 {cat_str} 类操作需确认"
        return (Decision.ASK, reason)

    return (Decision.ALLOW, "")


# 挂到 Engine 上作为方法
def _engine_check(
    self: "Engine", mode: "Mode", call: "ToolCall", read_only: bool
) -> "tuple[Decision, str]":
    """Engine.check 方法：前四层权限判定流水线。"""
    return _check_impl(self, mode, call, read_only)


Engine.check = _engine_check  # type: ignore[attr-defined]


def _engine_persist_local_allow(self: "Engine", call: "ToolCall") -> None:
    """Engine.persist_local_allow 方法：永久放行规则写入。"""
    from .persist import persist_local_allow

    persist_local_allow(self, call)


Engine.persist_local_allow = _engine_persist_local_allow  # type: ignore[attr-defined]


def check(
    engine: "Engine", mode: "Mode", call: "ToolCall", read_only: bool
) -> "tuple[Decision, str]":
    """模块级函数：前四层权限判定（委托到 _check_impl）。"""
    return _check_impl(engine, mode, call, read_only)


def _find_matching_rule(
    rule_set: "RuleSet", friendly: str, target: str, *, deny: bool = True
) -> str:
    """在 rule_set 中找第一个匹配的规则描述（供错误消息）。"""
    from .rule import match_rule

    rules = rule_set.deny if deny else rule_set.allow
    for rule in rules:
        if rule.tool == friendly and match_rule(rule, target):
            # 优先用 raw，否则用 matcher 的字符串表示
            desc = rule.raw or str(rule.matcher) if rule.matcher else rule.tool
            if rule.matcher is not None:
                return f"{rule.tool}({desc})"
            return rule.tool
    return friendly


# ── 启动模式 ────────────────────────────────────────────────────────────


def start_mode(engine: Engine) -> "Mode":
    """返回引擎的启动默认模式。"""
    return engine.start_mode

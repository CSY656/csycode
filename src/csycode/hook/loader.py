"""Hook 配置加载器 —— YAML 解析、字段校验、双层合并。

ch12: 扫描项目级和用户级 hooks.yaml，解析校验后构造 Engine。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from csycode.permission.matcher import compile_matcher as _compile_matcher

from .event import is_blocking, parse_event
from .rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    HookRule,
    HttpAction,
    PromptAction,
    ShellAction,
    SubagentAction,
)


# ── 时长解析 ──────────────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smh]?)$")


def _parse_duration(s: object) -> float | None:
    """解析时长字符串，支持 30s / 5m / 1h / 纯数字秒。

    Returns:
        秒数（float），解析失败返回 None。
    """
    if isinstance(s, (int, float)):
        return float(s)
    if not isinstance(s, str):
        return None
    s = s.strip()
    m = _DURATION_RE.match(s)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2)
    if unit == "m":
        return val * 60.0
    if unit == "h":
        return val * 3600.0
    return val


# ── 主入口 ────────────────────────────────────────────────────────────────


def load(project_root: str | Path):
    """加载两层 hooks.yaml 并构造 Engine。

    文件位置:
      - 项目级: <project_root>/.csycode/hooks.yaml
      - 用户级: ~/.csycode/hooks.yaml

    两层规则叠加合并，同名冲突时跳过后者。

    Args:
        project_root: 项目根目录。

    Returns:
        Engine 实例（含已加载的所有规则和来源文件列表）。
    """
    from .engine import Engine

    project_root = Path(project_root).resolve()
    project_path = project_root / ".csycode" / "hooks.yaml"
    user_path = Path.home() / ".csycode" / "hooks.yaml"

    sources: list[str] = []
    all_rules: list[HookRule] = []
    seen_names: set[str] = set()

    # 先加载项目级，再加载用户级
    for path, label in [(project_path, "project"), (user_path, "user")]:
        if not path.is_file():
            continue
        rules, path_sources = _load_file(path, label, seen_names)
        all_rules.extend(rules)
        sources.extend(path_sources)
        seen_names.update(r.name for r in rules)

    return Engine(rules=all_rules, sources=sources)


def _load_file(
    path: Path, label: str, seen_names: set[str]
) -> tuple[list[HookRule], list[str]]:
    """加载单个 YAML 文件并解析 hook 列表。

    Args:
        path: YAML 文件路径。
        label: 来源标签（"project" / "user"）。
        seen_names: 已见过的 hook name（用于冲突检测）。

    Returns:
        (规则列表, 来源路径列表)。所有错误走 stderr 不抛异常。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, PermissionError):
        return ([], [])

    if not raw.strip():
        return ([], [])

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        print(f"[hook] YAML parse error in {path}: {e}", file=sys.stderr)
        return ([], [])

    if data is None:
        return ([], [])

    if not isinstance(data, dict):
        print(f"[hook] invalid top-level structure in {path}: expected dict", file=sys.stderr)
        return ([], [])

    hooks_raw = data.get("hooks")
    if hooks_raw is None:
        return ([], [])

    if not isinstance(hooks_raw, list):
        print(f"[hook] 'hooks' field in {path} is not a list, skipping file", file=sys.stderr)
        return ([], [])

    rules: list[HookRule] = []
    source_str = str(path)

    for idx, raw_hook in enumerate(hooks_raw):
        if not isinstance(raw_hook, dict):
            print(
                f"[hook] item #{idx + 1} in {source_str} is not a dict, skipping",
                file=sys.stderr,
            )
            continue

        rule = _compile_hook(raw_hook, source_str, idx + 1)
        if rule is None:
            continue

        # 冲突检测
        if rule.name in seen_names:
            print(
                f'[hook] name conflict: "{rule.name}" already loaded, '
                f"skipping duplicate from {source_str}",
                file=sys.stderr,
            )
            continue

        rules.append(rule)

    return (rules, [source_str])


def _compile_hook(
    raw: dict[str, Any], source: str, idx: int
) -> HookRule | None:
    """从单个 YAML dict 编译 HookRule。

    Returns:
        HookRule 或 None（加载错误时 stderr 输出并跳过）。
    """
    # ── name（必填）────────────────────────────────────────────
    name = raw.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        print(
            f"[hook] item #{idx} in {source}: missing or empty 'name', skipping",
            file=sys.stderr,
        )
        return None
    name = name.strip()

    # ── event（必填）───────────────────────────────────────────
    event_str = raw.get("event")
    if not event_str or not isinstance(event_str, str):
        print(
            f'[hook] "{name}": missing or empty "event", skipping',
            file=sys.stderr,
        )
        return None
    event = parse_event(event_str.strip())
    if event is None:
        print(
            f'[hook] "{name}": unknown event "{event_str.strip()}", skipped',
            file=sys.stderr,
        )
        return None

    # ── action（必填）──────────────────────────────────────────
    action_raw = raw.get("action")
    if not isinstance(action_raw, dict):
        print(
            f'[hook] "{name}": missing or invalid "action", skipping',
            file=sys.stderr,
        )
        return None
    action = _compile_action(action_raw, name, source)
    if action is None:
        return None

    # ── if（可选）──────────────────────────────────────────────
    condition: Condition | None = None
    if_raw = raw.get("if")
    if if_raw is not None:
        if not isinstance(if_raw, dict):
            print(
                f'[hook] "{name}": "if" is not a dict, skipping hook',
                file=sys.stderr,
            )
            return None
        condition = _compile_condition(if_raw, name, source)
        if condition is None:
            return None

    # ── only_once（可选）───────────────────────────────────────
    only_once = raw.get("only_once", False)
    if not isinstance(only_once, bool):
        only_once = False

    # ── async（可选）───────────────────────────────────────────
    asyncio_mode = raw.get("async", False)
    if not isinstance(asyncio_mode, bool):
        asyncio_mode = False

    # ── async + 拦截事件冲突校验 ─────────────────────────────
    if asyncio_mode and is_blocking(event):
        print(
            f'[hook] "{name}": async not allowed for blocking events, skipped',
            file=sys.stderr,
        )
        return None

    # ── timeout（可选）─────────────────────────────────────────
    timeout_s = 30.0
    raw_timeout = raw.get("timeout")
    if raw_timeout is not None:
        parsed = _parse_duration(raw_timeout)
        if parsed is None:
            print(
                f'[hook] "{name}": invalid timeout "{raw_timeout}", using default 30s',
                file=sys.stderr,
            )
        elif parsed <= 0:
            print(
                f'[hook] "{name}": timeout must be positive, using default 30s',
                file=sys.stderr,
            )
        else:
            timeout_s = parsed

    return HookRule(
        name=name,
        event=event,
        action=action,
        condition=condition,
        only_once=only_once,
        asyncio_mode=asyncio_mode,
        timeout_s=timeout_s,
        source=source,
    )


def _compile_action(
    raw: dict[str, Any], hook_name: str, source: str
) -> Action | None:
    """编译动作对象。"""
    action_type_str = raw.get("type")
    if not action_type_str or not isinstance(action_type_str, str):
        print(
            f'[hook] "{hook_name}": action missing "type", skipping',
            file=sys.stderr,
        )
        return None

    try:
        action_type = ActionType(action_type_str.strip())
    except ValueError:
        print(
            f'[hook] "{hook_name}": unknown action type '
            f'"{action_type_str.strip()}", skipping',
            file=sys.stderr,
        )
        return None

    if action_type == ActionType.SHELL:
        command = raw.get("command")
        if not command or not isinstance(command, str):
            print(
                f'[hook] "{hook_name}": shell action missing "command", skipping',
                file=sys.stderr,
            )
            return None
        return Action(type=action_type, shell=ShellAction(command=command.strip()))

    if action_type == ActionType.PROMPT:
        text = raw.get("text")
        if not text or not isinstance(text, str):
            print(
                f'[hook] "{hook_name}": prompt action missing "text", skipping',
                file=sys.stderr,
            )
            return None
        return Action(type=action_type, prompt=PromptAction(text=text))

    if action_type == ActionType.HTTP:
        url = raw.get("url")
        if not url or not isinstance(url, str):
            print(
                f'[hook] "{hook_name}": http action missing "url", skipping',
                file=sys.stderr,
            )
            return None
        method = raw.get("method", "POST")
        if not isinstance(method, str):
            method = "POST"
        headers = raw.get("headers", {})
        if not isinstance(headers, dict):
            headers = {}
        # 确保 header value 都是字符串
        headers = {str(k): str(v) for k, v in headers.items()}
        body = raw.get("body")
        if body is not None and not isinstance(body, str):
            body = str(body)
        return Action(
            type=action_type,
            http=HttpAction(
                url=url.strip(),
                method=method.strip().upper(),
                headers=headers,
                body=body,
            ),
        )

    if action_type == ActionType.SUBAGENT:
        agent_name = raw.get("agent_name")
        if not agent_name or not isinstance(agent_name, str):
            print(
                f'[hook] "{hook_name}": subagent action missing '
                f'"agent_name", skipping',
                file=sys.stderr,
            )
            return None
        prompt = raw.get("prompt")
        if not prompt or not isinstance(prompt, str):
            print(
                f'[hook] "{hook_name}": subagent action missing '
                f'"prompt", skipping',
                file=sys.stderr,
            )
            return None
        return Action(
            type=action_type,
            subagent=SubagentAction(
                agent_name=agent_name.strip(),
                prompt=prompt.strip(),
            ),
        )

    return None


def _compile_condition(
    raw: dict[str, Any], hook_name: str, source: str
) -> Condition | None:
    """编译条件表达式。

    YAML 结构:
      if:
        all_of:  # 或 any_of（二选一，不可同时出现）
          - field: tool_name
            match:
              type: exact
              value: write_file
    """
    has_all = "all_of" in raw
    has_any = "any_of" in raw

    if has_all and has_any:
        print(
            f'[hook] "{hook_name}": "if" cannot have both '
            f'"all_of" and "any_of", skipping hook',
            file=sys.stderr,
        )
        return None

    if not has_all and not has_any:
        print(
            f'[hook] "{hook_name}": "if" must have "all_of" or "any_of", '
            f"skipping hook",
            file=sys.stderr,
        )
        return None

    mode = CombineMode.ALL_OF if has_all else CombineMode.ANY_OF
    atoms_raw = raw["all_of"] if has_all else raw["any_of"]

    if not isinstance(atoms_raw, list) or not atoms_raw:
        print(
            f'[hook] "{hook_name}": "if.{mode.value}" must be a non-empty list, '
            f"skipping hook",
            file=sys.stderr,
        )
        return None

    atoms: list[AtomCondition] = []
    for i, atom_raw in enumerate(atoms_raw):
        if not isinstance(atom_raw, dict):
            print(
                f'[hook] "{hook_name}": if.{mode.value}[{i}] is not a dict, '
                f"skipping hook",
                file=sys.stderr,
            )
            return None

        field = atom_raw.get("field")
        if not field or not isinstance(field, str):
            print(
                f'[hook] "{hook_name}": if.{mode.value}[{i}] missing '
                f'"field", skipping hook',
                file=sys.stderr,
            )
            return None

        match_raw = atom_raw.get("match")
        if not isinstance(match_raw, dict):
            print(
                f'[hook] "{hook_name}": if.{mode.value}[{i}] missing '
                f'"match" dict, skipping hook',
                file=sys.stderr,
            )
            return None

        matcher = _compile_match(match_raw)
        if matcher is None:
            print(
                f'[hook] "{hook_name}": if.{mode.value}[{i}] match '
                f"compile failed, skipping hook",
                file=sys.stderr,
            )
            return None

        atoms.append(AtomCondition(field=field.strip(), matcher=matcher))

    return Condition(mode=mode, atoms=atoms)


def _compile_match(raw: dict[str, Any]) -> "object | None":
    """从 YAML dict 编译 Matcher。

    YAML 格式:
      {type: exact, value: "write_file"}
      {type: glob, value: "**/*.py"}
      {type: regex, value: "(?i)delete"}
      {type: not, inner: {type: exact, value: "foo"}}

    Hook 上下文中的 match 作用于 payload 字段值，统一传 is_command=False。
    """
    match_type = raw.get("type")
    if not match_type or not isinstance(match_type, str):
        return None
    match_type = match_type.strip()

    if match_type == "not":
        inner_raw = raw.get("inner")
        if not isinstance(inner_raw, dict):
            return None
        inner = _compile_match(inner_raw)
        if inner is None:
            return None
        from csycode.permission.matcher import NotMatcher
        return NotMatcher(inner)

    value = raw.get("value")
    if value is None or not isinstance(value, str):
        return None

    # 构造 compile_matcher 的前缀串
    if match_type == "exact":
        prefix = f"={value}"
    elif match_type == "regex":
        prefix = f"~{value}"
    elif match_type == "glob":
        prefix = value  # 无前缀
    else:
        return None

    try:
        return _compile_matcher(prefix, is_command=False)
    except ValueError:
        return None

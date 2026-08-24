"""Hook 动作执行器 —— shell / prompt / http / subagent 四类动作。

ch12: 提供 Executor 类，负责执行 hook 动作并返回 ExecutionResult。
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .rule import Action, HookRule, Payload


@dataclass
class ExecutionResult:
    """单次 Hook 动作的执行结果。

    Attributes:
        blocked: 是否拦截（仅拦截类事件下有意义）。
        reason: 拦截原因（blocked=True 时）。
        prompt: prompt 动作的注入文本。
        err: hook 自身失败时的异常（不拦截，仅记日志）。
    """
    blocked: bool = False
    reason: str = ""
    prompt: str = ""
    err: Exception | None = None


def _marshal_sorted(payload: "Payload") -> bytes:
    """将 payload 序列化为 JSON（key 字典序），方便脚本 grep。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


class Executor:
    """Hook 动作执行器。

    四类动作:
      - shell: asyncio.create_subprocess_shell + stdin JSON
      - prompt: 直接返回文本
      - http: httpx POST + decision=block 解析
      - subagent: 占位 stderr 日志
    """

    def __init__(self) -> None:
        self._http_client: httpx.AsyncClient | None = None

    def _get_http_client(self) -> httpx.AsyncClient:
        """延迟创建 httpx 客户端（复用连接池）。"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def run(
        self,
        rule: "HookRule",
        payload: "Payload",
        *,
        blocking: bool,
    ) -> ExecutionResult:
        """根据 rule.action.type 分发到对应执行器。

        Args:
            rule: Hook 规则。
            payload: 事件 payload。
            blocking: 是否处于拦截类事件上下文。

        Returns:
            ExecutionResult。
        """
        action = rule.action
        timeout = rule.timeout_s

        if action.type == "shell" and action.shell is not None:
            return await self._run_shell(
                action.shell.command, payload, blocking, timeout
            )
        if action.type == "prompt" and action.prompt is not None:
            return ExecutionResult(prompt=action.prompt.text)
        if action.type == "http" and action.http is not None:
            return await self._run_http(action, payload, blocking, timeout)
        if action.type == "subagent" and action.subagent is not None:
            return self._run_subagent(action.subagent.agent_name)

        return ExecutionResult(
            err=RuntimeError(f"unknown action type: {action.type}")
        )

    # ── shell ────────────────────────────────────────────────────────

    async def _run_shell(
        self,
        command: str,
        payload: "Payload",
        blocking: bool,
        timeout: float,
    ) -> ExecutionResult:
        """执行 shell 命令。

        - 通过 stdin 传入 payload JSON（key 字典序）
        - returncode == 2 且 blocking=True → 拦截
        - returncode == 0 → 放行
        - 其他非零 → hook 失败但不拦截
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            return ExecutionResult(err=e)

        try:
            payload_bytes = _marshal_sorted(payload)
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload_bytes), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return ExecutionResult(err=TimeoutError(f"shell timeout after {timeout}s"))

        code = proc.returncode

        if code == 0:
            return ExecutionResult()
        if blocking and code == 2:
            # 拦截信号：stderr 或 stdout 作为拒绝原因
            reason_bytes = stderr or stdout
            reason = reason_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
            return ExecutionResult(blocked=True, reason=reason)

        # 非零且非拦截 → hook 失败
        err_text = stderr.decode("utf-8", errors="replace").strip()
        return ExecutionResult(
            err=RuntimeError(f"exit {code}: {err_text}" if err_text else f"exit {code}")
        )

    # ── http ─────────────────────────────────────────────────────────

    async def _run_http(
        self,
        action: "Action",
        payload: "Payload",
        blocking: bool,
        timeout: float,
    ) -> ExecutionResult:
        """执行 HTTP 请求。

        - 默认 POST，body 缺省时用 payload JSON
        - 2xx + {"decision":"block","reason":"..."} → 拦截
        - 网络错 / 超时 / 解析失败 → hook 失败但不拦截
        """
        ha = action.http
        if ha is None:
            return ExecutionResult(err=RuntimeError("http action missing http field"))

        # 构建请求体
        if ha.body is not None:
            try:
                body = ha.body.format_map(payload)
            except (KeyError, ValueError) as e:
                return ExecutionResult(err=ValueError(f"template render failed: {e}"))
        else:
            body = json.dumps(payload, sort_keys=True, ensure_ascii=False)

        client = self._get_http_client()

        try:
            resp = await client.request(
                method=ha.method,
                url=ha.url,
                content=body,
                headers=ha.headers,
                timeout=timeout,
            )
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            return ExecutionResult(err=e)

        # 检查拦截信号
        if blocking and 200 <= resp.status_code < 300:
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                return ExecutionResult()  # 非 JSON → 放行
            if isinstance(data, dict) and data.get("decision") == "block":
                reason = str(data.get("reason", ""))
                return ExecutionResult(blocked=True, reason=reason)

        return ExecutionResult()

    # ── subagent ─────────────────────────────────────────────────────

    def _run_subagent(self, agent_name: str) -> ExecutionResult:
        """subagent 占位实现：仅 stderr 日志。"""
        print(
            f"[hook subagent] not yet implemented, skipped: {agent_name}",
            file=sys.stderr,
        )
        return ExecutionResult()

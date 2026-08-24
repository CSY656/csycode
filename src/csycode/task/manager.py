"""后台任务管理器 —— 对齐 mewcode agents/task_manager.py。

Manager 管理所有后台子 Agent 的生命周期：
- launch: 创建后台任务并异步跑 run_to_completion
- adopt_running: 接管一个已在前台启动的子 Agent 转到后台
- stop: 取消运行中的任务
- send_message: 向已完成的 Agent 续派任务（复用同一 conv）
- subscribe_done: 获取完成通知队列
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable, Awaitable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.agent.loop import Agent
    from csycode.conversation import Conversation

log = logging.getLogger(__name__)


# ── Status 枚举 ──────────────────────────────────────────────────


class Status(IntEnum):
    RUNNING = 0
    COMPLETED = 1
    FAILED = 2
    CANCELLED = 3

    def __str__(self) -> str:
        _map = {0: "running", 1: "completed", 2: "failed", 3: "cancelled"}
        return _map.get(int(self), "unknown")


# ── 数据结构 ─────────────────────────────────────────────────────


@dataclass
class BackgroundTask:
    """一个后台子 Agent 的完整状态快照。

    Attributes:
        id: 唯一标识（格式 "task_<8字节 hex>"）。
        name: 用户指定的名称（来自 Agent 工具 name 参数），可空。
        agent: 子 Agent 实例。
        conv: 子对话。
        task: 初始任务文本。
        status: 当前状态。
        result: 跑完后的最终文本。
        err: 异常信息（status=FAILED 时）。
        start_time: 启动时间戳（monotonic）。
        end_time: 结束时间戳（monotonic）。
        input_tokens: 累计输入 token。
        output_tokens: 累计输出 token。
        tool_count: 工具调用次数计数器。
        last_activity: 最近一次工具名。
    """

    id: str
    name: str
    agent: "Agent"
    conv: "Conversation"
    task: str
    status: Status = Status.RUNNING
    result: str = ""
    err: BaseException | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_count: int = 0
    last_activity: str = ""


# ── Manager ──────────────────────────────────────────────────────


class Manager:
    """后台任务生命周期管理器（协程安全，单事件循环）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._by_name: dict[str, str] = {}  # name → id
        self._done: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._async_tasks: dict[str, asyncio.Task[None]] = {}
        self._counter: int = 0
        # ch15: on_task_done 回调列表
        self._on_task_done_callbacks: list[Callable[[str], Awaitable[None]]] = []
        # ch15: AgentNameRegistry 引用
        self._name_reg: Any = None

    # ── ID 生成 ──────────────────────────────────────────────────

    def _next_id(self) -> str:
        self._counter += 1
        return f"task_{secrets.token_hex(4)}"

    # ── launch ───────────────────────────────────────────────────

    async def launch(
        self,
        agent: "Agent",
        conv: "Conversation",
        name: str,
        task_text: str,
    ) -> str:
        """启动一个后台子 Agent。

        Args:
            agent: 子 Agent 实例（已设置好 allowed_tools、system_prompt 等）。
            conv: 子对话（Fork 路径已预装填消息）。
            name: 用户指定的名称。
            task_text: 初始任务文本。

        Returns:
            task_id。
        """
        task_id = self._next_id()
        bt = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            conv=conv,
            task=task_text,
            status=Status.RUNNING,
        )

        async with self._lock:
            self._tasks[task_id] = bt
            if name:
                self._by_name[name] = task_id

        # 起协程异步跑
        async_task = asyncio.create_task(self._run_background(task_id))
        self._async_tasks[task_id] = async_task

        return task_id

    async def _run_background(self, task_id: str) -> None:
        """内部：在后台跑 run_to_completion 并写终态。"""
        bt = self._tasks.get(task_id)
        if bt is None:
            return

        try:
            events: asyncio.Queue = asyncio.Queue(maxsize=64)
            # 起聚合协程
            aggregator = asyncio.create_task(self._aggregate_events(events, bt))

            try:
                result = await bt.agent.run_to_completion(bt.conv, bt.task, events)
                bt.result = result
                bt.status = Status.COMPLETED
            finally:
                aggregator.cancel()
                try:
                    await aggregator
                except asyncio.CancelledError:
                    pass

        except asyncio.CancelledError:
            bt.status = Status.CANCELLED
            bt.result = "任务已取消"
        except BaseException as e:
            log.error("后台任务 %s 失败: %s", task_id, e)
            bt.status = Status.FAILED
            bt.err = e
            bt.result = f"Error: {e}"
        finally:
            bt.end_time = time.monotonic()
            bt.input_tokens = bt.agent.total_input_tokens
            bt.output_tokens = bt.agent.total_output_tokens
            self._async_tasks.pop(task_id, None)
            try:
                self._done.put_nowait(task_id)
            except asyncio.QueueFull:
                print(
                    f"task manager: done queue full, dropping notification for {task_id}",
                )
            # ch15: 触发 on_task_done 回调
            for cb in self._on_task_done_callbacks:
                try:
                    await cb(task_id)
                except Exception:
                    pass

    async def _aggregate_events(
        self, queue: asyncio.Queue, bt: BackgroundTask
    ) -> None:
        """聚合子 Agent 事件：统计 tool_count / last_activity。"""
        while True:
            try:
                event = await queue.get()
                if event is None:
                    break
                if isinstance(event, dict) and event.get("type") == "tool":
                    bt.tool_count += 1
                    bt.last_activity = event.get("name", "")
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    # ── adopt_running ────────────────────────────────────────────

    async def adopt_running(
        self,
        agent: "Agent",
        conv: "Conversation",
        name: str,
        events: asyncio.Queue,
        handle: asyncio.Task,
        task_text: str = "",
    ) -> str:
        """接管一个已在前台启动的子 Agent 转到后台。

        Args:
            agent: 子 Agent 实例。
            conv: 子对话。
            name: 用户指定的名称。
            events: 前台跑动期间的事件队列。
            handle: 前台跑动协程的 asyncio.Task。
            task_text: 任务描述。

        Returns:
            task_id。
        """
        task_id = self._next_id()
        bt = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            conv=conv,
            task=task_text,
            status=Status.RUNNING,
        )

        async with self._lock:
            self._tasks[task_id] = bt
            if name:
                self._by_name[name] = task_id

        self._async_tasks[task_id] = handle

        # 起聚合协程
        asyncio.create_task(self._adopt_watcher(task_id, events, handle))
        return task_id

    async def _adopt_watcher(
        self, task_id: str, events: asyncio.Queue, handle: asyncio.Task
    ) -> None:
        """监控被接管的子 Agent 直到完成。"""
        bt = self._tasks.get(task_id)
        if bt is None:
            return

        aggregator = asyncio.create_task(self._aggregate_events(events, bt))

        try:
            await handle
            bt.status = Status.COMPLETED
            bt.result = ""  # adopt 模式结果由调用方处理
        except asyncio.CancelledError:
            bt.status = Status.CANCELLED
        except BaseException as e:
            bt.status = Status.FAILED
            bt.err = e
            bt.result = f"Error: {e}"
        finally:
            bt.end_time = time.monotonic()
            bt.input_tokens = bt.agent.total_input_tokens
            bt.output_tokens = bt.agent.total_output_tokens
            aggregator.cancel()
            try:
                await aggregator
            except asyncio.CancelledError:
                pass
            self._async_tasks.pop(task_id, None)
            try:
                self._done.put_nowait(task_id)
            except asyncio.QueueFull:
                pass
            # ch15: 触发 on_task_done 回调
            for cb in self._on_task_done_callbacks:
                try:
                    await cb(task_id)
                except Exception:
                    pass

    # ── 查询 ─────────────────────────────────────────────────────

    def get(self, task_id: str) -> BackgroundTask | None:
        """按 ID 获取任务。"""
        return self._tasks.get(task_id)

    def list_all(self) -> list[BackgroundTask]:
        """返回所有任务（含已完成），按 start_time 升序。"""
        return sorted(self._tasks.values(), key=lambda t: t.start_time)

    def subscribe_done(self) -> asyncio.Queue[str]:
        """获取完成通知队列。

        后台任务完成时会把 task_id push 到此队列。
        TUI 消费此队列以注入 <task-notification>。
        """
        return self._done

    # ── stop ─────────────────────────────────────────────────────

    async def stop(self, task_id: str) -> bool:
        """取消指定任务。

        Returns:
            True 如果找到并触发了取消，False 如果任务不存在或已终止。
        """
        bt = self._tasks.get(task_id)
        if bt is None or bt.status != Status.RUNNING:
            return False

        async_task = self._async_tasks.get(task_id)
        if async_task and not async_task.done():
            async_task.cancel()
            return True
        return False

    # ── send_message ─────────────────────────────────────────────

    async def send_message(self, name: str, message: str) -> str:
        """向已完成的 Agent 续派任务。

        Args:
            name: Agent 的 name（来自 Agent 工具 name 参数）。
            message: 新任务文本。

        Returns:
            task_id。

        Raises:
            ValueError: 找不到 name 对应的任务或任务未完成。
        """
        task_id = self._by_name.get(name)
        if task_id is None:
            raise ValueError(f"找不到 name='{name}' 的任务")

        bt = self._tasks.get(task_id)
        if bt is None:
            raise ValueError(f"任务 {task_id} 不存在")

        if bt.status != Status.COMPLETED:
            raise ValueError(
                f"任务 {task_id} (name='{name}') 状态为 {bt.status}，"
                f"只有 completed 状态才能续派任务"
            )

        # 追加新消息到对话
        bt.conv.add_user(message)
        bt.status = Status.RUNNING
        bt.result = ""

        # 重新起协程跑动
        async_task = asyncio.create_task(self._run_background(task_id))
        self._async_tasks[task_id] = async_task

        return task_id

    # ── ch15: on_task_done 回调 ───────────────────────────────────

    def on_task_done(self, fn: Callable[[str], Awaitable[None]]) -> None:
        """注册任务完成回调。

        Args:
            fn: async 回调函数，接收 task_id 参数。
        """
        self._on_task_done_callbacks.append(fn)

    def set_name_registry(self, reg: Any) -> None:
        """设置 AgentNameRegistry 引用（ch15）。

        Args:
            reg: AgentNameRegistry 实例。
        """
        self._name_reg = reg

    def get_by_name_from_registry(self, name: str) -> str | None:
        """优先通过 registry 解析 name → agent_id。"""
        if self._name_reg is not None:
            return self._name_reg.resolve(name)
        return self._by_name.get(name)

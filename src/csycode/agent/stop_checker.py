"""停止条件判断模块。

StopChecker 追踪 Agent Loop 的运行时状态，判断是否满足任一停止条件。
"""

from __future__ import annotations


class StopChecker:
    """循环停止条件状态机。

    追踪 5 种停止条件：模型说完、迭代上限、用户取消、连续未知工具、流出错。
    """

    def __init__(
        self,
        max_rounds: int = 50,
        max_consecutive_unknown: int = 2,
    ) -> None:
        self._max_rounds = max_rounds
        self._max_consecutive_unknown = max_consecutive_unknown
        self._round_count: int = 0
        self._stop_reason: str | None = None
        self._consecutive_unknown_count: int = 0

    @property
    def should_stop(self) -> bool:
        """当前是否应停止循环。"""
        return self._stop_reason is not None

    @property
    def stop_reason(self) -> str | None:
        """停止原因字符串，未停止时返回 None。"""
        return self._stop_reason

    def record_model_done(self) -> None:
        """记录：模型返回了纯文本（无工具调用），任务完成。"""
        self._stop_reason = "model_done"

    def record_round(self) -> None:
        """记录一轮完成，若达到上限则设置停止。

        每轮 LLM 调用 + 工具执行完成后调用。
        """
        self._round_count += 1
        if self._round_count >= self._max_rounds:
            self._stop_reason = "max_rounds"

    def record_unknown_tool(self) -> None:
        """记录一次未知工具调用。

        连续达到阈值时触发停止，防止模型反复调用不存在的工具。
        """
        self._consecutive_unknown_count += 1
        if self._consecutive_unknown_count >= self._max_consecutive_unknown:
            self._stop_reason = "unknown_tools"

    def reset_unknown_count(self) -> None:
        """重置连续未知工具计数器（在一次成功的工具调用后调用）。"""
        self._consecutive_unknown_count = 0

    def record_stream_error(self, err: Exception) -> None:
        """记录 LLM 流式响应异常。"""
        self._stop_reason = "stream_error"

    def record_user_cancel(self) -> None:
        """记录用户取消（Ctrl+C）。"""
        self._stop_reason = "user_cancel"

    def reset(self) -> None:
        """重置所有状态，准备新一轮 run。"""
        self._round_count = 0
        self._stop_reason = None
        self._consecutive_unknown_count = 0

    @property
    def round_count(self) -> int:
        """已完成的循环轮数。"""
        return self._round_count

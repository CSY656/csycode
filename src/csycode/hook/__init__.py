"""Hook 生命周期挂钩系统 —— csyCode 扩展机制。

ch12: 在 Agent 生命周期的 11 个固定时刻挂自动化动作。
提供 YAML 声明式配置、条件匹配、四种动作类型（shell/prompt/http/subagent）。

对外暴露:
  - Event: 11 个生命周期事件枚举
  - Engine: Hook 运行时引擎
  - DispatchResult: 事件分派结果
  - load: YAML 配置加载入口
"""

from .engine import DispatchResult as DispatchResult, Engine as Engine  # noqa: E402
from .event import Event as Event, is_blocking as is_blocking  # noqa: E402
from .loader import load as load  # noqa: E402
from .rule import HookRule as HookRule  # noqa: E402

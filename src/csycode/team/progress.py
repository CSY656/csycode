"""队员进度追踪 —— spinner + 最近工具活动。

对齐 mewcode teams/progress.py。

用于 TUI 显示 Team 队员的实时状态：
- 随机 spinner verb（如 "Cooking", "Mewing"）
- 最近 5 次工具调用活动
- token 用量统计
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# ── Spinner 动词池 ────────────────────────────────────────────────

SPINNER_VERBS = [
    "Accomplishing", "Architecting", "Baking", "Beboppin'", "Befuddling",
    "Bloviating", "Boogieing", "Boondoggling", "Bootstrapping", "Brewing",
    "Calculating", "Canoodling", "Caramelizing", "Cascading", "Cerebrating",
    "Choreographing", "Churning", "Coalescing", "Cogitating", "Combobulating",
    "Composing", "Computing", "Concocting", "Considering", "Contemplating",
    "Cooking", "Crafting", "Creating", "Crunching", "Crystallizing",
    "Cultivating", "Deciphering", "Deliberating", "Dilly-dallying",
    "Discombobulating", "Doodling", "Elucidating", "Enchanting", "Envisioning",
    "Fermenting", "Finagling", "Flambéing", "Flibbertigibbeting", "Flummoxing",
    "Forging", "Frolicking", "Gallivanting", "Garnishing", "Generating",
    "Germinating", "Grooving", "Harmonizing", "Hatching", "Honking",
    "Hullaballooing", "Ideating", "Imagining", "Improvising", "Incubating",
    "Inferring", "Infusing", "Kneading", "Lollygagging", "Manifesting",
    "Marinating", "Meandering", "Metamorphosing", "Mewing", "Moonwalking",
    "Moseying", "Mulling", "Musing", "Noodling", "Orbiting", "Orchestrating",
    "Percolating", "Philosophising", "Pondering", "Pontificating", "Pouncing",
    "Purring", "Puzzling", "Razzle-dazzling", "Ruminating", "Scampering",
    "Simmering", "Sketching", "Spelunking", "Spinning", "Sprouting",
    "Synthesizing", "Thinking", "Tinkering", "Transfiguring", "Transmuting",
    "Undulating", "Unfurling", "Unravelling", "Vibing", "Wandering",
    "Whisking", "Working", "Wrangling", "Zigzagging",
]


def random_verb() -> str:
    """随机返回一个 spinner 动词。"""
    return random.choice(SPINNER_VERBS)


# ── ToolActivity ──────────────────────────────────────────────────

@dataclass
class ToolActivity:
    """一次工具调用活动记录。"""
    tool_name: str
    description: str

    @classmethod
    def from_tool_use(cls, tool_name: str, args: dict) -> ToolActivity:
        """从工具名和参数构造活动记录。"""
        desc = _describe(tool_name, args)
        return cls(tool_name=tool_name, description=desc)


def _describe(tool_name: str, args: dict) -> str:
    """将工具调用转为人类可读的描述。"""
    match tool_name:
        case "read_file" | "ReadFile":
            return f"Reading {args.get('file_path', '')}"
        case "edit_file" | "EditFile":
            return f"Editing {args.get('file_path', '')}"
        case "write_file" | "WriteFile":
            return f"Writing {args.get('file_path', '')}"
        case "bash" | "Bash" | "run_command":
            cmd = str(args.get("command", ""))
            return f"Running {cmd[:40]}{'…' if len(cmd) > 40 else ''}"
        case "glob" | "Glob":
            return f"Searching {args.get('pattern', '')}"
        case "grep" | "Grep":
            return f"Grepping {args.get('pattern', '')}"
        case _:
            return tool_name


# ── TeammateProgress ──────────────────────────────────────────────

@dataclass
class TeammateProgress:
    """队员实时进度追踪器（线程安全）。

    Attributes:
        name: 队员名。
        team_name: 所属 Team 名。
        status: "running" / "idle" / "stopped" / "failed"。
        tool_use_count: 工具调用次数。
        token_count: 累计 token 数。
        last_activity: 最近一次工具活动。
        recent_activities: 最近 5 次活动（滚动窗口）。
        spinner_verb: 随机 spinner 动词。
        start_time: 启动时间（monotonic）。
        last_message: 最近一条流式文本。
    """
    name: str
    team_name: str
    status: str = "running"
    tool_use_count: int = 0
    token_count: int = 0
    last_activity: Optional[ToolActivity] = None
    recent_activities: list[ToolActivity] = field(default_factory=list)
    spinner_verb: str = field(default_factory=random_verb)
    start_time: float = field(default_factory=time.monotonic)
    last_message: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_tool_use(self, tool_name: str, args: dict) -> None:
        """记录一次工具调用（线程安全）。"""
        with self._lock:
            self.tool_use_count += 1
            act = ToolActivity.from_tool_use(tool_name, args)
            self.last_activity = act
            self.recent_activities.append(act)
            if len(self.recent_activities) > 5:
                self.recent_activities.pop(0)

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """记录 token 用量（线程安全）。"""
        with self._lock:
            self.token_count = input_tokens + output_tokens

    @property
    def activity_summary(self) -> str:
        """当前活动摘要：最近工具描述或 spinner verb。"""
        with self._lock:
            if self.last_activity:
                return self.last_activity.description
            return self.spinner_verb

    @staticmethod
    def format_tokens(n: int) -> str:
        """格式化 token 数为人类可读形式。"""
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)

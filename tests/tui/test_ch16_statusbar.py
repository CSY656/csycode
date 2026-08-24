"""ch16: 状态栏上下文窗口使用量显示 单元测试。

验证 _statusbar_left() 在四种场景下的输出是否正确。
"""

from __future__ import annotations

import pytest

from csycode.permission import Mode
from csycode.tui.app import csyCodeApp


@pytest.fixture
def app():
    """构造一个最小化 App，跳过 DOM 初始化。"""
    from csycode.config import Config

    config = Config(
        providers=[],
        agent={},
        tools={},
    )
    return csyCodeApp(config=config, work_dir=".")


class TestStatusbarLeft:
    """验证 _statusbar_left() 在各种状态下返回正确字符串。"""

    def test_no_record_returns_no_percent(self, app: csyCodeApp):
        """无请求记录时不显示百分比。"""
        app._last_input_tokens = None
        left = app._statusbar_left()
        assert left == "DEFAULT"
        assert "context used" not in left

    def test_context_window_zero_returns_no_percent(self, app: csyCodeApp):
        """context_window 为 0 时不显示百分比（除零守卫）。"""
        app._last_input_tokens = 100000
        app._context_window = 0
        left = app._statusbar_left()
        assert left == "DEFAULT"
        assert "context used" not in left

    def test_context_window_none_returns_no_percent(self, app: csyCodeApp):
        """context_window 为 None 时不显示百分比。"""
        app._last_input_tokens = 100000
        app._context_window = None
        left = app._statusbar_left()
        assert left == "DEFAULT"
        assert "context used" not in left

    def test_normal_percent_calculation(self, app: csyCodeApp):
        """正常计算百分比：85000 / 200000 = 43%。"""
        app._last_input_tokens = 85000
        app._context_window = 200000
        left = app._statusbar_left()
        assert left == "DEFAULT · 43% context used"

    def test_round_up_at_half(self, app: csyCodeApp):
        """四舍五入：101000 / 200000 = 50.5% → 51%。"""
        app._last_input_tokens = 101000
        app._context_window = 200000
        left = app._statusbar_left()
        assert "51%" in left

    def test_coordinator_mode_label(self, app: csyCodeApp):
        """Coordinator 模式下标签包含 [COORDINATOR]。"""
        app._last_input_tokens = None
        app.coordinator_mode = True
        left = app._statusbar_left()
        assert "[COORDINATOR]" in left

    def test_coordinator_with_percent(self, app: csyCodeApp):
        """Coordinator 模式下同时显示标签和百分比。"""
        app._last_input_tokens = 85000
        app._context_window = 200000
        app.coordinator_mode = True
        left = app._statusbar_left()
        assert left == "DEFAULT [COORDINATOR] · 43% context used"

    def test_plan_mode_label(self, app: csyCodeApp):
        """PLAN 模式下正确显示模式标签。"""
        app._last_input_tokens = None
        app._mode = Mode.PLAN
        left = app._statusbar_left()
        assert left.startswith("PLAN")
        assert "context used" not in left

    def test_round_up(self, app: csyCodeApp):
        """round(200000/200000*100) → 100% 边界。"""
        app._last_input_tokens = 200000
        app._context_window = 200000
        left = app._statusbar_left()
        assert "100%" in left

    def test_round_down_small(self, app: csyCodeApp):
        """round(999/200000*100) → 0%。"""
        app._last_input_tokens = 999
        app._context_window = 200000
        left = app._statusbar_left()
        assert "0%" in left

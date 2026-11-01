# SPDX-License-Identifier: MIT
"""
时间流速档 UX（StelCore::setTimeRate 星象仪叙事主轴"历"）钉扎测试
================================================================

  * ⏸/▶/×60/×300/×1800 按钮组驱动 new_spacetime 引擎倍率（0/1/60/300/1800）
    且按钮组单选互斥；
  * 快进档在未跳时先把引擎钉在当前时刻（否则 rate 对跟随墙钟态不生效）；
  * "实时"= 回墙钟 + 恢复 1x；
  * UTC 标签带倍率后缀（⏸ / ×N），实时无后缀。

引擎是进程内单例：fixture 在每条用例前后把引擎钉回墙钟 + 1x，
避免污染其它用例。

运行：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_time_rate_ui.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from desktop.rf_sky_view import RFSkyViewPanel  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_time_engine():
    eng = get_time_engine()
    eng.set_time_now()
    eng.set_rate(1.0)
    yield
    eng.set_time_now()
    eng.set_rate(1.0)


def _panel() -> RFSkyViewPanel:
    p = RFSkyViewPanel()
    p.resize(900, 600)
    return p


# ----------------------------------------------------------------------
class TestRateButtons:
    def test_fast_forward_sets_rate(self):
        p = _panel()
        p._rate_buttons["×60"].click()
        assert get_time_engine().get_rate() == pytest.approx(60.0)
        assert p._rate_buttons["×60"].isChecked()
        assert not p._rate_buttons["▶"].isChecked()

    def test_pause_and_resume_exclusive(self):
        p = _panel()
        p._rate_buttons["⏸"].click()
        assert get_time_engine().get_rate() == 0.0
        assert p._rate_buttons["⏸"].isChecked()
        p._rate_buttons["×300"].click()
        assert get_time_engine().get_rate() == pytest.approx(300.0)
        assert not p._rate_buttons["⏸"].isChecked()
        assert p._rate_buttons["×300"].isChecked()

    def test_ff_pins_engine_when_following_wall(self):
        eng = get_time_engine()
        eng.set_time_now()                    # _sim_jd=None（跟随墙钟）
        assert eng._sim_jd is None
        p = _panel()
        p._rate_buttons["×1800"].click()
        assert get_time_engine().get_rate() == pytest.approx(1800.0)
        # 引擎已被钉在跳时态，tick 才会按倍率推进
        assert eng._sim_jd is not None
        jd0 = eng.now_jd()
        eng.tick(real_dt=1.0)                 # 手动 1s：应推进 1800 模拟秒
        assert (eng.now_jd() - jd0) * 86400.0 == pytest.approx(1800.0, rel=1e-3)

    def test_live_reset_restores_wall_and_1x(self):
        p = _panel()
        p._rate_buttons["×60"].click()
        p._reset_time()
        eng = get_time_engine()
        assert eng.get_rate() == pytest.approx(1.0)
        assert eng._sim_jd is None            # 回墙钟
        assert p._rate_buttons["▶"].isChecked()


# ----------------------------------------------------------------------
class TestRateLabel:
    def test_label_shows_rate_suffix(self):
        p = _panel()
        p._rate_buttons["×60"].click()
        p._update_label()
        assert "×60" in p.time_label.text()

    def test_label_pause_suffix(self):
        p = _panel()
        p._rate_buttons["⏸"].click()
        p._update_label()
        assert "⏸" in p.time_label.text()

    def test_label_live_no_suffix(self):
        p = _panel()
        p._reset_time()
        p._update_label()
        assert "×" not in p.time_label.text()
        assert "⏸" not in p.time_label.text()
        assert "UTC" in p.time_label.text()

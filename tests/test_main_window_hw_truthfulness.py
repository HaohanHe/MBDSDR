# SPDX-License-Identifier: MIT
"""P2（假调谐回滚 / 真实读数回填 / sink 文案）与 P3（TLE 后台更新）验收。

对应 docs/pending-patches/2026-11-06-ux-basis-main-window.md 的反证表：
每条守卫都要求"改回旧行为 → 本文件对应用例红"，不是只证明新代码能跑。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def window(isolated_profile):
    w = MainWindow()
    yield w
    w._active_sdr_backend = None
    w._loop_sat_tracker = None
    w._tle_update_thread = None


def _wait_until(pred, timeout_s=3.0):
    """跨线程 queued signal 靠 processEvents 轮询（offscreen 下 singleShot(0) 不触发）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    app.processEvents()
    return pred()


class _Backend:
    """最小 SDR 后端桩：记录真实下发，可注入失败。"""

    def __init__(self, *, freq=100e6, gain=20.0, fail_freq=False, fail_gain=False):
        self.center_freq = freq
        self._gain = gain
        self.fail_freq = fail_freq
        self.fail_gain = fail_gain
        self.calls = []

    def set_frequency(self, hz):
        self.calls.append(("set_frequency", float(hz)))
        if self.fail_freq:
            raise RuntimeError("USB 已断开")

    def get_gain(self):
        return self._gain

    def set_gain(self, db):
        self.calls.append(("set_gain", float(db)))
        if self.fail_gain:
            raise RuntimeError("增益写入失败")


def _reset_tune_ui(w, hz=100e6):
    w._backend_center_hz = hz
    w.freq_label.setText(f"{hz / 1e6:.3f} MHz")
    w.status_freq.setText(f"频率: {hz / 1e6:.3f} MHz")


# ---------------------------------------------------------------- P2-A
def test_module_tune_failure_rolls_back(window):
    """模块面板调谐失败：必须下发过硬件，且界面拉回最后一次真实值。"""
    w = window
    be = _Backend(freq=100e6, fail_freq=True)
    w._active_sdr_backend = be
    w.module_panel.set_sdr_connected(True, tune_wired=True)
    _reset_tune_ui(w, 100e6)

    w._on_module_tune(101e6)

    assert be.calls == [("set_frequency", 101e6)], "没下发硬件=假调谐"
    assert w.freq_label.text() == "100.000 MHz", "失败后仍显示新频率=假成功"
    assert w.status_freq.text() == "频率: 100.000 MHz"
    assert abs(w.module_panel.freq_spin.value() - 100.0) < 1e-6, \
        "模块面板频率控件没拉回真实值"
    assert "未生效" in w.statusBar().currentMessage()
    w._active_sdr_backend = None


def test_module_tune_success_reaches_backend(window):
    """成功路径：真实下发后才更新界面。"""
    w = window
    be = _Backend()
    w._active_sdr_backend = be
    _reset_tune_ui(w, 100e6)

    w._on_module_tune(101e6)

    assert ("set_frequency", 101e6) in be.calls
    assert w.freq_label.text() == "101.000 MHz"
    w._active_sdr_backend = None


# ---------------------------------------------------------------- P2-B
def test_spectrum_drag_failure_reverts_ui(window):
    """频谱拖动/双击调谐失败：不许静默吞错后留下成功值。"""
    w = window
    be = _Backend(fail_freq=True)
    w._active_sdr_backend = be
    _reset_tune_ui(w, 100e6)

    w._on_spectrum_freq_changed(101.0)

    assert be.calls == [("set_frequency", 101e6)]
    assert w.freq_label.text() == "100.000 MHz"
    assert w.status_freq.text() == "频率: 100.000 MHz"
    assert "未生效" in w.statusBar().currentMessage(), \
        "失败被吞掉（没有原因）=用户以为调谐成功"
    w._active_sdr_backend = None


# ---------------------------------------------------------------- P2-D
def test_backfill_only_real_values(window):
    """连接后只回填设备真实回执；读不到就保持「—」，不许用 0/默认值凑数。"""
    w = window
    w.control_panel.set_sdr_connected(True)
    w.module_panel.set_sdr_connected(True, tune_wired=True)
    w.module_panel.gain_val.setText("—")

    be = _Backend(freq=101e6, gain=20.0)
    w._active_sdr_backend = be
    w._backfill_real_rf_readings()

    assert abs(w.module_panel.freq_spin.value() - 101.0) < 1e-6
    assert w.module_panel.gain_val.text() == "20 dB"
    if hasattr(w.control_panel, "set_gain_applied"):
        assert w.control_panel.gain_val.text() == "20 dB"

    # 设备不报增益/频率：保持「—」，不得回填 0
    w.module_panel.gain_val.setText("—")
    be_null = _Backend(freq=0.0, gain=None)
    w._active_sdr_backend = be_null
    w._backfill_real_rf_readings()
    assert w.module_panel.gain_val.text() == "—", "拿不到真实增益时编造了读数"
    w._active_sdr_backend = None


def test_gain_failure_rolls_back_real_value(window):
    """增益下发失败：读数必须回到设备真实值，不许停在新值。"""
    w = window
    w.control_panel.set_sdr_connected(True)
    w.module_panel.set_sdr_connected(True, tune_wired=True)
    be = _Backend(gain=20.0, fail_gain=True)
    w._active_sdr_backend = be
    # 用户意图：滑杆 25，配置里也记着 25（上一次成功值）
    w.control_panel.set_gain(25.0)
    w.settings.set("gain_db", 25.0)

    w._on_gain_changed(25)

    assert be.calls == [("set_gain", 25.0)]
    assert w.module_panel.gain_val.text() == "20 dB"
    assert w.control_panel.gain_val.text() == "20 dB", "控制面板必须显示真实读数"
    assert w.control_panel.gain_slider.value() == 25, "配置意图必须留在滑杆上"
    assert w.settings.get("gain_db") == 25.0, \
        "失败后不许把真实值 20 回写配置（配置仍是用户意图）"
    assert "未生效" in w.statusBar().currentMessage()
    w._active_sdr_backend = None


# ---------------------------------------------------------------- P2-E
def test_sink_status_says_record_only(window):
    """sink 下拉尚未接后端切换：状态栏必须明说「仅记录」。"""
    w = window
    w._on_module_sink_changed("声卡")
    msg = w.statusBar().currentMessage()
    assert "仅记录" in msg and "后端未切换" in msg


# ---------------------------------------------------------------- P3
class _FakeTracker:
    tle_date = "2026-11-06"
    last_refresh_failure = None

    def __init__(self, delay=0.6):
        self.delay = delay
        self.calls = 0

    def update_tle_from_celestrak(self):
        self.calls += 1
        time.sleep(self.delay)
        return 5

    def list_satellites(self):
        return [{"name": "ISS (ZARYA)"}]

    def downlink_freq_for(self, name):
        return 0.0


def test_tle_update_runs_off_gui_thread(window):
    """TLE 网络刷新（最坏数十秒）必须在后台；重复点击不叠加请求。"""
    w = window
    tr = _FakeTracker()
    w._loop_sat_tracker = tr
    w._tle_update_thread = None
    w.statusBar().clearMessage()

    t0 = time.monotonic()
    w._on_update_sat_tle()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"TLE 更新仍在 GUI 线程（{elapsed:.2f}s）"
    assert w._tle_update_thread is not None and w._tle_update_thread.is_alive()
    assert "正在更新 TLE" in w.statusBar().currentMessage()

    # 重入：不得再发一次网络请求
    w._on_update_sat_tle()
    assert "TLE 正在更新中" in w.statusBar().currentMessage()
    assert tr.calls == 1

    assert _wait_until(lambda: w._tle_update_thread is None, 3.0), \
        "queued 信号没有把 TLE 结果送回 GUI 线程"
    assert "TLE 已更新（5 颗刷新）" in w.statusBar().currentMessage()
    w._loop_sat_tracker = None

# SPDX-License-Identifier: MIT
"""书签应用顺序与三处一致性的回归锁。

用户 2026-11-12 裁定：书签激活按 SDR++ applyBookmark 的顺序
（mode → bandwidth → tune），且应用成功后
「界面显示 == 硬件/后端实际 == `_vfo_bw`」三者必须一致。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_bookmark_apply.py -v
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


class _FakeBackend:
    """记录硬件调用顺序；rebuild 计数走真实 SDRBackend 的失效点。"""

    def __init__(self):
        from mbdsdr_ai.sdr_backend import SDRBackend
        self._impl = SDRBackend(types.SimpleNamespace(
            frequency_range=(1e6, 6e9),
            sample_rate_range=(1e6, 3e6),
            name="bookmark-apply-fake"))
        self._impl.status.connected = True
        self._impl.status.sample_rate_hz = 2_048_000.0
        self._impl.status.frequency_hz = 100e6
        self._impl.status.bandwidth_hz = 180_000.0
        self._impl.status.demod_mode = "WFM"
        self.calls = []
        self.invalidate = []
        self.fail_bandwidth = False
        impl = self._impl
        orig_inv = impl._invalidate_rx_chain

        def inv(rebuild=True):
            self.invalidate.append(bool(rebuild))
            return orig_inv(rebuild)

        impl._invalidate_rx_chain = inv
        impl._apply_frequency = lambda hz: self._record("set_frequency", hz)
        # 底层写入只回报成功/失败，不记调用：set_demod 内部也会调它下发模式
        # 默认带宽，那条不该混进「书签应用顺序」的调用序列。
        impl._apply_bandwidth = lambda bw: True
        orig_demod = impl.set_demod

        def set_demod(mode):
            self.calls.append(("set_demod", mode))
            return orig_demod(mode)

        impl.set_demod = set_demod

    @property
    def status(self):
        return self._impl.status

    def _record(self, name, arg):
        self.calls.append((name, arg))
        return True

    def set_frequency(self, hz):
        return self._impl.set_frequency(hz)

    def set_bandwidth(self, bw):
        self.calls.append(("set_bandwidth", bw))
        if self.fail_bandwidth:
            return False
        return self._impl.set_bandwidth(bw)

    def set_demod(self, mode):
        return self._impl.set_demod(mode)


@pytest.fixture(scope="module")
def mw(isolated_profile):
    import main_window as mwmod
    w = mwmod.MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:
        pass


@pytest.fixture()
def backend(mw):
    be = _FakeBackend()
    mw._active_sdr_backend = be
    yield be
    mw._active_sdr_backend = None


def _run(mw, be, *, preset_mode, bm_mode, bm_bw, freq=145.8e6):
    if preset_mode:
        mw._on_mode_changed(preset_mode)
    be.calls.clear()
    be.invalidate.clear()
    mw._on_bookmark_tune_requested(freq, bm_mode, bm_bw)
    return list(be.calls), list(be.invalidate)


def test_order_mode_bandwidth_tune(mw, backend):
    calls, _ = _run(mw, backend, preset_mode="WFM",
                    bm_mode="NFM", bm_bw=12_500.0)
    assert [c[0] for c in calls] == [
        "set_demod", "set_bandwidth", "set_frequency"], calls
    assert calls[0] == ("set_demod", "NFM")
    assert calls[1] == ("set_bandwidth", 12_500.0)
    assert calls[2] == ("set_frequency", 145.8e6)


def test_order_skips_mode_when_unchanged(mw, backend):
    calls, _ = _run(mw, backend, preset_mode="NFM",
                    bm_mode="NFM", bm_bw=12_500.0)
    assert [c[0] for c in calls] == ["set_bandwidth", "set_frequency"], calls


def test_rebuild_count_measured(mw, backend):
    """切一次书签的接收链重建次数（计数，不是估算）。

    模式变了：set_demod + set_bandwidth 各一次 → 2 次；
    模式没变：只剩 set_bandwidth → 1 次。
    """
    _, inv = _run(mw, backend, preset_mode="WFM",
                  bm_mode="NFM", bm_bw=12_500.0)
    assert inv.count(True) == 2, inv
    _, inv = _run(mw, backend, preset_mode="NFM",
                  bm_mode="NFM", bm_bw=12_500.0)
    assert inv.count(True) == 1, inv


def test_three_way_consistency_with_recorded_bandwidth(mw, backend):
    _run(mw, backend, preset_mode="WFM", bm_mode="NFM", bm_bw=12_500.0)
    assert backend.status.bandwidth_hz == pytest.approx(12_500.0)
    assert mw._vfo_bw == pytest.approx(12_500.0)
    assert mw.control_panel.vfo_bw_combo.currentData() == pytest.approx(12_500.0)
    assert mw.control_panel._current_mode == "NFM"
    assert mw.dock_mode_label.text() == "NFM"


def test_three_way_consistency_legacy_zero_bandwidth(mw, backend):
    """旧书签 bw=0：status / `_vfo_bw` / 界面 必须同为一个已下发的值。

    不再允许 status=AM 默认 6k、硬件与 `_vfo_bw` 仍停在 12.5k。
    """
    import main_window as mwmod
    expected = float(mwmod.default_bandwidth("AM"))
    calls, _ = _run(mw, backend, preset_mode="NFM", bm_mode="AM", bm_bw=0.0)
    assert ("set_bandwidth", expected) in calls
    assert backend.status.bandwidth_hz == pytest.approx(expected)
    assert mw._vfo_bw == pytest.approx(expected)
    assert mw.control_panel.vfo_bw_combo.currentData() == pytest.approx(expected)
    assert mw.control_panel._current_mode == "AM"
    assert mw.dock_mode_label.text() == "AM"


def test_bandwidth_failure_stops_before_tune(mw, backend):
    backend.fail_bandwidth = True
    calls, _ = _run(mw, backend, preset_mode="WFM",
                    bm_mode="NFM", bm_bw=12_500.0)
    assert ("set_frequency", 145.8e6) not in calls, calls
    assert [c[0] for c in calls] == ["set_demod", "set_bandwidth"], calls


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

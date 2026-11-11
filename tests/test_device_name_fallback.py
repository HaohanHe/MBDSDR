# SPDX-License-Identifier: MIT
"""连接成功槽的设备名取值回归（无硬件，offscreen）。

缺口 G01：BladeRF / LimeSDR 的后端不是 ``SDRBackend`` 子类，没有
``device.name``（`bladerf_params.py:346`、`limesuite_params.py:367`）。
连接成功槽直取 ``backend.device.name`` 会抛 ``AttributeError``，把同一槽里
后面的「增益范围收放 / 参数下发 / 读数回填」全部跳过——界面停在"半个连接"，
而且用户看不到任何提示。

反证方式：把 ``_resolve_device_display_name`` 换回直取 ``backend.device.name``，
本文件即红——``_connect_backend`` 抛异常、三个调用计数全为 0、
设备槽里找不到「设备名称不可读」。
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402


class _DeviceNoName:
    """有 max_gain（能收放滑杆）但没有 name 的设备描述。"""

    def __init__(self, max_gain: float = 25.0):
        self.max_gain = max_gain


class _StubBackend:
    """连接成功槽依赖的最小后端接口；计数器给反证用。"""

    def __init__(self, device=None, *, has_device=True):
        if has_device:
            self.device = device if device is not None else _DeviceNoName()
        self.status = SimpleNamespace(error="", gain_db=20.0, demod_mode="FM",
                                      connected=False)
        self.connected = False
        self.freq = 98.0e6
        self.sr = 2.048e6
        self.gain_calls = []

    def connect(self):
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def get_status(self):
        return SimpleNamespace(connected=self.connected, error=self.status.error,
                               gain_db=self.status.gain_db,
                               demod_mode=self.status.demod_mode)

    def get_sample_rate(self):
        return self.sr

    def set_sample_rate(self, r):
        self.sr = float(r)

    def get_frequency(self):
        return self.freq

    def set_frequency(self, f):
        self.freq = float(f)

    def set_bandwidth(self, _b):
        pass

    def set_ppm(self, _p):
        pass

    def set_offset_tuning(self, _b):
        return True

    def set_agc(self, _b):
        return True

    def set_gain(self, g):
        self.gain_calls.append(float(g))

    def read_samples(self, n):
        return np.zeros(int(n), dtype=np.complex64)

    def subscribe_iq(self, _name):
        return SimpleNamespace(read=self.read_samples)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(30)
    yield w
    try:
        w._teardown_connection()
    except Exception:
        pass


def _instrument(w):
    """给三个必经步骤装计数器；返回计数 dict。"""
    counts = {"params": 0, "backfill": 0}
    orig_params = w._apply_connect_params
    orig_backfill = w._backfill_real_rf_readings

    def _params(*a, **k):
        counts["params"] += 1
        return orig_params(*a, **k)

    def _backfill(*a, **k):
        counts["backfill"] += 1
        return orig_backfill(*a, **k)

    w._apply_connect_params = _params
    w._backfill_real_rf_readings = _backfill
    return counts


def _instrument_gain_range(w):
    """记录 set_gain_range 的调用参数。"""
    applied = []
    for name in ("control_panel", "module_panel"):
        panel = getattr(w, name, None)
        fn = getattr(panel, "set_gain_range", None)
        if not callable(fn):
            continue
        orig = fn

        def _record(lo, hi, _orig=orig, _name=name):
            applied.append((_name, float(lo), float(hi)))
            _orig(lo, hi)

        panel.set_gain_range = _record
    return applied


@pytest.mark.usefixtures("qtapp")
class TestDeviceNameFallback:
    def _reset(self, window):
        from PySide6.QtTest import QTest
        window._teardown_connection()
        QTest.qWait(20)
        return window

    def test_missing_device_name_still_applies_gain_and_backfill(self, window):
        from PySide6.QtTest import QTest
        w = self._reset(window)
        counts = _instrument(w)
        applied = _instrument_gain_range(w)
        backend = _StubBackend(_DeviceNoName(max_gain=25.0))

        assert w._connect_backend(backend) is True, \
            "设备名不可读不得让连接成功槽抛异常/中断"

        assert counts["params"] == 1, "参数下发必须仍然执行"
        assert counts["backfill"] == 1, "读数回填必须仍然执行"
        assert applied, "增益范围收放必须仍然执行（滑杆不得停在兜底 0–49.6）"
        assert max(hi for _n, _lo, hi in applied) == pytest.approx(25.0), \
            "滑杆上限必须来自设备 max_gain"

        assert "设备名称不可读" in w.conn_label.text(), \
            "设备槽必须明确写出「设备名称不可读」，不许静默"
        assert "设备名称不可读" in w.status_conn.text()
        assert "_StubBackend" in w.conn_label.text(), \
            "读不到名字时必须回退到后端类名兜底"

        QTest.qWait(50)
        w._teardown_connection()

    def test_backend_without_device_object_still_connects(self, window):
        """BladeRF/Lime 式：后端根本没有 device 属性。"""
        from PySide6.QtTest import QTest
        w = self._reset(window)
        counts = _instrument(w)
        backend = _StubBackend(has_device=False)

        assert w._connect_backend(backend) is True
        assert counts["params"] == 1
        assert counts["backfill"] == 1
        assert "设备名称不可读" in w.conn_label.text()
        assert "_StubBackend" in w.conn_label.text()

        QTest.qWait(50)
        w._teardown_connection()

    def test_readable_name_is_used_verbatim(self, window):
        """有真名时不得加任何噪声后缀。"""
        from PySide6.QtTest import QTest
        w = self._reset(window)
        backend = _StubBackend(_DeviceNoName())
        backend.device.name = "RTL-SDR Blog V3"

        assert w._connect_backend(backend) is True
        assert w.conn_label.text().strip() == "状态: RTL-SDR Blog V3"
        assert "设备名称不可读" not in w.conn_label.text()

        QTest.qWait(50)
        w._teardown_connection()


class TestRealBackendCoverage:
    """后端 × 名称来源矩阵：每个真实后端类都走同一个受保护取值口。"""

    @staticmethod
    def _resolve(backend):
        import main_window as mw
        return mw.MainWindow._resolve_device_display_name(backend)

    def test_rtlsdr_local_and_rtl_tcp(self):
        from mbdsdr_ai.sdr_backend import RTLSDRBackend

        assert self._resolve(RTLSDRBackend(device_index=2)) == \
            ("RTL-SDR #2", True)
        assert self._resolve(RTLSDRBackend(host="127.0.0.1", port=1234)) == \
            ("rtl_tcp 127.0.0.1:1234", True)

    def test_soapy_uses_enumerated_label(self):
        from mbdsdr_ai.sdr_backend import SoapySDRBackend

        backend = SoapySDRBackend(
            "driver=rtlsdr",
            {"label": "Generic RTL2832U", "driver": "rtlsdr"})
        assert self._resolve(backend) == ("Generic RTL2832U", True)

    def test_soapy_without_label_falls_back_to_args(self):
        from mbdsdr_ai.sdr_backend import SoapySDRBackend

        backend = SoapySDRBackend("driver=x", {"driver": "x"})
        name, readable = self._resolve(backend)
        assert name == "driver=x"
        assert readable is True, "参数串是可读身份，不算名称不可读"

    def test_bladerf_and_lime_have_no_device_object(self):
        from mbdsdr_ai.bladerf_params import BladeRFBackend
        from mbdsdr_ai.limesuite_params import LimeSDRBackend

        assert self._resolve(BladeRFBackend()) == ("BladeRFBackend", False)
        assert self._resolve(LimeSDRBackend()) == ("LimeSDRBackend", False)

    def test_file_playback_uses_filename(self):
        from mbdsdr_ai.sdr_backend import FileIQBackend

        backend = FileIQBackend(r"C:\tmp\rec 001.cf32")
        assert self._resolve(backend) == ("IQ 文件回放: rec 001.cf32", True)

    def test_blank_name_falls_back_and_flags_unreadable(self):
        class _BlankName:
            device = SimpleNamespace(name="   ")

        name, readable = self._resolve(_BlankName())
        assert name == "_BlankName"
        assert readable is False


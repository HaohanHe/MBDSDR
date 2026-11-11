# SPDX-License-Identifier: MIT
"""写操作失败后，UI 必须显示设备真实值（tests/test_hw_rollback_shows_device_truth.py）
=====================================================================================

矩阵第二环：拒绝之后控件停在哪里。修前三种「显示着一个从未生效的数」：

1. 采样率异常路径（非录音 + 录音）：状态栏说了失败，下拉却仍停在用户请求值；
   修后回到 ``get_sample_rate()`` 回读值（回读也失败则用最后一次已知值）。
2. AGC 拒绝且本地没有历史配置（首启）：开关停在新值；
   修后按连接默认（AGC 开）回退。
3. 增益回读自身失败：``_show_real_gain`` 提前返回，标签/滑杆停在新值；
   修后退回最后一次成功读回值。
4. PPM 被拒：数值框停在用户刚输入的请求值（PPM 无回读接口）；
   修后回填最后确认生效值，从未确认过则退连接默认 0。
5. Offset Tuning 被拒：开关停在用户刚切的新状态；
   修后回填最后确认生效状态，从未确认过则退连接默认（关）。
6. 带宽被拒：下拉停在用户选的档；修后回到 ``get_bandwidth()`` 设备真实值，
   回读不可用/失败时用最后已生效的 ``_vfo_bw``（与采样率同一策略）。

反证：任一处改回旧行为，对应用例必红。
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def mw(qtapp):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001
        pass
    w.close()


def test_sample_rate_exception_restores_combo_to_device_value(mw):
    """set_sample_rate 抛异常时，下拉不得停在请求值上。"""
    class _Backend:
        status = SimpleNamespace(error="驱动异常")

        def set_sample_rate(self, rate):
            raise OSError("驱动拒绝切换采样率")

        def get_sample_rate(self):
            return 2_400_000.0

    cp = mw.control_panel
    # 用户已把下拉切到 3.2 MS/s（当前可见档），此时请求失败才可能「停在新值」
    cp.set_sample_rate_options([1_024_000, 2_400_000, 3_200_000], 3_200_000)
    assert cp.sample_rate_combo.currentData() == pytest.approx(3_200_000.0)
    mw._record_sr = 2_400_000.0
    mw._recording = False
    mw._active_sdr_backend = _Backend()

    mw._on_sample_rate_changed(3_200_000.0)

    assert cp.sample_rate_combo.currentData() == pytest.approx(2_400_000.0), \
        "失败后下拉必须回到设备真实值，不能停在从未生效的请求值"


def test_agc_rejection_without_history_reverts_to_connect_default(mw, monkeypatch):
    """首启无 agc_enabled 历史值时，拒绝后开关不得停在新值。"""
    class _Backend:
        def set_agc(self, enabled):
            return False

        def get_status(self):
            return SimpleNamespace(error="设备拒绝 AGC 切换")

    mw._active_sdr_backend = _Backend()
    monkeypatch.setattr(mw.settings, "get",
                        lambda key, default=None: None if key == "agc_enabled" else default)
    mw.control_panel.set_agc(False)
    assert mw.control_panel.agc_check.isChecked() is False

    mw._on_agc_changed(False)

    assert mw.control_panel.agc_check.isChecked() is True, \
        "拒绝后必须回到连接默认（AGC 开），不能显示失败的关状态"


def test_gain_readback_failure_falls_back_to_last_applied(mw, monkeypatch):
    """回读 get_gain() 失败时，标签退回最后一次成功读回值。"""
    class _Backend:
        def get_gain(self):
            raise OSError("读不到设备增益")

    shown = []
    # set_gain_applied 现带可选 readback_source：桩要按新签名收参，
    # 断言仍只钉「退回到最后一次读回值」。
    monkeypatch.setattr(mw.control_panel, "set_gain_applied",
                        lambda db, source="": shown.append(float(db)))
    mw._last_gain_applied = 20.0

    mw._show_real_gain(_Backend())

    assert shown == [20.0], "读不到真实值时必须退回最后一次读回值"


class _RefusingPpmBackend:
    def set_ppm(self, ppm):
        return False

    def get_status(self):
        return SimpleNamespace(error="设备拒绝该 PPM")


class _RefusingOffsetBackend:
    def set_offset_tuning(self, enabled):
        return False

    def get_status(self):
        return SimpleNamespace(error="设备拒绝 Offset Tuning")


def test_ppm_rejection_restores_last_applied(mw):
    """PPM 被拒后，数值框回到最后生效值，不停在请求值。"""
    cp = mw.control_panel
    cp.set_sdr_connected(True)
    mw._active_sdr_backend = None          # 先把「用户改值」与下发隔开
    mw._last_ppm_applied = 25
    cp.ppm_spin.setValue(25)
    cp.ppm_spin.setValue(50)
    assert cp.ppm_spin.value() == 50       # 此刻控件就是「从未生效的请求值」

    mw._active_sdr_backend = _RefusingPpmBackend()
    mw._on_ppm_changed(50)

    assert cp.ppm_spin.value() == 25, "拒绝后必须回到最后生效的 PPM"
    assert "未生效" in mw.statusBar().currentMessage()


def test_ppm_without_confirmed_history_falls_back_to_zero(mw):
    """从未确认过任何 PPM 时，拒绝后退回连接默认 0（不是请求值）。"""
    cp = mw.control_panel
    cp.set_sdr_connected(True)
    mw._active_sdr_backend = None
    mw._last_ppm_applied = None
    cp.ppm_spin.setValue(0)
    cp.ppm_spin.setValue(40)

    mw._active_sdr_backend = _RefusingPpmBackend()
    mw._on_ppm_changed(40)

    assert cp.ppm_spin.value() == 0, "无生效历史时必须退回连接默认 0"


def test_offset_rejection_restores_last_applied(mw):
    """Offset Tuning 被拒后，开关回到最后生效状态。"""
    cp = mw.control_panel
    cp.set_sdr_connected(True)
    mw._active_sdr_backend = None
    mw._last_offset_applied = True
    cp.offset_check.setChecked(True)
    cp.offset_check.setChecked(False)
    assert cp.offset_check.isChecked() is False

    mw._active_sdr_backend = _RefusingOffsetBackend()
    mw._on_offset_tuning_changed(False)

    assert cp.offset_check.isChecked() is True, "拒绝后必须回到最后生效状态"


def test_offset_without_confirmed_history_falls_back_to_off(mw):
    """从未确认过 Offset Tuning 时，拒绝后退回连接默认（关）。"""
    cp = mw.control_panel
    cp.set_sdr_connected(True)
    mw._active_sdr_backend = None
    mw._last_offset_applied = None
    cp.offset_check.setChecked(False)
    cp.offset_check.setChecked(True)

    mw._active_sdr_backend = _RefusingOffsetBackend()
    mw._on_offset_tuning_changed(True)

    assert cp.offset_check.isChecked() is False, "无生效历史时必须退回连接默认（关）"


class _RefusingBandwidthBackend:
    def __init__(self, readback):
        self._readback = readback

    def set_bandwidth(self, bw):
        return False

    def get_bandwidth(self):
        if isinstance(self._readback, Exception):
            raise self._readback
        return self._readback

    def get_status(self):
        return SimpleNamespace(error="设备拒绝该带宽")


def test_bandwidth_rejection_restores_device_readback(mw):
    """带宽被拒后，下拉回到 get_bandwidth() 的设备真实值。"""
    cp = mw.control_panel
    cp.set_sdr_connected(True)
    mw._active_sdr_backend = None
    idx = cp.vfo_bw_combo.findData(180_000.0)
    assert idx >= 0
    cp.vfo_bw_combo.setCurrentIndex(idx)
    assert cp.vfo_bw_combo.currentData() == 180_000.0
    # 下拉切换会经信号把本地 _vfo_bw 也改成 180 kHz；这里还原成最后一次
    # 真正下发成功的 12.5 kHz，失败回填才有可比较的真值。
    mw._vfo_bw = 12_500.0

    mw._active_sdr_backend = _RefusingBandwidthBackend(12_500.0)
    ok = mw._on_vfo_bandwidth_changed(180_000.0)

    assert ok is False
    assert cp.vfo_bw_combo.currentData() == 12_500.0, "必须回到设备真实带宽"
    assert "未生效" in mw.statusBar().currentMessage()


def test_bandwidth_rejection_without_readback_uses_last_known(mw):
    """回读也失败时，下拉退回最后已生效值（不回落到请求值）。"""
    cp = mw.control_panel
    cp.set_sdr_connected(True)
    mw._active_sdr_backend = None
    idx = cp.vfo_bw_combo.findData(180_000.0)
    assert idx >= 0
    cp.vfo_bw_combo.setCurrentIndex(idx)
    mw._vfo_bw = 12_500.0   # 最后真正下发成功的值（见上一条用例说明）

    mw._active_sdr_backend = _RefusingBandwidthBackend(OSError("读不到带宽"))
    ok = mw._on_vfo_bandwidth_changed(180_000.0)

    assert ok is False
    assert cp.vfo_bw_combo.currentData() == 12_500.0, \
        "回读失败必须用最后已知值"

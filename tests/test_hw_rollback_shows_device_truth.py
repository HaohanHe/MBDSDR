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
    monkeypatch.setattr(mw.control_panel, "set_gain_applied",
                        lambda db: shown.append(float(db)))
    mw._last_gain_applied = 20.0

    mw._show_real_gain(_Backend())

    assert shown == [20.0], "读不到真实值时必须退回最后一次读回值"

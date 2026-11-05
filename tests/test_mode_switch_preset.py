# SPDX-License-Identifier: MIT
"""
切模式自动套滤波预设 路径一致性钉扎测试（P1-6 收尾）
====================================================

两条入口必须汇到同一条「套默认带宽 + 发 vfo_bandwidth_changed」链路：
  * 面板下拉直选（mode_combo.currentTextChanged → _on_mode_changed）；
  * 频谱键盘快捷键（mode_hotkey_requested → _on_mode_hotkey → 改下拉文本
    → 同一 _on_mode_changed）。

验收：两条路都发出 default_bandwidth(mode)，且 vfo_bw_combo 落到
对应档位。反向用例：未注册模式进不来（下拉无此项 → 无信号）。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

log = logging.getLogger(__name__)

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def mw(isolated_profile, qapp):
    # isolated_profile 见 tests/conftest.py：本夹具写 app_mode，
    # 不隔离就会写进用户真实配置。
    from main_window import MainWindow
    w = MainWindow()
    w.settings.set("app_mode", "ide")
    w.set_app_mode("ide")
    yield w
    try:
        w._disconnect()
    except Exception as exc:  # noqa: BLE001 - 收尾失败必须留证，不静默
        log.warning("test_mode_switch_preset.py fixture 收尾失败（MainWindow._disconnect）：%s: %s",
                    type(exc).__name__, exc)


@pytest.mark.parametrize("mode", ["AM", "NFM", "FM", "WFM", "CW", "LSB", "USB"])
def test_panel_combo_applies_default_bandwidth(mw, mode):
    from mode_registry import default_bandwidth
    got = []
    mw.control_panel.vfo_bandwidth_changed.connect(got.append)
    mw.control_panel.mode_combo.setCurrentText(mode)
    assert got, f"面板直选 {mode} 未发 vfo_bandwidth_changed"
    assert got[-1] == float(default_bandwidth(mode))
    idx = mw.control_panel.vfo_bw_combo.currentIndex()
    assert (mw.control_panel.vfo_bw_combo.itemData(idx)
            == float(default_bandwidth(mode)))


@pytest.mark.parametrize("mode", ["AM", "NFM", "FM", "WFM", "CW", "LSB", "USB"])
def test_hotkey_rides_same_chain(mw, mode):
    from mode_registry import default_bandwidth
    got = []
    mw.control_panel.vfo_bandwidth_changed.connect(got.append)
    n_before = len(got)
    mw._on_mode_hotkey(mode)
    assert len(got) > n_before, f"快捷键 {mode} 未触发带宽联动"
    assert got[-1] == float(default_bandwidth(mode))


def test_unregistered_mode_rejected(mw):
    got = []
    mw.control_panel.vfo_bandwidth_changed.connect(got.append)
    mw.control_panel.mode_combo.setCurrentText("ZZZ")
    assert got == []
    assert mw.control_panel.mode_combo.currentText() != "ZZZ"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

# SPDX-License-Identifier: MIT
"""硬件操作被拒绝必须有可见失败（tests/test_hw_rejection_visible.py）
====================================================================

矩阵扫出的三处「静默成功」，逐条钉住：

1. 分档增益（GainPanel → GainStager）：修前 ``except: pass`` 后照样 emit
   ``stage_gain_changed`` / ``value_changed`` 并刷新总增益——滑杆动了就被当成
   已生效。修后失败发 ``stage_gain_failed``，不 emit 成功信号、不动总增益。
2. PPM 校正：修前只看异常，后端显式返回 ``False`` 被当成成功。
   修后走 ``_apply_hw_action``（False 与异常同等失败），状态栏可见。
3. Offset tuning：同上。

反证口径：任一处改回旧行为（吞异常/忽略 False），对应用例必红。
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
    except Exception:  # noqa: BLE001 - 收尾失败不拖垮用例
        pass
    w.close()


# ---------------------------------------------------------------- 1. 分档增益
def test_stage_gain_write_failure_is_not_reported_as_applied(qtapp):
    """后端写分档增益抛异常：不得 emit 成功，也不得刷新总增益读数。"""
    from gain_panel import GainPanel

    panel = GainPanel()
    failed = []
    applied = []
    panel.stage_gain_failed.connect(failed.append)
    panel.stage_gain_changed.connect(lambda logical, db: applied.append((logical, db)))

    class _Boom:
        def set_lna(self, db):
            raise OSError("设备拒绝 LNA 增益")

        def set_mixer(self, db):
            raise OSError("设备拒绝 Mixer 增益")

        def set_vga(self, db):
            raise OSError("设备拒绝 VGA 增益")

    panel._stager = _Boom()
    before = panel.total_label.text()
    panel._on_stage_changed("LNA", 12.0)

    assert failed, "失败必须上报，不能吞掉"
    assert "未生效" in failed[0] and "设备拒绝 LNA 增益" in failed[0]
    assert applied == [], "失败时不得发成功信号"
    assert panel.total_label.text() == before, "失败时总增益读数不得变化"


def test_stage_gain_failure_reaches_status_bar(mw):
    """主窗口必须把分档增益失败写到状态栏（信号已接线）。"""
    mw.gain_panel.stage_gain_failed.emit(
        "分档增益未生效（VGA 3.0 dB）：设备拒绝该档")
    assert "分档增益未生效" in mw.statusBar().currentMessage()


# ---------------------------------------------------------------- 2/3. PPM / offset
class _RejectingBackend:
    """显式返回 False 的后端：唯一失败信号是返回值，不抛异常。"""

    def __init__(self, detail):
        self._detail = detail

    def set_ppm(self, value):
        return False

    def set_offset_tuning(self, value):
        return False

    def get_status(self):
        return SimpleNamespace(error=self._detail)


def test_ppm_rejection_is_visible(mw, monkeypatch):
    monkeypatch.setattr(mw, "_active_sdr_backend",
                        _RejectingBackend("驱动拒绝 PPM 12"))
    mw._on_ppm_changed(12)
    assert "PPM未生效" in mw.statusBar().currentMessage(), \
        "后端返回 False 时不得静默当成成功"


def test_offset_tuning_rejection_is_visible(mw, monkeypatch):
    monkeypatch.setattr(mw, "_active_sdr_backend",
                        _RejectingBackend("驱动拒绝 offset tuning"))
    mw._on_offset_tuning_changed(True)
    assert "Offset tuning未生效" in mw.statusBar().currentMessage()


def test_ppm_without_backend_capability_is_visible(mw, monkeypatch):
    """后端没有 set_ppm：既不能调用，也不能装作已生效。"""
    monkeypatch.setattr(mw, "_active_sdr_backend", SimpleNamespace())
    mw._on_ppm_changed(5)
    assert "PPM 未下发" in mw.statusBar().currentMessage()

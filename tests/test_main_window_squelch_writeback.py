# SPDX-License-Identifier: MIT
"""
`_on_squelch_changed` 必须把静噪来源写回后端状态
=================================================

旧实现只改本地链（`_squelch_db` / `_demod_cfg`），不写
`backend.status.squelch_applied_by` / `squelch_db`，状态栏因此可能显示旧值或
`none`，用户看不到「现在是桌面余量在生效还是 C++ 自动在生效」。

契约：滑杆一动 → backend.set_squelch(值) → status 变成
`squelch_applied_by == "desktop-margin"`、`squelch_db == 该值`、
`squelch_threshold_dbfs is None`（Python 口径不下发绝对门），
且状态栏文案随 applied_by 变为「余量，桌面生效」。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.sdr_backend import SDRBackend, SDRStatus  # noqa: E402


class _RecordingBackend:
    """真语义的假后端：记录调用，状态改写走 SDRBackend.set_squelch 本体。"""

    def __init__(self):
        self.status = SDRStatus()
        self.calls = []
        self._receive_chain = None

    def set_squelch(self, value):
        self.calls.append(float(value))
        return SDRBackend.set_squelch(self, value)


def _bare_window():
    from main_window import MainWindow
    from receive_pipeline import SharedDemodConfig
    w = MainWindow.__new__(MainWindow)
    w._squelch_db = -80.0
    w._demod_cfg = SharedDemodConfig(squelch_db=-80.0)
    w._backend = _RecordingBackend()
    return w


def test_slider_writes_back_source_and_margin():
    from main_window import MainWindow
    w = _bare_window()

    MainWindow._on_squelch_changed(w, -20)

    st = w._backend.status
    assert st.squelch_applied_by == "desktop-margin", st.squelch_applied_by
    assert st.squelch_db == -20.0
    assert st.squelch_threshold_dbfs is None, "Python 余量口径不下发绝对门"
    assert w._backend.calls == [-20.0], w._backend.calls
    # 本地链仍然照旧
    assert w._squelch_db == -20.0
    assert w._demod_cfg.squelch_db == -20.0
    assert w._demod_cfg.auto_squelch is False


def test_status_text_follows_applied_by():
    from main_window import MainWindow
    from status_panel import _fmt_squelch
    w = _bare_window()

    MainWindow._on_squelch_changed(w, -20)

    st = w._backend.status
    text = _fmt_squelch(st.squelch_db, st.squelch_applied_by,
                        st.squelch_threshold_dbfs)
    assert text == "-20（16 dB 余量，桌面生效）", text
    assert "dBFS" not in text


def test_without_backend_does_not_raise():
    from main_window import MainWindow
    w = MainWindow.__new__(MainWindow)
    w._squelch_db = -50.0
    w._demod_cfg = None
    w._backend = None

    MainWindow._on_squelch_changed(w, -50)   # 无后端只是不写回，不许崩
    assert w._squelch_db == -50.0

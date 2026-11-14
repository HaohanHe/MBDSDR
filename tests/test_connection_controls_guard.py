# SPDX-License-Identifier: MIT
"""B1/B2：未连接时收听下拉置灰；连接中按钮置灰并显示「连接中…」。

判据：
  * ``_panels_set_sdr_connected(False)`` → listen_combo 不可用，tooltip 写明
    「未连接设备：先点「连接」」；连接后恢复可用且 tooltip 回到三档说明。
  * 后台连接等待窗口内，connect_btn 必须 disabled 且文案为「连接中…」；
    等待结束后恢复「连接」并重新可点（超时也要能重试）。
  * ai-sdr WebSocket 路径同样进入忙碌态，连接失败后文案收回「连接」。

反证：去掉 ``_set_connect_busy`` 调用（或 listen_combo 那段），对应断言红。
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
def qtapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def window(qtapp, isolated_profile):
    import main_window as mw
    w = mw.MainWindow()
    w._recording = False
    yield w, mw
    try:
        w._recording = False
        w._teardown_connection()
    except Exception:  # noqa: BLE001
        pass


def test_listen_combo_disabled_without_device(window):
    w, _mw = window
    w._panels_set_sdr_connected(False)

    assert not w.listen_combo.isEnabled(), "未连接时收听下拉必须置灰"
    tip = w.listen_combo.toolTip()
    assert "未连接设备" in tip and "先点「连接」" in tip, \
        "置灰必须说明为什么、下一步怎么做"


def test_listen_combo_enabled_after_connect(window):
    w, _mw = window
    w._panels_set_sdr_connected(True)

    assert w.listen_combo.isEnabled()
    assert "收听 = 解调" in w.listen_combo.toolTip()


def test_connect_button_busy_during_wait_then_restored(window, monkeypatch):
    w, mw = window
    seen = []

    def fake_exec(_loop):
        # 等后台线程跑完 connect()（假后端立即返回），再观察等待窗口内的按钮态
        import time
        time.sleep(0.1)
        seen.append((w.connect_btn.isEnabled(), w.connect_btn.text()))
        return 0

    monkeypatch.setattr(mw.QEventLoop, "exec", fake_exec)
    backend = SimpleNamespace(connect=lambda: True,
                              device=SimpleNamespace(name="FakeSDR"))

    ok = w._connect_backend_in_background(backend)

    assert ok is True
    assert seen == [(False, "连接中…")], \
        "等待窗口内按钮必须置灰且显示「连接中…」"
    assert w.connect_btn.isEnabled(), "等待结束后必须恢复可点（超时也要能重试）"
    assert w.connect_btn.text() == "连接"


def test_connect_real_sets_busy_and_failure_restores(window, monkeypatch):
    w, _mw = window
    monkeypatch.setattr(w, "_disconnect", lambda: None)
    monkeypatch.setattr(w, "_connect_worker_signals", lambda: None)
    monkeypatch.setattr(
        w._worker_manager, "start",
        lambda **kw: SimpleNamespace())

    w._connect_real("127.0.0.1", 1234)
    assert not w.connect_btn.isEnabled()
    assert w.connect_btn.text() == "连接中…"

    w._on_connection_for_ui(False, "连接失败")
    assert w.connect_btn.isEnabled(), "连接失败后必须能再次点「连接」"
    assert w.connect_btn.text() == "连接"

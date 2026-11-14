# SPDX-License-Identifier: MIT
"""D5：Esc 取消进行中的长操作（扫频 / 识别 / 回放），口径只写一处。

判据：
  * 扫频在跑：``_handle_escape_layer()`` 返回 True 并调用扫频取消；
  * 识别在跑：走 ``cancel_identify()``，状态栏写明结果作废；
  * 回放在跑：走 ``_stop_replay()``，状态栏写「已取消回放」；
  * 什么都没跑：返回 False（不吞 Esc，交给上层）；
  * 帮助页正文包含 ``help_panel.ESCAPE_LAYER_NOTE``（唯一口径）。

反证：去掉 ``_handle_escape_layer`` 里的 ``_cancel_long_operation`` 调用后
前三条红；去掉帮助页渲染行后口径用例红。
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
        w.scanner_panel._worker = None
        w.modulation_panel._worker = None
        w._teardown_connection()
    except Exception:  # noqa: BLE001
        pass


def _running():
    return SimpleNamespace(isRunning=lambda: True)


def test_escape_cancels_scan(window, monkeypatch):
    w, _mw = window
    cancelled = []
    w.scanner_panel._worker = _running()
    monkeypatch.setattr(w.scanner_panel, "_on_cancel_clicked",
                        lambda: cancelled.append(True))

    assert w._handle_escape_layer() is True
    assert cancelled == [True], "扫频中按 Esc 必须取消扫频"
    assert "正在取消扫频" in w.statusBar().currentMessage()


def test_escape_cancels_identify(window, monkeypatch):
    w, _mw = window
    cancelled = []
    w.modulation_panel._worker = _running()
    monkeypatch.setattr(w.modulation_panel, "cancel_identify",
                        lambda: cancelled.append(True) or True)

    assert w._handle_escape_layer() is True
    assert cancelled == [True], "识别中按 Esc 必须取消识别"
    assert "已取消识别" in w.statusBar().currentMessage()


def test_escape_cancels_replay(window, monkeypatch):
    w, _mw = window
    stopped = []
    w._replay_iq = object()
    monkeypatch.setattr(w, "_stop_replay", lambda: stopped.append(True))

    assert w._handle_escape_layer() is True
    assert stopped == [True], "回放中按 Esc 必须停止回放"
    assert "已取消回放" in w.statusBar().currentMessage()


def test_escape_without_long_op_bubbles_up(window):
    w, _mw = window
    w.scanner_panel._worker = None
    w.modulation_panel._worker = None
    w._replay_iq = None
    w._replay_timer = None
    w._replay_load_thread = None

    assert w._handle_escape_layer() is False, "没有长操作时不得吞掉 Esc"


def test_identify_cancel_discards_late_result(qtapp):
    from modulation_panel import ModulationPanel

    p = ModulationPanel()
    try:
        p._worker = SimpleNamespace(isRunning=lambda: True)
        assert p.cancel_identify() is True
        assert "已取消识别" in p.status_label.text()

        # 取消后 worker 仍会 emit done：结果必须作废，不能把卡片填成"识别完成"
        p._on_done(SimpleNamespace(modulation="FM", confidence=0.9,
                                   bandwidth=1e5, symbol_rate_hint=None,
                                   suggestions=[]))
        assert p._last is None, "Esc 之后迟到的识别结果必须丢弃"
        assert "已取消识别" in p.status_label.text()
    finally:
        p.close()


def test_help_page_states_escape_policy(qtapp):
    from PySide6.QtWidgets import QTextBrowser
    import help_panel as hp

    note = hp.ESCAPE_LAYER_NOTE
    assert "取消进行中的操作" in note
    assert "先按 Esc 退出覆盖层" in note

    panel = hp.HelpPanel()
    try:
        texts = [b.toPlainText() for b in panel.findChildren(QTextBrowser)]
    finally:
        panel.close()
    assert any(note in t for t in texts), \
        "帮助页必须原样渲染 ESCAPE_LAYER_NOTE（唯一口径，不许另抄一份）"

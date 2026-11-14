# SPDX-License-Identifier: MIT
"""A1：断开 / 切换设备时正在录音，必须先确认；拔设备不弹窗但要说清落盘路径。

判据：
  * 录音中点「断开」：弹确认框，默认按钮「取消（继续录音）」，正文写明
    保存路径；选取消 → 不拆连接、录音继续；选保存 → 才走 ``_disconnect``。
  * 录音中切换设备：走同一条确认；取消时整条切换不做（不拆旧连接）。
  * 设备被拔出：**不弹确认**（设备已不在，录音无法继续），状态栏写明
    「设备被拔出，录音已停止并保存到 <path>」。

反证：去掉 ``_on_disconnect_clicked`` / ``_connect_backend`` 里的录音确认后，
前两条用例的「必须弹框」「取消后录音继续」同时变红。
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

_REC_PATH = r"D:\mbdsdr\recordings\guard_test.iq"


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


def _fake_exec(boxes, answer):
    def fake_exec(box):
        boxes.append(box)
        return int(answer)
    return fake_exec


def _recording(w, path=_REC_PATH):
    w._recording = True
    w._record_file_path = path


def test_disconnect_while_recording_asks_and_cancel_keeps_recording(
        window, monkeypatch):
    w, mw = window
    boxes = []
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        _fake_exec(boxes, mw.QMessageBox.Cancel))
    disconnected = []
    monkeypatch.setattr(w, "_disconnect", lambda: disconnected.append(True))
    _recording(w)

    w._on_disconnect_clicked()

    assert boxes, "录音中断开必须先询问，不能直接停录"
    box = boxes[0]
    assert box.button(mw.QMessageBox.Cancel).text() == "取消（继续录音）"
    assert box.button(mw.QMessageBox.Save).text() == "停止录音并断开连接"
    assert box.defaultButton() is box.button(mw.QMessageBox.Cancel), \
        "默认按钮必须是「取消（继续录音）」"
    assert _REC_PATH in box.text(), "必须写明录音保存到哪个文件"
    assert "正在录音" in box.text()
    assert w._recording is True, "取消后必须继续录音"
    assert disconnected == [], "取消后不得拆连接"
    assert "已取消断开（录音继续）" in w.statusBar().currentMessage()


def test_disconnect_while_recording_save_proceeds(window, monkeypatch):
    w, mw = window
    boxes = []
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        _fake_exec(boxes, mw.QMessageBox.Save))
    disconnected = []
    monkeypatch.setattr(w, "_disconnect", lambda: disconnected.append(True))
    _recording(w)

    w._on_disconnect_clicked()

    assert boxes, "录音中断开必须先询问"
    assert disconnected == [True], "选「停止录音并断开连接」后才拆连接"


def test_switch_device_while_recording_asks_and_cancel_aborts_switch(
        window, monkeypatch):
    w, mw = window
    boxes = []
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        _fake_exec(boxes, mw.QMessageBox.Cancel))
    torn_down = []
    monkeypatch.setattr(w, "_teardown_connection",
                        lambda **kw: torn_down.append(True))
    _recording(w)
    w._active_sdr_backend = SimpleNamespace(
        device=SimpleNamespace(name="FakeSDR"), disconnect=lambda: None)
    try:
        ok = w._connect_backend(object())
    finally:
        w._active_sdr_backend = None

    assert ok is False, "取消后整条切换都不做"
    assert boxes, "录音中切换设备必须先询问"
    assert boxes[0].defaultButton() is boxes[0].button(mw.QMessageBox.Cancel)
    assert _REC_PATH in boxes[0].text()
    assert torn_down == [], "取消后不得拆旧连接"
    assert w._recording is True
    assert "已取消切换（录音继续）" in w.statusBar().currentMessage()


def test_unplugged_while_recording_does_not_ask_and_reports_path(
        window, monkeypatch):
    w, mw = window
    boxes = []
    monkeypatch.setattr(mw.QMessageBox, "exec", _fake_exec(boxes, 0))
    disconnected = []
    monkeypatch.setattr(w, "_disconnect", lambda: disconnected.append(True))
    _recording(w)

    w._on_device_unplugged()

    assert boxes == [], "拔设备不弹确认：设备已不在，问也拦不住"
    assert disconnected == [True], "拔设备仍必须走断开收尾"
    msg = w.statusBar().currentMessage()
    assert "设备被拔出" in msg
    assert _REC_PATH in msg, "必须写明录音保存到哪个文件"


def test_unplugged_without_recording_does_not_claim_saved(
        window, monkeypatch):
    w, mw = window
    monkeypatch.setattr(w, "_disconnect", lambda: None)
    w._recording = False

    w._on_device_unplugged()

    msg = w.statusBar().currentMessage()
    assert "设备被拔出" not in msg or "录音" not in msg, \
        "没在录音时不得声称「录音已保存」"

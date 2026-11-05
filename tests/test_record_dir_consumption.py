# SPDX-License-Identifier: MIT
"""录音目录消费点：只读 settings.record_dir，失败必须拒绝录音并显示原文。

反证：当前 `_start_recording` 写死 `~/mbdsdr_recordings`，失败静默退
tempfile；把 record_dir 指到不存在的盘时仍会开始录音。本文件第一条断言即红。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

_LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(350)
    yield w
    try:
        w._recording = False
        if w._record_timer is not None:
            w._record_timer.stop()
        w._teardown_connection()
    except Exception:
        _LOGGER.exception("MainWindow teardown 失败")


class _DummyBackend:
    status = type("S", (), {"gain_db": 20.0})()

    def read_samples(self, n):
        return None

    def get_sample_rate(self):
        return 1_000_000

    def get_frequency(self):
        return 100e6


def test_missing_record_drive_rejects_recording(window, monkeypatch):
    w = window
    calls = []
    # 打桩实例方法而非 QMessageBox.warning：_start_recording 走的是
    # _show_error_dialog -> QMessageBox.exec()，warning 根本不在调用路径上。
    # offscreen 下 exec() 永不返回（曾挂死 50 分钟，py-spy 栈取证：
    # _show_error_dialog -> box.exec()）。
    monkeypatch.setattr(
        w, "_show_error_dialog",
        lambda icon, title, text, code=None: calls.append((icon, title, text)))
    monkeypatch.setattr(w, "_open_record_segment", lambda *a, **k: None)
    w._active_sdr_backend = _DummyBackend()
    w.settings.set("record_dir", r"Z:\nope")

    w._start_recording()

    assert w._recording is False, "录音目录不可用时必须拒绝开始录音"
    assert calls, "必须弹窗说明目录不可用"
    assert calls[0][1] == "录音目录不可用", "弹窗标题必须是「录音目录不可用」"
    assert "录音目录不可用" in str(calls[0][2]), "弹窗必须显示设置线给的原文"
    assert "录音目录不可用" in w.statusBar().currentMessage(), (
        "状态栏也要留原因")
    assert not os.path.exists(r"Z:\nope"), "失败时不许偷偷建目录"


def test_unwritable_record_dir_rejects_recording(window, monkeypatch, tmp_path):
    """目录存在但不可写：同样必须拒绝，且文案仍是 ensure_record_dir 原文。

    Windows 下 chmod 去写位并不阻止当前用户建文件，这里用真实存在的目录 +
    对该目录注入 PermissionError(13) 模拟只读；断言口径与「盘不存在」完全一致，
    不许消费方另拼第二套文案。
    """
    w = window
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    calls = []
    # 打桩实例方法而非 QMessageBox.warning：_start_recording 走的是
    # _show_error_dialog -> QMessageBox.exec()，warning 根本不在调用路径上。
    # offscreen 下 exec() 永不返回（曾挂死 50 分钟，py-spy 栈取证：
    # _show_error_dialog -> box.exec()）。
    monkeypatch.setattr(
        w, "_show_error_dialog",
        lambda icon, title, text, code=None: calls.append((icon, title, text)))
    monkeypatch.setattr(w, "_open_record_segment", lambda *a, **k: None)
    w.settings.set("record_dir", str(ro_dir))

    import settings as settings_mod
    real_makedirs = settings_mod.os.makedirs
    target = os.path.normcase(os.path.abspath(str(ro_dir)))

    def deny_readonly(path, *a, **k):
        if os.path.normcase(os.path.abspath(str(path))) == target:
            raise PermissionError(13, "Permission denied")
        return real_makedirs(path, *a, **k)

    monkeypatch.setattr(settings_mod.os, "makedirs", deny_readonly)
    w._active_sdr_backend = _DummyBackend()

    w._start_recording()

    assert w._recording is False, "不可写目录也必须拒绝开始录音"
    assert calls, "必须弹窗说明目录不可用"
    assert calls[0][1] == "录音目录不可用", "弹窗标题必须是「录音目录不可用」"
    assert "录音目录不可用" in str(calls[0][2]), "弹窗必须是 ensure_record_dir 原文"
    assert "录音目录不可用" in w.statusBar().currentMessage(), (
        "状态栏也要留同一份原文")


def test_replay_dialog_uses_record_dir(window, monkeypatch, tmp_path):
    w = window
    rec_dir = tmp_path / "rec"
    w.settings.set("record_dir", str(rec_dir))
    captured = {}

    def fake_dialog(_parent, _title, start_dir, _filters):
        captured["dir"] = start_dir
        return "", ""

    monkeypatch.setattr("main_window.QFileDialog.getOpenFileName", fake_dialog)
    w._replay_recording()

    assert captured["dir"] == w.settings.record_dir, (
        f"回放初始目录应读 settings.record_dir，实际: {captured.get('dir')}")

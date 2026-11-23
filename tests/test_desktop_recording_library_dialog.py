# SPDX-License-Identifier: MIT
"""M4 录音库对话框：列出 / 回放 / 移入回收站。

口径（与实现同一份）：
1. 删除 = 移入 ``_trash/``，不是物理删除；对话框必须自己说清楚。
2. 空目录/目录不存在给人话，不显示假条目；回放/删除按钮置灰。
3. 回放只发路径信号，由主窗口现有回放链执行（对话框不读 IQ）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make_recording(root, name, n=8):
    from mbdsdr_ai.recorder import IQRecorder

    rec = IQRecorder(str(root / name), sample_rate=1.0e6,
                     center_freq_hz=100.0e6, device="RTL-SDR")
    assert rec.open()
    rec.write(np.ones(n, dtype=np.complex64))
    assert rec.close()
    return str(root / (name + ".sigmf-data"))


def test_format_helpers_do_not_guess():
    from recording_library_dialog import (
        format_duration, format_size, state_text)

    assert state_text("ok") == "正常"
    assert state_text("missing") == "文件不存在"
    assert state_text("weird") == "weird"
    assert format_duration(None) == "—"
    assert format_duration(-1) == "—"
    assert format_duration(3.25) == "3.2 s"
    assert format_duration(0.004) == "4 ms"
    assert format_duration(90) == "1.5 min"
    assert format_duration(7200) == "2.0 h"
    assert format_size(None) == "—"
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2.0 KB"


def test_dialog_lists_real_recording_and_enables_actions(qapp, tmp_path):
    from recording_library_dialog import RecordingLibraryDialog

    data = _make_recording(tmp_path, "one")
    dlg = RecordingLibraryDialog(str(tmp_path))
    try:
        assert dlg.table.rowCount() == 1
        assert dlg.table.item(0, 0).text() == os.path.basename(data)
        assert dlg.table.item(0, 4).text() == "正常"
        assert dlg.replay_btn.isEnabled() is False  # 未选中
        dlg.table.selectRow(0)
        assert dlg.replay_btn.isEnabled() is True
        assert dlg.trash_btn.isEnabled() is True
        assert "回收站" in dlg.trash_btn.toolTip()
    finally:
        dlg.close()


def test_dialog_replay_emits_path_and_closes(qapp, tmp_path):
    from recording_library_dialog import RecordingLibraryDialog

    data = _make_recording(tmp_path, "two")
    dlg = RecordingLibraryDialog(str(tmp_path))
    got = []
    dlg.replay_requested.connect(got.append)
    try:
        dlg.table.selectRow(0)
        dlg._on_replay()
        assert got == [data]
        assert dlg.result() == dlg.DialogCode.Accepted
    finally:
        dlg.close()


def test_dialog_trash_moves_file_to_trash(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from recording_library_dialog import RecordingLibraryDialog

    data = _make_recording(tmp_path, "three")
    dlg = RecordingLibraryDialog(str(tmp_path))
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes)
    try:
        dlg.table.selectRow(0)
        dlg._on_trash()
        assert not os.path.exists(data)
        trash = tmp_path / "_trash"
        assert trash.is_dir()
        moved = [p for p in trash.rglob("*") if p.is_file()]
        assert any(p.name.startswith("three") for p in moved)
        # 刷新后列表为空，按钮回到禁用
        assert dlg.table.rowCount() == 0
        assert dlg.replay_btn.isEnabled() is False
        assert dlg.trash_btn.isEnabled() is False
    finally:
        dlg.close()


def test_dialog_empty_directory_explains_and_disables(qapp, tmp_path):
    from recording_library_dialog import RecordingLibraryDialog

    dlg = RecordingLibraryDialog(str(tmp_path))
    try:
        assert dlg.table.rowCount() == 0
        assert dlg.table.isVisibleTo(dlg) is False
        assert dlg.empty_label.isVisibleTo(dlg) is True
        assert "还没有可列出的录音" in dlg.empty_label.text()
        assert dlg.replay_btn.isEnabled() is False
        assert dlg.trash_btn.isEnabled() is False
    finally:
        dlg.close()


def test_dialog_missing_directory_is_honest(qapp, tmp_path):
    from recording_library_dialog import RecordingLibraryDialog

    missing = str(tmp_path / "nope")
    dlg = RecordingLibraryDialog(missing)
    try:
        assert "录音目录还不存在" in dlg.status.text()
        assert dlg.replay_btn.isEnabled() is False
        assert dlg.trash_btn.isEnabled() is False
    finally:
        dlg.close()


def test_dialog_missing_file_row_cannot_replay(qapp, tmp_path):
    from recording_library_dialog import RecordingLibraryDialog

    data = _make_recording(tmp_path, "four")
    dlg = RecordingLibraryDialog(str(tmp_path))
    try:
        os.remove(data)
        os.remove(os.path.splitext(data)[0] + ".sigmf-meta")
        dlg.refresh()  # 核对磁盘 → 条目状态 missing
        assert dlg.table.rowCount() == 1
        assert dlg.table.item(0, 4).text() == "文件不存在"
        dlg.table.selectRow(0)
        assert dlg.replay_btn.isEnabled() is False
        assert "文件不存在" in dlg.replay_btn.toolTip()
        # 文件不在了仍可把索引标记为已回收
        assert dlg.trash_btn.isEnabled() is True
    finally:
        dlg.close()

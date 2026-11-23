# SPDX-License-Identifier: MIT
"""M5：启动扫描未完成录音——只提示，不删不改。

本文件钉三件事：
1. **只认显式未完成**：sidecar ``complete: false`` 才算中止；缺字段按完整，
   坏 JSON 不当线索（可能是别的配置）。
2. **残片单列**：``*.part/*.partial/*.tmp`` 只在磁盘上存在就报，不猜内容。
3. **扫描不改文件**：前后字节与 mtime 不变；UI 只把按钮点亮，不弹模态。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_aborted_sidecar_is_reported_with_reason(tmp_path):
    from recording_observation import find_incomplete_recordings

    data = _write(tmp_path / "a.iq", "IQ")
    _write(tmp_path / "a.iq.json", json.dumps(
        {"complete": False, "aborted_reason": "磁盘写满"}))
    got = find_incomplete_recordings(str(tmp_path))
    assert len(got) == 1
    item = got[0]
    assert item["kind"] == "aborted"
    assert item["path"] == str(data)
    assert item["reason"] == "磁盘写满"
    assert item["data_missing"] is False
    # size_bytes 报的是数据文件大小，不是 sidecar 大小（数据 = "IQ" 2 字节）
    assert item["size_bytes"] == 2


def test_complete_sidecar_is_not_reported(tmp_path):
    from recording_observation import find_incomplete_recordings

    _write(tmp_path / "b.iq", "IQ")
    _write(tmp_path / "b.iq.json", json.dumps({"complete": True}))
    # 老 sidecar 没有 complete 字段：按完整读，不报
    _write(tmp_path / "c.iq", "IQ")
    _write(tmp_path / "c.iq.json", json.dumps({"sample_rate": 2.4e6}))
    assert find_incomplete_recordings(str(tmp_path)) == []


def test_broken_json_is_not_a_lead(tmp_path):
    from recording_observation import find_incomplete_recordings

    _write(tmp_path / "notes.json", "{not json")
    _write(tmp_path / "array.json", "[1, 2]")
    assert find_incomplete_recordings(str(tmp_path)) == []


def test_residual_temp_files_are_reported(tmp_path):
    from recording_observation import find_incomplete_recordings

    _write(tmp_path / "x.iq.part", "partial")
    _write(tmp_path / "y.tmp", "tmp")
    got = find_incomplete_recordings(str(tmp_path))
    assert [i["kind"] for i in got] == ["residual", "residual"]
    assert all(i["sidecar"] == "" for i in got)


def test_missing_data_file_is_flagged(tmp_path):
    from recording_observation import find_incomplete_recordings

    _write(tmp_path / "gone.iq.json", json.dumps({"complete": False}))
    got = find_incomplete_recordings(str(tmp_path))
    assert len(got) == 1
    assert got[0]["data_missing"] is True


def test_scan_does_not_touch_files(tmp_path):
    from recording_observation import find_incomplete_recordings

    data = _write(tmp_path / "a.iq", "IQ")
    sidecar = _write(tmp_path / "a.iq.json", json.dumps(
        {"complete": False, "aborted_reason": "x"}))
    before = [(os.path.getsize(p), os.stat(p).st_mtime_ns)
              for p in (data, sidecar)]
    find_incomplete_recordings(str(tmp_path))
    after = [(os.path.getsize(p), os.stat(p).st_mtime_ns)
             for p in (data, sidecar)]
    assert before == after


def test_missing_directory_is_empty_not_error():
    from recording_observation import find_incomplete_recordings

    assert find_incomplete_recordings(os.path.join(_ROOT, "_no_such_dir_")) == []


def test_max_entries_caps_result(tmp_path):
    from recording_observation import find_incomplete_recordings

    for i in range(5):
        _write(tmp_path / f"{i}.part", "x")
    assert len(find_incomplete_recordings(str(tmp_path), max_entries=3)) == 3


class _Btn:
    def __init__(self):
        self.visible = None
        self.text = ""
        self.tip = ""

    def setVisible(self, value):  # noqa: N802 (Qt 命名)
        self.visible = value

    def setText(self, value):  # noqa: N802
        self.text = value

    def setToolTip(self, value):  # noqa: N802
        self.tip = value


class _Status:
    def __init__(self):
        self.message = ""

    def showMessage(self, text, *_a):  # noqa: N802
        self.message = text


class _WinStub:
    def __init__(self, directory):
        from types import SimpleNamespace

        self.settings = SimpleNamespace(record_dir=directory)
        self.incomplete_rec_btn = _Btn()
        self._incomplete_findings = []
        self._status = _Status()

    def statusBar(self):  # noqa: N802
        return self._status


def test_window_scan_lights_button_without_modal(tmp_path):
    """按钮点亮 + 状态栏提示；扫描本身不弹窗（弹窗只在用户点击时出现）。"""
    from main_window import MainWindow

    _write(tmp_path / "a.iq", "IQ")
    _write(tmp_path / "a.iq.json", json.dumps(
        {"complete": False, "aborted_reason": "断电"}))
    stub = _WinStub(str(tmp_path))
    MainWindow._scan_incomplete_recordings(stub)
    assert stub.incomplete_rec_btn.visible is True
    assert stub.incomplete_rec_btn.text == "未完成录音 1"
    assert "断电" not in stub.incomplete_rec_btn.tip  # tooltip 只给名字，细节在弹窗
    assert "未自动删除" in stub._status.message


def test_window_scan_hides_button_when_clean(tmp_path):
    from main_window import MainWindow

    stub = _WinStub(str(tmp_path))
    MainWindow._scan_incomplete_recordings(stub)
    assert stub.incomplete_rec_btn.visible is False
    assert stub._incomplete_findings == []

# SPDX-License-Identifier: MIT
"""内部发起的停止必须复位录音按钮（R-01）。

三条路径：写失败、开始回放、设备掉线。三者都不经过用户点按钮，
但都必须走同一个 ``_stop_recording`` 出口：
  * 工具栏录音按钮 ``record_btn.isChecked()`` 回到 False；
  * 控制面板 ``record_button.isChecked()`` 回到 False；
  * 面板文案不得是「录音完成 (N 秒)」。

反证：若 ``_stop_recording`` 退回「只改文案、不 setChecked」，三条断言全红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402
from settings import DesktopSettings  # noqa: E402


@pytest.fixture()
def window(isolated_profile):
    w = MainWindow()
    # MainWindow 构造时 QStandardPaths 可能已缓存真实 %APPDATA%；拆连接会写
    # 跟踪/收听状态，必须把 settings 显式钉到本模块临时目录，避免污染真配置。
    w.settings = DesktopSettings.load(
        str(isolated_profile / "desktop_settings.json"))
    w._active_sdr_backend = None
    w._recording = False
    yield w
    w._disconnect()
    w.close()


def _start_segment(w, path, sr=1_000_000.0, center=100_000_000.0):
    """按 ``_start_recording`` 成功后的最小状态开一段，并把两个按钮置为按下。

    按钮用 ``blockSignals`` 直接置位：这里要模拟「已经在录」的 UI 快照，
    不能触发 ``_toggle_record``/``_start_recording``（否则无后端会立刻复位，
    测的就不是停止路径了）。
    """
    w._record_session_path = str(path)
    w._record_segments = []
    w._record_segment_index = 1
    w._record_rate_changes = []
    w._record_seconds = 0
    w._record_gain_db = 0.0
    w._record_device = "Stub"
    w._record_driver = "StubBackend"
    w._record_start_time = "2026-11-11T00:00:00"
    w._record_timer = None
    ok = w._open_record_segment(
        str(path), sr, center, "2026-11-11T00:00:00", 1)
    if not ok:
        return False
    w._recording = True
    for btn in (w.record_btn, w.control_panel.record_button):
        btn.blockSignals(True)
        btn.setChecked(True)
        btn.blockSignals(False)
    return True


class _FailAfter:
    """委托真实文件对象，但第 ``ok`` 次之后的 write 抛磁盘满。"""

    def __init__(self, fh, ok=0):
        self._fh = fh
        self._ok = int(ok)

    def write(self, data):
        if self._ok <= 0:
            raise OSError(28, "No space left on device")
        self._ok -= 1
        return self._fh.write(data)

    def close(self):
        self._fh.close()

    def flush(self):
        self._fh.flush()


def _status(w):
    return w.statusBar().currentMessage()


def _assert_released(w):
    assert w._recording is False
    assert w.record_btn.isChecked() is False, "工具栏录音按钮仍处于按下态"
    assert w.control_panel.record_button.isChecked() is False, "面板录音按钮仍处于按下态"
    assert "完成" not in w.control_panel.record_status.text(), (
        f"面板仍显示完成态: {w.control_panel.record_status.text()!r}")
    # 面板说真话还不够：状态栏同一句话也不得把内部中止落成「录制完成」
    # （2026-11-11 实测：面板已改对，状态栏仍是「…失败…；录制完成: …」）。
    assert "完成" not in _status(w), f"状态栏仍显示完成态: {_status(w)!r}"


def test_write_failure_releases_button(window, tmp_path):
    """写失败（ENOSPC）走 _report_record_abort → _stop_recording。"""
    assert _start_segment(window, tmp_path / "fail.iq") is True
    window._record_write_chunk(np.ones(1024, dtype=np.complex64))
    window._record_stream_fh = _FailAfter(window._record_stream_fh, ok=0)
    window._record_write_chunk(np.ones(1024, dtype=np.complex64))
    _assert_released(window)
    assert "No space left on device" in _status(window), (
        f"状态栏必须写明中止原因，实际: {_status(window)!r}")


def test_replay_start_releases_button(window, tmp_path):
    """录音中启动回放：先落盘停录，再起播。"""
    path = tmp_path / "replay.iq"
    assert _start_segment(window, path) is True
    window._record_write_chunk(np.ones(1024, dtype=np.complex64))
    rec = {
        "iq": np.ones(4096, dtype=np.complex64),
        "sample_rate": 1_000_000.0,
        "center_freq_hz": 100_000_000.0,
        "segments": [],
        "duration_s": 0.004,
        "truncated": False,
    }
    window._begin_replay_from_rec(rec, str(path))
    try:
        _assert_released(window)
    finally:
        window._stop_replay()


def test_backend_lost_releases_button(window, tmp_path):
    """设备掉线：_teardown_connection 里 _apply_recording(False) 落盘停录。"""
    assert _start_segment(window, tmp_path / "lost.iq") is True
    window._record_write_chunk(np.ones(1024, dtype=np.complex64))
    window._handle_backend_lost("设备已断开")
    _assert_released(window)

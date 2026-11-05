# SPDX-License-Identifier: MIT
"""S5 / S2 修复后专用护栏（无硬件，offscreen）。

S5：录音与回放互斥（docs/interaction-flows.md 路径 2）
  - 录音中选文件回放：必须先停录（关流 + 写 sidecar），再进入回放；
  - 回放中按录音：必须先停回放，再开录。
  修复前探针：删掉 `_replay_recording` 里的 `_apply_recording(False)` 或
  `_start_recording` 里的 `_stop_replay()`，本文件对应用例即红。

S2：配置损坏（路径 1 / 6）
  - 解析失败先改名备份，原字节完整保留，不静默覆盖；
  - 保存走 tmp + os.replace；replace 失败时原文件必须原样可读；
  - 备份失败时拒绝保存（不覆盖唯一证据）。
  修复前探针：把 `_save_gui_config` 改回 `open(...,'w')` 直写、或把
  `_load_gui_config` 的备份分支去掉，本文件断言即红。
"""
import logging
import json
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

log = logging.getLogger(__name__)


class _StubBackend:
    """只提供录音/回放路径需要的最小后端接口。"""

    def __init__(self, sr=2_048_000.0, center=100_000_000.0):
        self._sr = float(sr)
        self._fc = float(center)
        self.connected = True
        self.status = types.SimpleNamespace(gain_db=0.0)
        self.disconnect_called = 0

    def read_samples(self, n):
        return np.zeros(int(n), dtype=np.complex64)

    def get_sample_rate(self):
        return self._sr

    def get_frequency(self):
        return self._fc

    def set_sample_rate(self, sr):
        self._sr = float(sr)

    def set_frequency(self, hz):
        self._fc = float(hz)

    def set_gain(self, db):
        pass

    def disconnect(self):
        self.connected = False
        self.disconnect_called += 1

    def get_status(self):
        return types.SimpleNamespace(connected=self.connected, error=None)


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
    QTest.qWait(350)  # 让 100ms 读配置 / 200ms 枚举提示跑完
    yield w
    try:
        w._teardown_connection()
    except Exception as exc:  # noqa: BLE001 - 收尾失败必须留证，不静默
        log.warning("test_record_replay_config.py fixture 收尾失败（MainWindow._teardown_connection）：%s: %s",
                    type(exc).__name__, exc)


def _isolate_home(monkeypatch, tmp_path):
    """把 ~ 指到临时目录，避免测试碰用户的 ~/.mbdsdr。"""
    home = str(tmp_path)

    def fake_expanduser(path):
        if isinstance(path, str) and path.startswith("~"):
            return home + path[1:]
        return path

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    return home


def _make_recording(window, monkeypatch, tmp_path):
    """连桩后端并开录，返回 (录制路径, 桩后端)。

    必须真写一块样本：S4 分段落盘对零样本段按设计删档、不写 sidecar，
    只开会话不喂数据会造出"录音中却零采样"的假状态（正是本文件要防的）。
    录制目录也指到 tmp_path，避免读写真机 settings 里的 record_dir。
    """
    monkeypatch.setattr(window.settings, "ensure_record_dir",
                        lambda: (str(tmp_path), None))
    backend = _StubBackend()
    window._active_sdr_backend = backend
    window._start_recording()
    assert window._recording is True
    assert window._record_stream_fh is not None, "录音未建立流式落盘句柄"
    window._record_write_chunk(np.ones(4096, dtype=np.complex64))
    assert window._record_samples_total > 0, "录音没有落盘任何样本"
    return window._record_file_path, backend


# ---------------------------------------------------------------- S5 互斥
def test_replay_stops_recording_and_saves_sidecar(window, monkeypatch, tmp_path):
    """录音中点回放：先停录落盘，再回放（不允许"录音中却零采样"假状态）。"""
    import main_window as mw
    from mbdsdr_ai import baseband_io

    _isolate_home(monkeypatch, tmp_path)
    rec_path, _backend = _make_recording(window, monkeypatch, tmp_path)

    iq_path = str(tmp_path / "replay_src.iq")
    # 300k 样本 ≈ 0.15s，确保断言时回放仍在进行（小文件会一 tick 播完）
    baseband_io.save_iq(np.ones(300_000, dtype=np.complex64), iq_path,
                        sample_rate=2_048_000.0, center_freq_hz=100e6)
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName",
                        lambda *a, **k: (iq_path, ""))

    window._replay_recording()

    assert window._recording is False, "S5：回放开始后录音仍为 True"
    assert window._record_stream_fh is None, "S5：回放开始后录音文件句柄仍开着"
    assert os.path.exists(rec_path + ".json"), "S5：停录后没有写 sidecar（数据未归档）"
    assert os.path.exists(rec_path), "S5：录音文件丢了"
    assert window._replay_timer is not None, "S5：没有进入回放"
    meta = json.loads(open(rec_path + ".json", encoding="utf-8").read())
    assert "sample_rate" in meta and "center_freq_hz" in meta

    window._teardown_connection()


def test_start_recording_stops_replay_first(window, monkeypatch, tmp_path):
    """回放中点录音：先停回放，再开录（不允许两套状态并行）。"""
    import main_window as mw
    from mbdsdr_ai import baseband_io
    from PySide6.QtTest import QTest

    _isolate_home(monkeypatch, tmp_path)
    iq_path = str(tmp_path / "replay_src2.iq")
    baseband_io.save_iq(np.ones(4096, dtype=np.complex64), iq_path,
                        sample_rate=2_048_000.0, center_freq_hz=100e6)
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName",
                        lambda *a, **k: (iq_path, ""))
    window._active_sdr_backend = _StubBackend()
    window._replay_recording()
    assert window._replay_timer is not None

    window._start_recording()
    assert window._replay_timer is None, "S5：开录时没有先停回放"
    assert window._recording is True and window._record_stream_fh is not None
    window._apply_recording(False)
    QTest.qWait(50)
    window._teardown_connection()


# ---------------------------------------------------------------- S2 配置
def _write_config(tmp_path, text):
    d = tmp_path / ".mbdsdr"
    d.mkdir(exist_ok=True)
    cfg = d / "gui_config.json"
    cfg.write_text(text, encoding="utf-8")
    return str(cfg)


def test_corrupt_config_backed_up_and_original_bytes_kept(window, monkeypatch, tmp_path):
    """S2：半截 JSON 不得静默覆盖；先改名备份，原字节完整保留。"""
    _isolate_home(monkeypatch, tmp_path)
    bad = '{"observer_lat": 31.2,'  # 写到一半被杀
    cfg_path = _write_config(tmp_path, bad)

    window._load_gui_config()

    assert getattr(window, "_gui_config_corrupt", False) is True, \
        "S2：损坏配置没有进入损坏态"
    backup = getattr(window, "_gui_config_corrupt_backup", None)
    assert backup and os.path.exists(backup), "S2：没有留下备份文件"
    assert open(backup, encoding="utf-8").read() == bad, "S2：备份内容不完整"
    assert not os.path.exists(cfg_path), "S2：损坏文件仍留在原位（会被后续保存覆盖）"

    # 备份成功后才允许保存：新文件必须是完整 JSON，且没有残留 .tmp
    window._save_gui_config()
    data = json.loads(open(cfg_path, encoding="utf-8").read())
    assert isinstance(data, dict) and "window" in data
    assert not os.path.exists(cfg_path + ".tmp"), "S2：原子写留下了临时文件"

    window._gui_config_corrupt = False
    window._gui_config_corrupt_backup = None


def test_atomic_save_keeps_previous_file_when_replace_fails(window, monkeypatch, tmp_path):
    """S2：rename 失败（磁盘满/被杀）时，旧配置必须原样可读。"""
    import main_window as mw

    _isolate_home(monkeypatch, tmp_path)
    old = json.dumps({"window": {"width": 1000, "height": 700},
                      "observer_lat": 10.0})
    cfg_path = _write_config(tmp_path, old)

    window._observer_lat = 55.0

    def boom(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(mw.os, "replace", boom)
    window._save_gui_config()

    assert open(cfg_path, encoding="utf-8").read() == old, \
        "S2：保存失败时旧配置被截断/覆盖"
    json.loads(open(cfg_path, encoding="utf-8").read())
    window._observer_lat = None


def test_corrupt_without_backup_refuses_to_save(window, monkeypatch, tmp_path):
    """S2：备份都失败时必须拒绝保存——原文件是唯一证据。"""
    import main_window as mw

    _isolate_home(monkeypatch, tmp_path)
    bad = '{"observer_lat": 31.2,'
    cfg_path = _write_config(tmp_path, bad)

    def boom(src, dst):
        raise OSError("cannot rename")

    monkeypatch.setattr(mw.os, "replace", boom)
    window._load_gui_config()
    assert getattr(window, "_gui_config_corrupt", False) is True
    assert getattr(window, "_gui_config_corrupt_backup", None) is None

    window._observer_lat = 55.0
    window._save_gui_config()
    assert open(cfg_path, encoding="utf-8").read() == bad, \
        "S2：备份失败后仍用默认值覆盖了唯一证据"

    window._gui_config_corrupt = False
    window._gui_config_corrupt_backup = None

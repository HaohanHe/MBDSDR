#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-09 独立复现脚本：录音中调谐后，sidecar 的中心频率是否更新。

roadmap 断言（原文）：
    「录音中调谐后 sidecar 中心频率不更新，回放绝对频率整份错位」
    证据等级：实测（gap-recording §2）。

做法（offscreen + 独立 profile + 真实 MainWindow）：
  1. 用产品方法 ``_open_record_segment`` 在 145.5 MHz 打开一段，写 4096 样本；
  2. 调 ``_on_tune_sdr(146.5 MHz)``（真实调谐入口）；
  3. ``_stop_recording()`` 收尾，读 sidecar JSON；
  4. 比对：sidecar.center_freq_hz 是否仍停在 145.5 MHz，且是否存在
     ``center_freq_changes`` / 多段记录。

退出码（三态）：
    0  复现成功   —— sidecar 中心频率是旧值，且没有任何变更记录
    1  未复现     —— sidecar 已带新中心频率或带 center_freq_changes
    2  无法判定   —— 构造/开段/收尾失败

反证对照：本脚本同时打印「若按修复方案写 center_freq_changes，回放端
应能还原 146.5 MHz」的差值；当前实现该字段不存在，即旧行为。

用法：
    python scripts/repro/2026-11-11-p0-09-record-center-freq-sidecar.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
from datetime import datetime

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


class _StubBackend:
    def __init__(self):
        self.status = types.SimpleNamespace(error="", gain_db=12.5,
                                            demod_mode="FM", connected=True)
        self.freq = 145.5e6
        self.sr = 2.048e6

    def get_sample_rate(self):
        return self.sr

    def get_frequency(self):
        return self.freq

    def set_frequency(self, f):
        self.freq = float(f)

    def set_demod(self, _m):
        pass

    def read_samples(self, n):
        import numpy as np
        return np.zeros(int(n), dtype=np.complex64)


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-09-profile-", dir=TMP_ROOT)
    rec_dir = tempfile.mkdtemp(prefix="p0-09-rec-", dir=TMP_ROOT)
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[k] = profile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        app = QApplication.instance() or QApplication(sys.argv[:1])
        import main_window as mw
        window = mw.MainWindow()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] Qt/MainWindow 构造失败：{type(exc).__name__}: {exc}")
        return 2

    backend = _StubBackend()
    window._active_sdr_backend = backend
    path = os.path.join(rec_dir, "baseband_p0_09.iq")
    start = datetime.now().isoformat(timespec="seconds")
    window._record_session_path = path
    window._record_segments = []
    window._record_segment_index = 1
    window._record_rate_changes = []
    window._record_start_time = start
    window._record_gain_db = 12.5
    window._record_device = "StubDevice"
    window._record_driver = "StubBackend"
    window._record_seconds = 0

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"临时档案: {profile}；录音目录: {rec_dir}")

    _hr("实测：145.5 MHz 开段 → 写到 146.5 MHz → 停止")
    try:
        if not window._open_record_segment(path, 2.048e6, 145.5e6, start, 1):
            print("[无法判定] _open_record_segment 失败。")
            return 2
        window._recording = True
        window._record_stream_fh.write(b"\x00" * (8 * 4096))
        window._record_samples_total = 4096
        app.processEvents()

        print(f"  开段中心频率 : {window._record_center_hz:,.0f} Hz")
        window._on_tune_sdr(146.5e6, "FM")
        app.processEvents()
        print(f"  调谐后后端频率: {backend.freq:,.0f} Hz")
        print(f"  调谐后录音中心: {window._record_center_hz:,.0f} Hz")
        window._stop_recording()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] 录音/调谐/收尾抛错：{type(exc).__name__}: {exc}")
        return 2

    segments = list(getattr(window, "_record_segments", None) or [])
    print(f"  落盘段数     : {len(segments)}")
    if not segments:
        print("[无法判定] 没有落盘段，无法检查 sidecar。")
        return 2
    seg = segments[-1]
    sidecar = seg.get("sidecar")
    print(f"  sidecar      : {sidecar}")
    if not sidecar or not os.path.exists(sidecar):
        print("[无法判定] sidecar 不存在。")
        return 2
    with open(sidecar, "r", encoding="utf-8") as f:
        meta = json.load(f)

    center = float(meta.get("center_freq_hz") or 0.0)
    changes = meta.get("center_freq_changes")
    segments_meta = meta.get("segments")
    print(f"  sidecar.center_freq_hz : {center:,.0f} Hz")
    print(f"  center_freq_changes    : {changes!r}")
    print(f"  segments 字段          : {segments_meta!r}")
    print(f"  回放按 sidecar 还原    : {center:,.0f} Hz")
    print(f"  实际信号中心          : {backend.freq:,.0f} Hz")
    print(f"  错位                  : {backend.freq - center:,.0f} Hz")

    try:
        window.close()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(profile, ignore_errors=True)
    shutil.rmtree(rec_dir, ignore_errors=True)

    stale = abs(center - 145.5e6) < 1.0 and abs(backend.freq - 146.5e6) < 1.0
    recorded_change = bool(changes) or bool(segments_meta)
    reproduced = stale and not recorded_change
    print(f"\n结论：当前实现复现 P0-09（sidecar 中心频率未更新）: {reproduced}")
    if reproduced:
        print("[复现] 调谐后 sidecar 仍写开段时的 145.5 MHz，且没有 "
              "center_freq_changes；回放整段会偏 1.0 MHz。")
        return 0
    print("[未复现] sidecar 已按调谐更新，或带变更记录。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

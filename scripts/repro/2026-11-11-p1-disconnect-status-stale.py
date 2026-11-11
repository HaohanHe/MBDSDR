#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""反证脚本：用户点「断开连接」后，状态栏消息区是否还留着「已连接 …」。

待排工单（尚未分配编号）——症状：
    `desktop/main_window.py:6940-6945` `_disconnect()` 只在「本来就没连」时写
    状态栏；真正有设备时只调 `_teardown_connection()`，而该函数
    （`:6778-6800`）刷新的是底部状态标签（`status_conn` 等），**不会覆盖
    状态栏消息区**。于是 `:6195` 连接成功时写的 `已连接 <设备名>` 会一直留着，
    用户点完「断开」看到的第一行仍然是「已连接」。

用法：
    python scripts/repro/2026-11-11-p1-disconnect-status-stale.py

退出码（三态，与 scripts/repro 其余脚本一致）：
    0  复现成功 —— 断开后状态栏/消息区仍含「已连接」
    1  未复现   —— 断开后已不再含「已连接」
    2  无法判定 —— Qt/MainWindow 构造失败或没能进入「已连接」态

落库时要补的那条断言（文件空出来后直接搬进 tests/）：
    断开后 `statusBar().currentMessage()` 与状态栏内所有 QLabel 文案
    都不得出现「已连接」；且应出现「已断开 / 未连接」之类的收口文案。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


class _StubBackend:
    """只满足「已连接」判定的最小后端。"""

    def __init__(self):
        self.status = types.SimpleNamespace(error="", gain_db=12.5,
                                            demod_mode="FM", connected=True)
        self.freq = 100.0e6
        self.sr = 2.048e6

    def get_sample_rate(self):
        return self.sr

    def get_frequency(self):
        return self.freq

    def read_samples(self, n):
        import numpy as np
        return np.zeros(int(n), dtype=np.complex64)

    def close(self):
        pass


def _status_widgets_text(window) -> list[str]:
    """状态栏里所有可见文案：消息区 + 常驻 QLabel。"""
    from PySide6.QtWidgets import QLabel
    bar = window.statusBar()
    texts = [bar.currentMessage()]
    for lab in bar.findChildren(QLabel):
        try:
            texts.append(lab.text())
        except Exception:  # noqa: BLE001
            pass
    return [t for t in texts if t]


def main() -> int:
    os.makedirs(TMP_ROOT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="p1-disc-profile-", dir=TMP_ROOT)
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

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"临时档案: {profile}")

    # 进入「已连接」态：后端存在 + 复刻连接成功时的状态栏文案（:6195）。
    # 先让构造/连接态的异步消息（TLE 等）跑完，再写「已连接」，
    # 否则会被后到的提示盖掉，探针就测不到真正要测的那一行。
    window._active_sdr_backend = _StubBackend()
    window._panels_set_sdr_connected(True)
    app.processEvents()
    window.statusBar().showMessage("已连接 Stub SDR", 8000)

    before = _status_widgets_text(window)
    print("\n断开前状态栏文案：")
    for t in before:
        print(f"  - {t!r}")
    if not any("已连接" in t for t in before):
        print("[无法判定] 未能构造出「已连接」态。")
        return 2

    print("\n调用 _disconnect()（等价于用户点工具栏「断开」）…")
    try:
        window._disconnect()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] _disconnect 异常：{type(exc).__name__}: {exc}")
        return 2

    after = _status_widgets_text(window)
    print("\n断开后状态栏文案：")
    for t in after:
        print(f"  - {t!r}")

    stale = [t for t in after if "已连接" in t]
    closed = [t for t in after if ("断开" in t or "未连接" in t)]
    print(f"\nstatus_conn: {window.status_conn.text()!r}（面板已复位，此为对照）")
    print(f"仍含「已连接」的文案：{len(stale)} 条；含断开收口文案：{len(closed)} 条")

    try:
        window.close()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(profile, ignore_errors=True)

    if stale:
        print("\n[复现] 断开后状态栏仍留着「已连接」，且没有收口文案覆盖它。")
        return 0
    if not closed:
        print("\n[复现] 「已连接」没了，但也没有任何断开/未连接的收口文案——"
              "用户看到的是一行与断开无关的旧消息。")
        return 0
    print("\n[未复现] 断开后状态栏已收口，不再出现「已连接」。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

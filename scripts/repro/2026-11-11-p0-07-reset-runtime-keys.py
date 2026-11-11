#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-07 独立复现脚本：「恢复全部默认」是否静默重置不可见运行态键。

roadmap 断言（原文）：
    「『恢复全部默认』静默重置 10 个不可见运行态键，界面不变」
    证据等级：实测（repro_settings_gap.py reset-runtime → 配置 98M/48/FM，
    界面仍是 145.5M/77/USB）。

做法（一个进程、独立 profile）：
  1. 构造真 MainWindow，写入运行态哨兵：frequency_hz / gain / demod /
     bandwidth / volume / app_mode；
  2. 再写一个**可见**可重置项 fft_size=4096（用来证明 reset 确实执行了）；
  3. 调 ``SettingsPanel.reset_all(confirm=False)``；
  4. 读回：运行态必须全部不变，fft_size 必须回默认。

退出码（三态）：
    0  复现成功   —— 至少一个运行态键被重置
    1  未复现     —— 运行态全不变，且 fft_size 确实回到默认
    2  无法判定   —— 构造/reset 失败，或 reset 根本没执行

反证对照：把 ``MBDSDR_REPRO_ROOT`` 指向 59eb6754^ 后重跑，必须出现
运行态键被改回默认（98M/FM/...）。

用法：
    python scripts/repro/2026-11-11-p0-07-reset-runtime-keys.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"

RUNTIME = {
    "frequency_hz": 145.5e6,
    "gain_db": 7.7,
    "demod_mode": "usb",
    "bandwidth_hz": 2400.0,
    "volume": 48,
    "app_mode": "planetarium",
}
RESETTABLE = {"fft_size": 4096}


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-07-profile-", dir=TMP_ROOT)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[k] = profile
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"临时档案: {profile}")

    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication, QMessageBox
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
        app = QApplication.instance() or QApplication(sys.argv[:1])
        from config_schema import SCHEMA
        from main_window import MainWindow
        window = MainWindow()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] Qt/MainWindow 构造失败：{type(exc).__name__}: {exc}")
        return 2

    panel = window.settings_panel

    def _write(key, value):
        item = SCHEMA[key]
        ctl = panel._rows.get(key)
        if ctl is not None:
            if item.kind == "enum":
                ctl.setCurrentIndex(list(item.choice_values).index(value))
            elif item.kind == "int":
                ctl.setValue(int(value))
            elif item.kind == "bool":
                ctl.setChecked(bool(value))
            else:
                ctl.setValue(float(value))
            return
        panel._store_value(item, value)
        panel._refresh_from_storage()

    print("\n" + "=" * 78)
    print("实测：写入运行态哨兵 → reset_all(confirm=False) → 读回")
    print("=" * 78)
    for key, value in RUNTIME.items():
        _write(key, value)
    for key, value in RESETTABLE.items():
        _write(key, value)
    try:
        window.settings.flush()
    except Exception:  # noqa: BLE001
        pass
    app.processEvents()

    before = {k: window.settings.get(k) for k in RUNTIME}
    before_fft = window.settings.get("fft_size")
    print(f"  reset 前运行态：{before}")
    print(f"  reset 前 fft_size：{before_fft}")

    try:
        ok = panel.reset_all(confirm=False)
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] reset_all 抛错：{type(exc).__name__}: {exc}")
        return 2
    print(f"  reset_all 返回：{ok}")

    after = {k: window.settings.get(k) for k in RUNTIME}
    after_fft = window.settings.get("fft_size")
    print(f"  reset 后运行态：{after}")
    print(f"  reset 后 fft_size：{after_fft}")

    diffs = []
    for key, want in before.items():
        got = after.get(key)
        same = (got == want)
        if isinstance(want, float) and isinstance(got, (int, float)):
            same = abs(float(got) - float(want)) < 1e-6
        print(f"  {key:<16} {want!r:<10} → {got!r:<10} {'不变' if same else '被改'}")
        if not same:
            diffs.append((key, want, got))

    reset_ran = (after_fft != before_fft)
    print(f"  reset 确实执行（fft_size 变化）：{reset_ran}")

    try:
        window.close()
    except Exception:  # noqa: BLE001
        pass

    if not reset_ran:
        print("[无法判定] fft_size 没有变化，reset_all 可能未执行，本条不可判定。")
        return 2

    reproduced = bool(diffs)
    print(f"\n结论：当前实现复现 P0-07（运行态被静默重置）: {reproduced}")
    if reproduced:
        print(f"[复现] {len(diffs)} 个运行态键被重置：{diffs}")
        return 0
    print("[未复现] 运行态键全部保持，只有可重置项回到默认。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

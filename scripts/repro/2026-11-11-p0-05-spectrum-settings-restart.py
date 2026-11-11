#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-05 独立复现脚本：3 个频谱设置重启后是否还原到真实消费点。

roadmap 断言（原文）：
    「9 个频谱设置重启后不还原，设置页仍显示旧值」
    证据等级：实测（repro_settings_gap.py set-spectrum + read-spectrum）。
本条复核其中三个有代表性的键：
      fft_size 4096 / waterfall_palette soft / peak_threshold_db 21

做法：两次独立进程（写入 → 新进程读取），共享一个临时 profile：
  1. ``--phase write``：构造真 MainWindow，通过设置面板控件写入三键并 flush；
  2. ``--phase read`` ：全新进程再构造 MainWindow，读**真实消费点**：
        spectrum.generator.fft_size
        spectrum._state.wf_colors（soft / sdrpp）
        spectrum._state.peak_rel_db
  3. 父进程比对并给三态退出码。

退出码（三态）：
    0  复现成功   —— 至少一个键重启后没有回到写入值
    1  未复现     —— 三键全部还原
    2  无法判定   —— Qt/MainWindow 构造失败，或子进程崩溃

反证对照：把 ``MBDSDR_REPRO_ROOT`` 指向修复提交的父提交（685e8f50^）后
重跑本脚本，必须重新出现 4096→1024 / soft→sdrpp / 21→6.0。

用法：
    python scripts/repro/2026-11-11-p0-05-spectrum-settings-restart.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"
EXPECT = {"fft_size": 4096, "waterfall_palette": "soft",
          "peak_threshold_db": 21.0}


def _bootstrap(profile: str):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[k] = profile
    os.makedirs(profile, exist_ok=True)
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

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
    from spectrum_widget import WATERFALL_COLORS, WATERFALL_COLORS_SOFT
    return app, SCHEMA, MainWindow, WATERFALL_COLORS, WATERFALL_COLORS_SOFT


def _emit(obj) -> None:
    print("RESULT_JSON " + json.dumps(obj, ensure_ascii=False, sort_keys=True))
    sys.stdout.flush()


def _drive(w, schema, key, value):
    ctl = w.settings_panel._rows.get(key)
    item = schema[key]
    if ctl is None:
        w.settings_panel._store_value(item, value)
        w.settings_panel._refresh_from_storage()
        return "store_value"
    if item.kind == "enum":
        ctl.setCurrentIndex(list(item.choice_values).index(value))
    elif item.kind == "int":
        ctl.setValue(int(value))
    elif item.kind == "bool":
        ctl.setChecked(bool(value))
    else:
        ctl.setValue(float(value))
    return "widget"


def _phase_write(profile: str) -> int:
    app, schema, MainWindow, _c, _s = _bootstrap(profile)
    w = MainWindow()
    app.processEvents()
    applied = {}
    for key, value in EXPECT.items():
        applied[key] = _drive(w, schema, key, value)
    try:
        w.settings.flush()
    except Exception:  # noqa: BLE001
        pass
    app.processEvents()
    _emit({"phase": "write", "applied": applied,
           "config": {k: w.settings.get(k) for k in EXPECT}})
    w.close()
    return 0


def _phase_read(profile: str) -> int:
    app, _schema, MainWindow, classic, soft = _bootstrap(profile)
    w = MainWindow()
    app.processEvents()
    st = w.spectrum._state
    colors = ("soft" if list(st.wf_colors) == list(soft)
              else ("sdrpp" if list(st.wf_colors) == list(classic) else "unknown"))
    actual = {
        "fft_size": w.spectrum.generator.fft_size,
        "waterfall_palette": colors,
        "peak_threshold_db": st.peak_rel_db,
        "raw_wf_colors_len": len(st.wf_colors),
    }
    _emit({"phase": "read", "actual": actual,
           "config": {k: w.settings.get(k) for k in EXPECT}})
    w.close()
    return 0


def _run_child(phase: str, profile: str) -> dict:
    env = dict(os.environ)
    env["MBDSDR_REPRO_ROOT"] = ROOT
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--phase", phase, profile],
        env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=240)
    out = proc.stdout or ""
    for line in out.splitlines():
        if line.startswith("RESULT_JSON "):
            try:
                return json.loads(line[len("RESULT_JSON "):])
            except ValueError:
                pass
    raise RuntimeError(
        f"{phase} 阶段无结果（rc={proc.returncode}）："
        f"{(proc.stderr or out).splitlines()[-1][:300] if (proc.stderr or out) else '空'}")


def main() -> int:
    if "--phase" in sys.argv:
        idx = sys.argv.index("--phase")
        phase = sys.argv[idx + 1]
        profile = sys.argv[idx + 2]
        return _phase_write(profile) if phase == "write" else _phase_read(profile)

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    profile = tempfile.mkdtemp(prefix="p0-05-profile-", dir=TMP_ROOT)
    print(f"临时档案: {profile}")

    try:
        written = _run_child("write", profile)
        readback = _run_child("read", profile)
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] {exc}")
        return 2

    print("\n" + "=" * 78)
    print("实测：写入 → 新进程重启读取")
    print("=" * 78)
    print(f"  写入后配置 : {written.get('config')}")
    print(f"  重启后配置 : {readback.get('config')}")
    print(f"  真实消费点 : {readback.get('actual')}")

    actual = readback.get("actual") or {}
    diffs = []
    for key, want in EXPECT.items():
        got = actual.get(key)
        if isinstance(want, float):
            ok = abs(float(got or 0.0) - want) < 1e-6
        else:
            ok = got == want
        print(f"  {key:<20} 期望={want!r:<8} 实际={got!r:<8} {'OK' if ok else 'DIFF'}")
        if not ok:
            diffs.append((key, want, got))

    reproduced = bool(diffs)
    print(f"\n结论：当前实现复现 P0-05（重启未还原）: {reproduced}")
    if reproduced:
        print(f"[复现] {len(diffs)} 个键没有回到真实消费点：{diffs}")
        return 0
    print("[未复现] 三个代表性频谱键都在重启后抵达真实消费点。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

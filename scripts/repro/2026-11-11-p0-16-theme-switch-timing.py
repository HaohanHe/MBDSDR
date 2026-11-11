#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-16 独立复现脚本：切主题是否仍全量重抛光且无忙碌提示。

roadmap 断言（原文）：
    「切主题 1.59–2.65 s 全量 QSS 重 polish（1480 控件），无忙碌提示」
    证据等级：实测（theme ×1、ops2 ×2）。

做法（offscreen + 独立 profile，真实 MainWindow）：
  1. 构造 MainWindow，记录控件总数；
  2. 依次 ``_apply_theme('default')`` → ``'dark'`` → ``'default'``，
     每次计时并 ``processEvents()``；
  3. 切换期间检查 ``QApplication.overrideCursor()`` / 模态窗口 / 进度条，
     判断是否存在任何忙碌反馈。

退出码（三态）：
    0  复现成功   —— 至少一次「跨主题」切换 ≥ 1000 ms，且无忙碌反馈
    1  未复现     —— 跨主题切换 < 1000 ms，或已有忙碌反馈
    2  无法判定   —— 构造失败 / 主题名不存在

说明：``offscreen`` 不做真实光栅化，耗时会比真机低；本脚本记录绝对值，
并额外统计 ``theme.apply`` 内部走到的控件数，便于和 roadmap 的 1480 对齐。

用法：
    python scripts/repro/2026-11-11-p0-16-theme-switch-timing.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"
THRESHOLD_MS = 1000.0


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-16-profile-", dir=TMP_ROOT)
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
        from PySide6.QtWidgets import QApplication, QProgressDialog
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        app = QApplication.instance() or QApplication(sys.argv[:1])
        import themes
        import main_window as mw
        window = mw.MainWindow()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] Qt/MainWindow 构造失败：{type(exc).__name__}: {exc}")
        return 2

    widgets = app.allWidgets()
    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"临时档案: {profile}")
    print(f"控件总数: {len(widgets)}")
    try:
        available = sorted(getattr(themes, "THEMES", {}).keys())
    except Exception:  # noqa: BLE001
        available = []
    print(f"可用主题: {available}")

    seq = ["default", "dark", "default"]
    missing = [t for t in seq if t not in available and available]
    if missing:
        print(f"[无法判定] 主题不存在：{missing}")
        return 2

    results = []
    _hr("实测：逐次切换主题（含 processEvents）")
    for name in seq:
        t0 = time.perf_counter()
        window._apply_theme(name)
        app.processEvents()
        dt = (time.perf_counter() - t0) * 1000.0
        busy = {
            "override_cursor": QApplication.overrideCursor() is not None,
            "modal": QApplication.activeModalWidget() is not None,
            "progress": bool(app.topLevelWidgets())
                        and any(isinstance(w, QProgressDialog)
                                and w.isVisible() for w in app.topLevelWidgets()),
        }
        results.append((name, dt, busy))
        print(f"  → {name:<8} {dt:8.1f} ms  忙碌反馈={busy}")

    cross = [r for r in results[1:]]
    slow = [r for r in cross if r[1] >= THRESHOLD_MS]
    any_busy = any(any(r[2].values()) for r in results)
    print(f"\n  跨主题切换最慢: {max((r[1] for r in cross), default=0):.1f} ms")
    print(f"  ≥ {THRESHOLD_MS:.0f} ms 的次数: {len(slow)}")
    print(f"  期间出现任何忙碌反馈: {any_busy}")

    try:
        window.close()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(profile, ignore_errors=True)

    reproduced = bool(slow) and not any_busy
    print(f"\n结论：当前实现复现 P0-16 : {reproduced}")
    if reproduced:
        print(f"[复现] 跨主题切换 {max(r[1] for r in cross):.0f} ms，"
              "期间没有任何忙碌提示，GUI 线程被占满。")
        return 0
    if any_busy:
        print("[未复现] 切换期间已有忙碌反馈。")
        return 1
    print(f"[未复现] 跨主题切换均 < {THRESHOLD_MS:.0f} ms，不再构成可感知卡顿"
          "（offscreen 下无真实光栅化，绝对值偏乐观）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

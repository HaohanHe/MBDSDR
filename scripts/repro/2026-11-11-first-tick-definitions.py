# SPDX-License-Identifier: MIT
"""W89「首拍」定义 A/B/C 并排测量（同快照、同入口、同隔离 profile）。

只改两个变量：**起点**（构造完成 / 进程入口）与**终点**（一次
``app.processEvents()`` 返回 / 首个 paint 事件派发完毕）。

  d1  构造完成 → ``show()``（``WA_DontShowOnScreen``，不上屏）+ **一次**
      ``app.processEvents()`` 返回。
      与 ``scripts/repro/2026-11-11-p0-15b-first-tick-cost.py --mode baseline
      --screen --show`` 逐字同口径；**不含构造期、不含真实上屏**。
  d2  进程入口（本文件第一行）→ 真屏 ``show()`` + **一次**
      ``app.processEvents()`` 返回。含 import + 构造期 + 布置/抛光。
  d3  进程入口 → 真屏 ``show()`` 后**首个 paint 事件派发完毕**。含首帧绘制，
      不含合成器真正呈现（那要 ``frameSwapped``）。

三者共用真屏指纹（windows / QT_SCALE_FACTOR=1 / MiSans 10pt），否则尺子也变了。

用法::

    python scripts/repro/2026-11-11-first-tick-definitions.py --def d1
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

T0 = time.perf_counter()          # 进程入口（不含解释器自身启动 ~30–60 ms）

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"
SCREEN_ENV = {
    "QT_QPA_PLATFORM": "windows",
    "QT_SCALE_FACTOR": "1",
    "QT_ENABLE_HIGHDPI_SCALING": "0",
}


def _prepare_env() -> str:
    os.makedirs(TMP_ROOT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="ftdef-", dir=TMP_ROOT)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ.update(SCREEN_ENV)
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    return profile


def _load_marker(psutil) -> dict:
    """规则 10 口径：系统 CPU + python 进程数/总占比（采样 1 s）。"""
    procs = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if "python" in (p.info["name"] or "").lower():
                procs.append(p)
        except Exception:
            continue
    before = {}
    for p in procs:
        try:
            before[p.pid] = p.cpu_times()
        except Exception:
            continue
    sys_pct = psutil.cpu_percent(interval=1.0)
    rows = []
    for p in procs:
        try:
            if p.pid not in before:
                continue
            a, b = p.cpu_times(), before[p.pid]
            delta = (a.user - b.user) + (a.system - b.system)
            rows.append({"pid": p.pid, "cpu_pct": round(delta * 100.0, 1)})
        except Exception:
            continue
    rows.sort(key=lambda r: -r["cpu_pct"])
    return {"sys_cpu_pct": round(sys_pct, 1),
            "python_proc_count": len(rows),
            "python_total_cpu_pct": round(sum(r["cpu_pct"] for r in rows), 1),
            "python_top": rows[:5]}


def _main() -> int:
    which = sys.argv[sys.argv.index("--def") + 1] if "--def" in sys.argv else "d1"
    if which not in ("d1", "d2", "d3"):
        print(f"[无法判定] 未知定义 {which}")
        return 2
    profile = _prepare_env()
    try:
        import psutil
        from PySide6.QtCore import QSettings, Qt, QObject, QEvent
        from PySide6.QtGui import QFontDatabase, QFontMetrics
        from PySide6.QtWidgets import QApplication

        load_before = _load_marker(psutil)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        import layout_store as ls
        ls.reset_layout_store_singleton()
        ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
        app = QApplication.instance() or QApplication(sys.argv)
        fams = QFontDatabase.families()
        from themes import get_app_font
        app.setFont(get_app_font())          # desktop/main.py:709 同一条生产入口
        if not fams:
            print("[无法判定] QFontDatabase.families() 为 0：字体退化成方框")
            return 2
        scr = app.primaryScreen()
        ruler = {
            "qt_platform": app.platformName(),
            "font_families": len(fams),
            "dpr": round(scr.devicePixelRatio(), 3) if scr else 0.0,
            "logical_dpi": round(scr.logicalDotsPerInch(), 1) if scr else 0.0,
            "font": app.font().family(),
            "pt": app.font().pointSizeF(),
            "line_h": QFontMetrics(app.font()).height(),
        }
        import ai_panel as ap
        import main_window as mw
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] {type(exc).__name__}: {exc}")
        shutil.rmtree(profile, ignore_errors=True)
        return 2

    kernel, agent = [], []
    orig_kernel = mw.MainWindow._ensure_agent_kernel
    orig_agent = ap.AIPanel.init_agent

    def _k(self):
        t = time.perf_counter()
        try:
            return orig_kernel(self)
        finally:
            kernel.append((time.perf_counter() - t) * 1000.0)

    def _a(self, *a, **k):
        t = time.perf_counter()
        try:
            return orig_agent(self, *a, **k)
        finally:
            agent.append((time.perf_counter() - t) * 1000.0)

    mw.MainWindow._ensure_agent_kernel = _k
    ap.AIPanel.init_agent = _a

    t_ctor = time.perf_counter()
    win = mw.MainWindow()
    ctor_ms = (time.perf_counter() - t_ctor) * 1000.0
    ctor_at = time.perf_counter()

    paints = []

    class _Paint(QObject):
        def eventFilter(self, obj, ev):      # noqa: N802（Qt 命名）
            if ev.type() == QEvent.Paint and not paints:
                paints.append(time.perf_counter())
            return False

    watcher = _Paint()
    if which == "d1":
        win.setAttribute(Qt.WA_DontShowOnScreen, True)
    else:
        win.installEventFilter(watcher)
    win.show()

    if which == "d3":
        deadline = time.perf_counter() + 15.0
        while not paints and time.perf_counter() < deadline:
            app.processEvents()
        end = paints[0] if paints else time.perf_counter()
        tick_ms = (end - ctor_at) * 1000.0
    else:
        app.processEvents()
        tick_ms = (time.perf_counter() - ctor_at) * 1000.0

    total_ms = (time.perf_counter() - T0) * 1000.0
    load_after = _load_marker(psutil)
    result = {
        "def": which,
        "ruler": ruler,
        "ctor_ms": round(ctor_ms, 1),
        "tick_ms": round(tick_ms, 1),
        "entry_to_tick_ms": round(ctor_ms + tick_ms, 1),
        "entry_to_end_ms": round(total_ms, 1),
        "paints": len(paints),
        "kernel_calls": len(kernel),
        "init_agent_calls": len(agent),
        "load_before": load_before,
        "load_after": load_after,
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    try:
        win.close()
        win.deleteLater()
        app.processEvents()
    except Exception:  # noqa: BLE001 - 收尾失败不影响读数
        pass
    shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main())

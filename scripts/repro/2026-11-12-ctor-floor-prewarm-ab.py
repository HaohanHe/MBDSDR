#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""ctor +525 归因：先付掉排版地板，再建主窗，看 ctor 是否回到 ~1048。

三个臂，同进程内只改「地板由谁付 / 主题是否预先应用」：

  A cold     不预暖、不预应用主题 —— 基线脚本口径（地板落在 ctor 里）
  B prewarm  先用一个中文 QLabel 真上屏 paint 一次（把地板付掉），不预应用主题
  C prod     预暖 + ctor 前 theme.apply（生产 main.py:709-713 口径）

每个臂独立进程、隔离 profile；主窗用 HEAD 快照（工作树归卫星线）。
同时记录：ctor、`_create_sky_widget`、每个 QLineEdit 构造、每次 Theme.apply。

用法::

    python scripts/repro/2026-11-12-ctor-floor-prewarm-ab.py --runs 3
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

T0 = time.perf_counter()

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVID = os.path.join(ROOT, "docs", "perf-evidence", "2026-11-12")
LOW_LOAD_PCT = 200.0
ARMS = ("cold", "prewarm", "prod")


def _load(psutil, sec: float = 2.0) -> dict:
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
    sys_pct = psutil.cpu_percent(interval=sec)
    rows = []
    for p in procs:
        try:
            if p.pid not in before:
                continue
            a, b = p.cpu_times(), before[p.pid]
            delta = (a.user - b.user) + (a.system - b.system)
            rows.append({"pid": p.pid, "cpu_pct": round(delta / sec * 100, 1)})
        except Exception:
            continue
    rows.sort(key=lambda r: -r["cpu_pct"])
    return {"sys_cpu_pct": round(sys_pct, 1),
            "python_proc_count": len(rows),
            "python_total_cpu_pct": round(sum(r["cpu_pct"] for r in rows), 1),
            "python_top": rows[:5], "cpu_count": psutil.cpu_count()}


def _wait_low(psutil, timeout_s: float = 240.0) -> dict | None:
    t_end = time.time() + timeout_s
    while True:
        s = _load(psutil, 2.0)
        print(f"[wait] python_total={s['python_total_cpu_pct']}% "
              f"procs={s['python_proc_count']}", flush=True)
        if s["python_total_cpu_pct"] < LOW_LOAD_PCT:
            return s
        if time.time() >= t_end:
            return None
        time.sleep(8)


def _prepare_env() -> str:
    profile = tempfile.mkdtemp(prefix="ctorab-")
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ.update({"QT_QPA_PLATFORM": "windows", "QT_SCALE_FACTOR": "1",
                       "QT_ENABLE_HIGHDPI_SCALING": "0"})
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    return profile


def _head_snapshot(rev: str = "HEAD") -> tuple[str, str]:
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", rev],
                          capture_output=True, text=True).stdout.strip()
    snap = os.path.join(tempfile.gettempdir(), "kilo", f"ctorab_head_{head}")
    os.makedirs(snap, exist_ok=True)
    for name in ("main_window.py", "module_panel.py", "device_panel.py",
                 "bookmark_panel.py"):
        out = subprocess.run(["git", "-C", ROOT, "show", f"{head}:desktop/{name}"],
                             capture_output=True).stdout
        with open(os.path.join(snap, name), "wb") as f:
            f.write(out)
    return snap, head


def _one(arm: str) -> int:
    snap = os.environ.get("MBDSDR_HEAD_SNAP")
    if snap:
        sys.path.insert(0, snap)
    _prepare_env()

    import psutil
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

    import themes
    import layout_store as ls

    profile = os.environ["HOME"]
    QSettings_cls = __import__("PySide6.QtCore", fromlist=["QSettings"]).QSettings
    QSettings_cls.setDefaultFormat(QSettings_cls.IniFormat)
    for scope in (QSettings_cls.UserScope, QSettings_cls.SystemScope):
        QSettings_cls.setPath(QSettings_cls.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
    ls.reset_layout_store_singleton()
    ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001

    if snap:
        sys.path.insert(0, snap)
    import main_window as mw

    app = QApplication.instance() or QApplication([])
    app.setFont(themes.get_app_font())

    theme_calls = []
    orig_apply = themes.Theme.apply

    def apply(self, app_):
        t = time.perf_counter()
        try:
            return orig_apply(self, app_)
        finally:
            theme_calls.append(round((time.perf_counter() - t) * 1000.0, 1))

    themes.Theme.apply = apply

    preapply_ms = None
    if arm in ("prod", "preapply"):
        t = time.perf_counter()
        themes.get_theme(themes.DEFAULT_THEME).apply(app)
        preapply_ms = round((time.perf_counter() - t) * 1000.0, 1)

    class _ProbeLabel(QLabel):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.paint_ms = None

        def paintEvent(self, ev):  # noqa: N802
            if self.paint_ms is not None:
                return super().paintEvent(ev)
            t = time.perf_counter()
            super().paintEvent(ev)
            self.paint_ms = round((time.perf_counter() - t) * 1000.0, 1)

    prewarm_ms = None
    prewarm_paint_ms = None
    if arm in ("prewarm", "prod"):
        t = time.perf_counter()
        host = QWidget()
        host.resize(420, 160)
        label = _ProbeLabel(host)
        label.setFont(app.font())
        label.setText("星象仪模式 频谱 控制 AI 助手")
        label.setGeometry(16, 16, 380, 90)
        host.show()
        deadline = time.perf_counter() + 20.0
        while label.paint_ms is None and time.perf_counter() < deadline:
            app.processEvents()
        prewarm_ms = round((time.perf_counter() - t) * 1000.0, 1)
        prewarm_paint_ms = label.paint_ms
        host.close()
        host.deleteLater()
        app.processEvents()

    prelude_ms = None
    if os.environ.get("MBDSDR_PRELUDE") == "families":
        from PySide6.QtGui import QFontDatabase, QFontMetrics
        t = time.perf_counter()
        QFontDatabase.families()
        QFontMetrics(app.font()).height()
        prelude_ms = round((time.perf_counter() - t) * 1000.0, 1)

    edits = []
    orig_le = QLineEdit.__init__

    def le_init(self, *a, **k):
        t = time.perf_counter()
        try:
            return orig_le(self, *a, **k)
        finally:
            edits.append(round((time.perf_counter() - t) * 1000.0, 2))

    QLineEdit.__init__ = le_init

    sky_ms = []
    orig_sky = mw.MainWindow._create_sky_widget

    def sky(self):
        t = time.perf_counter()
        try:
            return orig_sky(self)
        finally:
            sky_ms.append(round((time.perf_counter() - t) * 1000.0, 1))

    mw.MainWindow._create_sky_widget = sky

    QLineEdit.__init__ = le_init  # 再确认（main_window 导入不会重置）
    t_ctor = time.perf_counter()
    win = mw.MainWindow()
    ctor_ms = round((time.perf_counter() - t_ctor) * 1000.0, 1)

    app.processEvents()
    load_after = _load(psutil)

    result = {
        "arm": arm,
        "head": os.environ.get("MBDSDR_HEAD", ""),
        "mw_file": getattr(sys.modules.get("main_window"), "__file__", ""),
        "prewarm_ms": prewarm_ms,
        "prewarm_label_paint_ms": prewarm_paint_ms,
        "preapply_ms": preapply_ms,
        "prelude_ms": prelude_ms,
        "ctor_ms": ctor_ms,
        "sky_ms": sky_ms,
        "sky_total_ms": round(sum(sky_ms), 1) if sky_ms else 0.0,
        "theme_calls_ms": theme_calls,
        "lineedit_count": len(edits),
        "lineedit_sum_ms": round(sum(edits), 1),
        "lineedit_max_ms": max(edits) if edits else 0.0,
        "lineedit_top5": sorted(edits, reverse=True)[:5],
        "load_after": load_after,
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    try:
        win.close()
        win.deleteLater()
        app.processEvents()
    except Exception:  # noqa: BLE001
        pass
    return 0


def _driver(runs: int, rev: str = "HEAD", arms=ARMS,
            out_name: str = "ctor-floor-prewarm-ab.json") -> int:
    import psutil

    snap, head = _head_snapshot(rev)
    print(f"[snapshot] HEAD {head} -> {snap}")

    got = {a: [] for a in set(ARMS) | set(arms)}
    for r in range(runs):
        order = list(arms) if r % 2 == 0 else list(reversed(arms))
        for arm in order:
            gate = _wait_low(psutil)
            if gate is None:
                print(f"[{arm} r{r}] 低负载窗口不足，跳过", flush=True)
                continue
            env = dict(os.environ, MBDSDR_HEAD_SNAP=snap, MBDSDR_HEAD=head)
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--one", "--arm", arm],
                capture_output=True, text=True, timeout=600, env=env)
            payload = None
            for line in proc.stdout.splitlines():
                if line.startswith("RESULT "):
                    payload = json.loads(line[len("RESULT "):])
            if payload is None:
                print(f"[{arm} r{r}] 无结果 rc={proc.returncode}\n"
                      f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}", flush=True)
                continue
            payload["load_gate_before_python_pct"] = gate["python_total_cpu_pct"]
            got[arm].append(payload)
            print(f"[{arm} r{r}] ctor={payload['ctor_ms']} "
                  f"sky={payload['sky_total_ms']} le_max={payload['lineedit_max_ms']} "
                  f"le_n={payload['lineedit_count']}", flush=True)

    def col(arm, key):
        return [p[key] for p in got[arm] if p.get(key) is not None]

    def stat(arm, key):
        v = col(arm, key)
        if not v:
            return None
        return {"runs": v, "median": round(statistics.median(v), 1),
                "min": round(min(v), 1), "max": round(max(v), 1)}

    out = {
        "kind": "ctor +525 归因：预暖排版地板后重建主窗（A cold / B prewarm / C prod）",
        "date": "2026-11-12",
        "head": head,
        "snapshot": snap,
        "reference": {
            "baseline_ctor_1048_3": "docs/perf-evidence/2026-11-11/first_tick_def_c_lazy_settings_ab.json（旧 HEAD、无 splash、无预应用主题）",
            "production_ctor_1573_6": "docs/perf-evidence/2026-11-12/splash-t1-t2.json（HEAD 88e458c8，生产入口，splash 已付地板）"
        },
        "arms": {},
        "prelude": os.environ.get("MBDSDR_PRELUDE") or None,
        "load_rule": "每个 run 之前父进程采 2 s 低负载门（<200%），子进程跑完再采一次。",
    }
    for arm in arms:
        out["arms"][arm] = {
            "n": len(got[arm]),
            "ctor_ms": stat(arm, "ctor_ms"),
            "sky_total_ms": stat(arm, "sky_total_ms"),
            "lineedit_count": stat(arm, "lineedit_count"),
            "lineedit_sum_ms": stat(arm, "lineedit_sum_ms"),
            "lineedit_max_ms": stat(arm, "lineedit_max_ms"),
            "prewarm_ms": stat(arm, "prewarm_ms"),
            "prewarm_label_paint_ms": stat(arm, "prewarm_label_paint_ms"),
            "preapply_ms": stat(arm, "preapply_ms"),
            "prelude_ms": stat(arm, "prelude_ms"),
            "theme_calls_last_run": got[arm][-1]["theme_calls_ms"] if got[arm] else None,
            "samples": got[arm],
        }
    os.makedirs(EVID, exist_ok=True)
    path = os.path.join(EVID, out_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[saved] {path}")
    for arm in arms:
        a = out["arms"][arm]
        print(arm, "ctor", a["ctor_ms"], "sky", a["sky_total_ms"],
              "le_max", a["lineedit_max_ms"], "prewarm", a["prewarm_ms"])
    return 0


def main() -> int:
    runs = 3
    arm = "cold"
    rev = "HEAD"
    arms = ARMS
    out_name = "ctor-floor-prewarm-ab.json"
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--runs" and i + 1 < len(argv):
            runs = int(argv[i + 1])
        elif a == "--arm" and i + 1 < len(argv):
            arm = argv[i + 1]
        elif a == "--rev" and i + 1 < len(argv):
            rev = argv[i + 1]
        elif a == "--arms" and i + 1 < len(argv):
            arms = tuple(argv[i + 1].split(","))
        elif a == "--out" and i + 1 < len(argv):
            out_name = argv[i + 1]
    if "--one" in argv:
        return _one(arm)
    return _driver(runs, rev, arms, out_name)


if __name__ == "__main__":
    sys.exit(main())

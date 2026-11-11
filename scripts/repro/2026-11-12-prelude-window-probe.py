#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""钉死：`first-tick-definitions.py` 的 prelude 在计时窗口内还是窗口外。

对拍两臂（同进程结构、同隔离 profile、同 HEAD 快照）：

  prelude  与 `first-tick-definitions.py` 完全同序：T0 → QApplication →
           QFontDatabase.families() → QFontMetrics(app.font()).height() → ctor
  cold     同一结构但不跑 prelude（families/QFontMetrics 都跳过）

每条记录四个时间戳（全部相对 T0，单位 ms）：

  t_qapp             QApplication 建完
  t_prelude_end      prelude 跑完（这一行就是「prelude 之后立刻打点」）
  t_ctor_start       MainWindow() 之前
  t_first_paint      主窗 show() 后首个 Paint 派发完毕

由此可直接读出：
  定义 C 报出的 ctor+tick 窗口 = [t_ctor_start, t_first_paint]；
  prelude 落在 [t_qapp, t_prelude_end]，**在窗口之前**。
  真实入口墙钟 = t_first_paint - T0，含 prelude。

用法::

    python scripts/repro/2026-11-12-prelude-window-probe.py --runs 3
"""
from __future__ import annotations

import time

T0 = time.perf_counter()          # 进程入口（与 first-tick-definitions.py 同位）

import json                       # noqa: E402
import os                         # noqa: E402
import statistics                 # noqa: E402
import subprocess                 # noqa: E402
import sys                        # noqa: E402
import tempfile                   # noqa: E402

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVID = os.path.join(ROOT, "docs", "perf-evidence", "2026-11-12")
LOW_LOAD_PCT = 200.0
ARMS = ("prelude", "cold")


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
    profile = tempfile.mkdtemp(prefix="prelude-")
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


def _head_snapshot() -> tuple[str, str]:
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    snap = os.path.join(tempfile.gettempdir(), "kilo", f"prelude_head_{head}")
    os.makedirs(snap, exist_ok=True)
    for name in ("main_window.py", "module_panel.py", "device_panel.py",
                 "bookmark_panel.py"):
        out = subprocess.run(["git", "-C", ROOT, "show", f"HEAD:desktop/{name}"],
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
    from PySide6.QtCore import QEvent, QObject, QSettings
    from PySide6.QtGui import QFontDatabase, QFontMetrics
    from PySide6.QtWidgets import QApplication

    profile = os.environ["HOME"]
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope,
                          os.path.join(profile, "qsettings"))
    import layout_store as ls
    ls.reset_layout_store_singleton()
    ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001

    app = QApplication.instance() or QApplication(sys.argv)
    t_qapp = time.perf_counter()

    from themes import get_app_font
    app.setFont(get_app_font())          # main.py:709 同一条生产入口

    prelude_ms = 0.0
    if arm == "prelude":
        t = time.perf_counter()
        QFontDatabase.families()         # first-tick-definitions.py:110
        QFontMetrics(app.font()).height()  # :124
        prelude_ms = round((time.perf_counter() - t) * 1000.0, 1)
    t_prelude_end = time.perf_counter()

    if snap:
        sys.path.insert(0, snap)         # main_window 此前未导入，这里钉住快照
    import main_window as mw

    t_ctor_start = time.perf_counter()
    win = mw.MainWindow()
    t_ctor_end = time.perf_counter()
    ctor_ms = round((t_ctor_end - t_ctor_start) * 1000.0, 1)

    paints = []

    class _Paint(QObject):
        def eventFilter(self, obj, ev):      # noqa: N802（Qt 命名）
            if ev.type() == QEvent.Paint and not paints:
                paints.append(time.perf_counter())
            return False

    watcher = _Paint()                   # 必须留引用，否则被 GC 后过滤器失效
    win.installEventFilter(watcher)
    win.show()
    deadline = time.perf_counter() + 15.0
    while not paints and time.perf_counter() < deadline:
        app.processEvents()
    t_first_paint = paints[0] if paints else time.perf_counter()

    load_after = _load(psutil)
    result = {
        "arm": arm,
        "head": os.environ.get("MBDSDR_HEAD", ""),
        "mw_file": getattr(sys.modules.get("main_window"), "__file__", ""),
        "t0_abs": T0,
        "to_qapp_ms": round((t_qapp - T0) * 1000.0, 1),
        "to_prelude_end_ms": round((t_prelude_end - T0) * 1000.0, 1),
        "prelude_ms": prelude_ms,
        "to_ctor_start_ms": round((t_ctor_start - T0) * 1000.0, 1),
        "ctor_ms": ctor_ms,
        "tick_ms": round((t_first_paint - t_ctor_end) * 1000.0, 1),
        "def_c_reported_ms": round((t_first_paint - t_ctor_start) * 1000.0, 1),
        "entry_to_first_paint_ms": round((t_first_paint - T0) * 1000.0, 1),
        "paints": len(paints),
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


def _driver(runs: int) -> int:
    import psutil

    snap, head = _head_snapshot()
    print(f"[snapshot] HEAD {head} -> {snap}")

    got = {a: [] for a in ARMS}
    for r in range(runs):
        order = list(ARMS) if r % 2 == 0 else list(reversed(ARMS))
        for arm in order:
            gate = _wait_low(psutil)
            if gate is None:
                print(f"[{arm} r{r}] 低负载窗口不足，跳过", flush=True)
                continue
            from _qsettings_startup import child_cmd, child_env
            env = child_env(dict(os.environ, MBDSDR_HEAD_SNAP=snap,
                                 MBDSDR_HEAD=head))
            proc = subprocess.run(
                child_cmd(os.path.abspath(__file__), ["--one", "--arm", arm]),
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
            print(f"[{arm} r{r}] prelude={payload['prelude_ms']} "
                  f"ctor={payload['ctor_ms']} defC={payload['def_c_reported_ms']} "
                  f"entry_paint={payload['entry_to_first_paint_ms']}", flush=True)

    def stat(arm, key):
        v = [p[key] for p in got[arm] if p.get(key) is not None]
        if not v:
            return None
        return {"runs": v, "median": round(statistics.median(v), 1),
                "min": round(min(v), 1), "max": round(max(v), 1)}

    out = {
        "kind": "prelude 在定义 C 计时窗口内/外的直接测量",
        "date": "2026-11-12",
        "head": head,
        "snapshot": snap,
        "arms": {
            arm: {
                "n": len(got[arm]),
                "prelude_ms": stat(arm, "prelude_ms"),
                "to_prelude_end_ms": stat(arm, "to_prelude_end_ms"),
                "to_ctor_start_ms": stat(arm, "to_ctor_start_ms"),
                "ctor_ms": stat(arm, "ctor_ms"),
                "tick_ms": stat(arm, "tick_ms"),
                "def_c_reported_ms": stat(arm, "def_c_reported_ms"),
                "entry_to_first_paint_ms": stat(arm, "entry_to_first_paint_ms"),
                "samples": got[arm],
            } for arm in ARMS
        },
        "load_rule": "规则 10：每个子 run 前父进程采 2 s 低负载门（<200%），子进程跑完再采一次。",
    }
    os.makedirs(EVID, exist_ok=True)
    path = os.path.join(EVID, "prelude-window.json")
    p_arm, c_arm = out["arms"]["prelude"], out["arms"]["cold"]
    if p_arm["prelude_ms"] and c_arm["def_c_reported_ms"]:
        pre = p_arm["prelude_ms"]["median"]
        d_pre = p_arm["def_c_reported_ms"]["median"]
        d_cold = c_arm["def_c_reported_ms"]["median"]
        out["verdict"] = {
            "window_definition": "被报出的定义 C = ctor_ms + tick_ms，窗口 = [t_ctor_start, t_first_paint]",
            "prelude_position": "prelude 在 [t_qapp, t_prelude_end]，早于 t_ctor_start，即在窗口之外",
            "includes_floor": False,
            "prelude_ms_median": pre,
            "def_c_with_prelude_ms_median": d_pre,
            "def_c_cold_ms_median": d_cold,
            "understated_by_ms": round(d_cold - d_pre, 1),
            "conclusion": (
                "不含：1158.5 这类 ctor+tick 读数把首次排版地板留在了窗口外。"
                f"同一结构冷跑的中位比带 prelude 高 {round(d_cold - d_pre, 1)} ms"
                f"（{d_cold} vs {d_pre}），prelude 自身 {pre} ms。"
                "按该尺子，带地板的真实值 = 报出值 + 这个差值；跨尺子比较前必须补回。"),
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[saved] {path}")
    for arm in ARMS:
        a = out["arms"][arm]
        print(arm, "prelude", a["prelude_ms"], "defC", a["def_c_reported_ms"],
              "entry_paint", a["entry_to_first_paint_ms"])
    return 0


def main() -> int:
    runs = 3
    arm = "prelude"
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--runs" and i + 1 < len(argv):
            runs = int(argv[i + 1])
        elif a == "--arm" and i + 1 < len(argv):
            arm = argv[i + 1]
    if "--one" in argv:
        return _one(arm)
    return _driver(runs)


if __name__ == "__main__":
    sys.exit(main())

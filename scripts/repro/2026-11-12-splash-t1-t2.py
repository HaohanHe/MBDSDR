#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""T1/T2：进程入口 → splash 首帧 → 主窗首帧（都打 paint 完成点，不用定时器近似）。

一次子进程（``--one``）：

  T0            本文件第一行（父进程用 ``perf_counter`` 记 spawn 时刻，两者相减
                得到「进程创建 + 解释器启动」）；
  import main   把 desktop/main.py 作为模块导入（模块级 import 全跑一遍）；
  main.main()   照生产路径走：QApplication → _show_startup_splash → 自检 →
                主题 → MainWindow → show → exec；
  splash        用子类接住 ``QSplashScreen.show/paintEvent``，记录首帧 paint 完成；
  主窗          用 ``QApplication.notify`` 接住主窗自己的首个 Paint，调完
                ``super().notify`` 才算完成；同时记录首个 QLabel 的 Paint 时长。

同时记录 splash 的 3 次 ``QPainter.drawText``（那 384 的地板应该在 T1 里、在
splash 自己的 pixmap 预渲染里付掉），以及主窗侧各段（自检、主题、导入、构造）。

规则 10：父进程每个 run 之前采一次低负载门（<200%），子进程跑完再采一次。
不修改任何产品代码；main_window.py 归卫星线。
"""
from __future__ import annotations

import time

T0 = time.perf_counter()          # 本进程第一行

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
    profile = tempfile.mkdtemp(prefix="t1t2-")
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
    """把 HEAD 版 desktop 关键模块导出到临时目录，供子进程优先导入。

    工作树里 main_window / module_panel / device_panel / bookmark_panel 正被
    卫星线改（惰性星象仪），T2 里的主窗构造必须钉在可引用的 HEAD 上。
    """
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    snap = os.path.join(tempfile.gettempdir(), "kilo", f"t1t2_head_{head}")
    os.makedirs(snap, exist_ok=True)
    for name in ("main_window.py", "module_panel.py", "device_panel.py",
                 "bookmark_panel.py"):
        out = subprocess.run(["git", "-C", ROOT, "show", f"HEAD:desktop/{name}"],
                             capture_output=True).stdout
        with open(os.path.join(snap, name), "wb") as f:
            f.write(out)
    return snap, head


def _one() -> int:
    snap = os.environ.get("MBDSDR_HEAD_SNAP")
    if snap:
        sys.path.insert(0, snap)
    _prepare_env()

    import main as appmain          # ← 生产模块级 import 全在这里

    t_import_done = time.perf_counter()
    if snap:
        sys.path.insert(0, snap)    # main.py 会把 desktop 插到 0，这里重新顶到最前
    from PySide6.QtCore import QEvent, QTimer
    from PySide6.QtGui import QFontInfo, QFontMetrics
    from PySide6.QtWidgets import QLabel, QApplication

    M: dict = {"head": os.environ.get("MBDSDR_HEAD", "")}

    class _Splash(appmain.QSplashScreen):
        def show(self):                     # noqa: N802（Qt 命名）
            M["t_splash_show"] = time.perf_counter()
            super().show()

        def paintEvent(self, ev):           # noqa: N802
            first = M.get("t_splash_paint_done") is None
            super().paintEvent(ev)
            if first:
                M["t_splash_paint_done"] = time.perf_counter()

    appmain.QSplashScreen = _Splash

    class _Painter(appmain.QPainter):
        def drawText(self, *a, **k):        # noqa: N802
            t = time.perf_counter()
            out = super().drawText(*a, **k)
            dt = (time.perf_counter() - t) * 1000.0
            M["splash_drawtext_ms"] = round(
                M.get("splash_drawtext_ms", 0.0) + dt, 1)
            M.setdefault("splash_drawtext_each_ms", []).append(round(dt, 1))
            M["splash_drawtext_calls"] = M.get("splash_drawtext_calls", 0) + 1
            return out

    appmain.QPainter = _Painter

    def _timed(name):
        orig = getattr(appmain, name)

        def wrapper(*a, **k):
            t = time.perf_counter()
            try:
                return orig(*a, **k)
            finally:
                M[name + "_ms"] = round((time.perf_counter() - t) * 1000.0, 1)

        setattr(appmain, name, wrapper)

    _timed("_show_startup_splash")
    _timed("_run_import_smoke")
    _timed("_startup_dependency_problems")

    orig_splash = appmain._show_startup_splash

    def splash_wrap(app):
        M["t_splash_enter"] = time.perf_counter()
        try:
            return orig_splash(app)
        finally:
            M["t_splash_fn_exit"] = time.perf_counter()

    appmain._show_startup_splash = splash_wrap

    import themes
    orig_apply = themes.Theme.apply

    def apply(self, app_):
        t = time.perf_counter()
        try:
            return orig_apply(self, app_)
        finally:
            M["theme_apply_ms"] = round((time.perf_counter() - t) * 1000.0, 1)

    themes.Theme.apply = apply

    if hasattr(themes, "fit_scale_to_screen"):
        orig_fit = themes.fit_scale_to_screen

        def fit(*a, **k):
            t = time.perf_counter()
            try:
                return orig_fit(*a, **k)
            finally:
                M["fit_scale_ms"] = round((time.perf_counter() - t) * 1000.0, 1)

        themes.fit_scale_to_screen = fit

    orig_import = appmain._import_main_window

    def import_wrap(splash=None):
        t = time.perf_counter()
        cls = orig_import(splash)
        M["mw_import_ms"] = round((time.perf_counter() - t) * 1000.0, 1)
        M["mw_file"] = getattr(sys.modules.get("main_window"), "__file__", "")

        class _MW(cls):
            def __init__(self, *a, **k):
                t2 = time.perf_counter()
                super().__init__(*a, **k)
                M["mw_ctor_ms"] = round((time.perf_counter() - t2) * 1000.0, 1)
                M["t_mw_created"] = time.perf_counter()
                M["mw"] = self

            def show(self):                 # noqa: N802
                M["t_mw_show"] = time.perf_counter()
                super().show()

        return _MW

    appmain._import_main_window = import_wrap

    class _App(appmain.QApplication):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            M["t_app_created"] = time.perf_counter()
            QTimer.singleShot(30000, self._bail)   # 兜底：不挂死

        def _bail(self):
            M["timeout"] = True
            self.quit()

        def notify(self, receiver, event):
            if event.type() == QEvent.Paint:
                mw = M.get("mw")
                if (mw is not None and receiver is mw
                        and M.get("t_mw_paint_done") is None):
                    t = time.perf_counter()
                    try:
                        return super().notify(receiver, event)
                    finally:
                        M["t_mw_paint_done"] = time.perf_counter()
                        M["mw_paint_ms"] = round(
                            (M["t_mw_paint_done"] - t) * 1000.0, 1)
                        QTimer.singleShot(0, QApplication.instance().quit)
                if (M.get("t_label_paint_done") is None
                        and isinstance(receiver, QLabel)):
                    t = time.perf_counter()
                    try:
                        return super().notify(receiver, event)
                    finally:
                        M["t_label_paint_done"] = time.perf_counter()
                        M["label_paint_ms"] = round(
                            (M["t_label_paint_done"] - t) * 1000.0, 1)
            return super().notify(receiver, event)

    appmain.QApplication = _App
    sys.argv = ["mbdsdr", "--theme", "default"]

    err = None
    try:
        appmain.main()
    except SystemExit:
        pass
    except BaseException as exc:        # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"

    import psutil
    load_after = _load(psutil)

    app = QApplication.instance()
    metrics_ms = None
    if app is not None:
        f = app.font()
        t = time.perf_counter()
        QFontMetrics(f).horizontalAdvance("星象仪模式")
        metrics_ms = round((time.perf_counter() - t) * 1000.0, 3)

    result = {
        "head": M.get("head"),
        "mw_file": M.get("mw_file"),
        "t0_abs": T0,
        "t_import_done_abs": t_import_done,
        "t_app_created_abs": M.get("t_app_created"),
        "t_splash_enter_abs": M.get("t_splash_enter"),
        "t_splash_show_abs": M.get("t_splash_show"),
        "t_splash_paint_done_abs": M.get("t_splash_paint_done"),
        "t_mw_created_abs": M.get("t_mw_created"),
        "t_mw_show_abs": M.get("t_mw_show"),
        "t_mw_paint_done_abs": M.get("t_mw_paint_done"),
        "t_label_paint_done_abs": M.get("t_label_paint_done"),
        "splash_drawtext_ms": M.get("splash_drawtext_ms", 0.0),
        "splash_drawtext_each_ms": M.get("splash_drawtext_each_ms", []),
        "splash_drawtext_calls": M.get("splash_drawtext_calls", 0),
        "splash_fn_ms": M.get("_show_startup_splash_ms"),
        "import_smoke_ms": M.get("_run_import_smoke_ms"),
        "dependency_probe_ms": M.get("_startup_dependency_problems_ms"),
        "theme_apply_ms": M.get("theme_apply_ms"),
        "fit_scale_ms": M.get("fit_scale_ms"),
        "mw_import_ms": M.get("mw_import_ms"),
        "mw_ctor_ms": M.get("mw_ctor_ms"),
        "mw_paint_ms": M.get("mw_paint_ms"),
        "label_paint_ms": M.get("label_paint_ms"),
        "post_run_metrics_ms": metrics_ms,
        "resolved_font": (QFontInfo(app.font()).family() if app is not None else None),
        "timeout": M.get("timeout", False),
        "error": err,
        "load_after": load_after,
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    return 0 if err is None and not M.get("timeout") else 1


def _driver(runs: int) -> int:
    import psutil

    snap, head = _head_snapshot()
    print(f"[snapshot] HEAD {head} -> {snap}")

    rows = []
    for i in range(runs):
        gate = _wait_low(psutil)
        if gate is None:
            print(f"[run{i}] 低负载窗口不足，跳过", flush=True)
            continue
        env = dict(os.environ, MBDSDR_HEAD_SNAP=snap, MBDSDR_HEAD=head)
        t_spawn = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--one"],
            capture_output=True, text=True, timeout=600, env=env)
        payload = None
        for line in proc.stdout.splitlines():
            if line.startswith("RESULT "):
                payload = json.loads(line[len("RESULT "):])
        if payload is None:
            print(f"[run{i}] 无结果 rc={proc.returncode}\n"
                  f"{proc.stdout[-1200:]}\n{proc.stderr[-1200:]}", flush=True)
            continue
        payload["interpreter_ms"] = round((payload["t0_abs"] - t_spawn) * 1000.0, 1)
        payload["load_gate_before_python_pct"] = gate["python_total_cpu_pct"]
        payload["load_gate_before"] = gate
        payload = _derive(payload)
        rows.append(payload)
        print(f"[run{i}] t1={_t1(payload)} t2={_t2(payload)} "
              f"interp={payload['interpreter_ms']} err={payload['error']}",
              flush=True)

    def col(key):
        return [r[key] for r in rows if r.get(key) is not None]

    def stat(key):
        vals = col(key)
        if not vals:
            return None
        return {"runs": vals, "median": round(statistics.median(vals), 1),
                "min": round(min(vals), 1), "max": round(max(vals), 1)}

    out = {
        "kind": "T1/T2：进程入口 → splash 首帧 → 主窗首帧（paint 完成点）",
        "date": "2026-11-12",
        "definition": {
            "T1": "进程入口（本脚本第一行，含模块级 import）→ splash 首帧 paintEvent 返回",
            "T2": "splash 首帧 paintEvent 返回 → 主窗顶层 Paint 事件在 notify 中派发完毕",
            "entry_to_main_frame": "T1 + T2（不含父进程 spawn 与解释器启动）",
        },
        "runs": runs,
        "head": head,
        "snapshot": snap,
        "mw_file_samples": [r.get("mw_file") for r in rows],
        "load_rule": "每个 run 之前父进程采 2 s 低负载门（python 总占比 <200%）；"
                     "子进程跑完再采一次，逐 run 记录在 load_gate_before / load_after。",
        "samples": rows,
        "T1_ms": stat("_t1"),
        "T2_ms": stat("_t2"),
        "entry_to_main_frame_ms": stat("_entry"),
        "interpreter_startup_ms": stat("interpreter_ms"),
        "t1_split_ms": {
            "import_main": stat("_import"),
            "app_create_plus_argparse": stat("_appsetup"),
            "splash_fn_total": stat("splash_fn_ms"),
            "splash_drawtext_sum": stat("splash_drawtext_ms"),
            "splash_show_to_paint": stat("_splash_show_to_paint"),
        },
        "t2_split_ms": {
            "import_smoke": stat("import_smoke_ms"),
            "dependency_probe": stat("dependency_probe_ms"),
            "theme_apply": stat("theme_apply_ms"),
            "fit_scale": stat("fit_scale_ms"),
            "mw_import": stat("mw_import_ms"),
            "mw_ctor": stat("mw_ctor_ms"),
            "mw_show_to_paint": stat("_mw_show_to_paint"),
            "mw_paint_exec": stat("mw_paint_ms"),
        },
        "label_after_splash": {
            "first_label_paint_ms": stat("label_paint_ms"),
            "post_run_metrics_ms": stat("post_run_metrics_ms"),
            "offset_after_splash_ms": stat("_label_offset"),
        },
        "errors": [r["error"] for r in rows if r.get("error")],
        "timeout": any(r.get("timeout") for r in rows),
    }
    os.makedirs(EVID, exist_ok=True)
    path = os.path.join(EVID, "splash-t1-t2.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[saved] {path}")
    for k in ("T1_ms", "T2_ms", "entry_to_main_frame_ms",
              "interpreter_startup_ms"):
        print(k, out[k])
    print("t1_split", out["t1_split_ms"])
    print("t2_split", out["t2_split_ms"])
    print("label", out["label_after_splash"])
    return 0


def _t1(r):
    a = r.get("t_splash_paint_done_abs")
    return round((a - r["t0_abs"]) * 1000.0, 1) if a else None


def _t2(r):
    a = r.get("t_splash_paint_done_abs")
    b = r.get("t_mw_paint_done_abs")
    return round((b - a) * 1000.0, 1) if a and b else None


def _derive(r):
    r["_t1"] = _t1(r)
    r["_t2"] = _t2(r)
    a = r.get("t_mw_paint_done_abs")
    r["_entry"] = round((a - r["t0_abs"]) * 1000.0, 1) if a else None
    r["_import"] = round((r["t_import_done_abs"] - r["t0_abs"]) * 1000.0, 1)
    c = r.get("t_app_created_abs")
    r["_appsetup"] = (round((c - r["t_import_done_abs"]) * 1000.0, 1)
                      if c else None)
    s = r.get("t_splash_show_abs")
    p = r.get("t_splash_paint_done_abs")
    r["_splash_show_to_paint"] = (round((p - s) * 1000.0, 1)
                                  if s and p else None)
    w = r.get("t_mw_show_abs")
    r["_mw_show_to_paint"] = (round((a - w) * 1000.0, 1) if a and w else None)
    l = r.get("t_label_paint_done_abs")
    r["_label_offset"] = (round((l - p) * 1000.0, 1) if l and p else None)
    return r


def main() -> int:
    runs = 3
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--runs" and i + 1 < len(argv):
            runs = int(argv[i + 1])
    if "--one" in argv:
        return _one()
    return _driver(runs)


if __name__ == "__main__":
    sys.exit(main())

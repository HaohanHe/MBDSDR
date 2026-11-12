#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""延后导入（scipy 私有子树）的三条反证。

对应授权里的三条：
  ① gate_missing：模拟 scipy 系缺件 → find_spec 门槛必须中止并弹窗（不导入主窗）；
  ② defer_broken：模拟「文件在但加载失败」→ 首帧已出，延后导入必须报出且不崩；
  ③ frame_gap  ：生产原样，量首帧后延后导入期间的最坏单帧间隔。

另有两臂只服务「−962 ms 必须在改后代码上复现」这一条：
  * frame_gap_nodefer：延后导入整段不跑，量同一窗口里的掉帧底噪；
  * t2_baseline     ：**HEAD 的 main.py**（老代码：smoke + scipy 真 import 都在
                       splash 之后、主窗之前）跑同一条 T2 量法，作为改前基线。

不修改产品代码：只 patch main 模块里的清单/报告函数与本进程的
importlib.import_module（用来给每片计时）。

用法::

    python scripts/repro/2026-11-12-deferred-import-verify.py --runs 3 \
        --cases gate_missing,defer_broken,frame_gap,frame_gap_nodefer,t2_baseline
    python scripts/repro/2026-11-12-deferred-import-verify.py --one --case frame_gap
"""
from __future__ import annotations

import time

T0 = time.perf_counter()

import importlib                   # noqa: E402
import json                        # noqa: E402
import os                          # noqa: E402
import statistics                  # noqa: E402
import subprocess                  # noqa: E402
import sys                         # noqa: E402
import tempfile                    # noqa: E402

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVID = os.path.join(ROOT, "docs", "perf-evidence", "2026-11-12")
LOW_LOAD_PCT = 200.0
CASES = ("gate_missing", "defer_broken", "frame_gap", "frame_gap_nodefer",
         "t2_baseline")
OUT_NAME = "deferred-import-verify.json"
KEEP_ALIVE_MS = 9000


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
            rows.append({"pid": p.pid,
                         "cpu_pct": round(((a.user - b.user)
                                           + (a.system - b.system)) / sec * 100, 1)})
        except Exception:
            continue
    rows.sort(key=lambda r: -r["cpu_pct"])
    return {"sys_cpu_pct": round(sys_pct, 1), "python_proc_count": len(rows),
            "python_total_cpu_pct": round(sum(r["cpu_pct"] for r in rows), 1),
            "python_top": rows[:5]}


def _wait_low(psutil, timeout_s: float = 240.0):
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
    profile = tempfile.mkdtemp(prefix="defer-")
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ.update({"QT_QPA_PLATFORM": "windows", "QT_SCALE_FACTOR": "1",
                       "QT_ENABLE_HIGHDPI_SCALING": "0",
                       "MBDSDR_OFFLINE": "1", "MBDSDR_NO_HW": "1",
                       "MBDSDR_NULL_AUDIO": "1"})
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    return profile


_HEAD_FILES = ("main_window.py", "module_panel.py", "device_panel.py",
               "bookmark_panel.py")


def _head_snapshot() -> tuple[str, str, str]:
    """返回 (core_snap, old_main_snap, head)。

    core_snap：只钉 desktop 的四个界面模块（main.py 用工作树的**改后**代码）；
    old_main_snap：在 core_snap 基础上再放一份 HEAD 的 main.py，用来量改前基线。
    """
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    snap = os.path.join(tempfile.gettempdir(), "kilo", f"defer_head_{head}")
    os.makedirs(snap, exist_ok=True)

    def _dump(name, out_dir):
        out = subprocess.run(["git", "-C", ROOT, "show", f"HEAD:desktop/{name}"],
                             capture_output=True).stdout
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(out)

    for name in _HEAD_FILES:
        _dump(name, snap)
    snap_old = snap + "_oldmain"
    os.makedirs(snap_old, exist_ok=True)
    for name in _HEAD_FILES + ("main.py",):
        _dump(name, snap_old)
    return snap, snap_old, head


def _one() -> int:
    case = os.environ.get("MBDSDR_CASE", "frame_gap")
    snap = os.environ.get("MBDSDR_HEAD_SNAP")
    if case == "t2_baseline":
        snap = os.environ.get("MBDSDR_HEAD_SNAP_OLD") or snap
    _prepare_env()
    if snap:
        sys.path.insert(0, snap)   # 必须排在 desktop/ 之前：工作树可能正被别的线改
    if os.environ.get("MBDSDR_SWITCH") == "1":
        sys.setswitchinterval(0.001)   # 试「换路子」：缩小 GIL 让出周期

    import main as appmain
    if case == "t2_baseline":
        # 老 main.py 从 __file__ 往上两级找数据件；它现在住在临时快照目录里，
        # 会误判 dso_catalog.json 缺失 → 弹原生 MessageBox 卡死。指回真仓库根。
        appmain._required_data_path = lambda rel: os.path.join(ROOT, rel)
    # 首帧前那两段的墙钟：老代码 = smoke + 硬门槛；新代码 = 只有 find_spec 门槛。
    for _name in ("_run_import_smoke", "_startup_dependency_problems"):
        _fn = getattr(appmain, _name, None)
        if _fn is None:
            continue

        def _wrap(*a, _f=_fn, _n=_name, **k):
            t = time.perf_counter()
            try:
                return _f(*a, **k)
            finally:
                M.setdefault("phase_ms", {})[_n] = round(
                    (time.perf_counter() - t) * 1000.0, 1)

        setattr(appmain, _name, _wrap)
    if case == "frame_gap_nodefer":
        # 对照：延后导入整段不跑，量同一窗口里的掉帧底噪（产品自身的定时器）。
        appmain._start_deferred_imports = lambda window: None

    from PySide6.QtCore import QEvent, QTimer
    from PySide6.QtWidgets import QApplication

    M: dict = {"case": case, "head": os.environ.get("MBDSDR_HEAD", ""),
               "frame_gaps": [], "import_spans": [], "reports": [],
               "failure_dialog": None, "gate_problems": None}

    # --- 每片计时：记录 importlib.import_module 的起止（含线程名） ---
    import threading
    orig_import_module = importlib.import_module

    def timed_import(name, package=None):
        t = time.perf_counter()
        try:
            return orig_import_module(name, package)
        finally:
            M["import_spans"].append({
                "name": name, "thread": threading.current_thread().name,
                "ms": round((time.perf_counter() - t) * 1000.0, 1),
                "t": t})

    importlib.import_module = timed_import

    # --- 失败报告的落点（不弹真窗，记录文案与时刻） ---
    def report_failure(window, module, detail):
        record = {"module": module, "detail": detail,
                  "t": time.perf_counter(),
                  "after_first_frame": M.get("t_first_frame") is not None}
        M["reports"].append(record)
        orig_report(window, module, detail)
        try:                      # 证明「不阻塞的提示」真的落到了状态栏
            record["status_bar"] = window.statusBar().currentMessage()
        except Exception:         # noqa: BLE001
            record["status_bar"] = ""

    orig_report = getattr(appmain, "_report_deferred_import_failure", None)
    if orig_report is not None:          # 改前代码（t2_baseline）没有这个函数
        appmain._report_deferred_import_failure = report_failure

    if case == "gate_missing":
        # 模拟缺件：scipy 系追加一个必然 find_spec 不到的名字。
        appmain._SPEC_FIRST_DEPENDENCIES = list(appmain._SPEC_FIRST_DEPENDENCIES) + [
            ("scipy._mbdsdr_missing_probe", "SciPy 完整性探针（模拟缺件）",
             "模拟：相关功能不可用", "模拟：重新解压完整安装包")]
        appmain._report_startup_failure = (
            lambda summary, detail: M.__setitem__(
                "failure_dialog", {"summary": summary, "detail": detail}))

        def no_window(splash=None):
            M["window_imported_despite_missing"] = True
            raise AssertionError("缺件时不许导入主窗口")

        appmain._import_main_window = no_window

    if case == "defer_broken":
        # 造一个「find_spec 找得到、import 必失败」的模块，模拟 .pyd 加载失败。
        d = tempfile.mkdtemp(prefix="broken-")
        with open(os.path.join(d, "mbdsdr_broken_probe.py"), "w",
                  encoding="utf-8") as f:
            f.write('raise ImportError("模拟：文件在但 .pyd 加载失败")\n')
        sys.path.insert(0, d)
        appmain._RISKY_IMPORTS = ["mbdsdr_broken_probe"]

    # --- 主窗构造/首帧打点 ---
    orig_import_window = appmain._import_main_window

    def import_wrap(splash=None):
        cls = orig_import_window(splash)

        class _MW(cls):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                M["t_mw_created"] = time.perf_counter()
                M["mw"] = self

            def repaint(self):
                super().repaint()
                M["t_repaint_done"] = time.perf_counter()

        return _MW

    appmain._import_main_window = import_wrap

    orig_splash_fn = appmain._show_startup_splash

    def splash_wrap(app):
        sp = orig_splash_fn(app)
        M["t_splash_shown"] = time.perf_counter()
        return sp

    appmain._show_startup_splash = splash_wrap

    # splash 首帧（paintEvent 返回）= T1；主窗首帧 = T1 + T2。
    class _Splash(appmain.QSplashScreen):
        def paintEvent(self, ev):        # noqa: N802 (Qt 命名)
            first = M.get("t_splash_paint_done") is None
            super().paintEvent(ev)
            if first:
                M["t_splash_paint_done"] = time.perf_counter()

    appmain.QSplashScreen = _Splash

    gap_timer = {}
    done_timer = {}

    class _App(appmain.QApplication):
        def notify(self, receiver, event):
            mw = M.get("mw")
            first = (event.type() == QEvent.Paint and mw is not None
                     and receiver is mw and M.get("t_first_frame") is None)
            try:
                return super().notify(receiver, event)
            finally:
                # 首帧 = 这次 Paint **派发完毕**（含实际绘制），不是派发开始。
                if first and M.get("t_first_frame") is None:
                    M["t_first_frame"] = time.perf_counter()
                    _start_probes()

    def _start_probes():
        # 16 ms 心跳：两次回调的间隔就是「这一帧等了多久」的最坏值来源。
        t = QTimer()
        t.setInterval(16)
        state = {"last": time.perf_counter()}

        def tick():
            now = time.perf_counter()
            M["frame_gaps"].append({"t": now,
                                    "gap": round((now - state["last"]) * 1000.0, 1)})
            state["last"] = now

        t.timeout.connect(tick)
        t.start()
        gap_timer["t"] = t

        d = QTimer()
        d.setInterval(100)

        def check():
            w = M.get("mw")
            timer = getattr(w, "_deferred_import_timer", None)
            if timer is not None and not timer.isActive():
                M["t_deferred_done"] = time.perf_counter()
                d.stop()
                QTimer.singleShot(0, QApplication.instance().quit)
            elif timer is None and M.get("t_first_frame"):
                # t2_baseline：改前代码没有延后导入。首帧后再放 300 ms 让心跳
                # 收几个样就收工，别白等 KEEP_ALIVE_MS。
                if not M.get("_quit_scheduled"):
                    M["_quit_scheduled"] = True
                    d.stop()
                    QTimer.singleShot(300, QApplication.instance().quit)
            elif M.get("t_first_frame") and (
                    time.perf_counter() - M["t_first_frame"] > KEEP_ALIVE_MS / 1000):
                M["t_deferred_timeout"] = time.perf_counter()
                d.stop()
                QTimer.singleShot(0, QApplication.instance().quit)

        d.timeout.connect(check)
        d.start()
        done_timer["t"] = d

    appmain.QApplication = _App
    sys.argv = ["mbdsdr", "--theme", "default"]
    M["t_before_main"] = time.perf_counter()
    err = None
    code = 0
    try:
        ret = appmain.main()
        if isinstance(ret, int):
            code = ret
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except BaseException as exc:      # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"

    importlib.import_module = orig_import_module
    mw = M.get("mw")
    status_text = ""
    if mw is not None:
        try:
            status_text = mw.statusBar().currentMessage()
        except Exception:  # noqa: BLE001
            status_text = ""

    result = {
        "case": case, "head": M.get("head"), "exit_code": code, "error": err,
        "failure_dialog": M.get("failure_dialog"),
        "window_imported_despite_missing": M.get(
            "window_imported_despite_missing", False),
        "t_first_frame_ms": (
            round((M["t_first_frame"] - T0) * 1000.0, 1)
            if M.get("t_first_frame") else None),
        "t_splash_paint_ms": (
            round((M["t_splash_paint_done"] - T0) * 1000.0, 1)
            if M.get("t_splash_paint_done") else None),
        "t2_ms": (
            round((M["t_first_frame"] - M["t_splash_paint_done"]) * 1000.0, 1)
            if M.get("t_first_frame") and M.get("t_splash_paint_done") else None),
        "t_first_frame_abs": M.get("t_first_frame"),
        "phase_ms": M.get("phase_ms") or {},
        "repaint_before_first_frame_ms": (
            round((M["t_repaint_done"] - T0) * 1000.0, 1)
            if M.get("t_repaint_done") else None),
        "deferred_done_after_first_frame_ms": (
            round((M["t_deferred_done"] - M["t_first_frame"]) * 1000.0, 1)
            if M.get("t_deferred_done") and M.get("t_first_frame") else None),
        "deferred_timeout": bool(M.get("t_deferred_timeout")),
        "reports": [{"module": r["module"], "detail": r["detail"],
                     "after_first_frame": r["after_first_frame"],
                     "status_bar": r.get("status_bar", "")}
                    for r in M["reports"]],
        "status_bar": status_text,
        "frame_gaps": M["frame_gaps"],
        "import_spans": [s for s in M["import_spans"]
                         if s["thread"] == "mbdsdr-deferred-import"],
        "numpy_imported_before_first_frame": "numpy" in sys.modules,
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    return 0


def _stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {"runs": [round(v, 1) for v in vals],
            "median": round(statistics.median(vals), 1),
            "min": round(min(vals), 1), "max": round(max(vals), 1),
            "range": round(max(vals) - min(vals), 1)}


def _gap_attribution(r: dict, top_n: int = 3) -> list[dict]:
    """把最坏几次掉帧对回当时正在跑的那一片 import。"""
    gaps = sorted(r.get("frame_gaps") or [], key=lambda g: -g["gap"])[:top_n]
    spans = r.get("import_spans") or []
    first = r.get("t_first_frame_abs")
    out = []
    for g in gaps:
        start = g["t"] - g["gap"] / 1000.0
        hit = [s["name"] for s in spans
               if s["t"] < g["t"] and s["t"] + s["ms"] / 1000.0 > start]
        out.append({"gap_ms": g["gap"],
                    "gap_at_ms": (round((g["t"] - first) * 1000.0, 1)
                                  if first else None),
                    "imports_in_gap": hit})
    return out


def _driver(runs: int, cases: list[str]) -> int:
    import psutil
    snap, snap_old, head = _head_snapshot()
    print(f"[snapshot] HEAD {head} -> {snap} / {snap_old}")
    rows = []
    for i in range(runs):
        for case in cases:
            gate = _wait_low(psutil)
            env = dict(os.environ, MBDSDR_HEAD_SNAP=snap,
                       MBDSDR_HEAD_SNAP_OLD=snap_old, MBDSDR_HEAD=head,
                       MBDSDR_CASE=case)
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--one"],
                capture_output=True, text=True, timeout=900, env=env)
            payload = None
            for line in proc.stdout.splitlines():
                if line.startswith("RESULT "):
                    payload = json.loads(line[len("RESULT "):])
            if payload is None:
                print(f"[{case} run{i}] 无结果 rc={proc.returncode}\n"
                      f"{proc.stdout[-600:]}\n{proc.stderr[-600:]}", flush=True)
                continue
            payload["load_before_python_pct"] = (gate or {}).get(
                "python_total_cpu_pct")
            payload["run"] = i
            rows.append(payload)
            gaps = payload["frame_gaps"]
            worst = max((g["gap"] for g in gaps), default=None)
            print(f"[{case} run{i}] t1={payload['t_splash_paint_ms']} "
                  f"t2={payload['t2_ms']} "
                  f"first_frame={payload['t_first_frame_ms']} "
                  f"defer_done=+{payload['deferred_done_after_first_frame_ms']} "
                  f"worst_gap={worst} reports={len(payload['reports'])} "
                  f"phase={payload.get('phase_ms')} "
                  f"exit={payload['exit_code']}", flush=True)

    summary = {}
    for case in cases:
        sub = [r for r in rows if r["case"] == case]
        worst = [max((g["gap"] for g in r["frame_gaps"]), default=None) for r in sub]
        per_slice = {}
        for r in sub:
            for s in r["import_spans"]:
                per_slice.setdefault(s["name"], []).append(s["ms"])
        summary[case] = {
            "t1_splash_paint_ms": _stat([r["t_splash_paint_ms"] for r in sub]),
            "t2_ms": _stat([r["t2_ms"] for r in sub]),
            "first_frame_ms": _stat([r["t_first_frame_ms"] for r in sub]),
            "deferred_total_ms": _stat(
                [r["deferred_done_after_first_frame_ms"] for r in sub]),
            "worst_frame_gap_ms": _stat(worst),
            "worst_gap_attribution": [a for r in sub
                                      for a in _gap_attribution(r)],
            "reports": [{"module": x["module"], "detail": x["detail"],
                         "after_first_frame": x["after_first_frame"],
                         "status_bar": x.get("status_bar", "")}
                        for r in sub for x in r["reports"]],
            "status_bar": [r["status_bar"] for r in sub],
            "exit_codes": [r["exit_code"] for r in sub],
            "errors": [r["error"] for r in sub if r.get("error")],
            "failure_dialog": [r["failure_dialog"] for r in sub][:1],
            "window_imported_despite_missing": any(
                r.get("window_imported_despite_missing") for r in sub),
            "per_slice_ms": {k: _stat(v) for k, v in per_slice.items()},
            "phase_ms": {
                k: _stat([(r.get("phase_ms") or {}).get(k) for r in sub])
                for k in ("_run_import_smoke", "_startup_dependency_problems")
                if any((r.get("phase_ms") or {}).get(k) is not None for r in sub)
            },
        }

    # 改前/改后 T2：都在同一条量法、同一台机、同一 HEAD 快照下量。
    def _t2(case):
        return (summary.get(case, {}).get("t2_ms") or {}).get("median")

    delta_t2 = None
    if _t2("t2_baseline") is not None and _t2("frame_gap") is not None:
        delta_t2 = round(_t2("frame_gap") - _t2("t2_baseline"), 1)
    out = {
        "kind": "延后导入（scipy 私有子树）三条反证 + 最坏帧间隔 + T2 前后对比",
        "date": "2026-11-12", "head": head, "snapshot": snap,
        "snapshot_old_main": snap_old,
        "cases": list(cases), "runs_per_case": runs,
        "load_rule": "规则 10：每 run 前 2 s 低负载门（python 总占比 <200%）",
        "definition": {
            "t1_splash_paint_ms": "子进程 T0（脚本第一行）→ splash 首个 paintEvent 返回",
            "t2_ms": "splash 首个 paintEvent 返回 → 主窗顶层首个 Paint 派发完毕",
            "first_frame_ms": "子进程 T0 → 主窗首个 Paint 派发完毕",
            "worst_frame_gap_ms": "首帧后 16 ms 心跳定时器相邻两次回调的最大间隔；"
                                  "间隔里主线程没机会回到事件循环，就是一次掉帧",
            "deferred_total_ms": "首帧 → 延后导入全部完成（_deferred_import_timer 停止）",
            "t2_baseline": "HEAD 的 desktop/main.py（老代码：smoke + scipy 真 import "
                           "都在 splash 后、主窗前）；frame_gap 是改后代码，两者同一量法",
        },
        "delta_t2_vs_baseline_ms": delta_t2,
        "summary": summary,
        "samples": rows,
    }
    os.makedirs(EVID, exist_ok=True)
    path = os.path.join(EVID, OUT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[saved] {path}")
    for case in cases:
        s = summary[case]
        print(f"{case}: t1={(s['t1_splash_paint_ms'] or {}).get('median')} "
              f"t2={(s['t2_ms'] or {}).get('median')} "
              f"worst_gap={(s['worst_frame_gap_ms'] or {}).get('median')}")
    print("delta_t2_vs_baseline_ms", delta_t2)
    return 0


def main() -> int:
    argv = sys.argv[1:]
    runs = 3
    cases = list(CASES)
    for i, a in enumerate(argv):
        if a == "--runs" and i + 1 < len(argv):
            runs = int(argv[i + 1])
        elif a == "--cases" and i + 1 < len(argv):
            cases = [x for x in argv[i + 1].split(",") if x]
        elif a == "--out" and i + 1 < len(argv):
            global OUT_NAME
            OUT_NAME = argv[i + 1]
    if "--one" in argv:
        return _one()
    return _driver(runs, cases)


if __name__ == "__main__":
    sys.exit(main())

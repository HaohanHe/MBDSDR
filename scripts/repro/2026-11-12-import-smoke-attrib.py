#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""import smoke 1110 ms 的归属、可延后性与反证。

要回答四件事，全部按「同臂 ≥3 次、报中位与极差」的口径：

1. smoke 到底 import 了什么：把 ``importlib._bootstrap._find_and_load`` 包一层，
   按「谁在 import 它」记账（父模块名 + 是否在 ``_RISKY_IMPORTS`` 里直接点名），
   给累计/自耗时 top-N。
2. smoke 的清单从哪来、失败时用户看到什么：本脚本只记录行为，代码位置在报告里。
3. 哪些能在首帧之后做：用 ``defer_smoke`` 臂实测（首帧 paint 派发完毕后同步跑 smoke）。
4. 反证：``skip_one``（从清单里去掉一个模块）与 ``skip_smoke``（整段不跑）两臂，
   看 smoke 自己的耗时变化、以及首帧是否照常到达。

不修改任何产品代码：全部改的是本进程里的模块对象/函数引用。

用法::

    python scripts/repro/2026-11-12-import-smoke-attrib.py --runs 3 \
        --arms base,skip_one,defer_smoke,skip_smoke --skip-mod scipy.optimize
    python scripts/repro/2026-11-12-import-smoke-attrib.py --one --arm base --profile-imports
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
ARMS = ("base", "skip_one", "defer_smoke", "skip_smoke", "spec_gate")


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
    profile = tempfile.mkdtemp(prefix="smoke-")
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
    """desktop 关键模块钉在 HEAD（工作树可能有其它线在改）。"""
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    snap = os.path.join(tempfile.gettempdir(), "kilo", f"smoke_head_{head}")
    os.makedirs(snap, exist_ok=True)
    for name in ("main_window.py", "module_panel.py", "device_panel.py",
                 "bookmark_panel.py"):
        out = subprocess.run(["git", "-C", ROOT, "show", f"HEAD:desktop/{name}"],
                             capture_output=True).stdout
        with open(os.path.join(snap, name), "wb") as f:
            f.write(out)
    return snap, head


def _install_import_profiler(M: dict) -> None:
    """给 importlib 的加载入口记账：模块名 / 父模块 / 累计 ms / 自耗 ms。

    ``_gcd_import`` 每次都从 ``_bootstrap`` 的模块全局取 ``_find_and_load``，
    因此在外部改这个全局即可覆盖所有 import 路径（含 importlib.import_module）。
    """
    import importlib._bootstrap as bs

    orig = bs._find_and_load
    frames: list[dict] = []

    def wrapped(name, import_):
        t0 = time.perf_counter()
        frame = {"name": name,
                 "parent": frames[-1]["name"] if frames else None,
                 "child": 0.0, "depth": len(frames)}
        frames.append(frame)
        try:
            return orig(name, import_)
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            frames.pop()
            M["imports"].append({
                "name": name,
                "parent": frame["parent"],
                "cumulative_ms": round(dt, 3),
                "self_ms": round(max(dt - frame["child"], 0.0), 3),
                "depth": frame["depth"],
                "phase": M.get("phase", ""),
            })
            if frames:
                frames[-1]["child"] += dt

    bs._find_and_load = wrapped


def _one() -> int:
    arm = os.environ.get("MBDSDR_ARM", "base")
    skip_mod = os.environ.get("MBDSDR_SMOKE_SKIP", "scipy.optimize")
    prof = os.environ.get("MBDSDR_PROFILE") == "1"

    snap = os.environ.get("MBDSDR_HEAD_SNAP")
    if snap:
        sys.path.insert(0, snap)
    _prepare_env()

    import main as appmain          # 生产模块级 import 全在这里

    t_import_done = time.perf_counter()
    if snap:
        sys.path.insert(0, snap)
    from PySide6.QtCore import QEvent, QTimer
    from PySide6.QtWidgets import QApplication

    M: dict = {"head": os.environ.get("MBDSDR_HEAD", ""), "arm": arm,
               "imports": [], "smoke_probes": []}

    if prof:
        _install_import_profiler(M)

    # --- 逐项打点：smoke 与依赖探针共用 _probe_import，按 phase 区分 ---
    orig_probe = appmain._probe_import

    def probe(name):
        cached = name in sys.modules      # 探针入口前是否已有（命中缓存 ≈ 0 ms）
        t = time.perf_counter()
        ok = orig_probe(name)
        dt = round((time.perf_counter() - t) * 1000.0, 3)
        M["smoke_probes"].append({"name": name, "ok": ok, "ms": dt,
                                  "phase": M.get("phase", ""),
                                  "cached": cached})
        return ok

    appmain._probe_import = probe

    # smoke 之前 scipy/numpy 是否已经在 sys.modules（决定「谁先付」）
    M["scipy_loaded_before_app_main"] = any(
        n == "scipy" or n.startswith("scipy.") for n in sys.modules)
    M["numpy_loaded_before_app_main"] = "numpy" in sys.modules

    orig_smoke = appmain._run_import_smoke
    M["risky_imports"] = list(appmain._RISKY_IMPORTS)

    if arm == "skip_one":
        appmain._RISKY_IMPORTS = [m for m in appmain._RISKY_IMPORTS
                                  if m != skip_mod]
        M["removed"] = skip_mod
    if arm in ("defer_smoke", "skip_smoke", "spec_gate"):
        appmain._run_import_smoke = lambda: None

    # spec_gate：硬门槛保留，但 scipy 系探针改成 find_spec（只查「文件在不在」，
    # 不付 import 的钱）；numpy 二进制 / PySide6 仍然真 import（DLL 加载失败
    # 只有真 import 才能发现）。这是「可延后」之外唯一的减量选项。
    if arm == "spec_gate":
        import importlib.util

        def probe_spec(name):
            if name.split(".")[0] != "scipy":
                return probe(name)
            t = time.perf_counter()
            try:
                ok = importlib.util.find_spec(name) is not None
            except (ImportError, ModuleNotFoundError, ValueError):
                ok = False
            dt = round((time.perf_counter() - t) * 1000.0, 3)
            M["smoke_probes"].append({"name": name, "ok": ok, "ms": dt,
                                      "phase": M.get("phase", ""),
                                      "cached": name in sys.modules,
                                      "mode": "find_spec"})
            return ok

        appmain._probe_import = probe_spec

    def _timed(name):
        orig = getattr(appmain, name)

        def wrapper(*a, **k):
            M["phase"] = name
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

    class _Splash(appmain.QSplashScreen):
        def show(self):                     # noqa: N802
            M["t_splash_show"] = time.perf_counter()
            super().show()

        def paintEvent(self, ev):           # noqa: N802
            first = M.get("t_splash_paint_done") is None
            super().paintEvent(ev)
            if first:
                M["t_splash_paint_done"] = time.perf_counter()

    appmain.QSplashScreen = _Splash

    orig_import = appmain._import_main_window

    def import_wrap(splash=None):
        M["phase"] = "_import_main_window"
        t = time.perf_counter()
        cls = orig_import(splash)
        M["mw_import_ms"] = round((time.perf_counter() - t) * 1000.0, 1)
        M["mw_file"] = getattr(sys.modules.get("main_window"), "__file__", "")

        class _MW(cls):
            def __init__(self, *a, **k):
                M["phase"] = "mw_ctor"
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
            QTimer.singleShot(60000, self._bail)

        def _bail(self):
            M["timeout"] = True
            self.quit()

        def notify(self, receiver, event):
            mw = M.get("mw")
            if (event.type() == QEvent.Paint and mw is not None
                    and receiver is mw and M.get("t_mw_paint_done") is None):
                t = time.perf_counter()
                try:
                    return super().notify(receiver, event)
                finally:
                    M["t_mw_paint_done"] = time.perf_counter()
                    M["mw_paint_ms"] = round(
                        (M["t_mw_paint_done"] - t) * 1000.0, 1)
                    if arm in ("defer_smoke", "spec_gate"):
                        M["phase"] = "smoke_after_frame"
                        ts = time.perf_counter()
                        try:
                            orig_smoke()
                        except BaseException as exc:   # noqa: BLE001
                            M["deferred_smoke_error"] = (
                                f"{type(exc).__name__}: {exc}")
                        finally:
                            M["deferred_smoke_ms"] = round(
                                (time.perf_counter() - ts) * 1000.0, 1)
                    M["t_after_frame"] = time.perf_counter()
                    QTimer.singleShot(0, QApplication.instance().quit)
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

    result = {
        "head": M.get("head"),
        "arm": arm,
        "removed": M.get("removed"),
        "mw_file": M.get("mw_file"),
        "t0_abs": T0,
        "t_import_done_abs": t_import_done,
        "t_app_created_abs": M.get("t_app_created"),
        "t_splash_show_abs": M.get("t_splash_show"),
        "t_splash_paint_done_abs": M.get("t_splash_paint_done"),
        "t_mw_created_abs": M.get("t_mw_created"),
        "t_mw_show_abs": M.get("t_mw_show"),
        "t_mw_paint_done_abs": M.get("t_mw_paint_done"),
        "import_smoke_ms": M.get("_run_import_smoke_ms"),
        "dependency_probe_ms": M.get("_startup_dependency_problems_ms"),
        "theme_apply_ms": M.get("theme_apply_ms"),
        "mw_import_ms": M.get("mw_import_ms"),
        "mw_ctor_ms": M.get("mw_ctor_ms"),
        "mw_paint_ms": M.get("mw_paint_ms"),
        "deferred_smoke_ms": M.get("deferred_smoke_ms"),
        "deferred_smoke_error": M.get("deferred_smoke_error"),
        "risky_imports": M.get("risky_imports"),
        "smoke_probes": M["smoke_probes"],
        "scipy_loaded_before_app_main": M.get("scipy_loaded_before_app_main"),
        "numpy_loaded_before_app_main": M.get("numpy_loaded_before_app_main"),
        "imports": M["imports"] if prof else None,
        "timeout": M.get("timeout", False),
        "error": err,
        "load_after": load_after,
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    return 0 if err is None and not M.get("timeout") else 1


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
    return r


def _stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {"runs": [round(v, 1) for v in vals],
            "median": round(statistics.median(vals), 1),
            "min": round(min(vals), 1), "max": round(max(vals), 1),
            "range": round(max(vals) - min(vals), 1)}


def _top_modules(imports, key, n=20, phase=None):
    rows = [r for r in imports if phase is None or r.get("phase") == phase]
    seen = {}
    for r in rows:
        cur = seen.get(r["name"])
        if cur is None or r[key] > cur[key]:
            seen[r["name"]] = r
    return sorted(seen.values(), key=lambda r: -r[key])[:n]


def _driver(runs: int, arms: list[str], skip_mod: str, prof_arms: set[str],
            out_name: str) -> int:
    import psutil

    snap, head = _head_snapshot()
    print(f"[snapshot] HEAD {head} -> {snap}")

    rows, prof = [], None
    for i in range(runs):
        for arm in arms:
            gate = _wait_low(psutil)
            if gate is None:
                print(f"[{arm} run{i}] 低负载窗口不足，跳过", flush=True)
                continue
            want_prof = arm in prof_arms and prof is None
            env = dict(os.environ, MBDSDR_HEAD_SNAP=snap, MBDSDR_HEAD=head,
                       MBDSDR_ARM=arm, MBDSDR_SMOKE_SKIP=skip_mod,
                       MBDSDR_PROFILE="1" if want_prof else "0")
            t_spawn = time.perf_counter()
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--one"],
                capture_output=True, text=True, timeout=900, env=env)
            payload = None
            for line in proc.stdout.splitlines():
                if line.startswith("RESULT "):
                    payload = json.loads(line[len("RESULT "):])
            if payload is None:
                print(f"[{arm} run{i}] 无结果 rc={proc.returncode}\n"
                      f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}", flush=True)
                continue
            payload["interpreter_ms"] = round((payload["t0_abs"] - t_spawn) * 1000.0, 1)
            payload["load_gate_before_python_pct"] = gate["python_total_cpu_pct"]
            payload = _derive(payload)
            if want_prof:
                prof = payload
            rows.append(payload)
            print(f"[{arm} run{i}] t1={payload['_t1']} t2={payload['_t2']} "
                  f"smoke={payload['import_smoke_ms']} "
                  f"dep={payload['dependency_probe_ms']} "
                  f"defer={payload.get('deferred_smoke_ms')} "
                  f"err={payload['error']}", flush=True)

    def col(arm, key):
        return [r[key] for r in rows if r["arm"] == arm and r.get(key) is not None]

    summary = {}
    for arm in arms:
        summary[arm] = {
            "t1_ms": _stat(col(arm, "_t1")),
            "t2_ms": _stat(col(arm, "_t2")),
            "entry_to_first_frame_ms": _stat(col(arm, "_entry")),
            "import_smoke_ms": _stat(col(arm, "import_smoke_ms")),
            "dependency_probe_ms": _stat(col(arm, "dependency_probe_ms")),
            "mw_import_ms": _stat(col(arm, "mw_import_ms")),
            "mw_ctor_ms": _stat(col(arm, "mw_ctor_ms")),
            "deferred_smoke_ms": _stat(col(arm, "deferred_smoke_ms")),
            "errors": [r["error"] for r in rows
                       if r["arm"] == arm and r.get("error")],
            "reached_first_frame": all(r.get("_t2") is not None
                                       for r in rows if r["arm"] == arm),
        }

    base_t2 = (summary.get("base", {}).get("t2_ms") or {}).get("median")
    delta = {}
    for arm in arms:
        if arm == "base":
            continue
        v = (summary.get(arm, {}).get("t2_ms") or {}).get("median")
        if v is not None and base_t2 is not None:
            delta[arm] = round(v - base_t2, 1)

    out = {
        "kind": "import smoke（1100 ms）的模块归属、可延后性与隔离反证",
        "date": "2026-11-12",
        "head": head,
        "snapshot": snap,
        "arms": {
            "base": "生产原样：_run_import_smoke 在 splash 之后、主窗构造之前同步跑",
            "skip_one": f"只从 _RISKY_IMPORTS 去掉 {skip_mod}（其余不变）",
            "defer_smoke": "smoke 不跑；主窗首个 Paint 派发完毕后同步补跑（首帧之后）",
            "skip_smoke": "smoke 完全不跑（上界；依赖探针仍在首帧前）",
            "spec_gate": "硬门槛保留但 scipy 系探针改 find_spec（只查文件在不在）+ "
                         "smoke 挪到首帧后；numpy 二进制与 PySide6 仍真 import",
        },
        "definition": {
            "T2": "splash 首帧 paintEvent 返回 → 主窗顶层 Paint 派发完毕",
            "entry_to_first_frame": "T0（本脚本第一行）→ 主窗首个 Paint 派发完毕",
            "import_smoke_ms": "包住 appmain._run_import_smoke 的墙钟",
        },
        "load_rule": "规则 10：每 run 前父进程采 2 s（python 总占比 <200%），"
                     "子进程跑完再采一次。",
        "runs": runs,
        "summary": summary,
        "delta_vs_base_t2_ms": delta,
        "samples": [{k: v for k, v in r.items() if k != "imports"} for r in rows],
        "profile_run": None,
    }

    if prof is not None:
        imps = prof["imports"]
        smoke_imps = [r for r in imps if r.get("phase") == "_run_import_smoke"]
        out["profile_run"] = {
            "arm": prof["arm"],
            "note": "profile 只在一条 run 上开（_find_and_load 包装会给每次 import "
                    "加 Python 层开销，绝对值略高于计时臂，只用来看结构与父子关系）。",
            "scipy_loaded_before_app_main": prof.get("scipy_loaded_before_app_main"),
            "numpy_loaded_before_app_main": prof.get("numpy_loaded_before_app_main"),
            "module_count_in_smoke": len({r["name"] for r in smoke_imps}),
            "smoke_probes": [p for p in prof["smoke_probes"]
                             if p.get("phase") == "_run_import_smoke"],
            "top20_by_cumulative_in_smoke": _top_modules(smoke_imps, "cumulative_ms"),
            "top20_by_self_in_smoke": _top_modules(smoke_imps, "self_ms"),
            "risky_imports": prof.get("risky_imports"),
        }

    os.makedirs(EVID, exist_ok=True)
    path = os.path.join(EVID, out_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[saved] {path}")
    for arm in arms:
        s = summary[arm]
        print(arm, "t2=", (s["t2_ms"] or {}).get("median"),
              "smoke=", (s["import_smoke_ms"] or {}).get("median"),
              "dep=", (s["dependency_probe_ms"] or {}).get("median"))
    print("delta_vs_base_t2", delta)
    if out["profile_run"]:
        pr = out["profile_run"]
        print("smoke modules:", pr["module_count_in_smoke"])
        print("scipy before:", pr["scipy_loaded_before_app_main"],
              "numpy before:", pr["numpy_loaded_before_app_main"])
        for r in pr["top20_by_cumulative_in_smoke"][:12]:
            print(f"  {r['cumulative_ms']:9.1f} cum {r['self_ms']:8.1f} self "
                  f"{r['name']}  <- {r['parent']}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    runs = 3
    arms = list(ARMS)
    skip_mod = "scipy.optimize"
    prof_arms: set[str] = set()
    out_name = "import-smoke-attrib.json"
    for i, a in enumerate(argv):
        if a == "--runs" and i + 1 < len(argv):
            runs = int(argv[i + 1])
        elif a == "--arms" and i + 1 < len(argv):
            arms = [x for x in argv[i + 1].split(",") if x]
        elif a == "--skip-mod" and i + 1 < len(argv):
            skip_mod = argv[i + 1]
        elif a == "--out" and i + 1 < len(argv):
            out_name = argv[i + 1]
        elif a == "--profile-arms" and i + 1 < len(argv):
            prof_arms = set(argv[i + 1].split(","))
    if "--one" in argv:
        return _one()
    if not prof_arms:
        prof_arms = {"base"}
    return _driver(runs, arms, skip_mod, prof_arms, out_name)


if __name__ == "__main__":
    sys.exit(main())

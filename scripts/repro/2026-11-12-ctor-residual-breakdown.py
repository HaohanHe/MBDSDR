#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""构造期「≈815 ms 未归属」拆解 + 隐藏期 QSS 是否在 show 二次 polish。

两个模式，都是独立进程、每个 3 次：

  --mode ctor      只插时点，不改产品逻辑：
                   * 脚本入口 → import 完成 → QApplication 创建 → 主窗 __init__ 入口/返回；
                   * 主窗所有 ``_build_* / _restore_* / _init_* / _apply_* / _load_* /
                     _setup_* / _add_right_tab`` 用栈式计时器包裹（能给出
                     inclusive / exclusive / 父节点），构造期没人调的不会产生记录；
                   * 叶子嫌疑：``DesktopSettings.load`` / ``Theme.apply`` /
                     ``MCPWorkerManager`` / ``AudioPlayer`` / ``RealGNSSMonitor`` /
                     ``VfoManager`` / ``SharedDemodConfig`` / ``QIcon``；
                   * 构造期首次 import 的模块单独记账（``builtins.__import__`` 包裹）。
                   残余 = 主窗 __init__ exclusive − 上述可命名项，明确列出，不写「其它」。

  --mode qss-show  统计「不可见时 apply QSS」之后 show 时是否二次 polish：
                   应用级事件过滤器按 C++ 指针记 Polish / StyleChange / Paint 次数，
                   分 phase1（构造期主题应用）/ phase2（show + processEvents）。
                   ``--no-theme`` 是对照组：构造期把 ``Theme.apply`` 置空，
                   show 之后再应用一次，用来判断 show 那段时间到底是首帧还是重抛光。

规则 10：每个子进程测前/测后各采一次负载；python 总占比 ≥200% 的窗口不出数。
"""
from __future__ import annotations

import builtins
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time

T0 = time.perf_counter()          # 进程入口（本文件第一行之后）

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVID = os.path.join(ROOT, "docs", "perf-evidence", "2026-11-12")
LOW_LOAD_PCT = 200.0
TMP = os.path.join(tempfile.gettempdir(), "kilo")
os.makedirs(TMP, exist_ok=True)

# ---------------------------------------------------------------------------
# 负载（规则 10）
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 隔离 profile（不碰真实 ~/.mbdsdr）
# ---------------------------------------------------------------------------
def _prepare_env() -> str:
    profile = tempfile.mkdtemp(prefix="ctor-res-profile-")
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


# ---------------------------------------------------------------------------
# 栈式计时器
# ---------------------------------------------------------------------------
STACK: list[dict] = []
ROWS: dict[str, dict] = {}
CALLS: list[dict] = []


def _enter(label: str) -> dict:
    frame = {"name": label, "t": time.perf_counter(), "child": 0.0,
             "parent": STACK[-1]["name"] if STACK else None,
             "depth": len(STACK)}
    STACK.append(frame)
    return frame


def _exit(label: str, frame: dict) -> None:
    dt = (time.perf_counter() - frame["t"]) * 1000.0
    if STACK and STACK[-1] is frame:
        STACK.pop()
    row = ROWS.setdefault(label, {"calls": 0, "inclusive_ms": 0.0,
                                  "exclusive_ms": 0.0, "parents": {}})
    row["calls"] += 1
    row["inclusive_ms"] += dt
    row["exclusive_ms"] += dt - frame["child"]
    p = str(frame["parent"])
    row["parents"][p] = row["parents"].get(p, 0) + 1
    if STACK:
        STACK[-1]["child"] += dt
    CALLS.append({"name": label, "parent": frame["parent"], "depth": frame["depth"],
                  "ms": round(dt, 2), "exclusive_ms": round(dt - frame["child"], 2)})


def _wrap(owner, name: str, label: str | None = None) -> bool:
    """给 owner.name 套栈式计时。owner 可以是类（普通函数/classmethod）或实例。"""
    try:
        raw = owner.__dict__.get(name) if isinstance(owner, type) else None
        orig = getattr(owner, name)
        label = label or f"{getattr(owner, '__name__', type(owner).__name__)}.{name}"
        if isinstance(raw, classmethod):
            fn = orig.__func__

            def wrapper(cls_, *a, **k):
                fr = _enter(label)
                try:
                    return fn(cls_, *a, **k)
                finally:
                    _exit(label, fr)

            setattr(owner, name, classmethod(wrapper))
        else:
            def wrapper(*a, **k):
                fr = _enter(label)
                try:
                    return orig(*a, **k)
                finally:
                    _exit(label, fr)

            setattr(owner, name, wrapper)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[wrap-skip] {name}: {type(exc).__name__}: {exc}", flush=True)
        return False


# ---------------------------------------------------------------------------
# 构造期首次 import 记账
# ---------------------------------------------------------------------------
IMPORTS: dict[str, dict] = {}
_ORIG_IMPORT = builtins.__import__
_HOOK = {"on": False}


def _timed_import(name, globals=None, locals=None, fromlist=(), level=0):
    if not _HOOK["on"]:
        return _ORIG_IMPORT(name, globals, locals, fromlist, level)
    t = time.perf_counter()
    new = name not in sys.modules
    try:
        return _ORIG_IMPORT(name, globals, locals, fromlist, level)
    finally:
        dt = (time.perf_counter() - t) * 1000.0
        if new and dt >= 0.1:
            top = name.split(".")[0]
            row = IMPORTS.setdefault(top, {"ms": 0.0, "calls": 0, "parents": {}})
            row["ms"] += dt
            row["calls"] += 1
            import threading
            parent = STACK[-1]["name"] if STACK else None
            key = f"{parent}@{threading.current_thread().name}"
            row["parents"][key] = row["parents"].get(key, 0) + 1


builtins.__import__ = _timed_import


def _labels_of(cls, prefixes: str) -> list[str]:
    pat = re.compile(prefixes)
    return [n for n in cls.__dict__ if not n.startswith("__") and pat.match(n)]


# ---------------------------------------------------------------------------
# mode=ctor
# ---------------------------------------------------------------------------
def _run_ctor(args) -> dict:
    profile = _prepare_env()
    import psutil
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFontMetrics, QFontMetricsF, QIcon
    from PySide6.QtWidgets import (QApplication, QComboBox, QMainWindow, QStackedWidget,
                                   QWidget)

    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope,
                          os.path.join(profile, "qsettings"))

    t_import = time.perf_counter()
    import layout_store as ls
    ls.reset_layout_store_singleton()
    ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
    import settings as settings_mod
    import themes as themes_mod
    import touch_manager as tm_mod
    import main_window as mw
    import_phase_ms = (time.perf_counter() - t_import) * 1000.0

    app = QApplication(sys.argv[:1])
    from themes import get_app_font
    app.setFont(get_app_font())
    qapp_ms = (time.perf_counter() - t_import) * 1000.0 - import_phase_ms

    # 主窗方法全部上栈
    for name in _labels_of(mw.MainWindow,
                           r"^(_build_|_restore_|_init_|_apply_|_load_|_setup_|"
                           r"_add_right_tab$|_panels_set_sdr_connected$|"
                           r"_create_sky_widget$|_perf_key$|_perf_gnss_poll_ms$)"):
        _wrap(mw.MainWindow, name, f"MW.{name}")
    _wrap(mw.MainWindow, "__init__", "MW.__init__")
    _wrap(QMainWindow, "__init__", "QMainWindow.__init__")

    # 叶子嫌疑
    for owner, name, label in (
            (settings_mod.DesktopSettings, "load", "DesktopSettings.load"),
            (themes_mod.Theme, "apply", "Theme.apply"),
            (tm_mod.TouchManager, "set_from_settings", "TouchManager.set_from_settings"),
            (mw.MCPWorkerManager, "__init__", "MCPWorkerManager.__init__"),
            (mw.AudioPlayer, "__init__", "AudioPlayer.__init__"),
            (mw.RealGNSSMonitor, "__init__", "RealGNSSMonitor.__init__"),
            (mw.VfoManager, "__init__", "VfoManager.__init__"),
            (mw.SharedDemodConfig, "__init__", "SharedDemodConfig.__init__"),
            (mw.LoopSatTracker, "__init__", "LoopSatTracker.__init__"),
            (QIcon, "__init__", "QIcon.__init__"),
            (QFontMetrics, "__init__", "QFontMetrics.__init__"),
            (QFontMetricsF, "__init__", "QFontMetricsF.__init__"),
            (QWidget, "setStyleSheet", "QWidget.setStyleSheet"),
            (QMainWindow, "setCentralWidget", "QMainWindow.setCentralWidget"),
            (QStackedWidget, "addWidget", "QStackedWidget.addWidget"),
            (QWidget, "minimumSizeHint", "QWidget.minimumSizeHint"),
            (QComboBox, "addItems", "QComboBox.addItems"),
    ):
        if owner is not None:
            _wrap(owner, name, label)

    if args.get("widgets"):
        from PySide6 import QtWidgets
        for qt_name in ("QTableWidget", "QComboBox", "QGroupBox", "QTabWidget",
                        "QScrollArea", "QStackedWidget", "QListWidget", "QTreeWidget",
                        "QCheckBox", "QPushButton", "QToolButton", "QLabel", "QSlider",
                        "QSpinBox", "QLineEdit", "QProgressBar", "QFrame"):
            cls = getattr(QtWidgets, qt_name, None)
            if cls is not None:
                _wrap(cls, "__init__", f"W.{qt_name}")
        # 自绘控件：构造期真正被建的那几个
        import importlib
        for mod_name, cls_name in (("rf_sky_view", "RFSkyView"),
                                   ("spectrum_widget", "SpectrumCurveWidget"),
                                   ("spectrum_widget", "WaterfallWidget"),
                                   ("freq_display", "FreqDisplay"),
                                   ("right_column", "_SectionHeader"),
                                   ("gain_panel", "_StageSlider")):
            try:
                cls = getattr(importlib.import_module(mod_name), cls_name)
            except Exception:
                continue
            _wrap(cls, "__init__", f"W.{cls_name}")

    load_before = _wait_low(psutil, float(args.get("wait_low", 240)))
    if load_before is None:
        return {"aborted": "240 s 内没有 python 总占比 <200% 的窗口"}

    prewarm_font_ms = None
    if args.get("prewarm_font"):
        from PySide6.QtGui import QFont
        t_pw = time.perf_counter()
        QFontMetrics(app.font()).horizontalAdvance("Ag中")
        _mono = QFont("Consolas")
        _mono.setPointSizeF(14)
        QFontMetrics(_mono).horizontalAdvance("123.456 MHz")
        prewarm_font_ms = round((time.perf_counter() - t_pw) * 1000.0, 1)

    _HOOK["on"] = True
    t_ctor = time.perf_counter()
    window = mw.MainWindow()
    ctor_ms = (time.perf_counter() - t_ctor) * 1000.0
    _HOOK["on"] = False

    widgets = len(app.allWidgets())
    font = app.font()
    fm = QFontMetrics(font)
    ruler = {"qt_platform": os.environ.get("QT_QPA_PLATFORM"),
             "font": font.family(), "pt": font.pointSizeF(),
             "line_h": fm.height(), "dpr": app.primaryScreen().devicePixelRatio()}
    load_after = _load(psutil, 2.0)

    top = []
    for label, row in ROWS.items():
        top.append({"name": label, "calls": row["calls"],
                    "inclusive_ms": round(row["inclusive_ms"], 1),
                    "exclusive_ms": round(row["exclusive_ms"], 1),
                    "parents": {k: v for k, v in sorted(
                        row["parents"].items(), key=lambda kv: -kv[1])[:4]}})
    top.sort(key=lambda r: -r["exclusive_ms"])

    ctor_row = ROWS.get("MW.__init__", {})
    # 只算「父节点就是 MW.__init__」的那些调用（同一方法在别的父节点下的耗时不并入）
    named_ms = sum(c["ms"] for c in CALLS if c["parent"] == "MW.__init__")
    residual_ms = round(ctor_ms - named_ms, 1)

    imports = sorted(({"module": k, **v, "ms": round(v["ms"], 1)}
                      for k, v in IMPORTS.items()),
                     key=lambda r: -r["ms"])[:15]

    ctor_top = {}
    for c in CALLS:
        if c["parent"] == "MW.__init__":
            row = ctor_top.setdefault(c["name"], {"calls": 0, "ms": 0.0})
            row["calls"] += 1
            row["ms"] += c["ms"]
    ctor_top = sorted(({"name": k, "calls": v["calls"],
                        "inclusive_ms": round(v["ms"], 1)}
                       for k, v in ctor_top.items()),
                      key=lambda r: -r["inclusive_ms"])

    # 完整父子树（parent → 直接子项合计），每层最多 15 条，供任意下钻
    region_children = {}
    for c in CALLS:
        parent_name = c["parent"]
        if parent_name is None:
            continue
        row = region_children.setdefault(parent_name, {}).setdefault(
            c["name"], {"calls": 0, "ms": 0.0})
        row["calls"] += 1
        row["ms"] += c["ms"]
    region_children = {
        p: sorted(({"name": k, "calls": v["calls"],
                    "inclusive_ms": round(v["ms"], 1)}
                   for k, v in d.items()), key=lambda r: -r["inclusive_ms"])[:15]
        for p, d in region_children.items()}

    leaked = [c for c in CALLS if c["parent"] is None]
    try:
        window.close()
    except Exception:
        pass

    return {"load_before": load_before, "load_after": load_after,
            "import_phase_ms": round(import_phase_ms, 1),
            "qapp_ms": round(qapp_ms, 1),
            "prewarm_font_ms": prewarm_font_ms,
            "ctor_ms": round(ctor_ms, 1),
            "definition_c_ms": round((time.perf_counter() - T0) * 1000.0, 1),
            "widgets": widgets, "ruler": ruler,
            "named_top_level_ms": round(named_ms, 1),
            "residual_ms": residual_ms,
            "ctor_row": {"calls": ctor_row.get("calls"),
                         "inclusive_ms": round(ctor_row.get("inclusive_ms", 0.0), 1),
                         "exclusive_ms": round(ctor_row.get("exclusive_ms", 0.0), 1)},
            "rows": top, "ctor_top_level": ctor_top,
            "region_children": region_children,
            "imports_during_ctor": imports,
            "unparented_calls": leaked[:10]}


# ---------------------------------------------------------------------------
# mode=qss-show
# ---------------------------------------------------------------------------
def _run_qss(args) -> dict:
    profile = _prepare_env()
    import psutil
    from PySide6.QtCore import QEvent, QObject, QSettings
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QApplication

    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope,
                          os.path.join(profile, "qsettings"))
    import layout_store as ls
    ls.reset_layout_store_singleton()
    ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
    import themes as themes_mod
    app = QApplication(sys.argv[:1])
    from themes import get_app_font
    app.setFont(get_app_font())
    import main_window as mw

    try:
        import shiboken6

        def _ptr(o):
            return int(shiboken6.getCppPointer(o)[0])
    except Exception:  # noqa: BLE001
        def _ptr(o):
            return id(o)

    kinds = {QEvent.Type.Polish: "Polish",
             QEvent.Type.PolishRequest: "PolishRequest",
             QEvent.Type.StyleChange: "StyleChange",
             QEvent.Type.PaletteChange: "PaletteChange",
             QEvent.Type.Paint: "Paint"}
    state = {"phase": 1,
             "counts": {1: {}, 2: {}},
             "ids": {1: set(), 2: set()}}

    class Counter(QObject):
        def eventFilter(self, obj, ev):  # noqa: N802
            name = kinds.get(ev.type())
            if name:
                ph = state["phase"]
                state["counts"][ph][name] = state["counts"][ph].get(name, 0) + 1
                if name in ("Polish", "StyleChange"):
                    try:
                        state["ids"][ph].add(_ptr(obj))
                    except Exception:
                        pass
            return False

    counter = Counter()
    app.installEventFilter(counter)

    no_theme = bool(args.get("no_theme"))
    if no_theme:
        themes_mod.Theme.apply = lambda self, app_: None  # noqa: ARG005

    skip_theme = bool(args.get("skip_theme"))
    if skip_theme:
        # 真·跳过：构造期不挂任何主题 QSS（_apply_theme 置空），show 之后再挂一次。
        def _noop_theme(self, name):
            self._current_theme = name
            self._theme_qss = ""

        mw.MainWindow._apply_theme = _noop_theme

    load_before = _wait_low(psutil, float(args.get("wait_low", 240)))
    if load_before is None:
        return {"aborted": "240 s 内没有 python 总占比 <200% 的窗口"}

    t_ctor = time.perf_counter()
    window = mw.MainWindow()
    ctor_ms = (time.perf_counter() - t_ctor) * 1000.0
    widgets = len(app.allWidgets())
    # 关掉启动定时器，避免 100/200/400 ms 的任务混进 show 读数
    for attr in ("_startup_timers",):
        for t in getattr(window, attr, []) or []:
            try:
                t.stop()
            except Exception:
                pass
    for attr in ("_sky_update_timer", "_gnss_timer", "_utc_timer"):
        try:
            getattr(window, attr).stop()
        except Exception:
            pass

    state["phase"] = 2
    t_show = time.perf_counter()
    window.show()
    show_sync_ms = (time.perf_counter() - t_show) * 1000.0
    t_tick = time.perf_counter()
    app.processEvents()
    paint_ms = (time.perf_counter() - t_tick) * 1000.0
    show_ms = show_sync_ms + paint_ms

    apply_after_ms = None
    if no_theme or skip_theme:
        t_ap = time.perf_counter()
        themes_mod.get_theme("dark").apply(app)
        app.processEvents()
        apply_after_ms = (time.perf_counter() - t_ap) * 1000.0

    load_after = _load(psutil, 2.0)
    font = app.font()
    fm = QFontMetrics(font)
    out = {"no_theme": no_theme, "skip_theme": skip_theme,
           "load_before": load_before, "load_after": load_after,
           "ctor_ms": round(ctor_ms, 1),
           "show_sync_ms": round(show_sync_ms, 1),
           "paint_ms": round(paint_ms, 1),
           "show_plus_events_ms": round(show_ms, 1),
           "apply_after_show_ms": (None if apply_after_ms is None
                                   else round(apply_after_ms, 1)),
           "widgets": widgets,
           "ruler": {"qt_platform": os.environ.get("QT_QPA_PLATFORM"),
                     "font": font.family(), "pt": font.pointSizeF(),
                     "line_h": fm.height()},
           "phase1_ctor": state["counts"][1],
           "phase1_widgets_with_polish": len(state["ids"][1]),
           "phase2_show": state["counts"][2],
           "phase2_widgets_with_polish": len(state["ids"][2]),
           "widgets_polished_both_phases": len(state["ids"][1] & state["ids"][2])}
    try:
        window.close()
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
def _once(args) -> int:
    mode = args["mode"]
    fn = _run_ctor if mode == "ctor" else _run_qss
    try:
        data = fn(args)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        data = {"aborted": f"{type(exc).__name__}: {exc}"}
    with open(args["out"], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return 0


def _parse(argv: list[str]) -> dict:
    def val(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    return {"mode": val("--mode", "ctor"),
            "out": val("--out"),
            "runs": int(val("--runs", "3")),
            "wait_low": float(val("--wait-low", "240")),
            "no_theme": "--no-theme" in argv,
            "widgets": "--widgets" in argv,
            "prewarm_font": "--prewarm-font" in argv,
            "skip_theme": "--skip-theme" in argv,
            "once": "--once" in argv}


def _median(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(vals), 1) if vals else None


def _parent(args) -> int:
    runs = []
    for i in range(args["runs"]):
        out = os.path.join(TMP, f"ctorres_{args['mode']}_{os.getpid()}_{i}.json")
        cmd = [sys.executable, os.path.abspath(__file__), "--once",
               "--mode", args["mode"], "--out", out,
               "--wait-low", str(args["wait_low"])]
        if args["no_theme"]:
            cmd.append("--no-theme")
        if args["widgets"]:
            cmd.append("--widgets")
        if args["prewarm_font"]:
            cmd.append("--prewarm-font")
        if args["skip_theme"]:
            cmd.append("--skip-theme")
        print(f"[run {i + 1}/{args['runs']}] {' '.join(cmd[-4:])}", flush=True)
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True)
        tail = proc.stdout.decode("utf-8", "replace").strip().splitlines()[-3:]
        for line in tail:
            print("   ", line, flush=True)
        if proc.returncode != 0:
            print(proc.stderr.decode("utf-8", "replace")[-1500:], flush=True)
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                runs.append(json.load(f))
            os.remove(out)

    ok = [r for r in runs if not r.get("aborted")]
    payload = {"question": ("构造期 ≈815 ms 未归属拆解" if args["mode"] == "ctor"
                            else "不可见时 apply QSS 后 show 是否二次 polish"),
               "method": ("栈式计时器包主窗 _build_/_restore_/_init_/_apply_/... 方法 + "
                          "叶子嫌疑 + 构造期首次 import 记账；每个 case 独立进程"),
               "runs": runs}
    if not ok:
        payload["verdict"] = "窗口不满足：3 次都没有拿到 python 总占比 <200% 的窗口，不出数"
    elif args["mode"] == "ctor":
        keys = ("import_phase_ms", "qapp_ms", "ctor_ms", "named_top_level_ms",
                "residual_ms", "widgets", "prewarm_font_ms")
        payload["medians"] = {k: _median([r[k] for r in ok]) for k in keys}
        agg = {}
        for r in ok:
            for row in r["rows"]:
                a = agg.setdefault(row["name"], {"exclusive": [], "inclusive": [],
                                                 "calls": []})
                a["exclusive"].append(row["exclusive_ms"])
                a["inclusive"].append(row["inclusive_ms"])
                a["calls"].append(row["calls"])
                a.setdefault("parents", row["parents"])
        table = [{"name": k, "exclusive_ms": _median(v["exclusive"]),
                  "inclusive_ms": _median(v["inclusive"]),
                  "calls": _median(v["calls"]), "parents": v["parents"]}
                 for k, v in agg.items()]
        table.sort(key=lambda r: -(r["exclusive_ms"] or 0))
        payload["table"] = table
        topagg = {}
        for r in ok:
            for row in r.get("ctor_top_level", []):
                topagg.setdefault(row["name"], []).append(row["inclusive_ms"])
        payload["ctor_top_level"] = sorted(
            ({"name": k, "inclusive_ms": _median(v), "runs": len(v)}
             for k, v in topagg.items()), key=lambda r: -(r["inclusive_ms"] or 0))
        rcagg = {}
        for r in ok:
            for parent_name, rows in (r.get("region_children") or {}).items():
                for row in rows:
                    rcagg.setdefault(parent_name, {}).setdefault(
                        row["name"], []).append(row["inclusive_ms"])
        payload["region_children"] = {
            p: sorted(({"name": k, "inclusive_ms": _median(v), "runs": len(v)}
                       for k, v in d.items()), key=lambda r: -(r["inclusive_ms"] or 0))
            for p, d in rcagg.items()}
        imp = {}
        for r in ok:
            for row in r["imports_during_ctor"]:
                imp.setdefault(row["module"], []).append(row["ms"])
        payload["imports_during_ctor"] = sorted(
            ({"module": k, "median_ms": _median(v)} for k, v in imp.items()),
            key=lambda r: -(r["median_ms"] or 0))[:12]
        payload["verdict"] = (
            "构造期拆解完成；residual_ms 是主窗 __init__ 里未被任何 _build_/_restore_/"
            "_init_/_apply_/叶子项覆盖的直写代码，已显式列出。")
    else:
        keys = ("ctor_ms", "show_sync_ms", "paint_ms", "show_plus_events_ms",
                "apply_after_show_ms", "widgets",
                "phase1_widgets_with_polish", "phase2_widgets_with_polish",
                "widgets_polished_both_phases")
        payload["medians"] = {k: _median([r.get(k) for r in ok]) for k in keys}
        for ph in ("phase1_ctor", "phase2_show"):
            agg = {}
            for r in ok:
                for k, v in (r.get(ph) or {}).items():
                    agg.setdefault(k, []).append(v)
            payload.setdefault("medians", {})[ph] = {k: _median(v) for k, v in agg.items()}
        payload["verdict"] = ("见 medians：widgets_polished_both_phases > 0 且 "
                              "phase2_show 的 Polish/StyleChange 计数可观 → show 会重刷；"
                              "若两者≈0 且 show 耗时≈纯首帧 Paint → 不重刷。")

    name = ("ctor-residual-breakdown" if args["mode"] == "ctor"
            else "qss-hidden-then-show-repolish")
    if args["widgets"] and args["mode"] == "ctor":
        name += "-with-widget-types"
    if args["prewarm_font"]:
        name += "-prewarm-font"
    if args["skip_theme"]:
        name += "-skip-theme"
    if args["no_theme"]:
        name += "-no-theme-control"
    path = os.path.join(EVID, name + ".json")
    os.makedirs(EVID, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n[saved] {path}")
    print(json.dumps(payload.get("medians", {}), ensure_ascii=False, indent=1))
    return 0 if ok else 3


def main() -> int:
    args = _parse(sys.argv[1:])
    if args["once"]:
        return _once(args)
    return _parent(args)


if __name__ == "__main__":
    sys.exit(main())

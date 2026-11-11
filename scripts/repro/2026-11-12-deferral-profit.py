#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""「可延后」利润表：按「首帧是否需要它被画出来」重算三块。

三个模式，都是独立进程：

  --mode probe    基线 + 逐次拆账：
                  * 默认页归属（left_tab.currentIndex / is sky_view / _app_mode）；
                  * ``_create_sky_widget`` 的 inclusive / exclusive；
                  * ``Theme.apply`` 每次的 ms / qss 哈希 / palette、font、qss 三项
                    比较结果 / 实际分支（early_return / setStyleSheet / repolish）；
                  * ``QStackedWidget.addWidget`` 与 ``QMainWindow.setCentralWidget``
                    每次的 ms，以及各自区间内 ``minimumSizeHint`` 的控件身份集合
                    （用来判断两者是否对同一批控件重复算尺寸）。

  --mode stub     把 ``_create_sky_widget`` 换成同接口空控件跑一次构造（字体度量
                  由首帧里下一个需要文字尺寸的控件承担），构造完成并出首帧后再
                  调真实工厂一次 + 插回页签一次——回答「延后到首次切页要付多少、
                  会不会复现 394」。

  --mode prewarm  构造前预暖 MiSans / Consolas 字体度量，用来把冷启动的
                  ``_create_sky_widget`` 拆成「首次字体度量」与「控件构造」。

规则 10：每个子进程测前/测后各采一次负载；python 总占比 ≥200% 的窗口不出数。
不修改任何产品代码；main_window.py 归卫星线。
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import types

T0 = time.perf_counter()

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
# 隔离 profile
# ---------------------------------------------------------------------------
def _head_snapshot() -> tuple[str, str]:
    """把 HEAD 版本的 desktop 关键模块导出到临时目录并优先导入。

    工作树里 main_window.py 正被卫星线改（惰性星象仪），直接测工作树会测到
    一个移动目标；利润表的基线必须钉在可引用的 commit 上。
    """
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    snap = os.path.join(TMP, f"head_snapshot_{head}")
    os.makedirs(snap, exist_ok=True)
    for name in ("main_window.py", "module_panel.py", "device_panel.py",
                 "bookmark_panel.py"):
        rel = f"desktop/{name}"
        proc = subprocess.run(["git", "-C", ROOT, "show", f"HEAD:{rel}"],
                              capture_output=True)
        with open(os.path.join(snap, name), "wb") as f:
            f.write(proc.stdout)
    return snap, head


def _prepare_env() -> str:
    profile = tempfile.mkdtemp(prefix="defer-profit-")
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


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def _ptr(obj) -> int:
    try:
        import shiboken6
        return int(shiboken6.getCppPointer(obj)[0])
    except Exception:  # noqa: BLE001
        return id(obj)


def _describe(obj) -> dict:
    try:
        name = type(obj).__name__
        oname = obj.objectName() if hasattr(obj, "objectName") else ""
        return {"class": name, "object": oname, "ptr": _ptr(obj)}
    except Exception:  # noqa: BLE001
        return {"class": "?", "object": "", "ptr": 0}


# ---------------------------------------------------------------------------
# 通用计时栈（只给主窗方法 / 天空工厂用，避免影响 Qt 虚函数派发）
# ---------------------------------------------------------------------------
STACK: list[dict] = []


def _enter(label: str) -> dict:
    fr = {"name": label, "t": time.perf_counter(), "child": 0.0,
          "parent": STACK[-1]["name"] if STACK else None}
    STACK.append(fr)
    return fr


def _exit(frame: dict) -> tuple[float, float]:
    dt = (time.perf_counter() - frame["t"]) * 1000.0
    if STACK and STACK[-1] is frame:
        STACK.pop()
    if STACK:
        STACK[-1]["child"] += dt
    return dt, dt - frame["child"]


ROWS: dict[str, dict] = {}


def _record(label: str, parent, dt: float, excl: float) -> None:
    row = ROWS.setdefault(label, {"calls": 0, "inclusive_ms": 0.0,
                                  "exclusive_ms": 0.0, "parents": {}})
    row["calls"] += 1
    row["inclusive_ms"] += dt
    row["exclusive_ms"] += excl
    row["parents"][str(parent)] = row["parents"].get(str(parent), 0) + 1


def _wrap_method(owner, name: str, label: str) -> bool:
    try:
        orig = getattr(owner, name)

        def wrapper(*a, **k):
            fr = _enter(label)
            try:
                return orig(*a, **k)
            finally:
                dt, excl = _exit(fr)
                _record(label, fr["parent"], dt, excl)

        setattr(owner, name, wrapper)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[wrap-skip] {label}: {type(exc).__name__}: {exc}", flush=True)
        return False


# ---------------------------------------------------------------------------
# Theme.apply 逐次拆账
# ---------------------------------------------------------------------------
def _instrument_theme(themes_mod, app) -> dict:
    state = {"calls": [], "repolish_calls": 0}
    orig_apply = themes_mod.Theme.apply
    orig_repolish = getattr(themes_mod, "_repolish_all", None)

    if orig_repolish is not None:
        def _repolish(app_):
            state["repolish_calls"] += 1
            return orig_repolish(app_)

        themes_mod._repolish_all = _repolish

    def apply(self, app_):
        # 先算「应该是什么样」，再开始计时：计时区间只包真实 apply。
        try:
            palette = themes_mod._build_palette(self.colors)
            font = themes_mod.get_app_font()
            qss = self._qss()
            cur = app_.styleSheet()
            pal_eq = bool(app_.palette() == palette)
            font_eq = bool(app_.font() == font)
            qss_same = bool(cur == qss or cur.startswith(qss))
        except Exception:  # noqa: BLE001
            qss, cur = "", ""
            pal_eq = font_eq = qss_same = False
        repolish_before = state["repolish_calls"]
        stack = traceback.extract_stack()[:-1]
        chain = [f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}"
                 for f in stack[-6:]]
        t = time.perf_counter()
        try:
            return orig_apply(self, app_)
        finally:
            dt = (time.perf_counter() - t) * 1000.0
            if pal_eq and font_eq and qss_same:
                branch = "early_return"
            elif qss_same:
                branch = "repolish"
            else:
                branch = "setStyleSheet"
            state["calls"].append({
                "ms": round(dt, 1),
                "theme": getattr(self, "name", "?"),
                "qss_len": len(qss), "qss_sha": _sha(qss),
                "current_len": len(cur), "current_sha": _sha(cur),
                "palette_equal": pal_eq, "font_equal": font_eq,
                "qss_same": qss_same, "branch": branch,
                "repolish_delta": state["repolish_calls"] - repolish_before,
                "caller_chain": chain})

    themes_mod.Theme.apply = apply
    return state


# ---------------------------------------------------------------------------
# addWidget / setCentralWidget 逐次拆账 + sizeHint 身份集合
# ---------------------------------------------------------------------------
def _instrument_shell(qtw) -> dict:
    from PySide6.QtWidgets import QWidget

    state = {"active": None, "calls": [], "regions": {}, "region_ms": {},
             "slow_mh": [], "mh_total": 0}
    orig_mh = QWidget.minimumSizeHint

    def mh(self):
        reg = state["active"]
        if reg is not None:
            state["regions"].setdefault(reg, []).append(_ptr(self))
        t = time.perf_counter()
        out = orig_mh(self)
        dt = (time.perf_counter() - t) * 1000.0
        state["mh_total"] += 1
        if dt >= 5.0:
            state["slow_mh"].append({"class": type(self).__name__,
                                     "object": self.objectName()[:40],
                                     "ms": round(dt, 1),
                                     "region": reg})
            state["slow_mh"].sort(key=lambda r: -r["ms"])
            del state["slow_mh"][8:]
        if reg is not None:
            state["region_ms"][reg] = state["region_ms"].get(reg, 0.0) + dt
        return out

    QWidget.minimumSizeHint = mh

    def wrap(owner, name, label):
        orig = getattr(owner, name)

        def wrapper(self, arg, *a, **k):
            idx = len(state["calls"]) + 1
            key = f"{label}#{idx}"
            prev = state["active"]
            state["active"] = key
            state["regions"][key] = []
            t = time.perf_counter()
            try:
                return orig(self, arg, *a, **k)
            finally:
                dt = (time.perf_counter() - t) * 1000.0
                state["active"] = prev
                ptrs = state["regions"].get(key, [])
                uniq = list(dict.fromkeys(ptrs))
                state["calls"].append({
                    "seq": idx, "label": label, "ms": round(dt, 1),
                    "arg": _describe(arg),
                    "sizehint_calls": len(ptrs),
                    "sizehint_unique": len(uniq),
                    "sizehint_ms": round(state["region_ms"].get(key, 0.0), 1),
                    "sizehint_ptr_set": uniq})

        setattr(owner, name, wrapper)

    wrap(qtw.QStackedWidget, "addWidget", "QStackedWidget.addWidget")
    wrap(qtw.QMainWindow, "setCentralWidget", "QMainWindow.setCentralWidget")
    return state


# ---------------------------------------------------------------------------
# 天空空控件（只覆盖构造期真的会被调到的接口）
# ---------------------------------------------------------------------------
def _make_stub_class():
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget

    class _Any:
        def __call__(self, *a, **k):
            return None

        def connect(self, *a, **k):
            return None

        def disconnect(self, *a, **k):
            return None

        def emit(self, *a, **k):
            return None

        def get(self, *a, **k):
            return None

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return self

        def __getitem__(self, key):
            return self

        def __iter__(self):
            return iter(())

        def __bool__(self):
            return False

        def __float__(self):
            return 0.0

        def __int__(self):
            return 0

        def __str__(self):
            return ""

    class _SkyStub(QWidget):
        object_clicked = Signal(object)
        question_requested = Signal(str)
        preview_sky_requested = Signal()
        location_requested = Signal()
        night_vision_changed = Signal(bool)
        all_sky_mode_changed = Signal(bool)

        def layer_state(self):
            return {}

        def get_site_note(self):
            return ""

        def get_observer(self):
            return (None, None)

        def all_sky_active(self):
            return False

        def set_callsign(self, *a, **k):
            pass

        def set_observer(self, *a, **k):
            pass

        def set_site_note(self, *a, **k):
            pass

        def set_data_source(self, *a, **k):
            pass

        def set_antenna(self, *a, **k):
            pass

        def set_all_sky_mode(self, *a, **k):
            pass

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return _Any()

    return _SkyStub


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _run(args) -> dict:
    profile = _prepare_env()
    head = None
    if args.get("head_snapshot"):
        snap, head = _head_snapshot()
        sys.path.insert(0, snap)
    import psutil
    from PySide6.QtCore import QSettings
    from PySide6 import QtWidgets
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QApplication

    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope,
                          os.path.join(profile, "qsettings"))

    t_import = time.perf_counter()
    import layout_store as ls
    ls.reset_layout_store_singleton()
    ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
    import settings  # noqa: F401
    import themes as themes_mod
    import touch_manager  # noqa: F401
    import main_window as mw
    import_phase_ms = (time.perf_counter() - t_import) * 1000.0

    app = QApplication(sys.argv[:1])
    from themes import get_app_font
    app.setFont(get_app_font())

    # 生产入口 main.py:709-713 在建主窗之前就 app.setFont + theme.apply。
    # 复现这条顺序：否则 ctor 里第一次 Theme.apply 会替生产入口「补做」全树
    # setStyleSheet，把 main.py 里那一步的账算到 ctor 头上。
    splash_ms = None
    if args.get("splash"):
        # main.py::_show_startup_splash 在主窗构造前用 QPainter 画三行字，
        # 会先把 MiSans 的首次字体度量付掉。不模拟它，字体账就会落到工具栏
        # 或天空控件头上，利润表会把「splash 已付过的钱」算成可延后。
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPainter, QPixmap
        t_sp = time.perf_counter()
        pix = QPixmap(460, 260)
        pix.fill(QColor("#0f1419"))
        p = QPainter(pix)
        base = QFont(app.font())
        title_font = QFont(base)
        title_font.setPointSize(22)
        title_font.setBold(True)
        p.setFont(title_font)
        p.drawText(0, 84, 460, 44, Qt.AlignHCenter | Qt.AlignVCenter, "MBDSDR")
        sub_font = QFont(base)
        sub_font.setPointSize(10)
        p.setFont(sub_font)
        p.drawText(0, 132, 460, 24, Qt.AlignHCenter | Qt.AlignVCenter,
                   "AI 定义无线电 · 正在加载界面…")
        p.drawText(0, 206, 460, 20, Qt.AlignHCenter | Qt.AlignVCenter, "v0.0.0")
        p.end()
        splash_ms = round((time.perf_counter() - t_sp) * 1000.0, 1)

    pre_apply_ms = None
    if args.get("pre_apply_theme"):
        t_pa = time.perf_counter()
        themes_mod.get_theme(themes_mod.DEFAULT_THEME).apply(app)
        pre_apply_ms = round((time.perf_counter() - t_pa) * 1000.0, 1)

    theme_state = _instrument_theme(themes_mod, app)
    shell_state = _instrument_shell(QtWidgets)

    orig_create_func = mw.MainWindow.__dict__.get("_create_sky_widget")
    sky_state = {"label": "_create_sky_widget", "calls": 0,
                 "inclusive_ms": 0.0, "exclusive_ms": 0.0}
    if orig_create_func is not None:
        def sky_wrapper(self):
            fr = _enter("_create_sky_widget")
            try:
                return orig_create_func(self)
            finally:
                dt, excl = _exit(fr)
                sky_state["calls"] += 1
                sky_state["inclusive_ms"] += dt
                sky_state["exclusive_ms"] += excl

        mw.MainWindow._create_sky_widget = sky_wrapper

    if args["mode"] == "stub":
        _SkyStub = _make_stub_class()
        mw.MainWindow._create_sky_widget = lambda self: _SkyStub()

    _wrap_method(mw.MainWindow, "_build_planetarium_page",
                 "MW._build_planetarium_page")
    for _name in ("_build_tool_bar", "_build_central_widget",
                  "_build_kernel_panels", "_build_status_bar", "_build_menu_bar",
                  "_apply_restored_settings", "_build_mode_shell",
                  "_add_right_tab", "_panels_set_sdr_connected"):
        _wrap_method(mw.MainWindow, _name, f"MW.{_name}")

    load_before = _wait_low(psutil, float(args["wait_low"]))
    if load_before is None:
        return {"aborted": "240 s 内没有 python 总占比 <200% 的窗口"}

    prewarm_font_ms = None
    if args["mode"] == "prewarm":
        t_pw = time.perf_counter()
        QFontMetrics(app.font()).horizontalAdvance("Ag中")
        mono = QFont("Consolas")
        mono.setPointSizeF(14)
        QFontMetrics(mono).horizontalAdvance("123.456 MHz")
        prewarm_font_ms = round((time.perf_counter() - t_pw) * 1000.0, 1)

    t_ctor = time.perf_counter()
    window = mw.MainWindow()
    ctor_ms = (time.perf_counter() - t_ctor) * 1000.0
    widgets = len(app.allWidgets())

    default_page = {}
    try:
        lt = getattr(window, "left_tab", None)
        default_page = {
            "left_tab_count": lt.count() if lt is not None else None,
            "left_tab_current_index": lt.currentIndex() if lt is not None else None,
            "left_tab_current_is_sky": bool(
                lt is not None and lt.currentWidget() is getattr(window, "sky_view", None)),
            "left_tab_titles": [lt.tabText(i) for i in range(lt.count())]
            if lt is not None else [],
            "mode_stack_index": getattr(getattr(window, "_mode_stack", None),
                                        "currentIndex", lambda: None)(),
            "app_mode": getattr(window, "_app_mode", None),
        }
    except Exception:  # noqa: BLE001
        default_page = {"error": traceback.format_exc(limit=2)}

    # 关掉启动定时器，再出首帧（避免 100/200/400 ms 任务混进读数）
    for attr in ("_startup_timers",):
        for t in getattr(window, attr, []) or []:
            try:
                t.stop()
            except Exception:  # noqa: BLE001
                pass
    for attr in ("_sky_update_timer", "_gnss_timer", "_utc_timer"):
        try:
            getattr(window, attr).stop()
        except Exception:  # noqa: BLE001
            pass

    t_show = time.perf_counter()
    window.show()
    show_sync_ms = (time.perf_counter() - t_show) * 1000.0
    t_tick = time.perf_counter()
    app.processEvents()
    paint_ms = (time.perf_counter() - t_tick) * 1000.0
    definition_c_ms = (time.perf_counter() - T0) * 1000.0

    deferred = {}
    if args["mode"] == "stub" and orig_create_func is not None:
        bound = types.MethodType(orig_create_func, window)
        t_dc = time.perf_counter()
        real_sky = bound()
        deferred["create_ms"] = round((time.perf_counter() - t_dc) * 1000.0, 1)
        t_di = time.perf_counter()
        try:
            lt = window.left_tab
            idx = lt.indexOf(window.sky_view)
            lt.removeTab(idx)
            lt.insertTab(idx, real_sky, "星象仪模式")
            lt.tabBar().setTabData(idx, "rf_sky")
            lt.setCurrentIndex(idx)
            real_sky.show()
            app.processEvents()
        except Exception:  # noqa: BLE001
            deferred["insert_error"] = traceback.format_exc(limit=3)
        deferred["insert_and_show_ms"] = round(
            (time.perf_counter() - t_di) * 1000.0, 1)
        deferred["total_ms"] = round(
            deferred["create_ms"] + deferred["insert_and_show_ms"], 1)

    sky_visible_after_show = None
    try:
        sv = getattr(window, "sky_view", None)
        sky_visible_after_show = bool(sv is not None and sv.isVisible())
    except Exception:  # noqa: BLE001
        pass

    load_after = _load(psutil, 2.0)
    font = app.font()
    fm = QFontMetrics(font)
    ruler = {"qt_platform": os.environ.get("QT_QPA_PLATFORM"),
             "font": font.family(), "pt": font.pointSizeF(),
             "line_h": fm.height(),
             "dpr": app.primaryScreen().devicePixelRatio()}

    # addWidget(root) 与 setCentralWidget(stack) 的 sizeHint 身份重叠
    shell_calls = shell_state["calls"]
    overlap = None
    # addWidget 的第一次就是 root（_build_mode_shell 的第一句）；
    # setCentralWidget 的最后一次是 stack（同一函数的最后一句）。
    add_calls = [c for c in shell_calls
                 if c["label"] == "QStackedWidget.addWidget"]
    set_calls = [c for c in shell_calls
                 if c["label"] == "QMainWindow.setCentralWidget"]
    if add_calls and set_calls:
        a = add_calls[0]
        b = set_calls[-1]
        sa, sb = set(a["sizehint_ptr_set"]), set(b["sizehint_ptr_set"])
        overlap = {"addWidget_seq": a["seq"], "setCentralWidget_seq": b["seq"],
                   "add_sizehint_unique": len(sa), "set_sizehint_unique": len(sb),
                   "shared": len(sa & sb), "only_add": len(sa - sb),
                   "only_set": len(sb - sa)}

    out = {"mode": args["mode"],
           "head": head, "head_snapshot": bool(args.get("head_snapshot")),
           "load_before": load_before, "load_after": load_after,
           "import_phase_ms": round(import_phase_ms, 1),
           "prewarm_font_ms": prewarm_font_ms,
           "pre_apply_ms": pre_apply_ms,
           "pre_apply_theme": bool(args.get("pre_apply_theme")),
           "splash_ms": splash_ms,
           "splash": bool(args.get("splash")),
           "ctor_ms": round(ctor_ms, 1),
           "definition_c_ms": round(definition_c_ms, 1),
           "show_sync_ms": round(show_sync_ms, 1),
           "paint_ms": round(paint_ms, 1),
           "widgets": widgets, "ruler": ruler,
           "sky": {"calls": sky_state["calls"],
                   "inclusive_ms": round(sky_state["inclusive_ms"], 1),
                   "exclusive_ms": round(sky_state["exclusive_ms"], 1)},
           "planetarium_page": (
               {"calls": ROWS["MW._build_planetarium_page"]["calls"],
                "inclusive_ms": round(
                    ROWS["MW._build_planetarium_page"]["inclusive_ms"], 1),
                "exclusive_ms": round(
                    ROWS["MW._build_planetarium_page"]["exclusive_ms"], 1)}
               if "MW._build_planetarium_page" in ROWS else None),
           "theme_calls": theme_state["calls"],
           "shell_calls": [{k: v for k, v in c.items()
                            if k != "sizehint_ptr_set"} for c in shell_calls],
           "shell_overlap": overlap,
           "default_page": default_page,
           "sky_visible_after_show": sky_visible_after_show,
           "deferred": deferred or None,
           "minimum_size_hint_calls": shell_state["mh_total"],
           "slow_minimum_size_hints": shell_state["slow_mh"],
           "method_rows": {
               k: {"calls": v["calls"],
                   "inclusive_ms": round(v["inclusive_ms"], 1),
                   "exclusive_ms": round(v["exclusive_ms"], 1)}
               for k, v in ROWS.items()}}

    try:
        window.close()
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------------------
def _once(args) -> int:
    try:
        data = _run(args)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        data = {"aborted": f"{type(exc).__name__}: {exc}"}
    with open(args["out"], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return 0


def _parse(argv: list[str]) -> dict:
    def val(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    return {"mode": val("--mode", "probe"),
            "out": val("--out"),
            "runs": int(val("--runs", "3")),
            "wait_low": float(val("--wait-low", "240")),
            "pre_apply_theme": "--pre-apply-theme" in argv,
            "head_snapshot": "--head-snapshot" in argv,
            "splash": "--splash" in argv,
            "once": "--once" in argv}


def _median(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(vals), 1) if vals else None


def _median_of(rows, key):
    return _median([r.get(key) for r in rows])


def _suffix(args) -> str:
    suffix = ("-head" if args.get("head_snapshot") else "")
    if args.get("pre_apply_theme") and args.get("splash"):
        suffix += "-prod"
    elif args.get("pre_apply_theme"):
        suffix += "-preapply"
    elif args.get("splash"):
        suffix += "-splash"
    return suffix


def _spawn_once(mode: str, idx: int, args) -> dict:
    out = os.path.join(TMP, f"deferprofit_{mode}_{os.getpid()}_{idx}.json")
    cmd = [sys.executable, os.path.abspath(__file__), "--once",
           "--mode", mode, "--out", out,
           "--wait-low", str(args["wait_low"])]
    for flag, key in (("--pre-apply-theme", "pre_apply_theme"),
                      ("--head-snapshot", "head_snapshot"),
                      ("--splash", "splash")):
        if args.get(key):
            cmd.append(flag)
    print(f"[run {idx + 1}] mode={mode}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-2000:], flush=True)
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def _total_ms(r: dict) -> float:
    return round(r["ctor_ms"] + r.get("show_sync_ms", 0.0)
                 + r.get("paint_ms", 0.0), 1)


def _paired(args) -> int:
    """交替跑 probe / stub，按相邻对求差——压住机器负载漂移。"""
    pairs = []
    for i in range(args["runs"]):
        order = ["probe", "stub"] if i % 2 == 0 else ["stub", "probe"]
        got = {m: _spawn_once(m, i, args) for m in order}
        p, s = got["probe"], got["stub"]
        if "ctor_ms" in p and "ctor_ms" in s:
            pairs.append({
                "order": order,
                "probe_ctor": p["ctor_ms"], "stub_ctor": s["ctor_ms"],
                "delta_ctor_ms": round(p["ctor_ms"] - s["ctor_ms"], 1),
                "probe_total": _total_ms(p), "stub_total": _total_ms(s),
                "delta_total_ms": round(_total_ms(p) - _total_ms(s), 1),
                "probe_sky_exclusive": p["sky"]["exclusive_ms"],
                "head": p.get("head"),
                "loads": [p["load_before"]["python_total_cpu_pct"],
                          s["load_before"]["python_total_cpu_pct"]]})
    d_ctor = [x["delta_ctor_ms"] for x in pairs]
    d_total = [x["delta_total_ms"] for x in pairs]
    summary = {"mode": "paired", "valid_pairs": len(pairs),
               "head": (pairs[0].get("head") if pairs else None),
               "head_snapshot": bool(args.get("head_snapshot")),
               "splash": bool(args.get("splash")),
               "pre_apply_theme": bool(args.get("pre_apply_theme")),
               "pairs": pairs,
               "delta_ctor_all": d_ctor, "delta_ctor_median_ms": _median(d_ctor),
               "delta_total_all": d_total,
               "delta_total_median_ms": _median(d_total),
               "probe_sky_exclusive_all": [x["probe_sky_exclusive"]
                                           for x in pairs],
               "probe_sky_exclusive_median_ms": _median(
                   [x["probe_sky_exclusive"] for x in pairs])}
    out_path = os.path.join(EVID,
                            f"deferral-profit-paired{_suffix(args)}.json")
    os.makedirs(EVID, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


def _parent(args) -> int:
    if args["mode"] == "paired":
        return _paired(args)
    runs = [_spawn_once(args["mode"], i, args)
            for i in range(args["runs"])]

    ok = [r for r in runs if "aborted" not in r]
    summary = {"mode": args["mode"], "runs": len(runs),
               "valid_runs": len(ok),
               "head": (ok[0].get("head") if ok else None),
               "head_snapshot": bool(args.get("head_snapshot")),
               "aborted": [r.get("aborted") for r in runs if "aborted" in r]}
    if ok:
        summary.update({
            "ctor_ms": [r["ctor_ms"] for r in ok],
            "ctor_median_ms": _median_of(ok, "ctor_ms"),
            "definition_c_median_ms": _median_of(ok, "definition_c_ms"),
            "show_sync_median_ms": _median_of(ok, "show_sync_ms"),
            "paint_median_ms": _median_of(ok, "paint_ms"),
            "sky_inclusive_ms": [r["sky"]["inclusive_ms"] for r in ok],
            "sky_exclusive_ms": [r["sky"]["exclusive_ms"] for r in ok],
            "sky_exclusive_median_ms": _median(
                [r["sky"]["exclusive_ms"] for r in ok]),
            "theme_call_medians": [],
            "default_page": ok[0].get("default_page"),
            "sky_visible_after_show": ok[0].get("sky_visible_after_show"),
            "shell_overlap": ok[0].get("shell_overlap"),
            "deferred": [r.get("deferred") for r in ok],
            "prewarm_font_ms": [r.get("prewarm_font_ms") for r in ok],
            "pre_apply_ms": [r.get("pre_apply_ms") for r in ok],
            "pre_apply_theme": bool(args.get("pre_apply_theme")),
            "splash_ms": [r.get("splash_ms") for r in ok],
            "splash": bool(args.get("splash")),
            "minimum_size_hint_calls": [r.get("minimum_size_hint_calls")
                                        for r in ok],
            "slow_minimum_size_hints": ok[0].get("slow_minimum_size_hints"),
            "method_rows": ok[0].get("method_rows"),
            "theme_calls_sample": ok[0].get("theme_calls"),
        })
        max_calls = max((len(r.get("theme_calls") or []) for r in ok), default=0)
        for idx in range(max_calls):
            col = [r["theme_calls"][idx] for r in ok
                   if len(r.get("theme_calls") or []) > idx]
            summary["theme_call_medians"].append({
                "call": idx + 1,
                "ms": _median([c["ms"] for c in col]),
                "ms_all": [c["ms"] for c in col],
                "branches": [c["branch"] for c in col],
                "qss_same": [c["qss_same"] for c in col],
                "palette_equal": [c["palette_equal"] for c in col],
                "font_equal": [c["font_equal"] for c in col],
                "qss_sha": [c["qss_sha"] for c in col],
                "caller_chain": col[0]["caller_chain"] if col else None})
        max_shell = max((len(r.get("shell_calls") or []) for r in ok), default=0)
        summary["shell_call_medians"] = []
        for idx in range(max_shell):
            col = [r["shell_calls"][idx] for r in ok
                   if len(r.get("shell_calls") or []) > idx]
            summary["shell_call_medians"].append({
                "call": idx + 1,
                "label": col[0]["label"],
                "ms": _median([c["ms"] for c in col]),
                "ms_all": [c["ms"] for c in col],
                "arg": col[0]["arg"],
                "sizehint_calls": _median([c["sizehint_calls"] for c in col]),
                "sizehint_ms": _median([c["sizehint_ms"] for c in col]),
                "sizehint_unique": [c["sizehint_unique"] for c in col]})
    suffix = ("-head" if args.get("head_snapshot") else "")
    if args.get("pre_apply_theme") and args.get("splash"):
        suffix += "-prod"
    elif args.get("pre_apply_theme"):
        suffix += "-preapply"
    elif args.get("splash"):
        suffix += "-splash"
    out_path = os.path.join(
        EVID, f"deferral-profit-{args['mode']}{suffix}.json")
    os.makedirs(EVID, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    args = _parse(sys.argv[1:])
    if not args["out"]:
        return _parent(args)
    return _once(args)


if __name__ == "__main__":
    sys.exit(main())

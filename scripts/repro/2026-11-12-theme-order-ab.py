#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""重排实验：把生产入口的 `theme.apply(app)` 从「建树前」移到「ctor 后、show 前」。

两臂复刻 `desktop/main.py::main()` 的真实顺序（splash → smoke → deps →
setFont → 主题 → 守卫 → l10n → import → MainWindow → 设置钩子 → 图标 →
fit_scale → show → splash.finish），只改主题那一步的位置：

  before  生产现状：ctor 之前 `theme.apply(app)`
  after   ctor 内所有主题应用全部挂起，ctor 返回后一次性应用，再 show

量的东西：
  * 定义 C 端到端 = 进程入口(t0) → 主窗首个 Paint 派发完毕（不是只量 ctor）
  * ctor_ms
  * 首帧前那一次批量 polish（after 臂的 apply 耗时）+ 全树 relayout（activate）
  * 反证①：首帧时 app 样式表非空；grab() 首帧取样色 vs 主题期望色；
          QPushButton/QComboBox 的 sizeHint 在 apply 后、首帧后是否又跳
  * 反证②：若总计没省，如实报「省了 ctor、亏了 show」

用法::

    python scripts/repro/2026-11-12-theme-order-ab.py --runs 3
"""
from __future__ import annotations

import time

T0 = time.perf_counter()          # 进程入口

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
ARMS = ("before", "after")


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
    profile = tempfile.mkdtemp(prefix="theme-order-")
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
    snap = os.path.join(tempfile.gettempdir(), "kilo", f"themeorder_head_{head}")
    os.makedirs(snap, exist_ok=True)
    for name in ("main_window.py", "module_panel.py", "device_panel.py",
                 "bookmark_panel.py"):
        out = subprocess.run(["git", "-C", ROOT, "show", f"HEAD:desktop/{name}"],
                             capture_output=True).stdout
        with open(os.path.join(snap, name), "wb") as f:
            f.write(out)
    return snap, head


def _hints(win, Pbtn, Pcombo) -> dict:
    from PySide6.QtCore import QSize

    def snap(widgets):
        rows = []
        for w in widgets:
            s: QSize = w.sizeHint()
            rows.append({"cls": type(w).__name__, "w": s.width(), "h": s.height()})
        return rows

    return {"buttons": snap(win.findChildren(Pbtn)[:4]),
            "combos": snap(win.findChildren(Pcombo)[:4]),
            "min_width": int(win.minimumSizeHint().width())}


def _grab_colors(win, Pbtn, Pcombo) -> dict:
    import hashlib

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QImage, QPalette

    img = win.grab().toImage().convertToFormat(QImage.Format_RGB32)
    raw = bytes(img.constBits())
    out = {"size": [img.width(), img.height()],
           "sha1": hashlib.sha1(raw).hexdigest()[:16],
           "expected_window": win.palette().color(QPalette.Window).name()}
    thumb = img.scaled(8, 8, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out["thumb_8x8"] = [thumb.pixelColor(x, y).name()
                        for y in range(thumb.height())
                        for x in range(thumb.width())]
    try:
        c = win.rect().center()
        out["win_center"] = img.pixelColor(c.x(), c.y()).name()
    except Exception:  # noqa: BLE001
        out["win_center"] = None
    for tag, cls in (("btn", Pbtn), ("combo", Pcombo)):
        ws = win.findChildren(cls)
        if not ws:
            out[f"{tag}_center"] = None
            continue
        w = ws[0]
        try:
            p: QPoint = w.mapTo(win, w.rect().center())
            out[f"{tag}_center"] = img.pixelColor(p.x(), p.y()).name()
            out[f"{tag}_expected"] = w.palette().color(QPalette.Button).name()
        except Exception:  # noqa: BLE001
            out[f"{tag}_center"] = None
    return out


def _one(arm: str) -> int:
    snap = os.environ.get("MBDSDR_HEAD_SNAP")
    if snap:
        sys.path.insert(0, snap)
    _prepare_env()

    import psutil
    from PySide6.QtCore import QEvent, QObject, QSettings, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (QApplication, QComboBox, QPushButton,
                                   QSplashScreen)

    profile = os.environ["HOME"]
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope,
                          os.path.join(profile, "qsettings"))
    import layout_store as ls
    ls.reset_layout_store_singleton()
    ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001

    import main as appmain         # 会把 desktop 插到 sys.path[0]
    if snap:
        sys.path.insert(0, snap)   # 再钉一次，保证 main_window 来自快照
    import themes

    M = {"t": {}, "theme_calls": [], "defer": arm == "after", "theme_obj": None,
         "win": None}
    now = time.perf_counter

    class _Splash(QSplashScreen):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.paint_ms = None

        def paintEvent(self, ev):  # noqa: N802
            if self.paint_ms is not None:
                return super().paintEvent(ev)
            t = now()
            super().paintEvent(ev)
            self.paint_ms = round((now() - t) * 1000.0, 1)
            M["t"].setdefault("splash_paint_abs", now())

    appmain.QSplashScreen = _Splash

    orig_apply = themes.Theme.apply

    def apply_wrap(self, app_ref):                      # noqa: ANN001
        if M["defer"]:
            M["theme_obj"] = self
            M["theme_calls"].append({"t_ms": round((now() - T0) * 1000.0, 1),
                                     "ms": 0.0, "skipped": True})
            return None
        before = len(app_ref.styleSheet())
        t = now()
        try:
            return orig_apply(self, app_ref)
        finally:
            after = len(app_ref.styleSheet())
            M["theme_calls"].append({
                "t_ms": round((t - T0) * 1000.0, 1),
                "ms": round((now() - t) * 1000.0, 1),
                "skipped": False,
                "qss_len_before": before,
                "qss_len_after": after,
                "branch": "setStyleSheet" if after != before else "repolish_or_early",
            })

    themes.Theme.apply = apply_wrap

    class _App(QApplication):
        def notify(self, receiver, ev):                 # noqa: N802
            if (ev.type() == QEvent.Paint and receiver is M["win"]
                    and "win_paint_abs" not in M["t"]):
                M["t"]["win_paint_abs"] = now()
                M["style_len_at_first_paint"] = len(self.styleSheet())
            return super().notify(receiver, ev)

    app = _App(sys.argv)
    M["t"]["qapp_abs"] = now()
    M["deferred_qss_len"] = []
    scope = os.environ.get("MBDSDR_THEME_SCOPE", "all")

    if scope == "all":
        orig_ss = QApplication.setStyleSheet

        def ss_wrap(self, qss):                         # noqa: ANN001
            if M["defer"]:
                M["deferred_qss_len"].append(len(qss or ""))
                M["theme_calls"].append({"t_ms": round((now() - T0) * 1000.0, 1),
                                         "ms": 0.0, "skipped": True,
                                         "what": "app.setStyleSheet"})
                return None
            t = now()
            try:
                return orig_ss(self, qss)
            finally:
                M["theme_calls"].append({"t_ms": round((t - T0) * 1000.0, 1),
                                         "ms": round((now() - t) * 1000.0, 1),
                                         "skipped": False, "what": "app.setStyleSheet",
                                         "qss_len_set": len(qss or "")})

        QApplication.setStyleSheet = ss_wrap
    M["scope"] = scope
    app.setApplicationName("MBDSDR")
    app.setApplicationDisplayName("MBDSDR - AI 定义无线电")
    app.setOrganizationName("MBDSDR Project")

    splash = appmain._show_startup_splash(app)
    M["t"]["after_splash_abs"] = now()
    appmain._run_import_smoke()
    M["t"]["after_smoke_abs"] = now()
    appmain._startup_dependency_problems()
    M["t"]["after_deps_abs"] = now()
    app.setFont(themes.get_app_font())
    M["t"]["after_font_abs"] = now()

    theme = themes.get_theme(themes.DEFAULT_THEME)
    if arm == "before":
        t = now()
        theme.apply(app)
        M["t"]["theme_before_ctor_ms"] = round((now() - t) * 1000.0, 1)
    else:
        M["t"]["theme_before_ctor_ms"] = 0.0

    appmain._install_combo_wheel_guard(app)
    try:
        import l10n as _l10n
        from logging_setup import configure_from_settings
        from settings import DesktopSettings as _DesktopSettings
        _boot = _DesktopSettings.load()
        configure_from_settings(_boot)
        _l10n.install_translator(app, _boot.get("ui_language"))
    except Exception as exc:  # noqa: BLE001
        print(f"[theme-order] l10n 跳过: {exc}", file=sys.stderr)
    M["t"]["after_l10n_abs"] = now()

    t = now()
    MainWindow = appmain._import_main_window(splash)
    M["t"]["import_mw_ms"] = round((now() - t) * 1000.0, 1)
    M["mw_file"] = getattr(sys.modules.get("main_window"), "__file__", "")

    t = now()
    win = MainWindow()
    M["t"]["ctor_ms"] = round((now() - t) * 1000.0, 1)
    M["win"] = win
    win.app = app
    hints_ctor_end = _hints(win, QPushButton, QComboBox)

    apply_after_ms = None
    hints_after_apply = None
    if arm == "after":
        M["defer"] = False
        t = now()
        (M["theme_obj"] or theme).apply(app)
        apply_after_ms = round((now() - t) * 1000.0, 1)
        hints_after_apply = _hints(win, QPushButton, QComboBox)

    t = now()
    win.ensurePolished()
    lay = win.layout()
    if lay is not None:
        lay.activate()
    activate_ms = round((now() - t) * 1000.0, 1)

    icon_path = os.path.join(os.path.dirname(os.path.abspath(appmain.__file__)),
                             "assets", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
        win.setWindowIcon(QIcon(icon_path))

    from themes import fit_scale_to_screen
    t = now()
    try:
        fit_scale_to_screen(win, app, theme)
    except Exception as exc:  # noqa: BLE001
        print(f"[theme-order] fit_scale 失败: {exc}", file=sys.stderr)
    fit_ms = round((now() - t) * 1000.0, 1)

    t = now()
    win.show()
    if splash is not None:
        try:
            splash.finish(win)
        except Exception:  # noqa: BLE001
            pass
    t_show = now()
    deadline = t_show + 15.0
    while "win_paint_abs" not in M["t"] and now() < deadline:
        app.processEvents()
    t_paint = M["t"].get("win_paint_abs") or now()

    hints_after_paint = _hints(win, QPushButton, QComboBox)
    try:
        colors = _grab_colors(win, QPushButton, QComboBox)
    except Exception as exc:  # noqa: BLE001 - 检查失败不丢整条样本
        colors = {"error": f"{type(exc).__name__}: {exc}"}
    load_after = _load(psutil)

    result = {
        "arm": arm,
        "scope": M.get("scope"),
        "head": os.environ.get("MBDSDR_HEAD", ""),
        "mw_file": M["mw_file"],
        "t_to_qapp_ms": round((M["t"]["qapp_abs"] - T0) * 1000.0, 1),
        "splash_paint_ms": (round((M["t"]["splash_paint_abs"] - T0) * 1000.0, 1)
                            if "splash_paint_abs" in M["t"] else None),
        "smoke_ms": round((M["t"]["after_smoke_abs"]
                           - M["t"]["after_splash_abs"]) * 1000.0, 1),
        "deps_ms": round((M["t"]["after_deps_abs"]
                          - M["t"]["after_smoke_abs"]) * 1000.0, 1),
        "theme_before_ctor_ms": M["t"]["theme_before_ctor_ms"],
        "import_mw_ms": M["t"]["import_mw_ms"],
        "ctor_ms": M["t"]["ctor_ms"],
        "apply_after_ctor_ms": apply_after_ms,
        "activate_ms": activate_ms,
        "fit_scale_ms": fit_ms,
        "show_to_paint_ms": round((t_paint - t_show) * 1000.0, 1),
        "def_c_entry_to_paint_ms": round((t_paint - T0) * 1000.0, 1),
        "theme_calls": M["theme_calls"],
        "deferred_qss_len": M.get("deferred_qss_len"),
        "style_len_at_first_paint": M.get("style_len_at_first_paint"),
        "hints_ctor_end": hints_ctor_end,
        "hints_after_apply": hints_after_apply,
        "hints_after_paint": hints_after_paint,
        "colors": colors,
        "paints": 0 if "win_paint_abs" not in M["t"] else 1,
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


def _gui_checks(got: dict) -> dict:
    """反证①：after 臂 apply 后到首帧后 sizeHint 不再跳；两臂首帧外观一致。"""
    def rows(h):
        if not h:
            return []
        return h["buttons"] + h["combos"]

    jumps, identical, shas, style_lens = [], [], [], []
    before_rows = [rows(s["hints_ctor_end"]) for s in got["before"]]
    for i, s in enumerate(got["after"]):
        a, p = rows(s["hints_after_apply"]), rows(s["hints_after_paint"])
        jumps.append(sum(1 for x, y in zip(a, p) if (x["w"], x["h"]) != (y["w"], y["h"])))
        if i < len(before_rows):
            b = before_rows[i]
            identical.append(sum(1 for x, y in zip(a, b)
                                 if (x["w"], x["h"]) != (y["w"], y["h"])))
    for s in got["before"] + got["after"]:
        c = s.get("colors") or {}
        shas.append((s["arm"], c.get("sha1"), c.get("error")))
        style_lens.append((s["arm"], s["style_len_at_first_paint"]))
    thumbs = {a: [ (s.get("colors") or {}).get("thumb_8x8") for s in got[a] ] for a in ARMS}
    diffs = []
    for i, t_after in enumerate(thumbs["after"]):
        if i >= len(thumbs["before"]) or not t_after or not thumbs["before"][i]:
            continue
        t_before = thumbs["before"][i]
        worst = 0
        for ca, cb in zip(t_after, t_before):
            for k in (1, 3, 5):
                worst = max(worst, abs(int(ca[k:k + 2], 16) - int(cb[k:k + 2], 16)))
        diffs.append(worst)
    return {
        "after_sizehint_jump_after_first_paint": jumps,
        "after_apply_vs_before_ctor_sizehint_diff": identical,
        "grab_thumb_max_channel_diff_before_vs_after": diffs,
        "grab_sha1": shas,
        "style_len_at_first_paint": style_lens,
        "note": "jump=0 表示首帧后尺寸不再变；diff=0 表示 after 臂 apply 后的尺寸与 before 臂构造完的尺寸一致；缩略图最大通道差 ≤2 视为外观一致。",
    }


def _driver(runs: int, scope: str = "all") -> int:
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
            env = dict(os.environ, MBDSDR_HEAD_SNAP=snap, MBDSDR_HEAD=head,
                       MBDSDR_THEME_SCOPE=scope)
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--one", "--arm", arm],
                capture_output=True, text=True, timeout=600, env=env)
            payload = None
            for line in proc.stdout.splitlines():
                if line.startswith("RESULT "):
                    payload = json.loads(line[len("RESULT "):])
            if payload is None:
                print(f"[{arm} r{r}] 无结果 rc={proc.returncode}\n"
                      f"{proc.stdout[-800:]}\n{proc.stderr[-1200:]}", flush=True)
                continue
            payload["load_gate_before_python_pct"] = gate["python_total_cpu_pct"]
            got[arm].append(payload)
            print(f"[{arm} r{r}] ctor={payload['ctor_ms']} "
                  f"apply_after={payload['apply_after_ctor_ms']} "
                  f"show_paint={payload['show_to_paint_ms']} "
                  f"defC={payload['def_c_entry_to_paint_ms']}", flush=True)

    def stat(arm, key):
        v = [p[key] for p in got[arm] if p.get(key) is not None]
        if not v:
            return None
        return {"runs": v, "median": round(statistics.median(v), 1),
                "min": round(min(v), 1), "max": round(max(v), 1)}

    def delta(key):
        a, b = stat("after", key), stat("before", key)
        if not a or not b:
            return None
        return round(a["median"] - b["median"], 1)

    out = {
        "kind": "theme.apply 位置 AB：建树前（生产现状）vs ctor 后 show 前",
        "date": "2026-11-12",
        "scope": scope,
        "scope_note": ("all = 连 ctor 内 main_window 自己的 app.setStyleSheet 也一起推后"
                       if scope == "all" else
                       "entry = 只搬入口那一次 Theme.apply，ctor 内 QSS 应用保持原状"),
        "head": head,
        "snapshot": snap,
        "definition": "定义 C 端到端 = 进程入口(T0) → 主窗首个 Paint 派发完毕；两臂只在 theme.apply 的位置上不同",
        "arms": {
            arm: {
                "n": len(got[arm]),
                "ctor_ms": stat(arm, "ctor_ms"),
                "apply_after_ctor_ms": stat(arm, "apply_after_ctor_ms"),
                "activate_ms": stat(arm, "activate_ms"),
                "theme_before_ctor_ms": stat(arm, "theme_before_ctor_ms"),
                "show_to_paint_ms": stat(arm, "show_to_paint_ms"),
                "def_c_entry_to_paint_ms": stat(arm, "def_c_entry_to_paint_ms"),
                "splash_paint_ms": stat(arm, "splash_paint_ms"),
                "samples": got[arm],
            } for arm in ARMS
        },
        "delta_after_minus_before_ms": {
            "ctor": delta("ctor_ms"),
            "show_to_paint": delta("show_to_paint_ms"),
            "apply_after_ctor": delta("apply_after_ctor_ms"),
            "activate": delta("activate_ms"),
            "def_c_entry_to_paint": delta("def_c_entry_to_paint_ms"),
        },
        "load_rule": "规则 10：每个子 run 前父进程采 2 s 低负载门（<200%），子进程跑完再采一次。",
        "gui_checks": _gui_checks(got),
    }
    os.makedirs(EVID, exist_ok=True)
    path = os.path.join(EVID, "theme-order-ab.json" if scope == "all"
                        else "theme-order-ab-entry-only.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[saved] {path}")
    print(json.dumps(out["delta_after_minus_before_ms"], ensure_ascii=False))
    for arm in ARMS:
        a = out["arms"][arm]
        print(arm, "ctor", a["ctor_ms"], "defC", a["def_c_entry_to_paint_ms"],
              "show_paint", a["show_to_paint_ms"],
              "apply_after", a["apply_after_ctor_ms"])
    return 0


def main() -> int:
    runs = 3
    arm = "before"
    scope = "all"
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--runs" and i + 1 < len(argv):
            runs = int(argv[i + 1])
        elif a == "--arm" and i + 1 < len(argv):
            arm = argv[i + 1]
        elif a == "--scope" and i + 1 < len(argv):
            scope = argv[i + 1]
    if "--one" in argv:
        return _one(arm)
    return _driver(runs, scope)


if __name__ == "__main__":
    sys.exit(main())

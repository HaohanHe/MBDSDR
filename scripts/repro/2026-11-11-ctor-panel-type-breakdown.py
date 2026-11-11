"""构造期控件构建分解（面板维度 / 控件类型维度），只在低负载窗口测。

两种模式必须分开跑，否则插桩互相污染：
  --mode panels  只给面板类打桩，顺带记录每个面板新增的控件数与 _add_right_tab 开销
  --mode types   只给控件类/自绘控件打桩，只记耗时（**不**在每个 __init__ 里数
                 app.allWidgets()——那是 O(N²)，实测把构造期从 1.2 s 抬到 7 s）

要求（2026-11-11 用户口径）：
  - python 总 CPU 占比 < 200% 才开测；前后各采一次负载并记进结果；
  - 面板维度 top10：累计 ms + 控件数；
  - 控件类型维度 top10：累计 ms + 控件数。

用法：
    python scripts/repro/2026-11-11-ctor-panel-type-breakdown.py --mode panels --wait-low 900
    python scripts/repro/2026-11-11-ctor-panel-type-breakdown.py --mode types  --wait-low 900
结果追加进 docs/perf-evidence/2026-11-11/ctor_panel_type_breakdown_<mode>.json。
"""
from __future__ import annotations

import importlib
import json
import os
import statistics
import sys
import tempfile
import time

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVID = os.path.join(ROOT, "docs", "perf-evidence", "2026-11-11")
LOW_LOAD_PCT = 200.0

PANEL_CLASSES = [
    ("AIPanel", "ai_panel", "AIPanel"),
    ("ControlPanel", "control_panel", "ControlPanel"),
    ("RightColumn", "right_column", "RightColumn"),
    ("SpectrumPanel", "spectrum_widget", "SpectrumPanel"),
    ("ScannerPanel", "scanner_panel", "ScannerPanel"),
    ("ModulationPanel", "modulation_panel", "ModulationPanel"),
    ("BookmarkPanel", "bookmark_panel", "BookmarkPanel"),
    ("ServiceSettingsPanel", "settings_panel", "ServiceSettingsPanel"),
    ("SettingsPanel", "settings_panel", "SettingsPanel"),
    ("VfoPanel", "vfo_panel", "VfoPanel"),
    ("AnrPanel", "anr_panel", "AnrPanel"),
    ("ConstellationPanel", "constellation_panel", "ConstellationPanel"),
    ("DevicePanel", "device_panel", "DevicePanel"),
    ("GainPanel", "gain_panel", "GainPanel"),
    ("AdsbPanel", "adsb_panel", "AdsbPanel"),
    ("AprsPanel", "aprs_panel", "AprsPanel"),
    ("SatelliteImagePanel", "satellite_image_panel", "SatelliteImagePanel"),
    ("AdsbMapPanel", "adsb_map_panel", "AdsbMapPanel"),
    ("CapabilityDrawer", "capability_drawer", "CapabilityDrawer"),
]

WIDGET_TYPES = [
    ("QTableWidget", "QTableWidget"),
    ("QComboBox", "QComboBox"),
    ("QGroupBox", "QGroupBox"),
    ("QTabWidget", "QTabWidget"),
    ("QScrollArea", "QScrollArea"),
    ("QStackedWidget", "QStackedWidget"),
    ("QListWidget", "QListWidget"),
    ("QTreeWidget", "QTreeWidget"),
    ("QCheckBox", "QCheckBox"),
    ("QPushButton", "QPushButton"),
    ("QToolButton", "QToolButton"),
    ("QLabel", "QLabel"),
    ("QSlider", "QSlider"),
    ("QSpinBox", "QSpinBox"),
    ("QLineEdit", "QLineEdit"),
    ("QProgressBar", "QProgressBar"),
    ("QFrame", "QFrame"),
]

CUSTOM_PAINTED = [
    ("adsb_panel", "_AdsbMapCanvas"),
    ("constellation_panel", "_GnssSkyView"),
    ("constellation_panel", "_ConstellationView"),
    ("freq_display", "FreqDisplay"),
    ("gain_panel", "_StageSlider"),
    ("module_panel", "FlowCanvas"),
    ("new_spacetime_panel", "FreqOrbitChart"),
    ("osm_map", "OsmMapView"),
    ("rf_sky_view", "RFSkyView"),
    ("spectrum_widget", "SpectrumCurveWidget"),
    ("spectrum_widget", "WaterfallWidget"),
    ("right_column", "_SectionHeader"),
]


def _prepare_env() -> str:
    os.makedirs(os.path.join(tempfile.gettempdir(), "mbdsdr-repro"),
                exist_ok=True)
    profile = tempfile.mkdtemp(prefix="ctor-split-")
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
    time.sleep(sec)
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
    return {"python_proc_count": len(rows),
            "python_total_cpu_pct": round(sum(r["cpu_pct"] for r in rows), 1),
            "python_top": rows[:5], "cpu_count": psutil.cpu_count()}


def _wait_low(psutil, timeout_s: float) -> dict | None:
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


def _patch(cls, sink, label, app=None, count_widgets=False):
    orig = cls.__init__

    def patched(self, *a, **k):
        n0 = len(app.allWidgets()) if count_widgets else 0
        t = time.perf_counter()
        try:
            orig(self, *a, **k)
        finally:
            ms = (time.perf_counter() - t) * 1000.0
            rec = {"ms": ms}
            if count_widgets:
                rec["widgets"] = len(app.allWidgets()) - n0
            sink.setdefault(label, []).append(rec)

    cls.__init__ = patched
    return orig


def _rows(sink, top=10):
    out = []
    for label, items in sink.items():
        ms = [r["ms"] for r in items]
        row = {"name": label, "count": len(items),
               "total_ms": round(sum(ms), 1),
               "p50_ms": round(statistics.median(ms), 2),
               "max_ms": round(max(ms), 2)}
        if "widgets" in items[0]:
            row["widgets"] = sum(r["widgets"] for r in items)
        out.append(row)
    out.sort(key=lambda r: -r["total_ms"])
    return out[:top]


def _main() -> int:
    args = sys.argv[1:]
    mode = args[args.index("--mode") + 1] if "--mode" in args else "panels"
    wait_s = float(args[args.index("--wait-low") + 1]) if "--wait-low" in args else 600.0
    profile = _prepare_env()
    out_path = os.path.join(EVID, f"ctor_panel_type_breakdown_{mode}.json")

    import psutil
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication, QWidget

    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope,
                          os.path.join(profile, "qsettings"))
    import layout_store as ls
    ls.reset_layout_store_singleton()
    ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from themes import get_app_font
    app.setFont(get_app_font())

    import ai_panel  # noqa: F401
    import main_window as mw

    restore, sink = [], {}
    tab_sink = {}
    if mode == "panels":
        for label, mod, name in PANEL_CLASSES:
            try:
                cls = getattr(importlib.import_module(mod), name)
            except Exception:
                continue
            restore.append((cls, _patch(cls, sink, label, app, True)))
        orig_add = mw.MainWindow._add_right_tab

        def _timed_add(self, *a, **k):
            t = time.perf_counter()
            try:
                return orig_add(self, *a, **k)
            finally:
                title = a[0] if a else k.get("title", "?")
                tab_sink.setdefault(str(title), []).append(
                    (time.perf_counter() - t) * 1000.0)

        mw.MainWindow._add_right_tab = _timed_add
    else:
        from PySide6 import QtWidgets
        for label, name in WIDGET_TYPES:
            restore.append((getattr(QtWidgets, name),
                            _patch(getattr(QtWidgets, name), sink, label)))
        for mod, name in CUSTOM_PAINTED:
            try:
                cls = getattr(importlib.import_module(mod), name)
            except Exception:
                continue
            restore.append((cls, _patch(cls, sink, f"自定义:{name}")))

    data = []
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = [data]
        except Exception:
            data = []

    load_before = _wait_low(psutil, wait_s)
    if load_before is None:
        print(f"[abort] {wait_s}s 内没有出现 python 总占比 < {LOW_LOAD_PCT}% "
              f"的窗口，不出数")
        return 3

    t0 = time.perf_counter()
    window = mw.MainWindow()
    ctor_ms = (time.perf_counter() - t0) * 1000.0
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    t_show = time.perf_counter()
    window.show()
    show_ms = (time.perf_counter() - t_show) * 1000.0
    t_tick = time.perf_counter()
    app.processEvents()
    tick_ms = (time.perf_counter() - t_tick) * 1000.0
    widgets = len(app.allWidgets())
    load_after = _load(psutil, 2.0)

    row = {
        "mode": mode,
        "run": len(data) + 1,
        "load_before": load_before,
        "load_after": load_after,
        "ctor_ms": round(ctor_ms, 1),
        "show_ms": round(show_ms, 1),
        "tick_ms": round(tick_ms, 1),
        "widgets": widgets,
        "rows": _rows(sink),
    }
    if mode == "panels":
        row["add_right_tab"] = [
            {"title": t, "count": len(v), "total_ms": round(sum(v), 1),
             "max_ms": round(max(v), 1)}
            for t, v in sorted(tab_sink.items(), key=lambda kv: -sum(kv[1]))][:10]
    else:
        counts = {}
        for label, name in WIDGET_TYPES:
            cls = getattr(QtWidgets, name)
            counts[label] = sum(1 for w in app.allWidgets()
                                if type(w) is cls)
        for mod, name in CUSTOM_PAINTED:
            try:
                cls = getattr(importlib.import_module(mod), name)
            except Exception:
                continue
            counts[f"自定义:{name}"] = sum(1 for w in app.allWidgets()
                                          if isinstance(w, cls))
        for r in row["rows"]:
            r["widgets_total"] = counts.get(r["name"], 0)

    data.append(row)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"\n[{mode} run {row['run']}] ctor={ctor_ms:.0f} ms "
          f"show={show_ms:.0f} tick={tick_ms:.0f} widgets={widgets} "
          f"load_before={load_before['python_total_cpu_pct']}% "
          f"load_after={load_after['python_total_cpu_pct']}%")
    for r in row["rows"]:
        extra = f"w={r.get('widgets', r.get('widgets_total', 0)):<4d}"
        print(f"    {r['total_ms']:8.1f} ms  x{r['count']:<3d} {extra}  {r['name']}")
    for r in row.get("add_right_tab", []):
        print(f"    tab {r['total_ms']:7.1f} ms  x{r['count']}  {r['title']}")
    print(f"[json] {out_path}")

    for cls, orig in restore:
        cls.__init__ = orig
    if mode == "panels":
        mw.MainWindow._add_right_tab = orig_add
    return 0


if __name__ == "__main__":
    sys.exit(_main())

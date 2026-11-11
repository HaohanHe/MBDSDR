"""构造期拆段复量 + 并发负载采样。

与 scripts/repro/2026-11-11-p0-15b-first-tick-cost.py --mode baseline --screen --show
同尺子、同入口、同隔离 profile，只多两件事：
  1. 把构造期拆成 import / 控件构建 / 主题应用 / 其它（构造残余）/ show 布局 / 首拍；
  2. 测量前后各采一次机器负载（系统 CPU、python 进程数与各自 CPU 占比）。

用法：
    python scripts/repro/2026-11-11-ctor-segments-with-load.py --run 1
结果追加进 docs/perf-evidence/2026-11-11/ctor_segments_load.json（列表）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = os.path.join(tempfile.gettempdir(), "mbdsdr-repro")
OUT = os.path.join(ROOT, "docs", "perf-evidence", "2026-11-11",
                   "ctor_segments_load.json")
SCREEN_ENV = {
    "QT_QPA_PLATFORM": "windows",
    "QT_SCALE_FACTOR": "1",
    "QT_ENABLE_HIGHDPI_SCALING": "0",
}


def _prepare_env() -> str:
    os.makedirs(TMP_ROOT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="ctor-seg-")
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ.update(SCREEN_ENV)
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    return profile


def _sample_load(psutil, sec: float = 1.0) -> dict:
    """系统总 CPU + 本机 python 进程数与各自 CPU 占比（按 cpu_times 增量算）。"""
    procs = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
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
            a = p.cpu_times()
            b = before[p.pid]
            delta = (a.user - b.user) + (a.system - b.system)
            cmd = " ".join((p.info.get("cmdline") or [])[:3])[:80]
            rows.append({"pid": p.pid,
                         "cpu_pct": round(delta / sec * 100.0, 1),
                         "cmd": cmd})
        except Exception:
            continue
    rows.sort(key=lambda r: -r["cpu_pct"])
    total_pct = round(sum(r["cpu_pct"] for r in rows), 1)
    return {
        "sys_cpu_pct": round(psutil.cpu_percent(interval=sec), 1),
        "cpu_count": psutil.cpu_count(),
        "python_proc_count": len(rows),
        "python_total_cpu_pct": total_pct,
        "python_top": rows[:5],
    }


def _wrap_main_window(mw):
    """把 MainWindow 自己的 _build* / _create* / _init* 与 _apply_theme 计时。

    嵌套调用（_build_central_widget 里再调 _build_top_bar）必须只按**最外层**
    归属，否则父+子重复计数，拆段和会超过构造期总时长。这里记深度：
    level=1 才是顶层段，其余只进明细。
    """
    spans, theme_spans = [], []
    state = {"in_ctor": False, "depth": 0}

    def _mk(name, fn, sink):
        def wrapper(self, *a, **k):
            if not state["in_ctor"]:
                return fn(self, *a, **k)
            state["depth"] += 1
            level = state["depth"]
            t = time.perf_counter()
            try:
                return fn(self, *a, **k)
            finally:
                sink.append((name, (time.perf_counter() - t) * 1000.0, level))
                state["depth"] -= 1
        wrapper.__name__ = name
        wrapper.__doc__ = fn.__doc__
        return wrapper

    wrapped = 0
    for name, fn in list(vars(mw.MainWindow).items()):
        if not callable(fn) or not isinstance(fn, type(lambda: None)):
            continue
        if name.startswith(("_build", "_create", "_init_")):
            setattr(mw.MainWindow, name, _mk(name, fn, spans))
            wrapped += 1
        elif name == "_apply_theme":
            setattr(mw.MainWindow, name, _mk(name, fn, theme_spans))
            wrapped += 1
    return spans, theme_spans, state, wrapped


def _main() -> int:
    run = "?"
    if "--run" in sys.argv:
        run = sys.argv[sys.argv.index("--run") + 1]
    profile = _prepare_env()
    print(f"repo   : {ROOT}")
    print(f"run    : {run}")
    print(f"临时档案: {profile}")

    try:
        import psutil
        from PySide6.QtCore import QSettings, Qt
        from PySide6.QtGui import QFontDatabase, QFontMetrics
        from PySide6.QtWidgets import QApplication

        load_before = _sample_load(psutil)

        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        import layout_store as ls
        ls.reset_layout_store_singleton()
        ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
        app = QApplication.instance() or QApplication(sys.argv[:1])

        t0 = time.perf_counter()
        import ai_panel as ap  # noqa: F401
        ai_panel_ms = (time.perf_counter() - t0) * 1000.0

        from themes import get_app_font
        app.setFont(get_app_font())          # 与 desktop/main.py 同一条生产入口
        fams = QFontDatabase.families()
        if not fams:
            print("[无法判定] QFontDatabase.families() 为 0（字体退化），不出数")
            return 2
        scr = app.primaryScreen()
        fm = QFontMetrics(app.font())
        ruler = {
            "qt_platform": app.platformName(),
            "font_families": len(fams),
            "device_pixel_ratio": round(scr.devicePixelRatio(), 3) if scr else 0.0,
            "logical_dpi": round(scr.logicalDotsPerInch(), 1) if scr else 0.0,
            "font_family": app.font().family(),
            "font_point_size": app.font().pointSizeF(),
            "font_line_height": fm.height(),
            "scale_factor": os.environ.get("QT_SCALE_FACTOR") or "（未设）",
        }
        print("尺子指纹: " + " ".join(f"{k}={v}" for k, v in ruler.items()))

        t0 = time.perf_counter()
        import main_window as mw
        main_window_ms = (time.perf_counter() - t0) * 1000.0

        spans, theme_spans, state, wrapped = _wrap_main_window(mw)
        print(f"已插桩 MainWindow 方法 {wrapped} 个")

        state["in_ctor"] = True
        t_ctor = time.perf_counter()
        window = mw.MainWindow()
        ctor_ms = (time.perf_counter() - t_ctor) * 1000.0
        state["in_ctor"] = False

        build_rows = [(n, ms) for n, ms, lv in spans if lv == 1]
        theme_rows = [(n, ms) for n, ms, lv in theme_spans if lv == 1]
        build_ms = sum(ms for _n, ms in build_rows)
        theme_ms = sum(ms for _n, ms in theme_rows)
        other_ms = ctor_ms - build_ms - theme_ms

        window.setAttribute(Qt.WA_DontShowOnScreen, True)
        t_show = time.perf_counter()
        window.show()
        show_ms = (time.perf_counter() - t_show) * 1000.0
        t_tick = time.perf_counter()
        app.processEvents()
        tick_ms = (time.perf_counter() - t_tick) * 1000.0

        load_after = _sample_load(psutil)

        top = sorted(build_rows, key=lambda r: -r[1])[:10]
        top_theme = sorted(theme_rows, key=lambda r: -r[1])[:3]
        nested = sorted([(n, ms) for n, ms, lv in spans if lv >= 2],
                        key=lambda r: -r[1])[:10]
        widgets = len(app.allWidgets())

        print(f"\n[load-before] sys_cpu={load_before['sys_cpu_pct']}% "
              f"cpus={load_before['cpu_count']} "
              f"py_procs={load_before['python_proc_count']} "
              f"py_total={load_before['python_total_cpu_pct']}%")
        for r in load_before["python_top"]:
            print(f"    pid={r['pid']:<6d} {r['cpu_pct']:6.1f}%  {r['cmd']}")
        print(f"\n[import] ai_panel={ai_panel_ms:.0f} ms  "
              f"main_window={main_window_ms:.0f} ms  "
              f"合计={ai_panel_ms + main_window_ms:.0f} ms")
        print(f"[ctor] 合计={ctor_ms:.0f} ms "
              f"= 控件构建 {build_ms:.0f} + 主题应用 {theme_ms:.0f} "
              f"+ 其它 {other_ms:.0f}")
        print(f"  控件构建 top{len(top)}（仅最外层段）:")
        for n, ms in top:
            print(f"    {ms:8.1f} ms  {n}")
        print(f"  嵌套明细 top{len(nested)}（_build_central_widget 内部）:")
        for n, ms in nested:
            print(f"    {ms:8.1f} ms  {n}")
        if top_theme:
            print("  主题应用:")
            for n, ms in top_theme:
                print(f"    {ms:8.1f} ms  {n}")
        print(f"[show] {show_ms:.0f} ms   [首拍 processEvents] {tick_ms:.0f} ms   "
              f"widgets={widgets}")
        print(f"\n[load-after] sys_cpu={load_after['sys_cpu_pct']}% "
              f"py_procs={load_after['python_proc_count']} "
              f"py_total={load_after['python_total_cpu_pct']}%")
        for r in load_after["python_top"]:
            print(f"    pid={r['pid']:<6d} {r['cpu_pct']:6.1f}%  {r['cmd']}")

        row = {
            "run": run,
            "ruler": ruler,
            "shown": True,
            "load_before": load_before,
            "load_after": load_after,
            "import_ms": {"ai_panel": round(ai_panel_ms, 1),
                          "main_window": round(main_window_ms, 1),
                          "total": round(ai_panel_ms + main_window_ms, 1)},
            "ctor_ms": round(ctor_ms, 1),
            "ctor_build_ms": round(build_ms, 1),
            "ctor_theme_ms": round(theme_ms, 1),
            "ctor_other_ms": round(other_ms, 1),
            "build_top": [{"name": n, "ms": round(ms, 1)} for n, ms in top],
            "build_nested_top": [{"name": n, "ms": round(ms, 1)}
                                 for n, ms in nested],
            "theme_top": [{"name": n, "ms": round(ms, 1)} for n, ms in top_theme],
            "show_ms": round(show_ms, 1),
            "tick_ms": round(tick_ms, 1),
            "widgets": widgets,
            "wrapped_methods": wrapped,
        }
        data = []
        if os.path.exists(OUT):
            try:
                with open(OUT, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    data = [data]
            except Exception:
                data = []
        data.append(row)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"\n[json] {OUT}（第 {len(data)} 条）")
        return 0
    except Exception:
        traceback.print_exc()
        return 2
    finally:
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(_main())

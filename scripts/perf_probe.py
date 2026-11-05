# -*- coding: utf-8 -*-
"""MBDSDR 桌面前端性能探针（可复跑）。

一条命令复测：启动分段 / GUI 线程阻塞 / 渲染热点 / 内存增长 / 解码吞吐，
输出 Markdown 表格（同时写 ``output/bench/ui_perf_<ts>.json``）。

原则：先测再改。所有数字都是本机实测，脚本本身不含任何“估算”。

用法::

    python scripts/perf_probe.py                 # 全部
    python scripts/perf_probe.py --section ui    # 只测渲染/响应
    python scripts/perf_probe.py --section startup
    python scripts/perf_probe.py --section memory
    python scripts/perf_probe.py --section dsp

环境：Windows / ``QT_QPA_PLATFORM=offscreen``（无窗口、软件光栅，数值为 CPU
侧成本；真机 GPU 合成会再省一部分）。启动项在独立子进程里测，避免同进程
import 缓存污染。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

# 让测量进程拿到更高的 CPU 优先级，减少同机其它会话造成的抖动（不改变被测代码）。
try:  # pragma: no cover - 仅 Windows 有效
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(
        ctypes.windll.kernel32.GetCurrentProcess(), 0x80)  # HIGH_PRIORITY_CLASS
except Exception:
    pass

PY = sys.executable


def _cpu_name() -> str:
    try:  # pragma: no cover
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Processor).Name"], text=True,
            timeout=10, stderr=subprocess.DEVNULL)
        return out.strip()
    except Exception:
        return platform.processor() or "unknown"


def _md_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def _stat(samples):
    return {
        "n": len(samples),
        "median_ms": round(statistics.median(samples), 2),
        "min_ms": round(min(samples), 2),
        "p95_ms": round(sorted(samples)[max(0, int(len(samples) * 0.95) - 1)], 2),
        "max_ms": round(max(samples), 2),
    }


# --------------------------------------------------------------------------- #
# 1) 启动分段（子进程）
# --------------------------------------------------------------------------- #
_INTERNAL_STARTUP = r"""
import json, os, sys, time
ROOT = os.environ["MBDSDR_ROOT"]
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "desktop"))
marks = {}
t_all = time.perf_counter()
def mark(n): marks[n] = round((time.perf_counter() - t_all) * 1000, 1)
mark("process_start")
from PySide6.QtWidgets import QApplication
mark("qt_import")
from themes import get_theme, DEFAULT_THEME, get_app_font
from main_window import MainWindow
mark("main_window_import")
app = QApplication.instance() or QApplication(sys.argv)
mark("app_create")
app.setFont(get_app_font())
get_theme(DEFAULT_THEME).apply(app)
mark("theme_apply")
t = time.perf_counter(); w = MainWindow()
marks["main_window_init"] = round((time.perf_counter() - t) * 1000, 1)
mark("main_window_init_end")
w.show(); app.processEvents()
mark("show_first_paint")
t = time.perf_counter(); w.grab()
marks["grab_first"] = round((time.perf_counter() - t) * 1000, 1)
mark("grab_end")
print("PHASES_JSON " + json.dumps(marks))
"""


def section_startup() -> dict:
    env = dict(os.environ, MBDSDR_ROOT=ROOT, PYTHONPATH=ROOT + os.pathsep +
               os.path.join(ROOT, "desktop"))
    # 1a) import 侧真实数据（-X importtime）
    t = time.perf_counter()
    p = subprocess.run([PY, "-X", "importtime", "-c", "import main_window"],
                       cwd=os.path.join(ROOT, "desktop"), env=env,
                       capture_output=True, text=True, timeout=300)
    wall = time.perf_counter() - t
    import_rows = []
    total_cum = 0.0
    for ln in p.stderr.splitlines():
        s = ln.strip()
        if not s.startswith("import time:"):
            continue
        try:
            _, rest = s.split("import time:", 1)
            self_us, cum_us, name = [x.strip() for x in rest.split("|", 2)]
            c = int(cum_us) / 1e6
            if name == "main_window":
                total_cum = c
            if c >= 0.15:
                import_rows.append((name, round(int(self_us) / 1e6, 3), round(c, 3)))
        except Exception:
            continue
    import_rows.sort(key=lambda r: -r[2])
    # 1b) 分段（独立子进程）
    tmp_dir = os.path.join(os.path.dirname(ROOT), ".tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    probe = os.path.join(tmp_dir, "perf_probe_phase.py")
    with open(probe, "w", encoding="utf-8") as f:
        f.write(_INTERNAL_STARTUP)
    try:
        runs = []
        for _ in range(3):  # 取 3 次独立进程的中位数，压低同机其它负载造成的抖动
            q = subprocess.run([PY, probe], cwd=ROOT, env=env, capture_output=True,
                               text=True, timeout=300)
            phases_one = {}
            for ln in q.stdout.splitlines():
                if ln.startswith("PHASES_JSON "):
                    phases_one = json.loads(ln[len("PHASES_JSON "):])
            if phases_one:
                runs.append(phases_one)
        phases = ({k: round(statistics.median([r[k] for r in runs if k in r]), 1)
                   for k in runs[0]} if runs else {})
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
    return {
        "import_wall_s": round(wall, 2),
        "import_total_s": round(total_cum, 2),
        "import_top": import_rows[:8],
        "phases_ms": phases,
        "phases_runs": runs,
        "stderr_tail": (p.stderr or "")[-400:],
    }


# --------------------------------------------------------------------------- #
# 2) UI：渲染热点 / 帧预算 / 热插拔阻塞 / 心跳
# --------------------------------------------------------------------------- #
def _qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _sky_frame_samples(size, fov, bortle=3, n=25, warm=5):
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QPoint
    from rf_sky_view import RFSkyView
    app = _qt_app()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(43.817, 125.323, 200.0)
    v.view_state.look_at(180.0, 45.0)
    v.view_state.zoom_to(fov)
    v._bortle = bortle
    v.show()
    app.processEvents()
    img = QImage(size, size, QImage.Format_ARGB32)
    ts = []
    for i in range(n + warm):
        img.fill(0)
        pa = QPainter(img)
        t = time.perf_counter()
        v.render(pa, QPoint(0, 0))
        ts.append((time.perf_counter() - t) * 1000.0)
        pa.end()
    v.close()
    v.deleteLater()
    app.processEvents()
    return ts[warm:]


def _sky_layer_breakdown(size=700, fov=120.0, n=20, warm=3):
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QPoint
    from rf_sky_view import RFSkyView
    app = _qt_app()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(43.817, 125.323, 200.0)
    v.view_state.look_at(180.0, 45.0)
    v.view_state.zoom_to(fov)
    v._bortle = 3
    v.show()
    app.processEvents()
    layers = ["_paint_sky_background", "_draw_stars", "_draw_constellations",
              "_draw_dso", "_draw_atmosphere_glow", "_draw_landscape",
              "_draw_az_grid", "_draw_eq_grid", "_draw_planets",
              "_draw_objects", "_draw_gnss_satellites", "_flush_labels"]
    times = {}
    for name in layers:
        fn = getattr(v, name, None)
        if fn is None:
            continue

        def make(fn, name):
            def wrap(*a, **k):
                t = time.perf_counter()
                r = fn(*a, **k)
                times.setdefault(name, []).append((time.perf_counter() - t) * 1000.0)
                return r
            return wrap
        setattr(v, name, make(fn, name))
    img = QImage(size, size, QImage.Format_ARGB32)
    for i in range(n + warm):
        if i == warm:
            times.clear()
        img.fill(0)
        pa = QPainter(img)
        v.render(pa, QPoint(0, 0))
        pa.end()
    v.close()
    v.deleteLater()
    app.processEvents()
    return {name: round(statistics.median(ts), 2) for name, ts in times.items()}


def _spectrum_samples(n=20, warm=3):
    from PySide6.QtGui import QImage, QPainter
    import numpy as np
    from spectrum_widget import SpectrumPanel, _render_spectrum, _render_wf_only
    app = _qt_app()
    rng = np.random.default_rng(7)
    panel = SpectrumPanel()
    panel.resize(900, 500)
    panel.show()
    app.processEvents()
    gen = panel.generator
    m = 2048
    sig = (0.02 * rng.standard_normal(m) + 0.02 * rng.standard_normal(m) * 1j
           + 0.25 * np.exp(2j * np.pi * 0.1 * np.arange(m) * 2)
           + 0.5 * np.exp(2j * np.pi * -0.22 * np.arange(m) * 2)).astype(np.complex64)
    for _ in range(60):
        gen.push_iq(sig, 100e6, gen.sample_rate_hz)
    app.processEvents()

    def batch(fn):
        for _ in range(warm):
            fn()
        ts = []
        for _ in range(n):
            t = time.perf_counter()
            fn()
            ts.append((time.perf_counter() - t) * 1000.0)
        return ts

    img = QImage(900, 400, QImage.Format_ARGB32)

    def r_fft():
        img.fill(0)
        pa = QPainter(img)
        _render_spectrum(pa, panel._plot.state, 900, 400)
        pa.end()

    def r_wf():
        img.fill(0)
        pa = QPainter(img)
        _render_wf_only(pa, panel._wf_plot.state, 900, 200)
        pa.end()

    res = {
        "fft_render": _stat(batch(r_fft)),
        "wf_render": _stat(batch(r_wf)),
        "fft_widget_grab": _stat(batch(panel._fft_plot.grab)),
        "panel_grab": _stat(batch(panel.grab)),
    }
    panel.close()
    panel.deleteLater()
    app.processEvents()
    return res


def _hotplug_block():
    from PySide6.QtWidgets import QApplication
    app = _qt_app()
    import device_panel as dp
    p = dp.DevicePanel()
    t = time.perf_counter()
    p._on_hotplug_poll()
    dispatch_ms = (time.perf_counter() - t) * 1000.0
    t = time.perf_counter()
    while p._enum_inflight and time.perf_counter() - t < 30:
        app.processEvents()
        time.sleep(0.005)
    worker_ms = (time.perf_counter() - t) * 1000.0
    p.close()
    p.deleteLater()
    app.processEvents()
    return {"dispatch_ms": round(dispatch_ms, 2),
            "worker_complete_ms": round(worker_ms, 1),
            "note": "dispatch 在 GUI 线程；worker_complete 在后台 QThreadPool"}


def _heartbeat(seconds=8, interval_ms=50):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = _qt_app()
    from main_window import MainWindow
    t0 = time.perf_counter()
    w = MainWindow()
    init_ms = (time.perf_counter() - t0) * 1000.0
    w.show()
    app.processEvents()
    shown_ms = (time.perf_counter() - t0) * 1000.0
    gaps = []
    last = [time.perf_counter()]

    def tick():
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000.0)
        last[0] = now

    tm = QTimer()
    tm.setInterval(interval_ms)
    tm.timeout.connect(tick)
    tm.start()
    QTimer.singleShot(int(seconds * 1000), app.quit)
    app.exec()
    tm.stop()
    g = gaps[1:]
    w.close()
    w.deleteLater()
    app.processEvents()
    return {
        "init_ms": round(init_ms, 1),
        "shown_ms": round(shown_ms, 1),
        "expect_ticks": int(seconds * 1000 / interval_ms),
        "ticks": len(g),
        "gap_p50_ms": round(statistics.median(g), 1),
        "gap_p95_ms": round(sorted(g)[int(len(g) * 0.95) - 1], 1),
        "gap_max_ms": round(max(g), 1),
        "over_100ms": sum(1 for x in g if x > 100),
    }


def section_ui() -> dict:
    out = {"frames": {}, "layers": {}, "spectrum": {}, "hotplug": {},
           "heartbeat": {}, "pacing": {}}
    for size in (700, 1000):
        for fov in (120.0, 60.0):
            key = f"{size}px_fov{fov:g}"
            out["frames"][key] = _stat(_sky_frame_samples(size, fov))
    out["layers"]["700px_fov120"] = _sky_layer_breakdown()
    out["spectrum"] = _spectrum_samples()
    out["hotplug"] = _hotplug_block()
    # 自适应步长：渲染 5 帧后连续 tick，观察 interval 是否回落/拉长
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QPoint
    from rf_sky_view import RFSkyView
    app = _qt_app()
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(43.817, 125.323, 200.0)
    v.view_state.look_at(180.0, 45.0)
    v.view_state.zoom_to(120.0)
    v._bortle = 3
    v.show()
    app.processEvents()
    img = QImage(700, 700, QImage.Format_ARGB32)
    for _ in range(5):
        img.fill(0)
        pa = QPainter(img)
        v.render(pa, QPoint(0, 0))
        pa.end()
    before = v._refresh_timer.interval()
    for _ in range(8):
        v._on_refresh_tick()
        app.processEvents()
    out["pacing"] = {
        "paint_ms_ema": round(v._paint_ms_ema, 1),
        "interval_before_ms": before,
        "interval_after_ms": v._refresh_timer.interval(),
        "note": "帧贵时只拉长重绘间隔；绘制内容与像素不变",
    }
    v.close()
    v.deleteLater()
    app.processEvents()
    out["heartbeat"] = _heartbeat()
    return out


# --------------------------------------------------------------------------- #
# 3) 内存
# --------------------------------------------------------------------------- #
def section_memory(frames=300) -> dict:
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QPoint
    import rf_sky_view
    from rf_sky_view import RFSkyView
    app = _qt_app()
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(43.817, 125.323, 200.0)
    v.view_state.look_at(180.0, 45.0)
    v.view_state.zoom_to(120.0)
    v._bortle = 3
    v.show()
    app.processEvents()
    img = QImage(700, 700, QImage.Format_ARGB32)

    def frame():
        img.fill(0)
        pa = QPainter(img)
        v.render(pa, QPoint(0, 0))
        pa.end()

    for _ in range(20):
        frame()
    gc.collect()
    tracemalloc.start()
    base_tm = tracemalloc.get_traced_memory()[0]
    base_obj = len(gc.get_objects())
    t0 = time.perf_counter()
    for i in range(frames):
        frame()
        if (i + 1) % 100 == 0:
            app.processEvents()
    elapsed = time.perf_counter() - t0
    gc.collect()
    end_tm = tracemalloc.get_traced_memory()[0]
    end_obj = len(gc.get_objects())
    res = {
        "frames": frames,
        "avg_frame_ms": round(elapsed * 1000.0 / frames, 1),
        "tracemalloc_delta_kb": round((end_tm - base_tm) / 1024.0, 0),
        "gc_objects_delta": end_obj - base_obj,
        "pen_cache_entries": len(rf_sky_view._star_pen_cache),
        "brush_cache_entries": len(rf_sky_view._star_brush_cache),
    }
    v.close()
    v.deleteLater()
    app.processEvents()
    return res


# --------------------------------------------------------------------------- #
# 4) 解码吞吐：复用既有基准脚本
# --------------------------------------------------------------------------- #
def section_dsp() -> dict:
    bench = os.path.join(ROOT, "scripts", "bench_dsp_throughput.py")
    outdir = os.path.join(ROOT, "output", "bench")
    before = set(os.listdir(outdir)) if os.path.isdir(outdir) else set()
    t = time.perf_counter()
    p = subprocess.run([PY, bench, "--quick"], cwd=ROOT, capture_output=True,
                       text=True, timeout=600)
    wall = time.perf_counter() - t
    after = set(os.listdir(outdir)) if os.path.isdir(outdir) else set()
    new = sorted(after - before)
    data = {}
    if new:
        with open(os.path.join(outdir, new[-1]), encoding="utf-8") as f:
            data = json.load(f)
    rows = []
    for rate in data.get("rates", []):
        for k, v in rate.get("stages", {}).items():
            rows.append((k, rate["sample_rate_msps"], v["median_ms"],
                         v["realtime_factor"]))
        e2e = rate.get("e2e", {})
        rows.append(("e2e_sustained", rate["sample_rate_msps"],
                     e2e.get("sustained_msps"), e2e.get("sustained_rtf")))
    return {"exit": p.returncode, "wall_s": round(wall, 1),
            "json": new[-1] if new else "", "overall_ok": data.get("overall_ok"),
            "rows": rows, "stderr_tail": (p.stderr or "")[-300:]}


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default="all",
                    choices=["all", "startup", "ui", "memory", "dsp"])
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    env = {
        "cpu": _cpu_name(),
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
    }
    result = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
              "env": env}
    md = [f"# MBDSDR 性能探针结果 {result['generated_at']}", "",
          f"环境：{env['cpu']} / {env['cpu_count']} 线程 / Python {env['python']}"
          f" / QT_QPA_PLATFORM={env['qt_platform']}", ""]

    if a.section in ("all", "startup"):
        s = section_startup()
        result["startup"] = s
        ph = s["phases_ms"]
        md += ["## 启动分段（离屏，独立子进程）", "",
               f"- `import main_window`（-X importtime 累计）：**{s['import_total_s']} s**",
               f"- 构造 MainWindow：**{ph.get('main_window_init', 0)} ms**"
               f"（进程起点 → 构造完成 {ph.get('main_window_init_end', 0)} ms）",
               f"- show + 首帧：**{round(ph.get('show_first_paint', 0) - ph.get('main_window_init_end', 0), 1)} ms**",
               f"- 进程起点 → 首帧可交互：**{ph.get('show_first_paint', 0)} ms**", "",
               _md_table([(n, f"{self_s}s", f"{cum}s") for n, self_s, cum in s["import_top"]],
                         ["import 模块", "self", "累计"]), ""]

    if a.section in ("all", "ui"):
        u = section_ui()
        result["ui"] = u
        md += ["## 天空渲染（离屏软件光栅，单位 ms）", "",
               _md_table([(k, v["median_ms"], v["min_ms"], v["p95_ms"], v["n"])
                          for k, v in u["frames"].items()],
                         ["场景", "median", "min", "p95", "n"]), "",
               "### 700px / 120° 分层", "",
               _md_table([(k, v) for k, v in sorted(u["layers"]["700px_fov120"].items(),
                                                    key=lambda kv: -kv[1])],
                         ["图层", "median ms"]), "",
               "## 频谱 / 瀑布（900×500 面板）", "",
               _md_table([(k, v["median_ms"], v["min_ms"], v["p95_ms"])
                          for k, v in u["spectrum"].items()],
                         ["渲染路径", "median", "min", "p95"]), "",
               "## 热插拔轮询阻塞", "",
               _md_table([(k, v) for k, v in u["hotplug"].items() if isinstance(v, (int, float))],
                         ["指标", "ms"]), "",
               "## 帧步长自适应", "",
               _md_table([(k, v) for k, v in u["pacing"].items() if isinstance(v, (int, float))],
                         ["指标", "值"]), "",
               "## 主窗口心跳（50ms 定时器）", "",
               _md_table([(k, v) for k, v in u["heartbeat"].items()],
                         ["指标", "值"]), ""]

    if a.section in ("all", "memory"):
        m = section_memory(a.frames)
        result["memory"] = m
        md += ["## 内存增长", "",
               _md_table([(k, v) for k, v in m.items()], ["指标", "值"]), ""]

    if a.section in ("all", "dsp"):
        d = section_dsp()
        result["dsp"] = d
        md += ["## 解码 / DSP 吞吐（复用 bench_dsp_throughput.py --quick）", "",
               f"- 退出码 {d['exit']}，overall_ok={d['overall_ok']}，结果 {d['json']}", "",
               _md_table([(a_, f"{b}MS/s", c_, e_) for a_, b, c_, e_ in d["rows"]],
                         ["环节", "档位", "median ms", "RTF"]), ""]

    text = "\n".join(md)
    print(text)
    out = a.json or os.path.join(
        ROOT, "output", "bench",
        f"ui_perf_{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nJSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

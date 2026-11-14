# -*- coding: utf-8 -*-
"""长列表渲染基线复跑脚本（2026-11-14）。

回答同一个问题：**这次刷新/每个按键，渲染命中行数要花多少毫秒**，据此决定
要不要像命令面板那样「只渲染前 N 行 + 明说还有多少未显示」。

覆盖列表（每节都构造真实面板，只注入合成行数据，不联网、不碰真实配置）：
  * scanner  扫频结果 QListWidget       ``ScannerPanel._render_segments``
  * bookmark 书签 QTreeWidget           ``BookmarkPanel.refresh_list``（每键）
  * passes   卫星过境 QTableWidget      ``NewSpacetimePanel._populate_passes``
  * adsb     飞机 QTableWidget          ``AdsbPanel._refresh_table``（每批帧）
  * aprs     APRS 包 QTableWidget       ``AprsPanel._refresh_table``（每包）
  * device   设备 QComboBox             ``DevicePanel`` 的候选项渲染成本

口径：offscreen QApplication；每档 1 次预热 + R 次采样，报中位/最坏；脚本
前后各采一次机器负载。``--mode before`` 把新增上限放大到无穷，用来在同一
进程做「去掉上限」的反证对照（旧代码没有常量时等价于本来就不限）。

用法：
    python scripts/repro/2026-11-14-list-render-baseline.py
    python scripts/repro/2026-11-14-list-render-baseline.py --only bookmark,adsb
    python scripts/repro/2026-11-14-list-render-baseline.py --mode before \\
        --only bookmark,adsb --run before
结果追加进 ``docs/perf-evidence/2026-11-14/list-render-baseline.json``。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

REPO = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, "docs", "perf-evidence", "2026-11-14",
                   "list-render-baseline.json")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "desktop"))


def bench(fn, rounds=5):
    samples = []
    for n in range(1 + rounds):
        t0 = time.perf_counter()
        fn()
        if n >= 1:
            samples.append((time.perf_counter() - t0) * 1000.0)
    return round(statistics.median(samples), 2), round(max(samples), 2)


def _load(psutil, sec=1.0):
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
    sys_cpu = psutil.cpu_percent(interval=sec)
    rows = []
    for p in procs:
        try:
            a, b = p.cpu_times(), before[p.pid]
            delta = (a.user - b.user) + (a.system - b.system)
            rows.append(round(delta / sec * 100.0, 1))
        except Exception:
            continue
    return {"sys_cpu_pct": round(sys_cpu, 1), "cpu_count": psutil.cpu_count(),
            "python_proc_count": len(rows),
            "python_total_cpu_pct": round(sum(rows), 1),
            "python_top_pct": sorted(rows, reverse=True)[:5]}


# --------------------------------------------------------------------------- #
def scanner_section(app):
    from scanner_panel import ScannerPanel
    dlg = ScannerPanel()
    out = []
    base = 145_000_000
    for n in (100, 500, 2000, 5000):
        dlg._segments = [
            {"start_freq": base + i * 25_000,
             "end_freq": base + i * 25_000 + 12_500,
             "peak_db": -50.0 - i % 30, "kind": "NFM",
             "suggested_mode": "NFM"} for i in range(n)]
        med, worst = bench(dlg._render_segments)
        out.append({"rows": n, "widget_rows": dlg.result_list.count(),
                    "median_ms": med, "worst_ms": worst})
    dlg.deleteLater()
    return {"sizes": out, "callsite": "ScannerPanel._render_segments",
            "cadence": "扫描完成后一次",
            "real_bound": "默认 21 MHz/0.1 MHz=210 段；扫 2000 段本身要数十秒"}


def _bookmark(i):
    return SimpleNamespace(name=f"频道 {i}",
                           frequency_hz=145_000_000 + i * 1000,
                           modulation="FM", bandwidth_hz=12_500,
                           group=("业余" if i % 2 else ""))


def bookmark_section(app):
    import bookmark_panel as bp
    dlg = bp.BookmarkPanel(config_dir=tempfile.mkdtemp(prefix="bm-baseline-"))
    out = []
    for n, rounds in ((250, 5), (500, 5), (1000, 5), (5000, 5), (20000, 3)):
        items = [_bookmark(i) for i in range(n)]
        dlg._mgr = SimpleNamespace(all=lambda: items, search=lambda q: items)
        med, worst = bench(dlg.refresh_list, rounds=rounds)
        out.append({"rows": n, "tree_rows": dlg.tree.topLevelItemCount(),
                    "median_ms": med, "worst_ms": worst})
    dlg.deleteLater()
    return {"sizes": out, "callsite": "BookmarkPanel.refresh_list",
            "cadence": "搜索框每键 + 增删改后",
            "cap": getattr(bp, "MAX_RENDERED_BOOKMARKS", None)}


def _passes(n):
    from mbdsdr_ai.new_spacetime_timeline import SatellitePass
    t0 = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        rise = t0 + timedelta(minutes=i * 7)
        out.append(SatellitePass(
            name=f"SAT-{i:04d}", rise_time=rise, rise_azimuth=10.0,
            max_time=rise + timedelta(minutes=6), max_elevation=30.0 + i % 60,
            max_azimuth=20.0, set_time=rise + timedelta(minutes=12),
            set_azimuth=30.0, duration_sec=720.0,
            frequency_hz=437_000_000.0, max_doppler_hz=3000.0,
            tle_age_days=1.0, policy={}))
    return out


def passes_section(app):
    from new_spacetime_panel import NewSpacetimePanel
    dlg = NewSpacetimePanel()
    out = []
    for n in (200, 1000, 3000):
        ps = _passes(n)
        med, worst = bench(lambda: dlg._populate_passes(ps), rounds=3)
        fmed, fworst = bench(lambda: dlg._apply_passes_filter(""), rounds=3)
        out.append({"rows": n, "table_rows": dlg.passes_table.rowCount(),
                    "median_ms": med, "worst_ms": worst,
                    "filter_median_ms": fmed, "filter_worst_ms": fworst})
    dlg.deleteLater()
    return {"sizes": out, "callsite": "NewSpacetimePanel._populate_passes",
            "cadence": "后台过境计算结束后一次；过滤框每键",
            "real_bound": "只对 DEFAULT_SATELLITES(6) 计算，窗口上限 72h"}


def _aircraft(n):
    now = time.time()
    return [SimpleNamespace(icao_hex=f"{0xA00000 + i:06x}",
                            callsign=f"CA{i:04d}", lat=30.0 + i * 0.001,
                            lon=120.0 + i * 0.001, altitude_ft=10000 + i,
                            groundspeed_kt=420.0, track_deg=90.0,
                            last_seen=now - i * 0.1) for i in range(n)]


def adsb_section(app):
    import adsb_panel as ap
    dlg = ap.AdsbPanel()
    out = []
    for n in (100, 500, 1200, 2000):
        acs = _aircraft(n)
        dlg._tracker = SimpleNamespace(all=lambda: acs)
        dlg._tracker.__len__ = lambda: len(acs)
        med, worst = bench(dlg._refresh_table)
        out.append({"rows": n, "table_rows": dlg.table.rowCount(),
                    "median_ms": med, "worst_ms": worst})
    dlg.deleteLater()
    return {"sizes": out, "callsite": "AdsbPanel._refresh_table",
            "cadence": "每批解码帧（_after_update）",
            "cap": getattr(ap, "MAX_RENDERED_AIRCRAFT", None)}


def _packets(n):
    base = time.time()
    return [{"_ts": base - i, "source": "BG5ABC", "source_ssid": 9,
             "destination": "APRS", "destination_ssid": 0,
             "digipeaters": ["WIDE1-1", "WIDE2-1"], "type": "position",
             "comment": f"测试包 {i} 145.390MHz",
             "raw": f"BG5ABC>APRS,WIDE1-1:!3012.34N/12012.34E-测试包 {i}",
             "latitude": 30.2, "longitude": 120.2} for i in range(n)]


def aprs_section(app):
    from aprs_panel import AprsPanel, MAX_PACKETS
    dlg = AprsPanel()
    out = []
    for n in (100, 500):
        dlg._packets = _packets(n)
        med, worst = bench(dlg._refresh_table)
        out.append({"rows": n, "table_rows": dlg.table.rowCount(),
                    "median_ms": med, "worst_ms": worst})
    dlg.deleteLater()
    return {"sizes": out, "callsite": "AprsPanel._refresh_table",
            "cadence": "每收到一个包 + 过滤框每键",
            "real_bound": f"_packets 硬上限 MAX_PACKETS={MAX_PACKETS}"}


def device_section(app):
    from PySide6.QtWidgets import QComboBox
    combo = QComboBox()
    out = []
    for n in (8, 64):
        med, worst = bench(lambda: (combo.clear(),
                                    [combo.addItem(f"设备 {i} · RTL-SDR") 
                                     for i in range(n)]))
        out.append({"rows": n, "median_ms": med, "worst_ms": worst})
    return {"sizes": out, "callsite": "DevicePanel 的 dev_combo 填充",
            "cadence": "设备枚举后（拔插/刷新）",
            "real_bound": "物理 SDR 数量，通常 1–4 台"}


SECTIONS = {"scanner": scanner_section, "bookmark": bookmark_section,
            "passes": passes_section, "adsb": adsb_section,
            "aprs": aprs_section, "device": device_section}


def main():
    run = "?"
    mode = "auto"
    only = list(SECTIONS)
    if "--run" in sys.argv:
        run = sys.argv[sys.argv.index("--run") + 1]
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1].split(",")

    import psutil
    import PySide6
    from PySide6.QtWidgets import QApplication
    load_before = _load(psutil)
    app = QApplication.instance() or QApplication(sys.argv[:1])

    import adsb_panel as ap
    import bookmark_panel as bp
    caps = {"bookmark": getattr(bp, "MAX_RENDERED_BOOKMARKS", None),
            "adsb": getattr(ap, "MAX_RENDERED_AIRCRAFT", None)}
    if mode == "before":
        bp.MAX_RENDERED_BOOKMARKS = 10 ** 9
        ap.MAX_RENDERED_AIRCRAFT = 10 ** 9
    print(f"run={run} mode={mode} python={sys.version.split()[0]} "
          f"qt={PySide6.__version__} caps={caps}")

    sections = {}
    for name in only:
        print(f"=== {name} ===", flush=True)
        sections[name] = SECTIONS[name](app)
        print(json.dumps(sections[name], ensure_ascii=False), flush=True)
        app.processEvents()
    load_after = _load(psutil)

    record = {"run": run, "mode": mode, "caps": caps,
              "python": sys.version.split()[0], "qt": PySide6.__version__,
              "platform": app.platformName(),
              "sampling": "每档 1 次预热 + 5 次采样取中位（20000 书签/过境 3 次）",
              "load_before": load_before, "load_after": load_after,
              "sections": sections}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    data = []
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = [data]
        except Exception:
            data = []
    data.append(record)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"[json] {OUT}（第 {len(data)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

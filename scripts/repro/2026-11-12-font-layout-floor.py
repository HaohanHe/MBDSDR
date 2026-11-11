#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""进程字体/排版地板：最小 QApplication + QWidget + 一个中文 QLabel。

回答一个问题：``1158.5 ms`` 里有多少是本机**不可约的首次排版成本**。

一次进程内：

  1. ``QApplication()`` → ``app.setFont(get_app_font())``；
  2. 依次建 N 个最小窗口（默认 3）：``QWidget`` + 一个 ``QLabel``，同一 MiSans
     10pt，中文文本；``show()`` 后跑到该 label 的 **首次 paintEvent 返回**；
  3. 第 1 个窗口 = 冷进程地板，第 2/3 个 = 同进程热缓存地板；
  4. 最后再量一次 ``QFontMetrics.horizontalAdvance`` 确认字体度量已热。

``--text chinese|ascii|none`` 用来分离「首次文字排版」与「建窗口/paint 本身」：
``none`` 是空 QLabel（不排版任何文字），``ascii`` 是同样长度量级的 ASCII。

规则 10：每个子进程测前/测后各采一次系统 CPU + python 进程总占比；
<200% 的窗口才出数。不修改任何产品代码。
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

T0 = time.perf_counter()

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVID = os.path.join(ROOT, "docs", "perf-evidence", "2026-11-12")
LOW_LOAD_PCT = 200.0

TEXTS = {
    "chinese": "星象仪模式 频谱 控制 AI 助手",
    "ascii": "Sky View  Spectrum  Control  AI",
    "none": "",
}


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


def _prepare_env() -> str:
    profile = tempfile.mkdtemp(prefix="fontfloor-")
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
# 单进程：冷地板 + 热地板
# ---------------------------------------------------------------------------
def _measure(args: dict) -> int:
    profile = _prepare_env()
    try:
        import psutil
        from PySide6.QtCore import Qt
        from PySide6.QtGui import (QFont, QFontDatabase, QFontInfo, QFontMetrics,
                                   QImage, QPainter)
        from PySide6.QtWidgets import QApplication, QLabel, QWidget
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] {type(exc).__name__}: {exc}")
        return 2

    load_before = _load(psutil)
    wins = int(args.get("windows", 3))
    mode = args.get("text", "chinese")
    text = TEXTS[mode] if mode in TEXTS else TEXTS["chinese"]

    t_app = time.perf_counter()
    app = QApplication([])
    app_create_ms = (time.perf_counter() - t_app) * 1000.0

    import themes
    t_font = time.perf_counter()
    app.setFont(themes.get_app_font())
    font_set_ms = (time.perf_counter() - t_font) * 1000.0
    font = app.font()
    info = QFontInfo(font)

    class ProbeLabel(QLabel):
        """记录 paintEvent 进入/返回时刻；只认真实绘制，不认事件派发前。"""

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.paint_enter = None
            self.paint_exit = None
            self.paints = 0

        def paintEvent(self, ev):  # noqa: N802（Qt 命名）
            self.paints += 1
            first = self.paint_enter is None
            if first:
                self.paint_enter = time.perf_counter()
            super().paintEvent(ev)
            if first:
                self.paint_exit = time.perf_counter()

    # 每个窗口用哪套字体/文本：twofonts 用来量「第二个字体族」的首次度量。
    if mode == "twofonts":
        consolas = QFont(font)
        consolas.setFamilies(["Consolas"])
        consolas.setPointSizeF(14.0)
        plan = [("MiSans 10pt", TEXTS["chinese"], font),
                ("Consolas 14pt", "123.456 MHz", consolas),
                ("MiSans 10pt", TEXTS["chinese"], font)]
    else:
        plan = [("app font", text, font) for _ in range(wins)]

    rows = []
    for i, (tag, win_text, win_font) in enumerate(plan):
        # 窗口之间先拆掉上一个，避免被前一个窗口的 paint 污染。
        t_win = time.perf_counter()
        host = QWidget()
        host.setWindowTitle("floor")
        host.resize(420, 160)
        label = ProbeLabel(host)
        label.setFont(win_font)
        label.setText(win_text)
        label.setGeometry(16, 16, 380, 90)
        build_ms = (time.perf_counter() - t_win) * 1000.0

        t_show = time.perf_counter()
        host.show()
        show_ms = (time.perf_counter() - t_show) * 1000.0

        deadline = time.perf_counter() + 20.0
        while label.paint_exit is None and time.perf_counter() < deadline:
            app.processEvents()

        if label.paint_exit is None:
            print(f"[无法判定] 窗口 {i + 1} 20 s 内没有 paintEvent")
            return 2

        cycle_ms = (label.paint_exit - t_win) * 1000.0
        dispatch_ms = (label.paint_enter - t_win) * 1000.0
        paint_exec_ms = (label.paint_exit - label.paint_enter) * 1000.0
        total_ms = cycle_ms + (app_create_ms + font_set_ms if i == 0 else 0.0)
        rows.append({
            "window": i + 1,
            "cold": i == 0,
            "font_tag": tag,
            "font_family": QFontInfo(win_font).family(),
            "text": win_text,
            "text_len": len(win_text),
            "app_create_ms": round(app_create_ms, 1) if i == 0 else 0.0,
            "font_set_ms": round(font_set_ms, 1) if i == 0 else 0.0,
            "build_ms": round(build_ms, 1),
            "show_ms": round(show_ms, 1),
            "to_paint_dispatch_ms": round(dispatch_ms, 1),
            "paint_exec_ms": round(paint_exec_ms, 1),
            "cycle_to_paint_done_ms": round(cycle_ms, 1),
            "total_incl_app_ms": round(total_ms, 1),
            "paints": label.paints,
        })
        host.close()
        host.deleteLater()
        app.processEvents()
        time.sleep(0.1)

    # 已热后的字体度量（证明冷的那一笔就是首次度量）
    t_m = time.perf_counter()
    QFontMetrics(font).horizontalAdvance("星象仪模式")
    warm_metrics_ms = (time.perf_counter() - t_m) * 1000.0

    # 第二个字体族（Consolas 14pt）：量它自己是否还要再付一笔首次度量。
    cons = QFont(font)
    cons.setFamilies(["Consolas"])
    cons.setPointSizeF(14.0)
    t_c = time.perf_counter()
    QFontMetrics(cons).horizontalAdvance("123.456 MHz")
    consolas_metrics_ms = (time.perf_counter() - t_c) * 1000.0

    # 旁证：不走控件，纯 QPainter.drawText（splash 的路径）
    img = QImage(420, 160, QImage.Format_ARGB32)
    p = QPainter(img)
    p.setFont(font)
    t_d = time.perf_counter()
    p.drawText(16, 48, text or " ")
    draw_text_ms = (time.perf_counter() - t_d) * 1000.0
    p.end()

    ruler = {
        "qt_platform": app.platformName(),
        "font_families": len(QFontDatabase.families()),
        "resolved_family": info.family(),
        "resolved_point": app.font().pointSizeF(),
        "dpr": round(app.primaryScreen().devicePixelRatio(), 3),
        "text": text,
        "text_len": len(text),
    }
    load_after = _load(psutil)
    result = {
        "mode": mode,
        "windows": rows,
        "warm_metrics_ms": round(warm_metrics_ms, 3),
        "consolas_metrics_ms": round(consolas_metrics_ms, 3),
        "draw_text_after_warm_ms": round(draw_text_ms, 3),
        "ruler": ruler,
        "load_before": load_before,
        "load_after": load_after,
        "process_total_ms": round((time.perf_counter() - T0) * 1000.0, 1),
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    try:
        app.quit()
    except Exception:  # noqa: BLE001
        pass
    return 0


# ---------------------------------------------------------------------------
# 驱动：多进程取中位
# ---------------------------------------------------------------------------
def _spawn(mode: str, windows: int, idx: int) -> dict | None:
    cmd = [sys.executable, os.path.abspath(__file__), "--one",
           "--text", mode, "--windows", str(windows)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    print(f"[{mode} run{idx}] 无结果 rc={proc.returncode}\n{proc.stdout[-800:]}\n"
          f"{proc.stderr[-800:]}", flush=True)
    return None


def _driver(args: dict) -> int:
    import psutil

    modes = [m.strip() for m in args["text"].split(",") if m.strip()]
    runs = int(args.get("runs", 3))
    windows = int(args.get("windows", 3))
    out = {"kind": "进程字体/排版地板（最小 QApplication + QWidget + QLabel）",
           "date": "2026-11-12", "runs_per_mode": runs,
           "windows_per_process": windows, "modes": modes, "data": {}}

    for mode in modes:
        got = []
        for i in range(runs):
            before = _wait_low(psutil)
            if before is None:
                print(f"[{mode} run{i}] 低负载窗口不足，跳过", flush=True)
                continue
            r = _spawn(mode, windows, i)
            if r is None:
                continue
            r["load_gate_before_python_pct"] = before["python_total_cpu_pct"]
            got.append(r)
        if not got:
            out["data"][mode] = {"runs": [], "status": "窗口不满足"}
            continue

        def _med(key, w=None):
            vals = []
            for g in got:
                rows = g["windows"]
                if w is not None:
                    rows = [x for x in rows if x["window"] == w]
                vals.extend(x[key] for x in rows)
            return round(statistics.median(vals), 1) if vals else None

        cold = [_w for g in got for _w in g["windows"] if _w["cold"]]
        warm = [_w for g in got for _w in g["windows"] if not _w["cold"]]
        by_window = []
        for w in range(1, len(got[0]["windows"]) + 1):
            vals = [g["windows"][w - 1] for g in got]
            by_window.append({
                "window": w,
                "font_tag": vals[0]["font_tag"],
                "font_family": vals[0]["font_family"],
                "text_len": vals[0]["text_len"],
                "to_paint_dispatch_ms": [x["to_paint_dispatch_ms"] for x in vals],
                "paint_exec_ms": [x["paint_exec_ms"] for x in vals],
                "cycle_to_paint_done_ms": [x["cycle_to_paint_done_ms"] for x in vals],
                "cycle_median_ms": round(statistics.median(
                    [x["cycle_to_paint_done_ms"] for x in vals]), 1),
                "paint_exec_median_ms": round(statistics.median(
                    [x["paint_exec_ms"] for x in vals]), 1),
            })
        out["data"][mode] = {
            "ruler": got[0]["ruler"],
            "by_window": by_window,
            "cold": {
                "app_create_ms": [w["app_create_ms"] for w in cold],
                "font_set_ms": [w["font_set_ms"] for w in cold],
                "build_ms": [w["build_ms"] for w in cold],
                "to_paint_dispatch_ms": [w["to_paint_dispatch_ms"] for w in cold],
                "paint_exec_ms": [w["paint_exec_ms"] for w in cold],
                "cycle_to_paint_done_ms": [w["cycle_to_paint_done_ms"] for w in cold],
                "total_incl_app_ms": [w["total_incl_app_ms"] for w in cold],
                "total_incl_app_median_ms": _med("total_incl_app_ms", 1),
            },
            "warm": {
                "cycle_to_paint_done_ms": [w["cycle_to_paint_done_ms"] for w in warm],
                "to_paint_dispatch_ms": [w["to_paint_dispatch_ms"] for w in warm],
                "paint_exec_ms": [w["paint_exec_ms"] for w in warm],
                "cycle_median_ms": round(statistics.median(
                    [w["cycle_to_paint_done_ms"] for w in warm]), 1) if warm else None,
            },
            "warm_metrics_ms": [g["warm_metrics_ms"] for g in got],
            "consolas_metrics_ms": [g.get("consolas_metrics_ms") for g in got],
            "draw_text_after_warm_ms": [g["draw_text_after_warm_ms"] for g in got],
            "loads": [{"before_python_pct": g["load_before"]["python_total_cpu_pct"],
                       "after_python_pct": g["load_after"]["python_total_cpu_pct"]}
                      for g in got],
            "load_gate_before_python_pct": [g["load_gate_before_python_pct"] for g in got],
            "runs": got,
        }

    os.makedirs(EVID, exist_ok=True)
    fname = args.get("out") or "font-layout-floor.json"
    path = os.path.join(EVID, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[saved] {path}")
    for mode, d in out["data"].items():
        if not d.get("runs"):
            print(f"{mode}: 窗口不满足")
            continue
        print(f"{mode}: cold total median={d['cold']['total_incl_app_median_ms']} "
              f"warm cycle median={d['warm']['cycle_median_ms']} "
              f"warm_metrics={d['warm_metrics_ms']} "
              f"consolas_metrics={d.get('consolas_metrics_ms')}")
        for w in d.get("by_window", []):
            print(f"  win{w['window']} {w['font_tag']} {w['font_family']}: "
                  f"cycle={w['cycle_to_paint_done_ms']} "
                  f"paint={w['paint_exec_ms']}")
    return 0


def main() -> int:
    args = {"text": "chinese", "runs": 3, "windows": 3, "out": None}
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--text" and i + 1 < len(argv):
            args["text"] = argv[i + 1]
        elif a == "--runs" and i + 1 < len(argv):
            args["runs"] = argv[i + 1]
        elif a == "--windows" and i + 1 < len(argv):
            args["windows"] = argv[i + 1]
        elif a == "--out" and i + 1 < len(argv):
            args["out"] = argv[i + 1]
    if "--one" in argv:
        return _measure(args)
    return _driver(args)


if __name__ == "__main__":
    sys.exit(main())

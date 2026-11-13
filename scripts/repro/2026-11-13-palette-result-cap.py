# -*- coding: utf-8 -*-
"""命令面板「结果行数上限」前后对比复跑脚本（2026-11-13）。

目的：验证搜索慢的瓶颈是**渲染命中的行数**，并给出加结果上限前后的
同机、同输入、同样本对比数字。脚本对改动前后两种代码都可用：

  * 旧代码（无 ``MAX_RENDERED_RESULTS``）：全部命中都进渲染队列；
  * 新代码：默认按产品常量截断；``--mode before`` 把常量临时放大到无穷，
    用来在同一进程内做「去掉上限」的反证对照。

用法：
    python scripts/repro/2026-11-13-palette-result-cap.py --run before-oldcode
    python scripts/repro/2026-11-13-palette-result-cap.py --run after
    python scripts/repro/2026-11-13-palette-result-cap.py --run before-newcode \\
        --mode before

样本（固定合成，不随环境变化）：500 条书签形命令 + N 条卫星形命令。
每个 query 预热 2 次、采样 7 次，报中位/最坏。计时窗口只包含
``search_commands``（过滤）与 ``CommandPalette._refresh``（过滤 + 渲染，
不含 QApplication 创建/import），prelude 在窗口外。
测量前后各采一次机器负载（系统 CPU、python 进程数与各自 CPU 占比）。
结果追加进 ``docs/perf-evidence/2026-11-13/palette-result-cap.json``。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

REPO = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, "docs", "perf-evidence", "2026-11-13",
                   "palette-result-cap.json")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "desktop"))

SAT_SIZES = (2000, 10000)
QUERIES = ("", "sat")
ROUNDS = 7
WARMUP = 2


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
    return {
        "sys_cpu_pct": round(psutil.cpu_percent(interval=sec), 1),
        "cpu_count": psutil.cpu_count(),
        "python_proc_count": len(rows),
        "python_total_cpu_pct": round(sum(r["cpu_pct"] for r in rows), 1),
        "python_top": rows[:5],
    }


def _make_commands(cp, sat_count):
    out = []
    for i in range(500):
        freq = 145_000_000 + i * 12_500
        out.append(cp.Command(
            title=f"书签 {i:03d}",
            group="书签",
            description=f"书签：本地中继 {i}（FM，分组 测试）",
            trigger=f"{freq / 1e6:g} MHz · {freq} Hz · FM",
            source=f"bookmark:{freq}:书签 {i:03d}",
            run=lambda: {"ok": True},
        ))
    for i in range(sat_count):
        out.append(cp.Command(
            title=f"SAT-{i:05d}",
            group="卫星",
            description="卫星跟踪：跳到该卫星并让「卫星」下拉选中它（星历：预推）",
            trigger=f"NORAD {i + 10000} · 预推",
            source=f"sat:{i + 10000}",
            run=lambda: {"ok": True},
        ))
    return out


def _bench(cp, dlg, query):
    filter_ms, refresh_ms = [], []
    for n in range(WARMUP + ROUNDS):
        t0 = time.perf_counter()
        cp.search_commands(dlg._commands, query, recent=())
        t1 = time.perf_counter()
        dlg._refresh(query)
        t2 = time.perf_counter()
        if n >= WARMUP:
            filter_ms.append((t1 - t0) * 1000.0)
            refresh_ms.append((t2 - t0) * 1000.0)
    hidden = (dlg.hidden_count() if hasattr(dlg, "hidden_count") else None)
    return {
        "query": query,
        "hits": len(dlg._results),
        "list_rows": dlg.list_widget.count(),
        "hidden": hidden,
        "filter_median_ms": round(statistics.median(filter_ms), 2),
        "filter_worst_ms": round(max(filter_ms), 2),
        "refresh_median_ms": round(statistics.median(refresh_ms), 2),
        "refresh_worst_ms": round(max(refresh_ms), 2),
        "reason_tail": dlg.reason_label.text()[-80:],
    }


def main():
    run = "?"
    mode = "auto"
    cap_override = None
    if "--run" in sys.argv:
        run = sys.argv[sys.argv.index("--run") + 1]
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    if "--cap" in sys.argv:
        cap_override = int(sys.argv[sys.argv.index("--cap") + 1])

    import psutil
    from PySide6.QtWidgets import QApplication
    import PySide6
    import command_palette as cp

    load_before = _sample_load(psutil)
    app = QApplication.instance() or QApplication(sys.argv[:1])

    cap = getattr(cp, "MAX_RENDERED_RESULTS", None)
    if mode == "before":
        cp.MAX_RENDERED_RESULTS = 10 ** 9
    if cap_override is not None:
        cp.MAX_RENDERED_RESULTS = cap_override
    effective_cap = getattr(cp, "MAX_RENDERED_RESULTS", None)

    print(f"repo={REPO}")
    print(f"run={run} mode={mode} python={sys.version.split()[0]} "
          f"qt={PySide6.__version__}")
    print(f"product_cap={cap!r} effective_cap={effective_cap!r}")

    samples = []
    try:
        for sat_count in SAT_SIZES:
            cmds = _make_commands(cp, sat_count)
            dlg = cp.CommandPalette(window=None)
            dlg._commands = cmds
            print(f"--- 索引 {len(cmds)} 条（500 书签 + {sat_count} 卫星）---")
            for query in QUERIES:
                row = _bench(cp, dlg, query)
                print(row)
                row["index_size"] = len(cmds)
                row["sat_count"] = sat_count
                samples.append(row)
            dlg.deleteLater()
            app.processEvents()
        load_after = _sample_load(psutil)
    except Exception as exc:            # 留证后原样上抛
        import traceback
        traceback.print_exc()
        print(f"[失败] {type(exc).__name__}: {exc}")
        return 2

    print(f"[load-before] sys_cpu={load_before['sys_cpu_pct']}% "
          f"cpus={load_before['cpu_count']} "
          f"py_procs={load_before['python_proc_count']} "
          f"py_total={load_before['python_total_cpu_pct']}%")
    print(f"[load-after]  sys_cpu={load_after['sys_cpu_pct']}% "
          f"py_procs={load_after['python_proc_count']} "
          f"py_total={load_after['python_total_cpu_pct']}%")

    record = {
        "run": run,
        "mode": mode,
        "product_cap": cap,
        "effective_cap": effective_cap,
        "python": sys.version.split()[0],
        "qt": PySide6.__version__,
        "platform": app.platformName(),
        "sample": {"bookmarks": 500,
                   "satellites": list(SAT_SIZES),
                   "warmup": WARMUP, "rounds": ROUNDS,
                   "queries": list(QUERIES)},
        "load_before": load_before,
        "load_after": load_after,
        "samples": samples,
    }
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

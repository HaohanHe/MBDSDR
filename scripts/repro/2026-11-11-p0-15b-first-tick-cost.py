#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-15b 独立复现脚本：Agent 内核从构造期挪到「首个事件循环拍」后的代价。

背景：W24（`1fdf1958`）把 ``_ensure_agent_kernel`` 从构造期改成
``QTimer.singleShot(0, …)``。窗口确实先出来了，但首拍仍要装载 496 个工具，
用户照样干等 2.2 s——**延后不等于消失**。本脚本回答三件事：

  1. 首拍里谁在花时间：内核自身 / 它触发的模块导入 / 设备与文件探测各占多少；
  2. 能不能再挪到「用户第一次打开能力矩阵」：用 QTimer 代理截获那次
     ``singleShot(0, _ensure_agent_kernel)``，改在 ``_show_capability_drawer()``
     时执行（不改产品代码，只在探针进程里模拟）；
  3. 挪走后首拍降到多少、代价转移到哪里（首次打开多少 ms）。

测法：同一隔离 profile、同一入口（真 ``MainWindow``）。三种形态各
起一个**独立子进程**——同一进程里第二次跑时模块已全部导入，会把首拍测成
0 ms（这本身就是「导入占大头」的证据，但不能当读数用）。

**尺子（默认 offscreen，``--screen`` 换真屏指纹）**：offscreen 下
``QFontDatabase.families()`` 为 0，字体按方框度量（实测行高 13 px，真屏
DPR 1.0 + MiSans 10pt 为 27 px，差近 2 倍），所以绝对毫秒数不能直接当
用户体感。``--screen`` 固定 ``QT_QPA_PLATFORM=windows`` +
``QT_SCALE_FACTOR=1`` + ``QT_ENABLE_HIGHDPI_SCALING=0``，并走生产入口
``app.setFont(get_app_font())``（``desktop/main.py:709``，MiSans 10pt）；
字体库为 0 时直接退出码 2，绝不出数。``--show`` 再进一步按生产顺序
``show()`` 后再量首拍（窗口用 ``WA_DontShowOnScreen`` 不上屏，与
``scripts/ui_narrow_breakdown.py`` 同口径）：此时首拍含布局/抛光，更接近
用户真实等待。两个开关正交，四格读数都要带尺子标注。

注意：**不要**在这条链上 patch ``builtins.__import__`` 做导入计时。实测它会让
HEAD 的 ``eventFilter``（``main_window.py:3792``）递归报
``RuntimeError`` 并踩到已删除的 ``QVBoxLayout``。归因改用 cProfile：它只在
profiler 层记账，不动 builtins。cProfile 会放大墙钟（约 1.7–1.9×），所以
**墙钟以 ``baseline`` 子进程为准，profile 子进程只提供占比**。

退出码（parent，即不带参数直接跑）：
    0 = 复现：基线首拍仍 ≥ 1000 ms
    1 = 未复现：基线首拍 < 1000 ms
    2 = 无法判定：任一子进程环境失败

子进程：
    --mode baseline  退出码 0/1 同「首拍是否 ≥ 1000 ms」，2=环境失败
    --mode profile   同上，但首拍在 cProfile 下跑（读数带 profiled 标注）
    --mode deferred  退出码 0 = 挪走后首拍 < 1000 ms（方案可行），
                     1 = 挪走后首拍仍 ≥ 1000 ms，2 = 环境失败

用法：
    python scripts/repro/2026-11-11-p0-15b-first-tick-cost.py            # offscreen
    python scripts/repro/2026-11-11-p0-15b-first-tick-cost.py --screen --show
    $env:MBDSDR_REPRO_ROOT="D:\\mbdsdr\\output\\tmp\\p0-head-c3b48881"
    python scripts/repro/2026-11-11-p0-15b-first-tick-cost.py --screen --show
"""
from __future__ import annotations

import cProfile
import json
import os
import pstats
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"
SLOW_MS = 1000.0
#: 真屏指纹：windows 插件 + DPR 1.0（关高 DPI）+ 生产字体 MiSans 10pt。
SCREEN_ENV = {
    "QT_QPA_PLATFORM": "windows",
    "QT_SCALE_FACTOR": "1",
    "QT_ENABLE_HIGHDPI_SCALING": "0",
}


def _flags(argv) -> tuple[bool, bool]:
    """(screen, show)：尺子与是否按生产顺序上屏，两个变量正交。"""
    return ("--screen" in argv, "--show" in argv)


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _prepare_env(screen: bool) -> str:
    os.makedirs(TMP_ROOT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="p0-15b-profile-", dir=TMP_ROOT)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    if screen:
        os.environ.update(SCREEN_ENV)
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    return profile


class _TimerProxy:
    """``QTimer`` 代理：只截获 ``singleShot(0, <_ensure_agent_kernel>)``。

    其余属性/实例化原样转发；截获的那次不排程，交给调用方在「首次打开」
    时手动执行（模拟把装载再往后挪一档）。
    """

    def __init__(self, real, captured):
        self._real = real
        self._captured = captured

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __call__(self, *a, **k):
        return self._real(*a, **k)

    def singleShot(self, msec, *args):  # noqa: N802（Qt 命名）
        fn = args[-1] if args else None
        if (msec == 0 and callable(fn)
                and getattr(fn, "__name__", "").endswith("ensure_agent_kernel")):
            self._captured.append(fn)
            return None
        return self._real.singleShot(msec, *args)


def _stat_rows(pr) -> dict:
    """从 cProfile 结果里取首拍归因：导入 / open / listdir / 内核与 init_agent。"""
    rows = pstats.Stats(pr).stats
    out = {"import": None, "open": None, "scan": [], "modules": [],
           "kernel": None, "init_agent": None}

    def _pick(pred):
        best = None
        for (f, line, fn), (_cc, nc, tt, ct, _callers) in rows.items():
            if pred(f, fn) and (best is None or ct > best[1]):
                best = ((f, line, fn), ct, tt, nc)
        return best

    imp = _pick(lambda f, fn: fn == "_find_and_load")
    if imp:
        out["import"] = {"ms": imp[1] * 1000, "calls": imp[3]}
    op = _pick(lambda f, fn: "builtins.open" in fn)
    if op:
        out["open"] = {"ms": op[2] * 1000, "calls": op[3]}
    for (f, line, fn), (_cc, nc, tt, ct, _c) in rows.items():
        if fn in ("scandir", "listdir", "walk"):
            out["scan"].append((fn, tt * 1000, nc))
    builtins_rows = [((f, fn), tt * 1000, nc) for (f, line, fn),
                     (_cc, nc, tt, ct, _c) in rows.items()
                     if fn.startswith("{built-in") or fn.startswith("{method")]
    out["builtins"] = [
        {"name": fn, "ms": ms, "calls": nc}
        for (f, fn), ms, nc in sorted(builtins_rows, key=lambda r: -r[1])[:6]
        if ms >= 1.0]
    mods = [((f, fn), ct, tt) for (f, line, fn), (_cc, nc, tt, ct, _c)
            in rows.items() if fn == "<module>"]
    for (f, fn), ct, tt in sorted(mods, key=lambda r: -r[1])[:8]:
        out["modules"].append({"file": f, "ms": ct * 1000})
    ker = _pick(lambda f, fn: fn == "_probe_ensure_agent_kernel")
    if ker:
        out["kernel"] = {"ms": ker[1] * 1000, "calls": ker[3]}
    ini = _pick(lambda f, fn: fn == "_probe_init_agent")
    if ini:
        out["init_agent"] = {"ms": ini[1] * 1000, "calls": ini[3]}
    return out


def _run(mode: str, screen: bool = False, show: bool = False) -> int:
    profile = _prepare_env(screen)
    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"mode   : {mode}")
    print(f"尺子   : {'真屏指纹 windows/DPR1/MiSans 10pt' if screen else 'offscreen'}"
          f"{'，按生产顺序 show() 后量首拍' if show else ''}")
    print(f"临时档案: {profile}（HOME/USERPROFILE/APPDATA/LOCALAPPDATA 全重定向）")

    try:
        from PySide6.QtCore import QSettings, Qt, QTimer
        from PySide6.QtGui import QFontDatabase, QFontMetrics
        from PySide6.QtWidgets import QApplication
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        import layout_store as ls
        ls.reset_layout_store_singleton()
        ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
        app = QApplication.instance() or QApplication(sys.argv)
        fams = QFontDatabase.families()
        if screen:
            from themes import get_app_font
            app.setFont(get_app_font())      # 与 desktop/main.py:709 同一条生产入口
            if not fams:
                print("[无法判定] QFontDatabase.families() 为 0：字体退化成方框，"
                      "尺寸/耗时不代表真屏。")
                shutil.rmtree(profile, ignore_errors=True)
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
        import ai_panel as ap
        import main_window as mw
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] Qt/MainWindow 导入失败：{type(exc).__name__}: {exc}")
        shutil.rmtree(profile, ignore_errors=True)
        return 2

    kernel_rows = []
    agent_rows = []

    orig_kernel = mw.MainWindow._ensure_agent_kernel
    orig_init_agent = ap.AIPanel.init_agent

    def _probe_ensure_agent_kernel(self):
        t0 = time.perf_counter()
        try:
            return orig_kernel(self)
        finally:
            kernel_rows.append((time.perf_counter() - t0) * 1000.0)

    def _probe_init_agent(self, *a, **k):
        t0 = time.perf_counter()
        try:
            return orig_init_agent(self, *a, **k)
        finally:
            agent_rows.append((time.perf_counter() - t0) * 1000.0)

    mw.MainWindow._ensure_agent_kernel = _probe_ensure_agent_kernel
    ap.AIPanel.init_agent = _probe_init_agent

    captured = []
    if mode == "deferred":
        mw.QTimer = _TimerProxy(QTimer, captured)

    _hr(f"构造 MainWindow（{mode}），分「构造期 / 首拍」两段")
    window = None
    try:
        t_ctor = time.perf_counter()
        window = mw.MainWindow()
        ctor_ms = (time.perf_counter() - t_ctor) * 1000.0
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] MainWindow 构造失败：{type(exc).__name__}: {exc}")
        shutil.rmtree(profile, ignore_errors=True)
        return 2
    ctor_kernel = list(kernel_rows)
    ctor_agent = list(agent_rows)
    agent_at_ctor = getattr(window.ai_panel, "agent", None) is not None

    pr = None
    if mode == "profile":
        pr = cProfile.Profile()
        pr.enable()
    t_tick = time.perf_counter()
    if show:
        # 生产顺序：main.py 构造完立刻 show()，0 延迟定时器在首拍（含布局/抛光）
        # 才执行。WA_DontShowOnScreen 与 ui_narrow_breakdown 同口径，不上屏。
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
        window.show()
    app.processEvents()
    tick_ms = (time.perf_counter() - t_tick) * 1000.0
    if pr is not None:
        pr.disable()
    tick_kernel = list(kernel_rows[len(ctor_kernel):])
    tick_agent = list(agent_rows[len(ctor_agent):])
    label = "（profiled，墙钟被放大）" if pr is not None else ""

    print(f"构造期          : {ctor_ms:.0f} ms，_ensure_agent_kernel "
          f"{len(ctor_kernel)} 次 / {sum(ctor_kernel):.0f} ms，"
          f"agent 非空={agent_at_ctor}")
    print(f"首个事件循环拍  : {tick_ms:.0f} ms{label}，_ensure_agent_kernel "
          f"{len(tick_kernel)} 次 / {sum(tick_kernel):.0f} ms，"
          f"init_agent {len(tick_agent)} 次 / "
          f"{sum(tick_agent):.0f} ms")

    stats = {}
    if pr is not None:
        stats = _stat_rows(pr)
        if stats["import"]:
            print(f"  首拍内导入    : {stats['import']['calls']} 次 / "
                  f"{stats['import']['ms']:.0f} ms（importlib 累计）")
        for m in stats["modules"]:
            print(f"    {m['ms']:8.0f} ms  {m['file'][-96:]}")
        if stats["open"]:
            print(f"  首拍内 open() : {stats['open']['calls']} 次 / "
                  f"{stats['open']['ms']:.0f} ms（tottime）")
        for b in stats.get("builtins", []):
            print(f"  内置调用      : {b['name'][:52]:52s} "
                  f"{b['calls']:6d} 次 / {b['ms']:6.0f} ms")
        for fn, ms, nc in sorted(stats["scan"], key=lambda r: -r[1])[:4]:
            print(f"  首拍内 {fn:10s}: {nc} 次 / {ms:.0f} ms（tottime）")
        if stats.get("kernel"):
            extra = (f"；其中 init_agent {stats['init_agent']['ms']:.0f} ms"
                     if stats.get("init_agent") else "")
            print(f"  内核 cumtime  : {stats['kernel']['ms']:.0f} ms{extra}")

    open_cost = None
    if mode == "deferred":
        if len(captured) != 1:
            print(f"[无法判定] 预期截获 1 次内核排程，实际 {len(captured)} 次")
            shutil.rmtree(profile, ignore_errors=True)
            return 2
        before = len(kernel_rows)
        t_open = time.perf_counter()
        captured[0]()                      # 模拟「首次打开能力矩阵」时才装载
        window._refresh_capability_drawer()
        app.processEvents()
        open_cost = (time.perf_counter() - t_open) * 1000.0
        print(f"  首次打开代价  : {open_cost:.0f} ms（内核 "
              f"{sum(kernel_rows[before:]):.0f} ms + 抽屉刷新）")
        print(f"  挪走后首拍    : {tick_ms:.0f} ms（首拍已不含内核）")

    result = {
        "mode": mode,
        "ruler": ruler,
        "screen": screen,
        "shown": show,
        "ctor_ms": round(ctor_ms, 1),
        "ctor_kernel_calls": len(ctor_kernel),
        "ctor_kernel_ms": round(sum(ctor_kernel), 1),
        "agent_at_ctor": agent_at_ctor,
        "tick_ms": round(tick_ms, 1),
        "tick_profiled": pr is not None,
        "tick_kernel_calls": len(tick_kernel),
        "tick_kernel_ms": round(sum(tick_kernel), 1),
        "tick_init_agent_ms": round(sum(tick_agent), 1),
        "tick_import_ms": round((stats.get("import") or {}).get("ms", 0.0), 1)
        if stats else None,
        "tick_open_calls": (stats.get("open") or {}).get("calls") if stats else None,
        "tick_open_ms": round((stats.get("open") or {}).get("ms", 0.0), 1)
        if stats else None,
        "first_open_ms": None if open_cost is None else round(open_cost, 1),
    }
    print("RESULT " + json.dumps(result, ensure_ascii=False), flush=True)

    # 结果先落盘再退出：HEAD 的 eventFilter 在窗口析构 / 解释器收尾
    # （touch_helpers.event → main_window.eventFilter）上会踩到已删除的
    # QVBoxLayout，进程以 0xC0000005 退出；父进程只认结果文件，退出码不作为
    # 判据（避免「测到了却算无法判定」）。
    out_file = os.environ.get("MBDSDR_15B_RESULT_FILE")
    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)

    # 不调用 window.close()，也不走解释器正常退出：HEAD 的 eventFilter 在
    # 窗口析构链 / atexit（touch_helpers.event → main_window.eventFilter）上
    # 会踩到已删除的 QVBoxLayout，进程以非 0 退出且可能丢缓冲输出。
    # 子进程一测一退，直接 os._exit 保住退出码语义。
    shutil.rmtree(profile, ignore_errors=True)
    if mode == "deferred":
        # 实验形态：0 = 挪走后首拍已降到阈值下（方案可行）
        code = 0 if tick_ms < SLOW_MS else 1
        print(f"\n[{'可行' if code == 0 else '不可行'}] 挪到首次打开后首拍 "
              f"{tick_ms:.0f} ms，首次打开代价 {open_cost:.0f} ms。", flush=True)
    else:
        code = 0 if tick_ms >= SLOW_MS else 1
        print(f"\n[{'复现' if code == 0 else '未复现'}] 首拍 {tick_ms:.0f} ms "
              f"{'≥' if code == 0 else '<'} {SLOW_MS:.0f} ms。", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _parent(screen: bool = False, show: bool = False) -> int:
    env = os.environ.copy()
    results = {}
    child_flags = (["--screen"] if screen else []) + (["--show"] if show else [])
    for mode in ("baseline", "profile", "deferred"):
        print("\n" + "#" * 78)
        print(f"# 子进程：{mode}")
        print("#" * 78)
        out_file = os.path.join(
            TMP_ROOT, f"p0-15b-result-{mode}-{os.getpid()}.json")
        if os.path.exists(out_file):
            os.remove(out_file)
        child_env = dict(env, MBDSDR_15B_RESULT_FILE=out_file)
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--mode", mode]
            + child_flags,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=child_env, timeout=1200)
        print(proc.stdout)
        if proc.stderr.strip():
            print(proc.stderr, file=sys.stderr)
        if os.path.exists(out_file):
            with open(out_file, "r", encoding="utf-8") as f:
                results[mode] = json.load(f)
            os.remove(out_file)
        else:
            print(f"[无法判定] 子进程 {mode} 失败（退出码 {proc.returncode}，"
                  "无结果文件）")
            return 2

    b = results["baseline"]
    d = results["deferred"]
    p = results["profile"]
    _hr("汇总（同一快照、同一隔离 profile、三个独立进程）")
    rl = b.get("ruler") or {}
    print(f"快照源码根: {ROOT}")
    print(f"尺子: {rl.get('qt_platform')} DPR {rl.get('device_pixel_ratio')} "
          f"逻辑 DPI {rl.get('logical_dpi')} | 字体 {rl.get('font_family')} "
          f"{rl.get('font_point_size')}pt 行高 {rl.get('font_line_height')} px "
          f"| 字体库 {rl.get('font_families')} 个 "
          f"| scale_factor {rl.get('scale_factor')} "
          f"| show={b.get('shown')}")
    print(f"{'形态':18s} {'构造':>7s} {'构造期内核':>10s} {'首拍':>8s} "
          f"{'首拍内核':>9s} {'内核占比':>9s} {'首次打开':>9s}")
    for label, r in (("基线", b), ("基线（profiled）", p), ("模拟：首次打开", d)):
        share = (f"{r['tick_kernel_ms'] / r['tick_ms'] * 100:.0f}%"
                 if r["tick_ms"] else "—")
        print(f"{label:18s} {r['ctor_ms']:7.0f} {r['ctor_kernel_ms']:10.0f} "
              f"{r['tick_ms']:8.0f} {r['tick_kernel_ms']:9.0f} "
              f"{share:>9s} {(r['first_open_ms'] or 0):9.0f}")
    if b["tick_kernel_ms"]:
        print(f"\n首拍内核占比（基线）: {b['tick_kernel_ms']:.0f} / "
              f"{b['tick_ms']:.0f} ms = "
              f"{b['tick_kernel_ms'] / b['tick_ms'] * 100:.0f}%")
    if p.get("tick_import_ms"):
        print(f"首拍导入（profiled 归因）: {p['tick_import_ms']:.0f} ms，"
              f"占 profiled 首拍 {p['tick_import_ms'] / p['tick_ms'] * 100:.0f}%")

    tick = b["tick_ms"]
    if tick >= SLOW_MS:
        print(f"\n[复现] 基线首拍 {tick:.0f} ms ≥ {SLOW_MS:.0f} ms；"
              "挪到首次打开后首拍 "
              f"{d['tick_ms']:.0f} ms、代价 "
              f"{(d['first_open_ms'] or 0):.0f} ms。")
        return 0
    print(f"\n[未复现] 基线首拍 {tick:.0f} ms < {SLOW_MS:.0f} ms。")
    return 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    screen_flag, show_flag = _flags(argv)
    if "--mode" in argv:
        raise SystemExit(_run(argv[argv.index("--mode") + 1],
                              screen=screen_flag, show=show_flag))
    try:
        raise SystemExit(_parent(screen=screen_flag, show=show_flag))
    except subprocess.TimeoutExpired:
        print("[无法判定] 子进程超时（1200 s）")
        raise SystemExit(2)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)

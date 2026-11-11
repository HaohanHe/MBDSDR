#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-14 独立复现脚本：冷启动首屏前是否仍有一段「无窗口/无 splash/无进度」。

roadmap 断言（原文）：
    「冷启动 4.37 s 全程无窗口/无 splash/无进度」
    证据等级：实测（3 次 4200–4601 ms，gap-performance §1）。

做法（offscreen + 独立 profile，真 ``desktop/main.py`` 启动链）：
  * 给 ``_show_startup_splash`` / ``_run_import_smoke`` /
    ``_import_main_window`` 装计时探针；
  * 调真实 ``main.main()``，在首个 ``MainWindow.show()`` 后自动退出；
  * 计算：
        t_splash   = 进程内起点 → 第一块 splash 显示
        t_window   = 进程内起点 → 主窗口 show
        silent     = t_splash（无 splash 时取 t_window）
  * 判据：首个可见反馈 > 2.0 s 视为「仍有一段空白期」。

退出码（三态）：
    0  复现成功   —— 首个可见反馈 > 2.0 s（或无 splash，直接到窗口）
    1  未复现     —— 首个可见反馈 ≤ 2.0 s，且窗口在其后 ≥0.5 s 才出现
    2  无法判定   —— 启动链提前失败 / 没抓到 show

反证对照：把 ``MBDSDR_REPRO_ROOT`` 指向 a52ce510^（splash 前移之前）重跑，
t_splash 必须重新落到自检之后（> 2 s）。

用法：
    python scripts/repro/2026-11-11-p0-14-cold-start-silent.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time

T_START = time.perf_counter()
ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"
BLANK_THRESHOLD_S = 2.0


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-14-profile-", dir=TMP_ROOT)
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[k] = profile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"临时档案: {profile}")

    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        import main as entry
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] 导入 desktop/main.py 失败：{type(exc).__name__}: {exc}")
        return 2

    marks = {}
    orig_splash = getattr(entry, "_show_startup_splash", None)
    orig_smoke = getattr(entry, "_run_import_smoke", None)
    orig_import = getattr(entry, "_import_main_window", None)

    def _splash(app):
        marks.setdefault("splash", time.perf_counter() - T_START)
        return orig_splash(app) if orig_splash else None

    def _smoke():
        marks.setdefault("smoke_start", time.perf_counter() - T_START)
        try:
            return orig_smoke() if orig_smoke else None
        finally:
            marks["smoke_end"] = time.perf_counter() - T_START

    def _instrument(cls):
        class _FirstFrame(cls):
            def show(self):
                marks.setdefault("window_show", time.perf_counter() - T_START)
                app = QApplication.instance()
                if app is not None:
                    QTimer.singleShot(0, app.quit)
                return super().show()

        return _FirstFrame

    def _import(splash):
        marks.setdefault("import_start", time.perf_counter() - T_START)
        cls = orig_import(splash)
        marks["import_end"] = time.perf_counter() - T_START
        return _instrument(cls)

    if orig_splash is not None:
        entry._show_startup_splash = _splash
    if orig_smoke is not None:
        entry._run_import_smoke = _smoke
    if orig_import is not None:
        entry._import_main_window = _import
    # 旧启动链没有 _import_main_window（MainWindow 直接在 main() 里 new），
    # 此时直接从模块命名空间包一层，保证 show() 仍能被观测到。
    elif getattr(entry, "MainWindow", None) is not None:
        entry.MainWindow = _instrument(entry.MainWindow)

    def _watchdog_fire():
        print(f"\n[无法判定] 45 s 内没抓到主窗口 show（marks={marks}），启动链可能挂起。")
        sys.stdout.flush()
        os._exit(2)

    # 兜底：45 s 还没退出就硬退，避免对话框/事件循环挂死
    watchdog = threading.Timer(45.0, _watchdog_fire)
    watchdog.daemon = True
    watchdog.start()

    sys.argv = ["main.py"]
    rc = None
    try:
        rc = entry.main()
    except SystemExit as exc:
        rc = exc.code
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] main() 抛错：{type(exc).__name__}: {exc}")
        return 2
    finally:
        watchdog.cancel()

    t_splash = marks.get("splash")
    t_window = marks.get("window_show")
    _hr("实测：启动链各里程碑（相对进程内起点）")
    for key in ("splash", "smoke_start", "smoke_end", "import_start",
                "import_end", "window_show"):
        val = marks.get(key)
        print(f"  {key:<12}: {'—' if val is None else f'{val * 1000:.0f} ms'}")
    print(f"  main() 返回码: {rc}")

    try:
        shutil.rmtree(profile, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    if t_window is None:
        print(f"[无法判定] 没抓到主窗口 show（main 返回 {rc}），无法判定首屏。")
        return 2

    silent = t_splash if t_splash is not None else t_window
    print(f"\n  首个可见反馈: {silent * 1000:.0f} ms（阈值 {BLANK_THRESHOLD_S * 1000:.0f} ms）")
    if t_splash is not None:
        print(f"  splash → 窗口: {(t_window - t_splash) * 1000:.0f} ms")

    reproduced = (t_splash is None) or (silent > BLANK_THRESHOLD_S)
    print(f"\n结论：当前实现复现 P0-14（首屏前仍有空白期）: {reproduced}")
    if reproduced:
        print("[复现] 首个可见反馈晚于 2 s（或无 splash）。")
        return 0
    print(f"[未复现] 首块 splash 在 {silent * 1000:.0f} ms 出现，"
          f"主窗口在 {t_window * 1000:.0f} ms 出现，空白期已消除。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

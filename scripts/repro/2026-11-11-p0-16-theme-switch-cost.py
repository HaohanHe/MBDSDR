#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-16 同口径复测：切主题的控件数 / setStyleSheet 次数 / 墙钟 / CPU 时间。

背景：同一 HEAD 上两份数字差 10 倍（本线 4527/4387/3269 ms、性能线 386–540 ms），
必须先排除「入口不同 / 负载不同 / 快照不同」。本脚本固定：

  * 入口：``MainWindow._apply_theme``（直调），另跑一遍工具栏 ``theme_combo``
    的真实信号路径做旁证（两者应当同量级，否则说明入口本身有差）；
  * 负载：同一个进程、同一份隔离 profile、同一套主题序列 dark/default/dark；
  * 读数：控件总数 / 切换期 ``setStyleSheet`` 调用次数 / 墙钟 / ``process_time``。

退出码：0 = 仍然慢（任一次墙钟 ≥ 1000 ms）；1 = 已改善（全部 < 1000 ms）；
        2 = 环境不满足（Qt/构造失败）。

``--touch``：先切到触控模式再跑同一序列。触控模式会在主题 QSS 后追加
触控规则（`_apply_touch_stylesheet`），历史上每次切主题会多一次应用级
``setStyleSheet``（2.1–3.7 s）。**鼠标与触控必须各跑一次、各占一行**，
不许合并成一个数。

用法：
    $env:MBDSDR_REPRO_ROOT="<快照或基线根>"
    python scripts/repro/2026-11-11-p0-16-theme-switch-cost.py
    python scripts/repro/2026-11-11-p0-16-theme-switch-cost.py --touch
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"
SLOW_MS = 1000.0


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    os.makedirs(TMP_ROOT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="p0-16c-profile-", dir=TMP_ROOT)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication, QWidget
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        import layout_store as ls
        ls.reset_layout_store_singleton()
        ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
        app = QApplication.instance() or QApplication(sys.argv)
        import main_window as mw
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] Qt/MainWindow 导入失败：{type(exc).__name__}: {exc}")
        shutil.rmtree(profile, ignore_errors=True)
        return 2

    # setStyleSheet 计数：QWidget（控件级）与 QApplication（app 级）分开记。
    counter = {"widget": 0, "app": 0}
    orig_widget_ss = QWidget.setStyleSheet
    orig_app_ss = QApplication.setStyleSheet

    def _count_widget(self, *a, **k):
        counter["widget"] += 1
        return orig_widget_ss(self, *a, **k)

    def _count_app(self, *a, **k):
        counter["app"] += 1
        return orig_app_ss(self, *a, **k)

    QWidget.setStyleSheet = _count_widget
    QApplication.setStyleSheet = _count_app

    window = None
    try:
        _hr("构造 MainWindow（隔离 profile，offscreen）")
        t0 = time.perf_counter()
        window = mw.MainWindow()
        app.processEvents()
        print(f"构造耗时: {(time.perf_counter() - t0) * 1000:.1f} ms")
        widgets = len(QApplication.allWidgets())
        print(f"控件总数: {widgets}")

        touch = "--touch" in sys.argv
        if touch:
            # 触控模式下 QSS 带追加后缀，走的是 _apply_touch_stylesheet 那条路径。
            # 切换本身不算主题耗时，单独计时后清零。
            from touch_manager import touch_manager
            touch_manager().set_from_settings("on")
            t_touch = time.perf_counter()
            window._apply_touch_adaptation()
            app.processEvents()
            print(f"触控模式适配（不计入切主题）: "
                  f"{(time.perf_counter() - t_touch) * 1000:.1f} ms；"
                  f"is_touch_mode={touch_manager().is_touch_mode()}")
        print(f"模式: {'触控' if touch else '鼠标'}")

        seq = ("dark", "default", "dark")

        def _run(label: str, apply):
            rows = []
            for name in seq:
                counter["widget"] = 0
                counter["app"] = 0
                w0, c0 = time.perf_counter(), time.process_time()
                apply(name)
                app.processEvents()
                wall = (time.perf_counter() - w0) * 1000
                cpu = (time.process_time() - c0) * 1000
                rows.append((name, wall, cpu, counter["widget"], counter["app"]))
                print(f"  {label} -> {name:8s} 墙钟 {wall:8.1f} ms  "
                      f"CPU {cpu:8.1f} ms  setStyleSheet 控件/应用 = "
                      f"{counter['widget']}/{counter['app']}  "
                      f"QSS 长度 {len(app.styleSheet())}")
            return rows

        _hr("入口 A：直调 MainWindow._apply_theme")
        rows_a = _run("_apply_theme", window._apply_theme)

        _hr("入口 B：工具栏 theme_combo 真实信号路径")
        rows_b = []
        for name in seq:
            idx = window.theme_combo.findData(name)
            if idx < 0:
                print(f"  [跳过] theme_combo 无 {name}")
                continue
            counter["widget"] = 0
            counter["app"] = 0
            w0, c0 = time.perf_counter(), time.process_time()
            window.theme_combo.setCurrentIndex(idx)
            app.processEvents()
            wall = (time.perf_counter() - w0) * 1000
            cpu = (time.process_time() - c0) * 1000
            rows_b.append((name, wall, cpu, counter["widget"], counter["app"]))
            print(f"  combo -> {name:8s} 墙钟 {wall:8.1f} ms  CPU {cpu:8.1f} ms  "
                  f"setStyleSheet 控件/应用 = {counter['widget']}/{counter['app']}")

        _hr("汇总（固定四行）")
        for label, rows in (("A 直调", rows_a), ("B 下拉", rows_b)):
            if not rows:
                continue
            walls = [r[1] for r in rows]
            cpus = [r[2] for r in rows]
            wss = sum(r[3] + r[4] for r in rows)
            print(f"{label}: 控件 {widgets} | setStyleSheet {wss} 次 | "
                  f"墙钟 {min(walls):.0f}–{max(walls):.0f} ms（中位 "
                  f"{sorted(walls)[len(walls) // 2]:.0f}）| CPU "
                  f"{min(cpus):.0f}–{max(cpus):.0f} ms")

        worst = max([r[1] for r in rows_a] + [r[1] for r in rows_b or []] or [0.0])
        if worst >= SLOW_MS:
            print(f"\n[复现] 最慢一次 {worst:.0f} ms ≥ {SLOW_MS:.0f} ms，切主题仍卡 GUI。")
            return 0
        print(f"\n[未复现] 最慢一次 {worst:.0f} ms < {SLOW_MS:.0f} ms。")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] 运行失败：{type(exc).__name__}: {exc}")
        return 2
    finally:
        QWidget.setStyleSheet = orig_widget_ss
        QApplication.setStyleSheet = orig_app_ss
        if window is not None:
            try:
                window.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

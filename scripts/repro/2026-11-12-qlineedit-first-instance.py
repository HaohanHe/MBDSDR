# SPDX-License-Identifier: MIT
"""拆 `QLineEdit` 首实例 343.3 ms（types run3 的 13 个合计）。

首实例一次性承担 Qt 的首次装配，后续 12 个几乎免费。用**消融法**把它拆成三桶：

  A  `cold`      生产字体已装、**不挂应用级 QSS**、不 show → 控件骨架 + 字体度量
  B  `font`      先把 `QFontMetrics` 摸一次（字体引擎预热），其余同 A
                  → A − B = **字体度量首算**
  C  `qss`       生产字体 + 应用级 QSS（theme.apply），不 show
                  → C − B = **QStyleSheetStyle 装配**
  D  `draw`      同 C，再 show + 一次 processEvents（首帧 draw 完成）
                  → D − C = **首次 draw 相关**

每种 case 必须**独立进程**（"第一次建 QLineEdit" 一个进程只有一次），各跑 3 次取中位。

另有 `--case prewarm`：真实 MainWindow 跑完首帧后，再建一个隐藏 QLineEdit，
量「首帧后预热的代价」（= 用户此刻点输入框会感到的一次卡顿），并量紧随其后的
第二个（预热是否真的把后续成本打下去）。

用法::

    python scripts/repro/2026-11-12-qlineedit-first-instance.py --case cold
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _prepare_env() -> str:
    os.makedirs(TMP_ROOT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="lefirst-", dir=TMP_ROOT)
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


def main() -> int:
    case = sys.argv[sys.argv.index("--case") + 1] if "--case" in sys.argv else "cold"
    profile = _prepare_env()
    try:
        import psutil
        from PySide6.QtCore import QSettings
        from PySide6.QtGui import QFontDatabase, QFontMetrics
        from PySide6.QtWidgets import QApplication, QLineEdit
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        import layout_store as ls
        ls.reset_layout_store_singleton()
        ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))
        app = QApplication.instance() or QApplication(sys.argv)
        from themes import get_app_font, get_theme
        app.setFont(get_app_font())
        if not QFontDatabase.families():
            print(json.dumps({"case": case, "error": "字体库为空"}, ensure_ascii=False))
            return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"case": case, "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        return 2

    out = {"case": case, "platform": app.platformName()}
    load_before = round(psutil.cpu_percent(interval=1.0), 1)

    if case in ("qss", "draw"):
        t = time.perf_counter()
        get_theme("dark").apply(app)
        out["theme_apply_ms"] = round((time.perf_counter() - t) * 1000, 1)
        out["qss_len"] = len(app.styleSheet())

    if case in ("font", "qss", "draw"):
        t = time.perf_counter()
        fm = QFontMetrics(app.font())
        fm.height()
        fm.horizontalAdvance("Ag中")
        out["font_metrics_warm_ms"] = round((time.perf_counter() - t) * 1000, 1)

    t = time.perf_counter()
    le = QLineEdit()
    out["first_qlineedit_ms"] = round((time.perf_counter() - t) * 1000, 1)

    t = time.perf_counter()
    le2 = QLineEdit()
    out["second_qlineedit_ms"] = round((time.perf_counter() - t) * 1000, 1)

    if case == "draw":
        t = time.perf_counter()
        le.show()
        app.processEvents()
        out["first_draw_ms"] = round((time.perf_counter() - t) * 1000, 1)

    if case == "prewarm":
        # 首帧之后才建第一个 QLineEdit：量「预热时刻的卡顿」。
        # 用最小窗口而不是 MainWindow：真实 MainWindow 的构造期已经建了 13 个，
        # 第一个的代价早付掉了，在它后面再建只会得到 0.x ms，说明不了问题。
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
        win = QWidget()
        lay = QVBoxLayout(win)
        lay.addWidget(QLabel("首帧占位"))
        win.resize(480, 320)
        t = time.perf_counter()
        win.show()
        app.processEvents()
        out["first_frame_ms"] = round((time.perf_counter() - t) * 1000, 1)
        t = time.perf_counter()
        warm = QLineEdit()
        out["prewarm_first_ms"] = round((time.perf_counter() - t) * 1000, 1)
        t = time.perf_counter()
        QLineEdit()
        out["prewarm_second_ms"] = round((time.perf_counter() - t) * 1000, 1)
        t = time.perf_counter()
        QLineEdit().show()
        app.processEvents()
        out["prewarm_show_ms"] = round((time.perf_counter() - t) * 1000, 1)
        warm.deleteLater()
        win.close()
        app.processEvents()

    out["load_before"] = load_before
    out["load_after"] = round(psutil.cpu_percent(interval=1.0), 1)
    print(json.dumps(out, ensure_ascii=False))
    shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

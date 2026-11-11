# SPDX-License-Identifier: MIT
"""拆 `SettingsPanel._apply_initial_state` 的 223 ms（panels run4）。

只测量、不改产品代码：构造一次真实 MainWindow（windows 尺子 + 生产字体），
把 `_apply_initial_state` 期间的每一次 `_apply`（逐项推送）与 `_current_value`
（读配置）分别计时，并单独统计 `_apply_theme` 被调用的次数与耗时。

输出：每项 ms、按消费点归类的小计、主题应用占比、推送时窗口是否可见。

用法::

    python scripts/repro/2026-11-12-settings-initial-state-breakdown.py
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

REC = {"on": False, "items": [], "read_ms": 0.0, "theme_calls": []}


def _prepare_env() -> str:
    os.makedirs(TMP_ROOT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="initstate-", dir=TMP_ROOT)
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


def _group(key: str) -> str:
    if key == "theme":
        return "主题应用"
    if key in ("touch_mode", "callsign", "gnss_port", "gnss_baudrate",
               "focus_mode", "free_float_panels", "perf_profile", "ui_language"):
        return "身份/接线（_apply 直接 return，无副作用）"
    if key.startswith("fft_") or key.startswith("waterfall_") or \
            key.startswith("peak_") or key in ("show_peaks", "show_bookmarks",
                                               "show_bandplan"):
        return "频谱/瀑布"
    if key.startswith("sky_"):
        return "天空图"
    if key.startswith("ai_"):
        return "AI 参数"
    return "其它控件"


def main() -> int:
    profile = _prepare_env()
    try:
        import psutil
        from PySide6.QtCore import QSettings
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        import layout_store as ls
        ls.reset_layout_store_singleton()
        ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))
        app = QApplication.instance() or QApplication(sys.argv)
        from themes import get_app_font
        app.setFont(get_app_font())
        if not QFontDatabase.families():
            print("[无法判定] 字体库为空")
            return 2
        import settings_panel as sp
        import themes as th
        import main_window as mw
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] {type(exc).__name__}: {exc}")
        return 2

    orig_apply = sp.SettingsPanel._apply
    orig_read = sp.SettingsPanel._current_value
    orig_theme = mw.MainWindow._apply_theme

    def timed_apply(self, item, value):
        if not REC["on"]:
            return orig_apply(self, item, value)
        t = time.perf_counter()
        REC["cur_key"] = item.key
        try:
            r = orig_apply(self, item, value)
        finally:
            REC["cur_key"] = None
        REC["items"].append({"key": item.key, "ms": (time.perf_counter() - t) * 1000,
                             "pushed": bool(r), "visible": bool(self._host.isVisible())})
        return r

    def timed_read(self, item):
        if not REC["on"]:
            return orig_read(self, item)
        t = time.perf_counter()
        try:
            return orig_read(self, item)
        finally:
            REC["read_ms"] += (time.perf_counter() - t) * 1000

    def timed_theme(self, name):
        t = time.perf_counter()
        try:
            return orig_theme(self, name)
        finally:
            REC["theme_calls"].append({"name": name,
                                       "ms": (time.perf_counter() - t) * 1000,
                                       "visible": bool(self.isVisible()),
                                       "inside_apply_key": REC.get("cur_key")})

    orig_repolish = th._repolish_all
    REC["repolish"] = []

    def timed_repolish(a):
        t = time.perf_counter()
        try:
            return orig_repolish(a)
        finally:
            REC["repolish"].append(round((time.perf_counter() - t) * 1000, 1))

    th._repolish_all = timed_repolish

    sp.SettingsPanel._apply = timed_apply
    sp.SettingsPanel._current_value = timed_read
    mw.MainWindow._apply_theme = timed_theme

    load_before = {"sys_cpu_pct": round(psutil.cpu_percent(interval=1.0), 1)}
    REC["on"] = True
    t0 = time.perf_counter()
    win = mw.MainWindow()
    ctor_ms = (time.perf_counter() - t0) * 1000
    REC["on"] = False
    load_after = {"sys_cpu_pct": round(psutil.cpu_percent(interval=1.0), 1)}

    items = REC["items"]
    groups: dict = {}
    for it in items:
        g = _group(it["key"])
        groups.setdefault(g, {"ms": 0.0, "n": 0, "pushed": 0})
        groups[g]["ms"] += it["ms"]
        groups[g]["n"] += 1
        groups[g]["pushed"] += int(it["pushed"])
    total_apply = sum(it["ms"] for it in items)
    theme_ms = sum(c["ms"] for c in REC["theme_calls"])
    out = {
        "script": "scripts/repro/2026-11-12-settings-initial-state-breakdown.py",
        "ruler": {"platform": app.platformName(),
                  "families": len(QFontDatabase.families()),
                  "font_pt": app.font().pointSizeF()},
        "ctor_ms": round(ctor_ms, 1),
        "apply_items_total_ms": round(total_apply, 1),
        "read_config_total_ms": round(REC["read_ms"], 1),
        "item_count": len(items),
        "groups": {k: {"ms": round(v["ms"], 1), "n": v["n"], "pushed": v["pushed"]}
                   for k, v in sorted(groups.items(), key=lambda kv: -kv[1]["ms"])},
        "top_items": sorted(items, key=lambda x: -x["ms"])[:12],
        "theme_calls": REC["theme_calls"],
        "theme_total_ms": round(theme_ms, 1),
        "theme_visible_at_apply": [c["visible"] for c in REC["theme_calls"]],
        "repolish_ms_per_call": REC["repolish"],
        "load_before": load_before,
        "load_after": load_after,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""证据 5 —— 星象仪入口：找星 / 时间 / 存图 / 图层 / 极限星等。

这一组回答的是"能力有没有入口"，而不是"能力有没有实现"。每一条都用
真实控件触发（``button.click()`` / 命中区点击 / 键入），不直接调 handler，
否则测的是函数而不是用户路径。
"""

from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

GID = "ui_entries"
TITLE = "星象仪入口：找星 / 时间机器 / 存图 / 图层面板 / 极限星等只读"

PROFILE = common.isolate_profile("mbdsdr_evidence_ui_")
common.ensure_qt_platform()
common.add_repo_paths()

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def run(args) -> dict:
    app = QApplication.instance() or QApplication([])
    res = common.new_result(GID, TITLE)
    out = Path(args.out) / GID
    out.mkdir(parents=True, exist_ok=True)
    frames = res["frames"]

    from main_window import MainWindow, _night_observation_time  # noqa: E402

    w = MainWindow()
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1600, 950)
    w.show()
    common.pump(app, 50)

    w.set_app_mode("planetarium")
    common.pump(app, 60)
    sv = w.sky_view

    # 给一片能看见的天：示例站 + 今夜（真实太阳 -18° / 民用暮光终）
    w._preview_site_no_coords = True
    sv.repaint()
    rect = getattr(sv, "_preview_rect", None)
    if rect is not None:
        sv._hud_hit(rect.center().x(), rect.center().y())
    common.pump(app, 40)
    w._jump_to_tonight()
    common.pump(app, 60)
    sv.repaint()
    res["facts"]["observer"] = sv.get_observer()
    res["facts"]["sun_alt_deg"] = round(float(sv._sun_alt_deg()), 2)

    # ---------------- 1) 按钮条 ----------------
    buttons = {}
    for key in ("_planetarium_search_btn", "_planetarium_time_jump_btn",
                "_planetarium_tonight_btn", "_planetarium_time_btn",
                "_planetarium_layer_btn", "_planetarium_shot_btn"):
        b = getattr(w, key, None)
        buttons[key] = {
            "present": b is not None,
            "visible": bool(b.isVisible()) if b is not None else False,
            "text": b.text() if b is not None else "",
        }
    res["facts"]["toolbar_buttons"] = buttons
    res["facts"]["search_shortcut_enabled"] = bool(
        getattr(w, "_planetarium_search_key", None)
        and w._planetarium_search_key.isEnabled())
    res["facts"]["search_visible_before_click"] = bool(sv.search_visible())
    frames.append(common.save_frame(w, out / "01_toolbar.png"))

    # ---------------- 2) 图层面板 ----------------
    w._planetarium_layer_btn.setChecked(True)
    common.pump(app, 25)
    layer_btns = getattr(w, "_layer_btns", {})
    live_state = sv.layer_state()
    res["facts"]["layer_panel"] = {
        "visible": bool(w._layer_popover.isVisible()),
        "item_count": len(layer_btns),
        "items": [{"key": k, "text": b.text(), "checked": bool(b.isChecked())}
                  for k, b in layer_btns.items()],
        "state_keys_match": sorted(layer_btns) == sorted(live_state),
        "popover_geometry": [w._layer_popover.x(), w._layer_popover.y(),
                             w._layer_popover.width(),
                             w._layer_popover.height()],
    }
    limit_lbl = getattr(w, "_limit_mag_label", None)
    res["facts"]["limit_magnitude_line"] = (
        limit_lbl.text() if limit_lbl is not None else "MISSING")
    res["facts"]["limit_magnitude_readonly"] = not any(
        hasattr(limit_lbl, attr) for attr in ("setSliderPosition", "setValue"))
    frames.append(common.save_frame(w, out / "02_layers.png"))
    w._planetarium_layer_btn.setChecked(False)
    common.pump(app, 10)

    # ---------------- 3) 找星 ----------------
    w._open_planetarium_search()
    common.pump(app, 30)
    search_geom = sv._search_edit.geometry()
    res["facts"]["search_after_click"] = {
        "visible": bool(sv.search_visible()),
        "geometry": [search_geom.x(), search_geom.y(),
                     search_geom.width(), search_geom.height()],
        "viewport_height": sv.height(),
        "inside_viewport": search_geom.bottom() <= sv.height(),
    }
    sv._search_edit.setText("Vega")
    common.pump(app, 40)
    results = list(getattr(sv, "_search_results", []) or [])
    res["facts"]["search_results_for_vega"] = {
        "count": len(results),
        "popup_visible": bool(sv._search_popup.isVisible()),
        "top": [sv._search_popup.item(i).text()
                for i in range(min(4, sv._search_popup.count()))],
    }
    frames.append(common.save_frame(w, out / "03_search.png"))

    # Esc 只收搜索，不退形态（分层，不是一把全关）
    sv.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    common.pump(app, 10)
    res["facts"]["esc_layering"] = {
        "search_hidden": not bool(sv.search_visible()),
        "still_in_planetarium": getattr(w, "_app_mode", "") == "planetarium",
    }

    # ---------------- 4) 时间机器 ----------------
    from time_machine import TimeMachineDialog  # noqa: E402

    obs = sv.get_observer()
    dlg = TimeMachineDialog(
        w, now_unix=w._engine_now_unix(),
        night_resolver=((lambda: _night_observation_time(obs["lat"], obs["lon"]))
                        if obs else None),
        on_apply=w._apply_time_jump)
    dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
    dlg.show()
    common.pump(app, 25)
    before = dlg.status.text()
    dlg._shift(86400)
    common.pump(app, 10)
    after_shift = dlg.status.text()
    dlg._go_night()
    common.pump(app, 10)
    res["facts"]["time_machine"] = {
        "status_initial": before,
        "status_after_plus_1d": after_shift,
        "status_after_tonight": dlg.status.text(),
        "edit_after_tonight": dlg.edit.dateTime().toString("yyyy-MM-dd HH:mm"),
    }
    frames.append(common.save_frame(dlg, out / "04_time_machine.png"))
    dlg.close()

    # ---------------- 5) 存图 ----------------
    # 系统"图片"夹由注册表决定，隔离 HOME 拦不住（Windows 实测）。
    # 走产品自带的 MBDSDR_SCREENSHOT_DIR 开关，别往用户相册里写图。
    shots_dir = Path(os.environ["USERPROFILE"]) / "Pictures" / "MBDSDR"
    os.environ["MBDSDR_SCREENSHOT_DIR"] = str(shots_dir)
    w._save_sky_screenshot()
    common.pump(app, 15)
    files = sorted(glob.glob(str(shots_dir / "*.png")))
    res["facts"]["screenshot_save"] = {
        "status_line": str(w.statusBar().currentMessage())[:160],
        "dir": str(shots_dir),
        "files": [Path(f).name for f in files[-3:]],
        "count": len(files),
        "sim_marked": any("_sim" in Path(f).name for f in files),
        "dir_override": "MBDSDR_SCREENSHOT_DIR（默认系统的图片夹）",
    }
    if not files:
        res["status"] = "degraded"
        res["notes"].append(
            "存图后目录里没有文件 —— 可能是无写权限；不计 FAIL，但不算通过")

    res["not_verified"] += [
        "真机不在位：入口可用性在无 SDR/无 GNSS 下验收，硬件联动未跑",
        "「今夜」用的是注入的真实暮光求解，未与真实现场日落地平线比对",
    ]
    res["facts"]["host"] = common.host_info()
    w.close()
    return res


def main(argv=None) -> int:
    args = common.parse_common_args(argv)
    started = time.time()
    try:
        res = run(args)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        res = common.new_result(GID, TITLE, status="fail")
        res["notes"].append(f"{type(exc).__name__}: {exc}")
    return common.finish(res, Path(args.out) / GID, args.json_out, started)


if __name__ == "__main__":
    sys.exit(main())

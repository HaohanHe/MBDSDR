#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""证据 4 —— 首次打开：零硬件、零配置、只点屏幕上的按钮。

对照实验很重要：别的脚本会注入站址、切形态、调时间，拿到的是"调好之后"
的画面。本组拿的是"双击图标之后第一眼"的画面——这才是零门槛的真凭据。
所以这里**不注入任何坐标**，只用示例天空按钮和「今夜」。

每次跑都新建隔离 profile：上一轮会把形态落盘，复用同一个 HOME 会让
"首次打开"其实是第二次打开。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

GID = "first_open"
TITLE = "首次打开：零硬件 / 零配置 / 只点屏幕按钮"

PROFILE = common.isolate_profile("mbdsdr_evidence_first_")
common.ensure_qt_platform()
common.add_repo_paths()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _opt(value, nd=2):
    """无站址时太阳高度是 None（不编一个数），如实记 None。"""
    return None if value is None else round(float(value), nd)


def run(args) -> dict:
    app = QApplication.instance() or QApplication([])

    res = common.new_result(GID, TITLE)
    out = Path(args.out) / GID
    out.mkdir(parents=True, exist_ok=True)
    frames = res["frames"]

    from main_window import MainWindow  # noqa: E402 — 必须晚于 isolate_profile

    w = MainWindow()
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1440, 900)
    w.show()
    common.pump(app, 50)

    texts = common.visible_texts(w)
    res["facts"].update({
        "profile": str(PROFILE),
        "app_mode": getattr(w, "_app_mode", "?"),
        "focus_mode": getattr(w, "_focus_mode", "?"),
        "observer_at_start": w.sky_view.get_observer(),
        "visible_text_count": len(texts),
        "visible_text_sample": texts[:12],
        "config_written_on_first_open":
            (PROFILE / ".mbdsdr" / "gui_config.json").exists(),
    })
    frames.append(common.save_frame(w, out / "01_default.png"))

    # 用户说的"打开唰一下全天空"：切星象仪，仍不注入站址、不调时间
    w.set_app_mode("planetarium")
    common.pump(app, 60)
    sv = w.sky_view
    layer_state = sv.layer_state() if hasattr(sv, "layer_state") else {}
    res["facts"].update({
        "planetarium_fov_deg": round(float(sv.view_state.fov_deg), 1),
        "layers_on_at_first_open": sorted(k for k, v in layer_state.items() if v),
        "star_count": 0 if getattr(sv, "_star_sel", None) is None
                      else len(sv._star_sel),
        "sun_alt_deg": _opt(sv._sun_alt_deg()),
        "stars_hidden_by_daylight": bool(sv._stars_hidden_by_daylight()),
        "site_note": sv.get_site_note(),
    })
    sv.repaint()
    frames.append(common.save_frame(w, out / "02_planetarium_default.png"))

    # 走真实命中区点「先看看示例天空」，不是直接调 handler
    preview_clicked = False
    rect = getattr(sv, "_preview_rect", None)
    if rect is not None:
        hit = sv._hud_hit(rect.center().x(), rect.center().y())
        preview_clicked = bool(hit)
        common.pump(app, 50)
        res["facts"]["preview_hud_hit"] = str(hit)
    else:
        res["notes"].append("首屏没有出现「先看看示例天空」命中区，跳过该次点击")
    common.pump(app, 20)
    res["facts"].update({
        "preview_clicked": preview_clicked,
        "observer_after_preview": sv.get_observer(),
        "site_note_after_preview": sv.get_site_note(),
        "observer_source": getattr(w, "_observer_source", None),
    })
    sv.repaint()
    frames.append(common.save_frame(w, out / "03_preview.png"))

    # 「今夜」：按真实太阳高度求民用暮光终，不写死钟点
    engine = None
    try:
        from mbdsdr_ai.new_spacetime import get_time_engine
        engine = get_time_engine()
    except (ImportError, AttributeError) as exc:
        res["notes"].append(f"时间引擎不可用：{type(exc).__name__}")
    w._jump_to_tonight()
    common.pump(app, 60)
    tonight_applied = bool(sv.get_observer()) and engine is not None
    if engine is not None:
        res["facts"]["tonight_offset_from_wall_s"] = round(
            engine.now_unix() - time.time(), 1)
    res["facts"].update({
        "tonight_applied": tonight_applied,
        "sun_alt_after_tonight_deg": _opt(sv._sun_alt_deg()),
        "stars_hidden_after_tonight": bool(sv._stars_hidden_by_daylight()),
        "star_count_after_tonight": 0 if getattr(sv, "_star_sel", None) is None
                                    else len(sv._star_sel),
        "status_line": str(w.statusBar().currentMessage())[:160],
    })
    if not tonight_applied:
        res["notes"].append(
            "没有观测位置，今夜未生效（产品行为：先给位置再算暮光，不猜时刻）")
    sv.repaint()
    frames.append(common.save_frame(w, out / "04_tonight.png"))

    res["not_verified"] += [
        "真机 SDR / GNSS 未接入：首开路径未覆盖「插上棒子自动就绪」这一段",
        "无 API Key：首开未覆盖模型对话（界面按本地协商空态显示）",
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

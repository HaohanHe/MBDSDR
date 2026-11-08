#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""证据 6 —— 地点面板（搜城市 / 地图点选 / 来源）+ 投影切换。

默认**不联网**：地图点选与四投影全部离线可跑，这样任何人 clone 下来
都能得到同样的结论。``--online`` 才额外打一次 OpenStreetMap Nominatim，
并在报告里把它标成"本次联网跑过"。不联网时绝不说"搜索已验证"。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

GID = "location_projection"
TITLE = "地点面板（地图点选/搜索/来源）+ 四种投影"

PROFILE = common.isolate_profile("mbdsdr_evidence_loc_")
common.ensure_qt_platform()
common.add_repo_paths()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _parse(argv):
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--out", default=str(common.DEFAULT_OUT))
    p.add_argument("--json-out", default=None)
    p.add_argument("--online", action="store_true",
                   help="额外验证一次 OSM 在线搜索（需要网络）")
    return p.parse_args(argv)


def run(args) -> dict:
    app = QApplication.instance() or QApplication([])
    res = common.new_result(GID, TITLE)
    out = Path(args.out) / GID
    out.mkdir(parents=True, exist_ok=True)
    frames = res["frames"]

    from main_window import MainWindow  # noqa: E402

    w = MainWindow()
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1500, 920)
    w.show()
    common.pump(app, 40)

    # ================= 地点面板 =================
    w._open_location_panel()
    common.pump(app, 20)
    panel = w._location_panel
    panel.setAttribute(Qt.WA_DontShowOnScreen, True)
    panel.resize(480, 560)
    panel.show()
    common.pump(app, 20)

    provider = panel._provider_or_none()
    res["facts"]["panel"] = {
        "visible": bool(panel.isVisible()),
        "current_label": panel.current_label.text(),
        "provider": type(provider).__name__ if provider is not None else None,
        "provider_available": bool(provider.available())
                if provider is not None and hasattr(provider, "available")
                else None,
        "write_path": "signals only (site_chosen / cleared)",
    }
    frames.append(common.save_frame(panel, out / "01_panel.png"))

    # 联网搜索（可选）
    if args.online:
        panel.search_edit.setText("长春")
        t0 = time.time()
        panel._do_search()
        deadline = time.time() + 30
        while time.time() < deadline:
            common.pump(app, 5, 0.05)
            if panel._worker is not None and panel._worker.isFinished():
                common.pump(app, 5, 0.02)
                break
        got = [panel.result_list.item(i).text()
               for i in range(min(4, panel.result_list.count()))]
        res["facts"]["osm_search"] = {
            "query": "长春",
            "elapsed_s": round(time.time() - t0, 1),
            "hint": panel.hint.text(),
            "count": panel.result_list.count(),
            "top": got,
            "network": True,
        }
        frames.append(common.save_frame(panel, out / "02_search.png"))
        if panel.result_list.count():
            panel._on_item(panel.result_list.item(0))
            common.pump(app, 10)
            res["facts"]["osm_search"]["pending_label"] = panel.pending_label.text()
    else:
        res["facts"]["osm_search"] = {
            "count": 0,
            "network": False,
            "skipped": "未加 --online：本次没有联网，搜索链路未验证",
        }
        res["notes"].append("OSM 在线搜索未跑（默认离线）；加 --online 可验证")

    # 地图点选（离线，任何机器都能跑）
    panel._on_map_picked(31.2304, 121.4737)
    common.pump(app, 10)
    res["facts"]["map_pick"] = {
        "clicked": [31.2304, 121.4737],
        "pending_label": panel.pending_label.text(),
    }
    frames.append(common.save_frame(panel, out / "03_map.png"))
    panel._on_apply()
    common.pump(app, 30)

    mirror_path = PROFILE / ".mbdsdr" / "config.json"
    mirror = None
    if mirror_path.is_file():
        try:
            mirror = json.loads(mirror_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            mirror = "unreadable"
    res["facts"]["apply_map_pick"] = {
        "observer_lat": w._observer_lat,
        "observer_lon": w._observer_lon,
        "observer_source": w._observer_source,
        "sky_view_observer": w.sky_view.get_observer(),
        "mirror_written": mirror_path.is_file(),
        "mirror_site": (
            {"lat": mirror.get("ground_station_lat"),
             "lon": mirror.get("ground_station_lon")}
            if isinstance(mirror, dict) else mirror),
        "time_unchanged": "地点选择只改位置，不跳时刻",
    }

    # 清空 → 回到未定位空态
    panel._on_clear()
    common.pump(app, 20)
    # 必须**重新读**镜像文件：上面那个 mirror 是清空前的快照，
    # 拿它判"清没清掉"会永远 False，看着像 bug 其实是读错了时机。
    mirror_after = None
    if mirror_path.is_file():
        try:
            mirror_after = json.loads(mirror_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            mirror_after = "unreadable"
    res["facts"]["clear"] = {
        "observer_lat": w._observer_lat,
        "observer_lon": w._observer_lon,
        "sky_view_observer": w.sky_view.get_observer(),
        "mirror_after": ({"lat": mirror_after.get("ground_station_lat"),
                          "lon": mirror_after.get("ground_station_lon")}
                         if isinstance(mirror_after, dict) else mirror_after),
        "mirror_site_cleared": (
            isinstance(mirror_after, dict)
            and mirror_after.get("ground_station_lat") is None),
    }

    # ================= 投影 =================
    w.set_app_mode("planetarium")
    common.pump(app, 50)
    sv = w.sky_view
    sv.repaint()
    rect = getattr(sv, "_preview_rect", None)
    if rect is not None:
        sv._hud_hit(rect.center().x(), rect.center().y())
    common.pump(app, 40)
    w._jump_to_tonight()
    common.pump(app, 60)
    sv.view_state.look_at(180.0, 35.0)

    from mbdsdr_ai.celestial_geometry import (  # noqa: E402
        PROJECTION_INFO, projection_names)

    proj_facts = {}
    for name in projection_names():
        sv.set_projection(name)
        sv.repaint()
        common.pump(app, 20, 0.02)
        p = sv._sky_to_screen(200.0, 40.0)
        info = PROJECTION_INFO.get(name, {})
        proj_facts[name] = {
            "zh": info.get("zh", name),
            "fov_max": round(float(sv.view_state.max_fov), 1),
            "screen_of_az200_alt40": [round(p.x(), 1), round(p.y(), 1)],
            "stars": 0 if getattr(sv, "_star_sel", None) is None
                     else len(sv._star_sel),
        }
        frames.append(common.save_frame(w, out / f"04_proj_{name}.png"))
    res["facts"]["projections"] = proj_facts

    # 换投影必须真的动了画面，而不是只改了名字
    xs = {f["screen_of_az200_alt40"][0] for f in proj_facts.values()}
    res["facts"]["projections_change_geometry"] = len(xs) > 1
    if len(xs) <= 1:
        res["status"] = "fail"
        res["notes"].append("四种投影算出的屏幕坐标完全相同 —— 切换没接进换算核")

    w._sync_projection_combo()
    res["facts"]["combo_echo"] = {
        "text": w._planetarium_proj_combo.currentText(),
        "data": w._planetarium_proj_combo.currentData(),
    }

    if args.online:
        res["not_verified"].append(
            "OSM 在线搜索走的是公网 Nominatim：结果取决于当时网络与数据库快照，"
            "不是确定性证据")
    else:
        res["not_verified"].append(
            "OSM 在线搜索本次未跑（离线默认）；搜索链路只由单元测试用假 "
            "provider 覆盖")
    res["not_verified"].append(
        "真机 GNSS 不在位：GNSS fix 与「地点面板所选位置」同时存在时的优先级未跑")
    res["facts"]["host"] = common.host_info()
    w.close()
    return res


def main(argv=None) -> int:
    args = _parse(argv)
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

# SPDX-License-Identifier: MIT
"""HUD 绘制指令的行为契约（纯逻辑，不依赖 Qt）。

覆盖：
1. 级别差异：L0/L1 只有准星+提示；L2 粗标签；L3 精确标签；L4 手动标记；
2. 空目标、非法输入（坏位姿/坏级别/坏容器/坏尺寸）；
3. 1080p / 2.3° / 1000 目标下单次 ``build_hud`` ≤ 33 ms（实测打印耗时）。
"""

import math
import os
import random
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_hud import HUD_HINTS, build_hud  # noqa: E402

SIZE = (1920.0, 1080.0)
VIEW = {"center_az": 120.0, "center_alt": 45.0, "fov_deg": 2.3,
        "rotation_deg": 0.0, "min_fov": 0.5}
POSE = {"valid": True, "az_deg": 120.0, "alt_deg": 45.0,
        "roll_deg": 0.0, "fov_deg": 2.3}


def _targets(n=2):
    return [
        {"name": "角宿一", "az_deg": 120.3, "alt_deg": 45.2,
         "vmag": 0.98, "angular_distance_deg": 0.36, "kind": "star"},
        {"name": "试验卫星", "az_deg": 119.8, "alt_deg": 44.9,
         "vmag": 3.2, "angular_distance_deg": 0.39, "kind": "satellite"},
    ][:n]


def _crosshair(hud):
    return hud["crosshair"]["x"], hud["crosshair"]["y"]


def test_level_l0_l1_only_crosshair_and_hint():
    for level in ("L0", "L1"):
        hud = build_hud(POSE, _targets(), level, VIEW, SIZE,
                        asterism_segments=[{"name": "角宿", "points": [
                            (120.0, 45.0), (120.5, 45.0)]}])
        assert hud["level"] == level
        assert hud["hint"] == HUD_HINTS[level]
        assert hud["labels"] == []
        assert hud["asterism_segments"] == []
        assert hud["satellite_markers"] == []
        assert _crosshair(hud) == (SIZE[0] / 2.0, SIZE[1] / 2.0)


def test_level_l2_coarse_labels_only_names():
    hud = build_hud(POSE, _targets(), "L2", VIEW, SIZE)
    assert hud["hint"] == "已融合"
    assert hud["labels"], "L2 应给粗标签"
    for label in hud["labels"]:
        assert label["detail"] == "coarse"
        assert label["text"] == label["name"]
        assert "°" not in label["text"] and "等" not in label["text"]


def test_level_l3_precise_labels():
    hud = build_hud(POSE, _targets(), "L3", VIEW, SIZE)
    assert hud["hint"] == "已锁定"
    assert hud["labels"]
    for label in hud["labels"]:
        assert label["detail"] == "precise"
        assert "°" in label["text"]
        assert "等" in label["text"]
        assert math.isfinite(label["x"]) and math.isfinite(label["y"])


def test_level_l4_manual_mark_and_view_not_overridden():
    pose_elsewhere = {"valid": True, "az_deg": 10.0, "alt_deg": 20.0,
                      "roll_deg": 0.0, "fov_deg": 60.0}
    hud = build_hud(pose_elsewhere, _targets(), "L4", VIEW, SIZE)
    assert hud["level"] == "L4"
    assert hud["hint"] == "手动"
    assert hud["manual"] is True
    assert hud["pose_used"] is False
    # 位姿在 (10°,20°)，但视图参数仍是 120°/45°：标签应落在中心附近，
    # 而不是按位姿重新投影到别处。
    cx, cy = _crosshair(hud)
    assert hud["labels"]
    for label in hud["labels"]:
        assert abs(label["x"] - cx) < 400.0
        assert abs(label["y"] - cy) < 400.0


def test_empty_targets_still_has_crosshair_and_hint():
    hud = build_hud(POSE, [], "L3", VIEW, SIZE)
    assert hud["labels"] == []
    assert hud["satellite_markers"] == []
    assert hud["asterism_segments"] == []
    assert hud["targets_total"] == 0
    assert hud["hint"] == "已锁定"


def test_asterism_segments_are_projected_and_filtered():
    segments = [
        # overlay_asterisms.OverlaySegment.to_dict() 的 a/b 形态。
        {"name": "角宿", "a": {"az_deg": 120.0, "alt_deg": 45.0},
         "b": {"az_deg": 120.5, "alt_deg": 45.0}},
        # 整段在地平线以下：不应产生任何绘制段。
        {"name": "地平下", "points": [(0.0, -20.0), (10.0, -30.0)]},
    ]
    hud = build_hud(POSE, [], "L3", VIEW, SIZE, asterism_segments=segments)
    assert len(hud["asterism_segments"]) == 1
    seg = hud["asterism_segments"][0]
    assert seg["name"] == "角宿"
    assert len(seg["points"]) >= 2
    for x, y in seg["points"]:
        assert 0.0 <= x <= SIZE[0] and 0.0 <= y <= SIZE[1]


def test_satellite_markers_separate_from_star_labels():
    hud = build_hud(POSE, _targets(), "L3", VIEW, SIZE)
    assert [m["name"] for m in hud["satellite_markers"]] == ["试验卫星"]
    assert "试验卫星" in [lab["name"] for lab in hud["labels"]]


def test_no_pose_degrades_to_l0_and_draws_no_targets():
    for pose in (None, {}, {"valid": False, "az_deg": 120.0, "alt_deg": 45.0},
                 {"valid": True, "az_deg": float("nan"), "alt_deg": 45.0},
                 {"valid": True, "az_deg": 120.0, "alt_deg": 200.0}):
        hud = build_hud(pose, _targets(), "L3", VIEW, SIZE)
        assert hud["level"] == "L0"
        assert hud["hint"] == "还没对准"
        assert hud["labels"] == []
        assert hud["satellite_markers"] == []
        assert hud["pose_used"] is False


def test_invalid_level_and_target_container_degrade_honestly():
    hud = build_hud(POSE, "not-a-list", "X9", VIEW, SIZE)
    assert hud["level"] == "L0"
    assert hud["hint"] == "还没对准"
    assert hud["labels"] == []
    assert "级别非法" in hud["reason"]
    assert "targets" in hud["reason"]


def test_invalid_targets_are_skipped_not_invented():
    targets = [
        {"name": "坏星", "az_deg": float("nan"), "alt_deg": 45.0},
        {"name": "无坐标"},
    ]
    hud = build_hud(POSE, targets, "L3", VIEW, SIZE)
    assert hud["labels"] == []
    assert hud["targets_total"] == 2
    assert hud["targets_visible"] == 0


def test_invalid_size_raises():
    for size in ((0, 0), (-1, 100), (float("nan"), 100), ("a", "b"), (1,)):
        with pytest.raises(ValueError):
            build_hud(POSE, [], "L1", VIEW, size)


def test_precise_label_states_number_provenance():
    """L3 标签里的角距/星等必须注明来源，数值本身不变。"""
    hud = build_hud(POSE, _targets(), "L3", VIEW, SIZE)
    star = next(lab for lab in hud["labels"] if lab["name"] == "角宿一")
    assert "0.36°" in star["text"], star["text"]
    assert "推算" in star["text"], "角距是模型推算，必须标注"
    assert "1.0等" in star["text"], star["text"]
    assert "星表" in star["text"], "星等来自星表，必须标注"
    sat = next(m for m in hud["satellite_markers"]
               if m["name"] == "试验卫星")
    assert "0.39°" in sat["text"] and "推算" in sat["text"], sat["text"]
    assert "3.2等" in sat["text"] and "星表" in sat["text"], sat["text"]


def test_sources_text_labels_time_and_site_uncertainty():
    """人话数据源行：卫星授时/系统时钟、GNSS/手动位置都要带精度口径。"""
    online = {
        "sdr": {"connected": True},
        "gnss": {"connected": True, "time_source": "nmea", "fix": True},
        "time": {"source": "gnss", "time_source": "nmea"},
        "site": {"source": "gnss", "lat_deg": 48.1, "lon_deg": 11.5},
    }
    text = build_hud(POSE, [], "L3", VIEW, SIZE,
                     runtime_sources=online)["sources_text"]
    assert "时间：卫星授时（整秒级，无秒脉冲）" in text, text
    assert "位置：GNSS（接收机观测）" in text, text

    manual = {
        "sdr": {"connected": False},
        "gnss": {"connected": False, "time_source": "none", "fix": False},
        "time": {"source": "system", "time_source": "none"},
        "site": {"source": "manual", "lat_deg": 45.0, "lon_deg": 120.0},
    }
    text = build_hud(POSE, [], "L3", VIEW, SIZE,
                     runtime_sources=manual)["sources_text"]
    assert "时间：系统时钟（未校准）" in text, text
    assert "位置：手动位置（未校准）" in text, text


def test_performance_1080p_2_3deg_1000_targets():
    """1080p / 2.3° / 1000 目标：单次 ≤ 33 ms（取 3 次最优，打印每次）。"""
    rng = random.Random(20261117)
    targets = []
    for i in range(1000):
        targets.append({
            "name": f"星{i}",
            "az_deg": 120.0 + rng.uniform(-1.0, 1.0),
            "alt_deg": 45.0 + rng.uniform(-1.0, 1.0),
            "vmag": rng.uniform(-1.0, 6.0),
        })
    # 预热：解析投影/标签排序的一次性开销不算进预算。
    build_hud(POSE, targets, "L3", VIEW, SIZE)
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        hud = build_hud(POSE, targets, "L3", VIEW, SIZE)
        timings.append((time.perf_counter() - started) * 1000.0)
    best = min(timings)
    print("build_hud 1000 targets @2.3°/1080p: "
          + " / ".join(f"{ms:.2f} ms" for ms in timings)
          + f" (best {best:.2f} ms)")
    assert hud["targets_visible"] > 0
    assert best <= 33.0, f"单帧 {best:.2f} ms 超出 33 ms 预算：{timings}"

# SPDX-License-Identifier: MIT
"""位姿融合与「指向即识别」的纯逻辑测试（离线、确定性、不依赖 Qt）。

覆盖口径
--------
* 融合：单源、双源不同精度的加权、方位跨 0° 的圆均值、陈旧/低精度丢弃、
  NaN 与 alt 越界拒绝、全空不可用；
* 视图参数：合法位姿可套用、非法位姿不产出朝向、缺视场不猜默认值；
* 识别：用随仓星表真实算出一个可见亮星方向，必须命中且名字非空；
  地平以下方向必须 `matched=False`，不许最近邻凑数。
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_pointing import (  # noqa: E402
    PoseObservation,
    fuse_poses,
    identify_direction,
    view_params,
)

NOW = 1_700_000_000.0


def _obs(source="imu", t=None, az=10.0, alt=20.0, roll=0.0,
         accuracy=2.0, fov=None):
    return PoseObservation(source=source,
                           t_utc=NOW - 0.5 if t is None else t,
                           az_deg=az, alt_deg=alt, accuracy_deg=accuracy,
                           roll_deg=roll, fov_deg=fov)


# ---------------------------------------------------------------------------
# 融合
# ---------------------------------------------------------------------------
def test_single_source_passes_through():
    out = fuse_poses([_obs(az=10.0, alt=20.0, roll=5.0,
                           accuracy=2.0, fov=60.0)], NOW)
    assert out["valid"] is True
    assert out["az_deg"] == pytest.approx(10.0, abs=1e-9)
    assert out["alt_deg"] == pytest.approx(20.0, abs=1e-9)
    assert out["roll_deg"] == pytest.approx(5.0, abs=1e-9)
    assert out["fov_deg"] == pytest.approx(60.0, abs=1e-9)
    assert out["accuracy_deg"] == pytest.approx(2.0, abs=1e-9)
    assert out["sources"] == ["imu"]
    assert out["level"] == "single"
    assert out["dropped"] == []
    assert out["reason"] == ""


def test_two_sources_weighted_by_inverse_variance():
    # 精度 1° 的权重是 10° 的 100 倍：融合结果必须紧贴高精度源。
    out = fuse_poses([
        _obs(source="imu", az=10.0, alt=10.0, accuracy=1.0),
        _obs(source="camera", az=20.0, alt=30.0, accuracy=10.0),
    ], NOW)
    assert out["valid"] is True
    assert out["sources"] == ["imu", "camera"]
    assert out["level"] == "fused"
    # 圆均值与线性加权略有差异（sin/cos 合矢量），实算值即此处锁定的参考。
    assert out["az_deg"] == pytest.approx(10.0985227, abs=1e-6)
    assert out["alt_deg"] == pytest.approx(10.1980198, abs=1e-6)
    assert out["accuracy_deg"] == pytest.approx(1.0 / np.sqrt(1.01), abs=1e-9)
    # 明确不是两源的中点。
    assert abs(out["az_deg"] - 15.0) > 4.0


def test_azimuth_circular_mean_across_zero():
    out = fuse_poses([
        _obs(az=359.0, alt=45.0, accuracy=1.0),
        _obs(az=1.0, alt=45.0, accuracy=1.0),
    ], NOW)
    assert out["valid"] is True
    # 360° 与 0° 是同一方向；算术平均会错成 180°。
    assert min(out["az_deg"], abs(out["az_deg"] - 360.0)) < 1e-9


def test_stale_observation_dropped_with_reason():
    out = fuse_poses([_obs(t=NOW - 3.0)], NOW, max_age_s=2.0)
    assert out["valid"] is False
    assert out["az_deg"] is None
    assert len(out["dropped"]) == 1
    assert "陈旧" in out["dropped"][0]["reason"]


def test_low_accuracy_observation_dropped_with_reason():
    out = fuse_poses([_obs(accuracy=25.0)], NOW, max_accuracy_deg=20.0)
    assert out["valid"] is False
    assert len(out["dropped"]) == 1
    assert "低精度" in out["dropped"][0]["reason"]


def test_mixed_sources_keep_good_drop_bad():
    out = fuse_poses([
        _obs(source="imu", accuracy=25.0),
        _obs(source="camera", az=30.0, alt=40.0, accuracy=1.0),
    ], NOW)
    assert out["valid"] is True
    assert out["sources"] == ["camera"]
    assert out["az_deg"] == pytest.approx(30.0, abs=1e-9)
    assert [d["source"] for d in out["dropped"]] == ["imu"]


def test_nan_and_out_of_range_alt_rejected():
    nan_out = fuse_poses([_obs(az=float("nan"))], NOW)
    assert nan_out["valid"] is False
    assert "非法" in nan_out["dropped"][0]["reason"]

    inf_out = fuse_poses([_obs(accuracy=float("inf"))], NOW)
    assert inf_out["valid"] is False
    assert "非法" in inf_out["dropped"][0]["reason"]

    alt_out = fuse_poses([_obs(alt=91.0)], NOW)
    assert alt_out["valid"] is False
    assert "越界" in alt_out["dropped"][0]["reason"]

    fov_out = fuse_poses([_obs(fov=-1.0)], NOW)
    assert fov_out["valid"] is False
    assert "fov" in fov_out["dropped"][0]["reason"]


def test_no_usable_observation_is_not_guessed():
    empty = fuse_poses([], NOW)
    assert empty["valid"] is False
    assert empty["az_deg"] is None
    assert "为空" in empty["reason"]

    all_bad = fuse_poses([_obs(t=NOW - 100.0)], NOW)
    assert all_bad["valid"] is False
    assert "全部被丢弃" in all_bad["reason"]
    assert all_bad["sources"] == []

    bad_now = fuse_poses([_obs()], float("nan"))
    assert bad_now["valid"] is False
    assert "now_utc" in bad_now["reason"]


def test_cancelling_azimuths_are_undefined_not_guessed():
    # 三等分方位 → 合矢量为零，圆均值无定义；必须判不可用而不是给 0°/任意值。
    out = fuse_poses([
        _obs(az=0.0), _obs(az=120.0), _obs(az=240.0),
    ], NOW)
    assert out["valid"] is False
    assert "圆均值无定义" in out["reason"]


# ---------------------------------------------------------------------------
# view_params
# ---------------------------------------------------------------------------
def test_view_params_for_valid_pose():
    pose = fuse_poses([_obs(az=123.4, alt=45.6, roll=7.5,
                            accuracy=1.0, fov=60.0)], NOW)
    params = view_params(pose)
    assert params["valid"] is True
    assert params["center_az"] == pytest.approx(123.4, abs=1e-9)
    assert params["center_alt"] == pytest.approx(45.6, abs=1e-9)
    assert params["fov_deg"] == pytest.approx(60.0, abs=1e-9)
    assert params["rotation_deg"] == pytest.approx(7.5, abs=1e-9)
    assert params["fov_clamped"] is False


def test_view_params_uses_explicit_fov_and_reports_clamp():
    pose = fuse_poses([_obs(fov=None)], NOW)
    assert pose["fov_deg"] is None
    missing = view_params(pose)
    assert missing["valid"] is False
    assert "fov_deg" in missing["reason"]

    # 2.3° 目镜视场低于 ViewState 下限 30°，先夹取并如实标记。
    params = view_params(pose, fov_deg=2.3)
    assert params["valid"] is True
    assert params["fov_requested_deg"] == pytest.approx(2.3)
    assert params["fov_deg"] == pytest.approx(30.0)
    assert params["fov_clamped"] is True


def test_view_params_rejects_invalid_pose():
    out = view_params({"valid": False, "reason": "没有可用观测"})
    assert out["valid"] is False
    assert out["reason"] == "没有可用观测"

    out2 = view_params({"valid": True, "az_deg": 10.0, "alt_deg": 95.0,
                        "roll_deg": 0.0, "fov_deg": 60.0})
    assert out2["valid"] is False
    assert "越界" in out2["reason"]


# ---------------------------------------------------------------------------
# identify_direction（使用随仓 HYG 星表）
# ---------------------------------------------------------------------------
_LAT = 45.0
_LON = 120.0
_JD = 2461359.5  # 2026-11-16 00:00 UT1，固定值保证确定性


def _visible_named_star():
    """按星等从亮到暗找一个此刻地平高度 > 30° 且有专名的星。"""
    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    cat = get_catalog()
    gs = GroundStation(_LON, _LAT, 0.0, "test")
    for i in np.argsort(cat.vmag)[:400]:
        name = str(cat.name[i]).strip()
        if not name:
            continue
        v = vec_from_radec(float(cat.ra_deg[i]), float(cat.dec_deg[i]))
        az, alt = azalt_from_vec(j2000_to_altaz(v, gs, _JD))
        if float(alt) > 30.0:
            return name, float(az), float(alt)
    pytest.skip("固定时刻无可见亮星，环境星表异常")


def test_identify_direction_hits_real_catalog_star():
    name, az, alt = _visible_named_star()
    out = identify_direction(az, alt, 3.0, _JD, _LAT, _LON, limit=5)
    assert out["matched"] is True
    assert out["stars"], "命中分支必须给出至少一颗星"
    names = [s["name"] for s in out["stars"]]
    assert all(n.strip() for n in names), "返回的名字不许为空"
    assert name in names
    assert out["stars"][0]["angular_distance_deg"] < 0.01
    assert out["limit_mag"] == pytest.approx(6.5)
    assert out["total_matched"] >= len(out["stars"])
    assert out["truncated"] is (out["total_matched"] > 5)


def test_identify_direction_never_fabricates_hip_id():
    """HYG 的 -1 是「无编号」，不许被拼成「HIP -1」冒充标识。"""
    out = identify_direction(180.0, 60.0, 120.0, _JD, _LAT, _LON, limit=200)
    assert out["matched"] is True
    for star in out["stars"]:
        assert star["name"].strip()
        if star["name"].startswith("HIP "):
            assert star["hip"] > 0


def test_identify_direction_no_match_below_horizon():
    out = identify_direction(0.0, -60.0, 5.0, _JD, _LAT, _LON)
    assert out["matched"] is False
    assert out["stars"] == []
    assert "没有" in out["reason"]


def test_identify_direction_rejects_bad_inputs():
    out = identify_direction(float("nan"), 20.0, 5.0, _JD, _LAT, _LON)
    assert out["matched"] is False
    assert "输入非法" in out["reason"]

    out2 = identify_direction(10.0, 20.0, 0.0, _JD, _LAT, _LON)
    assert out2["matched"] is False
    assert "fov_deg" in out2["reason"]

    out3 = identify_direction(10.0, 20.0, 5.0, _JD, 120.0, _LON)
    assert out3["matched"] is False
    assert "lat_deg" in out3["reason"]

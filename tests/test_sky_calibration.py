# SPDX-License-Identifier: MIT
"""校准档案测试：往返、损坏、版本、原子写、残差门、病态输入。

全部使用合成观测值（已知方向 + 读数），不联网、不碰硬件；本文件证明
档案协议与拒绝路径，不代表真实设备的校准精度。
"""

import json
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    azalt_from_vec,
    vec_from_azalt,
)
from mbdsdr_ai.sky_calibration import (  # noqa: E402
    MAX_CALIBRATION_RESIDUAL_DEG,
    MIN_PAIR_SEPARATION_DEG,
    SCHEMA_VERSION,
    calibrate,
    calibrate_and_save,
    load_profile,
    save_profile,
)

_FIELDS = {"schema_version", "device_id", "fov_deg", "boresight_az_deg",
           "boresight_alt_deg", "roll_zero_deg", "created_utc",
           "verified_utc", "source"}


def _offset_obs():
    """两个方向都整体偏 1° 的合成校准点。"""
    return [
        {"known_az_deg": 100.0, "known_alt_deg": 40.0,
         "read_az_deg": 99.0, "read_alt_deg": 39.0},
        {"known_az_deg": 130.0, "known_alt_deg": 50.0,
         "read_az_deg": 129.0, "read_alt_deg": 49.0},
    ]


def _good_profile():
    out = calibrate(_offset_obs(), device_id="示例设备", fov_deg=8.0)
    assert out["ok"] is True, out["reason"]
    return out["profile"]


def _rotate_about(axis_az, axis_alt, angle_deg, az, alt):
    """把方向绕指定视轴旋转，返回新的 az/alt（合成 roll 误差）。"""
    u = vec_from_azalt(axis_az, axis_alt)
    v = vec_from_azalt(az, alt)
    k = np.array([[0.0, -u[2], u[1]],
                  [u[2], 0.0, -u[0]],
                  [-u[1], u[0], 0.0]])
    theta = math.radians(angle_deg)
    r = (np.eye(3) + math.sin(theta) * k
         + (1.0 - math.cos(theta)) * (k @ k))
    return azalt_from_vec(r @ v)


# ---------------------------------------------------------------------------
# 求解与往返
# ---------------------------------------------------------------------------
def test_calibrate_solves_offsets_and_roundtrips(tmp_path):
    out = calibrate(_offset_obs(), device_id="示例设备", fov_deg=8.0)
    assert out["ok"] is True, out["reason"]
    profile = out["profile"]
    assert set(profile) == _FIELDS
    assert profile["schema_version"] == SCHEMA_VERSION
    assert profile["device_id"] == "示例设备"
    assert profile["fov_deg"] == pytest.approx(8.0)
    assert profile["source"] == "two_star"
    # 读数比已知方向少 1°：修正量应约为 +1°。单旋转在视轴处的
    # 切平面分解对跨 30° 的两点不是逐点精确，故留 0.1° 容差。
    assert profile["boresight_az_deg"] == pytest.approx(1.0, abs=0.1)
    assert profile["boresight_alt_deg"] == pytest.approx(1.0, abs=0.05)
    # 独立的方位/俯仰偏置不是纯旋转，三参数拟合会让横滚吸收少量残差；
    # 横滚零位是否可辨识由下面的专门用例验证。
    assert abs(profile["roll_zero_deg"]) < 2.0
    assert out["residual_deg"] <= MAX_CALIBRATION_RESIDUAL_DEG

    path = tmp_path / "calibration.json"
    saved = save_profile(profile, str(path))
    assert saved["ok"] is True, saved["reason"]
    loaded = load_profile(str(path))
    assert loaded["ok"] is True
    assert loaded["calibrated"] is True
    assert loaded["profile"] == profile


def test_calibrate_recovers_roll_zero_from_rotated_pair():
    # 已知两点绕视轴被旋转 -1°；修正量应约为 +1°。
    axis_az, axis_alt = 110.0, 40.0
    knowns = [(100.0, 40.0), (120.0, 40.0)]
    obs = []
    for az, alt in knowns:
        read_az, read_alt = _rotate_about(axis_az, axis_alt, -1.0, az, alt)
        obs.append({"known_az_deg": az, "known_alt_deg": alt,
                    "read_az_deg": read_az, "read_alt_deg": read_alt})
    out = calibrate(obs, device_id="示例设备", fov_deg=10.0)
    assert out["ok"] is True, out["reason"]
    assert out["profile"]["roll_zero_deg"] == pytest.approx(1.0, abs=0.05)
    assert abs(out["profile"]["boresight_az_deg"]) < 0.1
    assert abs(out["profile"]["boresight_alt_deg"]) < 0.1


def test_landmark_source_is_preserved():
    out = calibrate(_offset_obs(), device_id="示例设备", fov_deg=8.0,
                    source="landmark")
    assert out["ok"] is True
    assert out["profile"]["source"] == "landmark"


# ---------------------------------------------------------------------------
# 损坏 / 版本 / 不覆盖原文件
# ---------------------------------------------------------------------------
def test_corrupt_file_is_rejected_and_left_untouched(tmp_path):
    path = tmp_path / "calibration.json"
    raw = b'{"schema_version": 1, "device_id": "half'
    path.write_bytes(raw)
    out = load_profile(str(path))
    assert out["ok"] is False
    assert out["calibrated"] is False
    assert out["profile"] is None
    assert out["error"] == "corrupt"
    assert path.read_bytes() == raw


def test_binary_garbage_degrades_without_exception(tmp_path):
    path = tmp_path / "calibration.json"
    raw = b"\xff\xfe\x00binary"
    path.write_bytes(raw)
    out = load_profile(str(path))
    assert out["ok"] is False
    assert out["calibrated"] is False
    assert out["profile"] is None
    assert out["error"] == "unreadable"
    assert path.read_bytes() == raw


def test_unsupported_version_is_rejected_and_left_untouched(tmp_path):
    profile = _good_profile()
    profile["schema_version"] = 99
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    before = path.read_bytes()
    out = load_profile(str(path))
    assert out["ok"] is False
    assert out["calibrated"] is False
    assert out["error"] == "unsupported_version"
    assert "99" in out["reason"]
    assert path.read_bytes() == before


def test_missing_file_degrades_to_uncalibrated(tmp_path):
    out = load_profile(str(tmp_path / "absent.json"))
    assert out["ok"] is False
    assert out["calibrated"] is False
    assert out["error"] == "not_found"


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------
def test_atomic_write_failure_keeps_original_and_no_temp_files(
        tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    first = _good_profile()
    assert save_profile(first, str(path))["ok"] is True
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("mbdsdr_ai.sky_calibration.os.replace", boom)
    second = dict(first)
    second["device_id"] = "另一个设备"
    out = save_profile(second, str(path))
    assert out["ok"] is False
    assert out["error"] == "write_failed"
    assert path.read_bytes() == before
    assert [p for p in os.listdir(tmp_path) if p.startswith(".sky-cal-")] == []


def test_invalid_profile_is_not_written(tmp_path):
    path = tmp_path / "calibration.json"
    broken = dict(_good_profile())
    broken["fov_deg"] = -1.0
    out = save_profile(broken, str(path))
    assert out["ok"] is False
    assert out["error"] == "invalid"
    assert not path.exists()


# ---------------------------------------------------------------------------
# 残差门与病态输入
# ---------------------------------------------------------------------------
def test_residual_gate_rejects_inconsistent_observations():
    obs = [
        {"known_az_deg": 100.0, "known_alt_deg": 40.0,
         "read_az_deg": 100.0, "read_alt_deg": 40.0},
        {"known_az_deg": 130.0, "known_alt_deg": 50.0,
         "read_az_deg": 130.0, "read_alt_deg": 44.0},
    ]
    out = calibrate(obs, device_id="示例设备", fov_deg=8.0)
    assert out["ok"] is False
    assert out["profile"] is None
    assert out["error"] == "residual_too_large"
    assert out["residual_deg"] > MAX_CALIBRATION_RESIDUAL_DEG


def test_too_few_points_rejected():
    out = calibrate(_offset_obs()[:1], device_id="示例设备", fov_deg=8.0)
    assert out["ok"] is False
    assert out["error"] == "too_few_points"


def test_points_too_close_are_pathological():
    obs = [
        {"known_az_deg": 100.0, "known_alt_deg": 40.0,
         "read_az_deg": 100.1, "read_alt_deg": 40.0},
        {"known_az_deg": 100.0 + MIN_PAIR_SEPARATION_DEG * 0.5,
         "known_alt_deg": 40.0,
         "read_az_deg": 100.0, "read_alt_deg": 40.0},
    ]
    out = calibrate(obs, device_id="示例设备", fov_deg=8.0)
    assert out["ok"] is False
    assert out["error"] == "pathological"
    assert "夹角" in out["reason"]


def test_nan_and_out_of_range_are_rejected():
    obs = _offset_obs()
    obs[0]["read_az_deg"] = float("nan")
    out = calibrate(obs, device_id="示例设备", fov_deg=8.0)
    assert out["ok"] is False
    assert out["error"] == "bad_input"

    obs = _offset_obs()
    obs[1]["known_alt_deg"] = 120.0
    out = calibrate(obs, device_id="示例设备", fov_deg=8.0)
    assert out["ok"] is False
    assert out["error"] == "bad_input"


def test_opposite_directions_are_pathological():
    obs = [
        {"known_az_deg": 0.0, "known_alt_deg": 10.0,
         "read_az_deg": 0.0, "read_alt_deg": 10.0},
        {"known_az_deg": 180.0, "known_alt_deg": -10.0,
         "read_az_deg": 180.0, "read_alt_deg": -10.0},
    ]
    out = calibrate(obs, device_id="示例设备", fov_deg=8.0)
    assert out["ok"] is False
    assert out["error"] == "pathological"


# ---------------------------------------------------------------------------
# 写后读回校验
# ---------------------------------------------------------------------------
def _tmp_leftovers(directory):
    return [p for p in os.listdir(directory) if p.startswith(".sky-cal-")]


def test_save_profile_reads_back_written_fields(tmp_path):
    path = tmp_path / "calibration.json"
    profile = _good_profile()
    out = save_profile(profile, str(path))
    assert out["ok"] is True, out["reason"]
    assert out["path"] == os.path.abspath(str(path))
    assert path.exists()
    loaded = load_profile(str(path))
    assert loaded["ok"] is True
    assert {key: loaded["profile"][key] for key in _FIELDS} == profile


def test_readback_failure_keeps_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    assert save_profile(_good_profile(), str(path))["ok"] is True
    before = path.read_bytes()

    changed = dict(_good_profile())
    changed["device_id"] = "另一个设备"
    monkeypatch.setattr(
        "mbdsdr_ai.sky_calibration.load_profile",
        lambda _path: {"ok": False, "calibrated": False, "profile": None,
                       "error": "corrupt", "reason": "模拟读回失败"})
    out = save_profile(changed, str(path))
    assert out["ok"] is False
    assert out["error"] == "verify_failed"
    assert "写后读回校验失败" in out["reason"]
    assert "模拟读回失败" in out["reason"]
    assert path.read_bytes() == before
    assert _tmp_leftovers(tmp_path) == []


def test_readback_mismatch_keeps_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    assert save_profile(_good_profile(), str(path))["ok"] is True
    before = path.read_bytes()

    def tampered(target):
        result = load_profile(target)
        profile = dict(result["profile"])
        profile["fov_deg"] = 3.0
        result["profile"] = profile
        return result

    monkeypatch.setattr("mbdsdr_ai.sky_calibration.load_profile", tampered)
    out = save_profile(dict(_good_profile()), str(path))
    assert out["ok"] is False
    assert out["error"] == "verify_failed"
    assert "fov_deg" in out["reason"]
    assert path.read_bytes() == before
    assert _tmp_leftovers(tmp_path) == []


# ---------------------------------------------------------------------------
# 校准 + 落盘
# ---------------------------------------------------------------------------
def test_calibrate_and_save_writes_matching_profile(tmp_path):
    path = tmp_path / "calibration.json"
    out = calibrate_and_save(_offset_obs(), device_id="示例设备",
                             fov_deg=8.0, path=str(path))
    assert out["ok"] is True, out["reason"]
    assert os.path.isabs(out["path"])
    assert out["residual_deg"] <= MAX_CALIBRATION_RESIDUAL_DEG
    assert path.exists()

    loaded = load_profile(str(path))
    assert loaded["ok"] is True
    assert loaded["profile"]["device_id"] == "示例设备"
    assert loaded["profile"]["fov_deg"] == pytest.approx(8.0)
    assert loaded["profile"] == out["profile"]


def test_calibrate_and_save_does_not_write_on_pathological_input(tmp_path):
    path = tmp_path / "calibration.json"
    obs = [
        {"known_az_deg": 100.0, "known_alt_deg": 40.0,
         "read_az_deg": 100.1, "read_alt_deg": 40.0},
        {"known_az_deg": 100.0 + MIN_PAIR_SEPARATION_DEG * 0.5,
         "known_alt_deg": 40.0,
         "read_az_deg": 100.0, "read_alt_deg": 40.0},
    ]
    out = calibrate_and_save(obs, device_id="示例设备", fov_deg=8.0,
                             path=str(path))
    assert out["ok"] is False
    assert out["error"] == "pathological"
    assert not path.exists()
    assert _tmp_leftovers(tmp_path) == []


def test_calibrate_and_save_returns_save_failure(tmp_path):
    out = calibrate_and_save(_offset_obs(), device_id="示例设备",
                             fov_deg=8.0, path=None)
    assert out["ok"] is False
    assert out["error"] == "bad_path"
    assert out["reason"]
    assert _tmp_leftovers(tmp_path) == []

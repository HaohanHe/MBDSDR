# SPDX-License-Identifier: MIT
"""校准档案叠加到位姿/视图的契约（Task 95）。

两部分：
1. 纯函数 ``apply_calibration``：方位跨 0°、alt/roll 偏置、窄视场不被
   30° 夹、入参不被修改、非法档案一律拒绝；
2. 离屏 ``RFSkyView``：带校准档案时视图真的落在校准后的指向/视场；
   档案损坏时退回未校准的原行为，并把原因记在
   ``sky_calibration_error``——不静默采用半套偏置。

全部离线、合成数据；不联网、不碰硬件。
"""

import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.sky_pointing import (  # noqa: E402
    apply_calibration,
    view_params,
)

POSE = {"valid": True, "az_deg": 359.5, "alt_deg": 40.0, "roll_deg": 350.0,
        "fov_deg": 60.0, "accuracy_deg": 2.0}

CAL = {
    "schema_version": 1,
    "device_id": "示例设备",
    "fov_deg": 2.3,
    "boresight_az_deg": 1.0,
    "boresight_alt_deg": 0.5,
    "roll_zero_deg": 20.0,
    "created_utc": "2026-11-17T00:00:00Z",
    "verified_utc": "2026-11-17T00:00:00Z",
    "source": "two_star",
}


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def test_offsets_apply_with_azimuth_wrapping_across_zero():
    out = apply_calibration(POSE, CAL)
    assert out["valid"] is True
    assert out["calibration_applied"] is True
    assert out["az_deg"] == pytest.approx(0.5)       # 359.5 + 1.0 -> 0.5
    assert out["alt_deg"] == pytest.approx(40.5)
    assert out["roll_deg"] == pytest.approx(10.0)    # 350 + 20 -> 10
    assert out["fov_deg"] == pytest.approx(2.3)


def test_narrow_fov_is_not_clamped_by_desktop_floor():
    out = apply_calibration(POSE, CAL)
    # 校准路径按「视场即目标视图下限」调用 view_params，2.3° 原样保留；
    # 不传 min_fov 时才会被默认 30° 夹掉（既有语义，本任务不改）。
    params = view_params(out, min_fov=out["fov_deg"])
    assert params["valid"] is True
    assert params["fov_deg"] == pytest.approx(2.3)
    assert params["fov_clamped"] is False
    assert params["center_az"] == pytest.approx(0.5)
    assert params["center_alt"] == pytest.approx(40.5)
    assert params["rotation_deg"] == pytest.approx(10.0)

    clamped = view_params(out)
    assert clamped["fov_deg"] == pytest.approx(30.0)
    assert clamped["fov_clamped"] is True


def test_input_dicts_are_not_modified():
    pose_before = copy.deepcopy(POSE)
    cal_before = copy.deepcopy(CAL)
    out = apply_calibration(POSE, CAL)
    assert POSE == pose_before
    assert CAL == cal_before
    assert out is not POSE


def test_negative_azimuth_offset_wraps_to_359():
    cal = dict(CAL, boresight_az_deg=-1.5)
    out = apply_calibration(dict(POSE, az_deg=1.0), cal)
    assert out["valid"] is True
    assert out["az_deg"] == pytest.approx(359.5)


@pytest.mark.parametrize("pose,cal,expected_error", [
    ({"valid": False, "reason": "没有可用观测"}, CAL, "bad_pose"),
    (dict(POSE, az_deg=float("nan")), CAL, "bad_pose"),
    (dict(POSE, alt_deg=95.0), CAL, "bad_pose"),
    (dict(POSE, roll_deg=None), CAL, "bad_pose"),
    (dict(POSE, alt_deg=89.9), dict(CAL, boresight_alt_deg=5.0),
     "bad_calibration"),
    (POSE, None, "bad_calibration"),
    (POSE, {"fov_deg": 2.3, "boresight_az_deg": 1.0}, "bad_calibration"),
    (POSE, dict(CAL, fov_deg=0.0), "bad_calibration"),
    (POSE, dict(CAL, fov_deg=0.3), "bad_calibration"),
    (POSE, dict(CAL, boresight_alt_deg=95.0), "bad_calibration"),
    (POSE, dict(CAL, boresight_az_deg="abc"), "bad_calibration"),
    (POSE, dict(CAL, schema_version=2), "bad_calibration"),
])
def test_invalid_inputs_are_rejected_with_reason(pose, cal, expected_error):
    out = apply_calibration(pose, cal)
    assert out["valid"] is False
    assert out["calibration_applied"] is False
    assert out["error"] == expected_error
    assert out["reason"]
    assert out["az_deg"] is None and out["alt_deg"] is None


def test_non_dict_pose_is_rejected():
    out = apply_calibration(None, CAL)
    assert out["valid"] is False
    assert out["error"] == "bad_pose"
    assert out["reason"]


# ---------------------------------------------------------------------------
# 离屏视图接线
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _view():
    from rf_sky_view import RFSkyView
    v = RFSkyView()
    v.resize(800, 600)
    return v


VIEW_POSE = {"valid": True, "az_deg": 33.0, "alt_deg": 52.0,
             "roll_deg": 0.0, "fov_deg": 8.0}


def test_view_applies_calibration_to_center_and_fov(qapp):
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        assert v.update_sky_pose(VIEW_POSE, [], "L3",
                                 calibration=CAL) is True
        assert v.view_state.center_az == pytest.approx(34.0)
        assert v.view_state.center_alt == pytest.approx(52.5)
        assert v.view_state.fov_deg == pytest.approx(2.3)
        assert v.view_state.rotation == pytest.approx(20.0)
        assert v.sky_calibration_error == ""
        v.grab()
    finally:
        v.close()


def test_view_falls_back_to_uncalibrated_pose_on_broken_profile(qapp):
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        broken = {"fov_deg": 2.3, "boresight_az_deg": 1.0}
        assert v.update_sky_pose(VIEW_POSE, [], "L3",
                                 calibration=broken) is True
        # 档案缺字段：不采用部分偏置，按未校准的原行为套用位姿。
        assert v.view_state.center_az == pytest.approx(33.0)
        assert v.view_state.center_alt == pytest.approx(52.0)
        assert v.view_state.fov_deg == pytest.approx(8.0)
        assert "缺少字段" in v.sky_calibration_error

        # 先成功校准，再喂坏档案：视图回到未校准口径，原因仍被记录。
        assert v.update_sky_pose(VIEW_POSE, [], "L3", calibration=CAL) is True
        assert v.view_state.center_az == pytest.approx(34.0)
        assert v.sky_calibration_error == ""

        assert v.update_sky_pose(VIEW_POSE, [], "L3",
                                 calibration=broken) is True
        assert v.view_state.center_az == pytest.approx(33.0)
        assert v.view_state.center_alt == pytest.approx(52.0)
        assert v.sky_calibration_error
    finally:
        v.close()


def test_view_records_no_calibration_error_for_bad_pose(qapp):
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        v.view_state.look_at(10.0, 20.0)
        before = (float(v.view_state.center_az),
                  float(v.view_state.center_alt))
        assert v.update_sky_pose({"valid": False}, [], "L1",
                                 calibration=CAL) is False
        # 位姿不可用不是校准档案的错，不误记原因，视图也不动。
        assert v.sky_calibration_error == ""
        assert (float(v.view_state.center_az),
                float(v.view_state.center_alt)) == before
        assert v.sky_hud["hint"] == "还没对准"
    finally:
        v.close()

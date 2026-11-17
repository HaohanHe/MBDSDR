# SPDX-License-Identifier: MIT
"""相机导演的行为测试：镜头选择、数字变焦、缺镜头时的诚实降级。

全部用合成能力档案，但档案的结构与真机探针完全一致；合成档案只能进测试，
这一点本身也有测试锁住（``synthetic_fixture`` 必须被拒绝）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.camera_capability import (  # noqa: E402
    CAN, CANNOT, UNKNOWN, PROVENANCE_MEASURED, PROVENANCE_SYNTHETIC,
    VERDICT_ALLOW, VERDICT_NEEDS_PROBE, VERDICT_REFUSE, VERDICT_WARN,
    CameraCapability, DeviceProfile,
)
from mbdsdr_ai.camera_tools import plan_from_args  # noqa: E402
from mbdsdr_ai.capture_plan import (  # noqa: E402
    GOAL_DETAIL, GOAL_PHOTO, GOAL_PREVIEW, GOAL_RECORD, GOAL_SOLVE, GOAL_STACK,
    CaptureGoal, TelescopeOptics, fov_at_zoom, plan_capture, zoom_for_fill,
)


def cam(**kw) -> CameraCapability:
    base = dict(id="0", role_hint="wide", focal_mm=4.2, f_number=1.8,
                sensor_diag_mm=9.8, max_exposure_s=1.0, manual_exposure=CAN,
                focus_lock=CAN, openable_directly=CAN,
                evidence=("dumpsys:media.camera",))
    base.update(kw)
    return CameraCapability(**base)


def prof(*cams, provenance=PROVENANCE_MEASURED, **kw) -> DeviceProfile:
    kw.setdefault("imu_rate_hz", 200.0)
    return DeviceProfile(cameras=tuple(cams), provenance=provenance,
                         model="test", **kw)


WIDE = cam(id="0", role_hint="wide", focal_mm=4.2, f_number=1.8,
           sensor_diag_mm=9.8)                     # 视场约 99°
TELE = cam(id="2", role_hint="telephoto", focal_mm=16.3, f_number=2.8,
           sensor_diag_mm=7.1)                     # 视场约 24.6°
PERI = cam(id="3", role_hint="periscope", focal_mm=16.3, f_number=3.4,
           sensor_diag_mm=7.1, openable_directly=CANNOT, logical_id="0")


# ---------------------------------------------------------------------------
# 解算：要宽，不要长焦
# ---------------------------------------------------------------------------
def test_solve_prefers_wide_even_when_telephoto_exists():
    plan = plan_capture(prof(WIDE, TELE), CaptureGoal(purpose=GOAL_SOLVE))
    assert plan.verdict == VERDICT_ALLOW
    assert plan.camera["id"] == "0"
    assert plan.fov_deg > 90
    assert plan.executable is True
    # 落选的长焦要交代原因，不能沉默
    why = {a["id"]: a["why_not"] for a in plan.alternatives}
    assert why["2"] == "narrower_field_than_selected"


def test_model_asking_periscope_for_solve_is_redirected_with_reason():
    plan = plan_capture(prof(WIDE, PERI), CaptureGoal(
        purpose=GOAL_SOLVE, want_role="periscope"))
    assert plan.camera["id"] == "0"
    assert any("role_unsuitable_for_solve" in n for n in plan.notes)
    assert plan.verdict == VERDICT_ALLOW


def test_solve_with_only_narrow_lens_refuses_instead_of_pretending():
    narrow = cam(id="9", role_hint="telephoto", focal_mm=100.0, f_number=4.0,
                 sensor_diag_mm=7.1)                # 视场约 4.1°
    plan = plan_capture(prof(narrow), CaptureGoal(purpose=GOAL_SOLVE))
    assert plan.verdict == VERDICT_REFUSE
    assert any("no_wide_field" in r for r in plan.reasons)
    assert plan.executable is False


def test_min_fov_is_a_parameter_not_a_hidden_constant():
    plan = plan_capture(prof(TELE), CaptureGoal(
        purpose=GOAL_SOLVE, min_fov_deg=10.0))
    assert plan.verdict == VERDICT_ALLOW      # 24.6° > 10°
    strict = plan_capture(prof(TELE), CaptureGoal(
        purpose=GOAL_SOLVE, min_fov_deg=30.0))
    assert strict.verdict == VERDICT_REFUSE


# ---------------------------------------------------------------------------
# 特写：有长焦用长焦，没有就说「数字变焦不增加细节」
# ---------------------------------------------------------------------------
def test_detail_picks_telephoto_and_computes_zoom_for_fill():
    plan = plan_capture(prof(WIDE, TELE), CaptureGoal(
        purpose=GOAL_DETAIL, target_deg=8.0, fill_fraction=0.5))
    assert plan.camera["id"] == "2"
    assert plan.zoom == pytest.approx(1.55, abs=0.02)
    assert plan.target_fill == pytest.approx(0.5, abs=1e-6)
    assert plan.verdict == VERDICT_WARN      # 数字变焦仍标为代价
    assert any("digital_zoom" in d for d in plan.degradations)


def test_detail_without_telephoto_refuses_optical_pretence():
    plan = plan_capture(prof(WIDE), CaptureGoal(
        purpose=GOAL_DETAIL, target_deg=0.5))
    assert plan.verdict == VERDICT_REFUSE
    assert any("no_optical_telephoto" in r for r in plan.reasons)
    assert plan.zoom == pytest.approx(10.0)  # 封顶，不假装能无限放大
    assert plan.camera["id"] == "0"


def test_digital_only_is_allowed_when_user_explicitly_accepts_it():
    plan = plan_capture(prof(WIDE), CaptureGoal(
        purpose=GOAL_DETAIL, target_deg=4.0, require_optical=False))
    assert plan.verdict == VERDICT_WARN
    assert plan.executable is True
    assert any("digital_zoom_no_new_information" in d for d in plan.degradations)


def test_asking_for_missing_periscope_explains_alternatives():
    plan = plan_capture(prof(WIDE), CaptureGoal(
        purpose=GOAL_DETAIL, target_deg=0.5, want_role="periscope"))
    assert any("want_role_not_present" in r for r in plan.reasons)
    assert plan.camera["id"] == "0"
    assert plan.executable is False


def test_periscope_reachable_via_logical_camera_is_selectable():
    plan = plan_capture(prof(WIDE, PERI), CaptureGoal(
        purpose=GOAL_DETAIL, target_deg=10.0, want_role="periscope"))
    assert plan.camera["id"] == "3"
    assert plan.camera["openable_directly"] == CANNOT
    assert plan.camera["logical_id"] == "0"
    assert plan.executable is True


def test_unknown_calibration_is_not_reported_as_pixels():
    plan = plan_capture(prof(WIDE, TELE), CaptureGoal(
        purpose=GOAL_DETAIL, target_deg=8.0))
    assert any("arcsec_per_px_unknown" in n for n in plan.notes)
    assert not any("target_span_px" in n for n in plan.notes)


def test_calibrated_scale_reports_target_pixels():
    calibrated = cam(id="2", role_hint="telephoto", focal_mm=16.3,
                     f_number=2.8, sensor_diag_mm=7.1,
                     calibrated_arcsec_per_px=20.0)
    plan = plan_capture(prof(calibrated), CaptureGoal(
        purpose=GOAL_DETAIL, target_deg=8.0, fill_fraction=0.5))
    assert any(n.startswith("target_span_px:") for n in plan.notes)


# ---------------------------------------------------------------------------
# 录像 / 堆栈 / 取景
# ---------------------------------------------------------------------------
def test_record_clamps_exposure_to_frame_time():
    plan = plan_capture(prof(WIDE), CaptureGoal(
        purpose=GOAL_RECORD, exposure_s=1.0, fps=30.0))
    assert plan.mode == "video"
    assert plan.exposure_s == pytest.approx(1.0 / 30.0)
    assert any("exposure_longer_than_frame" in r for r in plan.reasons)
    assert plan.verdict in (VERDICT_WARN, VERDICT_NEEDS_PROBE)


def test_stack_keeps_requested_frame_count():
    plan = plan_capture(prof(WIDE), CaptureGoal(
        purpose=GOAL_STACK, frames=16, exposure_s=0.5))
    assert plan.mode == "stack"
    assert plan.frames == 16
    assert plan.exposure_s == pytest.approx(0.5)


def test_preview_uses_wide_and_does_not_zoom():
    plan = plan_capture(prof(WIDE, TELE), CaptureGoal(purpose=GOAL_PREVIEW))
    assert plan.mode == "preview"
    assert plan.camera["id"] == "0"
    assert plan.zoom == 1.0


def test_exposure_request_above_device_max_is_clamped_and_explained():
    short = cam(id="0", max_exposure_s=0.5)
    plan = plan_capture(prof(short), CaptureGoal(
        purpose=GOAL_SOLVE, exposure_s=2.0))
    assert plan.exposure_s == pytest.approx(0.5)
    assert any("exposure_exceeds_device_max" in r for r in plan.reasons)


# ---------------------------------------------------------------------------
# 望远镜光路
# ---------------------------------------------------------------------------
XA90P = TelescopeOptics(aperture_mm=90.0, focal_mm=1000.0,
                        magnification=40.0, eyepiece_afov_deg=52.0,
                        true_field_deg=1.2)


def test_telescope_wide_camera_sees_whole_true_field():
    plan = plan_capture(prof(WIDE), CaptureGoal(
        purpose=GOAL_SOLVE, telescope=XA90P))
    assert plan.captured_true_field_deg == pytest.approx(1.2)
    assert XA90P.exit_pupil_mm == pytest.approx(2.25)


def test_telescope_narrow_camera_crops_center_and_zoom_crops_more():
    narrow = cam(id="2", role_hint="telephoto", focal_mm=30.0, f_number=3.0,
                 sensor_diag_mm=7.1)              # 视场约 13.5°
    plan = plan_capture(prof(narrow), CaptureGoal(
        purpose=GOAL_SOLVE, min_fov_deg=5.0, telescope=XA90P))
    # 1.2° × 13.5/52 ≈ 0.311°
    assert plan.captured_true_field_deg == pytest.approx(0.311, abs=0.005)


def test_pupil_underfilled_is_a_warning_not_a_silent_success():
    tiny = cam(id="0", focal_mm=2.0, f_number=2.4, sensor_diag_mm=9.8)
    plan = plan_capture(prof(tiny), CaptureGoal(
        purpose=GOAL_SOLVE, telescope=XA90P))
    assert any("pupil_underfilled" in r for r in plan.reasons)
    assert plan.verdict == VERDICT_WARN


# ---------------------------------------------------------------------------
# 档案纪律
# ---------------------------------------------------------------------------
def test_synthetic_profile_cannot_drive_capture():
    plan = plan_capture(prof(WIDE, provenance=PROVENANCE_SYNTHETIC),
                        CaptureGoal(purpose=GOAL_SOLVE))
    assert plan.verdict == VERDICT_REFUSE
    assert any("profile_not_measured" in r for r in plan.reasons)


def test_invalid_profile_is_refused_with_violations():
    bad = cam(manual_exposure="yes")
    plan = plan_capture(prof(bad), CaptureGoal(purpose=GOAL_SOLVE))
    assert plan.verdict == VERDICT_REFUSE
    assert plan.reasons[0] == "invalid_profile"
    assert len(plan.reasons) > 1


def test_unknown_manual_exposure_needs_probe_not_allow():
    c = cam(id="0", manual_exposure=UNKNOWN)
    plan = plan_capture(prof(c), CaptureGoal(purpose=GOAL_SOLVE))
    assert plan.verdict == VERDICT_NEEDS_PROBE
    assert any("manual_exposure_unknown" in r for r in plan.reasons)


def test_lens_without_manual_exposure_is_excluded():
    fixed = cam(id="1", manual_exposure=CANNOT)
    plan = plan_capture(prof(fixed), CaptureGoal(purpose=GOAL_SOLVE))
    assert plan.verdict == VERDICT_REFUSE
    assert any("no_usable_camera" in r for r in plan.reasons)
    assert plan.alternatives[0]["why_not"] == "no_manual_exposure"


def test_unknown_purpose_raises():
    with pytest.raises(ValueError):
        plan_capture(prof(WIDE), CaptureGoal(purpose="make_me_a_movie"))


# ---------------------------------------------------------------------------
# 几何
# ---------------------------------------------------------------------------
def test_zoom_for_fill_roundtrip():
    fov = 99.0
    zoom = zoom_for_fill(fov, 8.0, 0.5)
    assert 8.0 / fov_at_zoom(fov, zoom) == pytest.approx(0.5, abs=1e-9)


def test_fov_at_zoom_is_monotonic_and_rejects_bad_input():
    assert fov_at_zoom(60.0, 2.0) < 60.0
    assert fov_at_zoom(60.0, 1.0) == pytest.approx(60.0)
    with pytest.raises(ValueError):
        fov_at_zoom(0.0, 2.0)


# ---------------------------------------------------------------------------
# 模型工具面
# ---------------------------------------------------------------------------
def test_tool_without_profile_is_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("MBDSDR_DEVICE_PROFILE", str(tmp_path / "missing.json"))
    res = plan_from_args({"purpose": GOAL_SOLVE})
    assert res["ok"] is False
    assert res["reason"] == "no_device_profile"
    assert "probe_device" in res["hint"]


def test_tool_accepts_inline_profile_and_returns_plan():
    res = plan_from_args({
        "purpose": GOAL_SOLVE,
        "profile": prof(WIDE, TELE).to_dict(),
    })
    assert res["ok"] is True
    assert res["camera"]["id"] == "0"
    assert res["execution"].startswith("plan_only")
    json.dumps(res, ensure_ascii=False)      # 必须可序列化


def test_tool_rejects_unknown_purpose():
    res = plan_from_args({"purpose": "nope"})
    assert res["ok"] is False
    assert res["reason"].startswith("unknown_purpose")


def test_tool_rejects_unknown_role():
    res = plan_from_args({"purpose": GOAL_SOLVE, "want_role": "xray",
                          "profile": prof(WIDE).to_dict()})
    assert res["ok"] is False
    assert res["reason"].startswith("unknown_role")


def test_tool_passes_telescope_optics_through():
    res = plan_from_args({
        "purpose": GOAL_SOLVE,
        "profile": prof(WIDE).to_dict(),
        "telescope": {"aperture_mm": 90.0, "focal_mm": 1000.0,
                      "magnification": 40.0, "eyepiece_afov_deg": 52.0,
                      "true_field_deg": 1.2},
    })
    assert res["ok"] is True
    assert res["captured_true_field_deg"] == pytest.approx(1.2)


def test_register_camera_tools_exposes_plan_tool():
    from mbdsdr_ai.camera_tools import register_camera_tools
    from mbdsdr_ai.tool_registry import ToolRegistry

    class _Agent:
        def __init__(self):
            self.tool_registry = ToolRegistry()

    agent = _Agent()
    register_camera_tools(agent)
    entry = agent.tool_registry.tools["camera_capture_plan"]
    assert entry["category"] == "camera"
    # 规划工具是只读的：不能被自动判成写工具而走确认门
    assert entry.get("write") is not True


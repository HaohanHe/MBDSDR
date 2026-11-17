# SPDX-License-Identifier: MIT
"""S0 设备探针的离线验证：三态纪律、意图裁决、光学换算、无设备诚实 SKIP。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import probe_device as pd


# ---------------------------------------------------------------------------
# 构造器
# ---------------------------------------------------------------------------
def cam(**kw) -> pd.CameraCapability:
    base = dict(id="0", logical_id="", role_hint="wide", focal_mm=4.2, f_number=1.8,
                sensor_diag_mm=9.8, max_exposure_s=1.0, manual_exposure=pd.CAN,
                focus_lock=pd.CAN, openable_directly=pd.CAN,
                evidence=("dumpsys:media.camera",))
    base.update(kw)
    return pd.CameraCapability(**base)


def prof(*cams, provenance=pd.PROVENANCE_MEASURED, **kw) -> pd.DeviceProfile:
    kw.setdefault("imu_rate_hz", 200.0)
    return pd.DeviceProfile(cameras=tuple(cams), provenance=provenance, model="test", **kw)


# ---------------------------------------------------------------------------
# 光学
# ---------------------------------------------------------------------------
def test_exit_pupil_and_effective_focal():
    assert pd.exit_pupil_mm(90.0, 40.0) == pytest.approx(2.25)
    assert pd.effective_focal_mm(16.3, 40.0) == pytest.approx(652.0)


def test_entry_pupil_of_periscope_cam():
    c = cam(focal_mm=16.3, f_number=3.4, role_hint="periscope")
    assert c.entrance_pupil_mm == pytest.approx(4.794, abs=1e-3)
    assert c.fov_diag_deg == pytest.approx(33.5, abs=0.5)


def test_captured_true_field_when_lens_crops_image_circle():
    # 目镜把 1.2° 铺在 52° 里；手机只看到 22.3° → 0.515°
    got = pd.captured_true_field_deg(1.2, 22.3, 52.0)
    assert got == pytest.approx(0.5146, abs=1e-3)


def test_captured_true_field_when_lens_covers_whole_circle():
    assert pd.captured_true_field_deg(1.2, 84.0, 52.0) == pytest.approx(1.2)


def test_fov_from_star_pair_recovers_scale():
    # 5° 角距落在 500 px 上 → 焦距 500/(2·tan2.5°) ≈ 5725.9 px；4000 px 轴 → 38.51°
    res = pd.fov_from_star_pair(5.0, (100.0, 200.0), (600.0, 200.0), axis_px=4000.0)
    assert res["focal_px"] == pytest.approx(5725.94, abs=0.05)
    assert res["arcsec_per_px"] == pytest.approx(36.0, abs=1e-6)
    assert res["fov_axis_deg"] == pytest.approx(38.51, abs=0.02)


@pytest.mark.parametrize("sep,p1,p2", [(0.0, (0, 0), (1, 1)), (1.0, (5, 5), (5, 5))])
def test_fov_from_star_pair_rejects_degenerate(sep, p1, p2):
    with pytest.raises(ValueError):
        pd.fov_from_star_pair(sep, p1, p2)


# ---------------------------------------------------------------------------
# 三态纪律
# ---------------------------------------------------------------------------
def test_role_without_optics_is_a_violation():
    bad = cam(role_hint="periscope", focal_mm=None, f_number=None)
    assert any("角色" in v for v in pd.validate_profile(prof(bad)))


def test_claim_without_evidence_is_a_violation():
    bad = cam(manual_exposure=pd.CAN, evidence=())
    assert any("evidence" in v for v in pd.validate_profile(prof(bad)))


def test_bad_tri_state_is_a_violation():
    bad = cam(focus_lock="yes")
    assert any("三态" in v for v in pd.validate_profile(prof(bad)))


def test_clean_profile_has_no_violation():
    assert pd.validate_profile(prof(cam())) == []


# ---------------------------------------------------------------------------
# 意图裁决
# ---------------------------------------------------------------------------
def test_unmeasured_profile_cannot_drive_intent():
    res = pd.check_lens_intent(prof(cam(), provenance=pd.PROVENANCE_SYNTHETIC), "wide")
    assert res["verdict"] == pd.VERDICT_REFUSE
    assert any("profile_not_measured" in r for r in res["reasons"])


def test_missing_lens_lists_alternatives():
    res = pd.check_lens_intent(prof(cam()), "periscope")
    assert res["verdict"] == pd.VERDICT_REFUSE
    assert any("no_such_lens" in r for r in res["reasons"])
    assert res["numbers"]["available_roles"] == ["wide"]


def test_periscope_that_crops_the_image_circle_warns_with_math():
    # 潜望镜不能直接开，但有逻辑相机路径 → 走逻辑流，且必须报「只截中心」
    peri = cam(id="3", logical_id="0", role_hint="periscope", openable_directly=pd.CANNOT,
               focal_mm=16.3, f_number=3.4, sensor_diag_mm=7.1)
    res = pd.check_lens_intent(prof(cam(), peri), "periscope",
                               aperture_mm=90.0, magnification=40.0,
                               eyepiece_afov_deg=52.0, true_field_deg=1.2)
    assert res["verdict"] == pd.VERDICT_WARN
    assert any("crops_image_circle" in r for r in res["reasons"])
    assert res["numbers"]["exit_pupil_mm"] == pytest.approx(2.25)
    assert res["numbers"]["captured_true_field_deg"] < 0.6
    assert res["numbers"]["via"] == "logical"


def test_lens_not_exposed_and_no_logical_path_refuses():
    peri = cam(id="3", logical_id="", role_hint="periscope",
               focal_mm=16.3, f_number=3.4, openable_directly=pd.CANNOT)
    res = pd.check_lens_intent(prof(peri), "periscope")
    assert res["verdict"] == pd.VERDICT_REFUSE
    assert any("lens_not_exposed" in r for r in res["reasons"])


def test_lens_not_exposed_but_reachable_via_logical_camera_is_allowed():
    peri = cam(id="3", logical_id="0", role_hint="periscope",
               focal_mm=16.3, f_number=3.4, openable_directly=pd.CANNOT)
    res = pd.check_lens_intent(prof(peri), "periscope")
    assert res["verdict"] == pd.VERDICT_ALLOW
    assert res["numbers"]["via"] == "logical"


def test_no_manual_exposure_refuses_because_stars_need_locked_exposure():
    c = cam(manual_exposure=pd.CANNOT)
    res = pd.check_lens_intent(prof(c), "wide")
    assert res["verdict"] == pd.VERDICT_REFUSE
    assert any("no_manual_exposure" in r for r in res["reasons"])


def test_unknown_openability_needs_probe_not_silent_success():
    c = cam(openable_directly=pd.UNKNOWN, logical_id="")
    res = pd.check_lens_intent(prof(c), "wide")
    assert res["verdict"] == pd.VERDICT_NEEDS_PROBE
    assert any("openability_unknown" in r for r in res["reasons"])


# ---------------------------------------------------------------------------
# 采集：无设备必须 SKIP，不产假档案
# ---------------------------------------------------------------------------
def test_parse_adb_devices_offline_is_empty():
    assert pd.parse_adb_devices("List of devices attached\n\n") == []


def test_parse_adb_devices_online():
    text = "List of devices attached\nR58M123ABC\tdevice product:x model:y\n"
    assert pd.parse_adb_devices(text) == ["R58M123ABC"]


def test_collect_without_device_is_skip(tmp_path):
    def fake_run(cmd, timeout=25.0):
        return subprocess.CompletedProcess(list(cmd), 0, "List of devices attached\n\n", "")

    res = pd.collect_raw(tmp_path / "out", run=fake_run)
    assert res["status"] == "SKIP"
    assert not (tmp_path / "out").exists()


def test_collect_with_device_writes_raw_and_marks_unparsed(tmp_path):
    def fake_run(cmd, timeout=25.0):
        argv = list(cmd)
        if argv[:2] == ["adb", "devices"]:
            return subprocess.CompletedProcess(argv, 0, "List of devices attached\nXYZ\tdevice\n", "")
        if argv[-1] == "ro.product.model" or "ro.product.model" in argv:
            return subprocess.CompletedProcess(argv, 0, "TestPhone\n", "")
        return subprocess.CompletedProcess(argv, 0, f"dumpsys {' '.join(argv[3:])}\n", "")

    out = tmp_path / "raw"
    res = pd.collect_raw(out, run=fake_run)
    assert res["status"] == "PASS"
    meta = (out / "probe_meta.json").read_text(encoding="utf-8")
    assert '"parsed": false' in meta
    assert '"provenance": "measured_device_raw"' in meta
    assert (out / "media_camera.txt").is_file()

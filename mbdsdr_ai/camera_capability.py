# SPDX-License-Identifier: MIT
"""手机镜头能力契约：把「这台手机能干什么」变成可核对的三态事实。

为什么单独成模块
----------------
采集导演（``capture_plan``）与真机探针（``tools/probe_device.py``）都必须
读同一份能力档案。能力判断一旦有两份实现，模型就会拿到两种互相矛盾的
答案——这比没有能力档案更糟。

三条纪律
--------
* ``can`` / ``cannot`` / ``unknown`` 三态，``unknown`` **永远不等于可以**；
* 任何 ``can`` 都必须带证据（dumpsys 字段名、实测记录），空口声称不算；
* 只有 ``measured_device``（真机实测）能驱动产品决策，合成档案只能进测试。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

CAN = "can"
CANNOT = "cannot"
UNKNOWN = "unknown"
TRI_STATES = (CAN, CANNOT, UNKNOWN)

ROLES = ("wide", "ultrawide", "telephoto", "periscope", "unknown")

PROVENANCE_MEASURED = "measured_device"
PROVENANCE_RAW = "measured_device_raw"
PROVENANCE_SYNTHETIC = "synthetic_fixture"
PROVENANCES = (PROVENANCE_MEASURED, PROVENANCE_RAW, PROVENANCE_SYNTHETIC)

VERDICT_ALLOW = "allow"
VERDICT_WARN = "allow_with_warning"
VERDICT_NEEDS_PROBE = "needs_probe"
VERDICT_REFUSE = "refuse"


# ---------------------------------------------------------------------------
# 契约
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CameraCapability:
    """一颗镜头的事实。``unknown`` 表示没测到，不表示不支持。"""

    id: str
    logical_id: str = ""
    role_hint: str = UNKNOWN
    focal_mm: Optional[float] = None
    f_number: Optional[float] = None
    sensor_diag_mm: Optional[float] = None
    max_exposure_s: Optional[float] = None
    manual_exposure: str = UNKNOWN
    focus_lock: str = UNKNOWN
    openable_directly: str = UNKNOWN
    evidence: Tuple[str, ...] = ()
    #: 实拍标定得到的角分辨率（角秒/像素）。没有标定就是 None，不许用
    #: 传感器尺寸和「假设的像素数」反推——那会把规格表当成实测。
    calibrated_arcsec_per_px: Optional[float] = None

    @property
    def entrance_pupil_mm(self) -> Optional[float]:
        if self.focal_mm is None or not self.f_number:
            return None
        return self.focal_mm / self.f_number

    @property
    def fov_diag_deg(self) -> Optional[float]:
        if self.focal_mm is None or self.sensor_diag_mm is None or self.focal_mm <= 0:
            return None
        return 2.0 * math.degrees(math.atan(self.sensor_diag_mm / (2.0 * self.focal_mm)))

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "logical_id": self.logical_id,
            "role_hint": self.role_hint,
            "focal_mm": self.focal_mm,
            "f_number": self.f_number,
            "sensor_diag_mm": self.sensor_diag_mm,
            "max_exposure_s": self.max_exposure_s,
            "manual_exposure": self.manual_exposure,
            "focus_lock": self.focus_lock,
            "openable_directly": self.openable_directly,
            "evidence": list(self.evidence),
            "calibrated_arcsec_per_px": self.calibrated_arcsec_per_px,
            "derived": {
                "entrance_pupil_mm": self.entrance_pupil_mm,
                "fov_diag_deg": self.fov_diag_deg,
            },
        }

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "CameraCapability":
        def _f(key: str) -> Optional[float]:
            v = d.get(key)
            return float(v) if v is not None else None

        return cls(
            id=str(d.get("id", "")),
            logical_id=str(d.get("logical_id", "") or ""),
            role_hint=str(d.get("role_hint", UNKNOWN)),
            focal_mm=_f("focal_mm"),
            f_number=_f("f_number"),
            sensor_diag_mm=_f("sensor_diag_mm"),
            max_exposure_s=_f("max_exposure_s"),
            manual_exposure=str(d.get("manual_exposure", UNKNOWN)),
            focus_lock=str(d.get("focus_lock", UNKNOWN)),
            openable_directly=str(d.get("openable_directly", UNKNOWN)),
            evidence=tuple(str(x) for x in (d.get("evidence") or ())),
            calibrated_arcsec_per_px=_f("calibrated_arcsec_per_px"),
        )


@dataclass(frozen=True)
class DeviceProfile:
    cameras: Tuple[CameraCapability, ...] = ()
    imu_rate_hz: Optional[float] = None
    imu_timebase: str = UNKNOWN
    imu_align_to_camera: str = UNKNOWN
    gnss_has_pps: str = UNKNOWN
    model: str = ""
    provenance: str = PROVENANCE_SYNTHETIC
    probed_at: str = ""

    @property
    def usable_for_intent(self) -> bool:
        """只有真机实测档案才能用于产品决策；合成件只能进测试。"""
        return self.provenance == PROVENANCE_MEASURED

    def to_dict(self) -> Dict[str, object]:
        return {
            "schema_version": "device_profile/1",
            "model": self.model,
            "provenance": self.provenance,
            "probed_at": self.probed_at,
            "usable_for_intent": self.usable_for_intent,
            "cameras": [c.to_dict() for c in self.cameras],
            "imu": {
                "rate_hz": self.imu_rate_hz,
                "timebase": self.imu_timebase,
                "align_to_camera": self.imu_align_to_camera,
            },
            "gnss": {"has_pps": self.gnss_has_pps},
        }

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "DeviceProfile":
        imu = d.get("imu") or {}
        gnss = d.get("gnss") or {}
        rate = imu.get("rate_hz") if isinstance(imu, dict) else None
        return cls(
            cameras=tuple(CameraCapability.from_dict(c) for c in (d.get("cameras") or ())),
            imu_rate_hz=float(rate) if rate is not None else None,
            imu_timebase=str(imu.get("timebase", UNKNOWN)) if isinstance(imu, dict) else UNKNOWN,
            imu_align_to_camera=str(imu.get("align_to_camera", UNKNOWN))
            if isinstance(imu, dict) else UNKNOWN,
            gnss_has_pps=str(gnss.get("has_pps", UNKNOWN)) if isinstance(gnss, dict) else UNKNOWN,
            model=str(d.get("model", "")),
            provenance=str(d.get("provenance", UNKNOWN)),
            probed_at=str(d.get("probed_at", "")),
        )


# ---------------------------------------------------------------------------
# 校验：三态不许含糊，声称必须带证据
# ---------------------------------------------------------------------------
def validate_profile(profile: DeviceProfile) -> List[str]:
    """返回违规清单。空列表 = 档案可用于后续裁决。"""
    out: List[str] = []
    if profile.provenance not in PROVENANCES:
        out.append(f"provenance 非法: {profile.provenance!r}")

    for cam in profile.cameras:
        tag = f"camera[{cam.id}]"
        if not cam.id:
            out.append("camera 缺 id")
        if cam.role_hint not in ROLES:
            out.append(f"{tag} role_hint 非法: {cam.role_hint!r}")
        for name, val in (("manual_exposure", cam.manual_exposure),
                          ("focus_lock", cam.focus_lock),
                          ("openable_directly", cam.openable_directly)):
            if val not in TRI_STATES:
                out.append(f"{tag} {name} 必须三态，收到 {val!r}")
        if cam.role_hint not in ("unknown",) and (cam.focal_mm is None or cam.f_number is None):
            out.append(f"{tag} 声称角色 {cam.role_hint} 但缺焦距或光圈——角色只能记 unknown")
        if cam.focal_mm is not None and cam.focal_mm <= 0:
            out.append(f"{tag} focal_mm 必须为正")
        if cam.f_number is not None and cam.f_number <= 0:
            out.append(f"{tag} f_number 必须为正")
        if cam.calibrated_arcsec_per_px is not None and cam.calibrated_arcsec_per_px <= 0:
            out.append(f"{tag} calibrated_arcsec_per_px 必须为正")
        for name, val in (("manual_exposure", cam.manual_exposure),
                          ("focus_lock", cam.focus_lock),
                          ("openable_directly", cam.openable_directly)):
            if val == CAN and not cam.evidence:
                out.append(f"{tag} 声称 {name}=can 但没有任何 evidence")

    for name, val in (("imu.align_to_camera", profile.imu_align_to_camera),
                      ("gnss.has_pps", profile.gnss_has_pps)):
        if val not in TRI_STATES:
            out.append(f"{name} 必须三态，收到 {val!r}")
    if profile.imu_align_to_camera == CAN and not profile.imu_rate_hz:
        out.append("imu 声称可与相机对齐，但缺采样率")
    return out


# ---------------------------------------------------------------------------
# 光学换算
# ---------------------------------------------------------------------------
def exit_pupil_mm(aperture_mm: float, magnification: float) -> float:
    """目镜出瞳直径。手机能拿到的光被它限死。"""
    if aperture_mm <= 0 or magnification <= 0:
        raise ValueError("口径与倍率必须为正")
    return aperture_mm / magnification


def effective_focal_mm(camera_focal_mm: float, magnification: float) -> float:
    """目镜后等效焦距（afocal 投影）。"""
    if camera_focal_mm <= 0 or magnification <= 0:
        raise ValueError("焦距与倍率必须为正")
    return camera_focal_mm * magnification


def captured_true_field_deg(true_field_deg: float, camera_fov_deg: float,
                            eyepiece_afov_deg: float) -> float:
    """手机实际截到的天区：目镜把 ``true_field`` 铺在 ``afov`` 里，手机只取其中一段。

    相机视场大于目镜视场时，整幅天区都在画面里（只是周围一圈黑）；
    小于时按比例截中心——这正是长焦在目镜后的处境。
    """
    if true_field_deg <= 0 or camera_fov_deg <= 0 or eyepiece_afov_deg <= 0:
        raise ValueError("视场必须为正")
    if camera_fov_deg >= eyepiece_afov_deg:
        return true_field_deg
    return true_field_deg * (camera_fov_deg / eyepiece_afov_deg)


def fov_from_star_pair(sep_deg: float, p1: Tuple[float, float], p2: Tuple[float, float],
                       axis_px: Optional[float] = None) -> Dict[str, Optional[float]]:
    """用两颗已知角距的星反算像素尺度、焦距（像素）与沿该轴的视场。

    这是现场标定的第一步：不信任 EXIF 标称焦距，只信任画面上的角距。
    """
    if sep_deg <= 0:
        raise ValueError("两星角距必须为正")
    dx = float(p2[0]) - float(p1[0])
    dy = float(p2[1]) - float(p1[1])
    dist_px = math.hypot(dx, dy)
    if dist_px <= 0:
        raise ValueError("两点重合，无法反算尺度")

    focal_px = dist_px / (2.0 * math.tan(math.radians(sep_deg) / 2.0))
    scale_deg_per_px = sep_deg / dist_px
    out: Dict[str, Optional[float]] = {
        "focal_px": focal_px,
        "deg_per_px": scale_deg_per_px,
        "arcsec_per_px": scale_deg_per_px * 3600.0,
        "fov_axis_deg": None,
    }
    if axis_px is not None:
        if axis_px <= 0:
            raise ValueError("轴长必须为正")
        out["fov_axis_deg"] = 2.0 * math.degrees(math.atan(float(axis_px) / (2.0 * focal_px)))
    return out


# ---------------------------------------------------------------------------
# 镜头意图裁决（模型只提意图，这里给结论）
# ---------------------------------------------------------------------------
def check_lens_intent(
    profile: DeviceProfile,
    want: str,
    *,
    aperture_mm: Optional[float] = None,
    magnification: Optional[float] = None,
    eyepiece_afov_deg: Optional[float] = None,
    true_field_deg: Optional[float] = None,
) -> Dict[str, object]:
    """把「切到潜望镜」变成可执行/可拒绝的结论。"""
    reasons: List[str] = []
    violations = validate_profile(profile)
    if violations:
        return {"verdict": VERDICT_REFUSE, "reasons": ["invalid_profile"] + violations,
                "camera": None, "numbers": {}}
    if not profile.usable_for_intent:
        return {"verdict": VERDICT_REFUSE,
                "reasons": [f"profile_not_measured: provenance={profile.provenance}"],
                "camera": None, "numbers": {}}
    if want not in ROLES or want == UNKNOWN:
        return {"verdict": VERDICT_REFUSE, "reasons": [f"unknown_role: {want!r}"],
                "camera": None, "numbers": {}}

    candidates = [c for c in profile.cameras if c.role_hint == want]
    if not candidates:
        have = sorted({c.role_hint for c in profile.cameras})
        return {"verdict": VERDICT_REFUSE, "reasons": [f"no_such_lens: want={want}"],
                "camera": None, "numbers": {"available_roles": have}}

    # 优先能直接开的；否则走逻辑相机里的物理流
    direct = [c for c in candidates if c.openable_directly == CAN]
    via_logical = [c for c in candidates if c.logical_id]
    cam = (direct or via_logical or candidates)[0]
    numbers: Dict[str, object] = {"via": "direct" if cam in direct else ("logical" if cam.logical_id else "unknown")}

    if cam.openable_directly == CANNOT and not cam.logical_id:
        return {"verdict": VERDICT_REFUSE,
                "reasons": [f"lens_not_exposed: {cam.id} 不能直接打开，也没有逻辑相机路径"],
                "camera": cam.to_dict(), "numbers": numbers}
    if cam.openable_directly == UNKNOWN and not cam.logical_id:
        reasons.append(f"openability_unknown: {cam.id} 需真机探测")

    if cam.manual_exposure != CAN:
        return {"verdict": VERDICT_REFUSE,
                "reasons": [f"no_manual_exposure: {cam.manual_exposure}——星点必须锁曝光"],
                "camera": cam.to_dict(), "numbers": numbers}
    if cam.focus_lock != CAN:
        reasons.append(f"focus_lock_{cam.focus_lock}: 无法锁无穷远")

    if aperture_mm and magnification:
        ep = exit_pupil_mm(aperture_mm, magnification)
        numbers["exit_pupil_mm"] = ep
        ent = cam.entrance_pupil_mm
        numbers["entrance_pupil_mm"] = ent
        if ent is not None and ent < ep * 0.9:
            reasons.append(f"pupil_underfilled: 入瞳 {ent:.2f}mm < 出瞳 {ep:.2f}mm，画面会暗角")

    if cam.fov_diag_deg is not None:
        numbers["camera_fov_deg"] = cam.fov_diag_deg
        if eyepiece_afov_deg and cam.fov_diag_deg < eyepiece_afov_deg:
            reasons.append(
                f"crops_image_circle: 镜头视场 {cam.fov_diag_deg:.1f}° < 目镜 {eyepiece_afov_deg:.0f}°，只截中心")
            if true_field_deg:
                numbers["captured_true_field_deg"] = captured_true_field_deg(
                    true_field_deg, cam.fov_diag_deg, eyepiece_afov_deg)

    if reasons:
        verdict = VERDICT_NEEDS_PROBE if any(r.startswith("openability_unknown") for r in reasons) \
            else VERDICT_WARN
    else:
        verdict = VERDICT_ALLOW
    return {"verdict": verdict, "reasons": reasons, "camera": cam.to_dict(), "numbers": numbers}


__all__ = [
    "CAN", "CANNOT", "UNKNOWN", "TRI_STATES", "ROLES",
    "PROVENANCE_MEASURED", "PROVENANCE_RAW", "PROVENANCE_SYNTHETIC", "PROVENANCES",
    "VERDICT_ALLOW", "VERDICT_WARN", "VERDICT_NEEDS_PROBE", "VERDICT_REFUSE",
    "CameraCapability", "DeviceProfile", "validate_profile",
    "exit_pupil_mm", "effective_focal_mm", "captured_true_field_deg",
    "fov_from_star_pair", "check_lens_intent",
]

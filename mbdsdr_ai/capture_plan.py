# SPDX-License-Identifier: MIT
"""采集导演：把「我想拍成什么样」变成可执行、可解释、可拒绝的镜头方案。

模型只提**意图**（要解算 / 要特写 / 要录像 / 要堆栈），不直接碰硬件。
本模块负责回答四个问题：

1. 用哪颗镜头（主摄 / 超广 / 长焦 / 潜望）——依据是能力档案，不是猜测；
2. 要不要数字变焦、变到多少——用目标张角与视场算，不拍脑袋；
3. 变焦能换来什么、换不来什么——**数字变焦不增加信息**，只改构图；
4. 换不了时怎么办——给替代方案与代价，绝不静默降级。

三条与仓库诚实红线一致的口径：

* 档案里没有的事实（像素数、未标定的角分辨率）不猜，标为 ``unknown``；
* 只有 ``measured_device`` 档案能驱动决策，合成档案只能进测试；
* 任何 ``allow`` 都必须能说清「为什么是这颗镜头」，否则降为 ``needs_probe``。

两种采集形态共用本模块：

============  ==========================================================
形态          需求
============  ==========================================================
解算/认天区   宽视场（星多）、短曝、锁焦；长焦会让星数不够
特写/讲解     高角分辨率；没有光学长焦时，数字变焦只能构图、不能变清晰
============  ==========================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .camera_capability import (
    CAN, CANNOT, UNKNOWN,
    VERDICT_ALLOW, VERDICT_NEEDS_PROBE, VERDICT_REFUSE, VERDICT_WARN,
    CameraCapability, DeviceProfile,
    captured_true_field_deg, exit_pupil_mm, validate_profile,
)

#: 采集目的
GOAL_SOLVE = "solve"        # 星点解算 / 认天区
GOAL_PREVIEW = "preview"    # 实时取景
GOAL_DETAIL = "detail"      # 目标特写
GOAL_PHOTO = "photo"        # 单张成片
GOAL_RECORD = "record"      # 录像
GOAL_STACK = "stack"        # 短曝多帧堆栈

GOALS = (GOAL_SOLVE, GOAL_PREVIEW, GOAL_DETAIL, GOAL_PHOTO, GOAL_RECORD, GOAL_STACK)

#: 解算所需的默认最小视场（度）。低于这个值，先验姿态模式也可能凑不够星。
DEFAULT_MIN_SOLVE_FOV_DEG = 5.0

#: 默认最大数字变焦倍数。再大只是像素插值，画面已经糊了。
DEFAULT_MAX_DIGITAL_ZOOM = 10.0

#: 特写默认希望目标占画幅对角线的比例
DEFAULT_FILL_FRACTION = 0.5

_OPTICAL_ROLES = ("telephoto", "periscope")
_WIDE_ROLES = ("wide", "ultrawide")

#: 这些理由说明「方案仍能执行，但代价必须说清」，不构成拒绝。
_SOFT_REASONS = (
    "want_role_not_present",
    "role_unsuitable_for",
    "exposure_exceeds_device_max",
    "exposure_longer_than_frame",
    "pupil_underfilled",
    "fov_unknown",
    "manual_exposure_unknown",
    "openability_unknown",
)

#: 这些理由说明「缺测」，方案不能判 allow，只能 needs_probe。
_PROBE_REASONS = ("manual_exposure_unknown", "openability_unknown", "fov_unknown")


@dataclass(frozen=True)
class TelescopeOptics:
    """望远镜侧的光学事实（用户手填/规格书，不是实测）。"""

    aperture_mm: float
    focal_mm: float
    magnification: float
    eyepiece_afov_deg: float
    true_field_deg: float

    @property
    def exit_pupil_mm(self) -> float:
        return exit_pupil_mm(self.aperture_mm, self.magnification)


@dataclass(frozen=True)
class CaptureGoal:
    """一次采集意图。``want_role`` 是模型/用户点名要的镜头，可为空。"""

    purpose: str
    want_role: Optional[str] = None
    min_fov_deg: Optional[float] = None
    target_deg: Optional[float] = None
    fill_fraction: float = DEFAULT_FILL_FRACTION
    exposure_s: Optional[float] = None
    frames: int = 8
    fps: float = 30.0
    max_digital_zoom: float = DEFAULT_MAX_DIGITAL_ZOOM
    #: 特写是否必须光学变焦。True 时，只有数字变焦会直接拒绝。
    require_optical: bool = True
    telescope: Optional[TelescopeOptics] = None


@dataclass(frozen=True)
class CapturePlan:
    purpose: str
    verdict: str
    camera: Optional[Dict[str, Any]]
    zoom: float
    fov_deg: Optional[float]
    target_fill: Optional[float]
    mode: str
    exposure_s: Optional[float]
    frames: int
    captured_true_field_deg: Optional[float]
    degradations: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()
    alternatives: Tuple[Dict[str, Any], ...] = ()
    notes: Tuple[str, ...] = ()

    @property
    def executable(self) -> bool:
        """方案本身是否可交给手机执行。拒绝态永远不可执行。"""
        return self.verdict != VERDICT_REFUSE and self.camera is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "purpose": self.purpose,
            "verdict": self.verdict,
            "executable": self.executable,
            "camera": self.camera,
            "zoom": round(float(self.zoom), 3),
            "fov_deg": (round(float(self.fov_deg), 3)
                        if self.fov_deg is not None else None),
            "target_fill": (round(float(self.target_fill), 3)
                            if self.target_fill is not None else None),
            "mode": self.mode,
            "exposure_s": self.exposure_s,
            "frames": self.frames,
            "captured_true_field_deg": (
                round(float(self.captured_true_field_deg), 4)
                if self.captured_true_field_deg is not None else None),
            "degradations": list(self.degradations),
            "reasons": list(self.reasons),
            "alternatives": list(self.alternatives),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# 几何
# ---------------------------------------------------------------------------
def fov_at_zoom(fov_deg: float, zoom: float) -> float:
    """数字变焦后的视场。变焦是裁切，不是新光学。"""
    if fov_deg <= 0 or zoom <= 0:
        raise ValueError("视场与倍率必须为正")
    return 2.0 * math.degrees(math.atan(math.tan(math.radians(fov_deg) / 2.0) / zoom))


def zoom_for_fill(fov_deg: float, target_deg: float, fill_fraction: float) -> float:
    """让目标占 ``fill_fraction`` 画幅所需的数字变焦倍数。"""
    if fov_deg <= 0 or target_deg <= 0:
        raise ValueError("视场与目标张角必须为正")
    if not (0.0 < fill_fraction <= 1.0):
        raise ValueError("fill_fraction 必须在 (0, 1] 内")
    want_fov = target_deg / fill_fraction
    if want_fov >= fov_deg:
        return 1.0
    return math.tan(math.radians(fov_deg) / 2.0) / math.tan(math.radians(want_fov) / 2.0)


def _open_state(cam: CameraCapability) -> str:
    if cam.openable_directly == CAN:
        return "direct"
    if cam.logical_id:
        return "logical"
    if cam.openable_directly == CANNOT:
        return "blocked"
    return UNKNOWN


def _fov(cam: CameraCapability) -> Optional[float]:
    return cam.fov_diag_deg


def _focal(cam: CameraCapability) -> Optional[float]:
    return cam.focal_mm


def _sort_key(purpose: str, cam: CameraCapability):
    """目的决定排序：解算/取景看视场，特写看焦距。缺值排最后。"""
    if purpose in (GOAL_SOLVE, GOAL_PREVIEW):
        v = _fov(cam)
        return (v is None, -(v or 0.0))
    v = _focal(cam)
    return (v is None, -(v or 0.0))


def _pick(cameras: Sequence[CameraCapability], purpose: str,
          want_role: Optional[str]) -> Optional[CameraCapability]:
    pool = list(cameras)
    if want_role:
        named = [c for c in pool if c.role_hint == want_role]
        if named:
            pool = named
    pool.sort(key=lambda c: _sort_key(purpose, c))
    return pool[0] if pool else None


def _degradations(cam: CameraCapability, zoom: float) -> List[str]:
    out: List[str] = []
    if zoom > 1.0 + 1e-9:
        out.append(
            f"digital_zoom_no_new_information: {zoom:.1f}× 只是裁切放大，"
            "不会增加角分辨率；要更清晰只能换光学长焦")
    if cam.focus_lock == UNKNOWN:
        out.append("focus_lock_unknown: 未确认能锁无穷远，星点可能拉风箱")
    if cam.focus_lock == CANNOT:
        out.append("focus_lock_cannot: 不能锁焦，星点会虚")
    return out


def plan_capture(profile: DeviceProfile, goal: CaptureGoal) -> CapturePlan:
    """从能力档案与意图给出镜头方案。无法做到就明说，不静默降级。"""
    if goal.purpose not in GOALS:
        raise ValueError(f"未知采集目的: {goal.purpose!r}，可选 {GOALS}")

    violations = validate_profile(profile)
    if violations:
        return CapturePlan(
            purpose=goal.purpose, verdict=VERDICT_REFUSE, camera=None, zoom=1.0,
            fov_deg=None, target_fill=None, mode="none", exposure_s=None,
            frames=0, captured_true_field_deg=None,
            reasons=("invalid_profile",) + tuple(violations))

    if not profile.usable_for_intent:
        return CapturePlan(
            purpose=goal.purpose, verdict=VERDICT_REFUSE, camera=None, zoom=1.0,
            fov_deg=None, target_fill=None, mode="none", exposure_s=None,
            frames=0, captured_true_field_deg=None,
            reasons=(f"profile_not_measured: provenance={profile.provenance}",))

    if not profile.cameras:
        return CapturePlan(
            purpose=goal.purpose, verdict=VERDICT_REFUSE, camera=None, zoom=1.0,
            fov_deg=None, target_fill=None, mode="none", exposure_s=None,
            frames=0, captured_true_field_deg=None,
            reasons=("no_camera: 能力档案里没有任何镜头",))

    reasons: List[str] = []
    notes: List[str] = []

    # 点名镜头但档案里没有 —— 不是错误，是必须解释的降级
    if goal.want_role:
        exists = any(c.role_hint == goal.want_role for c in profile.cameras)
        if not exists:
            have = sorted({c.role_hint for c in profile.cameras})
            reasons.append(
                f"want_role_not_present: 点名 {goal.want_role}，这台机只有 {have}；"
                "改用可用镜头，不假装切过去了")

    usable: List[CameraCapability] = []
    alternatives: List[Dict[str, Any]] = []
    for cam in profile.cameras:
        state = _open_state(cam)
        if state == "blocked":
            alternatives.append({"id": cam.id, "role": cam.role_hint,
                                 "why_not": "not_openable_and_no_logical_path"})
            continue
        if cam.manual_exposure == CANNOT:
            alternatives.append({"id": cam.id, "role": cam.role_hint,
                                 "why_not": "no_manual_exposure"})
            continue
        usable.append(cam)

    if not usable:
        return CapturePlan(
            purpose=goal.purpose, verdict=VERDICT_REFUSE, camera=None, zoom=1.0,
            fov_deg=None, target_fill=None, mode="none", exposure_s=None,
            frames=0, captured_true_field_deg=None,
            reasons=tuple(reasons) + ("no_usable_camera: 没有既能打开又能锁曝光的镜头",),
            alternatives=tuple(alternatives))

    cam = _pick(usable, goal.purpose, goal.want_role)
    assert cam is not None

    # 点名镜头与目的冲突：解算要宽，长焦只会把星裁掉
    if (goal.purpose in (GOAL_SOLVE, GOAL_PREVIEW)
            and cam.role_hint in _OPTICAL_ROLES
            and any(c.role_hint in _WIDE_ROLES for c in usable)):
        wide = _pick([c for c in usable if c.role_hint in _WIDE_ROLES],
                     goal.purpose, None)
        if wide is not None:
            notes.append(
                f"role_unsuitable_for_{goal.purpose}: {cam.role_hint} 视场太窄，"
                f"解算改用 {wide.role_hint}({wide.id})")
            cam = wide

    fov = _fov(cam)
    focal = _focal(cam)

    # 曝光：目的决定默认上限
    max_exp = cam.max_exposure_s
    exposure = goal.exposure_s
    if exposure is not None and max_exp is not None and exposure > max_exp:
        reasons.append(
            f"exposure_exceeds_device_max: 要 {exposure}s，镜头上限 {max_exp}s")
        exposure = max_exp
    if exposure is None:
        if goal.purpose in (GOAL_SOLVE, GOAL_STACK, GOAL_DETAIL, GOAL_PHOTO):
            exposure = min(1.0, max_exp) if max_exp else None
        elif goal.purpose == GOAL_RECORD:
            exposure = 1.0 / max(1.0, goal.fps)

    zoom = 1.0
    target_fill: Optional[float] = None
    mode = "photo"

    if goal.purpose in (GOAL_SOLVE, GOAL_PREVIEW, GOAL_STACK):
        need_fov = goal.min_fov_deg or DEFAULT_MIN_SOLVE_FOV_DEG
        if fov is None:
            reasons.append("fov_unknown: 档案缺焦距/传感器尺寸，无法确认视场够不够")
        elif fov < need_fov:
            reasons.append(
                f"no_wide_field: 最佳镜头视场 {fov:.1f}° < 需要 {need_fov:.1f}°，"
                "解算会因星数不足失败")
        if goal.purpose == GOAL_STACK:
            mode = "stack"
            if exposure is not None and max_exp is not None:
                exposure = min(exposure, max_exp)
        elif goal.purpose == GOAL_PREVIEW:
            mode = "preview"
        else:
            mode = "photo"

    if goal.purpose == GOAL_RECORD:
        mode = "video"
        frame_time = 1.0 / max(1.0, goal.fps)
        if exposure is None:
            exposure = frame_time
        if exposure > frame_time + 1e-9:
            reasons.append(
                f"exposure_longer_than_frame: {exposure:.3f}s > 1/{goal.fps:g}fps "
                f"({frame_time:.3f}s)，已按帧时长截短，录像会变暗但不会拖影")
            exposure = frame_time

    if goal.purpose == GOAL_DETAIL:
        mode = "photo"
        if goal.target_deg is not None:
            if fov is None:
                reasons.append(
                    "fov_unknown: 档案缺焦距/传感器尺寸，算不出需要多少倍率")
            elif fov <= goal.target_deg:
                zoom = 1.0
                target_fill = goal.target_deg / fov
            else:
                zoom = min(goal.max_digital_zoom,
                           zoom_for_fill(fov, goal.target_deg,
                                         goal.fill_fraction))
                target_fill = goal.target_deg / fov_at_zoom(fov, zoom)
            if cam.calibrated_arcsec_per_px is not None:
                px = goal.target_deg * 3600.0 / (
                    cam.calibrated_arcsec_per_px / max(zoom, 1e-9))
                notes.append(f"target_span_px: {px:.1f}px（按标定角分辨率算）")
            else:
                notes.append("arcsec_per_px_unknown: 没有实拍标定，不能报目标占多少像素")
        else:
            notes.append("target_deg_missing: 没给目标张角，只能按焦距排序选镜头")

        only_digital = (cam.role_hint not in _OPTICAL_ROLES and zoom > 1.0 + 1e-9)
        if only_digital and goal.require_optical:
            reasons.append(
                f"no_optical_telephoto: 这台机没有可用长焦/潜望镜，"
                f"{zoom:.1f}× 数字变焦最多把目标放到 {target_fill:.0%} 画幅，"
                "但不会增加细节；要特写必须换光学镜头")

    if goal.purpose == GOAL_PHOTO:
        mode = "photo"
        if goal.target_deg is not None and fov is not None and fov > goal.target_deg:
            zoom = min(goal.max_digital_zoom,
                       zoom_for_fill(fov, goal.target_deg, goal.fill_fraction))
            target_fill = goal.target_deg / fov_at_zoom(fov, zoom)

    # 望远镜光路：出瞳、实得天区（再叠加数字裁切）
    captured: Optional[float] = None
    if goal.telescope is not None:
        tel = goal.telescope
        ent = cam.entrance_pupil_mm
        if ent is not None and ent < tel.exit_pupil_mm * 0.9:
            reasons.append(
                f"pupil_underfilled: 手机入瞳 {ent:.2f}mm < 目镜出瞳 "
                f"{tel.exit_pupil_mm:.2f}mm，画面会暗角")
        if fov is not None:
            base = captured_true_field_deg(tel.true_field_deg, fov,
                                           tel.eyepiece_afov_deg)
            captured = base / zoom
            if zoom > 1.0 + 1e-9:
                notes.append(
                    f"captured_field_after_digital_zoom: {captured:.3f}°"
                    f"（未变焦 {base:.3f}°）")

    degradations = _degradations(cam, zoom)

    # 质量未知：不能 allow，只能 needs_probe
    if cam.manual_exposure == UNKNOWN:
        reasons.append("manual_exposure_unknown: 没测到手动曝光，需真机探测")
    if cam.openable_directly == UNKNOWN and not cam.logical_id:
        reasons.append("openability_unknown: 没测到能否直接打开，需真机探测")

    hard = [r for r in reasons if not r.startswith(_SOFT_REASONS)]
    if hard:
        verdict = VERDICT_REFUSE
    elif any(r.startswith(_PROBE_REASONS) for r in reasons):
        verdict = VERDICT_NEEDS_PROBE
    elif degradations or reasons:
        verdict = VERDICT_WARN
    else:
        verdict = VERDICT_ALLOW

    # 落选镜头也要交代，避免「为什么不用长焦」无从回答
    for other in usable:
        if other.id == cam.id:
            continue
        why = "not_selected"
        if goal.purpose in (GOAL_SOLVE, GOAL_PREVIEW) and other.role_hint in _OPTICAL_ROLES:
            why = "narrower_field_than_selected"
        elif goal.purpose in (GOAL_DETAIL, GOAL_PHOTO) and (other.focal_mm or 0) < (focal or 0):
            why = "shorter_focal_than_selected"
        alternatives.append({"id": other.id, "role": other.role_hint,
                             "why_not": why})

    return CapturePlan(
        purpose=goal.purpose, verdict=verdict, camera=cam.to_dict(), zoom=zoom,
        fov_deg=(fov_at_zoom(fov, zoom) if fov is not None else None),
        target_fill=target_fill, mode=mode, exposure_s=exposure,
        frames=(goal.frames if mode == "stack" else 1),
        captured_true_field_deg=captured,
        degradations=tuple(degradations), reasons=tuple(reasons),
        alternatives=tuple(alternatives), notes=tuple(notes))


__all__ = [
    "GOAL_SOLVE", "GOAL_PREVIEW", "GOAL_DETAIL", "GOAL_PHOTO", "GOAL_RECORD",
    "GOAL_STACK", "GOALS", "DEFAULT_MIN_SOLVE_FOV_DEG",
    "DEFAULT_MAX_DIGITAL_ZOOM", "DEFAULT_FILL_FRACTION",
    "TelescopeOptics", "CaptureGoal", "CapturePlan",
    "fov_at_zoom", "zoom_for_fill", "plan_capture",
]

# SPDX-License-Identifier: MIT
"""相机导演工具面：让模型能决定「用哪颗镜头、多大倍率、拍几张」。

为什么不直接调相机
------------------
相机在手机上，桌面没有取景通道。所以这个工具**只产出可执行方案**：
镜头 id、数字变焦、曝光、帧数、实得天区、以及做不到时的原因。手机端拿到
方案后执行；执行结果必须回读验证（物理镜头 id 或实测角分辨率）。

没有能力档案时它不会编一个：直接返回 ``no_device_profile`` 与获取方法。
档案路径约定 ``~/.mbdsdr/device_profile.json``，或环境变量
``MBDSDR_DEVICE_PROFILE`` 指向的 JSON。

诚实口径
--------
* 合成档案（``synthetic_fixture``）一律拒绝——测试数据不能当产品事实；
* 数字变焦只改构图、不增加角分辨率，方案里必须显式标出来；
* 点名要潜望镜但这台机没有时，给替代方案与代价，不假装切过去了。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .camera_capability import ROLES, DeviceProfile
from .capture_plan import (
    GOALS, CaptureGoal, TelescopeOptics, plan_capture,
)

DEFAULT_PROFILE_PATH = "~/.mbdsdr/device_profile.json"
_ENV_PROFILE = "MBDSDR_DEVICE_PROFILE"


def _load_profile(args: Dict[str, Any]) -> Optional[DeviceProfile]:
    """从参数或约定路径读能力档案。读不到返回 None，不造默认档案。"""
    raw = args.get("profile")
    if isinstance(raw, dict):
        return DeviceProfile.from_dict(raw)
    if isinstance(raw, str) and raw.strip():
        return DeviceProfile.from_dict(json.loads(raw))

    path = (args.get("profile_path")
            or os.environ.get(_ENV_PROFILE) or DEFAULT_PROFILE_PATH)
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    return DeviceProfile.from_dict(json.loads(p.read_text(encoding="utf-8")))


def plan_from_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """工具与测试共用的纯函数：参数字典 → 方案字典。"""
    purpose = str(args.get("purpose") or "").strip()
    if purpose not in GOALS:
        return {"ok": False,
                "reason": f"unknown_purpose: {purpose!r}",
                "hint": f"可选 {GOALS}"}

    profile = _load_profile(args)
    if profile is None:
        return {
            "ok": False,
            "reason": "no_device_profile",
            "hint": ("先插入手机并跑 tools/probe_device.py collect，"
                     "把解析后的能力档案写到 ~/.mbdsdr/device_profile.json；"
                     "没有档案就不能决定用哪颗镜头"),
        }

    want_role = args.get("want_role")
    if want_role is not None and want_role not in ROLES:
        return {"ok": False,
                "reason": f"unknown_role: {want_role!r}", "hint": f"可选 {ROLES}"}

    optics = None
    tel = args.get("telescope") or {}
    if tel:
        try:
            optics = TelescopeOptics(
                aperture_mm=float(tel["aperture_mm"]),
                focal_mm=float(tel["focal_mm"]),
                magnification=float(tel["magnification"]),
                eyepiece_afov_deg=float(tel["eyepiece_afov_deg"]),
                true_field_deg=float(tel["true_field_deg"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False,
                    "reason": f"bad_telescope_optics: {exc}",
                    "hint": ("望远镜参数需要 aperture_mm / focal_mm / "
                             "magnification / eyepiece_afov_deg / true_field_deg")}

    goal = CaptureGoal(
        purpose=purpose,
        want_role=want_role,
        min_fov_deg=(float(args["min_fov_deg"])
                     if args.get("min_fov_deg") is not None else None),
        target_deg=(float(args["target_deg"])
                    if args.get("target_deg") is not None else None),
        fill_fraction=float(args.get("fill_fraction") or 0.5),
        exposure_s=(float(args["exposure_s"])
                    if args.get("exposure_s") is not None else None),
        frames=int(args.get("frames") or 8),
        fps=float(args.get("fps") or 30.0),
        max_digital_zoom=float(args.get("max_digital_zoom") or 10.0),
        require_optical=bool(args.get("require_optical", True)),
        telescope=optics,
    )
    plan = plan_capture(profile, goal)
    out = {"ok": True, **plan.to_dict()}
    if out.get("camera"):
        out["camera"]["role_hint"] = out["camera"].get("role_hint")
    out["execution"] = ("plan_only: 相机在手机端；桌面没有取景通道，"
                       "执行结果必须回读物理镜头 id 或实测角分辨率")
    return out


def register_camera_tools(agent) -> None:
    """把相机导演注册到 agent（只读规划，不碰硬件）。"""
    reg = agent.tool_registry

    reg.register(
        name="camera_capture_plan",
        description=(
            "相机导演：按采集目的（解算/取景/特写/单张/录像/堆栈）与设备能力档案，"
            "决定用哪颗镜头、多少倍数字变焦、曝光与帧数，并给出实得天区。"
            "没有长焦/潜望镜时说明数字变焦不增加细节；点名镜头不存在时给替代"
            "方案与代价。只做规划，不直接拍照（相机在手机端），无法执行时明说原因。"),
        parameters={"type": "object", "properties": {
            "purpose": {"type": "string", "enum": list(GOALS),
                        "description": "采集目的"},
            "want_role": {"type": "string", "enum": list(ROLES),
                          "description": "点名要的镜头（可空，系统按目的自选）"},
            "target_deg": {"type": "number",
                           "description": "目标张角（度）；特写时用于算倍率"},
            "fill_fraction": {"type": "number",
                              "description": "特写希望目标占画幅对角线的比例，默认 0.5"},
            "min_fov_deg": {"type": "number",
                            "description": "解算所需最小视场（度），默认 5"},
            "exposure_s": {"type": "number", "description": "期望曝光（秒）"},
            "fps": {"type": "number", "description": "录像帧率，默认 30"},
            "frames": {"type": "integer", "description": "堆栈帧数，默认 8"},
            "max_digital_zoom": {"type": "number",
                                 "description": "允许的最大数字变焦，默认 10"},
            "require_optical": {"type": "boolean",
                                "description": "特写是否必须光学变焦，默认 true"},
            "telescope": {"type": "object", "description": "望远镜光学参数（可空）",
                          "properties": {
                              "aperture_mm": {"type": "number"},
                              "focal_mm": {"type": "number"},
                              "magnification": {"type": "number"},
                              "eyepiece_afov_deg": {"type": "number"},
                              "true_field_deg": {"type": "number"},
                          }},
            "profile": {"type": "object",
                        "description": "能力档案（可空；空则读约定路径）"},
            "profile_path": {"type": "string",
                             "description": "能力档案 JSON 路径（可空）"},
        }, "required": ["purpose"]},
        handler=plan_from_args,
        category="camera",
        parallel_safe=True,
    )


__all__ = ["DEFAULT_PROFILE_PATH", "plan_from_args", "register_camera_tools"]

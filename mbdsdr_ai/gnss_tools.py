# SPDX-License-Identifier: MIT
"""GNSS 粗指向工具面：把「每颗卫星的仰角 + SNR」和「手机 IMU 姿态」对上。

为什么要有这个模块
------------------
``gnss_sky_reference.fit_boresight`` 已经把数学做完了，但模型够不着它：
serials 的 GSV 帧只到 UI 层，模型手里既没有卫星仰角，也没有一步能算出
「镜筒现在大概指哪」。这个工具就是把那一步接出来。

口径（别在这里重新发明物理）
----------------------------
* GSV 里的方位/仰角是**接收机算出来的**（卫星在哪），不是测量；
* 只有 SNR 是测量值，它随天线方向图变，才携带指向信息；
* 手机 IMU 提供姿态基准；两者一起才能给出粗指向；
* 俯仰/方位对不上时不自动裁决，只报冲突要求复核。

因此工具永远返回 ``ok`` 与 ``fit``（含 ``discriminative`` 与 ``reason``），
拒绝时给两个基线数字与下一步该查什么，不给一个假角度。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .gnss_sky_reference import (
    GnssSatellite, compare_with_imu, fit_boresight,
    satellites_from_gsv_frames,
)

#: 每次回答都带上的边界声明。模型转述时不该把粗指向说成精指向。
SCOPE = ("GNSS 的方位/仰角是接收机用星历算出来的，不说明镜筒指哪；"
         "只有 SNR 随天线方向图变化。本工具给的是几度到几十度的粗指向，"
         "且要求天线（手机）随镜筒一起转，不能替代视觉解算。")

_NO_INPUT_HINT = ("未给 frames 时会读本机最近一帧 GSV（10 秒内）；"
                  "需要 NMEA GSV 帧（serial_gnss 的 get_gsv_frames() 输出，"
                  "含每颗卫星 elevation/azimuth/snr_db），或直接给 "
                  "satellites[{azimuth_deg, elevation_deg, snr_db_hz}]。"
                  "没有真实观测就不给角度。")


def _live_gsv_frames():
    """本机最近一帧 GSV（桌面轮询发布的运行时快照）；过期/没有则 None。"""
    try:
        from .runtime_sources import get_runtime_sources
        block = (get_runtime_sources().snapshot() or {}).get("gnss_sats") or {}
        frames = block.get("frames")
        return frames if frames else None
    except Exception:
        return None


def _satellites_from_args(args: Dict[str, Any]) -> Tuple[List[GnssSatellite], str]:
    """从 ``frames`` 或 ``satellites`` 取卫星。坏条目跳过，不补零、不猜。"""
    frames = args.get("frames")
    if isinstance(frames, dict) and frames:
        return satellites_from_gsv_frames(frames), "gsv_frames"
    if isinstance(frames, list) and frames:
        return satellites_from_gsv_frames(frames), "gsv_frames"

    raw = args.get("satellites")
    if isinstance(raw, list) and raw:
        out: List[GnssSatellite] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                out.append(GnssSatellite.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return out, "satellites"
    return [], ""


def sky_fit_from_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """工具与测试共用的纯函数：参数字典 → 粗指向结果。"""
    sats, source = _satellites_from_args(args)
    if not sats:
        live = _live_gsv_frames()
        if live:
            sats, source = satellites_from_gsv_frames(live), "live_gsv"
    if not sats:
        return {"ok": False, "reason": "no_satellites",
                "hint": _NO_INPUT_HINT, "scope": SCOPE}

    fit = fit_boresight(sats)
    out: Dict[str, Any] = {
        "ok": fit.ok,
        "fit": fit.to_dict(),
        "input_source": source,
        "satellite_count": len(sats),
        "satellites_with_snr": sum(1 for s in sats if s.snr_db_hz is not None),
        "scope": SCOPE,
    }

    imu_az = args.get("imu_azimuth_deg")
    imu_el = args.get("imu_elevation_deg")
    if imu_az is not None or imu_el is not None:
        tol = args.get("tolerance_deg")
        out["imu_check"] = compare_with_imu(
            fit,
            None if imu_az is None else float(imu_az),
            None if imu_el is None else float(imu_el),
            tolerance_deg=None if tol is None else float(tol),
        )

    if not fit.ok:
        out["hint"] = ("这套观测没有分辨力：可能是普通全向贴片（SNR 不随指向变）、"
                       "卫星太少，或方位依赖被仰角趋势吃掉。"
                       "需要换定向天线，或改用 IMU + 视觉，不要在这里硬凑角度。")
    return out


def register_gnss_tools(agent) -> None:
    """把 GNSS 粗指向注册到 agent（只读计算，不碰串口）。"""
    reg = agent.tool_registry

    reg.register(
        name="gnss_sky_fit",
        description=(
            "GNSS 卫星 SNR 粗指向：输入 NMEA GSV 帧（每颗卫星的仰角/方位/信噪比）"
            "与可选的手机 IMU 方位/俯仰，拟合出镜筒（天线）大概指向，并检查与 IMU"
            "是否一致。省略 frames/satellites 时会读本机最近一帧 GSV（10 秒内），"
            "过期或无数据则明确拒绝，不编角度。卫星方位/仰角本身不携带指向信息，"
            "只有 SNR 携带；数据没有分辨力时明确拒绝并给出两个基线数字。"),
        parameters={"type": "object", "properties": {
            "frames": {
                "type": "array",
                "description": ("GSV 帧列表：[{talker, sats:[{id, elevation, "
                                "azimuth, snr_db}]}]，即 serial_gnss 的 "
                                "get_gsv_frames() 输出"),
                "items": {"type": "object"},
            },
            "satellites": {
                "type": "array",
                "description": ("也可以直接给卫星：[{azimuth_deg, elevation_deg, "
                                "snr_db_hz, constellation, prn}]"),
                "items": {"type": "object"},
            },
            "imu_azimuth_deg": {"type": "number",
                                "description": "手机 IMU 方位角（度，北=0 东=90）"},
            "imu_elevation_deg": {"type": "number",
                                  "description": "手机 IMU 俯仰角（度，向上为正）"},
            "tolerance_deg": {"type": "number",
                              "description": "一致性判定的容差（度），默认按拟合不确定度"},
        }},
        handler=sky_fit_from_args,
        category="sky",
        parallel_safe=True,
    )


__all__ = ["SCOPE", "sky_fit_from_args", "register_gnss_tools"]

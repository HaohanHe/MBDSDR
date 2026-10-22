# SPDX-License-Identifier: MIT
"""轨道动力学原子工具适配层（每块积木一个独立小工具，绝不打包）
================================================================

设计红线（AGENTS.md「原子能力、模型编排」+ 用户明确要求）：**不提供
「定轨工具」这种大黑盒**。每个工具只包一块最小原子积木，模型现场一个个
调用、自由编排成定轨/测速/预报等任意流程。工具是积木，拼法是模型的能力。

与 orbit_dynamics.py（纯物理层、可测）分工：本文件是薄适配层，把纯函数
暴露为 MCP 工具。积木清单见 orbit_dynamics.py 模块 docstring。

工具命名用「动词_对象」，刻意通用化（doppler/propagate/rls/coord...），
不出现「orbit_determine」这类把流程焊死的复合工具。
"""
from __future__ import annotations

import numpy as np

from .tool_registry import ToolResult, clean_exc
from . import orbit_dynamics as od


def _arr(x) -> list:
    """numpy -> 纯 Python 嵌套 list（JSON 可序列化）。"""
    return np.asarray(x, dtype=float).tolist()


def _err(e: Exception) -> ToolResult:
    return ToolResult(
        success=False,
        content=f"参数/计算错误: {clean_exc(e)}",
        data={"error": str(e)},
    )


# ─────────────────────────────────────────────────────────────
# 多普勒换算积木（多普勒工具，单用途）
# ─────────────────────────────────────────────────────────────
def _doppler_from_frequency(args):
    try:
        f = float(args["frequency_hz"])
        fc = float(args["center_frequency_hz"])
        rr = od.observed_pseudorange_rate(f, fc)
        dop = od.range_rate_to_doppler_hz(rr, fc)
        return ToolResult(success=True, content=(
            f"观测频率 {f:.3f} Hz -> 伪距率 {rr:.3f} m/s，多普勒频移 {dop:.3f} Hz"
        ), data={"range_rate_mps": rr, "doppler_hz": dop})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _doppler_to_frequency(args):
    try:
        rr = float(args["range_rate_mps"])
        fc = float(args["center_frequency_hz"])
        dop = od.range_rate_to_doppler_hz(rr, fc)
        f = fc + dop
        return ToolResult(success=True, content=(
            f"伪距率 {rr:.3f} m/s -> 接收频率 {f:.3f} Hz（多普勒 {dop:.3f} Hz）"
        ), data={"frequency_hz": f, "doppler_hz": dop})
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# TLE 取状态积木
# ─────────────────────────────────────────────────────────────
def _tle_state(args):
    try:
        st = od.tle_state_ecef(args["tle_line1"], args["tle_line2"], float(args["timestamp"]))
        return ToolResult(success=True, content=(
            f"ECEF 状态: r=({st[0]:.1f},{st[1]:.1f},{st[2]:.1f}) m, "
            f"v=({st[3]:.3f},{st[4]:.3f},{st[5]:.3f}) m/s"
        ), data={"state_ecef": _arr(st)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# 动力学传播积木（状态 + STM）
# ─────────────────────────────────────────────────────────────
def _propagate_state_stm(args):
    try:
        st = np.asarray(args["state_ecef"], dtype=float)
        t_from = float(args["t_from"])
        t_to = float(args["t_to"])
        out, stm = od.propagate_state_and_stm(st, t_from, t_to)
        return ToolResult(success=True, content=(
            f"传播 {t_to - t_from:.3f}s -> 末态 r=({out[0]:.1f},{out[1]:.1f},{out[2]:.1f}) m"
        ), data={"state_ecef": _arr(out), "stm": _arr(stm)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# 观测模型积木（伪距率 + 雅可比）
# ─────────────────────────────────────────────────────────────
def _pseudorange_rate_model(args):
    try:
        st = np.asarray(args["sat_state_ecef"], dtype=float)
        rec = np.asarray(args["receiver_ecef"], dtype=float)
        rate, jac = od.pseudorange_rate_and_jacobian(st, rec)
        return ToolResult(success=True, content=(
            f"预测伪距率 {rate:.3f} m/s（雅可比 7 维已返回）"
        ), data={"range_rate_mps": rate, "jacobian": _arr(jac)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# 估计积木（单步 RLS / 校时）
# ─────────────────────────────────────────────────────────────
def _rls_update(args):
    try:
        ref_state = np.asarray(args["ref_state"], dtype=float)
        ref_cov = np.asarray(args["ref_cov"], dtype=float)
        rec = np.asarray(args["receiver_ecef"], dtype=float)
        out = od.rls_update(
            ref_state, ref_cov,
            float(args["ref_timestamp"]), float(args["obs_timestamp"]),
            float(args["observation"]), rec, float(args["observation_sigma"]),
        )
        return ToolResult(success=True, content=(
            f"RLS 单步: 新息 {out['innovation']:.3f}, "
            f"后验残差 {out['postfit_residual']:.3f}, "
            f"归一新息 {out['normalized_innovation']:.3f}"
        ), data={
            "ref_state": _arr(out["ref_state"]),
            "ref_cov": _arr(out["ref_cov"]),
            "state": _arr(out["state"]),
            "innovation": out["innovation"],
            "normalized_innovation": out["normalized_innovation"],
            "postfit_residual": out["postfit_residual"],
        })
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _calibrate_time_offset(args):
    try:
        rows = [(float(t), float(f)) for t, f in args["observations"]]
        rec = np.asarray(args["receiver_ecef"], dtype=float)
        cal = od.calibrate_time_offset(
            rows, rec, float(args["center_frequency_hz"]),
            args["tle_line1"], args["tle_line2"],
            float(args.get("search_limit_s", 30.0)),
        )
        return ToolResult(success=True, content=(
            f"校时: 时间偏差 {cal['time_offset_s']:.2f}s, "
            f"伪距率偏差 {cal['rate_bias_mps']:.3f} m/s, "
            f"去偏 RMS {cal['centered_rms_mps']:.3f} m/s"
        ), data=cal)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# 坐标转换积木（6 个单用途）
# ─────────────────────────────────────────────────────────────
def _coord_geodetic_to_ecef(args):
    try:
        p = od.geodetic_to_ecef(float(args["lat_deg"]), float(args["lon_deg"]), float(args["alt_m"]))
        return ToolResult(success=True, content=f"ECEF: {_arr(p)}", data={"ecef": _arr(p)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _coord_ecef_to_geodetic(args):
    try:
        lon, lat, alt = od.ecef_to_geodetic(np.asarray(args["ecef"], dtype=float))
        return ToolResult(success=True, content=(
            f"经纬高: ({lon:.6f}°, {lat:.6f}°, {alt:.3f} m)"
        ), data={"lon_deg": lon, "lat_deg": lat, "alt_m": alt})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _coord_ecef_to_enu(args):
    try:
        enu = od.ecef_to_enu(np.asarray(args["target_ecef"], dtype=float),
                             np.asarray(args["receiver_ecef"], dtype=float))
        return ToolResult(success=True, content=f"ENU: {_arr(enu)}", data={"enu": _arr(enu)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _coord_enu_to_az_el(args):
    try:
        az, el = od.enu_to_az_el(np.asarray(args["enu"], dtype=float))
        return ToolResult(success=True, content=(
            f"方位 {az:.3f}°, 仰角 {el:.3f}°"
        ), data={"azimuth_deg": az, "elevation_deg": el})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _coord_ecef_to_eci(args):
    try:
        p, v = od.ecef_to_eci(np.asarray(args["pos_ecef"], dtype=float),
                              np.asarray(args["vel_ecef"], dtype=float),
                              float(args["timestamp"]))
        return ToolResult(success=True, content=(
            f"ECI: r={_arr(p)}, v={_arr(v)}"
        ), data={"pos_eci": _arr(p), "vel_eci": _arr(v)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _coord_eci_to_ecef(args):
    try:
        p, v = od.eci_to_ecef(np.asarray(args["pos_eci"], dtype=float),
                              np.asarray(args["vel_eci"], dtype=float),
                              float(args["timestamp"]))
        return ToolResult(success=True, content=(
            f"ECEF: r={_arr(p)}, v={_arr(v)}"
        ), data={"pos_ecef": _arr(p), "vel_ecef": _arr(v)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# 注册：每块积木一个独立小工具
# ─────────────────────────────────────────────────────────────
def register_orbit_brick_tools(tool_registry):
    """把轨道动力学原子积木逐块注册为独立小工具（模型编排，不打包）。

    与 agent.py 的 _register_orbit_tools（过境预测等复合工具）互补：本函数
    只注册最小原子积木（多普勒换算/取状态/STM/观测模型/RLS/校时/坐标），
    供模型现场编排，刻意不提供「定轨工具」大黑盒。
    """
    R = tool_registry.register

    R(
        name="doppler_from_frequency",
        description="多普勒换算：观测接收频率 -> 伪距率(m/s) + 多普勒频移(Hz)。原子积木，供模型组合多普勒/定轨流程。",
        parameters={"type": "object", "properties": {
            "frequency_hz": {"type": "number", "description": "观测到的接收频率 Hz"},
            "center_frequency_hz": {"type": "number", "description": "标称载频 Hz"},
        }, "required": ["frequency_hz", "center_frequency_hz"]},
        handler=_doppler_from_frequency, category="doppler",
    )
    R(
        name="doppler_to_frequency",
        description="多普勒换算：伪距率(m/s) -> 接收频率 + 多普勒频移(Hz)。doppler_from_frequency 的逆。",
        parameters={"type": "object", "properties": {
            "range_rate_mps": {"type": "number", "description": "伪距率 m/s（远离为正）"},
            "center_frequency_hz": {"type": "number", "description": "标称载频 Hz"},
        }, "required": ["range_rate_mps", "center_frequency_hz"]},
        handler=_doppler_to_frequency, category="doppler",
    )
    R(
        name="tle_state_ecef",
        description="TLE 取状态：TLE 两行 + unix 时间戳 -> ECEF 位置速度(SGP4)。原子积木。",
        parameters={"type": "object", "properties": {
            "tle_line1": {"type": "string", "description": "TLE 第 1 行"},
            "tle_line2": {"type": "string", "description": "TLE 第 2 行"},
            "timestamp": {"type": "number", "description": "unix 时间戳 UTC 秒"},
        }, "required": ["tle_line1", "tle_line2", "timestamp"]},
        handler=_tle_state, category="orbit",
    )
    R(
        name="propagate_state_stm",
        description="动力学传播：ECEF 7 维状态 [x,y,z,vx,vy,vz,clock_drift] 从 t_from 到 t_to，返回末状态 + 6x6 状态转移矩阵(STM)。二体 + 变分方程。原子积木。",
        parameters={"type": "object", "properties": {
            "state_ecef": {"type": "array", "items": {"type": "number"}, "description": "7 维 ECEF 状态 [x,y,z,vx,vy,vz,clock_drift]，SI"},
            "t_from": {"type": "number", "description": "起始 unix 时间戳 s"},
            "t_to": {"type": "number", "description": "终止 unix 时间戳 s"},
        }, "required": ["state_ecef", "t_from", "t_to"]},
        handler=_propagate_state_stm, category="orbit",
    )
    R(
        name="pseudorange_rate_model",
        description="观测模型：卫星 ECEF 状态 + 接收机 ECEF 位置 -> 预测伪距率(m/s) + 对 7 维状态的雅可比。原子积木。",
        parameters={"type": "object", "properties": {
            "sat_state_ecef": {"type": "array", "items": {"type": "number"}, "description": "卫星 7 维 ECEF 状态"},
            "receiver_ecef": {"type": "array", "items": {"type": "number"}, "description": "接收机 3 维 ECEF 位置"},
        }, "required": ["sat_state_ecef", "receiver_ecef"]},
        handler=_pseudorange_rate_model, category="orbit",
    )
    R(
        name="rls_update",
        description="递推最小二乘：参考历元 RLS 单步更新。给先验状态/协方差 + 单个伪距率观测，返回更新后的参考历元状态/协方差 + 观测历元状态。协方差单调下降。原子积木，模型逐观测调用编排成完整定轨。",
        parameters={"type": "object", "properties": {
            "ref_state": {"type": "array", "items": {"type": "number"}, "description": "参考历元 7 维先验状态"},
            "ref_cov": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}, "description": "参考历元 7x7 先验协方差"},
            "ref_timestamp": {"type": "number", "description": "参考历元 unix 时间戳 s"},
            "obs_timestamp": {"type": "number", "description": "观测历元 unix 时间戳 s"},
            "observation": {"type": "number", "description": "伪距率观测值 m/s"},
            "receiver_ecef": {"type": "array", "items": {"type": "number"}, "description": "接收机 3 维 ECEF"},
            "observation_sigma": {"type": "number", "description": "观测噪声标准差 m/s"},
        }, "required": ["ref_state", "ref_cov", "ref_timestamp", "obs_timestamp",
                        "observation", "receiver_ecef", "observation_sigma"]},
        handler=_rls_update, category="orbit",
    )
    R(
        name="calibrate_time_offset",
        description="校时：相对 TLE 搜索整段观测的固定时间偏差 + 伪距率偏差。多普勒定轨前调用，消除时间错位被误解释为沿轨误差。原子积木。",
        parameters={"type": "object", "properties": {
            "observations": {"type": "array", "description": "[[timestamp, frequency_hz], ...] 观测序列",
                             "items": {"type": "array", "items": {"type": "number"}}},
            "receiver_ecef": {"type": "array", "items": {"type": "number"}, "description": "接收机 3 维 ECEF"},
            "center_frequency_hz": {"type": "number", "description": "标称载频 Hz"},
            "tle_line1": {"type": "string", "description": "TLE 第 1 行"},
            "tle_line2": {"type": "string", "description": "TLE 第 2 行"},
            "search_limit_s": {"type": "number", "description": "搜索范围 ±秒，默认 30"},
        }, "required": ["observations", "receiver_ecef", "center_frequency_hz",
                        "tle_line1", "tle_line2"]},
        handler=_calibrate_time_offset, category="orbit",
    )

    # 坐标转换 6 个小工具
    R(
        name="coord_geodetic_to_ecef",
        description="坐标转换：大地坐标(纬°,经°,高m) -> ECEF(m)。WGS-72。",
        parameters={"type": "object", "properties": {
            "lat_deg": {"type": "number"}, "lon_deg": {"type": "number"}, "alt_m": {"type": "number"},
        }, "required": ["lat_deg", "lon_deg", "alt_m"]},
        handler=_coord_geodetic_to_ecef, category="coord",
    )
    R(
        name="coord_ecef_to_geodetic",
        description="坐标转换：ECEF(m) -> 大地坐标(经°,纬°,高m)。",
        parameters={"type": "object", "properties": {
            "ecef": {"type": "array", "items": {"type": "number"}, "description": "3 维 ECEF(m)"},
        }, "required": ["ecef"]},
        handler=_coord_ecef_to_geodetic, category="coord",
    )
    R(
        name="coord_ecef_to_enu",
        description="坐标转换：目标 ECEF + 接收机 ECEF -> 站心 ENU(东/北/天,m)。",
        parameters={"type": "object", "properties": {
            "target_ecef": {"type": "array", "items": {"type": "number"}},
            "receiver_ecef": {"type": "array", "items": {"type": "number"}},
        }, "required": ["target_ecef", "receiver_ecef"]},
        handler=_coord_ecef_to_enu, category="coord",
    )
    R(
        name="coord_enu_to_az_el",
        description="坐标转换：站心 ENU -> 方位角(°,自北顺时针) + 仰角(°)。",
        parameters={"type": "object", "properties": {
            "enu": {"type": "array", "items": {"type": "number"}, "description": "3 维 ENU"},
        }, "required": ["enu"]},
        handler=_coord_enu_to_az_el, category="coord",
    )
    R(
        name="coord_ecef_to_eci",
        description="坐标转换：ECEF 位置速度 -> ECI（含地球自转牵连速度）。",
        parameters={"type": "object", "properties": {
            "pos_ecef": {"type": "array", "items": {"type": "number"}},
            "vel_ecef": {"type": "array", "items": {"type": "number"}},
            "timestamp": {"type": "number"},
        }, "required": ["pos_ecef", "vel_ecef", "timestamp"]},
        handler=_coord_ecef_to_eci, category="coord",
    )
    R(
        name="coord_eci_to_ecef",
        description="坐标转换：ECI 位置速度 -> ECEF。",
        parameters={"type": "object", "properties": {
            "pos_eci": {"type": "array", "items": {"type": "number"}},
            "vel_eci": {"type": "array", "items": {"type": "number"}},
            "timestamp": {"type": "number"},
        }, "required": ["pos_eci", "vel_eci", "timestamp"]},
        handler=_coord_eci_to_ecef, category="coord",
    )

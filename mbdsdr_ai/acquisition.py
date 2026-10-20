# SPDX-License-Identifier: MIT
"""接收前端与指向规划原子工具（每块积木一个独立小工具，绝不打包）
================================================================

设计红线（AGENTS.md「原子能力、模型编排」）：**不提供「接收方案」这种大黑盒**。
每个工具只包一块最小原子积木（星表查询 / 同步轨道指向 / 天线增益 / 指向容差 /
前端链路预算 / 故障诊断 / 罐子馈源几何 / 遥测接收链参数），模型现场逐块调用，
自由编排成"接收一颗气象星""抓一次 ASTU-1 遥测""搭一个 L 波段前端"等任意流程。
工具是积木，拼法是模型的能力。

与 orbit_tools.py（轨道动力学积木）、sync_tools.py（同步/调制积木）分工：
本文件覆盖**射频前端与指向规划**这一层（天线→馈源→LNA→馈线→SDR 的预算与容差）。

参数来源（公开培训材料中的可复现数值，公式按公开教科书口径独立实现）：
- 天线/馈源几何：1.8 m 抛物面 + 罐子（Cantenna）馈源，TE11 模截止频率
  λg = λ0 / sqrt(1 - (fc/f)^2)，fc = 1.8412 c / (π D)；
  探针入罐 ≈ λ/4（自由空间），探针离罐底 ≈ 0.25 λg，罐长 ≈ 0.75 λg；
  焦点 F/D ≈ 0.3–0.4，离焦 > 3 cm 降 3–6 dB。
- 指向：L 波段 1.69 GHz 1.8 m 口径增益 25–30 dBi、波束 5–8°，
  指向偏差 > 2° 信号可能跌至解码门限以下。
- 链路：LNA 增益 ≥ 20 dB / NF < 1 dB、紧贴馈源（< 30 cm）、
  馈线 < 5 m 且 1.69 GHz 每米约 0.5–1 dB、SNR < 5 dB 视为不可解。

诚实红线：本模块只做**预算与容差计算**，不假装测过；所有门限都标注来源与
适用范围（不适用于 LNA 以下/馈线以上等未建模环节）。同频段不同口径的材料
参数不混用，冲突处在 profile 备注里如实标注。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .tool_registry import ToolResult

# 物理常数
C_LIGHT = 299_792_458.0
# 圆波导 TE11 模第一根零位（贝塞尔函数 J1 的第一个非零根）
TE11_ROOT = 1.8412
# 地球平均半径（m）
EARTH_RADIUS_M = 6_371_000.0
# 同步轨道半径近似（m）：高度 35786 km + 地球半径
GEO_RADIUS_M = EARTH_RADIUS_M + 35_786_000.0

# 公开材料给出的判据门限（作为默认参数，调用方可覆盖）
DEFAULT_REQUIRED_SNR_DB = 5.0          # "SNR < 5 dB 视为极低不可解"
DEFAULT_POINTING_TOLERANCE_DEG = 2.0   # "指向偏差 > 2° 可能跌至解码门限以下"
DEFAULT_LNA_GAIN_DB = 20.0              # LNA 增益 ≥ 20 dB
DEFAULT_LNA_NF_DB = 1.0                 # LNA NF < 1 dB 为佳
DEFAULT_FEEDLINE_MAX_M = 5.0           # 馈线 < 5 m
DEFAULT_FEEDLINE_LOSS_DB_PER_M = 0.5   # 1.69 GHz 每米约 0.5–1 dB
DEFAULT_LNA_SPACING_MAX_M = 0.30       # LNA 紧贴馈源（< 30 cm）
DEFAULT_FD_RATIO = 0.35                 # 焦比 F/D ≈ 0.3–0.4
DEFAULT_FOCUS_TOLERANCE_M = 0.03        # 离焦 > 3 cm 降 3–6 dB


@dataclass(frozen=True)
class RxProfile:
    """一颗卫星的接收剖面（材料可核实的确定参数）。"""

    name: str
    downlink_mhz: Optional[float]
    uplink_mhz: Optional[float] = None
    band: str = ""
    protocol: str = ""
    orbit: str = ""            # LEO / GEO / 深空
    pointing: str = ""         # 固定指向 / 实时跟踪 / 需星历
    geo_longitude_deg: Optional[float] = None   # 同步轨道定点经度
    altitude_km: Optional[float] = None
    tune_margin_hz: float = 0.0   # 预置频率余量（吸收多普勒）
    needs_tle: bool = True
    needs_ephemeris_note: str = ""
    notes: str = ""
    source: str = ""


# ── 卫星接收剖面表（材料可核实参数；缺参数处留 None 并注明，不臆造）──
RX_PROFILES: Dict[str, RxProfile] = {
    "ASTU-1": RxProfile(
        name="ASTU-1",
        downlink_mhz=435.400,
        uplink_mhz=145.000,
        band="UHF",
        protocol="BPSK 遥测（帧级协议由外部 dslwp 补丁实现）",
        orbit="LEO",
        pointing="实时跟踪（需 TLE 预测过境）",
        altitude_km=None,
        tune_margin_hz=10_000.0,
        needs_tle=True,
        notes=(
            "下行 435.400 MHz / 上行 145.000 MHz；接收侧按真实下行低 10 kHz 预置"
            "（Orbitron MyDDE 下传含多普勒频率），以 10 kHz 余量覆盖多普勒漂移。"
            "教程只给到星座/比特级链路参数，帧同步字、CRC、解扰、卷积码、"
            "交织与遥测字段换算未在教程给出——需外部补丁补齐，工具如实标注。"
        ),
        source="《使用 SDR 解码阿斯图友谊号卫星遥测》教程 v0.0.3",
    ),
    "GK-2A": RxProfile(
        name="GK-2A",
        downlink_mhz=1690.0,
        band="L",
        protocol="LRIT",
        orbit="GEO",
        pointing="固定指向（同步轨道）",
        geo_longitude_deg=128.2,
        altitude_km=35786.0,
        notes=(
            "定点东经 128.2°，距地约 35800 km；LRIT 下行约 1.69 GHz（L 波段），"
            "每 10 分钟下行一次全圆盘云图；中国东部/中部仰角可达 45° 以上。"
            "下行频率为材料给出的约值（1.69 GHz），精确中心频率以卫星任务参数为准。"
        ),
        source="《气象卫星图片接收赛前培训》",
    ),
    "FY-4A": RxProfile(
        name="FY-4A",
        downlink_mhz=1690.0,
        band="L",
        protocol="LRIT / HRIT（L 波段）+ DRT（S 波段）",
        orbit="GEO",
        pointing="固定指向（同步轨道）",
        geo_longitude_deg=104.7,
        altitude_km=35786.0,
        notes=(
            "定点东经 104.7°；LRIT/HRIT 走 L 波段，DRT 走 S 波段、码率更高，"
            "对天线指向精度与信噪比要求更高。"
        ),
        source="《气象卫星图片接收赛前培训》",
    ),
    "FY-4B": RxProfile(
        name="FY-4B",
        downlink_mhz=1690.0,
        band="L",
        protocol="LRIT / HRIT（L 波段）+ DRT（S 波段）",
        orbit="GEO",
        pointing="固定指向（同步轨道）",
        geo_longitude_deg=133.0,
        altitude_km=35786.0,
        notes="定点东经 133°；与 FY-4A 同型号，可用同一天线指向方案切换接收。",
        source="《气象卫星图片接收赛前培训》",
    ),
    "FY-3D": RxProfile(
        name="FY-3D",
        downlink_mhz=137.100,
        band="UHF",
        protocol="APT / HRPT（X 波段 HRPT 频率按当地方案确认）",
        orbit="LEO",
        pointing="实时跟踪（需 TLE 预测过境）",
        altitude_km=836.0,
        notes=(
            "极轨，轨道高度 836 km，每日过境多次，单次窗口 10–15 分钟，需实时跟踪天线。"
            "仓库既有频率表记 137.100 MHz；材料另称 HRPT/DPT 走 L/X 波段，"
            "X 波段具体频率材料未给，此处不臆造。"
        ),
        source="《气象卫星图片接收赛前培训》+ 仓库既有频率表",
    ),
    "LRO": RxProfile(
        name="LRO",
        downlink_mhz=2271.0,
        band="S",
        protocol="S 波段载波（多普勒观测，非图像）",
        orbit="深空（月球轨道）",
        pointing="深空跟踪（需月球星历）",
        altitude_km=None,
        needs_tle=False,
        needs_ephemeris_note=(
            "LRO 为月球轨道卫星，TLE 不适用（材料明确提示）；天空预测用"
            "仓库既有 lro_track 工具，多普勒定轨用 doppler_orbit_determine。"
        ),
        notes="下行 S 波段 2200–2300 MHz，接收中心约 2271 MHz；定轨目标为 LRO。",
        source="《赛前培训-定轨》",
    ),
}


def _err(e: Exception) -> ToolResult:
    return ToolResult(
        success=False,
        content=f"参数/计算错误: {type(e).__name__}: {e}",
        data={"error": str(e)},
    )


def _profile_dict(p: RxProfile) -> Dict[str, Any]:
    return {
        "name": p.name,
        "downlink_mhz": p.downlink_mhz,
        "uplink_mhz": p.uplink_mhz,
        "band": p.band,
        "protocol": p.protocol,
        "orbit": p.orbit,
        "pointing": p.pointing,
        "geo_longitude_deg": p.geo_longitude_deg,
        "altitude_km": p.altitude_km,
        "tune_margin_hz": p.tune_margin_hz,
        "needs_tle": p.needs_tle,
        "needs_ephemeris_note": p.needs_ephemeris_note,
        "notes": p.notes,
        "source": p.source,
    }


# ═══════════════════════════════════════════════════════════
# 1. 星表查询
# ═══════════════════════════════════════════════════════════
def _sat_rx_profile(args):
    """查询卫星接收剖面（频率/协议/轨道/指向要求）。"""
    try:
        name = str(args.get("name", "")).strip()
        if not name:
            raise ValueError("name 不能为空")
        key = None
        for k in RX_PROFILES:
            if k.lower() == name.lower():
                key = k
                break
        if key is None:
            # 允许部分匹配（"FY-4" 命中 FY-4A/FY-4B）
            hits = [k for k in RX_PROFILES if name.lower() in k.lower()]
            if len(hits) == 1:
                key = hits[0]
            elif len(hits) > 1:
                return ToolResult(
                    success=True,
                    content="匹配到多颗卫星，请给出更具体的名称："
                            + "、".join(hits),
                    data={"matches": hits},
                )
        if key is None:
            return ToolResult(
                success=False,
                content=f"未收录该卫星的接收剖面。已收录："
                        + "、".join(RX_PROFILES.keys()),
                data={"available": list(RX_PROFILES.keys())},
            )
        p = RX_PROFILES[key]
        return ToolResult(
            success=True,
            content=(
                f"{p.name}：下行 "
                f"{p.downlink_mhz if p.downlink_mhz is not None else '未给'} MHz"
                f"（{p.band}）· 协议 {p.protocol} · {p.orbit} · {p.pointing}"
                f"\n备注：{p.notes}"
            ),
            data=_profile_dict(p),
        )
    except Exception as e:
        return _err(e)


# ═══════════════════════════════════════════════════════════
# 2. 同步轨道指向（无 TLE 也能算，纯解析几何）
# ═══════════════════════════════════════════════════════════
def _geo_pointing(args):
    """同步轨道卫星指向：观测点 → 方位角/仰角/斜距/时延（解析法，无需 TLE）。"""
    try:
        obs_lat = float(args["observer_lat_deg"])
        obs_lon = float(args["observer_lon_deg"])
        obs_alt_m = float(args.get("observer_alt_m", 0.0))
        name = str(args.get("satellite", "")).strip()
        sat_lon = args.get("sat_longitude_deg")
        sat_alt_km = args.get("sat_altitude_km")

        if sat_lon is None:
            if not name:
                raise ValueError("需给 satellite 名或 sat_longitude_deg")
            key = next((k for k in RX_PROFILES if k.lower() == name.lower()), None)
            if key is None:
                raise ValueError(f"未收录卫星 {name!r}，请直接给 sat_longitude_deg")
            prof = RX_PROFILES[key]
            if prof.geo_longitude_deg is None:
                raise ValueError(
                    f"{key} 非同步轨道或无定点经度（needs_tle={prof.needs_tle}），"
                    "请改用 TLE 传播算指向（sat_pass_predict 积木）"
                )
            sat_lon = prof.geo_longitude_deg
            if sat_alt_km is None and prof.altitude_km is not None:
                sat_alt_km = prof.altitude_km
        sat_lon = float(sat_lon)
        if sat_alt_km is None:
            sat_alt_km = 35786.0
        sat_alt_km = float(sat_alt_km)

        # ECEF 坐标
        def ecef(lat_deg, lon_deg, alt_m):
            lat = math.radians(lat_deg)
            lon = math.radians(lon_deg)
            r = EARTH_RADIUS_M + alt_m
            return (r * math.cos(lat) * math.cos(lon),
                    r * math.cos(lat) * math.sin(lon),
                    r * math.sin(lat))

        sx, sy, sz = ecef(0.0, sat_lon, sat_alt_km * 1000.0)
        ox, oy, oz = ecef(obs_lat, obs_lon, obs_alt_m)
        dx, dy, dz = sx - ox, sy - oy, sz - oz
        rng = math.sqrt(dx * dx + dy * dy + dz * dz)

        # ENU 基（观测点处）
        lat = math.radians(obs_lat)
        lon = math.radians(obs_lon)
        east = (-math.sin(lon), math.cos(lon), 0.0)
        north = (-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon),
                 math.cos(lat))
        up = (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon),
              math.sin(lat))
        d_e = dx * east[0] + dy * east[1] + dz * east[2]
        d_n = dx * north[0] + dy * north[1] + dz * north[2]
        d_u = dx * up[0] + dy * up[1] + dz * up[2]

        el = math.degrees(math.asin(max(-1.0, min(1.0, d_u / rng))))
        az = math.degrees(math.atan2(d_e, d_n)) % 360.0
        one_way_ms = rng / C_LIGHT * 1000.0
        return ToolResult(
            success=True,
            content=(
                f"指向：方位 {az:.2f}°  仰角 {el:.2f}°  斜距 {rng/1000.0:.1f} km"
                f"  单程时延 {one_way_ms:.2f} ms"
                f"\n（同步轨道解析法：定点经度 {sat_lon:.4f}°E，高度 {sat_alt_km:.0f} km）"
            ),
            data={
                "azimuth_deg": az,
                "elevation_deg": el,
                "range_m": rng,
                "range_km": rng / 1000.0,
                "one_way_delay_ms": one_way_ms,
                "two_way_delay_ms": 2 * one_way_ms,
                "sat_longitude_deg": sat_lon,
                "sat_altitude_km": sat_alt_km,
                "observer_lat_deg": obs_lat,
                "observer_lon_deg": obs_lon,
            },
        )
    except Exception as e:
        return _err(e)


# ═══════════════════════════════════════════════════════════
# 3. 抛物面天线增益与波束
# ═══════════════════════════════════════════════════════════
def dish_gain_db(diameter_m: float, freq_mhz: float, efficiency: float = 0.6) -> float:
    """抛物面天线增益（dBi）：G = η (π D f / c)^2。"""
    lam = C_LIGHT / (freq_mhz * 1e6)
    return 10.0 * math.log10(efficiency * (math.pi * diameter_m / lam) ** 2)


def beamwidth_deg(diameter_m: float, freq_mhz: float) -> float:
    """半功率波束宽度（度）：θ3dB ≈ 70 λ / D（工程经验式）。"""
    lam = C_LIGHT / (freq_mhz * 1e6)
    return 70.0 * lam / diameter_m


def _dish_gain(args):
    """抛物面天线：增益 + 波束宽度 + 焦比建议。"""
    try:
        d = float(args["diameter_m"])
        f = float(args["frequency_mhz"])
        eta = float(args.get("efficiency", 0.6))
        if d <= 0 or f <= 0:
            raise ValueError("diameter_m 与 frequency_mhz 必须为正")
        g = dish_gain_db(d, f, eta)
        bw = beamwidth_deg(d, f)
        fd = float(args.get("f_over_d", DEFAULT_FD_RATIO))
        return ToolResult(
            success=True,
            content=(
                f"口径 {d:.2f} m @ {f:.1f} MHz：增益 {g:.1f} dBi（η={eta:.2f}）"
                f"  波束 {bw:.1f}°  建议焦距 F/D={fd:.2f}（{fd*d:.3f} m）"
            ),
            data={
                "gain_dbi": g,
                "beamwidth_deg": bw,
                "f_over_d": fd,
                "focal_length_m": fd * d,
                "efficiency": eta,
                "wavelength_m": C_LIGHT / (f * 1e6),
            },
        )
    except Exception as e:
        return _err(e)


# ═══════════════════════════════════════════════════════════
# 4. 指向偏差 → 增益损失 → 容差
# ═══════════════════════════════════════════════════════════
def pointing_penalty_db(offset_deg: float, bw_deg: float) -> float:
    """指向偏差的增益损失（dB）：高斯近似 12·(θ/θ3dB)^2。

    高斯近似是工程经验式（真实抛物面为 sinc² 旁瓣结构）；本函数在 docstring
    与输出里标注近似性质，不冒充精确方向图积分。
    """
    if bw_deg <= 0:
        raise ValueError("波束宽度必须为正")
    return 12.0 * (abs(offset_deg) / bw_deg) ** 2


def _pointing_tolerance(args):
    """指向容差：偏差→增益损失，并给出"跌出解码门限"的最大可接受偏差。"""
    try:
        bw = float(args["beamwidth_deg"])
        offset = float(args.get("offset_deg", 0.0))
        margin_db = float(args.get("margin_db", 0.0))
        tol = float(args.get("tolerance_deg", DEFAULT_POINTING_TOLERANCE_DEG))
        loss = pointing_penalty_db(offset, bw)
        if margin_db > 0:
            max_off = bw * math.sqrt(margin_db / 12.0)
        else:
            max_off = None
        ok = abs(offset) <= tol
        return ToolResult(
            success=True,
            content=(
                f"指向偏差 {offset:.2f}°（波束 {bw:.1f}°）→ 增益损失 {loss:.2f} dB"
                f"（高斯近似）"
                + (f"；余量 {margin_db:.1f} dB 下最大可接受偏差 {max_off:.2f}°"
                   if max_off is not None else "")
                + f"\n容差判据（±{tol:.1f}°）：{'通过' if ok else '超差，可能跌至解码门限以下'}"
            ),
            data={
                "offset_deg": offset,
                "beamwidth_deg": bw,
                "gain_loss_db": loss,
                "max_offset_deg_for_margin": max_off,
                "tolerance_deg": tol,
                "within_tolerance": ok,
                "model": "gaussian-approx",
            },
        )
    except Exception as e:
        return _err(e)


# ═══════════════════════════════════════════════════════════
# 5. 前端链路预算
# ═══════════════════════════════════════════════════════════
def _fe_link_budget(args):
    """前端链路预算：天线→馈源→LNA→馈线→SDR，给系统噪声系数与门限判据。

    诚实说明：本预算只算"可算的项"（天线增益/指向损失/馈线损耗/级联噪声系数）。
    卫星侧 EIRP 未知，故不臆造接收电平；用实测 SNR 与要求 SNR 比较给判据。
    """
    try:
        d = float(args["diameter_m"])
        f = float(args["frequency_mhz"])
        offset = float(args.get("offset_deg", 0.0))
        eta = float(args.get("efficiency", 0.6))
        lna_gain = float(args.get("lna_gain_db", DEFAULT_LNA_GAIN_DB))
        lna_nf = float(args.get("lna_nf_db", DEFAULT_LNA_NF_DB))
        fl_len = float(args.get("feedline_length_m", 0.0))
        fl_loss_pm = float(args.get("feedline_loss_db_per_m",
                                   DEFAULT_FEEDLINE_LOSS_DB_PER_M))
        sdr_nf = float(args.get("sdr_nf_db", 5.0))
        req_snr = float(args.get("required_snr_db", DEFAULT_REQUIRED_SNR_DB))
        meas_snr = args.get("measured_snr_db")
        spacing = args.get("lna_spacing_m")
        focus_off = args.get("focus_offset_m")

        g_dish = dish_gain_db(d, f, eta)
        bw = beamwidth_deg(d, f)
        loss_point = pointing_penalty_db(offset, bw)
        loss_feed = fl_len * fl_loss_pm
        # Friis 级联噪声系数：LNA 增益大，SDR 贡献被压低
        g_lna_lin = 10 ** (lna_gain / 10.0)
        nf_lin = 10 ** (lna_nf / 10.0)
        sdr_lin = 10 ** (sdr_nf / 10.0)
        nf_sys = 10 * math.log10(nf_lin + (sdr_lin - 1) / g_lna_lin)
        g_total = g_dish + loss_point - loss_feed + lna_gain

        findings: List[str] = []
        ok = True
        if abs(offset) > DEFAULT_POINTING_TOLERANCE_DEG:
            ok = False
            findings.append(
                f"指向偏差 {offset:.2f}° 超过 ±{DEFAULT_POINTING_TOLERANCE_DEG}° 容差，"
                f"增益损失 {loss_point:.2f} dB，信号可能跌至解码门限以下")
        if lna_gain < DEFAULT_LNA_GAIN_DB:
            ok = False
            findings.append(f"LNA 增益 {lna_gain:.1f} dB 低于建议的 ≥{DEFAULT_LNA_GAIN_DB} dB")
        if lna_nf > DEFAULT_LNA_NF_DB:
            findings.append(f"LNA 噪声系数 {lna_nf:.2f} dB 大于建议的 <{DEFAULT_LNA_NF_DB} dB（可解但灵敏度变差）")
        if fl_len > DEFAULT_FEEDLINE_MAX_M:
            findings.append(
                f"馈线 {fl_len:.1f} m 超过建议的 <{DEFAULT_FEEDLINE_MAX_M} m，"
                f"已计 {loss_feed:.2f} dB 损耗（每米 {fl_loss_pm:.2f} dB）")
        if spacing is not None and float(spacing) > DEFAULT_LNA_SPACING_MAX_M:
            findings.append(
                f"LNA 距馈源 {float(spacing)*100:.0f} cm 超过 {DEFAULT_LNA_SPACING_MAX_M*100:.0f} cm，"
                "馈线损耗将抬高噪声温度")
        if focus_off is not None and abs(float(focus_off)) > DEFAULT_FOCUS_TOLERANCE_M:
            findings.append(
                f"馈源离焦 {abs(float(focus_off))*100:.1f} cm 超过 {DEFAULT_FOCUS_TOLERANCE_M*100:.0f} cm，"
                "增益可能下降 3–6 dB")
        if meas_snr is not None:
            ms = float(meas_snr)
            eff = ms - loss_point
            if eff < req_snr:
                ok = False
                findings.append(
                    f"实测 SNR {ms:.1f} dB 扣除指向损失 {loss_point:.2f} dB 后为 {eff:.1f} dB，"
                    f"低于要求 {req_snr:.1f} dB → 判不可解")
            else:
                findings.append(
                    f"实测 SNR {ms:.1f} dB 扣除指向损失后 {eff:.1f} dB ≥ 要求 {req_snr:.1f} dB → 可解")

        return ToolResult(
            success=True,
            content=(
                f"天线 {g_dish:.1f} dBi（波束 {bw:.1f}°）→ 指向损失 {loss_point:.2f} dB"
                f" → 馈线 -{loss_feed:.2f} dB → LNA +{lna_gain:.1f} dB"
                f" → 系统 NF {nf_sys:.2f} dB；总增益 {g_total:.1f} dB"
                f"\n判据：{'通过' if ok else '不通过'}"
                + ("".join("\n  · " + x for x in findings) if findings else "")
            ),
            data={
                "dish_gain_dbi": g_dish,
                "beamwidth_deg": bw,
                "pointing_loss_db": loss_point,
                "feedline_loss_db": loss_feed,
                "lna_gain_db": lna_gain,
                "lna_nf_db": lna_nf,
                "system_noise_figure_db": nf_sys,
                "total_gain_db": g_total,
                "required_snr_db": req_snr,
                "measured_snr_db": meas_snr,
                "verdict_ok": ok,
                "findings": findings,
            },
        )
    except Exception as e:
        return _err(e)


# ═══════════════════════════════════════════════════════════
# 6. 前端故障诊断（症状 → 环节 → 处置）
# ═══════════════════════════════════════════════════════════
DIAGNOSIS: List[Dict[str, Any]] = [
    {
        "symptom": "无信号",
        "keywords": ["无信号", "没信号", "no signal", "频谱平"],
        "steps": [
            "天线指向：粗对准（Look4Sat/解析指向），再在频谱上精调 0.5° 步进锁峰",
            "馈源连接：检查接头与馈线是否接反/松动，罐子馈源探针位置是否偏移 >5 mm",
            "LNA 供电：确认 LNA 供电与增益设置",
            "馈线完整性：换线复测，1.69 GHz 每米 0.5–1 dB 损耗",
            "SDR 识别：设备列表为空时重装 Zadig 驱动",
            "软件配置：SDR 采样率/增益/频率中心与实际链路一致",
        ],
    },
    {
        "symptom": "SNR 极低",
        "keywords": ["snr", "信噪比低", "snr 极低", "噪声大"],
        "steps": [
            "按 0.5° 步进扫指向，看 SNR 变化曲线找峰",
            "前端噪声：LNA 紧贴馈源（<30 cm），馈线尽量短（<5 m）",
            "电磁环境：避开基站/WiFi 干扰，或加 L 波段带通滤波器",
            "软件增益：增益不是越大越好，过高会抬底噪反降 SNR",
        ],
    },
    {
        "symptom": "图像有条纹或缺损",
        "keywords": ["条纹", "缺损", "花", "撕裂", "条纹图"],
        "steps": [
            "信号短暂中断或天线被风吹偏 → 加固支架、查接头",
            "重收该时段数据（每 10 分钟一个完整下行周期）",
            "检查馈源/反射面形变（实心铝板需防形变）",
        ],
    },
    {
        "symptom": "全黑或全白",
        "keywords": ["全黑", "全白", "黑图", "白图", "饱和"],
        "steps": [
            "重置图像预处理参数（对比度/增益）",
            "检查极化与增益设置是否与预期一致",
            "若全白，多为增益过高；全黑多为增益过低或未锁定",
        ],
    },
    {
        "symptom": "设备列表为空",
        "keywords": ["设备列表", "no device", "找不到设备", "驱动"],
        "steps": [
            "重装 Zadig 驱动（USB 驱动）",
            "确认 USB 线材与端口供电",
            "确认 SDR 型号对应的驱动包",
        ],
    },
]


def _fe_diagnose(args):
    """前端故障诊断：症状 → 排查步骤（按环节顺序）。"""
    try:
        sym = str(args.get("symptom", "")).strip()
        if not sym:
            raise ValueError("symptom 不能为空")
        low = sym.lower()
        hits = [d for d in DIAGNOSIS
                if any(k.lower() in low for k in d["keywords"])
                or d["symptom"] in sym]
        if not hits:
            return ToolResult(
                success=False,
                content="未匹配已知症状。已知症状："
                        + "、".join(d["symptom"] for d in DIAGNOSIS),
                data={"known_symptoms": [d["symptom"] for d in DIAGNOSIS]},
            )
        d = hits[0]
        return ToolResult(
            success=True,
            content=f"症状「{sym}」→ 排查环节（按顺序）：\n"
                    + "\n".join(f"{i+1}. {s}" for i, s in enumerate(d["steps"])),
            data={"symptom": d["symptom"], "steps": d["steps"],
                  "other_symptoms": [x["symptom"] for x in DIAGNOSIS
                                     if x["symptom"] != d["symptom"]]},
        )
    except Exception as e:
        return _err(e)


# ═══════════════════════════════════════════════════════════
# 7. 罐子馈源几何
# ═══════════════════════════════════════════════════════════
def te11_cutoff_hz(diameter_m: float) -> float:
    """圆波导 TE11 模截止频率：fc = 1.8412 c / (π D)。"""
    return TE11_ROOT * C_LIGHT / (math.pi * diameter_m)


def guide_wavelength_m(freq_hz: float, cutoff_hz: float) -> float:
    """波导波长：λg = λ0 / sqrt(1 - (fc/f)^2)（f > fc）。"""
    if freq_hz <= cutoff_hz:
        raise ValueError(
            f"工作频率 {freq_hz/1e6:.1f} MHz 必须高于 TE11 截止 {cutoff_hz/1e6:.1f} MHz")
    lam0 = C_LIGHT / freq_hz
    return lam0 / math.sqrt(1.0 - (cutoff_hz / freq_hz) ** 2)


def _cantenna_design(args):
    """罐子（Cantenna）馈源几何：截止频率/波导波长/探针位置校验。"""
    try:
        freq = float(args["frequency_mhz"]) * 1e6
        d_mm = float(args["tank_inner_diameter_mm"])
        l_mm = args.get("tank_length_mm")
        probe_mm = args.get("probe_length_mm")
        offset_mm = args.get("probe_offset_from_bottom_mm")
        fc = te11_cutoff_hz(d_mm / 1000.0)
        if freq <= fc:
            raise ValueError(
                f"{freq/1e6:.1f} MHz 低于 TE11 截止 {fc/1e6:.1f} MHz，无 TE11 模")
        lam_g = guide_wavelength_m(freq, fc)
        lam0 = C_LIGHT / freq
        findings = []
        rec_len = round(0.75 * lam_g * 1000.0 / 5.0) * 5.0
        rec_probe = round(0.25 * lam_g * 1000.0 / 5.0) * 5.0
        rec_insert = round(0.25 * lam0 * 1000.0 / 5.0) * 5.0
        if l_mm is not None:
            dev = abs(float(l_mm) - rec_len)
            if dev > 5.0:
                findings.append(
                    f"罐长 {float(l_mm):.0f} mm 与 0.75λg={rec_len:.0f} mm 偏差 {dev:.0f} mm"
                    "（>5 mm 显著恶化驻波比）")
        if probe_mm is not None:
            dev = abs(float(probe_mm) - rec_insert)
            if dev > 5.0:
                findings.append(
                    f"探针入罐 {float(probe_mm):.0f} mm 与 λ/4={rec_insert:.0f} mm 偏差 {dev:.0f} mm"
                    "（>5 mm 显著恶化驻波比）")
        if offset_mm is not None:
            dev = abs(float(offset_mm) - rec_probe)
            if dev > 5.0:
                findings.append(
                    f"探针离罐底 {float(offset_mm):.0f} mm 与 0.25λg={rec_probe:.0f} mm 偏差 {dev:.0f} mm"
                    "（>5 mm 显著恶化驻波比）")
        return ToolResult(
            success=True,
            content=(
                f"罐内径 {d_mm:.0f} mm @ {freq/1e6:.1f} MHz：TE11 截止 {fc/1e6:.1f} MHz"
                f"（工作高于截止 {freq/1e6-fc/1e6:.1f} MHz）"
                f"  λ0={lam0*1000:.1f} mm  λg={lam_g*1000:.1f} mm"
                f"\n建议：罐长≈0.75λg={rec_len:.0f} mm  探针入罐≈λ/4={rec_insert:.0f} mm"
                f"  探针离底≈0.25λg={rec_probe:.0f} mm"
                + ("".join("\n  · " + x for x in findings) if findings else "\n几何在容差内")
            ),
            data={
                "te11_cutoff_hz": fc,
                "cutoff_margin_mhz": (freq - fc) / 1e6,
                "lambda0_m": lam0,
                "lambda_g_m": lam_g,
                "recommended_tank_length_mm": rec_len,
                "recommended_probe_insertion_mm": rec_insert,
                "recommended_probe_offset_mm": rec_probe,
                "findings": findings,
            },
        )
    except Exception as e:
        return _err(e)


# ═══════════════════════════════════════════════════════════
# 8. 遥测接收链参数（ASTU-1 教程级）
# ═══════════════════════════════════════════════════════════
ASTU_RX_CHAIN: Dict[str, Any] = {
    "satellite": "ASTU-1",
    "downlink_mhz": 435.400,
    "uplink_mhz": 145.000,
    "prescribed_downlink_mhz": 435.390,   # 真实下行低 10 kHz，吸收多普勒
    "tune_margin_hz": 10_000,
    "modulation": "BPSK（星座 {0,1} / {-1,+1}，rot_sym 2）",
    "rtlsdr": {
        "sample_rate_hz": 256_000,
        "center_freq_hz": 435_400_000,
        "freq_corr_ppm": 0,
        "rf_gain_db": 45.0, "if_gain_db": 20, "bb_gain_db": 20,
    },
    "resampler": {"interpolation": 24, "decimation": 125,
                  "fractional_bw": 0.06, "out_rate_hz": 49_152},
    "multiply_const": 10,
    "lpf": {"sample_rate_hz": 48_000, "cutoff_hz": 16_000,
            "transition_hz": 2_000, "window": "hamming", "beta": 6.75,
            "decimation": 1},
    "agc": {"type": "feed-forward", "max_samples": 1024, "reference": 1},
    "fll": {"samples_per_symbol": 5, "rolloff": 0.2,
            "prototype_filter_size": 999, "loop_bandwidth": 0.01},
    "clock_sync": {"samples_per_symbol": 5, "loop_bandwidth": 0.05,
                   "filter_size": 16, "initial_phase": 0,
                   "max_rate_deviation": 0.05, "output_sps": 2},
    "doppler": {"model": "Plan13", "length_rate": 112, "satellite_id": 33,
                "height_m": 650, "center_freq_hz": 435_400_000,
                "sample_rate_hz": 48_000, "mode": "RX"},
    "sdrsharp_audio": {"mode": "USB", "filter": "Blackman-Harris",
                       "bandwidth_hz": 24_000},
    "frame_protocol_missing": (
        "帧同步字/字同步/帧长、CRC 算法与覆盖范围、Scrambler 多项式与种子、"
        "TM 差分编码基准与复位时机、卷积码多项式与码率、交织方式、符号映射与"
        "字节序、遥测包头/包序号/时间戳字段与各通道换算系数——教程未给出，"
        "由外部 dslwp 补丁与专用程序承担；本工具如实标注，不臆造参数。"
    ),
}


def _astu_rx_chain(args):
    """遥测接收链参数（ASTU-1 教程级，逐块参数直出）。"""
    try:
        which = str(args.get("part", "all")).lower()
        if which in ("all", "chain"):
            return ToolResult(
                success=True,
                content=(
                    "ASTU-1 接收链参数：\n"
                    f"  下行 {ASTU_RX_CHAIN['downlink_mhz']} MHz（预置 "
                    f"{ASTU_RX_CHAIN['prescribed_downlink_mhz']} MHz，低 10 kHz 吸收多普勒）"
                    f"  上行 {ASTU_RX_CHAIN['uplink_mhz']} MHz\n"
                    f"  调制 {ASTU_RX_CHAIN['modulation']}\n"
                    f"  RTL-SDR {ASTU_RX_CHAIN['rtlsdr']['sample_rate_hz']} sps @ "
                    f"{ASTU_RX_CHAIN['rtlsdr']['center_freq_hz']} Hz，"
                    f"重采样 {ASTU_RX_CHAIN['resampler']['interpolation']}/"
                    f"{ASTU_RX_CHAIN['resampler']['decimation']} → "
                    f"{ASTU_RX_CHAIN['resampler']['out_rate_hz']} Hz\n"
                    f"  LPF {ASTU_RX_CHAIN['lpf']['cutoff_hz']} Hz @ "
                    f"{ASTU_RX_CHAIN['lpf']['sample_rate_hz']} Hz，"
                    f"AGC {ASTU_RX_CHAIN['agc']['max_samples']} 样本\n"
                    f"  FLL sps={ASTU_RX_CHAIN['fll']['samples_per_symbol']} "
                    f"lbw={ASTU_RX_CHAIN['fll']['loop_bandwidth']}，"
                    f"PCS sps={ASTU_RX_CHAIN['clock_sync']['samples_per_symbol']} "
                    f"lbw={ASTU_RX_CHAIN['clock_sync']['loop_bandwidth']}\n"
                    f"  多普勒 Plan13：length_rate={ASTU_RX_CHAIN['doppler']['length_rate']} "
                    f"sat_id={ASTU_RX_CHAIN['doppler']['satellite_id']} "
                    f"height={ASTU_RX_CHAIN['doppler']['height_m']} m\n"
                    f"\n帧级协议缺失项：{ASTU_RX_CHAIN['frame_protocol_missing']}"
                ),
                data=dict(ASTU_RX_CHAIN),
            )
        part = ASTU_RX_CHAIN.get(which)
        if part is None:
            raise ValueError(
                f"未知 part={which!r}，可用："
                + "、".join(k for k in ASTU_RX_CHAIN if k != "frame_protocol_missing"))
        return ToolResult(success=True, content=f"{which}: {part}", data={which: part})
    except Exception as e:
        return _err(e)


# ═══════════════════════════════════════════════════════════
# 注册
# ═══════════════════════════════════════════════════════════
def register_acquisition_tools(registry) -> None:
    """把射频前端/指向规划积木注册进工具注册表。

    每块积木一个独立小工具，模型现场逐块调用、自由编排——绝不打包成
    "接收方案" 大黑盒（AGENTS.md「原子能力、模型编排」）。
    """
    R = registry.register

    R(
        name="sat_rx_profile",
        description="卫星接收剖面查询：下行/上行频率、频段、协议、轨道类型、指向要求、"
                    "预置频率余量。原子积木，供模型组合接收计划。",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "卫星名，如 ASTU-1 / GK-2A / FY-4A / LRO"},
        }, "required": ["name"]},
        handler=_sat_rx_profile, category="acquisition",
    )
    R(
        name="geo_pointing",
        description="同步轨道卫星指向（解析法，无需 TLE）：方位角/仰角/斜距/上下行时延。"
                    "原子积木，供模型组合「对星」流程。",
        parameters={"type": "object", "properties": {
            "observer_lat_deg": {"type": "number"},
            "observer_lon_deg": {"type": "number"},
            "observer_alt_m": {"type": "number", "default": 0},
            "satellite": {"type": "string", "description": "卫星名（同步轨道剖面表内）"},
            "sat_longitude_deg": {"type": "number", "description": "定点经度（优先于 satellite）"},
            "sat_altitude_km": {"type": "number", "default": 35786},
        }, "required": ["observer_lat_deg", "observer_lon_deg"]},
        handler=_geo_pointing, category="acquisition",
    )
    R(
        name="dish_gain",
        description="抛物面天线：增益(dBi)/波束宽度(°)/建议焦比 F/D。原子积木。",
        parameters={"type": "object", "properties": {
            "diameter_m": {"type": "number", "description": "口径（米）"},
            "frequency_mhz": {"type": "number"},
            "efficiency": {"type": "number", "default": 0.6},
            "f_over_d": {"type": "number", "default": 0.35},
        }, "required": ["diameter_m", "frequency_mhz"]},
        handler=_dish_gain, category="acquisition",
    )
    R(
        name="pointing_tolerance",
        description="指向偏差→增益损失（高斯近似 12(θ/θ3dB)^2）与最大可接受偏差。"
                    "原子积木。",
        parameters={"type": "object", "properties": {
            "beamwidth_deg": {"type": "number"},
            "offset_deg": {"type": "number", "default": 0},
            "margin_db": {"type": "number", "description": "指向损失可用余量(dB)，给出最大可接受偏差"},
            "tolerance_deg": {"type": "number", "default": 2.0},
        }, "required": ["beamwidth_deg"]},
        handler=_pointing_tolerance, category="acquisition",
    )
    R(
        name="fe_link_budget",
        description="前端链路预算：天线→指向损失→馈线→LNA→系统噪声系数，并按实测 SNR "
                    "给「可解/不可解」判据与逐条整改建议。原子积木。",
        parameters={"type": "object", "properties": {
            "diameter_m": {"type": "number"},
            "frequency_mhz": {"type": "number"},
            "offset_deg": {"type": "number", "default": 0},
            "efficiency": {"type": "number", "default": 0.6},
            "lna_gain_db": {"type": "number", "default": 20},
            "lna_nf_db": {"type": "number", "default": 1.0},
            "feedline_length_m": {"type": "number", "default": 0},
            "feedline_loss_db_per_m": {"type": "number", "default": 0.5},
            "sdr_nf_db": {"type": "number", "default": 5.0},
            "required_snr_db": {"type": "number", "default": 5.0},
            "measured_snr_db": {"type": "number", "description": "实测 SNR（有则给判据）"},
            "lna_spacing_m": {"type": "number", "description": "LNA 距馈源距离"},
            "focus_offset_m": {"type": "number", "description": "馈源离焦量"},
        }, "required": ["diameter_m", "frequency_mhz"]},
        handler=_fe_link_budget, category="acquisition",
    )
    R(
        name="fe_diagnose",
        description="前端故障诊断：症状（无信号/SNR 极低/图像条纹/全黑全白/设备列表为空）"
                    "→ 按环节排序的排查步骤。原子积木。",
        parameters={"type": "object", "properties": {
            "symptom": {"type": "string"},
        }, "required": ["symptom"]},
        handler=_fe_diagnose, category="acquisition",
    )
    R(
        name="cantenna_design",
        description="罐子（Cantenna）馈源几何：TE11 截止频率/波导波长/罐长与探针位置"
                    "建议值及容差校验。原子积木。",
        parameters={"type": "object", "properties": {
            "frequency_mhz": {"type": "number"},
            "tank_inner_diameter_mm": {"type": "number"},
            "tank_length_mm": {"type": "number"},
            "probe_length_mm": {"type": "number", "description": "探针伸入长度"},
            "probe_offset_from_bottom_mm": {"type": "number", "description": "探针离罐底"},
        }, "required": ["frequency_mhz", "tank_inner_diameter_mm"]},
        handler=_cantenna_design, category="acquisition",
    )
    R(
        name="telemetry_rx_chain",
        description="遥测接收链参数（ASTU-1 教程级逐块参数：频率/重采样/LPF/AGC/FLL/"
                    "时钟同步/多普勒），并如实标注帧级协议缺失项。原子积木。",
        parameters={"type": "object", "properties": {
            "part": {"type": "string",
                     "description": "all 或某个块名（rtlsdr/resampler/lpf/agc/fll/clock_sync/doppler）"},
        }, "required": []},
        handler=_astu_rx_chain, category="acquisition",
    )

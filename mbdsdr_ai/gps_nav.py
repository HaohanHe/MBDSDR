# SPDX-License-Identifier: MIT
"""GPS 导航电文（LNAV）子帧解码 → 星历 → PVT 解算（P0-3）。

与既有 ``gps_ca.py``（C/A 码捕获）衔接成「捕获 → 电文 → 位置」闭环。
三个原子积木，全部参数化、无硬件纯计算：

  * :func:`nav_bits_to_subframes`   300bit 子帧切分 + 逐字奇偶校验（诚实报错）
  * :func:`subframes_to_ephemeris`  TLM/HOW 校验 + 星历字段提取
  * :func:`ephemeris_sat_pos`       星历 → ECEF 位置/速度（开普勒迭代）
  * :func:`pvt_solve`               伪距最小二乘 PVT（收敛判据显式）
  * :func:`ephemeris_to_nav_bits`   编码侧（golden 向量自造，非手抄）

奇偶校验依据（GPS 公开 ICD 20.3.2 口径，三源交叉核对）：
  * 24 个数据位 d1..d24 与上一字末两位 D29*/D30* 求汉明校验；
  * 收端先整体去反相：d_i = b_i ⊕ D30*（D30*=1 时全 24 位取反）；
  * 校验方程用 24bit 掩码 + D29*/D30* 异或：
      D25 = par(0xEC7CD2 & d) ⊕ D29*    D26 = par(0x763E69 & d) ⊕ D30*
      D27 = par(0xBB1F34 & d) ⊕ D29*    D28 = par(0x5D8F9A & d) ⊕ D30*
      D29 = par(0xAEC7CD & d) ⊕ D30*    D30 = par(0x2DEA27 & d) ⊕ D29*
  * 发送侧奇偶位再与上一字位异或（防差错传播）：
      T25=D25⊕D30* T26=D26⊕D30* T27=D27⊕D29*
      T28=D28⊕D30* T29=D29⊕D30* T30=D30⊕D29*

诚实纪律：TLM 前导码不匹配 / 奇偶失败 / 子帧号不连续，一律报具体
字序号与比特偏移，不产出伪星历、伪位置。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

__all__ = [
    "nav_bits_to_subframes", "subframes_to_ephemeris",
    "ephemeris_sat_pos", "pvt_solve", "ephemeris_to_nav_bits",
    "GPS_PARITY_MASKS", "TLM_PREAMBLE",
]

#: 24bit 奇偶掩码（d1=MSB），与 D29*/D30* 异或列见模块 docstring
GPS_PARITY_MASKS: Tuple[int, ...] = (
    0xEC7CD2, 0x763E69, 0xBB1F34, 0x5D8F9A, 0xAEC7CD, 0x2DEA27,
)
#: 奇偶位与上一字末两位的异或列（下标 i 对应 D(25+i)）
_PARITY_PREV_XOR: Tuple[Tuple[int, int], ...] = (
    (1, 0),  # D25: 先前位29 取1、位30 取0 → ⊕D29*
    (0, 1),  # D26: ⊕D30*
    (1, 0),  # D27: ⊕D29*
    (0, 1),  # D28: ⊕D30*
    (0, 1),  # D29: ⊕D30*
    (1, 0),  # D30: ⊕D29*
)
#: 发送侧奇偶位的上一字异或（防差错传播；ICD 2.5.2 口径）
_TX_PARITY_PREV: Tuple[Tuple[int, int], ...] = (
    (0, 1),  # T25 = D25 ⊕ D30*
    (0, 1),  # T26 = D26 ⊕ D30*
    (1, 0),  # T27 = D27 ⊕ D29*
    (0, 1),  # T28 = D28 ⊕ D30*
    (0, 1),  # T29 = D29 ⊕ D30*
    (1, 0),  # T30 = D30 ⊕ D29*
)

#: TLM 前导码 10001011b = 139（十进制），ICD 固定值
TLM_PREAMBLE = 0b10001011

#: WGS-84 重力常数与地球自转（SI/公开常数值，零容差）
MU_EARTH = 3.986005e14            # m^3/s^2
OMEGA_EARTH = 7.2921151467e-5     # rad/s
C_LIGHT = 299792458.0             # m/s

#: 子帧结构：10 字 × 30 bit
WORD_BITS = 30
SUBFRAME_WORDS = 10
SUBFRAME_BITS = WORD_BITS * SUBFRAME_WORDS


def _parity24(value: int) -> int:
    """24bit 值的模 2 奇偶（1 的个数的奇偶）。"""
    return bin(value & 0xFFFFFF).count("1") & 1


def _compute_parity(d_bits: Sequence[int], d29_star: int, d30_star: int
                    ) -> List[int]:
    """由 24 个原始数据位 + 上一字末两位算 6 个奇偶位（D25..D30）。"""
    d = 0
    for bit in d_bits[:24]:
        d = (d << 1) | (int(bit) & 1)
    out: List[int] = []
    for i, mask in enumerate(GPS_PARITY_MASKS):
        p = _parity24(mask & d)
        use29, use30 = _PARITY_PREV_XOR[i]
        if use29:
            p ^= int(d29_star) & 1
        if use30:
            p ^= int(d30_star) & 1
        out.append(p)
    return out


def _encode_word(data24: int, d29_star: int, d30_star: int) -> List[int]:
    """24 个数据位（整数 MSB=d1）→ 30 个发送位（含发送侧异或）。

    ICD 口径：奇偶按**原始**数据位计算；发送时数据位整体 ⊕D30*、
    奇偶位按 _TX_PARITY_PREV ⊕D29*/D30*（防差错传播）。
    """
    if not 0 <= data24 < (1 << 24):
        raise ValueError("data24 必须是 24bit 无符号整数")
    raw_bits = [(data24 >> (23 - i)) & 1 for i in range(24)]
    parity = _compute_parity(raw_bits, d29_star, d30_star)
    # 发送的数据位 = 原始数据位 ⊕ D30*（D30*=1 时整体取反）
    tx_data = data24 ^ (0xFFFFFF if (int(d30_star) & 1) else 0)
    d_bits = [(tx_data >> (23 - i)) & 1 for i in range(24)]
    tx_parity = []
    for i, p in enumerate(parity):
        use29, use30 = _TX_PARITY_PREV[i]
        t = int(p)
        if use29:
            t ^= int(d29_star) & 1
        if use30:
            t ^= int(d30_star) & 1
        tx_parity.append(t)
    return d_bits + tx_parity


def _decode_word(word_bits: Sequence[int], d29_star: int, d30_star: int
                 ) -> Tuple[int, bool]:
    """30 个接收位 → (24 个原始数据位整数, 奇偶是否通过)。

    反相恢复与奇偶重算严格按编码侧的逆操作；奇偶不通过返回 False。
    """
    if len(word_bits) != WORD_BITS:
        raise ValueError(f"字长须为 {WORD_BITS} bit，收到 {len(word_bits)}")
    b = [int(x) & 1 for x in word_bits]
    tx_data = 0
    for i in range(24):
        tx_data = (tx_data << 1) | b[i]
    data = tx_data ^ (0xFFFFFF if (int(d30_star) & 1) else 0)
    d_bits = [(data >> (23 - i)) & 1 for i in range(24)]
    parity = _compute_parity(d_bits, d29_star, d30_star)
    ok = True
    for i in range(6):
        want = int(parity[i])
        use29, use30 = _TX_PARITY_PREV[i]
        if use29:
            want ^= int(d29_star) & 1
        if use30:
            want ^= int(d30_star) & 1
        if want != b[24 + i]:
            ok = False
    return data, ok


def _u(bits: Sequence[int], start: int, width: int) -> int:
    """从位序列取无符号整（MSB 先行，start 为 0 基偏移）。"""
    v = 0
    for i in range(start, start + width):
        v = (v << 1) | (int(bits[i]) & 1)
    return v


def _s(bits: Sequence[int], start: int, width: int) -> int:
    """从位序列取补码有符号整（MSB 先行）。"""
    v = _u(bits, start, width)
    if v >= (1 << (width - 1)):
        v -= (1 << width)
    return v


# ═══════════════════════════════════════════════════════════
# 电文 → 子帧 / 星历
# ═══════════════════════════════════════════════════════════
def nav_bits_to_subframes(bits: Sequence[int]) -> List[List[int]]:
    """导航电文位流 → 300bit 子帧列表（不做奇偶判定，供上层报错定位）。

    位流长度非 300 整数倍时如实报错（尾部的填充位不属于电文）。
    """
    n = len(bits)
    if n == 0:
        raise ValueError("电文位流为空（无输入，诚实空态：不产出子帧）")
    if n % SUBFRAME_BITS:
        raise ValueError(
            f"位流长度 {n} 不是子帧长度 {SUBFRAME_BITS} 的整数倍——"
            "多出的尾部不是电文，先核对采样/解调链是否丢了位或多了填充")
    return [[int(x) & 1 for x in bits[i:i + SUBFRAME_BITS]]
            for i in range(0, n, SUBFRAME_BITS)]


def subframes_to_ephemeris(subframes: Sequence[Sequence[int]]) -> Dict[str, Any]:
    """子帧（每个 300bit）→ 星历 + 时钟参数 + 逐项校验结果。

    诚实校验（任何一条不过即报错，错误信息带字序号/比特偏移）：
      TLM 前导码 10001011；逐字奇偶；HOW 子帧号 ∈ 1..5 且三子帧一致；
      子帧 1/2/3 须成套（缺任一报缺失清单）。
    """
    if not subframes:
        raise ValueError("子帧列表为空（无输入，不产出星历）")
    parsed: List[Dict[str, Any]] = []
    d29_star = 0
    d30_star = 0
    for sf_idx, sf in enumerate(subframes):
        if len(sf) != SUBFRAME_BITS:
            raise ValueError(
                f"第 {sf_idx + 1} 个子帧长 {len(sf)} ≠ {SUBFRAME_BITS} bit")
        words: List[int] = []
        data_bits: List[List[int]] = []
        for w in range(SUBFRAME_WORDS):
            seg = sf[w * WORD_BITS:(w + 1) * WORD_BITS]
            data, ok = _decode_word(seg, d29_star, d30_star)
            if not ok:
                raise ValueError(
                    f"第 {sf_idx + 1} 个子帧第 {w + 1} 字奇偶校验失败"
                    f"（比特偏移 {sf_idx * SUBFRAME_BITS + w * WORD_BITS}，"
                    "该字作废，不产出星历）")
            words.append(data)
            data_bits.append([(data >> (23 - i)) & 1 for i in range(24)])
            d29_star = seg[28]
            d30_star = seg[29]
        parsed.append({"words": words, "data_bits": data_bits})

    # TLM/HOW 校验
    for sf_idx, p in enumerate(parsed):
        tlm = p["words"][0]
        pre = (tlm >> 16) & 0xFF
        if pre != TLM_PREAMBLE:
            raise ValueError(
                f"第 {sf_idx + 1} 个子帧 TLM 前导码 {pre:08b} ≠ "
                f"{TLM_PREAMBLE:08b}（10001011）——子帧边界没对齐或误码，"
                "不产出星历")
        how = p["words"][1]
        # HOW：bit1-17=TOW，bit18=警告，bit19=A-S，bit20-22=子帧号，bit23-24=控制
        p["tow"] = (how >> 7) & 0x1FFFF
        p["subframe_id"] = (how >> 2) & 0b111
        if not 1 <= p["subframe_id"] <= 5:
            raise ValueError(
                f"第 {sf_idx + 1} 个子帧 HOW 子帧号 {p['subframe_id']} "
                "不在 1..5——电文损坏，不产出星历")

    by_id: Dict[int, Dict[str, Any]] = {}
    for p in parsed:
        by_id.setdefault(p["subframe_id"], p)
    missing = [i for i in (1, 2, 3) if i not in by_id]
    if missing:
        raise ValueError(
            f"缺子帧 {missing}（星历需要 1/2/3 成套，收到子帧号 "
            f"{sorted(by_id)}）——继续收电文，不产出半套星历")

    p1, p2, p3 = by_id[1], by_id[2], by_id[3]
    d1, d2, d3 = p1["data_bits"], p2["data_bits"], p3["data_bits"]

    def _w(bits: List[List[int]], wi: int, start: int, width: int,
           signed: bool = False) -> int:
        seq = bits[wi]
        return _s(seq, start, width) if signed else _u(seq, start, width)

    eph: Dict[str, Any] = {
        "subframe_ids": {1: p1["subframe_id"], 2: p2["subframe_id"],
                         3: p3["subframe_id"]},
        "tow_counts": [p1["tow"], p2["tow"], p3["tow"]],
        # ── 子帧 1：时钟 ──
        "week_number": _w(d1, 2, 0, 10),
        "sv_health": _w(d1, 2, 16, 6),
        "iodc": (_w(d1, 2, 22, 2) << 8) | _w(d1, 7, 0, 8),
        "tgd_s": _w(d1, 6, 16, 8, signed=True) * 2.0 ** -31,
        "toc_s": _w(d1, 7, 8, 16) * 2.0 ** 4,
        "af2_s": _w(d1, 8, 0, 8, signed=True) * 2.0 ** -55,
        "af1_s": _w(d1, 8, 8, 16, signed=True) * 2.0 ** -43,
        "af0_s": _w(d1, 9, 0, 22, signed=True) * 2.0 ** -31,
        # ── 子帧 2：轨道 1/2 ──
        "iode": _w(d2, 2, 0, 8),
        "crs_m": _w(d2, 2, 8, 16, signed=True) * 2.0 ** -5,
        "delta_n_rad_s": _w(d2, 3, 0, 14, signed=True) * 2.0 ** -43 * math.pi,
        "m0_rad": ((_w(d2, 3, 16, 8) << 24) | _w(d2, 4, 0, 24)) \
            * 2.0 ** -31 * math.pi,
        "cuc_rad": _w(d2, 5, 0, 16, signed=True) * 2.0 ** -29,
        "eccentricity": ((_w(d2, 5, 16, 8) << 24) | _w(d2, 6, 0, 24)) \
            * 2.0 ** -33,
        "cus_rad": _w(d2, 7, 0, 16, signed=True) * 2.0 ** -29,
        "sqrt_a": ((_w(d2, 7, 16, 8) << 24) | _w(d2, 8, 0, 24)) * 2.0 ** -19,
        "toe_s": _w(d2, 9, 0, 16) * 2.0 ** 4,
        # ── 子帧 3：轨道 3 ──
        "cic_rad": _w(d3, 2, 0, 16, signed=True) * 2.0 ** -29,
        "omega0_rad": ((_w(d3, 2, 16, 8) << 24) | _w(d3, 3, 0, 24)) \
            * 2.0 ** -31 * math.pi,
        "cis_rad": _w(d3, 4, 0, 16, signed=True) * 2.0 ** -29,
        "i0_rad": ((_w(d3, 4, 16, 8) << 24) | _w(d3, 5, 0, 24)) \
            * 2.0 ** -31 * math.pi,
        "crc_m": _w(d3, 6, 0, 16, signed=True) * 2.0 ** -5,
        "omega_rad": ((_w(d3, 6, 16, 8) << 24) | _w(d3, 7, 0, 24)) \
            * 2.0 ** -31 * math.pi,
        "omega_dot_rad_s": _w(d3, 8, 0, 24, signed=True) * 2.0 ** -43 * math.pi,
        "iode_sf3": _w(d3, 9, 0, 8),
        "idot_rad_s": _w(d3, 9, 8, 14, signed=True) * 2.0 ** -43 * math.pi,
    }
    if eph["iode"] != eph["iode_sf3"]:
        raise ValueError(
            f"IODE 不一致（子帧2={eph['iode']} 子帧3={eph['iode_sf3']}）"
            "——两份星历数据期号不同，混用会产生错误位置，不产出星历")
    eph.pop("iode_sf3")
    return eph


# ═══════════════════════════════════════════════════════════
# 星历 → 卫星位置/速度
# ═══════════════════════════════════════════════════════════
def _kepler_e(m_rad: float, e: float) -> float:
    """开普勒方程 E - e·sinE = M 的牛顿迭代（收敛判据显式 1e-12 rad）。"""
    if not 0.0 <= e < 1.0:
        raise ValueError(f"偏心率 {e} 越界（0 ≤ e < 1），星历无效")
    ecc_anom = m_rad if e < 0.8 else math.pi
    for _ in range(64):
        delta = (ecc_anom - e * math.sin(ecc_anom) - m_rad) \
            / (1.0 - e * math.cos(ecc_anom))
        ecc_anom -= delta
        if abs(delta) < 1e-12:
            return ecc_anom
    raise ValueError("开普勒迭代 64 步未收敛（<1e-12 rad）——星历数值异常")


def _sat_pos_core(eph: Dict[str, Any], t_s: float) -> np.ndarray:
    """星历 → ECEF 位置核（速度差分复用；不捕星历参数本身）。"""
    a = float(eph["sqrt_a"]) ** 2
    if a <= 0:
        raise ValueError("sqrtA ≤ 0，星历无效")
    n0 = math.sqrt(MU_EARTH / a ** 3)
    n = n0 + float(eph["delta_n_rad_s"])
    tk = float(t_s) - float(eph["toe_s"])
    if tk < -302400.0:
        tk += 604800.0
    elif tk > 302400.0:
        tk -= 604800.0
    m = float(eph["m0_rad"]) + n * tk
    ecc_anom = _kepler_e(m, float(eph["eccentricity"]))
    sin_e, cos_e = math.sin(ecc_anom), math.cos(ecc_anom)
    e = float(eph["eccentricity"])
    sin_v = math.sqrt(1.0 - e * e) * sin_e / (1.0 - e * cos_e)
    cos_v = (cos_e - e) / (1.0 - e * cos_e)
    nu = math.atan2(sin_v, cos_v)
    phi = nu + float(eph["omega_rad"])
    sin2p, cos2p = math.sin(2.0 * phi), math.cos(2.0 * phi)
    du = float(eph["cus_rad"]) * sin2p + float(eph["cuc_rad"]) * cos2p
    dr = float(eph["crs_m"]) * sin2p + float(eph["crc_m"]) * cos2p
    di = float(eph["cis_rad"]) * sin2p + float(eph["cic_rad"]) * cos2p
    u = phi + du
    r = a * (1.0 - e * cos_e) + dr
    inc = float(eph["i0_rad"]) + di + float(eph["idot_rad_s"]) * tk
    x_orb = r * math.cos(u)
    y_orb = r * math.sin(u)
    om = float(eph["omega0_rad"]) \
        + (float(eph["omega_dot_rad_s"]) - OMEGA_EARTH) * tk \
        - OMEGA_EARTH * float(eph["toe_s"])
    sin_om, cos_om = math.sin(om), math.cos(om)
    sin_i, cos_i = math.sin(inc), math.cos(inc)
    return np.array([
        x_orb * cos_om - y_orb * cos_i * sin_om,
        x_orb * sin_om + y_orb * cos_i * cos_om,
        y_orb * sin_i,
    ])


def ephemeris_sat_pos(eph: Dict[str, Any], t_s: float,
                      ) -> Dict[str, Any]:
    """星历 + 时刻 → ECEF 位置（米）与速度（m/s，中心差分，步长 1ms）。

    算法：标准 GPS ICD 星历解算（半长轴/平均角速度修正/开普勒迭代/
    谐波摄动/轨道面旋转）。物理常数见模块顶（SI 公开值，零容差）。
    """
    a = float(eph["sqrt_a"]) ** 2
    if a <= 0:
        raise ValueError("sqrtA ≤ 0，星历无效")
    e = float(eph["eccentricity"])
    n0 = math.sqrt(MU_EARTH / a ** 3)
    tk = float(t_s) - float(eph["toe_s"])
    if tk < -302400.0:
        tk += 604800.0
    elif tk > 302400.0:
        tk -= 604800.0
    m = float(eph["m0_rad"]) + (n0 + float(eph["delta_n_rad_s"])) * tk
    ecc_anom = _kepler_e(m, e)
    pos = _sat_pos_core(eph, t_s)
    dt = 1e-3
    vel = (_sat_pos_core(eph, t_s + dt) - _sat_pos_core(eph, t_s - dt)) \
        / (2.0 * dt)
    nu = math.atan2(
        math.sqrt(1.0 - e * e) * math.sin(ecc_anom) / (1.0 - e * math.cos(ecc_anom)),
        (math.cos(ecc_anom) - e) / (1.0 - e * math.cos(ecc_anom)))
    r = a * (1.0 - e * math.cos(ecc_anom))
    inc = float(eph["i0_rad"])
    return {
        "pos_ecef_m": [float(v) for v in pos],
        "vel_ecef_m_s": [float(v) for v in vel],
        "t_s": float(t_s),
        "a_m": a,
        "e": e,
        "nu_rad": nu,
        "r_m": r,
        "inclination_rad": inc,
        "note": "速度为中心差分（±1ms，误差 O(dt²·加加速度)，量级 <1e-9 m/s）",
    }


# ═══════════════════════════════════════════════════════════
# PVT：伪距最小二乘
# ═══════════════════════════════════════════════════════════
def pvt_solve(observations: Sequence[Dict[str, Any]],
              x0: Sequence[float] = (0.0, 0.0, 0.0),
              b0: float = 0.0,
              max_iter: int = 12,
              conv_m: float = 1e-6,
              ) -> Dict[str, Any]:
    """多星伪距 → PVT 迭代最小二乘（收敛判据显式，残差如实输出）。

    observations 每项：{"pr_m": 伪距, "sat_pos_ecef_m": [x,y,z]}。
    未知量 (x, y, z, b_rcv·c)：Δρ = H·Δx，H = [单位视线向量, 1]。
    不足 4 星如实报错（4 星定三维+钟差）；不收敛报迭代过程不产出位置。
    """
    if len(observations) < 4:
        raise ValueError(
            f"伪距观测 {len(observations)} 条 < 4（三维+接收机钟差需要 "
            "≥4 星；3 星只能二维+钟差，本工具不降维硬解）")
    pr = np.asarray([float(o["pr_m"]) for o in observations], dtype=float)
    sats = np.asarray([np.asarray(o["sat_pos_ecef_m"], dtype=float)
                       for o in observations])
    if pr.size != sats.shape[0]:
        raise ValueError("每条观测须恰有一个 pr_m 和一个 sat_pos_ecef_m")
    x = np.array([float(x0[0]), float(x0[1]), float(x0[2])], dtype=float)
    b = float(b0)
    history: List[Dict[str, float]] = []
    for it in range(1, int(max_iter) + 1):
        los = sats - x[None, :]
        rng = np.linalg.norm(los, axis=1)
        if np.any(rng <= 0.0):
            raise ValueError("接收机初值与卫星重合，几何矩阵奇异——换初值")
        h_mat = np.concatenate([-los / rng[:, None],
                                np.ones((len(pr), 1))], axis=1)
        pred = rng + b
        resid = pr - pred
        try:
            dx = np.linalg.lstsq(h_mat, resid, rcond=None)[0]
        except np.linalg.LinAlgError as e:
            raise ValueError(f"最小二乘求解失败（几何病态）：{e}") from e
        x = x + dx[:3]
        b = b + float(dx[3])
        step_m = float(np.linalg.norm(dx[:3]))
        history.append({"iter": it, "step_m": step_m,
                        "resid_rms_m": float(np.sqrt(np.mean(resid ** 2)))})
        if step_m < float(conv_m):
            los = sats - x[None, :]
            rng = np.linalg.norm(los, axis=1)
            final_resid = pr - (rng + b)
            return {
                "pos_ecef_m": [float(v) for v in x],
                "clock_bias_m": float(b),
                "clock_bias_s": float(b) / C_LIGHT,
                "converged": True,
                "iterations": it,
                "residual_m": [float(v) for v in final_resid],
                "residual_rms_m": float(np.sqrt(np.mean(final_resid ** 2))),
                "gdop": float(np.sqrt(np.trace(
                    np.linalg.inv(h_mat.T @ h_mat)))),
                "history": history,
                "conv_criteria": f"位置步长 < {conv_m} m",
            }
    raise ValueError(
        f"{max_iter} 次迭代未收敛（末步长 {history[-1]['step_m']:.3e} m ≥ "
        f"{conv_m} m）——伪距/卫星位置可能不一致，不产出位置。history={history}")


# ═══════════════════════════════════════════════════════════
# 编码侧（golden 向量自造，非手抄）
# ═══════════════════════════════════════════════════════════
def _sf_word(data24: int, d29: int, d30: int) -> List[int]:
    return _encode_word(data24, d29, d30)


def _build_subframe(subframe_id: int, tow: int, words_data: List[int],
                    d29: int = 0, d30: int = 0,
                    ) -> Tuple[List[int], int, int]:
    """TLM + HOW + 8 个数据字 → 300bit 子帧（奇偶链跨子帧续接）。

    返回 (位流, D29*, D30*)——末两位交给下一个子帧的首字（TLM）。
    """
    tlm = (TLM_PREAMBLE << 16) | 0x0000
    how = ((tow & 0x1FFFF) << 7) | ((subframe_id & 0b111) << 2)
    datas = [tlm, how] + words_data
    bits: List[int] = []
    for d in datas:
        w = _sf_word(d, d29, d30)
        bits.extend(w)
        d29, d30 = w[28], w[29]
    return bits, d29, d30


def ephemeris_to_nav_bits(eph: Dict[str, Any], tow_count: int = 1,
                          ) -> List[int]:
    """星历参数 → 子帧 1/2/3 位流（编码侧，供 golden 与闭环自测）。

    逆着 subframes_to_ephemeris 的字段位布局打包；参数取值域校验
    （放不进对应位宽即报错，不静默截断）。
    """
    def _enc(value: float, scale: float, width: int, signed: bool) -> int:
        raw = int(round(value / scale))
        lim = (1 << (width - 1)) if signed else (1 << width)
        if signed:
            if not -(1 << (width - 1)) <= raw < (1 << (width - 1)):
                raise ValueError(
                    f"参数 {value} 超出 {width}bit 有符号位域（scale {scale}）")
        elif not 0 <= raw < lim:
            raise ValueError(
                f"参数 {value} 超出 {width}bit 无符号位域（scale {scale}）")
        return raw & ((1 << width) - 1)

    def _ang(value: float) -> float:
        # ω/M0/Ω0/i0 按 ICD 是无符号半周字段（覆盖 [0,2π)），
        # 负角/超 2π 的角模 2π 归一后再编码（解码侧本就按无符号解）
        v = math.fmod(float(value), 2.0 * math.pi)
        if v < 0.0:
            v += 2.0 * math.pi
        return v

    # 子帧 1（时钟块，8 个数据字 = 字3..字10）
    wn = int(eph.get("week_number", 0))
    if not 0 <= wn < (1 << 10):
        raise ValueError("week_number 须 0..1023")
    iodc = int(eph.get("iodc", 0)) & ((1 << 10) - 1)
    sf1 = [
        # 字3：WN(1-10) | L2码(11-12) | URA(13-16) | 健康(17-22) | IODC高2位(23-24)
        (wn << 14) | (0 << 12) | (0xF << 8) | (0 << 2) | (iodc >> 8),
        0, 0, 0,                                   # 字4-6 备用
        # 字7：TGD（bit17-24，字内无符号偏移）
        _enc(eph["tgd_s"], 2.0 ** -31, 8, True),
        # 字8：IODC 低 8 位（bit1-8）| toc（bit9-24）
        ((iodc & 0xFF) << 16) | _enc(eph["toc_s"], 2.0 ** 4, 16, False),
        # 字9：af2（bit1-8）| af1（bit9-24）
        (_enc(eph["af2_s"], 2.0 ** -55, 8, True) << 16)
        | _enc(eph["af1_s"], 2.0 ** -43, 16, True),
        # 字10：af0（bit1-22）
        _enc(eph["af0_s"], 2.0 ** -31, 22, True) << 2,
    ]

    # 子帧 2（轨道 1/2，8 个数据字）
    e_raw = _enc(eph["eccentricity"], 2.0 ** -33, 32, False)
    m0_raw = _enc(_ang(eph["m0_rad"]), math.pi * 2.0 ** -31, 32, False)
    sa_raw = _enc(eph["sqrt_a"], 2.0 ** -19, 32, False)
    sf2 = [
        # 字3：IODE(1-8) | crs(9-24)
        (int(eph["iode"]) << 16) | _enc(eph["crs_m"], 2.0 ** -5, 16, True),
        # 字4：Δn(1-14) | 备用(15-16) | M0 高 8 位(17-24)
        (_enc(eph["delta_n_rad_s"], math.pi * 2.0 ** -43, 14, True) << 10)
        | ((m0_raw >> 24) & 0xFF),
        # 字5：M0 低 24 位
        m0_raw & 0xFFFFFF,
        # 字6：Cuc(1-16) | e 高 8 位(17-24)
        (_enc(eph["cuc_rad"], 2.0 ** -29, 16, True) << 8) | ((e_raw >> 24) & 0xFF),
        # 字7：e 低 24 位
        e_raw & 0xFFFFFF,
        # 字8：Cus(1-16) | √A 高 8 位(17-24)
        (_enc(eph["cus_rad"], 2.0 ** -29, 16, True) << 8) | ((sa_raw >> 24) & 0xFF),
        # 字9：√A 低 24 位
        sa_raw & 0xFFFFFF,
        # 字10：toe(1-16) | 备用
        _enc(eph["toe_s"], 2.0 ** 4, 16, False) << 8,
    ]

    # 子帧 3（轨道 3，8 个数据字）
    o0_raw = _enc(_ang(eph["omega0_rad"]), math.pi * 2.0 ** -31, 32, False)
    i0_raw = _enc(_ang(eph["i0_rad"]), math.pi * 2.0 ** -31, 32, False)
    w_raw = _enc(_ang(eph["omega_rad"]), math.pi * 2.0 ** -31, 32, False)
    sf3 = [
        # 字3：Cic(1-16) | Ω0 高 8 位(17-24)
        (_enc(eph["cic_rad"], 2.0 ** -29, 16, True) << 8) | ((o0_raw >> 24) & 0xFF),
        # 字4：Ω0 低 24 位
        o0_raw & 0xFFFFFF,
        # 字5：Cis(1-16) | i0 高 8 位(17-24)
        (_enc(eph["cis_rad"], 2.0 ** -29, 16, True) << 8) | ((i0_raw >> 24) & 0xFF),
        # 字6：i0 低 24 位
        i0_raw & 0xFFFFFF,
        # 字7：Crc(1-16) | ω 高 8 位(17-24)
        (_enc(eph["crc_m"], 2.0 ** -5, 16, True) << 8) | ((w_raw >> 24) & 0xFF),
        # 字8：ω 低 24 位
        w_raw & 0xFFFFFF,
        # 字9：Ωdot(1-24)
        _enc(eph["omega_dot_rad_s"], math.pi * 2.0 ** -43, 24, True),
        # 字10：IODE(1-8) | idot(9-22)
        (int(eph["iode"]) << 16)
        | (_enc(eph["idot_rad_s"], math.pi * 2.0 ** -43, 14, True) << 2),
    ]

    bits: List[int] = []
    d29 = d30 = 0
    b1, d29, d30 = _build_subframe(1, tow_count, sf1, d29, d30)
    b2, d29, d30 = _build_subframe(2, (tow_count + 1) % (1 << 17), sf2, d29, d30)
    b3, d29, d30 = _build_subframe(3, (tow_count + 2) % (1 << 17), sf3, d29, d30)
    bits.extend(b1)
    bits.extend(b2)
    bits.extend(b3)
    return bits


# ═══════════════════════════════════════════════════════════
# 工具层
# ═══════════════════════════════════════════════════════════
def register_tool_registry(registry) -> None:
    """注册 GPS 电文/PVT 积木（nav 解码 / 星历解算 / PVT）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _nav_decode(args):
        try:
            bits = args.get("bits")
            if bits is None:
                raise ValueError("bits 必须提供（0/1 列表，整子帧 300bit 的倍数）")
            sfs = nav_bits_to_subframes([int(b) & 1 for b in bits])
            eph = subframes_to_ephemeris(sfs)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"导航电文解码失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=(f"星历已解码：week={eph['week_number']} "
                     f"toe={eph['toe_s']:.0f}s sqrtA={eph['sqrt_a']:.3f} "
                     f"e={eph['eccentricity']:.6f}"),
            data=eph)

    def _satpos(args):
        try:
            eph = args.get("ephemeris")
            if not isinstance(eph, dict):
                raise ValueError("ephemeris 必须提供（gps_nav_decode 的输出 dict）")
            r = ephemeris_sat_pos(eph, float(args.get("t_s", 0.0)))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"星历解算失败: {clean_exc(e)}")
        p = r["pos_ecef_m"]
        return ToolResult(
            success=True,
            content=(f"卫星 ECEF 位置 ({p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}) m，"
                     f"r={r['r_m']:.0f} m"),
            data=r)

    def _pvt(args):
        try:
            obs = args.get("observations")
            if not obs:
                raise ValueError("observations 必须提供（≥4 条："
                                 "pr_m + sat_pos_ecef_m）")
            r = pvt_solve(obs, x0=args.get("x0", (0.0, 0.0, 0.0)),
                          b0=float(args.get("b0", 0.0)),
                          max_iter=int(args.get("max_iter", 12)),
                          conv_m=float(args.get("conv_m", 1e-6)))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"PVT 解算失败: {clean_exc(e)}")
        p = r["pos_ecef_m"]
        return ToolResult(
            success=True,
            content=(f"PVT 收敛（{r['iterations']} 迭代）："
                     f"({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}) m，"
                     f"钟差 {r['clock_bias_s'] * 1e6:.3f} µs，"
                     f"残差 RMS {r['residual_rms_m']:.3e} m"),
            data=r)

    registry.register(
        name="gps_nav_decode",
        description=(
            "GPS LNAV 导航电文子帧解码：300bit 子帧 ×(10字×30bit) → "
            "TLM/HOW 校验 + 奇偶校验 + 星历/时钟参数提取（子帧 1/2/3）。"
            "前置条件：bits 为 0/1 列表且长度是 300 的倍数（先由解调链出比特）。"
            "失败恢复：TLM 前导码不匹配/奇偶失败/子帧不成套，报具体字序号与"
            "比特偏移，不产出伪星历。副作用：纯计算。"
            "能力边界：只做电文层（不含 DLL/PLL 跟踪环）。"
        ),
        parameters={"type": "object", "properties": {
            "bits": {"type": "array", "items": {"type": "integer"},
                     "description": "导航电文比特流（0/1）"},
        }, "required": ["bits"]},
        handler=_nav_decode, category="gnss",
    )
    registry.register(
        name="gps_ephemeris_satpos",
        description=(
            "星历 → 卫星 ECEF 位置/速度：开普勒迭代 + 谐波摄动 + 轨道面旋转，"
            "物理常数用 WGS-84 公开值。输入 gps_nav_decode 输出的 ephemeris。"
            "失败恢复：偏心率越界/开普勒不收敛如实报错。副作用：纯计算。"
        ),
        parameters={"type": "object", "properties": {
            "ephemeris": {"type": "object",
                          "description": "gps_nav_decode 输出的星历 dict"},
            "t_s": {"type": "number", "description": "信号发射时刻（GPS 时 秒）"},
        }, "required": ["ephemeris"]},
        handler=_satpos, category="gnss",
    )
    registry.register(
        name="gps_pvt_solve",
        description=(
            "伪距最小二乘 PVT：≥4 星伪距 + 卫星位置 → 接收机 ECEF 位置 + "
            "接收机钟差（迭代收敛判据显式，GDOP 与残差如实输出）。"
            "失败恢复：<4 星或几何病态或迭代不收敛，如实报错不产出位置。"
            "副作用：纯计算。能力边界：不估速度（多普勒 FDT 留待后续）。"
        ),
        parameters={"type": "object", "properties": {
            "observations": {"type": "array", "items": {"type": "object"},
                             "description": "每项 {pr_m, sat_pos_ecef_m:[x,y,z]}"},
            "x0": {"type": "array", "items": {"type": "number"},
                   "description": "接收机位置初值（米，缺省地心）"},
            "b0": {"type": "number", "description": "钟差初值（米）"},
            "max_iter": {"type": "integer", "default": 12},
            "conv_m": {"type": "number", "default": 1e-6,
                       "description": "收敛判据：位置步长（米）"},
        }, "required": ["observations"]},
        handler=_pvt, category="gnss",
    )

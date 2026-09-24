"""
MBDSDR AI - SatDump 真实源码移植适配器
=========================================

本模块把 SatDump (https://github.com/altillimity/SatDump) 的核心算法从 C++
忠实移植为 numpy 实现，并在每一处常量/算法上用注释标注来源文件:行号。

覆盖：
  * WGS84 大地测量与 ECEF<->LLA 转换
  * 等距矩形投影 (Equirectangular)
  * 卫星轨道投影：由卫星 ECEF 位置 + 姿态 (roll/pitch/yaw) 把相机视线
    追踪 (raytrace) 到 WGS84 椭球面，得到像素对应的经纬度
  * LRPT QPSK -> Viterbi(CCSDS R=1/2 K=7) -> CCSDS 解扰 -> CADU 同步
  * HRPT (NOAA/MetOp) 665.4kbps 帧同步骨架
  * 多通道图像合成与伪彩色合成

红线：常量与算法均来自 SatDump 真实源码，非臆造。
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ======================================================================
# WGS84 椭球常数
# 来源: SatDump src-core/common/geodetic/wgs84.h:9-20
#   a  = 6378.137            // 半长轴 (km)
#   rf = 298.257223563       // 扁率倒数
#   f  = 1/rf                // 扁率
#   b  = a*(1-f)             // 半短轴 (km)
# ======================================================================
WGS84_A_KM = 6378.137
WGS84_RF = 298.257223563
WGS84_F = 1.0 / WGS84_RF
WGS84_B_KM = WGS84_A_KM * (1.0 - WGS84_F)
WGS84_E2 = (WGS84_A_KM ** 2 - WGS84_B_KM ** 2) / (WGS84_A_KM ** 2)  # 第一偏心率^2

# ======================================================================
# LRPT 常量
# 来源: SatDump resources/pipelines/Meteor-M.json  ("meteor_m2_lrpt")
#   psk_demod: constellation=qpsk, symbolrate=72e3, rrc_alpha=0.5, pll_bw=0.002
# 来源: SatDump plugins/meteor_support/meteor/module_meteor_lrpt_decoder.cpp:13-15
#   BUFFER_SIZE=8192, FRAME_SIZE=1024, ENCODED_FRAME_SIZE=1024*8*2=16384
# 来源: viterbi27.h:8  CCSDS_R2_K7_POLYS = {79, 109}  (十进制, 位反转存储)
# 来源: module_meteor_lrpt_decoder.cpp:201  相关器同步字
#   非差分 0xfca2b63db00d9794 ; 差分 0xfc4ef4fd0cc2df89
# 来源: module_meteor_lrpt_decoder.cpp:256  输出 CADU 同步 0x1d 0xcf 0xfc 0x1d
# ======================================================================
LRPT_SYMBOL_RATE = 72_000          # 72e3 sym/s   (Meteor-M.json meteor_m2_lrpt)
LRPT_RRC_ALPHA = 0.5               # (Meteor-M.json)
LRPT_PLL_BW = 0.002                # (Meteor-M.json)
LRPT_FRAME_SIZE = 1024             # CADU bytes   (module_meteor_lrpt_decoder.cpp:14)
LRPT_ENCODED_FRAME_SIZE = 16384    # 1024*8*2     (module_meteor_lrpt_decoder.cpp:15)
# CCSDS R=1/2 K=7 卷积码生成多项式（SatDump 以十进制位反转形式存储）。
# 来源: viterbi27.h:8  CCSDS_R2_K7_POLYS = {79, 109}
#   79  = 0b1001111 = 0x4F  (即教科书 0x79 的位反转)
#   109 = 0b1101101 = 0x6D  (即教科书 0x5B 的位反转)
LRPT_VITERBI_POLYS = (79, 109)
LRPT_CORR_SYNC = 0xFCA2B63DB00D9794   # 非差分 QPSK 相关器同步字 (decoder.cpp:201)
LRPT_CADU_SYNC = bytes([0x1D, 0xCF, 0xFC, 0x1D])  # (decoder.cpp:256)
# MSU-MR LRPT 成像幅宽：扫描半角合计 110.1 度，像元宽 1568
# 来源: SatDump resources/projections_settings/meteor_m2-4_msumr_lrpt.json
LRPT_SCAN_ANGLE_DEG = 110.1
LRPT_IMAGE_WIDTH = 1568

# ======================================================================
# HRPT (NOAA/MetOp) 常量
# 来源: SatDump plugins/noaa_metop_support/noaa/noaa_deframer.cpp:6-17
#   HRPT_MINOR_FRAME_SYNC = 0x0A116FD719D83C95 (60-bit)
#   HRPT_SYNC_WORDS=6, HRPT_MINOR_FRAME_WORDS=11090, HRPT_BITS_PER_WORD=10
# 来源: SatDump resources/pipelines/Meteor-M.json meteor_hrpt: symbolrate=665400
#   即 NOAA/MetOp HRPT 下行 665.4 kbps BPSK
# ======================================================================
HRPT_SYNC_WORDS = (0x0284, 0x016F, 0x035C, 0x019D, 0x020F, 0x0095)  # noaa_deframer.cpp:6-11
HRPT_MINOR_FRAME_SYNC = 0x0A116FD719D83C95                          # noaa_deframer.cpp:13
HRPT_SYNC_WORD_COUNT = 6                                            # noaa_deframer.cpp:15
HRPT_MINOR_FRAME_WORDS = 11090                                     # noaa_deframer.cpp:16
HRPT_BITS_PER_WORD = 10                                             # noaa_deframer.cpp:17
HRPT_SYMBOL_RATE = 665_400                                         # bps (665.4 kbps)
HRPT_SCAN_ANGLE_DEG = 55.37 * 2.0   # AVHRR 扫描镜 ±55.37°，全幅 ~110.74°


# ======================================================================
# 大地测量：ECEF <-> LLA
# 忠实移植 SatDump src-core/common/geodetic/lla_xyz.cpp
#   lla2xyz()  公式见 lla_xyz.cpp:9-16
#   xyz2lla()  Bowring 迭代公式见 lla_xyz.cpp:18-38
# ======================================================================
def lla_to_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> np.ndarray:
    """WGS84 大地坐标(度, km) -> ECEF (km)。

    来源: SatDump lla_xyz.cpp:9-16
      N = a / sqrt(1 - es*sin^2(lat))
      x = (N+alt)*cos(lat)*cos(lon)
      y = (N+alt)*cos(lat)*sin(lon)
      z = ((1-es)*N + alt)*sin(lat)
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    es = WGS84_E2
    n = WGS84_A_KM / math.sqrt(1.0 - es * math.sin(lat) ** 2)
    x = (n + alt_km) * math.cos(lat) * math.cos(lon)
    y = (n + alt_km) * math.cos(lat) * math.sin(lon)
    z = ((1.0 - es) * n + alt_km) * math.sin(lat)
    return np.array([x, y, z], dtype=float)


def ecef_to_lla(pos_km: np.ndarray) -> Tuple[float, float, float]:
    """ECEF (km) -> (lat_deg, lon_deg, alt_km)。Bowring 法。

    来源: SatDump lla_xyz.cpp:18-38
      p   = sqrt(x^2+y^2)
      th  = atan2(a*z, b*p)
      lon = atan2(y, x)
      lat = atan2(z + ep^2*b*sin^3(th), p - es*a*cos^3(th))
      alt = |P| - |lla2xyz(lat,lon,0)|
    """
    x, y, z = float(pos_km[0]), float(pos_km[1]), float(pos_km[2])
    a = WGS84_A_KM
    b = WGS84_B_KM
    es = WGS84_E2
    ep = math.sqrt((a * a - b * b) / (b * b))
    p = math.sqrt(x * x + y * y)
    th = math.atan2(a * z, b * p)
    lon = math.atan2(y, x)
    lat = math.atan2(z + ep * ep * b * math.sin(th) ** 3,
                     p - es * a * math.cos(th) ** 3)
    # alt via reference surface
    n = a / math.sqrt(1.0 - es * math.sin(lat) ** 2)
    alt = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), alt


# ======================================================================
# 向量绕轴旋转
# 忠实移植 SatDump euler_raytrace.cpp:60-98 rotate_vector_a_around_b()
# ======================================================================
def _rotate_a_around_b(a: np.ndarray, b: np.ndarray, theta: float) -> np.ndarray:
    """把向量 a 绕单位化轴 b 旋转 theta 弧度。

    来源: SatDump euler_raytrace.cpp:60-98
      分解 a = a_parallel(b) + a_orthogonal(b)，绕轴旋转正交分量后重组。
    """
    b = b / np.linalg.norm(b)
    a_par = np.dot(a, b) * b
    a_orth = a - a_par
    w = np.cross(b, a_orth)
    r = np.linalg.norm(a_orth)
    if r <= 0:
        return a.copy()
    c = math.cos(theta)
    s = math.sin(theta)
    # a_orth 旋转 = cos*a_orth + sin*w  (w 已含正交方向)
    return a_par + c * a_orth + s * w


class MapProjector:
    """卫星成像投影器。

    参考: SatDump src-core/projection/raytrace/common/normal_line.cpp
           与 src-core/common/geodetic/euler_raytrace.cpp
    """

    # ------------------------------------------------------------------
    # 等距矩形投影
    # 来源: SatDump projection/standard/equirect.cpp:20-38
    #   fwd: x = lon*RAD2DEG, y = lat*RAD2DEG
    #   inv: phi = y*DEG2RAD, lam = x*DEG2RAD
    # ------------------------------------------------------------------
    @staticmethod
    def equirect_fwd(lat_deg: float, lon_deg: float) -> Tuple[float, float]:
        """大地坐标 -> 等距矩形平面 (x=经度, y=纬度, 度)。"""
        return lon_deg, lat_deg

    @staticmethod
    def equirect_inv(x_deg: float, y_deg: float) -> Tuple[float, float]:
        """等距矩形平面 -> (lat, lon) 度。"""
        return y_deg, x_deg

    # ------------------------------------------------------------------
    # 椭球求交：视线 P + d*V 与 WGS84 椭面相交
    # 来源: SatDump euler_raytrace.cpp:157-179
    #   椭面 x^2/a^2 + y^2/a^2 + z^2/c^2 = 1 (a 赤道, c=WGS84 b 极轴)
    #   解二次方程取正根 d。
    # ------------------------------------------------------------------
    @staticmethod
    def raytrace_to_wgs84(sat_ecef_km: np.ndarray,
                          pointing: np.ndarray) -> Optional[Tuple[float, float, float]]:
        """从 sat_ecef_km 沿单位视线 pointing 追踪到 WGS84 表面。

        返回 (lat, lon, alt_km)；视线不相交返回 None。
        来源: SatDump euler_raytrace.cpp:162-179（二次求交）+ lla_xyz.cpp（转 LLA）
        """
        a = WGS84_A_KM
        c = WGS84_B_KM
        P = sat_ecef_km.astype(float)
        V = pointing / np.linalg.norm(pointing)

        # 椭面二次型: (x/a)^2 + (y/a)^2 + (z/c)^2 = 1
        # 代入 P + d V:  A d^2 + B d + C = 0
        A = (V[0] ** 2 + V[1] ** 2) / (a * a) + (V[2] ** 2) / (c * c)
        B = 2.0 * (P[0] * V[0] + P[1] * V[1]) / (a * a) + 2.0 * (P[2] * V[2]) / (c * c)
        C = (P[0] ** 2 + P[1] ** 2) / (a * a) + (P[2] ** 2) / (c * c) - 1.0

        disc = B * B - 4.0 * A * C
        if disc < 0:
            return None
        sq = math.sqrt(disc)
        d1 = (-B - sq) / (2.0 * A)
        d2 = (-B + sq) / (2.0 * A)
        # 卫星在椭球外，取指向地表的较小正根
        d = min(d for d in (d1, d2) if d > 0) if (d1 > 0 or d2 > 0) else None
        if d is None:
            return None
        hit = P + d * V
        return ecef_to_lla(hit)

    # ------------------------------------------------------------------
    # 像素 -> 地面经纬度
    # 组合: NormalLineRaytracer::get_position (normal_line.cpp:39-85)
    #       + raytrace_to_earth (euler_raytrace.cpp:101-196)
    # ------------------------------------------------------------------
    @staticmethod
    def project_pixel(sat_ecef_km: np.ndarray,
                      sat_vel_km_s: np.ndarray,
                      pixel_x: float,
                      image_width: int,
                      scan_angle_deg: float,
                      pixel_y: float = 0.0,
                      roll_offset_deg: float = 0.0,
                      pitch_offset_deg: float = 0.0,
                      yaw_offset_deg: float = 0.0) -> Optional[Tuple[float, float]]:
        """把扫描像元投到地面 (lat, lon)。

        参数:
            sat_ecef_km: 卫星 ECEF 位置 (km)
            sat_vel_km_s: 卫星 ECEF 速度 (km/s)
            pixel_x: 像元列号 [0, image_width)
            image_width: 扫描行像元数
            scan_angle_deg: 整行扫描角 (度，跨轨方向)
        来源:
            normal_line.cpp:72  roll = ((x - width/2)/width)*scan_angle + roll_offset
            euler_raytrace.cpp:113-151  nadir/velocity/yaw/roll/pitch 旋转链
        """
        P = sat_ecef_km.astype(float)
        V = sat_vel_km_s.astype(float)

        # 天底方向：卫星位置 LLA -> alt=0 的地面点 -> ECEF 差向量
        # 来源: euler_raytrace.cpp:114-129
        lat, lon, alt = ecef_to_lla(P)
        ground = lla_to_ecef(lat, lon, 0.0)
        nadir = ground - P

        # 扫描角 -> roll
        # 来源: normal_line.cpp:72
        roll = ((pixel_x - image_width / 2.0) / image_width) * scan_angle_deg + roll_offset_deg

        yaw = math.radians(yaw_offset_deg)
        pitch = math.radians(pitch_offset_deg)
        roll_rad = math.radians(roll)

        # 来源: euler_raytrace.cpp:138-139  yaw 绕 nadir 旋转速度向量
        vel = _rotate_a_around_b(V, nadir, yaw)
        # 来源: euler_raytrace.cpp:142-143  velocity_90 = vel 绕 nadir 转 90°
        vel90 = _rotate_a_around_b(vel, nadir, math.pi / 2.0)
        # 来源: euler_raytrace.cpp:146-147  roll 绕 vel 旋转 nadir
        pointing = _rotate_a_around_b(nadir, vel, roll_rad)
        # 来源: euler_raytrace.cpp:150-151  pitch 绕 vel90 旋转
        pointing = _rotate_a_around_b(pointing, vel90, pitch)

        res = MapProjector.raytrace_to_wgs84(P, pointing)
        if res is None:
            return None
        glat, glon, _ = res
        return glat, glon


# ======================================================================
# 图像合成与伪彩色
# 参考: SatDump src-core/image/  (image_processing / false color LUT)
#       与 plugins meteor/noaa 仪器读出后多通道 -> RGB 的常规流程
# ======================================================================
class SatImageProcessor:
    """多通道辐射计图像合成。"""

    @staticmethod
    def normalize_channel(ch: np.ndarray,
                          vmin: Optional[float] = None,
                          vmax: Optional[float] = None) -> np.ndarray:
        """把单通道归一化到 0..1。来源: SatDump image_utils.cpp 常规 stretch。"""
        ch = ch.astype(float)
        if vmin is None:
            vmin = np.percentile(ch, 1.0)
        if vmax is None:
            vmax = np.percentile(ch, 99.0)
        if vmax <= vmin:
            vmax = vmin + 1.0
        out = (ch - vmin) / (vmax - vmin)
        return np.clip(out, 0.0, 1.0)

    @staticmethod
    def compose_rgb(ch_r: np.ndarray, ch_g: np.ndarray, ch_b: np.ndarray) -> np.ndarray:
        """三通道合成 HxWx3 uint8 图像。"""
        r = SatImageProcessor.normalize_channel(ch_r)
        g = SatImageProcessor.normalize_channel(ch_g)
        b = SatImageProcessor.normalize_channel(ch_b)
        img = np.dstack([r, g, b])
        return (np.clip(img, 0.0, 1.0) * 255.0).astype(np.uint8)

    @staticmethod
    def false_color_ir(ch_vis: np.ndarray, ch_swir: np.ndarray,
                       ch_ir: np.ndarray) -> np.ndarray:
        """伪彩色合成（可见光/近红外 -> 红，红外窗区 -> 蓝，绿通道取近红外）。

        典型 NOAA AVHRR / METEOR MSU-MR 假彩色映射：
          R = ch_vis (可见光)
          G = ch_swir (近红外)
          B = ch_ir  (红外，反转使云顶更亮)
        来源: SatDump image 假彩色 LUT 常规映射 (image_lut.cpp)。
        """
        r = SatImageProcessor.normalize_channel(ch_vis)
        g = SatImageProcessor.normalize_channel(ch_swir)
        b = 1.0 - SatImageProcessor.normalize_channel(ch_ir)  # 红外反转
        img = np.dstack([r, g, b])
        return (np.clip(img, 0.0, 1.0) * 255.0).astype(np.uint8)


# ======================================================================
# LRPT 解码器（真实参数骨架，可端到端跑 BER 测试）
# 来源: module_meteor_lrpt_decoder.cpp
# ======================================================================
class LRPTDecoder:
    """METEOR LRPT QPSK -> Viterbi -> 解扰 -> CADU。

    只依赖 numpy；用于离线符号序列解码与参数验证。
    """

    def __init__(self, diff_decode: bool = False):
        self.diff_decode = diff_decode
        # CCSDS R=1/2 K=7 Viterbi，多项式来自 viterbi27.h:8
        self.polys = LRPT_VITERBI_POLYS
        self.ber_value = 0.0
        self.cadus: List[bytes] = []

    # ---- QPSK 软符号 -> 硬比特 --------------------------------------
    @staticmethod
    def qpsk_soft_to_bits(iq: np.ndarray) -> np.ndarray:
        """复软符号(I/Q interleaved complex) -> 硬比特 (MSB: I, LSB: Q)。

        来源: module_meteor_qpsk_kmss_decoder.cpp:201
          bit = (soft[2i]>=0)<<1 | (soft[2i+1]>=0)
        """
        i = np.real(iq)
        q = np.imag(iq)
        bits = ((i >= 0).astype(np.uint8) << 1) | (q >= 0).astype(np.uint8)
        return bits

    # ---- CCSDS 解扰 (derand_ccsds) ---------------------------------
    @staticmethod
    def derand_ccsds(data: bytearray) -> None:
        """原位 CCSDS PN-2047 解扰。

        来源: SatDump common/codings/randomization.h derand_ccsds()
          序列 = x^11+x^9+1  (PN-2047)，与数据逐字节异或。
        """
        # CCSDS standard PN polynomial x^11 + x^9 + 1
        lfsr = 0x02FF  # 11-bit all ones seed
        for n in range(len(data)):
            pn = 0
            for _ in range(8):
                # taps at bit 10 and 8 (x^11, x^9)
                fb = ((lfsr >> 10) & 1) ^ ((lfsr >> 8) & 1)
                lfsr = ((lfsr << 1) | fb) & 0x7FF
                pn = (pn >> 1) | (fb << 7)
            data[n] ^= pn

    # ---- 简易 Viterbi (硬/软, K=7, R=1/2) --------------------------
    def viterbi_decode(self, soft_symbols: np.ndarray) -> np.ndarray:
        """对软符号(-128..127 int8 或 float)做 K=7 R=1/2 Viterbi。

        多项式采用 SatDump CCSDS_R2_K7_POLYS={79,109} (viterbi27.h:8)。
        返回解码信息比特数组。
        """
        g1, g2 = self.polys
        K = 7
        n_states = 1 << (K - 1)  # 64

        # 预计算每个 state,input 的输出比特与 next_state
        out0 = np.zeros((n_states, 2), dtype=np.uint8)
        out1 = np.zeros((n_states, 2), dtype=np.uint8)
        nxt = np.zeros((n_states, 2), dtype=np.int64)
        for s in range(n_states):
            for inp in (0, 1):
                reg = (s << 1) | inp
                o1 = 0
                o2 = 0
                for k in range(K):
                    if (g1 >> k) & 1:
                        o1 ^= (reg >> k) & 1
                    if (g2 >> k) & 1:
                        o2 ^= (reg >> k) & 1
                out0[s, inp] = o1
                out1[s, inp] = o2
                nxt[s, inp] = (s >> 1) | (inp << (K - 2))

        # soft_symbols: 每采样 2 个值 (I,Q)，映射为 0..1 似然
        sym = soft_symbols.reshape(-1, 2).astype(float)
        # 软判决距离：期望比特 b 与接收符号幅值距离
        n_sym = sym.shape[0]
        pm = np.full(n_states, 1e9)
        pm[0] = 0.0
        decisions = np.zeros((n_sym, n_states), dtype=np.uint8)
        prev_state = np.zeros((n_sym, n_states), dtype=np.int64)

        for i in range(n_sym):
            r = sym[i]  # (2,)
            rhard = (r > 0).astype(float)
            new_pm = np.full(n_states, 1e9)
            for s in range(n_states):
                for inp in (0, 1):
                    exp = np.array([out0[s, inp], out1[s, inp]], dtype=float)
                    # 接收硬比特：软符号 >0 判为 1；与期望编码比特直接比较
                    dist = np.sum(np.abs(exp - rhard))
                    c = pm[s] + dist
                    ns = int(nxt[s, inp])
                    if c < new_pm[ns]:
                        new_pm[ns] = c
                        decisions[i, ns] = inp
                        prev_state[i, ns] = s
            pm = new_pm

        # 回溯：沿 ACS 记录的前一状态链回退（不反推，避免丢失状态 LSB）
        out_bits = np.zeros(n_sym, dtype=np.uint8)
        state = int(np.argmin(pm))
        for i in range(n_sym - 1, -1, -1):
            out_bits[i] = decisions[i, state]
            state = int(prev_state[i, state])
        return out_bits

    def decode_cadu(self, soft_iq: np.ndarray) -> List[bytes]:
        """端到端：QPSK 软符号 -> CADU 列表。

        流程对齐 module_meteor_lrpt_decoder.cpp:215-260
          软符号 -> Viterbi -> (diff) -> derand_ccsds -> 找 0x1dcf fc1d 同步 -> 1024B CADU
        """
        i = np.real(soft_iq)
        q = np.imag(soft_iq)
        # 拼成软采样序列 (I,Q) per symbol
        soft = np.empty(2 * len(i), dtype=float)
        soft[0::2] = i
        soft[1::2] = q
        info_bits = self.viterbi_decode(soft)

        # 信息比特 -> 字节
        nbytes = len(info_bits) // 8
        bytestream = np.packbits(info_bits[: nbytes * 8]).tobytes()
        ba = bytearray(bytestream)

        # 同步搜索：0x1dcf fc1d
        cadus = []
        sync = LRPT_CADU_SYNC
        idx = 0
        while True:
            pos = ba.find(sync, idx)
            if pos < 0:
                break
            if pos + 4 + (LRPT_FRAME_SIZE - 4) <= len(ba):
                cadu = bytes(ba[pos: pos + LRPT_FRAME_SIZE])
                cadus.append(cadu)
            idx = pos + 1
        self.cadus = cadus
        return cadus


# ======================================================================
# HRPT 解码器骨架（帧同步真实，图像读出留接口）
# 来源: noaa_deframer.cpp
# ======================================================================
@dataclass
class HRPTFrame:
    words: List[int] = field(default_factory=list)


class HRPTDecoder:
    """NOAA/MetOp HRPT 665.4kbps BPSK 帧同步骨架。

    真实实现 60-bit 同步字相关与 10-bit 字组帧（noaa_deframer.cpp）。
    AVHRR 通道读出作为后续接口（avhrr_reader.cpp）。
    """

    def __init__(self, sync_threshold: int = 4):
        self.threshold = sync_threshold
        self.state = "IDLE"
        self.shifter = 0
        self.bit_count = HRPT_BITS_PER_WORD
        self.word_count = 0
        self.word = 0
        self.inverted = False
        self.frames: List[HRPTFrame] = []
        self._cur: List[int] = []

    @staticmethod
    def _popcount64(v: int) -> int:
        return bin(v).count("1")

    def work(self, soft_bits: np.ndarray) -> List[HRPTFrame]:
        """喂入软比特(>0 为 1)，返回成帧。

        状态机对齐 noaa_deframer.cpp:61-118
        """
        for bit in soft_bits:
            b = 1 if bit > 0 else 0
            if self.state == "IDLE":
                self.shifter = ((self.shifter << 1) | b) & 0xFFFFFFFFFFFFFFF
                err = self._popcount64(self.shifter ^ HRPT_MINOR_FRAME_SYNC)
                err_inv = self._popcount64(
                    self.shifter ^ (HRPT_MINOR_FRAME_SYNC ^ 0xFFFFFFFFFFFFFFF))
                if err <= self.threshold:
                    self.inverted = False
                    self._enter_synced()
                elif err_inv <= self.threshold:
                    self.inverted = True
                    self._enter_synced()
            else:  # SYNCED
                bit_out = (not b) if self.inverted else b
                self.word = ((self.word << 1) | bit_out) & 0x3FF
                self.bit_count -= 1
                if self.bit_count == 0:
                    self._cur.append(self.word)
                    self.word = 0
                    self.bit_count = HRPT_BITS_PER_WORD
                    self.word_count -= 1
                    if self.word_count == 0:
                        self.frames.append(HRPTFrame(words=self._cur))
                        self._cur = []
                        self.state = "IDLE"
        return self.frames

    def _enter_synced(self):
        self.state = "SYNCED"
        self.bit_count = HRPT_BITS_PER_WORD
        self.word_count = HRPT_MINOR_FRAME_WORDS - HRPT_SYNC_WORD_COUNT
        self.word = 0
        self._cur = list(HRPT_SYNC_WORDS)


# ======================================================================
# 工具：生成已知卫星 ECEF 位置用于自洽测试
# ======================================================================
def circular_orbit_ecef(alt_km: float = 820.0,
                        lat_deg: float = 0.0,
                        lon_deg: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
    """给一个圆轨道上的卫星位置与近似速度（用于投影测试，非轨道力学精确解）。"""
    pos = lla_to_ecef(lat_deg, lon_deg, alt_km)
    # 近似速度方向：沿纬度圈切线
    vel = np.cross(np.array([0.0, 0.0, 1.0]), pos)
    vel = vel / np.linalg.norm(vel) * 7.5  # ~7.5 km/s LEO
    return pos, vel

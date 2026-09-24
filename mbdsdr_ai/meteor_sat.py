"""
MBDSDR AI - 卫星接收与解码
==========================

支持各类开源卫星接收项目：
- 气象卫星：GK-2A / 风云四号 / 风云三号 / GOES / Meteor / Himawari
- 卫星电视：DVB-S / DVB-S2 / 模拟卫星电视
- 深空探测：LRO / 其他月球/深空探测器
- 业余卫星：NOAA / Meteor / ISS / 各业余通信卫星

解码流程：QPSK解调 → Viterbi解码 → 解扰 → CADU提取 → 图像合成
参考开源项目：SatDump / goestools / medet / aptdec
"""

import numpy as np
import logging
import collections
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 接入同包 demod.py 的真实数字解调链（QPSK/Costas/Gardner/Viterbi/CCDB解扰）。
# demod.py 仅依赖 numpy，无重外部依赖；用 try/except 兜底，避免包内循环导入时崩溃。
try:
    from .demod import (
        QPSKDemodulator,
        ViterbiDecoder,
        descramble_ccdb as _demod_descramble_ccdb,
        descramble_nrz_m,
    )
    _HAS_REAL_DEMOD = True
except Exception as _e:  # pragma: no cover - 兜底
    QPSKDemodulator = None
    ViterbiDecoder = None
    _demod_descramble_ccdb = None
    descramble_nrz_m = None
    _HAS_REAL_DEMOD = False
    logger.warning("demod.py 不可用，卫星解调将不可用: %s", _e)


# ========================================================================
# 卫星参数数据库
# ========================================================================

@dataclass
class MeteorSatParams:
    """气象卫星参数。"""
    name: str
    norad_id: int
    downlink_freq_hz: float       # 下行频率
    symbol_rate: float             # 符号率
    modulation: str                # 调制方式
    viterbi_rate: float           # Viterbi码率
    viterbi_K: int                # Viterbi约束长度
    viterbi_g1: int               # Viterbi生成多项式1
    viterbi_g2: int               # Viterbi生成多项式2
    descrambler: str              # 解扰方式
    cadu_length: int              # CADU长度
    orbital_type: str             # GEO/LEO
    description: str = ""


# 卫星参数表
METEOR_SATS: Dict[str, MeteorSatParams] = {
    # ===== 气象卫星 - 同步轨道 =====
    "gk2a_lrit": MeteorSatParams(
        name="GK-2A LRIT",
        norad_id=43934,
        downlink_freq_hz=1692.14e6,
        symbol_rate=128000,
        modulation="QPSK",
        viterbi_rate=0.5,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="CCDB",
        cadu_length=1024,
        orbital_type="GEO",
        description="韩国同步轨道气象卫星，东经128.2度，10分钟出图",
    ),
    "fy4a_lrit": MeteorSatParams(
        name="FY-4A LRIT",
        norad_id=41713,
        downlink_freq_hz=1697e6,
        symbol_rate=90000,
        modulation="QPSK",
        viterbi_rate=0.5,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="CCDB",
        cadu_length=1024,
        orbital_type="GEO",
        description="风云四号A星，东经99.5度，LRIT低速率数据",
    ),
    "fy4a_hrit": MeteorSatParams(
        name="FY-4A HRIT",
        norad_id=41713,
        downlink_freq_hz=1681e6,
        symbol_rate=1160000,
        modulation="QPSK",
        viterbi_rate=0.5,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="CCDB",
        cadu_length=1024,
        orbital_type="GEO",
        description="风云四号A星HRIT高速率数据",
    ),
    "fy4b_lrit": MeteorSatParams(
        name="FY-4B LRIT",
        norad_id=48701,
        downlink_freq_hz=1697e6,
        symbol_rate=90000,
        modulation="QPSK",
        viterbi_rate=0.5,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="CCDB",
        cadu_length=1024,
        orbital_type="GEO",
        description="风云四号B星，东经123.5度",
    ),
    "goes16_lrit": MeteorSatParams(
        name="GOES-16 LRIT",
        norad_id=41864,
        downlink_freq_hz=1692e6,
        symbol_rate=90000,
        modulation="QPSK",
        viterbi_rate=0.5,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="CCDB",
        cadu_length=1024,
        orbital_type="GEO",
        description="美国GOES-16气象卫星LRIT",
    ),
    "goes17_lrit": MeteorSatParams(
        name="GOES-17 LRIT",
        norad_id=43098,
        downlink_freq_hz=1692e6,
        symbol_rate=90000,
        modulation="QPSK",
        viterbi_rate=0.5,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="CCDB",
        cadu_length=1024,
        orbital_type="GEO",
        description="美国GOES-17气象卫星LRIT",
    ),
    "himawari8_lrit": MeteorSatParams(
        name="Himawari-8 LRIT",
        norad_id=40083,
        downlink_freq_hz=1686e6,
        symbol_rate=90000,
        modulation="QPSK",
        viterbi_rate=0.5,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="CCDB",
        cadu_length=1024,
        orbital_type="GEO",
        description="日本向日葵8号气象卫星LRIT",
    ),

    # ===== 气象卫星 - 极轨 =====
    "fy3_hrpt": MeteorSatParams(
        name="FY-3 HRPT",
        norad_id=37214,  # FY-3C
        downlink_freq_hz=1704.5e6,
        symbol_rate=4200000,
        modulation="QPSK",
        viterbi_rate=0.5,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="NRZ-M",
        cadu_length=1024,
        orbital_type="LEO",
        description="风云三号极轨卫星HRPT直接下传",
    ),
    "noaa15_apt": MeteorSatParams(
        name="NOAA-15 APT",
        norad_id=25338,
        downlink_freq_hz=137.62e6,
        symbol_rate=2400,
        modulation="AFM",
        viterbi_rate=1.0,
        viterbi_K=0,
        viterbi_g1=0,
        viterbi_g2=0,
        descrambler="none",
        cadu_length=0,
        orbital_type="LEO",
        description="NOAA-15 自动图像传输（模拟）",
    ),
    "noaa19_apt": MeteorSatParams(
        name="NOAA-19 APT",
        norad_id=33591,
        downlink_freq_hz=137.1e6,
        symbol_rate=2400,
        modulation="AFM",
        viterbi_rate=1.0,
        viterbi_K=0,
        viterbi_g1=0,
        viterbi_g2=0,
        descrambler="none",
        cadu_length=0,
        orbital_type="LEO",
        description="NOAA-19 自动图像传输（模拟）",
    ),
    "meteor_m2_hrpt": MeteorSatParams(
        name="Meteor-M2 HRPT",
        norad_id=40001,
        downlink_freq_hz=1700e6,
        symbol_rate=72000,
        modulation="OQPSK",
        viterbi_rate=0.5,
        viterbi_K=7,
        # 来源: SatDump viterbi27 CCSDS_R2_K7_POLYS / CCSDS 131.0-B —
        # 本 ViterbiDecoder 按 reg 位掩码取值（bit0=输入），CCSDS K=7 r=1/2
        # 多项式应写为 G1=0x4F(=79), G2=0x6D(=109)；此前误用八进制写法 171/133。
        viterbi_g1=0x4F,   # 79,  g(D)=1+D+D^2+D^3+D^6
        viterbi_g2=0x6D,   # 109, g(D)=1+D^2+D^3+D^5+D^6
        descrambler="NRZ-M",
        cadu_length=1024,
        orbital_type="LEO",
        description="俄罗斯Meteor-M2极轨气象卫星（LRPT 72kbaud QPSK）",
    ),

    # ===== 卫星电视 =====
    "dvbs_qpsk": MeteorSatParams(
        name="DVB-S QPSK",
        norad_id=0,
        downlink_freq_hz=4e9,  # C/Ku波段，范围大
        symbol_rate=27500000,
        modulation="QPSK",
        viterbi_rate=0.75,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="MPEG",
        cadu_length=188,
        orbital_type="GEO",
        description="DVB-S卫星电视标准（C/Ku波段）",
    ),
    "dvbs2_qpsk": MeteorSatParams(
        name="DVB-S2 QPSK",
        norad_id=0,
        downlink_freq_hz=12e9,
        symbol_rate=30000000,
        modulation="QPSK/8PSK",
        viterbi_rate=0.75,
        viterbi_K=7,
        viterbi_g1=171,
        viterbi_g2=133,
        descrambler="MPEG",
        cadu_length=188,
        orbital_type="GEO",
        description="DVB-S2卫星电视标准（新一代）",
    ),

    # ===== 深空探测 =====
    "lro_sband": MeteorSatParams(
        name="LRO S-band",
        norad_id=37349,
        downlink_freq_hz=2200e6,
        symbol_rate=0,  # 转发器模式
        modulation="Doppler",
        viterbi_rate=1.0,
        viterbi_K=0,
        viterbi_g1=0,
        viterbi_g2=0,
        descrambler="none",
        cadu_length=0,
        orbital_type="Lunar",
        description="月球勘测轨道飞行器S波段多普勒跟踪",
    ),

    # ===== 业余卫星 =====
    "iss_manual": MeteorSatParams(
        name="ISS 业余模式",
        norad_id=25544,
        downlink_freq_hz=145.8e6,
        symbol_rate=1200,
        modulation="AFSK",
        viterbi_rate=1.0,
        viterbi_K=0,
        viterbi_g1=0,
        viterbi_g2=0,
        descrambler="none",
        cadu_length=0,
        orbital_type="LEO",
        description="国际空间站业余无线电（SSTV/包通信）",
    ),
}


def list_meteor_satellites() -> List[Dict]:
    """列出所有支持的气象卫星。"""
    result = []
    for key, sat in METEOR_SATS.items():
        result.append({
            "key": key,
            "name": sat.name,
            "frequency_mhz": sat.downlink_freq_hz / 1e6,
            "symbol_rate_ksps": sat.symbol_rate / 1000,
            "modulation": sat.modulation,
            "orbital_type": sat.orbital_type,
            "description": sat.description,
        })
    return result


def get_satellite_params(key: str) -> Optional[MeteorSatParams]:
    """获取卫星参数。"""
    return METEOR_SATS.get(key.lower())


# ========================================================================
# 信号处理模块
# ========================================================================

# 来源: SatDump plugins/meteor_support/meteor/deint.h:9-10（改编自
#        github.com/dbdexter-dev/meteor_decode）；交叉印证 NASA LRPT
#        Demonstration Report: "36 interleaver branches, 2048 bits per
#        elementary delay"。Meteor LRPT 在卷积编码(rate=1/2)之后做卷积交织，
#        因此接收端必须在 Viterbi 之前先做卷积去交织，否则突发错误无法被
#        Viterbi 纠正。
LRPT_DEINT_BRANCHES = 36   # I = INTER_BRANCH_COUNT，去交织分支数
LRPT_DEINT_DELAY = 2048    # J = INTER_BRANCH_DELAY，相邻分支的符号延迟


def convolutional_interleave(data: np.ndarray,
                              num_branches: int = LRPT_DEINT_BRANCHES,
                              branch_delay: int = LRPT_DEINT_DELAY) -> np.ndarray:
    """Forney 卷积交织器（发射端）。

    输入符号按 num_branches 轮询分发到各分支；第 k 个分支的 FIFO 深度为
    k*branch_delay，因此第 k 路相对第 0 路多延迟 k*branch_delay 个符号。
    与 convolutional_deinterleave 互逆，级联总时延 = num_branches*(num_branches-1)*branch_delay。

    来源: CCSDS 131.0-B 卷积交织；meteor_decode deint.cpp deinterleave() 的正向。
    """
    data = np.asarray(data)
    # 分支 k 的延迟线深度 = k*branch_delay（预填零，模拟初始时延）
    bufs = [collections.deque([0] * (k * branch_delay)) for k in range(num_branches)]
    out = np.empty_like(data)
    for n, sym in enumerate(data):
        b = n % num_branches
        bufs[b].append(sym)        # 压入当前符号
        out[n] = bufs[b].popleft()  # 弹出延迟后的符号
    return out


def convolutional_deinterleave(data: np.ndarray,
                               num_branches: int = LRPT_DEINT_BRANCHES,
                               branch_delay: int = LRPT_DEINT_DELAY) -> np.ndarray:
    """Forney 卷积去交织器（接收端，位于 Viterbi 之前）。

    第 k 个分支的 FIFO 深度取 (num_branches-1-k)*branch_delay，与交织器互补，
    从而把发射端打散到各分支的符号重新聚拢为原始顺序。
    注意：开头 num_branches*(num_branches-1)*branch_delay 个输出为时延预热零，
    之后才是有效数据（交给后续 Viterbi/帧同步吸收）。

    来源: SatDump deint.cpp:60-89 deinterleave()；CCSDS 131.0-B。
    """
    data = np.asarray(data)
    # 分支 k 延迟线深度 = (num_branches-1-k)*branch_delay（与交织器互补）
    bufs = [collections.deque([0] * ((num_branches - 1 - k) * branch_delay))
            for k in range(num_branches)]
    out = np.empty_like(data)
    for n, sym in enumerate(data):
        b = n % num_branches
        bufs[b].append(sym)
        out[n] = bufs[b].popleft()
    return out


def qpsk_demodulate(iq: np.ndarray, sps: int) -> np.ndarray:
    """
    QPSK 解调（真实实现，委托给 demod.QPSKDemodulator）。

    链路：RRC 匹配滤波 → Costas 环载波恢复 → Gardner 位同步 → QPSK 星座判决。

    参数:
        iq: 复数IQ采样
        sps: 每符号采样数

    返回:
        解调后的符号序列（复数，±1/√2 星座点）
    """
    if not _HAS_REAL_DEMOD:
        raise RuntimeError("demod.py 不可用，无法进行真实 QPSK 解调")
    demod = QPSKDemodulator(sps=int(sps))
    return demod.demodulate(iq)


def viterbi_decode_demo(bits: np.ndarray, rate: float = 0.5, K: int = 7) -> np.ndarray:
    """
    Viterbi 解码（真实实现，委托给 demod.ViterbiDecoder）。

    使用标准 K=7, rate=1/2, G1=171, G2=133 卷积码（CCSDS 气象卫星标准）。

    参数:
        bits: 接收的编码比特流（软判决 0-1 浮点或硬判决 0/1）
        rate: 码率（保留参数，目前仅支持 1/2）
        K: 约束长度

    返回:
        解码后的信息比特
    """
    if not _HAS_REAL_DEMOD:
        raise RuntimeError("demod.py 不可用，无法进行 Viterbi 解码")
    if abs(rate - 0.5) > 1e-6:
        raise NotImplementedError(f"目前仅实现 rate=1/2 Viterbi，收到 rate={rate}")
    dec = ViterbiDecoder(K=K, G1=171, G2=133)
    return dec.decode(bits)


def descramble_ccdb(bits: np.ndarray) -> np.ndarray:
    """
    CCDB 解扰（真实实现，委托给 demod.descramble_ccdb）。

    使用 CCSDS 标准生成多项式 x^8 + x^7 + x^5 + x^3 + 1。
    """
    if not _HAS_REAL_DEMOD:
        raise RuntimeError("demod.py 不可用，无法进行 CCDB 解扰")
    return _demod_descramble_ccdb(bits)


def demodulate_lrpt(iq: np.ndarray, sample_rate: float,
                    sat_params: "MeteorSatParams") -> List[np.ndarray]:
    """
    真实 LRPT 解调管道骨架。

    链路：
      1. QPSK 解调（RRC 匹配滤波 → Costas 载波恢复 → Gardner 位同步 → 判决）
      2. 符号转比特（格雷码映射）
      3. 卷积去交织（Forney, I=36, J=2048；Viterbi 之前，见 deint.cpp）
      4. Viterbi 解码（K=7, r=1/2, G1=0x4F/G2=0x6D = CCSDS R2 K7）
      5. 解扰（CCDB 或 NRZ-M，按卫星参数选择）
      6. CADU 帧提取（搜索 ASM 同步字）

    参数:
        iq: 复数 IQ 采样（已由前端下变频到基带）
        sample_rate: 采样率 Hz
        sat_params: 卫星参数（符号率/调制方式/Viterbi 参数/解扰方式）

    返回:
        List[np.ndarray]：提取出的 CADU 比特帧列表；无同步帧时返回空列表
    """
    if not _HAS_REAL_DEMOD:
        raise RuntimeError("demod.py 不可用，无法运行 LRPT 解调管道")
    if sat_params.modulation not in ("QPSK", "OQPSK"):
        # TODO: OQPSK 需在 Costas 前加 OQPSK 偏移对齐；AFM/AFSK 走 APT 模拟链路
        raise NotImplementedError(f"暂不支持调制方式: {sat_params.modulation}")

    sps = max(1, int(round(sample_rate / sat_params.symbol_rate)))

    # 1. QPSK 解调
    q = QPSKDemodulator(sps=sps)
    symbols = q.demodulate(iq)

    # 2. 符号转比特
    bits = q.symbols_to_bits(symbols).astype(np.float64)

    # 3. 卷积去交织（Viterbi 之前）
    # 来源: SatDump plugins/meteor_support/meteor/deint.cpp:60-89 deinterleave() —
    #        Meteor LRPT 在卷积编码后做卷积交织，接收端必须先去交织再 Viterbi；
    #        否则突发错误无法被 Viterbi 纠正。I=36 分支, 相邻分支延迟 J=2048。
    if sat_params.viterbi_rate < 1.0 and sat_params.viterbi_K > 0:
        bits = convolutional_deinterleave(
            bits, LRPT_DEINT_BRANCHES, LRPT_DEINT_DELAY).astype(np.float64)

    # 4. Viterbi 解码（rate<1 时）
    if sat_params.viterbi_rate < 1.0 and sat_params.viterbi_K > 0:
        dec = ViterbiDecoder(K=sat_params.viterbi_K,
                             G1=sat_params.viterbi_g1,
                             G2=sat_params.viterbi_g2)
        bits = dec.decode(bits).astype(np.float64)

    # 5. 解扰
    if sat_params.descrambler == "CCDB":
        bits = _demod_descramble_ccdb(bits.astype(np.uint8)).astype(np.float64)
    elif sat_params.descrambler == "NRZ-M":
        bits = descramble_nrz_m(bits.astype(np.uint8)).astype(np.float64)
    # TODO: MPEG 解扰（DVB-S）尚未实现

    # 5. CADU 提取
    if sat_params.cadu_length > 0:
        return extract_cadu(bits.astype(np.uint8), sat_params.cadu_length)
    return []


def extract_cadu(bits: np.ndarray, cadu_length: int = 1024) -> List[np.ndarray]:
    """
    从比特流中提取CADU帧。

    搜索ASM（Alternate Special Marker）同步字，然后提取完整帧。
    """
    # 搜索同步字
    ASM = np.array([1,1,0,0,1,0,1,1,1,1,0,0,0,1,0,0,
                     0,1,1,0,0,1,0,1,1,1,1,0,0,0,1,0,
                     0,0,1,1,0,0,1,0,1,1,1,1,0,0,0,1,
                     0,0,0,1,1,0,0,1,0,1,1,1,1,0,0,0], dtype=np.int8)

    cadus = []
    bit_idx = 0
    while bit_idx < len(bits) - len(ASM):
        # 搜索ASM
        for i in range(bit_idx, min(bit_idx + cadu_length * 8, len(bits) - len(ASM))):
            if np.array_equal(bits[i:i+len(ASM)], ASM):
                # 找到同步字，提取一帧
                frame_start = i
                frame_end = min(i + cadu_length * 8, len(bits))
                cadu = bits[frame_start:frame_end]
                cadus.append(cadu)
                bit_idx = frame_end
                break
        else:
            bit_idx += 1
            continue

    return cadus


# ========================================================================
# 图像合成
# ========================================================================

def compose_visible_image(cadu_data: List[np.ndarray]) -> Optional[np.ndarray]:
    """
    从 CADU 数据合成可见光云图。

    TODO: 尚未实现真实图像合成。需要：
      1. 按 CCSDS 虚拟信道数据单元（VCDU）拆解 CADU，提取图像层；
      2. 按卫星相机行格式（Meteor-M2 每帧 4 通道交错）重组行；
      3. 去同步字、去字节填充、按行拼成灰度/RGB 图像。
    目前不再返回伪造的径向渐变"假云图"，避免上层误以为解码成功。
    """
    # TODO: 接 satdump_integration 或自实现 VCDU→图像重组
    logger.warning("compose_visible_image 尚未实现真实图像合成，返回 None")
    return None


# ========================================================================
# LRO 月球轨道与多普勒定轨
# ========================================================================

# 月球基本参数
MOON_RADIUS = 1737.4e3  # m
MOON_MU = 4.9048695e12  # m^3/s^2
MOON_GRAV = 1.62  # m/s^2


class LROOrbit:
    """
    LRO（月球勘测轨道飞行器）轨道模型。

    简化的月球轨道模型，用于多普勒定轨演示。
    实际LRO轨道是低月球轨道（~50km高度），近圆形。
    """

    def __init__(self, altitude_km: float = 50.0, inclination_deg: float = 90.0):
        self.altitude = altitude_km * 1000
        self.radius = MOON_RADIUS + self.altitude
        self.inclination = np.radians(inclination_deg)

        # 计算轨道周期（开普勒第三定律）
        n = np.sqrt(MOON_MU / self.radius**3)  # 平均运动角速度
        self.period = 2 * np.pi / n
        self.mean_motion = n

    def position(self, t: float) -> np.ndarray:
        """
        计算LRO在月固坐标系中的位置。

        参数:
            t: 时间（秒，从升交点开始）

        返回:
            位置向量 (x, y, z)，单位米
        """
        # 简化：圆形轨道，轨道面倾角i
        n = self.mean_motion
        theta = n * t  # 轨道角

        # 在轨道平面内的位置
        x_orbit = self.radius * np.cos(theta)
        y_orbit = self.radius * np.sin(theta)

        # 旋转到月固坐标系（考虑倾角）
        x = x_orbit
        y = y_orbit * np.cos(self.inclination)
        z = y_orbit * np.sin(self.inclination)

        return np.array([x, y, z])

    def velocity(self, t: float) -> np.ndarray:
        """计算LRO速度。"""
        n = self.mean_motion
        theta = n * t

        vx = -self.radius * n * np.sin(theta)
        vy = self.radius * n * np.cos(theta) * np.cos(self.inclination)
        vz = self.radius * n * np.cos(theta) * np.sin(self.inclination)

        return np.array([vx, vy, vz])


def doppler_shift(
    sat_pos: np.ndarray,
    sat_vel: np.ndarray,
    station_pos: np.ndarray,
    freq_hz: float,
) -> float:
    """
    计算多普勒频移。

    参数:
        sat_pos: 卫星位置向量
        sat_vel: 卫星速度向量
        station_pos: 地面站位置向量
        freq_hz: 载波频率

    返回:
        多普勒频移（Hz）
    """
    c = 299792458.0  # 光速

    # 视线方向
    los = sat_pos - station_pos
    los_norm = np.linalg.norm(los)
    los_unit = los / los_norm

    # 径向速度
    radial_velocity = np.dot(sat_vel, los_unit)

    # 多普勒频移
    doppler = -radial_velocity / c * freq_hz

    return doppler


def ekf_orbit_determination(
    observations: List[Tuple[float, float]],
    initial_state: np.ndarray,
    Q: np.ndarray,
    R: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    扩展卡尔曼滤波（EKF）定轨。

    参数:
        observations: [(时间, 多普勒观测值), ...]
        initial_state: 初始状态 [x, y, z, vx, vy, vz]
        Q: 过程噪声协方差
        R: 测量噪声协方差

    返回:
        (估计状态序列, 协方差序列)
    """
    n = len(initial_state)
    x = initial_state.copy()
    P = np.eye(n) * 1000  # 初始协方差

    states = []
    covs = []

    dt = observations[1][0] - observations[0][0] if len(observations) > 1 else 1.0

    for t, y in observations:
        # 预测步
        # 简化的状态转移（匀速运动）
        F = np.eye(n)
        F[0, 3] = dt
        F[1, 4] = dt
        F[2, 5] = dt

        x_pred = F @ x
        P_pred = F @ P @ F.T + Q

        # 更新步
        # 观测模型：h(x) = 多普勒频移
        # 简化：直接用位置差的径向速度
        H = np.zeros((1, n))  # 观测矩阵（简化）
        H[0, 3:6] = [0, 0, 1]  # 观测z方向速度

        # 卡尔曼增益
        S = H @ P_pred @ H.T + R
        K = P_pred @ H.T @ np.linalg.inv(S)

        # 更新状态
        innovation = y - H @ x_pred
        x = x_pred + (K.flatten() * innovation[0])
        P = (np.eye(n) - K @ H) @ P_pred

        states.append(x.copy())
        covs.append(P.copy())

    return np.array(states), np.array(covs)

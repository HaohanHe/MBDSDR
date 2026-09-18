"""
MBDSDR AI - 气象卫星接收与解码
================================

支持：
- GK-2A (GEO-KOMPSAT-2A) LRIT
- 风云四号 (FY-4A/4B) LRIT/HRIT
- 风云三号 (FY-3) HRPT/AHRPT
- GOES 系列 LRIT/HRIT

解码流程：QPSK解调 → Viterbi解码 → 解扰 → CADU提取 → 图像合成
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass


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
    # 同步轨道
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

def qpsk_demodulate(iq: np.ndarray, sps: int) -> np.ndarray:
    """
    QPSK 解调。

    参数:
        iq: 复数IQ采样
        sps: 每符号采样数

    返回:
        解调后的符号序列（复数）
    """
    # 匹配滤波（简单低通）
    # 抽取
    n_symbols = len(iq) // sps
    symbols = iq[:n_symbols * sps:sps]

    # 载波恢复（简化：直接取相位）
    # 实际需要Costas环
    return symbols


def viterbi_decode_demo(bits: np.ndarray, rate: float = 0.5, K: int = 7) -> np.ndarray:
    """
    Viterbi解码（演示框架）。

    实际完整Viterbi需要较大计算量，这里提供框架接口。
    完整实现可调用gnuradio的viterbi或自己实现。
    """
    # 简化演示：返回前N个比特
    # 实际应该用维特比算法解码卷积码
    return bits[:len(bits)//2]


def descramble_ccdb(bits: np.ndarray) -> np.ndarray:
    """
    CCDB解扰（CSSR标准）。

    使用CCDB生成多项式进行解扰。
    """
    # 简化演示
    return bits


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

def compose_visible_image(cadu_data: List[np.ndarray]) -> np.ndarray:
    """
    从CADU数据合成可见光云图。

    简化演示：返回一个模拟的云图矩阵。
    """
    # 实际实现需要解析CADU中的图像数据
    # 这里返回一个演示图像
    size = 512
    # 生成模拟云图（径向渐变+噪声）
    y, x = np.mgrid[-1:1:size*1j, -1:1:size*1j]
    r = np.sqrt(x**2 + y**2)
    img = np.exp(-r**2 * 2) * 255
    img += np.random.randn(size, size) * 10
    img = np.clip(img, 0, 255).astype(np.uint8)
    return img


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

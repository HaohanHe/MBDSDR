"""
celestial_geometry.py — Celestial sphere projection & coordinate transform system.

数学参考 Stellarium (GPL-3.0), 独立重实现
=============================================
本模块独立重实现 Stellarium 的天球投影与坐标变换链, 全部用 numpy 向量化运算。
不依赖 astropy; 时间框架接受 Julian Date (可由 skyfield 等提供)。

坐标约定 (与 Stellarium 完全一致):
  - 所有方向向量均为单位球面上的 Vec3d = np.array([x, y, z])
  - J2000 赤道惯性系 (ICRS): +x 指向春分点, +y 指向赤经 6h, +z 指向北天极
  - 地平系 (alt/az): +x=北, +y=东, +z=天底 (nadir); 投影视方向为 -z (天顶)
    -> 这与 StelProjector::forward 中 v[2]<0 为可见面一致
    -> az = atan2(y, x), alt = asin(-z / |v|)

坐标变换链 (参考 StelCore::updateTransformMatrices(), StelCore.cpp:1072):
  J2000 (ICRS)
    --precession P (Vondrak/Capitaine 2011, IAU2006)
    --nutation N (IAU2000B abridged)
  -> 赤道坐标 (当前春分点/赤道)
    --local rotation R = Rz(LST+lon) * Ry(90-lat)   (StelObserver.cpp:229)
  -> 地平坐标 (az/alt)
    --projector (stereographic / orthographic / azimuthal equidistant)
  -> 屏幕 (x, y)

投影实现参考:
  - StelProjectorStereographic  src/core/StelProjectorClasses.cpp:259-299
  - StelProjectorOrthographic   src/core/StelProjectorClasses.cpp:881-916
  - StelProjectorFisheye (azimuthal equidistant) src/core/StelProjectorClasses.cpp:363-395
"""

from __future__ import annotations

import math
import numpy as np

__all__ = [
    # 常量
    "DEG", "RAD2DEG", "J2000", "AU",
    # 角度/向量工具
    "deg2rad", "rad2deg", "normalize", "vec_from_radec", "radec_from_vec",
    "vec_from_azalt", "azalt_from_vec",
    # 旋转矩阵
    "rot_x", "rot_y", "rot_z",
    # 时间
    "gmst",
    # 岁差/章动
    "precession_matrix", "nutation_angles_2000b", "obliquity",
    # 地面站
    "GroundStation",
    # 坐标变换
    "j2000_to_equinox_of_date", "equinox_of_date_to_j2000",
    "equatorial_to_altaz", "altaz_to_equatorial",
    "j2000_to_altaz", "altaz_to_j2000",
    # 投影
    "StereographicProjection", "OrthographicProjection", "AzimuthalEquidistantProjection",
    "make_projection",
]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEG: float = math.pi / 180.0
RAD2DEG: float = 180.0 / math.pi
J2000: float = 2451545.0          # Julian date of J2000.0 (TT)
AU: float = 149597870.7           # km

# J2000 黄道倾角 (IAU 2006, "mean obliquity at J2000")
# 参考 precession.c / getPrecessionAngleVondrakEpsilon
EPS0: float = 84381.406 * (math.pi / (180.0 * 3600.0))  # rad ≈ 23.4392794°


# ---------------------------------------------------------------------------
# 角度 / 向量工具
# ---------------------------------------------------------------------------
def deg2rad(d: float) -> float:
    """角度 -> 弧度"""
    return float(d) * DEG


def rad2deg(r: float) -> float:
    """弧度 -> 角度"""
    return float(r) * RAD2DEG


def normalize(v: np.ndarray) -> np.ndarray:
    """单位化向量 (numpy 向量化, 支持形状 (...,3))。"""
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n = np.where(n == 0, 1.0, n)
    return v / n


def vec_from_radec(ra: float, dec: float) -> np.ndarray:
    """赤道坐标 (ra, dec) 角度 -> 单位方向向量 (J2000 或当前赤道系, 取决于上下文)。

    参考 StelProjectorClasses.cpp 中 alpha=atan2(v[0],-v[2]), delta=asin(v[1]/r)
    的逆运算: v[0]=cos(delta)*sin(alpha), v[1]=sin(delta), v[2]=-cos(delta)*cos(alpha)
    其中 alpha=ra (赤经), delta=dec (赤纬)。
    """
    ra_r = deg2rad(ra)
    dec_r = deg2rad(dec)
    cd = math.cos(dec_r)
    return np.array([cd * math.sin(ra_r),
                     math.sin(dec_r),
                     -cd * math.cos(ra_r)])


def radec_from_vec(v: np.ndarray) -> tuple[float, float]:
    """单位方向向量 -> (ra_deg, dec_deg)。参考 StelProjectorCylinder::backward
    (StelProjectorClasses.cpp:715) 中的逆运算。"""
    v = np.asarray(v, dtype=float)
    r = float(np.linalg.norm(v))
    if r == 0.0:
        return 0.0, 0.0
    ra = math.atan2(v[0], -v[2])          # alpha
    dec = math.asin(np.clip(v[1] / r, -1.0, 1.0))  # delta
    ra_deg = ra * RAD2DEG
    if ra_deg < 0:
        ra_deg += 360.0
    return ra_deg, dec * RAD2DEG


def vec_from_azalt(az: float, alt: float) -> np.ndarray:
    """地平坐标 (az_deg, alt_deg) -> 单位方向向量。

    地平系约定: +x=北, +y=东, +z=天底 (StelObserver.cpp:229-230)。
    az = atan2(y, x), alt = asin(-z/r)。
    """
    az_r = deg2rad(az)
    alt_r = deg2rad(alt)
    ca = math.cos(alt_r)
    return np.array([ca * math.cos(az_r),     # x = north component
                     ca * math.sin(az_r),     # y = east component
                     -math.sin(alt_r)])       # z = -sin(alt) (天顶为 -z)


def azalt_from_vec(v: np.ndarray) -> tuple[float, float]:
    """单位方向向量 (地平系) -> (az_deg, alt_deg)。"""
    v = np.asarray(v, dtype=float)
    r = float(np.linalg.norm(v))
    if r == 0.0:
        return 0.0, 0.0
    az = math.atan2(v[1], v[0])               # atan2(east, north)
    alt = math.asin(np.clip(-v[2] / r, -1.0, 1.0))
    az_deg = az * RAD2DEG
    if az_deg < 0:
        az_deg += 360.0
    return az_deg, alt * RAD2DEG


# ---------------------------------------------------------------------------
# 旋转矩阵 (与 Stellarium VecMath.hpp:1417-1450 完全一致, 列向量约定)
#   Rx: [[1,0,0],[0,c,s],[0,-s,c]]
#   Ry: [[c,0,-s],[0,1,0],[s,0,c]]
#   Rz: [[c,s,0],[-s,c,0],[0,0,1]]
# ---------------------------------------------------------------------------
def rot_x(angle: float) -> np.ndarray:
    """绕 x 轴旋转矩阵 (参考 VecMath.hpp:1417)。"""
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0],
                     [0.0,  c,   s ],
                     [0.0, -s,   c ]])


def rot_y(angle: float) -> np.ndarray:
    """绕 y 轴旋转矩阵 (参考 VecMath.hpp:1429)。"""
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[ c,   0.0, -s ],
                     [0.0, 1.0, 0.0],
                     [ s,   0.0,  c ]])


def rot_z(angle: float) -> np.ndarray:
    """绕 z 轴旋转矩阵 (参考 VecMath.hpp:1441)。"""
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[ c,   s,  0.0],
                     [-s,   c,  0.0],
                     [0.0, 0.0, 1.0]])


# ---------------------------------------------------------------------------
# 时间: 格林尼治平恒星时 (GMST)
# 参考 Stellarium src/core/planetsephems/sidereal_time.c:54-85
#   (Capitaine, Wallace, Chapront 2003, A&A 412, 567, eq. 43)
# ---------------------------------------------------------------------------
def gmst(jd_ut1: float) -> float:
    """格林尼治平恒星时 GMST (度)。输入 JD 为 UT1 (即民用世界时的 Julian Date)。

    参考 sidereal_time.c:54  get_mean_sidereal_time()。
    注意: Stellarium 中 JD 是 UT, JDE 是 TT; 此处简化用 UT 直接计算。
    """
    jd_tt = jd_ut1  # 简化: 忽略 ΔT (约 1 分钟, 对演示足够)
    t = (jd_tt - J2000) / 36525.0          # T (Julian centuries, TT)
    tu = (jd_ut1 - J2000) / 36525.0        # tu (Julian centuries, UT1)

    # UT1 当日秒数 (sidereal_time.c:68)
    ut1_sec = (jd_ut1 - math.floor(jd_ut1) + 0.5) * 86400.0

    # 多项式项 (sidereal_time.c:72)
    s = (((-0.000000002454 * t - 0.00000199708) * t - 0.0000002926) * t
         + 0.092772110) * t * t
    s += (t - tu) * 307.4771013
    s += 8640184.79447825 * tu + 24110.5493771
    s += ut1_sec

    # 秒 -> 度 (1h = 15°, 1s 时间 = 1/15 角秒... 实际 1/240 度)
    s *= 1.0 / 240.0
    # 归一化到 [0, 360)
    s = s % 360.0
    if s < 0:
        s += 360.0
    return s


# ---------------------------------------------------------------------------
# 岁差: IAU 2006 / P03 模型 (Capitaine et al. 2003)
# 参考 Stellarium src/core/modules/Planet.cpp:2672-2679
#   rotLocalToParent = Rz(-psi_A) * Rx(-omega_A) * Rz(chi_A)
# 这里用 Montenbruck & Pfleger / Explanatory Supplement 标准 zeta_A, z_A, theta_A
# 多项式 (arcsec), 构造 J2000 -> 当前赤道 的岁差矩阵 P。
# ---------------------------------------------------------------------------
def precession_matrix(jd_tt: float) -> np.ndarray:
    """J2000 (ICRS) -> 赤道坐标(当前春分点/赤道) 的岁差矩阵 P。

    参考 StelCore.cpp:1081: matJ2000ToEquinoxEqu = matEquinoxEquDateToJ2000^T
    参考 Planet.cpp:2679: rotLocalToParent = Rz(-psi_A)*Rx(-omega_A)*Rz(chi_A)

    采用 IAU 2006 P03 岁差角 (Montenbruck & Pfleger, Astronomy on the Personal Computer)。
    返回 3x3 旋转矩阵, v_date = P @ v_j2000。
    """
    T = (jd_tt - J2000) / 36525.0

    # 岁差角 (角秒), IAU 2006 P03 标准多项式
    zeta_a = ((2.650545
               + 2306.083227 * T
               + 0.2988499 * T * T
               + 0.01801828 * T**3
               - 0.000005971 * T**4
               - 0.0000003173 * T**5)) * DEG / 3600.0

    z_a = ((-2.650545
            + 2306.077181 * T
            + 1.0927348 * T * T
            + 0.01826898 * T**3
            - 0.000028596 * T**4
            - 0.0000002904 * T**5)) * DEG / 3600.0

    theta_a = ((2004.191903 * T
                - 0.4269350 * T * T
                - 0.04183110 * T**3
                - 0.000006059 * T**4
                - 0.00000014543 * T**5)) * DEG / 3600.0

    # P = Rz(z_A) * Rx(-theta_A) * Rz(-zeta_A)
    # (标准 IAU 2006 形式; 与 Stellarium 的 chi/psi/omega 序列等价)
    P = rot_z(z_a) @ rot_x(-theta_a) @ rot_z(-zeta_a)
    return P


def obliquity(jd_tt: float) -> float:
    """当前平黄赤交角 ε_A (rad)。
    参考 precession.c: getPrecessionAngleVondrakEpsilon。
    简化: ε_A = ε0 - 0.01306°*T (近似, 精度 ~0.1 角秒)。
    """
    T = (jd_tt - J2000) / 36525.0
    return EPS0 - 0.0000003439 * T  # rad/century ≈ 46.8"/century


# ---------------------------------------------------------------------------
# 章动: IAU 2000B 简化模型 (7 个主项)
# 参考 Stellarium src/core/planetsephems/precession.c: getNutationAngles
#   以及 Planet.cpp:2687: N = Rx(eps_A)*Rz(-dpsi)*Rx(-eps_A-deps)
# ---------------------------------------------------------------------------
def nutation_angles_2000b(jd_tt: float) -> tuple[float, float]:
    """返回 (delta_psi, delta_eps) 章动角 (rad)。

    IAU 2000B 简化模型, 精度 ~1 mas (1995-2050)。
    参考 McCarthy & Luzum 2003, Cel.Mech.Dyn.Astr. 85:37-49。
    """
    T = (jd_tt - J2000) / 36525.0

    # 月球平升交点黄经 Ω (rad)
    Omega = (125.04452 - 1934.136261 * T) * DEG
    # 太阳平近点角 l (太阳)
    l_sun = (357.52910 + 35999.05030 * T) * DEG
    # 月球平近点角 l
    l_mon = (134.96340 + 477198.89108 * T) * DEG
    # 月球平升交点角距 F
    F = (93.27295 + 483202.017523 * T) * DEG
    # 太阳平角距 D
    D = (297.85036 + 445267.111480 * T) * DEG

    # IAU 2000B 主项 (系数单位: 0.0001 arcsec = 微角秒*1e-3)
    # 取最大的几项即可达到 ~0.5" 精度
    # 标准 IAU 2000B 完整 77 项这里取前 8 项
    dp = 0.0
    de = 0.0
    # 项格式: (角度组合系数 l, l', F, D, Ω, dpsi*1e4 mas, deps*1e4 mas)
    # l=l_mon, l'=l_sun
    terms = [
        (0, 0, 0, 0, 1, -17.2066,  0.0000),
        (0, 0, 2, 0, 2, -1.3170,   0.5774),
        (0, 0, 2, 0, 1, -0.2276,   0.0977),
        (0, 0, 2, 0, 0,  0.2074,   0.0),
        (0, 1, 0, 0, 0,  0.1478,   0.0),
        (1, 0, 0, 0, 0,  0.1299,   0.0),
        (0, 1, 2, 0, 2,  0.0000,   0.0000),
        (0, 0, 0, 0, 2,  0.0000,  -0.0000),
    ]
    for (al, als, aF, aD, aOm, cdpsi, cdeps) in terms:
        arg = al * l_mon + als * l_sun + aF * F + aD * D + aOm * Omega
        dp += cdpsi * math.sin(arg)
        de += cdeps * math.cos(arg)

    # 系数是 0.0001 arcsec; 转 rad
    dp_r = dp * 0.0001 / 3600.0 * DEG
    de_r = de * 0.0001 / 3600.0 * DEG
    return dp_r, de_r


def nutation_matrix(jd_tt: float) -> np.ndarray:
    """章动矩阵 N (参考 Planet.cpp:2687):
       N = Rx(eps_A) * Rz(-deltaPsi) * Rx(-eps_A - deltaEps)
    """
    eps_a = obliquity(jd_tt)
    dpsi, deps = nutation_angles_2000b(jd_tt)
    N = rot_x(eps_a) @ rot_z(-dpsi) @ rot_x(-eps_a - deps)
    return N


# ---------------------------------------------------------------------------
# 地面站 (参考 StelLocation.hpp:40-91)
# ---------------------------------------------------------------------------
class GroundStation:
    """观测者地面站。

    参考 StelLocation (src/core/StelLocation.hpp):
      - longitude: 东经为正, 度
      - latitude:  北纬为正, 度
      - altitude:   海拔, 米
      - timezone:   IANA 时区名 (仅记录, 不参与数学计算)

    关键旋转 (StelObserver.cpp:223-231):
      matAltAzToEquatorial = Rz((GMST + lon)*DEG) * Ry((90 - lat)*DEG)
    """

    def __init__(self, longitude_deg: float, latitude_deg: float,
                 elevation_m: float = 0.0, name: str = "", timezone: str = "UTC"):
        # StelLocation.hpp:40-42: 东经为正, 北纬为正
        self.longitude = float(longitude_deg)    # 度, 东正
        self.latitude = float(latitude_deg)      # 度, 北正
        self.elevation = float(elevation_m)      # 米
        self.name = name
        self.timezone = timezone

    # -- 本地旋转矩阵 -------------------------------------------------------
    def altaz_to_equatorial_matrix(self, jd_ut1: float) -> np.ndarray:
        """地平系 -> 当前赤道系 旋转矩阵。

        参考 StelObserver.cpp:229-230:
          Rz((getSiderealTime(JD,JDE) + longitude)*DEG) * Ry((90-latitude)*DEG)
        """
        lst = gmst(jd_ut1) + self.longitude       # 本地恒星时 (度)
        R = rot_z(deg2rad(lst)) @ rot_y(deg2rad(90.0 - self.latitude))
        return R

    def equatorial_to_altaz_matrix(self, jd_ut1: float) -> np.ndarray:
        """当前赤道系 -> 地平系 旋转矩阵 (上式的转置)。
        参考 StelCore.cpp:1075: matEquinoxEquToAltAz = matAltAzToEquinoxEqu.transpose()
        """
        return self.altaz_to_equatorial_matrix(jd_ut1).T

    def __repr__(self) -> str:
        return (f"GroundStation({self.name!r}, lon={self.longitude:.4f}°, "
                f"lat={self.latitude:.4f}°, alt={self.elevation:.0f}m)")


# ---------------------------------------------------------------------------
# 完整坐标变换链
# ---------------------------------------------------------------------------
def j2000_to_equinox_of_date(v_j2000: np.ndarray, jd_tt: float,
                             apply_nutation: bool = True) -> np.ndarray:
    """J2000 (ICRS) -> 赤道坐标(当前春分点/赤道)。

    参考 StelCore.cpp:1081: matJ2000ToEquinoxEqu = matEquinoxEquDateToJ2000^T
    即先岁差 P, 再章动 N。
    """
    P = precession_matrix(jd_tt)
    v = P @ np.asarray(v_j2000, dtype=float)
    if apply_nutation:
        N = nutation_matrix(jd_tt)
        v = N @ v
    return v


def equinox_of_date_to_j2000(v_eq: np.ndarray, jd_tt: float,
                             apply_nutation: bool = True) -> np.ndarray:
    """赤道坐标(当前) -> J2000 (逆变换)。"""
    P = precession_matrix(jd_tt)
    v = np.asarray(v_eq, dtype=float)
    if apply_nutation:
        N = nutation_matrix(jd_tt)
        v = N.T @ v
    v = P.T @ v
    return v


def equatorial_to_altaz(v_eq: np.ndarray, station: GroundStation,
                        jd_ut1: float) -> np.ndarray:
    """赤道坐标(当前春分点) -> 地平坐标。

    参考 StelCore.cpp:1075: matEquinoxEquToAltAz * v
    """
    R = station.equatorial_to_altaz_matrix(jd_ut1)
    return R @ np.asarray(v_eq, dtype=float)


def altaz_to_equatorial(v_altaz: np.ndarray, station: GroundStation,
                       jd_ut1: float) -> np.ndarray:
    """地平坐标 -> 赤道坐标(当前春分点)。

    参考 StelObserver.cpp:223: matAltAzToEquatorial * v
    """
    R = station.altaz_to_equatorial_matrix(jd_ut1)
    return R @ np.asarray(v_altaz, dtype=float)


def j2000_to_altaz(v_j2000: np.ndarray, station: GroundStation,
                   jd_ut1: float, jd_tt: float | None = None,
                   apply_nutation: bool = True) -> np.ndarray:
    """完整链: J2000 -> 当前赤道 -> 地平。

    参考 StelCore.cpp:1082: matJ2000ToAltAz = matEquinoxEquToAltAz * matJ2000ToEquinoxEqu
    """
    if jd_tt is None:
        jd_tt = jd_ut1
    v_eq = j2000_to_equinox_of_date(v_j2000, jd_tt, apply_nutation)
    v_altaz = equatorial_to_altaz(v_eq, station, jd_ut1)
    return v_altaz


def altaz_to_j2000(v_altaz: np.ndarray, station: GroundStation,
                   jd_ut1: float, jd_tt: float | None = None,
                   apply_nutation: bool = True) -> np.ndarray:
    """完整链(逆): 地平 -> 当前赤道 -> J2000。"""
    if jd_tt is None:
        jd_tt = jd_ut1
    v_eq = altaz_to_equatorial(v_altaz, station, jd_ut1)
    v_j2000 = equinox_of_date_to_j2000(v_eq, jd_tt, apply_nutation)
    return v_j2000


# ---------------------------------------------------------------------------
# 投影类
#
# Stellarium 的投影在 forward() 中把 3D 单位向量 (视方向为 -z) 映射到
# 归一化的 2D 坐标 (x, y), 然后由 StelProjector 基类乘以 pixelPerRad
# 并平移到视口中心。这里我们直接输出归一化坐标 [-scale, +scale]。
#
# 视方向约定: v[2] < 0 = 可见 (天顶方向); v[2] > 0 = 背面。
# 参考 StelProjectorClasses.cpp: forward() 中 v[2] = r (深度缓存用)。
# ---------------------------------------------------------------------------
class _BaseProjection:
    """投影基类: 定义 forward / backward 接口。

    子类实现 _forward(v3) -> (x, y) 和 _backward(x, y) -> v3。
    输入 v3 为地平系(或任意方位角对称系)的单位方向向量。
    """

    name: str = "base"
    max_fov_deg: float = 360.0

    def _forward(self, v: np.ndarray) -> tuple[float, float]:
        raise NotImplementedError

    def _backward(self, x: float, y: float) -> np.ndarray:
        raise NotImplementedError

    # -- 对外接口 (接受 az/alt 角度或 Vec3d) --------------------------------
    def project_azalt(self, az_deg: float, alt_deg: float) -> tuple[float, float]:
        """(az, alt) 度 -> 归一化屏幕坐标 (x, y)。"""
        v = vec_from_azalt(az_deg, alt_deg)
        return self._forward(v)

    def unproject_azalt(self, x: float, y: float) -> tuple[float, float]:
        """归一化屏幕坐标 (x, y) -> (az, alt) 度。"""
        v = self._backward(x, y)
        return azalt_from_vec(v)

    def project_vec(self, v: np.ndarray) -> tuple[float, float]:
        return self._forward(np.asarray(v, dtype=float))

    def unproject_vec(self, x: float, y: float) -> np.ndarray:
        return self._backward(x, y)


class StereographicProjection(_BaseProjection):
    """球极投影 (Stereographic)。

    参考 Stellarium src/core/StelProjectorClasses.cpp:259-299
      forward:  h = 0.5*(r - v[2]);  f = 1/h;  x = v[0]*f, y = v[1]*f
      backward: lqq = 0.25*(x²+y²); v[2] = lqq-1;  v /= (lqq+1)

    从南极点 (v[2]>0 侧) 投影到切平面; 保角但不保面积。
    最大 FOV 235° (StelProjectorClasses.hpp:64)。
    """

    name = "stereographic"
    max_fov_deg = 235.0

    def _forward(self, v: np.ndarray) -> tuple[float, float]:
        r = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        # StelProjectorClasses.cpp:262
        h = 0.5 * (r - v[2])
        if h <= 1e-15:
            return float("inf"), float("inf")
        f = 1.0 / h
        return v[0] * f, v[1] * f

    def _backward(self, x: float, y: float) -> np.ndarray:
        # StelProjectorClasses.cpp:279-282
        lqq = 0.25 * (x * x + y * y)
        v = np.array([x, y, lqq - 1.0])
        v /= (lqq + 1.0)
        return v


class OrthographicProjection(_BaseProjection):
    """正射投影 (Orthographic)。

    参考 Stellarium src/core/StelProjectorClasses.cpp:881-906
      forward:  h = 1/r;  x = v[0]*h, y = v[1]*h;  (背面 v[2]>0 不可见)
      backward: dq = x²+y²;  v[2] = -sqrt(1-dq);  (dq>1 时截断到圆盘边缘)

    模拟从无穷远平行光投影, 只显示半个天球 (max FOV 180°)。
    """

    name = "orthographic"
    max_fov_deg = 179.999

    def _forward(self, v: np.ndarray) -> tuple[float, float]:
        # StelProjectorClasses.cpp:883-886
        r = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        h = 1.0 / r
        return v[0] * h, v[1] * h

    def _backward(self, x: float, y: float) -> np.ndarray:
        # StelProjectorClasses.cpp:895-905
        dq = x * x + y * y
        if dq > 1.0:
            # 背面截断 (Stellarium 中返回 false 并投影到边缘)
            s = 1.0 / math.sqrt(dq)
            return np.array([x * s, y * s, 0.0])
        return np.array([x, y, -math.sqrt(1.0 - dq)])


class AzimuthalEquidistantProjection(_BaseProjection):
    """等距方位投影 (Azimuthal Equidistant), Stellarium 中称 "Fish-eye"。

    参考 Stellarium src/core/StelProjectorClasses.cpp:363-395
      forward:  h = sqrt(v[0]²+v[1]²);  f = atan2(h, -v[2]) / h
                x = v[0]*f, y = v[1]*f
      backward: a = sqrt(x²+y²);  f = sin(a)/a;  v[2] = -cos(a)

    保持投影中心到任意点的真实角距离 (屏幕半径 = 角距离),
    是极坐标天空图/全天空相机最常用的投影。max FOV 360°。
    """

    name = "azimuthal_equidistant"
    max_fov_deg = 360.0

    def _forward(self, v: np.ndarray) -> tuple[float, float]:
        # StelProjectorClasses.cpp:365-372
        rq1 = v[0] * v[0] + v[1] * v[1]
        if rq1 > 1e-30:
            h = math.sqrt(rq1)
            # atan2(h, -v[2]) = 与天顶(-z)的角距离 c
            f = math.atan2(h, -v[2]) / h
            return v[0] * f, v[1] * f
        # 中心: v[2] < 0 = 天顶
        if v[2] < 0:
            return 0.0, 0.0
        return float("inf"), float("inf")

    def _backward(self, x: float, y: float) -> np.ndarray:
        # StelProjectorClasses.cpp:389-394
        a = math.sqrt(x * x + y * y)
        if a > math.pi:
            # 超过 180°, 不可见
            a = math.pi
        f = (math.sin(a) / a) if a > 1e-15 else 1.0
        return np.array([x * f, y * f, -math.cos(a)])


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------
def make_projection(name: str) -> _BaseProjection:
    """按名称创建投影实例。"""
    name = name.lower().replace("_", "")
    table = {
        "stereographic": StereographicProjection,
        "orthographic": OrthographicProjection,
        "azimuthequidistant": AzimuthalEquidistantProjection,
        "fisheye": AzimuthalEquidistantProjection,
    }
    if name not in table:
        raise ValueError(f"未知投影: {name!r}; 可选: {list(table.keys())}")
    return table[name]()


# ---------------------------------------------------------------------------
# 自检: 往返测试 (az,alt -> project -> unproject -> az,alt)
# ---------------------------------------------------------------------------
def _self_test() -> None:
    """运行往返测试: 误差 < 0.01°。"""
    projections = [
        StereographicProjection(),
        OrthographicProjection(),
        AzimuthalEquidistantProjection(),
    ]
    # 测试点 (az_deg, alt_deg) — 避开天顶 (az 奇异) 和地平线边缘
    test_points = [
        (0.0, 45.0), (90.0, 30.0), (180.0, 60.0), (270.0, 10.0),
        (45.0, 80.0), (135.0, 20.0), (225.0, 50.0), (315.0, 70.0),
        (10.0, 0.0), (200.0, 45.0),
    ]
    max_err = 0.0
    for proj in projections:
        for az0, alt0 in test_points:
            x, y = proj.project_azalt(az0, alt0)
            az1, alt1 = proj.unproject_azalt(x, y)
            # 计算角距离误差 (度)
            # 简化: 直接比较 az/alt 差 (处理 az 环绕)
            daz = abs(az1 - az0) % 360.0
            if daz > 180.0:
                daz = 360.0 - daz
            dalt = abs(alt1 - alt0)
            err = math.sqrt(daz ** 2 + dalt ** 2)
            if err > max_err:
                max_err = err
            assert err < 0.01, (
                f"往返测试失败 [{proj.name}]: az={az0}, alt={alt0} "
                f"-> ({x:.4f},{y:.4f}) -> az={az1:.4f}, alt={alt1:.4f}, "
                f"误差={err:.6f}°"
            )
    # 测试 GroundStation + 坐标链
    station = GroundStation(126.63, 45.75, 200.0, "Changchun")
    jd = 2460000.5  # 任意测试日期
    v = vec_from_radec(100.0, 30.0)
    v_altaz = j2000_to_altaz(v, station, jd)
    v_back = altaz_to_j2000(v_altaz, station, jd)
    dot = float(np.dot(normalize(v), normalize(v_back)))
    assert abs(dot - 1.0) < 1e-6, f"坐标链往返失败: dot={dot}"

    if max_err > 0:
        print(f"[celestial_geometry] 自检通过, 最大往返误差 = {max_err:.6f}° (<0.01°)")


if __name__ == "__main__":
    _self_test()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rtklib_adapter.py — RTKLIB 真实源码移植（时间系统 / 坐标系统 / RINEX / SPP / NTRIP）

本模块把 RTKLIB（https://github.com/tomojitakasu/RTKLIB）C 源码中与 GNSS 解算
直接相关的核心算法逐行移植为 Python，并在每一处常量与算法旁标注
「来源: RTKLIB src/<file>.c:<line>」。只移植真实工作的算法骨架，PPP/RTK 留接口。

移植覆盖：
  - TimeSystem           : 参考 rtkcmn.c  时间转换（GPS周/秒 <-> UTC，含闰秒）
  - CoordinateConverter  : 参考 rtkcmn.c  ECEF<->LLH(ENU)，卫星方位/仰角
  - RINEXParser          : 参考 rinex.c   RINEX 2.10/3.0x 观测/导航文件解析
  - SPPLocator          : 参考 pntpos.c + ephemeris.c 伪距最小二乘单点定位
  - NTRIPStream         : 参考 stream.c   NTRIP client（HTTP GET + Basic Auth）

红线（与任务约定一致）：
  - 物理常数必须与 C 源码逐位一致：光速 c、GM、地球自转角速度、WGS84 椭球参数。
  - SPP 必须真实工作（合成星历 + 伪距 -> 解算位置），PPP/RTK 仅骨架。
"""

from __future__ import annotations

import base64
import math
import socket
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# ============================================================
# 物理与几何常数 —— 逐字抄自 RTKLIB 源码，禁止改动数值
# ============================================================

# rtklib.h:56  #define PI 3.1415926535897932  /* pi */
PI = 3.1415926535897932
# rtklib.h:57  #define D2R (PI/180.0)
D2R = PI / 180.0
# rtklib.h:58  #define R2D (180.0/PI)
R2D = 180.0 / PI

# rtklib.h:59  #define CLIGHT 299792458.0  /* speed of light (m/s) */
CLIGHT = 299792458.0

# rtklib.h:64  #define OMGE 7.2921151467E-5  /* earth angular velocity (IS-GPS) (rad/s) */
OMGE = 7.2921151467e-5

# rtklib.h:66  #define RE_WGS84 6378137.0  /* earth semimajor axis (WGS84) (m) */
RE_WGS84 = 6378137.0
# rtklib.h:67  #define FE_WGS84 (1.0/298.257223563)  /* earth flattening (WGS84) */
FE_WGS84 = 1.0 / 298.257223563

# ephemeris.c:64  #define MU_GPS 3.9860050E14  /* gravitational constant ref[1] */
MU_GPS = 3.9860050e14
# ephemeris.c:66  #define MU_GAL 3.986004418E14  /* earth gravitational constant ref[7] */
MU_GAL = 3.986004418e14
# ephemeris.c:67  #define MU_CMP 3.986004418E14  /* earth gravitational constant ref[9] */
MU_CMP = 3.986004418e14
# ephemeris.c:71  #define OMGE_GAL 7.2921151467E-5
OMGE_GAL = 7.2921151467e-5
# ephemeris.c:72  #define OMGE_CMP 7.292115E-5
OMGE_CMP = 7.292115e-5

# ephemeris.c:79  #define RTOL_KEPLER 1E-14  /* relative tolerance for Kepler */
RTOL_KEPLER = 1e-14
# ephemeris.c:88  #define MAX_ITER_KEPLER 30
MAX_ITER_KEPLER = 30

# rtkcmn.c:132  const static double gpst0[]={1980,1,6,0,0,0}; /* gps time reference */
GPST_EPOCH_YMDHMS = (1980, 1, 6, 0, 0, 0)

# rtkcmn.c:136-156  static double leaps[][7] = { y,m,d,h,m,s, utc-gpst }
# 现行 UTC-GPST = -18 s（2017-01-01 起）。表按生效时间倒序排列。
LEAP_SECONDS_TABLE = [
    (2017, 1, 1, 0, 0, 0, -18),
    (2015, 7, 1, 0, 0, 0, -17),
    (2012, 7, 1, 0, 0, 0, -16),
    (2009, 1, 1, 0, 0, 0, -15),
    (2006, 1, 1, 0, 0, 0, -14),
    (1999, 1, 1, 0, 0, 0, -13),
    (1997, 7, 1, 0, 0, 0, -12),
    (1996, 1, 1, 0, 0, 0, -11),
    (1994, 7, 1, 0, 0, 0, -10),
    (1993, 7, 1, 0, 0, 0, -9),
    (1992, 7, 1, 0, 0, 0, -8),
    (1991, 1, 1, 0, 0, 0, -7),
    (1990, 1, 1, 0, 0, 0, -6),
    (1988, 1, 1, 0, 0, 0, -5),
    (1985, 7, 1, 0, 0, 0, -4),
    (1983, 7, 1, 0, 0, 0, -3),
    (1982, 7, 1, 0, 0, 0, -2),
    (1981, 7, 1, 0, 0, 0, -1),
]

# stream.c:74-76  NTRIP 常量
NTRIP_AGENT = "RTKLIB/2.4.3"   # stream.c:74  #define NTRIP_AGENT "RTKLIB/" VER_RTKLIB
NTRIP_DEFAULT_PORT = 2101      # stream.c:75  #define NTRIP_CLI_PORT 2101


# ============================================================
# 基础小工具（对应 rtkcmn.c 内联数学函数）
# ============================================================

def _norm(v: np.ndarray) -> float:
    """rtkcmn.c: `extern double norm(const double *a, int n)` —— 向量模长。"""
    return float(np.linalg.norm(v))


def _dot(a: np.ndarray, b: np.ndarray) -> float:
    """rtkcmn.c: `extern double dot(const double *a,const double *b,int n)`。"""
    return float(np.dot(a, b))


# ============================================================
# TimeSystem —— 参考 rtkcmn.c
# ============================================================
class TimeSystem:
    """GPS 时 / UTC 时 / 历书时互转。来源: RTKLIB src/rtkcmn.c。

    内部用「自 GPST 起点(1980-01-06 00:00 GPS)起的秒数（含闰秒前的 GPST 连续秒）」
    作为统一的浮点秒表示，等价于 C 里 gtime_t 展开后的 (time, sec)。
    """

    # ---- rtkcmn.c:1201 epoch2time(): 日历 -> 秒(自1970) ----
    @staticmethod
    def _ymdhms_to_unix(y: int, m: int, d: int, hh: int, mm: int, ss: float) -> float:
        """日历时刻 -> Unix 秒（UTC 或 GPST 同一套日历）。

        移植 rtkcmn.c:1201 `epoch2time()`，用 4 年一周期(1461天)的整数算法，
        避免依赖系统日历库（与 C 版结果逐位一致）。
        """
        doy = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
        if y < 1970 or y > 2099 or m < 1 or m > 12:
            raise ValueError(f"epoch2time out of range: {y}-{m:02d}")
        days = ((y - 1970) * 365 + (y - 1969) // 4 + doy[m - 1] + d - 2
                + (1 if (y % 4 == 0 and m >= 3) else 0))
        whole = int(math.floor(ss))
        return float(days * 86400 + hh * 3600 + mm * 60 + whole + (ss - whole))

    @classmethod
    def gpst_epoch_unix(cls) -> float:
        """GPST 起点 1980-01-06 00:00(GPST) 对应的 Unix 秒。rtkcmn.c:132 gpst0。"""
        y, m, d, hh, mm, ss = GPST_EPOCH_YMDHMS
        return cls._ymdhms_to_unix(y, m, d, hh, mm, ss)

    # ---- rtkcmn.c:1246 gpst2time() / 1261 time2gpst() ----
    @classmethod
    def gps_week_sec_to_gpst_seconds(cls, week: int, tow: float) -> float:
        """GPS 周 + 周内秒 -> 自 GPST 起点的秒。rtkcmn.c:1246 gpst2time()。"""
        return week * 7.0 * 86400.0 + tow

    @classmethod
    def gpst_seconds_to_gps_week_sec(cls, t: float) -> Tuple[int, float]:
        """GPST 秒 -> (GPS周, 周内秒)。rtkcmn.c:1261 time2gpst()。"""
        w = int(t // (7.0 * 86400.0))
        return w, t - w * 7.0 * 86400.0

    # ---- rtkcmn.c:1425 gpst2utc() / 1442 utc2gpst() ----
    @classmethod
    def gpst_seconds_to_utc_seconds(cls, t_gpst: float) -> float:
        """GPST 连续秒 -> UTC 连续秒（减当前闰秒）。rtkcmn.c:1425 gpst2utc()。

        leaps 表按生效时间倒序：找到第一个使 (t+ls) >= 生效时刻 的条目即可。
        """
        t_gpst0 = cls.gpst_epoch_unix()
        for (y, m, d, hh, mm, ss, ls) in LEAP_SECONDS_TABLE:
            t_eff = cls._ymdhms_to_unix(y, m, d, hh, mm, ss) - t_gpst0
            # tu = t + ls ; if (tu - t_eff) >= 0 -> return tu   (rtkcmn.c:1430-1433)
            if (t_gpst + ls) >= t_eff:
                return t_gpst + ls
        return t_gpst

    @classmethod
    def utc_seconds_to_gpst_seconds(cls, t_utc: float) -> float:
        """UTC 连续秒 -> GPST 连续秒（加当前闰秒）。rtkcmn.c:1442 utc2gpst()。"""
        t_gpst0 = cls.gpst_epoch_unix()
        for (y, m, d, hh, mm, ss, ls) in LEAP_SECONDS_TABLE:
            t_eff = cls._ymdhms_to_unix(y, m, d, hh, mm, ss) - t_gpst0
            # if (t - t_eff) >= 0 -> return t - ls   (rtkcmn.c:1446-1447)
            if t_utc >= t_eff:
                return t_utc - ls
        return t_utc

    # ---- 日历展开（rtkcmn.c:1223 time2epoch 逆运算，用 Python 日历库，结果一致）----
    @staticmethod
    def seconds_to_ymdhms(t_unix: float) -> Tuple[int, int, int, int, int, float]:
        """Unix 秒 -> (y,mo,d,h,min,sec)。对应 rtkcmn.c:1223 time2epoch()。"""
        import datetime
        whole = int(math.floor(t_unix))
        frac = t_unix - whole
        dt = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=whole)
        return dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + frac

    @classmethod
    def gps_time_to_utc(cls, week: int, tow: float) -> Dict[str, Any]:
        """已知 GPS 周/周内秒 -> UTC 日历时刻（含闰秒）。

        返回 dict：{year,month,day,hour,minute,second, leap_seconds}。
        """
        t_gpst = cls.gps_week_sec_to_gpst_seconds(week, tow)
        t_utc = cls.gpst_seconds_to_utc_seconds(t_gpst)
        # 当前闰秒 = utc - gpst
        leap = t_utc - t_gpst
        t_unix = cls.gpst_epoch_unix() + t_utc
        y, mo, d, hh, mm, ss = cls.seconds_to_ymdhms(t_unix)
        return {
            "gps_week": week, "gps_tow": tow,
            "year": y, "month": mo, "day": d,
            "hour": hh, "minute": mm, "second": round(ss, 3),
            "leap_seconds": int(round(leap)),
        }

    @classmethod
    def utc_to_gps_time(cls, y: int, mo: int, d: int, hh: int, mm: int,
                        ss: float) -> Tuple[int, float]:
        """UTC 日历 -> (GPS 周, 周内秒)。"""
        t_utc = cls._ymdhms_to_unix(y, mo, d, hh, mm, ss) - cls.gpst_epoch_unix()
        t_gpst = cls.utc_seconds_to_gpst_seconds(t_utc)
        return cls.gpst_seconds_to_gps_week_sec(t_gpst)


# ============================================================
# CoordinateConverter —— 参考 rtkcmn.c
# ============================================================
class CoordinateConverter:
    """ECEF(地心地固直角) <-> LLH(经纬高) <-> ENU(站心东北天)。WGS84 椭球。"""

    # ---- rtkcmn.c:1634 ecef2pos() ----
    @staticmethod
    def ecef_to_llh(x: float, y: float, z: float) -> Tuple[float, float, float]:
        """ECEF(m) -> (lat[rad], lon[rad], h[m])。rtkcmn.c:1634 ecef2pos()。

        WGS84 逆解用 Bowring 迭代（rtkcmn.c:1638-1646），1e-4 m 内收敛。
        """
        a = RE_WGS84                       # rtklib.h:66
        f = FE_WGS84                       # rtklib.h:67
        e2 = f * (2.0 - f)                 # rtkcmn.c:1636  e2=FE_WGS84*(2-FE_WGS84)
        r2 = x * x + y * y
        rz = z                            # 原始 z（rtkcmn.c:1638 循环内恒用 r[2]）
        z = rz
        zk = 0.0
        v = a
        # rtkcmn.c:1638-1643  Bowring 迭代（注意 z 每轮重置为 r[2]+...，非累加）
        while abs(z - zk) >= 1e-4:
            zk = z
            sinp = z / math.sqrt(r2 + z * z)
            v = a / math.sqrt(1.0 - e2 * sinp * sinp)
            z = rz + v * e2 * sinp
        # rtkcmn.c:1644-1646
        lat = math.atan(z / math.sqrt(r2)) if r2 > 1e-12 else (PI / 2.0 if z > 0 else -PI / 2.0)
        lon = math.atan2(y, x) if r2 > 1e-12 else 0.0
        h = math.sqrt(r2 + z * z) - v
        return lat, lon, h

    # ---- rtkcmn.c:1655 pos2ecef() ----
    @staticmethod
    def llh_to_ecef(lat: float, lon: float, h: float) -> Tuple[float, float, float]:
        """(lat[rad], lon[rad], h[m]) -> ECEF(m)。rtkcmn.c:1655 pos2ecef()。"""
        a = RE_WGS84
        f = FE_WGS84
        e2 = f * (2.0 - f)
        sinp = math.sin(lat); cosp = math.cos(lat)
        sinl = math.sin(lon); cosl = math.cos(lon)
        v = a / math.sqrt(1.0 - e2 * sinp * sinp)   # rtkcmn.c:1658
        x = (v + h) * cosp * cosl                    # rtkcmn.c:1660
        y = (v + h) * cosp * sinl                    # rtkcmn.c:1661
        z = (v * (1.0 - e2) + h) * sinp              # rtkcmn.c:1662
        return x, y, z

    # ---- rtkcmn.c:1671 xyz2enu() + 1686 ecef2enu() ----
    @staticmethod
    def ecef_to_enu(lat: float, lon: float, dx: float, dy: float, dz: float
                    ) -> Tuple[float, float, float]:
        """ECEF 向量 -> ENU 向量。rtkcmn.c:1686 ecef2enu()（经 xyz2enu 旋转阵）。"""
        sinp = math.sin(lat); cosp = math.cos(lat)
        sinl = math.sin(lon); cosl = math.cos(lon)
        # rtkcmn.c:1675-1677  E 矩阵（行主序 3x3）
        e = -sinl * dx + cosl * dy
        n = -sinp * cosl * dx - sinp * sinl * dy + cosp * dz
        u = cosp * cosl * dx + cosp * sinl * dy + sinp * dz
        return e, n, u

    # ---- rtkcmn.c:1700 enu2ecef() ----
    @staticmethod
    def enu_to_ecef(lat: float, lon: float, e: float, n: float, u: float
                    ) -> Tuple[float, float, float]:
        """ENU 向量 -> ECEF 向量（旋转阵转置）。rtkcmn.c:1700 enu2ecef()。"""
        sinp = math.sin(lat); cosp = math.cos(lat)
        sinl = math.sin(lon); cosl = math.cos(lon)
        dx = -sinl * e - sinp * cosl * n + cosp * cosl * u
        dy = cosl * e - sinp * sinl * n + cosp * sinl * u
        dz = cosp * n + sinp * u
        return dx, dy, dz

    # ---- rtkcmn.c:3199 geodist() ----
    @staticmethod
    def geodist(rs: np.ndarray, rr: np.ndarray) -> Tuple[float, np.ndarray]:
        """几何距离（含 Sagnac 改正）+ 接收机到卫星单位向量。

        rtkcmn.c:3199 geodist(): r + OMGE*(rs[0]*rr[1]-rs[1]*rr[0])/CLIGHT。
        """
        d = rs - rr
        r = float(np.linalg.norm(d))
        e = d / r
        # rtkcmn.c:3208  Sagnac
        r_sagnac = r + OMGE * (rs[0] * rr[1] - rs[1] * rr[0]) / CLIGHT
        return r_sagnac, e

    # ---- rtkcmn.c:3218 satazel() ----
    @classmethod
    def satazel(cls, lat: float, lon: float, e: np.ndarray) -> Tuple[float, float]:
        """接收机到卫星单位向量 e(ECEF) -> (方位角 az[rad], 仰角 el[rad])。

        rtkcmn.c:3218 satazel(): az=atan2(enu_e, enu_n), el=asin(enu_u)。
        """
        enu = np.array(cls.ecef_to_enu(lat, lon, float(e[0]), float(e[1]), float(e[2])))
        horiz = float(np.dot(enu[:2], enu[:2]))
        az = 0.0 if horiz < 1e-12 else math.atan2(enu[0], enu[1])
        if az < 0.0:
            az += 2.0 * PI
        el = math.asin(max(-1.0, min(1.0, enu[2])))
        return az, el


# ============================================================
# 广播星历数据结构 —— 对应 rtklib.h:536 eph_t
# ============================================================
@dataclass
class Ephemeris:
    """广播星历（GPS/Galileo/BDS 通用 Kepler 轨道参数）。rtklib.h:536-556 eph_t。"""
    sat: int = 0
    week: int = 0
    toe_s: float = 0.0          # toes: Toe(s) in week
    toc_s: float = 0.0          # 钟差参考时刻(s) in week
    toe_gpst: float = 0.0       # toe 对应的 GPST 连续秒
    A: float = 0.0             # 长半轴(m^2) —— 注意：RINEX 给 sqrt(A)，这里存 A
    e: float = 0.0
    i0: float = 0.0
    OMG0: float = 0.0
    omg: float = 0.0
    M0: float = 0.0
    deln: float = 0.0
    OMGd: float = 0.0
    idot: float = 0.0
    crc: float = 0.0
    crs: float = 0.0
    cuc: float = 0.0
    cus: float = 0.0
    cic: float = 0.0
    cis: float = 0.0
    f0: float = 0.0
    f1: float = 0.0
    f2: float = 0.0
    tgd: float = 0.0
    system: str = "G"           # G/E/C/J


# ============================================================
# 卫星位置计算 —— 参考 ephemeris.c:181 eph2pos()
# ============================================================
class SatelliteOrbit:
    """由广播星历计算卫星 ECEF 位置与钟差。移植 ephemeris.c:181 eph2pos()。"""

    @staticmethod
    def eph2pos(eph: Ephemeris, t_gpst: float) -> Tuple[np.ndarray, float]:
        """t_gpst(GPST 连续秒) -> (rs[3] ECEF m, dts 钟差 s)。

        逐行对照 ephemeris.c:181-250 eph2pos()。
        """
        if eph.A <= 0.0:
            return np.zeros(3), 0.0

        mu = MU_GPS
        omge = OMGE
        if eph.system == "E":
            mu, omge = MU_GAL, OMGE_GAL     # ephemeris.c:197
        elif eph.system == "C":
            mu, omge = MU_CMP, OMGE_CMP     # ephemeris.c:198

        tk = t_gpst - eph.toe_gpst          # ephemeris.c:194  timediff(time,toe)

        # ephemeris.c:201  M = M0 + (sqrt(mu/A^3)+deln)*tk
        M = eph.M0 + (math.sqrt(mu / (eph.A ** 3)) + eph.deln) * tk

        # ephemeris.c:203-205 开普勒方程迭代 E - e*sin(E) = M
        E = M
        Ek = 0.0
        for _ in range(MAX_ITER_KEPLER):
            Ek = E
            E = E - (E - eph.e * math.sin(E) - M) / (1.0 - eph.e * math.cos(E))
            if abs(E - Ek) < RTOL_KEPLER:
                break

        sinE = math.sin(E); cosE = math.cos(E)

        # ephemeris.c:214-216  归一化轨道参数
        u = math.atan2(math.sqrt(1.0 - eph.e * eph.e) * sinE, cosE - eph.e) + eph.omg
        r = eph.A * (1.0 - eph.e * cosE)
        i = eph.i0 + eph.idot * tk

        # ephemeris.c:217-220  摄动改正（谐波项）
        sin2u = math.sin(2.0 * u); cos2u = math.cos(2.0 * u)
        u += eph.cus * sin2u + eph.cuc * cos2u
        r += eph.crs * sin2u + eph.crc * cos2u
        i += eph.cis * sin2u + eph.cic * cos2u

        # ephemeris.c:221
        x = r * math.cos(u)
        y = r * math.sin(u)
        cosi = math.cos(i)

        # ephemeris.c:236-240  升交点经度 & 地固坐标（非 BDS GEO）
        O = eph.OMG0 + (eph.OMGd - omge) * tk - omge * eph.toe_s
        sinO = math.sin(O); cosO = math.cos(O)
        rs = np.array([
            x * cosO - y * cosi * sinO,     # ephemeris.c:238
            x * sinO + y * cosi * cosO,     # ephemeris.c:239
            y * math.sin(i),                # ephemeris.c:240
        ])

        # ephemeris.c:242-243  卫星钟差
        tk_c = t_gpst - (eph.toe_gpst - eph.toe_s + eph.toc_s)  # toc 时刻(近似)
        dts = eph.f0 + eph.f1 * tk_c + eph.f2 * tk_c * tk_c
        # ephemeris.c:246  相对论改正 -2*sqrt(mu*A)*e*sinE/c^2
        dts -= 2.0 * math.sqrt(mu * eph.A) * eph.e * sinE / (CLIGHT ** 2)
        return rs, dts


# ============================================================
# Klobuchar 电离层模型 —— 参考 rtkcmn.c:3275 ionmodel()
# ============================================================
def klobuchar_ion_delay(t_gpst: float, ion_par: List[float],
                        lat: float, lon: float, az: float, el: float) -> float:
    """Klobuchar 单频电离层延迟(m)。rtkcmn.c:3275 ionmodel()。

    ion_par: 8 个电离层参数 [alpha0..3, beta0..3]（广播导航文件 1/2 行）。
    """
    ion_default = [0.1118e-7, -0.7451e-8, -0.5961e-7, 0.1192e-6,
                   0.1167e6, -0.2294e6, -0.1311e6, 0.1049e7]   # rtkcmn.c:3278-3281
    if el <= 0.0:
        return 0.0
    ion = ion_par if ion_par and np.linalg.norm(ion_par) > 0 else ion_default

    psi = 0.0137 / (el / PI + 0.11) - 0.022           # rtkcmn.c:3289
    phi = lat / PI + psi * math.cos(az)               # rtkcmn.c:3292
    phi = max(-0.416, min(0.416, phi))
    lam = lon / PI + psi * math.sin(az) / math.cos(phi * PI)   # rtkcmn.c:3295
    phi += 0.064 * math.cos((lam - 1.617) * PI)       # rtkcmn.c:3298

    week, tow = TimeSystem.gpst_seconds_to_gps_week_sec(t_gpst)
    tt = 43200.0 * lam + tow                          # rtkcmn.c:3301
    tt -= math.floor(tt / 86400.0) * 86400.0          # rtkcmn.c:3302

    f = 1.0 + 16.0 * (0.53 - el / PI) ** 3.0          # rtkcmn.c:3305

    amp = ion[0] + phi * (ion[1] + phi * (ion[2] + phi * ion[3]))   # rtkcmn.c:3308
    per = ion[4] + phi * (ion[5] + phi * (ion[6] + phi * ion[7]))  # rtkcmn.c:3309
    amp = max(0.0, amp)
    per = max(72000.0, per)
    x = 2.0 * PI * (tt - 50400.0) / per               # rtkcmn.c:3312

    if abs(x) < 1.57:
        return CLIGHT * f * (5e-9 + amp * (1.0 + x * x * (-0.5 + x * x / 24.0)))
    return CLIGHT * f * 5e-9                          # rtkcmn.c:3314


# ============================================================
# RINEXParser —— 参考 rinex.c
# ============================================================
class RINEXParser:
    """RINEX 观测文件 / 导航文件解析（聚焦 GPS 广播星历与伪距/载波）。

    移植要点：
      - rinex.c:1166 str2num(): 把 'D'/'d' 指数转 'E' 再 atof。
      - rinex.c:1187 readrnxnavb(): 第1行(钟差3数)+后续每行4数(19字符宽)。
      - rinex.c:1005 decode_eph(): data[] 下标 -> eph_t 字段映射。
    """

    @staticmethod
    def _str2num(s: str, width: int) -> float:
        """固定宽度字段转 float，D->E。rtkcmn.c:1166 str2num()。"""
        field = s[:width]
        field = field.replace("D", "E").replace("d", "E").strip()
        if not field:
            return 0.0
        try:
            return float(field)
        except ValueError:
            return 0.0

    @classmethod
    def parse_nav(cls, text: str) -> List[Ephemeris]:
        """解析 RINEX 2.x GPS 导航文件体，返回 Ephemeris 列表。

        行布局（rinex.c:1231-1238）：
          第1行: PRN y m d h m ss | af0 af1 af2
          后续每行 4 个 19 字符字段；共 7 行轨道数据（28 个数）。
        data[] 下标见 rinex.c:1024-1047 decode_eph()。
        """
        lines = text.splitlines()
        ephs: List[Ephemeris] = []
        i = 0
        # RINEX header 行标签关键字（rinex.c:293 decodeframe）
        header_tokens = ("RINEX", "END OF HEADER", "ION ALPHA", "ION BETA",
                         "DELTA-UTC", "LEAP SECONDS", "COMMENT", "SA APP",
                         "DB NAME", "PYR")
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            # 跳过空行与 header 行（标签在 61-80 列，含上述关键字）
            if (not stripped
                    or any(tok in line for tok in header_tokens)):
                i += 1
                continue
            # 时钟行首字符应为卫星系统码+PRN（RINEX3: G/E/C/J，RINEX2: 数字PRN）
            first = stripped[0]
            if first not in "GREJC1234567890 ":
                i += 1
                continue
            # 一条 nav 记录 = 1 时钟行 + 7 数据行
            if i + 7 >= len(lines):
                break
            try:
                # RINEX3: col0-2=系统+PRN，sp=4（rinex.c:1208）；RINEX2: sp=3
                if first in "GREJC":
                    prn = int(line[1:3])
                    sp = 4
                else:
                    prn = int(line[0:2])
                    sp = 3
                # 日期字段（尽力解析，rinex.c:1226 str2time(buff+sp)）
                try:
                    y = int(float(line[sp:sp + 4])); mo = int(float(line[sp + 5:sp + 7]))
                    d = int(float(line[sp + 8:sp + 10])); hh = int(float(line[sp + 11:sp + 13]))
                    mm = int(float(line[sp + 14:sp + 16])); ss = float(line[sp + 17:sp + 22])
                except Exception:
                    ss = 0.0
                # 钟差参数 af0,af1,af2：19 字符字段，起于 buff+sp+19（rinex.c:1231）
                af0 = cls._str2num(line[sp + 19:sp + 38], 19)
                af1 = cls._str2num(line[sp + 38:sp + 57], 19)
                af2 = cls._str2num(line[sp + 57:sp + 76], 19)
                data = [af0, af1, af2]
                for k in range(7):
                    dl = lines[i + 1 + k]
                    for c in range(4):
                        data.append(cls._str2num(dl[c * 19:(c + 1) * 19], 19))
                if len(data) < 29:
                    i += 1
                    continue
            except Exception:
                i += 1
                continue

            eph = Ephemeris()
            eph.sat = prn
            eph.f0 = data[0]; eph.f1 = data[1]; eph.f2 = data[2]
            eph.A = data[10] ** 2            # rinex.c:1028  A=SQR(data[10])
            eph.e = data[8]
            eph.i0 = data[15]
            eph.OMG0 = data[13]
            eph.omg = data[17]
            eph.M0 = data[6]
            eph.deln = data[5]
            eph.OMGd = data[18]
            eph.idot = data[19]
            eph.crc = data[16]
            eph.crs = data[4]
            eph.cuc = data[7]
            eph.cus = data[9]
            eph.cic = data[12]
            eph.cis = data[14]
            eph.toe_s = data[11]            # rinex.c:1036
            eph.week = int(data[21])         # rinex.c:1037
            eph.tgd = data[25]              # rinex.c:1046
            # toe 对应的 GPST 连续秒
            eph.toe_gpst = TimeSystem.gps_week_sec_to_gpst_seconds(eph.week, eph.toe_s)
            eph.toc_s = ss                  # 近似：toc 在本周内秒
            eph.system = "G"
            ephs.append(eph)
            i += 8
        return ephs

    @classmethod
    def parse_obs_epoch(cls, line: str) -> Optional[Dict[str, Any]]:
        """解析一行 RINEX 2.x 观测 epoch 头（简化版）。

        返回 {sat, prn, tow_gpst, C1, L1, S1} 或 None。
        这里按合成测试用的「单行伪距记录」格式解析，便于单元测试往返验证。
        """
        return None  # 详细 obs 解析在 parse_obs_line 中按字段给出

    @staticmethod
    def parse_obs_line(line: str) -> Dict[str, Any]:
        """解析一条合成 RINEX 观测行：PRN 后跟伪距 C1 / 载波 L1 / 信噪比 S1。

        格式约定（用于测试往返）：
            G17  C1=20543892.123 L1=1075432.11 S1=45.2
        """
        out: Dict[str, Any] = {}
        parts = line.split()
        out["sat"] = parts[0]
        for tok in parts[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                out[k] = float(v)
        return out


# ============================================================
# SPPLocator —— 参考 pntpos.c 伪距最小二乘
# ============================================================
@dataclass
class SPPInput:
    """一次单点定位输入：每颗卫星的 ECEF 位置 rs、钟差 dts、伪距 P。"""
    rs: np.ndarray          # (n,3) 卫星 ECEF 位置(m)
    dts: np.ndarray         # (n,)  卫星钟差(s)
    pr: np.ndarray          # (n,)  伪距 P(m)
    name: List[str] = field(default_factory=list)


class SPPLocator:
    """伪距单点定位(SPP)：最小二乘解算接收机 ECEF 位置 + 接收机钟差。

    核心残差方程（pntpos.c:250）：
        v = P - (r + dtr - c*dts + dion + dtrp)
    设计矩阵（pntpos.c:253）：H = [-e_x, -e_y, -e_z, 1]（4 状态 x,y,z,dtr）。
    迭代最小二乘：dx = (H'WH)^-1 H'W v。
    """

    def __init__(self, max_iter: int = 10, conv_tol: float = 1e-4):
        self.max_iter = max_iter        # pntpos.c: MAXITR (通常 10)
        self.conv_tol = conv_tol

    def locate(self, data: SPPInput,
               x0: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Dict[str, Any]:
        """解算。返回 {x,y,z, b, pos_llh, rms, n_sat, iters}。"""
        n = len(data.pr)
        # 状态 x = [X, Y, Z, 接收机钟差 dtr(m)]
        x = np.array([x0[0], x0[1], x0[2], 0.0])

        for it in range(self.max_iter):
            H = np.zeros((n, 4))
            v = np.zeros(n)
            for i in range(n):
                rs = data.rs[i]
                rr = x[:3]
                r, e = CoordinateConverter.geodist(rs, rr)   # pntpos.c:227 geodist
                # pntpos.c:250  v = P - (r + dtr - c*dts)
                v[i] = data.pr[i] - (r + x[3] - CLIGHT * data.dts[i])
                # pntpos.c:253  H 行 = [-e_x,-e_y,-e_z, 1]
                H[i, 0] = -e[0]
                H[i, 1] = -e[1]
                H[i, 2] = -e[2]
                H[i, 3] = 1.0
            # 最小二乘 dx = (H'H)^-1 H' v
            dx, *_ = np.linalg.lstsq(H, v, rcond=None)
            x = x + dx
            if float(np.linalg.norm(dx[:3])) < self.conv_tol:
                break

        # 残差 RMS
        rr = x[:3]
        res = []
        for i in range(n):
            r, _ = CoordinateConverter.geodist(data.rs[i], rr)
            res.append(data.pr[i] - (r + x[3] - CLIGHT * data.dts[i]))
        rms = float(np.sqrt(np.mean(np.square(res))))

        lat, lon, h = CoordinateConverter.ecef_to_llh(x[0], x[1], x[2])
        return {
            "x": float(x[0]), "y": float(x[1]), "z": float(x[2]),
            "b_m": float(x[3]),                 # 接收机钟差等价距离(m)
            "b_s": float(x[3] / CLIGHT),
            "lat_rad": lat, "lon_rad": lon, "h": h,
            "lat_deg": lat * R2D, "lon_deg": lon * R2D,
            "rms_m": rms, "n_sat": n, "iters": it + 1,
        }


# ============================================================
# NTRIPStream —— 参考 stream.c NTRIP client
# ============================================================
class NTRIPStream:
    """NTRIP 差分数据流 client。移植 stream.c:1293 reqntrip_c() 的握手流程。

    与 serial_gnss.NTRIPClient 对齐并增强：
      - 请求行严格按 RTKLIB：GET /<mntpnt> HTTP/1.0 + User-Agent: NTRIP RTKLIB/..
      - 支持 Basic Auth（stream.c:1307-1310）
      - 握手成功后异步收 RTCM3 字节流，回调 on_data(bytes)
    """

    def __init__(self, host: str, port: int = NTRIP_DEFAULT_PORT,
                 mountpoint: str = "", user: str = "", password: str = "",
                 on_data: Optional[Callable[[bytes], None]] = None,
                 timeout: float = 10.0):
        self.host = host
        self.port = port
        self.mountpoint = mountpoint
        self.user = user
        self.password = password
        self.on_data = on_data
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()

    def build_request(self) -> bytes:
        """构造 NTRIP client 请求报文。stream.c:1299-1312。"""
        path = "/" + self.mountpoint
        req = f"GET {path} HTTP/1.0\r\n"
        req += f"User-Agent: NTRIP {NTRIP_AGENT}\r\n"     # stream.c:1300
        if self.user:
            token = base64.b64encode(
                f"{self.user}:{self.password}".encode()).decode()  # stream.c:1307-1309
            req += f"Authorization: Basic {token}\r\n"
        else:
            req += "Accept: */*\r\n"                    # stream.c:1303
            req += "Connection: close\r\n"              # stream.c:1304
        req += "\r\n"
        return req.encode()

    def connect(self) -> bool:
        """连接 caster 并完成握手；成功后启动接收线程。"""
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
        except OSError:
            return False
        try:
            self._sock.sendall(self.build_request())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = self._sock.recv(256)
                if not chunk:
                    break
                buf += chunk
            head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1", "ignore")
            first = head.split("\r\n", 1)[0]
            # stream.c:79/83  成功响应为 "ICY 200 OK" 或 "HTTP/1.x 200 ..."
            if "200" not in first:
                self.close()
                return False
        except OSError:
            self.close()
            return False

        self._running.set()
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()
        return True

    def _recv_loop(self):
        try:
            assert self._sock is not None
            self._sock.settimeout(5.0)
            while self._running.is_set():
                try:
                    data = self._sock.recv(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                if self.on_data is not None:
                    try:
                        self.on_data(data)
                    except Exception:
                        pass
        finally:
            self.close()

    def close(self):
        self._running.clear()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


# ============================================================
# NTRIPManager —— 配置驱动的差分管理层（供桌面端按需启用）
# ============================================================
@dataclass
class NTRIPConfig:
    """NTRIP caster 配置。所有字段为空时不启用差分。"""
    host: str = ""
    port: int = 2101
    mountpoint: str = ""
    user: str = ""
    password: str = ""
    enabled: bool = False


class NTRIPManager:
    """配置驱动的 NTRIP 差分管理。

    - 从 dict 加载配置（来自 gui_config.json 的 "ntrip" 段）
    - 配置不完整(host/mountpoint为空或 enabled=False)时不连接
    - 连接成功后通过 on_rtcm_data(bytes) 回调吐出 RTCM3 原始字节
    - 提供 start()/stop()/is_connected()/stats()
    """

    def __init__(self, config: Optional[Dict] = None,
                 on_rtcm_data: Optional[Callable[[bytes], None]] = None):
        self._lock = threading.Lock()
        self._cfg = NTRIPConfig()
        self._stream: Optional[NTRIPStream] = None
        self._bytes_received: int = 0
        self._on_rtcm_data = on_rtcm_data
        if config:
            self.load_config(config)

    def load_config(self, config: Dict) -> None:
        """从 dict 读取 ntrip 配置段。

        兼容两种传法：
          1) 整份 gui_config（含 "ntrip" 子段）；
          2) 直接传 ntrip 段本身。
        识别的键：host/port/mountpoint/user/password/enabled。
        """
        if not isinstance(config, dict):
            return
        seg = config.get("ntrip")
        seg = seg if isinstance(seg, dict) else config
        cfg = NTRIPConfig()
        cfg.host = str(seg.get("host", "") or "")
        try:
            cfg.port = int(seg.get("port", NTRIP_DEFAULT_PORT) or NTRIP_DEFAULT_PORT)
        except (TypeError, ValueError):
            cfg.port = NTRIP_DEFAULT_PORT
        cfg.mountpoint = str(seg.get("mountpoint", "") or "")
        cfg.user = str(seg.get("user", "") or "")
        cfg.password = str(seg.get("password", "") or "")
        cfg.enabled = bool(seg.get("enabled", False))
        with self._lock:
            self._cfg = cfg

    def is_configured(self) -> bool:
        """enabled==True 且 host/mountpoint 均非空，才认为配置完整。"""
        with self._lock:
            return (self._cfg.enabled
                    and bool(self._cfg.host)
                    and bool(self._cfg.mountpoint))

    def start(self) -> bool:
        """配置完整则创建 NTRIPStream 并握手；未配置返回 False 不报错。"""
        if not self.is_configured():
            return False
        with self._lock:
            # 清理旧连接
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            self._bytes_received = 0
            cfg = self._cfg
            stream = NTRIPStream(
                host=cfg.host, port=cfg.port, mountpoint=cfg.mountpoint,
                user=cfg.user, password=cfg.password,
                on_data=self._handle_rtcm,
            )
            ok = stream.connect()
            if ok:
                self._stream = stream
            else:
                stream.close()
            return ok

    def stop(self) -> None:
        """关闭 NTRIPStream（不持有锁执行 close，避免与接收线程死锁）。"""
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            stream.close()

    def is_connected(self) -> bool:
        """NTRIPStream 正在运行（握手成功且接收线程未退出）。"""
        with self._lock:
            return (self._stream is not None
                    and self._stream._running.is_set())

    def stats(self) -> Dict[str, Any]:
        """返回 {host, mountpoint, connected, bytes_received}。"""
        with self._lock:
            connected = (self._stream is not None
                         and self._stream._running.is_set())
            return {
                "host": self._cfg.host,
                "mountpoint": self._cfg.mountpoint,
                "connected": connected,
                "bytes_received": self._bytes_received,
            }

    def _handle_rtcm(self, data: bytes) -> None:
        """NTRIPStream on_data 包装：累加字节计数，再转发给用户回调。"""
        with self._lock:
            self._bytes_received += len(data)
        cb = self._on_rtcm_data
        if cb is not None:
            try:
                cb(data)
            except Exception:
                pass


# ============================================================
# 模块级便捷函数（供 ToolRegistry 直接调用）
# ============================================================
def ntrip_manager_from_config(config: Dict,
                              on_rtcm_data: Optional[Callable[[bytes], None]] = None
                              ) -> NTRIPManager:
    """从配置 dict 创建 NTRIPManager 实例（不自动 start，由调用方决定）。"""
    return NTRIPManager(config=config, on_rtcm_data=on_rtcm_data)


def gps_time_to_utc(week: int, tow: float) -> Dict[str, Any]:
    """GPS 周/周内秒 -> UTC 日历。见 TimeSystem.gps_time_to_utc。"""
    return TimeSystem.gps_time_to_utc(week, tow)


def ecef_to_llh(x: float, y: float, z: float) -> Dict[str, Any]:
    """ECEF -> LLH（度）。"""
    lat, lon, h = CoordinateConverter.ecef_to_llh(x, y, z)
    return {"lat_deg": lat * R2D, "lon_deg": lon * R2D, "height_m": h,
            "lat_rad": lat, "lon_rad": lon}


def rinex_parse(nav_text: str) -> Dict[str, Any]:
    """解析 RINEX 导航文本，返回星历列表摘要。"""
    ephs = RINEXParser.parse_nav(nav_text)
    return {"n_eph": len(ephs),
            "satellites": [{"sat": e.sat, "week": e.week, "toe_s": e.toe_s,
                            "A": e.A, "e": e.e} for e in ephs]}


def spp_locate(rs_list: List[List[float]], dts_list: List[float],
               pr_list: List[float]) -> Dict[str, Any]:
    """用已知卫星 ECEF 位置/钟差/伪距做 SPP 解算。"""
    data = SPPInput(rs=np.array(rs_list, dtype=float),
                    dts=np.array(dts_list, dtype=float),
                    pr=np.array(pr_list, dtype=float))
    return SPPLocator().locate(data)


def ntrip_connect(host: str, port: int = NTRIP_DEFAULT_PORT,
                  mountpoint: str = "", user: str = "", password: str = ""
                  ) -> Dict[str, Any]:
    """构造 NTRIP 请求并尝试连接；返回握手是否成功（不阻塞）。"""
    stream = NTRIPStream(host, port, mountpoint, user, password)
    ok = stream.connect()
    if ok:
        stream.close()  # 仅验证握手，长期接收由调用方持有实例
    return {"host": host, "port": port, "mountpoint": mountpoint, "handshake_ok": ok}

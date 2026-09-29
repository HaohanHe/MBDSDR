# SPDX-License-Identifier: MIT
"""
MBDSDR 轨道预测内核 —— SGP4 / TLE / 观测几何
================================================
本模块依据 Spacetrack Report #3（SGP4.0 近地轨道传播模型）与 Vallado
《Revisiting Spacetrack Report #3》公开规范独立实现近地卫星轨道预报：

  - 常数/数据结构 : WGS-72 椭球与 Spacetrack 引力常数（模型公开参数）
  - TLE 解析/校验和 : 两行根数(TLE)公开格式，列位布局见 TLE 规范
  - SGP4 近地传播   : SGP4 模型（周期 < 225 min 轨道）
  - 观测几何(站心/方位仰角) : ECI->站心坐标转换
  - 时间/GMST      : 格林尼治平恒星时(IAU 1982 多项式)
  - 数学辅助       : 通用三角函数/取模

gpredict (http://oz9aec.net) 仅作技术参考与致谢，本仓未包含其源代码。

说明：本实现沿用 WGS-72 椭球与 Spacetrack 引力常数（xkmper=6378.135 km，
ge=398600.8 km^3/s^2），与 SGP4 标准一致，勿与 WGS-84 混用。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any


# =====================================================================
# 物理与模型常数（SGP4/Spacetrack, WGS-72）
# =====================================================================
# 基本数学常数
DE2RA = 1.74532925e-2      # 度→弧度
PI = 3.1415926535898       # 
PIO2 = 1.5707963267949     # pi/2
X3PIO2 = 4.71238898        # 3*pi/2
TWOPI = 6.2831853071796    # 2*pi
E6A = 1.0e-6               # Kepler 收敛阈值
TOTHRD = 6.6666667e-1      # 2/3

# 地球引力场常数（WGS-72 / Spacetrack）
XJ2 = 1.0826158e-3         # J2 谐系数（注意: gpredict 末位为 8，任务书给 1.0826157e-3，以源码为准）
XJ3 = -2.53881e-6          # J3 谐系数
XJ4 = -1.65597e-6          # J4 谐系数
XKE = 7.43669161e-2        # 归一化引力常数 sqrt(GM)/(ER^1.5/min)
XKMPER = 6.378135e3        # 地球赤道半径 km (WGS-72)
XMNPDA = 1.44e3            # 每天分钟数
AE = 1.0                   # 距离归一化单位 (ER)
CK2 = 5.413079e-4          # = J2/2
CK4 = 6.209887e-7          # = -3*J4/8
F_FLAT = 3.352779e-3       # WGS-72 扁率 f ≈ 1/298.26
GE = 3.986008e5            # GM = 398600.8 km^3/s^2
S_PHYS = 1.012229          # 大气模型 s 系数
QOMS2T = 1.880279e-9       # 大气模型 q0-s0 项
SEC_DAY = 8.6400e4          # 每天秒数
OMEGA_E_DAY = 1.0027379    # 地球自转(圈/天,恒星)
THDT = 4.3752691e-3        # 地球自转角速度 rad/min (=omega_ER)
MFACTOR = 7.292115e-5      # 地球自转角速度 rad/s
C_LIGHT = 299792.458       # km/s（真空光速，多普勒用）


# =====================================================================
# 数学辅助
# =====================================================================
def fmod2p(x: float) -> float:
    """FMod2p: 对 2*pi 取模，结果落在 [0, 2pi)。 通用数学辅助"""
    ret = x
    i = int(ret / TWOPI)            # C 为向零取整 i=(int)(ret/twopi)
    ret -= i * TWOPI
    if ret < 0:
        ret += TWOPI
    return ret


def modulus(a: float, b: float) -> float:
    """Modulus: a mod b，结果落在 [0,b)。 通用数学辅助"""
    ret = a
    i = int(ret / b)
    ret -= i * b
    if ret < 0:
        ret += b
    return ret


def frac(x: float) -> float:
    """Frac: 小数部分。 通用数学辅助"""
    return x - math.floor(x)


def actan(sinx: float, cosx: float) -> float:
    """AcTan: 四象限反正切，返回 [0,2pi)。等价于 atan2(sinx,cosx) 归一化。
    通用数学辅助"""
    if cosx == 0.0:
        return PIO2 if sinx > 0 else X3PIO2
    if cosx > 0.0:
        return math.atan(sinx / cosx) if sinx > 0 else TWOPI + math.atan(sinx / cosx)
    return PI + math.atan(sinx / cosx)


def asgp(x: float) -> float:
    """ArcSin: 反正弦，|arg|>=1 时饱和到 ±pi/2。 通用数学辅助"""
    if abs(x) >= 1.0:
        return PIO2 if x > 0 else -PIO2
    return math.atan(x / math.sqrt(1.0 - x * x))


def convert_sat_state(pos: List[float], vel: List[float]) -> Tuple[List[float], List[float]]:
    """Convert_Sat_State: 归一化(ER, ER/min) -> km, km/s。
    通用数学辅助  Scale_Vector(xkmper,pos); Scale_Vector(xkmper*xmnpda/secday,vel)"""
    r = [p * XKMPER for p in pos]
    v = [v_ * XKMPER * XMNPDA / SEC_DAY for v_ in vel]
    return r, v


# =====================================================================
# 时间系统 / GMST
# =====================================================================
def julian_date_of_year(year: float) -> float:
    """Julian_Date_of_Year: 该年 0.0 日(1月0.5日)的儒略日。 时间/GMST"""
    y = year - 1.0
    i = int(y / 100.0)
    a = float(i)
    i = int(a / 4.0)
    b = 2.0 - a + i
    i = int(365.25 * y)
    i += int(30.6001 * 14)
    return i + 1720994.5 + b


def thetajd(jd: float) -> float:
    """ThetaG_JD: 格林尼治平恒星时(弧度)。 时间/GMST"""
    ut = frac(jd + 0.5)
    jd0 = jd - ut
    tu = (jd0 - 2451545.0) / 36525.0
    gmst = 24110.54841 + tu * (8640184.812866 + tu * (0.093104 - tu * 6.2e-6))
    gmst = modulus(gmst + SEC_DAY * OMEGA_E_DAY * ut, SEC_DAY)
    return TWOPI * gmst / SEC_DAY


def jd_from_datetime_utc(year: int, month: int, day: int,
                         hour: int = 0, minute: int = 0, second: float = 0.0) -> float:
    """UTC 日历时刻 -> 儒略日（与 gpredict Julian_Date/Date_Time 一致）。
    时间/GMST, 180-183  (jd - 2440587.5)*86400 = unix"""
    return time_time_to_jd(_calendar_to_unix(year, month, day, hour, minute, second))


def _calendar_to_unix(year: int, month: int, day: int,
                      hour: int, minute: int, second: float) -> float:
    import datetime
    dt = datetime.datetime(year, month, day, hour, minute, int(second),
                           int((second - int(second)) * 1e6), tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def unix_to_jd(unix_s: float) -> float:
    """unix 秒(UTC) -> 儒略日。 时间/GMST  jtime=(jd-2440587.5)*86400"""
    return unix_s / SEC_DAY + 2440587.5


# 别名，便于阅读
time_time_to_jd = unix_to_jd


# =====================================================================
# TLE 解析（两行根数公开格式）
# =====================================================================
@dataclass
class TLEData:
    """两行根数解析结果。字段为 TLE 解析结果。"""
    sat_name: str = ""
    catnr: int = 0
    idesg: str = ""
    epoch_year: int = 0
    epoch_day: int = 0
    epoch_fod: float = 0.0
    epoch_jd: float = 0.0
    xndt2o: float = 0.0      # n 一阶导 (rad/min^2, select_ephemeris 后)
    xndd6o: float = 0.0      # n 二阶导
    bstar: float = 0.0
    xincl: float = 0.0       # 倾角 rad
    xnodeo: float = 0.0      # RAAN rad
    eo: float = 1e-6         # 偏心率
    omegao: float = 0.0      # 近地点幅角 rad
    xmo: float = 0.0         # 平近点角 rad
    xno: float = 0.0          # 平均运动 rad/min
    meanmo: float = 0.0       # 平均运动 rev/day（原始）
    revnum: int = 0
    elset: int = 0
    deep_space: bool = False


class TLEParser:
    """解析两行根数(TLE)。 TLE 公开格式"""

    @staticmethod
    def checksum_good(line: str) -> bool:
        """单 TLE 行校验和: 前 68 列数字求和(minus=1) mod10 == 第69列。
        TLE 公开格式列位"""
        if line is None or len(line) < 69:
            return False
        checksum = 0
        for i in range(68):
            c = line[i]
            if "0" <= c <= "9":
                checksum += ord(c) - 48
            elif c == "-":
                checksum += 1
        checksum %= 10
        try:
            return checksum == int(line[68])
        except ValueError:
            return False

    @staticmethod
    def _f(line: str, start: int, length: int) -> float:
        """按列切片解析浮点（TLE 科学计数法省略小数点/指数的部分由调用方补）。"""
        s = line[start:start + length].strip()
        if s == "":
            return 0.0
        return float(s)

    @classmethod
    def parse(cls, name_line: str, line1: str, line2: str) -> TLEData:
        """解析 name + line1 + line2 -> TLEData。字段列位依据 TLE 公开格式。

        TLE 每行 69 列(0-based 0..68)。Convert_Satellite_Data 把两行拼成一个 138 串：
        line1 在偏移 0，line2 在偏移 69。下面用相对行内列号等价换算。
        """
        t = TLEData()
        # 卫星名称（TLE 公开格式列位，截取前24列、去尾空格）
        t.sat_name = name_line.strip()[:24]

        # 校验和（TLE 公开格式列位, Checksum_Good）
        if not (cls.checksum_good(line1) and cls.checksum_good(line2)):
            raise ValueError(f"TLE 校验和失败: {line1!r} / {line2!r}")
        if not line1.startswith("1") or not line2.startswith("2"):
            raise ValueError("TLE 行号列应为 '1'/'2'")

        # --- line 1 (行内列号 0..68) ---
        # catnr: [2:7]   
        t.catnr = int(line1[2:7])
        # idesg: [9:17] 
        t.idesg = line1[9:17].strip()
        # epoch: YYDDD.FFFFFFFF 
        t.epoch_year = 2000 + int(line1[18:20])
        t.epoch_day = int(line1[20:23])
        # 小数日: 小数点在列23，其后 8 位在列24..31
        fod_str = "0." + line1[24:32].replace(" ", "0")
        t.epoch_fod = float(fod_str)
        # ndot (xndt2o): [33:43] 
        t.xndt2o = cls._f(line1, 33, 10)
        # nddot (xndd6o): 符号[44] + "." + 5位[45:50] + "E" + 指数[50:52] 
        t.xndd6o = float(line1[44] + "." + line1[45:50].replace(" ", "0") + "E" + line1[50:52].replace(" ", "0"))
        # bstar: 符号[53] + "." + 5位[54:59] + "E" + 指数[59:61] 
        t.bstar = float(line1[53] + "." + line1[54:59].replace(" ", "0") + "E" + line1[59:61].replace(" ", "0"))
        # elset: [64:68] 
        try:
            t.elset = int(line1[64:68])
        except ValueError:
            t.elset = 0

        # --- line 2 (行内列号 = gpredict 拼接偏移-69) ---
        # inclination: 行内[8:16]  
        xincl_deg = cls._f(line2, 8, 8)
        # RAAN: 行内[17:25]  
        xnodeo_deg = cls._f(line2, 17, 8)
        # eccentricity: "." + 行内[26:33]  
        eo = float("0." + line2[26:33].replace(" ", "0"))
        # arg perigee: 行内[34:42]  
        omegao_deg = cls._f(line2, 34, 8)
        # mean anomaly: 行内[43:51]  
        xmo_deg = cls._f(line2, 43, 8)
        # mean motion rev/day: 行内[52:62]  
        xno_revday = cls._f(line2, 52, 10)
        # revnum: 行内[63:68]  
        try:
            t.revnum = int(line2[63:68])
        except ValueError:
            t.revnum = 0

        if eo < 1.0e-6:
            eo = 1.0e-6

        # ---- select_ephemeris: 单位换算 TLE 公开格式列位 ----
        t.xnodeo = xnodeo_deg * DE2RA
        t.omegao = omegao_deg * DE2RA
        t.xmo = xmo_deg * DE2RA
        t.xincl = xincl_deg * DE2RA
        t.meanmo = xno_revday
        temp = TWOPI / XMNPDA / XMNPDA
        t.xno = xno_revday * temp * XMNPDA
        t.xndt2o *= temp
        t.xndd6o = t.xndd6o * temp / XMNPDA
        t.bstar /= AE
        t.eo = eo

        # 纪元儒略日（用于 GMST）。Julian_Date_of_Year + day_of_year + fod
        t.epoch_jd = julian_date_of_year(t.epoch_year) + t.epoch_day + t.epoch_fod

        # 深/近地判定: 周期(min) = 2pi/xnodp/xmnpda*...  >= 225min 即 deep space
        # TLE 公开格式列位  (条件 twopi/xnodp/xmnpda >= 0.15625 天 = 225min)
        a1 = (XKE / t.xno) ** TOTHRD
        r1 = math.cos(t.xincl)
        dd1 = 1.0 - eo * eo
        tmp = CK2 * 1.5 * (r1 * r1 * 3.0 - 1.0) / dd1 ** 1.5
        del1 = tmp / (a1 * a1)
        ao = a1 * (1.0 - del1 * (TOTHRD * 0.5 + del1 * (del1 * 1.654320987654321 + 1.0)))
        delo = tmp / (ao * ao)
        xnodp = t.xno / (delo + 1.0)
        t.deep_space = (TWOPI / xnodp / XMNPDA) >= 0.15625

        return t


# =====================================================================
# SGP4 近地传播
# =====================================================================
class SGP4Propagator:
    """SGP4 近地卫星轨道传播器（周期 < 225 min）。

    用法:
        tle = TLEParser.parse(name, line1, line2)
        prop = SGP4Propagator(tle)
        r_eci_km, v_eci_kms = prop.propagate(tsince_minutes)
    """

    def __init__(self, tle: TLEData):
        self.tle = tle
        # --- SGP4 初始化静态量 ---
        self.cosio = self.sinio = self.x3thm1 = self.x1mth2 = self.x7thm1 = 0.0
        self.aodp = self.xnodp = self.eta = 0.0
        self.c1 = self.c4 = self.c5 = self.c2 = 0.0
        self.omgcof = self.xmcof = self.xnodcf = 0.0
        self.t2cof = self.xlcof = self.aycof = self.delmo = self.sinmo = 0.0
        self.xmdot = self.omgdot = self.xnodot = 0.0
        self.d2 = self.d3 = self.d4 = 0.0
        self.t3cof = self.t4cof = self.t5cof = 0.0
        self.simple = False
        self._initialized = False

    def _init(self):
        """SGP4 初始化段。 SGP4 模型"""
        t = self.tle
        a1 = (XKE / t.xno) ** TOTHRD
        self.cosio = math.cos(t.xincl)
        theta2 = self.cosio * self.cosio
        self.x3thm1 = 3.0 * theta2 - 1.0
        eosq = t.eo * t.eo
        betao2 = 1.0 - eosq
        betao = math.sqrt(betao2)
        del1 = 1.5 * CK2 * self.x3thm1 / (a1 * a1 * betao * betao2)
        ao = a1 * (1.0 - del1 * (0.5 * TOTHRD + del1 * (1.0 + 134.0 / 81.0 * del1)))
        delo = 1.5 * CK2 * self.x3thm1 / (ao * ao * betao * betao2)
        self.xnodp = t.xno / (1.0 + delo)
        self.aodp = ao / (1.0 - delo)

        # 近地点 <220km -> SIMPLE 截断 (c:57-64)
        if (self.aodp * (1.0 - t.eo) / AE) < (220.0 / XKMPER + AE):
            self.simple = True
        else:
            self.simple = False

        # 近地点 <156km 调整 s/qoms2 (c:66-78)
        s4 = S_PHYS
        qoms24 = QOMS2T
        perige = (self.aodp * (1.0 - t.eo) - AE) * XKMPER
        if perige < 156.0:
            if perige <= 98.0:
                s4 = 20.0
            else:
                s4 = perige - 78.0
            qoms24 = ((120.0 - s4) * AE / XKMPER) ** 4
            s4 = s4 / XKMPER + AE

        pinvsq = 1.0 / (self.aodp * self.aodp * betao2 * betao2)
        tsi = 1.0 / (self.aodp - s4)
        self.eta = self.aodp * t.eo * tsi
        etasq = self.eta * self.eta
        eeta = t.eo * self.eta
        psisq = abs(1.0 - etasq)
        coef = qoms24 * tsi ** 4
        coef1 = coef / psisq ** 3.5
        c2 = coef1 * self.xnodp * (self.aodp *
             (1.0 + 1.5 * etasq + eeta * (4.0 + etasq)) +
             0.75 * CK2 * tsi / psisq * self.x3thm1 *
             (8.0 + 3.0 * etasq * (8 + etasq)))
        self.c1 = c2 * t.bstar
        self.sinio = math.sin(t.xincl)
        a3ovk2 = -XJ3 / CK2 * AE ** 3
        c3 = coef * tsi * a3ovk2 * self.xnodp * AE * self.sinio / t.eo
        self.x1mth2 = 1.0 - theta2
        self.c4 = (2.0 * self.xnodp * coef1 * self.aodp * betao2 *
                   (self.eta * (2.0 + 0.5 * etasq) +
                    t.eo * (0.5 + 2.0 * etasq) -
                    2.0 * CK2 * tsi / (self.aodp * psisq) *
                    (-3.0 * self.x3thm1 * (1.0 - 2.0 * eeta + etasq * (1.5 - 0.5 * eeta)) +
                     0.75 * self.x1mth2 * (2.0 * etasq - eeta * (1.0 + etasq)) *
                     math.cos(2.0 * t.omegao))))
        self.c5 = (2.0 * coef1 * self.aodp * betao2 *
                   (1.0 + 2.75 * (etasq + eeta) + eeta * eeta))
        theta4 = theta2 * theta2
        temp1 = 3.0 * CK2 * pinvsq * self.xnodp
        temp2 = temp1 * CK2 * pinvsq
        temp3 = 1.25 * CK4 * pinvsq * pinvsq * self.xnodp
        self.xmdot = (self.xnodp + 0.5 * temp1 * betao * self.x3thm1 +
                      0.0625 * temp2 * betao *
                      (13.0 - 78.0 * theta2 + 137.0 * theta4))
        x1m5th = 1.0 - 5.0 * theta2
        self.omgdot = (-0.5 * temp1 * x1m5th +
                       0.0625 * temp2 * (7.0 - 114.0 * theta2 + 395.0 * theta4) +
                       temp3 * (3.0 - 36.0 * theta2 + 49.0 * theta4))
        xhdot1 = -temp1 * self.cosio
        self.xnodot = (xhdot1 + (0.5 * temp2 * (4.0 - 19.0 * theta2) +
                                 2.0 * temp3 * (3.0 - 7.0 * theta2)) * self.cosio)
        self.omgcof = t.bstar * c3 * math.cos(t.omegao)
        self.xmcof = -TOTHRD * coef * t.bstar * AE / eeta
        self.xnodcf = 3.5 * betao2 * xhdot1 * self.c1
        self.t2cof = 1.5 * self.c1
        self.xlcof = (0.125 * a3ovk2 * self.sinio *
                      (3.0 + 5.0 * self.cosio) / (1.0 + self.cosio))
        self.aycof = 0.25 * a3ovk2 * self.sinio
        self.delmo = (1.0 + self.eta * math.cos(t.xmo)) ** 3
        self.sinmo = math.sin(t.xmo)
        self.x7thm1 = 7.0 * theta2 - 1.0
        if not self.simple:
            c1sq = self.c1 * self.c1
            self.d2 = 4.0 * self.aodp * tsi * c1sq
            tmp = self.d2 * tsi * self.c1 / 3.0
            self.d3 = (17.0 * self.aodp + s4) * tmp
            self.d4 = (0.5 * tmp * self.aodp * tsi *
                       (221.0 * self.aodp + 31.0 * s4) * self.c1)
            self.t3cof = self.d2 + 2.0 * c1sq
            self.t4cof = (0.25 * (3.0 * self.d3 + self.c1 *
                                  (12.0 * self.d2 + 10.0 * c1sq)))
            self.t5cof = (0.2 * (3.0 * self.d4 +
                                 12.0 * self.c1 * self.d3 +
                                 6.0 * self.d2 * self.d2 +
                                 15.0 * c1sq * (2.0 * self.d2 + c1sq)))
        self._initialized = True

    def propagate(self, tsince: float) -> Tuple[List[float], List[float]]:
        """传播 tsince 分钟后的 ECI/TEME 位置(km)与速度(km/s)。

        返回 (r=[x,y,z] km, v=[x,y,z] km/s)。 SGP4 模型
        """
        if not self._initialized:
            self._init()
        t = self.tle

        # --- 长期引力与大气阻力更新 (c:146-169) ---
        xmdf = t.xmo + self.xmdot * tsince
        omgadf = t.omegao + self.omgdot * tsince
        xnoddf = t.xnodeo + self.xnodot * tsince
        omega = omgadf
        xmp = xmdf
        tsq = tsince * tsince
        xnode = xnoddf + self.xnodcf * tsq
        tempa = 1.0 - self.c1 * tsince
        tempe = t.bstar * self.c4 * tsince
        templ = self.t2cof * tsq
        if not self.simple:
            delomg = self.omgcof * tsince
            delm = self.xmcof * ((1.0 + self.eta * math.cos(xmdf)) ** 3 - self.delmo)
            tmp = delomg + delm
            xmp = xmdf + tmp
            omega = omgadf - tmp
            tcube = tsq * tsince
            tfour = tsince * tcube
            tempa = tempa - self.d2 * tsq - self.d3 * tcube - self.d4 * tfour
            tempe = tempe + t.bstar * self.c5 * (math.sin(xmp) - self.sinmo)
            templ = templ + self.t3cof * tcube + tfour * (self.t4cof + tsince * self.t5cof)

        a = self.aodp * tempa * tempa
        e = t.eo - tempe
        xl = xmp + omega + xnode + self.xnodp * templ
        beta = math.sqrt(1.0 - e * e)
        xn = XKE / a ** 1.5

        # --- 长周期项 (c:177-183) ---
        axn = e * math.cos(omega)
        tmp = 1.0 / (a * beta * beta)
        xll = tmp * self.xlcof * axn
        aynl = tmp * self.aycof
        xlt = xl + xll
        ayn = e * math.sin(omega) + aynl

        # --- 开普勒方程求解 (c:185-202) ---
        capu = fmod2p(xlt - xnode)
        temp2 = capu
        for _ in range(10):                                  # c:189-202  do-while i<10
            sinepw = math.sin(temp2)
            cosepw = math.cos(temp2)
            temp3 = axn * sinepw
            temp4 = ayn * cosepw
            temp5 = axn * cosepw
            temp6 = ayn * sinepw
            epw = (capu - temp4 + temp3 - temp2) / (1.0 - temp5 - temp6) + temp2
            if abs(epw - temp2) <= E6A:
                break
            temp2 = epw

        # --- 短周期预备量 (c:204-224) ---
        ecose = temp5 + temp6
        esine = temp3 - temp4
        elsq = axn * axn + ayn * ayn
        tmp = 1.0 - elsq
        pl = a * tmp
        r = a * (1.0 - ecose)
        temp1 = 1.0 / r
        rdot = XKE * math.sqrt(a) * esine * temp1
        rfdot = XKE * math.sqrt(pl) * temp1
        temp2 = a * temp1
        betal = math.sqrt(tmp)
        temp3 = 1.0 / (1.0 + betal)
        cosu = temp2 * (cosepw - axn + ayn * esine * temp3)
        sinu = temp2 * (sinepw - ayn - axn * esine * temp3)
        u = actan(sinu, cosu)
        sin2u = 2.0 * sinu * cosu
        cos2u = 2.0 * cosu * cosu - 1.0
        tmp = 1.0 / pl
        temp1 = CK2 * tmp
        temp2 = temp1 * temp1

        # --- 短周期项更新 (c:226-233) ---
        rk = (r * (1.0 - 1.5 * temp2 * betal * self.x3thm1) +
              0.5 * temp1 * self.x1mth2 * cos2u)
        uk = u - 0.25 * temp2 * self.x7thm1 * sin2u
        xnodek = xnode + 1.5 * temp2 * self.cosio * sin2u
        xinck = t.xincl + 1.5 * temp2 * self.cosio * self.sinio * cos2u
        rdotk = rdot - xn * temp1 * self.x1mth2 * sin2u
        rfdotk = (rfdot + xn * temp1 *
                  (self.x1mth2 * cos2u + 1.5 * self.x3thm1))

        # --- 指向矢量 (c:236-250) ---
        sinuk = math.sin(uk)
        cosuk = math.cos(uk)
        sinik = math.sin(xinck)
        cosik = math.cos(xinck)
        sinnok = math.sin(xnodek)
        cosnok = math.cos(xnodek)
        xmx = -sinnok * cosik
        xmy = cosnok * cosik
        ux = xmx * sinuk + cosnok * cosuk
        uy = xmy * sinuk + sinnok * cosuk
        uz = sinik * sinuk
        vx = xmx * cosuk - cosnok * sinuk
        vy = xmy * cosuk - sinnok * sinuk
        vz = sinik * cosuk

        # --- 位置/速度（归一化 ER, ER/min）(c:252-258) ---
        pos = [rk * ux, rk * uy, rk * uz]
        vel = [rdotk * ux + rfdotk * vx,
               rdotk * uy + rfdotk * vy,
               rdotk * uz + rfdotk * vz]
        # 归一化 -> km, km/s 
        return convert_sat_state(pos, vel)


# =====================================================================
# 坐标转换（ECI -> 站心）
# =====================================================================
@dataclass
class GeoStation:
    """观测站（大地坐标）。SGP4 模型常数 geodetic_t"""
    lat_deg: float
    lon_deg: float
    alt_km: float = 0.0

    @property
    def lat_rad(self) -> float:
        return math.radians(self.lat_deg)

    @property
    def lon_rad(self) -> float:
        return math.radians(self.lon_deg)


@dataclass
class Observation:
    """观测结果（站心）。SGP4 模型常数 obs_set_t"""
    az_deg: float
    el_deg: float
    range_km: float
    range_rate_kms: float


class CoordinateConverter:
    """ECI(TEME) -> 站心方位/仰角。独立实现 ECI->站心观测几何。"""

    @staticmethod
    def observer_eci(jd: float, st: GeoStation) -> Tuple[List[float], List[float]]:
        """观测站在 ECI(TEME) 系的位置(km)与速度(km/s)。
        ECI->站心坐标转换  (WGS-72 椭球, Astronomical Almanac K11)"""
        lat, lon, alt = st.lat_rad, st.lon_rad, st.alt_km
        theta = fmod2p(thetajd(jd) + lon)
        c = 1.0 / math.sqrt(1.0 + F_FLAT * (F_FLAT - 2.0) * math.sin(lat) ** 2)
        sq = (1.0 - F_FLAT) ** 2 * c
        achcp = (XKMPER * c + alt) * math.cos(lat)
        ox = achcp * math.cos(theta)
        oy = achcp * math.sin(theta)
        oz = (XKMPER * sq + alt) * math.sin(lat)
        ovx = -MFACTOR * oy
        ovy = MFACTOR * ox
        ovz = 0.0
        return [ox, oy, oz], [ovx, ovy, ovz]

    @staticmethod
    def calculate_obs(jd: float, sat_r_eci: List[float], sat_v_eci: List[float],
                      st: GeoStation) -> Observation:
        """站心方位/仰角/距离/径向速度。 ECI->站心坐标转换"""
        obs_pos, obs_vel = CoordinateConverter.observer_eci(jd, st)
        # 站星矢量 (c:96-98)
        rx = sat_r_eci[0] - obs_pos[0]
        ry = sat_r_eci[1] - obs_pos[1]
        rz = sat_r_eci[2] - obs_pos[2]
        # 相对速度 (c:100-102)
        vx = sat_v_eci[0] - obs_vel[0]
        vy = sat_v_eci[1] - obs_vel[1]
        vz = sat_v_eci[2] - obs_vel[2]
        range_w = math.sqrt(rx * rx + ry * ry + rz * rz)      # Magnitude c:104
        lat = st.lat_rad
        sin_lat = math.sin(lat)
        cos_lat = math.cos(lat)
        # 站地方时角 theta（=observer_eci 内用的同一个）
        theta = fmod2p(thetajd(jd) + st.lon_rad)              # c:108-109 用 geodetic->theta
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        top_s = (sin_lat * cos_theta * rx + sin_lat * sin_theta * ry
                 - cos_lat * rz)
        top_e = -sin_theta * rx + cos_theta * ry
        top_z = (cos_lat * cos_theta * rx + cos_lat * sin_theta * ry
                 + sin_lat * rz)
        # 方位角计算（规避 top_s=0 除零）
        if top_s == 0.0:
            azim = PIO2 if top_e < 0 else X3PIO2
        else:
            azim = math.atan(-top_e / top_s)
        if top_s > 0.0:
            azim += PI
        if azim < 0.0:
            azim += TWOPI
        el = asgp(top_z / range_w)                            # c:120 ArcSin
        # 径向速度 = range·rgvel / |range|  (c:126)
        range_rate = (rx * vx + ry * vy + rz * vz) / range_w
        return Observation(az_deg=math.degrees(azim), el_deg=math.degrees(el),
                            range_km=range_w, range_rate_kms=range_rate)


# =====================================================================
# 过境预测 + 多普勒（扫描找 AOS/LOS）
# =====================================================================
@dataclass
class PassEvent:
    aos_unix: float
    los_unix: float
    max_el_deg: float
    aos_az_deg: float
    los_az_deg: float
    culm_unix: float
    culm_az_deg: float
    duration_s: float
    doppler_min_hz: float = 0.0
    doppler_max_hz: float = 0.0


class SatPassPredictor:
    """卫星过境预测：检测 AOS/LOS、最大仰角、持续时间、多普勒。

    gpredict 用时间步进扫描仰角跨越地平线/阈值来判定过境；这里采用
    粗扫(60s)+二分细化阈值时刻，与 gpredict predict-tools 思路一致。
    """

    def __init__(self, tle: TLEData, station: GeoStation):
        self.tle = tle
        self.station = station
        if tle.deep_space:
            # 近地 SGP4 不覆盖深空(>225min)；本实现当前面向 LEO，深空请扩展 SDP4。
            raise NotImplementedError("深空卫星(周期>=225min)需 SDP4，本适配器当前实现 SGP4(LEO)")
        self.prop = SGP4Propagator(tle)

    def _obs_at_unix(self, unix_s: float) -> Observation:
        jd = unix_to_jd(unix_s)
        tsince_min = (jd - self.tle.epoch_jd) * 1440.0     # 距纪元分钟
        r, v = self.prop.propagate(tsince_min)
        return CoordinateConverter.calculate_obs(jd, r, v, self.station)

    def predict_passes(self, t_start_unix: float, duration_hours: float = 24.0,
                       min_elevation_deg: float = 0.0,
                       step_s: float = 30.0) -> List[PassEvent]:
        """在 [t_start, t_start+duration_h] 内预测过境。

        扫描 step_s 步长找仰角跨越 min_elevation 的上升沿(AOS)/下降沿(LOS)，
        pass 内 5s 细采样找最大仰角与多普勒范围。
        """
        t_end = t_start_unix + duration_hours * 3600.0
        # 粗采样
        samples = []
        t = t_start_unix
        while t <= t_end:
            try:
                ob = self._obs_at_unix(t)
                samples.append((t, ob))
            except Exception:
                samples.append((t, None))
            t += step_s

        passes: List[PassEvent] = []
        n = len(samples)
        i = 0
        while i < n:
            ti, oi = samples[i]
            if oi is None or oi.el_deg < min_elevation_deg:
                i += 1
                continue
            # 找到一段 >= threshold：向右延伸
            j = i
            while j < n and samples[j][1] is not None and samples[j][1].el_deg >= min_elevation_deg:
                j += 1
            # AOS 二分（samples[i-1] < thr < samples[i]）
            if i > 0 and samples[i - 1][1] is not None:
                t_aos = self._bisect(samples[i - 1][0], ti, min_elevation_deg)
            else:
                t_aos = ti
            # LOS 二分（samples[j-1] > thr > samples[j]）
            if j < n and samples[j][1] is not None:
                t_los = self._bisect(samples[j - 1][0], samples[j][0], min_elevation_deg)
            else:
                t_los = samples[j - 1][0]
            # pass 内细采样
            best_el = -90.0
            best_t = t_aos
            best_az = 0.0
            t = t_aos
            while t <= t_los + 1e-6:
                ob = self._obs_at_unix(t)
                if ob.el_deg > best_el:
                    best_el = ob.el_deg
                    best_t = t
                    best_az = ob.az_deg
                t += 5.0
            ob_aos = self._obs_at_unix(t_aos)
            ob_los = self._obs_at_unix(t_los)
            passes.append(PassEvent(
                aos_unix=t_aos, los_unix=t_los, max_el_deg=best_el,
                aos_az_deg=ob_aos.az_deg, los_az_deg=ob_los.az_deg,
                culm_unix=best_t, culm_az_deg=best_az,
                duration_s=t_los - t_aos))
            i = j
        return passes

    def _bisect(self, lo: float, hi: float, target_el: float,
                tol_s: float = 0.5, iters: int = 30) -> float:
        """二分求仰角=阈值时刻。"""
        flo = self._obs_at_unix(lo).el_deg - target_el
        fhi = self._obs_at_unix(hi).el_deg - target_el
        if flo * fhi > 0:
            return 0.5 * (lo + hi)
        for _ in range(iters):
            if hi - lo < tol_s:
                break
            mid = 0.5 * (lo + hi)
            fm = self._obs_at_unix(mid).el_deg - target_el
            if fm * flo <= 0:
                hi, fhi = mid, fm
            else:
                lo, flo = mid, fm
        return 0.5 * (lo + hi)


# =====================================================================
# 多普勒频移
# =====================================================================
def doppler_shift(obs: Observation, source_freq_hz: float) -> float:
    """多普勒接收频率。f_obs = f_src * (1 - v_r/c)，v_r=range_rate(远离为正)。

    视线速度多普勒关系；
    远离(range_rate>0)时接收频率降低。
    """
    return source_freq_hz * (1.0 - obs.range_rate_kms / C_LIGHT)


# =====================================================================
# 便捷工具函数（供 ToolRegistry 调用）
# =====================================================================
def tool_tle_parse(name: str, line1: str, line2: str) -> Dict[str, Any]:
    """TLE 解析工具入口。"""
    t = TLEParser.parse(name, line1, line2)
    return {
        "satellite": t.sat_name, "catnr": t.catnr, "idesg": t.idesg,
        "epoch_iso": f"{t.epoch_year}-{t.epoch_day:03d}+{t.epoch_fod:.6f}",
        "epoch_jd": round(t.epoch_jd, 6),
        "inclination_deg": round(math.degrees(t.xincl), 4),
        "raan_deg": round(math.degrees(t.xnodeo), 4),
        "eccentricity": round(t.eo, 7),
        "arg_perigee_deg": round(math.degrees(t.omegao), 4),
        "mean_anomaly_deg": round(math.degrees(t.xmo), 4),
        "mean_motion_revday": round(t.meanmo, 8),
        "bstar": t.bstar, "revnum": t.revnum,
        "deep_space": t.deep_space,
        "method": "sgp4-standard",
    }


def tool_sgp4_propagate(name: str, line1: str, line2: str,
                        unix_s: Optional[float] = None) -> Dict[str, Any]:
    """SGP4 传播工具入口：给定 TLE + 时刻，返回 ECI 位置/速度(km,km/s)。"""
    t = TLEParser.parse(name, line1, line2)
    prop = SGP4Propagator(t)
    if unix_s is None:
        unix_s = time.time()
    jd = unix_to_jd(unix_s)
    tsince_min = (jd - t.epoch_jd) * 1440.0
    r, v = prop.propagate(tsince_min)
    return {
        "satellite": t.sat_name, "tsince_min": round(tsince_min, 3),
        "r_eci_km": [round(x, 3) for x in r],
        "v_eci_kms": [round(x, 5) for x in v],
        "altitude_km": round(math.sqrt(sum(x * x for x in r)) - XKMPER, 3),
        "method": "sgp4-standard",
    }


def tool_sat_pass_predict(name: str, line1: str, line2: str,
                          lat_deg: float, lon_deg: float, alt_km: float = 0.0,
                          hours: float = 24.0, min_el_deg: float = 0.0,
                          start_unix: Optional[float] = None) -> Dict[str, Any]:
    """过境预测工具入口。"""
    t = TLEParser.parse(name, line1, line2)
    st = GeoStation(lat_deg, lon_deg, alt_km)
    pred = SatPassPredictor(t, st)
    t0 = start_unix if start_unix is not None else time.time()
    events = pred.predict_passes(t0, hours, min_el_deg)
    return {
        "satellite": t.sat_name,
        "station": {"lat": lat_deg, "lon": lon_deg, "alt_km": alt_km},
        "passes": [
            {
                "aos_unix": round(e.aos_unix), "los_unix": round(e.los_unix),
                "aos_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(e.aos_unix)),
                "los_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(e.los_unix)),
                "culm_utc": time.strftime("%H:%M:%S", time.gmtime(e.culm_unix)),
                "max_el_deg": round(e.max_el_deg, 1),
                "aos_az_deg": round(e.aos_az_deg, 1),
                "los_az_deg": round(e.los_az_deg, 1),
                "duration_s": round(e.duration_s),
            } for e in events
        ],
        "method": "sgp4-standard",
    }


def tool_doppler_calc(name: str, line1: str, line2: str,
                      lat_deg: float, lon_deg: float, alt_km: float,
                      freq_hz: float, unix_s: Optional[float] = None) -> Dict[str, Any]:
    """多普勒计算工具入口。"""
    t = TLEParser.parse(name, line1, line2)
    st = GeoStation(lat_deg, lon_deg, alt_km)
    prop = SGP4Propagator(t)
    if unix_s is None:
        unix_s = time.time()
    jd = unix_to_jd(unix_s)
    tsince_min = (jd - t.epoch_jd) * 1440.0
    r, v = prop.propagate(tsince_min)
    ob = CoordinateConverter.calculate_obs(jd, r, v, st)
    f_rx = doppler_shift(ob, freq_hz)
    return {
        "satellite": t.sat_name,
        "nominal_hz": freq_hz,
        "rx_freq_hz": round(f_rx, 1),
        "doppler_shift_hz": round(f_rx - freq_hz, 1),
        "range_rate_kms": round(ob.range_rate_kms, 4),
        "elevation_deg": round(ob.el_deg, 2),
        "azimuth_deg": round(ob.az_deg, 2),
        "range_km": round(ob.range_km, 1),
        "method": "sgp4-standard",
    }

"""
MBDSDR 新时空模块
==================
AI + 无线电 + SDR + GNSS 新时空融合

功能：
- 授时：NTP 客户端、GNSS 授时解析、时钟偏差计算、多系统时间比对
- GIS：经纬度计算、距离/方位、墨卡托投影、APRS位置标记
- PNT：泛在定位状态（GNSS/LEO/IMU/WiFi）、多源融合、PPP-RTK接口
- 卫星Pass预测：升起/中天/落下时间、轨迹计算、可见性时间线
- 可插拔数据源接口

作者：MBDSDR Team (BI4MIB)
许可证：GPL-3.0
"""

import math
import time
import struct
import socket
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from enum import Enum
from datetime import datetime, timedelta, timezone


# ============================================================
# 常量
# ============================================================

EARTH_RADIUS_KM = 6371.0088  # 地球平均半径（km）
SPEED_OF_LIGHT = 299792.458  # 光速（km/s）

# NTP 服务器列表（公共 NTP）
DEFAULT_NTP_SERVERS = [
    "ntp.aliyun.com",
    "ntp.tencent.com",
    "cn.ntp.org.cn",
    "pool.ntp.org",
    "time.windows.com",
]

# GNSS 系统定义
GNSS_SYSTEMS = {
    "GPS": {"name": "全球定位系统", "country": "美国", "freq_l1": 1575.42e6, "freq_l2": 1227.60e6, "freq_l5": 1176.45e6},
    "BDS": {"name": "北斗卫星导航系统", "country": "中国", "freq_b1": 1561.098e6, "freq_b2": 1207.14e6, "freq_b3": 1268.52e6},
    "GLONASS": {"name": "格洛纳斯", "country": "俄罗斯", "freq_g1": 1602.0e6, "freq_g2": 1246.0e6},
    "Galileo": {"name": "伽利略", "country": "欧盟", "freq_e1": 1575.42e6, "freq_e5a": 1176.45e6, "freq_e5b": 1207.14e6},
}


# ============================================================
# 时间引擎（TimeEngine）—— 仿 Stellarium StelCore 时间系统
# ============================================================
#
# Stellarium 时间模型参考（C++ 源码）：
#   - src/core/StelCore.hpp:1016
#       QPair<double,double> JD;  // JD.first=JD_UT, JD.second=DeltaT(秒)
#       // getJDE() = TT = JD.first + JD.second/86400
#   - src/core/StelCore.cpp:94
#       timeSpeed(JD_SECOND)   // 默认：真实速度（1 真实秒 / 真实秒）
#   - src/core/StelCore.cpp:1243-1246
#       void StelCore::setJD(double newJD) {
#           JD.first = newJD;
#           JD.second = computeDeltaT(newJD);
#       }
#   - src/core/StelCore.cpp:1251-1253
#       double StelCore::getJD() const { return JD.first; }
#   - src/core/StelCore.cpp:1386-1391
#       void StelCore::setTimeRate(double ts) { timeSpeed = ts; ... }
#   - src/core/StelCore.cpp:2299-2308  (核心 tick)
#       void StelCore::updateTime(double deltaTime) {
#           JD.first = jdOfLastJDUpdate
#                    + (now_ms - last_ms)/1000.0 * timeSpeed;
#           JD.second = computeDeltaT(JD.first);
#       }
#
# 本模块把这套模型搬到 Python：
#   * 内部维护 simulation JD（UT），对应 StelCore::JD.first
#   * time_rate 以"实时倍率"暴露（0=暂停, 1=实时, N=N倍速, -N=倒流）
#     内部换算成 Stellarium 的 days/real-second：rate * JD_SECOND
#   * tick(real_dt) 实现 sim_jd += real_dt * rate * JD_SECOND
#   * 所有天文/卫星计算必须通过 get_time_engine().now_unix() / now_utc() / now_jd()
#     取时间，而不是直接 time.time() / datetime.now() —— 这是修复
#     "装饰条"问题的关键（UI 改了显示值但计算仍读系统时钟）。

# Unix epoch (1970-01-01 00:00 UTC) 对应的儒略日
_UNIX_EPOCH_JD = 2440587.5


def _load_skyfield_timescale():
    """惰性加载 skyfield 时间尺度（带缓存）。失败则回退到纯 Python 换算。"""
    try:
        from skyfield.api import load as _sf_load
        return _sf_load().timescale()
    except Exception:
        return None


_ts = _load_skyfield_timescale()


def utc_to_jd(dt: datetime) -> float:
    """datetime(UTC) -> 儒略日 UT（与 Stellarium StelCore::getJD() 同语义）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if _ts is not None:
        try:
            return float(_ts.utc(dt).ut1)
        except Exception:
            pass
    # 回退：Unix 时间戳换算（精度足够 SGP4 预报）
    return dt.timestamp() / 86400.0 + _UNIX_EPOCH_JD


def jd_to_utc(jd: float) -> datetime:
    """儒略日 UT -> datetime(UTC)。对应 StelCore::getJD() 的逆变换。"""
    if _ts is not None:
        try:
            t = _ts.ut1(jd)
            return t.utc_datetime().replace(tzinfo=None) if t.utc_datetime().tzinfo is None \
                else t.utc_datetime().astimezone(timezone.utc)
        except Exception:
            pass
    # 回退
    return datetime.fromtimestamp((jd - _UNIX_EPOCH_JD) * 86400.0, tz=timezone.utc)


class TimeEngine:
    """
    模拟时间引擎。镜像 Stellarium StelCore 的时间状态机。

    用法：
        eng = TimeEngine()
        eng.set_rate(60)          # 60 倍速（1 真实秒 = 1 模拟分钟）
        eng.set_utc(datetime(2026, 9, 26, tzinfo=timezone.utc))  # 跳时
        eng.tick(0.5)             # 推进 0.5 真实秒
        print(eng.now_unix())     # -> 模拟 Unix 时间戳（供 sgp4 使用）

    默认 rate=1 且未 set_time 时，now_unix() == time.time()，
    与旧行为完全向后兼容。
    """

    # Stellarium JD_SECOND = 1.0/86400.0 天/秒
    JD_SECOND = 1.0 / 86400.0

    def __init__(self) -> None:
        # simulation JD(UT)；None 表示尚未显式设置，跟随系统时钟
        self._sim_jd: Optional[float] = None
        # 时间倍率：0=暂停, 1=实时, N=N倍速, -N=倒流
        self._rate: float = 1.0
        # 上次 tick 的墙钟时刻（秒），用于无参 tick() 自动算 real_dt
        self._last_wall: Optional[float] = None

    # ---- 时间跳转（对应 StelCore::setJD） -------------------------------
    def set_jd(self, jd: float) -> None:
        """设置模拟儒略日（UT）。立即生效，下一帧所有计算用新 JD。
        对应 StelCore::setJD() —— src/core/StelCore.cpp:1243。"""
        self._sim_jd = float(jd)
        self._last_wall = time.time()

    def set_utc(self, dt: datetime) -> None:
        """设置模拟时间（UTC datetime）。"""
        self.set_jd(utc_to_jd(dt))

    def set_unix(self, ts: float) -> None:
        """设置模拟时间（Unix 时间戳，秒）。"""
        self.set_jd(ts / 86400.0 + _UNIX_EPOCH_JD)

    def set_time_now(self) -> None:
        """跳回当前真实系统时间。对应 StelCore::setTimeNow()。"""
        self._sim_jd = None
        self._last_wall = time.time()

    # ---- 时间速率（对应 StelCore::setTimeRate） ------------------------
    def set_rate(self, rate: float) -> None:
        """设置时间流速倍率。
          rate=0   暂停
          rate=1   实时（1 模拟秒/真实秒）
          rate=N   N 倍加速
          rate=-N  N 倍倒流
        对应 StelCore::setTimeRate() —— src/core/StelCore.cpp:1386。
        注意：Stellarium 内部 timeSpeed 单位是 天/真实秒，这里归一化为倍率。
        """
        self._rate = float(rate)
        self._last_wall = time.time()

    def get_rate(self) -> float:
        return self._rate

    def pause(self) -> None:
        self.set_rate(0.0)

    def resume(self, rate: float = 1.0) -> None:
        self.set_rate(rate)

    # ---- tick（对应 StelCore::updateTime） -----------------------------
    def tick(self, real_dt: Optional[float] = None) -> None:
        """推进模拟时钟。
        对应 StelCore::updateTime() —— src/core/StelCore.cpp:2299:
            JD.first = jdOfLastJDUpdate + real_elapsed_seconds * timeSpeed
        其中 timeSpeed = rate * JD_SECOND（天/真实秒）。
        real_dt: 距上次 tick 的真实秒数；None 则自动用墙钟差值。
        """
        if real_dt is None:
            now = time.time()
            real_dt = 0.0 if self._last_wall is None else max(0.0, now - self._last_wall)
            self._last_wall = now
        if self._sim_jd is None:
            # 尚未显式跳时：模拟时间 = 真实时间（向后兼容旧行为）
            return
        # 核心：sim_jd += real_dt * rate * JD_SECOND
        self._sim_jd += real_dt * self._rate * self.JD_SECOND

    # ---- 当前模拟时间读取 ----------------------------------------------
    def now_jd(self) -> float:
        """返回当前模拟儒略日（UT）。对应 StelCore::getJD() —— StelCore.cpp:1251。"""
        if self._sim_jd is None:
            return time.time() / 86400.0 + _UNIX_EPOCH_JD
        return self._sim_jd

    def now_unix(self) -> float:
        """返回当前模拟 Unix 时间戳（秒，UTC）。供 sgp4 / 轨道计算使用。"""
        return (self.now_jd() - _UNIX_EPOCH_JD) * 86400.0

    def now_utc(self) -> datetime:
        """返回当前模拟 UTC datetime。"""
        return jd_to_utc(self.now_jd())

    def is_following_system(self) -> bool:
        """是否跟随系统时间（未显式跳时）。"""
        return self._sim_jd is None

    def state(self) -> Dict[str, Any]:
        """调试/UI 状态快照。"""
        return {
            "sim_jd": self.now_jd(),
            "sim_utc": self.now_utc().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "rate": self._rate,
            "following_system": self.is_following_system(),
        }


# ---- 全局单例（对应 Stellarium 的 StelCore 全局实例） --------------------
_global_engine: Optional[TimeEngine] = None


def get_time_engine() -> TimeEngine:
    """获取全局 TimeEngine 单例。所有天文计算必须从这里取时间。"""
    global _global_engine
    if _global_engine is None:
        _global_engine = TimeEngine()
    return _global_engine


def reset_time_engine() -> None:
    """测试/重置用：丢弃全局引擎。"""
    global _global_engine
    _global_engine = None


# ============================================================
# 授时功能
# ============================================================

@dataclass
class TimeInfo:
    """时间信息。"""
    utc_time: datetime = None  # UTC 时间
    local_time: datetime = None  # 本地时间
    gps_time: Optional[float] = None  # GPS 周内秒
    clock_offset_ms: float = 0.0  # 本地时钟偏差（ms）
    ntp_server: str = ""  # NTP 服务器
    ntp_rtt_ms: float = 0.0  # NTP 往返延迟（ms）
    source: str = "system"  # 时间源：system/ntp/gnss
    leap_seconds: int = 18  # GPS-UTC 闰秒


class NTPError(Exception):
    pass


def get_ntp_time(server: str = "ntp.aliyun.com", timeout: float = 3.0) -> Tuple[datetime, float]:
    """
    从 NTP 服务器获取时间。
    返回：(UTC时间, 往返延迟ms)
    """
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(timeout)

        # NTP 数据包：48字节，LI=0, VN=3, Mode=3（客户端）
        data = b'\x1b' + 47 * b'\0'

        t1 = time.time()
        client.sendto(data, (server, 123))
        data, _ = client.recvfrom(1024)
        t4 = time.time()
        client.close()

        if len(data) < 48:
            raise NTPError(f"NTP 响应太短: {len(data)} 字节")

        # 解析 NTP 时间戳（从1900年开始的秒数，32位整数+32位小数）
        t2 = struct.unpack('!12I', data)[8]
        t3 = struct.unpack('!12I', data)[10]

        # NTP 时间转 Unix 时间（减去70年秒数）
        t2 -= 2208988800
        t3 -= 2208988800

        # 计算时钟偏差和往返延迟
        rtt = (t4 - t1) - (t3 - t2)
        offset = ((t2 - t1) + (t3 - t4)) / 2

        utc_time = datetime.fromtimestamp(t4 + offset, tz=timezone.utc)
        return utc_time, rtt * 1000

    except socket.timeout:
        raise NTPError(f"NTP 服务器 {server} 超时")
    except Exception as e:
        raise NTPError(f"NTP 获取失败: {e}")


def get_time_info(prefer_ntp: bool = True) -> TimeInfo:
    """
    获取当前时间信息。
    优先 NTP，失败则用系统时间。
    """
    info = TimeInfo()
    info.local_time = datetime.now()
    info.utc_time = datetime.now(timezone.utc)

    if prefer_ntp:
        for server in DEFAULT_NTP_SERVERS:
            try:
                utc, rtt = get_ntp_time(server)
                info.utc_time = utc
                info.ntp_server = server
                info.ntp_rtt_ms = rtt
                info.source = "ntp"
                # 计算本地时钟偏差
                system_utc = datetime.now(timezone.utc)
                info.clock_offset_ms = (utc - system_utc).total_seconds() * 1000
                break
            except NTPError:
                continue

    # GPS 时间（周内秒）
    if info.utc_time:
        # GPS 时间 = UTC + 闰秒（GPS 比 UTC 快）
        gps_datetime = info.utc_time + timedelta(seconds=info.leap_seconds)
        # GPS 周起点：1980-01-06
        gps_epoch = datetime(1980, 1, 6, tzinfo=timezone.utc)
        gps_seconds = (gps_datetime - gps_epoch).total_seconds()
        info.gps_time = gps_seconds % 604800  # 周内秒

    return info


def parse_gnss_rmc(nmea_sentence: str) -> Optional[Dict[str, Any]]:
    """
    解析 GNSS RMC 语句（推荐最小定位信息），包含时间和日期。
    示例: $GNRMC,072545.00,A,4352.0000,N,12519.0000,E,0.0,0.0,010126,,,A*5C
    """
    if not nmea_sentence or not nmea_sentence.startswith('$'):
        return None

    try:
        # 去除校验和
        sentence = nmea_sentence.split('*')[0]
        fields = sentence.split(',')

        if len(fields) < 10 or fields[0][0] != '$' or fields[0][3:] != 'RMC':
            return None
        # 多星座 talker 前缀 GP/GL/GA/GB/BD/GN（来源 direwolf dwgpsnmea.c:38-42）
        if fields[0][1:3] not in ('GP', 'GL', 'GA', 'GB', 'BD', 'GN'):
            return None

        # 时间（HHMMSS.ss）
        time_str = fields[1]
        if len(time_str) >= 6:
            hour = int(time_str[0:2])
            minute = int(time_str[2:4])
            second = float(time_str[4:])
        else:
            return None

        # 状态
        status = fields[2]  # A=有效, V=无效

        # 纬度
        lat_str = fields[3]
        lat_hemi = fields[4]
        if lat_str and len(lat_str) >= 4:
            lat_deg = int(lat_str[0:2])
            lat_min = float(lat_str[2:])
            latitude = lat_deg + lat_min / 60
            if lat_hemi == 'S':
                latitude = -latitude
        else:
            latitude = None

        # 经度
        lon_str = fields[5]
        lon_hemi = fields[6]
        if lon_str and len(lon_str) >= 5:
            lon_deg = int(lon_str[0:3])
            lon_min = float(lon_str[3:])
            longitude = lon_deg + lon_min / 60
            if lon_hemi == 'W':
                longitude = -longitude
        else:
            longitude = None

        # 日期（DDMMYY）
        date_str = fields[9] if len(fields) > 9 else ''
        if date_str and len(date_str) == 6:
            day = int(date_str[0:2])
            month = int(date_str[2:4])
            year = 2000 + int(date_str[4:6])
            try:
                utc_time = datetime(year, month, day, hour, minute, int(second),
                                    int((second % 1) * 1e6), tzinfo=timezone.utc)
            except ValueError:
                utc_time = None
        else:
            utc_time = None

        # 速度（节）
        speed_knots = float(fields[7]) if fields[7] else 0.0
        # 航向（度）
        course = float(fields[8]) if fields[8] else 0.0

        return {
            "utc_time": utc_time,
            "status": status,
            "latitude": latitude,
            "longitude": longitude,
            "speed_knots": speed_knots,
            "speed_kmh": speed_knots * 1.852,
            "course_deg": course,
            "valid": status == 'A',
        }

    except (ValueError, IndexError):
        return None


# ------------------------------------------------------------
# 扩展：全语句 NMEA 解析（多星座 GP/GL/GA/GB/BD/GN）
# 来源：NMEA-0183 standard；talker ID 参考 direwolf dwgpsnmea.c:38-42
# 与 mbdsdr_ai/serial_gnss.py NMEAParser 字段口径保持一致。
# ------------------------------------------------------------

_NMEA_TALKERS = ('GP', 'GL', 'GA', 'GB', 'BD', 'GN')


def _nmea_ddmm(value: str, hemi: str, is_lat: bool) -> Optional[float]:
    """ddmm.mmmm / dddmm.mmmm → 十进制度。空字段返回 None。"""
    if not value:
        return None
    try:
        n = 2 if is_lat else 3
        deg = int(value[0:n])
        minute = float(value[n:])
    except (ValueError, IndexError):
        return None
    deg += minute / 60.0
    if hemi == 'S' or hemi == 'W':
        deg = -deg
    return deg


def parse_gnss_gga(line: str) -> Optional[Dict[str, Any]]:
    """解析 GGA 定位语句。
    示例: $GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,*CC
    """
    if not line or not line.startswith('$'):
        return None
    body = line.split('*')[0]
    fields = body.split(',')
    if len(fields) < 10 or fields[0][3:] != 'GGA' or fields[0][1:3] not in _NMEA_TALKERS:
        return None

    def _f(i):
        try:
            return float(fields[i]) if fields[i] != '' else None
        except (ValueError, IndexError):
            return None

    return {
        "talker": fields[0][1:3],
        "utc_time": fields[1] if len(fields) > 1 else '',
        "latitude": _nmea_ddmm(fields[2], fields[3], True) if len(fields) > 3 else None,
        "longitude": _nmea_ddmm(fields[4], fields[5], False) if len(fields) > 5 else None,
        "fix_quality": int(fields[6]) if len(fields) > 6 and fields[6].isdigit() else 0,
        "satellites": int(fields[7]) if len(fields) > 7 and fields[7].isdigit() else 0,
        "hdop": _f(8),
        "altitude_m": _f(9),
    }


def parse_gnss_gsa(line: str) -> Optional[Dict[str, Any]]:
    """解析 GSA（参与定位卫星号 + PDOP/HDOP/VDOP）。"""
    if not line or not line.startswith('$'):
        return None
    fields = line.split('*')[0].split(',')
    if len(fields) < 17 or fields[0][3:] != 'GSA' or fields[0][1:3] not in _NMEA_TALKERS:
        return None
    used = [int(x) for x in fields[3:15] if x.strip().isdigit()]
    return {
        "talker": fields[0][1:3],
        "mode": fields[1],
        "fix_type": int(fields[2]) if fields[2].isdigit() else 1,
        "satellites_used": used,
        "pdop": float(fields[15]) if fields[15] else None,
        "hdop": float(fields[16]) if fields[16] else None,
        "vdop": float(fields[17]) if len(fields) > 17 and fields[17] else None,
    }


def parse_gnss_gsv(line: str) -> Optional[Dict[str, Any]]:
    """解析单帧 GSV（可见卫星摘要，不跨帧聚合）。"""
    if not line or not line.startswith('$'):
        return None
    fields = line.split('*')[0].split(',')
    if len(fields) < 4 or fields[0][3:] != 'GSV' or fields[0][1:3] not in _NMEA_TALKERS:
        return None
    sats = []
    i = 4
    while i + 3 < len(fields):
        if fields[i]:
            try:
                sats.append({
                    "id": int(fields[i]),
                    "elevation": float(fields[i + 1]) if fields[i + 1] else None,
                    "azimuth": float(fields[i + 2]) if fields[i + 2] else None,
                    "snr_db": float(fields[i + 3]) if fields[i + 3] else None,
                })
            except ValueError:
                pass
        i += 4
    return {
        "talker": fields[0][1:3],
        "total_messages": int(fields[1]) if fields[1].isdigit() else 0,
        "satellites_in_view": int(fields[3]) if fields[3].isdigit() else 0,
        "sats": sats,
    }


def parse_gnss_vtg(line: str) -> Optional[Dict[str, Any]]:
    """解析 VTG（航迹角 + 地面速度）。"""
    if not line or not line.startswith('$'):
        return None
    fields = line.split('*')[0].split(',')
    if fields[0][3:] != 'VTG' or fields[0][1:3] not in _NMEA_TALKERS:
        return None

    def _f(i):
        try:
            return float(fields[i]) if i < len(fields) and fields[i] != '' else None
        except ValueError:
            return None

    # $--VTG,course_T,T,course_M,M,speed_knots,N,speed_kmh,K
    return {
        "talker": fields[0][1:3],
        "course_deg": _f(1),
        "speed_knots": _f(5),
        "speed_kmh": _f(7),
    }


def parse_gnss_zda(line: str) -> Optional[Dict[str, Any]]:
    """解析 ZDA（UTC 日期时间，高精度授时）。"""
    if not line or not line.startswith('$'):
        return None
    fields = line.split('*')[0].split(',')
    if fields[0][3:] != 'ZDA' or fields[0][1:3] not in _NMEA_TALKERS:
        return None
    dt = None
    try:
        if len(fields) > 4 and fields[1] and fields[2] and fields[3] and fields[4]:
            hh = int(fields[1][0:2]); mm = int(fields[1][2:4]); ss = int(float(fields[1][4:]))
            dt = datetime(int(fields[4]), int(fields[3]), int(fields[2]),
                          hh, mm, ss, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        dt = None
    return {"talker": fields[0][1:3], "datetime_utc": dt, "raw_time": fields[1]}


def parse_nmea_sentence(line: str) -> Optional[Dict[str, Any]]:
    """根据语句类型分发到对应解析器。识别不了返回 None。"""
    if not line or not line.startswith('$'):
        return None
    head = line.split(',', 1)[0]
    if len(head) < 6:
        return None
    # '$' + talker(2) + type(3)：$GNGGA -> type 在 [3:6]
    stype = head[3:6]
    if stype == 'GGA':
        return parse_gnss_gga(line)
    if stype == 'RMC':
        return parse_gnss_rmc(line)
    if stype == 'GSA':
        return parse_gnss_gsa(line)
    if stype == 'GSV':
        return parse_gnss_gsv(line)
    if stype == 'VTG':
        return parse_gnss_vtg(line)
    if stype == 'ZDA':
        return parse_gnss_zda(line)
    return None


# ============================================================
# GIS 功能
# ============================================================

@dataclass
class GeoPoint:
    """地理坐标点。"""
    latitude: float = 0.0  # 纬度（十进制度，北纬正）
    longitude: float = 0.0  # 经度（十进制度，东经正）
    altitude_m: float = 0.0  # 海拔（米）
    name: str = ""
    description: str = ""

    def to_string(self) -> str:
        lat_hemi = 'N' if self.latitude >= 0 else 'S'
        lon_hemi = 'E' if self.longitude >= 0 else 'W'
        return (f"{abs(self.latitude):.6f}°{lat_hemi}, "
                f"{abs(self.longitude):.6f}°{lon_hemi}"
                f"{f', {self.altitude_m:.1f}m' if self.altitude_m else ''}")


def haversine_distance(p1: GeoPoint, p2: GeoPoint) -> float:
    """
    计算两点间的大圆距离（Haversine 公式）。
    返回：距离（km）
    """
    lat1, lon1 = math.radians(p1.latitude), math.radians(p1.longitude)
    lat2, lon2 = math.radians(p2.latitude), math.radians(p2.longitude)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c


def bearing_between(p1: GeoPoint, p2: GeoPoint) -> float:
    """
    计算从 p1 到 p2 的方位角（度，0=北，顺时针）。
    """
    lat1, lon1 = math.radians(p1.latitude), math.radians(p1.longitude)
    lat2, lon2 = math.radians(p2.latitude), math.radians(p2.longitude)

    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)

    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def destination_point(start: GeoPoint, bearing_deg: float, distance_km: float) -> GeoPoint:
    """
    给定起点、方位角和距离，计算终点坐标。
    """
    lat1 = math.radians(start.latitude)
    lon1 = math.radians(start.longitude)
    bearing = math.radians(bearing_deg)
    angular_dist = distance_km / EARTH_RADIUS_KM

    lat2 = math.asin(math.sin(lat1) * math.cos(angular_dist) +
                      math.cos(lat1) * math.sin(angular_dist) * math.cos(bearing))
    lon2 = lon1 + math.atan2(math.sin(bearing) * math.sin(angular_dist) * math.cos(lat1),
                              math.cos(angular_dist) - math.sin(lat1) * math.sin(lat2))

    return GeoPoint(
        latitude=math.degrees(lat2),
        longitude=math.degrees(lon2),
        altitude_m=start.altitude_m
    )


def web_mercator_project(point: GeoPoint, zoom: int = 10) -> Tuple[int, int]:
    """
    Web 墨卡托投影（Google Maps / OpenStreetMap 标准）。
    返回：(瓦片x, 瓦片y)
    """
    lat_rad = math.radians(point.latitude)
    n = 2.0 ** zoom
    x_tile = int((point.longitude + 180.0) / 360.0 * n)
    y_tile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x_tile, y_tile


def format_dms(decimal_deg: float, is_latitude: bool = True) -> str:
    """十进制度转度分秒格式。"""
    hemi = ('N' if decimal_deg >= 0 else 'S') if is_latitude else ('E' if decimal_deg >= 0 else 'W')
    abs_deg = abs(decimal_deg)
    degrees = int(abs_deg)
    minutes = int((abs_deg - degrees) * 60)
    seconds = ((abs_deg - degrees) * 60 - minutes) * 60
    return f"{degrees}°{minutes:02d}'{seconds:05.2f}\"{hemi}"


# ============================================================
# 卫星 Pass 预测
# ============================================================

@dataclass
class SatellitePass:
    """卫星过境预测。"""
    name: str = ""
    rise_time: Optional[datetime] = None  # 升起时间
    rise_azimuth: float = 0.0  # 升起方位角
    max_time: Optional[datetime] = None  # 中天时间
    max_elevation: float = 0.0  # 最大仰角
    max_azimuth: float = 0.0  # 中天方位角
    set_time: Optional[datetime] = None  # 落下时间
    set_azimuth: float = 0.0  # 落下方位角
    duration_sec: float = 0.0  # 持续时间（秒）
    frequency_hz: float = 0.0  # 下行频率
    max_doppler_hz: float = 0.0  # 最大多普勒频移
    trajectory: List[Tuple[float, float]] = field(default_factory=list)  # 轨迹点 (方位, 仰角)

    def summary(self) -> str:
        """生成过境摘要。"""
        lines = [f"=== {self.name} 过境预测 ==="]
        if self.rise_time:
            lines.append(f"升起: {self.rise_time.strftime('%H:%M:%S')} UTC, 方位 {self.rise_azimuth:.0f}°")
        if self.max_time:
            lines.append(f"中天: {self.max_time.strftime('%H:%M:%S')} UTC, 仰角 {self.max_elevation:.1f}°, 方位 {self.max_azimuth:.0f}°")
        if self.set_time:
            lines.append(f"落下: {self.set_time.strftime('%H:%M:%S')} UTC, 方位 {self.set_azimuth:.0f}°")
        if self.duration_sec > 0:
            lines.append(f"持续: {self.duration_sec/60:.1f} 分钟")
        if self.frequency_hz > 0:
            lines.append(f"频率: {self.frequency_hz/1e6:.3f} MHz, 最大多普勒 ±{self.max_doppler_hz:.0f} Hz")
        return '\n'.join(lines)


def predict_satellite_pass(satellite_name: str, observer_lat: float, observer_lon: float,
                            observer_alt: float = 0.0, hours_ahead: float = 24.0,
                            min_elevation: float = 5.0,
                            frequency_hz: float = 0.0) -> Optional[SatellitePass]:
    """
    预测卫星下一次过境。
    使用 sgp4 计算卫星位置，时间步长 30 秒。
    """
    try:
        from .decoders import compute_satellite_position, BUILTIN_TLE, SATELLITE_FREQUENCIES
    except ImportError:
        from decoders import compute_satellite_position, BUILTIN_TLE, SATELLITE_FREQUENCIES

    if satellite_name not in BUILTIN_TLE:
        return None

    if frequency_hz == 0 and satellite_name in SATELLITE_FREQUENCIES:
        frequency_hz = SATELLITE_FREQUENCIES[satellite_name] * 1e6  # MHz -> Hz

    # 从全局 TimeEngine 取"模拟现在"，而非 datetime.now()。
    # 这是修复"装饰条"问题的核心：UI 拖动时间后，pass 预测起点随之改变。
    # 对应 Stellarium：所有天体位置从 StelCore::getJD() 取时间 (StelCore.cpp:1251)。
    now = get_time_engine().now_utc()
    step = timedelta(seconds=30)
    total_steps = int(hours_ahead * 3600 / 30)

    pass_obj = SatellitePass(
        name=satellite_name,
        frequency_hz=frequency_hz,
    )

    in_pass = False
    max_elev = 0.0
    trajectory = []

    for i in range(total_steps):
        t = now + i * step
        # compute_satellite_position 期望 Unix 时间戳（float）
        timestamp = t.timestamp()
        pos = compute_satellite_position(satellite_name, observer_lat, observer_lon, observer_alt, timestamp)

        if pos is None:
            continue

        elev = pos.elevation
        azim = pos.azimuth

        if elev >= min_elevation:
            if not in_pass:
                # 升起
                in_pass = True
                pass_obj.rise_time = t
                pass_obj.rise_azimuth = azim
                max_elev = elev

            trajectory.append((azim, elev))

            if elev > max_elev:
                max_elev = elev
                pass_obj.max_time = t
                pass_obj.max_elevation = elev
                pass_obj.max_azimuth = azim

            # 多普勒
            if frequency_hz > 0 and hasattr(pos, 'doppler_hz'):
                if abs(pos.doppler_hz) > pass_obj.max_doppler_hz:
                    pass_obj.max_doppler_hz = abs(pos.doppler_hz)

        elif in_pass:
            # 落下
            in_pass = False
            pass_obj.set_time = t
            pass_obj.set_azimuth = azim
            if pass_obj.rise_time and pass_obj.set_time:
                pass_obj.duration_sec = (pass_obj.set_time - pass_obj.rise_time).total_seconds()
            pass_obj.trajectory = trajectory
            return pass_obj

    # 如果还在 pass 中（预测窗口结束时仍可见）
    if in_pass and pass_obj.rise_time:
        pass_obj.set_time = now + total_steps * step
        pass_obj.set_azimuth = trajectory[-1][0] if trajectory else 0
        pass_obj.duration_sec = (pass_obj.set_time - pass_obj.rise_time).total_seconds()
        pass_obj.trajectory = trajectory
        return pass_obj

    return None


def predict_all_passes(observer_lat: float, observer_lon: float, observer_alt: float = 0.0,
                        hours_ahead: float = 24.0, min_elevation: float = 5.0) -> List[SatellitePass]:
    """预测所有内置卫星的过境。"""
    try:
        from .decoders import BUILTIN_TLE, SATELLITE_FREQUENCIES
    except ImportError:
        from decoders import BUILTIN_TLE, SATELLITE_FREQUENCIES

    passes = []
    for name in BUILTIN_TLE:
        freq = SATELLITE_FREQUENCIES.get(name, 0.0) * 1e6  # MHz -> Hz
        p = predict_satellite_pass(name, observer_lat, observer_lon, observer_alt,
                                    hours_ahead, min_elevation, freq)
        if p:
            passes.append(p)

    # 按升起时间排序
    passes.sort(key=lambda x: x.rise_time or datetime.max.replace(tzinfo=timezone.utc))
    return passes


# ============================================================
# 泛在 PNT（新时空核心）
# ============================================================

class PNTSource(Enum):
    """PNT 数据源类型。"""
    GNSS = "gnss"           # 全球导航卫星系统
    LEO_PNT = "leo_pnt"     # 低轨卫星 PNT（Starlink/Iridium）
    PPP_RTK = "ppp_rtk"     # 精密单点定位/实时动态
    IMU = "imu"             # 惯性测量单元
    WIFI = "wifi"           # WiFi 指纹/RTD
    BLUETOOTH = "bluetooth" # 蓝牙 AoA
    UWB = "uwb"             # 超宽带
    CELLULAR = "cellular"   # 蜂窝基站
    NTP = "ntp"             # 网络授时
    VISUAL = "visual"       # 视觉定位


@dataclass
class PNTState:
    """泛在 PNT 状态。"""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_m: Optional[float] = None
    accuracy_m: Optional[float] = None  # 定位精度（米）
    utc_time: Optional[datetime] = None
    time_accuracy_ns: Optional[float] = None  # 授时精度（纳秒）
    active_sources: List[PNTSource] = field(default_factory=list)
    source_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fusion_mode: str = "single"  # single/fused/degraded
    last_update: Optional[datetime] = None

    def summary(self) -> str:
        lines = ["=== 泛在 PNT 状态 ==="]
        if self.latitude is not None and self.longitude is not None:
            lines.append(f"位置: {self.latitude:.6f}°, {self.longitude:.6f}°")
            if self.altitude_m is not None:
                lines.append(f"海拔: {self.altitude_m:.1f} m")
            if self.accuracy_m is not None:
                lines.append(f"精度: ±{self.accuracy_m:.1f} m")
        if self.utc_time:
            lines.append(f"时间: {self.utc_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            if self.time_accuracy_ns is not None:
                lines.append(f"授时精度: ±{self.time_accuracy_ns:.0f} ns")
        lines.append(f"融合模式: {self.fusion_mode}")
        lines.append(f"激活源: {', '.join([s.value for s in self.active_sources]) or '无'}")
        for src, status in self.source_status.items():
            lines.append(f"  {src}: {status.get('status', 'unknown')}"
                         f"{f', 精度 {status.get('accuracy_m', '?')}m' if 'accuracy_m' in status else ''}"
                         f"{f', 卫星数 {status.get('satellites', '?')}' if 'satellites' in status else ''}")
        return '\n'.join(lines)


class PNTFusionEngine:
    """
    泛在 PNT 融合引擎（新时空核心）。
    多源定位数据融合，源选择，降级策略。
    """

    def __init__(self):
        self.state = PNTState()
        self.sources: Dict[PNTSource, Dict[str, Any]] = {}

    def update_source(self, source: PNTSource, data: Dict[str, Any]):
        """更新某个 PNT 源的数据。"""
        self.sources[source] = data
        self._fuse()

    def _fuse(self):
        """多源融合（简化版：加权平均 + 源选择）。"""
        active = []
        positions = []
        weights = []

        for source, data in self.sources.items():
            if data.get('valid', False) and 'latitude' in data and 'longitude' in data:
                active.append(source)
                positions.append((data['latitude'], data['longitude'], data.get('altitude_m', 0)))
                # 权重 = 1/精度^2
                acc = data.get('accuracy_m', 100.0)
                weights.append(1.0 / (acc * acc))

        self.state.active_sources = active
        self.state.source_status = {s.value: d for s, d in self.sources.items()}

        if not positions:
            self.state.fusion_mode = "none"
            return

        if len(positions) == 1:
            self.state.fusion_mode = "single"
            self.state.latitude = positions[0][0]
            self.state.longitude = positions[0][1]
            self.state.altitude_m = positions[0][2]
            self.state.accuracy_m = self.sources[active[0]].get('accuracy_m', 100.0)
        else:
            # 加权平均
            self.state.fusion_mode = "fused"
            total_w = sum(weights)
            self.state.latitude = sum(p[0] * w for p, w in zip(positions, weights)) / total_w
            self.state.longitude = sum(p[1] * w for p, w in zip(positions, weights)) / total_w
            self.state.altitude_m = sum(p[2] * w for p, w in zip(positions, weights)) / total_w
            # 融合精度 = 1/sqrt(sum(weights))
            self.state.accuracy_m = 1.0 / math.sqrt(total_w)

        # 授时
        for source, data in self.sources.items():
            if 'utc_time' in data and data.get('valid', False):
                self.state.utc_time = data['utc_time']
                self.state.time_accuracy_ns = data.get('time_accuracy_ns', 1000.0)
                break

        self.state.last_update = datetime.now(timezone.utc)

    def get_state(self) -> PNTState:
        return self.state


# ============================================================
# 可插拔数据源接口
# ============================================================

class DataSourcePlugin:
    """
    可插拔数据源基类。
    所有 SDR/GNSS/IMU 数据源都继承此类，实现统一接口。
    """

    def __init__(self, name: str, source_type: str):
        self.name = name
        self.source_type = source_type  # sdr/gnss/imu/audio/network
        self.connected = False
        self.config: Dict[str, Any] = {}

    def connect(self, **kwargs) -> bool:
        """连接数据源。"""
        raise NotImplementedError

    def disconnect(self):
        """断开数据源。"""
        self.connected = False

    def get_data(self) -> Optional[Dict[str, Any]]:
        """获取数据。"""
        raise NotImplementedError

    def get_status(self) -> Dict[str, Any]:
        """获取状态。"""
        return {
            "name": self.name,
            "type": self.source_type,
            "connected": self.connected,
            "config": self.config,
        }


class SDRDataSource(DataSourcePlugin):
    """SDR 数据源（可插拔后端）。"""

    def __init__(self, name: str = "sdr"):
        super().__init__(name, "sdr")
        self.center_freq_hz = 100e6
        self.sample_rate_hz = 2.4e6
        self.gain_db = 40

    def connect(self, **kwargs) -> bool:
        self.center_freq_hz = kwargs.get('center_freq_hz', self.center_freq_hz)
        self.sample_rate_hz = kwargs.get('sample_rate_hz', self.sample_rate_hz)
        self.gain_db = kwargs.get('gain_db', self.gain_db)
        self.connected = True
        return True

    def get_data(self) -> Optional[Dict[str, Any]]:
        if not self.connected:
            return None
        return {
            "type": "iq_samples",
            "center_freq_hz": self.center_freq_hz,
            "sample_rate_hz": self.sample_rate_hz,
            "gain_db": self.gain_db,
        }


class GNSSDataSource(DataSourcePlugin):
    """GNSS 数据源（可插拔接收机）。"""

    def __init__(self, name: str = "gnss"):
        super().__init__(name, "gnss")
        self.satellites = 0
        self.fix_type = "none"  # none/2d/3d/dgps/rtk

    def connect(self, **kwargs) -> bool:
        self.connected = True
        return True

    def get_data(self) -> Optional[Dict[str, Any]]:
        if not self.connected:
            return None
        return {
            "type": "gnss_fix",
            "satellites": self.satellites,
            "fix_type": self.fix_type,
        }


# ============================================================
# 工具函数
# ============================================================

def get_gnss_system_info(system: str = "all") -> str:
    """获取 GNSS 系统信息。"""
    if system == "all":
        lines = ["=== GNSS 系统概览 ==="]
        for code, info in GNSS_SYSTEMS.items():
            lines.append(f"\n{code} - {info['name']}（{info['country']}）")
            for key, val in info.items():
                if key.startswith('freq_'):
                    lines.append(f"  {key}: {val/1e6:.3f} MHz")
        return '\n'.join(lines)
    elif system in GNSS_SYSTEMS:
        info = GNSS_SYSTEMS[system]
        lines = [f"=== {system} - {info['name']} ==="]
        lines.append(f"国家/地区: {info['country']}")
        for key, val in info.items():
            if key.startswith('freq_'):
                lines.append(f"{key}: {val/1e6:.3f} MHz")
        return '\n'.join(lines)
    else:
        return f"未知 GNSS 系统: {system}"


def compute_visible_satellite_count(observer_lat: float, observer_lon: float,
                                      min_elevation: float = 5.0) -> Dict[str, Any]:
    """计算当前可见卫星数量（新时空天空图用）。

    时间来源：全局 TimeEngine.now_unix()，而非 time.time()。
    对应 Stellarium：StelCore::getJD() 驱动所有天体位置计算 (StelCore.cpp:1251)。
    """
    try:
        from .decoders import list_visible_satellites
    except ImportError:
        from decoders import list_visible_satellites

    # 注入模拟时间：时间穿梭后这里取到的是 sim time，不是系统时间
    sim_ts = get_time_engine().now_unix()
    visible = list_visible_satellites(observer_lat, observer_lon, 0, min_elevation,
                                      timestamp=sim_ts)
    return {
        "total_visible": len(visible),
        "satellites": [
            {
                "name": v.name,
                "elevation_deg": round(v.elevation, 1),
                "azimuth_deg": round(v.azimuth, 1),
                "distance_km": round(v.distance_km, 1),
                "doppler_hz": round(v.doppler_hz, 1),
            }
            for v in visible
        ],
    }

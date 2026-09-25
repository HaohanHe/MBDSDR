"""
MBDSDR AI - 真实串口 GNSS 接入（骨架）
======================================

把真实 GNSS 模块（USB-TTL / USB-CDC，输出 NMEA-0183）接到本软件：

- ``NMEAParser``      : 解析 GGA/RMC/GSA/GSV/VTG/ZDA/GLL/GST/TXT 全语句，兼容多星座
                         talker 前缀 GP/GL/GA/GB/BD/GN（含北斗 BDGGA/GBGSV），
                         GSV 多帧按 talker 聚合，带 NMEA XOR 校验和验证。
- ``SerialGNSSReader``: 后台线程读串口、逐行解析、维护最新 fix；``auto_detect()``
                        扫描 Windows COM* / Linux /dev/ttyUSB*,/dev/ttyACM* 与
                        常见波特率，读到合法 NMEA 即锁定；断线自动热插拔重连。
                        无设备返回 None 不崩溃。
- ``NTRIPClient``     : 连 NTRIP caster（HTTP GET + Basic Auth），把 RTCM3 原始字节
                        通过回调吐出。接口打通即可，不在此解算 RTCM。

红线：无真实定位数据时 ``get_fix()`` 返回 ``source="none"``、坐标为 None，
绝不造假坐标。

许可：GPL-3.0。本模块为从零实现的 NMEA-0183 标准解析，参考：
- 来源项目 direwolf  repos/direwolf/src/dwgpsnmea.c:38-42 （talker ID 含义：
    GP=GPS / GL=GLONASS / GA=Galileo / GB=BeiDou / GN=组合）
- NMEA-0183 standard field layout（GGA/RMC/GSA/GSV/VTG/ZDA 字段顺序）
- RTKLIB str2str（src/stream.c）NTRIP client：HTTP GET mountpoint + Basic Auth，
    收到 200 后持续转发 RTCM3 字节流。
"""

from __future__ import annotations

import os
import threading
import time
import socket
import base64
import math
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, List, Tuple

try:
    import serial  # pyserial 3.5+
    from serial.tools import list_ports
    _SERIAL_AVAILABLE = True
except Exception:  # pragma: no cover - 无 pyserial 时退化，不影响 import
    serial = None  # type: ignore
    list_ports = None  # type: ignore
    _SERIAL_AVAILABLE = False


# 常见 GNSS 波特率（来源：NMEA-0183 / 各模块默认波特率表）。
# ATGM336H / UBlox NEO-M8N 默认 9600，故 9600 排首位；RTK 模块常配 38400/115200。
DEFAULT_BAUDRATES = [9600, 38400, 115200, 57600, 4800]

# auto_detect 探测窗口参数：
# - 每个 端口×波特率 组合至少读 _AUTODETECT_PORT_WINDOW 秒（GNSS 模块上电 / CH340 枚举有延迟）
# - 窗口内累计收到至少 _AUTODETECT_MIN_NMEA 条“$ 开头且校验和正确”的语句才算命中
# - 所有组合扫描总时长硬上限 _AUTODETECT_TOTAL_BUDGET 秒，避免 UI 启动卡死
_AUTODETECT_PORT_WINDOW = 1.5
_AUTODETECT_MIN_NMEA = 3
_AUTODETECT_TOTAL_BUDGET = 15.0
_AUTODETECT_READ_CHUNK = 0.2  # 单次 readline 阻塞上限（秒），便于在窗口内循环计数

# GSV 多帧聚合：同一 talker 的一帧序列若超过 _GSV_AGG_TIMEOUT 秒未收齐最后一帧，
# 丢弃不完整缓冲，避免天空图把半截帧当成完整卫星列表而闪烁。
_GSV_AGG_TIMEOUT = 2.0

# 多星座 talker 前缀（来源 direwolf dwgpsnmea.c:38-42；补充 BD=北斗部分厂商私有前缀）
TALKER_IDS = ("GP", "GL", "GA", "GB", "BD", "GN")

# NMEA 语句类型（去掉 2 位 talker 后的 3 字母类型）
KNOWN_SENTENCE_TYPES = ("GGA", "RMC", "GSA", "GSV", "VTG", "ZDA",
                        "GLL", "GST", "TXT")

# auto_detect 优先探测的 GNSS 关键字（出现在 description/manufacturer 中即优先）
_GNSS_KEYWORDS = (
    "gps", "gnss", "u-blox", "ublox", "neo-m", "neo-6", "neo-7", "neo-8",
    "m8n", "m8t", "m8g", "max-m", "lea-", "atgm", "quectel", "lc29",
    "ag3335", "ag3325", "mt333", "sirf", "simcom", "l76", "cam-m8",
)

# 常见 USB-TTL 芯片关键字（仅用于端口识别信息展示，不影响解析）
_USB_TTL_CHIPS = ("ch340", "ch341", "cp2102", "cp210", "ft232", "ft231x",
                  "pl2303", "pl230", "ch9102")


# ============================================================
# NMEA 解析
# ============================================================

def nmea_checksum(body: str) -> int:
    """计算 NMEA 校验和：'$' 与 '*' 之间所有字符逐字节 XOR。

    （来源：NMEA-0183 standard；direwolf dwgpsnmea.c 同样的 XOR 算法）
    """
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return cs & 0xFF


def _ddmm_to_deg(value: str, hemi: str, is_latitude: bool) -> Optional[float]:
    """NMEA 角度 ddmm.mmmm（纬度）/ dddmm.mmmm（经度）→ 十进制度。

    南纬 S / 西经 W 取负。空字段返回 None。
    """
    if not value:
        return None
    try:
        if is_latitude:
            deg = int(value[0:2])
            minute = float(value[2:])
        else:
            deg = int(value[0:3])
            minute = float(value[3:])
    except (ValueError, IndexError):
        return None
    deg = deg + minute / 60.0
    if hemi == "S" or hemi == "W":
        deg = -deg
    return deg


class NMEAParser:
    """解析单行 NMEA-0183 语句，成功返回 dict，失败返回 None。

    每条语句 dict 至少包含 ``talker``、``sentence``（如 'GGA'）；
    具体字段见各 parse_* 实现。

    GSV 多帧聚合：内部按 ``(talker, total_messages)`` 缓冲各帧，收到最后一帧时合并返回
    完整 sats 列表（dict 带 ``aggregated=True``）；中间帧返回 None。
    若某 talker 的一帧序列超过 ``_GSV_AGG_TIMEOUT`` 秒仍未收齐最后一帧，
    丢弃不完整缓冲（避免返回半截卫星列表导致天空图闪烁）。
    """

    def __init__(self):
        # GSV 多帧缓冲：key = (talker, total_messages)，value =
        #   {"frames": [已收到的 frame dict, ...], "ts": 收到首帧的 monotonic 时间}
        self._gsv_buf: Dict[Tuple[str, int], Dict[str, Any]] = {}

    def _gsv_sweep_stale(self, now: Optional[float] = None) -> None:
        """丢弃超过 _GSV_AGG_TIMEOUT 仍未收齐的 GSV 缓冲（防半截帧残留）。"""
        now = time.time() if now is None else now
        stale = [k for k, v in self._gsv_buf.items()
                 if now - v.get("ts", now) > _GSV_AGG_TIMEOUT]
        for k in stale:
            self._gsv_buf.pop(k, None)

    def parse(self, line: str) -> Optional[Dict[str, Any]]:
        line = (line or "").strip()
        if not line.startswith("$"):
            return None

        # 校验和：$...*HH
        if "*" not in line:
            return None
        body, checksum_hex = line[1:].split("*", 1)
        checksum_hex = checksum_hex.strip()
        try:
            expected = int(checksum_hex, 16)
        except ValueError:
            return None
        if nmea_checksum(body) != expected:
            return None

        fields = body.split(",")
        head = fields[0]  # e.g. GNGGA / GPGGA / BDGGA
        if len(head) < 5:
            return None

        talker = head[0:2]
        stype = head[2:5]
        if talker not in TALKER_IDS:
            return None

        try:
            if stype == "GGA":
                return self._parse_gga(talker, fields)
            if stype == "RMC":
                return self._parse_rmc(talker, fields)
            if stype == "GSA":
                return self._parse_gsa(talker, fields)
            if stype == "GSV":
                return self._parse_gsv(talker, fields)
            if stype == "VTG":
                return self._parse_vtg(talker, fields)
            if stype == "ZDA":
                return self._parse_zda(talker, fields)
            if stype == "GLL":
                return self._parse_gll(talker, fields)
            if stype == "GST":
                return self._parse_gst(talker, fields)
            if stype == "TXT":
                return self._parse_txt(talker, fields)
        except (ValueError, IndexError):
            return None
        return None

    # ---- GGA: 定位数据（经纬度/高度/卫星数/HDOP） ----
    # $GNGGA,hhmmss.ss,llll.ll,a,yyyyy.yy,a,q,nn,hdop,alt,M,geoid,M,,*CC
    def _parse_gga(self, talker: str, f: List[str]) -> Dict[str, Any]:
        def _f(i: int) -> Optional[float]:
            try:
                return float(f[i]) if f[i] != "" else None
            except (ValueError, IndexError):
                return None

        return {
            "talker": talker, "sentence": "GGA",
            "utc_time": f[1] if len(f) > 1 else "",
            "latitude": _ddmm_to_deg(f[2], f[3], True) if len(f) > 3 else None,
            "longitude": _ddmm_to_deg(f[4], f[5], False) if len(f) > 5 else None,
            "fix_quality": int(f[6]) if len(f) > 6 and f[6].isdigit() else 0,  # 0=无,1=GPS,2=DGPS,4=RTK固定,5=RTK浮动
            "satellites": int(f[7]) if len(f) > 7 and f[7].isdigit() else 0,
            "hdop": _f(8),
            "altitude_m": _f(9),
        }

    # ---- RMC: 推荐最小定位（时间/日期/速度/航向/状态） ----
    # $GNRMC,hhmmss.ss,A,llll.ll,a,yyyyy.yy,a,knots,course,ddmmyy,,,a*CC
    def _parse_rmc(self, talker: str, f: List[str]) -> Dict[str, Any]:
        def _f(i: int) -> Optional[float]:
            try:
                return float(f[i]) if f[i] != "" else None
            except (ValueError, IndexError):
                return None

        status = f[2] if len(f) > 2 else "V"
        return {
            "talker": talker, "sentence": "RMC",
            "utc_time": f[1] if len(f) > 1 else "",
            "status": status,                 # A=有效 V=无效
            "valid": status == "A",
            "latitude": _ddmm_to_deg(f[3], f[4], True) if len(f) > 4 else None,
            "longitude": _ddmm_to_deg(f[5], f[6], False) if len(f) > 6 else None,
            "speed_knots": _f(7),
            "speed_kmh": (_f(7) or 0.0) * 1.852,
            "course_deg": _f(8),
            "date": f[9] if len(f) > 9 else "",
        }

    # ---- GSA: 精度因子 + 参与定位的卫星号/定位模式 ----
    # $GNGSA,A,1,nn,nn,...,1.0,1.0,1.0*CC  (mode1 M/A, mode2 1=no/2=2D/3=3D)
    def _parse_gsa(self, talker: str, f: List[str]) -> Dict[str, Any]:
        sat_ids = [x for x in f[3:15] if x.strip() != ""]
        mode2 = int(f[2]) if len(f) > 2 and f[2].isdigit() else 1
        return {
            "talker": talker, "sentence": "GSA",
            "mode": f[1] if len(f) > 1 else "",       # M=手动 A=自动
            "fix_type": mode2,                          # 1=无 2=2D 3=3D
            "satellites_used": [int(x) for x in sat_ids if x.isdigit()],
            "pdop": float(f[15]) if len(f) > 15 and f[15] else None,
            "hdop": float(f[16]) if len(f) > 16 and f[16] else None,
            "vdop": float(f[17]) if len(f) > 17 and f[17] else None,
        }

    # ---- GSV: 可见卫星（可能多帧，按 talker+总帧数 聚合） ----
    # $xxGSV,total_messages,message_number,satellites_in_view,sv,elev,az,snr,...*CC
    # 每个星座（GP=GPS / GB|BD=北斗 / GL=GLONASS / GA=Galileo）独立发多帧 GSV。
    # 聚合 key = (talker, total_messages)：
    #   - message_number==1            → 该 talker 新序列开始，清空旧缓冲；
    #   - message_number<total          → 追加进缓冲，暂不返回（中间帧）；
    #   - message_number==total         → 收齐，合并全部缓冲帧后返回完整 sats。
    # 若一帧序列超过 _GSV_AGG_TIMEOUT 秒未收齐，sweep 丢弃半截缓冲，绝不返回部分帧。
    # 北斗 GBGSV 卫星 PRN 可能 >32，按整数正常解析。
    def _parse_gsv(self, talker: str, f: List[str]) -> Optional[Dict[str, Any]]:
        sats = []
        # 每 4 字段一组：id, elev, az, snr
        i = 4
        while i + 3 < len(f):
            sid, elev, az, snr = f[i], f[i + 1], f[i + 2], f[i + 3]
            if sid:
                sats.append({
                    "id": int(sid) if sid.lstrip("-").isdigit() else sid,
                    "elevation": float(elev) if elev else None,
                    "azimuth": float(az) if az else None,
                    "snr_db": float(snr) if snr else None,
                })
            i += 4
        total = int(f[1]) if len(f) > 1 and f[1].isdigit() else 0
        num = int(f[2]) if len(f) > 2 and f[2].isdigit() else 0
        frame = {
            "talker": talker, "sentence": "GSV",
            "total_messages": total,
            "message_number": num,
            "satellites_in_view": int(f[3]) if len(f) > 3 and f[3].isdigit() else 0,
            "sats": sats,
            "aggregated": False,
        }
        # 先清扫过期的半截缓冲（可能某 talker 掉帧后再也收不到末帧）
        self._gsv_sweep_stale()
        # 单帧（total<=1）直接返回，不进聚合缓冲
        if total <= 1:
            return frame
        # 多帧：按 (talker, total) 分组缓冲
        key: Tuple[str, int] = (talker, total)
        now = time.time()
        if num <= 1:
            # 新一帧序列开始：重置该 talker 该总帧数的缓冲（防上轮丢帧脏数据）
            self._gsv_buf[key] = {"frames": [frame], "ts": now}
        else:
            entry = self._gsv_buf.get(key)
            if entry is None:
                # 错过了第 1 帧（例如读取中途启动）：以当前帧为新起点继续收，
                # 收齐末帧后合并手上已有帧，不空返也不报错。
                entry = {"frames": [], "ts": now}
                self._gsv_buf[key] = entry
            entry["frames"].append(frame)
        if num >= total:
            # 收到最后一帧：合并该 talker 缓冲的全部帧为完整 sats 列表
            all_sats: List[Dict[str, Any]] = []
            buffered = self._gsv_buf.pop(key, {"frames": [frame]})["frames"]
            for fr in buffered:
                all_sats.extend(fr.get("sats", []))
            agg = dict(frame)
            agg["sats"] = all_sats
            agg["aggregated"] = True
            agg["message_number"] = num
            agg["total_messages"] = total
            return agg
        # 中间帧：数据未齐，暂不返回
        return None

    # ---- GLL: 地理坐标（经纬度 + UTC 时间 + 状态） ----
    # $GNGLL,llll.ll,a,yyyyy.yy,a,hhmmss.ss,a[,m]*CC
    def _parse_gll(self, talker: str, f: List[str]) -> Dict[str, Any]:
        status = f[6] if len(f) > 6 else "V"
        return {
            "talker": talker, "sentence": "GLL",
            "latitude": _ddmm_to_deg(f[1], f[2], True) if len(f) > 2 else None,
            "longitude": _ddmm_to_deg(f[3], f[4], False) if len(f) > 4 else None,
            "utc_time": f[5] if len(f) > 5 else "",
            "valid": status == "A",
            "status": status,
        }

    # ---- GST: GNSS 伪距噪声统计（误差椭圆/各轴标准差） ----
    # $GNGST,hhmmss.ss,rms,major,minor,orient,lat_sig,lon_sig,alt_sig*CC
    # 空字段一律 None。
    def _parse_gst(self, talker: str, f: List[str]) -> Dict[str, Any]:
        def _f(i: int) -> Optional[float]:
            try:
                return float(f[i]) if len(f) > i and f[i] != "" else None
            except ValueError:
                return None
        return {
            "talker": talker, "sentence": "GST",
            "utc_time": f[1] if len(f) > 1 else "",
            "rms_std": _f(2),
            "major_sigma": _f(3),
            "minor_sigma": _f(4),
            "orient": _f(5),
            "lat_sigma": _f(6),
            "lon_sigma": _f(7),
            "alt_sigma": _f(8),
        }

    # ---- TXT: u-blox/厂商文本语句（BDTXT 等），不崩溃即可 ----
    # $--TXT,...任意逗号分隔文本...*CC
    def _parse_txt(self, talker: str, f: List[str]) -> Dict[str, Any]:
        # 把 f[1:] 全部拼成文本（TXT 载荷本身可能含逗号）
        text = ",".join(f[1:]) if len(f) > 1 else ""
        return {
            "talker": talker, "sentence": "TXT",
            "text": text,
        }

    # ---- VTG: 航迹角 + 地面速度 ----
    # $GNVTG,c,T,m,M,k,K,N*CC
    def _parse_vtg(self, talker: str, f: List[str]) -> Dict[str, Any]:
        def _f(i: int) -> Optional[float]:
            try:
                return float(f[i]) if f[i] != "" else None
            except (ValueError, IndexError):
                return None
        # VTG 标准：$--VTG,course_T,T,course_M,M,speed_knots,N,speed_kmh,K
        # body 已去掉 '$'，fields[0]=GNVTG，故 course=f[1], knots=f[5], kmh=f[7]
        return {
            "talker": talker, "sentence": "VTG",
            "course_deg": _f(1),
            "speed_kmh": _f(7),
            "speed_knots": _f(5),
        }

    # ---- ZDA: 日期/时间（含闰秒，高精度授时用） ----
    # $GNZDA,hhmmss.ss,dd,mm,yyyy,ltzh,ltzs*CC
    def _parse_zda(self, talker: str, f: List[str]) -> Dict[str, Any]:
        dt = None
        try:
            if len(f) > 4 and f[1] and f[2] and f[3] and f[4]:
                hh = int(f[1][0:2]); mm = int(f[1][2:4]); ss = int(float(f[1][4:]))
                dt = datetime(int(f[4]), int(f[3]), int(f[2]),
                              hh, mm, ss, tzinfo=timezone.utc)
        except (ValueError, IndexError):
            dt = None
        return {
            "talker": talker, "sentence": "ZDA",
            "datetime_utc": dt,
            "raw_time": f[1] if len(f) > 1 else "",
        }


# ============================================================
# 串口读取
# ============================================================

class SerialGNSSReader:
    """后台线程读串口 NMEA，维护最新 fix 状态。

    用法::

        r = SerialGNSSReader()
        info = r.auto_detect()          # 找不到返回 None
        if info: r.start(info["port"], info["baudrate"])
        fix = r.get_fix()               # 无数据 source="none"
        r.stop()
    """

    def __init__(self, parser: Optional[NMEAParser] = None):
        self.parser = parser or NMEAParser()
        self._ser = None  # type: ignore
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._port: Optional[str] = None
        self._baudrate: Optional[int] = None
        # 热插拔状态
        self._connected = False          # 串口真正打开且近期有数据
        self._data_timeout = 5.0         # 连续无数据超过此秒数视为断线
        self._reconnect_interval = 2.0   # 重连尝试间隔（秒）
        # 最新融合状态
        self._fix: Dict[str, Any] = self._empty_fix()
        # 最新 GSV/GSA 缓存：供天空图绘制真实卫星天空图
        # _latest_gsv: talker -> 最近一次聚合完成的 GSV frame（sats 完整列表）
        self._latest_gsv: Dict[str, Dict[str, Any]] = {}
        # _latest_gsa: talker -> 该星座最新 GSA 数据
        #   GP=GPS / BD|GB=北斗 / GL=GLONASS / GA=Galileo 分别独立存储，互不覆盖。
        #   get_gsa() 返回该 dict，并在顶层合并所有星座的 used PRN 供天空图高亮。
        self._latest_gsa: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _empty_fix() -> Dict[str, Any]:
        return {
            "source": "none",
            "latitude": None, "longitude": None, "altitude_m": None,
            "satellites": None, "hdop": None,
            "speed_kmh": None, "course_deg": None,
            "utc_time": None, "fix_quality": 0,
            "timestamp": None,
        }

    @staticmethod
    def _discover_by_id_ports() -> List[Dict[str, Any]]:
        """补充扫描 Linux ``/dev/serial/by-id/``（按 USB 序列号的稳定设备标识）。

        CH340/CP2102 等 USB-TTL 在此目录下有符号链接，比 ``/dev/ttyUSBx`` 更稳定
        （插拔后 ttyUSBx 编号可能漂移，by-id 名不变）。目录不存在/无权限时返回 []。
        """
        out: List[Dict[str, Any]] = []
        base = "/dev/serial/by-id"
        try:
            for name in os.listdir(base):
                path = os.path.join(base, name)
                if os.path.exists(path):
                    out.append({
                        "device": path,
                        "description": "usb-serial by-id",
                        "manufacturer": "",
                        "hwid": name,
                        "usb_ttl_chip": None,
                    })
        except OSError:
            pass
        return out

    @staticmethod
    def list_candidate_ports() -> List[Dict[str, Any]]:
        """枚举候选串口，返回带描述信息的字典列表。

        每项 ::

            {"device": "/dev/ttyUSB0",
             "description": "USB-SERIAL CH340",
             "manufacturer": "QinHeng",
             "hwid": "USB VID:PID=1A86:7523 ..."}

        自动识别 CH340/CP2102/FT232/PL2303 等常见 USB-TTL 芯片（description
        或 manufacturer 命中关键字即视为已知芯片）。同时合并 ``/dev/serial/by-id``
        下的稳定符号链接（按 realpath 去重，避免重复探测同一物理口）。
        无 pyserial 返回 []。
        """
        ports: List[Dict[str, Any]] = []
        seen_realpaths: set = set()
        if _SERIAL_AVAILABLE and list_ports is not None:
            try:
                for p in list_ports.comports():
                    desc = (p.description or "")
                    mfr = (p.manufacturer or "")
                    rp = os.path.realpath(p.device)
                    seen_realpaths.add(rp)
                    ports.append({
                        "device": p.device,
                        "description": desc,
                        "manufacturer": mfr,
                        "hwid": (p.hwid or ""),
                        "usb_ttl_chip": next(
                            (chip for chip in _USB_TTL_CHIPS
                             if chip in (desc + " " + mfr).lower()),
                            None),
                    })
            except Exception:
                pass
        # 追加 /dev/serial/by-id 符号链接（realpath 去重）
        for info in SerialGNSSReader._discover_by_id_ports():
            if os.path.realpath(info["device"]) in seen_realpaths:
                continue
            seen_realpaths.add(os.path.realpath(info["device"]))
            ports.append(info)
        return ports

    @staticmethod
    def list_candidate_port_devices() -> List[str]:
        """向后兼容：仅返回设备名（如 '/dev/ttyUSB0'）字符串列表。"""
        return [p["device"] for p in SerialGNSSReader.list_candidate_ports()]

    @staticmethod
    def _is_gnss_port(info: Dict[str, Any]) -> bool:
        desc = (info.get("description", "") + " " + info.get("manufacturer", "")).lower()
        return any(k in desc for k in _GNSS_KEYWORDS)

    @classmethod
    def auto_detect(cls, baudrates: Optional[List[int]] = None,
                    read_timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """扫描所有候选串口 × 波特率，读到足够合法 NMEA 即锁定。

        - 每个 端口×波特率 组合读取窗口至少 ``_AUTODETECT_PORT_WINDOW``（1.5s）：
          GNSS 模块上电、CH340 枚举后才开始吐 NMEA，窗口太短会漏检。
        - 命中判据：窗口内累计收到至少 ``_AUTODETECT_MIN_NMEA``（3）条
          “``$`` 开头且校验和正确”的 NMEA 语句——单条可能是上电残留/乱码误判。
        - 所有组合扫描总时长硬上限 ``_AUTODETECT_TOTAL_BUDGET``（15s），
          到点立即放弃，避免 UI 启动卡死。
        - 优先探测 description/manufacturer 命中 GNSS 关键字的端口。

        返回 {"port":..., "baudrate":...}；无设备/无合法 NMEA 返回 None，
        不阻塞、不抛异常、不返回假数据。
        """
        if not _SERIAL_AVAILABLE:
            return None
        baudrates = baudrates or list(DEFAULT_BAUDRATES)
        window = float(read_timeout) if read_timeout else _AUTODETECT_PORT_WINDOW
        if window < _AUTODETECT_PORT_WINDOW:
            window = _AUTODETECT_PORT_WINDOW  # 探测窗口下限 1.5s
        infos = cls.list_candidate_ports()
        if not infos:
            return None
        # GNSS 相关端口排前面（sort 稳定，保持其余顺序）
        infos.sort(key=lambda info: 0 if cls._is_gnss_port(info) else 1)
        parser = NMEAParser()
        budget_deadline = time.time() + _AUTODETECT_TOTAL_BUDGET
        for info in infos:
            if time.time() >= budget_deadline:
                break
            port = info["device"]
            for baud in baudrates:
                if time.time() >= budget_deadline:
                    break
                ser = None
                try:
                    # 单次 readline 阻塞短一点，便于在窗口内循环累计合法语句数
                    ser = serial.Serial(port, baud, timeout=_AUTODETECT_READ_CHUNK)
                    win_deadline = time.time() + window
                    valid = 0
                    while time.time() < win_deadline:
                        raw = ser.readline()
                        if not raw:
                            continue  # 本 chunk 超时，继续等到窗口结束
                        try:
                            line = raw.decode("ascii", errors="ignore")
                        except Exception:
                            continue
                        if line.startswith("$") and parser.parse(line) is not None:
                            valid += 1
                            if valid >= _AUTODETECT_MIN_NMEA:
                                return {"port": port, "baudrate": baud}
                except Exception:
                    # 端口被占用/无权限/非 GNSS：试下一个
                    pass
                finally:
                    if ser is not None:
                        try:
                            ser.close()
                        except Exception:
                            pass
        return None

    def _open_serial(self, port: str, baudrate: int):
        """打开串口（抽出成方法便于自测时 mock 重连状态机）。"""
        return serial.Serial(port, baudrate, timeout=1.0)

    def start(self, port: Optional[str] = None, baudrate: Optional[int] = None):
        """打开串口并启动后台读取线程。port/baudrate 为 None 时先 auto_detect。"""
        if self._running.is_set():
            return
        if port is None or baudrate is None:
            info = self.auto_detect()
            if info is None:
                return False
            port, baudrate = info["port"], info["baudrate"]
        try:
            self._ser = self._open_serial(port, baudrate)
        except Exception:
            self._ser = None
            return False
        self._port, self._baudrate = port, baudrate
        self._connected = True
        self._running.set()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._connected = False

    def is_connected(self) -> bool:
        """返回串口是否真正打开且在读数据（后台线程维护状态）。"""
        return self._running.is_set() and self._connected

    def _close_ser(self):
        """关闭当前串口并标记断开（不阻塞调用方，供后台线程调用）。"""
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self._connected = False

    def _reconnect(self) -> bool:
        """后台线程内重连：每隔 _reconnect_interval 秒尝试重开当前 port/baud。

        无限重试直到成功或 ``stop()`` 被调用（热插拔场景下用户可能随时插上模块）。
        返回 True=重连成功可继续读；False=被 stop() 打断。
        """
        while self._running.is_set():
            time.sleep(self._reconnect_interval)
            if not self._running.is_set():
                return False
            try:
                self._ser = self._open_serial(self._port, self._baudrate)  # type: ignore[arg-type]
                self._connected = True
                return True
            except Exception:
                self._ser = None
                self._connected = False
                continue
        return False

    def _read_loop(self):
        """后台读循环：读 NMEA → 合并；断线自动重连，不阻塞调用方。"""
        last_data = time.time()
        while self._running.is_set():
            if self._ser is None:
                # 断线态：进入重连循环（无限重试直到成功或 stop）
                self._connected = False
                if not self._reconnect():
                    break
                last_data = time.time()
                continue
            try:
                raw = self._ser.readline()
            except Exception:
                # 读异常（拔线/USB 断开）→ 关掉，进入重连
                self._close_ser()
                continue
            if not raw:
                # readline 超时（timeout=1s 返回空）：检查是否长时间无数据
                if time.time() - last_data > self._data_timeout:
                    self._close_ser()
                continue
            last_data = time.time()
            self._connected = True
            try:
                line = raw.decode("ascii", errors="ignore")
            except Exception:
                continue
            rec = self.parser.parse(line)
            if rec is not None:
                self._merge(rec)

    def _merge(self, rec: Dict[str, Any]):
        """把单条语句合并进最新 fix 状态。"""
        with self._lock:
            f = self._fix
            f["source"] = "real"
            f["timestamp"] = time.time()
            s = rec.get("sentence")
            if s == "GGA":
                if rec.get("latitude") is not None:
                    f["latitude"] = rec["latitude"]
                if rec.get("longitude") is not None:
                    f["longitude"] = rec["longitude"]
                if rec.get("altitude_m") is not None:
                    f["altitude_m"] = rec["altitude_m"]
                f["satellites"] = rec.get("satellites", f["satellites"])
                f["hdop"] = rec.get("hdop", f["hdop"])
                f["fix_quality"] = rec.get("fix_quality", f["fix_quality"])
            elif s == "RMC":
                if rec.get("latitude") is not None:
                    f["latitude"] = rec["latitude"]
                if rec.get("longitude") is not None:
                    f["longitude"] = rec["longitude"]
                f["speed_kmh"] = rec.get("speed_kmh")
                f["course_deg"] = rec.get("course_deg")
                f["utc_time"] = rec.get("utc_time")
            elif s == "VTG":
                if rec.get("speed_kmh") is not None:
                    f["speed_kmh"] = rec["speed_kmh"]
                if rec.get("course_deg") is not None:
                    f["course_deg"] = rec["course_deg"]
            elif s == "GSV":
                # 只缓存聚合完成（或单帧）的完整 GSV；中间帧 parser 已返回 None
                talker = rec.get("talker")
                if talker:
                    self._latest_gsv[talker] = dict(rec)
            elif s == "GSA":
                # 按 talker 分别存储：GP GSA 是 GPS 用星，BD GSA 是北斗用星……
                # 不同星座的 GSA 互不覆盖，get_gsa() 再合并 used PRN。
                talker = rec.get("talker")
                if talker:
                    self._latest_gsa[talker] = dict(rec)

    def get_fix(self) -> Dict[str, Any]:
        """返回最新 fix 快照。

        无有效 RMC/GGA（从未收到任何 NMEA）或数据过期（>10s 无新 NMEA）时，
        返回 ``source="none"`` 且所有坐标字段为 None——绝不返回 (0,0) 或旧坐标。
        只要 10s 内收到过合法 NMEA，就返回最新合并结果（即便 fix 未定，
        source 仍为 "real"、坐标为 None，符合“收到语句但未定位”的状态）。
        """
        with self._lock:
            ts = self._fix["timestamp"]
            stale = ts is None or (time.time() - ts > 10.0)
            if stale:
                # 过期/无数据：显式返回空 fix，杜绝旧坐标残留
                return self._empty_fix()
            return dict(self._fix)

    def _gsv_stale(self) -> bool:
        """天空图数据新鲜度：仅当“读线程在跑但连续 >10s 无新 NMEA”才视为过期。

        未启动线程时不算 stale（兼容直接 _merge 的单元测试/喂数据场景）。
        """
        return (self._running.is_set() and self._fix["timestamp"] is not None
                and time.time() - self._fix["timestamp"] > 10.0)

    def get_gsv_frames(self) -> List[Dict[str, Any]]:
        """返回各 talker 最新一帧（聚合后）GSV 的列表，供天空图绘制卫星位置。

        每项 :: {"talker": "GP"/"GL"/"GA"/"GB"/"GN",
                 "sats": [{"id":prn, "elevation":deg, "azimuth":deg, "snr_db":dB}, ...]}

        无数据 / 数据过期时返回 []，绝不造假卫星。
        """
        with self._lock:
            if self._gsv_stale():
                return []
            return [dict(fr) for fr in self._latest_gsv.values()]

    def get_gsa(self) -> Dict[str, Any]:
        """返回按 talker ID 分组的 GSA 数据，供天空图标记“定位中”卫星。

        返回 dict ::

            {
              "GP": {"talker":"GP", "fix_type":3, "mode":"A",
                     "satellites_used":[1,2,3], "prns":[1,2,3],
                     "pdop":..,"hdop":..,"vdop":..},
              "BD": {...}, "GL": {...}, "GA": {...},
              # 顶层合并字段（兼容天空图 update_gnss_satellites 的扁平读法）：
              "satellites_used": [1,2,3,33],   # 所有星座 used PRN 合并去重
              "fix_type": 3,                   # 最能代表当前定位的 fix_type
            }

        无 GSA 数据时返回空 dict ``{}``（falsy），绝不返回 None 或假数据。
        """
        with self._lock:
            if self._gsv_stale() or not self._latest_gsa:
                return {}
            out: Dict[str, Any] = {}
            used: List[int] = []
            best_fix = 0
            for talker, rec in self._latest_gsa.items():
                g = dict(rec)
                prns = list(rec.get("satellites_used", []) or [])
                g["prns"] = prns  # 任务约定的字段名别名
                out[talker] = g
                used.extend(prns)
                ft = rec.get("fix_type", 0) or 0
                # 取最高精度：3D(3) > 2D(2) > 无(1/0)
                if ft > best_fix:
                    best_fix = ft
            # 顶层合并 used PRN（去重、保序），供天空图直接高亮
            seen = set()
            merged_used: List[int] = []
            for p in used:
                if p not in seen:
                    seen.add(p)
                    merged_used.append(p)
            out["satellites_used"] = merged_used
            out["fix_type"] = best_fix
            return out


# ============================================================
# NTRIP Client（骨架：HTTP GET 收 RTCM3，回调原始字节）
# ============================================================

class NTRIPClient:
    """连接 NTRIP caster，拉取 RTCM3 差分数据流。

    参考 RTKLIB str2str（src/stream.c）NTRIP client 流程：
      1. TCP 连接 host:port（默认 2101）
      2. ``GET /<mountpoint> HTTP/1.0\r\nHost: ..\r\nAuthorization: Basic ..\r\n\r\n``
      3. 收到 ``ICY 200`` / ``HTTP/1.0 200`` 后，后续即为 RTCM3 字节流，
         通过 ``on_data(raw: bytes)`` 回调吐出（本模块不解算 RTCM）。
    """

    def __init__(self, host: str, port: int = 2101, mountpoint: str = "",
                 user: str = "", password: str = "",
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

    def connect(self) -> bool:
        """建立连接并发送 GET 请求；返回是否握手成功。"""
        try:
            self._sock = socket.create_connection((self.host, self.port),
                                                  timeout=self.timeout)
        except OSError:
            return False

        path = "/" + self.mountpoint if self.mountpoint else "/"
        req = f"GET {path} HTTP/1.0\r\nHost: {self.host}\r\n"
        if self.user:
            token = base64.b64encode(
                f"{self.user}:{self.password}".encode()).decode()
            req += f"Authorization: Basic {token}\r\n"
        req += "User-Agent: MBDSDR/1.0\r\nAccept: */*\r\n\r\n"
        try:
            self._sock.sendall(req.encode())
            # 读 HTTP 响应头（到 \r\n\r\n）
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = self._sock.recv(256)
                if not chunk:
                    break
                buf += chunk
            head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1", "ignore")
            if "200" not in head.split("\r\n", 1)[0]:
                # 401/404/403 等
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
        """握手成功后持续接收 RTCM3 字节流并回调。"""
        try:
            self._sock.settimeout(5.0)  # type: ignore
            while self._running.is_set() and self._sock is not None:
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
# 自测：合成 NMEA 数据（无硬件即可跑）
# ============================================================

if __name__ == "__main__":
    p = NMEAParser()

    def check(name, cond):
        print(f"  [{'OK' if cond else 'FAIL'}] {name}")
        assert cond, name

    # 一条合法 GGA（校验和手算）。构造：body 异或 = ?
    def make(body):
        cs = nmea_checksum(body)
        return f"${body}*{cs:02X}"

    # --- 1. 校验和 ---
    gga_body = "GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,"
    gga = make(gga_body)
    r = p.parse(gga)
    print("GGA:", gga)
    check("GGA 解析成功", r is not None and r["sentence"] == "GGA")
    check("GGA 纬度≈43.8667", r and abs(r["latitude"] - 43.866667) < 1e-4)
    check("GGA 经度≈125.3167", r and abs(r["longitude"] - 125.316667) < 1e-4)
    check("GGA 卫星=9", r and r["satellites"] == 9)
    check("GGA 高度=150", r and abs(r["altitude_m"] - 150.0) < 1e-6)
    check("GGA fix_quality=1", r and r["fix_quality"] == 1)

    # 错误校验和应被拒
    bad = gga[:-3] + "00"
    check("坏校验和被拒", p.parse(bad) is None)

    # --- 2. RMC（南纬/西经负号） ---
    rmc_body = "GNRMC,072545.00,A,3351.1111,S,11820.2222,W,10.5,45.0,010126,,A"
    rmc = p.parse(make(rmc_body))
    print("RMC:", make(rmc_body))
    check("RMC 南纬为负", rmc and rmc["latitude"] < 0)
    check("RMC 西经为负", rmc and rmc["longitude"] < 0)
    check("RMC speed_kmh≈19.45", rmc and abs(rmc["speed_kmh"] - 10.5 * 1.852) < 1e-3)
    check("RMC valid", rmc and rmc["valid"] is True)

    # --- 3. GSA / VTG / ZDA ---
    check("GSA", p.parse(make("GNGSA,A,3,01,02,03,,,,,,,,,,1.0,0.8,0.9")) is not None)
    check("VTG", p.parse(make("GNVTG,45.0,T,,M,10.5,N,19.4,K")) is not None)
    zda = p.parse(make("GNZDA,072545.00,24,09,2026,,"))
    check("ZDA", zda is not None and zda["datetime_utc"] is not None)

    # --- 3b. GLL 地理坐标语句 ---
    gll = p.parse(make("GNGLL,4352.0000,N,12519.0000,E,072545.00,A"))
    print("GLL:", make("GNGLL,4352.0000,N,12519.0000,E,072545.00,A"))
    check("GLL sentence", gll and gll["sentence"] == "GLL")
    check("GLL 纬度≈43.8667", gll and abs(gll["latitude"] - 43.866667) < 1e-4)
    check("GLL 经度≈125.3167", gll and abs(gll["longitude"] - 125.316667) < 1e-4)
    check("GLL utc_time", gll and gll["utc_time"] == "072545.00")
    check("GLL valid=True", gll and gll["valid"] is True)
    gll_v = p.parse(make("GNGLL,4352.0000,N,12519.0000,E,072545.00,V"))
    check("GLL status=V 时 valid=False", gll_v and gll_v["valid"] is False)

    # --- 3c. GST 伪距噪声统计 ---
    gst = p.parse(make("GNGST,072545.00,1.0,2.0,1.0,30.0,0.8,0.6,5.0"))
    print("GST:", make("GNGST,072545.00,1.0,2.0,1.0,30.0,0.8,0.6,5.0"))
    check("GST sentence", gst and gst["sentence"] == "GST")
    check("GST rms_std=1.0", gst and gst["rms_std"] == 1.0)
    check("GST major_sigma=2.0", gst and gst["major_sigma"] == 2.0)
    check("GST minor_sigma=1.0", gst and gst["minor_sigma"] == 1.0)
    check("GST orient=30.0", gst and gst["orient"] == 30.0)
    check("GST lat_sigma=0.8", gst and gst["lat_sigma"] == 0.8)
    check("GST lon_sigma=0.6", gst and gst["lon_sigma"] == 0.6)
    check("GST alt_sigma=5.0", gst and gst["alt_sigma"] == 5.0)
    gst_empty = p.parse(make("GNGST,072545.00,,,,,,,"))
    check("GST 空字段为 None", gst_empty and gst_empty["major_sigma"] is None
          and gst_empty["alt_sigma"] is None)

    # --- 3d. GSV 多帧聚合（按 talker 分组） ---
    pg = NMEAParser()
    f1 = pg.parse(make("GNGSV,3,1,11,01,88,045,42,02,45,120,38"))
    f2 = pg.parse(make("GNGSV,3,2,11,03,40,200,30"))
    f3 = pg.parse(make("GNGSV,3,3,11,04,30,300,25"))
    check("GSV 中间帧返回 None", f1 is None and f2 is None)
    check("GSV 末帧聚合返回 dict", f3 is not None and f3["aggregated"] is True)
    check("GSV 聚合出 4 颗卫星", f3 and len(f3["sats"]) == 4)
    check("GSV 聚合含 PRN=4", f3 and any(s["id"] == 4 for s in f3["sats"]))
    # 单帧 GSV 直接返回（不聚合）
    gsv1 = pg.parse(make("GNGSV,1,1,4,01,80,040,40"))
    check("GSV 单帧直接返回 aggregated=False", gsv1 and gsv1["aggregated"] is False
          and len(gsv1["sats"]) == 1)
    # 不同 talker 分组互不干扰：北斗 GBGSV 多帧
    pb = NMEAParser()
    b1 = pb.parse(make("GBGSV,2,1,6,211,80,040,40,212,50,100,35"))
    b2 = pb.parse(make("GBGSV,2,2,6,213,30,200,20"))
    check("GBGSV 中间帧 None", b1 is None)
    check("GBGSV 末帧聚合 3 颗（PRN>32 正常）", b2 and b2["aggregated"] is True
          and len(b2["sats"]) == 3 and any(s["id"] == 211 for s in b2["sats"]))

    # --- 3d2. GP GSV 三帧完整聚合（验收：收齐才返回完整列表） ---
    pgps = NMEAParser()
    g1 = pgps.parse(make("GPGSV,3,1,8,01,88,045,42"))
    g2 = pgps.parse(make("GPGSV,3,2,8,02,45,120,38"))
    g3 = pgps.parse(make("GPGSV,3,3,8,03,30,200,25"))
    check("GP GSV 帧1/2 不返回（未收齐）", g1 is None and g2 is None)
    check("GP GSV 帧3 收齐返回 3 颗", g3 and g3["aggregated"] is True
          and len(g3["sats"]) == 3
          and [s["id"] for s in g3["sats"]] == [1, 2, 3])

    # --- 3d3. GSV 超时：半截缓冲超 2s 被丢弃，不返回部分帧 ---
    pto = NMEAParser()
    t1 = pto.parse(make("GPGSV,2,1,6,01,80,040,40"))
    t2 = pto.parse(make("GPGSV,2,2,6,02,40,100,30"))  # 正常收齐 -> 2 颗
    check("超时用例前置：2 帧收齐=2 颗", t2 and len(t2["sats"]) == 2)
    pin = NMEAParser()
    pin.parse(make("GPGSV,3,1,9,11,80,040,40"))   # 帧1
    r_mid = pin.parse(make("GPGSV,3,2,9,12,40,100,30"))  # 帧2
    check("半截序列中间帧不返回", r_mid is None)
    # 把缓冲时间戳拨到 999 秒前，模拟超过 _GSV_AGG_TIMEOUT 未收齐
    pin._gsv_buf[("GP", 3)]["ts"] = time.time() - 999
    r_last = pin.parse(make("GPGSV,3,3,9,13,20,200,25"))  # 迟到的末帧
    # 半截缓冲已被丢弃：末帧单独成帧，只剩 1 颗（而非错误地拼出 3 颗）
    check("超时丢弃半截缓冲，不返回部分帧", r_last and len(r_last["sats"]) == 1
          and r_last["sats"][0]["id"] == 13)

    # --- 3e. BDTXT 文本语句（不崩溃） ---
    txt = p.parse(make("BDTXT,01,01,01,HW U-BLOX 8 READY"))
    check("BDTXT 解析为 TXT", txt and txt["sentence"] == "TXT"
          and "U-BLOX" in txt["text"])

    # --- 4. 多星座 talker 前缀（含北斗 BD/GB） ---
    check("BDGGA 北斗前缀", p.parse(make("BDGGA,072545.00,4352.00,N,12519.00,E,1,9,0.9,150.0,M,,,,"))["sentence"] == "GGA")
    check("GBGSV 北斗", p.parse(make("GBGSV,1,1,4,01,80,040,40")) is not None)
    check("GPRMC GPS", p.parse(make("GPRMC,072545.00,A,4352.00,N,12519.00,E,0,0,010126,,A")) is not None)
    check("GAGAL Galileo 拒绝非NMEA", p.parse("hello world") is None)

    # --- 5. SerialGNSSReader.get_fix 无设备默认 none ---
    rd = SerialGNSSReader()
    fix = rd.get_fix()
    check("无串口 get_fix source=none", fix["source"] == "none" and fix["latitude"] is None)
    check("无串口 get_fix 坐标全 None", fix["latitude"] is None
          and fix["longitude"] is None and fix["altitude_m"] is None)
    check("无串口 is_connected()=False", rd.is_connected() is False)

    # --- 5b. GSA 多星座：GP GSA + BD GSA 分别存储，used PRN 合并 ---
    rg = SerialGNSSReader()
    gsa_gp = p.parse(make("GPGSA,A,3,01,02,03,,,,,,,,,,1.2,0.9,0.6"))
    gsa_bd = p.parse(make("BDGSA,A,3,31,32,,,,,,,,,,,,1.5,1.0,0.8"))
    assert gsa_gp and gsa_bd
    rg._merge(gsa_gp)
    rg._merge(gsa_bd)
    gsa_out = rg.get_gsa()
    check("get_gsa 按 talker 分组含 GP/BD", "GP" in gsa_out and "BD" in gsa_out)
    check("GP GSA used PRN=[1,2,3]", gsa_out.get("GP", {}).get("satellites_used") == [1, 2, 3])
    check("BD GSA used PRN=[31,32]", gsa_out.get("BD", {}).get("satellites_used") == [31, 32])
    check("GP fix_type=3", gsa_out.get("GP", {}).get("fix_type") == 3)
    check("顶层合并 used PRN=[1,2,3,31,32]", gsa_out.get("satellites_used") == [1, 2, 3, 31, 32])
    check("顶层合并 fix_type=3", gsa_out.get("fix_type") == 3)
    # 无 GSA 数据时返回空 dict
    rg_empty = SerialGNSSReader()
    check("无 GSA get_gsa 返回 {}", rg_empty.get_gsa() == {})
    check("无 GSV get_gsv_frames 返回 []", rg_empty.get_gsv_frames() == [])

    # --- 6. auto_detect 在无设备环境返回 None 不崩溃 ---
    res = SerialGNSSReader.auto_detect()
    check("无设备 auto_detect 返回 None", res is None)

    # --- 6b. list_candidate_ports 向后兼容 ---
    devs = SerialGNSSReader.list_candidate_port_devices()
    check("list_candidate_port_devices 返回字符串列表", isinstance(devs, list))
    infos = SerialGNSSReader.list_candidate_ports()
    check("list_candidate_ports 返回 dict 列表", isinstance(infos, list))

    # --- 7. 热插拔重连状态机（mock 串口，无真实硬件） ---
    class _Disconnect(Exception):
        pass

    class _FakeSerial:
        def __init__(self, script):
            self._script = list(script)
            self.closed = False

        def readline(self):
            if self._script:
                item = self._script.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return b""  # 模拟 readline 超时

        def close(self):
            self.closed = True

    gga_line = make(
        "GNGGA,072545.00,4352.00,N,12519.00,E,1,9,0.9,150.0,M,,,,"
    ).encode("ascii")

    rd2 = SerialGNSSReader()
    rd2._reconnect_interval = 0.02   # 加快测试
    rd2._data_timeout = 1.0         # 测试窗口内不触发超时断线
    opens = {"n": 0}

    def fake_open(port, baud):
        opens["n"] += 1
        # 重连后给一个稳定串口（一帧 GGA，之后静默）
        return _FakeSerial([gga_line])

    rd2._open_serial = fake_open
    rd2._port = "/dev/fake"
    rd2._baudrate = 9600
    # 首连：读到一帧 GGA 后抛异常模拟拔线
    rd2._ser = _FakeSerial([gga_line, _Disconnect("unplugged")])
    rd2._connected = True
    rd2._running.set()
    rd2._thread = threading.Thread(target=rd2._read_loop, daemon=True)
    rd2._thread.start()

    time.sleep(0.6)  # 等：首帧合并 → 断线 → 重连 → 重连后首帧合并
    fix2 = rd2.get_fix()
    check("拔线后自动重连并恢复 fix", fix2["source"] == "real"
          and fix2["latitude"] is not None)
    check("重连至少发生一次", opens["n"] >= 1)
    check("重连后 is_connected()=True", rd2.is_connected() is True)
    rd2.stop()
    time.sleep(0.1)
    check("stop() 后 is_connected()=False", rd2.is_connected() is False)

    print("\nserial_gnss self-test PASSED")

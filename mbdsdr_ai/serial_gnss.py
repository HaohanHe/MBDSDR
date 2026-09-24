"""
MBDSDR AI - 真实串口 GNSS 接入（骨架）
======================================

把真实 GNSS 模块（USB-TTL / USB-CDC，输出 NMEA-0183）接到本软件：

- ``NMEAParser``      : 解析 GGA/RMC/GSA/GSV/VTG/ZDA 全语句，兼容多星座
                         talker 前缀 GP/GL/GA/GB/BD/GN（含北斗 BDGGA/GBGSV），
                        带 NMEA XOR 校验和验证。
- ``SerialGNSSReader``: 后台线程读串口、逐行解析、维护最新 fix；``auto_detect()``
                        扫描 Windows COM* / Linux /dev/ttyUSB*,/dev/ttyACM* 与
                        常见波特率，读到合法 NMEA 即锁定。无设备返回 None 不崩溃。
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

import threading
import time
import socket
import base64
import math
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, List

try:
    import serial  # pyserial 3.5+
    from serial.tools import list_ports
    _SERIAL_AVAILABLE = True
except Exception:  # pragma: no cover - 无 pyserial 时退化，不影响 import
    serial = None  # type: ignore
    list_ports = None  # type: ignore
    _SERIAL_AVAILABLE = False


# 常见 GNSS 波特率（来源：NMEA-0183 / 各模块默认波特率表，UBlox/NEO/M8N 默认 9600，
# 老模块 4800，RTK 模块常配 38400/115200）
DEFAULT_BAUDRATES = [4800, 9600, 38400, 57600, 115200]

# 多星座 talker 前缀（来源 direwolf dwgpsnmea.c:38-42；补充 BD=北斗部分厂商私有前缀）
TALKER_IDS = ("GP", "GL", "GA", "GB", "BD", "GN")

# NMEA 语句类型（去掉 2 位 talker 后的 3 字母类型）
KNOWN_SENTENCE_TYPES = ("GGA", "RMC", "GSA", "GSV", "VTG", "ZDA")


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
    """

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

    # ---- GSV: 可见卫星（可能多帧）。返回本帧摘要，不聚合 ----
    # $GNGSV,nummsg,msgnum,numsv,sv,elev,az,snr,sv,...*CC
    def _parse_gsv(self, talker: str, f: List[str]) -> Dict[str, Any]:
        sats = []
        # 每 4 字段一组：id, elev, az, snr
        i = 4
        while i + 3 < len(f):
            sid, elev, az, snr = f[i], f[i + 1], f[i + 2], f[i + 3]
            if sid:
                sats.append({
                    "id": int(sid) if sid.isdigit() else sid,
                    "elevation": float(elev) if elev else None,
                    "azimuth": float(az) if az else None,
                    "snr_db": float(snr) if snr else None,
                })
            i += 4
        return {
            "talker": talker, "sentence": "GSV",
            "total_messages": int(f[1]) if len(f) > 1 and f[1].isdigit() else 0,
            "message_number": int(f[2]) if len(f) > 2 and f[2].isdigit() else 0,
            "satellites_in_view": int(f[3]) if len(f) > 3 and f[3].isdigit() else 0,
            "sats": sats,
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
        # 最新融合状态
        self._fix: Dict[str, Any] = self._empty_fix()

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
    def list_candidate_ports() -> List[str]:
        """列出候选串口：Windows COM* / Linux /dev/ttyUSB*,/dev/ttyACM*。"""
        if not _SERIAL_AVAILABLE or list_ports is None:
            return []
        ports = []
        try:
            for p in list_ports.comports():
                ports.append(p.device)
        except Exception:
            pass
        return ports

    @classmethod
    def auto_detect(cls, baudrates: Optional[List[int]] = None,
                    read_timeout: float = 0.6) -> Optional[Dict[str, Any]]:
        """扫描所有候选串口 × 波特率，读到一条合法 NMEA 即锁定。

        返回 {"port":..., "baudrate":...}；无设备/无合法 NMEA 返回 None，绝不崩溃。
        """
        if not _SERIAL_AVAILABLE:
            return None
        baudrates = baudrates or DEFAULT_BAUDRATES
        ports = cls.list_candidate_ports()
        if not ports:
            return None
        parser = NMEAParser()
        for port in ports:
            for baud in baudrates:
                ser = None
                try:
                    ser = serial.Serial(port, baud, timeout=read_timeout)
                    # 丢弃启动垃圾，读若干行找合法 NMEA
                    deadline = time.time() + read_timeout + 0.3
                    while time.time() < deadline:
                        raw = ser.readline()
                        if not raw:
                            break
                        try:
                            line = raw.decode("ascii", errors="ignore")
                        except Exception:
                            continue
                        if line.startswith("$") and parser.parse(line) is not None:
                            ser.close()
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
            self._ser = serial.Serial(port, baudrate, timeout=1.0)
        except Exception:
            self._ser = None
            return False
        self._port, self._baudrate = port, baudrate
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

    def _read_loop(self):
        while self._running.is_set() and self._ser is not None:
            try:
                raw = self._ser.readline()
            except Exception:
                break
            if not raw:
                continue
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

    def get_fix(self) -> Dict[str, Any]:
        """返回最新 fix 快照。无数据时 source='none'、坐标为 None，绝不造假。"""
        with self._lock:
            # 超过 10 秒无新数据视为丢失
            if self._running.is_set() and self._fix["timestamp"] is not None:
                if time.time() - self._fix["timestamp"] > 10.0:
                    snap = dict(self._fix)
                    snap["source"] = "none"
                    return snap
            return dict(self._fix)


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

    # --- 3. GSA / VTG / ZDA / GSV ---
    check("GSA", p.parse(make("GNGSA,A,3,01,02,03,,,,,,,,,,1.0,0.8,0.9")) is not None)
    check("VTG", p.parse(make("GNVTG,45.0,T,,M,10.5,N,19.4,K")) is not None)
    zda = p.parse(make("GNZDA,072545.00,24,09,2026,,"))
    check("ZDA", zda is not None and zda["datetime_utc"] is not None)
    gsv = p.parse(make("GNGSV,2,1,11,01,88,045,42,02,45,120,38"))
    check("GSV 解析出 2 颗", gsv and len(gsv["sats"]) == 2)

    # --- 4. 多星座 talker 前缀（含北斗 BD/GB） ---
    check("BDGGA 北斗前缀", p.parse(make("BDGGA,072545.00,4352.00,N,12519.00,E,1,9,0.9,150.0,M,,,,"))["sentence"] == "GGA")
    check("GBGSV 北斗", p.parse(make("GBGSV,1,1,4,01,80,040,40")) is not None)
    check("GPRMC GPS", p.parse(make("GPRMC,072545.00,A,4352.00,N,12519.00,E,0,0,010126,,A")) is not None)
    check("GAGAL Galileo 拒绝非NMEA", p.parse("hello world") is None)

    # --- 5. SerialGNSSReader.get_fix 无设备默认 none ---
    rd = SerialGNSSReader()
    fix = rd.get_fix()
    check("无串口 get_fix source=none", fix["source"] == "none" and fix["latitude"] is None)

    # --- 6. auto_detect 在无设备环境返回 None 不崩溃 ---
    res = SerialGNSSReader.auto_detect()
    check("无设备 auto_detect 返回 None", res is None)

    print("\nserial_gnss self-test PASSED")

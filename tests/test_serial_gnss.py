"""
MBDSDR AI - 串口 GNSS 端到端测试
==================================

用 mock 串口 + 真实多星座 NMEA-0183 字节流做端到端断言：

- NMEAParser 单元测试：GGA/RMC/GSA/GSV/VTG/ZDA、多星座 talker 前缀、
  坏校验和 / 残缺句被拒或不崩溃。
- SerialGNSSReader：注入 MockSerial（readline 返回 NMEA 字节流），
  断言 get_fix() 融合结果；无定位（fix_quality=0）仍 source="real" 但坐标 None；
  超时 10s 后 source="none"（用 mock time 模拟时间流逝）。
- RealGNSSMonitor：用 fake reader 测试 get_position() 字段映射与无设备退化。

红线：所有 NMEA 校验和由 nmea_checksum() 动态计算，不手写；
测试坐标为合成已知向量，不代表真实定位。GPL-3.0。
"""

import os
import sys
import time
import queue
import threading
import unittest.mock as mock

import pytest

# 仓库根加入 sys.path（与 tests/ 下其它测试一致）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.serial_gnss import (  # noqa: E402
    nmea_checksum,
    NMEAParser,
    SerialGNSSReader,
    TALKER_IDS,
    DEFAULT_BAUDRATES,
)


# ============================================================
# NMEA 样本构造（校验和动态计算，绝不手写）
# ============================================================

def mk(body: str) -> bytes:
    """由 body（不含 $ 和 *）生成带正确校验和的 NMEA 字节行。"""
    cs = nmea_checksum(body)
    return f"${body}*{cs:02X}\r\n".encode("ascii")


# --- GNGGA 混合星座定位：经纬度/高度/卫星数/HDOP/fix_quality=1 ---
GGA_BODY = ("GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,")
# 期望: lat = 43 + 52/60 = 43.8666667 ; lon = 125 + 19/60 = 125.3166667
EXPECT_LAT = 43.0 + 52.0 / 60.0
EXPECT_LON = 125.0 + 19.0 / 60.0

# --- GPRMC GPS：有效 A，速度/航向/日期（e2e 用同 NE 坐标） ---
RMC_E2E_BODY = ("GPRMC,072545.00,A,4352.0000,N,12519.0000,E,5.0,30.0,240926,,A")
# RMC 单元测试用南纬/西经取负
RMC_SOUTHWEST_BODY = ("GNRMC,072545.00,A,3351.1111,S,11820.2222,W,10.5,45.0,010126,,A")

# --- GNGSA：3D fix，9 颗参与定位，PDOP/HDOP/VDOP ---
GSA_BODY = ("GNGSA,A,3,01,02,03,04,05,06,07,08,09,,,,2.1,1.2,1.5")

# --- GPGSV 多帧（3 帧 x 4 颗 = 12 颗可见），含 SNR ---
GSV_GP1_BODY = ("GPGSV,3,1,12,01,88,045,42,02,45,120,38,03,30,200,35,04,15,300,30")
GSV_GP2_BODY = ("GPGSV,3,2,12,05,60,010,40,06,55,090,37,07,20,180,,08,10,270,25")
GSV_GP3_BODY = ("GPGSV,3,3,12,09,70,050,41,10,40,150,36,11,25,220,33,12,5,330,28")
# --- GLGSV GLONASS 1 帧 ---
GSV_GL_BODY = ("GLGSV,1,1,04,73,80,040,40,74,45,120,38,75,30,200,35,76,15,300,30")
# --- GBGSV 北斗 1 帧（北斗 PRN 201+） ---
GSV_GB_BODY = ("GBGSV,1,1,04,201,80,040,40,202,45,120,38,203,30,200,35,204,15,300,30")

# --- GAGGA Galileo 定位 ---
GGA_GA_BODY = ("GAGGA,072545.00,4352.0000,N,12519.0000,E,1,08,1.0,150.0,M,0.0,M,,")
# --- BDGGA 北斗私有前缀定位 ---
GGA_BD_BODY = ("BDGGA,072545.00,4352.0000,N,12519.0000,E,1,08,1.0,150.0,M,0.0,M,,")

# --- GNVTG 航迹速度 ---
VTG_BODY = "GNVTG,30.0,T,,M,5.0,N,9.26,K"
# --- GNZDA 日期时间 ---
ZDA_BODY = "GNZDA,072545.00,24,09,2026,,"

# --- 无定位状态：GGA fix_quality=0 / RMC status=V ---
NOFIX_GGA_BODY = "GNGGA,072546.00,,,,,0,00,,,M,,M,,"
NOFIX_RMC_BODY = "GNRMC,072546.00,V,,,,,,,240926,,N"


# ============================================================
# Mock 串口：readline() 从队列吐 NMEA 字节，空时阻塞短超时后返回 b""
# ============================================================

class MockSerial:
    def __init__(self):
        self.q = queue.Queue()
        self.closed = False

    def feed(self, line: bytes):
        self.q.put(line)

    def feed_all(self, lines):
        for ln in lines:
            self.q.put(ln)

    def readline(self) -> bytes:
        try:
            return self.q.get(timeout=0.3)
        except queue.Empty:
            return b""

    def close(self):
        self.closed = True


# 端到端用的完整语句周期（同一坐标，避免 GGA/RMC 互相覆盖造成断言歧义）
E2E_CYCLE = [
    mk(GGA_BODY),
    mk(RMC_E2E_BODY),
    mk(GSA_BODY),
    mk(GSV_GP1_BODY),
    mk(GSV_GP2_BODY),
    mk(GSV_GP3_BODY),
    mk(GSV_GL_BODY),
    mk(GSV_GB_BODY),
    mk(VTG_BODY),
    mk(ZDA_BODY),
]


# ============================================================
# 1. NMEAParser 单元测试
# ============================================================

def test_nmea_checksum_basic():
    # 已知向量：GNGGA 示例 body 的 XOR 校验和应自洽
    assert nmea_checksum("GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,") == \
        nmea_checksum(GGA_BODY)
    # 空串异或为 0
    assert nmea_checksum("") == 0


def test_gga_parse():
    p = NMEAParser()
    r = p.parse(mk(GGA_BODY).decode("ascii"))
    assert r is not None
    assert r["sentence"] == "GGA"
    assert r["talker"] == "GN"
    assert abs(r["latitude"] - EXPECT_LAT) < 1e-4
    assert abs(r["longitude"] - EXPECT_LON) < 1e-4
    assert abs(r["altitude_m"] - 150.0) < 1e-6
    assert r["satellites"] == 9
    assert abs(r["hdop"] - 0.9) < 1e-6
    assert r["fix_quality"] == 1


def test_rmc_parse():
    p = NMEAParser()
    r = p.parse(mk(RMC_SOUTHWEST_BODY).decode("ascii"))
    assert r is not None
    assert r["sentence"] == "RMC"
    assert r["valid"] is True
    # 南纬 S 取负
    assert r["latitude"] < 0
    assert abs(r["latitude"] - (-(33.0 + 51.1111 / 60.0))) < 1e-4
    # 西经 W 取负
    assert r["longitude"] < 0
    assert abs(r["longitude"] - (-(118.0 + 20.2222 / 60.0))) < 1e-4
    # 节 -> km/h 换算
    assert abs(r["speed_kmh"] - 10.5 * 1.852) < 1e-3
    assert abs(r["course_deg"] - 45.0) < 1e-6
    assert r["date"] == "010126"


def test_gsa_parse():
    p = NMEAParser()
    r = p.parse(mk(GSA_BODY).decode("ascii"))
    assert r is not None
    assert r["sentence"] == "GSA"
    assert r["fix_type"] == 3  # 3D
    assert r["satellites_used"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert abs(r["pdop"] - 2.1) < 1e-6
    assert abs(r["hdop"] - 1.2) < 1e-6
    assert abs(r["vdop"] - 1.5) < 1e-6


def test_gsv_single_and_multi_frame():
    p = NMEAParser()
    # 单帧(total=1)直接返回本帧：GLONASS / 北斗 各一帧
    g = p.parse(mk(GSV_GL_BODY).decode("ascii"))
    assert g is not None
    assert g["sentence"] == "GSV"
    assert g["total_messages"] == 1
    assert g["message_number"] == 1
    assert g["satellites_in_view"] == 4
    assert len(g["sats"]) == 4
    assert g["aggregated"] is False
    # 第一颗卫星带 SNR
    s0 = g["sats"][0]
    assert s0["id"] == 73
    assert abs(s0["snr_db"] - 40.0) < 1e-6
    # 北斗 GBGSV 卫星 PRN 201+
    b = p.parse(mk(GSV_GB_BODY).decode("ascii"))
    assert b is not None and b["sats"][0]["id"] == 201

    # 多帧聚合：GP 3 帧。中间帧(第1/2帧)返回 None 等待聚合；
    # 收到最后一帧(第3帧)才合并返回完整 sats 列表。
    r1 = p.parse(mk(GSV_GP1_BODY).decode("ascii"))
    assert r1 is None, "GP GSV 第1帧(共3帧)为中间帧，应缓冲并返回 None"
    r2 = p.parse(mk(GSV_GP2_BODY).decode("ascii"))
    assert r2 is None, "GP GSV 第2帧(共3帧)为中间帧"
    r3 = p.parse(mk(GSV_GP3_BODY).decode("ascii"))
    assert r3 is not None
    assert r3["aggregated"] is True
    assert r3["total_messages"] == 3
    assert r3["satellites_in_view"] == 12
    assert len(r3["sats"]) == 12
    ids = [s["id"] for s in r3["sats"]]
    assert len(set(ids)) == 12
    # 帧 2 中 SNR 缺失的卫星仍被记录（snr_db=None）
    sat07 = [s for s in r3["sats"] if s["id"] == 7][0]
    assert sat07["snr_db"] is None


def test_vtg_zda_parse():
    p = NMEAParser()
    v = p.parse(mk(VTG_BODY).decode("ascii"))
    assert v is not None
    assert v["sentence"] == "VTG"
    assert abs(v["course_deg"] - 30.0) < 1e-6
    assert abs(v["speed_kmh"] - 9.26) < 1e-6
    assert abs(v["speed_knots"] - 5.0) < 1e-6

    z = p.parse(mk(ZDA_BODY).decode("ascii"))
    assert z is not None
    assert z["sentence"] == "ZDA"
    assert z["datetime_utc"] is not None
    assert z["datetime_utc"].year == 2026
    assert z["datetime_utc"].month == 9
    assert z["datetime_utc"].day == 24


def test_multi_constellation_talkers():
    p = NMEAParser()
    # GP / GL / GA / GB / BD / GN 都应能解析
    assert p.parse(mk("GPRMC,072545.00,A,4352.00,N,12519.00,E,0,0,240926,,A").decode())["talker"] == "GP"
    assert p.parse(mk(GSV_GL_BODY).decode())["talker"] == "GL"
    assert p.parse(mk(GGA_GA_BODY).decode())["talker"] == "GA"
    assert p.parse(mk(GSV_GB_BODY).decode())["talker"] == "GB"
    assert p.parse(mk(GGA_BD_BODY).decode())["talker"] == "BD"
    assert p.parse(mk(GGA_BODY).decode())["talker"] == "GN"
    # GBGSV 北斗卫星编号 201+
    g = p.parse(mk(GSV_GB_BODY).decode())
    assert g["sats"][0]["id"] == 201


def test_bad_checksum_rejected():
    p = NMEAParser()
    good = mk(GGA_BODY).decode("ascii")
    # 把校验和改成 00
    bad = good[:-3] + "00\r\n"
    assert p.parse(bad) is None
    # 校验和非十六进制
    bad2 = good[:-3] + "ZZ\r\n"
    assert p.parse(bad2) is None


def test_malformed_sentences():
    p = NMEAParser()
    # 非 $ 开头
    assert p.parse("hello world\r\n") is None
    assert p.parse("!AIVDO,1,1,,,B,0*hh\r\n") is None
    # 无校验和
    assert p.parse("$GNGGA,072545.00,4352.00,N,12519.00,E,1,9,0.9,150.0,M,,\r\n") is None
    # 残缺字段（校验和合法但字段很短）—— 不应崩溃
    try:
        r = p.parse(mk("GNGGA,abc,def").decode("ascii"))
    except Exception as e:  # pragma: no cover
        pytest.fail(f"残缺 GGA 崩溃: {e}")
    assert r is None or isinstance(r, dict)
    # 空串 / None
    assert p.parse("") is None
    assert p.parse(None) is None
    # 未知 talker（如 GL 之外的经典 4G 移动站）应被拒
    assert p.parse(mk("XXGGA,072545.00,4352.00,N,12519.00,E,1,9,0.9,150.0,M,,,").decode()) is None


def test_gll_gst_optional_support():
    """若 serial_gnss 已增强支持 GLL/GST 则断言字段；否则 skip。"""
    p = NMEAParser()
    # $GNGLL,llll.ll,a,yyyyy.yy,a,hhmmss.ss,A
    gll_body = "GNGLL,4352.0000,N,12519.0000,E,072545.00,A"
    gll = p.parse(mk(gll_body).decode("ascii"))
    if gll is None:
        pytest.skip("GLL 未在当前 serial_gnss.py 实现（可后续增强）")
    assert gll["sentence"] == "GLL"
    assert gll.get("status") == "A"
    assert abs(gll["latitude"] - EXPECT_LAT) < 1e-4

    # $GNGST,rms,ellipse,lat_err,lon_err,alt_err
    gst_body = "GNGST,072545.00,1.0,,,2.0,3.0,4.0"
    gst = p.parse(mk(gst_body).decode("ascii"))
    if gst is None:
        pytest.skip("GST 未在当前 serial_gnss.py 实现")
    assert gst["sentence"] == "GST"


# ============================================================
# 2. SerialGNSSReader mock 串口端到端测试
# ============================================================

def _run_reader_with_lines(lines, settle=0.3):
    """注入 MockSerial，跑后台读线程，喂数据，返回 (reader, mock_ser)。"""
    reader = SerialGNSSReader()
    ser = MockSerial()
    reader._ser = ser
    reader._running.set()
    t = threading.Thread(target=reader._read_loop, daemon=True)
    t.start()
    try:
        ser.feed_all(lines)
        time.sleep(settle)  # 等线程消费队列
    finally:
        reader._running.clear()
        t.join(timeout=2.0)
        ser.close()
    return reader, ser


def test_reader_e2e_fix():
    reader, _ = _run_reader_with_lines(E2E_CYCLE)
    fix = reader.get_fix()
    assert fix["source"] == "real"
    assert abs(fix["latitude"] - EXPECT_LAT) < 1e-3
    assert abs(fix["longitude"] - EXPECT_LON) < 1e-3
    assert abs(fix["altitude_m"] - 150.0) < 1e-6
    assert fix["satellites"] == 9
    assert abs(fix["hdop"] - 0.9) < 1e-6
    assert fix["fix_quality"] == 1
    # VTG/RMC 给速度航向
    assert fix["speed_kmh"] is not None
    assert abs(fix["course_deg"] - 30.0) < 1e-6


def test_reader_no_fix_still_real_but_no_coords():
    """无定位序列：合法 NMEA 但 fix_quality=0 / RMC=V。"""
    reader, _ = _run_reader_with_lines([mk(NOFIX_GGA_BODY), mk(NOFIX_RMC_BODY)])
    fix = reader.get_fix()
    # 收到合法 NMEA => source 仍为 real
    assert fix["source"] == "real"
    assert fix["fix_quality"] == 0
    # 但无定位 => 坐标应保持 None
    assert fix["latitude"] is None
    assert fix["longitude"] is None
    assert fix["altitude_m"] is None


def test_reader_data_timeout_to_none():
    """停止喂数据 11 秒后 get_fix() 返回 source='none'（mock time 流逝）。"""
    reader = SerialGNSSReader()
    reader._running.set()  # 模拟运行中
    rec = NMEAParser().parse(mk(GGA_BODY).decode("ascii"))
    reader._merge(rec)
    # 刚喂完：source=real
    assert reader.get_fix()["source"] == "real"
    # 把时间往前拨 11 秒
    now = time.time()
    with mock.patch("mbdsdr_ai.serial_gnss.time.time", return_value=now + 11.0):
        fix = reader.get_fix()
    assert fix["source"] == "none"
    reader._running.clear()


def test_reader_empty_default_is_none():
    """未注入任何串口、未启动时，get_fix() 必须 source='none' 且坐标 None。"""
    reader = SerialGNSSReader()
    fix = reader.get_fix()
    assert fix["source"] == "none"
    assert fix["latitude"] is None
    assert fix["longitude"] is None
    assert fix["satellites"] is None


def test_reader_start_stop_no_device_no_crash():
    """start 一个不存在的端口应返回 False 且不崩溃；stop 幂等。"""
    reader = SerialGNSSReader()
    ok = reader.start("/dev/nonexistent_gnss_port_xyz", 9600)
    assert ok is False
    # stop 两次也不应崩溃
    reader.stop()
    reader.stop()


# ============================================================
# 3. RealGNSSMonitor 集成测试
# ============================================================

def test_monitor_position_mapping():
    from mbdsdr_ai.gnss_monitor import RealGNSSMonitor

    class FakeReader:
        def get_fix(self):
            return {
                "source": "real",
                "latitude": EXPECT_LAT,
                "longitude": EXPECT_LON,
                "altitude_m": 150.0,
                "satellites": 9,
                "hdop": 0.9,
                "speed_kmh": 9.26,
                "course_deg": 30.0,
                "utc_time": "072545.00",
                "fix_quality": 1,
                "timestamp": 1234.5,
            }

        def stop(self):
            pass

    m = RealGNSSMonitor()
    m._reader = FakeReader()
    pos = m.get_position()
    assert pos.source == "real"
    assert abs(pos.lat - EXPECT_LAT) < 1e-3
    assert abs(pos.lon - EXPECT_LON) < 1e-3
    assert abs(pos.alt - 150.0) < 1e-6
    assert pos.sats == 9
    assert abs(pos.hdop - 0.9) < 1e-6
    assert abs(pos.speed - 9.26) < 1e-6
    assert abs(pos.course - 30.0) < 1e-6


def test_monitor_no_device():
    from mbdsdr_ai.gnss_monitor import RealGNSSMonitor

    # reader=None => 无设备
    m = RealGNSSMonitor()
    m._reader = None
    pos = m.get_position()
    assert pos.source == "none"
    assert pos.lat is None
    assert pos.lon is None
    assert pos.alt is None

    # reader 存在但 fix source != real
    class NoneReader:
        def get_fix(self):
            return SerialGNSSReader._empty_fix()

        def stop(self):
            pass

    m2 = RealGNSSMonitor()
    m2._reader = NoneReader()
    pos2 = m2.get_position()
    assert pos2.source == "none"
    assert pos2.lat is None


def test_talker_ids_and_baudrates_exported():
    assert "GP" in TALKER_IDS and "GN" in TALKER_IDS and "BD" in TALKER_IDS
    assert 9600 in DEFAULT_BAUDRATES


# ============================================================
# 4. 拔线 / 串口异常不崩 + 自动重连（mock 串口）
# ============================================================

class _UnplugThenReopen:
    """脚本化假串口：先吐一批字节，再在 readline 里抛 OSError（模拟拔线）。"""

    def __init__(self, script):
        self._script = list(script)
        self.closed = False

    def readline(self):
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return b""

    def close(self):
        self.closed = True


def test_reader_unplug_does_not_crash_and_reconnects():
    """拔线（readline 抛 OSError）后读线程不崩、自动进入重连循环，不刷异常。"""
    gga = mk(GGA_BODY)
    reader = SerialGNSSReader()
    reader._reconnect_interval = 0.02   # 加快测试
    reader._data_timeout = 1.0          # 测试窗口内不靠超时触发断线
    opens = {"n": 0}

    def fake_open(port, baud):
        opens["n"] += 1
        # 重连后给一个安静串口（一帧 GGA 之后静默）
        return _UnplugThenReopen([gga])

    reader._open_serial = fake_open
    reader._port = "/dev/fake_gnss"
    reader._baudrate = 9600
    # 首连：读到一帧 GGA 后立刻抛 OSError 模拟拔线
    reader._ser = _UnplugThenReopen([gga, OSError("USB disconnected")])
    reader._connected = True
    reader._running.set()
    t = threading.Thread(target=reader._read_loop, daemon=True)
    t.start()
    try:
        time.sleep(0.6)  # 等：首帧合并 → 拔线异常被吞 → 重连
        # 不崩即通过；重连至少发生一次
        assert opens["n"] >= 1, "拔线后应触发至少一次重连"
        # 重连后能再读到 fix
        fix = reader.get_fix()
        assert fix["source"] == "real"
    finally:
        reader._running.clear()
        t.join(timeout=2.0)


def test_reader_explicit_port_does_not_autodetect():
    """指定了 port 但 baudrate=None 时不应误走 auto_detect 忽略用户端口。"""
    reader = SerialGNSSReader()
    opened = {}

    def fake_open(port, baud):
        opened["port"] = port
        opened["baud"] = baud
        raise OSError("no such port")  # 打开失败，走 return False 路径

    reader._open_serial = fake_open
    ok = reader.start("COM10", None)
    assert ok is False
    assert opened["port"] == "COM10", "指定 COM10 不应被 auto_detect 覆盖"
    assert opened["baud"] == 9600, "未指定波特率应默认 9600"
    reader.stop()


# ============================================================
# 5. RealGNSSMonitor 新接口：start(port, baud) 参数透传 + is_connected
# ============================================================

def test_monitor_start_accepts_port_baud_args():
    from mbdsdr_ai.gnss_monitor import RealGNSSMonitor

    class StubReader:
        def __init__(self):
            self.calls = []

        def start(self, port, baud):
            self.calls.append((port, baud))
            return True

        def stop(self):
            pass

        def is_connected(self):
            return True

    m = RealGNSSMonitor()
    m._reader = StubReader()
    # 显式传 COM10 / 115200
    assert m.start("COM10", 115200) is True
    assert m._reader.calls == [("COM10", 115200)]
    assert m.is_connected is True
    # 空串 port 应规整为 None（auto_detect）
    m._reader.calls.clear()
    m.start("   ", 9600)
    assert m._reader.calls == [(None, 9600)]


def test_monitor_is_connected_delegates():
    from mbdsdr_ai.gnss_monitor import RealGNSSMonitor

    class OffReader:
        def is_connected(self):
            return False

        def stop(self):
            pass

    m = RealGNSSMonitor()
    m._reader = OffReader()
    assert m.is_connected is False
    # reader=None 也不崩
    m._reader = None
    assert m.is_connected is False
    assert m.start() is False

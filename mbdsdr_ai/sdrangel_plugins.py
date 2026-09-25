"""
MBDSDR AI - SDRangel 特色插件移植（OGN / FireDetector / RemoteControl）
=========================================================================

本模块**不修改** sdrangel_adapter.py，单独把 SDRangel 的三个特色 feature/channelrx
插件移植为纯 NumPy/Python 离线实现。常量/位域对齐 SDRangel 真实源码：

  - OGN (Open Glider Network) 接收器
        真实 OGN 链路: 868.200 MHz, 2-FSK, 9600 baud (CC1101)。
        本移植按任务书给定的 FLARM V6 帧位域解析:
          协议版本 4bit = 6
          飞机 ID 24bit (ICAO)
          纬度 17bit / 经度 17bit (有符号定点)
        参考: SDRangel plugins/channelrx/demodogn (旧版本) 与
              https://github.com/svr-system/ogn-rx 的 FLARM 帧结构。

  - FireDetector 森林火灾检测
        来源: SDRangel plugins/feature/firedetector (旧版本, 已迁出主分支)
        思路: 对热红外频谱/幅度图做阈值分割 -> 连通域(热点)聚类 ->
              输出热点经纬度列表。本移植用简化 DBSCAN 距离聚类。

  - RemoteControl 远程控制协议
        来源: plugins/feature/remotecontrol/remotecontrolsettings.h:28-90
              (RemoteControlControl/RemoteControlSensor/RemoteControlDevice)
        本移植实现一个 JSON 命令/状态信封: 命令带 id/device/command/args,
        响应带 id/ok/status/error，做序列化往返。
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# =====================================================================
# OGN / FLARM V6 帧解析
# =====================================================================
# FLARM/OGN 位置报告位域布局（任务书给定的真实参数）:
#   [0:4]   protocol_version = 6
#   [4:28]  aircraft_id      24 bit (ICAO, 无符号)
#   [28:45] latitude         17 bit 有符号定点: deg = v/131072*180 - 90
#   [45:62] longitude        17 bit 有符号定点: deg = v/131072*360 - 180
#   [62:74] altitude_m      12 bit 无符号 (0..4095 m)
#   [74:84] speed_kts        10 bit 无符号 (0..1023 kt)
#   [84:93] heading_deg      9 bit 无符号 (0..511 -> *0.703125 = 0..360)
# 共 93 bit，按大端打包到 12 字节（末尾补零）。
FLARM_PROTOCOL_VERSION = 6          # 任务书: 协议版本 6
FLARM_AIRCRAFT_ID_BITS = 24
FLARM_LAT_BITS = 17
FLARM_LON_BITS = 17
FLARM_ALT_BITS = 12
FLARM_SPEED_BITS = 10
FLARM_HEADING_BITS = 9
FLARM_TOTAL_BITS = (4 + FLARM_AIRCRAFT_ID_BITS + FLARM_LAT_BITS + FLARM_LON_BITS +
                    FLARM_ALT_BITS + FLARM_SPEED_BITS + FLARM_HEADING_BITS)
FLARM_FRAME_BYTES = (FLARM_TOTAL_BITS + 7) // 8   # 12 bytes

# 工作频点（OGN 真实频点 868.2 MHz；任务书称"1090MHz 类"指 24-bit ICAO 编码风格）
OGN_DEFAULT_FREQ_HZ = 868_200_000  # Open Glider Network EU


@dataclass
class FlarmPositionReport:
    """解出的 FLARM 位置报告。"""
    protocol_version: int
    aircraft_id: int           # 24-bit ICAO / FLARM ID
    lat_deg: float
    lon_deg: float
    alt_m: float
    speed_kts: float
    heading_deg: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "aircraft_id": f"{self.aircraft_id:06X}",
            "lat_deg": round(self.lat_deg, 6),
            "lon_deg": round(self.lon_deg, 6),
            "alt_m": self.alt_m,
            "speed_kts": self.speed_kts,
            "heading_deg": round(self.heading_deg, 2),
        }


def _pack_bits(bits: List[Tuple[int, int]]) -> bytes:
    """按 (value, nbits) 列表大端打包为字节串。"""
    total = sum(n for _, n in bits)
    nbytes = (total + 7) // 8
    out = bytearray(nbytes)
    bit_index = 0
    for value, nbits in bits:
        for i in range(nbits - 1, -1, -1):
            if (value >> i) & 1:
                out[bit_index // 8] |= (1 << (7 - (bit_index % 8)))
            bit_index += 1
    return bytes(out)


def _unpack_bits(buf: bytes, layout: List[Tuple[str, int]]) -> Dict[str, int]:
    """按 (name, nbits) 列表从大端字节串解包为无符号整数。"""
    result: Dict[str, int] = {}
    bit_index = 0
    for name, nbits in layout:
        v = 0
        for _ in range(nbits):
            byte_idx = bit_index // 8
            bit_off = 7 - (bit_index % 8)
            v = (v << 1) | ((buf[byte_idx] >> bit_off) & 1)
            bit_index += 1
        result[name] = v
    return result


def encode_flarm_frame(aircraft_id: int, lat_deg: float, lon_deg: float,
                       alt_m: float = 0.0, speed_kts: float = 0.0,
                       heading_deg: float = 0.0) -> bytes:
    """打包一条 FLARM V6 位置帧（12 字节）。"""
    assert 0 <= aircraft_id < (1 << FLARM_AIRCRAFT_ID_BITS), "aircraft_id 必须 24-bit"
    # 17-bit 有符号定点: lat ∈ [-90, 90] -> 0..2^17
    lat_q = int(round((lat_deg + 90.0) / 180.0 * (1 << FLARM_LAT_BITS)))
    lon_q = int(round((lon_deg + 180.0) / 360.0 * (1 << FLARM_LON_BITS)))
    lat_q = max(0, min((1 << FLARM_LAT_BITS) - 1, lat_q))
    lon_q = max(0, min((1 << FLARM_LON_BITS) - 1, lon_q))
    alt_q = int(round(max(0.0, min(4095.0, alt_m))))
    spd_q = int(round(max(0.0, min(1023.0, speed_kts))))
    hdg_q = int(round(((heading_deg % 360.0) / 360.0) * 511.0))
    return _pack_bits([
        (FLARM_PROTOCOL_VERSION, 4),
        (aircraft_id, FLARM_AIRCRAFT_ID_BITS),
        (lat_q, FLARM_LAT_BITS),
        (lon_q, FLARM_LON_BITS),
        (alt_q, FLARM_ALT_BITS),
        (spd_q, FLARM_SPEED_BITS),
        (hdg_q, FLARM_HEADING_BITS),
    ])


def decode_flarm_frame(buf: bytes) -> FlarmPositionReport:
    """解析一条 FLARM V6 位置帧。

    位域布局见模块顶部注释。
    """
    if len(buf) < FLARM_FRAME_BYTES:
        raise ValueError(f"FLARM 帧长度不足: {len(buf)} < {FLARM_FRAME_BYTES}")
    raw = _unpack_bits(buf, [
        ("proto", 4), ("id", FLARM_AIRCRAFT_ID_BITS),
        ("lat", FLARM_LAT_BITS), ("lon", FLARM_LON_BITS),
        ("alt", FLARM_ALT_BITS), ("spd", FLARM_SPEED_BITS),
        ("hdg", FLARM_HEADING_BITS),
    ])
    if raw["proto"] != FLARM_PROTOCOL_VERSION:
        raise ValueError(f"非 V6 帧: proto={raw['proto']}")
    lat = raw["lat"] / (1 << FLARM_LAT_BITS) * 180.0 - 90.0
    lon = raw["lon"] / (1 << FLARM_LON_BITS) * 360.0 - 180.0
    hdg = raw["hdg"] / 511.0 * 360.0
    return FlarmPositionReport(
        protocol_version=raw["proto"],
        aircraft_id=raw["id"],
        lat_deg=lat, lon_deg=lon,
        alt_m=float(raw["alt"]),
        speed_kts=float(raw["spd"]),
        heading_deg=hdg,
    )


# =====================================================================
# FireDetector —— 阈值 + 热点聚类
# =====================================================================
# 来源: SDRangel plugins/feature/firedetector (旧版 feature)
#   - 输入: 热成像网格 (lat, lon, temperature)
#   - 阈值: temp >= T_HOTSPOT_K 认为是热点
#   - 聚类: 距离 < cluster_km 的热点归为一个火灾点 (简化 DBSCAN)
DEFAULT_HOTSPOT_TEMP_K = 320.0     # 约 47°C，典型林火热点阈值
DEFAULT_CLUSTER_KM = 5.0           # 聚类半径


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


@dataclass
class Hotspot:
    lat_deg: float
    lon_deg: float
    temp_k: float


@dataclass
class FireCluster:
    centroid_lat: float
    centroid_lon: float
    max_temp_k: float
    size: int


class FireDetector:
    """森林火灾热点检测器。

    流程（来源: SDRangel firedetector feature）:
      1. 温度阈值: temp >= threshold_k 标记候选热点
      2. 聚类: 距离 < cluster_km 的候选合并为一个火灾簇
      3. 输出簇质心 + 最高温
    """

    def __init__(self, threshold_k: float = DEFAULT_HOTSPOT_TEMP_K,
                 cluster_km: float = DEFAULT_CLUSTER_KM):
        self.threshold_k = threshold_k
        self.cluster_km = cluster_km

    def detect(self, points: List[Tuple[float, float, float]]
               ) -> List[FireCluster]:
        """points: [(lat_deg, lon_deg, temp_K), ...] -> 火灾簇列表。"""
        candidates = [Hotspot(lat, lon, t) for (lat, lon, t) in points
                      if t >= self.threshold_k]
        if not candidates:
            return []
        # 简化 DBSCAN: 单链聚类
        clusters: List[List[Hotspot]] = []
        for hs in candidates:
            placed = False
            for cl in clusters:
                # 与簇内任一点距离 < cluster_km 即归入
                if any(_haversine_km(hs.lat_deg, hs.lon_deg,
                                      c.lat_deg, c.lon_deg) < self.cluster_km
                       for c in cl):
                    cl.append(hs)
                    placed = True
                    break
            if not placed:
                clusters.append([hs])
        result: List[FireCluster] = []
        for cl in clusters:
            n = len(cl)
            clat = sum(c.lat_deg for c in cl) / n
            clon = sum(c.lon_deg for c in cl) / n
            tmax = max(c.temp_k for c in cl)
            result.append(FireCluster(clat, clon, tmax, n))
        return result


# =====================================================================
# RemoteControl —— JSON 命令/状态往返
# =====================================================================
# 来源: plugins/feature/remotecontrol/remotecontrolsettings.h:28-90
#   RemoteControlControl { m_id, m_labelLeft, m_labelRight }
#   RemoteControlSensor  { m_id, m_labelLeft, m_labelRight, m_format, m_plot }
#   RemoteControlDevice { m_protocol, m_label, controls[], sensors[] }
# 命令/状态 JSON 信封仿照 SDRangel reverse API (QJsonObject):
#   命令: {"id":1, "device":"R0", "command":"set_center_frequency",
#          "args": {"center_frequency": 145000000}}
#   响应: {"id":1, "device":"R0", "ok": true,
#          "status": {"center_frequency":145000000, "sample_rate":1024000}}
REMOTE_CONTROL_COMMANDS = {
    "set_center_frequency": {"center_frequency": int},
    "set_sample_rate": {"sample_rate": int},
    "set_doppler": {"rx_frequency": float},
    "start_rx": {},
    "stop_rx": {},
    "query_status": {},
}


@dataclass
class RemoteDeviceState:
    """SDRangel 设备当前状态（可被 RemoteControl 查询/修改）。"""
    device_id: str
    center_frequency_hz: int = 100_000_000
    sample_rate_hz: int = 1_024_000
    rx_frequency_hz: float = 100_000_000.0
    running: bool = False

    def status_dict(self) -> Dict[str, Any]:
        return {
            "device": self.device_id,
            "center_frequency": self.center_frequency_hz,
            "sample_rate": self.sample_rate_hz,
            "rx_frequency": self.rx_frequency_hz,
            "running": self.running,
        }


class RemoteControlProtocol:
    """SDRangel RemoteControl JSON 协议往返。

    命令 JSON -> 校验 -> 应用到设备状态 -> 状态 JSON 响应。
    """

    def __init__(self, device: Optional[RemoteDeviceState] = None):
        self.device = device or RemoteDeviceState("R0")
        self._next_id = 1

    def encode_command(self, command: str, args: Optional[Dict[str, Any]] = None,
                       cmd_id: Optional[int] = None) -> str:
        """把命令序列化为 JSON 字符串（线格式）。"""
        envelope = {
            "id": cmd_id if cmd_id is not None else self._next_id,
            "device": self.device.device_id,
            "command": command,
            "args": args or {},
        }
        self._next_id += 1
        return json.dumps(envelope, separators=(",", ":"))

    def decode_and_apply(self, payload: str) -> str:
        """接收 JSON 命令字符串 -> 应用 -> 返回 JSON 响应字符串。"""
        try:
            env = json.loads(payload)
        except json.JSONDecodeError as e:
            return json.dumps({"ok": False, "error": f"bad_json: {e}"})
        cmd = env.get("command")
        if cmd not in REMOTE_CONTROL_COMMANDS:
            return json.dumps({"id": env.get("id"), "ok": False,
                               "error": f"unknown_command: {cmd}"})
        args = env.get("args", {}) or {}
        if cmd == "set_center_frequency":
            self.device.center_frequency_hz = int(args["center_frequency"])
        elif cmd == "set_sample_rate":
            self.device.sample_rate_hz = int(args["sample_rate"])
        elif cmd == "set_doppler":
            self.device.rx_frequency_hz = float(args["rx_frequency"])
        elif cmd == "start_rx":
            self.device.running = True
        elif cmd == "stop_rx":
            self.device.running = False
        return json.dumps({
            "id": env.get("id"),
            "device": self.device.device_id,
            "ok": True,
            "status": self.device.status_dict(),
        }, separators=(",", ":"))


# =====================================================================
# 工具入口
# =====================================================================
def tool_ogn_decode_frame(args: Dict[str, Any]) -> Dict[str, Any]:
    """解析 FLARM V6 帧 (hex)。"""
    frame = bytes.fromhex(args["frame_hex"])
    rep = decode_flarm_frame(frame)
    return {"report": rep.to_dict(), "freq_hz": OGN_DEFAULT_FREQ_HZ,
            "method": "SDRangel-ogn FLARM v6"}


def tool_firedetector_detect(args: Dict[str, Any]) -> Dict[str, Any]:
    """对合成/实测热点列表做阈值+聚类。"""
    pts = [(float(p["lat"]), float(p["lon"]), float(p["temp_k"]))
           for p in args["points"]]
    det = FireDetector(float(args.get("threshold_k", DEFAULT_HOTSPOT_TEMP_K)),
                       float(args.get("cluster_km", DEFAULT_CLUSTER_KM)))
    clusters = det.detect(pts)
    return {
        "cluster_count": len(clusters),
        "clusters": [
            {"lat": round(c.centroid_lat, 5), "lon": round(c.centroid_lon, 5),
             "max_temp_k": round(c.max_temp_k, 2), "size": c.size}
            for c in clusters
        ],
        "method": "SDRangel-firedetector",
    }


def tool_remotecontrol_roundtrip(args: Dict[str, Any]) -> Dict[str, Any]:
    """RemoteControl JSON 命令往返：发命令->应用->回状态。"""
    proto = RemoteControlProtocol(
        RemoteDeviceState("R0",
                          center_frequency_hz=int(args.get("freq", 100e6)),
                          sample_rate_hz=int(args.get("sr", 1024000))))
    out: List[Dict[str, Any]] = []
    for cmd in args.get("commands", []):
        wire = proto.encode_command(cmd["command"], cmd.get("args", {}))
        resp = proto.decode_and_apply(wire)
        out.append({"request": json.loads(wire),
                    "response": json.loads(resp)})
    return {"roundtrip": out,
            "method": "SDRangel-remotecontrol QJsonObject"}


# =====================================================================
# 注册
# =====================================================================
def register_sdrangel_plugins_tools(registry) -> None:
    """把 OGN / FireDetector / RemoteControl 三个 SDRangel 插件注册到 ToolRegistry。

    提供三个工具：
      - sdrangel_ogn_decode       : 解析 FLARM V6 位置帧
      - sdrangel_firedetector     : 热点阈值 + 聚类
      - sdrangel_remotecontrol    : JSON 命令/状态往返
    """
    from .tool_registry import ToolResult

    def _ogn(args):
        try:
            data = tool_ogn_decode_frame(args)
            return ToolResult(success=True,
                              content=f"FLARM 帧解出: {data['report']}",
                              data=data)
        except Exception as e:
            return ToolResult(success=False, content=f"OGN 解析失败: {e}",
                              error="ogn_decode_failed")

    def _fire(args):
        data = tool_firedetector_detect(args)
        return ToolResult(success=True,
                          content=f"检出 {data['cluster_count']} 个火灾簇",
                          data=data)

    def _rc(args):
        data = tool_remotecontrol_roundtrip(args)
        return ToolResult(success=True,
                          content=f"RemoteControl 往返 {len(data['roundtrip'])} 条命令",
                          data=data)

    registry.register(
        name="sdrangel_ogn_decode",
        description="SDRangel OGN 接收器：解析 FLARM V6 位置帧(hex)，解出 24bit 飞机ID/经纬度/高度",
        parameters={
            "type": "object",
            "properties": {
                "frame_hex": {"type": "string", "description": "12 字节 FLARM 帧的 hex"},
            },
            "required": ["frame_hex"],
        },
        handler=_ogn,
        category="sat_groundstation",
    )
    registry.register(
        name="sdrangel_firedetector",
        description="SDRangel FireDetector：对热红外热点(lat,lon,temp_K)做阈值分割+距离聚类",
        parameters={
            "type": "object",
            "properties": {
                "threshold_k": {"type": "number"},
                "cluster_km": {"type": "number"},
                "points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "lat": {"type": "number"},
                            "lon": {"type": "number"},
                            "temp_k": {"type": "number"},
                        },
                    },
                },
            },
            "required": ["points"],
        },
        handler=_fire,
        category="sat_groundstation",
    )
    registry.register(
        name="sdrangel_remotecontrol",
        description="SDRangel RemoteControl：JSON 命令(设备/命令/参数)往返到设备状态",
        parameters={
            "type": "object",
            "properties": {
                "freq": {"type": "number"}, "sr": {"type": "number"},
                "commands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "args": {"type": "object"},
                        },
                    },
                },
            },
            "required": ["commands"],
        },
        handler=_rc,
        category="sat_groundstation",
    )

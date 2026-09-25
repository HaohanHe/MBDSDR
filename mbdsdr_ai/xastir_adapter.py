"""
mbdsdr_ai/xastir_adapter.py
=============================
Xastir (xastir/xastir) APRS 地图/对象工作站的 Python 学习移植。

移植内容：
  - APRS 对象/物品 (Object/Item) 报文帧编解码
      来源: xastir src/objects.c:Create_object_item_tx_string (objects.c:142)
      对象帧:  ';' + 9字符名 + 存活标志('*'活/'_'死) + 8字节纬度
               + 符号表 + 9字节经度 + 符号码 + 注释
      物品帧:  ')' + 最长9字符名(空格结尾) + 8字节纬度 + 符号表
               + 9字节经度 + 符号码 + 注释
  - 未压缩位置编码 DDMM.hhN / DDDMM.hhW
      来源: direwolf decode_aprs.c:3585-3700 (本仓库 aprs_parser.py 已引)
  - 地理围栏 (geofence): 圆心+半径判定

参考：xastir GPL-2.0。本移植纯 Python，不依赖 X11/地图库。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# =====================================================================
# 位置编解码 —— 未压缩 APRS 经纬度
# =====================================================================
# 纬度 8 字节: DDMM.hhN  (direwolf decode_aprs.c:3585)
# 经度 9 字节: DDDMM.hhW (direwolf decode_aprs.c:3620)
def encode_latitude(lat: float) -> str:
    """纬度 -> 8 字节 'DDMM.hhN'。南纬 S，北纬 N。"""
    hemi = 'N' if lat >= 0 else 'S'
    a = abs(lat)
    deg = int(a)
    minute = (a - deg) * 60.0
    return f"{deg:02d}{minute:05.2f}{hemi}"


def decode_latitude(s: str) -> float:
    s = s.strip()
    hemi = s[-1].upper()
    body = s[:-1]
    deg = int(body[:2])
    minute = float(body[2:])
    val = deg + minute / 60.0
    return val if hemi == 'N' else -val


def encode_longitude(lon: float) -> str:
    """经度 -> 9 字节 'DDDMM.hhW'。西经 W，东经 E。"""
    hemi = 'E' if lon >= 0 else 'W'
    a = abs(lon)
    deg = int(a)
    minute = (a - deg) * 60.0
    return f"{deg:03d}{minute:05.2f}{hemi}"


def decode_longitude(s: str) -> float:
    s = s.strip()
    hemi = s[-1].upper()
    body = s[:-1]
    deg = int(body[:3])
    minute = float(body[3:])
    val = deg + minute / 60.0
    return val if hemi == 'E' else -val


# =====================================================================
# APRS 对象 / 物品帧
# =====================================================================
@dataclass
class APRSObject:
    """一个 APRS 对象（13 字节头: ';' + 9名 + 标志）。

    来源: xastir src/objects.c:142 Create_object_item_tx_string。
    """

    name: str                 # <= 9 字符
    lat: float
    lon: float
    live: bool = True         # '*'=live, '_'=killed
    symbol_table: str = '/'   # '/'=标准, '\\'=备用
    symbol_code: str = '>'    # '>'=电台车
    comment: str = ""

    def encode(self) -> bytes:
        # 9 字符名，空格补齐 (objects.c 用 call_sign 最长9)
        nm = self.name[:9].ljust(9)
        flag = '*' if self.live else '_'
        lat_s = encode_latitude(self.lat)     # 8 字节
        lon_s = encode_longitude(self.lon)    # 9 字节
        pkt = f";{nm}{flag}{lat_s}{self.symbol_table}{lon_s}{self.symbol_code}"
        pkt += self.comment
        return pkt.encode("ascii", "replace")


@dataclass
class APRSItem:
    """一个 APRS 物品（' ) ' 帧，名最长 9 字符，空格结尾）。

    来源: xastir src/objects.c:184 (else 分支 = item)。
    """

    name: str                 # <= 9 字符
    lat: float
    lon: float
    symbol_table: str = '/'
    symbol_code: str = '>'
    comment: str = ""

    def encode(self) -> bytes:
        nm = self.name[:9]
        lat_s = encode_latitude(self.lat)
        lon_s = encode_longitude(self.lon)
        pkt = f"){nm} {lat_s}{self.symbol_table}{lon_s}{self.symbol_code}"
        pkt += self.comment
        return pkt.encode("ascii", "replace")


def decode_object_or_item(pkt: bytes) -> Dict[str, Any]:
    """解析 APRS 对象(';')或物品(')')报文。"""
    s = pkt.decode("ascii", "replace")
    if s.startswith(";"):
        name = s[1:10].rstrip()
        flag = s[10]
        lat_s = s[11:19]
        stbl = s[19]
        lon_s = s[20:29]
        scode = s[29]
        comment = s[30:]
        return {
            "kind": "object", "name": name, "live": flag == '*',
            "lat": decode_latitude(lat_s), "lon": decode_longitude(lon_s),
            "symbol_table": stbl, "symbol_code": scode, "comment": comment,
        }
    elif s.startswith(")"):
        # 名到第一个空格为止
        rest = s[1:]
        sp = rest.find(" ")
        name = rest[:sp]
        body = rest[sp + 1:]
        lat_s = body[0:8]
        stbl = body[8]
        lon_s = body[9:18]
        scode = body[18]
        comment = body[19:]
        return {
            "kind": "item", "name": name,
            "lat": decode_latitude(lat_s), "lon": decode_longitude(lon_s),
            "symbol_table": stbl, "symbol_code": scode, "comment": comment,
        }
    raise ValueError("不是 APRS 对象/物品帧（应以 ';' 或 ')' 开头）")


# =====================================================================
# 地理围栏
# =====================================================================
EARTH_RADIUS_M = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点间大圆距离（米）。"""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass
class GeoFence:
    """圆形地理围栏：圆心 + 半径。

    来源：xastir 用 objects.c 的 area_object（矩形/走廊）；
    这里学习实现最常用的圆形围栏。
    """

    name: str
    center_lat: float
    center_lon: float
    radius_m: float

    def contains(self, lat: float, lon: float) -> bool:
        return haversine_m(self.center_lat, self.center_lon, lat, lon) <= self.radius_m

    def filter(self, points: List[Tuple[float, float]]) -> List[bool]:
        return [self.contains(la, lo) for la, lo in points]


# =====================================================================
# 工具注册
# =====================================================================
def register_xastir_tools(registry) -> None:
    """注册 Xastir APRS 对象/物品与地理围栏工具。"""
    from mbdsdr_ai.tool_registry import ToolResult

    def _encode_object(args: Dict[str, Any]) -> ToolResult:
        try:
            obj = APRSObject(
                name=args.get("name", "OBJ"),
                lat=float(args["lat"]), lon=float(args["lon"]),
                live=bool(args.get("live", True)),
                symbol_table=args.get("symbol_table", "/"),
                symbol_code=args.get("symbol_code", ">"),
                comment=args.get("comment", ""),
            )
            pkt = obj.encode()
            return ToolResult(
                success=True,
                content=f"APRS 对象帧: {pkt.decode('ascii','replace')}",
                data={"packet": pkt.decode("ascii", "replace"),
                      "n_bytes": len(pkt)},
            )
        except Exception as e:
            return ToolResult(False, f"对象编码失败: {e}")

    def _decode_frame(args: Dict[str, Any]) -> ToolResult:
        try:
            pkt = args["packet"].encode("ascii")
            res = decode_object_or_item(pkt)
            return ToolResult(
                success=True,
                content=f"APRS {res['kind']}: {res['name']} "
                        f"({res['lat']:.4f},{res['lon']:.4f})",
                data=res,
            )
        except Exception as e:
            return ToolResult(False, f"帧解码失败: {e}")

    def _geofence(args: Dict[str, Any]) -> ToolResult:
        try:
            fence = GeoFence(
                name=args.get("name", "fence"),
                center_lat=float(args["center_lat"]),
                center_lon=float(args["center_lon"]),
                radius_m=float(args["radius_m"]),
            )
            pts = [(float(p[0]), float(p[1])) for p in args.get("points", [])]
            inside = fence.filter(pts)
            dists = [round(haversine_m(fence.center_lat, fence.center_lon, la, lo), 1)
                     for la, lo in pts]
            return ToolResult(
                success=True,
                content=f"围栏 '{fence.name}' r={fence.radius_m:.0f}m: "
                        f"{sum(inside)}/{len(pts)} 点在内",
                data={"inside": inside, "distances_m": dists},
            )
        except Exception as e:
            return ToolResult(False, f"地理围栏失败: {e}")

    registry.register(
        name="xastir_encode_object",
        description="编码 APRS 对象帧 (';'+9名+* +未压缩经纬度+符号)。"
                    "来源: xastir src/objects.c:142。",
        parameters={"type": "object", "properties": {
            "name": {"type": "string"}, "lat": {"type": "number"},
            "lon": {"type": "number"}, "live": {"type": "boolean"},
            "symbol_table": {"type": "string"}, "symbol_code": {"type": "string"},
            "comment": {"type": "string"},
        }, "required": ["lat", "lon"]},
        handler=_encode_object,
        category="ham_modes",
    )
    registry.register(
        name="xastir_decode_frame",
        description="解码 APRS 对象(';')或物品(')')帧为结构化字段（含经纬度、符号、注释）。",
        parameters={"type": "object", "properties": {
            "packet": {"type": "string"},
        }, "required": ["packet"]},
        handler=_decode_frame,
        category="ham_modes",
    )
    registry.register(
        name="xastir_geofence",
        description="圆形 APRS 地理围栏：圆心+半径(米)判定一组 (lat,lon) 是否在内（Haversine）。",
        parameters={"type": "object", "properties": {
            "center_lat": {"type": "number"}, "center_lon": {"type": "number"},
            "radius_m": {"type": "number"},
            "points": {"type": "array", "items": {"type": "array"}},
        }, "required": ["center_lat", "center_lon", "radius_m"]},
        handler=_geofence,
        category="ham_modes",
    )

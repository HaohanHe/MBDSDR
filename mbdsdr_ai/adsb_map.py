"""
ADS-B 地图投影与航空器跟踪（纯 Python，无 Qt 依赖）
====================================================

本模块只做几何与数据归并，不画界面、不联网取瓦片：

  * ``project_equirectangular``  等距圆柱投影，把 (lat, lon) 映射到画布像素。
  * ``haversine_km``             大圆距离（公里）。
  * ``Aircraft``                 单架航空器的状态记录。
  * ``AircraftTracker``          按 ICAO 十六进制码归并多帧信息：呼号、
                                 气压高度、地速/航向可能来自不同帧，位置
                                 (lat/lon) 以最新一次配对成功的 CPR 位置覆盖。
  * ``WORLD_LAND_POLYGONS``      离线简化大陆轮廓（等距圆柱投影底图），
                                 仅用于低精度示意，不替代任何测绘数据。

数据来源约定：``update(frame_dict)`` 接收 ``mbdsdr_ai.adsb.ADSBDecoder.handle``
返回的 dict，字段包括 ``icao``(hex)、``callsign``、``altitude_ft``、
``velocity``(含 ``groundspeed_kt``/``track_deg``) 以及配对成功后才出现的
``lat``/``lon``。没有位置帧时不臆造坐标，``lat``/``lon`` 保持 None。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

__all__ = [
    "project_equirectangular",
    "haversine_km",
    "Aircraft",
    "AircraftTracker",
    "WORLD_LAND_POLYGONS",
]

# 平均地球半径（公里），用于大圆距离。
_EARTH_R_KM = 6371.0


def project_equirectangular(lat: float, lon: float,
                            width: float, height: float,
                            padding: float = 10.0) -> Tuple[float, float]:
    """等距圆柱投影：把经纬度映射为画布像素坐标 (x, y)。

    投影公式（与画布宽高无关的线性映射）::

        x = padding + (lon + 180) / 360 * (width  - 2*padding)
        y = padding + (90  - lat)    / 180 * (height - 2*padding)

    其中 lat 取值范围 [-90, 90]，lon 取值范围 [-180, 180]；
    结果再 clamp 到 [0, width] / [0, height]，避免极端值越出画布。
    """
    # 先把经纬度夹到合法范围，再做线性映射。
    lat = max(-90.0, min(90.0, float(lat)))
    lon = max(-180.0, min(180.0, float(lon)))

    x = padding + (lon + 180.0) / 360.0 * (width - 2.0 * padding)
    y = padding + (90.0 - lat) / 180.0 * (height - 2.0 * padding)

    x = max(0.0, min(float(width), x))
    y = max(0.0, min(float(height), y))
    return x, y


def haversine_km(lat1: float, lon1: float,
                 lat2: float, lon2: float) -> float:
    """用 Haversine 公式计算两点间大圆距离（公里）。"""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)

    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return _EARTH_R_KM * c


@dataclass
class Aircraft:
    """单架航空器的归并状态。

    位置 (lat/lon) 只在 CPR 偶/奇帧配对成功后才有值；在此之前保持 None，
    调用方据此判断能否上图、能否算距离。
    """

    icao_hex: str
    callsign: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude_ft: Optional[float] = None
    groundspeed_kt: Optional[float] = None
    track_deg: Optional[float] = None
    last_seen: float = 0.0


class AircraftTracker:
    """按 ICAO 码归并多帧 ADS-B 信息的有状态跟踪器。

    - 呼号、气压高度、地速/航向可能分别来自不同帧（识别帧 / 速度帧 /
      位置帧），本跟踪器做字段级合并，不互相覆盖。
    - ``lat``/``lon`` 一旦拿到就被新位置覆盖（位置帧自带最新解算结果）。
    - ``prune(ttl_sec)`` 清理长时间未更新的航空器；``all()`` 按最近
      一次见到的时间倒序返回，方便列表优先显示最新活动目标。
    """

    def __init__(self) -> None:
        self._ac: Dict[str, Aircraft] = {}

    # ------------------------------------------------------------------ #
    def update(self, frame: dict) -> Optional[Aircraft]:
        """合并一帧解码器输出；缺少 ``icao`` 时直接忽略。"""
        icao = frame.get("icao")
        if not icao:
            return None
        icao = str(icao).upper()

        ac = self._ac.get(icao)
        if ac is None:
            ac = Aircraft(icao_hex=icao)
            self._ac[icao] = ac

        # 呼号识别帧：仅在本帧真的带了呼号时覆盖（空串不清除已知呼号）。
        cs = frame.get("callsign")
        if cs:
            ac.callsign = str(cs).strip()

        # 气压高度帧：None 表示本帧未携带，不动已有值。
        alt = frame.get("altitude_ft")
        if alt is not None:
            ac.altitude_ft = alt

        # 速度帧：velocity 是 dict，含 groundspeed_kt / track_deg。
        vel = frame.get("velocity") or {}
        gs = vel.get("groundspeed_kt")
        if gs is not None:
            ac.groundspeed_kt = gs
        trk = vel.get("track_deg")
        if trk is not None:
            ac.track_deg = trk

        # 位置：只有配对成功的位置帧才带 lat/lon，直接覆盖。
        lat = frame.get("lat")
        lon = frame.get("lon")
        if lat is not None:
            ac.lat = float(lat)
        if lon is not None:
            ac.lon = float(lon)

        ac.last_seen = time.time()
        return ac

    # ------------------------------------------------------------------ #
    def prune(self, ttl_sec: float = 60.0) -> None:
        """删除超过 ``ttl_sec`` 未更新的航空器。"""
        now = time.time()
        stale = [k for k, a in self._ac.items()
                 if now - a.last_seen >= ttl_sec]
        for k in stale:
            del self._ac[k]

    def all(self) -> List[Aircraft]:
        """按 ``last_seen`` 降序返回全部航空器。"""
        return sorted(self._ac.values(),
                      key=lambda a: a.last_seen, reverse=True)

    def clear(self) -> None:
        """清空跟踪表。"""
        self._ac.clear()

    def __len__(self) -> int:
        return len(self._ac)


# --------------------------------------------------------------------------- #
# 离线简化大陆轮廓：每个多边形是闭合的 (lon, lat) 顶点列表。
# 仅做低精度示意底图，线条粗犷，不追求测绘精度；不联网、不取瓦片。
# 顶点全部取整数值，避免引入精确到小数点后多位的真实地标坐标。
# --------------------------------------------------------------------------- #
WORLD_LAND_POLYGONS: List[List[Tuple[float, float]]] = [
    # 北美洲（含阿拉斯加、加拿大、美国本土、中美地峡）
    [
        (-168, 66), (-165, 60), (-156, 58), (-152, 59), (-140, 60),
        (-128, 52), (-125, 48), (-124, 40), (-117, 33), (-110, 23),
        (-105, 20), (-97, 16), (-92, 15), (-88, 16), (-85, 21),
        (-81, 25), (-80, 31), (-75, 35), (-74, 40), (-67, 45),
        (-60, 47), (-55, 52), (-60, 58), (-70, 62), (-80, 66),
        (-85, 70), (-95, 72), (-110, 72), (-125, 70), (-140, 70),
        (-155, 71), (-168, 66),
    ],
    # 格陵兰
    [
        (-45, 60), (-40, 65), (-30, 68), (-22, 70), (-20, 76),
        (-30, 82), (-45, 83), (-58, 80), (-55, 74), (-52, 68),
        (-45, 60),
    ],
    # 南美洲
    [
        (-79, 9), (-75, 11), (-70, 12), (-62, 11), (-52, 5),
        (-50, 0), (-44, -3), (-38, -6), (-35, -8), (-39, -14),
        (-41, -22), (-48, -28), (-53, -34), (-58, -38), (-62, -41),
        (-65, -45), (-68, -50), (-69, -54), (-72, -54), (-74, -48),
        (-73, -40), (-72, -30), (-70, -20), (-70, -12), (-76, -6),
        (-80, -3), (-81, 2), (-79, 9),
    ],
    # 欧洲（含地中海北岸，粗轮廓）
    [
        (-10, 36), (-9, 43), (-2, 44), (0, 46), (-4, 48),
        (-1, 50), (2, 51), (5, 53), (8, 55), (8, 57),
        (10, 59), (12, 55), (18, 55), (24, 58), (25, 60),
        (30, 60), (28, 56), (30, 50), (40, 47), (38, 42),
        (30, 41), (28, 37), (23, 36), (19, 40), (15, 38),
        (12, 44), (3, 42), (-2, 37), (-10, 36),
    ],
    # 非洲
    [
        (-17, 15), (-16, 20), (-10, 28), (-2, 32), (10, 34),
        (20, 32), (32, 31), (34, 28), (43, 12), (51, 11),
        (48, 5), (40, -2), (40, -10), (36, -20), (32, -26),
        (26, -34), (20, -35), (18, -30), (15, -22), (12, -10),
        (9, -1), (8, 4), (4, 6), (-8, 5), (-13, 8), (-17, 15),
    ],
    # 亚洲（土耳其→西伯利亚→东亚→东南亚→南亚的粗轮廓）
    [
        (26, 40), (30, 45), (40, 47), (50, 45), (55, 50),
        (60, 55), (70, 60), (80, 68), (90, 73), (105, 76),
        (120, 73), (135, 71), (145, 70), (155, 68), (163, 69),
        (170, 66), (180, 65), (178, 64), (165, 60), (160, 58),
        (162, 52), (155, 50), (142, 45), (135, 35), (127, 38),
        (122, 30), (115, 22), (108, 18), (105, 10), (103, 5),
        (100, 8), (97, 16), (92, 21), (88, 22), (80, 15),
        (73, 20), (68, 24), (60, 25), (57, 26), (52, 28),
        (48, 30), (44, 33), (36, 36), (30, 37), (26, 40),
    ],
    # 澳洲
    [
        (114, -22), (114, -28), (115, -34), (120, -33), (124, -32),
        (129, -31), (132, -32), (136, -35), (139, -37), (144, -38),
        (147, -38), (150, -37), (153, -30), (153, -25), (146, -19),
        (142, -11), (136, -12), (132, -11), (126, -14), (122, -17),
        (114, -22),
    ],
    # 南极洲（沿纬圈的粗带状闭合多边形）
    [
        (-180, -72), (-150, -74), (-120, -73), (-90, -72), (-60, -68),
        (-30, -70), (0, -70), (30, -69), (60, -67), (90, -66),
        (120, -66), (150, -68), (180, -70), (180, -85), (90, -85),
        (0, -85), (-90, -85), (-180, -85), (-180, -72),
    ],
]

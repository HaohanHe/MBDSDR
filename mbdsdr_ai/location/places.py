# SPDX-License-Identifier: MIT
"""地点检索（城市 / 地名 → 经纬度）。

## 为什么单独一层

界面只该问两件事：**「这个名字在哪」** 和 **「这个坐标附近叫什么」**。
至于背后是 OpenStreetMap、离线城市表、还是将来要接的高德，界面不该知道。
所以这里只有一个 ``PlaceProvider`` 协议，实现可替换：

* ``OsmNominatimProvider``——在线查询 OSM Nominatim（当前默认）。实测可用，
  直接返回中文名（``accept-language=zh``），带 ODbL 声明。
* ``OfflineJsonProvider``——读内置城市表。表不存在时 ``available()``
  返回 (False, 原因)，**不假装能搜**。
* 将来接高德：新增 ``AmapPlaceProvider``，界面与主窗一行不改。

## 数据与许可证

OSM 数据 © OpenStreetMap contributors，ODbL 1.0。本模块只发查询、不缓存
数据库；若用离线表，表文件自带署名与许可证，见 ``mbdsdr_ai/data/places/``。

## 诚实边界

* 搜不到就是搜不到，不编一个"最近的大城市"顶上；
* 时区不做经度推算（``tz`` 拿不到就留 ``None``，界面写"未收录"）；
* 任何网络异常都只记 ``last_error`` 并返回空列表，绝不抛给 UI 线程。
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

__all__ = [
    "Place", "PlaceProvider", "OsmNominatimProvider", "OfflineJsonProvider",
    "make_provider", "USER_AGENT", "MIN_INTERVAL_S",
]

#: OSM 使用政策要求可识别的 User-Agent（默认的 Python-urllib 会被 406 拒）。
USER_AGENT = ("MBDSDR/1.0 (amateur-radio sky app; "
              "+https://github.com/HaohanHe/MBDSDR)")

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"

#: OSM 政策：绝对上限 1 req/s。
MIN_INTERVAL_S = 1.0

#: 默认离线表位置（不存在就由 OfflineJsonProvider 明确报不可用）。
_DEFAULT_TABLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "places", "osm_places.json")


@dataclass(frozen=True)
class Place:
    """一个地点。字段全部来自数据源，缺的留空/None，不做二次编造。"""

    name: str                    # 显示名（优先中文）
    lat: float
    lon: float
    alt_m: Optional[float] = None
    country: str = ""
    tz: Optional[str] = None     # 数据源没给就是 None，不按经度推算
    rank: float = 0.0            # 排序权重（Nominatim importance / 人口量级）
    kind: str = ""               # city / town / village / boundary …
    detail: str = ""             # 完整地址串（display_name）
    source: str = "osm"

    def coord_text(self) -> str:
        return f"{self.lat:.4f}°, {self.lon:.4f}°"


class PlaceProvider(Protocol):
    """地点检索的最小接口。实现必须**永不抛异常**给 UI。"""

    def available(self) -> Tuple[bool, str]:
        """(能否用, 不能用的原因)。"""
        ...

    def search(self, query: str, limit: int = 8) -> List[Place]:
        ...

    def nearest(self, lat: float, lon: float) -> Optional[Place]:
        ...


# --------------------------------------------------------------------------- #
# HTTP 小工具（单独抽出，方便测试注入/替换）
# --------------------------------------------------------------------------- #
def _http_get_json(url: str, *, timeout: float = 6.0) -> Any:
    """GET 一个 JSON；任何异常都抛给调用方统一记账。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    # 回环/内网不该走代理（与 MCP 客户端同一教训：代理会把真实错误伪装成 502）
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1"):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    else:
        opener = urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _f(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _place_from_nominatim(item: Dict[str, Any]) -> Optional[Place]:
    lat, lon = _f(item.get("lat")), _f(item.get("lon"))
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    display = str(item.get("display_name") or "")
    # jsonv2 的 name 是本地语名；缺失时退回显示名的首段（"北京市, 中国" → "北京市"）
    name = str(item.get("name") or "").strip()
    if not name and display:
        name = display.split(",")[0].strip()
    if not name:
        return None
    addr = item.get("address") if isinstance(item.get("address"), dict) else {}
    return Place(
        name=name,
        lat=lat,
        lon=lon,
        country=str(addr.get("country") or ""),
        rank=float(item.get("importance") or 0.0),
        kind=str(item.get("type") or item.get("class") or ""),
        detail=display,
        source="osm",
    )


class OsmNominatimProvider:
    """在线 OSM 地名检索（Nominatim）。

    节流：全局 1 req/s（实例内共享 ``_last_call``）。超时 6 s。
    失败只记录 ``last_error``，返回空表——UI 侧据此显示「离线」降级提示。
    """

    def __init__(self, timeout: float = 6.0, min_interval: float = MIN_INTERVAL_S):
        self.timeout = float(timeout)
        self.min_interval = max(0.0, float(min_interval))
        self._last_call = 0.0
        self.last_error = ""

    # -- 内部 ---------------------------------------------------------------
    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, url: str) -> Any:
        self._throttle()
        try:
            data = _http_get_json(url, timeout=self.timeout)
            self.last_error = ""
            return data
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    # -- 协议 ---------------------------------------------------------------
    def available(self) -> Tuple[bool, str]:
        # 网络是否通只有发请求才知道；这里不假装能探测。
        return True, ""

    def search(self, query: str, limit: int = 8) -> List[Place]:
        q = (query or "").strip()
        if not q:
            return []
        params = urllib.parse.urlencode({
            "q": q,
            "format": "jsonv2",
            "limit": max(1, min(int(limit), 20)),
            "accept-language": "zh",
            "addressdetails": "1",
        })
        data = self._get(f"{NOMINATIM_SEARCH}?{params}")
        if not isinstance(data, list):
            return []
        out: List[Place] = []
        for item in data:
            if isinstance(item, dict):
                p = _place_from_nominatim(item)
                if p is not None:
                    out.append(p)
        return out

    def nearest(self, lat: float, lon: float) -> Optional[Place]:
        params = urllib.parse.urlencode({
            "lat": f"{float(lat):.6f}",
            "lon": f"{float(lon):.6f}",
            "format": "jsonv2",
            "accept-language": "zh",
            "zoom": "10",
        })
        data = self._get(f"{NOMINATIM_REVERSE}?{params}")
        if not isinstance(data, dict):
            return None
        return _place_from_nominatim(data)


class OfflineJsonProvider:
    """内置城市表（由 ``scripts/build_place_index.py`` 生成）。

    表结构::

        {"meta": {...ODbL/来源/生成时间...},
         "places": [["北京", 39.9042, 116.4074, 21500000, "CN"], ...]}

    行数组而不是对象数组：几十万点时体积差一半以上。
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or _DEFAULT_TABLE
        self.last_error = ""
        self._places: Optional[List[Place]] = None
        self._load_error = ""

    def _load(self) -> List[Place]:
        if self._places is not None:
            return self._places
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, TypeError) as exc:
            # 面向界面的人话：不把 Python 异常名直接甩给用户
            self._load_error = f"内置城市表不存在或不可读（{type(exc).__name__}）"
            self._places = []
            return self._places
        rows = data.get("places") if isinstance(data, dict) else None
        out: List[Place] = []
        for row in (rows or []):
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            name, lat, lon = row[0], _f(row[1]), _f(row[2])
            if lat is None or lon is None or not name:
                continue
            pop = _f(row[3]) if len(row) > 3 else None
            country = str(row[4]) if len(row) > 4 else ""
            out.append(Place(str(name), lat, lon, country=country,
                             rank=float(pop or 0.0), source="offline"))
        self._places = out
        self._load_error = "" if out else "表为空"
        return out

    def available(self) -> Tuple[bool, str]:
        places = self._load()
        if places:
            return True, ""
        return False, (self._load_error or "未内置离线城市表")

    def search(self, query: str, limit: int = 8) -> List[Place]:
        q = (query or "").strip().lower()
        if not q:
            return []
        hits: List[Tuple[int, float, Place]] = []
        for p in self._load():
            n = p.name.lower()
            if n == q:
                hits.append((0, -p.rank, p))
            elif n.startswith(q):
                hits.append((1, -p.rank, p))
            elif q in n:
                hits.append((2, -p.rank, p))
        hits.sort(key=lambda t: (t[0], t[1]))
        return [p for _, _, p in hits[:max(1, int(limit))]]

    def nearest(self, lat: float, lon: float) -> Optional[Place]:
        best: Optional[Place] = None
        best_d = float("inf")
        for p in self._load():
            d = haversine_km(lat, lon, p.lat, p.lon)
            if d < best_d:
                best_d, best = d, p
        return best


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点大圆距离（公里）。用途：最近地点、UI 距离显示。"""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * r * math.asin(min(1.0, math.sqrt(a)))


def make_provider(kind: str = "auto") -> PlaceProvider:
    """选一个 provider。

    ``auto``：有内置离线表就用离线（快、离线可用）；没有则用在线 OSM。
    ``offline`` / ``osm`` 强制指定（测试与排查用）。
    """
    k = (kind or "auto").strip().lower()
    if k == "offline":
        return OfflineJsonProvider()
    if k in ("osm", "online", "nominatim"):
        return OsmNominatimProvider()
    # auto
    offline = OfflineJsonProvider()
    ok, _reason = offline.available()
    return offline if ok else OsmNominatimProvider()

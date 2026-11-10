# SPDX-License-Identifier: MIT
"""公用地图服务：OSM 在线瓦片，一套底图给全软件用。

## 为什么是"公用"

2026-11-10 用户定调两件事：

1. **不再自己画地图**。自绘世界轮廓既不准（等距圆柱把高纬拉长、轮廓只有
   几十个点），也有测绘合规风险——没有地图编制资质却出具地图，等于把
   把柄递出去。底图一律用 OpenStreetMap 数据。
2. **地图不是 ADS-B 的附属功能**。地点选择、飞机航路、APRS、天线指向、
   模型要给用户看的东西，都该共用这一套视图，而不是每个面板各画一个。
   所以本模块只提供"底图 + 投影 + 交互 + 图层"，业务层用
   ``add_layer(name, painter)`` 把自己的东西叠上去。

## 能力

- Web Mercator 瓦片；拖拽平移、滚轮以光标为锚缩放、单击点选、双击放大、
  键盘平移/缩放、触屏捏合。
- 三种瓦片风格：``osm``（官方标准·浅色）、``light`` / ``dark``（CARTO 的
  OSM 数据底图，深色版就是"OSM 的暗黑模式"；官方标准瓦片本身没有深色版）。
- 磁盘 + 内存缓存，缓存按风格分目录（同 z/x/y 不同源不会串图）。
- **离线下载**：选定中心/半径/层级范围，批量拉瓦片落盘，带进度、可取消、
  限并发限速；下过之后再打开同一片区域就是秒开，断网也能用。
- 多图层：``add_layer`` / ``remove_layer``，图层拿到 ``project(lat, lon)``
  自己画；ADS-B 飞机、APRS 站点、指向波束、模型标注都用这一条路。

## 四条诚实边界

1. **不内置任何地图数据**：没网/没代理就是没底图。此时深色底 + 真实经纬网
   + 一句"正在加载 / 瓦片未加载（检查联网或系统代理）"，不白屏、不画假海岸线。
2. **署名常驻**：右下角按当前风格显示 OSM（及 CARTO）署名，点击开版权页。
3. **遵守瓦片政策**：可识别 UA、只请求视野内（+1 圈预取）、单页在途上限、
   失败退避、磁盘缓存复用、下载器限并发限速并明确提示用途。
4. **风格可换、来源可查**：``style_key()`` / ``style_attribution()`` 如实
   返回当前用的哪一家，UI 上不冒充"我们自己的地图"。
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QObject, QPointF, QRect, QRectF, QRunnable, Qt, QThreadPool, QTimer, QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QImage, QPainter, QPen, QPixmap,
)
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkProxyFactory, QNetworkReply, QNetworkRequest,
)
from PySide6.QtWidgets import QWidget

__all__ = [
    "TILE_SIZE", "ATTRIBUTION", "STYLES", "DEFAULT_STYLE", "TileStyle",
    "style_for_dark", "get_style", "style_max_zoom",
    "lonlat_to_world", "world_to_lonlat", "tile_range", "tiles_for_bounds",
    "TileCache", "TileLoader", "RegionDownloader", "OsmMapView",
]

TILE_SIZE = 256
MAX_LAT = 85.05112878        # Web Mercator 可表示的纬度极限
MIN_ZOOM = 1
MAX_ZOOM = 20            # 各家上限不同：OSM 官方 19，CARTO 20
MAX_INFLIGHT = 4          # OSM 官方瓦片对并发敏感：12 并发实测被回 "Connection closed"
PREFETCH_RINGS = 1           # 视口外预取 1 圈瓦片（浏览器同款，不至于刷爆服务器）
RETRY_AFTER_S = 15.0         # 失败后退避（不刷服务器，也不至于让地图空白一分钟）
USER_AGENT = ("MBDSDR/1.0 (amateur-radio SDR desktop; "
              "+https://github.com/HaohanHe/MBDSDR)")


# --------------------------------------------------------------------------- #
# 瓦片风格
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TileStyle:
    """一种瓦片来源。

    ``filter`` 是**本地渲染**选项：OSM 官方瓦片只有浅色版，深色主题下
    与其去求第三方 key（CARTO 现在直接返回 "API KEY REQUIRED" 水印图，
    Stadia 401），不如拿同一份 OSM 瓦片在本地做反相+降饱和——数据来源
    不变、署名不变、不多依赖一家服务。处理后结果落盘缓存，只算一次。
    """

    key: str
    label: str
    url: str
    attribution: str
    max_zoom: int = MAX_ZOOM
    dark: bool = False
    filter: str = "none"           # "none" | "invert_dark"
    #: 备用镜像（同一份 OSM 数据、另一台社区服务器）。主源被限流时自动换一个，
    #: 不然用户看到的就是一张空图——2026-11-10 实测：并发 12 打到
    #: tile.openstreetmap.org 被回 "Connection closed"，之后连 curl 都 000。
    mirrors: Tuple[str, ...] = ()


ATTRIBUTION_OSM = "© OpenStreetMap contributors"
ATTRIBUTION_CARTO = "© OpenStreetMap contributors © CARTO"

#: OSM 官方标准瓦片（浅色，社区维护，无深色版）。
OSM_STANDARD = TileStyle(
    key="osm",
    label="标准（OpenStreetMap 官方）",
    url="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution=ATTRIBUTION_OSM,
    max_zoom=19,
    dark=False,
    mirrors=("https://tile.openstreetmap.de/{z}/{x}/{y}.png",),
)
#: 暗色：同一份 OSM 瓦片，本地反相+降饱和。不依赖任何 key，也不会
#: 把"某家商业底图"的水印带进产品。
OSM_DARK = TileStyle(
    key="dark",
    label="暗色（OSM 数据·本地渲染）",
    url="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution=ATTRIBUTION_OSM,
    max_zoom=19,
    dark=True,
    filter="invert_dark",
    mirrors=("https://tile.openstreetmap.de/{z}/{x}/{y}.png",),
)

STYLES: Dict[str, TileStyle] = {s.key: s for s in (OSM_STANDARD, OSM_DARK)}
DEFAULT_STYLE = "osm"
ATTRIBUTION = ATTRIBUTION_OSM       # 兼容旧引用


def get_style(key: Optional[str]) -> TileStyle:
    return STYLES.get(str(key or DEFAULT_STYLE), OSM_STANDARD)


def style_max_zoom(key: str) -> int:
    return max(MIN_ZOOM, min(MAX_ZOOM, int(get_style(key).max_zoom)))


def style_for_dark(dark: bool) -> str:
    """夜间/深色主题 → 暗色瓦片。没有深色版的标准瓦片硬调暗会糊成一团。"""
    return "dark" if dark else "osm"


# --------------------------------------------------------------------------- #
# 投影
# --------------------------------------------------------------------------- #
def lonlat_to_world(lat: float, lon: float, zoom: int) -> Tuple[float, float]:
    """经纬度 → 该 zoom 下的全球像素坐标（原点在左上，x 向东、y 向南）。"""
    lat = max(-MAX_LAT, min(MAX_LAT, float(lat)))
    lon = float(lon)
    world = float(TILE_SIZE) * (2.0 ** int(zoom))
    x = (lon + 180.0) / 360.0 * world
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1.0 + s) / (1.0 - s)) / (4.0 * math.pi)) * world
    return x, y


def world_to_lonlat(x: float, y: float, zoom: int) -> Tuple[float, float]:
    """``lonlat_to_world`` 的严格反解。"""
    world = float(TILE_SIZE) * (2.0 ** int(zoom))
    lon = float(x) / world * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * float(y) / world
    lat = math.degrees(math.atan(math.sinh(n)))
    return max(-MAX_LAT, min(MAX_LAT, lat)), max(-180.0, min(180.0, lon))


def tile_range(x0: float, y0: float, x1: float, y1: float,
               zoom: int) -> Tuple[int, int, int, int]:
    """像素矩形 → 需要请求的瓦片索引闭区间 (tx0, ty0, tx1, ty1)。

    y 索引按 Web Mercator 上限裁剪（极区没有瓦片）；x 索引按环形取模，
    调用方负责 ``% n``（这里返回未取模的原始范围，便于跨 180° 连续绘制）。
    """
    n = 2 ** int(zoom)
    tx0 = int(math.floor(x0 / TILE_SIZE))
    tx1 = int(math.floor((x1 - 1e-6) / TILE_SIZE))
    ty0 = max(0, int(math.floor(y0 / TILE_SIZE)))
    ty1 = min(n - 1, int(math.floor((y1 - 1e-6) / TILE_SIZE)))
    return tx0, ty0, tx1, ty1


def tiles_for_bounds(lat_min: float, lon_min: float, lat_max: float,
                     lon_max: float, zoom: int) -> List[Tuple[int, int]]:
    """经纬度矩形 → 该层级下覆盖它的 (x, y) 瓦片列表（离线下载用）。"""
    x0, y0 = lonlat_to_world(lat_max, lon_min, zoom)   # 左上
    x1, y1 = lonlat_to_world(lat_min, lon_max, zoom)   # 右下
    tx0, ty0, tx1, ty1 = tile_range(x0, y0, x1, y1, zoom)
    n = 2 ** zoom
    out = []
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            out.append((tx % n, ty))
    return out


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
def default_cache_dir() -> str:
    """瓦片缓存目录：``MBDSDR_CACHE_DIR`` 优先（测试隔离用），否则 ~/.mbdsdr。"""
    base = os.environ.get("MBDSDR_CACHE_DIR")
    if base:
        return os.path.join(base, "osm_tiles")
    return os.path.join(os.path.expanduser("~"), ".mbdsdr", "osm_tiles")


class TileCache:
    """内存 LRU + 磁盘 PNG 缓存，按风格分目录。

    内存上限按**张数**管，默认 128 张（256px×4B ≈ 33 MB）。曾经的 512 张
    等于 134 MB，够把一台老机器拖死——这不是"缓存越大越流畅"。
    """

    def __init__(self, root: Optional[str] = None, style: str = DEFAULT_STYLE,
                 max_mem: int = 128, max_files: int = 8192):
        self.root = root or default_cache_dir()
        self.style = str(style or DEFAULT_STYLE)
        self.max_mem = int(max_mem)
        self.max_files = int(max_files)
        self._mem: Dict[Tuple[int, int, int], QPixmap] = {}
        self._order: list = []
        try:
            if self.style_dir:
                os.makedirs(self.style_dir, exist_ok=True)
        except OSError:
            self.root = ""

    @property
    def style_dir(self) -> str:
        return os.path.join(self.root, self.style) if self.root else ""

    # -- 内存 -------------------------------------------------------------
    def get_mem(self, z: int, x: int, y: int) -> Optional[QPixmap]:
        key = (int(z), int(x), int(y))
        pm = self._mem.get(key)
        if pm is not None:
            try:
                self._order.remove(key)
            except ValueError:
                pass
            self._order.append(key)
        return pm

    def put(self, z: int, x: int, y: int, pixmap: QPixmap) -> None:
        key = (int(z), int(x), int(y))
        self._mem[key] = pixmap
        self._order.append(key)
        while self._order and len(self._order) > self.max_mem:
            old = self._order.pop(0)
            self._mem.pop(old, None)

    # -- 磁盘 -------------------------------------------------------------
    def path_for(self, z: int, x: int, y: int) -> str:
        base = self.style_dir
        if not base:
            return ""
        return os.path.join(base, str(int(z)), str(int(x)), f"{int(y)}.png")

    def has_disk(self, z: int, x: int, y: int) -> bool:
        p = self.path_for(z, x, y)
        return bool(p) and os.path.exists(p)

    def load_disk(self, z: int, x: int, y: int) -> Optional[QPixmap]:
        path = self.path_for(z, x, y)
        if not path or not os.path.exists(path):
            return None
        pm = QPixmap(path)
        if pm.isNull():
            return None
        self.put(z, x, y, pm)
        return pm

    def store_disk(self, z: int, x: int, y: int, data: bytes) -> None:
        path = self.path_for(z, x, y)
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError:
            return
        self._trim()

    def store_image(self, z: int, x: int, y: int, img) -> None:
        """存**处理过**的图（暗色渲染结果），下次直接读，不重复算。"""
        path = self.path_for(z, x, y)
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part.png"
            ok = img.save(tmp, "PNG")
            if not ok:
                return
            os.replace(tmp, path)
        except OSError:
            return
        self._trim()

    def count_files(self) -> int:
        total = 0
        if not self.style_dir:
            return 0
        for _dirpath, _dirs, files in os.walk(self.style_dir):
            total += len(files)
            if total > self.max_files:
                break
        return total

    def disk_bytes(self) -> int:
        """已占磁盘字节数（设置页要给人看真实值，不能报"大概"）。"""
        total = 0
        if not self.style_dir:
            return 0
        for dirpath, _dirs, files in os.walk(self.style_dir):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
        return total

    def _trim(self) -> None:
        """超过上限时删最旧的一批（只碰本风格目录，且只在超标时触发）。"""
        if self.count_files() <= self.max_files:
            return
        entries = []
        try:
            for dirpath, _dirs, files in os.walk(self.style_dir):
                for name in files:
                    if not name.endswith(".png"):
                        continue
                    fp = os.path.join(dirpath, name)
                    try:
                        entries.append((os.path.getmtime(fp), fp))
                    except OSError:
                        continue
        except OSError:
            return
        entries.sort()
        for _mtime, fp in entries[:max(1, len(entries) - self.max_files + 512)]:
            try:
                os.remove(fp)
            except OSError:
                continue

    def clear_memory(self) -> None:
        self._mem.clear()
        self._order.clear()


# --------------------------------------------------------------------------- #
# 网络
# --------------------------------------------------------------------------- #
def _use_system_proxy() -> None:
    """让 Qt 走系统代理（Windows 上是注册表里的 ProxyServer）。

    Qt 默认 ``usesSystemConfiguration() == False``，不显式打开的话
    在需要代理的机器上所有瓦片都会超时。
    """
    if os.environ.get("MBDSDR_OSM_NO_PROXY"):
        return
    try:
        QNetworkProxyFactory.setUseSystemConfiguration(True)
    except Exception:  # noqa: BLE001
        pass


def _make_request(url: str) -> QNetworkRequest:
    req = QNetworkRequest(QUrl(url))
    try:
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, USER_AGENT)
        req.setAttribute(
            QNetworkRequest.Attribute.CacheLoadControlAttribute,
            QNetworkRequest.CacheLoadControl.PreferCache)
        req.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
    except Exception:  # noqa: BLE001
        pass
    return req


class _DecodeSignals(QObject):
    """QRunnable 不能带信号，挂一个宿主对象把结果排回 GUI 线程。"""

    done = Signal(int, int, int, QImage)
    failed = Signal(int, int, int, str)


def darken_image(img: QImage, saturation: float = 1.0,
                 gain: float = 0.95) -> QImage:
    """OSM 浅色瓦片 → 暗色渲染（反相 + 色相翻转 180° + 压亮度）。

    为什么不是单纯反相：单纯反相会把绿地变紫、水系变橙，一张图上到处
    怪色。加一步 hue-rotate(180°) 之后，绿地还是绿、水系还是蓝，只有
    明暗对调——这就是浏览器 CSS ``filter: invert(1) hue-rotate(180deg)``
    的同一套做法（实测三档并排比对，sat=1.0 最像正常暗色地图）。

    只做颜色变换，不动几何：道路/水域/地名的位置与归属一个像素都不改，
    所以署名仍是 OpenStreetMap。numpy 在就快（65k 像素约 3 ms），
    不在就退回纯 Python（约 40 ms/张，只影响首次渲染）。
    """
    if img.isNull():
        return img
    img = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    # hue-rotate(180deg) 标准矩阵（CSS filter 规范同一组系数）
    m = ((-0.574, 1.430, 0.144),
         (0.426, 0.430, 0.144),
         (0.426, 1.430, -0.856))
    try:
        import numpy as np
        arr = np.frombuffer(bytes(img.constBits()), dtype=np.uint8)
        arr = arr.reshape(h, w, 4).copy()
        rgb = arr[:, :, :3].astype(np.float32)
        inv = 255.0 - rgb
        hued = np.empty_like(inv)
        for i in range(3):
            hued[:, :, i] = np.clip(
                inv[:, :, 0] * m[i][0] + inv[:, :, 1] * m[i][1]
                + inv[:, :, 2] * m[i][2], 0.0, 255.0)
        lum = (0.299 * hued[:, :, 0] + 0.587 * hued[:, :, 1]
               + 0.114 * hued[:, :, 2])[:, :, None]
        out = (lum + (hued - lum) * float(saturation)) * float(gain)
        arr[:, :, :3] = np.clip(out, 0, 255).astype(np.uint8)
        return QImage(arr.tobytes(), w, h, QImage.Format_RGBA8888).copy()
    except ImportError:
        out = QImage(w, h, QImage.Format_RGBA8888)
        for yy in range(h):
            for xx in range(w):
                c = img.pixelColor(xx, yy)
                ir, ig, ib = 255.0 - c.red(), 255.0 - c.green(), 255.0 - c.blue()
                r = ir * m[0][0] + ig * m[0][1] + ib * m[0][2]
                g = ir * m[1][0] + ig * m[1][1] + ib * m[1][2]
                b = ir * m[2][0] + ig * m[2][1] + ib * m[2][2]
                r, g, b = (max(0.0, min(255.0, v)) for v in (r, g, b))
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                r = (lum + (r - lum) * saturation) * gain
                g = (lum + (g - lum) * saturation) * gain
                b = (lum + (b - lum) * saturation) * gain
                out.setPixelColor(xx, yy, QColor(
                    max(0, min(255, int(r))), max(0, min(255, int(g))),
                    max(0, min(255, int(b))), c.alpha()))
        return out


class _DecodeTask(QRunnable):
    """把 PNG 解码（+可选暗色渲染）放到线程池。

    单张解码 1~3 ms、暗色渲染 3 ms，一次 12 张就是几十毫秒——主线程刚拖完
    图就被卡一下，手感立刻变"粘"。所以连滤镜也一起进线程。
    """

    def __init__(self, z: int, x: int, y: int, data: bytes,
                 signals: _DecodeSignals, filter_name: str = "none"):
        super().__init__()
        self.z, self.x, self.y = int(z), int(x), int(y)
        self.data = data
        self.signals = signals
        self.filter_name = str(filter_name or "none")

    def run(self) -> None:  # noqa: D401 (QRunnable)
        img = QImage.fromData(self.data)
        if img.isNull():
            self.signals.failed.emit(self.z, self.x, self.y, "不是有效图片")
            return
        if self.filter_name == "invert_dark":
            try:
                img = darken_image(img)
            except Exception as exc:  # noqa: BLE001
                self.signals.failed.emit(
                    self.z, self.x, self.y, f"暗色渲染失败：{exc}")
                return
        self.signals.done.emit(self.z, self.x, self.y, img)


class TileLoader(QObject):
    """异步抓瓦片；回调只发 ``ready(z, x, y)``，像素由缓存取。"""

    ready = Signal(int, int, int)
    failed = Signal(int, int, int, str)

    def __init__(self, cache: TileCache, style: Optional[str] = None,
                 online: bool = True, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.cache = cache
        self.style = get_style(style or cache.style)
        self.online = bool(online)
        self._inflight: set = set()
        self._pending: Dict[Tuple[int, int, int], bytes] = {}
        self._failed: Dict[Tuple[int, int, int], float] = {}
        #: 每张瓦片已经试到第几个源（0=主源，1..=镜像）
        self._try: Dict[Tuple[int, int, int], int] = {}
        #: 主源熔断：连续失败到阈值就暂时直接从镜像开跑，别让每张瓦片都
        #: 先等一次超时（实测这样首屏从 11 s 掉到 1~2 s）
        self._primary_fails = 0
        self._primary_dead_until = 0.0
        self._nam: Optional[QNetworkAccessManager] = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._sig = _DecodeSignals(self)
        self._sig.done.connect(self._on_decoded)
        self._sig.failed.connect(self._on_decode_failed)
        self.ok_count = 0
        self.fail_count = 0
        if self.online:
            _use_system_proxy()
            self._nam = QNetworkAccessManager(self)

    # ------------------------------------------------------------------
    def has(self, z: int, x: int, y: int) -> bool:
        return self.cache.get_mem(z, x, y) is not None

    def inflight(self) -> int:
        """在途请求数（HUD 用它说"正在加载"，不猜）。"""
        return len(self._inflight)

    def peek(self, z: int, x: int, y: int) -> Optional[QPixmap]:
        """只查缓存（内存→磁盘），**不发请求**。绘制首遍用它。"""
        pm = self.cache.get_mem(z, x, y)
        if pm is not None:
            return pm
        return self.cache.load_disk(z, x, y)

    def request(self, z: int, x: int, y: int) -> Optional[QPixmap]:
        """返回已就绪的瓦片；否则发起请求并返回 None。"""
        z, x, y = int(z), int(x), int(y)
        pm = self.cache.get_mem(z, x, y)
        if pm is not None:
            return pm
        pm = self.cache.load_disk(z, x, y)
        if pm is not None:
            return pm
        if not self.online or self._nam is None:
            return None
        key = (z, x, y)
        if key in self._inflight or len(self._inflight) >= MAX_INFLIGHT:
            return None
        last_fail = self._failed.get(key)
        if last_fail is not None and (time.monotonic() - last_fail) < RETRY_AFTER_S:
            return None
        n = 2 ** z
        if y < 0 or y >= n or z > self.style.max_zoom:
            return None
        url = self._url_for(key, x % n, y)
        self._inflight.add(key)
        reply = self._nam.get(_make_request(url))
        reply.finished.connect(lambda r=reply, k=key: self._on_finished(r, k))
        return None

    def _url_for(self, key: Tuple[int, int, int], x: int, y: int) -> str:
        """按已试次数选源：主源 → 镜像；主源熔断时直接走镜像。"""
        urls = (self.style.url,) + tuple(self.style.mirrors)
        start = 1 if (len(urls) > 1
                      and time.monotonic() < self._primary_dead_until) else 0
        idx = max(start, min(int(self._try.get(key, 0)), len(urls) - 1))
        return urls[idx].format(z=key[0], x=x, y=y)

    def _has_mirror(self, key: Tuple[int, int, int]) -> bool:
        return int(self._try.get(key, 0)) + 1 < 1 + len(self.style.mirrors)

    def _on_finished(self, reply, key) -> None:
        z, x, y = key
        self._inflight.discard(key)
        try:
            err = reply.error()
            data = bytes(reply.readAll())
        except RuntimeError:
            return
        finally:
            try:
                reply.deleteLater()
            except RuntimeError:
                pass
        # 注意：PySide6 的枚举是 enum.Enum，不是 IntEnum，
        # ``err != 0`` 恒为 True（2026-11-10 实测把 200 响应当成失败的就是它）。
        if err != QNetworkReply.NetworkError.NoError or not data:
            # 主源被限/抽风：先换镜像再谈失败（不计入 fail_count，避免
            # HUD 把"正在换源"报成"瓦片未加载"）
            if int(self._try.get(key, 0)) == 0:
                self._primary_fails += 1
                if self._primary_fails >= 3:
                    self._primary_dead_until = time.monotonic() + 300.0
            if self._has_mirror(key):
                self._try[key] = int(self._try.get(key, 0)) + 1
                n = 2 ** z
                self._inflight.add(key)
                url = self._url_for(key, x % n, y)
                r2 = self._nam.get(_make_request(url))
                r2.finished.connect(
                    lambda r=r2, k=key: self._on_finished(r, k))
                return
            self.fail_count += 1
            self._failed[key] = time.monotonic()
            msg = ""
            try:
                msg = reply.errorString()
            except RuntimeError:
                pass
            try:
                code = int(reply.attribute(
                    QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0)
                if code:
                    msg = f"{msg}·HTTP {code}"
            except (TypeError, ValueError, RuntimeError):
                pass
            self.failed.emit(z, x, y, msg)
            return
        # 无滤镜：原始 PNG 直接落盘（最省，也最快）；有滤镜：等线程处理完
        # 再存处理后的图，避免把浅色原图当成"暗色瓦片"缓存起来。
        if self.style.filter == "none":
            self.cache.store_disk(z, x, y, data)
        self._pending[key] = data
        self._pool.start(_DecodeTask(z, x, y, data, self._sig, self.style.filter))

    def _on_decoded(self, z: int, x: int, y: int, img: QImage) -> None:
        self._pending.pop((z, x, y), None)
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            self.fail_count += 1
            self.failed.emit(z, x, y, "解码失败")
            return
        self.cache.put(z, x, y, pm)
        if int(self._try.get((z, x, y), 0)) == 0:
            self._primary_fails = 0          # 主源能出图就复位熔断计数
        if self.style.filter != "none":
            try:
                self.cache.store_image(z, x, y, img)
            except Exception:  # noqa: BLE001
                pass
        self.ok_count += 1
        self.ready.emit(z, x, y)

    def _on_decode_failed(self, z: int, x: int, y: int, msg: str) -> None:
        self._pending.pop((z, x, y), None)
        self.fail_count += 1
        self._failed[(z, x, y)] = time.monotonic()
        self.failed.emit(z, x, y, msg)


# --------------------------------------------------------------------------- #
# 离线下载
# --------------------------------------------------------------------------- #
class RegionDownloader(QObject):
    """把一片区域的瓦片批量下到磁盘，之后断网也能看。

    用法::

        dl = RegionDownloader(cache, style="dark")
        dl.progress.connect(lambda done, total, skipped: ...)
        dl.finished.connect(lambda ok, bad: ...)
        dl.start(lat, lon, zoom_min=10, zoom_max=13, radius_km=20)

    边界：并发 4、批间 120 ms（不给瓦片服务器添麻烦）、可随时 cancel()、
    已缓存的瓦片跳过（``skipped`` 如实上报，不假装"全下了一遍"）。
    """

    progress = Signal(int, int, int)     # done, total, skipped
    finished = Signal(int, int)          # ok, failed
    message = Signal(str)

    MAX_CONCURRENT = 4
    BATCH_PAUSE_MS = 120

    def __init__(self, cache: TileCache, style: Optional[str] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.cache = cache
        self.style = get_style(style or cache.style)
        self._nam: Optional[QNetworkAccessManager] = None
        self._queue: List[Tuple[int, int, int]] = []
        self._total = 0
        self._done = 0
        self._skipped = 0
        self._ok = 0
        self._bad = 0
        self._active = 0
        self._cancelled = False
        self._running = False

    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._running

    @staticmethod
    def plan(lat: float, lon: float, zoom_min: int, zoom_max: int,
             radius_km: float) -> List[Tuple[int, int, int]]:
        """算出要下哪些瓦片（纯函数，测试与 UI 预览都用它）。

        半径按纬度换算成度：1° 纬度 ≈ 111 km；经度再除以 cos(lat)。
        """
        lat = max(-MAX_LAT, min(MAX_LAT, float(lat)))
        dlat = float(radius_km) / 111.0
        coslat = max(0.05, math.cos(math.radians(lat)))
        dlon = dlat / coslat
        jobs: List[Tuple[int, int, int]] = []
        for z in range(max(MIN_ZOOM, int(zoom_min)),
                       min(int(zoom_max), MAX_ZOOM) + 1):
            for (x, y) in tiles_for_bounds(lat - dlat, lon - dlon,
                                           lat + dlat, lon + dlon, z):
                jobs.append((z, x, y))
        return jobs

    def start(self, lat: float, lon: float, zoom_min: int, zoom_max: int,
              radius_km: float) -> int:
        jobs = self.plan(lat, lon, zoom_min, zoom_max, radius_km)
        self.start_jobs(jobs)
        return len(jobs)

    def start_jobs(self, jobs: List[Tuple[int, int, int]]) -> None:
        if self._running:
            return
        self._queue = []
        self._skipped = 0
        for z, x, y in jobs:
            if self.cache.has_disk(z, x, y):
                self._skipped += 1
                continue
            self._queue.append((z, x, y))
        self._total = len(self._queue)
        self._done = 0
        self._ok = 0
        self._bad = 0
        self._active = 0
        self._cancelled = False
        self.progress.emit(0, self._total, self._skipped)
        if not self._queue:
            self._running = False
            self.message.emit(
                f"这片区域已经下过了（{self._skipped} 张都在缓存里）")
            self.finished.emit(0, 0)
            return
        _use_system_proxy()
        self._nam = QNetworkAccessManager(self)
        self._running = True
        self.message.emit(
            f"开始下载 {self._total} 张（跳过已缓存 {self._skipped} 张）…")
        self._pump()

    def cancel(self) -> None:
        if not self._running:
            return
        self._cancelled = True
        self._queue.clear()
        self.message.emit("已取消下载（已下好的瓦片保留）")

    # ------------------------------------------------------------------
    def _pump(self) -> None:
        if self._cancelled:
            self._finish()
            return
        while self._active < self.MAX_CONCURRENT and self._queue:
            z, x, y = self._queue.pop(0)
            n = 2 ** z
            url = self.style.url.format(z=z, x=x % n, y=y)
            self._active += 1
            reply = self._nam.get(_make_request(url))
            reply.finished.connect(
                lambda r=reply, k=(z, x, y): self._on_reply(r, k))
        if self._active == 0 and not self._queue:
            self._finish()

    def _on_reply(self, reply, key) -> None:
        z, x, y = key
        self._active = max(0, self._active - 1)
        ok = False
        try:
            err = reply.error()
            data = bytes(reply.readAll())
            if err == QNetworkReply.NetworkError.NoError and data:
                self.cache.store_disk(z, x, y, data)
                ok = True
        except RuntimeError:
            pass
        finally:
            try:
                reply.deleteLater()
            except RuntimeError:
                pass
        if ok:
            self._ok += 1
        else:
            self._bad += 1
        self._done += 1
        self.progress.emit(self._done, self._total, self._skipped)
        if self._cancelled:
            self._finish()
            return
        if self._queue:
            QTimer.singleShot(self.BATCH_PAUSE_MS, self._pump)
        else:
            self._finish()

    def _finish(self) -> None:
        if not self._running:
            return
        self._running = False
        mb = self.cache.disk_bytes() / (1024.0 * 1024.0)
        self.message.emit(
            f"下载结束：成功 {self._ok} 张、失败 {self._bad} 张、"
            f"跳过 {self._skipped} 张；缓存共 {mb:.1f} MB")
        self.finished.emit(self._ok, self._bad)


# --------------------------------------------------------------------------- #
# 视图
# --------------------------------------------------------------------------- #
try:  # 触屏手势可选：缺了不影响鼠标使用
    from touch_helpers import PinchZoomMixin as _PinchMixin
except Exception:  # noqa: BLE001
    class _PinchMixin:  # type: ignore
        def init_pinch_zoom(self) -> None:
            return


# 配色（深色底，与桌面其他浮层同族；瓦片本身自带颜色）
_GRID = QColor(255, 255, 255, 34)   # 占位虚线得看得见，否则空白像"坏了"
_LOADING = QColor(255, 255, 255, 16)
_DIM = "#8f95a3"
_ACCENT = "#ff5b4a"
_BG_DARK = "#10151d"
_BG_LIGHT = "#e9eef4"


def _bg_for_style(key: str) -> str:
    return _BG_DARK if get_style(key).dark else _BG_LIGHT


class OsmMapView(QWidget, _PinchMixin):
    """OSM 瓦片地图视图：拖拽平移、滚轮缩放、单击点选、多图层叠加。

    信号
    ----
    picked(lat, lon)  单击选中（拖拽不算）
    zoom_changed(z)   缩放级别变化
    map_error(str)    连续失败、已无可显示瓦片时给一句人话

    图层
    ----
    ``add_layer(name, cb)``：``cb(painter, project)``，``project(lat, lon)``
    返回控件坐标 ``QPointF``。ADS-B 画飞机、APRS 画站点、指向画波束、
    模型要给用户看的标注，都走这一条路，不各造一个地图。
    """

    picked = Signal(float, float)
    zoom_changed = Signal(int)
    map_error = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None, *,
                 online: bool = True, cache: Optional[TileCache] = None,
                 tile_source: Optional[Callable] = None,
                 style: Optional[str] = None,
                 show_attribution: bool = True):
        super().__init__(parent)
        self.setMinimumSize(240, 160)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._online = bool(online)
        self._tile_source = tile_source
        self._show_attr = bool(show_attribution)
        self._style_key = get_style(
            style or (cache.style if cache else DEFAULT_STYLE)).key
        self._cache = cache or TileCache(style=self._style_key)
        self._loader = TileLoader(self._cache, style=self._style_key,
                                  online=self._online, parent=self)
        self._loader.ready.connect(self._on_tile_ready)
        self._loader.failed.connect(self._on_tile_failed)

        self._zoom = 3
        self._center_lat = 30.0
        self._center_lon = 110.0
        self._marker: Optional[Tuple[float, float]] = None
        self._layers: List[Tuple[str, Callable]] = []
        self._drag_from: Optional[QPointF] = None
        self._drag_start: Optional[QPointF] = None
        self._dragging = False
        self._error_text = ""
        self._plan: Optional[dict] = None
        # 触摸手势走 mixin 的 event()；这里只连信号
        try:
            self.init_pinch_zoom()
            self.pinch_scale_changed.connect(self._on_pinch)
            self.pan_changed.connect(self._on_touch_pan)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 状态
    def zoom(self) -> int:
        return self._zoom

    def center(self) -> Tuple[float, float]:
        return self._center_lat, self._center_lon

    def style_key(self) -> str:
        return self._style_key

    def style_attribution(self) -> str:
        return get_style(self._style_key).attribution

    def set_style(self, key: str) -> None:
        """换瓦片风格（浅色 / 暗色 / 浅灰）。缓存按风格分目录，不串图。"""
        style = get_style(key)
        if style.key == self._style_key:
            return
        root = self._cache.root or None
        self._style_key = style.key
        self._cache = TileCache(root=root, style=style.key)
        self._loader.deleteLater()
        self._loader = TileLoader(self._cache, style=style.key,
                                  online=self._online, parent=self)
        self._loader.ready.connect(self._on_tile_ready)
        self._loader.failed.connect(self._on_tile_failed)
        self._plan = None
        self.update()

    def set_view(self, lat: Optional[float], lon: Optional[float],
                 zoom: Optional[int] = None) -> None:
        """居中到给定经纬度；``None`` 保持当前中心（只改 zoom）。"""
        if lat is not None and lon is not None:
            try:
                self._center_lat = max(-MAX_LAT, min(MAX_LAT, float(lat)))
                self._center_lon = max(-180.0, min(180.0, float(lon)))
            except (TypeError, ValueError):
                pass
        if zoom is not None:
            new_z = max(MIN_ZOOM, min(style_max_zoom(self._style_key),
                                      int(zoom)))
            if new_z != self._zoom:
                self._zoom = new_z
                try:
                    self.zoom_changed.emit(self._zoom)
                except RuntimeError:
                    pass
        self._plan = None
        self.update()

    def set_marker(self, lat: Optional[float], lon: Optional[float]) -> None:
        self._marker = (None if lat is None or lon is None
                        else (float(lat), float(lon)))
        self.update()

    def marker(self) -> Optional[Tuple[float, float]]:
        return self._marker

    # ------------------------------------------------------------ 图层
    def add_layer(self, name: str, painter_cb: Optional[Callable]) -> None:
        """叠加一层绘制：``cb(painter, project)``。同名先移除再追加。"""
        self.remove_layer(name)
        if painter_cb is not None:
            self._layers.append((str(name), painter_cb))
            self.update()

    def remove_layer(self, name: str) -> None:
        self._layers = [(n, cb) for (n, cb) in self._layers if n != str(name)]

    def layer_names(self) -> List[str]:
        return [n for (n, _cb) in self._layers]

    def set_overlay(self, cb: Optional[Callable]) -> None:
        """兼容单图层老接口：等价于 add_layer("overlay", cb)。"""
        self.add_layer("overlay", cb)

    def tile_stats(self) -> Tuple[int, int]:
        """(成功数, 失败数)——给诊断与测试看，不给人看。"""
        return self._loader.ok_count, self._loader.fail_count

    def cache(self) -> TileCache:
        return self._cache

    # --------------------------------------------------------- 坐标换算
    def _world_of_center(self) -> Tuple[float, float]:
        return lonlat_to_world(self._center_lat, self._center_lon, self._zoom)

    def project(self, lat: float, lon: float) -> QPointF:
        cx, cy = self._world_of_center()
        x, y = lonlat_to_world(lat, lon, self._zoom)
        return QPointF(x - cx + self.width() / 2.0,
                       y - cy + self.height() / 2.0)

    def unproject(self, pos: QPointF) -> Tuple[float, float]:
        cx, cy = self._world_of_center()
        return world_to_lonlat(pos.x() - self.width() / 2.0 + cx,
                               pos.y() - self.height() / 2.0 + cy, self._zoom)

    def visible_bounds(self) -> Tuple[float, float, float, float]:
        """当前视口经纬度范围 (lat_min, lon_min, lat_max, lon_max)。"""
        nw = self.unproject(QPointF(0, 0))
        se = self.unproject(QPointF(self.width(), self.height()))
        return (se[0], nw[1], nw[0], se[1])

    # ------------------------------------------------------------ 绘制
    def _attr_rect(self) -> QRect:
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.style_attribution()) + 12
        return QRect(self.width() - w - 6, self.height() - 20, w, 16)

    def attribution_rect(self) -> QRectF:
        return QRectF(self._attr_rect())

    def _tile_plan(self) -> dict:
        """视口瓦片清单只在中心/层级/尺寸变化时重算——每帧重算是白烧 CPU。"""
        sig = (self._zoom, self.width(), self.height(),
               round(self._center_lat, 7), round(self._center_lon, 7))
        if self._plan is not None and self._plan["sig"] == sig:
            return self._plan
        cx, cy = self._world_of_center()
        x0 = cx - self.width() / 2.0
        y0 = cy - self.height() / 2.0
        pad = PREFETCH_RINGS * TILE_SIZE
        tx0, ty0, tx1, ty1 = tile_range(
            x0 - pad, y0 - pad, x0 + self.width() + pad,
            y0 + self.height() + pad, self._zoom)
        n = 2 ** self._zoom
        hw, hh = self.width() / 2.0, self.height() / 2.0
        items = []
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                px = tx * TILE_SIZE - x0
                py = ty * TILE_SIZE - y0
                d = ((px + TILE_SIZE / 2.0 - hw) ** 2
                     + (py + TILE_SIZE / 2.0 - hh) ** 2)
                items.append((d, px, py, tx % n, ty))
        items.sort(key=lambda it: it[0])
        self._plan = {"sig": sig, "items": items, "n": n}
        return self._plan

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.fillRect(self.rect(), QColor(_bg_for_style(self._style_key)))
            if self.width() <= 0 or self.height() <= 0:
                return
            plan = self._tile_plan()
            missing = []
            w, h = self.width(), self.height()
            for d, px, py, tx, ty in plan["items"]:
                if px > w or py > h:
                    continue
                if px + TILE_SIZE < 0 or py + TILE_SIZE < 0:
                    continue
                pm = self._cached_tile(tx, ty)
                if pm is not None:
                    p.drawPixmap(int(px), int(py), TILE_SIZE, TILE_SIZE, pm)
                    continue
                p.fillRect(QRectF(px, py, TILE_SIZE, TILE_SIZE), _LOADING)
                p.setPen(QPen(_GRID, 1, Qt.DotLine))
                p.drawRect(QRectF(px + 0.5, py + 0.5,
                                  TILE_SIZE - 1, TILE_SIZE - 1))
                on_screen = (-TILE_SIZE < px < w and -TILE_SIZE < py < h)
                if on_screen:
                    missing.append((d, tx, ty))
            if missing and self._tile_source is None:
                for _d, tx, ty in missing:
                    self._loader.request(self._zoom, tx, ty)
            self._draw_grid(p)
            for _name, cb in self._layers:
                try:
                    cb(p, self.project)
                except Exception:  # noqa: BLE001
                    continue
            self._draw_marker(p)
            self._draw_hud(p)
        finally:
            p.end()

    def _cached_tile(self, x: int, y: int) -> Optional[QPixmap]:
        """只取缓存（注入源直接调用），绝不在这里发请求。"""
        if self._tile_source is not None:
            try:
                got = self._tile_source(self._zoom, x, y)
            except Exception:  # noqa: BLE001
                return None
            return got if isinstance(got, QPixmap) else None
        return self._loader.peek(self._zoom, x, y)

    def _draw_grid(self, p: QPainter) -> None:
        """没瓦片时的经纬网（真正的坐标参考，不是假海岸线）。"""
        p.setPen(QPen(_GRID, 1))
        step = 30 if self._zoom <= 4 else 10 if self._zoom <= 6 else 1
        lat = -90
        while lat <= 90:
            y = self.project(lat, self._center_lon).y()
            if 0 <= y <= self.height():
                p.drawLine(QPointF(0, y), QPointF(self.width(), y))
            lat += step
        lon = -180
        while lon <= 180:
            x = self.project(self._center_lat, lon).x()
            if 0 <= x <= self.width():
                p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            lon += step

    def _draw_marker(self, p: QPainter) -> None:
        if self._marker is None:
            return
        pos = self.project(*self._marker)
        if not self.rect().adjusted(-40, -40, 40, 40).contains(pos.toPoint()):
            return
        # 空心细环 + 短十字：以前是 7px 实心半透明红盘配 30px 长十字，
        # 在任何深色底图上都像"一个大红点"，还会盖住叠在地图上的文字
        # （2026-11-10 截图实证：ADS-B 空态牌被它戳穿）。站址是参考点，
        # 不该比数据更抢眼。
        p.setPen(QPen(QColor(_ACCENT), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pos, 5.0, 5.0)
        p.setPen(QPen(QColor(_ACCENT), 1.0, Qt.DashLine))
        p.drawLine(QPointF(pos.x() - 10, pos.y()), QPointF(pos.x() + 10, pos.y()))
        p.drawLine(QPointF(pos.x(), pos.y() - 10), QPointF(pos.x(), pos.y() + 10))

    def _draw_hud(self, p: QPainter) -> None:
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1))
        p.setFont(f)
        style = get_style(self._style_key)
        badge = f"{style.label.split('（')[0]} z{self._zoom}"
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        w = p.fontMetrics().horizontalAdvance(badge) + 12
        p.fillRect(QRectF(6, 6, w, 17), QColor(10, 14, 20, 170))
        p.setPen(QPen(QColor(_DIM), 1))
        p.drawText(QRectF(6, 6, w, 17), Qt.AlignCenter, badge)
        ok, bad = self._loader.ok_count, self._loader.fail_count
        # 还在等第一批瓦片：明说"在加载"，别让空白被读成坏了
        if (self._tile_source is None and ok == 0 and bad == 0
                and self._loader.inflight() > 0):
            msg = "正在加载地图…（首次经代理会慢几秒，之后走缓存）"
            fw = min(self.width() - 20,
                     p.fontMetrics().horizontalAdvance(msg) + 16)
            p.fillRect(QRectF(10, 28, fw, 19), QColor(20, 34, 52, 210))
            p.setPen(QPen(QColor("#9fc4ee"), 1))
            p.drawText(QRectF(10, 28, fw, 19), Qt.AlignCenter, msg)
        # 瓦片全挂时的一句人话（不白屏装正常）
        if self._tile_source is None and ok == 0 and bad >= 3:
            msg = "瓦片未加载：检查联网或系统代理（离线仍可点选坐标）"
            fw = min(self.width() - 20,
                     p.fontMetrics().horizontalAdvance(msg) + 16)
            p.fillRect(QRectF(10, 28, fw, 19), QColor(90, 26, 26, 210))
            p.setPen(QPen(QColor("#ffb0a6"), 1))
            p.drawText(QRectF(10, 28, fw, 19), Qt.AlignCenter, msg)
        # 右下：署名（ODbL / 各家条款）
        if self._show_attr:
            r = self._attr_rect()
            p.fillRect(QRectF(r), QColor(10, 14, 20, 170))
            p.setPen(QPen(QColor("#b9c0cc"), 1))
            p.drawText(QRectF(r), Qt.AlignCenter, style.attribution)

    # ------------------------------------------------------------ 交互
    def _zoom_at(self, new_zoom: int, anchor: QPointF) -> None:
        new_zoom = max(MIN_ZOOM, min(style_max_zoom(self._style_key),
                                     int(new_zoom)))
        if new_zoom == self._zoom:
            return
        lat, lon = self.unproject(anchor)     # 锚点下的地理坐标保持不变
        self._zoom = new_zoom
        cx, cy = lonlat_to_world(lat, lon, self._zoom)
        world = float(TILE_SIZE) * (2 ** self._zoom)
        wx = cx - (anchor.x() - self.width() / 2.0)
        wy = cy - (anchor.y() - self.height() / 2.0)
        wx = (wx % world)
        self._center_lat, self._center_lon = world_to_lonlat(
            wx, max(0.0, min(world - 1e-6, wy)), self._zoom)
        self._plan = None
        try:
            self.zoom_changed.emit(self._zoom)
        except RuntimeError:
            pass
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt)
        delta = event.angleDelta().y()
        if delta == 0:
            return
        step = 1 if abs(delta) >= 120 else 0
        if step == 0:
            return
        self._zoom_at(self._zoom + (step if delta > 0 else -step),
                      event.position())
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.button() != Qt.LeftButton:
            return
        self._drag_from = QPointF(event.position())
        self._drag_start = QPointF(event.position())
        self._dragging = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt)
        if self._drag_from is None:
            return
        pos = QPointF(event.position())
        dist = (pos - self._drag_start).manhattanLength() if self._drag_start else 0
        if not self._dragging and dist < 4:
            return
        self._dragging = True
        self.setCursor(Qt.ClosedHandCursor)
        dx = pos.x() - self._drag_from.x()
        dy = pos.y() - self._drag_from.y()
        self._pan_pixels(dx, dy)
        self._drag_from = pos

    def _pan_pixels(self, dx: float, dy: float) -> None:
        if dx == 0 and dy == 0:
            return
        cx, cy = self._world_of_center()
        world = float(TILE_SIZE) * (2 ** self._zoom)
        nx = (cx - dx) % world
        ny = max(0.0, min(world - 1e-6, cy - dy))
        self._center_lat, self._center_lon = world_to_lonlat(nx, ny, self._zoom)
        self._plan = None
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.button() != Qt.LeftButton or self._drag_from is None:
            return
        was_drag = self._dragging
        self._drag_from = None
        self._drag_start = None
        self._dragging = False
        self.setCursor(Qt.CrossCursor)
        if was_drag:
            return
        if self._show_attr and self._attr_rect().contains(
                event.position().toPoint()):
            self.open_attribution()      # 署名校验：点它就该能开版权页
            return
        lat, lon = self.unproject(QPointF(event.position()))
        self.set_marker(lat, lon)
        self.picked.emit(lat, lon)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.button() == Qt.LeftButton:
            self._zoom_at(self._zoom + 1, QPointF(event.position()))
            event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt)
        k = event.key()
        step = 80.0
        if k == Qt.Key_Left:
            self._pan_pixels(-step, 0)
        elif k == Qt.Key_Right:
            self._pan_pixels(step, 0)
        elif k == Qt.Key_Up:
            self._pan_pixels(0, -step)
        elif k == Qt.Key_Down:
            self._pan_pixels(0, step)
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self._zoom_at(self._zoom + 1, QPointF(self.width() / 2,
                                                  self.height() / 2))
        elif k == Qt.Key_Minus:
            self._zoom_at(self._zoom - 1, QPointF(self.width() / 2,
                                                  self.height() / 2))
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _on_pinch(self, factor: float, center: QPointF) -> None:
        if factor > 1.15:
            self._zoom_at(self._zoom + 1, center)
        elif factor < 0.87:
            self._zoom_at(self._zoom - 1, center)

    def _on_touch_pan(self, dx: float, dy: float) -> None:
        self._pan_pixels(dx, dy)

    # --------------------------------------------------------- 瓦片回调
    def _on_tile_ready(self, _z: int, _x: int, _y: int) -> None:
        self._error_text = ""
        self.update()

    def _on_tile_failed(self, _z: int, _x: int, _y: int, msg: str) -> None:
        ok, bad = self._loader.ok_count, self._loader.fail_count
        if ok == 0 and bad == 3:
            self._error_text = msg or "网络不可达"
            try:
                self.map_error.emit(
                    f"OSM 瓦片加载失败（{self._error_text}）：检查联网或系统代理；"
                    "离线时仍可点选经纬度")
            except RuntimeError:
                pass
        self.update()

    def open_attribution(self) -> None:
        # 两种风格的数据都来自 OSM（暗色只是本地渲染），版权页同一个
        QDesktopServices.openUrl(
            QUrl("https://www.openstreetmap.org/copyright"))

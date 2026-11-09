# SPDX-License-Identifier: MIT
"""OSM 在线瓦片地图：可拖拽 / 缩放 / 点选经纬度。

## 为什么不用自绘世界多边形

以前地点面板自己画 ``WORLD_LAND_POLYGONS``（等距圆柱投影下的粗轮廓），
问题很直接：等距圆柱把高纬度横向拉长、形状失真，轮廓只有几十个点，
放大到城市级就是一片空白色块——**又拉伸又不如人家**。

现在直接消费 OpenStreetMap 的栅格瓦片（Web Mercator，官方标准样式），
用户看到的就是 osm.org 那张图。本模块只做三件事：

1. Web Mercator 正反解（``lonlat_to_world`` / ``world_to_lonlat``）；
2. 瓦片抓取 + 磁盘缓存（``TileLoader``、``TileCache``）；
3. 一个可复用的视图控件（``OsmMapView``）：拖拽、滚轮缩放、点选、
   可选覆盖层回调（ADS-B 飞机点就画在覆盖层上）。

## 四条诚实边界

1. **不内置任何地图数据**：没网/没代理就是没图。此时不白屏、不画假海岸线，
   改为深色底 + 经纬网 + 顶部一句「瓦片未加载（需联网或系统代理）」。
2. **署名常驻**：``© OpenStreetMap contributors`` 画在右下角，点击可开
   osm.org/copyright（ODbL 要求）。
3. **遵守瓦片使用政策**：带可识别 UA、只请求视野内瓦片、磁盘缓存复用、
   单页在途请求上限，不做批量预取。
4. **代理走系统设置**：Qt 默认不读 Windows 系统代理，这里显式
   ``QNetworkProxyFactory.setUseSystemConfiguration(True)``；需要绕开时设
   ``MBDSDR_OSM_NO_PROXY=1``（测试用）。

## 依赖

仅 QtCore/QtGui/QtWidgets/QtNetwork。测试可注入 ``tile_source`` 关闭网络。
"""
from __future__ import annotations

import math
import os
import time
from typing import Callable, Dict, Optional, Tuple

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QPainter, QPen, QPixmap,
)
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkProxyFactory, QNetworkReply, QNetworkRequest,
)
from PySide6.QtWidgets import QWidget

__all__ = [
    "TILE_SIZE", "TILE_URL_TEMPLATE", "ATTRIBUTION",
    "lonlat_to_world", "world_to_lonlat", "tile_range",
    "TileCache", "TileLoader", "OsmMapView",
]

TILE_SIZE = 256
TILE_URL_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATTRIBUTION = "© OpenStreetMap contributors"
USER_AGENT = ("MBDSDR/1.0 (amateur-radio SDR desktop; "
              "+https://github.com/HaohanHe/MBDSDR)")
MAX_LAT = 85.05112878        # Web Mercator 可表示的纬度极限
MIN_ZOOM = 1
MAX_ZOOM = 19
MAX_INFLIGHT = 12

# 配色（深色底，与桌面其他浮层同族；瓦片本身自带颜色）
_BG = "#0d121a"
_GRID = QColor(255, 255, 255, 18)
_LOADING = QColor(255, 255, 255, 10)
_DIM = "#8f95a3"
_ACCENT = "#ff5b4a"


# --------------------------------------------------------------------------- #
# 投影：Web Mercator（与 OSM 瓦片编号严格一致）
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
    """内存 LRU + 磁盘 PNG 缓存。

    只写自己目录下的 ``z/x/y.png``，不做任何删除以外目录的动作；
    磁盘上限按「文件数」粗管（超过 ``max_files`` 删最旧一批），
    避免长跑把用户磁盘塞满。
    """

    def __init__(self, root: Optional[str] = None, max_mem: int = 512,
                 max_files: int = 4096):
        self.root = root or default_cache_dir()
        self.max_mem = int(max_mem)
        self.max_files = int(max_files)
        self._mem: Dict[Tuple[int, int, int], QPixmap] = {}
        self._order: list = []
        try:
            os.makedirs(self.root, exist_ok=True)
        except OSError:
            self.root = ""

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
        if not self.root:
            return ""
        return os.path.join(self.root, str(int(z)), str(int(x)), f"{int(y)}.png")

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

    def count_files(self) -> int:
        total = 0
        if not self.root:
            return 0
        for _dirpath, _dirs, files in os.walk(self.root):
            total += len(files)
            if total > self.max_files:
                break
        return total

    def _trim(self) -> None:
        """超过上限时删最旧的一批（只碰本目录，且只在超标时触发）。"""
        if self.count_files() <= self.max_files:
            return
        entries = []
        try:
            for dirpath, _dirs, files in os.walk(self.root):
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
# 抓取
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


class TileLoader(QObject):
    """异步抓瓦片；回调只发 ``ready(z, x, y)``，像素由缓存取。"""

    ready = Signal(int, int, int)
    failed = Signal(int, int, int, str)

    def __init__(self, cache: TileCache, url_template: str = TILE_URL_TEMPLATE,
                 online: bool = True, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.cache = cache
        self.url_template = url_template
        self.online = bool(online)
        self._inflight: set = set()
        self._failed: Dict[Tuple[int, int, int], float] = {}
        self._nam: Optional[QNetworkAccessManager] = None
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
        # 失败退避：同一张瓦片 60 s 内不重复请求（瓦片服务器政策，也防刷屏）
        last_fail = self._failed.get(key)
        if last_fail is not None and (time.monotonic() - last_fail) < 60.0:
            return None
        n = 2 ** z
        if y < 0 or y >= n:
            return None
        url = self.url_template.format(z=z, x=x % n, y=y)
        req = QNetworkRequest(QUrl(url))
        try:
            req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader,
                          USER_AGENT)
            req.setAttribute(
                QNetworkRequest.Attribute.CacheLoadControlAttribute,
                QNetworkRequest.CacheLoadControl.PreferCache)
            req.setAttribute(
                QNetworkRequest.Attribute.RedirectPolicyAttribute,
                QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        except Exception:  # noqa: BLE001
            pass
        self._inflight.add(key)
        reply = self._nam.get(req)
        reply.finished.connect(lambda r=reply, k=key: self._on_finished(r, k))
        return None

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
        pm = QPixmap()
        if not pm.loadFromData(data):
            self.fail_count += 1
            self.failed.emit(z, x, y, "不是有效图片")
            return
        self.cache.put(z, x, y, pm)
        self.cache.store_disk(z, x, y, data)
        self.ok_count += 1
        self.ready.emit(z, x, y)


# --------------------------------------------------------------------------- #
# 视图
# --------------------------------------------------------------------------- #
try:  # 触屏手势可选：缺了不影响鼠标使用
    from touch_helpers import PinchZoomMixin as _PinchMixin
except Exception:  # noqa: BLE001
    class _PinchMixin:  # type: ignore
        def init_pinch_zoom(self) -> None:
            return


class OsmMapView(QWidget, _PinchMixin):
    """OSM 瓦片地图视图：拖拽平移、滚轮缩放、单击点选。

    信号
    ----
    picked(lat, lon)  单击选中（拖拽不算）
    zoom_changed(z)   缩放级别变化
    map_error(str)    连续失败、已无可显示瓦片时给一句人话

    覆盖层
    ------
    ``set_overlay(painter_cb)``：``cb(painter, project)``，``project(lat, lon)``
    返回控件坐标 ``QPointF``。ADS-B 用它画飞机点，无需另造地图。
    """

    picked = Signal(float, float)
    zoom_changed = Signal(int)
    map_error = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None, *,
                 online: bool = True, cache: Optional[TileCache] = None,
                 tile_source: Optional[Callable] = None,
                 show_attribution: bool = True):
        super().__init__(parent)
        self.setMinimumSize(240, 160)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._online = bool(online)
        self._cache = cache or TileCache()
        self._loader = TileLoader(self._cache, online=self._online, parent=self)
        self._loader.ready.connect(self._on_tile_ready)
        self._loader.failed.connect(self._on_tile_failed)
        # 测试注入：tile_source(z, x, y) -> QPixmap | None | "missing"
        self._tile_source = tile_source
        self._show_attr = bool(show_attribution)

        self._zoom = 3
        self._center_lat = 30.0
        self._center_lon = 110.0
        self._marker: Optional[Tuple[float, float]] = None
        self._overlay: Optional[Callable] = None
        self._drag_from: Optional[QPointF] = None
        self._drag_start: Optional[QPointF] = None
        self._dragging = False
        self._error_text = ""
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
            self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, int(zoom)))
            try:
                self.zoom_changed.emit(self._zoom)
            except RuntimeError:
                pass
        self.update()

    def set_marker(self, lat: Optional[float], lon: Optional[float]) -> None:
        self._marker = (None if lat is None or lon is None
                        else (float(lat), float(lon)))
        self.update()

    def marker(self) -> Optional[Tuple[float, float]]:
        return self._marker

    def set_overlay(self, cb: Optional[Callable]) -> None:
        self._overlay = cb
        self.update()

    def tile_stats(self) -> Tuple[int, int]:
        """(成功数, 失败数)——给诊断与测试看，不给人看。"""
        return self._loader.ok_count, self._loader.fail_count

    def attribution_rect(self) -> QRectF:
        rect = self._attr_rect()
        return QRectF(rect)

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

    # ------------------------------------------------------------ 绘制
    def _attr_rect(self):
        from PySide6.QtCore import QRect
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(ATTRIBUTION) + 12
        return QRect(self.width() - w - 6, self.height() - 20, w, 16)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.fillRect(self.rect(), QColor(_BG))
            if self.width() <= 0 or self.height() <= 0:
                return
            cx, cy = self._world_of_center()
            x0 = cx - self.width() / 2.0
            y0 = cy - self.height() / 2.0
            tx0, ty0, tx1, ty1 = tile_range(
                x0, y0, x0 + self.width(), y0 + self.height(), self._zoom)
            n = 2 ** self._zoom
            hw, hh = self.width() / 2.0, self.height() / 2.0
            missing = []
            # 第一遍：只画已缓存的，顺手记下缺哪些（按到屏幕中心的距离排队）。
            # 不按行优先请求——代理慢的时候，行优先会让"我在这"那块最后才出来。
            for tx in range(tx0, tx1 + 1):
                for ty in range(ty0, ty1 + 1):
                    px = tx * TILE_SIZE - x0
                    py = ty * TILE_SIZE - y0
                    pm = self._cached_tile(tx % n, ty)
                    if pm is not None:
                        p.drawPixmap(int(px), int(py), TILE_SIZE, TILE_SIZE, pm)
                    else:
                        p.fillRect(QRectF(px, py, TILE_SIZE, TILE_SIZE),
                                   _LOADING)
                        p.setPen(QPen(_GRID, 1, Qt.DotLine))
                        p.drawRect(QRectF(px + 0.5, py + 0.5,
                                          TILE_SIZE - 1, TILE_SIZE - 1))
                        d = ((px + TILE_SIZE / 2.0 - hw) ** 2
                             + (py + TILE_SIZE / 2.0 - hh) ** 2)
                        missing.append((d, tx % n, ty))
            # 第二遍：按距离由近到远发请求（在途上限由 TileLoader 把住）
            if missing and self._tile_source is None:
                missing.sort()
                for _d, tx, ty in missing:
                    self._loader.request(self._zoom, tx, ty)
            self._draw_grid(p)
            if self._overlay is not None:
                try:
                    self._overlay(p, self.project)
                except Exception:  # noqa: BLE001
                    pass
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
        p.setPen(QPen(QColor(_ACCENT), 2))
        p.setBrush(QColor(255, 91, 74, 60))
        p.drawEllipse(pos, 7.0, 7.0)
        p.setPen(QPen(QColor(_ACCENT), 1, Qt.DashLine))
        p.drawLine(QPointF(pos.x() - 15, pos.y()), QPointF(pos.x() + 15, pos.y()))
        p.drawLine(QPointF(pos.x(), pos.y() - 15), QPointF(pos.x(), pos.y() + 15))

    def _draw_hud(self, p: QPainter) -> None:
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1))
        p.setFont(f)
        # 左上：数据源与层级
        badge = f"OSM z{self._zoom}"
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
        # 右下：署名（ODbL）
        if self._show_attr:
            r = self._attr_rect()
            p.fillRect(QRectF(r), QColor(10, 14, 20, 170))
            p.setPen(QPen(QColor("#b9c0cc"), 1))
            p.drawText(QRectF(r), Qt.AlignCenter, ATTRIBUTION)

    # ------------------------------------------------------------ 交互
    def _zoom_at(self, new_zoom: int, anchor: QPointF) -> None:
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, int(new_zoom)))
        if new_zoom == self._zoom:
            return
        lat, lon = self.unproject(anchor)     # 锚点下的地理坐标保持不变
        self._zoom = new_zoom
        cx, cy = lonlat_to_world(lat, lon, self._zoom)
        # 让 (lat, lon) 仍落在 anchor 位置
        world = float(TILE_SIZE) * (2 ** self._zoom)
        wx = cx - (anchor.x() - self.width() / 2.0)
        wy = cy - (anchor.y() - self.height() / 2.0)
        wx = (wx % world)
        self._center_lat, self._center_lon = world_to_lonlat(
            wx, max(0.0, min(world - 1e-6, wy)), self._zoom)
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
            self.open_attribution()      # 署名校验：点它就该能开 ODbL 说明
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
        QDesktopServices.openUrl(QUrl("https://www.openstreetmap.org/copyright"))

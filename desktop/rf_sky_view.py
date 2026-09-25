"""
MBDSDR 射频天空视图 (RF Sky View) — 真实天文数据驱动
=====================================================
借鉴 Stellarium 的天空视图概念，搬到 SDR 领域：方位角等距投影
(azimuthal equidistant) 显示卫星实时位置、太阳/月亮、天线指向、
未来过境列表与昼夜背景。

本版本彻底移除任何经验性的假信号生成：卫星位置由 sgp4 + skyfield 真
传播给出，太阳/月亮由真实历表给出，昼夜背景由太阳高度决定。无 TLE 或
计算失败时优雅显示“未连接”，绝不伪造天空。

坐标 / 投影数学参考 Stellarium (GPL-3.0)，独立重实现于
mbdsdr_ai/celestial_geometry.py：
  - StelProjectorClasses.cpp:363-395  azimuthal equidistant (fisheye)
      forward: h=sqrt(vx²+vy²); f=atan2(h,-vz)/h; x=vx*f, y=vy*f
      backward: a=sqrt(x²+y²); f=sin(a)/a; vz=-cos(a)
  - StelCore.cpp:1075                  equinox-of-date -> alt/az 旋转
  - StelObserver.cpp:229-230           Rz(GMST+lon)*Ry(90-lat) 本地旋转
  - Satellite.cpp:1298-1305           固定步长采样轨道 trail
  - gSatTEME.cpp:66,80                twoline2rv + sgp4 传播
  - gSatWrapper.cpp:133-165           slant range -> 站心 az/alt
  - StelObject.cpp:939                民用晨昏太阳高度 -6°
  - MilkyWay.cpp:350 / StelToast.cpp:334  全天空亮度经验断点
  - StelCore.cpp:1243,2299            setJD / updateTime 时间引擎

时间统一来自 mbdsdr_ai.new_spacetime.get_time_engine()（时间穿梭真实驱动），
不使用系统墙钟做天文计算。

设计风格：日式低饱和 —— 米白 #F5F3EF 纸面、蓝灰 #5B7B8C 主色、
橙 #C4845C 强调色。字体优先 MiSans，禁用 emoji。

MBDSDR Project - AI定义无线电 - 全开源 GPL-3.0 - 呼号 BI4MIB
"""

import math
import os
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics,
    QPainterPath, QPolygonF, QLinearGradient, QRadialGradient,
    QMouseEvent, QWheelEvent, QResizeEvent,
)
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QLabel


# ============================================================================
# 数据类型
# ============================================================================


class SkyObject:
    """天空中的对象（卫星/信号源/干扰源/日月）。"""

    def __init__(
        self,
        name: str,
        azimuth_deg: float,
        elevation_deg: float,
        obj_type: str = "satellite",
        frequency_hz: float = 0.0,
        signal_strength_db: float = 0.0,
        description: str = "",
        color: str = "",
    ):
        self.name = name
        self.azimuth_deg = azimuth_deg  # 方位角 0-360°（北=0，东=90）
        self.elevation_deg = elevation_deg  # 仰角 -90..90°
        self.obj_type = obj_type  # satellite / signal / interferer / sun / moon / custom
        self.frequency_hz = frequency_hz
        self.signal_strength_db = signal_strength_db
        self.description = description
        self.color = color
        self.visible = True

    def is_above_horizon(self) -> bool:
        return self.elevation_deg > 0


class AntennaPointing:
    """天线指向状态。"""

    def __init__(
        self,
        azimuth_deg: float = 0.0,
        elevation_deg: float = 45.0,
        beamwidth_deg: float = 30.0,
        gain_dbi: float = 5.0,
        is_tracking: bool = False,
        target_name: str = "",
    ):
        self.azimuth_deg = azimuth_deg
        self.elevation_deg = elevation_deg
        self.beamwidth_deg = beamwidth_deg
        self.gain_dbi = gain_dbi
        self.is_tracking = is_tracking
        self.target_name = target_name


class HeatmapCell:
    """热力图单元格（方位-仰角-信号强度）。"""

    def __init__(self, azimuth_deg: float, elevation_deg: float,
                 signal_db: float, weight: float = 1.0):
        self.azimuth_deg = azimuth_deg
        self.elevation_deg = elevation_deg
        self.signal_db = signal_db
        self.weight = weight


# ============================================================================
# 地面站配置（从用户配置读取，不写死城市）
# ============================================================================


def load_ground_station_config() -> Optional[Dict[str, float]]:
    """从 ~/.mbdsdr/config.json 与环境变量读取地面站坐标。

    优先级（后者覆盖前者）：
      1) JSON: ground_station_lat / ground_station_lon / ground_station_alt_m
      2) 环境变量: MBDSDR_GS_LAT / MBDSDR_GS_LON / MBDSDR_GS_ALT_M

    未配置或坐标非法时返回 None（调用方据此显示“地面站未设置”，不造假）。
    """
    lat = lon = alt = None
    path = os.path.expanduser("~/.mbdsdr/config.json")
    try:
        if os.path.exists(path):
            import json
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            lat = cfg.get("ground_station_lat")
            lon = cfg.get("ground_station_lon")
            alt = cfg.get("ground_station_alt_m")
    except Exception:
        pass
    # 环境变量覆盖
    env_lat = os.environ.get("MBDSDR_GS_LAT")
    env_lon = os.environ.get("MBDSDR_GS_LON")
    env_alt = os.environ.get("MBDSDR_GS_ALT_M")
    if env_lat is not None:
        lat = env_lat
    if env_lon is not None:
        lon = env_lon
    if env_alt is not None:
        alt = env_alt
    try:
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
        alt = float(alt) if alt is not None else 0.0
    except (TypeError, ValueError):
        return None
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return {"lat": lat, "lon": lon, "alt_m": alt}


# ============================================================================
# 射频天空视图主组件
# ============================================================================


class RFSkyView(QWidget):
    """
    射频天空视图：方位角等距投影的天空图（天顶居中，地平线为边缘圆）。

    信号：
        object_clicked(SkyObject) - 点击天空对象
        pointing_changed(float, float) - 天线指向改变 (az, el)
    """

    object_clicked = Signal(object)  # SkyObject
    pointing_changed = Signal(float, float)  # az, el

    def __init__(self, parent=None):
        super().__init__(parent)

        # 视图状态
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._rotation = 0.0
        self._show_grid = True
        self._show_compass = True
        self._show_heatmap = False
        self._show_trajectories = True

        # 数据
        self._objects: List[SkyObject] = []
        self._antenna = AntennaPointing()
        self._heatmap: List[HeatmapCell] = []
        self._trajectories: Dict[str, List[Tuple[float, float]]] = {}

        # 数据来源："none"=无观测站位置 / "real"=真实GNSS或手动配置 / "sim"=模拟
        self._data_source: str = "none"
        # 卫星轨道计算是否真正连通（TLE+sgp4 至少成功算出一颗）
        self._satellites_connected: bool = False

        # 观测站坐标；未定位时为 None
        self._observer: Optional[Dict[str, float]] = None

        # 新时空：授时信息
        self._time_info = {
            "utc_time": None,
            "gps_week": 0,
            "gps_tow": 0.0,
            "ntp_server": "",
            "ntp_status": "system",
            "clock_offset_ms": 0.0,
        }

        # 真实天体位置（由 SatelliteTracker 每帧喂入；None=不绘制，不造假）
        self._sun: Optional[Dict[str, float]] = None   # {az, alt}
        self._moon: Optional[Dict[str, float]] = None  # {az, alt, illum}
        self._sky_brightness: float = 0.0              # 0=深夜 1=白天

        # 未来过境列表：[{name, rise(HH:MM UTC), max_alt, duration_min}]
        self._upcoming_passes: List[Dict[str, float]] = []

        # 交互状态
        self._dragging = False
        self._last_mouse_pos = QPointF()
        self._hovered_object: Optional[SkyObject] = None

        # 方位角等距投影（Stellarium fisheye）。
        # 坐标系：地平系 +x=北 +y=东 +z=天底；投影输出 (北分量, 东分量)。
        try:
            from mbdsdr_ai.celestial_geometry import (
                AzimuthalEquidistantProjection,
            )
            self._proj = AzimuthalEquidistantProjection()
        except Exception:
            self._proj = None

        # 颜色（日式低饱和）
        self._colors = {
            "paper": QColor("#F5F3EF"),       # 米白纸面
            "day_zenith": QColor("#DCE7EC"),
            "day_horizon": QColor("#F5F3EF"),
            "night_zenith": QColor("#0E1622"),
            "night_horizon": QColor("#1B2A3A"),
            "primary": QColor("#5B7B8C"),      # 蓝灰主色
            "accent": QColor("#C4845C"),       # 橙强调
            "grid_day": QColor(120, 140, 150, 90),
            "grid_night": QColor(90, 110, 130, 120),
            "horizon_day": QColor("#5B7B8C"),
            "horizon_night": QColor("#7E97A8"),
            "sat_weather": QColor("#5B7B8C"),  # 气象卫星：蓝灰
            "sat_amateur": QColor("#C4845C"),  # ISS/业余：橙
            "signal": QColor("#C4845C"),
            "interferer": QColor("#B06A6A"),
            "custom": QColor("#7E9B7E"),
            "sun": QColor("#D9A441"),
            "moon": QColor("#9AA7B4"),
            "antenna": QColor("#C4845C"),
            "antenna_beam": QColor(196, 132, 92, 36),
            "heatmap_low": QColor("#3E5A4A"),
            "heatmap_mid": QColor("#8A7A3A"),
            "heatmap_high": QColor("#8A4A3A"),
        }

        # 字体
        self._font = QFont()
        self._font.setFamilies([
            "MiSans", "MiSans Normal", "Noto Sans CJK SC",
            "PingFang SC", "Microsoft YaHei", "sans-serif",
        ])
        self._font.setPointSize(9)

        self._mono_font = QFont()
        self._mono_font.setFamilies([
            "JetBrains Mono", "Fira Code", "Source Code Pro",
            "Consolas", "monospace",
        ])
        self._mono_font.setPointSize(9)

        self.setMouseTracking(True)
        self.setMinimumSize(400, 400)

        # 自动刷新（30fps 重绘；轨道计算由 SatelliteTracker 单独定时器驱动）
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.update)
        self._refresh_timer.start(33)

    # ========================================================================
    # 公共 API
    # ========================================================================

    def set_objects(self, objects: List[SkyObject]):
        """设置天空对象列表。"""
        self._objects = objects
        self.update()

    def set_data_source(self, source: str):
        """设置数据来源标注："none" / "real" / "sim"。"""
        if source not in ("none", "real", "sim"):
            source = "none"
        self._data_source = source
        self.update()

    def set_satellites_connected(self, connected: bool):
        """标记卫星轨道计算是否真正连通（TLE+sgp4 成功）。"""
        self._satellites_connected = bool(connected)
        self.update()

    def set_gnss_position(self, fix_dict: dict):
        """由真实串口 GNSS fix 驱动观测站坐标与数据来源标注。"""
        source = fix_dict.get("source", "none")
        if source == "real" and fix_dict.get("latitude") is not None \
                and fix_dict.get("longitude") is not None:
            self._observer = {
                "lat": fix_dict["latitude"],
                "lon": fix_dict["longitude"],
                "alt_m": fix_dict.get("altitude_m"),
            }
            self.set_data_source("real")
        else:
            self._observer = None
            self.set_data_source("none")

    def get_observer(self) -> Optional[Dict[str, float]]:
        return self._observer

    def set_celestial_bodies(self, sun: Optional[Dict[str, float]],
                             moon: Optional[Dict[str, float]],
                             sky_brightness: float):
        """喂入真实太阳/月亮位置与天空亮度因子（由 SatelliteTracker 计算）。"""
        self._sun = sun
        self._moon = moon
        self._sky_brightness = max(0.0, min(1.0, float(sky_brightness)))
        self.update()

    def set_upcoming_passes(self, passes: List[Dict]):
        """喂入未来过境列表（rise 时间字符串 + max_alt + duration_min）。"""
        self._upcoming_passes = list(passes)[:12]
        self.update()

    def add_object(self, obj: SkyObject):
        self._objects.append(obj)
        self.update()

    def clear_objects(self):
        self._objects.clear()
        self.update()

    def set_antenna(self, antenna: AntennaPointing):
        self._antenna = antenna
        self.update()

    def get_antenna(self) -> AntennaPointing:
        return self._antenna

    def set_time_info(self, time_info: dict):
        self._time_info.update(time_info)
        self.update()

    def set_heatmap(self, cells: List[HeatmapCell]):
        self._heatmap = cells
        self._show_heatmap = len(cells) > 0
        self.update()

    def set_trajectory(self, name: str, points: List[Tuple[float, float]]):
        self._trajectories[name] = points
        self.update()

    def clear_trajectories(self):
        self._trajectories.clear()
        self.update()

    def set_show_grid(self, show: bool):
        self._show_grid = show
        self.update()

    def set_show_compass(self, show: bool):
        self._show_compass = show
        self.update()

    def set_show_heatmap(self, show: bool):
        self._show_heatmap = show
        self.update()

    def set_show_trajectories(self, show: bool):
        self._show_trajectories = show
        self.update()

    def reset_view(self):
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._rotation = 0.0
        self.update()

    # ========================================================================
    # 坐标转换：方位角等距投影 (Stellarium StelProjectorClasses.cpp:363-395)
    # ========================================================================

    def _sky_disk_radius(self) -> float:
        return min(self.width(), self.height()) * 0.45 * self._zoom

    def _sky_center(self) -> Tuple[float, float]:
        return (self.width() / 2 + self._pan_x, self.height() / 2 + self._pan_y)

    def _sky_to_screen(self, azimuth_deg: float, elevation_deg: float) -> QPointF:
        """天空坐标 (az, alt) -> 屏幕坐标。

        天顶(alt=90°)映射到圆心；地平(alt=0°)映射到边缘圆。
        方位角 0°=北在顶部，顺时针增大。

        用 celestial_geometry.AzimuthalEquidistantProjection 做前向投影：
          投影输出 (北分量*f, 东分量*f)，地平处模长 = pi/2 弧度。
        屏幕坐标约定 y 向下，故北(+)映射到 -y，东(+)映射到 +x。
        参考 StelProjectorClasses.cpp:365-372 forward。
        """
        cx, cy = self._sky_center()
        radius = self._sky_disk_radius()
        if self._proj is not None:
            alt = max(-90.0, min(90.0, elevation_deg))
            px, py = self._proj.project_azalt(azimuth_deg, alt)
            if not (math.isfinite(px) and math.isfinite(py)):
                return QPointF(cx, cy)
            # 地平处归一化半径 = pi/2
            scale = radius * 2.0 / math.pi
            sx = cx + py * scale   # 东 -> 右
            sy = cy - px * scale    # 北 -> 上
            return QPointF(sx, sy)

        # 纯数学回退（与投影等价的线性映射）
        r = radius * (90.0 - max(0.0, min(90.0, elevation_deg))) / 90.0
        ang = math.radians(azimuth_deg - 90.0 + self._rotation)
        return QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang))

    def _screen_to_sky(self, screen_x: float, screen_y: float) -> Tuple[float, float]:
        """屏幕坐标 -> (az, alt)。用投影逆变换 (StelProjectorClasses.cpp:389-394)。"""
        cx, cy = self._sky_center()
        radius = self._sky_disk_radius()
        if radius < 1:
            return 0.0, 90.0
        scale = radius * 2.0 / math.pi
        px = -(screen_y - cy) / scale   # 北分量
        py = (screen_x - cx) / scale    # 东分量
        # 截断到地平圆
        mag = math.hypot(px, py)
        max_mag = math.pi / 2.0
        if mag > max_mag:
            px *= max_mag / mag
            py *= max_mag / mag
            mag = max_mag
        if self._proj is not None:
            try:
                v = self._proj.unproject_vec(px, py)
                from mbdsdr_ai.celestial_geometry import azalt_from_vec
                az, alt = azalt_from_vec(v)
                return az % 360.0, alt
            except Exception:
                pass
        # 回退
        if mag < 1e-6:
            return 0.0, 90.0
        alt = 90.0 - math.degrees(mag)
        az = (math.degrees(math.atan2(py, px)) + 360.0) % 360.0
        return az, alt

    # ========================================================================
    # 颜色工具
    # ========================================================================

    def _ink(self) -> QColor:
        """根据昼夜亮度返回前景文字/网格色（白天深、夜晚浅）。"""
        b = self._sky_brightness
        # 0=夜 -> 浅蓝灰字；1=昼 -> 深灰字
        night = QColor("#D8DEE6")
        day = QColor("#3A4550")
        r = int(night.red() * (1 - b) + day.red() * b)
        g = int(night.green() * (1 - b) + day.green() * b)
        bl = int(night.blue() * (1 - b) + day.blue() * b)
        return QColor(r, g, bl)

    # ========================================================================
    # 绘制
    # ========================================================================

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        # 纸面背景（米白）
        painter.fillRect(self.rect(), self._colors["paper"])

        self._draw_sky_disk(painter)

        if self._show_heatmap and self._heatmap:
            self._draw_heatmap(painter)
        if self._show_grid:
            self._draw_grid(painter)
        if self._show_trajectories:
            self._draw_trajectories(painter)

        self._draw_antenna_beam(painter)
        self._draw_celestial_bodies(painter)
        self._draw_objects(painter)
        self._draw_antenna_pointer(painter)

        if self._show_compass:
            self._draw_compass(painter)

        self._draw_info_overlay(painter)
        self._draw_passes_panel(painter)

        if self._hovered_object:
            self._draw_hover_tooltip(painter)

        self._draw_data_source_overlay(painter)

        painter.end()

    def _draw_sky_disk(self, painter: QPainter):
        """绘制天空圆盘背景：按太阳高度做昼夜/黄昏渐变。

        亮度因子来自 atmosphere.sky_brightness_factor（MilkyWay.cpp:350 断点）：
        白天=米白浅蓝、黄昏=渐变、夜晚=深蓝。
        """
        cx, cy = self._sky_center()
        radius = self._sky_disk_radius()
        if radius < 2:
            return
        b = self._sky_brightness

        def mix(c1: QColor, c2: QColor, t: float) -> QColor:
            return QColor(
                int(c1.red() * (1 - t) + c2.red() * t),
                int(c1.green() * (1 - t) + c2.green() * t),
                int(c1.blue() * (1 - t) + c2.blue() * t),
            )

        zenith = mix(self._colors["night_zenith"], self._colors["day_zenith"], b)
        horizon = mix(self._colors["night_horizon"], self._colors["day_horizon"], b)

        painter.save()
        painter.setPen(Qt.NoPen)
        grad = QRadialGradient(QPointF(cx, cy), radius)
        grad.setColorAt(0.0, zenith)
        grad.setColorAt(1.0, horizon)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)
        painter.restore()

    def _draw_grid(self, painter: QPainter):
        """极坐标网格（方位线 + 仰角圈）。"""
        cx, cy = self._sky_center()
        radius = self._sky_disk_radius()
        painter.save()
        painter.setFont(self._font)

        grid_col = self._colors["grid_day"] if self._sky_brightness > 0.5 \
            else self._colors["grid_night"]
        grid_pen = QPen(grid_col, 1, Qt.DashLine)
        painter.setPen(grid_pen)

        for elev in (0, 30, 60):
            r = radius * (90.0 - elev) / 90.0
            painter.drawEllipse(QPointF(cx, cy), r, r)
            label_pos = self._sky_to_screen(270, elev)
            painter.setPen(self._ink())
            painter.drawText(QPointF(label_pos.x() - 26, label_pos.y() + 4), f"{elev}°")
            painter.setPen(grid_pen)

        for az in range(0, 360, 30):
            painter.drawLine(self._sky_to_screen(az, 0), self._sky_to_screen(az, 90))

        horizon_pen = QPen(
            self._colors["horizon_day"] if self._sky_brightness > 0.5
            else self._colors["horizon_night"], 2)
        painter.setPen(horizon_pen)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)
        painter.restore()

    def _draw_compass(self, painter: QPainter):
        painter.save()
        painter.setFont(self._font)
        for az, label in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            pos = self._sky_to_screen(az, 90)
            f = QFont(self._font)
            f.setBold(True)
            f.setPointSize(12)
            painter.setFont(f)
            painter.setPen(self._ink())
            painter.drawText(QPointF(pos.x() - 8, pos.y() + 5), label)
            painter.setFont(self._font)
        painter.setPen(self._ink())
        for az in range(0, 360, 30):
            if az % 90 == 0:
                continue
            pos = self._sky_to_screen(az, 3)
            painter.drawText(QPointF(pos.x() - 12, pos.y() + 4), f"{az}°")
        painter.restore()

    def _draw_celestial_bodies(self, painter: QPainter):
        """绘制真实太阳/月亮标记（在地平线上方才画）。"""
        painter.save()
        # 太阳
        if self._sun and self._sun.get("alt", -99) > -1:
            pos = self._sky_to_screen(self._sun["az"], max(0.0, self._sun["alt"]))
            col = self._colors["sun"]
            painter.setPen(QPen(col, 1))
            painter.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(), 60)))
            painter.drawEllipse(pos, 14, 14)
            painter.setBrush(QBrush(col))
            painter.drawEllipse(pos, 7, 7)
            painter.setPen(self._ink())
            painter.drawText(QPointF(pos.x() + 12, pos.y() - 8), "太阳")
        # 月亮
        if self._moon and self._moon.get("alt", -99) > 0:
            pos = self._sky_to_screen(self._moon["az"], self._moon["alt"])
            col = self._colors["moon"]
            painter.setPen(QPen(col, 1))
            painter.setBrush(QBrush(col))
            painter.drawEllipse(pos, 6, 6)
            painter.setPen(self._ink())
            painter.drawText(QPointF(pos.x() + 10, pos.y() - 6), "月亮")
        painter.restore()

    def _draw_objects(self, painter: QPainter):
        """绘制天空对象（卫星/信号源/干扰源）。"""
        painter.save()
        for obj in self._objects:
            if not obj.visible or not obj.is_above_horizon():
                continue
            pos = self._sky_to_screen(obj.azimuth_deg, obj.elevation_deg)
            color = QColor(obj.color) if obj.color else self._colors.get(
                obj.obj_type, self._colors["custom"])

            if obj.obj_type == "satellite":
                size = 6
                diamond = QPolygonF([
                    QPointF(pos.x(), pos.y() - size),
                    QPointF(pos.x() + size, pos.y()),
                    QPointF(pos.x(), pos.y() + size),
                    QPointF(pos.x() - size, pos.y()),
                ])
                painter.setPen(QPen(color, 2))
                painter.setBrush(QBrush(color))
                painter.drawPolygon(diamond)
                painter.setPen(self._ink())
                painter.setFont(self._font)
                painter.drawText(QPointF(pos.x() + 10, pos.y() - 5), obj.name)
                painter.setPen(self._ink())
                painter.setFont(self._mono_font)
                painter.drawText(
                    QPointF(pos.x() + 10, pos.y() + 22),
                    f"EL {obj.elevation_deg:4.1f} AZ {obj.azimuth_deg:5.1f}")
                if obj.frequency_hz > 0:
                    painter.drawText(
                        QPointF(pos.x() + 10, pos.y() + 36),
                        f"{obj.frequency_hz / 1e6:.1f} MHz")
            elif obj.obj_type == "signal":
                painter.setPen(QPen(color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(pos, 5, 5)
                painter.drawEllipse(pos, 9, 9)
                painter.setPen(self._ink())
                painter.setFont(self._font)
                painter.drawText(QPointF(pos.x() + 12, pos.y() + 4), obj.name)
            elif obj.obj_type == "interferer":
                size = 7
                triangle = QPolygonF([
                    QPointF(pos.x(), pos.y() - size),
                    QPointF(pos.x() + size, pos.y() + size),
                    QPointF(pos.x() - size, pos.y() + size),
                ])
                painter.setPen(QPen(color, 2))
                painter.setBrush(QBrush(color))
                painter.drawPolygon(triangle)
                painter.setPen(self._ink())
                painter.setFont(self._font)
                painter.drawText(QPointF(pos.x() + 10, pos.y() - 5), obj.name)
            else:
                painter.setPen(QPen(color, 2))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(pos, 5, 5)
                painter.setPen(self._ink())
                painter.setFont(self._font)
                painter.drawText(QPointF(pos.x() + 10, pos.y() + 4), obj.name)
        painter.restore()

    def _draw_antenna_beam(self, painter: QPainter):
        if self._antenna.elevation_deg <= 0:
            return
        painter.save()
        half_bw = self._antenna.beamwidth_deg / 2.0
        corners = [
            self._sky_to_screen(self._antenna.azimuth_deg - half_bw,
                                self._antenna.elevation_deg + half_bw),
            self._sky_to_screen(self._antenna.azimuth_deg + half_bw,
                                self._antenna.elevation_deg + half_bw),
            self._sky_to_screen(self._antenna.azimuth_deg + half_bw,
                                max(0, self._antenna.elevation_deg - half_bw)),
            self._sky_to_screen(self._antenna.azimuth_deg - half_bw,
                                max(0, self._antenna.elevation_deg - half_bw)),
        ]
        painter.setBrush(QBrush(self._colors["antenna_beam"]))
        painter.setPen(Qt.NoPen)
        path = QPainterPath()
        path.moveTo(corners[0])
        for c in corners[1:]:
            path.lineTo(c)
        path.closeSubpath()
        painter.drawPath(path)
        painter.restore()

    def _draw_antenna_pointer(self, painter: QPainter):
        if self._antenna.elevation_deg <= 0:
            return
        pos = self._sky_to_screen(self._antenna.azimuth_deg,
                                  self._antenna.elevation_deg)
        painter.save()
        painter.setPen(QPen(self._colors["antenna"], 2))
        s = 12
        painter.drawLine(QPointF(pos.x() - s, pos.y()), QPointF(pos.x() - 4, pos.y()))
        painter.drawLine(QPointF(pos.x() + 4, pos.y()), QPointF(pos.x() + s, pos.y()))
        painter.drawLine(QPointF(pos.x(), pos.y() - s), QPointF(pos.x(), pos.y() - 4))
        painter.drawLine(QPointF(pos.x(), pos.y() + 4), QPointF(pos.x(), pos.y() + s))
        painter.setBrush(QBrush(self._colors["antenna"]))
        painter.drawEllipse(pos, 3, 3)
        if self._antenna.is_tracking and self._antenna.target_name:
            painter.setPen(self._colors["antenna"])
            painter.setFont(self._font)
            painter.drawText(QPointF(pos.x() + 16, pos.y() - 8),
                             f"跟踪: {self._antenna.target_name}")
        painter.restore()

    def _draw_trajectories(self, painter: QPainter):
        """卫星轨迹（半透明线）。"""
        painter.save()
        for name, points in self._trajectories.items():
            if len(points) < 2:
                continue
            color = self._colors["sat_weather"]
            for obj in self._objects:
                if obj.name == name:
                    color = QColor(obj.color) if obj.color else color
                    break
            c = QColor(color.red(), color.green(), color.blue(), 110)
            pen = QPen(c, 1, Qt.DashLine)
            painter.setPen(pen)
            path = QPainterPath()
            first = True
            for az, el in points:
                if el <= 0:
                    first = True
                    continue
                pos = self._sky_to_screen(az, el)
                if first:
                    path.moveTo(pos)
                    first = False
                else:
                    path.lineTo(pos)
            painter.drawPath(path)
        painter.restore()

    def _draw_heatmap(self, painter: QPainter):
        if not self._heatmap:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        for cell in self._heatmap:
            if cell.elevation_deg <= 0:
                continue
            pos = self._sky_to_screen(cell.azimuth_deg, cell.elevation_deg)
            norm = max(0.0, min(1.0, (cell.signal_db + 100) / 70))
            lo, mid, hi = (self._colors["heatmap_low"], self._colors["heatmap_mid"],
                           self._colors["heatmap_high"])
            if norm < 0.5:
                t = norm * 2
                c = QColor(int(lo.red() * (1 - t) + mid.red() * t),
                           int(lo.green() * (1 - t) + mid.green() * t),
                           int(lo.blue() * (1 - t) + mid.blue() * t), 80)
            else:
                t = (norm - 0.5) * 2
                c = QColor(int(mid.red() * (1 - t) + hi.red() * t),
                           int(mid.green() * (1 - t) + hi.green() * t),
                           int(mid.blue() * (1 - t) + hi.blue() * t), 80)
            painter.setBrush(QBrush(c))
            painter.drawEllipse(pos, 15, 15)
        painter.restore()

    def _draw_info_overlay(self, painter: QPainter):
        """左下角信息卡片（天线指向 / 可见卫星 / 授时）。"""
        painter.save()
        card_w, card_h = 240, 132
        card_x, card_y = 12, self.height() - card_h - 12
        bg = QColor(245, 243, 239, 220) if self._sky_brightness > 0.5 \
            else QColor(20, 28, 38, 210)
        border = QColor("#5B7B8C") if self._sky_brightness > 0.5 else QColor("#3A4A5A")
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(card_x, card_y, card_w, card_h, 6, 6)

        ink = self._ink()
        dim = QColor("#7A8694") if self._sky_brightness > 0.5 else QColor("#8A96A4")
        label_col = QColor("#3A4550") if self._sky_brightness > 0.5 else QColor("#C8D0DA")

        y = card_y + 20
        painter.setPen(label_col)
        painter.setFont(self._font)
        painter.drawText(card_x + 12, y, "天线指向")
        painter.setFont(self._mono_font)
        painter.setPen(QPen(self._colors["antenna"], 2).color())
        painter.drawText(card_x + 80, y,
                         f"AZ {self._antenna.azimuth_deg:5.1f} EL {self._antenna.elevation_deg:4.1f}")

        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "可见卫星")
        painter.setFont(self._mono_font)
        visible = sum(1 for o in self._objects if o.visible and o.is_above_horizon())
        painter.setPen(ink)
        painter.drawText(card_x + 80, y, f"{visible} 颗")

        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "太阳高度")
        painter.setFont(self._mono_font)
        painter.setPen(ink)
        sun_alt = self._sun["alt"] if self._sun else None
        painter.drawText(card_x + 80, y,
                         f"{sun_alt:5.1f} deg" if sun_alt is not None else "--")

        y += 20
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "UTC")
        painter.setFont(self._mono_font)
        painter.setPen(dim)
        utc_now = self._time_info.get("utc_time") or datetime.now(timezone.utc)
        utc_str = utc_now.strftime("%H:%M:%S") if isinstance(utc_now, datetime) else str(utc_now)
        painter.drawText(card_x + 52, y, f"{utc_str} [{self._time_info.get('ntp_status','system')}]")

        y += 18
        painter.setFont(self._font)
        painter.setPen(dim)
        painter.drawText(card_x + 12, y, "滚轮缩放 | 拖拽旋转 | 双击重置")
        painter.restore()

    def _draw_passes_panel(self, painter: QPainter):
        """右侧未来过境列表（rise 时间 / 最大仰角 / 时长）。"""
        if not self._upcoming_passes:
            return
        painter.save()
        rows = self._upcoming_passes[:8]
        row_h = 18
        panel_w = 190
        panel_h = 26 + len(rows) * row_h
        panel_x = self.width() - panel_w - 12
        panel_y = 56
        bg = QColor(245, 243, 239, 220) if self._sky_brightness > 0.5 \
            else QColor(20, 28, 38, 210)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor("#5B7B8C"), 1))
        painter.drawRoundedRect(panel_x, panel_y, panel_w, panel_h, 6, 6)

        ink = self._ink()
        dim = QColor("#7A8694") if self._sky_brightness > 0.5 else QColor("#8A96A4")
        painter.setFont(self._font)
        painter.setPen(ink)
        painter.drawText(panel_x + 10, panel_y + 16, "未来过境 (24h)")

        painter.setFont(self._mono_font)
        ty = panel_y + 16 + row_h
        for p in rows:
            painter.setPen(dim)
            painter.drawText(panel_x + 10, ty, str(p.get("rise", "--")))
            painter.setPen(ink)
            name = str(p.get("name", ""))[:12]
            painter.drawText(panel_x + 58, ty, name)
            painter.setPen(self._colors["accent"])
            painter.drawText(panel_x + panel_w - 78, ty,
                             f"{float(p.get('max_alt', 0)):4.0f} deg")
            ty += row_h
        painter.restore()

    def _draw_data_source_overlay(self, painter: QPainter):
        """数据来源标注：未连接空状态 / 模拟角标 / 卫星未连接提示。"""
        painter.save()
        if self._data_source == "none":
            f = QFont(self._font)
            f.setPointSize(11)
            painter.setFont(f)
            painter.setPen(self._ink())
            painter.drawText(QRectF(0, 0, self.width(), self.height()),
                             Qt.AlignCenter,
                             "未连接 — 地面站未设置 (配置经纬度或连接 GNSS)")
        else:
            # 已配置站址但卫星轨道计算未连通
            if not self._satellites_connected:
                f = QFont(self._font)
                f.setPointSize(10)
                painter.setFont(f)
                painter.setPen(self._colors["accent"])
                painter.drawText(QRectF(0, 0, self.width(), self.height() - 60),
                                 Qt.AlignCenter, "卫星数据未连接 (TLE 不可用)")
            if self._data_source == "sim":
                badge = "[模拟]"
                f = QFont(self._font)
                f.setBold(True)
                f.setPointSize(9)
                painter.setFont(f)
                fm = QFontMetrics(f)
                bw = fm.horizontalAdvance(badge) + 20
                bh = fm.height() + 10
                bx, by = self.width() - bw - 12, 12
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor("#C4845C")))
                painter.drawRoundedRect(QRectF(bx, by, bw, bh), 4, 4)
                painter.setPen(QColor("#F5F3EF"))
                painter.drawText(QRectF(bx, by, bw, bh), Qt.AlignCenter, badge)
        painter.restore()

    def _draw_hover_tooltip(self, painter: QPainter):
        obj = self._hovered_object
        if not obj:
            return
        pos = self._sky_to_screen(obj.azimuth_deg, obj.elevation_deg)
        lines = [
            obj.name,
            f"方位 {obj.azimuth_deg:.1f}  仰角 {obj.elevation_deg:.1f}",
        ]
        if obj.frequency_hz > 0:
            lines.append(f"频率 {obj.frequency_hz / 1e6:.1f} MHz")
        if obj.description:
            lines.append(obj.description[:30])
        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        box_w = max(fm.horizontalAdvance(l) for l in lines) + 20
        box_h = len(lines) * 18 + 12
        box_x = int(pos.x() + 15)
        box_y = int(pos.y() - box_h - 10)
        if box_x + box_w > self.width():
            box_x = int(pos.x() - box_w - 15)
        if box_y < 0:
            box_y = int(pos.y() + 15)
        bg = QColor(245, 243, 239, 235) if self._sky_brightness > 0.5 \
            else QColor(20, 28, 38, 235)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor("#5B7B8C"), 1))
        painter.drawRoundedRect(box_x, box_y, box_w, box_h, 4, 4)
        painter.setPen(self._ink())
        y = box_y + 18
        for i, line in enumerate(lines):
            f = QFont(self._font)
            f.setBold(i == 0)
            painter.setFont(f)
            painter.drawText(box_x + 10, y, line)
            y += 18

    # ========================================================================
    # 交互事件（滚轮缩放 / 拖拽旋转 —— 接口预留，供后续增强）
    # ========================================================================

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_mouse_pos = QPointF(event.position())
            clicked = self._find_object_at(event.position().x(), event.position().y())
            if clicked:
                self.object_clicked.emit(clicked)

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()
        hovered = self._find_object_at(pos.x(), pos.y())
        if hovered != self._hovered_object:
            self._hovered_object = hovered
            self.update()
        if self._dragging:
            dx = pos.x() - self._last_mouse_pos.x()
            dy = pos.y() - self._last_mouse_pos.y()
            self._rotation = (self._rotation + dx * 0.3) % 360
            self._pan_y += dy
            self._last_mouse_pos = QPointF(pos)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.reset_view()

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(3.0, self._zoom * 1.1)
        else:
            self._zoom = max(0.3, self._zoom / 1.1)
        self.update()

    def _find_object_at(self, x: float, y: float, tolerance: float = 12.0) -> Optional[SkyObject]:
        for obj in self._objects:
            if not obj.visible or not obj.is_above_horizon():
                continue
            pos = self._sky_to_screen(obj.azimuth_deg, obj.elevation_deg)
            if math.hypot(pos.x() - x, pos.y() - y) < tolerance:
                return obj
        return None

    def resizeEvent(self, event: QResizeEvent):
        self.update()


# ============================================================================
# 天空视图面板（带标题栏和控制按钮）
# ============================================================================


class RFSkyViewPanel(QFrame):
    """射频天空视图面板：包含标题栏、控制按钮和 RFSkyView。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("skyViewPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sky_view = RFSkyView()
        layout.addWidget(self.sky_view)
        self._build_control_bar()

    def _build_control_bar(self):
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QFrame
        control_bar = QFrame()
        control_bar.setObjectName("skyControlBar")
        control_bar.setFixedHeight(36)
        control_bar.setStyleSheet("""
            QFrame#skyControlBar { background: rgba(245,243,239,200);
                border-top: 1px solid rgba(91,123,140,80); }
            QPushButton { background: rgba(91,123,140,150); color: #F5F3EF;
                border: 1px solid rgba(91,123,140,120); border-radius: 3px;
                padding: 2px 8px; font-size: 9pt; min-width: 40px; }
            QPushButton:hover { background: rgba(196,132,92,200); }
            QLabel { color: #5B7B8C; font-size: 9pt; padding: 0 8px; }
        """)
        bar = QHBoxLayout(control_bar)
        bar.setContentsMargins(8, 2, 8, 2)
        bar.setSpacing(4)
        self.time_label = QLabel("实时")
        self.time_label.setMinimumWidth(160)
        bar.addWidget(self.time_label)
        bar.addStretch()
        for label, cb in (("<< -10m", lambda: self._adjust(-600)),
                          ("实时", self._reset_time),
                          ("+10m >>", lambda: self._adjust(600))):
            b = QPushButton(label)
            b.clicked.connect(cb)
            bar.addWidget(b)
        bar.addSpacing(16)
        b = QPushButton("重置视图")
        b.clicked.connect(self.reset_view)
        bar.addWidget(b)
        self.layout().addWidget(control_bar)
        self._offset = 0.0

    def _adjust(self, seconds: float):
        self._offset += seconds
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            eng = get_time_engine()
            eng.set_unix(eng.now_unix() + seconds)
        except Exception:
            pass
        self._update_label()

    def _reset_time(self):
        self._offset = 0.0
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            get_time_engine().set_time_now()
        except Exception:
            pass
        self._update_label()

    def _update_label(self):
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            t = get_time_engine().now_utc()
            self.time_label.setText(t.strftime("%Y-%m-%d %H:%M UTC"))
        except Exception:
            self.time_label.setText("实时")

    def get_sim_time(self) -> float:
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            return get_time_engine().now_unix()
        except Exception:
            return time.time()

    def set_objects(self, objects):
        self.sky_view.set_objects(objects)

    def set_antenna(self, antenna):
        self.sky_view.set_antenna(antenna)

    def reset_view(self):
        self.sky_view.reset_view()


# ============================================================================
# 内置默认 TLE（离线兜底；联网时由 orbit.fetch_tle 自动刷新）
# ----------------------------------------------------------------------------
# 这些 TLE 为 2026-09-24 (epoch 26267) 的 celestrak 实测值，作为离线兜底。
# 真实运行时 SatelliteTracker 优先尝试在线拉取最新 TLE；拉取失败则用此兜底，
# 保证无网络也能算出合理的 az/alt。计算仍失败则显示“未连接”，不造假。
# 参考 gSatTEME.cpp:66 twoline2rv。
# ============================================================================

DEFAULT_TLES: Dict[str, Dict[str, object]] = {
    "NOAA 15": {
        "freq_mhz": 137.620, "kind": "weather",
        "tle": ["NOAA 15",
                "1 25338U 98030A   26267.25018134  .00000087  00000+0  53050-4 0  9999",
                "2 25338  98.5051 285.1988 0011269  60.6345 299.5961 14.27170562475423"],
    },
    "NOAA 18": {
        "freq_mhz": 137.9125, "kind": "weather",
        "tle": ["NOAA 18",
                "1 28654U 05018A   26267.27642994  .00000032  00000+0  39901-4 0  9995",
                "2 28654  98.8051 345.4844 0013504 226.2734 133.7322 14.13744400100313"],
    },
    "NOAA 19": {
        "freq_mhz": 137.100, "kind": "weather",
        "tle": ["NOAA 19",
                "1 33591U 09005A   26267.29309843  .00000009  00000+0  28340-4 0  9995",
                "2 33591  98.9434 338.1952 0014779 109.0362 251.2412 14.13486774908481"],
    },
    "ISS (ZARYA)": {
        "freq_mhz": 145.800, "kind": "amateur",
        "tle": ["ISS (ZARYA)",
                "1 25544U 98067A   26267.14191496  .00009634  00000+0  18116-3 0  9999",
                "2 25544  51.6318 170.3464 0004691 174.6338 185.4701 15.49258637587098"],
    },
    "FENGYUN 3D": {
        "freq_mhz": 136.900, "kind": "weather",
        "tle": ["FENGYUN 3D",
                "1 43010U 17072A   26267.23430764 -.00000010  00000+0  17383-4 0  9994",
                "2 43010  99.0253 243.5043 0002376 107.2307 252.9129 14.19756230459047"],
    },
}


# ============================================================================
# 实时卫星跟踪桥：sgp4 + skyfield 真传播，喂入天空图
# ============================================================================


class SatelliteTracker:
    """定时用 sgp4+skyfield 计算卫星实时地平坐标，更新天空图。

    时间统一从 new_spacetime.get_time_engine() 取（时间穿梭真实驱动），
    不用系统墙钟。站址由外部 set_location 注入；为 None 时不计算，
    天空图显示“未连接”。TLE 内置兜底 + 在线刷新，失败不造假。
    """

    def __init__(self, sky_view, lat: Optional[float], lon: Optional[float],
                 alt_km: float = 0.0, interval_ms: int = 10000):
        self.sky_view = sky_view
        self.lat = lat
        self.lon = lon
        self.alt_km = alt_km
        self._interval_ms = interval_ms
        self._sat_cache: Dict[str, object] = {}   # name -> skyfield EarthSatellite
        self._ts = None
        self._timer = QTimer(self.sky_view)
        self._timer.timeout.connect(self.refresh)
        if lat is None or lon is None:
            self.lat = None
            self.lon = None
            self.sky_view.set_objects([])
            self.sky_view.clear_trajectories()
            self.sky_view.set_satellites_connected(False)
            return
        self._timer.start(interval_ms)
        self.refresh()

    # ------------------------------------------------------------------ TLE
    def _get_ts(self):
        if self._ts is None:
            try:
                from mbdsdr_ai.sat_passes import make_timescale
                self._ts = make_timescale()
            except Exception:
                self._ts = False
        return self._ts or None

    def _get_satellite(self, name: str):
        """返回 (skyfield.EarthSatellite, kind)；失败返回 (None, None)。

        优先在线拉最新 TLE（orbit.fetch_tle，带缓存），失败回退内置兜底 TLE。
        参考 gSatTEME.cpp:66 twoline2rv。
        """
        if name in self._sat_cache:
            return self._sat_cache[name]
        info = DEFAULT_TLES.get(name)
        if info is None:
            return None, None
        kind = info["kind"]
        line1 = line2 = None
        # 1) 在线刷新（best-effort）
        try:
            from mbdsdr_ai import orbit
            catnr = orbit.BUILTIN_SATS.get(name)
            if catnr is not None:
                l1, l2 = orbit.fetch_tle(catnr)
                line1, line2 = l1, l2
        except Exception:
            line1 = line2 = None
        # 2) 内置兜底
        if line1 is None:
            tle = info["tle"]
            if len(tle) >= 3:
                line1, line2 = tle[1], tle[2]
        if line1 is None:
            return None, None
        try:
            from skyfield.api import EarthSatellite
            ts = self._get_ts()
            sat = EarthSatellite(line1, line2, name, ts)
        except Exception:
            return None, None
        self._sat_cache[name] = (sat, kind)
        return sat, kind

    # ------------------------------------------------------------- 位置计算
    def _topos(self):
        from skyfield.api import Topos
        return Topos(latitude_degrees=self.lat, longitude_degrees=self.lon,
                     elevation_m=self.alt_km * 1000.0)

    def _engine_unix(self) -> float:
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            eng = get_time_engine()
            eng.tick()
            return eng.now_unix()
        except Exception:
            return time.time()

    def _compute_sun_moon(self, t_unix: float) -> Tuple[Optional[Dict], Optional[Dict], float]:
        """真实太阳/月亮 az/alt + 天空亮度因子。任何一步失败都优雅降级。"""
        sun = moon = None
        brightness = 0.0
        jd = t_unix / 86400.0 + 2440587.5
        # 亮度：纯 Python Meeus（atmosphere，离线可用）
        try:
            from mbdsdr_ai import atmosphere
            sun_alt = atmosphere.sun_altitude_deg(jd, self.lat, self.lon)
            brightness = atmosphere.sky_brightness_factor(sun_alt)
        except Exception:
            sun_alt = None
        # 太阳方位角（Meeus，离线）
        try:
            from mbdsdr_ai.atmosphere import _sun_ra_dec_deg, _gmst_deg
            ra, dec = _sun_ra_dec_deg(jd)
            lst = (_gmst_deg(jd) + self.lon) % 360.0
            ha = math.radians((lst - ra) % 360.0)
            lat = math.radians(self.lat)
            dec_r = math.radians(dec)
            sin_alt = math.sin(lat) * math.sin(dec_r) \
                + math.cos(lat) * math.cos(dec_r) * math.cos(ha)
            sin_alt = max(-1.0, min(1.0, sin_alt))
            alt = math.degrees(math.asin(sin_alt))
            cos_alt = max(0.05, math.cos(math.radians(alt)))
            cos_az = (math.sin(dec_r) - math.sin(math.radians(alt)) * math.sin(lat)) \
                / (cos_alt * math.cos(lat))
            sin_az = -math.cos(dec_r) * math.sin(ha) / cos_alt
            az = (math.degrees(math.atan2(sin_az, cos_az)) + 360.0) % 360.0
            sun = {"az": az, "alt": alt}
        except Exception:
            pass
        # 月亮：优先真实历表（solar_system），失败则不画（不造假）
        try:
            from mbdsdr_ai import solar_system
            mp = solar_system.get_moon_position(
                t_unix, (self.lat, self.lon, self.alt_km * 1000.0))
            if mp is not None:
                moon = {"az": mp.az_deg, "alt": mp.alt_deg,
                        "illum": mp.illumination or 0.0}
        except Exception:
            moon = None
        return sun, moon, brightness

    # ------------------------------------------------------------------ 主刷新
    def set_location(self, lat: Optional[float], lon: Optional[float],
                     alt_km: Optional[float] = None):
        """更新观测站坐标；None 则停止计算并清空（空状态）。"""
        self.lat = lat
        self.lon = lon
        if alt_km is not None:
            self.alt_km = alt_km
        if lat is None or lon is None:
            self._timer.stop()
            self.sky_view.set_objects([])
            self.sky_view.clear_trajectories()
            self.sky_view.set_satellites_connected(False)
            self.sky_view.set_celestial_bodies(None, None, 0.0)
            self.sky_view.set_data_source("none")
            return
        if not self._timer.isActive():
            self._timer.start(self._interval_ms)
        self.refresh()

    def refresh(self):
        """每帧：从 TimeEngine 取时间 -> sgp4 算卫星 -> 投影喂给天空图。"""
        if self.lat is None or self.lon is None:
            return
        t_unix = self._engine_unix()

        # 太阳/月亮/昼夜
        try:
            sun, moon, brightness = self._compute_sun_moon(t_unix)
            self.sky_view.set_celestial_bodies(sun, moon, brightness)
        except Exception:
            pass

        ts = self._get_ts()
        if ts is None:
            self.sky_view.set_satellites_connected(False)
            return

        try:
            t = ts.from_datetime(
                datetime.fromtimestamp(t_unix, tz=timezone.utc))
        except Exception:
            self.sky_view.set_satellites_connected(False)
            return

        topo = self._topos()
        objs: List[SkyObject] = []
        connected = 0

        for name, info in DEFAULT_TLES.items():
            sat, kind = self._get_satellite(name)
            if sat is None:
                continue
            try:
                diff = sat - topo
                alt, az, dist = diff.at(t).altaz()
                alt_d, az_d = float(alt.degrees), float(az.degrees)
            except Exception:
                continue
            connected += 1
            color = ("#C4845C" if kind == "amateur" else "#5B7B8C")
            objs.append(SkyObject(
                name=name,
                azimuth_deg=az_d,
                elevation_deg=alt_d,
                obj_type="satellite",
                frequency_hz=float(info["freq_mhz"]) * 1e6,
                color=color,
                description=f"仰角{alt_d:.0f} 距离{float(dist.km):.0f}km",
            ))
            # 未来 10 分钟轨迹（每 60s 一点）—— Satellite.cpp:1298-1305 采样思想
            pts = []
            for k in range(0, 11):
                try:
                    tk = ts.from_datetime(
                        datetime.fromtimestamp(t_unix + k * 60, tz=timezone.utc))
                    a, e, _ = (sat - topo).at(tk).altaz()
                    pts.append((float(a.degrees), float(e.degrees)))
                except Exception:
                    continue
            if pts:
                self.sky_view.set_trajectory(name, pts)

        self.sky_view.set_objects(objs)
        self.sky_view.set_satellites_connected(connected > 0)

        # 未来过境列表（predict_upcoming_passes，best-effort）
        try:
            self._refresh_passes(ts, t_unix)
        except Exception:
            pass

    def _refresh_passes(self, ts, t_unix: float):
        """预测未来 24h 过境，喂入天空图侧栏。失败则清空列表（不造假）。"""
        try:
            from mbdsdr_ai import sat_passes
            gs = sat_passes.GroundStation(
                lat_deg=self.lat, lon_deg=self.lon, alt_m=self.alt_km * 1000.0)
            t0 = ts.from_datetime(
                datetime.fromtimestamp(t_unix, tz=timezone.utc))
            tle_list = [info["tle"] for info in DEFAULT_TLES.values()]
            results = sat_passes.predict_upcoming_passes(
                tle_list, gs, hours=24.0, min_alt=10.0)
            rows = []
            for r in results[:8]:
                p = r["pass"]
                dt = p.rise_time.utc_datetime()
                rows.append({
                    "name": p.sat_name,
                    "rise": dt.strftime("%H:%M"),
                    "max_alt": p.max_alt,
                    "duration_min": p.duration / 60.0,
                })
            self.sky_view.set_upcoming_passes(rows)
        except Exception:
            self.sky_view.set_upcoming_passes([])

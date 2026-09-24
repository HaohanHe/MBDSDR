"""
MBDSDR 射频天空视图 (RF Sky View)
====================================
借鉴 Stellarium 的天空视图概念，将其搬到 SDR 领域：
极坐标投影显示卫星位置、天线指向、信号源方向、干扰源方位。

核心功能：
- 极坐标投影（方位角 0-360° + 仰角 0-90° → 屏幕坐标）
- 卫星实时位置（基于 sgp4 轨道计算，支持 NOAA/ISS/风云等）
- 天线指向指示（基于 IMU 6DOF/9DOF 位姿融合）
- 信号强度热力图（方位-仰角-信号强度颜色渐变）
- 干扰源/信号源标注（用户或 AI 标记）
- 可交互：点击卫星显示详情、滚轮缩放、拖拽旋转、双击居中

设计风格：日式低饱和、半透明叠加、不喧宾夺主、稳定可读优先。
字体优先 MiSans，禁用 emoji。

MBDSDR Project - AI定义无线电 - 全开源 GPL-3.0 - 呼号 BI4MIB
"""

import math
import time
from typing import Optional, List, Dict, Tuple, Callable

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
    """天空中的对象（卫星/信号源/干扰源）。"""

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
        self.elevation_deg = elevation_deg  # 仰角 0-90°
        self.obj_type = obj_type  # satellite / signal / interferer / custom
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
# 射频天空视图主组件
# ============================================================================


class RFSkyView(QWidget):
    """
    射频天空视图：极坐标投影的天空图。

    借鉴 Stellarium 的设计理念：
    - 沉浸式主视图，天空占据大部分区域
    - 半透明叠加信息，不遮挡主视图
    - 底部简洁工具栏
    - 键盘快捷键驱动
    - 滚轮缩放，拖拽旋转

    信号：
        object_clicked(SkyObject) - 点击天空对象
        pointing_changed(float, float) - 天线指向改变 (az, el)
    """

    object_clicked = Signal(object)  # SkyObject
    pointing_changed = Signal(float, float)  # az, el

    def __init__(self, parent=None):
        super().__init__(parent)

        # 视图状态
        self._zoom = 1.0  # 缩放因子
        self._pan_x = 0.0  # 平移 x
        self._pan_y = 0.0  # 平移 y
        self._rotation = 0.0  # 视图旋转（度）
        self._show_grid = True
        self._show_compass = True
        self._show_heatmap = False
        self._show_trajectories = True

        # 数据
        self._objects: List[SkyObject] = []
        self._antenna = AntennaPointing()
        self._heatmap: List[HeatmapCell] = []
        self._trajectories: Dict[str, List[Tuple[float, float]]] = {}  # name -> [(az, el), ...]

        # 数据来源："none"=无观测站位置(空状态) / "real"=真实GNSS或手动配置 / "sim"=模拟数据
        # sim 模式右上角显示橙色“模拟数据”角标；none 时画布中央显示空状态提示。
        self._data_source: str = "none"

        # 观测站坐标（真实 GNSS 或手动配置）；未定位时为 None，不显示坐标
        self._observer: Optional[Dict[str, float]] = None

        # 新时空：授时信息
        self._time_info = {
            "utc_time": None,
            "gps_week": 0,
            "gps_tow": 0.0,
            "ntp_server": "",
            "ntp_status": "system",  # system/ntp/gnss
            "clock_offset_ms": 0.0,
        }

        # 交互状态
        self._dragging = False
        self._last_mouse_pos = QPointF()
        self._hovered_object: Optional[SkyObject] = None

        # 颜色（日式低饱和）
        self._colors = {
            "bg": QColor("#1A1D23"),
            "grid": QColor("#3A3F4A"),
            "grid_text": QColor("#8B90A0"),
            "horizon": QColor("#5A6070"),
            "zenith": QColor("#6B7280"),
            "satellite": QColor("#7EB8D4"),  # 低饱和蓝
            "signal": QColor("#D4A574"),  # 低饱和橙
            "interferer": QColor("#D47E7E"),  # 低饱和红
            "custom": QColor("#A5D47E"),  # 低饱和绿
            "antenna": QColor("#E8C547"),  # 天线指向（低饱和金）
            "antenna_beam": QColor(232, 197, 71, 40),  # 半透明波束
            "text": QColor("#D0D4DC"),
            "text_dim": QColor("#8B90A0"),
            "heatmap_low": QColor("#2A4A3A"),
            "heatmap_mid": QColor("#8A7A2A"),
            "heatmap_high": QColor("#8A3A3A"),
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

        # 启用鼠标追踪（悬停检测）
        self.setMouseTracking(True)
        self.setMinimumSize(400, 400)

        # 自动刷新定时器（30fps）
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
        """设置数据来源标注。

        source:
            "none" - 无观测站位置（GNSS 未连接或未手动配置），画布中央显示空状态提示；
            "real" - 真实 GNSS 定位或手动配置坐标，无角标；
            "sim"  - 模拟模式（合成坐标/信号），右上角显示橙色“模拟数据”角标。
        """
        if source not in ("none", "real", "sim"):
            source = "none"
        self._data_source = source
        self.update()

    def set_gnss_position(self, fix_dict: dict):
        """由真实串口 GNSS fix 驱动观测站坐标与数据来源标注。

        fix_dict 口径：{source, latitude, longitude, altitude_m, ...}
        - source=="real"：更新观测站坐标并设 source="real"（无角标）；
        - 其它（none/无坐标）：清空坐标并设 source="none"，画布显示空状态，
          绝不在无真实数据时显示坐标。
        （与真硬件联调：坐标变化后由上层 SatelliteTracker.set_location 刷新卫星，
          这里只负责记录观测站位置与来源标注。）
        """
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
        """返回当前观测站坐标 {lat,lon,alt_m}；未定位时 None。"""
        return self._observer

    def add_object(self, obj: SkyObject):
        """添加一个天空对象。"""
        self._objects.append(obj)
        self.update()

    def clear_objects(self):
        """清空所有天空对象。"""
        self._objects.clear()
        self.update()

    def set_antenna(self, antenna: AntennaPointing):
        """设置天线指向状态。"""
        self._antenna = antenna
        self.update()

    def get_antenna(self) -> AntennaPointing:
        return self._antenna

    def set_time_info(self, time_info: dict):
        """
        设置新时空授时信息。
        time_info 包含：utc_time, gps_week, gps_tow, ntp_server, ntp_status, clock_offset_ms
        """
        self._time_info.update(time_info)
        self.update()

    def set_heatmap(self, cells: List[HeatmapCell]):
        """设置信号强度热力图数据。"""
        self._heatmap = cells
        self._show_heatmap = len(cells) > 0
        self.update()

    def set_trajectory(self, name: str, points: List[Tuple[float, float]]):
        """设置卫星轨迹（方位角, 仰角）点列表。"""
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
        """重置视图（缩放/平移/旋转）。"""
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._rotation = 0.0
        self.update()

    # ========================================================================
    # 坐标转换
    # ========================================================================

    def _sky_to_screen(self, azimuth_deg: float, elevation_deg: float) -> QPointF:
        """
        天空坐标（方位角, 仰角）→ 屏幕坐标。

        极坐标投影：
        - 圆心 = 天顶（仰角 90°）
        - 半径与 (90 - 仰角) 成正比
        - 方位角 0° = 北（上方），顺时针增加
        """
        w = self.width()
        h = self.height()
        cx = w / 2 + self._pan_x
        cy = h / 2 + self._pan_y

        # 视图半径（取宽高较小值的 45%）
        radius = min(w, h) * 0.45 * self._zoom

        # 仰角 → 径向距离（仰角 90°=0，仰角 0°=radius）
        r = radius * (90.0 - max(0.0, min(90.0, elevation_deg))) / 90.0

        # 方位角 → 角度（北=0 在上方，顺时针）
        # Qt 角度：0°=右（东），逆时针为正
        # 我们需要：0°=上（北），顺时针为正
        angle_rad = math.radians(azimuth_deg - 90.0 + self._rotation)

        x = cx + r * math.cos(angle_rad)
        y = cy + r * math.sin(angle_rad)

        return QPointF(x, y)

    def _screen_to_sky(self, screen_x: float, screen_y: float) -> Tuple[float, float]:
        """
        屏幕坐标 → 天空坐标（方位角, 仰角）。
        用于点击/拖拽交互。
        """
        w = self.width()
        h = self.height()
        cx = w / 2 + self._pan_x
        cy = h / 2 + self._pan_y

        radius = min(w, h) * 0.45 * self._zoom

        dx = screen_x - cx
        dy = screen_y - cy
        r = math.sqrt(dx * dx + dy * dy)

        if r < 1:
            return 0.0, 90.0  # 天顶

        # 限制在视图半径内
        r = min(r, radius)

        # 仰角
        elevation = 90.0 - (r / radius) * 90.0

        # 方位角
        angle_rad = math.atan2(dy, dx)
        azimuth = math.degrees(angle_rad) + 90.0 - self._rotation
        azimuth = azimuth % 360.0

        return azimuth, elevation

    # ========================================================================
    # 绘制
    # ========================================================================

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        # 背景
        painter.fillRect(self.rect(), self._colors["bg"])

        # 热力图（在网格下方）
        if self._show_heatmap and self._heatmap:
            self._draw_heatmap(painter)

        # 网格
        if self._show_grid:
            self._draw_grid(painter)

        # 轨迹
        if self._show_trajectories:
            self._draw_trajectories(painter)

        # 天线波束
        self._draw_antenna_beam(painter)

        # 天空对象
        self._draw_objects(painter)

        # 天线指向标记
        self._draw_antenna_pointer(painter)

        # 罗盘
        if self._show_compass:
            self._draw_compass(painter)

        # 信息叠加（左下角）
        self._draw_info_overlay(painter)

        # 悬停提示
        if self._hovered_object:
            self._draw_hover_tooltip(painter)

        # 数据来源标注（空状态提示 / 模拟数据角标），最顶层绘制
        self._draw_data_source_overlay(painter)

        painter.end()

    def _draw_grid(self, painter: QPainter):
        """绘制极坐标网格（方位角线 + 仰角圈）。"""
        w = self.width()
        h = self.height()
        cx = w / 2 + self._pan_x
        cy = h / 2 + self._pan_y
        radius = min(w, h) * 0.45 * self._zoom

        painter.save()
        painter.setFont(self._font)

        # 仰角圈（0°, 30°, 60°, 90°）
        grid_pen = QPen(self._colors["grid"], 1, Qt.DashLine)
        painter.setPen(grid_pen)

        for elev in [0, 30, 60]:
            r = radius * (90.0 - elev) / 90.0
            painter.drawEllipse(QPointF(cx, cy), r, r)

            # 仰角标签（在左侧）
            label_pos = self._sky_to_screen(270, elev)
            painter.setPen(self._colors["grid_text"])
            painter.drawText(QPointF(label_pos.x() - 25, label_pos.y() + 4), f"{elev}°")
            painter.setPen(grid_pen)

        # 方位角线（每 30°）
        for az in range(0, 360, 30):
            start = self._sky_to_screen(az, 0)
            end = self._sky_to_screen(az, 90)
            painter.drawLine(start, end)

        # 地平线（加粗）
        horizon_pen = QPen(self._colors["horizon"], 2)
        painter.setPen(horizon_pen)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        painter.restore()

    def _draw_compass(self, painter: QPainter):
        """绘制罗盘方位标签（N/E/S/W + 度数）。"""
        painter.save()
        painter.setFont(self._font)

        # 主方位
        main_dirs = [
            (0, "N", 90),    # 北
            (90, "E", 90),   # 东
            (180, "S", 90),  # 南
            (270, "W", 90),  # 西
        ]

        for az, label, elev in main_dirs:
            pos = self._sky_to_screen(az, elev)
            painter.setPen(self._colors["text"])
            f = QFont(self._font)
            f.setBold(True)
            f.setPointSize(12)
            painter.setFont(f)
            painter.drawText(QPointF(pos.x() - 8, pos.y() + 5), label)
            painter.setFont(self._font)

        # 方位角刻度（每 30°）
        painter.setPen(self._colors["grid_text"])
        for az in range(0, 360, 30):
            if az % 90 == 0:
                continue  # 跳过主方位
            pos = self._sky_to_screen(az, 5)
            painter.drawText(QPointF(pos.x() - 12, pos.y() + 4), f"{az}°")

        painter.restore()

    def _draw_objects(self, painter: QPainter):
        """绘制天空对象（卫星/信号源/干扰源）。"""
        painter.save()

        for obj in self._objects:
            if not obj.visible or not obj.is_above_horizon():
                continue

            pos = self._sky_to_screen(obj.azimuth_deg, obj.elevation_deg)

            # 颜色
            color = QColor(obj.color) if obj.color else self._colors.get(
                obj.obj_type, self._colors["custom"]
            )

            # 绘制对象标记
            if obj.obj_type == "satellite":
                # 卫星：小菱形 + 名称
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

                # 名称
                painter.setPen(self._colors["text"])
                painter.setFont(self._font)
                painter.drawText(QPointF(pos.x() + 10, pos.y() - 5), obj.name)

                # 仰角/方位角（新时空标注）
                painter.setPen(self._colors["text_dim"])
                painter.setFont(self._mono_font)
                painter.drawText(
                    QPointF(pos.x() + 10, pos.y() + 22),
                    f"EL {obj.elevation_deg:4.1f}° AZ {obj.azimuth_deg:5.1f}°"
                )

                # 频率（如果有）
                if obj.frequency_hz > 0:
                    freq_mhz = obj.frequency_hz / 1e6
                    painter.setPen(self._colors["text_dim"])
                    painter.setFont(self._mono_font)
                    painter.drawText(
                        QPointF(pos.x() + 10, pos.y() + 36),
                        f"{freq_mhz:.1f} MHz"
                    )

            elif obj.obj_type == "signal":
                # 信号源：同心圆
                painter.setPen(QPen(color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(pos, 5, 5)
                painter.drawEllipse(pos, 9, 9)

                painter.setPen(self._colors["text"])
                painter.setFont(self._font)
                painter.drawText(QPointF(pos.x() + 12, pos.y() + 4), obj.name)

            elif obj.obj_type == "interferer":
                # 干扰源：三角形 + 警告标记
                size = 7
                triangle = QPolygonF([
                    QPointF(pos.x(), pos.y() - size),
                    QPointF(pos.x() + size, pos.y() + size),
                    QPointF(pos.x() - size, pos.y() + size),
                ])
                painter.setPen(QPen(color, 2))
                painter.setBrush(QBrush(color))
                painter.drawPolygon(triangle)

                painter.setPen(self._colors["text"])
                painter.setFont(self._font)
                painter.drawText(QPointF(pos.x() + 10, pos.y() - 5), obj.name)

            else:
                # 自定义：圆点
                painter.setPen(QPen(color, 2))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(pos, 5, 5)

                painter.setPen(self._colors["text"])
                painter.setFont(self._font)
                painter.drawText(QPointF(pos.x() + 10, pos.y() + 4), obj.name)

        painter.restore()

    def _draw_antenna_beam(self, painter: QPainter):
        """绘制天线波束覆盖区域。"""
        if self._antenna.elevation_deg <= 0:
            return

        painter.save()

        # 波束中心
        center = self._sky_to_screen(
            self._antenna.azimuth_deg,
            self._antenna.elevation_deg,
        )

        # 波束边缘（近似：在中心方位±半功率波束宽度，仰角±半功率波束宽度）
        half_bw = self._antenna.beamwidth_deg / 2.0

        # 绘制波束扇形（简化为椭圆区域）
        beam_color = self._colors["antenna_beam"]
        painter.setBrush(QBrush(beam_color))
        painter.setPen(Qt.NoPen)

        # 计算波束覆盖的屏幕区域（近似椭圆）
        # 取波束四个角点
        corners = [
            self._sky_to_screen(
                self._antenna.azimuth_deg - half_bw,
                self._antenna.elevation_deg + half_bw,
            ),
            self._sky_to_screen(
                self._antenna.azimuth_deg + half_bw,
                self._antenna.elevation_deg + half_bw,
            ),
            self._sky_to_screen(
                self._antenna.azimuth_deg + half_bw,
                max(0, self._antenna.elevation_deg - half_bw),
            ),
            self._sky_to_screen(
                self._antenna.azimuth_deg - half_bw,
                max(0, self._antenna.elevation_deg - half_bw),
            ),
        ]

        beam_path = QPainterPath()
        beam_path.moveTo(corners[0])
        for c in corners[1:]:
            beam_path.lineTo(c)
        beam_path.closeSubpath()

        painter.drawPath(beam_path)

        painter.restore()

    def _draw_antenna_pointer(self, painter: QPainter):
        """绘制天线指向标记（十字准星）。"""
        if self._antenna.elevation_deg <= 0:
            return

        pos = self._sky_to_screen(
            self._antenna.azimuth_deg,
            self._antenna.elevation_deg,
        )

        painter.save()
        painter.setPen(QPen(self._colors["antenna"], 2))

        # 十字准星
        size = 12
        painter.drawLine(QPointF(pos.x() - size, pos.y()), QPointF(pos.x() - 4, pos.y()))
        painter.drawLine(QPointF(pos.x() + 4, pos.y()), QPointF(pos.x() + size, pos.y()))
        painter.drawLine(QPointF(pos.x(), pos.y() - size), QPointF(pos.x(), pos.y() - 4))
        painter.drawLine(QPointF(pos.x(), pos.y() + 4), QPointF(pos.x(), pos.y() + size))

        # 中心点
        painter.setBrush(QBrush(self._colors["antenna"]))
        painter.drawEllipse(pos, 3, 3)

        # 跟踪状态标签
        if self._antenna.is_tracking and self._antenna.target_name:
            painter.setPen(self._colors["antenna"])
            painter.setFont(self._font)
            painter.drawText(
                QPointF(pos.x() + 16, pos.y() - 8),
                f"跟踪: {self._antenna.target_name}"
            )

        painter.restore()

    def _draw_trajectories(self, painter: QPainter):
        """绘制卫星轨迹。"""
        painter.save()

        for name, points in self._trajectories.items():
            if len(points) < 2:
                continue

            # 找到对应卫星的颜色
            color = self._colors["satellite"]
            for obj in self._objects:
                if obj.name == name:
                    color = QColor(obj.color) if obj.color else color
                    break

            pen = QPen(color, 1, Qt.DashLine)
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
        """绘制信号强度热力图。"""
        if not self._heatmap:
            return

        painter.save()
        painter.setPen(Qt.NoPen)

        for cell in self._heatmap:
            if cell.elevation_deg <= 0:
                continue

            pos = self._sky_to_screen(cell.azimuth_deg, cell.elevation_deg)

            # 信号强度 → 颜色（-100dB 低 → -30dB 高）
            norm = max(0.0, min(1.0, (cell.signal_db + 100) / 70))
            if norm < 0.5:
                t = norm * 2
                color = QColor(
                    int(self._colors["heatmap_low"].red() * (1 - t) + self._colors["heatmap_mid"].red() * t),
                    int(self._colors["heatmap_low"].green() * (1 - t) + self._colors["heatmap_mid"].green() * t),
                    int(self._colors["heatmap_low"].blue() * (1 - t) + self._colors["heatmap_mid"].blue() * t),
                    80,
                )
            else:
                t = (norm - 0.5) * 2
                color = QColor(
                    int(self._colors["heatmap_mid"].red() * (1 - t) + self._colors["heatmap_high"].red() * t),
                    int(self._colors["heatmap_mid"].green() * (1 - t) + self._colors["heatmap_high"].green() * t),
                    int(self._colors["heatmap_mid"].blue() * (1 - t) + self._colors["heatmap_high"].blue() * t),
                    80,
                )

            painter.setBrush(QBrush(color))
            painter.drawEllipse(pos, 15, 15)

        painter.restore()

    def _draw_info_overlay(self, painter: QPainter):
        """绘制左下角信息叠加（半透明卡片，含新时空授时信息）。"""
        painter.save()

        # 半透明背景（增大高度以容纳授时信息）
        card_w = 240
        card_h = 140
        card_x = 12
        card_y = self.height() - card_h - 12

        bg_color = QColor(26, 29, 35, 200)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(QColor(58, 63, 74), 1))
        painter.drawRoundedRect(card_x, card_y, card_w, card_h, 6, 6)

        # 文本
        painter.setPen(self._colors["text"])
        painter.setFont(self._font)

        y = card_y + 20
        painter.drawText(card_x + 12, y, "天线指向")
        painter.setFont(self._mono_font)
        painter.setPen(self._colors["antenna"])
        painter.drawText(
            card_x + 80, y,
            f"AZ {self._antenna.azimuth_deg:5.1f}°  EL {self._antenna.elevation_deg:4.1f}°"
        )

        y += 18
        painter.setFont(self._font)
        painter.setPen(self._colors["text"])
        painter.drawText(card_x + 12, y, "波束宽度")
        painter.setFont(self._mono_font)
        painter.setPen(self._colors["text_dim"])
        painter.drawText(card_x + 80, y, f"{self._antenna.beamwidth_deg:.0f}°  增益 {self._antenna.gain_dbi:.1f} dBi")

        y += 18
        painter.setFont(self._font)
        painter.setPen(self._colors["text"])
        painter.drawText(card_x + 12, y, "可见卫星")
        painter.setFont(self._mono_font)
        visible_count = sum(1 for o in self._objects if o.visible and o.is_above_horizon())
        painter.setPen(self._colors["satellite"])
        painter.drawText(card_x + 80, y, f"{visible_count} 颗")

        # 新时空：授时信息
        y += 22
        painter.setFont(self._font)
        painter.setPen(self._colors["text"])
        painter.drawText(card_x + 12, y, "授时")
        painter.setFont(self._mono_font)
        painter.setPen(self._colors["text_dim"])

        # UTC 时间
        from datetime import datetime, timezone
        utc_now = self._time_info.get("utc_time") or datetime.now(timezone.utc)
        if isinstance(utc_now, datetime):
            utc_str = utc_now.strftime("%H:%M:%S")
        else:
            utc_str = str(utc_now)
        ntp_status = self._time_info.get("ntp_status", "system")
        painter.drawText(card_x + 80, y, f"UTC {utc_str} [{ntp_status}]")

        y += 18
        painter.setFont(self._mono_font)
        painter.setPen(self._colors["text_dim"])
        gps_week = self._time_info.get("gps_week", 0)
        gps_tow = self._time_info.get("gps_tow", 0.0)
        if gps_week > 0:
            painter.drawText(card_x + 80, y, f"GPS W{gps_week} {gps_tow:06.1f}s")
        else:
            painter.drawText(card_x + 80, y, "GPS --:--:--")

        y += 18
        painter.setFont(self._font)
        painter.setPen(self._colors["text_dim"])
        painter.drawText(card_x + 12, y, "滚轮缩放 | 拖拽旋转 | 双击重置")

        painter.restore()

    def _draw_data_source_overlay(self, painter: QPainter):
        """绘制数据来源标注：无数据空状态提示 / 模拟数据角标。"""
        painter.save()

        if self._data_source == "none":
            # 空状态：画布中央提示“未连接 GNSS 或未配置观测站位置”
            f = QFont(self._font)
            f.setPointSize(11)
            painter.setFont(f)
            painter.setPen(self._colors["text_dim"])
            text = "无数据 — 未连接 GNSS 或未配置观测站位置"
            painter.drawText(QRectF(0, 0, self.width(), self.height()),
                             Qt.AlignCenter, text)
        elif self._data_source == "sim":
            # 模拟数据角标：右上角，日式低饱和橙 #C4845C
            badge_text = "模拟数据"
            f = QFont(self._font)
            f.setBold(True)
            f.setPointSize(9)
            painter.setFont(f)
            fm = QFontMetrics(f)
            pad_x, pad_y = 10, 5
            bw = fm.horizontalAdvance(badge_text) + pad_x * 2
            bh = fm.height() + pad_y * 2
            bx = self.width() - bw - 12
            by = 12
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#C4845C")))
            painter.drawRoundedRect(QRectF(bx, by, bw, bh), 4, 4)
            painter.setPen(QColor("#1A1D23"))
            painter.drawText(QRectF(bx, by, bw, bh), Qt.AlignCenter, badge_text)

        painter.restore()

    def _draw_hover_tooltip(self, painter: QPainter):
        """绘制悬停对象的详细提示。"""
        obj = self._hovered_object
        if not obj:
            return

        pos = self._sky_to_screen(obj.azimuth_deg, obj.elevation_deg)

        # 提示框
        lines = [
            obj.name,
            f"方位 {obj.azimuth_deg:.1f}°  仰角 {obj.elevation_deg:.1f}°",
        ]
        if obj.frequency_hz > 0:
            lines.append(f"频率 {obj.frequency_hz / 1e6:.1f} MHz")
        if obj.signal_strength_db != 0:
            lines.append(f"信号 {obj.signal_strength_db:.1f} dB")
        if obj.description:
            lines.append(obj.description[:30])

        # 计算框大小
        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        max_w = max(fm.horizontalAdvance(line) for line in lines)
        box_w = max_w + 20
        box_h = len(lines) * 18 + 12

        box_x = int(pos.x() + 15)
        box_y = int(pos.y() - box_h - 10)

        # 确保在窗口内
        if box_x + box_w > self.width():
            box_x = int(pos.x() - box_w - 15)
        if box_y < 0:
            box_y = int(pos.y() + 15)

        # 绘制
        bg_color = QColor(26, 29, 35, 230)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(QColor(100, 105, 120), 1))
        painter.drawRoundedRect(box_x, box_y, box_w, box_h, 4, 4)

        painter.setPen(self._colors["text"])
        y = box_y + 18
        for i, line in enumerate(lines):
            if i == 0:
                f = QFont(self._font)
                f.setBold(True)
                painter.setFont(f)
            else:
                painter.setFont(self._font)
            painter.drawText(box_x + 10, y, line)
            y += 18

    # ========================================================================
    # 交互事件
    # ========================================================================

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_mouse_pos = QPointF(event.position())

            # 检查是否点击了天空对象
            clicked = self._find_object_at(event.position().x(), event.position().y())
            if clicked:
                self.object_clicked.emit(clicked)

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()

        # 悬停检测
        hovered = self._find_object_at(pos.x(), pos.y())
        if hovered != self._hovered_object:
            self._hovered_object = hovered
            self.update()

        # 拖拽旋转
        if self._dragging:
            dx = pos.x() - self._last_mouse_pos.x()
            dy = pos.y() - self._last_mouse_pos.y()

            # 水平拖拽 → 旋转视图
            self._rotation += dx * 0.3
            self._rotation = self._rotation % 360

            # 垂直拖拽 → 平移
            self._pan_y += dy

            self._last_mouse_pos = QPointF(pos)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """双击重置视图。"""
        self.reset_view()

    def wheelEvent(self, event: QWheelEvent):
        """滚轮缩放。"""
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(3.0, self._zoom * 1.1)
        else:
            self._zoom = max(0.3, self._zoom / 1.1)
        self.update()

    def _find_object_at(self, x: float, y: float, tolerance: float = 12.0) -> Optional[SkyObject]:
        """找到指定屏幕坐标附近的天空对象。"""
        for obj in self._objects:
            if not obj.visible or not obj.is_above_horizon():
                continue
            pos = self._sky_to_screen(obj.azimuth_deg, obj.elevation_deg)
            dx = pos.x() - x
            dy = pos.y() - y
            if math.sqrt(dx * dx + dy * dy) < tolerance:
                return obj
        return None

    def resizeEvent(self, event: QResizeEvent):
        self.update()


# ============================================================================
# 天空视图面板（带标题栏和控制按钮）
# ============================================================================


class RFSkyViewPanel(QFrame):
    """
    射频天空视图面板：包含标题栏、控制按钮和 RFSkyView。

    借鉴 Stellarium 的 UI 设计：
    - 沉浸式主视图
    - 顶部简洁标题栏（半透明）
    - 底部控制按钮（半透明浮动）
    - 不喧宾夺主
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("skyViewPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 天空视图
        self.sky_view = RFSkyView()
        layout.addWidget(self.sky_view)

        # 底部浮动控制栏
        self._build_control_bar()

    def _build_control_bar(self):
        """构建底部浮动控制栏（时间控制 + 视图控制）。"""
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QLabel, QFrame
        from PySide6.QtCore import Qt

        control_bar = QFrame()
        control_bar.setObjectName("skyControlBar")
        control_bar.setFixedHeight(36)
        control_bar.setStyleSheet("""
            QFrame#skyControlBar {
                background: rgba(30, 40, 50, 200);
                border-top: 1px solid rgba(120, 150, 180, 80);
            }
            QPushButton {
                background: rgba(60, 80, 100, 150);
                color: #C8D8E8;
                border: 1px solid rgba(120, 150, 180, 100);
                border-radius: 3px;
                padding: 2px 8px;
                font-size: 9pt;
                min-width: 40px;
            }
            QPushButton:hover {
                background: rgba(80, 110, 140, 200);
            }
            QPushButton:checked {
                background: rgba(100, 140, 100, 200);
                color: #E0F0E0;
            }
            QLabel {
                color: #A8C0D8;
                font-size: 9pt;
                padding: 0 8px;
            }
        """)

        bar_layout = QHBoxLayout(control_bar)
        bar_layout.setContentsMargins(8, 2, 8, 2)
        bar_layout.setSpacing(4)

        # 时间控制
        self.time_label = QLabel("实时")
        self.time_label.setMinimumWidth(160)
        bar_layout.addWidget(self.time_label)

        bar_layout.addStretch()

        btn_rewind = QPushButton("<< -10m")
        btn_rewind.clicked.connect(lambda: self._adjust_time(-600))
        bar_layout.addWidget(btn_rewind)

        btn_pause = QPushButton("暂停")
        btn_pause.setCheckable(True)
        btn_pause.toggled.connect(self._toggle_pause)
        self._pause_btn = btn_pause
        bar_layout.addWidget(btn_pause)

        btn_live = QPushButton("实时")
        btn_live.clicked.connect(self._reset_time)
        bar_layout.addWidget(btn_live)

        btn_forward = QPushButton("+10m >>")
        btn_forward.clicked.connect(lambda: self._adjust_time(600))
        bar_layout.addWidget(btn_forward)

        bar_layout.addSpacing(16)

        # 视图控制
        btn_reset = QPushButton("重置视图")
        btn_reset.clicked.connect(self.reset_view)
        bar_layout.addWidget(btn_reset)

        # 插入到天空视图下方
        self.layout().addWidget(control_bar)

        # 时间状态
        self._time_offset = 0.0  # 秒
        self._paused = False
        self._pause_start_time = 0.0

        # 时间更新定时器
        self._time_timer = QTimer(self)
        self._time_timer.timeout.connect(self._update_time_label)
        self._time_timer.start(1000)

    def _adjust_time(self, seconds: float):
        """调整时间偏移（快进/快退）。"""
        self._time_offset += seconds
        self._paused = True
        self._pause_btn.setChecked(True)
        self._update_time_label()

    def _toggle_pause(self, paused: bool):
        """暂停/继续时间。"""
        self._paused = paused
        if paused:
            self._pause_start_time = time.time()
        else:
            # 恢复时，把暂停期间的时间加到偏移里
            paused_duration = time.time() - self._pause_start_time
            self._time_offset -= paused_duration
        self._update_time_label()

    def _reset_time(self):
        """重置到实时。"""
        self._time_offset = 0.0
        self._paused = False
        self._pause_btn.setChecked(False)
        self._update_time_label()

    def _update_time_label(self):
        """更新时间显示标签。"""
        if self._paused:
            sim_time = time.time() + self._time_offset
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sim_time))
            self.time_label.setText(f"模拟: {time_str} (暂停)")
        elif abs(self._time_offset) > 1:
            sim_time = time.time() + self._time_offset
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sim_time))
            offset_min = self._time_offset / 60
            sign = "+" if offset_min >= 0 else ""
            self.time_label.setText(f"模拟: {time_str} ({sign}{offset_min:.0f}m)")
        else:
            self.time_label.setText("实时")

    def get_sim_time(self) -> float:
        """获取当前模拟时间（epoch 秒）。"""
        if self._paused:
            return time.time() + self._time_offset
        return time.time() + self._time_offset

    # 代理方法
    def set_objects(self, objects):
        self.sky_view.set_objects(objects)

    def add_object(self, obj):
        self.sky_view.add_object(obj)

    def set_antenna(self, antenna):
        self.sky_view.set_antenna(antenna)

    def set_heatmap(self, cells):
        self.sky_view.set_heatmap(cells)

    def set_trajectory(self, name, points):
        self.sky_view.set_trajectory(name, points)

    def reset_view(self):
        self.sky_view.reset_view()


# ============================================================================
# 实时卫星跟踪桥：把真 sgp4 轨道数据接进天空图
# ============================================================================

class SatelliteTracker:
    """定时从 mbdsdr_ai.orbit 拉真 sgp4 卫星位置，更新天空图。

    无硬件依赖：TLE 在线拉取+缓存，纯算法。卫星在地平线下也画（半透明），
    便于看到过顶前后轨迹。
    """

    # 各卫星标称下行频率（MHz），用于标注
    _FREQ = {
        "NOAA 15": 137.620, "NOAA 18": 137.9125, "NOAA 19": 137.100,
        "ISS (ZARYA)": 145.800, "METEOR M2": 137.100, "FENGYUN 3D": 136.900,
    }

    def __init__(self, sky_view, lat: Optional[float], lon: Optional[float],
                 alt_km: float = 0.0, interval_ms: int = 10000):
        self.sky_view = sky_view
        self.lat = lat
        self.lon = lon
        self.alt_km = alt_km
        self._timer = QTimer(self.sky_view)
        self._timer.timeout.connect(self.refresh)
        # 观测站坐标为 None（未配置/GNSS 未定位）时不启动计算，
        # 清空卫星与轨迹，天空图由调用方置为“无数据”空状态。
        if lat is None or lon is None:
            self.lat = None
            self.lon = None
            self.sky_view.set_objects([])
            self.sky_view.clear_trajectories()
            return
        self._timer.start(interval_ms)
        self.refresh()

    def set_location(self, lat: Optional[float], lon: Optional[float],
                     alt_km: Optional[float] = None):
        """更新观测站坐标；传入 None 则停止卫星计算并清空天空图（空状态）。"""
        self.lat = lat
        self.lon = lon
        if alt_km is not None:
            self.alt_km = alt_km
        if lat is None or lon is None:
            self._timer.stop()
            self.sky_view.set_objects([])
            self.sky_view.clear_trajectories()
            self.sky_view.set_data_source("none")
            return
        if not self._timer.isActive():
            self._timer.start()
        self.refresh()

    def refresh(self):
        # 观测站坐标未知时不计算卫星位置
        if self.lat is None or self.lon is None:
            return
        try:
            from mbdsdr_ai import orbit
        except Exception:
            return
        try:
            objs = []
            traj = {}
            import time as _t
            now = _t.time()
            for name in orbit.BUILTIN_SATS:
                st = orbit.compute_satellite_state(name, self.lat, self.lon, self.alt_km)
                if st is None:
                    continue
                freq = self._FREQ.get(name, 0.0) * 1e6
                objs.append(SkyObject(
                    name=name,
                    azimuth_deg=st["azimuth"],
                    elevation_deg=max(0.0, st["elevation"]),
                    obj_type="satellite",
                    frequency_hz=freq,
                    description=f"仰角{st['elevation']:.0f}° 距离{st['range_km']:.0f}km",
                ))
                # 未来 10 分钟轨迹（每 60s 一点）
                pts = []
                for k in range(0, 11):
                    p = orbit.compute_satellite_state(name, self.lat, self.lon,
                                                       self.alt_km, when=now + k * 60)
                    if p:
                        pts.append((p["azimuth"], p["elevation"]))
                if pts:
                    traj[name] = pts
            self.sky_view.set_objects(objs)
            for name, pts in traj.items():
                self.sky_view.set_trajectory(name, pts)
        except Exception:
            # TLE 拉取/轨道计算失败（断网等）时保持上一帧，不崩溃
            pass

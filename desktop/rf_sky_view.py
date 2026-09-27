"""
MBDSDR 射频天空视图 (RF Sky View) — 对照 Stellarium 真实坐标数学
=====================================================================
方位角等距投影 (azimuthal equidistant) 显示卫星实时位置、太阳/月亮、
天线指向、未来过境列表与昼夜背景。

=== 坐标变换链（对照 Stellarium）===

Stellarium (StelCore.cpp:1072 updateTransformMatrices) 的天球变换链:

    J2000 (ICRS)
      --precession P (IAU 2006 P03)          StelCore.cpp:1081
      --nutation N  (IAU 2000B)
    -> 赤道坐标 (当前春分点/赤道, equinox-of-date)
      --Rz(GMST+lon) * Ry(90-lat)             StelObserver.cpp:229
    -> 地平坐标 (az/alt)
      --StelProjector (fisheye/az.eq.dist.)  StelProjectorClasses.cpp:363
    -> 屏幕像素

本模块卫星轨道计算使用 sgp4 标准传播（Vallado），坐标链为:

    sgp4 propagation -> TEME position/velocity (km, km/s)
      --Rz(-GMST)  [WGS-72 地球自转]           orbit.py:_gmst_days
    -> ECEF (地固坐标系)
      --ENU rotation [geodetic -> east/north/up]  orbit.py:geodetic_to_ecef
    -> 站心 ENU -> az/el/range/range_rate

此链与 Stellarium 链的对应关系：
  - TEME (True Equator Mean Equinox) ≈ Stellarium 的 equinox-of-date 赤道系。
    sgp4 直接输出在"当前赤道"下，因此不需要再做 P·N 岁差章动矩阵
    （Stellarium 对恒星/行星需要 P·N 从 J2000 转到 date，卫星不需要）。
  - Rz(GMST+lon)·Ry(90-lat) (Stellarium) ≡ R_ENU·Rz(-GMST) (本模块)。
    两者数学等价：前者从赤道系旋转到地平系，后者从 ECEF 旋转到站心 ENU。

简化项（对照 Stellarium，明确标注影响）:
  1. 极移 (polar motion): 忽略。影响 ~0.1-1 角秒，对 SDR 指向 (<0.1°) 可忽略。
  2. UT1-UTC 差值: 用 UTC 代替 UT1 计算 GMST。影响 <0.9 秒时间 = <0.004° 方位。
  3. 大气折射 (atmospheric refraction): 不施加。Stellarium 用
     RefractionExtinction.cpp 在地平线附近偏移 alt (白天 ~34' at horizon)。
     SDR 卫星过境预测通常忽略折射；低仰角 (<5°) 时显示 alt 比视觉值高 ~0.5°。
  4. 章动 (nutation): sgp4 TEME 已隐含在 date 赤道下，不再叠加 IAU2000B N 矩阵。
  5. WGS-72 vs WGS-84: sgp4 内核用 WGS-72 (a=6378.135km, f=1/298.26)，
     站心坐标也用 WGS-72，保持一致。WGS-84 差异 ~0.5m，对方位无影响。

投影数学（Stellarium StelProjectorClasses.cpp:363-395 fisheye）:
  forward:  h=sqrt(vx²+vy²); f=atan2(h,-vz)/h; x=vx*f, y=vy*f
  backward: a=sqrt(x²+y²);  f=sin(a)/a;       vz=-cos(a)
  实现在 mbdsdr_ai/celestial_geometry.py:AzimuthalEquidistantProjection。

交互模型（Stellarium StelMovementMgr）:
  - 拖拽:   dragView (反投影前后两点 -> panView)  sky_interaction.py
  - 滚轮:   handleMouseWheel (指数 FOV 缩放)      sky_interaction.py
  - 点选:   cleverFind (角距拾取)                  sky_interaction.py
  - 双击:   moveToAltAzi (goto 居中)              sky_interaction.py

时间统一来自 mbdsdr_ai.new_spacetime.get_time_engine()（时间穿梭真实驱动），
不使用系统墙钟做天文计算。

设计风格：低饱和米白纸面 —— 米白 #F5F3EF 纸面、蓝灰 #5B7B8C 主色、
橙 #C4845C 强调色、绿 #6BA89A 真实数据、红 #B85C5C 警告。
字体优先 MiSans，禁用 emoji。

MBDSDR Project - AI定义无线电 - 全开源 GPL-3.0
呼号由用户在状态栏设置（未设置时角标显“呼号：未设置”），不写死。
"""

import math
import os
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple, Set

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics,
    QPainterPath, QPolygonF,
    QLinearGradient,
    QMouseEvent, QWheelEvent, QResizeEvent,
)
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QLabel

# 交互控制复用 mbdsdr_ai/sky_interaction.py (Stellarium 视角模型独立重实现):
from mbdsdr_ai.celestial_geometry import (
    AzimuthalEquidistantProjection,
    PerspectiveProjection,
    GroundStation,
    vec_from_radec,
    altaz_to_j2000,
    j2000_to_altaz,
)
from mbdsdr_ai.sky_interaction import (
    ViewState,
    SkyInteractionHandler,
    sky_to_screen,
    screen_to_sky,
)
from mbdsdr_ai import skyengine
from mbdsdr_ai.skyengine import stars as _stars
from mbdsdr_ai.skyengine import satellites as _sats
from mbdsdr_ai.skyengine import jtime as _jtime

from tokens import tokens

# 触屏手势：双指捏合缩放 FOV + 单指拖动平移视角（Surface/平板）。
try:
    from touch_helpers import PinchZoomMixin
except Exception:  # pragma: no cover
    PinchZoomMixin = object  # type: ignore


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
    """热力图单元格（方位-仰角-信号强度）。

    注意：热力图仅在外部通过 set_heatmap() 喂入真实射频扫描数据时才绘制。
    本模块不生成任何合成热力图。
    """

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
    """从 ~/.mbdsdr/gui_config.json / config.json 与环境变量读取地面站坐标。

    优先级（后者覆盖前者）：
      1) gui_config.json: observer_lat / observer_lon
      2) config.json: ground_station_lat / ground_station_lon / ground_station_alt_m
      3) 环境变量: MBDSDR_GS_LAT / MBDSDR_GS_LON / MBDSDR_GS_ALT_M

    未配置或坐标非法时返回 None（调用方据此显示"地面站未设置"，不造假）。
    """
    lat = lon = alt = None

    # 1) gui_config.json (main_window.py 写入 observer_lat/lon)
    gui_cfg = os.path.expanduser("~/.mbdsdr/gui_config.json")
    try:
        if os.path.exists(gui_cfg):
            import json
            with open(gui_cfg, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            lat = cfg.get("observer_lat")
            lon = cfg.get("observer_lon")
    except Exception:
        pass

    # 2) config.json (通用地面站配置)
    cfg_path = os.path.expanduser("~/.mbdsdr/config.json")
    try:
        if os.path.exists(cfg_path):
            import json
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if lat is None:
                lat = cfg.get("ground_station_lat")
            if lon is None:
                lon = cfg.get("ground_station_lon")
            if alt is None:
                alt = cfg.get("ground_station_alt_m")
    except Exception:
        pass

    # 3) 环境变量覆盖
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
# TLE 加载（无硬编码 TLE 元素）
# ============================================================================


def _load_user_tle_catalog() -> List[Dict[str, object]]:
    """扫描 ~/.mbdsdr/tle/*.tle，返回用户提供的 TLE 列表。

    每个 .tle 文件为 3 行文本：
      第1行: 卫星名称（可含频率注释，如 "NOAA 15  137.62MHz"）
      第2行: TLE line 1 (以 "1 " 开头)
      第3行: TLE line 2 (以 "2 " 开头)

    返回: [{name, line1, line2, freq_mhz, kind}]
    无法解析的文件跳过。
    """
    catalog: List[Dict[str, object]] = []
    tle_dir = os.path.expanduser("~/.mbdsdr/tle")
    if not os.path.isdir(tle_dir):
        return catalog
    try:
        files = sorted(os.listdir(tle_dir))
    except OSError:
        return catalog
    for fn in files:
        if not fn.endswith(".tle"):
            continue
        try:
            with open(os.path.join(tle_dir, fn), "r", encoding="utf-8") as f:
                lines = [l.rstrip("\r\n") for l in f if l.strip()]
            if len(lines) < 3:
                continue
            name_line = lines[0].strip()
            line1 = lines[1].strip()
            line2 = lines[2].strip()
            if not (line1.startswith("1 ") and line2.startswith("2 ")):
                continue
            # 尝试从名称行解析频率（如 "NOAA 15  137.62MHz"）
            freq_mhz = 0.0
            kind = "custom"
            import re
            m = re.search(r"([\d.]+)\s*MHz", name_line, re.IGNORECASE)
            if m:
                try:
                    freq_mhz = float(m.group(1))
                except ValueError:
                    pass
            # 去掉频率注释，保留纯名称
            clean_name = re.sub(r"\s*[\d.]+\s*MHz.*$", "", name_line,
                               flags=re.IGNORECASE).strip()
            if not clean_name:
                clean_name = os.path.splitext(fn)[0]
            catalog.append({
                "name": clean_name,
                "line1": line1,
                "line2": line2,
                "freq_mhz": freq_mhz,
                "kind": kind,
            })
        except Exception:
            continue
    return catalog


def _load_celestrak_tle_catalog() -> List[Dict[str, object]]:
    """从 orbit.BUILTIN_SATS 目录 + Celestrak 缓存/在线获取 TLE。

    不硬编码 TLE 元素——只引用 NORAD CATNR，实际轨道数据通过
    orbit.fetch_tle() 从 Celestrak 下载或本地缓存 (~/.mbdsdr/tle_cache/) 读取。
    下载失败的卫星跳过（不造假位置）。
    """
    catalog: List[Dict[str, object]] = []
    try:
        from mbdsdr_ai import orbit
    except Exception:
        return catalog

    # 已知卫星元数据（名称 -> CATNR / 默认频率 / 类型）
    # 注意：这里只存目录索引，不存 TLE 元素本身。
    SATELLITE_META: Dict[str, Dict[str, object]] = {
        "NOAA 15":      {"catnr": 25338, "freq_mhz": 137.620, "kind": "weather"},
        "NOAA 18":      {"catnr": 28654, "freq_mhz": 137.9125, "kind": "weather"},
        "NOAA 19":      {"catnr": 33591, "freq_mhz": 137.100, "kind": "weather"},
        "ISS (ZARYA)":  {"catnr": 25544, "freq_mhz": 145.800, "kind": "amateur"},
        "METEOR M2":    {"catnr": 44016, "freq_mhz": 137.900, "kind": "weather"},
        "FENGYUN 3D":   {"catnr": 54234, "freq_mhz": 136.900, "kind": "weather"},
    }

    for name, meta in SATELLITE_META.items():
        catnr = int(meta["catnr"])
        try:
            line1, line2 = orbit.fetch_tle(catnr)
        except Exception:
            # Celestrak 不可用或缓存过期：跳过此卫星，不造假
            continue
        catalog.append({
            "name": name,
            "line1": line1,
            "line2": line2,
            "freq_mhz": float(meta["freq_mhz"]),
            "kind": str(meta["kind"]),
        })
    return catalog


# ============================================================================
# 射频天空视图主组件
# ============================================================================


class RFSkyView(PinchZoomMixin, QWidget, SkyInteractionHandler):
    """
    射频天空视图：方位角等距投影的天空图（天顶居中，地平线为边缘圆）。

    交互由混入类 SkyInteractionHandler 接管（Stellarium 视角模型）：
      - 左键拖拽：反投影平移视角（center_az/center_alt）
      - 滚轮：指数缩放 FOV（30..180°）
      - 左键点击：角距拾取天体
      - 双击：goto 居中到点击方向
    触屏（Surface/平板）：PinchZoomMixin 双指捏合缩放 FOV + 单指拖动平移，
    复用 SkyInteractionHandler 的 view_state.zoom/pan；鼠标模式不变。

    信号：
        object_clicked(SkyObject) - 点击天空对象
        pointing_changed(float, float) - 天线指向改变 (az, el)
    """

    object_clicked = Signal(object)  # SkyObject
    pointing_changed = Signal(float, float)  # az, el
    # 选中卫星后点「调到该星」：携带实时多普勒校正后的调谐参数。
    # dict: {name, tuned_freq_hz, doppler_shift_hz, base_freq_hz, mode}
    tune_satellite_requested = Signal(dict)

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

        # === SkyInteractionHandler 所需的宿主属性 ===
        # Stellarium StelProjector 透视投影 (pinhole camera); FOV 5..120°。
        self.view_state = ViewState(center_alt=90.0, fov_deg=120.0,
                                    min_fov=5.0, max_fov=120.0)
        self.projection = PerspectiveProjection()
        self.sky_objects = self._objects
        SkyInteractionHandler.__init__(self)

        # 触屏手势：双指捏合缩放 FOV + 单指拖动平移（PinchZoomMixin 兜底 object 时跳过）
        if PinchZoomMixin is not object and hasattr(self, "init_pinch_zoom"):
            self.init_pinch_zoom()
            try:
                self.pinch_scale_changed.connect(self._on_touch_pinch_zoom)
                self.pan_changed.connect(self._on_touch_pan)
            except Exception:
                pass

        # 图层开关 (Stellarium core.lines / landscapes / atmosphere)
        self._show_atmosphere: bool = True
        self._show_landscape: bool = True
        self._show_eqgrid: bool = False     # 赤道网格 (RA 时圈 + 赤纬圈)
        self._show_azgrid: bool = True      # 方位网格 (self._show_grid 同步)
        self._bortle: int = 3

        # 卫星目录索引 (SatelliteTracker 每帧喂入, 供信息卡/搜索)
        self._sat_catalog: Dict[str, Dict[str, object]] = {}
        # 当前选中天体的完整信息 (信息卡用)
        self._selected_info: Optional[Dict[str, object]] = None

        # 数据来源状态
        # "none" = 无观测站位置; "real" = 真实GNSS或手动配置
        self._data_source: str = "none"
        # 卫星轨道计算是否真正连通（TLE+sgp4 至少成功算出一颗）
        self._satellites_connected: bool = False
        # TLE 是否成功加载（区分"无TLE"和"TLE加载但无卫星过境"）
        self._tle_loaded: bool = False

        # 观测站坐标；未定位时为 None
        self._observer: Optional[Dict[str, float]] = None

        # 操作员呼号（由主窗口 set_callsign 从设置注入；空串 = 未设置）
        self._callsign: str = ""

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
        self._sun: Optional[Dict[str, float]] = None
        self._moon: Optional[Dict[str, float]] = None
        self._sky_brightness: float = 0.0

        # 未来过境列表
        self._upcoming_passes: List[Dict[str, float]] = []

        # === 「调到该星」覆盖按钮（自包含，不依赖 main_window）===
        # 选中带 TLE 的卫星且有观测站时才启用；否则置灰。
        # 按钮旁实时显示多普勒偏移(Hz)。
        from PySide6.QtWidgets import QPushButton
        self._tune_btn = QPushButton("📡 调到该星", self)
        self._tune_btn.setCheckable(False)
        self._tune_btn.setEnabled(False)
        self._tune_btn.setToolTip("按实时多普勒校正调谐到该卫星下行频率")
        self._tune_btn.clicked.connect(self._on_tune_satellite)
        self._doppler_label = QLabel("多普勒 --", self)
        _t = tokens()
        self._doppler_label.setStyleSheet(
            "QLabel { background: " + _t.COLORS["card_2"] + "; color: "
            + _t.text("secondary") + ";"
            " padding:2px 6px; border-radius:3px; font-size:9pt; }")
        self._tune_btn.setStyleSheet(
            "QPushButton { background: " + _t.COLORS["accent"] + "; color: "
            + _t.COLORS["bg_dark"] + ";"
            " border:none; padding:4px 10px; border-radius:3px; }"
            "QPushButton:hover { background: " + _t.COLORS["card_hover"] + "; }"
            "QPushButton:disabled { background: " + _t.COLORS["card_1"] + "; color: "
            + _t.text("disabled") + "; }")
        self._tune_btn.hide()
        self._doppler_label.hide()
        # 选中卫星名（None=未选卫星）；定时器刷新多普勒读数
        self._tune_sat_name: Optional[str] = None
        self._tune_refresh_timer = QTimer(self)
        self._tune_refresh_timer.setInterval(2000)
        self._tune_refresh_timer.timeout.connect(self._refresh_tune_doppler)

        # === 真实 GNSS 卫星天空图（NMEA GSV/GSA 驱动）===
        self._gnss_satellites: List[Dict] = []
        self._gnss_sats_used: Set[int] = set()
        self._gnss_connected: bool = False
        self._gnss_fix_type: int = 0
        self._gnss_colors: Dict[str, QColor] = {
            "GPS": QColor(_t.COLORS["accent"]),         # 蓝灰（token accent）
            "BeiDou": QColor(_t.COLORS["light_accent"]),  # 橙（星座标识色）
            "GLONASS": QColor(_t.COLORS["success"]),    # 柔和绿（token success）
            "Galileo": QColor(_t.COLORS["constellation_galileo"]),  # 柔和紫棕
            "未知": QColor(_t.COLORS["gray_300"]),      # 灰
        }

        # 交互状态
        self._dragging = False
        self._last_mouse_pos = QPointF()
        self._hovered_object: Optional[SkyObject] = None

        self._proj = self.projection

        # 颜色（CarWith dark_car 设计 token；天文天空渐变保留功能性配色）
        self._colors = {
            "paper": QColor(_t.COLORS["card_1"]),
            "day_zenith": QColor(_t.COLORS["sky_day_zenith"]),
            "day_horizon": QColor(_t.COLORS["light_bg"]),
            "night_zenith": QColor(_t.COLORS["sky_night_zenith"]),
            "night_horizon": QColor(_t.COLORS["sky_night_horizon"]),
            "primary": QColor(_t.COLORS["gray_200"]),
            "accent": QColor(_t.COLORS["accent"]),
            "good": QColor(_t.COLORS["success"]),        # 绿：真实数据
            "warning": QColor(_t.COLORS["danger"]),      # 红：警告/未连接
            "grid_day": QColor(88, 92, 99, 90),          # gray_400 带 alpha
            "grid_night": QColor(88, 92, 99, 140),
            "horizon_day": QColor(_t.COLORS["gray_300"]),
            "horizon_night": QColor(_t.COLORS["gray_200"]),
            "sat_weather": QColor(_t.COLORS["accent"]),
            "sat_amateur": QColor(_t.COLORS["light_accent"]),  # 橙：业余星标识
            "signal": QColor(_t.COLORS["accent"]),
            "interferer": QColor(_t.COLORS["danger"]),
            "custom": QColor(_t.COLORS["success"]),
            "sun": QColor(_t.COLORS["celestial_sun"]),    # 天文天体色
            "moon": QColor(_t.COLORS["celestial_moon"]),  # 天文天体色
            "antenna": QColor(_t.COLORS["accent"]),
            "antenna_beam": QColor(145, 156, 172, 36),   # accent #919cac 带 alpha
            "heatmap_low": QColor(_t.COLORS["heatmap_low"]),
            "heatmap_mid": QColor(_t.COLORS["heatmap_mid"]),
            "heatmap_high": QColor(_t.COLORS["heatmap_high"]),
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

        self._gnss_label_font = QFont(self._font)
        self._gnss_label_font.setPointSize(8)

        self.setMouseTracking(True)
        self.setMinimumSize(400, 400)

        # 搜索框 + 分类下拉 (自包含, 不依赖外部面板)
        self._build_search_widgets()

        # 自动刷新
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.update)
        self._refresh_timer.start(33)

    # ========================================================================
    # 自包含 UI: 搜索框 / 信息卡 / FOV 显示 (Stellarium 顶栏)
    # ========================================================================
    def _build_search_widgets(self):
        """顶部搜索框 + 分类结果下拉。"""
        from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem
        self._QLineEdit = QLineEdit
        self._QListWidget = QListWidget
        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("搜索天体: ISS / Vega / NOAA…")
        self._search_edit.setMinimumHeight(22)
        self._search_edit.setMaximumHeight(32)
        self._search_edit.textChanged.connect(self._on_search_text)
        self._search_popup = QListWidget(self)
        self._search_popup.setWindowFlags(Qt.Popup)
        self._search_popup.clicked.connect(self._on_search_picked)
        self._search_results: List[Dict[str, object]] = []

    def set_satellite_catalog(self, catalog: List[Dict[str, object]]):
        """SatelliteTracker 喂入当前 TLE 目录, 供信息卡/搜索分类。"""
        self._sat_catalog = {}
        for e in (catalog or []):
            name = str(e.get("name", ""))
            if not name:
                continue
            line1 = str(e.get("line1", ""))
            norad = _sats.norad_from_tle(line1)
            meta = _sats.SATELLITE_INDEX.get(norad) if norad else None
            self._sat_catalog[name] = {
                "norad": norad,
                "cospar": _sats.cospar_from_tle(line1,
                                                meta.cospar_override if meta else ""),
                "kind": meta.kind if meta else "satellite",
                "kind_label": meta.kind_label if meta else "Artificial Satellite",
                "aliases": list(meta.aliases) if meta else [name],
                "line1": line1,
                "line2": str(e.get("line2", "")),
                "freq_mhz": float(e.get("freq_mhz", 0.0)),
            }
        self.update()

    # ========================================================================
    # 公共 API
    # ========================================================================

    def set_objects(self, objects: List[SkyObject]):
        """设置天空对象列表。"""
        self._objects = objects
        self.sky_objects = objects
        self.update()

    def set_data_source(self, source: str):
        """设置数据来源标注："none" / "real"。"""
        if source not in ("none", "real"):
            source = "none"
        self._data_source = source
        self.update()

    def set_callsign(self, callsign: str):
        """设置操作员呼号（由主窗口从 desktop_settings.json 注入）。

        空串/空白 = 未设置，左下角信息卡显“呼号：未设置”。绝不写死呼号。
        """
        self._callsign = str(callsign or "").strip()
        self.update()

    def set_satellites_connected(self, connected: bool):
        """标记卫星轨道计算是否真正连通（TLE+sgp4 成功）。"""
        self._satellites_connected = bool(connected)
        self.update()

    def set_tle_loaded(self, loaded: bool):
        """标记 TLE 数据是否成功加载。"""
        self._tle_loaded = bool(loaded)
        self.update()

    def set_observer(self, lat: Optional[float], lon: Optional[float],
                     alt: Optional[float] = None):
        """直接设置观测站坐标（替代 GNSS/配置自动检测）。

        None/None 清除观测站，天空图回到"地面站未设置"空状态。
        """
        if lat is None or lon is None:
            self._observer = None
            self.set_data_source("none")
        else:
            self._observer = {
                "lat": float(lat),
                "lon": float(lon),
                "alt_m": float(alt) if alt is not None else 0.0,
            }
            if self._data_source == "none":
                self.set_data_source("real")
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

    # ------------------------------------------------------------------ #
    # 真实 GNSS 卫星天空图（NMEA GSV/GSA 驱动）
    # ------------------------------------------------------------------ #

    @staticmethod
    def _talker_to_constellation(talker: str, prn: int) -> str:
        """NMEA talker 前缀 -> 星座名。"""
        t = (talker or "").upper()
        if t == "GP":
            return "GPS"
        if t == "GL":
            return "GLONASS"
        if t == "GA":
            return "Galileo"
        if t in ("GB", "BD"):
            return "BeiDou"
        if t == "GN":
            try:
                p = int(prn)
            except (TypeError, ValueError):
                return "未知"
            if 65 <= p <= 96:
                return "GLONASS"
            return "未知"
        return "未知"

    def update_gnss_satellites(self, gsv_frames: List[Dict],
                               gsa_frame: Optional[Dict] = None):
        """入口：喂入最新 GSV（可多 talker）与可选 GSA，刷新天空图卫星层。

        无 GSV 数据时标记未连接、不画任何假卫星。
        """
        merged: Dict[Tuple[str, int], Dict] = {}
        for frame in (gsv_frames or []):
            talker = frame.get("talker", "")
            for sat in frame.get("sats", []) or []:
                prn = sat.get("id")
                if not isinstance(prn, int):
                    continue
                el = sat.get("elevation")
                az = sat.get("azimuth")
                if el is None or az is None:
                    continue
                cons = self._talker_to_constellation(talker, prn)
                key = (cons, prn)
                merged[key] = {
                    "prn": prn,
                    "talker": talker,
                    "constellation": cons,
                    "elevation": float(el),
                    "azimuth": float(az),
                    "snr_db": sat.get("snr_db"),
                }
        self._gnss_satellites = list(merged.values())

        self._gnss_sats_used = set()
        self._gnss_fix_type = 0
        if gsa_frame:
            try:
                self._gnss_sats_used = {
                    int(p) for p in (gsa_frame.get("satellites_used") or [])
                    if isinstance(p, int)
                }
            except (TypeError, ValueError):
                self._gnss_sats_used = set()
            try:
                self._gnss_fix_type = int(gsa_frame.get("fix_type") or 0)
            except (TypeError, ValueError):
                self._gnss_fix_type = 0

        self._gnss_connected = len(self._gnss_satellites) > 0
        self.update()

    def add_object(self, obj: SkyObject):
        self._objects.append(obj)
        self.sky_objects = self._objects
        self.update()

    def clear_objects(self):
        self._objects.clear()
        self.sky_objects = self._objects
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
        """设置热力图单元格。仅当外部喂入真实射频扫描数据时才调用。"""
        self._heatmap = cells
        self._show_heatmap = len(cells) > 0
        self.update()

    def set_trajectory(self, name: str, points: List[Tuple[float, float]]):
        self._trajectories[name] = points
        self.update()

    def clear_trajectories(self):
        self._trajectories.clear()
        self.update()

    def clear(self):
        """清空所有天空图数据（卫星/轨迹/热力图/天体/GNSS），保留视图设置。

        这是外部重置天空图的标准入口。清空后天空图回到空状态提示。
        """
        self._objects.clear()
        self.sky_objects = self._objects
        self._trajectories.clear()
        self._heatmap.clear()
        self._show_heatmap = False
        self._sun = None
        self._moon = None
        self._sky_brightness = 0.0
        self._upcoming_passes.clear()
        self._gnss_satellites.clear()
        self._gnss_sats_used.clear()
        self._gnss_connected = False
        self._gnss_fix_type = 0
        self._hovered_object = None
        self._observer = None
        self._data_source = "none"
        self._satellites_connected = False
        self._tle_loaded = False
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
        """重置视角：回到天顶居中 + FOV=120°。"""
        self.view_state.look_at(0.0, 90.0)
        self.view_state.zoom_to(120.0)
        self._rotation = 0.0
        self.update()

    # ========================================================================
    # 时间 / 坐标辅助 (Stellarium observer.tt 儒略日内核)
    # ========================================================================
    def _current_jd(self) -> float:
        """当前儒略日 (UTC)。优先 new_spacetime 时间引擎 (时间穿梭), 否则墙钟。"""
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            eng = get_time_engine()
            eng.tick()
            return _jtime.unix_to_jd(eng.now_unix())
        except Exception:
            return _jtime.unix_to_jd(time.time())

    def _ground_station(self) -> Optional[GroundStation]:
        if self._observer is None:
            return None
        return GroundStation(self._observer["lon"], self._observer["lat"],
                             self._observer.get("alt_m", 0.0))

    # ------------------------------------------------------------------ #
    # 搜索 (分类: 空间站 / 人造卫星 / 恒星, 图标区分)
    # ------------------------------------------------------------------ #
    def _on_search_text(self, text: str):
        text = (text or "").strip().lower()
        self._search_results = []
        if not text:
            self._search_popup.hide()
            return
        # 1) 卫星: 按名称 + 别名 + NORAD 号匹配
        for name, info in self._sat_catalog.items():
            hay = (name + " " + " ".join(info.get("aliases", [])) + " "
                   + str(info.get("norad") or "")).lower()
            if text in hay:
                self._search_results.append({
                    "kind": info["kind"], "kind_label": info["kind_label"],
                    "label": name, "sub": info["kind_label"],
                    "az": None, "alt": None, "obj_name": name,
                })
        # 2) 恒星: 亮星星表按名匹配
        for s in _stars.BRIGHT_STARS:
            if text in s.name.lower():
                self._search_results.append({
                    "kind": "star", "kind_label": "Long-Period Variable Star",
                    "label": s.name, "sub": "Variable / Long-Period Variable Star",
                    "ra": s.ra_deg, "dec": s.dec_deg, "vmag": s.vmag,
                })
        # 填充下拉
        self._search_popup.clear()
        for r in self._search_results[:40]:
            icon = {"space_station": "🛰", "satellite": "●",
                    "weather": "◉", "star": "✦"}.get(r["kind"], "•")
            item = self._QListWidgetItem(f"{icon}  {r['label']}   —   {r['sub']}")
            self._search_popup.addItem(item)
        if self._search_results:
            # 下拉弹窗跟随搜索框宽度（弹性，不写死像素）
            self._search_popup.setMinimumWidth(self._search_edit.width())
            self._search_popup.setMaximumWidth(max(self._search_edit.width(), 200))
            pos = self._search_edit.mapTo(self, self._search_edit.rect().bottomLeft())
            self._search_popup.move(pos)
            self._search_popup.show()

    def _on_search_picked(self, item):
        row = self._search_popup.currentRow()
        if row < 0 or row >= len(self._search_results):
            return
        r = self._search_results[row]
        self._search_popup.hide()
        # 卫星 -> 在 objects 里找并选中居中
        if r["kind"] in ("space_station", "satellite", "weather", "amateur"):
            for obj in self._objects:
                if obj.name == r["obj_name"]:
                    self._hovered_object = obj
                    self._build_selected_info(obj)
                    self.view_state.look_at(obj.azimuth_deg, obj.elevation_deg)
                    self.update()
                    return
        # 恒星 -> 居中到该星 (无需选中信息卡)
        if r["kind"] == "star" and self._ground_station() is not None:
            jd = self._current_jd()
            gs = self._ground_station()
            v = j2000_to_altaz(vec_from_radec(r["ra"], r["dec"]), gs, jd)
            from mbdsdr_ai.celestial_geometry import azalt_from_vec
            az, alt = azalt_from_vec(v)
            self.view_state.look_at(az, alt)
            self.update()

    # ------------------------------------------------------------------ #
    # 选中信息卡 (Stellarium 规格 10.4)
    # ------------------------------------------------------------------ #
    def _build_selected_info(self, obj: SkyObject) -> None:
        """根据选中卫星构建信息卡字段: NORAD/COSPAR/别名/Dist/RaDec/AzAlt/
        Magnitude(占位)/Visibility(Rise/Set)。无值给占位, 不编造。"""
        info: Dict[str, object] = {
            "title": obj.name, "kind_label": "Satellite",
            "norad": None, "cospar": "", "aliases": [],
            "dist_km": self._parse_distance_km(obj),
            "az": obj.azimuth_deg, "alt": obj.elevation_deg,
            "ra": None, "dec": None, "mag": None,
            "rise": None, "set": None,
        }
        meta = self._sat_catalog.get(obj.name)
        if meta:
            info["norad"] = meta["norad"]
            info["cospar"] = meta["cospar"]
            info["aliases"] = meta["aliases"]
            info["kind_label"] = meta["kind_label"]
        # Ra/Dec: 地平 -> 当前赤道 -> J2000
        gs = self._ground_station()
        if gs is not None:
            try:
                jd = self._current_jd()
                from mbdsdr_ai.celestial_geometry import vec_from_azalt, radec_from_vec
                v = vec_from_azalt(obj.azimuth_deg, obj.elevation_deg)
                vj = altaz_to_j2000(v, gs, jd)
                ra, dec = radec_from_vec(vj)
                info["ra"], info["dec"] = ra, dec
            except Exception:
                pass
            # Visibility: 未来 24h 升落
            if meta and meta.get("line1"):
                try:
                    from sgp4.api import Satrec
                    sat = Satrec.twoline2rv(meta["line1"], meta["line2"])
                    alt_km = (self._observer.get("alt_m", 0.0)) / 1000.0
                    rs = _sats.next_rise_set(sat, self._observer["lat"],
                                             self._observer["lon"], alt_km, jd)
                    info["rise"] = rs["rise_jd"]
                    info["set"] = rs["set_jd"]
                except Exception:
                    pass
        self._selected_info = info
        # 选中后刷新「调到该星」按钮可用性（无 TLE/无观测站则置灰）
        self._update_tune_button(obj)

    # ------------------------------------------------------------------ #
    # 「调到该星」：实时多普勒校正调谐（自包含，不依赖 main_window）
    # ------------------------------------------------------------------ #
    def _selected_sat_tle(self) -> Optional[Dict[str, object]]:
        """返回当前选中卫星的 TLE 目录条目（含 line1/line2），否则 None。"""
        info = self._selected_info or {}
        name = info.get("title")
        if not name:
            return None
        meta = self._sat_catalog.get(name)
        if not meta or not meta.get("line1") or not meta.get("line2"):
            return None
        return meta

    def _update_tune_button(self, obj=None):
        """根据选中对象/观测站/TLE 可用性更新按钮与多普勒读数。"""
        meta = self._selected_sat_tle()
        gs_ready = self._observer is not None
        if meta is None or not gs_ready:
            self._tune_sat_name = None
            self._tune_btn.hide()
            self._doppler_label.hide()
            self._tune_refresh_timer.stop()
            return
        name = (self._selected_info or {}).get("title")
        self._tune_sat_name = name
        self._tune_btn.show()
        self._doppler_label.show()
        self._tune_btn.setEnabled(True)
        self._refresh_tune_doppler()
        self._tune_refresh_timer.start()
        self._position_tune_overlay()

    def _build_tune_tracker(self, meta: Dict[str, object]):
        """用目录里的 TLE 构建卫星追踪器；缺依赖/失败返回 None。"""
        try:
            from mbdsdr_ai.satellite.tracker import SatelliteTracker
            return SatelliteTracker(
                str(meta.get("name") or (self._selected_info or {}).get("title")
                    or "SAT"),
                str(meta["line1"]), str(meta["line2"]),
                norad=int(meta.get("norad") or 0))
        except Exception:
            return None

    def _compute_tune(self):
        """计算当前选中卫星的实时调谐参数；失败返回 None。"""
        meta = self._selected_sat_tle()
        if meta is None or self._observer is None:
            return None
        tr = self._build_tune_tracker(meta)
        if tr is None:
            return None
        try:
            from mbdsdr_ai.satellite.tracker import (
                GroundStation, tune_command, DOWNLINK_FREQUENCIES)
        except Exception:
            return None
        gs = GroundStation(float(self._observer["lat"]),
                           float(self._observer["lon"]),
                           float(self._observer.get("alt_m", 0.0)) / 1000.0)
        try:
            res = tune_command(tr, gs)
        except Exception:
            return None
        if not res or "error" in res:
            return None
        # 下行频率已知的卫星给一个默认模式（NOAA APT=WFM 式接收）
        f_down = res.get("base_freq_hz", 0.0)
        mode = "WFM" if f_down and f_down < 200e6 else "NFM"
        res["mode"] = mode
        return res

    def _refresh_tune_doppler(self):
        """刷新按钮旁的多普勒偏移读数（不实际调谐，只显示）。"""
        if self._tune_sat_name is None:
            return
        res = self._compute_tune()
        if res is None:
            self._doppler_label.setText("多普勒 --")
            return
        dop = res.get("doppler_shift_hz", 0.0)
        sign = "+" if dop >= 0 else "−"
        self._doppler_label.setText(f"多普勒 {sign}{abs(dop):.0f} Hz")

    def _on_tune_satellite(self):
        """点击 → 算实时多普勒 → 发 tune_satellite_requested 给主窗口调谐。"""
        res = self._compute_tune()
        if res is None:
            return
        self.tune_satellite_requested.emit({
            "name": res.get("satellite", self._tune_sat_name),
            "tuned_freq_hz": float(res["tuned_freq_hz"]),
            "doppler_shift_hz": float(res.get("doppler_shift_hz", 0.0)),
            "base_freq_hz": float(res.get("base_freq_hz", 0.0)),
            "mode": res.get("mode", "NFM"),
        })

    def _position_tune_overlay(self):
        """把调谐按钮+多普勒标签放到左下角。"""
        try:
            bw = max(self._tune_btn.sizeHint().width(), 110)
            dw = self._doppler_label.sizeHint().width()
            h = 26
            x = 12
            y = self.height() - h - 12
            self._tune_btn.setGeometry(x, y, bw, h)
            self._doppler_label.setGeometry(x + bw + 6, y, max(dw, 90), h)
        except Exception:
            pass

    # ========================================================================
    # 坐标转换：统一委托给 sky_interaction.sky_to_screen / screen_to_sky
    # ========================================================================

    def _widget_size_tuple(self) -> Tuple[int, int]:
        return int(self.width()), int(self.height())

    def _sky_center(self) -> Tuple[float, float]:
        return self.width() / 2.0, self.height() / 2.0

    def _sky_disk_radius(self) -> float:
        return min(self.width(), self.height()) / 2.0

    def _sky_to_screen(self, azimuth_deg: float, elevation_deg: float) -> QPointF:
        """(az, alt) -> 屏幕像素。委托 sky_interaction.sky_to_screen。"""
        px, py = sky_to_screen(azimuth_deg, elevation_deg,
                               self.view_state, self.projection,
                               self._widget_size_tuple())
        if not (math.isfinite(px) and math.isfinite(py)):
            cx, cy = self._sky_center()
            return QPointF(cx, cy)
        return QPointF(px, py)

    def _screen_to_sky(self, screen_x: float, screen_y: float) -> Tuple[float, float]:
        """屏幕像素 -> (az, alt)。委托 sky_interaction.screen_to_sky。"""
        az, alt = screen_to_sky(screen_x, screen_y,
                                self.view_state, self.projection,
                                self._widget_size_tuple())
        return az % 360.0, alt

    def _gnss_az_el_to_screen(self, az_deg: float, el_deg: float) -> QPointF:
        """兼容旧接口：(az, el) -> 屏幕像素（固定经典等距极坐标）。

        语义与重构前一致：天顶(el=90)映射到圆心，地平(el=0)映射到圆盘边缘，
        径向距离随仰角线性变化；az=0 指北(屏幕上方)、az=90 朝东(屏幕右方)。

        说明：本部件的交互视角由 view_state.fov_deg=120° 控制（被
        test_sky_interaction_offscreen 锁定），此时地平会落在圆盘之外。
        GNSS 卫星实际绘制统一走 _sky_to_screen（支持 pan/zoom，与其他
        卫星视角一致，见 _draw_gnss_satellites）；本方法只是固定极坐标参考，
        供回归测试 tests/test_rf_sky_view_gnss.py 与旧调用方使用，不随
        pan/zoom 移动。
        """
        cx, cy = self._sky_center()
        r_max = self._sky_disk_radius()
        el = max(-90.0, min(90.0, float(el_deg)))
        az = math.radians(float(az_deg) % 360.0)
        radius = (90.0 - el) / 90.0 * r_max
        return QPointF(cx + radius * math.sin(az),
                       cy - radius * math.cos(az))

    # ========================================================================
    # 颜色工具
    # ========================================================================

    def _ink(self) -> QColor:
        """根据昼夜亮度返回前景文字/网格色。"""
        b = self._sky_brightness
        _t = tokens()
        night = QColor(_t.COLORS["gray_100"])
        day = QColor(_t.COLORS["sky_day_ink"])
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

        # 1) 天空背景 (透视窗口, 全屏) + 近地平线大气渐变
        self._paint_sky_background(painter)

        if self._observer is not None:
            # 2) 恒星 (分级星点 + 亮星标签)
            self._draw_stars(painter)
            # 3) 地景剪影
            if self._show_landscape:
                self._draw_landscape(painter)
            # 4) 网格: 方位 / 赤道 (独立开关)
            if self._show_grid and self._show_azgrid:
                self._draw_az_grid(painter)
            if self._show_eqgrid:
                self._draw_eq_grid(painter)
            if self._show_trajectories:
                self._draw_trajectories(painter)
            # 5) 天体/卫星层
            self._draw_antenna_beam(painter)
            self._draw_celestial_bodies(painter)
            self._draw_objects(painter)
            self._draw_antenna_pointer(painter)
            self._draw_gnss_satellites(painter)
            if self._show_compass:
                self._draw_compass(painter)
            if self._hovered_object:
                self._draw_picked_info(painter)

        # 6) 常驻叠加: FOV 显示 / 信息卡 / 过境 / GNSS / 空状态
        self._draw_fov(painter)
        self._draw_info_overlay(painter)
        self._draw_passes_panel(painter)
        self._draw_gnss_overlay(painter)
        self._draw_data_source_overlay(painter)

        painter.end()

    # ------------------------------------------------------------------ #
    # 天空背景: 透视窗口全屏, 近地平线大气散射渐变 (Preetham 简化)
    # ------------------------------------------------------------------ #
    def _paint_sky_background(self, painter: QPainter):
        b = self._sky_brightness

        def mix(c1: QColor, c2: QColor, t: float) -> QColor:
            return QColor(
                int(c1.red() * (1 - t) + c2.red() * t),
                int(c1.green() * (1 - t) + c2.green() * t),
                int(c1.blue() * (1 - t) + c2.blue() * t),
            )

        # 夜: 天顶深蓝 -> 地平线微亮; 昼: 浅蓝 -> 米白
        _t = tokens()
        zenith = mix(QColor(_t.COLORS["sky_night_zenith_deep"]),
                     QColor(_t.COLORS["sky_day_horizon_glow"]), b)
        horizon = mix(QColor(_t.COLORS["sky_night_horizon_deep"]),
                      QColor(_t.COLORS["sky_day_zenith"]), b)
        painter.save()
        painter.setPen(Qt.NoPen)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, zenith)
        grad.setColorAt(0.62, mix(zenith, horizon, 0.5))
        grad.setColorAt(1.0, horizon)
        painter.setBrush(QBrush(grad))
        painter.drawRect(self.rect())
        painter.restore()

    # ------------------------------------------------------------------ #
    # 恒星: J2000 -> 地平 -> 透视投影, 分级星点 + 亮星标签
    # ------------------------------------------------------------------ #
    def _draw_stars(self, painter: QPainter):
        gs = self._ground_station()
        if gs is None:
            return
        jd = self._current_jd()
        painter.save()
        painter.setFont(self._font)
        for s in _stars.BRIGHT_STARS:
            try:
                v = j2000_to_altaz(vec_from_radec(s.ra_deg, s.dec_deg), gs, jd)
                from mbdsdr_ai.celestial_geometry import azalt_from_vec
                az, alt = azalt_from_vec(v)
            except Exception:
                continue
            if alt < -1.0:
                continue
            pos = self._sky_to_screen(az, alt)
            if not self._on_screen(pos):
                continue
            r, bright = _stars.star_draw(s.vmag, self._bortle)
            if r <= 0:
                continue
            cr, cg, cb = _stars.bv_to_rgb(s.bv)
            alpha = int(255 * (0.35 + 0.65 * bright))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(cr, cg, cb, alpha)))
            painter.drawEllipse(pos, r, r)
            # 亮星 (V<2.0) 文字标签
            if s.vmag <= 2.0:
                painter.setPen(QColor(210, 220, 235, int(200 * bright + 40)))
                painter.drawText(QPointF(pos.x() + r + 2, pos.y() - r), s.name)
        painter.restore()

    def _on_screen(self, pos: QPointF, margin: float = 4.0) -> bool:
        return (-margin <= pos.x() <= self.width() + margin
                and -margin <= pos.y() <= self.height() + margin)

    # ------------------------------------------------------------------ #
    # 地景剪影 (底部地平线山峦轮廓, 可开关)
    # ------------------------------------------------------------------ #
    def _draw_landscape(self, painter: QPainter):
        # 采样地平线 (alt=0) 各方位, 连成剪影轮廓; 低于地平线涂黑。
        pts = []
        for az in range(0, 361, 6):
            pos = self._sky_to_screen(float(az), 0.0)
            pts.append(pos)
        if not pts:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        _t = tokens()
        col = (QColor(_t.COLORS["ground_shadow_night"])
               if self._sky_brightness < 0.5
               else QColor(_t.COLORS["ground_shadow_day"]))
        painter.setBrush(QBrush(col))
        path = QPainterPath()
        # 取地平线附近 y 的代表位置做轮廓 (透视下地平线在画面中下部)
        on = [p for p in pts if self._on_screen(p)]
        if not on:
            painter.restore()
            return
        ys = sorted(p.y() for p in on)
        horizon_y = ys[len(ys) // 2]
        path.moveTo(0.0, self.height())
        path.lineTo(0.0, horizon_y)
        for p in pts:
            if self._on_screen(p):
                path.lineTo(p.x(), p.y())
        path.lineTo(self.width(), self.height())
        path.closeSubpath()
        painter.drawPath(path)
        painter.restore()

    # ------------------------------------------------------------------ #
    # 方位网格 (等方位射线 + 等高圈), lines.c azimuthal
    # ------------------------------------------------------------------ #
    def _draw_az_grid(self, painter: QPainter):
        painter.save()
        grid_col = QColor(88, 92, 99, 90)  # gray_400 带 alpha
        painter.setPen(QPen(grid_col, 1, Qt.DashLine))
        # 等方位射线 (每 30°), 从地平 alt=0 向天顶 alt=90
        for az in range(0, 360, 30):
            path = QPainterPath()
            first = True
            for k in range(0, 21):
                alt = k * 4.5
                p = self._sky_to_screen(float(az), alt)
                if not self._on_screen(p):
                    first = True
                    continue
                if first:
                    path.moveTo(p); first = False
                else:
                    path.lineTo(p)
            painter.drawPath(path)
        # 等高圈 (alt = 0,30,60)
        for alt in (0.0, 30.0, 60.0):
            path = QPainterPath()
            first = True
            for az in range(0, 361, 4):
                p = self._sky_to_screen(float(az), alt)
                if not self._on_screen(p):
                    first = True
                    continue
                if first:
                    path.moveTo(p); first = False
                else:
                    path.lineTo(p)
            painter.drawPath(path)
        painter.restore()

    # ------------------------------------------------------------------ #
    # 赤道网格 (RA 时圈 + 赤纬圈), lines.c equatorial
    # ------------------------------------------------------------------ #
    def _draw_eq_grid(self, painter: QPainter):
        gs = self._ground_station()
        if gs is None:
            return
        jd = self._current_jd()
        painter.save()
        grid_col = QColor(88, 92, 99, 70)  # gray_400 带 alpha（赤道网格）
        painter.setPen(QPen(grid_col, 1, Qt.DotLine))
        # 赤纬圈: 每 30°, RA 扫一圈
        for dec in range(-60, 91, 30):
            path = QPainterPath()
            first = True
            for rah in range(0, 25):  # 0..24h
                ra = rah * 15.0
                try:
                    v = j2000_to_altaz(vec_from_radec(float(ra), float(dec)), gs, jd)
                    from mbdsdr_ai.celestial_geometry import azalt_from_vec
                    az, alt = azalt_from_vec(v)
                except Exception:
                    continue
                p = self._sky_to_screen(az, alt)
                if not self._on_screen(p):
                    first = True
                    continue
                if first:
                    path.moveTo(p); first = False
                else:
                    path.lineTo(p)
            painter.drawPath(path)
        # RA 时圈: 每 2h, dec 扫一圈
        for rah in range(0, 24, 2):
            ra = rah * 15.0
            path = QPainterPath()
            first = True
            for dec in range(-80, 81, 10):
                try:
                    v = j2000_to_altaz(vec_from_radec(float(ra), float(dec)), gs, jd)
                    from mbdsdr_ai.celestial_geometry import azalt_from_vec
                    az, alt = azalt_from_vec(v)
                except Exception:
                    continue
                p = self._sky_to_screen(az, alt)
                if not self._on_screen(p):
                    first = True
                    continue
                if first:
                    path.moveTo(p); first = False
                else:
                    path.lineTo(p)
            painter.drawPath(path)
        painter.restore()

    # ------------------------------------------------------------------ #
    # FOV 常驻显示 (右上角 "FOV xx.x°")
    # ------------------------------------------------------------------ #
    def _draw_fov(self, painter: QPainter):
        painter.save()
        _t = tokens()
        f = QFont(self._mono_font)
        f.setPointSize(9)
        painter.setFont(f)
        painter.setPen(QColor(_t.COLORS["gray_100"]))
        painter.drawText(self.width() - 92, 18,
                         f"FOV {self.view_state.fov_deg:4.1f}°")
        painter.restore()

    def _draw_grid(self, painter: QPainter):
        """兼容旧接口: 方位网格已由 _draw_az_grid (透视) 实现。"""
        self._draw_az_grid(painter)

    def _draw_compass(self, painter: QPainter):
        """方位罗盘: N/E/S/W 标在方位圈 (alt=0 附近), N 红色。"""
        painter.save()
        painter.setFont(self._font)
        f = QFont(self._font)
        f.setBold(True)
        f.setPointSize(12)
        painter.setFont(f)
        for az, label, col in ((0, "N", QColor(tokens().COLORS["danger"])),
                                (90, "E", self._ink()),
                                (180, "S", self._ink()),
                                (270, "W", self._ink())):
            pos = self._sky_to_screen(float(az), 2.0)
            if not self._on_screen(pos):
                continue
            painter.setPen(col)
            painter.drawText(QPointF(pos.x() - 8, pos.y() + 5), label)
        painter.restore()

    def _draw_celestial_bodies(self, painter: QPainter):
        """绘制真实太阳/月亮标记（在地平线上方才画）。"""
        painter.save()
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

        # 选中对象：橙色圆环高亮
        if self._hovered_object is not None and self._hovered_object.visible \
                and self._hovered_object.is_above_horizon():
            hp = self._sky_to_screen(self._hovered_object.azimuth_deg,
                                     self._hovered_object.elevation_deg)
            ring_pen = QPen(self._colors["accent"], 2)
            painter.setPen(ring_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(hp, 12, 12)
            painter.drawEllipse(hp, 16, 16)
        painter.restore()

    def _draw_picked_info(self, painter: QPainter):
        """选中天体信息卡 (Stellarium 规格 10.4)。"""
        obj = self._hovered_object
        if not obj:
            return
        pos = self._sky_to_screen(obj.azimuth_deg, obj.elevation_deg)

        # 选中标记环
        painter.save()
        painter.setPen(QPen(self._colors["accent"], 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(pos, 12, 12)
        painter.drawEllipse(pos, 16, 16)
        painter.restore()

        info = self._selected_info or {}
        _t = tokens()
        norad = info.get("norad")
        title = f"NORAD {norad}" if norad else obj.name
        kind_label = info.get("kind_label", "Satellite")

        def _dms(deg: float, signed: bool = True) -> str:
            sign = "-" if deg < 0 else ("+" if signed else "")
            a = abs(deg)
            d = int(a)
            m = int((a - d) * 60.0)
            s = ((a - d) * 60.0 - m) * 60.0
            return f"{sign}{d:3d}°{m:02d}'{s:04.1f}\""

        def _hms(ra_deg: float) -> str:
            h = ra_deg / 15.0
            hh = int(h); mm = int((h - hh) * 60); ss = ((h - hh) * 60 - mm) * 60
            return f"{hh:02d}h {mm:02d}m {ss:04.1f}s"

        rise = info.get("rise"); sset = info.get("set")
        def _hm(jd):
            if jd is None:
                return "..:.."
            from datetime import datetime, timezone
            return datetime.fromtimestamp(_jtime.jd_to_unix(jd),
                                          tz=timezone.utc).strftime("%H:%M")

        lines = [
            (title, True),
            (kind_label, False),
            ("Also known as: " + " · ".join(info.get("aliases", [obj.name]))
             + (f" · COSPAR {info['cospar']}" if info.get("cospar") else ""), False),
            (f"Magnitude  {'—' if info.get('mag') is None else info['mag']}", False),
            (f"Distance   {info.get('dist_km', 0.0):.2f} km", False),
            ("Ra/Dec    " + (f"{_hms(info['ra'])}  {_dms(info['dec'])}"
                            if info.get("ra") is not None else "—"), False),
            (f"Az/Alt    {_dms(obj.azimuth_deg)}  {_dms(obj.elevation_deg)}", False),
            (f"Visibility  Rise {_hm(rise)}   Set {_hm(sset)}", False),
        ]

        painter.save()
        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        box_w = max(fm.horizontalAdvance(t) for t, _ in lines) + 28
        box_h = len(lines) * 17 + 18
        box_x = 14
        box_y = 40
        bg = QColor(_t.COLORS["card_1"])
        bg.setAlpha(235)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(self._colors["accent"], 1))
        painter.drawRoundedRect(box_x, box_y, box_w, box_h, 6, 6)
        ty = box_y + 18
        for text, bold in lines:
            f = QFont(self._font)
            f.setBold(bold)
            painter.setFont(f)
            painter.setPen(QColor(_t.COLORS["text_primary"]) if not bold
                           else QColor(_t.COLORS["accent"]))
            painter.drawText(box_x + 12, ty, text)
            ty += 17
        painter.restore()

    @staticmethod
    def _parse_distance_km(obj: SkyObject) -> float:
        import re
        m = re.search(r"距离\s*([0-9.]+)\s*km", obj.description or "")
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return 0.0

    @staticmethod
    def _estimate_doppler_hz(obj: SkyObject) -> float:
        if obj.frequency_hz <= 0:
            return 0.0
        import re
        v_rel = 0.0
        m = re.search(r"径向速度\s*(-?[0-9.]+)\s*km/s", obj.description or "")
        if m:
            v_rel = float(m.group(1)) * 1000.0
        else:
            m = re.search(r"v_rel\s*=?\s*(-?[0-9.]+)\s*m/s", obj.description or "")
            if m:
                v_rel = float(m.group(1))
        c = 299792458.0
        return -v_rel * obj.frequency_hz / c

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
        painter.drawLine(QPointF(pos.x() - s, pos.y()),
                         QPointF(pos.x() - 4, pos.y()))
        painter.drawLine(QPointF(pos.x() + 4, pos.y()),
                         QPointF(pos.x() + s, pos.y()))
        painter.drawLine(QPointF(pos.x(), pos.y() - s),
                         QPointF(pos.x(), pos.y() - 4))
        painter.drawLine(QPointF(pos.x(), pos.y() + 4),
                         QPointF(pos.x(), pos.y() + s))
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
        """热力图：仅当外部喂入真实射频扫描数据时才绘制。"""
        if not self._heatmap:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        for cell in self._heatmap:
            if cell.elevation_deg <= 0:
                continue
            pos = self._sky_to_screen(cell.azimuth_deg, cell.elevation_deg)
            norm = max(0.0, min(1.0, (cell.signal_db + 100) / 70))
            lo, mid, hi = (self._colors["heatmap_low"],
                           self._colors["heatmap_mid"],
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
        """左下角信息卡片。"""
        painter.save()
        _t = tokens()
        card_w, card_h = 240, 150
        card_x, card_y = 12, self.height() - card_h - 12
        bg = QColor(_t.COLORS["card_2"])
        bg.setAlpha(220)
        border = QColor(_t.COLORS["gray_400"])
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(card_x, card_y, card_w, card_h, 6, 6)

        ink = self._ink()
        dim = QColor(_t.COLORS["gray_300"])
        label_col = QColor(_t.COLORS["gray_100"])

        y = card_y + 20
        painter.setPen(label_col)
        painter.setFont(self._font)
        painter.drawText(card_x + 12, y, "天线指向")
        painter.setFont(self._mono_font)
        painter.setPen(QPen(self._colors["antenna"], 2).color())
        painter.drawText(card_x + 80, y,
                         f"AZ {self._antenna.azimuth_deg:5.1f} "
                         f"EL {self._antenna.elevation_deg:4.1f}")

        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "可见卫星")
        painter.setFont(self._mono_font)
        visible = sum(1 for o in self._objects
                      if o.visible and o.is_above_horizon())
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
        utc_str = utc_now.strftime("%H:%M:%S") if isinstance(utc_now, datetime) \
            else str(utc_now)
        painter.drawText(card_x + 52, y,
                         f"{utc_str} [{self._time_info.get('ntp_status', 'system')}]")

        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "呼号")
        painter.setFont(self._mono_font)
        painter.setPen(ink)
        painter.drawText(card_x + 52, y,
                         self._callsign if self._callsign else "未设置")

        y += 18
        painter.setFont(self._font)
        painter.setPen(dim)
        painter.drawText(card_x + 12, y, "滚轮缩放 | 拖拽旋转 | 双击居中")
        painter.restore()

    def _draw_passes_panel(self, painter: QPainter):
        """右侧未来过境列表。"""
        if not self._upcoming_passes:
            return
        painter.save()
        _t = tokens()
        rows = self._upcoming_passes[:8]
        row_h = 18
        panel_w = 190
        panel_h = 26 + len(rows) * row_h
        panel_x = self.width() - panel_w - 12
        panel_y = 56
        bg = QColor(_t.COLORS["card_2"])
        bg.setAlpha(220)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor(_t.COLORS["gray_400"]), 1))
        painter.drawRoundedRect(panel_x, panel_y, panel_w, panel_h, 6, 6)

        ink = self._ink()
        dim = QColor(_t.COLORS["gray_300"])
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

    def _draw_gnss_satellites(self, painter: QPainter):
        """在天空图上画真实 GNSS 卫星点（GSV/GSA 驱动）。

        使用与其他卫星相同的 _sky_to_screen 投影（支持 pan/zoom），
        而非独立的极坐标投影，保证视角一致。
        """
        if not self._gnss_connected or not self._gnss_satellites:
            return
        painter.save()
        painter.setFont(self._gnss_label_font)
        dot_r = 7.0
        for sat in self._gnss_satellites:
            pos = self._sky_to_screen(sat["azimuth"], sat["elevation"])
            base = self._gnss_colors.get(sat["constellation"],
                                         self._gnss_colors["未知"])
            snr = sat.get("snr_db")
            snr = float(snr) if snr is not None else 0.0
            alpha = int(255.0 * max(0.3, min(1.0, snr / 50.0)))
            used = sat["prn"] in self._gnss_sats_used

            if used:
                fill = QBrush(QColor(base.red(), base.green(), base.blue(), alpha))
                painter.setBrush(fill)
                painter.setPen(QPen(QColor(base.red(), base.green(),
                                           base.blue(), 255), 1.4))
                painter.drawEllipse(pos, dot_r, dot_r)
                painter.setPen(QPen(QColor(base.red(), base.green(),
                                           base.blue(), 255), 2.4))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(pos, dot_r + 3.5, dot_r + 3.5)
            else:
                pen_col = QColor(base.red(), base.green(), base.blue(), alpha)
                painter.setPen(QPen(pen_col, 1.2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(pos, dot_r, dot_r)

            painter.setPen(self._ink())
            painter.drawText(QPointF(pos.x() + dot_r + 3, pos.y() - dot_r - 2),
                             str(sat["prn"]))
        painter.restore()

    def _draw_gnss_overlay(self, painter: QPainter):
        """GNSS 状态与图例。"""
        painter.save()
        _t = tokens()
        # 无地面站坐标时，中心区域由 _draw_data_source_overlay 的
        # "地面站未设置"空状态独占；这里不再绘制任何 GNSS 文字/图例，
        # 避免与居中的空状态文字重叠。
        if self._observer is None:
            painter.restore()
            return

        if not self._gnss_connected:
            f = QFont(self._font)
            f.setPointSize(11)
            painter.setFont(f)
            painter.setPen(QColor(_t.COLORS["gray_300"]))
            painter.drawText(QRectF(0, self.height() * 0.30, self.width(), 60),
                             Qt.AlignHCenter | Qt.AlignVCenter, "GNSS 未连接")
            painter.restore()
            return

        counts: Dict[str, int] = {}
        for s in self._gnss_satellites:
            counts[s["constellation"]] = counts.get(s["constellation"], 0) + 1

        n_vis = len(self._gnss_satellites)
        n_used = len(self._gnss_sats_used)
        fix_txt = {3: "3D", 2: "2D"}.get(self._gnss_fix_type, "无")

        cons_order = ["GPS", "BeiDou", "GLONASS", "Galileo", "未知"]
        rows = [c for c in cons_order if counts.get(c, 0) > 0]
        row_h = 18
        card_w = 250
        card_h = 26 + row_h * (1 + len(rows))
        x0, y0 = 12.0, 12.0

        bg = QColor(_t.COLORS["card_2"])
        bg.setAlpha(225)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor(_t.COLORS["gray_400"]), 1))
        painter.drawRoundedRect(QRectF(x0, y0, card_w, card_h), 6, 6)

        painter.setFont(self._font)
        painter.setPen(self._ink())
        painter.drawText(QPointF(x0 + 10, y0 + 19),
                         f"GNSS: 已连接 | 可见 {n_vis} 颗 | "
                         f"定位 {n_used} 颗 | 固定: {fix_txt}")
        ty = y0 + 19 + row_h
        for cons in rows:
            col = self._gnss_colors[cons]
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(col))
            painter.drawRect(QRectF(x0 + 12, ty - 9, 10, 10))
            painter.setPen(self._ink())
            painter.drawText(QPointF(x0 + 30, ty), f"{cons}  {counts[cons]} 颗")
            ty += row_h
        painter.restore()

    def _draw_data_source_overlay(self, painter: QPainter):
        """数据来源标注：空状态 / 警告角标。

        优先级：
          1. 无观测站坐标 -> "地面站未设置"（红色警告）
          2. 有观测站但无 TLE -> "无 TLE 数据"（红色警告）
        """
        painter.save()
        _t = tokens()

        if self._data_source == "none" or self._observer is None:
            # 空状态：地面站未设置（最高优先级，独占中心区域）
            f = QFont(self._font)
            f.setPointSize(12)
            painter.setFont(f)
            text = "地面站未设置\n请配置经纬度或连接 GNSS"

            # 先量出多行文字包围盒，在其外层包一层半透明圆角卡片，
            # 避免文字直接浮在天空圆盘上、也便于与其它 overlay 区分。
            fm = QFontMetrics(f)
            tight = fm.boundingRect(text)  # 多行文字紧凑包围盒，左上角在 (0,0)
            pad = 20.0
            radius = 12.0
            card_w = tight.width() + pad * 2
            card_h = tight.height() + pad * 2
            card_x = self.width() / 2.0 - card_w / 2.0
            card_y = self.height() / 2.0 - card_h / 2.0

            card_bg = QColor(_t.COLORS["card_2"])
            card_bg.setAlpha(220)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(card_bg))
            painter.drawRoundedRect(QRectF(card_x, card_y, card_w, card_h),
                                    radius, radius)

            painter.setPen(self._colors["warning"])
            painter.drawText(QRectF(card_x, card_y, card_w, card_h),
                             Qt.AlignCenter, text)
            painter.restore()
            return

        # 有观测站坐标，但 TLE 未加载
        if not self._tle_loaded:
            f = QFont(self._font)
            f.setPointSize(11)
            painter.setFont(f)
            painter.setPen(self._colors["warning"])
            painter.drawText(QRectF(0, 0, self.width(),
                                    self.height() - 80),
                             Qt.AlignCenter,
                             "无 TLE 数据\n请放置 TLE 文件到 ~/.mbdsdr/tle/ 或联网获取")
            painter.restore()
            return

        # 有观测站 + TLE，但 sgp4 计算未连通
        if not self._satellites_connected:
            f = QFont(self._font)
            f.setPointSize(10)
            painter.setFont(f)
            painter.setPen(self._colors["warning"])
            painter.drawText(QRectF(0, 0, self.width(),
                                    self.height() - 60),
                             Qt.AlignCenter, "卫星轨道计算失败 (sgp4 错误)")
            painter.restore()
            return

        painter.restore()

    # ========================================================================
    # 交互事件：委托给混入类 SkyInteractionHandler
    # ========================================================================

    def mousePressEvent(self, event: QMouseEvent):
        SkyInteractionHandler.mousePressEvent(self, event)

    def mouseMoveEvent(self, event: QMouseEvent):
        SkyInteractionHandler.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        SkyInteractionHandler.mouseReleaseEvent(self, event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        SkyInteractionHandler.mouseDoubleClickEvent(self, event)

    def wheelEvent(self, event: QWheelEvent):
        SkyInteractionHandler.wheelEvent(self, event)

    # ------------------------------------------------------------------ 触屏手势
    def _on_touch_pinch_zoom(self, scale_factor: float, center_pos: QPointF) -> None:
        """双指捏合缩放 FOV：复用 view_state.zoom 的指数缩放（与滚轮同手感）。

        scale_factor>1 = 双指张开 = 放大（FOV 变小）→ num_steps>0。
        """
        try:
            num_steps = math.log(max(0.2, min(5.0, scale_factor))) * 2.0
            self.view_state.zoom(num_steps)
            self.on_view_changed()
        except Exception:
            pass

    def _on_touch_pan(self, dx: float, dy: float) -> None:
        """单指拖动平移：复用 SkyInteractionHandler 的 screen_to_sky 反投影平移。"""
        try:
            last = getattr(self, "_pan_last_pos", None)
            if last is None:
                return
            size = (float(self.width()), float(self.height()))
            az1, alt1 = screen_to_sky(last.x(), last.y(), self.view_state,
                                      self.projection, size)
            nx, ny = last.x() + dx, last.y() + dy
            az2, alt2 = screen_to_sky(nx, ny, self.view_state,
                                      self.projection, size)
            daz = az2 - az1
            if daz > 180.0:
                daz -= 360.0
            elif daz < -180.0:
                daz += 360.0
            dalt = alt1 - alt2
            self.view_state.pan(daz, dalt)
            self.on_view_changed()
        except Exception:
            pass

    # ------------------------------------------------------------------ 钩子
    def on_view_changed(self) -> None:
        """视角变化 -> 触发重绘。"""
        self.update()

    def on_object_picked(self, obj) -> None:
        """点选到天体：高亮 + 构建信息卡 + emit 信号。"""
        if obj is None:
            self._hovered_object = None
            self._selected_info = None
            # 取消选中：隐藏调谐按钮
            self._tune_sat_name = None
            self._tune_btn.hide()
            self._doppler_label.hide()
            self._tune_refresh_timer.stop()
            self.update()
            return
        self._hovered_object = obj
        self._build_selected_info(obj)
        self.object_clicked.emit(obj)
        self.update()

    def on_drag_state_changed(self, dragging: bool) -> None:
        self._dragging = dragging

    def _find_object_at(self, x: float, y: float,
                         tolerance: float = 12.0) -> Optional[SkyObject]:
        """像素拾取（兼容旧接口）。"""
        for obj in self._objects:
            if not obj.visible or not obj.is_above_horizon():
                continue
            pos = self._sky_to_screen(obj.azimuth_deg, obj.elevation_deg)
            if math.hypot(pos.x() - x, pos.y() - y) < tolerance:
                return obj
        return None

    def resizeEvent(self, event: QResizeEvent):
        # 搜索框: 顶部居中 (自包含 UI)
        try:
            w = min(260, self.width() - 200)
            self._search_edit.setGeometry(max(60, (self.width() - w) // 2), 8,
                                          w, 26)
        except Exception:
            pass
        # 调谐按钮覆盖层跟随尺寸
        self._position_tune_overlay()
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
        # 控制条高度弹性：最小 28 / 最大 44
        control_bar.setMinimumHeight(28)
        control_bar.setMaximumHeight(44)
        _t = tokens()
        control_bar.setStyleSheet(
            "QFrame#skyControlBar { background: " + _t.COLORS["card_1"] + ";"
            " border-top: 1px solid " + _t.COLORS["gray_400"] + "; }"
            "QPushButton { background: " + _t.COLORS["card_3"] + "; color: "
            + _t.text("secondary") + ";"
            " border: none; border-radius: 3px;"
            " padding: 2px 8px; font-size: 9pt; min-width: 40px; }"
            "QPushButton:hover { background: " + _t.COLORS["card_hover"] + "; }"
            "QLabel { color: " + _t.text("tertiary") + "; font-size: 9pt; padding: 0 8px; }"
        )
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
# 实时卫星跟踪桥：sgp4 真传播，喂入天空图
# ----------------------------------------------------------------------------
# TLE 来源（无硬编码轨道元素）：
#   1. 用户 TLE 文件: ~/.mbdsdr/tle/*.tle (3行格式)
#   2. Celestrak 在线/缓存: orbit.fetch_tle(CATNR) -> ~/.mbdsdr/tle_cache/
#
# 坐标变换链（对照 Stellarium StelCore）：
#   sgp4 -> TEME -> Rz(-GMST) -> ECEF -> ENU -> az/el/range
#   (详见模块文档头部的对照说明)
# ============================================================================


class SatelliteTracker:
    """定时用 sgp4 计算卫星实时地平坐标，更新天空图。

    时间统一从 new_spacetime.get_time_engine() 取（时间穿梭真实驱动）。
    站址由外部 set_location 注入；为 None 时不计算，天空图显示"地面站未设置"。
    TLE 从用户文件或 Celestrak 加载；失败则显示"无 TLE 数据"，不造假。
    """

    def __init__(self, sky_view, lat: Optional[float], lon: Optional[float],
                 alt_km: float = 0.0, interval_ms: int = 10000):
        self.sky_view = sky_view
        self.lat = lat
        self.lon = lon
        self.alt_km = alt_km
        self._interval_ms = interval_ms
        self._catalog: List[Dict[str, object]] = []  # [{name, line1, line2, freq_mhz, kind}]
        self._satrec_cache: Dict[str, object] = {}   # name -> sgp4 Satrec

        self._timer = QTimer(self.sky_view)
        self._timer.timeout.connect(self.refresh)

        # 加载 TLE 目录
        self._reload_catalog()

        if lat is None or lon is None:
            self.lat = None
            self.lon = None
            self.sky_view.set_objects([])
            self.sky_view.clear_trajectories()
            self.sky_view.set_satellites_connected(False)
            self.sky_view.set_tle_loaded(len(self._catalog) > 0)
            return

        self.sky_view.set_tle_loaded(len(self._catalog) > 0)
        self._timer.start(interval_ms)
        self.refresh()

    # ------------------------------------------------------------------ TLE
    def _reload_catalog(self):
        """加载 TLE 目录：用户文件优先，Celestrak 缓存补充。"""
        self._catalog = []
        self._satrec_cache = {}

        # 1) 用户 TLE 文件
        user_cat = _load_user_tle_catalog()
        if user_cat:
            self._catalog.extend(user_cat)

        # 2) Celestrak 目录（仅当用户未提供任何 TLE 时尝试在线获取）
        #    如果用户已有自定义 TLE 文件，不覆盖
        if not self._catalog:
            cel_cat = _load_celestrak_tle_catalog()
            self._catalog.extend(cel_cat)

        self.sky_view.set_tle_loaded(len(self._catalog) > 0)

    def _get_satrec(self, name: str, line1: str, line2: str):
        """返回 sgp4 Satrec；失败返回 None。"""
        if name in self._satrec_cache:
            return self._satrec_cache[name]
        try:
            from sgp4.api import Satrec
            sat = Satrec.twoline2rv(line1, line2)
        except Exception:
            return None
        self._satrec_cache[name] = sat
        return sat

    # ------------------------------------------------------------- 位置计算
    def _engine_unix(self) -> float:
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            eng = get_time_engine()
            eng.tick()
            return eng.now_unix()
        except Exception:
            return time.time()

    def _compute_sun_moon(self, t_unix: float) -> Tuple[Optional[Dict], Optional[Dict], float]:
        """真实太阳/月亮 az/alt + 天空亮度因子。"""
        sun = moon = None
        brightness = 0.0
        jd = t_unix / 86400.0 + 2440587.5
        try:
            from mbdsdr_ai import atmosphere
            sun_alt = atmosphere.sun_altitude_deg(jd, self.lat, self.lon)
            brightness = atmosphere.sky_brightness_factor(sun_alt)
        except Exception:
            sun_alt = None
        try:
            from mbdsdr_ai.atmosphere import _sun_ra_dec_deg, _gmst_deg
            ra, dec = _sun_ra_dec_deg(jd)
            lst = (_gmst_deg(jd) + self.lon) % 360.0
            ha = math.radians((lst - ra) % 360.0)
            lat_r = math.radians(self.lat)
            dec_r = math.radians(dec)
            sin_alt = math.sin(lat_r) * math.sin(dec_r) \
                + math.cos(lat_r) * math.cos(dec_r) * math.cos(ha)
            sin_alt = max(-1.0, min(1.0, sin_alt))
            alt = math.degrees(math.asin(sin_alt))
            cos_alt = max(0.05, math.cos(math.radians(alt)))
            cos_az = (math.sin(dec_r) - math.sin(math.radians(alt)) * math.sin(lat_r)) \
                / (cos_alt * math.cos(lat_r))
            sin_az = -math.cos(dec_r) * math.sin(ha) / cos_alt
            az = (math.degrees(math.atan2(sin_az, cos_az)) + 360.0) % 360.0
            sun = {"az": az, "alt": alt}
        except Exception:
            pass
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
        """每帧：取时间 -> sgp4 算卫星 -> 投影喂给天空图。"""
        if self.lat is None or self.lon is None:
            return
        t_unix = self._engine_unix()

        # 太阳/月亮/昼夜
        try:
            sun, moon, brightness = self._compute_sun_moon(t_unix)
            self.sky_view.set_celestial_bodies(sun, moon, brightness)
        except Exception:
            pass

        # TLE 目录为空 -> 不计算卫星
        if not self._catalog:
            self.sky_view.set_satellites_connected(False)
            self.sky_view.set_objects([])
            return

        try:
            from mbdsdr_ai import orbit
            from sgp4.api import Satrec  # noqa: F401  (确保 sgp4 可用)
        except ImportError:
            self.sky_view.set_satellites_connected(False)
            return

        # unix -> UTC 儒略日
        jd_utc = t_unix / 86400.0 + 2440587.5

        objs: List[SkyObject] = []
        connected = 0

        for entry in self._catalog:
            name = entry["name"]
            line1 = entry["line1"]
            line2 = entry["line2"]
            freq_mhz = float(entry.get("freq_mhz", 0.0))
            kind = str(entry.get("kind", "custom"))

            sat = self._get_satrec(name, line1, line2)
            if sat is None:
                continue

            # 使用 orbit._state_from_satrec 做 TEME->ECEF->ENU->az/el
            try:
                state = orbit._state_from_satrec(
                    sat, name, jd_utc,
                    self.lat, self.lon, self.alt_km,
                    epoch=line1[18:32].strip() if len(line1) > 32 else "")
            except Exception:
                continue

            if state is None:
                continue

            az_d = float(state["azimuth"])
            alt_d = float(state["elevation"])
            dist_km = float(state["range_km"])
            range_rate = float(state.get("range_rate_kms", 0.0))

            connected += 1
            _t = tokens()
            color = (_t.COLORS["light_accent"] if kind == "amateur" else _t.COLORS["accent"])
            objs.append(SkyObject(
                name=name,
                azimuth_deg=az_d,
                elevation_deg=alt_d,
                obj_type="satellite",
                frequency_hz=freq_mhz * 1e6,
                color=color,
                description=f"仰角{alt_d:.0f} 距离{dist_km:.0f}km "
                            f"径向速度{range_rate:+.2f}km/s",
            ))

            # 未来 10 分钟轨迹（每 60s 一点）
            pts = []
            for k in range(0, 11):
                try:
                    jd_k = (t_unix + k * 60) / 86400.0 + 2440587.5
                    st = orbit._state_from_satrec(
                        sat, name, jd_k,
                        self.lat, self.lon, self.alt_km)
                    if st is not None:
                        pts.append((float(st["azimuth"]), float(st["elevation"])))
                except Exception:
                    continue
            if pts:
                self.sky_view.set_trajectory(name, pts)

        self.sky_view.set_objects(objs)
        self.sky_view.set_satellite_catalog(self._catalog)
        self.sky_view.set_satellites_connected(connected > 0)

        # 未来过境列表（best-effort）
        try:
            self._refresh_passes(jd_utc)
        except Exception:
            pass

    def _refresh_passes(self, jd_utc: float):
        """预测未来 24h 过境，喂入天空图侧栏。失败则清空列表。"""
        try:
            from mbdsdr_ai import sat_passes
            gs = sat_passes.GroundStation(
                lat_deg=self.lat, lon_deg=self.lon,
                alt_m=self.alt_km * 1000.0)
            t0_unix = (jd_utc - 2440587.5) * 86400.0
            rows = []
            for entry in self._catalog[:6]:
                name = entry["name"]
                line1 = entry["line1"]
                line2 = entry["line2"]
                try:
                    from sgp4.api import Satrec
                    sat = Satrec.twoline2rv(line1, line2)
                    # 简化：每 5 分钟采样找最大仰角
                    best_el = -90.0
                    best_t = 0.0
                    for mins in range(0, 24 * 60, 5):
                        jd_k = (t0_unix + mins * 60) / 86400.0 + 2440587.5
                        from mbdsdr_ai import orbit
                        st = orbit._state_from_satrec(
                            sat, name, jd_k, self.lat, self.lon, self.alt_km)
                        if st is not None and st["elevation"] > best_el:
                            best_el = st["elevation"]
                            best_t = mins
                    if best_el > 10.0:
                        dt = datetime.fromtimestamp(t0_unix + best_t * 60,
                                                    tz=timezone.utc)
                        rows.append({
                            "name": name,
                            "rise": dt.strftime("%H:%M"),
                            "max_alt": best_el,
                            "duration_min": 0.0,
                        })
                except Exception:
                    continue
            rows.sort(key=lambda r: r["rise"])
            self.sky_view.set_upcoming_passes(rows[:8])
        except Exception:
            self.sky_view.set_upcoming_passes([])

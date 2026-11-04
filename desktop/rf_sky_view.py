# SPDX-License-Identifier: MIT
"""
MBDSDR 天空模式视图 (RF Sky View) — 对照 Stellarium 真实坐标数学
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
  3. 大气折射 (atmospheric refraction): **只加在显示层**。后端历表/轨道仍是
     几何高度；恒星/DSO/行星/太阳/月亮的**绘制位置与视高度读数**经
     display_apparent_alt_deg() 抬升（Stellarium RefractionExtinction.cpp 同款
     分段式，默认 1013.25 hPa / 10 °C）。卫星/过境/天线/热力图/GNSS 保持
     几何高度（指向/跟踪数据），读数处显式标「几何」。地平线本身严格
     alt=0，不随折射移动；日出日落时刻用几何 -0.833° 标准判据（其已含
     折射与视半径），不二次叠加。
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

MBDSDR Project - AI定义无线电 - MIT License（独立实现）
呼号由用户在状态栏设置（未设置时角标显“呼号：未设置”），不写死。
"""

import math
import os
import re
import time

import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple, Set

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics,
    QPainterPath, QPolygonF, QTransform,
    QLinearGradient, QRadialGradient,
    QMouseEvent, QWheelEvent, QResizeEvent,
)
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QLabel

# 交互控制复用 mbdsdr_ai/sky_interaction.py (Stellarium 视角模型独立重实现):
from mbdsdr_ai.celestial_geometry import (
    AzimuthalEquidistantProjection,
    PerspectiveProjection,
    StereographicProjection,
    GroundStation,
    gmst,
    vec_from_radec,
    vec_from_azalt,
    azalt_from_vec,
    altaz_to_j2000,
    j2000_to_altaz,
    precession_matrix,
    nutation_matrix,
)
from mbdsdr_ai.sky_interaction import (
    ViewState,
    SkyInteractionHandler,
    pick_object,
    sky_to_screen,
    sky_to_screen_many,
    screen_to_sky,
)
from mbdsdr_ai import skyengine
from mbdsdr_ai.skyengine import stars as _stars
from mbdsdr_ai.skyengine import constellations as _constel
from mbdsdr_ai.skyengine import xingguan as _xingguan
from mbdsdr_ai.skyengine import satellites as _sats
from mbdsdr_ai.skyengine import jtime as _jtime

from tokens import tokens

# 触屏手势：双指捏合缩放 FOV + 单指拖动平移视角（Surface/平板）。
try:
    from touch_helpers import PinchZoomMixin
except Exception:  # pragma: no cover
    PinchZoomMixin = object  # type: ignore


# ============================================================================
# 暮光渐变 hex 表 (S4): 天顶 -> 地平多段 stops
# ============================================================================
# 夜/昼两张等长 stop 表, 按太阳高度驱动的 _sky_brightness b in [0,1]
# 逐 stop 混合。密度高于旧 3-stop 直通 lerp: 低空段更缓, 地平收口更亮,
# 视觉上接近 Stellarium 大气渐变的层次 (机制自研, 色值为公开天光常识)。
_TWILIGHT_NIGHT = [
    (0.00, "#05080F"),
    (0.35, "#0A1220"),
    (0.60, "#122031"),
    (0.80, "#1B2E42"),
    (0.93, "#24384C"),
    (1.00, "#2E4254"),
]
_TWILIGHT_DAY = [
    (0.00, "#3F6E9E"),
    (0.35, "#5E8FB8"),
    (0.60, "#82ACCB"),
    (0.80, "#A5C4DA"),
    (0.93, "#C6D9E6"),
    (1.00, "#DCE7EC"),
]


def _twilight_stops(brightness: float):
    """b=0 纯夜 -> b=1 纯昼 的暮光渐变 stops [(pos, QColor), ...]。"""
    b = max(0.0, min(1.0, float(brightness)))
    stops = []
    for (t1, c1), (_, c2) in zip(_TWILIGHT_NIGHT, _TWILIGHT_DAY):
        q1, q2 = QColor(c1), QColor(c2)
        stops.append((t1, QColor(
            int(q1.red() * (1 - b) + q2.red() * b),
            int(q1.green() * (1 - b) + q2.green() * b),
            int(q1.blue() * (1 - b) + q2.blue() * b))))
    return stops


def sun_glow_strength(sun_alt_deg: float) -> float:
    """日出/日落暖辉强度 0..1（SWE §4「日落方位暖红」的强度包络）。

    梯形帽：太阳贴近地平（alt∈[-6°,+3°]）最强——正是晨昏 reddening  peak；
    向夜侧 (-18° 天文夜) 与昼侧 (+15° 高日) 线性收 0。高日全天空已亮、
    深夜无散射，故两端无暖辉。纯函数，不读墙钟/不依赖 widget，便于断言。
    """
    a = float(sun_alt_deg)
    if a <= -18.0 or a >= 15.0:
        return 0.0
    if a < -6.0:
        return (a + 18.0) / 12.0        # -18→0, -6→1（晨昏渐强）
    if a <= 3.0:
        return 1.0                       # 地平峰区 [-6,+3]
    return 1.0 - (a - 3.0) / 12.0        # +3→1, +15→0（日升渐弱）


# ---------------------------------------------------------------------------
# 月球：可见性门、月相几何与亮区路径（纯函数，绘制层与拾取层共用同一事实源）
# ---------------------------------------------------------------------------
# 月球在地平线以上才画、才可点。绘制门与拾取门查询同一个常量/函数，
# 不各自持有阈值（上一轮「绘制门与拾取门同源」的延续）。
MOON_VISIBLE_MIN_ALT_DEG = 0.0

# 地平线严格是**几何地平** alt=0：折射只抬天体视高度，不抬地平线本身
# （否则等于把地平线一起抬，抵消折射的意义）。方位盘也以这条线为基准。
HORIZON_ALT_DEG = 0.0

# 历表缺失视直径时只画最小位置标记（不编造角直径）。
MOON_UNKNOWN_DIAMETER_RADIUS_PX = 2.0


def moon_visible(alt_deg: Optional[float]) -> bool:
    """月球是否应出现在天空图中（绘制与拾取同一判据）。"""
    try:
        return float(alt_deg) > MOON_VISIBLE_MIN_ALT_DEG
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# 大气折射（**显示层**）：几何高度 -> 视高度
# ---------------------------------------------------------------------------
# 后端（sgp4 轨道、历表）给出的高度角一律是几何高度，这里绝不回写。
# 仅天球对象（恒星/DSO/行星/太阳/月亮）的**绘制位置与视高度读数**用它；
# 卫星/过境/天线/热力图/GNSS 等指向与跟踪数据保持几何（见下方 _display_alt
# 与 _draw_objects 的注释）。
#
# 公式与分段口径对照 Stellarium RefractionExtinction.cpp:154-184（已逐行核）：
#   几何高度 > -3.54°  : Saemundsson (S&T 1986 / Meeus) 全式
#   -5.00° < h <= -3.54: 到 0 的线性过渡（避免分段跳变，Stellarium 同款）
#   h <= -5.00°        : 0（公式不再可信）
# 气压/温度修正因子 P/1010 * 283/(273+T) 与 Stellarium :151 一致。
#
# 默认口径：1013.25 hPa / 10 °C（标准大气，也是本仓 atmosphere.py 的默认值）。
# Stellarium 构造函数默认 1013 hPa/10 °C，与 1013.25 的差异 <0.03%，
# 在 1′ 显示精度下不可见（反见证 tests/test_sky_refraction.py）。
DISPLAY_REFRACTION_PRESSURE_HPA = 1013.25
DISPLAY_REFRACTION_TEMPERATURE_C = 10.0
_DISPLAY_REFR_MIN_GEO_ALT_DEG = -3.54       # Stellarium MIN_GEO_ALTITUDE_DEG
_DISPLAY_REFR_TRANSITION_WIDTH_DEG = 1.46   # Stellarium TRANSITION_WIDTH_GEO_DEG

ALT_FRAME_GEOMETRIC = "几何"
ALT_FRAME_APPARENT = "视(折射)"


def _refraction_scale(pressure_hpa: float, temperature_c: float) -> float:
    """气压/温度修正因子（度/角分口径，已除以 60）。"""
    return (float(pressure_hpa) / 1010.0) * 283.0 / (273.0 + float(temperature_c)) / 60.0


def display_refraction_delta_deg(
        geom_alt_deg: float,
        pressure_hpa: float = DISPLAY_REFRACTION_PRESSURE_HPA,
        temperature_c: float = DISPLAY_REFRACTION_TEMPERATURE_C) -> float:
    """几何高度 -> 视高度的抬升量（度，正值）。

    分段与 Stellarium 前向折射整数段一致；低于 -5° 返回 0。非有限输入
    返回 0（显示层不因缺测产生假抬升）。
    """
    try:
        h = float(geom_alt_deg)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(h):
        return 0.0
    scale = _refraction_scale(pressure_hpa, temperature_c)
    if h > _DISPLAY_REFR_MIN_GEO_ALT_DEG:
        x = math.radians(h + 10.3 / (h + 5.11))
        r = scale * (1.02 / math.tan(x) + 0.0019279)
        return max(0.0, r)
    if h > _DISPLAY_REFR_MIN_GEO_ALT_DEG - _DISPLAY_REFR_TRANSITION_WIDTH_DEG:
        h0 = _DISPLAY_REFR_MIN_GEO_ALT_DEG
        x0 = math.radians(h0 + 10.3 / (h0 + 5.11))
        r0 = scale * (1.02 / math.tan(x0) + 0.0019279)
        t = (h - (h0 - _DISPLAY_REFR_TRANSITION_WIDTH_DEG)) \
            / _DISPLAY_REFR_TRANSITION_WIDTH_DEG
        return max(0.0, r0 * t)
    return 0.0


def display_apparent_alt_deg(
        geom_alt_deg: float,
        pressure_hpa: float = DISPLAY_REFRACTION_PRESSURE_HPA,
        temperature_c: float = DISPLAY_REFRACTION_TEMPERATURE_C) -> float:
    """几何高度（度）-> 视高度（度）。"""
    return float(geom_alt_deg) + display_refraction_delta_deg(
        geom_alt_deg, pressure_hpa, temperature_c)


def format_alt_pair(geom_alt_deg: float,
                    pressure_hpa: float = DISPLAY_REFRACTION_PRESSURE_HPA,
                    temperature_c: float = DISPLAY_REFRACTION_TEMPERATURE_C,
                    digits: int = 1, compact: bool = False) -> str:
    """读数用「几何 + 视」双标注；两个模型各说各的，不合并成一个数。"""
    g = format_angle_deg(geom_alt_deg, digits=digits)
    a = format_angle_deg(display_apparent_alt_deg(
        geom_alt_deg, pressure_hpa, temperature_c), digits=digits)
    if compact:
        return f"{ALT_FRAME_GEOMETRIC} {g}·视 {a}"
    return f"{ALT_FRAME_GEOMETRIC} {g} / {ALT_FRAME_APPARENT} {a}"


def _star_label_limit_mag(fov_deg: float) -> float:
    """星名标签的视星等上限（按视场自适应）。

    拉远（≥60°）只给 V≤2.0 的具名亮星，避免整屏糊满；放大后逐步放开到
    5.5 等，让窄视场里的具名星也能看到名字。仅对 ``name`` 非空的星生效，
    无名星不编造名字。避让仍由既有 _queue_label 队列统一处理。
    """
    try:
        f = float(fov_deg)
    except (TypeError, ValueError):
        return 2.0
    if f >= 60.0:
        return 2.0
    if f >= 20.0:
        return 3.0
    if f >= 5.0:
        return 4.0
    return 5.5


def star_catalog_status_text(selection, daylight_hidden: bool = False,
                             error: str = "") -> str:
    """状态卡「星表」行的事实文案（截断时如实写 N/M，不静默少画）。

    ``selection`` 为 ``star_catalog.StarSelection``（鸭子类型，测试可注入
    假对象）；``error`` 为加载/取数失败原因；``daylight_hidden`` 为白天
    整层消隐。三种「没有星」的原因分别说清，不和「0 颗」混为一谈。
    """
    if error:
        return str(error)
    if daylight_hidden:
        return "白天·不显示"
    if selection is None:
        return "—"
    try:
        n = int(selection.returned)
        avail = int(selection.available)
        limit = float(selection.limit_mag)
    except Exception:
        return "—"
    if bool(getattr(selection, "truncated", False)):
        return f"已按上限显示 {n} / 可用 {avail}"
    if bool(getattr(selection, "beyond_catalog_limit", False)):
        return f"显示 {n} 颗 · 上限 V{limit:.1f}"
    return f"显示 {n} 颗 · 极限 V{limit:.1f}"


def compass_labels() -> List[Tuple[float, str, bool]]:
    """方位盘标签：(az, 文本, 是否基点)。

    N/E/S/W 为基点（大号），其余每 30° 给数字度数（小号），保证"方位盘
    有度数标记"；0°=N，顺时针增（与 format_azimuth 一致）。
    """
    out: List[Tuple[float, str, bool]] = []
    cardinal = {0.0: "N", 90.0: "E", 180.0: "S", 270.0: "W"}
    for az in range(0, 360, 30):
        a = float(az)
        text = cardinal.get(a)
        out.append((a, text if text else f"{az}°", text is not None))
    return out


def moon_phase_cos_alpha(illum: Optional[float]) -> Optional[float]:
    """月相相位角余弦 cos α = 2·illum − 1（illum 为被照亮比例 [0,1]）。

    缺值/非有限返回 None——不拿 0（新月）或 0.5（弦月）冒充缺测。
    """
    if illum is None:
        return None
    try:
        k = float(illum)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(k):
        return None
    return max(-1.0, min(1.0, 2.0 * k - 1.0))


def planet_phase_illumination(phase_angle_deg: Optional[float]
                              ) -> Optional[float]:
    """行星被照亮比例 = 0.5·(1 + cos 相位角)。

    与信息卡同一算式（Meeus 相位角定义），不另立模型；缺测返回 None。
    """
    if phase_angle_deg is None:
        return None
    try:
        ang = float(phase_angle_deg)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ang):
        return None
    return 0.5 * (1.0 + math.cos(math.radians(ang)))


def sunlit_state_label(sunlit: Optional[bool],
                       obj_type: str = "satellite",
                       status: Optional[str] = None) -> str:
    """受晒状态卡的唯一文案映射（不编造物理状态）。

    优先用后端 ``Illumination.status``（sunlit/umbra/penumbra/unknown）：
    半影不可二值化成「受晒」，必须如实写「半影（部分受照）」。
    没有 status 时退回快照的 ``sunlit`` 三态；None 既可能是后端没有太阳
    数据，也可能是半影——统一显示「未知」，绝不冒充受晒。
    非卫星对象没有受晒语义，显示占位「—」。
    """
    if obj_type != "satellite":
        return "—"
    s = (status or "").strip().lower()
    if s in ("umbra", "eclipse", "shadow"):
        return "地影"
    if s in ("penumbra", "partial"):
        return "半影（部分受照）"
    if s == "sunlit":
        return "受晒"
    if sunlit is True:
        return "受晒"
    if sunlit is False:
        return "地影"
    return "未知（无受晒数据）"


def sunlit_is_eclipsed(sunlit: Optional[bool],
                       status: Optional[str] = None) -> bool:
    """是否应画「地影」视觉状态：只有本影（umbra / sunlit=False）才画。

    半影（status=penumbra）与 unknown/None **不画**——避免用假阴影表示
    「部分受照」或「无数据」。
    """
    s = (status or "").strip().lower()
    if s in ("penumbra", "partial", "unknown"):
        return False
    if s in ("umbra", "eclipse", "shadow"):
        return True
    return sunlit is False



def _tangent_basis(az_deg: float, alt_deg: float,
                   towards_az_deg: float, towards_alt_deg: float
                   ) -> Optional[Tuple[tuple, tuple, tuple]]:
    """在 (az, alt) 处建立指向目标的切向基 (v0, t_towards, t_perp)。

    方向在 **az/alt 空间的大圆切平面** 上计算，不经过屏幕坐标——"屏幕左右"
    近似只在视图中心成立，月亮跑到别处就会把亮区画反。目标与起点重合或
    对跖时切向不唯一，返回 None（不编造方向）。
    """
    v0 = vec_from_azalt(float(az_deg), float(alt_deg))
    v1 = vec_from_azalt(float(towards_az_deg), float(towards_alt_deg))
    dot = float(v0[0] * v1[0] + v0[1] * v1[1] + v0[2] * v1[2])
    t = (float(v1[0] - dot * v0[0]),
         float(v1[1] - dot * v0[1]),
         float(v1[2] - dot * v0[2]))
    norm = math.sqrt(t[0] ** 2 + t[1] ** 2 + t[2] ** 2)
    if norm < 1e-9:
        return None
    t = (t[0] / norm, t[1] / norm, t[2] / norm)
    # t_perp = v0 × t_towards
    p = (float(v0[1] * t[2] - v0[2] * t[1]),
         float(v0[2] * t[0] - v0[0] * t[2]),
         float(v0[0] * t[1] - v0[1] * t[0]))
    return (tuple(float(x) for x in v0), t, p)


def great_circle_offset_point(az_deg: float, alt_deg: float,
                              towards_az_deg: float, towards_alt_deg: float,
                              step_deg: float
                              ) -> Optional[Tuple[float, float]]:
    """从 (az, alt) 沿大圆朝目标方向走 step_deg 后的地平坐标。

    切向不唯一（重合/对跖）时返回 None。
    """
    basis = _tangent_basis(az_deg, alt_deg, towards_az_deg, towards_alt_deg)
    if basis is None:
        return None
    v0, t, _p = basis
    s = math.radians(float(step_deg))
    c, sn = math.cos(s), math.sin(s)
    v = (c * v0[0] + sn * t[0],
         c * v0[1] + sn * t[1],
         c * v0[2] + sn * t[2])
    return azalt_from_vec(v)


def great_circle_perpendicular_point(az_deg: float, alt_deg: float,
                                     towards_az_deg: float,
                                     towards_alt_deg: float,
                                     step_deg: float
                                     ) -> Optional[Tuple[float, float]]:
    """从 (az, alt) 沿大圆朝 **垂直于目标方向** 走 step_deg 后的地平坐标。

    用于测量月面在切向正交方向上的投影像素尺度（不同投影径向/切向缩放
    可能不同）。切向不唯一时返回 None。
    """
    basis = _tangent_basis(az_deg, alt_deg, towards_az_deg, towards_alt_deg)
    if basis is None:
        return None
    v0, _t, p = basis
    s = math.radians(float(step_deg))
    c, sn = math.cos(s), math.sin(s)
    v = (c * v0[0] + sn * p[0],
         c * v0[1] + sn * p[1],
         c * v0[2] + sn * p[2])
    return azalt_from_vec(v)


def angular_separation_deg(az1_deg: float, alt1_deg: float,
                           az2_deg: float, alt2_deg: float) -> float:
    """两天体在地平坐标下的大圆角距（度）。口径与历表无关，纯几何。"""
    v1 = vec_from_azalt(float(az1_deg), float(alt1_deg))
    v2 = vec_from_azalt(float(az2_deg), float(alt2_deg))
    dot = float(v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2])
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


# ---------------------------------------------------------------------------
# 恒星：白天消隐（只按太阳地平阈值整层开关，**不做**天空亮度→极限星等模型）
# ---------------------------------------------------------------------------
# -0.833° 是「日落」的标准天文定义：太阳中心低于地平 50′（34′ 大气折射 +
# 16′ 日面半径）。太阳高于该值按白天处理，恒星整层不画。
# 这是**保守近似**，不是光度的标准模型：项目里没有公开的天空亮度→极限
# 星等数据，所以不做暮光渐变、不按 Bortle/太阳高度连续压暗恒星——要么按
# 夜空的极限星等画，要么整层关掉。留痕见 docs/limitations-register.md L24。
STAR_DAYLIGHT_SUN_ALT_DEG = -0.833


# 银河图层：仓库内没有银河亮度/尘埃带数据 -> 按「无数据·不提供」处理
# （不画装饰性光带当银河）。状态卡如实标注该图层缺失，接入所需见
# docs/limitations-register.md L25/L28。
GALAXY_LAYER_NOTE = "无数据·不提供"


def stars_hidden_by_daylight(sun_alt_deg: Optional[float]) -> bool:
    """太阳在地平上方（按日落阈值）时恒星整层隐藏（绘制门与拾取门同一判据）。

    太阳高度未知（历表未就绪）返回 False：无法断言是白天，就不假装是。
    """
    if sun_alt_deg is None:
        return False
    try:
        alt = float(sun_alt_deg)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(alt):
        return False
    return alt >= STAR_DAYLIGHT_SUN_ALT_DEG


def moon_lit_path(cos_alpha: float) -> QPainterPath:
    """单位半径月面亮区路径（局部坐标系: +x 指向太阳, +y 屏幕向下）。

    亮区 = 亮缘半圆 (+x 侧) ∪ 晨昏线半椭圆。晨昏线是月面大圆在视线上的
    投影，半短轴 = |cos α|、长轴 = 1；亮区面积比例恰为 (1+cos α)/2，
    即历表照度 illum——月牙/弦月/凸月/满月四态由同一条几何自动给出。
    """
    c = max(-1.0, min(1.0, float(cos_alpha)))
    path = QPainterPath()
    full = QRectF(-1.0, -1.0, 2.0, 2.0)
    # 亮缘半圆: 从 (0,-1) 经 (+1,0) 到 (0,+1)（+x = 朝向太阳）
    path.arcMoveTo(full, 90.0)
    path.arcTo(full, 90.0, -180.0)
    semi = abs(c)
    if semi < 1e-9:
        path.lineTo(0.0, -1.0)          # 弦月: 晨昏线是过月心的直线
    else:
        term = QRectF(-semi, -1.0, 2.0 * semi, 2.0)
        # 凸月 (c>0) 晨昏线向背阳侧凹；月牙 (c<0) 向向阳侧凸
        path.arcTo(term, 270.0, -180.0 if c >= 0.0 else 180.0)
    path.closeSubpath()
    return path


def label_boxes_keep(items: List[Tuple[float, float, float, float, float]],
                     pad: float = 2.0) -> List[int]:
    """二维标签碰撞剔除（纯函数，便于无头断言）。

    items: [(priority, x0, y0, x1, y1)]，priority 越小越优先；返回保留的下标。
    按优先级（同级按传入顺序）逐个放置，与已放置框（四周各留 pad）相交即剔除。
    星名/座名/行星标签因此在密集天区不互相压字，缩放变密时低优先级先消失。
    """
    order = sorted(range(len(items)), key=lambda i: (items[i][0], i))
    placed: List[Tuple[float, float, float, float]] = []
    keep: List[int] = []
    for i in order:
        _prio, x0, y0, x1, y1 = items[i]
        if any(x0 < px1 + pad and x1 > px0 - pad
               and y0 < py1 + pad and y1 > py0 - pad
               for px0, py0, px1, py1 in placed):
            continue
        placed.append((x0, y0, x1, y1))
        keep.append(i)
    return sorted(keep)


def _traj_alpha(i: int, n: int, base: int = 110,
                fade_frac: float = 0.15) -> int:
    """轨迹第 i 段（共 n 个可见点，n-1 段）的 alpha：两端 fade_frac
    比例段数内线性渐入/渐出，中段恒 base。"""
    if n < 2 or i < 0 or i >= n - 1:
        return 0
    fade = max(1, int(round(n * fade_frac)))
    ramp = min(i + 1, n - 1 - i)
    return int(base * min(1.0, ramp / float(fade)))


def selection_crosshair_geometry(t: float, base_radius: float = 14.0,
                                 amp: float = 1.6, period: float = 2.0,
                                 dash_sweep: float = 30.0
                                 ) -> Dict[str, object]:
    """选中十字标记的纯几何：4 段 90° 等分圆弧 + 2s 呼吸半径（SWE 同形）。

    时间 t（秒）由调用方注入，不读墙钟——断言可复现（见
    tests/test_selection_crosshair.py）。角度用 Qt drawArc 约定：0°=3 点钟、
    逆时针为正，4 段中心落在 0/90/180/270。返回
    {"radius": 呼吸后半径, "period": 周期, "dashes": [(起始角°, 跨度°)]}。
    """
    r = float(base_radius) + float(amp) * math.sin(
        2.0 * math.pi * float(t) / float(period))
    half = float(dash_sweep) / 2.0
    dashes = [(c - half, float(dash_sweep)) for c in (0.0, 90.0, 180.0, 270.0)]
    return {"radius": r, "period": float(period), "dashes": dashes}


def breathing_ring_radius(t: float, base_radius: float = 16.0,
                          amp: float = 1.6, period: float = 2.0) -> float:
    """月/行星选中标记的呼吸环半径（SWE 同形，2s 一吸一呼）。纯函数、
    时间注入不读墙钟，便于确定性断言。"""
    return float(base_radius) + float(amp) * math.sin(
        2.0 * math.pi * float(t) / float(period))


def format_observer_chip(observer: Optional[Dict[str, float]]) -> str:
    """左下角观测站读数：有坐标给「40.00°N 116.00°E 50m」，未设置给「未设置」。

    诚实空状态——绝不编造城市/坐标（红线：不写死站址）。纯函数便于确定性断言。
    """
    if not observer:
        return "未设置"
    try:
        lat = float(observer.get("lat"))
        lon = float(observer.get("lon"))
    except (TypeError, ValueError):
        return "未设置"
    ns = "N" if lat >= 0.0 else "S"
    ew = "E" if lon >= 0.0 else "W"
    txt = (f"{format_angle_deg(abs(lat), digits=2)}{ns} "
           f"{format_angle_deg(abs(lon), digits=2)}{ew}")
    alt = observer.get("alt_m")
    if alt is not None:
        try:
            txt += f" {_format_number(float(alt), 0, 'm')}"
        except (TypeError, ValueError):
            pass
    return txt


# ============================================================================
# 屏幕读数格式化：唯一实现（「每个数字的可信度」）
# ----------------------------------------------------------------------------
# 纪律：界面上出现的每个数值都必须经这里格式化，绘制/信息卡/过境面板不再
# 各自拼 f-string。每个函数都写出单位；时间注明 UTC 或本地 + 偏移。
#
# 舍入策略（防「显示精度把误差藏起来」）：
#   * 非零但按显示精度会舍成 0 的量 -> 显示 "<0.1°"（负值 ">-0.1°"），
#     绝不把 0.04° 冒充成 0°；
#   * 百分数在 0/100 边界自动多给一位（99.6% 不显示成 100%，0.04% 不显示
#     成 0.0%），最多补到 4 位小数。
# ============================================================================
AU_KM = 149597870.7

# 16 方位名：方位角语义（N/NE/E…）与度数同时显示，不让人猜 0° 是哪边。
_COMPASS_16 = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def _round_half_up(value: float, digits: int) -> float:
    """四舍五入（远离零），不随 Python 的 banker's rounding 抖动。"""
    q = 10.0 ** int(digits)
    v = float(value)
    return math.copysign(math.floor(abs(v) * q + 0.5) / q, v)


def compass_point(azimuth_deg: float) -> str:
    """方位角的 16 方位名（0°=N，顺时针）。"""
    az = float(azimuth_deg) % 360.0
    return _COMPASS_16[int((az + 11.25) // 22.5) % 16]


def _format_number(value: float, digits: int, unit: str,
                   signed: bool = False) -> str:
    """数值 + 单位的统一格式化（角度/距离/星等/角径共用这一处）。

    非零却被显示精度舍成 0 时返回 "<precision<unit>"（负值 ">-precision"），
    明确告诉读者「比显示精度还小」，而不是给一个会被误读的 0。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    d = max(0, int(digits))
    step = 10.0 ** (-d)
    r = _round_half_up(v, d)
    if v != 0.0 and r == 0.0:
        return f"{'>-' if v < 0 else '<'}{step:.{d}f}{unit}"
    if signed:
        return f"{r:+.{d}f}{unit}"
    return f"{r:.{d}f}{unit}"


def format_angle_deg(value: float, digits: int = 1,
                     signed: bool = False) -> str:
    """仰角/相位角/太阳高度等（°）。"""
    return _format_number(value, digits, "°", signed=signed)


def format_azimuth(azimuth_deg: float, digits: int = 1) -> str:
    """方位角：度数 + 16 方位名（例 "210.3° SW"）。0°=北，顺时针。"""
    try:
        az = float(azimuth_deg)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(az):
        return "—"
    wrapped = az % 360.0
    # 359.96° 舍入成 360.0° -> 折回 0.0°（显示成 360.0° 会让人以为越界）
    if _round_half_up(wrapped, digits) >= 360.0:
        wrapped = 0.0
    return f"{format_angle_deg(wrapped, digits)} {compass_point(wrapped)}"


def format_dms_deg(value: float, signed: bool = True,
                   digits: int = 1) -> str:
    """度分秒（赤纬等）：+ 40°42'46.1"。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    sign = "-" if v < 0 else ("+" if signed else "")
    a = abs(v)
    d = int(a)
    rem = (a - d) * 60.0
    m = int(rem)
    s = _round_half_up((rem - m) * 60.0, digits)
    if s >= 60.0:
        s -= 60.0
        m += 1
    if m >= 60:
        m -= 60
        d += 1
    if a > 0.0 and d == 0 and m == 0 and s == 0.0:
        return f"{sign}<{10.0 ** -digits:.{digits}f}\""
    return f"{sign}{d:3d}°{m:02d}'{s:0{3 + digits}.{digits}f}\""


def format_hms_ra(ra_deg: float, digits: int = 1) -> str:
    """赤经时分秒：12h 34m 56.7s。"""
    try:
        v = float(ra_deg)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    h = (v % 360.0) / 15.0
    hh = int(h)
    mm_all = (h - hh) * 60.0
    mm = int(mm_all)
    ss = _round_half_up((mm_all - mm) * 60.0, digits)
    if ss >= 60.0:
        ss -= 60.0
        mm += 1
    if mm >= 60:
        mm -= 60
        hh = (hh + 1) % 24
    if v > 0.0 and hh == 0 and mm == 0 and ss == 0.0:
        return f"<{10.0 ** -digits:.{digits}f}s"
    return f"{hh:02d}h {mm:02d}m {ss:0{3 + digits}.{digits}f}s"


def local_sidereal_time_deg(jd_ut1: float, lon_deg: float) -> float:
    """地方恒星时（度，0..360）。与后端同一 GMST 定义，不另造公式。"""
    return (gmst(float(jd_ut1)) + float(lon_deg)) % 360.0


def format_view_axis(az_deg: float, alt_deg: float) -> str:
    """摄像机视轴读数：AZ（16 方位）+ ALT（几何高度）。"""
    return f"AZ {format_azimuth(az_deg)} · ALT {format_angle_deg(alt_deg)}"


def star_catalog_credit_text() -> str:
    """星表来源署名（从 npz meta 读取；缺 meta 时如实说不可用）。"""
    try:
        from mbdsdr_ai.skyengine.star_catalog import get_catalog
        meta = get_catalog().meta
    except Exception:
        return "来源不可用"
    src = str(meta.get("source") or "").strip()
    lic = str(meta.get("source_license") or "").strip()
    if not src or not lic:
        return "来源元数据不完整"
    # 显示用短名：保留数据库名与版本，去掉冗余的 "Database" 词。
    src = re.sub(r"\s+Database\b", "", src)
    return f"{src} · {lic}"


def dso_magnitude(entry) -> Optional[float]:
    """DSO 星等；缺失/非有限一律返回 None（不拿 0 顶替）。"""
    try:
        v = getattr(entry, "magnitude", None)
        if v is None:
            return None
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def dso_size_arcmin(entry) -> Optional[float]:
    """DSO 长轴视大小（角分）；缺失/<=0 返回 None（0 在目录里是未测）。"""
    try:
        v = getattr(entry, "size_arcmin", None)
        if v is None:
            return None
        v = float(v)
        if not math.isfinite(v) or v <= 0.0:
            return None
        return v
    except (TypeError, ValueError):
        return None


def dso_axis_ratio(entry) -> Optional[float]:
    """星系短轴/长轴真实轴比；缺小轴或数据非法时返回 None。"""
    size = dso_size_arcmin(entry)
    if size is None:
        return None
    try:
        minor = getattr(entry, "size_minor_arcmin", None)
        if minor is None:
            return None
        minor = float(minor)
        if not math.isfinite(minor) or minor <= 0.0:
            return None
        ratio = minor / size
        if ratio <= 0.0 or ratio > 1.0:
            return None
        return ratio
    except (TypeError, ValueError):
        return None


def dso_label_text(entry) -> str:
    """DSO 标签：中文名/常用名优先，缺失退回编号；永不返回空串。"""
    for attr in ("display_name", "zh_name", "name"):
        try:
            val = getattr(entry, attr, None)
        except Exception:
            val = None
        if val is None:
            continue
        txt = str(val).strip()
        if txt:
            return txt
    return "深空天体"


def format_distance_km(km: float, digits: int = 2) -> str:
    """站心距离（km）。"""
    return _format_number(km, digits, " km")


def format_distance_ly(ly: float, digits: int = 1) -> str:
    """恒星距离（ly，光年）。"""
    return _format_number(ly, digits, " ly")


def format_distance_au(au: float, digits: int = 3) -> str:
    """行星/太阳距离：AU + 十进制换算（万/亿 km），单位写明不缩写。"""
    try:
        v = float(au)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    km = v * AU_KM
    a = abs(km)
    if a >= 1e8:
        q, unit = 1e8, "亿 km"
    elif a >= 1e4:
        q, unit = 1e4, "万 km"
    else:
        q, unit = 1.0, "km"
    au_txt = _format_number(v, digits, " AU")
    return f"{au_txt} · {km / q:,.1f} {unit}"


def format_magnitude(mag: float, digits: int = 2) -> str:
    """视星等（mag）——单位写在读数里，不只给一个裸数。"""
    return _format_number(mag, digits, " mag")


def format_arcsec(arcsec: float, digits: int = 1) -> str:
    """视直径/角径（″）。至少 1 位小数：1.4″ 不显示成 1″。"""
    return _format_number(arcsec, digits, "″")


def format_arcmin(arcmin: float, digits: int = 1) -> str:
    """视大小（′）。至少 1 位小数：1.4′ 不显示成 1′。"""
    return _format_number(arcmin, digits, "′")


def format_percent(fraction: float, digits: int = 1) -> str:
    """被照亮比例 [0,1] -> 百分数；0/100 边界自动多给一位精度。"""
    try:
        v = float(fraction)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    pct = v * 100.0
    d = max(0, int(digits))
    # 99.6% 不显示成 100%、0.04% 不显示成 0.0%：边界处补精度，最多 4 位。
    while d < 4 and 0.0 < pct < 100.0 \
            and _round_half_up(pct, d) in (0.0, 100.0):
        d += 1
    return f"{_round_half_up(pct, d):.{d}f}%"


def format_speed_kms(value: float, digits: int = 2) -> str:
    """径向速度（km/s）。"""
    return _format_number(value, digits, " km/s", signed=True)


def format_frequency_mhz(hz: float, digits: int = 1) -> str:
    """频率（MHz）。"""
    try:
        return _format_number(float(hz) / 1e6, digits, " MHz")
    except (TypeError, ValueError):
        return "—"


def format_frequency_hz(hz: float, digits: int = 0) -> str:
    """频率/多普勒（Hz，带符号）。"""
    return _format_number(hz, digits, " Hz", signed=True)


def _utc_datetime(when) -> Optional[datetime]:
    """datetime 或 unix 秒 -> 带 UTC tzinfo 的 datetime；缺值返回 None。"""
    if when is None:
        return None
    if isinstance(when, datetime):
        dt = when
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    try:
        return datetime.fromtimestamp(float(when), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def format_utc_time(when, fmt: str = "%H:%M") -> str:
    """UTC 时刻（调用处的行标签必须写明 UTC）。"""
    dt = _utc_datetime(when)
    return "—" if dt is None else dt.strftime(fmt)


def format_utc_datetime(when) -> str:
    """UTC 日期时刻（含 UTC 后缀）。"""
    dt = _utc_datetime(when)
    return "—" if dt is None else dt.strftime("%Y-%m-%d %H:%M UTC")


def format_duration_s(seconds: float) -> str:
    """时间跳转量（带符号）：秒 / 分钟 / 小时，随量级换单位。"""
    try:
        v = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    if abs(v) < 60.0:
        return f"{v:+.0f} 秒"
    if abs(v) < 3600.0:
        return f"{v / 60.0:+.1f} 分钟"
    return f"{v / 3600.0:+.2f} 小时"


def format_local_time(when, tz=None, fmt: str = "%H:%M:%S") -> str:
    """本地时刻 + 明确的 UTC 偏移，例 "03:06:07 (UTC+08:00)"。

    tz 缺省用系统本地时区；偏移由 tzinfo 在该时刻给出，夏令时切换自动正确
    （调用 `astimezone`，不手算固定偏移）。
    """
    dt = _utc_datetime(when)
    if dt is None:
        return "—"
    local = dt.astimezone(tz) if tz is not None else dt.astimezone()
    off = local.utcoffset()
    secs = int((off or timedelta(0)).total_seconds())
    sign = "+" if secs >= 0 else "-"
    mins = abs(secs) // 60
    return f"{local.strftime(fmt)} (UTC{sign}{mins // 60:02d}:{mins % 60:02d})"


def format_satellite_description(alt_deg: float, range_km: float,
                                 range_rate_kms: float) -> str:
    """卫星一句话读数（视图快转重算与 SatelliteTracker 共用同一口径）。"""
    return (f"仰角 {format_angle_deg(alt_deg)} · "
            f"距离 {format_distance_km(range_km)} · "
            f"径向速度 {format_speed_kms(range_rate_kms)}")


# ============================================================================
# 数据类型
# ============================================================================


# ============================================================================
# 行星层 (Stellarium 首屏玩法: 火/木/金等行星标记+标签)
# ============================================================================
# 位置计算复用 mbdsdr_ai/astronomy/planets.py (Meeus 低精度历表, 已入库
# 并对拍 de440s)。样式色值为公开天文常识级配色, 非上游代码。
# zh 为文化对位层主标签; en 进信息卡 aliases。
_PLANET_STYLE: Dict[str, Dict[str, object]] = {
    "mercury": {"zh": "水星", "color": (158, 158, 164)},
    "venus":   {"zh": "金星", "color": (244, 238, 214)},
    "mars":    {"zh": "火星", "color": (221, 112,  80)},
    "jupiter": {"zh": "木星", "color": (235, 215, 178)},
    "saturn":  {"zh": "土星", "color": (240, 226, 158)},
    "uranus":  {"zh": "天王星", "color": (159, 219, 229)},
    "neptune": {"zh": "海王星", "color": (122, 152, 240)},
}


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
        sunlit: Optional[bool] = None,
        sunlit_status: Optional[str] = None,
        boundary_uncertainty_s: Optional[float] = None,
    ):
        self.name = name
        self.azimuth_deg = azimuth_deg  # 方位角 0-360°（北=0，东=90）
        self.elevation_deg = elevation_deg  # 仰角 -90..90°
        self.obj_type = obj_type  # satellite / signal / interferer / sun / moon / custom
        self.frequency_hz = frequency_hz
        self.signal_strength_db = signal_strength_db
        self.description = description
        self.color = color
        # 受晒状态：True=受晒 / False=地影 / None=后端未提供（或半影不确定）。
        # 本视图**不自己算**物理判据，只消费后端快照字段；None 时按「未知」
        # 显示且不画阴影，绝不把默认值当成受晒。
        self.sunlit = sunlit
        # 后端 Illumination.status 原文（sunlit/umbra/penumbra/unknown）。
        # 有 status 时优先按它显示（半影不可二值化，别压成「受晒」）；
        # 快照未提供时保持 None，状态卡退回 sunlit 三态口径。
        self.sunlit_status = sunlit_status
        # 影边界时间不确定度（秒）：**内部保留、不对用户展示**。用于解释
        # 卫星刚进/出影时三态跳变（相邻两帧状态翻面仍在 ±uncertainty 内
        # 属正常抖动，不是数据错误）。只消费后端快照字段。
        self.boundary_uncertainty_s = boundary_uncertainty_s
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
            # TLE 可追溯（真实数据原则）：来源 + 历元（解析失败如实空串）
            from mbdsdr_ai.orbit import tle_epoch_iso as _tle_epoch_iso
            catalog.append({
                "name": clean_name,
                "line1": line1,
                "line2": line2,
                "freq_mhz": freq_mhz,
                "kind": kind,
                "tle_source": f"用户 TLE 文件（~/.mbdsdr/tle/{fn}）",
                "tle_epoch": _tle_epoch_iso(line1),
            })
        except Exception:
            continue
    return catalog


# ---------------------------------------------------------------------------
# 目录名 ↔ CATNR 一致性校验（44016/54234 错配事故的通用防线）
# ---------------------------------------------------------------------------
def _norm_sat_name(name: str) -> str:
    """卫星名归一化：大写 + 去分隔符（空格/连字符/点/括号）。

    'METEOR-M 2' / 'METEOR M2' / 'METEOR-M2' -> 'METEORM2'。
    """
    return "".join(ch for ch in str(name).upper() if ch.isalnum())


def satellite_name_matches(declared: str, tle_line0: str) -> bool:
    """目录声明名与 TLE line0 语义匹配判定。

    归一化后要求**完全相等**——防止串号：
    'METEOR M2'(METEORM2) 只允许匹配 line0 'METEOR-M 2'，
    不匹配 'METEOR-M2 2'(METEORM22) —— 那是另一颗星（44387）。
    """
    a = _norm_sat_name(declared)
    b = _norm_sat_name(tle_line0)
    return bool(a) and a == b


def satellite_catalog_mismatches(meta_table, resolve_line0):
    """扫描目录表，返回 (mismatches, no_data) 两类问题清单（全量，不只第一个）。

    meta_table: {name: {"catnr": int, ...}}
    resolve_line0: callable(catnr) -> str|None  （返回 None = 该编号无数据）
    返回:
      mismatches: [(name, catnr, line0)] —— 有数据但语义不匹配（错配）
      no_data:    [(name, catnr)]        —— 缓存缺该编号（单独归类，不算错配）
    """
    mismatches = []
    no_data = []
    for name, meta in meta_table.items():
        catnr = int(meta["catnr"])
        line0 = resolve_line0(catnr)
        if line0 is None:
            no_data.append((name, catnr))
            continue
        if not satellite_name_matches(name, line0):
            mismatches.append((name, catnr, str(line0).strip()))
    return mismatches, no_data


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
    # CATNR 与名称的一致性由 tests/test_satellite_catnr_identity.py 用
    # 真实 TLE line0 双向钉扎（44016/54234 错配事故的回归锁）：
    #   METEOR M2 = 40069（不是 44016 箭体碎片 ATLAS 5 CENTAUR DEB）；
    #   FENGYUN 3D = 43010（不是 54234 NOAA 21 JPSS-2）。
    SATELLITE_META: Dict[str, Dict[str, object]] = {
        "NOAA 15":      {"catnr": 25338, "freq_mhz": 137.620, "kind": "weather"},
        "NOAA 18":      {"catnr": 28654, "freq_mhz": 137.9125, "kind": "weather"},
        "NOAA 19":      {"catnr": 33591, "freq_mhz": 137.100, "kind": "weather"},
        "ISS (ZARYA)":  {"catnr": 25544, "freq_mhz": 145.800, "kind": "amateur"},
        "METEOR M2":    {"catnr": 40069, "freq_mhz": 137.900, "kind": "weather"},
        "FENGYUN 3D":   {"catnr": 43010, "freq_mhz": 136.900, "kind": "weather"},
    }

    for name, meta in SATELLITE_META.items():
        catnr = int(meta["catnr"])
        try:
            line1, line2 = orbit.fetch_tle(catnr)
        except Exception:
            # Celestrak 不可用或缓存过期：跳过此卫星，不造假
            continue
        # 下行频点优先取链路目录 sat_links（皮进配置，源码表只留兜底）；
        # 目录没有的星才用表里默认值。
        freq_mhz = float(meta["freq_mhz"])
        try:
            from mbdsdr_ai.sat_links import find_tuning_for
            found = find_tuning_for(name)
            if found is not None:
                freq_mhz = float(found["downlink"]["freq_hz"]) / 1e6
        except Exception:
            pass
        catalog.append({
            "name": name,
            "line1": line1,
            "line2": line2,
            "freq_mhz": freq_mhz,
            "kind": str(meta["kind"]),
            "tle_source": f"Celestrak CATNR {catnr}（在线/缓存 ~/.mbdsdr/tle_cache/）",
            "tle_epoch": orbit.tle_epoch_iso(line1),
        })
    return catalog


# ============================================================================
# 地景剪影：确定性值噪声多层轮廓（远山地平线 -> 树 -> 平顶房屋）
# ============================================================================

# (seed, base_alt_deg, amp_alt_deg, lattice_deg, octaves, quant_deg)
# 量化步进 >0 的层出平顶（房屋）；0 为自然曲线（山/树）。
_LANDSCAPE_LAYERS = (
    (11, 1.5, 6.0, 45.0, 3, 0.0),    # 远山：低频大起伏
    (37, 0.4, 3.4, 11.0, 3, 0.0),    # 树：中频密起伏
    (53, 0.0, 2.2, 23.0, 2, 0.35),   # 房屋：量化平顶
)


def _hash01(n: int) -> float:
    """整数 -> [0,1) 确定性散列（跨平台稳定，不依赖 hash() 随机化）。"""
    n = (n * 2654435761) % 4294967296
    n = ((n ^ (n >> 15)) * 2246822519) % 4294967296
    n = ((n ^ (n >> 13)) * 3266489917) % 4294967296
    n = n ^ (n >> 16)
    return (n % 1000000) / 1000000.0


def _landscape_layer_profile(az_deg: float, *, seed: int, base_alt: float,
                             amp_alt: float, lattice_deg: float,
                             octaves: int = 3, quant_deg: float = 0.0) -> float:
    """某方位上该层剪影的仰角（度）。值噪声多倍频叠加，输出 ∈ [base, base+amp]。"""
    total = 0.0
    amp = 1.0
    lat = lattice_deg
    for o in range(octaves):
        x = az_deg / lat
        i = math.floor(x)
        f = x - i
        t = f * f * (3.0 - 2.0 * f)      # smoothstep 插值，格点处一阶连续
        h0 = _hash01(seed * 4096 + o * 131 + i * 101)
        h1 = _hash01(seed * 4096 + o * 131 + (i + 1) * 101)
        total += amp * (h0 + (h1 - h0) * t)
        amp *= 0.5
        lat *= 0.5
    amp_sum = 2.0 - 0.5 ** octaves       # 1 + 0.5 + ... 归一化
    alt = base_alt + amp_alt * total / amp_sum
    if quant_deg > 0.0:
        alt = quant_deg * round(alt / quant_deg)
    return min(alt, 89.0)


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    """标准 smoothstep：x≤edge0→0，x≥edge1→1，之间三次 Hermite 平滑。

    edge0 可 > edge1（反向衰减）。基础数学，独立实现（非任何 AGPL 源码）。
    """
    if edge0 == edge1:
        return 0.0 if x < edge0 else 1.0
    t = (float(x) - edge0) / (edge1 - edge0)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


def _landscape_alpha(fov_deg: float, center_alt_deg: float) -> float:
    """地景整体不透明度 = alpha_fov × alpha_down（SWE §1 透明度规则）。

    alpha_fov  = smoothstep(1°,20°,fov)：放大到 ~1° 内地景淡出防糊
                 （剪影是粗值噪声，紧 fov 下会糊成黑团），≥20° 全显。
    alpha_down = 0.5 + 0.5·smoothstep(-45°,0°,视线alt)：俯视地下时渐隐到
                 一半（无穷远剪影模型在地平下失效，柔和退化而非硬切）。
    返回 ∈ [0,1]；≤~0 时调用方应整层跳过。
    """
    a_fov = _smoothstep(1.0, 20.0, float(fov_deg))
    a_down = 0.5 + 0.5 * _smoothstep(-45.0, 0.0, float(center_alt_deg))
    return max(0.0, min(1.0, a_fov * a_down))


# ============================================================================
# 天空模式视图主组件
# ============================================================================


class RFSkyView(PinchZoomMixin, QWidget, SkyInteractionHandler):
    """
    天空模式视图：方位角等距投影的天空图（天顶居中，地平线为边缘圆）。

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

    # ---- 快转（|rate|>1）下的一致性参数 --------------------------------
    # 摄像机锁定：指数趋近的时间常数按「真实秒」设计，快转时单帧目标位移
    # 会远超平滑可跟随范围（×300 每帧模拟 10s，LEO 可动 0.5-10°）。此时
    # 改为逐帧直贴（k=1），并一次性诚实提示——不静默改手感，也不让画面
    # 抽搐（滞后 + 追赶振荡）。阈值按单帧目标位移（度）判定，1x 不触发。
    LOCK_SMOOTH_K = 0.25
    LOCK_SMOOTH_MAX_STEP_DEG = 0.25
    # 单帧位移超过它时，直贴也只是「按帧跳进」，提示里如实说明
    LOCK_FAST_SLIDE_STEP_DEG = 25.0

    # 快转下轨迹过期判据：锚点落后当前模拟时刻超过 2 帧的模拟推进即重算。
    TRAJ_FRAME_S = 0.033               # 视图刷新节拍（33ms）
    TRAJ_MIN_STALE_S = 10.0            # 低倍率下限（避免每帧重算）
    TRAJ_MAX_STALE_S = 120.0           # 高倍率上限（≥2 帧，保证不画过期曲线）
    TRAJ_SPAN_S = 600.0                # 未来 10 分钟（与控制器喂入口径一致）

    object_clicked = Signal(object)  # SkyObject
    pointing_changed = Signal(float, float)  # az, el
    # 选中卫星后点「调到该星」：携带实时多普勒校正后的调谐参数。
    # dict: {name, tuned_freq_hz, doppler_shift_hz, base_freq_hz, mode}
    tune_satellite_requested = Signal(dict)
    # 「跟踪」开关：checked 开始 / unchecked 停止卫星闭环跟踪。
    # (on, sat_name, downlink_hz)——downlink_hz 用目录基准下行频点，
    # 多普勒校正是主窗口跟踪环（_sat_track_timer）的职责，这里不越权。
    track_toggled = Signal(bool, str, float)
    # 「云台跟随」开关：checked 后主窗口在同一 500ms 拍内驱动云台指向卫星。
    # 需要先有可用云台后端（ai-sdr-mini 板载 / rotctld），否则按钮置灰。
    gimbal_follow_toggled = Signal(bool)
    # 全天模式开关（立体投影 160°）；主窗口可据此同步按钮文案。
    all_sky_mode_changed = Signal(bool)

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
        # 轨迹时效锚点（模拟 Unix 秒）：只对带锚点的轨迹生效。
        # 外部只调 set_trajectory(name, points) 的旧语义保持不变（无锚点不重算）。
        self._traj_anchor: Dict[str, float] = {}
        self._traj_span_s: Dict[str, float] = {}
        # 视图侧 TLE -> Satrec 缓存（快转逐帧重算用；TLE 变即失效）
        self._view_satrec_cache: Dict[str, Tuple[str, str, object]] = {}
        # 最近一次按模拟时刻重算卫星位置的时刻（测试与面板一致性对拍用）
        self._sat_positions_unix: Optional[float] = None

        # === SkyInteractionHandler 所需的宿主属性 ===
        # Stellarium StelProjector 透视投影 (pinhole camera); FOV 0.5..120°。
        # 上限保持 120°：透视投影在 fov→180° 时 tan(fov/2) 发散、screen_scale
        # 趋 0，全天会塌到中心（不是可用的 180° 视图）。星表层本身支持
        # 180° 取数（star_catalog.select_for_view），但绘制层不宣称 180°。
        # 0.5° 下限让内行星圆面真的可分辨：金星最亮时视直径约 60″=0.0167°，
        # 700px 窗口下角半径 ≈ 11.7px（>= 2px 才会走 _draw_planet_disc），
        # 月亮视半径 ≈ 363px。滚轮是等比缩放（每步 ×exp(-0.5)），
        # 120°→0.5° 约 11 步、5°→0.5° 约 5 步，无跳变。
        self.view_state = ViewState(center_alt=90.0, fov_deg=120.0,
                                    min_fov=0.5, max_fov=120.0)
        self.projection = PerspectiveProjection()
        # 全天模式：独立 ViewState（隔离专业模式的指向/FOV）+ 立体投影。
        # 主视场仍是透视 120° 上限，只有该模式内部允许到 160°。
        self.ALL_SKY_FOV_DEG = 160.0
        self._all_sky_active = False
        self._all_sky_state = ViewState(
            center_alt=90.0, fov_deg=self.ALL_SKY_FOV_DEG,
            min_fov=0.5, max_fov=self.ALL_SKY_FOV_DEG)
        self._all_sky_projection = StereographicProjection()
        self._pro_state_backup = None
        self._pro_projection_backup = None
        self.setFocusPolicy(Qt.StrongFocus)
        # 状态卡滚动（内容超过可视高度时）：只动绘制偏移，不改任何数据。
        # 光标在卡内时滚轮滚卡，不在卡内时仍是 FOV 缩放（两套互不抢）。
        self._status_card_scroll = 0.0
        self._status_card_rect = QRectF()
        self._status_card_view_h = float(self.INFO_CARD_H)
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
        # 全天星表取数结果（绘制与拾取共用同一份；None=白天/无站/星表不可用）
        self._star_sel = None
        self._star_catalog_error: str = ""

        # 星座/星官图层：只存“开哪些体系”，数据、名称空间全部来自
        # skyengine.constellations / xingguan 的 overlay_layers()，本视图
        # 不自造连线、不合并两套体系的名称。默认仅 IAU 88 座。
        self._constellation_systems: Set[str] = {_constel.SYSTEM_IAU}
        self._constellation_error: str = ""
        # 折线拓扑/锚点按 system_id 首次准备后缓存，跨帧复用（不随 FOV 重建）
        self._constel_prepared: Dict[str, Dict[str, object]] = {}

        # 卫星目录索引 (SatelliteTracker 每帧喂入, 供信息卡/搜索)
        self._sat_catalog: Dict[str, Dict[str, object]] = {}
        # 本帧标签队列：((优先级, 锚点, [(行文本, 小字号, dy), ...], 颜色))。
        # 各层先登记，等全部层画完再统一避让：星名/座名/行星名/卫星块
        # 都不互相压字，且同一卫星的多行标签作为**一个块**整块取舍。
        self._label_items: List[Tuple[float, QPointF,
                                      List[Tuple[str, bool, float]],
                                      QColor]] = []
        # 当前选中天体的完整信息 (信息卡用)
        self._selected_info: Optional[Dict[str, object]] = None
        # 行星/恒星/深空天体选中态 (与 _hovered_object 互斥; 恒星/DSO 批
        # 逐步开放, 批 1 先行星)。body 为 {"kind","key","ra_deg","dec_deg",...}
        self._picked_body: Optional[Dict[str, object]] = None
        self._body_info: Optional[Dict[str, object]] = None
        # 信号出现点亮的卫星名集合（跟踪闭环下半场：频带里听到它，星就亮）
        self._signal_sats: set = set()
        # HUD 命中区 (绘制时刷新; None = 该控件当前不可点)
        self._info_card_rect: Optional[QRectF] = None
        self._info_close_rect: Optional[QRectF] = None
        self._fov_minus_rect: Optional[QRectF] = None
        self._fov_plus_rect: Optional[QRectF] = None

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

        # 显示层折射（只影响天球对象的绘制位置与视高度读数，不回写后端）。
        # 默认口径 1013.25 hPa / 10 °C，可用 set_refraction_params 覆盖。
        self._refraction_enabled: bool = True
        self._refraction_pressure_hpa: float = DISPLAY_REFRACTION_PRESSURE_HPA
        self._refraction_temperature_c: float = DISPLAY_REFRACTION_TEMPERATURE_C

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
        # 「跟踪」开关：checkable。按下后主窗口 500ms 闭环接管调谐
        # （多普勒自动校正 + 仰角<5° 自动停 + 过境后恢复频率）。
        self._track_btn = QPushButton("🎯 跟踪", self)
        self._track_btn.setCheckable(True)
        self._track_btn.setEnabled(False)
        self._track_btn.setToolTip(
            "闭环跟踪：每 500ms 按实时多普勒自动调谐，过境结束自动停止")
        self._track_btn.setStyleSheet(
            "QPushButton { background: " + _t.COLORS["card_2"] + "; color: "
            + _t.text("primary") + ";"
            " border:none; padding:4px 10px; border-radius:3px; }"
            "QPushButton:hover { background: " + _t.COLORS["card_hover"] + "; }"
            "QPushButton:checked { background: " + _t.COLORS["success"]
            + "; color: " + _t.COLORS["bg_dark"] + "; }"
            "QPushButton:disabled { background: " + _t.COLORS["card_1"] + "; color: "
            + _t.text("disabled") + "; }")
        self._track_btn.toggled.connect(self._on_track_toggled)
        self._track_btn.hide()
        # 「云台跟随」开关：checkable。未配置后端时置灰 + 诚实 tooltip。
        self._gimbal_btn = QPushButton("🛰 云台", self)
        self._gimbal_btn.setCheckable(True)
        self._gimbal_btn.setEnabled(False)
        self._gimbal_btn.setToolTip(
            "云台跟随：跟踪时同步驱动天线指向（无可用云台后端："
            "连接 ai-sdr-mini 或在 AI 对话里配置 rotctld）")
        self._gimbal_btn.setStyleSheet(
            "QPushButton { background: " + _t.COLORS["card_2"] + "; color: "
            + _t.text("primary") + ";"
            " border:none; padding:4px 10px; border-radius:3px; }"
            "QPushButton:hover { background: " + _t.COLORS["card_hover"] + "; }"
            "QPushButton:checked { background: " + _t.COLORS["light_accent"]
            + "; color: " + _t.COLORS["bg_dark"] + "; }"
            "QPushButton:disabled { background: " + _t.COLORS["card_1"] + "; color: "
            + _t.text("disabled") + "; }")
        self._gimbal_btn.toggled.connect(self._on_gimbal_toggled)
        self._gimbal_btn.hide()
        # 「摄像机锁定」开关：checkable。开启后视图中心每帧贴住选中卫星
        # （Stellarium StelMovementMgr::flagTracking 语义——摄像机跟星走，
        # 星空反向流动；与「跟踪」按钮的多普勒调谐闭环是两套东西）。
        self._camera_btn = QPushButton("📷 摄像机", self)
        self._camera_btn.setCheckable(True)
        self._camera_btn.setEnabled(False)
        self._camera_btn.setToolTip(
            "摄像机锁定：视图始终对准该卫星，星空随时间反向流动；"
            "拖拽/双击接管摄像机自动解锁")
        self._camera_btn.setStyleSheet(
            "QPushButton { background: " + _t.COLORS["card_2"] + "; color: "
            + _t.text("primary") + ";"
            " border:none; padding:4px 10px; border-radius:3px; }"
            "QPushButton:hover { background: " + _t.COLORS["card_hover"] + "; }"
            "QPushButton:checked { background: " + _t.COLORS["accent"]
            + "; color: " + _t.COLORS["bg_dark"] + "; }"
            "QPushButton:disabled { background: " + _t.COLORS["card_1"] + "; color: "
            + _t.text("disabled") + "; }")
        self._camera_btn.toggled.connect(self._on_camera_toggled)
        self._camera_btn.hide()
        # 摄像机锁定状态：目标星名（None=自由摄像机）+ 诚实退出通知
        self._camera_lock: Optional[str] = None
        # 最近一次锁定失败的具体原因（站址不可信 / 数据不可用），供诚实提示
        self._lock_denied_reason: Optional[str] = None
        self._camera_notice: str = ""
        self._camera_notice_until: float = 0.0
        # 当前已提示过的快转直贴倍率（None=未提示/已复位）；同一倍率只提示一次
        self._lock_snap_rate: Optional[float] = None
        # 上一次锁定目标 (az, el)：用于区分「逐帧目标位移」与「初始捕获跳转」
        self._lock_prev_target: Optional[Tuple[float, float]] = None
        # 待处理的一次性时间跳转（面板「±10m / 跳到此刻」后告知视图）：
        # 下一帧据此决定「目标仍可见→直接贴住并说明」还是「已落地→诚实解锁」
        self._pending_time_jump: Optional[str] = None
        # 选中卫星名（None=未选卫星）；定时器刷新多普勒读数
        self._tune_sat_name: Optional[str] = None
        # 跟踪环目标星名（跟踪中防止换星残留假状态）
        self._track_target_name: Optional[str] = None
        # 调谐环诚实通知（外部拒启/自动停止时说明「只有视角、未在调谐」）
        self._track_notice: str = ""
        self._track_notice_until: float = 0.0
        # 云台后端是否可用（由主窗口 set_gimbal_available 注入）
        self._gimbal_available = False
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
            # 地影状态（只消费后端 sunlit 字段，未知不画）：本体压暗 + 暗色虚线描边
            "sat_eclipse": QColor(_t.COLORS["gray_400"]),
            "sat_eclipse_ring": QColor(_t.COLORS["gray_300"]),
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
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start(33)

        # goto 缓动动画（Stellarium 式 moveToAltAzi）：33ms tick 内步进，
        # 墙钟驱动与帧率解耦。None = 无进行中的飞行。
        self._goto_anim = None
        self._goto_start_ms = 0.0
        self._goto_clock = lambda: time.monotonic() * 1000.0
        # 选中标记呼吸时钟（monotonic 秒）；_refresh_timer 30fps 重绘驱动
        # 呼吸动画。留成缝以便测试注入固定时间（不绑墙钟）。
        self._breath_clock = lambda: time.monotonic()

    def _on_refresh_tick(self):
        """33ms 节拍：快转重算位置/轨迹 + 步进 goto 动画 + 摄像机锁定跟随。"""
        self._refresh_fast_time_positions()
        self._step_goto_anim()
        self._apply_camera_lock()
        self.update()

    # ------------------------------------------------------------------ #
    # 快转（|rate|>1）一致性：位置、轨迹、过境面板都要跟当前模拟时刻同拍
    # ------------------------------------------------------------------ #
    def _time_rate(self) -> float:
        """当前时间倍率；取不到引擎时按 1.0（不启用快转分支）。"""
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            return float(get_time_engine().get_rate())
        except Exception:
            return 1.0

    def _view_satrec(self, name: str, line1: str, line2: str):
        """视图侧 Satrec 缓存（TLE 行变化即失效，不把旧轨道当新数据）。"""
        hit = self._view_satrec_cache.get(name)
        if hit is not None and hit[0] == line1 and hit[1] == line2:
            return hit[2]
        try:
            from sgp4.api import Satrec
            sat = Satrec.twoline2rv(line1, line2)
        except Exception:
            self._view_satrec_cache.pop(name, None)
            return None
        self._view_satrec_cache[name] = (line1, line2, sat)
        return sat

    def _refresh_fast_time_positions(self) -> None:
        """快转下按当前模拟时刻重算卫星点与过期轨迹（1x 完全不走这里）。

        1x 时外部 5s 刷新已把位置误差压在「5s 视运动」内（LEO <0.01°）；
        快转时 5s 墙钟 = 5×rate 模拟秒，天空点与轨迹会明显过期。此处用
        同一 TLE + 同一坐标链（orbit._state_from_satrec）现算，不画旧帧。
        无 TLE/无法解算 → 撤掉该轨迹（宁可不画，不画过期曲线）。
        """
        rate = self._time_rate()
        if abs(rate) <= 1.0:
            self._lock_snap_rate = None
            return
        obs = self._observer
        if obs is None or not self._sat_catalog:
            return
        try:
            from mbdsdr_ai import orbit
        except Exception:
            return
        t_unix = self._engine_unix()
        jd = t_unix / 86400.0 + 2440587.5
        self._sat_positions_unix = t_unix

        # --- 卫星点：只动带真实 TLE 的卫星对象，其余类型原样保留 ---
        for obj in self._objects:
            if getattr(obj, "obj_type", "") != "satellite":
                continue
            meta = self._sat_catalog.get(obj.name)
            if not meta or not meta.get("line1") or not meta.get("line2"):
                continue
            sat = self._view_satrec(obj.name, str(meta["line1"]),
                                    str(meta["line2"]))
            if sat is None:
                obj.visible = False
                continue
            try:
                st = orbit._state_from_satrec(
                    sat, obj.name, jd, obs["lat"], obs["lon"],
                    obs.get("alt_m", 0.0) / 1000.0)
            except Exception:
                st = None
            if st is None:
                obj.visible = False
                continue
            alt_d = float(st["elevation"])
            dist_km = float(st["range_km"])
            rr = float(st.get("range_rate_kms", 0.0))
            obj.azimuth_deg = float(st["azimuth"])
            obj.elevation_deg = alt_d
            obj.visible = True
            # 与 SatelliteTracker 喂入的文案同口径（信息卡不因刷新来源而变）
            obj.description = format_satellite_description(alt_d, dist_km, rr)

        # --- 轨迹：锚点落后当前模拟时刻超过 ~2 帧模拟推进 → 重算未来 10 分钟 ---
        stale_s = min(self.TRAJ_MAX_STALE_S,
                      max(self.TRAJ_MIN_STALE_S, 2.0 * self.TRAJ_FRAME_S * abs(rate)))
        for name in list(self._trajectories.keys()):
            anchor = self._traj_anchor.get(name)
            if anchor is None or abs(t_unix - anchor) < stale_s:
                continue
            meta = self._sat_catalog.get(name)
            sat = None
            if meta and meta.get("line1") and meta.get("line2"):
                sat = self._view_satrec(name, str(meta["line1"]),
                                        str(meta["line2"]))
            if sat is None:
                # 无法重算 → 不画过期曲线
                self._trajectories.pop(name, None)
                self._traj_anchor.pop(name, None)
                self._traj_span_s.pop(name, None)
                continue
            span = float(self._traj_span_s.get(name, self.TRAJ_SPAN_S))
            step = 60.0
            pts: List[Tuple[float, float]] = []
            k = 0
            while k * step <= span + 1e-6:
                jd_k = (t_unix + k * step) / 86400.0 + 2440587.5
                try:
                    st = orbit._state_from_satrec(
                        sat, name, jd_k, obs["lat"], obs["lon"],
                        obs.get("alt_m", 0.0) / 1000.0)
                except Exception:
                    st = None
                if st is not None:
                    pts.append((float(st["azimuth"]), float(st["elevation"])))
                k += 1
            if pts:
                self._trajectories[name] = pts
                self._traj_anchor[name] = t_unix
            else:
                self._trajectories.pop(name, None)
                self._traj_anchor.pop(name, None)
                self._traj_span_s.pop(name, None)

        # --- 过境面板：已过去的峰值不再冒充「未来过境」 ---
        self._upcoming_passes = [
            p for p in self._upcoming_passes
            if float(p.get("peak_unix", t_unix + 1.0)) >= t_unix
        ]

    # ------------------------------------------------------------------ #
    # 摄像机锁定（Stellarium StelMovementMgr::flagTracking 同款语义）
    # ----------------------------------------------------------------------------
    # Stellarium 的做法（reference/stellarium/src/core/StelMovementMgr.cpp）：
    #   - setFlagTracking(true) 要求有选中对象，先 moveToObject 缓动飞入；
    #   - updateMotion 每帧取选中对象的【当前】地平坐标，直接把视图方向
    #     重瞄到它（setViewDirection）——目标静止、星空反向流动；
    #   - 任何用户手动运动（panView/dragView/smoothPan）立即
    #     setFlagTracking(false)：用户永远接管，跟踪不与输入打架。
    # 我们的对应（坐标链与卫星绘制一致：TLE + sgp4，真实数据）：
    #   - _camera_lock 存目标星名；每 33ms tick 用当前时刻重算目标 az/el，
    #     指数趋近把 view_state 中心贴上去（平滑策略见 _apply_camera_lock）；
    #   - 拖拽/触屏平移/双击 goto/重置摄像机 → 立即解锁；
    #   - 目标落地/数据失效 → 诚实解锁并提示，画面不停在无意义角度假跟踪。
    # ------------------------------------------------------------------------
    @property
    def camera_lock_target(self) -> Optional[str]:
        """当前摄像机锁定目标星名；None = 自由摄像机。"""
        return self._camera_lock

    @property
    def track_target_name(self) -> Optional[str]:
        """当前调谐跟踪目标星名；None = 未在跟踪（与摄像机锁定独立）。"""
        return self._track_target_name if self._track_btn.isChecked() else None

    def _track_active_name(self) -> Optional[str]:
        """跟踪环实际目标：勾选态 + 目标名；缺一即视为未跟踪。"""
        if not self._track_btn.isChecked():
            return None
        return self._track_target_name or self._tune_sat_name

    def loop_state_lines(self) -> List[str]:
        """两个环的并排诚实状态（HUD 与状态卡共用，避免两处口径漂移）。

        摄像机锁定与多普勒调谐语义独立：一个开着、另一个没开时必须明说
        「仅视角 / 仅调谐」，不许让界面看起来两个都在跑。
        """
        cam = self._camera_lock
        trk = self._track_active_name()
        lines: List[str] = []
        if cam:
            lines.append(f"摄像机锁定: {cam}")
        elif trk:
            lines.append("摄像机: 未锁定（仅调谐）")
        if trk:
            lines.append(f"调谐跟踪: {trk}")
        elif cam:
            lines.append("调谐跟踪: 未开启（仅视角，未在调谐）")
        return lines

    def set_camera_lock(self, name: Optional[str]) -> bool:
        """设定/解除摄像机锁定。成功锁定返回 True；目标不可解算返回 False。"""
        if name is None:
            self._unlock_camera("")
            return True
        if self._lock_target_azalt(name) is None:
            why = self._lock_denied_reason
            # 站址不可信时如实说原因（与过境预测同一守卫），不笼统报「数据不可用」
            self._camera_notice = (
                f"无法锁定：站址不可信（{why}），拒绝用该站址算卫星位置"
                if why else f"无法锁定：{name} 轨道数据不可用")
            self._camera_notice_until = time.time() + 3.0
            self.update()
            return False
        self._camera_lock = str(name)
        self._lock_snap_rate = None
        self._lock_prev_target = None
        self._pending_time_jump = None
        self._sync_camera_btn()
        self.update()
        return True

    def _on_camera_toggled(self, on: bool):
        """按钮开关 → 锁定当前选中卫星 / 解锁。"""
        if on:
            name = self._tune_sat_name or (
                self._hovered_object.name if self._hovered_object else None)
            if not name:
                # 无选中：复位开关，不残留假状态
                self._camera_notice = "无法锁定：未选中卫星或轨道数据不可用"
                self._camera_notice_until = time.time() + 3.0
                self._camera_lock = None
                self._sync_camera_btn()
            elif not self.set_camera_lock(name):
                # set_camera_lock 已按真实原因写入提示（站址不可信/轨道数据
                # 不可用）；这里只复位开关，不覆盖成笼统文案。
                self._camera_lock = None
                self._sync_camera_btn()
        else:
            self._unlock_camera("")

    def _unlock_camera(self, notice: str):
        """解除锁定；notice 非空时显示诚实退出原因（3s 淡出）。"""
        was = self._camera_lock
        self._camera_lock = None
        self._lock_snap_rate = None
        self._lock_prev_target = None
        self._pending_time_jump = None
        if notice and was:
            self._camera_notice = notice
            self._camera_notice_until = time.time() + 3.0
        self._sync_camera_btn()
        self.update()

    def _sync_camera_btn(self):
        """按钮状态与 _camera_lock 对齐（不递归触发 toggled）。"""
        locked = self._camera_lock is not None
        if self._camera_btn.isChecked() != locked:
            self._camera_btn.blockSignals(True)
            self._camera_btn.setChecked(locked)
            self._camera_btn.blockSignals(False)

    def _station_trust_reason(self) -> Optional[str]:
        """站址可信度（复用 mbdsdr_ai.station 的唯一守卫，不另立阈值）。

        观测站未设置/0,0 占位/海上/无来源凭据 -> 返回原因字符串；
        可信返回 None。摄像机锁定与过境预测共用同一判据（P11 红线）。
        """
        obs = self._observer
        if obs is None:
            return "观测站未设置"
        try:
            from mbdsdr_ai.station import StationFix, evaluate_station
            ev = evaluate_station(StationFix(
                obs["lat"], obs["lon"], obs.get("alt_m", 0.0),
                source="user"))
        except Exception as e:  # noqa: BLE001 - 守卫失败要留痕，不能默认可信
            return f"站址校验失败（{type(e).__name__}: {e}）"
        if ev["trusted"]:
            return None
        return "；".join(ev["reasons"]) or "站址不可信"

    def _lock_target_azalt(self, name: str):
        """目标星当前 az/el（与卫星绘制同一坐标链：TLE + sgp4 现算）。

        返回 (az, el)；站址不可信/TLE 缺失/解算失败返回 None（诚实）。
        站址不可信时把具体原因写入 ``_lock_denied_reason``（拒绝用默认站址算）。
        """
        self._lock_denied_reason = None
        reason = self._station_trust_reason()
        if reason is not None:
            self._lock_denied_reason = reason
            return None
        meta = self._sat_catalog.get(name)
        obs = self._observer
        if meta is None or obs is None:
            return None
        line1, line2 = meta.get("line1"), meta.get("line2")
        if not line1 or not line2:
            return None
        try:
            from sgp4.api import Satrec
            from mbdsdr_ai import orbit
            sat = Satrec.twoline2rv(line1, line2)
            st = orbit._state_from_satrec(
                sat, name, self._current_jd(),
                obs["lat"], obs["lon"], obs.get("alt_m", 0.0) / 1000.0)
        except Exception:
            return None
        if st is None:
            return None
        return float(st["azimuth"]), float(st["elevation"])

    def notify_time_jump(self, label: str) -> None:
        """一次性时间跳转（面板 ±10m / 跳到此刻）后由面板调用。

        确定语义（下一帧在 ``_apply_camera_lock`` 里执行）：
          * 目标仍在地平线上 -> 继续锁定，但**直接贴住**新时刻的位置并提示，
            不做「瞬移后缓慢追上」的假跟踪；
          * 目标已落地/轨道失效 -> 诚实解锁并说明是跳转所致；
          * 未锁定时只复位快转直贴提示状态，无其它副作用。
        """
        self._pending_time_jump = str(label)
        self._lock_prev_target = None
        self._lock_snap_rate = None

    def _apply_camera_lock(self):
        """33ms tick：把视图中心贴到锁定目标的当前 az/el。

        平滑策略：指数趋近 center += delta * 0.25（每 tick 33ms）。静止目标
        无稳态误差；运动目标稳态滞后 ≈ 3 倍单帧目标位移（LEO 在 30fps 下
        约 0.1-0.15°，亚度级）；方位角按最短路径。
        快转（|rate|>1）且单帧目标位移超过 LOCK_SMOOTH_MAX_STEP_DEG 时，
        平滑跟随必然跟不上（×300 一帧模拟 10s，目标可动数度到数十度）→
        改为逐帧直贴（k=1），并一次性诚实提示「跟不上，已直贴」；位移超过
        LOCK_FAST_SLIDE_STEP_DEG 时提示画面只能按帧跳进（不改口、不冒充平滑）。
        目标落地（el<0）/数据失效 → 诚实解锁并提示。

        时间跳转（面板一次性跳时）单独处理：目标仍可见就按新时刻直接贴住
        并提示；目标已落地则解锁并说明——不把大跨度跳转伪装成平滑跟踪。
        """
        if self._camera_lock is None:
            self._pending_time_jump = None
            return
        name = self._camera_lock
        jump = self._pending_time_jump
        self._pending_time_jump = None
        res = self._lock_target_azalt(name)
        if res is None:
            why = self._lock_denied_reason
            if why:
                msg = f"摄像机已解锁：站址不可信（{why}），拒绝继续跟踪"
            else:
                msg = f"摄像机已解锁：{name} 轨道数据失效"
            if jump:
                msg += f"（时间跳转 {jump} 后）"
            self._unlock_camera(msg)
            return
        az, el = res
        if el < 0.0:
            msg = (f"摄像机已解锁：时间跳转 {jump} 后 {name} 已落入地平线下"
                   if jump else f"摄像机已解锁：{name} 落入地平线下")
            self._unlock_camera(msg)
            return
        vs = self.view_state
        if jump:
            # 大跨度跳转：锁定语义明确——目标还在天上就继续锁，但直接贴住，
            # 不缓动（缓动会看起来像「瞬移后假装还在慢慢跟」）。
            vs.center_az = az % 360.0
            vs.center_alt = max(-89.9999, min(89.9999, el))
            self._lock_prev_target = (az, el)
            self._lock_snap_rate = None
            self._camera_notice = (
                f"时间跳转 {jump}：{name} 仍在天空 "
                f"{format_angle_deg(el)}，已按新时刻直接贴住并继续锁定")
            self._camera_notice_until = time.time() + 4.0
            return
        d_az = ((az - vs.center_az + 540.0) % 360.0) - 180.0
        d_alt = el - vs.center_alt
        if abs(d_az) < 1e-6 and abs(d_alt) < 1e-6:
            return
        rate = self._time_rate()
        k = self.LOCK_SMOOTH_K
        # 逐帧真实目标位移（首帧/换星后无基准 → 0，不把初始捕获跳转谎报成跟不上）
        prev = self._lock_prev_target
        move_deg = (0.0 if prev is None
                    else angular_separation_deg(prev[0], prev[1], az, el))
        self._lock_prev_target = (az, el)
        if abs(rate) > 1.0:
            step_deg = angular_separation_deg(
                vs.center_az, vs.center_alt, az, el)
            if step_deg > self.LOCK_SMOOTH_MAX_STEP_DEG:
                k = 1.0                       # 逐帧直贴：贴住中心，不滞后
                if move_deg > self.LOCK_SMOOTH_MAX_STEP_DEG:
                    self._note_fast_lock(rate, move_deg)
        vs.center_az = (vs.center_az + d_az * k) % 360.0
        vs.center_alt = max(-89.9999, min(89.9999, vs.center_alt + d_alt * k))

    def _note_fast_lock(self, rate: float, step_deg: float) -> None:
        """快转直贴的一次性诚实提示（同一倍率只提示一次，解锁/换目标复位）。

        不改口成「平滑」：说清楚跟不上（单帧位移多大）、改成了逐帧直贴；
        位移大到直贴也只能按帧跳进时，明确建议降速观察。
        """
        if self._lock_snap_rate == rate:
            return
        self._lock_snap_rate = rate
        label = (f"×{abs(rate):g}" if rate > 0 else f"倒放×{abs(rate):g}")
        text = (f"摄像机跟踪：{label} 快转下单帧目标位移 "
                f"{format_angle_deg(step_deg)}，"
                f"平滑跟随跟不上，已改为逐帧直贴")
        if step_deg > self.LOCK_FAST_SLIDE_STEP_DEG:
            text += "（画面按帧跳进，建议降速观察）"
        self._camera_notice = text
        self._camera_notice_until = time.time() + 4.0

    def goto_animated(self, az_deg: float, alt_deg: float,
                      fov_deg: Optional[float] = None,
                      duration_ms: float = 200.0) -> None:
        """Stellarium 式 goto：带 OutCubic 缓动的居中飞行。

        与帧率解耦（真实墙钟毫秒推进）：帧率波动下终点一致。
        新飞行自然替换旧飞行；拖拽/滚轮/触屏手势会打断（见各事件处理）。
        """
        from mbdsdr_ai.sky_interaction import GotoAnimation
        self._goto_anim = GotoAnimation(self.view_state.copy(),
                                        az_deg, alt_deg, fov_deg,
                                        duration_ms)
        self._goto_start_ms = self._goto_clock()
        self.update()

    def _cancel_goto_anim(self) -> None:
        """打断进行中的 goto 飞行（用户主动接管视角时）。"""
        self._goto_anim = None

    def _step_goto_anim(self) -> None:
        anim = self._goto_anim
        if anim is None:
            return
        vs, done = anim.step(self._goto_clock() - self._goto_start_ms)
        self.view_state = vs
        if done:
            self._goto_anim = None
        self.update()

    # ========================================================================
    # 自包含 UI: 搜索框 / 信息卡 / FOV 显示 (Stellarium 顶栏)
    # ========================================================================
    def _build_search_widgets(self):
        """顶部搜索框 + 分类结果下拉。"""
        from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem
        self._QLineEdit = QLineEdit
        self._QListWidget = QListWidget
        self._QListWidgetItem = QListWidgetItem
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
        self._view_satrec_cache.clear()
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
                # TLE 可追溯（B2）：来源/历元透传（缺省空串，不编造）
                "tle_source": str(e.get("tle_source", "")),
                "tle_epoch": str(e.get("tle_epoch", "")),
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
            self._on_station_lost()
        else:
            self._observer = {
                "lat": float(lat),
                "lon": float(lon),
                "alt_m": float(alt) if alt is not None else 0.0,
            }
            if self._data_source == "none":
                self.set_data_source("real")
        self.update()

    def _on_station_lost(self) -> None:
        """站址失效：两个环各自收口，不留单边假状态。

        摄像机锁定依赖站址算 az/el；观测站没了就无法继续，必须解锁并说
        原因。调谐跟踪同理停环（发信号让主窗口停 500ms 轮询与云台）。
        """
        if self._camera_lock is not None:
            self._unlock_camera(
                "摄像机已解锁：观测站未设置或已失效，无法继续跟踪")
        self._stop_track_ui()
        self._stop_gimbal_ui()
        self._update_tune_button()

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
            self._on_station_lost()

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
        """喂入未来过境列表（rise 时刻 HH:MM:SS(UTC) + max_alt + duration_min）。"""
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

    def set_trajectory(self, name: str, points: List[Tuple[float, float]],
                       anchor_unix: Optional[float] = None,
                       span_s: float = 600.0):
        """设置卫星轨迹点（az, el）。

        anchor_unix 为这批点的基准模拟时刻：给了锚点后，快转下视图会按当前
        模拟时刻重算（过期曲线不画）。不给锚点 = 旧语义（外部喂入即画，
        视图不自行重算），保持既有调用方行为不变。
        """
        self._trajectories[name] = points
        if anchor_unix is None:
            self._traj_anchor.pop(name, None)
            self._traj_span_s.pop(name, None)
        else:
            self._traj_anchor[name] = float(anchor_unix)
            self._traj_span_s[name] = float(span_s)
        self.update()

    def clear_trajectories(self):
        self._trajectories.clear()
        self._traj_anchor.clear()
        self._traj_span_s.clear()
        self.update()

    def clear(self):
        """清空所有天空图数据（卫星/轨迹/热力图/天体/GNSS），保留视图设置。

        这是外部重置天空图的标准入口。清空后天空图回到空状态提示。
        """
        self._objects.clear()
        self.sky_objects = self._objects
        self._trajectories.clear()
        self._traj_anchor.clear()
        self._traj_span_s.clear()
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
        self._signal_sats.clear()
        self._observer = None
        self._data_source = "none"
        self._satellites_connected = False
        self._tle_loaded = False
        self.update()

    def set_sat_signal(self, name: str, present: bool):
        """信号出现点亮：跟踪闭环在频带里听到该星时置 True，星标加辉光。

        无数据/信号消失置 False（调用方负责按 SNR 回差判定），不自主猜测。
        """
        name = str(name)
        if present:
            self._signal_sats.add(name)
        else:
            self._signal_sats.discard(name)
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

    def set_show_eqgrid(self, show: bool):
        """赤道网格（RA 时圈 + 赤纬圈）开关。"""
        self._show_eqgrid = bool(show)
        self.update()

    def reset_view(self):
        """重置摄像机：回到天顶居中 + FOV=120°，并解除摄像机锁定。

        （「摄像机」 = 用户可见的视图/视角概念；内部标识仍叫 view_state，
        见 _camera_lock 区块的术语映射注释。）
        """
        self._unlock_camera("")
        self.view_state.look_at(0.0, 90.0)
        self.view_state.zoom_to(120.0)
        self._rotation = 0.0
        self.update()

    def enter_horizon_view(self, az_deg: float = 0.0):
        """星象仪模式入口视角：朝地平（与 Stellarium 默认构图一致——
        地景剪影与辉光带入画；天顶俯视留给 RF 全域工具）。"""
        self._cancel_goto_anim()
        self.view_state.look_at(float(az_deg) % 360.0, 20.0)
        self.view_state.zoom_to(120.0)
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

    def _engine_unix(self) -> float:
        """当前模拟 Unix 秒（同 _current_jd 的引擎推进，只取秒数）。"""
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            eng = get_time_engine()
            eng.tick()
            return float(eng.now_unix())
        except Exception:
            return time.time()

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
        # 2) 恒星: 亮星星表按名匹配 (西名 + 中国星名双匹配, 读懂层)
        for s in _stars.BRIGHT_STARS:
            zh = ""
            c = _stars.star_culture(s.name)
            if c:
                zh = c.get("zh", "")
            if text in s.name.lower() or (zh and text in zh.lower()):
                self._search_results.append({
                    "kind": "star", "kind_label": "Star",
                    "label": zh if zh else s.name,
                    "sub": f"Star · {s.name}" if zh else "Star",
                    "ra": s.ra_deg, "dec": s.dec_deg, "vmag": s.vmag,
                    "name": s.name,
                })
        # 3) 行星: 西名 + 中文名双匹配 (文化对位层)
        for en, style in _PLANET_STYLE.items():
            zh = str(style["zh"])
            if text in en or text in zh.lower():
                self._search_results.append({
                    "kind": "planet", "kind_label": "Planet",
                    "label": f"{zh} {en.capitalize()}", "sub": "Planet",
                    "key": en, "zh": zh,
                })
        # 4) 梅西耶天体: M 编号匹配 (深空搜索)
        if re.match(r"^m\s*\d{1,3}$", text):
            from mbdsdr_ai.astronomy import dso as _dso
            for e in _dso.BUILTIN_CATALOG:
                if text.replace(" ", "") == e.name.lower():
                    self._search_results.append({
                        "kind": "dso", "kind_label": "Deep Sky Object",
                        "label": e.name,
                        "sub": f"{e.dso_type.replace('_', ' ').title()}",
                        "key": e.name, "dso_type": e.dso_type,
                        "con": e.constellation,
                    })
        # 填充下拉
        self._search_popup.clear()
        for r in self._search_results[:40]:
            icon = {"space_station": "🛰", "satellite": "●",
                    "weather": "◉", "star": "✦", "planet": "◐",
                    "dso": "◎"}.get(r["kind"], "•")
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
        # 卫星 -> 统一选中入口（清 body/发 object_clicked/居中, 不绕过）
        if r["kind"] in ("space_station", "satellite", "weather", "amateur"):
            for obj in self._objects:
                if obj.name == r["obj_name"]:
                    self.on_object_picked(obj)
                    return
        # 恒星 -> 居中 + 选中出卡 (点星即读)
        if r["kind"] == "star" and self._ground_station() is not None:
            self.on_body_picked({"kind": "star", "key": r["name"]})
            jd = self._current_jd()
            gs = self._ground_station()
            v = j2000_to_altaz(vec_from_radec(r["ra"], r["dec"]), gs, jd)
            from mbdsdr_ai.celestial_geometry import azalt_from_vec
            az, alt = azalt_from_vec(v)
            self.goto_animated(az, alt)
        # 行星 -> 居中 + 选中出信息卡 (Stellarium 搜索行星同款交互)
        if r["kind"] == "planet" and self._ground_station() is not None:
            self.on_body_picked({"kind": "planet", "key": r["key"],
                                 "zh": r["zh"]})
            jd = self._current_jd()
            gs = self._ground_station()
            pos = self._planet_positions(jd).get(r["key"])
            if pos is not None:
                from mbdsdr_ai.celestial_geometry import azalt_from_vec
                az, alt = azalt_from_vec(
                    j2000_to_altaz(vec_from_radec(pos.ra_deg, pos.dec_deg),
                                   gs, jd))
                self.goto_animated(az, alt)
        # 梅西耶天体 -> 居中 + 选中出卡 (标记环+角大小)
        if r["kind"] == "dso" and self._ground_station() is not None:
            body = {"kind": "dso", "key": r["key"],
                    "dso_type": r.get("dso_type"), "con": r.get("con")}
            e = self._dso_entry(r["key"])
            if e is not None:
                body["ra_deg"], body["dec_deg"] = e.ra_deg, e.dec_deg
            self.on_body_picked(body)
            if e is not None:
                jd = self._current_jd()
                gs = self._ground_station()
                from mbdsdr_ai.celestial_geometry import azalt_from_vec
                az, alt = azalt_from_vec(
                    j2000_to_altaz(vec_from_radec(e.ra_deg, e.dec_deg),
                                   gs, jd))
                self.goto_animated(az, alt)

    # ------------------------------------------------------------------ #
    # 选中信息卡 (Stellarium 规格 10.4)
    # ------------------------------------------------------------------ #
    def _build_selected_info(self, obj: SkyObject) -> None:
        """根据选中卫星构建信息卡字段: NORAD/COSPAR/别名/Dist/RaDec/AzAlt/
        Magnitude(占位)/Visibility(Rise/Set)。无值给占位, 不编造。"""
        info: Dict[str, object] = {
            "title": obj.name, "kind_label": "Satellite",
            "obj_type": obj.obj_type,
            # 受晒状态由后端快照提供；None（无太阳数据/半影）原样保留，
            # 文案映射见 sunlit_state_label()。sunlit_status 为
            # Illumination.status 原文（有则优先，半影不被压成受晒）。
            "sunlit": getattr(obj, "sunlit", None),
            "sunlit_status": getattr(obj, "sunlit_status", None),
            # 内部字段，不渲染：解释刚进/出影时状态跳变用。
            "boundary_uncertainty_s": getattr(
                obj, "boundary_uncertainty_s", None),
            "norad": None, "cospar": "", "aliases": [],
            "dist_km": self._parse_distance_km(obj),
            "az": obj.azimuth_deg, "alt": obj.elevation_deg,
            "ra": None, "dec": None, "mag": None,
            "rise": None, "set": None,
            "downlink_mhz": None, "downlink_mode": "",
            "culture": "",
        }
        meta = self._sat_catalog.get(obj.name)
        if meta:
            info["norad"] = meta["norad"]
            info["cospar"] = meta["cospar"]
            info["aliases"] = meta["aliases"]
            info["kind_label"] = meta["kind_label"]
            # TLE 可追溯（B2）：来源 + 历元进信息卡；缺省空串如实缺省
            info["tle_source"] = str(meta.get("tle_source") or "")
            info["tle_epoch"] = str(meta.get("tle_epoch") or "")
        # 链路目录 sat_links：下行频点/体制 + 一句话星性注记（点星即听
        # 的文化层；目录没有的星如实缺省，不编造）。
        try:
            from mbdsdr_ai.sat_links import find_tuning_for
            link = find_tuning_for(obj.name)
        except Exception:
            link = None
        if link is not None:
            dl = link["downlink"]
            info["downlink_mhz"] = float(dl["freq_hz"]) / 1e6
            info["downlink_mode"] = dl.get("mode", "")
            info["culture"] = link["entry"].get("culture", "")
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
            # 选中失效：两个环各自诚实收口（不把「数据没了」伪装成仍在跟）
            if self._camera_lock is not None:
                self._unlock_camera(
                    "摄像机已解锁：观测站未设置或已失效，无法继续跟踪"
                    if not gs_ready else
                    "摄像机已解锁：选中目标轨道数据不可用")
            self._stop_track_ui()  # 选中失效：跟踪同步停，不残留假状态
            self._stop_gimbal_ui()  # 云台跟随随跟踪一起停
            self._tune_sat_name = None
            self._tune_btn.hide()
            self._track_btn.hide()
            self._gimbal_btn.hide()
            self._camera_btn.hide()
            self._doppler_label.hide()
            self._tune_refresh_timer.stop()
            return
        name = (self._selected_info or {}).get("title")
        # 换星：若正在跟踪别的星，先停（停跟踪是主窗口环的职责，发信号）。
        # 确定语义：换目标不把调谐偷偷改到新星，必须由用户重新开启；
        # 摄像机锁定则跟随选中（下方重锁），两者互不连带。
        if self._track_btn.isChecked() and name != self._track_target_name:
            prev_target = self._track_target_name or "上一目标"
            self._stop_track_ui()
            self._stop_gimbal_ui()
            self._track_notice = (
                f"调谐跟踪已停止：目标由 {prev_target} 切换到 {name}，"
                f"需重新开启跟踪")
            self._track_notice_until = time.time() + 4.0
        # 换星：摄像机锁跟着选中走（Stellarium 语义：跟踪绑定选中对象）
        if self._camera_lock is not None and name != self._camera_lock:
            if not self.set_camera_lock(name):
                why = self._lock_denied_reason
                self._unlock_camera(
                    f"摄像机已解锁：站址不可信（{why}），拒绝跟踪新目标"
                    if why else "摄像机已解锁：新选中目标轨道数据不可用")
        self._tune_sat_name = name
        self._tune_btn.show()
        self._track_btn.show()
        self._gimbal_btn.show()
        self._camera_btn.show()
        self._camera_btn.setEnabled(True)
        self._gimbal_btn.setEnabled(self._gimbal_available)
        self._doppler_label.show()
        # 下行频点解析不了（目录外卫星）→ 置灰，诚实空态不画饼
        tunable = self._compute_tune() is not None
        self._tune_btn.setEnabled(tunable)
        # 跟踪中不随瞬时解析失败抖动禁用（环内多普勒已经接管）
        if not self._track_btn.isChecked():
            self._track_btn.setEnabled(tunable)
        self._refresh_tune_doppler()
        self._tune_refresh_timer.start()
        self._position_tune_overlay()

    def _stop_track_ui(self) -> None:
        """跟踪按钮回到未跟踪态；若在跟踪则发停止信号（供主窗口停环）。"""
        if self._track_btn.isChecked():
            self._track_btn.blockSignals(True)
            self._track_btn.setChecked(False)
            self._track_btn.blockSignals(False)
            self.track_toggled.emit(False, self._track_target_name or "", 0.0)
        self._track_target_name = None
        self._track_btn.setEnabled(self._compute_tune() is not None)

    def set_tracking_checked(self, on: bool) -> None:
        """主窗口反向同步（过境自动停/启动失败时复位按钮），不发射信号。

        诚实边界：若按钮原本是勾选态、外部却回写 False（无 SDR/启动被拒/
        过境结束），说明「调谐环没在跑」；此时若摄像机仍锁定，必须讲清
        「只有视角在跟，没在调谐」，不能两个环看起来都在跑。
        """
        was_checked = self._track_btn.isChecked()
        self._track_btn.blockSignals(True)
        self._track_btn.setChecked(on)
        self._track_btn.blockSignals(False)
        if on:
            if self._track_target_name is None:
                self._track_target_name = self._tune_sat_name
            self._track_notice = ""
            self._track_notice_until = 0.0
            self._track_btn.setEnabled(True)
            self._track_btn.show()
            self._position_tune_overlay()
        else:
            target = self._track_target_name or self._tune_sat_name
            self._track_target_name = None
            if was_checked and target:
                self._track_notice = (
                    f"调谐跟踪已停止（或未启动）：{target} 当前未在调谐，"
                    + ("摄像机锁定仍保持（仅视角跟随，不会自动换频）"
                       if self._camera_lock else "摄像机也未锁定"))
                self._track_notice_until = time.time() + 4.0
            # 跟踪环停了，云台跟随不能再勾着（避免看起来还在跟）
            self._stop_gimbal_ui()

    def _on_track_toggled(self, checked: bool):
        """跟踪开关：check 时校验目录频点后发信号；uncheck 发停止。"""
        if not checked:
            self.track_toggled.emit(False, self._track_target_name or "", 0.0)
            self._track_target_name = None
            # 云台跟随挂靠跟踪环：跟踪停 → 跟随停（不残留「已在跟」的勾选）
            self._stop_gimbal_ui()
            return
        res = self._compute_tune()
        if res is None:
            # 目录外卫星/无观测站：诚实拒绝，按钮弹回
            self._track_btn.blockSignals(True)
            self._track_btn.setChecked(False)
            self._track_btn.blockSignals(False)
            return
        name = str(res.get("satellite") or self._tune_sat_name or "")
        self._track_target_name = name
        self._track_notice = ""
        self._track_notice_until = 0.0
        self.track_toggled.emit(True, name, float(res.get("base_freq_hz", 0.0)))

    # ------------------------------------------------------------------ #
    # 「云台跟随」开关：跟随主窗口跟踪环同拍驱动天线
    # ------------------------------------------------------------------ #
    def set_gimbal_available(self, available: bool) -> None:
        """主窗口注入云台后端可用性；不可用置灰 + 诚实 tooltip。"""
        self._gimbal_available = bool(available)
        if not available and self._gimbal_btn.isChecked():
            self._stop_gimbal_ui()
        self._gimbal_btn.setEnabled(bool(available) and
                                    self._track_btn.isVisibleTo(self))
        if available:
            self._gimbal_btn.setToolTip(
                "云台跟随：跟踪时同步驱动天线指向卫星（每 500ms 一拍）")
        else:
            self._gimbal_btn.setToolTip(
                "云台跟随：跟踪时同步驱动天线指向（无可用云台后端："
                "连接 ai-sdr-mini 或在 AI 对话里配置 rotctld）")

    def _stop_gimbal_ui(self) -> None:
        """云台按钮回到未跟随态；若在跟随则发停止信号。"""
        if self._gimbal_btn.isChecked():
            self._gimbal_btn.blockSignals(True)
            self._gimbal_btn.setChecked(False)
            self._gimbal_btn.blockSignals(False)
            self.gimbal_follow_toggled.emit(False)

    def set_gimbal_follow_checked(self, on: bool) -> None:
        """主窗口反向同步（跟踪停/后端掉线时复位），不发射信号。"""
        self._gimbal_btn.blockSignals(True)
        self._gimbal_btn.setChecked(on)
        self._gimbal_btn.blockSignals(False)

    def _on_gimbal_toggled(self, checked: bool):
        if not checked:
            self.gimbal_follow_toggled.emit(False)
            return
        if not self._gimbal_available:
            # 不可用却收到 check（不应发生）：诚实弹回
            self._gimbal_btn.blockSignals(True)
            self._gimbal_btn.setChecked(False)
            self._gimbal_btn.blockSignals(False)
            return
        self.gimbal_follow_toggled.emit(True)

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
        # 解调模式：优先按链路目录的体制给（apt/lrpt=宽带图像 WFM），
        # 目录没有的星回落到频率启发式。
        mode = ""
        try:
            from mbdsdr_ai.sat_links import find_tuning_for
            link = find_tuning_for(str(res.get("satellite")
                                       or self._tune_sat_name or ""))
            if link is not None:
                mode = {"apt": "WFM", "lrpt": "WFM", "nfm": "NFM",
                        "fm": "NFM", "packet": "NFM",
                        "bpsk": "NFM"}.get(
                            str(link["downlink"].get("mode", "")).lower(), "")
        except Exception:
            pass
        if not mode:
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
        self._doppler_label.setText(
            f"多普勒 {format_frequency_hz(dop)}")

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

    def sky_action_context(self, action: str) -> Optional[Dict[str, object]]:
        """为一次天空动作收集真实目标上下文（按钮 → agent 能力协商）。

        数据来自实时轨道/链路计算；拿不到的字段如实留空，不编造。
        """
        info = self._selected_info or {}
        name = str(info.get("title") or self._tune_sat_name or "")
        if not name:
            return None
        res = self._compute_tune()
        meta = self._selected_sat_tle() or {}
        try:
            from mbdsdr_ai.sky_negotiation import jd_to_utc_iso
        except Exception:
            jd_to_utc_iso = lambda _jd: ""  # noqa: E731
        ctx: Dict[str, object] = {
            "action": str(action),
            "satellite_name": name,
            "norad_id": meta.get("norad") or info.get("norad"),
        }
        if res:
            ctx.update({
                "azimuth_deg": res.get("azimuth_deg"),
                "elevation_deg": res.get("elevation_deg"),
                "nominal_freq_hz": res.get("base_freq_hz"),
                "tuned_freq_hz": res.get("tuned_freq_hz"),
                "doppler_hz": res.get("doppler_shift_hz"),
                "mode": res.get("mode", ""),
            })
        else:
            ctx.update({
                "azimuth_deg": info.get("az"),
                "elevation_deg": info.get("alt"),
            })
            if info.get("downlink_mhz") is not None:
                try:
                    ctx["nominal_freq_hz"] = float(info["downlink_mhz"]) * 1e6
                except (TypeError, ValueError):
                    pass
            ctx["mode"] = str(info.get("downlink_mode") or "")
        rise = jd_to_utc_iso(info.get("rise"))
        set_ = jd_to_utc_iso(info.get("set"))
        if rise:
            ctx["pass_rise_utc"] = rise
        if set_:
            ctx["pass_set_utc"] = set_
        if info.get("tle_epoch"):
            ctx["tle_epoch"] = str(info["tle_epoch"])
        obs = self._observer or {}
        try:
            if obs.get("lat") is not None and obs.get("lon") is not None:
                ctx["observer"] = (f"{float(obs['lat']):.4f},"
                                   f"{float(obs['lon']):.4f}")
        except (TypeError, ValueError):
            pass
        return ctx

    def _position_tune_overlay(self):
        """把调谐/跟踪/云台/摄像机按钮+多普勒标签放到左下角。"""
        try:
            bw = max(self._tune_btn.sizeHint().width(), 110)
            tw = max(self._track_btn.sizeHint().width(), 70) \
                if self._track_btn.isVisibleTo(self) else 0
            gw = max(self._gimbal_btn.sizeHint().width(), 64) \
                if self._gimbal_btn.isVisibleTo(self) else 0
            cw = max(self._camera_btn.sizeHint().width(), 84) \
                if self._camera_btn.isVisibleTo(self) else 0
            dw = self._doppler_label.sizeHint().width()
            h = 26
            x = 12
            y = self.height() - h - 12
            self._tune_btn.setGeometry(x, y, bw, h)
            tx = x + bw + 6
            self._track_btn.setGeometry(tx, y, tw, h)
            gx = tx + tw + 6
            self._gimbal_btn.setGeometry(gx, y, gw, h)
            cx = gx + gw + 6
            self._camera_btn.setGeometry(cx, y, cw, h)
            self._doppler_label.setGeometry(cx + cw + 6, y, max(dw, 90), h)
        except Exception:
            pass

    # ========================================================================
    # 坐标转换：统一委托给 sky_interaction.sky_to_screen / screen_to_sky
    # ========================================================================

    def _widget_size_tuple(self) -> Tuple[int, int]:
        return int(self.width()), int(self.height())

    # ------------------------------------------------------------------ 全天模式
    @property
    def all_sky_active(self) -> bool:
        return self._all_sky_active

    def set_all_sky_mode(self, enabled: bool) -> None:
        """切换全天模式（立体投影，FOV 上限 160°）。

        入口：视图内按 A 切换、Esc 退出（分层退出：先退全天，再交给上层）。
        专业模式的 ViewState/投影整体备份，退出时原样恢复——两套状态互不
        污染。主视场仍是透视 120°，本方法不改任何全局常量。
        """
        enabled = bool(enabled)
        if enabled == self._all_sky_active:
            return
        if enabled:
            self._pro_state_backup = self.view_state.copy()
            self._pro_projection_backup = self.projection
            st = self._all_sky_state
            cur = self.view_state
            st.center_az = cur.center_az
            st.center_alt = cur.center_alt
            st.fov_deg = self.ALL_SKY_FOV_DEG
            st._clamp()
            self.view_state = st
            self.projection = self._all_sky_projection
        else:
            if self._pro_state_backup is None:
                self._all_sky_active = False
                return
            self.view_state = self._pro_state_backup
            self.projection = self._pro_projection_backup
            self._pro_state_backup = None
            self._pro_projection_backup = None
        self._all_sky_active = enabled
        self._proj = self.projection
        self._cancel_goto_anim()
        self._status_card_scroll = 0.0
        self.update()
        self.all_sky_mode_changed.emit(enabled)

    def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
        if event.key() == Qt.Key_Escape and self._all_sky_active:
            self.set_all_sky_mode(False)
            event.accept()
            return
        if event.key() == Qt.Key_A:
            self.set_all_sky_mode(not self._all_sky_active)
            event.accept()
            return
        super().keyPressEvent(event)

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

    def _sky_to_screen_many(self, az_deg, alt_deg) \
            -> Tuple[np.ndarray, np.ndarray]:
        """批量正投影：直接委托 sky_interaction.sky_to_screen_many。

        与单点 `_sky_to_screen` 共用 `_to_screen_arrays` 唯一换算核；投影
        换成球极（全天模式）时同样生效，不再有第二套透视公式。
        """
        return sky_to_screen_many(az_deg, alt_deg, self.view_state,
                                  self.projection, self._widget_size_tuple())

    def _sky_to_screen_finite(self, azimuth_deg: float,
                              elevation_deg: float) -> Optional[QPointF]:
        """同 _sky_to_screen, 但视界外/相机背后返回 None。

        拾取专用: _sky_to_screen 对非有限投影兜底回屏中心, 拾取若不查有限性,
        视界外天体会在屏中心变成"幽灵命中"。
        """
        px, py = sky_to_screen(azimuth_deg, elevation_deg,
                               self.view_state, self.projection,
                               self._widget_size_tuple())
        if not (math.isfinite(px) and math.isfinite(py)):
            return None
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
            # 2) 恒星 (分级星点 + 亮星标签) + 星座连线/座名
            self._label_items = []          # 本帧标签队列（末尾统一避让绘制）
            self._draw_stars(painter)
            self._draw_constellations(painter)
            self._draw_dso(painter)
            # 2.5) 大气加法辉光：日落/日出方位暖红，加法叠在星空之上
            #      （SWE §4「加法混合 (ONE,ONE) 画在星空后」）——洗淡近辉光
            #      的星、给地平抹上暖色；地景随后盖在其上（山影遮地平辉光）。
            if self._show_atmosphere:
                self._draw_atmosphere_glow(painter)
            # 3) 几何地平线 (严格 alt=0) + 地景剪影
            self._draw_horizon_line(painter)
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
            self._draw_planets(painter)
            self._draw_objects(painter)
            self._draw_antenna_pointer(painter)
            self._draw_gnss_satellites(painter)
            # 天体/卫星/GNSS 标签（月亮/行星/卫星块/座名/亮星）统一避让后
            # 画在最上层，密集天区不互相压字。
            self._flush_labels(painter)
            if self._show_compass:
                self._draw_compass(painter)
            if self._hovered_object:
                self._draw_picked_info(painter)
            elif self._picked_body:
                self._draw_body_info(painter)

        # 6) 常驻叠加: FOV 显示 / 摄像机锁定 / 信息卡 / 过境 / GNSS / 空状态
        self._draw_fov(painter)
        self._draw_camera_hud(painter)
        self._draw_fov_control(painter)
        self._draw_info_overlay(painter)
        self._draw_passes_panel(painter)
        self._draw_gnss_overlay(painter)
        self._draw_data_source_overlay(painter)

        painter.end()

    # ------------------------------------------------------------------ #
    # 天空背景: 透视窗口全屏, 近地平线大气散射渐变 (Preetham 简化)
    # ------------------------------------------------------------------ #
    def _paint_sky_background(self, painter: QPainter):
        # 暮光渐变 hex 表 (S4): 多段 stops, 夜/昼按太阳高度混合
        painter.save()
        painter.setPen(Qt.NoPen)
        grad = QLinearGradient(0, 0, 0, self.height())
        for pos, color in _twilight_stops(self._sky_brightness):
            grad.setColorAt(pos, color)
        painter.setBrush(QBrush(grad))
        painter.drawRect(self.rect())
        painter.restore()

    # ------------------------------------------------------------------ #
    # 大气加法辉光: 日落/日出方位暖红 (SWE §4 加法混合, 画在星空后)
    # ------------------------------------------------------------------ #
    def _draw_atmosphere_glow(self, painter: QPainter):
        """晨昏暖辉：加法混合的径向暖光，锚在太阳方位的地平处。

        SWE §4「加法混合 (ONE,ONE) 画在星空后」+「日落方位暖红」的 QPainter
        等价实现：强度由 sun_glow_strength(太阳高度) 给（贴近地平最强，晨昏
        之外为 0）；用 CompositionMode_Plus 把暖色 *加* 到已画好的星空上，故
        辉光处的星被洗淡、地平抹暖——不是盖一层不透明色。太阳低于地平时也
        贴地平（alt 夹到 0）发光，正是曙暮辉光。strength≤0.01 直接跳过。
        """
        sun = self._sun
        if not sun:
            return
        az, alt = sun.get("az"), sun.get("alt")
        if az is None or alt is None:
            return
        strength = sun_glow_strength(float(alt))
        if strength <= 0.01:
            return
        center = self._sky_to_screen(float(az), max(0.0, float(alt)))
        radius = max(self.width(), self.height()) * 0.55
        if radius <= 1.0:
            return
        warm = QColor(255, 122, 48)              # 晨昏暖橙（公开天光常识色）
        peak = int(round(120.0 * strength))
        grad = QRadialGradient(center, radius)
        grad.setColorAt(0.00, QColor(warm.red(), warm.green(), warm.blue(), peak))
        grad.setColorAt(0.45, QColor(warm.red(), warm.green(), warm.blue(),
                                     int(round(peak * 0.34))))
        grad.setColorAt(1.00, QColor(warm.red(), warm.green(), warm.blue(), 0))
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawRect(self.rect())
        painter.restore()

    # ------------------------------------------------------------------ #
    # 恒星: J2000 -> 地平 -> 透视投影, 分级星点 + 亮星标签
    # ------------------------------------------------------------------ #
    def _star_selection(self, gs, jd):
        """当帧应画的星集合（与拾取共用同一份 select_for_view 结果）。

        星表缺失/加载失败时返回 None，并在状态卡如实写「星表不可用」——
        不静默回退到内置 141 颗亮星子集（那是另一套坐标/另一套可见性）。
        """
        try:
            from mbdsdr_ai.skyengine.star_catalog import select_for_view
        except Exception:
            self._star_catalog_error = "星表模块不可用"
            return None
        try:
            sel = select_for_view(
                gs, jd,
                float(self.view_state.center_az),
                float(self.view_state.center_alt),
                float(self.view_state.fov_deg),
                bortle=int(self._bortle),
                max_count=self.STAR_MAX_DRAW,
            )
        except Exception as exc:
            self._star_catalog_error = f"星表不可用: {type(exc).__name__}"
            return None
        self._star_catalog_error = ""
        return sel

    # 视场 LOD 放出的暗星（有效星等已超过裸眼基线）用最小可辨点表示：
    # 半径 0.5px、无辉光，alpha 随接近本帧极限而衰减。判据只有一条——
    # ``eff_mag <= selection.limit_mag``（limit 来自 star_catalog 的
    # 视场+Bortle+消光模型），不是第二套阈值；裸眼基线以内仍走
    # stars.star_draw 原模型，视觉不变。
    STAR_LOD_MIN_R_PX = 0.5

    def _star_visual(self, mag_eff: float, limit_mag: float,
                     bortle: int) -> Tuple[float, float, float, int]:
        """(半径, 亮度, 辉光, alpha) —— 绘制与拾取共用同一份视觉模型。"""
        r, bright, bloom = _stars.star_draw(mag_eff, bortle)
        if r > 0.0:
            return r, bright, bloom, int(255 * (0.35 + 0.65 * bright))
        try:
            from mbdsdr_ai.skyengine.star_catalog import naked_eye_limit_mag
            base = naked_eye_limit_mag(int(bortle))
            lim = float(limit_mag)
        except Exception:
            return 0.0, 0.0, 0.0, 0
        if float(mag_eff) > lim + 1e-9:
            return 0.0, 0.0, 0.0, 0
        span = max(1e-6, lim - base)
        fade = max(0.0, min(1.0, (lim - float(mag_eff)) / span))
        # 22..94 /255：暗星可见但不假装和裸眼星一样亮。
        return self.STAR_LOD_MIN_R_PX, 0.0, 0.0, int(255 * (0.09 + 0.28 * fade))

    def _draw_stars(self, painter: QPainter):
        gs = self._ground_station()
        if gs is None:
            self._star_sel = None
            return
        if self._stars_hidden_by_daylight():
            # 白天：恒星整层不画（近似口径与限制见 stars_hidden_by_daylight）。
            # 同时清空拾取集合，保证「看不见就不能点中」。
            self._star_sel = None
            return
        jd = self._current_jd()
        sel = self._star_selection(gs, jd)
        self._star_sel = sel
        if sel is None or len(sel) == 0:
            return
        painter.save()
        painter.setFont(self._font)
        label_mag = _star_label_limit_mag(self.view_state.fov_deg)
        # 批量落笔：把颜色/直径/alpha 量化成桶，每桶一次 drawPoints。
        # 120° 时逐星 setBrush+drawEllipse 实测 71ms；分桶后状态切换从
        # ~1200 次降到几十次。量化步长（色 8、直径 0.5px、alpha 8）
        # 小于 1px 显示精度，视觉上不可辨。
        dot_buckets: Dict[Tuple[int, int, int, int, int], List[QPointF]] = {}
        n = len(sel)
        # 视高度与屏幕位置一次批量算出（逐星重建旋转矩阵是主要瓶颈）。
        alt_draw_arr = np.array(
            [self._display_alt_deg(float(a)) for a in sel.alt_deg], dtype=float)
        px_arr, py_arr = self._sky_to_screen_many(sel.az_deg, alt_draw_arr)
        for i in range(n):
            try:
                mag_eff = float(sel.eff_mag[i])
                vmag = float(sel.vmag[i])
            except Exception:
                continue
            # 地平门按**视高度**（显示层折射）。星表取数已按几何 alt>=0
            # 裁剪；这里再按视高度确认一次（与历史 141 星路径同一门）。
            alt_draw = float(alt_draw_arr[i])
            if alt_draw < 0.0:
                continue
            px, py = float(px_arr[i]), float(py_arr[i])
            if not (math.isfinite(px) and math.isfinite(py)):
                continue
            if not (-4.0 <= px <= self.width() + 4.0
                    and -4.0 <= py <= self.height() + 4.0):
                continue
            pos = QPointF(px, py)
            # select_for_view 已含消光与极限星等（eff_mag）；不再叠加第二次。
            # 裸眼基线内走 star_draw 原模型，LOD 放出的更暗星走最小可辨点。
            r, bright, bloom, alpha = self._star_visual(
                mag_eff, sel.limit_mag, self._bortle)
            if r <= 0:
                continue
            try:
                bv = float(sel.bv[i])
            except Exception:
                bv = 0.6
            cr, cg, cb = _stars.bv_to_rgb(bv)
            # 亮星辉光（bloom）：同心圆递减 alpha，由内向外晕开。
            # 只有亮星（r>=_BLOOM_MIN_R）走这条慢路径，数量 <1%。
            if bloom > 0.0:
                for ring in range(3):
                    rr = r * (1.7 + 0.9 * ring)
                    glow_a = int(70 * bloom * (0.5 ** ring))
                    painter.setBrush(QBrush(QColor(cr, cg, cb, glow_a)))
                    painter.drawEllipse(pos, rr, rr)
            key = (int(cr) // 8 * 8, int(cg) // 8 * 8, int(cb) // 8 * 8,
                   max(1, int(round(max(1.0, 2.0 * r) * 2.0))),
                   max(1, int(alpha) // 8 * 8))
            dot_buckets.setdefault(key, []).append(pos)
            # 星名标签：只给 named 星，阈值随 FOV 收放（拉远不糊屏；
            # 放大后允许更暗的具名星出现）。无名星不编造名字。
            name = str(sel.name[i] or "")
            if name and vmag <= label_mag:
                self._queue_label(2.0 + vmag,
                                  QPointF(pos.x() + r + 2, pos.y() - r),
                                  _stars.culture_label(name),
                                  QColor(210, 220, 235, int(200 * bright + 40)))
        for (cr, cg, cb, dq, aq), pts in dot_buckets.items():
            pen = QPen(QColor(cr, cg, cb, aq))
            pen.setWidthF(max(1.0, dq / 2.0))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPoints(QPolygonF(pts))
        painter.restore()

    # ------------------------------------------------------------------ #
    # 深空天体层 (Messier/NGC 子集): 类型符号 + 视大小 + 视星等
    # ------------------------------------------------------------------ #
    # 与恒星共用同一套可见性规则（不是第二套阈值）：
    #   - 地平门：alt < 0 不画，也不给点；
    #   - 亮度门：star_draw(vmag + 消光, Bortle) 的 r<=0 不画（裸眼极限外）；
    #   - 白天门：stars_hidden_by_daylight() 为真时整层不画。
    # 数据全部来自 mbdsdr_ai.astronomy.dso.BUILTIN_CATALOG（Messier 110 +
    # 著名 NGC，公开星表的 RA/Dec/视星等/视大小），不画任何"像星云"的装饰。
    # 符号只表达类型（星系/星团/星云/超新星遗迹），不冒充形态还原；
    # 外形尺寸取星表视大小（角分→像素按投影采样），亮度取视星等。
    DSO_LABEL_PRIORITY = 1.5      # 座名(1.0)之后、星名(2.0+)之前
    DSO_MIN_SYMBOL_R_PX = 1.6     # 视大小 < 1.6px 时给小符号，否则不可辨
    # 符号限幅：仅当真实视半径超过视口短边 40% 时截断（否则仍按真实视大小画）。
    # 限幅半径由绘制/拾取共用的 _dso_symbol_geometry() 统一给出，不另立第二套。
    DSO_CLAMP_VIEW_FRAC = 0.40
    # 全天星表每帧最多返回/绘制条数（与 star_catalog.DEFAULT_MAX_COUNT 同口径）。
    # 超出时 StarSelection.truncated=True,界面如实写「已按上限显示 N / 可用 M」。
    STAR_MAX_DRAW = 20000

    # 左下信息卡尺寸（绘制与测试的唯一事实源，避免测试写死像素值漂移）
    INFO_CARD_W = 240
    # +54：恒星时/视轴/星表来源三行（时空读数与署名，见 docs/prototype-gap.md 批1）
    INFO_CARD_H = 330

    def _dso_symbol_geometry(self, az: float, alt: float,
                             dia_arcmin: Optional[float]) -> Tuple[float, bool]:
        """DSO 符号半径 + 是否限幅（绘制与拾取共用这一条事实源）。

        ``dia_arcmin=None`` 表示目录缺视大小：按最小可辨符号画，不把 0
        当成「0 角分」；卡内同时写「角大小 未知」。
        """
        dia = 0.0 if dia_arcmin is None else max(0.0, float(dia_arcmin))
        geom = self._angular_radius_px(az, alt, dia / 120.0)
        r_ang = float(geom[1]) if geom is not None else 0.0
        cap = self.DSO_CLAMP_VIEW_FRAC * float(min(self.width(), self.height()))
        if r_ang > cap:
            return cap, True
        return max(r_ang, self.DSO_MIN_SYMBOL_R_PX), False

    def _draw_dso_clamp_marker(self, painter: QPainter, pos: QPointF,
                               r: float, alpha: int) -> None:
        """限幅可见标记：虚线外框 + 中心「已限幅」角标。

        限幅符号半径不再等于真实视大小，必须让人一眼看出「这是被截断的」，
        否则就是把 M31 画小了还说它是真大小（新的谎报）。
        """
        _t = tokens()
        col = QColor(_t.COLORS["danger"])
        pen = QPen(QColor(col.red(), col.green(), col.blue(),
                          min(220, alpha + 80)), 1)
        pen.setStyle(Qt.DashLine)
        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(pen)
        painter.drawEllipse(pos, r + 3.0, r + 3.0)

        f = self._gnss_label_font
        painter.setFont(f)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance("已限幅") + 8
        th = fm.height() + 2
        bx, by = pos.x() - tw / 2.0, pos.y() - th / 2.0
        bg = QColor(_t.COLORS["card_1"])
        bg.setAlpha(210)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(QRectF(bx, by, tw, th), 2, 2)
        painter.setPen(QPen(col))
        painter.drawText(QRectF(bx, by, tw, th), Qt.AlignCenter, "已限幅")
        painter.restore()

    def _draw_dso(self, painter: QPainter):
        gs = self._ground_station()
        if gs is None:
            return
        if self._stars_hidden_by_daylight():
            return                      # 白天：与恒星同一门，整层不画
        try:
            from mbdsdr_ai.astronomy import dso as _dso
            from mbdsdr_ai.celestial_geometry import azalt_from_vec
        except Exception:
            return
        jd = self._current_jd()
        painter.save()
        for e in _dso.BUILTIN_CATALOG:
            try:
                v = j2000_to_altaz(vec_from_radec(e.ra_deg, e.dec_deg), gs, jd)
                az, alt = azalt_from_vec(v)
            except Exception:
                continue
            if alt < -0.6:
                continue                # 几何高度远在地平下（视高度必 < 0）
            # 绘制位置用视高度；消光仍按几何高度（光程是几何量）。
            alt_draw = self._display_alt_deg(alt)
            if alt_draw < 0.0:
                continue
            pos = self._sky_to_screen(az, alt_draw)
            if not self._on_screen(pos):
                continue
            mag = dso_magnitude(e)
            if mag is None:
                # 星等缺失：不伪造亮度，只按最低可辨符号画类型；卡内写「星等未知」。
                r_star, bright = 1.0, 0.35
            else:
                r_star, bright, _bloom = _stars.star_draw(
                    mag + _stars.extinction_delta_mag(alt), self._bortle)
                if r_star <= 0:
                    continue            # 与恒星同一亮度门（看不清的不画）
            r, clamped = self._dso_symbol_geometry(az, alt_draw, dso_size_arcmin(e))
            alpha = int(round(70.0 + 150.0 * bright))
            self._draw_dso_glyph(painter, pos, r, e.dso_type, alpha,
                                 dso_axis_ratio(e))
            if clamped:
                self._draw_dso_clamp_marker(painter, pos, r, alpha)
            # 小而不亮的条目（1.6px 符号）不挂名，避免宽 FOV 下满屏缩写；
            # 名字仍然可以在信息卡/检索里看到。
            if r >= 2.0 or bright >= 0.5:
                self._queue_label(self.DSO_LABEL_PRIORITY,
                                  QPointF(pos.x() + r + 3.0, pos.y() - r - 2.0),
                                  dso_label_text(e), self._ink(), small=True)
        painter.restore()

    def _draw_dso_glyph(self, painter: QPainter, pos: QPointF, r: float,
                        dso_type: str, alpha: int,
                        axis_ratio: Optional[float] = None) -> None:
        """按类型画不同符号；r 为星表视大小换算出的像素半径。

        形状约定（只用轮廓，不做亮度渐变——本视图没有表面亮度模型）：
          galaxy              椭圆（长轴 = 视大小，短轴 = 真实轴比）
          cluster             点状圆环（8 个点围成）
          nebula              虚线菱形
          cluster_nebula      虚线椭圆 + 内部星点（星团+星云复合体）
          double_star         两个实心点（双星）
          supernova_remnant   双同心圆
        星系轴比缺失时退回 0.55（卡内标「轴比未知」），不是真值。
        """
        col = self._ink()
        pen = QPen(QColor(col.red(), col.green(), col.blue(), min(255, alpha)), 1)
        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(pen)
        if dso_type == "galaxy":
            ratio = axis_ratio if axis_ratio is not None else 0.55
            painter.drawEllipse(pos, max(1.0, r), max(0.7, r * ratio))
        elif dso_type == "cluster":
            rr = max(1.4, r)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(),
                                           min(255, alpha))))
            for k in range(8):
                th = math.radians(45.0 * k)
                painter.drawEllipse(QPointF(pos.x() + rr * math.cos(th),
                                            pos.y() + rr * math.sin(th)),
                                    1.0, 1.0)
        elif dso_type == "cluster_nebula":
            # 复合体：外侧虚线椭圆（星云）+ 内侧点状环（星团），两型信息都在。
            rr = max(1.6, r)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawEllipse(pos, rr, max(1.0, rr * 0.78))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(),
                                           min(255, alpha))))
            for k in range(6):
                th = math.radians(60.0 * k)
                painter.drawEllipse(QPointF(pos.x() + rr * 0.55 * math.cos(th),
                                            pos.y() + rr * 0.55 * math.sin(th)),
                                    0.9, 0.9)
        elif dso_type == "double_star":
            # 双星：两个实心点，中间留空（与星团点环区分）。
            sep = max(1.4, r * 0.55)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(),
                                           min(255, alpha))))
            for dx in (-sep, sep):
                painter.drawEllipse(QPointF(pos.x() + dx, pos.y()), 1.15, 1.15)
        elif dso_type == "supernova_remnant":
            painter.drawEllipse(pos, max(1.2, r), max(1.2, r))
            if r > 3.0:
                painter.drawEllipse(pos, r * 0.5, r * 0.5)
        else:                               # nebula（及其它类型）→ 虚线菱形
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            rr = max(1.4, r)
            painter.drawPolygon(QPolygonF([QPointF(pos.x(), pos.y() - rr),
                                           QPointF(pos.x() + rr, pos.y()),
                                           QPointF(pos.x(), pos.y() + rr),
                                           QPointF(pos.x() - rr, pos.y())]))
        painter.restore()

    # ------------------------------------------------------------------ #
    # 星座/星官图层（Stellarium 宽 FOV 玩法）
    # ------------------------------------------------------------------ #
    # 数据全部来自 skyengine.constellations.overlay_layers()：
    #   - 每层强制带 system_id / display_name / tag（界面必须标明体系）；
    #   - IAU 88 座 842 段（1 段因 HIP 55203 在 HYG 缺失已丢弃，不补造）；
    #   - 中国星官 31 区域 / 287 个星官，其中 274 个有连线、13 个无连线
    #     如实不画；映射不到 HYG 的星（ra_deg=None）整段跳过，不猜坐标。
    #
    # FOV 分层（连续 alpha，跨阈值不跳变 → 不闪烁）：
    #   区域连线（IAU 骨架 / 三垣二十八宿宿线）：常显（alpha=1）
    #   星官内部细线：拉近浮现（55°→30° 渐显）
    #   座名 / 区域名：拉远浮现（45°→65° 渐显）
    CONSTEL_DETAIL_FOV = (55.0, 30.0)    # (渐显起点, 全显) —— 随 FOV 减小而增
    CONSTEL_LABEL_FOV = (45.0, 65.0)     # 随 FOV 增大而增

    @staticmethod
    def _ramp_up(x: float, x0: float, x1: float) -> float:
        if x1 <= x0:
            return 1.0 if x >= x1 else 0.0
        return max(0.0, min(1.0, (float(x) - x0) / (x1 - x0)))

    def _constellation_layer_alpha(self) -> Tuple[float, float]:
        """(星官内部线 alpha, 区域名 alpha) —— 纯函数，测试可直接断言。

        两条都是 FOV 的连续函数：相邻视场之间只差一点点，不存在
        “到了某个 FOV 突然冒出一整层”的闪烁。
        """
        fov = float(self.view_state.fov_deg)
        lo, hi = self.CONSTEL_DETAIL_FOV
        detail = 1.0 - self._ramp_up(fov, hi, lo)
        label = self._ramp_up(fov, self.CONSTEL_LABEL_FOV[0],
                              self.CONSTEL_LABEL_FOV[1])
        return detail, label

    def _j2000_altaz_many(self, ra_deg, dec_deg, gs, jd) \
            -> Tuple[np.ndarray, np.ndarray]:
        """批量 J2000 -> (az, alt)；岁差/章动/地平旋转矩阵只算一次。

        与 j2000_to_altaz 同一链（P→N→R_altaz），只是不逐星重建矩阵。
        """
        ra = np.radians(np.asarray(ra_deg, dtype=float))
        dec = np.radians(np.asarray(dec_deg, dtype=float))
        vec = np.column_stack((np.cos(dec) * np.sin(ra),
                               np.sin(dec),
                               -np.cos(dec) * np.cos(ra)))
        m = (gs.equatorial_to_altaz_matrix(jd)
             @ nutation_matrix(jd) @ precession_matrix(jd))
        v = vec @ m.T
        alt = np.degrees(np.arcsin(np.clip(-v[:, 2], -1.0, 1.0)))
        az = np.degrees(np.arctan2(v[:, 1], v[:, 0])) % 360.0
        return az, alt

    def _constellation_screen_points(self, az, alt, gs, jd) \
            -> List[Optional[QPointF]]:
        """批量落屏；地平以下（视高度 < -6°）或相机背后返回 None。"""
        alt_draw = np.array([self._display_alt_deg(float(a)) for a in alt],
                            dtype=float)
        px, py = self._sky_to_screen_many(az, alt_draw)
        out: List[Optional[QPointF]] = []
        for i in range(len(px)):
            x, y = float(px[i]), float(py[i])
            if not (math.isfinite(x) and math.isfinite(y)):
                out.append(None)
            elif float(alt_draw[i]) < -6.0:
                out.append(None)
            else:
                out.append(QPointF(x, y))
        return out

    def _layer_points(self, refs: Dict[object, Tuple[float, float]],
                      gs, jd) -> Dict[object, Optional[QPointF]]:
        """{key: (ra,dec)} -> {key: QPointF|None}（一次批量投影）。"""
        keys = list(refs.keys())
        if not keys:
            return {}
        ra = [refs[k][0] for k in keys]
        dec = [refs[k][1] for k in keys]
        az, alt = self._j2000_altaz_many(ra, dec, gs, jd)
        pts = self._constellation_screen_points(az, alt, gs, jd)
        return dict(zip(keys, pts))

    def _constellation_prepared(self, layer) -> Dict[str, object]:
        """按体系预计算折线拓扑与标签锚点，缓存后跨帧复用。

        缓存口径（硬约束）：只按 ``system_id`` 存，切 FOV、开关图层、切换
        体系都**不重建**；``segments()`` / ``all_segments()``（实测 0.26ms/次）
        只在首次准备时调用。屏幕坐标每帧仍要批量投影（视点/时间在变），
        但折线元组已就绪，FOV 切换只是对已加载 tuple 做裁剪。

        标签锚点只用数据层 ``xingguan_anchor()``（IAU 用 ``anchor()``），
        视图层不再遍历成员自己找最亮星（避免第二份事实源）。
        """
        sid = layer.system_id
        cached = self._constel_prepared.get(sid)
        if cached is not None:
            return cached
        refs: Dict[object, Tuple[float, float]] = {}
        region_segs: List[Tuple[object, object]] = []
        detail_segs: List[Tuple[object, object]] = []
        labels: List[Tuple[str, object]] = []

        if sid == _constel.SYSTEM_IAU:
            for con in layer.items:
                for sa, sb in con.segments():
                    refs[sa.hyg_id] = (sa.ra_deg, sa.dec_deg)
                    refs[sb.hyg_id] = (sb.ra_deg, sb.dec_deg)
                    region_segs.append((sa.hyg_id, sb.hyg_id))
                anchor = con.anchor()
                if anchor is not None:
                    refs[anchor.hyg_id] = (anchor.ra_deg, anchor.dec_deg)
                    labels.append((con.abbr, anchor.hyg_id))
        else:
            seen: Set[Tuple[int, int]] = set()
            for region in layer.items:
                for sa, sb in region.segments():
                    if sa.ra_deg is None or sb.ra_deg is None:
                        continue          # 未映射：整段跳过，不拿空坐标补造
                    refs[sa.hip] = (sa.ra_deg, sa.dec_deg)
                    refs[sb.hip] = (sb.ra_deg, sb.dec_deg)
                    region_segs.append((sa.hip, sb.hip))
                for xg in region.xingguan:
                    for poly in xg.lines:
                        for sa, sb in zip(poly, poly[1:]):
                            if sa.hip is None or sb.hip is None:
                                continue
                            if sa.ra_deg is None or sb.ra_deg is None:
                                continue
                            key = (min(sa.hip, sb.hip), max(sa.hip, sb.hip))
                            if key in seen:
                                continue
                            seen.add(key)
                            refs[sa.hip] = (sa.ra_deg, sa.dec_deg)
                            refs[sb.hip] = (sb.ra_deg, sb.dec_deg)
                            detail_segs.append((sa.hip, sb.hip))
                anchor = _xingguan.xingguan_anchor(region)
                if anchor is not None:     # None 时数据层给原因码，这里如实不画
                    refs[anchor.hip] = (anchor.ra_deg, anchor.dec_deg)
                    labels.append((region.key, anchor.hip))

        prepared = {
            "refs": refs,
            "region": tuple(region_segs),
            "detail": tuple(detail_segs),
            "labels": tuple(labels),
        }
        self._constel_prepared[sid] = prepared
        return prepared

    @staticmethod
    def _constel_path(segments, points) -> QPainterPath:
        """已加载折线元组 + 本帧投影点 -> 一个 QPainterPath（一次绘制）。"""
        path = QPainterPath()
        for ka, kb in segments:
            pa = points.get(ka)
            pb = points.get(kb)
            if pa is None or pb is None:
                continue
            path.moveTo(pa)
            path.lineTo(pb)
        return path

    def _draw_constellations(self, painter: QPainter):
        """星座/星官图层：按 FOV 分层浮现 + 座名标签（低于天体名优先级）。"""
        systems = tuple(s for s in _constel.system_ids()
                        if s in self._constellation_systems)
        if not systems:
            self._constellation_error = ""
            return
        gs = self._ground_station()
        if gs is None:
            return
        jd = self._current_jd()
        try:
            layers = _constel.overlay_layers(systems)
        except Exception as exc:       # 数据文件损坏/SHA 不符：如实空态，不 crash
            self._constellation_error = type(exc).__name__
            return
        self._constellation_error = ""
        multi = len(layers) > 1
        detail_alpha, label_alpha = self._constellation_layer_alpha()
        night = self._sky_brightness < 0.5

        for layer in layers:
            if layer.system_id == _constel.SYSTEM_IAU:
                base_line = QColor(112, 142, 190, 95 if night else 55)
                base_label = QColor(150, 170, 205, 165 if night else 105)
            else:  # 中国星官：暖色系，与 IAU 蓝灰在视觉上即可区分
                base_line = QColor(196, 150, 96, 105 if night else 60)
                base_label = QColor(214, 176, 122, 175 if night else 115)

            prepared = self._constellation_prepared(layer)
            points = self._layer_points(prepared["refs"], gs, jd)

            painter.save()
            painter.setPen(QPen(base_line, 1))
            painter.drawPath(self._constel_path(prepared["region"], points))
            if detail_alpha > 0.0 and prepared["detail"]:
                dcol = QColor(base_line)
                dcol.setAlpha(int(base_line.alpha() * detail_alpha))
                if dcol.alpha() >= 8:
                    painter.setPen(QPen(dcol, 1))
                    painter.drawPath(
                        self._constel_path(prepared["detail"], points))
            painter.restore()

            if label_alpha <= 0.0:
                continue
            col = QColor(base_label)
            col.setAlpha(int(base_label.alpha() * label_alpha))
            if col.alpha() < 8:
                continue
            for key, anchor_key in prepared["labels"]:
                pos = points.get(anchor_key)
                if pos is None or not self._on_screen(pos):
                    continue
                if layer.system_id == _constel.SYSTEM_IAU:
                    text = _constel.iau_constellation_label(
                        key, with_system_tag=multi)
                else:
                    text = _xingguan.xingguan_label(
                        key, with_system_tag=multi)
                self._queue_label(1.0, QPointF(pos.x() + 8, pos.y() - 8),
                                  text, col, small=True)


    def _on_screen(self, pos: QPointF, margin: float = 4.0) -> bool:
        return (-margin <= pos.x() <= self.width() + margin
                and -margin <= pos.y() <= self.height() + margin)

    def set_refraction_params(self, pressure_hpa: Optional[float] = None,
                              temperature_c: Optional[float] = None,
                              enabled: Optional[bool] = None) -> None:
        """覆盖显示层折射口径（气压 hPa / 温度 °C / 总开关）。"""
        if pressure_hpa is not None:
            self._refraction_pressure_hpa = float(pressure_hpa)
        if temperature_c is not None:
            self._refraction_temperature_c = float(temperature_c)
        if enabled is not None:
            self._refraction_enabled = bool(enabled)
        self.update()

    # -- 星座/星官图层开关（每套体系可单独开关） --------------------------
    def set_constellation_layer_visible(self, system_id: str,
                                        visible: bool) -> None:
        """单独开关一套天空文化体系；未知 system_id 直接报错，不静默忽略。"""
        ids = _constel.system_ids()
        if system_id not in ids:
            raise KeyError(f"未知天空文化体系: {system_id!r}")
        if visible:
            self._constellation_systems.add(system_id)
        else:
            self._constellation_systems.discard(system_id)
        self.update()

    def set_constellation_systems(self, systems) -> None:
        """一次设定打开的体系集合（顺序无关；空集合=整层关闭）。"""
        ids = set(_constel.system_ids())
        wanted = set(systems or ())
        unknown = wanted - ids
        if unknown:
            raise KeyError(f"未知天空文化体系: {sorted(unknown)!r}")
        self._constellation_systems = wanted
        self.update()

    def constellation_systems(self) -> Tuple[str, ...]:
        """当前打开的体系（按数据层固定顺序，便于状态卡与测试）。"""
        return tuple(s for s in _constel.system_ids()
                     if s in self._constellation_systems)

    def constellation_systems_text(self) -> str:
        """状态卡用体系徽标：单开给 tag，双开写「IAU + 中国星官」。"""
        ids = self.constellation_systems()
        if not ids:
            return "关闭"
        if self._constellation_error:
            return f"数据不可用: {self._constellation_error}"
        tags = []
        for sid in ids:
            try:
                tags.append(str(_constel.system_info(sid).get("tag", sid)))
            except Exception:
                tags.append(sid)
        return " + ".join(tags)

    def _display_alt_deg(self, geom_alt_deg: float) -> float:
        """天球对象（恒星/DSO/行星/太阳/月亮）绘制与读数用的**视高度**。

        卫星/过境/天线/热力图/GNSS 是"指向/跟踪"数据，仍用几何高度，
        读数处显式标「几何」——不让同一个数字混用两套口径。
        """
        if not self._refraction_enabled:
            return float(geom_alt_deg)
        return display_apparent_alt_deg(
            float(geom_alt_deg), self._refraction_pressure_hpa,
            self._refraction_temperature_c)

    def _format_alt_pair(self, geom_alt_deg: float, compact: bool = False,
                         digits: int = 1) -> str:
        """按本视图的折射口径输出「几何/视」双标注。"""
        if not self._refraction_enabled:
            return (f"{ALT_FRAME_GEOMETRIC} "
                    f"{format_angle_deg(geom_alt_deg, digits=digits)}")
        return format_alt_pair(geom_alt_deg, self._refraction_pressure_hpa,
                               self._refraction_temperature_c,
                               digits=digits, compact=compact)

    def _sun_alt_deg(self) -> Optional[float]:
        """太阳高度（历表未就绪时 None，不拿默认值冒充）。"""
        sun = self._sun
        if not sun:
            return None
        alt = sun.get("alt")
        if alt is None:
            return None
        try:
            return float(alt)
        except (TypeError, ValueError):
            return None

    def _stars_hidden_by_daylight(self) -> bool:
        """恒星是否因白天整层隐藏（绘制层与拾取层共用同一条判据）。"""
        return stars_hidden_by_daylight(self._sun_alt_deg())

    def _queue_label(self, priority: float, pos: QPointF, text: str,
                     color: QColor, small: bool = False) -> None:
        """登记单行天体标签（本帧末尾统一避让后绘制）。

        优先级越小越优先（可读性裁决，越"用户正在用的"越靠前）：
          0.0  月亮/行星 —— 本视图的观测主体，名字丢了最可惜
          0.5  卫星/信号/干扰/GNSS PRN —— 操作对象，块内含仰角方位或频率
          1.0  星座名 —— 宽 FOV 下的导航骨架
          1.5  深空天体名 —— 比座名更"点状"，但比星名更需要主动找
          2.0+ 星名（2 + 星等，亮星先占位）
        """
        self._queue_label_block(priority, pos, [(str(text), bool(small), 0.0)],
                                color)

    def _queue_label_block(self, priority: float, pos: QPointF,
                           lines: List[Tuple[str, bool, float]],
                           color: QColor) -> None:
        """登记多行标签块（整块一起避让：同一卫星的 3 行不会被各自拆掉）。"""
        if not lines:
            return
        self._label_items.append((float(priority), QPointF(pos),
                                  [(str(t), bool(s), float(dy))
                                   for t, s, dy in lines], QColor(color)))

    def _flush_labels(self, painter: QPainter) -> None:
        """本帧标签统一绘制：按优先级做二维碰撞剔除（label_boxes_keep）。

        块的外接框 = 所有行的并集，因此块内各行永远同进同退。
        """
        items = self._label_items
        self._label_items = []
        if not items:
            return
        fonts = {False: self._font, True: self._gnss_label_font}
        metrics = {k: QFontMetrics(f) for k, f in fonts.items()}
        boxes: List[Tuple[float, float, float, float, float]] = []
        for prio, pos, lines, _color in items:
            x0 = y0 = float("inf")
            x1 = y1 = float("-inf")
            for text, small, dy in lines:
                fm = metrics[small]
                x0 = min(x0, pos.x())
                x1 = max(x1, pos.x() + fm.horizontalAdvance(text))
                y0 = min(y0, pos.y() + dy - fm.ascent())
                y1 = max(y1, pos.y() + dy + fm.descent())
            boxes.append((prio, float(x0), float(y0), float(x1), float(y1)))
        painter.save()
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        for i in label_boxes_keep(boxes):
            _prio, pos, lines, color = items[i]
            painter.setPen(color)
            for text, small, dy in lines:
                painter.setFont(fonts[small])
                painter.drawText(QPointF(pos.x(), pos.y() + dy), text)
        painter.restore()

    # ------------------------------------------------------------------ #
    # 几何地平线 (alt=0)：与地景剪影无关的唯一地平基准
    # ------------------------------------------------------------------ #
    def _draw_horizon_line(self, painter: QPainter):
        """严格 alt=0 地平圈。画在地景之前（山脊可盖住它，但不改变基准）。

        地景剪影是**装饰性示意**（固定噪声轮廓，不是实测地形，也不参与
        拾取/遮挡判定）；地平基准始终是这条线，不随剪影起伏移动。
        """
        _t = tokens()
        col = QColor(_t.COLORS["gray_400"])
        col.setAlpha(110)
        painter.save()
        painter.setPen(QPen(col, 1))
        path = QPainterPath()
        first = True
        for az in range(0, 361, 2):
            pos = self._sky_to_screen(float(az), HORIZON_ALT_DEG)
            if not self._on_screen(pos):
                first = True
                continue
            if first:
                path.moveTo(pos); first = False
            else:
                path.lineTo(pos)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.restore()

    # ------------------------------------------------------------------ #
    # 地景：暮光辉光带 + 三层剪影（远山/树/平顶房屋，由远及近涂黑）
    # ------------------------------------------------------------------ #
    def _draw_landscape(self, painter: QPainter):
        # 地景整体不透明度：紧 fov 淡出防糊 + 俯视地下渐隐一半（SWE §1）。
        # ≤~0 直接整层跳过，省掉轮廓计算。
        alpha = _landscape_alpha(self.view_state.fov_deg,
                                 self.view_state.center_alt)
        if alpha <= 0.003:
            return
        # 地平线基准（alt=0 采样中位 y）：辉光带与剪影轮廓都以它为锚。
        pts = []
        for az in range(0, 361, 6):
            pos = self._sky_to_screen(float(az), 0.0)
            pts.append(pos)
        on = [p for p in pts if self._on_screen(p)]
        if not on:
            return
        ys = sorted(p.y() for p in on)
        horizon_y = ys[len(ys) // 2]

        _t = tokens()
        night = self._sky_brightness < 0.5

        painter.save()
        painter.setOpacity(alpha)

        # 暮光辉光带：剪影后方暖灰渐变，向上渐隐（仅夜；昼由天空渐变自带亮度）
        if night:
            glow_h = int(self.height() * 0.14)
            glow = QLinearGradient(0, horizon_y - glow_h, 0, horizon_y)
            gc = QColor(_t.COLORS["sky_night_glow"])
            glow.setColorAt(0.0, QColor(gc.red(), gc.green(), gc.blue(), 0))
            glow.setColorAt(1.0, QColor(gc.red(), gc.green(), gc.blue(), 170))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawRect(0, int(horizon_y - glow_h), self.width(), glow_h)

        layer_cols = ((None if not night else "ground_far_night"),
                      (None if not night else "ground_mid_night"),
                      (None if not night else "ground_near_night"))
        painter.setPen(Qt.NoPen)
        for idx, (seed, base, amp, lat, octaves, quant) in enumerate(_LANDSCAPE_LAYERS):
            key = layer_cols[idx]
            col = (QColor(_t.COLORS["ground_shadow_day"]) if key is None
                   else QColor(_t.COLORS[key]))
            painter.setBrush(QBrush(col))
            path = QPainterPath()
            edge: List[QPointF] = []
            for az in range(0, 361, 3):
                alt = _landscape_layer_profile(
                    float(az), seed=seed, base_alt=base, amp_alt=amp,
                    lattice_deg=lat, octaves=octaves, quant_deg=quant)
                p = self._sky_to_screen(float(az), alt)
                if self._on_screen(p):
                    edge.append(p)
            if not edge:
                continue
            # 从屏缘起笔/收笔，避免闭合边斜穿画面；断段（视界外的方位）
            # 以等高弦相连——恰落在地平线上。
            path.moveTo(0.0, edge[0].y())
            for p in edge:
                path.lineTo(p.x(), p.y())
            path.lineTo(self.width(), edge[-1].y())
            path.lineTo(self.width(), self.height())
            path.lineTo(0.0, self.height())
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
                # 赤道网格与恒星同帧绘制：用视高度，否则网格与星点在
                # 地平附近错开约 0.5°。
                p = self._sky_to_screen(az, self._display_alt_deg(alt))
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
                p = self._sky_to_screen(az, self._display_alt_deg(alt))
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
                         f"FOV {format_angle_deg(self.view_state.fov_deg)}")
        painter.restore()

    def _draw_camera_hud(self, painter: QPainter):
        """两个环的常驻状态 + 诚实通知（3-4s 淡出）。

        摄像机锁定（视角）与调谐跟踪（多普勒/频率）语义独立；只要有一个
        在跑就成对显示，另一个必须明说「未锁定 / 未开启」，避免看起来
        两个都在跑。外部停跟踪/快转直贴等事件写进通知行。
        """
        lines = self.loop_state_lines()
        now = time.time()
        notice_live = self._camera_notice and now < self._camera_notice_until
        track_notice_live = self._track_notice and now < self._track_notice_until
        if not lines and not notice_live and not track_notice_live:
            return
        painter.save()
        _t = tokens()
        f = QFont(self._font)
        f.setPointSize(9)
        painter.setFont(f)
        y = 40.0
        for line in lines:
            painter.setPen(QColor(_t.COLORS["accent"]))
            painter.drawText(QPointF(14.0, y), line)
            y += 18.0
        if notice_live:
            painter.setPen(QColor(_t.COLORS["danger"]))
            painter.drawText(QPointF(14.0, y), str(self._camera_notice))
            y += 18.0
        if track_notice_live:
            painter.setPen(QColor(_t.COLORS["danger"]))
            painter.drawText(QPointF(14.0, y), str(self._track_notice))
        painter.restore()

    def _draw_fov_control(self, painter: QPainter):
        """右下角 ⊖ ⊕ FOV 步进控件 (Stellarium 底部 FOV 条的精简版)。

        与滚轮同一缩放律 (view_state.zoom ±1 步)，夹在 min_fov..max_fov。
        命中区暴露给 mousePressEvent 的 _hud_hit。
        """
        painter.save()
        _t = tokens()
        btn = 26.0
        gap = 6.0
        label_w = 64.0
        total = btn * 2 + gap * 2 + label_w
        x0 = float(self.width()) - total - 12.0
        y0 = float(self.height()) - btn - 12.0
        bg = QColor(_t.COLORS["card_2"])
        bg.setAlpha(220)
        border = QColor(_t.COLORS["gray_400"])
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(border, 1))

        self._fov_minus_rect = QRectF(x0, y0, btn, btn)
        self._fov_plus_rect = QRectF(x0 + btn + gap * 2 + label_w, y0, btn, btn)
        for r in (self._fov_minus_rect, self._fov_plus_rect):
            painter.drawRoundedRect(r, 4, 4)

        f = QFont(self._font)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(_t.COLORS["gray_100"]))
        painter.drawText(self._fov_minus_rect, Qt.AlignCenter, "⊖")
        painter.drawText(self._fov_plus_rect, Qt.AlignCenter, "⊕")

        painter.setFont(self._mono_font)
        label = QRectF(x0 + btn + gap, y0, label_w, btn)
        painter.drawText(label, Qt.AlignCenter,
                         format_angle_deg(self.view_state.fov_deg, digits=0))
        painter.restore()

    def _draw_grid(self, painter: QPainter):
        """兼容旧接口: 方位网格已由 _draw_az_grid (透视) 实现。"""
        self._draw_az_grid(painter)

    def _draw_compass(self, painter: QPainter):
        """方位罗盘: 地平圈上的 N/E/S/W + 每 30° 数字度数，N 红色。"""
        painter.save()
        _t = tokens()
        dim = QColor(_t.COLORS["gray_300"])
        for az, label, cardinal in compass_labels():
            alt = 2.0 if cardinal else 1.3
            pos = self._sky_to_screen(float(az), alt)
            if not self._on_screen(pos, margin=14.0):
                continue
            if cardinal:
                f = QFont(self._font)
                f.setBold(True)
                f.setPointSize(12)
                painter.setFont(f)
                painter.setPen(QColor(_t.COLORS["danger"]) if label == "N"
                               else self._ink())
            else:
                f = QFont(self._font)
                f.setPointSize(8)
                painter.setFont(f)
                painter.setPen(dim)
            fm = QFontMetrics(painter.font())
            painter.drawText(QPointF(pos.x() - fm.horizontalAdvance(label) / 2.0,
                                     pos.y() + 5), label)
        painter.restore()

    # 太阳点源下限：宽 FOV 下真实视直径（≈0.53°）不足 2px，给下限保证可辨；
    # 与行星 "投影像素半径 < 2px 走点源" 同一条分辨率判据。
    SUN_POINT_MIN_R_PX = 2.0

    def _sun_screen_geom(self) -> Optional[Dict[str, object]]:
        """太阳标记的屏幕几何（绘制层与拾取层同一事实源）。

        地平门按**视高度**：几何高度先经显示层折射抬升，圆心可以略低于
        地平（几何约 -0.83° 时视高度约 -0.22°），只要上缘在地平以上就画。
        这正是真实日落「上缘贴地平、圆心已落下」的形态——不再把圆心夹到
        0°（旧实现夹取等于把太阳整体抬高，是显示层的说谎）。
        若历表没给视直径（r_deg=0），退化为"圆心在地平上才画"。

        尺寸：视直径来自 solar_system.get_sun_position（随日地距离变），
        经投影采样换算成像素；缺历表给 None（不编造角直径）。
        """
        s = self._sun
        if not s:
            return None
        try:
            az, alt = float(s["az"]), float(s["alt"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (math.isfinite(az) and math.isfinite(alt)):
            return None
        alt_draw = self._display_alt_deg(alt)
        r_deg = 0.0
        try:
            dia = float(s["diameter_deg"]) if s.get("diameter_deg") else None
        except (KeyError, TypeError, ValueError):
            dia = None
        if dia is not None and math.isfinite(dia) and dia > 0.0:
            r_deg = dia / 2.0
        if alt_draw < -r_deg:
            return None
        r = 0.0
        if r_deg > 0.0:
            geom = self._angular_radius_px(az, alt_draw, r_deg)
            if geom is not None:
                r = float(geom[1])
        r = max(r, self.SUN_POINT_MIN_R_PX)
        center = self._sky_to_screen_finite(az, alt_draw)
        if center is None:
            return None
        return {"center": center, "r": r, "az": az, "alt": alt,
                "alt_draw": alt_draw}

    def _draw_celestial_bodies(self, painter: QPainter):
        """绘制真实太阳/月亮标记（在地平线上方才画）。

        月亮的可见性判据与拾取层共用 moon_visible()，不各自持有阈值。
        太阳的可见性/尺寸/位置与拾取层共用 _sun_screen_geom()，同样不各持有阈值。
        """
        painter.save()
        sun_geom = self._sun_screen_geom()
        if sun_geom is not None:
            pos = sun_geom["center"]
            r = float(sun_geom["r"])
            col = self._colors["sun"]
            # 光晕随圆面走（下限 10px 让宽 FOV 下还找得到），
            # 圆面半径 = 真实视直径换算值（下限 2px 点源），不是写死 14/7。
            halo = max(10.0, r * 1.9)
            painter.setPen(QPen(col, 1))
            painter.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(), 60)))
            painter.drawEllipse(pos, halo, halo)
            painter.setBrush(QBrush(col))
            painter.drawEllipse(pos, r, r)
            painter.setPen(self._ink())
            painter.drawText(QPointF(pos.x() + halo + 4, pos.y() - 8), "太阳")
        frame = self._moon_screen_frame()
        if frame is not None:
            self._draw_moon_marker(painter, frame)
        painter.restore()

    def _moon_screen_frame(self) -> Optional[Dict[str, object]]:
        """月球亮区的屏幕几何（绘制层与拾取层同一事实源）。

        返回: center / radius_u_px / radius_v_px / radius_px(拾取用) /
        ux,uy(屏幕单位向量, 月心→太阳) / illum / cos_alpha。

        地平线下、视界外、或历表未喂入时返回 None——不画也不可点。
        亮缘朝向由 **az/alt 大圆切向** 决定（月心→太阳），再经投影换算到
        屏幕；不是"屏幕左右"近似。视直径从历表角直径换算成像素，随投影
        与 FOV 真实缩放。
        """
        m = self._moon
        if not m:
            return None
        try:
            az = float(m["az"])
            alt = float(m["alt"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (math.isfinite(az) and math.isfinite(alt)):
            return None
        # 月面也按视高度绘制/门控（同上：几何 -0.3° 的月亮视高度约 +0.1°）。
        alt_draw = self._display_alt_deg(alt)
        if not moon_visible(alt_draw):
            return None
        center = self._sky_to_screen_finite(az, alt_draw)
        if center is None:
            return None

        illum_raw = m.get("illum")
        try:
            illum: Optional[float] = (float(illum_raw)
                                      if illum_raw is not None else None)
        except (TypeError, ValueError):
            illum = None
        if illum is not None and not math.isfinite(illum):
            illum = None
        cos_alpha = moon_phase_cos_alpha(illum)

        try:
            dia_deg = float(m["diameter_deg"])
        except (KeyError, TypeError, ValueError):
            dia_deg = None
        if dia_deg is not None and (not math.isfinite(dia_deg) or dia_deg <= 0.0):
            dia_deg = None

        ru = rv = MOON_UNKNOWN_DIAMETER_RADIUS_PX
        ux = uy = None
        sun = self._sun
        sun_ok = bool(sun) and sun.get("az") is not None and sun.get("alt") is not None
        sun_alt_draw = (self._display_alt_deg(float(sun["alt"]))
                        if sun_ok else alt_draw)
        if dia_deg is not None:
            step = dia_deg / 2.0          # 角半径（度）
            p_on = p_perp = None
            if sun_ok:
                p_on = great_circle_offset_point(
                    az, alt_draw, float(sun["az"]), sun_alt_draw, step)
                p_perp = great_circle_perpendicular_point(
                    az, alt_draw, float(sun["az"]), sun_alt_draw, step)
            if p_on is None:
                # 太阳缺测、或太阳与月亮重合/对跖导致切向不唯一：
                # 只量尺度（沿正上方取角半径处的点），不声称亮缘朝向。
                p_on = (az, alt_draw + step) if alt_draw + step < 89.999 \
                    else (az, alt_draw - step)
                p_perp = None
            p_on_pt = self._sky_to_screen_finite(*p_on)
            if p_on_pt is not None:
                d = math.hypot(p_on_pt.x() - center.x(),
                               p_on_pt.y() - center.y())
                if d > 1e-6:
                    ru = rv = d
                    if sun_ok:
                        ux = (p_on_pt.x() - center.x()) / d
                        uy = (p_on_pt.y() - center.y()) / d
            if sun_ok and p_perp is not None:
                p_perp_pt = self._sky_to_screen_finite(*p_perp)
                if p_perp_pt is not None:
                    dv = math.hypot(p_perp_pt.x() - center.x(),
                                    p_perp_pt.y() - center.y())
                    if dv > 1e-6:
                        rv = dv

        radius_px = 0.5 * (ru + rv)
        if not self._on_screen(center, margin=4.0 + radius_px):
            return None
        return {
            "center": center,
            "radius_u_px": ru,
            "radius_v_px": rv,
            "radius_px": radius_px,
            "ux": ux, "uy": uy,
            "illum": illum,
            "cos_alpha": cos_alpha,
            "az": az, "alt": alt, "alt_draw": alt_draw,
        }

    def _draw_moon_marker(self, painter: QPainter,
                          frame: Dict[str, object]) -> None:
        """按真实相位画月面：亮缘半圆 + 晨昏线半椭圆，尺寸随视直径。"""
        center = frame["center"]
        assert isinstance(center, QPointF)
        ru = float(frame["radius_u_px"])       # type: ignore[arg-type]
        rv = float(frame["radius_v_px"])       # type: ignore[arg-type]
        ux, uy = frame["ux"], frame["uy"]
        col = self._colors["moon"]

        orient_known = ux is not None and uy is not None
        if not orient_known:
            # 朝向未知（太阳位置缺测）：只给月面边界，不填亮区——不编造方向。
            vx, vy = 0.0, 1.0
            ux, uy = 1.0, 0.0
        else:
            ux, uy = float(ux), float(uy)      # type: ignore[arg-type]
            vx, vy = -uy, ux
        transform = QTransform(ru * ux, ru * uy, rv * vx, rv * vy,
                               center.x(), center.y())

        painter.save()
        # 整盘轮廓：暗面不模拟地照（earthshine），只如实给月面边界。
        painter.setPen(QPen(col, 1))
        painter.setBrush(Qt.NoBrush)
        full = QPainterPath()
        full.addEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
        painter.drawPath(transform.map(full))

        cos_alpha = frame["cos_alpha"]
        if orient_known and cos_alpha is not None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(col))
            painter.drawPath(transform.map(moon_lit_path(float(cos_alpha))))

        r = max(ru, rv)
        # 标签只登记，本帧末尾与星名/座名统一避让（不压字）。
        self._queue_label(0.0, QPointF(center.x() + r + 8.0,
                                       center.y() - r - 4.0),
                          "月亮", self._ink())
        painter.restore()

    def _draw_selection_crosshair(self, painter: QPainter, pos: QPointF,
                                  base_radius: float, color: QColor) -> None:
        """选中标记：4 段圆弧十字 + 2s 呼吸（SWE 选中标记同形）。

        时间取自 self._breath_clock()（monotonic 秒），_refresh_timer 30fps
        重绘驱动呼吸；纯几何走 selection_crosshair_geometry 以便确定性测试。
        圆头粗线（.figma 设计语言）。"""
        geo = selection_crosshair_geometry(self._breath_clock(), base_radius)
        r = max(1.0, float(geo["radius"]))
        rect = QRectF(pos.x() - r, pos.y() - r, 2.0 * r, 2.0 * r)
        pen = QPen(color, 2)
        pen.setCapStyle(Qt.RoundCap)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for start_deg, sweep_deg in geo["dashes"]:
            painter.drawArc(rect, int(round(float(start_deg) * 16)),
                            int(round(float(sweep_deg) * 16)))
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
                # 受晒状态只消费后端字段：只有本影（umbra / sunlit=False）
                # 压暗本体 + 暗色虚线描边；受晒/半影/未知都保持原色，
                # 绝不伪造阴影。
                eclipsed = sunlit_is_eclipsed(
                    getattr(obj, "sunlit", None),
                    getattr(obj, "sunlit_status", None))
                body_color = (self._colors["sat_eclipse"]
                              if eclipsed else color)
                # 信号点亮：频带里听到它 -> 同心辉光环（由内向外渐淡）
                if obj.name in self._signal_sats:
                    for ring, (rr, aa) in enumerate(((size + 5, 90),
                                                     (size + 11, 45))):
                        halo = QColor(color.red(), color.green(),
                                      color.blue(), aa)
                        painter.setPen(QPen(halo, 2))
                        painter.setBrush(Qt.NoBrush)
                        painter.drawEllipse(pos, rr, rr)
                diamond = QPolygonF([
                    QPointF(pos.x(), pos.y() - size),
                    QPointF(pos.x() + size, pos.y()),
                    QPointF(pos.x(), pos.y() + size),
                    QPointF(pos.x() - size, pos.y()),
                ])
                painter.setPen(QPen(body_color, 2))
                painter.setBrush(QBrush(body_color))
                painter.drawPolygon(diamond)
                if eclipsed:
                    pen = QPen(self._colors["sat_eclipse_ring"], 1)
                    pen.setStyle(Qt.DashLine)
                    painter.setPen(pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawEllipse(pos, size + 4, size + 4)
                # 卫星名 + 仰角方位 + 频率作为一个块进统一避让：
                # 块内三行同进同退，块与星名/座名冲突时按 0.5 优先级占位
                # （卫星是操作对象，用户要靠它决定调谐/跟踪）。
                lines: List[Tuple[str, bool, float]] = [
                    (obj.name, False, -5.0),
                    (f"EL {format_angle_deg(obj.elevation_deg)}(几何) "
                     f"AZ {format_azimuth(obj.azimuth_deg)}",
                     True, 22.0),
                ]
                if obj.frequency_hz > 0:
                    lines.append((format_frequency_mhz(obj.frequency_hz),
                                  True, 36.0))
                self._queue_label_block(0.5, QPointF(pos.x() + 10, pos.y()),
                                        lines, self._ink())
            elif obj.obj_type == "signal":
                painter.setPen(QPen(color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(pos, 5, 5)
                painter.drawEllipse(pos, 9, 9)
                self._queue_label(0.5, QPointF(pos.x() + 12, pos.y() + 4),
                                  obj.name, self._ink())
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
                self._queue_label(0.5, QPointF(pos.x() + 10, pos.y() - 5),
                                  obj.name, self._ink())
            else:
                painter.setPen(QPen(color, 2))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(pos, 5, 5)
                self._queue_label(0.5, QPointF(pos.x() + 10, pos.y() + 4),
                                  obj.name, self._ink())

        # 选中对象：4 段圆弧十字 + 2s 呼吸（SWE 选中标记同形）
        if self._hovered_object is not None and self._hovered_object.visible \
                and self._hovered_object.is_above_horizon():
            hp = self._sky_to_screen(self._hovered_object.azimuth_deg,
                                     self._hovered_object.elevation_deg)
            self._draw_selection_crosshair(
                painter, hp, 14.0, self._colors["accent"])
        painter.restore()

    def _draw_picked_info(self, painter: QPainter):
        """选中天体信息卡 (Stellarium 规格 10.4)。"""
        obj = self._hovered_object
        if not obj:
            self._info_card_rect = None
            self._info_close_rect = None
            return
        pos = self._sky_to_screen(obj.azimuth_deg, obj.elevation_deg)

        # 选中标记（4 段圆弧十字 + 2s 呼吸）由 _draw_objects 统一绘制，
        # 此处不再重复画环，避免双标记。

        info = self._selected_info or {}
        _t = tokens()
        norad = info.get("norad")
        title = f"NORAD {norad}" if norad else obj.name
        kind_label = info.get("kind_label", "Satellite")

        rise = info.get("rise"); sset = info.get("set")

        def _hm(jd):
            if jd is None:
                return "..:.."
            return format_utc_time(_jtime.jd_to_unix(jd))

        kv = [
            ("Magnitude (mag)", "—" if info.get("mag") is None
             else format_magnitude(info["mag"])),
            ("Distance (km)", format_distance_km(info.get("dist_km", 0.0))),
            ("Ra/Dec (hms / °′″)",
             f"{format_hms_ra(info['ra'])}  {format_dms_deg(info['dec'])}"
             if info.get("ra") is not None else "—"),
            ("Az/Alt 几何 (Az 0°=N)",
             f"{format_azimuth(obj.azimuth_deg)}  "
             f"{format_angle_deg(obj.elevation_deg)}"),
            ("Rise/Set (UTC)", f"Rise {_hm(rise)}   Set {_hm(sset)}"),
        ]
        # 点星即听：该把收音机拧到哪（无则缺省行不画）
        if info.get("downlink_mhz"):
            dl = format_frequency_mhz(info['downlink_mhz'] * 1e6, digits=4)
            if info.get("downlink_mode"):
                dl += f" ({info['downlink_mode']})"
            kv.append(("Downlink", dl))
        # TLE 可追溯（B2）：来源 + 历元（有则画，无则如实缺省不画）
        if info.get("tle_epoch"):
            kv.append(("TLE 历元", str(info["tle_epoch"])))
        if info.get("tle_source"):
            kv.append(("TLE 来源", str(info["tle_source"])))

        painter.save()
        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        pad = 12.0
        close_reserve = 26.0          # 右上角 × 按钮占位
        key_col_w = max(fm.horizontalAdvance(k) for k, _ in kv)

        head = [title, kind_label,
                "Also known as: " + " · ".join(info.get("aliases", [obj.name]))
                + (f" · COSPAR {info['cospar']}" if info.get("cospar") else "")]
        widths = [fm.horizontalAdvance(t) for t in head]
        widths += [key_col_w + 10 + fm.horizontalAdvance(v) for _, v in kv]
        if info.get("culture"):
            widths.append(fm.horizontalAdvance(f"星性 {info['culture']}"))
        body_w = max(widths)
        box_w = body_w + pad * 2 + close_reserve
        text_w = box_w - pad * 2 - close_reserve   # 描述换行用的正文宽度

        desc_lines: List[str] = []
        raw_desc = (obj.description or "").strip()
        if raw_desc:
            cur = ""
            for ch in raw_desc:
                if ch == "\n":
                    desc_lines.append(cur); cur = ""; continue
                if fm.horizontalAdvance(cur + ch) > text_w and cur:
                    desc_lines.append(cur); cur = ch
                else:
                    cur += ch
            if cur:
                desc_lines.append(cur)
            if len(desc_lines) > 6:               # 封顶 6 行，超出截断
                desc_lines = desc_lines[:6]
                desc_lines[-1] = desc_lines[-1].rstrip("。，,;； ") + " …"

        row_h = 17.0
        n_rows = 3 + len(kv) + (1 if info.get("culture") else 0) \
            + (len(desc_lines) + 1 if desc_lines else 0)
        box_h = n_rows * row_h + 18
        box_x, box_y = 14.0, 40.0
        bg = QColor(_t.COLORS["card_1"])
        bg.setAlpha(235)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(self._colors["accent"], 1))
        painter.drawRoundedRect(QRectF(box_x, box_y, box_w, box_h), 6, 6)
        self._info_card_rect = QRectF(box_x, box_y, box_w, box_h)

        # × 关闭按钮（命中区暴露给 mousePressEvent）
        self._info_close_rect = QRectF(box_x + box_w - close_reserve,
                                       box_y + 3, 20, 20)
        painter.setPen(QPen(QColor(_t.COLORS["gray_300"]), 1))
        painter.drawText(self._info_close_rect, Qt.AlignCenter, "×")

        ty = box_y + 18
        f = QFont(self._font); f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(_t.COLORS["accent"]))
        painter.drawText(QPointF(box_x + pad, ty), title)
        ty += row_h
        painter.setFont(self._font)
        painter.setPen(QColor(_t.COLORS["gray_300"]))
        painter.drawText(QPointF(box_x + pad, ty), head[1])
        ty += row_h
        painter.drawText(QPointF(box_x + pad, ty), head[2])
        ty += row_h

        key_pen = QColor(_t.COLORS["gray_300"])
        val_pen = QColor(_t.COLORS["text_primary"])
        for k, v in kv:
            painter.setPen(key_pen)
            painter.drawText(QPointF(box_x + pad, ty), k)
            painter.setPen(val_pen)
            painter.drawText(QPointF(box_x + pad + key_col_w + 10, ty), v)
            ty += row_h

        if info.get("culture"):
            painter.setPen(key_pen)
            painter.drawText(QPointF(box_x + pad, ty), f"星性 {info['culture']}")
            ty += row_h

        if desc_lines:
            ty += row_h - 8              # 描述段前空一行
            painter.setPen(QColor(_t.COLORS["gray_300"]))
            for dl in desc_lines:
                painter.drawText(QPointF(box_x + pad, ty), dl)
                ty += row_h
        painter.restore()

    # ------------------------------------------------------------------ #
    # 行星层: 标记 + 标签 + 拾取 + 信息卡 (Stellarium 首屏玩法)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _planet_positions(jd: float):
        """当前时刻全部行星 J2000 视位置 (Meeus 低精度历表, 库内已验证)。"""
        from mbdsdr_ai.astronomy import planets as _planets
        return _planets.compute_all(jd)

    def _angular_radius_px(self, az: float, alt: float, radius_deg: float) \
            -> Optional[Tuple[QPointF, float]]:
        """角半径（度）→ (屏幕圆心, 像素半径)。

        走投影采样（与 FOV/离轴位置无关地真实缩放），不是写死像素：
        行星圆面、深空天体视大小、太阳视直径共用这一条换算。
        """
        if radius_deg is None or radius_deg <= 0.0:
            return None
        center = self._sky_to_screen_finite(az, alt)
        if center is None:
            return None
        probe = ((az, alt + radius_deg) if alt + radius_deg < 89.999
                 else (az, alt - radius_deg))
        p = self._sky_to_screen_finite(*probe)
        if p is None:
            return None
        r = math.hypot(p.x() - center.x(), p.y() - center.y())
        if r <= 0.0:
            return None
        return center, r

    def _planet_screen_disc(self, az: float, alt: float, diameter_deg: float
                            ) -> Optional[Dict[str, object]]:
        """行星圆面的屏幕几何：像素半径 + 月心/行心指向太阳的屏幕方向。

        视直径换算成像素走投影采样（与 FOV/离轴位置无关地真实缩放），
        不是写死像素。太阳位置缺测时 ux/uy=None（不编造相位朝向）。
        """
        if diameter_deg is None or diameter_deg <= 0.0:
            return None
        step = float(diameter_deg) / 2.0        # 角半径（度）
        geom = self._angular_radius_px(az, alt, step)
        if geom is None:
            return None
        center, r = geom
        ux = uy = None
        sun = self._sun
        if sun and sun.get("az") is not None and sun.get("alt") is not None:
            on = great_circle_offset_point(
                az, alt, float(sun["az"]), float(sun["alt"]), step)
            if on is not None:
                p_on = self._sky_to_screen_finite(*on)
                if p_on is not None:
                    dx = p_on.x() - center.x()
                    dy = p_on.y() - center.y()
                    n = math.hypot(dx, dy)
                    if n > 1e-6:
                        ux, uy = dx / n, dy / n
        return {"center": center, "r": r, "ux": ux, "uy": uy}

    def _draw_planet_disc(self, painter: QPainter, scr: QPointF,
                          disc: Dict[str, object], pos_eph: object,
                          color: tuple, alpha: int) -> None:
        """已分辨行星：画真实大小圆面 + 历表相位亮区。"""
        r = float(disc["r"])
        ux, uy = disc["ux"], disc["uy"]
        orient_known = ux is not None and uy is not None
        if not orient_known:
            ux, uy = 1.0, 0.0
        else:
            ux, uy = float(ux), float(uy)      # type: ignore[arg-type]
        vx, vy = -uy, ux
        transform = QTransform(r * ux, r * uy, r * vx, r * vy, scr.x(), scr.y())
        cr, cg, cb = color
        painter.setPen(QPen(QColor(cr, cg, cb, 90), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(scr, r, r)
        illum = planet_phase_illumination(
            getattr(pos_eph, "phase_angle_deg", None))
        if orient_known and illum is not None:
            cos_alpha = max(-1.0, min(1.0, 2.0 * illum - 1.0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(cr, cg, cb, alpha)))
            painter.drawPath(transform.map(moon_lit_path(cos_alpha)))

    def _draw_planets(self, painter: QPainter):
        """行星标记 (实心圆点 + 中文名标签), 位置随时间引擎流动。

        快转时间时行星沿黄道漂移 —— "历"叙事的一部分。低于地平（alt<0）不画：
        与恒星/月亮同一条地平门，不引入未标定的折射抬升（本视图不画地平线下
        天体；太阳是唯一例外，见 _draw_celestial_bodies 的日出日落处理）。
        大小/亮度/辉光走恒星同一条 star_draw(有效星等, Bortle) 模型
        （SWE core_get_point_for_mag 语义：所有天体共用一套亮度模型），
        只保留各自的识别色——于是金星(-4.7)明显大于木星(-1.9)大于土星(0.9)，
        而海王星(7.8)在 Bortle 3 的裸眼极限 6.5 等之外，如实不画。

        视直径可分辨时（窄 FOV，投影像素半径 >= 2px）改用历表的真实角直径
        画圆面，并按历表相位角填亮区（水星/金星相位最明显）；不可分辨时仍是
        点源标记——这是分辨率下限，不是把角直径丢掉。星等/亮度始终来自同一
        份 compute_all 数据，与信息卡一致。
        """
        gs = self._ground_station()
        if gs is None:
            return
        jd = self._current_jd()
        from mbdsdr_ai.celestial_geometry import azalt_from_vec
        painter.save()
        painter.setFont(self._font)
        try:
            positions = self._planet_positions(jd)
        except Exception:
            positions = {}
        for key, style in _PLANET_STYLE.items():
            pos_eph = positions.get(key)
            if pos_eph is None:
                continue
            try:
                v = j2000_to_altaz(vec_from_radec(pos_eph.ra_deg,
                                                  pos_eph.dec_deg), gs, jd)
                az, alt = azalt_from_vec(v)
            except Exception:
                continue
            alt_draw = self._display_alt_deg(alt)
            if alt_draw < 0.0:
                continue        # 地平线下不画（视高度门，与恒星/月亮同一判据）
            scr = self._sky_to_screen(az, alt_draw)
            if not self._on_screen(scr):
                continue
            mag = getattr(pos_eph, "magnitude", None)
            if mag is None:
                continue        # 历表没给星等就不画，不拿默认值冒充亮度
            r, bright, bloom = _stars.star_draw(
                float(mag) + _stars.extinction_delta_mag(alt), self._bortle)
            if r <= 0:
                continue        # 裸眼极限外：如实不可见（搜索/信息卡仍可查到）
            cr, cg, cb = style["color"]  # type: ignore[misc]
            alpha = int(255 * (0.35 + 0.65 * bright))
            if bloom > 0.0:
                for ring in range(3):
                    rr = r * (1.7 + 0.9 * ring)
                    glow_a = int(70 * bloom * (0.5 ** ring))
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(QColor(cr, cg, cb, glow_a)))
                    painter.drawEllipse(scr, rr, rr)
            disc = self._planet_screen_disc(
                az, alt_draw, float(getattr(pos_eph, "angular_diameter_arcsec", 0.0)) / 3600.0)
            if disc is not None and float(disc["r"]) >= 2.0:
                # 视直径已可分辨（窄 FOV）：按真实圆面画，并按历表相位角填亮区。
                self._draw_planet_disc(painter, scr, disc, pos_eph,
                                       (cr, cg, cb), alpha)
                r = max(r, float(disc["r"]))
            else:
                # 未分辨（点源）：沿用星等→像素模型。
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor(cr, cg, cb, alpha)))
                painter.drawEllipse(scr, r, r)
            # 行星名与月名同优先级（本片天空的主角），末尾统一避让。
            self._queue_label(0.0, QPointF(scr.x() + r + 4, scr.y() - r - 2),
                              str(style["zh"]),
                              QColor(210, 220, 235, int(200 * bright + 40)))
        painter.restore()

    # ------------------------------------------------------------------ #
    # 行星/恒星/DSO 拾取 (cleverFind 同款双键评分: 像素角距 + 亮度优先)
    # ------------------------------------------------------------------ #
    def _find_body_at(self, x: float, y: float,
                      tolerance: float = 14.0) -> Optional[Dict[str, object]]:
        """在像素 (x,y) 附近找行星/恒星/已选 DSO (cleverFind 双键评分)。

        恒星与行星混合候选, 亮度归一 = clamp((4.5-mag)/9): 亮者近者优先。
        候选门槛与绘制层同一条可见性规则（star_draw + 消光 + 光害极限星等 +
        各自的地平下限）：画出来的才给点，没画的不给点。未命中返回 None。
        月球单独走 _moon_screen_frame() —— 与绘制层同一份位置/尺寸/可见性，
        且拾取半径随画出的月面大小扩张（看得见的月面都能点中）。行星同理：
        视直径已分辨（半径 >= 2px 的真实圆面）时拾取半径同样扩张到圆面边缘，
        不再出现"圆面画了 20px、只有中心 14px 能点"的看得到却点不中。
        """
        gs = self._ground_station()
        if gs is None:
            return None
        jd = self._current_jd()
        from mbdsdr_ai.celestial_geometry import azalt_from_vec

        candidates: List[Tuple[str, str, float, float, float,
                                Optional[float]]] = []
        # (kind, key, ra, dec, mag, 角直径°) — 位置/尺寸统一在这里算一次
        try:
            for key_p, eph in self._planet_positions(jd).items():
                dia = getattr(eph, "angular_diameter_arcsec", None)
                candidates.append(("planet", key_p, eph.ra_deg, eph.dec_deg,
                                   eph.magnitude,
                                   float(dia) / 3600.0 if dia else None))
        except Exception:
            pass
        # 恒星候选改由当帧 select_for_view 集合直接给出（见循环尾部）；
        # 不再从 merged_star_index 另取一套坐标。
        # DSO 图层已画 → 星表里画得出来的都能点（与绘制层同一门：白天/地平/
        # 星等亮度三条都在下面循环里统一判）。
        if not self._stars_hidden_by_daylight():
            try:
                from mbdsdr_ai.astronomy import dso as _dso
                for e in _dso.BUILTIN_CATALOG:
                    size = dso_size_arcmin(e)
                    candidates.append(("dso", e.name, e.ra_deg, e.dec_deg,
                                       dso_magnitude(e),
                                       None if size is None else size / 60.0))
            except Exception:
                pass
        # 已选 DSO 有标记环, 可再点中（星表内条目用真实星等；目录外不给点）
        pb = self._picked_body
        if pb and pb.get("kind") == "dso":
            ra_d, de_d = pb.get("ra_deg"), pb.get("dec_deg")
            ent = self._dso_entry(str(pb.get("key")))
            if ra_d is not None and ent is not None:
                size = dso_size_arcmin(ent)
                candidates.append(("dso", ent.name, float(ra_d),
                                   float(de_d), dso_magnitude(ent),
                                   None if size is None else size / 60.0))

        best: Optional[Dict[str, object]] = None
        best_score: Optional[float] = None
        # 太阳：绘制层与拾取层共用 _sun_screen_geom()；给最高优先分，
        # 与行星/恒星重合时太阳先被点中（它是画面上最亮的天体）。
        sun_geom = self._sun_screen_geom()
        if sun_geom is not None:
            scr_s = sun_geom["center"]
            assert isinstance(scr_s, QPointF)
            dist_s = math.hypot(scr_s.x() - x, scr_s.y() - y)
            if dist_s < max(float(tolerance), float(sun_geom["r"])):
                best_score = dist_s * 0.5
                best = {"kind": "sun", "key": "sun"}
        for kind, key_b, ra_b, dec_b, mag, dia_deg in candidates:
            if kind == "planet" and key_b == "moon":
                # 月球：绘制层与拾取层共用同一份屏幕几何。不用 compute_all 的
                # geocentric RA/Dec 重投影——那是另一份历表/另一套坐标（含月球
                # 视差），窄 FOV 下会和真实画出的月面错开。
                frame = self._moon_screen_frame()
                if frame is None:
                    continue        # 未绘制（地平下/视界外/历表缺）→ 不可点
                scr = frame["center"]
                assert isinstance(scr, QPointF)
                dist = math.hypot(scr.x() - x, scr.y() - y)
                limit = max(float(tolerance), float(frame["radius_px"]))
                if dist >= limit:
                    continue
                if best_score is None or dist < best_score:
                    best_score = dist
                    best = {"kind": "planet", "key": "moon", "zh": "月亮"}
                continue
            try:
                v = j2000_to_altaz(vec_from_radec(ra_b, dec_b), gs, jd)
                az, alt = azalt_from_vec(v)
            except Exception:
                continue
            # 与绘制层同一条可见性门：画不出来的不给点。否则点空白天区
            # 会弹出肉眼看不见的天体信息卡（Bortle 3 的海王星 r<=0 不画），
            # 而画出来的暗星又点不中（旧的写死 4.5 等砍掉了 4.84 的 Graffias）。
            # DSO 也走这一条（不再有"标记环不看星等"的第二套阈值）。
            alt_draw = self._display_alt_deg(alt)
            if alt_draw < 0.0 or mag is None:
                continue
            r_pick, _, _ = _stars.star_draw(
                float(mag) + _stars.extinction_delta_mag(alt), self._bortle)
            if r_pick <= 0:
                continue
            scr = self._sky_to_screen_finite(az, alt_draw)
            if scr is None:
                continue
            # 拾取半径跟随"画出来的大小"：已分辨的行星圆面用圆面半径，
            # 与 _draw_planets 里 disc["r"] >= 2.0 走真实圆面同一条判据。
            limit = max(float(tolerance), r_pick)
            if kind == "planet" and dia_deg is not None and dia_deg > 0.0:
                disc = self._planet_screen_disc(az, alt_draw, float(dia_deg))
                if disc is not None and float(disc["r"]) >= 2.0:
                    limit = max(limit, float(disc["r"]))
            elif kind == "dso" and dia_deg is not None and dia_deg > 0.0:
                # 与绘制同一个限幅事实源：符号被截断时命中区同步收窄，
                # 不许「符号小、命中大」吃掉视口里其它天体。
                r_sym, _clamped = self._dso_symbol_geometry(
                    az, alt_draw, float(dia_deg) * 60.0)
                limit = max(limit, r_sym)
            dist = math.hypot(scr.x() - x, scr.y() - y)
            if dist >= limit:
                continue
            norm = max(0.0, min(1.0, (4.5 - mag) / 9.0))
            score = dist * (1.0 - 0.3 * norm)
            if best_score is None or score < best_score:
                best_score = score
                body: Dict[str, object] = {"kind": kind, "key": key_b}
                if kind == "planet":
                    body["zh"] = _PLANET_STYLE.get(key_b, {}).get("zh", key_b)
                if kind == "dso":
                    body["ra_deg"], body["dec_deg"] = ra_b, dec_b
                best = body
        # ---- 恒星：只迭代当帧实际绘制的 select_for_view 结果 ----
        # 不重投影、不另取 141 星坐标；画得出来才可点（同一份 az/alt、
        # 同一份 eff_mag、同一个 star_draw 亮度门）。
        sel = self._star_sel
        if sel is None and not self._stars_hidden_by_daylight():
            sel = self._star_selection(gs, jd)
            self._star_sel = sel
        if sel is not None and not self._stars_hidden_by_daylight():
            for i in range(len(sel)):
                try:
                    az = float(sel.az_deg[i])
                    alt = float(sel.alt_deg[i])
                    mag_eff = float(sel.eff_mag[i])
                    vmag = float(sel.vmag[i])
                except Exception:
                    continue
                alt_draw = self._display_alt_deg(alt)
                if alt_draw < 0.0:
                    continue
                # 与 _draw_stars 同一份 _star_visual（含 LOD 放出的暗星）。
                r_pick = self._star_visual(
                    mag_eff, sel.limit_mag, self._bortle)[0]
                if r_pick <= 0:
                    continue
                scr = self._sky_to_screen_finite(az, alt_draw)
                if scr is None or not self._on_screen(scr):
                    continue
                limit = max(float(tolerance), r_pick)
                dist = math.hypot(scr.x() - x, scr.y() - y)
                if dist >= limit:
                    continue
                norm = max(0.0, min(1.0, (4.5 - vmag) / 9.0))
                score = dist * (1.0 - 0.3 * norm)
                if best_score is None or score < best_score:
                    try:
                        hyg = int(sel.hyg_id[i])
                    except Exception:
                        hyg = 0
                    name = str(sel.name[i] or "")
                    best_score = score
                    # key 保持旧的「具名星用名」契约（星座/白昼等既有测试与
                    # 选中态按名标识）；唯一标识仍是 hyg_id，坐标/星等只来自
                    # 当帧 select_for_view 行，不另存第二套。
                    best = {
                        "kind": "star",
                        "key": name if name else f"HYG{hyg}",
                        "name": name,
                        "hyg_id": hyg,
                        "ra_deg": float(sel.ra_deg[i]),
                        "dec_deg": float(sel.dec_deg[i]),
                        "vmag": vmag,
                        "bv": float(sel.bv[i]),
                        "dist_pc": float(sel.dist_pc[i]),
                        "spect": str(sel.spect[i] or ""),
                        "con": str(sel.con[i] or ""),
                    }
        return best

    def on_body_picked(self, body: Optional[Dict[str, object]]) -> None:
        """选中行星/恒星/DSO: 高亮环 + 信息卡。None = 反选。

        与卫星选中态互斥; 不发 object_clicked (那是卫星调谐信号)。
        """
        if body is None:
            self._picked_body = None
            self._body_info = None
            self.update()
            return
        self._hovered_object = None
        self._selected_info = None
        # 选中体不可调谐: 隐藏卫星三件套, 不残留假状态
        self._tune_sat_name = None
        self._stop_track_ui()
        self._track_btn.hide()
        self._stop_gimbal_ui()
        self._gimbal_btn.hide()
        self._camera_btn.hide()
        # 选中体非卫星：摄像机锁定无目标可绑，解锁（诚实）
        self._unlock_camera("")
        self._tune_btn.hide()
        self._doppler_label.hide()
        self._tune_refresh_timer.stop()
        self._picked_body = dict(body)
        self._build_body_info(self._picked_body)
        self.update()

    def _body_rise_set(self, ra_deg: float, dec_deg: float, jd_now: float,
                       hours: float = 24.0, step_min: float = 10.0,
                       threshold_alt_deg: float = 0.0) \
            -> Dict[str, Optional[float]]:
        """恒星/行星的升降: 24h 高度扫描 (与卫星 next_rise_set 同思路)。

        用与绘制完全相同的坐标链 (j2000_to_altaz), 不引入第二套公式。
        拱极/24h 不升落 -> 对应项 None, 卡片如实显示。

        threshold_alt_deg 是**几何高度**判据：太阳的标准日出日落是圆心
        -0.833°（已含折射 34′ + 视半径 16′，Meeus 口径），所以太阳传
        -0.833，其余天体传 0。判据不叠加显示层折射（否则重复计一次）。
        """
        gs = self._ground_station()
        out: Dict[str, Optional[float]] = {"rise_jd": None, "set_jd": None}
        if gs is None:
            return out
        v = vec_from_radec(ra_deg, dec_deg)
        n = int(hours * 60.0 / step_min)
        prev_above: Optional[bool] = None
        for i in range(n + 1):
            jd = jd_now + (i * step_min * 60.0) / 86400.0
            try:
                from mbdsdr_ai.celestial_geometry import azalt_from_vec
                az, alt = azalt_from_vec(j2000_to_altaz(v, gs, jd))
            except Exception:
                continue
            above = alt > threshold_alt_deg
            if prev_above is False and above and out["rise_jd"] is None:
                out["rise_jd"] = jd
            if prev_above is True and not above and out["set_jd"] is None:
                out["set_jd"] = jd
            prev_above = above
            if out["rise_jd"] is not None and out["set_jd"] is not None:
                break
        return out

    def _build_body_info(self, body: Dict[str, object]) -> None:
        """构建行星信息卡字段 (Stellarium 规格同款行: Mag/Dist/RaDec/AzAlt/
        Visibility)。无值给占位, 不编造。"""
        info: Dict[str, object] = {
            "kind": body.get("kind"), "title": "", "kind_label": "",
            "aliases": [], "mag": None, "dist_text": "—",
            "ra": None, "dec": None, "rise": None, "set": None,
            "desc": "",
        }
        kind = body.get("kind")
        if kind == "planet":
            key = str(body.get("key"))
            jd = self._current_jd()
            pos = self._planet_positions(jd).get(key)
            if key == "moon":
                # 月球走同一条历表（compute_all 含 moon）与同一张信息卡骨架，
                # 但它不是行星：标题给中文"月亮"、类别标"Moon"。_PLANET_STYLE
                # 无 moon 项，这里显式补齐而不塞进行星样式表——塞进去会让
                # _draw_planets 给月球多画一颗行星点，与 _draw_celestial_bodies
                # 的大标记重叠（月球由后者专画）。
                info["title"] = "月亮"
                info["kind_label"] = "Moon"
                info["aliases"] = ["Luna"]
            else:
                zh = str(body.get("zh") or _PLANET_STYLE.get(key, {}).get("zh", key))
                info["title"] = zh
                info["kind_label"] = "Planet"
                info["aliases"] = [key.capitalize()]
            if pos is not None:
                info["mag"] = float(pos.magnitude)
                info["ra"] = float(pos.ra_deg)
                info["dec"] = float(pos.dec_deg)
                info["dist_text"] = format_distance_au(pos.distance_au)
                if key == "moon":
                    # 照度 = 0.5*(1+cos 相位角)，直接从历表已有的相位角导出，
                    # 是月球最直观的一条事实（满/弦/新月），不引入新模型。
                    illum = 0.5 * (1.0 + math.cos(math.radians(pos.phase_angle_deg)))
                    info["desc"] = (
                        f"照度 {format_percent(illum)} · "
                        f"视直径 {format_arcsec(pos.angular_diameter_arcsec)} · "
                        f"相位角 {format_angle_deg(pos.phase_angle_deg)} · "
                        f"Meeus ch.47 月球理论 (J2000 视位置)")
                else:
                    info["desc"] = (
                        f"视直径 {format_arcsec(pos.angular_diameter_arcsec)} · "
                        f"相位角 {format_angle_deg(pos.phase_angle_deg)} · "
                        f"Meeus 低精度历表 (J2000 视位置)")
                rs = self._body_rise_set(pos.ra_deg, pos.dec_deg, jd)
                info["rise"] = rs["rise_jd"]
                info["set"] = rs["set_jd"]
        elif kind == "star":
            if body.get("ra_deg") is not None:
                # 全天星表选中：数据全部来自当帧 select_for_view 行，
                # 不另查第二套坐标/第二份星等。
                name = str(body.get("name") or "")
                label = name if name else f"HYG {body.get('hyg_id')}"
                c = _stars.star_culture(name) if name else None
                info["title"] = c["zh"] if c else label
                info["kind_label"] = "Star"
                info["aliases"] = [name] if name else []
                info["mag"] = float(body["vmag"])
                info["ra"] = float(body["ra_deg"])
                info["dec"] = float(body["dec_deg"])
                dist_pc = float(body.get("dist_pc") or 0.0)
                info["dist_text"] = (format_distance_ly(dist_pc * 3.26156)
                                     if dist_pc > 0 else "—")
                jd = self._current_jd()
                rs = self._body_rise_set(info["ra"], info["dec"], jd)
                info["rise"] = rs["rise_jd"]
                info["set"] = rs["set_jd"]
                desc_bits = []
                if body.get("spect"):
                    desc_bits.append(f"光谱型 {body['spect']}")
                con = _constel.CONSTELLATION_NAMES.get(str(body.get("con") or ""), "")
                if con:
                    desc_bits.append(f"星座 {con}")
                if c and c.get("note"):
                    desc_bits.append(c["note"])
                desc_bits.append("HYG v4.1")
                info["desc"] = " · ".join(desc_bits)
            else:
                # 兼容旧 body（只有 key）：仅内置具名星能查到
                name = str(body.get("key"))
                star = _constel.merged_star_index().get(name)
                if star is not None:
                    c = _stars.star_culture(name)
                    info["title"] = c["zh"] if c else name
                    info["kind_label"] = "Star"
                    info["aliases"] = [name]
                    info["mag"] = float(star.vmag)
                    info["ra"] = float(star.ra_deg)
                    info["dec"] = float(star.dec_deg)
                    info["dist_text"] = (format_distance_ly(star.dist_ly)
                                         if star.dist_ly > 0 else "—")
                    jd = self._current_jd()
                    rs = self._body_rise_set(star.ra_deg, star.dec_deg, jd)
                    info["rise"] = rs["rise_jd"]
                    info["set"] = rs["set_jd"]
                    desc_bits = []
                    if star.spect:
                        desc_bits.append(f"光谱型 {star.spect}")
                    if c and c.get("note"):
                        desc_bits.append(c["note"])
                    if desc_bits:
                        info["desc"] = " · ".join(desc_bits)
        elif kind == "dso":
            name = str(body.get("key"))
            entry = self._dso_entry(name)
            info["title"] = name
            # 星表点选来的 body 只带 kind/key/ra/dec；类型与星座从星表补齐
            dso_type = (body.get("dso_type")
                        or (entry.dso_type if entry is not None else None)
                        or "Deep Sky Object")
            info["kind_label"] = str(dso_type).replace("_", " ").title()
            con_code = (body.get("con")
                        or (entry.constellation if entry is not None else "")
                        or "")
            con_full = _constel.CONSTELLATION_NAMES.get(str(con_code), "")
            info["aliases"] = [con_full] if con_full else []
            if entry is not None:
                mag = dso_magnitude(entry)
                if mag is not None:
                    info["mag"] = mag
                info["ra"] = float(entry.ra_deg)
                info["dec"] = float(entry.dec_deg)
                size = dso_size_arcmin(entry)
                bits = [("角大小 " + format_arcmin(size)) if size is not None
                        else "角大小 未知"]
                if str(dso_type) == "galaxy":
                    ratio = dso_axis_ratio(entry)
                    bits.append(f"轴比 {ratio:.2f}" if ratio is not None
                                else "轴比未知")
                if mag is None:
                    bits.append("星等未知")
                bits.append("公开星表数据")
                info["desc"] = " · ".join(bits)
                jd = self._current_jd()
                rs = self._body_rise_set(entry.ra_deg, entry.dec_deg, jd)
                info["rise"] = rs["rise_jd"]
                info["set"] = rs["set_jd"]
        elif kind == "sun":
            # 太阳走与行星同一张卡骨架：RA/Dec、视直径、距离来自
            # solar_system.get_sun_position；缺历表如实留占位。
            s = self._sun or {}
            info["title"] = "太阳"
            info["kind_label"] = "Star"
            info["aliases"] = ["Sol"]
            info["mag"] = -26.74          # 标准视星等常数（非历表计算）
            ra_s = s.get("ra_deg")
            de_s = s.get("dec_deg")
            if ra_s is not None and de_s is not None:
                try:
                    info["ra"], info["dec"] = float(ra_s), float(de_s)
                except (TypeError, ValueError):
                    pass
            dist_au = s.get("distance_au")
            if dist_au:
                info["dist_text"] = format_distance_au(float(dist_au), digits=5)
            dia = s.get("diameter_deg")
            if dia:
                info["desc"] = (f"视直径 {format_arcmin(float(dia) * 60.0)} · "
                                f"视星等 -26.74（标准常数） · "
                                f"solar_system 视位置")
            else:
                info["desc"] = "太阳历表缺测：只给方位/高度"
            if info["ra"] is not None and info["dec"] is not None:
                jd = self._current_jd()
                rs = self._body_rise_set(info["ra"], info["dec"], jd,
                                         threshold_alt_deg=-0.833)
                info["rise"] = rs["rise_jd"]
                info["set"] = rs["set_jd"]
                info["rise_set_frame"] = "几何 -0.833°"
        self._body_info = info

    @staticmethod
    def _dso_entry(name: str):
        from mbdsdr_ai.astronomy import dso as _dso
        for e in _dso.BUILTIN_CATALOG:
            if e.name == name:
                return e
        return None

    def _draw_body_info(self, painter: QPainter):
        """行星/恒星/DSO 信息卡: 与卫星卡同骨架 (Stellarium 规格)。"""
        body = self._picked_body
        info = self._body_info or {}
        if not body or not info:
            self._info_card_rect = None
            self._info_close_rect = None
            return
        # 高亮环: 实时位置重算 (快转时跟着走)
        gs = self._ground_station()
        if gs is not None and info.get("ra") is not None:
            try:
                from mbdsdr_ai.celestial_geometry import azalt_from_vec
                v = j2000_to_altaz(vec_from_radec(info["ra"], info["dec"]),
                                   gs, self._current_jd())
                az, alt = azalt_from_vec(v)
                pos = self._sky_to_screen(az,
                                          max(self._display_alt_deg(alt), -2.0))
                # 选中呼吸环（SWE 月/行星标记同形：2s 一吸一呼的虚线环）；
                # 卫星用 4 段十字（_draw_selection_crosshair），天体用呼吸环区分。
                rr = max(1.0, breathing_ring_radius(self._breath_clock(), 16.0))
                pen = QPen(self._colors["accent"], 2, Qt.DashLine)
                pen.setCapStyle(Qt.RoundCap)
                painter.save()
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(pos, rr, rr)
                painter.restore()
            except Exception:
                pass

        _t = tokens()

        def _hm(jd):
            if jd is None:
                return "..:.."
            return format_utc_time(_jtime.jd_to_unix(jd))

        rise = info.get("rise"); sset = info.get("set")
        if rise is None and sset is None:
            vis = "24h 内无升降 (拱极或不可见)"
        else:
            vis = f"Rise {_hm(rise)}   Set {_hm(sset)}"
        kv = [
            ("Magnitude (mag)", "—" if info.get("mag") is None
             else format_magnitude(info["mag"])),
            ("Distance", str(info.get("dist_text") or "—")),
            ("Ra/Dec (hms / °′″)",
             f"{format_hms_ra(info['ra'])}  {format_dms_deg(info['dec'])}"
             if info.get("ra") is not None else "—"),
            ("Az/Alt (视/几何, Az 0°=N)", "—"),   # 实时值在绘制循环尾部刷新
            ("Rise/Set (UTC)" + (f" · {info['rise_set_frame']}"
                                 if info.get("rise_set_frame") else ""), vis),
        ]

        painter.save()
        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        pad = 12.0
        close_reserve = 26.0
        key_col_w = max(fm.horizontalAdvance(k) for k, _ in kv)

        head = [str(info.get("title") or ""), str(info.get("kind_label") or ""),
                "Also known as: " + " · ".join(info.get("aliases") or ["—"])]
        widths = [fm.horizontalAdvance(t) for t in head]
        widths += [key_col_w + 10 + fm.horizontalAdvance(v) for _, v in kv]
        raw_desc = str(info.get("desc") or "")
        body_w = max(widths)   # 卡宽由键值行决定; 描述换行适配, 不撑宽
        box_w = body_w + pad * 2 + close_reserve
        text_w = box_w - pad * 2 - close_reserve

        # 描述换行（与卫星卡同款）：长口径注记不再把卡撑出屏
        desc_lines: List[str] = []
        if raw_desc:
            cur = ""
            for ch in raw_desc:
                if ch == "\n":
                    desc_lines.append(cur); cur = ""; continue
                if fm.horizontalAdvance(cur + ch) > text_w and cur:
                    desc_lines.append(cur); cur = ch
                else:
                    cur += ch
            if cur:
                desc_lines.append(cur)
            if len(desc_lines) > 6:
                desc_lines = desc_lines[:6]
                desc_lines[-1] = desc_lines[-1].rstrip("。，,;； ") + " …"

        row_h = 17.0
        n_rows = 3 + len(kv) + (len(desc_lines) + 1 if desc_lines else 0)
        box_h = n_rows * row_h + 18
        box_x, box_y = 14.0, 40.0
        bg = QColor(_t.COLORS["card_1"])
        bg.setAlpha(235)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(self._colors["accent"], 1))
        painter.drawRoundedRect(QRectF(box_x, box_y, box_w, box_h), 6, 6)
        self._info_card_rect = QRectF(box_x, box_y, box_w, box_h)

        self._info_close_rect = QRectF(box_x + box_w - close_reserve,
                                       box_y + 3, 20, 20)
        painter.setPen(QPen(QColor(_t.COLORS["gray_300"]), 1))
        painter.drawText(self._info_close_rect, Qt.AlignCenter, "×")

        ty = box_y + 18
        f = QFont(self._font); f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(_t.COLORS["accent"]))
        painter.drawText(QPointF(box_x + pad, ty), head[0])
        ty += row_h
        painter.setFont(self._font)
        painter.setPen(QColor(_t.COLORS["gray_300"]))
        painter.drawText(QPointF(box_x + pad, ty), head[1])
        ty += row_h
        painter.drawText(QPointF(box_x + pad, ty), head[2])
        ty += row_h

        key_pen = QColor(_t.COLORS["gray_300"])
        val_pen = QColor(_t.COLORS["text_primary"])
        for idx, (k, v) in enumerate(kv):
            if k.startswith("Az/Alt (视/几何"):
                # 实时坐标: 与行星标记同一坐标链现算；两个口径同时给出：
                # 绘制位置用视高度，几何高度保留给指向/对照。
                v = "—"
                if gs is not None and info.get("ra") is not None:
                    try:
                        from mbdsdr_ai.celestial_geometry import azalt_from_vec
                        vv = j2000_to_altaz(vec_from_radec(info["ra"],
                                                           info["dec"]),
                                            gs, self._current_jd())
                        az, alt = azalt_from_vec(vv)
                        v = f"{format_azimuth(az)}  {self._format_alt_pair(alt)}"
                    except Exception:
                        v = "—"
            painter.setPen(key_pen)
            painter.drawText(QPointF(box_x + pad, ty), k)
            painter.setPen(val_pen)
            painter.drawText(QPointF(box_x + pad + key_col_w + 10, ty), v)
            ty += row_h

        if desc_lines:
            ty += row_h - 8
            painter.setPen(key_pen)
            for dl in desc_lines:
                painter.drawText(QPointF(box_x + pad, ty), dl)
                ty += row_h
        painter.restore()

    @staticmethod
    def _parse_distance_km(obj: SkyObject) -> float:
        import re
        m = re.search(r"距离\s*([0-9,]+(?:\.[0-9]+)?)\s*km",
                      obj.description or "")
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
        return 0.0

    @staticmethod
    def _estimate_doppler_hz(obj: SkyObject) -> float:
        if obj.frequency_hz <= 0:
            return 0.0
        import re
        v_rel = 0.0
        m = re.search(r"径向速度\s*([+-]?[0-9.]+)\s*km/s",
                      obj.description or "")
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
        """卫星轨迹（半透明虚线，端部淡出）。

        只画地平线以上的可见段（el<=0 断开）；端部 ~15% 段数做
        alpha 渐入/渐出（Stellarium 轨道线端部淡出同效果），中段满
         alpha=110。逐段插 alpha，n 段 n-1 条线段。
        """
        painter.save()
        for name, points in self._trajectories.items():
            if len(points) < 2:
                continue
            color = self._colors["sat_weather"]
            for obj in self._objects:
                if obj.name == name:
                    color = QColor(obj.color) if obj.color else color
                    break
            seg = []
            for az, el in points:
                if el <= 0:
                    continue
                seg.append(self._sky_to_screen(az, el))
            if len(seg) < 2:
                continue
            n = len(seg)
            for i in range(n - 1):
                a = _traj_alpha(i, n)
                c = QColor(color.red(), color.green(), color.blue(), a)
                painter.setPen(QPen(c, 1, Qt.DashLine))
                painter.drawLine(seg[i], seg[i + 1])
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
        """左下角信息卡片。

        内容高度超过视口可用高度时只滚动、不裁掉数据：光标在卡内用滚轮
        上下滚（卡右侧有滚动条）；卡高最多占视口高度减 24px，不挡天空。
        """
        painter.save()
        _t = tokens()
        card_w = self.INFO_CARD_W
        avail_h = max(120.0, float(self.height()) - 24.0)
        card_h = min(float(self.INFO_CARD_H), avail_h)
        self._status_card_view_h = card_h
        max_scroll = max(0.0, float(self.INFO_CARD_H) - card_h)
        self._status_card_scroll = min(max(self._status_card_scroll, 0.0),
                                       max_scroll)
        card_x, card_y = 12, self.height() - card_h - 12
        self._status_card_rect = QRectF(card_x, card_y, card_w, card_h)
        bg = QColor(_t.COLORS["card_2"])
        bg.setAlpha(220)
        border = QColor(_t.COLORS["gray_400"])
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(card_x, card_y, card_w, card_h, 6, 6)

        ink = self._ink()
        dim = QColor(_t.COLORS["gray_300"])
        label_col = QColor(_t.COLORS["gray_100"])

        # 内容裁剪 + 滚动偏移：行坐标仍按整卡高度算，只平移绘制。
        painter.save()
        painter.setClipRect(QRectF(card_x + 1, card_y + 1,
                                   card_w - 2, card_h - 2))
        painter.translate(0.0, -self._status_card_scroll)
        y = card_y + 20
        painter.setPen(label_col)
        painter.setFont(self._font)
        painter.drawText(card_x + 12, y, "指向(几何)")
        painter.setFont(self._mono_font)
        painter.setPen(QPen(self._colors["antenna"], 2).color())
        painter.drawText(card_x + 80, y,
                         f"AZ {format_azimuth(self._antenna.azimuth_deg)} "
                         f"EL {format_angle_deg(self._antenna.elevation_deg)}")

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
        # 状态卡给「视高度」（屏幕上实际看到的），标签显式写「视」；
        # 几何高度在选中天体的信息卡里同时给出，不混口径。
        painter.drawText(card_x + 12, y, "太阳高度(视)")
        painter.setFont(self._gnss_label_font)
        painter.setPen(ink)
        sun_alt = self._sun["alt"] if self._sun else None
        painter.drawText(card_x + 80, y,
                         format_angle_deg(self._display_alt_deg(sun_alt))
                         if sun_alt is not None else "--")
        if self._stars_hidden_by_daylight():
            # 明示：白天恒星整层隐藏（近似口径见 stars_hidden_by_daylight）。
            painter.setFont(self._gnss_label_font)
            painter.setPen(label_col)
            painter.drawText(card_x + 150, y, "白天·无恒星")

        y += 20
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "UTC")
        painter.setFont(self._mono_font)
        painter.setPen(dim)
        utc_now = self._time_info.get("utc_time") or datetime.now(timezone.utc)
        painter.drawText(card_x + 52, y,
                         f"{format_utc_time(utc_now, '%H:%M:%S')} "
                         f"[{self._time_info.get('ntp_status', 'system')}]")

        # 本地时刻与 UTC 同时给，偏移由系统时区/DST 现算（不写死 +08:00）
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "本地")
        painter.setFont(self._mono_font)
        painter.setPen(dim)
        painter.drawText(card_x + 52, y, format_local_time(utc_now))

        # 恒星时 + 儒略日：同一 TimeEngine 时刻的独立读数（H-6 可复算）。
        # 无站址时 LST 无定义，写「—（无站址）」，不拿 0° 冒充。
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "恒星时")
        painter.setFont(self._mono_font)
        jd_now = self._current_jd()
        if self._observer is not None:
            lst = local_sidereal_time_deg(jd_now, self._observer["lon"])
            painter.setPen(dim)
            painter.drawText(card_x + 52, y,
                             f"{format_hms_ra(lst, digits=1)} · JD {jd_now:.5f}")
        else:
            painter.setPen(dim)
            painter.drawText(card_x + 52, y, "—（无站址）")

        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "呼号")
        painter.setFont(self._mono_font)
        painter.setPen(ink)
        painter.drawText(card_x + 52, y,
                         self._callsign if self._callsign else "未设置")

        # 观测站读数（Stellarium 左下角 location chip 同位）：有坐标给经纬度，
        # 未设置给「未设置」——诚实空状态，绝不编造城市/坐标。
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "观测站")
        painter.setFont(self._mono_font)
        obs_txt = format_observer_chip(self._observer)
        painter.setPen(ink if obs_txt != "未设置" else dim)
        painter.drawText(card_x + 64, y, obs_txt)

        # 视轴：摄像机中心 az/alt（几何口径）。原型 HUD 有这条，本仓此前
        # 只有右上角 FOV；补上后「视场在对准哪」可被独立复算（H-6）。
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "视轴(几何)")
        painter.setFont(self._mono_font)
        painter.setPen(ink)
        painter.drawText(card_x + 76, y,
                         format_view_axis(self.view_state.center_az,
                                          self.view_state.center_alt))

        # 两个环的状态各占一行（与 HUD 同一事实源 _camera_lock/_track_btn）：
        # 一个开着另一个没开时必须写出「未锁定 / 未开启」，不合并成一句
        # 「跟踪中」冒充两个都在跑。
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "视角")
        painter.setFont(self._mono_font)
        cam = self._camera_lock
        painter.setPen(ink if cam else dim)
        painter.drawText(card_x + 52, y,
                         f"锁定 {cam}" if cam else "未锁定")

        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "调谐")
        painter.setFont(self._mono_font)
        trk = self._track_active_name()
        painter.setPen(ink if trk else dim)
        painter.drawText(card_x + 52, y,
                         f"跟踪 {trk}" if trk else "未开启")

        # 受晒/地影：只消费后端快照的 sunlit 字段（本视图不自算物理判据）。
        # None 显示「未知（无受晒数据）」——既涵盖后端缺太阳数据，也涵盖
        # 半影（部分受照）；三种状态都不会画成「受晒」冒充。
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "受晒")
        painter.setFont(self._mono_font)
        sel = self._selected_info or {}
        sunlit_txt = sunlit_state_label(sel.get("sunlit"),
                                        str(sel.get("obj_type", "")),
                                        sel.get("sunlit_status"))
        if sunlit_txt == "受晒":
            painter.setPen(ink)
        elif sunlit_txt.startswith("未知") or sunlit_txt == "—":
            painter.setPen(dim)
        else:                        # 地影 / 半影：警示色
            painter.setPen(self._colors["warning"])
        painter.drawText(card_x + 52, y, sunlit_txt)

        # 图层诚实空态（复用状态卡既有状态位，不另起浮层）：
        # 本视图没有银河亮度数据，如实说「无数据·不提供」，不画装饰性光带。
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "银河")
        painter.setFont(self._gnss_label_font)
        painter.setPen(dim)
        painter.drawText(card_x + 52, y, GALAXY_LAYER_NOTE)

        # 全天星表：本帧实际绘制条数 + 极限星等；被 max_count 截断时
        # 如实写「已按上限显示 N / 可用 M」，不静默少画。星表缺失/白天
        # 消隐各自明说，不把「没数」写成「0 颗」。
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "星表")
        painter.setFont(self._gnss_label_font)
        star_txt = star_catalog_status_text(
            self._star_sel, self._stars_hidden_by_daylight(),
            self._star_catalog_error)
        truncated = bool(self._star_sel is not None
                         and getattr(self._star_sel, "truncated", False))
        if self._star_catalog_error or truncated:
            painter.setPen(self._colors["warning"])
        elif star_txt == "—" or star_txt.startswith("白天"):
            painter.setPen(dim)
        else:
            painter.setPen(ink)
        painter.drawText(card_x + 52, y, star_txt)

        # 星表署名：从 npz meta 读 source/source_license（H-9）。
        # 读不到就写「来源不可用/不完整」，不写死一个看起来可信的字符串。
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "星表来源")
        painter.setFont(self._gnss_label_font)
        credit_txt = star_catalog_credit_text()
        painter.setPen(dim if credit_txt.startswith("来源") else ink)
        painter.drawText(card_x + 52, y, credit_txt)

        # 星座/星官图层：写明当前打开的体系（两套同开时必须能看到徽标），
        # 数据不可用时如实说，不把空图层写成「无星座」。
        y += 18
        painter.setFont(self._font)
        painter.setPen(label_col)
        painter.drawText(card_x + 12, y, "星座")
        painter.setFont(self._gnss_label_font)
        constel_txt = self.constellation_systems_text()
        if self._constellation_error:
            painter.setPen(self._colors["warning"])
        elif constel_txt == "关闭":
            painter.setPen(dim)
        else:
            painter.setPen(ink)
        painter.drawText(card_x + 52, y, constel_txt)

        y += 18
        painter.setFont(self._font)
        painter.setPen(dim)
        painter.drawText(card_x + 12, y,
                         ("全天 160° · A/Esc 退出 | 滚轮缩放"
                          if self._all_sky_active else
                          "滚轮缩放 | 拖拽转动摄像机 | 双击居中 | A 全天"))
        # 结束内容裁剪与滚动偏移；滚动条画在裁剪区外，贴卡片右缘。
        painter.restore()

        if max_scroll > 0.0:
            track_x = card_x + card_w - 7.0
            track_y = card_y + 5.0
            track_h = card_h - 10.0
            thumb_h = max(20.0, track_h * card_h / float(self.INFO_CARD_H))
            travel = track_h - thumb_h
            frac = self._status_card_scroll / max_scroll
            thumb_y = track_y + travel * frac
            track = QColor(_t.COLORS["gray_400"])
            track.setAlpha(70)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(track))
            painter.drawRoundedRect(QRectF(track_x, track_y, 3.0, track_h),
                                    1.5, 1.5)
            thumb = QColor(_t.COLORS["gray_200"])
            thumb.setAlpha(200)
            painter.setBrush(QBrush(thumb))
            painter.drawRoundedRect(QRectF(track_x, thumb_y, 3.0, thumb_h),
                                    1.5, 1.5)
        painter.restore()

    # ------------------------------------------------------------------ 状态卡滚动
    def _status_card_max_scroll(self) -> float:
        """状态卡可滚动距离 = 内容高 - 可视高；不超高时为 0。"""
        return max(0.0, float(self.INFO_CARD_H)
                   - float(self._status_card_view_h))

    def _scroll_status_card(self, dy: float) -> bool:
        """按像素滚动状态卡；返回是否真的发生位移（越界即不动）。"""
        max_scroll = self._status_card_max_scroll()
        if max_scroll <= 0.0:
            return False
        before = self._status_card_scroll
        self._status_card_scroll = min(
            max(before + float(dy), 0.0), max_scroll)
        if abs(self._status_card_scroll - before) < 1e-9:
            return False
        self.update()
        return True

    def _wheel_on_status_card(self, event) -> bool:
        """光标在状态卡内且卡可滚时不缩放天空，只滚卡片。

        返回 True 表示滚轮已被卡片消费（调用方不再透传给 FOV 缩放）。
        """
        if self._status_card_max_scroll() <= 0.0:
            return False
        pos = SkyInteractionHandler._event_cursor_px(event)
        if pos is None or not self._status_card_rect.contains(
                QPointF(pos[0], pos[1])):
            return False
        try:
            delta = SkyInteractionHandler._event_wheel_delta(event)
        except TypeError:
            return False
        # 上滚（delta>0）看更早的行：内容整体下移，滚动偏移减小。
        self._scroll_status_card(-delta / 120.0 * 36.0)
        accept = getattr(event, "accept", None)
        if callable(accept):
            accept()
        return True

    def _draw_passes_panel(self, painter: QPainter):
        """右侧未来过境列表。"""
        if not self._upcoming_passes:
            return
        painter.save()
        _t = tokens()
        rows = self._upcoming_passes[:8]
        row_h = 18
        panel_w = 210
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
        painter.drawText(panel_x + 10, panel_y + 16, "未来过境 24h UTC · 峰值(几何)")

        painter.setFont(self._mono_font)
        ty = panel_y + 16 + row_h
        for p in rows:
            painter.setPen(dim)
            # rise 为 HH:MM:SS：峰值时刻给到秒，避免「分钟取整」在近天顶过境上
            # 与天空实际位置差出数度（NOAA19 87° 峰：整分钟处低 12°）
            painter.drawText(panel_x + 10, ty, str(p.get("rise", "--"))[:8])
            painter.setPen(ink)
            name = str(p.get("name", ""))[:10]
            painter.drawText(panel_x + 66, ty, name)
            painter.setPen(self._colors["accent"])
            painter.drawText(panel_x + panel_w - 78, ty,
                             format_angle_deg(float(p.get('max_alt', 0)),
                                              digits=1).rjust(8))
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

            # PRN 与卫星点同属操作对象（0.5 优先级），并入统一避让。
            self._queue_label(0.5, QPointF(pos.x() + dot_r + 3,
                                          pos.y() - dot_r - 2),
                              str(sat["prn"]), self._ink(), small=True)
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
        self._cancel_goto_anim()  # 用户接管视角：打断进行中的 goto 飞行
        px, py = self._event_xy(event)
        if self._hud_hit(px, py):
            return
        SkyInteractionHandler.mousePressEvent(self, event)

    def _hud_hit(self, px: float, py: float) -> bool:
        """HUD 控件命中测试：信息卡 × / FOV ⊖⊕。命中则消费事件并返回 True。"""
        r = self._info_close_rect
        if r is not None and r.contains(px, py):
            self.on_object_picked(None)
            return True
        # 卡身任意处点击只消费事件：点的是面板不是天空，不应反选天体
        r = self._info_card_rect
        if r is not None and r.contains(px, py):
            return True
        r = self._fov_minus_rect
        if r is not None and r.contains(px, py):
            self.view_state.zoom(1.0)     # 放大：FOV 变小
            self.on_view_changed()
            return True
        r = self._fov_plus_rect
        if r is not None and r.contains(px, py):
            self.view_state.zoom(-1.0)    # 缩小：FOV 变大
            self.on_view_changed()
            return True
        return False

    def mouseMoveEvent(self, event: QMouseEvent):
        SkyInteractionHandler.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        """左键释放: 未拖拽 = 点击拾取。优先级: SkyObject(卫星) ->
        像素容差补拾取 -> 行星/恒星/DSO -> 反选。

        不重委派 SkyInteractionHandler.mouseReleaseEvent: 它找不到 SkyObject
        时会直接 on_object_picked(None) 反选, 行星就没有选中机会了。
        拖拽收尾逻辑与其保持一致。
        """
        was_dragging = self._dragging
        px, py = self._event_xy(event)
        if was_dragging:
            self._dragging = False
            self.on_drag_state_changed(False)
        elif self._press_px is not None:
            size = (float(self.width()), float(self.height()))
            az, alt = screen_to_sky(px, py, self.view_state,
                                    self.projection, size)
            obj = pick_object(az, alt, self.sky_objects)
            if obj is None:
                obj = self._find_object_at(px, py)
            if obj is not None:
                self.on_object_picked(obj)
            else:
                body = self._find_body_at(px, py)
                if body is not None:
                    self.on_body_picked(body)
                else:
                    # 两处都没选中 = 真空白: 统一反选, 清掉卫星卡/环与 body
                    self.on_object_picked(None)
        self._drag_origin_px = None
        self._drag_last_px = None
        self._press_px = None

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """双击 goto：Stellarium 式缓动飞行（替代旧瞬移 look_at）。

        双击 = 用户给摄像机新指向：解除摄像机锁定（Stellarium 手动运动
        解除 flagTracking 同款语义）。
        """
        self._unlock_camera("")
        px, py = self._event_xy(event)
        size = (float(self.width()), float(self.height()))
        az, alt = screen_to_sky(px, py, self.view_state,
                                self.projection, size)
        self.goto_animated(az, alt)

    def wheelEvent(self, event: QWheelEvent):
        # 光标在状态卡内且卡内容超高时，滚轮只滚卡片，不改 FOV。
        if self._wheel_on_status_card(event):
            return
        self._cancel_goto_anim()  # 滚轮缩放打断飞行
        SkyInteractionHandler.wheelEvent(self, event)

    # ------------------------------------------------------------------ 触屏手势
    def _on_touch_pinch_zoom(self, scale_factor: float, center_pos: QPointF) -> None:
        """双指捏合缩放 FOV：锚在两指中点（zoom-to-cursor 同款，与滚轮一致）。

        scale_factor>1 = 双指张开 = 放大（FOV 变小）→ num_steps>0。
        """
        self._cancel_goto_anim()  # 触屏接管视角：打断飞行
        try:
            num_steps = math.log(max(0.2, min(5.0, scale_factor))) * 2.0
            cursor = ((float(center_pos.x()), float(center_pos.y()))
                      if center_pos is not None else None)
            self.zoom_at_cursor(num_steps, cursor)
        except Exception:
            pass

    def _on_touch_pan(self, dx: float, dy: float) -> None:
        """单指拖动平移：复用 SkyInteractionHandler 的 screen_to_sky 反投影平移。"""
        self._cancel_goto_anim()  # 触屏接管视角：打断飞行
        self._unlock_camera("")   # 触屏接管摄像机：解除锁定
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
            self._info_card_rect = None
            self._info_close_rect = None
            # 卫星/行星选中态互斥清理
            self._picked_body = None
            self._body_info = None
            # 取消选中：隐藏调谐按钮 + 停跟踪（发信号让主窗口停环）
            self._tune_sat_name = None
            self._stop_track_ui()
            self._track_btn.hide()
            self._stop_gimbal_ui()
            self._gimbal_btn.hide()
            self._camera_btn.hide()
            # 反选 = 选中对象消失：摄像机解锁（不停在无目标角度假跟踪）
            self._unlock_camera("摄像机已解锁：选中目标已取消（反选）")
            self._tune_btn.hide()
            self._doppler_label.hide()
            self._tune_refresh_timer.stop()
            self.update()
            return
        self._hovered_object = obj
        # 卫星/行星选中态互斥: 选中卫星清掉行星卡
        self._picked_body = None
        self._body_info = None
        self._build_selected_info(obj)
        self.object_clicked.emit(obj)
        # 点星即"唰"：缓动飞到选中星（已在视野内时是短行程，仍有反馈）
        self.goto_animated(obj.azimuth_deg, obj.elevation_deg)
        self.update()

    def on_drag_state_changed(self, dragging: bool) -> None:
        # 用户拖拽接管摄像机（Stellarium dragView→setFlagTracking(false) 同款）
        if dragging and self._camera_lock is not None:
            self._unlock_camera("")
        self._dragging = dragging

    def _find_object_at(self, x: float, y: float,
                         tolerance: float = 12.0) -> Optional[SkyObject]:
        """像素拾取（cleverFind 式评分：像素角距 + 亮度优先级双键）。

        候选门 = tolerance px；评分 = dist_px * (1 - 0.5*brightness_norm)，
        亮度归一 = clamp((signal_db + 60)/60, 0, 1)。等距时亮星优先；
        距离差拉开时近者优先（StelObjectMgr::cleverFind 双键思想，
        替代旧的固定容差首中即返）。
        """
        best = None
        best_score = float(tolerance)
        for obj in self._objects:
            if not obj.visible or not obj.is_above_horizon():
                continue
            pos = self._sky_to_screen_finite(obj.azimuth_deg,
                                             obj.elevation_deg)
            if pos is None:
                continue
            dist = math.hypot(pos.x() - x, pos.y() - y)
            if dist >= tolerance:
                continue
            norm = max(0.0, min(1.0,
                                (obj.signal_strength_db + 60.0) / 60.0))
            score = dist * (1.0 - 0.5 * norm)
            if score < best_score:
                best_score = score
                best = obj
        return best

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
    """天空模式视图面板：包含标题栏、控制按钮和 RFSkyView。"""

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
        self.time_label.setToolTip(
            "倍率=模拟时间推进速度（1 真实秒 = N 模拟秒），不是画面帧率；"
            "×1 为实时（模拟时间=真实时间）")
        bar.addWidget(self.time_label)
        bar.addStretch()
        for label, cb, tip in (
                ("<< -10m", lambda: self._adjust(-600),
                 "模拟时间一次性回退 10 分钟；锁定目标若已落地会诚实解锁"),
                ("跳到此刻", self._reset_time,
                 "跳到当前真实时间并恢复 1x；跨越大段时间时锁定目标若已落地"
                 "会诚实解锁，仍在天空则按新时刻直接贴住"),
                ("+10m >>", lambda: self._adjust(600),
                 "模拟时间一次性前进 10 分钟；锁定目标若已落地会诚实解锁")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            bar.addWidget(b)
        # 时间流速档（StelCore::setTimeRate UX 化）：快转看过境是
        # 星象仪叙事主轴"历"。倍率由 new_spacetime 时间引擎执行，
        # 视图 _current_jd 每帧 tick，天空/卫星轨道随之流动。
        self._rate_buttons: Dict[str, QPushButton] = {}
        _rate_tips = {
            "⏸": "暂停模拟时间（模拟时钟冻结；画面帧率不变）",
            "▶": "实时：1 模拟秒 = 1 真实秒（与画面帧率无关）",
            "×60": "模拟时间 ×60（1 真实秒 = 60 模拟秒）；与画面帧率无关",
            "×300": "模拟时间 ×300（1 真实秒 = 300 模拟秒）；与画面帧率无关",
            "×1800": "模拟时间 ×1800（1 真实秒 = 1800 模拟秒）；与画面帧率无关",
        }
        for label, rate in (("⏸", 0.0), ("▶", 1.0),
                            ("×60", 60.0), ("×300", 300.0),
                            ("×1800", 1800.0)):
            b = QPushButton(label)
            b.setToolTip(_rate_tips[label])
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, r=rate: self._set_rate(r))
            bar.addWidget(b)
            self._rate_buttons[label] = b
        self._rate_buttons["▶"].setChecked(True)
        bar.addSpacing(16)
        b = QPushButton("重置摄像机")
        b.setToolTip("重置摄像机：回到天顶居中 + FOV=120°，并解除摄像机锁定")
        b.clicked.connect(self.reset_view)
        bar.addWidget(b)
        bar.addSpacing(16)
        # 图层开关（Stellarium 右缘 layer toggles 同职能；顺着本视图既有底部
        # 控制条 seam 落，不另起右缘轨）。热力图由数据驱动，不在此手控。
        self._layer_buttons: Dict[str, QPushButton] = {}
        for label, attr, setter in (
                ("方位网格", "_show_grid", self.sky_view.set_show_grid),
                ("赤道网格", "_show_eqgrid", self.sky_view.set_show_eqgrid),
                ("罗盘", "_show_compass", self.sky_view.set_show_compass),
                ("轨迹", "_show_trajectories",
                 self.sky_view.set_show_trajectories)):
            lb = QPushButton(label)
            lb.setCheckable(True)
            lb.setChecked(bool(getattr(self.sky_view, attr)))
            lb.setToolTip(f"显示/隐藏{label}图层")
            lb.clicked.connect(
                lambda _c=False, s=setter, btn=lb: s(btn.isChecked()))
            bar.addWidget(lb)
            self._layer_buttons[label] = lb
        self.layout().addWidget(control_bar)
        self._offset = 0.0
        # 快转时 UTC 标签随模拟时钟滚动（500ms 刷新足够读出秒级流动）
        self._time_ui_timer = QTimer(self)
        self._time_ui_timer.timeout.connect(self._update_label)
        self._time_ui_timer.start(500)

    def _adjust(self, seconds: float):
        self._offset += seconds
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            eng = get_time_engine()
            jump = format_duration_s(seconds)
            eng.set_unix(eng.now_unix() + seconds)
            # 告知锁定逻辑：这是一次性跳时，不是正常时间推进
            self.sky_view.notify_time_jump(jump)
        except Exception:
            pass
        self._update_label()

    def _reset_time(self):
        self._offset = 0.0
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            eng = get_time_engine()
            old = eng.now_unix()
            eng.set_time_now()
            delta = eng.now_unix() - old
            self._set_rate(1.0)          # "跳到此刻" = 回墙钟 + 恢复 1x
            label = ("跳到此刻" if abs(delta) < 1.0
                     else f"跳到此刻 {format_duration_s(delta)}")
            self.sky_view.notify_time_jump(label)
        except Exception:
            pass
        self._update_label()

    def _set_rate(self, rate: float):
        """设置时间流速倍率（0=暂停 1=实时 N=Nx）。按钮组单选互斥。"""
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            eng = get_time_engine()
            if rate == 0.0:
                # 跟随墙钟态下 _sim_jd=None，单设 rate=0 不冻结时间；先钉住
                # 当前时刻再暂停，否则「⏸ 点了没反应」。
                if eng._sim_jd is None:
                    eng.set_unix(eng.now_unix())
                eng.pause()
            elif rate == 1.0:
                eng.resume(1.0)
            else:
                # 快进档位：若引擎还钉在墙钟（未跳时），先钉当前时刻再加速，
                # 否则 rate 对 _sim_jd=None 不生效（tick 早退）。
                if eng._sim_jd is None:
                    eng.set_unix(eng.now_unix())
                eng.set_rate(float(rate))
        except Exception:
            pass
        for label, btn in self._rate_buttons.items():
            want = {"⏸": 0.0, "▶": 1.0}.get(label)
            if want is not None:
                btn.setChecked(want == rate)
            else:
                btn.setChecked(float(label[1:]) == rate)
        self._update_label()

    def _update_label(self):
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            eng = get_time_engine()
            t = eng.now_utc()
            rate = eng.get_rate()
            # 显示口径：倍率是「模拟时间推进速度」，不是画面帧率；
            # ×1 必须明确写「实时」，暂停/倒放也要能一眼看出。
            if rate == 1.0:
                suffix = " · 实时"
            elif rate == 0.0:
                suffix = " ⏸ 暂停"
            elif rate > 0.0:
                suffix = f" · ×{rate:g} 模拟时间"
            else:
                suffix = f" · 倒放 ×{abs(rate):g} 模拟时间"
            self.time_label.setText(format_utc_datetime(t) + suffix)
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

    # 过境面板：24h 窗口、5 分钟粗扫 + 峰值邻域 30 秒细化（与天空同一坐标链）
    PASS_WIN_HOURS = 24.0
    PASS_COARSE_STEP_S = 300.0
    PASS_FINE_STEP_S = 30.0
    PASS_MIN_ALT_DEG = 10.0
    # 快转下按模拟时间差重算（墙钟下限防止高倍率每帧扫 24h）
    PASS_REFRESH_SIM_S = 300.0
    PASS_REFRESH_WALL_S = 1.0

    def __init__(self, sky_view, lat: Optional[float], lon: Optional[float],
                 alt_km: float = 0.0, interval_ms: int = 10000):
        self.sky_view = sky_view
        self.lat = lat
        self.lon = lon
        self.alt_km = alt_km
        self._interval_ms = interval_ms
        self._catalog: List[Dict[str, object]] = []  # [{name, line1, line2, freq_mhz, kind}]
        self._satrec_cache: Dict[str, object] = {}   # name -> sgp4 Satrec
        # 过境面板的预测基准时刻与墙钟（快转节流用；None=尚未预测）
        self._passes_anchor_unix: Optional[float] = None
        self._passes_wall: float = 0.0

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
            from mbdsdr_ai import solar_system as _sol
            sp = _sol.get_sun_position(
                t_unix, (self.lat, self.lon, self.alt_km * 1000.0))
            if sp is not None:
                # 视直径/距离/RA-Dec 走同一套历表（solar_system）；绘制层据此
                # 按真实视直径缩放，信息卡据此给升落时间。历表缺测则保留上面
                # 仅 az/alt 的太阳（此时按点源下限 2px 画，不编造角直径）。
                sun = {"az": sp.az_deg, "alt": sp.alt_deg,
                       "ra_deg": sp.ra_deg, "dec_deg": sp.dec_deg,
                       "distance_au": sp.distance_au,
                       "diameter_deg": sp.angular_diameter_deg}
        except Exception:
            pass
        try:
            from mbdsdr_ai import solar_system
            mp = solar_system.get_moon_position(
                t_unix, (self.lat, self.lon, self.alt_km * 1000.0))
            if mp is not None:
                # 照度与视直径原样透传：illum 缺测保持 None（不拿 0=新月冒充），
                # 视直径（度）供绘制层按真实大小缩放。
                moon = {"az": mp.az_deg, "alt": mp.alt_deg,
                        "illum": mp.illumination,
                        "diameter_deg": mp.angular_diameter_deg}
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
            self.sky_view.set_upcoming_passes([])
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

        # TLE 目录为空 -> 不计算卫星（过期轨迹/过境也要撤掉，不冒充现行数据）
        if not self._catalog:
            self.sky_view.set_satellites_connected(False)
            self.sky_view.set_objects([])
            self.sky_view.set_satellite_catalog([])
            self.sky_view.clear_trajectories()
            self.sky_view.set_upcoming_passes([])
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
                description=format_satellite_description(
                    alt_d, dist_km, range_rate),
                # 受晒三态与边界不确定度只透传后端快照字段（orbit._state_from_satrec
                # 已由 sat_illumination 圆锥模型算出），本视图不二次计算几何。
                sunlit=state.get("sunlit"),
                sunlit_status=state.get("sunlit_status"),
                boundary_uncertainty_s=state.get("boundary_uncertainty_s"),
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
                # 带锚点：快转下视图侧据此重算完整曲线，不画过期轨迹
                self.sky_view.set_trajectory(name, pts,
                                             anchor_unix=t_unix,
                                             span_s=600.0)

        self.sky_view.set_objects(objs)
        self.sky_view.set_satellite_catalog(self._catalog)
        self.sky_view.set_satellites_connected(connected > 0)

        # 未来过境列表（best-effort；快转下按模拟时间差重算，见 _maybe_refresh_passes）
        try:
            self._maybe_refresh_passes(jd_utc, t_unix)
        except Exception:
            pass

    def _engine_rate(self) -> float:
        """当前时间倍率（取不到引擎按 1.0）。"""
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            return float(get_time_engine().get_rate())
        except Exception:
            return 1.0

    def _maybe_refresh_passes(self, jd_utc: float, t_unix: float):
        """过境面板与天空同一模拟时刻：快转下按模拟时间差重算。

        1x 保持原有「每次 refresh（5s 墙钟）」语义；快转时若沿用 5s 墙钟，
        ×300 就是 25 分钟模拟时间一跳，会列出已经过去的峰值（L23「两个时间
        基准分叉」的复发形态）。快转下模拟时间差 ≥ PASS_REFRESH_SIM_S 才重算，
        同时设墙钟下限，避免 ×1800 每帧扫 24h 拖慢渲染。
        """
        if abs(self._engine_rate()) <= 1.0:
            self._refresh_passes(jd_utc)
            return
        now_wall = time.monotonic()
        if (self._passes_anchor_unix is not None
                and abs(t_unix - self._passes_anchor_unix)
                < self.PASS_REFRESH_SIM_S
                and now_wall - self._passes_wall < self.PASS_REFRESH_WALL_S):
            return
        self._refresh_passes(jd_utc)

    def _refresh_passes(self, jd_utc: float):
        """预测未来 24h 过境，喂入天空图侧栏。失败则清空列表。

        行字段：peak_unix（峰值精确模拟时刻，供面板/天空同刻对拍与过期过滤）、
        rise（HH:MM 显示口径的峰值时刻）、max_alt（峰值仰角）。
        粗采样 5 分钟 + 峰值邻域 30 秒细化：纯 5 分钟粗采样会系统性低估峰值
        （快过境可差数度），细化后 max_alt 才等于「该时刻天空里画出的仰角」。
        """
        t0_unix = (jd_utc - 2440587.5) * 86400.0
        try:
            from mbdsdr_ai import orbit
            from sgp4.api import Satrec
            win_s = self.PASS_WIN_HOURS * 3600.0
            rows = []
            for entry in self._catalog[:6]:
                name = entry["name"]
                line1 = entry["line1"]
                line2 = entry["line2"]
                try:
                    sat = Satrec.twoline2rv(line1, line2)
                except Exception:
                    continue

                def _el_at(sec: float):
                    try:
                        st = orbit._state_from_satrec(
                            sat, name, (t0_unix + sec) / 86400.0 + 2440587.5,
                            self.lat, self.lon, self.alt_km)
                    except Exception:
                        return None
                    return None if st is None else float(st["elevation"])

                best_el, best_t = -90.0, 0.0
                for sec in range(0, int(win_s) + 1,
                                 int(self.PASS_COARSE_STEP_S)):
                    e = _el_at(float(sec))
                    if e is not None and e > best_el:
                        best_el, best_t = e, float(sec)
                if best_el <= self.PASS_MIN_ALT_DEG:
                    continue
                # 峰值邻域细化（补回粗采样削掉的峰值）
                lo = max(0.0, best_t - self.PASS_COARSE_STEP_S)
                hi = min(win_s, best_t + self.PASS_COARSE_STEP_S)
                sec = lo
                while sec <= hi + 1e-6:
                    e = _el_at(sec)
                    if e is not None and e > best_el:
                        best_el, best_t = e, sec
                    sec += self.PASS_FINE_STEP_S
                peak_unix = t0_unix + best_t
                rows.append({
                    "name": name,
                    "rise": format_utc_time(peak_unix, "%H:%M:%S"),
                    "peak_unix": peak_unix,
                    "max_alt": best_el,
                    "duration_min": 0.0,
                })
            rows.sort(key=lambda r: r["peak_unix"])
            self._passes_anchor_unix = t0_unix
            self._passes_wall = time.monotonic()
            self.sky_view.set_upcoming_passes(rows[:8])
        except Exception:
            self.sky_view.set_upcoming_passes([])

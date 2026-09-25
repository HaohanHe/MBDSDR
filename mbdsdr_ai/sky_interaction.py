"""
sky_interaction.py — 桌面天空图交互控制模块
================================================

交互模型参考 Stellarium (GPL-3.0), 独立重实现
================================================
本模块把 Stellarium 的视角控制 / 点选 / 反投影交互模型搬到我们的
PySide6 极坐标天空图上, 全部数学独立重写, 不依赖 Stellarium 运行时。

参考的 Stellarium 源文件 (行号为本模块开发时检出的版本):
  - 视角平移:   src/core/StelMovementMgr.cpp:1576  panView(deltaAz, deltaAlt)
  - 拖拽反投影: src/core/StelMovementMgr.cpp:1659  dragView(x1,y1,x2,y2)
  - 滚轮缩放:   src/core/StelMovementMgr.cpp:537   handleMouseWheel()
  - goto 动画:   src/core/StelMovementMgr.cpp:1472  moveToAltAzi()
  - FOV 夹取:   src/core/StelMovementMgr.hpp:466    setFov(f) [minFov..maxFov]
  - 反投影:     src/core/StelProjector.cpp:604     unProject(x,y,v)
  - 点选搜索半径: src/core/StelObjectMgr.cpp:36     searchRadiusPixel=25
  - 点选算法:   src/core/StelObjectMgr.cpp:461     cleverFind(core,v)
  - 点选入口:   src/core/StelObjectMgr.cpp:516     cleverFind(core,x,y)
  - 脚本 API:   src/scripting/StelMainScriptAPI.hpp:442  moveToObject / :455 moveToAltAzi

坐标约定 (与 desktop/rf_sky_view.py 完全一致, 与 celestial_geometry.py 一致):
  - az: 方位角, 度, 北=0, 东=90, 南=180, 西=270, 顺时针为正
  - alt: 仰角, 度, 地平线=0, 天顶=90
  - 屏幕: px 向右, py 向下 (Qt 约定); 北指向屏幕上方

正/反投影使用 celestial_geometry.AzimuthalEquidistantProjection (Stellarium
的 "Fish-eye" / 等距方位投影, src/core/StelProjectorClasses.cpp:363)。
视图中心 (center_az, center_alt) 通过一个旋转矩阵 R_view 映射到屏幕中心,
等价于 Stellarium StelProjector 里的 modelViewTransform (StelProjector.cpp:615)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Any

import numpy as np

# 复用 celestial_geometry 的投影与坐标变换 (本项目已独立重实现)
from .celestial_geometry import (
    AzimuthalEquidistantProjection,
    vec_from_azalt,
    azalt_from_vec,
    rot_y,
    rot_z,
    deg2rad,
    rad2deg,
)

__all__ = [
    "ViewState",
    "SkyInteractionHandler",
    "screen_to_sky",
    "sky_to_screen",
    "pick_object",
    "angular_distance_deg",
    "build_view_rotation",
    "DEFAULT_MIN_FOV_DEG",
    "DEFAULT_MAX_FOV_DEG",
    "DEFAULT_PICK_RADIUS_DEG",
]

# ---------------------------------------------------------------------------
# 交互参数 (对照 Stellarium 默认值)
#   Stellarium minFov=0.001389° (5"), maxFov 默认 360° (StelMovementMgr.cpp:84,172)
#   我们的天空图是 SDR 全天空图, 不需要望远镜级缩放: 限制在 30°..180°。
# ---------------------------------------------------------------------------
DEFAULT_MIN_FOV_DEG: float = 30.0
DEFAULT_MAX_FOV_DEG: float = 180.0
# Stellarium searchRadiusPixel=25 (StelObjectMgr.cpp:36), 按 FOV/屏宽换算成角距。
# 我们直接用固定角距拾取半径 (度), 避免依赖像素缩放。
DEFAULT_PICK_RADIUS_DEG: float = 2.0


# ---------------------------------------------------------------------------
# 视图状态: 对应 Stellarium StelMovementMgr 的 (viewDirection, currentFov)
#   - viewDirection = 中心指向的 (az, alt)        -> center_az, center_alt
#   - currentFov    = 视场角 (度)                 -> fov_deg
# 参考 StelMovementMgr.hpp:504 currentFov, :506 minFov, :507 maxFov
# ---------------------------------------------------------------------------
@dataclass
class ViewState:
    """天空图视角状态。

    Attributes:
        center_az:   视图中心方位角 (度), 北=0 东=90。
        center_alt:  视图中心仰角 (度), 天顶=90。
        fov_deg:     视场角直径 (度), 30..180。越小越放大。
        rotation:    屏幕整体旋转 (度, 顺时针), 对应 rf_sky_view._rotation。
    """

    center_az: float = 0.0
    center_alt: float = 90.0          # 默认看天顶
    fov_deg: float = 120.0            # 默认: 能看到天顶到地平线
    rotation: float = 0.0

    # -- 夹取范围 (对照 StelMovementMgr.hpp:466 setFov 的 qBound) -----------
    min_fov: float = DEFAULT_MIN_FOV_DEG
    max_fov: float = DEFAULT_MAX_FOV_DEG

    def __post_init__(self) -> None:
        self._clamp()

    def _clamp(self) -> None:
        self.fov_deg = max(self.min_fov, min(self.max_fov, self.fov_deg))
        # alt 夹取到 ±(90°-ε), 与 StelMovementMgr.cpp:1634-1635 一致
        # 避免天顶/天底处方位角奇异。
        eps = 1e-4
        self.center_alt = max(-90.0 + eps, min(90.0 - eps, self.center_alt))
        self.center_az = self.center_az % 360.0
        if self.center_az < 0:
            self.center_az += 360.0

    # -- 平移: 对应 StelMovementMgr::panView (StelMovementMgr.cpp:1576) -----
    def pan(self, delta_az_deg: float, delta_alt_deg: float) -> None:
        """平移天球: delta_az>0 向东(右)拖动视角, delta_alt>0 向天顶拖动。

        参考 panView (StelMovementMgr.cpp:1626-1636):
          azVision -= deltaAz;  altVision += deltaAlt;
          alt 夹取到 ±(90°-1e-6) 防止极点奇异。
        注意方向: dragView 里调用的是 panView(az2-az1, alt1-alt2),
        即屏幕 y 向上对应 alt 增加 (我们下面 screen_to_sky 已把 y 翻转)。
        """
        self.center_az -= delta_az_deg
        self.center_alt += delta_alt_deg
        self._clamp()

    # -- 缩放: 对应 handleMouseWheel (StelMovementMgr.cpp:580) --------------
    def zoom(self, num_steps: float, zoom_speed: float = 30.0) -> None:
        """滚轮缩放。

        Stellarium: zoomFactor = exp(-mouseZoomSpeed * numSteps/60)
                    zoomTo(getAimFov()*zoomFactor, 0.2s)   (StelMovementMgr.cpp:580-582)
        num_steps>0 (滚轮上滚) -> FOV 变小 (放大); <0 -> FOV 变大 (缩小)。
        指数缩放保证不同 FOV 下滚轮手感一致 (对数缩放)。
        """
        zoom_factor = math.exp(-zoom_speed * num_steps / 60.0)
        self.fov_deg *= zoom_factor
        self._clamp()

    def zoom_to(self, fov_deg: float) -> None:
        """直接设定 FOV (对应 zoomTo, StelMovementMgr.cpp:1756)。"""
        self.fov_deg = fov_deg
        self._clamp()

    def look_at(self, az_deg: float, alt_deg: float) -> None:
        """goto: 居中到指定 (az, alt)。对应 moveToAltAzi (StelMovementMgr.cpp:1472)。"""
        self.center_az = az_deg
        self.center_alt = alt_deg
        self._clamp()

    def copy(self) -> "ViewState":
        return ViewState(self.center_az, self.center_alt, self.fov_deg,
                         self.rotation, self.min_fov, self.max_fov)


# ---------------------------------------------------------------------------
# 视图旋转矩阵: 把 (center_az, center_alt) 方向旋转到屏幕中心 (天顶方向)。
#
# 这等价于 Stellarium StelProjector 的 modelViewTransform (StelProjector.cpp:615
# 在 unProject 末尾调用 modelViewTransform->backward(v))。
#
# 地平系约定 (celestial_geometry.vec_from_azalt):
#   v = (cos(alt)cos(az), cos(alt)sin(az), -sin(alt)), 屏幕中心 = (0,0,-1)
#
# 我们构造 R_view = rot_y(calt-90°) @ rot_z(caz), 使得:
#   R_view @ vec_from_azalt(caz, calt) = (0,0,-1)
# 推导:
#   rot_z(caz) 把方位角转到相对方位 0:  vc -> (cos(calt), 0, -sin(calt))
#   rot_y(calt-90°) 把仰角转到 -90°(屏幕中心): -> (0,0,-1)
# ---------------------------------------------------------------------------
def build_view_rotation(view: ViewState) -> np.ndarray:
    """返回 3x3 旋转矩阵 R_view, v_view = R_view @ v_altaz。"""
    caz = deg2rad(view.center_az)
    calt = deg2rad(view.center_alt)
    # R_view = Ry(calt-90°) @ Rz(caz)
    return rot_y(calt - math.pi / 2.0) @ rot_z(caz)


# ---------------------------------------------------------------------------
# 像素缩放: 对应 Stellarium StelProjector::pixelPerRad
#   StelProjector.cpp:172: pixelPerRad = 0.5*viewportFovDiameter / fovToViewScalingFactor(fov/2)
# 对等距方位投影, fovToViewScalingFactor(fov/2) = fov/2 (弧度), 故:
#   pixelPerRad = (min_dim/2) / deg2rad(fov/2) = min_dim / deg2rad(fov)
# ---------------------------------------------------------------------------
def _pixel_per_rad(view: ViewState, widget_size: Tuple[int, int]) -> float:
    w, h = widget_size
    min_dim = float(min(w, h))
    if min_dim <= 0:
        return 1.0
    return min_dim / deg2rad(view.fov_deg)


def _widget_center(widget_size: Tuple[int, int]) -> Tuple[float, float]:
    w, h = widget_size
    return w / 2.0, h / 2.0


# ---------------------------------------------------------------------------
# 正投影: (az, alt) -> 屏幕像素
#   步骤 (对照 Stellarium StelProjector::project, StelProjector.cpp:390-391):
#     1. v = vec_from_azalt(az, alt)                      (地平系单位向量)
#     2. v_view = R_view @ v                              (modelViewTransform->forward)
#     3. (x_n, y_n) = proj._forward(v_view)               (投影核心)
#     4. px = cx + y_n*ppr;  py = cy - x_n*ppr            (北朝上, y 向下)
# ---------------------------------------------------------------------------
def sky_to_screen(az_deg: float, alt_deg: float,
                  view: ViewState,
                  projection: Optional[Any] = None,
                  widget_size: Tuple[int, int] = (400, 400)
                  ) -> Tuple[float, float]:
    """天球坐标 (az, alt) -> 屏幕像素 (px, py)。

    Args:
        az_deg, alt_deg: 地平坐标 (度)。
        view: 视图状态 (中心指向 + FOV)。
        projection: celestial_geometry 投影实例; 默认等距方位投影。
        widget_size: (width, height) 像素。

    Returns:
        (px, py) 屏幕像素坐标。若该点在投影背面, 返回离屏坐标 (仍可绘制裁剪)。
    """
    if projection is None:
        projection = AzimuthalEquidistantProjection()

    v = vec_from_azalt(az_deg, alt_deg)
    R = build_view_rotation(view)
    v_view = R @ v

    x_n, y_n = projection.project_vec(v_view)

    ppr = _pixel_per_rad(view, widget_size)
    cx, cy = _widget_center(widget_size)

    # 归一化坐标 -> 屏幕像素。
    # celestial_geometry 中: 北(az=0) -> +x_n, 东(az=90) -> +y_n。
    # 我们要: 北 -> 屏幕上方 (py 减小), 东 -> 屏幕右方 (px 增大)。
    #   px = cx + y_n*ppr ; py = cy - x_n*ppr
    px = cx + y_n * ppr
    py = cy - x_n * ppr

    # 整体旋转 (对应 rf_sky_view._rotation): 绕中心旋转屏幕坐标
    if abs(view.rotation) > 1e-9:
        ang = deg2rad(view.rotation)
        dx, dy = px - cx, py - cy
        px = cx + dx * math.cos(ang) - dy * math.sin(ang)
        py = cy + dx * math.sin(ang) + dy * math.cos(ang)

    return px, py


# ---------------------------------------------------------------------------
# 反投影: 屏幕像素 -> (az, alt)
#   对照 Stellarium StelProjector::unProject (StelProjector.cpp:604-616):
#     v[0] = (x - viewportCenter[0]) / pixelPerRad
#     v[1] = (y - viewportCenter[1]) / pixelPerRad
#     v[2] = 0;  backward(v);  modelViewTransform->backward(v)
# ---------------------------------------------------------------------------
def screen_to_sky(screen_x: float, screen_y: float,
                  view: ViewState,
                  projection: Optional[Any] = None,
                  widget_size: Tuple[int, int] = (400, 400)
                  ) -> Tuple[float, float]:
    """屏幕像素 (px, py) -> 天球坐标 (az, alt)。

    这是点选/拖拽的核心: 屏幕坐标 -> 归一化坐标 -> 反投影到 3D 单位向量
    -> 旋转回地平系 -> (az, alt)。
    对照 StelObjectMgr::cleverFind(core,x,y) (StelObjectMgr.cpp:516-532)。
    """
    if projection is None:
        projection = AzimuthalEquidistantProjection()

    ppr = _pixel_per_rad(view, widget_size)
    cx, cy = _widget_center(widget_size)

    # 撤销屏幕整体旋转
    px, py = screen_x, screen_y
    if abs(view.rotation) > 1e-9:
        ang = -deg2rad(view.rotation)
        dx, dy = px - cx, py - cy
        px = cx + dx * math.cos(ang) - dy * math.sin(ang)
        py = cy + dx * math.sin(ang) + dy * math.cos(ang)

    # 屏幕像素 -> 归一化投影坐标 (逆 sky_to_screen 的映射)
    #   px = cx + y_n*ppr  =>  y_n = (px-cx)/ppr
    #   py = cy - x_n*ppr  =>  x_n = -(py-cy)/ppr
    x_n = -(py - cy) / ppr
    y_n = (px - cx) / ppr

    # 投影反函数: 归一化坐标 -> 视图空间单位向量
    v_view = projection.unproject_vec(x_n, y_n)

    # 逆视图旋转: v_altaz = R_view^T @ v_view
    R = build_view_rotation(view)
    v = R.T @ v_view

    az, alt = azalt_from_vec(v)
    return az, alt


# ---------------------------------------------------------------------------
# 角距离与拾取
#   对照 StelObjectMgr::cleverFind (StelObjectMgr.cpp:461-508):
#     1. 由当前 FOV/屏宽把像素搜索半径换算成角距 fov_around
#     2. 遍历各模块 searchAround(v, fov_around)
#     3. 在候选里选 pixel_distance + priority 最小者
# 我们的简化版: 直接遍历天空对象, 找点击位置角距离最近的, 若在
# pick_radius_deg 内则选中。
# ---------------------------------------------------------------------------
def angular_distance_deg(az1: float, alt1: float,
                         az2: float, alt2: float) -> float:
    """两个 (az, alt) 方向之间的大圆角距离 (度)。

    球面余弦定理:
      cos(d) = sin(alt1)sin(alt2) + cos(alt1)cos(alt2)cos(az1-az2)
    """
    a1, a2 = deg2rad(alt1), deg2rad(alt2)
    da = deg2rad(az1 - az2)
    cos_d = math.sin(a1) * math.sin(a2) + math.cos(a1) * math.cos(a2) * math.cos(da)
    cos_d = max(-1.0, min(1.0, cos_d))
    return rad2deg(math.acos(cos_d))


def pick_object(az_deg: float, alt_deg: float,
                sky_objects,
                pick_radius_deg: float = DEFAULT_PICK_RADIUS_DEG):
    """在天空对象列表中找离 (az, alt) 最近且在拾取半径内的对象。

    Args:
        az_deg, alt_deg: 点击位置的地平坐标 (由 screen_to_sky 得到)。
        sky_objects: 可迭代对象, 每个元素需有 azimuth_deg / elevation_deg
                     属性 (与 rf_sky_view.SkyObject 一致)。
        pick_radius_deg: 拾取角半径 (度)。Stellarium 用 25px 像素半径
                         (StelObjectMgr.cpp:36), 这里用角距更直观。

    Returns:
        最近的 SkyObject, 或 None (无对象在半径内)。
    """
    best = None
    best_dist = pick_radius_deg
    for obj in sky_objects:
        if not getattr(obj, "visible", True):
            continue
        d = angular_distance_deg(az_deg, alt_deg,
                                 obj.azimuth_deg, obj.elevation_deg)
        if d < best_dist:
            best_dist = d
            best = obj
    return best


# ---------------------------------------------------------------------------
# Qt 交互混入类
#   对照 Stellarium 的事件分发:
#     - StelMovementMgr::handleMouseClicks (StelMovementMgr.cpp:614)
#         左键按下开始拖拽追踪; 释放时若位移 < dragTriggerDistance(4px,
#         StelMovementMgr.cpp:130) 则视为点击 -> findAndSelect (:714)
#     - StelMovementMgr::handleMouseWheel (StelMovementMgr.cpp:537)
#     - StelMovementMgr::dragView (StelMovementMgr.cpp:1659)
#
# 用法 (在你的 QWidget 里多继承):
#     class RFSkyView(QWidget, SkyInteractionHandler):
#         def __init__(self):
#             QWidget.__init__(self)
#             self.view_state = ViewState()
#             self.projection = AzimuthalEquidistantProjection()
#             self.sky_objects = []
#         # 事件直接转发:
#         def mousePressEvent(self, e): SkyInteractionHandler.mousePressEvent(self, e)
#         ...
#
# 为了让本模块在无 PySide6 的环境 (headless 测试) 也能 import, Qt 事件类
# 在方法内部才使用, 模块顶层不 import PySide6。
# ---------------------------------------------------------------------------
class SkyInteractionHandler:
    """可混入 QWidget 的天空图交互处理器。

    混入后, widget 需提供以下属性 (本类通过 self 访问):
        view_state : ViewState          — 视角状态
        projection : _BaseProjection    — 投影实例 (默认 AzimuthalEquidistant)
        sky_objects: list               — 可拾取的 SkyObject 列表

    可重写的钩子 (由本类在合适时机调用, 子类实现视觉反馈):
        on_object_picked(obj)            — 点选到天体
        on_view_changed()               — 视角变化 (平移/缩放/居中), 触发重绘
        on_drag_state_changed(dragging) — 拖拽状态切换
    """

    # 拖拽判定阈值 (像素), 对照 StelMovementMgr.cpp:130 dragTriggerDistance=4
    DRAG_THRESHOLD_PX: float = 4.0

    def __init__(self) -> None:
        # 这些属性由宿主 widget 设置; 这里给默认值避免 AttributeError
        if not hasattr(self, "view_state"):
            self.view_state = ViewState()
        if not hasattr(self, "projection"):
            self.projection = AzimuthalEquidistantProjection()
        if not hasattr(self, "sky_objects"):
            self.sky_objects = []
        # 交互运行时状态
        self._drag_origin_px: Optional[Tuple[float, float]] = None
        self._drag_last_px: Optional[Tuple[float, float]] = None
        self._drag_start_center: Optional[Tuple[float, float]] = None
        self._dragging: bool = False
        self._press_px: Optional[Tuple[float, float]] = None

    # -- 钩子默认实现 (子类可重写) ------------------------------------------
    def on_object_picked(self, obj) -> None: ...
    def on_view_changed(self) -> None: ...
    def on_drag_state_changed(self, dragging: bool) -> None: ...

    def _widget_size(self) -> Tuple[int, int]:
        """获取宿主 widget 尺寸。兼容 QWidget 与测试桩。"""
        w = getattr(self, "width", None)
        h = getattr(self, "height", None)
        if callable(w) and callable(h):
            return int(w()), int(h())
        return 400, 400

    # -- 事件处理 ----------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        """左键按下: 记录拖拽起点。

        对照 StelMovementMgr.cpp:614 handleMouseClicks 的按下分支:
        记录 previousX/previousY, 进入可能的拖拽状态。
        """
        # 兼容 QMouseEvent 与纯 (x,y) 元组测试桩
        px, py = self._event_xy(event)
        self._press_px = (px, py)
        self._drag_last_px = (px, py)
        self._drag_start_center = (self.view_state.center_az,
                                   self.view_state.center_alt)
        self._dragging = False

    def mouseMoveEvent(self, event) -> None:
        """左键拖拽: 平移视角。

        对照 StelMovementMgr::dragView (StelMovementMgr.cpp:1659-1688):
          unProject(x1,y1) -> v1; unProject(x2,y2) -> v2
          rectToSphe -> (az1,alt1),(az2,alt2)
          panView(az2-az1, alt1-alt2)
        这里直接用 screen_to_sky 把前后两点反投影, 求角位移, 调 view_state.pan。
        """
        if self._drag_last_px is None:
            return
        px, py = self._event_xy(event)
        last = self._drag_last_px

        # 位移超过阈值才认为是拖拽 (对照 dragTriggerDistance=4px)
        dx = px - last[0]
        dy = py - last[1]
        if not self._dragging:
            dist = math.hypot(px - self._press_px[0], py - self._press_px[1])
            if dist < self.DRAG_THRESHOLD_PX:
                return
            self._dragging = True
            self.on_drag_state_changed(True)

        # 反投影前后两点到天球
        size = self._widget_size()
        az1, alt1 = screen_to_sky(last[0], last[1], self.view_state,
                                  self.projection, size)
        az2, alt2 = screen_to_sky(px, py, self.view_state,
                                  self.projection, size)

        # pan 的方向: 拖动屏幕点, 让该点下的天空跟手指走。
        # Stellarium: panView(az2-az1, alt1-alt2) (dragView:1684)
        #   deltaAz = az2-az1, deltaAlt = alt1-alt2
        daz = az2 - az1
        if daz > 180.0:
            daz -= 360.0
        elif daz < -180.0:
            daz += 360.0
        dalt = alt1 - alt2
        self.view_state.pan(daz, dalt)

        self._drag_last_px = (px, py)
        self.on_view_changed()

    def mouseReleaseEvent(self, event) -> None:
        """左键释放: 若未拖拽则视为点击 -> 点选天体。

        对照 StelMovementMgr.cpp:700-717:
          if (!hasDragged) objectMgr->findAndSelect(core, x, y, ...)
        """
        was_dragging = self._dragging
        px, py = self._event_xy(event)

        if was_dragging:
            self._dragging = False
            self.on_drag_state_changed(False)
        elif self._press_px is not None:
            # 视为一次点击: 反投影并拾取
            size = self._widget_size()
            az, alt = screen_to_sky(px, py, self.view_state,
                                    self.projection, size)
            obj = pick_object(az, alt, self.sky_objects)
            self.on_object_picked(obj)

        self._drag_origin_px = None
        self._drag_last_px = None
        self._press_px = None

    def wheelEvent(self, event) -> None:
        """滚轮缩放 FOV。

        对照 StelMovementMgr::handleMouseWheel (StelMovementMgr.cpp:537-584):
          numSteps = angleDelta / 120
          zoomFactor = exp(-mouseZoomSpeed * numSteps / 60)
          zoomTo(aimFov * zoomFactor, 0.2)
        """
        # QWheelEvent: angleDelta().y() 上下滚; 纯桩给 (delta,) 元组
        delta = self._event_wheel_delta(event)
        num_steps = delta / 120.0
        self.view_state.zoom(num_steps)
        self.on_view_changed()

    def mouseDoubleClickEvent(self, event) -> None:
        """双击: 居中到点击位置 (goto)。

        对照 Stellarium: 中键点击已选对象 -> moveToObject (StelMovementMgr.cpp:735);
        这里映射为双击任意处 -> 居中到该天球方向 (moveToAltAzi, :1472)。
        """
        px, py = self._event_xy(event)
        size = self._widget_size()
        az, alt = screen_to_sky(px, py, self.view_state,
                                self.projection, size)
        self.view_state.look_at(az, alt)
        self.on_view_changed()

    # -- 事件解包 (兼容 QMouseEvent 与测试桩) ------------------------------
    @staticmethod
    def _event_xy(event) -> Tuple[float, float]:
        """从 QMouseEvent 或 (x,y) 元组/字典取出坐标。"""
        if hasattr(event, "position"):           # PySide6 QMouseEvent
            p = event.position()
            return float(p.x()), float(p.y())
        if hasattr(event, "x"):                  # PyQt4/旧 QMouseEvent
            return float(event.x()), float(event.y())
        if isinstance(event, (tuple, list)):
            return float(event[0]), float(event[1])
        if isinstance(event, dict):
            return float(event["x"]), float(event["y"])
        raise TypeError(f"无法从 {type(event)} 取坐标")

    @staticmethod
    def _event_wheel_delta(event) -> float:
        """从 QWheelEvent 或数值取滚轮增量。"""
        if hasattr(event, "angleDelta"):
            d = event.angleDelta()
            return float(d.x() + d.y())
        if isinstance(event, (int, float)):
            return float(event)
        if isinstance(event, dict):
            return float(event.get("delta", event.get("angleDelta", 0)))
        raise TypeError(f"无法从 {type(event)} 取滚轮增量")

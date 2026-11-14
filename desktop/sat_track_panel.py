# SPDX-License-Identifier: MIT
"""
MBDSDR 卫星闭环跟踪面板
=========================
在"天空模式"图之外提供一个独立控制面板：

  - QComboBox 选择已加载 TLE 卫星；
  - 下行频率输入框（MHz，按卫星名自动套用标准下行频率）；
  - "开始跟踪 / 停止跟踪"按钮（触发 main_window 的 500ms 自动调谐闭环）；
  - "更新 TLE"按钮（从 Celestrak 在线刷新）；
  - 实时显示：方位角 / 仰角 / 多普勒偏移 / 校正后接收频率 / 斜距；
  - 过境倒计时：AOS / LOS 相对当前时刻的剩余时间 + UTC↔本地显示开关；
  - 仰角指示器（0-90° 进度条）：仰角低于跟踪门限时变红并提示"跟踪已停止"。

本面板只负责 UI 展示与发信号，真正的轨道计算与 SDR 调谐闭环在
main_window（持有 mbdsdr_ai.sat_tracker.SatelliteTracker）。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional, Dict

from PySide6.QtCore import Qt, Signal, QTimer, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox,
    QDoubleSpinBox, QPushButton, QProgressBar, QGroupBox, QMessageBox,
    QWidget, QCheckBox, QSizePolicy,
)

from tokens import tokens

# 两个**语义不同**的仰角量，别合并：
#   VISIBLE_MIN_ALT_DEG（5°）= 当前可见/跟踪门：仰角低于它就不再把状态当作
#     「跟踪中」；它决定「现在能不能收」，与用户排班偏好无关。
#   effective_pass_min_elevation_deg()（用户设置，默认 10°）= 过境列表筛选门：
#     按**峰值**仰角筛「这次过境值不值得列出来」，不是当前仰角门。
from mbdsdr_ai.sat_passes import VISIBLE_MIN_ALT_DEG
from mbdsdr_ai.tle_integrity import age_band, tracking_policy
# 时间口径的单一事实源：轨道计算只用 UTC；本地时区仅是显示变换。
# 面板不自己算偏移、不读墙钟——倒计时的 now 由调用方（主窗口）用
# mbdsdr_ai.new_spacetime.get_time_engine() 的同一时基传入。
from mbdsdr_ai.time_basis import format_local, validate_pass_window
try:
    from settings import (
        effective_pass_min_elevation_deg,
        effective_pass_visibility_gate_deg,
        effective_pass_window_hours,
        effective_show_low_elevation_passes,
    )
except ImportError:  # pragma: no cover - 兼容 desktop 目录直接上 sys.path
    from .settings import (  # type: ignore
        effective_pass_min_elevation_deg,
        effective_pass_visibility_gate_deg,
        effective_pass_window_hours,
        effective_show_low_elevation_passes,
    )


#: 标题带的可点击高度（px）。与全局 QSS 的 `QGroupBox { margin-top: 20px }`
#: 成对：标题就画在这条 20px 的带里，带下方才是卡片正文。
_TITLE_BAND_PX = 20

#: TLE 年龄档 → 下拉项后缀/日期行/颜色全部由
#: ``mbdsdr_ai.tle_integrity.tracking_policy`` 给出（显示口径单一事实源），
#: 本模块不再维护第二份档位文案表，也不自己比较 tier。
_TIER_ALIAS = {"stale": "dead"}

#: 过境列表取数的冷却（秒）。列表只由宿主注入的 provider 产生；
#: 窗口内确实没有过境 / provider 失败时，冷却期内不再触发第二次 skyfield 计算。
_PASSES_COOLDOWN_S = 60.0


def unwrap_azimuths(points):
    """把 ``[(az°, el°), ...]`` 的方位角展开成连续序列。

    过境从 359° 跨到 1° 时朴素折线会在图上横穿整个圆盘；展开成
    359→361 后，``polar_xy`` 的三角函数天然把 361° 画回 1° 的位置，
    轨迹连续且不经过圆心。地平线以下（el < 0）的点丢弃。
    """
    out = []
    prev = None
    for az, el in points:
        if el < 0:
            continue
        az = az % 360.0
        if prev is not None:
            d = az - prev
            while d > 180.0:
                az -= 360.0
                d = az - prev
            while d < -180.0:
                az += 360.0
                d = az - prev
        out.append((az, el))
        prev = az
    return out


def polar_xy(az_deg, el_deg, cx, cy, radius):
    """天顶等距投影：仰角 90° 在圆心、0° 在外圈；北=上、东=右。

    对齐 gpredict ``gtk_polar_plot`` 的常见画法（天顶居中、方位顺时针）。
    """
    el = max(0.0, min(90.0, float(el_deg)))
    r = radius * (1.0 - el / 90.0)
    ang = math.radians(float(az_deg))
    return cx + r * math.sin(ang), cy - r * math.cos(ang)


class PolarPassPlot(QWidget):
    """过境方位/仰角极坐标图（只画宿主给的轨迹，不自己算命盘）。"""

    MARGIN_PX = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("polarPassPlot")
        self.setMinimumHeight(200)
        self.setFixedHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._track = []
        self._current = None
        self._note = "暂无过境轨迹"

    # -- 数据入口 ---------------------------------------------------------
    def set_track(self, points, note: str = ""):
        cleaned = []
        for p in points or []:
            try:
                az = float(p["az"])
                el = float(p["el"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(az) and math.isfinite(el):
                cleaned.append((az % 360.0, el))
        self._track = cleaned
        self._note = note or ("暂无过境轨迹" if not cleaned else "")
        self.update()

    def set_current(self, az, el=None):
        if az is None or el is None:
            self._current = None
        else:
            try:
                az = float(az)
                el = float(el)
            except (TypeError, ValueError):
                self._current = None
            else:
                self._current = (az % 360.0, el) if (
                    math.isfinite(az) and math.isfinite(el)) else None
        self.update()

    def track_points(self):
        return list(self._track)

    def current_point(self):
        return self._current

    # -- 绘制 -------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        cx, cy = w / 2.0, h / 2.0
        radius = max(10.0, min(w, h) / 2.0 - self.MARGIN_PX)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(t.COLORS["gray_400"]), 1))
        for el in (0, 30, 60):
            r = radius * (1.0 - el / 90.0)
            p.drawEllipse(QPointF(cx, cy), r, r)
        p.drawLine(QPointF(cx, cy - radius), QPointF(cx, cy + radius))
        p.drawLine(QPointF(cx - radius, cy), QPointF(cx + radius, cy))

        p.setPen(QPen(QColor(t.text("tertiary")), 1))
        font = p.font()
        font.setPointSizeF(8.0)
        p.setFont(font)
        for label, az in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            x, y = polar_xy(az, 0.0, cx, cy, radius + 11.0)
            p.drawText(QRectF(x - 8, y - 8, 16, 16), Qt.AlignCenter, label)

        pts = unwrap_azimuths(self._track)
        if len(pts) >= 2:
            path = QPainterPath()
            x0, y0 = polar_xy(pts[0][0], pts[0][1], cx, cy, radius)
            path.moveTo(x0, y0)
            for az, el in pts[1:]:
                x, y = polar_xy(az, el, cx, cy, radius)
                path.lineTo(x, y)
            p.setPen(QPen(QColor(t.COLORS["accent"]), 2))
            p.drawPath(path)
            for (az, el), color in ((pts[0], t.COLORS["success"]),
                                    (pts[-1], t.COLORS["danger"])):
                x, y = polar_xy(az, el, cx, cy, radius)
                p.setBrush(QColor(color))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(x, y), 3.5, 3.5)
        elif self._note:
            p.setPen(QPen(QColor(t.text("tertiary")), 1))
            p.drawText(QRectF(0, cy - 10, w, 20), Qt.AlignCenter, self._note)

        if self._current is not None:
            x, y = polar_xy(self._current[0], self._current[1], cx, cy, radius)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(t.COLORS.get("accent_warm",
                                               t.COLORS["accent"])), 2))
            p.drawEllipse(QPointF(x, y), 5.0, 5.0)
        p.end()


class DopplerCurvePlot(QWidget):
    """过境多普勒-时间曲线 ``f_d(t)``：只画宿主采样结果，不自己算星历。

    输入 ``points=[{"t": unix_s, "fd": hz}, ...]`` 与 AOS/TCA/LOS 三个时刻。
    点数不足 2 时画说明文字，**不画一条「零偏移」假曲线**——没有采样就是没有。
    """

    MARGIN_LEFT_PX = 46
    MARGIN_RIGHT_PX = 8
    MARGIN_TOP_PX = 10
    MARGIN_BOTTOM_PX = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dopplerCurvePlot")
        self.setMinimumHeight(150)
        self.setFixedHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._points = []
        self._aos = None
        self._tca = None
        self._los = None
        self._freq_hz = None
        self._note = "暂无多普勒曲线"

    # -- 数据入口 ---------------------------------------------------------
    @staticmethod
    def _as_float(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    def set_curve(self, points, aos=None, tca=None, los=None,
                  note: str = "", freq_hz=None):
        cleaned = []
        for item in points or []:
            try:
                tv = float(item["t"])
                fd = float(item["fd"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(tv) and math.isfinite(fd):
                cleaned.append((tv, fd))
        cleaned.sort(key=lambda pair: pair[0])
        self._points = cleaned
        self._aos = self._as_float(aos)
        self._tca = self._as_float(tca)
        self._los = self._as_float(los)
        self._freq_hz = self._as_float(freq_hz)
        self._note = note or ("暂无多普勒曲线" if len(cleaned) < 2 else "")
        self.update()

    def curve_points(self):
        return list(self._points)

    def markers(self):
        return {"aos": self._aos, "tca": self._tca, "los": self._los}

    def note(self):
        return self._note

    def freq_hz(self):
        return self._freq_hz

    # -- 绘制 -------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        left = self.MARGIN_LEFT_PX
        right = max(left + 10.0, w - self.MARGIN_RIGHT_PX)
        top = self.MARGIN_TOP_PX
        bottom = max(top + 10.0, h - self.MARGIN_BOTTOM_PX)

        p.setPen(QPen(QColor(t.COLORS["gray_400"]), 1))
        p.drawLine(QPointF(left, top), QPointF(left, bottom))
        p.drawLine(QPointF(left, bottom), QPointF(right, bottom))
        font = p.font()
        font.setPointSizeF(8.0)
        p.setFont(font)

        if len(self._points) < 2:
            p.setPen(QPen(QColor(t.text("tertiary")), 1))
            p.drawText(QRectF(left, top, right - left, bottom - top),
                       Qt.AlignCenter, self._note or "暂无多普勒曲线")
            p.end()
            return

        ts = [tv for tv, _ in self._points]
        fds = [fd for _, fd in self._points]
        t_lo, t_hi = ts[0], ts[-1]
        if t_hi <= t_lo:
            t_hi = t_lo + 1.0
        span_t = t_hi - t_lo
        f_abs = max(abs(min(fds)), abs(max(fds)), 1.0)

        def x_of(tv):
            return left + (tv - t_lo) / span_t * (right - left)

        def y_of(fv):
            # 对称 y 轴：零点居中，正负偏移的视觉权重一致
            return bottom - (fv + f_abs) / (2.0 * f_abs) * (bottom - top)

        zero_y = y_of(0.0)
        p.setPen(QPen(QColor(t.text("tertiary")), 1, Qt.DashLine))
        p.drawLine(QPointF(left, zero_y), QPointF(right, zero_y))
        p.setPen(QPen(QColor(t.text("tertiary")), 1))
        p.drawText(QRectF(0, zero_y - 8, left - 4, 16),
                   Qt.AlignRight | Qt.AlignVCenter, "0")
        p.drawText(QRectF(0, top - 2, left - 4, 14),
                   Qt.AlignRight | Qt.AlignVCenter, f"+{f_abs:.0f}")
        p.drawText(QRectF(0, bottom - 12, left - 4, 14),
                   Qt.AlignRight | Qt.AlignVCenter, f"-{f_abs:.0f}")

        # AOS/TCA/LOS 竖线：时刻不在采样区间内就不画，不把线钉在边上冒充真实时刻
        for mark, color_key, label in ((self._aos, "success", "AOS"),
                                       (self._tca, "gray_400", "TCA"),
                                       (self._los, "danger", "LOS")):
            if mark is None or not (t_lo <= mark <= t_hi):
                continue
            x = x_of(mark)
            p.setPen(QPen(QColor(t.COLORS.get(color_key,
                                              t.text("tertiary"))), 1, Qt.DashLine))
            p.drawLine(QPointF(x, top), QPointF(x, bottom))
            p.setPen(QPen(QColor(t.text("tertiary")), 1))
            p.drawText(QRectF(x - 18, bottom + 2, 36, 14), Qt.AlignCenter, label)

        path = QPainterPath()
        path.moveTo(QPointF(x_of(ts[0]), y_of(fds[0])))
        for tv, fv in self._points[1:]:
            path.lineTo(QPointF(x_of(tv), y_of(fv)))
        p.setPen(QPen(QColor(t.COLORS["accent"]), 2))
        p.drawPath(path)

        p.setPen(QPen(QColor(t.text("tertiary")), 1))
        p.drawText(QRectF(left - 22, bottom + 2, 44, 14), Qt.AlignCenter,
                   datetime.fromtimestamp(ts[0], timezone.utc).strftime("%H:%M:%S"))
        p.drawText(QRectF(right - 22, bottom + 2, 44, 14), Qt.AlignCenter,
                   datetime.fromtimestamp(ts[-1], timezone.utc).strftime("%H:%M:%S"))
        if self._note:
            p.drawText(QRectF(left, top + 2, right - left, 14),
                       Qt.AlignCenter, self._note)
        p.end()


def _tle_policy(age_days: Optional[float], tier: Optional[str] = None) -> Dict:
    """按年龄/档位取显示与闸门字段（桌面层只读，不写判据）。"""
    level = str(tier or "")
    if not level and age_days is not None:
        level = age_band(age_days)
    return tracking_policy({"tier": _TIER_ALIAS.get(level, level),
                            "age_days": age_days})


def tle_band_badge(age_days: Optional[float],
                   tier: Optional[str] = None) -> tuple:
    """TLE 年龄 → (下拉项后缀, 日期行标签, 语义色键)。

    只读 ``tracking_policy`` 的字段：改文案只改 tle_integrity，本函数不动。
    """
    pol = _tle_policy(age_days, tier)
    return pol["short_tag"], pol["label"], pol["severity"]


class _CollapsibleGroupBox(QGroupBox):
    """点标题行即可展开/收起的分组框。

    Qt 的可勾选 QGroupBox 只在那颗勾选框上响应鼠标（内部 hitButton 取
    SC_GroupBoxCheckBox），点标题文字没反应。可折叠面板的标题行是唯一的
    点击目标——勾选框本身被全局 QSS 收成 0×0，否则会同时出现「☑ ▼」两个
    指示符（2026-11-11 实拍右栏卫星跟踪面板），于是必须由这里接管点击。
    """

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(title, parent)
        self.setMouseTracking(True)

    def _in_title_band(self, y: float) -> bool:
        return y < _TITLE_BAND_PX

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._in_title_band(
                event.position().y()):
            self.setChecked(not self.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # 手型光标只落在标题带上：正文里的下拉/输入框不该变成"可点整块"。
        self.setCursor(Qt.PointingHandCursor
                       if self._in_title_band(event.position().y())
                       else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)


class SatTrackPanel(QFrame):
    """卫星闭环自动跟踪控制面板。

    Signals
    -------
    track_requested(str, float):
        用户点"开始跟踪"，携带 (卫星名, 下行频率 Hz)。
    stop_requested():
        用户点"停止跟踪"。
    update_tle_requested():
        用户点"更新 TLE"。
    """

    track_requested = Signal(str, float)
    stop_requested = Signal()
    update_tle_requested = Signal()

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setObjectName("satTrackPanel")
        self.setFrameShape(QFrame.StyledPanel)
        self._tracking = False
        # 窄屏「可折叠面板」：收起的是分组框正文，标题行与指示符始终可见。
        self._collapsible_boxes: list[QGroupBox] = []
        self._collapse_applied = False
        # 过境列表筛选门从这里读（不是当前可见门，见模块顶部注释）。
        self._settings = settings
        # ---- 过境倒计时（机制 C）----
        # 列表由宿主（主窗口）用 sat_passes.predict_upcoming_passes 算好后注入；
        # 面板不写第二套轨道计算，也不另起定时器去重算，只做「取一次 → 每秒重绘」。
        self._pass_provider = None          # provider(name) -> [{aos_unix, los_unix, ...}]
        self._passes: list[dict] = []
        self._passes_for = ""               # 这份列表属于哪颗卫星
        self._passes_fetched_unix = None    # 上次取数时刻（冷却用）
        self._pass_warning = ""             # 负时长等异常：如实显示，不画假倒计时
        self._last_now_unix = None          # 最近一次显示用的时基（UTC 开关复用）
        #: 已绑定完成回执的宿主；None=还没绑上（见 _bind_tle_completion）。
        self._tle_bound_host = None
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._build_ui()
        # 面板可能一直藏在非当前页签里（永不触发 showEvent），所以在事件循环
        # 第一次空闲时也判定一次；此时主窗口几何已就绪。
        # 三参重载把 receiver 绑到 self：面板的 C++ 对象销毁时定时器自动取消，
        # 否则「从未 show、在首次空闲前被销毁」会回调到已删除对象。
        QTimer.singleShot(0, self, self._apply_narrow_defaults)
        # 面板级 setStyleSheet 已删除，由全局 dark_car QSS 接管按钮/分组框/标签样式。

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel("卫星闭环自动跟踪")
        title.setStyleSheet("font-size: 11pt; font-weight: 600; color: "
                            + tokens().text("secondary") + ";")
        root.addWidget(title)

        tle_row = QHBoxLayout()
        tle_row.setContentsMargins(0, 0, 0, 0)
        tle_row.setSpacing(6)
        self.tle_date_label = QLabel("TLE 日期: --")
        self.tle_date_label.setWordWrap(True)
        self.tle_date_label.setStyleSheet("color: " + tokens().text("tertiary")
                                         + "; font-size: 8.5pt;")
        tle_row.addWidget(self.tle_date_label, 1)
        # 陈旧提示旁的一键入口：看到「建议更新星历」不用再去找下面的按钮。
        self.tle_refresh_btn = QPushButton("刷新星历")
        self.tle_refresh_btn.setToolTip(
            "立即从 Celestrak 更新 TLE；失败会如实报错，不伪造星历")
        self.tle_refresh_btn.setFixedHeight(20)
        self.tle_refresh_btn.clicked.connect(self._on_update_tle_clicked)
        tle_row.addWidget(self.tle_refresh_btn)
        root.addLayout(tle_row)

        # 更新进行中/结果行：状态来自宿主的完成回执（见 _bind_tle_completion），
        # 面板不另存一份「我以为更新完了」的真值。
        self.tle_status_label = QLabel()
        self.tle_status_label.setObjectName("tleStatusLabel")
        self.tle_status_label.setWordWrap(True)
        self.tle_status_label.setMinimumWidth(0)
        self.tle_status_label.setSizePolicy(QSizePolicy.Ignored,
                                            QSizePolicy.Preferred)
        self.tle_status_label.setStyleSheet(
            "color: " + tokens().text("tertiary") + "; font-size: 8.5pt;")
        self.tle_status_label.setVisible(False)
        root.addWidget(self.tle_status_label)

        # ---- 卫星选择 + 下行频率 ----
        sel_box = _CollapsibleGroupBox("目标卫星")
        self._make_collapsible(sel_box, "目标卫星", 2, "satSelBox")
        form = QFormLayout(sel_box)
        self.sat_combo = QComboBox()
        self.sat_combo.setMinimumWidth(220)
        self.sat_combo.currentTextChanged.connect(self._on_sat_selected)
        form.addRow("卫星:", self.sat_combo)

        self.freq_spin = QDoubleSpinBox()
        # 范围与 scanner_panel 一致（0.01–6000 MHz）：合法下行频率全段覆盖，
        # S 波段（如 2271/2401 MHz）也可输入，不被旧 100–2000 MHz 限卡住。
        self.freq_spin.setRange(0.01, 6000.0)      # MHz
        self.freq_spin.setDecimals(4)
        self.freq_spin.setSingleStep(0.0025)
        self.freq_spin.setSuffix(" MHz")
        self.freq_spin.setValue(137.620)
        self.freq_spin.setToolTip(
            "跟踪时调谐到的下行频率（MHz）；实际可调范围以当前 SDR 设备为准；"
            "先用所选制式的默认值，通联确认后再微调")
        form.addRow("下行频率:", self.freq_spin)
        root.addWidget(sel_box)

        # ---- 跟踪按钮 ----
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始跟踪")
        self.stop_btn = QPushButton("停止跟踪")
        self.stop_btn.setEnabled(False)
        self.update_tle_btn = QPushButton("更新 TLE")
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.update_tle_btn)
        root.addLayout(btn_row)

        self.start_btn.clicked.connect(self._on_start_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.update_tle_btn.clicked.connect(self._on_update_tle_clicked)

        # ---- 实时数据 ----
        data_box = _CollapsibleGroupBox("实时状态")
        self._make_collapsible(data_box, "实时状态", 6, "satDataBox")
        dform = QFormLayout(data_box)
        self.az_label = QLabel("--")
        self.el_label = QLabel("--")
        self.dop_label = QLabel("--")
        self.corr_label = QLabel("--")
        self.range_label = QLabel("--")
        self.state_label = QLabel("空闲")
        self.state_label.setStyleSheet("font-weight: 600;")
        dform.addRow("方位角:", self.az_label)
        dform.addRow("仰角:", self.el_label)
        dform.addRow("多普勒:", self.dop_label)
        dform.addRow("校正后频率:", self.corr_label)
        dform.addRow("距离:", self.range_label)
        dform.addRow("状态:", self.state_label)
        root.addWidget(data_box)

        # ---- 仰角指示器（0-90° 进度条）----
        el_box = _CollapsibleGroupBox("仰角指示器")
        self._make_collapsible(el_box, "仰角指示器", 2, "satElBox")
        el_layout = QVBoxLayout(el_box)
        self.el_bar = QProgressBar()
        self.el_bar.setRange(0, 90)
        self.el_bar.setValue(0)
        self.el_bar.setTextVisible(True)
        self.el_bar.setFormat("仰角 %v° / 90°")
        el_layout.addWidget(self.el_bar)
        # 口径分开展示：跟踪可见门（5°）≠ 过境列表峰值筛选门（用户设置）
        self.pass_gate_label = QLabel()
        self.pass_gate_label.setObjectName("passGateLabel")
        self.pass_gate_label.setWordWrap(True)
        self.pass_gate_label.setStyleSheet(
            "color: " + tokens().text("tertiary") + "; font-size: 8.5pt;")
        el_layout.addWidget(self.pass_gate_label)
        self._refresh_pass_gate_label()
        root.addWidget(el_box)

        # ---- 过境倒计时（AOS / LOS）----
        # 放在最后而不是嵌入「实时状态」：它是选星后就能看的信息，
        # 不依赖是否正在跟踪（跟踪中的 500ms 帧顺带刷新它）。
        cd_box = _CollapsibleGroupBox("过境倒计时")
        self._countdown_box = cd_box
        self._make_collapsible(cd_box, "过境倒计时", 3, "satPassBox")
        cd_layout = QVBoxLayout(cd_box)
        self.countdown_label = QLabel("--")
        self.countdown_label.setObjectName("passCountdownLabel")
        self.countdown_label.setWordWrap(True)
        # QSizePolicy.Ignored + 最小宽 0：文本再长也不参与右栏最小宽推导
        # （tests/test_ui_layout_locks.py 的「谁把下限顶高了」会点名）。
        self.countdown_label.setMinimumWidth(0)
        self.countdown_label.setSizePolicy(QSizePolicy.Ignored,
                                           QSizePolicy.Preferred)
        self.countdown_label.setStyleSheet(
            "font-size: 12pt; font-weight: 600; color: "
            + tokens().text("secondary") + ";")
        cd_layout.addWidget(self.countdown_label)
        self.pass_abs_label = QLabel("--")
        self.pass_abs_label.setObjectName("passAbsLabel")
        self.pass_abs_label.setWordWrap(True)
        self.pass_abs_label.setMinimumWidth(0)
        self.pass_abs_label.setSizePolicy(QSizePolicy.Ignored,
                                          QSizePolicy.Preferred)
        self.pass_abs_label.setStyleSheet(
            "color: " + tokens().text("tertiary") + "; font-size: 8.5pt;")
        cd_layout.addWidget(self.pass_abs_label)
        self.utc_check = QCheckBox("UTC 显示")
        self.utc_check.setObjectName("passUtcCheck")
        self.utc_check.setChecked(True)
        self.utc_check.setMinimumWidth(0)
        self.utc_check.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.utc_check.setToolTip(
            "只切换 AOS/LOS **绝对时刻**的显示口径（UTC ↔ 本地时区，带偏移）；"
            "倒计时数字与内部时基始终是同一个 UTC 时钟")
        self.utc_check.toggled.connect(self._on_utc_toggled)
        cd_layout.addWidget(self.utc_check)
        # 收起分组 = 倒计时不可见：1s 重绘必须跟着停，不做常驻心跳。
        cd_box.toggled.connect(self._sync_countdown_timer)
        root.addWidget(cd_box)

        # ---- 过境极坐标（方位/仰角轨迹）----
        # 轨迹点由宿主 provider 的 ``track`` 提供（与过境列表同一条轨道源），
        # 面板只画，不自己点 TLE 重算，避免出现第二份星历真值。
        polar_box = _CollapsibleGroupBox("过境极坐标")
        self._polar_box = polar_box
        self._make_collapsible(polar_box, "过境极坐标", 3, "satPolarBox")
        polar_layout = QVBoxLayout(polar_box)
        self.polar_plot = PolarPassPlot()
        polar_layout.addWidget(self.polar_plot)
        self.polar_hint = QLabel("天顶居中、外圈地平线；绿色=AOS，红色=LOS")
        self.polar_hint.setWordWrap(True)
        self.polar_hint.setMinimumWidth(0)
        self.polar_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.polar_hint.setStyleSheet(
            "color: " + tokens().text("tertiary") + "; font-size: 8.5pt;")
        polar_layout.addWidget(self.polar_hint)
        root.addWidget(polar_box)

        # ---- 过境多普勒-时间曲线 f_d(t) ----
        # 采样点由宿主 provider 的 ``doppler`` 提供（唯一实现是
        # ``sat_passes.compute_doppler_curve``）；面板只画折线与 AOS/TCA/LOS
        # 标线，不重算多普勒，也不缺省补一条零线。
        doppler_box = _CollapsibleGroupBox("过境多普勒")
        self._doppler_box = doppler_box
        self._make_collapsible(doppler_box, "过境多普勒", 3, "satDopplerBox")
        doppler_layout = QVBoxLayout(doppler_box)
        self.doppler_plot = DopplerCurvePlot()
        doppler_layout.addWidget(self.doppler_plot)
        self.doppler_hint = QLabel("f_d(t) = -v_r/c*f0；绿=AOS，灰=TCA，红=LOS")
        self.doppler_hint.setWordWrap(True)
        self.doppler_hint.setMinimumWidth(0)
        self.doppler_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.doppler_hint.setStyleSheet(
            "color: " + tokens().text("tertiary") + "; font-size: 8.5pt;")
        doppler_layout.addWidget(self.doppler_hint)
        root.addWidget(doppler_box)

        # ---- 星下点 / 地面轨迹 / 覆盖圈 ----
        # 复用公用 OSM 地图（osm_map.OsmMapView.add_layer），本面板不画底图。
        # 地图控件懒创建：不展开就不建、更不去拉瓦片。
        ground_box = _CollapsibleGroupBox("地面轨迹 / 覆盖圈")
        self._ground_box = ground_box
        self._make_collapsible(ground_box, "地面轨迹 / 覆盖圈", 3, "satGroundBox")
        gform = QFormLayout(ground_box)
        self.ground_minutes_spin = QDoubleSpinBox()
        self.ground_minutes_spin.setRange(10.0, 180.0)
        self.ground_minutes_spin.setDecimals(0)
        self.ground_minutes_spin.setSingleStep(10.0)
        self.ground_minutes_spin.setSuffix(" min")
        self.ground_minutes_spin.setValue(90.0)
        self.ground_minutes_spin.setToolTip(
            "向后推算多长时间的地面轨迹；默认 90 min 覆盖近地轨道一整圈")
        gform.addRow("轨迹窗口:", self.ground_minutes_spin)
        self.ground_elev_spin = QDoubleSpinBox()
        self.ground_elev_spin.setRange(0.0, 60.0)
        self.ground_elev_spin.setDecimals(0)
        self.ground_elev_spin.setSingleStep(1.0)
        self.ground_elev_spin.setSuffix(" °")
        self.ground_elev_spin.setValue(VISIBLE_MIN_ALT_DEG)
        self.ground_elev_spin.setToolTip(
            "覆盖圈按此最小仰角绘制；门限越高，覆盖半径越小")
        gform.addRow("最小仰角:", self.ground_elev_spin)
        self.ground_hint = QLabel("展开后按当前星历计算星下点与覆盖圈")
        self.ground_hint.setWordWrap(True)
        self.ground_hint.setMinimumWidth(0)
        self.ground_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.ground_hint.setStyleSheet(
            "color: " + tokens().text("tertiary") + "; font-size: 8.5pt;")
        gform.addRow(self.ground_hint)
        self.ground_refresh_btn = QPushButton("计算轨迹")
        self.ground_refresh_btn.setToolTip(
            "按当前星历重算星下点、地面轨迹与覆盖圈；不自动联网刷新")
        self.ground_refresh_btn.clicked.connect(self._on_ground_refresh_clicked)
        gform.addRow(self.ground_refresh_btn)
        self._ground_map = None
        self._ground_form = gform
        ground_box.toggled.connect(self._on_ground_toggled)
        self.ground_minutes_spin.valueChanged.connect(
            self._on_ground_params_changed)
        self.ground_elev_spin.valueChanged.connect(
            self._on_ground_params_changed)
        root.addWidget(ground_box)

        root.addStretch(1)

    # ------------------------------------------------- TLE 更新（宿主驱动）
    # 真值只有一份：主窗口持有更新线程，并在结束时 emit ``_tle_update_result``。
    # 面板只做两件事——把点击变成请求、收到回执后放开按钮并如实写结果。
    # 面板不存本地 inflight 布尔：否则「面板以为还在跑、宿主已经结束」时
    # 用户不知道信谁，等于造了第二真值源。
    def _bind_tle_completion(self) -> None:
        """把「更新是否结束」接到宿主的完成回执上（只读，不改宿主）。"""
        host = self.window()
        if host is None or host is self._tle_bound_host:
            return
        sig = getattr(host, "_tle_update_result", None)
        if sig is None or not hasattr(sig, "connect"):
            # 没有完成回执通道：宁可不进入「进行中」，也不靠计时器猜。
            self._tle_bound_host = None
            return
        sig.connect(self._on_tle_update_finished)
        self._tle_bound_host = host

    def _on_update_tle_clicked(self) -> None:
        self._bind_tle_completion()
        host = self.window()
        if self._tle_bound_host is None:
            # 宿主没有回执通道：请求照样发，但不假装自己知道结果。
            self.update_tle_requested.emit()
            self._set_tle_busy(False)
            self._show_tle_status(
                "已请求更新 TLE；本面板收不到宿主完成回执，结果请以状态栏为准。")
            return
        self._set_tle_busy(True)
        # 10 s = update_tle_from_celestrak 的 5 s 请求超时 + 5 s 墙钟余量
        # （_TLE_HARD_DEADLINE_MARGIN_S）；这是链路自报的上限，不是本面板猜的。
        self._show_tle_status("正在更新 TLE…（后台最长约 10 秒，结束后这里显示结果）")
        self.update_tle_requested.emit()
        if getattr(host, "_loop_sat_tracker", None) is None:
            # 宿主明确拒绝（没有可更新的星历目录）：不会再有完成回执，立即落地。
            self._set_tle_busy(False)
            self._show_tle_status(
                "未开始更新 TLE：本地星历目录不可用（没有可更新的卫星）。\n"
                "现在可以：确认 TLE 文件存在后点「更新 TLE」重试。")

    def _on_tle_update_finished(self, payload) -> None:
        """宿主完成回执：复位按钮，并写宿主给的结论（不自行判成功）。"""
        try:
            count, failure = payload
        except (TypeError, ValueError):
            count, failure = 0, {
                "code": "tle_unknown",
                "reason": "完成回执格式不可解析（不是（更新数, 失败对象））"}
        self._set_tle_busy(False)
        # 与状态栏同一个渲染器：措辞只有一份，不在这里发明第二套成败话术。
        from mbdsdr_ai.tle_fetch import tle_refresh_status_text
        self._show_tle_status(tle_refresh_status_text(int(count or 0), failure))

    def _set_tle_busy(self, busy: bool) -> None:
        self.update_tle_btn.setEnabled(not busy)
        self.tle_refresh_btn.setEnabled(not busy)
        self.update_tle_btn.setText("更新中…" if busy else "更新 TLE")

    def _show_tle_status(self, text: str) -> None:
        # 标签本身 setWordWrap(True)，全文直接可见；不再复写一份 tooltip：
        # 逐字重复可见文字没有新增信息，还会多出一条未登记的透传文案。
        self.tle_status_label.setText(text)
        self.tle_status_label.setVisible(bool(text))

    # ------------------------------------------------------- 窄屏可折叠
    def _make_collapsible(self, box: QGroupBox, label: str, count: int,
                          object_name: str) -> None:
        """把分组框改成「可折叠」：收起只留标题行 + 指示符，正文隐藏。

        可达性口径（docs/narrow-screen-plan.md §2.6）：
        * 标题行始终可见，且写出「里面有什么」与条目数；
        * 指示符 ▼=已展开 / ▶=已收起，tooltip 写清点击后的动作；
        * 控件一个不删——收起只是 ``setChecked(False)``，子控件仍可按属性名取到。
        """
        box.setObjectName(object_name)
        box.setCheckable(True)
        box.setChecked(True)
        # 让全局 QSS 把这颗勾选框收成 0×0：展开态已经由标题里的 ▼/▶ 表达。
        box.setProperty("collapseBox", True)
        self._collapsible_boxes.append(box)

        def _sync(checked: bool) -> None:
            box.setTitle(f"{'▼' if checked else '▶'} {label} · {count} 项")
            box.setToolTip("点击标题收起" if checked
                           else "已收起，点击标题展开")
            # 收起必须真的隐藏正文：Qt 的可勾选 QGroupBox 只置灰不隐藏，
            # 高度不会降。控件本身保留（不删），只是 setVisible(False)。
            lay = box.layout()
            if lay is not None:
                for i in range(lay.count()):
                    it = lay.itemAt(i)
                    w = it.widget() if it is not None else None
                    if w is not None:
                        w.setVisible(checked)

        box.toggled.connect(_sync)
        _sync(True)

    def _apply_narrow_defaults(self) -> None:
        """窄屏（<1440）首次判定：默认收起「实时状态」「仰角指示器」。

        目标卫星分组保持展开（开始跟踪要先选卫星）；只判定一次，避免窗口
        拖动来回抖动。宽屏保持全展开。默认态**不持久化**（见 plan §2.6）。
        """
        if self._collapse_applied:
            return
        self._collapse_applied = True
        if self.window().width() < 1440:
            for box in self._collapsible_boxes[1:]:
                # 过境倒计时是「选完星就能看」的主信息，窄屏也保持展开；
                # 其余大块状态可以收起省高度。
                if box is self._countdown_box:
                    continue
                box.setChecked(False)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_narrow_defaults()
        self._sync_countdown_timer()

    def hideEvent(self, event):
        self._countdown_timer.stop()
        super().hideEvent(event)

    def _sync_countdown_timer(self, _checked: bool = False) -> None:
        """1 s 重绘只在该看倒计时时存活：面板可见 **且** 分组展开。

        收起分组、切到别的页签（hideEvent）都必须停心跳——
        「重排很便宜」不是常驻每秒定时器的理由。
        """
        if self.isVisible() and self._countdown_box.isChecked():
            if not self._countdown_timer.isActive():
                self._countdown_timer.start()
            self._update_countdown(None)
        else:
            self._countdown_timer.stop()

    # ------------------------------------------------------------ 对外接口
    def populate_satellites(self, names, tier_badges: Optional[Dict[str, str]] = None):
        """填充卫星下拉框；保持当前选中项尽量不变。

        ``tier_badges`` = {卫星名: 陈旧后缀}（如「失效」「过期 12 天」），
        直接拼在下拉项末尾，让用户**在选之前**就知道这颗星历靠不靠得住。
        原始名永远存进 itemData，取名字一律用 ``current_satellite_name()``。
        """
        prev = self.current_satellite_name()
        self.sat_combo.blockSignals(True)
        self.sat_combo.clear()
        for n in names:
            badge = (tier_badges or {}).get(n) or ""
            self.sat_combo.addItem(f"{n}（{badge}）" if badge else n, n)
        if prev:
            idx = self.sat_combo.findData(prev)
            if idx < 0:
                idx = self.sat_combo.findText(prev)
            if idx >= 0:
                self.sat_combo.setCurrentIndex(idx)
        self.sat_combo.blockSignals(False)

    def current_satellite_name(self) -> str:
        """当前选中卫星的**原始名**（下拉项文本可能带「（失效）」后缀）。"""
        data = self.sat_combo.currentData()
        if data:
            return str(data)
        return self.sat_combo.currentText().strip()

    def set_tle_date(self, date_str: str, age_days: Optional[float] = None,
                     tier: Optional[str] = None):
        """TLE 日期 + 陈旧档位 + 下一步提示（全部读 tracking_policy 字段）。

        - usable：显示「建议更新星历」，**不阻断**调谐；
        - expired/dead/unknown：显示「需确认/请更新」，云台入口由主窗口按
          同一份 policy 的 ``allow_gimbal`` 禁用；
        - 档位阈值与文案只在 ``mbdsdr_ai.tle_integrity`` 维护，本层不比较 tier。
        """
        pol = _tle_policy(age_days, tier)
        text = f"TLE 日期: {date_str} · {pol['label']}"
        if pol["age_text"]:
            text += f"（{pol['age_text']} 天前）"
        if pol["hint"]:
            text += f" · {pol['hint']}"
        self.tle_date_label.setText(text)
        self.tle_date_label.setStyleSheet(
            "color: " + self._tle_color(pol["severity"]) + "; font-size: 8.5pt;")
        self.tle_date_label.setToolTip(
            "；".join(x for x in (pol["reason"], pol["next_action"]) if x))

    @staticmethod
    def _tle_color(key: str) -> str:
        if key == "warning":
            return tokens().status_color("warning")
        if key == "danger":
            return tokens().COLORS["danger"]
        return tokens().text("tertiary")

    def set_tracking_state(self, tracking: bool):
        self._tracking = tracking
        self.start_btn.setEnabled(not tracking)
        self.stop_btn.setEnabled(tracking)
        self.sat_combo.setEnabled(not tracking)
        self.freq_spin.setEnabled(not tracking)
        if not tracking:
            self.state_label.setText("已停止")
            self.state_label.setStyleSheet("color: " + tokens().text("tertiary")
                                           + "; font-weight:600;")

    def set_observer_ready(self, ready: bool):
        """观测者位置是否就绪；未就绪时开始按钮置灰并提示。"""
        if not ready and not self._tracking:
            self.start_btn.setEnabled(False)
        else:
            self.start_btn.setEnabled(not self._tracking)

    def update_position(self, pos: Dict[str, object], now_unix=None):
        """用 tracker.current_position() 的结果刷新显示。

        ``now_unix`` 与位置帧同一次采样（宿主的 get_time_engine().now_unix()）。
        传 None 时面板自己向同一时间引擎取一次——绝不用 ``time.time()`` 另造
        时间基准；引擎不可用就明说倒计时暂停。
        """
        # 即便 pos 无效（未跟踪/无星）也要刷新倒计时：AOS 前正是不在跟踪时。
        self._update_countdown(now_unix)
        # 设置可能被用户改过：每次刷新重读两个门的显示（只拼一行文本）。
        self._refresh_pass_gate_label()
        if not pos.get("valid"):
            self.az_label.setText("--")
            self.el_label.setText("--")
            self.dop_label.setText("--")
            self.corr_label.setText("--")
            self.range_label.setText("--")
            self.el_bar.setValue(0)
            self._set_el_color(tokens().COLORS["gray_300"])
            self.polar_plot.set_current(None)
            return

        az = float(pos["azimuth"])
        el = float(pos["elevation"])
        dop = float(pos["doppler_hz"])
        corr = float(pos["corrected_freq_hz"])
        rng = float(pos["range_km"])
        self.polar_plot.set_current(az, el)

        self.az_label.setText(f"{az:6.1f}°")
        self.el_label.setText(f"{el:6.1f}°")
        if abs(dop) < 0.5:      # N-02：负零归零，避免「-0 Hz」误读成反向偏移
            dop = 0.0
        self.dop_label.setText(f"{dop:+8.1f} Hz")
        self.corr_label.setText(f"{corr/1e6:9.6f} MHz")
        self.range_label.setText(f"{rng:7.0f} km")

        # 仰角条：钳到 0..90
        el_bar_val = int(max(0.0, min(90.0, el)))
        self.el_bar.setValue(el_bar_val)

        if el < VISIBLE_MIN_ALT_DEG:
            # 当前可见/跟踪门（5°）：低于它标红并提示过境结束。
            # 用户设置的「过境最低仰角」是峰值筛选门，**不用在这里**——
            # 25° 时卫星仍可跟踪，不能因为用户想少列过境就假装跟踪结束。
            _t = tokens()
            self._set_el_color(_t.COLORS["danger"])
            self.state_label.setText(
                f"跟踪已停止（仰角低于设定门限 {VISIBLE_MIN_ALT_DEG:g}°；"
                "卫星尚未落下）")
            self.state_label.setStyleSheet("color:" + _t.COLORS["danger"]
                                           + "; font-weight:600;")
        else:
            _t = tokens()
            self._set_el_color(_t.COLORS["success"])
            self.state_label.setText("跟踪中")
            self.state_label.setStyleSheet("color:" + _t.COLORS["success"]
                                           + "; font-weight:600;")

    def _resolved_settings(self):
        """显式注入优先；否则取宿主（主窗口）的 settings 实例（内存值即时生效）。"""
        if self._settings is not None:
            return self._settings
        return getattr(self.window(), "settings", None)

    def _refresh_pass_gate_label(self) -> None:
        """显示两个门的分工：可见/跟踪门 5° vs 过境列表峰值门（用户设置）。"""
        settings = self._resolved_settings()
        a1 = effective_pass_min_elevation_deg(settings)
        gate = effective_pass_visibility_gate_deg(settings)
        show_low = effective_show_low_elevation_passes(settings)
        text = (f"跟踪/可见门 {VISIBLE_MIN_ALT_DEG:g}°（只决定当前能否跟踪）；"
                f"过境列表筛选门：峰值仰角 ≥ {gate:g}°")
        if show_low and a1 > gate:
            text += f"（已含 {gate:g}–{a1:g}° 低仰角）"
        self.pass_gate_label.setText(text)

    def _set_el_color(self, hex_color: str):
        _t = tokens()
        self.el_bar.setStyleSheet(
            f"QProgressBar {{ border:1px solid {_t.COLORS['gray_400']}; "
            f"border-radius:3px; text-align:center; "
            f"background:{_t.COLORS['card_2']}; "
            f"color:{_t.text('secondary')}; }}"
            f"QProgressBar::chunk {{ background:{hex_color}; }}"
        )

    # ---------------------------------------------------------- 过境倒计时
    def set_pass_provider(self, provider) -> None:
        """注入过境数据源：``provider(name) -> [{aos_unix, los_unix, max_alt?}, ...]``。

        轨道计算在宿主（主窗口）里用 ``sat_passes.predict_upcoming_passes``
        完成；面板只消费结果——不写第二套轨道计算，也不另起定时器重算。
        """
        self._pass_provider = provider
        self.invalidate_passes()

    def invalidate_passes(self) -> None:
        """丢弃缓存的过境列表：换星 / TLE 更新 / 站址变更时由宿主调用。"""
        self._passes = []
        self._passes_for = ""
        self._passes_fetched_unix = None
        self._pass_warning = ""
        self.polar_plot.set_track([])
        self.doppler_plot.set_curve([])
        # 注意：不清 ``_last_now_unix``——它只是「上次显示用的时基」，
        # 与过境数据无关；清掉会让 UTC 开关退回一次时钟读取（数字会跳）。

    def _ensure_passes(self, name: str, now_unix: float) -> None:
        """确保手上有覆盖 now 的过境列表；只在「换星/窗口耗尽/冷却到期」时取数。"""
        if not name:
            self._passes = []
            self._passes_for = ""
            return
        if name != self._passes_for:
            # 切星：旧列表是别的卫星的过境，绝不能拿来给这颗星倒计时。
            self.invalidate_passes()
            self._passes_for = name
        if self._passes and max(r["los"] for r in self._passes) > now_unix:
            return
        if (self._passes_fetched_unix is not None
                and now_unix - self._passes_fetched_unix < _PASSES_COOLDOWN_S):
            # 刚取过（窗口内确实没有过境 / provider 失败）：冷却期内不再触发
            # 第二次 skyfield 计算，避免每秒一拍把界面拖死。
            return
        if self._pass_provider is None:
            self._passes = []
            return
        try:
            rows = list(self._pass_provider(name) or [])
        except Exception as exc:          # provider 是宿主的，异常不外抛
            self._passes = []
            self._passes_fetched_unix = now_unix
            self._pass_warning = f"过境预测失败：{exc}"
            return
        self._passes_fetched_unix = now_unix
        self._pass_warning = ""
        parsed = []
        for row in rows:
            try:
                aos = float(row["aos_unix"])
                los = float(row["los_unix"])
            except (KeyError, TypeError, ValueError):
                continue
            chk = validate_pass_window(aos, los, now_unix)
            if not chk["ok"]:
                # 负时长/时钟回跳：如实标出来，不画一个假倒计时。
                self._pass_warning = chk["warning"]
                continue
            peak = row.get("max_alt")
            parsed.append({"aos": aos, "los": los, "peak_alt": peak,
                           "track": row.get("track") or [],
                           # 多普勒曲线与 AOS/LOS 同源（宿主一次算好）；
                           # freq_hz 是采样时用的标称频率，缺它只能报未知。
                           "doppler": row.get("doppler") or [],
                           "tca": row.get("tca_unix"),
                           "freq_hz": row.get("freq_hz")})
        parsed.sort(key=lambda r: r["aos"])
        self._passes = parsed

    @staticmethod
    def _engine_now_unix():
        """全局时间引擎的 Unix 时刻（与主窗口/天空图同一时基）。

        失败返回 None——绝不回退 ``time.time()`` 另造时间基准。
        """
        try:
            from mbdsdr_ai.new_spacetime import get_time_engine
            return float(get_time_engine().now_unix())
        except Exception:
            return None

    @staticmethod
    def _fmt_when(ts_unix, utc: bool) -> str:
        """Unix 时刻 → 显示字符串。UTC 用「UTC」后缀，本地走 time_basis.format_local。"""
        dt = datetime.fromtimestamp(float(ts_unix), timezone.utc)
        if utc:
            return dt.strftime("%m-%d %H:%M:%S") + " UTC"
        return format_local(dt)

    @staticmethod
    def _fmt_countdown(seconds: float) -> str:
        """剩余秒 → ``H:MM:SS`` / ``MM:SS``；向上取整（还剩 0.2s 不显示 00:00）。"""
        s = int(math.ceil(max(0.0, float(seconds))))
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

    def _on_countdown_tick(self) -> None:
        """1s 重绘（不重算过境）：只把已取到的列表对着同一时基重新排一次。"""
        self._update_countdown(None)

    def _on_utc_toggled(self, _checked: bool = False) -> None:
        """UTC 开关只改绝对时刻的显示口径；倒计时数字与内部时基不动。"""
        self._update_countdown(self._last_now_unix)

    def _update_countdown(self, now_unix=None) -> None:
        """刷新 AOS/LOS 倒计时。

        ``now_unix=None`` → 向全局时间引擎取当前时刻（与主窗口同一时基）；
        引擎不可用就显示暂停说明，绝不回退墙钟。
        """
        if now_unix is None:
            now_unix = self._engine_now_unix()
        if now_unix is None:
            self.countdown_label.setText("--")
            self.pass_abs_label.setText("时间引擎不可用，倒计时暂停")
            self.polar_plot.set_track([])
            self.doppler_plot.set_curve([])
            return
        self._last_now_unix = float(now_unix)

        name = self.current_satellite_name()
        if not name:
            self.countdown_label.setText("--")
            self.pass_abs_label.setText("先选择一颗卫星")
            self.polar_plot.set_track([])
            self.doppler_plot.set_curve([])
            return
        self._ensure_passes(name, now_unix)
        if self._pass_provider is None:
            self.countdown_label.setText("--")
            self.pass_abs_label.setText("过境数据未接入（由主窗口注入预测结果）")
            self.polar_plot.set_track([])
            self.doppler_plot.set_curve([])
            return

        active = next((r for r in self._passes if r["los"] > now_unix), None)
        if active is None:
            self.countdown_label.setText("窗口内无过境")
            self.pass_abs_label.setText(
                self._pass_warning or "预测窗口内这颗星不再升起")
            self.polar_plot.set_track([])
            self.doppler_plot.set_curve([])
            return
        track = active.get("track") or []
        self.polar_plot.set_track(
            track, note="" if track else "该过境无轨迹数据")
        self._update_doppler_curve(active)

        utc = self.utc_check.isChecked()
        aos_txt = self._fmt_when(active["aos"], utc)
        los_txt = self._fmt_when(active["los"], utc)
        if now_unix < active["aos"]:
            self.countdown_label.setText(
                "AOS 倒计时 " + self._fmt_countdown(active["aos"] - now_unix))
            sub = f"AOS {aos_txt} · LOS {los_txt}"
        else:
            self.countdown_label.setText(
                "LOS 倒计时 " + self._fmt_countdown(active["los"] - now_unix))
            peak = active.get("peak_alt")
            peak_txt = ""
            if peak is not None:
                try:
                    peak_txt = f" · 峰值仰角 {float(peak):.0f}°"
                except (TypeError, ValueError):
                    peak_txt = ""
            sub = f"LOS {los_txt}{peak_txt}"
        if self._pass_warning:
            sub += f" · {self._pass_warning}"
        self.pass_abs_label.setText(sub)

    def _update_doppler_curve(self, active: Dict) -> None:
        """把宿主的 f_d(t) 采样交给绘图控件；缺数据时写明原因，不画假曲线。

        面板不做频率换算：``freq_hz`` 是宿主采样时用的标称频率，只用于标注
        「这条曲线是在什么频率下算的」。当前输入与之不同时明说尚未刷新，
        避免把旧频率的曲线当成新输入的结论。
        """
        points = active.get("doppler") or []
        used_hz = DopplerCurvePlot._as_float(active.get("freq_hz"))
        cur_hz = float(self.current_downlink_hz())
        if not points:
            note = ("未提供下行频率，无法画 f_d(t)"
                    if not used_hz or used_hz <= 0
                    else "该过境没有多普勒采样点（宿主未提供）")
        elif not used_hz or used_hz <= 0:
            note = "宿主未给采样频率，曲线频率口径未知"
        elif abs(cur_hz - used_hz) > 1.0:
            note = (f"f_d(t) @ {used_hz / 1e6:.4f} MHz"
                    f"（当前输入 {cur_hz / 1e6:.4f} MHz，下次取数后更新）")
        else:
            note = f"f_d(t) @ {used_hz / 1e6:.4f} MHz"
        self.doppler_plot.set_curve(points, active.get("aos"), active.get("tca"),
                                    active.get("los"), note=note,
                                    freq_hz=used_hz)

    # ------------------------------------------------------------ 内部槽
    def _on_sat_selected(self, name: str):
        """用户在下拉框选了卫星：由 main_window 注入下行频率。这里只发出查询请求，
        实际频率回填由 main_window 通过 set_downlink_mhz 完成。"""
        # 换星后旧过境列表属于别的卫星，立刻失效并重排一次倒计时。
        self.invalidate_passes()
        self._update_countdown(None)
        self.satellite_changed.emit(name)

    # 额外信号：卫星切换时通知 main_window 刷新下行频率
    satellite_changed = Signal(str)

    def set_downlink_mhz(self, mhz: float):
        if mhz > 0:
            self.freq_spin.blockSignals(True)
            self.freq_spin.setValue(mhz)
            self.freq_spin.blockSignals(False)

    def current_downlink_hz(self) -> float:
        return self.freq_spin.value() * 1e6

    def _on_start_clicked(self):
        if self._tracking:
            # 已在跟踪：重复触发（连点、菜单/外部调用）一律幂等，
            # 绝不再发一次 track_requested——主窗口若重入会覆盖
            # 「跟踪前频率/模式」，停止时就回不到用户原来的调谐。
            # 但必须留下可见反馈：静默 return 等于点了没反应。
            self.state_label.setText("跟踪中（忽略重复开始）")
            return
        name = self.current_satellite_name()
        if not name:
            QMessageBox.warning(self, "卫星跟踪", "请先选择一颗卫星。")
            return
        self.track_requested.emit(name, self.current_downlink_hz())

    def _on_stop_clicked(self):
        self.stop_requested.emit()

    # ------------------------------------------- 星下点 / 地面轨迹 / 覆盖圈
    def _on_ground_toggled(self, checked: bool) -> None:
        if checked:
            self._refresh_ground_view()

    def _on_ground_refresh_clicked(self, _checked: bool = False) -> None:
        self._refresh_ground_view()

    def _on_ground_params_changed(self, _value: float = 0.0) -> None:
        if self._ground_box.isChecked():
            self._refresh_ground_view()

    def _ensure_ground_map(self):
        """懒创建地图控件：展开时才 import/建图，失败给一句人话。"""
        if self._ground_map is not None:
            return self._ground_map
        try:
            from sat_ground_map import SatelliteGroundMap
        except ImportError as exc:      # 缺 QtNetwork 等可选依赖
            self.ground_hint.setText(f"地图不可用：{type(exc).__name__}")
            return None
        self._ground_map = SatelliteGroundMap(self)
        self._ground_form.addRow(self._ground_map)
        try:
            self._ground_map.feature_detail.connect(
                self._on_ground_feature_detail)
        except (AttributeError, RuntimeError):
            pass
        return self._ground_map

    def _on_ground_feature_detail(self, text: str, suggestion: str,
                                  level: str) -> None:
        """点中地面对象：详情走现有提示行，完整原因进 tooltip。

        不新增控件、不弹模态框；``unknown`` 的原因原样带出，不折叠成「无」。
        """
        try:
            self.ground_hint.setText(text)
            self.ground_hint.setToolTip(suggestion)
        except (AttributeError, RuntimeError):
            pass

    def _refresh_ground_view(self) -> None:
        """按当前选中卫星重算并重绘；失败如实写原因，不画假轨迹。"""
        name = self.current_satellite_name()
        if not name:
            self.ground_hint.setText("先选择一颗卫星，再展开地面轨迹")
            return
        view = self._ensure_ground_map()
        if view is None:
            return
        self.ground_hint.setText("正在按当前星历计算…")
        ok = view.update_satellite(
            name,
            minutes=float(self.ground_minutes_spin.value()),
            min_elevation_deg=float(self.ground_elev_spin.value()))
        self.ground_hint.setText(
            view.summary_text if ok else f"无法计算：{view.error_text}")

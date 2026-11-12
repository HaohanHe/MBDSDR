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

from PySide6.QtCore import Qt, Signal, QTimer
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
        self.tle_refresh_btn.clicked.connect(self.update_tle_requested.emit)
        tle_row.addWidget(self.tle_refresh_btn)
        root.addLayout(tle_row)

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
        self.update_tle_btn.clicked.connect(self.update_tle_requested.emit)

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

        root.addStretch(1)

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
            return

        az = float(pos["azimuth"])
        el = float(pos["elevation"])
        dop = float(pos["doppler_hz"])
        corr = float(pos["corrected_freq_hz"])
        rng = float(pos["range_km"])

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
            parsed.append({"aos": aos, "los": los, "peak_alt": peak})
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
            return
        self._last_now_unix = float(now_unix)

        name = self.current_satellite_name()
        if not name:
            self.countdown_label.setText("--")
            self.pass_abs_label.setText("先选择一颗卫星")
            return
        self._ensure_passes(name, now_unix)
        if self._pass_provider is None:
            self.countdown_label.setText("--")
            self.pass_abs_label.setText("过境数据未接入（由主窗口注入预测结果）")
            return

        active = next((r for r in self._passes if r["los"] > now_unix), None)
        if active is None:
            self.countdown_label.setText("窗口内无过境")
            self.pass_abs_label.setText(
                self._pass_warning or "预测窗口内这颗星不再升起")
            return

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

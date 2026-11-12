# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端 - SDR++ 式模块面板（信号流图 + 设备选择 + 参数 + sink）
=====================================================================

把 SDR++ 主窗口里最核心的那块"模块菜单 + 信号流"搬到 MBDSDR：

  SDR++ 主窗口布局（来源: core/src/core.cpp:138-157 menuElements）
      Source | Radio | Recorder | Sinks | Frequency Manager | ...
  SDR++ 源设备下拉（来源: source_modules/soapy_source/src/main.cpp:403-408）
      一个 Combo 列 enumerate() 出来的设备 label，没设备就只画 Refresh（:389-397）

本面板四区：
  左：源设备下拉（调 mbdsdr_ai.sdr_backend.enumerate_all_sdr_devices()，真实枚举）
  中：信号流可视化（QPainter 画模块节点 + 连线，SDR++ 式 source→demod→decoder→sink）
  右：选中模块的参数面板（频率/增益/采样率/解调模式）
  底：sink 选择（音频/文件/网络，对应 sink_modules/audio_sink | recorder | network_sink）

无设备时下拉显示"未发现SDR设备"，绝不写死假设备（用户红线）。

配色（固定深色画布，不随主题）：
  画布底 card_1 #1f1f1f / 主结构 gray_200 / 强调 accent / 占位 gray_300
  画布内的取色是模块级常量，换主题不会重绘配色。
"""
from __future__ import annotations

import os
import sys
import math
from typing import Dict, List, Optional

import logging

logger = logging.getLogger(__name__)

# 允许从 desktop/ 直接跑，也允许被 main_window import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QTimer, QSignalBlocker, \
    QObject, QRunnable, QThreadPool
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetricsF, QPainterPath,
    QLinearGradient,
)
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFormLayout, QComboBox, QPushButton,
    QLabel, QDoubleSpinBox, QSlider, QGroupBox, QMessageBox, QFrame,
)

from tokens import tokens as _tok
from mode_registry import get as _get_mode
from readouts import (
    format_gain_db as _format_gain_db,
    gain_differs as _gain_differs,
    gain_not_applied_hint as _gain_not_applied_hint,
)
from constants import (
    DEFAULT_SAMPLE_RATE_HZ, DEFAULT_SR_UNCONFIRMED,
    FREQ_MIN_HZ, FREQ_MAX_HZ, format_sample_rate,
    default_sample_rate_combo_index, sample_rate_combo_items,
    connect_sample_rate_hz)
# 连接请求的唯一持有者：本面板只渲染/写回，不再自持 _sample_rate/_gain 副本。
import connect_request_store as _req_store

try:  # pragma: no cover - 失败消息整形（clean_exc 战役先例），缺失时退化原文
    from mbdsdr_ai.tool_registry import clean_exc as _clean_exc
except Exception:  # noqa: BLE001
    def _clean_exc(e):  # type: ignore[misc]
        return str(e)

_t = _tok()

# CarWith dark_car 配色（画布底色/连线/文字从 tokens 取）
PAPER = QColor(_t.COLORS["card_1"])       # 画布底
STEEL = QColor(_t.COLORS["gray_200"])     # 连线/主结构
OCHRE = QColor(_t.COLORS["accent"])       # 强调/选中/sink
INK = QColor(_t.COLORS["text_primary"])   # 节点文字
MUTED = QColor(_t.COLORS["gray_300"])     # 占位/次要文字（主题表面）
#: 画布（PAPER）上的空态文字：MUTED 的 gray_300 对 card_1 只有 2.62:1，
#: 只够主题表面用；画布上取 gray_200（复用 STEEL 的取值，不新增直读）。
CANVAS_MUTED = QColor(STEEL)

# 模块节点按类型上色（source/processing/decoder/sink）
_TYPE_COLORS = {
    "source": QColor(_t.COLORS["source_node"]),     # 偏青：源（功能标识色）
    "processing": STEEL,              # 青灰：解调
    "decoder": QColor(_t.COLORS["success"]),  # 偏绿：解码
    "sink": OCHRE,                    # 强调：sink
}


def _elide_node_name(name: str, font: QFont, width: float) -> str:
    """信号流节点名按像素右省略。

    按字符数截断（旧实现 len>16 才切）在窄节点里不够：节点文本区只有
    108px，全角字符或长英文名仍会溢出，被 drawText 无声硬切且没有「…」
    提示还有下文。
    """
    return QFontMetricsF(font).elidedText(name, Qt.ElideRight, width)


# ======================================================================
#  信号流画布：QPainter 画模块节点 + 连线
# ======================================================================
class FlowCanvas(QWidget):
    """把 ModuleGraph 的模块节点和边画成 SDR++ 式横向信号流图。

    节点布局按类型分列：source | processing/decoder | sink，类似 SDR++ 信号从左到右。
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), PAPER)
        self.setPalette(pal)
        self.nodes: List[Dict] = []   # [{name,type,x,y,w,h}]
        self.edges: List[tuple] = []  # [(src_name, dst_name)]
        self.selected: Optional[str] = None

    def set_graph(self, modules: List[Dict], edges: List[tuple]) -> None:
        """根据模块图重排节点位置。"""
        self.nodes = []
        self.edges = edges
        # 三列：source(左) / processing+decoder(中) / sink(右)
        cols = {"source": 0, "processing": 1, "decoder": 1, "sink": 2}
        x_by_col = [0.12, 0.45, 0.78]
        w, h = 120, 54
        counters = [0, 0, 0]
        # 先按类型分组，保持顺序
        for m in modules:
            c = cols.get(m["type"], 1)
            count = counters[c]
            counters[c] += 1
            x = x_by_col[c]
            # 垂直居中排开
            y = 0.18 + count * 0.22
            self.nodes.append({
                "name": m["name"], "type": m["type"],
                "x": x, "y": y, "w": w, "h": h,
            })
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = self.width(), self.height()

        # 背景
        p.fillRect(self.rect(), PAPER)

        if not self.nodes:
            p.setPen(CANVAS_MUTED)
            f = QFont(self.font()); f.setPointSize(11)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "信号流图\n从下方选择源设备并添加模块")
            p.end()
            return

        # 先画连线（在节点下层）
        pos = {n["name"]: n for n in self.nodes}
        pen = QPen(STEEL, 2.0)
        p.setPen(pen)
        for src_name, dst_name in self.edges:
            if src_name not in pos or dst_name not in pos:
                continue
            a = pos[src_name]; b = pos[dst_name]
            x1 = (a["x"] + a["w"]) * W
            y1 = (a["y"] + a["h"] / 2) * H
            x2 = b["x"] * W
            y2 = (b["y"] + b["h"] / 2) * H
            path = QPainterPath(QPointF(x1, y1))
            mx = (x1 + x2) / 2
            path.cubicTo(QPointF(mx, y1), QPointF(mx, y2), QPointF(x2, y2))
            p.drawPath(path)
            # 箭头
            p.setBrush(STEEL)
            p.drawEllipse(QPointF(x2, y2), 3, 3)

        # 再画节点
        for n in self.nodes:
            x = n["x"] * W
            y = n["y"] * H
            rect = QRectF(x, y, n["w"], n["h"])
            color = _TYPE_COLORS.get(n["type"], STEEL)
            p.setPen(QPen(INK, 1.5 if self.selected == n["name"] else 0.75))
            p.setBrush(QBrush(color.lighter(130)))
            p.drawRoundedRect(rect, 8, 8)
            # 类型小标签
            p.setPen(INK)
            f = QFont(self.font()); f.setPointSize(9); f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(x + 6, y + 4, n["w"] - 12, 16),
                       Qt.AlignLeft | Qt.AlignVCenter, n["type"].upper())
            f2 = QFont(self.font()); f2.setPointSize(8)
            p.setFont(f2)
            # 按字符数截断会漏：16 个全角字符在 108px 文本区里远放不下，
            # 多余部分被 drawText 直接切掉（无省略号，读不出「还有下文」）。
            # 改按像素省略（QFontMetricsF 与当前字体同源，DPI 缩放后自动跟随）。
            elide = _elide_node_name(n["name"], f2, n["w"] - 12)
            p.drawText(QRectF(x + 6, y + 22, n["w"] - 12, n["h"] - 24),
                       Qt.AlignLeft | Qt.AlignVCenter, elide)
        p.end()

    def mousePressEvent(self, event):
        # 点中节点即选中，发信号给右侧参数面板
        W, H = self.width(), self.height()
        for n in self.nodes:
            rect = QRectF(n["x"] * W, n["y"] * H, n["w"], n["h"])
            if rect.contains(event.position()):
                self.selected = n["name"]
                self.node_selected.emit(n["name"], n["type"])
                self.update()
                return

    node_selected = Signal(str, str)


# ======================================================================
#  模块面板主体
# ======================================================================
#: 单次后台枚举的最长等待（毫秒）。枚举链路里有同步 USB open（与
#: device_panel 同一实现，见 mbdsdr_ai/sdr_backend.py），没有超时参数；
#: 设备/驱动卡死时任务永不返回，``finished`` 也就永不发射——本面板的
#: ``_enum_inflight`` 会永久为 True、刷新按钮永久禁用，用户只能重启程序。
#: 5 s 覆盖本机实测最慢 2.4 s，只在真卡住时触发（与 device_panel 同一上限）。
ENUM_TIMEOUT_MS = 5000


class _ModuleEnumSignals(QObject):
    """跨线程设备枚举结果（不挂父对象，生命周期由在跑的任务持有）。"""

    #: (devices | None, error_text | None, token)。token 用于丢弃超时后迟到
    #: 的结果，避免它清掉下一轮枚举的 inflight（见 ModulePanel._on_enum_timeout）。
    finished = Signal(object, object, int)


class _ModuleEnumTask(QRunnable):
    """后台枚举 SDR 设备，避免 GUI 线程付 1 s 枚举成本。"""

    def __init__(self, signals: _ModuleEnumSignals, token: int = 0):
        super().__init__()
        self._signals = signals
        self._token = int(token)

    def run(self):  # noqa: D102 - Qt 接口
        try:
            from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
            devices, err = list(enumerate_all_sdr_devices()), None
        except Exception as exc:  # noqa: BLE001 - 失败必须回传，不静默
            devices, err = None, _clean_exc(exc)
        try:
            self._signals.finished.emit(devices, err, self._token)
        except RuntimeError:
            pass  # 面板已销毁，结果无人接收


class ModulePanel(QWidget):
    """SDR++ 式模块面板。对外只发信号，不直接碰硬件（与 control_panel 解耦）。"""

    device_selected = Signal(dict)   # 选中一个真实设备 dict
    tune_requested = Signal(float)   # 改了频率
    demod_mode_changed = Signal(str) # 改了解调模式
    sink_changed = Signal(str)
    node_selected = Signal(str, str)  # 信号流图节点被点选 (name, type)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._devices: List[Dict] = []
        #: 与 _devices 同序的身份结论（key/quality/warning）。
        self._identities: List = []
        # 连接/调谐的真实状态：只有后端回执过的值才允许出现在控件里。
        self._sdr_connected = False
        # 模块面板调谐是否已接入后端（_apply_hw_action）。未接入时频率控件禁用，
        # 绝不发"看起来生效"的假调谐。
        self._tune_wired = False
        self._confirmed_freq_hz: Optional[float] = None
        self._confirmed_gain_db: Optional[float] = None
        self._enum_signals = _ModuleEnumSignals()
        self._enum_signals.finished.connect(self._on_devices_enumerated)
        self._enum_inflight = False
        #: 本轮枚举的 token；超时/完成后自增，迟到结果按 token 丢弃。
        self._enum_token = 0
        #: 枚举看门狗：USB open 无超时，卡死时 finished 永不发射，
        #: 没有它刷新按钮会永久禁用（见 ENUM_TIMEOUT_MS 处的说明）。
        self._enum_watchdog = QTimer(self)
        self._enum_watchdog.setSingleShot(True)
        self._enum_watchdog.timeout.connect(self._on_enum_timeout)
        self._build_ui()
        _req_store.subscribe(self._on_request_changed)
        # 设备枚举放到后台：实测本机 enumerate_all_sdr_devices 1.03 s
        # （含 rtlsdr_open + 0.5 s 网络探测），压在 GUI 线程里首帧得等它。
        self.refresh_devices_async()

    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)

        # ===== 左：源设备选择 =====
        left = QGroupBox("源设备 (Source)")
        lv = QVBoxLayout(left)
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(180)
        # 每次刷新都 clear() 后重建设备名，文案长短由枚举结果决定；
        # 默认 AdjustToContentsOnFirstShow 会把首帧的「未发现SDR设备」/长设备名
        # 冻进最小宽（实测 128 vs 清成短名后的 95）。改稳定策略。
        self.device_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.device_combo.setMinimumContentsLength(16)
        self.device_combo.currentIndexChanged.connect(self._on_device_selected)
        lv.addWidget(self.device_combo)
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh_devices_async)
        btn_row.addWidget(self.refresh_btn)
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self._on_connect)
        btn_row.addWidget(self.connect_btn)
        lv.addLayout(btn_row)
        self.device_info = QLabel("未枚举设备")
        self.device_info.setStyleSheet(f"color:{MUTED.name()};")
        self.device_info.setWordWrap(True)
        lv.addWidget(self.device_info)
        lv.addStretch()

        # 解调模式选择（SDR++ Radio 模块的 demod 选择）
        lv.addWidget(QLabel("解调模式"))
        self.mode_combo = QComboBox()
        # 解调模式显示名来自 mode_registry；本面板只暴露模拟语音/广播子集，
        # 顺序保持原 UI（WFM/NFM/AM/USB/LSB），未注册的模式自动剔除。
        self.mode_combo.addItems(
            [_get_mode(m).display_name for m in ["WFM", "NFM", "AM", "USB", "LSB"]
             if _get_mode(m)])
        self.mode_combo.currentTextChanged.connect(
            lambda t: self.demod_mode_changed.emit(t))
        lv.addWidget(self.mode_combo)

        row.addWidget(left, 2)

        # ===== 中：信号流画布 =====
        self.flow = FlowCanvas()
        self.flow.node_selected.connect(self._on_node_selected)
        row.addWidget(self.flow, 4)

        # ===== 右：参数面板 =====
        right = QGroupBox("参数")
        form = QFormLayout(right)
        self.freq_spin = QDoubleSpinBox()
        # MHz 人类量纲显示（七位数 Hz 不友好，settings-audit 七-3 销账）；
        # tune_requested 信号仍是 Hz——量纲换算只在控件边界做，消费方不动。
        self.freq_spin.setRange(FREQ_MIN_HZ / 1e6, FREQ_MAX_HZ / 1e6)
        self.freq_spin.setValue(100.0)
        self.freq_spin.setSuffix(" MHz")
        self.freq_spin.setDecimals(4)   # 0.0001 MHz = 100 Hz 步进
        self.freq_spin.setToolTip(
            "设备中心频率（MHz）；改完立即生效，实际可调范围看左侧设备信息。"
            "当前调谐未接入后端时控件禁用，显示值不是设备读数")
        self.freq_spin.valueChanged.connect(
            lambda v: self.tune_requested.emit(round(v * 1e6)))
        form.addRow("频率", self.freq_spin)

        self.gain_slider = QSlider(Qt.Horizontal)
        # 未连接时的兜底范围；连接后 main_window 调 set_gain_range() 按设备能力收放。
        self.gain_slider.setRange(0, 50)
        self.gain_slider.setValue(20)
        self.gain_slider.setToolTip(
            "增益（dB）。未连接时无真实读数（显示「—」且禁用）；连接后随所连"
            "设备能力动态收放，实际档位以设备为准（设备不支持的值由后端钳位）")
        self.gain_val = QLabel("—")
        self.gain_slider.valueChanged.connect(
            lambda v: self.gain_val.setText(_format_gain_db(v)))
        self.gain_slider.valueChanged.connect(self._push_gain)
        grow = QHBoxLayout()
        grow.addWidget(self.gain_slider)
        grow.addWidget(self.gain_val)
        gw = QWidget(); gw.setLayout(grow)
        form.addRow("增益", gw)

        self.sr_combo = QComboBox()
        # 档位来自所选设备能力（与主窗口 _fill_sr_combo 共用
        # constants.sample_rate_combo_items）；不持有前端静态档位表。
        # 未连接/能力未知时只显示单项「未知」说明并置灰（不静默列全表）。
        # 该下拉在连接/换设备时 clear() 重建；默认策略会把首帧的长「未知…」
        # 说明冻进最小宽（实测 333 vs 短档位 93），与 preset_combo 同形态。
        self.sr_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sr_combo.setMinimumContentsLength(16)
        self.sr_combo.setToolTip(
            "设备未报告采样率范围/来源时，下拉只有「档位未知，连接设备后探测」"
            "说明项并置灰；连接时按默认采样率兜底，连上后以控制面板的设备"
            "回执档位为准。")
        self._fill_sample_rate_options(None)
        self.sr_combo.currentIndexChanged.connect(self._push_sample_rate)
        form.addRow("采样率", self.sr_combo)

        self.bw_spin = QDoubleSpinBox()
        self.bw_spin.setRange(0, 250000)
        self.bw_spin.setSpecialValueText("自动")
        self.bw_spin.setValue(150000)
        self.bw_spin.setSuffix(" Hz")
        self.bw_spin.setToolTip(
            "设备射频带宽（硬件滤波），0 = 自动（跟随后端默认）。"
            "注意与解调带宽（控制面板「解调带宽」）不同")
        form.addRow("硬件带宽", self.bw_spin)
        self.bw_spin.valueChanged.connect(self._push_bandwidth)

        # 参数区的诚实说明：为什么禁用、哪个值不是真实读数。
        self.params_hint = QLabel()
        self.params_hint.setStyleSheet(f"color:{MUTED.name()};")
        self.params_hint.setWordWrap(True)
        form.addRow(self.params_hint)
        row.addWidget(right, 2)

        # ===== 底：sink 选择（跨整行）=====
        bottom = QFrame()
        bh = QHBoxLayout(bottom)
        bh.addWidget(QLabel("Sink:"))
        self.sink_combo = QComboBox()
        # 对应 sink_modules/audio_sink | recorder(file) | network_sink
        self.sink_combo.addItems(["Audio (扬声器)", "File (IQ 落盘)", "Network (UDP)"])
        self.sink_combo.setToolTip(
            "当前仅记录选择：后端 sink 切换尚未接入，不会改变音频/落盘/UDP 输出")
        self.sink_combo.setEnabled(False)
        self.sink_combo.currentTextChanged.connect(
            lambda t: self.sink_changed.emit(t))
        bh.addWidget(self.sink_combo)
        self.sink_note = QLabel("仅记录（未接入后端 sink 切换）")
        self.sink_note.setStyleSheet(f"color:{MUTED.name()};")
        bh.addWidget(self.sink_note)
        bh.addStretch()

        outer.addLayout(row, 1)
        outer.addWidget(bottom)

        self._refresh_flow()
        self.set_sdr_connected(False)

    # ------------------------------------------------------------------
    # 真实状态回填（连接状态 / 设备回执的频率与增益）
    # ------------------------------------------------------------------
    def set_sdr_connected(self, connected: bool, *, tune_wired: bool = False) -> None:
        """把连接状态同步到参数区。

        ``tune_wired`` 只有主窗口把 ``tune_requested`` 接到真实后端
        （``_apply_hw_action``）后才允许传 True；未接入时频率控件保持禁用，
        避免「拖了频率、界面变了、硬件没动」的假调谐。
        """
        self._sdr_connected = bool(connected)
        self._tune_wired = bool(connected and tune_wired)
        self.freq_spin.setEnabled(self._tune_wired)
        self.gain_slider.setEnabled(self._sdr_connected)
        if not self._sdr_connected:
            self.gain_val.setText("—")
            self.params_hint.setText(
                "设备未连接：频率/增益没有真实读数，控件禁用（不显示假 0 / 假 20 dB）")
            return
        if self._confirmed_gain_db is None:
            self.gain_val.setText("—")
        else:
            self.gain_val.setText(_format_gain_db(self._confirmed_gain_db))
        if self._tune_wired:
            self.params_hint.setText(
                "频率已接入后端：改频率走真实调谐（失败会回滚到上次成功值）")
        else:
            self.params_hint.setText(
                "频率不可调：模块面板调谐尚未接入后端（已禁用，避免假调谐）；"
                "增益滑块只在连接时随设备下发")

    def set_gain_applied(self, db: float) -> bool:
        """回填设备真实增益读数（只改标签，不动滑杆）。

        与 :meth:`ControlPanel.set_gain_applied` 同口径：滑杆是配置意图，
        标签是设备读数。``main_window._show_real_gain`` 在写入成功/失败后
        都要读回真值；此前只回写了控制面板，模块面板标签一直停在「—」，
        同一台设备在两个面板上给出两个读数。
        """
        if not self._sdr_connected:
            return False
        try:
            value = float(db)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(value):
            return False
        self._confirmed_gain_db = value
        self.gain_val.setText(_format_gain_db(value))
        # 与控制面板/状态面板同一条底线：后端夹值或写入失败时必须告警，
        # 文案也来自 readouts 的同一份实现（此前三处各写一句）。
        requested = float(self.gain_slider.value())
        if _gain_differs(value, requested):
            hint = _gain_not_applied_hint(value, requested)
            logger.warning("%s", hint)
            self.gain_val.setToolTip(f"⚠ {hint}")
        return True

    def set_rf_params(self, freq_hz: Optional[float] = None,
                      gain_db: Optional[float] = None) -> None:
        """用设备真实回执的频率/增益回填（不发 tune_requested，避免回环调谐）。"""
        if freq_hz is not None:
            try:
                value_hz = float(freq_hz)
            except (TypeError, ValueError):
                value_hz = 0.0
            if value_hz > 0:
                self._confirmed_freq_hz = value_hz
                blocker = QSignalBlocker(self.freq_spin)
                self.freq_spin.setValue(value_hz / 1e6)
                del blocker
        if gain_db is not None:
            try:
                value_db = float(gain_db)
            except (TypeError, ValueError):
                value_db = None
            if value_db is not None:
                self._confirmed_gain_db = value_db
                blocker = QSignalBlocker(self.gain_slider)
                self.gain_slider.setValue(int(round(value_db)))
                del blocker
                self.gain_val.setText(_format_gain_db(value_db))

    # ------------------------------------------------------------------
    def refresh_devices(self):
        """同步枚举 SDR 设备（同步调用入口，测试与显式请求用）。

        对应 SDR++ soapy_source refresh()（main.cpp:83-99）：空列表就只画 Refresh。
        启动与「刷新」按钮走 ``refresh_devices_async``，避免 1 s 枚举压在 GUI 线程。
        """
        try:
            from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
            self._devices = enumerate_all_sdr_devices()
            enum_err = None
        except Exception as e:  # noqa: BLE001 - 枚举失败要如实回传
            self._devices = []
            enum_err = _clean_exc(e)

        self._apply_devices(self._devices, enum_err)

    def refresh_devices_async(self):
        """派发一次后台设备枚举（本函数只做派发，不等结果）。

        真实结果在 ``_on_devices_enumerated`` 里落到界面上；枚举失败与"没插设备"
        仍然是两件事，分别如实显示（旧实现先 setText(异常) 再被空态文案覆盖）。
        """
        if self._enum_inflight:
            return  # 上一拍还没回来，不堆积任务
        self._enum_inflight = True
        self._enum_token += 1
        self._enum_watchdog.start(ENUM_TIMEOUT_MS)
        self.refresh_btn.setEnabled(False)
        self.device_info.setText("正在枚举设备…")
        if self.device_combo.count() == 0:
            self.device_combo.addItem("正在枚举设备…")
        QThreadPool.globalInstance().start(
            _ModuleEnumTask(self._enum_signals, self._enum_token))

    def _on_devices_enumerated(self, devices, enum_err, token: int = 0):
        """后台枚举回来了：在 GUI 线程里更新下拉框与提示。"""
        if token != self._enum_token:
            return  # 超时后迟到的旧结果：不得清掉新一轮的 inflight
        self._enum_inflight = False
        self._enum_watchdog.stop()
        self.refresh_btn.setEnabled(True)
        self._apply_devices(devices, enum_err)

    def _on_enum_timeout(self) -> None:
        """枚举超过上限仍未返回：必须让界面离开「正在枚举」并能重试。

        枚举没有可判定完成之外的手段（同步 USB open 不返回就是卡死），
        因此这里用看门狗兜底；它不修改任何设备结论——只把界面从
        「正在枚举」挪到「超时/未确认」，并保留上一份可用列表。
        """
        if not self._enum_inflight:
            return
        self._enum_inflight = False
        self._enum_token += 1  # 迟到结果作废
        logger.warning(
            "模块面板设备枚举超时（>%.1fs）：enumerate_all_sdr_devices 未返回，"
            "已复位 inflight，界面转为「超时/未确认」并允许重试",
            ENUM_TIMEOUT_MS / 1000.0)
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新")
        if not self._devices:
            # 上一份列表为空：下拉里可能只剩「正在枚举设备…」占位项，
            # 必须换成可判断的状态，否则用户看到的是一个永远在转的面板。
            self.device_combo.clear()
            self.device_combo.addItem("设备枚举超时（未确认有无设备）")
            self.device_info.setText(
                f"设备枚举超时：{ENUM_TIMEOUT_MS / 1000.0:.0f} 秒内没拿到设备列表。\n"
                "这不等于没插设备（USB 打开卡住时同样会超时）。\n"
                "现在可以：点「刷新」重试；反复超时就拔插设备后重启程序。")
            self.connect_btn.setEnabled(False)
        else:
            # 上一份列表仍然可用：保留设备项，只说明这次刷新没成功，
            # 不把旧列表悄悄当成最新结论。
            self.device_info.setText(
                f"刷新超时：仍显示上次枚举到的 {len(self._devices)} 台设备"
                "（插拔过设备的话，这份列表可能已过期）。\n"
                "现在可以：点「刷新」重试；连接前先确认列表与手上的设备一致。")

    def _apply_devices(self, devices, enum_err=None):
        """把一份设备列表落到界面（同步/异步/测试共用同一套显示规则）。"""
        self._devices = list(devices) if devices else []
        # 身份键与设备列表同序：撞键/无序列号时 resolve 会显式告警。
        self._identities = _req_store.resolve(self._devices)

        self.device_combo.clear()
        if not self._devices:
            # 红线：无设备不造假，如实显示
            self.device_combo.addItem("未检测到 SDR 设备")
            if enum_err is not None:
                # 枚举失败 ≠ 没插设备：错误信息不得被空态文案覆盖
                # （旧实现先 setText(异常) 再被"未发现SDR设备"盖掉，失败被吞）
                self.device_info.setText(
                    f"设备枚举失败: {enum_err}\n（这不等于没插设备）")
            else:
                self.device_info.setText(
                    "未检测到 SDR 设备\n插上 RTL-SDR/HackRF 后点刷新。")
            self.connect_btn.setEnabled(False)
            self._fill_sample_rate_options(None)
        else:
            self.connect_btn.setEnabled(True)
            for d in self._devices:
                label = d.get("label") or d.get("driver", "?")
                self.device_combo.addItem(label)
            d0 = self._devices[0]
            self._show_device_info(d0)
            self._fill_sample_rate_options(d0)
            self._render_request(0)

        # 设备列表没变化时界面只会"纹丝不动"；给刷新按钮一个明确的动作反馈，
        # 避免用户以为按钮坏了（本次交互审计发现的死按钮）。
        self.refresh_btn.setText("已刷新")
        QTimer.singleShot(1200, self.refresh_btn,
                          lambda: self.refresh_btn.setText("刷新"))

    def _show_device_info(self, d: Dict) -> None:
        """只显示设备真实报出的能力；未知项写「未知」，绝不填量程数字。"""
        try:
            from mbdsdr_ai.device_capabilities import summarize_device
            summary = summarize_device(d)
        except Exception as e:
            summary = f"能力未知（能力表不可用：{e}）"
            logger.warning("设备能力摘要生成失败：%s", e)
        self.device_info.setText(f"{d.get('driver', '?')}\n{summary}")

    def _on_device_selected(self, idx: int) -> None:
        if 0 <= idx < len(self._devices):
            self._show_device_info(self._devices[idx])
            self._fill_sample_rate_options(self._devices[idx])
            self._render_request(idx)

    # ------------------------------------------------------ 连接请求（唯一存储）
    def _current_identity(self):
        """当前选中设备的身份结论；无设备时返回 None。"""
        # 设备列表被直接替换（测试/异步枚举竞态）时补一次身份解析，
        # 避免把“还没跑 resolve”误判成“没有身份”。
        if len(self._identities) != len(self._devices):
            self._identities = _req_store.resolve(self._devices)
        idx = self.device_combo.currentIndex()
        if 0 <= idx < len(self._identities):
            return self._identities[idx]
        return None

    def _render_request(self, idx: int) -> None:
        """按唯一请求存储渲染采样率/增益/带宽，不再使用面板自持副本。"""
        if not (0 <= idx < len(self._identities)):
            return
        ident = self._identities[idx]
        req = _req_store.request_for(ident.key)

        if req.sample_rate_hz is not None and not self.sr_combo.hasFocus():
            target = float(req.sample_rate_hz)
            for i in range(self.sr_combo.count()):
                data = self.sr_combo.itemData(i)
                if data is not None and float(data) == target:
                    self.sr_combo.blockSignals(True)
                    self.sr_combo.setCurrentIndex(i)
                    self.sr_combo.blockSignals(False)
                    break

        if req.gain_db is not None and not self.gain_slider.isSliderDown():
            value = max(self.gain_slider.minimum(),
                        min(self.gain_slider.maximum(), int(round(req.gain_db))))
            self.gain_slider.blockSignals(True)
            self.gain_slider.setValue(value)
            self.gain_slider.blockSignals(False)
            self.gain_val.setText(_format_gain_db(value))

        if req.bandwidth_hz is not None and not self.bw_spin.hasFocus():
            self.bw_spin.blockSignals(True)
            self.bw_spin.setValue(float(req.bandwidth_hz))
            self.bw_spin.blockSignals(False)

        if ident.warning:
            # 身份不可靠必须在界面上说出来，不许静默共用/错位；
            # 能力摘要仍保留在前，告警另起一行。
            self._show_device_info(self._devices[idx])
            self.device_info.setText(
                f"{self.device_info.text()}\n⚠ {ident.warning}")

    def _push_sample_rate(self, _idx: int = -1) -> None:
        ident = self._current_identity()
        if ident is None:
            return
        data = self.sr_combo.currentData()
        if data is not None:
            _req_store.update(ident.key, sample_rate_hz=float(data))

    def _push_gain(self, value: int) -> None:
        ident = self._current_identity()
        if ident is not None:
            _req_store.update(ident.key, gain_db=float(value))

    def _push_bandwidth(self, value: float) -> None:
        ident = self._current_identity()
        if ident is not None:
            _req_store.update(ident.key, bandwidth_hz=float(value))

    def _on_request_changed(self, key: str) -> None:
        """另一面板改了同一台设备的请求 → 这边同步渲染。"""
        ident = self._current_identity()
        if ident is None or ident.key != key:
            return
        self._render_request(self.device_combo.currentIndex())

    def _fill_sample_rate_options(self, device) -> None:
        """按设备能力重填采样率下拉（单一真值源见 constants 同名函数）。

        能力已知 → 可选项来自后端离散表 ∩ 设备量程，可正常选择；
        能力未知（未连接/驱动未报告范围）→ 只保留一项显式说明，data=None，
        应用侧按 DEFAULT_SAMPLE_RATE_HZ 兜底，控件置灰避免「选了不生效」。
        """
        items = sample_rate_combo_items(device)
        self.sr_combo.blockSignals(True)
        self.sr_combo.clear()
        for text, hz in items:
            self.sr_combo.addItem(text, hz)
        self.sr_combo.setCurrentIndex(default_sample_rate_combo_index(items))
        self.sr_combo.blockSignals(False)
        known = any(hz is not None for _text, hz in items)
        self.sr_combo.setEnabled(known)
        self.sr_combo.setToolTip(
            "可设采样率来自所连设备能力（后端离散表 ∩ 设备量程）。"
            f"未报告范围时置灰，连接按 {DEFAULT_SR_UNCONFIRMED} 兜底。"
            if not known else
            "可设采样率来自所选设备的真实能力；连接时按此档下发，"
            "实际生效值以设备回执为准。")

    def selected_sample_rate_hz(self) -> float:
        """当前采样率下拉档位（Hz）。

        供连接时下发 + 测试断言。itemData 缺失/异常时的兜底口径由
        ``constants.connect_sample_rate_hz`` 唯一决定（不再在本面板写一遍默认）。
        """
        return connect_sample_rate_hz(self.sr_combo.currentData())

    def selected_gain_db(self) -> float:
        """当前增益滑块值（dB），语义与 device_panel.gain_edit 一致。"""
        return float(self.gain_slider.value())

    def set_gain_range(self, lo_db: float, hi_db: float):
        """按所连设备能力动态收放增益滑杆范围（连接后由 main_window 调用）。

        非法/退化范围（hi<=lo）如实拒绝不动，保留当前范围。
        """
        try:
            lo, hi = int(round(lo_db)), int(round(hi_db))
        except (TypeError, ValueError):
            return
        if hi <= lo:
            return
        self.gain_slider.setRange(lo, hi)
        if self.gain_slider.value() > hi:
            self.gain_slider.setValue(hi)
        self.gain_slider.setToolTip(
            f"增益 {lo}–{hi} dB（按所连设备能力动态收放；"
            "实际档位以设备为准，设备不支持的值由后端钳位）")

    def _on_connect(self):
        idx = self.device_combo.currentIndex()
        if not self._devices or idx >= len(self._devices):
            return
        # 面板参数随设备一起下发；参数先写回唯一请求存储，载荷由
        # fill_payload 统一生成（设备面板读到的是同一份，不再各写一份）。
        dev = dict(self._devices[idx])
        try:
            bandwidth = float(self.bw_spin.value())
        except (TypeError, ValueError):
            bandwidth = 0.0  # 0=自动（与后端 bandwidth_hz 语义一致）
        ident = self._current_identity()
        if ident is None:
            # 身份键拿不到就无法保证参数归属：拒绝连接并说明原因，
            # 绝不退回“各写一份副本”的老路（那正是本次要消灭的静默错值）。
            self.device_info.setText("无法确定设备身份，未发起连接")
            logger.warning("设备身份键不可用，拒绝连接：%r",
                           dev.get("label") or dev.get("driver"))
            return
        _req_store.update(ident.key,
                          sample_rate_hz=self.selected_sample_rate_hz(),
                          gain_db=self.selected_gain_db(),
                          bandwidth_hz=bandwidth)
        _req_store.fill_payload(dev, ident.key)
        self.device_selected.emit(dev)
        QMessageBox.information(self, "已选择设备",
                                f"{dev.get('label')}\n（真实连接由后端完成）")

    def _on_node_selected(self, name: str, ntype: str):
        self.flow.selected = name
        self.device_info.setText(f"选中模块:\n{name}\n类型: {ntype}")
        self.node_selected.emit(name, ntype)

    def _refresh_flow(self):
        """画一张默认信号流：Source → WFM → Audio Sink。"""
        mods = [
            {"name": "Source", "type": "source"},
            {"name": "WFM Demod", "type": "processing"},
            {"name": "RDS", "type": "decoder"},
            {"name": "Audio Sink", "type": "sink"},
        ]
        edges = [("Source", "WFM Demod"), ("WFM Demod", "RDS"),
                 ("WFM Demod", "Audio Sink")]
        self.flow.set_graph(mods, edges)


if __name__ == "__main__":
    # 独立冒烟：无设备也应显示"未发现SDR设备"且不崩
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = ModulePanel()
    w.resize(900, 360)
    w.show()
    print("ModulePanel launched; devices found:", len(w._devices))
    sys.exit(app.exec())

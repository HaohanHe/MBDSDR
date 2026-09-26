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

配色（低饱和浅色默认主题）：
  纸底 #F5F3EF / 青灰 #5B7B8C / 赭石 #C4845C
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

# 允许从 desktop/ 直接跑，也允许被 main_window import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath, QLinearGradient,
)
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFormLayout, QComboBox, QPushButton,
    QLabel, QDoubleSpinBox, QSlider, QGroupBox, QMessageBox, QFrame,
)

# 低饱和浅色配色（默认主题）
PAPER = QColor("#F5F3EF")     # 纸底
STEEL = QColor("#5B7B8C")     # 青灰（主结构/连线）
OCHRE = QColor("#C4845C")     # 赭石（强调/选中/sink）
INK = QColor("#3A3A3A")
MUTED = QColor("#9A9A9A")

# 模块节点按类型上色（对应 SDR++ source/decoder/sink 三类模块目录）
_TYPE_COLORS = {
    "source": QColor("#7E9BA8"),     # 偏青：源
    "processing": STEEL,             # 青灰：解调
    "decoder": QColor("#8FA88E"),    # 偏绿：解码
    "sink": OCHRE,                   # 赭石：sink
}


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
            p.setPen(MUTED)
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
            elide = n["name"]
            if len(elide) > 16:
                elide = elide[:15] + "…"
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
class ModulePanel(QWidget):
    """SDR++ 式模块面板。对外只发信号，不直接碰硬件（与 control_panel 解耦）。"""

    device_selected = Signal(dict)   # 选中一个真实设备 dict
    tune_requested = Signal(float)   # 改了频率
    demod_mode_changed = Signal(str) # 改了解调模式
    sink_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._devices: List[Dict] = []
        self._build_ui()
        self.refresh_devices()

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
        lv.addWidget(self.device_combo)
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh_devices)
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
        self.mode_combo.addItems(["WFM 广播", "NFM 窄带", "AM", "USB", "LSB"])
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
        self.freq_spin.setRange(1e3, 7.2e9)
        self.freq_spin.setValue(100.0e6)
        self.freq_spin.setSuffix(" Hz")
        self.freq_spin.setDecimals(0)
        self.freq_spin.valueChanged.connect(
            lambda v: self.tune_requested.emit(v))
        form.addRow("频率", self.freq_spin)

        self.gain_slider = QSlider(Qt.Horizontal)
        self.gain_slider.setRange(0, 50)
        self.gain_slider.setValue(20)
        self.gain_val = QLabel("20 dB")
        self.gain_slider.valueChanged.connect(
            lambda v: self.gain_val.setText(f"{v} dB"))
        grow = QHBoxLayout()
        grow.addWidget(self.gain_slider)
        grow.addWidget(self.gain_val)
        gw = QWidget(); gw.setLayout(grow)
        form.addRow("增益", gw)

        self.sr_combo = QComboBox()
        self.sr_combo.addItems(["1.024 MHz", "1.44 MHz", "2.048 MHz", "2.4 MHz", "3.2 MHz"])
        form.addRow("采样率", self.sr_combo)

        self.bw_spin = QDoubleSpinBox()
        self.bw_spin.setRange(100, 250000)
        self.bw_spin.setValue(150000)
        self.bw_spin.setSuffix(" Hz")
        form.addRow("带宽", self.bw_spin)
        row.addWidget(right, 2)

        # ===== 底：sink 选择（跨整行）=====
        bottom = QFrame()
        bh = QHBoxLayout(bottom)
        bh.addWidget(QLabel("Sink:"))
        self.sink_combo = QComboBox()
        # 对应 sink_modules/audio_sink | recorder(file) | network_sink
        self.sink_combo.addItems(["Audio (扬声器)", "File (IQ 落盘)", "Network (UDP)"])
        self.sink_combo.currentTextChanged.connect(
            lambda t: self.sink_changed.emit(t))
        bh.addWidget(self.sink_combo)
        bh.addStretch()

        outer.addLayout(row, 1)
        outer.addWidget(bottom)

        self._refresh_flow()

    # ------------------------------------------------------------------
    def refresh_devices(self):
        """真实枚举 SDR 设备。无设备 → 下拉显示'未发现SDR设备'。

        对应 SDR++ soapy_source refresh()（main.cpp:83-99）：
          devList = SoapySDR::Device::enumerate(); 空就只画 Refresh。
        """
        try:
            from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
            self._devices = enumerate_all_sdr_devices()
        except Exception as e:
            self._devices = []
            self.device_info.setText(f"枚举异常: {e}")

        self.device_combo.clear()
        if not self._devices:
            # 红线：无设备不造假，如实显示
            self.device_combo.addItem("未发现SDR设备")
            self.device_info.setText(
                "未发现SDR设备\n插上 RTL-SDR/HackRF 后点刷新。")
            self.connect_btn.setEnabled(False)
        else:
            self.connect_btn.setEnabled(True)
            for d in self._devices:
                label = d.get("label") or d.get("driver", "?")
                self.device_combo.addItem(label)
            d0 = self._devices[0]
            self.device_info.setText(
                f"{d0.get('driver','?')}\n"
                f"频率 {d0.get('freq_range','?')}\n"
                f"采样率 {d0.get('sample_rate_range','?')}")

    def _on_connect(self):
        idx = self.device_combo.currentIndex()
        if not self._devices or idx >= len(self._devices):
            return
        dev = self._devices[idx]
        self.device_selected.emit(dev)
        QMessageBox.information(self, "已选择设备",
                                f"{dev.get('label')}\n（真实连接由后端完成）")

    def _on_node_selected(self, name: str, ntype: str):
        self.flow.selected = name
        self.device_info.setText(f"选中模块:\n{name}\n类型: {ntype}")

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

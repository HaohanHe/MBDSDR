"""
MBDSDR 桌面端 - 气象卫星云图面板 (WeatherPanel)
==================================================

接收/解码气象卫星下行信号并显示云图：
  - 卫星选择：GK-2A / FY-4A / FY-4B / FY-3D/E/F / GOES-16 / NOAA-19
  - 接收模式：离线 IQ 文件 (.iq/.wav/.npy) 或 实时 SDR（无硬件置灰）
  - 解码：在 QThread 中调用后端 ToolRegistry 工具，完成后发信号刷新 QLabel
  - 图像处理：中值滤波 / 直方图均衡 / 白平衡 / Kuwahara 降噪 → sat_image_enhance
  - 状态栏：卫星参数（频率/符号率/调制）、解码进度、当前帧计数
  - 合成演示：[模拟]生成演示云图（标 [模拟] 标签）

配色（日式低饱和，全部取自 themes.py）：
  纸底 #F5F3EF / 蓝灰 #5B7B8C / 橙 #C4845C
不硬编码新颜色；matplotlib/占位文字统一从 ThemeManager 取色。

红线：无真实 SDR 硬件时，实时 SDR 模式按钮置灰并显示「未连接SDR设备」；
     合成/离线数据一律标 [模拟]。
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Dict, List, Optional

# 允许 desktop/ 直接跑，也允许被 main_window import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QPixmap, QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QPushButton,
    QLabel, QCheckBox, QFrame, QFileDialog, QGroupBox, QMessageBox,
    QScrollArea,
)

from themes import get_theme, DEFAULT_THEME


# ----------------------------------------------------------------------
# 卫星参数表（显示用：频率/符号率/调制；对应后端解码族）
# ----------------------------------------------------------------------
# decode 族：
#   gk2a   -> gk2a_lrit_decode   (complex64 raw IQ -> PNG)
#   fy4    -> fy4_lrit_decode    (VCDU hex 列表)
#   fy3    -> fy3_hrpt_decode    (软比特数组)
#   goes   -> goes_lrit_decode
#   noaa   -> noaa_apt
SATELLITES: Dict[str, Dict] = {
    "GK-2A (128.2°E)": {
        "freq_mhz": 1686.0, "symrate_ksps": 128.0, "mod": "BPSK",
        "family": "gk2a", "band": "L 波段 HRIT",
    },
    "FY-4A (104.7°E)": {
        "freq_mhz": 1680.0, "symrate_ksps": 720.0, "mod": "QPSK",
        "family": "fy4", "band": "L 波段 HRIT",
    },
    "FY-4B (133°E)": {
        "freq_mhz": 1680.0, "symrate_ksps": 720.0, "mod": "QPSK",
        "family": "fy4", "band": "L 波段 HRIT",
    },
    "FY-3D": {
        "freq_mhz": 1700.0, "symrate_ksps": 665.4, "mod": "BPSK",
        "family": "fy3", "band": "L 波段 HRPT",
    },
    "FY-3E": {
        "freq_mhz": 1700.0, "symrate_ksps": 665.4, "mod": "BPSK",
        "family": "fy3", "band": "L 波段 HRPT",
    },
    "FY-3F": {
        "freq_mhz": 1700.0, "symrate_ksps": 665.4, "mod": "BPSK",
        "family": "fy3", "band": "L 波段 HRPT",
    },
    "GOES-16": {
        "freq_mhz": 1686.6, "symrate_ksps": 622.0, "mod": "BPSK",
        "family": "goes", "band": "L 波段 HRIT",
    },
    "NOAA-19": {
        "freq_mhz": 137.1, "symrate_ksps": 2.4, "mod": "APT/AM",
        "family": "noaa", "band": "VHF APT",
    },
}

# 增强选项 -> sat_image_enhance steps op 名
ENHANCE_OPS = {
    "中值滤波": "median",
    "直方图均衡": "equalize",
    "白平衡": "white_balance",
    "Kuwahara 降噪": "kuwahara",
}


def _theme_colors() -> Dict[str, str]:
    """从 themes.py 取当前默认主题配色（不硬编码）。"""
    return get_theme(DEFAULT_THEME).colors


# ======================================================================
# 后台解码线程：调用 ToolRegistry，不阻塞 UI
# ======================================================================
class DecodeWorker(QObject):
    """在 QThread 中跑后端解码/增强/合成，完成后发信号。"""

    finished = Signal(object)   # ToolResult 或 dict（合成结果）
    progress = Signal(str)      # 进度文本
    failed = Signal(str)

    def __init__(self, action: str, params: Dict, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._action = action       # "decode" | "enhance" | "demo"
        self._params = params
        self._reg = None
        self._stop = False

    def request_stop(self):
        self._stop = True

    def _ensure_registry(self):
        if self._reg is None:
            from mbdsdr_ai.tool_registry import ToolRegistry
            self._reg = ToolRegistry()
            self._reg.register_builtin_tools()
        return self._reg

    def run(self):
        try:
            if self._action == "decode":
                self._run_decode()
            elif self._action == "enhance":
                self._run_enhance()
            elif self._action == "demo":
                self._run_demo()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}")

    # ------------------------------------------------------------------
    def _run_decode(self):
        reg = self._ensure_registry()
        family = self._params["family"]
        iq_file = self._params.get("iq_file")
        self.progress.emit(f"开始解码 {self._params.get('satellite','?')} ...")

        if family == "gk2a":
            out_png = self._params["out_png"]
            res = reg.call("gk2a_lrit_decode",
                           {"iq_path": iq_file, "out_png": out_png})
        elif family == "fy4":
            # fy4_lrit_decode 需要 VCDU hex 列表；离线 IQ 文件需先经解帧。
            # 这里如实调用并把后端返回（含参数要求）透传给 UI。
            res = reg.call("fy4_lrit_decode", {"iq_file": iq_file})
        elif family == "fy3":
            res = reg.call("fy3_hrpt_decode", {"iq_file": iq_file})
        else:
            # GOES / NOAA：离线 IQ 直解暂未接线，如实返回提示
            res = type("R", (), {
                "success": False,
                "content": f"{family} 离线 IQ 直解通道暂未接线，请先用离线工具链导出。",
                "data": {}, "error": "not_wired"})()
        self.progress.emit("解码完成" if getattr(res, "success", False) else "解码失败")
        self.finished.emit(res)

    def _run_enhance(self):
        reg = self._ensure_registry()
        steps = self._params["steps"]
        out_png = self._params["out_png"]
        res = reg.call("sat_image_enhance", {
            "image_path": self._params["image_path"],
            "steps": steps,
            "lut": self._params.get("lut", "iron"),
            "output_path": out_png,
        })
        self.finished.emit(res)

    def _run_demo(self):
        """[模拟] 合成一张全圆盘演示云图（径向梯度 + 涡旋），标 [模拟]。"""
        import numpy as np
        from PIL import Image, ImageDraw
        out_png = self._params["out_png"]
        self.progress.emit("[模拟] 生成演示云图 ...")

        size = 256
        y, x = np.mgrid[0:size, 0:size]
        cx = cy = size / 2.0
        r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
        rmax = size / 2.0
        # 径向梯度模拟云带 + 螺旋涡旋
        theta = np.arctan2(y - cy, x - cx)
        spiral = 0.5 + 0.5 * np.sin(6.0 * theta + 12.0 * r / rmax)
        base = 1.0 - r / rmax
        img = (120 + 110 * base * (0.6 + 0.4 * spiral)).astype(np.float64)
        img[r > rmax] = 20  # 太空黑
        u8 = np.clip(img, 0, 255).astype(np.uint8)
        rgb = np.stack([u8, u8, u8], axis=-1)

        pil = Image.fromarray(rgb)
        draw = ImageDraw.Draw(pil)
        draw.text((8, 8), "[SIM] DEMO CLOUD", fill=(255, 255, 255))
        os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
        pil.save(out_png)

        self.finished.emit({
            "success": True, "png": out_png, "width": size, "height": size,
            "simulated": True, "content": "[模拟] 合成演示云图",
        })


# ======================================================================
# 气象云图面板主体
# ======================================================================
class WeatherPanel(QWidget):
    """气象卫星云图面板。后端 ToolRegistry 懒加载（首次解码才创建）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        c = _theme_colors()
        self._c = c
        self._iq_file: Optional[str] = None
        self._current_png: Optional[str] = None
        self._sdr_connected = False     # 默认未连接真实 SDR 硬件
        self._worker: Optional[QThread] = None
        self._worker_obj: Optional[DecodeWorker] = None
        self._frame_count = 0
        self._build_ui()
        self._on_satellite_changed(0)
        self._refresh_sdr_mode_state()

    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # ===== 控制卡片 =====
        ctrl = QFrame()
        ctrl.setObjectName("card")
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(10, 8, 10, 10)
        cl.setSpacing(6)

        title = QLabel("气象卫星云图接收")
        title.setObjectName("sectionTitle")
        cl.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.sat_combo = QComboBox()
        self.sat_combo.setToolTip("选择气象卫星（决定下行频率/符号率与解码族）")
        for name in SATELLITES:
            self.sat_combo.addItem(name)
        self.sat_combo.currentIndexChanged.connect(self._on_satellite_changed)
        form.addRow("卫星", self.sat_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.setToolTip("离线 IQ 文件解码，或实时 SDR 接收（无硬件时置灰）")
        self.mode_combo.addItems(["离线 IQ 文件", "实时 SDR"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("接收模式", self.mode_combo)

        # 文件选择
        file_row = QHBoxLayout()
        self.file_label = QLabel("未选择文件")
        self.file_label.setStyleSheet(f"color:{self._c['text_secondary']};")
        self.file_btn = QPushButton("选择 IQ 文件...")
        self.file_btn.setToolTip("打开 .iq (complex64 raw) / .wav / .npy 录制文件")
        self.file_btn.clicked.connect(self._on_pick_file)
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(self.file_btn)
        file_w = QWidget(); file_w.setLayout(file_row)
        form.addRow("IQ 文件", file_w)

        cl.addLayout(form)

        # 操作按钮行
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始解码")
        self.start_btn.setObjectName("recordButton")
        self.start_btn.setToolTip("在后台线程调用后端解码工具出图")
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setToolTip("停止当前解码（后台轮询取消标志）")
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)

        self.demo_btn = QPushButton("[模拟] 生成演示云图")
        self.demo_btn.setToolTip("用合成测试数据生成一张演示云图，不依赖硬件/录制文件")
        self.demo_btn.clicked.connect(self._on_demo)
        btn_row.addWidget(self.demo_btn)
        cl.addLayout(btn_row)

        # 图像处理选项
        enh_box = QGroupBox("图像处理（应用后调用 sat_image_enhance）")
        eh = QHBoxLayout(enh_box)
        self.enh_checks: Dict[str, QCheckBox] = {}
        for label in ENHANCE_OPS:
            cb = QCheckBox(label)
            cb.setToolTip(f"后端处理: {ENHANCE_OPS[label]}")
            cb.toggled.connect(self._on_enhance_toggled)
            self.enh_checks[label] = cb
            eh.addWidget(cb)
        eh.addStretch()
        cl.addWidget(enh_box)

        outer.addWidget(ctrl)

        # ===== 图像显示卡片 =====
        img_card = QFrame()
        img_card.setObjectName("card")
        il = QVBoxLayout(img_card)
        il.setContentsMargins(10, 8, 10, 10)
        self.image_title = QLabel("云图")
        self.image_title.setObjectName("sectionTitle")
        il.addWidget(self.image_title)

        self.image_label = QLabel("无数据")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumHeight(320)
        self.image_label.setStyleSheet(
            f"background-color:{self._c['bg_alt']};border:1px solid {self._c['border']};"
            f"border-radius:6px;color:{self._c['text_disabled']};")
        f = QFont(self.image_label.font()); f.setPointSize(12)
        self.image_label.setFont(f)
        il.addWidget(self.image_label, 1)
        outer.addWidget(img_card, 1)

        # ===== 状态栏 =====
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusValue")
        self.status_label.setToolTip("卫星参数 / 解码进度 / 当前帧计数")
        outer.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def set_sdr_connected(self, connected: bool):
        """由主窗口在真实硬件 connect 成功 / disconnect 后调用。"""
        self._sdr_connected = bool(connected)
        self._refresh_sdr_mode_state()

    # ------------------------------------------------------------------
    def _current_sat(self) -> Dict:
        name = self.sat_combo.currentText()
        return SATELLITES.get(name, {})

    def _on_satellite_changed(self, _idx: int):
        sat = self._current_sat()
        self._sat_params = sat
        self._update_status()

    def _on_mode_changed(self, _idx: int):
        self._refresh_sdr_mode_state()

    def _refresh_sdr_mode_state(self):
        realtime = self.mode_combo.currentText() == "实时 SDR"
        if realtime and not self._sdr_connected:
            # 红线：无真实硬件时，实时 SDR 按钮置灰并如实提示
            self.start_btn.setEnabled(False)
            self.file_btn.setEnabled(False)
            self._update_status(extra="未连接SDR设备 — 实时模式不可用，请接好硬件或改用离线 IQ 文件")
        else:
            self.start_btn.setEnabled(not self._is_busy())
            self.file_btn.setEnabled(not realtime)
            self._update_status()

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _on_pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 IQ 录制文件", "",
            "IQ/音频文件 (*.iq *.wav *.npy *.cfile *.raw);;所有文件 (*)")
        if path:
            self._iq_file = path
            self.file_label.setText(os.path.basename(path))
            self.file_label.setToolTip(path)
            self._update_status()

    # ------------------------------------------------------------------
    def _out_png_path(self, tag: str) -> str:
        art = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mbdsdr_ai", "artifacts")
        os.makedirs(art, exist_ok=True)
        return os.path.join(art, f"weather_{tag}_{int(time.time())}.png")

    def _on_start(self):
        if self._is_busy():
            return
        realtime = self.mode_combo.currentText() == "实时 SDR"
        if realtime and not self._sdr_connected:
            QMessageBox.warning(self, "未连接 SDR", "未连接SDR设备，无法实时接收。")
            return
        if not realtime and not self._iq_file:
            QMessageBox.information(self, "选择文件", "请先选择离线 IQ 录制文件。")
            return

        sat = self._current_sat()
        out_png = self._out_png_path("decode")
        params = {
            "family": sat.get("family", "gk2a"),
            "satellite": self.sat_combo.currentText(),
            "iq_file": self._iq_file,
            "out_png": out_png,
            "realtime": realtime,
        }
        self._start_worker("decode", params)

    def _on_demo(self):
        if self._is_busy():
            return
        out_png = self._out_png_path("demo")
        self._start_worker("demo", {"out_png": out_png})

    def _on_stop(self):
        if self._worker_obj is not None:
            self._worker_obj.request_stop()
        if self._worker is not None:
            self._worker.quit()
            self._worker.wait(1500)
        self._on_idle()

    def _on_enhance_toggled(self, _checked: bool):
        # 仅在已有图像时即时应用（避免无谓后端调用）
        if self._current_png and os.path.exists(self._current_png) and not self._is_busy():
            steps = [{"op": ENHANCE_OPS[label]}
                     for label, cb in self.enh_checks.items() if cb.isChecked()]
            if not steps:
                # 无增强项：恢复原始图
                self._show_image(self._current_png)
                return
            out_png = self._out_png_path("enh")
            self._start_worker("enhance", {
                "image_path": self._current_png,
                "steps": steps,
                "out_png": out_png,
            })

    # ------------------------------------------------------------------
    def _start_worker(self, action: str, params: Dict):
        self._worker = QThread()
        self._worker_obj = DecodeWorker(action, params)
        self._worker_obj.moveToThread(self._worker)
        self._worker.started.connect(self._worker_obj.run)
        self._worker_obj.progress.connect(self._on_progress)
        self._worker_obj.failed.connect(self._on_failed)
        self._worker_obj.finished.connect(self._on_result)
        self._worker_obj.finished.connect(self._worker.quit)
        self._worker.finished.connect(self._on_idle)
        self._worker.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._update_status(extra="解码中 ...")

    def _on_idle(self):
        self.stop_btn.setEnabled(False)
        if self._worker is not None:
            self._worker = None
            self._worker_obj = None
        self._refresh_sdr_mode_state()

    def _on_progress(self, text: str):
        self._update_status(extra=text)

    def _on_failed(self, text: str):
        self.image_label.setText("解码失败")
        self._update_status(extra=f"错误: {text.splitlines()[0]}")
        QMessageBox.critical(self, "解码错误", text)

    def _on_result(self, result):
        # result 可能是 ToolResult 或 dict（合成）
        png = None
        simulated = False
        if isinstance(result, dict):
            png = result.get("png")
            simulated = result.get("simulated", False)
            ok = result.get("success", False)
            msg = result.get("content", "")
        else:
            png = (getattr(result, "data", {}) or {}).get("png")
            ok = getattr(result, "success", False)
            msg = getattr(result, "content", "") or getattr(result, "error", "")

        if ok and png and os.path.exists(png):
            self._current_png = png
            self._frame_count += 1
            self._show_image(png)
            tag = " [模拟]" if simulated else ""
            self.image_title.setText(f"云图{tag} — {self.sat_combo.currentText()}")
            self._update_status(extra=f"出图成功{tag}: {os.path.basename(png)}")
        else:
            self.image_label.setText("无数据")
            self._update_status(extra=f"未出图: {msg}")

    def _show_image(self, path: str):
        pix = QPixmap(path)
        if pix.isNull():
            self.image_label.setText("图像加载失败")
            return
        self.image_label.setPixmap(
            pix.scaled(self.image_label.size(),
                       Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 尺寸变化时重绘已加载的图
        if self._current_png and os.path.exists(self._current_png):
            self._show_image(self._current_png)

    # ------------------------------------------------------------------
    def _update_status(self, extra: str = ""):
        sat = self._current_sat()
        if not sat:
            self.status_label.setText(extra or "就绪")
            return
        line = (f"{sat.get('freq_mhz', 0):.1f} MHz | "
                f"{sat.get('symrate_ksps', 0):.1f} ksps | "
                f"{sat.get('mod', '?')} | {sat.get('band', '')} | "
                f"帧计数: {self._frame_count}")
        if extra:
            line += f" | {extra}"
        self.status_label.setText(line)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    from themes import get_theme
    get_theme(DEFAULT_THEME).apply(app)
    w = WeatherPanel()
    w.resize(900, 700)
    w.show()
    print("WeatherPanel launched; sdr_connected =", w._sdr_connected)
    sys.exit(app.exec())

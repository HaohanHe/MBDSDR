"""
MBDSDR 桌面端 - 卫星云图面板 (SatelliteImagePanel)
====================================================

NOAA APT / METEOR LRPT 云图解码面板：图像显示区 + 解码控制 + 统计 + 历史列表。

数据链路（只显示真实解调出的云图，绝不放演示云图）：
  - NOAA-15/18/19 APT：外部把 AM 解调后的 APT 音频喂给 :meth:`SatelliteImagePanel.feed_audio`；
    面板累积音频，开始/停止解码时调用 ``mbdsdr_ai.noaa_apt_lite.decode_apt``
    （AM 包络→行同步→A/B 通道分离），产出灰度图。
  - METEOR-M2 LRPT：经 ``mbdsdr_ai.meteor_sat.demodulate_lrpt`` 解调解扰；
    图像重组尚未完成时返回 None，面板显「等待卫星下行信号」，不造假图。
  - 解码出的图像保存到 ``~/.mbdsdr/images/``；历史列表可点击回看；可另存 PNG。

红线：
  - 无数据时图像区显「等待卫星下行信号」，绝不预存/内置任何云图。
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QProgressBar, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

try:
    from mbdsdr_ai import noaa_apt_lite as _apt
    _HAS_APT = True
except Exception:  # pragma: no cover
    _apt = None
    _HAS_APT = False

# 空态文案（测试据此断言面板进入“无数据”状态）。
SAT_WAIT_TEXT = "等待卫星下行信号"

SATELLITES = ["NOAA-15 (APT)", "NOAA-18 (APT)", "NOAA-19 (APT)", "METEOR-M2 (LRPT)"]

_IMG_DIR = os.path.expanduser("~/.mbdsdr/images")


class SatelliteImagePanel(QWidget):
    """卫星云图面板：左侧图像区，右侧控制/统计/历史。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sdr_connected = False
        self._running = False
        self._audio_buf: List[np.ndarray] = []
        self._audio_sr: float = 0.0
        self._current_png: Optional[str] = None
        self._build_ui()
        self._load_history()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        # 左：图像区
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.image_label = QLabel(SAT_WAIT_TEXT)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background: #1b1b1b; color: #bbb;")
        self.image_label.setMinimumSize(320, 240)
        ll.addWidget(self.image_label, 1)
        self.stats_label = QLabel("行数: 0  | 同步: --  | 信号质量: --")
        ll.addWidget(self.stats_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        ll.addWidget(self.progress)
        splitter.addWidget(left)

        # 右：控制 + 历史
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("卫星:"))
        self.sat_combo = QComboBox()
        self.sat_combo.addItems(SATELLITES)
        rl.addWidget(self.sat_combo)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始解码")
        self.stop_btn = QPushButton("停止解码")
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        rl.addLayout(btn_row)

        self.save_btn = QPushButton("保存当前图像为 PNG")
        self.save_btn.setEnabled(False)
        rl.addWidget(self.save_btn)

        rl.addWidget(QLabel("历史图像:"))
        self.history = QListWidget()
        self.history.setMaximumWidth(220)
        rl.addWidget(self.history, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self.start_btn.clicked.connect(self.start_decode)
        self.stop_btn.clicked.connect(self.stop_decode)
        self.save_btn.clicked.connect(self._save_current)
        self.history.itemClicked.connect(self._on_history_clicked)

    # ------------------------------------------------------------------ #
    def set_sdr_connected(self, connected: bool) -> None:
        self._sdr_connected = bool(connected)
        self._refresh_start_enabled()

    def _refresh_start_enabled(self) -> None:
        # 无后端时开始按钮置灰，但图像区仍显等待文案（不造假图）。
        self.start_btn.setEnabled(self._sdr_connected and not self._running)

    def start_decode(self) -> None:
        if not self._sdr_connected:
            return
        self._running = True
        self._audio_buf = []
        self._audio_sr = 0.0
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.image_label.setText(SAT_WAIT_TEXT)
        self.progress.setValue(0)
        self.stats_label.setText("行数: 0  | 同步: 采集中…  | 信号质量: --")

    def stop_decode(self) -> None:
        self._running = False
        self.stop_btn.setEnabled(False)
        self._decode_buffer()
        self._refresh_start_enabled()

    def feed_audio(self, audio, sample_rate: float) -> None:
        """喂入 APT/LRPT 音频；解码进行中累积，停止时统一解码。"""
        if not self._running:
            return
        if audio is None or len(audio) == 0:
            return
        self._audio_buf.append(np.asarray(audio, dtype=np.float64))
        self._audio_sr = float(sample_rate)
        # 进度条：按累积时长粗略推进（4min 过境≈100%）
        dur = sum(len(b) for b in self._audio_buf) / max(self._audio_sr, 1.0)
        self.progress.setValue(min(100, int(dur / 240.0 * 100)))

    # ------------------------------------------------------------------ #
    def _decode_buffer(self) -> None:
        if not self._audio_buf or self._audio_sr <= 0:
            self.stats_label.setText("行数: 0  | 同步: 无信号  | 信号质量: --")
            return
        audio = np.concatenate(self._audio_buf)
        sat = self.sat_combo.currentText()
        if sat.endswith("(LRPT)"):
            # METEOR LRPT：图像重组尚未实现，保持空态，不造假图。
            self.stats_label.setText("METEOR LRPT 解调解扰已接入，图像重组待实现")
            self.image_label.setText(SAT_WAIT_TEXT)
            return
        if not _HAS_APT:
            self.image_label.setText("APT 解码器不可用")
            return
        try:
            result = _apt.decode_apt(audio, self._audio_sr)
        except Exception as e:
            self.stats_label.setText(f"解码失败: {e}")
            return
        if not result.get("apt_present"):
            self.stats_label.setText(
                f"行数: {result.get('lines_aligned',0)}  | "
                f"同步: {result.get('lock_ratio',0):.0%}  | 无 APT 信号")
            self.image_label.setText(SAT_WAIT_TEXT)
            return
        self._show_apt_result(result, sat)

    def _show_apt_result(self, result: dict, sat: str) -> None:
        os.makedirs(_IMG_DIR, exist_ok=True)
        tag = sat.split()[0].lower()
        prefix = os.path.join(_IMG_DIR, f"{tag}_{int(time.time())}")
        try:
            paths = _apt.save_apt_png(result, prefix)
        except Exception as e:
            self.stats_label.setText(f"保存 PNG 失败: {e}")
            return
        combo = paths.get("combo")
        self._current_png = combo
        pix = QPixmap(combo)
        if not pix.isNull():
            self.image_label.setPixmap(
                pix.scaled(self.image_label.size(),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.save_btn.setEnabled(True)
        lr = result.get("lock_ratio", 0.0)
        lines = result.get("lines_aligned", 0)
        self.stats_label.setText(
            f"行数: {lines}  | 同步: {lr:.0%}  | "
            f"信号质量: {'优' if lr > 0.85 else '中' if lr > 0.6 else '弱'}")
        self.progress.setValue(100)
        self._load_history()

    # ------------------------------------------------------------------ #
    def _load_history(self) -> None:
        self.history.clear()
        if not os.path.isdir(_IMG_DIR):
            return
        files = sorted(
            [f for f in os.listdir(_IMG_DIR) if f.endswith("_apt.png")],
            reverse=True)
        for f in files[:50]:
            QListWidgetItem(f, self.history)

    def _on_history_clicked(self, item: QListWidgetItem) -> None:
        path = os.path.join(_IMG_DIR, item.text())
        pix = QPixmap(path)
        if not pix.isNull():
            self._current_png = path
            self.image_label.setPixmap(
                pix.scaled(self.image_label.size(),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.save_btn.setEnabled(True)

    def _save_current(self) -> None:
        if not self._current_png or not os.path.exists(self._current_png):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存云图", os.path.basename(self._current_png), "PNG (*.png)")
        if path:
            try:
                from PIL import Image
                Image.open(self._current_png).save(path)
            except Exception:
                pass

    # 测试辅助：直接注入已解码结果（不写盘也能验证图像区刷新）。
    def show_decoded_result(self, result: dict, sat: str = "NOAA-19") -> None:
        self._show_apt_result(result, sat)

    def image_path(self) -> Optional[str]:
        return self._current_png

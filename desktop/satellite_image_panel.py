# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端 - 卫星云图面板 (SatelliteImagePanel)
====================================================

NOAA APT / METEOR LRPT 云图解码面板：图像显示区 + 解码控制 + 统计 + 历史列表。

数据链路（只显示真实解调出的云图，绝不放演示云图）：
  - NOAA-15/18/19 APT：外部把 AM 解调后的 APT 音频喂给 :meth:`SatelliteImagePanel.feed_audio`；
    面板累积音频，开始/停止解码时调用 ``mbdsdr_ai.noaa_apt_lite.decode_apt``
    （AM 包络→行同步→A/B 通道分离），产出灰度图。
  - METEOR-M2 LRPT：QPSK 数字链路，**不能从 APT 音频解码**。2026-11-06 起
    「开始解码」在该模式下转为选择 complex64 录制 IQ，走与气象云图面板
    **同一个** ``weather_panel.DecodeWorker(family="meteor")``：
    ``meteor_sat.demodulate_lrpt`` → 帧约定诊断 → ``compose_visible_image``
    → PNG。帧首约定未对齐 / 段不全 / 解码失败一律保持空态并写明真实原因，
    不造假图、不拿半幅图冒充。
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

from PySide6.QtCore import Qt, QThread, QObject, Signal  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QProgressBar, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

try:
    from mbdsdr_ai import noaa_apt_lite as _apt
    _HAS_APT = True
except Exception:  # pragma: no cover
    _apt = None
    _HAS_APT = False

try:  # pragma: no cover - 失败消息整形（clean_exc 战役先例），缺失时退化原文
    from mbdsdr_ai.tool_registry import clean_exc as _clean_exc
except Exception:  # noqa: BLE001
    def _clean_exc(e):  # type: ignore[misc]
        return str(e)

from tokens import tokens as _tok  # noqa: E402

# 空态文案（测试据此断言面板进入“无数据”状态）。
SAT_WAIT_TEXT = "等待卫星下行信号"

SATELLITES = ["NOAA-15 (APT)", "NOAA-18 (APT)", "NOAA-19 (APT)", "METEOR-M2 (LRPT)"]

_IMG_DIR = os.path.expanduser("~/.mbdsdr/images")


class SatelliteImagePanel(QWidget):
    """卫星云图面板：左侧图像区，右侧控制/统计/历史。"""

    #: feed_audio 在 DemodWorker 线程调用；进度条是 QWidget，只经 Signal 上屏。
    progress_ready = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sdr_connected = False
        self._running = False
        self._audio_buf: List[np.ndarray] = []
        self._audio_sr: float = 0.0
        self._current_png: Optional[str] = None
        self._decode_thread: Optional[QThread] = None
        self._decode_worker: Optional[QObject] = None
        self._build_ui()
        self.progress_ready.connect(self.progress.setValue)  # Auto: 同线程同步
        self._load_history()
        self._on_sat_changed(self.sat_combo.currentText())

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
        _t = _tok()
        self.image_label.setStyleSheet(
            f"background: {_t.COLORS['image_placeholder_bg']}; "
            f"color: {_t.text('tertiary')};")
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

        # LRPT 离线 IQ 解码：采样率优先读 SigMF meta，读不到才用这个手动值
        # （并在状态行写明来源，不把假设值冒充实测值）。
        sr_row = QHBoxLayout()
        sr_row.addWidget(QLabel("IQ 采样率:"))
        self.sr_spin = QDoubleSpinBox()
        self.sr_spin.setRange(0.1, 20.0)
        self.sr_spin.setDecimals(3)
        self.sr_spin.setValue(1.0)
        self.sr_spin.setSuffix(" MS/s")
        self.sr_spin.setToolTip(
            "仅当录制文件没有 SigMF meta 时使用这个手动值")
        sr_row.addWidget(self.sr_spin)
        rl.addLayout(sr_row)

        # 入口只留一个：NOAA 时「开始解码」= 音频采集，METEOR 时同一个
        # 按钮变「选择录制 IQ 解码」。早先同时挂一个独立的 IQ 按钮，
        # 真机截图里两个入口并排、文案几乎一样，用户不知道点哪个。
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
        self.sat_combo.currentTextChanged.connect(self._on_sat_changed)
        self.sr_spin.valueChanged.connect(self._on_sr_changed)

    # ------------------------------------------------------------------ #
    def _on_sat_changed(self, text: str) -> None:
        """模式切换：NOAA = 实时音频采集，METEOR LRPT = 离线 IQ 文件解码。

        LRPT 是 QPSK 数字链路，喂 APT 音频永远解不出来；旧实现让玩家
        "开始解码→采四分钟音频→停止"最后只得到一句「图像重组待实现」。
        现在按钮语义随模式变，且离线解码不需要 SDR 在线。
        """
        lrpt = str(text).endswith("(LRPT)")
        self.start_btn.setText("选择录制 IQ 解码" if lrpt else "开始解码")
        self.sr_spin.setEnabled(lrpt)
        self._refresh_start_enabled()

    def _on_sr_changed(self, value: float) -> None:
        self.sr_spin.setToolTip(
            f"手动 {value:.3f} MS/s；录制文件带 SigMF meta 时优先用 meta 里的值")

    # ------------------------------------------------------------------ #
    def set_sdr_connected(self, connected: bool) -> None:
        self._sdr_connected = bool(connected)
        self._refresh_start_enabled()

    def _refresh_start_enabled(self) -> None:
        # NOAA 实时音频采集需要后端在线；METEOR LRPT 是离线文件解码，不需要
        # SDR 连接（但正在跑的解码任务不许重入）。
        lrpt = self.sat_combo.currentText().endswith("(LRPT)")
        th = self._decode_thread
        busy = th is not None and th.isRunning()
        self.start_btn.setEnabled(
            (True if lrpt else self._sdr_connected)
            and not self._running and not busy)

    def start_decode(self) -> None:
        if self.sat_combo.currentText().endswith("(LRPT)"):
            # 数字链路没有"采音频"这一步：按钮直接转成选录制 IQ 解码。
            self._choose_iq_decode()
            return
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
        self.progress_ready.emit(min(100, int(dur / 240.0 * 100)))

    # ------------------------------------------------------------------ #
    # METEOR LRPT 离线 IQ 解码：与气象云图面板共用同一个 DecodeWorker，
    # 不在这里复制第二份解调/重组实现（第二事实源会静默漂移）。
    # ------------------------------------------------------------------ #
    def _iq_sample_rate(self, path: str):
        """采样率的**唯一**来源判定：SigMF meta 优先，否则用手动值并标出来。

        绝不把手动假设值冒充实测值——状态行会明写来源。
        """
        import json
        meta_path = (path[:-len(".sigmf-data")] + ".sigmf-meta"
                     if path.endswith(".sigmf-data") else path + ".sigmf-meta")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    sr = float(json.load(f)["global"]["core:sample_rate"])
                if sr > 0:
                    return sr, f"SigMF meta {sr / 1e6:.3f} MS/s"
            except Exception:  # noqa: BLE001（meta 损坏/缺字段 → 诚实降级）
                pass
        sr = float(self.sr_spin.value()) * 1e6
        return sr, f"手动输入 {sr / 1e6:.3f} MS/s（文件无有效 SigMF meta）"

    def _choose_iq_decode(self) -> None:
        th = self._decode_thread
        if th is not None and th.isRunning():
            self.stats_label.setText("已有 LRPT 解码任务在跑，等它结束再选文件")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 complex64 录制 IQ (Meteor LRPT)", "",
            "IQ 录制 (*.sigmf-data *.iq *.cfile *.bin *.complex64);;所有文件 (*)")
        if not path:
            return
        sr, sr_note = self._iq_sample_rate(path)
        os.makedirs(_IMG_DIR, exist_ok=True)
        out_png = os.path.join(_IMG_DIR, f"meteor_{int(time.time())}_lrpt.png")
        try:
            from weather_panel import DecodeWorker
        except Exception as exc:
            self.stats_label.setText(
                f"LRPT 解码器不可用: {type(exc).__name__}: {_clean_exc(exc)}")
            return

        params = {
            "family": "meteor", "iq_file": path, "sample_rate": sr,
            "out_png": out_png, "satellite": "METEOR-M2 (LRPT)",
        }
        thread = QThread(self)
        worker = DecodeWorker(params)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_decode_progress)
        worker.failed.connect(self._on_decode_failed)
        worker.finished.connect(self._on_decode_result)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._on_decode_idle)
        self._decode_thread = thread
        self._decode_worker = worker
        self.image_label.setText(SAT_WAIT_TEXT)
        self.stats_label.setText(
            f"解码中: {os.path.basename(path)} | 采样率 {sr_note}")
        self.progress.setValue(0)
        self.start_btn.setEnabled(False)
        thread.start()

    def _on_decode_progress(self, text: str) -> None:
        self.stats_label.setText(str(text)[:160])

    def _on_decode_failed(self, text: str) -> None:
        self.image_label.setText(SAT_WAIT_TEXT)
        self.stats_label.setText(f"LRPT 解码失败: {str(text)[:160]}")
        self.progress.setValue(0)

    def _on_decode_result(self, res: dict) -> None:
        png = res.get("png")
        if res.get("ok") and png and os.path.isfile(png):
            pix = QPixmap(png)
            if pix.isNull():
                self.image_label.setText(SAT_WAIT_TEXT)
                self.stats_label.setText("LRPT 出图文件无法加载（PNG 损坏）")
                return
            self._current_png = png
            self.image_label.setPixmap(
                pix.scaled(self.image_label.size(),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.save_btn.setEnabled(True)
            frames = res.get("frames", "—")
            self.stats_label.setText(
                f"帧数: {frames}  | 来源: Meteor LRPT 录制 IQ  | "
                f"分辨率: {res.get('width', '—')}×{res.get('height', '—')}")
            self.progress.setValue(100)
            self._load_history()
            return
        # 不出图就是不出图：保持空态，写明真实原因（帧约定未对齐 / 段不全 /
        # 无 CADU），绝不补假图、绝不拿半幅图冒充。
        self.image_label.setText(SAT_WAIT_TEXT)
        err = str(res.get("error") or "未知原因")
        self.stats_label.setText(
            f"未出图（帧数 {res.get('frames', '—')}）: {err[:200]}")
        self.progress.setValue(0)

    def _on_decode_idle(self) -> None:
        self._decode_thread = None
        self._decode_worker = None
        self._on_sat_changed(self.sat_combo.currentText())

    def closeEvent(self, event) -> None:
        """解码线程必须在本对象销毁前停住，否则 QThread 析构会崩。"""
        worker = self._decode_worker
        if worker is not None and hasattr(worker, "request_stop"):
            worker.request_stop()
        th = self._decode_thread
        if th is not None and th.isRunning():
            th.quit()
            th.wait(1500)
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    def _decode_buffer(self) -> None:
        if not self._audio_buf or self._audio_sr <= 0:
            self.stats_label.setText("行数: 0  | 同步: 无信号  | 信号质量: --")
            return
        audio = np.concatenate(self._audio_buf)
        sat = self.sat_combo.currentText()
        if sat.endswith("(LRPT)"):
            # 不再谎称"图像重组待实现"——重组器在仓库里是有的，真实的坑是
            # QPSK 数字链路根本不吃 APT 音频。给出可执行入口而不是死胡同。
            self.stats_label.setText(
                "METEOR LRPT 是 QPSK 数字链路，不能从 APT 音频解码；"
                "请点「选择录制 IQ 解码」选 complex64 录制文件")
            self.image_label.setText(SAT_WAIT_TEXT)
            return
        if not _HAS_APT:
            self.image_label.setText("APT 解码器不可用")
            return
        try:
            result = _apt.decode_apt(audio, self._audio_sr)
        except Exception as e:
            self.stats_label.setText(f"解码失败: {_clean_exc(e)}")
            return
        if not result.get("apt_present"):
            lines = result.get("lines_aligned")
            lock = result.get("lock_ratio")
            self.stats_label.setText(
                f"行数: {'—' if lines is None else lines}  | "
                f"同步: {'—' if lock is None else f'{lock:.0%}'}  | 无 APT 信号")
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
            self.stats_label.setText(f"保存 PNG 失败: {_clean_exc(e)}")
            return
        combo = paths.get("combo")
        self._current_png = combo
        pix = QPixmap(combo)
        if not pix.isNull():
            self.image_label.setPixmap(
                pix.scaled(self.image_label.size(),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.save_btn.setEnabled(True)
        lr = result.get("lock_ratio")
        lines = result.get("lines_aligned")
        quality = ("—" if lr is None
                   else "优" if lr > 0.85 else "中" if lr > 0.6 else "弱")
        self.stats_label.setText(
            f"行数: {'—' if lines is None else lines}  | "
            f"同步: {'—' if lr is None else f'{lr:.0%}'}  | 信号质量: {quality}")
        self.progress.setValue(100)
        self._load_history()

    # ------------------------------------------------------------------ #
    def _load_history(self) -> None:
        self.history.clear()
        if not os.path.isdir(_IMG_DIR):
            return
        files = sorted(
            [f for f in os.listdir(_IMG_DIR)
             if f.endswith("_apt.png") or f.endswith("_lrpt.png")],
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
        """把当前云图另存为 PNG；**成败都要在状态行里说人话**。

        2026-11-06：旧实现是 ``except Exception: pass`` —— 写盘失败（目录不存在、
        权限、磁盘满）时用户看到的仍是上一次的状态文案，会以为已经保存成功。
        现在成功报目标文件名、失败报目标文件名 + 异常类型，两条都可复核。
        """
        if not self._current_png or not os.path.exists(self._current_png):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存云图", os.path.basename(self._current_png), "PNG (*.png)")
        if not path:
            return
        target_name = os.path.basename(str(path))
        try:
            from PIL import Image
            Image.open(self._current_png).save(path)
        except Exception as exc:
            self.stats_label.setText(
                f"保存 PNG 失败: {target_name}（{type(exc).__name__}）")
            return
        self.stats_label.setText(f"云图已另存: {target_name}")

    # 测试辅助：直接注入已解码结果（不写盘也能验证图像区刷新）。
    def show_decoded_result(self, result: dict, sat: str = "NOAA-19") -> None:
        self._show_apt_result(result, sat)

    def image_path(self) -> Optional[str]:
        return self._current_png

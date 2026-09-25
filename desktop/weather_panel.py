"""
MBDSDR 桌面端 - 气象卫星云图面板 (WeatherPanel)
==================================================

真实数据链路（无任何合成/示例云图）：
  - 卫星制式：GOES-16 HRIT / GK-2A LRIT / Meteor-M2 LRPT / NOAA-19 APT /
              FY-4A HRIT / FY-3D HRPT
  - 数据来源：
      * 录制文件：真实 .iq(complex64) / .wav(APT 音频) / .cfile 离线解调解码
      * 实时 SDR：需先在主窗口调谐到下行频率并用工具栏录音(Ctrl+R)落盘，
                  再切「录制文件」对同一 baseband 解码（不另起 read_samples
                  定时器与主窗口统一 IQ 轮询抢同一环形缓冲）
  - 解码链路（每一步都调用 mbdsdr_ai 真实模块，不跳过、不填假数据）：
      GK-2A  : gk2a_lrit.decode_iq_to_image
                 = BPSK 解调 → Viterbi(K=7,1/2,0x4F/0x6D) → 帧同步 0x1ACFFC1D
                   → CCSDS 解扰 → RS(255,223,I=4) → VCDU → M_PDU → TP_PDU
                   → SessionPDU → LRIT 文件头 → 图像段组装 → JPEG2000/裸像素 → PNG
      GOES   : 复用 gk2a_lrit 的 BPSK/Viterbi/同步/解扰/RS 物理层得到 892B VCDU，
                 喂 goes_lrit.HRITParser().feed_vcdu() → GOESImageDecoder().add_lrit_file()
      NOAA   : noaa_apt_lite.decode_apt() (AM 包络→行同步→A/B 通道) → save_apt_png
      Meteor : meteor_sat.demodulate_lrpt() (QPSK→去交织→Viterbi→CCDB 解扰→CADU)
                 可见光重组 compose_visible_image 上游未实现 → 如实报帧数，不出假图
      FY-4/3 : fengyun_sat 真实模块（DVB-S2 PL 同步 / HRPT 帧同步）；面板对 raw IQ
                 的端到端出图未接通时如实标注，绝不补假云图
  - 图像处理（可选后处理）：sat_image_processing.sat_image_enhance
                 （中值/直方图均衡/白平衡/Kuwahara）

红线：
  * 无 SDR 连接且无录制文件 → 图像区空白，仅显示「未连接 / 无数据」占位文字。
  * 不内置任何默认/示例/合成云图，不 np.random 生成假图，不从 URL 拉示例图。
  * 实时 SDR 模式在 set_sdr_connected(False) 时整体置灰。

配色（低饱和默认主题，与 themes.py 一致；面板局部强调色直接取任务给定值）：
  米白 #F5F3EF / 蓝灰 #5B7B8C / 橙 #C4845C / 绿 #6BA89A / 红 #B85C5C
"""
from __future__ import annotations

import os
import sys
import time
import traceback
import wave
from typing import Dict, List, Optional

# 允许 desktop/ 直接跑，也允许被 main_window import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from PySide6.QtCore import Qt, QThread, Signal, QObject  # noqa: E402
from PySide6.QtGui import QPixmap, QFont  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QPushButton,
    QLabel, QCheckBox, QFrame, QFileDialog, QGroupBox, QMessageBox,
    QLineEdit, QProgressBar,
)

from themes import get_theme, DEFAULT_THEME  # noqa: E402


# ----------------------------------------------------------------------
# 低饱和面板强调色（任务给定值；控件本体配色仍走 themes.py）
# ----------------------------------------------------------------------
C_BG = "#F5F3EF"
C_BLUE = "#5B7B8C"
C_ORANGE = "#C4845C"
C_GREEN = "#6BA89A"
C_RED = "#B85C5C"


# ----------------------------------------------------------------------
# 卫星参数表：freq/symrate/采样率/调制/解码族
# family 决定后台 worker 调用哪条真实 mbdsdr_ai 解码链。
# ----------------------------------------------------------------------
SATELLITES: Dict[str, Dict] = {
    "GOES-16 HRIT (75.2°W)": {
        "freq_mhz": 1686.6, "symrate_ksps": 921.6, "srate_msps": 2.0,
        "mod": "BPSK", "family": "goes", "band": "L 波段 HRIT",
    },
    "GK-2A LRIT (128.2°E)": {
        "freq_mhz": 1692.14, "symrate_ksps": 128.0, "srate_msps": 1.0,
        "mod": "BPSK", "family": "gk2a", "band": "L 波段 LRIT",
    },
    "Meteor-M2 LRPT": {
        "freq_mhz": 137.1, "symrate_ksps": 72.0, "srate_msps": 1.0,
        "mod": "QPSK", "family": "meteor", "band": "VHF LRPT",
    },
    "NOAA-19 APT": {
        "freq_mhz": 137.1, "symrate_ksps": 2.4, "srate_msps": 48.0,
        "mod": "APT/AM", "family": "noaa", "band": "VHF APT",
    },
    "FY-4A HRIT (104.7°E)": {
        "freq_mhz": 1680.0, "symrate_ksps": 720.0, "srate_msps": 2.0,
        "mod": "QPSK/DVB-S2", "family": "fy4", "band": "L 波段 HRIT",
    },
    "FY-3D HRPT": {
        "freq_mhz": 1700.0, "symrate_ksps": 665.4, "srate_msps": 2.0,
        "mod": "BPSK", "family": "fy3", "band": "L 波段 HRPT",
    },
}

# 后处理 op 名 → sat_image_processing 工具参数
ENHANCE_OPS = {
    "中值滤波": "median",
    "直方图均衡": "equalize",
    "白平衡": "white_balance",
    "Kuwahara 降噪": "kuwahara",
}


def _theme_colors() -> Dict[str, str]:
    return get_theme(DEFAULT_THEME).colors


# ======================================================================
# 录制文件读取（真实二进制，不做任何合成）
# ======================================================================
def _load_complex64(path: str) -> np.ndarray:
    """读 complex64 (float32 交错 I/Q) raw 录制为 complex64 ndarray。"""
    return np.fromfile(path, dtype=np.complex64)


def _load_wav_mono(path: str) -> "tuple[np.ndarray, float]":
    """读 .wav 为单声道 float64 归一化音频。支持 8/16/24/32-bit PCM。"""
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        fr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0
    elif sw == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        as32 = (b[:, 0].astype(np.int32)
                | (b[:, 1].astype(np.int32) << 8)
                | (b[:, 2].astype(np.int32) << 16))
        as32 = np.where(as32 & 0x800000, as32 | ~0xFFFFFF, as32)
        data = as32.astype(np.float64) / 8388608.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"不支持的 wav 采样位宽: {sw*8}-bit")
    if nch > 1:
        data = data.reshape(-1, nch).mean(axis=1)
    return data, float(fr)


# ======================================================================
# 通用 CCSDS BPSK 解帧：复用 gk2a_lrit 的真实物理层积木，输出 892B VCDU
# （GOES HRIT 与 GK-2A LRIT 同 CCSDS 卷积+RS  concatenated 链，差别仅在符号率）
# ======================================================================
def _ccsds_bpsk_deframe(iq: np.ndarray, sps: int) -> List[bytes]:
    """IQ complex64 → 892B VCDU 列表。

    链路全部来自 mbdsdr_ai/gk2a_lrit.py（对标 SatDump module_ccsds_conv_concat_decoder）：
      RRC 匹配 → BPSK 软符号 → Viterbi(K=7,1/2,0x4F/0x6D) → 帧同步 0x1ACFFC1D
      → CCSDS 解扰(PN255) → RS(255,223,I=4) → 892B VCDU
    """
    from mbdsdr_ai import gk2a_lrit as gk
    from mbdsdr_ai.gk2a_lrit import (
        bpsk_demod, ViterbiDecoder, frame_sync_search, bits_to_bytes,
        derandomize_ccsds, rs_decode_interleaved,
        CADU_BITS, CADU_BYTES, DERAND_OFFSET, VCDU_LEN, SYNC_WORD_BYTES,
    )

    soft = bpsk_demod(iq, sps)
    if len(soft) < CADU_BITS * 2:
        return []
    bits = ViterbiDecoder().decode(soft)
    off = frame_sync_search(bits)
    if off < 0:
        return []
    bits = bits[off:]
    vcdus: List[bytes] = []
    n_frames = len(bits) // CADU_BITS
    for fi in range(n_frames):
        fb = bits[fi * CADU_BITS: (fi + 1) * CADU_BITS]
        frame = bytearray(bits_to_bytes(fb))
        if bytes(frame[:4]) != SYNC_WORD_BYTES:
            continue
        derandomize_ccsds(frame, CADU_BYTES - DERAND_OFFSET, offset=DERAND_OFFSET)
        block = frame[DERAND_OFFSET: CADU_BYTES]
        errs = rs_decode_interleaved(block)
        if any(e < 0 for e in errs):
            continue
        vcdus.append(bytes(block[:VCDU_LEN]))
    return vcdus


# ======================================================================
# 后台解码线程：真实调用 mbdsdr_ai 解码模块，不产出任何合成图像
# ======================================================================
class DecodeWorker(QObject):
    finished = Signal(dict)        # {png?, width, height, annotation, sat, channel, meta, error, frames}
    progress = Signal(str)         # 进度文本
    failed = Signal(str)

    def __init__(self, params: Dict, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._p = params
        self._stop = False

    def request_stop(self):
        self._stop = True

    # ------------------------------------------------------------------
    def run(self):
        try:
            fam = self._p["family"]
            if fam == "gk2a":
                self._run_gk2a()
            elif fam == "goes":
                self._run_goes()
            elif fam == "noaa":
                self._run_noaa()
            elif fam == "meteor":
                self._run_meteor()
            elif fam in ("fy4", "fy3"):
                self._run_fengyun()
            else:
                self.finished.emit({"ok": False, "error": f"未知解码族: {fam}"})
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}")

    # ------------------------------------------------------------------
    def _emit(self, **kw):
        kw.setdefault("ok", False)
        kw.setdefault("satellite", self._p.get("satellite", ""))
        kw.setdefault("family", self._p.get("family", ""))
        self.finished.emit(kw)

    # ---- GK-2A LRIT：完整 IQ→PNG 真实链 -------------------------------
    def _run_gk2a(self):
        from mbdsdr_ai import gk2a_lrit as gk
        path = self._p["iq_file"]
        out_png = self._p["out_png"]
        sps = int(self._p.get("sps", 8))
        self.progress.emit(f"读取录制 {os.path.basename(path)} (complex64) ...")
        iq = _load_complex64(path)
        self.progress.emit(f"IQ {len(iq)/1e6:.2f} M 采样 → BPSK/Viterbi/RS/解帧 ...")
        res = gk.decode_iq_to_image(iq, out_png, sps=sps)
        if res.success:
            self._emit(ok=True, png=res.png_path, width=res.width, height=res.height,
                       annotation=res.annotation, channel="LRIT 全圆盘",
                       frames=res.n_files, meta=res.metadata)
        else:
            self._emit(ok=False, error=f"GK-2A 解码: {res.error}")

    # ---- GOES HRIT：复用 CCSDS BPSK 解帧 → goes_lrit 重组 --------------
    def _run_goes(self):
        from mbdsdr_ai import goes_lrit as gl
        path = self._p["iq_file"]
        out_png = self._p["out_png"]
        sps = int(self._p.get("sps", 2))
        self.progress.emit(f"读取录制 {os.path.basename(path)} (complex64) ...")
        iq = _load_complex64(path)
        self.progress.emit(f"IQ {len(iq)/1e6:.2f} M 采样 → CCSDS BPSK 解帧 ...")
        vcdus = _ccsds_bpsk_deframe(iq, sps)
        self.progress.emit(f"解出 {len(vcdus)} 个 892B VCDU → goes_lrit 重组 ...")
        parser = gl.HRITParser()
        dec = gl.GOESImageDecoder()
        got = None
        for vcdu in vcdus:
            for fbuf in parser.feed_vcdu(vcdu):
                img = dec.add_lrit_file(fbuf)
                if img is not None:
                    got = img
        if got is None:
            self._emit(ok=False, frames=len(vcdus),
                       error="VCDU 已解出但 LRIT 图像段未凑齐（录制时长过短或信号弱）")
            return
        from PIL import Image
        arr = np.asarray(got.pixels, dtype=np.uint8).reshape(got.height, got.width)
        Image.fromarray(arr, mode="L").save(out_png)
        self._emit(ok=True, png=out_png, width=got.width, height=got.height,
                   annotation=got.annotation, channel="GOES ABI",
                   frames=len(vcdus), meta={"image_identifier": got.image_identifier})

    # ---- NOAA-19 APT：wav 音频 → APT 解码 → PNG -----------------------
    def _run_noaa(self):
        from mbdsdr_ai import noaa_apt_lite as apt
        path = self._p["iq_file"]
        out_prefix = os.path.splitext(self._p["out_png"])[0]
        self.progress.emit(f"读取 APT 音频 {os.path.basename(path)} ...")
        audio, fs = _load_wav_mono(path)
        self.progress.emit(f"音频 {len(audio)/fs:.1f}s @ {fs/1e3:.1f}kHz → AM 包络/行同步 ...")
        res = apt.decode_apt(audio, fs)
        if not res.get("apt_present"):
            self._emit(ok=False, error=f"APT 未锁相: {res.get('reason')} "
                                       f"(对齐行 {res.get('lines_aligned', 0)}, "
                                       f"lock {res.get('lock_ratio', 0)})")
            return
        paths = apt.save_apt_png(res, out_prefix)
        self._emit(ok=True, png=paths["combo"],
                   width=res["image_a"].shape[1] * 2, height=res["image_a"].shape[0],
                   annotation=f"APT lock={res['lock_ratio']}", channel="APT A+B",
                   frames=res["lines_aligned"],
                   meta={"duration_s": res["duration_s"], "paths": paths})

    # ---- Meteor-M2 LRPT：真实解调链，可见光重组上游未实现，如实报帧数 ----
    def _run_meteor(self):
        from mbdsdr_ai import meteor_sat as ms
        path = self._p["iq_file"]
        fs = float(self._p.get("sample_rate", 1e6))
        self.progress.emit(f"读取 Meteor IQ {os.path.basename(path)} ...")
        iq = _load_complex64(path).astype(np.complex128)
        params = ms.get_satellite_params("meteor_m2_hrpt")
        if params is None:
            self._emit(ok=False, error="meteor_sat 未找到 meteor_m2_hrpt 参数")
            return
        self.progress.emit("QPSK→去交织→Viterbi→CCDB 解扰→CADU 提取 ...")
        cadus = ms.demodulate_lrpt(iq, fs, params)
        self._emit(ok=False, frames=len(cadus),
                   error=(f"已真实解出 {len(cadus)} 个 LRPT CADU 帧；"
                          "可见光通道重组 compose_visible_image 上游未实现，"
                          "本面板不出合成图"))

    # ---- FY-4 / FY-3：调用真实模块，raw IQ 端到端出图未接通则如实报告 ----
    def _run_fengyun(self):
        from mbdsdr_ai import fengyun_sat as fy
        path = self._p["iq_file"]
        self.progress.emit(f"读取录制 {os.path.basename(path)} ...")
        raw = open(path, "rb").read()
        fam = self._p["family"]
        if fam == "fy4":
            # DVB-S2 PL 同步（SOF 26-bit 0x18D2E82）— 真实模块
            bits = np.unpackbits(np.frombuffer(raw[:len(raw)//2], dtype=np.uint8))
            hits = fy.fy4_dvbs2_sync(bits.astype(np.uint8))
            self._emit(ok=False, frames=len(hits),
                       error=(f"FY-4 DVB-S2 PL 同步命中 {len(hits)} 帧；"
                              "LDPC/BB 解扰后 VCDU 重组出图链路在本面板未接通，"
                              "请用 SatDump FengYun-4.json 流水线"))
        else:
            # FY-3 HRPT：60-bit 帧同步真实模块
            frames = fy.fy3_ahrpt_sync(raw)
            self._emit(ok=False, frames=len(frames),
                       error=(f"FY-3 HRPT 帧同步命中 {len(frames)} 帧；"
                              "AVHRR 通道软比特解调→出图链路在本面板未接通"))


# ======================================================================
# 气象云图面板主体
# ======================================================================
class WeatherPanel(QWidget):
    """气象卫星云图面板：真实录制/实时 IQ → mbdsdr_ai 解码 → QPixmap。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._c = _theme_colors()
        self._iq_file: Optional[str] = None
        self._current_png: Optional[str] = None
        self._sdr_connected = False
        self._worker: Optional[QThread] = None
        self._worker_obj: Optional[DecodeWorker] = None
        self._frame_count = 0
        self._build_ui()
        self._on_satellite_changed(0)
        self._refresh_state()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        ctrl = QFrame(); ctrl.setObjectName("card")
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(10, 8, 10, 10); cl.setSpacing(6)

        title = QLabel("气象卫星云图接收")
        title.setObjectName("sectionTitle")
        cl.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.sat_combo = QComboBox()
        self.sat_combo.setToolTip("选择下行制式（决定频率/符号率与解码族）")
        for name in SATELLITES:
            self.sat_combo.addItem(name)
        self.sat_combo.currentIndexChanged.connect(self._on_satellite_changed)
        form.addRow("卫星制式", self.sat_combo)

        self.src_combo = QComboBox()
        self.src_combo.setToolTip("离线录制文件解码，或实时 SDR 边收边解")
        self.src_combo.addItems(["录制文件", "实时 SDR"])
        self.src_combo.currentIndexChanged.connect(self._refresh_state)
        form.addRow("数据来源", self.src_combo)

        # 文件选择
        file_row = QHBoxLayout()
        self.file_label = QLabel("未选择文件")
        self.file_label.setStyleSheet(f"color:{self._c['text_secondary']};")
        self.file_btn = QPushButton("选择录制文件...")
        self.file_btn.setToolTip("complex64 raw .iq/.cfile/.raw，或 NOAA APT 的 .wav")
        self.file_btn.clicked.connect(self._on_pick_file)
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(self.file_btn)
        file_w = QWidget(); file_w.setLayout(file_row)
        form.addRow("录制文件", file_w)

        # 频率/符号率/采样率（可编辑）
        self.freq_edit = QLineEdit()
        self.freq_edit.setToolTip("下行中心频率 MHz")
        form.addRow("中心频率", self.freq_edit)
        self.sym_edit = QLineEdit()
        self.sym_edit.setToolTip("符号率 ksps")
        form.addRow("符号率", self.sym_edit)
        self.sr_edit = QLineEdit()
        self.sr_edit.setToolTip("采样率 Msps（complex64 录制）")
        form.addRow("采样率", self.sr_edit)
        cl.addLayout(form)

        # 按钮行
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始解码")
        self.start_btn.setObjectName("recordButton")
        self.start_btn.setToolTip("后台线程调用 mbdsdr_ai 真实解码链出图")
        self.start_btn.clicked.connect(self.start_decode)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setToolTip("停止接收/解码")
        self.stop_btn.clicked.connect(self.stop_decode)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        cl.addLayout(btn_row)

        # 后处理
        enh_box = QGroupBox("后处理（调用 sat_image_enhance，仅对已出图生效）")
        eh = QHBoxLayout(enh_box)
        self.enh_checks: Dict[str, QCheckBox] = {}
        for label in ENHANCE_OPS:
            cb = QCheckBox(label)
            cb.toggled.connect(self._on_enhance_toggled)
            self.enh_checks[label] = cb
            eh.addWidget(cb)
        eh.addStretch()
        cl.addWidget(enh_box)

        outer.addWidget(ctrl)

        # 图像卡片
        img_card = QFrame(); img_card.setObjectName("card")
        il = QVBoxLayout(img_card)
        il.setContentsMargins(10, 8, 10, 10)
        self.image_title = QLabel("云图")
        self.image_title.setObjectName("sectionTitle")
        il.addWidget(self.image_title)
        self.meta_label = QLabel("卫星: — | 通道: — | 时间: — | 分辨率: —")
        self.meta_label.setStyleSheet(f"color:{self._c['text_secondary']};font-size:11px;")
        il.addWidget(self.meta_label)

        self.image_label = QLabel("未连接 / 无数据")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumHeight(340)
        self.image_label.setStyleSheet(
            f"background-color:{self._c['bg_alt']};"
            f"border:1px dashed {self._c['border']};border-radius:6px;"
            f"color:{self._c['text_disabled']};")
        f = QFont(self.image_label.font()); f.setPointSize(12)
        self.image_label.setFont(f)
        il.addWidget(self.image_label, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100); self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        il.addWidget(self.progress)
        outer.addWidget(img_card, 1)

        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusValue")
        outer.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # 对外接口（main_window 依赖）
    # ------------------------------------------------------------------
    def set_sdr_connected(self, connected: bool):
        """主窗口在真实硬件 connect/disconnect 后调用。"""
        self._sdr_connected = bool(connected)
        self._refresh_state()

    def set_iq_file(self, path: str):
        """外部直接指定录制文件路径。"""
        if path and os.path.exists(path):
            self._iq_file = path
            self.file_label.setText(os.path.basename(path))
            self.file_label.setToolTip(path)
            self.src_combo.setCurrentIndex(0)
            self._refresh_state()

    # ------------------------------------------------------------------
    # 内部状态
    # ------------------------------------------------------------------
    def _current_sat(self) -> Dict:
        return SATELLITES.get(self.sat_combo.currentText(), {})

    def _is_realtime(self) -> bool:
        return self.src_combo.currentText() == "实时 SDR"

    def _on_satellite_changed(self, _idx: int):
        sat = self._current_sat()
        self.freq_edit.setText(f"{sat.get('freq_mhz', 0):.3f}")
        self.sym_edit.setText(f"{sat.get('symrate_ksps', 0):.1f}")
        self.sr_edit.setText(f"{sat.get('srate_msps', 1.0):.2f}")
        self._refresh_state()

    def _refresh_state(self):
        realtime = self._is_realtime()
        busy = self._is_busy()
        no_hw = realtime and not self._sdr_connected

        # 文件选择仅离线模式可用
        self.file_btn.setEnabled(not realtime and not busy)
        # 频率/符号率/采样率编辑：解码中锁定
        for w in (self.freq_edit, self.sym_edit, self.sr_edit, self.sat_combo, self.src_combo):
            w.setEnabled(not busy)
        # 实时模式需要 SDR 连接；离线需要文件
        if no_hw:
            self.start_btn.setEnabled(False)
        elif busy:
            self.start_btn.setEnabled(False)
        elif realtime:
            # 实时：只要 SDR 已连接即可开始（边收边落盘）
            self.start_btn.setEnabled(self._sdr_connected)
        else:
            self.start_btn.setEnabled(self._iq_file is not None)
        self.stop_btn.setEnabled(busy)

        if no_hw:
            self._set_placeholder("未连接SDR设备 — 请接好硬件，或切到「录制文件」离线解码")
            self._update_status(extra="未连接SDR设备")
        elif not busy and not self._current_png:
            self._set_placeholder("未连接 / 无数据")
            self._update_status()

    def _set_placeholder(self, text: str):
        if not self._current_png or not os.path.exists(self._current_png):
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(text)

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _on_pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择录制文件", "",
            "录制文件 (*.iq *.cfile *.raw *.complex64 *.wav);;所有文件 (*)")
        if path:
            self.set_iq_file(path)

    # ------------------------------------------------------------------
    def _out_png_path(self, tag: str) -> str:
        art = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mbdsdr_ai", "artifacts")
        os.makedirs(art, exist_ok=True)
        return os.path.join(art, f"weather_{tag}_{int(time.time())}.png")

    # ------------------------------------------------------------------
    def start_decode(self):
        """开始解码（离线录制文件 或 实时 SDR 落盘后解码）。"""
        if self._is_busy():
            return
        realtime = self._is_realtime()
        sat = self._current_sat()
        out_png = self._out_png_path("decode")

        if realtime:
            if not self._sdr_connected:
                QMessageBox.warning(self, "未连接 SDR", "未连接SDR设备，无法实时接收。")
                return
            # 实时过境接收需要独占调谐到卫星下行频率并录制一个完整过境段。
            # 不再在此面板另起 read_samples 定时器——那会与主窗口统一 IQ 轮询
            # （50ms 一次 read_samples）抢同一环形缓冲，两路消费者互相偷样本。
            # 改为引导用户走已验证的录制文件链路：主窗口录音 → 落盘 .iq → 本面板解码。
            QMessageBox.information(
                self, "实时接收指引",
                "接收卫星云图需要把接收机调到下行频率并录制一个过境段。\n\n"
                "操作步骤：\n"
                "  1. 在主窗口调谐到下方中心频率；\n"
                "  2. 点工具栏「录音」(Ctrl+R) 录制 baseband 到 .iq 文件；\n"
                "  3. 停止录制后切到「录制文件」，选择该 .iq 解码出图。")
            return

        # 离线
        if not self._iq_file:
            QMessageBox.information(self, "选择文件", "请先选择真实录制的 IQ/WAV 文件。")
            return
        self.progress.setVisible(True); self.progress.setValue(0)
        params = {
            "family": sat.get("family", "gk2a"),
            "satellite": self.sat_combo.currentText(),
            "iq_file": self._iq_file,
            "out_png": out_png,
            "sps": max(1, round(float(self.sr_edit.text()) * 1e6 /
                               (float(self.sym_edit.text()) * 1e3 or 1))),
            "sample_rate": float(self.sr_edit.text()) * 1e6,
        }
        self._start_worker(params)

    # ------------------------------------------------------------------
    def stop_decode(self):
        """停止接收/解码。"""
        if self._worker_obj is not None:
            self._worker_obj.request_stop()
        if self._worker is not None:
            self._worker.quit(); self._worker.wait(1500)
        self._on_idle()

    # ------------------------------------------------------------------
    def _start_worker(self, params: Dict):
        self._worker = QThread()
        self._worker_obj = DecodeWorker(params)
        self._worker_obj.moveToThread(self._worker)
        self._worker.started.connect(self._worker_obj.run)
        self._worker_obj.progress.connect(self._on_progress)
        self._worker_obj.failed.connect(self._on_failed)
        self._worker_obj.finished.connect(self._on_result)
        self._worker_obj.finished.connect(self._worker.quit)
        self._worker.finished.connect(self._on_idle)
        self._worker.start()
        self.start_btn.setEnabled(False); self.stop_btn.setEnabled(True)
        self._update_status(extra="解码中 ...")

    def _on_idle(self):
        self.stop_btn.setEnabled(False)
        self.progress.setVisible(False)
        if self._worker is not None:
            self._worker = None; self._worker_obj = None
        self._refresh_state()

    def _on_progress(self, text: str):
        self._update_status(extra=text)

    def _on_failed(self, text: str):
        self._set_placeholder("解码失败")
        self._update_status(extra=f"错误: {text.splitlines()[0]}")
        QMessageBox.critical(self, "解码错误", text)

    def _on_result(self, res: dict):
        if res.get("ok") and res.get("png") and os.path.exists(res["png"]):
            self._current_png = res["png"]
            self._frame_count += 1
            self._show_image(res["png"])
            self.image_title.setText(f"云图 — {res.get('satellite','')}")
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            self.meta_label.setText(
                f"卫星: {res.get('satellite','—')} | "
                f"通道: {res.get('channel','—')} | "
                f"时间: {ts} | "
                f"分辨率: {res.get('width','?')}×{res.get('height','?')} | "
                f"注释: {res.get('annotation','') or '—'}")
            self._update_status(extra=f"出图成功: {os.path.basename(res['png'])}")
        else:
            # 不出图：保持空白占位，如实说明，绝不补假图
            self._set_placeholder("无数据")
            self.meta_label.setText("卫星: — | 通道: — | 时间: — | 分辨率: —")
            self._update_status(extra=f"未出图: {res.get('error','未知原因')}")

    def _show_image(self, path: str):
        pix = QPixmap(path)
        if pix.isNull():
            self._set_placeholder("图像加载失败")
            return
        self.image_label.setPixmap(
            pix.scaled(self.image_label.size(),
                       Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._current_png and os.path.exists(self._current_png):
            self._show_image(self._current_png)

    # ------------------------------------------------------------------
    def _on_enhance_toggled(self, _checked: bool):
        if not (self._current_png and os.path.exists(self._current_png)) or self._is_busy():
            return
        steps = [{"op": ENHANCE_OPS[l]} for l, cb in self.enh_checks.items() if cb.isChecked()]
        if not steps:
            self._show_image(self._current_png); return
        out_png = self._out_png_path("enh")
        try:
            from mbdsdr_ai.tool_registry import ToolRegistry
            reg = ToolRegistry(); reg.register_builtin_tools()
            reg.call("sat_image_enhance", {
                "image_path": self._current_png, "steps": steps,
                "output_path": out_png})
            if os.path.exists(out_png):
                self._current_png = out_png
                self._show_image(out_png)
        except Exception as e:  # noqa: BLE001
            self._update_status(extra=f"后处理失败: {e}")

    # ------------------------------------------------------------------
    def _update_status(self, extra: str = ""):
        sat = self._current_sat()
        if not sat:
            self.status_label.setText(extra or "就绪"); return
        line = (f"{self.freq_edit.text()} MHz | "
                f"{self.sym_edit.text()} ksps | {sat.get('mod','?')} | "
                f"{sat.get('band','')} | 帧/段: {self._frame_count}")
        if extra:
            line += f" | {extra}"
        self.status_label.setText(line)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    from themes import get_theme
    get_theme(DEFAULT_THEME).apply(app)
    w = WeatherPanel()
    w.resize(900, 720)
    w.show()
    print("WeatherPanel launched; sdr_connected =", w._sdr_connected)
    sys.exit(app.exec())

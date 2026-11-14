# SPDX-License-Identifier: MIT
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
                 → compose_visible_image（RFC 2435 段重组 + K.3 熵解码）→ PNG；
                 段不全/解码失败如实报帧数与原因，不出假图
      FY-4/3 : fengyun_sat 真实模块（DVB-S2 PL 同步 / HRPT 帧同步）；面板对 raw IQ
                 的端到端出图未接通时如实标注，绝不补假云图
  - 图像处理（可选后处理）：sat_image_processing.sat_image_enhance
                 （中值/直方图均衡/白平衡/Kuwahara）

红线：
  * 无 SDR 连接且无录制文件 → 图像区空白，仅显示「未连接 / 无数据」占位文字。
  * 不内置任何默认/示例/合成云图，不 np.random 生成假图，不从 URL 拉示例图。
  * 「实时 SDR」档位不在此面板边收边解，只是录音指引；该档位「开始解码」
    恒置灰（接了 SDR 也不点亮），文案不许写成「边收边解」。

配色（低饱和默认主题，与 themes.py 一致；面板局部强调色直接取任务给定值）：
  米白 #F5F3EF / 蓝灰 #5B7B8C / 橙 #C4845C / 绿 #6BA89A / 红 #B85C5C
"""
from __future__ import annotations

import logging
import os
import sys
import time
import wave
from typing import Dict, List, Optional

# 允许 desktop/ 直接跑，也允许被 main_window import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

try:  # pragma: no cover - 失败消息整形（clean_exc 战役先例），缺失时退化原文
    from mbdsdr_ai.tool_registry import clean_exc as _clean_exc
except Exception:  # noqa: BLE001
    def _clean_exc(e):  # type: ignore[misc]
        return str(e)

from PySide6.QtCore import Qt, QThread, Signal, QObject  # noqa: E402
from PySide6.QtGui import QPixmap, QFont  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QPushButton,
    QLabel, QCheckBox, QFrame, QFileDialog, QGroupBox, QMessageBox,
    QLineEdit, QProgressBar, QListWidget, QListWidgetItem,
)

from themes import get_theme, DEFAULT_THEME  # noqa: E402
from readouts import format_frequency_hz  # noqa: E402
# 采样率语法与设备面板同源：同一个字段名不允许两种写法（见 B1）。
from device_panel import parse_sample_rate  # noqa: E402


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

#: 解码产物目录：**用户目录**，不写回源码树。
#: 2026-11-11（P2-06）：旧实现把 PNG 写进 ``<repo>/mbdsdr_ai/artifacts/``，
#: 跑一次解码就污染工作区（git status 多一堆未跟踪文件，且清理时容易误删
#: 源码树里的东西）。输出与用户数据一律落 ``~/.mbdsdr/``（与 adsb_panel 的
#: ``~/.mbdsdr/adsb`` 同口径）。
_OUT_DIR = os.path.expanduser(os.path.join("~", ".mbdsdr", "weather"))

logger = logging.getLogger(__name__)


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
            self.failed.emit(_clean_exc(e))

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
            st = parser.stats()
            self._emit(
                ok=False, frames=len(vcdus),
                error=("VCDU 已解出但 LRIT 图像段未凑齐（录制时长过短或信号弱）；"
                       f"丢帧 {st['vcdus_missing']}、断链 {st['continuity_breaks']} 次、"
                       f"丢文件 {st['files_dropped']}、长度异常 {st['session_len_mismatch']}、"
                       f"CRC 失败 {st['tpdu_crc_fail'] + st['session_crc_fail']}"),
                meta={"reassembly": st})
            return
        from PIL import Image
        arr = np.asarray(got.pixels, dtype=np.uint8).reshape(got.height, got.width)
        Image.fromarray(arr, mode="L").save(out_png)
        self._emit(ok=True, png=out_png, width=got.width, height=got.height,
                   annotation=got.annotation, channel="GOES ABI",
                   frames=len(vcdus),
                   meta={"image_identifier": got.image_identifier,
                         "reassembly": parser.stats()})

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
            lines = res.get("lines_aligned")
            lock = res.get("lock_ratio")
            self._emit(ok=False, error=f"APT 未锁相: {res.get('reason')} "
                                       f"(对齐行 {'—' if lines is None else lines}, "
                                       f"lock {'—' if lock is None else lock})")
            return
        paths = apt.save_apt_png(res, out_prefix)
        self._emit(ok=True, png=paths["combo"],
                   width=res["image_a"].shape[1] * 2, height=res["image_a"].shape[0],
                   annotation=f"APT lock={res['lock_ratio']}", channel="APT A+B",
                   frames=res["lines_aligned"],
                   meta={"duration_s": res["duration_s"], "paths": paths})

    # ---- Meteor-M2 LRPT：真录链优先，旧合成级链作回退 --------------------
    # 2026-11-11（P1-23）：`lrpt_pipeline` 的真录链此前只被测试用，面板一直走
    # `meteor_sat` 的简化帧链——真录解出的 CADU 帧首是 CCSDS ASM，图像层只认
    # 3B 1DFCDC，于是真录必然报「帧约定不一致」、永远不出图。现改为先走
    # 真录链（重采样→软判决→ASM→擦除辅助 RS→woven→M_PDU→MCU→RGB），
    # 它解不出图时再回退旧链，并把真录链的阶段化原因一起告诉用户。
    # 2026-11-06 旧注释（保留作背景）：旧实现把 None 一律报成「段不全或数据
    # 损坏」，用户会以为信号差，其实是第一帧就被全部判成"无同步"。
    def _run_meteor(self):
        from mbdsdr_ai import lrpt_pipeline as lp
        from mbdsdr_ai import meteor_sat as ms
        path = self._p["iq_file"]
        fs = float(self._p.get("sample_rate", 1e6))
        self.progress.emit(f"读取 Meteor IQ {os.path.basename(path)} ...")
        iq = _load_complex64(path).astype(np.complex128)

        self.progress.emit(
            f"IQ {iq.size/1e6:.1f} M 采样 @ {fs/1e6:.3f} MS/s → LRPT 真录链 ...")
        try:
            real = lp.decode_recording_to_image(iq, fs)
        except Exception as e:  # noqa: BLE001 - 回退旧链前必须如实记录
            real = lp.RecordingImageResult(
                stage="exception", stats={"chain": "lrpt_pipeline.real"},
                error=f"真录链异常：{type(e).__name__}: {e}")
        if real.success and real.rgb is not None:
            from PIL import Image
            h, w = real.rgb.shape[:2]
            out_png = self._p.get("out_png") or os.path.join(
                os.path.dirname(self._p["iq_file"]), "meteor_recording.png")
            Image.fromarray(real.rgb).save(out_png)
            st = real.stats
            lock = st.get("demod_lock") or {}
            lock_txt = ("已锁" if lock.get("locked") else
                        "曾锁后失锁" if lock.get("locked_once") else "未锁")
            self._emit(
                ok=True, png=out_png, width=w, height=h,
                annotation=(f"链路：真录链 | LRPT {w}x{h}"
                            f"（VCDU {st.get('n_valid_vcdu', 0)} / "
                            f"图像包 {st.get('n_image_packets', 0)}）"
                            f"| 载波锁：{lock_txt}"),
                channel="Meteor-M2 LRPT",
                frames=int(st.get("n_valid_vcdu", 0)),
                link="真录链",
                meta={"chain": st})
            return
        real_reason = real.error or f"真录链在 {real.stage} 阶段未出图"
        self.progress.emit(f"真录链未出图（{real_reason}）→ 回退旧链 ...")

        params = ms.get_satellite_params("meteor_m2_hrpt")
        if params is None:
            self._emit(ok=False, error="meteor_sat 未找到 meteor_m2_hrpt 参数")
            return
        self.progress.emit("QPSK→去交织→Viterbi→CCDB 解扰→CADU 提取 ...")
        cadus = ms.demodulate_lrpt(iq, fs, params)
        if not cadus:
            self._emit(ok=False, frames=0,
                       error=("未解出任何 LRPT CADU 帧（信号弱/参数不符），无图可合成；"
                              f"真录链：{real_reason}"))
            return
        self.progress.emit(f"已解出 {len(cadus)} 帧 → 帧约定诊断 ...")
        probe = ms.probe_cadu_sync(cadus)
        if not probe["compose_compatible"]:
            head = probe["head_hex"][0] if probe["head_hex"] else "—"
            self._emit(
                ok=False, frames=len(cadus), meta={"frame_probe": probe},
                error=(f"已真实解出 {len(cadus)} 个 LRPT CADU 帧，但帧首 "
                       f"{head}… 属于「{probe['best'] or '未知'}」口径，"
                       "与图像重组器期待的 1DFCDC 简化帧不一致；"
                       f"真录链：{real_reason}"))
            return
        self.progress.emit(
            f"帧约定对齐（{probe['counts']['lrpc_simplified_3b']} 帧）"
            " → RFC 2435 段重组 + 熵解码 ...")
        img = ms.compose_visible_image(cadus)
        if img is None:
            self._emit(ok=False, frames=len(cadus),
                       meta={"frame_probe": probe},
                       error=(f"已真实解出 {len(cadus)} 个 LRPT CADU 帧；"
                              "但段重组/熵解码未得到完整图像"
                              f"（段不全或数据损坏）；真录链：{real_reason}"))
            return
        import numpy as _np
        from PIL import Image
        arr = _np.asarray(img)
        out_png = self._p.get("out_png") or os.path.join(
            os.path.dirname(self._p["iq_file"]), "meteor_composed.png")
        Image.fromarray(arr).save(out_png)
        # 旧链回退**必须可见**：annotation 是持久字段，用户从图上就能看出
        # 「这次不是真录链出的图」，避免哪天链路悄悄降级还以为一切正常。
        self._emit(ok=True, png=out_png, width=arr.shape[1], height=arr.shape[0],
                   annotation=(f"链路：旧链回退 | LRPT 合成 "
                               f"{arr.shape[1]}x{arr.shape[0]}"
                               f"（真录链原因：{real_reason}）"),
                   channel="Meteor-M2 LRPT", frames=len(cadus), link="旧链回退")

    # ---- FY-4 / FY-3：调用真实模块，raw IQ 端到端出图未接通则如实报告 ----
    def _run_fengyun(self):
        from mbdsdr_ai import fengyun_sat as fy
        path = self._p["iq_file"]
        fam = self._p["family"]
        if fam == "fy4":
            # 库层 decode_fy4_iq_to_image 是**已实现**的经典 CCSDS 链路
            # （IQ → ASM 同步 → Viterbi → 解扰 → VCDU → 段重组 → PNG），
            # tests/test_fy4_iq_chain.py 对 bpsk/qpsk 两种体制做逐像素往返。
            # 旧实现直接回绝「raw IQ 不支持」，属于库有能力、面板说没有。
            # 诚实边界：真实 FY-4 下行是 DVB-S2，本链路对经典 CCSDS 录制/合成
            # 数据有效；DVB-S2 的 PL 符号同步仍另有一条路，不在本分支冒充。
            out_png = self._p.get("out_png") or os.path.join(
                os.path.dirname(path), "fy4_composed.png")
            self.progress.emit(f"读取 FY-4 IQ {os.path.basename(path)} (complex64) ...")
            iq = _load_complex64(path)
            sps = int(self._p.get("sps", 4) or 4)
            preferred = str(self._p.get("scheme", "") or "").lower()
            schemes = ([preferred] if preferred in ("bpsk", "qpsk")
                       else ["bpsk", "qpsk"])
            attempts = []
            for scheme in schemes:
                self.progress.emit(
                    f"FY-4 CCSDS 链路：{scheme.upper()} / sps={sps} → ASM 同步 → "
                    f"Viterbi → 解扰 → 段重组 ...")
                res = fy.decode_fy4_iq_to_image(
                    iq, out_png, scheme=scheme, sps=sps,
                    shaping="rrc", alpha=0.35)
                if res.get("success"):
                    self._emit(
                        ok=True, png=res.get("png_path", out_png),
                        width=int(res.get("width", 0)),
                        height=int(res.get("height", 0)),
                        annotation=(f"FY-4 经典 CCSDS {scheme.upper()} 合成 "
                                    f"{res.get('width')}x{res.get('height')}"),
                        channel="FY-4", frames=int(res.get("n_images", 0)),
                        meta={"scheme": scheme, "sps": sps,
                              "stats": res.get("stats"),
                              "note": ("经典 CCSDS 链路；真实 FY-4 下行是 DVB-S2，"
                                       "PL 符号同步不在本链路内")})
                    return
                stage = res.get("stage", "?")
                diag = res.get("diagnostic") or {}
                attempts.append(f"{scheme}: {stage} / {res.get('error', '')}"
                                + (f"（{diag.get('cause')}）" if diag.get("cause") else ""))
            self._emit(
                ok=False, frames=0, stage="decode", error_code="no_image",
                error="FY-4 IQ 未解出完整图像（不返回半截图）：" + "；".join(attempts),
                meta={"raw_iq_supported": True, "attempts": attempts})
            return
        self.progress.emit(f"读取录制 {os.path.basename(path)} ...")
        with open(path, "rb") as stream:
            raw = stream.read()
        if fam == "fy3":
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

    #: 解码终态（原样转发 worker 的 result dict）。主窗口据此把真录链自带的
    #: FEC 纠错统计送进状态面板质量行（12.1）；面板本身不解释这些数字。
    decode_result = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._c = _theme_colors()
        self._iq_file: Optional[str] = None
        self._current_png: Optional[str] = None
        self._sdr_connected = False
        self._worker: Optional[QThread] = None
        self._worker_obj: Optional[DecodeWorker] = None
        self._frame_count = 0
        #: 最近一次解码的终态（出图成功 / 未出图 / 错误）。worker 的
        #: ``QThread.finished`` → ``_on_idle`` → ``_refresh_state`` 会在失败
        #: 弹窗自转事件循环期间排队执行，旧实现用「就绪」把它冲掉。
        self._terminal_status = ""
        #: 本轮的「卫星制式 / 文件」身份：终态文案必须自证是哪一次，否则先失败
        #: A 再失败 B 后，用户分不清状态行这条属于哪个文件。
        self._run_identity: str = ""
        #: 仍是状态行主体的那次失败；被下一条终态覆盖时移入保留位。
        self._pending_failure: Optional[dict] = None
        #: 已被最近一条终态覆盖掉的失败（时间/身份）。保留最近 = 丢信息，
        #: 丢掉的必须可见，所以状态行附「另有 N 次失败」。
        self._discarded_failures: List[dict] = []
        self._build_ui()
        self._on_satellite_changed(0)
        self._load_history()
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
        self.src_combo.setToolTip(
            "录制文件：解码已落盘的 IQ/WAV；"
            "实时 SDR：本面板不直接收（不与主窗口抢环形缓冲），只按录音步骤引导")
        self.src_combo.addItems(["录制文件", "实时 SDR"])
        self.src_combo.currentIndexChanged.connect(self._refresh_state)
        form.addRow("数据来源", self.src_combo)

        # 文件选择
        file_row = QHBoxLayout()
        self.file_label = QLabel("未选择文件")
        self.file_label.setStyleSheet(f"color:{self._c['text_secondary']};")
        self.file_btn = QPushButton("选择录制文件...")
        self.file_btn.setToolTip(
            "选择要解码的录制文件：IQ 录制用 .iq/.cfile/.raw，NOAA APT 录音用 .wav")
        self.file_btn.clicked.connect(self._on_pick_file)
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(self.file_btn)
        file_w = QWidget(); file_w.setLayout(file_row)
        form.addRow("录制文件", file_w)

        # 中心频率：只读展示。解码走录制文件里的基带，不读这个值——留成可编辑
        # 就是一个「能敲、敲了没用、还不说话」的框（C3）。值来源：SATELLITES
        # 制式表，由 _on_satellite_changed 填入。
        self.freq_edit = QLineEdit()
        self.freq_edit.setReadOnly(True)
        self.freq_edit.setToolTip(
            "中心频率由所选卫星制式预设，只读；它只用于状态行标注与核对录音时"
            "的调谐，解码走录制文件的基带，不读这个值。要换频点请回主窗口调谐"
            "后重新录音")
        form.addRow("中心频率 (制式预设)", self.freq_edit)
        self.sym_edit = QLineEdit()
        self.sym_edit.setToolTip(
            "卫星信号的符号率（ksps）；要与所选制式匹配，不确定就用制式默认值")
        form.addRow("符号率", self.sym_edit)
        self.sr_edit = QLineEdit()
        self.sr_edit.setToolTip(
            "录制文件的采样率（MS/s）；必须与录制时一致，不一致会解码失败")
        form.addRow("采样率 (MS/s)", self.sr_edit)
        # 三个输入框统一吃主题色：此前只靠全局 QSS，面板底色与控件底色同为
        # card_2 时看起来就是一块块硬黑砖，与周围割裂（2026-11-10 用户实指）。
        for _w in (self.freq_edit, self.sym_edit, self.sr_edit):
            _w.setStyleSheet(
                "QLineEdit {"
                f" background-color:{self._c.get('card', self._c['bg_alt'])};"
                f" color:{self._c['text']};"
                f" border:1px solid {self._c['border']};"
                " border-radius:6px; padding:4px 8px;"
                "}")
        cl.addLayout(form)

        # 按钮行
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始解码")
        self.start_btn.setObjectName("recordButton")
        self.start_btn.setToolTip(
            "解码所选录制文件（.iq/.wav）并在右侧出图，失败时下方写明原因；"
            "「实时 SDR」档位不在此面板解码，只给「主窗口录音 → 切录制文件」的指引")
        self.start_btn.clicked.connect(self.start_decode)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setToolTip(
            "停止当前解码；已经生成的图会保留，想重来再点「开始解码」")
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

        # 产物：另存为 + 历史列表。输出目录是用户目录（_OUT_DIR），
        # 不再污染源码树；历史列表让用户能找回前几次的解码/后处理结果。
        self.save_btn = QPushButton("另存为 PNG...")
        self.save_btn.setToolTip(
            "把当前云图另存到你选择的位置；保存成败都会写在状态行")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_current)
        cl.addWidget(self.save_btn)

        out_box = QGroupBox("历史产物（解码输出到 ~/.mbdsdr/weather）")
        ol = QVBoxLayout(out_box)
        ol.setContentsMargins(6, 6, 6, 6)
        self.history = QListWidget()
        self.history.setMaximumHeight(110)
        self.history.setToolTip("以往解码/后处理的产物，点击可在右侧预览")
        self.history.itemClicked.connect(self._on_history_clicked)
        ol.addWidget(self.history)
        self.history_hint = QLabel("还没有产物\n解码完成后会列在这里")
        self.history_hint.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.history_hint.setStyleSheet(
            f"color:{self._c['text_secondary']};font-size:11px;")
        ol.addWidget(self.history_hint)
        cl.addWidget(out_box)

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
        freq = sat.get("freq_mhz")
        sym = sat.get("symrate_ksps")
        srate = sat.get("srate_msps")
        self.freq_edit.setText("" if freq is None else f"{float(freq):.3f}")
        self.sym_edit.setText("" if sym is None else f"{float(sym):.1f}")
        self.sr_edit.setText("" if srate is None else f"{float(srate):.3f}")
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
        if realtime:
            # 本面板不做边收边解（见 start_decode 注释：会与主窗口统一 IQ 轮询
            # 抢同一环形缓冲），只给「先录音、再切录制文件」的指引。
            # 因此实时档位「开始解码」恒置灰——有连接也不点亮，避免按钮承诺
            # 一个本面板做不到的动作。
            self.start_btn.setEnabled(False)
        elif busy:
            self.start_btn.setEnabled(False)
        else:
            self.start_btn.setEnabled(self._iq_file is not None)
        self.stop_btn.setEnabled(busy)
        self.save_btn.setEnabled(
            bool(self._current_png and os.path.exists(self._current_png)))

        if no_hw:
            self._set_placeholder("未连接SDR设备 — 请接好硬件，或切到「录制文件」离线解码")
            self._update_status(extra="未连接SDR设备")
        elif realtime and not busy:
            self._set_placeholder(
                "实时档位不在此面板解码 — 主窗口调谐 → 录音(Ctrl+R) → "
                "切「录制文件」选该文件解码")
            self._update_status(extra="实时档位只给录音指引，不直接接收")
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
        """产物路径：用户目录（``~/.mbdsdr/weather``），**绝不写回源码树**。"""
        os.makedirs(_OUT_DIR, exist_ok=True)
        return os.path.join(_OUT_DIR, f"weather_{tag}_{int(time.time())}.png")

    # ------------------------------------------------------------------
    def start_decode(self):
        """开始解码（离线录制文件 或 实时 SDR 落盘后解码）。"""
        if self._is_busy():
            # 按钮已置灰，程序化调用仍会进来：给出回执，别让这次开始无声消失。
            self._update_status(extra="解码进行中（忽略重复开始，可先点「停止」）")
            return
        realtime = self._is_realtime()
        sat = self._current_sat()

        if realtime:
            # 本面板不做边收边解：另起 read_samples 定时器会与主窗口统一 IQ 轮询
            # （50ms 一次 read_samples）抢同一环形缓冲，两路消费者互相偷样本。
            # 实时档位只引导「主窗口录音 → 落盘 → 切录制文件解码」，因此这里
            # 不弹模态承诺「开始接收」，只在面板内说明下一步（按钮本身已置灰，
            # 本分支只兜住程序化调用）。
            self._update_status(extra="实时档位只给录音指引，不直接接收")
            self._set_placeholder(
                "实时档位不在此面板解码 — 主窗口调谐 → 录音(Ctrl+R) → "
                "切「录制文件」选该文件解码")
            return

        # 离线
        if not self._iq_file:
            QMessageBox.information(self, "选择文件", "请先选择真实录制的 IQ/WAV 文件。")
            return
        # 先解析参数再进「解码中」：旧实现先亮进度条，裸 float() 抛出的
        # ValueError 直接逃出槽函数（只进 stderr）——用户看到进度 0 卡住、
        # 按钮照旧可点，会以为任务在后台排队。解析失败必须当场说清「没有开始」。
        sr_text = self.sr_edit.text().strip()
        sym_text = self.sym_edit.text().strip()
        if not sr_text or not sym_text:
            QMessageBox.warning(
                self, "参数不完整", "采样率与符号率都不能为空，未开始解码。\n"
                "采样率按 MS/s（如 2.048，也可写 2.048M），"
                "符号率按 ksps（如 72.0）。")
            self._update_status(extra="采样率/符号率为空，未开始解码")
            return
        try:
            # 与设备面板同一份语法（parse_sample_rate）：本栏标签是 MS/s，
            # 所以纯数字按 MS/s 换算，2.048M / 2048000Hz 这类带单位写法也认。
            sr_hz = parse_sample_rate(sr_text, default_scale=1e6)
        except (ValueError, TypeError):
            QMessageBox.warning(
                self, "采样率无效",
                f"采样率“{sr_text}”无法识别，未开始解码。\n"
                "请写 2.048（按 MS/s）、2.048M 或 2048000Hz 中的一种。")
            self._update_status(extra="采样率无效，未开始解码")
            return
        sr_msps = sr_hz / 1e6
        # 本栏按 MS/s 计：2048000 会被读成 2.048 THz——这是「照设备面板按 Hz
        # 填」的典型写法。直接拒绝并写清两种写法，不静默算成一个巨值。
        if sr_msps > 200:
            QMessageBox.warning(
                self, "采样率超出范围",
                f"采样率“{sr_text}”按 MS/s 算是 {sr_msps:g} MS/s，超出常见范围，"
                "未开始解码。\n"
                "本栏按 MS/s 计：2.048 表示 2.048 MS/s；要写 Hz 请带单位，"
                "如 2048000Hz。")
            self._update_status(extra="采样率超出范围，未开始解码")
            return
        try:
            sym_ksps = float(sym_text)
        except ValueError:
            QMessageBox.warning(
                self, "符号率无效",
                f"符号率“{sym_text}”不是数字，未开始解码。\n"
                "请填纯数字（单位 ksps），如 72.0；不要带 k/ksps 后缀。")
            self._update_status(extra="符号率无效，未开始解码")
            return
        if sr_msps <= 0 or sym_ksps <= 0:
            QMessageBox.warning(
                self, "参数无效",
                f"采样率 {sr_msps:g} MS/s、符号率 {sym_ksps:g} ksps 都必须为正数，"
                "未开始解码。")
            self._update_status(extra="采样率/符号率必须为正数，未开始解码")
            return
        self.progress.setVisible(True); self.progress.setValue(0)
        out_png = self._out_png_path("decode")
        params = {
            "family": sat.get("family", "gk2a"),
            "satellite": self.sat_combo.currentText(),
            "iq_file": self._iq_file,
            "out_png": out_png,
            "sps": max(1, round(sr_hz / (sym_ksps * 1e3))),
            "sample_rate": sr_hz,
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
        # 新一轮解码开始：上一轮终态退出状态行主体，但失败不许静默消失，
        # 转入「另有 N 次失败」的保留位（时间/身份仍在）。
        if self._pending_failure is not None:
            self._discarded_failures.append(self._pending_failure)
            self._pending_failure = None
        self._terminal_status = ""
        self._run_identity = self._identity_of(params)
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

    def _identity_of(self, params: Optional[Dict] = None) -> str:
        """「卫星制式 / 录制文件」：终态文案的身份，缺项写「?」。"""
        if params:
            sat = str(params.get("satellite") or "?")
            iq = str(params.get("iq_file") or "")
        else:
            sat = self.sat_combo.currentText() or "?"
            iq = self._iq_file or ""
        return f"{sat} / {os.path.basename(iq) if iq else '?'}"

    def _record_failure(self, prefix: str, reason: str, detail: str) -> None:
        """记一次失败：状态行挂最近一条，更早的进保留位，完整正文进日志。

        「保留最近」本质是丢信息，丢掉的必须可见：被本条覆盖的那次失败带
        时间与身份留在 ``_discarded_failures``，由状态行附「另有 N 次失败」。
        """
        if self._pending_failure is not None:
            self._discarded_failures.append(self._pending_failure)
        identity = self._run_identity or self._identity_of()
        self._pending_failure = {
            "time": time.strftime("%H:%M:%S"),
            "identity": identity,
            "reason": reason,
        }
        self._terminal_status = f"{prefix}: {reason} · {identity}"
        logger.warning("气象解码失败（%s）%s", identity, detail)

    def _discarded_note(self) -> str:
        """被最近一条终态覆盖掉的失败：时间与身份必须仍可见。"""
        if not self._discarded_failures:
            return ""
        last = self._discarded_failures[-1]
        return (f"另有 {len(self._discarded_failures)} 次失败"
                f"（{last['time']} {last['identity']}）")

    def _on_failed(self, text: str):
        self._set_placeholder("解码失败")
        self._record_failure("错误", text.splitlines()[0], text)
        self._update_status()
        QMessageBox.critical(self, "解码错误", text)

    def _on_result(self, res: dict):
        # 12.1：先转发原始终态（成功/失败都发），质量行由接收方决定怎么用。
        self.decode_result.emit(dict(res or {}))
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
            self._terminal_status = (
                f"出图成功: {os.path.basename(res['png'])}"
                + (f" | 链路: {res['link']}" if res.get("link") else ""))
            if self._pending_failure is not None:
                # 成功覆盖了上一次失败：转保留位，状态行仍会提它一句。
                self._discarded_failures.append(self._pending_failure)
                self._pending_failure = None
            self._update_status()
            self.save_btn.setEnabled(True)
            self._load_history()
        else:
            # 不出图：保持空白占位，如实说明，绝不补假图
            self._set_placeholder("无数据")
            self.meta_label.setText("卫星: — | 通道: — | 时间: — | 分辨率: —")
            reason = str(res.get("error", "未知原因"))
            self._record_failure("未出图", reason, reason)
            self._update_status()

    def _show_image(self, path: str):
        pix = QPixmap(path)
        if pix.isNull():
            self._set_placeholder("图像加载失败")
            return
        self.image_label.setPixmap(
            pix.scaled(self.image_label.size(),
                       Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ------------------------------------------------------------------
    # 产物：另存为 / 历史列表
    # ------------------------------------------------------------------
    def _load_history(self):
        """刷新历史产物列表（只认 ``_OUT_DIR`` 下 weather_*.png）。"""
        self.history.clear()
        names = []
        if os.path.isdir(_OUT_DIR):
            names = sorted(
                (f for f in os.listdir(_OUT_DIR)
                 if f.startswith("weather_") and f.endswith(".png")),
                reverse=True)[:50]
        for f in names:
            QListWidgetItem(f, self.history)
        has_items = self.history.count() > 0
        self.history.setVisible(has_items)
        self.history_hint.setVisible(not has_items)

    def _on_history_clicked(self, item: "QListWidgetItem"):
        path = os.path.join(_OUT_DIR, item.text())
        if not os.path.exists(path):
            self._update_status(extra=f"产物已不存在: {item.text()}")
            return
        self._current_png = path
        self._show_image(path)
        self.image_title.setText(f"云图 — 历史产物 {item.text()}")
        self.save_btn.setEnabled(True)
        self._update_status(extra=f"预览历史产物: {item.text()}")

    def _save_current(self):
        """把当前云图另存为 PNG；**成败都要在状态行里说人话**（不许静默）。"""
        if not (self._current_png and os.path.exists(self._current_png)):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存云图", os.path.basename(self._current_png),
            "PNG (*.png)")
        if not path:
            return
        target_name = os.path.basename(str(path))
        try:
            from PIL import Image
            Image.open(self._current_png).save(path)
        except Exception as exc:  # noqa: BLE001 - 失败必须可见
            self._update_status(
                extra=f"保存 PNG 失败: {target_name}（{type(exc).__name__}）")
            return
        self._update_status(extra=f"云图已另存: {target_name}")

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
                self._load_history()
        except Exception as e:  # noqa: BLE001
            self._update_status(extra=f"后处理失败: {_clean_exc(e)}")

    # ------------------------------------------------------------------
    def _update_status(self, extra: str = ""):
        sat = self._current_sat()
        if not sat:
            text = extra or self._terminal_status or "就绪"
            note = "" if extra else self._discarded_note()
            self.status_label.setText(f"{text} | {note}" if note else text)
            return
        # 频率读数不再就地拼「{输入框} MHz」：输入是 MHz 字符串，是数值才按
        # 1 Hz 口径格式化（6 位小数 MHz）；不是数值就原样保留，不吞掉用户输入。
        raw_freq = self.freq_edit.text()
        try:
            freq_text = format_frequency_hz(float(raw_freq) * 1e6)
        except ValueError:
            freq_text = f"{raw_freq} MHz"
        line = (f"{freq_text} | "
                f"{self.sym_edit.text()} ksps | {sat.get('mod','?')} | "
                f"{sat.get('band','')} | 帧/段: {self._frame_count}")
        if extra:
            line += f" | {extra}"
        elif self._terminal_status:
            # 终态只在这里回显：_refresh_state 的排队回调不得用「就绪」
            # 把「出图成功 / 未出图 / 错误」冲掉。
            line += f" | {self._terminal_status}"
            note = self._discarded_note()
            if note:
                # 保留最近 = 丢掉更早那条；丢掉的必须可见。
                line += f" | {note}"
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

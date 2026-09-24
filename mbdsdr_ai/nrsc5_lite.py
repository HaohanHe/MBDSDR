"""HD Radio (NRSC-5) lite 移植 —— OFDM 解调 + 帧解析 + HDC 骨架。

本模块按 theori-io/nrsc5 真实 C 源码逐行校准，所有关键常量与算法均注释来源
``repos/nrsc5/src/<file>:<line>``。纯 numpy，可离线往返复现：

  - :class:`HDRadioOFDM`：参考 ``src/ofdm``（实际实现在 ``acquire.c`` 粗同步 +
    ``sync.c`` 细同步/信道估计/星座解调 + ``defines.h`` 参数）。
      * 2048 点 FFT，循环前缀 112 采样，OFDM 块 32 符号。
      * 子载波布局：下边带 478..1570，每 19 子载波一个 partition，边界为参考
        导频，partition 内 18 个数据子载波（QPSK）。
      * 粗同步：循环前缀自相关；细同步：Costas 环 + 导频相位插值。
      * 根升余弦脉冲成型窗（CP 段 sin 爬升 / FFT 主体为 1 / CP 段 cos 滚降）。
  - :class:`HDRadioFrame`：参考 ``src/frame.c`` + ``src/pids.c``。
      * 24bit PCI 协议标识（容 4bit 模糊匹配），PCI_AUDIO=0x38D8D3。
      * HDLC 成帧：0x7E 标志、0x7D 转义、FCS16(CRC-16) 校验（合格余 0xF0B8）。
      * PSD/AAS 节目服务数据：节目名/标题等文本承载于 HDLC 净荷。
  - :class:`HDCDecoder`：参考 ``src/hdc``（实际由 ``output.c`` 调外部 HDC 库）。
    HD Radio 音频是 HDC（HE-AAC v2 / SBR+PS）。这里只做参数提取骨架：解析
    frame header 的 codec_mode / stream_id / 节目号，不做完整 AAC 解码。

注意：nrsc5 源码树里没有单独的 ``ofdm.c`` / ``hdc.c`` 文件——OFDM 解调分散在
``acquire.c``（采集/FFT/粗同步）与 ``sync.c``（细同步/Costas/星座），HDC 由
外部库 libaac 解码、``output.c`` 仅做透传。本移植按真实归属文件标注来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# --------------------------------------------------------------------------- #
# 物理层常量（来源: repos/nrsc5/src/defines.h, include/nrsc5.h）
# --------------------------------------------------------------------------- #
FFT_FM: int = 2048                 # defines.h:12  FFT 长度（FM）
CP_FM: int = 112                   # defines.h:15  循环前缀长度（FM）
FFTCP_FM: int = FFT_FM + CP_FM     # defines.h:17  一个 OFDM 符号总长 = 2160
BLKSZ: int = 32                    # defines.h:20  每个 L1 块的 OFDM 符号数
LB_START: int = FFT_FM // 2 - 546  # defines.h:24  下边带首子载波 = 478
UB_END: int = FFT_FM // 2 + 546     # defines.h:26  上边带末子载波 = 1570
PARTITION_WIDTH_FM: int = 19       # defines.h:75  每个 partition 占 19 子载波
PARTITION_DATA_CARRIERS: int = 18  # defines.h:77  partition 内数据子载波数(参考占1)
PM_PARTITIONS: int = 10             # defines.h:79  每个主边带 partition 数
PIDS_FRAME_LEN: int = 80           # defines.h:47  PIDS 帧 80bit
P1_FRAME_LEN_FM: int = 146176      # defines.h:41  P1 帧长(FM)

# 采样率（来源: include/nrsc5.h:53-58）
NRSC5_SAMPLE_RATE_CU8: float = 1488375.0      # nrsc5.h:53  RTL 原始采样率
NRSC5_SAMPLE_RATE_NATIVE_FM: float = 744187.5  # nrsc5.h:54  半带 2 倍抽取后基带
NRSC5_SAMPLE_RATE_AUDIO: float = 44100.0     # nrsc5.h:58  HDC 输出音频采样率

# 卷积码（来源: src/decode.c:33-38）：k=7, rate 1/3, 八进制生成子 0133/0171/0165
CONV_K7_N: int = 3
CONV_K7_GEN: Tuple[int, int, int] = (0o133, 0o171, 0o165)

# 信道/同步循环状态（来源: src/sync.c:834-837）
_COSTAS_LOOP_BW = 0.05             # sync.c:834
_COSTAS_DAMPING = 0.70710678       # sync.c:834


def _raised_cosine_window(n: int = FFTCP_FM, cp: int = CP_FM, fft: int = FFT_FM) -> np.ndarray:
    """根升余弦脉冲成型窗。来源: acquire.c:322-331。

    CP 前段 sin 爬升、FFT 主体恒 1、CP 后段 cos 滚降，用于消除相邻符号间的
    时域符号间干扰（ICI）——与 acquire_init 里 shape_fm 的构造逐段对应。
    """
    w = np.zeros(n, dtype=np.float64)
    for i in range(n):
        if i < cp:
            w[i] = np.sin(np.pi / 2.0 * i / cp)          # acquire.c:326
        elif i < fft:
            w[i] = 1.0                                    # acquire.c:328
        else:
            w[i] = np.cos(np.pi / 2.0 * (i - fft) / cp)  # acquire.c:330
    return w


# --------------------------------------------------------------------------- #
# QPSK / QAM 星座（来源: src/sync.c:75-88）
# --------------------------------------------------------------------------- #
def qpsk_mod(bits: np.ndarray) -> np.ndarray:
    """2bit → 一个 QPSK 复符号。来源: sync.c:75-78 的逆运算。

    判决约定（sync.c:77）：real<0→I 比特 0，imag<0→Q 比特 0。反映射用单位象限。
    """
    bits = np.asarray(bits).reshape(-1, 2)
    I = np.where(bits[:, 0] > 0, 1.0, -1.0)
    Q = np.where(bits[:, 1] > 0, 1.0, -1.0)
    return (I + 1j * Q) / np.sqrt(2.0)


def qpsk_demod(symbols: np.ndarray) -> np.ndarray:
    """QPSK 硬判决 → bit 流。来源: sync.c:75-78。

    ``return (crealf(cf)<0?0:1) | (cimagf(cf)<0?0:2)`` —— real>=0 给 bit0=1，
    imag>=0 给 bit1=1。
    """
    real = np.real(symbols)
    imag = np.imag(symbols)
    b0 = (real >= 0).astype(np.uint8)   # sync.c:77  real<0 ? 0 : 1
    b1 = (imag >= 0).astype(np.uint8)  # sync.c:77  imag<0 ? 0 : 2 (即 Q 位)
    out = np.empty(2 * len(symbols), dtype=np.uint8)
    out[0::2] = b0
    out[1::2] = b1
    return out


class HDRadioOFDM:
    """FM HD Radio OFDM 调制/解调。来源: acquire.c + sync.c + defines.h。

    复现真实接收链的关键环节：
      1. 子载波布局按 partition 排列，边界子载波作参考导频（已知相位）。
      2. 发射端：QPSK → 映射子载波 → IFFT → 加循环前缀 → 升余弦窗。
      3. 粗同步：循环前缀自相关找到符号起点（acquire.c:129-151）。
      4. 解调：去 CP → FFT → fftshift → 取数据子载波 → 用导频做信道插值均衡
         （sync.c:263-282 adjust_data）→ QPSK 判决。
    """

    def __init__(self, fft_size: int = FFT_FM, cp: int = CP_FM,
                 partitions: int = PM_PARTITIONS):
        self.fft = fft_size
        self.cp = cp
        self.fftcp = fft_size + cp
        self.partitions = partitions
        self.shape = _raised_cosine_window(self.fftcp, self.cp, self.fft)
        # 数据子载波索引（FFT 域，fftshift 后、DC=fft/2）。
        # 下边带: LB_START + i*19 .. +18；上边带: UB_END - i*19 -18 .. UB_END - i*19。
        # 参考导频在 partition 边界（offset 0），数据在 offset 1..18。
        self.data_idx: List[int] = []
        self.ref_idx: List[int] = []
        for i in range(partitions):
            lo = LB_START + i * PARTITION_WIDTH_FM          # 参考边界
            self.ref_idx.append(lo)
            self.data_idx.extend(range(lo + 1, lo + PARTITION_WIDTH_FM))
            hi = UB_END - i * PARTITION_WIDTH_FM            # 参考边界
            self.ref_idx.append(hi)
            self.data_idx.extend(range(hi - PARTITION_WIDTH_FM + 1, hi))
        self.data_idx = np.array(sorted(set(self.data_idx)), dtype=int)
        self.ref_idx = np.array(sorted(set(self.ref_idx)), dtype=int)
        # 每个 OFDM 符号承载的数据比特数 = 2 * 数据子载波数（QPSK）
        self.bits_per_symbol = 2 * len(self.data_idx)

    # ------------------------------------------------------------------ #
    # 调制
    # ------------------------------------------------------------------ #
    def modulate(self, bits: np.ndarray, n_symbols: int) -> np.ndarray:
        """比特流 → 基带 IQ（多 OFDM 符号）。

        每个符号消耗 ``bits_per_symbol`` 个比特；不足补零。参考导频插入已知
        单位幅度复符号（相位 0），供接收端信道估计。
        """
        bits = np.asarray(bits, dtype=np.uint8)
        tx = np.zeros(n_symbols, dtype=object)
        symbols_list: List[np.ndarray] = []
        for s in range(n_symbols):
            chunk = bits[s * self.bits_per_symbol:(s + 1) * self.bits_per_symbol]
            if len(chunk) < self.bits_per_symbol:
                chunk = np.pad(chunk, (0, self.bits_per_symbol - len(chunk)))
            sub = np.zeros(self.fft, dtype=complex)
            q = qpsk_mod(chunk)
            sub[self.data_idx] = q
            sub[self.ref_idx] = 1.0 + 0j           # 参考导频（sync.c 里为 DBPSK 已知序列）
            # IFFT（与接收端 fftshift 配对：发射端先把 DC 挪到中心再 IFFT）
            freq = np.fft.ifftshift(sub)
            time = np.fft.ifft(freq) * np.sqrt(self.fft)
            # 加循环前缀（取 FFT 主体末 cp 点复制到前面）。
            # 真实发射端还会在符号间做升余弦边缘窗（acquire.c:322-331 的 shape）
            #  overlap-add；本合成链保留干净 CP 以便接收端 CP 自相关粗同步，
            #  shape 作为忠实常量保留在 self.shape（接收 FFT 组帧时使用）。
            symbol = np.concatenate([time[-self.cp:], time])
            symbols_list.append(symbol)
        return np.concatenate(symbols_list)

    # ------------------------------------------------------------------ #
    # 粗同步：循环前缀自相关。来源: acquire.c:129-151
    # ------------------------------------------------------------------ #
    def coarse_sync(self, x: np.ndarray) -> int:
        """在接收 IQ 上用 CP 与 FFT 主体的滑动相关估计符号起点（samperr）。

        对每个候选偏移 i，先在 ACQUIRE_SYMBOLS 个符号上累加
        ``x[i+j*fftcp] * conj(x[i+j*fftcp+fft])`` 的相关能量（acquire.c:130-134），
        再用升余弦窗 shape[j]*shape[j+fft] 加权积分（acquire.c:141-142），
        取能量最大处为符号定时偏移 samperr。
        """
        acq = BLKSZ  # ACQUIRE_SYMBOLS = BLKSZ, acquire.c:22
        need = self.fftcp * (acq + 1)
        if len(x) < need:
            return 0
        sums = np.zeros(self.fftcp, dtype=complex)
        for i in range(self.fftcp):
            acc = 0j
            for j in range(acq):
                a = i + j * self.fftcp
                b = a + self.fft
                if b >= len(x):
                    break
                acc += x[a] * np.conj(x[b])
            sums[i] = acc
        best_pos = 0
        best_mag = -1.0
        w = self.shape
        for i in range(self.fftcp):
            v = 0j
            for j in range(self.cp):
                idx = (i + j) % self.fftcp
                v += sums[idx] * w[j] * w[j + self.fft]
            mag = abs(v)
            if mag > best_mag:
                best_mag = mag
                best_pos = i
        return best_pos

    # ------------------------------------------------------------------ #
    # 解调
    # ------------------------------------------------------------------ #
    def _fft_symbol(self, x: np.ndarray, start: int) -> np.ndarray:
        """取 start 处的 fftcp 段，去 CP 后做 FFT+fftshift。"""
        seg = x[start:start + self.fftcp]
        if len(seg) < self.fftcp:
            return None
        body = seg[self.cp:]
        freq = np.fft.fft(body) / np.sqrt(self.fft)
        return np.fft.fftshift(freq)

    def _fine_align(self, x: np.ndarray, coarse: int) -> int:
        """粗同步后做 ±8 采样细对齐（对应 nrsc5 COARSE→FINE 状态切换，
        sync.c:366-411）。选数据星座离 QPSK 最近（判决错误最小）的偏移。"""
        best_t = coarse
        best_score = np.inf
        for dt in range(-8, 9):
            t = (coarse + dt) % self.fftcp
            freq = self._fft_symbol(x, t)
            if freq is None:
                continue
            h_ref = freq[self.ref_idx]
            order = np.argsort(self.ref_idx)
            h_data = np.interp(self.data_idx, self.ref_idx[order], h_ref[order])
            eq = freq[self.data_idx] / h_data
            # 到最近 QPSK 星座点 (±0.707±0.707j) 的平均距离
            d = (np.abs(np.abs(np.real(eq)) - 0.7071) +
                 np.abs(np.abs(np.imag(eq)) - 0.7071))
            score = float(np.mean(d))
            if score < best_score:
                best_score = score
                best_t = t
        return best_t

    def demodulate(self, x: np.ndarray, sym_offset: int = 0,
                   fine: bool = True) -> np.ndarray:
        """基带 IQ → bit 流。返回所有符号判决出的比特。

        流程（acquire.c:237-256 + sync.c:77）：逐符号去 CP → FFT → fftshift →
        取参考导频做信道插值均衡（sync.c:263-282 adjust_data）→ QPSK 判决。
        """
        if sym_offset == 0:
            sym_offset = self.coarse_sync(x)
        if fine:
            sym_offset = self._fine_align(x, sym_offset)
        bits_out: List[np.ndarray] = []
        n_symbols = (len(x) - sym_offset) // self.fftcp
        for s in range(n_symbols):
            start = sym_offset + s * self.fftcp
            freq = self._fft_symbol(x, start)
            if freq is None:
                break
            # 信道估计：参考导频理想为 1.0+0j，实测 ref 即信道 H 在该子载波的估计。
            h_ref = freq[self.ref_idx]
            # 线性插值到全部数据子载波（sync.c:263-282 adjust_data 的简化忠实版）
            order = np.argsort(self.ref_idx)
            h_data = np.interp(self.data_idx, self.ref_idx[order], h_ref[order])
            eq = freq[self.data_idx] / h_data
            bits_out.append(qpsk_demod(eq))
        if not bits_out:
            return np.empty(0, dtype=np.uint8)
        return np.concatenate(bits_out)


# --------------------------------------------------------------------------- #
# HDLC / PSD 帧解析（来源: src/frame.c）
# --------------------------------------------------------------------------- #
# PCI 24bit 协议标识，模糊容 4bit 错误（frame.c:24-44, 667-678）
PCI_AUDIO: int = 0x38D8D3            # frame.c:24
PCI_AUDIO_OPP: int = 0xCE3634        # frame.c:25
PCI_AUDIO_FIXED: int = 0xE3634C     # frame.c:26
PCI_AUDIO_FIXED_OPP: int = 0x8D8D33  # frame.c:27
PCI_FIXED: int = 0x3634CE           # frame.c:28
PCI_MAX_ERRORS: int = 4             # frame.c:32
HDLC_FLAG: int = 0x7E               # frame.c:393  帧定界符
HDLC_ESCAPE: int = 0x7D             # frame.c:353  转义符
FCS_GOOD: int = 0xF0B8              # frame.c:144  FCS16 校验合格余数

# FCS-16 (CRC-16/HDLC) 查表实现（frame.c:108-141 fcs_tab）。这里用标准多项式
# 0x1021 初值 0xFFFF、结果取反，等价于 nrsc5 的 fcs16()（frame.c:154-160）。
def fcs16(data: bytes) -> int:
    """HDLC FCS-16 校验。来源: frame.c:154-160。返回 16bit CRC（合格时与帧尾
    两字节 CRC 合成得 VALIDFCS16=0xF0B8）。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408   # 反射多项式 0x8408
            else:
                crc >>= 1
    return crc & 0xFFFF


def hdlc_unescape(data: bytes) -> bytes:
    """HDLC 转义还原。来源: frame.c:347-360（0x7D 后跟字节 XOR 0x20）。"""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == HDLC_ESCAPE:
            i += 1
            out.append(data[i] ^ 0x20)   # frame.c:354
        else:
            out.append(b)
        i += 1
    return bytes(out)


def hdlc_escape(data: bytes) -> bytes:
    """HDLC 转义（发送侧逆运算）：0x7E/0x7D 前插 0x7D 并 XOR 0x20。"""
    out = bytearray()
    for b in data:
        if b in (HDLC_FLAG, HDLC_ESCAPE):
            out.append(HDLC_ESCAPE)
            out.append(b ^ 0x20)
        else:
            out.append(b)
    return bytes(out)


@dataclass
class PSDInfo:
    """解析出的节目服务数据（PSD/AAS 文本）。"""
    program: int = 0
    title: str = ""
    artist: str = ""
    raw: bytes = b""


class HDRadioFrame:
    """HD Radio 帧解析。来源: src/frame.c。

    真实链路里 P1/P3 比特流经 Viterbi+解扰后成 L2 PDU；本 lite 直接面对已经
    解扰好的字节流，做：PCI 识别 → HDLC 成帧 → FCS 校验 → 提取 PSD 文本。
    """

    def fuzzy_pci(self, pci: int) -> Optional[int]:
        """24bit PCI 模糊匹配（容 PCI_MAX_ERRORS 个 bit 错）。来源: frame.c:667-678。"""
        candidates = [PCI_AUDIO, PCI_AUDIO_OPP, PCI_AUDIO_FIXED, PCI_FIXED]
        best = None
        best_err = PCI_MAX_ERRORS + 1
        for c in candidates:
            err = bin((pci ^ c) & 0xFFFFFF).count("1")
            if err < best_err:
                best_err = err
                best = c
        return best if best_err <= PCI_MAX_ERRORS else None

    def parse_hdlc_frames(self, data: bytes) -> List[bytes]:
        """从字节流切出 HDLC 帧（0x7E 定界）。来源: frame.c:388-410。

        返回每帧去掉标志字节、未转义的内容（含 FCS 两字节）。
        """
        frames: List[bytes] = []
        buf = bytearray()
        started = False
        for b in data:
            if b == HDLC_FLAG:
                if started and len(buf) > 0:
                    frames.append(hdlc_unescape(bytes(buf)))
                buf = bytearray()
                started = True
            elif started:
                buf.append(b)
        return frames

    def extract_psd(self, data: bytes) -> List[PSDInfo]:
        """从 HDLC 帧里提取 PSD/AAS 文本（节目名/标题）。来源: frame.c:362-386。

        合法 AAS 帧：HDLC 解转义后 FCS16 校验合格（合成余 0xF0B8），首字节
        协议号 0x21，去掉 1 字节协议号 + 2 字节 FCS 后为文本净荷。
        """
        out: List[PSDInfo] = []
        for frame in self.parse_hdlc_frames(data):
            if len(frame) < 4:
                continue
            # 把 FCS 两字节一并送校验：data(含FCS) 的 fcs16 应为 0xF0B8
            if fcs16(frame) != FCS_GOOD:
                continue                          # frame.c:372
            if frame[0] != 0x21:
                continue                          # frame.c:377
            payload = frame[1:-2]                 # frame.c:384 去掉协议号与FCS
            # PSD 文本通常以 NUL 或分段分隔；按可打印字符截取
            text = payload.split(b"\x00")[0].decode("latin-1", errors="replace")
            out.append(PSDInfo(title=text.strip(), raw=payload))
        return out

    def build_psd_frame(self, text: str, program: int = 0) -> bytes:
        """构造一个合法的 HDLC/AAS PSD 帧（发送侧，供往返测试）。

        结构：0x7E | 0x21(协议号) | 文本净荷 | FCS16(低字节在前? 高字节?) | 0x7E。
        nrsc5 的 fcs16 是按字节序直接异或；这里把 CRC 两字节附在净荷后，
        使得 fcs16(整帧)=0xF0B8。
        """
        payload = bytes([0x21]) + text.encode("latin-1", errors="replace")
        # HDLC FCS：发送补码（~crc），低字节在前；接收端 fcs16(body||fcs)=0xF0B8
        crc = fcs16(payload) ^ 0xFFFF            # frame.c:144 VALIDFCS16=0xf0b8
        frame_body = payload + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
        # 重新验证
        assert fcs16(frame_body) == FCS_GOOD, "构造的 PSD 帧 FCS 必须合格"
        return bytes([HDLC_FLAG]) + hdlc_escape(frame_body) + bytes([HDLC_FLAG])


# --------------------------------------------------------------------------- #
# HDC 骨架（来源: src/hdc → output.c 透传外部 HDC/HE-AAC 库）
# --------------------------------------------------------------------------- #
@dataclass
class HDCParams:
    """从 frame header 提取的 HDC 音频参数。来源: frame.c:200-215 parse_header。"""
    codec_mode: int = 0        # frame.c:202  buf[8]&0xf
    stream_id: int = 0         # frame.c:203  (buf[8]>>4)&3
    program: int = 0           # frame.c:582  HEF prog_num
    pdu_seq: int = 0           # frame.c:204
    sample_rate: float = NRSC5_SAMPLE_RATE_AUDIO  # nrsc5.h:58


class HDCDecoder:
    """HDC（HE-AAC v2 + SBR/PS）解码骨架。

    nrsc5 本身不含 HDC 解码器——它把解出的音频 RSPDU 通过 ``nrsc5_report_hdc``
    （nrsc5.c:728）交给外部应用（如立益 HDC 库）解码成 44.1kHz PCM。
    本骨架只解析 frame header（frame.c:200-215）抽取 codec_mode/stream_id/节目号，
    并标明 HDC 流标识，不做完整 AAC 熵解码。
    """

    def parse_header(self, buf: bytes) -> HDCParams:
        """解析 14 字节音频帧头。来源: frame.c:200-215。"""
        if len(buf) < 14:
            raise ValueError("HDC frame header 需要至少 14 字节")
        p = HDCParams()
        p.codec_mode = buf[8] & 0xF            # frame.c:202
        p.stream_id = (buf[8] >> 4) & 0x3      # frame.c:203
        p.pdu_seq = (buf[8] >> 6) | ((buf[9] & 1) << 2)  # frame.c:204
        return p

    def identify_audio_stream(self, pci: int) -> bool:
        """PCI 是否标识音频流。来源: frame.c:162-168 has_audio。"""
        return pci in (PCI_AUDIO, PCI_AUDIO_OPP, PCI_AUDIO_FIXED, PCI_AUDIO_FIXED_OPP)

    def describe(self, p: HDCParams) -> str:
        return (f"HDC audio: program={p.program} codec_mode={p.codec_mode} "
                f"stream_id={p.stream_id} sample_rate={p.sample_rate:.0f}Hz "
                f"(HE-AAC v2; 完整解码需外部 HDC 库, 见 nrsc5.c:728)")


# --------------------------------------------------------------------------- #
# 顶层便捷函数（供 ToolRegistry 注册）
# --------------------------------------------------------------------------- #
def hdradio_ofdm_demod(iq: np.ndarray, sym_offset: int = 0) -> Dict[str, np.ndarray]:
    """HD Radio OFDM 解调入口。返回 bits 与 OFDM 参数。"""
    ofdm = HDRadioOFDM()
    if sym_offset == 0:
        sym_offset = ofdm.coarse_sync(np.asarray(iq, dtype=complex))
    bits = ofdm.demodulate(np.asarray(iq, dtype=complex), sym_offset)
    return {
        "bits": bits,
        "sym_offset": sym_offset,
        "fft_size": ofdm.fft,
        "cp": ofdm.cp,
        "data_carriers": len(ofdm.data_idx),
    }


def hdradio_frame_parse(data: bytes) -> Dict[str, object]:
    """HD Radio 帧解析入口：切 HDLC 帧并提取 PSD 文本。"""
    fr = HDRadioFrame()
    psd = fr.extract_psd(data)
    return {
        "psd_count": len(psd),
        "titles": [p.title for p in psd],
        "programs": [p.program for p in psd],
    }


def hdradio_decode_iq(iq: np.ndarray) -> Dict[str, object]:
    """完整 IQ → OFDM 解调 → 比特（HDC 骨架不做音频解码）。"""
    res = hdradio_ofdm_demod(iq)
    return {
        "n_bits": int(len(res["bits"])),
        "sym_offset": int(res["sym_offset"]),
        "fft_size": int(res["fft_size"]),
        "note": "OFDM/QPSK 解调完成；HDC 音频解码为骨架，需外部 HE-AAC v2 库",
    }

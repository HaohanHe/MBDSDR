# SPDX-License-Identifier: MIT
"""SSDV over CCSDS 级联全链的可复用 TX 合成 / RX 解码层（干净室 MIT）。

本模块把 ``tests/test_phase45_cascade.py`` 里已跑通的合成器与接收链抽出为单一
可复用入口，供 ``tools/onboarding/onboard.py`` 与测试共用——**不在 onboard 里重写
链路**。物理层参数（采样率/符号率/中频偏移/帧长）全部具名传入，不硬编码任何活动
频率/符号率；纯噪声/不可解码诚实返回空，绝不伪造帧或图。

链路::

    RGB ─► SsdvEncoder(DSLWP 218B) ─► 外层 RS(255,223) ─► CCSDS 加扰
        ─► 卷积 K=7 r=1/2 (tail=6 归零) ─► ASM(32b) ─► BPSK 复 IQ   【TX 合成】

    复 IQ ─► demod_bpsk ─► ASM 帧同步 ─► Viterbi(final_state=0) ─► 解扰
        ─► RS(255,223) 逐块 ─► 218B 流 ─► SsdvDecoder(DSLWP) ─► JPEG  【RX】

参考（GPL，只学机制未复制代码）：fsphil SSDV 包格式、CCSDS 131.0-B TM 同步与
信道编码；本仓实现均为 MIT（``fec.py`` / ``ccsds_rx.py`` / ``ssdv_decoder.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .ccsds_rx import (
    CCSDS_ASM_WORD,
    AsmFramer,
    ConvolutionalEncoder,
    ViterbiDecoder,
    bits_to_bytes_msb,
    bytes_to_bits_msb,
)
from .fec import ReedSolomon, Scrambler
from .ssdv_phy import bpsk_modulate_bits, demod_bpsk
from .ssdv_decoder import (
    DIALECT_DSLWP,
    SsdvDecoder,
    SsdvImage,
    SsdvEncoder,
)


@dataclass
class CcsdsSsdvTx:
    """TX 合成产物。"""

    iq: np.ndarray                 # complex64 BPSK 复 IQ
    stream218: bytes              # 原始 DSLWP 218B 包流（校验用）
    frame_bits: int               # ASM 之后的编码比特数（framer 用）
    n_rsblocks: int               # 外层 RS(255) 块数


def synthesize_ssdv_ccsds_iq(rgb: np.ndarray, fs: float, symrate: float,
                             callsign: str, image_id: int = 0,
                             quality: int = 4, mcu_mode: int = 3,
                             f_if: float = 0.0, guard_bits: int = 64) -> CcsdsSsdvTx:
    """RGB → DSLWP 218B 包流 → 外层 RS → 加扰 → 卷积 → ASM → BPSK 复 IQ。

    云内合成/测试用：收发两端 fs/symrate 对齐即确定往返。``callsign`` 由调用方传入
    （合成测试用合成呼号，非任何真实电台）。不硬编码活动参数。
    """
    enc = SsdvEncoder(callsign=callsign, image_id=image_id,
                      quality=quality, mcu_mode=mcu_mode)
    pkts = enc.encode_image_dslwp(rgb)
    stream218 = b"".join(pkts)

    rs = ReedSolomon(nsym=32)
    pad = (-len(stream218)) % 223
    msg = stream218 + b"\x00" * pad
    blocks = [msg[i:i + 223] for i in range(0, len(msg), 223)]
    stream_rs = b"".join(rs.encode(b) for b in blocks)
    n_rsblocks = len(blocks)
    scrambled = Scrambler().scramble(stream_rs)

    pairs = ConvolutionalEncoder().encode_bytes(scrambled, tail=6)
    coded = [b for p in pairs for b in p]
    asm = bytes_to_bits_msb(CCSDS_ASM_WORD.to_bytes(4, "big"))
    pre = bytes_to_bits_msb(b"\x55" * 8)
    # 尾部保护带：真实链路帧间本就有 guard 间隔；给盲定时环路留采样余量，避免
    # warmup 裁剪/可变步长导致凑不满 frame_bits（framer 收集满 frame_bits 后即复位，
    # 保护带被忽略）。
    guard = [0, 1] * (guard_bits // 2)
    tx = np.array(pre + asm + coded + guard, dtype=np.int8)

    iq = bpsk_modulate_bits(tx, fs, symrate, f_if=f_if)
    return CcsdsSsdvTx(iq=iq, stream218=stream218,
                       frame_bits=len(coded), n_rsblocks=n_rsblocks)


@dataclass
class CcsdsSsdvResult:
    """RX 全链旁证（诚实：无 ASM 帧/无包时各计数为 0，不伪造图）。"""

    n_demod_bits: int = 0
    n_asm_frames: int = 0
    #: 盲 CFO 估计值 Hz（f_offset=None 时；显式下变频时为传入值）。
    cfo_est_hz: float = 0.0
    #: 每帧内每个 RS(255) 块纠正的符号数（-1=不可纠）。
    rs_nerrors: List[List[int]] = field(default_factory=list)
    n_packets: int = 0
    image_id: int = -1
    width: int = 0
    height: int = 0
    mcu_count: int = 0
    received_mcus: int = 0
    missing_mcus: List[int] = field(default_factory=list)
    eoi_seen: bool = False
    jpeg: bytes = b""


def ccsds_frame_to_dslwp(frame: bytes) -> tuple[bytes, List[int]]:
    """单个 ASM 后帧字节 → Viterbi(终态0) → 解扰 → RS 逐块 → (218B 流, [nerrors])。

    帧尾 tail=6 已被 flush 归零，Viterbi 用 final_state=0 确定性回溯。RS 不可纠的
    块 nerrors=-1 并照原样放行（后级 CRC 把关），不假装纠正成功。
    """
    fbits = bytes_to_bits_msb(frame)
    if len(fbits) % 2:
        fbits = fbits[: len(fbits) - 1]
    pairs = [(fbits[i], fbits[i + 1]) for i in range(0, len(fbits), 2)]
    vbytes = bits_to_bytes_msb(ViterbiDecoder().decode(pairs, final_state=0))

    n_rsblocks = len(vbytes) // 255
    stream_rs = vbytes[: n_rsblocks * 255]
    desc = Scrambler().descramble(stream_rs)
    rs = ReedSolomon(nsym=32)
    parts: List[bytes] = []
    nerrors: List[int] = []
    for i in range(0, len(desc), 255):
        res = rs.decode(desc[i:i + 255])
        nerrors.append(res.nerrors)
        parts.append(res.data)
    return b"".join(parts), nerrors


def ccsds_iq_to_result(iq: np.ndarray, fs: float, symrate: float,
                       frame_bits: int, f_offset: Optional[float] = 0.0,
                       timing: str = "coarse") -> CcsdsSsdvResult:
    """复 IQ → demod_bpsk → ASM 同步 → Viterbi → 解扰 → RS → 218B → DSLWP JPEG。

    参数
    ----------
    frame_bits :
        每个 ASM 之后要收集的编码比特数（= 卷积后总比特数；由 TX 合成/链路参数
        给出）。ASM 同步出几帧就解几帧。
    f_offset :
        显式带内频偏 Hz；传 ``None`` = 盲估计（平方环，见
        :func:`mbdsdr_ai.ssdv_phy.estimate_cfo_bpsk`）。
    timing :
        传 ``"coarse"``（眼图粗定时）或 ``"gardner"``（Gardner TED 闭环精跟踪）。

    诚实空态：ASM 0 帧 / RS 全不可纠 / CRC 不过 → 结果计数全 0、jpeg 为空，不伪造。
    """
    from .ssdv_phy import estimate_cfo_bpsk
    out = CcsdsSsdvResult()
    foff = f_offset
    if foff is None:
        foff, prom = estimate_cfo_bpsk(iq, fs)
    out.cfo_est_hz = float(foff)
    bits = demod_bpsk(iq, fs, symrate, f_offset=foff, timing=timing)
    out.n_demod_bits = int(bits.size)
    if bits.size == 0:
        return out

    framer = AsmFramer(frame_bits=frame_bits, max_hamming=0)
    frames = framer.feed_bits(bits.tolist())
    out.n_asm_frames = len(frames)
    if not frames:
        return out

    dec = SsdvDecoder(dialect=DIALECT_DSLWP)
    images: dict[int, SsdvImage] = {}
    for frame in frames:
        stream218, nerrors = ccsds_frame_to_dslwp(frame)
        out.rs_nerrors.append(nerrors)
        for pkt in dec.feed(stream218):
            if pkt is None:
                continue
            img = images.get(pkt.image_id)
            if img is None:
                img = SsdvImage(pkt.image_id)
                images[pkt.image_id] = img
            img.add(pkt)

    if not images:
        return out
    best_id = max(
        images,
        key=lambda k: (len(images[k].packets),
                       int(any(p.eoi for p in images[k].packets.values()))))
    res = images[best_id].build()
    out.image_id = best_id
    out.n_packets = len(images[best_id].packets)
    out.width, out.height = res.width, res.height
    out.mcu_count = res.mcu_count
    out.received_mcus = len(res.received_mcus)
    out.missing_mcus = list(res.missing_mcus)
    out.eoi_seen = res.eoi_seen
    out.jpeg = res.jpeg if not res.empty else b""
    return out


__all__ = [
    "CcsdsSsdvTx",
    "CcsdsSsdvResult",
    "synthesize_ssdv_ccsds_iq",
    "ccsds_frame_to_dslwp",
    "ccsds_iq_to_result",
]

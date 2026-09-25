"""
mbdsdr_ai/ardop_adapter.py
===========================
ARDOP (hamarituc/ardop, 源于 John Wiseman G8BPQ 的 ARDOP 调制解调器)
数字电台调制物理层的 Python 学习移植。

关键常量与算法均标注「来源: repos/ardop/ARDOP2/<file>:<line>」：
  - 采样率 12000 Hz        ALSASound.c:1300 / CalcTemplates.c:93
  - 中心频率 1500 Hz        CalcTemplates.c:186 (index 5)
  - 11 个载频表             CalcTemplates.c:186
  - 10 载频数据模式排除中心  CalcTemplates.c:191 (中心 1500Hz 作导频)
  - 双音前导 1475/1525 Hz   Modulate.c:48
  - 50/100 baud 符号        CalcTemplates.c:207 (120 采样=100baud)
  - 帧同步字 0x1A 0x59      (SendLeaderAndSYNC 后的 2 字节帧类型,
                            Modulate.c:91-118; 0x1A = D4FSK_500_50_E
                            ARDOPC.h:362)
  - QAM 星座               Modulate.c:363-389 (16QAM 2 sym/byte),
                            FrameInfo() ARDOPC.c:713-860

本移植纯 numpy，实现可测的 OFDM 帧结构 / QAM 星座 / 帧同步检测 /
连接状态机，不追求与官方二进制逐样本一致。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# =====================================================================
# 物理层常量 —— 来源 repos/ardop/ARDOP2/
# =====================================================================
ARDOP_SAMPLE_RATE = 12000          # ALSASound.c:1300
ARDOP_CENTER_FREQ = 1500.0         # CalcTemplates.c:186 index 5

# 11 个载频 (Hz) —— CalcTemplates.c:186
ARDOP_CARRIERS = [600, 800, 1000, 1200, 1400, 1500, 1600, 1800, 2000, 2200, 2400]
# 10 载频数据模式用 index 0..4,6..10，排除中心(index 5=1500Hz 作导频)
#   来源: CalcTemplates.c:191
ARDOP_PILOT_INDEX = 5
ARDOP_DATA_CARRIERS_10 = [ARDOP_CARRIERS[i] for i in range(11) if i != ARDOP_PILOT_INDEX]

# 双音前导 (leader) 音调 —— Modulate.c:48
ARDOP_LEADER_F1 = 1475.0
ARDOP_LEADER_F2 = 1525.0

# 帧同步字（2 字节）—— 0x1A = D4FSK_500_50_E (ARDOPC.h:362)，
# 后接帧类型/会话 ID 第二字节。任务约定同步字 = 0x1A 0x59。
ARDOP_SYNC_WORD = bytes([0x1A, 0x59])

# 符号时长 —— CalcTemplates.c:207
#   120 samples @12kHz = 10ms (100 baud); 240 samples = 20ms (50 baud)
ARDOP_SPS_100BAUD = 120
ARDOP_SPS_50BAUD = 240
# 任务约定 OFDM 帧长 160 ms = 16 个 100-baud 符号
ARDOP_FRAME_MS = 160
ARDOP_FRAME_SYMBOLS = 16

# FEC 类型 —— FrameInfo() ARDOPC.c:749-860
#   IDFRAME: data=12, RS(16,12) ; ConReq: RS(8,6) ; 16QAM: RS(160,120)
class ARDOPFEC(str, Enum):
    NONE = "none"
    RS_16_12 = "rs(16,12)"   # IDFRAME  ARDOPC.c:749-750
    RS_8_6 = "rs(8,6)"       # ConReq   ARDOPC.c:759-760
    RS_160_120 = "rs(160,120)"  # 16QAM  ARDOPC.c:824-825


# =====================================================================
# QAM 星座 —— Gray 编码
# =====================================================================
# Modulate.c:363 起：16QAM 每字节 2 符号 (l=2)；QPSK/4PSK 每字节 4 符号。
# 这里用标准 Gray 映射的复星座点（单位能量归一）。
def _gray(bits: int, n: int) -> int:
    """n bit 二进制 -> Gray 码。"""
    return bits ^ (bits >> 1)


def qam_constellation(order: int) -> np.ndarray:
    """返回 order=4/16/64 的单位平均功率复星座点，索引=Gray 符号值。

    4-QAM  = QPSK  (Modulate.c:828 4PSK)
    16-QAM = Modulate.c:363 "16QAM"
    64-QAM = 高阶模式（任务要求）
    """
    m = int(math.log2(order))
    nside = int(math.sqrt(order))
    levels = np.arange(nside) - (nside - 1) / 2.0
    # Gray 排序：把 Gray 码值映射到 (I,Q) 网格
    sym = np.zeros(order, dtype=complex)
    avg = 0.0
    for g in range(order):
        # Gray->binary
        b = g
        shift = g >> 1
        while shift:
            b ^= shift
            shift >>= 1
        ii = b // nside
        qq = b % nside
        I = levels[ii]
        Q = levels[qq]
        sym[g] = I + 1j * Q
        avg += abs(sym[g]) ** 2
    avg /= order
    sym /= math.sqrt(avg)
    return sym


QAM4 = qam_constellation(4)
QAM16 = qam_constellation(16)
QAM64 = qam_constellation(64)


def bits_to_symbols(bits: np.ndarray, order: int) -> np.ndarray:
    """比特数组 -> Gray 符号索引数组。"""
    m = int(math.log2(order))
    # 补齐到 m 的整数倍
    pad = (-len(bits)) % m
    if pad:
        bits = np.concatenate([bits, np.zeros(pad, dtype=bits.dtype)])
    n = len(bits) // m
    sym = np.zeros(n, dtype=np.int64)
    for i in range(m):
        sym |= bits[i::m][:n].astype(np.int64) << i
    return sym


def symbols_to_bits(sym: np.ndarray, order: int) -> np.ndarray:
    m = int(math.log2(order))
    out = np.zeros(len(sym) * m, dtype=np.int64)
    for i in range(m):
        out[i::m] = (sym >> i) & 1
    return out


def modulate_qam(sym: np.ndarray, order: int) -> np.ndarray:
    const = {4: QAM4, 16: QAM16, 64: QAM64}[order]
    return const[sym]


def demodulate_qam(tone: np.ndarray, order: int) -> np.ndarray:
    """软判决：每个复样本找最近星座点。"""
    const = {4: QAM4, 16: QAM16, 64: QAM64}[order]
    # tone: (N,) complex; const: (M,)
    d = np.abs(tone[:, None] - const[None, :])
    return np.argmin(d, axis=1)


# =====================================================================
# OFDM 帧结构
# =====================================================================
@dataclass
class ARDOFFrame:
    """一个 ARDOP 物理帧。

    布局（学习模型）：
      leader   : 双音 1475/1525 Hz (Modulate.c:48)
      sync     : 0x1A 0x59 两字节同步字 (4FSK)
      payload  : data_carriers 上的 QAM 符号
    """

    payload_bits: np.ndarray
    order: int = 16          # 4/16/64
    n_carriers: int = 10     # 1/2/10
    fec: ARDOPFEC = ARDOPFEC.RS_160_120


# ---- 4FSK 同步字节调制（同步字 0x1A 0x59） ----
# SendLeaderAndSYNC: 每字节 4 符号 (3 数据 + 1 奇偶)，
#   50baud 中心音调 1350/1450/1550/1650 Hz (Modulate.c:108-114)
FSK_SYNC_TONES = [1350.0, 1450.0, 1550.0, 1650.0]


def _tone(freq: float, n: int, fs: float, phase0: float = 0.0) -> np.ndarray:
    t = np.arange(n) / fs
    return np.cos(2.0 * math.pi * freq * t + phase0)


def build_leader(n_symbols: int = 10, fs: float = ARDOP_SAMPLE_RATE) -> np.ndarray:
    """双音前导：50 baud (240 samples/sym) 1475/1525 Hz 交替。

    来源: Modulate.c:45-70 GetTwoToneLeaderWithSync。
    """
    sps = ARDOP_SPS_50BAUD
    out = np.empty(n_symbols * sps, dtype=np.float64)
    sign = 1.0 if (n_symbols & 1) == 0 else -1.0
    for i in range(n_symbols):
        f = ARDOP_LEADER_F1 if (i % 2 == 0) else ARDOP_LEADER_F2
        out[i * sps:(i + 1) * sps] = sign * _tone(f, sps, fs)
        sign = -sign
    return out


def build_sync_word(
    word: bytes = ARDOP_SYNC_WORD,
    fs: float = ARDOP_SAMPLE_RATE,
) -> np.ndarray:
    """把 2 字节同步字编成 50baud 4FSK 波形。

    每字节 4 个 2-bit 符号 (Modulate.c:97-117)，符号 k 选
    FSK_SYNC_TONES[k]。最后一个符号为奇偶位（学习模型取 bit0）。
    """
    sps = ARDOP_SPS_50BAUD
    out = np.empty((len(word) * 4) * sps, dtype=np.float64)
    idx = 0
    for byte in word:
        for k in range(4):
            sym = (byte >> (2 * (3 - k))) & 0x3 if k < 3 else (byte & 0x1)
            tone = _tone(FSK_SYNC_TONES[sym], sps, fs)
            out[idx * sps:(idx + 1) * sps] = tone
            idx += 1
    return out


def build_ofdm_data(
    payload_bits: np.ndarray,
    order: int = 16,
    n_carriers: int = 10,
    fs: float = ARDOP_SAMPLE_RATE,
) -> np.ndarray:
    """把比特载荷在数据载波上做 QAM-OFDM 符号合成（学习模型）。

    每帧符号数 = ARDOP_FRAME_SYMBOLS (16)，每符号在 n_carriers 个
    子载波上发送一个 QAM 复符号（取实部叠加）。
    """
    carriers = ARDOP_DATA_CARRIERS_10[:n_carriers] if n_carriers == 10 else (
        [ARDOP_CENTER_FREQ] if n_carriers == 1 else [1400.0, 1600.0]
    )
    sps = ARDOP_SPS_100BAUD  # 100 baud
    sym = bits_to_symbols(np.asarray(payload_bits), order)
    # 需要的符号数 = frame_symbols * n_carriers
    need = ARDOP_FRAME_SYMBOLS * len(carriers)
    if len(sym) < need:
        sym = np.concatenate([sym, np.zeros(need - len(sym), dtype=sym.dtype)])
    sym = sym[:need]
    qam = modulate_qam(sym, order)
    # 逐符号叠加各载波
    out = np.zeros(ARDOP_FRAME_SYMBOLS * sps, dtype=np.float64)
    t = np.arange(sps) / fs
    for s in range(ARDOP_FRAME_SYMBOLS):
        seg = np.zeros(sps, dtype=np.float64)
        for c, car in enumerate(carriers):
            q = qam[s * len(carriers) + c]
            seg += (q.real * np.cos(2 * math.pi * car * t)
                    - q.imag * np.sin(2 * math.pi * car * t))
        out[s * sps:(s + 1) * sps] = seg.real
    return out


def build_frame(
    payload_bits: np.ndarray,
    order: int = 16,
    n_carriers: int = 10,
    leader_symbols: int = 10,
    fs: float = ARDOP_SAMPLE_RATE,
) -> np.ndarray:
    """组装完整 ARDOP 帧: leader + syncword + OFDM data。"""
    parts = [build_leader(leader_symbols, fs), build_sync_word(fs=fs),
             build_ofdm_data(payload_bits, order, n_carriers, fs)]
    return np.concatenate(parts)


def detect_sync_word(
    audio: np.ndarray,
    fs: float = ARDOP_SAMPLE_RATE,
) -> Optional[int]:
    """在音频中检测同步字 0x1A 0x59 的位置（样本偏移）。

    做法：对每个候选偏移，把 2 字节同步窗按 4FSK 4 音调相关，
    与 build_sync_word 生成的模板做互相关，取峰值位置。
    """
    audio = np.asarray(audio, dtype=np.float64)
    template = build_sync_word(fs=fs)
    if len(audio) < len(template):
        return None
    # 归一化互相关（滑动）
    n = len(template)
    tpl = template - template.mean()
    tpl_norm = np.linalg.norm(tpl) + 1e-12
    best_off = 0
    best_score = -1e18
    step = max(1, ARDOP_SPS_50BAUD // 2)  # 半符号步长搜索
    for off in range(0, len(audio) - n, step):
        seg = audio[off:off + n]
        if len(seg) < n:
            break
        s = seg - seg.mean()
        score = float(np.dot(s, tpl) / (np.linalg.norm(s) * tpl_norm + 1e-12))
        if score > best_score:
            best_score = score
            best_off = off
    if best_score < 0.3:  # 相关阈值
        return None
    return int(best_off)


# =====================================================================
# 连接状态机 —— 移植 pktSession.c 的 ARQ 会话状态
# =====================================================================
class ARDOPState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"           # SearchingForLeader (ofdm.c:986)
    CONNECT_REQ = "CONNECT_REQ"       # ConReq200/500/2500 (ARDOPC.c:757)
    CONNECTED = "CONNECTED"           # ConAck
    DATA = "DATA"                     # PktFrameData
    DISC = "DISC"                     # DISCFRAME


@dataclass
class ARDOPSession:
    """ARDOP ARQ 连接状态机（学习模型）。

    状态迁移对齐 pktSession.c / ofdm.c:986：
      LISTENING --(收到 leader+sync)--> CONNECT_REQ
      CONNECT_REQ --(ConAck)--> CONNECTED
      CONNECTED --(PktFrameData)--> DATA
      DATA --(ACK)--> CONNECTED / --(DISC)--> DISC
    """

    state: ARDOPState = ARDOPState.IDLE
    remote_callsign: str = ""
    bw: str = "500"
    events: List[str] = field(default_factory=list)

    def _transition(self, to: ARDOPState, ev: str) -> bool:
        allowed = {
            ARDOPState.IDLE: [ARDOPState.LISTENING],
            ARDOPState.LISTENING: [ARDOPState.CONNECT_REQ, ARDOPState.IDLE],
            ARDOPState.CONNECT_REQ: [ARDOPState.CONNECTED, ARDOPState.LISTENING],
            ARDOPState.CONNECTED: [ARDOPState.DATA, ARDOPState.DISC, ARDOPState.LISTENING],
            ARDOPState.DATA: [ARDOPState.CONNECTED, ARDOPState.DISC],
            ARDOPState.DISC: [ARDOPState.IDLE],
        }
        if to not in allowed.get(self.state, []):
            return False
        self.state = to
        self.events.append(ev)
        return True

    def listen(self) -> bool:
        return self._transition(ARDOPState.LISTENING, "listen")

    def connect_request(self, callsign: str, bw: str = "500") -> bool:
        if self._transition(ARDOPState.CONNECT_REQ, f"ConReq({callsign},{bw})"):
            self.remote_callsign = callsign
            self.bw = bw
            return True
        return False

    def connect_ack(self) -> bool:
        return self._transition(ARDOPState.CONNECTED, "ConAck")

    def send_data(self) -> bool:
        return self._transition(ARDOPState.DATA, "PktFrameData")

    def ack(self) -> bool:
        return self._transition(ARDOPState.CONNECTED, "DataACK")

    def disconnect(self) -> bool:
        return self._transition(ARDOPState.DISC, "DISC") or \
            self._transition(ARDOPState.IDLE, "DISC->idle")


# =====================================================================
# 工具注册
# =====================================================================
def register_ardop_tools(registry) -> None:
    """注册 ARDOP OFDM 物理层工具。"""
    from mbdsdr_ai.tool_registry import ToolResult

    def _constellation(args: Dict[str, Any]) -> ToolResult:
        try:
            order = int(args.get("order", 16))
            const = {4: QAM4, 16: QAM16, 64: QAM64}[order]
            pts = [{"sym": int(i), "re": float(c.real), "im": float(c.imag)}
                   for i, c in enumerate(const)]
            return ToolResult(
                success=True,
                content=f"{order}-QAM 星座: {len(pts)} 点, "
                        f"采样率 {ARDOP_SAMPLE_RATE}Hz, 中心 {ARDOP_CENTER_FREQ}Hz",
                data={"order": order, "points": pts,
                      "sample_rate": ARDOP_SAMPLE_RATE,
                      "center_hz": ARDOP_CENTER_FREQ,
                      "carriers": ARDOP_CARRIERS},
            )
        except Exception as e:
            return ToolResult(False, f"constellation 失败: {e}")

    def _build_frame(args: Dict[str, Any]) -> ToolResult:
        try:
            rng = np.random.default_rng(int(args.get("seed", 0)))
            nbits = int(args.get("n_bits", 256))
            bits = rng.integers(0, 2, size=nbits)
            order = int(args.get("order", 16))
            ncar = int(args.get("n_carriers", 10))
            audio = build_frame(bits, order=order, n_carriers=ncar)
            off = detect_sync_word(audio)
            return ToolResult(
                success=True,
                content=f"ARDOP 帧: {len(audio)} 样本 ({len(audio)/ARDOP_SAMPLE_RATE*1000:.0f}ms), "
                        f"sync@{off} samples",
                data={"n_samples": len(audio), "sync_offset": off,
                      "order": order, "n_carriers": ncar,
                      "fec": ARDOPFEC.RS_160_120.value},
            )
        except Exception as e:
            return ToolResult(False, f"build_frame 失败: {e}")

    def _state_machine(args: Dict[str, Any]) -> ToolResult:
        try:
            s = ARDOPSession()
            steps = [("listen", {}),
                     ("connect_request", {"callsign": "BI4MIB", "bw": "500"}),
                     ("connect_ack", {}),
                     ("send_data", {}), ("ack", {}),
                     ("disconnect", {})]
            trace = []
            for meth, kw in steps:
                ok = getattr(s, meth)(**kw)
                trace.append({"step": meth, "ok": ok, "state": s.state.value})
            return ToolResult(
                success=True,
                content=f"ARDOP 状态机: {' -> '.join(t['state'] for t in trace)}",
                data={"trace": trace, "events": s.events},
            )
        except Exception as e:
            return ToolResult(False, f"state machine 失败: {e}")

    registry.register(
        name="ardop_constellation",
        description="列出 ARDOP 4/16/64-QAM Gray 编码星座点及载频表 "
                    "(采样率12kHz, 中心1500Hz, 11载频)。来源: CalcTemplates.c:186, Modulate.c:363。",
        parameters={"type": "object", "properties": {
            "order": {"type": "integer", "enum": [4, 16, 64]},
        }},
        handler=_constellation,
        category="ham_modes",
    )
    registry.register(
        name="ardop_build_frame",
        description="合成一个 ARDOP OFDM 帧 (双音leader+同步字0x1A0x59+QAM数据) 并检测同步字位置。",
        parameters={"type": "object", "properties": {
            "n_bits": {"type": "integer", "default": 256},
            "order": {"type": "integer", "enum": [4, 16, 64], "default": 16},
            "n_carriers": {"type": "integer", "enum": [1, 2, 10], "default": 10},
            "seed": {"type": "integer", "default": 0},
        }},
        handler=_build_frame,
        category="ham_modes",
    )
    registry.register(
        name="ardop_state_machine",
        description="跑一遍 ARDOP ARQ 连接状态机 (LISTENING->CONNECT_REQ->CONNECTED->DATA->DISC)。",
        parameters={"type": "object", "properties": {}},
        handler=_state_machine,
        category="ham_modes",
    )

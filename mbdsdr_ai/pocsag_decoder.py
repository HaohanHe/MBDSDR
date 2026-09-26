"""
MBDSDR - POCSAG 寻呼解码器
==============================

本模块是 multimon-ng 中 POCSAG 协议栈的纯 Python+numpy 移植。
所有关键常量与算法均标注来源 file:line。

参考源码（已 clone 到 repos/）：
  - repos/multimon-ng/pocsag.c        帧同步状态机 + 地址/消息解析
  - repos/multimon-ng/bch.c            BCH(31,21) 编解码查表
  - repos/multimon-ng/bch.h             POCSAG 码字位布局说明
  - repos/multimon-ng/gen_pocsag.c      位流组装 + FSK 合成参考
  - repos/multimon-ng/demod_poc12.c     位同步 PLL（early-late gate）

POCSAG 空中接口要点：
  - 调制：FSK，音频子载频中心 1800 Hz，两音调 1200 / 2400 Hz
      逻辑 0 = 高频/标记 (2400 Hz)，逻辑 1 = 低频/空号 (1200 Hz)
      （来源: demod_poc12.c:37-44 隐含约定；与 ITU-R M.553 一致）
  - 线路编码：NRZ（每个码元周期内频率恒定，无归零）
  - 帧结构：前置码 576 bit 交替 1010...（gen_pocsag.c:52,279-283）
      每批 = 同步字(32bit) + 16 码字（8 帧 × 2 码字/帧）
      同步字 0x7CD215D8（pocsag.c:57），空闲字 0x7A89C197（pocsag.c:58）
  - 码字布局（32 bit，MSB 先发）：
      bit31   = 消息标志（0=地址, 1=消息）  pocsag.c:63
      bit30..11 = 20 bit 数据（含 bit31 共 21 bit 信息位）
      bit10..1  = BCH(31,21) 奇偶校验（10 bit）
      bit0      = 整体偶校验
      来源: bch.h:69-72, bch.c:201-206
  - 地址码字：bit31=0，bit30..13 = 地址位 20..3（18 bit），
      bit12..11 = 功能位（2 bit）；地址低 3 位 = 帧号(0..7)
      来源: gen_pocsag.c:95-100, pocsag.c:916-917
  - 消息码字：bit31=1，bit30..11 = 20 bit 载荷
      数字模式：5 个 BCD 半字节/码字（pocsag.c:454 转换表）
      字母模式：7-bit ASCII 位反转后按位流打包（pocsag.c:475-488）
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

# ─────────────────────────────────────────────────────────────────────
# 协议常量（全部标注来源）
# ─────────────────────────────────────────────────────────────────────

# 同步字 / 空闲字，来源: repos/multimon-ng/pocsag.c:57-58
POCSAG_SYNC = 0x7CD215D8   # pocsag.c:57
POCSAG_IDLE = 0x7A89C197   # pocsag.c:58
# 消息标志：bit31=1 表示消息码字，来源: pocsag.c:63
POCSAG_MESSAGE_FLAG = 0x80000000

# 前置码长度，来源: gen_pocsag.c:52
PREAMBLE_BITS = 576

# FSK 音调（音频子载频）：中心 1800 Hz，偏差 ±600 Hz
# 来源: demod_poc12.c:37-44 隐含约定（multimon 输入为 FM 鉴频后基带，
# 本模块自行完成 FSK 鉴频，故给出实际音调）
POCSAG_CENTER_FREQ = 1800.0
POCSAG_MARK_FREQ = 2400.0    # 逻辑 0 = 高频/标记
POCSAG_SPACE_FREQ = 1200.0   # 逻辑 1 = 低频/空号
POCSAG_DEFAULT_SAMPLE_RATE = 22050.0

# 支持的波特率，来源: demod_poc5.c / demod_poc12.c / demod_poc24.c
SUPPORTED_BAUDS = (512, 1200, 2400)

# BCH(31,21) 生成多项式 0x769（八进制 03551）
# 来源: bch.c:54
_POCSAG_POLY = 0x769
_BCH_DATA_BITS = 21
_BCH_PARITY_BITS = 10

# 数字字符 → BCD 半字节映射（gen_pocsag.c:117-138）
# 解码方向的反向查找表，来源: pocsag.c:454
_NUMERIC_TABLE = "084 2.6]195-3U7["
_NUMERIC_ENCODE = {
    '0': 0, '1': 8, '2': 4, '3': 12, '4': 2,
    '5': 10, '6': 6, '7': 14, '8': 1, '9': 9,
    'U': 13, ' ': 3, '-': 11, '.': 5, '[': 15, ']': 7,
}


# ─────────────────────────────────────────────────────────────────────
# BCH(31,21) 查表构建 —— 移植自 repos/multimon-ng/bch.c:402-476
# ─────────────────────────────────────────────────────────────────────

def _build_bch_tables() -> Tuple[List[int], List[int], List[int]]:
    """构建 POCSAG BCH 三张查找表。

    返回 (parity_tbl[21], syn_tbl[32], err_tbl[2048])。
    算法逐行对应 bch.c:416-473。
    """
    # parity_tbl[databit]：仅第 databit 个数据位为 1 时的 10 bit 校验
    # 来源: bch.c:416-424
    parity_tbl = [0] * _BCH_DATA_BITS
    for databit in range(_BCH_DATA_BITS):
        shreg = 1 << (databit + _BCH_PARITY_BITS)
        for i in range(_BCH_DATA_BITS - 1, -1, -1):
            if shreg & (1 << (i + _BCH_PARITY_BITS)):
                shreg ^= (_POCSAG_POLY << i)
        parity_tbl[databit] = shreg & 0x3FF

    # syn_tbl[bit]：31-bit BCH 域中第 bit 位出错时的 10 bit 伴随式
    # 来源: bch.c:450-457
    syn_tbl = [0] * 32
    for bit in range(31):
        shreg = 1 << bit
        for i in range(_BCH_DATA_BITS - 1, -1, -1):
            if shreg & (1 << (i + _BCH_PARITY_BITS)):
                shreg ^= (_POCSAG_POLY << i)
        syn_tbl[bit] = shreg & 0x3FF

    # err_tbl[11bit_syndrome] → 32 bit 错误图案
    # 来源: bch.c:461-473
    err_tbl = [0] * 2048
    # 单比特错误（bit 1..31；bit0 是偶校验位，单错必然翻转偶校验 → |0x400）
    for i in range(1, 32):
        syn = syn_tbl[i - 1] | 0x400
        err_tbl[syn] = 1 << i
    # 双比特错误：伴随式异或，偶校验位翻转相互抵消
    for i in range(1, 32):
        for j in range(i + 1, 32):
            syn = syn_tbl[i - 1] ^ syn_tbl[j - 1]
            if err_tbl[syn] == 0:
                err_tbl[syn] = (1 << i) | (1 << j)

    return parity_tbl, syn_tbl, err_tbl


_PARITY_TBL, _SYN_TBL, _ERR_TBL = _build_bch_tables()


def _parity32(x: int) -> int:
    """32 bit 偶校验。来源: bch.c:93-105"""
    x ^= x >> 16
    x ^= x >> 8
    x ^= x >> 4
    x ^= x >> 2
    x ^= x >> 1
    return x & 1


def bch_pocsag_encode(data21: int) -> int:
    """21 bit 数据 → 32 bit POCSAG 码字。来源: bch.c:228-249"""
    d = data21 & 0x1FFFFF
    parity = 0
    tmp = d
    while tmp:
        # 取最低置位位（ctz）
        bit = (tmp & -tmp).bit_length() - 1
        parity ^= _PARITY_TBL[bit]
        tmp &= tmp - 1
    cw = (d << 11) | (parity << 1)
    cw |= _parity32(cw)
    return cw


def bch_pocsag_correct(cw: int) -> Tuple[int, int]:
    """对 32 bit POCSAG 码字纠错。

    返回 (corrected_cw, n_errors)；n_errors=-1 表示不可纠。
    来源: bch.c:251-268, bch.c:209-226
    """
    bits = cw >> 1
    syn = 0
    while bits:
        bit = (bits & -bits).bit_length() - 1
        syn ^= _SYN_TBL[bit]
        bits &= bits - 1
    if _parity32(cw):
        syn |= 0x400
    if syn == 0:
        return cw, 0
    err = _ERR_TBL[syn]
    if err == 0:
        return cw, -1
    return cw ^ err, bin(err).count("1")


# ─────────────────────────────────────────────────────────────────────
# 码字构造 —— 移植自 gen_pocsag.c:95-114
# ─────────────────────────────────────────────────────────────────────

def _build_address_codeword(address: int, function: int) -> int:
    """构造地址码字。来源: gen_pocsag.c:95-100"""
    data = ((address >> 3) << 2) | (function & 3)
    return bch_pocsag_encode(data)


def _build_message_codeword(data20: int) -> int:
    """构造消息码字（bit31=1）。来源: gen_pocsag.c:109-114"""
    data = (1 << 20) | (data20 & 0xFFFFF)
    return bch_pocsag_encode(data)


# ─────────────────────────────────────────────────────────────────────
# 消息编码 —— 移植自 gen_pocsag.c:164-226
# ─────────────────────────────────────────────────────────────────────

def _detect_function(message: str) -> int:
    """根据消息内容判定功能位：纯数字/符号 → 0（数字），否则 → 1（字母）。"""
    numeric_chars = set("0123456789U .-[")
    if message and all(ch in numeric_chars for ch in message):
        return 0
    return 1


def _rev7(b: int) -> int:
    """7 bit 位反转。来源: pocsag.c:481-488 / gen_pocsag.c:141-147"""
    return (((b << 6) & 64) | ((b >> 6) & 1) |
            ((b << 4) & 32) | ((b >> 4) & 2) |
            ((b << 2) & 16) | ((b >> 2) & 4) |
            (b & 8))


def _encode_message(message: str, function: int) -> List[int]:
    """把消息编码成若干消息码字。来源: gen_pocsag.c:164-226"""
    cws: List[int] = []
    if function == 0:
        # 数字模式：每码字 5 个 BCD 半字节
        # 来源: gen_pocsag.c:170-185
        i = 0
        n = len(message)
        while i < n:
            data = 0
            for _ in range(5):
                if i < n:
                    data = (data << 4) | _NUMERIC_ENCODE.get(message[i], 3)
                    i += 1
                else:
                    data = (data << 4) | 3  # 空格填充
            cws.append(_build_message_codeword(data))
    else:
        # 字母模式：每字符 7 bit 位反转后 MSB 先打入位流，再按 4 bit 半字节打包
        # 来源: gen_pocsag.c:187-222
        bit_stream: List[int] = []
        for ch in message:
            c = _rev7(ord(ch) & 0x7F)
            for b in range(6, -1, -1):
                bit_stream.append((c >> b) & 1)
        # 对齐到半字节边界
        while len(bit_stream) % 4 != 0:
            bit_stream.append(0)
        # 每 20 bit（5 半字节）一个码字
        for off in range(0, len(bit_stream), 20):
            chunk = bit_stream[off:off + 20]
            while len(chunk) < 20:
                chunk.append(0)
            data = 0
            for b in chunk:
                data = (data << 1) | b
            cws.append(_build_message_codeword(data & 0xFFFFF))
    return cws


# ─────────────────────────────────────────────────────────────────────
# 编码入口：pocsag_encode(address, message, baud) -> np.ndarray
# ─────────────────────────────────────────────────────────────────────

def pocsag_encode(address: int,
                  message: str,
                  baud: int,
                  sample_rate: float = POCSAG_DEFAULT_SAMPLE_RATE,
                  mark_freq: float = POCSAG_MARK_FREQ,
                  space_freq: float = POCSAG_SPACE_FREQ,
                  amplitude: float = 0.8) -> np.ndarray:
    """合成 POCSAG FSK 音频。

    参数:
        address:  21 bit RIC 地址（0..2097151）
        message:  文本消息（数字或字母）
        baud:     512 / 1200 / 2400
        sample_rate: 输出采样率，默认 22050
        mark_freq: 逻辑 0 对应音调，默认 2400 Hz
        space_freq: 逻辑 1 对应音调，默认 1200 Hz
        amplitude: 正弦幅度

    返回:
        float32 音频数组（连续相位 FSK）
    """
    if baud not in SUPPORTED_BAUDS:
        raise ValueError(f"不支持的波特率 {baud}，仅支持 {SUPPORTED_BAUDS}")
    if not 0 <= address <= 0x1FFFFF:
        raise ValueError("address 必须在 0..2097151 范围内")

    function = _detect_function(message)
    msg_cws = _encode_message(message, function)

    # ── 组装位流 ──
    bits: List[int] = []
    # 前置码：1010... 起始于 1。来源: gen_pocsag.c:279-283
    for i in range(PREAMBLE_BITS):
        bits.append(1 if (i & 1) == 0 else 0)

    # 逐批填充：同步字 + 16 码字（8 帧 × 2）
    # 地址放在 frame=address&7 的第 0 个码字槽。来源: gen_pocsag.c:241,301-322
    frame_pos = address & 7
    address_sent = False
    msg_idx = 0
    batch = 0
    while True:
        # 同步字 MSB 先发。来源: gen_pocsag.c:291-298
        for i in range(31, -1, -1):
            bits.append((POCSAG_SYNC >> i) & 1)
        # 16 个码字槽
        for frame in range(8):
            for cw in range(2):
                if not address_sent and frame == frame_pos and cw == 0:
                    codeword = _build_address_codeword(address, function)
                    address_sent = True
                elif address_sent and msg_idx < len(msg_cws):
                    # 消息码字必须紧跟地址之后，来源: gen_pocsag.c:312
                    codeword = msg_cws[msg_idx]
                    msg_idx += 1
                else:
                    codeword = POCSAG_IDLE
                for i in range(31, -1, -1):
                    bits.append((codeword >> i) & 1)
        if address_sent and msg_idx >= len(msg_cws):
            break
        batch += 1
        if batch > 20:
            break

    # ── FSK 调制（连续相位）──
    spb = sample_rate / baud  # 每码元采样数
    n_samples = int(round(len(bits) * spb))
    audio = np.zeros(n_samples, dtype=np.float32)
    phase = 0.0
    two_pi = 2.0 * math.pi
    for n in range(n_samples):
        bit_pos = int(n / spb)
        if bit_pos >= len(bits):
            break
        f = mark_freq if bits[bit_pos] == 0 else space_freq
        phase += two_pi * f / sample_rate
        audio[n] = amplitude * math.sin(phase)
    return audio


# ─────────────────────────────────────────────────────────────────────
# FSK 解调：音频 → 每采样点切片后的二进制比特流
# ─────────────────────────────────────────────────────────────────────

def _fsk_discriminator(audio: np.ndarray,
                       sample_rate: float,
                       baud: int,
                       center_freq: float = POCSAG_CENTER_FREQ) -> np.ndarray:
    """FSK 鉴频：返回平滑的瞬时频偏（float 数组）。

    正 → 高频 2400 tone（逻辑 0），负 → 低频 1200 tone（逻辑 1）。
    方法：复数下变频到 1800 Hz → 移动平均低通 → 相位差分。
    """
    audio = np.asarray(audio, dtype=np.float64)
    peak = np.max(np.abs(audio))
    if peak < 1e-9:
        return np.zeros(len(audio), dtype=np.float64)
    audio = audio / peak

    n = len(audio)
    t = np.arange(n) / sample_rate
    I = audio * np.cos(2 * np.pi * center_freq * t)
    Q = -audio * np.sin(2 * np.pi * center_freq * t)

    # 移动平均低通，窗宽 ≈ 0.6 码元周期
    width = max(5, int(0.6 * sample_rate / baud))
    kernel = np.ones(width) / width
    I_lp = np.convolve(I, kernel, mode='same')
    Q_lp = np.convolve(Q, kernel, mode='same')

    angle = np.unwrap(np.arctan2(Q_lp, I_lp))
    df = np.diff(angle) * sample_rate / (2 * np.pi)
    # 补一个点保持长度
    df = np.concatenate([[df[0]], df])
    return df


def _bitsync_candidates(df: np.ndarray,
                       sample_rate: float,
                       baud: int) -> List[Tuple[List[int], int, int]]:
    """从鉴频输出恢复码元序列，返回多个候选 (bits, sync_at, polarity)。

    遍历 [0, spb) 内多个候选相位，用运行累加器采样，
    收集所有能检出同步字的相位，按 BCH 干净程度排序。
    参考 demod_poc12.c:67-84。
    """
    spb = sample_rate / baud
    n = len(df)
    if n < int(spb * 10):
        return []

    search_steps = max(64, int(spb * 3))
    candidates: List[Tuple[float, List[int], int, int]] = []

    for step in range(search_steps):
        phase = step * spb / search_steps
        # 采样
        bits = []
        ph = phase
        while True:
            idx = int(round(ph))
            if idx >= n:
                break
            bits.append(0 if df[idx] > 0 else 1)
            ph += spb

        if len(bits) < 600:
            continue

        # 找同步字位置
        sync_at = -1
        polarity = 0
        for i in range(min(len(bits) - 32, 800)):
            cw = 0
            for k in range(32):
                cw = (cw << 1) | bits[i + k]
            cw &= 0xFFFFFFFF
            c2, _ = bch_pocsag_correct(cw)
            if c2 == POCSAG_SYNC:
                sync_at = i
                polarity = 0
                break
            c2i, _ = bch_pocsag_correct((~cw) & 0xFFFFFFFF)
            if c2i == POCSAG_SYNC:
                sync_at = i
                polarity = 1
                break
        if sync_at < 0:
            continue

        # 评估本批全部 16 个数据字的干净程度
        quality = 0
        for w in range(1, 17):
            pos = sync_at + 32 * w
            if pos + 32 > len(bits):
                break
            cw = 0
            for k in range(32):
                cw = (cw << 1) | bits[pos + k]
            cw &= 0xFFFFFFFF
            if polarity:
                cw = (~cw) & 0xFFFFFFFF
            _, nerr = bch_pocsag_correct(cw)
            if nerr < 0:
                quality -= 50
            else:
                quality -= nerr * 2
        quality -= abs(sync_at - 576) * 0.1
        candidates.append((quality, bits, sync_at, polarity))

    candidates.sort(key=lambda x: -x[0])
    return [(b, s, p) for _, b, s, p in candidates[:4]]

    # 退回：最佳交替相位
    best_phase = 0.0
    best_score = -1.0
    for step in range(search_steps):
        phase = step * spb / search_steps
        score = 0
        prev = 0
        count = 0
        for k in range(min(200, int(n / spb))):
            idx = int(round(phase + k * spb))
            if idx >= n:
                break
            s = 1 if df[idx] > 0 else -1
            if k > 0 and s != prev:
                score += 1
            prev = s
            count += 1
        if count > 0 and score / count > best_score:
            best_score = score / count
            best_phase = phase
    bits = []
    ph = best_phase
    while True:
        idx = int(round(ph))
        if idx >= n:
            break
        bits.append(0 if df[idx] > 0 else 1)
        ph += spb
    return bits


# ─────────────────────────────────────────────────────────────────────
# 帧同步与状态机 —— 移植自 pocsag.c:800-985
# ─────────────────────────────────────────────────────────────────────

def _bits_to_word(bits: List[int], start: int) -> int:
    """从 bits[start] 起取 32 bit 组成码字（MSB 先发）。"""
    w = 0
    for k in range(32):
        w = (w << 1) | bits[start + k]
    return w & 0xFFFFFFFF


def _get7(buf: bytes, n: int) -> int:
    """取第 n 个 7 bit 字符。来源: pocsag.c:475-479"""
    b0 = buf[(n * 7) // 8]
    b1 = buf[(n * 7 + 6) // 8] if (n * 7 + 6) // 8 < len(buf) else 0
    return ((b0 << 8 | b1) >> ((n + 1) % 8)) & 0x7F


def _decode_numeric(buf: bytes, numnibbles: int) -> str:
    """数字消息解码。来源: pocsag.c:452-473"""
    chars = []
    for i in range(numnibbles):
        bi = i // 2
        if bi >= len(buf):
            break
        nib = (buf[bi] >> 4) & 0xF if (i & 1) == 0 else (buf[bi] & 0xF)
        chars.append(_NUMERIC_TABLE[nib])
    return ''.join(chars).rstrip()


def _decode_alpha(buf: bytes, numnibbles: int) -> str:
    """字母消息解码。来源: pocsag.c:490-523"""
    n_chars = numnibbles * 4 // 7
    chars = []
    for i in range(n_chars):
        c = _rev7(_get7(buf, i))
        if 32 <= c < 127:
            chars.append(chr(c))
    return ''.join(chars).rstrip()


def pocsag_decode(audio: np.ndarray,
                  sample_rate: float,
                  baud: int) -> List[dict]:
    """解码 POCSAG 音频。

    参数:
        audio: 一维浮点音频（FSK 音调 1200/2400 Hz）
        sample_rate: 采样率
        baud: 512 / 1200 / 2400

    返回:
        list[dict]，每项含 address / function / message / baud 字段。
    """
    if baud not in SUPPORTED_BAUDS:
        raise ValueError(f"不支持的波特率 {baud}")

    df = _fsk_discriminator(audio, sample_rate, baud)
    candidates = _bitsync_candidates(df, sample_rate, baud)
    if not candidates:
        return []

    def _parse_batch(bits: List[int], sync_pos: int, polarity: int) -> List[dict]:
        """从给定 bits/sync_pos/polarity 解析 POCSAG 帧。"""
        out: List[dict] = []
        pending: Optional[dict] = None
        buf = bytearray()
        numnibbles = 0

        def flush() -> None:
            nonlocal pending, buf, numnibbles
            if pending is not None and numnibbles > 0:
                func = pending["function"]
                if func == 0:
                    msg = _decode_numeric(bytes(buf), numnibbles)
                else:
                    msg = _decode_alpha(bytes(buf), numnibbles)
                out.append({
                    "address": pending["address"],
                    "function": pending["function"],
                    "message": msg,
                    "baud": baud,
                })
            pending = None
            buf = bytearray()
            numnibbles = 0

        pos = sync_pos + 32
        while pos + 32 <= len(bits):
            for word_idx in range(1, 17):
                if pos + 32 > len(bits):
                    break
                cw_raw = _bits_to_word(bits, pos)
                if polarity:
                    cw_raw = (~cw_raw) & 0xFFFFFFFF
                cw, nerr = bch_pocsag_correct(cw_raw)
                pos += 32

                if nerr < 0:
                    flush()
                    continue

                if cw == POCSAG_IDLE:
                    if pending is not None:
                        flush()
                    continue

                if cw & POCSAG_MESSAGE_FLAG:
                    data = (cw >> 11) & 0xFFFFF
                    if pending is None:
                        pending = {"address": None, "function": 1}
                    if numnibbles & 1:
                        buf[-1] = (buf[-1] & 0xF0) | ((data >> 16) & 0xF)
                        buf.append((data >> 8) & 0xFF)
                        buf.append(data & 0xFF)
                    else:
                        buf.append((data >> 12) & 0xFF)
                        buf.append((data >> 4) & 0xFF)
                        buf.append((data << 4) & 0xFF)
                    numnibbles += 5
                else:
                    if pending is not None:
                        flush()
                    addr = ((cw >> 10) & 0x1FFFF8) | ((word_idx >> 1) & 7)
                    func = (cw >> 11) & 3
                    pending = {"address": addr, "function": func}
            # 批尾不 flush，消息可能跨批继续
            if pos + 32 <= len(bits):
                nxt = _bits_to_word(bits, pos)
                if polarity:
                    nxt = (~nxt) & 0xFFFFFFFF
                nc, _ = bch_pocsag_correct(nxt)
                if nc != POCSAG_SYNC:
                    break
                pos += 32
        flush()
        return out

    # 逐个候选解析，选结果最完整的（有非空地址优先）
    best: List[dict] = []
    for bits, sync_at, polarity in candidates:
        res = _parse_batch(bits, sync_at, polarity)
        # 评分：有非空地址 + 有非空消息的候选优先
        score = sum(
            (100 if r["address"] is not None else 0)
            + len(r["message"])
            for r in res
        )
        if score > sum(
            (100 if r["address"] is not None else 0) + len(r["message"])
            for r in best
        ):
            best = res
    return best

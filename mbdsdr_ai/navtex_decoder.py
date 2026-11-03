# SPDX-License-Identifier: MIT
"""NAVTEX / SITOR-B（CCIR 476）报文编解码 + 100 baud FSK 调制解调（纯 numpy）。

事实层（ITU-R M.540 / CCIR 476 / ITA-2，公开标准，不含任何厂商源码）：

  * 射频：518 / 490 / 4209.5 kHz（本模块处理解调后音频/基带，不含射频）
  * 调制：FSK 100 baud，170 Hz 频移（mark = 高频 +85 Hz，space = 低频 −85 Hz）
  * 字符层：CCIR 476 七单元恒比码——每字符 7 bit 恰含 4 个 mark（C(7,4)=35
    个合法码点）；单比特错误必破坏恒比 → 逐字符检错
  * SITOR-B 时间分集：每个字符发射两遍，第二遍滞后 4 字符（分集纠错）
  * 定相：空闲/引导段重复发 α(0x0F) / RQ(0x66)
  * 报文结构：ZCZC + B1 B2 B3 B4（台标/报类/编号）+ 正文 + NNNN

位序：7 bit 码以 MSB first 上线路（与 ARRL/维基表记法一致）。
分集实现口径（诚实声明）：4 字符直接块 + 4 字符重复块交替——每个字符
两遍、恒定滞后 4 字符，满足 CCIR 476 分集性质；线路逐字符交错图案
各资料表述不一，本实现采用该自洽结构并在测试中往返钉扎。

注意：本模块与 cpp/src/dsp/navtex_decoder.h 的「5 bit ITA-2 + SITOR 分集」
口径不同——本模块按 CCIR 476 标准七单元恒比码实现（C++ 侧口径存疑，
已记入协调台账待核）。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# 常量（标准事实）
# ---------------------------------------------------------------------------
NAVTEX_BAUD = 100.0
NAVTEX_SHIFT_HZ = 170.0
NAVTEX_MARK_HZ = NAVTEX_SHIFT_HZ / 2.0    # mark = f0 + 85（高频）
NAVTEX_BITS_PER_CHAR = 7

IDLE_ALPHA = 0x0F     # 0001111 定相信号 1 / idle α
IDLE_BETA = 0x33      # 0110011 idle β
REPEAT_RQ = 0x66      # 1100110 定相信号 2（mode B）

CODE_LTRS = 0x5A      # 1011010
CODE_FIGS = 0x36      # 0110110
CODE_CR = 0x78        # 1111000
CODE_LF = 0x6C        # 1101100
CODE_SP = 0x5C        # 1011100
CODE_BLK = 0x6A       # 1101010

# CCIR 476 码表（bit7→bit1 MSB first；与公开锚点交叉验证：G/H/L/N/NUL
# 与维基 SITOR 词条 bit 串一致，全表经恒比性校验）
CCIR476_LETTERS: Dict[str, int] = {
    "A": 0x47, "B": 0x72, "C": 0x1D, "D": 0x53, "E": 0x56, "F": 0x1B,
    "G": 0x35, "H": 0x69, "I": 0x4D, "J": 0x17, "K": 0x1E, "L": 0x65,
    "M": 0x39, "N": 0x59, "O": 0x71, "P": 0x2D, "Q": 0x2E, "R": 0x55,
    "S": 0x4B, "T": 0x74, "U": 0x4E, "V": 0x3C, "W": 0x27, "X": 0x3A,
    "Y": 0x2B, "Z": 0x63,
}
CCIR476_FIGURES: Dict[str, int] = {
    "-": 0x47, "?": 0x72, ":": 0x1D, "ENQ": 0x53, "3": 0x56, "!": 0x1B,
    "&": 0x35, "#": 0x69, "8": 0x4D, "BEL": 0x17, "(": 0x1E, ")": 0x65,
    ".": 0x39, ",": 0x59, "9": 0x71, "0": 0x2D, "1": 0x2E, "4": 0x55,
    "'": 0x4B, "5": 0x74, "7": 0x4E, ";": 0x3C, "2": 0x27, "/": 0x3A,
    "6": 0x2B, '"': 0x63,
}
# figures 与 letters 共享码值，仅解释不同（A 档='-'，B 档='?'，…，
# Z 档='"'；特殊：D 档=WRU、J 档=BEL）。

_CODE_TO_LETTER = {v: k for k, v in CCIR476_LETTERS.items()}
_FIG_BY_LETTERPOS = {  # 同一码值的 figures 解释（按字母位查）
    "A": "-", "B": "?", "C": ":", "D": "ENQ", "E": "3", "F": "!",
    "G": "&", "H": "#", "I": "8", "J": "BEL", "K": "(", "L": ")",
    "M": ".", "N": ",", "O": "9", "P": "0", "Q": "1", "R": "4",
    "S": "'", "T": "5", "U": "7", "V": ";", "W": "2", "X": "/",
    "Y": "6", "Z": '"',
}


def is_valid_code(code: int) -> bool:
    """CCIR 476 恒比校验：7 bit 中恰 4 个 mark。"""
    return 0 <= code < 128 and bin(code).count("1") == 4


def code_to_char(code: int, figures: bool = False) -> Optional[str]:
    """码值 → 字符；shift/控制/非法/BLK 码返回 None（调用方处理）。"""
    if not is_valid_code(code):
        return None
    if code in (CODE_LTRS, CODE_FIGS, CODE_BLK):
        return None                      # BLK：合法码但不产字符（空白）
    if code == CODE_CR:
        return "\r"
    if code == CODE_LF:
        return "\n"
    if code == CODE_SP:
        return " "
    letter = _CODE_TO_LETTER.get(code)
    if letter is None:
        return None                      # α/β/RQ 等控制码
    return _FIG_BY_LETTERPOS[letter] if figures else letter


def char_to_code(ch: str, figures: bool = False) -> Optional[int]:
    """字符 → 码值；不在当前 shift 集返回 None。"""
    if ch == "\r":
        return CODE_CR
    if ch == "\n":
        return CODE_LF
    if ch == " ":
        return CODE_SP
    if ch == "\x00":
        return CODE_BLK
    if not figures and ch in CCIR476_LETTERS:
        return CCIR476_LETTERS[ch]
    if figures:
        for letter, fig in _FIG_BY_LETTERPOS.items():
            if fig == ch:
                return CCIR476_LETTERS[letter]
    return None


# ---------------------------------------------------------------------------
# 文本 ↔ 码流（自动管理 LTRS/FIGS shift）
# ---------------------------------------------------------------------------
def encode_text(text: str) -> List[int]:
    """正文文本 → 码值流（自动插入 LTRS/FIGS shift；不支持的字符跳过）。

    CR/LF/SP 为档无关字符，直接发射不触发 shift 切换。
    """
    out: List[int] = []
    figures = False
    for ch in text.upper():
        if ch in ("\r", "\n", " "):
            out.append(char_to_code(ch))
            continue
        if ch == "\x00":
            continue
        in_letters = char_to_code(ch, figures=False) is not None
        need_figs = not in_letters and \
            char_to_code(ch, figures=True) is not None
        if need_figs and not figures:
            out.append(CODE_FIGS)
            figures = True
        elif in_letters and figures:
            out.append(CODE_LTRS)
            figures = False
        code = char_to_code(ch, figures=figures)
        if code is not None:
            out.append(code)
    if figures:
        out.append(CODE_LTRS)            # 收尾回 letters 档
    return out


def decode_text(codes: Sequence[int]) -> Tuple[str, int]:
    """码值流 → (文本, 丢弃控制码计数)。shift 码更新状态不产出字符。"""
    out: List[str] = []
    dropped = 0
    figures = False
    for code in codes:
        if code == CODE_LTRS:
            figures = False
            continue
        if code == CODE_FIGS:
            figures = True
            continue
        ch = code_to_char(code, figures=figures)
        if ch is None:
            dropped += 1
            continue
        out.append(ch)
    return "".join(out), dropped


# ---------------------------------------------------------------------------
# SITOR-B 时间分集（4 字符直接块 + 4 字符重复块）
# ---------------------------------------------------------------------------
DIVERSITY_SPAN = 4


def diversity_encode(codes: Sequence[int]) -> List[int]:
    """码流 → 分集线路码流：每 4 字符一块，直接块后紧跟其重复块。"""
    out: List[int] = []
    for i in range(0, len(codes), DIVERSITY_SPAN):
        chunk = list(codes[i:i + DIVERSITY_SPAN])
        while len(chunk) < DIVERSITY_SPAN:      # 尾块不足补 idle α
            chunk.append(IDLE_ALPHA)
        out += chunk + chunk
    return out


def diversity_decode(wire: Sequence[int]) -> Tuple[List[int], int, int]:
    """分集线路码流 → (恢复码流, 纠错恢复数, 不可恢复数)。

    每对（直接, 重复）做恒比校验：仅一侧合法取合法侧（纠错+1）；
    两侧皆合法但不相等取直接侧并计不可恢复；两侧皆非法补 BLK 并计
    不可恢复——绝不编造字符。
    """
    if len(wire) % (2 * DIVERSITY_SPAN) != 0:
        raise ValueError("分集码流长度须为 8 的整数倍")
    out: List[int] = []
    corrected = 0
    lost = 0
    for i in range(0, len(wire), 2 * DIVERSITY_SPAN):
        direct = wire[i:i + DIVERSITY_SPAN]
        repeat = wire[i + DIVERSITY_SPAN:i + 2 * DIVERSITY_SPAN]
        for d, r in zip(direct, repeat):
            dv, rv = is_valid_code(d), is_valid_code(r)
            if dv and rv:
                if d == r:
                    out.append(d)
                else:
                    out.append(d)
                    lost += 1                    # 双合法冲突：如实计
            elif dv or rv:
                out.append(d if dv else r)
                corrected += 1
            else:
                out.append(CODE_BLK)
                lost += 1
    return out, corrected, lost


# ---------------------------------------------------------------------------
# FSK 调制解调（连续相位，mark=高频）
# ---------------------------------------------------------------------------
def fsk_modulate(bits: Sequence[int], fs: float,
                 center_hz: float = 1000.0,
                 baud: float = NAVTEX_BAUD) -> np.ndarray:
    """位流 → 连续相位 FSK 实音频。bit 1=mark(高频)，bit 0=space(低频)。"""
    sps = fs / baud
    if sps < 4:
        raise ValueError(f"采样率过低（需 ≥ {4 * baud:.0f} Hz），收到 {fs}")
    n = int(round(len(bits) * sps))
    bit_idx = np.minimum((np.arange(n) / sps).astype(int), len(bits) - 1)
    freq = center_hz + np.where(np.asarray(bits)[bit_idx] > 0,
                                NAVTEX_MARK_HZ, -NAVTEX_MARK_HZ)
    phase = 2.0 * math.pi * np.cumsum(freq) / fs
    return np.cos(phase)


def fsk_demodulate(audio: np.ndarray, fs: float,
                   center_hz: float = 1000.0,
                   baud: float = NAVTEX_BAUD) -> List[int]:
    """FSK 实音频 → 位流。

    解析信号瞬时频率法（对非整周期采样鲁棒）：hilbert → 共轭差分 →
    瞬时频率；位定时扫描符号相位 offset，取符号中心采样判决置信度
    （|频率偏移| 均值）最大者；判决在符号中心进行，避开切换瞬态。
    """
    from scipy.signal import hilbert
    sps_f = fs / baud
    if sps_f < 4 or len(audio) < int(2 * sps_f):
        raise ValueError("音频过短或采样率过低")
    sps = int(round(sps_f))
    a = hilbert(np.asarray(audio, dtype=float))
    dphi = np.angle(a[1:] * np.conj(a[:-1]))
    inst_freq = dphi * fs / (2.0 * math.pi) - center_hz  # 以 center 为零点
    half = sps // 2
    n = len(inst_freq)
    # 位定时：符号内方差最小（正确 offset 下段内瞬时频率平坦 ±85 Hz；
    # 错位则段内骑跨切换过渡区，方差显著增大）。均值/分位数类指标在
    # 连续相位 FSK 上区分度不足（段内 |频偏| 处处 ≈85 Hz）。
    best_off, best_score = 0, float("inf")
    for off in range(sps):
        nsym = min(120, (n - off) // sps)
        if nsym < 2:
            continue
        segs = inst_freq[off:off + nsym * sps].reshape(nsym, sps)
        var = float(np.median(segs.var(axis=1)))
        if var < best_score:
            best_score, best_off = var, off
    idx = best_off + half + np.arange((n - half - best_off) // sps) * sps
    idx = idx[idx < n]
    return (inst_freq[idx] > 0).astype(int).tolist()


# ---------------------------------------------------------------------------
# 码流 ↔ 位流（7 bit MSB first）
# ---------------------------------------------------------------------------
def codes_to_bits(codes: Sequence[int]) -> List[int]:
    out: List[int] = []
    for code in codes:
        out += [(code >> (6 - i)) & 1 for i in range(7)]
    return out


def bits_to_codes(bits: Sequence[int], align: int = 0) -> List[int]:
    out: List[int] = []
    for i in range(align, len(bits) - 6, 7):
        v = 0
        for b in bits[i:i + 7]:
            v = (v << 1) | (b & 1)
        out.append(v)
    return out


# ---------------------------------------------------------------------------
# 报文组装与解析
# ---------------------------------------------------------------------------
PHASING_MIN_CHARS = 4        # 定相所需最少 α/RQ 连续字符数
PHASING_CHARS = 16           # 编码侧发射的定相字符数


def build_message(b1: str, b2: str, serial: str, text: str,
                  phasing_chars: int = PHASING_CHARS) -> List[int]:
    """组装完整 NAVTEX 报文码流：定相 + ZCZC + 头 + 正文 + NNNN。"""
    b1 = (b1 or "A")[0].upper()
    b2 = (b2 or "A")[0].upper()
    serial = (serial or "00")[:2]
    head = ("Z", "C", "Z", "C", b1, b2, serial[0], serial[1])
    head_codes: List[int] = []
    figures = False
    for ch in head:
        if ch.isdigit():
            if not figures:
                head_codes.append(CODE_FIGS)
                figures = True
        else:
            if figures:
                head_codes.append(CODE_LTRS)
                figures = False
        code = char_to_code(ch, figures=figures)
        if code is None:
            raise ValueError(f"报文头含非法字符: {ch!r}")
        head_codes.append(code)
    if figures:
        head_codes.append(CODE_LTRS)
    phasing = [IDLE_ALPHA if i % 2 == 0 else REPEAT_RQ
               for i in range(phasing_chars)]
    body = ([CODE_CR, CODE_LF] + encode_text(text) +
            [CODE_CR, CODE_LF] + encode_text("NNNN"))
    return phasing + head_codes + body


def parse_message_codes(codes: Sequence[int]) -> Optional[Dict]:
    """从（分集恢复后的）码流解析一条报文。

    返回 dict(b1, b2, serial, text, head_index) 或 None（找不到 ZCZC）。
    ZCZC 在 letters 档匹配；头部 B3B4 在 figures 档解析。
    """
    zc = (CCIR476_LETTERS["Z"], CCIR476_LETTERS["C"])
    n = len(codes)
    for i in range(n - 8):
        if tuple(codes[i:i + 4]) == (zc[0], zc[1], zc[0], zc[1]):
            figures = False
            fields: List[str] = []
            j = i + 4
            while j < n and len(fields) < 4:
                c = codes[j]
                j += 1
                if c == CODE_FIGS:
                    figures = True
                    continue
                if c == CODE_LTRS:
                    figures = False
                    continue
                ch = code_to_char(c, figures=figures)
                if ch is not None:
                    fields.append(ch)
            if len(fields) < 4:
                return None
            # 正文直到 NNNN（letters 档连续 4 个 N）
            n_code = CCIR476_LETTERS["N"]
            body: List[int] = []
            k = j
            while k < n:
                if tuple(codes[k:k + 4]) == (n_code,) * 4:
                    break
                body.append(codes[k])
                k += 1
            text, _dropped = decode_text(body)
            return {
                "b1": fields[0], "b2": fields[1],
                "serial": fields[2] + fields[3],
                "text": text, "head_index": i,
                "end_index": min(k + 4, n),
            }
    return None


# ---------------------------------------------------------------------------
# 顶层：音频 ↔ 报文
# ---------------------------------------------------------------------------
def navtex_encode_audio(b1: str, b2: str, serial: str, text: str,
                        fs: float, center_hz: float = 1000.0) -> np.ndarray:
    """报文 → 定相+分集+FSK 实音频（一个完整发射帧）。"""
    codes = build_message(b1, b2, serial, text)
    wire = diversity_encode(codes)
    bits = codes_to_bits(wire)
    return fsk_modulate(bits, fs, center_hz=center_hz)


def navtex_decode_audio(audio: np.ndarray, fs: float,
                        center_hz: float = 1000.0) -> Dict:
    """FSK 实音频 → 报文。恒比/分集错误如实计数，找不到报文诚实空态。"""
    bits = fsk_demodulate(audio, fs, center_hz=center_hz)
    # 字符对齐：扫描 7 种偏移，取恒比合法率最高者
    best_align, best_ratio, best_codes = 0, -1.0, []
    for align in range(7):
        codes = bits_to_codes(bits, align)
        if not codes:
            continue
        ratio = sum(1 for c in codes if is_valid_code(c)) / len(codes)
        if ratio > best_ratio:
            best_ratio, best_align, best_codes = ratio, align, codes
    messages: List[Dict] = []
    fec_corrected = 0
    fec_lost = 0
    phasing_ok = False
    # 定相检测：存在连续 ≥PHASING_MIN_CHARS 个 α/RQ
    run = 0
    for c in best_codes:
        if c in (IDLE_ALPHA, REPEAT_RQ):
            run += 1
            if run >= PHASING_MIN_CHARS:
                phasing_ok = True
                break
        else:
            run = 0
    # 分集恢复：位对齐可能吞掉头部若干定相码，4+4 块边界未知——
    # 按「配对一致率」排序 8 种分集相位（正确相位下直接/重复对全等），
    # 依次尝试解析，取首个完整报文；找不到则如实空态。
    usable_all = len(best_codes) - (len(best_codes) % (2 * DIVERSITY_SPAN))

    def _pair_score(phase: int, end: int) -> float:
        seg = best_codes[phase:end]
        same = total = 0
        for i in range(0, len(seg), 2 * DIVERSITY_SPAN):
            for j in range(DIVERSITY_SPAN):
                same += seg[i + j] == seg[i + DIVERSITY_SPAN + j]
                total += 1
        return same / total if total else 0.0

    candidates = []
    for phase in range(2 * DIVERSITY_SPAN):
        end = usable_all
        if (end - phase) < 2 * DIVERSITY_SPAN:
            continue
        end -= (end - phase) % (2 * DIVERSITY_SPAN)
        candidates.append((_pair_score(phase, end), phase, end))
    for _score, phase, end in sorted(candidates, reverse=True):
        recovered, corr, lost = diversity_decode(best_codes[phase:end])
        msg = parse_message_codes(recovered)
        if msg is not None:
            fec_corrected, fec_lost = corr, lost
            msg.pop("head_index", None)
            msg.pop("end_index", None)
            messages.append(msg)
            break
    return {
        "messages": messages,
        "num_messages": len(messages),
        "phasing_ok": phasing_ok,
        "valid_code_ratio": best_ratio,
        "fec_corrected": fec_corrected,
        "fec_lost": fec_lost,
    }

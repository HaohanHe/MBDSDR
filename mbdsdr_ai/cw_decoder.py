"""
MBDSDR 纯 Python CW（莫尔斯电报）解码器
========================================

时域包络检测 + 自适应阈值 + 点划/间隔分类，无需外部依赖。
输入为实数采样序列（已解调为音频包络或幅度包络），输出莫尔斯文本。

算法：
1. 短时能量包络，自适应阈值（取 5%/95% 分位的中点）
2. 检测电平跳变，得到 mark（按键）/space（停顿）段
3. 用 mark 段中位数估计单位点长 unit（自适应），>2*unit 判为划(-)
4. 用 space 长度切分（相对 unit）：<1.5=字符内，1.5~5=字符间，>=5=字间
5. 莫尔斯表映射为文本

MBDSDR Project - AI定义无线电 - GPL-3.0 - BI4MIB
"""

import math
import statistics
from typing import List, Dict, Tuple, Optional

MORSE_TABLE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", "-....-": "-",
    ".----.": "'", "-..-.": "/", "-.--.": "(", "-.--.-": ")",
    "---...": ":", "-.-.--": "!", "...-..-": "@", ".-...": "&",
}


def _smooth(env: List[float], win: int = 5) -> List[float]:
    """滑动平均平滑包络。"""
    n = len(env)
    if n < win:
        return env
    out = []
    half = win // 2
    for i in range(n):
        s = max(0, i - half)
        e = min(n, i + half + 1)
        out.append(sum(env[s:e]) / (e - s))
    return out


def _segments(env: List[float], threshold: float) -> List[Tuple[bool, int]]:
    """把包络按阈值压成 (on/off, 持续采样数) 段。"""
    segs = []
    cur = env[0] >= threshold if env else False
    cnt = 1
    for v in env[1:]:
        on = v >= threshold
        if on == cur:
            cnt += 1
        else:
            segs.append((cur, cnt))
            cur = on
            cnt = 1
    segs.append((cur, cnt))
    return segs


def decode_cw(samples: List[float], sample_rate: float = 11025,
              wpm: Optional[float] = None) -> Dict:
    """
    解码 CW 采样。

    samples: 实数采样（音频包络或幅度包络，非必须带直流）。
    sample_rate: 采样率。
    wpm: 已知电码速度（字/分）；给定时用它定单位，否则自动估计。

    返回 dict: text, chars, dit_ms, dah_ms, wpm_est, confidence。
    """
    if samples is None or len(samples) == 0:
        return {"text": "", "chars": [], "wpm_est": 0, "confidence": 0}

    n = len(samples)
    # 去直流
    mean = sum(samples) / n
    env = [abs(s - mean) for s in samples]
    env = _smooth(env, max(3, n // 200 | 1))

    # 自适应阈值
    lo, hi = sorted(env)[int(n * 0.1)], sorted(env)[int(n * 0.9)]
    thr = lo + (hi - lo) * 0.4

    segs = _segments(env, thr)
    on_durs = [d for on, d in segs if on and d > 0]
    if not on_durs:
        return {"text": "", "chars": [], "wpm_est": 0, "confidence": 0}

    # 单位点长：用 mark 段中位数；划约 3 倍
    dit_samp = statistics.median(on_durs)
    # 短 mark 才是点，长 mark 是划。点长取最短众数
    short_marks = [d for d in on_durs if d < dit_samp * 1.6]
    if short_marks:
        dit_samp = statistics.median(short_marks)
    unit = dit_samp
    unit_ms = unit / sample_rate * 1000.0
    wpm_est = 1200.0 / unit_ms if unit_ms > 0 else 0  # PARIS: 1dit=1.2s/50=24ms@12wpm

    # 遍历段，切字符。Morse 标准时长（unit = 1 dit）：
    #   dot=1, dash=3, 字符内元素间隔=1, 字符间隔=3, 单词间隔=7。
    # 用自适应 unit（取点长中位数）按阈值划分 off 段：
    #   gap < 1.5*unit            -> 同一字符内的点划间隔（不切分）
    #   1.5*unit <= gap < 5*unit  -> 字符间隔（新字母）
    #   gap >= 5*unit             -> 单词间隔（新单词）
    letters = []
    cur_mark = []
    for on, d in segs:
        if on:
            sym = "-" if d > unit * 2.0 else "."  # dash=3 unit，中点阈值 2.0
            cur_mark.append(sym)
        else:
            if d >= unit * 5.0:        # 单词间隔（标准 7 unit）
                if cur_mark:
                    letters.append(("".join(cur_mark), "word"))
                    cur_mark = []
            elif d >= unit * 1.5:      # 字符间隔（标准 3 unit）
                if cur_mark:
                    letters.append(("".join(cur_mark), "char"))
                    cur_mark = []
            # gap < 1.5*unit：字符内元素间隔，保持当前字符不切开
    if cur_mark:
        letters.append(("".join(cur_mark), "char"))

    text_parts = []
    chars = []
    for code, kind in letters:
        ch = MORSE_TABLE.get(code, "?")
        if kind == "word":
            text_parts.append(" ")
        text_parts.append(ch)
        chars.append({"code": code, "char": ch})

    text = "".join(text_parts).strip()
    known = sum(1 for c in chars if c["char"] != "?")
    conf = known / len(chars) if chars else 0

    return {
        "text": text,
        "chars": chars,
        "dit_ms": round(unit_ms, 1),
        "dah_ms": round(unit_ms * 3, 1),
        "wpm_est": round(wpm_est, 1),
        "confidence": round(conf, 2),
    }


def encode_cw(text: str) -> List[Tuple[str, int]]:
    """把文本编成 (符号, 采样数) 的 CW 波形事件，用于生成测试信号。
    单位点长 60 采样（约 12.5 WPM @11025）。"""
    inv = {v: k for k, v in MORSE_TABLE.items()}
    unit = 60
    events = []
    for i, ch in enumerate(text.upper()):
        if ch == " ":
            events.append(("space", unit * 7))
            continue
        code = inv.get(ch, "")
        for j, sym in enumerate(code):
            events.append(("on", unit * (3 if sym == "-" else 1)))
            if j < len(code) - 1:
                events.append(("off", unit))
        events.append(("off", unit * 3))
    return events

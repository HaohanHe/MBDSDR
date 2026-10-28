# SPDX-License-Identifier: MIT
"""
MBDSDR 纯 Python CW（莫尔斯电报）解码器
========================================

时域包络检测 + 自适应阈值 + 点划/间隔分类，无需外部依赖。
输入为实数采样序列（已解调为音频包络或幅度包络），输出莫尔斯文本。

算法：
1. 短时能量包络，自适应阈值（取 10%/90% 分位间 0.4 处）
2. 检测电平跳变，得到 mark（按键）/space（停顿）段
3. 以最短 mark 锚定单位点长 unit（自适应），>2*unit 判为划(-)
4. 用 space 长度切分（相对 unit）：<1.5=字符内，1.5~5=字符间，>=5=字间
5. 莫尔斯表映射为文本（ITU 莫尔斯码，公开域）。
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


def _empty_result() -> Dict:
    """诚实空态：无信号/无法估计时的统一结果（键集与正常返回一致）。"""
    return {"text": "", "chars": [], "dit_ms": 0, "dah_ms": 0,
            "wpm_est": 0, "confidence": 0}


def decode_cw(samples: List[float], sample_rate: float = 11025,
              wpm: Optional[float] = None) -> Dict:
    """
    解码 CW 采样。

    samples: 实数采样，两种输入都支持：
      - 幅度包络（非负，如 OOK 包络）：直接当包络用；
      - 交流音频（含负值）：全波整流（减均值）提取包络。
    sample_rate: 采样率。
    wpm: 已知电码速度（字/分）；给定时用它定单位（unit = 1200/wpm 毫秒），
        否则自动估计。

    返回 dict: text, chars, dit_ms, dah_ms, wpm_est, confidence。
    """
    if samples is None or len(samples) == 0:
        return _empty_result()

    n = len(samples)
    mean = sum(samples) / n
    if min(samples) < 0:
        # 交流音频输入：全波整流取包络（减均值防直流偏置）
        env = [abs(s - mean) for s in samples]
    else:
        # 幅度包络输入：直接用作包络。不能做 abs(s - mean)——占空比
        # >50% 时均值偏在 mark 侧，整流会把 mark/space 反相
        # （SOS 这类密电码会被误成乱码）。
        env = [float(s) for s in samples]
    env = _smooth(env, max(3, n // 200 | 1))

    # 自适应阈值
    sorted_env = sorted(env)
    lo, hi = sorted_env[int(n * 0.1)], sorted_env[int(n * 0.9)]
    if hi <= lo:
        # 无动态范围（全零/常量/单样本）：没有按键信息，诚实空态，
        # 不把静默伪解成字符
        return _empty_result()
    thr = lo + (hi - lo) * 0.4

    segs = _segments(env, thr)
    on_durs = [d for on, d in segs if on and d > 0]
    if not on_durs:
        return _empty_result()

    if wpm is not None and wpm > 0:
        # 已知电码速度：unit = 1200/wpm 毫秒（PARIS 50 单位/词）
        dit_samp = (1200.0 / float(wpm)) / 1000.0 * float(sample_rate)
    else:
        # 点长锚定最短 mark（点=1 unit 是最短元素、划=3 unit），并取与
        # 最短 mark 同簇（<1.6 倍）的中位数抗噪。旧算法取全体 mark 中位数：
        # 划多于点的报文（如 "TEST 123" 数字段）中位数落在划上，unit 被
        # 高估 3 倍，字内/字间间隔全部塌进字符内 -> 乱码。
        d_min = min(on_durs)
        short_marks = [d for d in on_durs if d < d_min * 1.6]
        dit_samp = statistics.median(short_marks)
    unit = dit_samp
    unit_ms = unit / sample_rate * 1000.0
    wpm_est = 1200.0 / unit_ms if unit_ms > 0 else 0  # unit_ms = 1200 / wpm

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
        text_parts.append(ch)
        # kind 是该字母**之后**的间隔：word 间隔要落在字母后面，
        # 否则空格会插到前一词的末字母前（"CQ DE" 会被拼成 "C QD E"）
        if kind == "word":
            text_parts.append(" ")
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
    单位点长 60 采样（≈5.44 ms，约 220.5 WPM @11025）。"""
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

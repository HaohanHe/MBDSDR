# SPDX-License-Identifier: MIT
"""
cw_decoder.py（纯 Python 莫尔斯解码）行为测试
=============================================

用合成包络/合成音频钉行为，oracle 来自被测模块的真实语义：
  - encode_cw 事件流（unit=60 采样）合成矩形包络 -> decode_cw 往返；
  - wpm 参数语义：给定 wpm 时单位点长 = 1200/wpm 毫秒（模块 docstring）；
  - 空/常量输入 = 诚实空态（无按键信息不得伪解出字符）；
  - 词间空格应落在前一词末字母之后（"CQ DE" 而非 "C QD E"）；
  - 占空比 >50% 的密电码（如 SOS）不得因整流反相被解乱。
fixture 呼号按 AGENTS.md 约定用中性值 BI1AAA。

运行：python -m pytest mbdsdr_ai/tests/test_cw_decoder_behavior.py -q
"""
from __future__ import annotations

import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.cw_decoder import (  # noqa: E402
    MORSE_TABLE, decode_cw, encode_cw,
)

SR = 11025.0
EMPTY_KEYS = {"text", "chars", "dit_ms", "dah_ms", "wpm_est", "confidence"}


def _synth_envelope(text: str, unit: int = 60) -> list:
    """按 encode_cw 事件合成矩形包络（幅度包络输入，非负）。"""
    samples = []
    for kind, dur in encode_cw(text):
        samples.extend([1.0 if kind == "on" else 0.0] * dur)
    return samples


def _synth_with_unit(text: str, unit: int, sample_rate: float) -> list:
    """按给定 unit（采样）合成矩形包络（encode_cw 的 unit 固定 60，此处自定）。"""
    inv = {v: k for k, v in MORSE_TABLE.items()}
    samples = []
    for ch in text.upper():
        if ch == " ":
            samples.extend([0.0] * (unit * 7))
            continue
        code = inv.get(ch, "")
        for j, sym in enumerate(code):
            samples.extend([1.0] * (unit * (3 if sym == "-" else 1)))
            if j < len(code) - 1:
                samples.extend([0.0] * unit)
        samples.extend([0.0] * (unit * 3))
    return samples


def _synth_audio(text: str, unit: int, sample_rate: float, f_hz: float) -> list:
    """合成交流音频（正弦 OOK）——走音频整流分支。"""
    inv = {v: k for k, v in MORSE_TABLE.items()}
    samples = []
    t = 0

    def add(n: int, on: bool):
        nonlocal t
        for i in range(n):
            samples.append(
                math.sin(2 * math.pi * f_hz * (t + i) / sample_rate) if on else 0.0)
        t += n

    for ch in text.upper():
        if ch == " ":
            add(unit * 7, False)
            continue
        code = inv.get(ch, "")
        for j, sym in enumerate(code):
            add(unit * (3 if sym == "-" else 1), True)
            if j < len(code) - 1:
                add(unit, False)
        add(unit * 3, False)
    return samples


# ─────────────────────────── 往返（合成包络） ───────────────────────────

def test_roundtrip_sos_high_duty_envelope():
    # SOS 占空比 >50%：旧实现 abs(s-mean) 把 mark/space 反相，解成 'E'
    r = decode_cw(_synth_envelope("SOS"), SR)
    assert r["text"] == "SOS"
    assert r["confidence"] == 1.0
    assert [c["code"] for c in r["chars"]] == ["...", "---", "..."]


def test_roundtrip_multiword_fixture_callsign():
    # 词间空格必须落在前一词末字母之后（旧实现得 'C QD EBI1AAA'）
    r = decode_cw(_synth_envelope("CQ DE BI1AAA"), SR)
    assert r["text"] == "CQ DE BI1AAA"
    assert r["confidence"] == 1.0


def test_roundtrip_letters_and_digits():
    # 数字段划多于点（整体划>点）：旧的「全体 mark 中位数」估点长会把 unit
    # 抬高 3 倍、整段塌成 '??'——本用例钉最短 mark 锚定的自适应估计
    r = decode_cw(_synth_envelope("TEST 123"), SR)
    assert r["text"] == "TEST 123"
    assert r["confidence"] == 1.0


# ─────────────────────────── 空态 / 边界 ───────────────────────────

@pytest.mark.parametrize("samples", [
    [],
    [0.0] * 200,
    [1.0] * 200,
    [1.0],
])
def test_silence_and_constant_yield_honest_empty(samples):
    r = decode_cw(samples, SR)
    assert r["text"] == ""
    assert r["chars"] == []
    assert set(r) == EMPTY_KEYS  # 空态键集与正常返回一致（旧实现缺 dit_ms/dah_ms）


def test_empty_result_zero_metrics():
    r = decode_cw([0.0] * 200, SR)
    assert r["dit_ms"] == 0 and r["dah_ms"] == 0
    assert r["wpm_est"] == 0 and r["confidence"] == 0


def test_unknown_morse_pattern_reported_as_question():
    # 6 点 "......" 不在 ITU 表内 -> '?'，置信度 0（不伪造成字母）
    marks = []
    for i in range(6):
        marks.extend([1.0] * 60)
        marks.extend([0.0] * 60)
    marks.extend([0.0] * 180)
    r = decode_cw(marks, SR)
    assert r["chars"] == [{"code": "......", "char": "?"}]
    assert r["confidence"] == 0.0
    assert r["text"] == "?"


# ─────────────────────────── 速度估计 / wpm 参数 ───────────────────────────

def test_dit_estimate_matches_encode_unit():
    # encode_cw unit=60 采样 @11025 -> 5.442 ms = 220.5 WPM（闭式）
    r = decode_cw(_synth_envelope("SOS"), SR)
    assert abs(r["dit_ms"] - 60 / SR * 1000) <= 0.5
    assert abs(r["wpm_est"] - 1200.0 / (60 / SR * 1000)) <= 20.0
    assert abs(r["dah_ms"] - 3 * r["dit_ms"]) <= 0.5


def test_explicit_wpm_forces_unit():
    # wpm 参数此前是死参数（从未被使用）；语义：unit = 1200/wpm 毫秒
    # unit=384 采样 @8000 = 48 ms（25 WPM）
    samples = _synth_with_unit("SOS", 384, 8000)
    r25 = decode_cw(samples, 8000, wpm=25.0)
    assert r25["dit_ms"] == 48.0
    assert r25["wpm_est"] == 25.0
    assert r25["text"] == "SOS"
    # 强制 wpm=12（unit=100ms=800 采样）时 dit_ms 必须随参数走，而非信号实测
    r12 = decode_cw(samples, 8000, wpm=12.0)
    assert r12["dit_ms"] == 100.0
    assert r12["wpm_est"] == 12.0


# ─────────────────────────── 音频输入（整流分支） ───────────────────────────

def test_audio_tone_ook_decodes():
    # 400 Hz 正弦 OOK，unit=384@8000（记录够长，平滑窗 >> 载波周期）
    r = decode_cw(_synth_audio("SOS", 384, 8000, 400.0), 8000)
    assert r["text"] == "SOS"
    assert r["confidence"] == 1.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

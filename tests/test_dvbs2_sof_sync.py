# SPDX-License-Identifier: MIT
"""tests/test_dvbs2_sof_sync.py — DVB-S2 raw IQ SOF 同步层补网钉扎。

P3.1「缺口驱动补网」：test_fengyun_dvbs2.py 文件头自承「没有 raw IQ
同步」——本层补上这一环：raw 符号流 → find_sof_candidates（非相干
滑动相关 + NMS）→ parse_plframe → decode_qpsk_plframe → FECFRAME
比特逐位一致。

SOF 协议锚点（防「凭记忆」漂移）：ETSI EN 302 307-1 V1.4.1 §5.5.2.1
官方原文（2026-11-04 下载 etsi.org PDF 第 31 页核对）：
"SOF shall correspond to the sequence 18D2E82HEX (01-1000-....-0010
in binary notation, the left-side bit being the MSB of the PLHEADER)."

真样本缺口（P3.1 口径，诚实写明）：
  1. ~~DATAFIELD → VCDU/TS 重组未实现（BBFRAME datafield 到上层）~~
     **P5.1 已收口**：`mbdsdr_ai/dvbs2_datafield.py`
     （BBHEADER CRC-8 → DFL 截取 → SYNCD 定位 → VCDU 帧 + 完整性统计），
     钉扎见 `tests/test_dvbs2_datafield_vcdu.py`；
  2. 风云对星真实 OTA IQ 样本未获得——本文件全部为合成链自洽金样。
  最小可复现采样条件：FY-3/FY-4 下行频点（如 FY-4A L 波段 HRIT
  或 FY-3 X 波段）+ 符号率级复基带 .cf32，时长 ≥ 3 个 PLFRAME
  （normal QPSK 1/2 帧 33282 符号），载波粗中（CFO ≪ 符号率/26）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.dvbs2_pl import (  # noqa: E402
    SOF_BITS, SOF_SYMBOLS, SOF_VALUE, decode_qpsk_plframe,
    encode_qpsk_plframe, find_sof_candidates, modulate_plheader,
    parse_plframe, plframe_symbol_count, sof_reference_symbols,
)

MODCOD_QPSK_HALF = 4          # QPSK 1/2（ETSI 表 12：04H）
FRAME = "normal"


def _make_frame_bits(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2, 64800, dtype=np.uint8)


def _noise(rng, n):
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)


# ---------------------------------------------------------------------------
# 协议锚点（ETSI 原文核对值）
# ---------------------------------------------------------------------------
class TestSofAnchor:
    def test_sof_value_matches_etsi(self):
        """SOF 常量 == 0x18D2E82（ETSI EN 302 307-1 §5.5.2.1 原文核对）。"""
        assert SOF_VALUE == 0x18D2E82
        assert len(SOF_BITS) == 26
        # 26 位 MSB-first：0x18D2E82 = 01-1000-....-0010（ETSI 原句）
        assert int("".join(str(int(b)) for b in SOF_BITS), 2) == 0x18D2E82

    def test_reference_symbols_match_plheader_rule(self):
        """SOF 参考符号 == modulate_plheader 前 26 符号（同一 π/2BPSK 规则）。"""
        bits = np.zeros(90, dtype=np.uint8)
        bits[:SOF_SYMBOLS] = SOF_BITS
        header = modulate_plheader(bits)
        np.testing.assert_allclose(sof_reference_symbols(),
                                   header[:SOF_SYMBOLS], atol=1e-12)


# ---------------------------------------------------------------------------
# SOF 搜索层
# ---------------------------------------------------------------------------
class TestSofSync:
    def _two_frame_stream(self, seed=11):
        rng = np.random.default_rng(seed)
        f1 = encode_qpsk_plframe(_make_frame_bits(1), MODCOD_QPSK_HALF, FRAME)
        f2 = encode_qpsk_plframe(_make_frame_bits(2), MODCOD_QPSK_HALF, FRAME)
        pre, gap, tail = 137, 53, 200
        stream = np.concatenate([
            _noise(rng, pre), f1.symbols, _noise(rng, gap),
            f2.symbols, _noise(rng, tail),
        ])
        frame_len = plframe_symbol_count(MODCOD_QPSK_HALF, FRAME)
        return stream, [pre, pre + frame_len + gap], (f1, f2), frame_len

    def test_two_frames_exact_starts(self):
        stream, starts, _, _ = self._two_frame_stream()
        cands = find_sof_candidates(stream)
        assert [c[0] for c in cands] == starts
        assert all(c[1] > 0.99 for c in cands)

    def test_phase_rotation_invariant(self):
        stream, starts, _, _ = self._two_frame_stream()
        rotated = stream * np.exp(1j * 0.7)
        cands = find_sof_candidates(rotated)
        assert [c[0] for c in cands] == starts

    def test_slow_cfo_tolerated(self):
        stream, starts, _, _ = self._two_frame_stream()
        n = np.arange(len(stream))
        cfo = stream * np.exp(1j * 2 * np.pi * 0.001 * n)   # 0.026 rad/26 符号
        cands = find_sof_candidates(cfo)
        assert [c[0] for c in cands] == starts

    def test_pure_noise_honest_empty(self):
        rng = np.random.default_rng(99)
        cands = find_sof_candidates(_noise(rng, 5000))
        assert cands == [], "纯噪声必须诚实空态（不凑候选）"

    def test_short_input_empty(self):
        assert find_sof_candidates(np.ones(10, dtype=np.complex128)) == []


# ---------------------------------------------------------------------------
# 端到端补网：raw IQ → SOF 同步 → 解帧 → FECFRAME 逐位一致
# ---------------------------------------------------------------------------
class TestRawIqToFecframe:
    def test_stream_to_fecframe_bits(self):
        stream, starts, _, frame_len = TestSofSync()._two_frame_stream()
        cands = find_sof_candidates(stream)
        assert [c[0] for c in cands] == starts
        for (start, _m), seed in zip(cands, (1, 2)):
            window = stream[start:start + frame_len]
            decoded = decode_qpsk_plframe(window, max_sof_distance=0)
            np.testing.assert_array_equal(decoded.fecframe_bits,
                                          _make_frame_bits(seed))

    def test_parse_consumed_advances(self):
        """parse_plframe 消费符号数 == 帧长（流式前进口径）。"""
        stream, starts, _, frame_len = TestSofSync()._two_frame_stream()
        parsed = parse_plframe(stream[starts[0]:], allow_trailing=True)
        assert parsed.consumed_symbols == frame_len

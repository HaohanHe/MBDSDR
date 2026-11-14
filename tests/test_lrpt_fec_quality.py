# SPDX-License-Identifier: MIT
"""12.1 每帧 FEC 纠正量进质量面 —— 判据。

钉三件事：
  1. ``summarize_rs_errors`` 的聚合口径与逐块和一致（不可纠帧不进均值/最大值）；
  2. 真录链 ``decode_recording_to_image`` 的 stats 里真的有这组键，且
     「ASM 命中帧 = 参与统计的帧」（不许悄悄少算）；
  3. 空输入给空值，不拿 0 冒充「零纠错」。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.lrpt_pipeline import (  # noqa: E402
    LrptE2EPipeline,
    decode_recording_to_image,
    summarize_rs_errors,
)

_MANIFEST = os.path.join(
    _ROOT, "tests", "golden", "lrpt_meteor_real_soft_manifest.json")


# --------------------------------------------------------------------------- #
# 1) 纯聚合口径
# --------------------------------------------------------------------------- #
class TestSummarizeContract:
    def test_known_frames_match_block_sums(self):
        """已知逐块序列：聚合值与手算的逐帧块和一致。"""
        frames = [
            [0, 0, 0, 0],        # 无纠错
            [3, 0, 1, 0],        # 纠 4 个符号
            [16, 16, -1, 0],     # 不可纠（任一块 -1）
        ]
        got = summarize_rs_errors(frames)
        assert got["rs_frames"] == 3
        assert got["rs_corrected_frames"] == 1
        assert got["rs_max_errors"] == 4
        assert got["rs_uncorrectable_frames"] == 1
        # 均值只算可纠帧：(0 + 4) / 2 = 2.0
        assert got["rs_mean_errors"] == 2.0

    def test_uncorrectable_frame_excluded_from_mean(self):
        """不可纠帧不参与均值/最大值，但仍占「总帧数」这一格。"""
        got = summarize_rs_errors([[8, 8, 8, 8], [-1, 0, 0, 0]])
        assert got["rs_frames"] == 2
        assert got["rs_uncorrectable_frames"] == 1
        assert got["rs_max_errors"] == 32
        assert got["rs_mean_errors"] == 32.0
        assert (got["rs_corrected_frames"]
                + 0 <= got["rs_frames"] - got["rs_uncorrectable_frames"])

    def test_empty_is_null_not_zero(self):
        """没有任何帧：计数为 0，最大/均值给 None（空态，不伪造读数）。"""
        got = summarize_rs_errors([])
        assert got == {
            "rs_frames": 0,
            "rs_corrected_frames": 0,
            "rs_max_errors": None,
            "rs_uncorrectable_frames": 0,
            "rs_mean_errors": None,
        }

    def test_real_manifest_frames(self):
        """真录固定夹具（2026-11-07 重钉）的 rs_errors 聚合可复算。"""
        with open(_MANIFEST, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        frames = [fr["rs_errors"] for fr in manifest["frames"]]
        got = summarize_rs_errors(frames)
        frame_sums = [sum(e) for e in frames if all(e >= 0 for e in e)]
        assert got["rs_frames"] == len(frames)
        assert got["rs_max_errors"] == max(frame_sums)
        assert got["rs_mean_errors"] == round(sum(frame_sums) / len(frame_sums), 3)
        assert got["rs_uncorrectable_frames"] == 0


# --------------------------------------------------------------------------- #
# 2) 真录链接线：stats 里必须有这组键
# --------------------------------------------------------------------------- #
class TestRecordingChainStats:
    def test_injected_errors_aggregate_exactly(self, monkeypatch):
        """注入已知逐块纠错数：stats 聚合值与注入值逐项相等。"""
        from mbdsdr_ai import lrpt_pipeline as lp
        from mbdsdr_ai.lrpt_modem import FrameCandidate

        pipe = LrptE2EPipeline()
        rng = np.random.default_rng(121)
        payload = rng.integers(0, 256, size=892, dtype=np.uint8).tobytes()
        iq = pipe.build_tx_iq(payload)

        cands = [FrameCandidate(sync_symbol_index=0),
                 FrameCandidate(sync_symbol_index=20_000)]
        fake_soft = np.ones(100_000, dtype=np.uint8)
        monkeypatch.setattr(
            lp.LrptDemodulator, "demodulate_soft",
            lambda self, arr, max_hamming=8: (fake_soft, list(cands)))
        monkeypatch.setattr(
            lp, "_suppress_ghosts", lambda c, radius=32: list(c))

        # 第一帧纠 4 个符号（可纠、结构合法），第二帧一块不可纠。
        frames = iter([
            (bytes([0x40]) + b"\x00" * 891, [3, 0, 1, 0], [0, 0, 0, 0]),
            (b"", [-1, 0, 0, 0], [0, 0, 0, 0]),
        ])
        monkeypatch.setattr(
            lp.LrptCaduDecoder, "decode_cadu_erasure",
            lambda self, v, rel, order="woven", ladder=None: next(frames))
        monkeypatch.setattr(
            lp.LrptCaduDecoder, "decode_viterbi_to_bytes",
            lambda self, soft, return_reliability=False: (
                lp.CADU_ASM + b"\x00" * (lp.CADU_SIZE - 4),
                np.zeros(lp.CADU_SIZE)))

        res = decode_recording_to_image(iq, float(pipe.mod.fs))
        st = res.stats
        for key in ("rs_frames", "rs_corrected_frames", "rs_max_errors",
                    "rs_uncorrectable_frames", "rs_mean_errors"):
            assert key in st, f"stats 缺 {key}"
        assert st["rs_frames"] == st["n_asm"] == 2
        assert st["rs_corrected_frames"] == 1
        assert st["rs_max_errors"] == 4
        assert st["rs_uncorrectable_frames"] == 1
        assert st["rs_mean_errors"] == 4.0
        # 不可纠帧不进有效 VCDU：只有第一帧（结构合法）能留下。
        assert st["n_valid_vcdu"] == 1

    def test_noise_gives_empty_stats_not_zero(self):
        """纯噪声：没有 ASM 帧 → 计数 0，最大/均值 None（空态）。"""
        pipe = LrptE2EPipeline()
        rng = np.random.default_rng(20261114)
        noise = (rng.standard_normal(200_000)
                 + 1j * rng.standard_normal(200_000)).astype(np.complex64)
        res = decode_recording_to_image(noise, 576_000.0)
        st = res.stats
        assert st["rs_frames"] == 0
        assert st["rs_max_errors"] is None
        assert st["rs_mean_errors"] is None

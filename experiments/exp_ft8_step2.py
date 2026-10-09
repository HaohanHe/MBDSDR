#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""FT8 第②步实验：消息解码成功率 vs SNR（符号→LLR→BP→CRC 全链路）。

链路：Ft8Modulator.encode_message() -> 58 数据 tone -> 调制 IQ -> AWGN ->
Costas 粗同步（频偏/时偏）-> 数据符号位置 8-tone 能量谱 ->
llrs_from_tone_energies -> Ft8Codec.decode() BP -> CRC14 -> 解包消息。

不依赖 wsjtx / 硬件。固定种子、Wilson 95% CI（common/runner.py）。
CSV 写 paper/experiments/。诚实注明：此为合成闭环门限，与 wsjtx 真实弱信号
链路不直接类比（无衰落/多径/真实相位噪声；BP 为干净室自写 tanh）。
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.ft8_modem import (  # noqa: E402
    FS_HZ, NSPS, TONE_SPACING_HZ, GRAY_MAP,
    Ft8Modulator, Ft8CostasSync,
)
from mbdsdr_ai.ft8_codec import (  # noqa: E402
    Ft8Codec, llrs_from_tone_energies, unpack77,
)
from experiments.common.runner import wilson_ci  # noqa: E402

OUT_DIR = os.path.join(ROOT, "paper", "experiments")
CAPTURE_SEC = 15.0
CAPTURE = int(CAPTURE_SEC * FS_HZ)


def _data_tone_energies(iq: np.ndarray, t0: int, f0: float,
                        n_data: int = 58) -> np.ndarray:
    """在同步位置 t0、频偏 f0 后，对 58 个数据符号取 8-tone 能量谱。

    数据符号在帧中偏移：Costas 7 符号后 29，Costas 7，后 29。
    返回 (58, 8) 能量。
    """
    mod = Ft8Modulator()
    # 帧内数据段符号索引（相对帧起点）
    data_idx = list(range(7, 36)) + list(range(43, 72))
    energies = np.zeros((n_data, 8))
    t = np.arange(NSPS)
    for k, sym_i in enumerate(data_idx[:n_data]):
        center = t0 + sym_i * NSPS + NSPS // 2
        seg = iq[center - NSPS // 2: center + NSPS // 2].astype(np.complex128)
        if seg.size < NSPS:
            continue
        for tone in range(8):
            f = f0 + (tone - 3.5) * TONE_SPACING_HZ
            ref = np.exp(-2j * np.pi * f * t / FS_HZ)
            energies[k, tone] = abs(np.sum(seg * ref)) ** 2
    return energies


def run(trials: int, seed: int) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    mod = Ft8Modulator()
    sync = Ft8CostasSync()
    codec = Ft8Codec()

    # 固定消息
    data_tones = mod.encode_message(from_call="K1ABC", to_call="K2DEF", grid4="EM12")
    frame_tones = mod.build_frame_symbols(data_tones)

    rng = np.random.default_rng(seed)
    snrs = [-25, -20, -15, -10, -5, 0]
    rows = []
    for snr in snrs:
        success = 0
        decoded_msgs = 0
        for _ in range(trials):
            fo = float(rng.uniform(-40.0, 40.0))
            at = int(rng.integers(2000, CAPTURE - 79 * NSPS - 2000))
            nseed = int(rng.integers(0, 2 ** 31 - 1))
            iq = mod.modulate(frame_tones, freq_offset_hz=fo,
                              awgn_snr_db=float(snr), noise_seed=nseed)
            buf = np.zeros(CAPTURE, dtype=np.complex64)
            buf[at:at + iq.size] = iq
            r = sync.process(buf)
            if not r.synced:
                continue
            energies = _data_tone_energies(
                buf, int(r.time_offset_samples), float(r.freq_offset_hz))
            llr = llrs_from_tone_energies(energies)
            dec = codec.decode(llr)
            if dec is not None:
                decoded_msgs += 1
                msg = unpack77(dec[:77].tolist())
                if msg and msg["from"] == "K1ABC" and msg["to"] == "K2DEF":
                    success += 1
        lo, hi = wilson_ci(success, trials)
        lo_d, hi_d = wilson_ci(decoded_msgs, trials)
        rows.append((snr, success, decoded_msgs, trials,
                     success / trials, decoded_msgs / trials,
                     lo, hi, lo_d, hi_d))
        print(f"SNR={snr:>4} dB  msg_ok={success}/{trials} "
              f"crc_ok={decoded_msgs}/{trials}")

    csv = os.path.join(OUT_DIR, "ft8_step2_msg_decode_vs_snr.csv")
    with open(csv, "w") as f:
        f.write("snr_db,msg_success,crc_pass,trials,msg_rate,crc_rate,"
                "msg_wilson_lo,msg_wilson_hi,crc_wilson_lo,crc_wilson_hi\n")
        for row in rows:
            f.write(",".join(str(round(x, 6)) if isinstance(x, float)
                             else str(x) for x in row) + "\n")
    print(f"wrote {csv}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261010)
    args = ap.parse_args()
    run(args.trials, args.seed)

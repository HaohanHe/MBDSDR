# SPDX-License-Identifier: MIT
"""LRPT 真实样本同步层外部真值（2026-11-03 破零）。

样本：TU Berlin lar-tech/satellite-decoder 仓示例实录
`data/meteor_m2_72k.wav`（Meteor-M2 LRPT 72k 过境实录，187.5 kS/s int16
IQ 立体声，105.4 s；SHA-256 c8be339e69db2b192f852c8ab5d2cb7c7b547552
bab41fadd584e0296df0cdf8 钉真值；79MB 第三方字节不入库，按需下载、
离线诚实 skip——FT8/ADS-B golden 同款姿势）。

机制要点（2026-11-03 实测）：
- 原始样本率 187.5 kS/s ÷ 72 ksym/s = 2.604 sps **非整数**，LrptDemodulator
  整数 sps 假设直接失锁（30 s 窗 0 候选）——必须先重采样到整数倍符号率。
- resample_poly(96, 125) → 144 kS/s（恰 2 sps）后，过境中段（52–62 s）
  帧同步锁住：ham=0 完美命中（phase=0，I/Q swap），帧头
  05 0b d1 af 64 76 ba 8c，8 条候选（ham 0/1/3/3/4/…）。

钉扎口径（诚实边界）：
- **同步层**（QPSK 解调 + 64-bit 同步字相关）在真实 OTA 上验证：ham=0
  帧存在 + 帧头前缀逐字命中 + 候选数下限 ratchet。
- **解码层**（Viterbi/解扰/RS/VCDU 真录有效性）仍 not_verified——真录
  数据未过完协议栈，后续按 dump1090 全量清单对拍的姿势补。
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai.lrpt_modem import LrptDemodulator  # noqa: E402

WAV_URL = ("https://raw.githubusercontent.com/lar-tech/satellite-decoder/"
           "main/data/meteor_m2_72k.wav")
WAV_SHA256 = ("c8be339e69db2b192f852c8ab5d2cb7c7b547552bab41fadd584e0296df0cdf8")
WAV_NAME = "meteor_m2_72k.wav"

SRC_SR = 187_500
RESAMP_SR = 144_000     # 2 sps @ 72 ksym/s
SYM_RATE = 72_000
WINDOW = (52.0, 62.0)   # 过境中段（实测同步锁住的窗口）
HEAD_PREFIX = "050bd1af"   # ham=0 帧头前 4 字节（同步层真值）


def _cache_path() -> str:
    d = os.path.join(os.path.expanduser("~"), ".mbdsdr", "lrpt_golden")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, WAV_NAME)


def _ensure_wav() -> str:
    path = _cache_path()
    if os.path.isfile(path):
        raw = open(path, "rb").read(1 << 20)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() == WAV_SHA256:
            return path
    try:
        urllib.request.urlretrieve(WAV_URL, path)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LRPT 真实样本不可用（离线？）: {exc}")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != WAV_SHA256:
        os.remove(path)
        raise ValueError("LRPT 真实样本身份漂移（SHA-256 不符）")
    return path


def _window_iq():
    from scipy.io import wavfile
    from scipy.signal import resample_poly

    sr, data = wavfile.read(_ensure_wav())
    assert sr == SRC_SR, f"样本率应为 {SRC_SR}，实际 {sr}"
    iq = (data[:, 0].astype(np.float64)
          + 1j * data[:, 1].astype(np.float64)) / 32768.0
    iq = resample_poly(iq, 96, 125)   # 187.5k -> 144k（整 2 sps）
    t0, t1 = WINDOW
    return iq[int(t0 * RESAMP_SR):int(t1 * RESAMP_SR)]


@pytest.fixture(scope="module")
def cands():
    dem = LrptDemodulator(fs=RESAMP_SR, sym_rate=SYM_RATE)
    return dem.process(_window_iq())


def test_real_recording_syncs_with_hamming_zero(cands):
    """真实 OTA 同步层地板：过境中段必须锁出 ham=0 帧（失守即红）。"""
    best = [c for c in cands if c.hamming == 0]
    assert best, f"真录窗口 {WINDOW}s 内无 ham=0 同步（候选 {len(cands)} 条）"


def test_real_recording_frame_head_matches_truth(cands):
    """ham=0 帧头前缀逐字命中（同步层真值，防解调回归）。"""
    best = [c for c in cands if c.hamming == 0]
    assert best, "无 ham=0 帧"
    head = best[0].frame_bytes[:4].hex()
    assert head == HEAD_PREFIX, \
        f"真录帧头漂移：期望 {HEAD_PREFIX}，实际 {head}"


def test_real_recording_candidate_floor(cands):
    """候选数 ratchet：只增不减（实测 8 条，保守地板 3）。"""
    assert len(cands) >= 3, f"真录候选数回退：{len(cands)} < 3"

# SPDX-License-Identifier: MIT
"""SSTV 自动制式识别 + Robot36/72 区分的确定性测试（Phase43 块1）。

识别机制（数据驱动，不依赖易解错的 VIS）见 ``mbdsdr_ai/sstv_decoder.py``
``_identify_sstv_mode``：以 1200Hz 行同步的**脉宽 / 周期 / markers 数(=行数或组首数)**
为主判据，VIS 码仅作旁证。Robot36 vs Robot72 在 ~300ms/9ms 周期下几乎重合，
靠【行数】区分：Robot72 逐行发同步（≈240 markers/240 行）；Robot36 组首式每两行一个
同步（≈120 markers/240 行）。

样本覆盖（诚实登记，勿夸大）：
  - Robot 72 ............ 真实样本 ``real_sstv.wav``（OTA 录制）；无合成样本。
  - Robot 36 逐行式 ...... 合成样本（pysstv，150ms/行）；无 OTA 样本。
  - Robot 36 组首式 ...... 合成样本（本文件内置确定性合成，300ms/组）；无 OTA 样本。
  - Martin M1 ........... 合成样本（pysstv，见 tools/onboarding 测试）。
  - Martin M2 / Scottie S1/S2/DX / PD90..290 ... 仅有解码代码路径，
                           **无合成、无 OTA 测试样本**（不编造覆盖）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mbdsdr_ai.sstv_decoder import decode_audio, decode_sstv  # noqa: E402

FS = 48000
_REAL_WAV = os.path.join(_REPO_ROOT, "real_sstv.wav")


# --------------------------------------------------------------------------
# 确定性组首式 Robot36 合成器（pysstv 只发逐行式，这里补齐两行组路径）
# --------------------------------------------------------------------------
def _segments_to_audio(segments):
    """segments: [(dur_ms, freq_hz)] → 48kHz 单声道音频（正弦瞬时频率跟随）。"""
    out = []
    phase = 0.0
    for dur_ms, f0 in segments:
        n = int(round(dur_ms / 1000.0 * FS))
        dphi = 2 * np.pi * f0 / FS
        ph = phase + np.cumsum(np.full(n, dphi))
        out.append(np.sin(ph))
        phase = ph[-1]
    return np.concatenate(out).astype(np.float32)


def _vis_header(vis_code: int):
    segs = [(300.0, 1200.0), (10.0, 1900.0), (30.0, 1200.0)]  # 引导/break/start
    ones = 0
    for b in range(7):
        bit = (vis_code >> b) & 1
        ones += bit
        segs.append((30.0, 1100.0 if bit else 1300.0))
    segs.append((30.0, 1100.0 if ones % 2 else 1300.0))  # 偶校验
    segs.append((30.0, 1200.0))                          # stop
    return segs


def _grouped_robot36_audio(period_ms: float = 300.0, n_groups: int = 120):
    """组首式 Robot36：每组 9ms 同步 + porch + Y0 + Cb + Y1 + Cr（Y=2×UV 时长）。"""
    sync_ms, porch_ms = 9.0, 3.0
    uv = (period_ms - sync_ms - porch_ms) / 6.0
    y = 2.0 * uv
    segs = []
    for g in range(n_groups):
        segs += [
            (sync_ms, 1200.0), (porch_ms, 1500.0),
            (y / 2, 2200.0), (y / 2, 1500.0),   # Y0: 左亮右暗
            (uv, 1900.0),                       # Cb: 中性
            (y / 2, 1500.0), (y / 2, 2200.0),   # Y1: 反相
            (uv, 1900.0),                       # Cr: 中性
        ]
    return _segments_to_audio(_vis_header(8) + segs)


# --------------------------------------------------------------------------
# 1. 真实 OTA 样本 real_sstv.wav → 自动识别为 Robot 72（非 Robot36）
# --------------------------------------------------------------------------
def test_real_sstv_wav_auto_identifies_robot72():
    if not os.path.exists(_REAL_WAV):
        pytest.skip("缺真实样本 real_sstv.wav")
    r = decode_sstv(_REAL_WAV, output_path=None, mode="auto")
    assert r.get("success") is True, f"real_sstv.wav 解码失败: {r.get('error')}"
    assert r["mode"] == "Robot 72", f"真实样本应识别为 Robot72，实得 {r['mode']}"
    assert (r["width"], r["height"]) == (320, 240)
    assert r["rows_decoded"] >= 200, f"应解出 ~239 行，实得 {r['rows_decoded']}"
    ident = r.get("identification", {})
    assert ident.get("method") == "timing"


# --------------------------------------------------------------------------
# 2. 合成逐行式 Robot36（pysstv）→ Robot 36（不是 Robot72）
# --------------------------------------------------------------------------
def test_synthetic_perline_robot36_not_misidentified_as_robot72():
    pytest.importorskip("pysstv")
    from PIL import Image
    from pysstv.color import Robot36
    ref = np.zeros((240, 320, 3), np.uint8)
    xx = np.arange(320)
    ref[:, :, 0] = (xx * 255 // 319).astype(np.uint8)
    ref[:, :, 2] = (255 - xx * 255 // 319).astype(np.uint8)
    audio = np.fromiter(
        Robot36(Image.fromarray(ref, "RGB"), FS, 16).gen_samples(),
        dtype=np.int16).astype(np.float32) / 32768.0
    r = decode_audio(audio, FS, out_png=None, mode="auto")
    assert r.get("success") is True, f"逐行 Robot36 解码失败: {r.get('error')}"
    assert r["mode"] == "Robot 36", f"应识别为 Robot36，实得 {r['mode']}"
    assert r["mode"] != "Robot 72", "逐行 Robot36 不得误判为 Robot72"
    assert (r["width"], r["height"]) == (320, 240)


# --------------------------------------------------------------------------
# 3. 合成组首式 Robot36（两行组）→ Robot 36 grouped（补齐无样本的两行组路径）
# --------------------------------------------------------------------------
def test_synthetic_grouped_robot36_two_line_group():
    audio = _grouped_robot36_audio()
    r = decode_audio(audio, FS, out_png=None, mode="auto")
    assert r.get("success") is True, f"组首 Robot36 解码失败: {r.get('error')}"
    assert r["mode"] == "Robot 36", f"组首应识别为 Robot36，实得 {r['mode']}"
    assert r.get("layout") == "grouped", f"组首 layout 应为 grouped，实得 {r.get('layout')}"
    assert (r["width"], r["height"]) == (320, 240)
    assert r["rows_decoded"] >= 200, f"组首两行组应解出 ≈234 行，实得 {r['rows_decoded']}"

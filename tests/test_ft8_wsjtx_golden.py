# SPDX-License-Identifier: MIT
"""FT8 外部真值对拍：WSJT-X 官方样本 210703_133430.wav（2026-11-02）。

背景：本仓 FT8 链此前只有自体往返（自家编码→自家解码），圈内自洽、
无外部真值。本测试把解码链钉在 **WSJT-X 2.7.0~rc3 官方样本**上：

- 样本：SourceForge `wsjtx/samples/FT8/210703_133430.wav`
  （SHA-256 9feb99c275770a6618538026da7decc6b09eb6cf63121e5168fa86dcdf00c2f5，
  15 s / 12 kHz / 单声道实录；第三方字节**不入库**，按需下载 + SHA 钉真值，
  离线诚实 skip——与 de421.bsp 同款姿势）。
- 真值：WSJT-X `jt9 -8 -F 200 -f 1500` 在该样本上解出的 14 条消息
  （音频频率 / dt / 文本），镜像自 OrcSDR tools/ft8-reference/ft8_210703_133430.ref。

测量口径（2026-11-02 实测基线）：**参考频点 ±30 Hz 窄带重解**——把每条参考
信号混到基带，走本仓 Costas 同步 + 逐音软能量 + LDPC BP + CRC14 + unpack77
全链，文本与参考逐字比对。实测 **5/14 精确命中、零假接受**：

  HIT  2571 Hz  W1FC F5BZB -08
  HIT  2157 Hz  WM3PEN EA6VQ -09
  HIT   590 Hz  K1JT HA0DU KN07
  HIT   723 Hz  A92EE F5PSR -14
  HIT  1649 Hz  K1JT EA3AGB -15

本测试钉住这 5 条为回归地板（任一失守即红）+ 零假接受（任一频点解出
非参考文本即红）。其余 9 条为弱信号（-10 dB 档及以下）+ 4 条窄窗未同步，
**如实记为未命中、不虚报**——全盲宽带搜索与弱信号覆盖率提升是后续工作
（not_verified：盲搜候选数、与 WSJT-X 14 条的覆盖率）。

为什么可信：WSJT-X 官方样本是全球业余无线电界公认的解码参考基准；
本仓链路与 WSJT-X 无任何代码同源（独立按公开协议实现），能在其官方
实录上逐字复现解码 = 独立实现与事实对齐，这比自体往返强一个证据等级。
"""
from __future__ import annotations

import hashlib
import math
import os
import struct
import sys
import urllib.request

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai.ft8_modem import Ft8CostasSync, N_TONES  # noqa: E402
from mbdsdr_ai.ft8_decode import decode_ft8_payload, data_symbol_positions  # noqa: E402
from mbdsdr_ai.ft8_unpack import unpack77  # noqa: E402

WAV_URL = "https://downloads.sourceforge.net/project/wsjt/samples/FT8/210703_133430.wav"
WAV_SHA256 = "9feb99c275770a6618538026da7decc6b09eb6cf63121e5168fa86dcdf00c2f5"
WAV_NAME = "210703_133430.wav"

# WSJT-X 2.7.0~rc3 `jt9 -8 -F 200 -f 1500` 官方解出的 14 条
# (audio_hz, dt_s, message)。真值来源见模块 docstring。
REF = [
    (2571, 0.3, "W1FC F5BZB -08"),
    (1197, -0.8, "CQ F5RXL IN94"),
    (2157, -0.1, "WM3PEN EA6VQ -09"),
    (590, 0.3, "K1JT HA0DU KN07"),
    (723, 0.1, "A92EE F5PSR -14"),
    (2695, -0.1, "K1BZM EA3GP -09"),
    (641, 0.3, "N1JFU EA6EE R-07"),
    (466, 0.2, "N1PJT HB9CQK -10"),
    (2734, 0.4, "W1DIG SV9CVY -14"),
    (1649, 0.1, "K1JT EA3AGB -15"),
    (400, 0.3, "W0RSJ EA3BMU RR73"),
    (2853, 0.2, "XE2X HA2NP RR73"),
    (472, 0.4, "KD2UGC F6GCP R-23"),
    (2522, 0.2, "K1BZM EA3CJ JN01"),
]

# 回归地板：2026-11-02 实测命中的 5 条（音频频率 → 参考文本）。
GOLDEN_HITS = {
    2571: "W1FC F5BZB -08",
    2157: "WM3PEN EA6VQ -09",
    590: "K1JT HA0DU KN07",
    723: "A92EE F5PSR -14",
    1649: "K1JT EA3AGB -15",
}

FS = 12000
SEARCH_HZ = (-30.0, 30.0)   # 参考频点窄带重解窗（±30 Hz）


def _cache_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".mbdsdr", "ft8_golden", WAV_NAME)


def _ensure_wav() -> str:
    """取样本：本地缓存 → SHA 校验；没有则下载。失败诚实 skip，不造数据。"""
    path = _cache_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        if digest == WAV_SHA256:
            return path
        # 校验不符：不静默沿用，删缓存重下（防缓存污染）。
        os.remove(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        urllib.request.urlretrieve(WAV_URL, path)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"WSJT-X 官方样本下载失败（无网络？）: {e}；"
                    f"可手动下载 {WAV_URL} 放到 {path} 后重跑")
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != WAV_SHA256:
        os.remove(path)
        pytest.skip(f"样本 SHA-256 不符（期望 {WAV_SHA256[:16]}…，得 {digest[:16]}…），"
                    f"不使用未钉真的字节")
    return path


def _read_wav(path: str) -> np.ndarray:
    with open(path, "rb") as f:
        data = f.read()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    pos = 12
    fmt = audio = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        sz = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + sz]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            audio = body
        pos += 8 + sz + (sz & 1)
    assert fmt is not None and audio is not None
    assert fmt[0] == 1 and fmt[1] == 1 and fmt[2] == FS, \
        f"样本必须是 {FS} Hz 单声道 PCM，实际 fmt={fmt}"
    return np.frombuffer(audio, dtype="<i2").astype(np.float64) / 32768.0


def _decode_at(wav: np.ndarray, t: np.ndarray, audio_hz: float) -> str:
    """参考频点窄带重解：混到基带 → 同步 → 软能量 → LDPC → unpack77。

    返回解出文本；未同步或 CRC 不过返回空串（诚实空态，不编造）。
    """
    iq = wav.astype(np.complex128) * np.exp(-2.0j * math.pi * audio_hz * t)
    sync = Ft8CostasSync(freq_search_hz=SEARCH_HZ).process(iq)
    if not sync.synced:
        return ""
    dpos = data_symbol_positions()
    energies = sync.energies[dpos, :]
    r = decode_ft8_payload(energies, full_tones=list(sync.symbols))
    if not r["crc_ok"]:
        return ""
    d = unpack77(list(r["data_bits"]))
    return d.get("text", "") if isinstance(d, dict) else str(d)


@pytest.fixture(scope="module")
def wav_ctx():
    path = _ensure_wav()
    wav = _read_wav(path)
    t = np.arange(wav.size, dtype=np.float64) / FS
    return wav, t


def test_sample_integrity(wav_ctx):
    """样本字节必须与 SHA-256 真值一致（缓存/下载双路径都验）。"""
    wav, _ = wav_ctx
    assert wav.size == FS * 15, f"样本应为 15 s，实际 {wav.size} 样本"


def test_golden_hits_floor(wav_ctx):
    """回归地板：5 条已验证命中的参考消息必须逐字解出（失守即红）。

    这是外部真值地板——本仓 FT8 独立实现在 WSJT-X 官方实录上的既得战绩，
    不许退化。
    """
    wav, t = wav_ctx
    decoded = {}
    for audio_hz, _dt, msg in REF:
        if audio_hz in GOLDEN_HITS:
            decoded[audio_hz] = _decode_at(wav, t, audio_hz)
    for hz, want in GOLDEN_HITS.items():
        got = decoded.get(hz, "")
        assert got == want, f"{hz} Hz 地板失守：期望 {want!r}，实际 {got!r}"


def test_zero_false_accepts(wav_ctx):
    """零假接受：任一参考频点解出的文本（CRC 过）必须等于该频点参考文本。"""
    wav, t = wav_ctx
    for audio_hz, _dt, msg in REF:
        got = _decode_at(wav, t, audio_hz)
        if got:
            assert got == msg, \
                f"{audio_hz} Hz 假接受：解出 {got!r}，参考 {msg!r}"


def test_full_coverage_report_is_honest(wav_ctx):
    """全覆盖报告如实化：命中数只增不减（ratchet），未命中数不虚报为命中。"""
    wav, t = wav_ctx
    hits = []
    for audio_hz, _dt, msg in REF:
        if _decode_at(wav, t, audio_hz) == msg:
            hits.append(audio_hz)
    # 2026-11-02 实测 5/14。只许升不许降；对不上的 9 条在模块 docstring
    # 如实记为 not_verified（弱信号/窄窗未同步），不在此虚报。
    assert len(hits) >= 5, \
        f"命中数从 5 退化到 {len(hits)}：{sorted(hits)}"

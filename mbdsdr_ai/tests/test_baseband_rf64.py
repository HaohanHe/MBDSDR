# SPDX-License-Identifier: MIT
"""RF64 立体声 WAV 测试（SatDump 长录制互操作格式）。

口径：公开 RIFF/RF64 变体（非任何 GPL 源码）。小文件亦写确定尺寸的
RF64；>4GiB 时 data 块长度以 0xFFFFFFFF 哨兵 + ds64.dataSize 兜底
（本测试只覆盖可确定尺寸的路径，哨兵路径由读取端兜底逻辑覆盖）。
"""
import os

import numpy as np
import pytest

from mbdsdr_ai.baseband_io import _RF64_HEADER_BYTES, _read_rf64_wav, _write_rf64_wav


def _iq(n=2000, seed=7, amp=0.3):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * amp


def test_roundtrip_lossless_to_int16(tmp_path):
    p = tmp_path / "t.rf64"
    iq = _iq()
    size, scale = _write_rf64_wav(str(p), iq, 2.4e6)
    assert size == _RF64_HEADER_BYTES + len(iq) * 4
    arr, sr = _read_rf64_wav(str(p))
    assert sr == 2_400_000.0
    back = (arr[0::2] + 1j * arr[1::2]) * scale
    rel = np.max(np.abs(back - iq)) / np.max(np.abs(iq))
    assert rel < 1e-4            # int16 量化误差量级


def test_header_magic_and_layout(tmp_path):
    p = tmp_path / "h.rf64"
    _write_rf64_wav(str(p), _iq(64), 2_000_000)
    head = open(p, "rb").read(_RF64_HEADER_BYTES)
    assert head[:4] == b"RF64"
    assert head[8:12] == b"WAVE"
    assert head[12:16] == b"ds64"
    assert head[44:48] == b"fmt "
    assert head[68:72] == b"data"


def test_scale_allows_full_amplitude(tmp_path):
    """满幅 IQ 归一化后 int16 不裁切（scale 随峰值自适应）。"""
    p = tmp_path / "s.rf64"
    iq = _iq(512) * 5.0
    _, scale = _write_rf64_wav(str(p), iq, 2.4e6)
    arr, _ = _read_rf64_wav(str(p))
    assert int(np.max(np.abs(arr))) >= 32760
    assert 0 < scale <= 1.0 * 5.0      # 峰值归一化后不裁切


def test_read_rejects_non_wav(tmp_path):
    p = tmp_path / "bad.bin"
    p.write_bytes(b"NOTAWAVEFILE" * 8)
    with pytest.raises(ValueError):
        _read_rf64_wav(str(p))


def test_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        _read_rf64_wav(str(tmp_path / "nope.rf64"))

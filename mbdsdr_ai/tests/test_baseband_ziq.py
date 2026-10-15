# SPDX-License-Identifier: MIT
"""ZIQ 读写测试（SatDump 压缩基带格式，官方 docs/pages/ZIQ.md 口径）。

诚实边界：压缩开关需要 zstandard 包；未装时写/读压缩流必须报错
（不拿裸数据冒充压缩流、不把压缩流当裸样本）。测试用 skipif 标注依赖，
默认环境已装则真压缩真回读。
"""
import json
import os

import numpy as np
import pytest

from mbdsdr_ai.baseband_io import ZIQ_MAGIC, read_ziq, write_ziq

zstd = pytest.importorskip("zstandard", reason="ZIQ 压缩需要 zstandard 包")


def _iq(n=2048, seed=3, amp=0.25):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * amp


def test_roundtrip_int16(tmp_path):
    p = tmp_path / "t.ziq"
    iq = _iq()
    info = write_ziq(str(p), iq, 2_400_000, annotation={"note": "n"}, bits=16)
    assert info["compressed"] is True and info["samples"] == len(iq)
    back = read_ziq(str(p))
    arr = np.array(back["iq"])
    assert back["samples"] == len(iq)
    assert back["sample_rate"] == 2_400_000
    assert back["bits"] == 16
    assert back["annotation"]["note"] == "n"
    rel = np.max(np.abs(arr - iq)) / np.max(np.abs(iq))
    assert rel < 1e-2                      # int16 量化误差量级


def test_header_layout(tmp_path):
    p = tmp_path / "h.ziq"
    write_ziq(str(p), _iq(64), 1_000_000, annotation={"a": 1})
    with open(p, "rb") as f:
        head = f.read(22)
    assert head[:4] == ZIQ_MAGIC == b"ZIQ_"
    assert head[4] == 1                    # is_compressed
    assert head[5] == 16                   # bits_per_sample
    assert int.from_bytes(head[6:14], "little") == 1_000_000


def test_uncompressed_roundtrip(tmp_path):
    """is_compressed=0：裸样本路径同样可读写。"""
    p = tmp_path / "u.ziq"
    iq = _iq(512)
    info = write_ziq(str(p), iq, 480_000, bits=16, compress=False)
    assert info["compressed"] is False
    back = read_ziq(str(p))
    assert np.allclose(np.array(back["iq"]), iq, atol=1 / 32767.0 * 2)


def test_bad_magic_rejected(tmp_path):
    p = tmp_path / "bad.ziq"
    p.write_bytes(b"NOPE" + b"\x00" * 32)
    with pytest.raises(ValueError):
        read_ziq(str(p))


def test_bad_bits_rejected(tmp_path):
    p = tmp_path / "bd.ziq"
    write_ziq(str(p), _iq(16), 1_000_000, bits=16, compress=False)
    blob = bytearray(open(p, "rb").read())
    blob[5] = 24                            # 篡改位宽
    open(p, "wb").write(bytes(blob))
    with pytest.raises(ValueError):
        read_ziq(str(p))


def test_compress_requires_dependency(tmp_path, monkeypatch):
    """模拟无 zstandard：压缩写必须报错，不降级为裸数据。"""
    p = tmp_path / "nd.ziq"
    import builtins
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "zstandard":
            raise ImportError("no zstd")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(OSError):
        write_ziq(str(p), _iq(16), 1e6, bits=16, compress=True)

# SPDX-License-Identifier: MIT
"""回放读盘的分块流式（S6）：不许整文件 astype(float64)。

守门点：
1. cf32/cs16 必须用带 ``count`` 的 ``np.fromfile`` 分块读——整文件读会把
   峰值放大到文件大小的数倍（1.1 GB 文件实测 6 倍）；
2. 输出 complex64（8 字节/样本，与 cf32 文件同量纲），不再造 complex128；
3. ``max_samples`` 截断必须如实标 ``truncated`` + 原因，绝不补零/假装读全；
4. ``progress`` 每块回调，``should_cancel`` 可以中途停下并标 ``cancelled``。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import baseband_io  # noqa: E402

N = 262_144


@pytest.fixture
def cf32_file(tmp_path):
    path = str(tmp_path / "rec.cf32")
    idx = np.arange(N, dtype=np.float32)
    buf = np.empty(2 * N, dtype=np.float32)
    buf[0::2] = idx / 1e6
    buf[1::2] = -idx / 1e6
    buf.tofile(path)
    return path


def test_cf32_is_read_in_bounded_chunks(cf32_file, monkeypatch):
    """把 np.fromfile 换成检查器：任何 count=-1 的整文件读都算回归。"""
    real = np.fromfile
    calls = []

    def spy(file, dtype=None, count=-1, **kw):
        calls.append((np.dtype(dtype).itemsize, int(count)))
        assert count != -1, (
            "整文件 np.fromfile：分块流被拆掉，1.1 GB 文件的峰值会回到 6 倍量级")
        return real(file, dtype=dtype, count=count)

    monkeypatch.setattr(baseband_io.np, "fromfile", spy)
    rec = baseband_io.load_iq(cf32_file, chunk_samples=65_536)

    assert len(calls) >= 4, f"262144 样本按 65536 分块应读 4 块，实际 {len(calls)}"
    assert all(itemsize == 4 for itemsize, _ in calls), "读入 dtype 不该是 float64"
    assert max(count for _, count in calls) <= 2 * 65_536, (
        "单次读超过 2×分块（=2×样本数的 float32）：仍会把整文件一口气吃进内存")
    assert rec["iq"].dtype == np.complex64, "输出应是 complex64 而非 complex128"
    assert rec["samples"] == N
    assert rec["file_samples"] == N
    assert rec["truncated"] is False
    np.testing.assert_allclose(
        rec["iq"][:4].real, np.arange(4, dtype=np.float32) / 1e6, rtol=1e-6)
    np.testing.assert_allclose(
        rec["iq"][:4].imag, -np.arange(4, dtype=np.float32) / 1e6, rtol=1e-6)


def test_max_samples_truncates_honestly(cf32_file):
    rec = baseband_io.load_iq(cf32_file, max_samples=100_000,
                              chunk_samples=65_536)
    assert rec["samples"] == 100_000
    assert rec["file_samples"] == N
    assert rec["truncated"] is True
    assert "100000" in rec["truncation_reason"], rec["truncation_reason"]
    # 截断处必须是真实样本，不能补零
    np.testing.assert_allclose(rec["iq"][-1].real, 99_999 / 1e6, rtol=1e-6)


def test_progress_and_cancel(cf32_file):
    seen = []
    rec = baseband_io.load_iq(
        cf32_file, chunk_samples=50_000,
        progress=lambda done, total: seen.append((done, total)))
    assert seen and seen[-1] == (N, N), f"进度未走到终点：{seen[-1:]!r}"
    assert len(seen) >= 5, f"50k 分块应回调多块，实际 {len(seen)} 次"

    rec2 = baseband_io.load_iq(cf32_file, should_cancel=lambda: True)
    assert rec2["samples"] == 0
    assert rec2["cancelled"] is True
    assert rec2["cancel_reason"]


def test_cs16_is_also_streamed_and_normalized(tmp_path, monkeypatch):
    path = str(tmp_path / "rec.cs16")
    idx = np.arange(N, dtype=np.int16)
    buf = np.empty(2 * N, dtype=np.int16)
    buf[0::2] = idx
    buf[1::2] = -idx
    buf.tofile(path)
    with open(path + ".json", "w", encoding="utf-8") as f:
        f.write('{"format": "cs16", "sample_rate": 2400000}')

    real = np.fromfile
    calls = []

    def spy(file, dtype=None, count=-1, **kw):
        calls.append((np.dtype(dtype).itemsize, int(count)))
        assert count != -1, "cs16 也必须分块读"
        return real(file, dtype=dtype, count=count)

    monkeypatch.setattr(baseband_io.np, "fromfile", spy)
    rec = baseband_io.load_iq(path, chunk_samples=65_536)

    assert len(calls) >= 4
    assert all(itemsize == 2 for itemsize, _ in calls)
    assert max(count for _, count in calls) <= 2 * 65_536, "cs16 单次读也必须受分块约束"
    assert rec["iq"].dtype == np.complex64
    np.testing.assert_allclose(
        rec["iq"][:3].real, np.arange(3, dtype=np.float32) / 32768.0, rtol=1e-6)

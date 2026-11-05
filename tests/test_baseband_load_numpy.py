# SPDX-License-Identifier: MIT
"""S6：load_iq / read_ziq 必须返回 numpy 数组，不许逐样本造 Python complex。

反证：修前 `iq.tolist()` 把 12 万样本放大成 12 万个 Python complex 对象
（每个 32B + list 指针），长录音回放（60s@2.048MS/s = 1.2 亿样本）在
UI 线程里直接 OOM。这里直接断言返回类型与长度，不做精度放宽。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

from mbdsdr_ai import baseband_io  # noqa: E402


def _iq(n, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)
            ).astype(np.complex64)


def test_load_iq_returns_numpy_array(tmp_path):
    n = 120_000
    iq = _iq(n)
    path = str(tmp_path / "long.iq")
    baseband_io.save_iq(iq, path, 2.048e6)

    out = baseband_io.load_iq(path)

    assert out["samples"] == n
    assert isinstance(out["iq"], np.ndarray), (
        f"load_iq 又返回 {type(out['iq']).__name__}：长录音会按样本数线性放大")
    assert not isinstance(out["iq"], list)
    assert len(out["iq"]) == n
    assert np.allclose(out["iq"], iq, atol=1e-6)


def test_read_ziq_returns_numpy_array(tmp_path):
    n = 5_000
    # ZIQ 16 bit 要求 [-1,1] 归一化输入，先缩放到幅值 <1 再量化
    iq = _iq(n, seed=1) / 4.0
    path = str(tmp_path / "short.ziq")
    baseband_io.write_ziq(path, iq, 1.0e6, bits=16, compress=False)

    out = baseband_io.read_ziq(path)

    assert out["samples"] == n
    assert isinstance(out["iq"], np.ndarray), (
        f"read_ziq 又返回 {type(out['iq']).__name__}")
    assert len(out["iq"]) == n
    # ZIQ 16 bit 量化误差 1/32767 ≈ 3e-5，容差按量化步长给
    assert np.allclose(out["iq"], iq, atol=1e-4)

# SPDX-License-Identifier: MIT
"""P6 热点优化的等价性守卫。

优化只允许改“怎么算”，不允许改“算出什么”：

  * SpectrumDataGenerator._window 缓存：同 (窗名, 点数) 返回同一数组，
    换窗即失效，数值与 numpy 参考实现一致；
  * SpectrumDataGenerator._resample_max 向量化：可整除路径与逐 bin
    max 循环逐元素相同；不可整除仍走原循环；
  * VFO NCO 旋转子表缓存：与“每块重算 exp”的参考实例逐元素相同，
    改 offset 后缓存失效、结果仍一致。

不依赖硬件 / 声卡 / 真实 IQ。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402

from mbdsdr_ai.dsp import VFO  # noqa: E402
from spectrum_widget import SpectrumDataGenerator  # noqa: E402


def _loop_resample_max(spec: np.ndarray, nb: int) -> np.ndarray:
    edges = np.linspace(0, len(spec), nb + 1).astype(int)
    out = np.empty(nb, dtype=np.float32)
    for i in range(nb):
        seg = spec[edges[i]:edges[i + 1]]
        # 哨兵与可测范围不相交：空段 = 未采到 = NaN，不是 -120 dBFS。
        out[i] = float(seg.max()) if seg.size else np.nan
    return out


def test_window_cache_returns_same_values_and_invalidates():
    gen = SpectrumDataGenerator(num_bins=16)
    gen.fft_size = 2048
    w1 = gen._window(2048)
    w2 = gen._window(2048)
    assert w1 is w2                      # 命中缓存，复用同一数组
    ref = gen._compute_window(2048)
    np.testing.assert_array_equal(w1, ref)
    gen.window_name = "Hann"             # 换窗必须失效
    w3 = gen._window(2048)
    assert w3 is not w1
    np.testing.assert_allclose(w3, np.hanning(2048), atol=1e-12)


def test_resample_max_vectorized_matches_loop():
    gen = SpectrumDataGenerator(num_bins=512)
    rng = np.random.default_rng(7)
    for n in (1024, 2048, 16384):       # 可整除 → 向量化路径
        spec = rng.uniform(-120.0, 0.0, n).astype(np.float32)
        np.testing.assert_array_equal(gen._resample_max(spec),
                                      _loop_resample_max(spec, 512))
    for n in (1000, 1500):              # 不可整除 → 原循环
        spec = rng.uniform(-120.0, 0.0, n).astype(np.float32)
        np.testing.assert_array_equal(gen._resample_max(spec),
                                      _loop_resample_max(spec, 512))


def test_resample_max_empty_segments_use_nan_sentinel():
    """空段必须是 NaN（未采到），不能与真实 -120 dBFS 弱信号重合。"""
    gen = SpectrumDataGenerator(num_bins=512)
    res = gen._resample_max(np.arange(100, dtype=np.float32))
    assert res.shape == (512,)
    assert np.isnan(res).any(), "输入短于 bin 数时必然有空段，应为 NaN"
    finite = res[np.isfinite(res)]
    assert finite.size > 0
    assert not np.any(np.isclose(finite, -120.0)), (
        "-120 是真实可测电平，不能当哨兵")
    # 峰检测路径跳过非有限值：NaN 不得被当成峰
    from spectrum_widget import _detect_peak_bins
    bins = _detect_peak_bins(res, rel_threshold_db=6.0)
    assert all(np.isfinite(res[b]) for b in bins), "NaN 段不得入选峰"


def test_vfo_rotator_cache_matches_recompute_and_invalidates():
    sr, block = 960000.0, 4096
    rng = np.random.default_rng(11)
    cached = VFO(sr, 48000, 12500, 4500.0)
    reference = VFO(sr, 48000, 12500, 4500.0)

    def run_pair(iq):
        out_a = cached.process(iq)
        # 强制参考实例每块重算 exp（等价于优化前实现）
        reference._rot_cache_key = None
        out_b = reference.process(iq)
        np.testing.assert_array_equal(out_a, out_b)

    for _ in range(3):
        run_pair((rng.standard_normal(block)
                  + 1j * rng.standard_normal(block)).astype(np.complex64))

    # 改 offset：d_theta 变化，二者仍必须一致
    cached.set_offset(12000.0)
    reference.set_offset(12000.0)
    for _ in range(2):
        run_pair((rng.standard_normal(block)
                  + 1j * rng.standard_normal(block)).astype(np.complex64))

    # 缓存只保留一份（不随块数增长）
    assert isinstance(cached._rot_cache, tuple)
    assert len(cached._rot_cache) == 2

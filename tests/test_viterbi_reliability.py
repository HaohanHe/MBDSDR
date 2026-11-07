# SPDX-License-Identifier: MIT
"""Viterbi 每比特可靠性输出（MLM = 最大似然度量差）。

用途（LRPT 队列 #9）：擦除辅助 RS 需要「哪些字节可疑」的先验。软符号
幅度只是符号级置信度，而 Viterbi 译码后的每比特置信度必须由译码器给出：
对每一步取「幸存支路度量 − 竞争支路度量」，差值越小说明该比特越可能翻。

契约：
  - 默认调用返回 ndarray（向后兼容，既有 4 个消费方零影响）；
  - ``return_reliability=True`` 返回 (bits, rel)，两者等长；
  - 注入强噪声的比特，其邻域可靠性必须显著低于干净比特（这是擦除能
    定位错误的物理前提，不成立则整条擦除链无意义）；
  - 全零/全同输入不得产生 NaN。
"""
import numpy as np
import pytest

from mbdsdr_ai import lrpt_fec as lf


def _encode(rng, nbits=400):
    info = rng.integers(0, 2, nbits).astype(np.uint8)
    coded = lf.convolve_encode(info)
    return info, coded.astype(np.float64) * 2.0 - 1.0   # 0→-1, 1→+1


def test_default_call_still_returns_bits():
    """向后兼容：不传新参数时返回 ndarray，不是 tuple。"""
    rng = np.random.default_rng(1)
    info, soft = _encode(rng)
    out = lf.viterbi_decode(soft)
    assert isinstance(out, np.ndarray)
    assert out.dtype == np.uint8
    assert out.size == info.size


def test_reliability_matches_bit_length():
    rng = np.random.default_rng(2)
    info, soft = _encode(rng)
    bits, rel = lf.viterbi_decode(soft, return_reliability=True)
    assert bits.size == info.size
    assert rel.size == bits.size
    assert np.all(np.isfinite(rel)), "可靠性不得出现 NaN/Inf"
    assert np.all(rel >= 0.0), "度量差必须非负"


def test_clean_signal_has_high_reliability():
    """无噪信号：可靠性应普遍很高（每步两支路差异大）。"""
    rng = np.random.default_rng(3)
    info, soft = _encode(rng)
    bits, rel = lf.viterbi_decode(soft, return_reliability=True)
    assert np.array_equal(bits, info)
    assert float(np.median(rel)) > 1.0, f"干净信号中位可靠性过低：{np.median(rel)}"


def test_noisy_region_has_lower_reliability():
    """在固定位置把软值压到 ~0（最不确定）→ 该邻域可靠性必须显著下降。

    这是擦除定位的物理前提：译码器自己必须能指出「这里我不确定」。
    """
    rng = np.random.default_rng(4)
    info, soft = _encode(rng, nbits=600)
    bad = 220                       # 软位对索引（= 信息比特 110 附近）
    damaged = soft.copy()
    damaged[bad - 6: bad + 6] = 0.0  # 12 个软位归零 = 完全不确定
    bits, rel = lf.viterbi_decode(damaged, return_reliability=True)
    win = rel[bad // 2 - 8: bad // 2 + 8]
    clean_median = float(np.median(np.delete(rel, np.s_[bad // 2 - 8: bad // 2 + 8])))
    assert float(np.min(win)) < clean_median * 0.5, (
        f"损坏区可靠性未下降：win_min={np.min(win):.3f} "
        f"clean_median={clean_median:.3f}")


def test_all_zero_input_is_finite():
    """退化输入（全零软值）不得产生 NaN——擦除链会把 rel 拿去做排序。"""
    soft = np.zeros(200, dtype=np.float64)
    bits, rel = lf.viterbi_decode(soft, return_reliability=True)
    assert np.all(np.isfinite(rel))
    assert bits.size == rel.size


def test_reliability_does_not_change_decoded_bits():
    """可靠性采集是纯观测：开不开它，译码结果必须逐位相同。"""
    rng = np.random.default_rng(5)
    _, soft = _encode(rng, nbits=300)
    noisy = soft + rng.normal(0, 0.45, soft.size)
    plain = lf.viterbi_decode(noisy)
    with_rel, _ = lf.viterbi_decode(noisy, return_reliability=True)
    assert np.array_equal(plain, with_rel)

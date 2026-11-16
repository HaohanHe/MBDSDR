# SPDX-License-Identifier: MIT
"""gardner_recover 独立 oracle 钉扎（#116）。

真值源：公开 Gardner TED ``e = Re{(cur - prev) * conj(mid)}``，对已知 sps
的 QPSK+RRC 合成信号打分（闭式 RRC taps，独立于实现公式；固定种子，无
墙钟/网络输入，确定性）。

决定性缺陷签名（旧实现）：``(mid - cur) * prev`` 且 ``sps += gain*e``——
mid 含 prev 的脉冲尾，``E[mid*prev] = p(sps/2)*E[prev^2] > 0`` 形成系统性
正偏，所有初值（含完美猜测）都 runaway 到 clamp 天花板 ``sps_guess*1.5``；
且 ``max_iter`` 完全死（所有遍数 identical）。本测试钉扎纠正后的行为，
旧实现必红（外部反证 CONTROL/MUTATION/RESTORE 见
output/validate_gardner_fix.py：fixed mean 0.43 vs buggy 2.92）。
"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.coarse_sync import gardner_recover


def _rrc_taps(beta: float, span: int, sps: float) -> np.ndarray:
    """根升余弦 taps（闭式，独立于实现）。"""
    n = np.arange(-span * sps, span * sps + 1)
    t = n / sps
    taps = np.zeros_like(t, dtype=np.float64)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-12:
            taps[i] = 1.0 - beta + 4.0 * beta / np.pi
        elif beta > 0 and abs(abs(ti) - 1.0 / (4.0 * beta)) < 1e-9:
            taps[i] = (beta / np.sqrt(2.0)) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * beta))
                + (1 - 2 / np.pi) * np.cos(np.pi / (4 * beta)))
        else:
            num = (np.sin(np.pi * ti * (1 - beta))
                   + 4 * beta * ti * np.cos(np.pi * ti * (1 + beta)))
            den = np.pi * ti * (1 - (4 * beta * ti) ** 2)
            taps[i] = num / den
    taps /= np.sqrt(np.sum(taps ** 2))
    return taps


def _make_qpsk(sps_true: float, nsym: int = 600, beta: float = 0.35,
               seed: int = 20261115) -> np.ndarray:
    """合成已知 sps 的 QPSK+RRC（RMS 归一），真值 sps_true 先验已知。"""
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 4, nsym)
    constellation = np.exp(1j * (np.pi / 4 + bits * np.pi / 2))
    up = np.zeros(int(round(nsym * sps_true)), dtype=np.complex128)
    idx_i = np.round(np.arange(nsym) * sps_true).astype(int)
    ok = idx_i < len(up)
    up[idx_i[ok]] = constellation[ok]
    iq = np.convolve(up, _rrc_taps(beta, 8, sps_true), mode="same")
    iq /= (np.sqrt(np.mean(np.abs(iq) ** 2)) + 1e-12)
    return iq


_CASES = [(t, f) for t in (4.0, 6.0, 8.0) for f in (0.95, 1.00, 1.05)]


def test_gardner_known_sps_mean_error():
    """独立 oracle：已知 sps 的 QPSK+RRC，平均 |sps_est - sps_true| 须远
    小于旧实现的 runaway≈2.93。旧式（正偏 TED）此处必红。"""
    errs = [abs(gardner_recover(_make_qpsk(t), sps_guess=t * f,
                                max_iter=4).sps - t)
            for t, f in _CASES]
    mean = float(np.mean(errs))
    assert mean < 1.0, f"mean|err|={mean:.4f}（旧实现 runaway≈2.93）"


def test_gardner_perfect_guess_no_runaway():
    """决定性缺陷签名：完美初值（sps_guess=sps_true）绝不能 runaway 到
    clamp 天花板 sps_guess*1.5。旧实现此处必红（4→6.0, 6→9.0, 8→12.0）。"""
    for t in (4.0, 6.0, 8.0):
        gr = gardner_recover(_make_qpsk(t), sps_guess=t, max_iter=4)
        assert abs(gr.sps - t) < 0.5, f"sps_true={t} est={gr.sps:.4f} 偏离真值"
        assert gr.sps < t * 1.5 - 1e-6, \
            f"sps_true={t} runaway 到天花板 {gr.sps:.4f}"


def test_gardner_max_iter_is_live_and_annealed():
    """max_iter 须真生效（旧实现死参数：所有遍数 identical）且退火单调
    改善。钉扎 it8 平均误差 < it1：死参数则两者相等必红，恒增益发散亦红。"""
    cases = [(t, 0.95) for t in (4.0, 6.0, 8.0)]

    def mean_at(mi: int) -> float:
        return float(np.mean([
            abs(gardner_recover(_make_qpsk(t), sps_guess=t * f,
                                max_iter=mi).sps - t)
            for t, f in cases]))

    m1, m8 = mean_at(1), mean_at(8)
    assert m8 < m1, f"max_iter 退火未改善：it1={m1:.4f} it8={m8:.4f}（死参数则相等）"
    assert m8 < 1.0, f"it8 mean={m8:.4f} 仍 runaway"


def test_gardner_short_signal_honest_empty():
    """诚实空态：信号短于一个符号不伪造锁定，返回 sps_guess 与空符号。"""
    gr = gardner_recover(np.array([1 + 0j] * 3), sps_guess=8.0, max_iter=4)
    assert gr.symbols.size == 0
    assert gr.sps == pytest.approx(8.0)
    assert gr.timing_error == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
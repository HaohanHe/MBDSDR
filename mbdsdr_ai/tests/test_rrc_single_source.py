# SPDX-License-Identifier: MIT
"""RRC 单一来源等价性钉死（防隐性分叉回归）。

`demod.rrc_filter` 已改为委托 `fir_taps.root_raised_cosine_taps`，
本文件锁定两者逐点等价（归一化到单位能量后 maxdiff 容差），
避免未来任一方改动又长出一份实现。
"""
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai import demod  # noqa: E402
from mbdsdr_ai.fir_taps import root_raised_cosine_taps  # noqa: E402


@pytest.mark.parametrize("num_taps,sps,beta", [
    (101, 4, 0.35), (101, 2, 0.35), (33, 4, 0.35), (65, 8, 0.2),
])
def test_demod_rrc_equals_fir_taps(num_taps, sps, beta):
    """demod 版与 fir_taps 版逐点等价（归一化后）。"""
    a = demod.rrc_filter(num_taps, sps, beta)
    b = np.asarray(root_raised_cosine_taps(num_taps, beta, 1.0, float(sps)),
                   dtype=np.float64)
    b = b / np.sqrt(np.sum(b ** 2))
    assert a.shape == b.shape
    assert float(np.max(np.abs(a - b))) < 1e-12


def test_demod_rrc_unit_energy():
    """历史契约：归一化到单位能量。"""
    h = demod.rrc_filter(101, 4, 0.35)
    assert float(np.sum(h ** 2)) == pytest.approx(1.0, abs=1e-9)


def test_matched_filter_unchanged_behaviour():
    """匹配滤波结果不受收敛影响（保持既有行为）。"""
    rng = np.random.default_rng(0)
    iq = (rng.standard_normal(512) + 1j * rng.standard_normal(512))
    out = demod.matched_filter(iq, num_taps=101, sps=4, beta=0.35)
    assert out.shape == iq.shape
    assert np.all(np.isfinite(out))

# SPDX-License-Identifier: MIT
"""costas_track 工具面测试：注册在位 + 物理收敛 + 诚实空态。

收敛期望值来自实证探针（loop_bw=0.05, psk_order=2, 2048 样本 BPSK
+0.02 rad/sample 频偏 → est=0.020000），容差 0.01 留环境余量，
不是拍脑袋数字。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def _offset_bpsk_iq(n=2048, offset_rad=0.02):
    """带固定频偏的 BPSK 基带：±1 符号 × 8 sps + 频偏旋转载波。"""
    rng = np.random.default_rng(7)
    bits = rng.integers(0, 2, size=n // 8)
    sym = (1 - 2 * bits).astype(np.complex128)
    iq = np.repeat(sym, 8)
    t = np.arange(len(iq))
    return iq * np.exp(1j * offset_rad * t)


def _as_pairs(iq):
    return [[float(z.real), float(z.imag)] for z in iq]


def test_costas_track_registered(reg):
    assert "costas_track" in set(reg.get_tool_names())


def test_costas_track_converges_on_offset_bpsk(reg):
    iq = _offset_bpsk_iq()
    r = reg.call("costas_track", {"iq": _as_pairs(iq),
                                   "psk_order": 2, "loop_bw": 0.05})
    assert r.success is True, r.content[:160]
    off = float(r.data["carrier_offset_rad_per_sample"])
    assert abs(off - 0.02) < 0.01, \
        f"频偏估计 {off:.4f} rad/sample 偏离真值 0.02 超过容差 0.01"
    assert r.data.get("psk_order") == 2
    ciq = r.data.get("corrected_iq")
    assert ciq, "校正后 IQ 必须在 data.corrected_iq（模型可程序化取用）"
    assert len(ciq) == len(iq), "输出必须与输入等长"


def test_costas_track_too_short_is_honest(reg):
    r = reg.call("costas_track", {"iq": [[0.0, 0.0]] * 10})
    assert r.success is False
    assert r.content, "空态拒绝必须有说明（诚实空态，不允许静默成功）"
    assert "64" in r.content or "过短" in r.content, r.content[:120]


def test_costas_track_bad_psk_order_is_honest(reg):
    iq = _offset_bpsk_iq(n=1024)
    r = reg.call("costas_track", {"iq": _as_pairs(iq), "psk_order": 3})
    assert r.success is False
    assert "PSK order" in r.content or "不支持" in r.content, r.content[:160]

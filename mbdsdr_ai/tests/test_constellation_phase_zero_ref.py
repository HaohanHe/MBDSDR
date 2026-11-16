# SPDX-License-Identifier: MIT
"""
#113 回归：constellation_metrics 相位误差不得对「零理想点参考」伪造读数。

缺陷（修复前）：相位误差掩码 nz=np.abs(xn)>0.3 只按*接收幅度*过滤，未排除
「被判到零理想点」的符号。ASK 理想点 {0,1}，反相/强噪 ASK 的大符号被判到
0 点，phase_err=angle(xn·conj(0))=angle(±0) 凭浮点符号零伪造出 0°/180°，
把用户可见的「相位误差 σ」抬到 ~90°（实测反相 ASK σ=89.95°），与真实相位
质量无关。真值源＝本模块注释「ASK 的 0 点无相位」。

修复：nz=(np.abs(xn)>0.3)&(np.abs(pts[nearest])>0)，按*参考理想点*是否为零
排除。判决导向 phase_err 有界 ≤90°、不跨 ±180° 回绕，线性 mean/std 本就正确
（非 #107/#109 圆周统计类，圆均值修不了零参考伪影）。

这些测试用确定性合成符号（固定 rng 种子 / 解析角度），不绑墙钟/网络/硬件。
"""
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.modulation import (constellation_metrics, symbol_points,
                                  register_modulation_tools)
from mbdsdr_ai.tool_registry import ToolRegistry


@pytest.fixture()
def reg():
    r = ToolRegistry()
    register_modulation_tools(r)
    return r


def _noise(rng, n, sigma):
    return sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))


def test_mixed_ask_phase_error_reflects_only_nonzero_ref():
    """主真值测试：混合 ASK——有效「on」符号带已知 10° 相偏（判到 1 点），
    反相「on」符号（判到 0 点）必须被排除，不得污染相位误差。

    修复后：只有 10° 那组贡献 → mean≈10°、std≈0。
    修复前：反相组伪造 0°/180° 混入 → mean 被拖到 ~50°、std 暴涨（断言失败）。
    """
    rng = np.random.default_rng(113)
    n = 120
    off_deg = 10.0
    # A 组：有效 on，+1 方向带已知 10° 相偏，无噪 → 判到非零理想点 1
    a = np.exp(1j * np.radians(off_deg)) * np.ones(n)
    # B 组：反相 on，-1 附近带噪 → 判到零理想点 0（修复后排除）
    b = -1.0 + _noise(rng, n, 0.08)
    # C 组：off，0 附近 → 幅度掩码排除
    c = 0.0 + _noise(rng, n, 0.01)
    syms = np.concatenate([a, b, c])
    m = constellation_metrics(syms.tolist(), scheme="ask")
    assert m["phase_error_mean_deg"] == pytest.approx(off_deg, abs=0.5)
    assert m["phase_error_std_deg"] < 1.0


def test_inverted_ask_no_signed_zero_artifact():
    """全反相 ASK：所有大符号判到 0 点。修复后无有效相位样本 → 空态 0°，
    绝不再凭符号零伪造 ~90° 的 σ。反相由误符号率 SER 独立标记（不被藏掉）。
    """
    rng = np.random.default_rng(114)
    n = 2000
    on = -1.0 + _noise(rng, n, 0.1)     # 反相 on
    off = 0.0 + _noise(rng, n, 0.01)    # off
    syms = np.concatenate([on, off])
    m = constellation_metrics(syms.tolist(), scheme="ask")
    # 修复前 σ≈89.95°（符号零伪影）；修复后空态 0°
    assert m["phase_error_std_deg"] < 5.0
    assert abs(m["phase_error_mean_deg"]) < 5.0
    # 反相仍被 SER 如实暴露（on 符号判到 0 点 → 大误差）
    assert m["symbol_error_rate"] > 0.3


def test_normal_ask_phase_error_unaffected():
    """正常 ASK（on 判到非零 1 点）：相位误差仍正确反映小抖动，修复无回归。"""
    rng = np.random.default_rng(115)
    n = 2000
    on = 1.0 + _noise(rng, n, 0.03)
    off = 0.0 + _noise(rng, n, 0.01)
    syms = np.concatenate([on, off])
    m = constellation_metrics(syms.tolist(), scheme="ask")
    assert abs(m["phase_error_mean_deg"]) < 3.0
    assert m["phase_error_std_deg"] < 5.0


@pytest.mark.parametrize("scheme", ["bpsk", "qpsk", "8psk"])
def test_psk_phase_stats_unaffected_by_zero_ref_mask(scheme):
    """PSK 理想点全非零 → 新增的零参考排除条件是恒等，修复对 PSK 零影响。"""
    pts = symbol_points(scheme)
    assert np.all(np.abs(pts) > 0)   # 无零理想点，掩码新增项恒真
    rng = np.random.default_rng(116)
    idx = rng.integers(0, len(pts), 1500)
    syms = pts[idx] + _noise(rng, 1500, 0.02)
    m = constellation_metrics(syms.tolist(), scheme=scheme)
    assert m["phase_error_std_deg"] < 5.0


def test_tool_layer_inverted_ask_honest(reg):
    """注册工具层（用户可见「相位误差 σ=…°」）：反相 ASK 不再报 ~90° 伪影。"""
    rng = np.random.default_rng(117)
    n = 1500
    on = -1.0 + _noise(rng, n, 0.1)
    off = 0.0 + _noise(rng, n, 0.01)
    syms = np.concatenate([on, off])
    r = reg.call("constellation_metrics", {"symbols": syms.tolist(), "scheme": "ask"})
    assert r.success is True
    assert r.data["phase_error_std_deg"] < 5.0
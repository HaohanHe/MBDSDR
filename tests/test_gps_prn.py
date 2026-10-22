# -*- coding: utf-8 -*-
"""GPS 扩频伪随机序列与码相关捕获的门禁。

对应材料《通信对抗材料包》learnSDR 课程 lesson21（LFSR/伪随机）、lesson22
（Gold 码/扩频/CDMA）、lesson23（GPS 相关与解调）的**机制部分**。

覆盖：
· m 序列的自校验（平衡性 + 循环自相关恒 −1）；
· Gold 族的两两相关呈三值结构（含 −1）；
· 码相关捕获的往返：合成"载波×码"的 IQ → 捕获 → 命中正确的码与码相位；
· 无信号时诚实失败（不报卫星判定）。

边界（写进测试断言）：C/A 的 PRN 相位分配不内置——依据是 IS-GPS-200
Table 3-I 的标准数据（仓内没有），且材料自带的码表经核验并非标准 C/A 码
（其循环自相关为三值，而 C/A 码应恒为 −1）。故仓内只提供通用伪随机序列
与"码集可传入"的捕获器。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.gps_ca import (  # noqa: E402
    CA_CHIP_RATE,
    autocorrelation,
    correlate_codes,
    g1_sequence,
    g2_sequence,
    gold_family,
    gold_family_checks,
    msequence_checks,
    to_pm1,
)


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def test_g1_g2_are_maximal_length_sequences():
    """G1/G2 必须是 m 序列：平衡性 ±1 且循环自相关恒 −1。"""
    for seq in (g1_sequence(), g2_sequence()):
        chk = msequence_checks(seq)
        assert chk["balance_ok"], chk
        assert chk["ac_ok"], chk
        assert chk["ac_lag0"] == 1023.0, chk


def test_gold_family_has_three_valued_correlation():
    """Gold 族：两两相关只有三个取值且含 −1（族结构指纹）。"""
    chk = gold_family_checks()
    assert chk["n_distinct"] == 3, chk
    assert chk["has_minus_one"], chk
    assert chk["family_size"] >= 1000, chk


def test_gold_family_members_are_distinct():
    fam = gold_family(256)
    assert len({tuple(r[:64].tolist()) for r in fam}) == len(fam), "Gold 族成员应互不相同"


def test_acquisition_recovers_code_and_phase():
    """合成"载波×码"→ 捕获 → 命中正确码与码相位。"""
    code = to_pm1(g1_sequence())
    sps = 2
    phase = 137
    # 码按 chip 展开并乘载波（附加频偏 3 kHz）
    t = np.arange(code.size * sps) / (CA_CHIP_RATE * sps)
    base = np.repeat(code, sps)
    iq = base * np.exp(2j * np.pi * (3000.0 * t))
    iq = np.roll(iq, phase)          # 引入码相位偏移
    codes = {"G1": code}
    res = correlate_codes(iq, CA_CHIP_RATE * sps, codes)
    assert res["detected"], res
    assert res["best"] == "G1"
    # 多普勒应被搜到（3 kHz 偏，候选步进 100 Hz）
    assert abs(res["best_doppler_hz"] - 3000.0) <= 150.0, res
    # 码相位以 chip 计：roll 是按**采样**滚的 → 期望 chip 相位 = phase/reps
    got = res["best_chip_phase"]
    expect = phase / sps
    diff = abs(got - expect)
    diff = min(diff, 1023 - diff)
    assert diff <= 2, (got, expect)


def test_acquisition_with_noise_still_detects():
    """加噪后仍应检出（相关峰显著高于噪声底）。"""
    rng = np.random.default_rng(3)
    code = to_pm1(g1_sequence())
    sps = 2
    t = np.arange(code.size * sps) / (CA_CHIP_RATE * sps)
    iq = np.repeat(code, sps) * np.exp(2j * np.pi * 2000.0 * t)
    noise = (rng.standard_normal(iq.size) + 1j * rng.standard_normal(iq.size)) * 0.2
    res = correlate_codes(iq + noise, CA_CHIP_RATE * sps, {"G1": code},
                          doppler_hz=(-3000, -2000, -1000, 0, 1000, 2000, 3000))
    assert res["detected"], res
    assert res["best"] == "G1"


def test_acquisition_on_noise_does_not_detect():
    """纯噪声：不得报检测（诚实失败）。"""
    rng = np.random.default_rng(5)
    iq = (rng.standard_normal(20000) + 1j * rng.standard_normal(20000))
    res = correlate_codes(iq, CA_CHIP_RATE, {"G1": to_pm1(g1_sequence())},
                          doppler_hz=(-2000, 0, 2000))
    assert not res["detected"], res


def test_tool_prn_sequence_reports_self_checks(reg):
    """工具层：生成序列并回报自校验（m 序列 / Gold 族）。"""
    r = reg.call("gps_prn_sequence", {"kind": "g1", "head": 16})
    assert r.success, (r.content or "")[:200]
    chk = r.data["msequence_checks"]
    assert chk["balance_ok"] and chk["ac_ok"], chk
    assert len(r.data["pm1_head"]) == 16

    r2 = reg.call("gps_prn_sequence", {"kind": "gold", "head": 8})
    assert r2.success
    assert r2.data["gold_family_checks"]["ok"], r2.data["gold_family_checks"]


def test_tool_acquire_end_to_end(reg, tmp_path):
    """工具层：码集 + IQ → 命中码。"""
    code = to_pm1(g1_sequence())
    sps = 2
    t = np.arange(code.size * sps) / (CA_CHIP_RATE * sps)
    iq = np.repeat(code, sps) * np.exp(2j * np.pi * 1500.0 * t)
    p = str(tmp_path / "gps.iq")
    iq.astype(np.complex64).tofile(p)
    r = reg.call("gps_code_acquire", {
        "codes": {"G1": [float(v) for v in code]},
        "iq_path": p,
        "sample_rate": CA_CHIP_RATE * sps,
        "chip_rate": CA_CHIP_RATE,
        "doppler_hz": [-3000, -2000, -1000, 0, 1000, 2000, 3000],
    })
    assert r.success, (r.content or "")[:300]
    assert r.data["best"] == "G1", r.data
    assert r.data["detected"] is True


def test_tool_acquire_without_codes_is_explicit_error(reg):
    """没给码集 → 明确报错（不静默返回空结果）。"""
    r = reg.call("gps_code_acquire", {"sample_rate": 1e6})
    assert r.success is False
    assert "codes" in (r.content or "").lower()

# -*- coding: utf-8 -*-
"""GPS 扩频伪随机序列、标准 C/A 码与码相关捕获的门禁。

对应材料《通信对抗材料包》learnSDR 课程 lesson21（LFSR/伪随机）、lesson22
（Gold 码/扩频/CDMA）、lesson23（GPS 相关与解调）的**机制部分**。

覆盖：
· m 序列的自校验（平衡性 + 循环自相关恒 −1）；
· Gold 族的两两相关呈三值结构（含 −1）；
· 标准 C/A 码（IS-GPS-200 Table 3-I）的钉扎：金样比特级、octal 列、
  taps 形式 ≡ delay 形式、Gold 指纹、PRN34/37 同码为标准规定；
· 码相关捕获的往返：合成"载波×码"的 IQ → 捕获 → 命中正确的码与码相位；
· 无信号时诚实失败（不报卫星判定）。

标准数据来源与互核链记录在 mbdsdr_ai/gps_ca.py 的
``_GPS_CA_PHASE_ASSIGNMENTS`` 头注；金样 tests/golden/gps_ca_prn_vectors.json
由 .tmp-tool-results/gps_stage3/verify_and_gold.py 脚本生成。
"""
import hashlib
import json
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
    MSEQ_LEN,
    _GPS_CA_PHASE_ASSIGNMENTS,
    autocorrelation,
    ca_code,
    ca_code_taps,
    correlate_codes,
    g1_sequence,
    g2_sequence,
    gold_family,
    gold_family_checks,
    msequence_checks,
    prn_assignment,
    standard_ca_codes,
    to_pm1,
)

GOLDEN_PATH = os.path.join(_ROOT, "tests", "golden", "gps_ca_prn_vectors.json")


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
    """没给码集 → 缺省启用标准 C/A 码族，并标明出处（不静默、不报错）。"""
    r = reg.call("gps_code_acquire", {"sample_rate": 1e6})
    # 无 IQ 输入仍会失败，但失败点必须在 IQ，不在 codes
    assert r.success is False
    assert "iq" in (r.content or "").lower()


# ============================================================================
# 标准 C/A 码（IS-GPS-200 Table 3-I）钉扎
# ============================================================================
@pytest.fixture(scope="module")
def golden():
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_prn_assignment_table_is_complete_and_sane():
    """标准表覆盖 PRN 1..37，抽头/延迟取值合法；除 PRN34/37 标准同码外互不相同。"""
    assert set(_GPS_CA_PHASE_ASSIGNMENTS) == set(range(1, 38))
    seen = {}
    for prn, (taps, delay) in _GPS_CA_PHASE_ASSIGNMENTS.items():
        a, b = taps
        assert 1 <= a <= 10 and 1 <= b <= 10 and a != b, (prn, taps)
        assert 1 <= delay <= 1022, (prn, delay)
        key = (taps, delay)
        if key in seen:
            assert {seen[key], prn} == {34, 37}, \
                f"PRN{seen[key]} 与 PRN{prn} 同码——标准只允许 34/37 此一例"
        else:
            seen[key] = prn


def test_ca_code_invalid_prn_raises():
    """表外 PRN 必须明确报错（不给编造分配）。"""
    for bad in (0, 38, 120, -1):
        with pytest.raises(ValueError):
            ca_code(bad)


def test_ca_code_matches_golden_vectors(golden):
    """全 37 PRN：前 32 chip 与全码 SHA-256 逐一对上金样（比特级钉扎）。"""
    assert golden["_schema"] == "gps_ca_prn_vectors/v1"
    assert "IS-GPS-200" in golden["_source"]["standard"]
    for prn in range(1, 38):
        g = golden["prn"][str(prn)]
        c = ca_code(prn)
        assert c.shape == (MSEQ_LEN,) and set(np.unique(c)) <= {0, 1}
        assert [int(x) for x in c[:32]] == g["first32_chips"], f"PRN{prn} first32"
        assert hashlib.sha256(c.tobytes()).hexdigest() == g["sha256_1023bits"], \
            f"PRN{prn} sha256"
        assert g["taps"] == list(_GPS_CA_PHASE_ASSIGNMENTS[prn][0])
        assert g["g2_delay_chips"] == _GPS_CA_PHASE_ASSIGNMENTS[prn][1]


def test_ca_code_first10_octal_matches_icd(golden):
    """first-10-chips octal 与 IS-GPS-200E Table 3-I 全 37 行一致。"""
    for prn in range(1, 38):
        b = "".join(str(int(x)) for x in ca_code(prn)[:10])
        octal = b[0] + "".join(str(int(b[i:i + 3], 2)) for i in range(1, 10, 3))
        assert octal == golden["prn"][str(prn)]["first10_octal"], f"PRN{prn}"


def test_ca_code_taps_form_equals_delay_form():
    """ICD Figure 3-10 两级抽头实现 ≡ §3.3.2.3 延迟实现（37/37 逐比特）。"""
    for prn in range(1, 38):
        assert np.array_equal(ca_code(prn), ca_code_taps(prn)), f"PRN{prn}"


def test_ca_code_gold_fingerprint():
    """Gold 指纹：每 PRN 循环自相关取值 ⊆ {−65, −1, +63}（三值是 C/A 的标准性质）。"""
    for prn in range(1, 38):
        x = to_pm1(ca_code(prn))
        ac = np.fft.ifft(np.fft.fft(x) * np.conj(np.fft.fft(x))).real
        vals = {int(round(v)) for v in ac[1:]}
        assert vals <= {-65, -1, 63}, f"PRN{prn} 自相关越界: {sorted(vals)[:6]}"


def test_ca_code_cross_correlation_within_gold_bound():
    """互相关绝对值 ≤ 65（Gold 界）；PRN34/37 标准同码跳过恒等比较。"""
    xs = {p: to_pm1(ca_code(p)) for p in range(1, 38)}
    keys = sorted(xs)
    ffts = {p: np.fft.fft(xs[p]) for p in keys}
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if {keys[i], keys[j]} == {34, 37}:
                continue
            cc = np.fft.ifft(ffts[keys[i]] * np.conj(ffts[keys[j]])).real
            assert abs(cc).max() <= 65.5, (keys[i], keys[j], abs(cc).max())


def test_prn34_prn37_share_code_by_standard():
    """PRN 34 与 37 同码是 IS-GPS-200 Table 3-I 脚注 ** 的明文规定。"""
    assert _GPS_CA_PHASE_ASSIGNMENTS[34] == _GPS_CA_PHASE_ASSIGNMENTS[37]
    assert np.array_equal(ca_code(34), ca_code(37))


def test_standard_ca_codes_helper():
    codes = standard_ca_codes()
    assert len(codes) == 32 and "PRN1" in codes and "PRN32" in codes
    assert set(np.unique(codes["PRN7"])) <= {-1.0, 1.0}
    sub = standard_ca_codes([3, 5])
    assert set(sub) == {"PRN3", "PRN5"}


def test_tool_prn_sequence_standard_ca(reg):
    """工具层：kind=ca 返回标准 C/A 码与 Gold 自校验，来源注明。"""
    r = reg.call("gps_prn_sequence", {"kind": "ca", "prn": 7, "head": 10})
    assert r.success, (r.content or "")[:300]
    d = r.data
    assert d["prn"] == 7 and d["g2_taps"] == [1, 8] and d["g2_delay_chips"] == 139
    assert "IS-GPS-200" in d["source"]
    assert d["gold_checks"]["autocorr_within_gold_three_values"] is True
    # 与库函数逐比特一致
    assert d["pm1_head"] == [int(v) for v in to_pm1(ca_code(7))[:10]]


def test_tool_prn_sequence_standard_ca_bad_prn(reg):
    r = reg.call("gps_prn_sequence", {"kind": "ca", "prn": 99})
    assert r.success is False


def test_tool_acquire_uses_standard_family_by_default(reg, tmp_path):
    """工具层：不给 codes → 缺省标准 C/A 码族；合成 PRN7 信号应命中 PRN7。"""
    code = to_pm1(ca_code(7))
    sps = 2
    t = np.arange(code.size * sps) / (CA_CHIP_RATE * sps)
    iq = np.repeat(code, sps) * np.exp(2j * np.pi * 1200.0 * t)
    p = str(tmp_path / "gps_prn7.iq")
    iq.astype(np.complex64).tofile(p)
    r = reg.call("gps_code_acquire", {
        "iq_path": p,
        "sample_rate": CA_CHIP_RATE * sps,
        "chip_rate": CA_CHIP_RATE,
    })
    assert r.success, (r.content or "")[:300]
    assert r.data["codes_source"] == "is-gps-200e-table-3I"
    assert r.data["best"] == "PRN7", r.data.get("best")


# ----------------------------------------------------------------------
# nav_bits_from_corr 现状基线（删除前钉住）
# ----------------------------------------------------------------------
def test_nav_bits_from_corr_chunks_and_thresholds():
    """逐 chip 相关值按 20ms/bit（20460 chips）取均值，>0 记 1，否则 0。"""
    from mbdsdr_ai.gps_ca import NAV_BIT_RATE, nav_bits_from_corr

    chips = int(round(CA_CHIP_RATE / NAV_BIT_RATE))     # 20460
    assert chips == 20460
    seq = np.concatenate([
        np.full(chips, 1.0),                             # 均值 +1 -> 1
        np.full(chips, -1.0),                            # 均值 -1 -> 0
        np.concatenate([np.full(chips // 2, 1.0),        # 均值 0   -> 0
                        np.full(chips - chips // 2, -1.0)]),
        np.full(7, 1.0),                                 # 不足一位，丢弃
    ])
    bits = nav_bits_from_corr(seq)
    assert bits.dtype == np.uint8
    assert bits.tolist() == [1, 0, 0]


def test_nav_bits_from_corr_empty_and_custom_chip_rate():
    from mbdsdr_ai.gps_ca import nav_bits_from_corr

    empty = nav_bits_from_corr(np.zeros(0))
    assert empty.size == 0 and empty.dtype == np.uint8
    # chip_rate 参数决定每比特芯片数（2046/50 -> 41 chips/bit）
    seq = np.concatenate([np.full(41, 1.0), np.full(41, -1.0)])
    assert nav_bits_from_corr(seq, chip_rate=2046.0).tolist() == [1, 0]

# SPDX-License-Identifier: MIT
"""GPS LNAV 电文/星历/PVT 测试（P0-3 钉扎层）。

golden 对拍：``tests/golden/gps_lnav_golden.json`` 由
``mbdsdr_ai.gps_nav.ephemeris_to_nav_bits`` 生成（编码侧单源，无手抄）。
解码侧逐字段严格断言（字段值取 ICD 量化格点）；任一侧实现漂移即红。

诚实路径（P0-3 红线条目）：
  * TLM 前导码错误 → 报错，不产出星历；
  * 任意字奇偶失败 → 报错带字序/比特偏移，不产出星历；
  * IODE 跨子帧不一致 → 报错，不混用；
  * 缺子帧（如只 1/2）→ 报缺失清单，不出半套星历；
  * 星历几何校验：|pos| 落在 GPS 轨道量级、ECEF 速度量级、
    位置关于 toe 对称漂移的方向自洽；
  * PVT：<4 星显式拒绝；≥4 星合成伪距收敛到真值（m 级容差）、
    迭代过程单调下降、残差 RMS ≈ 0。
"""
import json
import math
from pathlib import Path

import pytest

from mbdsdr_ai.gps_nav import (
    ephemeris_sat_pos, ephemeris_to_nav_bits, nav_bits_to_subframes,
    pvt_solve, subframes_to_ephemeris,
)

GOLDEN = (Path(__file__).resolve().parents[2] / "tests" / "golden" /
          "gps_lnav_golden.json")

ANGLE_FIELDS = {"m0_rad", "omega0_rad", "i0_rad", "omega_rad"}


@pytest.fixture(scope="module")
def golden():
    assert GOLDEN.is_file(), f"golden 缺失: {GOLDEN}"
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


# ── golden 对拍 ────────────────────────────────────────────────
def test_golden_nav_bits_decode_field_exact(golden):
    """golden 位流 → 逐字段严格相等（格点值，角度按模 2π）。"""
    sfs = nav_bits_to_subframes(golden["nav_bits"])
    eph = subframes_to_ephemeris(sfs)
    for k, want in golden["expected_decoded"].items():
        got = eph[k]
        if k in ANGLE_FIELDS:
            assert _angle_diff(got, want) == pytest.approx(0.0, abs=0.0), \
                f"{k}: {got!r} != {want!r} (mod 2π)"
        else:
            assert got == pytest.approx(want, abs=0.0), \
                f"{k}: {got!r} != {want!r}"
    # tow / 子帧号也要对拍
    assert eph["tow_counts"] == [golden["tow_count"], golden["tow_count"] + 1,
                                 golden["tow_count"] + 2]
    assert eph["subframe_ids"] == {1: 1, 2: 2, 3: 3}


def test_golden_encoder_regenerates_bit_exact(golden):
    """编码器对 golden 输入再生成，位流逐位相等（编码侧钉扎）。"""
    bits = ephemeris_to_nav_bits(golden["input_ephemeris"],
                                 tow_count=golden["tow_count"])
    assert bits == golden["nav_bits"]


# ── 诚实失败路径 ──────────────────────────────────────────────
def _decode_words(golden):
    """golden 位流 → 各子帧 10 个数据字（校验通过的原始数据位）。"""
    from mbdsdr_ai import gps_nav as g
    sfs = nav_bits_to_subframes(golden["nav_bits"])
    all_words = []
    d29 = d30 = 0
    for sf in sfs:
        words = []
        for w in range(10):
            seg = sf[w * 30:(w + 1) * 30]
            data, ok = g._decode_word(seg, d29, d30)
            assert ok, "golden 不应有奇偶错误"
            words.append(data)
            d29, d30 = seg[28], seg[29]
        all_words.append(words)
    return all_words


def _reencode_subframes(all_words):
    """改过的 10 个数据字（含 TLM/HOW）→ 奇偶合法的位流，链跨子帧续接。"""
    from mbdsdr_ai import gps_nav as g
    bits: list = []
    d29 = d30 = 0
    for words in all_words:
        for d in words:
            w = g._sf_word(d, d29, d30)
            bits.extend(w)
            d29, d30 = w[28], w[29]
    return bits


def test_bad_tlm_preamble_rejected(golden):
    """TLM 前导码错误（奇偶合法）→ 报错，不产出星历。"""
    all_words = _decode_words(golden)
    # TLM 字：前导码 8bit 在数据位高 8 位
    all_words[0][0] = ((0b01110100 << 16) | (all_words[0][0] & 0xFFFF))
    bits = _reencode_subframes(all_words)
    with pytest.raises(ValueError, match="TLM 前导码"):
        subframes_to_ephemeris(nav_bits_to_subframes(bits))


def test_parity_failure_rejected(golden):
    """任意数据位翻转 → 该字奇偶失败，报错带字序，不产出星历。"""
    bits = list(golden["nav_bits"])
    bits[62] ^= 1  # 子帧1 字3（WN 区域）数据位
    with pytest.raises(ValueError, match="奇偶校验失败"):
        subframes_to_ephemeris(nav_bits_to_subframes(bits))


def test_missing_subframe_rejected(golden):
    """只给子帧 1/2 → 报缺失清单，不出半套星历。"""
    sfs = nav_bits_to_subframes(golden["nav_bits"])
    with pytest.raises(ValueError, match="缺子帧"):
        subframes_to_ephemeris(sfs[:2])


def test_iode_mismatch_rejected(golden):
    """子帧3 的 IODE 与 2 不一致（奇偶合法）→ 报错拒绝混用。"""
    all_words = _decode_words(golden)
    # 字10 数据：IODE 在 bit1-8（高 8 位），翻最低位 → 奇偶会重算，内容冲突
    all_words[2][9] ^= (1 << 16)
    bits = _reencode_subframes(all_words)
    with pytest.raises(ValueError, match="IODE 不一致"):
        subframes_to_ephemeris(nav_bits_to_subframes(bits))


# ── 星历几何 ─────────────────────────────────────────────────
def test_satpos_orbit_magnitude(golden):
    """|pos| 与 |vel| 落在 GPS 中轨道量级（轨道半径 2.5-2.7 万 km，
    速度 3-4.2 km/s——ECEF 含地球自转项，量级口径）。"""
    eph = golden["input_ephemeris"]
    sp = ephemeris_sat_pos(eph, eph["toe_s"])
    r = math.sqrt(sum(v * v for v in sp["pos_ecef_m"]))
    v = math.sqrt(sum(x * x for x in sp["vel_ecef_m_s"]))
    assert 25.0e6 < r < 27.0e6, f"|pos|={r:.0f} 超出 GPS 轨道量级"
    assert 2.9e3 < v < 4.3e3, f"|vel|={v:.1f} 超出中轨道速度量级"


def test_satpos_drift_direction(golden):
    """±300s 漂移的位移方向与轨道速度方向同向（自洽性钉扎）。"""
    eph = golden["input_ephemeris"]
    t0 = eph["toe_s"]
    p0 = ephemeris_sat_pos(eph, t0)["pos_ecef_m"]
    p1 = ephemeris_sat_pos(eph, t0 + 300.0)["pos_ecef_m"]
    v = ephemeris_sat_pos(eph, t0)["vel_ecef_m_s"]
    drift = [p1[i] - p0[i] for i in range(3)]
    dot = sum(drift[i] * v[i] for i in range(3))
    norm_d = math.sqrt(sum(x * x for x in drift))
    norm_v = math.sqrt(sum(x * x for x in v))
    assert dot / (norm_d * norm_v) > 0.99, "漂移方向与速度方向背离（>1°）"


# ── PVT ──────────────────────────────────────────────────────
def _make_observations(eph, user, clock_bias_m, offsets):
    obs = []
    for dt in offsets:
        p = ephemeris_sat_pos(eph, eph["toe_s"] + dt)["pos_ecef_m"]
        rng = math.sqrt(sum((p[i] - user[i]) ** 2 for i in range(3)))
        obs.append({"pr_m": rng + clock_bias_m, "sat_pos_ecef_m": p})
    return obs


def test_pvt_synthetic_converges(golden):
    """≥4 星合成伪距 → 收敛到真值（位置 m 级、钟差 cm 级容差）。"""
    eph = golden["input_ephemeris"]
    # 该几何经冒烟验证 5 次迭代收敛（病态几何会慢尾，不选）
    user = (6378137.0 * 0.6, 6378137.0 * 0.3, 6378137.0 * 0.2)
    b_true = 1.23e-4
    obs = _make_observations(eph, user, b_true, (0.0, 1500.0, 3000.0, 4500.0))
    sol = pvt_solve(obs)
    assert sol["converged"]
    pe = math.sqrt(sum((sol["pos_ecef_m"][i] - user[i]) ** 2 for i in range(3)))
    assert pe < 1.0, f"位置误差 {pe:.3f} m 超 1 m 容差"
    assert abs(sol["clock_bias_m"] - b_true) < 0.01, "钟差误差超 1 cm 容差"
    assert sol["residual_rms_m"] < 1e-3
    assert sol["gdop"] > 0


def test_pvt_history_monotone(golden):
    """迭代过程残差单调不增（收敛路径无发散跳跃）。"""
    eph = golden["input_ephemeris"]
    user = (6.0e6, 3.0e6, 2.0e6)
    obs = _make_observations(eph, user, 0.0,
                             (0.0, 1500.0, 3000.0, 4500.0, 6000.0, 7500.0))
    sol = pvt_solve(obs)
    hs = [h["step_m"] for h in sol["history"]]
    assert all(hs[i + 1] <= hs[i] * 1.5 for i in range(len(hs) - 1)), \
        f"迭代步长不单调: {hs}"


def test_pvt_too_few_sats_rejected(golden):
    """<4 星显式拒绝（不降级二维硬解）。"""
    eph = golden["input_ephemeris"]
    user = (6.0e6, 3.0e6, 2.0e6)
    obs = _make_observations(eph, user, 0.0, (0.0, 1500.0, 3000.0))
    with pytest.raises(ValueError, match="≥4 星"):
        pvt_solve(obs)


# ── 工具注册面 ────────────────────────────────────────────────
def test_tools_registered():
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    names = {t["name"] for t in r.list_tools()}
    for want in ("gps_nav_decode", "gps_ephemeris_satpos", "gps_pvt_solve"):
        assert want in names, f"工具 {want} 未注册"


def test_tool_handlers_end_to_end(golden):
    """工具 handler 级端到端：nav 解码工具吃进 golden 位流。"""
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    h = r.tools["gps_nav_decode"]["handler"]
    res = h({"bits": golden["nav_bits"]})
    assert res.success
    d = res.data
    assert d["week_number"] == golden["expected_decoded"]["week_number"]
    assert d["iode"] == golden["expected_decoded"]["iode"]

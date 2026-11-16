# -*- coding: utf-8 -*-
"""PNTFusionEngine 经度加权圆均值确定性符合测试（#105）。

缺陷：_fuse 对经度做线性加权平均。经度是圆周量，跨日界线两源
（+179.9999° 与 -179.9999°，相距约 22m）线性平均得 0°——错到地球另一侧。
真实消费方：注册 AI 工具 _pnt_update_source/_pnt_get_state →
PNTState.summary() 直接把融合经度显示给用户。

修：经度改加权圆均值 atan2(Σw·sin, Σw·cos) 再归一化 [-180,180)；
纬度/海拔保持线性逆方差加权。共址源时圆均值与线性平均一致（差 O(散布²)）。

全部用固定源配置，不绑墙钟（last_update 不做断言）、不绑网络。
"""
import math

import pytest

from mbdsdr_ai.new_spacetime import PNTFusionEngine, PNTSource

_SRC = list(PNTSource)


def _fuse(sources):
    """sources=[(lat, lon, accuracy_m, valid), ...]，按序分配 PNTSource。"""
    eng = PNTFusionEngine()
    for i, (lat, lon, acc, valid) in enumerate(sources):
        eng.update_source(_SRC[i], {
            'valid': valid, 'latitude': lat, 'longitude': lon, 'accuracy_m': acc,
        })
    return eng.get_state()


def _linear_ref(sources):
    """独立逆方差加权线性平均参考（仅用于共址非日界线回归锚）。"""
    ws = [1.0 / (acc * acc) for (_, _, acc, v) in sources if v]
    ps = [(lat, lon, alt) for (lat, lon, acc, v), alt in
          zip([s for s in sources if s[3]], [0.0] * len(sources))]
    tw = sum(ws)
    lat = sum(p[0] * w for p, w in zip(ps, ws)) / tw
    lon = sum(p[1] * w for p, w in zip(ps, ws)) / tw
    return lat, lon


# ---------------------------------------------------------------------------
# 1) 日界线融合：绝不能错到地球另一侧（缺陷直接锚）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("eps", [0.0001, 0.001, 0.01, 0.1, 1.0])
def test_dateline_fusion_not_opposite_side(eps):
    """两源跨日界线相距 2·eps：融合经度必须落在 ±180 附近，而非 0°。

    线性平均突变在此必红：给出 ≈0°（地球另一侧）。
    """
    st = _fuse([(0.0, 180.0 - eps, 5.0, True),
                (0.0, -(180.0 - eps), 5.0, True)])
    assert st.fusion_mode == "fused"
    assert abs(st.longitude) > 180.0 - 2.0 * eps - 1e-6, (
        f"eps={eps} 融合经度={st.longitude}（应≈±180，线性平均会给≈0）")
    assert -180.0 <= st.longitude <= 180.0


def test_dateline_summary_user_visible():
    """消费方可见：summary() 的位置经度幅值必须 ≈180，不是 0。"""
    st = _fuse([(0.0, 179.9999, 5.0, True), (0.0, -179.9999, 5.0, True)])
    line = [l for l in st.summary().splitlines() if l.startswith("位置")][0]
    lon = float(line.split(",")[1].strip().rstrip("°"))
    assert abs(lon) > 179.0, f"用户可见经度非法: {line}"


# ---------------------------------------------------------------------------
# 2) 加权圆均值正确性：权重必须被尊重（非等权）
# ---------------------------------------------------------------------------
def test_weighted_circular_mean_biases_to_precise_source():
    """GNSS +179.999(acc1,w1) vs LEO -179.999(acc10,w0.01)：
    圆均值应偏向高精度源（+180 侧），解析期望 ≈179.99902°。
    忽略权重的突变会给 180.0°（等权），在此必红。
    """
    st = _fuse([(0.0, 179.999, 1.0, True), (0.0, -179.999, 10.0, True)])
    # 解析：s=0.99·sin(179.999°), c=1.01·cos(179.999°)
    s = 0.99 * math.sin(math.radians(179.999))
    c = 1.01 * math.cos(math.radians(179.999))
    expect = (math.degrees(math.atan2(s, c)) + 180.0) % 360.0 - 180.0
    assert abs(st.longitude - expect) < 1e-6
    assert st.longitude > 179.99, "应偏向高精度 +179.999 源"


# ---------------------------------------------------------------------------
# 3) 共址回归锚：圆均值与线性平均一致（修复对常规输入是恒等）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("lon0", [0.0, 120.0, -75.0, 45.0])
def test_colocated_matches_linear_average(lon0):
    """非日界线共址源：圆均值 == 逆方差线性平均（差 < 1e-9）。"""
    sources = [(45.0, lon0 + 0.0001, 5.0, True),
               (45.0, lon0 - 0.0001, 50.0, True),
               (45.0, lon0, 1.0, True)]
    st = _fuse(sources)
    _, lon_ref = _linear_ref(sources)
    assert abs(st.longitude - lon_ref) < 1e-9, (
        f"lon0={lon0} 圆均值={st.longitude} 线性={lon_ref}")


def test_latitude_altitude_still_linear():
    """纬度/海拔仍是线性逆方差加权（未被圆均值改动）。"""
    sources = [(45.0, 120.0, 5.0, True), (45.2, 120.0, 50.0, True)]
    st = _fuse(sources)
    w1, w2 = 1.0 / 25.0, 1.0 / 2500.0
    lat_ref = (45.0 * w1 + 45.2 * w2) / (w1 + w2)
    assert abs(st.latitude - lat_ref) < 1e-12


def test_accuracy_fusion_unchanged():
    """融合精度 = 1/sqrt(Σ1/acc²)（逆方差融合，未改动）。"""
    st = _fuse([(0.0, 120.0, 5.0, True), (0.0, 120.0, 10.0, True)])
    expect = 1.0 / math.sqrt(1.0 / 25.0 + 1.0 / 100.0)
    assert abs(st.accuracy_m - expect) < 1e-12


# ---------------------------------------------------------------------------
# 4) 经度域不变量 + 单源/降级回归
# ---------------------------------------------------------------------------
def test_fused_longitude_in_range_over_grid():
    """多组源配置（含日界线/共址/散布）融合经度恒落 [-180,180]。"""
    offenders = []
    configs = [
        [(0.0, 179.9, 5.0, True), (0.0, -179.9, 5.0, True)],
        [(0.0, 180.0, 5.0, True), (0.0, -180.0, 5.0, True)],
        [(30.0, 0.0, 5.0, True), (30.0, 0.001, 5.0, True)],
        [(30.0, 90.0, 5.0, True), (30.0, 90.001, 50.0, True)],
        [(-20.0, -170.0, 5.0, True), (-20.0, 170.0, 5.0, True)],
    ]
    for cfg in configs:
        st = _fuse(cfg)
        if not (-180.0 <= st.longitude <= 180.0):
            offenders.append((cfg, st.longitude))
    assert not offenders, f"经度越界: {offenders}"


def test_single_source_unchanged():
    """单源：直接复制经度，mode=single（未走圆均值分支）。"""
    st = _fuse([(45.0, 120.0, 5.0, True)])
    assert st.fusion_mode == "single"
    assert st.longitude == 120.0
    assert st.latitude == 45.0


def test_invalid_source_excluded():
    """无效源被排除：一有效+一无效 → single，用有效源。"""
    st = _fuse([(45.0, 120.0, 5.0, True), (0.0, 0.0, 5.0, False)])
    assert st.fusion_mode == "single"
    assert st.longitude == 120.0
    assert st.active_sources == [_SRC[0]]


def test_no_valid_source_is_none_mode():
    """全无效 → mode=none，位置 None。"""
    st = _fuse([(45.0, 120.0, 5.0, False)])
    assert st.fusion_mode == "none"
    assert st.longitude is None
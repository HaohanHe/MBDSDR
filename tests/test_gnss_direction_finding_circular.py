# -*- coding: utf-8 -*-
"""八木 RSSI 测向方位角加权圆均值确定性符合测试（#106）。

缺陷：gnss_monitor.interference_direction_finding 用 np.average(angles, weights)
线性平均方位角。方位角是圆周量（0°/360° 在正北首尾相接），干扰源在正北附近、
八木全罗盘扫描主瓣跨 0/360 回绕时（如 345/350/0/5/10），线性平均偏向 ~119-180°
——把八木指向反方向。实证：真值 0° → 生产估算 119.1°。

真实消费方：注册 AI 工具 gnss_direction_find → estimated_direction_deg 显示给用户
+ sidecar interference_az 喂 gimbal_point 天线指向。

修：方位角改加权圆均值 atan2(Σw·sin, Σw·cos) 归一化 [0,360)；权重口径（仅高于
中位数样本计入）/peak/confidence/symmetric_check 不变。非回绕时圆均值==线性平均。

全部用固定扫描配置，不绑墙钟、不绑网络、不绑硬件。
"""
import math

import pytest

from mbdsdr_ai.gnss_monitor import interference_direction_finding as df


def _circ_dist(a, b):
    """两方位角间最短圆弧距离（度），跨 0/360 安全。"""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _scan(true_az, step=15, width=25.0, peak=30.0, floor=-90.0):
    """真实八木全罗盘扫描：每 step° 一采样，主瓣随圆距高斯衰减。"""
    out = {}
    for a in range(0, 360, step):
        d = abs(a - true_az) % 360.0
        d = min(d, 360.0 - d)
        out[float(a)] = round(floor + peak * math.exp(-(d / width) ** 2), 2)
    return out


def _analytic_circular_mean(scan):
    """独立参考：与生产同口径权重(max(0,rssi-median)) + 加权圆均值（math 实现）。"""
    angles = list(scan.keys())
    rssi = list(scan.values())
    med = sorted(rssi)[len(rssi) // 2] if len(rssi) % 2 else \
        (sorted(rssi)[len(rssi) // 2 - 1] + sorted(rssi)[len(rssi) // 2]) / 2.0
    ws = [max(0.0, v - med) for v in rssi]
    if sum(ws) <= 0:
        # fallback：peak（argmax RSSI）
        return angles[rssi.index(max(rssi))]
    s = sum(w * math.sin(math.radians(a)) for a, w in zip(angles, ws))
    c = sum(w * math.cos(math.radians(a)) for a, w in zip(angles, ws))
    return math.degrees(math.atan2(s, c)) % 360.0


# ---------------------------------------------------------------------------
# 1) 正北回绕：干扰源在 0° 附近，绝不能指向反方向（缺陷直接锚）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("true_az", [0.0, 359.0, 1.0, 5.0, 355.0])
def test_north_wrap_full_compass_scan(true_az):
    """全罗盘扫描主瓣跨正北回绕：估算方位圆误差必须 < 1.5°。

    线性平均突变在此必红：真值 0° 会给 ~119°（圆误差 ~119°）。
    """
    r = df(_scan(true_az))
    est = r["estimated_direction_deg"]
    assert _circ_dist(est, true_az) < 1.5, (
        f"真值{true_az}° 估算{est}° 圆误差{_circ_dist(est, true_az):.2f}°（线性平均会指向反方向）")


# ---------------------------------------------------------------------------
# 2) 加权圆均值正确性：独立解析参考（权重必须被尊重 + sin/cos 顺序正确）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scan", [
    {350.0: -60, 0.0: -65, 10.0: -70, 180.0: -95},   # 非对称主瓣跨正北
    {355.0: -62, 0.0: -64, 5.0: -66, 90.0: -90},     # 跨正北 + 远瓣
    {10.0: -70, 20.0: -65, 30.0: -72, 200.0: -95},   # 非回绕非对称
    {0.0: -64, 120.0: -80, 240.0: -82},              # 三向散布
])
def test_matches_analytic_weighted_circular_mean(scan):
    """生产估算 == 独立加权圆均值参考（圆差 < 0.05°）。

    等权突变（忽略权重）/ sin-cos 交换突变 / 线性平均突变在此必红。
    """
    r = df(scan)
    expect = _analytic_circular_mean(scan)
    assert _circ_dist(r["estimated_direction_deg"], expect) < 0.05, (
        f"估算{r['estimated_direction_deg']}° ≠ 解析圆均值{expect:.4f}°")


# ---------------------------------------------------------------------------
# 3) 非回绕常规源：修复对常规输入是恒等
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("true_az", [90.0, 180.0, 270.0, 45.0, 135.0])
def test_non_wrap_sources_identity(true_az):
    """主瓣不跨正北的常规源：圆误差 < 1.5°（sin/cos 交换突变在此必红）。"""
    r = df(_scan(true_az))
    assert _circ_dist(r["estimated_direction_deg"], true_az) < 1.5


# ---------------------------------------------------------------------------
# 4) 方位角域不变量 + 边界/fallback
# ---------------------------------------------------------------------------
def test_output_domain_0_360():
    """多组扫描配置估算方位恒落 [0,360)。"""
    offenders = []
    for true_az in (0.0, 359.0, 90.0, 180.0, 270.0, 355.0, 1.0):
        est = df(_scan(true_az))["estimated_direction_deg"]
        if not (0.0 <= est < 360.0):
            offenders.append((true_az, est))
    # 直接构造可能产出 360.0 的临界扫描
    est = df({359.9: -60, 0.0: -61, 0.1: -60.5})["estimated_direction_deg"]
    if not (0.0 <= est < 360.0):
        offenders.append(("critical", est))
    assert not offenders, f"方位越界 [0,360): {offenders}"


def test_single_above_median_sample():
    """仅一个样本高于中位数：圆均值退化为该样本方位。"""
    r = df({0.0: -65, 5.0: -71, 350.0: -80})
    assert r["estimated_direction_deg"] == 0.0


def test_all_below_median_fallback_to_peak():
    """权重全零（RSSI 全相等）：fallback 到 peak（argmax）方位。"""
    r = df({10.0: -80, 20.0: -80, 30.0: -80})
    # 全相等 → median==各值 → weights 全 0 → peak_angle=第一个 argmax=10
    assert r["estimated_direction_deg"] == 10.0


def test_empty_input_error():
    """空输入：优雅报错，不抛异常。"""
    r = df({})
    assert "error" in r


def test_other_outputs_preserved():
    """peak_rssi/confidence/symmetric_check 不受圆均值修复影响。"""
    scan = _scan(0.0)
    r = df(scan)
    assert r["peak_rssi_dbm"] == round(max(scan.values()), 1)
    assert r["confidence"] in ("high", "medium")
    # symmetric_check 仍用 (peak+180)%360 口径
    peak = max(scan, key=scan.get)
    assert f"{(peak + 180) % 360:.0f}°" in r["symmetric_check"]


# ---------------------------------------------------------------------------
# 5) 端到端消费方：注册 AI 工具 gnss_direction_find（用户可见 + 云台指向 sidecar）
# ---------------------------------------------------------------------------
def test_user_visible_tool_direction_near_north():
    """经真实注册工具 _gnss_direction_find：正北源散文与 sidecar 方位都 ≈0，非反向。"""
    from mbdsdr_ai import sdr_tools
    scan = _scan(0.0)
    txt, data = sdr_tools._gnss_direction_find({"rssi_by_azimuth": scan})
    assert "估算方向" in txt
    az = data["interference_az"]
    assert _circ_dist(az, 0.0) < 1.5, f"云台指向 sidecar 方位错到反方向: {az}"
    assert 0.0 <= az < 360.0
"""new_spacetime.format_dms 度分秒进位确定性符合测试。

#98/#99/#100 秒/分进位缺陷类的最后一处。原实现：
    seconds = ((abs_deg - degrees) * 60 - minutes) * 60   # 无 round、无进位
    return f"...{seconds:05.2f}\"..."
秒 ∈ [59.995, 60) 时 :05.2f 在**显示层**四舍五入成非法 60.00"。常见十进制
输入即触发（float 表示使 40.05 略小于 40.05 → int 截断到 02' → 秒≈59.9999）：
    40.05  -> 40°02'60.00"N   (应 40°03'00.00"N)
    40.15  -> 40°08'60.00"N   (应 40°09'00.00"N)
    0.016666 -> 0°00'60.00"N  (应 0°01'00.00"N)

修法沿兄弟函数同一口径（astronomy.dec_to_dms / ax25._encode_aprs_coord /
rf_sky_view.format_dms_deg）：先 round 到显示精度 0.01"，再秒→分→度逐级进位。
关键：进位判据必须作用在 **round 后** 的秒上——只查原始秒 >= 60 抓不到
[59.995,60) 这段（原始值 < 60，显示才越界）。本文件用确定性输入钉住该不变量，
不绑墙钟、不绑网络。纯增量测试 + 一处 correctness 修复，合法输入输出不变。
"""
import math
import re

import pytest

from mbdsdr_ai.new_spacetime import format_dms

# 格式：{度}°{分:02d}'{秒:05.2f}"{半球}；秒 05.2f = 2 整数位 + 2 小数位。
_DMS_RE = re.compile(r"^(\d+)°(\d{2})'(\d{2}\.\d{2})\"([NSEW])$")

# 往返精度：显示步长 0.01"，故最大往返误差 = 半格 0.005" = 0.005/3600 度。
# 加 1e-12 度浮点松弛；锚定本质量（显示量化误差）而非派生量。
_RTOL_DEG = 0.005 / 3600.0 + 1e-12


def _parse_dms(s: str) -> float:
    """把合法 DMS 串解析回十进制度（带半球符号）；非法串直接断言失败。"""
    m = _DMS_RE.match(s)
    assert m, f"非法 DMS 串: {s!r}"
    d = int(m.group(1)) + int(m.group(2)) / 60.0 + float(m.group(3)) / 3600.0
    return -d if m.group(4) in ("S", "W") else d


# ── 1. 秒→分进位：原缺陷触发点（常见输入），四半球精确期望 ──────────────
@pytest.mark.parametrize("deg,is_lat,expect", [
    (40.05, True, "40°03'00.00\"N"),
    (40.05, False, "40°03'00.00\"E"),
    (-40.05, True, "40°03'00.00\"S"),
    (-40.05, False, "40°03'00.00\"W"),
    (40.15, True, "40°09'00.00\"N"),
    (0.016666, True, "0°01'00.00\"N"),
    (40.7166665, True, "40°43'00.00\"N"),
])
def test_seconds_carry_into_minutes(deg, is_lat, expect):
    """秒进位到 60.00 时必须向分进 1、秒归 0（原实现产出非法 60.00"）。"""
    assert format_dms(deg, is_lat) == expect


# ── 2. 分→度进位：秒进位使分达 60 时必须再向度进 1 ──────────────────────
@pytest.mark.parametrize("deg,is_lat,expect", [
    (40.999999, True, "41°00'00.00\"N"),
    (89.999999, True, "90°00'00.00\"N"),
    (-40.999999, False, "41°00'00.00\"W"),
])
def test_minutes_carry_into_degrees(deg, is_lat, expect):
    """分进位到 60 时必须向度进 1、分归 0（杜绝 DD°60'00.00"）。"""
    assert format_dms(deg, is_lat) == expect


# ── 3. 合法输入回归锚：修复不得改变正常输出（独立可推导的精确值）──────────
@pytest.mark.parametrize("deg,is_lat,expect", [
    (40.7128, True, "40°42'46.08\"N"),    # 0.7128*60=42.768' -> 42' + 0.768*60=46.08"
    (0.0, True, "0°00'00.00\"N"),
    (-33.8688, True, "33°52'07.68\"S"),   # 0.8688*60=52.128' -> 52' + 0.128*60=7.68"
    (120.0, False, "120°00'00.00\"E"),
    (-120.0, False, "120°00'00.00\"W"),
])
def test_valid_inputs_unchanged(deg, is_lat, expect):
    """正常输入输出逐字符不变（进位修复是纯 correctness，无行为回归）。"""
    assert format_dms(deg, is_lat) == expect


# ── 4. 半球符号 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("deg,is_lat,hemi", [
    (10.0, True, "N"), (-10.0, True, "S"),
    (10.0, False, "E"), (-10.0, False, "W"),
    (0.0, True, "N"), (0.0, False, "E"),   # 零归北/东（>= 0）
])
def test_hemisphere_sign(deg, is_lat, hemi):
    s = format_dms(deg, is_lat)
    m = _DMS_RE.match(s)
    assert m and m.group(4) == hemi, f"{deg},{is_lat} -> {s!r} 期望半球 {hemi}"


# ── 5. 不变量：确定性网格上秒/分永不越界（property guard）──────────────────
def test_no_illegal_sexagesimal_over_grid():
    """固定网格（0.05° 步长，float 表示易触发进位边界）× 经纬 × 正负，
    秒必须 00.00–59.99、分必须 00–59。去进位/去 round 突变在此必红。
    """
    offenders = []
    for i in range(-3600, 3601):          # -180.0 .. 180.0，步长 0.05°
        deg = i * 0.05
        for is_lat in (True, False):
            s = format_dms(deg, is_lat)
            m = _DMS_RE.match(s)
            if not m:
                offenders.append((deg, is_lat, s, "格式非法"))
                continue
            mm, ss = int(m.group(2)), float(m.group(3))
            if mm >= 60 or ss >= 60.0:
                offenders.append((deg, is_lat, s, "分/秒越界"))
    assert not offenders, f"非法 DMS 输出 {len(offenders)} 例，前 5: {offenders[:5]}"


# ── 6. 往返精度：解析回十进制度，误差 <= 半格显示精度 ──────────────────────
def test_roundtrip_within_display_precision():
    """固定网格往返误差 <= 0.005"（显示量化半格），进位不得引入额外偏差。"""
    worst = 0.0
    worst_deg = None
    for i in range(-180000, 180001):      # -180 .. 180，步长 0.001°
        deg = i * 0.001
        back = _parse_dms(format_dms(deg, True))
        err = abs(back - deg)
        if err > worst:
            worst, worst_deg = err, deg
    assert worst <= _RTOL_DEG, (
        f"往返误差 {worst:.3e}° 超半格 {_RTOL_DEG:.3e}°（worst@{worst_deg}）")
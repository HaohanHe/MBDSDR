"""
sat_passes 模块自测：用已知 ISS TLE 预测一次过境，验证 rise/set/中天 与时长合理。

运行：python3 -m pytest mbdsdr_ai/tests/test_sat_passes.py -v
或：  python3 mbdsdr_ai/tests/test_sat_passes.py
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request
import ssl

# 让脚本既可 pytest 也可直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mbdsdr_ai.sat_passes import (  # noqa: E402
    GroundStation,
    SatellitePass,
    predict_passes,
    sample_trail,
    make_timescale,
)

# 已知 ISS (ZARYA, NORAD 25544) TLE 备份（离线兜底；在线则拉最新）。
# 这组 TLE 历元 2024-09-27，即便稍旧，LEO 几何过境仍稳定出现。
ISS_TLE_FALLBACK = [
    "ISS (ZARYA)",
    "1 25544U 98067A   24270.50000000  .00016717  00000-0  10270-3 0  9990",
    "2 25544  51.6400 235.5478 0006703  76.5300  25.5300 15.49000100 12345",
]

# 长春站（用户所在地）：约 43.82°N, 125.32°E
CHANGCHUN = GroundStation(lat_deg=43.82, lon_deg=125.32, alt_m=0.0)


def fetch_iss_tle() -> list:
    """在线拉最新 ISS TLE；失败回退内置备份。"""
    ctx = ssl.create_default_context()
    try:
        url = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=tle"
        req = urllib.request.Request(url, headers={"User-Agent": "MBDSDR-test/1.0"})
        data = urllib.request.urlopen(req, timeout=10, context=ctx).read().decode()
        lines = [l for l in data.splitlines() if l.strip()]
        if len(lines) >= 3 and lines[1].startswith("1 ") and lines[2].startswith("2 "):
            print("[test] 使用在线最新 ISS TLE")
            return lines[:3]
    except Exception as e:  # noqa: BLE001
        print(f"[test] 在线拉取失败({e})，使用内置备份 TLE")
    return ISS_TLE_FALLBACK


def test_empty_tle_returns_empty():
    """无/无效 TLE 必须返回空列表，不造假。"""
    assert predict_passes([], CHANGCHUN, hours=1) == []
    assert predict_passes(["garbage", "not a tle"], CHANGCHUN, hours=1) == []
    print("[ok] 空/无效 TLE → 空列表")


def test_iss_pass_geometry():
    """用 ISS TLE 预测过境，验证时序与时长合理。"""
    ts = make_timescale()
    tle = fetch_iss_tle()
    # 向后扫 36 小时，LEO(90min 周期) 必有多次 >10° 过境
    passes = predict_passes(tle, CHANGCHUN, start_time=ts.now(),
                            hours=36.0, min_alt=10.0)
    assert len(passes) >= 1, "36h 内至少应预测出一次 ISS 过境"
    print(f"\n[test] 共预测出 {len(passes)} 次过境（长春, 36h, min_alt=10°）：")
    for p in passes[:8]:
        print("   " + p.summary())

    # 取仰角最大的那次做严格校验（最可能是“完整”过境）
    p = max(passes, key=lambda x: x.max_alt)
    # 时序约束：rise <= max_alt_time <= set
    assert p.rise_time.utc <= p.max_alt_time.utc <= p.set_time.utc, "rise≤中天≤set 时序错误"
    # 时长：ISS LEO 完整过境典型 5–15 分钟（允许 4–18 分钟容差）
    dur_min = p.duration / 60.0
    print(f"\n[test] 选中最大仰角过境: max_alt={p.max_alt:.1f}°, duration={dur_min:.1f} min")
    assert 4.0 <= dur_min <= 18.0, f"过境时长 {dur_min:.1f}min 超出合理范围"
    assert 5.0 <= dur_min <= 15.0, f"过境时长 {dur_min:.1f}min 不在 5–15min 期望区间"
    # 方位角合理范围
    assert 0.0 <= p.rise_az <= 360.0
    assert 0.0 <= p.set_az <= 360.0
    assert p.max_alt >= 10.0, "最大仰角应 ≥ min_alt"
    print("[ok] ISS 过境 rise/中天/set 时序、时长(5–15min)、方位角均合理")


def test_trail_sampling():
    """trail 采样：围绕某时刻采一组 az/alt。"""
    ts = make_timescale()
    tle = ISS_TLE_FALLBACK
    trail = sample_trail(tle, CHANGCHUN, ts.now(),
                         before_s=300, after_s=300, step_s=30)
    assert len(trail) >= 5, "应采样到一组轨迹点"
    print(f"\n[ok] trail 采样 {len(trail)} 个点，首末 az/alt: "
          f"({trail[0][1]:.0f}°,{trail[0][2]:.0f}°) → "
          f"({trail[-1][1]:.0f}°,{trail[-1][2]:.0f}°)")


if __name__ == "__main__":
    test_empty_tle_returns_empty()
    test_iss_pass_geometry()
    test_trail_sampling()
    print("\n全部测试通过 ✅")

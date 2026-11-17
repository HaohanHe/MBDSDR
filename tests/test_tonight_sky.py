# SPDX-License-Identifier: MIT
"""今夜可看性测试：缺测必须说缺测，不许按晴天假设。

本文件钉三件事
--------------
1. **云量读的是 cloud_cover，不是降水量**：weather 样本的 ``value`` 是
   降水（0.0 很常见），拿错字段会把「无降水」当成「无云」。
2. **缺测不猜**：没有快照 / 没有云量 / 没有 Kp 时，各自给 unknown 与
   原因；云量缺失时 verdict 必须明说「不按晴天假设」。
3. **极光粗判可核对**：北京磁纬约 30°，Kp=9 也够不到极光椭圆；
   漠河磁纬约 53°，Kp=7 处于边缘。这些是外部可核对的量级。

天文/地磁真值（外部可核对）
--------------------------
* 北京（39.9°N, 116.4°E）地磁纬度约 30°；
* 漠河（53.5°N, 122.5°E）地磁纬度约 52–53°；
* NOAA 经验表里 Kp=7 时极光边缘约到地磁纬度 52°。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.astronomy import tonight as tn  # noqa: E402


def _sample(key, value=None, error="", measurements=None,
            observed_at="2026-11-17T12:00:00Z"):
    return {
        "key": key,
        "value": value,
        "measurements": dict(measurements or {}),
        "error": error,
        "observed_at": observed_at,
        "source": "test-source",
        "url": "https://example.invalid/" + key,
    }


def _snap(cloud=10.0, kp=2.0, lat=53.5, lon=122.5,
          cloud_error="", kp_error=""):
    samples = {
        "weather": _sample(
            "weather", 0.0, error=cloud_error,
            measurements=({} if cloud is None
                          else {"cloud_cover_pct": float(cloud)})),
        "kp": _sample("kp", kp, error=kp_error),
    }
    return {
        "schema": 1,
        "taken_at": "2026-11-17T12:00:00Z",
        "station": {"lat": lat, "lon": lon},
        "samples": samples,
    }


class TestGeomagnetic:
    def test_beijing_is_about_30_degrees(self):
        mlat = tn.geomagnetic_latitude(39.9, 116.4)
        assert 25.0 <= mlat <= 35.0, mlat

    def test_mohe_is_about_44_degrees(self):
        """偶极近似下漠河磁纬约 44°（不是地理纬 53.5°）。

        这个差值是真实存在的：磁极在北美方向，东亚的磁纬系统性偏低。
        """
        mlat = tn.geomagnetic_latitude(53.5, 122.5)
        assert 40.0 <= mlat <= 50.0, mlat

    def test_kp_needed_is_monotonic_in_latitude(self):
        """越往南（磁纬越低）需要的 Kp 越大——表不能反。"""
        needs = [tn.kp_needed_for_mlat(m) for m in (70, 60, 55, 50, 40, 30)]
        assert needs == sorted(needs), needs

    def test_kp_needed_clamps_high_end_and_extrapolates_low_end(self):
        assert tn.kp_needed_for_mlat(90.0) == 0.0
        # 磁纬 0° 时任何 Kp 都够不到：必须外推出 >9，而不是截断成 9。
        assert tn.kp_needed_for_mlat(0.0) > 9.0

    def test_noaa_table_anchor(self):
        """Kp=7 的经验边缘是 52.2°——表被改错时这条先红。"""
        assert tn.kp_needed_for_mlat(52.2) == pytest.approx(7.0, abs=1e-6)


class TestSky:
    def test_clear_sky_is_good(self):
        r = tn.assess_tonight(_snap(cloud=5.0))
        assert r["known"] is True
        assert r["sky"]["grade"] == "good"
        assert r["verdict"].startswith("云量 5%")

    def test_partly_cloudy_is_fair(self):
        r = tn.assess_tonight(_snap(cloud=45.0))
        assert r["sky"]["grade"] == "fair"

    def test_overcast_is_poor(self):
        r = tn.assess_tonight(_snap(cloud=90.0))
        assert r["sky"]["grade"] == "poor"
        assert "被云挡住" in r["verdict"]

    def test_missing_cloud_does_not_assume_clear(self):
        """weather 有 value=0.0（无降水）但没有 cloud_cover：不许当晴天。"""
        r = tn.assess_tonight(_snap(cloud=None))
        assert r["known"] is False
        assert r["sky"]["grade"] == "unknown"
        assert "不按晴天假设" in r["verdict"]
        assert "cloud_cover" in r["sky"]["reason"]

    def test_collection_error_is_surfaced(self):
        r = tn.assess_tonight(
            _snap(cloud=None, cloud_error="URLError: 断网"))
        assert "URLError: 断网" in r["sky"]["reason"]

    def test_no_snapshot_is_honest(self):
        r = tn.assess_tonight(None)
        assert r["known"] is False
        assert "未联网，不猜" in r["verdict"]
        assert r["aurora"]["chance"] == "unknown"

    def test_cloud_cover_is_clamped(self):
        r = tn.assess_tonight(_snap(cloud=140.0))
        assert r["sky"]["cloud_cover_pct"] == 100.0


class TestAurora:
    def test_high_latitude_kp2_is_likely(self):
        """特罗姆瑟磁纬约 67°，Kp=2 已在极光椭圆内。"""
        r = tn.assess_tonight(_snap(cloud=0.0, kp=2.0,
                                    lat=69.6, lon=18.9))
        assert r["aurora"]["known"] is True
        assert r["aurora"]["chance"] == "likely"

    def test_mohe_kp7_is_below_model_threshold(self):
        """漠河磁纬约 44°，NOAA 表的 Kp=7 边缘是 52.2°——按模型偏保守。

        现实中高 Kp 时漠河常见低空极光（极光有高度，能越过地平线）；
        本模块只按椭圆边缘判，宁可说「基本看不到」也不夸口。
        """
        r = tn.assess_tonight(_snap(cloud=0.0, kp=7.0,
                                    lat=53.5, lon=122.5))
        assert r["aurora"]["known"] is True
        assert r["aurora"]["kp_needed"] > 9.0
        assert r["aurora"]["chance"] in ("unlikely", "marginal")

    def test_beijing_kp9_is_unlikely(self):
        """北京磁纬约 30°，Kp=9 也够不到极光椭圆（经验表最低 48.1°）。"""
        r = tn.assess_tonight(_snap(cloud=0.0, kp=9.0,
                                    lat=39.9, lon=116.4))
        assert r["aurora"]["chance"] == "unlikely"

    def test_missing_kp_is_unknown_but_sky_still_known(self):
        s = _snap(cloud=10.0, kp=None, kp_error="URLError: 断网")
        r = tn.assess_tonight(s)
        assert r["known"] is True           # 云量已知
        assert r["aurora"]["chance"] == "unknown"
        assert "断网" in r["aurora"]["reason"]

    def test_missing_station_is_unknown(self):
        s = _snap(cloud=10.0)
        s["station"] = {}
        r = tn.assess_tonight(s)
        assert r["aurora"]["chance"] == "unknown"
        assert "没有站址" in r["aurora"]["reason"]

    def test_aurora_note_disclaims_forecast(self):
        r = tn.assess_tonight(_snap(cloud=0.0, kp=8.0))
        assert "不是极光椭圆预报" in r["aurora"]["note"]


class TestFormat:
    def test_format_includes_source_and_notes(self):
        r = tn.assess_tonight(_snap(cloud=12.0, kp=8.0))
        text = tn.format_tonight_section(r)
        assert "今夜可看性" in text
        assert "云量 = 12%" in text
        assert "test-source" in text
        assert "不是整夜预报" in text
        assert "注：" in text

    def test_format_says_missing(self):
        text = tn.format_tonight_section(tn.assess_tonight(None))
        assert "不按晴天假设" in text
        assert "极光：没有环境快照" in text

    def test_pure_function_is_deterministic(self):
        s = _snap(cloud=30.0, kp=6.0)
        assert tn.assess_tonight(s) == tn.assess_tonight(s)


class TestMoon:
    """月光只按调用方给的**单时刻**采样分档，不外推整夜。"""

    def _moon(self, illumination, alt, at="2026-11-17T14:00:00Z"):
        return {"illumination": illumination, "alt_deg": alt,
                "at": at, "source": "local_ephemeris:de421_bsp"}

    def test_strong_moon_marks_dark_sky_impact(self):
        r = tn.assess_tonight(_snap(cloud=0.0), moon=self._moon(0.9, 40.0))
        assert r["moon"]["known"] is True
        assert r["moon"]["interference"] == "strong"
        assert "月光强" in r["moon"]["text"]

    def test_moderate_and_weak_bands(self):
        mid = tn.assess_tonight(_snap(), moon=self._moon(0.5, 25.0))
        weak = tn.assess_tonight(_snap(), moon=self._moon(0.1, 30.0))
        assert mid["moon"]["interference"] == "moderate"
        assert weak["moon"]["interference"] == "weak"

    def test_below_horizon_is_no_direct_moonlight(self):
        r = tn.assess_tonight(_snap(), moon=self._moon(0.9, -10.0))
        assert r["moon"]["interference"] == "none"
        assert "地平线下" in r["moon"]["text"]

    def test_missing_moon_is_unknown_not_clear_sky(self):
        r = tn.assess_tonight(_snap())
        assert r["moon"]["known"] is False
        assert "没有月球采样" in r["moon"]["reason"]
        # 不能因为「没给月光」就说今夜适合看星：云量结论照旧独立
        assert r["known"] is True

    def test_non_finite_moon_sample_rejected(self):
        r = tn.assess_tonight(_snap(), moon={"illumination": float("nan"),
                                             "alt_deg": 20.0})
        assert r["moon"]["known"] is False
        assert "有限数" in r["moon"]["reason"]

    def test_format_prints_sample_time_and_scope(self):
        r = tn.assess_tonight(_snap(cloud=0.0),
                              moon=self._moon(0.9, 40.0,
                                              at="2026-11-17T14:00:00Z"))
        text = tn.format_tonight_section(r)
        assert "月光强" in text
        assert "2026-11-17T14:00:00Z" in text
        assert "local_ephemeris" in text
        assert "不覆盖整夜" in text

    def test_moon_does_not_change_cloud_verdict(self):
        base = tn.assess_tonight(_snap(cloud=12.0))
        with_moon = tn.assess_tonight(_snap(cloud=12.0),
                                      moon=self._moon(0.9, 40.0))
        assert with_moon["sky"] == base["sky"]
        assert with_moon["known"] == base["known"]


class TestShowers:
    """流星雨只按静态年历判「在不在活动期」，不给今晚每小时颗数。"""

    def test_peak_night_lists_perseids(self):
        from datetime import datetime, timezone

        r = tn.assess_tonight(_snap(cloud=0.0),
                              at=datetime(2026, 8, 12, 20, 0,
                                          tzinfo=timezone.utc))
        showers = r["meteor_showers"]
        assert showers["known"] is True
        assert showers["at"] == "2026-08-12"
        codes = [it["code"] for it in showers["items"]]
        assert "PER" in codes
        assert "英仙座流星雨" in showers["text"]
        assert "峰值 ZHR" in showers["text"]

    def test_gap_period_is_empty_list(self):
        from datetime import datetime, timezone

        r = tn.assess_tonight(_snap(), at=datetime(2026, 6, 15,
                                                   tzinfo=timezone.utc))
        showers = r["meteor_showers"]
        assert showers["known"] is True
        assert showers["items"] == []
        assert "都不在活动期" in showers["text"]

    def test_missing_time_is_unknown_not_empty(self):
        r = tn.assess_tonight(_snap())
        assert r["meteor_showers"]["known"] is False
        assert "未判断" in r["meteor_showers"]["reason"] or \
            "不判断" in r["meteor_showers"]["reason"]

    def test_naive_datetime_is_interpreted_as_utc(self):
        from datetime import datetime

        r = tn.assess_tonight(_snap(), at=datetime(2026, 8, 12, 23, 0))
        assert r["meteor_showers"]["at"] == "2026-08-12"
        assert any(it["code"] == "PER"
                   for it in r["meteor_showers"]["items"])

    def test_no_snapshot_still_reports_showers(self):
        from datetime import datetime, timezone

        r = tn.assess_tonight(None, at=datetime(2026, 8, 12,
                                                tzinfo=timezone.utc))
        assert r["known"] is False
        assert r["meteor_showers"]["known"] is True

    def test_schema_bumped_for_new_field(self):
        assert tn.TONIGHT_SCHEMA == 2

    def test_format_names_shower_and_never_per_hour(self):
        import re
        from datetime import datetime, timezone

        r = tn.assess_tonight(_snap(cloud=0.0),
                              at=datetime(2026, 8, 12,
                                          tzinfo=timezone.utc))
        text = tn.format_tonight_section(r)
        assert "英仙座流星雨" in text
        assert "UTC 2026-08-12" in text
        # 只能给峰值 ZHR，不许把包络折算成「每小时 N 颗」的假流量
        assert re.search(r"每小时\s*\d+\s*颗", text) is None

    def test_format_explains_missing_time(self):
        r = tn.assess_tonight(_snap())
        text = tn.format_tonight_section(r)
        assert "- 流星雨：" in text
        assert "未判断" in text or "不判断" in text


class TestSpaceWeatherWiring:
    def test_weather_collection_asks_for_cloud_cover(self):
        """采集 URL 必须带 cloud_cover，并把结果落进 measurements。

        这条防的是「判定层写好了、采集层没取」的静默缺口：那样每次
        都会诚实地说缺测，但永远不会成功。
        """
        from mbdsdr_ai import space_weather as sw

        seen = {"urls": []}

        def fake(url):
            # 采集是多线程并发：记录调用过的全部 URL，不能用「最后一次」
            # 判断（并发下最后一个不一定是 weather，曾在本机联跑时翻转）。
            seen["urls"].append(url)
            if url.startswith(sw.SOURCES["weather"]["url"]):
                return {"current": {
                    "temperature_2m": 3.0, "relative_humidity_2m": 40,
                    "precipitation": 0.0, "surface_pressure": 1010,
                    "weather_code": 1, "cloud_cover": 12,
                    "time": "2026-11-17T12:00",
                }}
            raise RuntimeError("只关心 weather 分支")

        snap = sw.collect_snapshot(lat=53.5, lon=122.5, fetcher=fake)
        assert any("cloud_cover" in u for u in seen["urls"]), seen["urls"]
        w = snap["samples"]["weather"]
        assert w["measurements"]["cloud_cover_pct"] == 12.0
        assert "云量 12%" in w["display"]

    def test_absent_cloud_cover_is_not_written(self):
        """接口没给 cloud_cover 时不许补 0——否则就是「晴天假设」。"""
        from mbdsdr_ai import space_weather as sw

        def fake(url):
            if url.startswith(sw.SOURCES["weather"]["url"]):
                return {"current": {"precipitation": 0.0}}
            raise RuntimeError("只关心 weather 分支")

        snap = sw.collect_snapshot(lat=53.5, lon=122.5, fetcher=fake)
        w = snap["samples"]["weather"]
        assert "cloud_cover_pct" not in w["measurements"]

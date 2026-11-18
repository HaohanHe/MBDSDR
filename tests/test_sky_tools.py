# SPDX-License-Identifier: MIT
"""星空工具面测试（S3）：让模型能回答"我指的这一片有什么"。

修的是什么缺口
--------------
在这之前全仓 `grep skyengine` 在 `agent.py` / `sdr_tools.py` /
`tool_registry.py` 里**零命中**——星表 83479 颗星对 AI 完全不可见。
模型能查卫星（`tle_parse` / `sat_pass_predict`），却答不了"那颗星是什么"。
而"AI 讲解层"正是这个项目区别于自动寻星设备的地方（它们解决"找得到"，
我们要解决"认得它、懂它"）。

本文件验三件事
--------------
1. **真能算对**：给北斗中心的指向，返回的星必须是北斗那几颗，
   星官归属必须是"北斗"，光行时必须落在天文真值附近。
2. **缺什么说什么**：站址是默认 (0,0) 时给警示而不是假装；距离缺失时
   `light_years=None` 而不是填 0；查不到就 `ok=False` 并给可尝试写法。
3. **只读**：这三个工具不能出现在写工具名单里。

天文真值（可外部核对，不依赖本仓实现）
--------------------------------------
* 天狼 Sirius 距 8.60 光年；织女 Vega 距 25.0 光年。
* 北斗七星的方位角跨度约 25.7°（本项目全量实算值），所以 35° 视场能装下、
  2.3° 目镜只能看到一小段。
"""

import datetime
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def agent():
    """模块级共享：MBDSDRAgent 构造会探测 SoapySDR/rtlsdr，逐例新建太慢。"""
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    ag = MBDSDRAgent(AgentConfig())
    ag.observer.latitude_deg = 45.0
    ag.observer.longitude_deg = 120.0
    ag.observer.height_m = 200.0
    return ag


@pytest.fixture(scope="module")
def beidou_center():
    """北斗七星质心（地平坐标）。用向量平均——方位角不能算术平均，
    跨 0° 时会把北天算成南天（我第一次就是这么错的）。"""
    import numpy as np

    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
    from mbdsdr_ai.new_spacetime import utc_to_jd
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    jd = utc_to_jd(datetime.datetime(2026, 11, 16, 13, 0,
                                    tzinfo=datetime.timezone.utc))
    gs = GroundStation(120.0, 45.0, 200.0, "test")
    cat = get_catalog()
    hip_arr = np.asarray(cat.hip)
    idx = {int(h): i for i, h in enumerate(hip_arr) if h > 0}
    seq = [54061, 53910, 58001, 59774, 62956, 65378, 67301]
    vecs = [j2000_to_altaz(
        vec_from_radec(float(cat.ra_deg[idx[h]]), float(cat.dec_deg[idx[h]])),
        gs, jd) for h in seq]
    m = np.mean(vecs, axis=0)
    m /= np.linalg.norm(m)
    az, alt = azalt_from_vec(m)
    return float(az), float(alt), jd


def _call(agent, name, args):
    """调工具并解析 JSON。

    注意：业务性失败（`ok:false`，比如"星表里找不到这颗"）也会让
    `ToolResult.success=False`——那是**工具正常执行并如实回答"没有"**，
    不是执行异常。所以这里始终优先解析 content，解析不出来才当异常。
    """
    r = agent.tool_registry.call(name, args)
    try:
        d = json.loads(r.content)
        if isinstance(d, dict):
            d["_tool_success"] = r.success
            return d
    except (json.JSONDecodeError, TypeError):
        pass
    return {"ok": False, "_raw_error": str(r.content),
            "_tool_success": r.success}


class TestRegistration:
    def test_three_tools_registered(self, agent):
        names = {t["name"] for t in agent.tool_registry.list_tools()}
        for n in ("sky_whats_here", "sky_object_lookup", "sky_asterism_lookup"):
            assert n in names, f"{n} 没注册"

    def test_meteor_tool_registered(self, agent):
        names = {t["name"] for t in agent.tool_registry.list_tools()}
        assert "sky_meteor_showers" in names, "流星雨工具没注册"

    def test_tonight_tool_registered(self, agent):
        names = {t["name"] for t in agent.tool_registry.list_tools()}
        assert "sky_tonight" in names, "今夜可看性工具没注册"

    def test_observing_sites_tool_registered(self, agent):
        names = {t["name"] for t in agent.tool_registry.list_tools()}
        assert "sky_observing_sites" in names, "台址工具没注册"

    def test_tools_are_read_only(self, agent):
        """只读红线：三个工具都不许进 tx_guard 的写门。"""
        from mbdsdr_ai.tool_registry import _auto_write_by_name

        for n in ("sky_whats_here", "sky_object_lookup",
                  "sky_asterism_lookup", "sky_meteor_showers",
                  "sky_tonight", "sky_observing_sites"):
            assert not _auto_write_by_name(n), f"{n} 被误判成写工具"


class TestWhatsHere:
    def test_finds_beidou(self, agent, beidou_center):
        az, alt, jd = beidou_center
        d = _call(agent, "sky_whats_here",
                  {"az_deg": az, "alt_deg": alt, "fov_deg": 35.0,
                   "jd_ut1": jd})
        assert d["ok"] is True
        assert d["star_count"] > 0

        names = {s["name"] for s in d["stars"]}
        # 北斗七星里最亮的四颗必须有西名
        for n in ("Dubhe", "Merak", "Alioth", "Alkaid"):
            assert n in names, f"35° 视场里没找到 {n}：{sorted(names)}"

    def test_beidou_asterism_reported(self, agent, beidou_center):
        az, alt, jd = beidou_center
        d = _call(agent, "sky_whats_here",
                  {"az_deg": az, "alt_deg": alt, "fov_deg": 35.0,
                   "jd_ut1": jd})
        ast = {a["name"]: a for a in d["asterisms"]}
        assert "北斗" in ast, f"星官列表里没有北斗：{list(ast)}"
        bd = ast["北斗"]
        assert bd["region"] == "紫微垣"
        assert bd["members_in_view"] >= 5
        assert bd["members_total"] >= 7

    def test_light_time_present_and_sane(self, agent, beidou_center):
        az, alt, jd = beidou_center
        d = _call(agent, "sky_whats_here",
                  {"az_deg": az, "alt_deg": alt, "fov_deg": 35.0,
                   "jd_ut1": jd})
        dubhe = next(s for s in d["stars"] if s["name"] == "Dubhe")
        # 天枢（Dubhe）距 123 光年。允许较大误差（HYG 视差本身有不确定度）。
        assert dubhe["light_years"] is not None
        assert 95 <= dubhe["light_years"] <= 155, dubhe["light_years"]

    def test_stars_sorted_brightest_first(self, agent, beidou_center):
        az, alt, jd = beidou_center
        d = _call(agent, "sky_whats_here",
                  {"az_deg": az, "alt_deg": alt, "fov_deg": 35.0,
                   "jd_ut1": jd})
        vmags = [s["vmag"] for s in d["stars"]]
        assert vmags == sorted(vmags), "星没按亮度排，模型会先讲一颗暗星"

    def test_narrow_fov_reports_truncation(self, agent, beidou_center):
        """2.3° 目镜装不下北斗——必须明说"只看到其中一段"。"""
        az, alt, jd = beidou_center
        d = _call(agent, "sky_whats_here",
                  {"az_deg": az, "alt_deg": alt, "fov_deg": 2.3,
                   "jd_ut1": jd})
        assert d["ok"] is True
        ast = {a["name"]: a for a in d["asterisms"]}
        if "北斗" in ast:
            assert ast["北斗"]["truncated"] is True

    def test_output_fits_tool_budget(self, agent, beidou_center):
        """输出必须整份塞进 4000 字符，否则会被中间截断成无法解析的残片。

        这是实测踩到的真缺陷：一开始返回 40 颗星 × 全字段，超上限后
        注册表按 head 60% / tail 40% 砍中间，JSON 直接废掉——
        模型拿到的是"看起来像 JSON 的碎片"，比报错更糟。
        """
        az, alt, jd = beidou_center
        r = agent.tool_registry.call(
            "sky_whats_here",
            {"az_deg": az, "alt_deg": alt, "fov_deg": 60.0, "jd_ut1": jd})
        assert not r.truncated, "输出被截断了：JSON 会变成无法解析的碎片"
        assert len(r.content) <= agent.tool_registry.tool_output_max_chars
        json.loads(r.content)  # 截断后这里会抛

    def test_omitted_count_is_reported(self, agent, beidou_center):
        """列出 12 颗但实际有更多时，必须说清还有多少没列。"""
        az, alt, jd = beidou_center
        d = _call(agent, "sky_whats_here",
                  {"az_deg": az, "alt_deg": alt, "fov_deg": 60.0,
                   "jd_ut1": jd})
        assert d["stars_listed"] == len(d["stars"])
        assert d["stars_listed"] + d["stars_omitted"] == d["star_count"]

    def test_invalid_fov_is_rejected(self, agent, beidou_center):
        az, alt, jd = beidou_center
        d = _call(agent, "sky_whats_here",
                  {"az_deg": az, "alt_deg": alt, "fov_deg": 0.0,
                   "jd_ut1": jd})
        assert d["ok"] is False
        assert "视场角" in d.get("_raw_error", "")

    def test_explicit_site_overrides_observer(self, agent, beidou_center):
        az, alt, jd = beidou_center
        d = _call(agent, "sky_whats_here",
                  {"az_deg": az, "alt_deg": alt, "fov_deg": 35.0,
                   "jd_ut1": jd, "lat_deg": -33.9, "lon_deg": 18.4})
        assert d["ok"] is True
        assert abs(d["site"]["lat_deg"] - (-33.9)) < 1e-6
        assert d["site"]["note"] == "由调用参数给出"


class TestDefaultSiteWarning:
    def test_zero_zero_site_warns_but_does_not_refuse(self):
        """默认 (0,0) 可能是"没设站址"，也可能是真在几内亚湾。

        不许拒绝（会把真在那里的观测者判错），也不许沉默（会让模型
        拿赤道坐标讲中国的天）。这是"不撒谎也不装懂"的落点。
        """
        from mbdsdr_ai.agent import MBDSDRAgent
        from mbdsdr_ai.config import AgentConfig

        ag = MBDSDRAgent(AgentConfig())
        # 不设 observer，保持默认 (0,0)
        d = _call(ag, "sky_whats_here",
                  {"az_deg": 0.0, "alt_deg": 45.0, "fov_deg": 30.0,
                   "jd_ut1": 2461361.0})
        assert d["ok"] is True
        assert d["site"]["warning"], "默认站址必须给警示"
        assert "astro_set_observer" in d["site"]["warning"]


class TestObjectLookup:
    def test_vega_distance(self, agent):
        """织女星距 25.0 光年——天文真值，不依赖本仓实现。"""
        d = _call(agent, "sky_object_lookup", {"query": "Vega"})
        assert d["ok"] is True
        assert d["name"] == "Vega"
        assert d["light_years"] is not None
        assert 20 <= d["light_years"] <= 31, d["light_years"]

    def test_sirius_distance(self, agent):
        """天狼星距 8.60 光年。"""
        d = _call(agent, "sky_object_lookup", {"query": "Sirius"})
        assert d["ok"] is True
        assert 6.5 <= (d["light_years"] or 0) <= 11.0, d["light_years"]

    def test_by_hip(self, agent):
        d = _call(agent, "sky_object_lookup", {"query": "HIP 91262"})
        assert d["ok"] is True
        assert d["name"] == "Vega"

    def test_by_bare_number(self, agent):
        d = _call(agent, "sky_object_lookup", {"query": "32349"})
        assert d["ok"] is True
        assert d["name"] == "Sirius"

    def test_missing_star_is_honest(self, agent):
        d = _call(agent, "sky_object_lookup", {"query": "NotARealStarName"})
        assert d["ok"] is False
        assert "找不到" in d["reason"]
        assert d["hint"], "查不到时必须给可尝试的写法"

    def test_empty_query_rejected(self, agent):
        d = _call(agent, "sky_object_lookup", {"query": "  "})
        assert d["ok"] is False


class TestAsterismLookup:
    def test_beidou_has_seven_members_and_span(self, agent):
        d = _call(agent, "sky_asterism_lookup", {"name": "北斗"})
        assert d["ok"] is True
        assert d["region"] == "紫微垣"
        assert d["has_lines"] is True
        # 注意：星官"北斗"不止七星——它还包含增星，本项目数据里共 22 颗成员。
        # 七星是其中最亮的一段，但"北斗"作为一个官指的是那张星图里的整组。
        assert d["star_count"] >= 7, d["star_count"]
        hips = {m["hip"] for m in d["members"]}
        for h in (54061, 53910, 58001, 59774, 62956, 65378, 67301):
            assert h in hips, f"北斗缺 HIP {h}"
        # 全官跨度（含增星）约 31°；七星本身 25.7°。
        assert 28.0 <= d["span_deg"] <= 35.0, d["span_deg"]

    def test_recommended_fov_is_a_sayable_number(self, agent):
        """FR-01：工具直接给'该用多大镜看'，不让用户拿跨度自己换算。"""
        d = _call(agent, "sky_asterism_lookup", {"name": "北斗"})
        fov = d["recommended_fov_deg"]
        assert fov is not None
        # 北斗全官跨度 31.2° ×1.2 留边 ≈ 37° → 向上取整到 40°
        assert fov >= d["span_deg"], (fov, d["span_deg"])
        assert fov == int(math.ceil(d["span_deg"] * 1.2 / 5.0) * 5), fov
        assert 30 <= fov <= 50, fov
        assert "视场" in d["fov_note"] and str(fov) in d["fov_note"]

    def test_recommended_fov_never_below_span(self, agent):
        """推荐视场必须装得下跨度；单测北斗抓不到昴宿的舍入坑。"""
        for name in ("北斗", "昴宿", "天狼", "参宿", "心宿"):
            d = _call(agent, "sky_asterism_lookup", {"name": name})
            if not d.get("ok") or d.get("recommended_fov_deg") is None:
                continue
            fov, span = d["recommended_fov_deg"], d["span_deg"]
            assert fov >= span, f"{name}: fov={fov} < span={span}"
            assert fov == int(math.ceil(span * 1.2 / 5.0) * 5), name

    def test_seven_star_span_matches_known_value(self, agent):
        """只取七星算跨度应约 25.7°——这是本项目全量实算值，可外部核对。"""
        import numpy as np

        from mbdsdr_ai.celestial_geometry import vec_from_radec
        from mbdsdr_ai.skyengine.star_catalog import get_catalog

        cat = get_catalog()
        hip_arr = np.asarray(cat.hip)
        idx = {int(h): i for i, h in enumerate(hip_arr) if h > 0}
        seq = [54061, 53910, 58001, 59774, 62956, 65378, 67301]
        vs = np.vstack([
            vec_from_radec(float(cat.ra_deg[idx[h]]), float(cat.dec_deg[idx[h]]))
            for h in seq])
        sim = np.clip(vs @ vs.T, -1.0, 1.0)
        span = float(np.degrees(np.arccos(sim.min())))
        assert 24.5 <= span <= 27.0, span

    def test_span_exceeds_eyepiece(self, agent):
        """跨度比目镜视场大一个量级——这是"看不出勺子"的物理原因。"""
        d = _call(agent, "sky_asterism_lookup", {"name": "北斗"})
        assert d["span_deg"] > 2.3 * 5

    def test_unknown_asterism_lists_alternatives(self, agent):
        d = _call(agent, "sky_asterism_lookup", {"name": "不存在的官"})
        assert d["ok"] is False
        assert "没有" in d["reason"]
        assert d["hint"]

    def test_single_star_asterism_is_flagged(self, agent):
        """一星成官很常见；这类官画不出线，但必须能被查到且说明。"""
        d = _call(agent, "sky_asterism_lookup", {"name": "天乙"})
        assert d["ok"] is True
        if not d["has_lines"]:
            assert d["note"], "单星星官没说明为什么画不出线"


class TestMeteorShowers:
    """流星雨工具面：活动期/辐射点/诚实措辞。"""

    @staticmethod
    def _jd(y, mo, d, h=12):
        from mbdsdr_ai.new_spacetime import utc_to_jd
        return utc_to_jd(datetime.datetime(
            y, mo, d, h, 0, tzinfo=datetime.timezone.utc))

    def test_perseids_peak_night(self, agent):
        d = _call(agent, "sky_meteor_showers",
                  {"jd_ut1": self._jd(2026, 8, 12, 20)})
        assert d["ok"] is True
        rows = {r["code"]: r for r in d["showers"]}
        assert "PER" in rows, sorted(rows)
        per = rows["PER"]
        assert per["activity"] == 1.0
        assert per["zhr_peak"] == 100
        assert per["peak_utc"] == "08-12"
        assert d["site"]["note"] == "取自 agent 观测站"
        # 措辞红线：必须显式说明「activity 不是每小时流星数」
        assert "不是今夜每小时流星数" in d["note"]

    def test_empty_season_returns_empty_not_invented(self, agent):
        d = _call(agent, "sky_meteor_showers",
                  {"jd_ut1": self._jd(2026, 3, 25)})
        assert d["ok"] is True
        assert d["showers"] == []

    def test_radiant_uses_same_coordinate_chain(self, agent):
        """辐射点方位/高度必须与星图同一套 j2000→altaz 链（不另算一套）。"""
        from mbdsdr_ai.celestial_geometry import (
            GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)

        jd = self._jd(2026, 8, 12, 20)
        d = _call(agent, "sky_meteor_showers", {"jd_ut1": jd})
        per = {r["code"]: r for r in d["showers"]}["PER"]
        az, alt = azalt_from_vec(j2000_to_altaz(
            vec_from_radec(48.0, 58.0), GroundStation(120.0, 45.0, 200.0), jd))
        assert per["radiant_az_deg"] == pytest.approx(round(float(az), 1),
                                                      abs=0.11)
        assert per["radiant_alt_deg"] == pytest.approx(round(float(alt), 1),
                                                       abs=0.11)
        assert per["above_horizon"] == (float(alt) > 0.0)

    def test_site_override_is_used(self, agent):
        d = _call(agent, "sky_meteor_showers", {
            "jd_ut1": self._jd(2026, 8, 12, 20),
            "lat_deg": -33.9, "lon_deg": 151.2, "alt_m": 40.0})
        assert d["site"]["lat_deg"] == -33.9
        assert d["site"]["note"] == "由调用参数给出"

    @pytest.mark.parametrize("bad", [1.0, 0.0, 1e12])
    def test_absurd_jd_is_refused_not_crashing(self, agent, bad):
        """真实缺陷：Windows 上 fromtimestamp(JD=1) 抛 OSError[Errno 22]。"""
        r = agent.tool_registry.call("sky_meteor_showers", {"jd_ut1": bad})
        assert r.success is False
        d = json.loads(r.content)
        assert d["ok"] is False
        assert d["error"] == "jd_out_of_range"
        assert "2000-01-01" in d["reason"]

    def test_nan_and_non_numeric_are_stopped_before_handler(self, agent):
        """NaN / 字符串在工具层参数体检就被拒，连 handler 都不进。"""
        for bad in (float("nan"), "昨晚"):
            r = agent.tool_registry.call("sky_meteor_showers", {"jd_ut1": bad})
            assert r.success is False
            assert "jd_ut1" in r.content

    def test_whats_here_also_guards_jd(self, agent):
        r = agent.tool_registry.call("sky_whats_here", {
            "az_deg": 180.0, "alt_deg": 45.0, "fov_deg": 30.0,
            "jd_ut1": 1.0})
        assert r.success is False
        assert json.loads(r.content)["error"] == "jd_out_of_range"


class TestTonight:
    """`sky_tonight`：云量 + 极光粗判，缺测不猜。"""

    @staticmethod
    def _snap(cloud=10.0, kp=2.0):
        return {
            "schema": 1,
            "taken_at": "2026-11-17T12:00:00Z",
            "station": {"lat": 45.0, "lon": 120.0},
            "samples": {
                "weather": {
                    "value": 0.0, "error": "", "observed_at": "t",
                    "source": "test", "url": "u",
                    "measurements": {"cloud_cover_pct": cloud},
                },
                "kp": {"value": kp, "error": "", "observed_at": "t"},
            },
        }

    def test_reads_fresh_snapshot_without_network(self, agent, monkeypatch):
        from mbdsdr_ai import space_weather as sw

        monkeypatch.setattr(sw, "latest_snapshot",
                            lambda **kw: (self._snap(cloud=8.0), "snap.json"))

        def _no_network(**kw):  # 有快照时绝不允许现采
            raise AssertionError("有新鲜快照却去联网了")

        monkeypatch.setattr(sw, "collect_snapshot", _no_network)
        d = _call(agent, "sky_tonight", {})
        assert d["ok"] is True
        assert d["known"] is True
        assert d["sky"]["grade"] == "good"
        assert "快照" in d["source_note"]

    def test_missing_cloud_is_unknown_not_clear(self, agent, monkeypatch):
        """快照里的 weather 只有降水没有云量：不许推断「晴」。"""
        from mbdsdr_ai import space_weather as sw

        snap = self._snap()
        del snap["samples"]["weather"]["measurements"]["cloud_cover_pct"]
        monkeypatch.setattr(sw, "latest_snapshot", lambda **kw: (snap, "snap.json"))
        d = _call(agent, "sky_tonight", {})
        assert d["known"] is False
        assert "不按晴天假设" in d["verdict"]

    def test_no_snapshot_offline_is_honest(self, agent, monkeypatch):
        from mbdsdr_ai import space_weather as sw

        monkeypatch.setattr(sw, "latest_snapshot", lambda **kw: None)
        d = _call(agent, "sky_tonight", {"allow_live": False})
        assert d["known"] is False
        assert "未联网，不猜" in d["verdict"]
        assert "allow_live=false" in d["source_note"]

    def test_live_path_is_injectable_and_not_persisted(self, agent, monkeypatch):
        from mbdsdr_ai import space_weather as sw

        monkeypatch.setattr(sw, "latest_snapshot", lambda **kw: None)
        monkeypatch.setattr(sw, "collect_snapshot",
                            lambda **kw: self._snap(cloud=90.0, kp=0.0))
        d = _call(agent, "sky_tonight", {})
        assert d["sky"]["grade"] == "poor"
        assert d["source_note"] == "现采（未落盘）"

    def test_site_override_flows_into_aurora(self, agent, monkeypatch):
        from mbdsdr_ai import space_weather as sw

        monkeypatch.setattr(sw, "latest_snapshot",
                            lambda **kw: (self._snap(cloud=0.0, kp=2.0), "s.json"))
        d = _call(agent, "sky_tonight", {
            "lat_deg": 69.6, "lon_deg": 18.9, "allow_live": False})
        assert d["site"]["note"] == "由调用参数给出"
        assert d["aurora"]["chance"] == "likely"

    def test_moon_sample_uses_site_and_local_ephemeris(self, agent, monkeypatch):
        """月光采样必须用本次站址，且来源标明本地历表（不是在线预报）。"""
        import types

        from mbdsdr_ai import solar_system as ss
        from mbdsdr_ai import space_weather as sw

        monkeypatch.setattr(sw, "latest_snapshot",
                            lambda **kw: (self._snap(cloud=0.0), "s.json"))
        seen = {}

        def _fake_moon(when, station):
            seen["lat"] = station.latitude_deg
            seen["lon"] = station.longitude_deg
            return types.SimpleNamespace(illumination=0.9, alt_deg=30.0,
                                         backend="test")

        monkeypatch.setattr(ss, "get_moon_position", _fake_moon)
        d = _call(agent, "sky_tonight", {
            "lat_deg": 45.0, "lon_deg": 120.0, "allow_live": False})
        assert abs(seen["lat"] - 45.0) < 1e-9
        assert abs(seen["lon"] - 120.0) < 1e-9
        assert d["moon"]["known"] is True
        assert d["moon"]["interference"] == "strong"
        assert d["moon"]["source"] == "local_ephemeris:test"

    def test_moon_unavailable_says_reason_not_clear(self, agent, monkeypatch):
        from mbdsdr_ai import solar_system as ss
        from mbdsdr_ai import space_weather as sw

        monkeypatch.setattr(sw, "latest_snapshot",
                            lambda **kw: (self._snap(cloud=0.0), "s.json"))
        monkeypatch.setattr(ss, "get_moon_position",
                            lambda when, station: None)
        d = _call(agent, "sky_tonight", {})
        assert d["moon"]["known"] is False
        assert "没有月球采样" in d["moon"]["reason"]
        assert d["known"] is True, "月光缺失不能把云量结论一起判死"

    def test_tool_passes_observation_time_to_showers(self, agent, monkeypatch):
        """工具层必须把当前 UTC 时刻喂给流星雨判定，否则永远「未判断」。"""
        from datetime import datetime, timezone

        from mbdsdr_ai import space_weather as sw

        monkeypatch.setattr(sw, "latest_snapshot",
                            lambda **kw: (self._snap(cloud=0.0), "s.json"))
        d = _call(agent, "sky_tonight", {"allow_live": False})
        assert "meteor_showers" in d, "返回里缺流星雨字段"
        assert d["meteor_showers"]["known"] is True
        assert d["meteor_showers"]["at"] == \
            datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TestObservingSites:
    def test_beijing_nearest_is_shahe_public_base(self, agent):
        d = _call(agent, "sky_observing_sites",
                  {"lat_deg": 39.9042, "lon_deg": 116.4074, "limit": 6})
        assert d["ok"] is True
        assert d["sites"][0]["site_id"] == "shahe-science-base"
        assert d["sites"][0]["access"] == "public"
        assert any(s["site_id"] == "xinglong" for s in d["sites"])

    def test_caveat_separates_research_and_public(self, agent):
        d = _call(agent, "sky_observing_sites",
                  {"lat_deg": 39.9, "lon_deg": 116.4, "limit": 6})
        assert "不对外开放" in d["caveat"]
        assert "public 不代表随时能进" in d["caveat"]
        kinds = {s["access"] for s in d["sites"]}
        assert kinds == {"research", "public"}

    def test_site_override_recorded(self, agent):
        d = _call(agent, "sky_observing_sites",
                  {"lat_deg": 26.7, "lon_deg": 100.0, "limit": 1})
        assert d["site"]["note"] == "由调用参数给出"
        assert d["sites"][0]["site_id"] == "lijiang-gaomeigu"

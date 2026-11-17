# SPDX-License-Identifier: MIT
"""观星台址目录测试：坐标必须有官方来源，科研台址不许当旅游推荐。

背景：消费级 App 的「观星点」常不写来源，也不区分科研台址与公众营地。
本文件钉两件事：

1. 内置点的坐标与官方页面一致（量级核对，不声称米级精度），
   科研（research）与公众（public）分开；
2. 用户导入的台址缺 `source_url` 一律拒绝——没有来源的坐标不收。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.astronomy import observing_sites as obs  # noqa: E402


def _site(**over):
    item = {
        "site_id": "my-spot",
        "name_zh": "我的观测点",
        "lat_deg": 30.0,
        "lon_deg": 110.0,
        "alt_m": 1200.0,
        "access": "public",
        "source_url": "https://example.org/site",
    }
    item.update(over)
    return item


class TestBuiltinCatalog:
    def test_catalog_ids_are_unique(self):
        """不把数量写死：目录会随官方页核对进度增长。"""
        ids = [s.site_id for s in obs.SITES]
        assert len(ids) >= 7
        assert len(set(ids)) == len(ids)

    def test_all_coordinates_are_in_range(self):
        for s in obs.SITES:
            assert -90.0 <= s.lat_deg <= 90.0, s.site_id
            assert -180.0 <= s.lon_deg <= 180.0, s.site_id
            # 官方页没给海拔时必须是 None，不许拿 0 顶替
            assert s.alt_m is None or 0.0 <= s.alt_m <= 9000.0, s.site_id

    def test_every_site_has_official_source(self):
        for s in obs.SITES:
            assert s.source_url.startswith(("http://", "https://")), s.site_id
            assert s.access in ("research", "public"), s.site_id

    def test_both_access_kinds_are_present(self):
        kinds = {s.access for s in obs.SITES}
        assert kinds == {"research", "public"}

    def test_public_sites_record_verification_date(self):
        for s in obs.SITES:
            if s.access == "public":
                assert s.verified_on == "2026-11-17", s.site_id

    def test_shahe_matches_official_dms(self):
        """官方页：东经 116°20′、北纬 40°06′、海拔 40 m，团体预约开放。"""
        s = next(x for x in obs.SITES if x.site_id == "shahe-science-base")
        assert abs(s.lat_deg - 40.100000) < 1e-6
        assert abs(s.lon_deg - 116.333333) < 1e-6
        assert s.alt_m == 40.0
        assert s.access == "public"
        assert "预约" in s.note

    def test_eboliang_matches_government_dms(self):
        """茫崖市政府：东经 92°15′25″、北纬 38°21′17″；海拔未给。"""
        s = next(x for x in obs.SITES if x.site_id == "eboliang-yardang")
        assert abs(s.lat_deg - 38.354722) < 1e-6
        assert abs(s.lon_deg - 92.256944) < 1e-6
        assert s.alt_m is None
        assert s.access == "public"

    def test_xinglong_matches_official_dms(self):
        """官方 40°23′39″N, 117°34′30″E。"""
        s = next(x for x in obs.SITES if x.site_id == "xinglong")
        assert abs(s.lat_deg - 40.39417) < 1e-4
        assert abs(s.lon_deg - 117.57500) < 1e-4
        assert s.alt_m == 960.0

    def test_lijiang_matches_ynao_page(self):
        """云南天文台：100°01′51″E, 26°42′32″N, 3193 m。"""
        s = next(x for x in obs.SITES if x.site_id == "lijiang-gaomeigu")
        assert abs(s.lat_deg - 26.70889) < 1e-4
        assert abs(s.lon_deg - 100.03083) < 1e-4
        assert s.alt_m == 3193.0

    def test_ali_matches_government_page(self):
        """阿里政府：32°19′18″N, 80°01′39″E。"""
        s = next(x for x in obs.SITES if x.site_id == "ali")
        assert abs(s.lat_deg - 32.32167) < 1e-4
        assert abs(s.lon_deg - 80.02750) < 1e-4

    def test_lenghu_matches_must_page(self):
        """清华 MUST：93°54′00″E, 38°36′50″N。"""
        s = next(x for x in obs.SITES if x.site_id == "lenghu-saishiteng")
        assert abs(s.lat_deg - 38.61389) < 1e-4
        assert abs(s.lon_deg - 93.90000) < 1e-4

    def test_xuyi_matches_pmo_page(self):
        """紫金山天文台：东经 118°28′、北纬 32°44′、海拔 180 m（分精度）。"""
        s = next(x for x in obs.SITES if x.site_id == "xuyi")
        assert abs(s.lat_deg - (32 + 44 / 60)) < 1e-5
        assert abs(s.lon_deg - (118 + 28 / 60)) < 1e-5
        assert s.alt_m == 180.0
        assert s.access == "research"           # 科研台址，不当旅游推荐
        assert "分精度" in s.note               # 1′ 的误差范围必须写出来
        assert "不对外开放" in s.note

    def test_muztagh_ata_matches_xao_page(self):
        """新疆天文台：38°19′47″N、74°53′48″E、海拔「约 4500 m」。"""
        s = next(x for x in obs.SITES if x.site_id == "muztagh-ata")
        assert abs(s.lat_deg - (38 + 19 / 60 + 47 / 3600)) < 1e-5
        assert abs(s.lon_deg - (74 + 53 / 60 + 48 / 3600)) < 1e-5
        assert s.alt_m == 4500.0
        assert s.access == "research"
        assert "约" in s.note                    # 官方原值带约数，不能装作实测

    def test_mohe_uses_center_of_official_range(self):
        """漠河市文旅局：东经 122°21′05″–122°21′30″、北纬 53°27′00″–53°33′30″。

        官方给的是矩形范围（不是点），本表取范围中心；这里同时钉住换算与
        「范围写进 note」两件事，防止以后把中心点说成景区门址。
        """
        s = next(x for x in obs.SITES if x.site_id == "mohe-beijicun")
        lat_lo = 53 + (27 + 0 / 60) / 60
        lat_hi = 53 + (33 + 30 / 60) / 60
        lon_lo = 122 + (21 + 5 / 60) / 60
        lon_hi = 122 + (21 + 30 / 60) / 60
        assert abs(s.lat_deg - (lat_lo + lat_hi) / 2) < 1e-5
        assert abs(s.lon_deg - (lon_lo + lon_hi) / 2) < 1e-5
        assert s.alt_m is None
        assert s.access == "public"
        assert "范围中心" in s.note and "122°21′05″" in s.note
        assert "极昼" in s.note          # 夏季无法观星必须写清


class TestDistance:
    def test_same_point_is_zero(self):
        assert obs.haversine_km(40.0, 116.0, 40.0, 116.0) == 0.0

    def test_beijing_to_xinglong_is_about_120km(self):
        d = obs.haversine_km(39.9042, 116.4074, 40.39417, 117.57500)
        assert 100.0 <= d <= 140.0, d

    def test_nearest_from_beijing_is_shahe_public_base(self):
        rows = obs.nearest_sites(39.9042, 116.4074, limit=6)
        assert rows[0]["site_id"] == "shahe-science-base"
        assert rows[0]["access"] == "public"
        assert rows[0]["distance_km"] < 40.0
        assert "xinglong" in [r["site_id"] for r in rows]
        assert [r["distance_km"] for r in rows] == sorted(
            r["distance_km"] for r in rows)

    def test_limit_is_respected(self):
        assert len(obs.nearest_sites(30.0, 110.0, limit=2)) == 2

    def test_invalid_coordinates_raise(self):
        for lat, lon in ((91.0, 0.0), (-91.0, 0.0), (0.0, 181.0)):
            try:
                obs.nearest_sites(lat, lon)
            except ValueError:
                continue
            raise AssertionError(f"({lat}, {lon}) 未被拒绝")

    def test_limit_must_be_positive(self):
        try:
            obs.nearest_sites(30.0, 110.0, limit=0)
        except ValueError:
            return
        raise AssertionError("limit=0 未被拒绝")


class TestGeojson:
    def test_point_coordinates_are_lon_lat(self):
        fc = obs.sites_as_geojson()
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) == len(obs.SITES)
        for f, s in zip(fc["features"], obs.SITES):
            assert f["geometry"]["type"] == "Point"
            assert f["geometry"]["coordinates"] == [s.lon_deg, s.lat_deg]

    def test_properties_carry_source_and_access(self):
        props = obs.sites_as_geojson()["features"][0]["properties"]
        assert props["role"] == "observing_site"
        assert props["access"] in obs.ACCESS_KINDS
        assert props["source_url"].startswith("http")
        assert props["name_zh"]
        assert "verified_on" in props

    def test_missing_altitude_exports_null_not_zero(self):
        """俄博梁官方页没给海拔：GeoJSON 里必须是 null，不是 0。"""
        fc = obs.sites_as_geojson()
        f = next(f for f in fc["features"]
                 if f["properties"]["site_id"] == "eboliang-yardang")
        assert f["properties"]["alt_m"] is None


class TestImportValidation:
    def test_valid_site_accepted(self):
        r = obs.validate_sites([_site()])
        assert r["ok"] is True
        assert r["counts"] == {"accepted": 1, "rejected": 0}

    def test_missing_source_is_rejected(self):
        r = obs.validate_sites([_site(source_url="")])
        assert r["ok"] is False
        assert "没有来源" in r["rejected"][0]["reasons"][0]

    def test_non_http_source_is_rejected(self):
        r = obs.validate_sites([_site(source_url="ftp://example.org/a")])
        assert r["ok"] is False
        assert any("http" in x for x in r["rejected"][0]["reasons"])

    def test_out_of_range_latitude_rejected(self):
        r = obs.validate_sites([_site(lat_deg=95.0)])
        assert r["ok"] is False
        assert any("lat_deg" in x for x in r["rejected"][0]["reasons"])

    def test_unknown_access_rejected(self):
        r = obs.validate_sites([_site(access="tourist")])
        assert r["ok"] is False
        assert any("access" in x for x in r["rejected"][0]["reasons"])

    def test_duplicate_id_rejected(self):
        r = obs.validate_sites([_site(), _site()])
        assert r["ok"] is False
        assert r["counts"] == {"accepted": 1, "rejected": 1}
        assert "重复" in " ".join(r["rejected"][0]["reasons"])

    def test_bool_latitude_rejected(self):
        r = obs.validate_sites([_site(lat_deg=True)])
        assert r["ok"] is False

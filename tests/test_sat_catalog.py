# SPDX-License-Identifier: MIT
"""卫星目录（S1）测试：从"只有 6 颗"到"内置基线 + 用户导入可合并"。

修的是什么
----------
改之前有两处**同抄一份**的 6 颗星目录（`orbit.BUILTIN_SATS` 与
`rf_sky_view.SATELLITE_META`），而且目录加载是**二选一**：

    user_cat = _load_user_tle_catalog()
    if user_cat: self._catalog.extend(user_cat)
    if not self._catalog:          # ← 用户有 TLE 就完全不用内置

于是用户想加一颗自己的星，NOAA/ISS/METEOR/FY-3D 就**全部消失**。
这不是"只有 6 颗"，是"要么 6 颗、要么你的"——两者不能并存。

修法：
1. 目录真值收进 `mbdsdr_ai/sat_catalog.BUILTIN_META` 一处，其余派生；
2. 加载改为**合并**：内置基线 + 磁盘缓存发现的星 + 用户文件，
   按归一化名称去重，用户同名优先。

本文件覆盖
----------
* 目录表单一来源（orbit 派生自 sat_catalog；桌面层已删除重复表）
* 缓存发现：跳过内置、坏文件不崩、无缓存不编
* 合并语义：并存 + 同名用户优先 + 顺序稳定
* 诚实口径：缓存星没有频率就是 None，不猜
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sat_catalog import (  # noqa: E402
    BUILTIN_CATNRS,
    BUILTIN_META,
    SatelliteMeta,
    catalog_report,
    discover_cached,
    merged_catalog,
    tle_age_days,
)

TLE_ISS = (
    "ISS (ZARYA)\n"
    "1 25544U 98067A   26320.50000000  .00016717  00000-0  30777-3 0  9993\n"
    "2 25544  51.6400 208.9163 0006317  69.9862  25.2906 15.50103450 12345\n"
)

#: 一颗**不在**内置目录里的星（NORAD 40069 是 METEOR M2，已内置；
#: 这里用 44387 = METEOR-M2 2，是另一颗，容易和 40069 混）。
TLE_USER = (
    "METEOR-M2 2\n"
    "1 44387U 19038A   26320.50000000  .00000100  00000-0  10000-3 0  9990\n"
    "2 44387  98.7000 200.0000 0001000  90.0000 270.0000 14.20000000 12345\n"
)


class TestCatalogSingleSource:
    def test_orbit_table_derives_from_sat_catalog(self):
        """`orbit.BUILTIN_SATS` 必须与 sat_catalog 一致（不许手抄）。"""
        from mbdsdr_ai.orbit import BUILTIN_SATS

        assert BUILTIN_SATS == {n: m.catnr for n, m in BUILTIN_META.items()}

    def test_rf_sky_view_no_longer_hardcodes_catnrs(self):
        """桌面绘制层的目录必须派生，不能在源码里再写一遍 6 个 NORAD。

        `SATELLITE_META` 那张重复表已从 `_load_celestrak_tle_catalog()`
        删除，加载器只走 `sat_catalog.merged_catalog()` + `BUILTIN_CATNRS`；
        因此改为解析源码 AST：必须 import sat_catalog，且不存在
        `{"catnr": <数字>}` 这类字面量（防止重复表回流）。
        """
        import ast

        import desktop.rf_sky_view as rsv

        tree = ast.parse(open(rsv.__file__, encoding="utf-8").read())
        imports_sat_catalog = any(
            isinstance(node, ast.ImportFrom) and node.module == "mbdsdr_ai.sat_catalog"
            for node in ast.walk(tree)
        )
        assert imports_sat_catalog, "绘制层没从 sat_catalog 取目录，说明又抄了一份"
        hardcoded = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "catnr"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, int)):
                    hardcoded.add(value.value)
        assert not (hardcoded & set(BUILTIN_CATNRS)), (
            f"源码里仍硬编码 NORAD {sorted(hardcoded & set(BUILTIN_CATNRS))}，"
            "唯一真值应只在 sat_catalog")

    def test_builtin_catnrs_match_meta(self):
        assert BUILTIN_CATNRS == frozenset(m.catnr for m in BUILTIN_META.values())

    def test_known_catnr_identity(self):
        """44/54234 错配事故的回归锁：编号必须与真实对象一致。"""
        assert BUILTIN_META["METEOR M2"].catnr == 40069      # 不是 44016（残骸）
        assert BUILTIN_META["FENGYUN 3D"].catnr == 43010     # 不是 54234（NOAA 21）


class TestDiscoverCached:
    def test_finds_non_builtin_star(self, tmp_path):
        (tmp_path / "44387.tle").write_text(TLE_USER, encoding="utf-8")
        found = discover_cached(str(tmp_path))
        assert len(found) == 1
        assert found[0].catnr == 44387
        assert found[0].name == "METEOR-M2 2"
        assert found[0].kind == "other"
        # 频率未知就是未知，不猜一个 137.x
        assert found[0].freq_mhz is None

    def test_skips_builtin_catnrs(self, tmp_path):
        """内置星由基线提供，缓存里同一颗不重复列。"""
        (tmp_path / "25544.tle").write_text(TLE_ISS, encoding="utf-8")
        assert discover_cached(str(tmp_path)) == []

    def test_bad_file_does_not_break_others(self, tmp_path):
        (tmp_path / "11111.tle").write_text("garbage", encoding="utf-8")
        (tmp_path / "44387.tle").write_text(TLE_USER, encoding="utf-8")
        found = discover_cached(str(tmp_path))
        # 坏文件要么被跳过，要么退化成"NORAD 11111"——但不能让好文件消失
        assert any(e.catnr == 44387 for e in found)

    def test_missing_dir_is_empty_not_error(self, tmp_path):
        assert discover_cached(str(tmp_path / "nope")) == []

    def test_non_tle_files_ignored(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
        assert discover_cached(str(tmp_path)) == []


class TestMergedCatalog:
    def test_builtin_first_then_cached(self, tmp_path):
        (tmp_path / "44387.tle").write_text(TLE_USER, encoding="utf-8")
        merged = merged_catalog(str(tmp_path))
        names = [e.name for e in merged]
        # 内置 6 颗在前，且顺序与 BUILTIN_META 声明一致
        assert names[:len(BUILTIN_META)] == list(BUILTIN_META)
        assert "METEOR-M2 2" in names[len(BUILTIN_META):]

    def test_without_cache_only_builtin(self, tmp_path):
        merged = merged_catalog(str(tmp_path))
        assert len(merged) == len(BUILTIN_META)

    def test_include_cached_false(self, tmp_path):
        (tmp_path / "44387.tle").write_text(TLE_USER, encoding="utf-8")
        merged = merged_catalog(str(tmp_path), include_cached=False)
        assert len(merged) == len(BUILTIN_META)


class TestReport:
    def test_counts_are_consistent(self, tmp_path):
        (tmp_path / "44387.tle").write_text(TLE_USER, encoding="utf-8")
        r = catalog_report(str(tmp_path))
        assert r["builtin_count"] == len(BUILTIN_META)
        assert r["cached_count"] == 1
        assert r["total_count"] == r["builtin_count"] + r["cached_count"]
        # 缓存星没有频率，必须被单独数出来，不能藏进"共 N 颗"
        assert r["cached_without_freq"] == 1

    def test_age_none_without_cache(self, tmp_path):
        assert tle_age_days(44387, str(tmp_path)) is None

    def test_age_non_negative_with_cache(self, tmp_path):
        (tmp_path / "44387.tle").write_text(TLE_USER, encoding="utf-8")
        age = tle_age_days(44387, str(tmp_path))
        assert age is not None and age >= 0.0


# ---------------------------------------------------------------------------
# 合并语义：这是 S1 的核心修复，必须直接钉住
# ---------------------------------------------------------------------------

class _FakeSkyView:
    def __init__(self):
        self.loaded = None

    def set_tle_loaded(self, value):
        self.loaded = value


class _FakeTracker:
    """只提供 `_reload_catalog` 需要的三个属性，不构造真 Qt 对象。"""

    def __init__(self):
        self._catalog = []
        self._satrec_cache = {}
        self.sky_view = _FakeSkyView()


def _reload(monkeypatch, rsv, user_rows, cel_rows):
    monkeypatch.setattr(rsv, "_load_user_tle_catalog", lambda: list(user_rows))
    monkeypatch.setattr(rsv, "_load_celestrak_tle_catalog", lambda: list(cel_rows))
    fake = _FakeTracker()
    rsv.SatelliteTracker._reload_catalog(fake)
    return fake._catalog


def _row(name, catnr):
    return {"name": name, "line1": "1 x", "line2": "2 x",
            "freq_mhz": None, "kind": "other"}


class TestMergeSemantics:
    def test_user_and_builtin_coexist(self, monkeypatch):
        """核心修复：加了用户星，内置星不能消失。"""
        import desktop.rf_sky_view as rsv

        builtin = [_row("NOAA 19", 33591), _row("ISS (ZARYA)", 25544)]
        user = [_row("METEOR-M2 2", 44387)]
        cat = _reload(monkeypatch, rsv, user, builtin)
        names = [r["name"] for r in cat]
        assert "NOAA 19" in names, "用户加了一颗星，内置星就没了——这就是修之前的病"
        assert "ISS (ZARYA)" in names
        assert "METEOR-M2 2" in names
        assert len(cat) == 3

    def test_user_same_name_wins(self, monkeypatch):
        """同名时用用户那份：他可能改了频率或换了 TLE 源。"""
        import desktop.rf_sky_view as rsv

        builtin = [{"name": "NOAA 15", "line1": "1 a", "line2": "2 a",
                    "freq_mhz": 137.62, "kind": "weather"}]
        user = [{"name": "NOAA 15", "line1": "1 b", "line2": "2 b",
                 "freq_mhz": 137.5, "kind": "custom"}]
        cat = _reload(monkeypatch, rsv, user, builtin)
        assert len(cat) == 1, "同名应该覆盖，不是并列两条"
        assert cat[0]["line1"] == "1 b"
        assert cat[0]["freq_mhz"] == 137.5

    def test_name_normalization_dedups(self, monkeypatch):
        """`METEOR-M 2` 与 `METEOR M2` 是同一颗，不该出现两条。"""
        import desktop.rf_sky_view as rsv

        builtin = [_row("METEOR M2", 40069)]
        user = [_row("METEOR-M 2", 40069)]
        cat = _reload(monkeypatch, rsv, user, builtin)
        assert len(cat) == 1, [r["name"] for r in cat]

    def test_duplicate_within_builtin_is_collapsed(self, monkeypatch):
        import desktop.rf_sky_view as rsv

        builtin = [_row("NOAA 19", 33591), _row("NOAA-19", 33591)]
        cat = _reload(monkeypatch, rsv, [], builtin)
        assert len(cat) == 1

    def test_empty_everything_marks_not_loaded(self, monkeypatch):
        import desktop.rf_sky_view as rsv

        fake = _FakeTracker()
        monkeypatch.setattr(rsv, "_load_user_tle_catalog", lambda: [])
        monkeypatch.setattr(rsv, "_load_celestrak_tle_catalog", lambda: [])
        rsv.SatelliteTracker._reload_catalog(fake)
        assert fake._catalog == []
        assert fake.sky_view.loaded is False

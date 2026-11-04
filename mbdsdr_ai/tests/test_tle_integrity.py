# SPDX-License-Identifier: MIT
"""TLE 目录真实性守卫（P10）确定性测试。

覆盖：
  * 名称 ↔ 数据对象名归一化校验（含防串号）；
  * 编号错配（44016→METEOR M2 / 54234→FENGYUN 3D）必须降级，不得可选、
    不得用于过境/跟踪；
  * 修正后的编号全目录扫描全部通过；
  * 缺数据单独归类 no_data，不冒充核实；
  * 多源冲突（同名不同号 / 同号不同名）逐条留痕。

不联网：数据池全部用夹具注入；本机缓存存在时另做一次实况扫描（只断言
「无错配」，缺文件不算失败）。

运行：python -m pytest mbdsdr_ai/tests/test_tle_integrity.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.tle_integrity import (  # noqa: E402
    CatalogEntry,
    cache_entries,
    freshness,
    names_match,
    normalize_name,
    scan_catalog,
    scan_live,
    verify_identity,
)

L1 = "1 40069U 14037A   26297.30989954  .00000323  00000+0  16680-3 0  9998"
L2 = "2 40069  98.5301 268.5877 0006497  31.2770 328.8793 14.21494425637660"

# 固定参考时刻：距夹具 TLE 历元 26297（2026-10-24）约 1.6 天 → usable
NOW = datetime(2026, 10, 26, 0, 0, 0, tzinfo=timezone.utc)

# 真实缓存快照（2026-11 本机 ~/.mbdsdr/tle_cache，Celestrak 下载件）
TRUTH = {
    25338: "NOAA 15",
    25544: "ISS (ZARYA)",
    28654: "NOAA 18",
    33591: "NOAA 19",
    40069: "METEOR-M 2",
    43010: "FENGYUN 3D",
}
BUGGY_TRUTH = dict(TRUTH)
BUGGY_TRUTH.update({44016: "ATLAS 5 CENTAUR DEB", 54234: "NOAA 21 (JPSS-2)"})


def _pool(truth: dict) -> list:
    """按真实对象名造数据池（line1 编号与名字一致即可，结构校验用）。"""
    out = []
    for catnr, obj in truth.items():
        out.append(CatalogEntry(
            name=obj, catnr=catnr, source="cache",
            line1=f"1 {catnr:05d}U 14037A   26297.30989954  .00000323  00000+0  16680-3 0  9998",
            line2=L2, object_name=obj, fetched_at=1.0,
        ))
    return out


FIXED_MAP = {
    "NOAA 15": 25338, "NOAA 18": 28654, "NOAA 19": 33591,
    "ISS (ZARYA)": 25544, "METEOR M2": 40069, "FENGYUN 3D": 43010,
}
BUGGY_MAP = dict(FIXED_MAP, **{"METEOR M2": 44016, "FENGYUN 3D": 54234})


def _decls(sat_map: dict) -> list:
    return [CatalogEntry(name=n, catnr=c, source="builtin")
            for n, c in sat_map.items()]


# ---------------------------------------------------------------------------
class TestIdentity:
    def test_normalization(self):
        assert normalize_name("METEOR-M 2") == "METEORM2"
        assert normalize_name("METEOR M2") == "METEORM2"
        assert normalize_name("ISS (ZARYA)") == "ISSZARYA"

    @pytest.mark.parametrize("declared,obj,ok", [
        ("METEOR M2", "METEOR-M 2", True),
        ("NOAA 19", "NOAA 19", True),
        ("METEOR M2", "ATLAS 5 CENTAUR DEB", False),
        ("FENGYUN 3D", "NOAA 21 (JPSS-2)", False),
        ("METEOR M2", "METEOR-M2 2", False),   # 防串号
    ])
    def test_names_match(self, declared, obj, ok):
        assert names_match(declared, obj) is ok

    def test_verify_identity_three_states(self):
        assert verify_identity("METEOR M2", "METEOR-M 2")["status"] == "verified"
        assert verify_identity("METEOR M2", "ATLAS 5 CENTAUR DEB")["status"] == "mismatch"
        assert verify_identity("METEOR M2", "")["status"] == "no_data"


# ---------------------------------------------------------------------------
class TestCatalogScan:
    def test_fixed_ids_all_pass(self):
        rep = scan_catalog(_decls(FIXED_MAP), _pool(TRUTH), now=NOW)
        assert (rep["total"], rep["passed"], rep["degraded"]) == (6, 6, 0)
        assert rep["mismatch"] == 0 and rep["no_data"] == 0
        assert all(r["selectable"] and r["pass_eligible"] for r in rep["rows"])
        assert rep["conflicts"] == []

    def test_buggy_ids_are_degraded_not_used(self):
        """事故回归锁：44016 声明为 METEOR M2、54234 声明为 FENGYUN 3D，
        数据侧实为 ATLAS 5 CENTAUR DEB / NOAA 21——必须降级，不得可选、
        不得用于过境与跟踪，且原因如实列出。"""
        rep = scan_catalog(_decls(BUGGY_MAP), _pool(BUGGY_TRUTH), now=NOW)
        assert rep["total"] == 6
        assert rep["passed"] == 4
        assert rep["degraded"] == 2
        assert rep["mismatch"] == 2 and rep["no_data"] == 0
        bad = {r["name"]: r for r in rep["rows"] if r["degraded"]}
        assert set(bad) == {"METEOR M2", "FENGYUN 3D"}
        for r in bad.values():
            assert r["selectable"] is False
            assert r["pass_eligible"] is False
            assert r["tracking_eligible"] is False
            assert r["identity"]["status"] == "mismatch"
        reasons = {x["name"]: x for x in rep["reasons"]["identity_mismatch"]}
        assert reasons["METEOR M2"]["object_name"] == "ATLAS 5 CENTAUR DEB"
        assert reasons["FENGYUN 3D"]["object_name"] == "NOAA 21 (JPSS-2)"

    def test_missing_data_is_no_data_not_mismatch(self):
        rep = scan_catalog(_decls(FIXED_MAP), [], now=NOW)
        assert rep["passed"] == 0 and rep["degraded"] == 6
        assert rep["mismatch"] == 0 and rep["no_data"] == 6
        for r in rep["rows"]:
            assert r["identity"]["status"] == "no_data"
            assert r["pass_eligible"] is False

    def test_declaration_conflicts_are_recorded(self):
        decls = [
            CatalogEntry(name="METEOR M2", catnr=40069, source="user"),
            CatalogEntry(name="METEOR M2", catnr=44016, source="builtin"),
            CatalogEntry(name="ATLAS 5 CENTAUR DEB", catnr=44016, source="cache"),
        ]
        rep = scan_catalog(decls, _pool(BUGGY_TRUTH), now=NOW)
        types = {c["type"] for c in rep["conflicts"]}
        assert types == {"name_catnr_conflict", "catnr_name_conflict"}
        # 冲突必须带在相关行上（不静默择一）
        assert any(r["conflicts"] for r in rep["rows"])

    def test_live_cache_has_no_mismatch(self):
        d = os.path.expanduser("~/.mbdsdr/tle_cache")
        if not os.path.isdir(d):
            pytest.skip("无 Celestrak 缓存目录（离线环境，跳过实况扫描）")
        rep = scan_live(cache_dir=d)
        assert rep["mismatch"] == 0, rep["reasons"]["identity_mismatch"]
        # 缓存齐全时只剩「过期」类降级（缓存件历元可能偏旧，如实标注）
        cached = {e.catnr for e in cache_entries(cache_dir=d)}
        if set(FIXED_MAP.values()) <= cached:
            assert rep["degraded"] == rep["stale"]
            assert rep["no_data"] == 0


# ---------------------------------------------------------------------------
class TestFreshness:
    """历元年龄四级：fresh ≤24h / usable ≤7d / expired ≤30d / dead >30d。"""

    OLD_L1 = "1 25544U 98067A   24275.50000000  .00016717  00000-0  10270-3 0  9000"

    @pytest.mark.parametrize("now,tier,eligible", [
        (datetime(2024, 10, 2, 12, tzinfo=timezone.utc), "fresh", True),
        (datetime(2024, 10, 5, 12, tzinfo=timezone.utc), "usable", True),
        (datetime(2024, 10, 20, 12, tzinfo=timezone.utc), "expired", False),
        (datetime(2024, 12, 1, 12, tzinfo=timezone.utc), "dead", False),
    ])
    def test_tiers(self, now, tier, eligible):
        f = freshness(self.OLD_L1, now=now)
        assert f["tier"] == tier
        assert f["pass_eligible"] is eligible
        assert f["tracking_eligible"] is eligible
        assert f["epoch_utc"] == "2024-10-01T12:00:00Z"

    def test_unknown_epoch_is_not_eligible(self):
        f = freshness("not a tle", now=NOW)
        assert f["tier"] == "unknown"
        assert f["pass_eligible"] is False and f["age_days"] is None

    def test_cache_time_is_separate_from_epoch(self):
        """缓存时间与历元分开报告：历元新鲜但缓存很久没刷新也要看得见。"""
        fetched = NOW.timestamp() - 5 * 86400.0
        f = freshness(L1, fetched_at=fetched, now=NOW)
        assert f["tier"] == "usable"
        assert f["cache_age_hours"] == pytest.approx(120.0, abs=0.5)

    def test_stale_entry_displayable_but_degraded(self):
        """过期条目仍可显示/可查，但默认不得用于过境与跟踪。"""
        old = CatalogEntry(name="ISS (ZARYA)", catnr=25544, source="cache",
                           line1=self.OLD_L1,
                           line2="2 25544  51.6400 292.0000 0001333  90.0000 270.0000 15.50000000    000",
                           object_name="ISS (ZARYA)")
        rep = scan_catalog([old], now=datetime(2024, 12, 1, tzinfo=timezone.utc))
        row = rep["rows"][0]
        assert rep["passed"] == 0 and rep["degraded"] == 1
        assert rep["stale"] == 1 and rep["mismatch"] == 0
        assert row["displayable"] is True
        assert row["degraded"] is True
        assert row["pass_eligible"] is False
        assert row["tracking_eligible"] is False
        assert row["degrade_reason"] == "tle_dead"
        assert row["freshness"]["tier"] == "dead"


# ---------------------------------------------------------------------------
class TestFetchRecord:
    """取新失败必须保留旧值并标「未更新」，不许静默。"""

    @staticmethod
    def _write_cache(tmp_path, catnr=40069, mtime=None):
        p = tmp_path / f"{catnr}.tle"
        p.write_text(f"METEOR-M 2\n{L1}\n{L2}\n", encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def test_fresh_cache_is_reported_as_cache(self, monkeypatch, tmp_path):
        import mbdsdr_ai.orbit as orbit
        import time as _time
        monkeypatch.setattr(orbit, "_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(orbit, "_TLE_CACHE", {})
        self._write_cache(tmp_path, mtime=_time.time() - 3600.0)
        rec = orbit.fetch_tle_record(40069)
        assert rec["refresh_status"] == "cache"
        assert rec["name"] == "METEOR-M 2"
        assert rec["line1"].startswith("1 40069")

    def test_stale_cache_kept_and_marked_not_updated(self, monkeypatch,
                                                     tmp_path):
        import mbdsdr_ai.orbit as orbit
        import time as _time
        monkeypatch.setattr(orbit, "_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(orbit, "_TLE_CACHE", {})
        monkeypatch.setenv("MBDSDR_OFFLINE", "1")
        old = _time.time() - 10 * 86400.0
        self._write_cache(tmp_path, mtime=old)
        rec = orbit.fetch_tle_record(40069)
        # 旧值不丢、不抛错，且明确标「未更新」+ 原因
        assert rec["refresh_status"] == "not_updated"
        assert "离线" in rec["refresh_error"]
        assert rec["line1"].startswith("1 40069")
        assert rec["line2"].startswith("2 40069")
        f = freshness(rec["line1"], rec["fetched_at"])
        assert f["tier"] in ("usable", "expired", "dead")
        assert f["pass_eligible"] is (f["tier"] in ("fresh", "usable"))

    def test_no_data_no_network_is_honest_error(self, monkeypatch, tmp_path):
        import mbdsdr_ai.orbit as orbit
        from mbdsdr_ai.orbit import TLEUnavailableError
        monkeypatch.setattr(orbit, "_CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(orbit, "_TLE_CACHE", {})
        monkeypatch.setenv("MBDSDR_OFFLINE", "1")
        with pytest.raises(TLEUnavailableError):
            orbit.fetch_tle_record(99999999)


# ---------------------------------------------------------------------------
class TestBuiltinTables:
    def test_orbit_builtin_ids_are_real(self):
        from mbdsdr_ai.orbit import BUILTIN_SATS
        assert BUILTIN_SATS["METEOR M2"] == 40069
        assert BUILTIN_SATS["FENGYUN 3D"] == 43010

    def test_new_spacetime_defaults_are_real(self):
        from mbdsdr_ai.new_spacetime_tle import DEFAULT_SATELLITES
        m = dict(DEFAULT_SATELLITES)
        assert m["METEOR M2"] == 40069
        assert m["FENGYUN 3D"] == 43010


def test_tle_line_fixture_is_self_consistent():
    """夹具自洽：line1/line2 的编号一致（避免测试本身造假）。"""
    assert CatalogEntry(name="METEOR M2", catnr=40069, line1=L1, line2=L2,
                        object_name="METEOR-M 2").has_tle

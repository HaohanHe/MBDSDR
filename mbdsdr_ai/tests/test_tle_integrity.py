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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.tle_integrity import (  # noqa: E402
    CatalogEntry,
    cache_entries,
    names_match,
    normalize_name,
    scan_catalog,
    scan_live,
    verify_identity,
)

L1 = "1 40069U 14037A   26297.30989954  .00000323  00000+0  16680-3 0  9998"
L2 = "2 40069  98.5301 268.5877 0006497  31.2770 328.8793 14.21494425637660"

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
        rep = scan_catalog(_decls(FIXED_MAP), _pool(TRUTH))
        assert (rep["total"], rep["passed"], rep["degraded"]) == (6, 6, 0)
        assert rep["mismatch"] == 0 and rep["no_data"] == 0
        assert all(r["selectable"] and r["pass_eligible"] for r in rep["rows"])
        assert rep["conflicts"] == []

    def test_buggy_ids_are_degraded_not_used(self):
        """事故回归锁：44016 声明为 METEOR M2、54234 声明为 FENGYUN 3D，
        数据侧实为 ATLAS 5 CENTAUR DEB / NOAA 21——必须降级，不得可选、
        不得用于过境与跟踪，且原因如实列出。"""
        rep = scan_catalog(_decls(BUGGY_MAP), _pool(BUGGY_TRUTH))
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
        rep = scan_catalog(_decls(FIXED_MAP), [])
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
        rep = scan_catalog(decls, _pool(BUGGY_TRUTH))
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
        # 缓存齐全时不允许有缺数据/降级
        cached = {e.catnr for e in cache_entries(cache_dir=d)}
        if set(FIXED_MAP.values()) <= cached:
            assert rep["degraded"] == 0


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

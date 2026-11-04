# SPDX-License-Identifier: MIT
"""
tests/test_satellite_catnr_identity.py
======================================
目录名 ↔ CATNR 一致性通用校验（44016/54234 错配事故的回归锁）。

事故背景：目录表把 "METEOR M2" 映射到 44016（实际为 ATLAS 5 CENTAUR DEB
箭体碎片）、把 "FENGYUN 3D" 映射到 54234（实际为 NOAA 21 JPSS-2），
导致天空视图中这两颗卫星位置完全错误。根因是「名称 ↔ NORAD 编号」
错配，且此前无任何校验覆盖。

钉住的事实（依据：Celestrak weather.txt 下载件
experiments/satellite_rx/data/tle_weather.txt，历元 2026 年第 297 天）：
  - 全目录 6 颗的名称→CATNR 映射与真实 TLE line0 语义一致；
  - 错配检测全量列出（不是只报第一个）；
  - 缓存缺编号按「缺数据」单独归类，不算错配；
  - 归一化必须完全相等，防串号（'METEOR M2' ≠ 'METEOR-M2 2'）。

运行:
    python -m pytest tests/test_satellite_catnr_identity.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from rf_sky_view import (  # noqa: E402
    _load_celestrak_tle_catalog,
    _norm_sat_name,
    satellite_catalog_mismatches,
    satellite_name_matches,
)


def _meta_table() -> dict:
    """从生产加载器拿目录表（函数内局部变量，测试通过解析函数源码不可靠，
    改为直接引用其内部表：加载器把 SATELLITE_META 定义在函数体内，
    这里用一次性执行截取——保持与生产数据单一来源）。"""
    import inspect
    import re as _re
    import rf_sky_view
    src = inspect.getsource(rf_sky_view._load_celestrak_tle_catalog)
    m = _re.search(r"SATELLITE_META[^=]*=\s*(\{.*?\n    \})", src, _re.DOTALL)
    assert m, "生产加载器里找不到 SATELLITE_META 表"
    # 表体是纯字面量 dict（name/catnr/freq_mhz/kind），安全 eval
    table = eval(m.group(1), {"__builtins__": {}})
    return {str(k): dict(v) for k, v in table.items()}


# ---------------------------------------------------------------------------
# 匹配语义（含防串号）
# ---------------------------------------------------------------------------
class TestNameMatching:
    @pytest.mark.parametrize("declared,line0", [
        ("NOAA 15", "NOAA 15"),
        ("ISS (ZARYA)", "ISS (ZARYA)"),
        ("METEOR M2", "METEOR-M 2"),       # 分隔符差异归一
        ("FENGYUN 3D", "FENGYUN 3D"),
    ])
    def test_positive(self, declared, line0):
        assert satellite_name_matches(declared, line0) is True

    @pytest.mark.parametrize("declared,line0", [
        ("METEOR M2", "ATLAS 5 CENTAUR DEB"),   # 44016 事故
        ("FENGYUN 3D", "NOAA 21 (JPSS-2)"),     # 54234 事故
        ("METEOR M2", "METEOR-M2 2"),           # 防串号：N2-2 是另一颗星
        ("METEOR M2", "METEOR-M2 3"),           # 防串号：N2-3
        ("NOAA 15", "NOAA 18"),
    ])
    def test_negative(self, declared, line0):
        assert satellite_name_matches(declared, line0) is False

    def test_normalization(self):
        assert _norm_sat_name("METEOR-M 2") == "METEORM2"
        assert _norm_sat_name("METEOR M2") == "METEORM2"
        assert _norm_sat_name("ISS (ZARYA)") == "ISSZARYA"


# ---------------------------------------------------------------------------
# 全目录扫描（fixture 注入 resolver，不依赖本机缓存）
# ---------------------------------------------------------------------------
class TestCatalogScan:
    def test_current_meta_matches_real_line0s(self):
        """生产目录表 ↔ 真实 TLE line0（Celestrak weather.txt 本地下载件）。
        每一颗都必须语义匹配；错配全量列出（断言信息即清单）。"""
        table = _meta_table()
        # 真实 line0 快照：本地缓存 + 已核实的 weather.txt 下载件
        truth = {
            25338: "NOAA 15",
            25544: "ISS (ZARYA)",
            28654: "NOAA 18",
            33591: "NOAA 19",
            40069: "METEOR-M 2",
            43010: "FENGYUN 3D",
            # 反例数据（事故条目）：
            44016: "ATLAS 5 CENTAUR DEB",
            54234: "NOAA 21 (JPSS-2)",
        }
        mismatches, no_data = satellite_catalog_mismatches(
            table, lambda c: truth.get(c))
        assert mismatches == [], \
            f"目录错配: {[(n, c, l0) for n, c, l0 in mismatches]}"
        assert no_data == []

    def test_buggy_snapshot_reports_all_mismatches(self):
        """注入事故快照：必须把两处错配全部列出（不是只报第一个）。"""
        table = _meta_table()
        buggy = {
            25338: "NOAA 15", 25544: "ISS (ZARYA)", 28654: "NOAA 18",
            33591: "NOAA 19",
            44016: "ATLAS 5 CENTAUR DEB",   # 44016 事故条目的真实 line0
            54234: "NOAA 21 (JPSS-2)",      # 54234 事故条目的真实 line0
        }
        # 用事故编号构造假表（模拟修复前状态）
        legacy = dict(table)
        legacy["METEOR M2"] = dict(table["METEOR M2"], catnr=44016)
        legacy["FENGYUN 3D"] = dict(table["FENGYUN 3D"], catnr=54234)
        mismatches, no_data = satellite_catalog_mismatches(
            legacy, lambda c: buggy.get(c))
        kinds = sorted(n for n, _c, _l0 in mismatches)
        assert kinds == ["FENGYUN 3D", "METEOR M2"]
        assert len(mismatches) == 2 and no_data == []

    def test_missing_cache_is_no_data_not_mismatch(self):
        table = _meta_table()
        mismatches, no_data = satellite_catalog_mismatches(
            table, lambda c: None)
        assert mismatches == []
        assert sorted(n for n, _c in no_data) == sorted(table.keys())


# ---------------------------------------------------------------------------
# 本机缓存实况（可选：缓存存在时钉死正确条目不再回退）
# ---------------------------------------------------------------------------
class TestLiveCache:
    @staticmethod
    def _resolver():
        d = os.path.expanduser("~/.mbdsdr/tle_cache")
        if not os.path.isdir(d):
            return None

        def _resolve(catnr):
            p = os.path.join(d, f"{catnr}.tle")
            if not os.path.isfile(p):
                return None
            lines = [l for l in open(p, encoding="utf-8",
                                       errors="replace") if l.strip()]
            return lines[0].strip() if lines else None

        return _resolve

    def test_live_cache_has_no_mismatch(self):
        resolve = self._resolver()
        if resolve is None:
            pytest.skip("无 Celestrak 缓存目录（离线环境，跳过实况校验）")
        table = _meta_table()
        mismatches, no_data = satellite_catalog_mismatches(table, resolve)
        # 错配必须为零；缺数据单独列出（联网刷新缓存后可消）
        assert mismatches == [], \
            f"缓存错配: {[(n, c, l0) for n, c, l0 in mismatches]}"

    def test_loader_builds_catalog_from_fixed_ids(self, monkeypatch):
        """修复后的编号走完整加载链：fetch_tle 按新编号取件（替身），
        目录里出现 METEOR M2/FENGYUN 3D 且 TLE 行与编号一致。"""
        import mbdsdr_ai.orbit as _orbit
        fake = {
            40069: ("1 40069U 14037A   26297.30989954  .00000323  00000+0  16680-3 0  9998",
                    "2 40069  98.5301 268.5877 0006497  31.2770 328.8793 14.21494425637660"),
            43010: ("1 43010U 17072A   26297.32660775  .00000262  00000+0  14549-3 0  9992",
                    "2 43010  99.0307 274.1912 0002437  56.2336 303.9071 14.19764186463312"),
        }
        monkeypatch.setattr(_orbit, "fetch_tle", lambda c: fake[c])
        # 链路目录替身（find_tuning_for 在加载器函数内按模块名查找）
        import mbdsdr_ai.sat_links as _sl
        monkeypatch.setattr(_sl, "find_tuning_for", lambda _n: None)
        from rf_sky_view import _load_celestrak_tle_catalog
        cat = _load_celestrak_tle_catalog()
        by_name = {e["name"]: e for e in cat}
        assert by_name["METEOR M2"]["line1"].startswith("1 40069")
        assert by_name["FENGYUN 3D"]["line1"].startswith("1 43010")


# ---------------------------------------------------------------------------
# P10 扩展：编号 ↔ 名称 ↔ 来源 三方校验接入身份红线
# ---------------------------------------------------------------------------
class TestCatalogIdentityGate:
    """编号-名称-来源校验的回归锁：错配必须降级，不得被采用。"""

    _ATLAS_L1 = ("1 44016U 14055BX  26305.50000000 -.00000086  00000+0  "
                 "00000+0 0  9990")
    _ATLAS_L2 = ("2 44016  19.8252 144.6831 4693919 161.0339 224.8913  "
                 "1.83868310 52931")

    def test_python_and_desktop_catalogs_agree_on_real_catnrs(self):
        """desktop 目录表与 Python 内置表必须指向同一组真实编号。"""
        from mbdsdr_ai.orbit import BUILTIN_SATS
        from mbdsdr_ai.new_spacetime_tle import DEFAULT_SATELLITES
        table = _meta_table()
        assert table["METEOR M2"]["catnr"] == 40069
        assert table["FENGYUN 3D"]["catnr"] == 43010
        assert BUILTIN_SATS["METEOR M2"] == table["METEOR M2"]["catnr"]
        assert BUILTIN_SATS["FENGYUN 3D"] == table["FENGYUN 3D"]["catnr"]
        assert dict(DEFAULT_SATELLITES)["METEOR M2"] == 40069
        assert dict(DEFAULT_SATELLITES)["FENGYUN 3D"] == 43010

    def test_injected_mismatch_is_degraded_not_used(self):
        """注入事故条目（METEOR M2 → 44016，数据实为 ATLAS 5 CENTAUR DEB）：
        必须降级、不可选、不可用于过境/跟踪，并给出真实对象名。"""
        from datetime import datetime, timezone
        from mbdsdr_ai.tle_integrity import CatalogEntry, scan_catalog
        decls = [CatalogEntry(name="METEOR M2", catnr=44016, source="builtin")]
        pool = [CatalogEntry(
            name="ATLAS 5 CENTAUR DEB", catnr=44016, source="cache",
            line1=self._ATLAS_L1, line2=self._ATLAS_L2,
            object_name="ATLAS 5 CENTAUR DEB", data_source="cache",
            fetched_at=datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp())]
        rep = scan_catalog(decls, pool,
                           now=datetime(2026, 11, 2, tzinfo=timezone.utc))
        row = rep["rows"][0]
        assert row["identity"]["status"] == "mismatch"
        assert row["object_name"] == "ATLAS 5 CENTAUR DEB"
        assert row["degraded"] is True
        assert row["selectable"] is False
        assert row["pass_eligible"] is False
        assert row["tracking_eligible"] is False
        assert rep["passed"] == 0 and rep["mismatch"] == 1

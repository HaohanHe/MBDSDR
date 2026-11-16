# SPDX-License-Identifier: MIT
"""卫星目录在**执行侧**（tracker）的完整性：一套系统不许有两个目录。

修的是什么
----------
`SatelliteTracker` 的目录以前由「内置 TLE 快照 + 这 6 颗的缓存更新」组成，
而天空图有自己的目录（`sat_catalog.BUILTIN_META`）。两者**不是同一个集合**：

    快照: NOAA 15/18/19, ISS, AO-73, FOX-1B
    基线: NOAA 15/18/19, ISS, METEOR M2, FENGYUN 3D

于是「天空图看得到 METEOR，跟踪/过境看不到它」。用户把别的星的 TLE 放进
缓存目录，也只有一边能看见。

修法：`_load_extra_catalog()` 在启动时
  1. 补齐基线里快照没有的星（从磁盘缓存取，缓存也没有就不编）；
  2. 并入缓存目录里的额外星；
  3. 并入 `~/.mbdsdr/tle/*.tle`。

本文件覆盖
----------
* 基线齐全（缓存可用时，`BUILTIN_META` 里每颗都在目录里）
* 额外缓存星与用户 TLE 目录都能进目录
* 同 NORAD 不重复
* 坏文件不连坐
* 缓存缺失时不编造（该缺就缺）
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sat_catalog import BUILTIN_META  # noqa: E402

TLE_METEOR = (
    "METEOR-M 2\n"
    "1 40069U 14037A   26320.50000000  .00000100  00000-0  10000-3 0  9990\n"
    "2 40069  98.7000 200.0000 0001000  90.0000 270.0000 14.20000000 12345\n"
)
TLE_FY3D = (
    "FENGYUN 3D\n"
    "1 43010U 17072A   26320.50000000  .00000100  00000-0  10000-3 0  9990\n"
    "2 43010  98.7000 210.0000 0001000  90.0000 270.0000 14.20000000 12345\n"
)
TLE_EXTRA = (
    "METEOR-M2 2\n"
    "1 44387U 19038A   26320.50000000  .00000100  00000-0  10000-3 0  9990\n"
    "2 44387  98.7000 200.0000 0001000  90.0000 270.0000 14.20000000 12345\n"
)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """隔离缓存目录与用户 TLE 目录，避免读到本机真实文件。"""
    cache = tmp_path / "cache"
    user = tmp_path / "user"
    cache.mkdir()
    user.mkdir()
    monkeypatch.setenv("MBDSDR_TLE_CACHE_DIR", str(cache))
    monkeypatch.setenv("MBDSDR_USER_TLE_DIR", str(user))
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")
    return cache, user


def _tracker():
    from mbdsdr_ai.sat_tracker import SatelliteTracker

    return SatelliteTracker()


def _norads(tracker) -> set:
    return {str(s["norad_id"]) for s in tracker.list_satellites()}


class TestBaselineCompleteness:
    def test_missing_baseline_entries_are_filled_from_cache(self, isolated):
        """快照里没有 METEOR/FY-3D，但缓存里有——必须补进目录。"""
        cache, _ = isolated
        (cache / "40069.tle").write_text(TLE_METEOR, encoding="utf-8")
        (cache / "43010.tle").write_text(TLE_FY3D, encoding="utf-8")

        t = _tracker()
        norads = _norads(t)
        for meta in BUILTIN_META.values():
            assert str(meta.catnr) in norads, \
                f"基线星 {meta.name}({meta.catnr}) 不在 tracker 目录里"

    def test_absent_cache_means_absent_entry(self, isolated):
        """缓存里也没有就不编——目录基线是"应该有"，不是"必须有"。"""
        t = _tracker()
        missing = [m for m in BUILTIN_META.values()
                   if str(m.catnr) not in _norads(t)]
        # 隔离环境下没有 40069/43010 缓存，这两颗应该确实不在
        assert {m.name for m in missing} >= {"METEOR M2", "FENGYUN 3D"}


class TestExtraCatalog:
    def test_extra_cached_star_is_loaded(self, isolated):
        cache, _ = isolated
        (cache / "44387.tle").write_text(TLE_EXTRA, encoding="utf-8")
        t = _tracker()
        assert "44387" in _norads(t)
        names = {s["name"] for s in t.list_satellites()}
        assert any("METEOR-M2 2" in n for n in names)

    def test_user_tle_dir_is_loaded(self, isolated):
        """`~/.mbdsdr/tle/` 里的文件与缓存同等对待，文件名不必是 NORAD。"""
        _, user = isolated
        (user / "my-sat.tle").write_text(TLE_EXTRA, encoding="utf-8")
        t = _tracker()
        assert "44387" in _norads(t)

    def test_user_dir_file_with_multiple_satellites(self, isolated):
        _, user = isolated
        (user / "bundle.tle").write_text(TLE_EXTRA + "\n" + TLE_METEOR,
                                        encoding="utf-8")
        t = _tracker()
        norads = _norads(t)
        assert "44387" in norads
        assert "40069" in norads

    def test_bad_file_does_not_break_others(self, isolated):
        cache, user = isolated
        (cache / "44387.tle").write_text(TLE_EXTRA, encoding="utf-8")
        (user / "broken.tle").write_text("这不是 TLE", encoding="utf-8")
        t = _tracker()
        assert "44387" in _norads(t)

    def test_no_duplicate_norad(self, isolated):
        """缓存与用户目录放同一颗，目录里也只该有一条。"""
        cache, user = isolated
        (cache / "44387.tle").write_text(TLE_EXTRA, encoding="utf-8")
        (user / "same.tle").write_text(TLE_EXTRA, encoding="utf-8")
        t = _tracker()
        rows = [s for s in t.list_satellites() if str(s["norad_id"]) == "44387"]
        assert len(rows) == 1

    def test_snapshot_members_survive(self, isolated):
        """补星不能把快照原有的星挤掉（AO-73 / FOX-1B 是真实在轨业余星）。"""
        cache, _ = isolated
        (cache / "40069.tle").write_text(TLE_METEOR, encoding="utf-8")
        t = _tracker()
        norads = _norads(t)
        assert "39444" in norads, "AO-73 被挤掉了"
        assert "43017" in norads, "FOX-1B 被挤掉了"
        assert "25544" in norads, "ISS 被挤掉了"

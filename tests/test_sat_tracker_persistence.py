# SPDX-License-Identifier: MIT
"""SatelliteTracker 的 TLE 落盘与重启读回。

背景（P0 卫星缺口）：``update_tle_from_celestrak`` 过去只改内存，进程退出即丢，
重启后 tracker 又回到内置 2026-09-25 快照——这是「同一时刻 ISS 仰角差 48.9°」
里「数据从哪来」的那一半。

反证口径：
  - 改前：刷新后新建 tracker（等价重启）仍读内置历元 2026-09-25；
  - 改后：刷新落盘，新实例读回刷新写入的历元，且内置快照不被旧缓存顶回。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai import orbit, sat_tracker
from mbdsdr_ai.sat_passes import GroundStation
from mbdsdr_ai.sat_tracker import BUILTIN_TLE_DATE, SatelliteTracker

_GS = GroundStation(lat_deg=39.9, lon_deg=116.4, alt_m=50.0)

# 内置 NOAA 15 的真实 TLE，只把历元列换成 2026-11-11（年内第 315.5 天），
# 便于断言「读回的纪元不是 2026-09-25」。
_NEW_L1 = "1 25338U 98030A   26315.50000000  .00000085  00000+0  52080-4 0  9991"
_NEW_L2 = "2 25338  98.5049 286.7672 0011219  56.3023 303.9227 14.27170971475651"

_BUILTIN_NOAA15_L1 = next(
    b[1] for b in sat_tracker._BUILTIN_TLES if str(b[0]) == "NOAA 15")


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """隔离磁盘缓存目录（与 tle_fetch 共用 orbit._CACHE_DIR 的唯一来源）。"""
    cache = tmp_path / "tle_cache"
    monkeypatch.setattr(orbit, "_CACHE_DIR", str(cache))
    return cache


class _Resp:
    text = f"NOAA 15\n{_NEW_L1}\n{_NEW_L2}\n"

    @staticmethod
    def raise_for_status():
        return None


class _Req:
    @staticmethod
    def get(*a, **k):
        return _Resp()


def _refresh(tracker, monkeypatch) -> int:
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)
    monkeypatch.setitem(sys.modules, "requests", _Req)
    return tracker.update_tle_from_celestrak()


def _noaa15(tracker):
    return next(s for s in tracker.list_satellites() if s["name"] == "NOAA 15")


def test_refresh_persists_and_new_instance_reads_back(monkeypatch, _isolated_cache):
    """刷新成功即写 `<NORAD>.tle`；新实例（等价重启）读到新历元，不回退内置。"""
    t1 = SatelliteTracker(_GS)
    assert t1.tle_date == BUILTIN_TLE_DATE          # 无缓存时仍是内置基线
    assert _refresh(t1, monkeypatch) >= 1
    assert t1.last_persist_error == ""

    path = _isolated_cache / "25338.tle"
    assert path.is_file(), "刷新成功必须落盘"
    assert path.read_text(encoding="utf-8").splitlines() == [
        "NOAA 15", _NEW_L1, _NEW_L2]

    t2 = SatelliteTracker(_GS)                       # 只靠磁盘，不共享内存
    sat = _noaa15(t2)
    assert sat["tle"] == [_NEW_L1, _NEW_L2]
    assert sat["tle_epoch_utc"].startswith("2026-11-11")
    assert sat["tle_epoch_utc"][:10] != BUILTIN_TLE_DATE
    assert t2.tle_date == "2026-11-11"
    assert sat["tle_tier"] in ("fresh", "usable")


def test_older_cache_never_overrides_builtin(_isolated_cache):
    """缓存历元比内置旧时不得采用——否则修完反而把轨道顶回更旧。"""
    _isolated_cache.mkdir(parents=True)
    old_l1 = _NEW_L1[:18] + "26200.00000000" + _NEW_L1[32:]
    (_isolated_cache / "25338.tle").write_text(
        f"NOAA 15\n{old_l1}\n{_NEW_L2}\n", encoding="utf-8")

    t = SatelliteTracker(_GS)
    assert _noaa15(t)["tle"][0] == _BUILTIN_NOAA15_L1
    assert t.tle_date == BUILTIN_TLE_DATE


def test_cache_with_wrong_object_name_not_adopted(_isolated_cache):
    """编号指向别的对象（身份不符）的缓存不得采用：脏缓存不能污染跟踪。"""
    _isolated_cache.mkdir(parents=True)
    (_isolated_cache / "25338.tle").write_text(
        f"ATLAS 5 CENTAUR DEB\n{_NEW_L1}\n{_NEW_L2}\n", encoding="utf-8")

    t = SatelliteTracker(_GS)
    assert _noaa15(t)["tle"][0] == _BUILTIN_NOAA15_L1
    assert t.tle_date == BUILTIN_TLE_DATE


def test_persist_failure_is_visible_not_silent(monkeypatch, _isolated_cache):
    """写盘失败：内存刷新照常生效，但必须留痕，且不留半截临时文件。"""
    t = SatelliteTracker(_GS)
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)
    monkeypatch.setitem(sys.modules, "requests", _Req)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(sat_tracker.os, "replace", _boom)
    assert t.update_tle_from_celestrak() >= 1
    assert "落盘失败" in t.last_persist_error
    assert not (_isolated_cache / "25338.tle").exists()
    assert list(_isolated_cache.glob("*.tmp.*")) == []

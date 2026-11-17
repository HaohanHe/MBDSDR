# SPDX-License-Identifier: MIT
"""TLE 来历诚实化（D4 / Task 18）：来源、epoch、抓取时间、陈旧天数。

口径：
  - ``epoch_utc`` 是 TLE 行自带历元（轨道数据的时刻）；
  - ``fetched_utc`` 是抓取/落盘时刻（与历元不是一回事）；
  - ``age_days`` 是历元距今；``stale`` 只表示「按刷新门槛该重拉了」；
  - 离线且无缓存必须给出「无 TLE，无法预报」，不得返回空列表冒充
    「今天没有过境」。

全部用注入的假时钟/假 opener/tmp 缓存目录，不联网。
"""
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.tle_epoch import parse_tle_epoch  # noqa: E402
from mbdsdr_ai.tle_fetch import (  # noqa: E402
    NO_TLE_TEXT,
    FetchPolicy,
    TleFetcher,
    tle_provenance,
)

CAT = 33591
NAME = "NOAA 19"
LINE1 = "1 33591U 09005A   26268.85039999  .00000028  00000+0  38838-4 0  9994"
LINE2 = "2 33591  98.9431 339.7518 0014811 104.9296 255.3517 14.13486977908701"
GOOD_BODY = f"{NAME}\n{LINE1}\n{LINE2}\n"
EPOCH_UNIX = parse_tle_epoch(LINE1).timestamp()


class ScriptedOpener:
    """只允许返回脚本响应；离线用例断言它一次都没被调用。"""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, timeout_s, deadline_s):
        self.calls.append(url)
        if not self.script:
            raise AssertionError("本用例不应联网")
        return self.script.pop(0)


def make_fetcher(tmp_path, opener=None, *, offline=False, clock_now=None,
                 policy=None):
    opener = opener or ScriptedOpener()
    now = time.time() if clock_now is None else float(clock_now)
    fetcher = TleFetcher(
        opener=opener,
        cache_dir_provider=lambda: str(tmp_path),
        memory_provider=lambda: {},
        offline_provider=lambda: offline,
        policy=policy or FetchPolicy(min_interval_s=0.0),
        clock=lambda: now,
        sleep=lambda _s: None,
        error_cls=RuntimeError,
    )
    return fetcher, opener


def write_cache(tmp_path, mtime, catnr=CAT):
    path = os.path.join(str(tmp_path), f"{catnr}.tle")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(GOOD_BODY)
    os.utime(path, (mtime, mtime))
    return path


def test_offline_without_cache_is_explicitly_unavailable(tmp_path):
    """无网络 + 无缓存：抛错并写明「无 TLE，无法预报」，不编造空结果。"""
    now = time.time()
    fetcher, opener = make_fetcher(tmp_path, offline=True, clock_now=now)

    with pytest.raises(RuntimeError) as ei:
        fetcher.fetch_record(CAT)

    msg = str(ei.value)
    assert NO_TLE_TEXT in msg, msg
    assert "TLE 缓存" in msg and "离线" in msg
    assert opener.calls == [], "离线模式不得联网"
    assert getattr(ei.value, "failure", {}).get("code") == "offline"
    assert list(tmp_path.iterdir()) == [], "无缓存时不得凭空落盘 TLE"


def test_fresh_cache_exposes_source_epoch_fetch_time_and_age(tmp_path):
    """有缓存：来源 / epoch / 抓取时间 / 年龄一次给全。"""
    now = EPOCH_UNIX + 3600.0          # 历元后 1 小时
    write_cache(tmp_path, mtime=now)
    fetcher, opener = make_fetcher(tmp_path, clock_now=now)

    rec = fetcher.fetch_record(CAT)

    assert opener.calls == []
    assert rec["upstream_source"] == "celestrak"   # 数据上游
    # 本次从哪读到（cache/memory/network）在 provenance.cache_source
    assert rec["source"] == "cache"
    assert rec["provenance"]["cache_source"] == "cache"
    assert rec["epoch_utc"] == "2026-09-25T20:24:34Z"
    assert rec["fetched_utc"] is not None
    assert rec["age_days"] == pytest.approx(1 / 24, abs=1e-3)
    assert rec["stale"] is False
    assert rec["stale_threshold_days"] == pytest.approx(1.0)
    # 同一份事实也在嵌套 provenance 里（便于上层整块透传）
    prov = rec["provenance"]
    assert prov["upstream_source"] == "celestrak"
    assert prov["epoch_utc"] == rec["epoch_utc"]
    assert prov["fetched_at"] == pytest.approx(now)
    assert prov["age_days"] == rec["age_days"]


def test_staleness_threshold_marks_old_tle(tmp_path):
    """阈值判断：历元 25 小时前（>24h 刷新门槛）→ stale=True。"""
    now = EPOCH_UNIX + 25 * 3600.0
    write_cache(tmp_path, mtime=now)
    fetcher, _ = make_fetcher(tmp_path, clock_now=now)

    rec = fetcher.fetch_record(CAT)

    assert rec["age_days"] == pytest.approx(25 / 24, abs=1e-3)
    assert rec["stale"] is True
    assert rec["provenance"]["stale"] is True

    # 21 小时：仍不算陈旧（门槛两侧各取一点，防止阈值写反）
    now2 = EPOCH_UNIX + 21 * 3600.0
    write_cache(tmp_path, mtime=now2)
    fetcher2, _ = make_fetcher(tmp_path, clock_now=now2)
    assert fetcher2.fetch_record(CAT)["stale"] is False


def test_offline_stale_cache_keeps_provenance(tmp_path):
    """离线沿用旧缓存：仍然标明来源/年龄，不冒充刚刷新。"""
    now = EPOCH_UNIX + 40 * 86400.0
    # 缓存本身也过期（30h > 24h 刷新门槛），才会走到「离线沿用旧值」分支
    write_cache(tmp_path, mtime=now - 30 * 3600.0)
    fetcher, opener = make_fetcher(tmp_path, offline=True, clock_now=now)

    rec = fetcher.fetch_record(CAT)

    assert opener.calls == []
    assert rec["refresh_status"] == "not_updated"
    assert rec["offline"] is True
    assert rec["age_days"] == pytest.approx(40.0, abs=1e-3)
    assert rec["stale"] is True
    assert rec["epoch_utc"].startswith("2026-09-25")


def test_provenance_without_tle_is_unknown_not_fabricated():
    """没有 line1 时四项元信息是 None/False，不拿抓取时间冒充历元。"""
    prov = tle_provenance({"source": "cache", "fetched_at": 123.0},
                          now=456.0)
    assert prov["epoch_utc"] is None
    assert prov["age_days"] is None
    assert prov["stale"] is False
    assert prov["fetched_utc"] is not None
    assert prov["upstream_source"] == "celestrak"


def test_every_builtin_tle_entry_has_declared_source():
    """内置 TLE 不得是「无来源」数据：逐条登记来源与提供方式。

    生产 ``decoders.BUILTIN_TLE`` 已清空（不再内置合成/凑整值）；仍保留的
    ``sat_tracker._BUILTIN_TLES`` 必须标 ``external_snapshot`` 并带来源，
    不得冒充在线实时。测试样本另放 ``tests/fixtures/tle_samples.py``。
    """
    from mbdsdr_ai.builtin_tle_meta import builtin_provenance_table

    table = builtin_provenance_table(include_real=True)
    assert table, "应能读到内置 TLE 登记表"
    for name, prov in table.items():
        assert prov.get("source"), f"{name} 缺来源登记"
        assert prov.get("provided_by"), f"{name} 缺提供方式登记"
        assert prov.get("origin_kind") in ("synthetic", "external_snapshot"), \
            f"{name} 的来历类别未登记"

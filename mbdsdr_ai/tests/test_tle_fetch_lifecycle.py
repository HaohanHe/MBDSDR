# SPDX-License-Identifier: MIT
"""P12 TLE 数据生命周期回归：调度阈值、去重、退避、离线、健康度。

全部用注入的假 HTTP / 假时钟，无网络也能跑。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai import tle_fetch  # noqa: E402
from mbdsdr_ai.tle_fetch import FetchPolicy, TleFetcher  # noqa: E402

CAT = 33591
NAME = "NOAA 19"
LINE1 = "1 33591U 09005A   26268.85039999  .00000028  00000+0  38838-4 0  9994"
LINE2 = "2 33591  98.9431 339.7518 0014811 104.9296 255.3517 14.13486977908701"
GOOD_BODY = f"{NAME}\n{LINE1}\n{LINE2}\n"


class HTTPError(Exception):
    def __init__(self, code, headers=None):
        super().__init__(f"HTTP {code}")
        self.code = code
        self.headers = headers or {}


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(float(s), 3))
        self.t += float(s)


class ScriptedOpener:
    """按脚本返回响应体或抛异常；记录每次调用（含 URL）。"""

    def __init__(self, script):
        self.script = list(script)
        self.last = None
        self.calls = []

    def __call__(self, url, timeout_s, deadline_s):
        self.calls.append(url)
        item = self.script.pop(0) if self.script else self.last
        self.last = item
        if isinstance(item, Exception):
            raise item
        return item


def make_fetcher(tmp_path, opener, *, memory=None, offline=None,
                 policy=None, clock=None):
    clock = clock or FakeClock(__import__("time").time())
    memory = {} if memory is None else memory
    f = TleFetcher(
        opener=opener,
        cache_dir_provider=lambda: str(tmp_path),
        memory_provider=lambda: memory,
        offline_provider=(offline or (lambda: False)),
        policy=policy or FetchPolicy(min_interval_s=0.0),
        clock=clock,
        sleep=clock.sleep,
        error_cls=RuntimeError,
    )
    return f, clock, memory


def write_cache(tmp_path, age_hours, catnr=CAT):
    path = os.path.join(str(tmp_path), f"{catnr}.tle")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(GOOD_BODY)
    import time
    ts = time.time() - age_hours * 3600.0
    os.utime(path, (ts, ts))


# --------------------------------------------------------------- 成功与缓存
def test_network_success_writes_cache_and_health(tmp_path):
    opener = ScriptedOpener([GOOD_BODY])
    f, clock, memory = make_fetcher(tmp_path, opener)

    rec = f.fetch_record(CAT)
    assert rec["source"] == "celestrak"
    assert rec["refresh_status"] == "network"
    assert (rec["line1"], rec["line2"]) == (LINE1, LINE2)
    assert os.path.isfile(rec["cache_path"])
    assert memory[str(CAT)][1] == (LINE1, LINE2)
    h = rec["health"]
    assert h["state"] == "healthy" and h["total_successes"] == 1
    assert h["last_success_utc"].endswith("Z")

    rec2 = f.fetch_record(CAT)
    assert rec2["source"] == "memory"
    assert len(opener.calls) == 1          # 内存命中不再打网络


def test_fresh_disk_cache_skips_network(tmp_path):
    write_cache(tmp_path, age_hours=1.0)
    opener = ScriptedOpener([GOOD_BODY])
    f, _, _ = make_fetcher(tmp_path, opener)
    rec = f.fetch_record(CAT)
    assert rec["refresh_status"] == "cache"
    assert opener.calls == []


def test_newer_disk_beats_stale_memory(tmp_path):
    """执行侧刷新落盘后，另一侧的内存快照必须让位。

    内存命中只比「自己有多新」，不比「磁盘有没有更新的」——不比对就会把
    旧根数冒充最新值，最长拖到 max_age（默认 24h），且没有任何失效广播。
    """
    import time

    newer_l1 = LINE1.replace("26268.85039999", "26315.50000000")
    memory = {str(CAT): (time.time() - 3600.0, (LINE1, LINE2))}
    path = os.path.join(str(tmp_path), f"{CAT}.tle")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"{NAME}\n{newer_l1}\n{LINE2}\n")
    os.utime(path, None)      # 磁盘写入时刻晚于内存快照

    opener = ScriptedOpener([GOOD_BODY])
    f, _, _ = make_fetcher(tmp_path, opener, memory=memory)
    rec = f.fetch_record(CAT)
    assert rec["line1"] == newer_l1, "内存旧快照压过了新落盘的 TLE"
    assert rec["source"] == "cache"
    assert memory[str(CAT)][1][0] == newer_l1, "内存应被纠正为新值"
    assert opener.calls == [], "磁盘已够新，不该再打网络"


# --------------------------------------------------------------- 失败与退避
def test_timeout_uses_stale_cache_not_updated(tmp_path):
    write_cache(tmp_path, age_hours=30.0)
    f, _, _ = make_fetcher(tmp_path, ScriptedOpener([TimeoutError("timed out")]))
    rec = f.fetch_record(CAT)
    assert rec["refresh_status"] == "not_updated"
    assert rec["failure_kind"] == "network_timeout"
    assert rec["source"] == "cache"
    assert rec["health"]["consecutive_failures"] == 1
    assert rec["health"]["retry_in_s"] >= 59.0
    assert rec["offline"] is False
    assert rec["offline_label"] == "缓存回退（未刷新）"


def test_unreachable_network_labels_offline_cache(tmp_path):
    import urllib.error
    write_cache(tmp_path, age_hours=30.0)
    f, _, _ = make_fetcher(
        tmp_path, ScriptedOpener([urllib.error.URLError("dns failed")]))
    rec = f.fetch_record(CAT)
    assert rec["failure_kind"] == "network_unreachable"
    assert rec["offline"] is True
    assert rec["offline_label"] == "离线·缓存（未刷新）"


def test_no_cache_failure_raises_honest_error(tmp_path):
    f, _, _ = make_fetcher(tmp_path, ScriptedOpener([TimeoutError("x")]))
    with pytest.raises(RuntimeError, match="无缓存"):
        f.fetch_record(CAT)


def test_429_sets_backoff_and_no_repeat_request(tmp_path):
    write_cache(tmp_path, age_hours=30.0)
    opener = ScriptedOpener([HTTPError(429, {"Retry-After": "600"})])
    f, clock, _ = make_fetcher(tmp_path, opener)

    rec = f.fetch_record(CAT)
    assert rec["failure_kind"] == "rate_limited"
    assert rec["health"]["retry_in_s"] == 600.0
    rec2 = f.fetch_record(CAT)             # 退避期内：直接读缓存，不再请求
    assert rec2["refresh_status"] == "not_updated"
    assert "退避中" in rec2["refresh_error"]
    assert len(opener.calls) == 1


def test_5xx_exponential_backoff_grows_and_caps(tmp_path):
    policy = FetchPolicy(min_interval_s=0.0, backoff_base_s=60.0,
                         backoff_max_s=3600.0)
    opener = ScriptedOpener([HTTPError(503)] * 6)
    f, clock, _ = make_fetcher(tmp_path, opener, policy=policy)
    waits = []
    for _ in range(6):
        try:
            f.fetch_record(CAT)
        except RuntimeError:
            pass
        h = f.health_report(clock())
        waits.append(h["retry_in_s"])
        clock.t += h["retry_in_s"] + 1.0   # 越过退避，制造下一次尝试
    assert waits[0] >= 59.0
    assert waits[1] > waits[0]
    assert max(waits) <= 3600.0
    assert all(w <= 3600.0 for w in waits)


def test_bad_and_empty_payload_classified(tmp_path):
    f1, _, _ = make_fetcher(tmp_path, ScriptedOpener(["not a tle\n"]))
    with pytest.raises(RuntimeError):
        f1.fetch_record(CAT)
    assert f1.health.last_error_kind == "parse_failure"

    f2, _, _ = make_fetcher(tmp_path, ScriptedOpener([""]))
    with pytest.raises(RuntimeError):
        f2.fetch_record(CAT)
    assert f2.health.last_error_kind == "empty_response"


def test_retry_after_honoured_and_capped(tmp_path):
    policy = FetchPolicy(min_interval_s=0.0, retry_after_max_s=120.0)
    opener = ScriptedOpener([HTTPError(429, {"Retry-After": "999"})])
    f, _, _ = make_fetcher(tmp_path, opener, policy=policy)
    try:
        f.fetch_record(CAT)
    except RuntimeError:
        pass
    assert f.health_report()["retry_in_s"] == 120.0


# ---------------------------------------------------------------- 离线降级
def test_offline_fresh_cache_labels_offline(tmp_path):
    write_cache(tmp_path, age_hours=1.0)
    opener = ScriptedOpener([GOOD_BODY])
    f, _, _ = make_fetcher(tmp_path, opener, offline=lambda: True)
    rec = f.fetch_record(CAT)
    assert rec["offline"] is True
    assert rec["offline_label"] == "离线·缓存"
    assert rec["source"] == "cache"
    assert opener.calls == []
    assert rec["health"]["state"] == "offline"


def test_offline_stale_cache_not_updated(tmp_path):
    write_cache(tmp_path, age_hours=30.0)
    opener = ScriptedOpener([GOOD_BODY])
    f, _, _ = make_fetcher(tmp_path, opener, offline=lambda: True)
    rec = f.fetch_record(CAT)
    assert rec["refresh_status"] == "not_updated"
    assert rec["offline_label"] == "离线·缓存（未刷新）"
    assert "离线" in rec["refresh_error"]
    assert opener.calls == []


def test_offline_without_cache_raises(tmp_path):
    f, _, _ = make_fetcher(tmp_path, ScriptedOpener([GOOD_BODY]),
                           offline=lambda: True)
    with pytest.raises(RuntimeError, match="离线"):
        f.fetch_record(CAT)


# --------------------------------------------------------------- 调度与去重
def test_refresh_plan_threshold_and_dedup(tmp_path):
    import time
    now = time.time()
    f, clock, _ = make_fetcher(tmp_path, ScriptedOpener([]))
    entries = [
        {"catnr": CAT, "fetched_at": now - 1 * 3600.0},       # 新鲜
        {"catnr": 25338, "fetched_at": now - 30 * 3600.0},    # 过期
        {"catnr": 25338, "fetched_at": now - 30 * 3600.0},    # 重复
        {"catnr": 28654, "fetched_at": None},                 # 无缓存
    ]
    plan = f.refresh_plan(entries, reason="startup", now=now)
    assert plan["due"] == [25338, 28654]
    assert [s["why"] for s in plan["skipped"]] == ["fresh"]
    assert plan["deduped_total"] == 3


def test_refresh_round_requests_each_object_once(tmp_path):
    opener = ScriptedOpener([GOOD_BODY, GOOD_BODY.replace("33591", "25338")])
    f, _, _ = make_fetcher(tmp_path, opener)
    entries = [{"catnr": CAT, "fetched_at": None},
               {"catnr": 25338, "fetched_at": None},
               {"catnr": CAT, "fetched_at": None}]
    out = f.refresh_round(entries, reason="startup")
    assert len(opener.calls) == 2
    assert sorted(int(k) for k in out["results"]) == [25338, CAT]


def test_refresh_plan_skips_entries_in_backoff(tmp_path):
    opener = ScriptedOpener([TimeoutError("x")])
    f, clock, _ = make_fetcher(tmp_path, opener)
    try:
        f.fetch_record(CAT)
    except RuntimeError:
        pass
    assert f.health.consecutive_failures == 1
    plan = f.refresh_plan([{"catnr": CAT, "fetched_at": None}],
                          now=clock())
    assert plan["due"] == []
    assert plan["skipped"][0]["why"] in ("object_backoff", "source_backoff")


def test_min_interval_spacing_between_objects(tmp_path):
    opener = ScriptedOpener([GOOD_BODY, GOOD_BODY.replace("33591", "25338")])
    policy = FetchPolicy(min_interval_s=1.0)
    f, clock, _ = make_fetcher(tmp_path, opener, policy=policy)
    f.fetch_record(CAT)
    f.fetch_record(25338)
    assert len(opener.calls) == 2
    assert clock.sleeps == [1.0]           # 第二个对象等满最小间隔


# ---------------------------------------------------------------- 健康度
def test_health_report_fields(tmp_path):
    f, _, _ = make_fetcher(tmp_path, ScriptedOpener([GOOD_BODY]))
    h0 = f.health_report()
    assert h0["state"] == "unknown"
    f.fetch_record(CAT)
    h1 = f.health_report()
    for key in ("source", "state", "last_success_utc", "consecutive_failures",
                "total_failures", "total_successes", "last_error",
                "backoff_until_utc", "retry_in_s", "policy"):
        assert key in h1
    assert h1["state"] == "healthy"
    assert h1["policy"]["fresh_max_hours"] == 24.0


def test_failure_kinds_labels():
    assert tle_fetch.classify_failure(status=429)["kind"] == "rate_limited"
    assert tle_fetch.classify_failure(status=404)["kind"] == "http_4xx"
    assert tle_fetch.classify_failure(status=500)["kind"] == "http_5xx"
    assert tle_fetch.classify_failure(exc=TimeoutError())["kind"] == "network_timeout"
    assert tle_fetch.classify_failure(body="")["kind"] == "empty_response"
    assert tle_fetch.classify_failure(body="junk")["kind"] == "parse_failure"
    assert tle_fetch.classify_failure(status=404)["retryable"] is False


# ------------------------------------------------------- 内置合成 TLE 策略
def test_synthetic_tle_policy_is_explicit():
    from mbdsdr_ai.builtin_tle_meta import synthetic_tle_policy

    p = synthetic_tle_policy()
    assert p["decision"] == "keep_but_not_for_precise_passes"
    assert p["enforced_by"]
    assert "精确" in p["summary"]


# ------------------------------------------------------------ orbit 集成
def test_tle_manager_reuses_lifecycle_and_labels_offline(tmp_path, monkeypatch):
    from mbdsdr_ai.new_spacetime_tle import TLEManager

    write_cache(tmp_path, age_hours=1.0)
    mgr = TLEManager(cache_dir=str(tmp_path))
    calls = []
    monkeypatch.setattr(mgr, "_http_get", lambda url: calls.append(url) or GOOD_BODY)
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")

    ent = mgr.fetch(CAT)
    assert ent is not None
    assert ent.offline is True
    assert ent.offline_label == "离线·缓存"
    assert ent.refresh_status == "cache"
    assert calls == []
    assert mgr.source_status()["state"] == "offline"


# --------------------------------------------- 群组拉取（缺口①：限速/退避/健康度）
GROUP_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle"
NOAA18_L1 = "1 28654U 05018A   26268.90422616  .00000041  00000+0  45056-4 0  9991"
NOAA18_L2 = "2 28654  98.8049 347.0872 0013570 221.3821 138.6324 14.13744708100547"
GROUP_BODY = f"{NAME}\n{LINE1}\n{LINE2}\nNOAA 18\n{NOAA18_L1}\n{NOAA18_L2}\n"


def test_group_fetch_parses_multiple_and_dedupes(tmp_path):
    body = GROUP_BODY + f"NOAA 19 (dup)\n{LINE1}\n{LINE2}\n"
    opener = ScriptedOpener([body])
    f, _, _ = make_fetcher(tmp_path, opener)
    res = f.fetch_group(GROUP_URL)
    assert res["refresh_status"] == "network"
    assert sorted(e["catnr"] for e in res["entries"]) == [28654, 33591]
    assert len(opener.calls) == 1
    assert res["health"]["state"] == "healthy"


def test_group_fetch_honors_min_interval(tmp_path):
    opener = ScriptedOpener([GROUP_BODY, GROUP_BODY])
    f, clock, _ = make_fetcher(tmp_path, opener,
                               policy=FetchPolicy(min_interval_s=1.0))
    f.fetch_group(GROUP_URL)
    f.fetch_group(GROUP_URL)
    # 同源最小间隔：第二次请求前必须等满 1s，而不是立刻打点
    assert clock.sleeps == [1.0]
    assert len(opener.calls) == 2


def test_group_failure_exponential_backoff_blocks_retry(tmp_path):
    opener = ScriptedOpener([TimeoutError("dns blackhole"), GROUP_BODY])
    f, clock, _ = make_fetcher(tmp_path, opener,
                               policy=FetchPolicy(min_interval_s=0.0))
    res1 = f.fetch_group(GROUP_URL)
    assert res1["refresh_status"] == "unavailable"
    assert res1["failure_kind"] == "network_timeout"
    assert res1["retry_in_s"] == 60.0            # backoff_base 起步
    assert res1["health"]["state"] == "backoff"

    res2 = f.fetch_group(GROUP_URL)              # 退避期内
    assert res2["refresh_status"] == "unavailable"
    assert "退避中" in res2["refresh_error"]
    assert res2["retry_in_s"] > 0
    assert len(opener.calls) == 1                # 绝不在退避期重试/刷屏


def test_group_rate_limit_uses_retry_after(tmp_path):
    opener = ScriptedOpener([HTTPError(429, {"Retry-After": "120"})])
    f, _, _ = make_fetcher(tmp_path, opener)
    res = f.fetch_group(GROUP_URL)
    assert res["failure_kind"] == "rate_limited"
    assert res["retry_in_s"] == 120.0
    assert res["health"]["consecutive_failures"] == 1


def test_group_empty_and_bad_body_not_success(tmp_path):
    f, _, _ = make_fetcher(tmp_path, ScriptedOpener(["   \n\n"]))
    res = f.fetch_group(GROUP_URL)
    assert res["refresh_status"] == "unavailable"
    assert res["failure_kind"] == "empty_response"
    assert res["entries"] == []


def test_group_offline_refuses_explicitly(tmp_path):
    opener = ScriptedOpener([GROUP_BODY])
    f, _, _ = make_fetcher(tmp_path, opener, offline=lambda: True)
    res = f.fetch_group(GROUP_URL)
    assert res["refresh_status"] == "offline" and res["offline"] is True
    assert "离线模式" in res["refresh_error"]
    assert "无本地缓存" in res["refresh_error"]
    assert opener.calls == []                    # 离线不联网
    assert res["health"]["state"] == "offline"


def test_orbit_status_and_offline_label(tmp_path, monkeypatch):
    from mbdsdr_ai import orbit

    write_cache(tmp_path, age_hours=1.0)
    monkeypatch.setattr(orbit, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(orbit, "_TLE_CACHE", {})
    monkeypatch.setattr(orbit, "_TLE_FETCHER", None)
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")

    rec = orbit.fetch_tle_record(CAT)
    assert rec["refresh_status"] == "cache"
    assert rec["offline"] is True and rec["offline_label"] == "离线·缓存"
    st = orbit.tle_source_status()
    assert st["state"] == "offline"
    assert "policy" in st

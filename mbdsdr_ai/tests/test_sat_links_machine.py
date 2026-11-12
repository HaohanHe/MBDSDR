# SPDX-License-Identifier: MIT
"""W92 卫星发射机机器数据源：拉取/缓存/离线回退/不覆盖人工值。

全部用注入的假 HTTP 与临时缓存目录，无网络也能跑；真正的联网拉取只在
对照报告里做一次，并保留原始快照。
"""
import json
import os
import time

import pytest

from mbdsdr_ai import sat_links as SL
from mbdsdr_ai import sat_links_fetch as SF


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MBDSDR_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)
    SF.clear_cache_memo()
    yield
    SF.clear_cache_memo()


def tx(uuid, low, high=None, mode="APT", baud=None,
       status="active", service="Unknown", **extra):
    item = {"uuid": uuid, "downlink_low": low, "downlink_high": high,
            "mode": mode, "baud": baud, "status": status,
            "service": service, "sat_id": "TEST-SAT",
            "updated": "2026-01-01T00:00:00Z", "description": mode}
    item.update(extra)
    return item


def opener_map(mapping):
    def _open(url, timeout_s, deadline_s):
        for norad, body in mapping.items():
            if f"norad_cat_id={norad}" in url:
                if isinstance(body, BaseException):
                    raise body
                return body if isinstance(body, (str, bytes)) else json.dumps(body)
        raise AssertionError(f"未预期的 URL：{url}")

    return _open


def target(norad, key="noaa_15"):
    return [{"norad_cat_id": norad, "key": key}]


def cache_file():
    return SF.cache_path()


def read_cache():
    with open(cache_file(), "r", encoding="utf-8") as fh:
        return json.load(fh)


# ------------------------------------------------------------- 人工目录兜底
def test_no_cache_keeps_manual_catalog_intact():
    assert not os.path.exists(cache_file())
    assert SL.machine_status_text() == \
        "SatNOGS 发射机库：未取到（无本地缓存），沿用人工目录"
    assert len(SL.list_link_sats()) == 8
    entry = SL.load_sat_link("noaa_15")
    assert entry["links"][0]["freq_hz"] == 137620000.0
    assert entry["links"][0]["mode"] == "apt"
    assert entry["_machine"]["state"] == "absent"
    assert SL.machine_entry_text(entry) == "未取到（无机器缓存）"
    hits = SL.identify_by_freq(137.1e6, tolerance_hz=3000.0)
    assert {"noaa_19", "meteor_m_n2"} <= {h["_link_name"] for h in hits}


def test_machine_targets_cover_all_eight_manual_sats():
    targets = SL.machine_targets()
    assert len(targets) == 8
    assert [t["key"] for t in targets] == SL.list_link_sats()
    for t in targets:
        assert isinstance(t["norad_cat_id"], int) and t["norad_cat_id"] > 0


# --------------------------------------------------------------- 拉取与合并
def test_refresh_writes_cache_and_merges_optional_fields():
    body = [tx("u-apt", 137620000, 137620000, "APT", None,
               "inactive", "Earth Observation")]
    res = SF.refresh(target(25338), opener=opener_map({25338: body}))
    assert res["state"] == "ok" and res["written"] is True
    assert os.path.exists(cache_file())

    payload = read_cache()
    assert payload["_schema"] == SF.CACHE_SCHEMA
    assert payload["satellites"]["25338"]["state"] == "ok"

    entry = SL.load_sat_link("noaa_15")
    link = entry["links"][0]
    # 人工值一字节不动
    assert link["freq_hz"] == 137620000.0
    assert link["mode"] == "apt"
    assert link["note"].startswith("APT 模拟图像下传")
    # 机器字段作为可选增量
    assert link["low_hz"] == 137620000.0
    assert link["high_hz"] == 137620000.0
    assert link["status"] == "inactive"
    assert link["service"] == "Earth Observation"
    assert link["source"] == "satnogs"
    assert entry["_machine"]["matched_links"] == 1
    assert "SatNOGS" in SL.machine_entry_text(entry)
    assert SL.machine_status_text().startswith("SatNOGS 发射机库：机器缓存")


def test_machine_never_overwrites_manual_freq_and_records_conflict():
    body = [tx("u-drift", 137621000, 137621000)]
    SF.refresh(target(25338), opener=opener_map({25338: body}))
    entry = SL.load_sat_link("noaa_15")
    link = entry["links"][0]
    assert link["freq_hz"] == 137620000.0          # 人工值优先
    assert link["low_hz"] == 137621000.0           # 机器值只进可选字段
    conflicts = entry["_machine"]["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["delta_hz"] == 1000.0


def test_identify_uses_machine_bandwidth_on_catalog_link():
    # 无缓存时 145.9385 超出 145.935±1000 的容差 → 不命中
    assert SL.identify_by_freq(145.9385e6, tolerance_hz=1000.0) == []
    # 机器库把这条人工信标标成一段区间（模拟转发器带宽）
    body = [tx("u-beacon", 145930000, 145940000, "BPSK", 1200)]
    SF.refresh(target(39444, "funcube_1"), opener=opener_map({39444: body}))

    # 有机器区间后按 [145.930, 145.940] 判定 → 命中且标 satnogs
    hits = SL.identify_by_freq(145.9385e6, tolerance_hz=1000.0)
    assert len(hits) == 1
    assert hits[0]["_link_name"] == "funcube_1"
    assert hits[0]["_matched_source"] == "satnogs"
    assert hits[0]["_matched_link"]["low_hz"] == 145930000.0
    assert hits[0]["_matched_link"]["high_hz"] == 145940000.0
    # 人工频点本身未变
    assert hits[0]["_matched_link"]["freq_hz"] == 145935000.0


def test_machine_only_links_exposed_but_not_injected_into_identify():
    body = [
        tx("u-beacon", 145935000, 145935000, "BPSK", 1200),
        tx("u-linear", 145950000, 145970000, "LSB", None),
    ]
    SF.refresh(target(39444, "funcube_1"), opener=opener_map({39444: body}))

    only = SL.machine_only_links("funcube_1")
    assert len(only) == 1
    assert only[0]["low_hz"] == 145950000.0
    assert only[0]["high_hz"] == 145970000.0
    assert only[0]["uuid"] == "u-linear"
    # 不自动注入 identify，避免机器库独有条目变成频谱反查噪音
    assert SL.identify_by_freq(145.965e6, tolerance_hz=1000.0) == []


def test_same_freq_prefers_matching_mode_over_active_status():
    # NOAA-18 同频两条：FSK「IQ Recording」active，APT「Downlink」inactive。
    # 人工 link 是 apt，必须配 APT 那条，不能因为 active 配成 FSK。
    body = [
        tx("u-iq", 137912500, 137912500, "FSK", 400, "active"),
        tx("u-apt", 137912500, 137912500, "APT", None, "inactive"),
    ]
    SF.refresh(target(28654, "noaa_18"), opener=opener_map({28654: body}))
    link = SL.load_sat_link("noaa_18")["links"][0]
    assert link["machine"]["uuid"] == "u-apt"
    assert link["machine"]["mode"] == "APT"
    assert link["status"] == "inactive"
    assert link["freq_hz"] == 137912500.0


# ----------------------------------------------------------- 失败与诚实回退
def test_offline_never_contacts_network_and_keeps_manual():
    os.environ["MBDSDR_OFFLINE"] = "1"

    def _boom(*_a, **_k):
        raise AssertionError("离线模式不应发起网络请求")

    res = SL.refresh_machine_links(opener=_boom)
    assert res["state"] == "offline"
    assert res["written"] is False
    assert not os.path.exists(cache_file())
    assert "离线模式" in SL.machine_status_text()
    assert SL.load_sat_link("noaa_18")["links"][0]["freq_hz"] == 137912500.0


def test_all_failures_keep_old_cache_untouched():
    good = [tx("u-apt", 137620000, 137620000)]
    assert SF.refresh(target(25338),
                      opener=opener_map({25338: good}))["written"]
    before = read_cache()

    from urllib.error import HTTPError
    failed = SF.refresh(
        target(25338),
        opener=opener_map({25338: HTTPError("u", 503, "boom", {}, None)}))
    assert failed["state"] == "failed"
    assert failed["written"] is False
    assert failed["failures"]["25338"]["kind"] == "http_5xx"
    assert read_cache() == before                  # 旧缓存原样保留
    entry = SL.load_sat_link("noaa_15")
    assert entry["links"][0]["freq_hz"] == 137620000.0
    assert entry["_machine"]["transmitter_count"] == 1


def test_partial_failure_keeps_refreshed_and_old_sats():
    two = target(25338) + target(28654, "noaa_18")
    assert SF.refresh(two, opener=opener_map({
        25338: [tx("u1", 137620000)],
        28654: [tx("u2", 137912500)]}))["written"]
    from urllib.error import HTTPError
    res = SF.refresh(two, opener=opener_map({
        25338: [tx("u1b", 137620000, status="active")],
        28654: HTTPError("u", 500, "boom", {}, None),
    }))
    assert res["state"] == "partial"
    data = read_cache()
    assert data["satellites"]["25338"]["transmitters"][0]["uuid"] == "u1b"
    assert data["satellites"]["28654"]["transmitters"][0]["uuid"] == "u2"
    assert data["satellites"]["28654"]["last_refresh_failure"]["kind"] == "http_5xx"


def test_invalid_cache_schema_is_reported_and_does_not_break_catalog():
    os.makedirs(os.path.dirname(cache_file()), exist_ok=True)
    with open(cache_file(), "w", encoding="utf-8") as fh:
        json.dump({"_schema": "someone-elses-schema", "satellites": {}}, fh)
    SF.clear_cache_memo()

    payload, state = SF.load_cache()
    assert payload is None and state["state"] == "invalid"
    text = SL.machine_status_text()
    assert "缓存结构不符" in text and "沿用人工目录" in text
    entry = SL.load_sat_link("iss_zarya")
    assert len(entry["links"]) == 2


def test_schema_mismatch_response_is_honest_and_keeps_manual():
    res = SF.refresh(
        target(25338),
        opener=opener_map({25338: {"results": [tx("u", 137620000)]}}))
    assert res["state"] == "failed"
    assert res["failures"]["25338"]["kind"] == "schema_mismatch"
    assert not os.path.exists(cache_file())
    assert "未取到" in SL.machine_status_text()


def test_parse_failure_and_empty_transmitter_list_are_distinguished():
    res = SF.refresh(
        target(25338) + target(28654, "noaa_18"),
        opener=opener_map({25338: "not-json{", 28654: []}))
    assert res["failures"]["25338"]["kind"] == "parse_failure"
    assert res["satellites"]["28654"]["state"] == "empty"
    assert res["written"] is True
    assert "该星无发射机记录" in SL.machine_entry_text(
        SL.load_sat_link("noaa_18"))


# --------------------------------------------------------------- 后台不阻塞
def test_background_refresh_returns_immediately_and_is_daemon():
    def _slow(url, timeout_s, deadline_s):
        time.sleep(0.4)
        return json.dumps([tx("u", 137620000)])

    t0 = time.monotonic()
    thread = SF.start_background_refresh(
        [{"norad_cat_id": 25338, "key": "noaa_15"}], opener=_slow)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"后台刷新阻塞了调用方 {elapsed:.3f}s"
    assert thread is not None and thread.daemon
    assert SF.refresh_in_progress() is True
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert SF.last_refresh_result()["state"] == "ok"
    assert SL.machine_status_text().startswith("SatNOGS 发射机库：机器缓存")

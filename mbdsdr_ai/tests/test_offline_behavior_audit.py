# SPDX-License-Identifier: MIT
"""MBDSDR_OFFLINE 全链路审计（P12 收口）。

强制离线时，每个联网功能必须二选一：

* **A 用本地数据并标注时效/来源**（缓存、内置 TLE、内置模型列表、本地星历），或
* **B 明确拒绝并说明缺什么**（错误信封里写清离线 + 缺的数据）。

禁止第三种：静默返回空、假装成功、无限重试/无截止阻塞。本文件逐条覆盖
``docs/offline-behavior.md`` 的清单，网络调用桩一律「被调用就失败」，
因此有网/无网都能在 CI 跑。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from mbdsdr_ai.new_spacetime import NTPError, get_ntp_time
from mbdsdr_ai.new_spacetime_tle import TLEManager
from mbdsdr_ai.sat_tracker import BUILTIN_TLE_DATE, SatelliteTracker
from mbdsdr_ai.sat_passes import GroundStation

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   26268.43198945  .00011731  00000+0  21853-3 0  9991"
ISS_L2 = "2 25544  51.6316 163.9608 0004776 179.9710 180.1280 15.49288785587293"


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")
    return True


def _boom(*args, **kwargs):
    raise AssertionError("离线模式仍然发起了网络请求")


def _manager(tmp_path, monkeypatch) -> TLEManager:
    mgr = TLEManager(cache_dir=str(tmp_path), timeout=1.0)
    monkeypatch.setattr(mgr, "_http_get", _boom)
    return mgr


def _write_cache(mgr: TLEManager, catnr: int) -> None:
    with open(mgr._cache_path(catnr), "w", encoding="utf-8") as f:
        f.write(f"{ISS_NAME}\n{ISS_L1}\n{ISS_L2}\n")


# ---------------------------------------------------------------- TLE 单星
def test_offline_tle_single_uses_cache_with_freshness_label(tmp_path, monkeypatch, offline):
    """A：有本地缓存 → 用缓存，并标注「离线·缓存」+ 数据时效。"""
    mgr = _manager(tmp_path, monkeypatch)
    _write_cache(mgr, 25544)
    ent = mgr.fetch(25544)
    assert ent is not None
    assert ent.offline is True
    assert ent.offline_label == "离线·缓存"
    assert ent.refresh_status == "cache"
    assert ent.catnr == 25544
    # 数据时效如实标注：该 TLE 历元已过期 => dead 且不可用于精确过境
    assert ent.freshness_tier == "dead"
    assert ent.pass_eligible is False
    assert mgr.source_status()["state"] == "offline"


def test_offline_tle_single_without_cache_refuses(tmp_path, monkeypatch, offline):
    """B：无缓存 → 诚实空态 + 说明缺缓存，不联网、不伪造。"""
    mgr = _manager(tmp_path, monkeypatch)
    assert mgr.fetch(25544) is None
    status = mgr.source_status()
    assert status["state"] == "offline"
    assert status["last_error"] or status["consecutive_failures"] == 0


# ---------------------------------------------------------------- TLE 群组
def test_offline_tle_group_refuses_with_reason(tmp_path, monkeypatch, offline):
    """B：群组无本地缓存索引 → 明确拒绝并写清原因，不静默返回空。"""
    mgr = _manager(tmp_path, monkeypatch)
    url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle"
    assert mgr.fetch_group(url) == []
    assert "离线模式" in mgr.last_group_error
    assert "无本地缓存" in mgr.last_group_error


def test_offline_tle_group_does_not_touch_network(tmp_path, monkeypatch, offline):
    calls = []
    mgr = _manager(tmp_path, monkeypatch)
    mgr._http_get = lambda url: calls.append(url) or ""
    mgr.fetch_group("https://example.com/group.tle")
    assert calls == []


# ---------------------------------------------------------------- 卫星跟踪刷新
def test_offline_sat_tracker_refresh_keeps_builtin_tle(monkeypatch, offline, tmp_path):
    """B+A：不访问 celerytrak；沿用内置真实 TLE，并如实报「离线未刷新」。

    缓存目录隔离到 tmp：本用例钉的是「无缓存时的内置基线」，不能受开发机
    真实 ``~/.mbdsdr/tle_cache`` 里已刷新的 TLE 影响。
    """
    from mbdsdr_ai import orbit
    monkeypatch.setattr(orbit, "_CACHE_DIR", str(tmp_path / "tle_cache"))
    tr = SatelliteTracker(GroundStation(39.9, 116.4))
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(get=_boom))
    assert tr.update_tle_from_celestrak(timeout=1.0) == 0
    assert "离线模式" in tr.last_refresh_error
    assert tr.tle_date == BUILTIN_TLE_DATE
    assert len(tr.list_satellites()) >= 5


# ---------------------------------------------------------------- 星历 de421
def test_offline_ephemeris_does_not_download(tmp_path, monkeypatch, offline):
    from mbdsdr_ai import solar_system as ss

    monkeypatch.setattr(ss, "EPHEMERIS_DIR", str(tmp_path))
    monkeypatch.setattr(ss, "DE421_PATH", str(tmp_path / "de421.bsp"))
    monkeypatch.setattr(ss.urllib.request, "urlopen", _boom)
    assert ss.download_de421(timeout=1) is None     # 不联网、返回明确失败


def test_offline_ephemeris_backend_reports_fallback(tmp_path, monkeypatch, offline):
    from mbdsdr_ai import solar_system as ss

    monkeypatch.setattr(ss, "EPHEMERIS_DIR", str(tmp_path))
    monkeypatch.setattr(ss, "DE421_PATH", str(tmp_path / "de421.bsp"))
    monkeypatch.setattr(ss, "_network_disabled", lambda: True)
    backend = ss._EphemerisBackend(auto_download=True)
    assert backend._download_thread is None         # 离线不启动下载线程
    assert "离线模式" in backend.last_download_error
    assert "de421.bsp" in backend.last_download_error
    assert backend.backend_name in ("vsop87_astropy[回退]", "none")
    if backend.backend_name != "de421_bsp":
        assert backend.precision_note                    # 回退精度如实标注


def test_offline_ephemeris_reuses_local_bsp(tmp_path, monkeypatch, offline):
    """A：本地已有 de421.bsp → 离线直接复用，不重复下载。"""
    from mbdsdr_ai import solar_system as ss

    target = tmp_path / "de421.bsp"
    with open(target, "wb") as f:
        f.truncate(10 * 1024 * 1024 + 1024)          # 仅用于大小校验
    monkeypatch.setattr(ss, "EPHEMERIS_DIR", str(tmp_path))
    monkeypatch.setattr(ss.urllib.request, "urlopen", _boom)
    assert ss.download_de421(timeout=1) == str(target)


# ---------------------------------------------------------------- 模型 API
def test_offline_model_list_uses_builtin_and_labels(monkeypatch, offline):
    from mbdsdr_ai.model_manager import BUILTIN_MODELS, ModelManager

    mm = ModelManager(api_key="sk-test", timeout=1)
    monkeypatch.setattr(mm._session, "get", _boom)
    models = mm.fetch_models(force_refresh=True)
    assert [m.id for m in models] == [m.id for m in BUILTIN_MODELS]
    assert "离线模式" in mm._last_error
    assert "内置" in mm._last_error


def test_offline_chat_refuses_without_retry(monkeypatch, offline):
    from mbdsdr_ai.model_manager import ModelManager

    mm = ModelManager(api_key="sk-test", max_retries=2, timeout=1)
    monkeypatch.setattr(mm._session, "post", _boom)
    res = mm.chat([{"role": "user", "content": "hi"}])
    assert res["success"] is False and res["offline"] is True
    assert "离线模式" in res["error"]
    assert mm.stats.total_calls == 1            # 只记一次，不重试
    assert mm.stats.failed_calls == 1


def test_offline_chat_stream_refuses(monkeypatch, offline):
    from mbdsdr_ai.model_manager import ModelManager

    mm = ModelManager(api_key="sk-test", timeout=1)
    monkeypatch.setattr(mm._session, "post", _boom)
    events = list(mm.chat_stream([{"role": "user", "content": "hi"}]))
    assert events[-1]["done"] is True
    assert events[-1]["success"] is False
    assert "离线模式" in events[-1]["error"]


# ---------------------------------------------------------------- 公开 API（ISS/太空人/天气/飞机）
@pytest.mark.parametrize("name,args,feature", [
    ("get_iss_position", (), "ISS"),
    ("get_people_in_space", (), "太空"),
    ("get_weather", (39.9, 116.4), "天气"),
    ("get_aircraft_nearby", (39.9, 116.4), "飞机"),
])
def test_offline_openapi_features_refuse(monkeypatch, offline, name, args, feature):
    from mbdsdr_ai import openapi_integration as oi

    monkeypatch.setattr(oi.requests, "get", _boom)
    monkeypatch.setattr(oi.requests, "post", _boom)
    res = getattr(oi, name)(*args)
    assert res["success"] is False and res["offline"] is True
    assert "离线模式" in res["error"] and feature in res["error"]


# ---------------------------------------------------------------- NTP 授时
def test_offline_ntp_offset_refuses(monkeypatch, offline):
    from mbdsdr_ai import time_sync

    monkeypatch.setattr(time_sync.socket, "socket", _boom)
    res = time_sync.ntp_offset(timeout=1.0)
    assert res["reachable"] is False and res["offline"] is True
    assert "离线模式" in res["error"] and "NTP" in res["error"]


def test_offline_new_spacetime_ntp_raises_explicitly(monkeypatch, offline):
    from mbdsdr_ai import new_spacetime as ns

    monkeypatch.setattr(ns.socket, "socket", _boom)
    with pytest.raises(NTPError) as ei:
        get_ntp_time(timeout=1.0)
    assert "离线模式" in str(ei.value)


def test_offline_time_info_falls_back_to_system_clock(monkeypatch, offline):
    """A：NTP 不可用 → 回退系统时钟，source 明确标 system（不冒充已校时）。"""
    from mbdsdr_ai import new_spacetime as ns

    monkeypatch.setattr(ns.socket, "socket", _boom)
    info = ns.get_time_info(prefer_ntp=True)
    assert info.source == "system"
    assert info.utc_time is not None
    assert info.ntp_server == ""


def test_offline_ntp_tool_reports_failure(monkeypatch, offline):
    """Agent 工具不得把「离线不可达」包成 success=True。"""
    from mbdsdr_ai import time_sync
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.tool_registry import ToolRegistry

    reg = ToolRegistry()
    MBDSDRAgent._register_time_sync_tools(SimpleNamespace(tool_registry=reg))
    monkeypatch.setattr(time_sync.socket, "socket", _boom)
    res = reg.call("time_sync_ntp", {"timeout": 1})
    assert res.success is False
    assert "离线模式" in (res.content + res.error)


# ---------------------------------------------------------------- Agent 联网工具
def _web_tools(monkeypatch):
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.tool_registry import ToolRegistry

    reg = ToolRegistry()
    MBDSDRAgent._register_web_tools(SimpleNamespace(tool_registry=reg))
    return reg


def test_offline_web_fetch_tool_refuses(monkeypatch, offline):
    reg = _web_tools(monkeypatch)
    monkeypatch.setattr("urllib.request.urlopen", _boom)
    res = reg.call("web_fetch_url", {"url": "https://example.com"})
    assert res.success is False
    assert "离线模式" in (res.content + res.error)


def test_offline_git_clone_tool_refuses(monkeypatch, offline, tmp_path):
    reg = _web_tools(monkeypatch)
    monkeypatch.setattr("subprocess.run", _boom)
    res = reg.call("git_clone_repo", {"url": "https://example.com/repo.git",
                                      "dest": str(tmp_path / "repo")})
    assert res.success is False
    assert "离线模式" in (res.content + res.error)
    assert not (tmp_path / "repo").exists()

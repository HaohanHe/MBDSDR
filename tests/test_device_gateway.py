# SPDX-License-Identifier: MIT
"""多设备网关（M2 内核）：配对、角色、只读端点与位姿上报。

钉死的不变量：
1. 默认只绑回环；绑私有网段必须显式 allow_lan；公网/通配一律拒绝。
2. 未配对只能 `/health` 与 `/pair`；其余 401。
3. 角色不符 / 替别人上报 / 陈旧位姿 / 越界数值一律拒绝并给原因。
4. 没有任何写硬件端点；`/bridge/*` 这类写路径在本网关 404。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.device_gateway import (  # noqa: E402
    DeviceGateway, PairingRegistry,
)


def _registry(**kw):
    kw.setdefault("code_factory", lambda: "123456")
    tokens = iter(["tok-lens-1", "tok-eye-1", "tok-hand-1", "tok-head-1"])
    kw.setdefault("token_factory", lambda: next(tokens))
    return PairingRegistry(**kw)


def _pair(gw, *, code="123456", name="手机A", role="lens"):
    status, raw, _ = gw.dispatch(
        "POST", "/pair",
        json.dumps({"code": code, "device_name": name,
                    "role": role}).encode())
    return status, json.loads(raw)


def _get(gw, path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    status, raw, _ = gw.dispatch("GET", path, b"", headers)
    return status, json.loads(raw)


def _pose(gw, token, device_id, role="lens", **over):
    body = {
        "schema": "sky-pose/1",
        "source": device_id,
        "role": role,
        "t_utc": time.time(),
        "az_deg": 123.4,
        "alt_deg": 45.6,
        "accuracy_deg": 2.5,
        "roll_deg": 0.0,
        "fov_deg": 60.0,
    }
    body.update(over)
    status, raw, _ = gw.dispatch(
        "POST", "/pose", json.dumps(body).encode(),
        {"Authorization": f"Bearer {token}"})
    return status, json.loads(raw)


# ---------------------------------------------------------------- 绑定地址

@pytest.mark.parametrize("bad", ["0.0.0.0", "::", "8.8.8.8", "example.com"])
def test_bind_rejects_wildcard_public_and_hostname(bad):
    with pytest.raises(ValueError):
        DeviceGateway(host=bad, allow_lan=True)


def test_private_bind_requires_explicit_allow_lan():
    with pytest.raises(ValueError):
        DeviceGateway(host="192.168.1.10")
    gw = DeviceGateway(host="192.168.1.10", allow_lan=True)
    assert gw.host == "192.168.1.10"
    assert DeviceGateway(host="127.0.0.1").host == "127.0.0.1"


# ---------------------------------------------------------------- 配对

def test_health_is_open_and_says_pairing_required():
    gw = DeviceGateway()
    status, d = _get(gw, "/health")
    assert status == 200
    assert d["pairing_required"] is True
    assert "lens" in d["roles"]


def test_wrong_code_and_lockout():
    gw = DeviceGateway(registry=_registry())
    for _ in range(4):
        status, d = _pair(gw, code="000000")
        assert status == 401 and d["code"] == "bad_code"
    status, d = _pair(gw, code="000000")
    assert status == 401
    # 第 6 次（含正确码）进入锁定
    status, d = _pair(gw, code="123456")
    assert status == 429 and d["code"] == "locked"


def test_pair_then_token_works_and_roster_lists_device():
    gw = DeviceGateway(registry=_registry(),
                       panorama_provider=lambda: {"ok": True, "schema": "x"})
    status, d = _pair(gw)
    assert status == 200 and d["ok"] is True
    token = d["token"]
    assert token == "tok-lens-1"
    assert d["device"]["role"] == "lens"

    status, roster = _get(gw, "/roster", token)
    assert status == 200
    assert roster["authority"] == "desktop"
    assert [x["role"] for x in roster["devices"]] == ["lens"]
    assert "token" not in json.dumps(roster)  # 名册不回传凭据

    status, pano = _get(gw, "/panorama", token)
    assert status == 200 and pano["ok"] is True


def test_unpaired_and_bad_token_are_401():
    gw = DeviceGateway(registry=_registry())
    status, d = _get(gw, "/roster")
    assert status == 401 and d["error"] == "unauthorized"
    status, d = _get(gw, "/roster", "nope")
    assert status == 401


def test_bad_role_and_empty_name_rejected():
    gw = DeviceGateway(registry=_registry())
    status, d = _pair(gw, role="admin")
    assert status == 401 and d["code"] == "bad_role"
    status, d = _pair(gw, name="   ")
    assert status == 401 and d["code"] == "bad_name"


def test_token_expiry_and_revoke():
    now = [1000.0]
    reg = _registry(now=lambda: now[0], token_ttl_s=10.0)
    gw = DeviceGateway(registry=reg)
    status, d = _pair(gw)
    assert status == 200
    token = d["token"]
    now[0] += 11.0
    status, _ = _get(gw, "/roster", token)
    assert status == 401
    # 重新配一个并主动吊销
    now[0] += 1.0
    status, d = _pair(gw)
    assert status == 200
    assert reg.revoke(d["token"]) is True
    status, _ = _get(gw, "/roster", d["token"])
    assert status == 401


# ---------------------------------------------------------------- 全景/只读

def test_panorama_without_provider_is_503_not_fake_data():
    gw = DeviceGateway(registry=_registry())
    _, d = _pair(gw)
    status, pano = _get(gw, "/panorama", d["token"])
    assert status == 503
    assert pano["error"] == "panorama_unavailable"
    assert "ok" not in pano or pano.get("ok") is False


def test_provider_exception_is_visible_not_swallowed():
    def boom():
        raise RuntimeError("上游断网")

    gw = DeviceGateway(registry=_registry(), panorama_provider=boom)
    _, d = _pair(gw)
    status, pano = _get(gw, "/panorama", d["token"])
    assert status == 500
    assert "上游断网" in pano["reason"]


def test_no_write_endpoints_exist():
    gw = DeviceGateway(registry=_registry())
    _, d = _pair(gw)
    token = d["token"]
    status, body, _ = gw.dispatch(
        "POST", "/bridge/invokeTool",
        json.dumps({"name": "set_frequency", "args": {}}).encode(),
        {"Authorization": f"Bearer {token}"})
    assert status == 404
    assert json.loads(body)["error"] == "not_found"


# ---------------------------------------------------------------- 位姿

def test_pose_requires_lens_or_eyepiece_role():
    gw = DeviceGateway(registry=_registry())
    _, h = _pair(gw, name="手持", role="handheld")
    status, d = _pose(gw, h["token"], h["device"]["id"], role="handheld")
    assert status == 403
    assert "只读" in d["reason"]


def test_pose_validates_source_schema_and_values():
    gw = DeviceGateway(registry=_registry())
    _, d = _pair(gw)
    token, dev = d["token"], d["device"]["id"]
    cases = [
        ({"schema": "bad"}, 400),
        ({"source": "dev-someone-else"}, 403),
        ({"t_utc": time.time() - 1000}, 400),
        ({"az_deg": 360}, 400),
        ({"alt_deg": 91}, 400),
        ({"accuracy_deg": 0}, 400),
        ({"fov_deg": 181}, 400),
    ]
    for override, expect in cases:
        status, body = _pose(gw, token, dev, **override)
        assert status == expect, (override, status, body)


def test_pose_accepts_and_latest_per_device_replaces():
    gw = DeviceGateway(registry=_registry())
    _, lens = _pair(gw, name="镜头", role="lens")
    _, eye = _pair(gw, name="目镜", role="eyepiece")
    status, d = _pose(gw, lens["token"], lens["device"]["id"], role="lens")
    assert status == 200 and d["pose"]["az_deg"] == 123.4
    status, d = _pose(gw, eye["token"], eye["device"]["id"], role="eyepiece",
                      az_deg=200.0, accuracy_deg=0.8)
    assert status == 200
    # 同一设备再报一次：只保留最新
    status, d = _pose(gw, lens["token"], lens["device"]["id"], role="lens",
                      az_deg=130.0)
    assert status == 200
    status, poses = _get(gw, "/poses", lens["token"])
    assert status == 200
    rows = {p["source"]: p for p in poses["poses"]}
    assert set(rows) == {lens["device"]["id"], eye["device"]["id"]}
    assert rows[lens["device"]["id"]]["az_deg"] == 130.0
    assert rows[eye["device"]["id"]]["role"] == "eyepiece"


# ---------------------------------------------------------------- 默认全景 provider

def test_default_provider_uses_build_panorama_and_reports_station(monkeypatch):
    from mbdsdr_ai import sky_tools, space_weather
    from mbdsdr_ai.device_gateway import build_default_panorama_provider

    monkeypatch.setattr(space_weather, "default_station",
                        lambda: (45.0, 120.0))
    seen = {}

    def fake_build(lat, lon, **kw):
        seen.update({"lat": lat, "lon": lon, **kw})
        return {"ok": True, "schema": "mbdsdr.sky-panorama/1"}

    monkeypatch.setattr(sky_tools, "build_panorama", fake_build)
    provider = build_default_panorama_provider(allow_live=False)
    out = provider()
    assert out["schema"] == "mbdsdr.sky-panorama/1"
    assert seen["lat"] == 45.0 and seen["lon"] == 120.0
    assert seen["allow_live"] is False
    assert seen["time_source"] in ("time_engine", "wall_clock")
    assert "设备网关" in seen["site_note"]


def test_default_provider_without_station_raises(monkeypatch):
    from mbdsdr_ai import space_weather
    from mbdsdr_ai.device_gateway import build_default_panorama_provider

    monkeypatch.setattr(space_weather, "default_station", lambda: (None, None))
    provider = build_default_panorama_provider()
    with pytest.raises(RuntimeError):
        provider()


def test_station_provider_reorders_each_call(monkeypatch):
    """桌面换站/GNSS fix 后，/panorama 必须用新站址（不缓存旧坐标）。"""
    from mbdsdr_ai import sky_tools
    from mbdsdr_ai.device_gateway import build_station_panorama_provider

    seen = []

    def fake_build(lat, lon, **kw):
        seen.append((lat, lon, kw.get("alt_m")))
        return {"ok": True}

    monkeypatch.setattr(sky_tools, "build_panorama", fake_build)
    station = {"v": (45.0, 120.0, 10.0)}
    provider = build_station_panorama_provider(
        lambda: station["v"], allow_live=False)
    provider()
    station["v"] = (46.0, 121.0, 20.0)
    provider()
    assert seen == [(45.0, 120.0, 10.0), (46.0, 121.0, 20.0)]
    station["v"] = (None, None, None)
    with pytest.raises(RuntimeError):
        provider()


# ---------------------------------------------------------------- socket 冒烟
def test_socket_smoke_health_and_pair():
    gw = DeviceGateway(registry=_registry(),
                       panorama_provider=lambda: {"ok": True,
                                                  "schema": "mbdsdr.sky-panorama/1"})
    base = gw.start()
    try:
        with urllib.request.urlopen(base + "health", timeout=5) as resp:
            health = json.loads(resp.read())
        assert health["service"] == "mbdsdr-device-gateway"

        req = urllib.request.Request(
            base + "pair",
            data=json.dumps({"code": "123456", "device_name": "手机A",
                             "role": "lens"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            paired = json.loads(resp.read())
        assert paired["ok"] is True

        req2 = urllib.request.Request(
            base + "panorama",
            headers={"Authorization": "Bearer " + paired["token"]})
        with urllib.request.urlopen(req2, timeout=5) as resp:
            pano = json.loads(resp.read())
        assert pano["ok"] is True

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "roster", timeout=5)
        assert exc.value.code == 401
    finally:
        gw.stop()

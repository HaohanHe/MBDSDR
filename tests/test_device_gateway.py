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
    DeviceGateway, PairingRegistry, SdrLeaseRegistry,
    build_station_passes_provider, build_tle_passes_provider, parse_tle_groups,
)


def _registry(**kw):
    kw.setdefault("code_factory", lambda: "123456")
    tokens = iter(["tok-lens-1", "tok-eye-1", "tok-hand-1", "tok-head-1",
                   "tok-head-2"])
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


# ---------------------------------------------------------------- 多机融合
def _pair_two(gw):
    """配对 lens + eyepiece，返回 (lens 配对回执, eyepiece 配对回执)。"""
    _, lens = _pair(gw, name="手机A（镜头）", role="lens")
    _, eye = _pair(gw, name="手机B（目镜）", role="eyepiece")
    return lens, eye


def test_fuse_without_poses_is_invalid_and_says_so():
    gw = DeviceGateway(registry=_registry())
    lens, _ = _pair_two(gw)
    status, d = _get(gw, "/fuse", lens["token"])
    assert status == 200
    assert d["schema"] == "mbdsdr.pose-fusion/1"
    assert d["observations"] == 0
    assert d["valid"] is False
    assert d["az_deg"] is None and d["alt_deg"] is None
    assert "observations 为空" in d["reason"]


def test_fuse_two_fresh_poses_weights_by_accuracy():
    gw = DeviceGateway(registry=_registry())
    lens, eye = _pair_two(gw)
    now = time.time()
    assert _pose(gw, lens["token"], lens["device"]["id"], role="lens",
                 t_utc=now, az_deg=100.0, alt_deg=40.0, accuracy_deg=4.0,
                 fov_deg=40.0)[0] == 200
    assert _pose(gw, eye["token"], eye["device"]["id"], role="eyepiece",
                 t_utc=now, az_deg=110.0, alt_deg=44.0, accuracy_deg=1.0,
                 fov_deg=8.0)[0] == 200

    status, d = _get(gw, "/fuse", lens["token"])
    assert status == 200
    assert d["valid"] is True
    assert d["level"] == "fused"
    assert d["observations"] == 2
    assert len(d["sources"]) == 2
    # 权重 1/acc²：目镜(1°) 权 1.0，镜头(4°) 权 0.0625 → 方位靠近 110°。
    assert 100.0 < d["az_deg"] < 110.0
    assert d["az_deg"] > 105.0
    assert 40.0 < d["alt_deg"] < 44.0
    assert d["accuracy_deg"] < 1.0
    assert d["reason"] == ""


def test_fuse_stale_pose_is_dropped_with_reason():
    gw = DeviceGateway(registry=_registry())
    lens, _ = _pair_two(gw)
    # /pose 的时钟窗是 ±300 s，但融合的陈旧阈值是 2 s：这里放一条 10 s 前的。
    assert _pose(gw, lens["token"], lens["device"]["id"], role="lens",
                 t_utc=time.time() - 10.0)[0] == 200
    status, d = _get(gw, "/fuse", lens["token"])
    assert status == 200
    assert d["valid"] is False
    assert d["az_deg"] is None
    assert len(d["dropped"]) == 1
    assert "陈旧" in d["dropped"][0]["reason"]
    assert "没有可用观测" in d["reason"]


def test_fuse_is_read_only_for_handheld_and_requires_token():
    gw = DeviceGateway(registry=_registry())
    _, hand = _pair(gw, name="手持机", role="handheld")
    status, d = _get(gw, "/fuse", hand["token"])
    assert status == 200
    assert d["valid"] is False

    status, d = _get(gw, "/fuse")
    assert status == 401
    assert d["error"] == "unauthorized"


def test_fuse_rejects_non_get():
    gw = DeviceGateway(registry=_registry())
    lens, _ = _pair_two(gw)
    status, raw, _ = gw.dispatch(
        "POST", "/fuse", b"{}", {"Authorization": f"Bearer {lens['token']}"})
    assert status == 405
    assert json.loads(raw)["error"] == "method_not_allowed"


# ---------------------------------------------------------------- 目镜识别（M3.3）
def test_pose_identification_only_for_eyepiece_and_validated():
    gw = DeviceGateway(registry=_registry())
    _, lens = _pair(gw, name="镜头", role="lens")
    _, eye = _pair(gw, name="目镜", role="eyepiece")

    # 镜头机携带识别结果 → 403（不冒充认星）。
    status, d = _pose(
        gw, lens["token"], lens["device"]["id"], role="lens",
        identification={"name": "Vega", "hip": 91262, "separation_deg": 0.2})
    assert status == 403
    assert "eyepiece" in d["reason"]

    # 目镜机合法识别 → 200，/poses 原样读回。
    status, d = _pose(
        gw, eye["token"], eye["device"]["id"], role="eyepiece",
        identification={"name": "织女一", "hip": 91262,
                        "separation_deg": 0.35})
    assert status == 200
    status, poses = _get(gw, "/poses", eye["token"])
    row = poses["poses"][0]
    assert row["identification"] == {
        "name": "织女一", "hip": 91262, "separation_deg": 0.35}

    # 非法识别字段逐条拒绝（不静默截断/补值）。
    cases = [
        {"hip": "91262"},          # 非整数
        {"separation_deg": 181},   # 角距越界
        {},                        # 空对象
    ]
    for bad in cases:
        status, d = _pose(
            gw, eye["token"], eye["device"]["id"], role="eyepiece",
            identification=bad)
        assert status == 400, (bad, status, d)

    # 融合只看朝向：单条目镜观测仍 valid，并保留识别字段在 /poses。
    status, fused = _get(gw, "/fuse", eye["token"])
    assert status == 200
    assert fused["valid"] is True and fused["level"] == "single"


# ---------------------------------------------------------------- 过境预报（M4.1）
_ISS_TLE = (
    "ISS (ZARYA)",
    "1 25544U 98067A   24001.50000000  .00016717  00000-0  30303-3 0  9993",
    "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49309023 42596",
)


def test_passes_unwired_is_503():
    gw = DeviceGateway(registry=_registry())
    _, lens = _pair(gw)
    status, d = _get(gw, "/passes", lens["token"])
    assert status == 503
    assert d["error"] == "passes_unavailable"


def test_passes_provider_error_and_bad_shape():
    def boom():
        raise RuntimeError("TLE 缺失")

    gw = DeviceGateway(registry=_registry(), passes_provider=boom)
    _, lens = _pair(gw)
    status, d = _get(gw, "/passes", lens["token"])
    assert status == 500 and d["error"] == "passes_failed"
    assert "TLE 缺失" in d["reason"]

    gw2 = DeviceGateway(registry=_registry(), passes_provider=lambda: [])
    _, lens2 = _pair(gw2)
    status, d = _get(gw2, "/passes", lens2["token"])
    assert status == 500 and d["error"] == "passes_bad_shape"


def test_passes_requires_token_and_rejects_post():
    gw = DeviceGateway(
        registry=_registry(),
        passes_provider=lambda: {"schema": "mbdsdr.sat-passes/1", "passes": []})
    status, d = _get(gw, "/passes")
    assert status == 401 and d["error"] == "unauthorized"

    _, lens = _pair(gw)
    status, raw, _ = gw.dispatch(
        "POST", "/passes", b"{}", {"Authorization": f"Bearer {lens['token']}"})
    assert status == 405
    assert json.loads(raw)["error"] == "method_not_allowed"


def test_parse_tle_groups_three_and_two_line():
    text = "\n".join([
        "# 注释行忽略",
        "ISS (ZARYA)", _ISS_TLE[1], _ISS_TLE[2],
        "",
        _ISS_TLE[1], _ISS_TLE[2],          # 无标题的两行组
        "junk line",
    ])
    groups = parse_tle_groups(text)
    assert len(groups) == 2
    assert groups[0] == list(_ISS_TLE)
    assert groups[1] == [_ISS_TLE[1], _ISS_TLE[2]]
    assert parse_tle_groups("nothing here") == []


def test_tle_passes_provider_serializes_real_passes(tmp_path):
    path = tmp_path / "tle.txt"
    path.write_text("\n".join(_ISS_TLE), encoding="utf-8")
    provider = build_tle_passes_provider(
        str(path), lat=40.0, lon=-100.0, hours=24.0, min_alt=10.0)
    data = provider()

    assert data["schema"] == "mbdsdr.sat-passes/1"
    assert data["source"] == "sgp4"
    assert data["hours"] == 24.0
    assert data["station"] == {"lat_deg": 40.0, "lon_deg": -100.0,
                               "alt_m": 0.0}
    passes = data["passes"]
    assert data["count"] == len(passes)
    # 24h 内 ISS 在该站至少有一次 >=10° 过境。
    assert len(passes) >= 1
    rises = [p["rise_unix"] for p in passes]
    assert rises == sorted(rises)
    keys = {"name", "rise_utc", "set_utc", "peak_utc", "rise_unix",
            "set_unix", "peak_unix", "max_alt_deg", "rise_az_deg",
            "set_az_deg", "duration_s", "sunlit"}
    for p in passes:
        assert set(p) == keys
        assert p["name"] == "ISS (ZARYA)"
        assert p["rise_unix"] <= p["peak_unix"] <= p["set_unix"]
        assert 10.0 <= p["max_alt_deg"] <= 90.0
        assert 0.0 <= p["rise_az_deg"] < 360.0
        assert 0.0 <= p["set_az_deg"] < 360.0
        assert p["duration_s"] > 0.0


def test_tle_passes_provider_rejects_bad_args_and_empty_file(tmp_path):
    with pytest.raises(ValueError):
        build_tle_passes_provider("x", lat=1, lon=2, hours=0)
    with pytest.raises(ValueError):
        build_tle_passes_provider("x", lat=1, lon=2, min_alt=90)
    empty = tmp_path / "empty.txt"
    empty.write_text("# 只有注释\n", encoding="utf-8")
    provider = build_tle_passes_provider(str(empty), lat=1, lon=2)
    with pytest.raises(ValueError):
        provider()
    missing = build_tle_passes_provider(
        str(tmp_path / "missing.txt"), lat=1, lon=2)
    with pytest.raises(FileNotFoundError):
        missing()


def test_station_passes_provider_reorders_station_each_call(tmp_path):
    path = tmp_path / "tle.txt"
    path.write_text("\n".join(_ISS_TLE), encoding="utf-8")
    station = {"value": (40.0, -100.0, 0.0)}
    provider = build_station_passes_provider(
        str(path), lambda: station["value"], hours=12.0)

    first = provider()
    assert first["station"] == {"lat_deg": 40.0, "lon_deg": -100.0,
                                "alt_m": 0.0}
    # 换站后下一次请求立即用新坐标（不缓存旧站）。
    station["value"] = (41.5, -99.0, 123.0)
    second = provider()
    assert second["station"] == {"lat_deg": 41.5, "lon_deg": -99.0,
                                 "alt_m": 123.0}


def test_station_passes_provider_missing_station_raises(tmp_path):
    path = tmp_path / "tle.txt"
    path.write_text("\n".join(_ISS_TLE), encoding="utf-8")
    provider = build_station_passes_provider(
        str(path), lambda: (None, None, None))
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


# ---------------------------------------------------------------- SDR 租约（M4.2）

def _post(gw, path, token, payload=None):
    body = b"" if payload is None else json.dumps(payload).encode()
    status, raw, _ = gw.dispatch(
        "POST", path, body, {"Authorization": f"Bearer {token}"})
    return status, json.loads(raw)


def _headset_setup(*, ttl_s=120.0, desktop_busy=None,
                   rtl_tcp=("127.0.0.1", 1234)):
    """配好 lens/eyepiece/handheld/headset，返回 (gw, tokens, clock)。

    tokens 按配对顺序给出；时钟是可变字典，测试可推进过期。
    """
    clock = {"now": 1_000_000.0}
    lease_ids = iter([f"lease-{i}" for i in range(1, 20)])
    reg = SdrLeaseRegistry(ttl_s=ttl_s, now=lambda: clock["now"],
                           lease_factory=lambda: next(lease_ids))
    gw = DeviceGateway(
        registry=_registry(), sdr_lease=reg,
        rtl_tcp_provider=(lambda: rtl_tcp) if rtl_tcp is not None else None,
        desktop_sdr_busy=desktop_busy,
    )
    tokens = {}
    for name, role in (("手机A", "lens"), ("手机B", "eyepiece"),
                       ("手持", "handheld"), ("头显", "headset")):
        status, d = _pair(gw, name=name, role=role)
        assert status == 200 and d["ok"] is True
        tokens[role] = d["token"]
        tokens[f"{role}_id"] = d["device"]["id"]
    return gw, tokens, clock


def test_sdr_lease_not_advertised_without_rtl_tcp():
    gw, tokens, _ = _headset_setup(rtl_tcp=None)
    status, health = _get(gw, "/health")
    assert status == 200 and health["sdr_lease"] is False
    status, d = _post(gw, "/sdr/lease", tokens["headset"])
    assert status == 503 and d["error"] == "sdr_not_configured"
    assert "rtl_tcp" in d["reason"]


def test_sdr_lease_denied_for_non_headset_roles():
    gw, tokens, _ = _headset_setup()
    for role in ("lens", "eyepiece", "handheld"):
        status, d = _post(gw, "/sdr/lease", tokens[role])
        assert status == 403 and d["error"] == "sdr_lease_role_denied"
        assert "headset" in d["reason"]
    # 只读查询对任意角色开放，且此时没有租约。
    status, d = _get(gw, "/sdr/lease", tokens["lens"])
    assert status == 200 and d["active"] is False and d["lease"] is None


def test_sdr_lease_grant_conflict_and_release():
    gw, tokens, _ = _headset_setup()
    status, d = _post(gw, "/sdr/lease", tokens["headset"],
                      {"purpose": "头显直连频谱"})
    assert status == 200 and d["ok"] is True and d["exclusive"] is True
    assert d["renewed"] is False
    assert d["rtl_tcp"] == {"host": "127.0.0.1", "port": 1234}
    assert d["lease"]["device_id"] == tokens["headset_id"]
    assert d["lease"]["role"] == "headset"
    assert d["lease"]["purpose"] == "头显直连频谱"
    assert d["lease"]["expires_unix"] == 1_000_000.0 + 120.0
    lease_id = d["lease"]["lease_id"]

    # 持有者可见 rtl_tcp；其它角色只知道「谁占着」。
    status, mine = _get(gw, "/sdr/lease", tokens["headset"])
    assert status == 200 and mine["active"] is True
    assert mine["rtl_tcp"] == {"host": "127.0.0.1", "port": 1234}
    status, other = _get(gw, "/sdr/lease", tokens["lens"])
    assert status == 200 and other["active"] is True
    assert "rtl_tcp" not in other
    assert other["lease"]["device_id"] == tokens["headset_id"]

    # 第二台头显：409 冲突；非持有者不能释放。
    status, d2 = _pair(gw, name="头显2", role="headset")
    assert status == 200
    token2 = d2["token"]
    status, conflict = _post(gw, "/sdr/lease", token2)
    assert status == 409 and conflict["error"] == "sdr_leased"
    assert conflict["lease"]["lease_id"] == lease_id
    status, denied = _post(gw, "/sdr/release", token2)
    assert status == 403 and denied["error"] == "not_holder"

    status, released = _post(gw, "/sdr/release", tokens["headset"])
    assert status == 200 and released["released"] is True
    assert released["lease_id"] == lease_id
    status, again = _post(gw, "/sdr/release", tokens["headset"])
    assert status == 409 and again["error"] == "no_lease"
    status, empty = _get(gw, "/sdr/lease", tokens["headset"])
    assert empty["active"] is False and empty["lease"] is None


def test_sdr_lease_renews_for_holder_and_expires():
    gw, tokens, clock = _headset_setup(ttl_s=120.0)
    status, first = _post(gw, "/sdr/lease", tokens["headset"])
    assert status == 200 and first["renewed"] is False
    first_id = first["lease"]["lease_id"]
    first_expiry = first["lease"]["expires_unix"]

    clock["now"] += 60.0
    status, renewed = _post(gw, "/sdr/lease", tokens["headset"])
    assert status == 200 and renewed["renewed"] is True
    assert renewed["lease"]["lease_id"] == first_id
    assert renewed["lease"]["expires_unix"] > first_expiry

    # 超过 TTL 未续：自动失效，下一位可申请新租约。
    clock["now"] += 121.0
    status, gone = _get(gw, "/sdr/lease", tokens["headset"])
    assert status == 200 and gone["active"] is False
    status, again = _post(gw, "/sdr/lease", tokens["headset"])
    assert status == 200 and again["renewed"] is False
    assert again["lease"]["lease_id"] != first_id


def test_sdr_lease_refuses_when_desktop_busy():
    gw, tokens, _ = _headset_setup(desktop_busy=lambda: True)
    status, d = _post(gw, "/sdr/lease", tokens["headset"])
    assert status == 503 and d["error"] == "sdr_busy_desktop"
    assert "桌面" in d["reason"]

    def broken():
        raise RuntimeError("状态总线不可读")

    gw2, tokens2, _ = _headset_setup(desktop_busy=broken)
    status, d2 = _post(gw2, "/sdr/lease", tokens2["headset"])
    assert status == 503 and d2["error"] == "sdr_busy_unknown"
    assert "状态总线不可读" in d2["reason"]


def test_sdr_lease_has_no_tuning_or_write_endpoints():
    gw, tokens, _ = _headset_setup()
    for path in ("/sdr/tune", "/sdr/gain", "/sdr/start", "/bridge/tune"):
        status, d = _get(gw, path, tokens["headset"])
        assert status == 404 and d["error"] == "not_found"


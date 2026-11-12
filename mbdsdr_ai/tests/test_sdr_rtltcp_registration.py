# SPDX-License-Identifier: MIT
"""rtl_tcp 网络源登记：env 解析 + SDRBackendManager._discover 集成。

RTLSDRBackend(host=...) 构造不联网（只建 SDRDevice 元数据），connect() 才真正
握手——所以这些测试无硬件、无网络也能确定性运行。
"""
import os

import pytest

from mbdsdr_ai.sdr_backend import (
    RTL_TCP_ENV,
    SDRBackendManager,
    enumerate_all_sdr_devices,
    parse_rtl_tcp_env,
    resolve_device_identities,
)


def _rtl_tcp_ids():
    """按生产口径算 rtl_tcp 源的 device_id（同一枚举 + 同一身份键）。

    device_id 已统一为身份键（``rtlsdr|serial:rtl_tcp_host_port``），
    不能再按 ``rtl_tcp_`` 前缀过滤——那正是「换了 id 就静默空转」的写法。
    """
    devices = enumerate_all_sdr_devices()
    return [key for dev, (key, _q, _w)
            in zip(devices, resolve_device_identities(devices))
            if dev.get("source") == "rtl_tcp"]


# ---------------------------------------------------------------------------
# parse_rtl_tcp_env：纯解析
# ---------------------------------------------------------------------------

def test_parse_empty_is_empty_list():
    assert parse_rtl_tcp_env("") == []
    assert parse_rtl_tcp_env(None) == []
    assert parse_rtl_tcp_env("  ,  ,") == []


def test_parse_bare_host_defaults_to_1234():
    assert parse_rtl_tcp_env("127.0.0.1") == [("127.0.0.1", 1234)]
    assert parse_rtl_tcp_env("radio.local") == [("radio.local", 1234)]


def test_parse_host_and_port():
    assert parse_rtl_tcp_env("127.0.0.1:1234") == [("127.0.0.1", 1234)]
    assert parse_rtl_tcp_env("10.0.0.5:9999") == [("10.0.0.5", 9999)]


def test_parse_multiple_sources():
    assert parse_rtl_tcp_env("127.0.0.1:1234, radio.local, 10.0.0.9:2345") == [
        ("127.0.0.1", 1234), ("radio.local", 1234), ("10.0.0.9", 2345)]


def test_parse_garbage_entries_skipped_not_faked():
    # 坏端口 / 端口越界 / 空主机：全部跳过，绝不造默认后端
    assert parse_rtl_tcp_env("host:notaport") == []
    assert parse_rtl_tcp_env("host:99999") == []
    assert parse_rtl_tcp_env("host:0") == []
    assert parse_rtl_tcp_env(":1234") == []
    # 好条目与坏条目混排：只保留好条目
    assert parse_rtl_tcp_env("bad:x, 127.0.0.1:1234") == [("127.0.0.1", 1234)]


# ---------------------------------------------------------------------------
# _discover 集成：env -> backends 登记
# ---------------------------------------------------------------------------

def test_discover_registers_rtl_tcp_source(monkeypatch):
    monkeypatch.setenv(RTL_TCP_ENV, "127.0.0.1:1234")
    expected = _rtl_tcp_ids()
    assert len(expected) == 1
    mgr = SDRBackendManager()
    dev = mgr.list_devices()
    rtl_tcp = [d for d in dev if d["device_id"] == expected[0]]
    assert len(rtl_tcp) == 1
    d = rtl_tcp[0]
    assert d["type"] == "rtl_sdr"
    assert d["name"] == "rtl_tcp 127.0.0.1:1234"
    assert d["connected"] is False   # 登记 != 连接
    assert d["supports_iq"] is True


def test_discover_without_env_has_no_rtl_tcp_source(monkeypatch):
    monkeypatch.delenv(RTL_TCP_ENV, raising=False)
    assert _rtl_tcp_ids() == []
    mgr = SDRBackendManager()
    assert not [d for d in mgr.list_devices()
                if d["name"].startswith("rtl_tcp ")]


def test_registered_backend_connects_to_live_rtl_tcp():
    """端到端（有真实 rtl_tcp 服务时）：连本机 1234 的官方 rtl_tcp。

    未配置/无服务时跳过（honest skip，绝不假装连接成功）。
    """
    entries = parse_rtl_tcp_env(os.environ.get(RTL_TCP_ENV, ""))
    if not entries:
        pytest.skip(
            "缺：环境变量 MBDSDR_RTL_TCP 未配置，没有可连的 rtl_tcp 端点｜"
            "可跑：在跑着 rtl_tcp 的机器上设 "
            "MBDSDR_RTL_TCP=127.0.0.1:1234 后重跑｜"
            "区分：这是「没给端点」；「端点给了但服务不在跑」是下一条 skip，"
            "两者修法不同——前者配 env，后者起服务"
        )
    mgr = SDRBackendManager()
    rtl_ids = _rtl_tcp_ids()
    # env 已配置 → 登记是确定性的，算不出 id 就是契约坏了，必须红（不许 skip）
    assert rtl_ids, "MBDSDR_RTL_TCP 已配置却算不出 rtl_tcp 身份键——登记契约被改坏"
    dev = [d for d in mgr.list_devices() if d["device_id"] in rtl_ids]
    assert dev, f"env 已配置 {entries} 但设备表里没有对应条目（契约问题，不许 skip）"
    # 契约即 docstring：无服务时 honest skip（开发机 env 全局挂着但服务器不在跑）
    import socket
    _host, _port = entries[0]
    try:
        _probe = socket.create_connection((_host, _port), timeout=2)
        _probe.close()
    except OSError as e:
        pytest.skip(
            f"缺：MBDSDR_RTL_TCP 已配为 {_host}:{_port}，但该端口连不上"
            f"（rtl_tcp 服务没起）: {e}｜"
            f"可跑：先在该机启动 `rtl_tcp -a {_host} -p {_port}` 再重跑｜"
            "区分：端点已给、服务未起 = skip；服务在跑但握手/收发失败会在下面的 "
            "assert 里红，不会落到这条"
        )
    assert mgr.connect(dev[0]["device_id"]) is True
    try:
        assert mgr.get_active() is not None
        assert mgr.get_active().status.connected
    finally:
        mgr.disconnect()


# ---------------------------------------------------------------------------
# device_manager 层：MCP controller 走的正是这条路径
# （controller.connect -> DeviceManager.list_devices/open -> OpenDevice）
# ---------------------------------------------------------------------------

def test_device_manager_registers_rtl_tcp(monkeypatch):
    from mbdsdr_ai.device_manager import DeviceManager
    monkeypatch.setenv(RTL_TCP_ENV, "127.0.0.1:1234")
    dm = DeviceManager()
    devs = dm.list_devices()
    rtl = [d for d in devs if d.driver == "rtltcp"]
    assert len(rtl) == 1
    assert rtl[0].serial == "rtl_tcp_127.0.0.1_1234"
    assert rtl[0].endpoint == ("127.0.0.1", 1234)
    assert rtl[0].name == "rtl_tcp 127.0.0.1:1234"


def test_device_manager_without_env_has_no_rtltcp(monkeypatch):
    from mbdsdr_ai.device_manager import DeviceManager
    monkeypatch.delenv(RTL_TCP_ENV, raising=False)
    assert not [d for d in DeviceManager().list_devices() if d.driver == "rtltcp"]


def test_device_manager_garbage_env_registers_nothing(monkeypatch):
    from mbdsdr_ai.device_manager import DeviceManager
    monkeypatch.setenv(RTL_TCP_ENV, "host:notaport, :1, x:99999")
    assert not [d for d in DeviceManager().list_devices() if d.driver == "rtltcp"]


def test_device_manager_open_rtl_tcp_live():
    """端到端（rtl_tcp 服务在跑时）：DeviceManager.open -> OpenDevice 读到 IQ。"""
    if not parse_rtl_tcp_env(os.environ.get(RTL_TCP_ENV, "")):
        pytest.skip(
            "缺：环境变量 MBDSDR_RTL_TCP 未配置，没有可连的 rtl_tcp 端点｜"
            "可跑：在跑着 rtl_tcp 的机器上设 "
            "MBDSDR_RTL_TCP=127.0.0.1:1234 后重跑｜"
            "区分：这是「没给端点」；「端点给了但服务不在跑」是下面那条 skip，"
            "两者修法不同——前者配 env，后者起服务"
        )
    from mbdsdr_ai.device_manager import DeviceManager
    dm = DeviceManager()
    target = [d for d in dm.list_devices() if d.driver == "rtltcp"]
    # env 已配置 → 登记确定性；拿不到条目是契约问题，必须红（不许 skip）
    assert target, "MBDSDR_RTL_TCP 已配置但 DeviceManager 未登记 rtl_tcp 源（契约问题）"
    try:
        dev = dm.open(target[0])
    except (OSError, ConnectionError, TimeoutError) as e:
        pytest.skip(
            f"缺：MBDSDR_RTL_TCP={os.environ.get(RTL_TCP_ENV)} 指向的服务开不出流"
            f"（连接层失败）: {e}｜"
            "可跑：确认 rtl_tcp 在监听该端口、且没被别的客户端独占"
            "（rtl_tcp 是单客户端服务）｜"
            "区分：连不上/端口不可达 = skip（服务没起）；连上之后的契约性错误"
            "（字段缺失、类型不对等）不在上面捕获范围内，会直接红"
        )
    try:
        dev.set_sample_rate(2_048_000)
        dev.set_frequency(435_000_000)
        dev.set_gain(20.0)
        iq = dev.read_samples(8192)
        assert iq is not None and len(iq) == 8192
    finally:
        dev.close()


# ---------------------------------------------------------------------------
# sdr_connect 诚实语义：显式未知 device_id 必须拒绝，不许静默回落连第一台。
# 背景：此前 connect 在 device_id 未知时会静默连第一台后端并报成功——传
# "mock"/手滑 id 会连上另一台设备还不自知，违反 sdr_connect 契约
# 「只能连接 sdr_list_devices 枚举到的真实设备」。钉扎锁死诚实拒绝。
# ---------------------------------------------------------------------------

def _stub_manager(monkeypatch):
    """真管理器 + 诚实测试桩后端（实现 manager 所需最小接口，不冒充真实硬件）。

    同时把 ``_discover`` 钉成 no-op：开发机可能真插着 USB RTL-SDR（本机实测
    FC0012 在位时 RTLSDRBackend 会被登记进 backends 首位，connect() 自动连的
    就是真机而不是桩），delenv 只挡得住 rtl_tcp、挡不住 USB 发现。钉死后
    backends == {测试桩} 是唯一确定性世界。
    """
    from mbdsdr_ai.sdr_backend import SDRDevice, SDRStatus

    class _HonestStubBackend:
        def __init__(self):
            self.device = SDRDevice(
                device_type="debug", device_id="honest_stub_debug",
                name="HonestStub（测试桩）",
                frequency_range=(0.0, 6e9), sample_rate_range=(1e3, 1e7),
                max_gain=0.0)
            self.status = SDRStatus()
            self.connect_calls = 0

        def connect(self) -> bool:
            self.connect_calls += 1
            self.status.connected = True
            return True

        def disconnect(self):
            self.status.connected = False

        def readback_hw_state(self):
            return None

    monkeypatch.delenv(RTL_TCP_ENV, raising=False)
    monkeypatch.setattr(
        SDRBackendManager, "_discover",
        lambda self: None, raising=True)
    mgr = SDRBackendManager()
    stub = _HonestStubBackend()
    mgr.backends[stub.device.device_id] = stub
    return mgr, stub


def test_connect_unknown_device_id_honest_rejection(monkeypatch):
    """未知 device_id → 拒绝连接，不静默连第一台后端（含历史 bug id "mock"）。"""
    mgr, stub = _stub_manager(monkeypatch)
    assert mgr.connect("mock") is False
    assert mgr.connect("no_such_device") is False
    assert mgr.active_backend is None
    assert stub.connect_calls == 0   # 没有任何后端被打开


def test_connect_known_device_id_connects_that_backend(monkeypatch):
    mgr, stub = _stub_manager(monkeypatch)
    assert mgr.connect("honest_stub_debug") is True
    assert mgr.active_backend is stub


def test_connect_without_device_id_keeps_auto_connect_first(monkeypatch):
    """不传（None/""）→ 保留文档化行为：自动连接第一台后端。"""
    mgr, stub = _stub_manager(monkeypatch)
    assert mgr.connect() is True
    assert mgr.active_backend is stub
    assert mgr.connect("") is True


def test_connect_unknown_id_empty_backends_honest(monkeypatch):
    """裸环境（无后端）：不传 id 也必须诚实失败，绝不虚构连接。"""
    monkeypatch.delenv(RTL_TCP_ENV, raising=False)
    # 钉死 _discover 为 no-op：真插着 RTL-SDR 的开发机上发现会登记真后端，
    # connect() 自动连真机返回 True，与「无后端」的测试意图不符（环境假设）。
    monkeypatch.setattr(
        SDRBackendManager, "_discover",
        lambda self: None, raising=True)
    mgr = SDRBackendManager()
    assert mgr.connect() is False
    assert mgr.connect("anything") is False
    assert mgr.active_backend is None

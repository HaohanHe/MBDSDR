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
    parse_rtl_tcp_env,
)


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
    mgr = SDRBackendManager()
    dev = mgr.list_devices()
    rtl_tcp = [d for d in dev if d["device_id"] == "rtl_tcp_127.0.0.1_1234"]
    assert len(rtl_tcp) == 1
    d = rtl_tcp[0]
    assert d["type"] == "rtl_sdr"
    assert d["name"] == "rtl_tcp 127.0.0.1:1234"
    assert d["connected"] is False   # 登记 != 连接
    assert d["supports_iq"] is True


def test_discover_without_env_has_no_rtl_tcp_source(monkeypatch):
    monkeypatch.delenv(RTL_TCP_ENV, raising=False)
    mgr = SDRBackendManager()
    assert not [d for d in mgr.list_devices()
                if d["device_id"].startswith("rtl_tcp_")]


def test_registered_backend_connects_to_live_rtl_tcp():
    """端到端（有真实 rtl_tcp 服务时）：连本机 1234 的官方 rtl_tcp。

    未配置/无服务时跳过（honest skip，绝不假装连接成功）。
    """
    entries = parse_rtl_tcp_env(os.environ.get(RTL_TCP_ENV, ""))
    if not entries:
        pytest.skip("MBDSDR_RTL_TCP 未配置")
    mgr = SDRBackendManager()
    dev = [d for d in mgr.list_devices() if d["device_id"].startswith("rtl_tcp_")]
    if not dev:
        pytest.skip("rtl_tcp 源未登记")
    # 契约即 docstring：无服务时 honest skip（开发机 env 全局挂着但服务器不在跑）
    import socket
    _host, _port = entries[0]
    try:
        _probe = socket.create_connection((_host, _port), timeout=2)
        _probe.close()
    except OSError as e:
        pytest.skip(f"rtl_tcp 服务不在跑（honest skip）: {e}")
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
        pytest.skip("MBDSDR_RTL_TCP 未配置")
    from mbdsdr_ai.device_manager import DeviceManager
    dm = DeviceManager()
    target = [d for d in dm.list_devices() if d.driver == "rtltcp"]
    if not target:
        pytest.skip("rtl_tcp 源未登记")
    try:
        dev = dm.open(target[0])
    except Exception as e:
        pytest.skip(f"rtl_tcp 服务不在跑（honest skip）: {e}")
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

    先 delenv 再构造：裸口径运行（登记 == 测试桩一家的确定性世界），防止
    开发机 env 泄漏出 USB/rtl_tcp 后端混进 backends 首位。
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
    mgr = SDRBackendManager()
    assert mgr.connect() is False
    assert mgr.connect("anything") is False
    assert mgr.active_backend is None

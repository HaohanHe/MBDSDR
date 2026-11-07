# SPDX-License-Identifier: MIT
"""SDRBackendManager 设备表 TTL 刷新（审计 B 节 P1）。

病灶：_discover() 只在 __init__ 跑一次，list_devices() 就是吃冷饭——
热插拔的棒子永远进不了 MCP/agent 的视野（sdr_list_devices 永远返回旧表）。

契约（本轮钉扎）：
  - list_devices()/connect()/switch_device() 在 TTL 过期后重探（只增不减）；
  - 已注册实例永不替换（连接态/句柄不丢）；
  - TTL 窗口内不重探（枚举是真实 USB open/close，不许当轮询用）；
  - 只增不减：探测失败 ≠ 不存在（占用中的棒子探测必失败），不摘除旧条目。

假件：sys.modules 注入 fake rtlsdr 控制探测成败；RTLSDRBackend 换成
假实现（真实 connect 会碰 pyrtlsdr）。无硬件、无网络、确定性。
"""
import sys
import types

import pytest

from mbdsdr_ai import sdr_backend as sdb
from mbdsdr_ai.sdr_backend import (
    NETWORK_IQ_ENV, RTL_TCP_ENV, SPYSERVER_ENV, SDRBackend,
    SDRBackendManager, SDRDevice,
)


class _ProbeState:
    def __init__(self):
        self.present = False
        self.probes = 0


STATE = _ProbeState()


class _FakeRtlSdr:
    def __init__(self, index=0):
        STATE.probes += 1
        if not STATE.present:
            raise OSError("no device attached")

    def close(self):
        pass


class FakeRTLBackend(SDRBackend):
    """假 RTL 后端：设备元数据同真形，connect 不碰硬件。"""
    serial = 0

    def __init__(self, device_index=0, host=None, port=1234):
        if host is None:
            did = f"rtl_{device_index}"
            name = f"fake rtl {device_index}"
        else:
            did = f"rtl_tcp_{host}_{port}"
            name = f"fake rtl_tcp {host}:{port}"
        dev = SDRDevice(
            device_type="rtl_sdr",
            device_id=did,
            name=name,
            frequency_range=(24_000_000, 1_766_000_000),
            sample_rate_range=(225_000, 3_200_000),
            max_gain=49.6,
            supports_iq=True,
            supports_tx=False,
        )
        super().__init__(dev)
        FakeRTLBackend.serial += 1
        self.serial = FakeRTLBackend.serial

    def connect(self):
        self.status.connected = True
        self.status.link_state = "connected"
        return True

    def disconnect(self):
        self.status.connected = False
        self.status.link_state = "disconnected"


@pytest.fixture
def mgr_env(monkeypatch):
    """干净枚举环境：fake rtlsdr + 假 RTL 后端 + 无网络源 + 无 Pluto/USRP/HackRF。"""
    STATE.present = False
    STATE.probes = 0
    FakeRTLBackend.serial = 0

    fake_mod = types.ModuleType("rtlsdr")
    fake_mod.RtlSdr = _FakeRtlSdr
    monkeypatch.setitem(sys.modules, "rtlsdr", fake_mod)
    monkeypatch.setattr(sdb, "RTLSDRBackend", FakeRTLBackend)
    monkeypatch.setattr(sdb.PlutoSDRBackend, "enumerate",
                        staticmethod(lambda: []))
    monkeypatch.setenv(RTL_TCP_ENV, "")
    monkeypatch.setenv(SPYSERVER_ENV, "")
    monkeypatch.setenv(NETWORK_IQ_ENV, "")
    yield STATE
    STATE.present = False


def _expire(mgr):
    """把 TTL 拨过期（不依赖时钟注入）。"""
    mgr._last_discover_ts = 0.0


class TestHotplugVisibility:
    def test_hotplugged_device_appears_after_ttl(self, mgr_env):
        mgr = SDRBackendManager()
        assert mgr.list_devices() == []          # 启动时没棒子
        STATE.present = True                     # 热插拔插上
        _expire(mgr)
        ids = [d["device_id"] for d in mgr.list_devices()]
        assert "rtl_0" in ids                    # 判据：过期后重探必须看见

    def test_within_ttl_does_not_reprobe(self, mgr_env):
        STATE.present = True
        mgr = SDRBackendManager()                # __init__ 探测一次
        assert STATE.probes == 1
        mgr.list_devices()
        mgr.list_devices()
        assert STATE.probes == 1                 # TTL 窗口内零重探

    def test_connect_unknown_id_triggers_refresh(self, mgr_env):
        mgr = SDRBackendManager()
        assert mgr.list_devices() == []
        STATE.present = True
        _expire(mgr)
        # 不先 list_devices，直接 connect 热插拔来的设备——必须先刷新再判成员
        assert mgr.connect("rtl_0") is True
        assert mgr.active_backend is not None


class TestRefreshSafety:
    def test_refresh_keeps_existing_instance(self, mgr_env):
        STATE.present = True
        mgr = SDRBackendManager()
        first = mgr.backends["rtl_0"]
        first.status.connected = True             # 模拟正在使用
        _expire(mgr)
        mgr.list_devices()
        assert mgr.backends["rtl_0"] is first     # 实例永不替换
        assert mgr.backends["rtl_0"].status.connected is True

    def test_refresh_does_not_reprobe_registered_rtl(self, mgr_env):
        STATE.present = True
        mgr = SDRBackendManager()
        assert STATE.probes == 1
        _expire(mgr)
        mgr.list_devices()
        assert STATE.probes == 1                 # 已登记就不再开棒子

    def test_additive_only_unplugged_stays_listed(self, mgr_env):
        STATE.present = True
        mgr = SDRBackendManager()
        assert "rtl_0" in [d["device_id"] for d in mgr.list_devices()]
        STATE.present = False                    # 拔掉
        _expire(mgr)
        ids = [d["device_id"] for d in mgr.list_devices()]
        assert "rtl_0" in ids                    # 只增不减：不摘除（探测失败≠不存在）

    def test_env_sources_survive_refresh(self, mgr_env, monkeypatch):
        monkeypatch.setenv(RTL_TCP_ENV, "127.0.0.1:1234")
        STATE.present = True
        mgr = SDRBackendManager()
        assert "rtl_tcp_127.0.0.1_1234" in [d["device_id"] for d in mgr.list_devices()]
        _expire(mgr)
        ids = [d["device_id"] for d in mgr.list_devices()]
        assert "rtl_tcp_127.0.0.1_1234" in ids   # env 登记源同样不被刷新冲掉
        assert "rtl_0" in ids

    def test_refresh_is_idempotent(self, mgr_env):
        STATE.present = True
        mgr = SDRBackendManager()
        _expire(mgr)
        mgr.list_devices()
        _expire(mgr)
        mgr.list_devices()
        ids = [d["device_id"] for d in mgr.list_devices()]
        assert ids.count("rtl_0") == 1           # 反复刷新不叠条目

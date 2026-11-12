# SPDX-License-Identifier: MIT
"""SDRBackendManager 设备表 TTL 刷新（审计 B 节 P1）。

病灶：_discover() 只在 __init__ 跑一次，list_devices() 就是吃冷饭——
热插拔的棒子永远进不了 MCP/agent 的视野（sdr_list_devices 永远返回旧表）。

契约（本轮钉扎，2026-11-12 改）：
  - 设备表由 ``enumerate_all_sdr_devices()`` **唯一驱动**：AI 与桌面端必须
    对同一根棒算出同一个身份键，否则「桌面端占着、AI 被拒」永远不成立；
  - list_devices()/connect() 在 TTL 过期后重新枚举（只增不减）；
  - 已注册实例永不重建（连接态/句柄不丢）——枚举重跑不等于重建后端；
  - TTL 窗口内不枚举（枚举会真开 USB，不许当轮询用）；
  - 只增不减：探测失败 ≠ 不存在（占用中的棒子探测必失败），不摘除旧条目。

假件：``RTLSDRBackend`` 换成只提供 ``list_devices()``/``__init__`` 的假实现，
枚举数量由 ``STATE.serials`` 控制——无硬件、无网络、确定性。真 USB 枚举
（含 tuner 探测）不参与，所以这些用例在真机在位/不在位都一样跑。
"""
import pytest

from mbdsdr_ai import sdr_backend as sdb
from mbdsdr_ai.sdr_backend import (
    NETWORK_IQ_ENV, RTL_TCP_ENV, SPYSERVER_ENV, SDRBackend,
    SDRBackendManager, SDRDevice,
)


class _ProbeState:
    def __init__(self):
        self.serials = []        # 当前"插着"的棒子（枚举结果）
        self.enumerations = 0    # 真跑了几次 USB 枚举


STATE = _ProbeState()

#: 枚举序号 → 身份键的固定口径（改键格式必须同步改这里，属于契约变更）。
KEY_SERIAL = "rtlsdr|serial:00000042"
KEY_RTL_TCP = "rtlsdr|serial:rtl_tcp_127.0.0.1_1234"


class FakeRTLBackend(SDRBackend):
    """假 RTL 后端：设备元数据同真形，connect 不碰硬件。"""

    def __init__(self, device_index=0, host=None, port=1234, ppm=0):
        if host is None:
            did = f"placeholder_rtl_{device_index}"
            name = f"fake rtl {device_index}"
        else:
            did = f"placeholder_rtl_tcp_{host}_{port}"
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

    @staticmethod
    def list_devices():
        """枚举形状与真 RTLSDRBackend.list_devices() 一致（index/serial/tuner）。"""
        STATE.enumerations += 1
        return [{"index": i, "serial": s, "tuner": "FakeTuner"}
                for i, s in enumerate(STATE.serials)]

    def connect(self):
        self.status.connected = True
        self.status.link_state = "connected"
        return True

    def disconnect(self):
        self.status.connected = False
        self.status.link_state = "disconnected"


@pytest.fixture
def mgr_env(monkeypatch):
    """干净枚举环境：假 RTL 枚举 + 无网络源 + 无 Pluto/USRP/HackRF。"""
    STATE.serials = []
    STATE.enumerations = 0

    monkeypatch.setattr(sdb, "RTLSDRBackend", FakeRTLBackend)
    monkeypatch.setattr(sdb.PlutoSDRBackend, "enumerate",
                        staticmethod(lambda: []))
    # osmosdr 侧不经 sdb.RTLSDRBackend，直接给空枚举（无库时本来也是空）。
    from mbdsdr_ai import osmosdr_source as _osmo
    monkeypatch.setattr(_osmo.DeviceEnumerator, "enumerate",
                        staticmethod(lambda: []))
    monkeypatch.setenv(RTL_TCP_ENV, "")
    monkeypatch.setenv(SPYSERVER_ENV, "")
    monkeypatch.setenv(NETWORK_IQ_ENV, "")
    yield STATE
    STATE.serials = []


def _expire(mgr):
    """把 TTL 拨过期（不依赖时钟注入）。"""
    mgr._last_discover_ts = 0.0


def _ids(mgr):
    return [d["device_id"] for d in mgr.list_devices()]


class TestHotplugVisibility:
    def test_hotplugged_device_appears_after_ttl(self, mgr_env):
        mgr = SDRBackendManager()
        assert mgr.list_devices() == []          # 启动时没棒子
        STATE.serials = ["00000042"]             # 热插拔插上
        _expire(mgr)
        assert KEY_SERIAL in _ids(mgr)           # 判据：过期后重探必须看见

    def test_within_ttl_does_not_reprobe(self, mgr_env):
        STATE.serials = ["00000042"]
        mgr = SDRBackendManager()                # __init__ 枚举一次
        assert STATE.enumerations == 1
        mgr.list_devices()
        mgr.list_devices()
        assert STATE.enumerations == 1           # TTL 窗口内零重探

    def test_connect_unknown_id_triggers_refresh(self, mgr_env):
        mgr = SDRBackendManager()
        assert mgr.list_devices() == []
        STATE.serials = ["00000042"]
        _expire(mgr)
        # 不先 list_devices，直接 connect 热插拔来的设备——必须先刷新再判成员
        assert mgr.connect(KEY_SERIAL) is True
        assert mgr.active_backend is not None


class TestRefreshSafety:
    def test_refresh_keeps_existing_instance(self, mgr_env):
        STATE.serials = ["00000042"]
        mgr = SDRBackendManager()
        first = mgr.backends[KEY_SERIAL]
        first.status.connected = True             # 模拟正在使用
        _expire(mgr)
        mgr.list_devices()
        assert mgr.backends[KEY_SERIAL] is first  # 实例永不重建（句柄不丢）
        assert mgr.backends[KEY_SERIAL].status.connected is True

    def test_refresh_reenumerates_but_does_not_duplicate(self, mgr_env):
        """TTL 到期必然重新枚举（身份键只能对整份枚举算），但不得叠条目。"""
        STATE.serials = ["00000042"]
        mgr = SDRBackendManager()
        assert STATE.enumerations == 1
        _expire(mgr)
        mgr.list_devices()
        assert STATE.enumerations == 2
        assert _ids(mgr).count(KEY_SERIAL) == 1

    def test_additive_only_unplugged_stays_listed(self, mgr_env):
        STATE.serials = ["00000042"]
        mgr = SDRBackendManager()
        assert KEY_SERIAL in _ids(mgr)
        STATE.serials = []                       # 拔掉
        _expire(mgr)
        assert KEY_SERIAL in _ids(mgr)           # 只增不减：探测失败≠不存在

    def test_env_sources_survive_refresh(self, mgr_env, monkeypatch):
        monkeypatch.setenv(RTL_TCP_ENV, "127.0.0.1:1234")
        STATE.serials = ["00000042"]
        mgr = SDRBackendManager()
        assert KEY_RTL_TCP in _ids(mgr)
        assert KEY_SERIAL in _ids(mgr)
        _expire(mgr)
        ids = _ids(mgr)
        assert KEY_RTL_TCP in ids                # env 登记源同样不被刷新冲掉
        assert KEY_SERIAL in ids

    def test_refresh_is_idempotent(self, mgr_env):
        STATE.serials = ["00000042"]
        mgr = SDRBackendManager()
        _expire(mgr)
        mgr.list_devices()
        _expire(mgr)
        mgr.list_devices()
        assert _ids(mgr).count(KEY_SERIAL) == 1  # 反复刷新不叠条目

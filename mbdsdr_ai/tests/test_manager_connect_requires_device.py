# SPDX-License-Identifier: MIT
"""connect() 不许「碰巧取第一」：无 device_id / 无 active / 无历史 → 必须拒绝。

反证（对应用户验收口径）：
- A：两个后端、无 device_id、无 active、无历史记录 → 不激活任何一个，
      返回 False，提示里带两个设备的索引 + 名称 + device_id；
- B：有历史记录 → 沿用那台，且提示里写明沿用的设备名；
- C：历史记录指向已不在枚举表中的设备 → 拒绝，并列出当前可用设备。

同时校验工具层 sdr_connect 的文案（不只是「未指定设备」）。
"""
import time

import pytest

from mbdsdr_ai.config import ConfigStore
from mbdsdr_ai.sdr_backend import SDRBackend, SDRBackendManager, SDRDevice
from mbdsdr_ai.sdr_tools import _sdr_connect


def _device(device_id: str, name: str) -> SDRDevice:
    return SDRDevice(
        device_type="fake",
        device_id=device_id,
        name=name,
        frequency_range=(24e6, 1.7e9),
        sample_rate_range=(1e6, 2.4e6),
        max_gain=40.0,
    )


class _FakeBackend(SDRBackend):
    """只记录 connect() 调用次数的假后端，不碰任何硬件。"""

    def __init__(self, device: SDRDevice):
        super().__init__(device)
        self.connect_calls = 0

    def connect(self) -> bool:
        self.connect_calls += 1
        self.status.connected = True
        return True


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    # 构造期不探测真实 USB 设备；本次测试只关心 connect 的选设备逻辑。
    monkeypatch.setattr(SDRBackendManager, "_discover", lambda self: None)
    m = SDRBackendManager()
    m._last_discover_ts = time.monotonic()   # TTL 内不重探
    a = _FakeBackend(_device("fake_a", "假设备A"))
    b = _FakeBackend(_device("fake_b", "假设备B"))
    m.backends = {"fake_a": a, "fake_b": b}
    m.active_backend = None
    # 每个用例独立 config.json，默认无历史记录。
    m._cfg_store = ConfigStore(str(tmp_path / "config.json"))
    return m, a, b


def test_no_device_no_active_no_history_activates_nothing(mgr):
    m, a, b = mgr
    assert m.connect() is False
    assert m.active_backend is None
    assert a.connect_calls == 0
    assert b.connect_calls == 0
    notice = m.last_connect_notice
    # 提示必须可操作：两台设备的名字、索引、device_id 都在
    assert "假设备A" in notice and "fake_a" in notice
    assert "假设备B" in notice and "fake_b" in notice
    assert "1)" in notice and "2)" in notice


def test_history_is_reused_and_named(mgr):
    m, a, b = mgr
    m._cfg_store.update(
        lambda d: d.__setitem__(
            SDRBackendManager._LAST_DEVICE_KEY,
            {"device_id": "fake_b", "name": "假设备B"}) or True)
    assert m.connect() is True
    assert m.active_backend is b
    assert b.connect_calls == 1
    assert a.connect_calls == 0
    assert "假设备B" in m.last_connect_notice
    assert "fake_b" in m.last_connect_notice


def test_stale_history_refused_with_choices(mgr):
    m, a, b = mgr
    m._cfg_store.update(
        lambda d: d.__setitem__(
            SDRBackendManager._LAST_DEVICE_KEY,
            {"device_id": "fake_gone", "name": "已拔掉的设备"}) or True)
    assert m.connect() is False
    assert m.active_backend is None
    assert a.connect_calls == 0 and b.connect_calls == 0
    notice = m.last_connect_notice
    assert "fake_gone" in notice
    assert "假设备A" in notice and "假设备B" in notice


def test_tool_layer_failure_text_lists_device_names(mgr):
    m, _a, _b = mgr
    res = _sdr_connect(m, None)
    assert res.success is False
    assert "假设备A" in res.content and "fake_a" in res.content
    assert "假设备B" in res.content and "fake_b" in res.content
    assert "1)" in res.content and "2)" in res.content
    assert res.content != "未指定设备"


def test_tool_layer_success_text_names_reused_device(mgr):
    m, _a, b = mgr
    m._cfg_store.update(
        lambda d: d.__setitem__(
            SDRBackendManager._LAST_DEVICE_KEY,
            {"device_id": "fake_b", "name": "假设备B"}) or True)
    res = _sdr_connect(m, None)
    assert res.success is True
    assert "假设备B" in res.content
    assert "沿用" in res.content

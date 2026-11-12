# SPDX-License-Identifier: MIT
"""DeviceManager 确定性测试。

对照 mbdsdr_ai/device_manager.py（上游 sdrpp source.h/source.cpp）。
红线：无设备时 list_devices() 返回 []，不造假。

身份契约（2026-11-13 起）：
  * 序列号是唯一身份；枚举顺序变化后，同一序列号仍是同一台。
  * 序列号读不到时 serial 为空串、has_stable_identity=False（身份不可分辨），
    绝不用 "rtl-0"/"soapysdr-0" 这类枚举序号冒充身份；enum_index 只作定位。
"""
import sys
import time
import types

import pytest

from mbdsdr_ai.device_manager import (
    DeviceDisconnectedError,
    DeviceInfo,
    DeviceManager,
    DeviceNotFoundError,
)


def test_list_devices_never_fake():
    """无硬件/无驱动时 list_devices() 必须返回 []，不造假。"""
    dm = DeviceManager()
    devs = dm.list_devices()
    assert isinstance(devs, list)
    # 不强制为 []（CI 可能真有硬件），但每个元素必须是 DeviceInfo
    for d in devs:
        assert isinstance(d, DeviceInfo)
        assert d.driver
        # 要么有真序列号，要么明确标成不可分辨（不能用序号拼假序列号）
        assert d.serial or not d.has_stable_identity
        if not d.has_stable_identity:
            assert d.enum_index >= 0, "无序列号时必须保留本次枚举位置以便定位"


def test_device_info_dataclass():
    d = DeviceInfo(
        name="RTL-SDR #0",
        driver="rtlsdr",
        serial="1234",
        sample_rates=(2_048_000.0,),
        gains=(0.0, 20.0),
    )
    assert d.device_key == "rtlsdr:1234"
    assert d.sample_rates == (2_048_000.0,)


def test_open_nonexistent_raises():
    """打开一个不在枚举里的设备必须抛 DeviceNotFoundError。"""
    dm = DeviceManager()
    fake = DeviceInfo(name="fake", driver="rtlsdr", serial="NO_SUCH_SERIAL")
    with pytest.raises(DeviceNotFoundError):
        dm.open(fake)


def test_monitor_callback_no_crash():
    """热插拔轮询线程能启动/停止，无设备时不崩。"""
    dm = DeviceManager(poll_interval=0.1)
    events = []
    dm.start_monitor(lambda ev: events.append(ev))
    time.sleep(0.3)
    dm.stop_monitor()
    # 无设备增删时 events 为空；有变化时会有记录
    assert isinstance(events, list)


def test_disconnected_error_exception_type():
    """DeviceDisconnectedError 是 DeviceError 子类。"""
    from mbdsdr_ai.device_manager import DeviceError
    assert issubclass(DeviceDisconnectedError, DeviceError)


# ---------------------------------------------------------------------------
# A13：身份必须是跟着设备走的事实（序列号），不是枚举顺序
# ---------------------------------------------------------------------------

def _rtl_items(*pairs):
    return [{"index": i, "serial": s, "tuner": "R820T"} for i, s in pairs]


def test_same_serial_is_same_device_after_reorder(monkeypatch):
    """插拔/换口导致枚举顺序翻转后，同一序列号必须仍是同一台（device_key 不变）。"""
    from mbdsdr_ai import sdr_backend

    first = _rtl_items((0, "00000001"), (1, "00000002"))
    flipped = _rtl_items((0, "00000002"), (1, "00000001"))
    monkeypatch.setattr(
        sdr_backend.RTLSDRBackend, "list_devices", staticmethod(lambda: first)
    )
    dm = DeviceManager()
    before = {d.serial: d for d in dm._probe_rtlsdr()}

    monkeypatch.setattr(
        sdr_backend.RTLSDRBackend, "list_devices", staticmethod(lambda: flipped)
    )
    after = {d.serial: d for d in dm._probe_rtlsdr()}

    assert before["00000001"].device_key == after["00000001"].device_key == "rtlsdr:00000001"
    assert before["00000002"].device_key == after["00000002"].device_key == "rtlsdr:00000002"
    # 顺序确实变了：同一台的 enum_index 变了，但身份没变
    assert before["00000001"].enum_index == 0
    assert after["00000001"].enum_index == 1
    assert all(d.has_stable_identity for d in after.values())


def test_missing_serial_is_indistinguishable_not_invented(monkeypatch):
    """反证：读不到序列号时必须是空串 + has_stable_identity=False。

    若把 `serial = ... or ("rtl-%d" % idx)` 之类的序号兜底加回来，
    这里的 serial/device_key 断言会立刻变红。
    """
    from mbdsdr_ai import sdr_backend

    items = [
        {"index": 0, "serial": "", "tuner": ""},
        {"index": 1, "serial": None, "tuner": ""},
    ]
    monkeypatch.setattr(
        sdr_backend.RTLSDRBackend, "list_devices", staticmethod(lambda: items)
    )
    dm = DeviceManager()
    devs = dm._probe_rtlsdr()

    assert [d.serial for d in devs] == ["", ""]
    assert all(not d.has_stable_identity for d in devs)
    assert [d.device_key for d in devs] == ["rtlsdr|index:0", "rtlsdr|index:1"]
    assert "rtl-0" not in devs[0].device_key
    assert "身份不可分辨" in devs[0].identity_warning
    # 可用身份的设备不提示
    good = DeviceInfo(name="RTL-SDR #0", driver="rtlsdr", serial="SN-1")
    assert good.identity_warning == ""


def test_soapysdr_missing_serial_is_indistinguishable(monkeypatch):
    """SoapySDR 这条路径同样不许用 label/序号兜底当序列号。"""
    fake_soapy = types.ModuleType("SoapySDR")

    class _Device:
        def __init__(self, args):
            raise RuntimeError("probe-only fake")

        @staticmethod
        def enumerate():
            return [
                {"driver": "hackrf", "label": "HackRF One"},
                {"driver": "hackrf", "label": "HackRF One", "serial": "ABC123"},
            ]

    fake_soapy.Device = _Device
    fake_soapy.DIRECTION_RX = 2
    monkeypatch.setitem(sys.modules, "SoapySDR", fake_soapy)

    devs = DeviceManager()._probe_soapysdr()
    assert [d.serial for d in devs] == ["", "ABC123"]
    assert devs[0].has_stable_identity is False
    assert devs[1].has_stable_identity is True
    assert "hackrf" not in devs[0].serial


def test_open_rtlsdr_follows_serial_not_enumeration_position(monkeypatch):
    """打开时按序列号定位：枚举顺序翻转后仍打开同一台，而不是旧序号那一台。"""
    from mbdsdr_ai import sdr_backend

    flipped = _rtl_items((0, "SN-B"), (1, "SN-A"))
    monkeypatch.setattr(
        sdr_backend.RTLSDRBackend, "list_devices", staticmethod(lambda: flipped)
    )
    opened = []

    class _FakeRtlSdr:
        def __init__(self, device_index=0):
            opened.append(device_index)

    fake_rtlsdr = types.ModuleType("rtlsdr")
    fake_rtlsdr.RtlSdr = _FakeRtlSdr
    monkeypatch.setitem(sys.modules, "rtlsdr", fake_rtlsdr)

    # 旧快照里 SN-A 在位置 0；翻转后它在位置 1
    info = DeviceInfo(name="RTL-SDR #0", driver="rtlsdr", serial="SN-A", enum_index=0)
    DeviceManager().open(info)
    assert opened == [1], "必须按序列号定位到新的枚举位置"


def test_open_missing_serial_is_positional_and_visible(monkeypatch, caplog):
    """无序列号时可以按本次枚举位置打开，但必须留下「无法确认是不是同一台」的警告。"""
    from mbdsdr_ai import sdr_backend

    items = [
        {"index": 0, "serial": "", "tuner": ""},
        {"index": 1, "serial": "", "tuner": ""},
    ]
    monkeypatch.setattr(
        sdr_backend.RTLSDRBackend, "list_devices", staticmethod(lambda: items)
    )
    opened = []

    class _FakeRtlSdr:
        def __init__(self, device_index=0):
            opened.append(device_index)

    fake_rtlsdr = types.ModuleType("rtlsdr")
    fake_rtlsdr.RtlSdr = _FakeRtlSdr
    monkeypatch.setitem(sys.modules, "rtlsdr", fake_rtlsdr)

    info = DeviceInfo(name="RTL-SDR #1", driver="rtlsdr", serial="", enum_index=1)
    DeviceManager().open(info)
    assert opened == [1]
    assert "无法确认是不是同一台" in caplog.text

    # 位置已不存在：明确报不可分辨，不许猜一个
    stale = DeviceInfo(name="RTL-SDR #9", driver="rtlsdr", serial="", enum_index=9)
    with pytest.raises(DeviceNotFoundError, match="身份不可分辨"):
        DeviceManager().open(stale)


def test_open_refuses_unidentifiable_without_position():
    """既无序列号又无枚举位置：直接报不可分辨，不进硬件初始化。"""
    info = DeviceInfo(name="ghost", driver="rtlsdr", serial="", enum_index=-1)
    with pytest.raises(DeviceNotFoundError, match="身份不可分辨"):
        DeviceManager().open(info)


class _SequenceDM(DeviceManager):
    """按脚本依次返回不同枚举快照，用于热插拔测试。"""

    def __init__(self, snapshots):
        super().__init__(poll_interval=0.05)
        self._snapshots = list(snapshots)
        self._cursor = 0

    def list_devices(self):
        idx = min(self._cursor, len(self._snapshots) - 1)
        self._cursor += 1
        return list(self._snapshots[idx])


def test_monitor_removed_event_keeps_real_identity():
    """拔掉设备时回调里带的是上一轮真实快照，不是从 device_key 反拼的假身份。"""
    dev = DeviceInfo(name="RTL-SDR #0", driver="rtlsdr", serial="SN-A", enum_index=0)
    dm = _SequenceDM([[dev], []])
    events = []
    dm.start_monitor(events.append)
    deadline = time.time() + 3.0
    while not events and time.time() < deadline:
        time.sleep(0.02)
    dm.stop_monitor()

    assert events, "设备消失必须触发 removed 回调"
    removed = events[0]["removed"]
    assert len(removed) == 1
    assert removed[0].serial == "SN-A", "不得把 device_key 反拼成 serial"
    assert removed[0].device_key == "rtlsdr:SN-A"

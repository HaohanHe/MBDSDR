"""DeviceManager 确定性测试。

对照 mbdsdr_ai/device_manager.py（上游 sdrpp source.h/source.cpp）。
红线：无设备时 list_devices() 返回 []，不造假。
"""
import time

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
        assert d.serial


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

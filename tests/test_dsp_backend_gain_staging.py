# SPDX-License-Identifier: MIT
"""
增益分级 LNA/Mixer/VGA 测试
===========================

对照上游：
- gqrx/src/receivers/nbrx.cpp:47-49  信号链 filter→meter→sql→agc
- SoapySDR Device.hpp:695 listGains / :708 setGainMode / :725 setGain(elem)

用 mock 设备（duck-type SoapySDR.Device）断言：
1. set_lna(x)/set_mixer(y)/set_vga(z) 分别调用 setGain(elem=对应元素名)
2. 增益范围从设备 getGainRange 查询得到，不硬编码
3. 设备不支持某档时 set_* 返回 False，不造假成功
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.gain_staging import GainStager


class _MockArgRange:
    def __init__(self, lo, hi):
        self.minimum = lo
        self.maximum = hi


class MockSoapyDevice:
    """模拟 SoapySDR.Device：记录所有 setGain 调用。"""

    def __init__(self, elements: dict):
        # elements: {"LNA": (0, 40), "IF": (0, 45), "VGA": (0, 62)}
        self.elements = elements
        self.calls = []   # 记录 (value, elem_name)

    def listGains(self, direction, channel):
        return list(self.elements.keys())

    def getGainRange(self, direction, channel, name):
        lo, hi = self.elements[name]
        return _MockArgRange(lo, hi)

    def setGain(self, direction, channel, value, name=""):
        self.calls.append((float(value), str(name)))


def test_gain_staging_three_stages_called_separately():
    """mock 设备有 LNA/IF/VGA 三档，set_lna/mixer/vga 分别下发到对应元素。"""
    dev = MockSoapyDevice({"LNA": (0, 40), "IF": (0, 45), "VGA": (0, 62)})
    gs = GainStager(dev, direction=0, channel=0)

    assert gs.set_lna(20.0) is True
    assert gs.set_mixer(30.0) is True
    assert gs.set_vga(40.0) is True

    # 三个调用分别落到了不同元素名
    elems_called = sorted(c[1] for c in dev.calls)
    assert elems_called == ["IF", "LNA", "VGA"], f"调用记录: {dev.calls}"

    # 数值也对
    d = {c[1]: c[0] for c in dev.calls}
    assert d["LNA"] == 20.0
    assert d["IF"] == 30.0
    assert d["VGA"] == 40.0


def test_gain_staging_clamps_to_device_range():
    """越界增益被 clamp 到设备查询得到的范围（不硬编码）。"""
    dev = MockSoapyDevice({"LNA": (0, 40), "IF": (0, 45), "VGA": (0, 62)})
    gs = GainStager(dev, direction=0, channel=0)

    gs.set_lna(100.0)       # 远超 40
    gs.set_vga(-5.0)        # 低于 0
    d = {c[1]: c[0] for c in dev.calls}
    assert d["LNA"] == 40.0, f"LNA 应被 clamp 到 40，实际 {d['LNA']}"
    assert d["VGA"] == 0.0, f"VGA 应被 clamp 到 0，实际 {d['VGA']}"


def test_gain_staging_unsupported_stage():
    """设备没有 VGA 档时 set_vga 返回 False，不造假成功。"""
    dev = MockSoapyDevice({"LNA": (0, 40), "IF": (0, 45)})  # 无 VGA
    gs = GainStager(dev, direction=0, channel=0)

    assert gs.is_supported("VGA") is False
    assert gs.set_vga(10.0) is False
    # 但 LNA/Mixer 仍正常
    assert gs.set_lna(10.0) is True
    assert gs.set_mixer(10.0) is True


def test_gain_staging_query_range_from_device():
    """范围来自设备 getGainRange，不是硬编码。"""
    # 用一个反常范围（LNA 0..100，VGA 0..10）验证我们没硬编码
    dev = MockSoapyDevice({"LNA": (0, 100), "VGA": (0, 10)})
    gs = GainStager(dev, direction=0, channel=0)
    info = gs.describe()
    assert info["LNA"]["max_db"] == 100.0
    assert info["VGA"]["max_db"] == 10.0


def test_gain_staging_alias_resolution():
    """设备元素名 "MIX" 应被识别为 Mixer 档（别名映射）。"""
    dev = MockSoapyDevice({"LNA": (0, 40), "MIX": (0, 30), "BB": (0, 40)})
    gs = GainStager(dev, direction=0, channel=0)
    assert gs.stages["Mixer"].elem_name == "MIX"
    assert gs.stages["VGA"].elem_name == "BB"

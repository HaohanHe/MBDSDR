# SPDX-License-Identifier: MIT
"""hal.SoapySDRBackend.list_devices 的设备身份行为测试（A13）
==============================================================

身份口径（唯一事实源在 ``mbdsdr_ai/device_identity.py``，
``device_manager.DeviceInfo.has_stable_identity`` 同口径）：

* 稳定身份只取序列号这类跟着设备走的唯一事实；
* label 仅作显示，可改可重名，不能当身份；
* 枚举下标是「这一次的位置」，拔插/换口就会变，绝不能当身份。

旧实现 ``name = result.get("label", f"{driver}-{i}")`` 拿枚举下标兜底：
两台同 driver 设备交换枚举顺序后名字互换，任何按名字挑选设备的流程
都会连到另一台物理设备（A13 loss=3）。本文件钉住修复后的契约：

1. 同 driver、无 label、有 serial 的两台设备：交换枚举顺序后，
   身份（名字/可用性）仍绑定 serial，不跟位置走；
2. 名字缺 label 时由稳定事实（serial）派生，而不是位置；
3. 缺 serial：不生成 ``driver-0/1`` 伪身份——条目仍在列表里可见，
   但标为不可用并给出可归因提示；
4. label 仅作显示：有 serial 时显示名可用 label，身份事实仍是 serial。

假 SoapySDR 模块注入 ``sys.modules``（hal 在方法内 ``import SoapySDR``），
不依赖真实硬件与真实 SoapySDR 库。
"""
import re
import sys
import types

import pytest

import mbdsdr_ai.hal as hal


def _install_fake_soapy(monkeypatch, results):
    """注入假 SoapySDR 模块：Device.enumerate() 返回给定 dict 列表。"""
    mod = types.ModuleType("SoapySDR")

    class _Device:
        @staticmethod
        def enumerate(args=None):
            return [dict(r) for r in results]

    mod.Device = _Device
    monkeypatch.setitem(sys.modules, "SoapySDR", mod)


def _rtl(serial, label=""):
    entry = {"driver": "rtlsdr", "manufacturer": "Realtek",
             "product": "RTL2838UHIDIR"}
    if serial is not None:
        entry["serial"] = serial
    if label:
        entry["label"] = label
    return entry


def test_identity_stays_bound_to_serial_when_enum_order_swaps(monkeypatch):
    """同 driver、无 label、有 serial 的两台：交换枚举顺序，身份跟 serial 走。"""
    a, b = _rtl("SN-AAAA"), _rtl("SN-BBBB")

    _install_fake_soapy(monkeypatch, [a, b])
    first = {d.serial: d for d in hal.SoapySDRBackend().list_devices()}

    _install_fake_soapy(monkeypatch, [b, a])  # 枚举顺序交换
    second = {d.serial: d for d in hal.SoapySDRBackend().list_devices()}

    assert set(first) == {"SN-AAAA", "SN-BBBB"}
    assert set(second) == {"SN-AAAA", "SN-BBBB"}
    for sn in first:
        # 同一台设备（同 serial）在两次枚举里必须得到同一个名字。
        # 旧实现给「位置」起名（rtlsdr-0/rtlsdr-1），交换后必然互换 → 红。
        assert first[sn].name == second[sn].name, (
            f"serial={sn} 的名字随枚举位置变了：{first[sn].name!r} → "
            f"{second[sn].name!r}，身份不再跟着设备走")
        # 名字里不得出现下标式伪身份（rtlsdr-0 / rtlsdr-1）
        assert not re.search(r"-\d+$", first[sn].name)
        assert not re.search(r"-\d+$", second[sn].name)
        # 有稳定身份的设备必须保持可用
        assert first[sn].is_available and second[sn].is_available


def test_no_label_name_derives_from_serial_not_position(monkeypatch):
    """无 label 时名字由稳定事实（serial）派生，而不是枚举位置。"""
    _install_fake_soapy(monkeypatch, [_rtl("SN-AAAA"), _rtl("SN-BBBB")])
    devs = hal.SoapySDRBackend().list_devices()
    assert len(devs) == 2
    for d in devs:
        assert d.serial in d.name, (
            f"名字 {d.name!r} 不含序列号 {d.serial!r}："
            "无 label 时名字必须由稳定事实派生")


def test_missing_serial_gets_no_pseudo_identity(monkeypatch):
    """缺 serial（键缺失/空串/纯空白）：不生成 driver-0/1 伪身份。

    条目仍可见（不静默吞掉真设备），但必须标不可用 + 可归因提示。
    """
    _install_fake_soapy(monkeypatch, [
        _rtl(None, label="Generic RTL2832U"),  # 无 serial 键
        _rtl(""),                              # serial 空串
        _rtl("   "),                           # serial 纯空白
    ])
    devs = hal.SoapySDRBackend().list_devices()
    assert len(devs) == 3, "缺身份的设备不得被静默丢掉，仍要在列表里可见"
    for d in devs:
        assert d.serial.strip() == ""
        # 没有稳定身份的条目不得冒充可选设备
        assert d.is_available is False, (
            f"{d.name!r} 读不到序列号却标为可用：按名选择会连错设备")
        # 不得出现 driver-0 / driver-1 式伪身份
        assert not re.search(r"rtlsdr-\d+", d.name), (
            f"名字 {d.name!r} 是枚举下标拼的伪身份")
        # 可归因提示：说清「为什么不可用」
        assert "序列号" in d.description, (
            f"缺身份条目的 description 必须可归因：{d.description!r}")


def test_label_is_display_only_identity_is_serial(monkeypatch):
    """label 仅作显示：同 label 两台靠 serial 区分，交换顺序绑定不变。"""
    a = {"driver": "hackrf", "label": "HackRF One", "serial": "H-1"}
    b = {"driver": "hackrf", "label": "HackRF One", "serial": "H-2"}

    _install_fake_soapy(monkeypatch, [a, b])
    first = {d.serial: d for d in hal.SoapySDRBackend().list_devices()}

    _install_fake_soapy(monkeypatch, [b, a])
    second = {d.serial: d for d in hal.SoapySDRBackend().list_devices()}

    # 两台同型号同 label 的设备必须同时可见，且靠 serial 区分
    assert set(first) == {"H-1", "H-2"}
    assert set(second) == {"H-1", "H-2"}
    # serial → 名字/可用性 的绑定不随枚举顺序变
    assert {sn: d.name for sn, d in first.items()} == \
        {sn: d.name for sn, d in second.items()}
    assert all(d.is_available for d in list(first.values()) + list(second.values()))


def test_no_soapy_fallback_also_has_no_index_identity(monkeypatch):
    """SoapySDR 未安装的降级分支同样不得用下标拼身份。"""
    monkeypatch.setitem(sys.modules, "SoapySDR", None)  # import 必抛 ImportError
    devs = hal.SoapySDRBackend().list_devices()
    assert devs, "降级分支应列出已知设备类型（标不可用），不是空列表"
    for d in devs:
        assert not re.search(r"-\d+$", d.name), (
            f"降级分支名字 {d.name!r} 含下标式身份")
        assert d.is_available is False

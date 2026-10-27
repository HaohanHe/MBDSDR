# SPDX-License-Identifier: MIT
"""RTL-SDR ctypes 回退枚举：接线钉扎 + 诚实空态门禁。

背景（本机真机实测）：pyrtlsdr 的 librtlsdr.py 在 import 期无条件解析
``rtlsdr_set_dithering``，而本机所有可用 librtlsdr.dll 构建均未导出该符号，
``import rtlsdr`` 直接抛 AttributeError，导致整个 pyrtlsdr 不可用，
``list_devices()`` 会把"棒子插着"误报成"无设备"。

本文件钉死三件事：
1. pyrtlsdr 炸时确实走了仓库自有 ctypes 回退（而不是静默返回 []）；
2. 回退拿不到 DLL 时返回空列表——诚实空态，绝不伪造设备条目；
3. 真机在位时枚举结果的字段契约成立（无设备则诚实 skip，不算失败）。
"""
import ctypes

import pytest

from mbdsdr_ai.sdr_backend import RTLSDRBackend


def test_fallback_invoked_when_pyrtlsdr_import_explodes(monkeypatch):
    """pyrtlsdr 在 import 期炸掉时，必须回退到 _enumerate_via_ctypes。

    这是本次修复的核心接线：旧逻辑在这里直接 return []，
    把"库坏了"和"没设备"混为一谈。
    """
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rtlsdr" or name.startswith("rtlsdr."):
            raise AttributeError("function 'rtlsdr_set_dithering' not found")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    sentinel = [{"index": 0, "serial": "SENTINEL", "tuner": "FC0012"}]
    monkeypatch.setattr(
        RTLSDRBackend, "_enumerate_via_ctypes", staticmethod(lambda: sentinel))

    assert RTLSDRBackend.list_devices() == sentinel


def test_fallback_returns_empty_when_no_dll(monkeypatch):
    """拿不到 rtlsdr.dll 时回退枚举必须返回空列表——诚实空态，不伪造。"""
    def boom(*_args, **_kwargs):
        raise OSError("Could not find module 'rtlsdr.dll'")

    monkeypatch.setattr(ctypes, "CDLL", boom)
    assert RTLSDRBackend._enumerate_via_ctypes() == []


def test_real_device_enumeration_shape():
    """真机在位时字段契约成立；无设备是诚实空态，skip 而非失败。"""
    devices = RTLSDRBackend.list_devices()
    if not devices:
        pytest.skip("本机无 RTL-SDR 设备（诚实空态，非失败）")

    for dev in devices:
        assert {"index", "serial", "tuner"} <= set(dev)
        assert isinstance(dev["index"], int)
        assert isinstance(dev["serial"], str)
        assert isinstance(dev["tuner"], str)

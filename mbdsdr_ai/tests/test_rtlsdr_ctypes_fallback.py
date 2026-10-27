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
import logging

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


def test_find_rtl_tcp_exe_never_raises():
    """定位 rtl_tcp 二进制：找得到返回路径，找不到返回 None —— 绝不抛、绝不下载。"""
    exe = RTLSDRBackend._find_rtl_tcp_exe()
    assert exe is None or isinstance(exe, str)


def test_disconnect_recycles_spawned_rtl_tcp():
    """disconnect 必须回收自动拉起的 rtl_tcp 进程。

    rtl_tcp 单客户端且独占 USB：留着会把棒子锁死，导致其它程序
    （含本应用下次枚举）再也看不到设备。这条纪律必须用测试钉死。
    """
    class _FakeProc:
        def __init__(self):
            self.terminated = False
            self.waited = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.waited = True
            return 0

        def kill(self):
            pass

    backend = RTLSDRBackend()
    fake = _FakeProc()
    backend._rtl_tcp_proc = fake
    backend._sdr = None
    backend._host, backend._port = "127.0.0.1", 63362

    backend.disconnect()

    assert fake.terminated, "disconnect 必须 terminate 掉拉起的 rtl_tcp"
    assert backend._rtl_tcp_proc is None, "进程句柄必须清空"
    # 恢复成本地 USB 语义，下次 connect 重新走直连→兜底判定
    assert backend._host is None


def test_warn_once_dedupes_static_environment_warnings(caplog):
    """环境缺失类告警进程内只报一次。

    实测：一次启动里设备枚举被触发多次，SoapySDR/pyrtlsdr 缺失提示被整刷
    4 遍共十余行，把真正的硬件信息淹掉。去重只去重复，不去严重性：
    首次仍完整报出并保留可执行指引。
    """
    from mbdsdr_ai.sdr_backend import _WARNED_ONCE, _warn_once

    key = "test_static_env_warning"
    _WARNED_ONCE.discard(key)          # 保证干净起点
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            _warn_once(key, "重复环境告警 %d", 7)

    hits = [r for r in caplog.records if "重复环境告警" in r.getMessage()]
    assert len(hits) == 1, "静态环境告警应只报一次，不刷屏"

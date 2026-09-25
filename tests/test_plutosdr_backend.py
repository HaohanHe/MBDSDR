"""
PlutoSDRBackend 单元测试
========================
用 mock 验证设备枚举、参数存储、无设备优雅降级逻辑（不依赖真实硬件 / libiio）。

覆盖：
- 模块导入不崩溃；
- 无 libiio/SoapySDR、无设备时 enumerate() 返回 [] 不报错；
- 有伪 iio 模块时 enumerate() 能解析出 PlutoSDR 条目；
- open/connect 无驱动时返回 False，status.error 标注 "[未连接-无libiio/SoapySDR驱动]"；
- set_frequency / set_sample_rate / set_gain 参数存储正确（未连接时也不崩溃）；
- 频率范围覆盖 2.2 GHz (LRO) 与 1.69 GHz (GK-2A)；
- read_samples / recv_samples 未连接返回 None；
- build_backend_for_device 能为 plutosdr driver 构造 PlutoSDRBackend。
"""

import sys
import types
import unittest
from unittest import mock

import numpy as np


# ---------------------------------------------------------------------------
# Fake iio 模块：模拟 pylibiio 绑定
# ---------------------------------------------------------------------------
class _FakeIIOChannel:
    def __init__(self, id_, output=False):
        self.id = id_
        self.output = output
        self.enabled = False
        self.attrs = {}


class _FakeIIODevice:
    def __init__(self, name):
        self.name = name
        self.channels = []
        self.attrs = {}

    def add_channel(self, ch):
        self.channels.append(ch)
        return ch

    def create_buffer(self, samples_count, circular=False):
        return _FakeIIOBuffer(samples_count)


class _FakeIIOBuffer:
    def __init__(self, samples_count):
        self.n = samples_count
        # 造一段 16-bit I/Q 交错的假数据（正弦波）
        t = np.arange(samples_count) / 4.0
        i = (np.cos(t) * 16000).astype(np.int16)
        q = (np.sin(t) * 16000).astype(np.int16)
        interleaved = np.empty(2 * samples_count, dtype=np.int16)
        interleaved[0::2] = i
        interleaved[1::2] = q
        self._bytes = interleaved.tobytes()

    def refill(self):
        return len(self._bytes)

    def read(self):
        return self._bytes


class _FakeIIOContext:
    """模拟 iio.Context：构造 ad9361-phy + cf-ad9361-lpc 设备树。"""
    def __init__(self, uri="ip:192.168.2.1"):
        self.uri = uri
        phy = _FakeIIODevice("ad9361-phy")
        # RX 基带通道 voltage0（输入）
        rx = phy.add_channel(_FakeIIOChannel("voltage0", output=False))
        rx.attrs = {
            "sampling_frequency": mock.Mock(value="2000000"),
            "rf_bandwidth": mock.Mock(value="1500000"),
            "gain_control_mode": mock.Mock(value="manual"),
            "hardwaregain": mock.Mock(value="40.0"),
        }
        # RX LO 输出通道 altvoltage0
        rx_lo = phy.add_channel(_FakeIIOChannel("altvoltage0", output=True))
        rx_lo.attrs = {
            "frequency": mock.Mock(value="1690000000"),
        }
        # 数据流设备
        stream = _FakeIIODevice("cf-ad9361-lpc")
        stream.add_channel(_FakeIIOChannel("voltage0", output=False))
        self.devices = [phy, stream]


class _FakeIIOModule(types.ModuleType):
    """伪造的 pylibiio 模块。"""
    @staticmethod
    def scan_contexts():
        return {"ip:192.168.2.1": "PlutoSDR (network)"}

    @staticmethod
    def Context(uri=""):
        return _FakeIIOContext(uri)


def _install_fake_iio():
    fake = _FakeIIOModule("iio")
    old = sys.modules.get("iio", None)
    sys.modules["iio"] = fake
    return old


class TestPlutoSDRImport(unittest.TestCase):
    def test_plutosdr_import(self):
        """模块导入不崩溃，类可访问。"""
        from mbdsdr_ai.sdr_backend import PlutoSDRBackend
        self.assertIsNotNone(PlutoSDRBackend)
        # 常量存在
        self.assertEqual(PlutoSDRBackend.FREQ_MIN_HZ, 325_000_000.0)
        self.assertEqual(PlutoSDRBackend.FREQ_MAX_HZ, 3_800_000_000.0)


class TestPlutoSDREnumerateNoDevice(unittest.TestCase):
    def setUp(self):
        # 确保无 iio / SoapySDR
        self._saved_iio = sys.modules.pop("iio", None)
        self._saved_soapy = sys.modules.pop("SoapySDR", None)

    def tearDown(self):
        if self._saved_iio is not None:
            sys.modules["iio"] = self._saved_iio
        else:
            sys.modules.pop("iio", None)
        if self._saved_soapy is not None:
            sys.modules["SoapySDR"] = self._saved_soapy
        else:
            sys.modules.pop("SoapySDR", None)

    def test_enumerate_no_device_returns_empty(self):
        """无 iio 模块、无设备时 enumerate() 返回 [] 不崩溃。"""
        # mock socket 探测也失败
        with mock.patch("socket.create_connection", side_effect=OSError):
            from mbdsdr_ai.sdr_backend import PlutoSDRBackend
            devs = PlutoSDRBackend.enumerate()
        self.assertEqual(devs, [])

    def test_driver_status_reports_missing(self):
        """无驱动时 driver_status() 如实报告。"""
        from mbdsdr_ai.sdr_backend import PlutoSDRBackend
        st = PlutoSDRBackend.driver_status()
        self.assertFalse(st["pylibiio"])
        self.assertFalse(st["soapysdr"])
        self.assertIn("pylibiio", st["note"])


class TestPlutoSDREnumerateWithFakeIIO(unittest.TestCase):
    def setUp(self):
        self._saved_iio = sys.modules.pop("iio", None)
        self._saved_soapy = sys.modules.pop("SoapySDR", None)
        _install_fake_iio()

    def tearDown(self):
        sys.modules.pop("iio", None)
        if self._saved_iio is not None:
            sys.modules["iio"] = self._saved_iio
        if self._saved_soapy is not None:
            sys.modules["SoapySDR"] = self._saved_soapy
        else:
            sys.modules.pop("SoapySDR", None)

    def test_enumerate_with_fake_iio(self):
        """有伪 iio.scan_contexts() 时枚举出 PlutoSDR 条目。"""
        from mbdsdr_ai.sdr_backend import PlutoSDRBackend
        devs = PlutoSDRBackend.enumerate()
        self.assertGreaterEqual(len(devs), 1)
        entry = devs[0]
        self.assertEqual(entry["driver"], "plutosdr")
        self.assertIn("192.168.2.1", entry["uri"])
        self.assertEqual(entry["freq_range"],
                         (PlutoSDRBackend.FREQ_MIN_HZ,
                          PlutoSDRBackend.FREQ_MAX_HZ))


class TestPlutoSDRParams(unittest.TestCase):
    """参数设置：未连接时也应安全存储，不崩溃。"""

    def setUp(self):
        from mbdsdr_ai.sdr_backend import PlutoSDRBackend
        self._saved_iio = sys.modules.pop("iio", None)
        self._saved_soapy = sys.modules.pop("SoapySDR", None)
        self.be = PlutoSDRBackend(uri="ip:192.168.2.1")

    def tearDown(self):
        if self._saved_iio is not None:
            sys.modules["iio"] = self._saved_iio
        if self._saved_soapy is not None:
            sys.modules["SoapySDR"] = self._saved_soapy

    def test_set_frequency(self):
        """set_frequency 未连接时返回 False（基类保护），但频率范围合法。"""
        # 未连接时基类返回 False
        self.assertFalse(self.be.set_frequency(1_690_000_000))
        # 强制模拟已连接：验证参数写入
        self.be.status.connected = True
        self.assertTrue(self.be.set_frequency(1_690_000_000))
        self.assertEqual(self.be.get_frequency(), 1_690_000_000)

    def test_set_sample_rate(self):
        self.be.status.connected = True
        self.assertTrue(self.be.set_sample_rate(2_000_000))
        self.assertEqual(self.be.get_sample_rate(), 2_000_000)

    def test_set_gain(self):
        self.be.status.connected = True
        self.assertTrue(self.be.set_gain(40.0))
        self.assertEqual(self.be.get_gain(), 40.0)
        # 增益被钳位到 max_gain=73
        self.assertTrue(self.be.set_gain(100.0))
        self.assertEqual(self.be.get_gain(), 73.0)

    def test_recv_samples_not_connected(self):
        """未连接时 recv_samples / read_samples 返回 None 不崩溃。"""
        self.assertIsNone(self.be.read_samples(1024))
        self.assertIsNone(self.be.recv_samples(1024))


class TestPlutoSDROpenFailsGracefully(unittest.TestCase):
    def setUp(self):
        self._saved_iio = sys.modules.pop("iio", None)
        self._saved_soapy = sys.modules.pop("SoapySDR", None)

    def tearDown(self):
        if self._saved_iio is not None:
            sys.modules["iio"] = self._saved_iio
        if self._saved_soapy is not None:
            sys.modules["SoapySDR"] = self._saved_soapy

    def test_open_without_driver_fails(self):
        """无 libiio / SoapySDR 时 connect()/open() 返回 False，不崩溃。"""
        from mbdsdr_ai.sdr_backend import PlutoSDRBackend
        be = PlutoSDRBackend()
        self.assertFalse(be.connect())
        self.assertFalse(be.status.connected)
        # error 字段必须标注"未连接-无驱动"
        self.assertIn("未连接-无libiio/SoapySDR驱动", be.status.error)
        # open() 别名同样失败
        self.assertFalse(be.open())
        # close() 不崩溃
        be.close()

    def test_setters_when_not_connected_no_crash(self):
        """未 open 状态下调所有 setter / read_samples 不崩溃。"""
        from mbdsdr_ai.sdr_backend import PlutoSDRBackend
        be = PlutoSDRBackend()
        be.set_frequency(100e6)
        be.set_sample_rate(2e6)
        be.set_gain(10.0)
        be.set_agc(True)
        be.set_bandwidth(1.5e6)
        self.assertIsNone(be.read_samples(1024))
        # 不抛异常即通过


class TestPlutoSDRFreqRange(unittest.TestCase):
    def test_freq_range_covers_lro_and_gk2a(self):
        """频率范围必须覆盖 2.2 GHz (LRO) 和 1.69 GHz (GK-2A)。"""
        from mbdsdr_ai.sdr_backend import PlutoSDRBackend
        be = PlutoSDRBackend()
        lo, hi = be.device.frequency_range
        # 2.2 GHz LRO
        self.assertGreaterEqual(2.2e9, lo, f"范围下限 {lo} 未覆盖 2.2GHz")
        self.assertLessEqual(2.2e9, hi, f"范围上限 {hi} 未覆盖 2.2GHz")
        # 1.69 GHz GK-2A
        self.assertGreaterEqual(1.69e9, lo)
        self.assertLessEqual(1.69e9, hi)
        # 3.5 GHz 验收硬指标
        self.assertGreaterEqual(hi, 3.5e9,
                                f"频率上限 {hi} 未达到 3.5GHz 验收线")

    def test_unlocked_range(self):
        """unlocked=True 时声明 70 MHz – 6 GHz。"""
        from mbdsdr_ai.sdr_backend import PlutoSDRBackend
        be = PlutoSDRBackend(unlocked=True)
        lo, hi = be.device.frequency_range
        self.assertAlmostEqual(lo, 70e6, places=0)
        self.assertAlmostEqual(hi, 6e9, places=0)


class TestPlutoSDRBuildBackend(unittest.TestCase):
    def test_build_backend_for_plutosdr(self):
        """build_backend_for_device 能为 plutosdr driver 构造 PlutoSDRBackend。"""
        from mbdsdr_ai.sdr_backend import (
            PlutoSDRBackend, build_backend_for_device)
        dev = {
            "driver": "plutosdr",
            "label": "PlutoSDR (network 192.168.2.1)",
            "device_args": {"uri": "ip:192.168.2.1"},
        }
        be = build_backend_for_device(dev)
        self.assertIsInstance(be, PlutoSDRBackend)
        self.assertEqual(be._uri, "ip:192.168.2.1")

    def test_enumerate_all_includes_plutosdr_key(self):
        """enumerate_all_sdr_devices() 在无设备时不崩溃（PlutoSDR 分支不抛异常）。"""
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        # 无 iio/SoapySDR 时 PlutoSDR 分支返回 []，不应影响整体枚举
        devs = enumerate_all_sdr_devices()
        self.assertIsInstance(devs, list)


class TestPlutoSDRReadSamplesFakeIIO(unittest.TestCase):
    """用伪 iio.Context 验证 read_samples 能把 16-bit I/Q 转 complex64。"""

    def setUp(self):
        self._saved_iio = sys.modules.pop("iio", None)
        self._saved_soapy = sys.modules.pop("SoapySDR", None)
        _install_fake_iio()

    def tearDown(self):
        sys.modules.pop("iio", None)
        if self._saved_iio is not None:
            sys.modules["iio"] = self._saved_iio
        if self._saved_soapy is not None:
            sys.modules["SoapySDR"] = self._saved_soapy

    def test_read_samples_returns_complex64(self):
        from mbdsdr_ai.sdr_backend import PlutoSDRBackend
        be = PlutoSDRBackend(uri="ip:192.168.2.1")
        self.assertTrue(be.connect())
        self.assertTrue(be.status.connected)
        # 读 1024 个样本
        out = be.read_samples(1024)
        self.assertIsNotNone(out)
        self.assertEqual(out.dtype, np.complex64)
        self.assertEqual(len(out), 1024)
        # 幅度应在 [-1, 1] 附近
        self.assertLessEqual(np.abs(out).max(), 1.0)
        be.disconnect()


if __name__ == "__main__":
    unittest.main()

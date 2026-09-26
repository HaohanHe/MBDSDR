"""
SoapySDRBackend 单元测试
========================
用 mock 验证设备枚举逻辑（不依赖真实硬件 / SoapySDR 库）。

覆盖：
- Python 绑定存在时：enumerate 结果被正确解析为统一设备 dict（含真实范围）；
- Python 绑定缺失但 SoapySDRUtil CLI 存在时：解析 CLI 文本输出；
- 两者都没有：返回 [] 且不崩溃（优雅报设备未找到）；
- connect() 在无库时必须返回 False（绝不静默切 mock 报 success）；
- enumerate_all_sdr_devices() 跨后端去重。
"""

import sys
import types
import unittest
from unittest import mock

import numpy as np


# ---------------------------------------------------------------------------
# Fake SoapySDR 模块：模拟 SWIG 绑定
# ---------------------------------------------------------------------------
class _FakeRange:
    """对应 SoapySDR::Range，来源: include/SoapySDR/Types.hpp:64。"""
    def __init__(self, lo, hi):
        self._lo, self._hi = float(lo), float(hi)

    def minimum(self):
        return self._lo

    def maximum(self):
        return self._hi


class _FakeProbeDevice:
    """枚举后 open 的临时 probe 设备，返回真实范围。"""
    def getFrequencyRange(self, direction, chan):
        return [_FakeRange(24e6, 1.764e9)]

    def getSampleRateRange(self, direction, chan):
        # 来源: SoapyRTLSDR/Settings.cpp:489-497
        return [_FakeRange(225001, 300000), _FakeRange(900001, 3200000)]

    def getGainRange(self, direction, chan, name=None):
        return _FakeRange(0.0, 49.6)


class _FakeSoapySDRModule(types.ModuleType):
    """伪造的 SoapySDR Python 模块。"""
    SOAPY_SDR_RX = 1
    SOAPY_SDR_CF32 = "CF32"

    class Device:
        # 类级：枚举返回的身份列表
        _ENUM_RESULTS = [
            {"driver": "rtlsdr", "label": "Generic RTL2832U :: 00000001",
             "serial": "00000001", "manufacturer": "Realtek",
             "product": "RTL2838UHIDIR"},
            {"driver": "hackrf", "label": "HackRF One :: SN0001",
             "serial": "SN0001", "manufacturer": "Great Scott Gadgets",
             "product": "HackRF One"},
        ]

        @classmethod
        def enumerate(cls, args=None):
            # 返回 list[dict]（与 SWIG KwargsList 兼容）
            return [dict(x) for x in cls._ENUM_RESULTS]

        def __init__(self, args):
            # probe 打开
            self._probe = _FakeProbeDevice()

        def __getattr__(self, name):
            return getattr(self._probe, name)


def _install_fake_soapysdr():
    """把假 SoapySDR 模块注入 sys.modules，返回之前的原值。"""
    fake = _FakeSoapySDRModule("SoapySDR")
    old = sys.modules.get("SoapySDR", None)
    sys.modules["SoapySDR"] = fake
    return old


class TestSoapySDREnumerate(unittest.TestCase):

    def setUp(self):
        # 确保每次测试从"无 SoapySDR 模块"状态开始
        self._saved = sys.modules.pop("SoapySDR", None)

    def tearDown(self):
        if self._saved is not None:
            sys.modules["SoapySDR"] = self._saved
        else:
            sys.modules.pop("SoapySDR", None)

    def test_python_binding_enumerate(self):
        """路径1：有 Python 绑定时枚举到 2 台设备并解析出真实范围。"""
        _install_fake_soapysdr()
        from mbdsdr_ai.sdr_backend import SoapySDRBackend
        devs = SoapySDRBackend.list_devices()
        self.assertEqual(len(devs), 2)
        by_serial = {d["serial"]: d for d in devs}
        self.assertIn("00000001", by_serial)
        rtl = by_serial["00000001"]
        self.assertEqual(rtl["driver"], "rtlsdr")
        self.assertIn("RTL2832U", rtl["label"])
        # 范围被 probe 读出：频率 24MHz~1.764GHz
        self.assertAlmostEqual(rtl["freq_range"][0], 24e6, places=0)
        self.assertAlmostEqual(rtl["freq_range"][1], 1.764e9, places=0)
        # 采样率范围合并两段：min=225001, max=3200000
        self.assertEqual(rtl["sample_rate_range"][0], 225001)
        self.assertEqual(rtl["sample_rate_range"][1], 3200000)
        self.assertEqual(rtl["gain_range"], (0.0, 49.6))

    def test_no_library_returns_empty(self):
        """路径3：无 Python 绑定、无 CLI → 返回 [] 不崩溃。"""
        # sys.modules 里没有 SoapySDR；mock subprocess.run 抛 FileNotFoundError
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            from mbdsdr_ai.sdr_backend import SoapySDRBackend
            devs = SoapySDRBackend.list_devices()
        self.assertEqual(devs, [])

    def test_import_error_returns_empty_and_logs_pip_hint(self):
        """红线：import SoapySDR 抛 ImportError 时，enumerate 返回 []，绝不抛异常到上层，
        且日志明确提示 pip install SoapySDR（而不是静默把"没装库"当成"没插设备"）。"""
        # setUp 已把 SoapySDR 从 sys.modules 摘掉 → import 必抛 ImportError；
        # 再让 CLI 降级也不可用，强制走到"都没有"分支。
        self.assertNotIn("SoapySDR", sys.modules)
        with mock.patch("subprocess.run", side_effect=FileNotFoundError), \
                self.assertLogs("mbdsdr_ai.sdr_backend", level="WARNING") as cm:
            from mbdsdr_ai.sdr_backend import SoapySDRBackend
            devs = SoapySDRBackend.list_devices()
        self.assertEqual(devs, [])
        joined = "\n".join(cm.output)
        self.assertIn("pip install SoapySDR", joined)

    def test_cli_fallback_parsing(self):
        """路径2：无 Python 绑定，解析 SoapySDRUtil --find 文本输出。"""
        fake_stdout = (
            "Found device 0:\n"
            "  :driver=rtlsdr\n"
            "  :label=Generic RTL2832U :: 00000001\n"
            "  :serial=00000001\n"
            "  :manufacturer=Realtek\n"
            "  :product=RTL2838UHIDIR\n"
        )
        fake_proc = mock.Mock(returncode=0, stdout=fake_stdout, stderr="")
        with mock.patch("subprocess.run", return_value=fake_proc):
            from mbdsdr_ai.sdr_backend import SoapySDRBackend
            devs = SoapySDRBackend._enumerate_via_cli()
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["driver"], "rtlsdr")
        self.assertEqual(devs[0]["serial"], "00000001")
        self.assertIn("RTL2832U", devs[0]["label"])

    def test_connect_without_library_fails_honestly(self):
        """红线：无库时 connect() 返回 False，绝不假装成功。"""
        from mbdsdr_ai.sdr_backend import SoapySDRBackend
        be = SoapySDRBackend(device_args="driver=rtlsdr",
                             device_info={"label": "t", "serial": "s"})
        self.assertFalse(be.connect())
        self.assertFalse(be.status.connected)
        self.assertTrue(be.status.error)  # error 字段记录真实错误
        # read_samples 未连接应返回 None
        self.assertIsNone(be.read_samples(1024))


class TestEnumerateAllDedup(unittest.TestCase):
    def test_dedup_across_backends(self):
        """enumerate_all_sdr_devices()：SoapySDR 与原生 RTL-SDR 同 serial 去重。"""
        _install_fake_soapysdr()
        # 伪造原生 pyrtlsdr 也枚举出同 serial 设备
        with mock.patch(
            "mbdsdr_ai.sdr_backend.RTLSDRBackend.list_devices",
            return_value=[{"index": 0, "serial": "00000001", "tuner": "R820T"}],
        ):
            from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
            devs = enumerate_all_sdr_devices()
        serials = [d["serial"] for d in devs]
        # 同 serial 只出现一次（SoapySDR 条目信息更全，优先保留）
        self.assertEqual(serials.count("00000001"), 1)
        # hackrf 仍在
        self.assertIn("SN0001", serials)


class TestReadSamplesTimeout(unittest.TestCase):
    """红线：readStream 超时/无可读样本时返回空 complex64 数组，绝不造假 IQ。"""

    def _make_connected_backend(self):
        from mbdsdr_ai.sdr_backend import SoapySDRBackend
        be = SoapySDRBackend(
            device_args={"driver": "hackrf"},
            device_info={"label": "HackRF One", "serial": "SN1"},
        )
        # 不真连硬件，直接伪造已连接状态 + mock 设备/流句柄
        be.status.connected = True
        be._sdr = mock.Mock()
        be._stream = object()
        return be

    def test_not_connected_returns_none(self):
        """未连接 → 仍返回 None（错误态），与既有契约一致。"""
        from mbdsdr_ai.sdr_backend import SoapySDRBackend
        be = SoapySDRBackend(device_args={}, device_info={})
        self.assertIsNone(be.read_samples(1024))

    def test_timeout_returns_empty_complex64_not_fake(self):
        """readStream 返回超时码 ret=-1 → 空 complex64 数组，且不推进样本计数。"""
        be = self._make_connected_backend()
        fake_status = mock.Mock()
        fake_status.ret = -1  # SOAPY_SDR_TIMEOUT
        be._sdr.readStream.return_value = fake_status

        out = be.read_samples(8192)

        self.assertIsNotNone(out)
        self.assertIsInstance(out, np.ndarray)
        self.assertEqual(out.dtype, np.complex64)
        self.assertEqual(out.size, 0)          # 空数组，不是 8192 个零样点假数据
        self.assertEqual(be._samples_read, 0)  # 没读到真实样本，计数不推进

    def test_zero_return_returns_empty(self):
        """ret=0（本帧无可读样本）同样返回空数组。"""
        be = self._make_connected_backend()
        fake_status = mock.Mock()
        fake_status.ret = 0
        be._sdr.readStream.return_value = fake_status
        out = be.read_samples(4096)
        self.assertEqual(out.size, 0)
        self.assertEqual(out.dtype, np.complex64)

    def test_success_returns_real_samples(self):
        """正常读到 n_read 个样本 → 返回前 n_read 个 complex64，并推进计数。"""
        be = self._make_connected_backend()
        n = 1000
        fake_status = mock.Mock()
        fake_status.ret = n
        be._sdr.readStream.return_value = fake_status
        out = be.read_samples(2048)
        self.assertEqual(out.size, n)
        self.assertEqual(out.dtype, np.complex64)
        self.assertEqual(be._samples_read, n)


if __name__ == "__main__":
    unittest.main()

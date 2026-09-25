"""
4 个 SDR UI/接收机架构适配器 - 往返验证
==========================================
对照各自开源仓库真实源码逐项验证：
  1. SDR#     : 设备枚举表非空；FFT 窗系数和校验；register 可调用
  2. OpenWebRX: FFT 平均/块尺寸公式；waterfall 调色板形状；设备带宽钳位
  3. CubicSDR : 多VFO 切换状态一致；drag-tune 步进；带宽/PPM 钳位
  4. HDSDR    : 增益档位钳位；总增益等于三级求和；带宽预设正确

所有常量来源见各 adapter 内联注释（项目 源文件:行号）。
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402
from mbdsdr_ai.sdrsharp_adapter import (  # noqa: E402
    SDRSHARP_DEVICE_TABLE, make_sdrsharp_window, sdrsharp_spectrum_power,
    register_sdrsharp_tools, SDRSHARP_WINDOW_TYPES,
)
from mbdsdr_ai.openwebrx_adapter import (  # noqa: E402
    owx_plan_fft, owx_waterfall_colormap, owx_client_bandwidth_adapt,
    register_openwebrx_tools, OWRX_TEEJEEZ_STOPS,
)
from mbdsdr_ai.cubicsdr_adapter import (  # noqa: E402
    CubicVFOManager, cubic_step_tuner, cubic_drag_to_steps,
    cubic_clamp_bw, cubic_clamp_ppm, register_cubicsdr_tools,
    CUBIC_CHANNELIZER_RATE_MAX, CUBIC_PPM_LIMIT,
)
from mbdsdr_ai.hdsdr_adapter import (  # noqa: E402
    hdsdr_total_gain, hdsdr_gain_clamp_stage, hdsdr_bandwidth_for_mode,
    register_hdsdr_tools, HDSDR_LNA_GAIN_DB, HDSDR_MIXER_GAIN_DB, HDSDR_VGA_GAIN_DB,
    HDSDR_BANDWIDTH_PRESETS,
)


# ═══════════════════════════════════════════════════════════════════════
#  SDR#
# ═══════════════════════════════════════════════════════════════════════
class TestSdrSharp(unittest.TestCase):
    def test_device_table_nonempty(self):
        """设备枚举表应非空且含 AIRSPY / RTL-SDR。"""
        self.assertGreater(len(SDRSHARP_DEVICE_TABLE), 10)
        names = [d["name"] for d in SDRSHARP_DEVICE_TABLE]
        self.assertIn("AIRSPY", names)
        self.assertTrue(any("RTL-SDR" in n for n in names))

    def test_window_coefficient_sum(self):
        """Hamming 窗 N=1024 时系数和 ≈ 0.54*N=553（FilterBuilder.cs:20-24）。"""
        win = make_sdrsharp_window("hamming", 1024)
        self.assertAlmostEqual(float(np.sum(win)), 0.54 * 1024, delta=5.0)
        # 边缘应≈0.08（Hamming 两端 0.54-0.46=0.08）
        self.assertAlmostEqual(float(win[0]), 0.08, delta=0.01)

    def test_blackmanharris4_peak_near_center(self):
        """BH4 窗中心应≈1.0（a0+a1+a2+a3=1），两端≈0（FilterBuilder.cs:38-42）。"""
        win = make_sdrsharp_window("blackmanharris4", 1024)
        self.assertAlmostEqual(float(win[512]), 1.0, delta=0.01)
        self.assertAlmostEqual(float(win[0]), 0.0, delta=0.01)
        self.assertAlmostEqual(float(win[0]), 0.0, delta=0.01)

    def test_spectrum_fft_shape(self):
        """合成单音 → 功率谱 bins 数=输入长度，0频居中。"""
        n = 1024
        t = np.arange(n) / 1_000_000.0
        iq = np.exp(1j * 2 * np.pi * 100_000 * t)
        power = sdrsharp_spectrum_power(iq, window="hamming")
        self.assertEqual(len(power), n)
        self.assertTrue(np.isfinite(power).all())

    def test_register_tools(self):
        reg = ToolRegistry()
        register_sdrsharp_tools(reg)
        self.assertGreaterEqual(len(reg.tools), 4)


# ═══════════════════════════════════════════════════════════════════════
#  OpenWebRX
# ═══════════════════════════════════════════════════════════════════════
class TestOpenWebRX(unittest.TestCase):
    def test_fft_plan_formula(self):
        """fft_averages = round(sr/size/fps/(1-voverlap))（fft.py:79）。"""
        plan = owx_plan_fft(1_920_000, 8192, 10.0, 0.5)
        expect = int(round(1_920_000 / 8192 / 10.0 / (1 - 0.5)))
        self.assertEqual(plan["fft_averages"], expect)
        self.assertEqual(plan["averager"], "LogAveragePower")

    def test_fft_plan_no_overlap_uses_logpower(self):
        plan = owx_plan_fft(1_920_000, 8192, 10.0, 0.0)
        self.assertEqual(plan["fft_averages"], 0)
        self.assertEqual(plan["averager"], "LogPower")

    def test_waterfall_colormap_shape(self):
        """teejeez 调色板应 n×3，首=黑，尾=白。"""
        cmap = owx_waterfall_colormap("teejeez", 256)
        self.assertEqual(cmap.shape, (256, 3))
        self.assertEqual(cmap[0].tolist(), [0, 0, 0])
        self.assertEqual(cmap[-1].tolist(), [255, 255, 255])

    def test_device_bandwidth_clamp(self):
        """rtl_sdr 5M 请求应钳到 3.2M，半带宽=1.6M。"""
        r = owx_client_bandwidth_adapt(5_000_000, "rtl_sdr")
        self.assertEqual(r["clamped_samp_rate"], 3_200_000.0)
        self.assertEqual(r["usable_half_bandwidth"], 1_600_000.0)
        self.assertFalse(r["connected"])

    def test_register_tools(self):
        reg = ToolRegistry()
        register_openwebrx_tools(reg)
        self.assertGreaterEqual(len(reg.tools), 4)


# ═══════════════════════════════════════════════════════════════════════
#  CubicSDR
# ═══════════════════════════════════════════════════════════════════════
class TestCubicSDR(unittest.TestCase):
    def test_vfo_switch_state_consistent(self):
        """添加两个 VFO 后按频率选中，活动指针应一致切换。"""
        mgr = CubicVFOManager(100_000_000, 2_000_000)
        v0 = mgr.add_vfo(100_100_000, 3000, "am")
        v1 = mgr.add_vfo(100_500_000, 12500, "nfm")
        # 最新 add 的是 current
        self.assertEqual(mgr.current_modem.id, v1.id)
        picked = mgr.select_by_frequency(100_100_000)
        self.assertEqual(picked.id, v0.id)
        self.assertEqual(mgr.current_modem.id, v0.id)
        self.assertEqual(mgr.active_visual.id, v0.id)

    def test_step_tuner_amount(self):
        """StepTuner digit=4 步进 ±10000（TuningCanvas.cpp:174）。"""
        self.assertEqual(cubic_step_tuner(100_000_000, 4, 1), 100_010_000)
        self.assertEqual(cubic_step_tuner(100_000_000, 4, -1), 99_990_000)

    def test_drag_to_steps(self):
        """5.0*normalized_delta=步进数：归一化拖拽 2.0 窗口宽 → 10 步（TuningCanvas.cpp:275）。"""
        self.assertEqual(cubic_drag_to_steps(2.0), 10)
        self.assertEqual(cubic_drag_to_steps(-2.0), -10)
        self.assertEqual(cubic_drag_to_steps(0.1), 0)

    def test_clamps(self):
        self.assertEqual(cubic_clamp_bw(999_999), CUBIC_CHANNELIZER_RATE_MAX)
        self.assertEqual(cubic_clamp_bw(-5), 0)
        self.assertEqual(cubic_clamp_ppm(99_999), CUBIC_PPM_LIMIT)
        self.assertEqual(cubic_clamp_ppm(-99_999), -CUBIC_PPM_LIMIT)

    def test_register_tools(self):
        reg = ToolRegistry()
        register_cubicsdr_tools(reg)
        self.assertGreaterEqual(len(reg.tools), 3)


# ═══════════════════════════════════════════════════════════════════════
#  HDSDR
# ═══════════════════════════════════════════════════════════════════════
class TestHDSDR(unittest.TestCase):
    def test_gain_stage_clamp(self):
        """越界档位应钳到 0..15。"""
        self.assertEqual(hdsdr_gain_clamp_stage("lna", 99), 15)
        self.assertEqual(hdsdr_gain_clamp_stage("vga", -5), 0)

    def test_total_gain_equals_sum(self):
        """总增益应等于三级 dB 之和（钳位后）。"""
        r = hdsdr_total_gain(7, 8, 8)
        expect = HDSDR_LNA_GAIN_DB[7] + HDSDR_MIXER_GAIN_DB[8] + HDSDR_VGA_GAIN_DB[8]
        self.assertAlmostEqual(r["total_gain_db"], round(expect, 2), places=1)

    def test_bandwidth_presets(self):
        self.assertEqual(hdsdr_bandwidth_for_mode("cw")["bandwidth_hz"], 500)
        self.assertEqual(hdsdr_bandwidth_for_mode("wfm")["bandwidth_hz"], 120000)
        # fm 是 nfm 别名
        self.assertEqual(hdsdr_bandwidth_for_mode("fm")["bandwidth_hz"],
                         HDSDR_BANDWIDTH_PRESETS["nfm"])

    def test_register_tools(self):
        reg = ToolRegistry()
        register_hdsdr_tools(reg)
        self.assertGreaterEqual(len(reg.tools), 3)


# ═══════════════════════════════════════════════════════════════════════
#  端到端：每个 register 注册的工具都能被 handler 真实调用
# ═══════════════════════════════════════════════════════════════════════
class TestEndToEndHandlers(unittest.TestCase):
    def _build_registry(self):
        reg = ToolRegistry()
        register_sdrsharp_tools(reg)
        register_openwebrx_tools(reg)
        register_cubicsdr_tools(reg)
        register_hdsdr_tools(reg)
        return reg

    def test_all_tools_callable(self):
        reg = self._build_registry()
        self.assertGreaterEqual(len(reg.tools), 14)
        # 挑每个 adapter 的一个无参/轻参工具跑通
        iq = (np.exp(1j * 2 * np.pi * np.arange(512) / 8)).tolist()
        calls = {
            "sdrsharp_list_devices": {},
            "sdrsharp_make_window": {"window": "hamming", "length": 512},
            "sdrsharp_spectrum_fft": {"iq": iq},
            "sdrsharp_plugin_contract": {"device": "AIRSPY"},
            "owrx_plan_fft": {},
            "owrx_waterfall_colormap": {},
            "owrx_fft_average": {"iq": iq},
            "owrx_device_bandwidth_adapt": {"device": "rtl_sdr"},
            "cubicsdr_waterfall_colormap": {},
            "cubicsdr_vfo_state_machine": {"freqs": [100_000_000]},
            "cubicsdr_drag_tune": {"delta_drag": 0.4, "digit": 4},
            "hdsdr_rf_gain": {},
            "hdsdr_bandwidth_preset": {"mode": "am"},
            "hdsdr_gain_table": {},
        }
        for name, args in calls.items():
            with self.subTest(tool=name):
                self.assertIn(name, reg.tools, f"{name} 未注册")
                res = reg.tools[name]["handler"](args)
                self.assertIsInstance(res, ToolResult)
                self.assertTrue(res.success, f"{name} 失败: {res.error or res.content}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

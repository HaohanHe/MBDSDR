# SPDX-License-Identifier: MIT
"""第4件：两个真缺口的行为钉。

1. ``SDRBackend.set_ppm`` 的**物理可信域**钳位：后端是唯一直接写晶振校正
   寄存器的入口，librtlsdr 只收 int、不做量级判断，所以越界请求必须在
   这里挡住。注意它与 UI 范围（±1000 的交互舒适区）、校准置信域
   （``ppm_calibration.clamp_ppm`` ±200）都不是一回事——本用例同时钉住
   三者「各管一段」的事实。
2. ``decoders.list_visible_satellites`` 的仰角门：历史上 ``min_elevation=0.0``
   是第三道没人认领的默认门，现在改为必传关键字，调用方必须自己声明。
"""
import inspect
import types
import unittest
from unittest import mock

from mbdsdr_ai import decoders
from mbdsdr_ai.ppm_calibration import clamp_ppm
from mbdsdr_ai.sdr_backend import PPM_PHYSICAL_LIMIT, RTLSDRBackend


class PpmPhysicalLimitTest(unittest.TestCase):
    def _backend(self) -> RTLSDRBackend:
        # 不 connect：_sdr=None，set_ppm 只保存生效值，不碰硬件。
        return RTLSDRBackend(device_index=0)

    def test_high_out_of_range_is_clamped(self):
        b = self._backend()
        with self.assertLogs("mbdsdr_ai.sdr_backend", level="WARNING") as cm:
            self.assertTrue(b.set_ppm(5000))
        self.assertEqual(b._ppm, PPM_PHYSICAL_LIMIT)
        self.assertIn("物理可信域", "\n".join(cm.output))

    def test_low_out_of_range_is_clamped(self):
        b = self._backend()
        with self.assertLogs("mbdsdr_ai.sdr_backend", level="WARNING"):
            self.assertTrue(b.set_ppm(-999999))
        self.assertEqual(b._ppm, -PPM_PHYSICAL_LIMIT)

    def test_in_range_value_is_untouched(self):
        b = self._backend()
        for ppm in (0, 47, -150, PPM_PHYSICAL_LIMIT, -PPM_PHYSICAL_LIMIT):
            with self.assertNoLogs("mbdsdr_ai.sdr_backend", level="WARNING"):
                self.assertTrue(b.set_ppm(ppm))
            self.assertEqual(b._ppm, ppm)

    def test_limit_is_not_the_ui_or_calibration_range(self):
        """物理域 1000 / 校准置信域 200：数值不同才说明是两把尺子。"""
        self.assertEqual(PPM_PHYSICAL_LIMIT, 1000)
        applied, clamped = clamp_ppm(1000)
        self.assertTrue(clamped)
        self.assertEqual(applied, 200)
        # 物理域更宽：校准建议值永远落在后端物理域内，不会触发后端钳位。
        self.assertLess(200, PPM_PHYSICAL_LIMIT)


class ElevationGateTest(unittest.TestCase):
    def test_min_elevation_is_required_keyword(self):
        sig = inspect.signature(decoders.list_visible_satellites)
        p = sig.parameters["min_elevation"]
        self.assertIs(inspect.Parameter.empty, p.default,
                      "min_elevation 不允许再有隐式默认（第三道门）")
        self.assertIs(inspect.Parameter.KEYWORD_ONLY, p.kind)
        with self.assertRaises(TypeError):
            decoders.list_visible_satellites(39.9, 116.4)  # type: ignore[call-arg]

    def test_gate_actually_filters(self):
        """门是调用方的：10° 只留高仰角，0° 全留（不访问网络，桩替身）。"""
        rows = {"LOW": 5.0, "HIGH": 80.0}

        def fake_position(name, *_args, **_kwargs):
            return types.SimpleNamespace(name=name, elevation=rows[name])

        with mock.patch.object(decoders, "BUILTIN_TLE", {k: None for k in rows}), \
                mock.patch.object(decoders, "compute_satellite_position",
                                  side_effect=fake_position):
            high = decoders.list_visible_satellites(
                39.9, 116.4, min_elevation=10.0)
            all_sats = decoders.list_visible_satellites(
                39.9, 116.4, min_elevation=0.0)
        self.assertEqual([p.name for p in high], ["HIGH"])
        self.assertEqual([p.name for p in all_sats], ["HIGH", "LOW"])


if __name__ == "__main__":
    unittest.main()

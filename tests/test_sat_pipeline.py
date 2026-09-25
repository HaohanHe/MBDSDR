"""sat_pipeline_params / sat_pipeline_runner 单元测试。

纯 Python，无硬件依赖，不产生任何运行时模拟信号。
只验证：参数表完整性、ETA 估算、参数合并、调度器的层级跳过与"未实现 stage"容错。

运行: python3 -m pytest tests/test_sat_pipeline.py -v
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sat_pipeline_params import (  # noqa: E402
    STAGE_LEVELS,
    get_pipeline,
    list_pipelines,
)
from mbdsdr_ai.sat_pipeline_runner import (  # noqa: E402
    DECODE_STATS_KEYS,
    EtaEstimator,
    prepare_params,
    run_pipeline,
)


class TestSatelliteParamTable(unittest.TestCase):
    """统一卫星参数表完整性校验。"""

    def test_list_non_empty(self):
        pipes = list_pipelines()
        self.assertGreaterEqual(len(pipes), 6)

    def test_all_pipelines_fields_complete(self):
        for p in list_pipelines():
            with self.subTest(pipeline=p.pipeline_id):
                self.assertTrue(p.pipeline_id)
                self.assertTrue(p.name)
                # 频率列表非空，每个变体频率 > 0
                self.assertGreater(len(p.frequencies), 0)
                for _name, freq in p.frequencies:
                    self.assertGreater(freq, 0.0)
                # 推荐采样率 > 0
                self.assertGreater(p.recommended_samplerate, 0.0)
                # stages 非空，且每个 stage 的 level 合法
                self.assertGreater(len(p.stages), 0)
                for s in p.stages:
                    self.assertIn(s.level, STAGE_LEVELS)

    def test_expected_pipelines_present(self):
        for pid in ["noaa_apt", "meteor_m2_lrpt", "meteor_m2x_lrpt_80k",
                    "gk2a_lrit", "goes_hrit", "fy3_ahrpt"]:
            self.assertIn(pid, {p.pipeline_id for p in list_pipelines()})

    def test_get_pipeline_roundtrip(self):
        p = get_pipeline("noaa_apt")
        self.assertEqual(p.pipeline_id, "noaa_apt")
        self.assertEqual(p.recommended_samplerate, 1e6)

    def test_get_pipeline_unknown_raises(self):
        with self.assertRaises(KeyError):
            get_pipeline("no_such_satellite")

    def test_known_spot_values_match_satdump(self):
        # 抽几个关键参数照抄 SatDump JSON，防止回归成拍脑袋值。
        mete = get_pipeline("meteor_m2_lrpt")
        soft = mete.stages[1]
        self.assertEqual(soft.params["constellation"], "qpsk")
        self.assertEqual(soft.params["symbolrate"], 72e3)

        gk = get_pipeline("gk2a_lrit")
        cadu = [s for s in gk.stages if s.level == "cadu"][0]
        self.assertEqual(cadu.params["cadu_size"], 8192)

        goes = get_pipeline("goes_hrit")
        self.assertAlmostEqual(goes.frequencies[0][1], 1694.1e6, places=3)

        fy3 = get_pipeline("fy3_ahrpt")
        fy3_soft = fy3.stages[1]
        self.assertEqual(fy3_soft.params["symbolrate"], 2.8e6)


class TestEtaEstimator(unittest.TestCase):
    def test_start_then_update_returns_nonempty_strings(self):
        eta = EtaEstimator()
        eta.start()
        elapsed, remaining = eta.update(0.5)
        self.assertIsInstance(elapsed, str)
        self.assertIsInstance(remaining, str)
        self.assertGreater(len(elapsed), 0)
        self.assertGreater(len(remaining), 0)
        # 格式应为 MM:SS 或 HH:MM:SS
        self.assertIn(":", elapsed)

    def test_full_progress_remaining_near_zero(self):
        eta = EtaEstimator()
        eta.start()
        # 先喂一点进度，让 EMA 有值
        eta.update(0.1)
        eta.update(0.5)
        _, remaining = eta.update(1.0)
        self.assertEqual(remaining, "00:00")

    def test_never_started_is_safe(self):
        eta = EtaEstimator()
        elapsed, remaining = eta.update(0.5)
        self.assertIsInstance(elapsed, str)
        self.assertIsInstance(remaining, str)


class TestPrepareParams(unittest.TestCase):
    def test_user_overrides_stage_default(self):
        stage = {"symbolrate": 72e3, "rrc_alpha": 0.5, "pll_bw": 0.002}
        user = {"pll_bw": 0.01}
        merged = prepare_params(stage, user)
        self.assertEqual(merged["symbolrate"], 72e3)   # 默认保留
        self.assertEqual(merged["rrc_alpha"], 0.5)     # 默认保留
        self.assertEqual(merged["pll_bw"], 0.01)       # 用户覆盖

    def test_user_param_not_in_stage_is_added(self):
        stage = {"symbolrate": 72e3}
        user = {"extra_tuning": True}
        merged = prepare_params(stage, user)
        self.assertTrue(merged["extra_tuning"])

    def test_empty_inputs(self):
        self.assertEqual(prepare_params({}, None), {})
        self.assertEqual(prepare_params({"a": 1}, None), {"a": 1})


class TestRunPipeline(unittest.TestCase):
    """调度器：层级跳过 + 未实现 stage 容错。纯调度逻辑，不喂真实信号。"""

    def test_unknown_pipeline_returns_failure_no_raise(self):
        res = run_pipeline("does_not_exist", input_data=None)
        self.assertFalse(res["success"])
        self.assertIn("未知", res["error"])

    def test_unknown_input_level_returns_failure(self):
        res = run_pipeline("goes_hrit", input_data=None, input_level="bogus")
        self.assertFalse(res["success"])
        self.assertIn("未知 input_level", res["error"])

    def test_unregistered_stage_returns_not_implemented_no_raise(self):
        # goes_hrit 没有注册任何处理函数；从 baseband 跑应在第一个 soft stage 停下
        res = run_pipeline("goes_hrit", input_data=None)
        self.assertFalse(res["success"])
        self.assertIn("not implemented", res["error"])
        self.assertIn("soft", res["error"])

    def test_input_level_skips_preceding_stages(self):
        # 从 baseband：停在 soft（psk_demod 未实现）
        r_base = run_pipeline("goes_hrit", input_data=None, input_level="baseband")
        self.assertIn("soft", r_base["error"])
        # 从 soft：跳过 soft，应停在更深的 cadu
        r_soft = run_pipeline("goes_hrit", input_data=None, input_level="soft")
        self.assertFalse(r_soft["success"])
        self.assertIn("cadu", r_soft["error"])
        # 两次失败的 stage 层级不同，证明前置 stage 被跳过
        self.assertNotIn("soft", r_soft["error"].split("(")[1])

    def test_already_at_products_is_success_passthrough(self):
        # 数据已在 products 层 -> 无 stage 需执行，直接透传成功
        res = run_pipeline("goes_hrit", input_data="ALREADY_PRODUCTS",
                           input_level="products")
        self.assertTrue(res["success"])
        self.assertEqual(res["output"], "ALREADY_PRODUCTS")

    def test_progress_callback_called(self):
        calls = []
        run_pipeline("goes_hrit", input_data=None, input_level="baseband",
                     progress_cb=lambda f, name: calls.append((f, name)))
        # 至少应回调一次（到达 soft stage 前的 fraction 报告）
        self.assertGreaterEqual(len(calls), 1)
        for f, _name in calls:
            self.assertGreaterEqual(f, 0.0)
            self.assertLessEqual(f, 1.0)

    def test_registered_handler_with_bad_input_does_not_raise(self):
        # noaa_apt 的 products 层已挂接 decode_apt；从 soft 层（音频已解调好）起步，
        # 传 None 进去不应让调度器崩溃，而应收敛为 success=False
        # （解码器内部异常被 try/except 包住）。
        res = run_pipeline("noaa_apt", input_data=None, input_level="soft")
        self.assertFalse(res["success"])
        self.assertIn("noaa_apt_decode", res["error"])

    def test_decode_stats_keys_defined(self):
        # 标准化统计 key 至少包含 APT 已有的 lock_ratio
        self.assertIn("lock_ratio", DECODE_STATS_KEYS)


if __name__ == "__main__":
    unittest.main()

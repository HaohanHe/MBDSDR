# SPDX-License-Identifier: MIT
"""sat_pipeline_runner 的 stage 数据契约（satdump 机制 E）。

背景：`register_stage_handler` 原来只做绑定，`run_pipeline` 只按 level 顺序
串联，接错层要到运行期才炸。现在 handler 可声明 `inputs` / `outputs`，
调度器在执行前校验相邻 stage 是否接得上；未声明的旧 handler 不参与校验，
行为与以前逐字一致。

运行: python -m pytest tests/test_sat_pipeline_contracts.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sat_pipeline_params import (  # noqa: E402
    SATELLITE_PIPELINES,
    PipelineStage,
    SatellitePipeline,
    list_pipelines,
)
from mbdsdr_ai.sat_pipeline_runner import (  # noqa: E402
    STAGE_CONTRACTS,
    STAGE_HANDLERS,
    _check_stage_contracts,
    register_stage_handler,
    run_pipeline,
)


def _pipeline(pid, stages):
    return SatellitePipeline(
        pipeline_id=pid,
        name=pid,
        frequencies=[("test", 137.1e6)],
        recommended_samplerate=1e6,
        stages=[PipelineStage(level="baseband", module="")] + stages,
    )


class _Base(unittest.TestCase):
    def install_pipeline(self, pid, stages):
        SATELLITE_PIPELINES[pid] = _pipeline(pid, stages)
        self.addCleanup(SATELLITE_PIPELINES.pop, pid, None)

    def install_handler(self, module, fn, **contract):
        register_stage_handler(module, fn, **contract)
        self.addCleanup(STAGE_HANDLERS.pop, module, None)
        self.addCleanup(STAGE_CONTRACTS.pop, module, None)


class TestStageContracts(_Base):
    def test_compatible_declared_chain_runs(self):
        calls = []

        def a(data, params):
            calls.append("a")
            return {"output": "CADU", "stats": {}, "success": True}

        def b(data, params):
            calls.append(("b", data))
            return {"output": "IMG", "stats": {}, "success": True}

        self.install_handler("__test_demod_a", a,
                             inputs=("iq",), outputs=("cadu",))
        self.install_handler("__test_decode_b", b,
                             inputs=("cadu",), outputs=("images",))
        self.install_pipeline("__test_contract_ok", [
            PipelineStage(level="soft", module="__test_demod_a"),
            PipelineStage(level="products", module="__test_decode_b"),
        ])

        res = run_pipeline("__test_contract_ok", input_data="IQ")
        self.assertTrue(res["success"], res)
        self.assertEqual(res["output"], "IMG")
        self.assertEqual(calls, ["a", ("b", "CADU")])

    def test_mismatched_declared_chain_fails_before_execution(self):
        calls = []

        def a(data, params):
            calls.append("a")
            return {"output": "CADU", "stats": {}, "success": True}

        def b(data, params):
            calls.append("b")
            return {"output": "IMG", "stats": {}, "success": True}

        self.install_handler("__test_demod_a", a,
                             inputs=("iq",), outputs=("cadu",))
        self.install_handler("__test_decode_b", b,
                             inputs=("audio",), outputs=("images",))
        self.install_pipeline("__test_contract_bad", [
            PipelineStage(level="soft", module="__test_demod_a"),
            PipelineStage(level="products", module="__test_decode_b"),
        ])

        res = run_pipeline("__test_contract_bad", input_data="IQ")
        self.assertFalse(res["success"])
        self.assertIn("类型不兼容", res["error"])
        self.assertIn("__test_decode_b", res["error"])
        self.assertEqual(calls, [], "校验必须发生在执行任何 handler 之前")

    def test_undeclared_stage_keeps_old_behavior(self):
        calls = []

        def a(data, params):
            calls.append("a")
            return {"output": "ANY", "stats": {}, "success": True}

        def b(data, params):
            calls.append("b")
            return {"output": "IMG", "stats": {}, "success": True}

        # a 未声明类型：不参与校验，接得上与否都照旧执行。
        self.install_handler("__test_plain_a", a)
        self.install_handler("__test_decode_b", b,
                             inputs=("audio",), outputs=("images",))
        self.install_pipeline("__test_contract_legacy", [
            PipelineStage(level="soft", module="__test_plain_a"),
            PipelineStage(level="products", module="__test_decode_b"),
        ])

        res = run_pipeline("__test_contract_legacy", input_data="IQ")
        self.assertTrue(res["success"], res)
        self.assertEqual(calls, ["a", "b"])

    def test_repo_pipelines_have_no_contract_conflict(self):
        self.assertGreaterEqual(len(list_pipelines()), 6)
        for pipe in list_pipelines():
            with self.subTest(pipeline=pipe.pipeline_id):
                err = _check_stage_contracts(pipe.stages[1:])
                self.assertEqual(err, "",
                                 f"{pipe.pipeline_id} 的 stage 契约接不上: {err}")


if __name__ == "__main__":
    unittest.main()

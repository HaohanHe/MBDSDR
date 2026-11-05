"""R5 模拟替代路径的契约钉扎：结论恒 SIMULATED，机械精度永远不可报。

`ring5_gimbal_sim.build_result()` 是「无真云台」时的替代证据。它一旦被判成
PASS/FAIL/SKIP，或允许 `mechanical_accuracy_claimable=True`，模拟数字就会
混进真机结论。这三条是口径红线，用测试钉死：

1. `status` 恒为 `SIMULATED`（不是 PASS/FAIL/SKIP 中任何一个），checks 全是未判；
2. `metrics.mechanical_accuracy_claimable is False`、`metrics.simulated is True`；
3. `pose_error_by_source.readback.samples == 0`；出现 readback 样本必须直接
   抛 `RuntimeError`（反证见最后一个用例），不许把污染过的样本照常出结论。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HW = ROOT / "scripts" / "hw_acceptance"
if str(HW) not in sys.path:
    sys.path.insert(0, str(HW))

import ring5_gimbal_sim as sim  # noqa: E402


@pytest.fixture(scope="module")
def result():
    return sim.build_result(steps=8)


def test_status_is_simulated_never_pass_fail_skip(result):
    assert result["status"] == sim.STATUS_SIMULATED == "SIMULATED"
    assert result["status"] not in ("PASS", "FAIL", "SKIP")
    assert result["checks"], result["checks"]
    assert all(c["skipped"] for c in result["checks"])


def test_mechanical_accuracy_never_claimable(result):
    m = result["metrics"]
    assert m["mechanical_accuracy_claimable"] is False
    assert m["simulated"] is True
    assert result["device_required"] is False


def test_readback_bucket_must_be_empty(result):
    by_src = result["metrics"]["pose_error_by_source"]
    assert int((by_src.get("readback") or {}).get("samples", 0)) == 0
    assert int(by_src["simulated"]["samples"]) > 0


def test_readback_sample_refuses_to_conclude(monkeypatch):
    """反证：模拟后端混进 readback 样本时，必须拒绝出结论。"""

    def fake_pass(**_kwargs):
        return {
            "pose_error_by_source": {"readback": {"samples": 3},
                                     "simulated": {"samples": 0}},
            "cmd_vs_truth": {}, "pose_stats": {}, "lag_cmd_vs_pose": {},
            "commands_sent": 0, "dropped": 0, "stopped": False, "error": "",
        }

    monkeypatch.setattr(sim, "run_simulated_pass", fake_pass)
    with pytest.raises(RuntimeError, match="readback"):
        sim.build_result(steps=2)

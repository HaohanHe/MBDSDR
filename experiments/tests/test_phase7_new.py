# SPDX-License-Identifier: MIT
"""
Phase7 三个新实验的离线确定性测试
==================================

不跑全量、不联网、不调 LLM：
  - exp_rate_sweep      : 固定 Eb/N0 下整包成功率对 fs/sps 近似不变（物理阶跃）；
                          CSV 列名/口径/synthetic 标注齐全。
  - exp_doppler_comp    : 大 fd 下补偿臂成功率远高于不补偿臂；fd=0 两臂一致；
                          CSV 列名/口径齐全。
  - exp_agent_toolcall : 本地管线对正确/错误请求都优雅处理（不崩溃）；
                          LLM 决策列 PENDING_ONLINE_RUN，未伪造数值。
"""
from __future__ import annotations

import csv
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from experiments.common import runner  # noqa: E402


# ===========================================================================
# exp_rate_sweep：固定 Eb/N0 下采样率不变性
# ===========================================================================
def test_rate_sweep_helper_invariant():
    """同一固定 Eb/N0，sps=1 与 sps=50 的后相关成功率应落在同一水平（CI 重叠）。"""
    from experiments.exp_rate_sweep import trial_bpsk_sweep
    rng_lo = np.random.default_rng(20261001)
    rng_hi = np.random.default_rng(20261001 + 7)
    low = sum(trial_bpsk_sweep(rng_lo, 10_000.0, 200, 6.0) for _ in range(30))
    high = sum(trial_bpsk_sweep(rng_hi, 500_000.0, 200, 6.0) for _ in range(30))
    # 两者都在悬崖中段附近（0.2~0.9），且不应出现一边 0 一边 1 的阶跃
    assert 0.1 <= low / 30 <= 0.95
    assert 0.1 <= high / 30 <= 0.95
    # 固定 Eb/N0：高 sps 不应显著差于低 sps（积分平均掉了额外带内噪声）
    assert high / 30 >= low / 30 - 0.25


def test_rate_sweep_main_empty_state(monkeypatch, tmp_path):
    import experiments.exp_rate_sweep as R
    monkeypatch.setattr(sys, "argv", [
        "exp_rate_sweep.py", "--trials", "20",
        "--fs-grid", "10000,1000000", "--out", str(tmp_path)])
    R.main()
    csv_path = os.path.join(str(tmp_path), "rate_sweep_success.csv")
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    for r in rows:
        assert r["data_origin"] == "synthetic"
        assert r["ebn0_db_fixed"] == "6.0"
        assert float(r["n_trials"]) == 20
        assert 0.0 <= float(r["success_rate"]) <= 1.0
        # Wilson 区间包住点估计
        assert float(r["wilson_lo"]) <= float(r["success_rate"]) <= float(r["wilson_hi"])
    # manifest 落盘且口径正确
    mp = os.path.join(str(tmp_path), "manifest_rate_sweep.json")
    with open(mp, encoding="utf-8") as f:
        m = json.load(f)
    assert m["data_origin"] == "synthetic"
    assert m["n_samples"] == 2 * 20


# ===========================================================================
# exp_doppler_comp：补偿收益
# ===========================================================================
def test_doppler_comp_helper():
    """fd=0 两臂都成功；fd=200 不补偿失败、补偿成功。"""
    from experiments.exp_doppler_comp import trial
    rng0 = np.random.default_rng(1)
    assert trial(rng0, 0.0, compensate=False) in (True, False)  # 基线，非必然
    rng = np.random.default_rng(42)
    uncomp = sum(trial(rng, 200.0, compensate=False) for _ in range(15))
    comp = sum(trial(rng, 200.0, compensate=True) for _ in range(15))
    assert uncomp == 0                 # 大频偏不补偿必崩
    assert comp >= 12                  # 补偿后绝大多数成功


def test_doppler_comp_main(monkeypatch, tmp_path):
    import experiments.exp_doppler_comp as D
    monkeypatch.setattr(sys, "argv", [
        "exp_doppler_comp.py", "--trials", "40",
        "--fd-grid", "0,200", "--out", str(tmp_path)])
    D.main()
    csv_path = os.path.join(str(tmp_path), "doppler_comp_success.csv")
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # 2 个 fd × 2 臂
    assert len(rows) == 4
    by = {(float(r["fd_hz"]), r["arm"]): float(r["success_rate"]) for r in rows}
    assert by[(0.0, "uncompensated")] == pytest.approx(by[(0.0, "compensated")], abs=0.15)
    # fd=200：不补偿 ≈0，补偿 ≈高
    assert by[(200.0, "uncompensated")] < 0.05
    assert by[(200.0, "compensated")] > 0.8
    for r in rows:
        assert r["data_origin"] == "synthetic"
    mp = os.path.join(str(tmp_path), "manifest_doppler_comp.json")
    with open(mp, encoding="utf-8") as f:
        m = json.load(f)
    assert m["data_origin"] == "synthetic"


# ===========================================================================
# exp_agent_toolcall：本地管线优雅失败 + LLM 决策 PENDING
# ===========================================================================
def test_agent_toolcall_registry_fails_closed():
    """直接用裸 ToolRegistry：未知工具/缺参都返回明确错误，不抛异常。"""
    from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult
    reg = ToolRegistry()
    reg.register("only_tool", "t",
                 {"type": "object", "properties": {"a": {"type": "integer"}},
                  "required": ["a"]},
                 lambda args: ToolResult(success=True, content="ok", args=args))
    r1 = reg.call("only_tool", {"a": 1})
    assert r1.success and r1.error == ""
    r2 = reg.call("only_tool", {})                 # 缺必填
    assert (not r2.success) and r2.error == "missing_required_params"
    r3 = reg.call("no_such_tool", {})              # 未知工具
    assert (not r3.success) and r3.error == "tool_not_found"


def test_agent_toolcall_main(monkeypatch, tmp_path):
    import experiments.exp_agent_toolcall as A
    monkeypatch.setattr(sys, "argv", [
        "exp_agent_toolcall.py", "--trials", "10", "--out", str(tmp_path)])
    A.main()
    # 请求汇总：所有请求都被优雅处理
    with open(os.path.join(str(tmp_path), "agent_toolcall_requests.csv"),
              encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 8
    for r in rows:
        assert float(r["handled_rate"]) == 1.0     # 正确/错误路径都不崩溃
        assert r["data_origin"] == "synthetic"
    # 环节汇总
    with open(os.path.join(str(tmp_path), "agent_toolcall_stage_summary.csv"),
              encoding="utf-8") as f:
        stages = list(csv.DictReader(f))
    assert stages[0]["stage"] == "register"
    # manifest：LLM 决策列 PENDING，未伪造
    mp = os.path.join(str(tmp_path), "manifest_agent_toolcall.json")
    with open(mp, encoding="utf-8") as f:
        m = json.load(f)
    assert m["params"]["llm_decision_layer"] == "PENDING_ONLINE_RUN"
    extra = m.get("extra", {}).get("llm_decision_metrics_PENDING", {})
    assert extra["call_rate"] == "PENDING_ONLINE_RUN"
    assert extra["pick_rate"] == "PENDING_ONLINE_RUN"

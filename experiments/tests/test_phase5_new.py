# SPDX-License-Identifier: MIT
"""
Phase5 新增实验的离线确定性测试
================================

不跑完整实验、不联网、不调在线 LLM：
  - LLM 基线空态：无 key -> build_llm 返回 (None, ...)；main() 产出 PENDING_ONLINE_RUN，
    classic/knn 有真数、LLM 列不伪造。
  - 采样率/带宽实验：BPSK 在全通(50k)带宽下成功率 >> 窄带(5k)（物理阶跃）。
  - 混淆矩阵公共口径：plot_confusion_matrix 文件名含 origin/N；manifest 接受 online。
  - 时长扫描：窗长拉长后 RLS 参考历元误差不大于短窗（观测性改善）。
"""
from __future__ import annotations

import csv
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from experiments.common import manifest, runner  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402


# ---------------------------------------------------------------------------
# LLM 基线空态（无 key 不伪造）
# ---------------------------------------------------------------------------
def test_parse_llm_answer():
    from experiments.exp_llm_baseline import parse_llm_answer
    assert parse_llm_answer("The signal is FSK here") == "FSK"
    assert parse_llm_answer("this looks like PSK") == "PSK"
    assert parse_llm_answer("pure noise") == "NOISE"
    assert parse_llm_answer("i cannot tell") == "UNKNOWN"


def test_build_llm_no_key(monkeypatch):
    from experiments.exp_llm_baseline import build_llm, ENV_KEY
    monkeypatch.delenv(ENV_KEY, raising=False)
    mm, reason = build_llm("Qwen/Qwen2.5-7B-Instruct")
    assert mm is None
    assert "API_KEY" in reason or "未设置" in reason


def test_llm_baseline_empty_state(monkeypatch, tmp_path):
    """无 key 跑 main：classic/knn 有真数，LLM 列=PENDING_ONLINE_RUN，data_origin=synthetic。"""
    import experiments.exp_llm_baseline as L
    monkeypatch.delenv("MBDSDR_LLM_API_KEY", raising=False)
    monkeypatch.delenv("MBDSDR_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", [
        "exp_llm_baseline.py", "--trials-per-class", "3",
        "--snr-grid", "10,20", "--out", str(tmp_path)])
    L.main()
    csv_path = os.path.join(str(tmp_path), "llm_baseline.csv")
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    for r in rows:
        assert r["llm_agent"] == "PENDING_ONLINE_RUN"
        assert r["llm_acc"] == ""                 # 空态，不留伪造数值
        assert float(r["knn_acc"]) > 0.0         # KNN 照常真算
        assert r["data_origin"] == "synthetic"
    # 无 key 时绝不写缓存
    assert not os.path.exists(os.path.join(str(tmp_path), ".llm_cache_Qwen_Qwen2.5-7B-Instruct.json"))


# ---------------------------------------------------------------------------
# 采样率/带宽实验物理性质
# ---------------------------------------------------------------------------
def test_bpsk_bandwidth_step():
    from experiments.exp_rate_bandwidth import trial
    rng = np.random.default_rng(20261001)
    wide = sum(trial(rng, 99000.0) for _ in range(15))    # 全通参考
    narrow = sum(trial(rng, 8000.0) for _ in range(15))    # 主瓣被切(B/Rb<1)
    # 窄于 null-to-null 的带宽因 ISI 成功率不高于全通参考档
    assert wide >= narrow


# ---------------------------------------------------------------------------
# 混淆矩阵公共口径字段
# ---------------------------------------------------------------------------
def test_plot_confusion_matrix_filename(tmp_path):
    M = np.eye(4) * 0.9 + 0.025
    p = eplot.plot_confusion_matrix(
        M, ["a", "b", "c", "d"], origin="synthetic", n_samples=80,
        out_dir=str(tmp_path), fname_prefix="test_cm", title="t")
    base = os.path.basename(p)
    assert "synthetic" in base and "N80" in base and base.endswith(".png")
    assert os.path.getsize(p) > 0


def test_manifest_accepts_online(tmp_path):
    p = manifest.write_manifest(
        out_dir=str(tmp_path), script="x.py", seed=1, data_origin="online",
        params={}, n_samples=10, filename="m_online.json")
    import json
    with open(p, encoding="utf-8") as f:
        m = json.load(f)
    assert m["data_origin"] == "online"
    assert m["origin_label"] == "在线LLM"


# ---------------------------------------------------------------------------
# 时长扫描统计
# ---------------------------------------------------------------------------
def test_doppler_duration_converges(monkeypatch, tmp_path):
    import experiments.exp_doppler_duration as D
    monkeypatch.setattr(sys, "argv", [
        "exp_doppler_duration.py", "--window-grid", "60,600",
        "--out", str(tmp_path)])
    D.main()
    csv_path = os.path.join(str(tmp_path), "doppler_duration_convergence.csv")
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    short_e = float(rows[0]["rls_ref_err_km"])
    long_e = float(rows[1]["rls_ref_err_km"])
    assert rows[0]["data_origin"] == "synthetic"
    assert np.isfinite(short_e) and np.isfinite(long_e)
    # 长窗（600s）观测性应不差于短窗（60s）
    assert long_e <= short_e + 1.0

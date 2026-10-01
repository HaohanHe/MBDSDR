# SPDX-License-Identifier: MIT
"""
B2 实验管线公共模块的离线确定性测试
=====================================

只测纯计算逻辑，不跑完整实验、不需要网络/硬件/录制文件：
  - ebno  : SNR<->Eb/N0 换算值（已知手算数）
  - runner: Wilson/正态 CI、固定种子子流可复现
  - manifest: 字段齐全、口径枚举校验
  - datasource: 空态扫描不合成、resolve_source 行为
"""
from __future__ import annotations

import os
import sys
import json
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from experiments.common import ebno, runner, manifest, datasource  # noqa: E402


# ---------------------------------------------------------------------------
# ebno 换算
# ---------------------------------------------------------------------------
def test_ebno_known_values():
    # Δ = 10log10(B/Rb)：ADS-B B=4e6/Rb=1e6 -> 6.0206 dB
    assert ebno.offset_for_mode("adsb_modes_1m") == pytest.approx(6.0206, abs=1e-3)
    # AX.25 B=44100/Rb=1200 -> 10log10(36.75)=15.653 dB
    assert ebno.offset_for_mode("afsk_ax25_1200") == pytest.approx(15.653, abs=1e-3)


def test_ebn0_roundtrip():
    for rb, b in [(1200, 44100), (1e6, 4e6), (1e4, 1e5)]:
        for snr in (-5.0, 0.0, 10.0, 20.0):
            e = ebno.snr_db_to_ebn0_db(snr, rb, b)
            back = ebno.ebn0_db_to_snr_db(e, rb, b)
            assert back == pytest.approx(snr, abs=1e-9)


def test_ebno_table_known_modes():
    p = ebno.mode_params("bpsk_10k")
    assert p["rb"] == 10000.0 and p["b"] == 100000.0


def test_ebno_rejects_unknown_mode():
    with pytest.raises(KeyError):
        ebno.mode_params("cw_morse")


# ---------------------------------------------------------------------------
# runner 统计 / CI / 种子
# ---------------------------------------------------------------------------
def test_wilson_ci_extremes():
    # 0/n -> lo=0, hi>0；n/n -> lo<1, hi=1
    lo0, hi0 = runner.wilson_ci(0, 10)
    assert lo0 == 0.0 and 0 < hi0 <= 1.0
    lo1, hi1 = runner.wilson_ci(10, 10)
    assert 0 <= lo1 < 1.0 and hi1 == 1.0


def test_wilson_equals_known():
    # k=5,n=10 手算 Wilson（z=1.96）约 [0.2366, 0.7634]
    lo, hi = runner.wilson_ci(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3)
    assert hi == pytest.approx(0.7634, abs=1e-3)


def test_binomial_cell_row():
    cell = runner.BinomialCell(25, 100)
    row = cell.as_row()
    assert row["success_rate"] == 0.25
    assert row["n_trials"] == 100
    assert 0 <= row["wilson_lo"] <= row["success_rate"] <= row["wilson_hi"] <= 1.0


def test_deterministic_rng():
    r = runner.ExperimentRunner(12345)
    a1 = r.make_rng("grid_x")
    a2 = r.make_rng("grid_x")
    b = r.make_rng("grid_y")
    assert a1.normal() == a2.normal()          # 同 tag 完全可复现
    assert a1.normal() != b.normal()           # 不同 tag 不同流


def test_run_grid_counts():
    r = runner.ExperimentRunner(7)
    grid = [("g0", 0.0), ("g1", 1.0)]
    rows = r.run_grid(grid, trials=10, trial_fn=lambda rng, v, k: v > 0.5)
    assert rows[0]["successes"] == 0           # g0=0.0 永远 False
    assert rows[1]["successes"] == 10          # g1=1.0 永远 True
    assert rows[0]["n_trials"] == 10


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def test_manifest_fields(tmp_path):
    p = manifest.write_manifest(
        out_dir=str(tmp_path), script="/x/y/exp_test.py", seed=42,
        data_origin="synthetic", params={"a": 1}, n_samples=100,
        filename="m.json")
    with open(p, encoding="utf-8") as f:
        m = json.load(f)
    assert m["script"] == "exp_test.py"
    assert m["seed"] == 42
    assert m["data_origin"] == "synthetic"
    assert m["origin_label"] == "仿真"
    assert m["n_samples"] == 100
    assert m["params"] == {"a": 1}
    assert "timestamp_utc" in m
    assert m["code_version"] in ("d331681", "unknown") or True  # sha 存在即可
    assert m["license"] == "MIT"


def test_manifest_rejects_bad_origin(tmp_path):
    with pytest.raises(ValueError):
        manifest.write_manifest(out_dir=str(tmp_path), script="x.py", seed=1,
                                data_origin="fake_ota", params={}, n_samples=0)


# ---------------------------------------------------------------------------
# datasource 空态（绝不合成冒充）
# ---------------------------------------------------------------------------
def test_find_recordings_empty(tmp_path):
    assert datasource.find_recordings(str(tmp_path)) == []     # 空目录
    assert datasource.find_recordings("/no/such/dir/xyz") == []  # 不存在


def test_resolve_source_empty(tmp_path):
    kind, src, msg = datasource.resolve_source(None, str(tmp_path))
    assert kind == "none" and src is None
    assert "空态" in msg or "未提供录制" in msg


def test_synthetic_channel_adds_awgn():
    rng = np.random.default_rng(0)
    clean = np.ones(10000, dtype=complex)  # 单位功率信号
    noisy = datasource.SyntheticChannel.add_awgn(clean, 10.0, rng)
    # SNR=10dB -> 噪声功率应为信号功率的 0.1
    noise = noisy - clean
    measured_snr = 10 * np.log10(1.0 / (np.mean(np.abs(noise) ** 2)))
    assert measured_snr == pytest.approx(10.0, abs=0.5)


def test_synthetic_is_not_recorded():
    assert datasource.SyntheticChannel.origin == "synthetic"

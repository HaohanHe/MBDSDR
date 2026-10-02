# SPDX-License-Identifier: MIT
"""
Phase8 P1：真机录制回填（exp_ota_run.py）确定性测试
=====================================================

红线验证：
  * 数字只来自注入的录制文件（用仓库真实 encode/modulate 造一段合法 SigMF，
    等价于 "onboard record 写出的录制"），脚本不合成信号。
  * 有录制 -> CSV/图/manifest 全落 recorded 口径；指标数字来自录制。
  * 空态（无 .sigmf-data / 指定文件不存在）-> N=0、退出码 0、只写空态 manifest、
    不产 CSV/图。
  * 口径字段：任何路径下 data_origin 都不得是 synthetic。
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

from experiments import exp_ota_run as E  # noqa: E402


# ---------------------------------------------------------------------------
# 夹具：造一段合法 SigMF（含一帧真实调制 ADS-B + 固定种子噪声）
# ---------------------------------------------------------------------------
def _make_sigmf_dir(root: str, mode: str = "adsb", with_messages: bool = True) -> str:
    """在 root 下写一段合法 cf32_le SigMF（+ onboard messages.json）。

    用仓库自己的 adsb.encode/modulate 造帧——这就是 onboard record 步落地的
    同一种文件；测试里它代表"真机录回来的那一段"。数字由此经真实解码器得出。
    """
    from mbdsdr_ai import adsb

    d = os.path.join(root, "recording_dir")
    os.makedirs(d, exist_ok=True)
    fs = 2_400_000.0

    me7 = bytes.fromhex("784f5e3a594620")
    frame = adsb.build_long_frame(0x11, 0xABCDEF, me7)
    sig = adsb.modulate_baseband(frame, fs=fs, lead_us=20, amplitude=1.0)
    rng = np.random.default_rng(20261002)
    noise = (rng.standard_normal(200000) + 1j * rng.standard_normal(200000)) * 0.05
    iq = np.concatenate([noise[:50000], sig, noise[50000:]]).astype(np.complex64)

    base = os.path.join(d, "20260101_000000_1090000000Hz_adsb")
    iq.tofile(base + ".sigmf-data")
    meta = {
        "global": {
            "core:datatype": "cf32_le", "core:sample_rate": fs,
            "core:version": "1.0.0", "core:num_channels": 1,
            "core:frequency": 1090e6, "core:hw": "RTL-SDR-fixture",
            "core:author": "pytest", "core:num_samples": int(iq.size),
            "core:description": "pytest fixture",
        },
        "captures": [{
            "core:sample_start": 0, "core:frequency": 1090e6,
            "core:datetime": "2026-01-01T00:00:00Z",
            "mbdsdr:time_source": "system",
            "mbdsdr:gain_db": 24.0, "mbdsdr:mode": mode,
        }],
        "annotations": [],
    }
    with open(base + ".sigmf-meta", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    if with_messages:
        with open(os.path.join(d, "20260101_000000_adsb_messages.json"), "w") as f:
            json.dump({"tool": "onboard", "mode": mode,
                       "frames": [{"icao": "ABCDEF"}]}, f)
    return d


# ---------------------------------------------------------------------------
# 正向：有录制 -> recorded 口径 CSV/图/manifest
# ---------------------------------------------------------------------------
def test_recorded_full_products(tmp_path):
    rec_dir = _make_sigmf_dir(str(tmp_path))
    out = str(tmp_path / "out")
    rc = E.main(["--recordings-dir", rec_dir, "--out", out,
                 "--no-doppler"])
    assert rc == 0

    # CSV：1 行，口径 recorded，数字来自录制
    csv_path = os.path.join(out, "ota_recorded_metrics.csv")
    assert os.path.exists(csv_path)
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    r = rows[0]
    assert r["data_origin"] == "recorded"          # 口径红线
    assert r["data_origin"] != "synthetic"
    assert r["mode"] == "adsb"
    assert int(float(r["fs_hz"])) == 2_400_000
    assert int(r["n_iq_samples"]) > 0              # 数字来自注入录制
    # 我们注入了一帧合法 ADS-B：CRC 通过 >=1；候选 >= CRC 通过
    assert int(r["n_crc_ok"]) >= 1
    assert int(r["n_candidates"]) >= int(r["n_crc_ok"])
    # SNR 估算存在、未标定；Eb/N0 由 fs/Rb 折算
    assert r["snr_estimate_db"] not in ("", None)
    assert r["snr_calibrated"] in ("False", "false", False)
    assert r["ebn0_db_estimate"] not in ("", None)
    # AMR：只报预测，不报准确率（真实录制无标签）
    assert r["amr_predicted"] not in ("", "ERROR")
    assert r["amr_accuracy"] == "N/A_no_ground_truth_labels"
    # onboard 既有帧数被只读汇总（交叉核对）
    assert int(r["n_onboard_frames"]) == 1

    # 图：有可估 Eb/N0 的点 -> 落 recorded 图
    fig_dir = os.path.join(out, "figures")
    figs = [x for x in os.listdir(fig_dir) if x.endswith(".png")]
    assert figs, "应有 recorded 口径图"
    assert all("recorded" in x for x in figs)

    # manifest：recorded 口径，样本数=前导候选
    mp = os.path.join(out, "manifest_ota_run.json")
    with open(mp, encoding="utf-8") as f:
        m = json.load(f)
    assert m["data_origin"] == "recorded"
    assert m["n_samples"] == int(r["n_candidates"]) >= 1
    assert m["license"] == "MIT"


# ---------------------------------------------------------------------------
# 空态：空目录 / 不存在的文件 -> N=0，退出码 0，不产 CSV
# ---------------------------------------------------------------------------
def test_empty_state_no_recordings(tmp_path):
    empty_dir = str(tmp_path / "empty")
    os.makedirs(empty_dir)
    out = str(tmp_path / "out")
    rc = E.main(["--recordings-dir", empty_dir, "--out", out, "--no-amr", "--no-doppler"])
    assert rc == 0
    # 不产 CSV
    assert not os.path.exists(os.path.join(out, "ota_recorded_metrics.csv"))
    # 空态 manifest：N=0，口径 recorded，明确提示
    with open(os.path.join(out, "manifest_ota_run.json"), encoding="utf-8") as f:
        m = json.load(f)
    assert m["n_samples"] == 0
    assert m["data_origin"] == "recorded"          # 空态也不是 synthetic
    assert m["params"]["status"] == "empty_state_no_recording"


def test_empty_state_missing_recording_file(tmp_path):
    out = str(tmp_path / "out")
    rc = E.main(["--recording", "/no/such/rec.sigmf-data",
                 "--out", out, "--no-amr", "--no-doppler"])
    assert rc == 0
    assert not os.path.exists(os.path.join(out, "ota_recorded_metrics.csv"))
    with open(os.path.join(out, "manifest_ota_run.json"), encoding="utf-8") as f:
        m = json.load(f)
    assert m["n_samples"] == 0


# ---------------------------------------------------------------------------
# 口径字段红线：recorded 绝不被写成 synthetic
# ---------------------------------------------------------------------------
def test_origin_never_synthetic(tmp_path):
    rec_dir = _make_sigmf_dir(str(tmp_path))
    out = str(tmp_path / "out")
    E.main(["--recordings-dir", rec_dir, "--out", out, "--no-amr", "--no-doppler"])
    with open(os.path.join(out, "ota_recorded_metrics.csv"), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        assert r["data_origin"] == "recorded"
        assert "synthetic" not in r["data_origin"]
    with open(os.path.join(out, "manifest_ota_run.json"), encoding="utf-8") as f:
        m = json.load(f)
    assert m["data_origin"] == "recorded"

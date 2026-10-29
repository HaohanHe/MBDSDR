#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""tools/ota_demo 的行为测试（零硬件、离线、确定性）。

钉住三件事：
  1. 一键复现真的能跑出真实 OTA 的解码结果（不是合成信号、不是占位）。
  2. 数字自洽：字节数 / 样本数 / 时长 / 元数据互相咬合。
  3. 诚实边界不许被删：`not_verified` 必须始终出现在输出里——这是对外
     不得声称"已验证"那些链路的唯一护栏。

不 mock 成功：缺 matplotlib 只降级不出图，缺样本/不自洽如实报错退出。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from tools.ota_demo import ota_demo as od  # noqa: E402


def test_sample_bytes_and_count_self_consistent():
    s = od._load_sample()
    assert s["n_bytes"] == s["n_samples"] * od.BYTES_PER_SAMPLE
    assert s["n_samples"] == od.N_SAMPLES
    assert abs(s["duration_s"] - od.N_SAMPLES / od.SAMPLE_RATE) < 1e-9


def test_sample_metadata_agrees_with_bytes():
    s = od._load_sample()
    assert s["provenance"] == "real_ota"
    assert s["datatype"] == "cu8"
    assert s["center_freq_hz"] == 88_100_000
    assert s["meta_duration_s"] == pytest.approx(0.512)


def test_spectrum_has_real_carrier_above_noise():
    s = od._load_sample()
    spec = od._spectrum(s["iq"])
    # 真实广播台：载波明显高于底噪，且几乎不削顶（增益调过）
    assert spec["peak_over_noise_db"] > 10.0
    assert spec["clip_fraction"] < 0.02


def test_demodulation_yields_finite_pilot_and_audio():
    s = od._load_sample()
    dem = od._demod(s["iq"])
    # 该缺陷由真实样本咬出：ba 系数溢出时 pilot_snr 为 nan、stereo 恒 False
    assert dem["pilot_snr_db"] is not None
    assert dem["pilot_snr_db"] == dem["pilot_snr_db"]  # 非 NaN
    assert dem["stereo"] is True
    assert dem["audio_rms"] > 0.01
    assert dem["audio_band_energy_frac"] > 0.5
    assert len(dem["left"]) == len(dem["right"])


def test_main_ok_writes_artifacts(tmp_path):
    rc = od.main(["--out-dir", str(tmp_path)])
    assert rc == 0
    pngs = [f for f in os.listdir(tmp_path) if f.endswith(".png")]
    assert len(pngs) == 2


def test_main_json_has_required_fields(tmp_path, capsys):
    rc = od.main(["--json", "--out-dir", str(tmp_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] in ("ok", "degraded")
    assert payload["hardware_required"] is False
    assert payload["network_required"] is False
    assert payload["sample"]["provenance"] == "real_ota"
    assert payload["spectrum"]["peak_over_noise_db"] > 10.0
    assert payload["demodulation"]["stereo"] is True


def test_honesty_boundary_is_always_emitted(tmp_path, capsys):
    """诚实边界不许被删：删了就等于对外声称全部已验证。"""
    assert od.main(["--json", "--out-dir", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["not_verified"]
    joined = " ".join(payload["not_verified"]).lower()
    assert "ads-b" in joined
    assert "lrpt" in joined


def test_missing_sample_exits_3(tmp_path, monkeypatch):
    monkeypatch.setattr(od, "FIXTURE_DIR", str(tmp_path / "nope"))
    assert od.main(["--json", "--out-dir", str(tmp_path)]) == 3


def test_inconsistent_sample_count_exits_4(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(od, "N_SAMPLES", 12345)
    assert od.main(["--json", "--out-dir", str(tmp_path)]) == 4
    assert "不自洽" in json.loads(capsys.readouterr().out)["error"]


def test_missing_matplotlib_degrades_not_fails(tmp_path, monkeypatch, capsys):
    """缺 matplotlib 只降级：不出图但指标照出，不补假图。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "matplotlib" or name.startswith("matplotlib."):
            raise ImportError("no matplotlib")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert od.main(["--json", "--out-dir", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "degraded"
    assert payload["artifacts"]["rendered"] is False
    assert payload["spectrum"]["peak_over_noise_db"] > 10.0

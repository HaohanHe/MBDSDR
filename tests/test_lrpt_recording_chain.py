# SPDX-License-Identifier: MIT
"""LRPT 真录链接线（2026-11-11，工单 P1-23）。

旧行为：`lrpt_pipeline` 的真录链只被测试用，气象面板 `_run_meteor` 一律走
`meteor_sat` 的 1DFCDC 简化帧链 —— 真录 CADU 帧首是 CCSDS ASM，图像层不认，
于是真录永远停在「帧约定不一致」，一张图都出不来。

本文件钉三件事：
  1. 真录链对噪点输入**诚实自曝**（stage/error 人话，不出图）；
  2. 面板**先**调真录链，成功就用它的 PNG（不再走旧链）；
  3. 真录链失败时**回退**旧链，并把两条链的原因一起报出来。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import lrpt_pipeline as lp  # noqa: E402


# --------------------------------------------------------------------------- #
# 1) 链本身：噪点必须诚实失败，不许拿噪点图冒充
# --------------------------------------------------------------------------- #
def test_noise_input_is_rejected_with_stage_reason():
    rng = np.random.default_rng(20261111)
    iq = (rng.standard_normal(480_000)
          + 1j * rng.standard_normal(480_000)).astype(np.complex64)
    res = lp.decode_recording_to_image(iq, 2_400_000)
    assert res.success is False
    assert res.rgb is None
    assert res.stage in ("sync", "decode"), res.stage
    assert res.error, "失败必须给人话原因"
    assert res.stats["chain"] == "lrpt_pipeline.real"
    assert res.stats["resampled_hz"] == 144_000
    assert res.stats["resample_up"] == 3 and res.stats["resample_down"] == 50


def test_resample_targets_integer_symbol_multiple():
    iq = np.zeros(187_500, dtype=np.complex64)
    out, target, up, down = lp._resample_for_symbols(iq, 187_500, 72_000, 2)
    assert target == 144_000
    assert (up, down) == (96, 125)
    assert out.size == 144_000


def test_empty_input_reports_input_stage():
    res = lp.decode_recording_to_image(np.zeros(0, dtype=np.complex64), 2_400_000)
    assert res.success is False and res.rgb is None
    assert res.stage == "input" and "空" in res.error


# --------------------------------------------------------------------------- #
# 2)/3) 面板接线：真录链优先 + 旧链回退
# --------------------------------------------------------------------------- #
def _make_worker(monkeypatch, tmp_path):
    import weather_panel as wp
    from mbdsdr_ai import meteor_sat as ms

    iq_file = tmp_path / "fake.iq"
    iq_file.write_bytes(b"")
    monkeypatch.setattr(wp, "_load_complex64",
                        lambda _p: np.zeros(32, dtype=np.complex64))
    worker = wp.DecodeWorker({
        "family": "meteor", "iq_file": str(iq_file),
        "sample_rate": 2_400_000, "out_png": str(tmp_path / "out.png"),
        "satellite": "METEOR-M2 (LRPT)",
    })
    return worker, ms


def test_panel_prefers_real_chain_and_uses_its_png(monkeypatch, tmp_path):
    worker, ms = _make_worker(monkeypatch, tmp_path)
    calls = {"legacy": 0}

    def _real(iq, fs, **_kw):
        r = lp.RecordingImageResult(success=True, stage="done")
        r.rgb = np.full((16, 32, 3), 120, dtype=np.uint8)
        r.stats = {"chain": "lrpt_pipeline.real", "n_valid_vcdu": 4,
                   "n_image_packets": 6}
        return r

    def _legacy(*_a, **_k):
        calls["legacy"] += 1
        return []

    monkeypatch.setattr(lp, "decode_recording_to_image", _real)
    monkeypatch.setattr(ms, "demodulate_lrpt", _legacy)

    results = []
    worker.finished.connect(results.append)
    worker._run_meteor()

    assert len(results) == 1
    r = results[0]
    assert r["ok"] is True
    assert r["width"] == 32 and r["height"] == 16
    assert "链路：真录链" in r["annotation"]
    assert "VCDU 4" in r["annotation"] and "图像包 6" in r["annotation"]
    assert os.path.isfile(r["png"]), "真录链出图必须落盘"
    assert calls["legacy"] == 0, "真录链成功后不得再走旧链"


def test_panel_falls_back_to_legacy_and_reports_both(monkeypatch, tmp_path):
    worker, ms = _make_worker(monkeypatch, tmp_path)
    calls = {"legacy": 0}

    def _real_fail(*_a, **_k):
        return lp.RecordingImageResult(stage="sync", error="帧同步未锁（桩）")

    def _legacy(*_a, **_k):
        calls["legacy"] += 1
        return []

    monkeypatch.setattr(lp, "decode_recording_to_image", _real_fail)
    monkeypatch.setattr(ms, "demodulate_lrpt", _legacy)

    results = []
    worker.finished.connect(results.append)
    worker._run_meteor()

    assert len(results) == 1
    err = results[0]["error"]
    assert results[0]["ok"] is False
    assert "未解出任何 LRPT CADU 帧" in err, "旧链原因不能丢"
    assert "真录链" in err and "帧同步未锁（桩）" in err, "真录链原因必须一起报"
    assert calls["legacy"] == 1

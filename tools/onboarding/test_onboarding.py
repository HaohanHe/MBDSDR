#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MBDSDR Onboarding 离线确定性测试
===================================

不依赖真机硬件：
  * 注入合成 IQ 数据（固定种子），验证 record → decode → output 全链路数字自洽。
  * detect 步：在云 VM 上必然无设备，验证其明确 FAIL 且不 mock。
  * 验证 SigMF 写入后能被 mbdsdr_ai.playback.IQPlayback 回读。
  * 验证 output 产物含口径 / 参数 / 时间戳。

跑法：
  python3 -m pytest tools/onboarding/test_onboarding.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

# 路径引导
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))

from onboard import (  # noqa: E402
    step_capture,
    step_decode,
    step_detect,
    step_output,
    step_record,
    MODES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_out_dir():
    d = tempfile.mkdtemp(prefix="onboard_test_")
    yield d
    # 不主动清理，便于失败时调试


@pytest.fixture
def adsb_synthetic_iq_uint8():
    """生成一帧带真实 ADS-B 前导+报文的 uint8 interleaved IQ（固定种子）。"""
    from mbdsdr_ai import adsb
    fs = 2.4e6
    frame = adsb.build_identification_frame("AABBCC", "CSN1234")
    sig = adsb.modulate_baseband(frame, fs=fs, lead_us=20)
    total = 200_000  # ≈ 83 ms @ 2.4 MS/s
    iq = np.zeros(total, dtype=np.complex64)
    offset = 20_000
    iq[offset:offset + len(sig)] = sig.astype(np.complex64)
    # 加固定种子噪声
    rng = np.random.default_rng(20261001)
    noise = (rng.standard_normal(total) + 1j * rng.standard_normal(total)) * 0.05
    iq = iq + noise
    # 转 uint8 interleaved（rtl_sdr 格式）
    raw = np.empty(total * 2, dtype=np.uint8)
    raw[0::2] = np.clip(iq.real * 50 + 127.5, 0, 255).astype(np.uint8)
    raw[1::2] = np.clip(iq.imag * 50 + 127.5, 0, 255).astype(np.uint8)
    return raw, fs, total


# ---------------------------------------------------------------------------
# 1. detect 步：无设备时明确 FAIL，不 mock
# ---------------------------------------------------------------------------
class TestDetectNoHardware:
    """云 VM 无硬件：detect 必须 FAIL 并给出明确原因，绝不能 PASS（假装有设备）。"""

    def test_detect_fails_on_no_device(self):
        r = step_detect(timeout=30.0)
        assert r.status == "FAIL", f"无硬件时 detect 应 FAIL，实际 {r.status}: {r.message}"
        assert "未检测到 RTL-SDR" in r.message or "RTL-SDR" in r.message
        assert len(r.fixes) > 0, "无设备时必须给出下一步修复建议"

    def test_detect_json_structure(self):
        """detect 结果 detail 里必须有 selfcheck 摘要（复用 hw_selfcheck）。"""
        r = step_detect(timeout=30.0)
        assert "selfcheck_summary" in r.detail
        assert "counts" in r.detail["selfcheck_summary"]


# ---------------------------------------------------------------------------
# 2. record 步：uint8 → SigMF 自洽，能被 IQPlayback 回读
# ---------------------------------------------------------------------------
class TestRecordSigMF:
    def test_record_writes_selfconsistent_sigmf(self, tmp_out_dir, adsb_synthetic_iq_uint8):
        raw, fs, n_samples = adsb_synthetic_iq_uint8
        raw_path = os.path.join(tmp_out_dir, "capture.bin")
        raw.tofile(raw_path)

        rec = step_record(
            raw_path=raw_path,
            out_dir=tmp_out_dir,
            freq_hz=1090e6,
            sample_rate_hz=fs,
            gain_db=24.0,
            mode="adsb",
        )
        assert rec.status == "PASS", f"record 应 PASS: {rec.message}"
        assert rec.detail["n_samples"] == n_samples

        # 验证文件存在
        data_path = rec.detail["sigmf_data"]
        meta_path = rec.detail["sigmf_meta"]
        assert os.path.exists(data_path)
        assert os.path.exists(meta_path)

        # 字节数自洽：cf32_le = 8 bytes/sample
        data_bytes = os.path.getsize(data_path)
        assert data_bytes == n_samples * 8, \
            f"SigMF data 字节数 {data_bytes} ≠ {n_samples}*8 = {n_samples*8}"

        # meta 字段检查
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta["global"]["core:datatype"] == "cf32_le"
        assert meta["global"]["core:sample_rate"] == fs
        assert meta["global"]["core:frequency"] == 1090e6
        assert meta["global"]["core:num_samples"] == n_samples
        assert meta["captures"][0]["core:frequency"] == 1090e6
        assert "core:datetime" in meta["captures"][0]

    def test_sigmf_readable_by_iqplayback(self, tmp_out_dir, adsb_synthetic_iq_uint8):
        """SigMF 写入后必须能被 mbdsdr_ai.playback.IQPlayback 打开。"""
        from mbdsdr_ai.playback import IQPlayback
        raw, fs, n_samples = adsb_synthetic_iq_uint8
        raw_path = os.path.join(tmp_out_dir, "capture.bin")
        raw.tofile(raw_path)

        rec = step_record(raw_path, tmp_out_dir, 1090e6, fs, 24.0, "adsb")
        assert rec.status == "PASS"

        pb = IQPlayback()
        ok = pb.open(rec.detail["sigmf_data"])
        assert ok, "IQPlayback 应能打开刚写入的 SigMF"
        assert pb.n_samples == n_samples
        assert pb.sample_rate == fs
        assert pb.center_freq_hz == 1090e6
        pb.close()


# ---------------------------------------------------------------------------
# 3. decode 步：固定 IQ 输入 → 确定性输出
# ---------------------------------------------------------------------------
class TestDecodeDeterministic:
    def test_adsb_decode_deterministic(self, tmp_out_dir, adsb_synthetic_iq_uint8):
        """同一帧合成 ADS-B IQ，两次解码结果必须一致（确定性）。"""
        raw, fs, n_samples = adsb_synthetic_iq_uint8
        raw_path = os.path.join(tmp_out_dir, "capture.bin")
        raw.tofile(raw_path)
        rec = step_record(raw_path, tmp_out_dir, 1090e6, fs, 24.0, "adsb")
        sigmf_data = rec.detail["sigmf_data"]

        dec1 = step_decode(sigmf_data, "adsb", fs, 1090e6)
        dec2 = step_decode(sigmf_data, "adsb", fs, 1090e6)

        assert dec1.status == "PASS", f"合成 ADS-B 应能解出: {dec1.message}"
        assert dec2.status == "PASS"
        assert dec1.detail["n_frames"] == dec2.detail["n_frames"], \
            "同一输入两次解码帧数应一致（确定性）"
        # 至少解出 1 帧
        assert dec1.detail["n_frames"] >= 1

    def test_decode_fails_without_sigmf(self):
        """不存在的 SigMF 文件 → 明确 FAIL。"""
        dec = step_decode("/nonexistent/path.sigmf-data", "adsb", 2.4e6, 1090e6)
        assert dec.status == "FAIL"
        assert "不存在" in dec.message or "找不到" in dec.message

    def test_decode_rejects_unknown_mode(self):
        dec = step_decode("/tmp/x.sigmf-data", "not_a_mode", 2.4e6, 1090e6)
        assert dec.status == "FAIL"
        assert "不支持的模式" in dec.message


# ---------------------------------------------------------------------------
# 4. output 步：产物含口径 / 参数 / 时间戳
# ---------------------------------------------------------------------------
class TestOutputArtifacts:
    def test_output_writes_manifest_with_origin_params_timestamp(
        self, tmp_out_dir, adsb_synthetic_iq_uint8
    ):
        raw, fs, n_samples = adsb_synthetic_iq_uint8
        raw_path = os.path.join(tmp_out_dir, "capture.bin")
        raw.tofile(raw_path)
        rec = step_record(raw_path, tmp_out_dir, 1090e6, fs, 24.0, "adsb")
        sigmf_data = rec.detail["sigmf_data"]
        dec = step_decode(sigmf_data, "adsb", fs, 1090e6)
        dec.detail["sigmf_data"] = sigmf_data

        out = step_output(
            out_dir=tmp_out_dir,
            mode="adsb",
            freq_hz=1090e6,
            sample_rate_hz=fs,
            n_samples=n_samples,
            decode_result=dec,
            data_origin="captured",
        )
        assert out.status == "PASS"
        artifacts = out.detail["artifacts"]
        assert len(artifacts) >= 3, f"至少应有 messages.json/txt + manifest，实际 {artifacts}"

        # 找 manifest.json 验证内容
        manifest_path = next(a for a in artifacts if a.endswith("manifest.json"))
        with open(manifest_path) as f:
            m = json.load(f)
        assert m["data_origin"] == "captured"
        assert m["params"]["freq_hz"] == 1090e6
        assert m["params"]["sample_rate_hz"] == fs
        assert m["params"]["n_samples"] == n_samples
        assert "timestamp_utc" in m
        assert m["license"] == "MIT"

    def test_output_rejects_bad_origin(self, tmp_out_dir):
        dec = type("FakeDec", (), {"status": "PASS", "message": "",
                                    "detail": {"n_frames": 0}})()
        out = step_output(tmp_out_dir, "adsb", 1090e6, 2.4e6, 1000,
                          dec, data_origin="fake_origin")
        assert out.status == "FAIL"
        assert "非法 data_origin" in out.message


# ---------------------------------------------------------------------------
# 5. capture 步：无 rtl_sdr 时明确失败
# ---------------------------------------------------------------------------
class TestCaptureNoRTLSDR:
    def test_capture_fails_without_rtl_sdr_binary(self):
        """如果 PATH 里没有 rtl_sdr，capture 必须 FAIL（不能假装成功）。"""
        r = step_capture(
            freq_hz=1090e6,
            sample_rate_hz=2.4e6,
            n_samples=1000,
            out_raw_path="/tmp/onboard_test_capture.bin",
        )
        # 在云 VM 上 rtl_sdr 可能在 ~/.local/bin，也可能不在
        # 如果有 rtl_sdr 但没设备，会因为写空文件 FAIL；两种情况都应该 FAIL
        assert r.status in ("FAIL",), \
            f"无硬件环境 capture 应 FAIL，实际 {r.status}: {r.message}"

    def test_capture_rejects_bad_sample_rate(self):
        r = step_capture(1090e6, 100e6, 1000, "/tmp/x.bin")
        assert r.status == "FAIL"
        assert "超出" in r.message or "范围" in r.message

    def test_capture_rejects_bad_freq(self):
        r = step_capture(10e9, 2.4e6, 1000, "/tmp/x.bin")
        assert r.status == "FAIL"
        assert "超出" in r.message or "范围" in r.message


# ---------------------------------------------------------------------------
# 6. 端到端：record → decode → output 全链路数字自洽
# ---------------------------------------------------------------------------
class TestEndToEnd:
    def test_full_pipeline_numbers_selfconsistent(self, tmp_out_dir,
                                                  adsb_synthetic_iq_uint8):
        raw, fs, n_samples = adsb_synthetic_iq_uint8
        raw_path = os.path.join(tmp_out_dir, "capture.bin")
        raw.tofile(raw_path)

        # record
        rec = step_record(raw_path, tmp_out_dir, 1090e6, fs, 24.0, "adsb")
        assert rec.status == "PASS"
        assert rec.detail["n_samples"] == n_samples

        # data 文件字节 = n_samples * 8 (cf32)
        data_bytes = os.path.getsize(rec.detail["sigmf_data"])
        assert data_bytes == n_samples * 8

        # decode
        dec = step_decode(rec.detail["sigmf_data"], "adsb", fs, 1090e6)
        assert dec.status == "PASS"
        dec.detail["sigmf_data"] = rec.detail["sigmf_data"]

        # output
        out = step_output(tmp_out_dir, "adsb", 1090e6, fs, n_samples, dec,
                          data_origin="captured")
        assert out.status == "PASS"

        # manifest 里 n_samples 与 record 一致
        manifest_path = next(a for a in out.detail["artifacts"]
                             if a.endswith("manifest.json"))
        with open(manifest_path) as f:
            m = json.load(f)
        assert m["n_samples"] == n_samples
        assert m["params"]["n_samples"] == n_samples

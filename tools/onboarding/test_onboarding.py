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


# ---------------------------------------------------------------------------
# 7. SSTV 真机闭环（Phase14 P1）
#
# 确定性合成：pysstv 按制式参数把参考图编码成 SSTV 基带音频 →
#   (a) 直接 decode_audio 解码出图（数组入口往返）；
#   (b) 把基带音频 FM 调制成复 IQ → onboard step_decode("sstv") 整条链路出图。
# 无信号（纯噪声）必须诚实空态：success=False 且不伪造 PNG。
# ---------------------------------------------------------------------------
SSTV_TEST_SR = 48000  # sstv_decoder 目标采样率，省去重采样便于快速往返
SSTV_FDEV = 5000.0    # 与 onboard _demod_fm(max_dev=5000) 对齐，解调后线性还原基带


@pytest.fixture(scope="module")
def sstv_baseband():
    """用 pysstv 合成 Martin M1 / Robot 36 基带音频（48000Hz，固定参考图）。"""
    pytest.importorskip("pysstv")
    from PIL import Image
    from pysstv.color import MartinM1, Robot36

    fs = SSTV_TEST_SR
    # Martin M1：均匀灰 320x256
    ref_m = Image.fromarray(np.full((256, 320, 3), 120, np.uint8), "RGB")
    audio_m = np.fromiter(
        MartinM1(ref_m, fs, 16).gen_samples(),
        dtype=np.int16).astype(np.float32) / 32768.0
    # Robot 36：红蓝水平渐变 320x240
    ref_r = np.zeros((240, 320, 3), np.uint8)
    xx = np.arange(320)
    ref_r[:, :, 0] = (xx * 255 // 319).astype(np.uint8)
    ref_r[:, :, 1] = 128
    ref_r[:, :, 2] = (255 - xx * 255 // 319).astype(np.uint8)
    audio_r = np.fromiter(
        Robot36(Image.fromarray(ref_r, "RGB"), fs, 16).gen_samples(),
        dtype=np.int16).astype(np.float32) / 32768.0
    return {"fs": fs, "Martin M1": audio_m, "Robot 36": audio_r}


def _fm_modulate(baseband: np.ndarray, fs: float, fdev: float = SSTV_FDEV):
    """基带音频 → FM 复 IQ（卫星 SSTV 走 FM 语音信道的物理层接线）。"""
    bb = np.asarray(baseband, dtype=np.float64)
    phase_inc = 2 * np.pi * fdev * bb / fs
    return np.exp(1j * np.cumsum(phase_inc)).astype(np.complex64)


class TestSstvDecodeAudioRoundtrip:
    """decode_audio 数组入口：合成 SSTV 音频 → 解码出图（Martin M1 / Robot36）。"""

    def test_martin_m1_roundtrip(self, sstv_baseband, tmp_path):
        from mbdsdr_ai.sstv_decoder import decode_audio
        audio = sstv_baseband["Martin M1"]
        png = str(tmp_path / "martin_m1.png")
        res = decode_audio(audio, sstv_baseband["fs"], out_png=png, mode="auto")
        assert res.get("success") is True, f"Martin M1 解码失败: {res.get('error')}"
        assert res["mode"] == "Martin M1"
        assert res["width"] == 320 and res["height"] == 256
        assert os.path.exists(png) and os.path.getsize(png) > 1000
        # 像素非空：不能是全黑/全空图
        from PIL import Image
        arr = np.asarray(Image.open(png))
        assert arr.size > 0 and np.any(arr > 0), "解码图像素全空"
        assert res["rows_decoded"] >= 200

    def test_robot36_roundtrip(self, sstv_baseband, tmp_path):
        from mbdsdr_ai.sstv_decoder import decode_audio
        audio = sstv_baseband["Robot 36"]
        png = str(tmp_path / "robot36.png")
        res = decode_audio(audio, sstv_baseband["fs"], out_png=png, mode="auto")
        assert res.get("success") is True, f"Robot36 解码失败: {res.get('error')}"
        assert res["mode"] == "Robot 36"
        assert res["width"] == 320 and res["height"] == 240
        assert os.path.exists(png) and os.path.getsize(png) > 1000
        from PIL import Image
        arr = np.asarray(Image.open(png))
        assert arr.size > 0 and np.any(arr > 0), "解码图像素全空"
        assert res["rows_decoded"] >= 200

    def test_memory_mode_returns_image_without_disk(self, sstv_baseband):
        """out_png=None：不写盘，返回内存图。"""
        from mbdsdr_ai.sstv_decoder import decode_audio
        res = decode_audio(sstv_baseband["Robot 36"], sstv_baseband["fs"],
                           out_png=None, mode="Robot 36")
        assert res.get("success") is True
        assert "image" in res and res["image"].shape == (240, 320, 3)
        assert "output_path" not in res

    def test_noise_honest_empty(self, tmp_path):
        """纯噪声：诚实 success=False，不兜底出图。"""
        from mbdsdr_ai.sstv_decoder import decode_audio
        rng = np.random.default_rng(20261002)
        noise = rng.standard_normal(SSTV_TEST_SR * 4).astype(np.float32) * 0.1
        png = str(tmp_path / "should_not_exist.png")
        res = decode_audio(noise, SSTV_TEST_SR, out_png=png, mode="auto")
        assert res.get("success") is False
        assert "image" not in res and "output_path" not in res
        assert not os.path.exists(png), "无信号时不得伪造 PNG"


class TestSstvOnboardChain:
    """onboard sstv 模式：合成 FM IQ → step_decode 正向路径 + 空态。"""

    def test_sstv_positive_chain(self, sstv_baseband, tmp_out_dir):
        iq = _fm_modulate(sstv_baseband["Robot 36"], sstv_baseband["fs"])
        data_path = os.path.join(tmp_out_dir, "capture.sigmf-data")
        iq.tofile(data_path)
        dec = step_decode(data_path, "sstv", sstv_baseband["fs"], 145.8e6)
        assert dec.status == "PASS", f"正向链路应 PASS: {dec.message}"
        png = dec.detail.get("_sstv_output_path")
        assert png and os.path.exists(png) and os.path.getsize(png) > 1000
        sstv_res = dec.detail.get("sstv_result", {})
        assert sstv_res.get("mode") == "Robot 36"

    def test_sstv_empty_state_no_fake_image(self, tmp_out_dir):
        """纯噪声 FM IQ：step_decode sstv 必须 FAIL 且不产出 PNG（诚实空态）。"""
        rng = np.random.default_rng(20261002)
        iq = (rng.standard_normal(SSTV_TEST_SR * 5)
              + 1j * rng.standard_normal(SSTV_TEST_SR * 5)).astype(np.complex64)
        data_path = os.path.join(tmp_out_dir, "noise.sigmf-data")
        iq.tofile(data_path)
        dec = step_decode(data_path, "sstv", float(SSTV_TEST_SR), 145.8e6)
        assert dec.status == "FAIL", f"无信号应诚实 FAIL: {dec.status} {dec.message}"
        assert "_sstv_output_path" not in dec.detail, "无信号时不得给出 PNG 路径"

    def test_sstv_in_modes_registry(self):
        assert "sstv" in MODES
        assert MODES["sstv"]["default_sr"] > 0


# ---------------------------------------------------------------------------
# 8. SSDV 字节流入口（Wave2，fsphil 核心已接通）
#
# onboard ssdv 接收"解调后 256B 包字节流文件"→ SsdvDecoder.feed（同步/RS/CRC）
# → SsdvImage.build 按 MCU 重组标准 JPEG。物理层（AFSK/卷积/解扰→字节）云内无
# 射频留真机。正向链用 SsdvEncoder（测试编码方向）合成包；无字节/无包诚实空态。
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ssdv_byte_stream():
    """用 SsdvEncoder 把小渐变图分包成 SSDV 字节流（callsign 由参数传入）。"""
    pytest.importorskip("PIL")
    from mbdsdr_ai.ssdv_decoder import SsdvEncoder
    arr = np.zeros((64, 64, 3), dtype=np.uint8)
    arr[..., 0] = np.linspace(0, 255, 64, dtype=np.uint8)
    arr[..., 1] = np.linspace(0, 255, 64, dtype=np.uint8)[:, None]
    arr[..., 2] = 128
    enc = SsdvEncoder(callsign="TEST", image_id=1, quality=4, mcu_mode=3)
    packets = enc.encode_image(arr)
    return {"packets": packets, "width": 64, "height": 64}


def _corrupt(pkt: bytes, positions, xor=0xA5):
    b = bytearray(pkt)
    for p in positions:
        b[p] ^= xor
    return bytes(b)


class TestSsdvOnboardSkeleton:
    def test_ssdv_registered_without_hardcoded_freq(self):
        assert "ssdv" in MODES
        # 规格 §6：频率/速率未官方发布前留空，禁硬编码猜测。
        assert MODES["ssdv"]["freq_hint"] is None

    def test_ssdv_rejects_nonexistent_file(self):
        dec = step_decode("/nonexistent/ssdv_bytes.bin", "ssdv", 19200.0, None)
        assert dec.status == "FAIL"
        assert "不存在" in dec.message

    def test_ssdv_empty_bytes_is_honest_empty(self, tmp_out_dir):
        path = os.path.join(tmp_out_dir, "empty_ssdv.bin")
        open(path, "wb").close()  # 0 字节
        dec = step_decode(path, "ssdv", 19200.0, None)
        assert dec.status == "FAIL"
        assert "空" in dec.message
        assert "_ssdv_jpeg_path" not in dec.detail, "无字节时不得给出 JPEG 路径"

    def test_ssdv_garbage_bytes_no_fake_image(self, tmp_out_dir):
        """非空但无有效 256B 包（噪声）：诚实 FAIL，不伪造 JPEG。"""
        rng = np.random.default_rng(20261002)
        junk = rng.integers(0, 256, size=4096, dtype=np.uint8).tobytes()
        path = os.path.join(tmp_out_dir, "junk_ssdv.bin")
        with open(path, "wb") as f:
            f.write(junk)
        dec = step_decode(path, "ssdv", 19200.0, None)
        assert dec.status == "FAIL", f"无有效包应诚实 FAIL: {dec.status} {dec.message}"
        assert dec.detail.get("n_frames") == 0
        assert "_ssdv_jpeg_path" not in dec.detail, "无包时不得伪造 JPEG"

    def test_ssdv_positive_chain_reassembles_jpeg(self, ssdv_byte_stream, tmp_out_dir):
        """正向链：合成 SSDV 字节流（纠错内注入误码）→ onboard ssdv → JPEG 可打开。"""
        from PIL import Image
        rng = np.random.default_rng(7)
        packets = ssdv_byte_stream["packets"]
        # 每个包注入 ≤10 个错字节（RS t=16 内可纠）
        corrupted = [_corrupt(p, rng.choice(range(1, 256), size=10, replace=False))
                     for p in packets]
        raw = b"".join(corrupted)
        path = os.path.join(tmp_out_dir, "ssdv_ok.bin")
        with open(path, "wb") as f:
            f.write(raw)

        dec = step_decode(path, "ssdv", 19200.0, None)
        assert dec.status == "PASS", f"正向链应 PASS: {dec.message}"
        jpg = dec.detail.get("_ssdv_jpeg_path")
        assert jpg and os.path.exists(jpg) and os.path.getsize(jpg) > 100
        ssdv = dec.detail["ssdv"]
        assert ssdv["missing_mcus"] == [], f"纠错内应无缺失 MCU: {ssdv['missing_mcus']}"
        with Image.open(jpg) as im:
            im.load()
            assert im.size == (ssdv_byte_stream["width"], ssdv_byte_stream["height"])

    def test_ssdv_over_capacity_reports_missing(self, ssdv_byte_stream, tmp_out_dir):
        """超 RS 纠错能力（>16 错字节）→ 该包被丢 → 诚实报缺失 MCU，不造假整图。"""
        from PIL import Image
        rng = np.random.default_rng(11)
        packets = ssdv_byte_stream["packets"]
        assert len(packets) >= 2, "需多包图才能观察到丢包后的缺失 MCU"
        # 把第一个包注入 18 个错字节（>t=16），其余干净
        bad0 = _corrupt(packets[0],
                        rng.choice(range(1, 256), size=18, replace=False))
        raw = bad0 + b"".join(packets[1:])
        path = os.path.join(tmp_out_dir, "ssdv_bad.bin")
        with open(path, "wb") as f:
            f.write(raw)

        dec = step_decode(path, "ssdv", 19200.0, None)
        # 仍应产出部分图（其余包可解），并诚实报告缺失 MCU
        ssdv = dec.detail.get("ssdv", {})
        assert ssdv.get("missing_mcus"), "超纠错能力应诚实报告缺失 MCU"
        jpg = dec.detail.get("_ssdv_jpeg_path")
        if jpg and os.path.exists(jpg):
            with Image.open(jpg) as im:
                im.load()  # 仍可被标准解码器打开（缺失 MCU 处花屏）


# ---------------------------------------------------------------------------
# 9. SSDV IQ 物理层一条命令（Phase43 块2）
#
# 云内用合成 BPSK IQ 跑通：SSDV 包字节 → BPSK 调制到复 IQ（带内中频偏移）
#   → onboard step_decode(ssdv_input="iq") 带内下变频 + BPSK 解调 → 字节流
#   → SsdvDecoder 自同步 → MCU 重组 JPEG。确定性、固定种子，无真机/无活动参数。
# 真机符号率/中频偏移由 --ssdv-symrate/--ssdv-tone-offset 传入（代码不硬编码）。
# ---------------------------------------------------------------------------
PHY_FS = 48_000.0
PHY_SYMR = 4_800.0
PHY_F_IF = 3_000.0


def _ssdv_packets_image(w=48, h=48):
    from mbdsdr_ai.ssdv_decoder import SsdvEncoder, TYPE_NORMAL
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[..., 0] = np.linspace(0, 255, w, dtype=np.uint8)
    arr[..., 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]
    arr[..., 2] = 128
    enc = SsdvEncoder(callsign="PHYTST", image_id=5, quality=4, mcu_mode=3,
                      pkt_type=TYPE_NORMAL)
    return enc.encode_image(arr), w, h


class TestSsdvIqPhysicalLayer:
    """合成 BPSK IQ → onboard ssdv iq 物理层 → JPEG 全链（确定性）。"""

    def test_ssdv_iq_chain_demods_to_jpeg(self, tmp_out_dir):
        from PIL import Image
        from mbdsdr_ai.ccsds_rx import bytes_to_bits_msb
        from mbdsdr_ai.ssdv_phy import bpsk_modulate_bits
        packets, w, h = _ssdv_packets_image()
        raw = b"".join(packets)
        bits = bytes_to_bits_msb(raw)
        iq = bpsk_modulate_bits(bits, PHY_FS, PHY_SYMR, f_if=PHY_F_IF)

        iq_path = os.path.join(tmp_out_dir, "ssdv_capture.sigmf-data")
        iq.tofile(iq_path)

        dec = step_decode(iq_path, "ssdv", PHY_FS, None,
                          ssdv_input="iq", ssdv_symrate=PHY_SYMR,
                          ssdv_tone_offset=PHY_F_IF)
        assert dec.status == "PASS", f"SSDV IQ 物理层应 PASS: {dec.status} {dec.message}"
        jpg = dec.detail.get("_ssdv_jpeg_path")
        assert jpg and os.path.exists(jpg) and os.path.getsize(jpg) > 100
        ssdv = dec.detail.get("ssdv", {})
        assert ssdv.get("missing_mcus") == [], f"干净信号不应缺 MCU: {ssdv.get('missing_mcus')}"
        assert ssdv.get("dialect") == "fsphil"
        with Image.open(jpg) as im:
            im.load()
            assert im.size == (w, h)

    def test_ssdv_iq_noise_honest_empty(self, tmp_out_dir):
        """纯噪声 IQ：物理层解不出有效包 → 诚实 FAIL，不伪造 JPEG。"""
        rng = np.random.default_rng(20261005)
        iq = (rng.standard_normal(int(PHY_FS)) + 1j * rng.standard_normal(int(PHY_FS)))
        iq = iq.astype(np.complex64)
        iq_path = os.path.join(tmp_out_dir, "ssdv_noise.sigmf-data")
        iq.tofile(iq_path)
        dec = step_decode(iq_path, "ssdv", PHY_FS, None,
                          ssdv_input="iq", ssdv_symrate=PHY_SYMR,
                          ssdv_tone_offset=PHY_F_IF)
        assert dec.status == "FAIL", f"纯噪声应诚实 FAIL: {dec.status} {dec.message}"
        assert "_ssdv_jpeg_path" not in dec.detail

    def test_ssdv_iq_requires_symrate(self, tmp_out_dir):
        """IQ 物理层未给符号率 → 诚实 FAIL（不猜速率、不硬编码）。"""
        iq_path = os.path.join(tmp_out_dir, "ssdv_x.sigmf-data")
        np.zeros(1024, dtype=np.complex64).tofile(iq_path)
        dec = step_decode(iq_path, "ssdv", PHY_FS, None,
                          ssdv_input="iq", ssdv_symrate=0.0)
        assert dec.status == "FAIL"
        assert "symrate" in dec.message.lower() or "符号率" in dec.message







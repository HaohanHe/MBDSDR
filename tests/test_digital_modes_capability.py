# SPDX-License-Identifier: MIT
"""tests/test_digital_modes_capability.py — 数字模式能力表钉扎（P5.2）。

守的是三件事：
  1. 「表 ↔ 注册处」双向齐全：能力表里登记的模式必须有 handler，
     有 handler 的模式必须登记（防以后加解码器漏登记）；
  2. 表里声明的东西是真的：依赖模块可导入、实现文件存在、证据等级合法；
  3. 未知模式 / 缺依赖 / 缺样本三种失败给三种不同的诚实错误码，
     不许混成一句「解码失败」。

来源口径：能力表字段与证据等级的取值来自 P1/P2 的解码器盘点结论
（AIS/NAVTEX 缺真录、LRPT/FT8/ADS-B 有真录），不是凭印象填的。
"""
import os
import sys
import wave

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import decode_router as dm  # noqa: E402
from mbdsdr_ai.decoders import decode_digital_mode  # noqa: E402

# 走别的入口的解码器（图像/RDS），不属于 decode_digital_mode 能力表
NON_DIGITAL_MODES = {"noaa_apt", "rds", "sstv"}
PKG_DIR = os.path.dirname(os.path.abspath(dm.__file__))


def _write_wav(path, samples, rate=12000):
    pcm = np.asarray(samples, dtype=np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return str(path)


def _write_cf32(path, count=4096):
    # 极弱复噪声，避免全零被当成有效能量
    rng = np.random.default_rng(7)
    iq = (rng.standard_normal(count) + 1j * rng.standard_normal(count))
    iq = (iq * 1e-6).astype(np.complex64)
    np.stack([iq.real, iq.imag], axis=1).astype(np.float32).tofile(str(path))
    return str(path)


# ---------------------------------------------------------------------------
# 表 ↔ 注册处一致性
# ---------------------------------------------------------------------------
class TestTableRegistration:
    def test_table_and_handlers_bijective(self):
        assert set(dm.DIGITAL_MODE_CAPABILITIES) == set(dm.DIGITAL_MODE_HANDLERS)

    def test_table_key_matches_entry_mode(self):
        for key, cap in dm.DIGITAL_MODE_CAPABILITIES.items():
            assert cap.mode == key

    def test_dependency_modules_importable(self):
        for key, cap in dm.DIGITAL_MODE_CAPABILITIES.items():
            if not cap.dependency:
                # 空依赖 = 通用入口未接线（仅 ft8），必须显式声明
                assert key in {"ft8"}, f"{key} 缺 dependency 但不在豁免名单"
                continue
            assert dm._find_spec(cap.dependency) is not None, (
                f"{key} 声明的依赖 {cap.dependency} 导不进来")

    def test_impl_files_exist(self):
        for key, cap in dm.DIGITAL_MODE_CAPABILITIES.items():
            path = os.path.join(PKG_DIR, cap.impl)
            assert os.path.exists(path), f"{key} 声明的实现文件不存在: {cap.impl}"

    def test_evidence_levels_are_known(self):
        for key, cap in dm.DIGITAL_MODE_CAPABILITIES.items():
            assert cap.evidence in dm.EVIDENCE_LEVELS, f"{key} 证据等级非法"
            assert cap.min_sample_condition.strip(), f"{key} 缺最小采样条件"

    def test_formats_and_output_kind_declared(self):
        for key, cap in dm.DIGITAL_MODE_CAPABILITIES.items():
            assert cap.input_formats, f"{key} 未声明输入格式"
            assert cap.output_kind, f"{key} 未声明输出类型"
            assert cap.sample_rate_rule, f"{key} 未声明采样率/带宽要求"

    def test_controller_decoders_all_registered(self):
        """controller 的 SUPPORTED_DECODERS 里凡是数字模式的都必须在表里。"""
        from mbdsdr_ai.core import controller
        missing = set(controller.SUPPORTED_DECODERS) - NON_DIGITAL_MODES \
            - set(dm.DIGITAL_MODE_CAPABILITIES)
        assert not missing, f"controller 已注册但能力表漏登记: {sorted(missing)}"

    def test_matrix_export_is_complete(self):
        rows = dm.capability_matrix()
        assert [r["mode"] for r in rows] == dm.supported_modes()
        assert all(r["handler_registered"] for r in rows)

    def test_empty_capable_modes_declare_nothing_detected(self):
        """能产出 0 条的模式必须登记空结果码（七态口径，声明=实现）。

        ft8 通用入口未接线、meteor 输出 symbols/bits，二者不走空结果分支。
        """
        for key in ("adsb", "ft4", "aprs", "pocsag", "acars", "wspr",
                    "ais", "navtex"):
            cap = dm.DIGITAL_MODE_CAPABILITIES[key]
            assert dm.ERR_NOTHING_DETECTED in cap.failure_codes, key


# ---------------------------------------------------------------------------
# 三类诚实错误码
# ---------------------------------------------------------------------------
class TestHonestErrorCodes:
    def test_unknown_mode_is_its_own_code(self, tmp_path):
        f = _write_wav(tmp_path / "x.wav", np.zeros(100))
        out = decode_digital_mode(f, "no_such_mode")
        assert out["error_code"] == dm.ERR_UNKNOWN_MODE
        assert out["ok"] is False and out["status"] == "error"
        assert out["messages"] == []

    def test_unknown_mode_lists_supported_modes(self, tmp_path):
        f = _write_wav(tmp_path / "x.wav", np.zeros(100))
        out = decode_digital_mode(f, "no_such_mode")
        assert out["supported_modes"] == dm.supported_modes()
        for mode in dm.supported_modes():
            assert mode in out["error"]

    def test_missing_dependency_is_its_own_code(self, tmp_path, monkeypatch):
        f = _write_cf32(tmp_path / "x.cf32")
        monkeypatch.setattr(dm, "_find_spec", lambda name: None)
        out = decode_digital_mode(f, "adsb")
        assert out["error_code"] == dm.ERR_MISSING_DEPENDENCY
        assert out["missing_dependency"] == "mbdsdr_ai.adsb"
        assert "mbdsdr_ai.adsb" in out["error"]
        assert out["evidence_level"] == dm.EVIDENCE_REAL

    def test_dependency_present_does_not_fire(self, tmp_path):
        f = _write_cf32(tmp_path / "x.cf32")
        out = decode_digital_mode(f, "ais", sample_rate=192000)
        assert out["error_code"] != dm.ERR_MISSING_DEPENDENCY

    def test_missing_sample_is_its_own_code(self, tmp_path):
        ghost = str(tmp_path / "not-here.cf32")
        out = decode_digital_mode(ghost, "ais")
        assert out["error_code"] == dm.ERR_MISSING_SAMPLE
        assert "缺样本" in out["error"]
        assert out["input_file"] == ghost

    def test_three_codes_are_distinct(self, tmp_path):
        assert {dm.ERR_UNKNOWN_MODE, dm.ERR_MISSING_DEPENDENCY,
                dm.ERR_MISSING_SAMPLE} == {
            "unknown_mode", "missing_dependency", "missing_sample"}

    def test_missing_sample_beats_format_error(self, tmp_path):
        """文件不存在时报缺样本，而不是先抱怨扩展名（先因后果要诚实）。"""
        out = decode_digital_mode(str(tmp_path / "ghost.bin"), "ais")
        assert out["error_code"] == dm.ERR_MISSING_SAMPLE

    def test_mode_name_is_case_insensitive(self, tmp_path):
        f = _write_cf32(tmp_path / "x.cf32")
        out = decode_digital_mode(f, "AIS", sample_rate=192000)
        assert out["mode"] == "ais"
        # 极弱噪声 = 无信号：0 条按空结果归类，不是 ok=True
        assert out["status"] == dm.STATUS_EMPTY
        assert out["error_code"] == dm.ERR_NOTHING_DETECTED


# ---------------------------------------------------------------------------
# 格式门（表驱动 vs handler 自校验）
# ---------------------------------------------------------------------------
class TestFormatGate:
    def test_table_gate_rejects_wrong_format(self, tmp_path):
        f = _write_wav(tmp_path / "ais.wav", np.zeros(480))
        out = decode_digital_mode(f, "ais")
        assert out["error_code"] == dm.ERR_UNSUPPORTED_FORMAT
        assert "cf32" in out["error"]

    def test_delegated_gate_keeps_narrowband_contract(self, tmp_path):
        f = _write_cf32(tmp_path / "ft4.cf32")
        out = decode_digital_mode(f, "ft4")
        assert out["status"] == "rejected"
        assert out["rejected"] is True
        assert out["reject_reason"] == "unsupported_format"
        assert out["error_code"] == dm.ERR_UNSUPPORTED_FORMAT
        assert "spectral_peak" not in out

    def test_unwired_mode_still_reports_not_implemented(self, tmp_path):
        """FT8 通用入口仍未接线：如实报 not_implemented，不装成解出来了。"""
        f = _write_wav(tmp_path / "ft8.wav", np.zeros(480))
        out = decode_digital_mode(f, "ft8")
        assert out["error_code"] == dm.ERR_NOT_IMPLEMENTED
        assert out["ok"] is False
        assert out["note"]

    def test_aprs_accepts_wav_and_cf32(self, tmp_path):
        """APRS 已接线：.wav（16-bit PCM 单声道）与 .cf32 都走真实流水线。"""
        fixture_dir = os.path.join(_ROOT, "tests", "fixtures", "decode_pipeline")
        wav = os.path.join(fixture_dir, "aprs_two_frames_48000.wav")
        res = decode_digital_mode(wav, "aprs",
                                  output_dir=str(tmp_path / "out"))
        assert res["ok"] is True and res["status"] == "decoded"
        assert res["num_frames"] == 2
        assert len(res["stages"]) == 6
        assert res["frames"][0]["source"] == "BI1AAA"
        assert res["frames"][0]["digipeaters"]

        fixture = os.path.join(fixture_dir, "aprs_two_frames.cf32")
        res2 = decode_digital_mode(fixture, "aprs", sample_rate=96000.0,
                                   output_dir=str(tmp_path / "out2"))
        assert res2["ok"] is True and res2["num_frames"] == 2
        assert [f["frame_hex"] for f in res["frames"]] == \
            [f["frame_hex"] for f in res2["frames"]], "两条入口必须逐字节一致"

    def test_aprs_rejects_other_containers(self, tmp_path):
        """真正不支持的容器仍要格式门报错，且给出允许格式。"""
        f = tmp_path / "aprs.mp3"
        f.write_bytes(b"\x00" * 64)
        out = decode_digital_mode(str(f), "aprs")
        assert out["error_code"] == dm.ERR_UNSUPPORTED_FORMAT
        assert ".wav" in out["error"] and "cf32" in out["error"]

    def test_aprs_rejects_unsupported_wav_encoding(self, tmp_path):
        """立体声 wav 不许静默当单声道：格式门 + 具体原因（声道数）。"""
        stereo = np.zeros(960, dtype=np.int16)
        stereo = np.repeat(stereo[:, None], 2, axis=1).reshape(-1)
        path = tmp_path / "aprs_stereo.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(stereo.tobytes())
        out = decode_digital_mode(str(path), "aprs")
        assert out["ok"] is False
        assert out["error_code"] == "unsupported_format"
        assert "声道数" in out["detail"]


# ---------------------------------------------------------------------------
# 每种模式冒烟：合法输入不抛异常，且结果带能力元数据
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", sorted(dm.DIGITAL_MODE_CAPABILITIES))
def test_each_mode_smoke(tmp_path, mode):
    cap = dm.DIGITAL_MODE_CAPABILITIES[mode]
    if ".wav" in cap.input_formats or "*" in cap.input_formats:
        path = _write_wav(tmp_path / f"{mode}.wav", np.zeros(4096))
    else:
        path = _write_cf32(tmp_path / f"{mode}.cf32")
    out = decode_digital_mode(
        path, mode, output_dir=str(tmp_path / "out"),
        sample_rate=1e6, center_frequency_hz=1500.0,
    )
    assert isinstance(out, dict)
    assert out["mode"] == mode
    assert out["evidence_level"] == cap.evidence
    assert out["min_sample_condition"] == cap.min_sample_condition
    assert out["output_kind"] == cap.output_kind
    assert "error_code" in out

"""IQ 调制一致性机器检查（ACARS 之后的全解码器审计）。

两条机器断言：

1. 静态一致性：能力表里每个模式声明「真实调制 + `.cf32` 路由解调入口」，
   映射表校验「调制 → 解调」必须匹配，且 handler 源码确实引用该入口；
   只有音频入口的模式不得声明 `iq_demod`。
2. 行为断言：同一份 IQ 调制不匹配时（AM IQ 喂 NFM 模式、FM IQ 喂 AM 模式），
   必须显式失败，绝不能 `ok=True` + 0 帧的静默成功。

映射依据（协议/实现）：
  am    → `_demod_am_to_audio`        ACARS VHF AM-MSK，信息在包络
  nfm   → `_demod_iq_to_audio` / `run_aprs_pipeline`
                                     APRS/POCSAG 是 NFM 上音频 FSK，鉴频取音频
  gmsk  → `ais_decode_iq`             AIS GMSK，差分相位解调
  ppm   → `decode_baseband_any_fs`    ADS-B PPM/OOK，包络脉冲
  qpsk  → `meteor_lrpt_demod`         Meteor LRPT 恒包络 QPSK
"""

from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai import decode_router as dr

# 解调入口 → 调制族
DEMOD_FAMILY = {
    "_demod_am_to_audio": "am",
    "_demod_iq_to_audio": "nfm",
    "run_aprs_pipeline": "nfm",     # 流水线内部是 NFM 鉴频
    "ais_decode_iq": "gmsk",
    "decode_baseband_any_fs": "ppm",
    "meteor_lrpt_demod": "qpsk",
}

KIND_FAMILY = {
    "am": "am",
    "nfm": "nfm",
    "gmsk": "gmsk",
    "ppm": "ppm",
    "qpsk": "qpsk",
    "ssb_audio": "audio",
}


def _iq_modes():
    return {m: c for m, c in dr.DIGITAL_MODE_CAPABILITIES.items()
            if ".cf32" in c.input_formats or ".cfile" in c.input_formats}


def test_iq_modes_declare_consistent_demod():
    for mode, cap in _iq_modes().items():
        assert cap.modulation_kind in KIND_FAMILY, mode
        assert cap.iq_demod in DEMOD_FAMILY, (mode, cap.iq_demod)
        assert DEMOD_FAMILY[cap.iq_demod] == KIND_FAMILY[cap.modulation_kind], (
            f"{mode}: 声明调制 {cap.modulation_kind} 却用 {cap.iq_demod}")


def test_audio_only_modes_do_not_claim_iq_demod():
    for mode, cap in dr.DIGITAL_MODE_CAPABILITIES.items():
        if mode in _iq_modes():
            continue
        assert cap.iq_demod == "", f"{mode} 只有音频入口却声明 iq_demod"
        assert cap.modulation_kind in KIND_FAMILY, mode


def test_route_source_uses_declared_demod():
    """handler 的运行时字节码必须真的引用声明的解调入口。

    旧版是 ``inspect.getsource`` 后查源码里有没有那串字符：注释里写一遍
    就能骗过，改个换行又会误红。改成查 code object 的真实全局引用
    （co_names）+ 内嵌字符串常量，只认运行时调用关系。
    """
    for mode, cap in _iq_modes().items():
        handler = dr.DIGITAL_MODE_HANDLERS[mode]
        refs = set(handler.__code__.co_names)
        refs.update(c for c in handler.__code__.co_consts
                    if isinstance(c, str))
        assert cap.iq_demod in refs, (
            f"{mode} 声明 {cap.iq_demod}，handler 未真实引用该解调入口")


# ---------------------------------------------------------------------------
# 行为：调制不匹配 → 显式失败，不许静默 0 帧
# ---------------------------------------------------------------------------
def _write_cf32(path, iq):
    inter = np.empty(len(iq) * 2, dtype=np.float32)
    inter[0::2] = np.asarray(iq).real.astype(np.float32)
    inter[1::2] = np.asarray(iq).imag.astype(np.float32)
    inter.tofile(str(path))
    return str(path)


def _acars_over_air():
    from mbdsdr_ai.acars_decoder import SOH, SYN, build_acars_frame

    body = build_acars_frame(mode="2", reg="N842UA", label="DF",
                             block_id="6", text="WEATHER 1500Z WIND 270/15")
    return bytes([SYN, SYN, SOH]) + body


def _msk_audio(fs):
    from mbdsdr_ai.acars_decoder import frame_bits_from_bytes, synthesize_msk_audio

    return synthesize_msk_audio(frame_bits_from_bytes(_acars_over_air()),
                                fs, 1200)


def _am_iq(audio, fs, carrier_hz=3000.0, depth=0.7):
    base = 1.0 + depth * np.asarray(audio) / np.max(np.abs(audio))
    t = np.arange(base.size) / float(fs)
    return base * np.exp(1j * 2 * np.pi * carrier_hz * t)


def _fm_iq(audio, fs, carrier_hz=3000.0):
    return np.exp(1j * 2 * np.pi * carrier_hz * np.cumsum(audio) / fs)


def test_am_iq_into_nfm_aprs_is_explicit_failure(tmp_path):
    fs = 48000.0
    cf = _write_cf32(tmp_path / "am_for_aprs.cf32",
                     _am_iq(_msk_audio(fs), fs))
    out = dr.run_digital_mode(cf, "aprs", sample_rate=fs)

    assert not (out["ok"] is True and out.get("num_frames", 0) == 0), out
    assert out["ok"] is False
    assert out["error_code"]


def test_fm_iq_into_am_acars_is_explicit_failure(tmp_path):
    fs = 48000.0
    cf = _write_cf32(tmp_path / "fm_for_acars.cf32",
                     _fm_iq(_msk_audio(fs), fs))
    out = dr.run_digital_mode(cf, "acars", sample_rate=fs)

    assert out["num_messages"] == 0
    assert out["ok"] is False
    assert out["error_code"] == dr.ERR_SIGNAL_PRESENT_NO_FRAME
    assert "0 帧" in out["error"]


def test_zero_iq_is_not_reported_as_signal_present(tmp_path):
    """无能量（全零）不是「有信号但解不出」，保持原有 0 帧语义。"""
    cf = _write_cf32(tmp_path / "zeros.cf32",
                     np.zeros(3000, dtype=np.complex128))
    out = dr.run_digital_mode(cf, "acars", sample_rate=12500.0)
    assert out["error_code"] != dr.ERR_SIGNAL_PRESENT_NO_FRAME

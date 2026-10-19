# SPDX-License-Identifier: MIT
"""
sdrpp_decoders.py 解码器封装层——确定性离线测试

被测对象是"薄壳"（真正算法复用本仓 lite 实现），因此测点集中在壳的契约：

  - SDRPPConstants：公开标准事实常量（RDS/APT/POCSAG 参数）；
  - RDS/APT/ADSB 三个 decode：正常路径透传底层 dict；
    底层抛异常时按各自契约返回错误结构，不把异常泄给调用方；
  - POCSAGDecoder.sync_search：滑动窗口汉明同步检测——
    精确命中、<=4bit 容差命中、5bit 拒绝、仅扫前 512 比特、位置为窗起始下标；
  - register_sdrpp_decoders：四个工具注册进 ToolRegistry
    （名称/分类/参数 schema/handler 可调用）；
  - _wrap_file_handler：缺文件参数 / 加载失败 / 正常 .npy 三条路径，
    并用 spy 钉死 file->decode 的数据与采样率透传。

运行：python3 -m pytest mbdsdr_ai/tests/test_sdrpp_decoders.py -v
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai import sdrpp_decoders as sd  # noqa: E402
from mbdsdr_ai.sdrpp_decoders import (  # noqa: E402
    ADSBDecoder,
    APTScanDecoder,
    POCSAGDecoder,
    RDSDecoder,
    SDRPPConstants,
    register_sdrpp_decoders,
)
from mbdsdr_ai.tool_registry import ToolRegistry  # noqa: E402


# ---------------------------------------------------------------- 常量

def test_constants_public_standards():
    """常量必须等于公开标准事实，任一漂移都会让解码偏离标准。"""
    # RDS（EN 300 401 子集）
    assert SDRPPConstants.RDS_BIT_RATE == 1187.5
    assert SDRPPConstants.RDS_SUBCARRIER_HZ == 57_000.0
    assert SDRPPConstants.RDS_INTERNAL_FS == 5_000.0
    assert SDRPPConstants.RDS_BANDPASS == (0.0, 2_375.0)
    # APT（NOAA POES）
    assert SDRPPConstants.APT_SUBCARRIER_HZ == 2_400.0
    assert SDRPPConstants.APT_VIDEO_RATE == 4_160.0
    assert SDRPPConstants.APT_LINE_SECONDS == 0.5
    # POCSAG
    assert SDRPPConstants.POCSAG_FSK_SHIFT_HZ == 4_500.0
    assert SDRPPConstants.POCSAG_FRAME_SYNC == 0x7CD215D8
    assert SDRPPConstants.POCSAG_BATCH_CODEWORDS == 16
    assert SDRPPConstants.POCSAG_DEFAULT_BAUD == 1200.0


def test_sync_word_is_32bit():
    """帧同步码字须恰为 32bit，滑动窗口才能按 32 位对齐检测。"""
    assert 0 < SDRPPConstants.POCSAG_FRAME_SYNC <= 0xFFFFFFFF


# ---------------------------------------------------------------- 三个 decode 封装

def test_rds_short_passthrough():
    """短输入：壳透传底层 rds_present=False 判定（不伪造解调结果）。"""
    r = RDSDecoder().decode(np.zeros(2000, dtype=np.float32), 250_000.0)
    assert r["rds_present"] is False
    assert "reason" in r  # 底层给出的原因原样透传


def test_rds_error_contract(monkeypatch):
    """底层抛异常 -> {"rds_present": False, "error": ...}，异常不泄出。"""
    from mbdsdr_ai import rds_lite

    def boom(mpx, sr, min_groups=2):
        raise ValueError("dsp exploded")

    monkeypatch.setattr(rds_lite, "decode_rds", boom)
    r = RDSDecoder().decode(np.zeros(100), 250_000.0)
    assert r == {"rds_present": False, "error": "dsp exploded"}


def test_apt_short_passthrough():
    """短于 min_lines 行：透传底层 too_short 判定。"""
    a = APTScanDecoder().decode(np.zeros(100, dtype=np.float32), 4_160.0)
    assert a["apt_present"] is False
    assert a["reason"] == "too_short"


def test_apt_error_contract(monkeypatch):
    """底层抛异常 -> {"ok": False, "error": ...}。"""
    from mbdsdr_ai import noaa_apt_lite

    def boom(audio, sr, polarity=1, min_lines=4):
        raise RuntimeError("apt exploded")

    monkeypatch.setattr(noaa_apt_lite, "decode_apt", boom)
    a = APTScanDecoder().decode(np.zeros(10), 4_160.0)
    assert a == {"ok": False, "error": "apt exploded"}


def test_adsb_noise_real_dsp_no_frames():
    """真实跑 DSP：纯噪声 IQ 不应解出任何帧（crc 全败），且不报 error。"""
    rng = np.random.default_rng(7)
    iq = (rng.standard_normal(20_000) + 1j * rng.standard_normal(20_000)).astype(np.complex64)
    d = ADSBDecoder().decode(iq, 2e6)
    assert "error" not in d
    assert d["frames"] == []           # 噪声无 CRC 有效帧
    assert d["aircraft"] == {}         # 也无飞机摘要
    assert "threshold_amp" in d        # 底层自适应门限字段透传


def test_adsb_error_contract(monkeypatch):
    """底层抛异常 -> {"frames": [], "aircraft": {}, "error": ...}。"""
    from mbdsdr_ai import adsb_lite

    def boom(iq, sr, threshold_sigma=4.0, return_invalid=False):
        raise RuntimeError("adsb exploded")

    monkeypatch.setattr(adsb_lite, "decode_adsb", boom)
    d = ADSBDecoder().decode(np.zeros(10, dtype=np.complex64), 2e6)
    assert d == {"frames": [], "aircraft": {}, "error": "adsb exploded"}


# ---------------------------------------------------------------- POCSAG 同步搜索

def _sync_bits() -> list:
    return [(SDRPPConstants.POCSAG_FRAME_SYNC >> (31 - i)) & 1 for i in range(32)]


def test_hamming_known_pairs():
    p = POCSAGDecoder()
    assert p._hamming(0, 0) == 0
    assert p._hamming(0xFFFFFFFF, 0) == 32
    assert p._hamming(0b1010, 0b0110) == 2
    assert p._hamming(SDRPPConstants.POCSAG_FRAME_SYNC,
                      SDRPPConstants.POCSAG_FRAME_SYNC) == 0


def test_sync_exact_position():
    """精确同步码字：报出的位置是码字起始下标。"""
    bits = np.zeros(200, dtype=np.uint8)
    bits[100:132] = _sync_bits()
    assert POCSAGDecoder().sync_search(bits) == [100]


def test_sync_tolerance_4bit():
    """<=4 bit 误差仍应命中（汉明容差 SYNC_DIST=4）。"""
    p = POCSAGDecoder()
    for nflip in (1, 2, 3, 4):
        bits = np.zeros(64, dtype=np.uint8)
        sb = _sync_bits()
        for k in range(nflip):
            sb[k] ^= 1
        bits[16:48] = sb
        assert 16 in p.sync_search(bits), f"{nflip} bit 误差应仍命中"


def test_sync_rejects_5bit():
    """5 bit 误差必须拒绝（恰在容差之外）。"""
    bits = np.zeros(64, dtype=np.uint8)
    sb = _sync_bits()
    for k in range(5):
        sb[k] ^= 1
    bits[16:48] = sb
    assert POCSAGDecoder().sync_search(bits) == []


def test_sync_window_only_first_512():
    """只扫前 512 比特：窗内命中、窗外即使完整也不扫。"""
    bits = np.zeros(1024, dtype=np.uint8)
    bits[480:512] = _sync_bits()   # 末比特恰在 index 511，窗内
    bits[600:632] = _sync_bits()   # 窗外
    assert POCSAGDecoder().sync_search(bits) == [480]


def test_sync_random_rare_false_positive():
    """512 随机比特误报概率 ~4e-3，固定种子下断言零误报（确定性）。"""
    rng = np.random.default_rng(42)
    bits = rng.integers(0, 2, 512).astype(np.uint8)
    assert POCSAGDecoder().sync_search(bits) == []


def test_sync_accepts_python_int_list():
    """输入为普通 int 列表也能工作（int() 收敛）。"""
    bits = [0] * 8 + _sync_bits() + [0] * 8
    assert POCSAGDecoder().sync_search(bits) == [8]


# ---------------------------------------------------------------- ToolRegistry 注册

def test_register_four_tools():
    reg = ToolRegistry()
    register_sdrpp_decoders(reg)
    names = {"sdrpp_decode_rds", "sdrpp_decode_apt",
             "sdrpp_decode_adsb", "sdrpp_pocsag_sync"}
    assert names <= set(reg.tools)
    for n in names:
        t = reg.tools[n]
        assert t["category"] == "sdrpp_decoder"
        assert t["available"] is True
        assert callable(t["handler"])
        fn = t["definition"]["function"]
        assert fn["name"] == n
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"


def test_registered_pocsag_handler_callable():
    """经注册表调 POCSAG handler：bits -> sync_positions。"""
    reg = ToolRegistry()
    register_sdrpp_decoders(reg)
    out = reg.tools["sdrpp_pocsag_sync"]["handler"]({"bits": _sync_bits()})
    assert out == {"sync_positions": [0]}


def test_file_handler_missing_file_arg():
    reg = ToolRegistry()
    register_sdrpp_decoders(reg)
    h = reg.tools["sdrpp_decode_adsb"]["handler"]
    assert h({}) == {"error": "missing input file"}
    assert h({"sample_rate": 2e6}) == {"error": "missing input file"}


def test_file_handler_load_failure(tmp_path):
    reg = ToolRegistry()
    register_sdrpp_decoders(reg)
    h = reg.tools["sdrpp_decode_adsb"]["handler"]
    missing = tmp_path / "nope.npy"
    out = h({"iq_file": str(missing), "sample_rate": 2e6})
    assert out["error"].startswith("load ")
    assert "failed" in out["error"]


def test_file_handler_npy_roundtrip(monkeypatch, tmp_path):
    """正常 .npy：数据与采样率原样透传给 decode（spy 钉死，不依赖 DSP 结果）。"""
    seen = {}

    def spy(self, iq, sample_rate, threshold_sigma=4.0):
        seen["shape"] = np.asarray(iq).shape
        seen["sr"] = sample_rate
        return {"spy": True}

    monkeypatch.setattr(sd.ADSBDecoder, "decode", spy)
    reg = ToolRegistry()
    register_sdrpp_decoders(reg)
    p = tmp_path / "iq.npy"
    iq = np.zeros(128, dtype=np.complex64)
    np.save(p, iq)
    out = reg.tools["sdrpp_decode_adsb"]["handler"](
        {"iq_file": str(p), "sample_rate": 2_000_000})
    assert out == {"spy": True}
    assert seen == {"shape": (128,), "sr": 2_000_000.0}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

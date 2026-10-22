# SPDX-License-Identifier: MIT
"""工具面对���链式自洽回归：编码类工具的输出必须能直接喂给解码类工具。

背景：AFSK 调制工具曾把音频**降采样到 2000 点**且只放在 content 里，
模型拿不到可回灌的完整载荷，"编码→解码"链路在工具面上根本走不通
（解码器收到的是被抽稀的音频）。这类缺陷在单工具冒烟里看不出来
（每个工具单独都"成功"），只有成对跑才暴露。

本文件对可成对编排的工具做端到端自洽断言。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def test_afsk_modulate_to_demodulate(reg):
    """AFSK：调制 -> 解调 必须解出原呼号/信息且 FCS 有效。"""
    m = reg.call("afsk_modulate", {"source": "BA1234", "destination": "BA5678",
                                    "info": "TEST", "sample_rate": 48000})
    assert m.success is True
    audio = m.data.get("audio")
    assert audio and len(audio) > 2000, "调制输出必须是完整音频（不得降采样）"
    d = reg.call("afsk_demodulate", {"audio": audio, "sample_rate": 48000})
    assert d.success is True
    frames = d.data.get("frames")
    assert frames, "解码器未解出任何帧（音频可能被降采样）"
    f = frames[0]
    assert f["source"] == "BA1234"
    assert f["destination"] == "BA5678"
    assert f["info"].strip() == "TEST"
    assert f["fcs_valid"] is True


def test_fsk_modulate_to_demodulate(reg):
    """FSK：调制 -> 解调 比特零误码。"""
    bits = [1, 0] * 40
    m = reg.call("fsk_modulate", {"bits": bits, "baud": 300, "sample_rate": 48000})
    assert m.success is True
    d = reg.call("fsk_demodulate", {"samples": m.data.get("audio"), "baud": 300,
                                    "sample_rate": 48000})
    assert d.success is True
    got = list(d.data.get("bits", []))
    n = min(len(got), len(bits))
    assert n > 0
    err = sum(1 for i in range(n) if int(got[i]) != int(bits[i]))
    assert err == 0, f"误码 {err}/{n}"


def test_freedv_modulate_to_demodulate(reg):
    """FreeDV：调制 -> 解调 比特零误码。"""
    bits = [1, 0] * 30
    m = reg.call("freedv_modulate", {"bits": bits, "mode": "700D"})
    assert m.success is True
    d = reg.call("freedv_demodulate", {"signal": m.data.get("signal"), "mode": "700D"})
    assert d.success is True
    got = list(d.data.get("bits", []))
    n = min(len(got), len(bits))
    assert n > 0
    err = sum(1 for i in range(n) if int(got[i]) != int(bits[i]))
    assert err == 0, f"误码 {err}/{n}"


def test_modulation_kernel_roundtrip(reg):
    """新增统一调制内核：modulate_symbols -> demodulate_symbols 零误码。"""
    bits = [1, 0, 0, 1] * 25
    m = reg.call("modulate_symbols", {"bits": bits, "scheme": "bpsk",
                                      "sps": 4, "shaping": "rrc"})
    assert m.success is True
    d = reg.call("demodulate_symbols", {"iq": m.data["waveform"], "scheme": "bpsk",
                                        "sps": 4, "shaping": "rrc"})
    assert d.success is True
    got = list(d.data.get("bits", []))
    n = min(len(got), len(bits))
    err = sum(1 for i in range(n) if int(got[i]) != int(bits[i]))
    assert err == 0, f"误码 {err}/{n}"


def test_m17_roundtrip_accepts_explicit_bits(reg):
    """M17 往返工具：bits 传列表（模型自然写法）必须可用，不得崩。"""
    bits = [1, 0, 1, 1] * 20
    r = reg.call("m17_4fsk_roundtrip", {"bits": bits})
    assert r.success is True
    assert r.data.get("roundtrip_ok") is True
    assert r.data.get("bit_source") == "given"


def test_urh_modulate_to_decode_chain(reg):
    """URH 链路补全：调制 -> 位判决 必须解回原比特，且调制载荷必须是完整波形。

    历史缺陷：urh_modulate 只返回第一个采样点的 I/Q（载荷截断），
    且 URHDecoder 未暴露为工具 -> 链路断在"调完拿不到波形"。
    """
    bits = [1, 0, 1, 1, 0, 0, 1, 0] * 10
    m = reg.call("urh_modulate", {"bits": bits, "modulation": "ASK",
                                  "samples_per_symbol": 100,
                                  "sample_rate": 48000})
    assert m.success is True
    iq = m.data.get("complex_iq")
    assert iq and len(iq) == 2 * 100 * len(bits), \
        f"调制载荷长度异常（应为完整交错 IQ）: {len(iq) if iq else 0}"
    d = reg.call("urh_decode_iq", {"iq": iq, "modulation": "ASK",
                                    "samples_per_symbol": 100})
    assert d.success is True
    got = list(d.data.get("bits", []))
    n = min(len(got), len(bits))
    assert n > 0
    err = sum(1 for i in range(n) if int(got[i]) != int(bits[i]))
    assert err == 0, f"误码 {err}/{n}"


def test_urh_bpsk_alias_works(reg):
    """模型说 BPSK 必须能用（此前报"暂不支持的调制: BPSK"）。"""
    bits = [1, 0, 1, 1, 0, 0, 1, 0] * 10
    m = reg.call("urh_modulate", {"bits": bits, "modulation": "BPSK",
                                  "samples_per_symbol": 100,
                                  "sample_rate": 48000})
    assert m.success is True, m.content[:120]
    d = reg.call("urh_decode_iq", {"iq": m.data.get("complex_iq"),
                                    "modulation": "BPSK",
                                    "samples_per_symbol": 100})
    assert d.success is True, d.content[:120]
    got = list(d.data.get("bits", []))
    # Costas 锁定期的前几个符号不可信，工具如实报告偏移，对齐后比较
    off = int(d.data.get("symbol_offset", 0))
    assert off >= 1, "PSK 应跳过 Costas 锁定期并报告偏移"
    exp = bits[off:]
    got = got[:len(exp)]
    n = min(len(got), len(exp))
    assert n > 0
    err = sum(1 for i in range(n) if int(got[i]) != int(exp[i]))
    assert err == 0, f"误码 {err}/{n}"

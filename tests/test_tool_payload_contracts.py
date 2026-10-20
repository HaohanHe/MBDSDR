# SPDX-License-Identifier: MIT
"""编码类工具的载荷契约回归：输出必须能被模型程序化取用（data 非空 + 稳定键）。

背景：本仓多次出现"工具调用成功但载荷只在 content 里"的缺陷——
AFSK 调制把音频降采样到 2000 点、URH 调制只给第一个采样点、
AX.25/APRS/KISS/Morse 编码只回中文文本。模型拿不到载荷就无法把
"编码→解码"串起来，而单工具冒烟完全看不出来（各自都"成功"）。

本文件把关键编码工具的载荷契约钉死。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


# (工具名, 参数, 必须存在的载荷键)
CASES = [
    ("afsk_modulate", {"source": "BA1234"}, ["audio"]),
    ("ax25_encode", {"source": "BA1234"}, ["hex"]),
    ("sdr_encode_ax25", {"source": "BA1234", "destination": "APRS",
                          "info": "T"}, ["hex"]),
    ("sdr_aprs_encode", {"source": "BI4MIB", "latitude": 45.0,
                          "longitude": 120.0}, ["hex"]),
    ("sdr_kiss_encode", {"ax25_hex": "82A0A4A64040E0848262646668"},
     ["hex", "kiss_hex"]),
    ("morse_encode", {"text": "TEST SOS"}, ["morse"]),
    ("urh_modulate", {"bits": [1, 0] * 20, "modulation": "ASK",
                      "samples_per_symbol": 100}, ["complex_iq"]),
    ("fsk_modulate", {"bits": [1, 0] * 20}, ["audio"]),
    ("gmsk_modulate", {"bits": [1, 0] * 20}, ["iq"]),
    ("freedv_modulate", {"bits": [1, 0] * 20}, ["signal"]),
    ("bell103_modulate_text", {"text": "TEST"}, ["audio"]),
    ("modulate_symbols", {"bits": [1, 0] * 20}, ["waveform"]),
    ("ft8_encode", {"text": "CQ TEST"}, ["tones"]),
    ("fst4_encode", {"text": "CQ TEST"}, ["tones"]),
    ("baudot_encode", {"text": "TEST"}, ["words"]),
    ("psk31_encode", {"text": "TEST"}, ["bits"]),
    ("codec2_encode", {"audio": [0.1, -0.1] * 500}, ["bits"]),
    ("m17_lsf_build", {"src": "BI4MIB"}, ["lsf_hex"]),
    ("pat_b2f_encode", {"fromcall": "BI4MIB", "tocall": "BA1234"},
     ["bytes_b64"]),
    ("xastir_encode_object", {"lat": 45.0, "lon": 120.0}, ["packet"]),
]


@pytest.mark.parametrize("tool,args,keys", CASES)
def test_encoder_exposes_machine_readable_payload(reg, tool, args, keys):
    """编码工具必须把载荷放进 data（模型可直接取用并喂给下一个工具）。"""
    r = reg.call(tool, args)
    assert r.success is True, f"{tool}: {r.content[:120]}"
    assert r.data, f"{tool}: data 为空（载荷只在 content 里，模型无法程序化取用）"
    for k in keys:
        assert k in r.data, f"{tool}: data 缺键 {k}（现有键 {list(r.data.keys())}）"
        assert r.data[k] not in (None, "", []), f"{tool}: 载荷 {k} 为空"


def test_afsk_payload_not_downsampled(reg):
    """AFSK 载荷必须是完整波形（防再次降采样到 ~2000 点）。"""
    r = reg.call("afsk_modulate", {"source": "BA1234", "destination": "BA5678",
                                    "info": "TEST", "sample_rate": 48000})
    assert r.success is True
    audio = r.data["audio"]
    assert len(audio) > 2000, f"音频被降采样（len={len(audio)}）"


def test_urh_payload_is_full_waveform(reg):
    """URH 载荷必须是完整交错 IQ（防再次只给第一个采样点）。"""
    bits = [1, 0] * 8                      # 16 bit
    sps = 8
    r = reg.call("urh_modulate", {"bits": bits, "modulation": "ASK",
                                  "samples_per_symbol": sps, "sample_rate": 48000})
    assert r.success is True
    iq = r.data["complex_iq"]
    # 交错 IQ：每采样 2 个实数，每符号 sps 个采样
    assert len(iq) == 2 * sps * len(bits), f"URH 载荷长度异常 {len(iq)}"


def test_registry_promotes_json_content_to_data(reg):
    """注册表层通用提升：content 是 JSON 且 data 为空时自动填 data。"""
    r = reg.call("ax25_encode", {"source": "BA1234"})
    assert r.success is True
    assert isinstance(r.data, dict) and r.data

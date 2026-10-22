# -*- coding: utf-8 -*-
"""ASRTU-1（阿斯图友谊号）遥测：IQ → CCSDS 帧 → 遥测文本 的端到端门禁。

材料《教程：使用 SDR 解码阿斯图友谊号卫星遥测 Ver 0.0.3》的收尾产出是
"命令提示符窗口将会有字符出现，提示 Received data successfully"——即
CCSDS 帧载荷里的**可打印遥测文本**。本文件造一条合成向量（ASCII 遥测
文本 → CCSDS 帧 → BPSK+RRC 成形 IQ），再走新链路解回文本，验证：
  · 文本逐字还原（教程最后一幕真的可复现）；
  · 二进制载荷时**不编造文本**（给可打印率与原始载荷头）；
  · 无信号时诚实失败并给诊断（点名帧长/体制这类最可能原因）。

红线：这是合成向量，不是真实 OTA 样本（仓内真实样本为零，测试文件头如实标注）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.ccsds_tools import (  # noqa: E402
    ccsds_encode_frame,
    ccsds_frames_to_text,
)
from mbdsdr_ai.modulation import modulate_bits  # noqa: E402

TELEMETRY = (b"ASRTU-1 TELEMETRY OK\r\n"
              b"BATT=12.42V\r\n"
              b"TEMP=21.5C\r\n"
              b"MODE=NOMINAL\r\n")


def _text_to_iq(payload: bytes, sps: int = 4, alpha: float = 0.35):
    """遥测文本 → CCSDS 帧 → BPSK+RRC IQ（并回传该帧的帧长参数）。"""
    enc = ccsds_encode_frame({
        "data": list(payload),
        "use_viterbi": True,
        "use_descramble": True,
        "conv_tail_bits": 6,
    })
    mod = modulate_bits([int(b) & 1 for b in enc["bits"]], scheme="bpsk",
                        sps=sps, shaping="rrc", alpha=alpha)
    iq = np.asarray(mod["waveform"], dtype=np.complex64)
    return iq, int(enc["frame_bits_after_asm"])


@pytest.fixture(scope="module")
def reg():
    return MBDSDRAgent().tool_registry


def test_tool_recovers_telemetry_text(reg):
    """IQ → 遥测文本：逐字还原（教程的最后一幕可复现）。"""
    iq, frame_bits = _text_to_iq(TELEMETRY)
    r = reg.call("astu_telemetry_decode", {
        "iq": [complex(float(v.real), float(v.imag)) for v in iq],
        "scheme": "bpsk", "sps": 4, "shaping": "rrc", "alpha": 0.35,
        "use_viterbi": True, "use_descramble": True,
        "frame_bits_after_asm": frame_bits,
    })
    assert r.success, (r.content or "")[:400]
    text = r.data["text"]
    assert "ASRTU-1 TELEMETRY OK" in text, text[:200]
    assert "BATT=12.42V" in text, text[:200]
    assert r.data["n_frames"] >= 1
    assert r.data["printable_ratio"] > 0.8, r.data


def test_text_extraction_keeps_printable_runs():
    """ccsds_frames_to_text：抽连续可打印段，短段不计入。"""
    frames = [b"\x00\x01" + b"HELLO" + b"\xff" + b"WORLD!" + b"\x00"]
    info = ccsds_frames_to_text(frames, min_run=4)
    assert "HELLO" in info["text"]
    assert "WORLD!" in info["text"]
    assert info["n_printable"] == 11      # "HELLO"(5) + "WORLD!"(6)
    assert 0.0 < info["printable_ratio"] < 1.0


def test_binary_payload_is_not_fabricated(reg):
    """二进制载荷：不得编造遥测文本，要给可打印率与原始载荷头。"""
    payload = bytes(range(256))
    iq, frame_bits = _text_to_iq(payload)
    r = reg.call("astu_telemetry_decode", {
        "iq": [complex(float(v.real), float(v.imag)) for v in iq],
        "scheme": "bpsk", "sps": 4, "shaping": "rrc", "alpha": 0.35,
        "use_viterbi": True, "use_descramble": True,
        "frame_bits_after_asm": frame_bits,
    })
    assert r.success, (r.content or "")[:300]
    data = r.data
    # 允许有零星可打印段，但不得凭空出现"像遥测"的整段文本
    assert data["printable_ratio"] < 0.9, data
    assert "payload_hex_head" in data and data["payload_hex_head"]
    if not data["text"]:
        assert data.get("note"), "无可打印文本时应给说明"


def test_no_signal_fails_honestly():
    """无信号：必须显式失败并给诊断（不得返回空文本当成功）。"""
    reg = MBDSDRAgent().tool_registry
    rng = np.random.default_rng(11)
    iq = (rng.standard_normal(4000) + 1j * rng.standard_normal(4000))
    r = reg.call("astu_telemetry_decode", {
        "iq": [complex(float(v.real), float(v.imag)) for v in iq],
        "scheme": "bpsk", "sps": 4,
    })
    assert r.success is False
    data = r.data or {}
    if data:
        assert data.get("error") or data.get("diagnostic"), data
        assert data.get("n_frames", 0) == 0
    else:
        # 统一错误信封（_err）只带 content：也要说清原因
        assert (r.content or "").strip(), "失败时必须给出原因"


def test_missing_iq_is_explicit_error():
    """既无 iq 也无 iq_path/iq_base64 → 明确报错。"""
    reg = MBDSDRAgent().tool_registry
    r = reg.call("astu_telemetry_decode", {})
    assert r.success is False
    assert "iq" in (r.content or "").lower()

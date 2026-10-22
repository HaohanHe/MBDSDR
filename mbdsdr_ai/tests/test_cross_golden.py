# SPDX-License-Identifier: MIT
"""跨语言对拍 golden（Python 侧钉扎）。

与 ``cpp/tests/test_cross_golden.cpp`` 钉扎**同一份** JSON
（``tests/golden/cross_decode_vectors.json``，Python 参考端生成自洽）：
任一侧实现漂移，对应 CI job 即红——这就是 FT8 / ADS-B 双实现的自动对拍。

诚实契约（两层）：
  * 全零码字（线性码数学必然）字段层必须拒绝（unpack "???"），
  * 杂散 LLR 解码层必须整体放弃（None / nullopt），绝不编造。
"""
import base64
import json
from pathlib import Path

import numpy as np
import pytest

from mbdsdr_ai import adsb
from mbdsdr_ai.ft8_codec import Ft8Codec, unpack77

GOLDEN = (Path(__file__).resolve().parents[2] / "tests" / "golden" /
          "cross_decode_vectors.json")


@pytest.fixture(scope="module")
def golden():
    assert GOLDEN.is_file(), f"golden 缺失: {GOLDEN}"
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def codec():
    return Ft8Codec()


def test_ft8_codeword_and_fields(golden, codec):
    """编码器钉扎：77-bit → CRC → LDPC 码字逐位等于 golden；解包字段同 golden。"""
    for case in golden["ft8"]["cases"]:
        msg91 = case["bits77"] + case["msg91"][77:]
        cw = codec.encode(msg91).tolist()
        assert cw == case["codeword174"], f'{case["name"]}: codeword drift'
        got = codec.decode(case["llr_clean"])
        assert got is not None, f'{case["name"]}: clean decode fail'
        assert got.tolist() == case["msg91"], f'{case["name"]}: msg91 drift'
        fields = unpack77(got[:77].tolist())
        assert fields is not None
        assert fields["from"] == case["from"], case["name"]
        assert fields["to"] == case["to"], case["name"]
        assert fields["exchange"] == case["exchange"], case["name"]
        assert bool(fields["report"]) == bool(case["report"]), case["name"]


def test_ft8_noisy_converges(golden, codec):
    """确定性轻噪声下 BP 必须收敛到同一消息（对拍：C++ 同一输入同一输出）。"""
    for case in golden["ft8"]["cases"]:
        got = codec.decode(case["llr_noisy"])
        assert got is not None, f'{case["name"]}: noisy decode gave up'
        assert got.tolist() == case["msg91"], f'{case["name"]}: noisy msg drift'


def test_ft8_honest_failure_layers(golden, codec):
    """全零码字字段层拒绝；杂散 LLR 解码层放弃——两层都不许编造。"""
    for case in golden["ft8"]["cases"]:
        got_zero = codec.decode(case["llr_zero_cw"])
        fields = None if got_zero is None else unpack77(got_zero[:77].tolist())
        assert fields is None, f'{case["name"]}: zero-codeword fields must be null'
        assert codec.decode(case["llr_garbage"]) is None, \
            f'{case["name"]}: garbage must not decode'


def test_adsb_crc24_head(golden):
    """CRC-24 头部钉扎值：两侧 crc24 实现必须同值。"""
    for case in golden["adsb"]["ident_cases"]:
        frame = bytes.fromhex(case["frame_hex"])
        head = frame[:-3]
        crc = adsb.mode_s_crc24(adsb.bytes_to_bits(head))
        assert crc == case["crc24_head"], f'{case["name"]}: crc24 drift'
        assert frame[-3:] == crc.to_bytes(3, "big"), f'{case["name"]}: frame crc'


def test_adsb_ident_iq_roundtrip(golden):
    """同一段 golden IQ（float32 b64）→ 解调出同一 ICAO/呼号（C++ 同输入同输出）。"""
    for case in golden["adsb"]["ident_cases"]:
        raw = np.frombuffer(base64.b64decode(case["iq_b64"]), dtype=np.float32)
        iq = raw.view(np.complex64).astype(np.complex128)
        dec = adsb.decode_baseband(iq, fs=case["sample_rate"])
        assert dec.get("found") and dec.get("crc_ok"), f'{case["name"]}: IQ decode'
        assert dec.get("icao", "").upper() == case["icao"], case["name"]
        assert dec.get("callsign", "").strip() == case["callsign"], case["name"]
        assert int(dec.get("df", -1)) == case["df"], case["name"]


def test_adsb_cpr_global_decode(golden):
    """CPR 偶/奇全局解码钉扎（含奇偶两槽输出口径）。"""
    for case in golden["adsb"]["cpr_cases"]:
        e_lat, e_lon = case["even_cprlat"], case["even_cprlon"]
        o_lat, o_lon = case["odd_cprlat"], case["odd_cprlon"]
        got_even = adsb.decode_cpr_airborne(e_lat, e_lon, o_lat, o_lon, 0)
        got_odd = adsb.decode_cpr_airborne(e_lat, e_lon, o_lat, o_lon, 1)
        exp_even, exp_odd = case["expect_even_slot"], case["expect_odd_slot"]
        assert abs(got_even[0] - exp_even[0]) < 1e-4, case["name"]
        assert abs(got_even[1] - exp_even[1]) < 1e-4, case["name"]
        assert abs(got_odd[0] - exp_odd[0]) < 1e-4, case["name"]
        assert abs(got_odd[1] - exp_odd[1]) < 1e-4, case["name"]

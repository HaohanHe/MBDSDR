# SPDX-License-Identifier: MIT
"""P0-5 TT&C 上行链路测试（TC 帧官方 golden 对拍 + 调制 + 发射人工门）。

golden 逐字节转录 CCSDS 230.1-G-3 Annex F Example 10，独立于本仓编码器；
主头、长度及 CRC 规则按 CCSDS 232.0-B-4 原文核对。任一侧漂移即红。

红线（本任务最硬的一条）：**无有效人工审批令牌的发射调用被明确拒绝**——
无 arm_manual → GATED；已武装无设备 → NEED_HW；绝不谎报发射成功。
"""
import json
from pathlib import Path

import numpy as np
import pytest

from mbdsdr_ai.tc_uplink import (
    pcm_psk_pm_modulate, tc_build_frame, tc_frame_parse, tc_uplink_transmit,
)

GOLDEN = (Path(__file__).resolve().parents[2] / "tests" / "golden" /
          "ccsds_tc_frame_golden.json")
OFFICIAL_ANNEX_F_EXAMPLE_10 = "001B000E06010203040506070814BB"
OFFICIAL_ANNEX_F_EXAMPLE_2 = "301B000900820000F6F0"


@pytest.fixture(scope="module")
def golden():
    assert GOLDEN.is_file(), f"golden 缺失: {GOLDEN}"
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


# ── 独立官方 golden 对拍 ───────────────────────────────────────
def test_golden_frame_parse_fields_exact(golden):
    """Annex F 帧 → 5 字节主头逐字段严格相等 + CRC OK + 数据回环。"""
    assert golden["frame_hex"] == OFFICIAL_ANNEX_F_EXAMPLE_10
    r = tc_frame_parse(bytes.fromhex(golden["frame_hex"]))
    assert r["crc_ok"]
    for k, v in golden["expected_fields"].items():
        assert r["fields"][k] == v, f"{k}: {r['fields'][k]} != {v}"
    assert r["data_hex"] == golden["input"]["data_hex"]
    assert "reserved" not in r["fields"]


def test_golden_encoder_regenerates_byte_exact(golden):
    """构建器对官方 Example 10 输入再生成，15 字节逐字节相等。"""
    r = tc_build_frame(bytes.fromhex(golden["input"]["data_hex"]),
                       **{k: golden["input"][k] for k in
                          ("spacecraft_id", "virtual_channel_id",
                           "bypass", "ctrl_cmd", "frame_seq")})
    assert r["frame_hex"] == OFFICIAL_ANNEX_F_EXAMPLE_10
    assert r["header_hex"] == "001B000E06"
    assert r["fecf_hex"] == "14BB"
    assert r["n_bytes"] == golden["n_bytes"] == 15


def test_official_type_bc_control_frame_byte_exact():
    """Annex F Example 2：Bypass/Control=11，Type-B 序列号为 0。"""
    r = tc_build_frame(b"\x82\x00\x00", spacecraft_id=0x1B,
                       bypass=1, ctrl_cmd=1, frame_seq=0)
    assert r["frame_hex"] == OFFICIAL_ANNEX_F_EXAMPLE_2
    parsed = tc_frame_parse(bytes.fromhex(OFFICIAL_ANNEX_F_EXAMPLE_2))
    assert parsed["crc_ok"]
    assert parsed["fields"]["bypass"] == 1
    assert parsed["fields"]["ctrl_cmd"] == 1
    assert parsed["fields"]["frame_seq"] == 0
    assert parsed["data_hex"] == "820000"


# ── 诚实失败路径与协议边界 ─────────────────────────────────────
def test_crc_corruption_flagged(golden):
    """CRC 区翻 1 bit → crc_ok=False + reason，不产出伪正常帧。"""
    raw = bytearray.fromhex(golden["frame_hex"])
    raw[-1] ^= 0x01
    r = tc_frame_parse(bytes(raw))
    assert not r["crc_ok"]
    assert "reason" in r


def test_length_field_mismatch_rejected(golden):
    """截断 1 字节 → 帧长字段与总长不自洽，拒绝解析。"""
    raw = bytes.fromhex(golden["frame_hex"])[:-1]
    with pytest.raises(ValueError, match="帧长字段|帧边界"):
        tc_frame_parse(raw)


def test_length_boundaries():
    """含 FECF 的最小 7 字节/最大 1024 字节帧均可正反解析。"""
    smallest = tc_build_frame(b"", spacecraft_id=0x1B)
    assert smallest["n_bytes"] == 7
    assert smallest["fields"]["frame_length_field"] == 6
    parsed = tc_frame_parse(bytes.fromhex(smallest["frame_hex"]))
    assert parsed["crc_ok"] and parsed["data"] == []

    largest = tc_build_frame(bytes(1017), spacecraft_id=0x3FF,
                             virtual_channel_id=0x3F, frame_seq=0xFF)
    assert largest["n_bytes"] == 1024
    assert largest["fields"]["frame_length_field"] == 1023
    parsed = tc_frame_parse(bytes.fromhex(largest["frame_hex"]))
    assert parsed["crc_ok"] and len(parsed["data"]) == 1017


def test_oversize_frame_and_length_overflow_rejected():
    with pytest.raises(ValueError, match="1024|1017"):
        tc_build_frame(bytes(1018))
    largest = tc_build_frame(bytes(1017))
    with pytest.raises(ValueError, match="1024"):
        tc_frame_parse(bytes.fromhex(largest["frame_hex"]) + b"\x00")


@pytest.mark.parametrize(("field", "value"), [
    ("spacecraft_id", -1), ("spacecraft_id", 1024),
    ("virtual_channel_id", -1), ("virtual_channel_id", 64),
    ("frame_seq", -1), ("frame_seq", 256),
    ("bypass", -1), ("bypass", 2),
    ("ctrl_cmd", -1), ("ctrl_cmd", 2),
])
def test_field_range_validation(field, value):
    """SCID/VCID/序列号及控制位越界均报错，不静默截断。"""
    with pytest.raises(ValueError, match=field):
        tc_build_frame(b"\x00", **{field: value})


@pytest.mark.parametrize("field", ["spacecraft_id", "virtual_channel_id",
                                   "frame_seq", "bypass", "ctrl_cmd"])
def test_non_integer_fields_rejected(field):
    with pytest.raises(ValueError, match=field):
        tc_build_frame(b"\x00", **{field: 1.5})


def test_reserved_control_combination_and_type_b_seq_rejected():
    with pytest.raises(ValueError, match="保留组合"):
        tc_build_frame(b"\x00", bypass=0, ctrl_cmd=1)
    with pytest.raises(ValueError, match="Type-B"):
        tc_build_frame(b"\x00", bypass=1, frame_seq=1)


@pytest.mark.parametrize("value", [-1, 256, 1.5, "1", True])
def test_non_octet_input_rejected_without_masking(value):
    with pytest.raises(ValueError, match="0..255"):
        tc_build_frame([0, value])
    with pytest.raises(ValueError, match="0..255"):
        tc_frame_parse([0, value])


def test_scalar_data_is_not_implicitly_expanded():
    with pytest.raises(ValueError, match="data"):
        tc_build_frame(8)


def test_tool_handlers_reject_non_octet_input_without_masking():
    from mbdsdr_ai.tool_registry import ToolRegistry
    registry = ToolRegistry()
    registry.register_builtin_tools()
    built = registry.tools["tc_build_frame"]["handler"]({"data": [0, 256]})
    assert not built.success and "0..255" in built.content
    parsed = registry.tools["tc_frame_parse"]["handler"](
        {"frame": [0, 0, 0, 0, 0, 256, 0]})
    assert not parsed.success and "0..255" in parsed.content


def test_frame_too_short_rejected():
    with pytest.raises(ValueError, match="不是完整 TC 帧"):
        tc_frame_parse(b"\x00" * 6)


# ── PCM/PSK/PM 调制 ──────────────────────────────────────────
def test_modulate_length_and_params():
    """调制输出长度 = n_bits × fs/bitrate；参数回执诚实复算。"""
    bits = [0, 1, 1, 0, 1, 0, 0, 1] * 4
    r = pcm_psk_pm_modulate(bits, sample_rate_hz=48000.0,
                            bit_rate_hz=1200.0, subcarrier_hz=2400.0)
    assert r["n_samples"] == len(bits) * 40
    assert r["params"]["sps"] == pytest.approx(40.0)
    assert len(r["iq"]) == r["n_samples"]
    # 恒模（PM）性质：|iq| ≈ 1
    assert float(np.mean(np.abs(r["iq"]))) == pytest.approx(1.0, abs=1e-3)


def test_modulate_nrz_variants_differ():
    """NRZ-L / M / S 编码确实产生不同波形（同输入比特）。"""
    bits = [1, 0, 0, 1, 1, 0, 1, 1] * 8
    waves = {}
    for nrz in ("nrz_l", "nrz_m", "nrz_s"):
        r = pcm_psk_pm_modulate(bits, sample_rate_hz=48000.0,
                                bit_rate_hz=1200.0, subcarrier_hz=2400.0,
                                nrz=nrz)
        waves[nrz] = r["iq"]
    assert not np.array_equal(waves["nrz_l"], waves["nrz_m"])
    assert not np.array_equal(waves["nrz_l"], waves["nrz_s"])


def test_modulate_bad_params_rejected():
    """副载波超奈奎斯特 / sps<4 / 空比特 → 报错。"""
    with pytest.raises(ValueError, match="奈奎斯特"):
        pcm_psk_pm_modulate([1, 0], sample_rate_hz=48000.0,
                            bit_rate_hz=1200.0, subcarrier_hz=30000.0)
    with pytest.raises(ValueError, match="每码元采样"):
        pcm_psk_pm_modulate([1, 0], sample_rate_hz=2000.0,
                            bit_rate_hz=1200.0, subcarrier_hz=800.0)
    with pytest.raises(ValueError, match="为空"):
        pcm_psk_pm_modulate([], sample_rate_hz=48000.0, bit_rate_hz=1200.0,
                            subcarrier_hz=2400.0)


# ── 发射人工门（红线）────────────────────────────────────────
def test_tx_rejected_without_arm():
    """无 arm_manual → GATED 拒绝，success 假、状态明确。"""
    r = tc_uplink_transmit(arm_manual=False, iq=np.zeros(16),
                           freq_hz=435.4e6)
    assert r["status"] == "GATED"


def test_tx_rejected_armed_no_freq():
    """已获外部确认但频点缺失 → PARAM_ERROR（安全关键参数不猜）。"""
    from mbdsdr_ai.tx_guard import get_tx_gate
    tok = get_tx_gate().arm(ttl_s=10)
    r = tc_uplink_transmit(iq=np.zeros(16), tx_token=tok)
    assert r["status"] == "PARAM_ERROR"


def test_tx_armed_no_device_need_hw():
    """已获外部确认 + 参数齐 + 无设备 → NEED_HW 诚实空态（绝不谎报发射）。"""
    from mbdsdr_ai.tx_guard import get_tx_gate
    tok = get_tx_gate().arm(ttl_s=10)
    r = tc_uplink_transmit(iq=np.zeros(16, dtype=complex),
                           freq_hz=435.4e6, tx_token=tok)
    assert r["status"] == "NEED_HW"
    assert r["armed"] is True


def test_tx_schema_requires_external_token():
    """S13 契约钉扎：模型可传参数里不再有 arm_manual；改为一次性 confirm_token。"""
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    schema = r.tools["tc_uplink_transmit"]["definition"]["function"]["parameters"]
    assert "arm_manual" not in schema["properties"]
    assert "confirm_token" in schema["properties"]
    assert "arm_manual" not in schema.get("required", [])


# ── 工具注册面 ────────────────────────────────────────────────
def test_tools_registered():
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    names = {t["name"] for t in r.list_tools()}
    for want in ("tc_build_frame", "tc_frame_parse",
                 "pcm_psk_pm_modulate", "tc_uplink_transmit"):
        assert want in names, f"工具 {want} 未注册"


def test_tool_handlers_end_to_end(golden):
    """handler 级：构建 → 解析 → 发射门拒绝，全链走通。"""
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    b = r.tools["tc_build_frame"]["handler"](
        {"data": golden["input"]["data_hex"],
         "spacecraft_id": golden["input"]["spacecraft_id"],
         "virtual_channel_id": golden["input"]["virtual_channel_id"],
         "ctrl_cmd": golden["input"]["ctrl_cmd"],
         "frame_seq": golden["input"]["frame_seq"]})
    assert b.success
    assert b.data["frame_hex"] == golden["frame_hex"]
    p = r.tools["tc_frame_parse"]["handler"]({"frame": b.data["frame_hex"]})
    assert p.success and p.data["crc_ok"]
    tx = r.tools["tc_uplink_transmit"]["handler"](
        {"arm_manual": False, "freq_hz": 435.4e6})
    assert not tx.success and tx.data["status"] == "GATED"

# SPDX-License-Identifier: MIT
"""P0-5 TT&C 上行链路测试（TC 帧 golden 对拍 + 调制 + 发射人工门）。

golden 对拍：``tests/golden/ccsds_tc_frame_golden.json`` 由
``mbdsdr_ai.tc_uplink.tc_build_frame`` 生成（编码侧单源）。解析侧逐字段
+ CRC 断言钉扎；任一侧漂移即红。

红线（本任务最硬的一条）：**无有效人工审批令牌的发射调用被明确拒绝**——
无 arm_manual → GATED；已武装无设备 → NEED_HW；绝不谎报发射成功。
"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from mbdsdr_ai.tc_uplink import (
    pcm_psk_pm_modulate, tc_build_frame, tc_frame_parse, tc_uplink_transmit,
)

GOLDEN = (Path(__file__).resolve().parents[2] / "tests" / "golden" /
          "ccsds_tc_frame_golden.json")


@pytest.fixture(scope="module")
def golden():
    assert GOLDEN.is_file(), f"golden 缺失: {GOLDEN}"
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


# ── golden 对拍 ────────────────────────────────────────────────
def test_golden_frame_parse_fields_exact(golden):
    """golden 帧 → 逐字段严格相等 + CRC OK + 数据回环。"""
    r = tc_frame_parse(bytes.fromhex(golden["frame_hex"]))
    assert r["crc_ok"]
    for k, v in golden["expected_fields"].items():
        assert r["fields"][k] == v, f"{k}: {r['fields'][k]} != {v}"
    assert r["data_hex"] == golden["input"]["data_hex"]


def test_golden_encoder_regenerates_byte_exact(golden):
    """构建器对 golden 输入再生成，帧字节逐字节相等。"""
    r = tc_build_frame(bytes.fromhex(golden["input"]["data_hex"]),
                       **{k: golden["input"][k] for k in
                          ("spacecraft_id", "virtual_channel_id",
                           "bypass", "ctrl_cmd", "frame_seq")})
    assert r["frame_hex"] == golden["frame_hex"]
    assert r["n_bytes"] == golden["n_bytes"]


# ── 诚实失败路径 ──────────────────────────────────────────────
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


def test_field_range_validation():
    """字段越界报错，不静默截断。"""
    with pytest.raises(ValueError, match="spacecraft_id"):
        tc_build_frame(b"\x00", spacecraft_id=1024)
    with pytest.raises(ValueError, match="virtual_channel_id"):
        tc_build_frame(b"\x00", virtual_channel_id=64)
    with pytest.raises(ValueError, match="frame_seq"):
        tc_build_frame(b"\x00", frame_seq=256)


def test_empty_data_rejected():
    with pytest.raises(ValueError):
        tc_frame_parse(b"\x00" * 7)  # 太短


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
    """已武装但频点缺失 → PARAM_ERROR（安全关键参数不猜）。"""
    r = tc_uplink_transmit(arm_manual=True, iq=np.zeros(16))
    assert r["status"] == "PARAM_ERROR"


def test_tx_armed_no_device_need_hw():
    """已武装 + 参数齐 + 无设备 → NEED_HW 诚实空态（绝不谎报发射）。"""
    r = tc_uplink_transmit(arm_manual=True, iq=np.zeros(16, dtype=complex),
                           freq_hz=435.4e6)
    assert r["status"] == "NEED_HW"
    assert r["armed"] is True


def test_tx_schema_requires_arm_manual():
    """工具 schema 必须显式声明 arm_manual（门禁契约钉扎）。"""
    from mbdsdr_ai.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.register_builtin_tools()
    schema = r.tools["tc_uplink_transmit"]["definition"]["function"]["parameters"]
    assert "arm_manual" in schema["properties"]
    assert "arm_manual" in schema["required"]


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

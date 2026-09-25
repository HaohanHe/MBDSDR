#!/usr/bin/env python3
"""
tests/ham_modes_roundtrip.py
=============================
MBDSDR 5 个业余无线电模式适配器的往返验证。

覆盖：
  1. pat     —— Winlink B2F 消息编解码往返 + MID 列表
  2. ARDOP   —— OFDM 帧合成 + 帧同步字 0x1A 0x59 检测
  3. JS8Call —— 77-bit 消息编码往返（呼号/网格）
  4. Xastir  —— APRS 对象帧编解码往返 + 地理围栏
  5. minimodem —— Bell103 300baud FSK 调制/解调往返（不破坏现有接口）

运行：python3 tests/ham_modes_roundtrip.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from mbdsdr_ai.pat_adapter import (
    B2FMessage, B2FAttachment, B2FListEntry,
    encode_mid_list, decode_mid_list, list_transport_methods,
    register_pat_tools,
)
from mbdsdr_ai.ardop_adapter import (
    build_frame, build_leader, build_sync_word, detect_sync_word,
    QAM4, QAM16, QAM64, modulate_qam, demodulate_qam,
    ARDOPSession, ARDOP_SYNC_WORD, ARDOP_SAMPLE_RATE,
    register_ardop_tools,
)
from mbdsdr_ai.js8call_adapter import (
    JS8Message, pack_message, unpack_message,
    pack_callsign, unpack_callsign, pack_grid, unpack_grid,
    message_to_tones, list_submodes, JS8_PAYLOAD_BITS,
    register_js8call_tools,
)
from mbdsdr_ai.xastir_adapter import (
    APRSObject, APRSItem, decode_object_or_item,
    encode_latitude, decode_latitude, encode_longitude, decode_longitude,
    GeoFence, haversine_m, register_xastir_tools,
)
from mbdsdr_ai.minimodem_adapter import (
    FSKModem, ASCIIFrame, BaudotCodec,
    bell103_300baud_modem, bell103_modulate_text, bell103_demodulate_text,
    BELL103_MARK_FREQ, BELL103_SPACE_FREQ, BELL103_BAUD,
    register_minimodem_tools,
)


# ---------------------------------------------------------------------
# 简易 registry 桩（验证每个 register 函数至少注册 2 个工具）
# ---------------------------------------------------------------------
class _FakeRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, name, description, parameters, handler, category="general"):
        self.tools[name] = {"handler": handler, "category": category}


def test_registration_counts():
    for reg_fn in (register_pat_tools, register_ardop_tools,
                   register_js8call_tools, register_xastir_tools,
                   register_minimodem_tools):
        reg = _FakeRegistry()
        reg_fn(reg)
        assert len(reg.tools) >= 2, f"{reg_fn.__name__} 注册工具数 < 2"
    print("[OK] 5 个适配器均注册 >=2 个工具")


# ---------------------------------------------------------------------
# 1. Winlink B2F 消息往返
# ---------------------------------------------------------------------
def test_pat_b2f_roundtrip():
    att = B2FAttachment(filename="note.txt", data=b"hello winlink")
    msg = B2FMessage(
        fromcall="BI4MIB", tocall="SP1ABC",
        subject="Test via B2F",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        body="Hello from MBDSDR",
        attachments=[att],
        mid=12345678,
    )
    blob = msg.encode()
    back = B2FMessage.decode(blob)
    assert back.fromcall == "BI4MIB"
    assert back.tocall == "SP1ABC"
    assert back.subject == "Test via B2F"
    assert back.timestamp == datetime(2024, 1, 1, 12, 0, 0)
    assert "MBDSDR" in back.body
    assert len(back.attachments) == 1
    assert back.attachments[0].filename == "note.txt"
    assert back.attachments[0].data == b"hello winlink"

    # MID 列表往返
    entries = [
        B2FListEntry(mid=111, size=234, flags="R", subject="hello"),
        B2FListEntry(mid=222, size=567, flags="C", subject="second"),
    ]
    blob2 = encode_mid_list(entries)
    back2 = decode_mid_list(blob2)
    assert [e.mid for e in back2] == [111, 222]
    assert back2[1].flags == "C"

    # 传输模式枚举
    methods = list_transport_methods()
    schemes = {m["scheme"] for m in methods}
    assert {"ardop", "pactor", "ax25", "telnet"} <= schemes
    print(f"[OK] Winlink B2F 往返: 消息+附件+MID列表, {len(methods)} 传输模式")


# ---------------------------------------------------------------------
# 2. ARDOP 帧同步字检测 + QAM 星座
# ---------------------------------------------------------------------
def test_ardop_sync_and_constellation():
    # 同步字常量
    assert ARDOP_SYNC_WORD == bytes([0x1A, 0x59])

    # QAM 星座往返：随机符号 -> 调制 -> 最近点解调
    rng = np.random.default_rng(1)
    for order in (4, 16, 64):
        sym = rng.integers(0, order, size=200)
        tx = modulate_qam(sym, order)
        rx = demodulate_qam(tx, order)
        assert np.array_equal(rx, sym), f"{order}-QAM 星座往返失败"

    # 合成完整帧并检测同步字
    bits = rng.integers(0, 2, size=256)
    frame = build_frame(bits, order=16, n_carriers=10)
    assert len(frame) > 0
    off = detect_sync_word(frame)
    assert off is not None, "未检测到同步字"
    # 同步字应紧跟在 leader 之后
    leader_len = 10 * 240  # 10 symbols * 240 sps
    assert abs(off - leader_len) <= 240, f"同步位置 {off} 偏离 leader 末尾 {leader_len} 过远"

    # 状态机
    s = ARDOPSession()
    assert s.listen()
    assert s.connect_request("BI4MIB", "500")
    assert s.connect_ack()
    assert s.send_data()
    assert s.ack()
    assert s.disconnect()
    print(f"[OK] ARDOP: QAM(4/16/64)星座往返, 同步字@{off}样本, 状态机闭环")


# ---------------------------------------------------------------------
# 3. JS8 消息编码往返
# ---------------------------------------------------------------------
def test_js8_message_roundtrip():
    # 呼号/网格单独往返（学习模型固定布局: [alnum][alnum][digit][L][L][L]）
    for call in ["BI4MIB", "SP1ABC", "DL1ABC", "EA5XYZ"]:
        assert unpack_callsign(pack_callsign(call)) == call.ljust(6).strip()
    for grid in ["OL95", "JO22", "CN86"]:
        assert unpack_grid(pack_grid(grid)) == grid

    # 完整 77-bit 消息往返
    msg = JS8Message(type=1, de="BI4MIB", to="SP1ABC", grid="OL95")
    v = pack_message(msg)
    assert 0 <= v < (1 << JS8_PAYLOAD_BITS)
    back = unpack_message(v)
    assert back.de == "BI4MIB"
    assert back.to == "SP1ABC"
    assert back.grid == "OL95"
    assert back.type == 1

    # CQ 消息
    cq = JS8Message(type=0, de="BI4MIB", to="CQ0AAA", grid="OL95")
    v2 = pack_message(cq)
    b2 = unpack_message(v2)
    assert b2.type == 0 and b2.de == "BI4MIB" and b2.to == "CQ0AAA"

    # 8-FSK 符号序列
    tones = message_to_tones(v)
    assert all(0 <= t < 8 for t in tones)

    subs = list_submodes()
    assert any(s["name"] == "NORMAL" for s in subs)
    print(f"[OK] JS8: 77bit 消息往返 ({len(tones)} 8-FSK 符号), {len(subs)} 子模式")


# ---------------------------------------------------------------------
# 4. APRS 对象帧编解码往返 + 地理围栏
# ---------------------------------------------------------------------
def test_xastir_aprs_roundtrip():
    # 位置编解码
    for la, lo in [(39.9042, 116.4074), (-33.8688, 151.2093), (0.0, 0.0)]:
        assert abs(decode_latitude(encode_latitude(la)) - la) < 0.001
        assert abs(decode_longitude(encode_longitude(lo)) - lo) < 0.001

    # 对象帧往返
    obj = APRSObject(name="BI4MIB", lat=39.9042, lon=116.4074,
                     live=True, symbol_table="/", symbol_code=">",
                     comment="testing")
    pkt = obj.encode()
    assert pkt[:1] == b";"
    assert pkt[10:11] == b"*"  # live flag
    res = decode_object_or_item(pkt)
    assert res["kind"] == "object"
    assert res["name"] == "BI4MIB"
    assert abs(res["lat"] - 39.9042) < 0.001
    assert abs(res["lon"] - 116.4074) < 0.001
    assert res["live"] is True
    assert res["comment"] == "testing"

    # 物品帧
    item = APRSItem(name="APT", lat=40.0, lon=116.0, comment="hi")
    pkt2 = item.encode()
    res2 = decode_object_or_item(pkt2)
    assert res2["kind"] == "item"
    assert res2["name"] == "APT"

    # 地理围栏：圆心北京 39.9,116.4 r=5km
    fence = GeoFence("beijing", 39.9042, 116.4074, 5000.0)
    inside = fence.contains(39.90, 116.41)     # ~1km 内
    outside = fence.contains(51.50, -0.12)    # 伦敦
    assert inside is True and outside is False
    d = haversine_m(39.9042, 116.4074, 39.9042, 116.4074)
    assert d < 1.0
    print(f"[OK] Xastir: 对象/物品帧往返, 地理围栏内外判定正确")


# ---------------------------------------------------------------------
# 5. minimodem Bell103 300baud 往返（且现有接口不受影响）
# ---------------------------------------------------------------------
def test_bell103_roundtrip():
    # 常量正确
    assert BELL103_BAUD == 300.0
    assert BELL103_MARK_FREQ == 1270.0
    assert BELL103_SPACE_FREQ == 1070.0

    # 专用 modem 频率
    m = bell103_300baud_modem()
    assert m.f_mark == 1270.0
    assert m.f_space == 1070.0

    # 端到端文本往返（加一点 SNR）
    rng = np.random.default_rng(3)
    text = "HELLO BI4MIB 123"
    audio = np.asarray(bell103_modulate_text(text), dtype=np.float64)
    sig_p = np.mean(audio ** 2)
    noise_p = sig_p / (10 ** (15.0 / 10.0))
    noisy = audio + rng.normal(0, np.sqrt(noise_p), size=len(audio))
    res = bell103_demodulate_text(noisy)
    assert res["text"].startswith("HELLO BI4MIB"), f"解调文本: {res['text']!r}"
    assert res["mark_freq"] == 1270.0

    # 现有 Bell202 接口仍可用（回归保护）
    m202 = FSKModem(baud=1200)
    assert m202.f_mark == 1200.0 and m202.f_space == 2200.0
    fr = ASCIIFrame()
    bits = fr.encode_bytes(b"OK")
    a202 = m202.modulate_bits(bits)
    rxb, _ = m202.demodulate_bits(a202, n_bits=len(bits))
    assert fr.decode_bits(rxb) == b"OK"

    # Baudot 现有接口仍可用
    assert BaudotCodec().decode_words(BaudotCodec().encode_string("HELLO")) == "HELLO"
    print(f"[OK] Bell103 300baud 文本往返: '{res['text'].strip()}' (conf={res['confidence']:.2f}); "
          f"Bell202/Baudot 现有接口无回归")


# ---------------------------------------------------------------------
if __name__ == "__main__":
    test_registration_counts()
    test_pat_b2f_roundtrip()
    test_ardop_sync_and_constellation()
    test_js8_message_roundtrip()
    test_xastir_aprs_roundtrip()
    test_bell103_roundtrip()
    print("\n=== 全部 5 个业余无线电模式往返测试通过 ===")

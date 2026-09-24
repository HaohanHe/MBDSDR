#!/usr/bin/env python3
"""dump1090 真实 Mode-S/ADS-B 解码往返验证（不能造假）。

所有协议常量均逐条对照 dump1090 源码实现（见 mbdsdr_ai/adsb.py 内
「来源: dump1090 <file>:<line>」标注）。本测试证明：
  - CRC-24(多项式0xFFF409) 对真实公开 DF17 测试帧余数为 0；
  - TC1-4 呼号编解码往返一致；
  - TC19 空中速度解出的地速/航向与手算一致；
  - CPR 全局编码→解码在多个纬度还原经纬度（量化精度内）；
  - 合成 preamble+数据能被前导检测找到并解出 CRC 有效帧。

运行：
    cd <repo_root>
    python3 tests/adsb_real_roundtrip.py
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.adsb import (  # noqa: E402
    ADSBDecoder,
    build_identification_frame,
    build_long_frame,
    decode_cpr_airborne,
    decode_frame,
    decode_velocity_me,
    encode_cpr_airborne,
    modulate_baseband,
    mode_s_crc24,
    bytes_to_bits,
    bits_to_bytes,
    register_adsb_tools,
    cpr_nl,
    CRC_POLY,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    print("== dump1090 真实 ADS-B 解码往返验证 ==")

    # ------------------------------------------------------------------ #
    # 1. CRC-24：真实公开 DF17 帧（dump1090/pyModeS 测试语料）
    # ------------------------------------------------------------------ #
    print("\n[1] CRC-24 多项式 0xFFF409 校验")
    check("CRC 多项式常量", CRC_POLY == 0xFFF409, f"(got {CRC_POLY:#08x})")
    KNOWN = "8D40621D58C382D690C8AC2863A7"
    raw = bytes.fromhex(KNOWN)
    syndrome = mode_s_crc24(bytes_to_bits(raw))
    check("已知 DF17 帧整帧 syndrome==0", syndrome == 0,
          f"(syndrome={syndrome:#08x})")
    fr = decode_frame(raw)
    check("解码出 DF=17", fr.df == 17, f"(DF={fr.df})")
    check("ICAO 明文 AA 域 = 40621D", fr.icao_hex == "40621D",
          f"(ICAO={fr.icao_hex})")
    check("TC=11 空中位置", fr.tc == 11, f"(TC={fr.tc})")
    check("高度 = 38000 ft", fr.altitude_ft == 38000,
          f"(alt={fr.altitude_ft} ft)")
    check("偶 CPR 帧(cpr_odd=False)", fr.cpr.get("cpr_odd") is False)

    # 改一个比特 CRC 必须失败（证明校验真的在工作，不是恒真）
    bad = bytearray(raw)
    bad[-1] ^= 0x01
    check("翻转 1bit 后 CRC 失败",
          mode_s_crc24(bytes_to_bits(bytes(bad))) != 0)

    # ------------------------------------------------------------------ #
    # 2. TC1-4 呼号编解码往返
    # ------------------------------------------------------------------ #
    print("\n[2] 航空器识别呼号（TC1-4）")
    for cs in ["BAW123", "UAL2167", "CES582", "B4MIB"]:
        frame = build_identification_frame("4CA8EE", cs)
        d = decode_frame(frame)
        check(f"呼号 '{cs}' CRC 通过", d.crc_ok)
        check(f"呼号 '{cs}' 还原", d.callsign == cs, f"(got {d.callsign!r})")

    # ------------------------------------------------------------------ #
    # 3. TC19 空中速度解码（手算对照）
    # ------------------------------------------------------------------ #
    print("\n[3] 空中速度（TC19，子类型1 地速）")
    # 构造 ME：TC=19, sub=1, E/W raw=300(E+), N/S raw=250(N+)
    m = [0] * 56

    def sb(first, last, val):
        w = last - first + 1
        for i in range(w):
            m[first - 1 + w - 1 - i] = (val >> i) & 1
    sb(1, 5, 19); sb(6, 8, 1); sb(15, 24, 300); sb(26, 35, 250)
    me = bits_to_bytes(m)
    v = decode_velocity_me(me)
    expect_gs = math.hypot(299, 249)  # (raw-1)
    expect_track = math.degrees(math.atan2(299, 249))
    check("速度类型=ground_speed", v.get("kind") == "ground_speed")
    check("地速≈389.1 kt", abs(v["groundspeed_kt"] - expect_gs) < 0.2,
          f"(got {v['groundspeed_kt']}, expect {expect_gs:.1f})")
    check("航向≈50.2°", abs(v["track_deg"] - expect_track) < 0.2,
          f"(got {v['track_deg']}, expect {expect_track:.2f})")

    # ------------------------------------------------------------------ #
    # 4. CPR 全局编码→解码往返（多个纬度，验证 NL 表）
    # ------------------------------------------------------------------ #
    print("\n[4] CPR 位置全局解码（NL 表）")
    check("赤道 NL=59", cpr_nl(0.0) == 59)
    check("50° 处 NL=38", cpr_nl(50.0) == 38, f"(NL={cpr_nl(50.0)})")
    check("51.5° 处 NL=37", cpr_nl(51.5) == 37, f"(NL={cpr_nl(51.5)})")
    check("近极点(88°) NL=1", cpr_nl(88.0) == 1)
    for lat, lon in [(51.47, -0.45), (39.9, 116.4), (-33.86, 151.2), (46.0, 7.5)]:
        enc = encode_cpr_airborne(lat, lon)
        rlat, rlon = decode_cpr_airborne(
            enc["even_cprlat"], enc["even_cprlon"],
            enc["odd_cprlat"], enc["odd_cprlon"], 0)
        # CPR 17bit 量化分辨率 ~0.00027°/2；容差取量化格一半
        check(f"CPR 往返 ({lat},{lon})",
              abs(rlat - lat) < 0.001 and abs(rlon - lon) < 0.001,
              f"(got {rlat:.5f},{rlon:.5f})")

    # ADSBDecoder 有状态配对：喂偶+奇两帧解出位置
    print("\n[5] ADSBDecoder 有状态 CPR 配对")
    dec = ADSBDecoder()
    enc = encode_cpr_airborne(39.9, 116.4)
    # 偶帧 ME: TC=11, odd=0, cprlat/lon
    def make_pos_frame(odd, clat, clon):
        mm = [0] * 56
        sb2 = lambda a, b, vv: [mm.__setitem__(a - 1 + (b - a + 1) - 1 - i,
                                                (vv >> i) & 1)
                                for i in range(b - a + 1)]
        sb2(1, 5, 11); sb2(22, 22, 1 if odd else 0)
        sb2(23, 39, clat); sb2(40, 56, clon)
        return build_long_frame(0x8D, 0x40621D, bits_to_bytes(mm))
    dec.handle(make_pos_frame(False, enc["even_cprlat"], enc["even_cprlon"]))
    out = dec.handle(make_pos_frame(True, enc["odd_cprlat"], enc["odd_cprlon"]))
    check("配对后输出 lat/lon", "lat" in out and "lon" in out,
          f"(got {out.get('lat'):.4f},{out.get('lon'):.4f})" if "lat" in out else "")
    if "lat" in out:
        check("配对位置≈(39.9,116.4)",
              abs(out["lat"] - 39.9) < 0.001 and abs(out["lon"] - 116.4) < 0.001)

    # ------------------------------------------------------------------ #
    # 6. 前导检测：合成 preamble + 数据能被找到
    # ------------------------------------------------------------------ #
    print("\n[6] Preamble 检测 + PPM 位判决")
    frame = build_identification_frame("4CA8EE", "BAW123")
    iq = modulate_baseband(frame, fs=4_000_000, lead_us=50.0, amplitude=1.0)
    # 无噪
    from mbdsdr_ai.adsb import decode_baseband
    res = decode_baseband(iq, fs=4_000_000, long_frame=True)
    check("前导被检测到", res.get("found"), f"(score reason={res.get('reason')})")
    check("合成帧 CRC 通过", res.get("crc_ok"), f"(raw={res.get('raw_bytes')})")
    check("解出呼号 BAW123", res.get("callsign") == "BAW123",
          f"(got {res.get('callsign')!r})")

    # ------------------------------------------------------------------ #
    # 7. 注册到 ToolRegistry
    # ------------------------------------------------------------------ #
    print("\n[7] ToolRegistry 注册")
    from mbdsdr_ai.tool_registry import ToolRegistry
    reg = ToolRegistry()

    class _Ag:  # 最小 agent 替身
        tool_registry = reg
    register_adsb_tools(_Ag())
    names = {t["name"] for t in reg.list_tools()}
    check("adsb_decode_hex 已注册", "adsb_decode_hex" in names, f"({sorted(names)})")
    check("adsb_decode_iq 已注册", "adsb_decode_iq" in names)
    call = reg.call("adsb_decode_hex", {"hex": KNOWN})
    check("adsb_decode_hex 解出已知帧 CRC",
          call.success and call.data and call.data["crc_ok"])

    # ------------------------------------------------------------------ #
    print(f"\n== 结果: {PASS} passed, {FAIL} failed ==")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

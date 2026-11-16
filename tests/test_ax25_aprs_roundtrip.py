# SPDX-License-Identifier: MIT
"""AX.25 / APRS 端到端往返验证（合成向量，非硬件）。

依据公开 TAPR AX.25 与 APRS 规范独立实现，端到端验证编码->信道->解码往返。
direwolf (https://github.com/wb2osz/direwolf) 仅作技术参考与致谢，本仓未包含其源代码。
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.ax25 import (  # noqa: E402
    AX25Frame, AFSKModem, build_aprs_position_frame,
    hdlc_bit_stuff, hdlc_bit_unstuff, crc16_ccitt,
    APRSPosition, APRSWeather,
)
from mbdsdr_ai.aprs_parser import parse_aprs_frame  # noqa: E402


def _mkframe(info: bytes, dst: str = "APRS", src: str = "BI1AAA") -> AX25Frame:
    return AX25Frame(destination=dst, source=src,
                     control=0x03, pid=0xF0, info=info)


def test_crc_vector() -> None:
    # CRC-16/X.25 标准校验矢量，与 direwolf fcs_calc.c 表驱动结果一致。
    assert crc16_ccitt(b"123456789") == 0x906E, "CRC 校验矢量应为 0x906E"


def test_frame_roundtrip() -> None:
    f = build_aprs_position_frame("BI1AAA", 39.9042, 116.4074,
                                   comment="roundtrip", digipeaters=["WIDE2-1"])
    blob = f.to_bytes()
    g = AX25Frame.from_bytes(blob)
    assert g is not None, "解帧失败"
    assert g.fcs_valid, f"FCS 校验失败: got 0x{g.fcs:04X}"
    assert g.source == "BI1AAA"
    assert g.destination == "APRS"
    assert len(g.digipeaters) == 1 and g.digipeaters[0][0] == "WIDE2"
    print(f"  [ok] 帧往返: {g.source}->{g.destination} via "
          f"{[c for c,_,_ in g.digipeaters]}, FCS=0x{g.fcs:04X} valid")


def test_bitstuff_roundtrip() -> None:
    patterns = [
        bytes([0xFF, 0x01, 0x7E, 0x03]),
        bytes(range(256)),
        b"BI1AAA>APRS,WIDE2-1:!3954.25N/11624.44E-test",
        bytes([0xFF] * 7),
    ]
    for p in patterns:
        assert hdlc_bit_unstuff(hdlc_bit_stuff(p)) == p, f"位填充往返失败: {p[:8]!r}"
    print(f"  [ok] HDLC 位填充/去填充 往返 ({len(patterns)} 种模式)")


def test_afsk_noisy_roundtrip() -> None:
    rng = np.random.default_rng(20240924)
    modem = AFSKModem(sample_rate=48000.0)
    N = 30
    ok = 0
    for i in range(N):
        f = build_aprs_position_frame(
            "BI1AAA", 39.9 + i * 0.01, 116.4 + i * 0.01,
            comment=f"noise test {i}", digipeaters=[])
        audio = modem.modulate(f, preamble_bytes=15, postamble_bytes=4)
        sig_pow = float(np.mean(audio ** 2))
        noise = rng.standard_normal(len(audio)) * np.sqrt(sig_pow / 10.0)  # SNR=10dB
        frames = modem.demodulate(audio + noise)
        if any(fr.info == f.info for fr in frames):
            ok += 1
    rate = ok / N
    print(f"  [ok] AFSK SNR=10dB 解调成功率: {ok}/{N} = {100*rate:.0f}%")
    assert rate > 0.80, f"SNR 10dB 成功率 {rate:.0%} 低于 80%"


def test_uncompressed_position() -> None:
    # direwolf 样例
    f = _mkframe(b"!4903.50N/07201.75W_220/004g005t077r000p000P000h50b09900wRSW")
    r = parse_aprs_frame(f)
    assert r["type"] == "position"
    assert abs(r["latitude"] - 49.058333) < 1e-4, r["latitude"]
    assert abs(r["longitude"] - (-72.029167)) < 1e-4, r["longitude"]
    wx = r["weather"]
    assert wx["wind_direction"] == 220
    assert wx["wind_speed"] == 4
    assert wx["temperature_f"] == 77
    assert wx["humidity"] == 50
    assert abs(wx["pressure_mbar"] - 990.0) < 0.1
    print(f"  [ok] 未压缩位置+气象: {r['latitude']:.5f}N {abs(r['longitude']):.5f}W "
          f"wind={wx['wind_direction']}deg/{wx['wind_speed']}kt "
          f"T={wx['temperature_f']}F hum={wx['humidity']}%")


def test_compressed_position() -> None:
    # direwolf 样例 /5L!!<*e7 = 49.5N / 72.75W
    f = _mkframe(b"=/5L!!<*e7_7P[g005t077r000p000P000h50b09900")
    r = parse_aprs_frame(f)
    assert r["type"] == "position"
    assert abs(r["latitude"] - 49.5) < 1e-3, r["latitude"]
    assert abs(r["longitude"] - (-72.75)) < 1e-3, r["longitude"]
    print(f"  [ok] 压缩位置(base91): {r['latitude']:.5f} {r['longitude']:.5f}")


def test_positionless_weather() -> None:
    # direwolf 样例
    f = _mkframe(b"_10090556c220s004g005t077r000p000P000h50b09900wRSW")
    r = parse_aprs_frame(f)
    assert r["type"] == "weather"
    wx = r["weather"]
    assert wx["wind_direction"] == 220
    assert wx["wind_speed"] == 4
    assert wx["wind_gust"] == 5
    assert wx["temperature_f"] == 77
    print(f"  [ok] 无位置气象: wind {wx['wind_direction']}deg/{wx['wind_speed']}kt "
          f"gust={wx['wind_gust']} T={wx['temperature_f']}F hum={wx['humidity']}%")


def test_message() -> None:
    f = _mkframe(b":BI1AAA   :Hello 73{01")
    r = parse_aprs_frame(f)
    assert r["type"] == "message"
    assert r["message"]["addressee"] == "BI1AAA"
    assert r["message"]["message"] == "Hello 73"
    assert r["message"]["message_id"] == "01"
    print(f"  [ok] 消息: to={r['message']['addressee']!r} "
          f"text={r['message']['message']!r} id={r['message']['message_id']!r}")


def test_aprs_position_minutes_conformance() -> None:
    """APRS 位置/气象坐标分钟字段必须 00–59（进位到 60 时向度进 1）。

    反证旧缺陷：APRSPosition(89.999999,179.999999).encode() 曾产出
    '!8960.00N/17960.00E-'（两处 MM=60，非法）；APRSWeather 同理。
    位置格式 '!'+纬8+符号表1+经9+…，气象格式 '_'+纬8+'/'+经9+'_'+…，
    纬度 MM.hh 在 payload[3:8]、经度 MM.hh 在 payload[13:18]。
    """
    # 边界进位
    assert APRSPosition(latitude=89.999999,
                        longitude=179.999999).encode() == "!9000.00N/18000.00E-"
    assert APRSWeather(latitude=45.999999,
                       longitude=12.99999).encode().startswith("_4600.00N/01300.00E_")

    # 密集扫描：位置 + 气象，分钟字段恒 < 60
    for deg in range(0, 90):
        for frac in (0.99999, 0.999999):
            coord = deg + frac
            pp = APRSPosition(latitude=coord, longitude=coord).encode()
            assert float(pp[3:8]) < 60.0, pp
            assert float(pp[13:18]) < 60.0, pp
            ww = APRSWeather(latitude=coord, longitude=coord).encode()
            assert float(ww[3:8]) < 60.0, ww
            assert float(ww[13:18]) < 60.0, ww

    # 生产帧构建器（build_aprs_position_frame 走 APRSPosition.encode）
    f = build_aprs_position_frame("BI1AAA", 89.999999, 179.999999)
    assert f.info == b"!9000.00N/18000.00E-", f.info

    # 正常值不变（回归守卫）
    assert APRSPosition(latitude=39.9042,
                        longitude=116.4074).encode() == "!3954.25N/11624.44E-"

    # 往返精度保持（含边界）
    for la, lo in [(39.9042, 116.4074), (89.999999, 179.999999), (-33.8688, 151.2093)]:
        dec = APRSPosition.decode(APRSPosition(latitude=la, longitude=lo).encode())
        assert dec is not None
        assert abs(dec.latitude - la) < 0.001, (dec.latitude, la)
        assert abs(dec.longitude - lo) < 0.001, (dec.longitude, lo)
    print("  [ok] APRS 位置/气象分钟字段进位合规（MM 恒 00–59），正常值不变")


def main() -> int:
    print("== AX.25 / AFSK / APRS 真实往返测试 ==")
    test_crc_vector()
    print("  [ok] CRC-16/X.25 校验矢量 0x906E")
    test_frame_roundtrip()
    test_bitstuff_roundtrip()
    test_afsk_noisy_roundtrip()
    test_uncompressed_position()
    test_compressed_position()
    test_positionless_weather()
    test_message()
    test_aprs_position_minutes_conformance()
    print("== 全部通过 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())

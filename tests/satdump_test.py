"""SatDump 真实源码移植验证测试。

对照 SatDump (github.com/altillimity/SatDump) C++ 源码逐行移植后的自检：
  1. 投影：已知卫星位置 + 像元 -> 地面经纬度（星下点应回到星下点，边缘应偏离）；
  2. 等距矩形投影往返；
  3. 图像合成：已知通道数据 -> RGB / 伪彩色不崩溃、形状正确；
  4. LRPT：QPSK 软符号 -> Viterbi(CCSDS R=1/2 K=7) -> 无噪往返 0 误码、加噪退化；
  5. HRPT 骨架：注入 60-bit 同步字 + 10-bit 字 -> 成帧；
  6. 参数验证：符号率/码率/帧长与 SatDump 源码常量一致。

运行：pytest tests/satdump_test.py -v
"""

from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai import satdump_adapter as SA


# --------------------------------------------------------------------------- #
# 6. 参数验证：与 SatDump 源码常量一致
# --------------------------------------------------------------------------- #
def test_lrpt_params_match_satdump_source():
    # Meteor-M.json meteor_m2_lrpt: symbolrate=72e3
    assert SA.LRPT_SYMBOL_RATE == 72_000
    assert SA.LRPT_RRC_ALPHA == 0.5
    # module_meteor_lrpt_decoder.cpp:14-15
    assert SA.LRPT_FRAME_SIZE == 1024
    assert SA.LRPT_ENCODED_FRAME_SIZE == 16384
    # viterbi27.h:8  CCSDS_R2_K7_POLYS = {79, 109}
    assert SA.LRPT_VITERBI_POLYS == (79, 109)
    assert SA.LRPT_VITERBI_POLYS[0] == 79
    assert SA.LRPT_VITERBI_POLYS[1] == 109
    # decoder.cpp:256 CADU sync
    assert list(SA.LRPT_CADU_SYNC) == [0x1D, 0xCF, 0xFC, 0x1D]
    # msumr_lrpt projection settings
    assert SA.LRPT_SCAN_ANGLE_DEG == 110.1
    assert SA.LRPT_IMAGE_WIDTH == 1568


def test_hrpt_params_match_satdump_source():
    # noaa_deframer.cpp:6-17
    assert SA.HRPT_SYNC_WORDS == (0x0284, 0x016F, 0x035C, 0x019D, 0x020F, 0x0095)
    assert SA.HRPT_MINOR_FRAME_SYNC == 0x0A116FD719D83C95
    assert SA.HRPT_SYNC_WORD_COUNT == 6
    assert SA.HRPT_MINOR_FRAME_WORDS == 11090
    assert SA.HRPT_BITS_PER_WORD == 10
    assert SA.HRPT_SYMBOL_RATE == 665_400
    # minor frame = 11090 * 10 bits = 110900 bits; at 665400 bps ~= 6 fps
    assert SA.HRPT_MINOR_FRAME_WORDS * SA.HRPT_BITS_PER_WORD == 110_900


def test_wgs84_constants():
    # wgs84.h:9-20
    assert SA.WGS84_A_KM == pytest.approx(6378.137)
    assert SA.WGS84_RF == pytest.approx(298.257223563)
    assert SA.WGS84_B_KM == pytest.approx(6356.7523142, rel=1e-6)


# --------------------------------------------------------------------------- #
# 1+2. 投影：星下点 / 边缘 / 等距矩形往返
# --------------------------------------------------------------------------- #
def test_projection_nadir_matches_subpoint():
    """星下点像元(x=width/2)应投影回卫星正下方经纬度。"""
    pos, vel = SA.circular_orbit_ecef(alt_km=821.0, lat_deg=45.0, lon_deg=100.0)
    sat_lat, sat_lon, _ = SA.ecef_to_lla(pos)
    res = SA.MapProjector.project_pixel(
        pos, vel, SA.LRPT_IMAGE_WIDTH / 2,
        SA.LRPT_IMAGE_WIDTH, SA.LRPT_SCAN_ANGLE_DEG,
    )
    assert res is not None
    glat, glon = res
    assert glat == pytest.approx(sat_lat, abs=0.5)
    assert glon == pytest.approx(sat_lon, abs=0.5)


def test_projection_edge_offset_from_nadir():
    """边缘像元应明显偏离星下点。"""
    pos, vel = SA.circular_orbit_ecef(alt_km=821.0, lat_deg=45.0, lon_deg=100.0)
    nadir = SA.MapProjector.project_pixel(
        pos, vel, SA.LRPT_IMAGE_WIDTH / 2, SA.LRPT_IMAGE_WIDTH, SA.LRPT_SCAN_ANGLE_DEG)
    edge = SA.MapProjector.project_pixel(
        pos, vel, 0, SA.LRPT_IMAGE_WIDTH, SA.LRPT_SCAN_ANGLE_DEG)
    assert edge is not None
    d = max(abs(edge[0] - nadir[0]), abs(edge[1] - nadir[1]))
    assert d > 5.0  # 跨轨扫描应产生显著地面偏移


def test_ecef_lla_roundtrip():
    pos = SA.lla_to_ecef(30.0, -50.0, 820.0)
    lat, lon, alt = SA.ecef_to_lla(pos)
    assert lat == pytest.approx(30.0, abs=1e-6)
    assert lon == pytest.approx(-50.0, abs=1e-6)
    assert alt == pytest.approx(820.0, abs=1e-3)


def test_equirectangular_roundtrip():
    x, y = SA.MapProjector.equirect_fwd(-23.5, 120.0)
    assert x == pytest.approx(120.0)
    assert y == pytest.approx(-23.5)
    lat, lon = SA.MapProjector.equirect_inv(x, y)
    assert lat == pytest.approx(-23.5)
    assert lon == pytest.approx(120.0)


# --------------------------------------------------------------------------- #
# 3. 图像合成
# --------------------------------------------------------------------------- #
def test_image_compose_rgb_and_falsecolor():
    rng = np.random.default_rng(0)
    ch1 = rng.random((50, 1568)) * 4095
    ch2 = rng.random((50, 1568)) * 4095
    ch3 = rng.random((50, 1568)) * 4095
    rgb = SA.SatImageProcessor.compose_rgb(ch1, ch2, ch3)
    assert rgb.shape == (50, 1568, 3)
    assert rgb.dtype == np.uint8
    fc = SA.SatImageProcessor.false_color_ir(ch1, ch2, ch3)
    assert fc.shape == (50, 1568, 3)
    assert fc.min() >= 0 and fc.max() <= 255


# --------------------------------------------------------------------------- #
# 4. LRPT Viterbi 往返
# --------------------------------------------------------------------------- #
def _build_encoder_table():
    g1, g2 = SA.LRPT_VITERBI_POLYS
    K = 7
    n = 1 << (K - 1)
    o0 = np.zeros((n, 2), dtype=np.uint8)
    o1 = np.zeros((n, 2), dtype=np.uint8)
    nx = np.zeros((n, 2), dtype=np.int64)
    for s in range(n):
        for u in (0, 1):
            reg = (s << 1) | u
            a = b = 0
            for k in range(K):
                if (g1 >> k) & 1:
                    a ^= (reg >> k) & 1
                if (g2 >> k) & 1:
                    b ^= (reg >> k) & 1
            o0[s, u] = a
            o1[s, u] = b
            nx[s, u] = (s >> 1) | (u << (K - 2))
    return o0, o1, nx


def test_lrpt_viterbi_noiseless_zero_ber():
    o0, o1, nx = _build_encoder_table()
    rng = np.random.default_rng(1)
    info = rng.integers(0, 2, 800)
    # zero-terminate
    info_t = np.concatenate([info, np.zeros(6, dtype=int)])
    enc = []
    st = 0
    for b in info_t:
        enc += [o0[st, b], o1[st, b]]
        st = nx[st, b]
    soft = np.array(enc, dtype=float) * 2 - 1  # +1/-1
    dec = SA.LRPTDecoder()
    decoded = dec.viterbi_decode(soft)
    ber = np.mean(decoded[:800] != info)
    assert ber == pytest.approx(0.0, abs=1e-9)


def test_lrpt_viterbi_ber_degrades_with_noise():
    o0, o1, nx = _build_encoder_table()
    rng = np.random.default_rng(2)
    info = rng.integers(0, 2, 600)
    info_t = np.concatenate([info, np.zeros(6, dtype=int)])
    enc = []
    st = 0
    for b in info_t:
        enc += [o0[st, b], o1[st, b]]
        st = nx[st, b]
    base = np.array(enc, dtype=float) * 2 - 1
    dec = SA.LRPTDecoder()
    ber_low = np.mean(dec.viterbi_decode(base + rng.standard_normal(len(base)) * 0.5)[:600] != info)
    ber_high = np.mean(dec.viterbi_decode(base + rng.standard_normal(len(base)) * 2.0)[:600] != info)
    assert ber_low < 0.05
    assert ber_high > ber_low  # 噪声越大误码越高


# --------------------------------------------------------------------------- #
# 5. HRPT 帧同步骨架
# --------------------------------------------------------------------------- #
def test_hrpt_deframer_locks_on_sync():
    dec = SA.HRPTDecoder(sync_threshold=0)
    # 构造比特流：同步字 60 bit (MSB first)
    bits = []
    for i in range(59, -1, -1):
        bits.append((SA.HRPT_MINOR_FRAME_SYNC >> i) & 1)
    # 补足一帧：(11090-6)*10 bit 填充 0
    nfill = (SA.HRPT_MINOR_FRAME_WORDS - SA.HRPT_SYNC_WORD_COUNT) * SA.HRPT_BITS_PER_WORD
    bits += [0] * nfill
    frames = dec.work(np.array(bits, dtype=float))
    assert len(frames) >= 1
    f = frames[0]
    assert len(f.words) == SA.HRPT_MINOR_FRAME_WORDS
    assert tuple(f.words[:6]) == SA.HRPT_SYNC_WORDS

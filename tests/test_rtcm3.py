#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_rtcm3.py — RTCM3 解析 + 多星座 SPP + RTK float 接口测试

覆盖：
  1. CRC24Q 已知向量
  2. RTCM3 帧同步 / 长度解析
  3. CRC 错误检测
  4. 1005 帧解析（基站 ECEF）
  5. 1077 GPS MSM7 解析
  6. 1097 Galileo MSM7 解析（另一星座）
  7. SPP 多星座合成数据位置 < 100m
  8. SPP GPS+GLONASS 钟差分离
  9. RTK float 接口可调用（mock 观测）
 10. GNSSPipeline SPP 兜底
"""

from __future__ import annotations

import math
import struct

import numpy as np
import pytest

from mbdsdr_ai.rtcm3_decoder import (
    RTCM3Decoder, RTCM3_0_PREAMBLE, build_rtcm3_frame, rtcm3_crc24,
)
from mbdsdr_ai.rtklib_adapter import (
    CLIGHT, CoordinateConverter, GNSSPipeline, SPPLocator,
)
from mbdsdr_ai.rtk_solver import RTKFloatSolver, RTKInput, RTKObs


# ============================================================
# 位打包工具（测试用）
# ============================================================
def pack_bits(bits):
    """[(n, value), ...] -> bytes（MSB-first）。"""
    out = bytearray()
    cur = 0
    n = 0
    for width, val in bits:
        for i in range(width - 1, -1, -1):
            cur = (cur << 1) | ((val >> i) & 1)
            n += 1
            if n == 8:
                out.append(cur & 0xFF)
                cur = 0
                n = 0
    if n:
        out.append((cur << (8 - n)) & 0xFF)
    return bytes(out)


def make_1005_payload(station_id: int, x_m: float, y_m: float, z_m: float) -> bytes:
    return pack_bits([
        (12, 1005),
        (12, station_id),
        (6, 0), (4, 0),
        (38, int(round(x_m * 10000))),
        (38, int(round(y_m * 10000))),
        (38, int(round(z_m * 10000))),
    ])


def make_msm7_payload(msg_type: int, sys_code: int, station_id: int,
                      tow_ms: int, sats: list, sigs: list,
                      pr_corr: float = 0.0, cnr_dbhz: float = 45.0) -> bytes:
    """构造最小 MSM7 payload。

    sats: list of PRN (1..64)
    sigs: list of signal id (1..32)
    """
    bits = []
    bits.append((12, msg_type))
    bits.append((12, station_id))
    if sys_code == 1:  # GLO: dow(3)+tod(27)
        bits.append((3, 0))
        bits.append((27, tow_ms))
    elif sys_code == 4:  # BDS: tow(30)
        bits.append((30, tow_ms))
    else:  # GPS/GAL
        bits.append((30, tow_ms))
    bits += [(1, 0), (3, 0), (7, 0), (2, 0), (2, 0), (1, 0), (3, 0)]
    for j in range(1, 65):
        bits.append((1, 1 if j in sats else 0))
    for j in range(1, 33):
        bits.append((1, 1 if j in sigs else 0))
    # cellmask: nsat*nsig, all 1
    for _ in range(len(sats) * len(sigs)):
        bits.append((1, 1))
    # sat data: MSM7 按字段跨卫星排列（rtcm3.c:2026-2040）：
    #   range(8)*nsat, ext(4)*nsat, range_mod(10)*nsat, phaserate(14)*nsat
    for _ in sats:
        bits.append((8, 20000 & 0xFF))
    for _ in sats:
        bits.append((4, 0))
    for _ in sats:
        bits.append((10, 0))
    for _ in sats:
        bits.append((14, 0))
    # cell data: 按字段跨单元格排列（rtcm3.c:2042-2062）：
    #   pr_corr(20)*ncell, cp_corr(24)*ncell, lock(10)*ncell,
    #   half(1)*ncell, cnr(10)*ncell, phaserate(15)*ncell
    ncell = len(sats) * len(sigs)
    pr_int = int(round(pr_corr / (2.0 ** -29 * CLIGHT * 0.001)))
    for _ in range(ncell):
        bits.append((20, pr_int))
    for _ in range(ncell):
        bits.append((24, 0))
    for _ in range(ncell):
        bits.append((10, 100))
    for _ in range(ncell):
        bits.append((1, 0))
    for _ in range(ncell):
        bits.append((10, int(round(cnr_dbhz * 16))))
    for _ in range(ncell):
        bits.append((15, 0))
    return pack_bits(bits)


# ============================================================
# 1. CRC24Q 测试向量
# ============================================================
class TestCRC24:
    def test_empty_input_is_zero(self):
        assert rtcm3_crc24(b"") == 0

    def test_known_vector_1005_frame(self):
        """对一个已知 1005 payload 计算 CRC，与重算自洽。"""
        payload = make_1005_payload(1234, 1000000.0, -500000.0, 3500000.0)
        frame = build_rtcm3_frame(payload)
        # frame = preamble(1) + len(2) + payload + crc(3)
        body = frame[:-3]
        crc_stored = int.from_bytes(frame[-3:], "big")
        assert rtcm3_crc24(body) == crc_stored

    def test_poly_is_crc24q(self):
        """CRC 多项式应为 0x1864CFB（与 RTKLIB 表驱动一致）。"""
        # 与表驱动实现做随机向量对比
        POLY = 0x1864CFB
        tbl = []
        for k in range(256):
            crc = k << 16
            for _ in range(8):
                crc <<= 1
                if crc & 0x1000000:
                    crc ^= POLY
                crc &= 0xFFFFFF
            tbl.append(crc)

        def table_crc(data):
            crc = 0
            for b in data:
                crc = ((crc << 8) & 0xFFFFFF) ^ tbl[((crc >> 16) ^ b) & 0xFF]
            return crc

        for blob in [b"\xd3\x00\x13", b"hello rtcm3", bytes(range(64))]:
            assert rtcm3_crc24(blob) == table_crc(blob)


# ============================================================
# 2-3. 帧同步 / CRC 错误
# ============================================================
class TestFraming:
    def test_preamble_constant(self):
        assert RTCM3_0_PREAMBLE == 0xD3

    def test_stream_finds_frame_after_noise(self):
        payload = make_1005_payload(1, 0.0, 0.0, 0.0)
        frame = build_rtcm3_frame(payload)
        # 前面加垃圾字节
        noisy = b"\x00\xff\xaa" + frame
        dec = RTCM3Decoder()
        msgs = dec.feed(noisy)
        assert len(msgs) == 1
        assert msgs[0]["type"] == 1005

    def test_crc_mismatch_rejected(self):
        payload = make_1005_payload(1, 0.0, 0.0, 0.0)
        frame = bytearray(build_rtcm3_frame(payload))
        # 翻转 payload 中一个字节
        frame[5] ^= 0xFF
        dec = RTCM3Decoder()
        msgs = dec.feed(bytes(frame))
        assert len(msgs) == 1
        assert msgs[0]["crc_ok"] is False


# ============================================================
# 4. 1005 解析
# ============================================================
class Test1005:
    def test_parse_1005(self):
        x, y, z = 1000000.1234, -500000.5, 3500000.0
        frame = build_rtcm3_frame(make_1005_payload(42, x, y, z))
        dec = RTCM3Decoder()
        msgs = dec.feed(frame)
        assert len(msgs) == 1
        m = msgs[0]
        assert m["type"] == 1005
        assert m["crc_ok"] is True
        assert m["station_id"] == 42
        # 0.1 mm 分辨率
        assert abs(m["antenna_ecef"][0] - x) < 1e-3
        assert abs(m["antenna_ecef"][1] - y) < 1e-3
        assert abs(m["antenna_ecef"][2] - z) < 1e-3


# ============================================================
# 5-6. MSM7 解析
# ============================================================
class TestMSM7:
    def test_parse_gps_1077(self):
        payload = make_msm7_payload(
            1077, 0, 100, 40000000,
            sats=[1, 2], sigs=[2],  # sig id=2 -> "1C"
        )
        frame = build_rtcm3_frame(payload)
        dec = RTCM3Decoder()
        msgs = dec.feed(frame)
        assert len(msgs) == 1
        m = msgs[0]
        assert m["type"] == 1077
        assert m["constellation"] == "GPS"
        assert m["station_id"] == 100
        assert abs(m["gps_tow"] - 40000.0) < 1e-3
        assert m["nsat"] == 2
        assert m["nsig"] == 1
        assert m["ncell"] == 2
        assert len(m["observations"]) == 2
        for obs in m["observations"]:
            assert obs["constellation"] == "GPS"
            assert obs["cnr"] == pytest.approx(45.0, abs=0.1)
            assert "pseudorange" in obs
            assert "half_cycle_ambiguity" in obs

    def test_parse_galileo_1097(self):
        payload = make_msm7_payload(
            1097, 2, 200, 50000000,
            sats=[1, 2, 3], sigs=[1],  # sig id=1 -> "" (reserved), but parses
        )
        frame = build_rtcm3_frame(payload)
        dec = RTCM3Decoder()
        msgs = dec.feed(frame)
        assert len(msgs) == 1
        m = msgs[0]
        assert m["type"] == 1097
        assert m["constellation"] == "Galileo"
        assert m["nsat"] == 3
        assert len(m["observations"]) == 3

    def test_chunked_stream(self):
        """跨包喂入字节流应能正确组帧。"""
        payload = make_msm7_payload(1077, 0, 1, 1000000, sats=[1], sigs=[2])
        frame = build_rtcm3_frame(payload)
        dec = RTCM3Decoder()
        msgs = []
        for i in range(0, len(frame), 3):
            msgs += dec.feed(frame[i:i + 3])
        assert len(msgs) == 1
        assert msgs[0]["type"] == 1077


# ============================================================
# 7-8. SPP 多星座
# ============================================================
def _place_satellites(lat0_deg, lon0_deg, h0_m, rsat=26500000.0):
    """在真实接收机周围布置 6 颗 GPS 卫星（合成）。"""
    lat, lon = math.radians(lat0_deg), math.radians(lon0_deg)
    rr_true = np.array(CoordinateConverter.llh_to_ecef(lat, lon, h0_m))
    sats = [(45, 0), (45, 90), (45, 180), (45, -90), (10, 45), (10, 225)]
    return rr_true, sats


class TestSPPMulti:
    def test_spp_gps_position_under_100m(self):
        rr_true, sats = _place_satellites(39.9, 116.4, 50.0)
        obs = []
        b_true = 2000.0
        for slat, slon in sats:
            rs = np.array(CoordinateConverter.llh_to_ecef(
                math.radians(slat), math.radians(slon), 26500000.0))
            r = float(np.linalg.norm(rs - rr_true))
            obs.append({"rs": rs, "dts": 0.0, "pr": r + b_true, "system": "GPS"})
        sol = SPPLocator().locate_multi(obs)
        err = math.sqrt(
            (sol["x"] - rr_true[0]) ** 2 +
            (sol["y"] - rr_true[1]) ** 2 +
            (sol["z"] - rr_true[2]) ** 2)
        assert err < 100.0
        assert sol["n_sat"] == 6
        assert sol["fix_type"] != "NONE" if "fix_type" in sol else True

    def test_spp_gps_glonass_bias_separation(self):
        rr_true, gps_sats = _place_satellites(39.9, 116.4, 50.0)
        glo_sats = [(20, 45), (20, 225), (20, 135)]
        obs = []
        b_gps, b_glo = 2000.0, 500.0
        for slat, slon in gps_sats:
            rs = np.array(CoordinateConverter.llh_to_ecef(
                math.radians(slat), math.radians(slon), 26500000.0))
            r = float(np.linalg.norm(rs - rr_true))
            obs.append({"rs": rs, "dts": 0.0, "pr": r + b_gps, "system": "GPS"})
        for slat, slon in glo_sats:
            rs = np.array(CoordinateConverter.llh_to_ecef(
                math.radians(slat), math.radians(slon), 26500000.0))
            r = float(np.linalg.norm(rs - rr_true))
            obs.append({"rs": rs, "dts": 0.0, "pr": r + b_gps + b_glo,
                        "system": "GLONASS"})
        sol = SPPLocator().locate_multi(obs)
        # 应分离出 GPS 与 GLONASS 钟差
        assert "GLONASS" in sol["sys_biases_m"]
        assert abs(sol["sys_biases_m"]["GLONASS"] - b_glo) < 50.0
        err = math.sqrt(
            (sol["x"] - rr_true[0]) ** 2 +
            (sol["y"] - rr_true[1]) ** 2 +
            (sol["z"] - rr_true[2]) ** 2)
        assert err < 100.0

    def test_spp_too_few_satellites_returns_none(self):
        rr_true, sats = _place_satellites(39.9, 116.4, 50.0)
        obs = []
        for slat, slon in sats[:3]:
            rs = np.array(CoordinateConverter.llh_to_ecef(
                math.radians(slat), math.radians(slon), 26500000.0))
            r = float(np.linalg.norm(rs - rr_true))
            obs.append({"rs": rs, "dts": 0.0, "pr": r, "system": "GPS"})
        sol = SPPLocator().locate_multi(obs)
        assert sol["fix_type"] == "NONE"


# ============================================================
# 9. RTK float 接口
# ============================================================
class TestRTKFloat:
    def _make_input(self):
        lat, lon, h = math.radians(39.9), math.radians(116.4), 50.0
        base_ecef = np.array(CoordinateConverter.llh_to_ecef(lat, lon, h))
        rover_ecef = np.array(CoordinateConverter.llh_to_ecef(
            lat + 0.001, lon, h))
        sats = [(45, 0), (45, 90), (45, 180), (45, -90), (10, 45), (10, 225)]
        base_obs, rover_obs = [], []
        for k, (slat, slon) in enumerate(sats, start=1):
            rs = np.array(CoordinateConverter.llh_to_ecef(
                math.radians(slat), math.radians(slon), 26500000.0))
            r_b = float(np.linalg.norm(rs - base_ecef))
            r_r = float(np.linalg.norm(rs - rover_ecef))
            base_obs.append(RTKObs(prn=k, rs=rs, pseudorange=r_b,
                                   carrier_phase=r_b))
            rover_obs.append(RTKObs(prn=k, rs=rs, pseudorange=r_r,
                                    carrier_phase=r_r))
        return RTKInput(base_ecef=base_ecef, base_obs=base_obs,
                       rover_obs=rover_obs)

    def test_rtk_float_runs_and_outputs_format(self):
        inp = self._make_input()
        sol = RTKFloatSolver().solve(inp)
        assert sol["fix_type"] == "RTK_FLOAT"
        assert "lat_deg" in sol
        assert "lon_deg" in sol
        assert "alt" in sol
        assert sol["num_sats"] >= 4
        assert "hdop" in sol
        assert "float only" in sol["note"]

    def test_rtk_too_few_satellites(self):
        inp = self._make_input()
        inp.rover_obs = inp.rover_obs[:3]
        inp.base_obs = inp.base_obs[:3]
        sol = RTKFloatSolver().solve(inp)
        assert sol["fix_type"] == "NONE"

    def test_lambda_fix_is_todo(self):
        solver = RTKFloatSolver()
        with pytest.raises(NotImplementedError):
            solver.fix_ambiguities()


# ============================================================
# 10. GNSSPipeline
# ============================================================
class TestGNSSPipeline:
    def test_pipeline_spp_fallback(self):
        """无基站观测时，pipeline 应退化为 SPP。"""
        rr_true, sats = _place_satellites(39.9, 116.4, 50.0)
        # 先喂一个 1005（仅坐标，无 MSM 观测）
        frame = build_rtcm3_frame(make_1005_payload(1, *rr_true))
        pipe = GNSSPipeline()
        pipe.feed_rtcm(frame)
        assert pipe.base_ecef is not None

        rover_obs = []
        for slat, slon in sats:
            rs = np.array(CoordinateConverter.llh_to_ecef(
                math.radians(slat), math.radians(slon), 26500000.0))
            r = float(np.linalg.norm(rs - rr_true))
            rover_obs.append({"prn": 1, "system": "GPS", "rs": rs,
                              "dts": 0.0, "pseudorange": r + 2000.0})
        sol = pipe.solve(rover_obs)
        # 因基站没有观测，应走 SPP 路径
        assert sol["fix_type"] == "SPP"
        assert sol["num_sats"] >= 4
        assert "hdop" in sol

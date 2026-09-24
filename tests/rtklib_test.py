#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rtklib_test.py — 验证 rtklib_adapter.py 移植自 RTKLIB 真实源码的算法。

覆盖（对应任务第二步要求）：
  1. 时间转换：已知 GPS 周/秒 -> UTC 正确（含闰秒）
  2. 坐标转换：已知 ECEF -> LLH 正确（误差 < 1cm）
  3. ENU 转换：已知站心坐标 -> 方位角/仰角正确
  4. RINEX：合成导航/观测行 -> 解析出正确星历与伪距
  5. SPP：已知卫星位置 + 伪距 -> 解算位置（合成无噪测试）
  6. 常数验证：光速/GM/椭球参数与 RTKLIB C 源码逐位一致

所有断言旁标注来源 RTKLIB src/<file>.c:<line>。
"""

import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai import rtklib_adapter as ra


class TestPhysicalConstants(unittest.TestCase):
    """物理常数必须与 RTKLIB 源码逐位一致。"""

    def test_constants_match_source(self):
        # rtklib.h:59  CLIGHT 299792458.0
        self.assertEqual(ra.CLIGHT, 299792458.0)
        # rtklib.h:64  OMGE 7.2921151467E-5
        self.assertEqual(ra.OMGE, 7.2921151467e-5)
        # rtklib.h:66  RE_WGS84 6378137.0
        self.assertEqual(ra.RE_WGS84, 6378137.0)
        # rtklib.h:67  FE_WGS84 1/298.257223563
        self.assertAlmostEqual(ra.FE_WGS84, 1.0 / 298.257223563, places=16)
        # ephemeris.c:64  MU_GPS 3.9860050E14
        self.assertEqual(ra.MU_GPS, 3.9860050e14)
        # rtklib.h:56  PI
        self.assertEqual(ra.PI, 3.1415926535897932)


class TestTimeSystem(unittest.TestCase):
    """时间系统 —— 来源 rtkcmn.c:1246/1261/1425/1442。"""

    def test_gps_epoch_zero(self):
        """GPS 周0 周内秒0 = 1980-01-06 00:00:00 UTC（当时无闰秒）。"""
        out = ra.gps_time_to_utc(0, 0.0)
        self.assertEqual(out["year"], 1980)
        self.assertEqual(out["month"], 1)
        self.assertEqual(out["day"], 6)
        self.assertEqual(out["hour"], 0)
        self.assertEqual(out["minute"], 0)
        self.assertAlmostEqual(out["second"], 0.0, places=3)
        # 1980 起点 UTC-GPST = 0
        self.assertEqual(out["leap_seconds"], 0)

    def test_modern_leap_seconds_18(self):
        """2024 年 UTC-GPST = -18 s（rtkcmn.c:137 表首条 2017-01-01 起）。"""
        # 选一个 2024 中的 GPS 时刻，验证闰秒字段
        # 2024-01-01 00:00:00 UTC -> 反推 GPS 周/秒
        w, tow = ra.TimeSystem.utc_to_gps_time(2024, 1, 1, 0, 0, 0.0)
        back = ra.gps_time_to_utc(w, tow)
        self.assertEqual(back["year"], 2024)
        self.assertEqual(back["month"], 1)
        self.assertEqual(back["day"], 1)
        self.assertEqual(back["hour"], 0)
        self.assertEqual(back["leap_seconds"], -18)

    def test_gps_utc_roundtrip(self):
        """任意 UTC 时刻 <-> GPS 周/秒 往返一致。"""
        cases = [(2019, 6, 15, 8, 30, 45.5), (2026, 9, 24, 12, 0, 0.0)]
        for (y, mo, d, hh, mm, ss) in cases:
            w, tow = ra.TimeSystem.utc_to_gps_time(y, mo, d, hh, mm, ss)
            out = ra.gps_time_to_utc(w, tow)
            self.assertEqual(out["year"], y)
            self.assertEqual(out["month"], mo)
            self.assertEqual(out["day"], d)
            self.assertEqual(out["hour"], hh)
            self.assertEqual(out["minute"], mm)
            self.assertAlmostEqual(out["second"], ss, places=3)


class TestCoordinateConverter(unittest.TestCase):
    """坐标转换 —— 来源 rtkcmn.c:1634/1655/1686/3218。"""

    def test_equator_prime_meridian(self):
        """ECEF (a,0,0) -> lat=0,lon=0,h=0。"""
        lat, lon, h = ra.CoordinateConverter.ecef_to_llh(ra.RE_WGS84, 0.0, 0.0)
        self.assertAlmostEqual(lat, 0.0, places=9)
        self.assertAlmostEqual(lon, 0.0, places=9)
        self.assertAlmostEqual(h, 0.0, places=3)

    def test_pole(self):
        """ECEF (0,0,b) -> lat=+90°, h=0。b=a*(1-f)。"""
        b = ra.RE_WGS84 * (1.0 - ra.FE_WGS84)
        lat, lon, h = ra.CoordinateConverter.ecef_to_llh(0.0, 0.0, b)
        self.assertAlmostEqual(lat, ra.PI / 2.0, places=9)
        self.assertAlmostEqual(h, 0.0, places=3)

    def test_llh_ecef_roundtrip_under_1cm(self):
        """LLH->ECEF->LLH 往返误差 < 1cm。rtkcmn.c:1634/1655 互逆。"""
        for (lat_d, lon_d, h) in [(39.9042, 116.4074, 50.0),
                                  (-33.8688, 151.2093, 100.0),
                                  (0.0, 0.0, 0.0)]:
            lat = lat_d * ra.D2R; lon = lon_d * ra.D2R
            x, y, z = ra.CoordinateConverter.llh_to_ecef(lat, lon, h)
            lat2, lon2, h2 = ra.CoordinateConverter.ecef_to_llh(x, y, z)
            self.assertLess(abs(lat2 - lat) * ra.RE_WGS84, 0.01, f"lat err {lat_d}")
            self.assertLess(abs(lon2 - lon) * ra.RE_WGS84, 0.01, f"lon err {lon_d}")
            self.assertLess(abs(h2 - h), 0.01, f"h err {h}")

    def test_ecef_to_enu_north_up(self):
        """站心(赤道, lon=0)处：ECEF+Z = 北向，ECEF+X = 天向。
        rtkcmn.c:1671 xyz2enu 在 lat=0,lon=0 给出 E=(0,1,0),N=(0,0,1),U=(1,0,0)。"""
        e, n, u = ra.CoordinateConverter.ecef_to_enu(0.0, 0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(e, 0.0, places=9)
        self.assertAlmostEqual(n, 1.0, places=9)
        self.assertAlmostEqual(u, 0.0, places=9)
        e2, n2, u2 = ra.CoordinateConverter.ecef_to_enu(0.0, 0.0, 1.0, 0.0, 0.0)
        self.assertAlmostEqual(u2, 1.0, places=9)

    def test_azel_due_east(self):
        """站心(赤道,lon=0)，卫星在 ECEF +Y 方向 -> 正东：az=90°, el=0°。
        rtkcmn.c:3218 satazel(): az=atan2(enu_e, enu_n)。"""
        # 接收机在 (a,0,0)，卫星方向向量取单位 (0,1,0)（近似视线）
        e_vec = np.array([0.0, 1.0, 0.0])
        az, el = ra.CoordinateConverter.satazel(0.0, 0.0, e_vec)
        self.assertAlmostEqual(az, ra.PI / 2.0, places=6)   # 90°
        self.assertAlmostEqual(el, 0.0, places=6)

    def test_azel_zenith(self):
        """头顶正上方 -> el=90°。"""
        e_vec = np.array([1.0, 0.0, 0.0])  # 站在(a,0,0)，径向即 +X = Up
        az, el = ra.CoordinateConverter.satazel(0.0, 0.0, e_vec)
        self.assertAlmostEqual(el, ra.PI / 2.0, places=6)


class TestRINEXParser(unittest.TestCase):
    """RINEX 解析 —— 来源 rinex.c:1005/1187/1166。"""

    # 时钟行：PRN(col0-1) + col2 空格 + 19 字符日期(col3-21) + 3 个 19 字符钟差字段
    # 对应 rinex.c:1212 prn=str2num(buff,0,2); sp=3; 钟差起于 buff+sp+19=col22
    _date = ("23 01 01 00 00  0.0").ljust(19)          # cols 3..21
    # 28 个轨道参数（data[3..30]），顺序对应 rinex.c:1237-1238 每行4个19字符字段
    _orbit = [
        10.2, 120.0, 3e-8, 0.51,            # line2: IODE,Crs,Deln,M0
        2e-7, 1.23456e-3, 4e-8, 5.1536e7,   # line3: Cuc,e,Cus,sqrtA
        30240.0, 10.0, 2e-7, 1.3,           # line4: toe,Cic,OMG0,Cis
        0.55, 150.0, -3e-8, 1e-9,           # line5: i0,Crc,omg,OMGd
        1.0, 1000.0, 2048.0, 1.0,           # line6: idot,code,week,flag
        2.0, 0.0, -1.5e-9, 1000.0,          # line7: URA,svh,TGD,IODC
        2048.0, 0.0, 0.0, 0.0,              # line8: ttr,fit,...
    ]
    _ol = ""
    for _k in range(7):
        _ol += "%19.10e%19.10e%19.10e%19.10e\n" % tuple(_orbit[_k * 4:_k * 4 + 4])
    NAV_TEXT = (
        (" 1 " + _date + "%19.10e%19.1e%19.1e\n"
         % (1.23e-8, 4.0e-12, -1.0e-10))
        + _ol
    )

    def test_parse_nav_ephemeris_fields(self):
        ephs = ra.RINEXParser.parse_nav(self.NAV_TEXT)
        self.assertEqual(len(ephs), 1)
        e = ephs[0]
        self.assertEqual(e.sat, 1)
        # data[10] = sqrt(A) = 5.1536e7 -> A = (sqrtA)^2  (rinex.c:1028)
        self.assertAlmostEqual(math.sqrt(e.A), 5.1536e7, places=1)
        # data[8] = e = 1.23456e-3  (rinex.c:1028)
        self.assertAlmostEqual(e.e, 1.23456e-3, places=8)
        # data[11] = toe = 30240 s  (rinex.c:1036)
        self.assertAlmostEqual(e.toe_s, 30240.0, places=1)
        # data[21] = week = 2048  (rinex.c:1037)
        self.assertEqual(e.week, 2048)
        # D->E 转换生效（rinex.c:1172）
        self.assertNotEqual(e.A, 0.0)

    def test_parse_obs_line(self):
        out = ra.RINEXParser.parse_obs_line("G17  C1=20543892.123 L1=1075432.11 S1=45.2")
        self.assertEqual(out["sat"], "G17")
        self.assertAlmostEqual(out["C1"], 20543892.123, places=3)
        self.assertAlmostEqual(out["L1"], 1075432.11, places=2)
        self.assertAlmostEqual(out["S1"], 45.2, places=1)


class TestSPPLocator(unittest.TestCase):
    """SPP 单点定位 —— 来源 pntpos.c:250/253 残差与设计矩阵。

    合成无噪数据：已知真实接收机位置 rr_true + 钟差 b_true，
    选 6 颗分布良好的卫星，伪距 P_i = |rs_i-rr_true| + b_true。
    SPP 应无误差恢复 rr_true 与 b_true。
    """

    def test_spp_recovers_position(self):
        rr_true = np.array([4210000.0, 117000.0, 4780000.0])
        b_true = 300.0  # 接收机钟差等价距离(m) = 1us * c

        # 6 颗卫星，分布在 26560km 轨道半径上
        dirs = [np.array(d) for d in [
            (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
            (-0.6, 0.6, 0.6), (0.6, -0.6, 0.6), (0.6, 0.6, -0.6),
        ]]
        R = 26560000.0
        rs_list, dts_list, pr_list = [], [], []
        for d in dirs:
            d = d / np.linalg.norm(d)
            rs = d * R
            # 真值伪距用同一个 geodist（含 Sagnac，rtkcmn.c:3208）生成，保证自洽
            r, _ = ra.CoordinateConverter.geodist(rs, rr_true)
            rs_list.append(rs.tolist())
            dts_list.append(0.0)
            pr_list.append(r + b_true)   # 无噪伪距

        data = ra.SPPInput(rs=np.array(rs_list),
                           dts=np.array(dts_list),
                           pr=np.array(pr_list))
        sol = ra.SPPLocator().locate(data, x0=(0.0, 0.0, 0.0))

        # 位置误差应 < 1cm（无噪最小二乘，仅受浮点迭代影响）
        self.assertLess(abs(sol["x"] - rr_true[0]), 0.01, sol)
        self.assertLess(abs(sol["y"] - rr_true[1]), 0.01, sol)
        self.assertLess(abs(sol["z"] - rr_true[2]), 0.01, sol)
        self.assertLess(abs(sol["b_m"] - b_true), 0.01, sol)
        self.assertGreaterEqual(sol["n_sat"], 6)
        self.assertLess(sol["rms_m"], 0.01)

    def test_spp_via_module_helper(self):
        """模块级 spp_locate 便捷函数可调用并返回位置。"""
        rr_true = np.array([6378137.0 + 100.0, 0.0, 0.0])
        dirs = [np.array(d) for d in [
            (1.0, 0.3, 0.2), (0.2, 1.0, 0.3), (0.3, 0.2, 1.0),
            (-0.5, 0.6, 0.6), (0.6, -0.5, 0.6), (0.6, 0.6, -0.5),
        ]]
        R = 26560000.0
        rs_l, dts_l, pr_l = [], [], []
        for d in dirs:
            rs = d / np.linalg.norm(d) * R
            r, _ = ra.CoordinateConverter.geodist(rs, rr_true)
            rs_l.append(rs.tolist())
            dts_l.append(0.0)
            pr_l.append(r)
        out = ra.spp_locate(rs_l, dts_l, pr_l)
        self.assertLess(abs(out["x"] - rr_true[0]), 0.01)
        self.assertLess(abs(out["z"] - rr_true[2]), 0.01)


class TestNTRIPStream(unittest.TestCase):
    """NTRIP 请求构造 —— 来源 stream.c:1299-1312（不实际连网）。"""

    def test_request_format(self):
        s = ra.NTRIPStream("www.ntrip.example", 2101, "MOUNT1",
                           user="u", password="p")
        req = s.build_request().decode()
        self.assertTrue(req.startswith("GET /MOUNT1 HTTP/1.0\r\n"))
        self.assertIn("User-Agent: NTRIP RTKLIB/", req)
        self.assertIn("Authorization: Basic ", req)
        # base64("u:p")
        import base64
        self.assertIn(base64.b64encode(b"u:p").decode(), req)

    def test_no_auth_accept_close(self):
        s = ra.NTRIPStream("h", 2101, "M")
        req = s.build_request().decode()
        self.assertIn("Accept: */*", req)
        self.assertIn("Connection: close", req)


if __name__ == "__main__":
    unittest.main(verbosity=2)

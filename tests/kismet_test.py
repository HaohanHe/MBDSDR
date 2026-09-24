#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kismet_test.py — 验证 kismet_adapter.py 移植自 Kismet 真实源码的解析/跟踪逻辑。

覆盖（对应任务第二步要求）：
  1. 802.11 帧解析：合成信标帧 -> 解析出 SSID / 信道 / BSSID
  2. 设备跟踪：多个数据包 -> 设备列表正确更新（首次/最后时间、包计数、分类）
  3. RSSI 映射：已知原始值 -> dBm 正确
  4. 信息元素：SSID IE / DS-Param IE / 速率 IE 解析正确
  5. 参数验证：帧类型/子类型常量与 Kismet 源码逐位一致

所有断言旁标注来源 Kismet <file>:<line>。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai import kismet_adapter as K


class TestDot11FrameParse(unittest.TestCase):
    """合成信标帧 -> 解析出 SSID/信道/BSSID。

    来源: phy_80211_dissectors.cc:956-1003 (beacon 子类型 8 解包)
    """

    def setUp(self):
        # 合成一条占位信标帧（非真实网络）
        self.parser = K.Dot11Parser()
        self.frame = K.build_beacon_frame("02:00:00:00:00:01", b"TestNet", 6, 100)

    def test_frame_type_subtype(self):
        f = self.parser.parse(self.frame)
        # packet_ieee80211.h:46  management = 0
        self.assertEqual(f.type, K.DOT11_TYPE_MANAGEMENT)
        # packet_ieee80211.h:62  packet_sub_beacon = 8
        self.assertEqual(f.subtype, K.DOT11_SUB_BEACON)

    def test_bssid_dest_source(self):
        f = self.parser.parse(self.frame)
        # phy_80211_dissectors.cc:972-974
        #   dest=addr1(广播), source=addr2, bssid=addr3
        self.assertEqual(f.bssid, "02:00:00:00:00:01")
        self.assertEqual(f.source_mac, "02:00:00:00:00:01")
        self.assertEqual(f.dest_mac, "FF:FF:FF:FF:FF:FF")

    def test_ssid_channel(self):
        f = self.parser.parse(self.frame)
        # phy_80211_dissectors.cc:1679 SSID IE / :1881 DS-Param IE
        self.assertEqual(f.ssid, "TestNet")
        self.assertFalse(f.ssid_hidden)
        self.assertEqual(f.channel, 6)

    def test_beacon_interval(self):
        f = self.parser.parse(self.frame)
        # phy_80211_dissectors.cc:997  beacon_interval = letoh16(fixparm->beacon)
        self.assertEqual(f.beacon_interval_tu, 100)
        # phy_80211.cc:96  1 TU = 1024 us -> 100 TU = 102.4 ms
        self.assertAlmostEqual(
            f.beacon_interval_tu * K.DOT11_TU_USEC / 1000.0, 102.4, places=1)

    def test_header_offset(self):
        f = self.parser.parse(self.frame)
        # phy_80211_dissectors.cc:968  beacon header_offset = 24 + 12
        self.assertEqual(f.header_offset, 36)

    def test_sequence_control(self):
        f = self.parser.parse(self.frame)
        # packet_ieee80211.h:401-404  frag:4 | sequence:12
        self.assertEqual(f.fragment, 0)
        self.assertEqual(f.sequence, 0)


class TestInformationElements(unittest.TestCase):
    """信息元素解析。

    来源: dot11_parsers/dot11_ie.cc:82-92 (tag_num/len/data)
          phy_80211_dissectors.cc:1679-1882 (各 tag 解释)
    """

    def setUp(self):
        self.ie = K.Dot11IEParser()

    def test_ssid_ie(self):
        # SSID IE: tag=0, len=8, data="HelloNet"
        raw = bytes([0, 8]) + b"HelloNet"
        tags = self.ie.parse(raw)
        self.assertEqual(tags[0]["tag"], K.IE_TAG_SSID)
        ssid, hidden = self.ie.decode_ssid(tags[0]["data"])
        self.assertEqual(ssid, "HelloNet")
        self.assertFalse(hidden)

    def test_ssid_hidden(self):
        # phy_80211_dissectors.cc:1684-1691 空 SSID -> hidden
        raw = bytes([0, 0])
        tags = self.ie.parse(raw)
        ssid, hidden = self.ie.decode_ssid(tags[0]["data"])
        self.assertEqual(ssid, "")
        self.assertTrue(hidden)

    def test_ds_param_channel(self):
        # tag=3, len=1, data=[11] -> channel 11
        raw = bytes([3, 1, 11])
        m = self.ie.as_map(raw)
        self.assertEqual(self.ie.decode_channel(m[K.IE_TAG_DS_PARAM]), 11)

    def test_supported_rates(self):
        # phy_80211_dissectors.cc:1721-1860  rate = (byte&~0x80)/2 Mbps
        # 0x82 -> (0x02)/2 = 1.0 ; 0x8C -> (0x0C)/2 = 6.0 ; 0x6C -> (0x6C)/2=54.0
        rates = self.ie.decode_rates(bytes([0x82, 0x84, 0x8C, 0x6C]))
        self.assertEqual(rates, [1.0, 2.0, 6.0, 54.0])

    def test_bad_ssid_length(self):
        # packet_ieee80211.h:34  SSID_SIZE 32
        with self.assertRaises(ValueError):
            self.ie.decode_ssid(b"x" * 33)


class TestRSSIMapping(unittest.TestCase):
    """RSSI 映射。

    来源: kis_dlt_radiotap.cc:380,418  signal_dbm = (int8_t) raw
          kis_dlt_radiotap.cc:399-404  无符号比例换算
    """

    def setUp(self):
        self.m = K.RSSIMapper()

    def test_int8_negative(self):
        # radiotap 直接是 int8 dBm；raw 0xD3 = -45
        self.assertEqual(self.m.int8_to_dbm(0xD3), -45)

    def test_int8_positive(self):
        self.assertEqual(self.m.int8_to_dbm(0x2D), 45)

    def test_int8_clamp(self):
        # 300 截断到 255 -> int8 = -1；-5 截断到 0 -> 0
        self.assertEqual(self.m.int8_to_dbm(300), -1)
        self.assertEqual(self.m.int8_to_dbm(-5), 0)

    def test_unsigned_linear(self):
        # raw=255 -> max (-30dBm); raw=0 -> min (-95dBm)
        self.assertEqual(self.m.raw_unsigned_to_dbm(255), -30.0)
        self.assertEqual(self.m.raw_unsigned_to_dbm(0), -95.0)
        mid = self.m.raw_unsigned_to_dbm(128)
        self.assertTrue(-95.0 < mid < -30.0)


class TestDeviceTracker(unittest.TestCase):
    """设备跟踪：多个数据包 -> 设备列表正确更新。

    来源: devicetracker.cc:1152-1266 update_common_device()
    """

    def setUp(self):
        self.t = K.DeviceTracker()
        self.parser = K.Dot11Parser()

    def test_ap_discovery_first_last(self):
        # 同一条 AP 信标来两次
        fr = K.build_beacon_frame("02:00:00:00:00:0A", b"NetA", 6)
        f = self.parser.parse(fr)
        self.t.ingest_dot11(f, signal_dbm=-50, ts=1000.0)
        self.t.ingest_dot11(f, signal_dbm=-48, ts=1010.0)

        devs = self.t.device_list()
        self.assertEqual(len(devs), 1)
        d = devs[0]
        self.assertEqual(d["type"], "WiFi AP")      # phy_80211.cc:1256 source==bssid
        self.assertEqual(d["ssid"], "NetA")
        self.assertEqual(d["channel"], 6)
        # devicetracker.cc:1253 inc_packets
        self.assertEqual(d["packets"], 2)
        # devicetracker.cc:1189 first_time / :1250 last_time
        self.assertEqual(d["first_time"], 1000.0)
        self.assertEqual(d["last_time"], 1010.0)
        # devicetracker.cc:1323 append_signal 平均信号
        self.assertEqual(d["avg_signal_dbm"], -49.0)

    def test_client_probe(self):
        # 探测请求：手工构造（management subtype=4）
        hdr = bytearray()
        b0 = (K.DOT11_SUB_PROBE_REQ << 4) | (K.DOT11_TYPE_MANAGEMENT << 2)
        hdr += bytes([b0, 0x00])
        hdr += (0).to_bytes(2, "little")
        hdr += bytes([0xFF] * 6)                       # DA 广播
        hdr += bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x0B])  # SA
        hdr += bytes([0xFF] * 6)                       # BSSID 广播
        hdr += (0).to_bytes(2, "little")               # seq
        f = self.parser.parse(bytes(hdr))
        self.t.ingest_dot11(f, signal_dbm=-60, ts=2000.0)
        devs = self.t.device_list()
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["mac"], "02:00:00:00:00:0B")
        self.assertEqual(devs[0]["type"], "WiFi client")

    def test_multiple_devices(self):
        for i, ch in enumerate([1, 6, 11]):
            fr = K.build_beacon_frame(
                "02:00:00:00:00:%02X" % (0x10 + i), b"Net%d" % i, ch)
            self.t.ingest_dot11(self.parser.parse(fr), signal_dbm=-50, ts=float(i))
        devs = self.t.device_list()
        self.assertEqual(len(devs), 3)
        # 最后发现时间倒序（devicetracker.cc:1464-1465）
        self.assertEqual(devs[0]["ssid"], "Net2")

    def test_bluetooth(self):
        d = self.t.ingest_bluetooth("02:00:00:00:00:99", signal_dbm=-70, ts=1.0)
        self.assertEqual(d.dev_type, "BTLE")   # phy_bluetooth.cc:91
        self.assertEqual(d.phy, "bluetooth")


class TestDeviceFingerprint(unittest.TestCase):
    """设备指纹：OUI + 信号 + 时间特征。

    来源: manuf.cc:59-61 OUI = b0<<16|b1<<8|b2
          devicetracker.cc:1194 lookup_oui
    """

    def test_oui_extract(self):
        # 00:0C:29:AA:BB:CC -> 0x000C29
        oui = K.DeviceFingerprint.oui_of("00:0C:29:AA:BB:CC")
        self.assertEqual(oui, 0x000C29)

    def test_manufacturer_known(self):
        self.assertEqual(
            K.DeviceFingerprint.manufacturer("00:0C:29:AA:BB:CC"), "VMware")

    def test_manufacturer_unknown(self):
        self.assertEqual(
            K.DeviceFingerprint.manufacturer("06:06:06:AA:BB:CC"), "Unknown")

    def test_fingerprint_dict(self):
        fp = K.DeviceFingerprint.fingerprint(
            "00:0C:29:00:00:01", [-50, -48, -52], beacon_interval_tu=100)
        self.assertEqual(fp["oui"], "000C29")
        self.assertEqual(fp["manufacturer"], "VMware")
        self.assertEqual(fp["avg_signal_dbm"], -50.0)
        self.assertEqual(fp["max_signal_dbm"], -48.0)
        # 100 TU = 102.4 ms
        self.assertAlmostEqual(fp["beacon_interval_ms"], 102.4, places=1)


class TestConstants(unittest.TestCase):
    """帧类型/子类型常量必须与 Kismet 源码逐位一致。

    来源: packet_ieee80211.h:43-100
    """

    def test_frame_types(self):
        # packet_ieee80211.h:46-49
        self.assertEqual(K.DOT11_TYPE_MANAGEMENT, 0)
        self.assertEqual(K.DOT11_TYPE_PHY, 1)
        self.assertEqual(K.DOT11_TYPE_DATA, 2)
        self.assertEqual(K.DOT11_TYPE_EXTENSION, 3)

    def test_management_subtypes(self):
        # packet_ieee80211.h:56-69
        self.assertEqual(K.DOT11_SUB_PROBE_REQ, 4)
        self.assertEqual(K.DOT11_SUB_PROBE_RESP, 5)
        self.assertEqual(K.DOT11_SUB_BEACON, 8)
        self.assertEqual(K.DOT11_SUB_AUTH, 11)
        self.assertEqual(K.DOT11_SUB_DEAUTH, 12)
        self.assertEqual(K.DOT11_SUB_ACTION, 13)

    def test_control_subtypes(self):
        # packet_ieee80211.h:75-78
        self.assertEqual(K.DOT11_SUB_RTS, 11)
        self.assertEqual(K.DOT11_SUB_CTS, 12)
        self.assertEqual(K.DOT11_SUB_ACK, 13)

    def test_ssid_size(self):
        # packet_ieee80211.h:34  #define SSID_SIZE 32
        self.assertEqual(K.DOT11_PROTO_SSID_LEN, 32)

    def test_tu_usec(self):
        # phy_80211.cc:96  beacon interval * 1024 us
        self.assertEqual(K.DOT11_TU_USEC, 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)

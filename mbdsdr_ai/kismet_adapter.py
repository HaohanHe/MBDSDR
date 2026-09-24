#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kismet_adapter.py — Kismet 真实源码移植（802.11 帧解析 + 设备跟踪 + RSSI + 指纹）

本模块把 Kismet（https://github.com/kismetwireless/kismet）C++ 源码中与
无线设备发现直接相关的核心逻辑逐行移植为 Python，并在每一处常量与算法旁标注
「来源: Kismet <file>:<line>」。只移植真实工作的解析/跟踪骨架，不引入任何
真实网络/设备信息。

移植覆盖：
  - Dot11Parser        : 参考 phy_80211_dissectors.cc 帧解包 + packet_ieee80211.h 帧头
  - Dot11IEParser      : 参考 dot11_parsers/dot11_ie.cc 信息元素流
  - RSSIMapper         : 参考 kis_dlt_radiotap.cc 原始 RSSI -> dBm
  - DeviceFingerprint  : 参考 manuf.cc OUI + phy_80211.cc 信号/时间特征
  - DeviceTracker      : 参考 devicetracker.cc 设备生命周期

红线（与任务约定一致）：
  - 帧类型/子类型/位域布局必须与 C++ 源码逐位一致。
  - 不硬编码任何真实 MAC 地址或真实网络 SSID；所有用例均为合成/占位数据。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 802.11 帧常量 —— 逐字抄自 Kismet 源码，禁止改动数值
# ============================================================

# packet_ieee80211.h:43-50  enum ieee_80211_type
DOT11_TYPE_NOISE = -2      # packet_noise: 太短/损坏
DOT11_TYPE_UNKNOWN = -1     # packet_unknown
DOT11_TYPE_MANAGEMENT = 0   # packet_management
DOT11_TYPE_PHY = 1           # packet_phy (控制/物理层短帧)
DOT11_TYPE_DATA = 2          # packet_data
DOT11_TYPE_EXTENSION = 3    # packet_extension (s1g)

# packet_ieee80211.h:56-69  管理帧子类型
DOT11_SUB_ASSOC_REQ = 0       # packet_sub_association_req
DOT11_SUB_ASSOC_RESP = 1      # packet_sub_association_resp
DOT11_SUB_REASSOC_REQ = 2     # packet_sub_reassociation_req
DOT11_SUB_REASSOC_RESP = 3    # packet_sub_reassociation_resp
DOT11_SUB_PROBE_REQ = 4       # packet_sub_probe_req
DOT11_SUB_PROBE_RESP = 5      # packet_sub_probe_resp
DOT11_SUB_BEACON = 8          # packet_sub_beacon
DOT11_SUB_ATIM = 9            # packet_sub_atim
DOT11_SUB_DISASSOC = 10       # packet_sub_disassociation
DOT11_SUB_AUTH = 11           # packet_sub_authentication
DOT11_SUB_DEAUTH = 12          # packet_sub_deauthentication
DOT11_SUB_ACTION = 13          # packet_sub_action

# packet_ieee80211.h:71-80  控制帧(phy)子类型
DOT11_SUB_PSPOLL = 10        # packet_sub_pspoll
DOT11_SUB_RTS = 11            # packet_sub_rts
DOT11_SUB_CTS = 12            # packet_sub_cts
DOT11_SUB_ACK = 13            # packet_sub_ack

# packet_ieee80211.h:83-96  数据帧子类型
DOT11_SUB_DATA = 0            # packet_sub_data
DOT11_SUB_DATA_NULL = 4       # packet_sub_data_null
DOT11_SUB_QOS_DATA = 8        # packet_sub_data_qos_data

# packet_ieee80211.h:34   #define SSID_SIZE 32
DOT11_PROTO_SSID_LEN = 32

# phy_80211.cc:96  interval_per_sec = in_interval * 1024 / 1e6
# 信标间隔单位 TU（Time Unit）= 1024 微秒；默认 100 TU = 102.4 ms
DOT11_TU_USEC = 1024

# phy_80211_dissectors.cc:588  最短有效 802.11 帧（控制帧头）
DOT11_MIN_LEN = 10
# phy_80211_dissectors.cc:777  含完整地址头的最小长度（addr1-3 + seq）
DOT11_MIN_FULL_LEN = 24
# phy_80211_dissectors.cc:957  beacon/probe_resp 至少 36 字节（24 头 + 12 固定参数）
DOT11_MIN_MGT_FIXED_LEN = 36

# MAC 长度
PHY80211_MAC_LEN = 6  # phy_80211_dissectors.cc 各处 mac_addr(..., PHY80211_MAC_LEN)


# ============================================================
# 信息元素（IE）标签号 —— 来自 Kismet IE 解析 switch
# ============================================================
# phy_80211_dissectors.cc:1679   case 0:  SSID
IE_TAG_SSID = 0
# phy_80211_dissectors.cc:1707   case 1:  Supported Rates
IE_TAG_SUPP_RATES = 1
# phy_80211_dissectors.cc:1864   case 3:  DS Parameter Set（当前信道）
IE_TAG_DS_PARAM = 3
# phy_80211_dissectors.cc:1883   case 7:  Country / 802.11d
IE_TAG_COUNTRY = 7
# phy_80211_dissectors.cc:1708   case 50: Extended Supported Rates
IE_TAG_EXT_RATES = 50
# phy_80211_dissectors.cc:1951   case 45: HT Capabilities
IE_TAG_HT_CAP = 45


# ------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------
def _mac_str(b: bytes) -> str:
    """6 字节 MAC -> 'AA:BB:CC:DD:EE:FF'。对应 mac_addr::mac_to_string。"""
    return ":".join("%02X" % x for x in b)


def _le16(data: bytes, off: int) -> int:
    """小端 16 位读取。对应 phy_80211_dissectors.cc:636-637 的 duration。"""
    return data[off] | (data[off + 1] << 8)


class Dot11IEParser:
    """802.11 信息元素流解析器。

    来源: dot11_parsers/dot11_ie.cc:82-92
        m_tag_num   = read_u1();
        m_tag_len   = read_u1();
        m_tag_data  = read_bytes(tag_len());
    每个 IE = 1 字节标签号 + 1 字节长度 + N 字节数据。
    """

    def parse(self, data: bytes) -> List[Dict[str, Any]]:
        """把 IE 流解析成 [{tag, len, data(bytes)}]，越界即停。

        对应 dot11_ie.cc:67-79  while(!eof){ tag.parse(...); map[num]=&tag; }
        """
        tags: List[Dict[str, Any]] = []
        off = 0
        n = len(data)
        while off + 2 <= n:
            tag_num = data[off]
            tag_len = data[off + 1]
            off += 2
            if off + tag_len > n:
                break
            tag_data = data[off:off + tag_len]
            off += tag_len
            tags.append({"tag": tag_num, "len": tag_len, "data": tag_data})
        return tags

    def as_map(self, data: bytes) -> Dict[int, bytes]:
        """标签号 -> 最后出现的数据字节串。

        对应 dot11_ie.h:53-55  m_tags_map[tag_num] = &tag（后者覆盖前者）。
        """
        m: Dict[int, bytes] = {}
        for t in self.parse(data):
            m[t["tag"]] = t["data"]
        return m

    def decode_ssid(self, ie_data: bytes) -> Tuple[str, bool]:
        """解析 SSID IE（tag 0）。

        来源: phy_80211_dissectors.cc:1679-1705
            ssid_len = data.length();
            if (ssid_len == 0) ssid_blank = true;
            else if (全部为 '\\0') ssid_blank = true;
            else ssid = printable(data);
        返回 (ssid字符串, 是否隐藏/空)。
        """
        if len(ie_data) == 0:
            return "", True
        if len(ie_data) > DOT11_PROTO_SSID_LEN:
            # phy_80211_dissectors.cc:1695-1702 超长 SSID 视为异常
            raise ValueError("SSID longer than %d bytes (possible malformed frame)"
                             % DOT11_PROTO_SSID_LEN)
        if ie_data.find(b"\x00") == 0 and ie_data == b"\x00" * len(ie_data):
            return "", True
        try:
            s = ie_data.decode("utf-8", errors="replace")
        except Exception:
            s = ie_data.decode("latin-1", errors="replace")
        # phy_80211_dissectors.cc:1693 munge_to_printable：不可打印替换掉
        s = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in s)
        return s, False

    def decode_channel(self, ie_data: bytes) -> int:
        """解析 DS Parameter Set IE（tag 3）-> 当前信道号。

        来源: phy_80211_dissectors.cc:1881
            packinfo->channel = fmt::format("{}", (uint8_t) tag_data[0]);
        """
        if len(ie_data) < 1:
            raise ValueError("DS parameter IE needs >=1 byte")
        return ie_data[0]

    def decode_rates(self, *chunks: bytes) -> List[float]:
        """解析 Supported Rates(1)/Extended Rates(50) -> Mbps 列表。

        来源: phy_80211_dissectors.cc:1721-1860
            每字节速率 = (byte & ~0x80) / 2  Mbps；bit7(0x80) 是 basic 标志。
        （radiotap 速率同步用 kis_dlt_radiotap.cc:374: (u8 & ~0x80)/2*10 = 500kbps 单位）
        """
        rates: List[float] = []
        for chunk in chunks:
            for b in chunk:
                raw = b & 0x7F          # 去掉 basic 标志位
                rates.append(raw / 2.0)  # 0.5 Mbps 单位
        return rates


@dataclass
class Dot11Frame:
    """解析后的 802.11 帧。字段对应 dot11_packinfo。"""
    version: int
    type: int
    subtype: int
    to_ds: bool
    from_ds: bool
    wep: bool
    duration: int
    addr1: str = ""   # RA/TX 方向（Kismet addr0）
    addr2: str = ""   # TA/SA（Kismet addr1）
    addr3: str = ""   # BSSID/DA（Kismet addr2）
    addr4: str = ""   # 可选（WDS，Kismet addr3）
    sequence: int = 0
    fragment: int = 0
    # 管理帧载荷
    bssid: str = ""
    source_mac: str = ""
    dest_mac: str = ""
    ssid: str = ""
    ssid_hidden: bool = False
    channel: int = 0
    beacon_interval_tu: int = 0
    rates_mbps: List[float] = field(default_factory=list)
    ies: Dict[int, bytes] = field(default_factory=dict)
    header_offset: int = 0
    corrupt: bool = False


class Dot11Parser:
    """802.11 帧解析器。

    来源: phy_80211_dissectors.cc:567  packet_dot11_dissector()

    帧布局（小端，phy_80211_dissectors.cc:609-789）：
        [0:2]   frame_control  (frame_control 位域)
        [2:4]   duration_id
        [4:10]  addr1  (Kismet addr0)
        [10:16] addr2  (Kismet addr1)
        [16:22] addr3  (Kismet addr2)
        [22:24] sequence_control (frag:4 | seq:12)
        [24:30] addr4  (Kismet addr3, 仅 WDS)
    """

    def __init__(self) -> None:
        self.ie = Dot11IEParser()

    def _frame_control(self, data: bytes) -> Tuple[int, int, int, bool, bool, bool]:
        """解 frame_control 两个字节。

        来源: packet_ieee80211.h:385-399 (little-endian frame_control 位域)
            byte0: version:2 | type:2 | subtype:4
            byte1: to_ds:1 | from_ds:1 | morefrag:1 | retry:1 |
                   powermgmt:1 | moredata:1 | wep:1 | order:1
        """
        b0 = data[0]
        b1 = data[1]
        version = b0 & 0x03
        ftype = (b0 >> 2) & 0x03
        subtype = (b0 >> 4) & 0x0F
        to_ds = bool(b1 & 0x01)
        from_ds = bool(b1 & 0x02)
        wep = bool(b1 & 0x40)
        return version, ftype, subtype, to_ds, from_ds, wep

    def parse(self, frame: bytes) -> Dot11Frame:
        """解析一整条原始 802.11 帧（无 radiotap 头）。"""
        # phy_80211_dissectors.cc:588  太短直接丢弃
        if len(frame) < DOT11_MIN_LEN:
            f = Dot11Frame(0, DOT11_TYPE_NOISE, -1, False, False, False, 0)
            f.corrupt = True
            return f

        version, ftype, subtype, to_ds, from_ds, wep = self._frame_control(frame)

        # phy_80211_dissectors.cc:636-637  duration（wire 小端）
        duration = _le16(frame, 2)

        f = Dot11Frame(version, ftype, subtype, to_ds, from_ds, wep, duration)

        # phy_80211_dissectors.cc:643  addr1 = data[4:10]
        a1 = frame[4:10]
        if len(a1) == PHY80211_MAC_LEN:
            f.addr1 = _mac_str(a1)

        if len(frame) < DOT11_MIN_FULL_LEN:
            # 控制短帧（PS-Poll/RTS/CTS/ACK）在 phy_80211_dissectors.cc:666-772 处理
            if len(frame) >= 16:
                a2 = frame[10:16]
                if len(a2) == PHY80211_MAC_LEN:
                    f.addr2 = _mac_str(a2)
            f.corrupt = False
            return f

        # phy_80211_dissectors.cc:784-789
        a2 = frame[10:16]
        a3 = frame[16:22]
        if len(a2) == PHY80211_MAC_LEN:
            f.addr2 = _mac_str(a2)
        if len(a3) == PHY80211_MAC_LEN:
            f.addr3 = _mac_str(a3)

        # phy_80211_dissectors.cc:786,791-792  sequence_control: frag:4 | seq:12
        seqraw = _le16(frame, 22)
        f.fragment = seqraw & 0x000F          # packet_ieee80211.h:402  frag:4
        f.sequence = (seqraw >> 4) & 0x0FFF    # packet_ieee80211.h:403  sequence:12

        a4 = frame[24:30]
        if len(a4) == PHY80211_MAC_LEN:
            f.addr4 = _mac_str(a4)

        # 管理帧：地址语义固定为 da=addr1, sa=addr2, bssid=addr3
        # phy_80211_dissectors.cc:972-974 (beacon) / 920-922 (probe)
        if ftype == DOT11_TYPE_MANAGEMENT:
            f.dest_mac = f.addr1
            f.source_mac = f.addr2
            f.bssid = f.addr3
            self._parse_management(frame, f)
        elif ftype == DOT11_TYPE_DATA:
            # 数据帧地址语义随 to_ds/from_ds（phy_80211_dissectors.cc:656-663）
            f.source_mac, f.dest_mac, f.bssid = self._data_addresses(
                f.addr1, f.addr2, f.addr3, f.addr4, to_ds, from_ds)

        return f

    @staticmethod
    def _data_addresses(a1: str, a2: str, a3: str, a4: str,
                        to_ds: bool, from_ds: bool) -> Tuple[str, str, str]:
        """根据 to_ds/from_ds 推导 SA/DA/BSSID。

        来源: phy_80211_dissectors.cc:656-663 分布方向
            to_ds=0,from_ds=0 : adhoc   DA=a1 SA=a2 BSSID=a3
            to_ds=0,from_ds=1 : from-AP DA=a3 SA=a2 BSSID=a1
            to_ds=1,from_ds=0 : to-AP   DA=a1  SA=a3 BSSID=a2
            to_ds=1,from_ds=1 : inter   DA=a4  SA=a3 BSSID=a1 (WDS)
        """
        if not to_ds and not from_ds:
            return a2, a1, a3
        elif not to_ds and from_ds:
            return a2, a3, a1
        elif to_ds and not from_ds:
            return a3, a1, a2
        else:
            return a3, a4, a1

    def _parse_management(self, frame: bytes, f: Dot11Frame) -> None:
        """解析管理帧固定参数 + IE 载荷。

        来源: phy_80211_dissectors.cc:821-1003
        """
        if f.subtype == DOT11_SUB_BEACON or f.subtype == DOT11_SUB_PROBE_RESP:
            # phy_80211_dissectors.cc:957,967-968  至少 36 字节；固定参数 12 字节
            if len(frame) < DOT11_MIN_MGT_FIXED_LEN:
                f.corrupt = True
                return
            # fixed_parameters: timestamp(8) + beacon_interval(2) + capability(2) = 12
            # packet_ieee80211.h:406-423
            # phy_80211_dissectors.cc:997  beacon_interval = letoh16(fixparm->beacon)
            f.beacon_interval_tu = _le16(frame, 24 + 8)
            f.header_offset = 24 + 12   # line 968
        elif f.subtype == DOT11_SUB_PROBE_REQ:
            # phy_80211_dissectors.cc:917  probe_req 无固定参数
            f.header_offset = 24
        elif f.subtype in (DOT11_SUB_AUTH, DOT11_SUB_DEAUTH, DOT11_SUB_DISASSOC):
            f.header_offset = 24        # line 1033/1062/1081
        else:
            f.header_offset = 24

        if f.header_offset >= len(frame):
            return

        # phy_80211_dissectors.cc:1575-1581  从 header_offset 起解析 IE 流
        ie_stream = frame[f.header_offset:]
        f.ies = self.ie.as_map(ie_stream)

        if IE_TAG_SSID in f.ies:
            try:
                f.ssid, f.ssid_hidden = self.ie.decode_ssid(f.ies[IE_TAG_SSID])
            except ValueError:
                f.corrupt = True
        if IE_TAG_DS_PARAM in f.ies:
            try:
                f.channel = self.ie.decode_channel(f.ies[IE_TAG_DS_PARAM])
            except ValueError:
                pass
        supp = f.ies.get(IE_TAG_SUPP_RATES, b"")
        ext = f.ies.get(IE_TAG_EXT_RATES, b"")
        if supp or ext:
            f.rates_mbps = self.ie.decode_rates(supp, ext)


# ============================================================
# RSSI 映射
# ============================================================
class RSSIMapper:
    """原始信号强度 -> dBm。

    来源: kis_dlt_radiotap.cc:380-418
        radiotap IEEE80211_RADIOTAP_DBM_ANTSIGNAL 字段是 int8_t，单位即 dBm：
            record_signal = u.i8;
            ...
            signal_info.signal_dbm = record_signal;
    即：常见抓包源给出的"原始 RSSI"在 radiotap 里已经是有符号 8 位 dBm（负值，
    约 -30 ~ -95 dBm）。本类同时提供把无符号 0..255 原始计数线性映射到
    典型 dBm 区间的后备路径（对应 OpenBSD 分支 kis_dlt_radiotap.cc:399-404）。
    """

    # 典型 2.4GHz/Wi-Fi 接收动态范围
    DEFAULT_MAX_DBM = -30   # 极近点强信号
    DEFAULT_MIN_DBM = -95   # 灵敏度边缘

    @staticmethod
    def int8_to_dbm(raw: int) -> int:
        """把 0..255 的无符号字节按有符号 int8 解释为 dBm。

        对应 kis_dlt_radiotap.cc:380  record_signal = (int8_t) u.u8。
        """
        if raw < 0:
            raw = 0
        if raw > 255:
            raw = 255
        if raw >= 128:
            raw -= 256
        return raw

    def raw_unsigned_to_dbm(self, raw: int,
                            max_dbm: int = DEFAULT_MAX_DBM,
                            min_dbm: int = DEFAULT_MIN_DBM) -> float:
        """把 0..255 无符号计数线性映射到 [min_dbm, max_dbm]。

        对应 kis_dlt_radiotap.cc:403 的比例换算思路（rssi/total*range）。
        注意：raw 越大信号越强；dBm 越接近 0 越强。
        """
        if raw < 0:
            raw = 0
        if raw > 255:
            raw = 255
        frac = raw / 255.0
        return min_dbm + frac * (max_dbm - min_dbm)


# ============================================================
# 设备指纹
# ============================================================
class DeviceFingerprint:
    """设备指纹：OUI 厂商 + 信号特征 + 时间特征。

    来源:
      - manuf.cc:59-61   OUI = (b0<<16)|(b1<<8)|b2，devicetracker.cc:1194 lookup_oui
      - devicetracker.cc:1323(approx) append_signal：信号历史
      - phy_80211.cc:96  信标间隔(时间特征)
    """

    # 常见 IEEE OUI 前缀示例（仅作演示占位，非真实特定网络）
    KNOWN_OUI = {
        0x000C29: "VMware",
        0x005056: "VMware",
        0x001A11: "Google",
        0x0017C8: "Samsung",
    }

    @staticmethod
    def oui_of(mac: str) -> int:
        """从 'AA:BB:CC:..' 取 24 位 OUI。

        来源: manuf.cc:59-61
            oui = si[0]<<16 | si[1]<<8 | si[2];
        """
        parts = mac.split(":")
        if len(parts) != 6:
            raise ValueError("bad mac: %s" % mac)
        b = [int(p, 16) for p in parts]
        return (b[0] << 16) | (b[1] << 8) | b[2]

    @classmethod
    def manufacturer(cls, mac: str) -> str:
        """OUI -> 厂商名（查不到返回 'Unknown'）。

        来源: devicetracker.cc:1194  manufdb->lookup_oui(in_mac)
        """
        oui = cls.oui_of(mac)
        return cls.KNOWN_OUI.get(oui, "Unknown")

    @classmethod
    def fingerprint(cls, mac: str, signal_history: List[float],
                    beacon_interval_tu: int = 0) -> Dict[str, Any]:
        """组合指纹字典。"""
        avg_sig = sum(signal_history) / len(signal_history) if signal_history else 0.0
        return {
            "mac": mac,
            "oui": "%06X" % cls.oui_of(mac),
            "manufacturer": cls.manufacturer(mac),
            "avg_signal_dbm": round(avg_sig, 1),
            "max_signal_dbm": max(signal_history) if signal_history else 0.0,
            "beacon_interval_ms": round(
                beacon_interval_tu * DOT11_TU_USEC / 1000.0, 1),  # phy_80211.cc:96
        }


# ============================================================
# 设备跟踪
# ============================================================
@dataclass
class TrackedDevice:
    """跟踪中的设备。对应 kis_tracked_device_base。"""
    key: str                 # (phy, mac)  —— devicetracker.cc:1169 device_key
    mac: str
    phy: str
    dev_type: str = "unknown"   # phy_bluetooth.cc:91 BTLE / phy_80211 等
    first_time: float = 0.0
    last_time: float = 0.0
    packets: int = 0
    tx_packets: int = 0
    rx_packets: int = 0
    channel: int = 0
    ssid: str = ""
    ssid_hidden: bool = False
    bssid: str = ""
    signal_history: List[float] = field(default_factory=list)

    def avg_signal(self) -> float:
        if not self.signal_history:
            return 0.0
        return sum(self.signal_history) / len(self.signal_history)


class DeviceTracker:
    """802.11 / 蓝牙设备跟踪器。

    来源: devicetracker.cc:1152  device_tracker::update_common_device()
      - line 1169  key = (phyname_hash, mac)
      - line 1189  新设备 set_first_time(packet.ts)
      - line 1250  set_if_lt_last_time(ts)
      - line 1253  inc_packets()
      - line 1256-1262 source/transmitter==mac -> tx；dest==mac -> rx
      - line 1194  manufdb->lookup_oui
    """

    def __init__(self, phy_name: str = "IEEE802.11") -> None:
        self.phy_name = phy_name
        self.devices: Dict[str, TrackedDevice] = {}

    def _key(self, mac: str) -> str:
        # devicetracker.cc:1169  device_key(phyname_hash, mac)
        return "%s/%s" % (self.phy_name, mac.upper())

    def ingest_dot11(self, frame: Dot11Frame, signal_dbm: Optional[int] = None,
                     ts: Optional[float] = None) -> List[TrackedDevice]:
        """从一条解析后的 802.11 帧更新设备表。返回本帧涉及的设备列表。"""
        now = ts if ts is not None else time.time()
        touched: List[TrackedDevice] = []

        # 决定本帧涉及的 MAC 角色
        # phy_80211.cc:1256/1305  source==bssid -> AP
        if frame.type == DOT11_TYPE_MANAGEMENT and frame.subtype in (
                DOT11_SUB_BEACON, DOT11_SUB_PROBE_RESP):
            # AP：信标/探测响应的源即 BSSID
            ap = self._observe(frame.bssid or frame.source_mac, now, signal_dbm)
            ap.dev_type = "WiFi AP"
            if frame.ssid:
                ap.ssid = frame.ssid
                ap.ssid_hidden = frame.ssid_hidden
            if frame.channel:
                ap.channel = frame.channel
            touched.append(ap)
            return touched

        if frame.type == DOT11_TYPE_MANAGEMENT and frame.subtype == DOT11_SUB_PROBE_REQ:
            # 探测请求：源是客户端
            client = self._observe(frame.source_mac, now, signal_dbm)
            client.dev_type = "WiFi client"
            touched.append(client)
            return touched

        # 通用：跟踪源（发射）与目的
        if frame.source_mac:
            src = self._observe(frame.source_mac, now, signal_dbm)
            src.packets += 0  # inc_packets 在 _observe 内做
            touched.append(src)
        if frame.dest_mac and frame.dest_mac != "FF:FF:FF:FF:FF:FF":
            dst = self._observe(frame.dest_mac, now, signal_dbm)
            touched.append(dst)
        return touched

    def ingest_bluetooth(self, mac: str, signal_dbm: Optional[int] = None,
                         ts: Optional[float] = None,
                         btle: bool = True) -> TrackedDevice:
        """登记一个蓝牙设备。

        来源: phy_bluetooth.cc:184,327 update_common_device(btaddr_mac, btphy,...)
              phy_bluetooth.cc:91  btdev_btle = "BTLE"
        """
        now = ts if ts is not None else time.time()
        dev = self._observe(mac, now, signal_dbm, phy="bluetooth")
        dev.dev_type = "BTLE" if btle else "Bluetooth"
        return dev

    def _observe(self, mac: str, now: float, signal_dbm: Optional[int],
                 phy: Optional[str] = None) -> TrackedDevice:
        ph = phy or self.phy_name
        key = "%s/%s" % (ph, mac.upper())
        dev = self.devices.get(key)
        if dev is None:
            # devicetracker.cc:1171-1201 新设备
            dev = TrackedDevice(key=key, mac=mac.upper(), phy=ph)
            dev.first_time = now          # line 1189 set_first_time
            dev.last_time = now
            self.devices[key] = dev
        # devicetracker.cc:1250 set_if_lt_last_time
        if now > dev.last_time:
            dev.last_time = now
        # devicetracker.cc:1253 inc_packets
        dev.packets += 1
        if signal_dbm is not None:
            dev.signal_history.append(float(signal_dbm))
            # 只保留最近 32 个样本（类似 RRD 环形缓冲 trackedrrd.h）
            if len(dev.signal_history) > 32:
                dev.signal_history.pop(0)
        return dev

    def device_list(self) -> List[Dict[str, Any]]:
        """导出设备列表（按最后发现时间倒序）。"""
        out: List[Dict[str, Any]] = []
        for d in self.devices.values():
            out.append({
                "mac": d.mac,
                "phy": d.phy,
                "type": d.dev_type,
                "ssid": d.ssid,
                "ssid_hidden": d.ssid_hidden,
                "channel": d.channel,
                "packets": d.packets,
                "tx_packets": d.tx_packets,
                "rx_packets": d.rx_packets,
                "first_time": d.first_time,
                "last_time": d.last_time,
                "avg_signal_dbm": round(d.avg_signal(), 1),
                "manufacturer": DeviceFingerprint.manufacturer(d.mac),
            })
        out.sort(key=lambda x: x["last_time"], reverse=True)
        return out


# ------------------------------------------------------------
# 便捷合成工具：构造一条信标帧（仅用于自测，不含真实网络）
# ------------------------------------------------------------
def build_beacon_frame(bssid: str, ssid: bytes, channel: int,
                       beacon_interval_tu: int = 100) -> bytes:
    """合成一条最小合法 802.11 信标帧（无 FCS）。

    布局对齐 phy_80211_dissectors.cc:956-1003：
      fc(2) + dur(2) + DA(6,广播) + SA/BSSID(6) + BSSID(6) + seq(2)
      + timestamp(8) + beacon_interval(2) + capability(2) + IEs
    """
    # frame control 位域(packet_ieee80211.h:386-388): version:2|type:2|subtype:4
    # beacon: subtype=8,type=0,version=0 -> b0 = (8<<4)|0|0 = 0x80
    b0 = (DOT11_SUB_BEACON << 4) | (DOT11_TYPE_MANAGEMENT << 2) | 0
    b1 = 0x00
    hdr = bytearray()
    hdr += bytes([b0, b1])
    hdr += (0).to_bytes(2, "little")          # duration
    hdr += bytes([0xFF] * 6)                   # addr1 = 广播 DA
    hdr += _unmac(bssid)                       # addr2 = SA
    hdr += _unmac(bssid)                       # addr3 = BSSID
    hdr += (0).to_bytes(2, "little")           # seq control
    # fixed parameters
    hdr += (0).to_bytes(8, "little")           # timestamp
    hdr += beacon_interval_tu.to_bytes(2, "little")  # beacon interval (TU)
    hdr += (0x0001).to_bytes(2, "little")     # capability: ESS
    # IEs: SSID(0), SupportedRates(1), DSParam(3)
    ies = bytearray()
    ies += bytes([IE_TAG_SSID, len(ssid)]) + ssid
    ies += bytes([IE_TAG_SUPP_RATES, 4, 0x82, 0x84, 0x8B, 0x96])  # 1/2/5.5/11 basic
    ies += bytes([IE_TAG_DS_PARAM, 1, channel])
    return bytes(hdr + ies)


def _unmac(s: str) -> bytes:
    return bytes(int(x, 16) for x in s.split(":"))

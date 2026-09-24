# Kismet 真实 802.11 设备发现移植笔记

> 本笔记记录把 Kismet（最强的开源无线网络/设备发现工具）真实 C++ 源码移植到 MBDSDR 的过程与结论。
> 移植产物：`mbdsdr_ai/kismet_adapter.py`（纯 Python，逐行对照），验证：`tests/kismet_test.py`。

## 1. 为什么要真读源码

任务红线：**必须读 .cc/.h，不能只看 README**。Kismet 的设备发现核心是一条
"抓包 → 链路层解包 → 物理层 dissector → IE 解析 → 设备跟踪器"的流水线。
本移植只取其中与无线设备发现直接相关、可离线合成验证的部分。

## 2. 真读了哪些文件

| 文件 | 作用 | 关键内容 |
|---|---|---|
| `packet_ieee80211.h` | 帧头/帧类型定义 | `frame_control` 位域(h:385-399)、类型枚举(h:43-50)、子类型枚举(h:53-100)、`fixed_parameters`(h:406-423) |
| `phy_80211_dissectors.cc` | 802.11 帧解包 | `packet_dot11_dissector`(cc:567)：地址偏移(cc:643-789)、管理帧各子类型(cc:821-1136) |
| `phy_80211_dissectors.cc` | IE 解释 | `packet_dot11_ie_dissector`(cc:1612)：SSID(tag0,cc:1679)、速率(tag1/50,cc:1707)、信道(tag3,cc:1864) |
| `dot11_parsers/dot11_ie.cc` | IE 流解析 | `dot11_ie_tag::parse`(cc:82-92)：tag_num/len/data |
| `kis_dlt_radiotap.cc` | 信号 | `DBM_ANTSIGNAL`=int8 dBm(cc:380,418) |
| `devicetracker.cc` | 设备跟踪 | `update_common_device`(cc:1152)：key(cc:1169)/首末时间(cc:1189,1250)/包计数(cc:1253)/tx-rx(cc:1256) |
| `manuf.cc` | OUI 厂商 | `oui = b0<<16\|b1<<8\|b2`(cc:59-61) |
| `phy_bluetooth.cc` | 蓝牙 | `btdev_btle="BTLE"`(cc:91) |

> 注：本版 Kismet 的帧解析不在 `dot11/` 目录，而在 `phy_80211_dissectors.cc` +
> `dot11_parsers/dot11_ie.*`；任务书里写的 `packagetracker.cc` 对应本版的 `packetchain.cc`。

## 3. 802.11 帧布局（来源 phy_80211_dissectors.cc:609-789）

```
偏移   长度  字段
0-1    2    frame_control (frame_control 位域, packet_ieee80211.h:385-399)
2-3    2    duration (小端, cc:636-637)
4-9    6    Address1 = RA/DA   (Kismet addr0)
10-15  6    Address2 = TA/SA   (Kismet addr1)
16-21  6    Address3 = BSSID/DA (Kismet addr2)
22-23  2    sequence_control: frag:4 | sequence:12 (h:401-404)
24-29  6    Address4 (仅 WDS, Kismet addr3)
```

`frame_control` 字节（小端位域，h:386-398）：
- byte0 = `version:2 | type:2 | subtype:4`
- byte1 = `to_ds:1 | from_ds:1 | morefrag:1 | retry:1 | powermgmt:1 | moredata:1 | wep:1 | order:1`

## 4. 关键常量（来源 packet_ieee80211.h，已逐行注释进代码）

| 常量 | 值 | 含义 |
|---|---|---|
| `DOT11_TYPE_MANAGEMENT` | 0 | 管理帧 h:46 |
| `DOT11_TYPE_PHY` | 1 | 控制短帧 h:47 |
| `DOT11_TYPE_DATA` | 2 | 数据帧 h:48 |
| `DOT11_SUB_BEACON` | 8 | 信标 h:62 |
| `DOT11_SUB_PROBE_REQ/RESP` | 4 / 5 | 探测请求/响应 h:60-61 |
| `DOT11_SUB_AUTH/DEAUTH` | 11 / 12 | 认证/解除认证 h:65-66 |
| `SSID_SIZE` | 32 | SSID 最大长度 h:34 |
| `1 TU` | 1024 µs | 信标间隔单位（phy_80211.cc:96） |

## 5. 管理帧地址语义与固定参数

- 信标/探测响应/认证等管理帧（cc:972-974）：`DA=Addr1, SA=Addr2, BSSID=Addr3`。
- 信标固定参数（h:406-423）：`timestamp(8) + beacon_interval(2) + capability(2) = 12 字节`，
  IE 流从 `24 + 12 = 36` 字节开始（cc:968）。
- `beacon_interval = letoh16(fixparm->beacon)`（cc:997），单位 TU；100 TU = 102.4 ms。
- 数据帧地址语义随 to_ds/from_ds（cc:656-663）：adhoc/from-AP/to-AP/inter-WDS。

## 6. 信息元素（IE，dot11_ie.cc:82-92）

每个 IE = `tag(1B) + len(1B) + data(len B)`。关键 tag：

| tag | 名称 | 解释（cc 行号） |
|---|---|---|
| 0 | SSID | len==0 或全 0 → 隐藏(cc:1684-1691) |
| 1 / 50 | Supported / Extended Rates | `(byte & ~0x80)/2` Mbps，bit7=basic(cc:1721) |
| 3 | DS Parameter Set | `data[0]` = 当前信道号(cc:1881) |
| 7 | Country | 802.11d 国家码(cc:1883) |

## 7. RSSI 与设备指纹

- radiotap `DBM_ANTSIGNAL` 字段本就是 int8 dBm（kis_dlt_radiotap.cc:380），
  Kismet 直接存 `signal_dbm = (int8_t)raw`（cc:418）。`RSSIMapper.int8_to_dbm` 即此。
- 无符号 0..255 原始计数则线性映射到典型 dBm 区间（cc:399-404 的比例思路）。
- 设备指纹 = OUI（manuf.cc:59-61）+ 信号历史均值/极值 + 信标间隔（时间特征）。

## 8. 设备生命周期（devicetracker.cc:1152 update_common_device）

```
key = (phy, mac)                       cc:1169
新设备 -> set_first_time(pkt.ts)        cc:1189
每包   -> set_if_lt_last_time(ts)      cc:1250
        -> inc_packets()                cc:1253
source/transmitter==mac -> inc_tx       cc:1256
dest==mac               -> inc_rx       cc:1261
AP 判定: 信标/probe_resp 的 source==bssid cc:1305
```

## 9. 验证结论（tests/kismet_test.py，28 项全过）

- **帧解析**：合成信标帧 → type=0/subtype=8、BSSID、SSID="TestNet"、信道 6、
  信标间隔 100 TU、header_offset=36、速率 [1,2,5.5,11] Mbps 全对。
- **设备跟踪**：同 AP 两包 → packets=2、first=1000/last=1010、平均信号 -49 dBm、
  类型 "WiFi AP"；探测请求 → "WiFi client"；多设备按最后时间倒序。
- **RSSI**：0xD3→-45 dBm、255→-1、线性边界 ±30/-95 正确。
- **IE**：SSID/隐藏/DS-Param 信道/速率解码正确；>32 字节 SSID 抛错。
- **常量**：帧类型/子类型/SSID_SIZE=32/TU=1024 与源码逐位一致。

## 10. ToolRegistry 注册

`kismet_parse_dot11` / `kismet_discover_devices` / `kismet_device_list`
已注册（category=wireless）。跟踪器为进程内单例，跨调用保持状态。

## 11. 边界

- 只移植了裸 802.11（无 radiotap 头）的解析；真实 SDR/抓包源需先剥离 radiotap。
- 蓝牙仅按 phy_bluetooth.cc 登记为 "BTLE"，未移植 LE 广播字段细节。
- 不硬编码任何真实 MAC/SSID；测试数据均为合成占位。

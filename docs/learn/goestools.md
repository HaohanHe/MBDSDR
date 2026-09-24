# goestools：GOES HRIT/LRIT 接收解码套件真读笔记

> 本笔记对应提交：`feat(sat): goestools真实GOES LRIT/HRIT帧解析+虚拟信道重组+图像提取，验证通过`
> 原则：**只读 README 不算数，必须读 .cc/.h 源码**。所有常量在代码里以
> `来源: goestools src/<file>:<行号>` 标注。源码已 clone 到 `repos/goestools/`。

---

## 一、goestools 是什么

[goestools](https://github.com/pietern/goestools)（Pieter Noordhuis）是 GOES-R 系列
气象卫星（GOES-16/17/18/19）HRIT/LRIT 信号接收与解码的完整开源套件。本仓库只移植
**数字后端**：从 Viterbi/R-S 译码后的字节流一路重组到 LRIT 文件与图像段。

目录结构（真读）：

| 目录 | 作用 |
|---|---|
| `src/decoder/` | 位同步、Viterbi、解扰、Reed-Solomon、组帧（packetizer） |
| `src/assembler/` | VCDU 解复用 → CCSDS TP_PDU → SessionPDU（一个文件） |
| `src/lrit/` | LRIT 文件头定义与解析（Primary/ImageStructure/Annotation/SegmentId/Rice…） |
| `src/goeslrit/` | 命令行工具：把 VCDU 流传成 .lrit 文件 |
| `src/goesproc/` | 后处理：图像投影、调色、NWS/EMWIN 文本 |
| `src/goesrecv/` | SDR 前端（rtl-sdr/airspy → 软符号） |

---

## 二、数据流（物理层 → 文件）

```
软符号
  → Viterbi (K=7, r=1/2)                       decoder/viterbi.h
  → 帧同步字 0x1ACFFC1D 相关峰                   decoder/compute_sync_words.cc:37-40
  → 1024 B/帧 = 4 B 同步字 + 1020 B 数据        decoder/packetizer.h:14-26
  → 解扰 (PN: x^8+x^7+x^5+x^3+1, init=0xFF)    decoder/derandomizer.cc:9-30
  → Reed-Solomon (255,223) → 892 B VCDU        assembler/vcdu.h:9
  → 按 VCID 解复用 + M_PDU 第一头指针            assembler/virtual_channel.cc:44
  → CCSDS TP_PDU (APID/seqFlag/CRC16)          assembler/transport_pdu.h:33-68
  → 按 APID 重组 SessionPDU = 一个 LRIT 文件     assembler/session_pdu.cc:73-220
  → LRIT 文件头 → 图像段 / 文本 / DCS            lrit/lrit.h:16-149
```

本移植（`mbdsdr_ai/goes_lrit.py`）不实现 Viterbi / Reed-Solomon（CPU/优化密集），
从 **892 字节 VCDU** 开始向上重组；同时提供同步字搜索与解扰表，可对 1020 B 净荷离线处理。

---

## 三、关键常量（全部真读自源码）

| 常量 | 值 | 来源 |
|---|---|---|
| 传输帧同步字 | **0x1ACFFC1D** | `decoder/compute_sync_words.cc:37-40` |
| 一帧长度 | **1024 B** = 8192 bit | `decoder/packetizer.h:14` |
| 同步字后净荷 | **1020 B** = 1024−4 | `decoder/packetizer.cc:178` |
| RS 译码后 VCDU | **892 B** | `assembler/vcdu.h:9` |
| VCDU 头长度 | 6 B（version/SCID/VCID/counter） | `assembler/vcdu.h:34-38` |
| VCDU 数据长度 | 886 B（M_PDU） | `assembler/vcdu.h:37-39` |
| 填充 VCID | **63** | `assembler/assembler.cc:13` |
| M_PDU 第一头指针"无包" | **2047** | `assembler/virtual_channel.cc:95` |
| 填充 APID | **2047** | `assembler/virtual_channel.cc:118` |
| TP_PDU 主头 | 6 B CCSDS | `assembler/transport_pdu.h:14` |
| LRIT 符号率 | 293883 baud | `decoder/packetizer.cc:123` |
| HRIT 符号率 | 927000 baud | `decoder/packetizer.cc:121` |
| 解扰多项式 | x⁸+x⁷+x⁵+x³+1, LFSR 初值 0xFF | `decoder/derandomizer.cc:9-30` |
| CRC-16 初值 | 0xFFFF（CCITT 表） | `assembler/crc.cc:47` |

### VCDU 位域（`vcdu.h:17-31`）

```
byte0: [version(2) | SCID_high(6)]
byte1: [SCID_low(2) | VCID(6)]
byte2..4: VCDU counter (24-bit, big-endian, mod 2^24)
byte5:   保留
byte6..: M_PDU (886 B)
```

### M_PDU → TP_PDU（`virtual_channel.cc:44-107`）

```
fhp = ((data[0] & 0x7) << 8) | data[1]   # 第一头指针，指向 M_PDU[2:] 内偏移
# 跳过 data[0:2]，从 mpdu[fhp] 起切 CCSDS 源包
# fhp==2047 表示本 VCDU 没有新包起始
```

### CCSDS TP_PDU 主头（`transport_pdu.h:33-68`）

```
byte0: [version(3) | type(1) | secHdr(1) | APID_high(3)]
byte1: [APID_low(8)]                     # APID 共 11 bit
byte2: [seqFlag(2) | seqCount_high(6)]
byte3: [seqCount_low(8)]                 # seqCount 14 bit, mod 16384
byte4..5: packet_length (BE) = 后续字节数 − 1
```

- **seqFlag**：3=整包一个 TP_PDU；1=首段；0=续段；2=末段
  （`virtual_channel.cc:161-168` 注释原文）。
- 每个 TP_PDU 末尾 **2 字节 CRC-16/CCITT**（`crc.cc` 查表，初值 0xFFFF）。
- 第一个 TP_PDU 的用户数据前 **10 字节是垃圾**，要跳过
  （`session_pdu.cc:78-82` 注释："First 10 bytes of the first TP_PDU appears to be garbage"）。

---

## 四、LRIT 文件头（`lrit/lrit.h:16-149`）

一个 SessionPDU 收齐后就是一个完整 LRIT 文件。开头是一连串二级头，每条记录：
`type(1B) + length(2B BE) + payload`（`lrit.cc:91-102`）。

| type | 名称 | 关键字段 | 来源 |
|---|---|---|---|
| 0 | PrimaryHeader | fileType, totalHeaderLength(4B BE), dataLength(8B BE)；固定 16B | `lrit.cc:164-172` |
| 1 | ImageStructureHeader | bitsPerPixel, columns, lines, compression | `lrit.cc:174-183` |
| 2 | ImageNavigationHeader | 投影名 32B, 经纬度缩放/偏移 | `lrit.cc:185-204` |
| 4 | AnnotationHeader | 文件名文本 | `lrit.cc:214-220` |
| 5 | TimeStampHeader | CCSDS 7B 时间（1958 起算，偏移 4383 天到 Unix） | `lrit.cc:38-52` |
| 128 | SegmentIdentificationHeader | imageId, segment#, startCol/Line, maxSegment/Col/Line | `lrit.cc:238-250` |
| 129 | NOAALRITHeader | productID/subID（GOES-R 图像 productID=16..19） | `lrit.cc:252-262` |
| 131 | RiceCompressionHeader | flags, pixelsPerBlock, scanLinesPerPacket | `lrit.cc:272-280` |

- **fileType**：0=图像，1=文本消息，2=产品/EMWIN，130=DCS
  （`goeslrit.cc:26-41`）。
- **compression==1** 表示 Rice 压缩，必须读 type=131 头拿参数
  （`session_pdu.cc:139-160`）。
- 图像按段下发：同一张图的所有段 `imageIdentifier` 相同，段号 1..maxSegment，
  每段用 `segmentStartColumn/Line` 定位到整图画布（`goeslrit.cc:55-64`）。

---

## 五、Rice 压缩（骨架）

goestools 真实解码依赖 NASA **szlib**（`session_pdu.h:7-9` `#include <szlib.h>`）：

```c
SZ_com_t p;
p.options_mask        = rch.flags | SZ_RAW_OPTION_MASK;
p.bits_per_pixel      = ish.bitsPerPixel;
p.pixels_per_block    = rch.pixelsPerBlock;
p.pixels_per_scanline = ish.columns;
SZ_BufftoBuffDecompress(out, &outLen, in, inLen, &p);   // session_pdu.cc:211
```

本移植 `RiceDecoder` 保留参数解析与未压缩直通路径；完整 Rice 解码需链接 szlib。

---

## 六、本仓库移植对照

| goestools 概念 | Python 类（`mbdsdr_ai/goes_lrit.py`） |
|---|---|
| VCDU | `VCDU.parse()` |
| M_PDU 切包 + 丢包检测 | `_VirtualChannel.process()` |
| CCSDS TP_PDU | `TransportPDU.parse_header()` |
| SessionPDU 按 APID 重组 | `_SessionPDU.append()` |
| LRIT 头遍历 | `parse_lrit_headers()` |
| 解扰 PN 表 | `derandomize()` / `DERANDOM_TABLE` |
| CRC-16/CCITT | `crc16_ccitt()` |
| 同步字搜索 | `LRITParser.find_sync()` |
| 图像段拼接 | `GOESImageDecoder.add_lrit_file()` |
| Rice 参数 | `RiceDecoder`（骨架） |

注册到 ToolRegistry 的工具：

- `goes_lrit_parse`：喂 892B VCDU hex 列表 → 重组 LRIT 文件并解析头
- `goes_hrit_parse`：同协议栈，符号率 927kbaud
- `goes_extract_image`：多段 LRIT 图像 → 整幅灰度图

测试：`tests/goes_lrit_test.py`（18 用例，覆盖同步字/VCDU 字段/跨 VCDU 重组/
跨 TP_PDU 重组/CRC 校验失败丢弃/段拼接/IR 灰度映射）。

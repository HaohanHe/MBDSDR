# 气象卫星接收管道参数交叉核对（GK-2A / FY-4 / GOES / METEOR / NOAA）

> 本笔记对 SatDump、goestools、noaa-apt、meteor_demod（meteord）、gr-satellites 等开源实现的物理层与传输层参数做逐文件交叉核对。
> 所有取值均标注 `项目名 文件路径:行号`。仓库根为 `repos/`。
> 许可证红线：上述项目均为 GPL-3.0 系，引用代码片段仅作学习标注，不做再分发。

---

## 0. 一句话结论

除 NOAA APT 与 METEOR LRPT 走 VHF/FM 或 QPSK 外，**GK-2A / GOES-R / FY-4 / ELEKTRO 的 LRIT/HRIT 下行在物理层本质上都是同一套 CCSDS 串行链路**：
BPSK（或 OQPSK/QPSK）+ 速率 1/2、K=7 卷积码（Viterbi）+ RS(255,223)、交织深度 I=4 + 255 字节 PN 加扰 + 32-bit ASM `0x1ACFFC1D`。
各项目的差异主要在**符号率、频点、同步字在 Viterbi 前还是后相关、以及 xRIT 文件头解析**上。

---

## 1. 物理层参数对比表

### 1.1 Geo 系列（L 波段 SSPA 下行，CCSDS 串行链路）

| 卫星/链路 | 下行频率 | 调制 | 符号率 | 滚降 α | FEC | 同步字位置 | 来源 |
|---|---|---|---|---|---|---|---|
| GK-2A LRIT | 1692.14 MHz | BPSK | 128 ksym/s | 0.5 | 1/2 K=7 + RS(255,223) I=4 | Viterbi 后 32-bit ASM | SatDump `resources/pipelines/GK2A.json:22,32` |
| GK-2A HRIT | 1695.4 MHz | QPSK | 3.0 Msym/s | 0.5 | 同上 | 同上 | `GK2A.json:129,139` |
| GK-2A UHRIT | 8070 MHz | DVB-S2 | 15.62244 Msym/s | 0.25 | DVB-S2 modcod=13, pilots | DVB-S2 SOF | `GK2A.json:174,182` |
| GOES-R HRIT | 1694.1 MHz | BPSK | 927 ksym/s | 0.5 | 1/2 K=7 + RS(255,223) I=4, NRZ-M | Viterbi 前 64-bit 编码同步字 | SatDump `GOES.json:67,117`；goestools `correlator.cc:15` |
| GOES-N LRIT | 1691.0 MHz | BPSK | 293 ksym/s（规范值 293 883） | 0.5 | 同上，NRZ-M 关闭 | Viterbi 后 ASM | SatDump `GOES.json:371,381`；goestools `config.cc:448` |
| GOES-R GRB | 1686.6 MHz | DVB-S2 | 8.665938 Msym/s | 0.25 | DVB-S2 modcod=11, mt_bch | DVB-S2 SOF | `GOES.json:219,228` |
| FY-4A LRIT | 1697.0 MHz | DVB-S2 | 90 ksym/s | 0.25 | DVB-S2 modcod=3，BB 25728 字节，PID 3004 | DVB-S2 SOF（被禁用锁定） | SatDump `FengYun-4.json:22,38,49` |
| FY-4B LRIT | 1697.0 MHz | DVB-S2 | 120 ksym/s | 0.25 | modcod=10，BB 57472 字节，PID 3000 | 同上 | `FengYun-4.json:78,94,105` |
| FY-4A HRIT-II/III | 1679.0 MHz | DVB-S2 | 1.0 Msym/s | 0.25 | modcod=9，BB 53840，PID 3002 | 同上 | `FengYun-4.json:135,151,161` |
| ELEKTRO-L LRIT | 1691.0 MHz | BPSK | 294 ksym/s | 0.5 | 1/2 K=7 + RS(255,223) I=4 | Viterbi 后 ASM | SatDump `Elektro_Arktika.json`（elektro_lrit 段） |
| ELEKTRO-L HRIT | 1691.0 MHz | OQPSK | 1.15 Msym/s | 0.5 | 同上 | 同上 | 同上（elektro_hrit 段） |

### 1.2 LEO 系列（NOAA / METEOR）

| 卫星/链路 | 下行频率 | 调制 | 符号率/比特率 | 滚降 α | FEC | 来源 |
|---|---|---|---|---|---|---|
| NOAA-15 APT | 137.620 MHz | FM（副载波 2400 Hz AM） | 音频 50 kHz（每像素行 2080 px） | — | 无纠错（模拟传真） | SatDump `NOAA.json`（noaa_apt 段）；noaa-apt `src/decode.rs:38` |
| NOAA-18 APT | 137.9125 MHz | FM | 同上 | — | 无 | `NOAA.json` noaa_apt 段 |
| NOAA-19 APT | 137.100 MHz | FM | 同上 | — | 无 | 同上 |
| NOAA-15/18/19 HRPT | 1702.5 / 1707 / 1698 MHz | FM-PM | 665.4 ksym/s | 0.6 | 无（裸帧 + TIP 奇偶校验） | SatDump `NOAA.json` noaa_hrpt 段 |
| METEOR-M2 LRPT | 137.1 / 137.9 MHz | QPSK（M2-2 起 OQPSK） | 72 ksym/s（=144 kbps） | **0.5（SatDump）/ 0.6（meteord）** | 无内码，Viterbi 关闭，差分可选 | SatDump `Meteor-M.json:90,92`；meteor_demod `src/main.c:19`、README |

### 1.3 关键交叉核对发现

1. **GOES-R HRIT 同步字的“假象不一致”**：
   - SatDump 在 Viterbi **之后**用 32-bit ASM `0x1ACFFC1D` 做帧同步（`module_ccsds_conv_concat_decoder.cpp:90`）。
   - goestools 在 Viterbi **之前**直接对卷积编码软比特流相关，用 64-bit 字 `0x035d49c24ff2686b`（及其反码 `0xfca2b63db00d9794`）（`correlator.cc:12-13`）。
   - **二者并不矛盾**：64-bit 字正是 32-bit ASM `0x1ACFFC1D` 经速率 1/2 卷积编码器后的输出。goestools 把“Viterbi 译码 + ASM 相关”合并成一步在编码域完成，节省一次全帧 Viterbi。这是两种等价工程路线，不是参数冲突。

2. **Viterbi 生成多项式的位序差异**：
   - SatDump：`CCSDS_R2_K7_POLYS = {79, 109}`（`viterbi27.h:8`）。
   - goestools：`poly[2] = {0x4f, 0x6d}`（`viterbi.h:27`）= {79, 109}，K=7。
   - 标准 CCSDS / LRIT 文档写法是八进制 G1=171, G2=133（见 NOAA LRIT MSD：G1=1111001, G2=1011011）。
   - **换算关系**：{79,109} 是 {171o,133o} 的位反转表示（79=0b1001111，反转后 0b1111001=121=171o）。两个项目用同一多项式，只是 LSB/MSB 约定相反，**交叉核对一致**。

3. **加扰多项式**：
   - goestools 显式注释并实现 `h(x) = x^8 + x^7 + x^5 + x^3 + 1`，初值 `lfsr=0xff`，生成 255 字节表（`derandomizer.cc:11,20-33`）。
   - SatDump 直接硬编码 255 字节 PN 表 `ccsds_pn[255]`，首字节 `0xFF 0x48 0x0E 0xC0 0x9A ...`（`randomization.cpp:4`），加扰从 ASM 之后第 4 字节开始（`module_ccsds_conv_concat_decoder.cpp` 中 `d_derand_from` 默认 4）。
   - 两份表首字节均为 `0xFF`，按 CCSDS 101.0-B 一致；goestools 的注释多项式是该 LFSR 的工程近似写法，**运行结果一致**。

4. **METEOR 滚降系数不一致（真正需要注意的点）**：
   - SatDump pipeline 用 `rrc_alpha=0.5`（`Meteor-M.json:92`）。
   - meteor_demod（meteord）默认 `RRC_ALPHA=0.6`（`src/main.c:25` 注释“alpha taken from the .grc meteor decode script”，README 默认 `-a 0.6`）。
   - **原因分析**：早期 GNU Radio meteor-m2-lrpt.grc 模板用 0.6，meteord 沿用；SatDump 后来统一改成更常见的 0.5。实际接收时 α 偏 0.1 不会失锁（RRC 匹配裕度大），但级联根升余弦时收发 α 应一致，否则眼图张开度下降。建议以卫星制造方文档（0.5）为准，0.6 仅为历史模板遗留。

5. **GOES-N LRIT 符号率精度**：SatDump 写 `293e3`，实际规范为 **293 883 sym/s**（见 NOAA LRIT MSD 与多篇 GNU Radio 教程）。SatDump 取整到 293 ksym/s，靠符号同步环吸收约 0.3% 偏差，可工作但非精确值。

---

## 2. GK-2A 专项

### 2.1 专用 xRIT 头结构

SatDump 在 `plugins/xrit_support/xrit/gk2a/gk2a_headers.h` 定义了两个 Mission-Specific 头：

```cpp
// gk2a_headers.h:24  KeyHeader, TYPE = 7
struct KeyHeader {            // 解密密钥索引（HRIT 加密时）
    uint8_t type;             // =7
    uint16_t record_length;
    uint32_t key;             // data[3..6]
};

// gk2a_headers.h:38  ImageSegmentationIdentification, TYPE = 128
struct ImageSegmentationIdentification {
    uint8_t type;             // =128
    uint16_t record_length;
    uint8_t image_seq_nb;
    uint8_t total_segments_nb;
    uint16_t line_nb;
};
```

对照通用 xRIT 头（`xrit_file.h`）：TYPE 0=PrimaryHeader、1=ImageStructureRecord、2=ImageNavigationRecord、3=ImageDataFunctionRecord、4=AnnotationRecord、5=TimeStampRecord。GK-2A 复用 7（Key）并自定义 128（段标识）。

### 2.2 压缩方式：不是 Rice，是 JPEG / JPEG2000

> ⚠️ 任务书里写“Rice 压缩参数”，但核对源码后**GK-2A 并不使用 Rice**。这是一个需要纠正的前提。

`plugins/xrit_support/xrit/gk2a/decomp.cpp:24-40` 根据 `ImageStructureRecord.compression_flag` 判断：

| compression_flag | 含义 | 解码路径 |
|---|---|---|
| 2 | Progressive JPEG（LRIT 图） | `decompress_jpeg()` / `decompress_jpeg12()`（>8 bit 时） |
| 1 | Wavelet / JPEG2000（HRIT/UHRIT） | `decompress_j2k_openjp2()`，UHRIT 需跳过 85 字节偏移（`decomp.cpp:46`） |
| 0 | 无压缩（raw） | 不解压 |

位深处理：>8 bit 的 JPEG12 解出后左移 2 位（`img.set(c, img.get(c)<<2)`，`decomp.cpp:38`）；J2K 解出后按 `16 - bit_per_pixel` 左移（`decomp.cpp:49`）。

### 2.3 图像分辨率/通道

GK-2A AMI 全圆盘 4 通道（VI00.5km、WV10.4km、SW3.9km 等），分段数默认 10（`segment_decoder.h:42`：`total_segments_nb : 10`）。段宽/段高直接取自 `ImageStructureRecord.columns_count / lines_count`，解码器按 `line_offset` 拼接（`segment_decoder.h:55` `imemcpy(image, seg_width*pos, ...)`）。

### 2.4 与 goestools GOES 实现的异同

| 维度 | GK-2A (SatDump) | GOES-R (goestools + SatDump) |
|---|---|---|
| 物理层 | BPSK/QPSK + 1/2 K=7 + RS I=4 | **完全相同** |
| ASM | `0x1ACFFC1D`（Viterbi 后） | goestools 用编码域 64-bit，等价 |
| 图像压缩 | **JPEG / JPEG2000**（compression_flag 1/2） | **Rice**（`RiceCompressionHeader`, CODE=131） |
| 段标识头 | TYPE=128 `ImageSegmentationIdentification` | TYPE=128 `SegmentIdentificationHeader`（字段更全：imageId/segmentNum/startCol/startLine/maxSeg/maxCol/maxLine，goestools `src/lrit/lrit.h:100`） |
| 专用压缩头 | 无（走通用 ImageStructureRecord） | TYPE=131 Rice：`pixelsPerBlock`、`scanLinesPerPacket`（`lrit.h:133-141`） |
| 加密 | KeyHeader TYPE=7 | 无（GOES-R HRIT 不加密） |

**结论**：GK-2A 与 GOES-R 在 CCSDS 传输层同源，但在图像编码层分道扬镳——GK-2A 用成熟的 JPEG/J2K，GOES-R ABI 用 NOAA 自定义的 Rice（LOCO 类）压缩。

---

## 3. FY-4 专项

SatDump 在 `plugins/xrit_support/xrit/fy4/fy4_headers.h` 定义了 FY-4 专用头：

```cpp
// fy4_headers.h:23  ImageInformationRecord, TYPE = 1
struct ImageInformationRecord {
    uint8_t type;                 // =1（注意：覆盖了通用的 ImageStructureRecord）
    std::string satellite_name;   // data[3..11] 9 字节
    std::string instrument_name;  // data[12..18] 7 字节
    uint8_t bit_per_pixel;        // data[19]
    uint16_t columns_count;       // data[20..21]
    uint16_t lines_count;         // data[22..23]
    uint8_t compression_flag;     // data[24]
    uint8_t channel_number;       // data[25]
    uint8_t total_segment_count;  // data[26]
    uint8_t current_segment_number;  // data[27]
    uint8_t compressed_info_algo / _lossless / _level;  // data[31] 打包
};

// fy4_headers.h:57  ImageNavigationRecord, TYPE = 2
// projection_name[32], 随后 4 个 IEEE-754 float（column/line scaling + offset），小端逐字节拼接
```

**与 GK-2A/GOES 的关键差异**：
1. FY-4 **复用 TYPE=1/2** 但语义完全重定义（不是通用 ImageStructureRecord），SatDump 里专门存了一份 `image_navigation_record_fy4`（见 `identify.h:46`）。
2. FY-4 LRIT 走 **DVB-S2 物理层**（`dvbs2_demod`），不是 GK-2A/GOES 的串行 CCSDS+BPSK。modcod、BBFRAME 长度、TS PID 都不同：FY-4A LRIT modcod=3/BB 25728/PID 3004；FY-4B modcod=10/BB 57472/PID 3000（`FengYun-4.json:38-49,94-105`）。
3. FY-4 也有 KeyHeader TYPE=7（加密），字段布局与 GK-2A 相同（`fy4_headers.h:15`）。

---

## 4. 帧结构对比

### 4.1 VCDU / CADU

- **CADU**（Channel Access Data Unit）= ASM(4 字节) + VCDU。SatDump 通用 CADU 8192 bit = 1024 字节（`GK2A.json`/`GOES.json` 中 `cadu_size: 8192`）。
- **VCDU 解析**（`src-core/common/ccsds/ccsds_aos/vcdu.cpp`）：
  - `version = cadu[4]>>6`，`spacecraft_id = (cadu[4]&0x3f)<<2 | cadu[5]>>6`，`vcid = cadu[5]&0x3f`，`vcdu_counter = cadu[6..8]`。
- goestools 侧 VCDU = `std::array<uint8_t,892>`（`vcdu.h:11`），即 RS(255,223)×4 交织译码后的净荷 = 4×223 = 892 字节；VCDU 头占 6 字节，MPDU 域 = 886 字节，再扣 2 字节 first-header-pointer 得 **MPDU 净荷 884 字节**。
- SatDump `XRITDemux` 默认 `mpdu_size=884`（`xrit_demux.h:48`），**与 goestools 完全一致**。

### 4.2 TP_PDU / CCSDS Packet

- VCDU 净荷剥出 MPDU → CCSDS Space Packet（primary header 6 字节：version/type/secondary-header-flag/APID 11 bit、sequence flags 2 bit、packet sequence count 14 bit、data length 16 bit）。
- xRIT 数据按 **APID 分文件**：`wip_files[vcdu.vcid][pkt.header.apid]`，一个 APID 对应一个 xRIT 文件（`xrit_demux.cpp:46`）。filler APID=2047 跳过。
- **CRC**：xRIT 包尾 2 字节 CRC-16，多项式查表 `0xFFFF` 初值（`xrit_demux.cpp:17` `computeCRC`）。goestools 侧 `TransportPDU::verifyCRC()`（`transport_pdu.cc:33`）同样校验包尾 CRC。
- sequence_flag==1（首包）或 3（单包）→ 开新文件；==0（中间）→ 续写；==2（尾包）→ finalize。

### 4.3 LRIT 文件头类型汇总

| TYPE | 名称 | SatDump | goestools |
|---|---|---|---|
| 0 | PrimaryHeader | `xrit_file.h:17` | — |
| 1 | ImageStructureRecord（通用）/ FY-4 ImageInformation | `xrit_file.h:38` / `fy4_headers.h:23` | ImageStructure |
| 2 | ImageNavigationRecord（通用）/ FY-4 Nav | `xrit_file.h:60` | ImageNavigation |
| 3 | ImageDataFunctionRecord | `xrit_file.h:89` | ImageDataFunction |
| 4 | AnnotationRecord | `xrit_file.h:105` | `lrit.h:91` CODE=4 |
| 5 | TimeStampRecord | `xrit_file.h:121` | CODE=5 |
| 6 | AncillaryTextRecord | — | `lrit.h:83` CODE=6 |
| 7 | KeyHeader（GK-2A/FY-4 加密） | `gk2a_headers.h:24` | — |
| 128 | SegmentIdentification | 通用 / GK-2A `gk2a_headers.h:38` | `lrit.h:100` CODE=128 |
| 129 | NOAA LRIT Header（压缩标志） | `goes_headers.h:24` | `lrit.h:114` CODE=129 |
| 130 | HeaderStructureRecord | — | `lrit.h:126` |
| 131 | RiceCompressionHeader | `goes_headers.h:48` `pixels_per_block=data[5]` | `lrit.h:133` `pixelsPerBlock` |
| 132 | DCSFileNameHeader | — | `lrit.h:144` |

---

## 5. 图像处理算法对比

### 5.1 SatDump processors

`plugins/firstparty_support/processors/` 下按数据格式分目录：`hrit/`（xRIT 拼图）、`hsd/`、`nat/`、`nc/`、`hdf/`。核心是分段图像拼接 + 几何投影（`xrit/processor/get_img.h`）。
- GK-2A 段拼接：`segment_decoder.h:55` `imemcpy` 按行偏移；JPEG/J2K 解压后按 `bit_per_pixel` 归一到 8/16 bit。
- 投影：基于 ImageNavigationRecord 的 column/line scaling 与 offset，把像素 (col,line) 反算到卫星视几何角，再映射到地图投影。

### 5.2 noaa-apt 图像处理

`src/decode.rs`：
- APT 行结构常量：`PX_SYNC_FRAME=39`、`PX_SPACE_DATA=47`、`PX_CHANNEL_IMAGE_DATA=909`、`PX_TELEMETRY_DATA=45`，合计 `PX_PER_CHANNEL=1040`，两行 `PX_PER_ROW=2080`（`decode.rs:23-37`）。
- 副载波 `CARRIER_FREQ=2400 Hz`（`decode.rs:38`），最终速率 `FINAL_RATE=4160`（=2×2080，每像素 2 采样）。
- 同步：靠 39 像素的 APT 帧同步码（alternating 1/0 方波）做行锁定； telemetry 楔块用于 6-bit 灰度自动拉伸（`telemetry.rs`）。
- 与 SatDump 的 SDR++ 降噪（`noaa_apt` pipeline 里 `sdrpp_noise_reduction=true`）相比，noaa-apt 更偏“先重采样+低通+DC 去除，再沿行同步”，不做频域降噪。

**对比结论**：SatDump 偏“多卫星统一流水线 + 几何投影”，noaa-apt 偏“单星 APT 模拟链精细 DSP（重采样/DC 去除/telemetry 拉伸）”，二者不构成竞争而是互补。

---

## 6. 参数速查表（大表）

| 参数 | GK-2A LRIT | GOES-R HRIT | GOES-N LRIT | FY-4A LRIT | METEOR M2 LRPT | NOAA APT |
|---|---|---|---|---|---|---|
| 频率 | 1692.14 MHz (`GK2A.json:22`) | 1694.1 MHz (`GOES.json:67`) | 1691.0 MHz (`GOES.json:371`) | 1697.0 MHz (`FY4.json:22`) | 137.1/137.9 MHz (`Meteor-M.json:53,57`) | 137.62/137.9125/137.1 MHz (`NOAA.json`) |
| 调制 | BPSK (`GK2A.json:30`) | BPSK (`GOES.json:115`) | BPSK (`GOES.json:379`) | DVB-S2 QPSK (`FY4.json:34`) | QPSK (`Meteor-M.json:88`) | FM→2400Hz AM (`decode.rs:38`) |
| 符号率 | 128 k (`GK2A.json:32`) | 927 k (`GOES.json:117`) | 293 k（规范 293883）(`GOES.json:381`) | 90 k (`FY4.json:38`) | 72 k (`Meteor-M.json:90`) | 音频 50 k (`NOAA.json`) |
| 滚降 α | 0.5 (`GK2A.json:34`) | 0.5 (`GOES.json:119`) | 0.5 (`GOES.json:383`) | 0.25 (`FY4.json:40`) | 0.5 SD / 0.6 meteord (`Meteor-M.json:92`, `main.c:25`) | — |
| 卷积码 | 1/2 K=7 {79,109} (`viterbi27.h:8`) | 同左 (`viterbi.h:27`) | 同左 | DVB-S2 modcod=3 | 无 | 无 |
| RS | RS(255,223) I=4 (`GK2A.json` rs_i=4) | 同左 | 同左 | DVB-S2 BCH/LDPC | 无 | 无 |
| 加扰 | 255B PN，初值 FF (`randomization.cpp:4`) | 同左 (`derandomizer.cc:11`) | 同左 | DVB-S2 BB 加扰 | 可选 diff_decode | 无 |
| 同步字 | `0x1ACFFC1D` (`module...cpp:90`) | 编码域 64-bit `0x035d49c2...` (`correlator.cc:12`) | `0x1ACFFC1D` | DVB-S2 SOF | `0x1ACFFC1D` | 39px 方波同步头 (`decode.rs:26`) |
| CADU/帧长 | 8192 bit (`GK2A.json`) | 8192 bit | 8192 bit | BB 25728B (`FY4.json:49`) | 软 QPSK 符号流 | 2080 px/行 (`decode.rs:36`) |
| 图像压缩 | JPEG/J2K (`decomp.cpp:24`) | Rice TYPE=131 (`lrit.h:133`) | Rice | 私有压缩 (`fy4_headers.h:46`) | raw BBP | raw 8-bit |
| MPDU | 884 B (`xrit_demux.h:48`) | 884 B (`vcdu.h:11` 892-6-2) | 884 B | TS PID 3004 | — | — |

---

## 7. 参考来源（外部）

- NOAA GOES LRIT Mission Specific Data：BPSK 293 ksym/s、K=7、G1=1111001/G2=1011011。["https://pietern.github.io/goestools/_downloads/1e7106b42c7bfd8f01ccd502841cb951/5_LRIT_Mission-data.pdf"]
- CCSDS ASM `0x1ACFFC1D` 与 293883 sym/s 解调流程。["https://lucasteske.dev/2016/11/goes-satellite-hunt-part-3-frame-decoder/","https://jimizhou.com/GOES-Satellite"]
- GK-2A LRIT：128 kbaud BPSK、α=0.5、1/2 卷积后 64 kbps。["https://www.sigidwiki.com/wiki/GK-2A_LRIT_(_Low-Rate_Image_Transmission_)"]
- METEOR-M2 LRPT：QPSK 72 ksym/s、32-bit ASM `0x1ACFFC1D`。["https://meteorm2.particlesector.com/technical/"]

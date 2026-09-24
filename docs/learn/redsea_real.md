# redsea 真实解码：从 C++ 源码到 `mbdsdr_ai/rds_lite.py` 的落地笔记

> 本地参考克隆：`repos/redsea/`（windytan/redsea，FM RDS 解码器）
> 配套实现：`mbdsdr_ai/rds_lite.py`
> 往返验证：`tests/rds_real_roundtrip.py`（15 项全过）
>
> 本文件只记录**这次真读 .cc/.hh 源码后逐行对齐到 Python 的常量与位域**，
> 每条都给 `file:line`。注意 redsea 源码树是扁平的（`src/*.cc`），**没有**
> `src/rds/` 子目录——任务描述里的 `src/rds/xxx.cc` 实际对应 `src/xxx.cc`。

---

## 1. 物理层

| 项 | 值 | 来源 |
|---|---|---|
| 副载波 | 57 kHz | `repos/redsea/src/dsp/subcarrier.cc:104` `oscillator.init(... angularFreq(57000,...))` |
| 比特率 | 1187.5 bit/s | `repos/redsea/src/constants.hh:23` `kBitsPerSecond` |
| 目标内部采样率 | 171 kHz | `repos/redsea/src/constants.hh:29` `kTargetSampleRate_Hz` |
| 每 PSK 符号样本数 | 3 | `repos/redsea/src/dsp/subcarrier.hh:79` `kSamplesPerSymbol` |
| 抽取比 | 24 | `repos/redsea/src/dsp/subcarrier.hh:80-81` |
| 低通截止 | 2400 Hz | `repos/redsea/src/dsp/subcarrier.cc:39` `kLowpassCutoff_Hz` |
| 编码 | BPSK + 双相(Manchester) + 差分解码 | `subcarrier.cc:50-93` `BiphaseDecoder` / `DeltaDecoder` |

要点：
- RDS 数据是 **Manchester(双相) 后再差分**：每个数据位 = 两个反相 PSK 符号；
  `biphase = (now - prev)*0.5`，再 `out = (in != prev)`（subcarrier.cc:55,90）。
- PSK 符号率 = 2 × 1187.5 = 2375 sym/s；组 = 4 × 26 bit = 104 bit，
  ≈ 11.428 组/秒（不是 104 组/秒；那是把“104 bit/组”误读成了“104 组/秒”）。

---

## 2. (26,16) 块码与偏移字

来源：`repos/redsea/src/block_sync.cc`。

- **块长 26 bit** = 16 bit 信息 + 10 bit 校验字（`block_sync.cc:38,40`）。
- redsea **不用 LFSR 长除法**求伴随式，而是写死一个 **26×10 校验矩阵 H**
  （`block_sync.cc:87-114`）。伴随式 = “输入位中为 1 的那些 H 行做 GF(2) 异或”
  （`block_sync.cc:121-127`）。
- 偏移字（10 bit，XOR 进校验字以区分块）——`block_sync.cc:138-144`：

| 块 | 偏移字 | 特征伴随式 |
|---|---|---|
| A | `0x0FC` (`0011111100`) | `0x3D8` (`1111011000`) |
| B | `0x198` (`0110011000`) | `0x3D4` (`1111010100`) |
| C | `0x168` (`0101101000`) | `0x25C` (`1001011100`) |
| C' | `0x350` (`1101010000`) | `0x3CC` (`1111001100`) |
| D | `0x1B4` (`0110110100`) | `0x258` (`1001011000`) |

  特征伴随式表见 `block_sync.cc:71-81`。
- 块循环后继：A→B→C→D→A（C' 后也接 D）（`block_sync.cc:57-68`）。
- 信息字 = `raw >> 10`（`block_sync.cc:289`）。

> **旧版 rds_lite.py 的坑**：旧代码用自造 LFSR（多项式 0x1B9）算出的伴随式表是
> A=`0x17F`、B=`0x00E`…——那只是“自己编码→自己解码自洽”，**真实 redsea 接收端
> 按 H 矩阵会期望 0x3D8…，根本同步不上**。本版改为逐字照搬 H 矩阵，并用逆映射表
> 求校验字，保证真 redsea 能收。多项式 `0x1B9` 是 g(x) 去最高位的低 10 位系数
> （EN 50067 B.1），与 H 矩阵等价，但判定必须用 H。

**真实测试向量**：
- `encode_block(0xDDEE,'A')` 后 `calculate_syndrome(raw) == 0x3D8` ✓
- 翻转任意 1 bit → 伴随式失配、该组被块同步丢弃 ✓

---

## 3. 组类型与公共字段

来源：`repos/redsea/src/group.cc:14-16`、`src/station.cc:217-251`。

Block B（16 bit）布局：
```
bit15..12  组号(0..15)
bit11      版本 (0=A, 1=B)
bit10      TP  交通节目
bit9..5    PTY 节目类型(5bit)
bit4..0    低5位（各组复用）
```
- 组类型 `getBits<5>(B,11)`：号=`(t>>1)&0xF`，版本=`t&1`（group.cc:15-16）。
- PI = Block A（group.cc:54）。
- PTY = `(B>>5)&0x1F`，TP = `(B>>10)&1`，TA = `(B>>4)&1`（station.cc:225,229,251）。

---

## 4. 各数据组

### 0A/0B 基本调谐（PS + AF）— `station.cc:244-345`
- **PS 段地址 = Block B 低 2 bit** `B & 0x3`（station.cc:248）——**不是** Block C！
- 每个 0A 组的 **Block D** 给 2 个 PS 字符（高字节先）（station.cc:333）。
- 4 段（段地址 0..3）拼成 8 字符电台名。
- AF 方法 A：Block C 两个字节 = 两个频率码（station.cc:263-264）。

### 2A/2B RadioText — `station.cc:418-481`
- 地址 = `B & 0xF`，A/B 版标志 = `(B>>4)&1`（station.cc:425,428）。
- 2A：缓冲 64 字符，每组 4 字符：Block C 高/低字节 + Block D 高/低字节，
  位置 = `addr*4 .. addr*4+3`。2B：缓冲 32 字符，位置 = `addr*2`。

### 3A 开放数据应用(ODA) — `station.cc:513-525`
- ODA 组号 = `B & 0x1F`，消息 = Block C，**应用 ID = Block D**。
  例：RT+ = `0x4BD7`，TMC = `0xCD46/CD47`。

### 4A 时钟时间(CT) — `station.cc:576-655`
- MJD(17bit) = `((B<<16 | C) >> 1) & 0x1FFFF`（station.cc:585）。
- 时(5bit) = `((C<<16 | D) >> 12) & 0x1F`（station.cc:606）。
- 分(6bit) = `(D>>6) & 0x3F`；本地偏移 = 符号`D[5]` × `D[4:0]` / 2 小时（station.cc:607-610）。
- MJD→日期用 redsea 截断常数（station.cc:593-604），已对 1858-11-17 历元校验：
  MJD 58119=2018-01-01，60310=2024-01-01，61307=2026-09-24。

### 10A PTY 名 — `station.cc:738-756`
段地址 = `B & 1`，每组 4 字符（Block C/D）。

### 14A EON — `station.cc:761-767`
其他网络 PI = Block D；其他网络 TP = `(B>>4)&1`。

---

## 5. 落地到 Python

- `rds_lite.calculate_syndrome()` 逐行照搬 H 矩阵；`encode_block()` 用预计算的
  “校验字→伴随式”逆表（已验证 1024 项双射）反推校验字。
- 块同步 `blocksync_from_bits()`：滑窗求伴随式定位块，按 A→B→C→D 节奏成组，
  C 位置接受 C'。
- 工具注册：`register_rds_tools(registry)` 注册 `rds_decode_mpx` /
  `rds_decode_groups` / `rds_extract_ps_rt`，在 `sdr_tools.register_sdr_tools` 末尾调用。

# 数字语音 / 集群无线电移植（sdrtrunk / op25 / M17）

> 源码来源：
>   - `repos/sdrtrunk/`（github.com/DSheirer/sdrtrunk）—— Java 多信道跟踪
>   - `repos/op25/`（github.com/boatbod/op25）—— P25 Phase1/2 解码
>   - `repos/m17/`（github.com/mobilinkd/m17-cxx-demod）—— M17 物理层
>
> 本任务逐行实读关键文件后移植到：
>   - `mbdsdr_ai/sdrtrunk_adapter.py`
>   - `mbdsdr_ai/op25_adapter.py`
>   - `mbdsdr_ai/m17_adapter.py`
>
> 验证见 `tests/digital_voice_roundtrip.py`（7 项全过）。

---

## 1. 三项目定位对照

| 项目 | 语言 | 职责 | 本移植关注点 |
|---|---|---|---|
| sdrtrunk | Java | 多信道跟踪（P25/CAP+/DMR/TETRA），控制信道解析、业务信道调度 | 信道状态机、通话组管理、grant/affiliation/group update 事件 |
| op25 | C++/Python | P25 Phase1/2 物理层解码（NID/同步/IMBE） | NID BCH、DUID 枚举、IMBE 144-bit 码书帧结构、Phase2 TDMA |
| M17 | C++ | 开源数字语音协议物理层 | LSF/Lich、CRC16(0x5935)、卷积码(K=5,g=0o31/0o27)、4FSK(4800sps,625Hz) |

---

## 2. M17 帧结构

### 2.1 物理层常量

| 参数 | 值 | 源码位置 |
|---|---|---|
| 符号率 | 4800 sps | `M17Modulator.h:94`（1920 baseband @ 48k / 10 upsample） |
| 频偏 | 625 Hz/级（+1875/+625/-625/-1875 Hz） | M17 spec §3；`M17Modulator.h:596-617` RRC 抽头 |
| 帧长 | 40 ms = 192 符号 = 384 bit | `M17Modulator.h:93`；`M17Framer.h:13` N=368 净荷 |
| 同步字 LSF | `0x55F7`（16 bit） | `M17Modulator.h:110` |
| 同步字 DATA | `0xFF5D` | `M17Modulator.h:111` |
| 同步字 STREAM | `0x3243` | `M17Modulator.h:109` |
| 加扰序列 DC | 46 字节（见 `M17Randomizer.h:16-22`） | `M17Randomizer.h:65-75` XOR 对称 |

### 2.2 帧层次

```
每帧 (40ms, 192 符号):
  ├── 同步字 16 bit (8 符号)  [0x55F7 for LSF, 0xFF5D for voice/data]
  └── 净荷 368 bit (184 符号)
        │
        LSF 帧 (一次性):
          30 字节 LSF
            = src_callsign(6) + dst_callsign(6) + type(2) + CRC16(2)
          -> 卷积码 (K=5, r=1/2) -> 60+1 字节
          -> P1 删余 (61-bit 模板, 每 4 删 1) -> 368 bit
          -> 交织 + DC 加扰
        │
        语音/数据帧 (每 40ms):
          = LICH(96 bit = 12 字节, 4×Golay24) + payload(272 bit = 34 字节)
          payload = FN(2) + codec2(16) + CRC(2) -> 卷积 -> P2 删余(12-bit, r=6/11) -> 272 bit
          -> 交织 + DC 加扰
```

### 2.3 关键算法

**CRC16**（`CRC16.h:12-70`）：
- poly = `0x5935`，init = `0xFFFF`，MSB-first
- 已知向量：`'A' -> 0x206E`，`'123456789' -> 0x772B`（`tests/CRC16Test.cpp:27-47`）
- 本移植：`m17_adapter.py:M17CRC16`

**卷积码**（`Convolution.h:12-21` + `M17Modulator.h:176-227`）：
- K=5（memory=4，32 状态），rate 1/2
- g1 = `0o31` = 0b11001，g2 = `0o27` = 0b10111
- 每个信息 bit 输出 (g1, g2) 两位，末尾 flush 4 个 0 tail bit
- 本移植：`m17_adapter.py:m17_conv_encode` + `m17_viterbi_decode`

**4FSK 符号映射**（`M17Modulator.h:137-147`）：
- dibit 0 -> +1，1 -> +3，2 -> -1，3 -> -3
- 每个符号传 2 bit（先 MSB 后 LSB）
- 本移植：`m17_adapter.py:m17_bits_to_symbols` / `m17_fsk_modulate`

**LSF 呼号编码**（`LinkSetupFrame.h:48-89`）：
- base-40：A=1..Z=26，0=27..9=36，-=37，/=38，.=39
- 6 字节大端；`0xFFFFFFFFFFFF` = BROADCAST

---

## 3. OP25 / P25 帧结构

### 3.1 物理层常量

| 参数 | 值 | 源码位置 |
|---|---|---|
| 符号率 | 9600 sps（C4FM / 4-FSK dibit） | TIA-102-BAAC |
| Phase1 帧同步 | `0x5575F5FF77FF`（48 bit） | `frame_sync_magics.h:39` |
| Phase2 帧同步 | `0x575D57F7FF`（40 bit） | `frame_sync_magics.h:47` |
| NID 长度 | 64 bit | `p25_framer.cc:62` |
| NID 布局 | NAC(12) + DUID(4) + BCH parity(48) | `p25_framer.cc:101-102` |
| Phase1 语音帧 | 216 ms = 9×20ms IMBE | `op25_imbe_frame.h:61` |

### 3.2 NID / DUID

NID 解码（`p25_framer.cc:67-135`）：
1. 收 64 bit，先做 BCH(64,16) 纠错（最多 4 bit 错）
2. 提取 `nac = (acc >> 52) & 0xFFF`，`duid = (acc >> 48) & 0xF`
3. 按 DUID/parity 组合判定合法性

DUID 枚举（`op25_msg_types.h:39-45`）：

| DUID | 名称 | 含义 |
|---|---|---|
| 0 | HDU | 首片数据单元 |
| 3 | TDU | 终止数据单元 |
| 5 | LDU1 | 语音片 1（含慢信令） |
| 7 | TSBK | 时隙信令块（控制信道） |
| 10 | LDU2 | 语音片 2 |
| 12 | PDU | 数据包 |
| 15 | TDULC | 终止数据单元（链路控制） |

BCH 生成多项式（`bch.cc:22-26`）：48 系数 `bchG[48]`，本移植按 GF(2) 多项式除法实现编码/校验。

### 3.3 IMBE 语音帧

每个 LDU 含 **9 个 144-bit 码书帧**（`op25_imbe_frame.h:61`）。每个 144-bit 码书帧拆成 88 bit 参数：

| 参数 | bit 宽 | 纠错编码 |
|---|---|---|
| u0 | 12 | Golay(23,12) |
| u1 | 12 | Golay(23,12) XOR PN |
| u2 | 12 | Golay(23,12) XOR PN |
| u3 | 12 | Golay(23,12) XOR PN |
| u4 | 11 | Hamming(15,11) XOR PN |
| u5 | 11 | Hamming(15,11) XOR PN |
| u6 | 11 | Hamming(15,11) XOR PN |
| u7 | 7 | 无编码 |
| **合计** | **88** | |

PN 序列发生器（`op25_imbe_frame.h:266-289`）：`Pr = (173*Pr + 13849) & 0xFFFF`，取 bit15。

### 3.4 Phase2 TDMA

- 12.5 kHz 信道分 2 个 6.25 kHz 时隙，每时隙 30 ms
- Phase2 同步字 40 bit（`frame_sync_magics.h:47`）
- DUID 6 种（`duid.py:41-58`）：4v / 2v / SACCH w / FACCH w / SACCH w/o / FACCH w/o

---

## 4. sdrtrunk 多信道状态机

### 4.1 状态枚举（`State.java:29-161`）

| 状态 | 含义 |
|---|---|
| IDLE | 空闲，未解码消息 |
| ACTIVE | 活跃但非通话/数据（如 TDU 间隔） |
| CALL | 通话中（有音频） |
| CONTROL | 控制信道 |
| DATA | 数据分组 |
| ENCRYPTED | 加密音频 |
| FADE | 渐隐过渡期 |
| TEARDOWN | 拆线 |
| RESET | 可复用 |

### 4.2 合法状态转换

来源 `State.java:36-161` 每个枚举的 `canChangeTo()`：

```
IDLE      -> ACTIVE/CALL/CONTROL/DATA/ENCRYPTED/FADE/RESET
CALL      -> ACTIVE/CONTROL/DATA/ENCRYPTED/FADE/IDLE/TEARDOWN/RESET
CONTROL   -> IDLE/FADE/RESET           (注意：CONTROL 不能直接到 CALL!)
DATA      -> ACTIVE/CALL/CONTROL/ENCRYPTED/FADE/RESET/TEARDOWN
ENCRYPTED -> FADE/TEARDOWN/RESET
FADE      -> 任意非 FADE/RESET
TEARDOWN  -> RESET
RESET     -> IDLE
```

### 4.3 超时机制（`StateMachine.java:99-109, 210-240`）

- 进入任何活动状态（ACTIVE/CALL/CONTROL/DATA/ENCRYPTED）时刷新 `fade_timeout`
- `checkState()` 发现 fade_timeout 到期 -> FADE
- FADE 状态下 end_timeout 到期 -> TEARDOWN
- 本移植默认 fade=5s，end=3s（经验值）

### 4.4 事件流

```
控制信道持续监听 TSBK
  ├── Channel Grant(TG, source, freq, ch_id) -> 业务信道 IDLE->CALL
  ├── Affiliation(TG, unit)                  -> 更新通话组表 affiliations
  ├── Group Update(TG, source, freq)         -> 更新通话组最近频率
  └── Terminator                            -> 业务信道 CALL->FADE->TEARDOWN->RESET
```

---

## 5. 移植对照表

| 原项目文件 | 本移植位置 | 说明 |
|---|---|---|
| m17 `CRC16.h:12-70` | `m17_adapter.py:M17CRC16` | CRC16 poly=0x5935 |
| m17 `Convolution.h:12-21` + `M17Modulator.h:176-227` | `m17_adapter.py:m17_conv_encode` / `m17_viterbi_decode` | K=5 r=1/2 |
| m17 `Trellis.h:17-35` | `m17_adapter.py:M17_PUNCTURE_P1/P2` | 删余模板 |
| m17 `M17Modulator.h:137-159` | `m17_adapter.py:m17_bits_to_symbols` | dibit 映射 |
| m17 `M17Randomizer.h:16-22,65-75` | `m17_adapter.py:M17_DC_SEQUENCE` / `m17_scramble` | DC 加扰 |
| m17 `LinkSetupFrame.h:48-89` | `m17_adapter.py:m17_encode_callsign` | base-40 呼号 |
| m17 `Golay24.h:87,184-201` | `m17_adapter.py:golay24_encode` | Lich 用 |
| op25 `frame_sync_magics.h:39,47` | `op25_adapter.py:P25_FRAME_SYNC` | 同步字 |
| op25 `p25_framer.cc:67-135` | `op25_adapter.py:p25_encode_nid` / `p25_decode_nid` | NID BCH |
| op25 `bch.cc:22-26` | `op25_adapter.py:_P25_BCH_G` | BCH 生成多项式 |
| op25 `op25_msg_types.h:39-45` | `op25_adapter.py:P25_DUID_NAMES` | DUID 枚举 |
| op25 `op25_imbe_frame.h:61,299-343` | `op25_adapter.py:p25_imbe_extract_params` | IMBE 参数拆分 |
| op25 `apps/tdma/duid.py:41-58` | `op25_adapter.py:p25_phase2_slot_info` | Phase2 TDMA |
| sdrtrunk `State.java:29-161` | `sdrtrunk_adapter.py:ChannelState` / `_ALLOWED` | 状态枚举+转换表 |
| sdrtrunk `StateMachine.java:99-109,122-193` | `sdrtrunk_adapter.py:TrunkedChannel` | 状态机+超时 |

---

## 6. 验证

`python3 tests/digital_voice_roundtrip.py` 结果：

```
PASS: M17 CRC16 known vectors        ('A'=0x206E, '123456789'=0x772B)
PASS: M17 CRC16 roundtrip           (5 组数据追加 FCS 余数为 0)
PASS: M17 convolutional codec roundtrip (8/16/32/64/128 bit 无噪全对)
PASS: M17 4FSK mod/demod roundtrip  (96/192/384 bit 无噪全对)
PASS: P25 NID decode                (0x3F55B22A854A58B2 -> NAC=0x3F5 DUID=5/LDU1)
PASS: sdrtrunk channel state machine (grant->CALL, CONTROL->CALL 被拒, CALL->FADE->TEARDOWN)
PASS: M17 LSF build/verify           (W9GL -> BROADCAST, CRC OK)
```

每个适配器注册 4 个工具（共 12 个），均挂在 `digital_voice` category 下。

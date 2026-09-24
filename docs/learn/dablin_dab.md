# dablin 真实 DAB/DAB+：从 C++ 源码到 `mbdsdr_ai/dab_plus_lite.py` 的落地笔记

> 本地参考克隆：`repos/dablin/`（Opendigitalradio/dablin v1.16.1，轻量 DAB/DAB+ 接收端）
> 配套实现：`mbdsdr_ai/dab_plus_lite.py`
> 往返验证：`tests/dab_plus_test.py`（9 项全过）
>
> 本文件只记录**真读 .cpp/.h 源码后逐行对齐到 Python 的常量与位域**，每条都给
> `file:line`。**dablin 本身不做 OFDM 解调/Viterbi/解扰**——它通过 `dab2eti`/`eti-cmdline`
> 等外部工具拿到 ETI(NI) 字节流后才开始解析（`src/dablin.cpp:243,263-276`）。
> 本移植与 dablin 一样停在"ETI 帧 → FIC → 服务表 → 音频参数"这一层。

---

## 0. 先纠正任务书里的三处臆测（真读源码后的事实）

| 任务书写的 | 上游 dablin 实际 | 来源 |
|---|---|---|
| ETI 同步字 `0x0792` | 24-bit FSYNC = `0x073AB6` / `0xF8C549`（偏移 1..3），偏移 0 = ERR=`0xFF` | `src/eti_player.cpp:25-26,33`; `src/eti_source.h:58-59` |
| FIC CRC 多项式 `0x108` | CRC-16/CCITT，多项式 **`0x1021`**，初值 `0xFFFF`，末尾取反 | `src/tools.cpp:218`; `src/tools.h:91-110` |
| FIG 0/1 = ensemble 标签；FIG 0/3 = 子信道 | **FIG 0/1 = 子信道组织**；ensemble 标签是 **FIG 1/0**；子信道比特率也在 FIG 0/1 | `src/fic_decoder.cpp:149`(0/1 子信道), `:646`(1/0 标签) |

初值 `0xFFFF`、ETI 帧 6144 字节、每帧 24 ms 这些任务书是对的，予以保留。
下文全部以上游 `.cpp/.h` 为准。

---

## 1. 传输模式 I 物理层参数

dablin 工作在 ETI 层，不直接持有 OFDM 常数；模式 I 参数来自 EN 300 401。
其中"ETI 帧 24 ms / 6144 字节"可由 dablin 源码直接证实：

| 项 | 值 | 来源 |
|---|---|---|
| FFT 大小 | 2048 | EN 300 401 模式 I（dablin 不触及，仅记录） |
| 循环前缀 | 246 µs（506 样本 @2.048MHz） | EN 300 401 |
| 有用符号时长 | 1 ms | EN 300 401 |
| 每帧符号数 | 76（含零符号） | EN 300 401 |
| ETI/CIF 帧长 | **24 ms** | `src/ensemble_source.cpp:200,236`（`frames*24` 当毫秒） |
| ETI 帧字节数 | **6144** | `src/eti_source.h:57` `EnsembleSource(..., 6144)` |

---

## 2. CRC-16/CCITT（FIB / ETI 头 / ETI-MST 共用）

来源：`src/tools.cpp:218`、`src/tools.h:91-110`。

```
CalcCRC_CRC16_CCITT(true, true, 0x1021)
  poly      = 0x1021   (x^16+x^12+x^5+1)
  init      = 0xFFFF   (initial_invert=true, tools.h:91-93)
  位移      = 左移 MSB-first，查表 (crc<<8) ^ lut[(crc>>8)^b]  (tools.h:95-98)
  末尾      = ~crc & 0xFFFF  (final_invert=true, tools.h:107-110)
```

- **FIB CRC**：对 FIB 前 30 字节计算，CRC 大端存于 FIB[30..31]（`fic_decoder.cpp:44-45`）。
- **ETI 头 CRC**：覆盖 `frame[4 : 4+4+nst*4+2]`，存于其后（`eti_player.cpp:49-55`）。
- **ETI-MST CRC**：覆盖 `(fl-nst-1)*4` 字节（`eti_player.cpp:63-69`）。
- **DAB+ 超帧 fire code**：另用多项式 `0x782F`、初值 0、不取反（`tools.cpp:220`，
  `dabplus_decoder.cpp:178-179`）。

> 自检：对 `"123456789"`，标准 CCITT-FALSE(xorout=0)=`0x29B1`，dablin 末尾取反后
> = `0xD64E`。`dab_plus_lite.crc16_ccitt(b"123456789") == 0xD64E` ✓。

---

## 3. ETI(NI) 帧布局

来源：`src/eti_player.cpp:23-99`、`src/eti_source.h:57-59`。

```
偏移   字段
[0]    ERR        必须 0xFF 否则丢弃          (eti_player.cpp:33)
[1..3] FSYNC      0x073AB6 或 0xF8C549(取反)  (eti_player.cpp:25-26)
[4..7] MNSC       4 字节
[5]     = FICF(bit7) | NST(bit6..0)          (eti_player.cpp:43-44)
[6..7]  = MID(bit6..4 右移3) | FL(bit2..0<<8 | [7])  (eti_player.cpp:45-46)
[8 .. 8+nst*4]    STC：nst 条 x 4 字节
        条内: scid = ([8+i*4] & 0xFC)>>2
              stl  = (([8+i*4+2]&3)<<8) | [8+i*4+3]   -> 字节数=stl*8  (eti_player.cpp:84-85)
头CRC  位于 4+4+nst*4+2 处 2 字节             (eti_player.cpp:50)
MST区  起始 = 4+4+nst*4+4                     (eti_player.cpp:60)
       FIC = ficl*4 字节; ficl = ficf?(mid==3?32:24):0  (eti_player.cpp:57,71-73)
```

- 同步：在缓冲区里扫 `[1..3]` 命中两个互补 FSYNC 之一（`ensemble_source.cpp:179-183`）。
- `find_sync()` 容忍前缀杂散字节，返回 FSYNC 偏移（测试里前缀 `AA BB CC` → offset 3）。

---

## 4. FIC：FIB → FIG

来源：`src/fic_decoder.cpp`。

### 4.1 FIB（32 字节）
- `Process()` 要求长度为 32 的倍数（`fic_decoder.cpp:32-35`）。
- 每 FIB 先做 CRC：`crc16(fib[:30]) == fib[30]<<8|fib[31]`，失败则 `FICDiscardedFIB()`
  丢弃（`fic_decoder.cpp:44-49`）。
- 再遍历 FIG：`type=byte>>5`，`len=byte&0x1F`，遇 `0xFF` 停止（`fic_decoder.cpp:52-67`）。

### 4.2 FIG 头
- FIG 0 头：`cn=0x80, oe=0x40, pd=0x20, ext=低5位`（`fic_decoder.h:40`）；
  `cn|oe|pd` 任一为真即忽略（`fic_decoder.cpp:84`）。
- FIG 1 头：`charset=>>4, oe=0x08, ext=低3位`（`fic_decoder.h:48`）。

### 4.3 关键 FIG 扩展（本移植实现的）

| FIG | 含义 | 关键位域 | 来源 |
|---|---|---|---|
| 0/0 | Ensemble 信息：EId + 告警 | `eid=data[0]<<8\|data[1]`; `alarm=data[2]&0x20` | `fic_decoder.cpp:136-137` |
| 0/1 | **基本子信道组织**（起始CU/长度/保护/比特率） | `subchid=data[0]>>2`; `start=(data[0]&3)<<8\|data[1]` | `fic_decoder.cpp:154-155` |
| 0/2 | 服务与音频组件：SId→子信道 | `sid=data[0]<<8\|data[1]`; `ascty=data[off]&0x3F`; `subchid=data[off+1]>>2` | `fic_decoder.cpp:214,225-226` |
| 1/0 | **Ensemble 标签** | 字段长 = 2+16+2；`label=data[2:18]`; `mask=data[18]<<8\|data[19]` | `fic_decoder.cpp:615,624-625,631` |
| 1/1 | 节目服务标签 | 同 1/0，`sid=data[0]<<8\|data[1]` | `fic_decoder.cpp:635` |

> 任务书把"子信道配置"记成 FIG 0/3、把 ensemble 标签记成 FIG 0/1——**都不对**。
> 上游 dablin 根本不处理 FIG 0/3；子信道配置是 FIG 0/1，ensemble 标签是 FIG 1/0。

### 4.4 子信道比特率（FIG 0/1）
- **长格式 EEP**（`data[off]&0x80`）：`option=(data[off]&0x70)>>4`，`pl=(data[off]&0xC)>>2`
  （`fic_decoder.cpp:164-165`）。
  - EEP-A (option=0)：`bitrate = size / eep_a[pl] * 8`，因子 `[12,8,6,4]`（`:172,:840`）
  - EEP-B (option=1)：`bitrate = size / eep_b[pl] * 32`，因子 `[27,21,18,15]`（`:177,:841`）
- **短格式 UEP**：64 项查表 `uep_sizes/uep_pls/uep_bitrates`（`fic_decoder.cpp:822-839`）。

### 4.5 音频类型（FIG 0/2）
- `ascty == 0` → DAB（MPEG Audio Layer II，`src/dab_decoder.h` MP2Decoder，外部 mpg123）。
- `ascty == 63` → DAB+（HE-AAC，`src/dabplus_decoder.h` SuperframeFilter）。
  （`fic_decoder.cpp:232-234`）

---

## 5. DAB+ 超帧音频参数

来源：`src/dabplus_decoder.cpp:170-214`、`src/dabplus_decoder.h` SuperframeFormat。

- 超帧同步 = fire code（poly `0x782F`）覆盖 `sf[2..10]`，存于 `sf[0..1]`（`:178-179`）。
- 格式字节 `sf[2]`：`dac_rate=0x40, sbr=0x20, aac_mode=0x10, ps=0x08`（`:185-188`）。
- 核心采样率（`GetCoreSrIndex`，`dabplus_decoder.h`）：

| dac_rate | sbr | 核心采样率 |
|---|---|---|
| 0 | 0 | 32 kHz |
| 1 | 0 | 48 kHz |
| 0 | 1 | 16 kHz |
| 1 | 1 | 24 kHz |

---

## 6. 落地到 Python 与测试

- `mbdsdr_ai/dab_plus_lite.py`：`DABParams` / `FICDecoder` / `ETIParser` /
  `DABAudioInfo` + 高层 `dab_fic_decode` / `dab_eti_parse` / `dab_decode_iq`。
- 工具注册：`ToolRegistry.register_dab_plus_tools()`（在 `register_builtin_tools`
  末尾调用），注册 `dab_fic_decode` / `dab_eti_parse` / `dab_decode_iq`，category=`broadcast`。
- 往返验证 `tests/dab_plus_test.py`（9 项）：
  - CRC 已知向量 `0xD64E`；合成 FIB 通过、翻 1 bit 被丢弃；
  - FIG 1/0 标签 → 名称/短标签掩码；FIG 0/1 EEP-A → startCU/size/保护/32kbps；
  - 合成 6144B ETI 帧 → `find_sync` → 两级 CRC → NST/MID/FICL/子信道字节数全对；
    破坏 FSYNC 被拒；模式 I 参数；DAB+ 采样率映射；`dab_decode_iq` 带前缀杂散仍能同步。
- **边界**：与 dablin 一致，本层输入是已分帧 ETI；OFDM 解调/Viterbi/解扰不在范围内
  （dablin 本身也靠 `dab2eti` 外部完成）。

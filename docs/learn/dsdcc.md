# DSDcc 数字语音解码移植（DMR / P25 Phase 1 / NXDN / D-Star）

> 源码来源：`repos/DSDcc/`（github.com/f4exb/dsdcc），本任务逐行实读后移植到
> `mbdsdr_ai/dsdcc_lite.py`。验证见 `tests/dsdcc_test.py`（10 项全过）。

## 1. DSDcc 是什么

DSDcc 是 DSD（Digital Speech Decoder）的 C++ 重写，把 **FM 判别器输出**（48000 S/s 实数采样）
解调为数字语音帧，再交 mbelib 合成音频。它是一个"多模式路由"解码器：同一个 4FSK/C4FM
前端，靠同步字识别 DMR / P25 P1 / NXDN / D-Star / YSF / dPMR 等模式。

本移植按任务要求聚焦 **DMR / P25**，前端（4FSK 解调、同步字）对所有模式通用。

## 2. 物理层关键常量（均标注源码）

| 参数 | 值 | 源码位置 |
|---|---|---|
| DMR/P25 符号率 | 4800 sym/s | `dsd_symbol.cpp:41` ringingFilter(48000,4800)；`dsd_decoder.cpp:321` |
| NXDN 符号率 | 2400 sym/s | `dsd_symbol.cpp:379-387` setSamplesPerSymbol(20) |
| 工作采样率 | 48000 S/s | `dsd_symbol.cpp:41` |
| 每符号采样数 | 10 (4800baud) / 20 (2400baud) | `dsd_symbol.cpp:52,370,379` |
| 根升余弦滚降 α | 0.2 | `dsd_filters.cpp:29` xcoeffs 注释 |
| DMR 时隙长度 | 288 bit = 144 dibit (30ms) | `dmr.h:26` |
| DMR 同步/中间字段 | 48 bit = 24 dibit | `dmr.h:28` |
| AMBE+2 语音帧 | 72 bit / 20ms | `dmr.h:34` |
| P25 IMBE 语音帧 | 88 bit / 20ms | `dsd_mbe.cpp:61` |

> 注：`dsd_filters.cpp:86` 的 `dmrcoeffs` 是另一组 α=0.7 的 RRC，`dmr_filter()` 实际
> 走它（mode=3）；`xcoeffs`（α=0.2，line 30）为 mode=1。本移植按标准 DMR 取 α=0.2。

## 3. 4FSK(C4FM) 解调链

对应 `DSDSymbol` 类（`dsd_symbol.h:31`）：

```
判别器采样 -> RRC 匹配滤波 -> 符号定时 -> 4电平判决 -> dibit{0,1,2,3}
```

- **RRC 匹配滤波**：`dsd_filters.cpp:151-202` 是 FIR 卷积；本移植用解析 RRC 公式生成，
  并内嵌 DSDcc 真实 `xcoeffs`（61 抽头）做对照（`dsdcc_lite.py:DMR_RRC_COEFFS_ALPHA02`）。
- **4 电平判决**（`dsd_symbol.cpp:408-456 digitize`）：
  ```
  center = (max+min)/2
  umid   = center + (max-center)/2
  lmid   = center + (min-center)/2
  > umid  -> dibit 1 (+3)
  (center,umid) -> dibit 0 (+1)
  (lmid,center) -> dibit 2 (-1)
  < lmid  -> dibit 3 (-3)
  ```
  门限由 min/max 以 α=0.25 IIR 自适应刷新（`dsd_symbol.cpp:329-337 snapMinMax`）。
- **符号同步**：DSDcc 用"采样值平方 + 二阶递归带通(ringing filter) 提符号率线 + 过零/PLL"
  （`dsd_symbol.cpp:99-311`）。本移植用最大能量点估相位 + 固定 sps 抽取。

## 4. 同步字检测

DSDcc 的同步缓冲**只记录极性**：正样值压成 1、负样值压成 3
（`dsd_symbol.cpp:463`）。因此同步字是 {1,3} 极性序列，长度 24 符号，容差 2
（`dsd_sync.cpp:61-89`）。

四组 DMR 同步字（`dsd_sync.cpp:30-33`，极性序列）：

| 模式 | 注释 hex |
|---|---|
| BS voice | `75 5F D7 DF 75 F7` |
| BS data | `DF F5 7D 75 DF 5D` |
| MS voice | `7F 7D 5D D5 7D FD` |
| MS data | `D5 D7 F7 7F D7 57` |

P25 Phase 1 同步字见 `dsd_sync.cpp:47`（`SyncP25P1`）。

## 5. DMR 时隙结构（144 dibits）

由 `dmr.cpp:666-928 processVoiceDibit` 的偏移链推出：

```
dibit 0..11    CACH            (24 bit)
dibit 12..47   语音帧1 AMBE    (72 bit)
dibit 48..65   语音帧2 前半    (36 bit)
dibit 66..89   中间48bit字段   = 同步字(同步帧) 或 EMB(8b)+SS(32b)+EMB(8b)
dibit 90..107  语音帧2 后半    (36 bit)
dibit 108..143 语音帧3 AMBE    (72 bit)
```

- **时隙分离**：CACH 用 Hamming(7,4) 译码取 Slot 指示（`dmr.cpp:931-977 decodeCACH`）。
- **色码**：EMB 用 QR(16,7,6) 译码取高 4 bit（`dmr.cpp:1016-1038 processEMB`）。
- 每个语音突发含 **3 个 AMBE+2 帧（72bit/20ms）**。

## 6. P25 Phase 1

- C4FM 4800 baud，4FSK（`dsd_decoder.cpp:645-674` setFSK(4)）。
- 同步字即 NID 字前导；NID = NAC(12bit) + DUID(4bit) + 校验(28bit)。
- 语音 DUID=0；语音帧为 IMBE **88 bit/20ms**（`dsd_mbe.cpp:61`）。
- C4FM 判决后用决策-directed 高斯启发式均衡（`p25p1_heuristics.cpp`），本移植未移植该均衡。

## 7. MBE 参数提取

`dsd_mbe.cpp` 本身**不做语音合成**——它把 72/88 bit 帧填进 `ambe_fr[4][24]` /
`imbe_d[88]`，再调 mbelib（`mbe_processAmbe3600x2450Framef` 等，`dsd_mbe.cpp:86-127`）。
本移植的 `MBEParams` 只提取原始位帧并粗拆基音/清浊/能量位域，**不合成波形**（需 mbelib）。

## 8. 已注册工具

| 工具 | 作用 |
|---|---|
| `fourfsk_demod` | 判别器采样 -> dibit 流 |
| `dmr_decode` | 144-dibit 时隙 -> burst/时隙/色码/AMBE 帧 |
| `p25_decode` | 24-dibit NID -> NAC/DUID/语音标志 |
| `dsd_decode_iq` | 一段采样 -> 自动搜索 DMR/P25 同步 |

## 9. 验证

```bash
python -m pytest tests/dsdcc_test.py -v
# 10 passed: 常量/RRC/4FSK往返/判决/DMR同步+帧提取/DMR流检测/P25同步/P25 NID/MBE/一键
```

## 10. 边界与未做

- 未移植 mbelib 语音合成（需 C 库）；解调/帧同步/参数提取已真实工作。
- 未移植 QR(16,7,6)/Golay(20,8) FEC 完整译码；色码/NAC 取位做了简化观测。
- 未移植 C4FM 决策-directed 均衡、PLL 符号时钟恢复；用最大能量相位估计替代。
- NXDN/D-Star/YSF/dPMR 同步字已收录于源码阅读范围，本版未单独解码。

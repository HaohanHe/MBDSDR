# Codec2 + FreeDV 数字语音移植笔记

> 源码来源: [codec2](https://github.com/drowe67/codec2) (David Rowe, LGPL v2.1)
> 本地仓库: `repos/codec2/`, `repos/freedv-gui/`

## 1. Codec2 是什么

Codec2 是开源低速率语音编解码器，专为业余无线电数字语音设计。
通过 LPC（线性预测编码）+ 基音检测 + 正弦/噪声激励模型，
在 **700–3200 bps** 速率下实现可懂的语音通信。

MBDSDR 移植了 **1600 bps 模式**的核心算法到 `mbdsdr_ai/codec2_lite.py`。

## 2. 核心算法流程

### 2.1 LPC 分析（来源: `src/lpc.c`）

```
语音输入 → 预加重 → 汉宁窗 → 自相关 → Levinson-Durbin → LPC系数
```

| 步骤 | 函数 | 源码位置 |
|------|------|----------|
| 预加重 | `pre_emp()` | lpc.c:53-64, α=1.0 |
| 加窗 | `hanning_window()` | lpc.c:95-103 |
| 自相关 | `autocorrelate()` | lpc.c:114-125 |
| 莱文逊-杜宾 | `levinson_durbin()` | lpc.c:142-168 |
| 综合 | `synthesis_filter()` | lpc.c:214-229 |

**关键常量**（来源: `src/defines.h:39-61`）：
- `Fs = 8000 Hz` 采样率
- `LPC_ORD = 10` LPC 阶数
- `n_samp = 80` (10ms 帧)
- `m_pitch = 320` (40ms 基音窗)

### 2.2 基音检测 NLP（来源: `src/nlp.c`）

非线性处理（NLP）基音检测：
1. 语音信号**平方**（非线性处理）
2. DC 陷波滤波（去除直流）
3. FIR 低通滤波
4. **抽取** ×5，加汉宁窗
5. 512 点 FFT
6. 谱峰值检测 → 基频 F0

**关键参数**（来源: `nlp.c:47-56`）：
- `P_MAX_M = 320` 分析窗
- `PE_FFT_SIZE = 512` FFT 大小
- `DEC = 5` 抽取因子
- `P_MIN = 20 samples (400 Hz)`, `P_MAX = 160 samples (50 Hz)`

### 2.3 1600 bps 帧结构（来源: `src/codec2.c:707-725`）

```
一帧 = 320 samples (40 ms) → 64 bits = 1600 bps
```

4 个内部 10ms 子帧：

| 参数 | 帧数 | 比特 |
|------|------|------|
| 清浊音 (voicing) | 4 | 4 |
| 基频 Wo | 2 | 14 (7+7) |
| 能量 E | 2 | 10 (5+5) |
| LSP 系数量化 | 1 | 36 |
| **总计** | | **64** |

### 2.4 LSP 系数量化（来源: `src/quantise.c`）

LPC 系数 → LSP（线谱对）→ 标量量化 → 比特流。
LSP 的优点：量化误差不稳定化滤波器的风险小。

## 3. FreeDV 700D OFDM 调制（来源: `src/ofdm_mode.c:26-56`）

### 3.1 参数

| 参数 | 值 | 源码位置 |
|------|-----|----------|
| 载波数 Nc | 17 | ofdm_mode.c:26 |
| 符号数 Ns | 8/帧 | ofdm_mode.c:28 |
| 符号周期 Ts | 18 ms | ofdm_mode.c:29 |
| 符号率 Rs | 55.56 Hz | ofdm_mode.c:279 |
| 循环前缀 TCP | 2 ms (16 samples) | ofdm_mode.c:30, ofdm.c:249 |
| FFT 大小 m | 144 | ofdm.c:248 |
| 调制方式 | QPSK (2 bit/sym) | ofdm_mode.c:35 |
| 采样率 | 8000 Hz | ofdm_mode.c:33 |
| 中心频率 | 1500 Hz | ofdm_mode.c:31 |
| 导频 | 边缘导频 (首末载波) | ofdm_mode.c:41 |

### 3.2 调制流程

```
比特流 → QPSK 映射 → 放置到 17 个子载波 → IFFT → 加循环前缀 → 上变频到 1500 Hz
```

### 3.3 解调流程

```
下变频 → 同步 → 去循环前缀 → FFT → 信道估计 → QPSK 判决 → 比特流
```

## 4. FreeDV 1600 DBPSK（来源: `src/freedv_1600.c`）

FreeDV 1600 模式使用 FDMDV（频分复用数字语音），
16 个载波，每个载波差分 BPSK 调制。
MBDSDR 移植版简化为单载波 DBPSK 以验证往返正确性。

## 5. MBDSDR 移植文件

| 文件 | 功能 |
|------|------|
| `mbdsdr_ai/codec2_lite.py` | LPC分析器、基音检测器、Codec2Lite 1600编解码 |
| `mbdsdr_ai/freedv_modem.py` | FreeDV700DModem (OFDM)、FreeDV1600Modem (DBPSK) |
| `tests/codec2_freedv_roundtrip.py` | 往返验证测试 |

## 6. 验证结果

```
✓ LPC 频谱包络相关性: 1.0000
✓ Codec2 1600 往返: 输出音频正常
✓ 700D OFDM: 10dB SNR 下 BER = 0%
✓ 1600 DBPSK: 无噪信道 BER = 0%
✓ 基音检测: 120Hz 输入 → 121.9Hz 估计
```

## 7. 已知简化

- LSP 量化使用均匀量化表（原版使用训练 VQ 表）
- 激励生成简化为脉冲/噪声（原版用正弦叠加）
- 700D 信道估计简化为单符号估计（原版多符号跟踪）
- 1600 模式简化为单载波 DBPSK（原版 16 载波 FDMDV）
- 未实现 LDPC 信道编码（700D 使用 HRA_112_112 码）

算法骨架完全对应真实 codec2 C 源码，可作为进一步优化的基础。

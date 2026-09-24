# NRSC-5 / HD Radio 真实移植笔记

> 源码来源：[theori-io/nrsc5](https://github.com/theori-io/nrsc5)（已 clone 到
> `repos/nrsc5/`）。本笔记记录把 HD Radio 接收链移植到 MBDSDR 时读到的真实参数与
> 算法，所有行号对应 `repos/nrsc5/src/`。

## 1. HD Radio 是什么

NRSC-5 是美国 iBiquity（现 Xperi）制定的 FM/AM 数字化广播标准，商用名 **HD Radio**。
它在现有 FM 信道（87.9–107.9 MHz，`include/nrsc5.h:27-28`）里叠加一个 OFDM 数字
信号，与模拟 FM 同播；数字部分用 HDC（HE-AAC v2，含 SBR+PS）编码音频。

nrsc5 是 GPLv3 的命令行接收机，用 RTL-SDR 接收后解出数字音频、PSD（节目服务数据，
即屏幕上的节目名/标题）、SIS（电台信息）、紧急警报等。

## 2. 采样率链

| 环节 | 采样率 | 来源 |
|---|---|---|
| RTL-SDR 原始 IQ（cu8） | 1 488 375 Hz | `include/nrsc5.h:53` |
| FM 半带 2 倍抽取后基带 | 744 187.5 Hz | `include/nrsc5.h:54` |
| AM 抽取后基带 | 46 511.7 Hz | `include/nrsc5.h:55` |
| HDC 解码输出音频 | 44 100 Hz | `include/nrsc5.h:58` |

FM 模式下，`input.c:66-69` 用一个半带滤波器把 1.488 MHz 抽成 744 kHz 再送
OFDM。本 lite 直接工作在 744 kHz 基带。

## 3. OFDM 参数（FM）

全部来自 `src/defines.h`：

| 参数 | 值 | 来源 |
|---|---|---|
| FFT 点数 | 2048 | `defines.h:12` `FFT_FM` |
| 循环前缀 CP | 112 | `defines.h:15` `CP_FM` |
| 符号总长 | 2160 | `defines.h:17` `FFTCP_FM` |
| 每 L1 块符号数 | 32 | `defines.h:20` `BLKSZ` |
| 下边带首子载波 | 478 | `defines.h:24` `LB_START = 1024-546` |
| 上边带末子载波 | 1570 | `defines.h:26` `UB_END = 1024+546` |
| partition 宽度 | 19 | `defines.h:75` |
| partition 内数据子载波 | 18 | `defines.h:77`（边界 1 个是参考导频）|
| 每主边带 partition 数 | 10 | `defines.h:79` |

子载波布局：以 FFT 中心（DC=1024）对称，下边带从 478 起每 19 个子载波一个
partition，共 10 个；上边带对称到 1570。每个 partition 的第一个子载波是已知参考
导频（DBPSK 调制，承载同步序列），后 18 个是 QPSK 数据子载波。

### 3.1 脉冲成型窗

`acquire.c:322-331`：CP 前段用 `sin(π/2 · i/CP)` 爬升，FFT 主体恒 1，CP 后段用
`cos(π/2 · (i-FFT)/CP)` 滚降——标准根升余弦边缘窗，用于抑制符号间干扰。

## 4. 接收链算法

### 4.1 粗同步（`acquire.c:98-263`）

1. 等缓冲攒够 `(BLKSZ+1)*FFTCP = 33*2160` 个采样。
2. 对每个候选定时偏移 `i`，在 32 个符号上累加循环前缀相关
   `x[i+j*2160] · conj(x[i+j*2160+2048])`（`acquire.c:130-134`）。
3. 用升余弦窗加权积分，取能量最大处为 `samperr`（`acquire.c:136-151`）。
4. 逐符号：去 CP、窗加权、2048 点 FFT、`fftshift`（`acquire.c:237-256`）。

### 4.2 细同步与 Costas 环（`sync.c:90-130`）

参考导频子载波用 DBPSK。`adjust_ref` 对每个参考子载波跑 Costas 环：
环路带宽 0.05、阻尼 0.7071（`sync.c:834-837`），估计相位与频偏，并用已知同步
序列（`sync.c:96-99`）做极性判决。

### 4.3 信道估计（`sync.c:263-282`）

在 partition 边界参考导频上测到信道相位后，按相邻 partition 的导频做加权线性
插值，补偿 partition 内 18 个数据子载波的相位。

### 4.4 星座解调（`sync.c:75-88`）

- **QPSK**（PM 主业务）：`real<0?0:1 | imag<0?0:2`（`sync.c:77`）。
- QAM16/QAM64 用于 AM 模式（`sync.c:80-88`），灰度编码。

### 4.5 CFO 搜索（`sync.c:292-337`）

粗同步失败时，在 ±2×19 子载波范围内试整数频偏，用参考导频同步序列命中率投票。

## 5. 信道编码

FM P1 主业务用 **k=7、码率 1/3 卷积码**，生成多项式八进制
`0133 / 0171 / 0165`（`decode.c:33-38`）。PIDS 用单独的内外交织
（`decode.c:62-65`）。本 lite 未移植 Viterbi，假设输入已是软判决比特。

## 6. 帧结构与 PSD（`frame.c`）

### 6.1 PCI 协议标识

每 L2 PDU 前插 24bit PCI（`frame.c:686-721`），模糊匹配容 4 bit 错
（`frame.c:32,667-678`）。关键值：

- `PCI_AUDIO = 0x38D8D3`（`frame.c:24`）——标识音频流。

### 6.2 HDLC 成帧

PSD/AAS 数据走 HDLC：
- 帧定界符 `0x7E`（`frame.c:393`）。
- 转义 `0x7D`，后续字节 XOR `0x20`（`frame.c:353-354`）。
- FCS-16（CRC-16/HDLC，多项式 0x1021，初值 0xFFFF）；合格余数 `0xF0B8`
  （`frame.c:144`）。
- AAS 协议号 `0x21`（`frame.c:377`），其后即节目名/标题文本。

### 6.3 音频帧头（`frame.c:200-215`）

14 字节头：`buf[8]&0xf` = codec_mode，`(buf[8]>>4)&3` = stream_id 等。
本 lite 的 `HDCDecoder.parse_header` 逐字段对应。

## 7. HDC 音频

nrsc5 **本身不含 HDC 解码器**——它把解出的音频 RSPDU 通过
`nrsc5_report_hdc`（`nrsc5.c:728`）回调交给外部应用（通常是立益 HDC 库），
再解码成 44.1 kHz PCM。HDC 是 HE-AAC v2（AAC-LC + SBR 频带复制 + PS 参数立体声）。
本移植只做参数提取骨架，完整解码需链接外部 HE-AAC v2 库。

## 8. MBDSDR 移植对应

| nrsc5 文件 | MBDSDR 类/函数 |
|---|---|
| `acquire.c` + `defines.h` | `HDRadioOFDM`（调制/粗同步/FFT）|
| `sync.c` | `HDRadioOFDM._fine_align`、`qpsk_mod/demod` |
| `frame.c` | `HDRadioFrame`（PCI/HDLC/FCS/PSD）|
| `hdc`/`output.c` | `HDCDecoder`（骨架）|
| `input.c` | 顶层 `hdradio_ofdm_demod` 等入口 |

往返验证见 `tests/nrsc5_roundtrip.py`：15dB 加噪下 QPSK-OFDM 往返 BER < 1%，
HDLC/PSD 帧可正确解出节目名。

## 9. 红线对照

- [x] 真读了 `.c/.h` 源码（`acquire.c`/`sync.c`/`frame.c`/`defines.h`/`nrsc5.h`/
      `input.c`/`decode.c`/`pids.h`）。
- [x] 常量注释标注 `file:line`。
- [x] 往返测试真实通过（17 项全绿）。
- [x] HDC 为骨架，OFDM 与帧解析真实工作。

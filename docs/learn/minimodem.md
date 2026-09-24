# minimodem —— 通用软件 FSK 调制解调器移植笔记

> 上游仓库：<https://github.com/kamalmostafa/minimodem>（GPLv3, (C) 2011-2020 Kamal Mostafa）
> 本地克隆：`repos/minimodem/`
> Python 移植：`mbdsdr_ai/minimodem_adapter.py`
> 测试：`tests/minimodem_test.py`（7 项全部通过）

## 1. 它是什么

minimodem 是一个把音频当成无线电信道的**通用软件 FSK（频移键控）调制解调器**：
stdin 字节 → 调频音频 → 扬声器/声卡 → 空气中 → 麦克风 → 解调回字节。
它实现了电话网里经典的 **Bell 103 / Bell 202** 呼叫音，以及无线电爱好者用的
**RTTY（Baudot/ITA-2）**。

核心思想极简：**两种频率代表 0/1**。

| 比特 | 频率 | 名称 |
|------|------|------|
| 1 | mark  音 | 传号 |
| 0 | space 音 | 空号 |

## 2. 关键常量（与源码逐行核对）

| 常量 | 值 | 来源 |
|------|----|----|
| 默认采样率 | 48000 Hz | `minimodem.c:534` |
| Bell 103 mark/space | 1270 / 1070 Hz | `minimodem.c:913,917,919` |
| Bell 202 mark/space | 1200 / 2200 Hz | `minimodem.c:902,906,908` |
| Bell 202 分析带宽 band_width | 200 Hz | `minimodem.c:910` |
| Bell 103 分析带宽 | 50 Hz | `minimodem.c:921` |
| RTTY mark/space | 1585 / 1415 Hz | `minimodem.c:926,928,930` |
| 帧格式（默认） | 1 起始位 + 8 数据位 + 1 停止位 | `minimodem.c:937-940` |
| 帧搜索置信门限 | 1.5 | `minimodem.c:519` |
| 前导/尾部 mark 位数 | 2 / 2 | `minimodem.c:51-52` |

**自动预设逻辑**（`minimodem.c:900-933`）：
- `baud >= 400` → Bell 202：`mark = baud/2 + 600`，`space = mark + baud*5/6`
- `100 <= baud < 400` → Bell 103：`mark=1270`，`space=1070`
- `baud < 100` → RTTY：`mark=1585`，`space=mark-170`

## 3. 调制侧（发）

`fsk_transmit_frame()`（`minimodem.c:81-112`）：

```
起始位(space/0) → b0 b1 ... b7 (LSB 先) → 停止位(mark/1)
```

- `bit==1 → mark 频率`，`bit==0 → space 频率`（`minimodem.c:106`）
- 每个比特占 `bit_nsamples = sample_rate/baud` 个采样（`minimodem.c:132`）
- 正弦波用**连续相位累加**生成，相邻频率切换不跳相（`simple-tone-generator.c:98,162`）
- 开头先发 2 个 mark 前导音，结尾发 2 个 mark 尾部音

Python 对应：`FSKModem.modulate_bits()`。

## 4. 解调侧（收）

`fsk_bit_analyze()`（`fsk.c:117-174`）是判决核心：

1. 取一个比特窗（`bit_nsamples` 长）
2. 做 FFT，取 mark bin 和 space bin 的幅度：`mag = |X(f)| * 2/N`（`fsk.c:132,158-159`）
3. **谁大就是谁**：`mag_mark > mag_space → bit=1`（`fsk.c:161`）

整帧判决（`fsk_frame_analyze`/`fsk_find_frame`，`fsk.c:179,449`）：
- 期望串 `"10dddddddd1"`：上一帧停止位(1) + 起始位(0) + 8 数据位(d) + 停止位(1)
  （`build_expect_bits_string`, `minimodem.c:443-487`）
- 在 ±半个比特内滑动搜索最佳采样相位，取帧信噪比 `snr*(1-divergence)` 最大者
  （`CONFIDENCE_ALGO=6`, `fsk.c:265,336`）

Python 对应：`FSKModem.demodulate_bits()` 用单频复相关（等价于 FFT bin 幅度）做判决，
并在一个比特周期内搜索最佳位相位。

## 5. 数据层

### ASCII 8N1（`databits_ascii.c`）
数据位本身直通（`databits_ascii.c:28-44`），起停位由帧结构加。Python：`ASCIIFrame`。

### Baudot / ITA-2（`baudot.c`）
5 位码，带**字母/数字换档**：
- `LTRS=0x1F`（字母态）、`FIGS=0x1B`（数字态）、`SPACE=0x04`（`baudot.c:187-189`）
- 解码表 32 项：字母列 + 美国数字列（`baudot.c:34-71`）
- 编码时若当前态与字符所在表不匹配，先插一个换档字再发数据字（`baudot.c:275-304`）
- **uso**（unshift-on-space=1）：收到/发出空格后自动回到字母态（`baudot.c:202,230,307`）

Python：`BaudotCodec`。

## 6. 移植到 MBDSDR

| C 源码 | Python 类/函数 |
|--------|---------------|
| `fsk.c` + `minimodem.c:81` | `FSKModem` |
| `databits_ascii.c` + `minimodem.c:443` | `ASCIIFrame` |
| `baudot.c` + `databits_baudot.c` | `BaudotCodec` |

已注册到 ToolRegistry：
`fsk_modulate`, `fsk_demodulate`, `baudot_encode`, `baudot_decode`,
`minimodem_decode_audio`（category=`digital_modes`）。

## 7. 验证结果

`pytest tests/minimodem_test.py` → **7 passed**：

- Bell 103/202 频率常量与源码一致
- ASCII 8N1 全 256 字节往返无误
- **FSK 加噪往返：10dB SNR 下比特正确率 100%**（>95% 要求；3dB 仍 100%）
- Bell 103 300bps / Bell 202 1200bps 端到端文本正确
- Baudot 字母↔数字换档自动插入 FIGS/LTRS，uso 行为正确

## 8. 用法示例

```python
from mbdsdr_ai.minimodem_adapter import FSKModem, ASCIIFrame

fr = ASCIIFrame()
modem = FSKModem(baud=300)                 # Bell 103
audio = modem.modulate_bits(fr.encode_bytes(b"HELLO"))
bits, conf = modem.demodulate_bits(audio)
print(fr.decode_bits(bits))                 # b'HELLO'
```

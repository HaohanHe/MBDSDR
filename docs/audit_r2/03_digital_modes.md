# 第二轮深度审查 — mbdsdr_ai/digital_modes.py

- 文件：`/home/user/Doubao/chats/38438160041798146/mbdsdr_ai/digital_modes.py`
- 行数：199（实际读到 199 行，与题面一致）
- 审查方式：逐行通读
- 审查日期：2026-09-24

---

## 0. 结论速览

**题面与实际代码严重不符。** 审查任务书要求核查 "BPSK31、RTTY、Varicode、ITA-2、31.25 Hz、45.45 波特、170 Hz 频移"，但本文件从头到尾**没有出现任何一个 BPSK31 / RTTY / Varicode / ITA-2 相关常量、编码表、类或函数**。文件 docstring（第 1–10 行）自述为 "FT8/FT4 数字模式工具"，实际内容也确实只是 FT8/FT4/AIS/ADS-B/DVB-S 的**参数常量表**加一个 WSJT-X 日志解析函数。

因此审查重点 1、2、4（BPSK31/RTTY 编解码正确性、相位翻转、频移、ITA-2 表、往返测试）**全部不适用**——没有代码可审。下面只对文件真实存在的内容做硬审查。

| 分类 | 计数 |
|---|---|
| [真bug] | 4 |
| [空壳] | 2 |
| [占位] | 1 |
| [建议] | 3 |

---

## 1. 逐类发现

### 1.1 BPSK31 / RTTY 相关 —— [空壳] 整类缺失

- `digital_modes.py:1-10`（docstring）：文件自我定位是 "FT8/FT4 数字模式工具"，**未声明**支持 BPSK31、RTTY。
- 全文件 grep：无 `BPSK`、`RTTY`、`Varicode`、`ITA2`、`baudot`、`31.25`、`45.45`、`170` 等任何符号。
- 结论：**题面声称的 "支持 BPSK31、RTTY 等数字模式" 在本文件中不存在**。如果产品需求/对外文档宣称支持，那是文档撒谎；如果是别处注册了模式名，那就是 [空壳]——只注册名字没有编解码实现。本次审查范围内未发现注册处，建议主控 agent 在 `sdr_tools.py` / `signal_analysis.py` / `decoders.py`（grep 命中的另外三个文件）里再追一次，确认是否有 "在别处注册 BPSK31/RTTY 但实际调不到本文件" 的挂名模式。

> 审查重点 1、2、4 在此文件**无可审代码**。Varicode 表、相位翻转检测、170 Hz 频移、45.45 波特、ITA-2 字母表——一行都没有。

---

### 1.2 FT8/FT4 参数表 —— [真bug] 符号率错误

**`digital_modes.py:27` — FT8 `symbol_duration_ms: 256` 错误。**

- 标准 FT8（WSJT-X）：符号率 6.25 baud，即符号时长 **160 ms**（79 个符号 × 160 ms = 12.64 s，加 2.36 s 空闲凑满 15 s 周期）。
- 本文件写 256 ms = 3.906 baud，**与标准差 1.6 倍**。任何按此常量做调制/解调的下游代码，生成的信号在 fldigi/WSJT-X 眼里都是 2.5× 慢节奏，**完全不可互操作**。
- 对比同文件 `FT4_PARAMS`（第 41 行）`symbol_duration_ms: 48` —— FT4 标准为 48.74 ms（~20.5 baud），这里是对的。同一份文件两个模式一个对一个错，说明 FT8 那个数字不是笔误就是照抄了某个错误来源。

**`digital_modes.py:32` — FT8 `coding: "LDPC (K=79, N=174)"` 标签可疑。**

- FT8 实际是 (N=174, K=91) 量级的 LDPC 类码，信息位约 77 bit（含 12-bit CRC），编码后映射成 79 个 MFSK 符号。把 "K=79" 直接写出来会误导调用方把 79 当成信息位长度（实际 79 是符号数）。
- `digital_modes.py:46` FT4 写 "LDPC (K=79, N=170)" 同样把符号数和信息位混为一谈；FT4 符号数是 105 不是 79。
- 判为 [真bug]（标注语义错），但不影响运行——只是字符串。

**`digital_modes.py:28` / `:42` — `bandwidth_hz: 79` 命名误导。**

- 79 是 FT8 的**符号数**，不是带宽。FT8 实际占用带宽约 43.75 Hz（8 个音 × 6.25 Hz 间隔）。字段名叫 `bandwidth_hz` 但塞了个符号数进去，下游如果真拿这个值做滤波带宽会偏大约 1.8 倍。[建议] 改名 `num_symbols` 或改值为 ~50。

**频率表 `digital_modes.py:51-63`：基本正确，但 6m/2m 有疑点。**

- 主流波段（160/80/40/30/20/17/15/12/10m）FT8 频率与 WSJT-X 默认 Dial Frequency 一致（7.074、14.074、10.136、18.100、21.074、24.915、28.074、50.313），正确。
- 62 行 2m FT8 = 144.174 MHz —— 标准 2m FT8 dial 是 **144.174 MHz**，正确。FT4 2m = 144.170 MHz 略偏，实际 FT4 在 2m 不常用，可忽略。
- 61 行 6m FT4 = 50.318 MHz，FT8 = 50.313 MHz，与 WSJT-X 一致。
- 频率表本身没有 [真bug]。

---

### 1.3 WSJTInterface —— [空壳] + [真bug]

**`digital_modes.py:90-136` 类名/注释谎称 UDP，实际只是读日志文件。**

- 第 94–96 行 docstring："通过UDP与WSJT-X通信，获取解码结果。WSJT-X默认UDP端口：2237"。
- `__init__`（98–100）只存了 host/port，**没有任何 socket、select、recv 调用**。`import socket` 都没有。
- `get_decoded_messages`（102–132）只做一件事：如果传了 `wsjtx_log` 路径，就打开文件按空白切行。这是一个**纯文件解析函数**，套了个 "网络接口" 类名。
- 判定：**[空壳]** —— 对外宣称 WSJT-X 网络接口，实际没有网络能力。真实 WSJT-X 的 UDP 监听需要实现 WSJT-X Message Types（QSO_LOGGED、DECODE、STATUS、HEARTBEAT 等，二进制头 + JSON 段），本文件一行都没有。

**`digital_modes.py:125-127` 日志字段解析有 bug。**

```python
"snr": parts[1] if parts[1].lstrip('-').isdigit() else None,
"dt": parts[2] if parts[2].replace('.', '').isdigit() else None,
"freq": parts[3] if parts[3].replace('.', '').isdigit() else None,
```

- WSJT-X 标准 `wsjtx_decoupled.txt` / ALL.TXT 一行形如：`234512  1234   -2  0.3  1000.5  CQ ABC1DE`。
  - `parts[1]` = SNR 整数（如 `-2`），`lstrip('-')` 后变 `"2"`，isdigit 通过 ✓
  - `parts[2]` = dt，可能为负（`-0.3`），`replace('.','')` 后变 `"-03"`，**`isdigit()` 对负号返回 False** → 负数 dt 全部被吞成 None。[真bug]
  - `parts[3]` = 音频频率（kHz，如 `1000.5`），`replace('.','')` → `"10005"`，isdigit 通过 ✓；但 WSJT-X 实际日志里这个字段可能是 `1234.5` 这种浮点，能过。
- 另外 `freq` 没有做单位换算（日志里是 kHz，代码直接原样字符串返回，没标单位）。下游如果当成 Hz 用会差 1000 倍。[建议]
- 第 122 行 `if len(parts) >= 5` 这个门槛对太短的行会静默跳过，没有 warning——日志格式一变就悄悄返回空列表。[建议]

**`digital_modes.py:134-136` `list_available_modes` 硬编码。**

- 返回 `["FT8","FT4","JT9","JT65","WSPR","Echo","FSK CW","QRA64"]`，缺 WSJT-X 早已支持的 FST4、FST4W、Q65。不是 bug，是过期快照。[建议]

---

### 1.4 AIS / ADS-B / DVB-S 参数表

- `digital_modes.py:143-151` AIS：161.975 / 162.025 MHz、9600 波特、GMSK、NRZI、256 bit 帧长 —— **全部正确**（AIS 标准 ITU-R M.1371）。
- `digital_modes.py:163-169` ADS-B：1090 MHz、PPM、1 Mbps、112 bit 帧、24-bit CRC —— **正确**（DO-260B）。注意：ADS-B 前导码 + 数据帧共 120 bit（8 bit preamble + 112 bit data），这里只写了 112，漏了前导，但不影响理解。[建议]
- `digital_modes.py:181-194` DVB-S/S2：QPSK/8PSK/16APSK/32APSK、FEC 范围、符号率 1–45 MSym/s、C/Ku 波段 —— 正确。
- **这三块只是常量字典，没有任何编解码实现**。`get_ais_params()` 等函数只 return dict。属 [占位]：把行业常识写成 Python 字典当 "工具" 暴露，没有任何 DSP/物理层能力。

---

### 1.5 其他

- `digital_modes.py:12` `import subprocess`、`:13` `import json` —— **全文未使用**。dead import。[建议]
- `digital_modes.py:15` `from typing import Dict, List, Optional` —— `Optional` 未使用。[建议]
- 第 9 行注释 "工具化：调用外部解码器/编码器，解析结果" —— 但代码里没有任何 `subprocess.run` / 外部二进制调用，与注释不符。[占位]

---

## 2. 对照审查重点的回答

| 审查重点 | 回答 |
|---|---|
| 1. BPSK31 Varicode 表 / 相位翻转 / 31.25 Hz / 同步 | **文件中不存在 BPSK31 代码**，无表可查。[空壳] |
| 2. RTTY 170 Hz / 45.45 波特 / ITA-2 | **文件中不存在 RTTY 代码**，无表可查。[空壳] |
| 3. 每个模式是否真有编解码 | FT8/FT4/AIS/ADS-B/DVB-S **全部只有参数字典，无编解码**。WSJTInterface 只解析文本日志，不做 UDP。 |
| 4. "实验室绿、真机红" 往返通过但不兼容 | 因为根本没有编解码器，谈不上往返测试。**唯一接近可执行代码的 FT8 symbol_duration_ms=256 是错的**（应为 160 ms），任何照它做的调制在 WSJT-X 里必然解调失败——典型的 "真机红" 雷。 |
| 5. 与 WSJT-X/fldigi 互操作 | 频率表正确；但符号率错（FT8 256 ms vs 标准 160 ms），一旦真有人拿这个常量发信号，WSJT-X 解不出来。 |

---

## 3. 最关键的三个问题（按严重度）

1. **[真bug] `digital_modes.py:27` FT8 symbol_duration_ms=256，应为 160**。这是本文件唯一一处会直接破坏与 WSJT-X 互操作的数值错误。
2. **[空壳] `digital_modes.py:90-136` WSJTInterface 自称 UDP 网络接口，实际只 `open(wsjtx_log)` 读文件**。类名、docstring、host/port 参数全是误导。
3. **[空壳] 题面声称的 BPSK31/RTTY 在本文件不存在**。需要主控 agent 确认是需求方记错了文件位置，还是项目里别处真的挂了 BPSK31/RTTY 的名但没实现。

---

## 4. 给主控 agent 的建议

- 在 `decoders.py`、`sdr_tools.py`、`signal_analysis.py` 里追一下 BPSK/RTTY 关键字的命中处，确认是否存在 "模式名注册了但数字模式实际是空函数" 的情况。
- 把 `digital_modes.py` 改名为 `digital_mode_refs.py` 或者把 FT8 symbol_duration_ms 修成 160 ms，二者至少做一件。
- WSJTInterface 要么真接 UDP（用 `python-qt`/裸 socket 实现 WSJT-X 网络协议），要么改名为 `WSJTLogParser` 并删掉 host/port 参数。

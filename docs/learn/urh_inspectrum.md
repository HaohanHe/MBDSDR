# URH 与 inspectrum 源码学习笔记

本文件记录移植协议分析 / 自动调制识别到 MBDSDR 上游阅读时的关键机制，全部带 `file:line` 锚点，供后续对照。

- URH 仓库：`repos/urh/`（Python + Cython）
- inspectrum 仓库：`repos/inspectrum/`（C++ / Qt）

---

## 1. URH：自动调制识别（AMR）

### 1.1 支持的调制类型枚举
- `repos/urh/src/urh/signalprocessing/Modulator.py:20` — `MODULATION_TYPES = ["ASK", "FSK", "PSK", "GFSK", "OQPSK"]`
- `repos/urh/src/urh/signalprocessing/Modulator.py:21-27` — `MODULATION_TYPES_VERBOSE` 中英文说明
- `repos/urh/src/urh/signalprocessing/Signal.py:26` — `MODULATION_TYPES = ["ASK", "FSK", "PSK", "QAM"]`（Signal 视角，比 Modulator 多 QAM、少 GFSK/OQPSK）
- `repos/urh/src/urh/signalprocessing/Modulator.py:92-102` — `is_amplitude_based / is_frequency_based / is_phase_based` 通过子串匹配分类，移植时可直接复用这一三分类思路。

### 1.2 自动检测入口
URH 没有名为 `ReceiveController.py` 的文件；自动检测的真正入口是：
- `repos/urh/src/urh/signalprocessing/Signal.py:537-571` — `Signal.auto_detect()`：调用 `AutoInterpretation.estimate()`，把估计出的 `modulation_type / center / tolerance / bit_length` 回写到 Signal。
- `repos/urh/src/urh/ainterpretation/AutoInterpretation.py:373-471` — `estimate(iq_array, noise, modulation)` 主流水线：
  1. `detect_noise_level(magnitudes)` 估噪声（`:60-91`，分块取均值的最小值）。
  2. `segment_messages_from_magnitudes(magnitudes, noise)` 切消息（`:94-104`，Cython）。
  3. `detect_modulation_for_messages()` 对每条消息判调制并投票（`:210-223`）。
  4. `afp_demod()` 正交解调（`:399-403`，Cython `signal_functions.afp_demod`）。
  5. `detect_center()` 在解调后矩形信号上做直方图找电平中心（`:226-277`）。
  6. `get_plateau_lengths` → `merge_plateau_lengths` → `get_bit_length_from_plateau_lengths` 由平台长度直方图估采样/符号（`:301-370`）。

### 1.3 调制判别核心（移植重点）
`repos/urh/src/urh/ainterpretation/AutoInterpretation.py:151-207` — `detect_modulation(data)`：
- `:153-158` — 若数据中零样本占比高 → `OOK`（开关键控）。
- `:161-165` — 对 `|data|` 和 `|data|/|data|`（归一化）分别做 Haar 连续小波变换 `Wavelet.cwt_haar`。
- `:167-175` — 计算原始幅度小波方差 `var_mag`、归一化幅度小波方差 `var_norm_mag`、中值滤波后方差 `var_filtered_mag / var_filtered_norm_mag`。
- `:177-181` — 四个方差都 `<0.15` → `OOK`（信号近似常数幅度）。
- `:183-186` — `var_mag > 1.5 * var_norm_mag` → `ASK`（幅度在变、归一化后不变）。
- `:189-190` — 否则若 `var_mag > 10 * var_filtered_mag` → `PSK`（相位跳变在原始幅度小波上产生高频尖峰，中值滤波后被压掉）。
- `:194-205` — 否则对数据做 FFT，若前 10 个峰值里存在两个相距 ≥10 bin 且幅值 ≥100 的峰 → `FSK`（两个频点）。
- `:207` — 兜底 → `OOK`。

> 移植启示：URH 的 AMR 本质是「幅度变化 vs 归一化幅度变化 vs 相位跳变 vs 双频峰」的规则决策树。我们在 `modulation_classifier.py` 中沿用这一思路，但扩展到瞬时幅度/相位/频率的统计矩（均值/方差/峰度）+ 频谱平坦度 + 零交叉率，并加置信度输出与 AI 启发式。

### 1.4 时钟恢复 / 比特提取
- `repos/urh/src/urh/signalprocessing/Signal.py:474-484` — `quad_demod()` 调 Cython `afp_demod`，内部含 Costas 环（`costas_loop_bandwidth`，`:57`）。
- `repos/urh/src/urh/ainterpretation/AutoInterpretation.py:417-419` — `get_plateau_lengths(msg_rect_data, center, percentage=25)`：以中心电平 ±25% 为阈值，把解调后矩形信号切成平台，平台长度即「采样数」。
- URH 没有显式的 Gardner / Early-Late 算法；它走的是「先估 samples_per_symbol，再在符号中心采样」的开环路线。我们的 `clock_recovery.py` 把 Gardner 和 Early-Late 这两种经典闭环算法补上，是差异化增强。

---

## 2. URH：协议解析

### 2.1 字段类型枚举
- `repos/urh/src/urh/signalprocessing/FieldType.py:11-21` — `FieldType.Function`：`PREAMBLE / SYNC / LENGTH / SRC_ADDRESS / DST_ADDRESS / SEQUENCE_NUMBER / TYPE / DATA / CHECKSUM / CUSTOM`。
- `repos/urh/src/urh/signalprocessing/FieldType.py:66-71` — `default_field_types()` 生成默认字段类型列表。

### 2.2 协议树
- `repos/urh/src/urh/models/ProtocolTreeItem.py:13-25` — `ProtocolTreeItem` 是 Qt 树模型节点，包装 `ProtocolAnalyzer`（协议帧）或 `ProtocolGroup`（文件夹）。
- `repos/urh/src/urh/models/ProtocolTreeItem.py:108-135` — `children / parent / childCount / indexInParent` 标准树接口。
- 真正的「字段树」在 `Message` 上挂 `Label`（`ProtocoLabel.py`），不是嵌套树；`ProtocolTreeItem` 是消息分组树。我们的 `ProtocolField` 设计成嵌套字段树（name/bit_length/type/value），比 URH 更直接。

### 2.3 消息结构
- `repos/urh/src/urh/signalprocessing/Message.py:17` — `Message` 类；`:46` `__init__`；`:235-305` `encoded_bits / decoded_bits / plain_bits` 三层比特表示（编码后 / 解码后 / 标签去除后）。
- `repos/urh/src/urh/signalprocessing/Message.py:201` — `get_byte_length()`；`:426` `get_duration(sample_rate)`。

### 2.4 CRC 校验
- `repos/urh/src/urh/util/GenericCRC.py:36-67` — `STANDARD_CHECKSUMS` 表：CRC8/CCITT、CRC16/CCITT（poly `0x1021`，ref_in/ref_out=True）、CRC32 等。
- `repos/urh/src/urh/util/GenericCRC.py:188` — `crc(inpt)` 计算；`:444` `guess_all(bits)` 暴力猜多项式与数据范围。
- `repos/urh/src/urh/cythonext/util.pyx:75` — Cython 版 `crc()` 位级实现。
- `repos/urh/src/urh/awre/engines/ChecksumEngine.py:36-80` — `ChecksumEngine.find()`：对每条消息先试 WSP 校验和，再 `GenericCRC.guess_all` 暴力枚举。

### 2.5 AI 协议逆向（awre 包）
- `repos/urh/src/urh/awre/FormatFinder.py` — 字段格式发现主类。
- `repos/urh/src/urh/awre/engines/LengthEngine.py:21-48` — `LengthEngine.find()`：按消息长度聚类，找「同长度内公共、不同长度间不同」的比特范围 → 长度字段。
- `repos/urh/src/urh/awre/engines/AddressEngine.py` — 地址字段（公共且变化范围小）。
- `repos/urh/src/urh/awre/engines/SequenceNumberEngine.py` — 序列号字段（单调递增）。
- `repos/urh/src/urh/awre/engines/ChecksumEngine.py` — 校验字段（CRC 猜测）。

> 移植启示：我们的 `protocol_parser.py` 实现「同步字 + 长度字段 + CRC-16/CCITT」自动检测；AI 增强部分用「熵分析 + 重复 n-gram 模式」自动推断未知协议字段边界，思路对应 LengthEngine/AddressEngine，但用纯 Python 实现、不依赖 Cython。

---

## 3. inspectrum：时频分析

### 3.1 样本加载与格式
- `repos/inspectrum/src/inputsource.cpp:43-220` — 各种 SampleAdapter（cf32/cs16/cs8/cu8/rf32/ri16/ri8 等），统一转 `std::complex<float>`。
- `repos/inspectrum/src/inputsource.cpp:356-451` — `openFile()` 按后缀选 adapter，支持 SigMF meta/data。
- `repos/inspectrum/src/inputsource.cpp:464-482` — `getSamples(start, length)` mmap 直接拷贝。

### 3.2 STFT / 频谱图
- `repos/inspectrum/src/spectrogramplot.cpp:36-53` — `SpectrogramPlot` 构造：默认 `fftSize=512`，colormap 用 HSV（`:46-49`）。
- `repos/inspectrum/src/spectrogramplot.cpp:288-323` — `getLine(dest, sample)`：
  - `:295-297` 以 `sample` 为中点取 `fftSize` 个样本；
  - `:305-307` 加窗；
  - `:309` FFT；
  - `:312-320` `k = i ^ (fftSize>>1)` 做 fftshift，功率 `real²+imag²`，转 dB：`log2(power) * 10/log2(10)`。
- `repos/inspectrum/src/spectrogramplot.cpp:325-328` — `getStride() = fftSize / zoomLevel`（hop size，zoom=1 时不重叠）。
- `repos/inspectrum/src/fft.cpp:24-44` — FFT 类封装 fftw3。

### 3.3 选框 / 测量工具（Tuner）
- `repos/inspectrum/src/tuner.cpp:23-35` — `Tuner` 三个游标：`minCursor / cfCursor / maxCursor`。
- `repos/inspectrum/src/tuner.cpp:37-40` — `centre()` 返回中心频率 bin。
- `repos/inspectrum/src/tuner.cpp:61-64` — `deviation()` 返回半带宽 bin 数（`|cursor - cfCursor|`）。
- `repos/inspectrum/src/tuner.cpp:126-131` — `updateCursors()`：`min = cf - deviation`，`max = cf + deviation`。
- `repos/inspectrum/src/spectrogramplot.cpp:330-344` — `getTunerPhaseInc / getTunerTaps`：把 tuner 选的频偏转 NCO 相位增量和 Kaiser 低通抽头，做数字下变频（DDC）。

### 3.4 缩放 / 平移
- `repos/inspectrum/src/plotview.cpp:227-247` — 滚轮缩放：`zoomLevel ∈ [1, fftSize]`，以鼠标下样本为锚点。
- `repos/inspectrum/src/plotview.cpp:199-212` — 选中时间段：`selectedSamples = columnToSample(...)`，`selectionTime = sampleCount / rate`。
- `repos/inspectrum/src/plotview.cpp:166-178` — 右键菜单「导出选框样本」。

> 移植启示：我们的 `spectrogram.py` 用 `scipy.signal.stft` 或自建 `np.fft` 实现 STFT；选框测量直接给出「中心频率 / 带宽 / 持续时间 / 选框 IQ 样本」四个量，对应 inspectrum 的 Tuner + 导出功能。AI 增强：在选框上自动跑调制识别，给出建议解调参数。

---

## 4. 移植对照表

| 上游机制 | 上游位置 | MBDSDR 移植位置 |
|---|---|---|
| 调制类型枚举 | `Modulator.py:20` | `analysis/modulation_classifier.py` |
| 小波方差 AMR | `AutoInterpretation.py:151-207` | `analysis/modulation_classifier.py`（规则树 + 统计矩） |
| 噪声阈值估计 | `AutoInterpretation.py:60-91` | `analysis/modulation_classifier.py` |
| 平台长度→符号率 | `AutoInterpretation.py:344-370` | `analysis/clock_recovery.py` |
| Gardner 定时恢复 | （URH 无，补经典算法） | `analysis/clock_recovery.py` |
| Early-Late 门 | （URH 无，补经典算法） | `analysis/clock_recovery.py` |
| 字段类型枚举 | `FieldType.py:11-21` | `analysis/protocol_parser.py` |
| CRC-16/CCITT | `GenericCRC.py:49` | `analysis/protocol_parser.py` |
| 长度字段聚类 | `LengthEngine.py:21-48` | `analysis/protocol_parser.py`（熵+n-gram） |
| STFT | `spectrogramplot.cpp:288-323` | `analysis/spectrogram.py` |
| Tuner 选框测量 | `tuner.cpp:37-64` | `analysis/spectrogram.py` |
| 选框导出 IQ | `plotview.cpp:166-178` | `analysis/spectrogram.py` |

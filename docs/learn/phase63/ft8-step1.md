# FT8 第①步落地：8-FSK 调制器 + Costas 粗同步器（Python）

> 日期：2026-10-10  作者：MBDSDR 工程轮
> 基线 HEAD：2ff3840  产物：纯 Python 层，未改任何 cpp
> 配套：`ft8-mechanism-study.md`（机制笔记，干净室）、`ft8-landing-path.md` §轮次 1
> 许可：本模块自写 MIT，未抄 wsjtx GPL 源码；下列 file:line 仅指本仓实现位置

---

## 0. 本轮范围与诚实边界

**做了**：
- `mbdsdr_ai/ft8_modem.py`：确定性 8-FSK 复基带合成器 + Costas 粗同步器；
- `mbdsdr_ai/tests/test_ft8_modem.py`：19 项 pytest 互测全绿；
- `experiments/exp_ft8_step1.py`：SNR 扫描 + 纯噪声虚警对照，CSV 落盘。

**没做（第②步，诚实声明）**：
- LDPC(174,91) 编码 / log-domain BP 解码 / 解交织 / 77-bit 消息 unpack；
- `Ft8Modulator.encode_message()` 仅留签名，调用即 `NotImplementedError`；
- 本轮数据段是 `placeholder_data_symbols()` 注入的**确定性伪随机符号**
  （固定 seed），不是真实 LDPC 码字。所有实验数字 `data-origin: synthetic`。

---

## 1. 机制笔记 → 实现对照（file:line）

| 机制笔记条目 | 实现位置（mbdsdr_ai/ft8_modem.py） |
|------|------|
| §1 8-FSK：NSPS=1920、音距 6.25、8 音 | 常量 `FS_HZ/NSPS/TONE_SPACING_HZ/N_TONES` L60-66；`modulate()` 连续相位合成 L194 |
| §1 格雷表 `[0,1,3,2,5,6,4,7]` | `GRAY_MAP` L73；`gray_to_tone/tone_to_gray` L88-99 |
| §2 帧 79 符号 = S7 D29 S7 D29 S7 | `FRAME_LAYOUT` L79；`build_frame_symbols()` L168 |
| §2 Costas 序列 `[3,1,4,0,6,5,2]` | `COSTAS_SEQ` L76；三块位置 0/36/72 抽头 L307-310 |
| §2 nssy=4 / nfos=2（3.125 Hz bin） | 时间步 480（L300）、DFT 3840（L297）、相关平面 L347-356 |
| §5 符号谱 + 二维 Costas 相关峰 | `_symbol_spectrum()` L315；`process()` 相关峰搜索 L358-368 |
| §5 峰/次峰比作同步质量 | `sync_quality = p_peak/p_2nd` L370；门限 1.5 L289 |
| §0 干净室 / 诚实空态 | 模块 docstring L1-30；纯噪声 `synced=False` 空符号 L371-373 |

### 1.1 调制器 Ft8Modulator（L120-252）
- 连续相位 8-FSK：`np.cumsum` 相位不重置符号边界，避免硬切换宽带泄漏（L215-218）；
- 可注入：频偏 Hz（复数混频 L221-223）、时偏样本数（帧前补零 L225-229）、
  AWGN（dB，固定 seed RNG，信号功率归一 L232-244）；
- `iq_to_interleaved()`（L102）输出 interleaved float32（I,Q,I,Q…）供 SigMF/C++ 消费。

### 1.2 Costas 同步器 Ft8CostasSync（L266-432）
- **粗同步**：4× 时间 / 2× 频率过采样符号谱上，对 (off_bin, j) 二维求和 21 个
  Costas 音位（L347-356）；峰位给出频偏（bin 级，±1.56 Hz）与近似时偏；
- **精同步**：粗频偏混掉后，在粗时偏 ±2 符号内用逐符号 1920 点匹配滤波
  （8 音本地振荡 L395-399）搜精确符号边界（L408-415），再逐符号 8 音
  匹配能量 argmax 提取 79 符号（L419-425）。这一步规避了 3840 窗跨两符号的
  串音问题，使逐符号精确还原。
- **诚实空态**：`sync_quality < 1.5`（或非有限）→ `synced=False`、符号空数组，
  不编造（L371-373）。

---

## 2. pytest 互测结论（mbdsdr_ai/tests/test_ft8_modem.py）

**19 passed**（`python3 -m pytest mbdsdr_ai/tests/test_ft8_modem.py`）：

| 断言 | 用例 | 结论 |
|------|------|------|
| 协议常量自洽 | `test_protocol_constants` | 12k/1920/0.16s/6.25Hz/79符号/58数据/格雷表/Costas 全对 |
| S7D29S7D29S7 布局 | `test_frame_layout_*` | 同步块在符号 0-6/36-42/72-78，两段数据各 29 |
| 输出形状/类型 | `test_modulator_output_shape_dtype` | complex64、151680 样本、\|iq\|≈1 |
| 确定性 | `test_modulator_deterministic` | 同 seed 两次输出逐样本相等 |
| 占位数据确定性 | `test_modulator_placeholder_data_deterministic` | 固定 seed → 同 58 符号 |
| encode_message 占位 | `test_encode_message_not_implemented` | 调用即 NotImplementedError |
| interleaved 序列化 | `test_iq_to_interleaved` | [I,Q,I,Q] float32 正确 |
| 格雷 round-trip | `test_gray_map_roundtrip` | word→tone→word 自洽，tone 为 0..7 排列 |
| 无噪 round-trip | `test_sync_roundtrip_noiseless` | 79 符号逐符号相等、时偏=0 |
| ±50 Hz 频偏 | `test_sync_freq_offset[±50]` | 估计误差 < 1.6 Hz，符号全对 |
| 时偏对齐 | `test_sync_time_offset[0/1920/4800/9600]` | 检出时偏样本数精确，符号全对 |
| S7 位置检测 | `test_s7_position_detected` | 三段 Costas 序列在正确符号位置被检出 |
| AWGN 检出率 | `test_sync_awgn_detection_rate[10/0 dB]` | 8 trials × ±52 Hz/随机放置 → 100% 逐符号检出 |
| 纯噪声空态 | `test_sync_pure_noise_honest_empty` | 20 窗纯噪声全部 `synced=False`、空符号、0 虚警 |

> 说明：格雷表 `[0,1,3,2,5,6,4,7]` 并非严格相邻单比特格雷链（机制笔记 §1 的
> "相邻音差 1 bit" 是宽松表述）；本轮诚实只断言协议表值 + 正逆映射自洽，
> 不硬套严格格雷邻接断言。

---

## 3. 实验 CSV 结论（experiments/exp_ft8_step1.py）

固定主种子 20261010，每格 trials=20，随机频偏 ±80 Hz + 随机时偏放置 + AWGN，
成功率配 Wilson 95% CI（`experiments/common/runner.py`）。

输出：
- `paper/experiments/ft8_step1_sync_vs_snr.csv`
- `paper/experiments/ft8_step1_false_alarm.csv`

SNR 曲线要点（逐符号精确检出率）：

| SNR dB | 同步率 | 逐符号精确率 | Wilson 95% CI | 频偏误差 mean (Hz) |
|-------:|------:|------------:|--------------:|------------------:|
| -20 | 1.00 | 0.95 | [0.76, 0.99] | 0.71 |
| -15 | 1.00 | 1.00 | [0.84, 1.00] | 0.75 |
| -10 | 1.00 | 1.00 | [0.84, 1.00] | 0.94 |
| -5  | 1.00 | 1.00 | [0.84, 1.00] | 0.82 |
| 0   | 1.00 | 1.00 | [0.84, 1.00] | 0.91 |
| +10 | 1.00 | 1.00 | [0.84, 1.00] | 0.81 |
| +20 | 1.00 | 1.00 | [0.84, 1.00] | 0.76 |

- **-15 dB 起逐符号精确检出 100%**；-20 dB 仍 95%（Wilson 下界 0.76）；
- 频偏估计误差均值 ~0.7-0.9 Hz，稳定在 bin 半宽 1.56 Hz 以内；
- 时偏误差均值 < 30 样本（远小于 1920 符号长）；
- **纯噪声虚警 0/50**（Wilson 上界 0.071）——诚实空态成立。

> 注意：本轮是**符号层**闭环（合成 8-FSK 波形 → Costas 同步 → 逐符号还原），
> 不是消息解码；-20 dB 的"逐符号精确"是合成理想信道 + 匹配滤波的结果，
> 与 wsjtx 真实链路的 -20 dB 消息解码门限不可直接类比（后者还含 LDPC/CRC）。

---

## 4. 红线核对

- [x] 干净室：MIT 头，未抄 wsjtx GPL 代码；常量为公开协议事实；
- [x] GPL 措辞中立：无活动竞赛类字样；活动参数只进 docs；
- [x] 不预置呼号：示例占位 `CALL1`（仅在 NotImplementedError docstring 出现）；
- [x] 未碰 `cpp/*`、`cpp/tests/*`、`ui_diag_freeze.cpp`、`cpp/scratch/*`；
- [x] 未 git add/commit/push；
- [x] 全程 CLI，无 GUI；只接合成数据，无 mock 解码。

---

## 5. 未完成项 / 第②步衔接

1. `encode_message()` 实现：pack77 → CRC14(0x6757) → LDPC(174,91) → 每 3 bit 格雷选音；
2. log-domain BP 解码（tan h/atanh）+ CRC14 早停；
3. 77-bit unpack（i5bit=0 标准消息：呼号/网格/报告）；
4. 加噪扫 SNR 到**消息解码**级 50% 门限（预期 ~ -20 dB 量级）；
5. C++ 移植（轮次 3）前，本 Python 模块作为参考基准。

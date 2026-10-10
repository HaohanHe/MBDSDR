# LRPT 第①步：QPSK 解调 + 帧同步（落地对照）

> 日期：2026-10-10  作者：MBDSDR 工程轮（phase63）
> 配套：`weather-sat-digital-study.md`（机制学习）、`weather-sat-landing-path.md`（落地路径）
> 本步范围：Python 原型——QPSK 复基带合成 + 解调 + 64-bit 帧同步；**不做 Viterbi/RS**（第②步）。

---

## 1. 落地文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `mbdsdr_ai/lrpt_modem.py` | 497 | 调制器 + 解调器 + 帧同步（干净室 MIT，纯 NumPy） |
| `mbdsdr_ai/tests/test_lrpt_modem.py` | 194 | 36 个确定性 pytest（镜像 test_ft8_modem.py 风格） |

---

## 2. 机制落地对照（笔记 file:line → 实现 file:line）

### 2.1 调制器（LrptModulator）

| 机制（笔记证据） | 实现位置 | 自述 |
|-----------------|---------|------|
| LRPT 72 ksym/s（main.c:19） | `lrpt_modem.py:37` `SYM_RATE=72_000` | 符号率常量 |
| 64-bit 同步字（lrpt_decoder.cpp:201） | `lrpt_modem.py:48` `LRPT_SYNC_WORD=0xFCA2B63DB00D9794` | SatDump 非差分支（NRZ-L） |
| QPSK 位→符号（correlator.cpp:80 硬判决约定） | `lrpt_modem.py:112` `bits_to_symbols()` | bit=1→+1, bit=0→-1，(±1±j)/√2 |
| 零阶保持上采样（原型；真实 RRC α=0.6） | `lrpt_modem.py:179` `iq = np.repeat(sym, self.sps)` | 矩形脉冲原型，第②步换 RRC |
| 频偏注入（复数乘 exp(j2πft)） | `lrpt_modem.py:185` | 可注入任意 Hz |
| 时偏注入（帧前补零） | `lrpt_modem.py:189` | 样本数 |
| AWGN 注入（固定 seed） | `lrpt_modem.py:194-200` | `np.random.default_rng(noise_seed)` |
| IQ→interleaved float32（SigMF/C++ 消费） | `lrpt_modem.py:61` `iq_to_interleaved()` | 镜像 ft8_modem.py:83 |

### 2.2 解调器（LrptDemodulator）

| 机制（笔记证据） | 实现位置 | 自述 |
|-----------------|---------|------|
| AGC（RMS 归一化） | `lrpt_modem.py:314` `_agc()` | 原型；真实慢环 AGC |
| QPSK Costas 误差检测（pll.c:117-118） | `lrpt_modem.py:338` `_costas_track_symbols()` | e = tanh(I)·Q − tanh(Q)·I |
| 二阶 Costas 环系数（ζ=0.707） | `lrpt_modem.py:261-267` | alpha/beta 由 bw 解析 |
| Gardner TED（demod.c:192-200） | `lrpt_modem.py:362` `_gardner_sample()` | 机制自述；本轮前馈试所有 sps 偏移（见下） |
| QPSK 8 相位假设相关（correlator.cpp:54-63） | `lrpt_modem.py:295` `_build_sync_hypotheses()` | 4 相位 × I/Q swap = 8 假设 |
| 64-bit 滑窗汉明相关 | `lrpt_modem.py:403` `_find_frames()` | 滑窗 ≤ max_hamming=4 即候选 |
| 硬判决（I/Q 符号位 → bit） | `lrpt_modem.py:395` `_symbols_to_bits()` | bit=1 当 I/Q>0 |
| 4 次方法粗 CFO（QPSK 去调制） | `lrpt_modem.py:322` `_estimate_cfo_4thpower()` | iq^4 → FFT 峰值 → ÷4 |

### 2.3 与笔记的诚实偏差

| 笔记机制 | 本轮实现 | 偏差原因 |
|---------|---------|---------|
| 闭环 Gardner TED 定时恢复 | 前馈：试所有 sps=8 个偏移，选同步最佳者 | 原型用矩形脉冲（零阶保持），任何符号内采样点等价；闭环 Gardner 在 RRC 脉冲下才有意义。`_gardner_sample()` 已写但未在主流程使用，第②步换 RRC 时启用。 |
| 逐样本 Costas 环 | 4 次方法 FFT 粗 CFO + 前馈同步 | 矩形脉冲下逐样本 Costas 误差信号太弱；4 次方法更鲁棒。符号速率 Costas（`_costas_track_symbols`）已备，第②步细调。 |

---

## 3. 测试结论（pytest 36/36 通过）

```
36 passed, 1 warning in 3.42s
```

| 测试类 | 用例数 | 结论 |
|--------|-------|------|
| TestFrameStructure | 5 | 同步字常量、位长、IQ shape、interleaved 全部正确 |
| TestRoundTrip | 3 | 无噪 round-trip 0 汉明距；同 seed 逐样本一致；不同 seed 噪声不同 |
| TestFrequencyOffset | 9 | **±50 kHz 频偏全部捕获**（4 次方法 CFO 估计） |
| TestTimeOffset | 7 | 时偏 0..256 样本全部同步，符号索引误差 ≤1 |
| TestPhaseOffset | 5 | 0°/30°/90°/180°/270° 全部同步（8 假设覆盖相位模糊） |
| TestNoiseTolerance | 4 | SNR ≥ 5 dB 可同步（hamming ≤ 4） |
| TestFalseAlarm | 3 | 纯噪声 5 trial 0 虚警；空输入/过短输入诚实空态 |

### 关键指标实测

| 指标 | 值 |
|------|----|
| 频偏捕获范围 | **±50 kHz**（4 次方法 FFT CFO） |
| 时偏容忍 | 0..256 样本（≈ 0..32 符号） |
| 相位模糊 | 0/90/180/270° + I/Q swap（8 假设） |
| SNR 同步门限 | ~5 dB（hamming ≤4）；0 dB 以下漏检 |
| 纯噪声虚警 | 0/5 trial（max_hamming=4） |

---

## 4. 诚实边界

1. **不做 Viterbi / RS / 解扰**：本轮帧同步后输出的字节流**含错误位**，未纠错。
   `FrameCandidate.frame_bytes` 是"同步字后的原始位"，不代表已纠错数据。
2. **与真实 LRPT 不直接类比**：合成信号是矩形脉冲 + 理想信道，真实 LRPT 用
   RRC α=0.6 成形、多径、多普勒；本轮全合成闭环，`data-origin: synthetic`。
3. **前馈定时 ≠ 闭环 Gardner**：`_gardner_sample()` 已写但主流程用前馈试偏移；
   换 RRC 脉冲后必须切回闭环 Gardner（否则符号间干扰会导致误码率上升）。
4. **Costas 环未在主流程启用**：`_costas_track_symbols()` 已备但本轮靠 4 次方法
   粗 CFO + 前馈同步闭环；细载波跟踪留第②步。
5. **纯噪声 0 虚警是 max_hamming=4 的结果**：放宽到 19（SatDump 阈值）会增虚警；
   生产需配合 RS 纠错判帧（见笔记 §2.2 锁帧=RS 可纠）。

---

## 5. 红线与纪律

- **干净室**：lrpt_modem.py 自写 MIT，未复制 SatDump/meteor_demod/goestools 代码；
  file:line 仅作机制证据。
- **不预置呼号**：占位 payload 用 PRNG，无呼号。
- **不发射**：纯被动接收合成。
- **不 git add/commit/push**：仅写文件。
- **隔离文件勿动**：未碰 `cpp/*`、`ui_diag_freeze.cpp`、`cpp/scratch/*`。
- **无 cpp 改动** → 双构型基线 D98/R98 139/139 保持有效（如实标注）。

---

## 6. 第②步衔接

第②步目标：**Viterbi K7 r1/2 软判决 + CCSDS 解扰 + RS(255,223)×4**。

衔接点：
1. **解调输出升级为软 I/Q**：本轮硬判决 → 第②步保留符号级软值（-127..+127），
   喂 Viterbi ACS。
2. **换 RRC 脉冲成形**：合成器 `np.repeat` 改 RRC FIR（α=0.6，order 64，sps=4~8），
   同步切闭环 Gardner。
3. **Viterbi K7 r1/2**：多项式 {79,109}（笔记 §2.2），64 状态 ACS + 回溯，
   软判决输入。
4. **CCSDS 解扰**：PN x^8+x^7+x^5+x^3+1 init 0xff，1020 字节表（笔记 §4.2）。
5. **RS(255,223)×4 交织**：复用本仓 `mbdsdr_ai/fec.py:ReedSolomon`（已有 MIT 实现）。
6. **锁帧判据升级**：hamming ≤4 → RS 4 块全可纠（笔记 §2.2:254）。
7. **实验衔接**：接入 `experiments/common/runner.py` FixedSeed + Wilson CI，
   画 Viterbi 后 BER vs Eb/N0 曲线（笔记落地路径 §3）。

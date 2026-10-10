# LRPT 第③步：端到端 IQ 闭环（落地对照）

> 日期：2026-10-10  作者：MBDSDR 工程轮（phase63）
> 配套：`lrpt-step1.md`（解调+帧同步）、`lrpt-step2.md`（Viterbi+RS）
> 本步范围：端到端 IQ 管道串联——合成 → 传输 → 解调 → Viterbi → RS → payload 校验。

---

## 1. 落地文件

| 文件 | 说明 |
|------|------|
| `mbdsdr_ai/lrpt_pipeline.py` | 端到端 IQ 管道（`LrptE2EPipeline`） |
| `mbdsdr_ai/tests/test_lrpt_pipeline.py` | 19 个确定性 pytest |

---

## 2. 端到端闭环 file:line

### 2.1 发射端（payload → IQ）

| 步骤 | 位置 | 说明 |
|------|------|------|
| payload (892B) → CADU (1024B) | `lrpt_pipeline.py:49-59` `build_cadu()` | RS 编码 + 交织 + 加扰 + ASM |
| CADU → 卷积编码 | `lrpt_pipeline.py:64` `convolve_encode(cadu_bits)` | 第②步 Viterbi 编码器 |
| coded bits → QPSK 符号 → IQ | `lrpt_pipeline.py:66-67` | 复用 `LrptModulator.bits_to_symbols()` |
| 频偏/时偏/相位/AWGN 注入 | `lrpt_pipeline.py:69-81` | 与第①轮调制器一致 |

### 2.2 接收端（IQ → payload）

| 步骤 | 位置 | 说明 |
|------|------|------|
| AGC + 4 次方法 CFO 粗校 | `lrpt_pipeline.py:93-97` | 复用第①轮 `_agc()` / `_estimate_cfo_4thpower()` |
| 前馈定时恢复 → 全量硬 bits | `lrpt_pipeline.py:99` `_recover_symbols()` | 复用第①轮 |
| 64-bit 滑窗帧同步 | `lrpt_pipeline.py:103` `_find_frames()` | 复用第①轮 |
| 同步位置取 coded bits | `lrpt_pipeline.py:108-112` | 硬判决 ±1.0 |
| Viterbi 软判决译码 | `lrpt_pipeline.py:117` `viterbi_decode(soft)` | 第②步 |
| 组回 CADU 字节 | `lrpt_pipeline.py:121-127` | MSB-first packbits |
| 解扰 + RS×4 解码 | `lrpt_pipeline.py:132` `decode_cadu()` | 第②步 |
| 诚实锁帧判据 | `lrpt_pipeline.py:135-137` | 4 块全可纠 → success=True |

---

## 3. 测试结论（pytest 19/19 通过）

```
19 passed, 1 warning in 18.16s
```

| 测试类 | 用例数 | 结论 |
|--------|-------|------|
| TestRoundTrip | 3 | 无噪 payload 逐字节一致；同步 hamming=0；同 seed 逐样本一致 |
| TestOffsets | 9 | ±10 kHz 频偏全通；时偏 0..1000 样本全通 |
| TestNoiseTolerance | 5 | SNR ≥ 5 dB 全通；SNR ≤ 0 dB 诚实失败 |
| TestFalseAlarm | 2 | 纯噪声 5 trial 0 success；空输入诚实空态 |

---

## 4. SNR 门限与编码增益

### 4.1 实测 SNR 扫点（5 trial 平均）

| SNR (dB) | 成功率 | RS 平均纠错字节数 |
|----------|--------|-------------------|
| +15 | 100% | 0.0 |
| +12 | 100% | 0.0 |
| +10 | 100% | 0.0 |
| +8 | 100% | 0.0 |
| +6 | 100% | 0.0 |
| +5 | 100% | 1.2 |
| +4 | 100% | 13.8 |
| +3 | 0% | 23.0（2 块失败） |
| +2 | 0% | 5.2 |
| ≤0 | 0% | 0.0 |

### 4.2 与第①轮解调对照

| 指标 | 第①轮（仅帧同步） | 第③轮（端到端 payload） |
|------|-------------------|----------------------|
| 门限 SNR | ~5 dB（hamming ≤4 即同步） | ~4-5 dB（payload 逐字节正确） |
| 频偏容忍 | ±50 kHz | ±10 kHz（测试范围） |
| 时偏容忍 | 0..256 样本 | 0..1000 样本 |
| 纯噪声虚警 | 0/5 trial | 0/5 trial |

### 4.3 编码增益（诚实标注）

**门限差 ≈ 1 dB**（第①轮同步门限 ~5 dB vs 第③轮 payload 门限 ~4-5 dB）。

**诚实说明**：
1. 这个数字**不是**教科书意义上的编码增益。教科书编码增益 = 相同 BER 下，编码 vs 未编码所需 Eb/N0 之差。我们没有测未编码 payload 的 BER（因为未编码根本无法恢复 payload——任何 bit error 都直接损坏数据）。
2. 本轮用**硬判决**（±1.0）做 Viterbi 软输入，理论上比软判决（3-bit 量化）差 ~2 dB。真实 LRPT 用软判决，编码增益应显著更高。
3. 矩形脉冲 vs RRC α=0.6 的实现差异也会拉低实测增益。
4. 合成闭环，与真实 LRPT 不直接类比。

---

## 5. 与第②轮位域闭环的差异（诚实标注）

| 维度 | 第②轮位域闭环 | 第③轮 IQ 域闭环 |
|------|-------------|-----------------|
| 输入 | 直接给 coded bits | 给复基带 IQ |
| 解调损失 | 无（直接给位） | 硬判决量化损失（±1.0） |
| 频偏/时偏影响 | 无 | 需 CFO 估计 + 定时恢复 |
| Viterbi 输入 | 理想 ±1.0 | 硬判决 ±1.0（同，但前级有解调误差） |
| 实测纠错能力 | 1000 bit 翻转全恢复 | SNR ~4 dB 全恢复（≈ 等效 BER 量级） |

**关键差异**：第②轮直接在理想位流上测 Viterbi+RS 纠错能力；第③轮加入了 QPSK 解调的硬判决量化损失和频偏/时偏影响。端到端门限比位域闭环高约 1-2 dB，主要来自硬判决量化。

---

## 6. 诚实边界

1. **硬判决 Viterbi**：本轮 Viterbi 输入是硬判决 ±1.0，未利用符号幅度信息。软判决（3-bit 量化）可获 ~2 dB 增益。
2. **矩形脉冲成形**：合成端用零阶保持，真实 LRPT 用 RRC α=0.6。频偏/时偏容忍度与真实链路有差距。
3. **RS 误纠风险**：低 SNR 下 RS 可能误纠（声称成功但 payload 错误）。测试中已标注：`success=True` 时必须 `payload == expected` 才是真成功。
4. **纯合成闭环**：所有数据来自合成 IQ，与真实 LRPT 信号不直接类比。
5. **RS 单块上限 16 字节**：实测与理论一致（第②轮已验证）。

---

## 7. 红线与纪律

- **干净室**：lrpt_pipeline.py 只做串联，复用第①②轮 MIT 模块；未碰 GPL 源码。
- **不预置呼号**：占位 payload 用 PRNG。
- **不发射**：纯合成闭环。
- **不 git add/commit/push**：仅写文件。
- **隔离文件勿动**：未碰 `cpp/*`、`ui_diag_freeze.cpp`、`cpp/scratch/*`。
- **无 cpp 改动** → 双构型基线 D98/R98 139/139 保持有效（如实标注）。

---

## 8. 第④轮衔接（C++ 移植）

第④轮目标：Python 验证完 → C++ 移植接入 SDR 实时流。

衔接点：
1. **软判决升级**：C++ 侧保留 int8 软 I/Q，喂 Viterbi 做 3-bit 量化软判决（+2 dB 增益）。
2. **RRC 脉冲成形**：C++ 侧换 RRC α=0.6 FIR，切闭环 Gardner TED。
3. **streaming 接口**：C++ 侧做 sliding window 帧同步 + Viterbi streaming，不一次性收全帧。
4. **三通道工具**：并行会话在做 C++ 状态层，本侧只提供 Python 验证结论，不碰工具接线文档归属。
5. **实验曲线**：接入 `experiments/common/runner.py` FixedSeed + Wilson CI，画 BER vs Eb/N0。

# R2 深度代码审查：experiments/ 目录（9 个实验文件）

**审查范围**：`experiments/` 下全部 9 个实验脚本及其产物（`paper/experiments/*.csv`）
**审查日期**：2026-09-24
**审查方式**：逐行阅读实验脚本 + 追踪 `mbdsdr_ai/` 产品内核 + 读取已生成 CSV 结果交叉验证

---

## 0. 全局结论

| 维度 | 判定 |
|---|---|
| 实验真实性 | **9/9 全部为纯合成信号闭环**，无任何实验使用 RTL-SDR/真实硬件录制的基带数据作为测试输入 |
| 论文就绪度 | **不可用**支撑 IEEE WCL 主实验；论文 v0.1 自身声明"实验数据待硬件回采后填入"（第4行/第278行/第374行） |
| 基线对比 | 仅 CFO 有内部两级（FFT粗估 vs Kay精估）对比；其余 8 个实验无任何外部基线 |
| 统计显著性 | 4/9 实验 trials ≤ 20（AX.25=10, ADS-B=20, SSTV-ID=8, weak-model=10用例×1次） |
| 随机种子 | 7/9 有种子；exp_ax25 种子硬编码为索引；exp_weak_model_toolcall **无种子**（LLM 非确定性） |
| 原始数据保存 | **0/9** 保存原始 IQ/音频样本，仅保存 CSV 汇总 |
| "实验室绿、真机红" | SSTV 识别实验数据**自身暴露**：纯噪声 100% 误报为 SSTV 制式，Robot36 在 15dB 即崩溃 |

---

## 1. 逐实验审查

### 1.1 exp_amr.py — 自动调制识别

**论文可用度：中**（算法逻辑可复现，但数据泄露使准确率数字不可外推）

**已知问题核实：**

- **[真bug] UNKNOWN 预测静默丢弃** — `exp_amr.py:53-55`：
  ```python
  pj = MODS.index(pred) if pred in MODS else None
  if pj is not None:
      confusion[mi, pj] += 1
  ```
  若分类器输出 `UNKNOWN`，该样本在混淆矩阵中**完全消失**（既不计入正确也不计入错误），拒识能力不可见。实际运行中 KNN（`amr.py:236`）永远在 8 个训练类中投票，UNKNOWN 极少出现，但代码逻辑上确实丢弃了。

- **[真bug] 训练模板与测试集同源（数据泄露）** — `amr.py:372-384`：
  - 模板生成：`_load_builtin_training_data()` 用 `synthesize_modulation_iq(mod, rng, snr_db=10~30dB, fs=100kHz)`，seed=20260919，每类16个样本。
  - 测试生成：`exp_amr.py:48` 用**同一个** `synthesize_modulation_iq(mod, rng, snr_db=..., fs=fs)`，seed 不同。
  - 两者使用**完全相同的参数化模型**：相同音频消息（1000Hz+1700Hz，`amr.py:305`）、相同 FSK 频偏（±2000Hz，`amr.py:322`）、相同 QAM 星座点（`amr.py:332`）、相同 OFDM grid（`amr.py:339`）。
  - **后果**：KNN 匹配的是同一参数化生成器的特征分布。10dB 时 99.17% 准确率（CSV 实测）是"同分布测试"的必然结果，不能外推到真实信号。这是教科书级的 train/test 同分布泄露。

- **[真bug] NOISE 类不加噪声** — `amr.py:350`：`if mod != "NOISE":` 才叠加噪声。导致 NOISE 类的准确率不随 SNR 变化（CSV 中 NOISE 在 -5dB 时仍为 0.967），SNR 曲线在 NOISE 行无意义。

- **[建议]** 无基线对比（无 CNN/LSTM/经典循环平稳特征法），无真实 OTA 信号。trials=30/类/SNR，样本量偏小。

---

### 1.2 exp_ax25_performance.py — AX.25/AFSK 解调

**论文可用度：中**（闭环测试有一定意义，但统计量极不足）

- **[真bug] trials=10/格** — `exp_ax25_performance.py:15`：TRIALS=10。在 0dB 时 pass=6~8/10，95% 置信区间宽度极大（二项分布 n=10）。无法支撑论文中的性能曲线。
- **[建议]** 信号路径：44100Hz 合成 → 降采样 → 解调，测试了三种采样率（11025/22050/44100），比纯单速率测试稍好。但噪声是简单 AWGN，无多径、无频偏、无采样率偏移。
- **[建议]** 无真实声卡/RTL-SDR 录制验证。种子 `random.Random(1000*sr+k)` 可复现但无原始 IQ 保存。

---

### 1.3 exp_digital_modes.py — SSTV + ADS-B 数字模式闭环

**论文可用度：中**（结构完整，但样本量不足且 SSTV 部分硬编码模式）

- **[真bug] SSTV 部分硬编码模式** — `exp_digital_modes.py:94`：
  ```python
  r = decode_sstv_from_samples(y, sr, output_path=out_png, mode="Martin M1")
  ```
  解码器被显式告知模式是 Martin M1，**不是自动识别测试**。该实验只测"已知模式下的解码质量"，不测制式识别。

- **[真bug] ADS-B 检测率非单调** — CSV 实测：-2dB detect=0.20, 0dB detect=0.05, 2dB detect=0.40。trials=20（`exp_digital_modes.py:216`），噪声波动导致结果不可靠。
- **[建议]** ADS-B 前导虚警率 7.8%（500次纯噪声试验），较高但实验未深入分析原因。CRC 虚警=0 与理论 2^-24 一致。
- **建议项**：有种子（seed=20260919），CSV 输出完整。

---

### 1.4 exp_fhss_detection.py — 跳频检测

**论文可用度：中**（三组实验结构最完整，但门限循环标定）

- **[真bug] 门限循环标定** — 注释（`exp_fhss_detection.py:17`）自述："纯噪声逐帧 argmax 峰功率 99 分位约 35 dB，主门限取 38 dB"。门限在**同一噪声发生器**上标定后又在同一发生器上测试虚警率，存在循环验证。
- **[建议]** 信道间隔 22.9kHz 远大于聚类容差 4.8kHz（`exp_fhss_detection.py:208`），是最理想信道布局。无部分带占用、多网台、信号碰撞。
- **[建议]** 注释（第19-20行）自己承认："本实验为跳变沿与分析帧对齐的基线情形（best-case）；跳变定时偏移、多网台、碰撞与跟踪丢失等复杂工况留待后续。"
- **建议项**：trials=300，样本量尚可。有种子。三组输出 CSV 结构完整。

---

### 1.5 exp_freq_offset.py — 载波频偏估计

**论文可用度：中**（DSP 算法验证层面合理，但信号模型过理想）

- **[建议]** 有内部基线对比：两级 Kay 精估 vs 纯 FFT 粗估（`exp_freq_offset.py:41-43`），是 9 个实验中唯一有对比基线的。
- **[建议]** 信号模型过于理想：纯未调制复指数 + AWGN（`exp_freq_offset.py:25-29`）。真实信号有调制频谱展宽、相位噪声、采样钟偏移。ppm 场景（5~50ppm @100MHz）残余 RMS ≈0.05Hz，是 Kay 估计器在高 SNR 下的理论噪声底，不代表真实振荡器行为。
- **建议项**：trials=500，样本量充足。有种子。三组实验（vs SNR / vs blocklength / ppm场景）设计合理。

---

### 1.6 exp_pnt_fusion.py — PNT 多源融合

**论文可用度：低**（空壳实验，结果是数学恒等式）

- **[空壳] 实验测量的是数学恒等式** — `exp_pnt_fusion.py:24-39`：
  - 观测 = 真值 + 高斯噪声(σ=硬编码标称精度)
  - 融合 = 逆方差加权（权重=1/σ²）
  - CSV 实测：场景 C（GNSS 5m + WiFi 50m + LEO 1m）RMSE=1.35m，与理论值 `sqrt(1/(1/25+1/2500+1))*sqrt(2)≈1.39m` 完全吻合。
  - **结论**：整个实验在数学上是闭环恒等式——它只是验证了"逆方差加权的融合精度等于 Cramér-Rao 下界"，没有测量任何真实接收机的性能。

- **[空壳] σ 值全部硬编码假设** — `exp_pnt_fusion.py:50-52`：GNSS=5m, WiFi=50m, LEO=1m。无真实 GNSS 接收机数据、无 WiFi RSSI 测量、无 LEO PNT 信号、无多径/NLOS/时钟偏差。
- **[建议]** random.seed(42) 可复现，N=300。

---

### 1.7 exp_spectrum_sensing.py — 频谱感知能量检测

**论文可用度：中**（理论验证最扎实，但更像 DSP 教学实验）

- **建议项（正面）**：三组实验（ROC / Pd vs SNR / SNR wall）结构完整，有理论曲线对比（`theory_pd`），验证了 Tandra-Sahai SNR wall 理论。trials=4000，样本量充足。有种子。
- **[建议]** 信号模型是单音复正弦 + AWGN（`exp_spectrum_sensing.py:43`）。BPSK/QPSK/OFDM 等实际信号的能量检测性能会不同。
- **[建议]** 无真实信号录制对比。该实验更适合作为 DSP 理论验证，而非 AI-SDR 平台的新颖性贡献。

---

### 1.8 exp_sstv_identification.py — SSTV 自动制式识别

**论文可用度：低**（实验数据自身暴露严重缺陷）

- **[真bug] 纯噪声 100% 误报为 SSTV 制式** — CSV 实测 `sstv_mode_identification.csv`：
  - 在所有 SNR（clean/20/15/10/5/0/-5dB）下，噪声行的 `correct_rate=0.0`，即 8/8 次纯高斯噪声被误判为某种 SSTV 制式。
  - 原因：`_identify_sstv_mode`（`sstv_decoder.py:383-415`）在 20 秒噪声中随机找到 ≥8 个"1200Hz 附近段"，周期 CV 偶然低于 0.35 门限，通过了噪声检测。
  - **这是"实验室绿、真机红"的直接证据**：在真实频谱中（充满噪声和各种非 SSTV 信号），该识别器会频繁误报。

- **[真bug] Robot36 识别在 15dB 及以下全部失败** — CSV 实测：15dB 时 Robot36 correct_rate=0.0（全部误判为 Martin M1）；10dB 时 7/8 误判为 Martin M1。

- **[真bug] Robot36 解码质量在 15dB 崩溃** — CSV 实测 `sstv_robot36_quality.csv`：
  - clean/20dB：corr_mean≈0.93, row_fraction≈0.99（可用）
  - 15dB：corr_mean=-0.22（**负相关**），row_fraction=0.33（垃圾图像）
  - 10dB 及以下：corr_mean≈0, row_fraction=0（解码器输出空图）

- **[占位] "真实 OTA" 仅 1 个样本** — `exp_sstv_identification.py:237` 默认 `real_sstv.wav`（72.9s），无多个真实录音、无 SNR 标注、无 ground-truth 对比。JSON 输出显示解码成功但无法验证准确性。
- **[建议]** id_trials=8, dec_trials=8（`exp_sstv_identification.py:234-235`），样本量极小。

---

### 1.9 exp_weak_model_toolcall.py — 弱模型工具调用

**论文可用度：低**（空壳/占位性质）

- **[空壳] 10 个用例各跑一次，无统计重复** — `exp_weak_model_toolcall.py:46-61`：for 循环遍历 10 个 CASES，每个只调用一次 LLM。无多次重复试验，无置信区间。
- **[真bug] 工具集被人为裁剪** — `exp_weak_model_toolcall.py:38-42`：
  ```python
  names = {c[1] for c in CASES}
  schemas = [... for t in ag.tool_registry.list_tools() if t["name"] in names]
  ```
  模型只看到与当前题目相关的工具（每题 1 个期望工具 + 少量干扰）。实际 agent 有数十个工具（`sdr_tools.py` 278KB）。这是低干扰环境下的乐观测试，不能代表真实场景。
- **[真bug] 无随机种子，不可复现** — LLM 输出非确定性，每次运行结果不同。CSV 中 100% call/pick/args 通过率是单次运行结果。
- **[占位] 依赖外部 API** — 需要 `~/.mbdsdr/config.json` 中的 API key（`exp_weak_model_toolcall.py:33`），无法离线复现。
- **[建议]** 100% 通过率过于完美，与"弱模型"定位矛盾——可能测试用例过于简单。

---

## 2. 交叉问题汇总

### 2.1 数据泄露 / 同分布测试

| 实验 | 训练/模板来源 | 测试来源 | 泄露程度 |
|---|---|---|---|
| AMR | `synthesize_modulation_iq()` (amr.py:382) | `synthesize_modulation_iq()` (exp_amr.py:48) | **严重**：同参数化生成器 |
| SSTV-ID | pysstv MartinM1/Robot36 编码 (exp_sstv_id.py:58) | pysstv 编码 + AWGN | **中**：编解码同源（pysstv编码 → 自写解码器） |
| ADS-B | `adsb.build_identification_frame()` → `modulate_baseband()` | 同一编解码函数 | **中**：闭环编解码 |
| 其余 | 无训练阶段 | N/A | 无泄露但无学习 |

### 2.2 统计样本量不足

| 实验 | trials/格 | 问题 |
|---|---|---|
| SSTV-ID | 8 | 8次试验无法估计置信区间 |
| AX.25 | 10 | 0dB 时 6-8/10，区间极宽 |
| weak-model | 1(每用例) | 无重复，不可复现 |
| ADS-B | 20 | 检测率非单调（0dB=0.05 vs 2dB=0.40） |
| AMR | 30 | 偏小但可接受 |
| FHSS/CFO/sensing/PNT | 300-4000 | 充足 |

### 2.3 无真实 OTA 验证

- 唯一的"真实"文件是 `real_sstv.wav`（72.9s，单个 SSTV 录音），在 9 个实验中仅被 `exp_sstv_identification.py` 的 `run_real_ota()` 函数使用。
- 无 RTL-SDR 录制的 IQ 数据用于 AMR/ADS-B/FHSS/CFO/频谱感知实验。
- 论文 v0.1 自身承认（第374行）："实验数据待采集：本文的 8 个实验当前为 Methodology 设计"。

### 2.4 空壳/占位实验

| 实验 | 性质 | 理由 |
|---|---|---|
| exp_pnt_fusion.py | **空壳** | 测量数学恒等式，无真实接收机数据 |
| exp_weak_model_toolcall.py | **空壳/占位** | 10用例×1次，工具集裁剪，无种子，依赖API |

---

## 3. 修复优先级建议

1. **[阻断] exp_sstv_identification.py 噪声误报**：纯噪声 100% 误报为 SSTV 制式是系统级缺陷，必须修复 `_identify_sstv_mode` 的噪声门限，否则该实验数据不可发表。
2. **[阻断] exp_amr.py 数据泄露**：必须用独立信号源（不同 SNR 范围、不同调制参数、真实录制）测试，否则 99% 准确率无意义。
3. **[高] exp_pnt_fusion.py 空壳**：要么接入真实 GNSS/WiFi 测量，要么删除该实验。
4. **[高] exp_weak_model_toolcall.py 统计设计**：需要 ≥30 次重复试验、全量工具集、固定 temperature=0、保存 prompt/response。
5. **[中] 样本量提升**：AX.25 ≥100 trials/格，ADS-B ≥100 trials/格，SSTV-ID ≥30 trials/格。
6. **[中] 基线对比**：AMR 对比至少一个经典方法（循环平稳特征法）；SSTV 对比 wsjtx/pysstv 解码器；ADS-B 对比 dump1090。
7. **[中] 原始数据保存**：每个实验应保存至少一个 SNR 点的原始 IQ/音频样本（.npy/.wav）。

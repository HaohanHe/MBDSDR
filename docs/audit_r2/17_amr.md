# 第二轮深度代码审查：`mbdsdr_ai/amr.py`（自动调制识别 AMR）

- 审查对象：`mbdsdr_ai/amr.py`（572 行）
- 关联实验：`experiments/exp_amr.py`（100 行）
- 实验产物：`paper/experiments/amr_accuracy_vs_snr.csv`、`amr_confusion_matrix_snr10.csv`
- 审查方式：只读，未修改任何代码

---

## 0. 总体结论

`amr.py` **不是空壳**：25 维特征全部真实计算（时域统计 + FFT 频谱矩 + 瞬时相位/频率），KNN 分类器纯 Python 可跑通，内置训练模板由 `synthesize_modulation_iq` 合成信号提取真实特征得到。

但它存在三类结构性问题：

1. **"实验室绿、真机红"风险极高**：训练模板与测试集来自**同一个合成器 `synthesize_modulation_iq`**、同一个 `fs=100 kHz`、训练 SNR 仅覆盖 10–30 dB。实验 CSV 显示 10 dB 混淆矩阵近似对角（99.2%），但 -5 dB 总体准确率仅 36.7%，AM/CW/PSK 在低 SNR 直接崩到 0%。真实电台信号带宽、符号率、成型滤波、CFO、采样率都与模拟器不同，KNN 不会有这种分布对齐。
2. **声称支持的调制类型与实际逻辑不符**：文档头声称 SSB / BPSK/QPSK / 16QAM/64QAM，但 `BUILTIN_MODULATIONS` 只有 8 类，`synthesize_modulation_iq` 里 PSK 实际是 BPSK、QAM 实际是 QPSK（4 点星座），SSB 直接 `raise ValueError`。
3. **缺少经典 AMR 特征族**：没有高阶累积量（CH_20/CH_21/CH_40/CH_41/CH_42/CH_61/CH_63）、没有小波/循环谱、没有眼图/星座子矩。25 维里有 2 维是冗余的（`center_frequency` 与 `carrier_offset` 完全相同）。

---

## 1. 特征提取审查（重点 1）

### 1.1 实际使用的特征（25 维，`AMRFeature`，line 52–98）

| 类别 | 维度 | 字段 | 计算位置 |
|---|---|---|---|
| 时域幅度 | 8 | mean/std/max/min/rms/PAPR/skew/kurt | line 403–417 |
| 瞬时相位/频率 | 4 | mean_phase/std_phase/mean_freq/std_freq | line 420–434 |
| 零交叉 / I-Q 相关 | 2 | zcr / iq_correlation | line 437–448 |
| 星座密度 | 1 | constellation_density | line 451–455 |
| 频谱矩 | 8 | centroid/spread/skew/kurt/flatness/rolloff/magnitude_cv | line 462–499 |
| 中心频率/带宽/偏移 | 3 | center_frequency / bandwidth / carrier_offset | line 458–459, 526 |

**未使用**：高阶累积量、循环谱、小波变换、Gabor、眼图、子载波数估计、符号率估计、成型滤波器滚降估计——这些才是工业界 AMR 区分 M-PSK/QAM 阶数的主力特征。

### 1.2 特征计算正确性问题

- **[真bug] `center_frequency` 与 `carrier_offset` 完全冗余**
  - `amr.py:458`：`center_freq = mean_freq / sample_rate`
  - `amr.py:526`：`carrier_offset = mean_freq / sample_rate if sample_rate > 0 else 0`
  - 两者公式、输入完全一致，25 维里实际只有 24 个独立特征。文档 line 84 把 `carrier_offset` 描述为"载波频率偏移（归一化）"，但代码里就是 `center_frequency` 的副本。

- **[真bug] 零交叉率只用 I 路实部**
  - `amr.py:437`：`(iq_samples[i-1].real >= 0) != (iq_samples[i].real >= 0)`
  - 复信号零交叉应判 `I[i-1]*I[i] < 0 或 Q[i-1]*Q[i] < 0`，或基于包络过零。当前实现对纯 Q 路信号（如 QPSK 的 Q 分量）漏检，zcr 对 PSK/QAM 的区分度被削弱。

- **[建议] `constellation_density` 阈值过粗**
  - `amr.py:452–455`：先按包络归一化，再 `round(real, 0.1)` 量化。0.1 的量化半径对 4 点 QPSK 会塌缩到 4 个点，但对 OFDM（高斯分布星座）会饱和到 ~100 个点。该特征**只在"离散星座 vs 连续高斯"之间有效**，无法区分 16QAM/64QAM，与文档 line 24 的声称不符。
  - 另外当 `n < 100` 时 `iq_samples[::max(1, n//100)]` 取全部点，`len(set)` 可能 >100，`constellation_density` 会 >1，未做截断。

- **[建议] `spectral_rolloff` 定义特殊**
  - `amr.py:491–495`：按 `|fn|` 排序累计 85% 能量，返回半径。这是"以 DC 为中心的能量半径"，不是经典频谱滚降点（85%–95% 带宽比）。对零中频复基带 OK，但变量名 `rolloff` 易误导。

- **[真bug] 训练 SNR 覆盖与测试 SNR 网格不匹配**
  - `amr.py:381`：`snr = 10.0 + 2.0 * (i % 11)`，i∈[0,15] → SNR ∈ {10,12,...,30} dB。
  - `exp_amr.py:29`：测试 SNR ∈ {-5,0,5,10,...,30} dB。
  - 训练集**完全没有** 0–5 dB 以下样本。CSV 显示 -5 dB 时 AM=0.00、CW=0.00、PSK=0.00，5 dB 时 FSK 仅 0.23——这是分布外泛化失败，不是"鲁棒性好"。

### 1.3 特征尺度

`KNNClassifier.fit`（line 191–201）用训练集 mean/std 做 z-score。但每类只有 16 个样本、共 128 个，归一化方差主要来自**类间差异**而非类内方差，欧氏距离实际退化为"类心距离"——本质是最近类心分类器（nearest-class centroid），不是真正的 KNN。k=5 投票在类内样本高度相似时几乎不产生分歧。

---

## 2. 分类器审查（重点 2）

### 2.1 分类器类型

- `KNNClassifier`（line 169–293）：纯 Python KNN，k=5，欧氏/曼哈顿/余弦三种距离。
- **不是** SVM / 决策树 / 神经网络 / 模板匹配库。
- 没有任何权重文件、`.pkl`、`.onnx`、HDF5——"模型"就是内存里 128 个 `TrainingSample` 对象。

### 2.2 训练数据来源

- `AMRClassifier._load_builtin_training_data`（line 372–384）：
  - 固定种子 `np.random.default_rng(20260919)`
  - 每类 16 个样本，共 8 类 = 128 个
  - 样本由 `synthesize_modulation_iq(mod, rng, snr_db=snr, fs=100_000)` 现场合成
- **没有任何真实采集数据**，没有 RadioML / RADIOML 2016.10a 等公开数据集，没有用户录音回放。

### 2.3 "增量学习"

- `add_training_sample`（line 565–568）/ `KNNClassifier.add_sample`（line 268–273）：运行时可加样本，但**无持久化**——进程退出即丢失，重启 `AMRClassifier()` 又回到 128 个内置模板。文档 line 15 声称"用户可以添加新的训练样本"属实，但没有 save/load 接口。

### 2.4 [真bug] `classify` 重复计算距离

- `amr.py:537` 调 `self.classifier.predict(feature)` 内部已经算了一次全部距离并排序；
- `amr.py:540–547` 又遍历 `self.classifier.samples` 再算一次距离取最近 5 个。
- 128 个样本 × 25 维，每次分类重复计算 2 倍距离，纯浪费。应在 `predict` 内同时返回 `distances` 切片。

---

## 3. 支持的调制类型审查（重点 3）

文档头 line 17–26 声称支持：AM / FM / SSB / CW / FSK / PSK(BPSK/QPSK) / QAM(16/64) / OFDM / NOISE。

实际：

| 声称 | `BUILTIN_MODULATIONS` (line 366) | `synthesize_modulation_iq` 实现 | 判定 |
|---|---|---|---|
| AM | ✅ | line 313–314，`(0.6+0.4*audio)` 实数包络 | 真 |
| FM | ✅ | line 315–316，复指数相位积分 | 真 |
| **SSB (USB/LSB)** | ❌ 不在列表 | line 345–346 `raise ValueError` | **[占位]** |
| CW | ✅ | line 317–318，恒定幅度常数 | 真（但这是未调制载波，不是莫尔斯 CW） |
| FSK | ✅ | line 319–323，2-FSK ±2 kHz | 真（仅 2-FSK，无 4FSK/MSK/GFSK） |
| PSK (BPSK/QPSK) | ✅ | line 324–327，**仅 BPSK**（±1 实数） | **[占位]** QPSK 未实现 |
| QAM (16/64) | ✅ | line 328–333，**实际是 QPSK**（`(±1±j)/√2`，4 点） | **[占位]** 16QAM/64QAM 未实现 |
| OFDM | ✅ | line 334–342，64 子载波 QPSK + CP | 真 |
| NOISE | ✅ | line 343–344，复高斯 | 真 |
| UNKNOWN | 枚举有 | 训练集无该类 | 兜底 |

### 3.1 [占位] SSB

- 文档 line 20 列了 SSB (USB/LSB)，但 `BUILTIN_MODULATIONS` 是 `("AM","FM","CW","FSK","PSK","QAM","OFDM","NOISE")`（line 366），不含 SSB。
- `synthesize_modulation_iq("SSB", ...)` 会走到 line 345 `raise ValueError("不支持的调制类型: SSB")`。
- 结果：任何输入信号都不可能被分类为 SSB，因为训练集里没有 SSB 模板，KNN 最近邻只会落到其余 8 类。**SSB 是纯文档占位。**

### 3.2 [占位] BPSK/QPSK、16QAM/64QAM 子型号

- 文档 line 23 "PSK（相移键控，BPSK/QPSK）"：代码 line 327 `np.where(bits==1, 1.0, -1.0)` 只有 BPSK。
- 文档 line 24 "QAM（正交幅度调制，16QAM/64QAM）"：代码 line 332 `((2*bi-1)+1j*(2*bq-1))/√2` 是 4 点 QPSK，不是 16/64QAM。
- 分类输出枚举只有 `ModulationType.PSK` / `ModulationType.QAM`，没有 `BPSK/QPSK/16QAM/64QAM` 子类。
- 结果：KNN 根本不可能区分 BPSK vs QPSK vs 16QAM——**子型号区分是空头支票**。

### 3.3 [建议] CW 语义错位

- line 317–318：`x = np.ones(n, complex)` 是连续未调制载波。
- 真实 CW（莫尔斯电码）是断续的载波（dit/dah），包络有通断键控（OOK）特征。当前实现的"CW"模板与单音连续波无法区分，对真实莫尔斯信号识别会失败。

---

## 4. "实验室绿、真机红"分析（重点 4）

### 4.1 实验产物实测（`paper/experiments/`）

`amr_accuracy_vs_snr.csv`：

| SNR dB | 总体 | AM | FM | CW | FSK | PSK | QAM | OFDM | NOISE |
|---|---|---|---|---|---|---|---|---|---|
| -5 | **0.367** | 0.00 | 0.83 | 0.00 | 0.97 | 0.00 | 0.03 | 0.13 | 0.97 |
| 0 | 0.504 | 0.27 | 1.00 | 0.00 | 0.77 | 0.00 | 0.30 | 0.77 | 0.93 |
| 5 | 0.808 | 1.00 | 1.00 | 1.00 | 0.23 | 0.70 | 0.57 | 1.00 | 0.97 |
| 10 | **0.992** | 1.00 | 1.00 | 1.00 | 0.97 | 1.00 | 1.00 | 1.00 | 0.97 |
| 20 | 0.992 | ... | | | | | | | |
| 30 | 0.979 | ... | | | | | | | |

`amr_confusion_matrix_snr10.csv` 近似对角，最大混淆是 FSK→QAM 3.3%、NOISE→OFDM 3.3%。

### 4.2 为什么这不能证明真机可用

1. **训练-测试同源**：训练模板来自 `synthesize_modulation_iq(seed=20260919)`，测试来自 `synthesize_modulation_iq(seed=40000+...)`。同一个函数、同样的 `fs=100 kHz`、同样的 `n=8192`、同样的脉冲成型（line 307–311 矩形滑动平均）、同样的音频调制信号（line 305 `0.6*sin(2π1000t)+0.3*sin(2π1700t)`）。
2. **参数固定无随机化**：
   - AM 调制音频固定 1000/1700 Hz（line 305）
   - FM 最大频偏固定 3000 Hz（line 316）
   - FSK 频偏固定 ±2000 Hz（line 322）
   - PSK/QAM 过采样率固定 16（line 325/329）
   - OFDM 子载波数固定 64、有效子载波 48、CP=16（line 335–339）
   - 真实电台这些参数会随信号变化，KNN 模板不会覆盖。
3. **采样率未做随机化**：训练与测试都是 `fs=100_000`。真机 USDR/RTL-SDR 常见采样率 1/2/2.048/2.4 MSps，特征 `center_frequency = mean_freq/fs` 归一化后理论上应无关，但 `spectral_rolloff`（line 495）返回的是归一化频率半径，对带宽占空比敏感；符号率/采样率比变化会改变 constellation_density。
4. **无 CFO、无相位噪声、无多径、无 IQ 不平衡**：模拟器只有加性高斯白噪声（line 352–354）。真实 SDR 前端有正交失真、直流泄漏、本振泄漏——这些会让 I/Q correlation（line 448）、spectral_centroid（line 470）偏移。
5. **低 SNR 已暴露脆弱**：-5 dB 总体 36.7%，3 个类完全崩。真机弱信号场景远多于高 SNR。

**结论**：实验证明的是"KNN 能把同一个模拟器的 8 种参数化输出分开"，**没有任何真机数据验证**。论文/白皮书中若引用 99.2%@10dB，必须加限定"在合成信号、fs=100 kHz、SNR≥10 dB 条件下"。

---

## 5. 代码量与占位比例（重点 5）

572 行分布（粗估）：

| 块 | 行范围 | 行数 | 性质 |
|---|---|---|---|
| 模块文档/枚举/dataclass | 1–166 | ~166 | 真实类型定义 |
| KNNClassifier | 169–293 | ~125 | 真实算法 |
| `synthesize_modulation_iq` | 296–355 | ~60 | 真实合成器（但只服务于模板） |
| AMRClassifier 外壳 | 358–572 | ~215 | 真实特征提取 + 分类调度 |
| **纯占位/未实现** | — | **~0 行** | 无 `raise NotImplementedError` |

- **没有空函数体**，没有 `TODO`/`FIXME`，没有 `pass` 占位。
- 所有函数都能跑通。
- "占位"主要体现在**文档声称 vs 实现不符**（SSB、BPSK/QPSK 子型号、16/64QAM），而不是代码骨架缺失。

---

## 6. 空壳/占位识别（重点 6）

| 位置 | 类型 | 说明 |
|---|---|---|
| `amr.py:20` 文档声称 SSB | **[占位]** | `BUILTIN_MODULATIONS` 无 SSB，合成器对 SSB raise ValueError |
| `amr.py:23` 文档声称 BPSK/QPSK | **[占位]** | 合成器只产 BPSK，枚举无子类 |
| `amr.py:24` 文档声称 16QAM/64QAM | **[占位]** | 合成器只产 4 点 QPSK |
| `amr.py:14` 文档声称"特征重要性分析" | **[占位]** | 类中无 `feature_importance` / `permutation_importance` 方法 |
| `amr.py:15` 文档声称"增量学习" | **[建议]** | `add_sample` 存在但无持久化 save/load，重启即丢 |
| `amr.py:317` CW = `np.ones(n)` | **[建议]** | 与真实莫尔斯 OOK 不符，包络特征全平 |

没有发现"函数只 return 固定类别"或 `raise NotImplementedError` 的硬空壳。

---

## 7. 与 `experiments/exp_amr.py` 的关系（重点 7）

- `exp_amr.py:26` 直接 `from mbdsdr_ai.amr import AMRClassifier, synthesize_modulation_iq`。
- `exp_amr.py:36` `clf = AMRClassifier(k=5)` —— **开箱即用，不做再训练**，符合 line 7–8 的实验声明。
- `exp_amr.py:48` 测试信号由同一个 `synthesize_modulation_iq` 生成，仅换种子。
- `exp_amr.py:53` `pj = MODS.index(pred) if pred in MODS else None`：若预测为 `UNKNOWN` 则不计入混淆矩阵（静默丢弃）。这意味着如果真机上 KNN 输出 UNKNOWN，混淆矩阵不会反映出来——**评估对"拒识"不敏感**。
- CSV 产物已生成（见 §4.1），实验**确实跑过**，不是纸上谈兵。
- 但实验**只验证了合成集内部分类**，没有：
  - 跨采样率测试
  - 跨符号率/带宽测试
  - 真机录制回放测试
  - 与 RadioML 等公开数据集对比
  - 与基准（模板匹配、SVM、CNN）对比

---

## 8. 关键问题清单（按严重度）

### [真bug]

1. **`amr.py:458` vs `amr.py:526`**：`center_frequency` 与 `carrier_offset` 公式完全相同，25 维特征退化为 24 维独立特征。
2. **`amr.py:437`**：零交叉率只用 I 路实部，复信号零交叉定义错误，削弱对 PSK/QAM 的区分度。
3. **`amr.py:381`**：训练 SNR 仅覆盖 10–30 dB，测试却下探到 -5 dB；低 SNR 准确率崩到 36.7%，却在文档中未做任何声明。
4. **`amr.py:537–547`**：`classify` 内重复计算 KNN 距离（`predict` 算一次，line 540 又算一次），性能浪费 2 倍。

### [占位]

5. **`amr.py:20` + `:366` + `:345`**：SSB 在文档中列出，训练集无此类，合成器对 SSB raise ValueError——纯文档占位。
6. **`amr.py:23` + `:327`**：PSK 只实现 BPSK，文档声称的 QPSK 无模板。
7. **`amr.py:24` + `:332`**：QAM 实际是 4 点 QPSK，16QAM/64QAM 无模板。
8. **`amr.py:14`**：声称"特征重要性分析"，类中无对应方法。

### [建议]

9. 补充高阶累积量特征（CH_42/CH_61/CH_63），这是区分 M-PSK/QAM 阶数的经典手段。
10. 合成器应参数化符号率、滤波滚降、CFO、IQ 失衡，否则训练-测试同源问题无法缓解。
11. 增加 `save()/load()` 接口持久化用户增量样本。
12. `exp_amr.py:53` 应把 UNKNOWN 计入混淆矩阵，否则拒识能力不可见。
13. CW 合成应改为 OOK 断续载波，而非 `np.ones(n)`。
14. 真机验证计划：至少接入一段 RTL-SDR 录制的真实 FM/AM/PSK 信号，报告真实准确率，否则白皮书 5.3 节的 99% 准确率不能作为产品指标引用。

---

## 9. 一句话总结

`amr.py` 是一个**能跑、特征真实、但训练-测试同源、调制子型号虚标、无真机验证**的 KNN AMR 原型。合成集上 99%@10dB 的数字不能外推到真机；SSB/BPSK/QPSK/16QAM/64QAM 中只有 AM/FM/CW/2-FSK/BPSK/QPSK(伪装成 QAM)/OFDM/NOISE 共 7.5 类有真实模板。

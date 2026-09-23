# 第二轮深度代码审查 · 06 星座图 / 信号质量 / 频谱感知

- 审查范围：`mbdsdr_ai/constellation.py`（70 行）、`mbdsdr_ai/signal_quality.py`（76 行）、`mbdsdr_ai/spectrum_sensing.py`（174 行）
- 行数核验（`wc -l`）：constellation.py=70、signal_quality.py=76、spectrum_sensing.py=174，与任务书一致。
- 审查方式：只读，通读全部真实代码 + 全局 grep 追调用方 + 本地 Python 实跑复现。
- 接线佐证：`agent.py`（工具注册）、`sdr_tools.py:5514 _energy_sense`、`dsp.py:675 anr_denoise`、`experiments/exp_spectrum_sensing.py`。

---

## 结论速览

| 审查点 | 结论 |
|---|---|
| 实时星座图是否真算 I/Q 点 | **真实现**（去 DC + AGC + 均匀重采样，返回真实 (I,Q) 对），但为**按需拉取**而非持续推送 |
| QPSK/8PSK/16QAM 判定 | **只实现 QPSK EVM**；无 8PSK/16QAM，无自动调制识别 |
| SNR 估计 | 本两文件内**无 SNR**；仅 spectrum_sensing 有能量粗估（合理） |
| MER | **未实现**（无独立 MER 函数，仅有 EVM） |
| 除零保护 | 主要除法有 eps 保护；但**空输入路径崩溃**（2 处真 bug） |
| 频谱感知能量检测 | **完整真实实现**（含噪声不确定度 / SNR wall） |
| 周期平稳检测 | **完全缺失**（仅能量检测，且文档诚实声明） |
| 阈值自适应 | PFA 门限真实，但**不随时间自适应跟踪噪声底**，需人工标定 |
| ANR 自动降噪 | **真实 STFT 谱减**（非 LMS），已接成按需工具，未进自动解调链 |
| 空壳 / NotImplementedError | **无** |
| BPSK31/RTTY 挂名 | 审查三文件内**无任何引用**；decoders.py / sdr_tools.py 也无 |

---

## 1. 实时星座图（constellation.py）

### 1.1 scatter_points —— 真算 I/Q 点，不是空数组  [真实现，非空壳]
`constellation.py:49-70`。真实做了：
- 去 DC：`x = x - np.mean(x)`（:58）
- 单位方差归一化：`x = x/np.sqrt(p)`（:60-61），且 `p>1e-12` 保护
- 均匀重采样到 `n_points`：`np.linspace(...).astype(int)`（:62-64）
- 返回 `[[re,im], ...]`

复现：
```python
from mbdsdr_ai.constellation import scatter_points
import numpy as np
scatter_points(np.random.randn(2000)+1j*np.random.randn(2000))['n_points']  # -> 1024（真实点）
```
空输入返回 `{"points": [], "n_points": 0}`（:56-57）是**正常防御分支**，不是空壳。

### 1.2 [建议] “实时推送”名不副实：按需拉取，无持续流
`scatter_points` 被注册成 MCP 工具 `sdr_constellation_scatter`（`agent.py:1225-1249`），每次调用处理**一段**传入 IQ buffer 后返回，**没有后台线程 / 回调 / websocket 持续推点**。所谓“实时星座图”本质是“给一段 IQ 算一批散点”，UI 若要实时需自行循环调用。不算 bug，命名有误导。

### 1.3 QPSK/8PSK/16QAM 判定 —— 仅 QPSK，无多制式  [真实现 / 能力缺口]
`evm_qpsk`（`constellation.py:12-36`）只把理想星座硬编码为 QPSK 四点 `(±1±j)/sqrt(2)`（:19）。
- **没有** 8PSK、16QAM 的 EVM 函数；
- **没有**任何自动调制识别（按聚类点数 / 半径比判定 QPSK vs 8PSK vs 16QAM）。

复现（把 16QAM 符号喂给 evm_qpsk，它仍按 4 个 QPSK 点折，给出误导性结果）：
```python
levels=np.array([-3,-1,1,3])/np.sqrt(10)
sym=levels[rng.integers(0,4,2000)]+1j*levels[rng.integers(0,4,2000)]
evm_qpsk(sym)
# -> {'evm_rms_pct': 45.73, ... cluster_centers 仍只报 4 个 ~0.64/0.62 的“象限中心”}
```
即非 QPSK 输入不会报错，只是给出一个无意义的 EVM%。工具描述（`agent.py:1140`）已诚实写明“输入一段 QPSK 软符号”，故属**能力缺口**而非欺骗性声明；建议加调制类型入参或在聚类半径异常时告警。

### 1.4 [建议] 死代码 + 注释承诺的“误判率”未交付
`constellation.py:25-26`：
```python
# 误判率（理想点归一化后，越限算错）
hard = np.sign(np.stack([xn.real, xn.imag], axis=1))
```
`hard` 计算后**再未被使用**，返回 dict（:27-36）里也**没有误判率 / BER 字段**。注释承诺了误判率但没实现。建议要么删掉该行，要么补上 BER 统计并返回。

---

## 2. 信号质量（signal_quality.py）

### 2.1 signal_quality —— 真实，但缺 SNR / MER
`signal_quality()`（:12-36）真实计算：DC offset I/Q、I/Q 不平衡 dB、RMS、peak、PAPR、圆形均值相位（用 `atan2(mean sin, mean cos)` 正确处理跨 ±π，:24-26）。
- **SNR 估计：本文件无**。全局仅 `spectrum_sensing.py:167` 有能量粗估 `snr_linear = stat/noise_power - 1`（合理，H1 下 E[T]=σ²(1+λ)，已钳位到 -90 dB）。
- **MER：未实现**。没有独立 MER 函数（工程上 MER≈-EVM_dB，但代码里既无 MER 字段也无换算）。

### 2.2 除零保护 —— 已有，但不全
- I/Q 不平衡：`(rms_i+1e-9)/(rms_q+1e-9)`（:30）✓
- PAPR：`(peak+1e-9)/(rms+1e-9)`（:33）✓
- squelch RSSI：`p+1e-12`（:57）✓
- constellation 归一化：`sqrt(p)+1e-9`（:17）、`log10(evm+1e-9)`（:29）✓

但保护未覆盖空输入，见下两条真 bug。

### 2.3 [真bug] signal_quality(空数组) 崩溃
`signal_quality.py:21` `peak = float(np.max(mag))` 对空数组抛错。
复现：
```python
from mbdsdr_ai.signal_quality import signal_quality
signal_quality(np.array([], dtype=complex))
# RuntimeWarning: Mean of empty slice.
# ValueError: zero-size array to reduction operation maximum which has no identity
```
`agent.py:928` / `:1301` 直接把传入 IQ 喂进来，上游若传空 buffer 会崩（虽被工具层 `try/except` 兜成失败消息，但底层不该裸崩）。建议在 :14 后加 `if len(x)==0: return {...空值...}`。

### 2.4 [真bug] squelch_gate(空数组) ZeroDivisionError
`signal_quality.py:47-49`：
```python
if n < block:      # n=0 < 1024 -> True
    block = n      # block = 0
blocks = n // block # 0 // 0 -> ZeroDivisionError
```
复现：
```python
from mbdsdr_ai.signal_quality import squelch_gate
squelch_gate(np.array([], dtype=complex))
# ZeroDivisionError: integer division or modulo by zero
```
建议在 :46 后加 `if n == 0: return {"open": False, "duty_cycle": 0.0, ...}`。

---

## 3. 频谱感知（spectrum_sensing.py）—— 真实、数学正确

### 3.1 能量检测：完整真实实现  [真实现]
- `qfunc`/`qinv`（:30-70）：Q 函数与 Acklam 有理逼近 Q⁻¹，无需 scipy，边界 `p>=1`/`p<=0` 处理正确。
- `energy_statistic`（:73-78）：T=(1/N)Σ|x|²，空样本显式 raise。
- `threshold_for_pfa`（:86-96）：门限 `γ=σ²[1+Q⁻¹(Pfa)/√N]`，含噪声不确定度 ρ=10^(U/10) 保守上限。**推导核验正确**（H0: T~N(σ²,σ⁴/N)，令 Q((γ-σ²)/(σ²/√N))=Pfa 即得）。
- `theory_pd`（:99-113）：Pd=Q(√N(ρ-1-λ)+ρ·Q⁻¹(Pfa))，U=0 退化为经典 Pd=Q(Q⁻¹(Pfa)-√N·λ)，**核验正确**。
- `snr_wall_db`（:116-125）：Tandra–Sahai SNR wall，正确。
- `EnergyDetector.detect`（:148-174）：真实 H0/H1 判决，强制要求 `noise_power` 或 `noise_ref`，**明确拒绝**从含信号样本自估门限（:159-161，注释合理）。
- 产品接线：`sdr_tools.py:5514 _energy_sense` 支持实时设备采集或传入 IQ，`experiments/exp_spectrum_sensing.py` 与产品同源复用。

### 3.2 [占位/缺失] 周期平稳（cyclostationary）检测未实现
整个模块只有**能量检测**，docstring（:3）也诚实自定位为 Energy Detector。无任何循环自相关 / 谱相关函数（SCF）。若产品宣称“认知无线电多算法频谱感知”，则周期平稳检测是缺口；若仅承诺能量检测，则不算撒谎。建议在文档中明确“仅能量检测”。

### 3.3 [建议] 阈值不随时间自适应跟踪噪声底
门限由**一次性**给定的 `noise_power` 或一段 `noise_ref` 算出，模块内**无**序统计量 / 最小统计量 / 滑动噪声底跟踪。噪声漂移或温漂时需重新标定。`noise_uncertainty_db` 是离线保守补偿，不是在线自适应。

---

## 4. ANR 自动降噪（dsp.py:675，跨文件追踪）

任务点 4 要求确认 ANR 是否实现、是 LMS 还是谱减、是否接链。结论：

- **方法：STFT 谱减（spectral subtraction），不是 LMS**。`dsp.py:675-717`：Hanning 窗 STFT（:688-692）、前 `noise_frames` 帧平均估计噪声谱（:709-710）、`cleaned = mag - oversub*noise_mag`（:713）、`floor_db` 地板防音乐噪声（:714-715）、保留相位（:712）、重叠相加 ISTFT（:694-704）。纯 numpy，逻辑真实。
- **是否接链：已接成按需工具，但未进自动解调链**。注册为 `sdr_anr_denoise`（`agent.py:1185-1223`），由 agent 显式调用；接收/解调主链路（demod/sdr_backend）并未自动串联降噪。
- [建议] `dsp.py:686 wg = float(np.mean(win))` 计算后未使用（死变量）。

---

## 5. 空壳 / 占位扫描

- 三文件内**无任何 `raise NotImplementedError`**。
- 无函数返回固定假值 / 魔数冒充结果。
- `scatter_points` 空数组返回 `[]`（constellation.py:57）与 `energy_statistic` 空样本 raise（spectrum_sensing.py:77）均为**正当防御**，非空壳。
- 真正“没做完”的只有：周期平稳检测（3.2）、8PSK/16QAM EVM 与自动调制识别（1.3）、误判率/BER（1.4）。

---

## 6. BPSK31 / RTTY 挂名排查（任务点 6）

- 本审查三文件（constellation / signal_quality / spectrum_sensing）内 **grep 无** `psk31/rtty/baudot/varicode/ita2` 任何命中。
- 扩查 `decoders.py`、`sdr_tools.py`：同样**零命中**。
- 全项目仅在白皮书 `AI定义无线电-概念定义与框架-v2.1.md:981`（“ft8/psk31/rtty/sstv 数字模式，部分实现”）与 `digital_modes.py:189` 的参数常量表出现字样；实际编解码实现缺失情况已由 03_digital_modes.md 记录。
- 结论：**审查范围内不存在“注册了 BPSK31/RTTY 模式名但无实现”的挂名**，无需追加告警。

---

## 附：发现清单（按严重度）

| # | 级别 | 位置 | 说明 | 复现 |
|---|---|---|---|---|
| 1 | [真bug] | signal_quality.py:21 | `np.max(空mag)` 抛 ValueError | `signal_quality(np.array([],complex))` |
| 2 | [真bug] | signal_quality.py:49 | 空输入 `block=0` 后 `n//block` ZeroDivisionError | `squelch_gate(np.array([],complex))` |
| 3 | [建议] | constellation.py:26 | `hard` 死代码；注释承诺误判率但未返回 | 读码 |
| 4 | [建议] | constellation.py:19 | 仅 QPSK，喂 16QAM 不报错只给误导 EVM | 见 1.3 |
| 5 | [占位/缺失] | spectrum_sensing.py 全文 | 无周期平稳检测，仅能量检测 | grep cyclostat 零命中 |
| 6 | [建议] | spectrum_sensing.py:143 | 阈值不在线自适应跟踪噪声底 | 读码 |
| 7 | [建议] | agent.py:1225 | “实时星座图”实为按需拉取，无持续推送 | 读码 |
| 8 | [建议] | dsp.py:686 | `wg` 死变量（ANR 谱减本身真实） | 读码 |

**正面确认**：spectrum_sensing.py 是本批质量最高的模块——能量检测、PFA 门限、噪声不确定度、SNR wall、理论 Pd 全部真实且数学推导经核验正确，并已同时接产品（sdr_tools）与实验（exp_spectrum_sensing）。constellation / signal_quality 核心算法真实，主要问题是两处空输入崩溃与“只支持 QPSK”的能力边界。

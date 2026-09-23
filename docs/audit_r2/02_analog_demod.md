# 02 · 模拟解调审查（AM / FM / SSB）

> 审查范围：`mbdsdr_ai/demod.py`（486 行）、`mbdsdr_ai/analog_demod.py`（72 行）
> 只读审查，未修改任何代码。所有结论均附 `file:line` 与复现方式。

---

## 0. 范围校正（重要）

任务书称「这两个文件负责 AM/FM/SSB 等模拟解调」。**与实际代码不符：**

| 文件 | 实际内容 | 行数 |
|---|---|---|
| `mbdsdr_ai/demod.py` | **QPSK 数字解调**：RRC 匹配滤波 → Costas 环 → Gardner 位同步 → Viterbi → 解扰。**不含任何 AM/FM/SSB 模拟音频代码** | 486 |
| `mbdsdr_ai/analog_demod.py` | 真正的模拟解调入口 `demod_analog()`：am/fm/usb/lsb | 72 |

因此模拟解调的全部实质逻辑集中在 `analog_demod.py`（72 行）。`demod.py` 的模拟解调职责为空，仅在末尾附带一个独立 QPSK 链路。下文以 `analog_demod.py` 为主，`demod.py` 只在第 8 节附带说明。

调用链确认：`agent.py:838` `from mbdsdr_ai.analog_demod import demod_analog`，经工具 `demod_analog_audio`（`agent.py:863`）暴露，参数 `mode/max_dev/audio_bw` 直接透传（`agent.py:850-855`）。

---

## 1. FM 鉴频器

### 1.1 鉴频方法本身正确 ✅
`analog_demod.py:37` 采用差分相位法：
```python
phase = np.angle(x[1:] * np.conj(x[:-1]))
audio = phase / (2 * np.pi * max_dev / sr)
```
推导：`arg(x[n]·conj(x[n-1])) ≈ 2π·f_dev/sr`，归一化到 `max_dev` 得 `phase·sr/(2π·max_dev)`，与代码一致。比「复数除法」更稳（除法在小信号时数值爆炸），此处选差分相位是对的。自测 `analog_demod.py:66-72` 用合成单音可正确解出 400Hz，符合「实验室绿」。

### 1.2 [真bug] FM 支路没有去直流 / CFO 残留
`analog_demod.py:37-40`：AM 支路在 `:33` 做了 `audio = audio - np.mean(audio)`，**FM 支路没有做任何直流去除**。真机必然存在 VFO 中心频偏（CFO），每个采样恒定相位旋转 → 鉴频器输出一个常数直流，被低通保留为背景哼声。

**复现**（在 `mbdsdr_ai/` 目录下）：
```python
import numpy as np
from mbdsdr_ai.analog_demod import demod_analog
sr=480000; t=np.arange(sr)/sr
iq = np.exp(1j*2*np.pi*5000*np.cumsum(0.4*np.sin(2*np.pi*400*t))/sr)
print(np.mean(demod_analog(iq, sr, 'fm', 5000)['audio']))        # ~0
iq_cfo = iq*np.exp(1j*2*np.pi*200*t)                            # +200Hz 真机频偏
print(np.mean(demod_analog(iq_cfo, sr, 'fm', 5000)['audio']))   # +0.091 直流残留
```
实测：无 CFO 均值≈0；+200Hz CFO 均值 **+0.091**（满量程峰值归一化后仍残留约 9% 直流）。这是「实验室绿、真机红」的直接来源之一——自测信号 `iq = np.exp(1j*phase)` 载波严格居中，CFO=0。

### 1.3 [空壳] 去加重（50µs / 75µs）完全缺失
FM 广播标准去加重是一个 6dB/oct 的 RC 低通（时间常数 50µs 中/欧，75µs 美/韩）。`analog_demod.py:40` 只用了一个加窗 sinc 低通到 `audio_bw`（默认 3kHz），**没有任何 RC 去加重一阶环节**。后果：真实广播解调后高音尖刺、发毛。

> 对照：项目里其实已有正确实现 `dsp.py:288-289` 与 `wfm_stereo_lite.py:120-121`，用 `a=exp(-1/(tau*1e-6*sr))` 的一阶 IIR。`analog_demod.py` 既没有调用也没有复刻。

### 1.4 [空壳] 立体声解码未接入本文件
`analog_demod.py` 的 `fm` 分支只输出单声道（`_lowpass` 到 audio_bw），**没有 19kHz 导频提取、没有 38kHz 副载波同步、没有 L/R 分离**。项目内立体声解码器 `wfm_stereo_lite.decode_stereo` 存在，但只被 `sdr_tools.py:3083` / `agent.py:895` 的独立工具 `demod_wfm_stereo` 调用，**`demod_analog` 的 fm 路径完全没有接它**。即：走 `demod_analog_audio` 工具永远是单声道，立体声开关在此文件里不存在。

---

## 2. AM 解调

### 2.1 [建议/设计取舍] 仅包络检波，无同步检波 / 无载波恢复
`analog_demod.py:32-34`：
```python
audio = np.abs(x)          # 包络检波（二极管检波的数字等效）
audio = audio - np.mean(audio)
audio = _lowpass(audio, sr, audio_bw)
```
- 对强信号 DSB-LC AM 包络检波可行；`-mean` 去载波直流也是对的。
- 但**没有任何载波恢复**（无 Costas、无平方环、无插入载波），弱信号下包络检波有门限效应（threshold effect）：SNR 低于 ~10dB 时噪声冲击直接破坏包络，音质骤降。这正是「真实广播弱台失败」的一个原因。
- 没有 AGC：包络幅度随接收信号强度大幅变化，全靠 `:48` 的整段峰值归一化兜底。

---

## 3. SSB 解调（USB / LSB）—— 问题最严重

### 3.1 [真bug] USB 与 LSB 是同一个分支，切换是空操作
`analog_demod.py:41-44`：
```python
elif mode in ("usb", "lsb"):
    audio = np.real(x)
    audio = _lowpass(audio, sr, audio_bw)
```
USB 和 LSB 共用同一段代码，仅取 `np.real(x)`（即 I 路），**完全丢弃 Q 路**，没有任何边带选择（无 Hilbert 相移法 / Weaver 法 / 陡带通滤波），也没有 BFO 混频。

**复现**（构造纯上边带 +1kHz 与纯下边带 -1kHz）：
```python
sr=48000; t=np.arange(sr)/sr
usb = np.exp(1j*2*np.pi*1000*t)   # 仅 +1kHz
lsb = np.exp(-1j*2*np.pi*1000*t)  # 仅 -1kHz
au = demod_analog(usb, sr,'usb',audio_bw=3000)['audio']
al = demod_analog(lsb, sr,'lsb',audio_bw=3000)['audio']
np.allclose(au, al)   # -> True；两边 FFT 主峰都落在 1000Hz
```
实测：USB 模式解 +1kHz、LSB 模式解 -1kHz，**输出逐样本完全相同（`np.allclose=True`），主峰都报 1000Hz**。即：
- 模式参数 `usb/lsb` 对输出**零影响**；
- 下边带的频率倒置（LSB 高音=低频 RF）未做任何补偿；
- 若上下边带同时存在（未做前置通道滤波），`np.real(x)` 会把双边带折叠成 DSB，无法分离边带。

正确做法应是相移法：`audio_usb = I·cos(ω_bfo t) + Q·sin(ω_bfo t)`，`audio_lsb = I·cos(ω_bfo t) − Q·sin(ω_bfo t)`，靠 Q 路符号区分边带。本文件把 Q 路直接丢了。

### 3.2 [空壳] 没有 BFO（拍频振荡器）
`analog_demod.py` 函数签名（`:20-21`）只有 `iq/sample_rate/mode/max_dev/audio_bw`，**没有 BFO 频率参数**，注释 `:42` 「已在 VFO 中心」把问题甩给上游。但 SSB/CW 是抑制载波信号，真机 VFO 不可能恰好对准抑制载波，必须能微调 BFO 几百 Hz 把语音/电报到自然音高。项目里 `dsp.py:415-422` 有正确的 CW/BFO 实现（`bfo=exp(1j·2π·f·t)` 混频），但 `analog_demod.py` 的 usb/lsb 分支没有复用。

---

## 4. 「实验室绿、真机红」归因

自测 `analog_demod.py:58-72` 的合成信号特征：
```python
iq = np.exp(1j * phase)   # 恒包络、零 CFO、零噪声、无邻道、频偏严格=max_dev
```
对照真机差异：

| 真机因素 | 本文件后果 | 证据 |
|---|---|---|
| VFO 频偏 CFO | FM 输出直流哼声 | §1.2，+200Hz→+0.091 直流 |
| 衰落/多径（幅度起伏） | AM 包络失真、FM 咔哒声 | §2.1 |
| 邻道密集频谱 | 低通过渡带太宽，邻道抑制差 | §5 |
| 广播去加重 | 高音尖刺 | §1.3 |
| 信号间隙/弱信号 | 无静噪，白噪直冲耳朵 | §6 |
| SSB 载波未对准 | 音高漂移，且 USB/LSB 无差别 | §3 |

---

## 5. [真bug] 低通滤波器抽头数硬编码，不随采样率缩放

`analog_demod.py:12-17`：
```python
def _lowpass(x, sr, cutoff, taps=63):
    n = np.arange(taps) - taps//2
    h = 2*cutoff/sr * np.sinc(2*cutoff/sr * n)
    h *= np.hanning(taps); h /= h.sum()
```
`cutoff` 随 `sr` 归一化是对的，但 **`taps=63` 固定不变**。高采样率下过渡带宽 ≈ 1.8·sr/taps。

**复现**（sr=480k, cutoff=3k, taps=63 的幅频实测）：
| 频率 | 增益 |
|---|---|
| 1 kHz | −0.1 dB |
| 3 kHz（截止点） | −0.8 dB |
| 5 kHz | −2.3 dB |
| 10 kHz | −9.9 dB |
| 20 kHz | −34.7 dB |

即截止 3kHz 处几乎无衰减，距截止仅 2kHz 的邻道只压了 2.3dB。实验室单音测试（无邻道）听不出来；真机拥挤频段邻道串入明显。应按 `taps ∝ sr/cutoff` 动态定长（例如 `taps = int(4*sr/cutoff)|1`）。

---

## 6. 静噪（Squelch）

### 6.1 [空壳] `analog_demod.py` 内无任何静噪逻辑
`demod_analog()` 对噪声-only 输入照样输出音频（仅在 `:48` 做整段峰值归一化，反而把噪声拉到满幅）。函数内没有 RSSI 估计、没有门控、没有 mute。

### 6.2 未接入处理链
项目里静噪其实存在——`signal_quality.py:39 squelch_gate`，并被 `agent.py:1154-1169` 注册为**独立工具** `sdr_squelch_gate`。但它与 `demod_analog` 是两个互不调用的工具：解调前是否先过门控完全靠上层 LLM/调用者自己串联，**`demod_analog` 内部并不强制静噪门控**。即「静噪」能力存在但未接进模拟解调链路。

---

## 7. 采样率假设

`analog_demod.py` 把 `sample_rate` 作为入参并正确用于鉴频归一化（`:38`）和低通设计（`:14`），**没有把某个采样率硬编码死在算法里**——这点合格。但有两个隐含假设：
1. 入参 IQ 必须是**已抽取到音频附近带宽**的复信号；函数自身不做抗混叠抽取。若直接喂 2Msps 原始 IQ，鉴频虽数值正确，但 §5 的 63 抽低通完全不够用。
2. 自测 `:60` 硬编码 `sr=480000`，易让维护者误以为只支持此速率。

---

## 8. `demod.py` 附带观察（数字域，超本文件模拟职责）

`demod.py` 整文件是 QPSK 数字解调，与本次模拟解调范围无关，仅记录两处可疑点供后续数字域审查组跟进：

- **[疑似真bug] Viterbi 回溯方向反了**：`demod.py:436-439`
  ```python
  for i in range(n_symbols-1,-1,-1):
      bit = survivors[i, state]
      decoded[i] = bit
      state = self.next_state[state, bit]   # 这是“前向”转移，回溯应取逆
  ```
  回溯时从 `state` 与决策位 `bit` 求上一时刻状态，应为 `prev = ((state<<1)|bit) & mask`（K=7 码），而代码用 `next_state[state,bit]` 做了一次前向跳转。对对称移位寄存器码未必等价，建议数字域组用已知卷积码序列做端到端验证。
- Gardner 误差 `demod.py:212` 用 `real(out-line[idx+1])*imag(mid)`，与标准复 Gardner `real[(y_k−y_{k-1})·conj(y_{k-1/2})]` 形式不一致，数字域复核。

以上两点不影响本次模拟解调结论。

---

## 9. 结论汇总

| # | 严重度 | 位置 | 问题 |
|---|---|---|---|
| 1 | **[真bug]** | `analog_demod.py:41-44` | USB/LSB 同一分支、丢 Q 路，模式切换是空操作 |
| 2 | **[真bug]** | `analog_demod.py:37-40` | FM 支路无去直流，真机 CFO → 输出直流哼声 |
| 3 | **[真bug]** | `analog_demod.py:12-17` | 低通 taps=63 硬编码，高采样率邻道抑制差 |
| 4 | **[空壳]** | `analog_demod.py:40` | FM 去加重（50/75µs）缺失 |
| 5 | **[空壳]** | `analog_demod.py:35-40` | 立体声解码未接入，fm 恒为单声道 |
| 6 | **[空壳]** | `analog_demod.py` 全文 | 静噪未实现、未接入解调链（仅独立工具存在） |
| 7 | **[空壳]** | `analog_demod.py:20-21,42` | SSB 无 BFO，无法微调载波音高 |
| 8 | [建议] | `analog_demod.py:32-34` | AM 仅包络检波，弱信号门限效应，无 AGC/载波恢复 |
| 9 | [建议] | `analog_demod.py:48` | 整段峰值归一化替代 AGC，流式音量跳变 |
| 10 | 范围校正 | `demod.py` 全文 | 实为 QPSK 数字解调，不含 AM/FM/SSB；见 §8 附查 |

**一句话**：`analog_demod.py` 的核心鉴频/包络数学是对的（所以合成单音自测能绿），但它是一个「去掉了所有真机必需外围电路」的最小骨架——无去加重、无立体声、无 BFO、无静噪、无 AGC、USB/LSB 不区分、FM 不去直流、低通抽头数不随采样率缩放。这些缺失恰好一一对应「真机语音/广播失败」。

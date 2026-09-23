# R2 深度审查报告：mbdsdr_ai/dsp.py

**审查范围**：`mbdsdr_ai/dsp.py`（747 行）
**审查日期**：2026-09-24
**审查人**：DSP 核心子 agent

---

## 模块概述

本文件是项目的纯 numpy DSP 核心，分为 5 个区块：

| 区块 | 内容 | 函数/类数量 |
|------|------|-------------|
| 1. IQ 前端校正 | DCBlocker、IQCalibrator、decimate、front_end | 2 类 + 2 函数 |
| 2. 解调算法 | fm_demod、wfm_broadcast_demod、rds_decode_from_wfm、audio_to_playback、am_demod、ssb_demod、cw_demod、demodulate | 8 函数 |
| 3. AGC | AGC 类 | 1 类 |
| 4. 录制写文件 | write_cf32、write_cs16、write_wav、write_csv、write_sidecar_json | 5 函数 |
| 5. 信号分析 | compute_snr、estimate_bandwidth、anr_denoise、find_spectrum_peaks | 4 函数 |

**总计**：3 个类 + 19 个函数 = 22 个可调用单元。

- **真正实现**：18 个（DCBlocker、IQCalibrator、decimate、front_end、fm_demod、wfm_broadcast_demod、audio_to_playback、am_demod、ssb_demod、cw_demod、demodulate、AGC、write_cf32、write_cs16、write_wav、write_csv、write_sidecar_json、compute_snr、anr_denoise、find_spectrum_peaks）
- **占位/半实现**：1 个（`rds_decode_from_wfm`——仅做副载波能量检测，无块同步/CRC，文档已自述）
- **无空壳**：没有 `raise NotImplementedError` 或返回固定值的函数

---

## 发现列表

### P1-1 | 真bug | `dsp.py:41-46, 50-58` | DCBlocker 的 I/Q 通道共享递归状态，Q 通道每 chunk 开头被 I 通道污染

**问题描述**：
`DCBlocker.process()` 对复数输入分别调用 `_process_real(x.real)` 和 `_process_real(x.imag)`，但两者共享同一个 `self._x_prev` / `self._y_prev` 实例变量。处理完 I 通道后，状态变量被覆写为 I 通道的最后一个样本，Q 通道从这个错误状态开始递归。

一阶 IIR 的时间常数 τ = 1/(1-r) = 1/0.002 = **500 个样本**。这意味着每个 chunk 的前 ~500 个 Q 样本都被 I 通道的直流暂态污染。对于 2048 样本的 chunk，约 25% 的 Q 数据受影响；对短 chunk 几乎全毁。

**复现方式**：
```python
import numpy as np
from mbdsdr_ai.dsp import DCBlocker
N = 2048
i = np.random.randn(N) * 0.1          # I 无直流
q = np.random.randn(N) * 0.1 + 1.0    # Q 有 1.0 直流
x = i + 1j * q
dc = DCBlocker(r=0.998)
y = dc.process(x)
# 期望：Q 输出直流被去除（均值≈0）
# 实际：Q 前 100 样本均值 = 0.91，后 100 样本均值 = 0.019
print(np.mean(y.imag[:100]), np.mean(y.imag[-100:]))
```
实测输出：`0.909` vs `0.019`——前 100 样本几乎没被 DC 阻断。

**修复建议**：
为 I 和 Q 分别维护状态。在 `process()` 中保存/恢复，或在 `__init__` 中用两个独立状态变量（如 `_x_prev_i/_x_prev_q`）。最简改法：
```python
def process(self, x):
    if np.iscomplexobj(x):
        state = (self._x_prev, self._y_prev)
        i_part = self._process_real(x.real)
        self._x_prev, self._y_prev = state  # 恢复 I 的状态给 Q 用
        q_part = self._process_real(x.imag)
        return i_part + 1j*q_part
    return self._process_real(x)
```
更好的做法是在 `_process_real` 内部用局部变量并返回 (y, x_prev, y_prev)，避免实例变量交叉污染。

---

### P1-2 | 真bug | `dsp.py:171` | decimate 抗混叠低通滤波器截止频率是应有值的 2 倍

**问题描述**：
整数抽取前的抗混叠 LPF 设计为：
```python
h = np.sinc(2 * t / factor) / factor
```
注释说"截止频率 0.5/factor"，但 `np.sinc(2*t/D)` 对应的截止频率是 **1/D**（cycles/sample），而非 1/(2D)。正确公式应为 `np.sinc(t/factor)/factor`。

对于 D=2 抽取，正确截止应为 0.25 cycles/sample（新 Nyquist），但代码实现的滤波器在整个 0–0.5 Nyquist 通带内增益 ≈ 1.0，**等于没有抗混叠滤波**。

数值验证（D=2，64 taps，Hanning 窗）：

| 频率 (cycles/sample) | 代码滤波器增益 | 正确滤波器增益 |
|---|---|---|
| 0.20 | 1.000 | 0.998 |
| 0.25（新 Nyquist） | 1.000 | 0.500 |
| 0.30（混叠区） | **1.000** | 0.002 |
| 0.40 | 1.000 | 0.000 |

在 0.30 处，代码滤波器全通，正确滤波器已衰减到 0.2%。真实宽带有信号在 0.25–0.5 区间的能量会直接混叠进基带。

**"实验室绿、真机红"特征**：合成测试通常只放一个基带附近的单音，不会触发；真机 SDR 输入包含宽频带能量，混叠后噪声底明显抬高。

**复现方式**：
```python
import numpy as np
from mbdsdr_ai.dsp import decimate
# 输入一个在 0.3 cycles/sample 的单音（D=2 后应被滤除）
fs = 2.4e6; D = 2; N = 8192
t = np.arange(N)/fs
x = np.exp(1j*2*np.pi*0.3*fs/2*t)  # 频率 = 0.3 * fs/2 = 360kHz
y = decimate(x, D)
# 正确实现：y 应该接近零（信号在通带外被滤除）
# 实际：y 仍有明显能量（混叠到基带）
print(np.mean(np.abs(y)))
```

**修复建议**：
将第 171 行改为：
```python
h = np.sinc(t / factor) / factor
```
并将 `num_taps` 适当增大（64 taps 对 D=2 时过渡带偏宽，建议至少 128 taps 或按 D 缩放：`num_taps = min(128, len(x)//4)`）。

---

### P2-1 | 真bug | `dsp.py:647` | estimate_bandwidth 未加窗，矩形窗泄漏导致带宽严重高估

**问题描述**：
`estimate_bandwidth()` 直接 `np.fft.fft(x)`，未加 Hann 窗。对比同文件的 `compute_snr()`（第 609 行加了 Hann 窗）和 `find_spectrum_peaks()`（第 730 行加了 Hann 窗），此处遗漏了窗函数。

矩形窗的第一旁瓣在 -13 dB，而检测门限是峰值 -20 dB。旁瓣高于门限，导致旁瓣被误判为信号带宽。

**复现方式**：
```python
import numpy as np
from mbdsdr_ai.dsp import estimate_bandwidth
N = 8192; fs = 2.4e6
t = np.arange(N)/fs
x = np.exp(1j*2*np.pi*100000*t)  # 完美单音，真实带宽=0
result = estimate_bandwidth(x, sample_rate=fs)
# 实测：bandwidth_hz = 1757.8 Hz（应为 ~0）
```

**修复建议**：
在 FFT 前加 Hann 窗并做窗增益补偿：
```python
win = np.hanning(len(x))
spectrum = np.fft.fftshift(np.fft.fft(x * win))
```
功率计算时除以窗增益平方（参考 `compute_snr` 的做法）。

---

### P2-2 | 真bug | `dsp.py:401` | ssb_demod 移动平均低通实际截止 ~1.76kHz，注释声称 ~4kHz

**问题描述**：
```python
kernel_size = max(1, int(sample_rate / 4000))  # ~4kHz 音频带宽
```
移动平均滤波器的 -3dB 截止约为 `0.44 * fs / N`。当 fs=2.4MHz 时 kernel_size=600，实际截止 ≈ 0.44 × 2.4e6 / 600 ≈ **1760 Hz**，而非注释声称的 4kHz。这会把 SSB 语音（300–3000 Hz）的高频部分（1.7–3 kHz）过度衰减。

此外，`np.convolve(audio, kernel, mode='same')` 是直接 O(N×M) 卷积，对 2.4M 样本 × 600 taps ≈ 14 亿次乘加，在低性能机器上需数秒。

**复现方式**：
```python
from mbdsdr_ai.dsp import ssb_demod
import numpy as np
fs = 2.4e6; N = 240000
t = np.arange(N)/fs
# 输入 2.5kHz 单音（SSB 语音高频）
x = np.exp(1j*2*np.pi*2500*t)
y = ssb_demod(x, mode="USB", sample_rate=fs)
# 2.5kHz 应保留，但移动平均在 1.76kHz 以上已大幅衰减
```

**修复建议**：
1. 修正 kernel_size 使截止匹配目标带宽：`kernel_size = max(1, int(0.44 * sample_rate / cutoff_hz))`，其中 cutoff_hz=3000。
2. 改用 `scipy.signal.firwin` 设计真正的低通滤波器，或用 FFT 卷积加速。

---

### P2-3 | 建议 | `dsp.py:479` | AGC 在近零电平时提前返回，不更新增益，导致静默后释放不充分

**问题描述**：
```python
if level < 1e-10:
    return x  # 直接返回，不更新 _current_gain
```
当信号从大信号突变为接近零时，AGC 增益保持在低水平不释放。后续弱信号到来时需要多个 chunk 才能缓慢提升增益，期间声音很小。

实测：大信号后 gain≈1.0，全零 chunk 后 gain 仍≈1.0，0.001 弱信号到来后 gain 仅升到 1.59（远未达到 max_gain=60）。

**复现方式**：
```python
from mbdsdr_ai.dsp import AGC
import numpy as np
agc = AGC(target_level=0.5, attack=0.01, release=0.001, max_gain=60.0)
agc.process(np.ones(1000)*0.8)   # 大信号，gain→1.0
agc.process(np.zeros(1000))      # 静默，gain 不变（应为 release 上升）
print(agc._current_gain)          # 仍 ~1.0
```

**修复建议**：静默时仍按 release 系数提升增益（向 max_gain 方向），而非直接返回。或把阈值改为仍走增益平滑逻辑，仅跳过电平计算。

---

### P2-4 | 建议 | 性能：四处 Python 循环在大数据量下拖慢

| 位置 | 问题 | 规模 |
|------|------|------|
| `dsp.py:53` | DCBlocker._process_real 逐样本 Python for 循环 | 2.4M 样本 ≈ 2.4M 次 Python 迭代，约 0.5–1s |
| `dsp.py:404` | ssb_demod 用 np.convolve（直接卷积 O(N×M)） | 2.4M × 600 taps ≈ 1.4e9 ops |
| `dsp.py:740` | find_spectrum_peaks 逐 bin Python 循环找局部极大 | N=2.4M 时 2.4M 次 Python 迭代 |
| `dsp.py:574` | write_csv 逐样本 f.write 字符串 | 2.4M 行 Python IO，极慢 |

这些不影响正确性，但在低性能机器（树莓派/老旧 x86）上会成为瓶颈。DCBlocker 可向量化为 `lfilter`；find_spectrum_peaks 可用 `np.diff(np.sign(...))` 向量化；write_csv 可用 `np.savetxt` 或批量 join。

---

### P2-5 | 占位 | `dsp.py:306-328` | rds_decode_from_wfm 是占位实现

**问题描述**：
该函数仅做带通滤波 + 能量检测，判断 57kHz 副载波是否存在。**不做**块同步、差分解码、CRC 纠错、104bit 组解码。函数文档已明确说明这一点。

这不是 bug，但调用方需知道：返回的 `rds_present` 只是"副载波能量是否超过门限"，并非真正的 RDS 数据解码。

**复现方式**：调用 `rds_decode_from_wfm(x, fs)` 不会得到任何 RDS 文本（电台名/频率），只返回布尔值和 dB 值。

**修复建议**：保持现状（已标注占位），后续接入开源 RDS 库（如 `rds-rb` 或 GNU Radio RDS 模块）时替换即可。

---

## 未发现 P0 问题

- 无空指针/崩溃路径（上一轮已修）
- 无除零崩溃（各处有 `max(..., 1e-12)` 保护）
- 无 dtype 假设违反导致的硬崩溃
- 无 `raise NotImplementedError` 的空壳函数

## 总结

| 等级 | 数量 | 关键项 |
|------|------|--------|
| P0 | 0 | — |
| P1 | 2 | DCBlocker I/Q 状态串扰；decimate 抗混叠截止 2 倍错误 |
| P2 | 5 | estimate_bandwidth 未加窗；ssb_demod 截止偏差+性能；AGC 静默不释放；4 处 Python 循环性能；RDS 占位 |

两个 P1 都是"实验室合成信号通过、真机宽频输入暴露"的典型类型：DCBlocker 状态串扰在 I/Q 直流相近时不可见，decimate 混叠在单音测试时不触发。建议优先修复 P1-2（decimate 滤波器系数，一行改动）和 P1-1（DCBlocker 状态分离）。

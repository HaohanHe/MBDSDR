# 第二轮深度代码审查 — 频谱与信号分析模块

审查范围：
- `mbdsdr_ai/spectrum_processor.py`（438 行）
- `mbdsdr_ai/signal_spectrum.py`（114 行）
- `mbdsdr_ai/signal_analysis.py`（264 行）

审查方式：只读，通读三文件全文 + 追溯 `sdr_tools.py` / `agent.py` 调用点。
分级标注：[真bug] 功能/数值错误；[空壳] 只返回零/固定值；[占位] 有实现但未接线/未完成；[建议] 改进项。

---

## 一、总评

| 文件 | 结论 |
|---|---|
| `signal_spectrum.py` | **质量最高**：Welch 50% 重叠 + Hann 窗 + `wpow*sr` 归一化 PSD + Peak Hold + 中值底噪 + 连续段峰列表，是真实可用的实现，无空壳。 |
| `spectrum_processor.py` | 主链路 `compute_spectrum` 有实现但 **dB 未标定**，导致下游绝对阈值检测失效；瀑布图/zoom/pan 为**未接线的占位状态**。 |
| `signal_analysis.py` | 检测/调制识别为真实启发式实现；`estimate_ber` **全仓零调用**且算法本身可疑，属死代码。 |

整体没有"只返回零数组"的空壳；主要问题集中在**标定不一致**和**视图/瀑布功能未接线**。

---

## 二、[真bug] 发现

### B1. `spectrum_processor.py:106-108` — dBFS 未标定，参考电平随 FFT 大小漂移

```python
spectrum = np.fft.fftshift(np.fft.fft(windowed, fft_size))
powers = np.abs(spectrum) ** 2
powers_db = 10 * np.log10(powers + 1e-12)
```

- 功率谱**没有除以 FFT 点数 `fft_size`**，也**没有做窗相干增益补偿**（Hann 窗相干增益 ≈ 0.5，处理增益 ≈ `sum(w)` 而非 `sum(w^2)`）。
- 后果：一个满量程正弦波 A=1 的主峰峰值 ≈ `10*log10((N*A/2)^2)`，即 **峰值随 N 线性变化**（N=1024 时约 +54 dB，N=4096 时约 +66 dB）。这个轴**不是 dBFS，也不是 dBm，参考电平未定义**，换个 fft_size 数值整体平移 12 dB。
- 对比：`signal_spectrum.py:46` 做了正确的 PSD 归一化 `|sp|**2/(wpow*sr)`。两个模块同一个项目内 dB 尺度不一致。
- `+1e-12`（line 108）是绝对底噪，与未归一化的功率叠加，进一步污染小信号读数。
- 建议：周期图标定应为 `P = |FFT(x·w)|² / (fft_size·sum(w²))`（功率谱），再 `10*log10`；若要 dBFS 还需按满量程幅度归一。

### B2. `sdr_tools.py:2866` 配合 `spectrum_processor.py:137` — 绝对阈值 -60 dB 在未标定尺度上几乎必然失效

```python
# sdr_tools.py:2866
signals = spec.find_signals(spectrum, threshold_db=-60)
```

- `find_signals` 的 `threshold_db` 默认 `-60.0`（`spectrum_processor.py:137`）是**绝对门限**。
- 但按 B1 的未标定尺度，典型噪声底在 `10*log10(N*σ²)` 量级（N=1024、σ≈0.1 时约 +10 dB），比 -60 dB **高 70 dB 以上**。
- 后果：`powers_db > -60` 对几乎所有 bin 为真 → 整个带被合并成**一个横跨全采样率的"信号"**，`num_signals_detected` 恒为 1，带宽 ≈ 全采样率。
- 对比正确做法：`signal_spectrum.py:61-62` 用 `noise + margin_db`（相对底噪门限），以及 `signal_analysis.py:37-40` 用 `median(spectrum)+threshold_db`。`find_signals` 是三者里唯一用绝对门限的，与未标定 dB 叠加必然出错。
- 建议：`find_signals` 应改为相对底噪（`noise_floor + margin_db`），与项目内其他模块对齐。

### B3. `signal_analysis.py:169-176` — `estimate_ber` 的错误判定阈值逻辑可疑

```python
for point in constellation:
    dists = np.abs(ref_points - point)
    min_idx = np.argmin(dists)
    threshold = np.mean(dists) * 0.5     # ← 每个点重新算
    if dists[min_idx] > threshold:
        errors += 1
```

- `threshold = 0.5 * mean(到全部参考点的距离)` 是**逐点自适应**的：一个靠近某参考点的点，其最近距离天然远小于 mean(dists)，几乎永不判错；而簇中心（正确判决区）附近的点反而可能被判错。该判据与真实"误符号/误比特"无单调关系。
- 且没有任何比特映射（最近邻 → bit error），返回的是"离群点比例"，不是 BER，与函数名/文档不符。
- 叠加：该函数**全仓零调用**（grep 仅命中定义处），属死代码。
- 建议：删除或改用固定判决半径（星座点间距的某比例）+ 格雷映射计 bit error。

---

## 三、[占位] 发现（有实现但未接线/未完成）

### P1. `spectrum_processor.py:244-261` — zoom/pan 只存状态，从不作用于频谱数据

- `zoom()` / `pan()` 仅写入 `self._zoom_factor` / `self._pan_offset`；`get_view_range()`（line 257-261）据此算出一个频率范围字符串。
- 但 `compute_spectrum()`（line 111）**永远返回整段 `[-sr/2, sr/2]` 的频率轴与功率轴**，从不按 `_zoom_factor`/`_pan_offset` 裁剪或重采样。
- 调用方 `sdr_tools.py:2882-2896` 把 `get_view_range` 结果**仅作为文本回给 LLM**（"视图范围 X-Y MHz"），LLM 以为缩放生效了，但实际拿到的频谱数据仍是全带。属于"UI/视图假象"。
- 建议：要么在 `compute_spectrum` 内按 view range 裁剪 bin 区间，要么明确注释"视图变换由前端/LLM 侧自行解释"。

### P2. `spectrum_processor.py:263-287` + `sdr_tools.py:654,2907-2926` — 瀑布图未接线

- `compute_waterfall` 全仓**零调用**（grep 仅命中定义处）。
- 截图工具 `sdr_spectrum_screenshot` 声明了 `include_waterfall` 参数（`sdr_tools.py:654`），但实现 `_spectrum_screenshot`（`sdr_tools.py:2898-2926`）**只画单帧频谱，完全没读这个参数**，更没调 `compute_waterfall`。
- 即"瀑布图"是一个对外宣传、对内未实现的功能。
- 附带问题（即便接上也存在）：`compute_waterfall` 的 `times = np.arange(len(samples_blocks))`（line 278）只是块序号，**没有真实时间戳**；无环形缓冲/无帧间连续状态，多帧之间是否连续完全依赖调用方喂连续块，本类不保证。

### P3. 实时频谱无平均/无重叠 —— 与"实时"预期不符

- `compute_spectrum` 对 `num_samples`（默认 = fft_size，见 `sdr_tools.py:2861`）做**单次 FFT，无帧平均、无重叠、无 peak-hold**。
- `signal_spectrum.py:37` 用了 `hop = fft_size//2`（50% 重叠 Welch 平均），底噪稳定；而实时路径走的是 `SpectrumProcessor` 这条**无平均**路径， trace 会逐帧抖动。
- 好的一面：本项目频谱是**工具调用按需触发**（`sdr_spectrum_analyze`，非高频流式 push），所以"推送频率"本身不会导致 UI 卡顿——每次 LLM 调一次工具算一帧 FFT，N=1024~4096，开销很小。代价是没有连续刷新的实时谱，也没有连续瀑布。

---

## 四、[建议] 改进项

### 性能 / 冗余

- `spectrum_processor.py:329-330`：`_spectral_centroid`（line 354）和 `_spectral_flatness`（line 363）**各自对同一段 samples 又做了一次 FFT**，可复用同一次 `np.fft.fft` 结果，省一半 FFT。
- `spectrum_processor.py:157-193`、`signal_analysis.py:47-81`：连续段检测用 Python `for` 循环逐 bin 扫描。N≤4096 时无所谓，但可用 `np.diff` + 边沿向量化（`np.where(np.diff(above.astype(int))==1/-1)`）。
- `signal_analysis.py:169`：`estimate_ber` 逐点 Python 循环可向量化为 `scipy.spatial.cKDTree` 或广播矩阵；但函数当前是死代码（见 B3）。
- `spectrum_processor.py:128`：`timestamp=__import__("time").time()` 应为顶部 `import time`。

### 数值 / 算法

- `spectrum_processor.py:213-225`：抛物线亚 bin 插值直接作用在 **dB（对数）** 轴上。标准做法是在**线性幅度/功率**上插值再换算频率，对数轴会引入偏置。
- `spectrum_processor.py:119-120`：噪声底取"最低 10% 平均"，若某强信号占用 >90% 带宽，底噪估计被污染；`signal_spectrum.py:61` 用 `np.median` 更稳健，建议对齐。
- `spectrum_processor.py:172,188`：字段名 `center_freq` 实际取的是段内**峰值频率**，并非段中心频率 `(start+end)/2`，与文档注释（"中心频率"）不符，下游易误用。
- `signal_analysis.py:200-209`：-3dB 带宽取 `spectrum > peak-3dB` 的**最左~最右**区间，若带内有多个分离峰，会把中间谷底也算进带宽；建议逐峰求 -3dB 宽度。
- `spectrum_processor.py:380-389` `_guess_modulation`：`freq_std` 用绝对 Hz 门限（1000 Hz），随采样率/带宽变化，无归一化，跨场景阈值不可移植。

### 与 UI / 调用方一致性

- 本模块 dB 尺度（B1）与 `signal_spectrum.py` 的 PSD 尺度不一致，两个工具给 LLM 的"功率 dB"含义不同，建议统一标定口径。
- `signal_analysis.py:99` `freq = np.diff(phase)` 单位是 rad/sample，阈值（0.2/0.8 等）是无量纲门限；而 `spectrum_processor.py:312` 的 `inst_freq` 换算成了 Hz。两处"频率标准差"单位不同，复用时需注意。

---

## 五、确认无问题的点（防误报）

- FFT 全部使用 `numpy.fft`（`spectrum_processor.py:106`、`signal_spectrum.py:45,52`），无手工 DFT 循环。
- `signal_spectrum.py` 的窗函数（Hann）、50% 重叠、`wpow` 归一化、`fftshift` 后频率轴（line 59 `center+(arange-N/2)*df`）均正确；`frames==0` 的补帧兜底（line 50-55）也处理了 N<fft_size 边界。
- `signal_analysis.detect_signals`（line 22-83）、`identify_modulation`（line 90-149）、`detect_interference`（line 228-264）均为真实启发式实现，非空壳；且 `sdr_tools.py:5683` 在调 `detect_interference` 前已做 `powers_db - noise_floor` 相对化，抵消了 B1 的绝对阈值问题（仅此一处调用方做了补偿，`find_signals` 调用方没做——见 B2）。
- 未发现任何函数"只返回零数组或固定常量"的空壳。

---

## 六、优先级建议

1. **P0**：B1（dBFS 标定）+ B2（`find_signals` 绝对门限）——直接影响"找台/找信号"主链路输出正确性。
2. **P1**：P1（zoom/pan 假视图）、P2（瀑布图未接线）——功能对外宣称但未实现。
3. **P2**：B3 删除/重写 `estimate_ber`、性能冗余（重复 FFT）、抛物线插值改线性轴。

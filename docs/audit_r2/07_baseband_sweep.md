# 07 · baseband_io / sweep 深度审查报告

- 审查范围：`mbdsdr_ai/baseband_io.py`（87 行）、`mbdsdr_ai/sweep.py`（238 行）
- 关联核对：`mbdsdr_ai/sdr_tools.py`（sweep 工具包装层 2259–2318）、`mbdsdr_ai/sdr_backend.py`（基类 101–163、RTL 后端 486–570）、`mbdsdr_ai/dsp.py:331`
- 审查方式：只读静态阅读 + 边界条件数值复现，未修改任何代码。

---

## 一、结论速览

| # | 级别 | 位置 | 一句话 |
|---|---|---|---|
| 1 | 🔴 [真bug] | `sdr_tools.py:2293`（被 `sweep.py:151` 间接放大） | sweep 的 acquire 回调吞掉 `set_frequency` 返回 False，越界时静默用旧频率继续扫 |
| 2 | 🔴 [真bug] | `sweep.py:127` | `overlap` 参数语义反向：0.5 凑巧正确，0.9 实际只剩 10% 重叠 |
| 3 | 🟠 [占位] | `baseband_io.py` 全文 | 只写自家 `.iq`+`.json`，不写 WAV / CS16，与 SDR++ 录制格式不兼容 |
| 4 | 🟠 [占位] | `dsp.py:331` + 全仓 grep | 无 sounddevice/pyaudio，`audio_to_playback` 只返 float32 数组，无实时声卡播放 |
| 5 | 🟠 [占位] | `sdr_backend.py:122,500` | 增益只有单标量 `set_gain`，无 LNA/VGA/基带分段接口 |
| 6 | 🟡 [建议] | `baseband_io.py:67` | `load_iq` 直接 `iq.tolist()`，长录音会撑爆内存 |
| 7 | 🟡 [建议] | `baseband_io.py:37,63` | numpy tofile/fromfile 用原生字节序，未显式 little-endian |
| 8 | 🟡 [建议] | `sweep.py:152,172` | 全部采空 / 越界采错时静默返回空 activities，无告警 |
| 9 | ✅ | 两文件全文 | 无 `NotImplementedError` 空壳、无固定桩返回 |

---

## 二、详细发现

### 🔴 [真bug] #1 — sweep 吞掉 `set_frequency` 越界 False（已知问题传导）

**位置**：
- 包装层：`mbdsdr_ai/sdr_tools.py:2291-2303`
- 放大点：`mbdsdr_ai/sweep.py:150-153`
- 根因：`mbdsdr_ai/sdr_backend.py:101-108`

**代码事实**：

```python
# sdr_tools.py:2291
def acquire(center, sample_rate, n):
    if hasattr(backend, "set_frequency"):
        backend.set_frequency(int(center))   # ← 返回值被丢弃
        time.sleep(0.03)
    got = []
    ...
```

```python
# sdr_backend.py:101
def set_frequency(self, freq_hz: float) -> bool:
    if not self.status.connected:
        return False
    if freq_hz < self.device.frequency_range[0] or freq_hz > self.device.frequency_range[1]:
        return False                          # ← 越界静默 False，不改 status.frequency_hz
    self.status.frequency_hz = freq_hz
    return True
```

```python
# sweep.py:150
for center in centers:
    iq = acquire(center, sr, dwell_samples)
    if iq is None or len(iq) == 0:
        continue                              # ← 只判空，不判"是否真的调到了 center"
```

**后果**：用户请求扫频范围超出器件 `frequency_range` 时（例如 RTL-SDR 下限 ~24 MHz，却从 10 MHz 扫起；或 Airspy 上限 1.8 GHz 却扫到 2 GHz），`set_frequency` 返回 False 且**不更新当前频率**，随后 `read_samples` 仍按上一次合法频率采样。`sweep_scan` 拿到的是重复的同一段频谱，却把它当成不同 center 的 PSD 拼到全局网格上——结果是边缘频段出现"复制粘贴"的伪峰，活动段提取会误报，且**全程无任何错误抛出**（外层 `try/except` 也捕不到）。

这正是题目所述 `sdr_tools.py:2392` 附近"越界返回 False 被 try/except 吞掉"的同源问题，且 sweep 路径上**比书签跳转更危险**：书签跳转只跳一次，用户立刻能看到"频率没变"；sweep 是 N 段连续拼接，错误被平均掩盖。

**复现方式**：
1. 接 RTL-SDR，先把频率调到 100 MHz。
2. 调 `sdr_sweep_scan`，参数 `f_start_mhz=10, f_stop_mhz=20, sample_rate_hz=2.4e6`。
3. centers ≈ 11.2 MHz、12.4 MHz、…、18.8 MHz，全部低于 RTL 下限。
4. 每段 `set_frequency` 都返回 False，频率停在 100 MHz；`read_samples` 反复返回 100 MHz 附近的同一段信号。
5. 返回的 `SweepResult.activities` 会在 10–20 MHz 轴上拼出一条形状来自 100 MHz 电台的假谱，且 `noise_floor_db` 不是噪声底而是该电台的功率。

**修复方向（仅记录，不改码）**：`acquire` 内检查 `set_frequency` 返回值，False 时抛 `RuntimeError` 或返回 `None`；`sweep_scan` 在某 center 采空时把对应权重记为无效并在最终结果里报 `centers_failed`。

---

### 🔴 [真bug] #2 — `overlap` 参数语义反向

**位置**：`mbdsdr_ai/sweep.py:126-127`

```python
if step_hz is None:
    step_hz = sr * max(0.1, min(0.9, overlap))
```

**对照 docstring**（`sweep.py:116`）：
> overlap : 相邻调谐段重叠比例（0~0.9），默认 0.5，抑制边缘滚降。

**分析**：若 `overlap=0.5` 表示"50% 重叠"，步进应为 `step = sr * (1 - overlap) = 0.5*sr`。代码写成 `step = sr * overlap`。
- `overlap=0.5` → step=0.5*sr → 实际重叠 50%。**凑巧正确**（0.5 自逆）。
- `overlap=0.9` → step=0.9*sr → 实际重叠仅 10%，与 docstring 承诺的"90% 重叠抑制边缘滚降"完全相反。
- `overlap=0.1` → step=0.1*sr → 实际重叠 90%，段数翻 9 倍，扫描时间翻 9 倍，用户完全无法预期。

**复现方式**：
```python
# 伪代码语义检查
for ov in (0.1, 0.5, 0.9):
    step = 2.4e6 * max(0.1, min(0.9, ov))
    actual_overlap = 1 - step/2.4e6
    print(ov, "->", round(actual_overlap, 2))
# 期望: 0.1/0.5/0.9；实际: 0.9/0.5/0.1
```
（0.5 是不动点，所以默认参数下所有 demo 都"看起来对"。）

**附带**：`sdr_tools.py:2283` 把 `overlap` 直接透传，工具层未做校正。

---

### 🟠 [占位] #3 — baseband_io 格式与 GNU Radio / SDR++ 兼容性

**位置**：`mbdsdr_ai/baseband_io.py:29-50`

**事实**：
- 写盘：`np.float32` 交错 I/Q 裸流，扩展名 `.iq`（`baseband_io.py:33-37`）。
- sidecar：自定义 JSON，键名 `sample_rate / center_freq_hz / format="float32_interleaved"`（`baseband_io.py:38-45`）。
- 模块 docstring（`baseband_io.py:4`）自称"SDR++ 的录制功能"。

**兼容性核对**：
| 消费方 | 能否直接打开 | 说明 |
|---|---|---|
| GNU Radio `file_source` + `complex` | ✅ 同机型可读 | 假定原生 float32；`.iq` 扩展名不影响 GNU Radio |
| SDR++ 录制/回放 | ❌ | SDR++ 原生录制为 `.wav`（CS16 PCM）或带自身 sidecar 命名约定的 raw；不识别 `.iq` + 自定义 JSON |
| SDR# / GQRX | ❌ | 同上 |
| 本项目 `FileIQBackend` | ✅ | `sdr_backend.py:988` 读 `meta.get("center_freq", …)`，键名匹配 |

**结论**：模块实质是"项目内自闭环录制"，与 docstring 宣称的 SDR++ 对标不符。元数据（采样率/中心频率）**有保存**到 sidecar，这一点合格；但格式选型未覆盖 WAV/CS16 这两个最常见的互操作格式。

**建议**（不改码）：增加 `save_iq_wav`（`scipy.io.wavfile.write`，int16 交错）路径，或在 docstring 中把"SDR++ 的录制功能"改为"项目内录制/回放"。

---

### 🟠 [占位] #4 — 无实时声卡播放接口

**位置**：
- 全仓 grep `sounddevice|pyaudio|sd\.|pa\.|AudioOutputStream` → **零命中**。
- `mbdsdr_ai/dsp.py:331` `audio_to_playback` 只做：去 DC → 低通 → `resample_poly` 到 48 kHz → 归一化 → 返回 `np.float32` 数组（`dsp.py:359`）。
- 调用点 `sdr_tools.py:3740` 把返回值塞给 ToolResult，最终回到 LLM 文本通道，**不触碰扬声器**。

**结论**：解调音频只能以数组形式返回给上层（再由上层决定存 WAV 或丢弃），本项目内核没有任何"边收边听"的实时音频环回。对于"对讲机守听 / 航空波段收听"这类用例，用户拿不到声音。

**级别**：占位/设计缺口，不是 bug。若产品定位是 LLM 文本分析而非人工监听，则合理；否则需引入 `sounddevice.OutputStream`。

---

### 🟠 [占位] #5 — 增益只有单标量，无 LNA/VGA/基带分段

**位置**：
- 基类 `sdr_backend.py:122-127`：`set_gain(gain_db)` 把增益钳到 `[0, max_gain]` 一个标量。
- RTL 后端 `sdr_backend.py:500-508`：`self._sdr.gain = float(gain_db)`，rtlsdr 库本身支持的 `tuner_gain_values` / LNA·MIX·VGA 三级增益**未暴露**。
- Airspy / SDRplay 后端（grep 命中 `sdr_backend.py:701,804,891` 的 `set_frequency`）也未见 `set_lna_gain / set_vga_gain / set_mixer_gain` 之类方法。

**结论**：RTL-SDR 用户无法独立设置 LNA 增益、混频增益、VGA 增益——而这是弱信号接收时最常用的调参手段（例如航空波段 118–136 MHz 通常需要 LNA=Manual、VGA 拉高）。当前只有"自动 AGC 或单总增益"两档。

---

### 🟡 [建议] #6 — `load_iq` 把整个 IQ 转 Python list

**位置**：`baseband_io.py:67`

```python
return { ..., "iq": iq.tolist(), ... }
```

2.4 MS/s × 60 s 的录制 = 1.44e8 个 complex128，转成 Python list 约 1.44e8 × ~104 B（Python complex 对象 + list 指针）≈ **15 GB**。LLM/JSON 通道根本消费不了这种体量。

**复现**：
```python
info = save_iq(np.ones(2_400_000, dtype=complex), "/tmp/min.iq", 2.4e6)
back = load_iq("/tmp/min.iq")   # 1 秒录音即生成 4.8e6 个 Python float，~300 MB
```

**建议**：默认返回 numpy 数组；仅在 LLM 显式要求时再切片 tolist。

---

### 🟡 [建议] #7 — 字节序未显式声明

**位置**：`baseband_io.py:37`（`interleaved.tofile`）与 `baseband_io.py:63`（`np.fromfile(..., dtype=np.float32)`）。

numpy 用主机原生字节序。x86/ARM 均为 little-endian，日常无碍；但 sidecar 里 `"format": "float32_interleaved"` 未注明 `<f4`（小端）。若日后在 big-endian 主机回放或与网络字节流对接，会静默错样。

**建议**：写盘前 `interleaved = interleaved.astype("<f4")`，sidecar format 改为 `"cf32le"`。

---

### 🟡 [建议] #8 — sweep 全采空 / 越界采错时静默

**位置**：`sweep.py:152-153, 172-173`

```python
if iq is None or len(iq) == 0:
    continue
...
if not np.any(in_range):
    return SweepResult(grid_freqs, power_db, [], 0.0, 0.0, centers)
```

- 所有 center 都采空（设备忙 / USB 断流）→ 返回 `noise_floor_db=0, threshold_db=0, activities=[]`。
- 结合 #1，越界采错时返回的"结果"形状正常但数据是错的，外层 ToolResult 仍标 `success=True`。

**建议**：在 `SweepResult` 增加 `centers_failed: List[float]` 与 `coverage_ratio`，工具层据此告警。

---

## 三、已核对但**未发现**的问题

1. **空壳函数**：`baseband_io.py`、`sweep.py` 内**没有任何** `raise NotImplementedError`，也没有 `return None/0` 桩函数。`sdr_backend.py:95,163` 的 `NotImplementedError` 在抽象基类里，不在本次审查范围。
2. **PSD 拼接数值正确性**：`sweep.py:159-168` 采用"dB → 线性 → `np.add.at` 累加 → 按权重平均 → 回 dB"，重叠段平均方式正确；`_segment_psd` 的 Welch 窗口归一化（`win_power`）与 `np.fft.fftshift` 频率轴方向正确。
3. **窄带分支**：当扫频范围小于一个瞬时带宽时（`sweep.py:133-135`），取中点单次采集，全局网格仍按 `f_start±half` 展开，逻辑自洽。
4. **活动段合并**：`_extract_activities` 允许 ≤ `gap_bins` 的凹陷把相邻超门限段合并，`mean_db` 用 `seg_p[mask]` 排除凹陷 bin，未把 gap 计入均值，正确。
5. **PLL 稳定**：包装层 `sdr_tools.py:2294` 在每次 `set_frequency` 后 `time.sleep(0.03)`，并在 `finally` 恢复扫描前频率/采样率，资源清理合格。
6. **IQ 交错格式自检**：`baseband_io.py:24` 对奇数长度实数列表抛 `ValueError`，`__main__` 块验证了虚部未丢，单元自检存在。

---

## 四、修复优先级建议

| 优先级 | 项 | 理由 |
|---|---|---|
| P0 | #1 越界 False 吞掉 | 直接产生错误结果且无告警，用户会拿假谱做判断 |
| P1 | #2 overlap 反向 | 非默认参数下扫描时间/重叠量完全失控 |
| P2 | #4 实时声卡播放、#5 分段增益 | 产品能力缺口，影响"守听/弱信号调参"核心场景 |
| P3 | #3 WAV 互操作、#6 tolist 内存、#7 字节序、#8 静默失败 | 健壮性与互操作 |

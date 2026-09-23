# R2 代码审查 04：CFO 估计/校正 & 频率管理器

- 审查范围：`mbdsdr_ai/cfo.py`（139 行）、`mbdsdr_ai/frequency_manager.py`（283 行）
- 关联链路：`mbdsdr_ai/sdr_tools.py`（CFO 工具封装 / bookmark goto）、`mbdsdr_ai/sdr_backend.py`（set_frequency/set_ppm）
- 审查方式：只读，逐行阅读真实代码 + 理论对照

---

## 一、CFO 估计算法（cfo.py）

### 结论：实现的是「FFT 粗估 + Kay 精估」两级方案，**不是 M&M / Fitz / L&R**
本模块 docstring（cfo.py:8-18）已明确声明两级结构，未实现 M&M / Fitz / L&R。对照审查点 1 的回答：这三种经典算法在本代码库中**不存在**，不属于“实现错误”，而是“未实现”。下面只审实际存在的两段。

### 1.1 FFT 粗估 `estimate_cfo_fft` — 正确
- cfo.py:48-51：`np.hanning(n)` 加窗 → `fftshift(fft)` → `abs**2`，频率轴 `fftshift(fftfreq)` 与谱轴对齐，无 bin 错位。
- cfo.py:28-36 `_parabolic_subbin`：三点抛物线亚 bin 插值
  `δ = 0.5·(y0−y2)/(y0−2y1+y2)`，`peak = freqs[k] + δ·bin_hz`（cfo.py:61）。
  公式与符号均正确，落在 [-0.5, 0.5] bin。
- 估计范围：FFT 天然无模糊区间 `[-fs/2, fs/2]`，与理论一致。
- search_hz 邻域选峰（cfo.py:52-56）：`np.where(band, power, -inf)` 后取全局 argmax，下标与 `freqs[k]` 对应正确，无 off-by-one。

### 1.2 Kay 精估 `estimate_cfo_kay` — 加权形状正确
- cfo.py:71：`dphi = angle(x[1:]·conj(x[:-1]))` 相邻样本相位差，正确。
- cfo.py:74-75：窗函数
  `w = 1.5·(1 − ((idx−(m−1)/2)/((m+1)/2))²)`，再 `w/=sum(w)`。
  手算验证：m=N−1 时，该抛物线在两端取 `1−((m−1)/(m+1))² = 4m/(m+1)²`、顶点取 1，顶点/端比 ≈ N/4，**与 Kay(1989) 的 `w(n)∝(n+1)(N−1−n)` 抛物窗形状完全一致**（仅差一个归一化常数，已由 `w/=sum(w)` 吸收）。
- cfo.py:76：`fs/(2π)·Σw·dphi`，把 rad/sample 换算成 Hz，正确。
- 无模糊范围：`|dphi|<π ⇒ |Δf|<fs/2`。docstring（cfo.py:13）要求“粗估补偿后残余必然满足”，工程上成立（粗估后残余 ≪ fs/2）。

### 1.3 CFO 校正方向 `correct_cfo` — 正确，无混叠
- cfo.py:83：`x·exp(−j2π·offset_hz·n/fs)`。
- 方向核验：baseband 峰值 `offset = peak − f_expected > 0` 表示信号被 LO 偏在“上方”，乘 `exp(−j2π·offset·n/fs)` 即把信号**向下**混频回 DC，符号正确。
- 与工具层建议自洽：sdr_tools.py:5510 输出“将接收频率上移 offset_hz Hz”——即增大 LO，等价于在基带向下混频，二者方向一致，不存在“加/减反了”。
- 混叠：校正只是复乘单音旋转，不改变采样率、不产生混叠；n 用 `np.arange(len(x))`，长序列相位单调无 wrap 问题。

### 1.4 门控与残余估计 — 设计合理，两处小问题
- cfo.py:123-128：粗补后算圆相干性 `_phase_coherence`（cfo.py:103-107），仅当 `coh≥0.9` 且 `|fine_raw|≤0.5·bin` 才采纳 Kay，否则回退纯 FFT。这是稳健做法（Kay 低 SNR 阈值门限）。
- cfo.py:132-133：残余用 FFT 在 `0±max(bin_hz, |fine|+bin_hz)` 内重估。

  > [建议] cfo.py:132 残余搜索窗偏窄且含 DC：RTL-SDR 直采 IQ 普遍有 DC 泄漏，若 DC bin 功率落在窗内，残余会被 DC “吃掉”而显示 ≈0，掩盖真实残余。建议残余估计时剔除 ±1~2 bin 的 DC 区，或改用 Kay 在粗补信号上的结果作残余参考。

  > [建议] cfo.py:138 `res.corrected = corrected`：在 `@dataclass CFOResult`（cfo.py:86-95）外部动态挂属性，未在 dataclass 声明字段。类型不完整、`asdict()`/序列化拿不到 corrected。建议把 `corrected` 加为 dataclass 字段（或单独返回元组）。

---

## 二、多 VFO / 频段切换 / 多普勒（frequency_manager.py）

### 2.1 多 VFO：[空壳] — 只有单 VFO + 书签跳转
- 全文件无 VFO 列表、无 VFO 状态机、无“同时多个 VFO”字段。`Bookmark`（frequency_manager.py:25-35）是单频点/单区间记录，没有 VFO A/B、没有 waterfall 多峰同时跟踪。
- 实际跳转路径在 sdr_tools.py:2365-2403 `_bookmark_goto`：取 `b.center_hz` → 一次 `backend.set_frequency(int(freq))`（sdr_tools.py:2393）。这是**单 LO 一次调谐**，不是多 VFO。
- 即：本模块名虽叫 FrequencyManager，实质是“书签库 + 单频点跳转”，与 SDR++ 的多 VFO 无关。

### 2.2 频段边界检查：[空壳]（manager 内无）+ 链路上有但被吞
- frequency_manager.py 自身**没有任何频率边界检查**：`add()`（frequency_manager.py:267-275）不校验 freq_hz 是否在设备可调范围内；`find()` 对任意数字都生成临时 Bookmark（frequency_manager.py:256-257）。
- 后端基类其实**有**边界检查：sdr_backend.py:105 `if freq_hz < range[0] or > range[1]: return False`。
- 但该检查结果被吞掉（见第四节 [真bug]）。

### 2.3 多普勒补偿接口：[空壳] — 无任何接入点
- frequency_manager.py 全文无 Doppler、Doppler rate、卫星预测、`orbit` 联动字段。
- cfo.py 也无多普勒 API：它是对**已知标称频率 `f_expected`** 的一次性残差估计，对过境卫星会把瞬时多普勒**吸收进 `offset_hz`**，但不跟踪、不刷新、不与 `orbit.py` 联动。没有“传入预测多普勒后扣除”的入口。
- 结论：审查点 5 要求的“卫星多普勒接入点”**未预留**，需要后续在 `Bookmark`/调谐链路加 `doppler_hz` 偏移量参数。

---

## 三、单位与 off-by-one 链路（Hz / kHz / MHz）

### 3.1 内部单位一致，无 Hz/kHz/MHz 混淆
- frequency_manager.py 全程内部单位为 **Hz**：`_m()`（frequency_manager.py:51-52）`mhz*1e6`；`center_hz`、`bandwidth_hz`、`start/end_hz` 均 Hz。
- 书签 goto：`freq = b.center_hz`（Hz）→ `backend.set_frequency(int(freq))`（sdr_tools.py:2393），后端签名 `set_frequency(freq_hz)`（sdr_backend.py:101），单位对齐。
- SI4732 子类换算正确：sdr_backend.py:706 `freq_mhz = freq_hz/1e6`、:709 `freq_khz = int(freq_hz/1000)`，换算方向与量级正确。
- RTL-SDR：sdr_backend.py:490 `self._sdr.center_freq = freq_hz`（pyrtlsdr 单位即 Hz），正确。

### 3.2 [真bug，轻微] `find()` 数字解析单位歧义
frequency_manager.py:252：
```python
hz = v * 1e6 if ("." in s or v < 100000) else v
```
启发式：“带小数点 或 数值<100000 当作 MHz，否则当 Hz”。
- 反例：用户输入整数 `99999`（本意 99999 Hz = 99.999 kHz，例如 IF 频点/音频/窄 CW），因 `v<100000` 被当成 **99999 MHz = 99.999 GHz**，直接跳到设备量程外。
- 输入 `100000` 恰好等于阈值时走 else（按 Hz），边界靠 `<` 而非 `≤`，行为不直观。
- 影响面：日常台站都是 MHz，触发概率低；但作为通用频率解析入口，这是真实的单位误判。
- 建议：要求显式单位后缀（`145.8M` / `145000k` / `145000000`），或至少把“纯整数且 < 1e6”按 kHz 解析，避免默认放大 1e6 倍。

---

## 四、链路问题（调谐失败被静默吞掉）

> [真bug] sdr_tools.py:2392-2395（frequency_manager 的实际调谐调用方）
> ```python
> try:
>     backend.set_frequency(int(freq))
> except Exception as e:
>     return ToolResult(success=False, ...)
> ```
> 后端基类 sdr_backend.py:105-108 越界时是 **`return False`**（不抛异常）。因此当书签/目标频率落在设备 `frequency_range` 之外时：
> - `set_frequency` 返回 False 但不进 except；
> - 函数继续走到 sdr_tools.py:2402 返回 “已跳转到 …”，**向用户谎报成功，实际 LO 未动**。
>
> 这正好命中审查点 4“频段切换是否越界”：边界检查存在，但其返回值在 manager→backend 链路上被丢弃。
> 建议：`if not backend.set_frequency(int(freq)): return ToolResult(success=False, content="频率超出设备可调范围")`。`_watch_capture_tool`（sdr_tools.py:2445）有同样问题。

---

## 五、书签数据准确性抽查（frequency_manager.py）

抽查未发现数值错误：
- NOAA APT 137.100 / 137.620 / 137.9125 MHz（frequency_manager.py:109-111）正确。
- FT8 dial 频点 1.840/3.573/7.074/10.136/14.074/18.100/21.074/24.915/28.074/50.313/144.174（frequency_manager.py:130-137）与 WSJT-X 标准一致。
- GPS L1 1575.42 / 北斗 B1I 1561.098 / GLONASS L1 1602（:174-179）、BPM 2.5/5/10/15 MHz（:167-170）、ADS-B 1090 / 1030（:144-147）均正确。
- Bookmark 位置参数构造（如 :74 `B(name, _m(121.5), "AM", "航空", 10e3, note=...)`）与 dataclass 字段顺序 `name,freq_hz,mode,category,bandwidth_hz,...` 对齐，无错位。

---

## 六、发现汇总

| # | 级别 | 位置 | 说明 |
|---|------|------|------|
| 1 | [真bug] | sdr_tools.py:2392-2395（chain，源自 manager 无校验） | `set_frequency` 越界返回 False 被 except 吞掉，谎报跳转成功 |
| 2 | [真bug，轻微] | frequency_manager.py:252 | 纯整数 <100000 被默认当 MHz，单位误判（如 99999 Hz→99.999 GHz） |
| 3 | [空壳] | frequency_manager.py 全文 | 无多 VFO（仅单 LO 跳转），无频率边界检查，无多普勒接入点 |
| 4 | [建议] | cfo.py:132-133 | 残余 FFT 窗窄且含 DC，DC 泄漏会掩盖真实残余；建议剔 DC 或用 Kay 参考 |
| 5 | [建议] | cfo.py:138 | `res.corrected` 动态挂属性，应声明为 dataclass 字段 |
| 6 | [建议] | cfo.py / sdr_tools.py:5510 | CFO 只打印“建议上移 X Hz”，未闭环调用 `set_ppm`/调 LO；无跟踪式多普勒接口 |

**算法正确性结论**：实际实现的 FFT 粗估 + Kay 精估在数学上正确，校正方向正确，无混叠，单位链路（Hz）端到端一致。审查点 1 中提到的 M&M/Fitz/L&R 未实现（非错误）；审查点 3/5 的多 VFO 与多普勒接口为空壳；审查点 4 的边界检查在后端存在但被上层吞掉（发现 #1）。

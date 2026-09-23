# MBDSDR 第二轮深度审查 — 性能与工程依赖

> 审查人：子agent（性能/依赖专项）
> 日期：2026-09-24
> 范围：dsp.py / sdr_backend.py / spectrum_processor.py / sstv_decoder.py / ft8_*.py / desktop/ / requirements.txt

---

## 一、依赖分析

### 1.1 核心依赖（requirements.txt）

| 依赖 | 用途 | 是否必需 | 安装体积估计 |
|------|------|----------|-------------|
| numpy≥1.24 | DSP/频谱 | **必需** | ~30 MB |
| requests≥2.28 | LLM API | **必需** | ~0.5 MB |
| websocket-client≥1.6 | 连硬件 | **必需** | ~0.3 MB |
| websockets≥12 | 模拟服务器 | **必需** | ~0.5 MB |
| sgp4≥2.22 | 卫星轨道 | **必需** | ~0.1 MB |
| Pillow≥9.0 | SSTV/APT PNG输出 | **必需** | ~5 MB |

**结论：核心依赖仅 6 个，总计 ~36 MB，无 tensorflow/pytorch/scipy 强制依赖。设计良好。**

### 1.2 可选依赖（已注释降级）

| 依赖 | 缺失降级方案 | 实际使用模块数 |
|------|-------------|---------------|
| scipy | numpy 简化版 | 22 个模块（但多为函数内延迟 import） |
| skyfield | sgp4 简化坐标 | 1 个模块 |
| pyserial | 无串口 | 3 个模块 |
| PySide6 | 无 GUI，仅 CLI/MCP | desktop/ 全部 |
| SoapySDR | 无 SDR 硬件接入 | 3 个模块 |

### 1.3 桌面端依赖（desktop/requirements.txt）

仅额外增加 **PySide6≥6.5**（~100 MB），其余复用核心依赖。matplotlib 已注释掉（用 QPainter 替代），明智。

### 1.4 关键发现

- **[建议] scipy 被 22 个模块引用但列为可选**：多数模块在函数内部 `from scipy.signal import ...` 延迟导入（如 dsp.py:267, dsp.py:312, dsp.py:339），这是好的做法。但 sstv_decoder.py:21-25 在模块顶层 try-import scipy，无 scipy 时走手写 WAV 解析 fallback（sstv_decoder.py:138-168），也可用。
- **[真bug] ft8_ldpc.py:28-33, 45-49 硬编码读取外部 Fortran 文件**：`repos/wsjtx/lib/ft8/ldpc_174_91_c_parity.f90`。如果 repos/wsjtx 不存在，`_parse_graph()` 抛 FileNotFoundError，FT8 解码完全不可用。H矩阵应该内嵌为 Python 常量。
- **[建议] 无重量级 AI 框架依赖**：项目没有强制依赖 torch/tensorflow，AMR 分类器（amr.py）用纯 numpy 实现，适合 Chromebook 部署。

---

## 二、性能瓶颈量化

### 2.1 DCBlocker 逐样本 Python 循环 — [真bug] 实时性致命

**位置**：`mbdsdr_ai/dsp.py:53-56`

```python
for n in range(len(x)):
    y[n] = x[n] - x_prev + r * y_prev
    x_prev = x[n]
    y_prev = y[n]
```

**实测基准**（本机 i7 级 CPU）：
- 240k 样本（100ms @ 2.4MSPS）：**125.8 ms**
- 实时因子：**1.26×**（即处理 1 秒需要 1.26 秒）
- 在 Chromebook Celeron N4020 上（约慢 3-4×）：估计 **400-500 ms** 处理 100ms 数据
- **实时因子将达到 4-5×，完全无法实时**

**影响**：DCBlocker 是 front_end() 的第一步（dsp.py:204），是所有 IQ 处理的入口。在低性能机器上，2.4MSPS 实时处理不可行。

**修复方向**：用 scipy.signal.lfilter 向量化（`lfilter([1,-1],[1,-r],x)`），或用 numpy 实现一阶 IIR 的差分方程向量化形式。

---

### 2.2 FT8 Goertzel 扫描 — [真bug] 灾难性性能

**位置**：`mbdsdr_ai/ft8_lite.py:26-38`（Goertzel），`ft8_lite.py:48-57`（扫描循环）

```python
def _goertzel(samples, rate, freq):
    for s in samples:          # Python 逐样本
        s0 = s + coeff * s1 - s2
        s2 = s1; s1 = s0
```

**实测基准**：
- 15 秒音频 @ 12kHz = 180,000 样本
- 扫描 300-3000 Hz，1Hz 步进 = **2700 次 Goertzel 调用**
- 总耗时：**52.0 秒**（即处理 15 秒音频需要 52 秒）
- 完整 decode_ft8_audio：**52.6 秒**

**计算复杂度**：O(2700 × 180000) = **4.86 亿次 Python 循环迭代**

**影响**：FT8 模式周期是 15 秒。在 Chromebook 上，一个 15 秒帧的解码需要 3 分钟以上，完全无法实时。这是整个项目最严重的性能问题。

**修复方向**：用 FFT 替代逐频点 Goertzel。12kHz 采样率下，15 秒 FFT（N=180000）只需要几毫秒。或者向量化 Goertzel。

---

### 2.3 瀑布图 QImage.setPixelColor 双层循环 — [真bug] UI 渲染瓶颈

**位置**：`desktop/spectrum_widget.py:342-359`（软件渲染版），`spectrum_widget.py:576-584`（OpenGL 版也是同样逻辑）

```python
for row in range(n_lines):          # ~200 行
    for col in range(w):            # ~800 px 宽
        color = value_to_color(...) # 创建 QColor 对象
        image.setPixelColor(col, row, color)  # Python 调用 Qt
```

**量化**：
- 每帧：200 × 800 = **160,000 次** setPixelColor 调用
- 20 FPS（timer 50ms）：**320 万次/秒**
- 每次调用涉及：数组索引 → value_to_color 插值 → QColor 构造 → Qt C++ 调用
- 估计每帧 30-80ms 在软件渲染下，已经吃掉大部分 50ms 帧预算

**注意**：OpenGL 版（SpectrumGLWidget）虽然继承自 QOpenGLWidget，但 paintGL 里仍然用 QPainter + QImage + setPixelColor（spectrum_widget.py:507-585），**没有真正用 GPU 纹理渲染瀑布图**。OpenGL 加速名存实亡。

**修复方向**：用 numpy 数组预计算颜色映射 LUT，然后 `QImage.setBits()` 一次性拷贝整块像素数据。

---

### 2.4 FT8 LDPC BP 译码 — [建议] 可接受但有优化空间

**位置**：`mbdsdr_ai/ft8_ldpc.py:67-103`

**实测**：1.04 ms/次（平均 ~10 次迭代）。不是瓶颈。

但实现用 Python dict 存储校验消息（`q: dict[tuple[int,int], float]`），在 83 校验 × ~3 变量 = ~250 条边上做迭代。向量化为 numpy 数组可以更快，但当前性能已够用。

---

### 2.5 find_spectrum_peaks 逐 bin 循环 — [建议] 按需调用，非实时路径

**位置**：`mbdsdr_ai/dsp.py:740-742`

```python
for i in range(2, n - 2):
    if mag_db[i] >= thresh and ...:
        cand.append(...)
```

**实测**：65536 点 FFT + 峰值搜索 = **32 ms**。这是"找台"功能，不是实时频谱刷新路径，可接受。

---

### 2.6 write_csv 逐行写入 — [建议] 非实时路径

**位置**：`mbdsdr_ai/dsp.py:574-575`

```python
for sample in x:
    f.write(f"{sample.real:.8f},{sample.imag:.8f}\n")
```

**实测**：10,000 样本 = 6.6 ms。CSV 格式仅用于教学/审计，建议配合大抽取。sdr_backend.py:262 的录制路径用 `np.savetxt`（向量化），更好。

---

### 2.7 SSB 解调卷积 O(N×M) — [建议] 可接受

**位置**：`mbdsdr_ai/dsp.py:401-404`

kernel_size = sample_rate/4000 = 600 taps @ 2.4MSPS。
**实测**：240k 样本 = 28.6 ms，实时因子 0.29×。在 Celeron 上约 0.8-1.0×，勉强可用。

---

## 三、内存使用分析

### 3.1 FileIQBackend 整文件加载 — [真bug] 内存爆炸

**位置**：`mbdsdr_ai/sdr_backend.py:995-1008`

所有格式都加载为 `complex128`（16 bytes/sample）：

```python
self._samples = np.asarray(np.load(self._path), dtype=np.complex128)
# cu8: raw uint8 → float32 → complex64 → complex128（3次转换）
```

**计算**：
- 2.4MSPS × 1 秒 = 2.4M 样本 × 16 bytes = **38.4 MB/秒**
- 1 分钟录音 = **2.3 GB** complex128
- 4GB RAM 的 Chromebook 完全无法回放 1 分钟以上的 IQ 文件

**更严重的是中间转换**：cu8 格式路径（sdr_backend.py:997-1000）：
1. `np.fromfile(uint8)` → 原始字节数组
2. `.reshape(-1,2).astype(float32)` → 临时 float32 数组（2× 大小）
3. `.view(complex64)` → complex64 数组
4. `np.asarray(..., dtype=complex128)` → complex128 拷贝

**峰值内存 = 原始文件 × 4-5 倍**。

**修复方向**：用 `np.memmap` 按需读取，或至少保留 complex64（8 bytes/sample）而非 complex128。

---

### 3.2 IQ 数据多余拷贝

**位置**：`mbdsdr_ai/dsp.py:513`（write_cf32），`dsp.py:534`（write_cs16），`sdr_backend.py:247-256`（录制循环）

每次写文件都做 `np.column_stack([x.real, x.imag]).flatten()` 创建临时数组。在录制循环中（sdr_backend.py:247），每 16384 样本做一次，开销不大，但可以用 `np.empty` + 视图避免。

**位置**：`mbdsdr_ai/dsp.py:94`（IQCalibrator.fit），`dsp.py:143`（apply）

`np.column_stack([x.real, x.imag])` 创建 N×2 临时数组。对大段 IQ 不必要，但 calibrator 只 fit 10000 样本，影响小。

---

### 3.3 瀑布图内存累积

**位置**：`mbdsdr_ai/spectrum_processor.py:263-287`（compute_waterfall）

`powers_db = np.array(spectra)` 把所有帧堆叠成 2D 数组。如果传入 100 帧 × 4096 bin，就是 100×4096×8 bytes = 3.2 MB，可接受。

但 desktop/spectrum_widget.py:38-81 的 `SpectrumDataGenerator.waterfall` 是 Python list，每帧 `.copy()` 一份 float32 数组，200 行 × 512 bin × 4 bytes = 400 KB，可接受。

---

## 四、部署架构

### 4.1 桌面端：原生 PySide6 Qt — 确认

**位置**：`desktop/main.py:24-29`，`desktop/main_window.py`

桌面端是原生 PySide6 Qt 应用，通过 MCP/WebSocket 连接硬件。频谱组件有 QPainter 软件渲染版和 QOpenGLWidget 版（但 OpenGL 版实际仍用 QPainter 绘制）。

### 4.2 mbdsdr-mobile.html — [空壳] 远程控制面板，非完整 SDR

**位置**：`mbdsdr-mobile.html`（23KB 静态 HTML）

这是一个**纯静态 HTML 远程控制面板**，通过 WebSocket 连接 ai-sdr Mini 硬件做调谐/音量/录音控制。它**不是**一个 Web 版 SDR：
- 没有 FFT 频谱显示
- 没有 IQ 数据处理
- 没有解调
- 只是状态显示 + 按钮控制

**不存在重复 UI 实现**：desktop 是全功能 Qt SDR，mobile.html 是极简远程控制壳。

### 4.3 无 Web 版 SDR

当前没有浏览器端实时 IQ 处理能力。如果要在 Chromebook 上用，只能跑原生 PySide6 桌面端（但 Chromebook 装 PySide6 较重）。

---

## 五、实时性与延迟

### 5.1 当前桌面端无实时 IQ 处理管线

**关键发现**：desktop/main_window.py 的 `mcp_worker.py` 只轮询状态（RSSI/SNR/GPS/IMU），**没有 IQ 数据流**。频谱 widget 显示的是 `SpectrumDataGenerator.generate()` 合成的模拟数据（spectrum_widget.py:55-83）。

`set_iq_data()` 接口存在（spectrum_widget.py:185-196）但没有被实际调用接入真实数据流。

**结论**：当前桌面端是 UI 壳 + 模拟数据，实时管线尚未接通。因此"采样→处理→显示延迟"目前不是问题——因为这条链路还不存在。

### 5.2 潜在缓冲区风险

当未来接通真实 IQ 流时：
- MockSDRBackend.read_samples（sdr_backend.py:357）每次生成新数组，无缓冲区溢出风险
- RTLSDRBackend.read_samples（sdr_backend.py:571）直接透传 pyrtlsdr 的 read_samples，pyrtlsdr 内部有环形缓冲区
- 录制线程（sdr_backend.py:209-279）用 chunk_size=16384 流式写盘，无内存堆积

---

## 六、启动时间

### 6.1 import 性能

**实测**：
- `import mbdsdr_ai`（全量）：**706 ms**
- 仅 import dsp / sdr_backend / spectrum_processor：~0 ms（已缓存）

### 6.2 __init__.py 全量导入 — [建议]

**位置**：`mbdsdr_ai/__init__.py:27-65`

顶层导入了 **40+ 个模块**，包括 agent（153KB 大文件）、sdr_tools（279KB）、decoders、pose、astronomy、amr 等。冷启动时全部加载。

**影响**：在 Chromebook SSD 上，700ms 启动时间可接受。但如果只需要 DSP 功能（如 `from mbdsdr_ai.dsp import demodulate`），也会触发全量 __init__。

**修复方向**：__init__.py 改为延迟导入（`__getattr__`），或在 CLI 入口只导入需要的子模块。

---

## 七、SSTV 解码器性能

### 7.1 已优化部分

`sstv_decoder.py:451-467` 的 `_freq_to_pixel_series` 用累积和做向量化滑动窗口均值，避免了逐像素切片（注释里明确写了"76800 次 mean 带来数十秒解码耗时"）。这是好的优化。

### 7.2 仍存在的 Python 循环 — [建议]

- **`_instantaneous_frequency`**（sstv_decoder.py:218-227）：逐过零点做线性插值，过零点数量约为采样率/频率/2 ≈ 48000/1850/2 ≈ 13 个/秒。对 90 秒 SSTV 音频约 1170 个过零点，Python 循环可接受。
- **`_detect_vis_header`**（sstv_decoder.py:266-286）：逐样本扫描频率数组找 break，O(n) 但 n=48000×90=4.3M，Python 循环约 0.5-1 秒。非实时路径，可接受。
- **`_find_sync_markers`**（sstv_decoder.py:316-333）：scipy.ndimage.label 路径是向量化的；fallback 的 while 循环（322-333）是 Python 逐样本扫描，在无 scipy 时慢。

---

## 八、总结评级

| 维度 | 评级 | 说明 |
|------|------|------|
| 依赖精简度 | **A** | 核心仅 6 包，无重量级 AI 框架，可选依赖降级完善 |
| 桌面端架构 | **B+** | 原生 PySide6，有 OpenGL 检测但实际未用 GPU 渲染 |
| Chromebook 可行性 | **C-** | DCBlocker + FT8 Goertzel 在 Celeron 上实时性极差 |
| 内存安全 | **C** | FileIQBackend 整文件加载 complex128，4GB RAM 机器易爆 |
| 实时管线完整性 | **D** | 桌面端尚无真实 IQ 数据流，频谱是模拟数据 |
| 瀑布图渲染 | **C** | setPixelColor 双层 Python 循环，OpenGL 名存实亡 |

### Top 3 优先修复

1. **[P0] FT8 Goertzel 改 FFT**（ft8_lite.py:26-38）：52 秒/15 秒帧 → 应 <1 秒
2. **[P0] DCBlocker 向量化**（dsp.py:53-56）：实时因子 1.26× → 应 <0.1×
3. **[P1] FileIQBackend 用 memmap/complex64**（sdr_backend.py:995-1008）：内存占用减半到 1/4

# 42 · desktop/spectrum_widget.py 深度审查（R2）

- 审查对象：`desktop/spectrum_widget.py`（658 行）
- 审查日期：2026-09-24
- 审查范围：实时频谱/瀑布图渲染、数据源、缩放/平移、性能、空槽、主题、与 main_window 集成、星座图
- 配套已知问题引用：`mbdsdr_ai/spectrum_processor.py:106-108`（FFT 不除 N、无窗增益补偿）、`spectrum_processor.py:244-261`（zoom/pan 只存状态）、`spectrum_processor.py:263`（compute_waterfall 零调用）

---

## 0. 一句话结论

**整个桌面端频谱控件 100% 是合成数据演示，从未接过后端真实 IQ。** `set_iq_data` 定义了但全工程零调用；`SpectrumProcessor` 只在后端 `agent.py`/`sdr_tools.py` 里跑，UI 拿不到它的输出。默认走 OpenGL 分支（`SpectrumGLWidget`），而该分支连 `set_iq_data` 方法都没定义，且 paintGL 里用 QPainter 包了一层，并没有真正用 GL shader，"OpenGL 加速"名不副实。

---

## 1. 实时频谱渲染：是否真用 Qt 绘制？数据从哪来？

### 1.1 绘制本身是真的
- `SpectrumWidget.paintEvent`（`spectrum_widget.py:209-249`）用 `QPainter` 真画了：背景、网格、频谱曲线 + 渐变填充、中心频率标记、瀑布图、频率/dB 刻度。
- `SpectrumGLWidget.paintGL`（`spectrum_widget.py:503-602`）逻辑同上，但**内部仍然 `QPainter(self)`**（line 508）——并没有写任何 GL shader/VBO 绘制频谱，VBO/Program 成员虽然在 `__init__` 声明（line 462-463）却从未使用。所谓"优先 OpenGL 渲染"是壳，本质还是软件光栅。[空壳]

### 1.2 数据源：全部合成
- 唯一数据源是内置 `SpectrumDataGenerator`（`spectrum_widget.py:30-87`），硬编码 5 个高斯电台峰 + 高斯白噪声（line 41-47, 64-71）。
- `_on_timer`（line 198-203）每 50 ms 调一次 `self.generator.generate()` 或使用 `self._real_spectrum`。
- **`set_iq_data`（line 185-196）在整个仓库零调用**（已全局 grep 确认：除定义外无任何引用）。也就是说 `_using_real` 永远为 False，UI 永远跑合成分支。
- `mbdsdr_ai/spectrum_processor.py` 的 `SpectrumProcessor` 实例化只发生在：
  - `mbdsdr_ai/sdr_tools.py:61`（挂在 agent 上）
  - `mbdsdr_ai/sdr_tools.py:3840`（独立工具函数内）
  - `tests/` 里
  **desktop 端从未 import SpectrumProcessor，也从未把它的输出喂给 SpectrumWidget。** [空壳]

### 1.3 工厂函数默认就选 GL 分支
- `create_spectrum_widget(prefer_opengl=True)`（line 640-658）在非 offscreen 平台直接返回 `SpectrumGLWidget`。
- `main_window.py:271` 正是用 `prefer_opengl=True` 创建的。
- 后果：**用户在正常桌面环境跑起来的就是 `SpectrumGLWidget`，而该类根本没有 `set_iq_data` 方法**（line 450-633 全类搜不到），即使未来有人想接真 IQ，对 GL 分支调 `set_iq_data` 会直接 `AttributeError`。[真bug]

---

## 2. dB 标定问题（对照 spectrum_processor:106-108）

### 2.1 后端已知问题复述
`spectrum_processor.py:106-108`：
```python
spectrum = np.fft.fftshift(np.fft.fft(windowed, fft_size))
powers = np.abs(spectrum) ** 2
powers_db = 10 * np.log10(powers + 1e-12)
```
- 没除 N / 采样率 → dBm 绝对值错，只和 FFT size 线性相关；
- 没做窗增益补偿（Hann 相干增益 ≈ 0.5，处理增益 ≈ -6 dB）；
- 没有阻抗/参考电平标定。

### 2.2 UI 层的标定问题
UI 自己的 `set_iq_data`（line 192-194）：
```python
win = _np.hanning(len(arr))
spec = _np.abs(_np.fft.rfft(arr * win))
self._real_spectrum = 20 * _np.log10(spec + 1e-9)
```
存在同样的问题，甚至更糟：
1. **没除 N**：幅值随 FFT 点数线性增长；
2. **没窗增益补偿**：Hanning 窗相干损失约 6 dB；
3. **用 `20*log10(|.|)` 而非 `10*log10(|.|²)`**——数学上等价于功率 dB，但同样没有任何参考电平（1 Ω？1 mW？），标不上 dBm；
4. **没 `fftshift`**：rfft 输出本来就是 0~Fs/2 单边，顺序没错，但 line 267-270 画曲线时直接把 `_current_spectrum` 从低到高 bin 映射到屏幕 x=0→w，**没有任何频率轴校准**（sample_rate 根本没传进来）；
5. **瀑布图不接真数据**：`set_iq_data` 只更新 `_current_spectrum`，从不 `self.generator.waterfall.append(...)`。一旦真 IQ 生效，上半屏是新的、下半屏瀑布会永远停在最后一帧合成数据。[真bug]

但因为 `set_iq_data` 零调用，以上全部是**潜伏 bug**——现在用户看到的 -100~-20 dB 刻度是合成 generator 硬编码的（line 40, 149-150），和真实信号毫无关系。[占位]

---

## 3. 缩放/平移是否真生效？（对照 spectrum_processor zoom/pan）

后端 `SpectrumProcessor.zoom/pan`（`spectrum_processor.py:244-261`）确实只存状态，`compute_spectrum` 始终返回全带——这是后端问题。

UI 层自己实现了一套独立的"缩放/平移"，**没有走后端，而是直接改 generator 的 center_freq/span 再重新合成**。这意味着：
- 对合成数据：缩放/平移**视觉上生效**（因为 generator 会按新 span 重新生成高斯峰位置）；
- 对真 IQ：UI 根本没接真 IQ，谈不上缩放真数据；
- 而且 UI 的缩放实现本身有 bug：

### 3.1 [真bug] 滚轮缩放方向错误（Raster 版）
`spectrum_widget.py:403-415`：
```python
if delta > 0:
    self._zoom_factor *= 1.1
else:
    self._zoom_factor /= 1.1          # → 0.909
self._zoom_factor = max(0.1, min(10.0, self._zoom_factor))
new_span = self.generator.span / self._zoom_factor if delta > 0 else self.generator.span * self._zoom_factor
self.generator.set_span(max(0.1, new_span))
self._zoom_factor = 1.0              # 立刻重置
```
- 滚轮上滚（delta>0，期望 zoom in→span 变窄）：`span / 1.1` ✓
- 滚轮下滚（delta<0，期望 zoom out→span 变宽）：`span * 0.909` ✗ **反而更窄**
- 即两个方向都在 zoom in，永远越滚越窄。`_zoom_factor` 状态算完就 reset，纯属多余。

### 3.2 [真bug] GL 版滚轮方向与 Raster 版相反
`spectrum_widget.py:604-608`：
```python
factor = 1.1 if delta > 0 else 0.9
self.generator.set_span(max(0.1, self.generator.span * factor))
```
- delta>0：span × 1.1 → 变宽 = zoom out（与 Raster 版相反）
- delta<0：span × 0.9 → 变窄 = zoom in（与 Raster 版相反）

两份实现方向不一致，且 GL 版方向反了。[真bug]

### 3.3 平移是真的
- `mouseMoveEvent`（line 422-430 / 615-622）真的改 `generator.center_freq` 并 emit `freq_changed`，main_window `_on_spectrum_freq_changed`（`main_window.py:582-585`）会回控 `tune_fm`。平移链路通，但调的是合成 generator，不是真后端频率。

---

## 4. 瀑布图是否真有数据？

### 4.1 合成模式下有数据
- `SpectrumDataGenerator.generate()`（line 79-81）每帧 `self.waterfall.append(spectrum.copy())`，超 200 行 pop 头部。
- `_draw_waterfall`（line 330-366）真的把它画成 QImage。
- 所以演示模式下瀑布图是"活"的，但内容是 5 个固定高斯峰随时间上下抖动的噪声——没有任何真实时间-频率演化意义。

### 4.2 compute_waterfall 零调用（后端）
已全局确认：`SpectrumProcessor.compute_waterfall`（`spectrum_processor.py:263`）在整个仓库无任何调用方。后端算的瀑布数据从来没到 UI。

### 4.3 [真bug] 真 IQ 路径下瀑布不更新
如 2.2 第 5 点：`set_iq_data` 不写 `generator.waterfall`。未来接真 IQ 时瀑布会冻结。

---

## 5. 性能

### 5.1 刷新频率
- Raster 版 `QTimer.start(50)`（line 159）= 20 FPS。
- GL 版 `QTimer.start(50)`（line 477）= 20 FPS。
- 20 FPS 对频谱仪是合理下限（专业仪表常 15-30 FPS），本身不激进。[建议] 可考虑降到 10-15 FPS 以降低 CPU，或用 adaptive throttle。

### 5.2 [真bug] 瀑布图像素级 Python 循环，必然掉帧
`_draw_waterfall`（line 340-359）：
```python
image = QImage(w, n_lines, QImage.Format_RGB32)
for row in range(n_lines):
    ...
    for col in range(w):
        ...
        image.setPixelColor(col, row, color)
```
- `QImage.setPixelColor` 是带格式转换 + 颜色校验的慢调用；800×150 = 12 万次/帧，20 FPS = 240 万次/秒，全在 GUI 线程。
- 每次 paintEvent 都新建 QImage、逐像素填色，没有任何 numpy 向量化（应直接构造 `np.uint8` 数组再 `QImage(arr.data, ...)` 零拷贝包装）。
- GL 版 `paintGL`（line 575-585）同样的双层 Python 循环。
- 后果：窗口拉大到 1080p 时瀑布区约 1920×400 = 76.8 万像素/帧，GUI 线程会明显卡顿，鼠标平移/滚轮都跟手延迟。[真bug]

### 5.3 [真bug] 频谱曲线 Python 循环
`_draw_spectrum`（line 283-295）用 `for i in range(n)` 逐点 `lineTo`。n=512 还好，但应该用 `QPolygonF` + `drawPolyline` 或把 spectrum 转 `QPolygonF` 一次性画。[建议]

### 5.4 大 FFT 是否阻塞 UI 线程？
- 当前 `set_iq_data` 零调用，UI 线程不做 FFT。
- 一旦未来启用，line 192-193 在 GUI 线程里做 `np.fft.rfft`，点数 8k-64k 时会阻塞几十~上百 ms。应该放到 worker 线程通过信号发回。[建议/潜伏]

### 5.5 不必要的重绘
- 鼠标平移时每次 `mouseMoveEvent` 都 `self.update()`（line 430）——合理。
- 定时器每 50 ms 无条件 `update()`（line 203）——即使数据没变也重画整屏（含瀑布像素循环）。可考虑 dirty flag。[建议]

---

## 6. 空槽/死按钮

### 6.1 spectrum_widget 自身
- 类内方法清单：`set_center_freq / set_span / toggle_waterfall / set_iq_data / paintEvent / _draw_* / wheelEvent / mouse*`。
- **没有"保持/Peak Hold"、"峰值检测"按钮或菜单项**——main_window 工具栏也没加（`main_window.py:197-201` 只有瀑布图按钮）。所以不存在"connect 了但函数体 pass"的死按钮；而是这些功能**压根没做**。[占位]
- `set_iq_data` 本身是"有实现但无人调用"的死方法。[空壳]

### 6.2 main_window 侧
- `waterfall_action`（`main_window.py:124-128`）和 `waterfall_btn`（line 197-202）都 connect 到 `_toggle_waterfall`（line 602-603），后者真的调 `self.spectrum.toggle_waterfall()`。链路通。
- `freq_changed` 信号连到 `_on_spectrum_freq_changed`（line 272, 582），会回控 worker。链路通。
- 没有发现 connect 到 `pass` 函数体的情况。

---

## 7. 无限递归？

- `paintEvent` / `paintGL` 内部**没有调用 `self.update()`**。
- `update()` 只在：timer timeout（line 158, 476）、`set_theme_colors`（line 170）、`set_center_freq/span`（line 174, 178）、`toggle_waterfall`（line 182）、wheelEvent（line 415）、mouseMove（line 430, 622）、mouseDoubleClick（line 442, 633）里调用。
- **无 paintEvent→update→paintEvent 循环**。✅

---

## 8. 主题硬编码

### 8.1 Raster 版（SpectrumWidget）——基本合规
- `__init__` 设了默认色（line 134-142），但 `set_theme_colors`（line 161-170）会被 `main_window._apply_theme`（`main_window.py:359-366`）调用并覆盖。✅
- 字体：`_draw_freq_scale` / `_draw_db_scale` 里 `QFont(); setPointSize(8)`（line 371-373, 389-391），**没走 themes.py 的 MiSans/JetBrains Mono 体系**。[建议]

### 8.2 [真bug] GL 版（SpectrumGLWidget）——大量硬编码，主题切换无效
- `set_theme_colors`（line 479-481）**只更新 `spectrum_colors`**，bg/grid/text/line/marker 全部忽略。
- `paintGL` 里硬编码：
  - 背景 `QColor("#1E1E20")`（line 525）
  - 网格 `QColor("#3A3A3E")`（line 528）
  - 频谱曲线 `QColor("#7A9CAC")`（line 563）
  - 中心标记 `QColor("#D4956A")`（line 568）
  - 文字 `QColor("#D0D0D0")`（line 588）
- 即：用户切到"日式浅色"或"高对比"主题时，频谱图的背景/网格/曲线色**纹丝不动**，只有瀑布渐变跟着变。这是主题系统的漏网之鱼。[真bug]
- `initializeGL` 里 `glClearColor(0.12, 0.12, 0.13, 1.0)`（line 498）也硬编码深色。

---

## 9. 与 main_window 的集成

| 调用点 | 行号 | 作用 | 状态 |
|---|---|---|---|
| `create_spectrum_widget(prefer_opengl=True)` | main_window.py:271 | 实例化 | 默认走 GL 分支（见 1.3） |
| `freq_changed.connect(_on_spectrum_freq_changed)` | main_window.py:272 | 拖屏→回控 tune_fm | ✅ 链路通 |
| `set_theme_colors(...)` | main_window.py:359 | 主题切换 | Raster 生效，GL 不生效 |
| `set_center_freq(freq)` | main_window.py:506, 518, 576 | 外部改频率 | 只改合成 generator |
| `toggle_waterfall()` | main_window.py:603 | 菜单/按钮 | ✅ |
| worker 信号 → spectrum | — | — | **无任何信号喂 IQ/频谱数据** |

关键证据：`MCPWorker` 的信号清单（`mcp_worker.py:115-121`）只有 `status_updated / gps_updated / imu_updated / connection_changed / tool_result / error_occurred / log_message`，**没有 spectrum/iq/waterfall 信号**。`_connect_worker_signals`（main_window.py:428-442）也没把任何 worker 输出接到 spectrum widget。[空壳]

---

## 10. 星座图显示

- **desktop 目录下不存在任何星座图控件**。grep `constellation` 在 desktop/ 下零命中。
- `mbdsdr_ai/constellation.py` 存在（后端算法），但没有任何 UI 包装，main_window 的 tab 列表（`main_window.py:275,280,299,303,309`）只有：频谱 / 射频天空 / 控制 / 状态 / AI 助手。
- 即：**星座图功能在桌面端完全缺失，谈不上"接后端"**。[占位]

---

## 11. 其他发现

### 11.1 [建议] `set_iq_data` 里重复 import numpy
line 188 `import numpy as _np`，而文件顶部 line 11 已 `import numpy as np`。冗余。

### 11.2 [建议] `value_to_color` 每像素调用
line 354-358 在瀑布双层循环里对每个像素调 `value_to_color`（含 `QColor` 构造 + 浮点插值），是 5.2 性能问题的一部分。应预生成 256 级 LUT 查表。

### 11.3 [建议] 瀑布图 QImage 高度不匹配
line 337 `n_lines = min(len(self.generator.waterfall), h)`，但 line 340 `QImage(w, n_lines, ...)` 高度是 n_lines，line 362 `painter.drawImage(rect, image)` 又把它拉伸到整个 waterfall_rect（高度 = h - spectrum_h）。行数少于像素高度时图像被纵向拉伸模糊。

### 11.4 [建议] 频率刻度不随 zoom/pan 联动单位
`_draw_freq_scale`（line 368-384）永远 `f"{freq:.1f}"`，span 缩到 0.1 MHz 时一位小数不够，span 拉到 20 MHz 时一位小数又太粗。

### 11.5 [占位] 文档字符串与实现不符
文件头 line 4-7 宣称"优先 OpenGL 渲染""预留真实 IQ 数据接口"——OpenGL 是 QPainter 伪装，真实 IQ 接口从未接线。

---

## 12. 问题汇总表

| # | 位置 | 类型 | 描述 |
|---|---|---|---|
| 1 | spectrum_widget.py:185-196 | 空壳 | `set_iq_data` 全工程零调用，真 IQ 路径未接线 |
| 2 | spectrum_widget.py:450-633 | 空壳 | `SpectrumGLWidget` 无 `set_iq_data`，默认分支永远合成数据 |
| 3 | spectrum_widget.py:508 | 空壳 | "OpenGL" 内部仍是 `QPainter(self)`，未用 shader/VBO |
| 4 | main_window.py:428-442 | 空壳 | MCPWorker 无 spectrum/IQ 信号，UI 拿不到后端数据 |
| 5 | spectrum_widget.py:403-415 | 真bug | Raster 滚轮下滚反而 zoom in（两个方向都变窄） |
| 6 | spectrum_widget.py:604-608 | 真bug | GL 滚轮方向与 Raster 相反，且方向反了 |
| 7 | spectrum_widget.py:192-194 | 真bug（潜伏） | `set_iq_data` FFT 不除 N、无窗增益补偿、无参考电平、无 fftshift 后的频率轴校准 |
| 8 | spectrum_widget.py:195 vs 79 | 真bug（潜伏） | 真 IQ 不写 waterfall，瀑布会冻结 |
| 9 | spectrum_widget.py:340-359, 575-585 | 真bug | 瀑布图逐像素 `setPixelColor` Python 双层循环，大窗口必卡 GUI |
| 10 | spectrum_widget.py:479-481, 525,528,563,568,588 | 真bug | GL 版主题色硬编码，`set_theme_colors` 只改瀑布渐变 |
| 11 | spectrum_widget.py:498 | 真bug | `glClearColor` 硬编码深色，主题切换无效 |
| 12 | spectrum_widget.py:371-373,389-391 | 建议 | 频率/dB 刻度字体未走 themes.py 字体体系 |
| 13 | spectrum_widget.py:159,477 | 建议 | 20 FPS 无 dirty flag，每帧无条件重画整屏 |
| 14 | spectrum_widget.py:283-295 | 建议 | 频谱曲线逐点 lineTo，可改 QPolygonF 一次性画 |
| 15 | spectrum_widget.py:354 | 建议 | `value_to_color` 每像素调用，应预生成 LUT |
| 16 | spectrum_widget.py:337-362 | 建议 | 瀑布 QImage 高度与 rect 不匹配导致纵向拉伸模糊 |
| 17 | spectrum_widget.py:381 | 建议 | 频率刻度永远 `.1f` 小数位，不随 span 自适应 |
| 18 | desktop/ 全目录 | 占位 | 无星座图控件；`mbdsdr_ai/constellation.py` 无 UI 包装 |
| 19 | spectrum_widget.py 全类 | 占位 | 无 Peak Hold / 峰值检测 / 保持按钮（main_window 也没加） |
| 20 | spectrum_widget.py:188 | 建议 | 函数内重复 `import numpy as _np`，顶部已有 np |

---

## 13. 优先级建议（供后续修复参考）

1. **P0**：把 worker 的真实频谱/IQ 信号接进 UI（或明确宣告"桌面端 v0.1 仅演示"），否则整个频谱控件是演示品。
2. **P0**：修滚轮缩放方向（两处）。
3. **P1**：GL 版补 `set_iq_data`、补主题色字段、或干脆删除 GL 分支用纯 Raster（反正没真用 GL）。
4. **P1**：瀑布图改 numpy 向量化 + QImage 零拷贝，消除逐像素 Python 循环。
5. **P2**：`set_iq_data` 补 N 归一化、窗增益补偿、sample_rate 传入、waterfall 入栈。
6. **P2**：决定星座图是否纳入 v0.2；不纳入就从路线图里删掉避免误导。

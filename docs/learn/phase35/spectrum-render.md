# Phase35 L4：SDR++ 频谱/瀑布渲染深读（spectrum / waterfall / fft window / colormap / history）

> 上游：`repos/sdrpp/core/src/gui/`（GPLv3）。本文只学机制，按干净室原则描述思路，不复制代码；所有结论给 `file:line`。
> 对照我方：`cpp/src/ui/spectrum_display.{cpp,h}`、`cpp/src/ui/spectrum_render.h`、`cpp/src/dsp/power_spectrum.{cpp,h}`、`cpp/src/dsp/spectrum_engine.{cpp,h}`（MIT）。
> 前置：`docs/learn/phase12/sdrpp-waterfall.md` 已画过管线总图；本篇补 Phase35 要求的"窗口类型 / 色板切换 / 历史保持与回放"细节，并做差距判定。

---

## 1. 渲染管线全貌（从 IQ 到像素，零拷贝交接）

```
IQFrontEnd DSP 线程
  Reshaper(keep=_nzFFTSize, skip=skip)        # 解耦采样率与 FFT 帧率
    └ handler()                               # iq_frontend.cpp:~243
        volk_32fc_32f_multiply_32fc           # 加窗（窗 *= (-1)^n，内含 fftshift）
        fftwf_execute                         # 正向 FFT
        acquireFFTBuffer() → getFFTBuffer()   # 返回 rawFFTs 环里一行 float*，原地写
        volk_32fc_s32f_power_spectrum_32f     # 复 FFT → dBFS
        releaseFFTBuffer() → pushFFT()       # doZoom→最新行、memmove 帧缓冲上滚、查 LUT
UI 线程 draw()
  drawFFT()    # 折线 + 网格 + 阴影 + hold 折线（waterfall.cpp:120-206）
  drawWaterfall() # AddImage(textureId) 贴一张 GL 纹理（:208-227）
```

接线证据 `core/src/gui/main_window.cpp:230-236`：
`acquireFFTBuffer` 直接返回 `gui::waterfall.getFFTBuffer()` 的环行指针；DSP 线程在这行上写 dB，写完回调 `pushFFT()` 完成滚行。FFT 计划在 UI 线程 `main_window.cpp:87-89` 创建，在 DSP 线程执行——计划本身线程安全（FFTW 约定），但缓冲指针跨线程交接靠 `buf_mtx`/`latestFFTMtx` 两把递归锁。

**关键机制：fftshift 藏在窗函数里。** `iq_frontend.cpp` 的 `updateFFTPath()` 生成窗时把每个采样乘 `(i%2 ? -1 : 1)`（即 `(-1)^n` 调制），等价于频域搬移 N/2，于是 FFT 输出 bin 0 直接落在中心频率，省掉事后 `std::rotate`。我方 `power_spectrum.cpp:47-48,117-118` 用 `std::rotate` 做同样的事——语义等价，我方更直白，不算差距。

---

## 2. FFT 窗口类型（上游实际只暴露 3 种）

`iq_frontend.h:17-21`：
```cpp
enum FFTWindow { RECTANGULAR, BLACKMAN, NUTTALL };
```
DSP 窗族 `core/src/dsp/window/` 下其实有 `hann.h / hamming.h / blackman.h / blackman_harris.h / blackman_nuttall.h / nuttall.h / cosine.h / rectangular.h`，全部是余弦和窗的内联生成器（`hann.h` = `{0.5, 0.5}`，`blackman.h` = `{0.42, 0.5, 0.08}`）。但 IQFrontEnd 只接 RECT/BLACKMAN/NUTTALL 三种；Hann/Hamming/Flattop 在 GUI 层根本没暴露。`main_window.cpp:91` 默认 `NUTTALL`（低旁瓣，适合扫频看信号）。

**对照我方**：`power_spectrum.cpp:79-104` 的 `rebuildWindow()` 实现 Hann / Flattop / Blackman 三种，且 `spectrum_engine.h:293` 注释明确 `0=Hann 1=Flattop 2=Blackman`。我方窗口集比上游 GUI 暴露的还全（多了 Flattop 平坦幅频），**这一项我方不缺，反而略优**。

---

## 3. 色板（colormap）机制

### 3.1 LUT 大小与构建
`waterfall.h:11` 定义 `WATERFALL_RESOLUTION 1000000`；`waterfall.cpp:944-958` `updatePallette()` 把 N 个控制点在 1,000,000 个 uint32 BGRA 项里做线性插值。默认 13 个控制点 `waterfall.cpp:11-25`（深蓝→蓝→白→黄→橙→红→暗红），启动时 `main_window.cpp:164` 改为外部 `Turbo` 色板。

### 3.2 外部色板文件
`colormaps.cpp:12-47`：从 `resources/colormaps/*.json` 读 `{"name":..,"author":..,"map":["#000000",..]}`，把 `#rrggbb` 六位 hex 转成 0-255 float 三元组，存进全局 `std::map<string,Map>`。切色板即换一张 1M LUT。

### 3.3 像素上色
`pushFFT()` `waterfall.cpp:901-905`：新一行 dB → clamp 到 `[waterfallMin,waterfallMax]` → 归一化到 `[0,1]` → 乘 `WATERFALL_RESOLUTION-1` → 查 `waterfallPallet[]` 直接得 BGRA uint32。

### 3.4 全量重染
`updateWaterfallFb()` `waterfall.cpp:600-631`：缩放/平移/改 dB 上下限时，把整个 `rawFFTs` 环逐行 doZoom + 查 LUT 重画到 `waterfallFb`，下一帧 `updateWaterfallTexture()` `:704-711` 整图 `glTexImage2D` 上传。

**对照我方**：
- `spectrum_render.h:121-140` `buildLut256`：256 项 LUT（不是 1M）。显示深度本来就是 8-bit/通道，256 项足够；上游 1M 项（4 MB BSS）是过度设计。**判定：YAGNI，不学。**
- `spectrum_render.h:174-252` `parseColormapJson`：同时支持 `{stops:["#rrggbb",..]}` 与 `[{t,c}]` 两种 root，比上游 `colormaps.cpp` 只认均匀分布更宽容；解析失败返回错误串而不是静默 fallback（`:180-235`）。**我方更稳。**
- `spectrum_display.cpp:242-265` `rebuildColormap()` + `:220-240` `materialiseHistory()`：切色板或改 dB 范围后，整圈 `ringDb_` 重上色——对应上游 `updateWaterfallFb`，但我方存的是 raw dB 行（`spectrum_display.h:361` `ringDb_`），不是已上色像素。**机制一致，我方按 dB 存更省内存（行宽=bins 而非 dataWidth×4B×rows）。**

---

## 4. 历史（ring buffer）与滚行

上游 `rawFFTs` 是 `float*`，尺寸 `waterfallHeight × rawFFTSize`（`waterfall.h:286,293`），每行存一整圈原始 FFT bin 的 dB。`getFFTBuffer()` `:876-887` 在 DSP 线程被调用时把 `currentFFTLine--`（环形头回退一格），返回新槽位指针让 DSP 原地写。`pushFFT()` `:898` 用 `memmove(&waterfallFb[dataWidth], waterfallFb, dataWidth*(waterfallHeight-1)*4)` 把 RGBA 帧缓冲整体上滚一行，再在顶行写新像素。

`onResize()` `:742-749` 改高度时用 `malloc + memcpy + memmove` 手工旋转环形行——脆弱但能用。

**对照我方**：`spectrum_display.cpp:208-218` `pushHistoryRow()` 把 `frame_.dbfs` 拷进 `ringDb_[ringHead_]`，头前进一格；`:220-240` `materialiseHistory()` 每次从环重渲一张 `QImage` 快照（logical row 0 = 最新）。历史深度 `tokens.h:443` `kWaterfallHistoryLines=256`。

**可借鉴点**：
- 上游零拷贝（DSP 直接写环行）vs 我方一次拷贝。我方 FFT 帧率低（Qt 主线程帧驱动），拷贝开销可忽略；零拷贝会把 DSP 线程和 UI 线程的锁耦合得更紧。**判定：YAGNI。**
- 上游 `updateWaterfallFb` 里 `:608` 每调用都 `new float[dataWidth]`——堆分配抖动。我方 `spectrum_display.cpp:779-780` 复用 `decScratch_` scratch 行，已经避开。**反例，我方做法正确。**

---

## 5. doZoom：块最大降采样（核心算法）

`waterfall.cpp:65-90`：
```cpp
float factor = (float)width / (float)outSize;   // 输入 bin 数 / 输出像素数
for (int i = 0; i < outSize; i++) {
    maxVal = -INFINITY;
    for (int j = 0; j < uFactor; j++)
        if (in[sId+j] > maxVal) maxVal = in[sId+j];
    out[i] = maxVal;
    id += factor;
}
```
**取块最大，不取平均**——窄带 CW 峰在缩放下不被邻居平均掉。我方 `spectrum_render.h:82-107` `decimateBlockMaxRange` 已干净室重写，且：
- 用 `double scale` 而非上游 `float factor` 累加，避免长图漂移；
- 非有限值 `std::isfinite` 过滤（`:103`）；
- 每块至少 1 个源 bin（`:96` `if(b<=a) b=a+1`）。

我方瀑布绘制 `spectrum_display.cpp:766-797` 还区分了两种路径：放大/1:1 时直接 `drawImage`（Qt bilinear），缩小时逐行 `decimateBlockMaxRange` 到 `fallsPeak_` 缓存再贴——上游只在 pushFFT 时 doZoom 一次，我方在缩放时才对全历史 doZoom，语义等价。**这一项我方已对齐且实现更细。**

---

## 6. 频谱折线的平滑 / Hold / SNR

### 6.1 EMA 平滑
`waterfall.cpp:914-920`：开关打开时
`latestFFT = alpha*latestFFT + beta*smoothingBuf`（volk 向量化），`alpha=speed, beta=1-speed`（`:1190-1194`）。

### 6.2 Peak-Hold 衰减
`waterfall.cpp:935-939`：
```cpp
latestFFTHold[i] = std::max(latestFFT[i], latestFFTHold[i] - fftHoldSpeed);
```
即包络上沿保持、每帧向下衰减 `fftHoldSpeed` dB——比"永久 max"更耐看。

### 6.3 VFO SNR
`waterfall.cpp:558-598`：选中 VFO 带内取 max 作为 strength，两侧 guard band 取平均作为噪声底，SNR = max - avg。tooltip 里 Ctrl 才显示（`:486-503`）。

### 6.4 autoRange
`waterfall.cpp:976-990`：扫当前 `latestFFT` 的 min/max，`fftMin=min-5, fftMax=max+5`。

**对照我方**：
- EMA 平滑：我方没有专门的 trace EMA，但有 `setPersistenceMode`（`spectrum_display.h:95-97`）余晖包络，`tokens.h:121-123` 衰减系数 0.78/0.93——语义是"拖尾残影"而非"瞬时 EMA 平均"。两种用途重叠，**判定：YAGNI，不加 EMA 开关。**
- Peak-Hold 衰减：我方 `maxHold_`（`spectrum_display.h:343`）是 flat max envelope，**没有逐帧衰减**。这是一个真实小差距：上游 `max(new, old-speed)` 的缓慢衰减在长信号观察下更友好。**可借鉴（小改）**：在 `setSpectrum` 里对 `maxHold_` 每个 bin 做 `std::max(frame[i], maxHold_[i]-kHoldDecay)`，约 5 行。
- VFO SNR：我方走"注入真实测量噪声底"路线（`setNoiseFloorDb` `spectrum_display.h:148`），比上游的"guard band 平均"更诚实（真 RSSI 不是邻道估计）。**判定：不抄上游。**
- autoRange：我方 `spectrum_display.cpp:295-304` 用滑动 24 帧峰值窗 +  eased ceiling（`kAutoWindowFrames=24`），比上游"整幅扫一次 min/max"更稳。**我方更好。**

---

## 7. 反例核查（上游的坑，我方别踩）

| 上游做法 | 位置 | 问题 | 我方现状 |
|---|---|---|---|
| `waterfallPallet[1000000]` 静态 BSS | waterfall.h:236 | 4 MB 常驻，8-bit 显示用不上 | `lut_` 256 项，`spectrum_display.h:368` ✓ |
| doZoom 用 `float factor` 累加 id | waterfall.cpp:74,88 | 长图终点漂移几个 bin | `decimateBlockMaxRange` 用 double ✓ |
| `updateWaterfallFb` 每帧 `new float[dataWidth]` | waterfall.cpp:608 | 堆分配抖动 | 复用 `decScratch_` ✓ |
| onResize 用 malloc+memcpy 手工旋环 | waterfall.cpp:742-749 | 脆弱、易越界 | 重分配整圈 ringDb_，无旋转 ✓ |
| 4 把递归 mutex 保护缓冲 | waterfall.h:249-252 | 锁粒度细但易死锁 | Qt 单线程 UI 摄入，无此问题 ✓ |
| 窗口集 GUI 只暴露 3 种 | iq_frontend.h:17-21 | Flattop/Hann 不可选 | 我方 Hann/Flattop/Blackman 全暴露 ✓ |
| SNR 用 guard band 估计噪声 | waterfall.cpp:576-587 | 邻道信号会污染底噪 | 我方注入实测噪声底 ✓ |
| `_fullUpdate` 开关跳过瀑布重染 | waterfall.h:302, :1087 | 平移后短暂条纹残留 | 我方 `materialiseHistory` 每帧全量重渲，无残留 ✓ |

---

## 8. 差距判定汇总（补齐 vs YAGNI）

| 能力 | 上游 | 我方 | 判定 |
|---|---|---|---|
| raw-dB 环存历史 | ✓ rawFFTs | ✓ ringDb_ | 已对齐 |
| 块最大降采样 doZoom | ✓ | ✓ decimateBlockMaxRange | 已对齐（且更稳） |
| 外部 JSON 色板 | ✓ colormaps.cpp | ✓ parseColormapJson | 已对齐（错误报告更诚实） |
| 256/1M LUT | 1M | 256 | YAGNI（显示深度够） |
| FFT 窗口集 | 3 种 GUI | 3 种（含 Flattop） | 我方略优 |
| fftshift | (-1)^n 藏窗里 | std::rotate | 等价，不抄 |
| 零拷贝环行写 | ✓ acquire/release 回调 | 一次拷贝 | YAGNI（帧率低） |
| EMA trace 平滑 | ✓ | 余晖包络近似 | YAGNI（语义重叠） |
| Peak-Hold 衰减 | ✓ max(old-speed) | flat max | **小补**：加 `kHoldDecay` 衰减 |
| VFO SNR | guard band 平均 | 实测噪声底注入 | 不抄（我方更诚实） |
| autoRange | 全幅 min/max±5 | 滑动窗+eased ceiling | 我方更好 |
| GL 纹理整图上传 | glTexImage2D | QImage+Qt 绘制 | 等价，不抄 |

**唯一建议落地的小改动**：给 `maxHold_` 加逐帧衰减（对应上游 `waterfall.cpp:935-939`），约 5 行，放 `setSpectrum` 末尾。其余机制我方要么已对齐、要么实现更稳、要么属于上游过度设计。

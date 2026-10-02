# SDR++ 频谱/瀑布渲染子系统研习（waterfall / colormaps / GL 绘制 / style）

> 上游：`repos/sdrpp`（master `8c9f5ee`），**GPLv3**（`repos/sdrpp/license` 即 GNU GPL v3 全文）。
> 本文只学机制/算法/管线划分，按干净室原则描述思路，**不复制源码**；所有引用给 `file:line` 与极短示意（注释性引用），落地时以自有实现重写。
> 对照基线：MBDSDR `cpp/src/ui/spectrum_display.{cpp,h}`、`spectrum_widget.{cpp,h}`、`dsp/power_spectrum.{cpp,h}`、`dsp/fft.{cpp,h}`（MIT）。
> 范围：`core/src/gui/widgets/waterfall.{cpp,h}`、`core/src/gui/colormaps.{cpp,h}`、`core/src/gui/gui.cpp`、`core/src/gui/style.{cpp,h}`，以及 FFT 引擎交接点 `core/src/gui/main_window.cpp`。

---

## 0. 上游渲染管线总览（先看数据怎么流，再看每块）

SDR++ 把"频谱折线 + 瀑布"做成一个 ImGui 自绘控件 `ImGui::WaterFall`（`widgets/waterfall.h:83`）。
关键设计：**瀑布历史按"原始 dB 行"保存，而不是按"已上色像素"保存**；显示时再降采样 + 查 LUT 上色。

数据流向（GPLv3，机制引用）：

```
FFT 引擎(iq_frontend) ──acquireFFTBuffer()──► waterfall.getFFTBuffer()   返回 rawFFTs 环里一行 float*，引擎原地写 dB
       │
       └──releaseFFTBuffer()──► waterfall.pushFFT()                       ① doZoom 到显示宽 ② memmove 帧缓冲上滚一行
                                                                         ③ 新顶行查 waterfallPallet[] 上色 ④ 置 waterfallUpdate
UI 线程每帧 draw() ──► drawWaterfall(): 若 waterfallUpdate 则 updateWaterfallTexture()（glTexImage2D 整图上传）
                      └─► DrawList->AddImage(textureId, wfMin, wfMax)  贴一张 GL 纹理
```

接线证据 `core/src/gui/main_window.cpp:230-236`：
```cpp
float* MainWindow::acquireFFTBuffer(void* ctx){ return gui::waterfall.getFFTBuffer(); }
void   MainWindow::releaseFFTBuffer(void* ctx){ gui::waterfall.pushFFT(); }
```
即 **FFT 引擎持有一行 raw float 缓冲的指针直接写**（零拷贝交接），写完回调 `pushFFT()` 完成"滚行 + 上色 + 标记需重传纹理"。

---

## 1. 瀑布环形 raw FFT 缓冲（历史按 dB 存，不按像素存）

### 1.1 上游真实做法（GPLv3）
`widgets/waterfall.h:236,286-293`——历史是一整圈 **float 原始 dB 行** + 一张 **uint32 RGBA8 帧缓冲**：
```cpp
uint32_t waterfallPallet[WATERFALL_RESOLUTION];        // :236 调色板 LUT（1,000,000 项，见 §5）
float*   rawFFTs   = NULL;                              // :286 环形：waterfallHeight 行 × rawFFTSize 列，存 dB
float*   latestFFT  = NULL;                              // :287 已降采样到 dataWidth 的当前显示行
uint32_t* waterfallFb;                                   // :293 瀑布帧缓冲（dataWidth × waterfallHeight，RGBA8）
```
`getFFTBuffer()`（`waterfall.cpp:876-887`）每次把环形写指针回退一格、返回新槽位：
```cpp
currentFFTLine--; fftLines++;
currentFFTLine = ((currentFFTLine + waterfallHeight) % waterfallHeight);   // 环形前进
return &rawFFTs[currentFFTLine * rawFFTSize];                             // 引擎原地写这一行
```
`setRawFFTSize()`（`waterfall.cpp:1134-1147`）在 FFT 点数变化时按 `rawFFTSize × waterfallHeight` 重分配 raw 环并清零。
`onResize()`（`waterfall.cpp:738-757`）在控件高度变化时** memmove 平移环内已有行再 realloc**，尽量保留历史。

要点：**raw 环是"真相之源"**——改调色板、改 dB 量程、缩放窗口都不用重算 FFT，只需从这些 dB 行重新上色（§4、§6）。

### 1.2 MBDSDR 现状
`ui/spectrum_display.h:316-326` + `ui/spectrum_display.cpp:168-192`——环形按**已上色的 QRgb 像素行**保存：
```cpp
std::vector<QImage> ringRows_;   // :318 每行是 bins×1 的 ARGB32 像素条（已上色）
int ringDepth_; int ringHead_; int ringCount_;
std::array<QRgb,256> lut_{};     // :323 仅 256 项 LUT
```
`pushHistoryRow()`（`spectrum_display.cpp:181-189`）在入环时就把 `frame_.dbfs[i]` 当场查色：
```cpp
QImage& row = ringRows_[ringHead_];
auto* line = reinterpret_cast<QRgb*>(row.bits());
line[i] = colourForDb(db);                 // 入环即上色，raw dB 不保留
ringHead_ = (ringHead_+1) % ringDepth_;
```
- 历史深度固定 `kWaterfallHistoryLines = 256`（`core/tokens.h:408`）。
- **MBDSDR 环里存的是颜色，不是 dB**；这直接导致 §G2 的"改量程/换色板不重染旧行"。

### 1.3 差距判定
- **已实现但缺深度**：两家都有"单像素高行环形缓冲"的滚动瀑布骨架；上游额外保留 raw dB 行，使重着色/量程重映射廉价。

---

## 2. doZoom：峰保持降采样（zoom-out 时不糊峰）

### 2.1 上游真实做法（GPLv3）
`widgets/waterfall.cpp:65-90`——把 raw FFT（`rawFFTSize` 点）降到显示宽（`dataWidth` 点）时做**块内取最大**（max-pool），而非平均：
```cpp
float factor = (float)width / (float)outSize;
for (int i = 0; i < outSize; i++) {
    maxVal = -INFINITY;
    sId = (int)id;
    uFactor = ...;                       // 该输出像素覆盖的输入 bin 数（含末尾裁剪）
    for (int j = 0; j < uFactor; j++)
        if (in[sId + j] > maxVal) maxVal = in[sId + j];
    out[i] = maxVal;                     // 取块内峰值 → 缩小视图时信号峰不被抹平
    id += factor;
}
```
调用点：新行入瀑布 `waterfall.cpp:897`、非瀑布模式 `:909`、整段重渲染 `:616`。
zoom-out（显示宽 < raw FFT 宽）时，一个输出像素聚合若干 bin 的**峰值**，窄峰在缩窄视图里仍然亮着。

### 2.2 MBDSDR 现状
- 历史图始终是**全 bin 分辨率**（`bins_ = frame.dbfs.size()`，`spectrum_display.cpp:254`），入环不降采样。
- 绘制时 `paintEvent()`（`spectrum_display.cpp:660-667`）裁一个浮点源矩形整图交给 Qt：
```cpp
const double srcL = clampd((fLo - bandLo)/frameFsHz*bins, 0.0, bins_);
const double srcR = clampd((fHi - bandLo)/frameFsHz*bins, 0.0, bins_);
p.drawImage(falls, history_, QRectF(srcL, 0, srcR-srcL, ringDepth_));   // Qt 双线性缩放
```
- 即 zoom-out（可视窗 > 全带、需把窄源图放大）走 Qt 双线性**放大**；而当可视窗远窄于全带时是放大。真正"显示宽 < 全带、需把很多 bin 压到一个像素"的缩窄场景，折线侧是 `xForFreq` 逐 bin 落像素、离屏 bin 跳过（`:589-593`），**没有 max-pool 聚合**——多个 bin 落到同一像素时谁先画/被覆盖，峰的完整性无保证。

### 2.3 差距判定
- **已实现但缺深度**：能 zoom（裁源矩形），但降采样是朴素双线性/逐点落位，缺峰保持聚合；宽带缩窄视图下峰易糊/易丢。

---

## 3. 帧缓冲滚动 + 调色板索引（pushFFT）

### 3.1 上游真实做法（GPLv3）
`pushFFT()`（`waterfall.cpp:896-907`）——每来一行新 FFT：
```cpp
doZoom(drawDataStart, drawDataSize, rawFFTSize, dataWidth,
       &rawFFTs[currentFFTLine*rawFFTSize], latestFFT);          // ① 当前 raw 行 → 显示宽
memmove(&waterfallFb[dataWidth], waterfallFb,
        dataWidth*(waterfallHeight-1)*sizeof(uint32_t));          // ② 整帧上滚一行（O(W·H)）
for (int j = 0; j < dataWidth; j++) {
    pixel = (clamp(latestFFT[j], waterfallMin, waterfallMax) - waterfallMin)
            / (waterfallMax - waterfallMin);
    waterfallFb[j] = waterfallPallet[(int)(pixel*(WATERFALL_RESOLUTION-1))]; // ③ 顶行查 LUT
}
waterfallUpdate = true;                                            // ④ 标记下次 draw 重传纹理
```
- 滚动靠 **memmove 整张帧缓冲**（每新行一次 O(W·H) 拷贝），不是环形。
- 上色是"归一化 dB ∈[0,1] → 整数索引 → 32-bit BGRA"，查找 O(1)。
- 注意：瀑布量程 `waterfallMin/Max` 与折线量程 `fftMin/Max` **相互独立**（`waterfall.h:279-282`），折线增益和瀑布颜色增益可分别拖。

### 3.2 MBDSDR 现状
- 滚动是**环形指针 O(1) 前进**（`spectrum_display.cpp:189`），再由 `materialiseHistory()`（`:194-209`）把 256 个环行按逻辑序 blit 进一张 `history_`：
```cpp
for (int logical = 0; logical < ringDepth_; ++logical) {
    const int phys = (ringHead_-1-logical + ringDepth_) % ringDepth_;
    c.drawImage(0, logical, ringRows_[phys]);
}
```
- 上色 `colourForDb()`（`spectrum_display.cpp:233-240`）：
```cpp
float t = (db - dbFloorDb_) / (dbCeilDb_ - dbFloorDb_);
t = clamp(t, 0.f, 1.f);
return lut_[clamp((int)(t*255.f), 0, 255)];
```
- 量程耦合：`colourForDb` 与折线 `dbToY` **共用同一对** `dbCeilDb_/dbFloorDb_`（头文件 `spectrum_display.h:74-78` 明说"by the shared dbToY / colourForDb mapping"），无独立瀑布增益。

### 3.3 差距判定
- **滚动机制：已实现且真实，且 MBDSDR 更优**（环形 O(1) vs 上游整帧 memmove O(W·H)）。
- **独立瀑布增益：未实现**（与折线量程耦死）。

---

## 4. GL 纹理上传与绘制循环

### 4.1 上游真实做法（GPLv3）
`init()`（`waterfall.cpp:116-118`）建一张 GL 纹理；`updateWaterfallTexture()`（`:704-711`）**整图 `glTexImage2D` 重传**：
```cpp
glBindTexture(GL_TEXTURE_2D, textureId);
glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, dataWidth, waterfallHeight, 0,
             GL_RGBA, GL_UNSIGNED_BYTE, (uint8_t*)waterfallFb);
```
`drawWaterfall()`（`:208-216`）只在 `waterfallUpdate` 置位时才重传，然后贴图：
```cpp
if (waterfallUpdate) { waterfallUpdate = false; updateWaterfallTexture(); }
{ lock(texMtx); window->DrawList->AddImage((void*)(intptr_t)textureId, wfMin, wfMax); }
```
- 缩放/平移不改纹理内容，而是**改"从 raw 环重渲染哪一段"**（`updateWaterfallFb`，§6），再整图重传；纹理与可视窗 1:1，不靠 GPU 裁源矩形。
- 折线部分 `drawFFT()`（`:120-206`）走 ImGui `DrawList->AddLine` 逐段画，附一条 20% 透明的下投影（`:130,172`）；hold 线用主题色 `themeManager.fftHoldColor`（`:129`）。

### 4.2 MBDSDR 现状
- 纯 Qt raster：`paintEvent()`（`spectrum_display.cpp:556`）开 `QPainter`，折线 `drawPolyline`（`:609`），瀑布 `drawImage`（`:667`）。**无 OpenGL 纹理**。
- 每次 `setSpectrum` 末尾 `update()`（`:332`）触发整控件重绘；瀑布历史靠 `history_` 这张 CPU QImage。
- 折线/余晖/hold 三层叠加：余晖 ghost 先画（`:601-607`）、live trace（`:609`）、maxHold（`:610-613`）。

### 4.3 差距判定
- **未实现（GPU 路径）**——但这是架构取向（Qt raster 够 256×4096），不是缺陷；价值在性能，非正确性。

---

## 5. 调色板：1M 项 LUT 构建 + 外部 JSON 色板文件

### 5.1 上游真实做法（GPLv3）
(a) **高分辨率 LUT 插值** `waterfall.cpp:944-958`——把 N 个控制点色线性插值成 `WATERFALL_RESOLUTION=1,000,000` 项（`waterfall.h:11`）：
```cpp
for (int i = 0; i < WATERFALL_RESOLUTION; i++) {
    int lowerId = floorf(((float)i/WATERFALL_RESOLUTION)*colorCount);
    int upperId = ceilf (...);                       // 夹紧到 [0, colorCount-1]
    float ratio = (((float)i/WATERFALL_RESOLUTION)*colorCount) - lowerId;
    float r = colors[lowerId][0]*(1-ratio) + colors[upperId][0]*ratio;   // R/G/B 线性插值
    waterfallPallet[i] = (255u<<24) | (b<<16) | (g<<8) | r;              // 打包 BGRA8
}
updateWaterfallFb();                                  // 改色板后整段重染历史
```
- 默认 13 档 `DEFAULT_COLOR_MAP`（`waterfall.cpp:11-25`，深蓝→蓝→白→黄→橙→红）。

(b) **外部色板文件** `colormaps.cpp:12-47`——从磁盘读 JSON `{name, author, map:["#rrggbb",...]}`：
```cpp
mapTxt = data["map"].get<std::vector<std::string>>();
for (col : mapTxt) {                                   // 每个 "#rrggbb" → 3 个 0-255 浮点
    map.map[i*3+0] = std::stoi(col.substr(1,2), NULL, 16);
    map.map[i*3+1] = std::stoi(col.substr(3,2), NULL, 16);
    map.map[i*3+2] = std::stoi(col.substr(5,2), NULL, 16);
}
maps[map.name] = map;
```
- 运行时切换：`menus/display.cpp:65,238` 调 `waterfall.updatePalletteFromArray(map.map, map.entryCount)`；启动默认 `main_window.cpp:164` 用 `"Turbo"`。即**色板是用户可放文件扩展的**，不写死在代码里。

### 5.2 MBDSDR 现状
- LUT `rebuildColormap()`（`spectrum_display.cpp:211-231`）：256 项 `std::array<QRgb,256>`，在命名 stops 间分段线性插值：
```cpp
for (int i = 0; i < 256; ++i) {
    const float t = i/255.f;
    while (seg < n-2 && t > stops[seg+1].t) ++seg;     // 定位所在段
    const float u = (t - stops[seg].t)/(stops[seg+1].t - stops[seg].t);
    lut_[i] = qRgb(a.red()+u*(b.red()-a.red()), ...);  // 三色线性插值
}
```
- 三套**写死在** `core/tokens.h:414-444`：classic 8 档、mono 5 档、viridis 5 档；`setPalette(int)`（`spectrum_display.cpp:435-439`）只在 0..2 间循环。**无外部文件、无用户导入**。
- LUT 256 项对 8-bit 显示足够（上游 1M 项本身偏奢侈，无可见增益）。

### 5.3 差距判定
- **LUT 线性插值：已实现且真实**（机制等价；256 vs 1M 非缺陷）。
- **外部色板文件：未实现**（用户不可扩展色板）。

---

## 6. 缩放 / 平移 / 全量重渲染（onResize / view 变化）

### 6.1 上游真实做法（GPLv3）
`updateWaterfallFb()`（`waterfall.cpp:600-631`）——**整段重渲染**：对环里每一行 raw dB 重做 doZoom + 查 LUT，填满 `waterfallFb`：
```cpp
int count = std::min<float>(waterfallHeight, fftLines);
for (int i = 0; i < count; i++) {
    drawDataSize  = (viewBandwidth/wholeBandwidth) * rawFFTSize;          // 可视窗占整带比例
    drawDataStart = (rawFFTSize/2)*(offsetRatio+1) - drawDataSize/2;      // 平移偏移 → 列起点
    doZoom(..., &rawFFTs[((i+currentFFTLine)%waterfallHeight)*rawFFTSize], tempData);
    for (j) waterfallFb[i*dataWidth+j] = waterfallPallet[(int)(pixel*(RES-1))];
}
for (i=count; i<waterfallHeight; i++)  // 历史未满处填不透明黑
    for (j) waterfallFb[i*dataWidth+j] = (uint32_t)255 << 24;
```
- 触发点：`onResize`（`:805`）、`updatePallette`（`:957`）、`setViewBandwidth/Offset`（`:1039,1061`，受 `_fullUpdate` 开关）、`setWaterfallMin/Max`（`:1098,1111`）。
- 因为 raw dB 行保留着，**拖量程/换色板/缩放窗口都能整段重染，无需重跑 FFT**。

### 6.2 MBDSDR 现状
- `setViewBandwidth/Offset` 类操作对应 `setZoomFactor/resetZoom`（`spectrum_widget.cpp:389-394`）→ canvas 改 `zoomFactor_/viewCenterHz_`，**只改 paintEvent 的裁窗 srcL/srcR**（`:662-665`），不重算历史。
- 但因环里是**已上色像素**，拖 dB 量程（`setDbRange`，`:335-343`）或换色板（`setPalette`，`:435-439`）**不会重染已有行**——只有新推入的行用新映射。

### 6.3 差距判定
- **未实现（重渲染缺 raw 依据）**：缩放能做（裁窗），但"改量程/换色板整段重染历史"无 raw dB 可依，做不到。

---

## 7. FFT 平滑 / Hold / VFO-SNR（与 dsp 笔记交叉，此处只记渲染侧）

> 平滑机制详见 `sdrpp-dsp.md` §5。这里只补渲染侧差异。

- **折线 EMA**：`waterfall.cpp:914-920`（VOLK 向量化 `y=αx+(1-α)y`），α 连续可调（`:1190-1194`）。MBDSDR 在 `power_spectrum.cpp:125-139` 做帧域盒式平均（4/16 档硬切），**已实现且真实**（取向差，见 dsp 笔记 G5）。
- **Hold（峰值保持衰减）**：`waterfall.cpp:935-939` `latestFFTHold[i]=max(latestFFT[i], latestFFTHold[i]-fftHoldSpeed)`——带衰减的峰持。MBDSDR 有硬 maxHold（`spectrum_display.cpp:304-309`，不衰减）+ 独立余晖衰减包络（`:311-322`，画成另一条 ghost 折线），**机制等价、拆分更细**。
- **VFO SNR**：`calculateVFOSignalInfo`（`waterfall.cpp:571-597`）= VFO 通带内 max − 边带 bin 均值，可 EMA。MBDSDR 在引擎侧用全谱中位数估噪声底（`spectrum_engine.cpp:915-931`），思路不同但都真实。

---

## 8. style.cpp（线条/配色/缩放，非瀑布本体）

### 8.1 上游真实做法（GPLv3）
`style.cpp:16-50,55-73`：
```cpp
#ifndef __ANDROID__
    float uiScale = 1.0f;          // 桌面
#else
    float uiScale = 3.0f;          // 手机放大
#endif
baseFont = fonts->AddFontFromFileTTF(..., 16.0f*uiScale, ...);   // :48
bigFont  = ... 45.0f*uiScale;   // 频率大字，字形范围只留 '.','9'（:37）
hugeFont = ...128.0f*uiScale;   // 标题，字形范围只留 'S','D','R','+',' '（:43）
```
- 瀑布/折线**线条色不在 style.cpp**：取 ImGui 主题色 `ImGuiCol_PlotLines`（`waterfall.cpp:128`）与 `themeManager.fftHoldColor`（`:129`）、`waterfallBg`（`:835`），由主题管理。
- `beginDisabled/endDisabled`（`style.cpp:55-73`）只调透明度做"禁用态"。

### 8.2 MBDSDR 现状
- 配色集中在 `core/tokens.h` 的 DPI 设计令牌（`kSpectrumBg / kAccent / kTextAlpha*` 等），绘制处 `tokens::rgbaA(...)`、`tokens::scaled(...)`（`spectrum_display.cpp:570,608`）。
- 同样是"令牌集中、绘制处引用"，与上游主题管理思路一致。

### 8.3 差距判定
- **已实现且真实**：DPI 缩放 + 集中配色令牌两家都有；无需补。

---

## 9. 差距判定片段（供 Wave2 整合 `sdrpp-gap-analysis.md`）

| # | 上游 file:line | MBDSDR file:line | 判定 | 落地价值 | 云内可确定性验证 |
|---|---|---|---|---|---|
| G1 | `widgets/waterfall.cpp:65-90`（doZoom 块内取 max 的峰保持降采样），调用 `:616,897,909` | `ui/spectrum_display.cpp:660-667`（裁源矩形交 Qt 双线性缩放）+ `:589-593`（折线逐 bin 落像素、离屏跳过，无聚合） | **已实现但缺深度**：能 zoom，但缩窄降采样不保持峰 | 高：宽带缩窄视图下窄峰不糊/不丢 | 部分：max-pool 降采样器可写 ctest 对已知输入逐点断言（确定性）；视觉"不糊"需 offscreen QImage 像素比对（QTest 可做） |
| G2 | raw dB 行环 `waterfall.h:286` + `waterfall.cpp:1134-1147`；整段重染 `updateWaterfallFb:600-631`；改色板即重染 `updatePallette:957` | `ui/spectrum_display.cpp:181-189`（入环即 `colourForDb` 烤成 QRgb，raw dB 不留）；`rebuildColormap:211-231` 只重建 LUT；`setPalette:435-439`/`setDbRange:335-343` 不重染旧行 | **已实现但缺深度**：换色板/拖量程只影响新行，历史不重着色 | 高：用户拖 dB 量程或换色板时整张瀑布应即时重染，而非只染新行 | 是：存入 dB 行 → 改 palette/dbRange → 断言历史像素 == 新 LUT(dB)（纯 CPU、可单测） |
| G3 | `colormaps.cpp:12-47`（读 `{name,author,map:["#rrggbb"]}` JSON）+ `waterfall.cpp:960-974`（updatePalletteFromArray）+ `menus/display.cpp:65,238`（运行时切） | `core/tokens.h:414-444`（三套写死 stops）+ `ui/spectrum_display.cpp:435-439`（setPalette 仅 0..2 循环） | **未实现**：色板不可由用户文件扩展 | 中：可扩展色板生态；viridis 已覆盖色觉安全 | 是：解析一个 stops JSON → 重建 LUT，断言关键档 RGB（确定性） |
| G4 | `widgets/waterfall.cpp:116-118`（glGenTextures）+ `:704-711`（整图 glTexImage2D）+ `:215`（AddImage）；四把锁 `waterfall.h:249-252`；零拷贝交接 `main_window.cpp:230-236` | `ui/spectrum_display.cpp:194-209`（每推入用 QPainter blit 256 行到 history_）+ `:667`（drawImage 缩放）；引擎→UI 整 vector 拷贝 `spectrum_engine.cpp:855-866` emit、`spectrum_display.cpp:246` frame_=frame | **未实现（GPU 路径）**，架构取向差异 | 中：纯性能；256×4096 CPU raster 在桌面已够，上 GL 属过早优化 | 否：GPU/重绘耗时，云内只能压帧率冒烟，难确定性判定 |
| G5 | `waterfall.h:279-282` + `waterfall.cpp:610,900`（瀑布 waterfallMin/Max 与折线 fftMin/Max 独立） | `ui/spectrum_display.h:74-78` + `:233-240`（colourForDb 与 dbToY 共用 dbCeilDb_/dbFloorDb_） | **已实现但缺深度**：折线增益与瀑布颜色增益耦死 | 低-中：可单独压暗瀑布颜色而不动折线量程 | 是：解耦后断言 colourForDb 用独立 waterMin/Max 映射、dbToY 仍用原量程 |

> 反向提示（MBDSDR 已领先 / 不必补）：
> - **瀑布滚动**：MBDSDR 环形 O(1) 前进（`spectrum_display.cpp:189`）优于上游整帧 `memmove` O(W·H)（`waterfall.cpp:898`）——保留，勿照抄上游。
> - **窗口函数**：MBDSDR 有 Hann/Blackman/**Flattop**（`power_spectrum.cpp:87-100`），上游瀑布 FFT 链路窗口档更少。
> - **LUT 分辨率**：MBDSDR 256 项对 8-bit 显示足够，上游 1,000,000 项无可见增益，不必照抄。

---

## 10. 红线与未决
- 全部为机制性引用，**未复制 GPLv3 代码**；落地须以自有实现重写并保留 MIT（GPL 干净室）。
- 未在本机实跑：SDR++ 实际 GL 驱动行为、`Turbo` 色板文件落点（`colormaps::maps` 的扫描目录）需落地时再读 `theme_manager.cpp` / `main_window.cpp` 启动段。
- 本轮只读机制，**未改任何 cpp/ 代码，未 commit/push**（遵循云环境无凭据约定）。

---

## 11. 落地记录（Phase12 waterfall 深度落地，G1/G2/G3 + 时间轴联动）

> 本节记录差距清单 G1/G2/G3 的干净室落地结果。全部为自有 MIT 实现，未抄 GPLv3。
> 纯函数抽离到新头 `cpp/src/ui/spectrum_render.h`（对标 `spectrum_tune.h` 的 header-only、
> widget-free 风格），widget 只做接线。

### 11.1 G1 doZoom 峰保持（块内 max 降采样）
- 纯函数 `decimateBlockMaxRange()` / `decimateBlockMax()`（`spectrum_render.h:82,111`）：
  把 `[srcBegin,srcEnd)` 按比例切成 `dstN` 块，每块取 **max**（非均值），窄峰在 zoom-out 不被抹平。
- 接入瀑布绘制（`spectrum_display.cpp:700-740`）：可视源宽 `srcW <= falls.width()`（放大/1:1，
  即 zoom-in 视图）仍走 Qt `drawImage` 双线性；当 `srcW > falls.width()`（多 bin 压到一个像素）
  逐行对原始 dB 做 `decimateBlockMaxRange` 到显示宽，再查 LUT 上色，缓存到 `fallsPeak_`。
- 与上游取向一致：zoom-in（窄视窗）不触发聚合，zoom-out（宽带压窄）才峰保持。

### 11.2 G2 瀑布历史重染（保留 raw dB 行，不丢帧）
- 环形由"已上色 `QRgb` 行"改为"**raw dB 行**"：`ringDb_`（`spectrum_display.h` 私有成员，
  `allocateRing` `:169`、`pushHistoryRow` `:183` 只写 raw dB，不再入环即烤色）。
- `history_` 仍是 bin 宽彩色快照（公开契约不变，`realFrameDrivesHistory` 等旧测试不破），
  由 `materialiseHistory()`（`:195`）从 `ringDb_` + 当前 LUT/量程重算。
- 触发重染：`setPalette`（`:443`）、`setDbRange`（`:339`，auto off 时）、`setAutoRangeOn(false)`
  （`:352`）都在改参数后调 `materialiseHistory()`——整段历史即时重着色，无需重跑 FFT、不丢行。
- 未写历史（`logical >= ringCount_`）的槽位填黑，与旧行为一致。

### 11.3 G3 外部 JSON 色板（stops 解析+校验，诚实回退）
- 纯解析 `parseColormapJson()`（`spectrum_render.h:171`，Qt6 `QJsonDocument`）：接受
  `{"name":..,"stops":["#rrggbb",..]}`（均匀 0..1）或 `[{"t":..,"c":"#rrggbb"},..]`（显式位置）；
  任意畸形（非 object / <2 stops / 非 #rrggbb / object 无 `t`）返回 false 且**不动 out**，
  调用方保留内置三档。
- `SpectrumDisplay::loadColormapFromJson/File`（`:451,462`）：成功即替换 `customStops_`、重建 LUT、
  重染历史；失败诚实回退。内置 classic/mono/viridis 三档保留（`setPalette` 清自定义档）。

### 11.4 瀑布时间轴刻度 × 缩放/平移联动（纯函数）
- `niceStepForSpan()` / `freqTicksNice()`（`spectrum_render.h:234,244`）：取 `{1,2,2.5,5,10}×10^k`
  步长使约 5 条刻度铺满可视窗；`paintEvent` 的频率条（`:823`）改为调它。
- 数学与旧内联完全等价（行为不变），但现在可单测：zoom-in 步长变细（2.4 MHz→500 kHz，
  ×16→50 kHz），平移一个整步步首刻度随之移动。

### 11.5 确定性单测（ctest）
- 新 `tests/test_spectrum_render.cpp`（header-only，链 Core/Gui，同 tune 范式）：
  块内 max 语义、窄峰 survive、LUT 端点、量程重映射、JSON 合法/显式/非法回退、刻度随缩放/平移。
- `tests/test_spectrum_display.cpp` 新增 `reRenderHistoryOnPaletteSwitch`：换灰阶色板后同列像素
  变近白（229）、floor 列纯黑、行数不丢、畸形 JSON 拒绝保持原样、收窄量程后同 raw 值变 204。
- CMakeLists 追加 `test_spectrum_render`（tune 块之后）。

### 11.6 测试结果（offscreen, Qt 6.8.2, GCC 10.3）
- `spectrum_render`：11 passed / 0 failed（新增纯函数）。
- `spectrum_display`：15 passed（旧 14 + 新重染用例）。
- `spectrum_autorange` 9 / `spectrum_interaction` 8 / `spectrum_tune` 12：全绿，98 基线不破。
- `ctest -R spectrum`：5/5 passed。

### 11.7 红线遵守
- 只写 `cpp/src/ui/{spectrum_render.h, spectrum_display.{h,cpp}}`、`cpp/tests/{test_spectrum_render.cpp,
  test_spectrum_display.cpp}`、`cpp/CMakeLists.txt`（追加）、`docs/learn/phase12/`、`docs/learn/phase13/`。
- GPL 干净室：仅学机制，全部自有重写；禁 mock（纯函数直断言数值）；未 commit/push。


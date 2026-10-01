# A1 渲染器差异审计报告（只读）

> 范围：桌面 `cpp/src/ui/spectrum_widget.{h,cpp}`（容器 441 行）+ `cpp/src/ui/spectrum_display.{h,cpp}`（统一画布 1018 行）；移动 `mobile/lib/widgets/spectrum_display.dart`（CustomPainter 914 行）+ 调用方 `mobile/lib/pages/spectrum_page.dart`。
> 依据：`docs/learn/phase3/_PHASE3_SPEC.md` §0/§1/§3。
> 原则：全部证据带 `file:line`；拿不准标「推断」；原文未给标「原文未给」。本报告只给差异与修复建议，不写实现代码。
> 阅读对象：Wave2 实现 agent——凭本报告即可动手，不必重读两端全部代码。

---

## 0. 两端骨架速查（先建立坐标系）

| 维度 | 桌面（C++/Qt） | 移动（Flutter） |
|---|---|---|
| 容器 | `SpectrumWidget`：工具条+画布+峰值表（`spectrum_widget.cpp:24-304`） | `SpectrumPage._ConnectedBody`：状态栏+S 表+widget（`spectrum_page.dart:245-290`） |
| 画布结构 | 单 `QWidget` 内三段：trace→1px gap→频率条→可拖 divider→瀑布（`spectrum_display.cpp:55-92`） | Column 三段：`Expanded(flex:5)` trace → fixed 高度频率条 → `Expanded(flex:4)` 瀑布（`spectrum_display.dart:261-350`） |
| x 域来源 | `plotX0=scaled(kDispLeftInset=50)` 起，三区域共享 `plotW`（`spectrum_display.cpp:57-59,85-88`） | 每行 `Row` = `[左gutter=48][Expanded][右gutter=24]`，三行重复（`spectrum_display.dart:128-130,266-348`） |
| 可见窗口 | `span=Fs/zoomFactor`，zoom 1–16，可 pan（`spectrum_display.cpp:102-112`） | **无 zoom/pan**，恒 `span=sampleRateHz`（`spectrum_display.dart:88-92`） |
| 帧喂入 | `MainWindow` → `SpectrumWidget::setSpectrum` → `canvas_->setSpectrum`（`spectrum_widget.cpp:359-361`；`main_window.cpp:1701`） | `controller.spectrumStream.listen` → `setState(_frame=f)`（`spectrum_page.dart:252-254`） |
| VFO | 多 VFO band box，USB/LSB/CW 边带对齐（`spectrum_display.cpp:438-454`） | 仅中央一条竖线+三角+BW 阴影带（`spectrum_display.dart:630-674`） |
| 余晖 | 每 bin 连续包络衰减（`spectrum_display.cpp:310-321`） | 最多 12 层历史帧叠加（`spectrum_display.dart:145-157,586-606`） |

---

## 1. 几何对齐（trace / 频率条 / 瀑布 x 域）

### 差异 1.1【核心·用户可感知】trace 用「bin 中心」、瀑布用「像素边」映射，缩放越大错位越大

- **现状（桌面）**：
  - trace 折线按 bin 中心取 x：`f = bandLo + (i+0.5)*binHz; x = xForFreq(f,...)`（`spectrum_display.cpp:565-569`）。
  - 瀑布按像素边裁剪：`srcX=floor((fLo-bandLo)/Fs*bins)`、`srcW=ceil(span/Fs*bins)`，再把 `[srcX,srcX+srcW]` 整段 stretch 到 `falls.width()`（`spectrum_display.cpp:631-638`）。
  - 两套约定差固定 0.5 bin：trace bin i 中心落在瀑布列 `i-0.5` 处。
- **影响**：
  - zoom=1：`binW=bins≈2048`，错位 ≈ `plotW/(2*bins)` ≈ 0.25px，肉眼不可见。
  - zoom=16（`kZoomMax=16`，`tokens.h:214`）：`binW=bins/16≈128`，错位 ≈ `plotW/(2*128)` ≈ 4px（1000px 宽画布）。**用户在 Ctrl+滚轮放大后会看到「trace 峰尖比瀑布同色块偏右 ~4px」**，即点名缺陷「频谱与瀑布对不齐」的主因之一。
  - 叠加 `floor/ceil` 取整（`spectrum_display.cpp:633-634`），pan 后 `binF` 为小数时再引入 ≤1 bin 误差，zoom 16 下再 ±8px。
- **修复建议**：
  - 让瀑布裁剪与 trace 共用同一套 bin→x 公式：瀑布也按「列 i 中心 = bandLo+(i+0.5)*binHz」取 srcX/srcW，或 trace 改按像素边取 x（二选一，保持两端一致）。
  - 去掉 `floor/ceil`，改用浮点源 `QRectF` 并依赖 `drawImage` 的浮点缩放（`QPainter::drawImage` 已支持浮点 src rect）。
- **确定性测试（桌面 offscreen QTest）**：
  - 构造一帧已知 dbfs（单尖峰在 bin=1024），分别在 zoom=1 与 zoom=16 下：
    - 断言 `displayCanvas()->xForFrequency(peakFreqHz)`（`spectrum_display.h:131`）与 `waterfallCropLeftBin()`（`spectrum_display.h:150` 已暴露）推出的瀑布列中心 x 之差 ≤ 1px。
    - 现有 `waterfallCropLeftBin()` 就是为这个契约暴露的（注释 `spectrum_display.h:147-150`），补 zoom=16 用例即可。

### 差异 1.2【核心·用户可感知】Flutter trace 重采样到 120–500 点 + 3-tap 平滑，瀑布却是 1:1 设备像素列

- **现状（Flutter）**：
  - trace：`samples=(size.width/2).clamp(120,500)`，`xs` 线性分布，`bin=round(k/(samples-1)*(n-1))`，再做 `ys[k]=(ys[k-1]+ys[k]+ys[k+1])/3` 三点平滑（`spectrum_display.dart:414-422`）。
  - 瀑布：`bin=(x/_w*n).floor()`，`_w=size.width*dpr`（`spectrum_display.dart:175-183,353-363`）。
  - 即 trace 最多 500 个采样点，瀑布在 dpr=3 的手机上是 `1080*3≈3000` 列；trace 每点覆盖 ~6 瀑布列，再被三点平滑横向模糊。
- **影响**：
  - 窄手机竖屏（width≈390 logical，dpr=3）：`samples=195`，`_w≈1170`，trace 峰尖被平滑后比瀑布同色峰横向宽 ~2–3 列 ≈ 3–5px。**用户在高 dpr 手机上看到「trace 峰是圆滑的、瀑布峰是锐利的，两者中心错开 ~3px」**——移动端「对不齐」的主因。
- **修复建议**：
  - 二选一：(a) trace 也按 `samples=min(n, _w)` 逐列取点、去掉三点平滑（或平滑只作用于 y 不作用于 x）；(b) 瀑布列数对齐 trace 采样数（`_ensureBuffer` 的 `_w` 取 `min(_w, samples)`）。
  - 推荐 (a)：trace 与瀑布共用同一 bin→列 映射，平滑保留 y 方向即可。
- **确定性测试（Flutter widget test）**：
  - 注入一帧 db 仅在 `bin=100` 处为 0dB、其余 -100dB；`pumpWidget` 后用 `tester.renderObject` 拿到 `_SpectrumPainter`，断言 trace path 在 `x=100/n*size.width` 处有顶点、且 `_ingestFrame` 写入的 rgba 缓冲第 `floor(100/_w*n)` 列颜色与该 x 对应。
  - 可对 `_buildTrace`（`spectrum_display.dart:410`）做纯函数单测：给定尖峰帧，断言返回的 `trace` path 最高点 x 与 `_dxToHz` 反推的 bin 一致。

### 差异 1.3【用户可感知】Flutter 把 BW 阴影带画在 trace 上却没画在瀑布上

- **现状**：
  - Flutter：`bandL/bandR` 矩形只在 `_SpectrumPainter.paint` 里画一次（`spectrum_display.dart:630-639`），`_WaterfallPainter` 只画中央一条 `textAt(0.12)` 细线（`spectrum_display.dart:896-900`）。
  - 桌面：VFO band box 同时 `fillRect(trace)` 与 `fillRect(falls)`（`spectrum_display.cpp:652-653`），左右边+中心线也各画两遍（`spectrum_display.cpp:656-663`）。
- **影响**：Flutter 用户看到 trace 中间有一块半透明阴影带、瀑布上却没有，**视觉上「trace 有框、瀑布没框」，进一步放大「对不齐」错觉**。
- **修复建议**：在 `_WaterfallPainter.paint` 里按相同 `bandL/bandR` 公式补画半透明阴影带与左右边（公式与 trace 完全一致，因为 x 域相同）。
- **确定性测试**：widget test 断言 `_WaterfallPainter` 在 `Rect.fromLTRB(bandL,0,bandR,h)` 区域绘制了 `accent.withAlpha(0.08)` 像素（可用 `matchesGoldenFile` 或自绘 record）。

### 差异 1.4【内部一致性问题】两端 gutter 宽度与 dB 标签画法不同，但不影响三区域对齐

- **现状**：
  - 桌面：`plotX0=scaled(50)` 即 plot 左缘，dB 标签直接画在 plot 左缘内侧 `trace.left()+padR`（`spectrum_display.cpp:552`）——标签压在折线图左缘上方；右 inset 仅 12px。
  - Flutter：左 gutter=48、右 gutter=24 是独立 SizedBox，plot 在 gutter 之后才开始；dB 数字画在左 gutter painter 内右对齐（`spectrum_display.dart:436-465`），右 gutter 只画刻度线不画字（`spectrum_display.dart:468-487`）。
- **影响**：桌面 dB 数字与 trace 左缘 ~20px 有视觉重叠（克制风格下可接受）；Flutter 干净分离。**不导致三区域不对齐**，但两端观感不同。
- **修复建议**：A1 不强制统一；若做 UI 收敛（W2c），建议桌面把 dB 标签移出 plot 区（仿照 Flutter 独立 gutter）。
- **确定性测试**：无功能影响，不必加测试。

---

## 2. 占位比例（trace / 瀑布高度分配）

### 差异 2.1【核心·用户可感知】Flutter 固定 flex:5/4 让 trace 比瀑布大；桌面默认 1:1 且 divider 可拖

- **现状**：
  - 桌面：`traceShare_ = kDefaultSpecFraction = 0.5`（`tokens.h:448`；`spectrum_display.cpp:49`），trace:瀑布 = 1:1；divider 可拖，clamp 到 `[kSpecAreaMinH=120, pool-kWfAreaMinH=60]`（`spectrum_display.cpp:70-79,859-869`）。频率条固定 `kDispFreqStripH=30`（`tokens.h:441`）。
  - Flutter：`Expanded(flex:5)` trace（`spectrum_display.dart:264-265`）+ `Expanded(flex:4)` 瀑布（`spectrum_display.dart:328-329`），trace:瀑布 = 5:4 ≈ **55.6% : 44.4%**；频率条固定 `spacingL*2.4 = 16*2.4 = 38.4`（`spectrum_display.dart:130`）。**无 divider、不可拖**。
- **影响**：
  - 用户点名「频谱占位过大」即指此：Flutter trace 占 55.6%、瀑布只 44.4%，而 SDR 类仪器（SDR++/SDRConsole）惯例是**瀑布为主、trace 为辅**（瀑布是时频历史，信息量更大）。Figma 预览（`cpp/scratch/figma_unzip/preview/`）是车机深色卡片语言，并未规定比例，但「克制、瀑布为主」是 SDR 行业惯例。
  - 桌面默认 1:1 也偏小瀑布，但用户可拖 divider 自救；Flutter 无自救手段。
- **修复建议**：
  - Flutter：把 trace 改 `flex:4`、瀑布改 `flex:5`（瀑布为主），或直接 `flex:1` : `flex:2`（瀑布 2/3）。建议与设计确认；最低修正是 5:4 → 4:5。
  - 若要对齐桌面「可拖」能力，Flutter 可在频率条下方加一个 8px 拖动手势区（`GestureDetector` 竖直拖动改 flex）；但这是增强，不是本缺陷必需。
  - 频率条高度：Flutter 38.4 vs 桌面 30（scaled 后随 dpi 缩放），不强制统一。
- **确定性测试（Flutter widget test）**：
  - `tester.getSize(find.byType(Expanded).first)` 取 trace 高度、`.last` 取瀑布高度，断言 `瀑布高度 > trace 高度`（或固定断言 `traceH:fallsH == 4:5`）。
  - 桌面 offscreen：`resize(800,600)` 后断言 `displayCanvas()->dividerY()` 落在 `topPad+traceH` 处、且 `traceRect.height():fallsRect.height() ≈ 1:1`（`spectrum_display.h:52-56` 已暴露）。

---

## 3. 时间轴（频率刻度条）算法

### 差异 3.1【用户可感知】刻度步长选择算法不同：桌面「nice number 凑 5 格」 vs Flutter「固定候选表 + 64px 最小间距」

- **现状**：
  - 桌面：`raw=span/kWaterfallFreqTicks(=5)`，`mag=10^floor(log10(raw))`，取 `{2,2.5,5,10}*mag` 中第一个 ≥ raw 的（`spectrum_display.cpp:716-719`；`kWaterfallFreqTicks=5` 在 `tokens.h:260`）。目标 ~5 格。
  - Flutter：候选表固定 `[1e5,2.5e5,5e5,1e6,2e6,5e6,1e7]`（`spectrum_display.dart:384-392,795-803`），选第一个 `step/span*width >= 64px` 的（`spectrum_display.dart:818-827`）。
- **影响**：同一扫宽同一画布宽度下，两端格数不同。例如 span=2MHz、width=800：
  - 桌面：raw=400k，mag=100k，nice=500k → 4 格（1M/1.5M/2M…）。
  - Flutter：候选 500k → 500k/2M*800=200px ≥64，选中 500k → 也是 4 格。多数情况接近，但 span=5MHz 时桌面 raw=1M→nice=1M（5 格），Flutter 1M/5M*800=160px 选中 1M（5 格）——偶尔一致，边界 span（如 raw 落在 2.5x 与 3x 之间）会分歧。
- **修复建议**：两端统一为同一份「nice number」算法（桌面那套更标准），Flutter 改为按 raw 算 mag 再取 {1,2,2.5,5,10}*mag；候选表保留作 fallback。
- **确定性测试**：
  - Flutter 纯函数单测：给定 `(spanHz, plotWidthPx)`，断言选出的 step 落在「相邻刻度间距 40–80px」区间。
  - 桌面 offscreen：`paint` 后断言 strip 上刻度线数量在 3–8 之间。

### 差异 3.2【用户可感知】标签小数位：桌面恒 3 位小数 vs Flutter 自适应 0/1/2 位

- **现状**：
  - 桌面：`QString::number(f/1e6, 'f', 3)` 恒 3 位（`spectrum_display.cpp:726`）——任何步长都画 "100.000"、"100.500"、"2.000"。
  - Flutter：按步长带 decimals：100k/250k/500k→2 位、1M/2M→1 位、5M/10M→0 位（`spectrum_display.dart:795-803,843`）——画 "100.00"、"100.0"、"100"。
- **影响**：用户在两端看到同一频率，桌面永远带 ".000" 后缀（啰嗦），Flutter 干净。**两端观感不一致**；桌面在 10MHz 扫宽下标签 "100.000" 与 "105.000" 冗余。
- **修复建议**：桌面改 Flutter 同款自适应小数位（按 nice step 取 decimals），或提取成共用函数。
- **确定性测试**：桌面 offscreen 在 span=20MHz 下断言 strip 标签文本不含 ".000"（即 "100" 而非 "100.000"）。

### 差异 3.3【用户可感知】刻度方向、中心高亮、背景色均不同

- **现状**：
  - 刻度线方向：桌面从 `strip.bottom()` 向上画 `kWaterfallTickH=4`（`spectrum_display.cpp:725`）——刻度挂在条底部；Flutter 从 `y=0` 向下画 6px（`spectrum_display.dart:841`）——刻度挂在条顶部。
  - 中心高亮：Flutter 在中央画 10px 长 accent 长线 + accent 加粗中心频率标签（`spectrum_display.dart:856-876`）；桌面**无中心高亮**（只有普通刻度）。
  - 背景：桌面 `kSpectrumBg.darker(120)`（`spectrum_display.cpp:715`）比 trace 背景深；Flutter `spectrumBg` 与 trace 同色（`spectrum_display.dart:807-810`）。
  - 顶部分隔线：Flutter 在条顶画 1px divider 线（`spectrum_display.dart:833`）；桌面在 divider hairline 处画线（`spectrum_display.cpp:731-734`）。
- **影响**：刻度条是用户定位频率的主要视觉参考，两端「刻度方向相反、中心有没有高亮」直接影响找频效率。Flutter 的中心 VFO 高亮是好设计，桌面缺失。
- **修复建议**：
  - 刻度方向统一（建议 Flutter 同款挂顶部，因为下面就是瀑布）。
  - 桌面补中心频率高亮长线 + 加粗标签（仿照 Flutter `spectrum_display.dart:856-876`）。
  - 背景统一为 `spectrumBg`（不要 darker），与 Flutter 对齐。
- **确定性测试**：桌面 offscreen 断言 strip 中央 `x=plotX0+plotW/2` 处有一条更长的刻度线（可通过 `QImage` 像素断言，或暴露 strip tick 数给测试）。

---

## 4. 余晖（persistence）

### 差异 4.1【核心·算法不同】桌面是「每 bin 连续包络衰减」，Flutter 是「N 层历史帧叠加」

- **现状（桌面）**：
  - 每 bin 一个 `persist_[i]` 包络；每帧 `aged=persist_[i]*decay; persist_[i]=max(fresh, aged)`（`spectrum_display.cpp:311-320`）。
  - decay：低=0.78、高=0.93（`tokens.h:110-111`）。
  - 绘制：整条 ghost 折线用**恒定 alpha**：低=0.35、高=0.55（`tokens.h:113-114`），颜色 `kAccent`（`spectrum_display.cpp:577-583`）。
  - 层数无上限（包络自然衰减到 -inf）。
- **现状（Flutter）**：
  - 维护 `List<SpectrumFrame> _history`，最多 `persistenceMaxLayers=12` 层（`tokens.dart:140`；`spectrum_display.dart:148-153`）。
  - 每帧把上一帧 push 进 history，绘制时对第 age 层画一条独立 trace，`alpha = persistenceBaseAlpha(0.34) * decay^age`（`spectrum_display.dart:376-380,587-606`）。
  - decay：低=0.70、高=0.88（`tokens.dart:143,146`）。
  - 最旧层 age=12 时 alpha=0.34*0.88^12≈0.03，自动不可见。
- **影响**：
  - 视觉差异：桌面 ghost 是「包络」——信号掉落后峰顶仍以包络形式慢慢回落（max 保持）；Flutter ghost 是「过去每一帧的完整 trace 残影」——信号掉落后整段 trace 渐隐。两种手感不同。
  - decay 常数不同：桌面高 0.93（更长残影）vs Flutter 高 0.88（较短）。
  - alpha 模型不同：桌面高恒 0.55（整条 ghost 最亮）；Flutter 最新层才 0.34*0.88≈0.30。
  - **用户在两端切换会感觉「桌面余晖更长更亮、Flutter 更短更淡」**。
- **修复建议**：
  - 统一算法：推荐桌面包络模型（更省内存、更接近专业 SDR），Flutter 改为按 bin 维护 `Float32List` 包络、与桌面同 decay/alpha。
  - 若短期不统一算法，至少对齐 decay 常数（0.78/0.93）与 base alpha（0.35/0.55）。
  - 持久化：桌面把档位存 QSettings（`spectrum_widget.cpp:90,300-303`），Flutter 页面本地态不落盘（`spectrum_page.dart:85`）——建议统一（移动端也可落盘，或桌面也不持久化，按产品定）。
- **确定性测试**：
  - 桌面：构造尖峰帧后连续喂 N 帧 -100dB，断言 `persist_` 中该 bin 值按 `decay^k` 衰减（需把 `persist_` 暴露只读，或通过 `paintEvent` 后 QImage 像素断言 ghost 线 alpha）。
  - Flutter：`persistenceLayerAlpha(mode, age)` 已是纯函数（`spectrum_display.dart:376`），直接单测 `low,age=1==0.34*0.70`、`high,age=12<0.02`。

### 差异 4.2【行为不同】clear 时机与切档不清空

- **现状**：
  - 桌面：`clearPersistence()` 立即 `persist_.clear()`（`spectrum_display.h:94`）；切到 0 档也 `persist_.clear()`（`spectrum_display.cpp:385`）。
  - Flutter：靠 `persistenceClearTick` 变化触发 `_history.clear()`（`spectrum_display.dart:136-138`）；切到 off 档**不清空 history**，只是不绘制（`spectrum_display.dart:587` 的 `if (persistence.isOn)` 跳过）——从 off 切回 low/high 会把旧 history 重新画出来。
- **影响**：Flutter 用户「关余晖再开」会看到旧残影突然回来，违反直觉。
- **修复建议**：Flutter 在 `didUpdateWidget` 检测 `widget.persistence` 从 on→off 时也 `_history.clear()`。
- **确定性测试**：widget test 先 on 喂 3 帧、切 off、再切 on，断言 history 长度为 0。

---

## 5. 标记（fixed mark / VFO）

### 差异 5.1【能力差异】fixed mark 带名字 vs 仅频率；拖动 snap 与否

- **现状（桌面 fixed mark）**：
  - 结构 `FixedMarker{double freqHz; QString name}`（`spectrum_display.h:48`），自动命名 M1/M2…（`spectrum_widget.cpp:109-110`）。
  - 绘制：细竖线 + 顶部 `name + freqMHz` 文本（`spectrum_display.cpp:620-625`）；选中=琥珀虚线 `#e0b35a` + 顶部手柄圆点（`spectrum_display.cpp:603-619`）。
  - 交互：点击选中（容差 `touchMin/2`，`spectrum_display.cpp:801-806`）、拖动**自由频率无 snap**（`spectrum_display.cpp:906-911`）、键盘 ←/→ 步长 `viewSpan/200`（0.5% 视窗，`spectrum_display.cpp:963`）、Delete 删除。
  - 持久化：容器 QSettings（`spectrum_widget.cpp:306-328`），编辑完发 `fixedMarkersEdited` 信号落盘。
- **现状（Flutter fixed mark）**：
  - 结构 `List<double> fixedMarksHz`（`spectrum_display.dart:46`）——**无名字、无文本标签**。
  - 绘制：未选中=琥珀 `warning` 虚线（`spectrum_display.dart:570-581`）；选中=琥珀实线 + 顶部三角手柄（`spectrum_display.dart:554-568`）。
  - 交互：点击选中、水平拖动 **snap 到 adaptive step**（`spectrum_display.dart:117-125`）、键盘 ←/→ 步长 = `adaptiveFreqStepHz`（100k–10M 粗档，`spectrum_display.dart:226-232`）、Delete 删除。
  - 持久化：回调 `onMarkChanged(old,new)` / `onDeleteMark(hz)` 由父页面落盘（`spectrum_display.dart:49-52`）。
- **影响**：
  - Flutter 无名字标签——用户放了多个标记后无法区分（只有竖线）。
  - 拖动手感不同：桌面自由、Flutter snap 到粗网格（可能跳变）。
  - 键盘步长不同：桌面 0.5% 视窗（细腻）vs Flutter 100k–10M（粗糙，在 2MHz 扫宽下步长=100k 已经是 5% 视窗）。
  - 未选中线色：桌面 `kAccent` 低透明（`spectrum_display.cpp:607-609`）vs Flutter `warning` 琥珀（`spectrum_display.dart:573`）——颜色家族不同。
- **修复建议**：
  - Flutter fixed mark 结构加 `name`（或至少画 freq 文本标签）。
  - 统一拖动：推荐 Flutter 去掉 snap 或把 snap 步长调细（与桌面 0.5% 视窗对齐）。
  - 统一键盘步长为 `viewSpan/200`。
  - 未选中线色统一为 `kAccent` 低透明（桌面风格）或琥珀（Flutter 风格），二选一。
- **确定性测试**：
  - Flutter widget test：拖动标记后断言 `onMarkChanged` 回调的 newHz 与拖动位置反推频率一致（容差 ≤ snapStep/2）。
  - 桌面 offscreen：mousePress 命中标记、mouseMove 50px、mouseRelease，断言 `fixedMarkers().last().freqHz` 变化量 = `50/plotW*span`（无 snap）。

### 差异 5.2【能力差距】VFO：桌面多 VFO band box 可拖边改带宽 vs Flutter 单中央线不可交互

- **现状（桌面 VFO）**：
  - 多 VFO：`QVector<VfoMarker>`，每 marker 有 id/freqHz/bandwidthHz/mode/color/selected（`spectrum_display.h:249`）。
  - 绘制：band box 同时画在 trace 和瀑布上（`spectrum_display.cpp:652-663`）；USB dial 在 box 左缘、LSB/CW 在右缘、其他居中（`spectrum_display.cpp:443-449`）。
  - 交互：拖 box 体=tune、拖左右边=改带宽（clamp `[kVfoMinBandwidthHz, kVfoMaxBandwidthHz]`，`spectrum_display.cpp:894-905`）、点击选中（`spectrum_display.cpp:818-835`）。
- **现状（Flutter VFO）**：
  - 仅中央一条 `accentHover` 竖线 + 顶部小三角（`spectrum_display.dart:660-674`）+ BW 半透明阴影带（`spectrum_display.dart:630-639`）。
  - **无多 VFO、无拖边改带宽、无边带对齐**；点 trace 任意位置 = `onTapFrequency` 直接改 VFO 频率（`spectrum_display.dart:207`）。
- **影响**：Flutter 移动端是「单 VFO、只读」——用户不能框选带宽、不能看 USB/LSB 边带位置。这是**功能差距**，不是 bug，但两端 VFO 概念不一致。
- **修复建议**：
  - 短期（W2a）：Flutter 至少把 BW 阴影带左右边画在瀑布上（见 1.3），并在 trace 上画 VFO 左右边线（不要求可拖，先视觉对齐）。
  - 长期：移动端补拖边改带宽手势（横拖边），与桌面 VFO 模型对齐。
- **确定性测试**：Flutter widget test 断言 BW 阴影带左右边 x 与 `channelBandwidthHz` 公式一致（`bandL=(centerBin-halfBins)/n*width`）。

---

## 6. 其他渲染差异

### 差异 6.1【用户可感知】dB 量程：桌面手动+自动量程 vs Flutter 硬编码 [-100,0]

- **现状**：
  - 桌面：`setDbRange(min,max)` 手动 spinbox（`spectrum_widget.cpp:123-148`）+ 自动量程按滑动峰值 easing 调整 ceiling（`spectrum_display.cpp:260-301`），floor 钉在手动值。
  - Flutter：`_dbToY` 硬编码 `lower=-100, upper=0`（`spectrum_display.dart:367-372`），无自动量程、无手动调整。
- **影响**：弱信号帧（峰值 -80dB）在 Flutter 上 trace 贴底、看不出起伏；桌面自动量程会把 ceiling 收到 -40dB 左右让弱信号可见。
- **修复建议**：Flutter 移植桌面自动量程算法（滑动 24 帧峰值、easing 1.5dB/帧），或至少加一个手动 dB 范围入口。
- **确定性测试**：Flutter 纯函数测试给定峰值序列，断言 dB 范围随峰值收敛。

### 差异 6.2【数据源不同】噪声底：桌面注入实测值 vs Flutter 当前帧中位数

- **现状**：
  - 桌面：`setNoiseFloorDb(float)` 由集成层注入真实测量（`spectrum_display.h:117`），画虚线 + "NF" 标签（`spectrum_display.cpp:666-680`）。
  - Flutter：`noiseFloor = sorted[n~/2]` 当前帧 db 中位数（`spectrum_display.dart:623-624`），画琥珀虚线 + "NF xx"（`spectrum_display.dart:688-714`）。
- **影响**：桌面 NF 是引擎长期跟踪值；Flutter NF 是单帧中位数，会随单帧噪声抖动。两者数值不可比。
- **修复建议**：Flutter 的 `SpectrumFrame` 若已带 noiseFloor 字段则用之；否则在 FFT processor 里做长期滑动中位数。
- **确定性测试**：widget test 注入已知 db 序列，断言 NF 线 y 坐标对应中位数。

### 差异 6.3【能力差距】峰值表：桌面成熟跟踪+底部表格 vs Flutter 仅全局最大峰

- **现状**：
  - 桌面：`dsp::detectPeaks` 多峰检测 + 跨帧跟踪（seen/missed 计数，`spectrum_display.cpp:476-527`），trace 上画三角+下拉线（`spectrum_display.cpp:682-712`），底部 QTableWidget 列峰值表（`spectrum_widget.cpp:272-294`），双击表格 tune。
  - Flutter：仅 `db.indexOf(max)` 单峰（`spectrum_display.dart:615-622`），画一个三角 + 左上角读数盒（`spectrum_display.dart:716-775`）。
- **影响**：Flutter 看不到多峰列表，无法点峰 tune。
- **修复建议**：W2a 可暂不补多峰（功能增强归 W2c/W2d），但本报告登记差距。

### 差异 6.4【调色板】桌面 3 种瀑布色板可切 vs Flutter 仅经典

- **现状**：
  - 桌面：经典/单色/Viridis 三选（`spectrum_display.cpp:411-415`；`tokens.h:396-426`），combo 切换、QSettings 持久化（`spectrum_widget.cpp:221-234`）。
  - Flutter：`waterfallStops` 硬编码经典（`tokens.dart:118-127`，注释自称与 cpp 一致），无切换 UI。
- **影响**：移动端用户不能切色板。非缺陷，登记差距。
- **修复建议**：W2c 补色板切换按钮。

### 差异 6.5【空态】Flutter 有诚实空态 vs 桌面永远画背景

- **现状**：
  - Flutter：frame==null 返回 `SizedBox.shrink()`（`spectrum_display.dart:259`），外层 `_DisplayArea` 给 EmptyState「未连接 rtl_tcp」（`spectrum_page.dart:180-193`）。
  - 桌面：`paintEvent` 永远 `fillRect` 背景（`spectrum_display.cpp:534`），无空态区分；`haveFrame_=false` 时只是不画折线，背景仍在。
- **影响**：桌面未连硬件时用户看到空黑画布，无提示。`_PHASE3_SPEC.md` §0 已要求「无硬件诚实空态」。
- **修复建议**：桌面在 `!haveFrame_` 时画布中央画「未连接硬件」提示文字（仿 Flutter EmptyState）。
- **确定性测试**：offscreen 不喂帧直接 show，断言画布中央有提示文本（可用 QLabel 覆盖或 `QImage` 断言非空像素）。

### 差异 6.6【其他细节】trace 填充、抗锯齿、瀑布速度、历史深度

- **trace 填充**：Flutter 在 trace 下画 `accent alpha 0.10` 填充到基线（`spectrum_display.dart:646-649`）；桌面仅折线无填充（`spectrum_display.cpp:585`）。
- **抗锯齿**：Flutter 显式 `isAntiAlias=true`（`spectrum_display.dart:603,656`）；桌面 QPainter 默认非 AA（`spectrum_display.cpp:533` 未调用 `setRenderHint`）。桌面折线会有锯齿。
- **瀑布速度**：桌面 1x/2x/4x 可选（`spectrum_display.cpp:406-409`）；Flutter 每帧推一行，无速度档。
- **历史深度**：桌面 ringDepth=`kWaterfallHistoryLines=256`（`tokens.h:390`）；Flutter `_h=height*dpr clamp(1,256)`（`spectrum_display.dart:356`）——接近。
- **瀑布边框**：桌面画 `cardEdge` 1px 边框（`spectrum_display.cpp:639-640`）；Flutter 无边框。
- **修复建议**：桌面 `QPainter::setRenderHint(QPainter::Antialiasing)`；Flutter 瀑布补速度档；其余按 UI 收敛统一。

---

## 7. 用户点名三缺陷的根因汇总

| 用户缺陷 | 根因（file:line） | 严重度 |
|---|---|---|
| 频谱与瀑布对不齐 | ① 桌面 trace bin 中心 vs 瀑布像素边，zoom 16 下偏 4px（`spectrum_display.cpp:566` vs `631-638`）；② Flutter trace 重采样 500 点+三点平滑 vs 瀑布 3000 设备列（`spectrum_display.dart:414-422` vs `175-183`）；③ Flutter BW 带只画 trace 不画瀑布（`spectrum_display.dart:630-639` vs `896-900`） | 高 |
| 频谱占位过大 | Flutter flex:5 trace > flex:4 瀑布，且不可调（`spectrum_display.dart:264,328`）；桌面 1:1 可拖 divider（`spectrum_display.cpp:49,859-869`） | 高 |
| 时间轴/余晖/标记不一致 | 时间轴：标签小数位恒 3 位 vs 自适应（`spectrum_display.cpp:726` vs `spectrum_display.dart:843`）、刻度方向相反、桌面缺中心高亮；余晖：包络 vs 分层算法、decay/alpha 常数不同；标记：桌面有名字/自由拖/0.5% 步长 vs Flutter 无名字/snap/粗步长 | 中 |

---

## 8. Wave2 修复优先级建议

1. **P0（对齐，两端都修）**：差异 1.1、1.2、1.3、2.1——直接对应用户三缺陷。
2. **P1（时间轴统一）**：差异 3.2、3.3（标签小数位 + 中心高亮）。
3. **P1（余晖行为）**：差异 4.2（Flutter 切 off 清空 history）。
4. **P2（能力对齐）**：差异 5.1（Flutter 标记加名字）、6.1（Flutter dB 自动量程）、6.5（桌面空态）。
5. **P3（UI 收敛，归 W2c）**：差异 6.3、6.4、6.6。

---

## 9. 自检记录

- [x] 桌面 `spectrum_widget.{h,cpp}` 441 行逐行读完。
- [x] 桌面 `spectrum_display.{h,cpp}` 1018 行逐行读完。
- [x] Flutter `spectrum_display.dart` 914 行逐行读完。
- [x] 调用方核实：桌面 `main_window.cpp:1701/2860` 喂帧与 VFO；Flutter `spectrum_page.dart:252/276` 喂帧与 widget 驱动。
- [x] token 取值核实：`tokens.h`（C++）与 `tokens.dart`（Flutter）关键常量已比对（见各差异条目）。
- [x] 未写任何代码、未 git、未调 API。
- [x] 所有差异带 `file:line`；推断处已标注（如 Figma 设计意图为推断）。
- [ ] 未读 `main_window.cpp` 全部 2860+ 行（仅 grep 喂帧点）——若 Wave2 需要桌面 VFO 状态机细节，需补读 `main_window.cpp:1985-1996,2860` 附近。
- [ ] 未读 `mobile/lib/dsp/fft_processor.dart` 全部（仅 grep SpectrumFrame 字段）——Flutter 帧 db 来源是否已带 noiseFloor 字段待 Wave2 核实。

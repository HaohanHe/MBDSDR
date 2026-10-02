# Phase12-E：SDR++ UI/交互子系统研习笔记

> 上游：`repos/sdrpp`（GPLv3）。本笔记**只学机制/交互范式**，所有代码片段仅作机制引注（标注 GPLv3 出处），落地实现为 MBDSDR 自有代码（clean-room），不逐字复制。
> 研习范围：`core/src/gui/{main_window,tuner,theme_manager,style,smgui}.{cpp,h}` + `widgets/{waterfall,frequency_select}.{cpp,h}`。
> 对照基线：MBDSDR `cpp/src/ui/{spectrum_display,spectrum_tune.h,main_window,shortcuts_catalog.h}` + `cpp/src/core/tokens.h`。

---

## ① 上游真实做法（file:line + 短片段）

### 1.1 调谐主循环：MainWindow::draw() 里的"事件 flag 收集器"
SDR++ 不在控件内部直接调谐，而是由瀑布控件置位 flag，每帧在 `MainWindow::draw()` 统一消费：

- VFO 被拖动 → 中心调谐模式下连带源重调
  `core/src/gui/main_window.cpp:270-280`（GPLv3）：
  ```cpp
  if (vfo->centerOffsetChanged) {
      if (tuningMode == tuner::TUNER_MODE_CENTER)
          tuner::tune(tuner::TUNER_MODE_CENTER, gui::waterfall.selectedVFO,
                      gui::waterfall.getCenterFrequency() + vfo->generalOffset);
      gui::freqSelect.setFrequency(...);
      core::configManager.conf["vfoOffsets"][selectedVFO] = vfo->generalOffset; // 每帧持久化
  }
  ```
- 数字频率输入框改频 → `main_window.cpp:293-307`：`tuner::tune(tuningMode, selectedVFO, freqSelect.frequency)`。
- 拖频率刻度带（整个频谱平移）→ `main_window.cpp:310-322`：`centerFreqMoved` flag → `sourceManager.tune(centerFreq)`。

### 1.2 键盘：箭头键 snap 步进 + 修饰键倍率
`main_window.cpp:556-567`（GPLv3）：
```cpp
if (ImGui::IsKeyPressed(ImGuiKey_LeftArrow) && !gui::freqSelect.digitHovered) {
    double nfreq = centerFreq + vfo->generalOffset - vfo->snapInterval;
    nfreq = roundl(nfreq / vfo->snapInterval) * vfo->snapInterval; // snap 到 snapInterval 网格
    tuner::tune(tuningMode, selectedVFO, nfreq);
}
```
`!freqSelect.digitHovered` 是关键守卫：鼠标正悬停在数字频率框某一位上时，左右键归该位编辑（见 `widgets/frequency_select.cpp:198` 置 `digitHovered`、`:137` 滚轮调当前位），不调谐 VFO。

### 1.3 滚轮：snapInterval 步进 + Shift×10 / Alt×0.1
`main_window.cpp:579-597`（GPLv3）：
```cpp
int wheel = ImGui::GetIO().MouseWheel;
if (wheel != 0 && (mouseInFFT || mouseInWaterfall)) {
    double interval = vfo->snapInterval;
    if (ImGui::IsKeyDown(ImGuiKey_LeftShift))      interval *= 10.0;  // 粗
    else if (ImGui::IsKeyDown(ImGuiKey_LeftAlt))  interval *= 0.1;  // 细
    nfreq = centerFreq + vfo->generalOffset + interval * wheel;
    nfreq = roundl(nfreq / interval) * interval;
    tuner::tune(tuningMode, selectedVFO, nfreq);
}
```
无 VFO 时滚轮改为平移视图：`nfreq = centerFreq - viewBandwidth*wheel/20`（同段 599 行）。

### 1.4 tuner：两种调谐状态机（保持 VFO 在捕获带内）
`core/src/gui/tuner.cpp:22-109` `normalTuning()` 的四象限决策（GPLv3，机制）：
1. VFO 仍在视口内 → 只 `vfoManager.setCenterOffset`，**不重调源**（51-54 行）；
2. VFO 越过 SDR 捕获带边缘 → 反向设 offset + `sourceManager.tune()` 把 LO 拖回带内（57-74 行）；
3. VFO 在带内但出视口 → 平移视口，并留 `viewBW/10` 边距（77-108 行）。
`centerTuning()`（8-20 行）则把 VFO offset 清零、源频率即 dial 频率；`tuner::tune()`（117-135 行）按 5 种模式（CENTER/NORMAL/LOWER_HALF/UPPER_HALF/IQ_ONLY）分发。

### 1.5 瀑布控件：鼠标命中分层与拖拽（真正的交互核心）
`widgets/waterfall.cpp:249-556` `processInputs()`，按优先级判定：
- **VFO 边框命中**（带宽拖拽热区，2px×uiScale 握柄，`:1346-1350` 生成 `lbwSel/rbwSel` 矩形）→ `vfoBorderSelect`；拖拽时 `:360-372`：
  ```cpp
  double dist = (ref==REF_CENTER) ? fabsf(mouse.x-lineMin.x) : (mouse.x-lineMin.x);
  if (ref==REF_UPPER) dist = -dist;
  double hzDist = dist * (viewBandwidth/dataWidth);
  if (ref==REF_CENTER) hzDist *= 2.0;            // 中心参考：双边各动一半
  hzDist = clamp(hzDist, minBandwidth, maxBandwidth);
  vfo->setBandwidth(hzDist);
  vfo->onUserChangedBandwidth.emit(hzDist);
  ```
- **VFO 体命中** → 仅选中（`:336-341`），不立刻拖；
- **频率刻度带命中** → `freqScaleSelect`，拖动即平移视口（`:375-408`），视口撞到捕获带边缘时 `centerFreq += 溢出量; centerFreqMoved = true`（回 1.1 由 MainWindow 重调 LO）；
- **频谱/瀑布空白处点击** → 直接把选中 VFO snap 到该像素频率（`:466-476`）：
  ```cpp
  double off = (((refCenter/(dataWidth/2.0))-1.0) * (viewBandwidth/2.0)) + viewOffset;
  off += centerFreq;
  off = (round(off/snapInterval)*snapInterval) - centerFreq; // snap
  selVfo->setOffset(off);
  ```
  触发条件由 `VFOMoveSingleClick` 决定：单击即跳（中心调谐模式，`main_window.cpp:207`）或按住拖动（普通模式）。
- **滚轮在刻度带上** → 视口平移 `viewBandwidth/20` 每格（`:411-435`），与 1.3 的"滚轮调谐"按区域分流；
- **PgUp/PgDn** → 按频率偏移最近邻循环切换 VFO（`:512-555`）。

### 1.6 VFO 模型与高亮绘制
`widgets/waterfall.h:33-80`：每个 VFO 有 `generalOffset/centerOffset/lowerOffset/upperOffset/bandwidth/snapInterval(默认5000)`、`reference∈{REF_LOWER,REF_CENTER,REF_UPPER}`、`leftClamped/rightClamped` 标志；`setOffset/setCenterOffset/setBandwidth`（`:1219-1282`）按 reference 模式互推三个偏移量。
高亮（`waterfall.cpp:1356-1360`，GPLv3）：
```cpp
window->DrawList->AddRectFilled(rectMin, rectMax, color);          // 半透明填充（默认白 50α）
window->DrawList->AddLine(lineMin, lineMax,
    selected ? IM_COL32(255,0,0,255) : IM_COL32(255,255,0,255), style::uiScale); // 选中红/未选黄
```
瀑布上的 VFO 框同色绘制（`:220-226`）。`updateDrawingVars`（`:1302-1354`）把 Hz→像素，越界时置 `leftClamped/rightClamped` 并禁掉对应边的带宽握柄。

### 1.7 主题/皮肤体系
`theme_manager.cpp:40-116`：每个主题一个 JSON，键名是 ImGuiCol 名（映射表 `:189-243` 共 ~50 项），值为 `#RRGGBBAA` 9 位 hex；另有三个特例键 `WaterfallBackground/ClearColor/FFTHoldColor`。`applyTheme`（`:118-170`）先 `StyleColorsDark()` 重置，再把 JSON 逐项写回 `ImGui::GetStyle().Colors`。字体体系 `style.cpp:48-50`：base 16px / big 45px（频率数字专用字形表 `.9`）/ huge 128px，`uiScale` 桌面 1.0、Android 3.0。

### 1.8 smgui 的真实身份（纠正提示词假设）
`smgui.cpp` **不是快捷键系统**，而是 SDR++ Server 远程 UI 的"绘制录放"层：本地把每帧 widget 调用录成 `DrawStep` 流（`:62-203`），远端回放。快捷键实际是散落在 main_window 里的硬编码：`ImGuiKey_Menu`（菜单开关 `:336`）、`ImGuiKey_End`（播放/停止 `:350/357`）、`Left/Right`（1.2）、`PgUp/PgDn`（1.5）、`Escape`（关 credits `:432`），以及 `displaymenu::checkKeybinds()`（`:470`）。

---

## ② MBDSDR 现状（file:line）

| 机制 | MBDSDR 位置 | 说明 |
|---|---|---|
| 点频谱调谐（press/release） | `cpp/src/ui/spectrum_display.cpp:887-896`（press 起 Grab::VfoBody/Tune）、`:986-1010`（release 结算） | 结算走纯函数 `ui::tuneSettleFreq`，`<3px` 视为单击跳频、否则拖动幂等（`cpp/src/ui/spectrum_tune.h:54-61`） |
| 滚轮 snap | `spectrum_display.cpp:1067-1077` | `snapFreqToStep(dial+±step, step)` 0 锚 snap（`spectrum_tune.h:66-69`） |
| 滚轮 Ctrl+光标缩放 | `spectrum_display.cpp:1055-1066` | 以光标频率为中心 ×1.2 变焦 |
| 带宽拖拽 | `spectrum_display.cpp:876-879`（边命中 `kBandEdgeHitTol`）、`:945-958` | `bandwidthAfterEdgeDrag`：半带宽=|中心−边|，钳 [100Hz,500kHz]（`tokens.h:512-513`） |
| VFO 高亮 | `spectrum_display.cpp:672-693` | 选中/未选双档：fill α 0.20/0.10、边 α 0.95/0.55、线宽 1.8/1.2px，外加中心线 + 名称标签（`tokens.h:503-513`） |
| 视图平移 | `spectrum_display.cpp:838-843`（刻度带拖）、`:845-848`（Shift+拖迹线）、`:1018-1025`（双击重定中心） | 瀑布为定高历史切片（`:364 waterfallSourceRect`），自动下滚，无手动纵滚 |
| 快捷键 | `shortcuts_catalog.h:36-46`（声明式目录）+ `main_window.cpp:2486-2583`（QShortcut 接线） | ←/→ 步进、Shift 细调、↑/↓ 带宽×2、PgUp/PgDn 步进档位、Ctrl+Tab/Ctrl+1..9 切 VFO、Space 静音、Ctrl+R 录制 |
| 调谐/L0 决策 | `main_window.cpp:2105-2113` | 全部走 `engine_->vfoSetOffset()`：引擎内部决定滑 channelizer offset 还是重调 LO |
| 主题 | `cpp/src/core/tokens.h`（编译期常量） | 无运行时 JSON 主题加载/导出 |

---

## ③ 用户视角五项核对表

| # | 用户视角 | SDR++ 做法 | MBDSDR 现状 | 判定 |
|---|---|---|---|---|
| 1 | 点频谱调谐 | 空白处按住即 snap 到像素频率（`waterfall.cpp:466-476`） | 单击跳频/拖动幂等结算，纯函数单测覆盖（`spectrum_display.cpp:991-1010`） | ✅ 已实现且真实，且比上游多了 click/drag 判别与确定性单测 |
| 2 | 滚轮 snap | snapInterval 网格 snap；Shift×10 / Alt×0.1 倍率级联（`main_window.cpp:585-596`） | 0 锚 step 网格 snap 有（`spectrum_tune.h:66-69`）；**滚轮无倍率修饰键**（细调只绑 Shift+方向键，`shortcuts_catalog.h:38`） | 🟡 已实现但缺深度：滚轮无级联倍率 |
| 3 | 瀑布滚动 | 历史自动下滚；刻度带拖动/滚轮/方向键横平移视图（`waterfall.cpp:375-463`） | 自动下滚有；横平移=刻度带拖+Shift 拖迹线+双击居中；**滚轮在频谱区=调谐，不平移视图** | ✅ 已实现且真实（分流模型不同，不缺） |
| 4 | VFO 高亮 | 选中红线/未选黄线 + 半透明填充（`waterfall.cpp:1357-1359`） | 选中/未选双档 α+线宽 + 中心刻度 + 名称标签（`spectrum_display.cpp:676-692`） | ✅ 已实现且真实，视觉体系超过上游 |
| 5 | 带宽拖拽 | 2px 握柄热区，双边拖，钳 [minBw,maxBw]，支持 REF_LOWER/UPPER 单边参考（`waterfall.cpp:312-372`） | 边命中容差拖边，半带宽钳 [100,500k]（`spectrum_display.cpp:945-958`） | ✅ 已实现且真实；仅中心对称模型，无单边参考模式（设计选择） |

---

## ④ 差距判定片段（供 Wave2 整合）

### G1 滚轮步进倍率级联（Shift×10 粗 / Alt×0.1 细）
- 上游：`core/src/gui/main_window.cpp:585-593`（GPLv3）
- MBDSDR：`cpp/src/ui/spectrum_display.cpp:1067-1077`（wheel 单档位）；细调仅键盘 `shortcuts_catalog.h:54-57`
- 判定：**已实现但缺深度**（snap 网格有，滚轮无级联倍率）
- 落地价值：中。鼠标不挪位即可跨波段快速调谐，是 SDR 用户高频肌肉记忆。
- 云内可确定性验证：✅ 纯函数 `wheelTierFreq(cur, step, dir, tier∈{×0.1,×1,×10})` 加进 `spectrum_tune.h`，沿用 `test_spectrum_tune.cpp` 模式，零 Qt/零硬件。

### G2 滚轮在刻度带/频谱区的视图平移分流
- 上游：`core/src/gui/widgets/waterfall.cpp:411-435`（wheel on strip → 视口平移 viewBw/20）
- MBDSDR：`cpp/src/ui/spectrum_display.cpp:1049-1079`（wheel 一律调谐，Ctrl=缩放，无平移档）
- 判定：**未实现**（平移靠拖动刻度带，滚轮不参与）
- 落地价值：低-中。双手不离鼠标浏览大跨度频谱。
- 云内可确定性验证：✅ 纯函数 `panOffsetAfterWheel(cur, wheelTicks, viewBw)` + 区域判定，同 G1 模式单测。

### G3 长距离调谐后的视图自动跟随（VFO 不跑出屏幕）
- 上游：`core/src/gui/tuner.cpp:77-108`（VFO 出视口时视口自动平移，留 viewBW/10 边距；出捕获带才重调 LO）
- MBDSDR：LO 重调在引擎层已有（`main_window.cpp:2105-2113` vfoSetOffset 内部决策）；但 `viewCenterHz_` 只在 Pan/双击/选 VFO 时变化（`spectrum_display.cpp:922-928,1018-1025`），**纯调谐（点频/滚轮/方向键）不自动带动视图**
- 判定：**部分未实现**（引擎跟随有，画布自动跟随缺）
- 落地价值：中。点远处谱峰后 VFO 应自动移入视口居中，否则用户拖完还得自己找。
- 云内可确定性验证：✅ 纯函数 `followCenterAfterTune(curViewCenter, vfoFreq, span, margin=10%)` 单测；UI 层接 `vfoOffsetChanged` 信号即可。

### G4 运行时可换肤主题体系
- 上游：`core/src/gui/theme_manager.cpp:40-170`（JSON 主题目录 → ImGuiCol 映射 + 3 特例色）
- MBDSDR：`cpp/src/core/tokens.h` 全部编译期常量；无主题加载/切换/导出
- 判定：**未实现**
- 落地价值：低（当前阶段优先级靠后；MBDSDR 已有统一 tokens 体系，后续要做也是"tokens 外置为 JSON"而非照抄 ImGuiCol 映射）
- 云内可确定性验证：🟡 弱（需 Qt 渲染测试；可先做"tokens JSON 加载→结构校验"纯逻辑单测）

### G5 快捷键：声明式目录 + 对话框（MBDSDR 反超上游，正向记录）
- 上游：快捷键散点硬编码于 `main_window.cpp:336/350/556-567` 等
- MBDSDR：`shortcuts_catalog.h:36-46` 单一目录同时驱动对话框与接线，`main_window.cpp:2486-2583`
- 判定：**MBDSDR 已实现且超过上游**——不补，仅记录。

---

## 落地建议（排序）
1. **G1（滚轮倍率）+ G3（视图跟随）** 优先：纯函数层即可完成 + 单测，贴合用户视角第 2/3 项，ctest 基线（95/95）增量零风险。
2. G2 顺带：wheel 平移档可与 G1 共用修饰键决策表。
3. G4 暂缓：记为 backlog，待视觉定制需求出现时再把 tokens.h 外置为 JSON。
4. 红线遵守：全部自有代码重写，不复制上游片段；GPL 出处仅留于本笔记。

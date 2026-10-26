# Phase83 · UI 设计推进蓝图 + 设置前后端治理

> 日期：2026-10-26 ｜ 状态：本批已落地 P0 修复（见 §5），路线图待逐批推进
> 依据材料：docs/learn/phase82/research/02-sdrpp-gqrx-ui.md（SDR++/gqrx UI 对标）、
> docs/learn/carwith_ui_design_teardown.md（CarWith 设计语言拆解，git afcb93c 可取）、
> mbdsdr-ui-mockup/index.html（产品视觉愿景）、desktop/tokens.py + cpp/src/core/tokens.h
> （CarWith Figma 令牌已提取固化）、本次 86 键全量孤儿审计。
>
> 备注：tokens.h 提到的原始 Figma 画板目录 `docs/design_ref/carwith_figma/`（画板
> 284_75 / 284_410 / 284_456 / 284_566）**已不在磁盘与 git 历史中**。设计令牌已
> 完整收敛进 tokens，本文以 tokens + 拆解文档 + mockup 为准；若原始画板另有备份，
> 后续可再校对细节。

---

## 0. 第一性原理：布局是活的（用户长期要求，最高优先级）

**"它是可以活动的、它是动态的、它可以被调整的。"** ——这条是一切布局决策的前提，
优先级高于任何"整齐"“固定”审美主张。布局不是施工图，是用户手里的**工作面**。

### 0.1 五个动词（一切布局交互归入这五个）

| 动词 | 含义 | 现状 | 缺口 |
|------|------|------|------|
| **拖** | 每条分界线都能抓住调宽窄/高低 | 三栏水平分界 ✅（`ui/splitterSizes`+双击重置）、谱/瀑布分界 ✅（`view/specFraction`） | 左栏组间垂直分界 ❌、右栏组间垂直分界 ❌、画布右缘竖滑条 ❌ |
| **折** | 每个面板组可折叠成一条标题 | 面板显隐（设置+右栏 ✕）✅、focus 模式 ✅ | 组内点击标题折叠 ❌、折叠态持久化 ❌ |
| **搬** | 页签可挪位、可浮出 | 右栏 ✕ 关闭 ✅ | 页签拖动重排 ❌（`QTabBar::setMovable`）、页签浮出为独立窗 ❌、组跨栏搬运 ❌（远景） |
| **排** | 顺序即使用频率，拖拽定序 | 显隐列表 ✅ | 页签顺序 / 左栏组顺序持久化 ❌ |
| **存** | 一切尺寸开合落盘 + 命名快照 | splitterSizes / specFraction / hiddenLists / focusMode ✅ | 命名布局快照（专注/分析/全景/自定义）❌ |

### 0.2 双端同一模型（产品命题在布局上的体现）

`cpp/src/ui/workspace_layout.h` 的 **WorkspaceLayout** 已是布局唯一真源（JSON 可序列化，
`workspaceSpecToJson/fromJson` 已有）：AI 操作员经 A2UI（createSurface/updateComponents/
deleteSurface）改布局的通道**已经打通**。缺的是**人手直接操纵**的入口——人的拖/折/搬/排
也必须写进同一个 WorkspaceLayout，而不是散落的旁路状态。

这就是"AI 操作员、人是指挥官"在布局上的形态：**指挥官与操作员共用一张工作台**。
布局快照（命名 JSON 存档）对模型层几乎是免费的——序列化已就绪。

### 0.3 约束（动态不等于混乱）

- 拖拽有下限：`kMinPanel = 120px`（已有），任何 pane 不可拖没。
- 默认布局提供肌肉记忆：三栏 280/800/280 起步（已有），高频控件默认位置稳定。
- 收窄窗口走 compact 重规划（已有：kCompactBreakpoint=1100）。
- 一切可活动元素的变化必须落盘并可一键恢复默认（双击分界线重置的语义推广到所有）。

---

## 1. 什么是"真正好的 SDR UI"——从三份材料提炼

### 1.1 SDR++ 给的答案（仪器感的来源）

1. **三色分工是硬语义**：trace 青（信号）/ max-hold 黄（历史）/ 瀑布底纯黑（能量），
   一眼分清"当下/过去/能量"。色相不是装饰，是信息编码。
2. **瀑布是第一视觉主体**（占 70%+），LUT 2048 级以上才无色带；分界线可拖。
3. **显示参数三档收纳**：最高频（Zoom/Max/Min）→ 画布右缘竖滑条常驻；
   中频（FFT/窗/色图）→ 左栏折叠区；低频（UI 缩放）→ 同区底部。顶栏只留 6 件。
4. **调谐是"机手感"**：大频率逐位点按/滚轮/清位/粘贴，一个动作一个键。
5. **模块即面板**：启用开关在面板标题上，顺序可拖，不藏设置里。

### 1.2 CarWith/MIUIX 给的答案（质感与克制）

1. **纯黑 OLED 底 + 三档灰抬层级**（#000 → #181818 → #242424 → #2C2C2C），
   不靠描边分层。
2. **文字只用黑白 × alpha**（100/80/60/40/30%），不另设色板。
3. **按压 = 12% 黑叠层**，hover = 6% 叠层，无描边变色。
4. **圆角体系**：胶囊 36 / 卡片 9 / 浮窗 12-24，4dp 栅格间距。
5. **动效两级**：反馈 150ms OutCubic、转场 300ms，退场比入场快一倍。
6. **平行视界**：主区铺满 + 副区并排可拖（16:10），副窗可折叠为窄条。
7. **状态安静**：status 是信息不是贴纸（低饱和、小字、角落 chip）。

### 1.3 mockup（mbdsdr-ui-mockup）给出的产品答案

三栏"平行视界"世界：左=天空/轨道沉浸视图、中=世界地图+悬浮命令栏（AI 入口）、
右=当前任务（AI 对话/解码）；底部 Dock 放模式砖。**核心气质：以世界/任务为中心，
而不是以控件为中心**——控件退居边缘，数据占满视野。这是"不费劲"的终极方向。

---

## 2. 现状诊断——"费劲"的五个结构性病因

| # | 病因 | 证据 | 后果 |
|---|------|------|------|
| 0 | **布局刚性（根因）**：尺寸/顺序/开合不可随手调 | §0 缺口表：组折叠/页签重排/浮出/命名快照全缺 | 用户被迫适应界面，而不是界面长成用户的样子 |
| 1 | **右栏 14 平铺页签**，280px 里标题被截断 | main_window.cpp:1355-1368 + 14 处 addTab | 像工具箱清单；找面板靠记忆；一页签一面板的"孤儿感" |
| 2 | **左栏 GroupBox 平铺 12+ 组**，低频参数无收纳 | main_window.cpp:317-1162 | 滚动长；对比 gqrx"主面板 5-8 行 + 弹窗收纳"差距大 |
| 3 | **设置项前后端失联**（孤儿数据） | 本次审计 6 处硬孤儿 + 60 裸字面量键 + 4 处裸 QSettings | 用户改了不生效/生效了不能改；三套存储三套键名 |
| 4 | **视觉层级未拉开**（trace/hold/瀑布三色分工未完成率低） | phase82 ★1-★3 已修，但专注模式/页签/收纳未动 | 屏幕上每样东西都在"喊"，真正要读的数据反而不突出 |

---

## 3. 设置前后端治理契约（今后必须遵守）

**每个设置项必须五件套成对存在，缺一即孤儿：**

```
① 存储键     —— tokens::kSettingsKey* 常量（禁止裸字面量）
② 读  路径   —— 启动恢复（restoreUiState / ctor）
③ 写  路径   —— UI 变更 handler 或 saveUiState
④ UI 控件    —— 设置对话框 或 主窗口可见控件（二选一，双入口必须同键同 apply 路径）
⑤ 生效路径   —— 真正调用 engine_/canvas_/agent_ 的 setter（不是只落盘）
```

**配套纪律（已在 app_settings.h 注释中，本次继续执行）：**
- 一切设置读写走 `mbdsdr::core::AppSettings`；裸 `QSettings s;` 视为 bug
  （现存违规 4 处：activity_log.cpp:18,45 / frequency_calibrator.cpp:347,352 /
  control_http_server.cpp:102 / main_window.cpp:6574，随批清理）。
- 同一键只允许一种拼写：`tokens::kSettingsKey*` 常量。
  现存 7 键 17 处"常量与字面量混用"（rx/dbMin、rx/dbMax、view/zoomFactor、
  rx/fftSize、view/persistMode、ui/hiddenRightTabs、view/wfColormapFile），
  下一批统一。
- 三套存储（QSettings / ai_config.json / desktop_settings.json）并存是历史事实；
  **C++ 桌面为准**。Python desktop/ 的 12 键零交集，属独立遗留 UI，不迁移不扩散，
  新键只进 C++ 侧。

**键总账**：86 个 QSettings 键 + AiConfig 8 字段。本批后：
- OK 闭环 85 键（+3 修复归队）；真正有意无 UI 的仅剩 `control/httpPort`
  （运维覆盖键，tokens.h:1317-1326 已文档化，保持现状）。

---

## 4. UI 路线图（活动布局 → 仪表盘 → 远景）

### 阶段 A · 活动布局落地（第一优先级，§0 的动词全部变成现实）
- **A1 左栏组可折叠 + 垂直可拖**：每个 QGroupBox 标题点击折叠/展开（折叠态+
  高度持久化），组间垂直 QSplitter——左栏从固定清单变成可折叠的积木栈。
- **A2 右栏页签可拖动重排 + 顺序落盘**：`rightTabs_->setMovable(true)` +
  tabOrder 持久化；顺序即使用频率。
- **A3 页签浮出**：双击/拖出页签 → 浮动工具窗（Qt::Tool），关闭归位；
  位置尺寸落盘。让"搬"成立。
- **A4 命名布局快照**：复用 WorkspaceLayout JSON 序列化，存档"专注/分析/全景/
  我的布局N"，一键切换；改布局时"另存为"入口常驻。
- **A5 收纳随行**：低频参数（SPY/GNSS/网络音频/录制模板）进"高级…"弹窗，
  每组 ≤5 行；收纳动作本身也是"折"的一种。
- **A6 滚轮 Shift/Alt 分档**（★10）：Shift=10×、Alt=0.1× snap + 瞬时 tooltip。
- **A7 设置键收口**：7 处常量-字面量混用统一；4 处裸 QSettings 改 AppSettings。

### 阶段 B · 仪表盘（观感对齐 SDR++，与活动布局并行）
- **B1 右缘竖滑条（Zoom/Max/Min）**：画布右缘 60px 竖排常驻，顶栏再减 3 件
  ——SDR++ 招牌手势，也是"拖"的延伸。
- **B2 书签渲染进频谱 overlay**（★8）：多层级标签 + 斜线 + 虚线竖线。
- **B3 色图 JSON 资源化**（★4）：`res/colormaps/*.json`（name/author/map），
  运行时扫描；14 张 SDR++ 色图可移植。
- **B4 网格层离屏缓存**（★9）：overlay pixmap，视窗/范围变化才重绘。
- **B5 AI 悬浮命令栏**（自 mockup 抽取的唯一专业元素）：command-palette 式
  入口，`Ctrl+K` 呼出；AI 改布局的消息也可从这里发。

### 阶段 C · 远景（mockup 世界化布局——**降级为参考，不立项**）
专业测量工具的主路线是仪表台 + 活动布局；mockup 的车机式"世界铺满 + 底部大
Dock"不进主路线。从 mockup 唯一抽取的专业元素——悬浮 AI 命令栏——已进 B5。
若未来做外场/展示场景，可作为可选布局快照（命名布局快照机制天然支持一键切换）。

**优先级排序原则**：§0 活动布局是第一性原理（布局的一切决策服从"可活动/动态/可调"），
然后"改的每个设置都生效"（治理已还清），再"一眼看清信号"（仪表盘）。
每批都要带测试，不欠账。

---

## 5. 本批已落地（2026-10-26，孤儿对齐批次）

| 修复 | 类型 | 改动 |
|------|------|------|
| 设置对话框「首选采样率」死控件 | 前端孤儿 | itemData 带 Hz，load 吸附/save 回写 `rx/sampleRate`，新增 `sampleRateHz()`；main_window apply 链走 `onSetSampleRate` 同路径，左栏 combo 吸附最近支持值 |
| `rx/frontendDecimation` 写后即丢 | 前端孤儿 | restoreUiState 按 objectName 找回 decimCombo，回填+单次派发引擎（垃圾值诚实回落"关"） |
| `stationAlt` 落盘零消费 | 前端孤儿 | WorldView::setStation 支持海拔（NaN=未知不造假 0m）；本站 chip 显示"坐标 · 海拔"；GNSS 路径接 `fix.altitudeMsl`；设置/GNSS/重载四条路径全通 |
| `view/wfOverlay` 只读开关 | 后端孤儿 | 画布右键菜单新增「SDR++ 叠加显示（瀑布铺底）」可勾选项；`SpectrumDisplay::setWfOverlay` 实时翻转+落盘 |
| `rec/max_seg_s` 死 API | 后端孤儿 | 录制组新增「分段时长上限」spinbox（30–3600s），接 `setRecMaxSegmentSeconds`（引擎 ctor 读/setter 写，闭环） |
| `control/httpPort` 无 UI | 有意设计 | 保持运维覆盖键语义（env > QSettings > 50732），不加 UI |

**验证**：新增 `tests/test_settings_orphans.cpp`（5 用例：采样率往返/吸附、
wfOverlay 落盘、分段 API 活性、抽取重启恢复）；ctest `settings_orphans` + `ui_audit`
全绿；UI 相关子集 18 项仅 `spectrum_display`/`font_cjk_guard` 两项已知环境红
（与本批无关，10-26 早前已存在）。

---

## 6. 开放问题（待你拍板）

1. **A 批动工顺序**：A1 左栏折叠/垂直可拖 与 A2 页签重排先行，A3 浮出随后，
   A4 布局快照压轴——还是你有更痛的先做？
2. **Figma 原始画板**是否还有备份可校对？（令牌已提取，但 1920x1200 画板的
   布局细节只有拆解文档与 mockup 佐证。）
3. **Python desktop/ 遗留 UI** 的处置：冻结（推荐）/ 删除 / 继续双轨？

已拍板（不再议）：右栏不搞固定三分类页签，走"模块抽屉 + 活动布局"；
阶段 C 世界化布局降级为远景参考，AI 命令栏进 B5。

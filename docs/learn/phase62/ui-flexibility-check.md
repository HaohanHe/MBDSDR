# Phase62 · UI 弹性化自查（窄窗 / 全屏 offscreen 快照）

- 仓库 HEAD：`d6a1a22`；全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`），未操控 GUI。
- 快照目标：`cpp/tests/ui_screenshot_narrow.cpp` → `ui_shot_narrow`（本轮把窗口尺寸参数化为
  `MBD_W` / `MBD_H` 环境变量，缺省仍为原来的 820×640，向后兼容）。

## 快照（真实 offscreen PNG）

| 档 | 请求尺寸 | 实际抓帧 | 路径 |
|---|---|---|---|
| 窄窗 | MBD_W=640 | **960×640**（窗口最小宽 `tokens::kMainMinW=960` 钳制，见下） | `docs/learn/phase62/ui-shot-640.png` |
| 全屏 | MBD_W=1920 | 1920×1080 | `docs/learn/phase62/ui-shot-1920.png` |

绝对路径：

- `/home/user/Doubao/chats/38438160041798146/MBDSDR/docs/learn/phase62/ui-shot-640.png`
- `/home/user/Doubao/chats/38438160041798146/MBDSDR/docs/learn/phase62/ui-shot-1920.png`

运行时实测（日志）：`scaleFactor=1.000`；splitter 三栏宽度：
- 960 窗：`140 / 672 / 140`
- 1920 窗：`140 / 1632 / 140`

## 逐面板核查结论

### 640 档（实际 960×640）

| 面板 | 结论 |
|---|---|
| 顶栏（MBDSDR/无信号源/控制HTTP/UTC/专注/校准/?/关于/齿轮） | 无叠字、无裁切，贴边正常 |
| 左栏 · 源与连接 | sourceBanner / 本地 RTL-SDR 正常；**RSSI 仪表最大值标签“高”被左栏右缘水平裁切**（viewport 宽不足，横向滚动条为 AlwaysOff） |
| 左栏 · 设备信息 | “调谐范围 未知 / 采样率范围 未知 / 来源”正常；**“设备 RTL-SDR”的取值在栏右缘被裁成“RTL-SD”** |
| 左栏 · SpyServer 远程 | 开启 SpyServer 按钮 / 端口 / 未开启，无异常 |
| 中部 · 标签页（频谱/世界/气象/时空视图） | 正常 |
| 中部 · 控制排（FFT 2048/窗 Hann/平均 Off/Max/Rst/余晖关/清/标记/清标记） | 无叠字；游标排（游标A/B/清游标/dB -100/0/自动/门限 15 dB）紧凑但不重叠 |
| 中部 · 频谱 + 瀑布 | 网格 -0…-100 可读，游标 0.00 正常；瀑布小窗正常 |
| 中部 · 参数表格 | 表头 # / 频率(MHz) / 强度(dBFS) / 带宽(kHz) 无挤压，空表 |
| 右栏 · 解码（解码/CW ◀▶） | 标签条可读，解码区空，无裁切 |
| 底部 · 三步上手条 | ① 连接 RTL-SDR ② 调谐频率 ③ 选解调模式 + 去连接 / 不再提示，无叠字 |
| 状态栏 | MBDSDR C++ … NFM 未连接 VFO A 98.500 MHz … 静噪 OFF … S-meter 无设备，可读 |

### 1920 档（1920×1080）

| 面板 | 结论 |
|---|---|
| 顶栏 | 正常 |
| 左栏 · 源与连接 / RSSI | 同上：**“高”仍被栏右缘裁切**（栏宽两档都只有 140px） |
| 左栏 · 设备信息 | **“RTL-SDR”仍被裁成“RTL-SD”** |
| 左栏 · 频率 | **中心频率取值 “98.500 MHz” 被裁成 “98.500 M”**（spinbox 内容宽 > 可视宽，自带文字被裁） |
| 左栏 · 接收参数 | 采样率/增益/解调 NFM/带宽 12.5 kHz/抽取 关 正常；**“声道”行“强制单声”按钮右半被栏右缘裁切** |
| 中部 · 控制排 | 全部控件一行排开无叠字、无贴边 |
| 中部 · 频谱 + 瀑布 | 网格与游标正常；瀑布区大片留空（无信号），非缺陷 |
| 中部 · 参数表格 | 表头列无挤压 |
| 右栏 · 解码 | 标签条正常，区空 |
| 状态栏 | 可读 |

### 共因（新发现，本轮不改布局语义，如实记录）

两档窗口宽度下左右栏 splitter 宽度都被钉在 **140px**（1920 窗增量全部被中部吃掉）。
走查定位：`main_window.cpp` 构造期 `restoreUiState()` → `setFocusMode(false)` 走 else 分支
以**构造当时**的 splitter 宽度 × `tokens::kRatioLeft(0.22)` 算出 ≈140，并对左右栏
`setMaximumWidth(140)`；此后窗口再拉大，`maximumWidth` 上限不解除，栏就不再随窗变宽。
左栏内容最小需求 ≈ margins(24) + 频率 spinbox 最小宽(`kFreqSpinMinW=140`) = 164 > 140，
故左栏出现上述 4 处水平裁切。修复方向（解除构造期 stale maximumWidth、或在首次 show/resize
时按 `kRatioLeft/Right` 重设）属布局语义变更，按本轮硬约束不动，留待后续阶段决策。

## 固定值 token 化清单

本轮代码走查（`setFixedWidth/Height`、裸字号、裸边距、固定列宽）仅发现 **1 处**未走 token 的硬编码：

- `cpp/src/ui/main_window.cpp:5618`（AI 摘要标注行）：裸 `font-size:9pt;`
  → 改用既有具名字号 token `tokens::kFontAuxPt`（语义即“status / hints / 小标注”，9.5pt）。
  视觉变化如实说明：9pt → 9.5pt，同一行内小标注，差 0.5pt，肉眼无感。

其余走查结果：
- 裸 `setFixedWidth/Height`、裸 `setMinimumWidth`（非 token）：无；
- 裸 `setColumnWidth` / 表格固定列宽：无；
- 裸 padding/margin 字面量：无（`main_window.cpp:4302` 的 padding/radius 已走 `kPadBanner` / `kRadiusSmall`）；
- 左右栏宽度本身已走 `kRatioLeft/kRatioRight` + `scaled()`，140px 钉死是逻辑问题而非新字面量，不入 token 清单。

## 改动文件清单

1. `cpp/tests/ui_screenshot_narrow.cpp`：窗口尺寸参数化（`MBD_W`/`MBD_H`，缺省 820×640 向后兼容）；
   抓帧前打印 `scaleFactor` 与 splitter 三栏实际宽度（诊断用，offscreen 专用）。
2. `cpp/src/ui/main_window.cpp:5618`：裸 `9pt` → `tokens::kFontAuxPt`。

未 git add / 未 commit / 未 push；未触碰 CMake；活动参数零硬编码；无预置 TLE/呼号。

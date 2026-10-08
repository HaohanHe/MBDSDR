# Phase63 · UI 几何裸数续查（cpp/src/ui/，只读）

- 仓库 HEAD：`1068d5d`；全程只读（git show / grep / Read），**零代码改动**，未 git add/commit/push。
- 基线：phase62 全量扫描（`docs/learn/phase62/ui-geometry-scan.md`，基线 HEAD `e0f9242`）。
  本轮 = 续查 `e0f9242..HEAD` 之间 `cpp/src/ui/` 的全部改动。
- 判定口径沿用前几轮：
  - **OK 不列**：走 `tokens::scaled()` 或具名 token（`tokens::k*`）；`width()/height()` 派生；颜色 rgba；
    零值布局语义（`setSpacing(0)`/`setContentsMargins(0,0,0,0)`）。
  - **列清单判定**：该 token 化 / 保留（与局部惯例一致）/ 留档（观察类）。

## 1. 本轮改动范围（git diff --stat e0f9242..HEAD -- cpp/src/ui/）

```
cpp/src/ui/data_text_panel.cpp |   2 +-   (290db98 tokenize)
cpp/src/ui/m17_panel.cpp       |   2 +-   (290db98 tokenize)
cpp/src/ui/main_window.cpp     | 349 +++  (290db98 tokenize 5 处 + 1068d5d 新 UI)
cpp/src/ui/main_window.h       |  45 +++  (1068d5d 新成员声明，纯声明无几何)
cpp/src/ui/pocsag_panel.cpp    |   2 +-   (290db98 tokenize)
cpp/src/ui/s_meter.cpp         |   2 +-   (290db98 tokenize)
```

- 提交：`290db98`（tokenize 收尾）+ `1068d5d`（orphan 三特性接线）。
- **无新文件加入 `cpp/src/ui/`**："峰值表面板"由并行会话加入一说，在当前 HEAD 无对应文件
  （ui/ 共 55 个文件与 phase62 基线一致；grep "peak" 命中的是 s_meter/elevation_plot/spectrum_display
  等既有峰值绘制代码，非新面板）。本轮扫既有改动区即可。

## 2. `290db98`：phase62 §2.2 观察族的 token 化收尾

该提交把 phase62 建议优先提升的 4 组行内 scaled 裸数全部具名化（grep tokens.h 核实定义在位）：

| 位置 | 改动 | 新 token（tokens.h） |
|---|---|---|
| main_window.cpp:163 | `resize(scaled(1280), scaled(800))` → `scaled(kInitWinW), scaled(kInitWinH)` | `kInitWinW=1280 / kInitWinH` |
| main_window.cpp:1919 | `splitter->setSizes({scaled(280), scaled(800), scaled(280)})` → `scaled(kSplitInitL/M/R)` | `kSplitInitL=280 / kSplitInitM / kSplitInitR` |
| data_text_panel.cpp:69 / m17_panel.cpp:89 / pocsag_panel.cpp:71 | `setMinimumSectionSize(scaled(36))` ×3 → `scaled(kTableMinSectionW)` | `kTableMinSectionW=36` |
| s_meter.cpp:47 | `sizeHint()` `QSize(scaled(220), scaled(kSMeterH))` → `QSize(scaled(kSMeterW), scaled(kSMeterH))` | `kSMeterW=220` |

**判定：4 组观察族已全部 token 化，phase62 §4 的收尾建议闭环。无遗留。**

## 3. `1068d5d`：新 UI 区几何裸数逐处判定

新 UI = 左轨"网络音频外送"组（main_window.cpp:446–522）、扫描页"活动扫描链"组（:1459–1508）、
录制库"导出原始 IQ 段"行（:1746–1754）+ 模态时长对话框（onRecLibExportIq）。

| # | 位置 | 写法 | 判定 |
|---|---|---|---|
| U1 | main_window.cpp:454 `gNetLay->setSpacing(tokens::kSpacingS)` | 具名 token **未走 scaled()**，值=4 | **保留**：与同轨兄弟卡片写法一致——:1304 `capBar`、:1352 `bmLay`、:1358 `scanBoxLay`、:1512 `bmBoxLay`、:1701 `recLay`、:1706 `wBoxLay`、:1718 `lBoxLay`、:1765 `aBoxLay` 全部是 `setSpacing(tokens::kSpacingS/M)` 未 scaled 形态。这是**左轨/右页卡片组的既定局部惯例**（与频谱/星座工具条那批 `scaled(kSpacingS)` 并列两套），新代码遵从局部惯例；4px 小间距，DPI 影响可忽略。留档，不改。 |
| U2 | main_window.cpp:1469 `linkLay->setSpacing(tokens::kSpacingS)` | 同上 | **保留**，同 U1（扫描页卡片组，与 :1358/:1512 同页同风格）。 |
| U3 | main_window.cpp:466–467 `netAudioHostEdit_->setMinimumWidth(scaled(64)) / setMaximumWidth(scaled(128))` | 行内 scaled 裸数，未具名 | **留档**：已走 scaled()（DPI 弹性正常），符合 phase62 §2.2 观察类口径，不构成违规。注释说明是 640px 裁切回归修复的 4pt 栅格上限（host 列右缘 ≤165px），有意为之的单点调参。若后续要收尾可提 `kNetHostEditMinW=64 / kNetHostEditMaxW=128`，优先级低。 |
| U4 | main_window.cpp:1750 `recLibIqBtn_->setMinimumHeight(scaled(tokens::kTouchMinDim))` | 全 token | **OK**，触控目标 ≥44 逻辑 px，与既有"清空"按钮同写法。 |
| U5 | onRecLibExportIq 模态 `QDialog + QFormLayout` | 无 margin/spacing/padding 字面量；secsSpin 范围 0.1–30.0 / step 0.5 是**功能参数非几何** | **OK**。 |
| U6 | QSS / 字号 / 边框 / padding | 本轮 diff **0 处新增** `setStyleSheet`，无 font-size/padding/margin/border 字面量 | **OK**。 |
| U7 | QTimer `setInterval(1000)` / `setInterval(50)`、`setRange(1,65535)`、`setValue(49100)` | 功能/时序参数 | 不属几何扫描范围，不列。 |

## 4. 扫描基线校准说明

phase62 §2.1 曾记录"setSpacing(<非零>)：全部走 scaled(kSpacing*)"。本轮逐处复核当前 main_window.cpp
发现：该结论只覆盖工具条/画布条一族（:957/:1133/:1135/:842/:944/:970/:4916/:4920 走 scaled）；
**左轨/右页 QGroupBox 卡片组一贯使用未 scaled 的具名 token**（见 U1 列举 9 处既有代码）。
这不是本轮新引入的偏差，而是两套并存的局部惯例；本轮新 UI 选择遵从了所在卡片族的惯例。
后续扫描基线建议按"工具条条族=必须 scaled；卡片组族=具名 token 即可"区分，避免误报。

## 5. 结论

- **需 token 化项：无新增。** 本轮改动区（6 文件）几何裸数仅 U3 一处行内 scaled 族，
  按既定观察类口径留档；U1/U2 为局部惯例保留。
- phase62 §4 列的 4 组收尾建议已由 `290db98` 全部完成。
- 本轮未引入新 Qt 模块（网络音频/扫描链落在既有 Network/Multimedia/Core 内），与 windeployqt 复核结论一致。
- 硬约束自查：零代码改动（仅新增本文件）；活动参数零硬编码（扫描链配置全部复用既有扫描面板控件读数，
  见 commit message 与 onScanLinkToggled）；无预置 TLE/呼号；全文无竞赛类字样；措辞中立。

# Phase37 Block1+2 (cpp) — UI 裸数审计表（QSS / 布局 / 尺寸）

> 范围：`cpp/src/ui/`（23 个 .cpp/.h，不含 `coastline_data.h` 数据数组）+ `cpp/src/core/tokens.h`。
> 方法：正则扫 `setStyleSheet` / `setFixed|Minimum|Maximum*` / `setSpacing` / `setContentsMargins` / `setGeometry` / `setPointSize*` / `font-weight` / `QFont::*` / 裸 `#hex` / 裸 `44`。
> 排除：tokens.h 定义自身；算法/物理常数（DSP、SGP4、ICAO、表格列索引、采样/缓存深度）。
> 红线：触控目标必须派生 `kTouchMinDim`（禁裸 44）；字重走 `kWeight*`；4pt 栅格。
> 原则：**不改变功能行为**——所有“替换/具名化”均为值相同的纯 token 化。

## 基线（编辑前）

- `build_ui`（Unix Makefiles, Qt 6.8.2, offscreen）ctest 注册 **120** 项。
- 84 passed / **35 Not Run**（本增量构建目录未编译其测试二进制，沿用基线）/ **1 真实失败**：
  `#89 agent — TestAgent::testToolParse: tools.size()==14, expected 13`（ai/ 工具表计数漂移，**非 UI 域，预先存在**）。
- 全部已编译的 UI 相关测试在基线即通过。

## 汇总

| 判定 | 数量 | 说明 |
|---|---|---|
| 替换（既有 token，值相同） | 13 | font-weight:600×6、QFont::DemiBold×1、border-radius:4px×1、裸 44×1、裸栅格 scaled(4/8/16)×4 |
| 具名化（新增 tokens.h 常量，值相同） | 24 | 面板/控件最小尺寸、内嵌面板高度、对话框尺寸 |
| 保留（注明理由） | 18+ | 算法/物理常数、本地已具名常量、off-grid 已 scaled 的画布绘制几何、0 边距（功能性） |

---

## A. 替换（改走既有 tokens.h 具名常量，值相同）

| 位置 file:line | 裸数 | 语义 | 应替换 token | 判定 |
|---|---|---|---|---|
| ui/main_window.cpp:1910 | `font-weight:600` | 状态栏监听项字重 | `kWeightSemi`(=600) | 替换 |
| ui/main_window.cpp:1912 | `font-weight:600` | 状态栏扫描项字重 | `kWeightSemi` | 替换 |
| ui/main_window.cpp:1914 | `font-weight:600` | 状态栏录制项字重 | `kWeightSemi` | 替换 |
| ui/main_window.cpp:3115 | `font-weight:600` | 扫描中状态字重 | `kWeightSemi` | 替换 |
| ui/main_window.cpp:3120 | `font-weight:600` | 命中状态字重 | `kWeightSemi` | 替换 |
| ui/main_window.cpp:3124 | `font-weight:600` | 已暂停状态字重 | `kWeightSemi` | 替换 |
| ui/calibration_dialog.cpp:372 | `QFont::DemiBold` | 频率校准 hero 数字字重（Qt6 DemiBold==600） | `kWeightSemi` | 替换（`static_cast<QFont::Weight>`） |
| ui/main_window.cpp:4235 | `border-radius:4px` | 来源横幅圆角 | `kRadiusSmall`(=4) | 替换 |
| ui/vor_panel.cpp:20 | `kReadoutH = 44` | 底部读数条高度（触控条） | `kTouchMinDim`(=44) | **替换（红线：禁裸 44，改派生）** |
| ui/main_window.cpp:178 | `scaled(16)`×2 | 顶栏左右内边距 | `kSpacingL`(=16) | 替换 |
| ui/main_window.cpp:254 | `scaled(8)` | 左轨列间距 | `kSpacingM`(=8) | 替换 |
| ui/main_window.cpp:866 | `scaled(4)` | 天空页信息卡列间距 | `kSpacingS`(=4) | 替换 |
| ui/main_window.cpp:879 | `scaled(8)` | 天空页网格间距 | `kSpacingM`(=8) | 替换 |
| ui/weather_panel.cpp:21 | `kPad = 8` | 图像区内边距（本地常量） | `kSpacingM`(=8) | 替换（派生） |

## B. 具名化（新增 tokens.h 常量，值相同，已接线）

新增常量集中于 `core/tokens.h`（Phase37 UI-audit 块），逐一替换调用点裸数：

| 位置 file:line | 裸数 | 语义 | 新增 token |
|---|---|---|---|
| ui/about_dialog.cpp:12 | `380`（未 scaled） | 关于对话框最小宽 | `kAboutMinW=380` |
| ui/calibration_dialog.cpp:179 | `180` | 校准对话框宽增量（加在 kSettingsMinW 上） | `kCalibMinWExtra=180` |
| ui/calibration_dialog.cpp:180 | `420` | 校准对话框最小高 | `kCalibMinH=420` |
| ui/calibration_dialog.cpp:330,419 | `120` | 校准引导/对比标签最小高 | `kCalibReadoutMinH=120` |
| ui/constellation_view.cpp:30 | `180`×2 | 星座图控件最小尺寸 | `kCstViewMinW/H=180` |
| ui/elevation_plot.cpp:13 | `280`,`160` | 仰角-时间图控件最小尺寸 | `kElevPlotW=280`,`kElevPlotH=160` |
| ui/sky_view.cpp:66 | `220`×2 | 极坐标天空图最小尺寸 | `kSkyViewMinW/H=220` |
| ui/vor_panel.cpp:26 | `200`,`240` | VOR 面板最小尺寸 | `kVorPanelMinW=200`,`kVorPanelMinH=240` |
| ui/weather_panel.cpp:26 | `320`,`240` | 气象卫星面板最小尺寸 | `kWeatherPanelMinW=320`,`kWeatherPanelMinH=240` |
| ui/world_view.cpp:18 | `300`,`200` | GIS 世界图最小尺寸 | `kWorldViewMinW=300`,`kWorldViewMinH=200` |
| ui/s_meter.cpp:12 | `34` | S-meter 控件高 | `kSMeterH=34` |
| ui/main_window.cpp:446 | `140` | 中心频率 spinbox 最小宽 | `kFreqSpinMinW=140` |
| ui/main_window.cpp:452,462,488,502 | `120`×4 | 步进/采样率/解调/带宽 combo 最小宽 | `kComboMinW=120` |
| ui/main_window.cpp:473 | `90` | 增益 combo 最小宽 | `kGainComboMinW=90` |
| ui/main_window.cpp:632 | `16` | 电平平度条固定高 | `kLevelBarH=16` |
| ui/main_window.cpp:645 | `80` | 录制目录行编辑最小宽 | `kRecDirMinW=80` |
| ui/main_window.cpp:1117 | `120` | 内嵌仰角图最小高 | `kElevPlotMinH=120` |
| ui/main_window.cpp:1118 | `160` | 内嵌仰角图最大高 | `kElevPlotMaxH=160` |
| ui/main_window.cpp:1155 | `120` | 在视卫星表最大高 | `kNavSatTableMaxH=120` |
| ui/main_window.cpp:1717 | `120`（未 scaled） | AI 会话切换 combo 宽 | `kAiSessionComboMinW=120` |
| ui/main_window.cpp:1825 | `360` | AI 任务步骤列表最大高 | `kAiTaskStepsMaxH=360` |
| ui/main_window.cpp:1844 | `140` | AI 信号活动日志最大高 | `kAiActivityMaxH=140` |

> 备注：`kAboutMinW` / `kAiSessionComboMinW` 调用点**保持未 scaled**，与审计前行为逐像素一致（已在 tokens.h 注释标注）。

## C. 保留（注明理由）

| 位置 file:line | 裸数 | 理由 |
|---|---|---|
| ui/main_window.cpp:465 | `setRange(0,50)` | 增益滑杆范围，硬件域（kGainMinDb/kGainMaxDb 派生）；滑杆取整上界 50，改值会动交互范围 |
| ui/main_window.cpp:4041 | `(dbfs+60)/60*100` | 电平条 -60..0 dBFS→0..100% 显示映射（音频域）；算法显示常数，保留（注释可具名，未动以免动行为） |
| ui/main_window.cpp:253,749-751,851-853,864-865,1081,1124,1195,4460-4466 | `2,3,6,10,12` | off-grid 但已 `scaled()` 的布局边距/间距；**吸附栅格会改像素**，与“不改行为”冲突；保留，DPI 正确 |
| ui/elevation_plot.cpp:105-176, s_meter.cpp:59-83, sky_view.cpp:150-424, world_view.cpp:97-462, spectrum_display.cpp:736, calibration_dialog.cpp:81-82 | `scaled(2..140)` | `paintEvent` 画布绘制几何（刻度偏移、标记点半径、命中容差）；非 QSS/布局；已 `scaled()`，改值会动画布绘制 |
| ui/vor_panel.cpp:19,21 / weather_panel.cpp:20,22 | `26,10,3` | 本地匿名 namespace **已具名**常量（kHeaderH/kDialPad/kPresetCount）；非魔法数 |
| ui/constellation_view.cpp:21-26 | `14,18,22,2,4` | 本地已具名画布内边距/点半径常量 |
| ui/main_window.cpp:127-145 | `kStepValuesHz[]`,`kBwMinHz/MaxHz` | 业务/引擎频率范围，本地已具名 |
| ui/spectrum_display.cpp:30-50 | `kAuto*` | 自动量程算法常数，本地已具名 |
| ui/aircraft_tracker.h:73-74, m17/pocsag 列索引, map_projection.h:120-121 | 各值 | ADS-B 接收机尺度/表列索引/地图投影，算法必需，已具名 |
| 多处 `setContentsMargins(0,0,0,0)` / `setSpacing(0)` / `setMaximumWidth(0)` | `0` | 功能性清零（折叠/去边距），非尺寸魔法数 |
| core/tokens.h:858-859 | `S(6)`,`S(3)` | QSS 生成器内滚动条宽/圆角；属 tokens.h 定义自身（生成器实现），未在本轮改动 |

---

## 验证

- 增量重编：单目标 `mbdsdr`（`make -j2 mbdsdr`，OOM 8GB 约束下低并发）。
- 全量 ctest：见回报输出。基线 84 passed / 35 Not Run / 1 预先失败(agent)；改动后须同集、无新增失败。
- 未重编目标沿用基线产物（35 项 Not Run 测试二进制本就未在本目录编译）。

# Phase45 块3：compass sky-token 组（UI 最后字面量收尾）

> 范围：`mobile/lib/widgets/compass_dial.dart` 极坐标画布裸数全具名化 → 单开
> `AppTokens.kSky*` 组；桌面 C++ 对应物核实。值全部与原字面量逐一相等，**零渲染/行为变化**。

## 1. 交付内容

### 1.1 AppTokens 新增 kSky* 组
文件：`mobile/lib/app/tokens.dart:265-359`（「天空极坐标图（SkyRadar）」段）。
新增 **29 个** `static const double kSky*` token，覆盖 Phase37 mobile-audit §4#1
登记的全部画布几何字面量：

| 类别 | token（值） | 原字面量位置（compass_dial.dart） |
|---|---|---|
| 边距 | `kSkyOuterMargin=26` | 原 `_PolarPainter.outerMargin=26` |
| 标注字号 | `kSkyAzMinorFontSize=8.5` | 非主方位角小标注 |
| 标注字号 | `kSkyAltitudeFontSize=8.0` | 仰角圈标注 30°/60° |
| 标注字号 | `kSkySatLabelFontSize=10.5` | 卫星名标签 |
| 点半径 | `kSkyDotRadius=3.0` / `kSkyDotRadiusHighlight=4.5` | 普通点 / 选中·最高仰角点 |
| 点半径 | `kSkyNavRingRadius=5.0` | 导航预测空心圈 |
| 点半径 | `kSkyTopHaloRadius=7.5` | 最高仰角外圈光晕 |
| 线宽 | `kSkyGridStrokeWidth=1.0` / `kSkyGridCardinalStrokeWidth=1.1` | 网格基线 / 主方位辐条 |
| 线宽 | `kSkyOuterCircleStrokeWidth=1.2` | 地平线外圆 |
| 线宽 | `kSkyArcStrokeWidth=1.2` | 过境弧 |
| 线宽 | `kSkyTrajectoryStrokeWidth=1.6` | 选中卫星真实轨迹 |
| 线宽 | `kSkyNavRingStrokeWidth=1.4` / `kSkyHaloStrokeWidth=1.0` | 导航空心圈 / 光晕环 |
| 线宽 | `kSkyLeaderStrokeWidth=0.8` | 点→标签引线 |
| 标签偏移 | `kSkyTickLength=3.0` | 外圆 30° 刻度径向伸出 |
| 标签偏移 | `kSkyAzLabelInset=10.0` | 方位标注径向内收 |
| 标签偏移 | `kSkyAltLabelDx=3.0` / `kSkyAltLabelDy=-1.0` | 仰角标注偏移 |
| 标签偏移 | `kSkyLabelGap=8.0` | 标签在点上/下方间隙 |
| 标签偏移 | `kSkyLabelPad=3.0` | 标签避让盒内边距 |
| 标签偏移 | `kSkyLabelAnchorRadius=15.0` | 8 向试位锚点半径 |
| 标签偏移 | `kSkyLeaderMinGap=18.0` / `kSkyLeaderEndFraction=0.4` | 引线触发距离 / 端点回缩 |
| 安全/护界 | `kSkyEdgeInset=2.0` / `kSkyMinRadius=8.0` | 画布边距保护 / 窄窗最小 R |
| 标签限宽 | `kSkyNavLabelMaxWidth=110.0` / `kSkySatLabelMaxWidth=96.0` | 标签 maxWidth |

> 4pt 栅格说明：下列值落在亚像素视觉调优档（8.5/4.5/0.8/1.1/10.5），是图形
> 笔画/点径/标注偏移，**不属 4pt 间距栅格**，故单列图形 token 组，不并入
> `spacingS/M/L`（组头注释已写明理由）。触控无新增控件，沿用 `touchMin=44`。

### 1.2 compass_dial.dart 全部改引用
文件：`mobile/lib/widgets/compass_dial.dart`。
- 删除 painter 私有 `static const double outerMargin = 26;`，改引用
  `AppTokens.kSkyOuterMargin`（`_handleTap`、`paint`、方位标注三处）。
- 画布内 **41 处** `AppTokens.kSky*` 引用替换原裸数。
- 保留为角度数学/点选逻辑（非画布绘制参数，不在登记范围）：
  - `180.0/360.0/90.0/270.0` 方位角归一与弧断段阈值（极坐标固有数学）；
  - `_handleTap` 点选容差 `bestD=30.0`、方位权重 `0.3`（交互热区，非绘制几何）；
  - `polarPointInverse` 的 `r>1.2` 夹取（纯函数逆映射安全界，测试契约一部分）。

## 2. 桌面 C++ 核实结论（不硬造两端）

- **cpp 无 compass 同名控件**。功能对应物为 `cpp/src/ui/sky_view.cpp` 的
  `SkyView`（az/el 极坐标天空图，`polarToXY` 纯投影，与 mobile `polarPoint` 同类）。
- 桌面**早已自建独立 `kSky*` token 组**（`cpp/src/core/tokens.h:358-378`）：
  `kSkyGutter=28`、`kSkyRingDotR=2`、`kArcWidth=1.4`、`kArcSelWidth=2.2`、
  `kTrajLineWidth=1.2`、`kLabelOffset=10`、`kLabelMinInset=6`、`kSkyViewMinW/H=220`。
- mobile 本块具名化的具体数值（字号 8.5/8/10.5、点径 3.0/4.5/5.0/7.5、线宽
  1.2/1.6/1.4）在 cpp **无等值对应物**——桌面按自身 DPI 策略取不同基准值
  （如 gutter 28 vs mobile 26、弧宽 1.4 vs mobile 1.2）。
- `sky_view.cpp` paintEvent 内残留的 `scaled(2..140)` 字面量，已被 Phase37 桌面
  `ui-audit.md:83` 明确登记为「保留」（已 DPI 缩放的画布绘制几何，改值会动画布）。
- `constellation_view.cpp` 为 I/Q 数字星座散点面板（`kDotR/kRefR` 文件内已具名），
  与天空罗盘无关。

**结论：compass 为 mobile 独有画布（mobile SkyRadar CustomPainter）；桌面 SkyView
已有其独立且已具名的几何组。两端数值本就按平台分设，本块不向 cpp 反造同名 token，
故未改动任何 cpp 文件。**

## 3. 复验（无回归）

### 3.1 mobile
- `flutter analyze` → **No issues found!**（改动后 0 issue，与基线一致）
- `flutter test`（全量实际计数）→ **All tests passed! +366**（基线 366，无回归）
- 专项：`sky_polar_test.dart`（纯函数 `polarPoint`/`polarPointInverse` 落点）+
  `sky_nav_overlay_test.dart`（注入导航星 widget 渲染 / 空态诚实不画）共 **16 passed**。

### 3.2 桌面 cpp
- 本块**未动 cpp**，按任务条件（「若动 cpp 则 ctest + offscreen 三态截图」）
- 不触发 ctest 全量与 `docs/learn/phase45/screenshots/` 三态截图。

## 4. 改动文件清单（仅以下 2 个）
- `mobile/lib/app/tokens.dart`（+29 token，265-359）
- `mobile/lib/widgets/compass_dial.dart`（41 处引用替换，删 `outerMargin` 私有常量）

> 未碰 `mbdsdr_ai/`（A 域）；未预置活动数据/无假数据/无比赛字样/活动参数未入；
> 未新增测试夹具 mock（复用既有 polarPoint / nav-overlay 测试）；
> **未 git add（禁 add -A）、未 commit/push**。

## 5. 未解决项
- `_handleTap` 点选容差 `bestD=30.0`、方位权重 `0.3`、`polarPointInverse` 的
  `r>1.2` 逆映射夹取为交互/纯函数逻辑常数，非画布绘制几何，本块按登记范围保留；
  如需把「点选手感」也具名化，可另开交互 token 组（不属 UI 画布字面量收尾）。
- 桌面 `sky_view.cpp` paintEvent 残留 `scaled()` 字面量为既有桌面审计「保留」项，
  本块不扩大范围处理。

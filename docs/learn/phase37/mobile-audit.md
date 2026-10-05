# Phase37 Wave1-B：mobile/lib 裸数审计 + AppTokens 对齐

> 范围：`mobile/lib/`（排除 `app/tokens.dart` 自身 = 唯一 token 源；排除算法/文件格式必需常数）。
> 对齐基线：`cpp/src/core/tokens.h` 弹性派生理念 —— 触控 ≥44 逻辑高、4pt 栅格（S4/M8/L16/XL24/XXL32）、字重 400/500/600（主力 500）、强调色 `#919cac`。
> 红线复核：业务代码**无裸 0xFF 颜色**（全部集中在 tokens.dart）；本块消除唯一一处裸 `44`。
> 验证基线：改动前 `flutter test 342/342` + `flutter analyze No issues`；改动后同为 **342/342 + 0 issue**，功能行为不变。

## 0. 扫描方法
- 颜色：`Color(0x…)` / `0xFF……` / `Color.fromARGB/RGBO` → **仅命中 tokens.dart**，业务代码 0 裸色。
- 尺寸/间距/圆角：`SizedBox(` / `EdgeInsets.*` / `BorderRadius.circular(` / `width|height:`。
- 字号/字重：`fontSize:` / `FontWeight.w…`。
- 专项：裸 `44`、`minHeight`、`MaterialTapTargetSize`、`spacingS + 2` 等派生裸偏移。

## 1. 判定「替换」——已落地走 AppTokens 具名常量

| 位置 | 裸数 | 语义 | 应替换 token | 判定 |
|---|---|---|---|---|
| `pages/settings_page.dart:233` | `width: 44` | 音量百分比读位列定宽（"100%" 右对齐列） | `AppTokens.touchMin` | **替换**（裸 44 红线；改 `width: AppTokens.touchMin`，注释改为"≥touchMin 读位列"） |
| `pages/sky_page.dart:793` | `fontWeight: FontWeight.w600` | "已对准 ✓" 字重 | `AppTokens.weightSemi` | **替换**（裸字重 → 语义 token） |
| `widgets/compass_dial.dart:281` | `copyWith(fontSize: 12, fontWeight: FontWeight.w600)` | 主方位角 N/E/S/W 标注 | `AppTokens.auxiliary.copyWith(fontWeight: AppTokens.weightSemi)` | **替换**：`fontSize:12` = `auxiliary` 自身字号（12）冗余，删除；`w600`→`weightSemi` |
| `widgets/connection_status_line.dart:47,48,51` | `AppTokens.spacingS + 2`（=6） | 连接状态圆点直径 + 圆点→文字间隙 | **新增** `AppTokens.statusDotSize`(=6) | **替换**（两处文件复用，消除派生裸偏移） |
| `widgets/device_info_card.dart:160,161,167` | `AppTokens.spacingS + 2`（=6） | 设备信息状态圆点直径 + 间隙 | `AppTokens.statusDotSize` | **替换**（同上） |

### 新增 token（`app/tokens.dart` 尺寸段）
```dart
/// 状态指示小圆点直径（连接状态点 / 设备信息状态点）。
/// 圆点与其后文字的相邻间隙沿用同一尺寸。跨 connection_status_line /
/// device_info_card 两处复用，禁止散落裸写 `spacingS + 2`（=6）。
static const double statusDotSize = 6;
```
> 6 为图形直径而非 4pt 栅格间距，故单列 token（参照桌面 `kMapGnssOuterR=6` 同类图形尺寸命名）。

**替换统计：5 处站点、6 个文件、新增 1 个 token；数值逐一等价，0 视觉/行为变化。**

## 2. 判定「保留 + 理由」——不改（技术笔画 / 画布几何 / 文件格式常数）

| 位置 | 裸数 | 语义 | 保留理由 |
|---|---|---|---|
| `pages/chat_page.dart:471` | `Divider(height:1, color: divider)` | 会话列表分隔发丝线 | 1px 发丝，与 `dividerTheme` thickness:1 一致；技术笔画非布局魔法数 |
| `pages/activity_log_page.dart:87` | `Divider(height:1, color: cardEdge)` | 活动日志行分隔线 | 同上，1px 发丝 |
| `app/home_shell.dart:294` | `VerticalDivider(width:1, thickness:1)` | 导航栏↔内容竖发丝 | 1px 发丝，主题化分隔 |
| `widgets/s_meter.dart:225` | `Container(width:2, color: warning)` | S-meter 峰值保持游标竖线 | 2px 峰值标记线（仪器画布），与桌面 `kFixedMarkerLineWidth` 同类 |
| `pages/spacetime_status.dart:158` | `SizedBox(height:2)` | 单元格标题↔正文微间隙 | 2px 标签聚拢微间隙（亚栅格），非节奏间距 |
| `widgets/remote_decoder_panel.dart:463` | `vertical: 2` | VOR 质量 chip 上下内边距 | chip 内文字微 padding |
| `widgets/remote_decoder_panel.dart:363` | `height: 110` | VOR 表盘 CustomPaint 画布高 | 表盘画布固定尺寸（非 4pt 栅格间距）；如后续扩组可具名 `vorDialH`，本块不动以免牵动画布布局 |
| `widgets/task_progress.dart:72` | `width: 22` | 步骤序号列定宽（"1".."8"） | 序号读位列固定宽；非 4pt 栅格。本块不改以免牵动行内排布，记入待办 |
| `widgets/task_progress.dart:133` | `CircularProgressIndicator(strokeWidth:1.6)` | 进度圈线宽 | 技术笔画 |
| `widgets/device_info_card.dart:154` | `width: 56` | 标签列定宽 | on-grid（14×4）读位列 |
| `pages/settings_page.dart:481-484` | `width:16, height:16, strokeWidth:2` | 按钮内联定位进度圈 | 16 = `spacingL` on-grid 内联图标尺寸 |
| `pages/sky_page.dart:419` | `LinearProgressIndicator(minHeight:2)` | 进度条厚度 | 2px 发丝条 |
| `pages/sky_page.dart:738` | `Icon(Icons.location_off, size:40)` | 空态插画图标 | on-grid(10×4) 插画字形，与 `iconSizeEmpty`(48) 不同档 |
| `widgets/compass_dial.dart` 画布几何 | fontSize 8.5/8/10.5；dotR 3.0/4.5/5.0/7.5；strokeWidth 1.2/1.6/1.4/1.0；标签偏移 10/15/8 | Stellarium 式极坐标图绘制参数 | 仪器画布几何，与桌面 `kSky*`/`kMap*` 同类；单测对纯函数 `polarPoint` 落点校验，与像素参数解耦。本块聚焦 UI token 对齐，未扩 sky-token 组（见未解决项） |
| `audio/wav_writer.dart`（多处） | `44` | WAV RIFF 头字节数 | 文件格式必需常数 |
| `astro/tle.dart:117` | `substring(44,45)` | TLE 字符串字段列位 | TLE 格式偏移必需常数 |

### 已合规、无需动作（派生自 token）
- `widgets/spectrum_display.dart:130` `_freqStripH = spacingL * 2.4`、`widgets/empty_state.dart:64` `spacingL * 2` —— token 派生。
- `pages/spectrum_page.dart:585`、`pages/chat_page.dart:720` 触控热区已走 `BoxConstraints(minHeight: AppTokens.touchMin)`。

## 3. 验证（全量）
- `flutter test` → **All tests passed! +342**（不回归）。
- `flutter analyze` → **No issues found!**（改动 6 文件 0 issue；全包 0 issue）。
- 注：任务提示 `test/radio_scan_test.dart` 有 2 条既有 lint；本块基线实测该文件与全包均 **0 issue**（疑似并行 WIP 已清）。本块未触碰该文件。

## 4. 未解决项 / 后续建议
1. `widgets/compass_dial.dart` 极坐标图画布几何（标注字号 8.5/8/10.5、点半径、线宽、标签偏移）仍为字面量 —— 建议后续单开「sky-token 组」（对齐桌面 `kSky*`/`kMap*`）统一具名，本块不扩组以免越界。
2. `widgets/task_progress.dart:72` 序号列 `width:22`、`widgets/remote_decoder_panel.dart:363` 表盘 `height:110` 为非 4pt 栅格固定画布/读位列尺寸 —— 如需严格 4pt 栅格可再议（会动视觉密度），本块保守保留。
3. 1px/2px 发丝与微间隙（Divider/游标线/chip padding）保留为技术笔画；如需与桌面 `kDivider` 完全同名可后续补 `hairlineThickness` token。

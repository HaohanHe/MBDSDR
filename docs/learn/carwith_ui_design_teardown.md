# 小米 CarWith (com.miui.carlink) v4.0.2 — UI/窗口设计拆解

> **拆解对象**：`signed_PLATFORM_CarWith_4.0.2-20260626_release.apk`（61MB，平台签名）
> **包名**：`com.miui.carlink` | **版本**：4.0.2-20260626 (versionCode 104000002)
> **SDK**：minSdk 29 (Android 10) / targetSdk 33 (Android 13)
> **方法**：androguard 解析二进制 AndroidManifest.xml + resources.arsc，AXMLPrinter 反编译混淆后的 layout/drawable/anim XML
> **资源状态**：APK 经 AndResGuard 类资源混淆，`res/` 下文件名为短随机串（如 `res/N-5.xml`），真实资源名保留在 arsc 中
> **红线**：本报告仅做设计模式研究与描述，不复制任何反编译代码、smali、资源或图标到任何项目

---

## 0. 一句话结论

CarWith 的本质是**在手机端用特权 API 凭空造一块"虚拟第二屏"**，把第三方 App 的任务栈搬上去、按车机横屏做"平行视界式主区+副区"分割，再编码成视频流推给车机。它在用户态复刻了一套 WMS/AMS 双服务窗口管理，用透明 singleInstance Activity 当"窗口积木"，靠全量自管 configChanges 实现零重建的动态拖拽分屏。视觉上基于 MIUIX Design Token 体系，走"品牌蓝 + 纯黑暗色 + 大圆角胶囊 + Material Motion 六级时长 + 弹簧物理"的车机沉稳风格。

---

## 1. 架构总览：手机端虚拟化第二屏

### 1.1 不是车机端 App，是手机端投屏平台

| 证据 | 含义 |
|---|---|
| 未声明 `android.hardware.type.automotive` | 不是车机 Head Unit 系统应用 |
| 申请 `ADD_TRUSTED_DISPLAY` / `ADD_ALWAYS_UNLOCKED_DISPLAY` | 在手机端创建受信虚拟显示器（车机屏本质是 Secondary Display） |
| 申请 `CAPTURE_VIDEO_OUTPUT` / `READ_FRAME_BUFFER` | 抓取虚拟屏画面编码上行 |
| 申请 `MANAGE_ACTIVITY_TASKS` / `SET_ACTIVITY_WATCHER` | 把第三方 App 任务搬进虚拟屏、监听启动 |
| 72 个 Activity 无一个 `MAIN/LAUNCHER` | 不靠桌面图标启动，由 USB AOA / 系统 Service / `miui.intent.action.UCAR_*` 拉起 |
| APK 文件名含 `PLATFORM`，应用级 `miui.supportAppContinuity=true` | 平台签名组件，向 MIUI 申请窗口形态改造与跨端接续 |

### 1.2 用户态 WMS/AMS 双服务

CarWith 在应用进程内复刻了系统级窗口管理分工：

```
┌─────────────────────────────────────────────────────────┐
│                    CarlinkService (数据面)                │
│  USB AOA / 蓝牙 BLE / WiFi 通道 · H.264 编码上行 · 触控下行 │
├──────────────────────┬──────────────────────────────────┤
│  UCarWindowManager   │   UCarActivityManagerService      │
│  Service (WMS 面)    │   (AMS 面)                        │
│  · 虚拟 Display 创建  │   · 任务栈路由/重排/删除            │
│  · 窗口位置/大小/层级 │   · Activity 启动监听 (watcher)    │
│  · 横竖屏/分屏比例    │   · 主区/副区任务栈回退管理         │
│  · 卡片圆角/阴影/叠层 │   · 第三方 App 搬进虚拟屏          │
├──────────────────────┴──────────────────────────────────┤
│           DrivingModeWindowService (驾驶模式策略)          │
│           FocusAccessibilityService (无障碍兜底拆分)       │
└─────────────────────────────────────────────────────────┘
```

- **WMS**（`com.carwith.launcher.wms.UCarWindowManagerService`）：管窗口位置/大小/方向/Overlay，手握 Display/Orientation/SystemAlert 权限
- **AMS**（`com.carwith.launcher.ams.UCarActivityManagerService`）：管任务栈路由/重排/删除/Activity watcher，手握 Task/GetTask/RemoveTask 权限
- **驾驶模式窗口服务**（`DrivingModeWindowService`）：驾驶场景下接管窗口策略——大字号卡片、屏蔽非驾驶弹窗、固定导航/音乐/电话布局
- **无障碍兜底**（`FocusAccessibilityService`，BIND_ACCESSIBILITY_SERVICE）：在第三方 App 不做任何适配的情况下，读界面树、劫持返回键、把任务搬进虚拟屏——这是 CarWith 能"裸投"任意 App 的关键

---

## 2. 平行视界（Embedding）的实现机制

### 2.1 透明 singleInstance Activity 作为生命周期锚点

核心证据：`com.carwith.launcher.miuiembedding.EmbeddingLifecycleActivity`

| 属性 | 值 | 设计含义 |
|---|---|---|
| `launchMode` | **singleInstance** (3) | 独占任务栈，自己不显示内容，只做生命周期锚点/桩 |
| `theme` | **`Transparent_Theme`** | 半透明，用户看不见 |
| `configChanges` | **0x50002FF0** | screenLayout/screenSize/smallestScreenSize/orientation/uiMode/layoutDirection **全量自管**，系统不重建 |
| `excludeFromRecents` | true | 不进最近任务 |

同属性组合的还有：
- `map.wms.MinMapLauncherActivity` — 小窗地图启动器（副区卡片）
- `map.wms.BigMapLauncherActivity` — 全屏大地图启动器（主区）
- `castfwk.CastingActivity` — 投屏画面承载（透明叠层）
- `activity.SplashCardActivity` — 启动卡片悬浮窗

**设计要点**：平行视界不是用 Jetpack WindowManager 的 XML 静态 SplitPair 规则，而是**运行时动态拆分**——第三方 App 在虚拟 Display 上全屏启动，`EmbeddingLifecycleActivity` 作为透明锚点感知配置变化，`UCarWindowManagerService` 通过 `MANAGE_ACTIVITY_TASKS` 把同一 App 的两个子页面（列表/详情，或导航/音乐）放到同一虚拟 Display 的两个 task 的两个窗口位置，左右并排。

### 2.2 全量自管 configChanges = 零重建动态窗口

核心动态窗口 Activity 的 `configChanges=0x50002FF0`，配合 `resizeableActivity=false`（应用级）：

> 系统不要因旋转/分屏比例变化而重建 Activity，一切尺寸/形态变化由 `UCarWindowManagerService` 在代码里重新布局窗口。

这是"动态窗口能丝滑拖拽分屏、旋转不闪屏"的底层原因。CarWith **主动放弃** Android 原生分屏/PiP（无 `resizeableActivity=true`、无 `supportsPictureInPicture`），所有窗口自由度走自己的服务。

### 2.3 布局层的平行视界证据

**最硬证据——可拖拽滑块动态分配地图/音乐空间**：

`mini_map_card.xml`（混淆路径 `res/kNE.xml`）内 TextView 原文：
> "地图位置分配中 / **拖动滑块调整地图/音乐空间** / 调整完毕点击确认按钮保存"

同层有 `BlurView` 遮罩 + "确认"按钮，证明存在运行时可拖动的分隔 handle，实时调整两区宽度比例并可保存。

**并列双栏（weight 动态分配）**：
- `fragment_media_play.xml`：水平 LinearLayout 内 **左 ConstraintLayout weight=1.6 ∶ 右 RelativeLayout weight=1.0**（左=歌词/进度/控制，右=旋转唱片+播放列表）
- `layout_commuter_card.xml`：双列 **weight=1∶1** 等宽卡片，中间 alpha=0.06 细分隔
- `docker_view_bottom.xml`：多个 `Space weight=0.5/1/2` 动态分配图标簇位置

**MiUI Embedding 平行窗口容器**：
- `miui_embedding_window_layout.xml`（`res/HpP.xml`）：FrameLayout > ConstraintLayout > `com.carwith.launcher.miuiembedding.EmbeddingView`（id=0x7F0A01FC，match×match），旁挂 BlurView 遮罩——承载双应用并排的分屏宿主

---

## 3. 动态窗口形态

### 3.1 透明/空 Activity 桩体系

CarWith 不依赖 Dialog/PopupWindow 做临时浮层，而是**为每一种瞬态窗口注册独立透明/空 Activity**，靠 Activity 栈管理层级、返回键和生命周期：

| Activity | 主题 | 作用 |
|---|---|---|
| `TransparentActivity` | `TransparentNoFocusTheme`（半透明+**不抢焦点**） | 最"透"的桩：输入穿透给下层窗口，用于不打断操作前提下插生命周期节点 |
| `VehScrEmptyActivity` / `BeforeThirdAppEmptyActivity` | `CarWithTheme.NoAnimation` | 车屏空占位 + 第三方 App 启动前过渡，无转场动画硬切防闪屏 |
| `SmallEmptyActivity` | `AppTheme.NoTitle` | 小号空占位，尺寸动画中间帧 |
| `FullScreenMapHelperActivity` | 系统全屏黑主题 | 地图卡片→全屏缩放时的黑场补帧 |
| `SplashCardActivity` | `Transparent_Theme` + singleInstance | 圆角卡片形启动过渡窗（弹簧动画+触感震动） |

### 3.2 用 Activity 实现对话框

`com.carwith.common.view.CommonDialog` 类名像 Dialog，但是 `<activity>`：
- 主题 `DialogActivity`（对话框式浮动窗口）
- `taskAffinity=com.miui.carlink.common.dialog`（独立任务栈）
- `windowSoftInputMode=stateHidden|adjustResize`

**原因**：虚拟 Display + 跨任务栈架构下，Dialog 没有独立 WindowToken 会丢窗；Activity 自带任务栈与 WindowToken，能浮在任何 App 之上、能被无障碍/通知/车机端命令从任意位置拉起。

### 3.3 卡片即窗口

- `activity_card` 与 `activity_home` **复用同一主容器 id**（`0x7F0A01D1`），只是外层是否带顶部窄条
- `activity_splash_card_horizontal`：40% 水平 Guideline 切分上下 + 16:9 预览卡 + ViewPager2
- `layout_media_floating`：wrap×wrap、cornerRadius=50dp 的**胶囊悬浮迷你控制器**（elevation=4dp）
- 地图上叠加 `large_map_float_layout`（竖向 wrap 缩放组）、`map_float_view`（DPI 切换工具）

---

## 4. 主界面布局结构

### 4.1 "全屏主容器 + 悬浮 Dock"叠加式

`activity_home.xml`（`res/N-5.xml`）视图树：

```
CardView (match×match, cornerRadius, elevation=2dp)
└─ BlurRelativeLayout (match×match, 毛玻璃根)
   ├─ FrameLayout id=0x7F0A01D1 (match×match)  ← ★主内容 Fragment 容器，全屏铺底
   ├─ DockerView id=0x7F0A0248 (match宽, wrap高) ← Dock 浮在主内容上层
   ├─ include @layout/carlife_con_tips          ← 连接提示浮层
   └─ ImgLoadingView (5dp×5dp, gone, alpha=0.1) ← 隐形探测 View
```

**关键**：主内容与 Dock **不是 LinearLayout 三段式堆叠**，而是 Frame/Relative 叠加——主内容铺满整屏，地图可沉浸式延伸到 Dock 下方。

### 4.2 同一套 View id、横竖两份 Dock 变体

| 变体 | orientation | 布局 |
|---|---|---|
| `docker_view` | vertical（竖 rail） | Space+weight 垂直分布：时钟/SIM/电量 → 地图/媒体/其他 → 呼叫/语音 → Home |
| `docker_view_bottom` | horizontal（横 bar） | Space+weight 水平分布：Home → 中央图标簇(呼叫/时间/地图/媒体) → DockerMusic+Call → 右端状态(时钟/SIM/电量) |

两者**根 id（0x7F0A0246）与全部子 id 完全一致**，靠运行时 inflate 不同 xml 适配屏幕方向，对外提供一致 API。另有 `docker_view_bottom_phone/_port`、`fragment_media_play_land/_port` 等成对变体。

### 4.3 驾驶模式：显式三段式

`layout_driving_mode_main.xml`：
- 上部：大地图 FrameLayout（margins=16dp，约束在顶与媒体卡之间）
- 中部：媒体卡片 **固定 112dp**（专辑+标题+上/下一曲）
- 底部：快捷栏（一排 40-46dp 快捷开关 + 紧凑媒体条）

驾驶模式导航字号放大到 **22.5sp**，Dock 用蓝灰 `#803A4259` 反白。

### 4.4 副窗折叠：visibility 切换而非独立窗口

- 播放列表 `MediaPlayListView`（id=0x7F0A044D）：一律 `alignParentRight` + `visibility=gone` + `elevation=1dp`——右侧抽屉，默认收起可展开
- 遮罩 BlurView、紧凑媒体条、空态卡均默认 gone，按状态切显隐
- 地图容器 id 统一 `0x7F0A01FA`：小地图 TextureView / 大地图 BigMapView / 全屏 CardView+TextureView 共用同一容器，大小切换是**换 Surface 而非换页**

---

## 5. 视觉语言

### 5.1 配色系统（MIUIX Design Token）

视觉基座是 **MIUIX 自研组件库**，所有颜色收敛到 `miuix_default_color_*_light/dark` 两组 token，主题通过 `app:colorPrimary/colorSurface/colorOnSurface` 引用，深浅模式只切 token 组。

**主色与语义色**：

| 用途 | Light | Dark |
|---|---|---|
| 品牌主色 | `#FF3482FF`（MIUI 蓝） | `#FF4788FF`（略提亮） |
| 主色上内容 | `#FFFFFFFF` | `#E6FFFFFF` |
| 次色（主色浅染） | `#1A3482FF`（10% 蓝） | `#404788FF`（25% 蓝） |
| 错误 Error | `#FFFA382E` | `#FFFA4238` |
| 错误容器 | `#1AFA382E`（10% 红） | `#38FA4238`（22% 红） |
| 警示 Caution | `#FFFF9F05` | `#FFFFA30F` |
| 警示容器 | `#1AFF9F05`（10% 橙） | `#38FFA30F`（22% 橙） |

**背景/表面层级**（暗色走 OLED 纯黑 + 三档灰抬层级）：

| 层级 | Light | Dark |
|---|---|---|
| Surface 基底 | `#FFFFFFFF` | `#FF000000`（纯黑） |
| SurfaceLow | `#FFF7F7F7` | `#FF000000` |
| SurfaceMedium | `#FFFFFFFF` | `#FF181818` |
| SurfaceHigh | `#FFFFFFFF` | `#FF242424` |
| SurfaceHighest | `#FFFFFFFF` | `#FF2C2C2C` |
| 浮窗 PopWindow | `#FFF7F7F7` | `#FF242424` |
| 卡片容器 | `#0A000000`（4% 黑） | `#14FFFFFF`（8% 白） |
| 遮罩 Mask | `#4D000000`（30% 黑） | `#99000000`（60% 黑） |

**文字色：黑/白主色 × 8 级 alpha**（不另设色板）：

| 层级 | Light | Dark |
|---|---|---|
| 主要 | `#FF000000` | `#F2FFFFFF`（95%） |
| 二级 | `#CC000000`（80%） | `#CCFFFFFF` |
| 三级 | `#99000000`（60%） | `#80FFFFFF` |
| 四级 | `#66000000`（40%） | `#80FFFFFF` |
| 八级 | `#4D000000`（30%） | `#4DFFFFFF` |

**分割线/描边**：Light `#1A000000`（10% 黑），Dark `#33FFFFFF`（20% 白）。

### 5.2 尺寸与间距

**字号层级（sp）**：

| 层级 | 值 | 用途 |
|---|---|---|
| 导航大数字 | 22.5sp | 驾驶模式距离 |
| 通用正文 | 18sp | 车机正文（比手机大） |
| 标题 | 15sp | 卡片日期/选择标题 |
| 按钮 | 14sp | 主按钮/协议 |
| 副标题 | 13.5sp | 导航方向/通勤公司 |
| 卡片小标题 | 12sp | 日历标题 |
| 辅助说明 | 11.25sp | 权限提示 |
| 最小可读 | 7.5sp | 车机小卡星期 |

**圆角体系（dp）**：

| 用途 | 值 |
|---|---|
| 主按钮/胶囊 | 36dp |
| 微按钮/Inline | 200dp（全圆） |
| 搜索框 | 72dp（全圆角胶囊） |
| 输入框 | 18dp |
| 通用车机卡片 | 9dp |
| CarLife 形状 | 18dp |
| 浮窗/箭头弹窗 | 12-14dp |
| 列表菜单头 | 24dp |
| 页面指示器 | 50dp |

**栏高/图标**：操作栏 56dp（max 72dp），卡片底部栏 32dp，媒体工具栏 42.4dp，迷你窗顶栏 30dp，小卡片图标 33.75dp，CTA 图标 32dp。

**间距节奏**：页面水平边距 36dp，卡片内边距 ≈16dp，卡片间纵向 13.3dp，整体遵循 **4dp 栅格**（4/8/12/16/20/24/36dp）。

### 5.3 按压反馈：半透明叠层而非描边

- 主按钮 = 纯色胶囊底 + `pressedAlpha=0.12`（12% 黑按下叠层）+ `hoveredAlpha=0.06`（6% 悬停叠层），无描边
- 涟漪用 `#33FFFFFF`（20% 白）或 `#1A0D84FF`（10% 品牌蓝），**无边界半透明叠加**
- 列表项 pressed→半透明蓝/灰，normal→白/透明

### 5.4 自研控件能力（declare-styleable）

- **弹簧物理**：`springDamping / springResponse / springStiffness / springMass / springBackMode`
- **平滑模糊**：`miuix_useSmooth / miuix_blurRadius / textureBlurFactor`
- **四角独立圆角**：`riv_corner_radius_*`（RoundedImageView）
- **按压/悬停**：`pressedAlpha / hoveredAlpha / itemRippleColor`
- **层级阴影**：`elevation / cardElevation / pressedTranslationZ`

---

## 6. 动效系统

### 6.1 Material Motion 六级时长

| 级别 | 时长 | 用途 |
|---|---|---|
| short1 | 75ms | 极快微反馈 |
| short2 | 150ms | 沉浸菜单、按钮退场 |
| medium1 | 200ms | 列表/卡片反馈 |
| medium2 | 250ms | 操作栏出现 |
| long1 | 300ms | Fragment 转场、对话框弹入 |
| long2 | 350ms | 浮窗弹簧动画 |

### 6.2 关键动画

**Fragment 页面转场（300ms，共享轴式缩放+透明度）**：
- 进入：scale 0.85→1.0（`fast_out_extra_slow_in`）+ alpha 0→1（50ms）
- 退出：scale 1.0→1.15（后退页放大营造纵深）+ alpha→0
- 关闭：scale 1.10→1.0 / 1.0→0.90

**对话框（150-300ms）**：
- 中心弹入：scale 80%→100% + alpha 0→1，`decelerate_cubic`，300ms
- 底部滑入：translateY 100%→0，300ms
- 退场：150ms（比入场快，"快速消失"）

**沉浸菜单（150ms 快速反馈）**：
- scale 0.8→1.0 + alpha，`decelerate_sextic`，8 方向变体

**浮窗弹簧（350ms 强调级）**：
- 进入：`translatewithclip`，damping=0.4、response=0.36
- 退出：springInterpolator，damping=0.9、response=0.5 + alpha 延迟淡出
- 提供 `_land` / `_auto_dpi` 横屏变体

**插值器**：标准 `fast_out_slow_in` / `mtrl_fast_out_slow_in`；自研 `decelerate_cubic/sextic/square`、`accelerate_quart/sextic`；弹簧 `spring_phy_default`（damping=0.95, response=0.30）。

### 6.3 动效节奏总结

- **快速反馈 ≤200ms**：列表按下/悬停（alpha 叠层即时）、沉浸菜单 150ms、按钮退场
- **流畅过渡 200-400ms**：Fragment 转场 300ms、对话框 300ms、浮窗弹簧 350ms
- **强调动画 >400ms**：连接中循环 1300ms（无限）、fade_in 500ms

---

## 7. 车机适配特征

- **7 种车机横屏分辨率桶**：1200x480 / 1800x600 / 1160x720 / 1740x900 / 1740x1076 / 1740x1080 / 1800x720，覆盖 ldpi→xxxhdpi
- **横屏限定资源 282 个**：`sw600dp-land`（236）、`land`（34）等
- **夜间模式完整覆盖**：94 色 / 100 drawable / 102 style，规律为白底→深灰/纯黑、黑字→白字、分割线黑半透→白半透
- **驾驶模式**：蓝牙连接自动触发，限制视频/文字应用，导航超大字号 22.5sp，高对比蓝灰 Dock
- **按屏幕规格换布局**：arsc 中 layout 存在 `sw600dp` 第二 config chunk，同名 layout 在大屏有第二份资源

---

## 8. 可迁移到桌面 PySide6 的设计要点清单

> 以下将 CarWith 的设计模式映射到 PySide6（Qt for Python）桌面 SDR 软件的具体实现建议。核心思想：**主区+副窗联动、可拖拽分区、悬浮面板、Design Token 驱动、Material Motion 节奏**。

### 8.1 窗口分区结构

| CarWith 模式 | PySide6 对应 | SDR 应用建议 |
|---|---|---|
| 全屏主容器 + 悬浮 Dock 叠加（非三段式） | `QMainWindow` 中心 `QStackedWidget` 铺满 + `QDockWidget` 设为浮动/叠加而非停靠 | 频谱/瀑布图主区铺满整个中央控件，控制面板以浮动 `QDockWidget` 叠在边缘，主可视化可延伸到面板下方（沉浸式） |
| 平行视界主区+副区并排（weight=1.6:1.0） | `QSplitter`（horizontal）+ `QStackedWidget` 或 `QWidget` 容器，设置 `setStretchFactor(0, 16)` / `setStretchFactor(1, 10)` | 左侧频谱主区（stretch=16），右侧实时状态/AI 对话副窗（stretch=10），比例可拖 |
| 可拖拽滑块动态分区（mini_map_card 实证） | `QSplitter` 自带拖拽 handle，`setHandleWidth(4)`，配合 `QSplitterHandle` 自定义视觉 | 频谱区与参数面板区之间放可拖拽分隔条，拖动实时调整宽度，双击重置默认比例 |
| 副窗折叠/展开（visibility=gone 抽屉） | `QDockWidget` 的 `show()/hide()` + `QPropertyAnimation` 做宽度动画；或 `QWidget::setVisible(false)` | 右侧 AI 对话副窗可一键折叠为窄条（仅留图标），再点展开；折叠时主区自动占满 |
| 同一容器 id 换 Surface（大小地图切换） | `QStackedWidget` 同一容器内 `setCurrentIndex()` 切换不同 widget | 小频谱卡片 / 大频谱全屏 / 瀑布图 共用同一容器位置，切换是换 widget 而非换窗口 |
| 卡片即窗口（activity_card 复用主容器） | `QDockWidget` 浮动模式 + `QFrame` 卡片样式 | 常用工具（录音、标记、解码）做成可浮动卡片面板，可停靠可拖出 |

### 8.2 窗口管理架构

| CarWith 模式 | PySide6 对应 | 建议 |
|---|---|---|
| WMS/AMS 双服务分工（窗口位置 vs 任务生命周期） | 分离 `WindowManager`（管 Dock 位置/大小/层级）与 `PanelManager`（管面板注册/显示/回退） | 不要把面板管理逻辑散在 MainWindow 里；抽一个 `PanelRegistry` 管面板元数据，一个 `DockLayoutManager` 管位置/保存/恢复 |
| 全量自管 configChanges = 零重建 | Qt 天然不重建（`QWidget` 持久），但需在 `resizeEvent` 中重布局而非销毁重建 | 窗口缩放/分屏比例变化时，只调 `QSplitter` 的 `setSizes()`，不要销毁重建频谱 widget |
| 透明 Activity 桩做窗口切换锚点 | 不需要（Qt 单窗口架构），但可借鉴"用空 widget 做布局占位/动画中间帧" | 面板切换动画中用一个透明 `QWidget` 占位做缩放过渡，避免内容闪烁 |
| 对话框用独立窗口（跨 Display 可靠） | `QDialog` 或 `QMainWindow` 独立顶层窗口，`setWindowFlag(Qt.Tool)` | 重要对话框（设备选择、导出配置）用独立 `QDialog` 而非内嵌 popup，确保在多屏/浮动面板场景下不丢失 |

### 8.3 视觉 Design Token

| CarWith 模式 | PySide6 对应 | 建议值 |
|---|---|---|
| MIUIX Design Token（颜色/圆角/间距收敛到 token） | `QPalette` + 全局 `DesignToken` 单例类（Python dataclass），QSS 引用变量 | 建一个 `tokens.py`：`COLOR_PRIMARY="#3482FF"`、`RADIUS_CARD=9`、`SPACING_BASE=4`，所有 QSS 用 `{}`.format() 或 f-string 注入 |
| 品牌蓝统一 + 语义色低 alpha 容器 | `QPalette.setColor(QPalette.Accent, ...)`；错误/警示用 10-22% alpha 容器色 | 主色 `#3482FF`；错误底色 `#1AFA382E`（10% 红）而非纯红块；警示底色 `#1AFF9F05`（10% 橙） |
| 8 级文字透明度（不另设色板） | `QColor(name).setAlpha(alpha)` 或 QSS `color: rgba(0,0,0,80%)` | 文字只用黑/白 × alpha：主要 100%、二级 80%、三级 60%、四级 40%、辅助 30% |
| 暗色 OLED 纯黑 + 三档灰抬层级 | 暗色主题 `QPalette`：Window=`#000000`、Base=`#181818`、AlternateBase=`#242424`、ToolTipBase=`#2C2C2C` | SDR 软件天然适合暗色：频谱背景纯黑，面板 `#181818`，弹窗 `#242424`，悬浮 `#2C2C2C` |
| 圆角体系分明（胶囊 36-72dp / 卡片 9dp / 浮窗 12-24dp） | QSS `border-radius`：按钮 `border-radius: 18px`（Qt 中 dp≈px at 96dpi），卡片 `border-radius: 9px` | 主按钮大圆角胶囊（18px），内容卡片 9px，浮窗/弹窗 12-16px；搜索框全圆角 |
| 4dp 栅格间距 | 所有 margin/padding 取 4 的倍数 | 页面边距 16px，卡片内边距 12-16px，卡片间距 8-12px，紧凑区 4px |
| 按压反馈 = 半透明叠层（pressedAlpha=0.12） | QSS `QPushButton:pressed { background-color: rgba(0,0,0,12%); }`；hover 6% | 按钮不要用描边/变色做 pressed，用 12% 黑叠层；hover 用 6%；涟漪效果可用 `QGraphicsDropShadowEffect` 或自定义 paint |
| 毛玻璃背景（BlurRelativeLayout） | `QGraphicsBlurEffect` + 半透明背景，或 Qt 6 的 `QQuick` 背景模糊 | 浮动面板背景用 70-80% 不透明 + `QGraphicsBlurEffect` 模糊下层，营造悬浮感 |

### 8.4 字号层级（SDR 桌面适配）

| CarWith（车机 sp） | 桌面建议（px，96dpi） | 用途 |
|---|---|---|
| 22.5sp（导航大数字） | 20-22px | 频率/信号强度大数字 |
| 18sp（正文） | 14-15px | 面板正文/列表 |
| 15sp（标题） | 13px | 面板标题/卡片标题 |
| 14sp（按钮） | 12-13px | 按钮文字 |
| 13.5sp（副标题） | 11-12px | 辅助信息/参数值 |
| 12sp（小标题） | 11px | 分组标签 |
| 7.5sp（最小） | 9-10px | 坐标轴刻度/状态指示 |

> 车机字号偏大（驾驶安全），桌面 SDR 可适当缩小 15-20%，但频率/信号等关键数字保留大字号。

### 8.5 动效节奏

| CarWith 级别 | PySide6 对应 | 用途 |
|---|---|---|
| ≤200ms 快速反馈 | `QPropertyAnimation` duration 150ms，`QEasingCurve.OutCubic` | 按钮 pressed、面板折叠/展开、tooltip 出现 |
| 200-400ms 流畅过渡 | duration 300ms，`QEasingCurve.OutCubic` 或 `OutQuart` | 面板切换、对话框弹入（scale 0.85→1.0 + alpha）、频谱视图切换 |
| 350ms 浮窗弹簧 | `QPropertyAnimation` + 自定义 `QEasingCurve` 弹簧，或 `QSpringAnimation`（Qt Quick） | 浮动面板拖出/收回，damping≈0.4-0.9 |
| 页面转场 85%→100% 缩放 | `QGraphicsScale` + `QGraphicsOpacityEffect` 动画 | 主视图切换（频谱→瀑布图→解调面板） |
| 退场比入场快（150ms vs 300ms） | 关闭动画 duration 设为入场的 1/2 | 对话框/面板关闭要"快速消失"，不要慢吞吞 |

**Qt 实现建议**：
- 用 `QPropertyAnimation` 动画 `geometry`、`windowOpacity`、`maximumWidth`（面板折叠）
- 面板折叠动画：动画 `maximumWidth` 从当前值到 0（或 32px 窄条），配合 `setVisible`
- 对话框弹入：`QGraphicsOpacityEffect` + `QGraphicsScale`（需 `QGraphicsView` 体系），或简单用 `windowOpacity` + `resize` 动画
- 插值器优先 `OutCubic`（先快后慢，"快速进入、从容停留"），退场用 `InCubic` 或 `InQuad`

### 8.6 多屏/自适应

| CarWith 模式 | PySide6 对应 | 建议 |
|---|---|---|
| 7 种车机分辨率桶 + sw600dp 换布局 | `QScreen` 几何检测 + `QSplitter` 动态比例 + 保存/恢复 `saveState()` | SDR 软件应支持：窄屏（单栏频谱）、宽屏（频谱+副窗并排）、超宽屏（三栏）；用 `QSettings` 保存 `QSplitter::saveState()` 和 Dock 位置 |
| 横竖屏 Dock 变体（同 id 不同 xml） | 同一 `QDockWidget` 内容 widget 在不同布局下重新 `setLayout()` | 控制面板在窄屏时竖排、宽屏时横排，用同一个 widget 换 layout 而非两套 widget |
| 驾驶模式（场景化布局预设） | 预设布局方案：`DefaultLayout` / `FocusLayout`（仅频谱+参数）/ `MultiPanelLayout` | 提供"专注模式"（隐藏所有面板，仅频谱全屏）、"分析模式"（频谱+瀑布+参数三栏），一键切换 |

### 8.7 可直接落地的 Top 10 设计要点

1. **主区铺满 + 浮动面板叠加**：不要用三段式固定布局，频谱主区铺满中央，控制面板用浮动 `QDockWidget` 叠加
2. **QSplitter 可拖拽分区**：主区与副窗之间放 `QSplitter`，stretch 16:10，handle 宽 4px，双击重置
3. **副窗可折叠为窄条**：右侧 AI/状态面板 `setMaximumWidth()` 动画折叠到 32-40px，仅留图标条
4. **Design Token 单例**：所有颜色/圆角/间距收敛到一个 `tokens.py`，QSS 用变量注入，深浅主题只切 token
5. **暗色纯黑 + 三档灰**：频谱背景 `#000000`，面板 `#181818`，弹窗 `#242424`，悬浮 `#2C2C2C`
6. **文字只用黑白 × alpha**：主要 100% / 二级 80% / 三级 60% / 四级 40%，不另设文字色板
7. **大圆角胶囊按钮 + 9dp 卡片**：主按钮 `border-radius: 18px`，内容卡片 9px，搜索框全圆角
8. **按压用 12% 黑叠层**：`QPushButton:pressed { background: rgba(0,0,0,12%) }`，不用描边变色
9. **动效 150/300ms 两级**：反馈 150ms OutCubic，转场 300ms OutCubic，退场比入场快一倍
10. **布局预设 + 状态持久化**：提供专注/分析/多面板预设，`QSettings` 保存 `QSplitter` 和 Dock 状态

---

## 附录：分析产物索引

| 文件 | 内容 | 行数 |
|---|---|---|
| `01_manifest_window.md` | Manifest 窗口属性、服务架构、平行视界线索、动态窗口形态 | 181 |
| `02_layout_hierarchy.md` | 657 个 layout 枚举、核心视图树、主界面分区还原、平行视界证据 | 300 |
| `03_visual_animation.md` | 配色 token、尺寸间距、主题样式、drawable、动画资源、车机适配 | 291 |

> 中间产物路径：`/home/user/.doubao/agent_mode/workspace/.sessions/38438160041798146/agents/o_000cMu1m2Bk/scratch/analysis/`

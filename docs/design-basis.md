# MBDSDR 设计基准对照（design-basis）

> 状态：初稿，待评审。产出人：UX 修复线。冻结期内**不改产品代码**，本文只做取证与判定。
> 判定口径：每条判定必须能指到「node id / 原型锚点 / 参考项目证据 / 基线测试证据」四者之一；指不到的一律写「无依据」，不许写「大致符合」。

## 0. 先说一个必须承认的基准事实

**Figma 导出的 30 个节点不是「30 个界面」，而是同一张概念稿被切成 30 片。**

**尺寸核实边界（先按「不知道就别写已核实」记）**：**宽 1920 已核实**；**整屏高未核实**——
导出物里能核实到的只有 `width: 1920px`（`284_76`/`284_92`/`284_643`/`284_644`）和
顶/底栏 `height: 100px`，**没有任何整屏高度声明**；`desktop/tokens.py` 文件头记
「画布 1920x1200」，另有 1080 之说，两者都无法在导出物里复核（最大单张切片只有
592×1000 / 552×982，不是整屏矩形，位图资产里也没有 1920×1200 或 1920×1080 的整屏图）。
**因此本文不把 1080 或 1200 当事实写进任何结论表。** 证据：

- `D:\mbdsdr\.figma\284_76\index.module.scss` 与 `284_92`、`284_643`、`284_644` 都是 `width: 1920px; height: 100px`（整幅横条）；
- `284_586`、`284_618`、`284_644` 反复出现 `Starboy`、`The Weeknd`、`2:20 / 5:20`、`23.5`、`20:36`、`468km`——这是**音乐播放器 + 天气/里程**的网页概念画面，不是专业无线电工作台；
- `284_633` 的文案是「您想去往哪里？」+ 搜索/home/work 图标，是**出行类应用的搜索框**；
- 大量节点（`284_88/89/90/91/119/120/121/125/126/127/128/129/612/613/614`）只有单个 `rectangleNN`/`img`，是纯装饰矩形或位图切片。

**结论：Figma 这一份只能当「深色、密集、圆角卡片、浮层 HUD」的视觉基调参考，不能当专业工作台的布局蓝图；信息架构基准以原型 `dist/index.html` 为准。** 若用户本意是让 Figma 当布局基准，需要补充真正的专业形态设计稿——这一条请评审时确认。

## §1 30 个 Figma 节点逐条

| node id | 它是什么 | 判断依据（jsx / scss 片段） | 对应原型哪一块 | 主界面 |
| --- | --- | --- | --- | --- |
| `284_76` | 概念稿顶栏：时间 20:36、里程 468km、进度条、消息图标 | jsx `a2036` / `a468Km`；scss `1920×100` | 原型顶栏 `index.html:L404-L424` | 是（概念） |
| `284_88` | 左侧栏背景占位（半透明大矩形） | 仅 `rectangle1072`；scss `552×982` + 低透明灰 | 星象仪侧栏 `index.html:L427-L507` | 否 |
| `284_89` | 圆形控件/头像基座 | 仅 `rectangle63`；scss `130×130`，`border-radius:84px` | 无对应（装饰） | 否 |
| `284_90` | 小圆角控件（65×65） | 仅 `rectangle64`；`border-radius:42px` | 无对应（装饰） | 否 |
| `284_91` | 同 `284_90`，另一颗圆角控件 | 仅 `rectangle65`；`65×65` | 无对应（装饰） | 否 |
| `284_92` | 概念稿底栏：播放/暂停、进度、音量、Starboy | jsx `playPause`/`track`/`vol`/`starboy`；scss `1920×100` | 原型底部工具栏 | 是（概念） |
| `284_119` | 按钮占位（120×42） | 仅 `rectangle1073`；scss `120×42` | 无对应（装饰） | 否 |
| `284_120` | 按钮占位（120×42） | 仅 `rectangle1081`；`120×42` | 无对应（装饰） | 否 |
| `284_121` | 按钮占位（120×42） | 仅 `rectangle1084`；`120×42` | 无对应（装饰） | 否 |
| `284_123` | 矢量图标（21×11） | jsx `<img src="../image/*.svg">` 类名 `vector57` | 无对应（图标） | 否 |
| `284_124` | 小按钮占位（89×42） | 仅 `rectangle1078`；`89×42` | 无对应（装饰） | 否 |
| `284_125` | 按钮占位（120×42） | 仅 `rectangle1075`；`120×42` | 无对应（装饰） | 否 |
| `284_126` | 小按钮占位（89×42） | 仅 `rectangle1079`；`89×42` | 无对应（装饰） | 否 |
| `284_127` | 按钮占位（120×42） | 仅 `rectangle1074`；`120×42` | 无对应（装饰） | 否 |
| `284_128` | 矩形占位 | 仅 `rectangle1080`；单 div，无文案 | 无对应（装饰） | 否 |
| `284_129` | 矩形占位 | 仅 `rectangle1076`；单 div，无文案 | 无对应（装饰） | 否 |
| `284_130` | 长横条（1175×79），疑似顶栏/分隔区 | 仅 `rectangle1077`；scss `1175×79` | 未能判定，需用户确认 | 未能判定 |
| `284_131` | 控件组片段（`frame137`/`autoWrapper`/`ellipse19`/`rectangle1081` 嵌套） | jsx L5-L80 多层嵌套，无业务文案 | 未能判定，需用户确认 | 未能判定 |
| `284_177` | 大方圆角容器（334×334） | 仅 `rectangle1083`；圆角 334 | 未能判定，需用户确认 | 未能判定 |
| `284_178` | 细长条（473×5） | 仅 `rectangle1085`；`473×5` | 无对应（分隔线/进度条） | 否 |
| `284_567` | 概念稿主画布：592×1000 圆角深色容器 + 3 张位图 + 点阵 ellipse | jsx `frame1`/`rectangle3`/`frame21`/`aRemoval1-3`；scss `592×1000` | 星象仪全屏画布 `index.html:L427-L507` | 是（概念） |
| `284_586` | 音乐内容卡（Starboy / The Weeknd / 下载图标） | jsx 文案 `Starboy`、`音乐`、`The Weeknd` | 无对应（概念稿内容，产品无此功能） | 否 |
| `284_599` | 退出/返回控件（「退出」+ 返回箭头 + 地图底图） | jsx 文案 `退出` + 返回 `img` + 底图 | 星象仪退出入口（原型 HUD） | 是 |
| `284_612` | 细分隔条（8×100） | 仅 `rectangle6`；scss `8×100` | 无对应（装饰） | 否 |
| `284_613` | 矩形占位 | 仅 `rectangle7`；单 div | 无对应（装饰） | 否 |
| `284_614` | 位图切片 | jsx `<img src="../image/mw0uh155-cm6dnhw.png" class=frame7>` | 无对应（装饰） | 否 |
| `284_618` | 播放器控件组（播放列表/进度/2:20 / 5:20/收藏） | jsx 文案 `2:20`、`5:20` + 多个图标 | 无对应（概念稿） | 否 |
| `284_633` | 搜索面板：「您想去往哪里？」+ 搜索/home/work 图标 | jsx `<p class=text>您想去往哪里？</p>` + 3 个 svg | 原型顶栏搜索胶囊 `index.html:L404-L424` | 是 |
| `284_643` | 顶栏（与 `284_76` 同类）：20:36 / 468km / 消息 | jsx 同 `284_76` 文案，scss 1920×100 | 原型顶栏 | 是（概念） |
| `284_644` | 底栏（与 `284_92` 同类）：23.5° + Starboy + 播放 + 音量 | jsx `a235`/`starboy`/`playPause`/`vol`；scss 1920×100 | 原型底部工具栏 | 是（概念） |

**本批统计：30 个节点，确认身份 26 个（其中主界面级 7 个：76/92/567/599/633/643/644），4 个需用户确认。** 重复切片 4 组（76↔643、92↔644、90↔91、119/120/121/125/126/127 同一按钮位）。

### §1.1 待用户确认的 4 个节点（拿这张小表去问）

| node id | 已知事实 | 我卡在哪 | 需要用户回答 |
| --- | --- | --- | --- |
| `284_130` | 单矩形 `rectangle1077`，`1175×79`，无文案 | 宽度接近整幅却只有 79 高，可能是顶栏/分区底板，也可能是装饰条；无文案无法定位 | 这条长条在产品里对应哪块？ |
| `284_131` | 嵌套 `frame137`/`autoWrapper`/`ellipse19`/`rectangle1081`，jsx L5-L80，无业务文案 | 有真实层级结构但全是无字容器，无法命名 | 这块是卡片容器、列表行还是弹窗？ |
| `284_177` | 单矩形 `rectangle1083`，`334×334` 全圆角 | 圆形大容器，可能是头像/星球/图标基座 | 它是装饰还是可点控件？ |
| `284_586` | 文案 `Starboy`/`音乐`/`The Weeknd` + 下载图标 | 概念稿里的音乐卡；产品当前没有音乐播放功能 | 星象仪要不要保留背景音乐？若不要，这 4 条（586/618/92/644 的音乐部分）只能当视觉参考 |

## §1.2 天空形态的基准边界（并入天空线 `docs/design-basis-sky-figma-note.md` 的 §3/§4，提交 `b2d689d`）

天空线的 §0/§1 与本文 §0/§1 重复，这里只并两块：**两份基准能给什么、不能给什么**。

**两条硬证据（可复跑，命令见下）**：

- `index.html` 的 `#skyCanvas` 脚本段内，`planet` / `moon` / `sun` 各 **0 次**；全文 `planet`
  只出现 6 次，**全部是 i18n 文案里的 “Planetarium Mode / 星象仪模式”**；`moon` 仅 1 处
  `h_moon`（只用于地平亮度模型，不是月面绘制）；`sun`（不区分大小写）19 次全部用于昼夜亮度与大气。
- `.figma` 30 个节点里**没有天空屏**：全目录搜 `星空/天空/望远镜/频谱/卫星/月球/月相/SDR` 均 0 命中；
  搜 `Star` 只命中歌曲名 `Starboy`（`284_586`/`284_644`/`284_92`），不是天文恒星。

**结论（写死）**：**两份基准都没有月亮 / 行星绘制。** 因此月相亮区（亮缘半圆 + 晨昏线半椭圆）、
月面朝向、未知月相占位、行星窄视场圆面填亮区、行星识别色——**全部属纯自拟**
（`806f207` / `5332140`），保留/修正/回退必须在本表单独裁决，不许拿「原型里应该有」当理由。

| 用途 | 允许 | 说明 |
| --- | --- | --- |
| 颜色 / 圆角 / 字号 / 间距 / 动效 | 可以（视觉语言） | 走 `desktop/tokens.py` 的 CarWith 令牌，不散落魔法数字 |
| 数据块排布、卡片密度、对比层级、状态呈现 | 可以（视觉语言） | 只参考排布与层级，不照搬车机屏结构 |
| 天空屏布局、图层顺序、天体符号形状 | **不可以** | 结构依据只认 `index.html` |
| 月相/行星绘制的几何与外观 | **不可以** | 两份基准都没有对应物，须单独裁决 |

复跑命令（照抄天空线 note §5，全部只读）：

```powershell
$all=[IO.File]::ReadAllText('D:\mbdsdr\output\mbdsdr-product\dist\index.html')
foreach($k in '月相','月亮'){ "$k`t$([regex]::Matches($all,$k).Count)" }          # 应 0 / 0
$i=$all.IndexOf('#skyCanvas'); $end=$all.IndexOf('</script>',$i); $seg=$all.Substring($i,$end-$i)
foreach($k in 'planet','moon','sun'){ "$k`t$([regex]::Matches($seg,$k).Count)" }  # 应 0 / 0 / 0
Select-String -Path D:\mbdsdr\.figma\* -Pattern "星空|天空|望远镜|频谱|卫星|月球|月相|SDR"   # 应无输出
```

## §2 逐屏对照表

| 屏幕 / 面板 | 基准来源 | 当前实现 | 判定 | 偏离具体点 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 顶栏（品牌 + 模式切换 + 搜索） | 原型 `index.html:L404-L424`（`MBDSDR` / `AI 定义无线电 · 时空操作台` / `星象仪模式/专业模式` / 搜索胶囊） | `desktop/main_window.py:MainWindow._build_menu_bar` + `QToolBar`；`select_tab` 负责页签定位 | 偏离 | 无品牌副标题条；无常驻搜索胶囊（只有菜单项「命令面板 Ctrl+K」）；模式切换不在顶栏，走菜单/快捷键 | `desktop/main_window.py:_show_command_palette`、`select_tab` |
| 顶栏（模式切换归属） | 原型 `index.html:L427-L507`（星象仪为透明浮层顶栏） | `_enter_planetarium` / `_exit_planetarium` + `planetariumStrip` | 部分一致 | 概念方向对（浮层），但入口位置不在顶栏 | `desktop/main_window.py:_enter_planetarium`（L~1853 `planetariumStrip`） |
| 星象仪画布 + 侧栏 + HUD | 原型 `index.html:L427-L507`；Figma `284_567`（画布）、`284_88`（侧栏底） | `desktop/rf_sky_view.py:RFSkyView` + 右上浮层 | 偏离 | 无侧栏导航、无卫星信息卡、无缩放按钮；HUD 只有 152px 小浮层（退出+录音） | `desktop/main_window.py:_position_planetarium_overlay`（L4967） |
| 星象仪退出 / 返回 | Figma `284_599`（「退出」+ 返回箭头） | `planetariumExit` 按钮（文案「退出星象仪\n(Esc)」，最小高 44） | 一致（机制）/ 偏离（外观） | 位置改为右上浮层；Figma 概念里是左上返回 | `desktop/main_window.py:_build_planetarium_page`（`exit_btn.setObjectName("planetariumExit")`）；`tests/test_planetarium_entry_visibility.py:test_planetarium_exit_entry_is_readable` |
| 专业形态主区（频谱 + 瀑布） | 原型「专业模式」工具域 `index.html:L1081-L1126` | `desktop/spectrum_widget.py:create_spectrum_widget` + 左栏 `QTabWidget` | 偏离（无逐屏基准） | Figma 没有专业形态设计稿，当前频谱/瀑布布局无 node 可指 | `desktop/main_window.py:_build_central_widget` |
| 右栏页签（控制/AI/设置/天气/过境/时空/模块） | 原型工具域分区；参考 satdump `src-ui/imgui` 的分组式面板 | `desktop/control_panel.py:ControlPanel`、`ai_panel.py:AIPanel`、`settings_panel.py:SettingsPanel`、`weather_panel.py:WeatherPanel`、`sat_track_panel.py:SatTrackPanel`、`new_spacetime_panel.py:NewSpacetimePanel`、`module_panel.py:ModulePanel` | 未实现（按基准） | 页签是并列摊开的，无主次分级；原型里「专业模式」有主/次分区 | `desktop/main_window.py` import 段（`from control_panel import ControlPanel` 等） |
| 底部状态栏 | 原型 `index.html:L427-L507`（HUD/状态 chip）；Figma `284_644`（23.5° + 音乐条，概念稿内容） | `desktop/status_panel.py:StatusPanel` + `QStatusBar`（三档时长 `MSG_QUICK/NORMAL/IMPORTANT`） | 偏离 | 信息密度低于原型；无状态 chip 化的模式/连接指示 | `desktop/main_window.py:MSG_QUICK/NORMAL/IMPORTANT` |
| 无设备 / 空态 | 无基准（原型与 Figma 都没有「无设备」画面）；参考 satdump：设备未开时给固定默认范围 `usrp_sdr.cpp:L261-L268` | `_NO_SDR_HINT` 横幅 + `_no_device_hint`（单行不换行，`e6f0e7d` 起） | 我们多做的 | 基准里没有对应画面；横幅与状态栏仍双份（wordWrap 已撤，见 §3） | `desktop/main_window.py:_NO_SDR_HINT`；`tests/test_no_device_onboarding.py` |
| 参数面板分组 / 恢复默认 | 参考 satdump `usrp_sdr.cpp:L55-L106`（连接后重算可用采样率并回默认）；sdrpp 无对应可指 | `desktop/settings_panel.py:ServiceSettingsPanel`（`reset_btn` + `_restore_from_settings`） | 偏离 | 无分组容器语义（`RemoteControl`/`Web` 两个 groupbox 之外不分层）；恢复默认不给可见反馈 | `desktop/settings_panel.py:ServiceSettingsPanel._restore_from_settings` |
| 大列表（卫星过境） | 参考 stellarium `guide/README.md`（星表/天体列表按类别分层） | `desktop/new_spacetime_panel.py:NewSpacetimePanel`（`QTableWidget`） | 偏离 | 表头无排序/过滤；空态刚补文案（别的线 `44371b4`） | `desktop/new_spacetime_panel.py:refresh_passes` |
| 书签列表 | 参考 sdrpp 频率书签（`reference/sdrpp`，机制：内存表 + 组合框） | `desktop/control_panel.py:ControlPanel`（`bookmark_combo` + 「收藏当前频率」） | 一致（机制） | 无搜索/分类；靠下拉框浏览 | `desktop/control_panel.py` 收藏分组 |

## §3 已提交视觉改动逐条重判

| commit | 改了什么 | 依据 | 判定 | 理由 / 回退要动的符号 |
| --- | --- | --- | --- | --- |
| `b696e48` | 侧栏 `setFixedWidth(tokens().SIZE["collapse_strip"])` → 硬编码 `132`；退出按钮改「退出星象仪\n(Esc)」+ 最小高 44；新增 `planetariumRecIndicator` | 无基准（概念稿 `284_599` 只有「退出」二字） | **按基准修正** | 令牌→魔数违反参数治理铁律，必须撤；文案与 44px 触控高可保留。注意：`132` 在 `e18a8c4` 后已不存在，现在是 `152`。回退要动 `desktop/main_window.py:_build_planetarium_page` 里的 `strip.setFixedWidth(...)`（HEAD `02a56db` L1857；带他人 WIP 的工作区在 L1902）+ `desktop/tokens.py:DesignTokens.SIZE`（L313） |
| `e18a8c4` | 撤 132px 侧栏，改右上浮层：宽 152、`rgba(18,19,22,0.55)`、圆角 10、边距 12 | 概念方向对（原型星象仪是浮层 HUD `index.html:L427-L507`）；具体数值无基准 | **保留（数值待定）** | 浮层方向成立（96px 常驻占位 vs 遮一隅，代价可算）；但 152/0.55/10/12 四个数在 Figma 里指不到 → 等 §1 确认后再锚定。回退要动 `_planetarium_overlay_pos`（L124）、`strip.setFixedWidth`+QSS（L1857-1865） |
| `f625944` | 与 AI 命令栏相交才下移「命令栏底+8」，否则回 12 | 无基准（纯行为规则） | **保留** | 不改变像素风格，只保证发送/麦克风不被遮；属可用性硬约束。回退要动 `_planetarium_overlay_pos`（L124-L133）+ `_position_planetarium_overlay`（L4967） |
| `e86e16d` | 横幅换 `_NO_SDR_HINT` 长文案 + `setWordWrap(True)`；设备框加占位项、OK 置灰、`deviceResultLabel`；帮助菜单加「命令面板」 | 文案无基准；wordWrap 无基准 | **按基准修正（撤 wordWrap）→ 已执行 `e6f0e7d`** | 先红：带 wordWrap 时 1000×600 频谱归一化 **320×228**，基线 320×240，`Δh=-12` 判几何漂移（实跑红）。后绿：只删一行 wordWrap，四档基线全过，1000×600 回到 **320×241 / 1280×720 198 / 1600×900 192 / 1920×1080 189**（基线 240/197/191/189，均在 ±2px 取整容差内）。改动符号：`desktop/main_window.py:_build_central_widget` 的 `self._no_device_hint.setWordWrap(True)` 一行 + `tests/test_no_device_onboarding.py` 的 `wordWrap() is True` 断言（该断言本身就是 e86e16d 一并加的，随本笔翻成 `False`）。**替代方案（单行省略 + 完整文案进 tooltip）另案，未混入本笔。** |

已执行的后续两笔（与上表一一对应，供评审对账）：

- `e6f0e7d`：撤 `e86e16d` 的 wordWrap（上表第 4 行）。
- `5ba61b7`：`strip.setFixedWidth(152)` → `tokens().SIZE["planetarium_strip_w"]`（上表第 1 行的令牌回收）。
  像素等价证据：同尺寸 1280×800 星象仪截图，`strip` 9120 px、整窗 640000 px **逐像素 diff = 0**。

取证附带发现（`af7208b` 已修）：`test_ui_layout_locks.py` 原来的 `800x600` 参数**实际跑在 1000×600**。
根因是 `desktop/main_window.py:L548` 的 `self.setMinimumSize(1000, 600)`——显式硬下限，`resize(800, 600)` 被夹回 1000 宽。
`af7208b` 已删掉伪 800 档，改为两条真正的夹取断言：`test_request_800_is_clamped_to_declared_1000`
（断言请求 800 实得 1000×600）与 `test_window_never_shrinks_below_declared_minimum`。
最小宽口径同步更新为实测 `HEAD 5ba61b7`：**`minimumSizeHint()` = 1625×729**（随左栏当前页可到 740），
其中 1625 由**顶栏 `topBar`** 驱动、并非频谱工具栏（频谱页最小宽 1941 只造成左栏内部挤压）。窄屏改法见
[narrow-screen-plan.md](narrow-screen-plan.md)。

## §4 差距最大的 10 处（按用户可感知度排序）

| # | 基准要什么 | 现在是什么 | 差在哪 | 改法要点 |
| --- | --- | --- | --- | --- |
| 1 | 顶栏有「专业模式 / 星象仪模式」切换 + 搜索胶囊（`index.html:L404`） | 只有菜单栏与工具栏，「命令面板」是菜单项 | 两个形态切换成本高；搜索不可见 | 顶栏加模式切换段 + 常驻搜索框，复用 `select_tab` 与命令面板 |
| 2 | 星象仪有侧栏导航 + 卫星信息卡 + 缩放按钮 + HUD（`index.html:L427`） | `RFSkyView` + 152px 小浮层 | 星象仪信息密度远低于基准 | 先做只读信息卡（选中卫星/视场角/极限星等），再谈侧栏 |
| 3 | 专业形态有明确主/次分区（工具域 `index.html:L1081`） | 右栏 7 个页签并列 | 没有主次，用户找不到重点 | 页签分组（观测/解码/工具），默认页按任务排序 |
| 4 | 无设备引导是「基准外」的我们多做项 | 横幅 + 状态栏双份引导；横幅已改单行不换行（`e6f0e7d`） | 仍是双份占位：横幅独占一行高度 | wordWrap 已撤（§3）；下一步把「横幅 / 状态栏」合成一处主位置 + 单行省略 + tooltip 全文（另案，未混入撤 wordWrap 那一笔） |
| 5 | 顶栏/底栏承担状态信息（`284_76`/`284_644`） | 全压到底部 `QStatusBar` | 信息集中在最不显眼处 | 连接/录音/过境状态升级为常驻 chip |
| 6 | 参数面板分组 + 恢复默认有反馈（satdump `usrp_sdr.cpp:L55`） | `ServiceSettingsPanel` 两个 groupbox + 无反馈的 reset | 分组粒度粗，reset 看不到变化 | 分组按子系统，reset 后给「已恢复 N 项」 |
| 7 | 大列表可筛选（stellarium `guide/README.md`） | `QTableWidget` 无过滤 | 过境表越长越难用 | 加列头排序 + 就地过滤框 |
| 8 | 能力矩阵/命令面板作为「发现层」（`index.html:L509`） | 帮助菜单里的条目 | 发现路径埋在菜单 | 与 §1 `284_633` 搜索面板合并成一个入口 |
| 9 | 深色 + 密集 + 少留白，且视觉数值可复现 | `tokens().SIZE` 只有 5 个尺寸键；浮层 152 / `0.55` / 10 / 12 全手写 | 间距/圆角/浮层宽没有系统 token，评审无处可指，下次必再手写 | 把间距、圆角、浮层宽/透明度收进 `desktop/tokens.py:DesignTokens`；本次令牌补丁（像素不变）是第一步 |
| 10 | **1366×768 笔记本必须能完整显示** | 实测（`scripts/ui_narrow_breakdown.py`）：`minimumSizeHint()` = **1625×729**；1366×768 扣任务栏可用约 **728** | **宽度差 259；高度差 1**。三处宽度溢出：顶栏 `topBar` 最小宽 **1625**（隐藏 topBar 后窗口降到 808，主工具栏隐藏无影响——它只挤压自身）、主工具栏可用下限 **1670**（QToolBar 自报 52 是假的）、频谱工具栏 **1941**（1366 下频谱实际只有 756 宽）。高度 729 = 菜单栏 24 + 主工具栏 39 + 中央 644 + 状态栏 22；中央 644 = 顶栏 56 + 左栏 514 + 底坞 74 | **结论：1366×768 上当前无法完整显示**，属产品可用性问题，须记录。改法见 [narrow-screen-plan.md](narrow-screen-plan.md)。**本行只记录问题，不改代码。** |

## §5 硬规则（可执行、可检查）

1. **留痕规则（先指依据，再改代码）**：任何 UI 改动（位置、尺寸、颜色、层级、控件增删、浮层、横幅、置灰样式）提交前，必须能指到下面四者之一：① `index.html` 锚点（`Lxxx` 行号）；② Figma 节点做法（`node id` + 具体位置，如圆角/密度/层级）；③ 参考项目做法（`文件:符号`）；④ 现有基线测试证据。**指不到就必须在 §2/§3 显式标「自拟」，并写理由与预期收益**——不标「自拟」又指不到依据的提交视为违规。
2. **参照是参考，不是像素级硬约束**：三份基准给的是「这块该有什么、主次怎么排、状态怎么呈现」，不是「必须 152px、必须 rgba(…,0.55)」。Figma 这份还是张概念稿（§0），更只能取视觉基调。只有**与基准直接冲突**的才算偏离（例：原型顶栏有搜索胶囊，我们把它埋在菜单 = 偏离；浮层宽度取 152 而非 132 = 自拟，不算偏离）。
3. **像素常量只能来自 `desktop/tokens.py:DesignTokens`**（或先加 token 再引用）；业务代码出现裸像素数字即违规。`b696e48` 的 `setFixedWidth(132)` 已登记为反例。
4. **基线文件不动**：`tests/baselines/*.png` 在「不改用户可见设计」前提下不许改；确需改必须走 `ui_baseline` 的 reason + 几何变更双开关，并在本文登记。
5. **判定只认证据**：写「一致」必须贴 node id / 原型行号 / 测试断言；写不出就标「自拟」。「大致符合」一律无效。
6. **两形态独立判定**：星象仪与专业形态各自对照，不许拿一个形态的基准去改另一个形态。
7. **顺序**：先更新本文的判定行，再提代码；判定行与代码同一次提交，避免「文档说改了、代码没改」或反向漂移。

## 待用户确认的问题

1. Figma 这份（概念稿/音乐播放器画面）是否本来就只是**视觉基调**参考？若你以为它是专业形态布局基准，需要补真稿。
2. `284_130`（1175×79 长条）、`284_131`（嵌套控件组）、`284_177`（334×334 圆形容器）、`284_586`（Starboy 音乐卡）四处无法判定，逐条问题见 **§1.1 小表**。
3. `284_586`/`284_618`（Starboy 音乐卡）是否产品要保留的「星象仪背景音乐」功能？现在产品里没有。
4. 浮层四个数值（152 / 0.55 / 10 / 12）：152 已按你的口径收进 `tokens().SIZE["planetarium_strip_w"]`（`5ba61b7`，像素不变）；**0.55 / 10 / 12** 仍无基准，是否接受「自拟但冻结」。
5. §0 的整屏高：`tokens.py` 记 1200、另有 1080 之说，导出物都无法复核。**若你知道真值，请直接给**；否则本文按「未核实」处理。
6. 窄屏方案 [narrow-screen-plan.md](narrow-screen-plan.md) 需拍板：1366×768 目标是否含 Windows 任务栏（决定高度要省 12px 还是 1px），以及能否接受「隐藏菜单栏」这一行为变化。

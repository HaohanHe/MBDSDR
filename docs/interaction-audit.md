# 交互覆盖审计（死按钮 / 假按钮 / 静默异常）

> 由 `scripts/ui_interaction_audit.py` 运行时遍历生成，请勿手改。
> 环境：offscreen + `MBDSDR_NULL_AUDIO=1` + `MBDSDR_NO_HW=1` + `MBDSDR_OFFLINE=1`，HOME/QSettings 隔离到临时目录。
> 口径：`receivers()==0` = 真死；有接收者但触发后无状态/信号下游/窗口/弹窗变化 = 假活；触发抛出或被 Qt 吞掉的异常单列。
> 防误报：触发前先空跑事件循环，把 UTC 时钟/SNR 读数等自变字段识别为 volatile 并从 diff 里剔除；运行时新建的同类对话框按「标题+控件名」去重。

## 结论

- 遍历动作 **219** 个（QAction 41 / QAbstractButton 178；enabled 184 / visible 91）
- **真死** 0 个
- **假活** 0 个
- **抛异常** 0 个
- 诚实禁用 35 个；未实时触发 9 个；输入控件 2 个

## 一、真死（receivers() == 0，点了不可能有反应）

无。

## 二、假活（有接收者，但触发后无可观察变化）

无。

## 三、抛异常（同步抛出 / 被 Qt 吞掉只打到 stderr）

无。

## 四、已登记的假活白名单（幂等/状态相关，待他线改）

> 本轮复现情况随设备枚举结果变化；白名单里的每一条都要有理由，
> pytest 守卫也读这份表（`FALSE_ALIVE_WHITELIST`）。

| 动作 | 理由 |
| --- | --- |
| 按钮\|刷新\|DevicePanel | `device_panel._refresh_devices` 确实重枚举设备并把状态写成「检测到 N 台设备」；设备集合/状态未变时界面零变化（幂等刷新，非死按钮）。可选改进：每次刷新都走状态栏提示，需改 `desktop/device_panel.py`，归操作逻辑线，已登记限制总账 |
| 按钮\|刷新设备列表\|对话框:选择 SDR 设备 | `main_window._reload_devices` 重跑 enumerate 并重建下拉框；设备集合不变时文本/索引一致（幂等刷新，非死按钮）。可选改进：刷新后给「检测到 N 台设备」提示，需改 `desktop/main_window.py`，归操作逻辑线，已登记限制总账 |

## 五、诚实禁用但没写为什么

无。

## 附：全量明细

| 动作 | 类型 | 所在面板 | 启用 | 可见 | 有槽 | 可观察结果 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 主工具栏 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='—' visible=0 -> text='—' visible=1；QToolButton(qt_toolbar_ext_button): text='' checked=0 enabled=1 visible=0 -> text='' checked=0 enabled=1 visible=1；QPushButton(): text='+ VFO' checked=0 enabled=1 visible=0 -> text='+ VFO' checked=0 enabled=1 visible=1；QPushButton(): text='切换主听' checked=0 enabled=1 visible=0 -> text='切换主听' checked=0 enabled=1 visible=1；QAction(主工具栏): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1 | 可用 |
| 工具(&T) / FM 扫频找台 | 菜单项 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 工具(&T) / NTRIP 设置 | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：NTRIP 设置；新窗口：NTRIP 设置 | 可用 |
| 工具(&T) / 停止卫星跟踪 | 菜单项 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 工具(&T) / 关于 MBDSDR | 菜单项 | MainWindow | 是 | 是 | 是 | 弹窗：关于「关于 MBDSDR」<h3>MBDSDR - AI 定义无线电</h3><p>版本 v3.14.150.1013</p><p>开源 MIT 许可的软件定义 | 可用 |
| 工具(&T) / 开始/停止录音 | 菜单项 | MainWindow | 是 | 是 | 是 | 弹窗：警告「无法录音」未连接 SDR，无法录制 baseband。<br>请先连接真实 SDR 设备。；界面变化：QLabel(): text='未连接' visible=1 -> text='录音完成 (0 秒)' visible=1 | 可用 |
| 工具(&T) / 开始跟踪选中卫星 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：需要观测者位置：连接 GNSS 或在 gui_config.json 配置 observer_lat/lon | 可用 |
| 工具(&T) / 断开 GNSS | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：GNSS 已断开 | 可用 |
| 工具(&T) / 更新卫星 TLE (Celestrak) | 菜单项 | MainWindow | 是 | 是 | 是 | 未实时触发：联网拉 Celestrak TLE | 未实时触发（代码核对） |
| 工具(&T) / 连接 GNSS... | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：连接 GNSS | 可用 |
| 帮助(&H) / 三步上手 | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：帮助与能力目录；新窗口：帮助与能力目录 | 可用 |
| 帮助(&H) / 命令面板 | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：命令面板；新窗口：命令面板 | 可用 |
| 帮助(&H) / 快捷键一览 | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：快捷键一览；新窗口：快捷键一览 | 可用 |
| 帮助(&H) / 能力目录 | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：帮助与能力目录；新窗口：帮助与能力目录 | 可用 |
| 文件(&F) / 断开连接 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：当前未连接 SDR，没有可断开的设备；界面变化：QLabel(): text='空闲' visible=0 -> text='已停止' visible=0 | 可用 |
| 文件(&F) / 连接设备... | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：选择 SDR 设备 | 可用 |
| 文件(&F) / 退出 | 菜单项 | MainWindow | 是 | 是 | 是 | 未实时触发：关闭主窗口退出进程；等价于 win.close()，审计窗口还要继续用，不真退出 | 未实时触发（代码核对） |
| 视图(&V) / 主题 / CarWith 深色 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(CarWith 深色): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QLabel(statusValue): text='UTC 18:17:03' visible=1 -> text='UTC 18:17:04' visible=1；QComboBox(): idx=2 text='高对比' -> idx=3 text='CarWith 深色' | 可用 |
| 视图(&V) / 主题 / Figma 深色 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='UTC 18:17:04' visible=1 -> text='UTC 18:17:06' visible=1；QComboBox(): idx=3 text='CarWith 深色' -> idx=4 text='Figma 深色'；QAction(Figma 深色): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1 | 可用 |
| 视图(&V) / 主题 / 深色 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='UTC 18:16:59' visible=1 -> text='UTC 18:17:01' visible=1；QComboBox(): idx=0 text='默认' -> idx=1 text='深色'；QAction(深色): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1 | 可用 |
| 视图(&V) / 主题 / 高对比 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='UTC 18:17:01' visible=1 -> text='UTC 18:17:03' visible=1；QComboBox(): idx=1 text='深色' -> idx=2 text='高对比'；QAction(高对比): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1 | 可用 |
| 视图(&V) / 主题 / 默认 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='UTC 18:16:57' visible=1 -> text='UTC 18:16:59' visible=1；QAction(默认): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QComboBox(): idx=3 text='CarWith 深色' -> idx=0 text='默认' | 可用 |
| 视图(&V) / 全天模式（160°） | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(): text='看整片天' checked=0 enabled=1 visible=0 -> text='正在看整片天' checked=1 enabled=1 visible=0；QAction(全天模式（160°）): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1 | 可用 |
| 视图(&V) / 全屏 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(planetariumFullscreen): text='⛶ 全屏' checked=0 enabled=1 visible=0 -> text='🗗 退出全屏' checked=0 enabled=1 visible=0；__window__: visible=1 fullscreen=0 maximized=0 minimized=0 geo=1400x900 -> visible=1 fullscreen=1 maximized=0 minimized=0 geo=800x800 | 可用 |
| 视图(&V) / 星象仪模式 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='步进:' visible=1 -> text='步进:' visible=0；QToolButton(topBarAction): text='⧉' checked=0 enabled=1 visible=1 -> text='⧉' checked=0 enabled=1 visible=0；QStackedWidget(): idx=0 -> idx=1；QPushButton(): text='-' checked=0 enabled=0 visible=1 -> text='-' checked=0 enabled=0 visible=0；QToolButton(topBarAction): text='详细' checked=0 enabled=1 visible=1 -> text='详细' checked=0 enabled=1 visible=0 | 可用 |
| 视图(&V) / 显示瀑布图 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(显示瀑布图): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 视图(&V) / 触屏模式 / 强制触屏 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：触屏模式：强制触屏；界面变化：QLabel(statusValue): text='UTC 18:17:06' visible=1 -> text='UTC 18:17:07' visible=1；QAction(强制触屏): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QAction(自动检测): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 视图(&V) / 触屏模式 / 强制鼠标 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：触屏模式：强制鼠标；界面变化：QAction(强制鼠标): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QLabel(statusValue): text='UTC 18:17:07' visible=1 -> text='UTC 18:17:09' visible=1；QAction(强制触屏): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 视图(&V) / 触屏模式 / 自动检测 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：触屏模式：自动检测 | 可用 |
| 视图(&V) / 重置窗口位置 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：窗口已重置到主屏中央；界面变化：__window__: visible=1 fullscreen=1 maximized=0 minimized=0 geo=800x800 -> visible=1 fullscreen=0 maximized=0 minimized=0 geo=860x800 | 可用 |
| 面板(&P) / ADS-B 航路 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(ADS-B 航路): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / AI 助手 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(AI 助手): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QLabel(): text='起始' visible=0 -> text='起始' visible=1；QStackedWidget(qt_tabwidget_stackedwidget): idx=2 -> idx=6；QPushButton(): text='取消' checked=0 enabled=0 visible=0 -> text='取消' checked=0 enabled=0 visible=1；QTabWidget(): idx=2 -> idx=6 | 可用 |
| 面板(&P) / 卫星跟踪 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='卫星:' visible=1 -> text='卫星:' visible=0；QLabel(): text='下行频率:' visible=1 -> text='下行频率:' visible=0；QPushButton(): text='更新 TLE' checked=0 enabled=1 visible=1 -> text='更新 TLE' checked=0 enabled=1 visible=0；QLabel(): text='TLE 日期: 2026-09-25' visible=1 -> text='TLE 日期: 2026-09-25' visible=0；QLabel(): text='卫星闭环自动跟踪' visible=1 -> text='卫星闭环自动跟踪' visible=0 | 可用 |
| 面板(&P) / 恢复出厂布局 | 菜单项 | MainWindow | 是 | 是 | 是 | 弹窗：询问「恢复出厂布局」清除所有自定义布局并恢复到默认「专注」布局？ | 可用 |
| 面板(&P) / 控制 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='步进:' visible=1 -> text='步进:' visible=0；QLabel(statusValue): text='未连接' visible=0 -> text='未连接' visible=1；QPushButton(): text='-' checked=0 enabled=0 visible=1 -> text='-' checked=0 enabled=0 visible=0；QLabel(): text='更新:' visible=0 -> text='更新:' visible=1；QLabel(statusValue): text='—' visible=0 -> text='—' visible=1 | 可用 |
| 面板(&P) / 新时空 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(新时空): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 星象仪模式 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(星象仪模式): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 模块/信号流 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(模块/信号流): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 气象云图 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(气象云图): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 状态 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='未连接' visible=1 -> text='未连接' visible=0；QLabel(): text='更新:' visible=1 -> text='更新:' visible=0；QLabel(statusValue): text='—' visible=1 -> text='—' visible=0；QLabel(): text='序列号:' visible=1 -> text='序列号:' visible=0；QLabel(): text='纬度:' visible=1 -> text='纬度:' visible=0 | 可用 |
| 面板(&P) / 频谱 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(频谱): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 启用 ANR | 按钮 | AnrPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 12；切页签 QTabWidget -> 12；— | 诚实禁用 |
| 采样噪声底 | 按钮 | AnrPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 删除 | 按钮 | BookmarkPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 导入 CSV | 按钮 | BookmarkPanel | 是 | 否 | 是 | 文件框：打开 | 可用 |
| 导出 CSV | 按钮 | BookmarkPanel | 是 | 否 | 是 | 文件框：保存 | 可用 |
| 找最近 | 按钮 | BookmarkPanel | 是 | 否 | 是 | 弹窗：提示「找最近」书签列表为空。 | 可用 |
| 添加当前频率 | 按钮 | BookmarkPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 8；切页签 QTabWidget -> 8；文件框：输入 | 可用 |
| 编辑 | 按钮 | BookmarkPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 清空 | 按钮 | ConstellationPanel | 是 | 否 | 是 | 状态栏：星座图已清空，统计已重置；新窗口：qtooltip_label | 可用 |
| 网格 | 按钮 | ConstellationPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 13；切页签 QTabWidget -> 13；新窗口：qtooltip_label；界面变化：QCheckBox(): text='网格' checked=1 enabled=1 visible=0 -> text='网格' checked=0 enabled=1 visible=0 | 可用 |
| + | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| - | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| AGC 自动增益 | 按钮 | ControlPanel | 是 | 否 | 是 | 界面变化：QLabel(statusValue): text='-12.0 dBFS' visible=0 -> text='-11.7 dBFS' visible=0；QCheckBox(): text='AGC 自动增益' checked=1 enabled=1 visible=0 -> text='AGC 自动增益' checked=0 enabled=1 visible=0 | 可用 |
| DC 去除 | 按钮 | ControlPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='DC 去除' checked=1 enabled=1 visible=0 -> text='DC 去除' checked=0 enabled=1 visible=0 | 可用 |
| IQ 平衡 | 按钮 | ControlPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='IQ 平衡' checked=1 enabled=1 visible=0 -> text='IQ 平衡' checked=0 enabled=1 visible=0 | 可用 |
| 偏移调谐 | 按钮 | ControlPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='偏移调谐' checked=0 enabled=1 visible=0 -> text='偏移调谐' checked=1 enabled=1 visible=0 | 可用 |
| 删除选中 | 按钮 | ControlPanel | 是 | 否 | 是 | 弹窗：提示「内置书签」「航空应急频率 121.500」是内置频率，不能删除。下一步：先调到你要保留的频点，再用「收藏当前频率」保存为你自己的自定义书签。 | 可用 |
| 开始录音 | 按钮 | ControlPanel | 是 | 否 | 是 | 未实时触发：启动录音线程并落盘（control_panel），审计不写真实录音 | 未实时触发（代码核对） |
| 收藏当前频率 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 自动 | 按钮 | ControlPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='自动' checked=1 enabled=1 visible=0 -> text='自动' checked=0 enabled=1 visible=0 | 可用 |
| 调谐 | 按钮 | ControlPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 0；切页签 QTabWidget -> 0；界面变化：QLineEdit(): text='' style='' -> text='' style='background-color:#B85C5C;' | 可用 |
| 刷新 | 按钮 | DevicePanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 14；切页签 QTabWidget -> 14；新窗口：qtooltip_label | 可用 |
| 断开 | 按钮 | DevicePanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(): text='当前设备上限 948.5 MHz，无法接收 1090 MHz；需覆盖 1090 MHz 的前端（如 R820T/R828D）' visible=0 -> text='未连接（等待 1090MHz 数据）' visible=0；QCheckBox(): text='书签' checked=1 enabled=1 visible=0 -> text='书签' checked=1 enabled=0 visible=0；QPushButton(): text='-' checked=0 enabled=1 visible=0 -> text='-' checked=0 enabled=0 visible=0；QLabel(statusValue): text='频率: 98.500 MHz' visible=1 -> text='频率: —' visible=1；QLabel(statusValue): text='32' visible=0 -> text='—' visible=0 | 可用 |
| 连接 | 按钮 | DevicePanel | 是 | 否 | 是 | 状态栏：正在连接 RTL-SDR #0 ...；新窗口：qtooltip_label；界面变化：QLabel(): text='范围: --' visible=0 -> text='设备无此增益档' visible=0；QLabel(): text='Mixer:' visible=0 -> text='Mixer: 不支持' visible=0；QLabel(): text='未连接（等待 1090MHz 数据）' visible=0 -> text='当前设备上限 948.5 MHz，无法接收 1090 MHz；需覆盖 1090 MHz 的前端（如 R820T/R828D）' visible=0；QCheckBox(): text='书签' checked=1 enabled=0 visible=0 -> text='书签' checked=1 enabled=1 visible=0；QPushButton(): text='-' checked=0 enabled=0 visible=0 -> text='-' checked=0 enabled=1 visible=0 | 可用 |
| 硬件 AGC | 按钮 | GainPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 15；切页签 QTabWidget -> 15；— | 诚实禁用 |
| + VFO | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：新建 VFO vfo001 @ 98.500 MHz | 可用 |
| A 星名 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QLabel(statusValue): text='-24.3 dBFS' visible=0 -> text='-25.2 dBFS' visible=0；QLabel(statusValue): text='-24 dBFS' visible=0 -> text='-25 dBFS' visible=0；QPushButton(planetariumLayer_labels): text='A 星名' checked=1 enabled=1 visible=0 -> text='A 星名' checked=0 enabled=1 visible=0 | 可用 |
| ≈ 大气 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumLayer_atmosphere): text='≈ 大气' checked=1 enabled=1 visible=0 -> text='≈ 大气' checked=0 enabled=1 visible=0；QLineEdit(): text='' style='background-color:#B85C5C;' -> text='' style='' | 可用 |
| ≡ 赤道网 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumLayer_eqgrid): text='≡ 赤道网' checked=0 enabled=1 visible=0 -> text='≡ 赤道网' checked=1 enabled=1 visible=0；QLabel(statusValue): text='-23.6 dBFS' visible=0 -> text='-22.8 dBFS' visible=0；QLabel(statusValue): text='-24 dBFS' visible=0 -> text='-23 dBFS' visible=0 | 可用 |
| ⊞ 叠层 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumLayer_hud): text='⊞ 叠层' checked=1 enabled=1 visible=0 -> text='⊞ 叠层' checked=0 enabled=1 visible=0；QLabel(statusValue): text='-22.8 dBFS' visible=0 -> text='-30.5 dBFS' visible=0；QLabel(statusValue): text='-23 dBFS' visible=0 -> text='-30 dBFS' visible=0 | 可用 |
| ⌂ | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 0；界面变化：QLabel(sectionTitle): text='频谱显示' visible=0 -> text='频谱显示' visible=1；QStackedWidget(qt_tabwidget_stackedwidget): idx=0 -> idx=1；QTabWidget(): idx=0 -> idx=1；QLabel(statusValue): text='— MHz' visible=0 -> text='— MHz' visible=1 | 可用 |
| ⏱ 1× | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；状态栏：时间流速 60×（天空按同一时刻重算）；界面变化：QPushButton(planetariumTimeRate): text='⏱ 1×' checked=0 enabled=1 visible=1 -> text='⏱ 60×' checked=0 enabled=1 visible=1 | 可用 |
| ▦ 图层 | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumLayer_landscape): text='▽ 地景' checked=0 enabled=1 visible=0 -> text='▽ 地景' checked=0 enabled=1 visible=1；QPushButton(planetariumLayer_eqgrid): text='≡ 赤道网' checked=1 enabled=1 visible=0 -> text='≡ 赤道网' checked=1 enabled=1 visible=1；QPushButton(planetariumLayer_azgrid): text='◎ 方位网' checked=0 enabled=1 visible=0 -> text='◎ 方位网' checked=0 enabled=1 visible=1；QPushButton(planetariumLayer_night): text='夜视' checked=1 enabled=1 visible=0 -> text='夜视' checked=1 enabled=1 visible=1；QLabel(): text='图层' visible=0 -> text='图层' visible=1 | 可用 |
| ▽ 地景 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumLayer_landscape): text='▽ 地景' checked=1 enabled=1 visible=0 -> text='▽ 地景' checked=0 enabled=1 visible=0 | 可用 |
| ◍ 深空 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QLabel(statusValue): text='-25.2 dBFS' visible=0 -> text='-23.4 dBFS' visible=0；QLabel(statusValue): text='-25 dBFS' visible=0 -> text='-23 dBFS' visible=0；QPushButton(planetariumLayer_dso): text='◍ 深空' checked=1 enabled=1 visible=0 -> text='◍ 深空' checked=0 enabled=1 visible=0 | 可用 |
| ◎ 方位网 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumLayer_azgrid): text='◎ 方位网' checked=1 enabled=1 visible=0 -> text='◎ 方位网' checked=0 enabled=1 visible=0 | 可用 |
| ☰ | 按钮 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='起始' visible=1 -> text='起始' visible=0；QPushButton(): text='取消' checked=0 enabled=0 visible=1 -> text='取消' checked=0 enabled=0 visible=0；QPushButton(): text='开始扫描' checked=0 enabled=0 visible=1 -> text='开始扫描' checked=0 enabled=0 visible=0；QLabel(): text='结束' visible=1 -> text='结束' visible=0；QLabel(): text='门限余量' visible=1 -> text='门限余量' visible=0 | 可用 |
| ⛶ 全屏 | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumFullscreen): text='🗗 退出全屏' checked=0 enabled=1 visible=1 -> text='⛶ 全屏' checked=0 enabled=1 visible=1；__window__: visible=1 fullscreen=1 maximized=0 minimized=0 geo=860x800 -> visible=1 fullscreen=0 maximized=0 minimized=0 geo=1400x900 | 可用 |
| ✦ 卫星 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumLayer_sats): text='✦ 卫星' checked=1 enabled=1 visible=0 -> text='✦ 卫星' checked=0 enabled=1 visible=0 | 可用 |
| ✧ 星座 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumLayer_constellations): text='✧ 星座' checked=1 enabled=1 visible=0 -> text='✧ 星座' checked=0 enabled=1 visible=0 | 可用 |
| ⟳ 轨道 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：QPushButton(planetariumLayer_orbits): text='⟳ 轨道' checked=1 enabled=1 visible=0 -> text='⟳ 轨道' checked=0 enabled=1 visible=0；QLabel(): text='RBW 1171.88 Hz · 瀑布 4.64233 s' visible=0 -> text='RBW 1171.88 Hz · 瀑布 4.70085 s' visible=0 | 可用 |
| ⧉ | 按钮 | MainWindow | 是 | 是 | 是 | 新窗口：菜单：QMenu | 可用 |
| 今夜 | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；状态栏：先点「先看看示例天空」或设置观测位置，再切到今夜 | 可用 |
| 切换主听 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：主听切换 → vfo001（次听-未解调）；界面变化：QComboBox(): idx=-1 text='' -> idx=0 text='FM'；QLineEdit(): text='' style='' -> text='98.5' style='' | 可用 |
| 发射 | 按钮 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 回放 | 按钮 | MainWindow | 是 | 否 | 是 | 状态栏：未选择录音文件；文件框：打开 | 可用 |
| 多普勒 -- | 按钮 | MainWindow | 是 | 否 | 是 | 状态栏：未绑定卫星目标 | 可用 |
| 夜视 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(planetariumLayer_night): text='夜视' checked=0 enabled=1 visible=0 -> text='夜视' checked=1 enabled=1 visible=0 | 可用 |
| 存图 | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；状态栏：已存图：C:/Users/xxhzb/OneDrive/Pictures\MBDSDR\sky_20261110_021719.png；界面变化：QLabel(statusValue): text='-30.2 dBFS' visible=0 -> text='-25.4 dBFS' visible=0；QLabel(statusValue): text='-30 dBFS' visible=0 -> text='-25 dBFS' visible=0；QLabel(): text='RBW 1171.88 Hz · 瀑布 3.92315 s' visible=0 -> text='RBW 1171.88 Hz · 瀑布 3.98598 s' visible=0 | 可用 |
| 录音 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 断开 | 按钮 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 本后端发射：未连接设备（能力未知） | 按钮 | MainWindow | 是 | 是 | 是 | 新窗口：qtooltip_label | 可用 |
| 看整片天 | 按钮 | MainWindow | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QPushButton(): text='正在看整片天' checked=1 enabled=1 visible=0 -> text='看整片天' checked=0 enabled=1 visible=0；QAction(全天模式（160°）): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QLabel(statusValue): text='-30.5 dBFS' visible=0 -> text='-25.0 dBFS' visible=0；QLabel(statusValue): text='-30 dBFS' visible=0 -> text='-25 dBFS' visible=0 | 可用 |
| 详细 | 按钮 | MainWindow | 是 | 是 | 是 | 新窗口：QListWidget；界面变化：QToolButton(topBarAction): text='详细' checked=0 enabled=1 visible=1 -> text='收起' checked=1 enabled=1 visible=1；QLabel(statusValue): text='频率: —' visible=0 -> text='频率: —' visible=1；QLabel(statusValue): text='多普勒: —' visible=0 -> text='多普勒: —' visible=1；QLabel(statusValue): text='GNSS: 未连接' visible=0 -> text='GNSS: 未连接' visible=1；QLabel(statusValue): text='发射: —' visible=0 -> text='发射: —' visible=1 | 可用 |
| 连接 | 按钮 | MainWindow | 是 | 是 | 是 | 对话框：选择 SDR 设备 | 可用 |
| 退出星象仪<br>(Esc) | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；未实时触发：星象仪浮层按钮只在星象仪形态显示（`_exit_planetarium` 里 `_planetarium_strip.hide()`，IDE 形态不可见也不可点）；审计每条动作前强制回 IDE 页，此处点击等于 no-op。星象仪形态下的真实点击路径由 `tests/test_planetarium_entry_visibility.py::test_overlay_exit_button_is_clickable` 覆盖 | 未实时触发（代码核对） |
| 静音 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：QLabel(statusValue): text='48' visible=0 -> text='0' visible=0；QPushButton(): text='静音' checked=0 enabled=1 visible=0 -> text='静音' checked=1 enabled=1 visible=0 | 可用 |
| 频谱 | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QLabel(): text='窗:' visible=0 -> text='窗:' visible=1；QLabel(): text='Max' visible=0 -> text='Max' visible=1；QLabel(): text='FFT:' visible=0 -> text='FFT:' visible=1；QToolButton(spectrumMoreButton): text='更多' checked=0 enabled=1 visible=0 -> text='更多' checked=0 enabled=1 visible=1；QLabel(): text='98.500 MHz' visible=0 -> text='98.500 MHz' visible=1 | 可用 |
| 🎤 | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；状态栏：语音输入未配置（接麦克风后可用） | 可用 |
| 🎯 跟踪 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 📍 地点 | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；状态栏：地点面板已打开：可搜索城市 / 地图点选 / 手填经纬度 | 可用 |
| 📡 调到该星 | 按钮 | MainWindow | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；— | 诚实禁用 |
| 📷 摄像机 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 🔍 找星 | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；状态栏：输入天体名：ISS / Vega / 木星 / M42；↑↓ 选择，回车定位 | 可用 |
| 🕒 时间 | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；对话框：时间机器；界面变化：QLabel(statusValue): text='-23.6 dBFS' visible=0 -> text='-30.2 dBFS' visible=0；QLabel(statusValue): text='-24 dBFS' visible=0 -> text='-30 dBFS' visible=0 | 可用 |
| 🛰 云台 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 应用该解调链 | 按钮 | ModulationPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 识别信号 | 按钮 | ModulationPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 7；切页签 QTabWidget -> 7；— | 诚实禁用 |
| 已刷新 | 按钮 | ModulePanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 3；切页签 QTabWidget -> 3；界面变化：QLabel(): text='rtlsdr\n频率范围 24.0 MHz–1.76 GHz（声明）｜采样率 225.0 kHz–3.20 MHz（声明）｜增益 0.0–49.6 dB（声明）｜带宽 未知（原生枚举未查询带宽）｜发射 未知（原生枚举未查询 TX 通道）｜天线' visible=0 -> text='正在枚举设备…' visible=0；QPushButton(): text='刷新' checked=0 enabled=1 visible=0 -> text='刷新' checked=0 enabled=0 visible=0 | 可用 |
| 连接 | 按钮 | ModulePanel | 是 | 否 | 是 | 状态栏：正在连接 RTL-SDR #0 ...；弹窗：提示「已选择设备」RTL-SDR #0 (FC0012)<br>（真实连接由后端完成）；界面变化：QLabel(): text='未连接（等待 1090MHz 数据）' visible=0 -> text='当前设备上限 948.5 MHz，无法接收 1090 MHz；需覆盖 1090 MHz 的前端（如 R820T/R828D）' visible=0；QDoubleSpinBox(): value='100.0000 MHz' -> value='98.5000 MHz'；QPushButton(): text='静音' checked=0 enabled=0 visible=0 -> text='静音' checked=0 enabled=1 visible=0；QCheckBox(): text='书签' checked=1 enabled=0 visible=0 -> text='书签' checked=1 enabled=1 visible=0；QLabel(statusValue): text='频率: —' visible=0 -> text='频率: 98.500 MHz' visible=0 | 可用 |
| 保存当前图像为 PNG | 按钮 | SatelliteImagePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 停止解码 | 按钮 | SatelliteImagePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始解码 | 按钮 | SatelliteImagePanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 18；切页签 QTabWidget -> 18；— | 诚实禁用 |
| 取消 | 按钮 | ScannerPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始扫描 | 按钮 | ScannerPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 6；切页签 QTabWidget -> 6；— | 诚实禁用 |
| 启用 Web 服务 | 按钮 | ServiceSettingsPanel | 是 | 否 | 是 | 未实时触发：绑定端口起 HTTP 服务（settings_panel） | 未实时触发（代码核对） |
| 启用远程控制 | 按钮 | ServiceSettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 9；切页签 QTabWidget -> 9；未实时触发：起远程控制服务（settings_panel） | 未实时触发（代码核对） |
| 恢复本组默认 | 按钮 | ServiceSettingsPanel | 是 | 否 | 是 | 前置：扰动 QSpinBox: 7356 -> 7358；界面变化：QSpinBox(): value='7358' -> value='7356'；QLineEdit(qt_spinbox_lineedit): text='7358' style='' -> text='7356' style=''；QLabel(hintLabel): text='' visible=0 -> text='已恢复本组默认值' visible=0 | 可用 |
| 保存 | 按钮 | SettingsPanel | 是 | 否 | 是 | 文件框：输入 | 可用 |
| 删除 | 按钮 | SettingsPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 导入配置 | 按钮 | SettingsPanel | 是 | 否 | 是 | 文件框：打开；新窗口：qtooltip_label | 可用 |
| 导出配置 | 按钮 | SettingsPanel | 是 | 否 | 是 | 文件框：保存；新窗口：qtooltip_label | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：过境搜索窗口' visible=0 -> text='已保存：显示低仰角过境' visible=0；QLabel(hintLabel): text='未来 24 小时；列表按峰值仰角 ≥ 10° 筛选（峰值低于该值的过境不列出；要列 5–10° 请打开「显示低仰角过境」）' visible=0 -> text='未来 24 小时；列表按峰值仰角 ≥ 10° 筛选；「显示低仰角过境」已开启：额外列出 5–10° 的过境并标注「低仰角」' visible=0；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0；QLabel(hintLabel): text='已恢复默认：峰值判定阈值' visible=0 -> text='已保存：标注峰值' visible=0；QCheckBox(): text='峰值' checked=1 enabled=0 visible=0 -> text='峰值' checked=0 enabled=0 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：标注峰值' visible=0 -> text='已保存：瀑布时间平滑' visible=0；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0；QCheckBox(): text='瀑布平滑' checked=0 enabled=0 visible=0 -> text='瀑布平滑' checked=1 enabled=0 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QCheckBox(): text='书签' checked=1 enabled=0 visible=0 -> text='书签' checked=0 enabled=0 visible=0；QLabel(hintLabel): text='已恢复默认：瀑布时间平滑' visible=0 -> text='已保存：显示书签旗标' visible=0；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：显示书签旗标' visible=0 -> text='已保存：显示频段彩带' visible=0；QCheckBox(): text='频段' checked=1 enabled=0 visible=0 -> text='频段' checked=0 enabled=0 visible=0；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；切页签 QTabWidget -> 1；新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：大气消光系数 k' visible=0 -> text='已保存：专注页面（默认极简）' visible=0；QToolButton(topBarAction): text='收起' checked=1 enabled=1 visible=1 -> text='详细' checked=0 enabled=1 visible=1；QLabel(statusValue): text='频率: —' visible=1 -> text='频率: —' visible=0；QLabel(): text='—' visible=1 -> text='—' visible=0；QLabel(statusValue): text='多普勒: —' visible=1 -> text='多普勒: —' visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：已归位「AI 助手」；界面变化：QToolButton(topBarAction): text='⧉' checked=0 enabled=1 visible=1 -> text='⧉' checked=0 enabled=1 visible=0；QLabel(hintLabel): text='已恢复默认：专注页面（默认极简）' visible=0 -> text='已保存：自由窗口（面板可拖出）' visible=0；QStackedWidget(qt_tabwidget_stackedwidget): idx=10 -> idx=2；QTabWidget(): idx=10 -> idx=2；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：主题' visible=0 -> text='已保存：显示瀑布图' visible=0；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0 | 可用 |
| 恢复全部默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：观测位置已清空：天空回到未定位空态；界面变化：QLabel(hintLabel): text='已恢复默认：GNSS 波特率' visible=0 -> text='已恢复全部默认' visible=0 | 可用 |
| 打开目录 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：录音目录不存在：C:\Users\xxhzb\AppData\Local\Temp\mbdsdr_audit_jas9k50f\home\MBDSDR\reco；新窗口：qtooltip_label | 可用 |
| 打开配置目录 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：无法打开目录（系统拒绝请求）：C:\Users\xxhzb\AppData\Local\Temp\mbdsdr_audit_jas9k50f\home/.mbd；新窗口：qtooltip_label | 可用 |
| 选择目录… | 按钮 | SettingsPanel | 是 | 否 | 是 | 文件框：选目录；新窗口：qtooltip_label | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 6；切页签 QTabWidget -> 6；切堆栈 QStackedWidget -> 10；切页签 QTabWidget -> 10；新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='' visible=0 -> text='已恢复默认：录音目录' visible=0；QComboBox(): idx=3 text='CarWith 深色' -> idx=4 text='Figma 深色'；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0；QComboBox(): idx=0 text='自动检测' -> idx=2 text='强制鼠标' | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：录音目录' visible=0 -> text='已恢复默认：日志级别' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 5；切页签 QTabWidget -> 5；新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：日志级别' visible=0 -> text='已恢复默认：回答随机度（temperature）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：回答随机度（temperature）' visible=0 -> text='已恢复默认：采样截断（top_p）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：采样截断（top_p）' visible=0 -> text='已恢复默认：单次回答长度上限' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：单次回答长度上限' visible=0 -> text='已恢复默认：上下文窗口' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：上下文窗口' visible=0 -> text='已恢复默认：请求超时（秒）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 4；切页签 QTabWidget -> 4；状态栏：观测位置已清空：天空回到未定位空态；新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：请求超时（秒）' visible=0 -> text='已恢复默认：地面站纬度' visible=0；QLabel(spacetimeObserverLabel): text='未设置' visible=0 -> text='未设置（天空按未定位空态显示）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：观测位置已清空：天空回到未定位空态；新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：地面站纬度' visible=0 -> text='已恢复默认：地面站经度' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：地面站经度' visible=0 -> text='已恢复默认：过境最低仰角' visible=0；QLabel(): text='无数据' visible=0 -> text='未配置观测站位置：请在纬度/经度输入框填写地面站坐标' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：过境最低仰角' visible=0 -> text='已恢复默认：过境搜索窗口' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已保存：显示低仰角过境' visible=0 -> text='已恢复默认：显示低仰角过境' visible=0；QLabel(hintLabel): text='未来 24 小时；列表按峰值仰角 ≥ 10° 筛选；「显示低仰角过境」已开启：额外列出 5–10° 的过境并标注「低仰角」' visible=0 -> text='未来 24 小时；列表按峰值仰角 ≥ 10° 筛选（峰值低于该值的过境不列出；要列 5–10° 请打开「显示低仰角过境」）' visible=0；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 3；切页签 QTabWidget -> 3；新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：显示低仰角过境' visible=0 -> text='已恢复默认：频谱分辨率（FFT 点数）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：频谱分辨率（FFT 点数）' visible=0 -> text='已恢复默认：频谱窗函数' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：频谱窗函数' visible=0 -> text='已恢复默认：频谱平均帧数' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：频谱平均帧数' visible=0 -> text='已恢复默认：瀑布配色' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：瀑布配色' visible=0 -> text='已恢复默认：峰值判定阈值' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0；QLabel(hintLabel): text='已保存：标注峰值' visible=0 -> text='已恢复默认：标注峰值' visible=0；QCheckBox(): text='峰值' checked=0 enabled=0 visible=0 -> text='峰值' checked=1 enabled=0 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已保存：瀑布时间平滑' visible=0 -> text='已恢复默认：瀑布时间平滑' visible=0；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0；QCheckBox(): text='瀑布平滑' checked=1 enabled=0 visible=0 -> text='瀑布平滑' checked=0 enabled=0 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QCheckBox(): text='书签' checked=0 enabled=0 visible=0 -> text='书签' checked=1 enabled=0 visible=0；QLabel(hintLabel): text='已保存：显示书签旗标' visible=0 -> text='已恢复默认：显示书签旗标' visible=0；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已保存：显示频段彩带' visible=0 -> text='已恢复默认：显示频段彩带' visible=0；QCheckBox(): text='频段' checked=0 enabled=0 visible=0 -> text='频段' checked=1 enabled=0 visible=0；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：显示频段彩带' visible=0 -> text='已恢复默认：静噪门限默认值' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：静噪门限默认值' visible=0 -> text='已恢复默认：调谐步进' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：调谐步进' visible=0 -> text='已恢复默认：瀑布历史深度' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 2；切页签 QTabWidget -> 2；新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：瀑布历史深度' visible=0 -> text='已恢复默认：当地大气压' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：当地大气压' visible=0 -> text='已恢复默认：当地气温' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：当地气温' visible=0 -> text='已恢复默认：光害等级（Bortle）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已恢复默认：光害等级（Bortle）' visible=0 -> text='已恢复默认：大气消光系数 k' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：qtooltip_label；界面变化：QLabel(hintLabel): text='已保存：专注页面（默认极简）' visible=0 -> text='已恢复默认：专注页面（默认极简）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 10；切页签 QTabWidget -> 10；新窗口：AI 助手；界面变化：QToolButton(topBarAction): text='⧉' checked=0 enabled=1 visible=0 -> text='⧉' checked=0 enabled=1 visible=1；QLabel(hintLabel): text='已保存：自由窗口（面板可拖出）' visible=0 -> text='已恢复默认：自由窗口（面板可拖出）' visible=0；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：自由窗口（面板可拖出）' visible=0 -> text='已恢复默认：主题' visible=0；QLabel(statusValue): text='UTC 18:17:13' visible=1 -> text='UTC 18:17:15' visible=1；QComboBox(): idx=4 text='Figma 深色' -> idx=3 text='CarWith 深色'；QComboBox(): idx=4 text='Figma 深色' -> idx=3 text='CarWith 深色' | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已保存：显示瀑布图' visible=0 -> text='已恢复默认：显示瀑布图' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 0；切页签 QTabWidget -> 0；界面变化：QLabel(hintLabel): text='已恢复默认：显示瀑布图' visible=0 -> text='已恢复默认：界面语言' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：触屏模式：自动检测；界面变化：QLabel(hintLabel): text='已恢复默认：界面语言' visible=0 -> text='已恢复默认：触屏适配' visible=0；QComboBox(): idx=2 text='强制鼠标' -> idx=0 text='自动检测' | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：触屏适配' visible=0 -> text='已恢复默认：操作员呼号' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：操作员呼号' visible=0 -> text='已恢复默认：GNSS 串口' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：GNSS 串口' visible=0 -> text='已恢复默认：GNSS 波特率' visible=0 | 可用 |
| Max保持 | 按钮 | SpectrumPanel | 是 | 是 | 是 | 界面变化：QCheckBox(): text='Max保持' checked=0 enabled=1 visible=1 -> text='Max保持' checked=1 enabled=1 visible=1 | 可用 |
| Min保持 | 按钮 | SpectrumPanel | 是 | 是 | 是 | 界面变化：QCheckBox(): text='Min保持' checked=0 enabled=1 visible=1 -> text='Min保持' checked=1 enabled=1 visible=1 | 可用 |
| 书签 | 按钮 | SpectrumPanel | 是 | 是 | 是 | 界面变化：QCheckBox(): text='书签' checked=1 enabled=1 visible=1 -> text='书签' checked=0 enabled=1 visible=1 | 可用 |
| 峰值 | 按钮 | SpectrumPanel | 是 | 是 | 是 | 界面变化：QCheckBox(): text='峰值' checked=1 enabled=1 visible=1 -> text='峰值' checked=0 enabled=1 visible=1；QLabel(statusValue): text='-20.6 dBFS' visible=0 -> text='-25.1 dBFS' visible=0；QLabel(statusValue): text='-21 dBFS' visible=0 -> text='-25 dBFS' visible=0 | 可用 |
| 更多 | 按钮 | SpectrumPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 1；新窗口：菜单：QMenu；界面变化：QCheckBox(): text='书签' checked=1 enabled=1 visible=0 -> text='书签' checked=1 enabled=1 visible=1；QLabel(peakGateLabel): text='峰值门(相对本帧中位):' visible=0 -> text='峰值门(相对本帧中位):' visible=1；QCheckBox(): text='频段' checked=1 enabled=1 visible=0 -> text='频段' checked=1 enabled=1 visible=1；QCheckBox(): text='峰值' checked=1 enabled=1 visible=0 -> text='峰值' checked=1 enabled=1 visible=1；QLabel(): text='调色板:' visible=0 -> text='调色板:' visible=1 | 可用 |
| 瀑布平滑 | 按钮 | SpectrumPanel | 是 | 是 | 是 | 界面变化：QCheckBox(): text='瀑布平滑' checked=0 enabled=1 visible=1 -> text='瀑布平滑' checked=1 enabled=1 visible=1 | 可用 |
| 调谐:中心 | 按钮 | SpectrumPanel | 是 | 是 | 是 | 界面变化：QPushButton(): text='调谐:中心' checked=1 enabled=1 visible=1 -> text='调谐:VFO' checked=0 enabled=1 visible=1 | 可用 |
| 频段 | 按钮 | SpectrumPanel | 是 | 是 | 是 | 界面变化：QCheckBox(): text='频段' checked=1 enabled=1 visible=1 -> text='频段' checked=0 enabled=1 visible=1；QLabel(statusValue): text='-25.1 dBFS' visible=0 -> text='-24.3 dBFS' visible=0；QLabel(statusValue): text='-25 dBFS' visible=0 -> text='-24 dBFS' visible=0 | 可用 |
| + VFO | 按钮 | VfoPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 11；切页签 QTabWidget -> 11；— | 诚实禁用 |
| 循环主听 | 按钮 | VfoPanel | 否 | 否 | 是 | — | 诚实禁用 |
| Kuwahara 降噪 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='Kuwahara 降噪' checked=0 enabled=1 visible=0 -> text='Kuwahara 降噪' checked=1 enabled=1 visible=0 | 可用 |
| 中值滤波 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='中值滤波' checked=0 enabled=1 visible=0 -> text='中值滤波' checked=1 enabled=1 visible=0 | 可用 |
| 停止 | 按钮 | WeatherPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始解码 | 按钮 | WeatherPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 白平衡 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='白平衡' checked=0 enabled=1 visible=0 -> text='白平衡' checked=1 enabled=1 visible=0 | 可用 |
| 直方图均衡 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='直方图均衡' checked=0 enabled=1 visible=0 -> text='直方图均衡' checked=1 enabled=1 visible=0 | 可用 |
| 选择录制文件... | 按钮 | WeatherPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 4；切页签 QTabWidget -> 4；文件框：打开 | 可用 |
| 刷新过境 | 按钮 | newSpacetimePanel | 是 | 否 | 是 | 界面变化：QPushButton(): text='刷新过境' checked=0 enabled=1 visible=0 -> text='更新中…' checked=0 enabled=0 visible=0 | 可用 |
| 开始分析 | 按钮 | newSpacetimePanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 5；切页签 QTabWidget -> 5；界面变化：QPushButton(): text='开始分析' checked=0 enabled=1 visible=0 -> text='停止分析' checked=1 enabled=1 visible=0 | 可用 |
| 更改地点… | 按钮 | newSpacetimePanel | 是 | 否 | 是 | 状态栏：地点面板已打开：可搜索城市 / 地图点选 / 手填经纬度；新窗口：选择观测地点 | 可用 |
| 停止跟踪 | 按钮 | satTrackPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始跟踪 | 按钮 | satTrackPanel | 是 | 否 | 是 | 未实时触发：按秒轮询硬件指向（sat_track_panel.start_tracking）；真跑会起定时器并写后端 | 未实时触发（代码核对） |
| 更新 TLE | 按钮 | satTrackPanel | 是 | 否 | 是 | 未实时触发：联网拉 Celestrak TLE（sat_track_panel） | 未实时触发（代码核对） |
| 拉取挂载点列表 | 按钮 | 对话框:NTRIP 设置 | 是 | 是 | 是 | 未实时触发：发起 NTRIP 网络请求（ntrip_panel） | 未实时触发（代码核对） |
| 断开 | 按钮 | 对话框:NTRIP 设置 | 否 | 是 | 是 | — | 诚实禁用 |
| 连接 | 按钮 | 对话框:NTRIP 设置 | 是 | 是 | 是 | 弹窗：警告「无法连接」请填写 Caster Host 与 Mountpoint。 | 可用 |
| 关闭 | 按钮 | 对话框:帮助与能力目录 | 是 | 是 | 是 | 界面变化：QPushButton(): text='关闭' checked=0 enabled=1 visible=1 -> <已销毁（关闭/删除）>；QToolButton(ScrollLeftButton): text='' checked=0 enabled=0 visible=0 -> <已销毁（关闭/删除）>；QLabel(): text='共 43 项，其中 33 项已配键；键位与菜单显示一致' visible=1 -> <已销毁（关闭/删除）>；QLabel(): text='没有匹配的能力。换个关键词（例如「FT8」「卫星」），或把分类切回「全部」再试。' visible=0 -> <已销毁（关闭/删除）>；QLineEdit(): text='' style='' -> <已销毁（关闭/删除）> | 可用 |
| 打开能力目录 | 按钮 | 对话框:帮助与能力目录 | 是 | 是 | 是 | 界面变化：QPushButton(): text='查看快捷键一览' checked=0 enabled=1 visible=1 -> text='查看快捷键一览' checked=0 enabled=1 visible=0；QLabel(): text='显示 53 / 共 53 项 · 可用 47 · 有条件 2 · 暂不可用 4' visible=0 -> text='显示 53 / 共 53 项 · 可用 47 · 有条件 2 · 暂不可用 4' visible=1；QStackedWidget(qt_tabwidget_stackedwidget): idx=0 -> idx=1；QPushButton(): text='打开能力目录' checked=0 enabled=1 visible=1 -> text='打开能力目录' checked=0 enabled=1 visible=0；QLabel(): text='本目录由注册表实时生成：面板 / 解调模式 / 数字解码能力表 / 长尾解码器 / AI 工具域 / 快捷键。新增能力会自动出现；标「暂不可用」的条目按原因处理，不会点了没反应。' visible=0 -> text='本目录由注册表实时生成：面板 / 解调模式 / 数字解码能力表 / 长尾解码器 / AI 工具域 / 快捷键。新增能力会自动出现；标「暂不可用」的条目按原因处理，不会点了没反应。' visible=1 | 可用 |
| 查看快捷键一览 | 按钮 | 对话框:帮助与能力目录 | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 0；切页签 QTabWidget -> 0；对话框：快捷键一览；新窗口：快捷键一览；界面变化：QLabel(statusValue): text='-24.3 dBFS' visible=0 -> text='-22.0 dBFS' visible=0；QLabel(statusValue): text='-24 dBFS' visible=0 -> text='-22 dBFS' visible=0 | 可用 |
| 全部恢复默认 | 按钮 | 对话框:快捷键一览 | 否 | 是 | 是 | — | 诚实禁用 |
| 关闭 | 按钮 | 对话框:快捷键一览 | 是 | 是 | 是 | 界面变化：QLabel(): text='没有匹配的快捷键。换个关键词试试（例如「录音」「卫星」），或清空搜索框。' visible=0 -> <已销毁（关闭/删除）>；QLabel(): text='键位来自 desktop/shortcuts.py（唯一事实源）；标「无快捷键」的项只能从菜单/按钮触发，原因写在说明列。' visible=1 -> <已销毁（关闭/删除）>；QLineEdit(): text='' style='' -> <已销毁（关闭/删除）>；QPushButton(): text='改键…' checked=0 enabled=0 visible=1 -> <已销毁（关闭/删除）>；QPushButton(): text='全部恢复默认' checked=0 enabled=0 visible=1 -> <已销毁（关闭/删除）> | 可用 |
| 恢复默认 | 按钮 | 对话框:快捷键一览 | 否 | 是 | 是 | — | 诚实禁用 |
| 改键… | 按钮 | 对话框:快捷键一览 | 否 | 是 | 是 | — | 诚实禁用 |
| +1 天 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 界面变化：QLabel(tmHint): text='模拟中 · 比现在 −1 天' visible=1 -> text='当前就是现在（实时）' visible=1；QDateTimeEdit(): value='2026-11-09  02:17:19' -> value='2026-11-10  02:17:19'；QLineEdit(qt_spinbox_lineedit): text='2026-11-09  02:17:19' style='' -> text='2026-11-10  02:17:19' style='' | 可用 |
| +1 小时 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 界面变化：QLabel(tmHint): text='模拟中 · 比现在 −1 天 1 小时' visible=1 -> text='模拟中 · 比现在 −1 天' visible=1；QDateTimeEdit(): value='2026-11-09  01:17:19' -> value='2026-11-09  02:17:19'；QLineEdit(qt_spinbox_lineedit): text='2026-11-09  01:17:19' style='' -> text='2026-11-09  02:17:19' style='' | 可用 |
| −1 天 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 前置：打开对话框 时间机器；界面变化：QLabel(tmHint): text='当前就是现在（实时）' visible=1 -> text='模拟中 · 比现在 −1 天' visible=1；QDateTimeEdit(): value='2026-11-10  02:17:19' -> value='2026-11-09  02:17:19'；QLineEdit(qt_spinbox_lineedit): text='2026-11-10  02:17:19' style='' -> text='2026-11-09  02:17:19' style='' | 可用 |
| −1 小时 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 界面变化：QLabel(tmHint): text='模拟中 · 比现在 −1 天' visible=1 -> text='模拟中 · 比现在 −1 天 1 小时' visible=1；QDateTimeEdit(): value='2026-11-09  02:17:19' -> value='2026-11-09  01:17:19'；QLineEdit(qt_spinbox_lineedit): text='2026-11-09  02:17:19' style='' -> text='2026-11-09  01:17:19' style='' | 可用 |
| 今夜 | 按钮 | 对话框:时间机器 | 否 | 是 | 是 | — | 诚实禁用 |
| 回到现在 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 界面变化：QLabel(tmHint): text='当前就是现在（实时）' visible=1 -> text='已在现在 · 实时' visible=1 | 可用 |
| 跳过去 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 状态栏：时刻已回到此刻 11-10 02:17（实时）；界面变化：QLabel(tmTitle): text='时间机器' visible=1 -> text='时间机器' visible=0；QPushButton(): text='+1 天' checked=0 enabled=1 visible=1 -> text='+1 天' checked=0 enabled=1 visible=0；QLabel(tmHint): text='已在现在 · 实时' visible=1 -> text='当前就是现在（实时）' visible=0；QLabel(tmHint): text='跳到任意时刻看那时的天空 · 观测条件设定，不是观测数据' visible=1 -> text='跳到任意时刻看那时的天空 · 观测条件设定，不是观测数据' visible=0；QPushButton(planetariumTimeRate): text='⏱ 60×' checked=0 enabled=1 visible=0 -> text='⏱ 1×' checked=0 enabled=1 visible=0 | 可用 |
| Cancel | 按钮 | 对话框:连接 GNSS | 是 | 是 | 是 | 前置：打开对话框 连接 GNSS；界面变化：QDialog(连接 GNSS): visible=1 -> visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0；QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QLabel(): text='波特率:' visible=1 -> text='波特率:' visible=0；QLabel(): text='串口号:' visible=1 -> text='串口号:' visible=0 | 可用 |
| OK | 按钮 | 对话框:连接 GNSS | 是 | 是 | 是 | 前置：打开对话框 连接 GNSS；界面变化：QDialog(连接 GNSS): visible=1 -> visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0；QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QLabel(): text='波特率:' visible=1 -> text='波特率:' visible=0；QLabel(): text='串口号:' visible=1 -> text='串口号:' visible=0 | 可用 |
| Cancel | 按钮 | 对话框:选择 SDR 设备 | 是 | 是 | 是 | 前置：打开对话框 选择 SDR 设备；界面变化：QLabel(deviceResultLabel): text='检测到 1 台 SDR 设备' visible=1 -> text='检测到 1 台 SDR 设备' visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0；QCheckBox(): text='启用自动增益 (AGC)' checked=1 enabled=1 visible=1 -> text='启用自动增益 (AGC)' checked=1 enabled=1 visible=0；QLabel(): text='增益' visible=1 -> text='增益' visible=0；QDialog(选择 SDR 设备): visible=1 -> visible=0 | 可用 |
| OK | 按钮 | 对话框:选择 SDR 设备 | 是 | 是 | 是 | 界面变化：QLabel(deviceResultLabel): text='检测到 1 台 SDR 设备' visible=1 -> text='检测到 1 台 SDR 设备' visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0；QCheckBox(): text='启用自动增益 (AGC)' checked=1 enabled=1 visible=1 -> text='启用自动增益 (AGC)' checked=1 enabled=1 visible=0；QLabel(): text='增益' visible=1 -> text='增益' visible=0；QDialog(选择 SDR 设备): visible=1 -> visible=0 | 可用 |
| Offset Tuning（直流抵消） | 按钮 | 对话框:选择 SDR 设备 | 是 | 否 | **否** | 无 clicked/toggled 槽；布尔值在 `_connect_dialog` 里经 `offset_chk.isChecked()` 读取后下发后端（main_window.py 设备参数区） | 输入控件（无独立槽，值被下游读取） |
| 刷新设备列表 | 按钮 | 对话框:选择 SDR 设备 | 是 | 是 | 是 | 前置：打开对话框 选择 SDR 设备；界面变化：QLabel(statusValue): text='-19.4 dBFS' visible=0 -> text='-27.4 dBFS' visible=0；QPushButton(): text='静音' checked=0 enabled=1 visible=0 -> <已销毁（关闭/删除）>；QPushButton(): text='✓' checked=0 enabled=1 visible=0 -> <已销毁（关闭/删除）>；QPushButton(): text='删' checked=0 enabled=1 visible=0 -> <已销毁（关闭/删除）>；QLabel(statusValue): text='-19 dBFS' visible=0 -> text='-27 dBFS' visible=0 | 可用 |
| 启用自动增益 (AGC) | 按钮 | 对话框:选择 SDR 设备 | 是 | 否 | **否** | 无 clicked/toggled 槽；布尔值在 `_connect_dialog` 里经 `agc_chk.isChecked()` 读取后下发后端（main_window.py 设备参数区） | 输入控件（无独立槽，值被下游读取） |

## 未覆盖项（如实列出，不计入通过）

- 右键菜单（频谱「删除此 VFO」）在交互时才构造；已源码核对：菜单项选中后调用 `_delete_vfo`，属可用。
- 工具栏 `QWidgetAction` 是控件容器，本身不可点，不计入动作数。
- Qt 自带的页签滚动/工具栏折叠按钮不是业务动作，已剔除。
- `SKIP_LIVE` 里的动作只做代码核对，未实时触发（起线程/联网/写用户数据）。

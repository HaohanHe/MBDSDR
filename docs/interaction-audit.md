# 交互覆盖审计（死按钮 / 假按钮 / 静默异常）

> 由 `scripts/ui_interaction_audit.py` 运行时遍历生成，请勿手改。
> 环境：offscreen + `MBDSDR_NULL_AUDIO=1` + `MBDSDR_NO_HW=1` + `MBDSDR_OFFLINE=1`，HOME/QSettings 隔离到临时目录。
> 口径：`receivers()==0` = 真死；有接收者但触发后无状态/信号下游/窗口/弹窗变化 = 假活；触发抛出或被 Qt 吞掉的异常单列。
> 防误报：触发前先空跑事件循环，把 UTC 时钟/SNR 读数等自变字段识别为 volatile 并从 diff 里剔除；运行时新建的同类对话框按「标题+控件名」去重。

## 结论

- 遍历动作 **133** 个（QAction 37 / QAbstractButton 96；enabled 86 / visible 63）
- **真死** 0 个
- **假活** 0 个
- **抛异常** 0 个
- 诚实禁用 39 个；未实时触发 12 个；输入控件 2 个

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

| 动作 | 类型 | 所在面板 | 位置 | 证据 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 连接 | 按钮 | DevicePanel | QTabWidget / QScrollArea / DevicePanel / QGroupBox | — | 诚实禁用（理由未登记） |
| 连接 | 按钮 | ModulePanel | QTabWidget / QScrollArea / ModulePanel / QGroupBox | — | 诚实禁用（理由未登记） |
| 调谐 | 按钮 | ControlPanel | QTabWidget / QScrollArea / ControlPanel / QGroupBox | — | 诚实禁用（理由未登记） |
| - | 按钮 | ControlPanel | QTabWidget / QScrollArea / ControlPanel / QGroupBox | — | 诚实禁用（理由未登记） |
| + | 按钮 | ControlPanel | QTabWidget / QScrollArea / ControlPanel / QGroupBox | — | 诚实禁用（理由未登记） |
| 收藏当前频率 | 按钮 | ControlPanel | QTabWidget / QScrollArea / ControlPanel / QGroupBox | — | 诚实禁用（理由未登记） |
| DC 去除 | 按钮 | ControlPanel | QTabWidget / QScrollArea / ControlPanel / QGroupBox | — | 诚实禁用（理由未登记） |
| IQ 平衡 | 按钮 | ControlPanel | QTabWidget / QScrollArea / ControlPanel / QGroupBox | — | 诚实禁用（理由未登记） |

## 附：全量明细

| 动作 | 类型 | 所在面板 | 启用 | 可见 | 有槽 | 可观察结果 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 主工具栏 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(主工具栏): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QToolButton(qt_toolbar_ext_button): text='' checked=0 enabled=1 visible=1 -> text='' checked=0 enabled=1 visible=0；QPushButton(): text='连接' checked=0 enabled=1 visible=1 -> text='连接' checked=0 enabled=1 visible=0；QPushButton(): text='☰' checked=0 enabled=1 visible=1 -> text='☰' checked=0 enabled=1 visible=0；QPushButton(): text='断开' checked=0 enabled=0 visible=1 -> text='断开' checked=0 enabled=0 visible=0 | 可用 |
| 工具(&T) / FM 扫频找台 | 菜单项 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 工具(&T) / NTRIP 设置 | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：NTRIP 设置 | 可用 |
| 工具(&T) / 停止卫星跟踪 | 菜单项 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 工具(&T) / 关于 MBDSDR | 菜单项 | MainWindow | 是 | 是 | 是 | 弹窗：关于「关于 MBDSDR」<h3>MBDSDR - AI 定义无线电</h3><p>开源 MIT 许可的软件定义无线电平台（独立实现）</p><p>呼号：在「设 | 可用 |
| 工具(&T) / 开始/停止录音 | 菜单项 | MainWindow | 是 | 是 | 是 | 弹窗：警告「无法录音」未连接 SDR，无法录制 baseband。<br>请先连接真实 SDR 设备。；界面变化：QLabel(): text='未录音' -> text='录音完成 (0 秒)' | 可用 |
| 工具(&T) / 开始跟踪选中卫星 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：需要观测者位置：连接 GNSS 或在 gui_config.json 配置 observer_lat/lon | 可用 |
| 工具(&T) / 断开 GNSS | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：GNSS 已断开 | 可用 |
| 工具(&T) / 更新卫星 TLE (Celestrak) | 菜单项 | MainWindow | 是 | 是 | 是 | 未实时触发：联网拉 Celestrak TLE | 未实时触发（代码核对） |
| 工具(&T) / 连接 GNSS... | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：连接 GNSS | 可用 |
| 文件(&F) / 断开连接 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：当前未连接 SDR，没有可断开的设备；界面变化：QLabel(): text='空闲' -> text='已停止' | 可用 |
| 文件(&F) / 连接设备... | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：选择 SDR 设备 | 可用 |
| 文件(&F) / 退出 | 菜单项 | MainWindow | 是 | 是 | 是 | 未实时触发：关闭主窗口退出进程；等价于 win.close()，审计窗口还要继续用，不真退出 | 未实时触发（代码核对） |
| 视图(&V) / 主题 / CarWith 深色 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(CarWith 深色): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QComboBox(): idx=2 text='高对比' -> idx=3 text='CarWith 深色' | 可用 |
| 视图(&V) / 主题 / Figma 深色 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(Figma 深色): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QComboBox(): idx=3 text='CarWith 深色' -> idx=4 text='Figma 深色' | 可用 |
| 视图(&V) / 主题 / 深色 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(深色): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QComboBox(): idx=0 text='默认' -> idx=1 text='深色' | 可用 |
| 视图(&V) / 主题 / 高对比 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(高对比): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QComboBox(): idx=1 text='深色' -> idx=2 text='高对比' | 可用 |
| 视图(&V) / 主题 / 默认 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(默认): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QComboBox(): idx=4 text='Figma 深色' -> idx=0 text='默认' | 可用 |
| 视图(&V) / 全天模式（160°） | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(全天模式（160°）): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QPushButton(): text='🌐 全天 160°' checked=0 enabled=1 visible=1 -> text='🌐 全天 160°（已进入）' checked=1 enabled=1 visible=1 | 可用 |
| 视图(&V) / 全屏 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：__window__: visible=1 fullscreen=0 maximized=0 minimized=0 geo=1000x774 -> visible=1 fullscreen=1 maximized=0 minimized=0 geo=800x800 | 可用 |
| 视图(&V) / 星象仪模式 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(recordButton): text='开始录音' checked=0 enabled=0 visible=1 -> text='开始录音' checked=0 enabled=0 visible=0；QCheckBox(): text='自动' checked=1 enabled=1 visible=1 -> text='自动' checked=1 enabled=1 visible=0；QPushButton(): text='+' checked=0 enabled=0 visible=1 -> text='+' checked=0 enabled=0 visible=0；__window__: visible=1 fullscreen=0 maximized=0 minimized=0 geo=1000x800 -> visible=1 fullscreen=1 maximized=0 minimized=0 geo=1000x800；QCheckBox(): text='IQ 平衡' checked=1 enabled=0 visible=1 -> text='IQ 平衡' checked=1 enabled=0 visible=0 | 可用 |
| 视图(&V) / 显示瀑布图 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(显示瀑布图): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 视图(&V) / 触屏模式 / 强制触屏 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：触屏模式：强制触屏；界面变化：QAction(强制触屏): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QAction(自动检测): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 视图(&V) / 触屏模式 / 强制鼠标 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：触屏模式：强制鼠标；界面变化：QAction(强制触屏): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QAction(强制鼠标): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1 | 可用 |
| 视图(&V) / 触屏模式 / 自动检测 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：触屏模式：自动检测；界面变化：QAction(自动检测): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QAction(强制鼠标): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 视图(&V) / 重置窗口位置 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：窗口已重置到主屏中央；界面变化：__window__: visible=1 fullscreen=1 maximized=0 minimized=0 geo=800x800 -> visible=1 fullscreen=0 maximized=0 minimized=0 geo=1000x800 | 可用 |
| 面板(&P) / ADS-B 航路 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(ADS-B 航路): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / AI 助手 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(): text='显示执行步骤' checked=0 enabled=1 visible=1 -> text='显示执行步骤' checked=0 enabled=1 visible=0；QPushButton(): text='扫频 87-108 MHz 找所有电台' checked=0 enabled=1 visible=1 -> text='扫频 87-108 MHz 找所有电台' checked=0 enabled=1 visible=0；QPushButton(): text='取消' checked=0 enabled=0 visible=0 -> text='取消' checked=0 enabled=0 visible=1；QStackedWidget(qt_tabwidget_stackedwidget): idx=2 -> idx=6；QPushButton(): text='配置' checked=0 enabled=1 visible=1 -> text='配置' checked=0 enabled=1 visible=0 | 可用 |
| 面板(&P) / 卫星跟踪 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(): text='停止跟踪' checked=0 enabled=0 visible=1 -> text='停止跟踪' checked=0 enabled=0 visible=0；QAction(卫星跟踪): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QToolButton(ScrollRightButton): text='' checked=0 enabled=0 visible=0 -> text='' checked=0 enabled=1 visible=0；QPushButton(): text='开始跟踪' checked=0 enabled=1 visible=1 -> text='开始跟踪' checked=0 enabled=1 visible=0；QPushButton(): text='更新 TLE' checked=0 enabled=1 visible=1 -> text='更新 TLE' checked=0 enabled=1 visible=0 | 可用 |
| 面板(&P) / 天空模式 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(天空模式): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 恢复出厂布局 | 菜单项 | MainWindow | 是 | 是 | 是 | 弹窗：询问「恢复出厂布局」清除所有自定义布局并恢复到默认「专注」布局？ | 可用 |
| 面板(&P) / 控制 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(控制): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QPushButton(recordButton): text='开始录音' checked=0 enabled=0 visible=1 -> text='开始录音' checked=0 enabled=0 visible=0；QCheckBox(): text='自动' checked=1 enabled=1 visible=1 -> text='自动' checked=1 enabled=1 visible=0；QPushButton(): text='+' checked=0 enabled=0 visible=1 -> text='+' checked=0 enabled=0 visible=0；QStackedWidget(qt_tabwidget_stackedwidget): idx=0 -> idx=1 | 可用 |
| 面板(&P) / 新时空 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(新时空): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 模块/信号流 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(模块/信号流): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 气象云图 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(气象云图): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 状态 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(): text='显示执行步骤' checked=0 enabled=1 visible=0 -> text='显示执行步骤' checked=0 enabled=1 visible=1；QPushButton(): text='扫频 87-108 MHz 找所有电台' checked=0 enabled=1 visible=0 -> text='扫频 87-108 MHz 找所有电台' checked=0 enabled=1 visible=1；QStackedWidget(qt_tabwidget_stackedwidget): idx=1 -> idx=2；QPushButton(): text='配置' checked=0 enabled=1 visible=0 -> text='配置' checked=0 enabled=1 visible=1；QTabWidget(): idx=1 -> idx=2 | 可用 |
| 面板(&P) / 频谱 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(频谱): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 发送 | 按钮 | AIPanel | 是 | 否 | 是 | 未实时触发：启动 AI 推理线程（ai_panel 写对话区并切「思考中…」）；不在审计里真跑模型 | 未实时触发（代码核对） |
| 当前信号质量怎么样 | 按钮 | AIPanel | 是 | 否 | 是 | 未实时触发：同「发送」：快捷指令把问题填进对话框并起 AI 推理线程 | 未实时触发（代码核对） |
| 扫频 87-108 MHz 找所有电台 | 按钮 | AIPanel | 是 | 否 | 是 | 未实时触发：同「发送」：填充问题并进入 AI 思考态 | 未实时触发（代码核对） |
| 找一个信号强的 FM 电台 | 按钮 | AIPanel | 是 | 否 | 是 | 未实时触发：同「发送」：填充问题并进入 AI 思考态 | 未实时触发（代码核对） |
| 显示执行步骤 | 按钮 | AIPanel | 是 | 否 | 是 | 界面变化：QPushButton(): text='显示执行步骤' checked=0 enabled=1 visible=0 -> text='隐藏执行步骤' checked=0 enabled=1 visible=0 | 可用 |
| 调谐到 98.5 MHz 并录 30 秒 | 按钮 | AIPanel | 是 | 否 | 是 | 未实时触发：同「发送」：填充问题并进入 AI 思考态 | 未实时触发（代码核对） |
| 配置 | 按钮 | AIPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 2；切页签 QTabWidget -> 2；对话框：AI 配置 | 可用 |
| 启用 ANR | 按钮 | AnrPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 11；切页签 QTabWidget -> 11；— | 诚实禁用 |
| 采样噪声底 | 按钮 | AnrPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 删除 | 按钮 | BookmarkPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 导入 CSV | 按钮 | BookmarkPanel | 是 | 否 | 是 | 文件框：打开 | 可用 |
| 导出 CSV | 按钮 | BookmarkPanel | 是 | 否 | 是 | 文件框：保存 | 可用 |
| 找最近 | 按钮 | BookmarkPanel | 是 | 否 | 是 | 弹窗：提示「找最近」无当前频率（未调谐）。 | 可用 |
| 添加当前频率 | 按钮 | BookmarkPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 8；切页签 QTabWidget -> 8；弹窗：提示「添加书签」无当前频率（未连接 SDR 或未调谐）。<br>可手动输入频率。；文件框：输入 | 可用 |
| 编辑 | 按钮 | BookmarkPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 清空 | 按钮 | ConstellationPanel | 是 | 否 | 是 | 状态栏：星座图已清空，统计已重置 | 可用 |
| 网格 | 按钮 | ConstellationPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 12；切页签 QTabWidget -> 12；界面变化：QCheckBox(): text='网格' checked=1 enabled=1 visible=0 -> text='网格' checked=0 enabled=1 visible=0 | 可用 |
| + | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用（理由未登记） |
| - | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用（理由未登记） |
| AGC 自动增益 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| DC 去除 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用（理由未登记） |
| IQ 平衡 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用（理由未登记） |
| 偏移调谐 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 删除选中 | 按钮 | ControlPanel | 是 | 否 | 是 | 界面变化：QComboBox(): idx=0 text='航空应急频率 121.500 (12…' -> idx=0 text='航空应急备用 123.100 (1…' | 可用 |
| 开始录音 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 收藏当前频率 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用（理由未登记） |
| 自动 | 按钮 | ControlPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='自动' checked=1 enabled=1 visible=0 -> text='自动' checked=0 enabled=1 visible=0 | 可用 |
| 调谐 | 按钮 | ControlPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 0；切页签 QTabWidget -> 0；— | 诚实禁用（理由未登记） |
| 刷新 | 按钮 | DevicePanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 13；切页签 QTabWidget -> 13；界面变化：QLabel(statusValue): text='已断开' -> text='未检测到 SDR 设备' | 可用 |
| 断开 | 按钮 | DevicePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 连接 | 按钮 | DevicePanel | 否 | 否 | 是 | — | 诚实禁用（理由未登记） |
| 硬件 AGC | 按钮 | GainPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 14；切页签 QTabWidget -> 14；— | 诚实禁用 |
| + VFO | 按钮 | MainWindow | 是 | 否 | 是 | 状态栏：新建 VFO vfo001 @ 98.500 MHz | 可用 |
| IDE | 按钮 | MainWindow | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；界面变化：QCheckBox(): text='瀑布平滑' checked=0 enabled=0 visible=1 -> text='瀑布平滑' checked=0 enabled=0 visible=0；QToolButton(statusValue): text='发射' checked=0 enabled=0 visible=0 -> text='发射' checked=0 enabled=0 visible=1；QToolButton(ScrollRightButton): text='' checked=0 enabled=1 visible=0 -> text='' checked=0 enabled=0 visible=0；QStackedWidget(): idx=1 -> idx=0；QCheckBox(): text='频段' checked=1 enabled=0 visible=1 -> text='频段' checked=1 enabled=0 visible=0 | 可用 |
| ⌂ | 按钮 | MainWindow | 是 | 是 | 是 | 界面变化：QCheckBox(): text='瀑布平滑' checked=0 enabled=0 visible=0 -> text='瀑布平滑' checked=0 enabled=0 visible=1；QCheckBox(): text='频段' checked=1 enabled=0 visible=0 -> text='频段' checked=1 enabled=0 visible=1；QTabWidget(): idx=0 -> idx=2；QCheckBox(): text='书签' checked=1 enabled=0 visible=0 -> text='书签' checked=1 enabled=0 visible=1；QCheckBox(): text='峰值' checked=1 enabled=0 visible=0 -> text='峰值' checked=1 enabled=0 visible=1 | 可用 |
| ☰ | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：QPushButton(): text='取消' checked=0 enabled=0 visible=1 -> text='取消' checked=0 enabled=0 visible=0；QToolButton(ScrollLeftButton): text='' checked=0 enabled=0 visible=1 -> text='' checked=0 enabled=0 visible=0；QToolButton(ScrollRightButton): text='' checked=0 enabled=1 visible=1 -> text='' checked=0 enabled=1 visible=0；QPushButton(): text='开始扫描' checked=0 enabled=0 visible=1 -> text='开始扫描' checked=0 enabled=0 visible=0 | 可用 |
| 保存 | 按钮 | MainWindow | 是 | 否 | 是 | 文件框：输入 | 可用 |
| 切换主听 | 按钮 | MainWindow | 是 | 否 | 是 | 状态栏：主听切换 → vfo001（次听-未解调）；界面变化：QLineEdit(): text='' -> text='98.5' | 可用 |
| 删除 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 发射 | 按钮 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 回放 | 按钮 | MainWindow | 是 | 否 | 是 | 状态栏：未选择录音文件；文件框：打开 | 可用 |
| 多普勒 -- | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：未绑定卫星目标 | 可用 |
| 录音 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 断开 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 连接 | 按钮 | MainWindow | 是 | 否 | 是 | 对话框：选择 SDR 设备 | 可用 |
| 静音 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：QLabel(statusValue): text='48' -> text='0'；QPushButton(): text='静音' checked=0 enabled=1 visible=0 -> text='静音' checked=1 enabled=1 visible=0 | 可用 |
| 🌐 全天 160° | 按钮 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(全天模式（160°）): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QPushButton(): text='🌐 全天 160°（已进入）' checked=1 enabled=1 visible=1 -> text='🌐 全天 160°' checked=0 enabled=1 visible=1 | 可用 |
| 🎤 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：语音输入未配置（接麦克风后可用） | 可用 |
| 🎯 跟踪 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 📡 调到该星 | 按钮 | MainWindow | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 0；切页签 QTabWidget -> 0；— | 诚实禁用 |
| 📷 摄像机 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 🛰 云台 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 应用该解调链 | 按钮 | ModulationPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 识别信号 | 按钮 | ModulationPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 7；切页签 QTabWidget -> 7；— | 诚实禁用 |
| 刷新 | 按钮 | ModulePanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 3；切页签 QTabWidget -> 3；界面变化：QPushButton(): text='刷新' checked=0 enabled=1 visible=0 -> text='已刷新' checked=0 enabled=1 visible=0 | 可用 |
| 连接 | 按钮 | ModulePanel | 否 | 否 | 是 | — | 诚实禁用（理由未登记） |
| 保存当前图像为 PNG | 按钮 | SatelliteImagePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 停止解码 | 按钮 | SatelliteImagePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始解码 | 按钮 | SatelliteImagePanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 17；切页签 QTabWidget -> 17；— | 诚实禁用 |
| 取消 | 按钮 | ScannerPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始扫描 | 按钮 | ScannerPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 6；切页签 QTabWidget -> 6；— | 诚实禁用 |
| 启用 Web 服务 | 按钮 | ServiceSettingsPanel | 是 | 否 | 是 | 未实时触发：绑定端口起 HTTP 服务（settings_panel） | 未实时触发（代码核对） |
| 启用远程控制 | 按钮 | ServiceSettingsPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 9；切页签 QTabWidget -> 9；未实时触发：起远程控制服务（settings_panel） | 未实时触发（代码核对） |
| Max保持 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| Min保持 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 书签 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 峰值 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 瀑布平滑 | 按钮 | SpectrumPanel | 否 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 2；切页签 QTabWidget -> 2；— | 诚实禁用 |
| 调谐:中心 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 频段 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| + VFO | 按钮 | VfoPanel | 否 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 10；切页签 QTabWidget -> 10；— | 诚实禁用 |
| 循环主听 | 按钮 | VfoPanel | 否 | 否 | 是 | — | 诚实禁用 |
| Kuwahara 降噪 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='Kuwahara 降噪' checked=0 enabled=1 visible=0 -> text='Kuwahara 降噪' checked=1 enabled=1 visible=0 | 可用 |
| 中值滤波 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='中值滤波' checked=0 enabled=1 visible=0 -> text='中值滤波' checked=1 enabled=1 visible=0 | 可用 |
| 停止 | 按钮 | WeatherPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始解码 | 按钮 | WeatherPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 白平衡 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='白平衡' checked=0 enabled=1 visible=0 -> text='白平衡' checked=1 enabled=1 visible=0 | 可用 |
| 直方图均衡 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='直方图均衡' checked=0 enabled=1 visible=0 -> text='直方图均衡' checked=1 enabled=1 visible=0 | 可用 |
| 选择录制文件... | 按钮 | WeatherPanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 4；切页签 QTabWidget -> 4；文件框：打开 | 可用 |
| 刷新过境 | 按钮 | newSpacetimePanel | 是 | 否 | 是 | 界面变化：QLabel(): text='无数据' -> text='未获取到卫星 TLE（离线或无网络），无法计算过境' | 可用 |
| 开始分析 | 按钮 | newSpacetimePanel | 是 | 否 | 是 | 前置：切堆栈 QStackedWidget -> 5；切页签 QTabWidget -> 5；界面变化：QPushButton(): text='开始分析' checked=0 enabled=1 visible=0 -> text='停止分析' checked=1 enabled=1 visible=0 | 可用 |
| 停止跟踪 | 按钮 | satTrackPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 开始跟踪 | 按钮 | satTrackPanel | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 1；切页签 QTabWidget -> 1；未实时触发：按秒轮询硬件指向（sat_track_panel.start_tracking）；真跑会起定时器并写后端 | 未实时触发（代码核对） |
| 更新 TLE | 按钮 | satTrackPanel | 是 | 是 | 是 | 未实时触发：联网拉 Celestrak TLE（sat_track_panel） | 未实时触发（代码核对） |
| Cancel | 按钮 | 对话框:AI 配置 | 是 | 是 | 是 | 前置：打开对话框 AI 配置；界面变化：QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0；QDialog(AI 配置): visible=1 -> visible=0 | 可用 |
| OK | 按钮 | 对话框:AI 配置 | 是 | 是 | 是 | 前置：打开对话框 AI 配置；界面变化：QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0；QDialog(AI 配置): visible=1 -> visible=0 | 可用 |
| 拉取挂载点列表 | 按钮 | 对话框:NTRIP 设置 | 是 | 是 | 是 | 未实时触发：发起 NTRIP 网络请求（ntrip_panel） | 未实时触发（代码核对） |
| 断开 | 按钮 | 对话框:NTRIP 设置 | 否 | 是 | 是 | — | 诚实禁用 |
| 连接 | 按钮 | 对话框:NTRIP 设置 | 是 | 是 | 是 | 弹窗：警告「无法连接」请填写 Caster Host 与 Mountpoint。 | 可用 |
| Cancel | 按钮 | 对话框:连接 GNSS | 是 | 是 | 是 | 前置：打开对话框 连接 GNSS；界面变化：QDialog(连接 GNSS): visible=1 -> visible=0；QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0 | 可用 |
| OK | 按钮 | 对话框:连接 GNSS | 是 | 是 | 是 | 前置：打开对话框 连接 GNSS；界面变化：QDialog(连接 GNSS): visible=1 -> visible=0；QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0 | 可用 |
| Cancel | 按钮 | 对话框:选择 SDR 设备 | 是 | 是 | 是 | 前置：打开对话框 选择 SDR 设备；界面变化：QCheckBox(): text='Offset Tuning（直流抵消）' checked=0 enabled=1 visible=1 -> text='Offset Tuning（直流抵消）' checked=0 enabled=1 visible=0；QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QPushButton(): text='刷新设备列表' checked=0 enabled=1 visible=1 -> text='刷新设备列表' checked=0 enabled=1 visible=0；QDialog(选择 SDR 设备): visible=1 -> visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0 | 可用 |
| OK | 按钮 | 对话框:选择 SDR 设备 | 是 | 是 | 是 | 界面变化：QCheckBox(): text='Offset Tuning（直流抵消）' checked=0 enabled=1 visible=1 -> text='Offset Tuning（直流抵消）' checked=0 enabled=1 visible=0；QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QPushButton(): text='刷新设备列表' checked=0 enabled=1 visible=1 -> text='刷新设备列表' checked=0 enabled=1 visible=0；QDialog(选择 SDR 设备): visible=1 -> visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0 | 可用 |
| Offset Tuning（直流抵消） | 按钮 | 对话框:选择 SDR 设备 | 是 | 否 | **否** | 无 clicked/toggled 槽；布尔值在 `_connect_dialog` 里经 `offset_chk.isChecked()` 读取后下发后端（main_window.py 设备参数区） | 输入控件（无独立槽，值被下游读取） |
| 刷新设备列表 | 按钮 | 对话框:选择 SDR 设备 | 是 | 是 | 是 | 前置：打开对话框 选择 SDR 设备；状态栏：未发现 SDR 设备（请检查 USB/驱动后点刷新）；界面变化：QPushButton(): text='已刷新' checked=0 enabled=1 visible=0 -> text='刷新' checked=0 enabled=1 visible=0 | 可用 |
| 启用自动增益 (AGC) | 按钮 | 对话框:选择 SDR 设备 | 是 | 否 | **否** | 无 clicked/toggled 槽；布尔值在 `_connect_dialog` 里经 `agc_chk.isChecked()` 读取后下发后端（main_window.py 设备参数区） | 输入控件（无独立槽，值被下游读取） |

## 未覆盖项（如实列出，不计入通过）

- 右键菜单（频谱「删除此 VFO」）在交互时才构造；已源码核对：菜单项选中后调用 `_delete_vfo`，属可用。
- 工具栏 `QWidgetAction` 是控件容器，本身不可点，不计入动作数。
- Qt 自带的页签滚动/工具栏折叠按钮不是业务动作，已剔除。
- `SKIP_LIVE` 里的动作只做代码核对，未实时触发（起线程/联网/写用户数据）。

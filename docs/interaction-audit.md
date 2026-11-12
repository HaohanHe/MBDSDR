# 交互覆盖审计（死按钮 / 假按钮 / 静默异常）

> 由 `scripts/ui_interaction_audit.py` 运行时遍历生成，请勿手改。
> 环境：offscreen + `MBDSDR_NULL_AUDIO=1` + `MBDSDR_NO_HW=1` + `MBDSDR_OFFLINE=1`，HOME/QSettings 隔离到临时目录。
> 口径：`receivers()==0` = 真死；有接收者但触发后无状态/信号下游/窗口/弹窗变化 = 假活；触发抛出或被 Qt 吞掉的异常单列。
> 防误报：触发前先空跑事件循环，把 UTC 时钟/SNR 读数等自变字段识别为 volatile 并从 diff 里剔除；运行时新建的同类对话框按「标题+控件名」去重。

## 结论

- 遍历动作 **353** 个（QAction 55 / QAbstractButton 298；enabled 298 / visible 113）
- **真死** 0 个
- **假活** 0 个
- **抛异常** 0 个
- 诚实禁用 53 个；未实时触发 8 个；输入控件 2 个

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

> 证据等级 E4：本段数字未验证，引用前须复核（判据见 docs/evidence-standard.md）。

| 动作 | 类型 | 所在面板 | 启用 | 可见 | 有槽 | 可观察结果 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ADS-B | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='ADS-B' checked=0 enabled=1 visible=0 -> text='ADS-B' checked=1 enabled=1 visible=0 | 可用 |
| ANR | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='ANR' checked=0 enabled=1 visible=1 -> text='ANR' checked=1 enabled=1 visible=1；QPushButton(): text='采样噪声底' checked=0 enabled=0 visible=0 -> text='采样噪声底' checked=0 enabled=0 visible=1；QLabel(): text='强度' visible=0 -> text='强度' visible=1；QLabel(): text='降噪类型' visible=0 -> text='降噪类型' visible=1；QLabel(): text='α 2.0' visible=0 -> text='α 2.0' visible=1 | 可用 |
| APRS | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(hintLabel): text='还没有收到数据包\n调到 144.390 MHz（FM），等附近台站发报后会自动列出' visible=0 -> text='还没有收到数据包\n调到 144.390 MHz（FM），等附近台站发报后会自动列出' visible=1；QLabel(): text='过滤:' visible=0 -> text='过滤:' visible=1；QLabel(): text='未连接（等待 144.390MHz 数据包）' visible=0 -> text='未连接（等待 144.390MHz 数据包）' visible=1；_SectionHeader(rightSectionHeader): text='APRS' checked=0 enabled=1 visible=1 -> text='APRS' checked=1 enabled=1 visible=1 | 可用 |
| VFO | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(): text='循环主听' checked=0 enabled=0 visible=0 -> text='循环主听' checked=0 enabled=0 visible=1；_SectionHeader(rightSectionHeader): text='VFO' checked=0 enabled=1 visible=1 -> text='VFO' checked=1 enabled=1 visible=1；QLabel(statusValue): text='未连接' visible=0 -> text='未连接' visible=1；QPushButton(): text='+ VFO' checked=0 enabled=0 visible=0 -> text='+ VFO' checked=0 enabled=0 visible=1；QLabel(hintLabel): text='还没有 VFO\n连上设备后点右上「+ VFO」新建一个' visible=0 -> text='还没有 VFO\n连上设备后点右上「+ VFO」新建一个' visible=1 | 可用 |
| 主工具栏 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='—' visible=0 -> text='—' visible=1；QPushButton(): text='☰' checked=0 enabled=1 visible=0 -> text='☰' checked=0 enabled=1 visible=1；QAction(主工具栏): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QPushButton(recordButton): text='录音' checked=0 enabled=0 visible=0 -> text='录音' checked=0 enabled=0 visible=1；QPushButton(): text='回放' checked=0 enabled=1 visible=0 -> text='回放' checked=0 enabled=1 visible=1 | 可用 |
| 书签 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(): text='导出 CSV' checked=0 enabled=1 visible=0 -> text='导出 CSV' checked=0 enabled=1 visible=1；QPushButton(): text='添加当前频率' checked=0 enabled=1 visible=0 -> text='添加当前频率' checked=0 enabled=1 visible=1；QLabel(hintLabel): text='0 条书签' visible=0 -> text='0 条书签' visible=1；QPushButton(): text='导入 CSV' checked=0 enabled=1 visible=0 -> text='导入 CSV' checked=0 enabled=1 visible=1；QPushButton(): text='最近书签' checked=0 enabled=1 visible=0 -> text='最近书签' checked=0 enabled=1 visible=1 | 可用 |
| 卫星云图 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='行数: 0  \| 同步: --  \| 信号质量: --' visible=0 -> text='行数: 0  \| 同步: --  \| 信号质量: --' visible=1；QLabel(): text='卫星:' visible=0 -> text='卫星:' visible=1；QPushButton(): text='开始解码' checked=0 enabled=0 visible=0 -> text='开始解码' checked=0 enabled=0 visible=1；QLabel(): text='等待卫星下行信号' visible=0 -> text='等待卫星下行信号' visible=1；QPushButton(): text='停止解码' checked=0 enabled=0 visible=0 -> text='停止解码' checked=0 enabled=0 visible=1 | 可用 |
| 增益 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='-- dB' visible=0 -> text='-- dB' visible=1；_SectionHeader(rightSectionHeader): text='增益' checked=0 enabled=1 visible=1 -> text='增益' checked=1 enabled=1 visible=1；QLabel(statusValue): text='总增益: 未连接' visible=0 -> text='总增益: 未连接' visible=1；QLabel(): text='VGA:' visible=0 -> text='VGA:' visible=1；QLabel(): text='Mixer:' visible=0 -> text='Mixer:' visible=1 | 可用 |
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
| 扫频 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(hintLabel): text='未连接' visible=0 -> text='未连接' visible=1；QPushButton(): text='开始扫描' checked=0 enabled=0 visible=0 -> text='开始扫描' checked=0 enabled=0 visible=1；QPushButton(): text='取消' checked=0 enabled=0 visible=0 -> text='取消' checked=0 enabled=0 visible=1；_SectionHeader(rightSectionHeader): text='扫频' checked=0 enabled=1 visible=1 -> text='扫频' checked=1 enabled=1 visible=1；QLabel(scanEmptyHint): text='还没有活动段\n连上设备后点「开始扫描」' visible=0 -> text='还没有活动段\n连上设备后点「开始扫描」' visible=1 | 可用 |
| 文件(&F) / 断开连接 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：当前未连接 SDR，没有可断开的设备；界面变化：QLabel(): text='空闲' visible=0 -> text='已停止' visible=0 | 可用 |
| 文件(&F) / 连接设备... | 菜单项 | MainWindow | 是 | 是 | 是 | 对话框：选择 SDR 设备 | 可用 |
| 文件(&F) / 退出 | 菜单项 | MainWindow | 是 | 是 | 是 | 未实时触发：关闭主窗口退出进程；等价于 win.close()，审计窗口还要继续用，不真退出 | 未实时触发（代码核对） |
| 星座图 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(constellationGnssSdr): text='＋ 叠加上 SDR' checked=0 enabled=1 visible=0 -> text='＋ 叠加上 SDR' checked=0 enabled=1 visible=1；QPushButton(constellationGnssOnly): text='只看 GNSS' checked=1 enabled=1 visible=0 -> text='只看 GNSS' checked=1 enabled=1 visible=1；QLabel(statusValue): text='GNSS 未连接 · 无可见卫星' visible=0 -> text='GNSS 未连接 · 无可见卫星' visible=1；_SectionHeader(rightSectionHeader): text='星座图' checked=0 enabled=1 visible=1 -> text='星座图' checked=1 enabled=1 visible=1 | 可用 |
| 服务设置 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='服务设置' checked=0 enabled=1 visible=1 -> text='服务设置' checked=1 enabled=1 visible=1；QLabel(): text='状态：未监听' visible=0 -> text='状态：未监听' visible=1；QLabel(): text='状态：未启动' visible=0 -> text='状态：未启动' visible=1；QLabel(): text='监听地址' visible=0 -> text='监听地址' visible=1；QLabel(): text='端口' visible=0 -> text='端口' visible=1 | 可用 |
| 视图(&V) / 主题 / CarWith 深色 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:12' visible=1 -> text='UTC 10:30:14' visible=1；QComboBox(): idx=2 text='高对比' -> idx=3 text='CarWith 深色'；QAction(CarWith 深色): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 视图(&V) / 主题 / Figma 深色 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(Figma 深色): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QLabel(statusValue): text='UTC 10:30:14' visible=1 -> text='UTC 10:30:15' visible=1；QComboBox(): idx=3 text='CarWith 深色' -> idx=4 text='Figma 深色' | 可用 |
| 视图(&V) / 主题 / 深色 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:09' visible=1 -> text='UTC 10:30:10' visible=1；QAction(深色): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QComboBox(): idx=0 text='默认' -> idx=1 text='深色' | 可用 |
| 视图(&V) / 主题 / 高对比 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:10' visible=1 -> text='UTC 10:30:12' visible=1；QAction(高对比): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QComboBox(): idx=1 text='深色' -> idx=2 text='高对比' | 可用 |
| 视图(&V) / 主题 / 默认 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(默认): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QLabel(statusValue): text='UTC 10:30:06' visible=1 -> text='UTC 10:30:09' visible=1；QComboBox(): idx=3 text='CarWith 深色' -> idx=0 text='默认' | 可用 |
| 视图(&V) / 全天模式（160°） | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(): text='看整片天' checked=0 enabled=1 visible=0 -> text='正在看整片天' checked=1 enabled=1 visible=0；QAction(全天模式（160°）): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1 | 可用 |
| 视图(&V) / 全屏 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：已切换全屏状态（F11 可再切回）；界面变化：QPushButton(planetariumFullscreen): text='⛶ 全屏' checked=0 enabled=1 visible=0 -> text='🗗 退出全屏' checked=0 enabled=1 visible=0；__window__: visible=1 fullscreen=0 maximized=0 minimized=0 geo=860x736 -> visible=1 fullscreen=1 maximized=0 minimized=0 geo=800x800；QPushButton(skyFullscreenInline): text='⛶' checked=0 enabled=1 visible=1 -> text='⊞' checked=0 enabled=1 visible=1 | 可用 |
| 视图(&V) / 星象仪模式 | 菜单项 | MainWindow | 是 | 是 | 是 | 新窗口：MBDSDR · 星象仪；界面变化：QAction(星象仪模式): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QLabel(hintLabel): text='正在查找 SDR 设备…没有设备也可以先看星象仪模式和 AI 对话。' visible=0 -> text='正在查找 SDR 设备…没有设备也可以先看星象仪模式和 AI 对话。' visible=1；QToolButton(spectrumMoreButton): text='更多' checked=0 enabled=1 visible=0 -> text='更多' checked=0 enabled=1 visible=1；QLabel(dbCapLabel): text='最大' visible=0 -> text='最大' visible=1；QLabel(statusValue): text='— MHz' visible=0 -> text='— MHz' visible=1 | 可用 |
| 视图(&V) / 显示瀑布图 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(显示瀑布图): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 视图(&V) / 触屏模式 / 强制触屏 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：触屏模式：强制触屏；界面变化：QAction(强制触屏): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QAction(自动检测): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QLabel(statusValue): text='UTC 10:30:15' visible=1 -> text='UTC 10:30:17' visible=1 | 可用 |
| 视图(&V) / 触屏模式 / 强制鼠标 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：触屏模式：强制鼠标；界面变化：QAction(强制触屏): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；QLabel(statusValue): text='UTC 10:30:17' visible=1 -> text='UTC 10:30:18' visible=1；QAction(强制鼠标): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1 | 可用 |
| 视图(&V) / 触屏模式 / 自动检测 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：触屏模式：自动检测 | 可用 |
| 视图(&V) / 重置窗口位置 | 菜单项 | MainWindow | 是 | 是 | 是 | 状态栏：窗口已重置到主屏中央；界面变化：__window__: visible=1 fullscreen=1 maximized=0 minimized=0 geo=800x800 -> visible=1 fullscreen=0 maximized=0 minimized=0 geo=860x800 | 可用 |
| 设备 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='采样率(Hz)' visible=0 -> text='采样率(Hz)' visible=1；QLabel(statusValue): text='= 1.024 MS/s' visible=0 -> text='= 1.024 MS/s' visible=1；QLabel(): text='PPM' visible=0 -> text='PPM' visible=1；_SectionHeader(rightSectionHeader): text='设备' checked=0 enabled=1 visible=1 -> text='设备' checked=1 enabled=1 visible=1；QLabel(): text='输出设备' visible=0 -> text='输出设备' visible=1 | 可用 |
| 设置 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(): text='重启后生效：Qt 翻译器必须在窗口创建前安装，切换后重启应用才生效。' visible=0 -> text='重启后生效：Qt 翻译器必须在窗口创建前安装，切换后重启应用才生效。' visible=1；_SectionHeader(rightSectionHeader): text='卫星与多普勒' checked=0 enabled=1 visible=0 -> text='卫星与多普勒' checked=0 enabled=1 visible=1；QLabel(hintLabel): text='与 GNSS 接收机一致的波特率；多数模块出厂为 9600。' visible=0 -> text='与 GNSS 接收机一致的波特率；多数模块出厂为 9600。' visible=1；_SectionHeader(rightSectionHeader): text='设置' checked=0 enabled=1 visible=1 -> text='设置' checked=1 enabled=1 visible=1；QToolButton(): text='默认' checked=0 enabled=1 visible=0 -> text='默认' checked=0 enabled=1 visible=1 | 可用 |
| 调制识别 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='调制识别' checked=0 enabled=1 visible=1 -> text='调制识别' checked=1 enabled=1 visible=1；QLabel(): text='--' visible=0 -> text='--' visible=1；QLabel(hintLabel): text='未连接，无法识别' visible=0 -> text='未连接，无法识别' visible=1；QLabel(): text='--' visible=0 -> text='--' visible=1；QLabel(): text='建议带宽' visible=0 -> text='建议带宽' visible=1 | 可用 |
| 面板(&P) / ADS-B 航路 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='ADS-B' checked=0 enabled=1 visible=1 -> text='ADS-B' checked=0 enabled=1 visible=0；QAction(ADS-B 航路): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / AI 助手 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(AI 助手): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 卫星跟踪 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(卫星跟踪): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；_SectionHeader(rightSectionHeader): text='卫星跟踪' checked=0 enabled=1 visible=1 -> text='卫星跟踪' checked=0 enabled=1 visible=0 | 可用 |
| 面板(&P) / 恢复出厂布局 | 菜单项 | MainWindow | 是 | 是 | 是 | 弹窗：询问「恢复出厂布局」清除所有自定义布局并恢复到默认「专注」布局？ | 可用 |
| 面板(&P) / 控制 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QCheckBox(): text='IQ 平衡' checked=1 enabled=0 visible=1 -> text='IQ 平衡' checked=1 enabled=0 visible=0；QCheckBox(): text='DC 去除' checked=1 enabled=0 visible=1 -> text='DC 去除' checked=1 enabled=0 visible=0；QLabel(): text='频段:' visible=1 -> text='频段:' visible=0；QLabel(): text='步进:' visible=1 -> text='步进:' visible=0；QPushButton(): text='-' checked=0 enabled=0 visible=1 -> text='-' checked=0 enabled=0 visible=0 | 可用 |
| 面板(&P) / 新时空 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(新时空): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；_SectionHeader(rightSectionHeader): text='新时空' checked=0 enabled=1 visible=1 -> text='新时空' checked=0 enabled=1 visible=0 | 可用 |
| 面板(&P) / 星象仪模式 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(星象仪模式): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 模块/信号流 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(模块/信号流): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；_SectionHeader(rightSectionHeader): text='模块' checked=0 enabled=1 visible=1 -> text='模块' checked=0 enabled=1 visible=0 | 可用 |
| 面板(&P) / 气象云图 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(气象云图): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1；_SectionHeader(rightSectionHeader): text='气象云图' checked=0 enabled=1 visible=1 -> text='气象云图' checked=0 enabled=1 visible=0 | 可用 |
| 面板(&P) / 状态 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='状态' checked=0 enabled=1 visible=1 -> text='状态' checked=0 enabled=1 visible=0；QAction(状态): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 能力矩阵（◧ 事实源） | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QAction(能力矩阵（◧ 事实源）): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1；QToolButton(): text='+' checked=0 enabled=1 visible=0 -> text='+' checked=0 enabled=1 visible=1；QToolButton(): text='⤢' checked=0 enabled=1 visible=0 -> text='⤢' checked=0 enabled=1 visible=1；QLabel(floatingPanelTitle): text='◧ 能力矩阵 · 事实源自动生成' visible=0 -> text='◧ 能力矩阵 · 事实源自动生成' visible=1；QToolButton(): text='⤶' checked=0 enabled=1 visible=0 -> text='⤶' checked=0 enabled=1 visible=1 | 可用 |
| 面板(&P) / 频谱 | 菜单项 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(hintLabel): text='正在查找 SDR 设备…没有设备也可以先看星象仪模式和 AI 对话。' visible=1 -> text='正在查找 SDR 设备…没有设备也可以先看星象仪模式和 AI 对话。' visible=0；QToolButton(spectrumMoreButton): text='更多' checked=0 enabled=1 visible=1 -> text='更多' checked=0 enabled=1 visible=0；QLabel(dbCapLabel): text='最大' visible=1 -> text='最大' visible=0；QToolButton(ScrollRightButton): text='' checked=0 enabled=0 visible=0 -> text='' checked=0 enabled=1 visible=0；QLabel(statusValue): text='— MHz' visible=1 -> text='— MHz' visible=0 | 可用 |
| 启用 ANR | 按钮 | AnrPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 采样噪声底 | 按钮 | AnrPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 删除 | 按钮 | BookmarkPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 导入 CSV | 按钮 | BookmarkPanel | 是 | 否 | 是 | 文件框：打开 | 可用 |
| 导出 CSV | 按钮 | BookmarkPanel | 是 | 否 | 是 | 文件框：保存 | 可用 |
| 最近书签 | 按钮 | BookmarkPanel | 是 | 否 | 是 | 弹窗：提示「最近书签」还不知道当前频率（设备未连接或未调谐）。<br>先连接并调谐到任意频率，再点「最近书签」。 | 可用 |
| 添加当前频率 | 按钮 | BookmarkPanel | 是 | 否 | 是 | 弹窗：提示「添加书签」无当前频率（未连接 SDR 或未调谐）。<br>可手动输入频率。；文件框：输入 | 可用 |
| 编辑 | 按钮 | BookmarkPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 只看 GNSS | 按钮 | ConstellationPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 清空 | 按钮 | ConstellationPanel | 是 | 否 | 是 | 状态栏：星座图已清空，统计已重置 | 可用 |
| 网格 | 按钮 | ConstellationPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='网格' checked=1 enabled=1 visible=0 -> text='网格' checked=0 enabled=1 visible=0 | 可用 |
| ＋ 叠加上 SDR | 按钮 | ConstellationPanel | 是 | 否 | 是 | 界面变化：QPushButton(constellationGnssSdr): text='＋ 叠加上 SDR' checked=0 enabled=1 visible=0 -> text='＋ 叠加上 SDR' checked=1 enabled=1 visible=0；QPushButton(constellationGnssOnly): text='只看 GNSS' checked=1 enabled=1 visible=0 -> text='只看 GNSS' checked=0 enabled=1 visible=0 | 可用 |
| + | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| - | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| AGC 自动增益 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| DC 去除 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| IQ 平衡 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 偏移调谐 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 删除选中 | 按钮 | ControlPanel | 是 | 否 | 是 | 弹窗：提示「内置书签」「航空应急频率 121.500」是内置频率，不能删除。下一步：先调到你要保留的频点，再用「收藏当前频率」保存为你自己的自定义书签。 | 可用 |
| 开始录音 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 收藏当前频率 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 自动 | 按钮 | ControlPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='自动' checked=1 enabled=1 visible=0 -> text='自动' checked=0 enabled=1 visible=0 | 可用 |
| 调谐 | 按钮 | ControlPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 刷新 | 按钮 | DevicePanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 断开 | 按钮 | DevicePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 连接 | 按钮 | DevicePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 硬件 AGC | 按钮 | GainPanel | 否 | 否 | 是 | — | 诚实禁用 |
| + VFO | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：新建 VFO vfo001 @ 98.500 MHz | 可用 |
| A 星名 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ADS-B | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='ADS-B' checked=1 enabled=1 visible=0 -> text='ADS-B' checked=0 enabled=1 visible=0；QLabel(statusValue): text='UTC 10:30:24' visible=1 -> text='UTC 10:30:25' visible=1 | 可用 |
| AI 智能体 | 按钮 | MainWindow | 是 | 否 | 是 | — | 探测前已被销毁（前面动作重建面板），不计入 |
| ANR | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='ANR' checked=1 enabled=1 visible=0 -> text='ANR' checked=0 enabled=1 visible=0 | 可用 |
| APRS | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='APRS' checked=1 enabled=1 visible=0 -> text='APRS' checked=0 enabled=1 visible=0 | 可用 |
| VFO | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='VFO' checked=1 enabled=1 visible=0 -> text='VFO' checked=0 enabled=1 visible=0 | 可用 |
| ≈ 大气 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ≡ 赤道网 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ⊞ 叠层 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ⌂ | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：已回到频谱主页 | 可用 |
| ⏱ 1× | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：时间流速 60×（天空按同一时刻重算） | 可用 |
| ▦ 图层 | 按钮 | MainWindow | 是 | 是 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ▽ 地景 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ◍ 深空 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ◎ 方位网 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ☰ | 按钮 | MainWindow | 是 | 是 | 是 | 界面变化：QPushButton(): text='导出 CSV' checked=0 enabled=1 visible=1 -> text='导出 CSV' checked=0 enabled=1 visible=0；QLabel(): text='采样率(Hz)' visible=1 -> text='采样率(Hz)' visible=0；QLabel(): text='重启后生效：Qt 翻译器必须在窗口创建前安装，切换后重启应用才生效。' visible=1 -> text='重启后生效：Qt 翻译器必须在窗口创建前安装，切换后重启应用才生效。' visible=0；_SectionHeader(rightSectionHeader): text='调制识别' checked=1 enabled=1 visible=1 -> text='调制识别' checked=1 enabled=1 visible=0；QLabel(hintLabel): text='还没有收到数据包\n调到 144.390 MHz（FM），等附近台站发报后会自动列出' visible=1 -> text='还没有收到数据包\n调到 144.390 MHz（FM），等附近台站发报后会自动列出' visible=0 | 可用 |
| ⛶ | 按钮 | MainWindow | 是 | 否 | 是 | 状态栏：已切换全屏状态（F11 可再切回） | 可用 |
| ⛶ 全屏 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：已切换全屏状态（F11 可再切回） | 可用 |
| ✦ 卫星 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ✧ 星座 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ⟳ 轨道 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ⧉ | 按钮 | MainWindow | 是 | 是 | 是 | 新窗口：菜单：QMenu | 可用 |
| 书签 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='书签' checked=1 enabled=1 visible=0 -> text='书签' checked=0 enabled=1 visible=0 | 可用 |
| 今夜 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：先点「先看看示例天空」或设置观测位置，再切到今夜 | 可用 |
| 切换主听 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：主听切换 → vfo001（次听-未解调）；界面变化：QLineEdit(): text='' style='' -> text='98.5' style=''；QComboBox(): idx=-1 text='' -> idx=0 text='FM' | 可用 |
| 卫星云图 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='卫星云图' checked=1 enabled=1 visible=0 -> text='卫星云图' checked=0 enabled=1 visible=0 | 可用 |
| 卫星跟踪 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='卫星跟踪' checked=0 enabled=1 visible=0 -> text='卫星跟踪' checked=1 enabled=1 visible=0 | 可用 |
| 发射 | 按钮 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 回放 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：未选择录音文件；文件框：打开 | 可用 |
| 增益 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='增益' checked=1 enabled=1 visible=0 -> text='增益' checked=0 enabled=1 visible=0 | 可用 |
| 多普勒 -- | 按钮 | MainWindow | 是 | 否 | 是 | 状态栏：未绑定卫星目标 | 可用 |
| 夜视 | 按钮 | MainWindow | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 存图 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：已存图：C:/Users/xxhzb/OneDrive/Pictures\MBDSDR\sky_20261111_183026.png | 可用 |
| 录音 | 按钮 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 扫频 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='扫频' checked=1 enabled=1 visible=0 -> text='扫频' checked=0 enabled=1 visible=0 | 可用 |
| 控制 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='控制' checked=1 enabled=1 visible=0 -> text='控制' checked=0 enabled=1 visible=0 | 可用 |
| 断开 | 按钮 | MainWindow | 否 | 是 | 是 | — | 诚实禁用 |
| 新时空 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：QPushButton(): text='刷新' checked=0 enabled=0 visible=0 -> text='已刷新' checked=0 enabled=1 visible=0；QLabel(): text='正在枚举设备…' visible=0 -> text='未发现SDR设备\n插上 RTL-SDR/HackRF 后点刷新。' visible=0；_SectionHeader(rightSectionHeader): text='新时空' checked=0 enabled=1 visible=0 -> text='新时空' checked=1 enabled=1 visible=0 | 可用 |
| 星座图 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='星座图' checked=1 enabled=1 visible=0 -> text='星座图' checked=0 enabled=1 visible=0 | 可用 |
| 服务设置 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='服务设置' checked=1 enabled=1 visible=0 -> text='服务设置' checked=0 enabled=1 visible=0 | 可用 |
| 本后端发射：未连接设备（能力未知） | 按钮 | MainWindow | 是 | 是 | 是 | 新窗口：qtooltip_label | 可用 |
| 模块 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='模块' checked=0 enabled=1 visible=0 -> text='模块' checked=1 enabled=1 visible=0 | 可用 |
| 气象云图 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='气象云图' checked=0 enabled=1 visible=0 -> text='气象云图' checked=1 enabled=1 visible=0 | 可用 |
| 状态 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='状态' checked=0 enabled=1 visible=0 -> text='状态' checked=1 enabled=1 visible=0 | 可用 |
| 看整片天 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：QAction(全天模式（160°）): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 设备 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='设备' checked=1 enabled=1 visible=0 -> text='设备' checked=0 enabled=1 visible=0 | 可用 |
| 设置 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='设置' checked=1 enabled=1 visible=0 -> text='设置' checked=0 enabled=1 visible=0 | 可用 |
| 详细 | 按钮 | MainWindow | 是 | 是 | 是 | 新窗口：QListWidget；界面变化：QLabel(statusValue): text='多普勒: —' visible=0 -> text='多普勒: —' visible=1；QToolButton(txCapability): text='本后端发射：未连接设备（能力未知）' checked=0 enabled=1 visible=0 -> text='本后端发射：未连接设备（能力未知）' checked=0 enabled=1 visible=1；QLabel(statusValue): text='增益: —' visible=0 -> text='增益: —' visible=1；QToolButton(topBarAction): text='详细' checked=0 enabled=1 visible=1 -> text='收起' checked=1 enabled=1 visible=1；QLabel(statusValue): text='带宽: —' visible=0 -> text='带宽: —' visible=1 | 可用 |
| 调制识别 | 按钮 | MainWindow | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='调制识别' checked=1 enabled=1 visible=0 -> text='调制识别' checked=0 enabled=1 visible=0 | 可用 |
| 跳到分组 ▾ | 按钮 | MainWindow | 是 | 是 | 是 | 新窗口：菜单：QMenu | 可用 |
| 连接 | 按钮 | MainWindow | 是 | 是 | 是 | 对话框：选择 SDR 设备；界面变化：QLabel(statusValue): text='UTC 10:30:19' visible=1 -> text='UTC 10:30:20' visible=1 | 可用 |
| 退出星象仪<br>(Esc) | 按钮 | MainWindow | 是 | 是 | 是 | 未实时触发：星象仪浮层按钮只在星象仪形态显示（`_exit_planetarium` 里 `_planetarium_strip.hide()`，IDE 形态不可见也不可点）；审计每条动作前强制回 IDE 页，此处点击等于 no-op。星象仪形态下的真实点击路径由 `tests/test_planetarium_entry_visibility.py::test_overlay_exit_button_is_clickable` 覆盖 | 未实时触发（代码核对） |
| 静音 | 按钮 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='48' visible=0 -> text='0' visible=0；QPushButton(): text='静音' checked=0 enabled=1 visible=1 -> text='静音' checked=1 enabled=1 visible=1 | 可用 |
| 频谱 | 按钮 | MainWindow | 是 | 是 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:25' visible=1 -> text='UTC 10:30:26' visible=1 | 可用 |
| 🎤 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：语音输入未配置（接麦克风后可用） | 可用 |
| 🎯 跟踪 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 📍 地点 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：地点面板已打开：可搜索城市 / 地图点选 / 手填经纬度 | 可用 |
| 📡 调到该星 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 📷 摄像机 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 🔍 找星 | 按钮 | MainWindow | 是 | 是 | 是 | 状态栏：输入天体名：ISS / Vega / 木星 / M42；↑↓ 选择，回车定位 | 可用 |
| 🕒 时间 | 按钮 | MainWindow | 是 | 是 | 是 | 对话框：时间机器 | 可用 |
| 🛰 云台 | 按钮 | MainWindow | 否 | 否 | 是 | — | 诚实禁用 |
| 应用该解调链 | 按钮 | ModulationPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 识别信号 | 按钮 | ModulationPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 已刷新 | 按钮 | ModulePanel | 是 | 否 | 是 | 界面变化：QPushButton(): text='刷新' checked=0 enabled=1 visible=0 -> text='刷新' checked=0 enabled=0 visible=0；QLabel(): text='未发现SDR设备\n插上 RTL-SDR/HackRF 后点刷新。' visible=0 -> text='正在枚举设备…' visible=0 | 可用 |
| 连接 | 按钮 | ModulePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 保存当前图像为 PNG | 按钮 | SatelliteImagePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 停止解码 | 按钮 | SatelliteImagePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始解码 | 按钮 | SatelliteImagePanel | 否 | 否 | 是 | — | 诚实禁用 |
| 取消 | 按钮 | ScannerPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始扫描 | 按钮 | ScannerPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 启用 Web 服务 | 按钮 | ServiceSettingsPanel | 是 | 否 | 是 | 未实时触发：绑定端口起 HTTP 服务（settings_panel） | 未实时触发（代码核对） |
| 启用远程控制 | 按钮 | ServiceSettingsPanel | 是 | 否 | 是 | 未实时触发：起远程控制服务（settings_panel） | 未实时触发（代码核对） |
| 恢复本组默认 | 按钮 | ServiceSettingsPanel | 是 | 否 | 是 | 前置：扰动 QSpinBox: 7356 -> 7358；界面变化：QSpinBox(): value='7358' -> value='7356'；QLineEdit(qt_spinbox_lineedit): text='7358' style='' -> text='7356' style=''；QLabel(hintLabel): text='' visible=0 -> text='已恢复本组默认值' visible=0 | 可用 |
| AI | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='AI' checked=0 enabled=1 visible=0 -> text='AI' checked=1 enabled=1 visible=0 | 可用 |
| 保存 | 按钮 | SettingsPanel | 是 | 否 | 是 | 文件框：输入 | 可用 |
| 删除 | 按钮 | SettingsPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 卫星与多普勒 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='卫星与多普勒' checked=0 enabled=1 visible=0 -> text='卫星与多普勒' checked=1 enabled=1 visible=0；QLabel(statusValue): text='UTC 10:30:23' visible=1 -> text='UTC 10:30:24' visible=1 | 可用 |
| 存储与日志 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='存储与日志' checked=0 enabled=1 visible=0 -> text='存储与日志' checked=1 enabled=1 visible=0 | 可用 |
| 导入配置 | 按钮 | SettingsPanel | 是 | 否 | 是 | 文件框：打开 | 可用 |
| 导出配置 | 按钮 | SettingsPanel | 是 | 否 | 是 | 文件框：保存 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(): text='—' visible=1 -> text='—' visible=0；QLabel(statusValue): text='多普勒: —' visible=1 -> text='多普勒: —' visible=0；QToolButton(txCapability): text='本后端发射：未连接设备（能力未知）' checked=0 enabled=1 visible=1 -> text='本后端发射：未连接设备（能力未知）' checked=0 enabled=1 visible=0；QPushButton(): text='☰' checked=0 enabled=1 visible=1 -> text='☰' checked=0 enabled=1 visible=0；QLabel(statusValue): text='增益: —' visible=1 -> text='增益: —' visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：已归位「AI 智能体」；界面变化：QLabel(hintLabel): text='已恢复默认：专注页面（默认极简）' visible=0 -> text='已保存：自由窗口（面板可拖出）' visible=0；QToolButton(topBarAction): text='⧉' checked=0 enabled=1 visible=1 -> text='⧉' checked=0 enabled=1 visible=0；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0；QLabel(hintLabel): text='已恢复默认：主题' visible=0 -> text='已保存：显示瀑布图' visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0；QLabel(hintLabel): text='已恢复默认：峰值判定阈值' visible=0 -> text='已保存：标注峰值' visible=0；QCheckBox(): text='峰值' checked=1 enabled=0 visible=0 -> text='峰值' checked=0 enabled=0 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='瀑布平滑' checked=0 enabled=0 visible=0 -> text='瀑布平滑' checked=1 enabled=0 visible=0；QLabel(hintLabel): text='已恢复默认：标注峰值' visible=0 -> text='已保存：瀑布时间平滑' visible=0；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0；QLabel(hintLabel): text='已恢复默认：瀑布时间平滑' visible=0 -> text='已保存：显示书签旗标' visible=0；QCheckBox(): text='书签' checked=1 enabled=0 visible=0 -> text='书签' checked=0 enabled=0 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：显示书签旗标' visible=0 -> text='已保存：显示频段彩带' visible=0；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0；QCheckBox(): text='频段' checked=1 enabled=0 visible=0 -> text='频段' checked=0 enabled=0 visible=0 | 可用 |
| 开启 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='未来 24 小时；列表按峰值仰角 ≥ 10° 筛选（峰值低于该值的过境不列出；要列 5–10° 请打开「显示低仰角过境」）' visible=0 -> text='未来 24 小时；列表按峰值仰角 ≥ 10° 筛选；「显示低仰角过境」已开启：额外列出 5–10° 的过境并标注「低仰角」' visible=0；QLabel(hintLabel): text='已恢复默认：过境搜索窗口' visible=0 -> text='已保存：显示低仰角过境' visible=0；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0 | 可用 |
| 恢复全部默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：观测位置已清空：天空回到未定位空态；界面变化：QLabel(hintLabel): text='已恢复默认：日志级别' visible=0 -> text='已恢复全部默认' visible=0 | 可用 |
| 打开目录 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：录音目录不存在：C:\Users\xxhzb\AppData\Local\Temp\mbdsdr_audit_f4rj2qc1\home\MBDSDR\reco | 可用 |
| 打开配置目录 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：无法打开目录（系统拒绝请求）：C:\Users\xxhzb\AppData\Local\Temp\mbdsdr_audit_f4rj2qc1\home/.mbd | 可用 |
| 星象仪 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='星象仪' checked=0 enabled=1 visible=0 -> text='星象仪' checked=1 enabled=1 visible=0 | 可用 |
| 显示与主题 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='显示与主题' checked=0 enabled=1 visible=0 -> text='显示与主题' checked=1 enabled=1 visible=0 | 可用 |
| 选择目录… | 按钮 | SettingsPanel | 是 | 否 | 是 | 文件框：选目录 | 可用 |
| 通用 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:20' visible=1 -> text='UTC 10:30:21' visible=1；_SectionHeader(rightSectionHeader): text='通用' checked=1 enabled=1 visible=0 -> text='通用' checked=0 enabled=1 visible=0 | 可用 |
| 频谱与解码 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：_SectionHeader(rightSectionHeader): text='频谱与解码' checked=0 enabled=1 visible=0 -> text='频谱与解码' checked=1 enabled=1 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0；QComboBox(): idx=0 text='自动检测' -> idx=2 text='强制鼠标'；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0；QLabel(hintLabel): text='' visible=0 -> text='已恢复默认：界面语言' visible=0；QComboBox(): idx=3 text='CarWith 深色' -> idx=4 text='Figma 深色' | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：触屏模式：自动检测；界面变化：QComboBox(): idx=2 text='强制鼠标' -> idx=0 text='自动检测'；QLabel(hintLabel): text='已恢复默认：界面语言' visible=0 -> text='已恢复默认：触屏适配' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：触屏适配' visible=0 -> text='已恢复默认：操作员呼号' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：操作员呼号' visible=0 -> text='已恢复默认：GNSS 串口' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：GNSS 串口' visible=0 -> text='已恢复默认：GNSS 波特率' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：GNSS 波特率' visible=0 -> text='已恢复默认：接收性能档' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已保存：专注页面（默认极简）' visible=0 -> text='已恢复默认：专注页面（默认极简）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 新窗口：AI 智能体；界面变化：QLabel(hintLabel): text='已保存：自由窗口（面板可拖出）' visible=0 -> text='已恢复默认：自由窗口（面板可拖出）' visible=0；QToolButton(topBarAction): text='⧉' checked=0 enabled=1 visible=0 -> text='⧉' checked=0 enabled=1 visible=1；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QPushButton(): text='已刷新' checked=0 enabled=1 visible=0 -> text='刷新' checked=0 enabled=1 visible=0；QLabel(hintLabel): text='已恢复默认：自由窗口（面板可拖出）' visible=0 -> text='已恢复默认：主题' visible=0；QLabel(statusValue): text='UTC 10:30:21' visible=1 -> text='UTC 10:30:23' visible=1；QComboBox(): idx=4 text='Figma 深色' -> idx=3 text='CarWith 深色'；QComboBox(): idx=4 text='Figma 深色' -> idx=3 text='CarWith 深色' | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已保存：显示瀑布图' visible=0 -> text='已恢复默认：显示瀑布图' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：显示瀑布图' visible=0 -> text='已恢复默认：当地大气压' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：当地大气压' visible=0 -> text='已恢复默认：当地气温' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：当地气温' visible=0 -> text='已恢复默认：光害等级（Bortle）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：光害等级（Bortle）' visible=0 -> text='已恢复默认：大气消光系数 k' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QComboBox(): idx=0 text='1024' -> idx=1 text='2048'；QLabel(hintLabel): text='已恢复默认：大气消光系数 k' visible=0 -> text='已恢复默认：频谱分辨率（FFT 点数）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：频谱分辨率（FFT 点数）' visible=0 -> text='已恢复默认：频谱窗函数' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：频谱窗函数' visible=0 -> text='已恢复默认：频谱平均帧数' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：频谱平均帧数' visible=0 -> text='已恢复默认：瀑布配色' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：瀑布配色' visible=0 -> text='已恢复默认：峰值判定阈值' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0；QLabel(hintLabel): text='已保存：标注峰值' visible=0 -> text='已恢复默认：标注峰值' visible=0；QCheckBox(): text='峰值' checked=0 enabled=0 visible=0 -> text='峰值' checked=1 enabled=0 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='瀑布平滑' checked=1 enabled=0 visible=0 -> text='瀑布平滑' checked=0 enabled=0 visible=0；QLabel(hintLabel): text='已保存：瀑布时间平滑' visible=0 -> text='已恢复默认：瀑布时间平滑' visible=0；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0；QLabel(hintLabel): text='已保存：显示书签旗标' visible=0 -> text='已恢复默认：显示书签旗标' visible=0；QCheckBox(): text='书签' checked=0 enabled=0 visible=0 -> text='书签' checked=1 enabled=0 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已保存：显示频段彩带' visible=0 -> text='已恢复默认：显示频段彩带' visible=0；QCheckBox(): text='开启' checked=0 enabled=1 visible=0 -> text='开启' checked=1 enabled=1 visible=0；QCheckBox(): text='频段' checked=0 enabled=0 visible=0 -> text='频段' checked=1 enabled=0 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：显示频段彩带' visible=0 -> text='已恢复默认：静噪门限默认值' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：静噪门限默认值' visible=0 -> text='已恢复默认：调谐步进' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：调谐步进' visible=0 -> text='已恢复默认：瀑布历史深度' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：观测位置已清空：天空回到未定位空态；界面变化：QLabel(spacetimeObserverLabel): text='未设置' visible=0 -> text='未设置（天空按未定位空态显示）' visible=0；QLabel(hintLabel): text='已恢复默认：瀑布历史深度' visible=0 -> text='已恢复默认：地面站纬度' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 状态栏：观测位置已清空：天空回到未定位空态；界面变化：QLabel(hintLabel): text='已恢复默认：地面站纬度' visible=0 -> text='已恢复默认：地面站经度' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：地面站经度' visible=0 -> text='已恢复默认：过境最低仰角' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：过境最低仰角' visible=0 -> text='已恢复默认：过境搜索窗口' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='未来 24 小时；列表按峰值仰角 ≥ 10° 筛选；「显示低仰角过境」已开启：额外列出 5–10° 的过境并标注「低仰角」' visible=0 -> text='未来 24 小时；列表按峰值仰角 ≥ 10° 筛选（峰值低于该值的过境不列出；要列 5–10° 请打开「显示低仰角过境」）' visible=0；QLabel(hintLabel): text='已保存：显示低仰角过境' visible=0 -> text='已恢复默认：显示低仰角过境' visible=0；QCheckBox(): text='开启' checked=1 enabled=1 visible=0 -> text='开启' checked=0 enabled=1 visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：显示低仰角过境' visible=0 -> text='已恢复默认：回答随机度（temperature）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：回答随机度（temperature）' visible=0 -> text='已恢复默认：采样截断（top_p）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：采样截断（top_p）' visible=0 -> text='已恢复默认：单次回答长度上限' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：单次回答长度上限' visible=0 -> text='已恢复默认：上下文窗口' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：上下文窗口' visible=0 -> text='已恢复默认：请求超时（秒）' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：请求超时（秒）' visible=0 -> text='已恢复默认：录音目录' visible=0 | 可用 |
| 默认 | 按钮 | SettingsPanel | 是 | 否 | 是 | 界面变化：QLabel(hintLabel): text='已恢复默认：录音目录' visible=0 -> text='已恢复默认：日志级别' visible=0 | 可用 |
| Max保持 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| Min保持 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 书签 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 峰值 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 更多 | 按钮 | SpectrumPanel | 是 | 否 | 是 | 新窗口：菜单：QMenu；界面变化：QLabel(): text='调色板:' visible=0 -> text='调色板:' visible=1；QCheckBox(): text='瀑布平滑' checked=0 enabled=0 visible=0 -> text='瀑布平滑' checked=0 enabled=0 visible=1；QLabel(): text='FFT:' visible=0 -> text='FFT:' visible=1；QLabel(): text='窗:' visible=0 -> text='窗:' visible=1；QLabel(): text='平均:' visible=0 -> text='平均:' visible=1 | 可用 |
| 瀑布平滑 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 自动量程 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 调谐:中心 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| 频段 | 按钮 | SpectrumPanel | 否 | 是 | 是 | — | 诚实禁用 |
| + VFO | 按钮 | VfoPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 循环主听 | 按钮 | VfoPanel | 否 | 否 | 是 | — | 诚实禁用 |
| Kuwahara 降噪 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='Kuwahara 降噪' checked=0 enabled=1 visible=0 -> text='Kuwahara 降噪' checked=1 enabled=1 visible=0 | 可用 |
| 中值滤波 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='中值滤波' checked=0 enabled=1 visible=0 -> text='中值滤波' checked=1 enabled=1 visible=0 | 可用 |
| 停止 | 按钮 | WeatherPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始解码 | 按钮 | WeatherPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 白平衡 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='白平衡' checked=0 enabled=1 visible=0 -> text='白平衡' checked=1 enabled=1 visible=0 | 可用 |
| 直方图均衡 | 按钮 | WeatherPanel | 是 | 否 | 是 | 界面变化：QCheckBox(): text='直方图均衡' checked=0 enabled=1 visible=0 -> text='直方图均衡' checked=1 enabled=1 visible=0 | 可用 |
| 选择录制文件... | 按钮 | WeatherPanel | 是 | 否 | 是 | 文件框：打开 | 可用 |
| + | 按钮 | floatingPanel | 是 | 是 | 是 | 界面变化：QToolButton(capCell): text='multimon\n真实注册 5 个工具' checked=0 enabled=1 visible=0 -> text='multimon\n真实注册 5 个工具' checked=0 enabled=1 visible=1；QToolButton(capCell): text='radio_control\n真实注册 8 个工具' checked=0 enabled=1 visible=0 -> text='radio_control\n真实注册 8 个工具' checked=0 enabled=1 visible=1；QToolButton(capCell): text='ui\n真实注册 2 个工具' checked=0 enabled=1 visible=0 -> text='ui\n真实注册 2 个工具' checked=0 enabled=1 visible=1；QToolButton(capCell): text='capture\n真实注册 1 个工具' checked=0 enabled=1 visible=0 -> text='capture\n真实注册 1 个工具' checked=0 enabled=1 visible=1；QToolButton(capCell): text='new_spacetime\n真实注册 12 个工具' checked=0 enabled=1 visible=0 -> text='new_spacetime\n真实注册 12 个工具' checked=0 enabled=1 visible=1 | 可用 |
| Hooks 事件总线<br>0 条钩子 · 47 类事件 · 最近 0 条 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| acquisition<br>真实注册 9 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | 新窗口：◧ 能力矩阵 · 事实源自动生成；界面变化：QAction(能力矩阵（◧ 事实源）): checked=0 enabled=1 visible=1 -> checked=1 enabled=1 visible=1 | 可用 |
| amr<br>真实注册 4 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| astronomy<br>真实注册 9 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| broadcast<br>真实注册 6 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| capture<br>真实注册 1 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| code_editor<br>真实注册 10 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| coord<br>真实注册 6 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| decode<br>真实注册 22 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| demod<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| digital<br>真实注册 4 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| digital_mode<br>真实注册 5 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| digital_modes<br>真实注册 10 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| digital_voice<br>真实注册 12 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| doppler<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| dsp<br>真实注册 9 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| encode<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| evolution<br>真实注册 6 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| fec<br>真实注册 3 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| file_tracker<br>真实注册 5 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| gimbal<br>真实注册 7 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| gnss<br>真实注册 11 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:27' visible=1 -> text='UTC 10:30:28' visible=1 | 可用 |
| gnss_monitor<br>真实注册 3 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| guardian<br>真实注册 3 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| hal<br>真实注册 7 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ham_modes<br>真实注册 13 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| judge<br>真实注册 3 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| learning<br>真实注册 4 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| memory<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| meta<br>真实注册 6 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| modem<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| modulation<br>真实注册 7 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| multimon<br>真实注册 5 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| new_spacetime<br>真实注册 12 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| orbit<br>真实注册 15 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| planning<br>真实注册 4 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| pointing<br>真实注册 1 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| pose<br>真实注册 5 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| protocol<br>真实注册 3 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| radio_control<br>真实注册 8 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| rds_decoder<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sat_groundstation<br>真实注册 7 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sat_profile<br>真实注册 4 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| satellite<br>真实注册 44 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| scheduler<br>真实注册 6 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:29' visible=1 -> text='UTC 10:30:30' visible=1 | 可用 |
| sdr<br>真实注册 15 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_ai<br>真实注册 7 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_analysis<br>真实注册 12 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_decode<br>真实注册 14 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_demod<br>真实注册 7 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_device<br>真实注册 10 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_dsp<br>真实注册 6 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_frequency<br>真实注册 9 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_gain<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_location<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_network<br>真实注册 8 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_protocol<br>真实注册 7 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:30' visible=1 -> text='UTC 10:30:31' visible=1 | 可用 |
| sdr_record<br>真实注册 3 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_satellite<br>真实注册 5 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_spectrum<br>真实注册 16 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sdr_ui<br>真实注册 9 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| signal_analysis<br>真实注册 8 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| skill<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sky<br>真实注册 5 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| spectrum<br>真实注册 8 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ssdv_rx<br>真实注册 1 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| subagent<br>真实注册 4 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| sync<br>真实注册 3 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:31' visible=1 -> text='UTC 10:30:32' visible=1 | 可用 |
| testing<br>真实注册 1 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ui<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| vna<br>真实注册 3 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| voice<br>真实注册 4 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| web<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| wireless<br>真实注册 3 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| workflow<br>真实注册 2 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| workflow_recorder<br>真实注册 5 个工具 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| ⤢ | 按钮 | floatingPanel | 是 | 是 | 是 | 新窗口：◧ 能力矩阵 · 事实源自动生成 | 可用 |
| ⤶ | 按钮 | floatingPanel | 是 | 是 | 是 | 状态栏：已收起能力抽屉；界面变化：QAction(能力矩阵（◧ 事实源）): checked=1 enabled=1 visible=1 -> checked=0 enabled=1 visible=1 | 可用 |
| 子代理<br>常驻 · SubagentManager | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 工作流引擎<br>常驻 · WorkflowEngine | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 插件系统<br>常驻 · PluginManager | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 星名文化（只读）<br>亮星 124 条 · 星座 27 条 · 仓库数据 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 智能编排<br>0 个任务 · 并发上限 4 | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 本地能力协商<br>模型未配置时仍可执行 496 个可用工具 | 按钮 | floatingPanel | 是 | 否 | 是 | 界面变化：QLabel(statusValue): text='UTC 10:30:32' visible=1 -> text='UTC 10:30:33' visible=1 | 可用 |
| 调度器<br>常驻 · Scheduler | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 长期记忆<br>常驻 · MemoryStore | 按钮 | floatingPanel | 是 | 否 | 是 | — | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 刷新过境 | 按钮 | newSpacetimePanel | 是 | 否 | 是 | 界面变化：QLabel(): text='无过境数据：先确认已设观测站，再点「刷新过境」（需要联网更新 TLE）。' visible=0 -> text='未配置观测站位置：请在纬度/经度输入框填写地面站坐标' visible=0 | 可用 |
| 开始分析 | 按钮 | newSpacetimePanel | 是 | 否 | 是 | 界面变化：QPushButton(): text='开始分析' checked=0 enabled=1 visible=0 -> text='停止分析' checked=1 enabled=1 visible=0 | 可用 |
| 更改地点… | 按钮 | newSpacetimePanel | 是 | 否 | 是 | 状态栏：地点面板已打开：可搜索城市 / 地图点选 / 手填经纬度；新窗口：选择观测地点 | 可用 |
| 停止跟踪 | 按钮 | satTrackPanel | 否 | 否 | 是 | — | 诚实禁用 |
| 开始跟踪 | 按钮 | satTrackPanel | 是 | 否 | 是 | 未实时触发：按秒轮询硬件指向（sat_track_panel.start_tracking）；真跑会起定时器并写后端 | 未实时触发（代码核对） |
| 更新 TLE | 按钮 | satTrackPanel | 是 | 否 | 是 | 未实时触发：联网拉 Celestrak TLE（sat_track_panel） | 未实时触发（代码核对） |
| 拉取挂载点列表 | 按钮 | 对话框:NTRIP 设置 | 是 | 是 | 是 | 未实时触发：发起 NTRIP 网络请求（ntrip_panel） | 未实时触发（代码核对） |
| 断开 | 按钮 | 对话框:NTRIP 设置 | 否 | 是 | 是 | — | 诚实禁用 |
| 连接 | 按钮 | 对话框:NTRIP 设置 | 是 | 是 | 是 | 弹窗：警告「无法连接」请填写 Caster Host 与 Mountpoint。 | 可用 |
| 关闭 | 按钮 | 对话框:帮助与能力目录 | 是 | 是 | 是 | 界面变化：QPushButton(): text='查看快捷键一览' checked=0 enabled=1 visible=1 -> <已销毁（关闭/删除）>；QLineEdit(): text='' style='' -> <已销毁（关闭/删除）>；QPushButton(): text='恢复默认' checked=0 enabled=0 visible=1 -> <已销毁（关闭/删除）>；QToolButton(ScrollLeftButton): text='' checked=0 enabled=0 visible=0 -> <已销毁（关闭/删除）>；QLabel(): text='没有匹配的能力。换个关键词（例如「FT8」「卫星」），或把分类切回「全部」再试。' visible=0 -> <已销毁（关闭/删除）> | 可用 |
| 打开能力目录 | 按钮 | 对话框:帮助与能力目录 | 是 | 是 | 是 | 界面变化：QPushButton(): text='查看快捷键一览' checked=0 enabled=1 visible=1 -> text='查看快捷键一览' checked=0 enabled=1 visible=0；QTabWidget(): idx=0 -> idx=1；QLabel(): text='显示 53 / 共 53 项 · 可用 47 · 有条件 2 · 暂不可用 4' visible=0 -> text='显示 53 / 共 53 项 · 可用 47 · 有条件 2 · 暂不可用 4' visible=1；QLabel(): text='本目录由注册表实时生成：面板 / 解调模式 / 数字解码能力表 / 长尾解码器 / AI 工具域 / 快捷键。新增能力会自动出现；标「暂不可用」的条目按原因处理，不会点了没反应。' visible=0 -> text='本目录由注册表实时生成：面板 / 解调模式 / 数字解码能力表 / 长尾解码器 / AI 工具域 / 快捷键。新增能力会自动出现；标「暂不可用」的条目按原因处理，不会点了没反应。' visible=1；QStackedWidget(qt_tabwidget_stackedwidget): idx=0 -> idx=1 | 可用 |
| 查看快捷键一览 | 按钮 | 对话框:帮助与能力目录 | 是 | 是 | 是 | 前置：切堆栈 QStackedWidget -> 0；切页签 QTabWidget -> 0；对话框：快捷键一览；新窗口：快捷键一览 | 可用 |
| 全部恢复默认 | 按钮 | 对话框:快捷键一览 | 否 | 是 | 是 | — | 诚实禁用 |
| 关闭 | 按钮 | 对话框:快捷键一览 | 是 | 是 | 是 | 界面变化：QPushButton(): text='改键…' checked=0 enabled=0 visible=1 -> <已销毁（关闭/删除）>；QLabel(): text='键位来自 desktop/shortcuts.py（唯一事实源）；标「无快捷键」的项只能从菜单/按钮触发，原因写在说明列。' visible=1 -> <已销毁（关闭/删除）>；QLineEdit(): text='' style='' -> <已销毁（关闭/删除）>；QPushButton(): text='关闭' checked=0 enabled=1 visible=1 -> <已销毁（关闭/删除）>；QPushButton(): text='恢复默认' checked=0 enabled=0 visible=1 -> <已销毁（关闭/删除）> | 可用 |
| 恢复默认 | 按钮 | 对话框:快捷键一览 | 否 | 是 | 是 | — | 诚实禁用 |
| 改键… | 按钮 | 对话框:快捷键一览 | 否 | 是 | 是 | — | 诚实禁用 |
| +1 天 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 界面变化：QLabel(tmHint): text='模拟中 · 比现在 −1 天' visible=1 -> text='当前就是现在（实时）' visible=1；QLineEdit(qt_spinbox_lineedit): text='2026-11-10  18:30:26' style='' -> text='2026-11-11  18:30:26' style=''；QDateTimeEdit(): value='2026-11-10  18:30:26' -> value='2026-11-11  18:30:26' | 可用 |
| +1 小时 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 界面变化：QLabel(tmHint): text='模拟中 · 比现在 −1 天 1 小时' visible=1 -> text='模拟中 · 比现在 −1 天' visible=1；QLineEdit(qt_spinbox_lineedit): text='2026-11-10  17:30:26' style='' -> text='2026-11-10  18:30:26' style=''；QDateTimeEdit(): value='2026-11-10  17:30:26' -> value='2026-11-10  18:30:26' | 可用 |
| −1 天 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 前置：打开对话框 时间机器；界面变化：QLabel(tmHint): text='当前就是现在（实时）' visible=1 -> text='模拟中 · 比现在 −1 天' visible=1；QLineEdit(qt_spinbox_lineedit): text='2026-11-11  18:30:26' style='' -> text='2026-11-10  18:30:26' style=''；QDateTimeEdit(): value='2026-11-11  18:30:26' -> value='2026-11-10  18:30:26' | 可用 |
| −1 小时 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 界面变化：QLabel(tmHint): text='模拟中 · 比现在 −1 天' visible=1 -> text='模拟中 · 比现在 −1 天 1 小时' visible=1；QLineEdit(qt_spinbox_lineedit): text='2026-11-10  18:30:26' style='' -> text='2026-11-10  17:30:26' style=''；QDateTimeEdit(): value='2026-11-10  18:30:26' -> value='2026-11-10  17:30:26' | 可用 |
| 今夜 | 按钮 | 对话框:时间机器 | 否 | 是 | 是 | — | 诚实禁用 |
| 回到现在 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 界面变化：QLabel(tmHint): text='当前就是现在（实时）' visible=1 -> text='已在现在 · 实时' visible=1 | 可用 |
| 跳过去 | 按钮 | 对话框:时间机器 | 是 | 是 | 是 | 状态栏：时刻已回到此刻 11-11 18:30（实时）；界面变化：QLabel(tmHint): text='已在现在 · 实时' visible=1 -> text='当前就是现在（实时）' visible=0；QPushButton(): text='−1 天' checked=0 enabled=1 visible=1 -> text='−1 天' checked=0 enabled=1 visible=0；QPushButton(): text='+1 小时' checked=0 enabled=1 visible=1 -> text='+1 小时' checked=0 enabled=1 visible=0；QDialog(时间机器): visible=1 -> visible=0；QPushButton(): text='回到现在' checked=0 enabled=1 visible=1 -> text='回到现在' checked=0 enabled=1 visible=0 | 可用 |
| Cancel | 按钮 | 对话框:连接 GNSS | 是 | 是 | 是 | 前置：打开对话框 连接 GNSS；界面变化：QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0；QLabel(): text='波特率:' visible=1 -> text='波特率:' visible=0；QDialog(连接 GNSS): visible=1 -> visible=0；QLabel(): text='串口号:' visible=1 -> text='串口号:' visible=0 | 可用 |
| OK | 按钮 | 对话框:连接 GNSS | 是 | 是 | 是 | 前置：打开对话框 连接 GNSS；界面变化：QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QPushButton(): text='OK' checked=0 enabled=1 visible=1 -> text='OK' checked=0 enabled=1 visible=0；QLabel(): text='波特率:' visible=1 -> text='波特率:' visible=0；QDialog(连接 GNSS): visible=1 -> visible=0；QLabel(): text='串口号:' visible=1 -> text='串口号:' visible=0 | 可用 |
| Cancel | 按钮 | 对话框:选择 SDR 设备 | 是 | 是 | 是 | 界面变化：QLabel(): text='检测到的 SDR 设备：' visible=1 -> text='检测到的 SDR 设备：' visible=0；QLabel(): text='增益' visible=1 -> text='增益' visible=0；QPushButton(): text='Cancel' checked=0 enabled=1 visible=1 -> text='Cancel' checked=0 enabled=1 visible=0；QDialog(选择 SDR 设备): visible=1 -> visible=0；QCheckBox(): text='Offset Tuning（直流抵消）' checked=0 enabled=0 visible=1 -> text='Offset Tuning（直流抵消）' checked=0 enabled=0 visible=0 | 可用 |
| OK | 按钮 | 对话框:选择 SDR 设备 | 否 | 是 | 是 | — | 诚实禁用 |
| Offset Tuning（直流抵消） | 按钮 | 对话框:选择 SDR 设备 | 否 | 否 | **否** | 无 clicked/toggled 槽；布尔值在 `_connect_dialog` 里经 `offset_chk.isChecked()` 读取后下发后端（main_window.py 设备参数区） | 输入控件（无独立槽，值被下游读取） |
| 刷新设备列表 | 按钮 | 对话框:选择 SDR 设备 | 是 | 是 | 是 | 前置：打开对话框 选择 SDR 设备；— | 当前形态不可见（浮层/折叠页/它形态），用户点不到，不计假活 |
| 启用自动增益 (AGC) | 按钮 | 对话框:选择 SDR 设备 | 否 | 否 | **否** | 无 clicked/toggled 槽；布尔值在 `_connect_dialog` 里经 `agc_chk.isChecked()` 读取后下发后端（main_window.py 设备参数区） | 输入控件（无独立槽，值被下游读取） |

## 未覆盖项（如实列出，不计入通过）

- 右键菜单（频谱「删除此 VFO」）在交互时才构造；已源码核对：菜单项选中后调用 `_delete_vfo`，属可用。
- 工具栏 `QWidgetAction` 是控件容器，本身不可点，不计入动作数。
- Qt 自带的页签滚动/工具栏折叠按钮不是业务动作，已剔除。
- `SKIP_LIVE` 里的动作只做代码核对，未实时触发（起线程/联网/写用户数据）。

# 交互覆盖审计（死按钮清单）

> 本文件由 `scripts/ui_interaction_audit.py` 运行时遍历生成，请勿手改。
> 运行环境：offscreen + `MBDSDR_NULL_AUDIO=1` + `MBDSDR_NO_HW=1` + `MBDSDR_OFFLINE=1`（无硬件/无网络/无样本）。
> 判定：有槽 = 主信号至少一个接收者；可观测 = 状态栏/弹窗/对话框/文件框/界面状态变化；置灰必须给出原因。

- 遍历动作：**127** 个（可点 94 / 置灰 33）
- 死按钮（有槽但点了没反应 / 没接槽）：**0**

| 动作 | 位置 | 有槽 | 可观测结果 | 不可用理由 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 主工具栏 | MainWindow / mainToolBar | 是 | 界面状态变化 | — | 可用 |
| 工具(&T) / FM 扫频找台 | 菜单栏 | 是 | — | 未连接 SDR（FM 扫频需要可调谐的本地接收机） | 诚实禁用 |
| 工具(&T) / NTRIP 设置 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 工具(&T) / 停止卫星跟踪 | 菜单栏 | 是 | 状态栏：卫星跟踪已停止 | — | 可用 |
| 工具(&T) / 关于 MBDSDR | 菜单栏 | 是 | 弹窗：关于「关于 MBDSDR」<h3>MBDSDR - AI 定义无线电</h3><p>开源 MIT 许可的软件定义无线电平台（独立实现）</p | — | 可用 |
| 工具(&T) / 开始/停止录音 | 菜单栏 | 是 | 弹窗：警告「无法录音」未连接 SDR，无法录制 baseband。
请先连接真实 SDR 设备。 | — | 可用 |
| 工具(&T) / 开始跟踪选中卫星 | 菜单栏 | 是 | 状态栏：需要观测者位置：连接 GNSS 或在 gui_config.json 配置 observer_lat/lon | — | 可用 |
| 工具(&T) / 断开 GNSS | 菜单栏 | 是 | 状态栏：GNSS 已断开 | — | 可用 |
| 工具(&T) / 更新卫星 TLE (Celestrak) | 菜单栏 | 是 | 状态栏：TLE 在线更新失败，使用内置 TLE | — | 可用 |
| 工具(&T) / 连接 GNSS... | 菜单栏 | 是 | 对话框：连接 GNSS | — | 可用 |
| 文件(&F) / 断开连接 | 菜单栏 | 是 | 状态栏：当前未连接 SDR，没有可断开的设备 | — | 可用 |
| 文件(&F) / 连接设备... | 菜单栏 | 是 | 对话框：选择 SDR 设备 | — | 可用 |
| 文件(&F) / 退出 | 菜单栏 | 是 | 状态栏：当前未连接 SDR，没有可断开的设备 | — | 可用 |
| 视图(&V) / 主题 / CarWith 深色 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 视图(&V) / 主题 / Figma 深色 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 视图(&V) / 主题 / 深色 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 视图(&V) / 主题 / 高对比 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 视图(&V) / 主题 / 默认 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 视图(&V) / 全屏 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 视图(&V) / 星象仪模式 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 视图(&V) / 显示瀑布图 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 视图(&V) / 触屏模式 / 强制触屏 | 菜单栏 | 是 | 状态栏：触屏模式：强制触屏 | — | 可用 |
| 视图(&V) / 触屏模式 / 强制鼠标 | 菜单栏 | 是 | 状态栏：触屏模式：强制鼠标 | — | 可用 |
| 视图(&V) / 触屏模式 / 自动检测 | 菜单栏 | 是 | 状态栏：触屏模式：自动检测 | — | 可用 |
| 面板(&P) / ADS-B 航路 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 面板(&P) / AI 助手 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 面板(&P) / 卫星跟踪 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 面板(&P) / 天空模式 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 面板(&P) / 恢复出厂布局 | 菜单栏 | 是 | 弹窗：询问「恢复出厂布局」清除所有自定义布局并恢复到默认「专注」布局？ | — | 可用 |
| 面板(&P) / 控制 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 面板(&P) / 新时空 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 面板(&P) / 模块/信号流 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 面板(&P) / 气象云图 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 面板(&P) / 状态 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 面板(&P) / 频谱 | 菜单栏 | 是 | 界面状态变化 | — | 可用 |
| 拉取挂载点列表 | MainWindow / NtripConfigDialog | 是 | 未实时触发：发起 NTRIP 网络请求，结果写面板状态标签 | — | 可用（代码核对） |
| 断开 | MainWindow / NtripConfigDialog | 是 | — | NTRIP 配置对话框内尚未建立连接 | 诚实禁用 |
| 连接 | MainWindow / NtripConfigDialog | 是 | 弹窗：警告「无法连接」请填写 Caster Host 与 Mountpoint。 | — | 可用 |
| 刷新设备列表 | MainWindow / QDialog | 是 | 界面状态变化 | — | 可用 |
| Cancel | MainWindow / QDialog / QDialogButtonBox | 是 | 所在对话框仅在连接/配置时弹出；Cancel 走 QDialog.reject，关闭对话框且不改任何配置 | — | 可用（上下文限定） |
| Cancel | MainWindow / QDialog / QDialogButtonBox | 是 | 界面状态变化 | — | 可用 |
| OK | MainWindow / QDialog / QDialogButtonBox | 是 | 界面状态变化 | — | 可用 |
| OK | MainWindow / QDialog / QDialogButtonBox | 是 | 界面状态变化 | — | 可用 |
| Offset Tuning（直流抵消） | MainWindow / QDialog / QGroupBox | 是 | 输入控件：勾选值在对话框确认时读取 | — | 可用（代码核对） |
| 启用自动增益 (AGC) | MainWindow / QDialog / QGroupBox | 是 | 输入控件：勾选值在对话框确认时读取 | — | 可用（代码核对） |
| ⌂ | MainWindow / QStackedWidget / bottomDock | 是 | 界面状态变化 | — | 可用 |
| IDE | MainWindow / QStackedWidget / planetariumPage / planetariumStrip | 是 | 界面状态变化 | — | 可用 |
| 多普勒 -- | MainWindow / QStackedWidget / topBar | 是 | 状态栏：未绑定卫星目标 | — | 可用 |
| + VFO | MainWindow / mainToolBar | 是 | 状态栏：新建 VFO vfo001 @ 98.500 MHz | — | 可用 |
| ☰ | MainWindow / mainToolBar | 是 | 界面状态变化 | — | 可用 |
| 保存 | MainWindow / mainToolBar | 是 | 文件选择：输入 | — | 可用 |
| 切换主听 | MainWindow / mainToolBar | 是 | 状态栏：主听切换 → vfo001（次听-未解调） | — | 可用 |
| 删除 | MainWindow / mainToolBar | 是 | — | 控件提示：删除当前选中的自定义布局（内置布局不可删） | 诚实禁用 |
| 回放 | MainWindow / mainToolBar | 是 | 状态栏：未选择录音文件；文件选择：打开 | — | 可用 |
| 录音 | MainWindow / mainToolBar | 是 | — | 未连接 SDR，没有 IQ 信号可录（连接成功后自动启用） | 诚实禁用 |
| 断开 | MainWindow / mainToolBar | 是 | — | 未连接 SDR，没有可断开的设备（连接成功后自动启用） | 诚实禁用 |
| 连接 | MainWindow / mainToolBar | 是 | 对话框：选择 SDR 设备 | — | 可用 |
| 静音 | MainWindow / mainToolBar | 是 | 界面状态变化 | — | 可用 |
| Kuwahara 降噪 | QScrollArea / WeatherPanel / card / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 中值滤波 | QScrollArea / WeatherPanel / card / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 白平衡 | QScrollArea / WeatherPanel / card / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 直方图均衡 | QScrollArea / WeatherPanel / card / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 思考中... | QSplitter / QTabWidget / QScrollArea / AIPanel | 是 | — | AI 推理进行中，按钮文案即状态；完成后恢复为「发送」 | 诚实禁用 |
| 显示执行步骤 | QSplitter / QTabWidget / QScrollArea / AIPanel | 是 | 界面状态变化 | — | 可用 |
| 配置 | QSplitter / QTabWidget / QScrollArea / AIPanel | 是 | 对话框：AI 配置 | — | 可用 |
| 删除 | QSplitter / QTabWidget / QScrollArea / BookmarkPanel | 是 | — | 控件提示：先在列表里选中一条书签 | 诚实禁用 |
| 导入 CSV | QSplitter / QTabWidget / QScrollArea / BookmarkPanel | 是 | 文件选择：打开 | — | 可用 |
| 导出 CSV | QSplitter / QTabWidget / QScrollArea / BookmarkPanel | 是 | 文件选择：保存 | — | 可用 |
| 找最近 | QSplitter / QTabWidget / QScrollArea / BookmarkPanel | 是 | 弹窗：提示「找最近」书签列表为空。 | — | 可用 |
| 添加当前频率 | QSplitter / QTabWidget / QScrollArea / BookmarkPanel | 是 | 文件选择：输入 | — | 可用 |
| 编辑 | QSplitter / QTabWidget / QScrollArea / BookmarkPanel | 是 | — | 控件提示：先在列表里选中一条书签 | 诚实禁用 |
| 清空 | QSplitter / QTabWidget / QScrollArea / ConstellationPanel | 是 | 状态栏：星座图已清空，统计已重置 | — | 可用 |
| 网格 | QSplitter / QTabWidget / QScrollArea / ConstellationPanel | 是 | 界面状态变化 | — | 可用 |
| 取消 | QSplitter / QTabWidget / QScrollArea / ScannerPanel | 是 | — | 当前没有正在运行的扫描任务 | 诚实禁用 |
| 开始扫描 | QSplitter / QTabWidget / QScrollArea / ScannerPanel | 是 | 未实时触发：启动扫频线程；无后端时由面板写状态文案 | — | 可用（代码核对） |
| + VFO | QSplitter / QTabWidget / QScrollArea / VfoPanel | 是 | — | 控件提示：在当前中心频率附近新建一个 VFO | 诚实禁用 |
| 循环主听 | QSplitter / QTabWidget / QScrollArea / VfoPanel | 是 | — | 控件提示：在 VFO 间循环切主听（Ctrl+Tab） | 诚实禁用 |
| 停止跟踪 | QStackedWidget / QSplitter / QTabWidget / satTrackPanel | 是 | — | 当前没有正在运行的卫星跟踪任务 | 诚实禁用 |
| 开始跟踪 | QStackedWidget / QSplitter / QTabWidget / satTrackPanel | 是 | 未实时触发：按秒轮询硬件指向；无硬件时由跟踪面板写「未连接」状态 | — | 可用（代码核对） |
| 更新 TLE | QStackedWidget / QSplitter / QTabWidget / satTrackPanel | 是 | 状态栏：TLE 在线更新失败，使用内置 TLE | — | 可用 |
| 🎯 跟踪 | QStackedWidget / planetariumPage / planetariumSkySlot / RFSkyView | 是 | — | 控件提示：闭环跟踪：每 500ms 按实时多普勒自动调谐，过境结束自动停止 | 诚实禁用 |
| 📡 调到该星 | QStackedWidget / planetariumPage / planetariumSkySlot / RFSkyView | 是 | — | 控件提示：按实时多普勒校正调谐到该卫星下行频率 | 诚实禁用 |
| 📷 摄像机 | QStackedWidget / planetariumPage / planetariumSkySlot / RFSkyView | 是 | — | 控件提示：摄像机锁定：视图始终对准该卫星，星空随时间反向流动；拖拽/双击接管摄像机自动解锁 | 诚实禁用 |
| 🛰 云台 | QStackedWidget / planetariumPage / planetariumSkySlot / RFSkyView | 是 | — | 控件提示：云台跟随：跟踪时同步驱动天线指向（无可用云台后端：连接 ai-sdr-mini 或在 AI 对话里配置 rotctld） | 诚实禁用 |
| 🎤 | QStackedWidget / planetariumPage / planetariumSkySlot / aiCommandBar | 是 | 状态栏：语音输入未配置（接麦克风后可用） | — | 可用 |
| Max保持 | QStackedWidget / planetariumPage / planetariumWaterfallSlot / SpectrumPanel | 是 | — | 控件提示：Max hold：逐 bin 保留历史最高电平包络（抓瞬时 burst/峰值轮廓） | 诚实禁用 |
| Min保持 | QStackedWidget / planetariumPage / planetariumWaterfallSlot / SpectrumPanel | 是 | — | 控件提示：Min hold：逐 bin 保留历史最低电平包络（看噪声底下沿/底噪漂移） | 诚实禁用 |
| 书签 | QStackedWidget / planetariumPage / planetariumWaterfallSlot / SpectrumPanel | 是 | — | 控件提示：在谱面顶部显示书签竖旗；点击药丸直接调到该书签频率 | 诚实禁用 |
| 峰值 | QStackedWidget / planetariumPage / planetariumWaterfallSlot / SpectrumPanel | 是 | — | 频谱无 IQ 数据时置灰（收到首帧数据后联动启用） | 诚实禁用 |
| 瀑布平滑 | QStackedWidget / planetariumPage / planetariumWaterfallSlot / SpectrumPanel | 是 | — | 频谱无 IQ 数据时置灰（收到首帧数据后联动启用） | 诚实禁用 |
| 调谐:中心 | QStackedWidget / planetariumPage / planetariumWaterfallSlot / SpectrumPanel | 是 | — | 控件提示：中心模式：点击谱面重调硬件中心频率（画面跟过去）
VFO 模式：点击只移动当前 VFO，画面不动（瀑布保留多信号轨迹） | 诚实禁用 |
| 频段 | QStackedWidget / planetariumPage / planetariumWaterfallSlot / SpectrumPanel | 是 | — | 控件提示：在谱面上显示频段彩带（短波/广播/航空/业余/卫星/海事）与名称；悬停提亮看详情、点击常驻描边；条目可在 bandplan.json 配置 | 诚实禁用 |
| 当前信号质量怎么样 | QTabWidget / QScrollArea / AIPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 扫频 87-108 MHz 找所有电台 | QTabWidget / QScrollArea / AIPanel / QGroupBox | 是 | 未实时触发：同「发送」：把问题填进对话框并进入思考态 | — | 可用（代码核对） |
| 找一个信号强的 FM 电台 | QTabWidget / QScrollArea / AIPanel / QGroupBox | 是 | 未实时触发：同「发送」：把问题填进对话框并进入思考态 | — | 可用（代码核对） |
| 调谐到 98.5 MHz 并录 30 秒 | QTabWidget / QScrollArea / AIPanel / QGroupBox | 是 | 未实时触发：同「发送」：把问题填进对话框并进入思考态 | — | 可用（代码核对） |
| 启用 ANR | QTabWidget / QScrollArea / AnrPanel / QGroupBox | 是 | — | 未连接 SDR，没有 IQ 数据可做降噪 | 诚实禁用 |
| 采样噪声底 | QTabWidget / QScrollArea / AnrPanel / QGroupBox | 是 | — | 控件提示：在无信号/静音段按此按钮，学习当前噪声谱 | 诚实禁用 |
| + | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| - | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| AGC 自动增益 | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| DC 去除 | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| IQ 平衡 | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 偏移调谐 | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 删除选中 | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 开始录音 | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 未实时触发：启动录音线程并落盘到 ~/mbdsdr_recordings，失败写状态栏 | — | 可用（代码核对） |
| 收藏当前频率 | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 文件选择：输入 | — | 可用 |
| 自动 | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 调谐 | QTabWidget / QScrollArea / ControlPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 刷新 | QTabWidget / QScrollArea / DevicePanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 断开 | QTabWidget / QScrollArea / DevicePanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 连接 | QTabWidget / QScrollArea / DevicePanel / QGroupBox | 是 | 状态栏：已连接 RTL-SDR #0 | — | 可用 |
| 硬件 AGC | QTabWidget / QScrollArea / GainPanel / QGroupBox | 是 | — | 未连接 SDR，无法下发硬件增益策略 | 诚实禁用 |
| 应用该解调链 | QTabWidget / QScrollArea / ModulationPanel / QGroupBox | 是 | — | 未连接 SDR，解调链无处下发 | 诚实禁用 |
| 识别信号 | QTabWidget / QScrollArea / ModulationPanel / QGroupBox | 是 | — | 未连接 SDR，没有实时信号可识别 | 诚实禁用 |
| 刷新 | QTabWidget / QScrollArea / ModulePanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 连接 | QTabWidget / QScrollArea / ModulePanel / QGroupBox | 是 | 状态栏：已连接 RTL-SDR #0；弹窗：提示「已选择设备」RTL-SDR #0 (FC0012)
（真实连接由后端完成） | — | 可用 |
| 保存当前图像为 PNG | QTabWidget / QScrollArea / SatelliteImagePanel / QSplitter | 是 | — | 没有已解码的图像可保存 | 诚实禁用 |
| 停止解码 | QTabWidget / QScrollArea / SatelliteImagePanel / QSplitter | 是 | — | 当前没有解码会话在跑 | 诚实禁用 |
| 开始解码 | QTabWidget / QScrollArea / SatelliteImagePanel / QSplitter | 是 | — | 未选择录制文件，没有可解码的样本 | 诚实禁用 |
| 启用 Web 服务 | QTabWidget / QScrollArea / ServiceSettingsPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 启用远程控制 | QTabWidget / QScrollArea / ServiceSettingsPanel / QGroupBox | 是 | 界面状态变化 | — | 可用 |
| 停止 | QTabWidget / QScrollArea / WeatherPanel / card | 是 | — | 控件提示：停止接收/解码 | 诚实禁用 |
| 开始解码 | QTabWidget / QScrollArea / WeatherPanel / card | 是 | — | 未选择录制文件，没有可解码的样本 | 诚实禁用 |
| 选择录制文件... | QTabWidget / QScrollArea / WeatherPanel / card | 是 | 文件选择：打开 | — | 可用 |
| 开始分析 | QTabWidget / QScrollArea / newSpacetimePanel / amrGroup | 是 | 界面状态变化 | — | 可用 |
| 刷新过境 | QTabWidget / QScrollArea / newSpacetimePanel / timelineGroup | 是 | 界面状态变化 | — | 可用 |

## 未覆盖项

- 右键菜单（频谱「删除此 VFO」）在交互时才构造，无法静态遍历；已源码核对：菜单项选中后调用 `_delete_vfo`，属可用。
- 工具栏 `QWidgetAction` 是控件容器，本身不可点，不计入动作数。
- Qt 自带的页签滚动/工具栏折叠按钮不是业务动作，已剔除。

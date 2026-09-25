# MBDSDR 产品体验差距评审报告

> 评审视角：产品经理 / 真实 SDR 用户
> 对标项目：SDR++、GQRX、CubicSDR、SDRangel、SatDump、gpredict（均来自 `repos/` 实读源码）
> 评审范围：`desktop/` 全部 UI 源码 + `mbdsdr_ai/` 内核能力
> 日期：2026-09-26

---

## 一句话结论

**MBDSDR 现在还不像一个"能开箱即用的 SDR 软件"。** DSP 内核已经追平 GQRX/SDR++（`analog_demod.py` + `gqrx_receiver.py` 几乎逐行移植了 nbrx/wfmrx/agc_impl），但 UI 层和主接收循环还停留在"FM 广播棒"水平——用户插好设备点连接后，大概率**听不到任何声音**（增益 0dB、静噪 -80dBFS、带宽 12kHz、频率不下发），而且差异化功能（AI/卫星/气象云图）在代码层面存在**断头路**（IQ 回调从未接线、调谐指令不落硬件）。修完 P0 的 15 项，产品形态才能从"技术演示"跳到"可用的 SDR 接收机"。

---

## 二、P0 差距清单（开箱即用阻塞）

### 2.1 首次启动：连上硬件 ≠ 听到声音

| # | 现状（文件:行号） | 对标标杆怎么做 | 建议改法 |
|---|---|---|---|
| P0-1 | 裸启动不自动连设备，全控件置灰，首屏只有一个"连接"按钮，无任何引导（`main.py:73-79`、`main_window.py:142`） | GQRX 首次启动自动弹 I/O 配置对话框（`gqrx/src/applications/gqrx/mainwindow.cpp:370-383`）；CubicSDR 启动即弹设备选择（`CubicSDR/src/AppFrame.cpp:320-321`） | 首次启动（`~/.mbdsdr/gui_config.json` 不存在）自动弹出设备选择对话框；无设备时显式提示"未检测到 RTL-SDR/HackRF，请插 USB" |
| P0-2 | 连接成功后只 `set_sample_rate()`，**从不调用 `set_frequency()`**；硬件停在 librtlsdr 残留频（常 ~100MHz），UI 却显示 98.5MHz（`main_window.py:597-632`、`sdr_backend.py:69,872`、`control_panel.py:93`） | SDR++ 默认 100MHz 并真正下发（`sdrpp/core/src/core.cpp:129`）；GQRX 启动即 `setFrequency(144.5M)`（`gqrx/mainwindow.cpp:109`） | 连接成功后主动 `backend.set_frequency(控制面板当前显示频点)`，并同步频谱中心频 + 控制面板显示 |
| P0-3 | 增益滑块初值 **0dB**，连接后无 AGC、无增益提升（`control_panel.py:251`） | GQRX 明确注释"rtlsdr gain 0 会让用户觉得设备聋了"并初始化到中点（`gqrx/mainwindow.cpp:571-578`）；SDR++ 新设备默认拉到最高增益档（`sdrpp/source_modules/rtl_sdr_source/src/main.cpp:252`） | 连接成功后把 LNA 增益默认设到中档（24~29dB）或开启 Tuner AGC；滑块同步到该值 |
| P0-4 | 静噪默认 **-80dBFS**，全带宽 IQ 功率低于此值直接静音（`main_window.py:127,1119`、`control_panel.py:266`） | SDR++ 静噪默认 OFF（`sdrpp/decoder_modules/radio/src/radio_module.h:445-446`）；GQRX 无强制静噪门 | 默认静噪设为 -120dBFS（即不门控）或"关闭静噪"；让用户先听到本底噪声再自行收紧 |
| P0-5 | 模式下拉只有 `["FM","AM"]`，默认"FM"映射到 VFO 带宽 **12kHz**（`control_panel.py:146`、`main_window.py:877-878`）；98.5MHz 落在 FM 广播段但 12kHz 解不出广播 | SDR++ 默认解调 = WFM（广播 180kHz 带宽）（`sdrpp/decoder_modules/radio/src/radio_module.h:882`）；GQRX WFM 带宽 ±100kHz（`gqrx/src/qtgui/dockrxopt.cpp:61-63`） | 模式下拉加"WFM 广播"并设为默认；默认 VFO 带宽 180kHz；连接后自动切到 WFM 模式 |

### 2.2 解调核心：内核已就绪，UI 和主循环没接线

| # | 现状（文件:行号） | 对标标杆怎么做 | 建议改法 |
|---|---|---|---|
| P0-6 | `_demod_and_play()` 只有 FM 正交鉴频一条路径（`main_window.py:1103-1152`）；切到 AM 模式后后端只改了个字符串，声卡仍在跑 FM 鉴频——**AM 实际不工作** | SDR++ 8 种解调模式各有独立类（`sdrpp/decoder_modules/radio/src/demodulators/`）；GQRX 12 种模式（`gqrx/src/qtgui/dockrxopt.cpp:35-46`） | `_demod_and_play` 改用已写好的 `NarrowbandReceiver`/`GQRXReceiver`（`mbdsdr_ai/analog_demod.py`、`mbdsdr_ai/gqrx_receiver.py`），按模式分发到包络检波/正交鉴频/BFO 混频 |
| P0-7 | "FM"不区分 WFM（广播，150k 带宽/去加重）与 NFM（对讲机，12.5k/5k 频偏），88-108MHz 广播和 144MHz 对讲机复用同一个"FM" | SDR++ `wfm.h:270` defaultBW=150k、`nfm.h:58` defaultBW=12.5k；GQRX WFM 走 `wfmrx`（无 AGC、75k 频偏），NFM 走 `nbrx`（5k 频偏） | 拆成 WFM/NFM 两个选项；WFM 路径接 15kHz 音频低通 + 50/75μs 去加重（`analog_demod.py:59-66` 已有常量），NFM 接 5k maxdev |
| P0-8 | 无 AGC 开关/模式；内核 `GqrxAGC` 已完整移植（`mbdsdr_ai/gqrx_receiver.py:74-253`，支持 on/hang/threshold/manual_gain/slope/decay_ms）但 `_demod_and_play` 完全没用 | GQRX AGC 面板：on/hang/threshold(-100dB)/manual gain(0-100dB)/slope/decay(500ms)（`gqrx/src/qtgui/dockrxopt.cpp:433-463`）；SDR++ attack 1-200/decay 1-20 滑条（`sdrpp/decoder_modules/radio/src/demodulators/am.h:51-99`） | 控制面板加 AGC 复选 + decay 滑条（Long/Medium/Fast = 1000/500/200ms）；默认 on+hang；WFM 模式自动旁路（对齐 GQRX `wfmrx.cpp`） |
| P0-9 | 无带宽/信道滤波器 UI；VFO 带宽硬编码 WFM=180k/其余=12k（`main_window.py:876-883`） | SDR++ 每模式 `getDefaultBandwidth/getMinBandwidth/getMaxBandwidth`（`wfm.h:270-278`、`nfm.h:58-66`）；GQRX 每模式 WIDE/NORMAL/NARROW 三档预设（`dockrxopt.cpp:50-63`） | 加带宽滑条，范围按模式自动收窄（WFM 50-250k、NFM 1-50k、AM 1-15k、USB/LSB 0.5-12k、CW 50-500）；切模式时 `set_bandwidth(default)` |
| P0-10 | 静噪检测用**全带宽 IQ 功率**（`main_window.py:1117-1120`），不是信道滤波后功率；-80dBFS 门限是对全采样率 2.4Msps 而言，切窄带台时口径错配 | GQRX 拓扑 filter→squelch→AGC→demod（`gqrx/src/receivers/nbrx.cpp:76-79`）；MBDSDR 内核 `analog_demod.py:369-373` 已实现 3dB 滞回、`:331-341` 已实现 auto_squelch | 静噪检测移到 VFO/信道低通之后；加"自动静噪"按钮调 `auto_squelch()`；门限改 dBFS 相对信道功率 |

### 2.3 调谐：频率输入被广播带钳死，无用户书签

| # | 现状（文件:行号） | 对标标杆怎么做 | 建议改法 |
|---|---|---|---|
| P0-11 | 频率输入框 `_on_freq_input` 把输入限死在 FM 64-108MHz / AM 531-1710kHz（`control_panel.py:356-371`）；手输 137.1MHz 气象/144MHz 业余会被**静默丢弃**（超范围无 else 分支） | SDR++ 顶栏 freqSelect 任意 Hz 直输（`sdrpp/core/src/gui/main_window.cpp:378`）；GQRX `CFreqCtrl` 任意频率（`gqrx/src/qtgui/freqctrl.h:37`） | 去掉频段钳制，改为按当前模式/硬件范围自由输入（Hz 级）；非法频率给红字提示而非静默丢弃 |
| P0-12 | 步进只有 ±0.1 / ±1.0 MHz 两档按钮（`control_panel.py:166`）；对航空 25kHz 步进、CW 10Hz 步进完全不可用 | SDR++/GQRX/SDR# 全部是"逐位权重步进"数字拨盘——点哪一位按哪一位走（Hz 位=1Hz、kHz 位=1kHz），无固定档位（`sdrpp/core/src/gui/widgets/frequency_select.cpp:119-150`、`gqrx/src/qtgui/freqctrl.h:77-106`） | 至少加 ±0.01MHz(10kHz) / ±0.001MHz(1kHz) 档；长期应改成 GQRX `CFreqCtrl` 式数字拨盘，或在输入框旁加步进档位选择器（1Hz/10Hz/1kHz/10kHz/100kHz/1MHz） |
| P0-13 | 无用户书签系统；`preset_combo` 是只读硬编码列表（`control_panel.py:25-63`）；`PRESET_FM_STATIONS=[]` 故意留空（`control_panel.py:18-22`） | SDR++ `frequency_manager` 模块：用户自建命名列表，每条=频率+带宽+模式，一键"把当前 VFO 存为书签"，双击调谐，JSON 导入导出，书签作为黄色标签叠在瀑布图上可点（`sdrpp/misc_modules/frequency_manager/src/main.cpp:25-36,107-124,424-524`）；初始列表为空（`:829-832`）。GQRX 底部 Bookmarks Dock + 工具栏 Add Bookmark（`gqrx/src/applications/gqrx/mainwindow.cpp:202,mainwindow.ui:446`） | 加"★ 存当前频率"按钮（存 freq+mode+name 到 `~/.mbdsdr/bookmarks.json`），书签下拉可双击调谐、可删；这是替代"最近频率"的正解 |

### 2.4 信息架构：页签互斥思维，中央频谱被频繁替换

| # | 现状（文件:行号） | 对标标杆怎么做 | 建议改法 |
|---|---|---|---|
| P0-14 | "模块/信号流"独占左 Tab（`main_window.py:379`），且与"控制"Tab **重复造了一套** 设备下拉/连接/模式/增益/采样率（`module_panel.py:202-269` vs `control_panel.py`），两处状态不同步；用户改源设置必须离开频谱 | SDR++ Source 折叠组常驻左栏（`sdrpp/core/src/gui/main_window.cpp:478-496`）；GQRX DockInputCtl 可停靠并排（`gqrx/mainwindow.cpp:190-198`） | 改为常驻左栏可折叠 Dock，删掉与控制面板重复的一套控件；连接按钮统一 |
| P0-15 | 菜单"FM 扫频找台"的实现是**强行把右 Tab 切到 AI 页再往输入框塞文字**（`main_window.py:1334-1341`）——一个内置功能要靠"跳 Tab + 填输入框"才能工作 | — | 扫频做成工具栏/菜单直接动作，不依赖 AI 输入框；AI 改为可呼出抽屉 Dock，不与"控制"互斥 |

### 2.5 AI/卫星：差异化功能存在代码级断头路

| # | 现状（文件:行号） | 对标标杆怎么做 | 建议改法 |
|---|---|---|---|
| P0-16 | `WeatherPanel.attach_iq_source()`（`weather_panel.py:490`）定义了但 `main_window.py` 全文零调用；选"实时 SDR"→点开始→必然弹"未拿到 IQ 流回调"（`weather_panel.py:632`）——**实时收 NOAA 走不通** | SatDump 选星即绑定 pipeline，`DopplerCorrectBlock` 自动插进基带流（`SatDump/src-core/modules/demod/module_demod_base.cpp:125-176`），用户无感 | 在 `_start_iq_streams()` 中把 IQ 数据回调注册到 `WeatherPanel.attach_iq_source()`；或统一走一个 IQ 分发总线 |
| P0-17 | `DopplerPanel.feed_iq()`（`doppler_panel.py:446`）同样从未被调用，`_rt_buffer` 恒空，永远"观测不足 0/2"——**多普勒实时观测走不通** | gpredict 定时器算 range_rate→多普勒→rigctld 持续推校频，是闭环（`gpredict/src/`） | 同 P0-16，把 IQ 分发到 DopplerPanel；或明确该面板为"事后离线定轨"并在 UI 上标注 |
| P0-18 | AI/天图调谐不落硬件：`_on_ai_tool_call`（`main_window.py:918-927`）只更新屏幕标签，不调 `_active_sdr_backend.set_frequency`；点卫星调谐包在 `if self._worker:` 里（`:1572`），真实后端模式下 `worker=None`，只改文字不改频率 | — | AI 工具调用和天图卫星点击必须走统一的调谐总线，最终调用 `backend.set_frequency()` + 同步控制面板 + 频谱中心频 |

### 2.6 设置与默认值：无设置中心，默认呼号硬编码为他人呼号

| # | 现状（文件:行号） | 对标标杆怎么做 | 建议改法 |
|---|---|---|---|
| P0-19 | 无任何「设置/Preferences」对话框；关于框却写"呼号：在「设置」中配置"（`main_window.py:1375`）——**悬空引用**，那个设置根本不存在 | GQRX 有完整的 I/O 配置 + AGC/音频/解调选项对话框群（`gqrx/src/qtgui/`）；SDR++ 模块设置面板（`sdrpp/core/src/gui/main_window.cpp:473-531`） | 新建「设置」对话框，分页：设备 / 接收(增益·静噪·采样率·AGC) / 音频输出 / 录音(目录·格式) / 呼号 / 外观 / AI 接口 |
| P0-20 | ARDOP/JS8Call/Fldigi 默认呼号硬编码 **BI4MIB**（`mbdsdr_ai/ardop_adapter.py:418`、`mbdsdr_ai/js8call_adapter.py:227`、`mbdsdr_ai/fldigi_modes.py:745`）——用户不传参就用此呼号**发射** | 呼号必须用户自填，缺省用 `NOCALL`/`XX1XXX` 占位，不得替用户署真实呼号 | 从设置读 `my_callsign`，缺省 `NOCALL`，发射前校验非 NOCALL 才允许 |
| P0-21 | 天空图常驻横幅硬编码"呼号 BI4MIB"（`rf_sky_view.py:63`）——产品把作者呼号当成了所有用户的呼号画在屏幕上 | 呼号为用户配置项 | 读设置里的用户呼号，未配置时显示"未配置呼号" |
| P0-22 | 频率/模式/音量/瀑布开关**存了不读**：`_save_gui_config`（`main_window.py:1645-1649`）写了这四项，但 `_load_gui_config`（`main_window.py:1673-1708`）只读回窗口几何/observer 坐标/theme/ntrip——重启后全部丢失 | GQRX `restoreGeometry/restoreState` + 恢复上次 RF 频率（`gqrx/mainwindow.cpp:430-431,540-542`） | 在 `_load_gui_config` 补回这四项到 control_panel/spectrum |

---

## 三、P1 差距清单（常用但缺）

| # | 现状（文件:行号） | 对标标杆怎么做 | 建议改法 |
|---|---|---|---|
| P1-1 | 增益只有一个 LNA 滑条 0-49dB（`control_panel.py:244-256`），不接驱动暴露的离散档 | SDR++ RTL source 直接枚举 29 档（`sdrpp/source_modules/rtl_sdr_source/src/main.cpp:192-200`）+ tunerAgc 开关；GQRX 走 osmosdr 具名级（LNA/VGA/AMP） | 增益滑条改用 `rtlsdr_params._R82XX_GAIN_TENTHS`（29 档）吸附；加"Tuner AGC"开关对接 `RTLSDRBackend.set_agc`（`sdr_backend.py:894`） |
| P1-2 | 音频输出设备不可选，走系统默认（`audio_out.py:119` 未传 `device=`） | SDR++ audio sink 枚举 PortAudio 设备（`sdrpp/sink_modules/audio_sink/src/main.cpp:39-188`）；GQRX `set_output_device()`（`gqrx/src/applications/gqrx/receiver.cpp:262`） | 设置里加输出设备下拉，启动时 `sd.query_devices()` 枚举，传给 `sd.OutputStream(device=...)` |
| P1-3 | 录音只有 `.iq` 原始基带（`main_window.py:1242`），无音频 WAV；无格式/位宽选择 | SDR++ recorder WAV + Uint8/Int16/Int32/Float32（`sdrpp/misc_modules/recorder/src/main.cpp:57-67`）；GQRX 音频 WAV 48k 立体声 + IQ 双选项（`gqrx/receiver.cpp:1007,1201`） | 录音按钮改双模式："录 IQ"/"录音频"；音频走 `wave` 模块写 48k Int16 WAV |
| P1-4 | `SDR_BAND_PRESETS` 硬编码地区性频率：146.520"2m 全美呼叫"（美国）、144.390 APRS（因国而异）、北斗 B1（中国）、868/915 ISM（欧/美）、NOAA 具体星历（137.100 同时挂在"NOAA 19"和"METEOR M2"下，数据重复错误）（`control_panel.py:25-63`） | SDR++ band plan 是外部每国一份 JSON（`sdrpp/core/src/gui/widgets/bandplan.cpp:76-109`），用户选国家；书签初始为空（`frequency_manager/main.cpp:829-832`） | 删除地区性/航天器条目，保留全球通用段（航空 118-136、FM 87.5-108）；频段分区改外部 JSON；修掉 137.100 重名；真正常用频率由用户自建书签承载 |
| P1-5 | 单击频谱放的 marker 是纯内存、最多 8 个、断连即清（`spectrum_widget.py:794-800`），无名字、无模式、点 marker 不调谐 | SDR++ 瀑布书签标签点一下直接调谐（`frequency_manager/main.cpp:734-737`） | 单击 marker 改为"在瀑布上画书签标签、点标签=调谐"；marker 与书签数据打通（书签即持久化 marker） |
| P1-6 | 无频段扫描 GUI；只有 AI 面板关键词触发 `scan_fm`（`ai_panel.py:621`） | SDR++ scanner 模块：起止频率/步进间隔/信号门限/调谐等待/linger/方向（`sdrpp/misc_modules/scanner/src/main.cpp:43-200`） | 加扫描对话框：start/end/interval/level/linger，扫到超门限频率停留 |
| P1-7 | 射频天空/气象云图/多普勒定轨三个互斥左 Tab（`main_window.py:373/383/387`），用户跟踪卫星时必须离开频谱 | SDRangel Feature Dock 与主频谱并存（`sdrangel/sdrgui/mainwindow.cpp:2493`）；SDR++ 模块挂在常驻左侧可折叠栏 | 三者合并为可折叠"卫星"Dock 组，平时收起，不挤占频谱；跟踪卫星时频谱和天空图同屏可见 |
| P1-8 | 音量滑杆在右"控制"Tab（`control_panel.py:208`），切到别的 Tab 即够不着 | SDR++ 音量在顶栏常驻（`sdrpp/core/src/gui/main_window.cpp:373`） | 把音量滑杆移到顶栏或频谱标题栏，常驻可达 |
| P1-9 | 录音时长只在控制 Tab 显示（`control_panel.py:440`），工具栏录音按钮无计时（`main_window.py:309`） | GQRX DockAudio 录音按钮常驻可见 | 工具栏录音按钮旁加 `00:00` 计时 label，录音中常红 |
| P1-10 | 频谱滚轮缩放只改显示采样率、不回发硬件（`spectrum_widget.py:817-822`），放大后频率轴是"假"的；无 Zoom 复位 | SDR++ 右轨 Zoom 纯视图缩放、滚轮=调谐（`sdrpp/main_window.cpp:578-631`） | 滚轮改为 snap 步进调谐；缩放交给独立视图带宽滑杆；加"复位缩放"按钮 |
| P1-11 | SNR 仅在右"状态"Tab（`status_panel.py:53-56`），状态栏只有 RSSI | SDR++ 顶栏 SNR 表（`sdrpp/main_window.cpp:416`） | 状态栏或频谱标题栏常驻一个小 SNR 数字 |
| P1-12 | 无日志系统，全 `desktop/` 无 `import logging`；运行信息只靠 5-8 秒状态栏消息（`main_window.py:744,1295`） | SDR++ `utils/flog.h` 分级日志（`sdrpp/core/src/core.cpp:21`）；GQRX/CubicSDR 均有日志通道 | 引入 `logging`，写 `~/.mbdsdr/logs/`，加可折叠日志面板；用户可回溯"刚才为什么连不上" |
| P1-13 | 快捷键仅 5 个硬编码（Ctrl+C 连接、Ctrl+D 断开、Ctrl+Q 退出、Ctrl+R 录音、F11 全屏）（`main.py:94-100`、`main_window.py:183-232`）；无空格录音、无箭头步进频率、无模式切换键 | GQRX 有 Ctrl+J/R/F/A/B 切面板、F 输入频率、Z 零拍频等（`gqrx/mainwindow.cpp:159-174`）；SDR++ `display.cpp:checkKeybinds` | 补空格=录音、↑↓=步进、数字键/快捷键切模式；后续可做成可自定义 |
| P1-14 | 采样率失败/音频设备忙**静默吞错**（`main_window.py:628-630` try/except:pass、`:121-125` 静默降级）；非法频率输入无提示（`control_panel.py:370` except ValueError:pass） | GQRX 设备参数变化即提示 | 这些 except 分支改为状态栏/对话框友好提示，频率非法弹 warning |
| P1-15 | 增益/静噪/采样率**不持久化**，重启全丢 | CubicSDR `AppConfig` 按设备存 PPM/采样率/天线/AGC/全部增益（`CubicSDR/src/AppConfig.cpp:269-283`） | 加入 gui_config 并在加载时回填 control_panel |
| P1-16 | 设备下拉硬编码"ai-sdr Mini (WebSocket 192.168.4.1:81)"为第一项（`main_window.py:522`），真实 RTL-SDR 列在其后 | SDR++ 枚举真实 USB 设备并按名称排序，无硬编码项（`rtl_sdr_source/main.cpp:122-144`）；有 USB SDR 时默认选中第一个真实设备（`selectFirst`） | 真实枚举设备为空时才把 ai-sdr Mini 放在末尾；有 USB SDR 时默认选中第一个真实设备 |
| P1-17 | 无"快速开始"/首次引导；无预设电台（`PRESET_FM_STATIONS=[]`），连上后用户面对空白频谱 | GQRX 默认 144.5MHz 落在有信号的业余段；SDR++ 100MHz 在 FM 广播段内 | 首次连接后自动跑一次 FM 扫频（已有 `_start_sweep`，`main_window.py:228`），或预置 1-2 个全球性强信号频点作为落点 |

---

## 四、P2 差距清单（锦上添花）

| # | 现状（文件:行号） | 对标标杆怎么做 | 建议改法 |
|---|---|---|---|
| P2-1 | 无 PPM/频偏校正入口 | GQRX freqCorrSpinBox（`gqrx/src/qtgui/dockinputctl.ui:168`）；CubicSDR `setPPM`（`CubicSDR/src/AppConfig.h:25`） | 设备设置对话框或控制面板加 PPM spinbox |
| P2-2 | 无 Bias-T 开关 | SDR++ rtl_sdr biasT 勾选（`rtl_sdr_source/src/main.cpp:205`） | 设备设置对话框加 Bias-T 勾选（天线供电场景必需） |
| P2-3 | 无独立 DSP 播放/停止按钮，连接即流（`main_window.py:614-632`） | SDR++ 顶栏 Play/Stop 大按钮（`sdrpp/main_window.cpp:348-361`） | 连接后默认暂停，顶栏加播放键 |
| P2-4 | 频谱无 band plan 彩色叠加 | SDR++ bandplan 彩色频段带（`bandplan.cpp:76-109`）；GQRX band 自带推荐步进（`bandplan.h:40`） | 频谱顶部/底部画频段分区色带（外部 JSON），选中频段时自动给出该段推荐步进 |
| P2-5 | 音量 0-63 映射 `gain/63*2.0`（`main_window.py:842`），无刻度、无静音键 | SDR++/GQRX 输出电平 dB 表 + mute | 改 dBFS 刻度（-60..0），加 Mute 按钮 |
| P2-6 | 无 SSB BFO / CW pitch 调节 UI；内核 `_MODE_DEFAULTS` 已给 usb=+1500、lsb=-1500、cw=700（`analog_demod.py:123-125`） | GQRX `cwOffsetChanged`（`dockrxopt.cpp:105`） | 切到 USB/LSB/CW 时显示 BFO/pitch 微调滑条 ±2kHz |
| P2-7 | 无去加重 50/75μs 选择；WFM 立体声/RDS 未暴露（`analog_demod.py:59-62` 有常量） | SDR++ `DEEMP_MODE_22US/50US/75US`（`wfm.h:278`）；GQRX WFM-stereo/oirt 选项 | WFM 模式下加去加重地区选择（中/欧 50μs、美 75μs）；后续加立体声开关 |
| P2-8 | 窗口布局无 `saveState`，splitter/工具栏不记忆（`main_window.py:1638` 只手存 x/y/w/h） | GQRX `saveGeometry()+saveState()` 全量恢复（`mainwindow.cpp:430-431,540-542`） | 改用 `saveGeometry()+saveState()` 替代手写 x/y |
| P2-9 | 采样率默认 2.048MHz（`control_panel.py:236`），FM 广播带扫不全 | SDR++ 新设备默认 2.4MHz（`rtl_sdr_source/main.cpp:202`）；CubicSDR 默认 2.5MHz（`CubicSDRDefs.h:41`） | 默认改 2.4MHz 以便一屏扫过整段 FM 广播 |
| P2-10 | AI API key 只能手改 `~/.mbdsdr/config.json`（`main_window.py:902-916`），无界面 | — | 纳入设置对话框的"AI 接口"页（api_key/base_url/model） |
| P2-11 | 工具描述示例含 `BI4MIB`（`agent.py:678,686`、`sdr_tools.py:1543,1576`、`ft8_encode.py:268` 等） | — | 泛化为 `XX1XXX` 占位示例 |
| P2-12 | menubar 无 Source/Display/Band Plan 等价物，工具菜单塞着 NTRIP/关于（`main_window.py:224-246`） | SDR++ 左栏分组菜单（`main_window.cpp:73-79`）；GQRX View 菜单列出所有 Dock | 随 Dock 化改造，把菜单重组为"视图"下的 Dock 显隐列表 |
| P2-13 | 状态栏无采样率/模式/带宽显示（`main_window.py:433-456`） | 标杆状态栏均有模式+采样率 | 状态栏补 `模式/BW` 与 `采样率` |

---

## 五、"从打开到听到电台"理想旅程步骤对照

### 标杆旅程（以 SDR++ 为例，约 3 步出声）

| 步骤 | 用户操作 | 系统响应 |
|---|---|---|
| 1 | 插好 RTL-SDR，启动 SDR++ | 自动枚举设备，单设备自动选中；默认中心频 100MHz、采样率 2.4MHz、增益拉到最高档、默认 WFM 解调 |
| 2 | 左侧 Source 菜单点一下设备名 | 开始采数，频谱铺开，Audio Sink 自动出声 |
| 3 | 点频谱上的信号 / 顶栏 freqSelect 敲频率 | VFO 移动，即时听到对应电台 |

### GQRX 旅程（约 4 步出声）

| 步骤 | 用户操作 | 系统响应 |
|---|---|---|
| 1 | 启动 GQRX | 首次自动弹 I/O 配置对话框，选设备 |
| 2 | 点 OK | 自动连设备，默认 144.5MHz、增益初始化到中点（避免"聋"）、DSP 自动启动 |
| 3 | 模式下拉选 WFM/NFM | 解调切换，出声 |
| 4 | 大频率拨盘敲频率 / 点频谱 | 调谐 |

### MBDSDR 当前旅程（实际上听不到声音，约 7+ 步且处处卡点）

| 步骤 | 用户操作 | 系统响应 / 卡点 |
|---|---|---|
| 1 | 启动 MBDSDR | 灰屏，所有控件置灰，只有一个"连接"按钮，无引导 |
| 2 | 点"连接" | 弹设备对话框，第一项是硬编码的 ai-sdr Mini（需要 IP），真实设备在后面 |
| 3 | 选 RTL-SDR，点 OK | 连接成功，设采样率 2.048MHz，**但不设频率**——硬件停在残留频 ~100MHz，UI 显示 98.5MHz |
| 4 | 频谱铺开 | 增益 0dB（灵敏度极低）、静噪 -80dBFS（全静音）、VFO 带宽 12kHz（解不出 FM 广播）、模式"FM"实际不区分 WFM/NFM |
| 5 | 用户想敲 137.1MHz 收气象 | 频率输入框**静默拒绝**（限死 64-108MHz），无任何提示 |
| 6 | 用户想用 ±步进微调 | 只有 ±0.1/±1.0MHz 两档，对窄带信号太粗 |
| 7 | 用户切到 AM 模式想收航空 | **AM 实际不工作**——主循环只跑 FM 鉴频，后端只改了个字符串 |
| 结果 | — | **用户大概率听不到任何声音**，也不知道该调哪里 |

### MBDSDR 理想旅程（修完 P0 后，约 3 步出声）

| 步骤 | 用户操作 | 系统响应 |
|---|---|---|
| 1 | 插好 RTL-SDR，启动 MBDSDR | 首次自动弹设备选择对话框；有 USB 设备时默认选中第一个真实设备；无设备时显式提示 |
| 2 | 点"连接" | 自动设频率（98.5MHz 或上次保存的频率）、设 WFM 模式、VFO 带宽 180kHz、增益中档 / AGC 开启、静噪关闭（-120dBFS）、频谱铺开、声卡自动出声 |
| 3 | 双击频谱上的信号 / 数字拨盘敲任意频率 / 点书签 | VFO 移动，即时听到对应电台；步进可按位微调；书签可一键保存 |

---

## 六、专项：bi4mib 默认呼号清理清单

> 用户要求：不要默认内置 bi4mib。以下为全仓库审计结果，按处理优先级分类。

### A. 必须改：硬编码为发送默认值（用户不传参就用 BI4MIB 发射）

| 文件:行号 | 内容 | 处理建议 |
|---|---|---|
| `mbdsdr_ai/ardop_adapter.py:418` | `("connect_request", {"callsign": "BI4MIB", ...})` | 改为从设置读 `my_callsign`，缺省 `NOCALL` |
| `mbdsdr_ai/js8call_adapter.py:227` | `de=args.get("de", "BI4MIB")` | 同上 |
| `mbdsdr_ai/fldigi_modes.py:745` | `text = args.get("text", "HELLO BI4MIB")` | 缺省文本改为 `"HELLO"` 或从设置读呼号 |
| `desktop/rf_sky_view.py:63` | 天空图常驻横幅绘制"呼号 BI4MIB" | 读设置里的用户呼号，未配置显示"未配置呼号" |

### B. 应泛化：工具描述 / 报错文案里的示例

| 文件 | 位置 | 处理建议 |
|---|---|---|
| `mbdsdr_ai/agent.py` | `:678,686,695,696,703,799,807,823` | FT8 工具 description/报错示例 `'CQ BI4MIB OM74'` → 泛化为 `'CQ XX1XXX YY1ZZZ'` |
| `mbdsdr_ai/sdr_tools.py` | `:1543,1576,1610,1661,2211` | schema 描述 `"如 BI4MIB"` → `"如 XX1XXX"` |
| `mbdsdr_ai/ft8_encode.py` | `:268,272,273` | docstring/报错示例 → 泛化 |

### C. 测试数据（可保留，建议泛化）

- `mbdsdr_ai/fst4_encode.py:120`、`ft8_encode.py:288`：`__main__` 自测调用
- `mbdsdr_ai/ft8_callsign.py:60,69,81`：单元测试注释

### D. 版权署名（保留，合理）

- `aprs_parser.py:14`、`ax25.py:12`、`cw_decoder.py:15`、`fldigi_modes.py:15`、`ft8_lite.py:14`、`new_spacetime.py:13`：文件头 `作者：MBDSDR Team (BI4MIB)`——这是项目作者署名，保留。
- `desktop/README.md:3`：项目说明中的作者呼号，可保留但建议措辞改为"作者呼号：BI4MIB"以区别于"用户默认呼号"。

---

## 七、专项：主题命名（"默认"而非"日式"）

> 用户要求：主题可以叫默认但不能叫日式。

### 现状

- 用户可见的主题 display_name **已经是"默认"**（`themes.py` 中 `DEFAULT_LIGHT` 的 display_name="默认"，`DEFAULT_THEME = "default"`），✅ UI 层面无需改。
- 问题在于**内部注释和别名里残留"日式 / japanese"字样**，共 17 处：

| 位置 | 内容 | 处理建议 |
|---|---|---|
| `desktop/themes.py:371` | 注释 `# 日式低饱和浅色（默认主题）` | 改为 `# 默认低饱和浅色主题` |
| `desktop/themes.py:481` | `_THEME_ALIASES = {"japanese_light": "default", ...}` | 别名可保留（向后兼容读旧配置），但新代码不得再引用 `japanese_light` 作为规范名 |
| `desktop/README.md:37` | `（日式低饱和）` | 改为 `（默认低饱和）` |
| `desktop/assets/generate_icon.py:7`、`assets/icon.svg:4` | 注释 `设计语言：日式低饱和配色` | 改为 `默认低饱和配色` |
| `desktop/doppler_panel.py:16`、`module_panel.py:20,41`、`ntrip_panel.py:15,42`、`rf_sky_view.py:59,429`、`spectrum_widget.py:35,537,722`、`weather_panel.py:32,61` | 各面板注释 `（日式低饱和）` | 统一改为 `（默认低饱和）` |

---

## 八、内核能力 vs UI 暴露对照（"很多东西没做到位"的根因）

MBDSDR 的核心问题不是"做不出来"，而是**内核已经实现的能力没有接到 UI 上**。以下是已实现但未暴露的功能清单：

| 内核已实现 | 文件 | UI 状态 |
|---|---|---|
| 6 种解调模式（am/fm/wfm/usb/lsb/cw）默认带宽/BFO/静噪 | `mbdsdr_ai/analog_demod.py:117-126` | UI 只暴露 FM/AM 两项 |
| GQRX 完整 AGC（on/hang/threshold/manual_gain/slope/decay_ms） | `mbdsdr_ai/gqrx_receiver.py:74-253` | 无 AGC UI，主循环未调用 |
| NarrowbandReceiver 完整接收链（filter→squelch→AGC→demod） | `mbdsdr_ai/analog_demod.py:286-323` | `_demod_and_play` 绕开了它，自己写了个 FM 鉴频 |
| 自动静噪（当前电平+3dB）+ 3dB 滞回 | `mbdsdr_ai/analog_demod.py:331-341,369-373` | 未接线 |
| RTL-SDR 29 档离散增益枚举 | `mbdsdr_ai/rtlsdr_params.py:65-84` | UI 用 0-49 连续滑条，不吸附离散档 |
| 后端 `set_agc()` / `set_bandwidth()` 接口 | `mbdsdr_ai/sdr_backend.py:211-232,894-924` | UI 无对应控件 |
| WFM 去加重常量（50/75μs）+ 15kHz 音频低通 | `mbdsdr_ai/analog_demod.py:59-66` | 未暴露 |
| SSB BFO 默认值（usb=+1500、lsb=-1500、cw=700） | `mbdsdr_ai/analog_demod.py:123-125` | 无 BFO 调节 UI |
| WeatherPanel 实时 IQ 接收接口 | `desktop/weather_panel.py:490` | `main_window.py` 从未调用 |
| DopplerPanel 实时 IQ 馈送接口 | `desktop/doppler_panel.py:446` | `main_window.py` 从未调用 |
| 双击频谱调谐、拖拽平移、滚轮缩放 | `desktop/spectrum_widget.py:761-822` | ✅ 已接线（这是做得好的部分） |

**结论**：P0 中的大部分工作不是"从零开发"，而是"把已写好的内核能力接到 UI 和主循环上"。这意味着修复成本相对可控，优先级应该集中在接线和默认值上。

---

## 九、修复优先级建议（落地路线图）

### 第一阶段：让用户能听到声音（P0 中的 P0，预计 1-2 天）

1. 连接后下发默认频率（P0-2）
2. 增益默认中档 / 开启 AGC（P0-3）
3. 静噪默认关闭（P0-4）
4. 默认 WFM 模式 + 180kHz 带宽（P0-5）
5. `_demod_and_play` 改用 NarrowbandReceiver，让 AM/SSB 真正工作（P0-6）
6. 频率输入放开广播带限制（P0-11）

### 第二阶段：补齐核心接收功能（P0 其余 + P1 关键项，预计 3-5 天）

7. 模式下拉补全 WFM/NFM/AM/USB/LSB/CW（P0-7）
8. AGC UI + 接线（P0-8）
9. 带宽滑条（P0-9）
10. 信道后静噪（P0-10）
11. 用户书签系统（P0-13）
12. 模块面板从孤岛 Tab 改常驻 Dock（P0-14）
13. AI/天图调谐落硬件（P0-18）
14. 去掉默认呼号 BI4MIB（P0-20、P0-21）
15. 建设置对话框（P0-19）
16. 修复配置存了不读的 bug（P0-22）

### 第三阶段：差异化功能打通（P0 断头路 + P1，预计 3-5 天）

17. WeatherPanel / DopplerPanel IQ 回调接线（P0-16、P0-17）
18. 卫星视图合并为 Dock（P1-7）
19. 扫描器 GUI（P1-6）
20. 音频 WAV 录音（P1-3）
21. 日志系统（P1-12）

### 第四阶段： polish（P2，持续迭代）

22. PPM/Bias-T/播放停止（P2-1/2/3）
23. Band plan 彩色叠加（P2-4）
24. 窗口 saveState（P2-8）
25. 注释清理"日式"→"默认"（P2-9，见第七节）
26. 工具描述示例泛化（P2-11）

---

*本报告所有结论均基于 `desktop/` 和 `mbdsdr_ai/` 源码实读，标杆行为均标注了 `repos/` 中对应项目的文件路径，未臆造。*

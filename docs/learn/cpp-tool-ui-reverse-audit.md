# C++ 端「UI 有控件、工具面无对应」反向对拍审计

日期：2026-10-30 · 范围：`cpp/src/ui/`（UI 控件）↔ `cpp/src/ai/tool_schema.cpp` `registeredToolSpecs()`（90 个工具）
方法：脚本提取全部 6 类可调控件实例化点（含上下文）→ 逐条对照工具 schema/执行器/校验器/tokens → D1/D2/D3 分类。
纪律：不编造缺陷；核实不成立的项如实记账；无工具链，只做**静态审计**，不声称"已修复/已验证"；本轮**未改任何 C++ 源码**。
前置：正向对拍（16 个 `set_*` 工具逐一核对有 UI 入口）见 `docs/learn/settings-surface-audit.md`，本轮做反向，不重复其结论。

## 0. 摘要

| 分类 | 条数 | 含义 |
|---|---|---|
| **D1 有 UI 无工具** | **45** | 用户能在 GUI 调、AI agent 经 MCP 调不到（能力不对称） |
| **D2 有 UI 有工具但语义不一致** | **9** | 范围/单位/默认值/枚举两边对不上 |
| **D3 参数语义反人类** | **8** | 范围与硬件能力不符/单位不明/危险写无确认（不限于 UI） |
| 不计入缺口（有意不暴露/瞬态交互） | 12 | 见 §5，如实记账不算 D1 |

盘点口径：`cpp/src/ui/*.cpp` 中 `new QComboBox/QCheckBox/QDoubleSpinBox/QSpinBox/QSlider/QLineEdit`
共 **143 处实例化点**（40 combo / 35 checkbox / 21 lineedit / 19 doublespin / 16 spin / 12 slider，
与任务口径一致）；settings_dialog 的左右栏面板复选框循环（`settings_dialog.cpp:156`/`:177`）
与频谱右缘三滑条辅助函数（`spectrum_widget.cpp:623`）展开后运行时约 **169 个具象控件**。
工具面：`registeredToolSpecs()` 共 **90 个工具**（`tool_schema.cpp:63-2409`，快照时点；
销账后 135，见 §7）。

## 1. D1 有 UI 无工具（45 条，可执行清单）

> 2026-10-30 第二轮落地 19 条（A 全组/B1/C 全组/H1/I1）、第三轮落地余 26 条，
> **45/45 全部落地**，销账记录见 §7。本表保持审计快照原貌，行数口径不变。
建议工具名均为**新工具**（或显式扩参），照此清单即可逐条补工具。
钉扎测试 `tests/test_cpp_tool_ui_reverse_audit.py` 现行契约（§7 后已改为双账）：
45 个建议工具名必须在 `tool_schema.cpp` 的 `s.name` 集合与
`agent_tools.cpp` dispatchTable 双侧全在场，一个都不能少。

### A. 接收链硬件参数（RTL-SDR 源选项，8 条）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-A1 | 增益滑杆/档位/工具栏旋钮/设置镜像（gainSlider_, gainCombo_, toolbarGainSpin） | `main_window.cpp:639`、`:649`、`spectrum_widget.cpp:428`、`settings_dialog.cpp:195` | `set_gain` | **上轮已核实 UI 语义做对（负增益=RF 衰减+离散档位），但反向看工具面为零**：无任何写工具；`get_status` 明说增益/采样率不返回（`tool_schema.cpp:229`），`get_capabilities` 只读 gains_db 档表（`:1794`）。本轮新问题，据实列 D1 |
| D1-A2 | 采样率下拉（srCombo_ 左栏 + 设置偏好） | `main_window.cpp:633`、`settings_dialog.cpp:75` | `set_sample_rate` | 同上：正向核实过 UI 是能力驱动的诚实空态，工具面仍缺写入口 |
| D1-A3 | 前端抽取 decimCombo（关/×2/×4/×8） | `main_window.cpp:702` | `set_decimation` | 直连引擎抗混叠抽取 |
| D1-A4 | 直采模式 dsCombo_（关/I/Q） | `main_window.cpp:383` | `set_direct_sampling` | |
| D1-A5 | 偏移调谐 offsetChk_ | `main_window.cpp:386` | `set_offset_tuning` | |
| D1-A6 | RTL AGC / Tuner AGC / AI 页 Tuner AGC（rtlAgcChk_, tunerAgcChk_, aiAgcCheck_） | `main_window.cpp:388`、`:390`、`:2613` | `set_agc` | 三个入口一套状态，补工具时读回需统一 |
| D1-A7 | Bias-T 偏置器 biasTeeChk_ | `main_window.cpp:392`（接线 `:3328-3329`） | `set_bias_tee` | **硬件供电**，补工具时描述必须写明天线供电风险并走手动模式写门 |
| D1-A8 | 强制单声道 forceMonoCheck_ | `main_window.cpp:728` | `set_force_mono` | WFM 立体声混合置 0 |

### B. 音频/降噪（2 条）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-B1 | ANR 降噪开关+强度（anrCheck_, anrSlider_ 0..100） | `main_window.cpp:960`、`:964` | `set_anr` | |
| D1-B2 | 主音量 volumeSlider_（0..100）+ 输出设备 audioDeviceCombo_ | `settings_dialog.cpp:106`、`:94` | `set_audio_output` | set_network_audio_sink 只管网络外送，不管本地输出 |

### C. 录制参数（8 条；start/stop_recording 已有但参数面全缺）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-C1 | 录制对象 recTargetCombo_（IQ SigMF / 音频 WAV） | `main_window.cpp:1105` | `set_recording_target` | `start_recording` 描述称"由录制目标决定"（`tool_schema.cpp:120`）但工具无法设置该目标 |
| D1-C2 | 录制目录 recDirEdit_ | `main_window.cpp:1096` | `set_recording_dir` | get_recording_state 只读回（`:1814`） |
| D1-C3 | 文件名模板 recTemplateEdit_ | `main_window.cpp:1109` | `set_recording_template` | |
| D1-C4 | 立体声 recStereoCheck_ | `main_window.cpp:1114` | `set_recording_stereo` | |
| D1-C5 | 忽略静噪 recIgnoreSqlChk_ | `main_window.cpp:1117` | `set_recording_ignore_squelch` | |
| D1-C6 | 分段上限 recSegSpin（30..3600 s） | `main_window.cpp:1126` | `set_recording_max_segment` | 引擎 rec/max_seg_s 的 UI 面 |
| D1-C7 | 触发式分段 gatedCheck_ | `main_window.cpp:1143` | `set_gated_recording` | |
| D1-C8 | 值守录制 watchCheck_ + 门限 watchThrSlider_ + 前滚 watchPrerollSpin_ + 结束延时 watchHangSpin_ | `main_window.cpp:1148`、`:1163`、`:1174`、`:1181` | `set_watch_recording` | get_recording_state 仅回 watch_enabled（`tool_schema.cpp:1814`），门限/前滚/延时全无工具面 |

### D. 扫描（1 条，参数面整体缺失）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-D1 | 扫描面板 7 个参数：驻留 scanDwellSpin_、门限 scanThrSpin_、方向 scanDirCombo_、命中停留 scanHoldCombo_、消失延时 scanLingerSpin_、固定停留 scanHoldMsSpin_、只扫书签 scanBmOnlyChk_ | `main_window.cpp:1841`/`:1849`/`:1857`/`:1861`/`:1865`/`:1871`/`:1877` | `set_scan_params` | `scan_band` 只收 low/high/step 三参（`tool_schema.cpp:164-184`）；起止/步进有部分对应（见 D2-8），这 7 个参数 AI 完全够不着 |

### E. 频谱/显示（10 条）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-E1 | 余晖 persistCombo（关/低/高） | `spectrum_widget.cpp:241`、`settings_dialog.cpp:253` | `set_persistence` | |
| D1-E2 | 瀑布深度 depthCombo_（行数） | `spectrum_widget.cpp:525`、`settings_dialog.cpp:259` | `set_waterfall_depth` | |
| D1-E3 | 瀑布速度 scrollCombo_（1x/2x/4x） | `spectrum_widget.cpp:487` | `set_waterfall_speed` | |
| D1-E4 | 内置色板 paletteCombo_（经典/单色/Viridis） | `spectrum_widget.cpp:502` | `set_builtin_palette` | `set_color_map` 只收外部 JSON 文件，明文"内置 3 个不经本工具切换"（`tool_schema.cpp:1694-1695`）——内置色板切换是纯 UI 能力 |
| D1-E5 | 显示 dB 上下限（dbMinSpin_/dbMaxSpin_ + 设置双滑条） | `spectrum_widget.cpp:300`/`:305`、`settings_dialog.cpp:209`/`:222` | `set_spectrum_db_range` | |
| D1-E6 | 显示缩放 zoomSlider_ + 右缘竖排三滑条 | `settings_dialog.cpp:235`、`spectrum_widget.cpp:623` | `set_spectrum_zoom` | 三滑条（缩放/上限/下限）由同一 helper 生成 |
| D1-E7 | dB 格线密度 gridCombo | `spectrum_widget.cpp:381` | `set_db_grid_step` | |
| D1-E8 | 峰值门限 peakThreshSpin_（5..40 dB） | `spectrum_widget.cpp:409` | `set_peak_threshold` | export_peaks_csv 硬编码 6 dB 门限（`tool_schema.cpp:1147`），与此 UI 门限互不可见（并入 D2 备注） |
| D1-E9 | Max/Min 保持 + 衰减（maxHoldChk, minHoldChk, decayCombo） | `spectrum_widget.cpp:178`/`:232`/`:200` | `set_peak_hold` | |
| D1-E10 | 右缘滑条显隐 edgeRailChk_ | `settings_dialog.cpp:267` | `set_edge_rail_visibility` | 低优先级 |

### F. 源与网络（2 条）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-F1 | 源类型 srcTypeCombo_（本地 RTL-SDR / rtl_tcp / 测试信号） | `main_window.cpp:326` | `set_source_type` | `connect_network_source` 只能连 rtl_tcp（`tool_schema.cpp:1757-1781`），切回本地/测试源无工具 |
| D1-F2 | SpyServer 开关+端口（spyserverChk_, spyserverPortSpin_） | `main_window.cpp:439`、`:445` | `set_spyserver` | 端口 1024..65535 默认 5555 |

### G. GNSS/图层（2 条）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-G1 | GNSS 串口 gnssDeviceEdit_ + 波特率 gnssBaudCombo_ | `main_window.cpp:1233`、`:1237` | `connect_gnss` | |
| D1-G2 | 地图图层 layerGnssChk_/layerAdsbChk_/layerSatChk_ | `main_window.cpp:1245-1247` | `set_map_layers` | 低优先级 |

### H. 本站/任务参数（3 条）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-H1 | 本站位置 latSpin_/lonSpin_/altSpin_ | `settings_dialog.cpp:41`/`:45`/`:49` | `set_station_position` | `predict_passes` 只有 per-call 覆盖参（`tool_schema.cpp:265-274`）；`set_doppler_compensation` 要求"已设置本站位置"（`:1736-1738`）却无工具能设——AI 卫星任务闭环断在这里 |
| D1-H2 | 卫星过境任务模板 aiTemplateCombo_ 第 4 项"卫星过境接收"+ aiParamLat_/aiParamLon_/aiParamSatName_ | `main_window.cpp:2527`、`:2555`、`:2561`、`:2567` | `set_sat_pass_template` | `list_templates`/`run_template` 只有 3 个模板（`tool_schema.cpp:2242-2244`、enum `:2282`），UI 的第 4 个模板 AI 发起不了 |
| D1-H3 | 调谐步进 stepCombo_（1 Hz..1 MHz） | `main_window.cpp:613` | `set_tune_step` | 低优先级（tune_frequency 传绝对值不受影响） |

### I. VNA（2 条）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-I1 | VNA 串口连接 portEdit_ + 连接按钮 | `vna_panel.cpp:200` | `vna_connect` | `vna_device` 动作枚举只有 status/vbat/freq_offset/edelay/averaging（`tool_schema.cpp:1891-1893`），**AI 无法连接 VNA**——set_vna_sweep 无设备时 applied=false 只能"先接好设备"，而接设备只能人来点 |
| D1-I2 | 夹具厚度 thickEdit_（喂 μr 磁导率计算） | `vna_panel.cpp:229`（用点 `:357` `permeabilityNRW`） | `analyze_vna_permeability` | analyze_vna_* 系列均无厚度/磁导率入口 |

### J. 外设电台面板（radio_panel，TX，4 条）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-J1 | CAT 串口/波特率/协议（deviceEdit_, baudCombo_, protoCombo_） | `radio_panel.cpp:41`/`:45`/`:48` | `set_cat_control` | |
| D1-J2 | CW 速度 cwWpm_ + 文本 cwText_ | `radio_panel.cpp:71`、`:75` | `send_cw` | **发射类**：补工具必须带显式确认（confirm 参数或人工门）+ 法规提示，见 D3-2 |
| D1-J3 | AX.25 信标 callEdit_/pathEdit_/msgEdit_ | `radio_panel.cpp:85`/`:88`/`:91` | `send_ax25_beacon` | 同上，发射类 |
| D1-J4 | SoapySDR 发射 driverEdit_/soapyFreq_ | `radio_panel.cpp:101`、`:104` | `soapy_transmit` | 同上，发射类 |

### K. 外观/语言（2 条，低优先级）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-K1 | UI 缩放 scaleCombo_ | `settings_dialog.cpp:118` | `set_ui_scale` | |
| D1-K2 | 语言 languageCombo_ | `settings_dialog.cpp:126` | `set_language` | 改后需重启（UI 有提示） |

### L. 布局（1 条）

| # | UI 控件 | 证据 | 建议工具 | 备注 |
|---|---|---|---|---|
| D1-L1 | 左栏分组显隐（showAllLeftChk_ + leftPanelChecks_ 循环，12 组） | `settings_dialog.cpp:169`、`:177` | `set_left_groups` | `arrange_workspace` 只动 splitter 与右侧 tab 可见性（`tool_schema.cpp:2090`），左栏 12 个分组（`tokens.h:982-986`）的显隐 AI 够不着；右侧 14 tab（`:968-972`）已有工具覆盖 |

## 2. D2 有 UI 有工具但语义不一致（9 条）

| # | 不一致 | 证据 | 建议 |
|---|---|---|---|
| D2-1 | **解调模式三层集合不一致**：UI 15 种（含 BPSK/QPSK/ADS-B）vs ControlHub 白名单 12 种 vs `set_mode` schema 枚举仅 6 种 | UI：`main_window.cpp:663-664`；白名单：`tokens.h:1359-1362`（12 种，无 BPSK/QPSK/ADS-B）；schema 枚举 6 种：`tool_schema.cpp:109`；校验器严格按 enum 拒绝：`arguments_validator.cpp:97-100` | ①BPSK/QPSK/ADS-B 经任何工具都不可达（连白名单都不收）——要么补进白名单+枚举，要么 UI 标注"仅本地"；②描述称"POCSAG/m17/…传入也会被接受"（`tool_schema.cpp:100-101`）与 enum 校验实际拒绝矛盾——把 12 种全部写进 enumValues；③`set_vfo_mode` 无 enum、可达 12 种（执行器白名单同 `agent_tools.cpp:2389-2393`），两个工具可达集不同，应统一 |
| D2-2 | **fft_size 假成功**：schema 边界 256..65536，引擎只收 1024/2048/4096 且**静默丢弃**，回显当前值冒充请求值 | 边界：`tool_schema.cpp:23-24`、`:1663-1664`；引擎：`spectrum_engine.cpp:163-165`；执行器回显 engine 当前值：`agent_tools.cpp:2527-2530`；描述自认"静默忽略且回显当前值"：`tool_schema.cpp:1652-1654` | 边界改为枚举 {1024,2048,4096} 或对非法值诚实 ok:false——"回显当前值"让 agent 误以为已生效，违背自家"失败诚实报错"红线；UI 只给三档（`spectrum_widget.cpp:135`） |
| D2-3 | **窗函数拼写**：UI 显示 "FlatTop"，工具枚举 "Flattop"，执行器精确匹配后回落 Hann | UI：`spectrum_widget.cpp:158`；enum：`tool_schema.cpp:1670`；匹配：`agent_tools.cpp:2510-2512` | 统一为 "FlatTop" 或执行器大小写不敏感；agent 照抄 UI 文案会被静默降级为 Hann |
| D2-4 | **静噪门限无边界无单位**：工具只写"静噪门限 dB"，UI 滑杆 [-100,-20] dBFS 且注明是 48kHz 音频块 RMS 域 | 工具：`tool_schema.cpp:696-698`（threshold_db 无 min/max）；UI/tokens：`main_window.cpp:934`、`tokens.h:365-377` | schema 补 [-100,-20] 边界 + 描述写明"音频块 RMS dBFS 域，越负越灵敏"；另 `set_squelch` 的 `auto` 参数 UI 无对应控件（不算缺口，反向记录） |
| D2-5 | **VNA 点数/频率范围**：UI pointsSpin_ 1..401、start/stop 0..999999999；工具 points 只要求"正整数"、start/stop 无边界 | UI：`vna_panel.cpp:220-225`；工具：`tool_schema.cpp:956-963` | 工具侧补硬件实界（点数 ≤401，NanoVNA 面板已知上限）防下发硬件做不到的扫描 |
| D2-6 | **带宽三面不一致**：`set_bandwidth` 仅 7 个预设枚举；`set_vfo_bandwidth` 任意正数无边界；UI 键盘微调/画布拖边钳在 100..500k，而 bwCombo 预设含 2 MHz（ADS-B）——注释宣称"one range for every UI surface"（`main_window.cpp:165-168`）与预设表自相矛盾 | 枚举：`tool_schema.cpp:210-213`；无边界：`:1558-1562`；钳位：`tokens.h:750-751`；预设 500..2000000：`main_window.cpp:159-161` | 统一"合法带宽"定义：要么把 kVfoMaxBandwidthHz 提到 2 MHz，要么预设表标注 2 MHz 不可拖出；`set_vfo_bandwidth` 补同一边界 |
| D2-7 | **IQ 导出参数语义两样**：UI 对话框按时长 0.1..30 s，工具按 sample_count 1024..16777216 | UI：`main_window.cpp:4552-4557`；工具：`tool_schema.cpp:531-536` | 同一能力两种度量；工具补 seconds 可选参或描述写明换算口径（采样率×秒） |
| D2-8 | **扫描默认值/驻留口径**：UI 步进默认 100 kHz、驻留 100..2000 ms 可调默认 300；`scan_band` 步进缺省 200 kHz、驻留硬编码 ~20 ms | UI：`main_window.cpp:1836-1847`；工具：`tool_schema.cpp:160`、`:181` | 两个"扫频"功能默认口径不同（one-shot 扫峰 vs 扫描会话）；至少在 scan_band 描述注明"每步 ~20ms 固定驻留，不同于扫描面板" |
| D2-9 | **书签频率域**：编辑对话框允许 0.1..2200 MHz，`add_bookmark` 参数边界 24..1700 MHz（kFreqMinHz/kFreqMaxHz） | UI：`main_window.cpp:2032`；工具：`tool_schema.cpp:1286-1287`、`tokens.h:281-282` | UI 能存工具拒绝的频点，之后 tune_to_bookmark 调谐也会越界——对话框范围收到 tokens 域内 |

## 3. D3 参数语义反人类（8 条，不限于 UI）

| # | 问题 | 证据 | 建议 |
|---|---|---|---|
| D3-1 | **频率范围三处越硬件域**：扫描起止（0.1..2200 MHz）、书签对话框（0.1..2200 MHz）、AI 任务参数（100 kHz..2 GHz）都越过 tokens 合法域 24..1700 MHz，输入框不提示不可达 | `main_window.cpp:1822`/`:1830`/`:2032`/`:2533`/`:2539` vs `tokens.h:281-282`（kFreqMinHz=24e6、kFreqMaxHz=1700e6）；同文件 freqSpin_ 就是对的（`:583`） | 三处 setRange 对齐 tokens；或越界即时标红提示"超出本机可调范围" |
| D3-2 | **发射类写操作零确认**：发送 CW / 发送信标 / SoapySDR 发射均无确认对话框，全文件无 QMessageBox | `radio_panel.cpp`（全文 grep 无 QMessageBox/confirm/确认；按钮在 `:78`/`:94`/`:110` 附近） | 发射是监管敏感动作；至少弹一次确认+呼号/频率自报；这也是 D1-J2/J3/J4 补工具时的前置要求 |
| D3-3 | **"dBFS" 一词三种域**：静噪是 48 kHz 音频块 RMS dBFS（`tokens.h:366`），扫描门限是 -120..0 dBFS（`main_window.cpp:1851`，IQ/FFT 域），值守触发门限标签只写 "-50 dB"（`:1167`）不带 FS——同屏三种口径 | `tokens.h:365-377`、`main_window.cpp:1849-1854`、`:1163-1167` | 统一单位标注：音频域标 "dBFS(音频)"、频谱域标 "dBFS(频谱)"、值守门限补 FS 或改标 RSSI 域 |
| D3-4 | 值守触发门限标签 "-50 dB" 无 FS 后缀，与扫描门限后缀 " dBFS" 并排出现 | `main_window.cpp:1167` vs `:1854` | 同 D3-3，补单位 |
| D3-5 | VNA 起始频率允许 0 Hz（setRange(0, 999999999)），无物理意义 | `vna_panel.cpp:220` | 下限提到仪器实际下限（如 50 kHz） |
| D3-6 | 带宽"合法范围"自相矛盾：微调/拖边钳 500 kHz 上限，预设却有 2 MHz——用户拖不到 2 MHz 但下拉能设 | `tokens.h:750-751` vs `main_window.cpp:159-161`、`:165-168`（"one range" 注释） | 同 D2-6，先定哪个是真实硬件上限 |
| D3-7 | `delete_bookmark` 按 index 删、无 confirm 字段，书签列表插队即移位易误删（描述已警告"无撤销"，且手动模式有写门，故列低危） | `tool_schema.cpp:1337-1352` | 改按 freq/name 定位或强制 confirm:=true |
| D3-8 | `set_fft_params` 对非法 fft_size 静默忽略+回显当前值=假成功（文档化了但仍是坑，与"绝不静默夹值"红线冲突） | 同 D2-2 | 同 D2-2 |

README 默认值口径：grep 未发现 README 对增益/静噪/音量等默认值有可冲突的声明，**无 D3 条目**（不编造）。

## 4. 与正向对拍的衔接（不重复、不翻案）

- 上轮"不成立"项维持：set_color_map 色图文件路径入口、set_fft_params 信号链、set_noise_blanker 接线均在；增益 UI 语义（负增益=RF 衰减+离散档位）做对。**本轮 D1-A1/A2 不是翻案**：那两条核实的是"UI 侧语义/入口做对"，本轮发现的是"工具侧写入口为零"，属反向新问题。
- `settings-surface-audit.md` 三、3 两套带宽语义（Python 端）不在本轮 C++ 范围；C++ 端的带宽矛盾是新证据（D2-6/D3-6），不与之混记。

## 5. 可调控件中**不计入 D1** 的（12 项，如实记账）

| 控件 | 证据 | 不计入理由 |
|---|---|---|
| apiKeyEdit_/baseUrlEdit_/modelEdit_ | `settings_dialog.cpp:60-63` | AI 凭据/端点，安全边界上有意不暴露给 agent 自改 |
| aiManualCheck_（手动模式） | `main_window.cpp:2508` | 写操作安全门，故意 AI 不可写 |
| aiInput_/aiSessionCombo_ | `main_window.cpp:2642`/`:2479` | AI 面板自身的会话/输入交互，不是被操作的电台能力 |
| freqQuickEdit_/tuneHistCombo_/bandCombo | `main_window.cpp:591`/`:623`/`:681` | 一次性调谐动作/历史跳转/预设启动器，语义已被 tune_frequency 覆盖 |
| skyTimeSlider_/offAnaSeek_ | `main_window.cpp:1648`/`:2371` | 视图时间轴/回放进度，瞬态交互非持久设置 |
| 书签对话框 5 控件（nameEdit/freqMhz/modeCombo/bwKhz/groupEdit） | `main_window.cpp:2025-2048` | add_bookmark 五参全覆盖（范围问题另记 D2-9） |
| export 对话框 secsSpin | `main_window.cpp:4552` | export_iq_segment 覆盖能力（参数语义差异记 D2-7） |
| VFO 面板按钮/标尺 | `main_window.cpp:920` 附近 | set_vfo_*/add_vfo/switch_vfo/rename_vfo 系列覆盖（正向已核） |
| calibration 对话框 refCombo_/freqSpin_ | `calibration_dialog.cpp:253`/`:288` | calibrate_frequency 的 reference_type/reference_freq_hz 覆盖（枚举 handheld/gsm_fcch/manual 对应 UI 三项，`tool_schema.cpp:318`） |
| ctcss/cdcss/ft8/lrpt/nb/squelch/静噪链控件 | `main_window.cpp:739-930` 等 | set_ctcss/set_cdcss/set_ft8/set_lrpt/set_noise_blanker/set_squelch 正反双向均已核（唯一残留是 D2-4 边界/单位） |
| netAudio 三控件 + tcpHost/tcpPort | `main_window.cpp:515-541`/`:341-344` | set_network_audio_sink / connect_network_source 覆盖 |
| ppmSpin_ | `main_window.cpp:394` | apply_frequency_correction 覆盖（同一 QSettings 键 rtl/ppm：`tokens.h:322` ↔ `tool_schema.cpp:332`；范围 kPpmMin/Max 两边同 tokens，`tokens.h:313-314` ↔ `tool_schema.cpp:354-355`）——**核实一致，不是缺陷** |

## 6. 钉扎与声明

- 防漂移测试：`tests/test_cpp_tool_ui_reverse_audit.py`（offscreen、纯文本解析、不碰硬件）——
  ①控件实例化点计数=143 与本清单口径一致；②D1/D2/D3 条数=45/9/8 与本清单一致；
  ③D1 双账同步（§7 后）：45 个建议工具名在 `tool_schema.cpp` 的 `s.name` 集合与
  `agent_tools.cpp` 的 dispatchTable 行**双侧必须全在场**（第二轮 19 + 第三轮 26，
  一个都不能少——任何一侧漂移即红）。
- 本清单 §1–§5 为**静态审计结论**（2026-10-30 上午快照）：未编译未运行。
  所有行号以 2026-10-30 工作区为准。

## 7. 销账（2026-10-30 第二、三轮执行）

### D1：45/45 全部落地（第二轮 19 + 第三轮 26）

**第二轮已落地 19 条**——每条 = `tool_schema.cpp` 声明 + `agent_tools.cpp` 真执行器
（接 `engine->` 真 setter / AiConfig 真持久化）+ dispatchTable 行 + C++ 金样三处同步
（`test_tool_registry.cpp` kAllCxxTools/kExpectedWriteTools/109 计数、
`test_tool_schema.cpp` 描述金样、`test_agent.cpp` 门烟测入参）：

- **A 接收链硬件参数（8/8）**：`set_gain`（onSetGain，-90..49.6 dB，负=衰减）、
  `set_sample_rate`（onSetSampleRate，枚举四档同 tokens）、`set_decimation`（1/2/4/8）、
  `set_direct_sampling`（off/i/q→0/1/2）、`set_offset_tuning`、`set_agc`（rtl/tuner 双开关）、
  `set_bias_tee`（描述带馈电风险警告）、`set_force_mono`
- **B 音频（1/2）**：`set_anr`（enabled + strength_pct 0..100→引擎 0..1）
- **C 录制参数（8/8）**：`set_recording_target`（iq_sigmf/audio_wav）、`set_recording_dir`、
  `set_recording_template`、`set_recording_stereo`、`set_recording_ignore_squelch`、
  `set_recording_max_segment`（5..3600 s）、`set_gated_recording`、
  `set_watch_recording`（enabled/threshold_db/preroll_ms/hang_ms）
- **H（1/3）**：`set_station_position`——写 AiConfig（同设置对话框持久化路径）+
  ADS-B 参考点同步，闭合「set_doppler_compensation 要求已设本站却无工具能设」断环；
  描述诚实注明多普勒仍需捕获过境才 available
- **I（1/2）**：`vna_connect`（connectSerial/close，失败带 errorString，不假装已连）

**第三轮落地 26 条**（行标带 `#` 前缀，防撞 §1 快照行计数正则）——同上四件套 +
C++ 金样三处 + 双账同步；UI 面经 `dsp::UiControlSurface` 抽象接缝（35 纯虚，
`ui_control_surface.h` 依赖最小头文件；MainWindow 多继承实现并注册进 engine）驱动
**真控件**（setValue/setCurrentIndex/setChecked 触发既有 handler 落 AppSettings+生效，
不复制业务逻辑），headless 指针为空→诚实报「无 UI 控制面（该参数由桌面端控件持有，
headless 不可用）」；B2/H2/K1/K2 为引擎/配置直连不走 surface：

| # | 落地 |
|---|---|
| #D1-B2 | `set_audio_output`——volume 0..100→引擎 0..1 + rx/volume 持久化；device 精确匹配 description（"default"=系统默认），匹配失败带 available_devices 诚实拒绝 |
| #D1-D1 | `set_scan_params`——7 参数域逐字取控件 setRange（dwell 100..2000/thr -120..0/linger+hold 0..60000），下一次『开始扫描』生效（描述如实注明进行中扫描不受影响） |
| #D1-E1 | `set_persistence`（off/low/high） |
| #D1-E2 | `set_waterfall_depth`——**能力对拍不缩水**：kWaterfallDepthChoices 六档全放行（128..4096，UI depthCombo_ 同一数组；施工契约曾误写三档，落地时修为全档） |
| #D1-E3 | `set_waterfall_speed`（1/2/4 行/帧） |
| #D1-E4 | `set_builtin_palette`（classic/monochrome/viridis），闭「内置色板切不了」 |
| #D1-E5 | `set_spectrum_db_range`（min -140..-20 / max -40..20，min<max 校验，tokens 同源） |
| #D1-E6 | `set_spectrum_zoom`（1.0..16.0，kZoomMin/Max 同源） |
| #D1-E7 | `set_db_grid_step`（10/20/40） |
| #D1-E8 | `set_peak_threshold`（5..40 dB） |
| #D1-E9 | `set_peak_hold`（max/min 开关 + decay slow/medium/fast=0.5/1.5/3.0 dB/帧） |
| #D1-E10 | `set_edge_rail_visibility` |
| #D1-F1 | `set_source_type`（local_rtlsdr/rtl_tcp/test_signal；描述如实注明 test_signal=合成 IQ 非真实接收、rtl_tcp 只切模式建链另用 connect_network_source） |
| #D1-F2 | `set_spyserver`——绑定失败（端口被占）诚实 ok:false，绝不谎报监听中 |
| #D1-G1 | `connect_gnss`——start() 异步，「已发起连接≠已定位」如实回显，失败带错误串 |
| #D1-G2 | `set_map_layers`（gnss/adsb/sat 三开关） |
| #D1-H2 | `set_sat_pass_template`——planSatelliteCapture + PendingPlanStore 登记（executed=false 待用户确认），闭「UI 第 4 模板 AI 发起不了」断环；**demo/stale TLE 一律 ok:false 绝不登记**（镜像 UI 诚实分支） |
| #D1-H3 | `set_tune_step`（1..1000000 七档，kStepValuesHz 同源） |
| #D1-I2 | `analyze_vna_permeability`（26 条里唯一读工具 s.write=false）——permeabilityNRW 真算 μr（厚度 mm 同面板夹具厚 ×1e-3）；未连接/无扫频诚实 ok:false，病态 ok:true+valid:false+note，μr 非有限→JSON null |
| #D1-J1 | `set_cat_control`——TX 链 confirm=true 显式确认门（缺失/false/非布尔一律拒），失败回显 uiRadioStatus() 原文 |
| #D1-J2 | `send_cw`——confirm 门 + 法规提示；空文本拒绝；WPM 5..60 |
| #D1-J3 | `send_ax25_beacon`——confirm 门 + 法规提示 |
| #D1-J4 | `soapy_transmit`——confirm 门 + 法规提示；**只打开发射设备一步，PTT/载波保持人工按住**（D3-2 取舍，绝不自动键控） |
| #D1-K1 | `set_ui_scale`（0.7/1.0/1.25/1.5 与设置对话框 scaleCombo_ 同档，越档拒绝防下次启动静默跳档） |
| #D1-K2 | `set_language`（zh_CN/en/ja 严格枚举不归一）；描述如实注明翻译器仅启动时装载、重启生效 |
| #D1-L1 | `set_left_groups`——单组单调（kLeftPanelIds 12 组枚举 + visible 布尔，原子积木不打包），显隐持久化 ui/hiddenLeftGroups 同设置对话框路径 |

架构落法（原子积木红线）：26 条=26 个独立小工具让模型逐个调用，未打包成专用大工具；
TX 四条确认门放**工具层**（RadioPanel 裸路径跳过 QMessageBox，避开按住 PTT 弹模态
卡键坑），门文案统一「发射链路操作需 confirm=true 显式确认（发射受无线电管理条例约束）」。

### D2：9/9 修复

| # | 修法 |
|---|---|
| #D2-1 | set_mode 枚举扩为 tokens::kControlHubModes 12 种（与 CH 同源构造，不再各写各的）；描述删掉"传入也会被接受"的失实表述，如实标注 BPSK/QPSK/ADS-B 仅 UI 可达；set_vfo_mode 补同一枚举。**执行时另坐实一个潜伏 bug**：execSetMode/execSetVfoMode 用 toUpper() 匹配白名单，"m17"→"M17" 永远匹配不上（引擎解码键是精确大小写）——改为大小写不敏感匹配 + 回显 token 原拼写 |
| #D2-2 | fft_size 从 [256,65536] 边界改为枚举 {1024,2048,4096}；执行器越档明确 ok:false，不再"静默忽略且回显当前值"假成功 |
| #D2-3 | 窗函数：schema 枚举改 UI 拼写 "FlatTop"，mapWindowString 改大小写不敏感（旧拼写 Flattop 仍识别，test_agent 金样不断） |
| #D2-4 | set_squelch threshold_db 补 [-100,-20] 边界（tokens 同源）+ 描述写明 48 kHz 音频块 RMS dBFS 域；执行器同步域拒（-10.0 等越域金样随修复改为 -30.0，并新增越域拒绝断言） |
| #D2-5 | set_vna_sweep：start/stop 补 50 kHz..999999999 边界、points 补 1..401（面板硬上限），描述写明 0 Hz 无物理意义 |
| #D2-6 | tokens::kVfoMaxBandwidthHz 500k→2M（对齐 ADS-B 预设，"one range" 注释自相矛盾消除）；set_vfo_bandwidth 补 [100,2M] 边界；拖边/微调/画布随 token 自动同界 |
| #D2-7 | export_iq_segment 增可选 seconds（0.1..30，UI 对话框同口径），按 latestSampleRateHz() 折算样本数（sample_count 优先）；引擎补 latestSampleRateHz() 只读口 |
| #D2-8 | scan_band 描述明写"每步约 20 ms 固定驻留，不同于扫描面板可调驻留" |
| #D2-9 | 书签对话框 freqMhz setRange 对齐 tokens 24..1700 MHz |

### D3：8/8 修复

| # | 修法 |
|---|---|
| #D3-1 | 扫描起止（24..1700 MHz）、AI 任务参数（24e6..1700e6 Hz）三处 setRange 对齐 tokens 域 |
| #D3-2 | radio_panel 发射前确认框：CW（WPM+文本+法规提示）、信标（呼号/路径/内容）、Soapy **在"打开设备"时确认**（PTT 是按住即发，pressed() 弹模态有卡键风险——确认点前移到进入发射就绪，返回体如实注明设计取舍） |
| #D3-3/3-4 | 值守门限标签 "-50 dB"→"-50 dBFS"（含动态刷新两处 + 电平行"门限 %2 dBFS"）；扫描门限后缀改 " dBFS(频谱)" 区分域 |
| #D3-5 | VNA 起/止 setRange 下限 0→50000 Hz |
| #D3-6 | 同 D2-6（2 MHz 上限统一） |
| #D3-7 | delete_bookmark 增必填 confirm:=true（仅真 JSON true 放行），金样四处同步（test_agent 烟测/真实删除/null store、test_control_hub），并新增"无 confirm 必拒"断言 |
| #D3-8 | 同 D2-2 |

### 声明（不虚报）

- 本机无 Qt6/CMake 工具链，**上述 C++ 改动未编译未运行**。正确性由三层静态钉住：
  Python 防漂移双账（`tests/test_cpp_tool_ui_reverse_audit.py`）、C++ 金样
  （`test_tool_registry`/`test_tool_schema`/`test_agent`/`test_control_hub` 随修复同步）、
  schema↔executor 双表一致性（135 = 90+19+26，93 写/42 读）。**待 CI 编译首验**。
- test_agent 金样更新记录（断言随修复更新，非放宽换绿）：set_squelch 越域值
  -10.0→-30.0 并新增越域拒绝用例；delete_bookmark 全部调用点补 confirm=true
  并新增无确认拒绝用例；工具计数 90→109、写/读 49/41→68/41。
- 第三轮金样更新记录：工具计数 109→135、写/读 68/41→93/42；
  `test_tool_registry` 名单 +26/+25/+1（写/读）并修正存量硬计数
  （notARegression 109→135、末名 vna_connect→set_left_groups）；
  `test_tool_schema` 描述金样 +26（脚本逐字抽取保字节一致）+ 存量硬计数
  （specs 109→135、文档头计数 90→135）；`test_agent` 门烟测入参 +25 组
  （TX 四条带 confirm=true）+ 诚实空态例外名单 +analyze_vna_permeability。

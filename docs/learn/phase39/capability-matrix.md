# Phase39 Wave1 — 桌面 vs 移动 能力差清点矩阵

> 只读勘察，不改功能代码。证据均为 `file:line`，相对仓库根 `/home/user/Doubao/chats/38438160041798146`。
> 桌面 = `cpp/`（Qt 本机引擎）；移动 = `mobile/lib/`（Flutter，rtl_tcp 瘦客户端 + 桌面 ControlHub 只读查看器）。
> 判定三分类：**真差距**（同架构下移动缺该有之物）/ **架构性差异**（两端信号路径本就不同，不移植）/ **已对齐**（能力等价或移动经既定桥接覆盖）。

---

## 0. 信号路径基线（判定前提）

| 维度 | 桌面 | 移动 |
|---|---|---|
| 数据链路 | 本机直接开 RTL 设备，`SpectrumEngine` 持有 IQ，本地 FFT/解调/解码全在进程内 | rtl_tcp **客户端**，仅拿远端 IQ 做本地 FFT + NFM/WFM 解调（`mobile/lib/services/radio_controller.dart:455-477`） |
| 数字解码 | 引擎内 POCSAG/M17/VOR/CW/ADS-B/星座（`cpp/src/dsp/vfo_manager.h:77-79`） | 不本地解码；经 HTTP 只读拉桌面 ControlHub 结果（`mobile/lib/widgets/remote_decoder_panel.dart:1-12`） |
| 控制点 | 35 Agent 工具 + ControlHub HTTP 6 端点 | 8 AI 工具直接调本机 rtl_tcp（`mobile/lib/app/ai_tools.dart:53-402`） |

---

## 1. 解码面板

| 能力 | 桌面 | 移动现状 | 判定 | 证据 |
|---|---|---|---|---|
| POCSAG 寻呼 | 引擎解码 + 右栏「寻呼」面板，行=真实 RIC/功能/文本，可清空 | **只读查看器**：轮询 `GET /pocsag_messages` 渲染列表，标「远程引擎·POCSAG」，不本地解码、不下发命令 | **已对齐**（经既定桥接：移动=远程只读查看器） | 桌面 `cpp/src/ui/main_window.cpp:964-971`；移动 `mobile/lib/widgets/remote_decoder_panel.dart:1-12`、`mobile/lib/services/control_hub_client.dart:99-107` |
| M17 呼叫 | 引擎解码 + 「m17」面板；语音帧诚实标「语音·未解码」（Codec2 未内置） | **只读查看器**：轮询 `GET /m17_calls`，语音帧同样标「语音·未解码」 | **已对齐**（同桥接） | 桌面 `main_window.cpp:973-982`；移动 `remote_decoder_panel.dart:8-9`、`control_hub_client.dart:110-115` |
| VOR 径向 | 引擎解码 + 「VOR」仪表面板；未锁定藏针、径向读「—」 | **只读查看器**：轮询 `GET /vor_radial`，未锁定不画针、方位读「—」 | **已对齐**（同桥接） | 桌面 `main_window.cpp:984-993`；移动 `remote_decoder_panel.dart:10-11`、`control_hub_client.dart:118-120` |
| CW 解码 | 右栏「CW」文本面板，切 CW 模式解码（`main_window.cpp:925-945`） | 无（解调模式仅 NFM/WFM，`radio_controller.dart:432-435`） | **架构性差异**（移动解调链只做 NFM/WFM 音频，无 CW/数字解调器；CW 属引擎内多 VFO 解码，见 §4） | 桌面 `main_window.cpp:945`；移动 `radio_controller.dart:432-435` |
| ADS-B（1090） | 右栏 8 列表格（ICAO/呼号/高度/速度…）+ 1s TTL 剪枝（`main_window.cpp:947-958`） | 无 | **架构性差异**（ADS-B 需 2MHz 宽频捕获 + 引擎内 decoder；移动为瘦客户端，且未对接桌面该端点） | 桌面 `main_window.cpp:954-958`；移动无对应页 |
| 星座图（BPSK/QPSK scatter） | 右栏「星座」页 + 缩放/直方图工具条（`main_window.cpp:1001-1066`） | 无 | **架构性差异**（星座来自选中数字 VFO 的实时符号流，移动无数字 VFO 解调） | 桌面 `main_window.cpp:1013` |
| 气象卫星 NOAA APT | 中央「气象」页，解码图 1818px（`main_window.cpp:830-840`） | 无（天空页只做过境预测+捕获，不解 APT 图） | **架构性差异**（APT 解码是引擎内基带处理；移动只捕获下行频点） | 桌面 `main_window.cpp:837-839`；移动 `sky_page.dart:858-868`（仅捕获） |

---

## 2. 扫频 / 书签

| 能力 | 桌面 | 移动现状 | 判定 | 证据 |
|---|---|---|---|---|
| 范围扫频核心（起/止/步进/驻留/门限，真实调谐+量测） | 频率扫描组：起始/终止/步进(10k/12.5k/100k/1M)/驻留/门限 | 有：起止对话框，**步进固定 25kHz、驻留固定 300ms**，门限取当前静噪门限，真实调谐+真实 RMS 量测，命中写活动日志 | **已对齐**（核心等价；步进/驻留为硬编码简化） | 桌面 `main_window.cpp:1254-1286`；移动 `mobile/lib/pages/spectrum_page.dart:466-524`、`radio_controller.dart:626-670` |
| 扫频方向（上/下/来回） | 有 `scanDirCombo_` 向上/向下/来回 | 无（单向顺序扫） | **真差距**（纯 UI/控制参数，不动 DSP） | 桌面 `main_window.cpp:1288-1290`；移动 `radio_controller.dart:641-643` |
| 扫频命中停留模式（直到信号消失/固定时长）+ 消失延时/固定停留 | 有 `scanHoldCombo_` + linger/hold ms | 无（驻留到点即移） | **真差距**（控制策略参数） | 桌面 `main_window.cpp:1292-1306` |
| 扫频暂停/恢复 | 有 暂停 按钮 | 无（仅开始/停止） | **真差距**（一行状态机） | 桌面 `main_window.cpp:1326-1327`；移动 `spectrum_page.dart:890-896`（仅停止） |
| 只扫书签 | 有 `scanBmOnlyChk_` | 无 | **真差距** | 桌面 `main_window.cpp:1308-1309` |
| 命中存为书签 | 有「存入书签」（仅命中态可用，带真实 freq/mode/bandwidth） | 无（命中只写活动日志） | **真差距** | 桌面 `main_window.cpp:1333-1340`；移动 `radio_controller.dart:654-662` |
| 书签 CRUD + 跳频 | 5 列表格（名称/频率/模式/带宽/分组），增改删、点击跳频、编辑对话框 | 有：命名收藏 + chip 列表，点击跳频+应用模式；**带宽随模式派生不存**；无「分组」列 | **已对齐**（核心增删跳频等价；分组列/独立带宽为简化） | 桌面 `main_window.cpp:1356-1379`；移动 `spectrum_page.dart:528-562,920-963` |
| 固定频率参考标记 | 桌面 VFO 框/刻度带承担参考线 | 有：钉频为画布琥珀虚线参考线（`fixedMarksHz`），与书签区分 | **已对齐**（移动用 marks 补参考线） | 移动 `spectrum_page.dart:964-1005` |

---

## 3. 静噪（含自动门限）

| 能力 | 桌面 | 移动现状 | 判定 | 证据 |
|---|---|---|---|---|
| 静噪开关 + 门限滑杆 | 有（音频 RMS 域） | 有（解调音频真实 RMS） | **已对齐** | 桌面 `main_window.cpp:575-590`；移动 `spectrum_page.dart:727-736`、`radio_controller.dart:730-747` |
| 自动门限（门限=实测噪声底+裕量，自动跟随） | 有「自动门限」checkable，同域噪声底 | 有「自动」FilterChip，门限=噪声底+`squelchAutoMarginDb`；手动拖杆即退出自动 | **已对齐**（语义逐字对齐） | 桌面 `main_window.cpp:591-600`；移动 `spectrum_page.dart:743-750`、`radio_controller.dart:749-758` |
| 门开合状态显示 | CLOSED/OPEN label | SQ OPEN/CLOSED/OFF chip | **已对齐** | 桌面 `main_window.cpp:601-603`；移动 `spectrum_page.dart:348-352` |
| ANR 音频降噪（STFT-Wiener） | 有 ANR 组（开关+强度滑杆） | 无 | **架构性差异**（ANR 在引擎 DSP 链，移动解调链未实现；非 UI 缺失） | 桌面 `main_window.cpp:606-621` |
| Noise Blanker 噪声抑制 | 有 checkbox 接引擎 `setNoiseBlanker` | 无 | **架构性差异**（DSP 后处理，移动解调链无此级） | 桌面 `main_window.cpp:625-628` |

---

## 4. 多 VFO

| 能力 | 桌面 | 移动现状 | 判定 | 证据 |
|---|---|---|---|---|
| 多 VFO（增/删/复制/重命名/切换/独立 freq/offset/bandwidth/mode） | VFO 列表 + 添加/复制/删除按钮；控制命令 `vfo_add/remove/select/set_freq/...` + `add_vfo/switch_vfo/rename_vfo` | **无 VFO 概念**：单 `_freqHz` 中心频点，单一解调 | **架构性差异**（历史裁决：rtl_tcp 瘦客户端单通道；多 VFO 需引擎内 channelizer+多态 decoder，见 architectural-diffs） | 桌面 `main_window.cpp:544-570`、`control_hub.cpp:100-118,819-838`；移动 `radio_controller.dart:195`（单 `_freqHz`） |
| 解码面板按 VFO 通道取数 | `?channel=N` query 选 VFO 快照 | 不适用（只读全量快照，无通道概念） | **架构性差异**（随 VFO 不移植） | 桌面 `control_http_server.cpp:41-57,272-278` |

---

## 5. 双游标

| 能力 | 桌面 | 移动现状 | 判定 | 证据 |
|---|---|---|---|---|
| 双测量游标 A/B（放置/拖动/显示 A-B 频差/SNR） | 有「游标A/B/清游标」按钮，可拖动测频差 | 无（仅单频点 tap 调谐 + 固定参考线 marks，无 A/B 双游标频差测量） | **真差距**（纯画布交互，可在 SpectrumDisplay 叠加两个可拖竖线，不碰 DSP） | 桌面 `cpp/src/ui/spectrum_widget.cpp:123-138`；移动 `spectrum_page.dart:288-299`（无 cursor A/B） |

---

## 6. 录制 / 回放

| 能力 | 桌面 | 移动现状 | 判定 | 证据 |
|---|---|---|---|---|
| 解调音频 WAV 录制（16-bit + sidecar） | 有（录制对象可选「解调音频 WAV」） | 有（过静噪门后录「听到的」，16-bit 小端 WAV + sidecar JSON） | **已对齐** | 桌面 `main_window.cpp:653-660`；移动 `radio_controller.dart:548-593`、`ai_tools.dart:222-260` |
| IQ 基带录制（SigMF cf32 + meta） | 有（录制对象可选「基带 IQ SigMF」） | 无（移动只录解调音频 WAV） | **架构性差异**（历史裁决：WAV vs SigMF IQ，ai 入口一致产物不同；见 architectural-diffs） | 桌面 `main_window.cpp:654`、`cpp/src/dsp/recorder.cpp:91-111`；移动 `ai_tools.dart:227-228` |
| 录制文件列表 + 删除 | 有（录制库列表 + 刷新/删除/复制路径） | 有（录音列表 + 真实删除 + 清空） | **已对齐** | 桌面 `main_window.cpp:1562-1588`；移动 `recordings_page.dart:42-86,181-248` |
| 回放（真实出声） | 有（分块 20ms 推 AudioOutput） | **未接线**：`onPlay/onStop/playing` 为可选注入，未注入时诚实不渲染播放按钮（原生 AudioTrack 未真机验证） | **真差距**（UI 缝已留，缺原生播放落地；非架构禁止） | 桌面 `main_window.cpp:1637-1653`；移动 `recordings_page.dart:19,209-229` |
| 值守/触发式录制（信号超过门限才录，带前滚/结束延时） | 有（值守录制 + 触发门限/前滚/结束延时） | 无（仅手动开/关录） | **架构性差异**（值守录制器 `gated_recorder` 在引擎；移动为便携值守场景，资源/电源受限；非本轮桥接对象） | 桌面 `main_window.cpp:670-713`、`cpp/src/dsp/gated_recorder.cpp`；移动 `radio_controller.dart:548-593` |
| 离线分析（打开 .wav/.sigmf，暂停/seek 过 DSP 链） | 有（离线分析组 + seek 滑条） | 无 | **架构性差异**（离线文件源过 DSP 链是引擎能力；移动无离线文件源） | 桌面 `main_window.cpp:1594-1687` |
| 文件名模板 / 立体声 / 忽略静噪持续录 | 有（`{time}_{freq}_{mode}`/立体声/忽略静噪） | 无（固定命名/单声道/录过门后音频） | **架构性差异**（桌面桌面形态偏好；移动便携场景不优先） | 桌面 `main_window.cpp:657-666` |

---

## 7. 时空视图

| 能力 | 桌面 | 移动现状 | 判定 | 证据 |
|---|---|---|---|---|
| 时空 2x2 概览（时间源/接收目标/多普勒/GNSS） | 中央「时空视图」页：设备/信号/解码/GNSS tile + 目标/时间源/多普勒三行 | 有 `SpacetimeStatusCard` 2x2：时间源/接收目标/多普勒补偿/GNSS，同 SpRole 语义、诚实空态 | **已对齐**（结构/语义对齐，移植自桌面 `spacetime_format.h`） | 桌面 `main_window.cpp:848-903`；移动 `mobile/lib/pages/spacetime_status.dart:30-112` |
| 多普勒补偿（真实 range-rate 实时补偿并显值） | 有「多普勒补偿」开关，实时接 SGP4 range-rate | **未接线**：`dopplerHz` 恒 null，诚实显示「未补偿（无目标）」；仅捕获时一次性预测调谐 | **真差距**（移动有 SGP4 传播，缺实时 range-rate 补偿引擎；见候选清单） | 桌面 `main_window.cpp:1696`（`dopplerCompChk_`）；移动 `sky_page.dart:422-427`、`spacetime_status.dart:98-108` |
| 天空极坐标雷达 + 过境表 + 指向引导 | 右栏 Sky 页极坐标 + TLE 新鲜度 + 过境表 | 有：`SkyRadar` 极坐标 + 24h 过境表 + az/el 指向引导卡 + 时间预览滑条±30min | **已对齐**（移动甚至有时间预览滑条，桌面未见） | 移动 `sky_page.dart:398-405,753-807,504-627`；桌面 `main_window.cpp:1069-1240` |
| 世界地图（GNSS/ADS-B/卫星图层） | 中央「世界」页离线地图 + GNSS 串口配置 | 无（天空页只极坐标，无地图） | **架构性差异**（地图为桌面大屏交互；移动用极坐标雷达替代） | 桌面 `main_window.cpp:737-785` |
| GNSS 导航卫星（在视列表） | 导航星表（5 列） | 有 `_NavSatList`（SGP4 预测，「预:」前缀诚实标注） | **已对齐** | 桌面 `main_window.cpp:1146`；移动 `sky_page.dart:871-940` |

---

## 8. AI 会话（多会话 / 压缩 / 流式）

| 能力 | 桌面 | 移动现状 | 判定 | 证据 |
|---|---|---|---|---|
| 多会话（列表/新建/切换/删除） | 会话下拉 + 新/重命名/删 | 有：抽屉列表 + 新建/切换/删除，持久化同形 JSON（KvStore） | **已对齐** | 桌面 `main_window.cpp:1714-1732`；移动 `chat_page.dart:132-154,452-524`、`chat_session_store.dart:177-227` |
| 会话重命名 | 有「重命名」按钮 | 无（自动用首条/频率占位，无手动重命名） | **真差距**（小：加一个重命名入口） | 桌面 `main_window.cpp:1723-1724`；移动 `chat_page.dart`（无 rename） |
| 上下文压缩（早期轮次超预算折叠为摘要；可手动触发） | 有「压缩上下文」按钮 | **无**：history 全量直传，无折叠/摘要 | **真差距**（移动长会话会撑 token 预算；见候选清单） | 桌面 `main_window.cpp:1734-1738`；移动 `ai_client.dart:429-430`（`working` 全量）、`chat_session_store.dart:38-40` |
| 流式输出（SSE 逐字） | `aiChat_` QPlainTextEdit + input/发送 | 有：partial 逐字 + ▋光标，完成固化一次，错误保留 partial | **已对齐**（移动气泡流式 UX 更细） | 桌面 `main_window.cpp:1858-1866`；移动 `chat_page.dart:340-366,633-644` |
| 工具调用内联显示（chip/步骤） | 工具结果进 `aiChat_` 文本流 | 有：`_UiToolCall` chip + `TaskProgressView`（开始/结果） | **已对齐** | 移动 `chat_page.dart:344-356,621-632` |
| 手动模式（AI 只建议不执行写工具） | 有「手动模式」checkbox | 有「AI 接管」开关，mutating 工具被 gate 回 `gated:true` | **已对齐** | 桌面 `main_window.cpp:1744-1747`；移动 `settings_page.dart:450-468`、`ai_tools.dart:404-424` |
| 自主多步任务模板（扫频找信号/目标捕获/固定录制/卫星接收，运行+停止+步骤视图） | 有：模板下拉 + 可编辑参数 + 运行/停止 + `TaskStepsView` | **无**：`TaskTemplatesBar` 仅把草稿填入输入框（不自动多步执行） | **真差距**（或架构性：依赖桌面 35 工具全集；见 architectural-diffs §AI 任务） | 桌面 `main_window.cpp:1758-1830`；移动 `chat_page.dart:401-402`、`ai_client.dart`（仅单轮工具循环） |
| 会话持久化跨端互读 | 写真实文件 `AppData/ai_sessions` | 写 KvStore，同形 JSON 可手工互读 | **已对齐**（容器差异已文档化，非差距） | 移动 `chat_session_store.dart:32-40` |

---

## 9. 工具调用（桌面 35 vs 移动）

| 桌面工具（`agent_tools.cpp:855-892`） | 移动是否有（`ai_tools.dart`） | 判定 | 证据 |
|---|---|---|---|
| tune_frequency / set_frequency | ✅ set_frequency | 已对齐（命名不同，能力等价） | 移动 `ai_tools.dart:60-108` |
| set_mode | ✅ set_mode（仅 nfm/wfm） | 已对齐（模式集差异属解调链） | 移动 `ai_tools.dart:109-140` |
| start_recording / stop_recording | ✅ | 已对齐（产物 WAV vs SigMF，入口一致） | 移动 `ai_tools.dart:222-293` |
| export_iq_segment | ❌ | 架构性差异（IQ 导出属引擎 baseband，移动无 IQ 录制） | 桌面 `agent_tools.cpp:860` |
| scan_band | ⚠️ 部分（移动范围扫描走 UI 非 AI 工具） | 真差距（AI 不可触发扫频） | 移动 `ai_tools.dart`（无 scan 工具） |
| set_bandwidth | ❌（移动带宽随模式派生） | 架构性差异 | 移动 `spectrum_page.dart:25-29` |
| get_status | ✅ | 已对齐（五态语义对齐） | 移动 `ai_tools.dart:294-322` |
| predict_passes | ✅ | 已对齐 | 移动 `ai_tools.dart:323-401` |
| calibrate_frequency / apply_frequency_correction（PPM 校正） | ❌ | 架构性差异（PPM 校正桌面高级面板 `main_window.cpp:329`） | 桌面 `agent_tools.cpp:865-866` |
| get_pocsag/m17/vor | ❌（移动经 HTTP 面板查看，非 AI 工具） | 架构性差异（移动 AI 不读远程解码） | 移动 `ai_tools.dart`（无此 3 工具） |
| set_network_audio_sink / get_network_audio_status | ❌ | 架构性差异（网络音频 sink 是桌面形态） | 桌面 `agent_tools.cpp:871-872` |
| start/stop/get_scan_link（扫描联动） | ❌ | 真差距（与扫频差距同源） | 桌面 `agent_tools.cpp:873-875` |
| set_squelch / get_squelch_status | ❌（移动有静噪 UI 但未暴露 AI 工具） | 真差距（静噪已有本地能力，补 AI 工具便宜） | 移动 `ai_tools.dart`（无 set_squelch） |
| list/add/tune_to/delete_bookmark | ❌（移动有书签 UI 但无 AI 工具） | 真差距（本地能力已有，补 AI 工具便宜） | 移动 `ai_tools.dart`（无 bookmark 工具） |
| list_vfos / add_vfo / switch_vfo / rename_vfo | ❌ | 架构性差异（随多 VFO 不移植） | 桌面 `agent_tools.cpp:882-885` |
| list_recordings / delete_recording / export_recording | ❌（移动有录制 UI 但无 AI 工具） | 真差距（本地能力已有，补 AI 工具便宜） | 移动 `ai_tools.dart`（无 recording list/delete 工具） |
| set_fft_params / set_color_map / get_spectrum_status | ❌ | 架构性差异（FFT/色板是桌面画布控制） | 桌面 `agent_tools.cpp:889-891` |
| （移动独有）set_gain / set_sample_rate | ✅（移动有，桌面 AI 表无） | 移动反超（不判差距） | 移动 `ai_tools.dart:141-221` |

**小计**：移动 AI 工具 8 个（`ai_tools.dart:60-401`）；桌面 35 个。可补且本地能力已具备的「真差距工具」= set_squelch、list/add/tune_to/delete_bookmark、list/delete_recordings、scan_band；其余缺失均随架构性差异（VFO/IQ/网络音频/FFT/远程解码）不移植。

---

## 10. 状态显示

| 能力 | 桌面 | 移动现状 | 判定 | 证据 |
|---|---|---|---|---|
| 连接状态机 | 5 态：no_telemetry/connected/dropped/error/disconnected（含 source 名 + readback freq/sr/gain） | 5 态：disconnected/connecting/reconnecting/connected/error（指数退避 1→15s 自动重连） | **已对齐**（词汇不同：dropped↔reconnecting，语义等价，属 rtl_tcp 客户端重连语义） | 桌面 `control_hub.cpp:752-766`；移动 `radio_controller.dart:386-415` |
| 紧凑状态栏（频率/模式/RSSI/静噪/采样率/增益） | 状态栏 mode/sr/vfo/gain/sdr（1Hz 遥测推） | 有：频率/模式/RSSI(峰 dBFS)/SQ 态/采样率/AGC|增益 chip 行 | **已对齐** | 桌面 `main_window.cpp:1903-1908`；移动 `spectrum_page.dart:339-357` |
| RSSI/S 表 | SMeter + sMeterWidget | 有 `SMeter`（signalDbfs/noiseFloorDbfs） | **已对齐** | 移动 `spectrum_page.dart:284-287` |
| 设备信息卡（连接态/后端/采样率/频率回读） | 设备管理面板 | 有 `DeviceInfoCard` | **已对齐** | 移动 `settings_page.dart:373-382` |

---

## 判定统计（本轮清点）

| 判定 | 项数 | 明细 |
|---|---|---|
| **已对齐** | 21 | POCSAG/M17/VOR 远程查看、扫频核心、书签 CRUD、固定标记、静噪开关+自动门限+状态、音频 WAV 录制、录制列表删除、时空 2x2 结构、极坐标雷达+过境+引导、时间预览滑条、导航星表、AI 多会话、流式、工具内联、手动模式、会话互读、状态机、状态栏、SMeter、设备信息、3 个 AI 调谐工具 |
| **架构性差异** | 17 | CW、ADS-B、星座图、气象 APT、ANR、Noise Blanker、多 VFO + channel、IQ SigMF 录制、值守/触发录制、离线分析、录制模板/立体声/忽略静噪、世界地图、export_iq_segment、set_bandwidth、PPM 校正、远程解码 3 工具、网络音频 sink、VFO 4 工具、FFT/色板工具 |
| **真差距** | 10 | 扫频方向/命中停留/暂停/只扫书签/命中存书签（扫频完备性 5 项）、双游标 A/B、录制回放落地、多普勒实时补偿、AI 会话重命名、AI 上下文压缩、AI 自主多步任务模板、AI 工具缺口（set_squelch/bookmark CRUD/recordings list-delete/scan_band） |

> 注：扫频 5 个控制参数同属「扫频完备性」一项簇，AI 工具缺口同属「工具覆盖面」一项簇；上表按可独立排期粒度计数。汇总口径见文末「真差距候选清单」。

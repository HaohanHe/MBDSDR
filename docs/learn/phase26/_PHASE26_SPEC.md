# 第二十六阶段实现规格：能力全面工具化（三通道一致）

> 基线：HEAD = d010600（已推送），ctest 118/118、flutter 325。
> 三通道一致：每个 Agent 工具 = ControlHub 命令 = HTTP POST /command 同 gate；AI gate 手动模式拦截写动作；只读工具两模式均真实执行。
> 现有 14 Agent 工具。勘察：ControlHub 已有 36 命令（含 vfo_*/set_squelch_*/scan_band/get_vfos 等），书签在 ui::BookmarkManager（纯数据类，QSettings "ui/bookmarks"，可被 control 引用）、录音目录 engine recDir_（QSettings rec/dir 默认 "record"）、vfo rename/网络流状态/scan link 实例/色板桥待补。

## 冻结的 21 个新工具/命令（写=入写门；读=两模式真实执行）
| # | 工具名 | 写/读 | 参数 | 后端 |
|---|---|---|---|---|
| 1 | set_network_audio_sink | 写 | enable/port/format | engine setNetworkAudioSink（Phase24 N1）+ 状态成员 |
| 2 | get_network_audio_status | 读 | — | engine 网络流状态 |
| 3 | start_scan_link | 写 | target_freq_hz | engine 持 ScanActivityLink 实例（Phase24 N3）并 start |
| 4 | stop_scan_link | 写 | — | engine stop |
| 5 | get_scan_link_status | 读 | — | ScanActivityLink 状态（scanning/dwelling/hit） |
| 6 | set_squelch | 写 | enabled/threshold_db/auto | engine setSquelchEnabled/Threshold + auto 链路 |
| 7 | get_squelch_status | 读 | — | enabled/threshold/auto/当前 open |
| 8 | list_bookmarks | 读 | — | ui::BookmarkManager（纯数据类，control 可持实例，同 QSettings 键） |
| 9 | add_bookmark | 写 | freq_hz/name/mode | BookmarkManager::add |
| 10 | tune_to_bookmark | 写 | index | tune + 返回真实结果 |
| 11 | delete_bookmark | 写 | index | BookmarkManager::removeAt |
| 12 | list_vfos | 读 | — | engine getVfos |
| 13 | add_vfo | 写 | — | engine vfoAdd |
| 14 | switch_vfo | 写 | index | engine vfoSelect |
| 15 | rename_vfo | 写 | index/name | vfo_manager 补 rename 接口（缺则最小补齐） |
| 16 | list_recordings | 读 | — | 扫 engine recDir_（真实目录，诚实空态） |
| 17 | delete_recording | 写 | name | 删 recDir_ 下文件（谨慎：先确认路径在 recDir_ 内） |
| 18 | export_recording | 写 | name/out_path | 复制到导出路径，诚实返回 |
| 19 | set_fft_params | 写 | fft_size/window/average | engine setFftSize/average/window（已有接口） |
| 20 | set_color_map | 写 | file_path | 写入 QSettings view/wfColormapFile（渲染重染属 UI 持有；headless 持久化偏好，局限如实声明） |
| 21 | get_spectrum_status | 读 | — | fft_size/window/average 真实值 |

## 质量门 / 红线
- 每工具：ControlHub 命令（写入写门 gate）+ Agent exec（写经 llm_worker isWriteTool 拦截）+ tool_schema 声明式规格；**工具清单全同步 14→35**：test_tool_registry（kAllCxxTools/kExpectedWriteTools）、test_tool_schema（计数+逐字 description）、test_ai_real_link（集合+isWriteTool）——精确一致，漏一处即红。
- 每工具带 ctest 真实引擎效应断言（set_squelch 后门限真实改变、add_bookmark 后 QSettings 真实落盘、switch_vfo 后活动点真实切换、set_fft_params 后 fftSize 真实改变、list_recordings 真实目录/空态）；写门拦截断言；坏参诚实报错。
- 干净室 MIT、不复制 GPL；通用非专用；不硬编码（端口/阈值 tokens）；诚实空态；活动参数禁入代码。
- 全量构建 + 全量 ctest（118 基线不回归）全量输出；只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、全量输出、未解决项。

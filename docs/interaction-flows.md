# MBDSDR 交互流程审计（只读）

> 审计对象：`desktop/` 下的 PyQt 专业工作台 + 星象仪。
> 审计口径：按“用户真要走完一件事”的路径审，不按控件审；不评像素布局，不重复 `interaction-audit.md` 的死按钮清单。
> 约束：本文件是本次唯一新增文件；未改代码、测试、CI、`cpp/`。
> 证据约定：`文件:行号` 表示可复核的代码位置；`探针` 表示本次用 offscreen 运行时实测（临时脚本放在系统临时目录，未进仓库）。

## 0. 结论

共审 **7 条路径**，确认 **30 条缺陷**：**阻断 2 / 严重 15 / 一般 10 / 观感 3**。

最先该修的三条：

1. **设备切换不复位旧后端**（B1）：界面说已连新设备，实际数据仍来自旧设备，旧 USB 棒仍被占用。
2. **AI 的“人工确认”是模型自报的 `arm_manual=true`**（S13）：没有真人弹窗，写频率/写寄存器/发射都可被模型一句话放行。
3. **录音与回放的互斥缺失**（S5/S3）：录音中回放会让“录音中”假活、零采样，并把两种状态写进同一个文件。

### 探针环境（均为 offscreen + 无硬件）

```text
QT_QPA_PLATFORM=offscreen
MBDSDR_OFFLINE=1  MBDSDR_NO_HW=1  MBDSDR_NULL_AUDIO=1
APPDATA/HOME/USERPROFILE 指向临时目录；QSettings 几何恢复被探针桩替换
```

探针只做了四件事：用桩后端走真实 `_connect_backend`；建临时 `.iq`+`.json` 走真实回放；用 `QTest.qWait` 推进事件循环；构造真实 `MainWindow` 观察首启/恢复。下文“探针”均指这组结果。

---

## 1. 首次启动与冷启动

### 状态步骤

1. `MainWindow.__init__` 建界面 → `QTimer.singleShot(200, _auto_enumerate_and_connect)`（`desktop/main_window.py:476`）。
2. 左栏默认页是 **天空模式**（`:986`），频谱页 `:1052` 才建；**“未找到 RTL-SDR”引导横幅挂在频谱页内**（`:1021-1030`）。首启看不到。
3. 200ms 后在 GUI 线程同步枚举（`:2510-2533`）；无设备走 `_show_no_rtl_hint`（`:2586-2606`）：横幅 `setVisible(True)`（`:2599`，但父页隐藏）+ 状态栏 8s。
4. `_apply_restored_settings` 恢复频率/模式（`:2404` 起）；**不恢复“上次用的哪台设备”**，`_auto_enumerate_and_connect` 只挑第一条 RTL-SDR/第一条真实设备（`:2533-2573`）。
5. 100ms 后 `_load_gui_config` 恢复观测站并 `self.move(x, y)`（`:6764-6765`），不做屏幕边界校验；它晚于 `restoreGeometry`（`:5615`）。
6. `_restore_app_mode`（`:1750`）在建窗口期间调用（`:1233`）；`_enter_planetarium` 只有 `isVisible()` 才 `showFullScreen`（`:1684`），此时窗口未显示 → 恢复的星象仪是窗口化的。
7. 用户真正连接：设备面板 / 菜单“连接设备” / `Ctrl+C` → `_connect_dialog` → `_connect_backend`（`:2607`）。

### 标记

- **[断] S1**：首启引导在一张看不见的页签上，8 秒后连状态栏也消失；只剩“未连接”。用户只能自己猜“设备”菜单。
- **[陷阱] B2**：外接显示器拔掉后，上次的 `(5000, 400)` 被原样应用，窗口在屏幕外；应用内没有“重置窗口位置”（`_reset_layout` 只重置布局，`:2313` 起）。
- **[陷阱] S2**：`gui_config.json` 半写/损坏 → 只写日志（`:6860-6861`）；退出时 `_save_gui_config` 用内存值直接覆盖（`:6743-6744`），观测站坐标永久变 null。
- **[歧义] M1**：多设备时“自动连接”到底连了谁没有提示；用户以为恢复的是上次那台。
- **[陷阱] M3**：枚举在 GUI 线程做，冷启动窗口已出现但事件循环被枚举阻塞；热插拔每 2s 再枚举一次（`desktop/device_panel.py:181-211`）。
- **[观感] M2**：上次在星象仪全屏退出，下次是窗口化星象仪。

### 探针

- 新建临时 HOME，构造真实 `MainWindow`：`left_tab.currentIndex()=0`，`tabText=天空模式`，`_no_device_hint.isVisible()=False`，连 `isVisibleTo(window)` 都是 `False`。
- 预置 `gui_config.json` 的 `x=5000,y=400`：400ms 后窗口 `pos=(5000,400)`。
- 预置 `app_mode=planetarium`：`_app_mode=planetarium`，`isFullScreen()=False`。
- 预置损坏的 `gui_config.json`：`_observer_lat` 保持 `None`（未恢复），`_save_gui_config()` 后文件里 `observer_lat` 为 `null`，原文件被覆盖。

---

## 2. 主链路：调谐 → 解调 → 录音 → 回放 → 归档

### 状态步骤

1. 调谐 `_on_tune_sdr`（`:3290`）：先改 `freq_label`/`_vfo_center_hz`/`_demod_cfg`，再 `backend.set_frequency`；失败只 `pass`，随后仍 `settings.set("frequency_hz")`（`:3350`）。
2. 解调模式/带宽改 `_demod_cfg` 并下发后端；流水线读同一份配置。
3. 录音 `_start_recording`（`:4734`）：要求后端存在；路径 `~/mbdsdr_recordings/baseband_<秒>.iq`（`:4777-4783`）；目录创建失败静默退系统临时目录（`:4780-4781`）；文件打开失败退**内存缓冲**（`:4794-4798`）。
4. 录制中改采样率 `_on_sample_rate_changed`（`:3515`）：`set_sample_rate` 失败静默（`:3520`）→ `_stop_iq_streams`（`:3535`）→ `_start_iq_streams`（`:3536`）→ 记一条 `_record_rate_changes`（`:3527`）→ 继续写**同一个文件**；`_record_sr` 改成新值（`:3533`）。
5. 录音中回放 `_replay_recording`（`:5824`）：同步 `load_iq`（`:5844`）→ `_stop_iq_streams`（`:5864`）→ 起 `_replay_timer`；**没有先停录音**。
6. 回放结束 `_stop_replay`（`:5927`）→ `_start_iq_streams`（`:5943`）重建流水线，录音继续写同一文件。
7. 停止录音 `_stop_recording`（`:4819`）：流式路径写 sidecar（`:4841-4856`）；`duration_s` 只按最终采样率算；写 sidecar 失败 → `info=None`，最终提示“录制结束（无数据落盘）”（`:4859-4861, :4901`）。
8. 回放入口只有 `QFileDialog`（`:5824` 起），默认目录 `~/mbdsdr_recordings`；没有录音库/最近录制列表。

### 标记

- **[陷阱] S3**：任何流水线重建（改采样率、开始/结束回放、重连）都会执行 `_stop_iq_streams`，它删除 **全部 VFO（含用户新建）**（`:4251-4257`）；`_build_pipeline` 只重建主 VFO + 第二 VFO（`:3974-3981`）。同时把 `_demod_cfg.mode` 用**后端上报的 `status.demod_mode` 覆盖**（`:3968-3970`）。
- **[陷阱] S4**：录音中改采样率 → 一个 `.iq` 里混两种采样率；sidecar 的 `duration_s` 按最后一次速率算（`:4841`）；`sample_rate_changes` 写了（`:4854-4855`）但 `baseband_io.load_iq` 完全不读（`mbdsdr_ai/baseband_io.py:181, 210`）。
- **[断] S5**：录音中开始回放 → `_recording` 仍为 `True`、文件句柄仍开、录音计时器仍走、状态显示“录音中... 00:00”，但流水线已停，采样数冻结；回放期间这一段录像是 **0 字节增长的空档**。
- **[陷阱] S6**：`load_iq` 返回 `iq.tolist()`（`mbdsdr_ai/baseband_io.py:202`），回放在 UI 线程同步执行（`:5844`）。60s@2.048MS/s 的文件要构造 1.2 亿个 Python complex。
- **[陷阱] S7**：流式写盘失败 `pass` 后退回内存（`:4210-4213`）；停止时只要 `fh` 还在，内存缓冲被直接清空（`:4881`）→ 无声丢数据。若一开始磁盘打不开，整场录制都在 RAM 里累计（`:4794-4798`），只有 4s 状态栏提示。
- **[陷阱] S2（跨路径）**：录完只在状态栏显示路径 8s；`control_panel.record_status` 只显示样本数。
- **[观感] L3**：文件名到秒，同一秒内停-录-再录会覆盖同名文件。

### 探针

- 桩后端录音 + 4s 临时 `.iq` 回放：回放中 `recording=True`、`record_timer_active=True`、`record_fh_open=True`、`pipeline_during_replay=False`、样本数 `1622016` 在 600ms 内完全不变，`record_status_text="录音中... 00:00"`。
- 录音中改采样率前用户 VFO 数 2 → 后 1（新建的次听 VFO 被删）。
- 先选 `USB` 再改采样率：`_demod_cfg.mode` 从 `USB` 变回 `FM`，VFO 的 `mode` 同样变回 `FM`。
- 回放一个带 `sample_rate_changes` 的 12 万样本文件：`load_iq` 返回 `sample_rate=2048000`、`duration_s=0.059`，`iq` 是长度 120000 的 Python list。

---

## 3. 设备切换与断开

### 状态步骤

1. 已连接状态下再点“连接”：菜单/`Ctrl+C` 走 `_connect_dialog`（`:2723`）；设备面板走 `_on_panel_device_connect`（`:5331`）。两条路都在 `:2907/:2931/:5342` 直接调 `_connect_backend`，**不先 `_disconnect`**。
2. `_connect_backend` 成功分支：停旧 worker（`:2642`）→ `self._active_sdr_backend = backend`（`:2644`）→ 面板置已连接（`:2645`）→ 下发参数 → `_start_iq_streams`（`:2720`）。
3. `_build_pipeline` 看到 `self._pipeline is not None` 直接 `return`（`:3927-3928`）。
4. USB 拔出：`DevicePanel` 2s 轮询比较 key/locator（`desktop/device_panel.py:181-211`）→ `device_unplugged` → `_disconnect`（`:3193`）。
5. rtl_tcp 掉线：`RtlTcpBackend.read_samples` 超时/OSError 返回 `None`（`mbdsdr_ai/rtltcp_client.py:301-332`），只把内部 health 置 stale；读线程把 `None` 当“暂时没数据”，`msleep(2)` 无限重试（`desktop/receive_pipeline.py:87-94`）。
6. worker（ai-sdr WebSocket）模式：`_connect_real` 在“连接中”就调 `_panels_set_sdr_connected(True)`（`:3041`）；失败/掉线回调 `_on_connection_for_ui(False)` 只改三个标签（`:3255-3266`）。

### 标记

- **[断] B1**：设备切换不复位旧后端/旧流水线。界面显示新设备，频谱/录音/解调仍读旧设备；旧 USB 棒（或旧 rtl_tcp 连接）继续被占用；调新设备无效果。只有后续触发一次流水线重建（改采样率/回放/重连）才会“自愈”到新后端。
- **[严重] S8**：断源（USB 拔、rtl_tcp 断）没有任何“错误”信号进入 UI；`_update_status_bar` 只在收到 IQ 帧的槽里被调（`:4169-4173`），没有帧就永不刷新；桌面端也从不读 backend 的 health/stale（`health` 在 `desktop/` 无引用）。表现是：频谱定格、RSSI/频率长期陈旧、无提示，只能手动断开重连。
- **[严重] S9**：worker 连接失败后控件仍可用（`_on_connection_for_ui(False)` 不调 `_panels_set_sdr_connected(False)`），用户继续调谐/点录音都只会得到假动作。
- **[严重] S15**：`_disconnect` 不停卫星跟踪（见路径 7）。
- **重插恢复**：`_on_device_plugged` 只刷新列表（`:5256` 起），不自动重连；用户需 1 次点击，可接受，不单列缺陷。

### 探针

- 桩后端 A 连接成功后，再 `_connect_backend(B)`：`A.disconnect_called=0`、`A_still_connected=True`、`B_read_calls=0`、`A_read_calls` 持续增长、`backend_is_B=True`、`pipeline_alive=True`。
- 随后改一次采样率：`B_read_calls_after_rebuild` 才开始增长——证明“只有重建才切到新设备”。

---

## 4. 模式切换（专业 ⇄ 天空）

### 状态步骤

1. 进星象仪 `_enter_planetarium`（`:1676`）：reparent 天空视图与频谱，`showFullScreen`；**不打断录音/回放/跟踪**（这三条状态机不感知形态）。
2. 星象仪页只有：天空图、瀑布条、状态条、AI 卡片（`:1502` 起），**没有录音按钮、没有连接状态、没有“正在录音”指示**。
3. 退出 `_exit_planetarium`：reparent 回去 + `showNormal()`（`:1717`）。
4. 启动恢复形态 `_restore_app_mode`（`:1750`，`:1233` 调用）。

### 标记

- **[陷阱] M8**：在专业模式开始录音后切到星象仪，录音继续，但没有任何可见指示/停止入口；用户可能一直以为没在录，或忘了在录。设备掉线同样无提示（状态标签在专业页）。
- **[观感] L1**：退出星象仪统一 `showNormal`，原先的“最大化”状态丢失。
- **[观感] M2（跨路径）**：从星象仪退出重启不会回到全屏。
- **正确行为**：切换不打断回放/跟踪/录音（没有额外的 stop 调用），无需修。

---

## 5. AI 对话与工具执行

### 状态步骤

1. AI 输入框：`_on_submit` 有忙锁，`_is_processing=True` 时直接 return（`desktop/ai_panel.py:621-625`）。
2. 星象仪命令栏：`_on_ai_command`（`:3366-3372`）→ 直接 `ai_panel._call_ai(text)`；`_call_ai` 自身没有忙锁（`ai_panel.py:659-661`）。
3. `_call_ai` 覆盖 `self._worker_thread`/`self._worker`（`ai_panel.py:679-682`）；`_on_ai_finished` 无条件解锁（`ai_panel.py:762-767`）。
4. 工具执行：本地链路走 agent 自己的 `sdr_manager`（`mbdsdr_ai/sdr_tools.py:189`）；硬件链路走 `_mcp_call_handler`（`:3746`）→ `worker.call_tool_sync`；写工具要求 `arm_manual`（`:3757`）。
5. UI 同步：`_on_ai_tool_call` 先 `_sync_ui_for_tool`（`:3799`），再判门（`:3802`）；`_mcp_call_handler` 失败时也先 `emit _ai_tool_done`（`:3771`）再返回错误。
6. 缺参数：`freq_mhz` 缺失回落到 `tokens().AI["demo_freq_mhz"]`（`:3781`）。
7. 结果回填：`_on_ai_finished` 用 `i < executed` 跳过已执行调用（`ai_panel.py:784-787`）。
8. 失败回退：错误以 `[错误] ...` 写回对话卡片（`ai_panel.py:822-826`），但**设备/UI 不回滚**。

### 标记

- **[严重] S10**：星象仪命令栏可绕过忙锁，产生两个并发 AIWorker，互相覆盖引用；先结束的那个提前解锁按钮，另一个还在跑。探针直接调两次 `_on_ai_command`，`_call_ai` 被调用 2 次。
- **[严重] S11**：工具失败或人工门拦下时，UI 已先按“模型想要的频率/音量”改掉；失败信封只进对话，界面与设备不一致。
- **[严重] S12**：模型省略 `freq_mhz` 时 UI 跳到演示频率 98.5MHz，且没有任何“演示值”标记。
- **[严重] S13**：`arm_manual` 是工具 schema 里的普通布尔参数，模型自己就能填 `true`；桌面侧 `main_window.py:3757` 只检查这个值，没有任何真人弹窗/确认令牌。发射工具同样（`mbdsdr_ai/sdr_tools.py:2377` 起，`mbdsdr_ai/tx_guard.py`）。
- **[一般] M6**：`tool_results` 按下标对齐；`ai_steps.steps_from_chat` 明确允许结果缺失（`desktop/ai_steps.py:137-184`），一旦不是前缀对齐，步骤卡会错配，`i < executed` 还可能重复下发已执行调用（写工具若带 `arm_manual=true` 会重复写）。
- **[一般] M7**：AI 执行期间没有任何设备互斥：`_is_processing` 只锁输入框（`ai_panel.py:105/624`），控制面板、断开、录音仍可点；并且 AI 的 `SDRBackendManager` 与桌面 `_active_sdr_backend` 是两个独立所有权（`sdr_tools.py:189`，桌面无该 manager 引用），同一物理设备可能被 AI 再打开一次。

### 探针

- `_sync_ui_for_tool("tune_fm", {})` → 控制面板频率变 `98.5 MHz`；`{"freq_mhz":120}` → `120 MHz`，且当时 `_worker=None`（没有任何硬件调用）。
- 连续两次 `_on_ai_command` → `_call_ai` 调用计数 2。

---

## 6. 误操作与不可逆动作

### 状态步骤

1. 删除录音：**没有录音库/删除入口**，只能去文件管理器删；应用内无确认、无撤销。
2. 覆盖配置：`_save_gui_config` 直接 `open(...,'w')` + `json.dump`（`:6743-6744`，非原子）；`_load_gui_config` 失败只写日志（`:6860-6861`）。
3. 改站址：三套入口——
   - “新时空”面板的纬度/经度输入（`desktop/new_spacetime_panel.py:329-352`）只用于本地 `refresh_passes`（`:503-505`），**不回写主窗口、不落盘、不影响跟踪/天空图**；
   - `gui_config.json` 的 `observer_lat/lon`（只有手工编辑才生效）；
   - GNSS 串口（生效）。
   跟踪被拒时提示“在 gui_config.json 配置 observer_lat/lon”（`:6109`）。
4. 发射：桌面 UI 没有发射按钮；AI 侧 `sdr_transmit_cw` 的门是 `arm_manual`（见 S13）。
5. 撤销：`_execute_ui_action` 会返回 inverse（`:1255` 起），但**没有撤销栈**，没有 `Ctrl+Z`；注释里的“撤销栈”不存在。

### 标记

- **[严重] S2（跨路径）**：覆盖配置 = 一次半写就让站址永久丢失。
- **[严重] S14**：站址三源不一致；用户在界面上改的站址不生效且不落盘，唯一提示还让他去改 JSON。
- **[严重] S13（跨路径）**：发射/写设备没有真人确认。
- **[一般] M10**：AI 改频谱/音量/模式后无法撤销，只能手工改回。

---

## 7. 退出与崩溃

### 状态步骤

1. `closeEvent`（`:6645`）→ `_save_gui_config`（`:6698`）→ `settings.flush()` → `_disconnect()`（`:3193`）→ `_release_runtime_resources`。
2. `_disconnect` 顺序：`_stop_replay`（`:3201`）→ 扫频停 → `_stop_iq_streams`（`:3217`）→ 停录音并写 sidecar（`:3227`）→ `backend.disconnect`（`:3232`）。
3. 进程被杀：录音流式文件已落盘，但 sidecar 只在 `_stop_recording` 写（`:4856`）；下次启动无检测/清理。
4. 重启：`DesktopSettings` 原子写（`desktop/settings.py:208-218`，先 `.tmp` 再 `os.replace`），`gui_config.json` 不是（见 S2）。

### 标记

- **[严重] S15**：`_disconnect` 只停回放/扫频/IQ/录音/后端，**没有 `_stop_satellite_tracking()`**（`:3193-3233`；该函数定义在 `:6290` 附近）。拔出设备后 `_sat_track_timer` 仍每 500ms 触发 `_poll_satellite_track`（`:6154`）→ `_on_tune_sdr`（`:6195`），后者在无后端时照样改 UI 频率并 `settings.set("frequency_hz")`（`:3350`）。探针：`_disconnect()` 后 `timer.isActive()=True`、`_active_sdr_backend=None`。
- **[一般] M5**：崩溃残留的 `.iq` 无 sidecar，`load_iq` 按默认 `2.4e6` 回放（`baseband_io.py:181`），频率/速率全错；无“发现未完成录音”的启动提示。
- **[观感] L2**：关闭时正在录音没有确认，静默停录并保存（对“用户其实还想继续”是误操作）。
- **[观感] 退出顺序**：`_disconnect` 里 `_stop_replay` 会先 `_start_iq_streams`（`:5943`）重启流水线，紧接着 `:3217` 又停一次；退出瞬间多做一次开/停设备，拖慢退出。

---

# 缺陷清单

> 一条一行。`侧` 用于分派：界面层 / 后端 / 契约层。标“未验证”的只有纯推断项。

## 阻断（2）

| ID | 路径 | 现象 | 期望 | 证据 | 严重度 | 建议修法 / 侧 |
|---|---|---|---|---|---|---|
| B1 | P3 设备切换 | 再连接新设备时不先断开旧后端，`_pipeline` 已存在直接 return；界面显示新设备，数据仍来自旧设备，旧设备仍被占用 | 连接入口统一先 `_disconnect`（或 `_connect_backend` 开头复用断开流程），确认 pipeline/worker 全停后再换后端 | `desktop/main_window.py:2644, 2720, 3927-3928, 2907, 2931, 5342`；探针 `A.disconnect_called=0, B_read_calls=0, A_read_calls=继续增长` | 阻断 | 界面层：`_connect_backend` 第一步断开现有后端与流水线 |
| B2 | P1 冷启动恢复 | 上次窗口坐标不校验屏幕，重启后窗口可能在屏幕外；应用内无“重置窗口位置” | `move` 前用 `QGuiApplication.screens()` 校验可见矩形；越界则居中/回主屏；提供“重置窗口位置”菜单 | `desktop/main_window.py:6764-6765, 5615`；探针 `x=5000,y=400` 原样应用；`:2313` 重置布局不含窗口位置 | 阻断 | 界面层 |

## 严重（15）

| ID | 路径 | 现象 | 期望 | 证据 | 严重度 | 建议修法 / 侧 |
|---|---|---|---|---|---|---|
| S1 | P1 首启 | 引导横幅挂在“频谱”页（第 3 个页签），首启当前页是“天空模式”；横幅 `isVisible=false`，状态栏提示 8s 后消失 | 首启把横幅挂到当前页，或自动切到“频谱”并保持横幅常驻 | `:986, :1052, :1021-1030, :2599, :2586-2606`；探针 `current=天空模式, hint.isVisible=false, isVisibleTo=false` | 严重 | 界面层 |
| S2 | P1/P6/P7 | `gui_config.json` 损坏只写日志；退出时用内存默认值直接覆盖原文件；写盘非原子 | 原子写（tmp+replace）；损坏时备份并提示用户；不要用默认值静默覆盖 | `:6860-6861, :6743-6744`；探针损坏后 `observer_lat=None`，保存后文件中为 `null` | 严重 | 后端/契约层（配置读写）+ 界面层提示 |
| S3 | P2 主链路 | 任何流水线重建都会删掉全部 VFO（含用户新建），并把解调模式用后端 `status.demod_mode` 覆盖 | 重建只清运行时绑定、保留用户 VFO 目录；模式以用户设置为准，后端只读 | `:4251-4257, :3974-3981, :3968-3970`；探针 VFO 2→1，`USB`→`FM` | 严重 | 界面层 + 契约层（VFO/模式所有权） |
| S4 | P2 录音 | 录音中改采样率后，同一 `.iq` 混两种速率；`duration_s` 按末次速率算；`load_iq` 忽略 `sample_rate_changes` | 改率时切新文件（分段落盘）或禁止录音中改率；sidecar 若保留 changes，读取端必须按段处理 | `:3515, :3520, :3527, :3533, :3535-3536, :4841, :4854-4855`；`mbdsdr_ai/baseband_io.py:181, 210` | 严重 | 界面层（状态互斥）+ 契约层（sidecar 语义） |
| S5 | P2 录音/回放 | 录音中开始回放：录音状态仍“录音中”、句柄仍开、计时仍走，但采样冻结；回放结束可能继续往同一文件写 | 录音与回放互斥：开始回放自动停录并提示；回放结束不自动续录 | `:5824-5864`（无 `_recording` 检查）, `:5943`；探针 `recording=true, pipeline=None, samples=1622016 不变` | 严重 | 界面层（状态机） |
| S6 | P2 回放 | `load_iq` 返回 `.tolist()`，长录音在 UI 线程同步加载 → 卡死/OOM | 返回 numpy 数组，或分块流式回放；加载放后台线程 | `:5844`；`baseband_io.py:202`；探针 12 万样本即返回长度 120000 的 list（长文件按此线性放大，推断） | 严重 | 后端 |
| S7 | P2 录音 | 流式写盘失败静默退内存；停止时内存缓冲被丢弃；磁盘打不开时整场录制在 RAM 累计，仅 4s 提示 | 写失败要报错并停止录音（保留已写部分）；磁盘不可用直接拒绝开始 | `:4210-4213, :4881, :4794-4798` | 严重 | 后端 + 界面层（错误提示） |
| S8 | P3 断源 | USB 拔/rtl_tcp 断后读线程无限重试；UI 没有错误信号；状态栏只在有帧时刷新 | 读线程上报连续失败；UI 定时轮询 backend health/stale；断源进“已断开”并保留错误说明 | `receive_pipeline.py:87-94`；`rtltcp_client.py:301-332`；`:4169-4173`；`desktop/` 无 health 引用 | 严重 | 后端（错误通道）+ 界面层（订阅/轮询） |
| S9 | P3 worker 模式 | 连接中即启用控件；连接失败/掉线只改标签，不禁用控件、不清频谱 | 失败/掉线调 `_panels_set_sdr_connected(False)` 并给出重试入口 | `:3041, :3255-3266, :3070` | 严重 | 界面层 |
| S10 | P5 AI | 星象仪命令栏直接 `_call_ai`，绕过 `_is_processing`；可并发两个 worker，先结束者提前解锁 | `_call_ai` 自身加忙锁；或命令栏统一走 `_on_submit` | `:3366-3372`；`ai_panel.py:621-625, 659-661, 679-682, 762-767`；探针两次命令 → 2 次 `_call_ai` | 严重 | 界面层 |
| S11 | P5 AI | 工具失败/被门拦时 UI 已先改频率/音量/模式；失败只进对话 | 先执行、成功后同步 UI；失败回滚或至少不预改 | `:3799, :3802, :3771-3776`；探针无 worker 时 UI 仍跳到 120MHz | 严重 | 界面层 |
| S12 | P5 AI | 缺 `freq_mhz` 时 UI 落到演示频率 98.5MHz，无“演示”标记 | schema 必填/缺参直接报错；禁止演示值进入 UI | `:3781`；探针 `after_empty_params=98500000.0` | 严重 | 界面层 + 契约层（schema） |
| S13 | P5/P6 AI/发射 | “人工确认”是模型自带 `arm_manual=true`，桌面无真人确认；发射工具同门 | 由 UI 生成一次性确认令牌（用户点击后下发），模型只能申请、不能宣称 | `mcp_protocol.py:78-130`；`main_window.py:3757, :3802`；`sdr_tools.py:2377` 起；`tx_guard.py` | 严重 | 契约层 + 界面层（确认弹窗） |
| S14 | P6 站址 | 新时空面板经纬度只供本地过境计算，不回写主窗口/不落盘；跟踪仍用 gui_config/GNSS；被拒提示让用户改 JSON | 站址单一数据源：面板编辑即生效、即落盘、即刷新天空/跟踪；或直接移除该输入 | `new_spacetime_panel.py:329-352, 503-505`；`main_window.py:1154, 4312, 6106-6110`；`:6109` | 严重 | 界面层 + 契约层（单一站址源） |
| S15 | P7 断开 | `_disconnect` 不停卫星跟踪；定时器继续 `_on_tune_sdr`，写 `frequency_hz`，下次启动频率被多普勒值覆盖 | `_disconnect` 调 `_stop_satellite_tracking`；跟踪状态随连接状态联动 | `:3193-3233`（无 stop tracking）, `:6154-6195, :3350`；探针 `timer_active=true, backend=None` | 严重 | 界面层 |

## 一般（10）

| ID | 路径 | 现象 | 期望 | 证据 | 严重度 | 建议修法 / 侧 |
|---|---|---|---|---|---|---|
| M1 | P1 多设备 | 不保存“上次用的设备”；启动只挑第一条 RTL-SDR/第一条真实设备，无提示 | settings 保存设备 key，恢复失败时明确提示并打开选择框 | `:2510-2573`；settings 键列表无设备标识 | 一般 | 界面层 + 后端 |
| M2 | P1/P4 形态 | 恢复星象仪时不进全屏（建窗口阶段 `isVisible()=false`） | 恢复形态后 `show()` 时补 `showFullScreen` | `:1233, :1750, :1684`；探针 `app_mode=planetarium, isFullScreen=false` | 一般 | 界面层 |
| M3 | P1/P3 冷启动/热插拔 | 枚举在 GUI 线程同步执行；热插拔每 2s 再枚举 | 枚举移到后台线程，结果回调 UI | `:2510-2533`；`device_panel.py:181-211`（具体阻塞时长未验证） | 一般 | 后端/界面层 |
| M4 | P2 归档 | 录完路径只在状态栏 8s；无录音库/最近列表；sidecar 写失败却说“无数据落盘” | 持久录音列表 + “打开目录”；sidecar 失败要说明“文件在、元数据缺” | `:4891-4906, :4901, :4859-4861`；回放入口 `QFileDialog` `:5824` 起 | 一般 | 界面层 |
| M5 | P7 崩溃 | 强杀后残留无 sidecar 的 `.iq`，回放按默认 2.4MS/s；无检测/清理 | 启动扫描最近未完成录音并提示；sidecar 缺失时要求用户确认速率 | `baseband_io.py:181`；`_stop_recording` 才写 sidecar `:4856` | 一般 | 后端 + 界面层 |
| M6 | P5 AI | `tool_results` 按下标对齐，结果缺失时步骤卡错位、可能重复下发已执行调用 | 用 `call_id` 集合判定“已执行”，而不是 `len(results)` 下标 | `ai_panel.py:784-787`；`ai_steps.py:137-184` | 一般 | 契约层 |
| M7 | P5 AI | AI 执行期间无设备互斥；AI `SDRBackendManager` 与桌面 backend 两个所有权 | 桌面与 AI 共用同一 backend 单例；或加设备操作锁（写操作串行） | `ai_panel.py:105/624`；`sdr_tools.py:189`；`main_window.py:2644` | 一般 | 契约层 + 界面层 |
| M8 | P4 星象仪 | 星象仪无录音/连接状态指示，录音在不可见状态下继续 | 星象仪状态条显示录音红点与连接状态；提供停止入口 | `:1502` 起（页面仅天空/瀑布/状态条/AI 卡）；`:1684` | 一般 | 界面层 |
| M9 | P2 回放 | 声卡 `start()` 失败被 `pass`，回放无声也无提示 | 失败要在状态栏/对话框明确告知 | `:3869-3876, :5868-5872` | 一般 | 界面层 |
| M10 | P6 AI 撤销 | `ui_action` 返回 inverse 但无撤销栈、无 Ctrl+Z | 实现撤销栈或删除“撤销”暗示；至少给“恢复默认布局” | `:1255` 起（仅返回 inverse） | 一般 | 界面层 |

## 观感（3）

| ID | 路径 | 现象 | 期望 | 证据 | 严重度 | 建议修法 / 侧 |
|---|---|---|---|---|---|---|
| L1 | P4 退出星象仪 | `showNormal()` 统一处理，丢失之前的最大化状态 | 记录进入前窗口状态，退出时还原 | `:1717` | 观感 | 界面层 |
| L2 | P7 关闭 | 正在录音时关窗无确认，静默停止并保存 | 询问“正在录音，退出将停止录音？” | `:6645` 起；`:3227` | 观感 | 界面层 |
| L3 | P2 录音命名 | 文件名精确到秒，同秒二次录音会覆盖 | 加序号或毫秒 | `:4782-4783` | 观感 | 后端 |

---

# 按严重度排序的修复建议（含分派）

> 顺序按“会造成错误数据/不可恢复状态”优先，而不是按代码改动量。

## 阻断

1. **B1 设备切换**（界面层）：把“断开现有后端/停 pipeline”抽成 `_connect_backend` 的第一步；所有入口（`_connect_dialog`、`_on_panel_device_connect`、自动连接）都只走这一条。验收：旧后端 `disconnect` 必被调用，`_build_pipeline` 不再因旧 pipeline 提前 return。
2. **B2 窗口越界**（界面层）：`_load_gui_config` 的 `move` 前置屏幕校验；另加“重置窗口位置”菜单项。验收：`x=5000` 时窗口出现在主屏可见区。

## 严重（按修复收益排序）

3. **S13 AI 人工门**（契约层 + 界面层）：写工具改为“UI 侧确认令牌”：模型调用只产生待确认项，界面弹窗后下发一次性 token；`arm_manual` 不再由模型参数决定。TX 同门。
4. **S5 + S3 音频状态机**（界面层）：录音/回放互斥；`_stop_iq_streams` 不再删除用户 VFO；`_build_pipeline` 不覆盖 `_demod_cfg.mode`。
5. **S4 录音中改率**（界面层 + 契约层）：录音中改采样率自动切新文件段，或禁用该控件并说明；读取端按 `sample_rate_changes` 分段。
6. **S8 断源感知**（后端 + 界面层）：读线程连续失败 → 发 error/stale 信号；UI 1s 轮询 backend health，进入“已断开（原因）”并保留重连按钮。
7. **S11 + S12 AI UI 同步**（界面层）：删除“执行前预改 UI”；`freq_mhz` 设为必填，禁止演示值回落。
8. **S2 配置原子写/损坏保护**（后端 + 界面层）：`gui_config` 改 tmp+replace；损坏时备份 `.bak` 并提示；退出前若加载失败则不覆盖。
9. **S7 落盘失败**（后端 + 界面层）：写失败立即停录、报错、保留已写部分；磁盘不可用直接拒绝开始；删除内存兜底。
10. **S6 长录音回放**（后端）：`load_iq` 返回 numpy；回放改分块/后台线程。
11. **S10 AI 并发**（界面层）：`_call_ai` 加忙锁并校验 sender，命令栏统一走 `_on_submit`。
12. **S9 worker 失败态**（界面层）：`_on_connection_for_ui(False)` 必须调 `_panels_set_sdr_connected(False)`。
13. **S1 首启引导**（界面层）：首启切到“频谱”或把横幅挂到当前页，且不依赖 8s 状态栏。
14. **S14 站址单一源**（界面层 + 契约层）：新时空面板编辑直接写 settings/gui_config 并刷新天空/跟踪；跟踪被拒提示改为指向该面板。
15. **S15 断开停跟踪**（界面层）：`_disconnect` 调 `_stop_satellite_tracking`；跟踪启动也要求后端存在（或明确“仅规划模式”）。

## 一般

16. **M6 结果对齐**（契约层）：改用 `call_id`。
17. **M7 设备所有权**（契约层）：桌面与 AI 共用 backend 单例；加设备写锁。
18. **M1 上次设备**（界面层 + 后端）：settings 存 device key + label，恢复失败提示。
19. **M5 未完成录音恢复**（后端 + 界面层）：启动扫描并提示。
20. **M4 归档入口**（界面层）：最近录音列表 + 打开目录；sidecar 失败文案修正。
21. **M2 / L1 窗口状态**（界面层）：记录/还原 fullscreen/maximized。
22. **M8 星象仪状态条**（界面层）：录音红点 + 连接状态。
23. **M9 声卡失败提示**（界面层）。
24. **M3 枚举后台化**（后端/界面层）。
25. **M10 撤销**（界面层）：实现或移除暗示。

## 观感

26. **L2 关闭确认**（界面层）。
27. **L3 录音命名**（后端）。

---

## 附：未验证/边界说明

- **S6 的“长录音 OOM”**：用 12 万样本验证了 `.tolist()` 路径本身；60s@2.048MS/s 的内存量按线性推算，未在真机跑大文件。
- **M3 的枚举阻塞时长**：代码路径确定在 GUI 线程，但未在装有 SoapySDR/多设备的 Windows 上计时。
- **S8 的 rtl_tcp 实机断线**：未起真实 rtl_tcp 服务；依据是 `read_samples` 返回 `None` + 读线程无限重试 + 桌面端无 health 消费三处代码。
- **S13 的发射**：桌面 UI 无发射入口；结论针对 AI 工具 `sdr_transmit_cw` 的 `arm_manual` 门。
- 删除录音：应用内没有录音库/删除入口，因此不存在“删除确认/撤销”可审；已如实记录，不编造缺陷。

---

## 追加：书签脏数据撑爆控制面板（2026-11-04）

### 现象（硬证据）
`~/.mbdsdr/bookmarks.json` 中一条 **100012 字符** 的书签名会让
`bookmark_combo.minimumSizeHint` 变成 **1,300,208px**，控制面板被撑到
1,300,272×1010；`freq_display.grab()` 得到 1,300,208×60 的图，按固定宽度归一化后
高度只剩 **1px**（320x1 vs 期望 320x20），5 张 UI 基线因此长期失真。该现象在修复前
的探针里可复现（默认 `QComboBox` / `AdjustToContents` 均为 1,200,208px 量级）。

### 约束取值依据
书签名上限 **64 字符**：常见台名/中继名（呼号+频点+亚音，如
`BG5ABC 439.750 -5 88.5`）约 24 字符，64 留出地名/用途备注余量，同时保证在
240px 宽度内可读（按字宽省略）。

### 修复
- 数据层：新输入超过 64 字符 → 弹窗拒绝并说明，**不静默截断、不写盘**。
- 显示层：下拉框 `AdjustToMinimumContentsLengthWithIcon` + `minimumContentsLength=16`
  + 水平 `QSizePolicy.Ignored`；item 文本用 `QFontMetrics.elidedText` 省略到 ≤240px。
  三重保证控件尺寸不随提示文本的 `minimumSizeHint` 无限增长。
- 历史脏数据：**不删除、不改写用户文件**；显示时截到 64 字符 + “…”并加 `⚠` 前缀，
  item tooltip 写明“原 N 字符，超过上限，显示已截断；原始数据未改动”，下拉框整体
  tooltip 汇总异常条数。仅显示层裁剪，是否清理由用户决定，程序不自动改写。

### 极端脏数据用例（`tests/test_bookmark_dirty_data.py`）
- 喂入 100,012 字符书签名后：控制面板 `minimumSizeHint` 宽度不增长；
  `bookmark_combo.minimumSizeHint().width()` 242px（阈值断言 ≤400）；
  `freq_display.grab()` 宽不增长、高 ≥20px（不再退化为 1px）。
- 原始 `_bookmarks[0][1]` 仍为完整 100012 字符（未删/未改）。
- 新输入 200 字符 → 拒绝写入且出现提示。
- 未改动用户 `bookmarks.json`（测试全部在内存中构造数据）。


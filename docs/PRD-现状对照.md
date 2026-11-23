# PRD 现状对照（P0 → 仓库现状 → 工单）

> 复核时点：**2026-11-23** · 基准 HEAD：**3d26432e** · 口径：`MBDSDR_OFFLINE=1` 软件事实；真机项另标 `not_verified`。
> 本版由两个独立复核子 agent 逐条对当前代码重定位（行号 + 字面量 + blame），主 agent 抽查决定性锚点（B2/S1/S12/S14/M4/M10）后汇总。上一版为 2026-11-04 快照（E4），数字与行号已全面刷新。
> 状态列属 B 类完成断言（判据见 [evidence-standard.md](evidence-standard.md) §7.5），逐条绑四元组 `<结论> — <hash> — <path>:<line> — <字面量>`，基准取该行 blame（§7.7）；位置/对照列属 A 类，给可点开锚点。
> 行号随提交漂移，以**函数名/符号**为语义锚点。测量数字的证据见 §6 复跑输出（E2；日志在仓库外 `D:/mbdsdr/output/refresh_20261123/`）。
> 配套 [PRD.md](PRD.md)。本表回答三件事：**每条 P0 当前做到哪一步、证据在哪、差的那段谁接。**

## 0. 判读口径与统计

| 维度 | 取值 | 定义 |
|---|---|---|
| 实现状态 | **有** | 端到端实现，产品定义上无缺口（已知缺陷不改变该条定义） |
| | **半有** | 有主要实现，但存在**定义级缺口**：未接线 / 单边状态 / 两套来源 / 时间基准分叉 / 严重缺陷导致验收不成立 |
| | **无** | 未实现 |
| 证据强度 | 真机真验 | 有真机、真实样本或第三方真值背书 |
| | 外部真值 | 外采官方样本/第三方清单 |
| | 仅自测 | 只有本仓合成 fixture、假后端或单测自洽 |
| | 未验证 | 本轮环境物理上做不到（无硬件/无过境/无 key/无网络） |

统计：**P0 共 11 条 —— 有 9、半有 2、无 0。**

| 实现状态 | 条目 |
|---|---|
| 有（9） | P0-01 双形态、P0-02 全星表天空、P0-03 时间引擎、P0-04 双环锁定、P0-05 站址单一来源、P0-07 录音/回放/录音库、P0-09 收星闭环、P0-10 诚实性（H-3 未逐条复核，带星号）、P0-11 动态 UI 边界 |
| 半有（2） | P0-06 SDR 基本功（S1/S9 修复在位，调谐语义待真机）、P0-08 AI 协商真模型（真 provider 未联调） |
| 无（0） | — |

**与 2026-11-04 版的判定变化**

| P0 | 变化 | 依据（当前锚点） |
|---|---|---|
| P0-05 | 半有 → **有** | S14 收口：站址唯一写入口 `set_observer_site`（[main_window.py:14706](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L14706)），面板只读且已 unregister，AI Observer 同步回灌（`b263f3be`） |
| P0-09 | 半有 → **有** | S10 忙锁统一、S11 两链均成功后同步、S12 缺参拒绝、S13 真人门 + 审计全在位（§2 对应行四元组） |
| P0-10 | 半有 → **有\*** | S2 原子写 + 损坏备份拒写、S12 收口；H-3 未逐条复核，保留星号 |
| P0-11 | 半有 → **有** | 比例校验与 `parse_layout_ratio` 共用解析（旧 `int()` 直转路径不再存在）；撤销栈 + `ui_layout_undo` 工具在位（桌面无 Ctrl+Z，见 M10） |
| P0-07 | 半有 → **有** | M4 录音库对话框（列出/回放/移入回收站）与 M5 启动残留扫描都在位；S4/S6/S7/L3 早已收口（§2 第 19/20 行） |
| P0-06/08 | 仍半有，缺口收窄 | P0-06 的 B1/S8/S9 在位，调谐语义待真机；P0-08 真 provider 未联调 |

---

## 1. P0 逐条对照（证据均可点开）

| ID | P0 功能 | 状态 | 证据强度 | 证据锚点（当前行号） | 缺口 / 收口判据 |
|---|---|---|---|---|---|
| P0-01 | 双形态（星象仪 / 工作台） | 有 | 仅自测 | 壳构建 [main_window.py:2836](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L2836)；进入 `_enter_planetarium`:3746；退出 `_exit_planetarium`:3892；独立天空窗全屏 `_detach_planetarium_page`:3856 | B2/M2/L1 修复在位（§2） |
| P0-02 | 全星表天空视图与视场 LOD | 有 | 仅自测（数据层有外部样本对拍） | 数据层 [star_catalog.py:729](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/skyengine/star_catalog.py#L729) `select_for_view`；UI 接线 [rf_sky_view.py:5807](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L5807) `_star_selection`，绘制/拾取共用同一星集合 | 补 AC-02.1 逐星像素对拍 |
| P0-03 | 时间引擎与可控时轴 | 有 | 仅自测 | 引擎 [new_spacetime.py:121](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/new_spacetime.py#L121) `TimeEngine`（`set_rate`:177）；倍率接线 [rf_sky_view.py:10727](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L10727) `_set_rate` | 保持 §6 天空三件绿 |
| P0-04 | 双环锁定 | 有 | 仅自测 | 四组合文案 [rf_sky_view.py:2807](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L2807) `loop_state_lines`；站址失效:3338；断连先停跟踪 [main_window.py:7853](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L7853) | 补断连探针回归 |
| P0-05 | 观测站单一来源 | **有** | 真机真验（GNSS 定位段） | 唯一写入口 [main_window.py:14706](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L14706) `set_observer_site`；AI 同步 `_sync_observer_to_agent`:14849；面板只读 [new_spacetime_panel.py:490](file:///D:/mbdsdr/MBDSDR/desktop/new_spacetime_panel.py#L490) | S14 收口 — `686661cc` — desktop/main_window.py:14706 — `set_observer_site`（基准 `686661cc`）；清空写 None 不填 (0,0)。**2026-11-23 真机**：COM10@9600 实 fix（17–19 星，GSV `BD/GP`、GSA `GN`）→ `_observer_source="gnss"`、天空视图与主窗同址；AI 未建 agent（无 API Key）故未比对 agent 坐标 |
| P0-06 | SDR 工作台基本功 | **半有** | 仅自测（无真机） | 调谐语义 [spectrum_widget.py:968](file:///D:/mbdsdr/MBDSDR/desktop/spectrum_widget.py#L968) `_view_center_hz`、`_tune_click`:4167、`_tune_peak`:4193；断源上报 [main_window.py:9922](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L9922) `_on_pipeline_read_failed` | B1/S8/S9 修复在位；调谐/刻度待真机回归 |
| P0-07 | 录音、回放、录音库 | **有** | 仅自测 | 录音 [main_window.py:11296](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L11296) `_start_recording`；回放 `_replay_selected_path`；`load_iq` [baseband_io.py:397](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/baseband_io.py#L397) numpy 直返；录音库 [recording_library_dialog.py](file:///D:/mbdsdr/MBDSDR/desktop/recording_library_dialog.py)（列出/回放/移入回收站）+ `trash_entry`；M5 启动扫描 | S4/S6/S7/L3/M4/M5 全部在位（§2）；真机长时间录制/崩溃恢复回归未做 |
| P0-08 | AI 能力协商 | **半有** | 仅自测（假模型/规则替身） | 账本 [sky_negotiation.py:37](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sky_negotiation.py#L37) `CAPABILITY_SLOTS`、`CapabilityLedger`:199；防问卷文案:383/402（「不是问卷、不是固定流程」「不要生成表单」） | 真 provider 未联调；AC-08.5 阈值待拍板 |
| P0-09 | 一句话收星闭环 | **有** | 仅自测 | 双总线 [main_window.py:2552](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L2552)/2629；真人门 `_request_human_approval`:9343；GATE_PARAM 剥离:9383/9477；S12 缺参拒绝:9384 | S10/S11/S12/S13 修复在位（§2 四元组）；真机闭环演练未做 |
| P0-10 | 真实性与诚实性基线 | **有\*** | 部分真机 + 部分仅自测 | 错误目录 [error-catalog.md](file:///D:/mbdsdr/MBDSDR/docs/error-catalog.md)；离线 A/B [offline-behavior.md](file:///D:/mbdsdr/MBDSDR/docs/offline-behavior.md)；原子写 [main_window.py:16295](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L16295) `_save_gui_config` | S2/S12 收口；H-3 未逐条复核（星号）；H-8 时间基准已统一（§5 P1-02） |
| P0-11 | 动态 UI 边界 | **有** | 仅自测 | 比例校验 [main_window.py:2609](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L2609)；解析 [ui_actions.py:37](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/ui_actions.py#L37) `parse_layout_ratio`；撤销栈:60 `_undo_stack`；工具 [agent.py:2806](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/agent.py#L2806) `ui_layout_undo` | 比例非法返回 `PARAM_ERROR`；撤销栈在位但桌面无 Ctrl+Z（M10 部分） |

> **§2.4 战略节证据**：[PRD.md §2.4](PRD.md) 的体验基准为仓库外产品工程 dist（自包含 HTML）；原型演示项与「不许照抄」清单以 PRD 该节为准。

---

## 2. 23 条流程缺陷 → P0 → 专线 → 完成判据（无孤儿）

> 专线取值：**接入层**、**UI频谱**、**操作逻辑**、**天空**、**工具审计**、**数据**。
> 23 = B1/B2 + S1–S15 + M2/M4/M5/M10 + L1/L3。本轮复核：**修复在位 22 / 仍待修 0 / 部分 1（M10）**。
> 「修复在位」= 当前代码含该修复实现（四元组可点开），**不等于真机回归通过**；回归口径见 §6。

| # | P0 | 缺陷 | 专线 | 完成判据（DoD） | 现状 | 证据（四元组，基准取 blame） |
|---|---|---|---|---|---|---|
| 1 | P0-06 | B1 换设备不复位旧后端 | 接入层 | 切换时旧后端 disconnect 必被调用、旧读停止、新设备真实出数后才显示已连接；失败回滚 | 修复在位 | 修复在位 — `7f518fe6` — desktop/main_window.py:7858 — `self._active_sdr_backend.disconnect()`（基准 `477cf44b`） |
| 2 | P0-01 | B2 窗口恢复可能到屏外 | 操作逻辑 | 启动位置落在任一屏可见区；越界回主屏；菜单提供重置窗口位置 | 修复在位 | 修复在位 — `2579e284` — desktop/main_window.py:6310 — `def _on_reset_window_position`（基准 `2579e284`） |
| 3 | P0-06 | S1 首启引导挂不可见页 | UI频谱 | 首启引导出现在当前可见页，含连接入口；未连接横幅常驻 | 修复在位 | 修复在位 — `2579e284` — desktop/main_window.py:6782 — `lt.setCurrentWidget(page)`（基准 `2579e284`） |
| 4 | P0-10 | S2 配置损坏静默覆盖 | 数据 | 损坏先备份并提示；保存原子写；损坏且备份失败时跳过保存 | 修复在位 | 修复在位 — `7f518fe6` — desktop/main_window.py:16295 — `os.replace(tmp_path, config_file)`（基准 `7f518fe6`） |
| 5 | P0-06 | S3 重建删用户 VFO/覆盖解调 | 操作逻辑 | 重建后用户 VFO 数量不变；解调保持用户选择 | 修复在位 | 修复在位 — `7f518fe6` — desktop/main_window.py:9834 — `s1 = self._vfo_mgr.get(self._main_vfo_id)`（基准 `7f518fe6`） |
| 6 | P0-07 | S4 录音中改率混流 | 数据 | 改率生成新文件段；读取端按 segments 分段；duration 自洽 | 修复在位 | 修复在位 — `a4f65611` — desktop/main_window.py:8782 — `info = self._finalize_record_segment()`（基准 `a4f65611`） |
| 7 | P0-07 | S5 录音/回放不互斥 | 操作逻辑 | 回放前停录落盘；录音前停回放；不假活、不自动续录 | 修复在位 | 修复在位 — `7f518fe6` — desktop/main_window.py:14236 — `self._apply_recording(False)`（基准 `7f518fe6`） |
| 8 | P0-07 | S6 长录音同步 .tolist() | 数据 | 回放不阻塞 UI（后台读盘）；load_iq 不返回 Python list | 修复在位 | 修复在位 — `815ec729` — mbdsdr_ai/baseband_io.py:473 — `"iq": iq,`（基准 `815ec729`） |
| 9 | P0-07 | S7 写盘失败静默退内存 | 数据 | 磁盘不可用直接拒绝开始；写失败停录并报已写字节 | 修复在位 | 修复在位 — `d0b585a0` — desktop/main_window.py:10935 — `_report_record_abort`（基准 `d0b585a0`） |
| 10 | P0-06 | S8 断源无上报、频谱定格 | 接入层 | 断源短时内 UI 显示已断开（原因）；频谱停止清帧；给重连入口；不无限重试假活 | 修复在位 | 修复在位 — `7f518fe6` — desktop/receive_pipeline.py:133 — `STALL_TIMEOUT_S = 1.0`（基准 `7f518fe6`） |
| 11 | P0-06 | S9 连接失败只改标签 | 接入层 | 失败禁用调谐/录音/收听等依赖控件并显示原因；恢复后解禁 | 修复在位 | 修复在位 — `95087ccc` — desktop/main_window.py:7642 — `cp.set_connected(connected)`（基准 `95087ccc`） |
| 12 | P0-09 | S10 命令条绕过忙锁 | 工具审计 | 命令条与 AI 面板共享忙锁；连发两条只执行一条 | 修复在位 | 修复在位 — `2579e284` — desktop/main_window.py:8422 — `if self.ai_panel._call_ai(text) is False:`（基准 `2579e284`） |
| 13 | P0-09 | S11 失败前 UI 先行变更 | 操作逻辑 | 工具失败时频率/模式/音量不变；先执行、成功后同步 UI | 修复在位 | 修复在位 — `742d485e` — desktop/main_window.py:9489 — `self._pending_ui_sync[tool_name] = params`（基准 `742d485e`） |
| 14 | P0-10 | S12 缺参落到演示频率 | 工具审计 | 缺 freq_mhz 直接报错；UI 不变；不接受演示频率 | 修复在位 | 修复在位 — `742d485e` — desktop/main_window.py:9384 — `args.get("freq_mhz")`（基准 `742d485e`） |
| 15 | P0-09 | S13 人工门是模型自报 | 工具审计 | 入口剥离 arm_manual/confirmed；真人弹窗确认后才下发；超时=拒绝；全程审计 | 修复在位 | 修复在位 — `7f518fe6` — desktop/main_window.py:9383 — `args.pop(GATE_PARAM, None)`（基准 `7f518fe6`） |
| 16 | P0-05 | S14 站址两套来源 | 天空 | 面板输入回写唯一事实源并落盘；天空/过境/AI 同刻 az/el 一致 | 修复在位 | 修复在位 — `686661cc` — desktop/main_window.py:14706 — `set_observer_site`（基准 `686661cc`）；AI Observer 同步 `b263f3be` |
| 17 | P0-04 | S15 断连不停跟踪 | 天空 | 断开/切换设备前先停跟踪；frequency_hz 不再被多普勒改写 | 修复在位 | 修复在位 — `7f518fe6` — desktop/main_window.py:7853 — `self._stop_satellite_tracking()`（基准 `7f518fe6`） |
| 18 | P0-01 | M2 星象仪恢复成窗口化 | 操作逻辑 | 启动恢复星象仪即全屏；退出后下次启动仍全屏 | 修复在位 | 修复在位 — `a2be554a` — desktop/main_window.py:3856 — `win.showFullScreen()`（基准 `a2be554a`） |
| 19 | P0-07 | M4 无录音库列表 | 数据 | 录音库可列出/回放/删除；无文件时按钮置灰并说明 | 修复在位 | 新增工具栏「录音库」→ `RecordingLibraryDialog`（列出/回放/移入回收站）；删除走 `RecordingLibrary.trash_entry(confirm=True)` 只移 `_trash/`；空目录/目录不存在/文件丢失均置灰并写明原因（`desktop/recording_library_dialog.py`，`tests/test_desktop_recording_library_dialog.py` 7 passed，2026-11-23） |
| 20 | P0-07 | M5 崩溃残留无提示 | 数据 | 启动扫描未完成 sidecar/残留文件并在 UI 提示（可恢复/可删除） | 修复在位 | 启动扫描 `find_incomplete_recordings`（sidecar `complete=false` + `*.part/*.partial/*.tmp`）→ 工具栏「未完成录音 N」+ 状态栏提示；点击看详情并可「打开录音目录」，**不自动删/改**（`desktop/recording_observation.py`，`tests/test_desktop_incomplete_recordings.py` 10 passed，2026-11-23） |
| 21 | P0-11 | M10 无撤销栈/Ctrl+Z | UI频谱 | 有撤销栈并支持 Ctrl+Z；或移除一切可撤销暗示；两者择一且文档一致 | **部分** | 撤销栈 [ui_actions.py:60](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/ui_actions.py#L60) `_undo_stack` + `undo`:113 + 工具 `ui_layout_undo`（agent.py:2806）在位；`desktop/shortcuts.py` 无 undo（本轮 grep 0 命中）→ 缺 Ctrl+Z |
| 22 | P0-01 | L1 退出丢最大化 | 操作逻辑 | 退出星象仪恢复进入前的最大化/普通状态 | 修复在位 | 修复在位 — `a2be554a` — desktop/main_window.py:3944 — `showNormal`（基准 `a2be554a`；形态改独立窗，主窗最大化态不被触碰） |
| 23 | P0-07 | L3 同秒录音覆盖 | 数据 | 同一秒两次录音生成两个文件，不覆盖 | 修复在位 | 修复在位 — `be5d8101` — desktop/main_window.py:11399 — `_unique_record_path`（基准 `be5d8101`） |

**孤儿检查**：23 行全部有唯一专线，分布为 **接入层 3**（B1/S8/S9）、**UI频谱 2**（S1/M10）、**操作逻辑 6**（B2/S3/S5/S11/M2/L1）、**天空 2**（S14/S15）、**工具审计 3**（S10/S12/S13）、**数据 7**（S2/S4/S6/S7/M4/M5/L3），合计 23。**无孤儿。**

**残留 98.5 MHz 说明（诚实边界）**：S12 收口指 **AI 缺参路径**不再落演示频率（:9384/:9480 拒绝）。连接初频回退仍用 98.5 MHz（:7223 首次连接默认、:8117 设备拒绝初始频率后重试），属另一条路径，不在 S12 判据内；是否也要去掉由 P0-10 H-3 复核裁定。

---

## 3. 收口工单（按专线派活）

| 工单 | 专线 | 对应 P0 / 缺陷 | 本轮判定 |
|---|---|---|---|
| W-01 窗口与形态恢复 | 操作逻辑 | P0-01 / B2、M2、L1 | **关闭**（三条修复在位，保留回归用例） |
| W-05 站址单例 | 天空 | P0-05 / S14 | **关闭**（唯一写入口 + AI 同步在位） |
| W-06 连接与空态 | 接入层 + UI频谱 | P0-06 / S1、S8、S9 | **关闭**（引导切页 / 断源上报 / 失败置灰在位；真机回归待 §6） |
| W-07 录音状态机与库 | 数据 + 操作逻辑 | P0-07 / S4、S6、S7、M4、M5、L3 | **仍开**：S4/S6/S7/L3 收口；**M4 录音库接线、M5 启动残留扫描待修** |
| W-08 真模型协商回归 | 工具审计 | P0-08 | **仍开**：真 provider 未联调；AC-08.5 阈值待拍板（不设阈值） |
| W-09 收星并发与 UI 一致性 | 工具审计 + 操作逻辑 | P0-09 / S10、S11 | **关闭**（共享忙锁 + 失败零 UI 改动在位） |
| W-10 诚实性反例 | 工具审计 + 数据 | P0-10 / S12 | **关闭**（缺参拒绝在位；S2 回归绿；H-3 复核另列） |
| W-11 动态 UI 边界 | UI频谱 | P0-11 / M10 | **部分关闭**：比例校验 + 撤销栈在位；**Ctrl+Z 或移除暗示待二选一** |

> 已关闭工单保留回归用例，不再派开发。W-02 星表接线、W-04 断连收跟踪维持关闭。

---

## 4. P0 之外的前置条件（不拍板就会卡住）

| 编号 | 事项 | 当前状态 | 影响 |
|---|---|---|---|
| L18 | `arm_manual` 是否允许会话 token 自动 arm | **未拍板**（[limitations-register.md:81](file:///D:/mbdsdr/MBDSDR/docs/limitations-register.md#L81) 起；PRD.md:327 仍写待拍板） | P0-09；当前实现为真人弹窗、超时拒绝，token 未做 |
| L19 | 发射频段/合规策略 | **未拍板**（PRD.md:573） | 演示脚本能否包含发射 |
| L22 | 本轮集成验收红线 | **未拍板**（PRD.md:989） | 环境红/真红的处置口径 |
| L23 | 天空/AI 时间基准分叉 | **已统一**（§5 P1-02，`_physics_now` 显式 timestamp > TimeEngine，不退回墙钟）；口径销账待 L22 一并确认 | 违反 H-8 的风险已消除 |
| AC-08.5 | 真模型协商通过阈值 | **未拍板**（当前不设阈值） | W-08 |
| FZ-04 | 视觉解冻 U01–U23 | **20 项待拍**（[2026-11-12-user-decisions.md](file:///D:/mbdsdr/MBDSDR/docs/2026-11-12-user-decisions.md)，a11y 专项表，不覆盖 L18–L23） | 视觉类改动须用户逐项或「都按建议批」 |

---

## 5. P1/P2 现状摘要

| 条目 | 现状一句话（当前锚点） |
|---|---|
| P1-01 受晒接通 | **收口**：快照带受晒三态 [orbit.py:528](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/orbit.py#L528) `sunlit/sunlit_status`，UI 渲染 [rf_sky_view.py:545](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L545) `sunlit_state_label`，有 test_orbit_sunlit_snapshot 对拍 |
| P1-02 时间基准 | **收口**：[sdr_tools.py:129](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sdr_tools.py#L129) `_physics_now`「显式 timestamp > TimeEngine，不退回墙钟」，`_satellite_sky_view`:4737 同口径 |
| P1-04 dB 自动量程 | **收口**：[spectrum_widget.py:1726](file:///D:/mbdsdr/MBDSDR/desktop/spectrum_widget.py#L1726) `_auto_db_range` + 自动量程勾选:2840 + 手动让位:3040（代码参数定义：底噪锚 `10%`、跨度 `40–80 dB`、滞回 `3 dB`，非测量值） |
| P1-05 真实录音库 | **桌面已接线（M4，2026-11-23）**：工具栏「录音库」列出/回放/移入回收站（`_trash/`，不物理删除）；迁移/损坏索引仍走库内自测 |
| P1-06 真模型回归 | 仍仅自测（W-08） |
| P1-07 MCP 面 | 工具总数 521（`tool_surface_audit` registry=521，2026-11-23）；MCP 控制器面与 AI 工具面的取舍仍待拍板（L21） |
| P1-09 DPI/触屏 | 本轮未复核，保留旧状（布局四档基线未跑） |
| P1-11 多机互联 | **spec + M2 内核 + CLI + 桌面/手机 UI + 两机融合**（[spec.md](file:///D:/mbdsdr/.trae/specs/multi-device-interconnect/spec.md)）：`device_gateway.py` 配对码/token、角色、只读 `/roster` `/panorama` `/poses` `/fuse`、`POST /pose`；默认回环、私有网段需显式 allow_lan、无写端点；CLI 与「文件 → 多设备网关（配对）...」均可启动并显示配对码/名册，`/panorama` 共用 `build_panorama` 且每次调用重取当前站址（`tests/test_device_gateway.py` 26 + 对话框 7 passed）；手机 `DeviceGatewayClient` + 设置页配对分区（角色/配对码/解除/全景摘要，令牌只进 SecureStore，只读角色拒发位姿；离线 18 条、全量 `flutter test` 591 passed）；lens+eyepiece 两条位姿经同一 `fuse_poses` 内核加权，对话框显示参与来源与丢弃原因；手机可刷新名册（角色·设备名/条数，不回显 token）；指天视图可手动上报真实位姿（缺传感器/越界本地拒发，精度为观测波动估计或保守 10°）；可选 1 Hz 自动连续上报（显式开关、默认关闭，上一拍未回来就跳过，缺航向/俯仰不补 0°，配对失效自动停；全量 `flutter test` 604 passed）；目镜识别结果随位姿单列（`identification`，仅 eyepiece；桌面显示星名/HIP/角距，不并入粗指向）；头显只读客户端已备（pair/roster/panorama/passes/poses/fuse，10 条 JVM 离线用例）；权威端 `GET /passes` 用本地 TLE 离线 SGP4（桌面对话框可选 TLE 文件启用，每次请求取当前站址；未接线 503）；头显主窗已接只读网关面板（配对 + 手动刷新全景/过境摘要，token 仅内存）；断网刷新失败时保留上次摘要并标注「未联网·上次结果」，从未成功过则明说回看包内内容（M4.3，3 条 JVM 用例）；「此刻」窗口新增「网关」一行，只转述 `/panorama` 站址/太阳高度/缺项（带 host:port 与断网标记，不覆盖 bundle）；头显直连 SDR 的权威端**一次性授权协议**已落（headset 独占租约 300 s 可续，非 headset 403 / 他人持有 409 / 桌面占用或未知 503，网关 41 passed；桌面对话框可选 rtl_tcp 地址 + 自报桌面占用并显示租约状态，对话框 12 passed；头显端授权客户端已备（sdrStatus/sdrLease/sdrRelease + 面板三按钮，非 headset 本地拒发，headset 162 用例 0 失败）；头显主窗可只读消费 `/fuse`（「刷新融合」显示方位/高度/精度/条数/来源，不可用只转述 reason））。**未做**：真机配对/上报验证、头显 rtl_tcp I/Q 链路、四窗星图用增量数据替换 |
| P1-12 模型全景信息 | **软件收口**：只读工具 `sky_panorama`（`mbdsdr.sky-panorama/1`）一次返回站址/时刻/太阳/月球/天气/空间天气（Kp/F10.7/SSN/X 射线）/传播/今夜 + `missing`；真实快照探针 1752 字符，缺云量/Kp 时如实列缺；真模型调用回归未做 |
| P2-01 银河 | 仍无数据不画（[rf_sky_view.py:689](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L689)「无数据·不提供」，状态卡如实标注，不给假按钮） |
| P2-05 打包许可 | 星表在包内（[pyproject.toml:83](file:///D:/mbdsdr/MBDSDR/pyproject.toml#L83) `data/*.npz`，走 setuptools package-data）；第三方许可清单待补 |

---

## 6. 复跑命令与本轮输出（E2，2026-11-23）

> 环境：`PYTHONUTF8=1 PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen MBDSDR_OFFLINE=1`，`-p no:cacheprovider`。
> **坑（本轮实测）**：根 tests/ 的 conftest 锁守卫**禁止** `MBDSDR_TEST_LOCK=0` 绕锁构造 QApplication——带该变量首轮全红（非产品缺陷），去掉后全绿。每个测试文件单独 subprocess（conftest 会 hard-exit）。
> 日志原件在仓库外 `D:/mbdsdr/output/refresh_20261123/<文件名>.log`（未入库，E2 以命令复跑为准）。

```powershell
python scripts/ai_tool_consistency.py        # 本轮：520 工具，结构性错误 0，漂移 0，一致；EXIT=0
python scripts/tool_availability_audit.py    # 本轮：total=520，一类 412 / 二类 108，STUB=0 BROKEN=0；EXIT=0
python -m pytest tests/test_sky_dual_loops.py -q          # 16 passed
python -m pytest tests/test_sky_time_lock.py -q           # 15 passed
python -m pytest tests/test_sky_readouts.py -q            # 70 passed
python -m pytest tests/test_startup_log_policy.py -q      # 8 passed
python -m pytest tests/test_demod_worker_failure_visibility.py -q  # 4 passed
python -m pytest tests/test_backend_resilience.py -q      # 26 passed
python -m pytest tests/test_ui_settings_isolation_guard.py -q      # 10 passed
python -m pytest tests/test_modal_dialog_guard.py -q      # 3 passed
python -m pytest tests/test_recording_overwrite_guard.py -q        # 8 passed（L3）
python -m pytest tests/test_sample_rate_changes.py -q     # 3 passed（S4）
```

本轮合计 **163 passed / 0 failed / 0 skipped**（上列 10 个文件）。工具面一类/二类由 413/107 变为 **412/108**（`sdr_get_gps` 归①SDR 组），`docs/tool-availability.md` 由审计脚本按自身设计同步刷新。

---

## 7. 健壮性架构实现对照（2026-11-23 复核）

> 对应 PRD §10.7。口径：**已实现** = 代码存在且有自动化用例；**已实现（缺测试）** = 代码存在但只算实现存在；**未接入** = 库层已实现、桌面端没有调用方；**待定** = 等外部条件或用户拍板。

| 类别 | 判定 | 证据（当前锚点） | 备注 |
|---|---|---|---|
| 启动自检、日志落点、DLL 报告 | 已实现 | `desktop/main.py:_deferred_import_plan`:328/`_deferred_import_worker`:342（旧名 `_run_import_smoke`，现为上列两符号）、`_packaging_verdict`:318、`_report_runtime_dlls`:705；`tests/test_startup_log_policy.py` | 符号漂移已重定位 |
| 缺件三分类弹窗 | 已实现（缺测试） | `main.py:_packaging_verdict` | 三分类无单测，按 PRD AC-R20 手工判 |
| 线程边界：可预期异常限流上报 | 已实现 | `receive_pipeline.py:DemodWorker._report_demod_failure`:376/`_report_unexpected`:437；`tests/test_demod_worker_failure_visibility.py` | — |
| 线程边界：编程错误停线程不上抛 | 已实现 | 同上，`_report_unexpected` → `_running=False` | — |
| 读线程断源上报 | 已实现 | `receive_pipeline.py:IqReaderThread.STALL_TIMEOUT_S`:133/read_failed；`main_window.py:_on_pipeline_read_failed`:9922 | — |
| FFT worker 异常可见 | **未收口** | `receive_pipeline.py:FftWorker.run` 两处 `except Exception: pass`（:232-233/:237-238，本轮亲手确认仍在）；全仓无 `frame_failed` | 需先补 frame_failed 信号 |
| rtl_tcp / SpyServer 链路状态 | 已实现 | `rtltcp_client.py:link_state`:178；`spyserver_client.py:link_state`:238；`tests/test_backend_resilience.py` | — |
| 自动重连 | **未接入（库已实现）** | `reconnection.py:ReconnectionManager`；桌面明说不自动重连 [main_window.py:10018](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L10018) 注释；desktop 无 import | 断链仍是拆除 + 手动连接 |
| 设备热插拔 | **未接入（库已实现）** | `device_manager.py:DeviceManager.start_monitor`:647 示例；desktop 零调用（本轮 grep 确认） | — |
| 无设备横幅 | 已实现 | `main_window.py:_show_no_rtl_hint`:6782/`_NO_SDR_HINT`:393；`tests/test_no_device_onboarding.py` | — |
| 补偿量未知显示「—」 | 已实现 | `main_window.py:format_doppler_status`:203；`tests/test_doppler_readout.py` | — |
| 哨兵 NaN 与可测范围不相交 | 已实现 | `spectrum_widget.py:SpectrumGenerator._resample_max`:663；`tests/test_dsp_perf_optimizations.py`、`test_spectrum_fft_display_truth.py` | **本轮翻转**：旧判缺测试，现有覆盖 |
| 失败不落盘 | 已实现 | `tests/test_tune_failure_no_commit.py` | — |
| NTP 不可达不报成功 | 已实现 | `agent.py:_ntp`:2347（`time_sync_ntp`）；`mbdsdr_ai/tests/test_offline_behavior_audit.py` | **本轮翻转**：旧判缺测试，现有覆盖 |
| 发射能力不冒充 | 已实现 | `main_window.py:ptt_state`:536 | 部分 TX 后端有驱动级实现；无 verified=True，属真机待验 |
| 测试隔离（真实配置零写入 / 禁真模态 / Qt 全局锁） | 已实现 | `tests/test_ui_settings_isolation_guard.py`、`test_modal_dialog_guard.py`；`tests/conftest.py` 文件锁 300s:659/`pytest_configure`:1132 | Qt 锁按 AC-R18 脚本判 |
| AC-08.5 阈值 | 待定 | PRD §6 AC-08.5 | 等用户拍板 |
| 真机七环 R6 | **待定（UNVERIFIABLE）** | `docs/real_hw_acceptance_20261103.md` R6；`2b6f555b` 复验 + 2026-11-22 加长窗口复测 | 未验证（hardware-absent）：FC0012 上限 948.6 MHz < 1090 MHz 物理不可达；复测 574 候选帧全 CRC 失败，原件未入库 |
| TX 真机验证 | 待定 | `main_window.py:ptt_state` | 等设备 + 授权 + 假负载 |
| 1366×768 布局验收 | 待定 | `docs/design-basis.md` 第 `6` 条 | 等用户拍板窄屏方案 |

---

## 8. 方向评估（2026-11-23，用户愿景对照）

> 本节是设计描述与优先级建议（A 类），不是测量断言。灵魂锚点：**这个项目最初的意义是给小姑娘看天空有多浪漫，不是救灾**——凡不服务于「一个什么都不懂的人，指一下就被温柔地告知」的功能，都不排在前面。三条硬约束（不许假数据 / 不许静默 / 显示可复算，见 PRD §0）无条件成立，不参与方向权衡，也不因任何愿景豁免。

**愿景 → 现状映射（证据见 §1–§7 与 .trae/specs 两份视觉 spec）**

| 用户愿景 | 现状 | 缺口 |
|---|---|---|
| 激光笔式指星即认 | 软件链全绿（sky-ar-pointing-lifecycle：AR 142 + 工具面 59 测试通过；手机指哪认哪 567 passed） | 真实夜空端到端 not_verified |
| 手机架望远镜，AI 教怎么架 | provider 契约 + 两星校准 + `2.3°` 窄视场参数在位 | 引导叙事未成型；目镜后 `~1°` 视场物理不可靠（spec 已如实写明） |
| 当天云量气象（天文通式） | openapi_weather 工具 + tonight.py 云量阈值 + 空间天气在位；**综合观星指数（云量×月光，`stargazing_index`）已落内核并进 AI 讲解**（`TONIGHT_SCHEMA=3`，缺云量不给分、缺月光显式标注）；**星象仪顶部「观星指数」按钮已接线**（点一下按当前站址后台取数，未设站址/缺测都给「未知」并写明原因，站址变更即作废） | 视宁度/透明度无可信数据源未做；指数只在桌面星象仪、AI 工具与人话输出，手机/头显尚未显示；反编译参照在仓库外 scratch/twt-apk/FUNCTIONS.md |
| Stellarium 全包含 | 星表 + 星官 + 时间引擎 + 投影互逆钉扎 | 银河无数据（P2-01）、DSO 表未接；按浪漫主线分批补，不宣称全包含 |
| SDR++/OnAir 几乎全功能 + 全工具化 | 521 工具（`tool_surface_audit`，2026-11-23）；SSTV/气象星全链/FT8 族/ADS-B 族/数字语音外挂/GPS-RTK/VNA 在位 | QO-100 全仓零命中；数字电视/TETRA/IoT/蜂窝/军用 HF 缺（OnAir MODES 表为参照，非照单全收） |
| GNSS 方位推姿态 | gnss_sky_fit 接实时 GSV，拟合结论「全向天线无角分辨力」→ 如实拒绝 | 单天线 GNSS 物理上无航向；正确路径 = GNSS 位置时间 + 星历算方位 + 罗盘航向（已在走） |
| 云台/抛物面/射电望远镜指向工具化 | rotctld + Alpaca 赤道仪工具在位，三级承诺 + 一次性 token + Abort | 真机指向闭环 not_verified；射电望远镜走同一 rotctld 接缝 |
| 点卫星→AI→指→收→解调→档案 | 每块积木在位（§1 P0-09 收口） | 端到端真机闭环未完整演练；HANDOFF §8 P1 共享环形缓冲真缺陷未修 |
| 头显手一扒拉指星（PICO） | Kotlin 工程 + 数据契约三端钉扎 + 单测全绿 + APK 可构建 | 模拟器/真机目视、性能、空间音频、参赛材料未验；**硬截止 2026-12-19 09:00** |

**冲刺序列建议（与 HANDOFF §8 队列对齐）**

1. **P0 · PICO 窗口**：模拟器四窗目视 → 真机（若能借到）→ 3 分钟演示脚本（加载包 → 指星 → 听真实录音 → 跳档案）→ README + 身份红线 → 锁版本。截止不等人。
2. **P0.5 · 收星闭环真机演练**：GNSS/SDR 已可插上——挑一颗真有下行的星（气象星 APT/LRPT 或 ISS SSTV），完整走「点选 → AI 讲解 → 指向 → 接收 → 出图 → 档案回放」，顺手修共享环形缓冲真缺陷。这一遍走通就是 demo 本体。
3. **P1 · 浪漫主线功能面**：QO-100 profile、业余星逐星档案、天文通式观星指数、DSO 表（openngc）。
4. **明确不做**：LTE/5G、军用 HF、TETRA/trunking、IoT 长尾——不服务于「指一下就被温柔告知」的一律往后放；追平模式数量是造活，收星闭环才是意义。

**仍压着的拍板项**：FZ-04 视觉解冻 20 项（§4）、L18/L19/L22（L23 已统一待销账）、AC-08.5 阈值。方向认可后按子 agent 制派工。
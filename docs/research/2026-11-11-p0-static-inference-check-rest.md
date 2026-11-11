# 2026-11-11 P0 逐条实测核查（第二批）

> 范围：`docs/research/2026-11-11-roadmap.md` §1 中除 P0-03/04/10/11 之外的 13 条
> （P0-01/02/05/06/07/08/09/12/13/14/15/16/17）。P0-03/04/10/11 见同族文档
> `2026-11-11-p0-static-inference-check.md`。
> 性质：**只新增脚本与文档，未改任何产品代码**；ACARS 乱码帧只登记、不修（解码线负责）。
> 三分类口径：**真 P0（还能复现）／已被修掉（必须附修复提交号 + 反证对照组）／无法判定（写明缺什么前置）**。
> 规则：没有对照组的只能记「无法判定」，不许记「已修」。

## 1. 口径与环境

| 项 | 值 |
|---|---|
| 测量快照 | `D:\mbdsdr\output\tmp\p0-head-c899eb51`（detached worktree，HEAD `c899eb51`） |
| 基线归档 | `git archive` 解到 `D:\mbdsdr\output\tmp\p0-base-<sha>`，脚本用 `MBDSDR_REPRO_ROOT` 指向 |
| Python / OS | 3.14.0 / Windows |
| Qt | PySide6；除 P0-13 用 `QT_QPA_PLATFORM=windows` 外全部 `offscreen` |
| 配置隔离 | `HOME/USERPROFILE/APPDATA/LOCALAPPDATA` 全部重定向到临时 profile，`QSettings.IniFormat` |
| 硬件 | 无 SDR 真机；无 SoapySDR / libhackrf（与本批结论无关，均以桩或纯算法路径测量） |
| DPR | 默认 1.5；P0-13 另用 `QT_ENABLE_HIGHDPI_SCALING=0` 构造 DPR 1.0 样本 |

退出码约定（全部脚本一致）：**0 = 复现成功（缺陷仍在）／1 = 未复现／2 = 无法判定**。

### 为什么固定在快照上量

仓库当天被多条线并发提交，工作区还有别的线未提交改动。快照 worktree 让「同一份代码」可被反复重放，
基线归档让「把修复改回去」不必 checkout、不必占别人的文件。所有数字都可用第 6 节的命令复跑。

## 2. 三分类总表

| 条目 | 退出码（HEAD） | 分类 | 修复提交 / 缺什么前置 |
|---|---|---|---|
| P0-01 dead TLE 仍闭环跟踪 | 0 | **真 P0** | — |
| P0-02 四套卫星清单不同源 | 0 | **真 P0（残余）** | 交集 4 / 并集 8；已有 `f94d6bf0`/`221fbce2` 收敛前两份，downlink 表仍独立 |
| P0-05 频谱设置重启不还原 | 1 | **已被修掉** | `685e8f50`；对照 `685e8f50^`=`d77d9b78` 退出码 0 |
| P0-06 导入即全量覆盖 | 1 | **已被修掉** | `6f8f5fcf`；对照 `6f8f5fcf^`=`a1506664` 退出码 0 |
| P0-07 恢复默认重置运行态 | 1 | **已被修掉** | `59eb6754` + `905d5483`；对照 `59eb6754^`=`d372f3e7` 退出码 0 |
| P0-08 内部停止按钮不复位 / 假「完成」 | 1 | **已被修掉** | `bf06e9a4`+`f81f1301`+`c2ce967a`+`bc199e01`；对照 `bf06e9a4^`=`82bb5421` 退出码 0 |
| P0-09 录音 sidecar 中心频率不更新 | 0 | **真 P0** | — |
| P0-12 `_sky_to_screen_opt` 未定义调用 | 1 | **无法判定** | HEAD 无调用也无定义；`git log --all -S` 全历史从未出现该符号，无修复提交可回退，缺发生当时的未提交现场 |
| P0-13 窄宽闸门漏测 topBarDetail | 1 | **已被修掉** | `e0900f6e`（尺子）+ `ff48f7b9`（把被测对象移走）；对照 `e0900f6e^`=`0aaa6afe` 退出码 0；注入式对照复原 6→24 |
| P0-14 冷启动无首屏反馈 | 1 | **已被修掉** | `a52ce510`；对照 `a52ce510^`=`1f72f0c3` 退出码 0 |
| P0-15 构造期同步 `init_agent` | 0 | **真 P0（形态迁移）** | 不在 `AIPanel.__init__`，在 `MainWindow` 构造链；路径需勘误 |
| P0-16 切主题全量重抛光无反馈 | 0 | **真 P0** | — |
| P0-17 连接设备同步阻塞 GUI | 0 | **真 P0** | 启动自动连接已异步，手动路径未改 |

合计：真 P0 6 条（其中 P0-02 为残余、P0-15 为形态迁移）、已被修掉 6 条（全部带对照组）、无法判定 1 条。

## 3. 逐条证据（请求 / 实际 / 差在哪）

### 3.1 真 P0

**P0-01｜policy 已给出 deny，desktop 仍下发调谐（退出码 0）**

- 脚本：`scripts/repro/2026-11-11-p0-01-dead-tle-tracking-gate.py`
- 请求：把内置 ISS TLE 历元改成 `24001.00000000`，`tracking_policy_for()` 应拒绝；仰角 45° 跑一拍真实 `MainWindow._poll_satellite_track`。
- 实际：`policy: allow_tune=False command_allowed=False`；`_on_tune_sdr` 调用 **1 次** `[(145800000.0, 'FM')]`；异常 None。
- 差在哪：数据层 `1693de55` 已经把闸门做成唯一入口，但 `_poll_satellite_track`（快照 `desktop/main_window.py:12203`）
  完全不读 `pos["policy"]`，只看 valid / 仰角 ≥5°，闸门形同虚设。
- 反证对照：同一桩 + 显式读 `policy.allow_tune` 的等价实现 → `_on_tune_sdr` **0 次**，证明计数探针有效、差值来自产品代码。

**P0-02｜四套清单仍不同源（退出码 0，残余）**

- 脚本：`scripts/repro/2026-11-11-p0-02-satellite-list-single-source.py`
- 请求：逐名核对 `orbit.BUILTIN_SATS`、`new_spacetime_tle.DEFAULT_SATELLITES`、`rf_sky_view.SATELLITE_META`（AST 取 `AnnAssign`）、`sat_tracker.BUILTIN_DOWNLINK_HZ`。
- 实际：前 3 份 6/6 一致；downlink 表 6 项为 `FUNBCUBE-1 (AO-73)`/`RADFXSAT (FOX-1B)` 替换了 `FENGYUN 3D`/`METEOR M2`；
  **交集 4 / 并集 8**；同名 CATNR 冲突：无。TLE 缓存目录仍有 3 种口径（`~/.mbdsdr/new_spacetime_tle`、`~/.mbdsdr/tle`、`~/.mbdsdr/tle_cache`）。
- 差在哪：`f94d6bf0`（DEFAULT_SATELLITES 并入 orbit）与 `221fbce2` 已把「四套」收敛到「两份 + 一份 catalog」，但
  downlink 频率表仍是独立名单，同一颗星在不同入口可见性不同。roadmap 的「交集 4 / 并集 8」在 HEAD 仍成立。

**P0-09｜sidecar 中心频率不更新（退出码 0）**

- 脚本：`scripts/repro/2026-11-11-p0-09-record-center-freq-sidecar.py`
- 请求：开段 `_open_record_segment(path, 2.048e6, 145.5e6, …)` → 写 4096 样本 → `_on_tune_sdr(146.5e6,'FM')` → `_stop_recording()` → 读 sidecar。
- 实际：`sidecar.center_freq_hz = 145,500,000 Hz`；`center_freq_changes = None`；`segments = None`。
- 差在哪：整段回放会按 145.5 MHz 解释，实际后段是 146.5 MHz，**错位 1.0 MHz**；既不写 `center_freq_changes[]` 也不分段。

**P0-15｜构造期同步 `init_agent`（退出码 0，形态迁移）**

- 脚本：`scripts/repro/2026-11-11-p0-15-init-agent-in-ctor.py`
- 请求：静态检查 `AIPanel.__init__` 是否直调 `init_agent`；再给 `AIPanel.init_agent` 装计时探针构造真实 MainWindow。
- 实际：静态 `AIPanel.__init__` 直调 = **False**（roadmap 的路径不成立）；构造期仍调用 **1 次、2259 ms**，
  调用栈 `_build_central_widget:2117 ← _build_capability_drawer:4371 ← _ensure_agent_kernel:4412`；`MainWindow` 构造总耗时 5690 ms（本轮波动，历史 2813–5690 ms）。
- 差在哪：成本仍在首帧前，只是位置从 AI 面板搬到了能力抽屉的 `_ensure_agent_kernel`。roadmap 的 `desktop/ai_panel.py:671` 需勘误。

**P0-16｜切主题 3–4.5 s 无忙碌提示（退出码 0）**

- 脚本：`scripts/repro/2026-11-11-p0-16-theme-switch-timing.py`
- 请求：`_apply_theme('default') → 'dark' → 'default'` 计时，检查 `overrideCursor` / 模态窗口 / `QProgressDialog`。
- 实际：控件 **1575**；`default 4527.2 ms`、`dark 4387.0 ms`、`default 3269.4 ms`；忙碌反馈三项全 False；≥1000 ms 出现 2 次。
- 差在哪：GUI 线程被全量 QSS 重抛光占满，期间没有任何反馈；控件数比 roadmap 的 1480 更多。

**P0-17｜手动连接同步阻塞 2.8 s（退出码 0）**

- 脚本：`scripts/repro/2026-11-11-p0-17-connect-blocks-gui.py`
- 请求：50 ms 心跳 QTimer 先空转 2 s 作正例；再用 `connect()` sleep 2.0 s 的后端调真实 `_connect_backend`。
- 实际：空转 2 s 心跳 **31 次**（探针有效）；`_connect_backend` 总耗时 **2813 ms**，期间心跳 **1 次**，`connect` 调用 1 次。
- 差在哪：`_auto_enumerate_and_connect_async` 已存在（启动路径异步），但手动「连接」仍走同步 `backend.connect()`（快照 `main_window.py:6059`），
  中间只有 1 次 `processEvents()`。

### 3.2 已被修掉（全部附对照组）

**P0-05｜频谱设置重启不还原 —— 修复 `685e8f50`**

- HEAD：`fft_size 4096 OK`、`waterfall_palette soft OK`、`peak_threshold_db 21.0 OK`（真实消费点 `generator.fft_size` / `_state.wf_colors` / `_state.peak_rel_db`），退出码 1。
- 对照组（`d77d9b78` = `685e8f50^`）：重启后真实消费点 `1024 / sdrpp / 6.0`，三个键全 DIFF，退出码 **0**（复现）。
- 结论：修复前可复现、修复后不可复现，差值来自该提交。

**P0-06｜导入即全量覆盖 —— 修复 `6f8f5fcf`**

- HEAD：只导入 `theme` 后，5 个哨兵全不变；`ImportResult` 用 `absent=[…52 项]` 语义，`changes=[('theme','dark_car','dark')]`，退出码 1。
- 对照组（`a1506664` = `6f8f5fcf^`）：`filled=[…52 项]`，`frequency_hz 145.5M→98M`、`tuning_step_hz 1000→10000`、
  `observer_lat/lon → ''`、`ai_temperature 0.42→0.7`，退出码 **0**（复现）。

**P0-07｜恢复全部默认重置运行态 —— 修复 `59eb6754` + `905d5483`**

- HEAD：`reset_all(confirm=False)` 后 `frequency_hz/gain_db/demod_mode/bandwidth_hz/app_mode` 全保持，`fft_size 4096→2048`（证明 reset 确实执行），退出码 1。
- 对照组（`d372f3e7` = `59eb6754^`）：`frequency_hz→98M`、`gain_db→20`、`demod_mode→FM`、`bandwidth_hz→0`、`app_mode→ide`，退出码 **0**（复现）。
- 说明：`905d5483` 删的是后端 `settings.reset_all` 入口，`59eb6754` 改的是 UI 应用端；两笔互补，对照组只回退了后者即可复现。

**P0-08｜内部停止不复位 / 假「录制完成」 —— 修复 `bf06e9a4`+`f81f1301`+`c2ce967a`+`bc199e01`**

- HEAD：
  - 路径 A 正常停止：按钮 `主=False 面板=False`，结果态 `completed`，状态栏「录制完成: … (2048 样本…)」。
  - 路径 B 内部中止（`_stop_recording(error="写盘失败: disk full")`）：按钮 `False/False`，结果态 `failed`，状态栏仅「写盘失败: disk full」。
  - 路径 C 未连接直接点录音：警告 1 次，主按钮 False。
  - 退出码 1。
- 对照组（`82bb5421` = `bf06e9a4^`，早于全部四笔）：A/B 按钮均 `主=True 面板=True`；B 结果态 `None`；B 状态栏是
  「写盘失败: disk full；录制完成: …_2.iq (2048 样本…)」；`control_panel` 结果态字段不存在。退出码 **0**（复现）。
- 探针说明：记录前两轮踩过的两个假象已在脚本里固化——预 `setChecked(True)` 会触发 `toggled→_toggle_record` 导致立刻停录，
  必须 `blockSignals(True)`；路径 C 不能预置按钮，否则会多弹一次。

**P0-13｜窄宽闸门漏测 topBarDetail —— 修复 `e0900f6e`（尺子）+ `ff48f7b9`（对象）**

- HEAD（DPR 1.5 与 DPR 1.0 各跑一次）：`legacy_gate / detail_open / default` 三档滑杆列越界全 **0 px**，退出码 1。
- 对照组 1（`0aaa6afe` = `e0900f6e^`，旧闸门）：JSON 无 `states/default`，仍是单状态口径，只能报挤压/裁切计数，
  脚本判「闸门未测 topBarDetail」，退出码 **0**（复现）。
- 对照组 2（注入式，`0aaa6afe` 旧树 + HEAD 新闸门，`QT_ENABLE_HIGHDPI_SCALING=0` → DPR 1.0）：
  `legacy_gate +6 px / detail_open +7 px / default +24 px`，**复原 18 px 缺口**。
- 结论：`e0900f6e` 把尺子改成状态矩阵，`ff48f7b9` 随后把三根量程滑杆收进工具栏「显示」弹出层，
  HEAD 上「24 px 越界」这个被测对象本身已不存在，所以 HEAD 读数是 0 而不是 24。
- 环境限制：DPR 1.5 下新旧尺子两态都是 0（闸门脚本 `slider_column_report` 注释亦自述），
  要构造 6→24 必须 `QT_ENABLE_HIGHDPI_SCALING=0`。这也是本组对照必须写清 DPR 的原因。

**P0-14｜冷启动无首屏反馈 —— 修复 `a52ce510`**

- HEAD：splash **452 ms**、窗口 **12913 ms**，首个可见反馈远早于 2 s 阈值，退出码 1。
- 对照组（`1f72f0c3` = `a52ce510^`）：splash **6890 ms**、smoke 4365–6769 ms、窗口 **14503 ms**，退出码 **0**（复现）。
- 差在哪：修复把「自检 + 主窗口导入」挪到 splash 之后，首块可见反馈从 6.9 s 提前到 0.45 s。

### 3.3 无法判定

**P0-12｜`_sky_to_screen_opt` 5 处调用 0 处定义 —— 无法判定**

- 脚本：`scripts/repro/2026-11-11-p0-12-sky-opt-undefined-call.py`
- 请求：AST 统计 `desktop/` 全域该符号的调用/定义。
- 实际：HEAD **调用 0 / 定义 0**（退出码 1，未复现）。注入式对照（临时文件加 1 处调用、删定义）检测器报 **1/0**，说明探针有效。
- 缺什么前置：`git log --all -S "_sky_to_screen_opt" -- desktop/` **没有任何提交**——该符号从未进过已提交代码，
  原「5 用 0 定义」只能来自某一时刻的未提交工作区。没有可回退的修复提交，按规则不能记「已修」，
  也不能把「工作区现象」升级成 HEAD 结论。若后续要定案，需要文件持有线提供当时的 diff 或 snapshot。

## 4. 文档路径勘误

| 文档写法 | 实际位置 | 影响 / 备注 |
|---|---|---|
| `desktop/sdr_backend.py`（上一批报告） | `mbdsdr_ai/sdr_backend.py`（`desktop/sdr_backend.py` 不存在） | 按文档路径找会白找半小时 |
| P0-15 `desktop/ai_panel.py:671` | `desktop/main_window.py` → `_build_capability_drawer:4366` → `_ensure_agent_kernel:4406`；实测栈 `_build_central_widget:2117 ← _build_capability_drawer:4371 ← _ensure_agent_kernel:4412` | 断言本身（构造期同步）成立，但定位与「AIPanel 构造」不符；按原路径查会得出「静态不成立」的误判 |
| P0-03 `mbdsdr_ai/core/controller.py:46-47` | 实为 `47`、`50`（上一批已核） | 行号漂移 |
| P0-01 `desktop/main_window.py:12037-12044`、`12103-12121` | 当前 `_poll_satellite_track` 在 `12203`；函数内不读 `pos["policy"]` | 按旧行号读到的不是跟踪环主体 |
| P0-17 `desktop/main_window.py:6025 附近` | `_connect_backend` 在 `6012`，同步 `backend.connect()` 在 `6059` | 定位偏差 30+ 行 |
| P0-13 `scripts/ui_narrow_breakdown.py:507-512` | 现为 `slider_column_report:241-283`、状态矩阵 `655`、6/24 反证段 `895-907` | 行号漂移 |
| P0-05 `desktop/settings_panel.py:524-535`、`1083-1136` | `_apply_initial_state:565`、`import_from:1387` | 行号漂移 |
| P0-07 `desktop/settings.py:527-539` | 后端入口已由 `905d5483` 删除；UI 端在 `desktop/settings_panel.py:1281 reset_all` | 照文档找会报「文件/函数不存在」 |

## 5. roadmap 回填建议（本轮未写入）

`docs/research/2026-11-11-roadmap.md` 的两个前置条件都不满足：

- `file_claim.py status`：`owner=roadmap合成线 held=42.5/90.0min 剩余 47.5min`；
- `git status --porcelain -- docs/research/2026-11-11-roadmap.md` → ` M`（该线有未提交改动）。

按纪律**不抢、不跨线协商**，只给出建议回填文本，由 roadmap 合成线写入：

| 条目 | 建议「当前真实行为（证据来源）」单元格 |
|---|---|
| P0-01 | 实测 2026-11-11（快照 `c899eb51`）：`tracking_policy_for` 已返 `allow_tune=False`，但 `_poll_satellite_track` 不读 policy，仰角 45° 时仍调 `_on_tune_sdr`（145.8 MHz/FM）1 次；脚本 `scripts/repro/2026-11-11-p0-01-dead-tle-tracking-gate.py`（退出码 0；显式读 policy 的对照 0 次） |
| P0-02 | 实测 2026-11-11：`f94d6bf0`/`221fbce2` 已把前三份收敛为 6/6 一致，但 downlink 表仍独立 → 交集 4 / 并集 8、无同名 CATNR 冲突；TLE 缓存仍有 3 种目录口径；脚本 `…p0-02-satellite-list-single-source.py`（退出码 0） |
| P0-05 | 实测 2026-11-11：重启后 `generator.fft_size=4096`、`_state.wf_colors='soft'`、`_state.peak_rel_db=21.0`，**已由 `685e8f50` 修复**；对照组 `d77d9b78` 复现 1024/sdrpp/6.0；脚本 `…p0-05-spectrum-settings-restart.py` |
| P0-06 | 实测 2026-11-11：仅含 theme 的导入后 5 个哨兵全不变（`absent=[…]` 合并语义），**已由 `6f8f5fcf` 修复**；对照组 `a1506664` 复现 98M/10000/''/0.7；脚本 `…p0-06-import-overwrite.py` |
| P0-07 | 实测 2026-11-11：`reset_all` 只回默认可重置项（`fft_size 4096→2048`），运行态 5 键全保持，**已由 `59eb6754`+`905d5483` 修复**；对照组 `d372f3e7` 复现整组被重置；脚本 `…p0-07-reset-runtime-keys.py` |
| P0-08 | 实测 2026-11-11：三路径按钮态/终态文案均正确（B 为 `failed`、不出现「录制完成」、未连接只弹 1 次），**已由 `bf06e9a4`+`f81f1301`+`c2ce967a`+`bc199e01` 修复**；对照组 `82bb5421` 复现按钮不复位与假「完成」；脚本 `…p0-08-record-stop-ui.py` |
| P0-09 | 实测 2026-11-11：调谐到 146.5 MHz 后 sidecar 仍写 145.5 MHz、`center_freq_changes=None`，回放整段错位 1.0 MHz；脚本 `…p0-09-record-center-freq-sidecar.py`（退出码 0） |
| P0-12 | 实测 2026-11-11（快照）：HEAD 调用 0 / 定义 0；全历史 `git log -S` 无该符号 → 属工作区现象，**无法归因到某次修复，记无法判定**；脚本 `…p0-12-sky-opt-undefined-call.py`（退出码 1 + 注入式对照 1/0） |
| P0-13 | 实测 2026-11-11：闸门已按 `legacy_gate/detail_open/default` 状态矩阵测量（`e0900f6e`），且三根滑杆已被 `ff48f7b9` 移出右栏 → HEAD 三档均 0 px；旧树+旧尺子无 states（对照退出码 0），旧树+新尺子 @DPR1.0 复原 6/7/24；脚本 `…p0-13-narrow-gate-topbar-detail.py` |
| P0-14 | 实测 2026-11-11：首块 splash 452 ms、主窗口 12913 ms，**已由 `a52ce510` 修复**；对照组 `1f72f0c3` 复现 splash 6890 ms / 窗口 14503 ms；脚本 `…p0-14-cold-start-silent.py` |
| P0-15 | 实测 2026-11-11：`AIPanel.__init__` 不直调（原路径不成立）；真实成本在 `MainWindow._build_capability_drawer → _ensure_agent_kernel`，构造期 1 次 2259 ms、构造总 5690 ms；脚本 `…p0-15-init-agent-in-ctor.py`（退出码 0，路径需勘误） |
| P0-16 | 实测 2026-11-11：控件 1575，切换 4527/4387/3269 ms，忙碌反馈全无；脚本 `…p0-16-theme-switch-timing.py`（退出码 0） |
| P0-17 | 实测 2026-11-11：手动连接阻塞 2813 ms、期间心跳仅 1 次（正例 31 次）；`_auto_enumerate_and_connect_async` 仅覆盖启动路径；脚本 `…p0-17-connect-blocks-gui.py`（退出码 0） |

## 6. 复跑命令

```powershell
# 0) 快照（若已存在可跳过）
git -C D:\mbdsdr\MBDSDR worktree add --detach D:\mbdsdr\output\tmp\p0-head-c899eb51 c899eb51

# 1) HEAD 逐条
$env:MBDSDR_REPRO_ROOT="D:\mbdsdr\output\tmp\p0-head-c899eb51"
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-01-dead-tle-tracking-gate.py

# 2) 基线归档（对照组）
git -C D:\mbdsdr\MBDSDR archive -o $env:TEMP\b.tar d77d9b78
tar -xf $env:TEMP\b.tar -C D:\mbdsdr\output\tmp\p0-base-d77d9b78
$env:MBDSDR_REPRO_ROOT="D:\mbdsdr\output\tmp\p0-base-d77d9b78"
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-05-spectrum-settings-restart.py   # 期望退出码 0=复现

# 3) P0-13 必须在 DPR 1.0 下才有 6/24 样本
$env:QT_ENABLE_HIGHDPI_SCALING="0"
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-13-narrow-gate-topbar-detail.py

# 4) P0-13 注入式对照（旧树 + HEAD 尺子）
Copy-Item D:\mbdsdr\MBDSDR\scripts\ui_narrow_breakdown.py `
  D:\mbdsdr\output\tmp\p0-base-0aaa6afe\scripts\ui_narrow_new.py -Force
python D:\mbdsdr\output\tmp\p0-base-0aaa6afe\scripts\ui_narrow_new.py --json   # legacy 6 / detail 7 / default 24
```

基线对照一览：

| 条目 | 基线 sha | 基线退出码 | 现场读数 |
|---|---|---|---|
| P0-05 | `d77d9b78` | 0 | 1024 / sdrpp / 6.0 |
| P0-06 | `a1506664` | 0 | 98M / 10000 / '' / 0.7 |
| P0-07 | `d372f3e7` | 0 | 98M / 20 / FM / 0 |
| P0-08 | `82bb5421` | 0 | 按钮 True/True、B 结果态 None、含「录制完成」 |
| P0-13 | `0aaa6afe` | 0 | 旧闸门无 states；新尺子 @DPR1.0 = 6/7/24 |
| P0-14 | `1f72f0c3` | 0 | splash 6890 ms、窗口 14503 ms |

## 7. 边界与未做的事

- **只新增**：`scripts/repro/2026-11-11-p0-{01,02,05,06,07,08,09,12,13,14,15,16,17}-*.py` 与本文档；产品代码零改动。
- P0-14 脚本在本轮修了一处探针缺陷：旧启动链没有 `_import_main_window`，只包 `entry.MainWindow`；
  另外 45 s 兜底改为先打印 `[无法判定]` 再硬退，避免只留一个退出码。
- **ACARS 乱码帧只登记、不修**：解码线负责；本条不影响上表任何结论。
- P0-03/04/10/11 的实测结论见 `2026-11-11-p0-static-inference-check.md`，本文不重复。
- 观测噪声：P0-15/16/17 的绝对毫秒数随机器负载波动（同脚本两次可差 1 s 以上），但都是「秒级、无反馈/阻塞」量级，
  不影响三分类；P0-13 的 0 与 6/24 则是确定性读数，受 DPR 控制。

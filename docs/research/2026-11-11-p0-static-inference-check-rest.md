# 2026-11-11 P0 逐条实测核查（第二批 · 含跨线数字对齐）

> 范围：`docs/research/2026-11-11-roadmap.md` §1 中除 P0-03/04/10/11 之外的 13 条。
> P0-03/04/10/11 见 `2026-11-11-p0-static-inference-check.md`。
> 性质：**只新增脚本与文档，未改产品代码**；ACARS 乱码帧只登记、不修（解码线负责）。
> 三分类：**真 P0（还能复现）／已被修掉（附修复提交号 + 对照组）／无法判定（写明缺什么前置）**。
> 规则：没有对照组的只能记「无法判定」，不许记「已修」。

## 1. 口径与环境（两轮）

| 轮次 | 快照 | 说明 |
|---|---|---|
| 第一轮 | `c899eb51` | 13 条首测；此后又有 6 笔提交落地，部分条目状态已变 |
| 第二轮 | `a2837671` | 跨线数字对齐后的复核；本文所有「当前」结论以本轮为准 |

第二轮新增的 6 笔提交（都在 `c899eb51` 之后）：`bedf3c4d`（W26 连接后台化）、
`1fdf1958`（W24 内核装载延后）、`0ce78fb3`（主题改 QPalette）、
`07167624`（卫星跟踪启停读 policy）、`7501d5d4`（星历陈旧度）、`eb7adea1`（断开残留）。

其余环境：Windows / Python 3.14.0 / PySide6；默认 `QT_QPA_PLATFORM=offscreen`，
P0-13 用 `windows`；`HOME/USERPROFILE/APPDATA/LOCALAPPDATA` 全部重定向到临时 profile；
DPR 默认 1.5，P0-13 另用 `QT_ENABLE_HIGHDPI_SCALING=0` 构造 DPR 1.0。
退出码：**0 = 复现成功／1 = 未复现／2 = 无法判定**。

## 2. 三分类总表（复核后）

| 条目 | 第一轮 `c899eb51` | 第二轮 `a2837671` | 最终分类 | 修复提交 / 缺什么 |
|---|---|---|---|---|
| P0-01 dead TLE 仍闭环跟踪 | 0 真 P0 | 0 真 P0 | **真 P0** | `07167624` 只闸了「开始跟踪」，逐拍跟踪环仍未闸 |
| P0-02 四套卫星清单不同源 | 0 | 0（交集 4/并集 8） | **真 P0（残余）** | `f94d6bf0`/`221fbce2` 收敛前三份，downlink 表仍独立 |
| P0-05 频谱设置重启不还原 | 1 | 1（三键全 OK） | **已被修掉** | `685e8f50`；对照 `d77d9b78` 退出码 0 |
| P0-06 导入即全量覆盖 | 1 | 1（5 哨兵不变） | **已被修掉** | `6f8f5fcf`；对照 `a1506664` 退出码 0 |
| P0-07 恢复默认重置运行态 | 1 | 1（运行态全保持） | **已被修掉** | `59eb6754`+`905d5483`；对照 `d372f3e7` 退出码 0 |
| P0-08 内部停止不复位 / 假完成 | 1 | 1（三路径全对） | **已被修掉** | `bf06e9a4`+`f81f1301`+`c2ce967a`+`bc199e01`；对照 `82bb5421` 退出码 0 |
| P0-09 sidecar 中心频率不更新 | 0 | 0（仍 145.5 MHz） | **真 P0** | — |
| P0-12 `_sky_to_screen_opt` 未定义调用 | 1 | 1（调用 0/定义 0） | **无法判定** | 全历史无该符号，无修复提交可回退 |
| P0-13 窄宽闸门漏测 topBarDetail | 1 | 2（闸门闪退） | **已被修掉**（依据 `c899eb51`） | `e0900f6e`+`ff48f7b9`；对照 `0aaa6afe` 退出码 0；HEAD 复测被闸门闪退挡住（见 §5.2） |
| P0-14 冷启动无首屏反馈 | 1 | 1（splash 185 ms） | **已被修掉** | `a52ce510`；对照 `1f72f0c3` 退出码 0 |
| P0-15 构造期同步 init_agent | 0 | 1（构造期 0 次，首拍 2183 ms） | **已被修掉（构造期）** | `1fdf1958`；对照 `28a59c87` 退出码 0；首拍成本仍是一笔尾巴 |
| P0-16 切主题无反馈 | 0（4527/4387/3269 ms） | 1（810/954/498 ms） | **已被修掉** | `0ce78fb3`；对照 `20f64d35` 退出码 0 |
| P0-17 连接同步阻塞 GUI | 0（心跳 1 次） | 1（心跳 32 次） | **已被修掉** | `bedf3c4d`；对照 `35e09799` 退出码 0 |

最终：真 P0 4 条（P0-01/02/09 + 无）、已被修掉 7 条、无法判定 1 条，P0-13 单独标注复测阻塞。

> 更正上一轮的两处结论：**P0-15 不是「构造期仍同步」**（第一轮把 `processEvents()` 也算进构造期，
> 见 §3.3）；**P0-16/P0-17 的第一轮数字是修复前的快照值**，不应与性能线的修复后数字直接比较。

## 3. 三处跨线矛盾对齐

### 3.1 切主题差 10 倍：不是入口/负载，是提交不同

同一脚本 `scripts/repro/2026-11-11-p0-16-theme-switch-cost.py`，同一隔离 profile，
入口逐一测「直调 `MainWindow._apply_theme`」与「工具栏 `theme_combo` 信号路径」，
主题序列固定 `dark/default/dark`，读数四行：

| 快照 | 控件数 | `setStyleSheet` 次数（控件级/应用级） | 墙钟 | CPU 时间 |
|---|---|---|---|---|
| `a2837671`（HEAD，含 `0ce78fb3`） | 1620 | 7 / **0** | 498 / 810 / 954 ms | 516 / 1047 / 1125 ms |
| `20f64d35`（`0ce78fb3^`，对照组） | 1585 | 39 / **3**（每次 1 次 app 级） | 5814 / 5956 / 8027 ms | 4719 / 5956 / 7438 ms |
| `c899eb51`（第一轮快照，本轮补测） | 1575 | 13 / **3** | 4994 / 6714 / 8847 ms | 4203 / 4766 / 5828 ms |

- 入口 B（下拉信号）与入口 A 同量级（HEAD：0.1 / 490 / 508 ms），**入口不是变量**；
- 真正的变量是 `0ce78fb3`「主题色改走 QPalette，不再重设 QSS」：应用级 `setStyleSheet` 从每次 1 次降为 0，
  墙钟从 5–8 s 降到 0.5–0.95 s。性能线的 386–540 ms 与本次 HEAD 的 498–954 ms 是同一量级，
  差异来自机器负载（本轮同时有 Qt 进程在跑）；
- 我第一轮的 4527/4387/3269 ms 是 `c899eb51`（修复前）的读数，**不是 HEAD**。数字不改口，但必须标注快照。

### 3.2 P0-17 与 W26：W26 已覆盖手动路径，不需要重开

| 项 | 结论 |
|---|---|
| 手动入口 | 连接按钮 → `MainWindow._connect_backend`（`desktop/main_window.py:6104`） |
| W26 改了什么 | `bedf3c4d` 新增 `_connect_backend_in_background`（`6053`）：`threading.Thread("sdr-connect")` + `QEventLoop` + 30 ms 轮询 + 20 s watchdog；并把 `_connect_backend` 里原来的直接 `backend.connect()`（旧 6059）改为 `ok = self._connect_backend_in_background(backend)`（现 `6156`） |
| 为什么首测没覆盖 | 首测快照 `c899eb51` 早于 `bedf3c4d`，那时 `_connect_backend` 仍是同步调用、仅前置一次 `processEvents()` |
| HEAD 实测 | `_connect_backend` 总耗时 2659 ms，期间心跳 **32** 次（空转正例 31 次）→ GUI 线程未被独占 |
| 对照组 `35e09799`（W26 父提交） | 总耗时 2824 ms，心跳 **1** 次，退出码 0（复现） |
| 启动自动连接 | `_auto_enumerate_and_connect_async`（`5821`）走 `already_connected=True`，硬件 connect 已在后台完成，本来就异步 |

结论：P0-17 已被 `bedf3c4d` 修掉，W26 无需重开；第一轮的 2813 ms/1 次心跳是修复前读数。

### 3.3 P0-15 来源：`_ensure_agent_kernel` 由能力矩阵常驻引入，W24 只挪了同一个调用点

- `git log --all -S "_ensure_agent_kernel" -- desktop/` 只有一笔：`c47cdce9`
  「九条用户口径收口 —— 能力矩阵常驻」，它新增 `_build_capability_drawer()`，
  并在构造期调用 `self._ensure_agent_kernel()`（后者对空 panel 执行 `panel.init_agent()`）。
- W24 `1fdf1958` 改的是**同一行**：`self._ensure_agent_kernel()` →
  `QTimer.singleShot(0, self._ensure_agent_kernel)`（现 `main_window.py:4383`）。
- HEAD 实测（分段计时）：构造期 **0 次**、构造返回时 `ai_panel.agent is None`、
  构造 1147 ms；**首个事件循环拍** 装载 1 次、**2183 ms**。
- 对照组 `28a59c87`（W24 父提交）：构造期 1 次、**6216 ms**，退出码 0（复现）。
- 上一轮「构造期仍有 2259 ms」的来源：第一版探针把 `app.processEvents()` 放进构造计时窗口，
  0 延迟定时器在那一拍执行，于是被算成「构造期」。本轮已把两段拆开（脚本内注释说明），
  结论改为「构造期已修，成本挪到首拍」——**延后不是消失**，首拍 2.2–4.4 s 仍会顶到首帧后的交互，
  建议另立条目跟踪（本报告只登记，不记作 P0-15 未修）。
- 另一处 `ai_panel.init_agent(...)` 在 `main_window.py:7940`（`_load_ai_config`），
  只在有 `api_key` 且用户触发时执行，不属构造链，未计入。

## 4. 逐条证据（第二轮读数）

### 4.1 真 P0

- **P0-01**（HEAD，退出码 0）：`tracking_policy_for` 返回 `allow_tune=False`、仰角 45° 时
  `_on_tune_sdr` 仍被调用 1 次（145.8 MHz/FM）；显式读 policy 的对照 0 次。
  `07167624` 新增 `_tracking_policy_for`(12224)/`_confirm_tracking_policy`(12242) 并闸住
  `_start_satellite_tracking`(12253)，但 `_poll_satellite_track`(12358) 逐拍仍不读 policy。
- **P0-02**（退出码 0）：3 份 catalog 6/6 一致、无同名 CATNR 冲突；downlink 表多
  `AO-73`/`FOX-1B`、缺 `METEOR M2`/`FY-3D` → 交集 4 / 并集 8；TLE 缓存 3 种目录口径。
- **P0-09**（退出码 0）：`_open_record_segment(...145.5e6)` → 调谐 146.5 MHz → 停止后
  sidecar 仍 `center_freq_hz=145,500,000`、`center_freq_changes=None`，回放错位 1.0 MHz。

### 4.2 已被修掉（均带对照组）

| 条目 | HEAD 读数 | 对照组（基线退出码 0 的现场） |
|---|---|---|
| P0-05 | `fft_size=4096`、`wf_colors='soft'`、`peak_rel_db=21.0` 全 OK | `d77d9b78`：1024 / sdrpp / 6.0 |
| P0-06 | 5 哨兵全不变（`absent=[…]` 合并语义） | `a1506664`：98M / 10000 / '' / 0.7 |
| P0-07 | 运行态 5 键全保持，`fft_size 4096→2048` | `d372f3e7`：整组被重置 |
| P0-08 | A `completed`；B `failed`、无「录制完成」；C 仅 1 次警告 | `82bb5421`：按钮 True/True、B 结果态 None、仍写「录制完成」 |
| P0-13 | `c899eb51` 三档滑杆列 0 px；HEAD 闸门闪退（§5.2） | `0aaa6afe` 旧闸门无 `states`；注入式（旧树+新尺 @DPR1.0）= 6/7/**24** |
| P0-14 | splash 185 ms、窗口 10663 ms | `1f72f0c3`：splash 6890 ms、窗口 14503 ms |
| P0-16 | 见 §3.1 四行表 | `20f64d35`：5814–8027 ms、app 级 setStyleSheet 每切 1 次 |
| P0-17 | 见 §3.2 | `35e09799`：2824 ms、心跳 1 次 |

### 4.3 无法判定

- **P0-12**：HEAD 调用 0 / 定义 0；注入式检测器报 1/0（探针有效）；
  `git log --all -S "_sky_to_screen_opt" -- desktop/` 无任何提交——从未入库，
  无修复提交可回退，只能记工作区现象。**roadmap 记「无法判定」，不得记「已修」**。

## 5. 文档路径与测量环境勘误

### 5.1 路径 / 行号

| 文档写法 | 实际位置 | 影响 |
|---|---|---|
| `desktop/sdr_backend.py` | `mbdsdr_ai/sdr_backend.py`（前者不存在） | 按文档找会白找 |
| P0-15 `desktop/ai_panel.py:671` | `main_window.py` → `_build_capability_drawer:4366` → `_ensure_agent_kernel:4406` | 会误判「静态不成立」 |
| P0-03 `controller.py:46-47` | `47`、`50` | 行号漂移 |
| P0-01 `main_window.py:12037-12044`/`12103-12121` | 现 `_poll_satellite_track:12358`、`_start_satellite_tracking:12253` | 读到旧实现 |
| P0-17 `main_window.py:6025 附近` | `_connect_backend:6104`、后台包装 `6053`、调用点 `6156` | 定位偏差 30+ 行 |
| P0-13 `ui_narrow_breakdown.py:507-512` | `slider_column_report:241-283`、状态矩阵 `655`、6/24 反证 `895-907` | 行号漂移 |
| P0-05 `settings_panel.py:524-535`/`1083-1136` | `_apply_initial_state:565`、`import_from:1387` | 行号漂移 |
| P0-07 `desktop/settings.py:527-539` | 后端入口已由 `905d5483` 删除；UI 端在 `settings_panel.py:1281` | 文件/函数不存在 |

### 5.2 测量环境（这两条会把结论判反，务必先看）

1. **DPR 决定 P0-13 有没有样本**：`6 / 7 / 24 px` 只在 DPR 1.0（`QT_ENABLE_HIGHDPI_SCALING=0`）
   构造得出来；DPR 1.5 下新旧尺子两态都是 0。不写 DPR 就断言「闸门 0 px = 正常」会被误导。
2. **HEAD `a2837671` 上窄屏闸门稳定闪退**：直接跑 `scripts/ui_narrow_breakdown.py --json`，
   3 次全部 `0xC0000005`（access violation），栈在
   `main_window.py:3788 eventFilter` → `threading.get_ident()` 抛
   `SystemError: returned a result with an exception set`；该线程判定由 `09527ff5`
   （应用级事件过滤器在非 GUI 线程读 QEvent 踩空）引入。属新发现，只登记不修；
   P0-13 在 HEAD 的复测因此记为「被闸门闪退挡住」，不据此改判。

## 6. roadmap 回填建议（仍未写入）

`docs/research/2026-11-11-roadmap.md` 仍被 roadmap合成线 占用（`file_claim` 未过期，
且该路径有未提交改动），按纪律不抢，建议文本见本轮回复/交接，要点：

- P0-01：**仍复现**，`07167624` 只闸了开始跟踪，逐拍 `_poll_satellite_track` 未闸。
- P0-02：**仍复现（残余）**，交集 4 / 并集 8。
- P0-05/06/07/08/14：**已修**，附提交号与基线对照退出码 0。
- P0-09：**仍复现**，sidecar 偏 1.0 MHz。
- P0-12：**无法判定**（符号从未入库），不是「已修」。
- P0-13：**已修**（`e0900f6e`+`ff48f7b9`），但 HEAD 闸门闪退待新条目跟踪。
- P0-15：**已修（构造期）** by `1fdf1958`；首拍仍有 2.2–4.4 s，应另立条目。
- P0-16：**已修** by `0ce78fb3`（5–8 s → 0.5–0.95 s，app 级 setStyleSheet 归零）。
- P0-17：**已修** by `bedf3c4d`（心跳 1 → 32 次），W26 无需重开。

## 7. 复跑

```powershell
# 快照
git -C D:\mbdsdr\MBDSDR worktree add --detach D:\mbdsdr\output\tmp\p0-head-a2837671 a2837671
$env:MBDSDR_REPRO_ROOT="D:\mbdsdr\output\tmp\p0-head-a2837671"

# 主题四行表（HEAD / 对照 20f64d35）
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-16-theme-switch-cost.py
$env:MBDSDR_REPRO_ROOT="D:\mbdsdr\output\tmp\p0-base-20f64d35"
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-16-theme-switch-cost.py   # 期望 0=仍慢

# P0-15 分段（HEAD 期望 1；对照 28a59c87 期望 0）
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-15-init-agent-in-ctor.py

# P0-17（HEAD 期望 1；对照 35e09799 期望 0）
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-17-connect-blocks-gui.py

# P0-13：DPR 1.0 才有 6/24；HEAD 闸门当前闪退（0xC0000005）
$env:QT_ENABLE_HIGHDPI_SCALING="0"
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-13-narrow-gate-topbar-detail.py
```

基线一览：`d77d9b78`(P0-05) / `a1506664`(P0-06) / `d372f3e7`(P0-07) / `82bb5421`(P0-08) /
`0aaa6afe`(P0-13) / `1f72f0c3`(P0-14) / `28a59c87`(P0-15) / `20f64d35`(P0-16) / `35e09799`(P0-17)，
全部以「退出码 0」证明改前确实坏。

## 8. 边界

- 只新增脚本与文档；产品代码零改动。
- ACARS 乱码帧只登记、不修（解码线负责），不影响上表。
- 观测噪声：毫秒数随机器负载波动（同脚本两次可差 1 s 以上），但量级与三分类不受影响；
  P0-13 的 0 与 6/24 是确定性读数，受 DPR 控制。
- 本轮新增脚本：`2026-11-11-p0-16-theme-switch-cost.py`；并修正
  `2026-11-11-p0-15-init-agent-in-ctor.py` 的计时窗口（构造期与首拍分段）与
  `2026-11-11-p0-14-cold-start-silent.py` 的旧启动链兼容兜底。

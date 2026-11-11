# 2026-11-11 P0 逐条实测核查（第二批 · 含跨线数字对齐）

> 范围：`docs/research/2026-11-11-roadmap.md` §1 中除 P0-03/04/10/11 之外的 13 条。
> P0-03/04/10/11 见 `2026-11-11-p0-static-inference-check.md`。
> 性质：**只新增脚本与文档，未改产品代码**；ACARS 乱码帧只登记、不修（解码线负责）。
> 三分类：**真 P0（还能复现）／已被修掉（附修复提交号 + 对照组）／无法判定（写明缺什么前置）**。
> 规则：没有对照组的只能记「无法判定」，不许记「已修」。

## 1. 口径与环境（四轮）

| 轮次 | 快照 | 说明 |
|---|---|---|
| 第一轮 | `c899eb51` | 13 条首测；此后又有 6 笔提交落地，部分条目状态已变 |
| 第二轮 | `a2837671` | 跨线数字对齐后的复核 |
| 第三轮 | `33b8de5e` | 另立「首拍代价」、快照纪律、触控模式单列 |
| 第四轮 | `c3b48881` | 触控修复（`68962abc`）已进链后重测 P0-16b；首拍加真屏指纹。本文「当前」结论以本轮为准 |

第二轮新增的 6 笔提交（都在 `c899eb51` 之后）：`bedf3c4d`（W26 连接后台化）、
`1fdf1958`（W24 内核装载延后）、`0ce78fb3`（主题改 QPalette）、
`07167624`（卫星跟踪启停读 policy）、`7501d5d4`（星历陈旧度）、`eb7adea1`（断开残留）。
第三轮快照 `33b8de5e` 在 `a2837671` 之后又含 `bb4f2de9`（主题触控后缀按前缀判同）、
`bf7f6388`（右栏）、`dc733452`（控制列折叠）等，但**不含**触控修复。
第四轮快照 `c3b48881` 已含 `68962abc`（`_apply_theme` 只记 `theme._qss()`、
`_apply_touch_stylesheet` 未变即返回）——`git merge-base --is-ancestor 68962abc c3b48881` 返回 0。

### 1.1 快照硬纪律（两轮补全）

三条一起执行，缺一条读数就不可比：

1. **报性能数字必须带 commit 短哈希。** 同一台机器、同一脚本，不同提交可以差 10 倍；
   今晚就发生过一次（4527 ms 属 `c899eb51`，498–954 ms 属 `a2837671`，两边都以为对方量错）。
2. **报「现状」必须用当前 HEAD 重测；历史读数只能标为历史。** P0-16b 就是活例子：
   本文上一版把 `33b8de5e` 的「触控每切 1 次应用级重设、2.0–2.8 s」写成现状，
   而修复 `68962abc` 已经在链上——旧快照的读数只能进「历史」列，不能进「当前」列。
   同理，快照一旦落后，先 `git merge-base --is-ancestor <修复提交> <快照>` 确认修复在不在里面。
3. **报毫秒数必须标尺子。** 本轮起所有读数分两种尺子：
   `offscreen`（`QFontDatabase.families()==0`，行高 12 px）与
   **真屏指纹**（`QT_QPA_PLATFORM=windows` + `QT_SCALE_FACTOR=1` +
   `QT_ENABLE_HIGHDPI_SCALING=0` + 生产字体 `MiSans 10pt`，行高 27 px）。
   两者不可换算、不可互相代表（§3.5）。

下表原样保留，不再合并（`尺子` 列是本轮补的）：

| 快照 | 尺子 | 主题（鼠标） | 主题（触控） | 说明 |
|---|---|---|---|---|
| `c899eb51` | offscreen | 3269–8847 ms（两次运行；第一轮 4527 / 4387 / 3269） | 未测 | 修复前，1575 控件 |
| `20f64d35` | offscreen | 5814–8027 ms（1585 控件） | 未测 | `0ce78fb3^` 对照组 |
| `a2837671` | offscreen | 498–954 ms（1620 控件） | 3566–6848 ms（每切 2 次应用级重设） | 第二轮「HEAD」 |
| `33b8de5e` | offscreen | 207–401 ms（1626 控件） | 2029–2849 ms（每切 1 次应用级重设，QSS 每次 +528 字符）——**历史** | 含 `bb4f2de9`，触控修复 `68962abc` 尚未进链 |
| `c3b48881` | offscreen | 369–512 ms（1626 控件） | **225–454 ms（应用级 0 次，QSS 恒 20655 字符）** | 第四轮 HEAD，`68962abc` 已在链上 |

没有哈希的性能数字一律视为无效读数；引用时写「<毫秒数> @ <短哈希> @ <尺子>」。
**跨快照只认量级**：并行跑两个 1600 控件窗口时，触控读数可被推到 5.6 s
（另一进程抢 CPU/GPU），同快照重复跑也能差 1 倍以上——所以不允许拿单次读数做
「差 10 倍」的判断，必须先排除哈希、尺子、并发三件事。

其余环境：Windows / Python 3.14.0 / PySide6；默认 `QT_QPA_PLATFORM=offscreen`
（本项目重定向后 DPR 1.0、字体库 0、行高 12 px），P0-13 与 P0-15b `--screen` 用 `windows`；
`HOME/USERPROFILE/APPDATA/LOCALAPPDATA` 全部重定向到临时 profile；
P0-13 用 `QT_ENABLE_HIGHDPI_SCALING=0` 构造 DPR 1.0；P0-15b `--screen` 另加
`QT_SCALE_FACTOR=1` + MiSans 10pt（真屏指纹，行高 27 px，见 §3.5）。
退出码：**0 = 复现成功／1 = 未复现／2 = 无法判定**。

## 2. 三分类总表（四轮复核后）

| 条目 | 第一轮 `c899eb51` | 第二轮 `a2837671` | 第三轮 `33b8de5e` | 第四轮 `c3b48881` | 最终分类 | 修复提交 / 缺什么 |
|---|---|---|---|---|---|---|
| P0-01 dead TLE 仍闭环跟踪 | 0 真 P0 | 0 真 P0 | 0（仍复现） | 沿用 | **真 P0** | `07167624` 只闸了「开始跟踪」，逐拍跟踪环仍未闸 |
| P0-02 四套卫星清单不同源 | 0 | 0（交集 4/并集 8） | 0（缓存目录 3 种） | 沿用 | **真 P0（残余）** | `f94d6bf0`/`221fbce2` 收敛前三份，downlink 表仍独立 |
| P0-05 频谱设置重启不还原 | 1 | 1（三键全 OK） | 1 | 沿用 | **已被修掉** | `685e8f50`；对照 `d77d9b78` 退出码 0 |
| P0-06 导入即全量覆盖 | 1 | 1（5 哨兵不变） | 1 | 沿用 | **已被修掉** | `6f8f5fcf`；对照 `a1506664` 退出码 0 |
| P0-07 恢复默认重置运行态 | 1 | 1（运行态全保持） | 1 | 沿用 | **已被修掉** | `59eb6754`+`905d5483`；对照 `d372f3e7` 退出码 0 |
| P0-08 内部停止不复位 / 假完成 | 1 | 1（三路径全对） | 1 | 沿用 | **已被修掉** | `bf06e9a4`+`f81f1301`+`c2ce967a`+`bc199e01`；对照 `82bb5421` 退出码 0 |
| P0-09 sidecar 中心频率不更新 | 0 | 0（仍 145.5 MHz） | 0（偏 1.0 MHz） | 沿用 | **真 P0** | — |
| P0-12 `_sky_to_screen_opt` 未定义调用 | 1 | 1（调用 0/定义 0） | —（沿用） | 沿用 | **无法判定** | 全历史无该符号，无修复提交可回退 |
| P0-13 窄宽闸门漏测 topBarDetail | 1 | 2（闸门闪退） | 2（仍闪退） | 沿用 | **已被修掉**（依据 `c899eb51`） | `e0900f6e`+`ff48f7b9`；对照 `0aaa6afe` 退出码 0；复测被闪退挡住（§4.4） |
| P0-14 冷启动无首屏反馈 | 1 | 1（splash 185 ms） | 1 | 沿用 | **已被修掉** | `a52ce510`；对照 `1f72f0c3` 退出码 0 |
| P0-15 构造期同步 init_agent | 0 | 1（构造期 0 次，首拍 2183 ms） | 1（构造 1147–2500 ms，构造期 0 次） | 沿用 | **已被修掉（构造期）** | `1fdf1958`；对照 `28a59c87` 退出码 0；**首拍代价另立 P0-15b（真 P0）**，见 §3.4 |
| P0-16 切主题无反馈 | 0（4527/4387/3269 ms） | 1（810/954/498 ms） | 鼠标 1（207–401 ms）；触控 0（2029–2849 ms） | **鼠标 1 / 触控 1（应用级 0 次，225–454 ms）** | **已被修掉（鼠标 `0ce78fb3` + 触控 `68962abc`）** | 对照 `20f64d35`（鼠标）/ `33b8de5e`（触控）退出码 0；触控四行见 §3.1.1 |
| P0-17 连接同步阻塞 GUI | 0（心跳 1 次） | 1（心跳 32 次） | 1 | 沿用 | **已被修掉** | `bedf3c4d`；对照 `35e09799` 退出码 0 |

最终（第四轮口径）：真 P0 4 条（P0-01/02/09 + P0-15b 首拍尾部）、
已被修掉 10 条（P0-05/06/07/08/13/14/15/16/16b/17）、无法判定 1 条（P0-12）、
只登记不修 1 条（P0-13b 闸门闪退，证据已转崩溃线）。

> 更正上一轮的两处结论：**P0-15 不是「构造期仍同步」**（第一轮把 `processEvents()` 也算进构造期，
> 见 §3.3）；**P0-16/P0-17 的第一轮数字是修复前的快照值**，不应与性能线的修复后数字直接比较。
> 第三轮补充：**P0-16 的「已修」只覆盖鼠标模式**；触控模式当时仍 2.0–2.8 s，必须单列（§3.1.1）。
> 第四轮更正：**触控已由 `68962abc` 修掉**，上一版那条「没消除」的说法作废，只作历史读数保留。

## 3. 跨线矛盾对齐与另立条目

### 3.1 切主题差 10 倍：不是入口/负载，是提交不同

同一脚本 `scripts/repro/2026-11-11-p0-16-theme-switch-cost.py`，同一隔离 profile，
入口逐一测「直调 `MainWindow._apply_theme`」与「工具栏 `theme_combo` 信号路径」，
主题序列固定 `dark/default/dark`，读数四行（**鼠标/触控必须分行，不许合并**）：

| 快照 | 尺子 | 模式 | 控件数 | `setStyleSheet`（控件级/应用级） | 墙钟 | CPU 时间 |
|---|---|---|---|---|---|---|
| `c3b48881`（第四轮 HEAD） | offscreen | 鼠标 | 1626 | 7 / **0** | 369 / 399 / 512 ms | 500 / 562 / 953 ms |
| `c3b48881`（第四轮 HEAD） | offscreen | 触控 | 1626 | 7 / **0** | 225 / 266 / 279 ms（另一次 360 / 410 / 454） | 234 / 266 / 281 ms |
| `33b8de5e` | offscreen | 鼠标 | 1626 | 7 / **0** | 300 / 319 / 362 ms（另一次 207 / 233 / 380） | 438 / 437 / 453 ms |
| `33b8de5e` | offscreen | 触控 | 1626 | 13 / **3**（每切 1 次）——**历史** | 2029 / 2326 / 2849 ms | 2094 / 2401 / 2641 ms |
| `a2837671` | offscreen | 鼠标 | 1620 | 7 / **0** | 498 / 810 / 954 ms | 516 / 1047 / 1125 ms |
| `a2837671` | offscreen | 触控 | 1620 | 38 / **6**（每切 2 次） | 4377 / 5119 / 6848 ms | 4969 / 5846 / 6547 ms |
| `20f64d35`（`0ce78fb3^`） | offscreen | 鼠标 | 1585 | 39 / **3** | 5814 / 5956 / 8027 ms | 4719 / 5956 / 7438 ms |
| `c899eb51`（第一轮） | offscreen | 鼠标 | 1575 | 13 / **3** | 3269 / 4527 / 8847 ms | 4203 / 4766 / 5828 ms |

> 表内是三次切换的逐次数值（不是 min/median/max 三档）；同一快照重复跑可差 1 倍以上
> （并行跑两个 1600 控件窗口时触控读数可到 5.6 s），所以**跨快照比较只认量级**，
> 且必须带哈希。
> 本表**全部是 offscreen 尺子**（`QT_QPA_PLATFORM=offscreen`，行高 12 px、字体库 0）：
> 它适合「同尺子跨快照」比较，绝对值不代表真屏体感（§3.5）。

- 入口 B（下拉信号）与入口 A 同量级（`33b8de5e` 鼠标：5–380 ms），**入口不是变量**；
- 真正的变量是 `0ce78fb3`「主题色改走 QPalette，不再重设 QSS」：应用级 `setStyleSheet`
  从每次 1 次降为 0，墙钟从 5–8 s 降到 0.2–0.4 s（`33b8de5e`）；
- 性能线的 386–540 ms 与 `a2837671` 的 498–954 ms 同一量级，差异来自机器负载
  （本轮同时有别的 Qt 进程在跑）；
- 第一轮的 4527/4387/3269 ms 是 `c899eb51`（修复前）读数，**不是 HEAD**。数字不改口，
  但必须标注快照（§1.1）。

### 3.1.1 触控模式单列（P0-16b：已修）

口径照旧四行（控件数 / 应用级 `setStyleSheet` 次数 / 墙钟 / CPU），**鼠标与触控分行、不合并**：

| 快照 | 状态 | 控件数 | 应用级 `setStyleSheet` | 墙钟 | CPU | QSS 长度 |
|---|---|---|---|---|---|---|
| `a2837671` | 历史（每切 2 次） | 1620 | 每次 2 次（3 次共 6） | 4377 / 5119 / 6848 ms | 4969 / 5846 / 6547 ms | 递增 |
| `33b8de5e` | 历史（`bb4f2de9` 2→1 次） | 1626 | 每次 1 次（3 次共 3） | 2029 / 2326 / 2849 ms | 2094 / 2401 / 2641 ms | 20832 → 21360 → 21888（+528/次） |
| `c3b48881` | **当前 HEAD（`68962abc` 1→0 次）** | 1626 | **0 次** | **225 / 266 / 279 ms**（另一次 360 / 410 / 454） | 234 / 266 / 281 ms | **恒 20655** |

修法（`68962abc`，`desktop/main_window.py`）：

1. `_apply_theme` 只记主题基串 `theme._qss()`，不再读 `app.styleSheet()`
   （触控模式下后者已含后缀，记成基串就会每切一次再叠一份，+528 字节/次）；
2. `_apply_touch_stylesheet` 记住上次挂上去的模式（`_touch_qss_applied`），模式未变直接
   `return`；只在主题应用真的重设过样式表（此时恰好等于基串、后缀被冲掉）时允许重挂一次；
3. 新增 `tests/test_theme_qss_growth.py`：触屏连切 20 次 `len(app.styleSheet())` 必须恒定。

对照与反证：

- 历史基线 `33b8de5e`（`--touch`）退出码 0 = 仍慢（1 次应用级重设、2.0–2.8 s）；
  当前 HEAD 退出码 **1 = 未复现**；
- 性能线证据 `docs/perf-evidence/2026-11-11/theme_touch_qss_no_growth.json`：
  触控 5 主题 0 次应用级、198.5–236.3 ms；连切 20 次 QSS 恒 20655 字节、`app_setStyleSheet` 全 0；
  反证把 `_theme_qss` 改回读 `app.styleSheet()` → 21,183 → 31,743（20 × 528 字节）；
- 本线在 `c3b48881` 快照上跑 `pytest tests/test_theme_qss_growth.py`：**2 passed**。

> 上一版「`bb4f2de9` 只把 2 次降到 1 次、**没有消除**」这句话只适用于 `33b8de5e` 当时；
> 现状是 `68962abc` 已消除，**不得再当现状引用**（这正是 §1.1 第 2 条的由来）。

### 3.2 P0-17 与 W26：W26 已覆盖手动路径，不需要重开

| 项 | 结论 |
|---|---|
| 手动入口 | 连接按钮 → `MainWindow._connect_backend`（`desktop/main_window.py:6104`） |
| W26 改了什么 | `bedf3c4d` 新增 `_connect_backend_in_background`（`6053`）：`threading.Thread("sdr-connect")` + `QEventLoop` + 30 ms 轮询 + 20 s watchdog；并把 `_connect_backend` 里原来的直接 `backend.connect()`（旧 6059）改为 `ok = self._connect_backend_in_background(backend)`（现 `6156`） |
| 为什么首测没覆盖 | 首测快照 `c899eb51` 早于 `bedf3c4d`，那时 `_connect_backend` 仍是同步调用、仅前置一次 `processEvents()` |
| 实测（`a2837671`） | `_connect_backend` 总耗时 2659 ms，期间心跳 **32** 次（空转正例 31 次）→ GUI 线程未被独占 |
| 对照组 `35e09799`（W26 父提交） | 总耗时 2824 ms，心跳 **1** 次，退出码 0（复现）。完整哈希 **`35e09799d1b84eaf1df7df8c65876b7234c2df3b`**，已核 = `bedf3c4d^`（W26 的直接父提交） |
| 启动自动连接 | `_auto_enumerate_and_connect_async`（`5821`）走 `already_connected=True`，硬件 connect 已在后台完成，本来就异步 |

结论：P0-17 已被 `bedf3c4d` 修掉，W26 无需重开；第一轮的 2813 ms/1 次心跳是修复前读数。

### 3.3 P0-15 来源：`_ensure_agent_kernel` 由能力矩阵常驻引入，W24 只挪了同一个调用点

- `git log --all -S "_ensure_agent_kernel" -- desktop/` 只有一笔：`c47cdce9`
  「九条用户口径收口 —— 能力矩阵常驻」，它新增 `_build_capability_drawer()`，
  并在构造期调用 `self._ensure_agent_kernel()`（后者对空 panel 执行 `panel.init_agent()`）。
- W24 `1fdf1958` 改的是**同一行**：`self._ensure_agent_kernel()` →
  `QTimer.singleShot(0, self._ensure_agent_kernel)`（现 `main_window.py:4383`）。
- 第二轮 `a2837671` 实测（分段计时）：构造期 **0 次**、构造返回时 `ai_panel.agent is None`、
  构造 1147 ms；**首个事件循环拍** 装载 1 次、**2183 ms**。第三轮 `33b8de5e` 复测同结论，
  首拍 796–2329 ms（读数随机器负载/文件缓存波动），归因与「再往后挪一档」的模拟见 §3.4。
- 对照组 `28a59c87`（W24 父提交）：构造期 1 次、**6216 ms**，退出码 0（复现）。
- 上一轮「构造期仍有 2259 ms」的来源：第一版探针把 `app.processEvents()` 放进构造计时窗口，
  0 延迟定时器在那一拍执行，于是被算成「构造期」。本轮已把两段拆开（脚本内注释说明），
  结论改为「构造期已修，成本挪到首拍」——**延后不是消失**，首拍代价见 §3.4，另立 P0-15b。
- 另一处 `ai_panel.init_agent(...)` 在 `main_window.py:7940`（`_load_ai_config`），
  只在有 `api_key` 且用户触发时执行，不属构造链，未计入。

### 3.4 另立 P0-15b：首拍代价（仍复现）

脚本：`scripts/repro/2026-11-11-p0-15b-first-tick-cost.py`（不开参数 = 父进程跑三个独立子进程：
`baseline` / `profile` / `deferred`）。快照 `c3b48881`，退出码 0 = 首拍 ≥ 1000 ms（复现）。
`--screen` / `--show` 两个开关（正交）分别切尺子与生产顺序，见 §3.5。

两段计时 + 可复跑读数（offscreen，同一脚本多次，机器负载有波动，故给区间）：

| 段 | 读数 | 说明 |
|---|---|---|
| 构造期 | 1147–2500 ms，`_ensure_agent_kernel` **0 次**，`agent is None` | W24 后构造期不再建内核 |
| 首个事件循环拍 | **796–2329 ms**，其中内核 1.2–1.6 s（占 90%+） | 窗口已出来，但首帧后的交互被顶住 |

取证一：首拍里谁在花时间（`--mode profile`，cProfile 归因；墙钟被放大 1.7–1.9×，
只取占比）：

| 归因 | 一次代表读数 | 占 profiled 首拍 |
|---|---|---|
| `_ensure_agent_kernel` 累计 | 3115 ms | 96% |
| 其中 `init_agent()` | 3107 ms | 95% |
| 其中 importlib 累计 | 1958 ms | 60% |
| 其中 `builtins.open` / `listdir` | 无 > 1 ms 条目 | ≈0 |

导入大头（模块级 cumtime，父子有重叠）：
`mbdsdr_ai/sat_image_processing.py` 980 ms → `scipy.ndimage` 948 ms；
`mbdsdr_ai/orbit_perturbation.py` 633 ms / `orbit_dynamics.py` 632 ms → `scipy.integrate` 631 ms。
结论：**首拍成本 60% 是 `init_agent` 触发的模块导入（含 .pyc/.pyd 载入），
没有独立的文件 IO 热点**；剩下约 35% 是内核自身建 496 个工具对象。
另一次运行还观察到 `sdr_tools` 导入链里 `rtlsdr.rtlsdr.open` 467 ms——那是 USB 设备枚举，
不是文件 IO，随硬件环境波动，未计入上表。

取证二：能不能再挪到「用户第一次打开 Agent 面板」（`--mode deferred`，探针用
`QTimer` 代理截获 `singleShot(0, _ensure_agent_kernel)`，改在
`_show_capability_drawer()` 里执行；不改产品代码）：

| 形态 | 首拍 | 首次打开能力矩阵 |
|---|---|---|
| 基线（首拍装载，offscreen） | 796–2329 ms | — |
| 模拟：首次打开才装载（offscreen） | **85–126 ms** | **1299 ms**（内核 1247 ms + 抽屉刷新 ≈52 ms） |
| 基线（首拍装载，真屏指纹 + show） | **1568–2418 ms** | — |
| 模拟：首次打开才装载（真屏指纹 + show） | **272–311 ms** | **1433 ms**（内核 1351 ms + 抽屉刷新） |

取证三：挪走后的代价归属。内核装载的 1.2–1.3 s 原样转移到用户点开能力矩阵的那一刻，
且只付一次；不进首屏、不挡主题/录音/连接这些首批交互。代价是能力矩阵首开要多等 1.3 s，
可再叠加「工具清单惰性构建 / 后台线程」处理，但不属本条。

建议（交给 roadmap 线定档）：把 `QTimer.singleShot(0, self._ensure_agent_kernel)`
改为「首次打开能力矩阵时装载」，P0-15 保持「构造期已修」，P0-15b 单列跟踪首拍。

### 3.5 尺子：offscreen 不能代表用户体感（本节新增）

offscreen 下 `QFontDatabase.families()==0`，字体按方框度量（实测：`Sans Serif` 9 pt、
行高 **12 px**、DPR 1.0、逻辑 DPI 96），控件尺寸全靠 fallback。真屏指纹
（`windows` + `QT_SCALE_FACTOR=1` + `QT_ENABLE_HIGHDPI_SCALING=0` +
`app.setFont(get_app_font())` 即 MiSans 10pt）实测：字体库 **394** 个、`MiSans 10.0 pt`、
行高 **27 px**、DPR 1.0、逻辑 DPI 144（本机系统缩放 150%）。**行高差 2.25 倍**，
与性能线「同一批控件在两种尺子下高度差 1.03–1.70 倍」互相印证。

同一快照 `c3b48881`、同一隔离 profile、每格 2 次（`--mode baseline`，进程独立）：

| 尺子 | show | 构造期 | 首拍 | 首拍内核 | 内核占比 |
|---|---|---|---|---|---|
| offscreen | 否 | 1566 / 2452 ms | 2446 / 2847 ms | 2300 / 2662 ms | 93–94% |
| offscreen | 是（生产顺序） | 1450 / 1547 ms | 1598 / 1976 ms | 1449 / 1794 ms | 91% |
| 真屏指纹 | 否 | 3104 / 3286 ms | 2609 / 3065 ms | 2315 / 2689 ms | 88–89% |
| 真屏指纹 | 是（生产顺序） | 2277 / 2398 ms | 1568 / 2239 ms | 1159 / 1798 ms | 74–80% |

读法与判定：

- **构造期：真屏比 offscreen 高 1.3–2.0×（+34%…+110%）**，同 show 条件逐对比较全部 >15%，
  这是尺子的系统性差——真字体下每个控件的 sizeHint/布局都更贵；
- **首拍：差值 −2%…+19%**，被机器负载淹没（offscreen 自身两次就差 16%，真屏两次差 43%）；
  首拍的 74–94% 是内核（`init_agent` 的模块导入），**与字体无关**，所以「真屏一定慢 N 倍」
  不成立，能说的只有「offscreen 的绝对值不可引用」；
- 结论：**offscreen 不能代表用户体感**（构造期差 >15%，且单次首拍差也常 >15%）。
  跨快照比较仍可用 offscreen（同尺子），但凡是要对用户承诺的绝对毫秒数、
  尤其是 **W89「首个事件循环拍 ≤800 ms」判据，一律钉在真屏指纹 + 生产顺序（show）上**；
- 真屏口径下 W89 判定：基线首拍 **1568–2418 ms（热身，4 次）/ 3293 ms（新工作树首跑、无 `__pycache__`）**，
  **超 800 ms 判据 2–4 倍，不达标**；模拟「首次打开能力矩阵才装载」后首拍 **272–311 ms**，
  首次打开 770–1433 ms——过判据，但打开要等 0.8–1.4 s；
- 冷/热必须标：新 worktree 没有 `__pycache__`，首次运行要多编译 .pyc，导入段会被放大
  （实测同配置 3293 → 1618 ms）。用户「装完第一次启动」就是冷态，**不能只报热态**。

## 4. 逐条证据（第四轮读数）

### 4.1 真 P0

- **P0-01**（`33b8de5e` 复跑，退出码 0）：`tracking_policy_for` 返回 `allow_tune=False`、仰角 45° 时
  `_on_tune_sdr` 仍被调用 1 次（145.8 MHz/FM）；显式读 policy 的对照 0 次。
  `07167624` 新增 `_tracking_policy_for`(12224)/`_confirm_tracking_policy`(12242) 并闸住
  `_start_satellite_tracking`(12253)，但 `_poll_satellite_track`(12358) 逐拍仍不读 policy。
- **P0-02**（`33b8de5e` 复跑，退出码 0）：3 份 catalog 6/6 一致、无同名 CATNR 冲突；downlink 表多
  `AO-73`/`FOX-1B`、缺 `METEOR M2`/`FY-3D` → 交集 4 / 并集 8；TLE 缓存目录 `~/.mbdsdr/tle`、
  `~/.mbdsdr/tle_cache`、`~/.mbdsdr/new_spacetime_tle` 三种口径并存。
- **P0-09**（`33b8de5e` 复跑，退出码 0）：`_open_record_segment(...145.5e6)` → 调谐 146.5 MHz → 停止后
  sidecar 仍 `center_freq_hz=145,500,000`、`center_freq_changes=None`，回放错位 1.0 MHz。
- **P0-15b 首拍**（`c3b48881`，退出码 0）：读数与归因见 §3.4/§3.5；构造期已 0 次装载，
  首拍 offscreen 796–2847 ms、真屏指纹 1568–2418 ms（冷态 3293 ms），其中 74–94%
  是内核触发的模块导入。

### 4.2 已被修掉（均带对照组）

| 条目 | HEAD 读数 | 对照组（基线退出码 0 的现场） |
|---|---|---|
| P0-05 | `fft_size=4096`、`wf_colors='soft'`、`peak_rel_db=21.0` 全 OK | `d77d9b78`：1024 / sdrpp / 6.0 |
| P0-06 | 5 哨兵全不变（`absent=[…]` 合并语义） | `a1506664`：98M / 10000 / '' / 0.7 |
| P0-07 | 运行态 5 键全保持，`fft_size 4096→2048` | `d372f3e7`：整组被重置 |
| P0-08 | A `completed`；B `failed`、无「录制完成」；C 仅 1 次警告 | `82bb5421`：按钮 True/True、B 结果态 None、仍写「录制完成」 |
| P0-13 | `c899eb51` 三档滑杆列 0 px；HEAD 闸门闪退（§4.4） | `0aaa6afe` 旧闸门无 `states`；注入式（旧树+新尺 @DPR1.0）= 6/7/**24** |
| P0-14 | splash 185 ms、窗口 10663 ms | `1f72f0c3`：splash 6890 ms、窗口 14503 ms |
| P0-15 | 构造期 0 次、`agent is None`；首拍见 P0-15b（真屏 1568–2239 ms） | `28a59c87`：构造期 1 次 / 6216 ms |
| P0-16（鼠标） | `c3b48881`：369–512 ms、app 级 `setStyleSheet` 0 次（`33b8de5e` 207–401 ms 同量级） | `20f64d35`：5814–8027 ms、每切 1 次 |
| P0-16（触控） | **`c3b48881`：225–454 ms、app 级 0 次、QSS 恒 20655**（`33b8de5e` 的 2029–2849 / 每切 1 次为历史） | `a2837671`：每切 2 次、3566–6848 ms；`33b8de5e`：每切 1 次、2029–2849 ms |
| P0-17 | 见 §3.2 | `35e09799d1b84eaf1df7df8c65876b7234c2df3b`：2824 ms、心跳 1 次 |

### 4.3 无法判定

- **P0-12**：HEAD 调用 0 / 定义 0；注入式检测器报 1/0（探针有效）；
  `git log --all -S "_sky_to_screen_opt" -- desktop/` 无任何提交——从未入库，
  无修复提交可回退，只能记工作区现象。**roadmap 记「无法判定」，不得记「已修」**。

### 4.4 测量环境陷阱（会把结论判反，先看）

1. **DPR 决定 P0-13 有没有样本**：`6 / 7 / 24 px` 只在 DPR 1.0
   （`QT_ENABLE_HIGHDPI_SCALING=0`）构造得出来；DPR 1.5 下新旧尺子两态都是 0。
   不写 DPR 就断言「闸门 0 px = 正常」会被误导。
2. **HEAD 窄屏闸门稳定闪退**：直接跑 `scripts/ui_narrow_breakdown.py --json`，
   3 次全部 `0xC0000005`，栈在 `main_window.py:3788 eventFilter` →
   `threading.get_ident()` 抛 `SystemError: returned a result with an exception set`；
   该线程判定由 `09527ff5` 引入。属新发现，只登记不修（证据已转给查 `0xC0000005` 的线）。
3. **窗口析构 / 解释器收尾也会踩同一颗雷**：在探针里 `window.close()` 或正常退出时，
   `touch_helpers` 的事件会回到 `eventFilter`，同样 `0xC0000005`。
   P0-15b 脚本因此把结果先写盘、再用 `os._exit()` 退出，父进程只认结果文件，
   不拿退出码当判据——否则会「测到了却算无法判定」。
4. **cProfile 放大墙钟约 1.7–1.9×**：P0-15b 的 `baseline` 子进程给墙钟，
   `profile` 子进程只给占比，两者不可混读。
5. **不要 patch `builtins.__import__` 做导入计时**：实测会让 `eventFilter` 递归报
   `RuntimeError` 并踩到已删除的 `QVBoxLayout`；归因改用 cProfile。
6. **offscreen 不是真屏的缩小版**：`QFontDatabase.families()==0`、行高 12 px 对真屏 27 px，
   构造期系统性差 1.3–2.0×（§3.5）。凡是要对用户承诺「首屏多久能点」，必须用真屏指纹；
   offscreen 只用于同尺子跨快照比较。
7. **新 worktree 是冷态**：没有 `__pycache__`，首跑要多编译 .pyc，导入段被放大
   （同配置 3293 → 1618 ms）。用户装完第一次启动就是冷态，冷/热要分开报。

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

### 5.2 测量环境

DPR 1.0/1.5 的坑、`0xC0000005` 闪退、cProfile 放大与「不要 patch
`builtins.__import__`」全部收进 **§4.4**（结论会被判反的都在那里）。

## 6. roadmap 回填建议（仍未写入）

`docs/research/2026-11-11-roadmap.md` 仍被 roadmap合成线 占用（`file_claim` 未过期，
且该路径有未提交改动），按纪律不抢。逐行建议单元格见
`docs/pending-patches/2026-11-11-p0-roadmap-backfill-handoff.md`（已按第四轮改口更新），要点：

| 条目 | 建议写入「当前真实行为」的文本 |
|---|---|
| P0-01 | **仍复现**（`33b8de5e`，退出码 0）：`07167624` 只闸了开始跟踪，逐拍 `_poll_satellite_track` 未闸 |
| P0-02 | **仍复现（残余）**：交集 4 / 并集 8，TLE 缓存目录 3 种 |
| P0-05/06/07/08/14 | **已修**，附提交号与基线对照退出码 0 |
| P0-09 | **仍复现**：sidecar 偏 1.0 MHz（`33b8de5e`，退出码 0） |
| P0-12 | **无法判定**（符号全历史不存在，属未提交现场），不得记「已修」 |
| P0-13 | **已修**（`e0900f6e`+`ff48f7b9`），但 HEAD 复测被闸门闪退挡住；闪退另立条目 |
| P0-15 | **已修（构造期）** by `1fdf1958`；**首拍另立 P0-15b**（真屏 `c3b48881`：1568–2418 ms，退出码 0） |
| P0-16 | **鼠标 + 触控均已修**：鼠标 `0ce78fb3`（`c3b48881`：369–512 ms、app 级 0 次）；触控 `68962abc`（`c3b48881`：225–454 ms、app 级 0 次、QSS 恒 20655）。**触控旧读数 2029–2849 ms 只能标历史** |
| P0-17 | **已修** by `bedf3c4d`（`a2837671`：2659 ms、心跳 32 次），W26 无需重开 |

新增三条（建议 roadmap 单列，别塞回原条目）：
**P0-15b 首拍代价**（真屏判据）、**P0-16b 触控切主题（已修，`68962abc`）**、
**P0-13b 闸门 `0xC0000005`**（只登记，证据已转给查崩溃的线）。

## 7. 复跑

```powershell
# 快照（数字必须带哈希，见 §1.1；用当前 HEAD，先确认修复是否在链上）
git -C D:\mbdsdr\MBDSDR merge-base --is-ancestor 68962abc HEAD   # 0 = 触控修复已在链上
git -C D:\mbdsdr\MBDSDR worktree add --detach D:\mbdsdr\output\tmp\p0-head-c3b48881 c3b48881
$env:MBDSDR_REPRO_ROOT="D:\mbdsdr\output\tmp\p0-head-c3b48881"

# 主题：鼠标 / 触控各跑一次，各占一行（HEAD 期望都是 1=未复现）
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-16-theme-switch-cost.py
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-16-theme-switch-cost.py --touch
# 历史对照（触控旧状态：期望 0=仍慢）
$env:MBDSDR_REPRO_ROOT="D:\mbdsdr\output\tmp\p0-head-33b8de5e"
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-16-theme-switch-cost.py --touch

# P0-15 构造期（期望 1；对照 28a59c87 期望 0）
$env:MBDSDR_REPRO_ROOT="D:\mbdsdr\output\tmp\p0-head-c3b48881"
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-15-init-agent-in-ctor.py

# P0-15b 首拍（三段：墙钟 / 归因 / 模拟首次打开；期望 0=首拍仍 ≥1000 ms）
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-15b-first-tick-cost.py            # offscreen
python D:\mbdsdr\MBDSDR\scripts\repro\2026-11-11-p0-15b-first-tick-cost.py --screen --show  # W89 判据用这行

# P0-17（期望 1；对照 35e09799 期望 0）
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
- **尺子分离**：offscreen 读数只做同尺子跨快照比较；对用户承诺的绝对毫秒数与
  W89「首拍 ≤800 ms」判据只用真屏指纹（`--screen --show`），且冷/热分开报（§3.5）。
- 第三轮新增脚本：`2026-11-11-p0-15b-first-tick-cost.py`（首拍三形态）；
  扩展 `2026-11-11-p0-16-theme-switch-cost.py`（`--touch` + QSS 长度）。
- 第四轮：`2026-11-11-p0-15b-first-tick-cost.py` 加 `--screen` / `--show`（尺子与生产顺序
  正交）；P0-16b 在 `c3b48881` 重测为已修（`68962abc`）。
- 前两轮新增/修正：`2026-11-11-p0-16-theme-switch-cost.py`；
  `2026-11-11-p0-15-init-agent-in-ctor.py` 的计时窗口（构造期与首拍分段）；
  `2026-11-11-p0-14-cold-start-silent.py` 的旧启动链兼容兜底。

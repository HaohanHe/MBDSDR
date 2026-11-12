# 2026-11-11 性能与响应性缺口调研（只读）

> 证据状态：E4——本文件数字未逐条绑定原始件/复跑命令，引用前须复核（判据见 docs/evidence-standard.md）。
> 范围：用户可感知层（启动、首帧、页签/面板、连接、频谱绘制、长跑内存）。
> 性质：**只读调研**，未改任何产品代码。所有数字均为本机实测，未标注「推断」的条目为直接测量值。

## 0. 结论

1. **最大缺口是启动：进程起到可交互 4.36 s，到完整首帧 4.62 s，期间窗口不存在、也没有任何 splash/进度条**——用户看到的是 4 秒多的「双击没反应」。
2. 启动期的可归因大头：scipy 自检导入 **929 ms**、`main_window` 导入 **643 ms**、`MainWindow()` 构造 **2527 ms**。构造里最大单块是 **AI 面板 `init_agent()` 973–1079 ms**（占构造 26–32%），其次 `_build_mode_shell` 466–625 ms、右栏 20 个面板 + `_add_right_tab` 370–430 ms、事件过滤器在构造期处理 7 万次事件 404–475 ms。
3. **主题切换是唯一复现的 >1 s 交互冻结：1.59–2.65 s**。全部花在 `app.setStyleSheet()` 对 1480 个控件重新 polish，业务代码只占 ~4 ms。
4. 其余**交互操作全部 < 100 ms**（页签 0.6 ms、右栏面板 0.1–16 ms、命令面板 44.7 ms、星象仪进出 35–51 ms、缩放峰值 73 ms），说明「切页签卡顿」不是当前缺口。
5. **频谱重绘不是瓶颈**：默认 eco 档 100 ms 间隔、曲线 6.6 ms/帧；balanced 66 ms 间隔、曲线 12.1 ms/帧（占预算 18%），实测 13.3 fps（理论 15.15），存在约 12% 的定时器合并丢帧，但无 >100 ms 卡顿。
6. **10 分钟长跑无泄漏迹象**：RSS 356→362 MB 后回落到 ~301 MB 并保持平坦（后 5 分钟 +1.2 MB），句柄 1463→1440（无增长），线程 43→36。1 小时未测，不外推。
7. 仍有 4 处**主线程同步等待**会在特定场景冻结界面：连接设备（代码内实测 2.0 s）、扫频/断开 join 链（单线程 1.5–2.0 s，流水线 5 线程最坏 10 s）、设置落盘锁（最长 2.0 s）、MCP 停止（3 s）。

## 1. 测量条件（可复现）

| 项 | 值 |
|---|---|
| 时间 | 2026-11-11 20:05–20:55 CST |
| 代码 | `HEAD=a1506664`，工作区另有 35 个其他工作线的未提交改动（一并测量） |
| 机器 | Intel i5-13420H / 16 GB / Windows |
| 平台 | `QT_QPA_PLATFORM` 未设置 → **真实 windows 平台**，DPR=1.5，屏幕 1280×800（非 offscreen，字体与渲染路径真实） |
| 环境 | `MBDSDR_OFFLINE=1`（不碰硬件/网络）；`APPDATA/HOME/USERPROFILE` 与 QSettings 全部重定向到临时目录，设置文件为真实配置的副本（`record_dir` 改写为临时目录），未读写用户真实配置 |
| 负载 | 启动/交互测量为单进程；两项 10 分钟长跑并行，另有一个外部 Python 进程占用约 1 核 → **长跑的 CPU 百分比偏保守（偏高）**，内存/句柄趋势不受影响 |
| 计时口径 | 心跳定时器 10 ms，`hb_max` = 操作窗口内事件循环最大间隔（含操作后首次绘制）；`wall` = 槽函数自身耗时 |

## 2. 启动耗时分解

### 2.1 分阶段（3 次冷启动，取中位）

| 阶段 | run1 | run2 | run3 | 中位 | 说明 |
|---|---:|---:|---:|---:|---|
| 进程启动 | 0 | 0 | 0 | 0 | 探针 `perf_counter` 起点（不含解释器启动 ~80 ms） |
| `startup_paths` + 版本 | 13.9 | 16.9 | 14.0 | **14.0** | |
| 自检 risky imports（含 `scipy.signal`） | 1067.8 | 943.1 | 869.4 | **943.1** | 窗口出现前，纯阻塞 |
| → `import PySide6` | 1149.2 | 1023.8 | 946.3 | **1023.8**（+80.7） | |
| → `import themes` | 1153.4 | 1028.7 | 950.3 | **1028.7**（+4.9） | |
| → `import main_window` | 1979.5 | 1671.5 | 1658.9 | **1671.5**（+642.8） | 连带 `ai_panel`/`spectrum_widget`/`sdr_backend`/`sounddevice` |
| → `QApplication()` | 2003.8 | 1697.4 | 1683.9 | **1697.4**（+25.9） | |
| → 字体 + 主题 | 2005.8 | 1701.2 | 1686.0 | **1701.2**（+3.8） | |
| → `MainWindow()` 构造 | 4469.3 | 4084.7 | 4227.7 | **4227.7**（+2526.5） | 全同步，见 2.2 |
| → `show()` + 首个事件循环 | 4600.5 | 4199.9 | 4364.8 | **4364.8**（+137.1） | **≈ 可交互时刻** |
| → 首次整窗 `grab()` 完成 | 4875.5 | 4409.7 | 4621.0 | **4621.0**（+256.2） | 256 ms 是 1280×800@1.5x 的合成成本，不是用户等待 |

**冷启动到可交互 = 4365 ms（中位，区间 4200–4601 ms）。**

### 2.2 构造阶段内部（ctor 专项探针，2 次）

| 项 | 耗时 | 次数 | 备注 |
|---|---:|---:|---|
| `MainWindow()` 总 | 3623 / 4228 ms | 2 | 专项探针挂了 20 个类计时器且系统负载更高，故高于 2.1 的 2527 ms |
| `_build_central_widget` | 3124 / 3336 ms | 2 | 含下面多项 |
| **`AIPanel.init_agent()`** | **973 / 1079 ms** | 2 | 位于 `_build_capability_drawer()` 内，**构造期最大的单块** |
| `_build_mode_shell` | 466 / 625 ms | 2 | 其中 `_build_planetarium_page` 仅 14 / 23 ms；余量推断为 `setCentralWidget()` 重挂 + polish（**推断，未单独计时**） |
| `_add_right_tab` ×20 | 370 / 430 ms | 20 | 平均 18.5–21.5 ms/次；20 个面板全部 eager 构造 |
| `eventFilter`（构造期） | 404 / 475 ms | 69,973 / 69,962 次 | 主窗口级事件过滤器，平均 ~6 µs/次 |
| `_build_digital_decode_panels` / `_build_extended_panels` | 79–158 / 64–78 ms | 1 | |
| `SettingsPanel.__init__` | 141 / 167 ms | 1 | |
| `_build_kernel_panels` | 288 / 300 ms | 1 | |
| `ControlPanel.__init__` | 42 / 111 ms | 1 | |
| `WeatherPanel` / `SatTrackPanel` / `AdsbMapPanel` / `NewSpacetimePanel` | 7–59 ms | 1 | 单次构造，波动主要来自地图瓦片/字体缓存 |

> 归因说明：`_build_central_widget` 3124–3336 ms 中，明确可归因的是 agent 内核 973–1079、右栏 370–430、Settings 141–167；剩余约 1.3–1.6 s 分散在天空/频谱/地图/底部停靠区/布局 polish，未逐项定位（**未归因**，不臆测）。

### 2.3 导入耗时前 20（`python -X importtime`，按 cumulative 排序）

| # | cumulative ms | self ms | 模块 |
|---:|---:|---:|---|
| 1 | 816.6 | 49.4 | `main_window` |
| 2 | 654.6 | 37.1 | `scipy.signal._support_alternative_backends` |
| 3 | 616.7 | 0.8 | `scipy.signal._signal_api` |
| 4 | 273.1 | 20.2 | `ai_panel` |
| 5 | 263.9 | 0.7 | `scipy.signal._peak_finding` |
| 6 | 261.7 | 1.3 | `scipy.stats` |
| 7 | 258.1 | 7.4 | `spectrum_widget` |
| 8 | 241.9 | 2.5 | `constants` |
| 9 | 239.4 | 10.3 | `mbdsdr_ai.sdr_backend` |
| 10 | 225.6 | 0.7 | `scipy.signal._spline_filters` |
| 11 | 224.9 | 2.2 | `scipy.signal._signaltools` |
| 12 | 208.6 | 1.6 | `mbdsdr_ai.audio_out` |
| 13 | 207.0 | **196.1** | `sounddevice`（自身耗时最大，导入时即加载 PortAudio DLL） |
| 14 | 152.6 | 35.5 | `scipy.stats._stats_py` |
| 15 | 143.7 | 0.9 | `scipy.signal._ltisys` |
| 16 | 140.0 | 0.8 | `scipy.interpolate` |
| 17 | 131.3 | 1.5 | `mbdsdr_ai.model_manager` |
| 18 | 129.8 | 2.3 | `requests` |
| 19 | 121.0 | 2.4 | `scipy.interpolate._interpolate` |
| 20 | 116.6 | 0.6 | `scipy.signal.windows` |

> cumulative 含子模块，不能相加；`scipy.signal` 全家在真实启动顺序中位于窗口出现之前，是「空白期」的一部分。`sounddevice` 196 ms 自耗与 `requests` 130 ms 与「首屏无关」但被 `main_window` 导入链拖入。

### 2.4 首屏空白期

- `main.py` 在 `MainWindow()` 完成后才 `show()`；全仓无 `QSplashScreen`/splash 逻辑（grep 无命中）。
- 因此 **首屏空白期 ≈ 4365 ms**，且无进度提示；操作系统层面窗口尚未创建，任务栏无窗口。
- 首个事件循环到首帧绘制再 +137 ms；首帧绘制由中心区控件承担，`QMainWindow.paintEvent` 未被调用（探针 `first_paint_at_ms = -1`，属正常）。

## 3. 阻塞 GUI >100 ms 的操作清单（实测优先，静态证据补充）

| 操作 | 实测冻结 | 复现性 | 证据 | 根因（文件:行） |
|---|---:|---|---|---|
| 启动（进程→窗口可交互） | **4365 ms** | 3/3 | `startup_1..3.json` | 2.1/2.2；无 splash |
| 切换主题（默认↔深色） | **1592–2651 ms** | 2 次 ×4 次切换 | `theme_1.json`、`ops2_1/2.json` | `theme.apply()` → `app.setStyleSheet()` 对 1480 控件重 polish（`main_window.py:5263` 起，`themes.py` apply） |
| 连接设备（RTL-SDR 原生 connect） | **2000 ms** | 代码内记载的本机实测 | `main_window.py:6042-6043` 注释 | 同步阻塞，仅 `processEvents()` 一次；本次无设备未复测 |
| 频谱首次渲染（切到频谱页后第一帧） | **122 / 178 ms** | 2/2 | `ops2_1/2.json` | 首帧字体/渐变缓存冷启动；稳态仅 6.6–12.1 ms |
| 关闭「地点」面板 | **126.5 / 128.4 ms** | 2/2 | `ops2_1/2.json` | 隐藏+析构+地图重排的延迟阻塞（**推断**，未逐行定位） |
| 扫频结束 / 断开连接 | 单线程 wait 1500–2000 ms | 静态 | `main_window.py:6787`、`main_window.py:11163`、`receive_pipeline.py:700-705` | `_sweep_worker.wait(1500/2000)`；流水线 5 个线程各 `wait(2000)`，**最坏 10 s** |
| 停止 TX | join 2000 ms | 静态 | `main_window.py:5226` | `_tx_thread.join(timeout=2.0)` |
| 设置落盘（任一设置变更后 500 ms 防抖触发） | **2003 ms** | 1/1（锁竞争受控实验） | `lock_contention.json`、`settings.py:265`、`392-395`、`652-667` | 写盘在 GUI 线程；另一进程持锁时自旋到 2 s 上限后放弃本次写入 |
| 天气/卫星云图停止 | wait 1500 ms | 静态 | `weather_panel.py:706`、`satellite_image_panel.py:357` | GUI 槽内 `quit()+wait(1500)` |
| MCP 服务停止 | wait 3000 ms | 静态 | `mcp_worker.py:338-346` | GUI 线程 `thread.wait(3000)` |
| 窗口缩放 1280↔900 | 事件循环峰值 **72.9 ms**（<100） | 2/2 | `ops2_1/2.json` | 布局重排，未超阈值，列此作边界参考 |

> 未列入：OSM 瓦片缓存的 `os.walk`/`disk_bytes` 在下载线程执行（`osm_map.py:296-355`、`_finish`），已确认不在 GUI 线程；扫描器 `time.sleep(0.03)` 在 PSD worker 内。

## 4. 逐项实测：交互操作（`ops_1.json`，单项 1 次；括号为事件循环峰值）

| 操作 | 槽耗时 | 事件循环峰值 | 是否阻塞 |
|---|---:|---:|---|
| 左栏页签切换（星象仪↔频谱） | 0.6 ms | 11.2 ms | 否 |
| 右栏 20 个面板切换 | 0.1–16.2 ms | ≤29.5 ms | 否 |
| 面板隐藏 / 恢复（`_panel_actions`） | 0.0–0.1 ms | ≤10.3 ms | 否 |
| 打开命令面板 | 44.7 ms | 44.9 ms | 否（一次性） |
| 进入星象仪 | 35.3 ms | 41.0 ms | 否 |
| 退出星象仪 | 50.6 ms | 50.7 ms | 否 |
| 性能档 eco↔quality | 0.4 ms | ≤12 ms | 否（重配延迟到下次绘制） |
| 全屏 / 退出全屏 | 26.4 / 51.8 ms | ≤51.8 ms | 否 |
| 打开地点面板 | 27–38 ms | 38 ms | 否 |
| 关闭地点面板 | 3.4 ms（槽） | **126–128 ms** | **是（延迟）** |
| 关闭窗口（空闲、未连接） | 76.3 ms | 76.3 ms | 否 |
| 主题切换 | 1592–2651 ms | 同左 | **是** |

## 5. 频谱绘制与帧率（`spectrum_3.json`，1280×800，曲线区 417×238）

| 档位 | 定时器间隔 | 实测 fps（理论） | 曲线 p50 / p95 / max | 瀑布 p50 | 单核占用（绘制） |
|---|---:|---:|---:|---:|---:|
| eco（默认） | 100 ms | 9.33（10.0） | 6.59 / 8.11 / 8.48 ms | 1.18 ms | ~8% |
| balanced | 66 ms | 13.34（15.15） | 12.13 / 15.48 / 16.13 ms | 1.16 ms | ~20% |
| `generator.push_iq`（FFT 1024/384） | — | — | 2.14 / 2.78 ms 中位，最大 17.7 ms | — | 生产在 `FftWorker` 线程，不占 GUI |

结论：
- 「66 ms 是结构性下限」成立，但**默认档实际是 eco(100 ms)**，不是 66 ms；brief 中的「默认 66 ms」与代码不符（`perf_profile.py` 默认 `eco` → `_PERF_LEVELS` level 2 = 100 ms）。
- balanced 档 66 ms 预算下绘制 12.1 ms（18%），fps 13.3 < 15.15，说明约 12% 的定时器节拍被合并/丢弃，但**不产生 >100 ms 卡顿**。
- 曲线区只有 417×238；更大窗口下的绘制成本**未可靠测得**（`setFixedSize` 被布局覆盖，数据不可信），不做外推。

## 6. 10 分钟长跑（真实 windows 平台，空闲 vs 合成 IQ 每 50 ms 推送）

| 指标 | 空闲 | 合成推送 | 结论 |
|---|---:|---:|---|
| RSS @10 s | 359.2 MB | 356.1 MB | |
| RSS 峰值（t≈70–190 s） | 360.6 MB | 362.3 MB | |
| RSS @600 s | 301.1 MB | 302.5 MB | t≈230–250 s 发生一次工作集回收（**成因未归因**，两进程同时发生，疑与系统内存压力/工作集修剪有关） |
| 后 5 分钟 RSS 斜率 | +1.2 MB / 300 s | +1.2 MB / 300 s | 无持续增长 |
| 句柄 @10 s → @600 s | 1465 → 1440 | 1463 → 1440 | 无增长 |
| 线程 @10 s → @600 s | 44 → 37 | 43 → 37 | 后台池收缩，非泄漏 |
| CPU% | 7.3–7.7 | 7.5–10.0 | 两进程并行 + 外部 1 核负载，绝对值偏高 |
| 事件循环 10 分钟 p95 / max | 11.1 / 27.9 ms | 11.0 / 30.5 ms | 长跑期间无 >100 ms 停顿 |

GDI/USER 对象：`GetGuiResources` 探针返回 0（句柄伪句柄截断问题），**未采信**，故不报告。

## 7. 静态扫描：GUI 线程上的同步调用（已逐条核对调用线程）

| 位置 | 调用 | 线程证据 |
|---|---|---|
| `main_window.py:6042-6055` | 设备 `connect()`（注释：本机实测 2.0 s） | GUI 槽，仅前置一次 `processEvents()` |
| `main_window.py:6979-7015` | 连接后同步下发采样率/带宽/频率/PPM/增益/AGC | GUI 线程，逐项 await 后端 |
| `main_window.py:5226` | `_tx_thread.join(timeout=2.0)` | GUI 线程 |
| `main_window.py:6787`、`:11163` | `_sweep_worker.wait(1500/2000)` | GUI 线程 |
| `receive_pipeline.py:700-705` | 5 个线程逐个 `wait(2000)` | 由 GUI 槽调用 `stop()` |
| `settings.py:265,392-395,479-496` | 500 ms 防抖后在 GUI 线程写盘，锁等待上限 2.0 s | GUI 线程 |
| `weather_panel.py:706` / `satellite_image_panel.py:357` | `quit()+wait(1500)` | GUI 槽 |
| `mcp_worker.py:338-346` | `thread.wait(3000)` | GUI 槽 |
| `main_window.py:12362-12380` | NTP 已移后台线程 | 已确认非阻塞（背景说明） |

## 8. 缺口清单（按严重度排序，≤20 行）

| # | 严重度 | 缺口 | 实测/证据 | 影响 |
|---:|---|---|---|---|
| 1 | P0 | 冷启动 4.37 s，全程无窗口/无 splash/无进度 | 3 次 4200–4601 ms | 双击后 4 秒无反馈，易被当成未启动而重复双击 |
| 2 | P0 | `AIPanel.init_agent()` 在构造期同步执行 973–1079 ms（496 工具内核） | ctor ×2 | 占启动构造 26–32%，纯浪费在用户还没打开 AI 面板时 |
| 3 | P0 | 主题切换 1.59–2.65 s 全量 QSS 重 polish（1480 控件） | theme ×1、ops2 ×2 | 设置页切主题界面完全冻结，无忙碌提示 |
| 4 | P0 | 连接设备在 GUI 线程同步 2.0 s，只 `processEvents()` 一次 | 代码内实测注释 + 静态 | 「连接中」标签仍可能停更；点击后整个窗口不动 |
| 5 | P1 | 频谱首帧 122–178 ms（切页后第一次绘制） | ops2 ×2 | 切到频谱有一次明显顿挫 |
| 6 | P1 | 关闭地点面板延迟阻塞 126–128 ms | ops2 ×2 | 关面板有可感知的「粘手」 |
| 7 | P1 | 设置写盘在 GUI 线程，另一进程持锁时实测阻塞 2003 ms | `lock_contention.json` | 多开时改任一设置/关窗会卡满 2 秒，且本次写入直接放弃 |
| 8 | P1 | 断开/停止流水线 join 单线程 1.5–2.0 s，最坏 5×2 s | `receive_pipeline.py:700-705` | 断开时窗口假死，硬件异常时更久 |
| 9 | P1 | 20 个右栏面板 + Settings 全部 eager 构造 0.51–0.60 s | `_add_right_tab` 20 次 | 直接用启动时间换「打开即快」，可用懒加载换回 |
| 10 | P1 | 构造期事件过滤器 7 万次调用 404–475 ms | ctor ×2 | 占构造 11–15%，可在构造完成后安装 |
| 11 | P1 | balanced 档频谱绘制 12.1 ms/帧，实测 13.3 fps（理论 15.15） | spectrum ×1 | 12% 节拍丢失；高档位在更大窗口下风险更高（未实测） |
| 12 | P2 | 自检 scipy 全家导入 929 ms 在窗口出现前 | importtime + 分阶段 | 与 1 叠加，是空白期第二大户 |
| 13 | P2 | 窗口缩放峰值 72.9 ms（1280↔900） | ops2 ×2 | 未超 100 ms，但接近预算 |
| 14 | P2 | TX 停止 join 2.0 s | `main_window.py:5226` | 发射场景下停止/关窗可能卡 2 秒 |
| 15 | P2 | 天气 / 卫星云图 stop wait 1.5 s | 两个面板 | 关闭或刷新时短暂冻结 |
| 16 | P2 | MCP 停止 wait 3.0 s | `mcp_worker.py:344` | 关服务时明显卡顿 |
| 17 | P2 | 命令面板首次打开 44.7 ms | ops ×1 | 可接受，但可预热 |
| 18 | P2 | 星象仪进出 35–51 ms | ops ×1 | 可接受 |
| 19 | P2 | 长跑 t≈230 s 的 RSS 回落（362→301 MB）成因未归因 | soak ×2 | 不是泄漏，但无法解释的回收事件需一次对照实验 |
| 20 | P3 | 1 小时内存/句柄未测（本次上限 10 分钟） | soak ×2 | 不能据此声称 1 小时稳定 |

## 9. 未测项与限制

- **连接设备真实耗时**未复测：本机无可用 SDR（枚举 0 设备；冷枚举 215 ms / 温枚举 42 ms，`probe_enum` 实测），且真机 connect 会占用硬件，需独占窗口。报告采用代码内记载的 2.0 s 实测值。
- **录音/回放帧率**未测：需要音频设备与写盘，本次只做只读环境。
- **1 小时长跑**未测，仅 10 分钟；不外推。
- 频谱更大窗口下的绘制成本未可靠测量（布局覆盖了 `setFixedSize`），不外推。
- 构造期 `_build_central_widget` 内约 1.3–1.6 s 未逐项归因，报告中标为「未归因」，未臆测。
- 长跑 CPU 数字受并行负载影响偏保守（偏高）。

## 10. 证据文件

- `docs/perf-evidence/2026-11-11/startup_1.json`、`startup_2.json`、`startup_3.json` — 分阶段启动计时、页签清单、DPR
- `docs/perf-evidence/2026-11-11/ctor_1.json`、`ctor_2.json` — 构造期阶段与 20 个面板构造耗时、`init_agent` 计时
- `docs/perf-evidence/2026-11-11/ops_1.json` — 页签/面板/命令面板/星象仪/性能档操作耗时与心跳峰值
- `docs/perf-evidence/2026-11-11/ops2_1.json`、`ops2_2.json` — 缩放/主题/单帧 grab/全屏/地点面板，两次复现
- `docs/perf-evidence/2026-11-11/theme_1.json` — 1480 控件、4 次主题切换、`setStyleSheet` 归因
- `docs/perf-evidence/2026-11-11/spectrum_3.json` — eco/balanced 帧率、曲线/瀑布绘制分布、`push_iq` 耗时
- `docs/perf-evidence/2026-11-11/soak_idle.json`、`soak_feed.json` — 10 分钟 RSS/句柄/线程/心跳采样
- `docs/perf-evidence/2026-11-11/lock_contention.json` — 另一进程持锁时 `flush()` 的受控阻塞实验
- `docs/perf-evidence/2026-11-11/importtime.log` — `python -X importtime` 原始输出

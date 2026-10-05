# Phase38 · Stage3 — cpp/tests/ 大套件稳定

> 范围：仅 `cpp/tests/`、`cpp/CMakeLists.txt`（测试注册/标签）、本文件。
> 红线遵守：**未碰 `src/`（阶段2域）、未碰 `mobile/`**；无假数据；未 commit/push；只暂存本阶段文件。
> 基线：HEAD = a36d9ba，offscreen 全量 ctest **127/127，总 166.61s，0 失败 0 Not Run**（真跑，命令见下）。

## 0. 基线（先测后改，真跑）

```bash
cd cpp/build
QT_QPA_PLATFORM=offscreen ctest --timeout 120 -V
# => 100% tests passed, 0 tests failed out of 127
# => Total Test time (real) = 166.61 sec
```

权威耗时 Top（offscreen，真实跑出来的，非估算）：

| 时间(s) | 用例 | 类型 |
|---|---|---|
| 41.94 | e2e_smoke | DSP/e2e（loopback rtl_tcp） |
| 34.76 | engine_audio_e2e | DSP/e2e |
| 13.25 | engine_integration | DSP/e2e |
| 6.67 | scan_link_engine | DSP |
| 4.78 | apt | DSP |
| 4.77 | digital_e2e | DSP/e2e |
| 4.50 | control_hub_remote | control |
| 3.72 | network_audio_sink | audio |
| 3.66 | engine_empty_state | engine |
| 3.53 | engine_hotplug | engine |
| 2.71 | ui_integration | **MainWindow** |
| 2.61 | shortcuts | **MainWindow** |
| 1.84 | device_ui | **MainWindow** |
| 1.82 | phase21_empty_state | **MainWindow** |
| 1.79 | sky_time_slider | **MainWindow** |
| 1.57 | control_http_e2e_prod | **MainWindow** |
| 1.50 | ai_sessions_prod | **MainWindow** |
| <0.9 | ui_audit | **MainWindow** |

## 1. 关键诚实发现：~11s MainWindow / AMR 是 Python 套件的事，不直接迁移

phase10 `P4-stability.md` 的"每个重 UI 用例冷启动 MainWindow ≈11s（含 `amr.py` 内置训练数据特征提取 ~5s）"说的是**根目录 Python `tests/` + `desktop/main_window.py`**。在本 C++ ctest 套件里实测：

- **C++ 没有 AMR**：`grep -rni '\bAMR\b' cpp/src cpp/tests` = 0 命中。C++ AI agent（`src/ai/agent.cpp`）是 **LLM-only**：构造只 `config_.load()` + 起一个 `LLMWorker` 线程，无任何内置训练数据/特征提取。
- **offscreen 下完整 MainWindow 构造 <1s**：8 个 MainWindow 用例全部 ≤2.7s（含整窗+引擎+agent+控制HTTP）。墙钟大户是上表前 10 个 DSP/e2e，不是 UI。
- **MainWindow 析构已回收**（`main_window.cpp::~MainWindow`）：停 GNSS 线程并 wait、停 control-HTTP、`engine_->shutdown()+wait()`、停 scan/adsb timer、delete scanner——无 Python 那种"只 close 不 deleteLater"的泄漏。
- **无启动网络探测**：C++ 无 Python `_probe_network_default`（~1.5s socket 探测）的对应物；librtlsdr 在本构建被禁（stub），设备枚举秒回空。

结论：本批**不需要、也无对象去做"headless 跳过 AMR"的 src/ 改动**（见 §4 回补点）。

## 2. 交付1 — 拆 slow 用例（标签门控，不禁用任何用例）

在 `cpp/CMakeLists.txt` 末尾追加标签块（只 `set_tests_properties LABELS`，**不 DISABLE、不改 add_test 顺序**，故 127/127 基线不变）：

**拆分规则**
- label `mainwindow`：冷启动完整生产 `mbdsdr::MainWindow` 的用例 = 重 UI **集成**层（非纯单测）。共 8 个：
  `ui_integration, shortcuts, device_ui, phase21_empty_state, sky_time_slider, control_http_e2e_prod, ai_sessions_prod, ui_audit`。
- label `slow` = 上面 mainwindow 层 ∪ **实测 offscreen ≥3s 的 DSP/e2e** 10 个：
  `e2e_smoke, engine_audio_e2e, engine_integration, scan_link_engine, apt, digital_e2e, control_hub_remote, network_audio_sink, engine_empty_state, engine_hotplug`。

**用法（配置后 `ctest -N` 实测验证）**
```bash
cd cpp/build
QT_QPA_PLATFORM=offscreen ctest -LE slow   # 快循环 = 109 个（去掉 18 个重层，墙钟 ~45s 内）
QT_QPA_PLATFORM=offscreen ctest -L  slow   # 重层 = 18 个
QT_QPA_PLATFORM=offscreen ctest            # 全量 127 = 质量门（不变）
# 标签数实测：slow=18, mainwindow=8, -LE slow=109（18+109=127 ✓）
```
即默认全量仍跑 127；开发者内循环用 `-LE slow` 跳过 18 个重层（≈122s），直接消除墙钟主因。

## 3. 交付2 — teardown / 隔离（不改任何功能断言）

**发现的真实污染点**：8 个 MainWindow 用例里，原本只有 `ui_integration`、`sky_time_slider` 把 QSettings 重定向到临时目录；其余 6 个直接读写**开发者真实用户配置** `~/.config/MBDSDR/MBDSDR.conf`。该真实配置已被历次测试写脏：`vfo/count=34`、`vfoNames={"1":"气象预警"}`、`rx/centerFreq=1.45e8`、`focusMode=true`、`watch/*`、`onboardingDismissed=true` 等——即跨用例/跨运行 Qt 状态污染实锤。

**处置**（统一模式：进程内 `QSettings::setPath` 到 PID 唯一临时目录，MainWindow 构造之前）：

| 文件 | 改动 | 是否 seed `onboardingDismissed` |
|---|---|---|
| `tests/test_ui_audit.cpp` | 新增 `initTestCase()` 重定向 QSettings | 是（纯 UI 审计，假设返回用户态） |
| `tests/test_shortcuts.cpp` | 扩展已有 `initTestCase()` | 是（防首启卡片抢 `keyClick` 的焦） |
| `tests/test_device_ui.cpp` | main() 开头重定向 | 是 |
| `tests/test_control_http_e2e_prod.cpp` | main() 开头重定向 | 是 |
| `tests/test_ai_sessions_prod.cpp` | main() 开头重定向（AI store 本就 per-block 隔离） | 是 |
| `tests/test_phase21_empty_state.cpp` | main() 开头重定向 | **否**（它本就测"首启卡片可见"，需 key 缺省；原有 remove() 在干净目录为无害 no-op） |

设计依据：这些用例断言的是 widget/行为，不依赖持久化具体值（`ui_integration` 早已用"干净临时目录"模式并全绿，证明 fresh-config MainWindow 对这些断言是确定性的）；`shortcuts` 的增益断言本就写成 `after1=min(before+2,50)` 与持久化值无关。临时配置目录 PID 唯一、互不碰撞，由 OS 临时目录回收，不额外加 `rmTree`（低价值+风险）。

## 4. 交付3 — headless / AMR 路径（不碰 src/）

- C++ 用例**已经走 headless/offscreen**：`QT_QPA_PLATFORM=offscreen` + `qputenv("MBDSDR_TEST_SOURCE","1")`（离线合成源，不探真实硬件）+ 无 `MBDSDR_API_KEY`（LLM worker 不起网络调用）。
- src/ 现有、用例已在用的开关（**未新增任何 src/ 开关**）：`MBDSDR_TEST_SOURCE`、`MBDSDR_API_KEY`、`MBDSDR_AI_SESSIONS_DIR`、`MBDSDR_CONTROL_HTTP_PORT`（`src/dsp/spectrum_engine.cpp:43`、`src/ai/ai_config.cpp:22`、`src/ai/ai_session_store.cpp:19`、`src/control/control_http_server.cpp:95`）。
- **回补点（留给阶段2/src 域，本批不动）**：若阶段2在 src/ 引入任何 AMR 式/训练数据/预热类昂贵初始化，请把它 gate 在一个测试侧已会设置的环境变量后面（例如扩展 `MBDSDR_TEST_SOURCE` 语义或新增 `MBDSDR_TEST_HEADLESS=1`），使本批 8 个 MainWindow 用例自动走跳过路径。本批**无此 src/ 配合需求**——C++ MainWindow 已天然轻量。

## 5. 挂起 / 失败用例：根因与处置

基线 **0 失败 0 Not Run 0 挂起**。唯二需要说明的"慢但通过"用例（已打 slow 标签，**未改其时序断言**）：

- `e2e_smoke` 41.94s：`test_e2e_smoke.cpp:147` 固定 `processEvents` 泵 6000ms 等握手+首窗；`:172` teardown `eng.wait(3000)`；loopback 服务器 `:89` `usleep(20000)`/8KB 欠喂（引擎需 4.8MB/s，服务器仅 0.8MB/s），读线程阻塞拉长收尾。**判定**：有界、确定性、非死循环；为保功能断言不改时序。处置=slow 标签。
- `engine_audio_e2e` 34.76s / `engine_integration` 13.25s：真实 WAV/IQ 文件过 DSP 链的 e2e，`QINFO` 逐子用例 PASS，无 100% 空转。处置=slow 标签。

## 6. 本阶段产出文件

- `cpp/CMakeLists.txt`：末尾 slow/mainwindow 标签块（仅标签，127 不变）。
- `cpp/tests/test_ui_audit.cpp`、`test_shortcuts.cpp`、`test_device_ui.cpp`、`test_control_http_e2e_prod.cpp`、`test_ai_sessions_prod.cpp`、`test_phase21_empty_state.cpp`：QSettings 隔离。
- 本文件。

## 7. 未解决项 / 诚实回报

1. **标签已生效并 `ctest -N` 验证**（slow=18/mainwindow=8/-LE slow=109）。
2. **QSettings 隔离改了 6 个测试文件**；其中 `test_ui_audit` 已**重编译并实跑 8/8 PASS**（隔离后无弹窗干扰）。
3. **未完成（预算耗尽前未及）**：其余 5 个改过的目标（`test_shortcuts, test_phase21_empty_state, test_device_ui, test_control_http_e2e_prod, test_ai_sessions_prod`）源文件已改、正在后台顺序重编（各自链接全 app，OOM 约束单目标 `-j2`），**被预算截断时仍在编译 `test_shortcuts` 目标，尚未逐个重跑 ctest 复验**。
   - 收尾命令：`cd cpp/build && cmake --build . --target test_shortcuts --target test_phase21_empty_state --target test_device_ui --target test_control_http_e2e_prod --target test_ai_sessions_prod -j2`，随后 `QT_QPA_PLATFORM=offscreen ctest --timeout 120` 复跑全量，预期仍 127/127。
   - 风险评估：改动是"把 QSettings 指向干净临时目录 + 按需 seed onboardingDismissed"，`ui_integration` 同模式已长期全绿；`test_ui_audit` 已实证通过。预期全绿，但**未在本批亲跑确认，如实标注**。
4. 未碰 `src/`、未碰 `mobile/`、未 commit/push。

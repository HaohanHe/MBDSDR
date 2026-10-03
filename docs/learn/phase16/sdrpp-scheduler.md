# Wave3 / P3 — SDR++ misc_modules/scheduler 精读笔记

> 范围：`repos/sdrpp/misc_modules/scheduler/` 全部源码（仅 5 个文件、约 486 行）：
> `src/main.cpp`、`src/sched_task.h`、`src/sched_action.h`、`src/actions/tune_vfo.h`、`src/actions/start_recorder.h`、`CMakeLists.txt`。
>
> **版权声明**：SDR++ 为 GPLv3。下列引用仅为**机制学习**的注释性短片段（每处 ≤3 行，锚定 file:line），**不复制任何实现代码进 MBDSDR**；MBDSDR 落地走干净室（自有命名/结构，MIT）。
>
> **一句话结论（先读这条）**：SDR++ 的 scheduler **不是一个能用的调度器，而是一个只搭了一半骨架的半成品**。它只实现了「可序列化 Action 对象 + Task=Action 列表」这层概念；**触发条件、时间源、调度引擎、持久化全部没写**——没有定时器线程、没有墙钟、没有过境预测，`Task::trigger()` 在整个仓库里没有任何调用方。本笔记如实记录。

---

## 1. 真读 file:line + 注释性短片段

> 路径前缀统一 `repos/sdrpp/misc_modules/scheduler/src/`。

### 1.1 Action 抽象 —— `sched_action.h`（32 行，全模块最有价值的部分）

- `sched_action.h:9-17`：抽象 `ActionClass`，纯虚接口 = 一个「可触发、可序列化命令」策略对象：
  ```cpp
  class ActionClass {
  public:
      virtual ~ActionClass(){};
      virtual void trigger() = 0;
      virtual void loadFromConfig(json config) = 0;   // 可从 JSON 恢复
      virtual json saveToConfig() = 0;                // 可序列化成 JSON
      virtual std::string getName() = 0;
  ```
- `sched_action.h:29`：动作以 `std::shared_ptr<ActionClass>` 持有（`typedef std::shared_ptr<ActionClass> Action;`），Task  thus 可以异质装不同动作。
- `sched_action.h:32-33`：头文件尾部**硬编码 include 两个具体动作**（动作注册表是编译期静态的，不是运行期插件）：
  ```cpp
  #include <actions/start_recorder.h>
  #include <actions/tune_vfo.h>
  ```
  机制总结：这是一个「命令模式（Command）+ JSON 序列化」的小框架。每个动作知道自己怎么 `trigger()`、怎么存盘/读盘。新增动作 = 新写一个子类 + 在这里 include。

### 1.2 TuneVFO 动作 —— `actions/tune_vfo.h`（与 VFO/频率管理器的唯一真实联动）

- `actions/tune_vfo.h:32-35`：trigger 时真正调用全局 tuner 调谐：
  ```cpp
  void trigger() {
      if (vfoName.empty()) { return; }
      tuner::tune(tuningMode, vfoName, frequency);
  }
  ```
- `actions/tune_vfo.h:106-120`：JSON 往返字段就是 `{vfo, frequency, tuningMode}`，即「把名为 X 的 VFO 调到 Y Hz」。
- `actions/tune_vfo.h:48-56`：编辑时从 `gui::waterfall.vfos` 枚举当前所有 VFO 名，填充下拉。**注意它直接耦合 GUI**（`gui::waterfall.vfos`、`gui/tuner.h`），不是无头可用的动作。

### 1.3 StartRecorder 动作 —— `actions/start_recorder.h`（空壳）

- `actions/start_recorder.h:10-11`：trigger **是空函数**，只在构造时存了个 recorder 名字：
  ```cpp
  void trigger() {
  }
  ```
  即「启动录音」这个动作在 SDR++ 里**根本没实现**，只有配置壳。

### 1.4 Task —— `sched_task.h`（Action 列表，但无触发模型）

- `sched_task.h:9-17`：Task 就是一个 action 数组，`trigger()` 顺序 fire 全部动作：
  ```cpp
  void trigger() {
      for (auto& act : actions) { act->trigger(); }
  }
  ```
- `sched_task.h:61-64`：**「Triggers」表是写死的占位字符串**，没有任何触发条件数据结构：
  ```cpp
  ImGui::TableNextRow();
  ImGui::TableSetColumnIndex(0);
  ImGui::TextUnformatted("Every day at 00:00:00");   // 硬编码，不是真实 cron
  ```
  机制总结：作者想要「任务 = 一组动作 + 一组触发条件」，但只写了动作侧，触发侧只画了一行 UI 文字。

### 1.5 模块主体 —— `main.cpp`（无引擎、无持久化、countdown 显示 todo）

- `main.cpp:7-13`：标准 SDR++ 模块导出（`SDRPP_MOD_INFO` + 5 个 `_INIT_/_CREATE_INSTANCE_/...` C ABI）。
- `main.cpp:21-41`：构造函数里**硬编码演示任务**（两个名字、一个 TuneVFO 到 103.5 MHz + 一个 StartRecorder），不是从配置加载。
- `main.cpp:115-116`：任务列表第二列「Countdown」**直接打印 `"todo"`**：
  ```cpp
  ImGui::TableSetColumnIndex(1);
  ImGui::TextUnformatted("todo");
  ```
- 全模块 grep 结果（`thread|chrono|sleep|time|sat|pass|tle|cron|timer`）**零命中**；`Task::trigger()` 的定义存在但**全仓库无调用方**；`main.cpp` 里没有 `saveCall()`/`config.conf[...]` 写回——**任务不落盘**。

---

## 2. 机制总结（SDR++ scheduler 实际有什么 / 没有什么）

| 层 | 状态 | 证据 |
|---|---|---|
| 动作抽象（可序列化命令对象） | ✅ 有 | `sched_action.h:9-29` |
| Task = 有序动作列表 | ✅ 有 | `sched_task.h:9-17` |
| 一个真实动作（调谐 VFO） | ✅ 有 | `tune_vfo.h:32-35` |
| 第二个动作（启动录音） | ❌ 空壳 | `start_recorder.h:10-11` |
| 触发条件数据模型 | ❌ 无（UI 写死一行字） | `sched_task.h:63` |
| 调度引擎 / 定时器线程 | ❌ 无 | grep 零命中 |
| 时间源（墙钟 / 过境） | ❌ 无 | grep 零命中 |
| 任务持久化 | ❌ 无（构造函数硬编码 demo） | `main.cpp:21-41` |
| `Task::trigger()` 被执行 | ❌ 无调用方 | 全仓 grep |

**结论**：SDR++ scheduler 贡献的「通用价值」只有一条——**把一次无线电操作抽象成一个可 JSON 序列化、可 replay 的命令对象（Action），再把多个命令串成一个 Task**。至于「定时/过境自动任务编排」这个宣传名，**代码里并不存在**：没有 cron、没有卫星过境预测、没有后台等待-触发循环。它是一个留作未来扩展的骨架。

---

## 3. MBDSDR 现状对照（读真实代码，file:line）

> 路径前缀 `cpp/src/`。MBDSDR 的自动化/调度零件**远比 SDR++ 这具骨架完整**，方向一致但更实。

### 3.1 动作 / 计划模型 —— `ai/task_orchestrator.h`

- `ai/task_orchestrator.h:31-35`：一个 `TaskStep = {tool, args, description}`，通过**同一个工具执行入口**驱动电台（不是第二条执行路径）：
  ```cpp
  struct TaskStep {
      QString tool;            // "scan_band" / "tune_frequency"
      QJsonObject args;        // 字面值 或 {"fromStep":n,"path":"..."} 引用
  ```
- `ai/task_orchestrator.h:51-55`：`TaskPlan = {name, steps, abortOnFail}`，与 SDR++ `Task`（action 数组）概念同形，但多了**步骤间结果引用**（`resolveRef`，`task_orchestrator.h:88-90`）、**写门 gate**（`task_orchestrator.h:62-63,47`）、**原子停止**（`requestStop`，`task_orchestrator.h:78`）、**StepState 状态机**（`Pending/Running/Succeeded/Failed/Gated/Aborted`，`task_orchestrator.h:28`）。
- **对照判定**：MBDSDR 的 Action 抽象（TaskStep→工具分发）**已经比 SDR++ 的 ActionClass 更强**（有引用、有 gate、有单执行点）。SDR++ 这层无可抄。

### 3.2 过境预测 —— `ai/sat_task_planner.h`（SDR++ 完全没有的一层）

- `ai/sat_task_planner.h:23-31`：`SatPassEntry{name, catalogNumber, aosUtc, azAos, losUtc, azLos, maxEl}` —— 真实 AOS/LOS/过顶点模型。
- `ai/sat_task_planner.h:48-58`：`predictSatellitePasses()` 走磁盘新鲜 TLE 缓存；另有**纯函数确定性 seam** `predictPassesFromEntries(entries, ...)`（`sat_task_planner.h:55-58`），单测可注入固定 TLE、无磁盘/网络。
- `ai/sat_task_planner.h:75-77`：`planSatelliteCapture()` 输出 `SatTaskResult{plan, captureFreqHz, dopplerAtPeakHz, mode}`。
- **对照判定**：MBDSDR 已有 SGP4 过境预测并能产出 TaskPlan——这正是 SDR++ scheduler 宣传但没写的「过境」能力。MBDSDR 在此项**领先**。

### 3.3 捕获动作与多普勒限速 —— `core/sat_capture.h`

- `core/sat_capture.h:47-74`：`recommendSatelliteMode(f0Hz)` 按下行载频频段映射默认模式/带宽（ADS-B/LRIT/APT/语音/遥测）。
- `core/sat_capture.h:79-82`：`captureTargetHz = f0DownlinkHz + dopplerAtPeakHz`，未知载波返回 0（诚实）。
- `core/sat_capture.h:90-114`：`DopplerStepLimiter::advance(target)` —— 每个 tick 朝目标最多走 `kDopplerMaxStepHz`，防本振抖动。
- **对照判定**：这是 SDR++ `tune_vfo.h` 动作的「增强版」——MBDSDR 的调谐动作自带多普勒目标与限速。

### 3.4 扫描 / 信号触发 —— `dsp/frequency_scanner.h`、`dsp/signal_watch.h`

- `dsp/frequency_scanner.h:32-35`：`ScanState{Idle,Scanning,Paused,Hit}`、方向/回扫/命中停留模式；纯逻辑状态机，**不持有电台、不碰墙钟**，由外部 `tick(elapsedMs, rssiDb, &needTune)` 驱动（`frequency_scanner.h:89`）。
- `dsp/signal_watch.h:34`：`SignalWatch::update(levelDb)` 快攻慢释平滑 + 过门限确认，作为无人值守录音的**信号存在性触发器**。
- **对照判定**：MBDSDR 已有「时间驻留型触发（扫描）」和「信号强度型触发（watch→gated recorder）」两类触发件；SDR++ scheduler 一个都没有。

### 3.5 MBDSDR 缺的那块（诚实）

- grep `QTimer / cron / auto-arm / atAos`：MBDSDR 现有 `QTimer` 全是**一次性**（AI 请求超时 `llm_client.cpp:157`、重连 `spectrum_engine.cpp:385`、启动快照 `main.cpp:119`），**没有一个后台循环在等「墙钟时刻」或「预测 AOS」到点后自动发起 TaskPlan**。
- 卫星捕获目前是「一键捕获」：用户先 predict、再按捕获按钮；`planSatelliteCapture` 产出 plan 后**没有一个调度器把它挂到 aosUtc 上自动到点执行**。
- `FrequencyScanner` 用的是相对 `elapsedMs`（外部喂），不是墙钟绝对时刻。

---

## 4. 差距判定（通用价值 + 云内可确定性验证，不硬抄）

| 项 | MBDSDR 现状 | 通用价值 | 判定 |
|---|---|---|---|
| 可序列化动作/计划 | ✅ TaskStep+TaskPlan（含引用/gate/单执行点）已真实落地 | — | **已实现且更强**，无需从 SDR++ 学 |
| 过境预测→捕获计划 | ✅ SGP4 predict + planSatelliteCapture 已落地 | — | **已实现**，SDR++ 反而没有 |
| 信号/驻留触发件 | ✅ SignalWatch / FrequencyScanner tick 状态机 | — | **已实现** |
| **后台调度引擎**：持有命名任务列表，后台 tick 用「墙钟时刻 / 预测 AOS / 信号出现」判定到点，自动 fire TaskPlan | ❌ 全部自动化都是「按需触发」（用户按键 / LLM 调用），无自动武装 | **高** | **未实现，是真正的缺口** |
| 任务持久化（用户/Agent 存下「每个 NOAA 过境自动录一段」） | ❌ 无（SDR++ 也没写） | 中 | 未实现；依赖上一项 |

### 建议（只提通用、可云内确定性验证的）

1. **一个纯逻辑 `Scheduler` 引擎，时钟可注入**（干净室，不抄 SDR++ 骨架）：
   - 输入：已武装任务列表 `{id, trigger{type: wallclock|aos|signal, dueUtc/hours}, plan}` + 注入的 `nowUtc`；
   - 输出：本 tick 到期应 fire 的任务列表。纯函数 + 注入时钟，单测可确定性断言「到 AOS 前 N 分钟武装、到 AOS 触发、过期/取消如何处理」，**不碰硬件**。
   - 复用现有件：过境时刻来自 `predictSatellitePasses`，动作来自 `TaskOrchestrator::run`，信号触发来自 `SignalWatch`。**MBDSDR 只缺这层「到点判定循环」**，积木全在。
2. **不要**照搬 SDR++ scheduler 的 ImGui 耦合（`gui::waterfall.vfos`）与空壳 StartRecorder——那层是半成品，无头调度引擎应直接吃 `TaskPlan`，不依赖 GUI。
3. 持久化（任务存盘）建议延后到引擎验证通过后，且走与现有 ConfigManager/会话存储一致的路；本轮只判定、不强落地（P3 红线）。

---

## 5. 未读透清单（如实）

- `misc_modules/scheduler` 已**全部读完**（5 文件 ~486 行，无遗漏）。
- MBDSDR 对照读了 `ai/task_orchestrator.h`、`ai/sat_task_planner.h`、`core/sat_capture.h`、`dsp/frequency_scanner.h`、`dsp/signal_watch.h` 的**头文件与接口语义**；`sat_task_planner.cpp`(160 行)、`frequency_scanner.cpp` 的具体 SGP4 调用与 tick 推进细节**只 grep 了关键点、未逐行精读**。
- SDR++ 侧与 scheduler 相邻的 `misc_modules/frequency_manager`、`scanner`、`recorder` 本轮**未读**（不在 P3 指定范围；其中 recorder 恰是 StartRecorder 动作本该对接的对象，若后续要落地调度引擎需补读）。

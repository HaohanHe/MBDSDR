<!-- SPDX-License-Identifier: MIT | Phase35 L3：深读 SDR++ scheduler 与 decoder/modem 组织。
对照 cpp/src/dsp/frequency_scanner、cpp/src/ai/task_orchestrator。只学机制不抄代码，file:line 为证。 -->

# SDR++ Scheduler 与 Decoder/Modem 组织精读

> 范围：`repos/sdrpp/misc_modules/scheduler/`（Task/Action）与 `decoder_modules/`
> 组织（注册与挂进处理链）。判定哪些机制我方已等价拥有、哪些值得借、哪些是反例。

---

## 1. 一句话结论

**SDR++ 的 scheduler 是一个「Task→Action 组合 + 抽象 Action 工厂」的骨架，但其真正
的「调度」（时间触发/倒计时）根本没实现——是个 UI 壳子；decoder 侧的可借鉴点是
「VFO 作为抽点(tap) + 多态 decoder 工厂按 mode 热切换 + 能力查询接口」。**
对照我方：**Task→Action 链我方 `task_orchestrator` 已做得更纪律**（单一执行点、
引用解析、原子停止）；**VFO 抽点 + 多态 decoder 热切换我方 `vfo_manager.h` 已完整落地**。
真正的缺口只有两处小项（见 §6），其余均为 **YAGNI**。

---

## 2. Scheduler 机制总结（Task / Action）

### 2.1 结构：Composite + 抽象 Action + 工厂
- `Task` 持有 `vector<Action>`，`trigger()` 顺序广播：
  `for (act: actions) act->trigger();` —— `misc_modules/scheduler/src/sched_task.h:9-13`。
  这就是组合模式：一个 Task 是一组 Action 的有序集合。
- `Action` 是抽象基类 `ActionClass`，纯虚接口：`trigger / prepareEditMenu / showEditMenu /
  loadFromConfig(json) / saveToConfig() / getName` —— `sched_action.h:9-17`。
  别名 `typedef std::shared_ptr<ActionClass> Action;` —— `sched_action.h:29`
  （用 shared_ptr 做动作对象的轻量多态句柄）。
- 每个具体 Action 一个**自由工厂函数**返回 `shared_ptr`：
  `Action TuneVFO() { return Action(new TuneVFOClass); }` —— `actions/tune_vfo.h:143-145`；
  `Action StartRecorder()` —— `actions/start_recorder.h:43-45`。
- 配置可序列化：`loadFromConfig(json)` / `saveToConfig()` —— `tune_vfo.h:106-120`
  （vfo/frequency/tuningMode 三键往返）。
- 组装用法（demo）：先 new 两个 Action、`loadFromConfig`、再 `t.addAction(...)` ——
  `main.cpp:31-41`。

### 2.2 它是怎么「挂」进宿主的
- 与所有 SDR++ 模块同构：`SDRPP_MOD_INFO{...}` 元信息 + 5 个 C 导出符号
  `_INIT_ / _CREATE_INSTANCE_ / _DELETE_INSTANCE_ / _END_` —— `main.cpp:7-13, 131-144`。
- 模块本体 IS-A `ModuleManager::Instance`（基类接口 `postInit/enable/disable/isEnabled`，
  `core/src/module.h:43-50`）。模块管理器内部维护两张表：
  `modules`（注册表）+ `instances`（实例表）—— `module.h:100-101`。
- 动态库加载：`.so/.dll/.dylib` 经 dlopen，`MOD_EXPORT extern "C"` —— `module.h:17-29`。

### 2.3 关键判定：「调度」本身是空的
- 倒计时列直接打印字面量 `"todo"` —— `main.cpp:116`。
- 触发条件表硬编码一行 `"Every day at 00:00:00"`，无任何定时器 —— `sched_task.h:63`。
- `Trigger()` 只是同步地挨个 `act->trigger()`，**没有时间轴、没有重复周期、没有下一次触发
  计算**。即：这是「动作序列编辑器」，不是「调度器」。

---

## 3. Decoder/Modem 组织总结（如何注册 + 如何挂进处理链）

### 3.1 挂接点 = VFO
decoder 不是直接吃宽带 IQ，而是吃一个 **VFO 的输出流**：
- `vfo = sigpath::vfoManager.createVFO(name, REF_CENTER, 0, 9600, 14400, ...)` ——
  `decoder_modules/m17_decoder/src/main.cpp:55`。
- VFO 对外暴露 `dsp::stream<dsp::complex_t>* output` —— `core/src/signal_path/vfo_manager.h:29`；
  内部包一个 `dsp::channel::RxVFO* dspVFO`（频率搬移 + 抽取的信道切片）—— `vfo_manager.h:2,33`。
- decoder 链的首块直接以 `vfo->output` 为输入：`decoder.init(vfo->output, ...)` —— `m17/main.cpp:59`。
- **机制本质**：宽带源 → 宽带 splitter → 每 decoder 一个 RxVFO 切片 → 各自的窄带 DSP 链。
  decoder「挂进处理链」= 拿一个 VFO 的 output 当输入流。

### 3.2 一条链的装配范式（init→start→stop 生命周期）
`m17/main.cpp:59-76`：`decoder.init(vfo->output) → resamp.init(decoder.out, 8000, sr) →
reshape.init → diagHandler.init → 各 .start() → sinkManager.registerStream(name,&stream)`。
销毁对称：`stop()` 链 + `deleteVFO` + `unregisterStream` —— `m17/main.cpp:81-94`。
`enable()/disable()` 负责**重建/销毁 VFO**（在已有宽带窗口内重切一个切片）—— `m17/main.cpp:98-124`。

### 3.3 多态 decoder 热切换（策略模式）
- pager：极简基类 `Decoder{ setVFO / start / stop / showMenu }` —— `pager_decoder/src/decoder.h:4-11`；
  `selectProtocol()` 里 `decoder.reset(); switch(newProto){ make_unique<POCSAGDecoder/FLEXDecoder>; }`
  —— `pager_decoder/src/main.cpp:86-114`。运行时按协议换实现。
- weather_sat：`std::map<std::string, SatDecoder*> decoders` 注册表 + `selectDecoder()` 切换 ——
  `weather_sat_decoder/src/main.cpp:49, 92-99`；基类 `SatDecoder` 接口更宽
  （select/start/stop/setVFO/canRecord/startRecording/drawMenu）—— `sat_decoder.h:5-16`。

### 3.4 Demod 工厂 + 能力查询（radio 模块，最值得学的一条）
- `demod::Demodulator` 是一个**宽能力接口**：除 init/start/stop 外，大量 getter
  `getDefaultBandwidth / getMinBandwidth / getMaxBandwidth / getDefaultSnapInterval /
  getVFOReference / getDeempAllowed / getFMIFNRAllowed / getSquelchAllowed ...`
  —— `radio/src/demod.h:37-60`。
- 工厂按 ID switch 出实现：`instantiateDemod()` —— `radio/src/radio_module.h:365-378`
  （NFM/WFM/AM/DSB/USB/CW/LSB/RAW）。
- **宿主用能力查询自配置 VFO**：new 完 demod 后读 `getDefaultBandwidth/Min/Max/SnapInterval`
  去 clamp 带宽、配 VFO，并按 `demod->getName()` 做**每解调器独立配置命名空间**
  `config.conf[name][demod->getName()]` —— `radio_module.h:381-398`。
  即：新增一种解调模式时，它自带「我需要多宽的带宽、允许哪些后处理」，宿主不必硬编码。

### 3.5 DSP 线程 → GUI 的交接约定
静态 C 回调 + `void* ctx` + mutex，并**用时间戳判 stale**：
`m17/main.cpp:144-147`——超过 1s 没收到新 LSF 就把 `lsf.valid=false`，UI 显示 "--"。
配合 `lsfHandler` 里 `lock_guard` 写共享结构 —— `m17/main.cpp:258-263`。

---

## 4. 关键算法 / 机制索引（file:line 速查）

| 机制 | 位置 |
|---|---|
| Task 顺序触发 Action | `scheduler/src/sched_task.h:9-13` |
| Action 抽象接口 | `scheduler/src/sched_action.h:9-17` |
| shared_ptr Action 句柄 | `scheduler/src/sched_action.h:29` |
| Action 自由工厂 | `actions/tune_vfo.h:143-145` |
| Action JSON 序列化 | `actions/tune_vfo.h:106-120` |
| 模块 5 符号导出契约 | `scheduler/src/main.cpp:131-144`；`core/src/module.h:43-50,100-101` |
| VFO 输出流即挂接点 | `core/src/signal_path/vfo_manager.h:29,33`；`m17/main.cpp:55,59` |
| 多态 decoder 热切换 | `pager_decoder/src/main.cpp:86-114` |
| Demod 能力查询接口 | `radio/src/demod.h:37-60` |
| 工厂 + 按名配置命名空间 | `radio/src/radio_module.h:365-398` |
| stale 时间戳判活 | `m17/main.cpp:144-147` |

---

## 5. 可借鉴点（机制，非代码）

1. **「动作对象 = 可序列化 + 可 trigger + 可被工厂产出」三件套**：让一组操作可被存盘、回放、
   组合。我方已用更纪律的形式实现（见 §6.1），无需补，但这个心智模型可沿用。
2. **VFO 作为唯一抽点**：decoder 从不直接绑源，只绑一个窄带切片的 output。换频率只动切片
   位置，不动 decoder。我方 `vfo_manager.h` 已完全是这个模型。
3. **能力查询接口（§3.4）**：让「新增一种模式」自带 VFO/后处理需求，宿主零硬编码——这是
   面向扩展开放(OCP)的正例。
4. **stale 时间戳判活（§3.5）**：DSP 线程异步产出，UI 按「多久没更新」决定显示真实值还是
   占位符，比「只看最后一帧」更诚实。

---

## 6. 我方差距判定（对照 frequency_scanner / task_orchestrator）

### 6.1 Task→Action 链 —— 我方已等价甚至更优，不补
- `task_orchestrator.h`：`TaskPlan = 确定性步骤序列`，每步 = 一次 tool 调用，且**走与 LLM
  tool-loop 完全相同的执行点**，不存在第二条执行路径 —— `cpp/src/ai/task_orchestrator.h:4-10`。
- 比 SDR++ scheduler 多出来的纪律：步骤参数可引用上一步**真实结果**（JSON path
  `{"fromStep":n,"path":"..."}`，`task_orchestrator.h:31-35, 88-90`）；
  `StepState` 含 `Gated/Failed/Aborted`（:28）；原子 `requestStop()`（:78-79）；
  确定性模板 `planSweepFindAndRecord / planTargetCapture`（:103-110）。
- 结论：**SDR++ 的 Action 抽象我方已泛化为「tool + json-args + 单执行点」**，无需补。

### 6.2 时间触发（cron / 周期调度）—— YAGNI，明确不补
- SDR++ 想做时间触发但**根本没做**（§2.3，`"todo"` / 硬编码 `"Every day at 00:00:00"`）。
- 我方 `task_orchestrator` 刻意**不持有任何定时器/墙钟**（`:76-77` 注释「No timers are owned」），
  与 `frequency_scanner.h:3-22`「owns no wall clock、由 caller 喂 elapsedMs」是同一纪律：
  触发时机交给引擎/外层，纯逻辑可单测。
- 结论：卫星过境排班已由 `sat_task_planner` + 引擎 Doppler 负责；桌面端不需要 cron 式调度器。
  **不采纳 SDR++ 这一半成品方向。**

### 6.3 VFO 抽点 + 多态 decoder 热切换 —— 我方已完整落地，不补
- `vfo_manager.h:77-79` 每个 `VfoChannel` = `Channelizer + unique_ptr<IDemod> + resampler`；
  数字 decoder **仅在匹配 mode 时构造、否则为 null**，并随 mode/采样率 rebuild 一并重建
  （:100-138 rds/stereo/pocsag/m17/vor 的生命周期注释）；能力谓词
  `isPocsag/isM17/isDigital`（:149-155）；诚实空态（:140-146「never fabricated」）。
- 结论：这比 SDR++ 的「每 decoder 一个独立 .so + 自己 createVFO」**更内聚**，不补。

### 6.4 Demod 能力查询接口 —— 暂 YAGNI，列为观察项
- 我方 `demod.h:20-24` 的 `IDemod` 是**极简**接口（process/reset/name/outputSampleRate/setBandwidth），
  **没有** `getDefaultBandwidth/Min/Max/SquelchAllowed/DeempAllowed` 那一组。
- 当前带宽预设是中心化管理（`tests` 里有 `test_bandwidth_preset`），不需要每个解调器自报带宽区间。
- 判定：**仅当未来要做「用户任选 per-mode 带宽并自动撑开 VFO 框/自动决定 squelch/去加重可用否」
  时才补齐这组 getter**。现在补 = 过度设计。记为 watch-item，不立项。

### 6.5 stale 时间戳判活 —— 小补齐候选（优先级低）
- 我方数字解码读出现已是「诚实快照」（`vfo_manager.h:144-146`），但 pocsagMessages/m17Calls
  只在 rebuild/clear 时清，**没有「这条消息是多久前的」时间戳**。
- 候选小补：给每条读出附 `lastUpdateMs`，UI 据此灰显（对标 m17 的 Receiving/Idle，`m17/main.cpp:230-237`）。
  属增量优化，不阻塞当前。

---

## 7. 反例核查（这些不要抄）

1. **基类私有 `valid` 与子类 out-param `bool& valid` 分裂**：`sched_action.h:24-27` 私有 `valid`
   经 `isValid()`(:19-21) 暴露，但子类根本不写它——子类写的是 `showEditMenu(bool& valid)` 的
   出参（`tune_vfo.h:94`）。结果 `isValid()` 永远 false，是死代码。→ 我方状态只留一处真实来源。
2. **空实现却当功能交付**：`StartRecorderClass::trigger(){}` 空函数（`start_recorder.h:10-11`），
   UI 里却叫 "Start Recorder"。→ 我方坚持「诚实空态/显式 TODO」，不发空实现冒充可用。
3. **字面量占位混进 shipped UI**：倒计时列打印 `"todo"`（`main.cpp:116`）、触发条件写死
   `"Every day at 00:00:00"`（`sched_task.h:63`）。→ 不把未实现项以成品形态进 UI。
4. **死分支**：`tune_vfo.h:59` `if (id < 0 && !vfoNames.empty())`——`id` 是循环计数器、
   到这里必 ≥0，分支永假。→ 写前先核对变量取值域。
5. **裸 new 不 delete / VFO 重复创建**：weather_sat `new SatDecoder(...)` 存进裸指针 map
   却从不 delete（`main.cpp:49`）；m17 在构造函数（:55）和 enable()（:100）各 createVFO 一次、
   前者未删。→ 我方 `unique_ptr` 化（`vfo_manager.h:78,96,105`）已是正确姿势，继续保持。
6. **领域 Action 内嵌 ImGui 编辑菜单**：`ActionClass` 既管业务又管画 UI（`sched_action.h:13-14`）。
   → 我方刻意把纯逻辑（scanner/orchestrator）与 Qt/UI 分层（`frequency_scanner.h:21-22` 无 QObject），
   这条边界要守住，别把 GUI 拉回领域对象里。

---

## 8. 收口

- **学什么**：VFO 抽点模型、多态 decoder 工厂按 mode 热切换、（条件成熟时的）能力查询接口、
  stale 时间戳判活。
- **不学什么**：SDR++ scheduler 的时间触发半成品、它的 UI/业务耦合与上述 6 条反例。
- **我方现状**：Task→Action（orchestrator）与 VFO/decoder 组织（vfo_manager）均已落地且更纪律；
  唯一真缺口是 §6.5 的读出时间戳小补，§6.4 能力查询接口列观察项。整体判定：**以 YAGNI 为主，不补 scheduler 方向**。

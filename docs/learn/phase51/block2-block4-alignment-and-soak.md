# Phase51 块2 + 块4：35 工具三通道对齐 + 10 分钟 IQ 流值守

> 执行：2026-10-06（UTC+8）。基线 HEAD = 31d7b34。8GB OOM（-j2 单目标增量）。
> 红线：数字全为实跑实际值；架构差异如实 file:line；不虚构修复；无比赛字样；活动参数未入通用代码；未 commit/push。

---

## 一、ctest 全量回归（不回归核对）

| 项 | 基线 | 本次实测 | 结论 |
|---|---|---|---|
| ctest 注册 | 129 | **129** | ✅ |
| 通过 | 128 passed + e2e_smoke SKIP | **"100% tests passed, 0 tests failed out of 129"** | ✅ |
| e2e_smoke | 环境性 Skipped（exit 77） | `did not run: 17 - e2e_smoke (Skipped)` | ✅ 预期 |
| control_http（本块新测） | 1 个 ctest 条目 | **Passed 0.81s**（含新增 3 个 QTest 槽） | ✅ |
| startup_robustness | Passed | Passed 0.83s | ✅ |

> 本块新增的 3 个测试是**既有 `test_control_http` 可执行文件内部的 QTest 槽**，不新增 ctest 条目，故注册数保持 129 不变。单跑 `./test_control_http` = **11 passed, 0 failed**（8 原有 + 3 新增）。

---

## 二、块2：35 工具三通道行为对齐

### 2.1 三通道架构（对齐的结构基础）

| 通道 | 入口 | 分发方式 | 门控 |
|---|---|---|---|
| **ControlHub（本地）** | `control/control_hub.cpp` `execute()` → 命令表 ~58 条（39 写 + 19 读） | 各 `cmd*` handler 直调 engine slot | `writeEnabled_` → `gatedResult()` |
| **HTTP JSON** | `control/control_http_server.cpp` `route()` | **薄代理**：每个端点 = `hub_->execute(...)` | 继承 ControlHub 门控 |
| **Agent function-calling** | `ai/agent_tools.cpp` `executeTool()` → 35 工具 dispatchTable | 各 `exec*` 函数**直调 engine**（不经 ControlHub） | `llm_worker.cpp:118` `isWriteTool` → `gatedToolResult()` |

**关键结论：HTTP ≡ ControlHub 是构造性对齐**——HTTP 不重写任何控制逻辑，所有端点直接 `hub_->execute()`（control_http_server.cpp:282/285/288/291/320），JSON 形状、读写门控、诚实错误、诚实空态**逐字一致**。唯一 HTTP 特有行为是路由、404/400/204 状态码、`?channel=N` query→args 映射、CORS。

### 2.2 门控形状对齐（已对齐）

| | ControlHub | Agent |
|---|---|---|
| 形状 | `{ok:false, gated:true, error:"..."}` | `{ok:false, gated:true, error:"..."}` |
| 错误文案 | `"写入被禁止（write gate 关闭）：未执行 X"`（control_hub.cpp gatedResult） | `"手动模式：未执行 X"`（agent_tools.cpp:22-28） |

→ **形状完全对齐**，仅文案不同（面向不同客户端：GUI 操作员 vs LLM）。如实记录为文案差异，非行为 bug。

### 2.3 逐工具对照（参数 / 返回值 / 错误码 / 门控）

抽样核对重叠工具（tune / set_mode / start_recording / 解码读 / 书签 / VFO）：

| 工具（Agent 名） | 参数名跨通道 | 返回值形状 | 门控 | 差异判定 |
|---|---|---|---|---|
| tune_frequency / tune | `freq_hz` 一致 | Agent: `{ok, frequency_hz, message, connected, test_signal, source}`；ControlHub: `{ok, command:"tune", frequency_hz, clamped}` | 写门控一致 | **架构差异（见 2.4-A）** |
| set_mode | `mode` 一致（enum AM/NFM/...） | 同上 Echo 风格差异 | 写门控一致 | 架构差异 |
| start_recording | 无参一致 | Agent 如实回显 engine 的 bool（失败→`{ok:false,error:"录制启动失败"}`） | 写门控一致 | 对齐 |
| get_pocsag/m17/vor | `channel` 可选一致 | 均 `{ok, count:0, [], locked:false}` 诚实空态 | 读永远放行 | **构造性对齐** |
| add_bookmark / vfo_* / recordings | `index/id` 一致 | Echo 风格差异 | 写门控一致 | 架构差异 |

### 2.4 差异项判定（修了什么 / 什么不修 + 理由）

**A. 频率越界：Agent 拒绝 vs ControlHub clamp —— 架构性，不改（理由：两类客户端契约）**
- Agent：`arguments_validator.cpp:105-120` 对 `freq_hz` 强制 schema min/max，越界**拒绝**（`{ok:false, error:"参数校验失败...超出上界"}`），永不触 engine。
- ControlHub/HTTP：`control_hub.cpp cmdTune` 越界**clamp**到 `[kFreqMinHz,kFreqMaxHz]` 并回 `clamped:true`。
- 不改理由：Agent 路径是给 LLM 的严格 schema 契约（拒绝幻觉参数），ControlHub 路径是给程序化 HTTP 客户端的宽容 clamp。两者都诚实、都不编造；强行统一会破坏 129 基线断言与 LLM 工具调用契约。**如实记录 file:line**。

**B. 返回值附加字段 —— 架构性，不改**
- Agent 每个结果注入 `connected/test_signal/source/message`（agent_tools.cpp:56 `addSourceFields`）——为让 LLM 知道自己在真机还是合成信号上操作。
- ControlHub/HTTP 用 `command:/clamped:` echo 字段。
- 不改理由：这是面向 LLM 的诚实来源标注，ControlHub 已有自己的 telemetry 快照（`get_telemetry`）。属设计差异。

**C. 未知工具错误词表 —— 架构性，不改**
- Agent `executeTool` 未命中返回纯串 `"未知工具: X"`（agent_tools.cpp 末尾）——但生产中不可达（llm_worker.cpp:243 `validateArguments` 先用空 schema 拦截未知工具）。
- ControlHub 返回结构化 `{ok:false, error:"未知命令: X"}`。
- 不改理由：Agent 的纯串分支仅在直接单测 `executeTool` 时出现，生产路径已被 schema 校验前置拦截。

### 2.5 补 HTTP 端点测试（P41 指出的覆盖缺口）

现有 `test_control_http.cpp` 原覆盖 6 场景。本块补 **3 个真实缺口**（全部通过）：

| 新测试槽 | 断言 | 覆盖的缺口 |
|---|---|---|
| `postCommandArgsMustBeObject()` | POST `/command` 的 `args` 为 string/array/number → **400**；省略 args → 200 | "args 类型校验"路径此前无测试 |
| `optionsPreflightReturns204()` | `OPTIONS /command` → **204 No Content** 空体，不触 engine | CORS 预检路径此前无测试 |
| `channelQueryPassthroughIsHonest()` | `?channel=N` 数字→诚实空态；`?channel=abc` 非数字→忽略回退；m17/vor 同查不崩 | query→args 透传与非数字容错此前无测试 |

改动文件：`cpp/tests/test_control_http.cpp`（+3 槽，+声明）。单跑 11/11 通过。

---

## 三、块4：10 分钟纯桌面 IQ 流值守压力

**方法**：`cpp/scripts/stress_iq_stream.sh` 启动**真实桌面二进制** `cpp/build/mbdsdr`，offscreen 平台 + `MBDSDR_TEST_SOURCE=1`（离线合成 IQ，无硬件无网络），每 10s 采样 `ps` RSS，持续 600s。结果落 `docs/learn/phase51/soak_rss.csv`。

### 真实数据（RSS 时间序列，节选；全量见 CSV）

| t(s) | RSS(KB) | alive |
|---|---|---|
| 0 | 89,456 | 1 |
| 60 | 93,256 | 1 |
| 120 | 97,344 | 1 |
| 180 | 102,080 | 1 |
| 240 | 105,336 | 1 |
| 300 | 112,504 | 1 |
| 360 | 115,576 | 1 |
| 420 | 118,392 | 1 |
| 480 | 121,356 | 1 |
| 540 | 124,300 | 1 |
| 600 | 137,612 | 1 |

**汇总**：
- **存活**：61/61 采样 `alive=1`，全程无崩溃、无退出。
- **RSS**：89.5 MB → 137.6 MB，**+48.2 MB / 600s ≈ 4.8 MB/min**，单调上升、未见平台。
- **内部日志**：无 error/crash/exception/reconnect/drop/NaN（`grep` 全空）。
- 末尾 t=590 有 +10.8 MB 台阶（126→137MB），疑为 Qt 缓存/waterfall 缓冲重分配。

### 泄漏/漂移判定（如实）

- **未崩溃、无重连、无 NaN**：通过。
- **慢漂移属实**：RSS 线性 +4.8 MB/min 且不收敛，1 小时外推 ≈ +290 MB。
- **已排查的有界结构**：waterfall `history_` 是固定 `QImage(bins_, ringDepth_)` ring buffer（spectrum_display.cpp:225-226）；audio sink 无设备重试路径不分配（仅日志+定时器，qt_audio_sink.cpp）；未发现逐帧无界 QList 累积。
- **未定位/未修的理由（防虚构）**：环境无 heaptrack/valgrind/massif（`which` 全空），8GB OOM 预算下无法跑 heap profiler 精确定位；glibc malloc arena 保留也会在无应用泄漏时抬升 RSS。**故如实标注为"慢漂移/泄漏候选，需 heap profiling 定位"，未虚构修复。**

---

## 四、本块改动文件（仅 cpp/ + docs/learn/phase51/）

| 文件 | 类型 |
|---|---|
| `cpp/tests/test_control_http.cpp` | 修改（+3 HTTP 测试槽） |
| `cpp/scripts/stress_iq_stream.sh` | 新增（值守脚本） |
| `docs/learn/phase51/soak_rss.csv` | 新增（真实 RSS 时间序列） |
| `docs/learn/phase51/soak_meta.txt` | 新增（peak_rss_kb=137612） |
| `docs/learn/phase51/block2-block4-alignment-and-soak.md` | 本报告 |

## 五、未解决项

1. **RSS 慢漂移未定位**：+4.8 MB/min 单调上升，需 heaptrack/valgrind 在内存充裕环境做 heap profiling 才能定位是哪块累积；本块环境无 profiler 且 OOM 预算不允许。
2. **Agent vs ControlHub 越界语义差**（拒绝 vs clamp）为有意架构差异，已 file:line 记录，未统一。
3. **APK**（沿 P41）：云端 JDK 11 vs Gradle 9.3.1 需 JDK 17，无法复建新包。
4. 真机过境/真机 rtl_tcp 闭环长时间值守未验（本次为离线合成源）。

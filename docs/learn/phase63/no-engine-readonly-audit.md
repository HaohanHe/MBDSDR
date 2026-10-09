# Phase63 — MBDSDR 只读工具「引擎缺失 / 未 attach」态抽查（只读审计 + 落档）

- **HEAD**: `aabc309`（审计前后均未变）
- **仓库根**: `/home/user/Doubao/chats/38438160041798146/MBDSDR`
- **范围**: 上一轮 `readonly-tools-audit.md` 审过「引擎在、源断」三态（connected=false / dropped / no_telemetry）。本轮聚焦**更基础态**：**引擎指针本身为 null**（ControlHub 未 `setEngine` / Agent 未 `setEngine`）时，三个只读工具 `get_status / get_squelch_status / get_spectrum_status` 在 Agent / ControlHub / HTTP 三通道的返回形状与错误语义，重点验证**无假成功**（无引擎时不得回包 `ok:true` + 假数值）。
- **方法**: 全程只读源码 + offscreen 跑既有测试二进制（`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，clean env，未设 `MBDSDR_TEST_SOURCE`）。未改源码、未 git add/commit/push、未 mock、未操控 GUI。

---

## 0. 命名与注册表核对（沿用历轮结论）

以 Agent `dispatchTable()`（`cpp/src/ai/agent_tools.cpp:1308`）与 CH 命令表（`cpp/src/control/control_hub.cpp:84` 起）为准：

| 抽查项 | Agent executor | CH handler | HTTP |
|---|---|---|---|
| `get_status` | `execGetStatus`（`agent_tools.cpp:239`） | `cmdGetStatus`（`control_hub.cpp:806`） | `GET /status` → `execute("get_status")`（`control_http_server.cpp:286`）；亦可 `POST /command` |
| `get_squelch_status` | `execGetSquelchStatus`（`agent_tools.cpp:715`） | `cmdGetSquelchStatus`（`control_hub.cpp:1262`） | 仅 `POST /command`（`control_http_server.cpp:306`） |
| `get_spectrum_status` | `execGetSpectrumStatus`（`agent_tools.cpp:1181`） | `cmdGetSpectrumStatus`（`control_hub.cpp:1487`） | 仅 `POST /command` |

三者在两侧均**同名注册**，且在 CH 表中 `write=false`（只读行，`control_hub.cpp:132/150/155`）。

---

## 1. 引擎为 null 时的守卫路径（核心下钻）

### 1.1 Agent 侧：`executeTool` 守卫（`agent_tools.cpp:1391-1402`）

```cpp
QString executeTool(const QString& name, const QJsonObject& args,
                    dsp::SpectrumEngine* engine, ui::BookmarkManager* bookmarks) {
    if (!engine) return "error: no engine";          // ← line 1393，纯字符串
    const SourceInfo src = readSourceInfo(engine);
    for (const ToolDispatch& d : dispatchTable()) {
        if (name == QLatin1String(d.name))
            return d.exec(args, engine, src, bookmarks);
    }
    return "未知工具: " + name;
}
```

- **守卫位置**：`agent_tools.cpp:1393`，是函数第一行，在 `readSourceInfo(engine)` 与任何 executor 之前。
- **守卫行为**：直接 `return "error: no engine";`——**纯字符串**，不是 JSON 对象。
- **executor 是否被调用**：**否**。三个只读 executor（`execGetStatus:239` / `execGetSquelchStatus:715` / `execGetSpectrumStatus:1181`）体内均直接 `engine->centerFreq()` / `engine->squelchEnabled()` / `engine->fftSize()` 等解引用，**自身无 null 守卫**；靠 dispatch 层这一行挡住，才不会解引用 null 崩溃。
- **下游消费**：`LLMWorker::dispatchToolCall`（`llm_worker.cpp:113-123`）原样返回 `executeTool(...)` 结果；`doChat`（`llm_worker.cpp:259-266`）把该字符串塞进 `ChatMessage tr.content` 作为 tool 消息喂回 LLM。**不做 JSON parse**，所以纯字符串不会崩 LLM worker，但对 LLM 而言它是一段非结构化文本，与正常 `{ok:true,...}` JSON 回包形状不一致。

### 1.2 CH 侧：`ControlHub::execute` 守卫（`control_hub.cpp:339-394`）

```cpp
QString ControlHub::execute(const QString& command, const QJsonObject& args) {
    // 1) 查表
    const CommandRow* row = nullptr;
    for (const CommandRow& r : table()) { ... }
    if (!row) { return compact(errResult("未知命令: ...")); }      // :345

    // 2) 无引擎：所有命令（含只读）一律诚实失败                       // :351-357
    if (!engine_) {
        QJsonObject o = errResult("无引擎连接（ControlHub 未 attach SpectrumEngine）");
        emit commandExecuted(command, false);
        return compact(o);
    }

    // 3) 写门（write gate）
    if (row->write && !writeEnabled_.load()) { ... }              // :360

    // 4) 分派到 handler
    dsp::SpectrumEngine* eng = engine_;
    const bool onHome = (QThread::currentThread() == eng->thread());
    if (onHome) return dispatch(row, command, args);              // :385
    ...
}
```

- **守卫位置**：`control_hub.cpp:353`，在查表之后、写门（`:360`）与分派（`:385`）之前。
- **守卫形状**：`errResult(...)` = `{ok:false, error:"无引擎连接（ControlHub 未 attach SpectrumEngine）"}`（`errResult` 定义于 `control_hub.cpp:276-281`）。**是标准 JSON 信封**，与参数错误、未知命令同形状。
- **哪些只读命令在守卫之前/之后**：
  - 守卫**之前**唯一可能返回的是「未知命令」（`:345`）——命令名不在表里。
  - 守卫**之后**才到写门与 handler。因此 `get_status / get_squelch_status / get_spectrum_status` 三个已注册只读命令，在 `engine_==nullptr` 时**全部命中 `:353` 守卫**，handler 体内（`cmdGetStatus:806` / `cmdGetSquelchStatus:1262` / `cmdGetSpectrumStatus:1487`）**根本不会被调用**。
- **handler 自身无 null 守卫**：三个 handler 体内均直接 `engine_->centerFreq()` / `engine_->squelchEnabled()` / `engine_->fftSize()`（`control_hub.cpp:806+`、`:1265-1268`、`:1490-1492`），靠 `execute()` 这一行挡住。若有人绕过 `execute()` 直接调 `cmdGetStatus(args)`，会解引用 null 崩溃——但公开 API 面只有 `execute()`，生产路径不可达。

### 1.3 HTTP 侧：纯透传（`control_http_server.cpp`）

- `GET /status`（`:284-286`）：`return hub_->execute("get_status", QJsonObject()).toUtf8();`
- `POST /command`（`:306-331`）：解析 body → `hub_->execute(tool, args).toUtf8()`。
- **无任何信封包装、字段改写、HTTP 状态码升级**（透传 200，错误在 JSON body 内）。因此 HTTP 返回形状 ≡ CH 返回形状，逐字节一致。

### 1.4 假成功检查（本轮重点）

| 通道 | 无引擎时是否回 `ok:true`？ | 是否回假数值（rssi=0 / freq=0 当真值）？ | 结论 |
|---|---|---|---|
| Agent（`executeTool:1393`） | **否**——返回纯字符串 `"error: no engine"`，连 JSON 都不是，更没有 `"ok":true` | 否（executor 未执行，`engine->...` 未被调用） | ✅ 无假成功 |
| CH（`execute:353`） | **否**——返回 `{ok:false, error:"无引擎连接..."}` | 否（handler 未执行） | ✅ 无假成功 |
| HTTP（透传） | **否**（≡ CH） | 否（≡ CH） | ✅ 无假成功 |

> 三通道在「引擎指针 null」这一最基础态下均**不回 `ok:true`**，不存在「无引擎却报频率=0/静噪=关当真值」的假成功路径。Agent 侧虽形状是纯字符串而非 JSON，但语义上仍是「错误」，未伪装成功。

### 1.5 构造早期窗口：MainWindow 构造顺序（`main_window.cpp`）

| 顺序 | 行号 | 动作 |
|---|---|---|
| 1 | `:176` | `engine_ = new dsp::SpectrumEngine(this);` —— 引擎**最先**创建 |
| 2 | `:181` | `engine_->setDopplerControlSurface(this);` |
| 3 | `:2775-2776` | `agent_ = new ai::Agent(this); agent_->setEngine(engine_);` —— Agent 创建时引擎已存在 |
| 4 | `:3328-3329` | `controlHub_ = new control::ControlHub(this); controlHub_->setEngine(engine_);` —— CH 创建时引擎已存在 |

- **结论**：生产路径下，`engine_` 在 `agent_` / `controlHub_` 构造时**必然非 null**，且 `setEngine` 紧随构造调用。不存在「UI 已起、Agent/CH 已活、但引擎还没 new」的时间窗。
- **null 引擎态的真实可达场景**：
  1. 单元测试（`test_control_hub.cpp:267` `control::ControlHub hub;` 从不调 `setEngine`；`test_control_http.cpp:306` 同模式）；
  2. 假设性的 headless 嵌入（只 new ControlHub 不 attach engine）；
  3. **Agent 侧目前没有任何测试覆盖 `executeTool(name, args, nullptr, ...)` 路径**（grep `test_agent.cpp` 全部 `executeTool` 调用第三参均为 `&engine`，无 nullptr）。

---

## 2. 三态 × 三通道返回形状对照表

图例：✓=一致；△=信封形状差异但语义同为错误；✗=值/形状实质分歧。`—`=该状态下不出该字段。

> 三态定义：
> - **S1 = 引擎指针 null**（本轮重点）
> - **S2 = 引擎已构造、未 attach source / 未流式**（fresh engine + NullSource，历轮「未初始化」态）
> - **S3 = 引擎已流式、后断连**（dropped，历轮「断连」态）

### 2.1 `get_status`

| 字段 | S1 Agent | S1 CH / HTTP | S2 Agent | S2 CH / HTTP | S3 Agent | S3 CH / HTTP |
|---|---|---|---|---|---|---|
| 返回形状 | **纯字符串** `"error: no engine"`（`:1393`） | `{ok:false, error:"无引擎连接..."}`（`:354`） | `{ok:true, ...}` | `{ok:true, command:"get_status", ...}` | `{ok:true, ...}` | `{ok:true, ..., status:"dropped"}` |
| ok | （无此字段） | **false** | true | true | true | true |
| connected | — | — | false | false | false | false |
| frequency_hz | — | — | 0.0 | 0.0 | 0.0 | 0.0 |
| 其他业务字段 | — | — | 全部出现 | 全部出现 | 全部出现 | 全部出现 |

**S1 判定**：△ 形状差异（纯字符串 vs JSON 信封），语义同为错误。见 D1。

### 2.2 `get_squelch_status`

| 字段 | S1 Agent | S1 CH / HTTP | S2 Agent | S2 CH / HTTP | S3 Agent | S3 CH / HTTP |
|---|---|---|---|---|---|---|
| 返回形状 | 纯字符串 `"error: no engine"` | `{ok:false, error:"无引擎连接..."}` | `{ok:true, enabled:false, threshold_db:-50.0, auto:false, open:false, connected:false,...}` | `{ok:true, command:"get_squelch_status", enabled:false, threshold_db:-50.0, auto:false, open:false}` | 同 S2 | 同 S2 |
| ok | （无） | **false** | true | true | true | true |

**S1 判定**：△ 形状差异。S2/S3 两侧值已对齐（上轮 D1 已修，Agent 不再回 null）。

### 2.3 `get_spectrum_status`

| 字段 | S1 Agent | S1 CH / HTTP | S2 Agent | S2 CH / HTTP | S3 Agent | S3 CH / HTTP |
|---|---|---|---|---|---|---|
| 返回形状 | 纯字符串 `"error: no engine"` | `{ok:false, error:"无引擎连接..."}` | `{ok:true, fft_size:2048, window_type:..., average_mode:..., connected:false,...}` | `{ok:true, command:"get_spectrum_status", fft_size:2048, window:..., average:...}` | 同 S2 | 同 S2 |
| ok | （无） | **false** | true | true | true | true |

**S1 判定**：△ 形状差异。S2/S3 key 漂移（`window_type` vs `window`）沿用上轮 D2，登记不修。

---

## 3. 差异判定清单

| # | 差异 | 性质 | 判定 | 最小方案 / 理由 |
|---|---|---|---|---|
| **D1** | S1（引擎 null）时，Agent 侧 `executeTool` 返回纯字符串 `"error: no engine"`（`agent_tools.cpp:1393`）；CH 侧 `execute` 返回 `{ok:false, error:"无引擎连接..."}` JSON 信封（`control_hub.cpp:354`）。同一逻辑条件（无引擎），Agent 给 LLM 一段非结构化文本，CH/HTTP 给标准错误 JSON。 | **信封形状不一致**（语义同为错误，无假成功） | **该修（最小）** | 最小方案：把 `agent_tools.cpp:1393` 从 `return "error: no engine";` 改为返回与 CH 同形状的 JSON：`QJsonObject o; o["ok"]=false; o["error"]=QString::fromUtf8("无引擎连接（Agent 未 attach SpectrumEngine）"); return compact(o);`。理由：LLM tool 消息通道上，所有错误都应是同一 JSON 信封，避免 LLM 把纯文本误判为「工具返回了一段描述」而非结构化错误；与 CH 对齐后两侧 wire 形状一致。按只读纪律本轮不落代码，仅登记。 |
| **D2** | S1 Agent 路径**无任何测试覆盖**：`test_agent.cpp` 全部 `executeTool` 调用第三参均为 `&engine`（非 null），没有用例断言「`executeTool("get_status", {}, nullptr, nullptr)` 返回错误形状」。CH/HTTP 侧有 `noEngineIsHonest`（`test_control_hub.cpp:266`）与 `bindsLoopbackOnlyAndHasBanner`（`test_control_http.cpp:305`）覆盖。 | **测试缺口** | **该修（补测）** | 最小补测：在 `test_agent.cpp` 加一条 `nullEngineReturnsHonestError()`，对三个只读工具断言 `executeTool(name, {}, nullptr, nullptr)` 解析为 JSON 后 `ok==false` 且 `error` 为字符串（与 D1 修复配套）。按只读纪律本轮不补测，登记。 |
| **D3** | S2/S3 三通道值形状差异（`window_type` vs `window`、Agent 无五态、Agent 追加 `connected/test_signal/source`、CH 追加 `command`）沿用上轮 `readonly-tools-audit.md` D2/D3/D5，属**有意分层**，本轮不重复判定。 | 系统性约定 | **架构性不修（记录）** | 沿用上轮结论。 |

> 本轮**无「假成功」类差异**——S1 三通道均不回 `ok:true`。唯一「该修」项是 D1 的信封形状对齐（纯字符串 → JSON 错误信封），属契约整洁性问题，不涉及数据正确性。

---

## 4. 金集实跑计数（offscreen，clean env）

环境：`env -u MBDSDR_TEST_SOURCE LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib QT_QPA_PLATFORM=offscreen`，执行于 `cpp/build` 既有二进制。

| 二进制 | passed | failed | skipped | 耗时 | 退出码 |
|---|---|---|---|---|---|
| `test_agent` | **32** | 0 | 0 | 610 ms | 0 |
| `test_control_hub` | **28** | 0 | 0 | 1299 ms | 0 |
| `test_control_http` | **14** | 0 | 0 | 42 ms | 0 |
| **合计** | **74** | **0** | **0** | — | — |

三金集全绿，无回归。HTTP 用例回环端口 `QWARN` 横幅为正常提示，非失败。
（对比上轮 HEAD `bda5e48`：`test_agent` 30→32、`test_control_hub` 27→28、合计 71→74，增量来自并行会话在途用例，非本轮改动。）

---

## 5. 红线扫描结果

| 红线项 | 结果 |
|---|---|
| 改源码 | ✅ 未改。`git status --short` 仅有 4 个**既有/并行会话**未跟踪隔离文件：`cpp/scratch/regen_tool_doc`、`cpp/scratch/regen_tool_doc.cpp`、`cpp/tests/ui_diag_freeze.cpp`（上轮已登记）+ `cpp/scratch/gated_render_snapshot.cpp`（本轮新增，并行会话在途，未触碰、未误判、未回滚） |
| git add / commit / push | ✅ 未执行 |
| 「比赛 / competition」字样（`cpp/src` + `cpp/tests`） | ✅ 无命中（grep exit 0 空输出） |
| mock / 操控 GUI | ✅ 未 mock；offscreen 纯跑既有测试二进制，未起 GUI |
| HEAD | ✅ 审计前后均为 `aabc309` |
| `/tmp` 构建目录 | ✅ 未在 `/tmp` 构建；测试二进制直接跑 `cpp/build` 既有产物（测试日志里 `/tmp/mbdsdr_agent_cap_rec/...` 是被测代码 Recorder 自己的录制输出路径，非本轮新增构建） |

---

## 6. 诚实未完成项

1. **D1 仅登记、未修**：按只读纪律未落地代码修复；建议落点 `agent_tools.cpp:1393`，需后续写会话语句把纯字符串 `"error: no engine"` 改为与 CH 同形状的 `{ok:false, error:"..."}` JSON 信封。
2. **D2 补测未做**：未新增 `test_agent.cpp` 的 null-engine 用例；建议落点为一条 `nullEngineReturnsHonestError()`，对 `get_status / get_squelch_status / get_spectrum_status` 三个名字断言 `executeTool(name, {}, nullptr, nullptr)` 返回 JSON 错误信封。
3. **S1 Agent 路径未在 offscreen 实跑**：因测试二进制无对应用例（见 D2），本轮对「`executeTool` 传 nullptr」的形状结论**完全来自源码阅读**（`agent_tools.cpp:1393` 一行守卫），未实际跑一个传 nullptr 的进程做断言。建议与 D2 补测一并落地后复核。
4. **handler 直接调用途的崩溃风险**（绕过 `execute()` 直调 `cmdGetStatus` 等）为源码推断，公开 API 面不可达，未做崩溃复现。
5. **S2/S3 三通道值形状差异**沿用上轮结论，本轮未重复下钻枚举值。

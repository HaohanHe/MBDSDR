# Gate 拦截后 HTTP 客户端呈现审计（POST /command 端到端）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），审计基线 HEAD = `b1812c2`（main）。
- 交付方式：**只读为主 + 最小测试补强 + 落档**。新增本 md；测试侧仅在 `cpp/tests/test_control_http.cpp` 的 `gateClosedRefusesWritePost()` 内 +10 行断言（同步重跑该二进制 PASS）；**未改任何 `cpp/src/**`、未 `git add/commit/push`、未触碰任何未跟踪隔离文件**。
- 环境：`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，构建/运行一律在 `cpp/build`（既有二进制增量构建，**未用 `/tmp`**）；金集一律 **clean env（不设 `MBDSDR_TEST_SOURCE`）**。
- 上轮基线：`gate-presentation-rerun.md`（HEAD `abf5cc4`，UI 渲染快照 + 金集 92/0）。本轮把焦点从 UI 渲染下移到 **HTTP 边界本身**：手动模式/write gate 关闭时，`POST /command` 经真实回环 TCP 到达客户端的**响应形状、HTTP 状态码、`gated` 标志保持、无假成功字段**这四件事，端到端钉死。

## 0. 结论摘要

- **gate 拦截时 HTTP 响应形状逐字段保持，`gated:true` 不丢。** CH `execute()` 在 write gate 关闭时返回 compact JSON `{"ok":false,"gated":true,"error":"写入被禁止（write gate 关闭）：未执行 <tool>"}`；HTTP 层 `POST /command` 把这串**原样 `.toUtf8()` 字节透传**（`control_http_server.cpp:331`），不注入、不改写、不删字段。客户端解析后 `ok==false`、`gated==true`、`error` 为非空字符串，三字段同存。
- **HTTP 状态码 = 200（语义分层正确）。** gate 拒绝是**应用层语义结果**（请求格式良好、服务端已理解、仅写策略拒绝），不是协议错误。400 仅保留给协议层失败（JSON 体非法 / 缺 `tool` / `args` 非对象）；404 仅给未知路径。测试 `QCOMPARE(r.status, 200)` 钉住（`test_control_http.cpp:220`）。
- **无假成功：gated 响应不含任何写工具成功字段。** 本轮新增最小断言：gated 的 `tune` 响应体里**不得出现** `frequency_hz` / `command` / `clamped`（真实 `cmdTune` 成功时才会回吐的三个字段）。结构上由 `gatedResult()` 只 set 三个键天然保证，本轮在 HTTP 边界直接钉死，防止将来某层"顺手补字段"。
- **引擎零接触已钉。** gate 分支在 `execute()` 内**早于** `dispatch()`/engine 调用返回（`control_hub.cpp:367-371`），HTTP 读回 `GET /status` 的 `frequency_hz` 与拦截前逐字节相等；同时读快照（`/pocsag_messages` 等）在 gate 关闭下仍 200 + `ok:true`（读永不 gated）。
- **Agent 通道同形复核成立。** Agent 侧 `llm_worker.cpp:119` → `agent_tools.cpp:25-31` `gatedToolResult()` 产出 `{"ok":false,"gated":true,"error":"手动模式：未执行 <tool>"}`——**同三键信封**（`ok:false / gated:true / error:string`），仅 error 文案按各自 gate 语义不同（CH 主写闸 vs Agent 手动模式），形状零漂移。
- **差异判定：零"该修" → 零源码待修。** 唯一补强是测试断言 +10 行（见 §4）；无架构性不修项新增。
- 金集复跑（clean env，真实计数）：**test_control_http 14/0、test_control_hub 29/0、test_agent 34/0，合计 77/0**。
- 红线扫描：**CLEAN**（无 `ghp_` 真实 token；竞技措辞关键词在 `cpp/src/{ai,control}`、`cpp/tests` 0 命中；工作树 diff 仅本测试文件 +10 行）。

### 0.1 金集实跑（本次独立执行，clean env 真实计数）

| 金集测试 | passed | failed | 关键后置行为槽（本轮实跑） |
|---|---|---|---|
| `test_control_http` | **14** | **0** | `gateClosedRefusesWritePost`（HTTP 200 + `ok:false` + `gated:true` + error 字符串 + **本轮新增：不泄漏 frequency_hz/command/clamped** + 引擎频率不变 + 读快照仍 200）；`postCommandNoiseBlankerRoute`（gated 后引擎状态不动）PASS |
| `test_control_hub` | **29** | **0** | `readAlwaysAllowedWriteGateBothStates`、`phase26WritesAreGatedAndBadArgsHonest`、`exportIqSegment_gatedWhenWriteGateClosed`、`digitalSnapshotReadsAreHonestAndUngated`、`phase63BilateralAliasContract` PASS |
| `test_agent` | **34** | **0** | `manualMode_gateSpotCheckAllWrites`（29 写全 gated + 后端 byte 不变）、`manualMode_gatesApplyCorrection`、`manualMode_allowsCalibrateRead`、`nullEngineReturnsHonestErrorEnvelope`、`phase63BilateralAliasContract` PASS |
| **合计** | **77** | **0** | — |

> 运行期告警仅 `no librtlsdr ops bound; stub start() returns false`（offscreen 无硬件 QINFO）与 HTTP 回环绑定横幅（QWARN，本机回环仅 127.0.0.1，无鉴权提示），均非 FAIL。
>
> 本轮**未设** `MBDSDR_TEST_SOURCE`（沿用前轮口径：该 env 只属 `test_ui_integration` 源注入，不用于 agent/hub/http）。

## 1. Gate 响应端到端链路（源码逐跳追踪）

```
客户端 POST /command  {"tool":"tune","args":{"freq_hz":160e6}}
   │  真实回环 TCP（QTcpServer LocalHost only，control_http_server.cpp:80）
   ▼
HttpControlServer::tryHandle → route()                       [control_http_server.cpp:162/239]
   │  1) 解析请求行 / Content-Length / body
   │  2) POST /command：QJsonDocument::fromJson(body)
   │     - body 非法 / 缺 tool / args 非对象 → statusOut=400，返回 {ok:false,error:...}   [:309-328]
   ▼
hub_->execute("tune", args)                                  [control_http_server.cpp:331]
   │  ControlHub::execute                                     [control_hub.cpp:346]
   │  3) 命令查表 → row->write == true
   │  4) writeEnabled_ == false（gate 关闭）                   [:367]
   ▼
ControlHub::gatedResult("tune")                              [control_hub.cpp:283-289]
   │  QJsonObject o;
   │  o["ok"]    = false;
   │  o["gated"] = true;
   │  o["error"] = "写入被禁止（write gate 关闭）：未执行 tune";
   ▼
QJsonDocument(o).toJson(Compact)  →  QString（execute 返回）
   │  HTTP 层：return hub_->execute(...).toUtf8();             [control_http_server.cpp:331]
   │  statusOut 保持 200（route 入口默认 200，本分支未改）      [:240]
   ▼
sock->write("HTTP/1.1 200 OK\r\n" + headers + body)          [:219-228]
```

### 1.1 客户端实际收到的字节（推导 + 测试解析双重确认）

- **状态行**：`HTTP/1.1 200 OK`
- **响应头**：`Content-Type: application/json; charset=utf-8`、`Content-Length`、`Access-Control-Allow-Origin: *`、`Connection: close`
- **响应体（compact，逐字）**：
  ```json
  {"ok":false,"gated":true,"error":"写入被禁止（write gate 关闭）：未执行 tune"}
  ```

字段保持核对：

| 字段 | gated 时 | 真实 tune 成功时（对照） | 结论 |
|---|---|---|---|
| `ok` | `false` | `true` | 语义反转，无假成功 |
| `gated` | `true` | （不存在） | **标志保持，HTTP 透传不丢** |
| `error` | 非空字符串 | （不存在） | 诚实说明拒绝原因 |
| `frequency_hz` | **不存在**（本轮新增钉扎） | `160e6`（实际值） | **不泄漏成功回吐字段** |
| `command` | **不存在** | `"tune"` | 同上 |
| `clamped` | **不存在** | `false` | 同上 |

### 1.2 状态码分层的设计意图

- **400 Bad Request** = 协议层：服务端无法理解这是什么请求（JSON 语法错、`tool` 不是字符串、`args` 不是对象）。
- **404 Not Found** = 协议层：路径不存在。
- **200 OK + `{ok:false,gated:true,...}`** = 应用层：请求完全合法、语义清晰，但写策略拒绝执行。把它做成 4xx 会迫使客户端把"权限/策略拒绝"和"我发错包"混为一谈；200 + 信封是刻意的分层，测试在 `control_http.cpp:220` 与 `:284`（未知命令也是 200 + ok:false）双重钉住。

## 2. 无假成功核查

1. **HTTP 层零字段加工。** `control_http_server.cpp:331` 唯一动作是 `hub_->execute(...).toUtf8()`——没有 `o.insert(...)`、没有 `if (gated) ... else ...` 分支、没有读成功 payload 合并。gated 体就是 `gatedResult()` 体。
2. **gate 分支早于 engine。** `control_hub.cpp:367-371`：写命令 + gate 关 → 直接 `gatedResult()` 返回，**不进入** `dispatch()`、不调用任何 `cmdTune/cmdSetMode/...`，引擎 setter 零触发。
3. **读回证据。** 测试 `gateClosedRefusesWritePost` 在拦截后立即 `GET /status`，断言 `frequency_hz` 与拦截前完全相等（`test_control_http.cpp:227-228`）；`postCommandNoiseBlankerRoute` 断言 gated 后 `eng.noiseBlankerEnabled()` 仍为原值（`:540`）。
4. **读快照不受影响。** gate 关闭下 `GET /pocsag_messages` 仍 200 + `ok:true`（`:230-233`），证明 gate 是"写闸"不是"全站闸"。

## 3. 测试覆盖核对

既有覆盖（本轮复核，全部 PASS）：

- `test_control_http.cpp`
  - `gateClosedRefusesWritePost`：HTTP 200 / `ok:false` / `gated:true` / error 字符串 / 引擎频率不变 / 读快照仍通。
  - `postCommandNoiseBlankerRoute`：gated 后引擎状态不动，`gated` 标志在。
  - `unknownPathAndBadRequestsAreHonest`：未知命令 200+ok:false；malformed/缺 tool/args 非对象 → 400。
- `test_control_hub.cpp`：写全 gated、读永不 gated（`:217-234`、`:364-388`、`:419-422`、`:480-496`、`:786-799`、`:892-895`、`:946-973`）。
- `test_agent.cpp`：29 写全 gated + 18 读全开放 + 后端 byte 不变（`manualMode_gateSpotCheckAllWrites`），error 文案逐字钉 `手动模式：未执行 <tool>`。

**本轮补强（最小，+10 行，仅测试）**：在 `gateClosedRefusesWritePost` 内新增三条 `QVERIFY2(!o.contains(...))`，把"gated 响应不得携带写工具成功字段"从源码结构推断升级为 HTTP 边界的直接断言。改动同步重跑该二进制，PASS（见 §0.1）。

## 4. 差异判定清单

| # | 项 | 判定 | 位置 | 处置 |
|---|---|---|---|---|
| 1 | HTTP 透传是否丢 `gated` 标志 | **不丢**，`:331` 原样 relay | `control_http_server.cpp:331` | 不修（既有测试已钉） |
| 2 | gated 时 HTTP 状态码 | **200**（语义分层正确） | `control_http_server.cpp:240,331` | 不修（既有测试 `:220` 钉住） |
| 3 | gated 响应泄漏写工具成功字段 | 结构上不可能，但**测试未直接钉** | `test_control_http.cpp:221-224` | **本轮补 +10 行断言**（见 §3） |
| 4 | gated 分支是否触碰引擎 | **不触碰**，早返回 | `control_hub.cpp:367-371` | 不修（既有测试钉读回不变） |
| 5 | Agent vs HTTP gate 信封形状 | **同三键**（ok/gated/error），文案各按语义 | `llm_worker.cpp:119` / `agent_tools.cpp:25-31` | 不修 |

**源码待修：0 处。** 架构性不修：0 条（本轮无新增观察）。

## 5. 红线扫描

- 工作树 diff：仅 `cpp/tests/test_control_http.cpp` +10/-0（本审计补强）；未跟踪文件 `cpp/scratch/gated_render_snapshot.cpp`、`cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp` 均**未触碰**（并行会话在途产物）。
- `git add` / `commit` / `push`：**未执行**。
- 竞技措辞（`比赛` / `competition`）：在 `cpp/src/**`、`cpp/tests/**` grep 0 命中。
- 真实 token（`ghp_` 形态）：0 命中。
- mock：金集均为真实 `SpectrumEngine` + 真实 `QTcpServer` 回环 + 真实 `QTcpSocket` 客户端，无 mock 对象。
- `/tmp`：本轮构建/运行全部落在 `cpp/build`，未向 `/tmp` 写任何文件。
- GUI：全程 `QT_QPA_PLATFORM=offscreen`，未弹出任何窗口。

## 6. 诚实未完成项

- 本轮未对"gate 关闭时 HTTP 大并发/长连接"做压测（既有实现每请求 `Connection: close`，天然无 keep-alive 状态污染，风险低）；属下一阶段范畴。
- 移动端（`mobile/lib/app/ai_tools.dart` `_gated()`）的同形信封仅在源码注释（`agent_tools.h:38`）中声明对齐，本轮未在移动端实跑复核——本轮焦点是桌面 HTTP 边界。

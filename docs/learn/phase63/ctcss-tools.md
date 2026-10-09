# Phase63 — CTCSS 亚音三通道工具落地（set_ctcss / get_ctcss_status）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），基线 HEAD = `2781b12`
- 职责范围：在已由并行会话落地的 CTCSS DSP 核心（`dsp/ctcss.*`、`SpectrumEngine`
  的 `setCtcssEnabled/setCtcssFreqHz/ctcssEnabled/ctcssFreqHz/ctcssPresent`，tokens
  `kCtcssToneHzMin=67.0 / kCtcssToneHzMax=254.1 / kCtcssToneHzDefault=88.5`）之上，
  把**两个 Agent 工具**贯通 Agent / ControlHub / HTTP 三通道，并写工具手动模式 gate。
- 纪律：全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`，
  `LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），未操控 GUI；无 mock；未改 DSP 代码；
  未 `git add/commit/push`；未触碰并行会话在途文件（`dsp/ctcss.*`、`spectrum_engine.*`、
  `core/tokens.h`、UI 会话的 `main_window.*` / `test_ui_integration.*` / `ctcss-ui.md`、
  隔离文件 `cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/*`）。

## 0. 结论摘要

- Agent 工具注册表 **47 → 49**（写分类 29 → 30，读分类 18 → 19）。
- 三通道一致：Agent executor、ControlHub 命令表 handler、HTTP `POST /command` 通用委托
  三处均可达同一对工具；写工具 `set_ctcss` 在手动/写门关闭时被拦截。
- 金集实跑（offscreen，clean env 不设 `MBDSDR_TEST_SOURCE`）：任务指定的四个金集二进制
  **88 passed / 0 failed**（test_tool_registry 8 + test_agent 35 + test_control_hub 30 +
  test_control_http 15）。连同 test_tool_schema(10)、test_ai_real_link(17) 共 **115 / 0**。
- mobile catalog 只读目录 **47 → 49** 同步；`buildRadioTools()` **保持 10 个不接入**（取舍见 §4）。

## 1. 三通道落地 file:line

### 1.1 Agent 声明表（tool_schema.cpp）

| 工具 | 位置 | 写/读 | 说明 |
|---|---|---|---|
| `set_ctcss`（spec） | `cpp/src/ai/tool_schema.cpp:546-573` | **write（gated）** | `enabled`(bool, **必填**) + `frequency_hz`(number, 选填，min=67.0 / max=254.1 来自 tokens) |
| `get_ctcss_status`（spec） | `cpp/src/ai/tool_schema.cpp:576-585` | read | 返回 enabled / frequency_hz / active |

注册位置紧接 `get_squelch_status` 之后、`set_noise_blanker` 之前——与静噪同族相邻，
顺序与 mobile catalog 逐字对齐。

### 1.2 Agent 执行器 + dispatch 表（agent_tools.cpp）

| 项 | 位置 |
|---|---|
| `execSetCtcss` | `cpp/src/ai/agent_tools.cpp:810`（实现体 802-839） |
| `execGetCtcssStatus` | `cpp/src/ai/agent_tools.cpp:844`（实现体 841-858） |
| dispatch 注册行 | `cpp/src/ai/agent_tools.cpp:1436-1437`（`{"set_ctcss",&execSetCtcss}` / `{"get_ctcss_status",&execGetCtcssStatus}`） |

- 镜像 `execSetSquelch` / `execGetSquelchStatus`（及 noise_blanker）既有白名单/守卫模式。
- 写 gate 无需额外代码：`isWriteTool()` 直接读 `ToolSchemaSpec::write`（tool_schema 表是
  唯一事实源），`set_ctcss` 标 write=true 即被手动模式 gate 拦截。
- `execSetCtcss` 行为：`enabled` 缺/非 bool → `errResult`（诚实错，绝不静默切换）；
  `frequency_hz` 选填，缺省沿用当前/默认 88.5 Hz；**越界（<67.0 或 >254.1，含非有限数）
  在工具层 `ok:false` 拒绝**——引擎内部是 clamp，工具层才是「拒绝而非假成功钳位」的诚实门。

### 1.3 ControlHub 命令表 + handler（control_hub.cpp / .h）

| 项 | 位置 |
|---|---|
| 命令表写行 `set_ctcss` | `cpp/src/control/control_hub.cpp:115`（write=true） |
| 命令表读行 `get_ctcss_status` | `cpp/src/control/control_hub.cpp:152`（read=false） |
| `cmdSetCtcss` 实现 | `cpp/src/control/control_hub.cpp:1344` |
| `cmdGetCtcssStatus` 实现 | `cpp/src/control/control_hub.cpp:1367` |
| 声明（.h） | `cpp/src/control/control_hub.h:246-247` |

- 镜像 `cmdSetSquelch` / `cmdGetSquelchStatus` + noise_blanker handler 模式：
  `needBool`/`needDbl` 取参 → 越界拒绝 → 引擎 setter 真落地 → JSON 信封回读。
- 命令表现计 **68 行（44 写 + 24 读）**，本轮 +1 写 +1 读。

### 1.4 HTTP 层

- **无需新路由**。`cpp/src/control/control_http_server.cpp` 的 `POST /command` 是通用委托
  （body 取 `tool`/`args` → `hub_->execute(tool, args)`，无 per-route handler），
  `set_ctcss` / `get_ctcss_status` 注册进 ControlHub 表后自动可达；写门关闭时写命令在
  `ControlHub::execute()` 内被 `gatedResult` 拦。已由 `test_control_http::postCommandCtcssRoute`
  钉住（写落地引擎 / 线上回读 / 越界拒绝 / 写门关闭拦截且引擎不动）。

## 2. 工具参数表

### set_ctcss（write，手动模式 gated）

| 参数 | 类型 | 必填 | 约束 | 缺省 | 越界行为 |
|---|---|---|---|---|---|
| `enabled` | boolean | **是** | — | — | 缺/非 bool → `{ok:false,error:"参数 enabled 缺失或不是布尔值"}` |
| `frequency_hz` | number | 否 | 67.0 – 254.1 Hz，有限数 | 沿用当前/默认 88.5 Hz | 越界 → `{ok:false,error:"CTCSS 亚音频率越界：必须在 67.0–254.1 Hz 之间…"}`（不静默钳位） |

成功回包：`{ok:true, enabled:<bool>, frequency_hz:<Hz>, ...source字段}`。

### get_ctcss_status（read，永不 gated）

| 输出字段 | 类型 | 来源 | 诚实性 |
|---|---|---|---|
| `enabled` | bool | `engine->ctcssEnabled()` | 真实使能开关 |
| `frequency_hz` | number | `engine->ctcssFreqHz()` | 真实调谐频率（默认 88.5） |
| `active` | bool | `engine->ctcssPresent()` | 真值读回；无信号/未使能恒 `false`，**不编造亚音** |

成功回包：`{ok:true, enabled:<bool>, frequency_hz:<Hz>, active:<bool>, ...source字段}`。

## 3. 测试与金集计数

| 二进制 | 槽数(+新增) | 本轮断言要点 |
|---|---|---|
| `test_tool_registry` | 8（无新槽） | `schemas.size()==49`、`kAllCxxTools` +2、`kExpectedWriteTools`(30) +`set_ctcss`、`kFlutterUngatedReadTools`(19) +`get_ctcss_status`、`defs.size()==49` |
| `test_tool_schema` | 10（无新槽） | `specs.size()==49`、逐字 name+description 对 +2（含 CTCSS 两条描述逐字）、`headerCount==49` |
| `test_agent` | **35**（+`ctcssLandReadbackOutOfRangeRejectAndGate`） | 越界频率 `ok:false` 且引擎不动；合法落地引擎回读；缺/非 bool enabled 诚实错；手动门 gated 且引擎不翻；全量 gate 遍历 `QCOMPARE(writes,30)/(reads,19)` + CTCSS 快照 byte 不变 |
| `test_ai_real_link` | 17（无新槽） | `supported` 集 +2、`newTools` dispatch 探针 +2、isWriteTool 抽钉 |
| `test_control_hub` | **30**（+`ctcssSetLandOutOfRangeRejectedAndGate`） | 写落地引擎、线上回读、越界拒绝引擎不动、缺 enabled 错、写门关 gated 且读仍放行；gate 循环样本 +`set_ctcss` |
| `test_control_http` | **15**（+`postCommandCtcssRoute`） | POST /command 写落地/读回/越界拒绝/写门关拦截 |

- 任务指定四金集合计：8 + 35 + 30 + 15 = **88 passed / 0 failed**（基线 85 + 本轮 3 个新测试槽）。
- 全 6 二进制合计 115 / 0。

## 4. mobile catalog 同步与 buildRadioTools 取舍

- `mobile/lib/app/tool_catalog.dart`：只读目录 **47 → 49**，在 `get_squelch_status` 之后插入
  `set_ctcss`(write) / `get_ctcss_status`(read) 两条，顺序/写标志与 `tool_schema.cpp`
  注册序逐字对齐；头部注释同步为「49 = 30 写 + 19 读」；后续区段编号注释顺移。
- `mobile/test/tools_catalog_test.dart`：长度/唯一名断言 47 → 49，页面文案断言「桌面端共 49 个」。
- `mobile/lib/pages/tools_catalog_page.dart`：计数用 `all.length` 动态渲染（自动变 49），仅注释同步。
- **`mobile/lib/app/ai_tools.dart::buildRadioTools() 保持 10 个，不接入 CTCSS**：
  - 取舍理由：移动端 AiClient 走的是移动端 rtl_tcp 通道的 10 个硬件原语工具面；CTCSS 要在
    移动端真实生效，需移动端解调链把 mono 音频喂给 CTCSS 检测器并做 UI 呈现——这是独立的
    移动端 DSP/UI 集成，超出本轮「桌面三通道工具落地」职责。
  - 因此 `kMobileImplementedToolNames` **不加入** `set_ctcss`/`get_ctcss_status`：
    catalog 里这两条诚实标为「桌面端能力、移动端未接入」，绝不冒充移动端已有——
    与 `calibrate_frequency`、VFO 系列等桌面独有工具的处理一致。
  - 未来若移动端接入 CTCSS，再在 `buildRadioTools()` 追加两条并把计数 10 → 12。

## 5. 红线扫描

| 项 | 范围 | 结果 |
|---|---|---|
| `competition` / `比赛` / `赛事` | `cpp/src/ai/**`、`cpp/src/control/**` | **0 命中** |
| `ghp_`（GitHub token 泄露） | `cpp/src/ai/**`、`cpp/src/control/**`、`mobile/lib/**` | **0 真实 token** |
| License | 本轮改动源文件头 | 均 `SPDX-License-Identifier: MIT`（项目 MIT，中立 GPL 表述无引入） |

## 6. 诚实未完成项与边界

- `docs/learn/phase31/agent-tool-documentation.md` 是 `generateToolDocumentation()` 的同源自动生成快照，
  仍停留在旧计数；按 `tool-count-sync.md` 既例，该文档由链接 `libmbdsdr_core.a` 的一次性重生成小程序
  刷新（本轮未跑该重生成流程，故此处仅登记、不代改）。`generateToolDocumentation()` 本身已
  基于 `specs.size()` 自动输出「49 个工具」。
- phase63 下历轮审计档（`tool-count-integrity.md`、`read-output-contract-audit.md` 的「金集 85」、
  `gate-*-audit.md` 等）为**历轮时点快照**，按项目「每轮新增文档、不改写旧档」惯例保留原文，
  其计数由本档（49 / 88）作为本轮新基线承接。
- HTTP「命令自动覆盖」为架构断言（统一委托、无 per-route handler），测试只钉代表路由，未枚举跑全部命令。
- offscreen 环境无 pipewire（Qt multimedia 符号解析 QINFO 告警），非 FAIL，不影响断言。
- 未执行任何 `git add/commit/push`；工作树仅本档 + 职责文件改动，并行会话在途改动原样保留。

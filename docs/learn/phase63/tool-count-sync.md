# 工具计数快照同步清单（35 → 47）

- 仓库：MBDSDR，核对基线 HEAD = `f14cade`。
- 权威源：`cpp/src/ai/tool_schema.cpp::registeredToolSpecs()`（名字 / 参数 / `write` 标志 / 描述；`tool_schema.h:42` `bool write = false` 缺省即读）。
- 本轮口径：桌面 Agent 工具注册表 = **47 个 = 29 写 + 18 读**。
- 纪律：本轮只改 `mobile/` 三个文件 + 本清单；未跑 `cmake/cmake --build`、未触碰任何 `cpp/` 文件（避免与并行 cpp agent 的构建竞态）；未 `git add/commit/push`。云端无 Flutter SDK，Dart 改动**待 SDK 环境验证**，未声称 `flutter analyze/test` 通过。

## 0. 计数自洽表

| 口径 | 值 | 出处 |
|---|---|---|
| Agent spec 总数 | **47** | `tool_schema.cpp:63 registeredToolSpecs()` |
| 写（`write=true`，手动模式写门拦截） | **29** | 逐 spec `s.write = true` |
| 读（`write=false` 或缺省） | **18** | 含显式 `s.write = false` 与缺省 |
| 写 + 读 | 29 + 18 = **47** ✓ | 与总数闭合 |

交叉印证：`docs/learn/phase63/tool-three-channel-47.md:16`（Agent spec 47 = 29/18）、C++ 金集 `test_tool_registry.cpp:139`（expected 47 tools）/`:67-85`（29 写集合）/`:104-114`（18 读集合）。

## 1. 本轮已同步（mobile/，file:line）

移动端只读快照此前钉在 35，且前 7 条顺序与 cpp 注册序不一致；本轮按 `registeredToolSpecs()` 注册序重排并补齐 12 条。

| 文件 | 改动 | 落点 |
|---|---|---|
| `mobile/lib/app/tool_catalog.dart` | 文件头说明 35→47；`kDesktopToolCatalog` 由 35 条重排/补齐为 **47 条**，名字顺序与 `write` 标志逐字对齐 cpp spec；分组注释补齐新组（噪声抑制 #24-25、VFO 写族 #34-37、多普勒/网络源/能力态 #44-47） | 头注释 `:6`；目录 doc 注释 `:36`；列表起点 `:38`；末条 `get_recording_state` `:235` |
| `mobile/test/tools_catalog_test.dart` | 长度断言 35→47、唯一名计数 35→47、页面文案断言「桌面端共 35 个」→「桌面端共 47 个」、注释同步 | `:1,:4,:16,:17,:19,:40,:41` |
| `mobile/lib/pages/tools_catalog_page.dart` | 文件头注释「对照桌面端 35 个」→47；正文计数行本就用 `桌面端共 ${all.length} 个` 动态渲染，catalog 改后自动显示 47，无需字面改 | 头注释 `:1`；动态计数行 `:42` |

**核对方式**：程序化 diff——解析 cpp spec（按独立变量 `s.name = "..."` / `s.write = true|false` 切分，正则排除 `hrs.name` 等参数名误报）与 Dart 目录逐条比对；结果：名字顺序完全一致、`write` 标志逐条一致、均为 47 = 29 写 + 18 读。`kMobileImplementedToolNames`（移动端同名子集）未变，测试仍断言它是桌面目录的真子集。

补齐的 12 条（相对旧 35 快照）：`get_acars_packets`、`get_navtex_messages`、`set_noise_blanker`、`get_noise_blanker_status`、`set_vfo_armed`、`set_vfo_frequency`、`set_vfo_mode`、`set_vfo_bandwidth`、`set_doppler_compensation`、`connect_network_source`、`get_capabilities`、`get_recording_state`。

## 2. 待 cpp 侧重生成（本轮不跑构建，明确移交）

| 文件 | 现状 | 处理方式 |
|---|---|---|
| `docs/learn/phase31/agent-tool-documentation.md` | 头部写「自动生成，**45 个工具**」（`:1`），工具块停在 45。该文件是 cpp 侧 `generateToolDocumentation()`（`tool_schema.cpp:959`）的同源自动生成产物，非手写文档。 | **由并行 cpp agent 用新构建重生成覆盖到 47**：链接 `libmbdsdr_core.a` 的一次性小程序调用 `generateToolDocumentation()` 输出全文（沿 phase62 已建立的做法，见 `docs/learn/phase62/tool-three-channel-audit.md:131-132`），头部同步为「47 个工具」、工具块 45→47。本轮不跑 `cmake --build`，故此处仅登记、不代改。 |

## 3. 当前态、需后续同步到 47（活文档，超出本轮改动范围，未改）

根 `README.md` 是持续维护的产品总览（非 phase 时点快照），其中两处仍写 35，应随注册表同步到 47；但本轮纪律只允许改 `mobile/`，故登记为待办、不在本轮动：

| 文件:行 | 现状表述 | 建议改为 |
|---|---|---|
| `README.md:8` | 桌面行「AI 助手工具面实测 **35 个**（`cpp/src/ai/tool_schema.cpp`…）」 | 35 → 47 |
| `README.md:10` | Python 原型行「…**不是**随桌面交付的 35 工具集」 | 35 → 47 |

## 4. 历史快照（保持不改，仅登记）

下列均为各 phase 的时点工程记录，描述的是该 phase 当时的注册表规模（35 或 45），属历史过程描述，**不改**；其中 Python 原型时代的「45」指假闭环工具、与 Agent 注册表无关。

| 文件:行 | 历史表述 | 为何保持 |
|---|---|---|
| `docs/learn/phase3/audits/A2-fake-tools.md:13` | 「约 45 个工具」假闭环 | Python 原型时代审计，非 Agent 注册表；历史记录 |
| `docs/learn/phase31/_PHASE31_SPEC.md:7,18` | 35 工具缺口规格 / 文档生成 | Phase31 时点规格 |
| `docs/learn/phase31/_WAVE2_AGENT_LANDING.md:4,19` | 35 工具落地说明 | Phase31 时点记录 |
| `docs/learn/phase31/tool-calling-boundary.md:3,24,88,112,113` | 我方 35 工具系统 | Phase31 模型侧依据 |
| `docs/learn/phase32/_PHASE32_SPEC.md:26` | 35 工具能力清单 | Phase32 时点规格 |
| `docs/learn/phase39/architectural-diffs.md:11,64` | AI 35 工具 / 依赖 35 全集 | 桌面 vs 移动差异的时点快照 |
| `docs/learn/phase39/capability-matrix.md:113` | 依赖桌面 35 工具全集 | 能力矩阵时点快照 |
| `docs/learn/phase41/_PHASE41_SPEC.md:13` | 35 工具数核对 | Phase41 时点规格 |
| `docs/learn/phase42/block2-mobile-report.md:49,50` | 移动端只读清单页=35 工具判定 | 本轮移动端快照的历史来源；Phase42 时点 |
| `docs/learn/phase51/_PHASE51_SPEC.md:4,9`；`block2-block4-alignment-and-soak.md:1,22,30` | 35 工具三通道对齐 | Phase51 时点审计 |
| `docs/learn/phase54/receive-capability-audit.md:52` | Agent 35 工具 | Phase54 时点审计 |
| `docs/learn/phase62/orphan-features.md:4,7,44` | 45 工具 UI 接线普查 | Phase62 时点（45 版） |
| `docs/learn/phase62/tool-schema-field-audit.md:23,100,131` | 45 工具逐字段表 | Phase62 时点（45 版） |
| `docs/learn/phase62/tool-three-channel-audit.md:23,131-133` | 45 版三通道表；`:131-133` 记录上一轮 `agent-tool-documentation.md` 由 35→45 重生成的过程 | Phase62 时点；本轮 45→47 是其延续，旧档不回改 |
| `docs/learn/phase63/tool-three-channel-47.md:27,137` | 47 工具逐行表 / 金集=47 | 当前 phase 已为 47，与本轮口径一致，无需改 |

## 5. 诚实未完成项

- **Dart 未跑验证**：云端无 Flutter SDK，`flutter analyze` / `flutter test` 未执行；`mobile/test/tools_catalog_test.dart` 的 47 断言与页面渲染行为**待 SDK 环境验证**。本轮仅做静态逐行对齐与 grep 核对。
- **README.md:8,10**（§3）与 **agent-tool-documentation.md**（§2）未由本轮改：前者受本轮改动范围约束，后者需 cpp 侧新构建重生成。
- 未触碰未跟踪隔离文件 `cpp/tests/ui_diag_freeze.cpp`；未执行任何 git 写操作。

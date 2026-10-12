# Agent 驱动 UI（AIGUI / Generative UI）开放协议设计依据

> **文档性质**：本文件是外部公开规范与官方文档的**一手原文摘录 + 出处汇编**，作为 MBDSDR「模型动态编排工作台（AIGUI / 第 10 域「工作台编排」）」的设计依据。
> - 协议语义一律以引用 URL 的官方原文为准；本文**不自创协议、不替代规范**。
> - 中文为译注；英文引用块（`>`）为官方原文摘录（节选，非全文）。
> - **第 9 节「本项目取舍」是 MBDSDR 自己的工程决策，不是任何规范的原文**，已隔离标注。
> - 摘录与访问日期：**2026-10-12**。该领域在快速演进，开工实现前必须回对应 URL 核对最新版本（见第 11 节待核对项）。

---

## 0. 版本状态速记（截至 2026-10-12）

| 协议 / 文档 | 摘录时状态 | 许可 / 归属 | 在 MBDSDR 的角色 |
| --- | --- | --- | --- |
| **A2UI Protocol** | **v1.0 Candidate（候选）** | Open protocol，对比页标注 Apache 2.0 | 声明式 UI **内容格式**基准 |
| **AG-UI** | CopilotKit 发起、开源；多框架 1st/community SDK（含 C++/Dart） | 开源 | agent↔前端**事件传输层**（远期） |
| **SEP-1865 MCP Apps** | **Final**（2025-11-21，Extensions Track） | MCP 官方扩展 | 工具自带预制 HTML（iframe）路数，**不采用** |
| **MCP Client Best Practices** | MCP 官方文档（URL 为 2026-07-28 版） | MCP 官方 | 渐进式工具发现（工具二级分层） |
| **assistant-ui `generative-ui`** | 开源 npm 库（`@assistant-ui/generative-ui`），**非协议** | 开源库 | 单 `present` 工具 + 组件词汇表的成熟实现范式 |
| **Qt 动态 UI 机制** | Qt 官方文档 + KDAB 技术文章 | Qt / KDAB | 原生侧渲染器落地机制与安全告诫 |

---

## 1. 一句话选型

- 界面**内容格式**对齐 **A2UI**：声明式组件、宿主原生渲染、声明式数据无代码执行、跨 Web/移动/桌面、支持跨信任边界多 agent。
- **传输**：P0 复用 MBDSDR 已有的 MCP / 进程内直连，在工具结果里承载 A2UI 形状的 JSON；**AG-UI 事件流列为远期**（Web / 远程 / 流式 / 多客户端再上）。
- **不走 MCP Apps 的 iframe HTML 路数**（官方自述 Web-focused、远端服务器控制外观、界面由服务器预制而非模型流式生成），只借鉴其「工具元数据关联界面、预声明可审查、UI 发起写操作需用户同意」思想。
- 工具上下文采用 MCP 官方 **Progressive Tool Discovery** 三层模式做二级分层。
- Qt 渲染器干净室自写：**组件名白名单工厂**，不 eval、不动态 import、不加载任意 QML 字符串或外部 `.ui`。

> **关键事实（不得记错）**：A2UI 官方对比页写明 A2UI 与 MCP 的桥是 **“Upcoming bridge”**（截至摘录日尚未稳定）。因此 P0 **不得假设“A2UI 已有稳定的原生 MCP 绑定”**；先在自有 MCP 工具输出中承载 A2UI 形状的 JSON，待官方桥定稿后再对齐（见第 2.3、11 节）。

---

## 2. 协议分层（官方原文）

### 2.1 AG-UI 官方的三层协议划分

AG-UI 文档原文把三个开放协议按层划分：

> | Layer | Protocol / Example | Purpose |
> | **Agent ↔ User Interaction** | **AG-UI (Agent–User Interaction Protocol)** | The open, event-based standard that connects agents to user-facing applications — enabling real-time, multimodal, interactive experiences. |
> | **Agent ↔ Tools & Data** | **MCP (Model Context Protocol)** | Open standard (originated by Anthropic) that lets agents securely connect to external systems — tools, workflows, and data sources. |
> | **Agent ↔ Agent** | **A2A (Agent to Agent)** | Open standard (originated by Google) which defines how agents coordinate and share work across distributed agentic systems. |

AG-UI 自我定义原文：

> “AG-UI is an **open, lightweight, event-based** protocol that standardizes how AI agents connect to user-facing applications … the general-purpose, bi-directional connection between a user-facing application and any agentic backend.”

### 2.2 A2UI 官方三方对比表（At a Glance，原文照录关键行）

> | | **A2UI** | **MCP Apps** | **AG-UI** |
> | **Approach** | Declarative component blueprints | Pre-built HTML via `ui://` URIs | High-bandwidth protocol connecting backends to frontends |
> | **Rendering** | Native components (Angular, Flutter, Lit, etc.) | Sandboxed `iframe` | Developer-defined (any framework) |
> | **Styling** | Host app controls: inherits design system | Isolated: remote server controls appearance | Developer controls: part of the host app |
> | **Security** | Declarative data, no code execution | Sandboxed iframe isolation | Trusted code within your own app |
> | **Multi-agent** | ✅ Across trust boundaries | ✅ Multiple MCP servers | ⚠️ Primarily single-agent |
> | **Cross-platform** | ✅ Web, mobile, desktop, native | ⚠️ Web-focused (iframe) | ✅ Protocol is framework-agnostic |
> | **LLM generation** | ✅ Designed for streaming output | ❌ Pre-built by server | ✅ Via A2UI integration |
> | **Spec** | Open protocol (Apache 2.0) | MCP extension (SEP-1865) | Open source (by CopilotKit) |

### 2.3 三者关系与 MCP 桥状态（官方原话）

> “A2UI is a generative UI **specification** allowing agents to deliver UI widgets, where AG-UI is the Agent↔User Interaction **protocol** — which connects an agentic frontend to any agentic backend.”（AG-UI 文档）

> “AG-UI is a **transport protocol** … A2UI is a **UI format**: the payload that describes what to render. They're complementary: use AG-UI as the pipe, A2UI as the content. AG-UI has day-zero A2UI compatibility.”（A2UI 对比页）

> “**A2UI + MCP**: Upcoming bridge lets MCP servers provide A2UI blueprints alongside HTML resources.”（A2UI 对比页；注意 “Upcoming”）

> “MCP Apps treat UI as a **resource**: servers provide pre-built HTML via `ui://` URIs … A2UI takes a **declarative UI** approach: agents send component blueprints, but the host application controls styling, theming, and how those components are configured and rendered. Choose MCP Apps when the server should own the full UI experience; choose A2UI when you want dynamic, cross-platform UI that fits naturally into your app.”（A2UI 对比页）

---

## 3. A2UI v1.0 Candidate 规范原文摘录

### 3.1 设计哲学与消息类型

> “The A2UI Protocol is designed for **dynamically rendering user interfaces from a stream of JSON objects** sent from an agent. Its core philosophy emphasizes a **clean separation of UI structure and application data**, enabling **progressive rendering** as the renderer processes each message.”

Agent→renderer 共四种核心消息（envelope 顶层键另含 `callRendererFunction`、`agentFunctionResponse`）：

- `createSurface`：创建一个新 surface 并开始渲染；
- `updateComponents`：向指定 surface 新增/更新组件定义；
- `updateDataModel`：向 surface 的数据模型插入或替换数据；
- `deleteSurface`：显式移除一个 surface 及其内容。

### 3.2 传输解耦（transport-agnostic）的契约

规范声明不绑定具体传输层，但传输必须满足：

> 1. **Reliable delivery**: messages must be delivered in the order they were generated. A2UI relies on stateful updates (e.g. creating a surface before updating it) …
> 2. **Message framing**: … newline-delimited JSON (JSONL), WebSocket frames, or SSE events.
> 3. **Metadata support**: … data-model synchronization (`sendDataModel`) … capabilities exchange (renderer capabilities — supported catalogs, custom components — and agent capabilities, via metadata or handshakes like Agent Cards in A2A or initialization in MCP).
> 4. **Bidirectional capability (optional)**: the rendering stream is unidirectional (Agent → Renderer); interactive applications require a return channel for `action` messages.

> “While A2UI is transport agnostic, it is most commonly used with … **AG-UI** … the standard transport binding for Agent-to-User Interaction.”

### 3.3 组件模型：扁平邻接表（adjacency list）

> “[`updateComponents`] provides a **flat list** of UI components to add or update … Relationships between components are defined by **ID references in an adjacency list**. The component with `\"id\": \"root\"` mounts as the child of the surface's canonical `Surface` container.”

> “Because components may reference children or data bindings that do not yet exist, renderers should **handle missing references gracefully by rendering placeholders (progressive rendering)**.”

每个组件对象：

- `id`（必填）：该组件实例的唯一 ID，用于父子引用；
- `component`（必填）：组件类型名（如 `"Text"`、`"Column"`、`"Button"`）；
- `catalogId`（可选）：覆盖 surface 级默认目录；
- 其余键为该组件类型的属性。

`createSurface` 隐式实例化规范定义的 `Surface` 容器（固定挂 `root`），`surfaceId` 必须在 renderer 生命周期内全局唯一；多 agent / 子 agent 场景由编排器用**前缀或 UUID** 防止 surfaceId 冲突。

### 3.4 Mixable catalogs（开放组件生态的关键条款）

> “Renderers can support components and functions from **multiple catalogs simultaneously within a single surface (mixable catalogs)**. When a renderer advertises `supportedCatalogIds` in its capabilities, components from any of those catalogs can be combined in the same UI tree. The set of available catalogs for a surface includes both `supportedCatalogIds` and the `catalogId` of any **inline catalog** declared in `inlineCatalogs` … All catalog IDs … must refer to catalogs which use the same A2UI specification version.”

关于 `catalogId`：

> “`catalogId` … is a string identifier, **not a resolvable URI**; while it is conventionally formatted as a URI (e.g. `https://mycompany.com/1.0/somecatalog`) to avoid naming collisions across organizations, it **does not need to point to any deployed resource or downloadable file**.”

### 3.5 数据绑定原语

- `DynamicString / DynamicNumber / DynamicBoolean / DynamicStringList`：属性可绑定数据，取值为三选一——**字面值**、**DataBinding**（`{"@path": "..."}`，使用 JSON Pointer）、**FunctionCall**（`{"@call": "...", "args": {...}}`）。
- `ChildList`：容器持有子组件的方式——`array`（静态的组件 id 引用列表）或 `object`（按某个数据绑定列表、用模板组件 id 动态生成子组件）。
- `AccessibilityAttributes`：标准化无障碍属性 `label` / `description` / `live`（off/polite/assertive）/ `hidden`。

### 3.6 动作：回传 agent 事件 vs 调用 renderer 本地函数

- Agent 动作（回传给模型）：

> ```json
> "action": { "event": { "name": "submit_form", "context": { "itemId": "123" } } }
> ```

- 本地动作（renderer 侧执行，再以 `functionResponse` 回送）：

> ```json
> "action": { "functionCall": { "call": "openUrl", "args": { "url": "${/url}" } } }
> ```

### 3.7 双向绑定：本地立即改、动作才回传（节省上下文的关键语义）

> 输入类组件（`TextField`/`CheckBox`/`Slider`/`ChoicePicker`/`DateTimeInput`）在用户交互时**立即更新 renderer 本地 data model**；但：
>
> “Two-Way Binding is **local to the renderer**. User inputs (keystrokes, toggles) do **not** automatically trigger network requests to the agent. The updated state is sent to the agent **only when a specific User Action is triggered** (e.g. a `Button` click).”

表单提交模式：输入组件绑定 `/formData/email` → 用户打字只改本地 model → 点 Submit 时 action 的 `context` 用 `{"path": "/formData/email"}` 把值解析后一次性回传。

### 3.8 `updateDataModel` 的 upsert 语义

> “Strict upsert semantics: if the path exists, the value is updated; if it does not exist, the value is created; **if the value is `null`, the key at that path is removed**.” `path` 省略或为 `/` 时替换整个 data model。

### 3.9 Renderer→Agent 消息

每个回传消息恰含一个顶层键：`action`、`callAgentFunction`、`rendererFunctionResponse` 或 `error`；函数调用用 `functionCallId` 在异步传输里做请求/响应关联。

---

## 4. assistant-ui `present` 工具范式原文（最直接的“工具化编排 UI”先例）

### 4.1 核心范式：发布组件词汇表，让模型组装

> “Let the model compose an interface at runtime from a **component vocabulary you ship** … you ship a **vocabulary** of components and let the model **assemble** them, instead of writing one component per tool. The model calls a **single `present` tool** whose arguments are a **JSON tree of component names and props**, and assistant-ui renders that tree.”

默认词汇表含 29 个组件（cards / facts / tables / charts / forms / controls），最短配置只需注册一个工具、不写任何组件。

### 4.2 模型产出的树结构

> “Every node is a flat object: **`_type` names the component, `children` nests, and every other key is a prop**.”

保留字：`_type`、`_key`、`_action`，以及所有以 `$` 开头的键（如注入的 `$status`、`$input`）；模型侧用下划线拼写是因为部分模型提供方拒绝 schema 属性名含 `$`。

### 4.3 安全边界（本项目白名单工厂的直接依据，原文）

> “**The library is the boundary on which components can render: the model can only name components you put in it, resolved by lookup with no `eval` and no dynamic import. Unknown names are dropped.**”

> “**Treat props as untrusted input**: validate URLs and values passed to APIs, and sanitize content rendered as raw HTML.”

补充：流式部分属性（`streamProperties`）时组件会收到未填完的值，需按不完整数据处理。

### 4.4 动作注册表与撤销窗

- `createActionRegistry({ purchase: async ({payload}) => {...} })`：模型在节点上放 `_action: {type, ...}`，按 `type` 匹配 handler；**未知 action 类型被忽略**（开发态告警）；没有注册表时树照常渲染、动作降级为 no-op。
- `undoable: true`：发送/删除等不可逆动作给 **5 秒撤销窗**（倒计时，再次点击或 Esc 取消）。
- 输入控件触发动作时把用户值作为 `$input`；动作内可用 `{"$field": "name"}` 引用表单内控件当前值，支持 `fallback`。

### 4.5 树是纯 JSON，不绑定浏览器（跨端 / 跨宿主）

> “**The tree is plain JSON, so it is not tied to the browser.**” 包根与 `/react`、`/vue`、`/slack`（转 Slack Block Kit）、`/teams`（转 Microsoft Adaptive Card）、`/a2ui`（“consumes A2UI surfaces over AG-UI”）分别消费同一棵树。

### 4.6 节奏控制

> “`present` is a frontend tool: it resolves in the browser, and the run only continues once its result is sent back. Without `sendAutomaticallyWhen`, the UI renders and the conversation then stops.”

（对桌面端的启示：渲染与对话是否继续的节奏由宿主显式控制。）

---

## 5. AG-UI 能力积木原文（远期传输层对照）

AG-UI Overview 列出的 building blocks（节选与 UI 相关项）：

> - **Generative UI, static**: Render model output as stable, typed components under app control.
> - **Generative UI, declarative**: Small declarative language for constrained yet open-ended agent UIs; **agents propose trees and constraints, the app validates and mounts**.
> - **Shared state** (read-only & read-write): typed store shared between agent and app, with streamed event-sourced diffs and conflict resolution.
> - **Interrupts (human in the loop)**: Pause, approve, edit, retry, or escalate mid flow without losing state.
> - **Sub-agents and composition**: Nested delegation with scoped state, tracing, and cancellation.
> - **Frontend tool calls**: typed handoffs from agent to frontend-executed actions, and back.
> - **Custom events**: open-ended data exchange for needs not covered by the protocol.

客户端不限于 Web：

> “An AG-UI client **does not have to be a web application**. The protocol describes an **event stream rather than a rendering target**, so a terminal, a mobile app, or a chat platform can each act as a client.”

（MBDSDR 注：社区 SDK 列表含 C++ 与 Dart；但 P0 为本地进程内通信，不需要 SSE/WS，AG-UI 留待 Web/远程。）

---

## 6. SEP-1865 MCP Apps 原文（记录“另一条路”及不采用理由）

### 6.1 提案摘要（Final，2025-11-21）

> “MCP Apps introduces a standardized pattern for **declaring UI resources via the `ui://` URI scheme, associating them with tools through metadata**, and facilitating **bi-directional communication** between the UI and the host using MCP's JSON-RPC base protocol … The initial specification focuses on **HTML resources** (`text/html;profile=mcp-app`).”

四个要素：

- **UI Resources**：用 `ui://` scheme 预声明资源；
- **Resource Discovery**：工具通过 metadata 引用其 UI 资源；
- **Bi-directional Communication**：UI iframe 与 host 之间复用标准 MCP JSON-RPC；
- **Security Model**：强制 iframe 沙箱 + 可审计通信。

### 6.2 为何用“预声明资源”而非内联

> 预声明允许 host 在工具执行前**预取模板**、把**表现（模板）与数据（工具结果）分离**以便缓存、并对 UI 资源做**安全审查**。

### 6.3 安全四件套（原文）

> - **Iframe sandboxing**: all UI content runs in sandboxed iframes with restricted permissions.
> - **Predeclared templates**: hosts can review HTML content before rendering.
> - **Auditable messages**: all UI-to-host communication goes through loggable JSON-RPC.
> - **User consent**: hosts can require explicit approval for UI-initiated tool calls.

HTML-only MVP 的理由：普遍支持、最简单安全模型（标准 iframe sandbox）、可截图/预览。该扩展为**可选**，需经 capability negotiation 显式协商，向后兼容。

### 6.4 MBDSDR 不采用此路数的依据

官方对比表中 MCP Apps 为 “Web-focused (iframe)”、外观 “isolated: remote server controls appearance”、内容 “Pre-built by server”（非模型流式生成）。MBDSDR 是桌面原生 Qt / Flutter 仪器软件，要求**原生组件渲染、继承本机设计系统、模型按实时测量数据现场编排**——这些特征与 A2UI 行一致。可借鉴点：工具经 metadata 关联界面、预声明可审查、**UI 发起的写操作需用户同意**（与 MBDSDR 现有写工具手动 gate 一致）。

---

## 7. MCP 渐进式工具发现原文（工具二级分层依据）

### 7.1 问题

> “When a host has access to dozens of servers exposing hundreds of tools, those definitions alone can **consume the majority of the context window before the model has even read the user's message**.” 预先全量加载浪费 token、增加延迟、降低模型表现。

### 7.2 做法

> “The host fetches tool definitions via `tools/list` as normal, but **defers injecting them into the model's context**. The host provides a lightweight **`search_tools` meta-tool** to the model. The host **loads full definitions into context only as needed**.”

启用阈值建议：当工具定义占上下文窗口的 **1%–5%** 时切换到渐进发现。

### 7.3 三层模式（Catalog / Inspect / Execute）

> - **Layer 1: Catalog** — `search_tools({query})` 只返回匹配工具名 + 一行简述。
> - **Layer 2: Inspect** — `get_tool_details({name})` 拉取**单个**工具的完整 input/output schema 与文档。
> - **Layer 3: Execute** — 模型只带着所需定义调用工具。

检索策略：Keyword（BM25/regex）、Embedding（同义/语义）、Subagent（小而快的二级模型）、Hybrid；OpenAI、Anthropic 已有内置 tool search。

### 7.4 动态服务器与技能联动

> 维护可用服务器注册表与高层描述；模型判定需要某服务器能力时才连接；任务不再相关则断开以释放上下文。agent 的 skill 文件可声明它需要哪些 MCP server，host 在该 skill 被调用时才连接。

### 7.5 实现要点与 prompt cache 共存

- 提供 name-only / name+description / full-schema 多档细节；host 侧缓存已拉取定义；收到 `notifications/tools/list_changed` 时重建索引；按来源服务器分组呈现。
- 为避免工具列表变化击穿 prompt 前缀缓存：把新发现的定义**追加到 cache 断点之后**、不重排 `tools` 数组，或让所有调用都走**单一稳定的 `call_tool({name, args})` 元工具**，使 `tools` 数组恒定；服务器断连按会话边界处理。

### 7.6 授权不变量与数量级（二手资料，非 MCP 官方原文）

- 社区对三元工具（`list_tools` / `load_tool` / `unload_tool` + always-load 热工具集 + allow-list）的安全总结：**“Progressive disclosure changes *when* a permitted tool's schema enters context, never *whether* the tool is permitted.”** allow-list 应在代理层强制，而非写进 prompt。
- 社区/厂商给出的数量级（非 MCP 官方结论，仅供量级参考）：可见工具超过约 30–50 个后选择准确率下降；约 400 个工具的 schema 可达 40 万 token；Anthropic 被援引称按需加载最多节省约 85%。

---

## 8. Qt 原生侧动态 UI 的官方机制与安全告诫

> 背景：MBDSDR 桌面端为 QWidget / QSplitter / QDockWidget；A2UI 官方 renderer 仅列 Angular / Flutter / Lit，**没有 Qt renderer**，需干净室自写。以下为 Qt 官方机制与权威技术资料。

- **QFormBuilder（Qt 官方，Qt Widgets Designer）**：

  > “The QFormBuilder class is used to **dynamically construct user interfaces from UI files at run-time**.”（`builder.load(&file, this)`）

  仅适用于可信的、Qt Designer 产出的内置 `.ui`；不应加载外部不可信 `.ui`。

- **QML Loader 按需加载（Qt 官方 Scalability 文档）**：

  > “A Loader can load a QML file (using the `source` property) or a Component object (using `sourceComponent`). It is useful for **delaying the creation of a component until it is required** … when a component should be created on demand.”

- **动态创建（Qt 6 Book, Dynamic QML）**：完整方案是 `Qt.createComponent` + `createObject`；`Qt.createQmlObject`（从 QML 字符串实例化）“does, however, have limitations”。

- **KDAB《Factory Design Techniques》（2024，Qt Quick + JSON 工厂）**：用 JSON 里的 `"component"` 字符串 **switch 到预定义 Component**（Button / Column / ColumnLayout…），以 Loader + Repeater **递归实例化组件树**，再按 JSON 属性配置布局——这正是“白名单工厂”形态。作者把安全单独成篇，明确指出：

  > “Some complications and **security implications** arise due to the fact that anchors can point to IDs … **security implications for which dynamically instantiating components from JSON might not be a good idea after all**.”（完整论据在该系列 Parts 3/4）

**由上述资料推导的渲染器安全准则（MBDSDR 工程准则，非 Qt 官方条文）**：组件类型字符串只能查编译期注册表，未知类型渲染占位或拒绝；不执行任意 QML/JS 字符串、不加载任意外部 `.ui`；属性按各组件 schema 校验并视为不可信输入；id / 锚点引用只能在同一受控 surface 内解析。

---

## 9. 本项目取舍（本节为 MBDSDR 工程决策，不是规范原文）

### 9.1 协议对齐

- **内容格式**：实现 A2UI v1.0 的一个务实子集——容器先做 Surface / Row / Column / Splitter / Tab，加 MBDSDR 仪器组件 id；数据绑定先支持字面值与 `@path`；暂不做 `@call` 与远程 renderer function。
- **传输**：P0 在现有三通道工具（tool_schema / executor / control_hub / MCP）的工具结果中承载 A2UI 形状 JSON（createSurface / updateComponents / deleteSurface 语义）。因官方 A2UI↔MCP 桥标注为 Upcoming，**不声称合规于某已定稿 MCP 绑定**，待其定稿再对齐。AG-UI（SSE/WS、HITL、shared state）列 P2 以后。
- **MCP Apps iframe**：不实现。

### 9.2 第 10 域「工作台编排」工具面

- 读工具：`list_ui_components`（组件目录，lazy 披露）、`get_workspace_layout`。
- 写工具（全部走现有手动模式 gate）：`arrange_workspace`（核心 present）、`resize_panel` / `focus_panel` / `emphasize_panel`、`add_panel` / `remove_panel`、`save_workspace` / `load_workspace` / `list_workspaces` / `reset_workspace`、`set_window_size`。
- 组件白名单即安全边界：只允许操作 QSplitter 比例、QDockWidget 停靠 / tabify、show/hide、stretch；不触碰数据采集与硬件；无 eval / 无动态 import。
- 默认布局为内置 default spec（现状三栏）；模型改动叠加为 overlay，`reset_workspace` 可回滚。
- **不预置场景剧本**：不写死“卫星通联 / 驻波诊断”固定布局。模型依据实时工具结果（VSWR、星座、底噪、解码状态）自行决定强调哪个面板——判断在模型，工具只提供能力与真实数据。

### 9.3 工具二级分层（77 工具向上百扩展）

- L0：`list_domains` / `list_tools`（仅名字 + 一行描述；组件目录同样 lazy）；L1：`load_tool_schema`（用时拉完整 schema）；少量热工具 always-load。
- 授权唯一事实源是 allow-list / 手动 gate：渐进披露只改变“schema 何时进入上下文”，不改变“工具是否允许被调用”。

### 9.4 开放生态两步走（桌面原生的客观安全边界）

1. **数据级开放（先做）**：第三方只能引用 MBDSDR 注册的原子组件，以配置 / 组合方式拼新面板（inline catalog 仅能引用白名单原子，不能引入新原生类型）。
2. **原生代码级开放（后期）**：允许第三方自带原生 widget 前，必须先具备进程隔离 / 签名 / 能力协商。桌面进程内第三方原生 widget 等同第三方原生代码，无沙箱即任意代码执行风险；隔离方案就位前不开放。

### 9.5 跨端

同一棵 workspace 组件树由 Qt（QWidget 白名单工厂，P0）与 Flutter（对齐 A2UI / Flutter GenUI 思路，P2）**同源渲染**；容器与组件 id、语义保持一致，视觉样式各自继承宿主设计系统。

### 9.6 阶段

- **P0**：A2UI 子集 spec + Qt 白名单工厂 + create/update/delete surface + arrange/get/list/reset + default spec + 640/960/1920 三档快照 0 裁切、0 叠字验收。
- **P1**：`@path` 数据绑定（读数实时进面板）+ 工具 progressive discovery 三元组 + workspace 存取 + A2UI 形状经 MCP 输出（第三方 agent 可驱动界面）+ 能力协商（`supportedCatalogIds`）。
- **P2**：Flutter 同源渲染 + 数据级第三方 inline catalog + A2A 多 agent surface 命名空间隔离；评估 AG-UI 与定稿后的 A2UI↔MCP 桥。

### 9.7 许可与干净室

A2UI（Apache 2.0）、AG-UI（开源）、MCP（开放协议）仅作为协议与机制参考；MBDSDR 整体 MIT，渲染器与工具代码干净室自写，**不复制上述库源码、不引入其 npm 依赖**；assistant-ui 只借鉴工具形态与安全模型。

---

## 10. 出处清单（一手 URL，摘录日 2026-10-12）

**规范 / 官方文档（一手）**

1. A2UI Protocol v1.0 — Candidate：<https://a2ui.org/specification/v1.0-a2ui/>
2. A2UI 官方对比（At a Glance；与 MCP Apps / AG-UI / A2A / MCP 的关系；MCP 桥状态）：<https://a2ui.org/introduction/agent-ui-ecosystem/>
3. AG-UI Overview：<https://docs.ag-ui.com/introduction>
4. SEP-1865 MCP Apps（Final）：<https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp>
5. assistant-ui Generative UI（`present` 工具 / 安全边界 / 跨端）：<https://www.assistant-ui.com/docs/tools/generative-ui>
6. MCP Client Best Practices — Progressive Tool Discovery：<https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices>
7. Qt QFormBuilder（运行时从 `.ui` 构建）：<https://doc-snapshots.qt.io/qt6-6.12/qformbuilder.html>
8. Qt Scalability — Loader 按需加载：<https://doc.qt.io/qt-6/scalability.html>
9. Qt 6 Book — Dynamic QML（createComponent / createObject / createQmlObject）：<https://www.qt.io/product/qt6/qml-book/ch15-dynamicqml-summary>
10. KDAB — Laying Out Components with Qt Quick and JSON（JSON 工厂；安全见 Parts 3/4）：<https://www-1.codebrowser.dev/laying-out-components-with-qt-quick-and-json/>

**二手解读 / 数量级参考（非规范原文，引用时已标注）**

11. 三元工具 list/load/unload 与 allow-list 不变量：<https://dreaming.press/posts/build-progressive-tool-disclosure-mcp-client.html>
12. Dual-path routing（主模型固定核心工具集 + router 子模型搜全量）：<https://arxiv.org/html/2608.22708>
13. AWS — MCP tool design（参数数量、enum 约束、lazy、省约 85% 表述）：<https://aws.amazon.com/blogs/machine-learning/mcp-tool-design-practical-approaches-and-tradeoffs/>

---

## 11. 开工实现前必须回原文核对的待确认项

- A2UI 摘录时为 **v1. Candidate**：消息 schema、catalog 协商、`@call` 语义、无障碍属性可能变化，以官网最新 spec 为准。
- **A2UI↔MCP 桥摘录日为 “Upcoming”**：其定稿形态（tool result / resource subscription / 新 capability 协商）直接决定 P1 传输实现，不得凭本文假设。
- A2UI 官方**无 Qt renderer**：白名单工厂、邻接表到 QSplitter/QDockWidget 的映射为本项目自定，必须自行补单测钉扎，不能宣称“官方支持 Qt”。
- KDAB 关于动态实例化安全的完整论据在系列 Parts 3/4（anchors 指向 ID 的具体攻击面），实现动态引用解析前应回读。
- 若 P2 采用 AG-UI，其 16 类事件、HITL、shared state、custom event 的具体 schema 需回 docs.ag-ui.com 的 Events / Core architecture 原文。
- assistant-ui 为快速演进的 npm 库，`_action` / `$field` / spec mode 等 API 以其当前版文档为准；本项目不直接依赖该库。

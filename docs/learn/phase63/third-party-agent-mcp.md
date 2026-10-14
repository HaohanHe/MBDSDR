# 第三方 Agent MCP 接入（headless stdio JSON-RPC）

**模块**: `cpp/src/agentapi/mcp_server.{h,cpp}` + `cpp/src/agentapi/mbdsdr_mcp_main.cpp`
**二进制**: `mbdsdr_mcp`（无头，无 GUI）
**参考实现**: `mbdsdr_ai/core/mcp_server.py`（Python 版 stdio MCP）
**协议**: JSON-RPC 2.0 over stdin/stdout，**逐行 JSON**（非 Content-Length 分帧）

---

## 1. 为什么要这个服务

桌面端 AI 工具面原本只在 GUI 进程内被本地 LLM worker 调用。本模块把同一套
**声明式工具注册表**（`ai::toolDefs()`）原样暴露成 MCP，让任意外部 agent
（Claude Desktop / Cursor / 通用 MCP 客户端）通过 stdio 直接驱动接收机，
无需内嵌任何模型。

**零模型依赖**：`mbdsdr_mcp` 进程**不实例化 LLM 客户端、不读 API key、不发 chat
请求**。模型是外部 agent 自己的事——本进程只做工具发现与真实分发。链接
`mbdsdr_core` 是复用 `executeTool` 的必然（符号存在即可），运行路径永不触碰
`llm_client`/`llm_worker`。

---

## 2. 线协议

* 每行一条请求；每响应一行 JSON 对象 + `'\n'`，写后立即 `flush`。
* 解析失败的行：写 **stderr**（`[mcp] 无法解析 JSON: ...`），**stdout 不回任何东西**，
  与 Python 参考一致——错误流永不污染协议流。
* 通知（无 `id`，当前为 `notifications/initialized`）不回写 stdout。

### 2.1 方法表

| 方法 | 入参 | 结果 |
|------|------|------|
| `initialize` | — | `{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"mbdsdr-core","version":"0.1.0"}}` |
| `notifications/initialized` | — | 无回复（通知） |
| `tools/list` | `{}` / `{"domain":<名>}` / `{"detailed":true}` / `{"detail":"name"\|"desc"\|"schema"}` | 默认→9 域卡片(name)；`domain`→该域；`detail`/`detailed` 控字段粗细（见 §2.1.1/§2.1.2） |
| `tools/call` | `{"name":..,"arguments":{..}}` | `{"content":[{"type":"text","text":<executeTool 返回的 JSON 串>}]}` |

* **工具表唯一事实源**：`ai::toolDefs()`（底层 `toolDefsFromSpecs(registeredToolSpecs())`）。
  `tools/list` 里没有第二份手写清单；`inputSchema` 直接取 `ToolDef.parameters`
  （已是完整 JSON Schema object），缺 `required` 数组时补空数组 `[]`。
* 未知方法 → JSON-RPC 错误 `{"code":-32601,"message":"未知方法: <m>"}`。

### 2.1.1 tools/list 二级域分组视图（P1 #64）

工具数量增长后，一次性吐出全部 schema 对 LLM 是噪声。`tools/list` 改为**两级目录**：
第一级给 9 个**冻结域**卡片（只列工具名），第二级按需下钻到完整 schema。
域分配是**服务器侧视图**——`ai::toolDefs()` / `registeredToolSpecs()` 本体一个字不改，
分组只是把既有名字桶分到 9 个域（C++ 侧 `domainForTool`，Python 侧 `_TOOL_DOMAIN`，
两边**同 9 域名、同序**，跨语言钉扎）。

9 个冻结域（summary 为一句话用途）：

| 域 | 一句话 |
|----|--------|
| `SDR 调谐/VFO` | 接收机调谐、解调模式、带宽/静噪/噪声门限与多 VFO 及频谱显示参数控制 |
| `亚音数字解码` | CTCSS/DCS 亚音与 POCSAG/M17/VOR/ACARS/NAVTEX/FT8/LRPT 等数字报文解码 |
| `VNA 测量` | VNA 扫频设置、S 参数原始测量与设备级杂项（电池/频偏/电延迟/平均） |
| `VNA 分析` | 对测量数据做谐振/TDR/滤波/等效电路/阻抗匹配等分析 |
| `VNA 校准` | VNA 校准状态与校准流程控制 |
| `导出演制` | IQ 录制、录制状态与各类快照/报文结果导出 |
| `书签扫描卫星` | 书签管理、波段/链路扫描与卫星过境预报/调谐 |
| `系统设备` | 设备连接、频率基准校准、网络音频/音源接入与整体状态 |
| `记忆编排` | 本地持久记忆（SQLite FTS5）写入/覆盖/全文检索，按 user/agent/session 作用域分层 |

三种入参分支（C++ 与 Python 参考实现同形状）：

* **默认（无 params）** → `result.tools` = 9 张域卡片数组：
  `[{"domain":<名>,"summary":<句>,"tools":[<工具名>...]}, ...]`。
  把全部卡片的 `tools` 名展开即等于 `ai::toolDefs()` 名集合——**不丢、不重**。
* **`params.domain`**（精确名或容错：trim/小写/唯一子串）→ `result.tools` =
  该域完整工具列表 `[{name,description,inputSchema}...]`（schema 仍来自 `toolDefs()` 单源）。
  未知 `domain` → JSON-RPC 错误 `-32602`，message 里**诚实列出全部合法域名**。
* **`params.detailed:true`** → `result.domains` = 9 张卡片，每张 `tools` 带完整 schema，
  总数同样不丢。

> 跨语言一致性：Python 参考 `mbdsdr_ai/core/mcp_server.py` 用**同一组 9 域名**做分组
> （其工具表来自自身 `_build_tool_table`，逐名映射）。Python 面当前无 VNA 工具，故
> 三个 VNA 域与「记忆编排」域在该面为空卡片（诚实空态）；9 域名集合本身两侧逐名相等。

### 2.1.2 tools/list 多级详情分层（`detail` 按需分级 + 缓存语义）

域目录给了「有哪些工具」，但一次拉全量 schema（`~50KB`）对每次发现都太贵。
按 2026 MCP client best practice，`tools/list` 支持**按需分级**，由 `params.detail`
控制每条工具记录的字段粗细（C++ 与 Python 同形状、同合法值）：

| `detail` | 每条工具记录 | 用途 |
|----------|--------------|------|
| `"name"`（默认，省略） | 仅名字串 | 最小目录，先看全貌再决定下钻哪个 |
| `"desc"` | `{name, description}`（无 `inputSchema`） | 不发 schema 就能读懂每个工具干什么 |
| `"schema"` | `{name, description, inputSchema}`（全量） | 真正要构造 `tools/call` 参数时才拉 |

* `params.detailed:true` 是 `detail:"schema"` 的**兼容别名**，保留旧客户端不破。
* 省略 `detail` 时：目录默认回到 `"name"`；但**单独钻取某域**（带 `params.domain` 而
  不显式给 `detail`）仍返回完整 schema（向后兼容的富下钻）。一旦显式给 `detail`，
  目录与单域都按该级别返回。
* 未知 `detail` 值 → JSON-RPC 错误 `-32602`，message 诚实列出 `name/desc/schema`。
* 实测线报体积：`detail=name` 序列化长度明显小于 `detail=schema`（进程级用例钉死）。

**缓存语义（`listChanged`）**：`initialize` 的 `capabilities.tools` 现在声明
`"listChanged": true`。客户端可**缓存 tools/list 结果**，无需每次重拉；当工具表变化时，
服务端发 `notifications/tools/list_changed`，客户端收到即失效重建。
当前工具表是**静态**的（进程内不变），故服务端此刻**不会**主动发该通知——这是诚实声明，
为未来动态注册预留；若客户端反向把它当请求发回，无 id 则不回 stdout，带 id 则诚实回
`result:null`（不当成未知方法）。

**客户端建议的发现节奏**：先 `tools/list`（默认 name 目录）→ 锁定要下钻的域后用
`{"domain":<域>,"detail":"desc"}` 读描述 → 真要调用时再 `{"domain":<域>,"detail":"schema"}`
取 inputSchema。既省往返体积，又不牺牲任何 schema 精度。

### 2.2 写工具手动 gate（MCP 侧强制）

`tools/call` 命中写类工具（`ai::isWriteTool(name)`，读自声明式 spec 表）时：

* `arguments.arm_manual == true` → 先从 arguments **移除** `arm_manual` 键，再真实
  `executeTool`（该键不泄漏给底层执行器）。
* 否则 → **工具不执行**，text 载荷为 `ai::gatedToolResult(name)`：
  `{"ok":false,"gated":true,"error":"手动模式：未执行 <name>"}`。

这把“写动作必须显式确认”的安全语义外推到外部 agent：agent 想调谐/录制/改模式，
必须自己解释为什么要带 `arm_manual:true`。

### 2.3 错误形态（诚实空态）

* 无后端/未连接：工具返回 `{"error":"not_connected",...}` / 各读工具返回空数组与
  `provenance:"未连接"`，**绝不编造硬件值、绝不崩溃**（对齐 ControlHub null-engine
  错误包络）。
* 未知工具名：text 载荷含 `{"error":"unknown_tool","tool":<name>,...}`。
* 引擎为 null（`handleRequestLine` 的 `engine` 形参）：走 `executeTool` 的 null-engine
  诚实错误路径。

---

## 3. headless 运行

```bash
# 二进制自己会在 main() 里 qputenv 这两项；容器/无显示环境也可显式给：
QT_QPA_PLATFORM=offscreen MBDSDR_NULL_AUDIO=1 ./mbdsdr_mcp
```

* 用 `QCoreApplication`（`SpectrumEngine` 是 `QThread`、`AudioOutput` 是普通 QObject，
  不需要 widget 事件循环）。构造引擎前强制 offscreen + null audio，使无硬件时落在
  诚实 `NullSource` + `NullAudioSink`，不打开真实音频/渲染设备。
* `--help` 打印用法（stdio 三方法、`arm_manual` 语义）。
* stdin 收到 EOF 即优雅退出。

---

## 4. 客户端配置示例

### 4.1 Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "mbdsdr": {
      "command": "/abs/path/to/mbdsdr_mcp",
      "args": [],
      "env": {
        "QT_QPA_PLATFORM": "offscreen",
        "MBDSDR_NULL_AUDIO": "1"
      }
    }
  }
}
```

### 4.2 Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "mbdsdr": {
      "command": "/abs/path/to/mbdsdr_mcp",
      "args": [],
      "env": {
        "QT_QPA_PLATFORM": "offscreen",
        "MBDSDR_NULL_AUDIO": "1"
      }
    }
  }
}
```

把 `/abs/path/to/mbdsdr_mcp` 换成本机构建产物绝对路径。本进程不需要任何 API key /
模型配置；模型由 Claude Desktop / Cursor 自己提供。

---

## 5. 一次手动往返（验证用）

```bash
printf '%s\n' \
'{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
'{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_capabilities","arguments":{}}}' \
| ./mbdsdr_mcp
```

* 响应 1：`serverInfo.name=="mbdsdr-core"`。
* 响应 2：`tools` 数组长度 == `ai::toolDefs().size()`（当前 68，随工具增长）。
* 响应 3：text 解析后 `ok:true`、`provenance:"未连接"`（无硬件时诚实标注）。


---

## 工具权限规则集（2026-10-14，上游审计第一落点落地）

kimi-code（MoonshotAI/kimi-code）与 kilocode（Kilo-Org/kilocode）两份克隆审计
独立收敛于同一结论：二值 arm_manual 门是 MBDSDR agent 侧最大缺口。本批在
Python MCP 面落地第一段（C++ 面镜像待 mbdsdr_mcp 构建后同步，见文末登记）。

### 语义（Python 面）

- **三级策略**：读工具默认 `allow`；写工具默认 `ask`（= 既有 arm_manual 语义，
  完全向后兼容）；可经规则覆盖为 `allow`（免问自动执行）或 `deny`（硬否决，
  即使带 arm_manual=true 也不执行——对齐 kilocode hard ruleset veto）。
- **规则解析**：精确工具名 > fnmatch 通配（如 `set_*`）> 默认。deny 精确规则
  不被宽松通配放行。
- **会话批准缓存**：调用带 `arm_manual=true` 且 `approve_session=true` 时，
  按 (工具名, 规范化参数) 记入本会话免问名单；后续同工具同参数自动执行，
  结果带 `approval:"session"`（可观测，不隐形放行）；参数变化则重新询问。
- **信封**：gated 信封新增 `policy`（ask）与 `hint`（如何授权）两个引导字段，
  原三键（ok/error/gated）不变——外部 agent 收到门时知道下一步怎么走，
  不再是哑门。
- **配置**（可选）：`~/.mbdsdr/tool_policy.json`（或
  `MBDSDR_TOOL_POLICY` 环境变量覆盖路径）：
  `{"rules":[{"pattern":"shutdown","action":"deny"},{"pattern":"set_*","action":"allow"}]}`；
  文件缺失/坏 JSON -> 默认策略并在 stderr 明说（不静默吞、不伪造策略）。
- **测试注入**：`MCPToolSet(policy=ToolPolicy(...))`；活体规则文件经 env 覆盖。

### 登记（未做）

- C++ 面（llm_worker.cpp:120 门）规则集镜像：待本机 Qt MinGW 修复后随
  mbdsdr_mcp 构建落地，规则 JSON schema 与 Python 面同形（跨语言钉扎测试
  届时一起补，参考 aigui_messages 的 C++ registry 钉扎模式）。
- 域级 ruleset（域分组卡片 + 子代理继承裁剪，kilocode agent schema 内嵌
  permission: Ruleset 形态）与结构化 question 工具（kilocode
  question 服务）为下一批候选。
- deny 的工具从 tools/list 视野隐藏（kilocode 行为）暂未做：当前 deny 仍在
  目录可见但执行被拒——诚实可见 > 隐藏，待产品决策。


---

## 工具结果 spill 与 outcome 分类（2026-10-14）

上游审计第二落点：工具结果信封升级——长输出 spill 落盘 + 结果分类（outcome）。
read_spectrum/read_iq 等工具动辄返回上千点 JSON，全量塞进对话上下文会把 LLM
上下文打爆；desktop 侧 tool_registry 只有 4000 字符硬截断、无落盘指针。本批
在 Python MCP 面落地（测试 `mbdsdr_ai/tests/test_tool_result_spill.py` 钉死）。

### 机制

- 位置：`mbdsdr_ai/core/mcp_server.py` 的 `MCPToolSet.call_tool` 结果出口
  （handler 返回后统一过 `_finalize_tool_result`）。
- 序列化：handler 结果按与 `tools/call` 响应相同的选项序列化
  （`json.dumps(..., ensure_ascii=False, default=str)`），超长判定与客户端
  实际看到的文本同源。
- 未超阈值：**原对象原样返回（零行为变化）**——既有调用方的相等性断言不回退。
- 超阈值：完整 JSON 落盘，上下文只留 spill 信封（字段见下）。
- 会话批准标注（`approval:"session"`）若被 spill 信封替换，会一并补位进信封，
  不因落盘丢失可观测性。

### 信封字段

spill 信封（正常执行且超长）：

```json
{"ok": true, "tool": "read_spectrum", "outcome": "executed",
 "truncated": true, "spill_path": "~/.mbdsdr/tool_results/read_spectrum_20261014T083952_123456.json",
 "preview_head": "……≤600 字符……", "preview_tail": "……≤600 字符……",
 "bytes": 12345, "full_result_available": true}
```

`bytes` = 完整结果序列化后的 UTF-8 字节数（= 落盘文件大小）。

outcome 分类（对齐 kimi-code toolHooks outcome 枚举的 MBDSDR 子集；既有信封
只增 `outcome` 键、既有键一律不变，向后兼容）：

| outcome | 触发路径 |
|---------|----------|
| `executed` | 正常执行且结果超长（spill 信封；未落盘的小结果信封零变化、不带 outcome） |
| `gated` | 手动门未授权（既有 gated 信封 + `policy`/`hint`） |
| `denied` | deny 规则硬否决（既有 denied 信封，arm_manual 也拒） |
| `error` | SDRUserError / 内部异常 / unknown_tool / spill_write_failed |

### 阈值常量（模块级，可被测试 monkeypatch 注入）

- `TOOL_RESULT_SPILL_THRESHOLD = 4000`：序列化字符数，**严格超过**才落盘
  （等于阈值仍原样返回）；与 desktop `tool_output_max_chars=4000` 对齐。
- `TOOL_RESULT_PREVIEW_CHARS = 600`：`preview_head`/`preview_tail` 各取
  JSON 串头/尾的字符数上限；600×2 < 4000， spilled 结果头尾永不重叠。
- `TOOL_RESULT_SPILL_DIR = "~/.mbdsdr/tool_results"`：落盘目录，目录不存在
  则现场创建。

### spill 目录

`~/.mbdsdr/tool_results/<tool>_<YYYYMMDDTHHMMSS_ffffff>.json`（UTC 微秒
时间戳防同秒碰撞；工具名经文件系统安全过滤）。文件内容 = 完整结果的 UTF-8
JSON，`json.load` 可无损读回。

### 失败语义

spill 写盘失败（OSError，makedirs 或 open 任一环节）：**诚实返回**

```json
{"ok": false, "outcome": "error", "error": "spill_write_failed",
 "technical_detail": "<OSError 原文>"}
```

不假装成功、不给半截结果、不把超大原文塞回上下文，也不让 `call_tool` /
stdio 主循环崩；stderr 同步记一行（`[mcp] tool 结果 spill 写入失败...`），
不污染协议流。不可序列化的结果（如循环引用）按既有行为原样上交，不在此发明
新语义。

### 与 kimi-code 的对应关系

kimi-code 的 toolHooks 用 output/isError/truncated/spill 信封承载工具结果，
MBDSDR 落地的是其子集：

- `truncated` -> 本面 `truncated:true` + `preview_head`/`preview_tail`
  （kimi 只截断丢尾部；MBDSDR 落盘后另给 `spill_path`/
  `full_result_available` 指针，全文不丢）。
- `spill` -> 本面 `spill_path`（MBDSDR 子集：JSON 全文落盘 + 头尾预览；
  未做行号读回/分段检索——agent 面 sdr_tools 的 `sdr_spill_read` 是另一套
  文本 spill，两面暂不合并）。
- `isError` -> 本面 `outcome:"error"`（外加 gated/denied 两个 MBDSDR 门
  特有的分类，kimi-code 侧无对应）。
- `output` -> 本面未超阈值时的原样返回（零变化，不套信封）。

### 登记（未做）

- C++ 面镜像（`cpp/src/agentapi/mcp_server.cpp` 的 executeTool 出口）：
  待 mbdsdr_mcp 构建落地，信封 schema 与 Python 面同形（跨语言钉扎测试
  届时一起补）。
- spill 保留策略/清理：当前只写不删，`~/.mbdsdr/tool_results` 会随调用增长；
  保留期/TTL/容量上限（及与 `~/.mbdsdr/spill` 的统一治理）是下一批候选。
- spill 读回工具（在 MCP 面暴露按 spill_path 读回全文的工具）：当前 agent
  只能自行按路径读文件；是否产品化待决策。


---

## 工具权限规则集（2026-10-14，上游审计第一落点落地）

kimi-code（MoonshotAI/kimi-code）与 kilocode（Kilo-Org/kilocode）两份克隆审计
独立收敛于同一结论：二值 arm_manual 门是 MBDSDR agent 侧最大缺口。本批在
Python MCP 面落地第一段（C++ 面镜像待 mbdsdr_mcp 构建后同步，见文末登记）。

### 语义（Python 面）

- **三级策略**：读工具默认 `allow`；写工具默认 `ask`（= 既有 arm_manual 语义，
  完全向后兼容）；可经规则覆盖为 `allow`（免问自动执行）或 `deny`（硬否决，
  即使带 arm_manual=true 也不执行——对齐 kilocode hard ruleset veto）。
- **规则解析**：精确工具名 > fnmatch 通配（如 `set_*`）> 默认。deny 精确规则
  不被宽松通配放行。
- **会话批准缓存**：调用带 `arm_manual=true` 且 `approve_session=true` 时，
  按 (工具名, 规范化参数) 记入本会话免问名单；后续同工具同参数自动执行，
  结果带 `approval:"session"`（可观测，不隐形放行）；参数变化则重新询问。
- **信封**：gated 信封新增 `policy`（ask）与 `hint`（如何授权）两个引导字段，
  原三键（ok/error/gated）不变——外部 agent 收到门时知道下一步怎么走，
  不再是哑门。
- **配置**（可选）：`~/.mbdsdr/tool_policy.json`（或
  `MBDSDR_TOOL_POLICY` 环境变量覆盖路径）：
  `{"rules":[{"pattern":"shutdown","action":"deny"},{"pattern":"set_*","action":"allow"}]}`；
  文件缺失/坏 JSON -> 默认策略并在 stderr 明说（不静默吞、不伪造策略）。
- **测试注入**：`MCPToolSet(policy=ToolPolicy(...))`；活体规则文件经 env 覆盖。

### 登记（未做）

- C++ 面（llm_worker.cpp:120 门）规则集镜像：待本机 Qt MinGW 修复后随
  mbdsdr_mcp 构建落地，规则 JSON schema 与 Python 面同形（跨语言钉扎测试
  届时一起补，参考 aigui_messages 的 C++ registry 钉扎模式）。
- 域级 ruleset（域分组卡片 + 子代理继承裁剪，kilocode agent schema 内嵌
  permission: Ruleset 形态）与结构化 question 工具（kilocode
  question 服务）为下一批候选。
- deny 的工具从 tools/list 视野隐藏（kilocode 行为）暂未做：当前 deny 仍在
  目录可见但执行被拒——诚实可见 > 隐藏，待产品决策。


---

## PreToolUse 可否决钩子（2026-10-14，kimi-code 审计 Top-3）

形态对齐 kimi-code externalHooks 的「本地命令在关键点 gate 风险工具调用」，
MBDSDR 取进程内子集（不上外部命令执行面——那是另一个威胁模型，暂不开放）：

- 新事件 `AGENT_PRE_TOOL_USE`（hooks.py）：在 `MCPToolSet.call_tool` 的
  规则门与分发**之前**触发，payload = {tool, arguments, write}；
- 否决语义：任一同步钩子返回 `{"decision": "deny", "reason": ...}` 即拦截，
  信封 `{ok:false, gated:true, outcome:"vetoed", veto_reason}`；
- **优先级**：veto 压过 `arm_manual` 与 policy allow——与 deny 规则同级的
  运行时安全闸（外部编排方/测试可经 `toolset.hooks` 注册拦截）；
- 兜底：`Hook.trigger` 已把钩子异常降为 None 并留痕 stdout，`_pre_tool_veto`
  再兜一层（钩子整体故障记 stderr 放行）——观测面绝不当主流程单点；
- 每 toolset 实例独立 HookManager；未注入时用本进程实例。
- 测试 6 例（test_tool_hooks.py）：armed 写工具被否、读工具同样过钩、
  非 deny 结果放行、钩子异常不炸主流程、disable 后不否、实例隔离。
- 未做（登记）：hook 配置持久化之外的 C++ 面对等事件。
  （外部命令 hooks 已于同日下一批落地，见文末「外部命令 hooks
  （2026-10-14）」；威胁模型与 fail-open 取舍记在其节内。）


---

## 外部命令 hooks（2026-10-14）

kimi-code 审计 Top-3 的完整形态：进程内 PreToolUse 钩子（上一节）只覆盖
同进程回调，无法让**用户自有的本地命令**在生命周期关键点 gate 风险工具
调用。本批补齐外部命令 hooks（配置驱动），模块
`mbdsdr_ai/hook_commands.py`、测试 `mbdsdr_ai/tests/test_hook_commands.py`
（19 例）。

### 配置格式

`~/.mbdsdr/hooks.json`（路径可经 `MBDSDR_HOOKS_CONFIG` 环境变量覆盖；
构造 `HookCommandRunner` 时显式传 `path` 优先，对齐 tool_policy 的
`MBDSDR_TOOL_POLICY` 解析序）：

```json
{"hooks": [{"event": "agent.pre_tool_use",
            "command": ["python", "check.py"],
            "timeout_s": 30}]}
```

- `event`：当前仅支持 `"agent.pre_tool_use"`；未知 event 名**诚实跳过并
  在 stderr 明说**，不静默吞。
- `command`：非空字符串数组，`shell=False` 原样 execve（**不引入
  shell=True、不做任何参数插值**——事件数据只走 stdin，不拼命令行）。
- `timeout_s`：可选，默认 30s。
- 配置缺失 = 空转（默认 `MCPToolSet()` 的 runner 在无配置时不拦任何
  调用）；坏 JSON / 坏数组条目逐条 stderr 留痕、跳过、放行。
- 懒加载 + mtime 缓存：首次 `decide` 读盘，配置 mtime 变化才重载
  （复用 skill_registry 缓存思路，未做 inotify）。

### 否决语义

`MCPToolSet._pre_tool_veto` 在进程内 `HookManager` 判定放行后，追加运行
`HookCommandRunner.decide(ev)`（注入点：`MCPToolSet(hook_commands=runner)`）；
任一闸否决即拦，否决信封与进程内钩子同形：

| 子进程结局 | 判定 | 留痕 |
|-----------|------|------|
| 退出码 == 2 | **否决** | reason 取 stdout JSON deny 的 reason/message，否则 `hook_command_exit_2` |
| 退出码 0 且 stdout JSON `{"decision":"deny","reason"/"message":...}` | **否决** | 并入 `veto_reason` |
| 退出码 0 且 stdout 非 deny（含非 JSON） | 放行 | 无 |
| 其他非零退出码（1 等） | 放行 | stderr 记退出码 |
| 超时（默认 30s，`timeout_s` 可配） | 放行 | stderr 记超时（子进程已杀） |
| 命令不存在 / 启动失败 | 放行 | stderr 记启动失败 |

事件 JSON 经 stdin 以 UTF-8 传入，形态同 `hooks.Event.to_dict()`
（`event_type` + `data`，`data` 含 `tool`/`arguments`/`write`），stdout
回读供 deny 判定。

### 超时放行（fail-open）的理由

外部命令 hook 是**策略/观测面，不是可靠性单点**。若超时/崩溃/依赖缺失
按拦截处理，一个有 bug 的 check 脚本会把接收机全部工具调用冻结——这与
进程内 `Hook.trigger` 把钩子异常降为 None 的既有兜底同源。fail-open 的
代价是「观测故障期策略真空」，取舍依据：需要硬保证的拦截应落在进程内
hook 或 tool_policy deny 规则（同为本地信任面，不引入进程外故障域）。
超时与失败全部 stderr 留痕，失败显式报、不静默。

### 威胁模型（边界）

- **配置文件属于用户本人的信任面**：`hooks.json` 里的命令以调用方
  （MBDSDR 进程自身）的同等权限执行——能跑进程内 hook 的一切都能做。
  不要把来路不明的 hooks 配置装进来，那等同于把 shell 交出去。
- 已知并刻意收敛的攻击面：不引入 `shell=True`（无命令解释器、无元字符
   expands）、无参数插值（事件数据仅经 stdin 传递）。
- fail-open 的另一面：一个返回 exit 2 的本地脚本可任意否决工具调用
  （DoS 面）——仍在用户信任面内，与进程内 hook 无差别。

### 与 kimi-code 的对应关系

kimi-code externalHooks 的本地命令 gate：事件 JSON 走 stdin + 退出码 2
否决 + 30s 默认超时。MBDSDR 落地即该子集（stdin JSON / exit 2 / 30s
三件同形），差异仅在事件名集合与 fail-open 取舍（kimi 侧超时语义以
其实现为准，本面按仓库钩子兜底传统取放行）。

### 登记（未做）

- **其他生命周期事件**：当前只接 `agent.pre_tool_use`；post_tool_use /
  session 级事件（kimi-code 有更多 lifecycle hooks）待需求驱动。
- **per-tool 过滤**：规则暂按 event 匹配，不支持 `{"tool": "set_*"}`
  形式的工具名过滤（命令内可自行按 stdin JSON 判定，先不在此发明 DSL）。
- **配置 schema 校验严格化**：当前逐条诚实跳过（坏字段 stderr 明说），
  无 schema 版本号 / 无 unknown-key 告警；后续可引入显式 schema 版本。
- C++ 面镜像与跨语言钉扎测试：同本文件前几节登记，待 mbdsdr_mcp 构建。


---

## 域级工具规则 + 外部命令 hooks（2026-10-14，两份审计收尾）

**域级规则**（kilocode「agent schema 内嵌 permission: Ruleset」的轻量对等）：
tool_policy 的 pattern 新增 `domain:<域名>` 形态，`~/.mbdsdr/tool_policy.json`
可按整域授权，如 `{"pattern": "domain:SDR 调岑/VFO", "action": "allow"}`；
解析顺序 精确工具名 > 名通配 > 域精确 > 域通配 > 默认（deny 任意层优先生效）。
域名来自 MCP `_TOOL_DOMAIN` 映射（与 tools/list 域卡片同源）。用例：把常用
域整体设为 allow 免问、敏感域按域 deny。

**外部命令 hooks**（mbdsdr_ai/hook_commands.py，对齐 kimi-code externalHooks
安全子集）：`~/.mbdsdr/hooks.json`（或 `MBDSDR_HOOKS_CONFIG`）声明
`{"hooks": [{"event": "agent.pre_tool_use", "command": [...], "timeout_s": 30}]}`；
事件 JSON 走 stdin，退出码 2 或 stdout `{"decision":"deny"}` 即否决；超时/
崩溃/缺依赖一律放行并留痕 stderr（观测面 fail-open，硬拦截仍由进程内 hook 与
tool_policy deny 保证）；shell=False、无参数插值、配置属用户信任面（威胁模型
写入模块 docstring）。至此工具调用前共有四道串联：command hook -> 进程内
hook -> policy deny -> arm_manual 门。

**测试**：域规则 5 例 + MCP 集成 1 例；外部命令 hooks 19 例（真子进程，
覆盖 exit 2 / JSON deny / 放行 / 超时 / 坏配置 / env 覆盖 / 集成）。
全量 520 passed / 10 skipped。


---

## MCP 客户端（2026-10-14，Kilo Top-4）

kilocode 审计 Top-4：MCP 客户端需要 **transport 抽象 + 连接状态机**
（stdio / SSE / HTTP 传输，connected / failed / needs_auth 等连接状态）。
MBDSDR 此前只有服务端两副（`mbdsdr_ai/core/mcp_server.py` 的 Python 面、
`cpp/src/agentapi/mcp_server.cpp` 的 C++ 面）和一个 ESP32 特例桥
（`mbdsdr_mcp_client.py`，WebSocket→stdio，硬件专用）。本批新增**通用
MCP 客户端** `mbdsdr_ai/mcp_client.py`（SPDX MIT，中文注释，**纯标准库、
零新依赖**：只用 json / os / subprocess / threading / queue / urllib /
socket / enum / collections，不引入 requests 或任何三方件），
让 MBDSDR 的 agent 侧能消费外部 MCP server（第三方工具链、satdump 的
MCP 化、其他 agent 暴露的工具面）。

### 配置格式

`~/.mbdsdr/mcp_servers.json`（路径可经 `MBDSDR_MCP_SERVERS` 环境变量
覆盖；显式传 `path` 优先——与 `MBDSDR_TOOL_POLICY` / `MBDSDR_HOOKS_CONFIG`
同一解析约定；`utf-8-sig` 读取，透明剥离子 Windows 编辑器写的 BOM）：

```json
{"servers": [
    {"name": "demo", "type": "stdio",
     "command": ["python", "-m", "mbdsdr_ai.core.mcp_server"],
     "env": {"PYTHONPATH": "D:/mbdsdr/MBDSDR"}},
    {"name": "http1", "type": "streamable_http",
     "url": "http://127.0.0.1:50732/mcp",
     "headers": {"Authorization": "Bearer ..."},
     "timeout_s": 20}
]}
```

| 字段 | 适用 | 语义 |
|------|------|------|
| `name` | 全部 | 注册名（manager API 的句柄），非空字符串 |
| `type` | 全部 | `stdio` 或 `streamable_http`（唯一两个合法值，其余诚实跳过） |
| `command` | stdio | 非空字符串数组，`shell=False` 原样 execve（无 shell、无插值） |
| `env` | stdio | 可选对象，逐键覆盖子进程环境（与 `os.environ` 合并） |
| `url` | streamable_http | `http(s)://` 端点（MCP streamable HTTP 的 POST URL） |
| `headers` | streamable_http | 可选对象，随每个请求透传（Authorization 等） |
| `timeout_s` | 全部 | 可选，单次请求超时（默认 15s，必须为正） |

坏配置的诚实语义：文件缺失 -> `config_not_found` 信封；坏 JSON ->
`bad_json`；顶层非 `{"servers":[...]}` -> `bad_config`；**坏条目逐条
跳过并把理由收进 `skipped`**（缺 name / 未知 type / 缺 command/url /
坏 headers），绝不让一条坏条目把整个 manager 拖垮，也不静默吞。

### 传输层

- **stdio**（`StdioTransport`）：`subprocess.Popen`（text 模式、
  UTF-8、errors=replace），逐行 framing（与 mbdsdr server 侧同一约定）；
  `initialize` 握手带 `capabilities` + `clientInfo`，随后发
  `notifications/initialized`。stdout 由读线程逐行解析入队，请求侧按
  id 匹配 + 超时等待（Windows 无名管道没有 select，读线程+队列是跨平台
  的正解）；stderr 另起线程排入 50 行有界环形缓冲（诊断用，不进协议流）。
- **streamable_http**（`StreamableHttpTransport`）：`urllib.request`
  POST JSON-RPC，`Content-Type: application/json` +
  `Accept: application/json, text/event-stream`（MCP streamable HTTP
  约定）；`initialize` 响应的 `Mcp-Session-Id` 头被捕获并在后续请求
  回带；202 空体（通知的常态）识别为「无响应」。

**SSE 实现边界（诚实声明）**：`text/event-stream` 响应路径**已做**——
`data:` 多行拼接（SSE 语义以 `\n` 连接）、`event:` 字段记录、空行派发、
`:` 开头注释/keep-alive 跳过、按 id 匹配取回本请求的 JSON-RPC 响应。
**未做（文档化 TODO，不伪造）**：SSE 断线重连续传（`Last-Event-ID` /
`retry`）、服务端→客户端的 GET 通知流、批量（batch）请求。即「能做多少
做多少」落在标准库舒适区内：单请求-单响应（JSON 或 SSE 两种外壳）完整
可跑，长连接/续传面明确留白。

### 连接状态机

| 迁移 | 触发 |
|------|------|
| `DISCONNECTED` → `CONNECTING` | `connect()` 开始握手 |
| `CONNECTING` → `CONNECTED` | `initialize` 握手成功 |
| `CONNECTING` → `FAILED` | 握手失败 / 子进程起不来 |
| `CONNECTED` → `FAILED` | 运行时传输故障（超时 / 子进程死亡 / IO 失败） |
| `CONNECTED` → `DISCONNECTED` | 主动 `disconnect()` |
| `FAILED` → `CONNECTING` | 失败后重连（`FAILED` 可重试） |

- **四状态钉死**：`disconnected` / `connecting` / `connected` / `failed`
  （`ConnState` 枚举，测试直接断言枚举成员表）。**needs_auth 是文档化
  TODO，未实现为第五状态**：HTTP 401 诚实落入 `FAILED` + 错误信封
  `error="needs_auth"`（`technical_detail.status=401`）——OAuth/token
  刷新整个认证面不在本批范围，宁可用错误码说话，不造一个永远不翻转的
  假状态。
- **运行时降级**：`CONNECTED` 期间发生超时 / 子进程退出 / IO 失败，
  状态降为 `FAILED` 并记录 `last_error`——状态永远反映事实，不假装
  还连着。反过来，服务端能回 **JSON-RPC error** 说明传输是活的，
  此时**不打** `FAILED`（信封如实报 `jsonrpc_error` + code/message）。
- **失败可重试**：`FAILED` 后再次 `connect()` 是普通重试路径
  （failed → connecting → connected 全链路有测试覆盖）。

### 单客户端纪律（rtl_tcp 式）

**每个已注册 server 同时最多一个活客户端**。对已 `CONNECTED` 的 server
再次 `connect()`：先断开旧连接（terminate 子进程 / 关流）再建新连接，
返回信封带 `replaced_existing: true`（可观测的接管，不是静默替换）；
对 `FAILED`/`DISCONNECTED` 的旧客户端则是普通重试，**不带**该标记
（诚实语义：没连上过就谈不上"替换"）。并发双 connect 由 per-name 小锁
串行化：4 线程同时连同一 server，最终只剩一个活客户端、旧子进程全部
被杀（测试 4 连钉死）。`disconnect()` 幂等（未连接也返回成功 + note）。

### API 与错误信封

`MCPClientManager()`：`register(entry)` /
`register_from_config(path=None)` / `connect(name)` /
`disconnect(name)` / `disconnect_all()` / `status()` / `tools(name)` /
`call(name, tool, args)`。**全部返回 dict 信封、不向调用方抛异常**
（编程错误除外）：未知名 `unknown_server`、未连接 `not_connected`、
超时 `timeout`、JSON-RPC error `jsonrpc_error`（detail 带 code/message）、
HTTP 错误 `http_error` / `needs_auth`（technical_detail 带 status 与
body 片段）、坏响应 `bad_response`、坏参数 `bad_arguments`、起进程失败
`spawn_failed`、传输中断 `transport` / `transport_closed`……每个信封都带
`suggestion` 或 `technical_detail`，绝不伪造成功。

`tools/list` 做了**形状归一化**：mbdsdr 自己的 server 无 params 时只回
域卡片 name 级（省 token 的目录约定），客户端显式带 `detail="schema"`
并把三种返回形状（标准 flat / mbdsdr 域卡片 name 级 / mbdsdr
`domains` detailed）统一归一成扁平工具表，从域卡片展开时补记 `domain`
字段；无法识别的形状返回空表，不把不可解析的响应当成工具清单。
`tools/call` 结果（`content` 数组）翻译成可编程消费的值：单 text 段且
为 JSON 则解析，否则原文；`isError=true` -> `ok:false` +
`error:"external_tool_error"`。

### MCP 工具面（「外部工具」扩展域）

`mbdsdr_ai/core/mcp_server.py` 新增扩展域「外部工具」（与解码器长尾 /
人机问答并列，`PYTHON_EXTENSION_DOMAIN_SUMMARIES` 与 `_TOOL_DOMAIN`
同步，`tests/test_mcp_server.py` 的 `EXPECTED_EXTENSION_DOMAINS` 加新域）：

| 工具 | 读/写 | 门 | 语义 |
|------|-------|----|------|
| `mcp_client_list` | 读 | 无 | 各 server 状态 + serverInfo + last_error（不起子进程） |
| `mcp_client_connect` | 写 | arm_manual | 连接外部 server（起子进程/发请求；先断后连） |
| `mcp_client_disconnect` | 写 | arm_manual | 断开外部 server（杀子进程/关流；幂等） |
| `mcp_client_call` | 写 | arm_manual | 代发外部 `tools/call`（server/tool/arguments） |

`MCPToolSet` 持有一个 `MCPClientManager` 实例，构造即
`register_from_config()`（配置缺失 = 空注册的诚实空态，不抛不崩；
**构造期不连接**——连接是写工具动作，走既有 `arm_manual` 门）。
`shutdown` 工具顺带 `disconnect_all()`：外部客户端是 stdio 子进程 /
HTTP 会话，不清就会随 server 生命周期泄漏。

### 与 kilocode mcp/index.ts 的对应（粗粒度）

kilocode 的 MCP 客户端子系统（transports + auth 分层）是本设计的
参照系；MBDSDR 落地的是其**协议子集**，差异如实登记：

| kilocode 概念 | MBDSDR 对应 | 差异 |
|---------------|-------------|------|
| StdioTransport | `StdioTransport` | 同形（子进程 + 逐行 JSON-RPC + initialize 握手） |
| StreamableHttpTransport | `StreamableHttpTransport` | 单请求-单响应（JSON/SSE 两种外壳）完整；断线重连续传、GET 通知流未做 |
| SSETransport（旧式） | 未单独实现 | SSE 仅作为 streamable_HTTP 的响应外壳解析；旧式独立 SSE 传输（GET 长连接 + endpoint POST）未做 |
| auth / OAuth 流程 | **不做** | 401 -> FAILED + `needs_auth` 错误信封；`needs_auth` 状态、token 刷新、OAuth 发现均为文档化 TODO |
| 连接状态管理 | `ConnState` 四状态枚举 | kilocode 的 needs_auth 等细分状态未镜像（见上） |
| 工具结果消毒 | **不做** | 外部结果一律按不可信数据上抛，消毒责任在调用方 |

### 安全边界

- **外部 server 的工具结果一律当不可信数据**：本客户端只做协议转发与
  信封标注，不解释、不执行外部内容；`mcp_client_call` 的信封里
  `external_tool_error` 也明示"勿直接执行"。
- **连接动作走 manual 门**：connect/disconnect/call 均注册为写工具
  （会起子进程 / 发网络请求），无 `arm_manual` 即被既有规则门拦下
  （`mcp_client_list` 只读，不 gate）。
- **配置文件属于用户信任面**：`mcp_servers.json` 里的 `command` 以
  MBDSDR 进程同等权限 execve、`url`/`headers` 决定网络出口——同
  `hooks.json` 的威胁模型，不要把来路不明的配置装进来。

### 测试

`mbdsdr_ai/tests/test_mcp_client.py` 26 例：stdio 全链路对真 server
（initialize/notifications/tools/list/tools/call get_status）、单客户端
纪律（接管杀旧进程 / 失败重连不冒充接管 / 4 并发连只剩一个活客户端）、
配置诚实信封（缺失/坏 JSON/坏结构/5 类坏条目跳过）、状态机（四状态枚举
钉死、慢握手期间 connecting 可观测、spawn 失败、**握手超时也杀子进程**、
运行时可观测降级 failed、JSON-RPC error 保持 connected）、入站通知进
有界缓冲、streamable_http（JSON 路径 / SSE 多行 data + 注释跳过路径 /
Accept 头约定 / headers 透传 / 不伪造 session 头 / 401 needs_auth /
500 / garbage / 连不上）、MCP 工具四件套端到端（gated + arm_manual
真连真调真断）。

### 登记（未做）

- **认证/OAuth**：401 只是 FAILED + 错误信封；`needs_auth` 独立状态、
  token 刷新、OAuth discovery 全部未做（文档化 TODO）。
- **SSE 长连接面**：断线重连续传（Last-Event-ID/retry）、服务端→客户端
  GET 通知流、batch 请求未做。
- **通知消费**：服务端→客户端入站通知只进有界缓冲（`inbound`），没有
  订阅/回调表。
- **C++ 面客户端镜像**：同前几节登记，待 mbdsdr_mcp 构建后评估（服务端
  优先级高于客户端）。


---

## A2UI 便携 renderer + 演示闭环（2026-10-14）

「AI 编排 UI」在此前只有 agent 侧（构造/校验）与 C++ renderer（编不了）。
本批补上投影 renderer，把整环在无 Qt 机器上闭合：

- **mbdsdr_ai/aigui_render.py**：官方信封 -> 单文件 HTML 终态投影（纯函数）。
  语义对齐 renderer 契约：createSurface 整面替换、updateComponents 按 id
  合并、props.visible=false 收起（连 children 引用一并收起）、未知 id 诚实
  占位、updateDataModel 只读 JSON 面板、deleteSurface 回默认；扁平邻接表
  的 children 引用补渲（tab/center 视图）。**不持有布局状态、不与 C++ 争
  事实源**——它是投影，不是第二个模型（红线不动）。
- **mbdsdr_ai/aigui_demo.py**：`python -m mbdsdr_ai.aigui_demo --out x.html`
  四幕演示（默认三栏 280/800/280 -> 选中 tab:VNA 收起 CW/ADS-B ->
  updateDataModel 状态面板 -> 故意引用未知 id 看诚实占位）。
- **MCP 工具 aigui_render_preview**（工作台编排域，读）：编 (op, components,
  data_model) 直接出 HTML 文件返回路径；产物是对话层预览，不碰布局模型/
  硬件，不走 arm_manual 门（同 ask_question 判定）。
- 测试 9 例（test_aigui_render.py）：终态非逐帧、合并/收起/占位/数据面板/
  deleteSurface 复位、纯函数确定性、演示信封全部官方形状、MCP 落盘+默认
  目录。踩坑记录：新工具忘记登记 _TOOL_DOMAIN 触发「未分配工具」钉扎测试
  （分组视图静默丢工具正是该测试要防的）。
- 位置声明：这是 cc1 损坏期间的**可用见证**与 P2 Flutter renderer 的协议
  参照；C++ renderer 落地后两者应保持投影一致（后续可加跨语言快照比对，
  参考 aigui_messages 的 component_registry 钉扎模式）。

全量 564 passed / 10 skipped。

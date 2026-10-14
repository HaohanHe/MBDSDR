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

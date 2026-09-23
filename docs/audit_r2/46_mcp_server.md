# 46. MCP 服务器入口审查 — `mbdsdr_ai_mcp_server.py`

> 审查范围：`/home/user/Doubao/chats/38438160041798146/mbdsdr_ai_mcp_server.py`（404 行，14.4 KB）
> 审查方式：静态阅读 + 真实 JSON-RPC 握手回放（用 Python subprocess 模拟 Cursor/Claude Desktop 客户端）+ 边界用例压测
> 关联文件：`mbdsdr_ai/tool_registry.py`、`mbdsdr_ai/agent.py`、`mbdsdr_ai/sdr_backend.py`、`mcp_server.example.json`、`MCP配置示例-Cursor-Claude.md`

---

## 0. 总体结论

这是一个**手写的、最小可用的 MCP stdio 服务器**，没有使用官方 `mcp` Python SDK，而是自己用 `sys.stdin` 行迭代 + `json.dumps` 实现了 JSON-RPC 2.0 over stdio。

- 它**确实**通过 `self.tool_registry.call(...)` 转发到真实的 `ToolRegistry`（`mbdsdr_ai_mcp_server.py:96,167`），**没有自己重写一套工具注册表**——这是好的。
- 它**确实**把注册表里的全部工具暴露出去了：实测 `tools/list` 返回 **250 个工具**（与用户说的"约 250"吻合；文件 docstring 第 5/28/66/313 行写的"195 个"已过期，属文档漂移）。
- 基本握手（initialize → notifications/initialized → tools/list → tools/call → ping）**在实验室里能跑通**。
- 但在**真实 MCP 客户端边界**下有若干会导致进程崩溃或协议流污染的硬伤，详见下文。

---

## 1. 发现清单

### [真bug] 1.1 JSON-RPC 批量请求（array）直接打死进程

- **位置**：`mbdsdr_ai_mcp_server.py:299` → `handle_request` `:241`
- **现象**：stdin 循环里只 `json.loads(line)`，然后无脑 `request.get("method", "")`。如果客户端按 JSON-RPC 2.0 规范发批量请求（一行是一个 JSON array，例如 `[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]`），`request` 是 `list`，`.get` 抛 `AttributeError: 'list' object has no attribute 'get'`。
- **实测 traceback**（用 subprocess 真实复现）：
  ```
  File "mbdsdr_ai_mcp_server.py", line 299, in run_stdio
      response = self.handle_request(request)
  File "mbdsdr_ai_mcp_server.py", line 241, in handle_request
      method = request.get("method", "")
  AttributeError: 'list' object has no attribute 'get'
  ```
  进程退出码 1，客户端直接看到 EOF。
- **为什么是"实验室绿、真机红"**：实验室自测用 `--cli call_tool` 或单条 JSON，从不发批量；而某些 MCP host（尤其带批处理优化的 agent）在初始化阶段会批量探测方法。一发就崩。
- **建议修复**：在 `handle_request` 入口加 `if not isinstance(request, dict): return None`（或对 array 做循环分发）。

### [真bug] 1.2 非 dict 顶层 JSON（裸字符串/数字/数组）同样崩

- **位置**：`mbdsdr_ai_mcp_server.py:294-299`
- **现象**：`try` 块只包了 `json.loads`，没包 `handle_request`。任何能被 `json.loads` 成功解析但不是 dict 的输入（`"hello"`、`123`、`true`、`[...]`）都会落到 `request.get(...)` 崩掉。
- **实测**：发 `"hello"` 一行，服务进程立刻死，客户端侧 `BrokenPipeError`。
- **修复**：同 1.1，加 `isinstance(request, dict)` 守卫；对非法请求返回 `-32600` Invalid Request。

### [真bug] 1.3 客户端断开时 BrokenPipeError 打死进程

- **位置**：`mbdsdr_ai_mcp_server.py:301`
  ```python
  print(json.dumps(response, ensure_ascii=False), flush=True)
  ```
- **现象**：没有任何 `try/except BrokenPipeError`。当 IDE 关窗、重载 MCP、或 client 崩溃后，server 还在响应上一条请求时，`stdout` 已关闭，`print` 抛 `BrokenPipeError: [Errno 32] Broken pipe`，进程以退出码 1 退出。
- **实测**：关闭 stdout 后再发一条 `tools/list`，traceback 末尾：
  ```
  File "mbdsdr_ai_mcp_server.py", line 301, in run_stdio
      print(json.dumps(response, ensure_ascii=False), flush=True)
  BrokenPipeError: [Errno 32] Broken pipe
  ```
- **影响**：Cursor/Claude Desktop 在 reload window、切换工作区、断开重连时，server 经常以这种方式猝死，日志里看到一堆 traceback，用户体验就是"MCP 时好时坏"。
- **修复**：包 `try/except BrokenPipeError: return`，或用 `sys.stdout.write` + 检查。

### [真bug] 1.4 录制线程的 `print()` 污染 stdout 协议流

- **位置**：`mbdsdr_ai/sdr_backend.py:268` 和 `:308`
  ```python
  except Exception as e:
      print(f"录制线程错误: {e}")          # line 268
  ...
  except Exception as e:
      print(f"写 sidecar JSON 错误: {e}")  # line 308
  ```
- **现象**：MCP stdio 协议规定 stdout **只能**是 JSON-RPC 帧。这两个 `print` 在后台录制线程里，一旦录制过程中出错（写盘失败、sidecar 序列化失败），错误文本会直接混进 stdout 流，破坏下一条 JSON-RPC 帧的 framing。
- **为什么是"实验室绿、真机红"**：
  - 实验室/CLI 模式下，stdout 本来就给人看，`print` 是正常日志；
  - 真实 MCP 客户端把 stdout 当管道，一行脏数据会让 client 端 `json.loads` 失败，整个连接挂掉。
- **同类风险**：`mbdsdr_ai_mcp_server.py` 自己的 `_log()` 已经正确走 stderr（`:117-120`），但**它无法约束它拉起的 SDR 后端线程**。
- **修复**：把这两处改成 `print(..., file=sys.stderr)` 或走 logging。

### [真bug] 1.5 `handle_request` 对 error 分支的包装不一致（潜在）

- **位置**：`mbdsdr_ai_mcp_server.py:259-273`
- **现象**：未知方法在 `:260-267` 直接 `return {"jsonrpc":"2.0","id":...,"error":{...}}`（正确，不包 result）。但如果未来有人在 `else` 分支后误加逻辑，或某个 handler 抛异常，异常会穿透到 `run_stdio` 的 `for line in sys.stdin`——`handle_request` 本身没有 try/except，`run_stdio` 也没有。
- **现状**：当前三个 handler（initialize/tools_list/tools_call）都不抛，所以暂未触发；但 `_handle_tools_call` → `call_tool` 内部已经 try/except 包住了（`:193-203`），所以这条暂时是 latent。
- **定级**：潜伏 bug，建议在 `handle_request` 外层包一层兜底，把任何未捕获异常转成 `-32603` Internal error，而不是打死进程。

---

### [空壳] 2.1 `self._initialized` / `self._request_id` 只写不读

- **位置**：`mbdsdr_ai_mcp_server.py:114-115`
  ```python
  self._initialized = False
  self._request_id = 0
  ```
- `_initialized` 在 `_handle_initialize` 里被置 `True`（`:209`），但**全文件没有任何地方读它**。意味着 server 在客户端还没 `initialize` 完就敢直接服务 `tools/list` / `tools/call`。
- `_request_id` 自始至终没自增、没读过——纯死代码。
- 这两个字段是"我打算做状态机"的占位，但状态机本身没实现。

### [空壳] 2.2 没有 `resources` / `prompts` / `logging` / `completion` 能力

- **位置**：`mbdsdr_ai_mcp_server.py:212-216`
  ```python
  "capabilities": {
      "tools": {"listChanged": False},
  }
  ```
- 只声明了 `tools`。这本身不算错（MCP 能力是可选的），但意味着：
  - 客户端不能通过 `resources/read` 读 SDR 状态快照；
  - 不能用 `prompts/get` 拿预设的 SDR 操作提示词；
  - server 不能通过 `logging/setLevel` 把后端日志推回给客户端。
- 论文里讲的"AI 即工具"目前只暴露了 tools 这一面，resources/prompts 都是空壳占位。

### [空壳] 2.3 `else` 分支的二次 `json.dumps` 是死代码

- **位置**：`mbdsdr_ai_mcp_server.py:171`
  ```python
  content_text = result.content if isinstance(result.content, str) \
      else json.dumps(result.content, ensure_ascii=False, indent=2)
  ```
- `ToolResult.content` 在 `tool_registry.py:27` 声明为 `str`，且 `registry.call()` 所有分支都把 content 塞成字符串（dict 分支也会 `json.dumps` 成字符串，见 `tool_registry.py:341`）。所以 `isinstance(result.content, str)` 恒为 `True`，else 分支永远不走。
- 不是 bug，但是占位代码——作者可能以为 handler 会返回 dict。

---

### [占位] 3.1 docstring 里的 "195 个工具" 已过期

- **位置**：`mbdsdr_ai_mcp_server.py:5, 28, 66, 313`
- 实测 `tools/list` 返回 **250** 个工具（见下方 §5 实测）。docstring 停在 195，是早期版本残留。
- 不影响运行，但会让读代码的人误判暴露面。

### [占位] 3.2 "API Key 认证" 的说法误导

- **位置**：`mbdsdr_ai_mcp_server.py:33`（docstring）和 `:91`
  ```python
  config = AgentConfig(api_key=api_key or "sk-mcp-server-no-key", ...)
  ```
- `MBDSDR_API_KEY` / `--api-key` 是给**上游 LLM API**（硅基流动）用的，**不是 MCP 客户端的接入凭证**。stdio MCP server 本身没有任何认证——任何能 spawn 这个进程的本地进程都能调全部 250 个工具。
- docstring 第 33 行写"API Key: 环境变量 MBDSDR_API_KEY 或 --api-key 参数"，容易让人以为这是 MCP 鉴权。

---

### [建议] 4.1 protocolVersion 硬编码为 `2024-11-05`，不协商

- **位置**：`mbdsdr_ai_mcp_server.py:211`
  ```python
  "protocolVersion": "2024-11-05",
  ```
- `_handle_initialize` 完全忽略 `params.protocolVersion`，永远回 `2024-11-05`。
- 当前主流客户端（Cursor、Claude Desktop 2025 版）握手时会发 `2025-06-18` 或更新。MCP 规范允许 server 选一个自己支持且 client 也支持的版本；硬回旧版本在大多数 client 上能跑（向后兼容），但一旦某个 client 严格只认新版本，就会握手失败。
- **建议**：至少在 `params.protocolVersion` 是更新版本时回一个受支持的交集，或升级到官方 SDK。

### [建议] 4.2 无工具权限控制 / 无 allowlist / 无速率限制

- **位置**：整个文件，无任何过滤逻辑
- **实测暴露的高危工具**（全部 250 个无差别暴露）：
  - `code_modify_file` / `code_modify_section` / `code_hot_reload` / `code_run_tests` / `code_git_commit` / `code_rollback`（自编程，能改 server 自己的源码）
  - `web_fetch_url`（SSRF，能拉任意 http/https）
  - `git_clone_repo`（能 clone 任意仓库到工作区）
  - `subagent_execute` / `orchestrator_execute` / `workflow_execute`（嵌套执行）
  - `sdr_transmit_cw`（发射无线电——法规风险）
- stdio MCP 通常信任本地 spawn 它的 host，但：
  1. 一个被 prompt injection 攻陷的 AI 对话可以调这些工具做破坏性操作；
  2. 没有 read-only / write / destructive 分层。
- **建议**：至少加一个 `--allow-tools` / `--deny-tools` CLI 参数，或在 `list_tools()` 里按 MCP annotations 标 `destructiveHint`。

### [建议] 4.3 MCP annotations 只设了 title，没设语义 hint

- **位置**：`mbdsdr_ai_mcp_server.py:141-144`
  ```python
  mcp_tool["annotations"] = {"title": f"[{tool['category']}] {tool['name']}"}
  ```
- MCP annotations 规范还支持 `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`。现在全靠 client 自己猜。
- 像 `sdr_set_frequency`（应 idempotent）、`code_modify_file`（应 destructive）、`web_fetch_url`（应 openWorld）都没标。

### [建议] 4.4 单线程 stdio 循环，长任务阻塞 ping / 取消

- **位置**：`mbdsdr_ai_mcp_server.py:288-301`
- `for line in sys.stdin` 同步处理。一个 `sdr_record_start` + 长录制、或 `sdr_spectrum_analyze` 大 FFT 会把循环堵住，期间 client 发 `ping` 得不到响应，MCP 心跳超时。
- 也没处理 `notifications/cancelled`，长任务无法取消。
- **建议**：工具调用丢到线程池，主循环只做 IO；或至少对慢工具加超时。

### [建议] 4.5 JSON 解析失败静默丢弃

- **位置**：`mbdsdr_ai_mcp_server.py:293-297`
  ```python
  except json.JSONDecodeError:
      self._log(f"JSON 解析失败: {line[:100]}")
      continue
  ```
- 按 JSON-RPC 2.0 规范，解析失败应返回 `-32700` Parse error。但因为连 id 都拿不到，回不出去——这是合理的妥协，不过 `_log` 只在 verbose 模式打印，生产环境静默丢帧，难排查。

### [建议] 4.6 `tools/list` 每次全量重建

- **位置**：`mbdsdr_ai_mcp_server.py:223-226` → `list_tools()` `:130-146`
- 每次 `tools/list` 都遍历 250 个工具重新拼 dict。因为 `self._tools` 在 `__init__` 里缓存了（`:99`），这个开销可控，但每次都重新构造 `annotations` 字典。
- 更重要的是：`self._tools` 是初始化时的快照，如果运行时通过 `register_mcp_tools` 动态加工具，MCP 侧看不到（`listChanged: False` 也声明了不会变）。一致性 OK，但要注意 `agent.register_mcp_tools` 在 MCP server 模式下不会被调用（因为没有下游硬件 MCP client），所以目前不影响。

---

## 5. 实测证据

用 Python subprocess 模拟真实 MCP 客户端握手（脚本见审查过程 `/tmp/mcp_handshake_test.py`）：

| 测试项 | 结果 |
|---|---|
| `initialize`（client 声明 `2025-06-18`） | server 回 `protocolVersion: 2024-11-05`，握手成功 |
| `notifications/initialized` | 正确无响应 |
| `tools/list` | 返回 **250** 个工具，每个都有 `name`/`description`/`inputSchema`/`annotations` |
| `inputSchema.type` 缺失检查 | 0 个缺失（全部有 `type: object`） |
| `tools/call sdr_set_frequency {frequency_hz:985e5}` | 成功，返回 `isError:false`，text="频率已设置: 98.500 MHz" |
| `tools/call nonexistent_tool` | `isError:true`，text 含"工具不存在" |
| `ping` | 返回 `{}` |
| `resources/list`（未实现方法） | 返回 `-32601` Method not found（正确） |
| 启动期 stdout 泄漏 | 0 字节（干净） |
| **批量请求 `[{...}]`** | **进程崩，AttributeError** |
| **裸字符串 `"hello"`** | **进程崩，BrokenPipeError on client side** |
| **客户端中途断开后再发请求** | **进程崩，BrokenPipeError at print** |

---

## 6. 与 tool_registry / agent 的关系（回答审查点 3）

- MCP server **没有自己实现一套工具表**，而是直接复用 `self.agent.tool_registry`（`mbdsdr_ai_mcp_server.py:96`）。
- 工具调用路径：`handle_request` → `_handle_tools_call` → `call_tool` → `self.tool_registry.call(name, arguments)`（`:167`）。
- `ToolRegistry.call()` 已经做了：工具名别名解析（`tool_registry.py:180-229`）、必填参数预校验（`:307-326`）、输出 head/tail 截断（`:374-383`）、异常捕获（`:361-368`）。MCP 层只是把 `ToolResult` 翻译成 MCP content 数组。
- **schema 转换是透传**：`tool_registry.list_tools()` 返回的 `parameters` 直接当 `inputSchema` 用（`:135`）。因为注册表的 `parameters` 本来就是 JSON Schema 风格（`type:object/properties/required`），转换无损。

---

## 7. 传输层（回答审查点 4）

- **stdio**，不是 HTTP/SSE。从 stdin 按行读、stdout 按行写 JSON（`:288-301`）。
- 帧格式：NDJSON（每行一个 JSON 帧，无 `Content-Length:` 头）。与 MCP stdio 规范一致。
- stderr 只用于 `_log()`（`:117-120`），不污染协议流——这一点是对的。
- 连接管理：**没有**。没有连接生命周期、没有 idle 超时、没有 client 断开检测（只能靠 BrokenPipeError 崩掉）。

---

## 8. 错误处理（回答审查点 5）

- 工具级错误：`ToolResult.success=False` → MCP `isError:true`（`:181-191`）。**正确**。
- 工具抛异常：`call_tool` 里 try/except 兜底（`:193-203`），转成 `isError:true`。**正确**。
- JSON-RPC 级错误：未知方法返回 `-32601`（`:263-266`）。**正确**。
- **漏洞**：
  - JSON 解析失败不回 `-32700`（丢帧，见 §4.5）；
  - 非 dict 请求直接崩（见 §1.1/1.2）；
  - BrokenPipeError 不捕获（见 §1.3）；
  - `handle_request` 没有最外层兜底（见 §1.5）。

---

## 9. 性能（回答审查点 9）

- **没有不必要的序列化开销**：`ToolResult.content` 已经是 str（registry 层已截断到 4000 字符），MCP 层直接塞进 `content[].text`，不会二次 `json.dumps`（else 分支是死代码，见 §2.3）。
- `tools/list` 每次重建 250 个 dict，约几百微秒级，可接受。
- 唯一的性能隐患是单线程阻塞（§4.4），不是序列化问题。

---

## 10. "实验室绿、真机红" 根因汇总

| # | 实验室为什么绿 | 真机为什么红 |
|---|---|---|
| 1.1 批量请求崩 | 自测脚本只发单条 | 某些 MCP host 初始化时发 batch |
| 1.3 BrokenPipe 崩 | 自测时脚本保持 stdin 打开 | IDE reload/关窗时 client 先关管道 |
| 1.4 录制线程 print 污染 | CLI 模式下 stdout 本来就是日志 | 真机把 stdout 当协议通道，脏帧杀连接 |
| 4.1 protocolVersion | 实验室用 2024-11-05 测 | 新版 client 发 2025-xx，server 硬回旧版 |
| 4.4 长任务阻塞 | 实验室只调快工具（set_freq） | 真机录 30s 时 ping 超时 |

---

## 11. 修复优先级建议

1. **P0（崩溃类）**：
   - `run_stdio` 主循环包 try/except，区分 `BrokenPipeError`（静默退出）/ 其他异常（转 `-32603` 或写 stderr 后 continue，不要崩）。
   - `handle_request` 入口加 `isinstance(request, dict)` 守卫。
   - `sdr_backend.py:268,308` 的 `print` 改成 `file=sys.stderr`。
2. **P1（协议健壮性）**：
   - 处理 `notifications/cancelled`；
   - `protocolVersion` 至少做交集协商；
   - 长工具丢线程池。
3. **P2（安全/体验）**：
   - 加 `--deny-tools` / `--allow-tools`；
   - 给 destructive 工具标 MCP annotations；
   - 更新 docstring 的工具数（195 → 250）。

---

*审查完成。只读，未修改任何代码。*

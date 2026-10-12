# Agent 本地记忆族设计笔记（P3 #73）

状态：已落地（工具 #73–#75，工具面 72→75）。
代码位置：`cpp/src/ai/memory_store.{h,cpp}`；工具 `memory_add`/`memory_search`/`memory_update`
（`cpp/src/ai/tool_schema.cpp` / `agent_tools.cpp` / `control_hub.{h,cpp}`）；
MCP 第 9 域「记忆编排」（`cpp/src/agentapi/mcp_server.cpp` + `mbdsdr_ai/core/mcp_server.py` 域集合钉扎）。

## 1. 存储设计

- 系统 SQLite C API（3.49.1，含 FTS5），**不引入 Qt SQL 插件**；`mbdsdr_core` 直链 `libsqlite3`。
- 固定路径 `~/.mbdsdr/memory.db`（测试用 `MBDSDR_MEMORY_DB` 环境变量重定向，生产路径永不被测试触碰）。
- 表结构：
  - `mem_meta(scope, scope_id, key, updated_ms, PRIMARY KEY(scope,key))` —— 元数据 + upsert 锚点。
  - `mem_fts(scope, scope_id, key, content) USING fts5(tokenize='unicode61')` —— 检索索引；
    显式 rowid 与 mem_meta 对齐，写入同事务（BEGIN IMMEDIATE / COMMIT）。
- **每调用 open→操作→close，无全局句柄**；WAL + synchronous=NORMAL，进程重启不丢数据。

## 2. 中文检索的关键决策

`unicode61` 把连续汉字串当作**单个 token**，直接拼原始 query 会导致中文永远检索不到。
本实现做双侧对称归一化：

- **索引侧**：content 写入 FTS 前在相邻汉字之间插入空格（`cjkSplit`），汉字逐字成 token；
  读回时仅删除「夹在两个汉字之间的空格」（`cjkJoin`）还原原文，英文单词空格原样保留。
- **查询侧**：非字母数字边界切词（`after-injection` → `after AND injection`，绝不把两个词拼成一个假 token），
  每个 token 再 cjkSplit，token 间 `AND` 拼接；全程绑定参数，**原始 query 永不拼入 MATCH 表达式**。
- snippet() 取 content 列命中片段（【】标记）；limit clamp 1..100，默认 20。

## 3. 作用域分层语义

| scope | 语义 | 典型内容 |
|---|---|---|
| `user` | 用户事实/长期偏好（跨会话稳定） | 台站天线高度、常用频偏、设备操作偏好 |
| `agent` | 模型经验/踩坑（跨会话积累） | 某拓扑无解条件、某设备测量陷阱、修好的参数 |
| `session` | 本次会话现场（短期上下文） | 当前过境参数、临时扫频结果、对话中刚确认的事实 |

`scope_id` 是作用域内实例标识（台站号、过境 id）。(scope, key) 唯一：
- `memory_add`：新增；重复 key 诚实报错并提示用 update 覆盖。
- `memory_update`：按 (scope,key) upsert，覆盖 content/scope_id/时间戳。
- `memory_search`：可选 scope / scope_id 过滤；无命中返回 `hits=[]` + note，**不编造记忆**。

## 4. checkpoint-writer 编排语义（文档级说明）

目标形态：记忆写入是**后台 writer 职责**，不占用主对话上下文。

- 编排层（未来）由 checkpoint writer 在回合间隙批量执行 `memory_add`/`memory_update`；
  主上下文只决定「记什么、写到哪个 scope」，不亲自同步等待 SQLite 落盘。
- **代码侧 gate 保持 arm_manual 不变**：memory_add/memory_update 仍是 `write=true` 手动门拦截工具，
  writer 作为受控调用方在非手动模式下调用，与人在环流程不冲突。
- memory_search 为只读工具，任何上下文都可即时查询，不经 gate。

## 5. 红线与诚实边界

- 禁 mock：测试用真实 SQLite 临时文件（`test_memory_store.cpp`）；回放夹具仅用于 VNA wire。
- db 打不开 / 目录不可写 / 空 query / 坏 scope：全部诚实 ok:false + 原因，不静默成功。
- 未连接、无命中等空态一律显式 note；禁止编造记忆内容。

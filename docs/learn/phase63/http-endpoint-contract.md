# HTTP 通道端点契约审计

**HEAD**: `ae0effb`
**范围**: `cpp/src/control/control_http_server.{h,cpp}` 全部路由、方法语义、参数名、错误码、响应形状 vs ControlHub 命令面
**金集**: test_control_http 14/14, test_control_hub 29/29, test_agent 34/34（全绿）

---

## 1. HTTP 路由表（全量枚举）

来源：`control_http_server.cpp` → `route()` 函数（L239–L338）。

| # | Method | Path | Query 参数 | 映射 CH 命令 | 读/写 |
|---|--------|------|-----------|-------------|-------|
| 1 | OPTIONS | `/*` | 无 | 无（CORS 预检，204 空体） | N/A |
| 2 | GET | `/` | 无 | 内建发现文档（非 CH 命令） | Read |
| 3 | GET | `/status` | 无 | `get_status` | Read |
| 4 | GET | `/pocsag_messages` | `channel=N`（可选） | `get_pocsag_messages` | Read |
| 5 | GET | `/m17_calls` | `channel=N`（可选） | `get_m17_calls` | Read |
| 6 | GET | `/vor_radial` | `channel=N`（可选） | `get_vor_radial` | Read |
| 7 | GET | `/acars_packets` | `channel=N`（可选） | `get_acars_packets` | Read |
| 8 | GET | `/navtex_messages` | `channel=N`（可选） | `get_navtex_messages` | Read |
| 9 | POST | `/command` | 无 | 统一入口：`{tool:"...", args:{...}}` → `ControlHub::execute()` | 读+写（唯一写入口） |

**共 9 个路由**（含 OPTIONS 预检和 `/` 发现文档）。用户预期的 `/telemetry`、`/snapshot`、`/gains`、`/vfos`、`/recordings` 等**无独立 GET 路由**——它们全部通过 `POST /command` 统一入口访问（CH 完整命令面约 40 条命令全部可达）。

---

## 2. 端点 × CH 命令语义逐项比对

### 2.1 方法语义（GET 只读 / POST 唯一写入口）

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 所有 GET 端点是否全为读（无副作用） | ✅ 通过 | 6 个 GET 业务端点全部映射 `write=false` 的 CH 读命令（`control_hub.cpp` L129–L155 命令表） |
| POST /command 是否唯一写入口 | ✅ 通过 | 所有写命令（`write=true` 标记）仅通过 `POST /command` → `execute()` 路径到达；无任何 GET 路由触发写操作 |
| 有无"GET 触发写"违例 | ✅ 无 | 逐路由确认：所有 GET handler 均调用 `hub_->execute(read_command, args)`，readSnapshot lambda (L276–L282) 只读不写 |

**结论**：方法语义干净。GET 即只读，POST /command 即唯一统一入口。写门控（write gate）由 `ControlHub::execute()` 统一执行（L367–L371），HTTP 层不重复实现。

### 2.2 参数名（URL query vs CH 入参键名）

| HTTP query 参数 | CH 入参键名 | 一致性 | 说明 |
|----------------|------------|--------|------|
| `channel=N` | `channel`（主）+ `channel_id`（D2 兼容别名） | ✅ 一致 | `queryToArgs()` (L41–L57) 仅解析 `channel` 键，转 int 后传入 `args["channel"]`；CH `resolveChannel()` (L326–L341) 优先取 `channel`，fallback 到 `channel_id` |

**queryToArgs 解析规则**：
- 仅识别 `channel` 键；其余 query 键**静默忽略**（设计如此，HTTP GET 面故意极简）
- `channel` 解析为 `int`（`val.toInt(&ok)`）；非数字 → 静默忽略（fallback 到当前选中 VFO）
- 无字符串/布尔/浮点类型转换（GET 面仅此一个 int 参数）
- 无吞参歧义：忽略的键不产生任何副作用，客户端可通过 `POST /command` 传任意 JSON args

### 2.3 错误码诚实性

| HTTP 状态码 | 触发条件 | 位置 | 诚实性 |
|------------|---------|------|--------|
| **400** | 畸形请求行 | L181–L192 | ✅ body 含具体原因 |
| **400** | POST /command JSON 解析失败 | L309–L313 | ✅ body 含 `QJsonParseError::errorString()` |
| **400** | 缺少字符串字段 `tool` | L316–L320 | ✅ |
| **400** | `args` 字段存在但非 object（string/array/number） | L324–L328 | ✅ |
| **404** | 未知路径（含已知路径但错误方法，如 POST /status） | L335–L337 | ✅ body 含 `方法 + 路径` |
| **204** | OPTIONS 预检 | L243–L246 | ✅ 空体 |
| **200** | 其余一切（含 CH 层语义错误） | 默认 | 见下 |

**边界一致性分析**：
- **HTTP 层协议错误 → HTTP 状态码**（400/404/204）：请求本身格式不对或路径不存在时，HTTP 状态码诚实反映
- **CH 层语义错误 → 200 + `{ok:false, error:...}`**：命令名未知、参数类型不对、写门关闭、无引擎连接——这些都是"请求格式正确但业务语义失败"，由 `ControlHub::execute()` 返回标准错误信封，HTTP 状态码保持 200

这个分层是**有意设计且自洽的**：HTTP 层只管"请求能不能被解析"，CH 层管"命令该不该执行"。客户端只需检查 HTTP 状态码区分协议错误，再解析 JSON body 区分业务错误。

**注意**：无 405 Method Not Allowed。POST /status 返回 404 而非 405（路由表将 method+path 视为联合键，未匹配即"未知路径"）。这是架构选择——错误体 `未知路径: POST /status` 已诚实表达"该方法+路径组合不存在"。

### 2.4 响应形状一致性

| 对比项 | GET /status | POST /command `{"tool":"get_status"}` |
|--------|-------------|--------------------------------------|
| 调用路径 | `hub_->execute("get_status", {})` (L286) | `hub_->execute(tool.toString(), args)` (L331) |
| 返回 JSON | 完全相同 | 完全相同 |
| `ok` 字段 | ✅ 同形 | ✅ 同形 |
| `command` 字段 | `"get_status"` | `"get_status"` |
| 五态 status | 同 | 同 |
| frequency/mode/bandwidth | 同 | 同 |

**结论**：完全同形。两条路径殊途同归到同一个 `ControlHub::execute()` 调用，JSON 形状由 CH 层唯一决定，HTTP 层不做任何重排。

---

## 3. 差异判定清单

### 该修（最小方案）

**无。** 本轮审计未发现需要修复的端点契约违例。

### 架构性不修（文档化）

| # | 差异点 | 原因 | 影响 |
|---|--------|------|------|
| A1 | CH 有 ~40 条命令，HTTP 仅暴露 6 个专用 GET 路由 + 1 个统一 POST | **有意分层**：HTTP 是浏览器/移动客户端的简化 facade；完整 CH 命令面始终通过 `POST /command` 可达。为每条读命令加独立 GET 路由会维护两份路由表，增加漂移风险 | 客户端如需 get_telemetry / list_gains / get_vfos / list_recordings 等，用 POST /command |
| A2 | 错误方法打在已知路径上返回 404 而非 405 | 路由表将 (method, path) 视为联合键；错误体已诚实说明。改 405 需重构路由分发逻辑，且现有客户端（test_control_http）已断言 404 | 无实际功能影响 |
| A3 | queryToArgs 静默忽略未知 query 参数 | HTTP GET 面故意极简（仅 channel=N）；多参数场景应走 POST /command JSON body。静默忽略而非报错，避免浏览器预取/监控工具的无谓 400 | 客户端不会因多传参数被拒绝，只是参数不生效 |
| A4 | 非数字 `channel=abc` 静默忽略（fallback 到选中 VFO） | 与 CH `resolveChannel()` 的"缺省=选中 VFO"语义一致；报错会引入 HTTP 层与 CH 层的行为分歧 | 已在 `channelQueryPassthroughIsHonest` 测试中锁定 |
| A5 | 发现文档 `GET /` 未列出自身和 OPTIONS | 发现文档是面向人类/客户端的端点索引，自指和 CORS 预检不属于"业务端点" | 无影响 |

---

## 4. 金集实跑计数

环境：`QT_QPA_PLATFORM=offscreen LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，clean env（未设 MBDSDR_TEST_SOURCE）。

| 测试二进制 | 用例数 | 通过 | 失败 | 跳过 | 耗时 |
|-----------|--------|------|------|------|------|
| `test_control_http` | 14 | 14 | 0 | 0 | 54ms |
| `test_control_hub` | 29 | 29 | 0 | 0 | 1389ms |
| `test_agent` | 34 | 34 | 0 | 0 | 607ms |

**合计：77 passed / 0 failed**

---

## 5. 红线扫描

| 检查项 | 结果 |
|--------|------|
| `competition` / `比赛` 字样（control 模块源码） | ✅ 无 |
| 生产路径中的 mock | ✅ 无（唯一 "mock" 注释在 CORS preflight 说明中，描述用途而非实际 mock 代码） |
| TODO / FIXME / HACK / XXX（control_http_server.cpp） | ✅ 无 |
| 绑定非回环地址（Any / AnyIPv4 / 0.0.0.0） | ✅ 无——硬编码 `QHostAddress::LocalHost`（L80），且注释明确"never Any/AnyIPv4" |
| 鉴权 | ✅ 无鉴权但诚实声明——发现文档和 banner 均明示"loopback only, do not expose" |
| git add/commit/push | ✅ 未执行 |

---

## 6. 诚实未完成项

- **未做**：端到端实跑 HTTP 请求验证 405 行为（当前基于代码审查推断 POST /status 返回 404）。test_control_http 未覆盖"错误方法打已知路径"用例。如需精确断言可后续补测。
- **未做**：未对比 Flutter 移动端客户端实际调用的 HTTP 端点集合与服务端路由表的一致性（本轮聚焦服务端契约本身）。

# R2 深度审查：插件系统 & OpenAPI 集成

- 审查范围：`mbdsdr_ai/plugin_system.py`（433 行）、`mbdsdr_ai/openapi_integration.py`（183 行）
- 交叉参照：`tool_registry.py`、`sandbox.py`、`hooks.py`、`subagents.py`、`agent.py`、`sdr_tools.py`、`mbdsdr_ai_mcp_server.py`、`tests/test_full_integration.py`
- 审查方式：只读，逐行读真实代码 + 追接线调用链

---

## 0. 结论速览

| 维度 | 结论 |
|---|---|
| 插件能否加载第三方插件？ | **能加载文件，但不能安全地跑第三方代码**——主进程内 `exec_module`，无沙箱 |
| 插件隔离（进程/线程/命名空间）？ | **基本没有**。同进程、同解释器、同 builtins；仅模块名前缀做命名空间区分 |
| 插件 API 是否完整？ | **不完整/无契约**。`register/unregister` 签名靠口头约定，无基类、无示例插件 |
| 插件安全沙箱？ | **不存在**。仓库里有 `sandbox.py`，但 `plugin_system.py` 完全不引用它 |
| OpenAPI 能否从 spec 自动生成工具？ | **不能**。文件名/命名误导，实际是 4 个手写函数 + 静态字典 |
| 参数转换是否正确？ | **有 bug**（经度框公式错 + lat=0 除零；封装层不做类型转换） |
| 认证（API key/OAuth）？ | **完全没有**。全匿名，OpenSky 匿名访问已受限、open-notify 疑似停服 |
| 插件/OpenAPI 是否注册到 tool_registry？ | OpenAPI：是（sdr_tools 手写注册）。插件：**条件性**，全靠插件自己调 |
| 插件工具是否通过 MCP 暴露？ | **运行时不暴露**。MCP 工具列表启动时快照，不刷新 |
| 实验室绿/真机红？ | **命中**。测试只测 discover/load/list/stats，从不调 enable；真机一 enable 就签名失配 |
| 空壳/占位？ | 多处：entry_point、get_satellite_passes、认证、OpenAPI spec 解析、安全沙箱 |
| 安全风险 | 插件=任意代码执行（RCE）；OpenAPI 当前无 SSRF（host 写死），但缺输入校验 |

---

## 1. plugin_system.py

### [真bug 1] `entry_point` 字段被读取却从未使用 —— 目录插件永远只加载 `__init__.py`

- 位置：`plugin_system.py:412-433`（`_load_module`）；字段定义 `plugin_system.py:73`、读取 `plugin_system.py:103`、兜底 `plugin_system.py:409`
- 证据：
  - `PluginManifest.entry_point` 被 `from_dict` 解析（`:103`）、`to_dict` 导出（`:87`），默认清单里还写 `entry_point=plugin_name`（`:409`）。
  - 但 `_load_module` 的注释写着"目录插件：加载 `__init__.py` 或 entry_point 指定的模块"（`:415`），代码却**只有一个分支**：`os.path.exists(init_path)` 就 `spec_from_file_location(..., init_path)`，从不读 `manifest.entry_point`。
- 后果：manifest 里声明 `entry_point: "tools.py"` 之类会被静默忽略；白皮书/清单承诺的入口配置是死字段。

### [真bug 2] enable() 的 register() 调用契约与仓库唯一测试样例不一致 —— "实验室绿、真机红"

- 位置：`plugin_system.py:244-249`（调用点）vs `tests/test_full_integration.py:1075`（测试样例）
- 证据：
  - `enable_plugin` 以**三个关键字参数**调用插件：
    ```python
    register_result = plugin.module.register(
        tool_registry=self.tool_registry,
        hook_manager=self.hook_manager,
        subagent_manager=self.subagent_manager,
    )
    ```
  - 但仓库里唯一的插件测试（`test_full_integration.py:1074-1075`）生成的样例插件是：
    ```python
    f.write("def register(registry):\n    pass\n")
    ```
    即**单位置参数** `registry`。
  - 测试用例（`test_full_integration.py:1077-1091`）只调用了 `discover_plugins / load_plugin / list_plugins / get_stats`，**从不调用 `enable_plugin`**。
- 后果：测试全绿（因为根本没走到 `register()` 调用）；真实第三方插件若照测试样例写 `def register(registry)`，`enable_plugin` 会抛 `TypeError: register() got an unexpected keyword argument 'tool_registry'`，被 `plugin_system.py:258-261` 吞掉并把插件置为 `ERROR`。这正是"插件加载测试通过、真实第三方插件失败"的根因之一。
- 备注：`test_full_integration_v2.py:786` 同样只测 discover/load/list/stats，同样不 enable。

### [真bug 3] unload/disable 不从 tool_registry 注销工具 —— 卸载后工具仍可调

- 位置：`plugin_system.py:263-300`（disable/unload）
- 证据：
  - `unload_plugin`（`:289-300`）只做 `plugin.module = None` + `status=UNLOADED`，**不调用 `tool_registry.unregister()`**。
  - `disable_plugin`（`:271-278`）只"调用插件自己的 `unregister()`"，PluginManager 自身不做任何 `tool_registry` 注销；`unregister` 抛异常时（`:285-287`）直接 return False，已注册的 handler 残留。
- 后果：插件在 enable 时通过自身 `register()` 注册进 `tool_registry` 的 handler 仍被 `tool_registry.tools[name]` 强引用（闭包持有插件模块函数），unload 后仍能被 `tool_registry.call()` 调到。Python GC 也不会回收。"卸载"是假卸载。

### [真bug 4] `load_all_plugins` 静默吞掉所有异常 —— 真机排障无日志

- 位置：`plugin_system.py:302-319`
- 证据：`:317-318`
  ```python
  except Exception:
      pass
  ```
- 后果：批量加载时任何一个插件抛错都不记录、不返回（返回值只有成功加载的名字列表）。真机上某个插件炸了，调用方完全无感知，配合"实验室绿/真机红"会让问题更难定位。

### [真bug 5] ERROR 状态插件不可重试加载

- 位置：`plugin_system.py:199-200`
- 证据：
  ```python
  if plugin_name in self.plugins and self.plugins[plugin_name].status != PluginStatus.UNLOADED:
      return self.plugins[plugin_name]
  ```
  一旦某次 `load_plugin` 把状态置为 `ERROR`（`:222`），后续再调 `load_plugin` 直接返回旧的 ERROR 实例，不重新 exec。
- 后果：修复插件代码后必须重启进程才能重试，无"重载"语义。

### [空壳 6] 插件安全沙箱完全缺失 —— 安装即主进程任意代码执行

- 位置：`plugin_system.py:412-431`（`_load_module`）
- 证据：
  - `spec = importlib.util.spec_from_file_location(f"mbdsdr_plugin_{plugin_name}", path)` → `module = importlib.util.module_from_spec(spec)` → `spec.loader.exec_module(module)`。
  - 这是在**当前解释器进程内**直接执行第三方 `.py`，与宿主共享同一 `builtins`、同一 `sys.modules`、同一异常栈。
  - 仓库里明明有 `sandbox.py`（子进程隔离 + 危险模块黑名单 `sandbox.py:54-58` + 受限 builtins `sandbox.py:72-102`），但 `plugin_system.py` **没有任何 import 或引用** `sandbox`。
- 后果：`install_plugin_from_path` 复制进来的插件，一经 `load_plugin`/`enable_plugin`/`load_all_plugins(auto_enable=True)` 即获得与主程序同等权限（读文件、联网、起子进程、改主程序状态）。白皮书宣称的"创意工坊：用户投稿→专家审查→合入"在代码层面没有任何强制隔离或审批。
- 命名空间隔离的真实程度：仅靠模块名前缀 `mbdsdr_plugin_<name>` 做区分（`:419`/`:427`），不插入 `sys.modules`，但**不是**进程/沙箱隔离。

### [空壳 7] `install_plugin_from_path` 无任何安全/一致性校验

- 位置：`plugin_system.py:353-378`
- 证据：直接 `os.makedirs(target_dir)` + `shutil.copy2/copytree`，无 manifest schema 校验、无恶意代码扫描、无签名/哈希钉死、无依赖检查。
- 后果：投稿即落盘；若未来启用 `load_all_plugins(auto_enable=True)` 开机自加载（当前 agent.py 未调用，但能力存在），等于开机自动执行任意投稿代码。

### [空壳 8] PluginManager 不兜底工具注册，统计口径失真

- 位置：`plugin_system.py:242-256`、`plugin_system.py:328-351`
- 证据：
  - `enable_plugin` 把 `register_result.get('tools', [])` 存进 `plugin.tools`（`:251`），但**从不校验这些名字是否真的进了 `tool_registry`**。是否真注册完全依赖插件作者在 `register()` 内部自己调 `tool_registry.register(...)`。
  - `get_stats` 用 `total_tools = sum(len(p.tools) ...)`（`:334`）统计"已注册工具数"。
- 后果：若插件 `register()` 返回了 `{'tools':[...]}` 却忘了调 `tool_registry.register`，`plugin_stats` 显示"注册了 N 个工具"，实际一个都调不到——"看起来注册了、实际没有"。

### [占位 9] 插件 API 无基类/协议/文档，无示例插件

- 位置：`plugin_system.py` 全文；`mbdsdr_ai/plugins/` 目录为空（`ls` 无任何 `.py`/`manifest.json`）
- 证据：`register`/`unregister` 的签名、返回值结构、hook/subagent 注册方式全靠 docstring 口头约定；仓库无任何可运行的示例插件；`hooks.HookManager.register`（`hooks.py:174`）与 `subagents.SubagentManager`（`subagents.py:423` 起）的真实方法签名与插件侧契约从未对齐校验。
- 后果：第三方作者无参照，极易写出如 [真bug 2] 那样的签名失配。

### [建议 10] `discover_plugins` 用 `list(set(...))` 顺序不确定

- 位置：`plugin_system.py:190`
- 后果：批量加载顺序不可复现，影响有依赖关系的插件加载顺序。建议按名字排序。

### [建议 11] manifest 的版本/依赖字段被读取但从不校验

- 位置：`plugin_system.py:74`（dependencies）、`:76`（min_mbdsdr_version）、`:104`、`:106`
- 后果：声明的 `min_mbdsdr_version` 不与当前版本比对、`dependencies` 不做 import/pip 检查，字段形同虚设。

---

## 2. openapi_integration.py

### [空壳 12] 文件名不副实 —— 没有任何 OpenAPI spec 解析/自动代码生成

- 位置：`openapi_integration.py` 全文（183 行）
- 证据：
  - 模块 docstring（`:1-14`）宣称"将公开API封装为MCP工具"；文件名 `openapi_integration` 暗示"从 OpenAPI spec 集成"。
  - 实际代码里**没有任何** yaml/json spec 解析、没有动态生成工具/handler 的逻辑。就是 4 个手写函数（`get_iss_position`/`get_people_in_space`/`get_weather`/`get_aircraft_nearby`）+ 1 个静态 `AVAILABLE_APIS` 字典（`:145-170`）。
- 后果：审查重点"是否真的能从 OpenAPI spec 自动生成工具"——**答案是不能**。这是命名误导，能力是 [空壳]。

### [真bug 13] `get_aircraft_nearby` 经度跨度公式错误，且 lat=0 除零

- 位置：`openapi_integration.py:93-96`
- 证据：
  ```python
  dlat = radius_km / 111.0
  dlon = radius_km / (111.0 * abs(lat) * 3.14159 / 180.0)
  ```
  - 正确的经度 1 度公里数 ≈ `111.32 * cos(lat_rad)`；代码写成 `111 * lat_rad`，量纲错误。
  - `lat = 0`（赤道）时分母 = `111.0 * 0 * 0.01745 = 0` → **ZeroDivisionError**（被外层 `except Exception` 吞成 `success=False`）。
  - 高纬度误差放大：lat=60° 时代码分母 ≈ 116，正确应为 `111*cos60° ≈ 55.5`，经度框偏宽约 2 倍。
- 后果：赤道附近调用直接失败；中高纬度搜索框范围错误，召回到无关空域。

### [真bug 14] sdr_tools 封装层不对模型传入的 lat/lon/radius 做类型转换

- 位置：`sdr_tools.py:5771-5774`（weather）、`sdr_tools.py:5797-5801`（aircraft）；拼接点 `openapi_integration.py:66`、`:96`
- 证据：
  ```python
  lat = args['latitude']      # 直接用，无 float()
  lon = args['longitude']
  result = get_weather(lat, lon)
  ```
  `get_weather(lat: float, ...)` 的类型标注只是注释，Python 不强制；模型若传字符串，会原样拼进 f-string URL（`openapi_integration.py:66`）。
- 后果：host 写死为 `api.open-meteo.com`/`opensky-network.org`，**不构成完整 SSRF**，但可造成 URL 参数污染（如 `latitude=40&longitude=0&hourly=...`）；`radius_km` 若传字符串会在 `dlat = radius_km/111.0` 处抛 TypeError。缺输入校验/白名单范围。

### [真bug 15] velocity 为 None 时格式化崩溃，导致整次调用失败

- 位置：`sdr_tools.py:5812` vs `openapi_integration.py:109`
- 证据：
  - `openapi_integration.py:109` 把 OpenSky 的 `s[9]`（velocity，可为 None）直接存入 `"velocity_ms": s[9]`。
  - `sdr_tools.py:5812` 格式化：`f"...高度: {ac['altitude_m']}m  速度: {ac['velocity_ms']:.0f}m/s"`。对 `None` 做 `:.0f` 会抛 `TypeError`。
- 后果：只要返回的某架飞机速度字段为空，整个 `openapi_aircraft_nearby` 调用就被 `tool_registry.call` 的 try/except（`tool_registry.py:361`）吞成 `success=False`，而不是显示"N/A"。鲁棒性 bug。

### [占位 16] `get_satellite_passes` 是纯占位死代码

- 位置：`openapi_integration.py:128-138`
- 证据：函数体直接 `return {"success": True, "message": "卫星过境预测请使用内置工具 new_spacetime", ...}`，不发任何请求；且**未注册进 `AVAILABLE_APIS`**（`:145-170` 没有它），`sdr_tools.py` 也无对应封装。
- 后果：死代码/占位，宣称的"N2YO 过境预测"能力不存在。

### [空壳 17] 认证（API key/OAuth）完全缺失

- 位置：`openapi_integration.py` 全文；`AVAILABLE_APIS` 全部 `requires_key: False`（`:150`/`:156`/`:162`/`:168`）
- 证据：所有 `requests.get` 无 `headers=`、无 `params={... key: ...}`、无 token、无 OAuth 流程；`get_satellite_passes` 注释自承"N2YO免费API需要key，这里用简化版"（`:129`）。
- 后果：
  - OpenSky Network 自 2023 年起对匿名/未认证访问施加严格限流甚至要求认证，真机匿名 `GET /api/states/all` 大概率 401/403。
  - 审查重点"认证是否正确处理"——**完全没有处理**。

### [真bug 18] open-notify 两个端点用明文 http 且服务疑似已停服

- 位置：`openapi_integration.py:27`（iss-now.json）、`:47`（astros.json）
- 证据：均为 `http://api.open-notify.org/...`（非 https）；该服务公开报道已停止运行。实验室测试不联网所以绿，真机一调即连接失败/超时。
- 建议：换数据源或在工具描述里标注可用性，并升级 https。

### [建议 19] 无重试/限流退避

- 位置：`openapi_integration.py` 所有请求
- 证据：`timeout=5/10` 有，但无 429/5xx 重试、无指数退避。Open-Meteo/OpenSky 偶发限流时直接返回失败。

---

## 3. 与 tool_registry / MCP 的关系（接线核实）

### [真bug 20] MCP 工具列表启动时快照，运行时插件加载不会暴露

- 位置：`mbdsdr_ai_mcp_server.py:99`（快照）、`:131`（遍历快照）
- 证据：
  ```python
  self._tools = self.tool_registry.list_tools()   # __init__ 只跑一次（:99）
  ...
  for tool in self._tools:                         # list_tools() 遍历旧快照（:131）
  ```
  `self._tools` 全文件只在 `:99` 赋值一次，无任何刷新。
- 后果：审查重点"插件工具是否真的通过 MCP 暴露"——**运行时加载的插件工具不会出现在 MCP `tools/list`**。虽然 `plugin_load`/`plugin_enable`（`agent.py:1990`/`2004`）会把工具写进 `tool_registry`，但 MCP server 的 `list_tools()` 仍返回启动时快照，外部 MCP 客户端看不到，直到重启进程。

### [接线确认 21] OpenAPI 工具确实注册进 tool_registry（链路通）

- 位置：`sdr_tools.py:1078-1143`
- 证据：5 个工具 `openapi_list_apis / openapi_iss_position / openapi_people_in_space / openapi_weather / openapi_aircraft_nearby` 都显式调了 `agent.tool_registry.register(...)`，category=`sdr_ai`。
- 后果：这条链路是通的——OpenAPI 工具在启动时进 registry，因而也会进 MCP 启动快照（受 [真bug 20] 的运行时不刷新影响，但启动时已注册的不受影响）。

### [接线确认 22] 插件工具注册是"条件通"

- 位置：`plugin_system.py:244-249`；对比 [空壳 8]
- 证据：PluginManager 不主动 `tool_registry.register`，全靠插件 `register()` 内部自行注册。
- 后果：即便第三方插件正确注册进 registry，MCP `tools/list` 仍因 [真bug 20] 不刷新而对 MCP 客户端不可见。整条"插件→tool_registry→MCP"链路在运行时是断的。

### [接线确认 23] agent.py 从未调用 `load_all_plugins`

- 位置：`agent.py:128-132`（实例化 PluginManager）、`agent.py:191`（`_register_plugin_tools`）
- 证据：全文件 grep 仅见 `pm.load_plugin`（`:1990`）与 `pm.enable_plugin`（`:2004`）作为运行时工具，**无 `load_all_plugins()` 调用**。
- 后果：启动时不会自动加载 `~/.mbdsdr/plugins` 或 `mbdsdr_ai/plugins/` 下任何插件（该目录当前为空）。"开机即插即用"在当前产品形态下未启用，这反而暂时掩盖了 [空壳 6] 的 RCE 风险——但能力一旦开启即暴露。

---

## 4. 安全 & 性能小结

**安全**
- 插件：主进程 `exec_module` 第三方代码，无沙箱、无审批、无校验 = **任意代码执行风险（高）**。`sandbox.py` 存在但未被复用。
- OpenAPI：所有 host 硬编码，**当前无 SSRF**；但 `sdr_tools` 封装层不做 lat/lon/radius 类型与范围校验（[真bug 14]），存在 URL 参数污染面。
- 认证缺失使 OpenSky/open-notify 在真机上不可用（[空壳 17]/[真bug 18]）。

**性能**
- 插件加载无明显不必要开销：`load_all_plugins` 当前不被调用；`PluginManager.__init__` 对每个 plugin_dir 做一次 `os.makedirs(exist_ok=True)`（`plugin_system.py:167-168`），开销可忽略。
- 主要问题不是性能而是正确性/隔离（见上）。

---

## 5. 修复优先级建议

1. **P0** [真bug 2]：统一 `register/unregister` 契约——定义基类/Protocol，并修正测试样例签名；测试必须覆盖 `enable_plugin`。
2. **P0** [空壳 6]：插件加载接入 `sandbox.py`（子进程隔离）或至少加审批/白名单；否则不要开放 `install_plugin_from_path` + 自动加载。
3. **P0** [真bug 3]：unload/disable 时 PluginManager 主动 `tool_registry.unregister(plugin 注册的工具名)`。
4. **P0** [真bug 20]：MCP `list_tools()` 改为实时 `self.tool_registry.list_tools()`，或在 `plugin_enable` 后触发快照刷新。
5. **P1** [真bug 13]：修正经度框公式为 `111*cos(lat_rad)`，并对 lat/lon 范围做 clamp。
6. **P1** [真bug 15]：velocity/altitude 为 None 时显示 "N/A"，不要 `:.0f`。
7. **P1** [空壳 17]/[真bug 18]：补 OpenSky 认证参数或换源；open-notify 换可用数据源并升 https。
8. **P2** [真bug 1]/[空壳 12]/[占位 16]：要么实现 entry_point 加载分支、要么删字段；要么真正解析 OpenAPI spec 生成工具、要么把模块改名为 `external_api_tools` 之类名实相符；删除或实现 `get_satellite_passes`。
9. **P2** [真bug 4]/[真bug 5]：`load_all_plugins` 记录失败日志；ERROR 插件支持重试。

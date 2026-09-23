# 第二轮深度审查：tool_registry.py / skill_registry.py

- 审查范围：`mbdsdr_ai/tool_registry.py`（595 行）、`mbdsdr_ai/skill_registry.py`（101 行）
- 关联核查：`mbdsdr_ai/agent.py`（工具注册调用方）、`mbdsdr_ai/sdr_tools.py`（SDR 工具集）、`mbdsdr_ai_mcp_server.py`
- 审查方式：只读，逐行阅读真实代码 + 全量 grep 统计注册名
- 日期：2026-09-24

---

## 一、注册工具统计

| 来源 | 文件 | 注册数 |
|---|---|---|
| 内置 meta 工具 | `tool_registry.py:481-574` (`register_builtin_tools`) | 6（`list_tools` 恒注册；`context_status`/`model_status`/`list_models`/`switch_model`/`tool_log` 需 `agent_ref`） |
| Agent 内联工具 | `agent.py`（38 个 `_register_*` 函数） | 117 |
| SDR 专用工具 | `sdr_tools.py`（`register_sdr_tools`） | 126 |
| **合计（去重后唯一名）** | | **250** |

- **精确同名重复注册：0**（全量 grep 后 `sort | uniq -d` 为空）。
- **真实实现：~240+**，handler 均委托到具体业务模块（dsp/decoders/orbit/hal/...），未发现"只 return note/固定字符串"的纯空壳。
- **条件可用（非空壳，但无硬件即返回提示）**：`instrument_list`/`instrument_query`/`radio_*`/`satdump_*`/`gimbal_*`/`sdr_transmit_cw` 等依赖外部硬件或可选库，handler 真实存在，仅在无设备时返回"未连接/未安装"文案。
- **有 bug：见下文 [真bug] 共 5 类。**

> 注：MCP 路径 `register_mcp_tools()`（tool_registry.py:104）在当前架构里用于对接外部硬件 MCP server（如 ESP32）。本仓库 250 个工具绝大多数是进程内直接注册的，不经过 MCP 通道。

---

## 二、发现清单

### [真bug] 1. `call()` 无任何权限模型——危险工具零确认直接执行
- 位置：`tool_registry.py:233-386`（`call` 全方法）
- 核实结论：**属实**。`call()` 流程为：短名解析(249) → 存在性(253) → 可用性(268) → 别名归一化(284) → required 校验(307) → 直接 `handler(args)`(331)。全程**没有**白名单/黑名单、没有人工确认、没有风险分级拦截。
- 实际可被无确认触发的高危动作：
  - `sdr_transmit_cw`（`sdr_tools.py:1852`）——真实 RF 发射，handler 调 `mgr.transmit_cw()`（`sdr_tools.py:4832`），仅 description 里写"需遵守法规"，代码不拦截。
  - `code_modify_file` / `code_run_tests` / `code_hot_reload` / `code_git_commit`（agent.py 2278/2312/2328/2342）——改代码、跑测试、热重载。
  - `evolution_commit`（agent.py:1581）、`plugin_install` / `plugin_load`（agent.py 2031/1981）。
  - `web_fetch_url` / `git_clone_repo`（agent.py 331/365）——任意 HTTP / 克隆。
  - `radio_ptt` / `radio_send_cw`（sdr_tools.py:1966/1980）——PTT 发射。
  - `instrument_query`（sdr_tools.py:1892）——向仪器发任意 SCPI 命令。
- 全仓唯一的 risk/confirm 机制在 `self_evolution.py:264-281`，且**只覆盖 evolution 提案**，不覆盖通用 `tool_registry.call()`。即"自进化提案要确认，但让模型直接调发射/改文件工具不用确认"。

### [真bug] 2. `has_tool()` 不走短名解析，与 `call()` 行为不一致
- 位置：`tool_registry.py:176-178`
- `has_tool()` 直接 `return name in self.tools and ...`，不调用 `_resolve_tool_name()`。而 `call()` 在 249-250 行会先做短名→全名映射。
- 后果：`has_tool("spectrum_analyze")` 返回 `False`，但 `call("spectrum_analyze")` 能成功映射到 `sdr_spectrum_analyze`。任何用 `has_tool()` 做前置判断的调用方会误判工具不可用。

### [真bug] 3. `call_from_model` 的 JSON 修复会破坏字符串内容
- 位置：`tool_registry.py:407-409`
- fallback 修复：`args_str.replace("'", '"').replace("True", "true").replace("False", "false")`。
- 这是**全局替换**：若模型给出的 arguments 字符串值本身含单引号（如 `{"path": "O'Brien/notes.md"}`）或含 `True`/`False` 字样，替换后 JSON 结构被改坏，反而把本可解析的输入搞成 `{"_raw": ...}`（411 行兜底）。

### [真bug] 4. `register_mcp_tools` 硬编码 `available=True`，离线工具仍对模型可见
- 位置：`tool_registry.py:129`
- `register(..., available=True)` 写死。MCP server 离线 / 设备未连时，这些工具仍出现在 `list_tools()` 和 `get_tool_definitions()` 里，模型据此发起调用，到 `call()` 内 MCP 调用才失败——典型"实验室绿、真机红"：注册/发现全绿，真实分发才暴露。应在连接成功后再 `set_available(True)`，断连时 `set_available(False)`。

### [真bug] 5. 同职责工具多份注册（非同名，故 register 不告警），模型选择混乱
- 位置：多处（见下）。这些工具名互不相同，因此第 7 项的"同名覆盖"检测抓不到，但功能重叠：
  - 卫星过境预测 ×4：`predict_satellite_passes`(agent.py:970) / `sdr_satellite_passes`(sdr_tools.py:917) / `satellite_predict_pass`(sdr_tools.py:1583) / `satellite_predict_all`(sdr_tools.py:1602)
  - 调制识别 ×2：`sdr_identify_modulation`(sdr_tools.py:992) / `signal_identify_modulation`(sdr_tools.py:1272)
  - NTP 对时 ×2：`time_sync_ntp`(agent.py:1109) / `time_ntp_sync`(sdr_tools.py:1463)
  - 云台指向 ×2：`gimbal_move`(agent.py:1048) / `gimbal_point`(sdr_tools.py:1715)
  - CW 解码 ×2：`cw_decode_audio`(agent.py:663) / `sdr_decode_cw`(sdr_tools.py:818)
  - FT8 解码 ×2：`ft8_decode_audio`(agent.py:438) / `sdr_decode_ft8`(sdr_tools.py:833)
- 后果：模型面对多个"预测卫星过境"工具，参数 schema 还不一致，容易选错参数（与别名归一化 284-305 叠加后更难排错）。

### [建议] 6. 参数预校验只查 required 存在性，不查类型/枚举/范围
- 位置：`tool_registry.py:307-326`
- 仅 `missing = [p for p in required_params if p not in args or args[p] is None]`。**不校验**：类型（schema 声明 integer 而模型传 string）、enum 枚举值、`minimum/maximum`（如 amplitude 0-1.0、频率范围）、array 元素类型。类型错误会漏到 handler 抛异常，被 361 行 `except` 收成模糊的"调用失败: TypeError"。这是"实验室绿、真机红"的第二来源：单测只传对类型就过，弱模型传错类型才崩。

### [建议] 7. `register()` 同名静默覆盖，无警告
- 位置：`tool_registry.py:97`（`self.tools[name] = {...}`）
- 本次 250 个名无精确重复，但机制上后注册的同名工具会**静默覆盖**前者，无日志无异常。建议覆盖时 `warnings.warn` 或抛错。

### [建议] 8. `_resolve_tool_name` 候选列表冗余死代码
- 位置：`tool_registry.py:190-222`
- 第 193 行已无条件 append `sdr_{tool_name}`；随后 196-197（spectrum_ 前缀）、204-205（set_ 前缀）、208-209（record_ 前缀）、217-218（identify_ 前缀）又各自 append **同一个** `sdr_{tool_name}`。同一候选被重复入列 2-5 次。不影响正确性（dict 查 O(1)），但映射表设计草率、误导维护者以为有区分逻辑。

### [建议] 9. `_PARAM_ALIASES` 每次 call 重建 + 原地改 args
- 位置：`tool_registry.py:284`（dict 定义在函数体内）、`305`（`args.pop(_alias)`）
- 别名表每次工具调用重建（微小开销）；`pop` 原地修改调用方传入的 dict（副作用）。建议提到模块级常量，并用新 dict 构造。

### [建议] 10. MCP 工具描述兜底无信息量
- 位置：`tool_registry.py:115`
- `description = tool.get("description", f"MCP 工具: {name}")`。MCP server 不提供 description 时，模型只看到"MCP 工具: xxx"，无法判断何时调用——工具发现形同虚设。

### [占位] 11. SkillRegistry：无版本管理、无卸载、无 enable/disable、frontmatter 解析简陋
- 位置：`skill_registry.py` 全文
- `Skill` dataclass（21-30）**无 `version` 字段**，无技能版本管理。
- 无 `unload()` / `enable()` / `disable()`；唯一"卸载"是 `_scan()` 清空整个 `_cache`（56 行）。
- frontmatter 解析（36-45）只支持扁平 `key: value`，**不支持嵌套 YAML / 列表 / 多行引号**；缺 `description` 时静默取正文首行（76 行）兜底，坏 frontmatter 不报错。
- 集成情况：技能系统**确实存在**，且已在 agent.py:391/409 注册为 `skill_list`/`skill_load` 两个工具，`skills/` 目录下有 3 个真实技能（find-interference、sat-track、sstv-decode）。但"可加载/卸载/版本管理"三项中只有"加载"成立。

### [性能] 12. SkillRegistry 每次 list/load 全量扫描目录，无缓存
- 位置：`skill_registry.py:86`（`list()` 内 `self._scan()`）、`91`（`load()` 内 `self._scan()`）
- 每次 `skill_list` 或 `skill_load` 都 `os.listdir` + 逐个 `open` 读 frontmatter。加载单个技能也要重扫整个目录。无 mtime 缓存/无增量。技能仅 3 个时影响可忽略，但与 docstring（第 6-8 行）宣称的"不堆上下文、按需加载"理念有偏差：省的是模型 token，但目录 IO 每次全量。

### [建议] 13. 文档/注释与实际数量严重漂移
- `sdr_tools.py:22` 注释"共 38 个 SDR 专用工具"，实际 **126** 个。
- `agent.py:172` 注释"注册 SDR 专用工具（38个）"，实际 126。
- `mbdsdr_ai_mcp_server.py:5/28/66` 多处宣称"195 个工具"，实际 **250**。
- 这些数字漂移会让审查/排障时误判工具是否注册完整。

---

## 三、"实验室绿、真机红"归因小结

| 环节 | 现象 | 根因 |
|---|---|---|
| 注册/发现 | list_tools 全绿 | `register_mcp_tools` 硬编码 `available=True`（#4） |
| 参数 | 单测传对类型就过 | 只校验 required 存在性，不校验类型/枚举/范围（#6） |
| 分发 | 短名在 workflow 里能跑 | `has_tool()` 不做短名解析（#2），但 `call()` 做——判断与执行不一致 |
| 选择 | 真实调用选错工具 | 同职责工具多份注册、schema 不一致（#5） |

---

## 四、修复优先级建议

1. **[高]** `call()` 增加危险工具拦截层（发射/写文件/执行代码/PTT 类工具要求确认或显式授权标志）——#1。
2. **[高]** `has_tool()` 复用 `_resolve_tool_name()`——#2。
3. **[中]** `register_mcp_tools` 改为连接成功后再置 available——#4。
4. **[中]** 参数校验补类型/enum/range（至少对 number 做 min/max）——#6。
5. **[中]** 合并/去重同职责工具，统一命名与 schema——#5。
6. **[低]** 修 `call_from_model` 字符串替换、清理 `_resolve_tool_name` 死候选、文档数字对齐——#3/#8/#13。

# 114 个「无条目」错误码的用户可见性判定

判定对象：`error_catalog` 共 129 个 code 中，扣除 15 条精选条目（`CURATED_CODES`）
后剩 114 个——它们要么已在 `troubleshooting.VISIBLE_BY_FAMILY` 里补了条目，
要么在 `error_remedy.NO_ENTRY_REASONS` 里登记「不出入口」。本文逐行给出
**触发条件 / 是否用户可见 / 可见路径 / 文件:行证据 / 处置**。
（2026-11-06 新增 6 个存量野码，第八节；目录总数不在此手写，实时值见
`len(error_catalog.ERRORS)`。）

三条判据（逐条都问，缺一即判不可见）：

1. 有没有任何路径把 `message` 或 `code` 原文送到用户可见面（状态栏、弹窗、
   面板文本、AI 回复、步骤卡、导出、tooltip）；日志与 MCP 客户端不算；
2. 触发条件是否用户可造成（点按钮 / 插拔设备 / 改设置 / 断网 / 磁盘满 /
   设备状态），还是只有开发者才会遇到的内部错误；
3. 是否在我们自己的 UI 里被展示过（含间接展示，如把 error 串拼进文案）。

## 〇、本轮复核修订（2026-11-05 第二轮）

复核对 `tle_fetch`（7 行：107–113）、`recording_library` / `recorder` / `playback`
（28 行：79–106）、`missing_iq`（1 行：76）共 **36 行**重查调用链。
**判定翻转 0 行**：三族仍全部不可见；其中「桌面零调用」的说法被纠正为
「面板走 X，本族为 Y 的底层，Y 的错误不上浮」。

| 族 | 行 | 最终判定 | 一句话机制 |
| --- | --- | --- | --- |
| TLE 细项（7） | 107–113 | 确不可见 | 过境面板确实拉 TLE，但失败只渲染固定通用提示（`new_spacetime_panel.py:640,655`）；`failure_label` 虽取自目录 meaning（`tle_fetch.py:88-90`），桌面零引用 |
| 录音 / 回放（28） | 79–106 | 确不可见 | 录音面板 `main_window.py:6119-6140` 直接 `open()` 直写、回放 `:7597-7607` 走 `baseband_io.load_iq`；`recording_library` 全仓无 Python 调用方，`recorder` 只被两个同样无调用方的模块引用，`playback` 只被 `experiments/` 引用 |
| `missing_iq`（1） | 76 | 确不可见 | chat 路摘要只看 `ToolResult.content`（`ai_steps.py:162,165,171`）；该工具 content 是 137 字手写说明、code 才放在 `data.error`；实测 summary 全文无 code、`step.error` 为空 |

- 修正行数：**36 行**（只改表里的「判定依据 / 处置」两列）；新增帮助条目 **0**。
- `mapping_gaps() == []`、`mapping_overlaps() == []`（复跑 `tests/test_error_remedy_links.py` 全绿）。

## 一、114 行的四类统计

| 分类 | 行数 | 说明 |
| --- | --- | --- |
| 可见-已补 | 52 | 39 个在 `e95945b` 已补，13 个在 `0e4b9ef6` 补（设备调谐 5 + AI 模型 7 + 工具结果 1） |
| 可见-待补 | 0 | 无 |
| 不可见-有理由 | 62 | 每行都写了机制证据与不补理由，无空白 |
| 无法判定 | 0 | 无 |
| **合计** | **114** | 129 − 15 精选条目 |

补完后的完整性：`error_remedy.mapping_gaps() == []`、`mapping_overlaps() == []`
（`tests/test_error_remedy_links.py::test_every_catalog_code_is_registered` 断言）。

## 二、反证三条（删条目 → 测试必须红）

做法：临时从 `desktop/troubleshooting.py` 的 `VISIBLE_BY_FAMILY` 删掉一个已在
映射里的 code，跑同一个用例，贴红原文，再恢复。三次均按此循环，恢复后
`git status -- desktop/troubleshooting.py` 为空。

### 反证 1：删 `unknown_mode`（解码结果族）

```text
        """每个 error_catalog code：有条目，或显式登记「不出入口 + 原因」。"""
>       assert error_remedy.mapping_gaps() == []
E       AssertionError: assert ['unknown_mode'] == []
tests\test_error_remedy_links.py:38: AssertionError
FAILED tests/test_error_remedy_links.py::test_every_catalog_code_is_registered - AssertionError: assert ['unknown_mode'
] == []
1 failed in 0.43s
```

### 反证 2：删 `sdr_set_gain_failed`（设备调谐族）

```text
        """每个 error_catalog code：有条目，或显式登记「不出入口 + 原因」。"""
>       assert error_remedy.mapping_gaps() == []
E       AssertionError: assert ['sdr_set_gain_failed'] == []
E         Left contains one more item: 'sdr_set_gain_failed'
E         +     'sdr_set_gain_failed',
tests\test_error_remedy_links.py:38: AssertionError
FAILED tests/test_error_remedy_links.py::test_every_catalog_code_is_registered - AssertionError: assert ['sdr_set_gain_
failed'] == []
  Left contains one more item: 'sdr_set_gain_failed'
  +     'sdr_set_gain_failed',
1 failed in 0.43s
```

### 反证 3：删 `llm_timeout`（AI 模型族）

```text
        """每个 error_catalog code：有条目，或显式登记「不出入口 + 原因」。"""
>       assert error_remedy.mapping_gaps() == []
E       AssertionError: assert ['llm_timeout'] == []
tests\test_error_remedy_links.py:38: AssertionError
FAILED tests/test_error_remedy_links.py::test_every_catalog_code_is_registered - AssertionError: assert ['llm_timeout']
 == []
1 failed in 0.44s
```

恢复后复跑：`13 passed, 2 warnings in 2.71s`（`tests/test_error_remedy_links.py` 整文件）。

## 三、114 行判定总表

可见路径缩写：

- **P1** AI 助手「执行步骤」卡渲染 `错误 {code}`（`desktop/ai_panel.py:701`；
  `code` 来自 `mbdsdr_ai/tool_registry.py:917` 的 `ToolResult.error`）
- **P2** 状态面板「解码结果」卡渲染 `错误码 {code}: {message}`
  （`desktop/status_panel.py:273`，标签映射 `:185-203`；入口 `:1159 is_decode_tool`）
- **P3** 状态栏渲染 `设备已断开：{message}`（`desktop/main_window.py:7304`，
  由 `:7307` 的 `_on_health_tick` 触发；`message` = `sdr_backend` 写入的
  `status.error = user_message(code)`）
- **P4** AI 对话区渲染 `[错误] {message}`（`desktop/ai_panel.py:1159`）
- **P5** 步骤卡摘要显示兜底 message（`desktop/ai_panel.py:696-699`，
  正文由 `mbdsdr_ai/tool_registry.py:967` 用 `user_message("missing_error_detail")` 填入）

| # | code | 触发条件 | 用户可见 | 可见路径 | 判定依据（文件:行） | 处置 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `tool_not_found` | 模型调用未注册的工具名（拼错 / 插件未装载） | 是 | P1 | `mbdsdr_ai/tool_registry.py:772-778` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 2 | `tool_unavailable` | 工具已注册但设备未连接 / 功能未启用时被调用 | 是 | P1 | `mbdsdr_ai/tool_registry.py:789-793` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 3 | `invalid_arguments_type` | 模型把参数发成字符串 / 数组，不是 JSON 对象 | 是 | P1 | `mbdsdr_ai/tool_registry.py:288-289,802-807` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 4 | `invalid_arguments` | 工具 handler 运行期校验判定参数内容不合法 | 是 | P1 | `mbdsdr_ai/tool_registry.py:278-400,875-883` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 5 | `missing_required_params` | 模型漏传必填参数 | 是 | P1 | `mbdsdr_ai/tool_registry.py:854-859` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 6 | `param_type_mismatch` | 参数类型与声明不符 | 是 | P1 | `mbdsdr_ai/tool_registry.py:306-316` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 7 | `param_out_of_range` | 参数数值超出声明范围 | 是 | P1 | `mbdsdr_ai/tool_registry.py:324-335` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 8 | `param_not_in_enum` | 枚举值不在允许集合内 | 是 | P1 | `mbdsdr_ai/tool_registry.py:394-396` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 9 | `invalid_json_arguments` | 模型输出被截断 / 生成非法 JSON | 是 | P1 | `mbdsdr_ai/tool_registry.py:1031-1035` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 10 | `invalid_rs_shape` | AI 传入的 RS 矩阵参数形状不符 | 是 | P1 | `mbdsdr_ai/tool_registry.py:2138-2140` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 11 | `empty_taps` | AI 让滤波器使用空 taps 列表 | 是 | P1 | `mbdsdr_ai/tool_registry.py:3154-3155` → `desktop/ai_panel.py:701` | 已补（工具参数族，`e95945b`） |
| 12 | `handler_error` | handler 以「错误:」开头返回，或失败但没给码（registry 兜底） | 是 | P1 | `mbdsdr_ai/tool_registry.py:929,955,960` → `desktop/ai_panel.py:701` | 已补（工具执行族，`e95945b`） |
| 13 | `exception` | handler 抛异常，`exception_code()` 生成 `exception:<Type>` | 是 | P1 | `mbdsdr_ai/tool_registry.py:945` → `desktop/ai_panel.py:701` | 已补（工具执行族，`e95945b`） |
| 14 | `empty_result_without_reason` | 工具成功但无内容且未说明原因 | 是 | P1 | `mbdsdr_ai/tool_registry.py:973-977` → `desktop/ai_panel.py:701` | 已补（工具执行族，`e95945b`） |
| 15 | `compose_failed` | AI 调用的报文组装 / 合成工具失败 | 是 | P1 | `mbdsdr_ai/sdr_tools.py:7152` → `desktop/ai_panel.py:701` | 已补（工具执行族，`e95945b`） |
| 16 | `gimbal_stop_failed` | 云台停止指令未被硬件执行 | 是 | P1 | `mbdsdr_ai/sdr_tools.py:7835` → `desktop/ai_panel.py:701` | 已补（工具执行族，`e95945b`） |
| 17 | `no_rssi` | 扫描一轮没有采到任何 RSSI 样本 | 是 | P1 | `mbdsdr_ai/sdr_tools.py:7842-7847` → `desktop/ai_panel.py:701` | 已补（工具执行族，`e95945b`） |
| 18 | `offline_or_api_error` | 联网类工具离线或上游接口无数据 | 是 | P1 | `mbdsdr_ai/sdr_tools.py:7618,7644,7676,7711` → `desktop/ai_panel.py:701` | 已补（工具执行族，`e95945b`） |
| 19 | `recording_not_found` | AI 指定不存在的记录 / 模板名 | 是 | P1 | `mbdsdr_ai/agent.py:2866` → `desktop/ai_panel.py:701` | 已补（工具执行族，`e95945b`） |
| 20 | `subagent_not_found` | AI 指定不存在或已结束的子代理 | 是 | P1 | `mbdsdr_ai/agent.py:2688-2690` → `desktop/ai_panel.py:701` | 已补（工具执行族，`e95945b`） |
| 21 | `agent_busy` | 上一轮未结束时用户再发一条指令 | 是 | P4 | `mbdsdr_ai/agent.py:126,3782-3794` → `desktop/ai_panel.py:1159` | 已补（AI 会话族，`e95945b`） |
| 22 | `cancelled` | 用户点停止 / 取消本轮对话 | 是 | P4 | `mbdsdr_ai/agent.py:127,3903-3905,3956-3958,4131-4134` → `desktop/ai_panel.py:1159` | 已补（AI 会话族，`e95945b`） |
| 23 | `device_unavailable` | 未连接设备时调用需要设备的工具 | 是 | P1 | `mbdsdr_ai/sdr_tools.py:2960` → `desktop/ai_panel.py:701` | 已补（设备能力族，`e95945b`） |
| 24 | `ptt_not_on` | 未打开 PTT 就让 AI 调发射工具 | 是 | P1 | `mbdsdr_ai/sdr_tools.py:6744` → `desktop/ai_panel.py:701` | 已补（设备能力族，`e95945b`） |
| 25 | `capability_unknown` | 设备能力未声明，无法判断是否支持该能力（如发射） | 是 | P1 | `mbdsdr_ai/sdr_tools.py:6326-6327` → `desktop/ai_panel.py:701` | 已补（设备能力族，`e95945b`） |
| 26 | `NEED_HW` | 无硬件 / 离线环境下调用 VNA 等需硬件工具 | 是 | P1 | `mbdsdr_ai/vna_tools.py:175,179` → `desktop/ai_panel.py:701` | 已补（设备能力族，`e95945b`） |
| 27 | `invalid_vcdu` | FY-4 / GOES 帧解析校验失败（用户可给坏文件 / 弱信号） | 是 | P1、P2 | `mbdsdr_ai/fengyun_sat.py:834,902`、`goes_lrit.py:915` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` | 已补（解码工具族，`e95945b`） |
| 28 | `decode_failed` | RTL433 等无线电解码失败（信号质量不足） | 是 | P1、P2 | `mbdsdr_ai/rtl433_decoder.py:803` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` | 已补（解码工具族，`e95945b`） |
| 29 | `unknown_device` | AI 指定未知设备名 | 是 | P1、P2 | `mbdsdr_ai/rtl433_decoder.py:798` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` | 已补（解码工具族，`e95945b`） |
| 30 | `doppler_failed` | 卫星解码时多普勒补偿计算失败 | 是 | P1、P2 | `mbdsdr_ai/satnogs_adapter.py:373` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` | 已补（解码工具族，`e95945b`） |
| 31 | `ogn_decode_failed` | OGN 报文解码失败 | 是 | P1、P2 | `mbdsdr_ai/sdrangel_plugins.py:411` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` | 已补（解码工具族，`e95945b`） |
| 32 | `schedule_failed` | 云图排程失败 | 是 | P1、P2 | `mbdsdr_ai/r2cloud_adapter.py:380` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` | 已补（解码工具族，`e95945b`） |
| 33 | `meta_failed` | 云图元数据获取失败 | 是 | P1、P2 | `mbdsdr_ai/r2cloud_adapter.py:388` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` | 已补（解码工具族，`e95945b`） |
| 34 | `bad_hex` | RDS 工具收到非法十六进制串 | 是 | P1、P2 | `mbdsdr_ai/rds_lite.py:558` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` | 已补（解码工具族，`e95945b`） |
| 35 | `unknown_mode` | 解码卡收到能力表未登记的数字模式 | 是 | P2 | `mbdsdr_ai/decode_router.py:811-816` → `desktop/status_panel.py:185,273` | 已补（解码结果族，`e95945b`） |
| 36 | `missing_sample` | 解码卡输入的捕获文件不存在或为空 | 是 | P2 | `mbdsdr_ai/decode_router.py:827-831` → `desktop/status_panel.py:185,273` | 已补（解码结果族，`e95945b`） |
| 37 | `unsupported_format` | 解码卡输入扩展名与模式要求不符 | 是 | P2 | `mbdsdr_ai/decode_router.py:835-841` → `desktop/status_panel.py:185,273` | 已补（解码结果族，`e95945b`） |
| 38 | `decode_error` | 解码器运行期异常（弱信号 / 坏样本） | 是 | P2 | `mbdsdr_ai/decode_router.py:408,913-921` → `desktop/status_panel.py:185,273` | 已补（解码结果族，`e95945b`） |
| 39 | `not_implemented` | 模式能力存在但通用入口未接线，用户仍可点 | 是 | P2 | `mbdsdr_ai/decode_router.py:382-383` → `desktop/status_panel.py:185,273` | 已补（解码结果族，`e95945b`） |
| 40 | `sdr_set_frequency_failed` | 用户在「控制」面板改频率，设备拒绝 / 底层失败 | 是 | P3 | `mbdsdr_ai/sdr_backend.py:283,289` → `desktop/main_window.py:7304` | 已补（设备调谐族，`0e4b9ef6`） |
| 41 | `sdr_set_sample_rate_failed` | 用户改采样率，设备不支持或底层失败 | 是 | P3 | `mbdsdr_ai/sdr_backend.py:368,374` → `desktop/main_window.py:7304` | 已补（设备调谐族，`0e4b9ef6`） |
| 42 | `sdr_set_gain_failed` | 用户改增益，设备拒绝或底层失败 | 是 | P3 | `mbdsdr_ai/sdr_backend.py:418-423` → `desktop/main_window.py:7304` | 已补（设备调谐族，`0e4b9ef6`） |
| 43 | `sdr_set_bandwidth_failed` | 用户改带宽，设备拒绝或底层失败 | 是 | P3 | `mbdsdr_ai/sdr_backend.py:471-477` → `desktop/main_window.py:7304` | 已补（设备调谐族，`0e4b9ef6`） |
| 44 | `doppler_compensation_failed` | 接收链多普勒补偿失败（无预测 / 计算异常），该块音频跳过 | 是 | P3 | `mbdsdr_ai/sdr_backend.py:832,872` → `desktop/main_window.py:7304` | 已补（设备调谐族，`0e4b9ef6`） |
| 45 | `llm_offline` | 用户开 `MBDSDR_OFFLINE` 或离线时点 AI 发送 | 是 | P4 | `mbdsdr_ai/model_manager.py:268,275` → `desktop/ai_panel.py:1159` | 已补（AI 模型族，`0e4b9ef6`） |
| 46 | `llm_rate_limited` | 模型接口返回 429（用户连续发指令 / 额度耗尽） | 是 | P4 | `mbdsdr_ai/model_manager.py:31-32` → `desktop/ai_panel.py:1159` | 已补（AI 模型族，`0e4b9ef6`） |
| 47 | `llm_http_4xx` | API Key 错 / 模型名不存在，接口返回 4xx | 是 | P4 | `mbdsdr_ai/model_manager.py:35-36` → `desktop/ai_panel.py:1159` | 已补（AI 模型族，`0e4b9ef6`） |
| 48 | `llm_http_5xx` | 模型服务端 5xx | 是 | P4 | `mbdsdr_ai/model_manager.py:33-34` → `desktop/ai_panel.py:1159` | 已补（AI 模型族，`0e4b9ef6`） |
| 49 | `llm_timeout` | 用户设置超时过小或网络慢，模型请求超时 | 是 | P4 | `mbdsdr_ai/model_manager.py:40` → `desktop/ai_panel.py:1159` | 已补（AI 模型族，`0e4b9ef6`） |
| 50 | `llm_unreachable` | 断网 / 代理错误 / 域名不可达 | 是 | P4 | `mbdsdr_ai/model_manager.py:42` → `desktop/ai_panel.py:1159` | 已补（AI 模型族，`0e4b9ef6`） |
| 51 | `llm_unexpected` | 模型客户端内部异常（用户可触发，如坏响应） | 是 | P4 | `mbdsdr_ai/model_manager.py:44` → `desktop/ai_panel.py:1159` | 已补（AI 模型族，`0e4b9ef6`） |
| 52 | `missing_error_detail` | 工具失败且 handler 没给正文，registry 兜底填 message | 是 | P5 | `mbdsdr_ai/tool_registry.py:967` → `desktop/ai_panel.py:696-699` | 已补（工具结果族，`0e4b9ef6`） |
| 53 | `workflow_executor_missing` | 工作流引擎未注入执行器（构造顺序错误，开发者态） | 否 | — | `mbdsdr_ai/workflow_engine.py:298-299`；`mbdsdr_ai/agent.py:453` 总会 `set_tool_executor`，用户路径造不出 | 不补：仅开发者构造错误可达，桌面无渲染路径 |
| 54 | `unknown_tool` | 旧 MCP 服务端收到未注册工具名（外部客户端发起） | 否 | — | `mbdsdr_ai/core/mcp_server.py:2072`；桌面 AI 走 `tool_not_found`（`tool_registry.py`），不读该 envelope | 不补：只有外部 MCP 客户端能看到，我方 UI 不渲染 |
| 55 | `bad_arguments` | 仓内 `mcp_client` 库 API 的参数校验失败 | 否 | — | `mbdsdr_ai/mcp_client.py:1061,1066`；桌面不 import 该模块（`git grep -- desktop` 仅 `error_remedy.py`） | 不补：桌面零调用，属库 API 返回值 |
| 56 | `bad_command` | 浏览器 / HTTP 客户端向 Web 服务发非法指令 | 否 | — | `mbdsdr_ai/web/server.py:217,433`；桌面只 `settings_panel.py:73` 启停服务，不读请求回包 | 不补：错误回给 HTTP 客户端，不进桌面 UI |
| 57 | `bad_config` | MCP 客户端配置缺 `type` / 配置文件损坏 | 否 | — | `mbdsdr_ai/mcp_client.py:748,910,939`；桌面零 import | 不补：属客户端库配置错误，桌面不走该路径 |
| 58 | `bad_json` | MCP 客户端配置文件 JSON 解析失败 | 否 | — | `mbdsdr_ai/mcp_client.py:934`；桌面零 import | 不补：同上，库 API 级错误 |
| 59 | `config_not_found` | MCP 客户端指定配置文件不存在 | 否 | — | `mbdsdr_ai/mcp_client.py:925`；桌面零 import | 不补：同上，库 API 级错误 |
| 60 | `unknown_server` | 客户端调用未登记的 MCP server 名 | 否 | — | `mbdsdr_ai/mcp_client.py:1037`；桌面零 import | 不补：外部客户端调用面 |
| 61 | `unknown_command` | Web 服务收到未知命令字 | 否 | — | `mbdsdr_ai/web/server.py:463`；桌面不读请求回包 | 不补：HTTP 客户端可见，桌面不可见 |
| 62 | `unsupported` | Web 服务收到不支持的操作 | 否 | — | `mbdsdr_ai/web/server.py:448,454,460` | 不补：同上 |
| 63 | `identity_mismatch` | 站控服务端校验站点身份不匹配 | 否 | — | `mbdsdr_ai/core/controller.py:1075`；桌面只 import 其 `VALID_MODES` 常量（`config_schema.py:66`） | 不补：桌面无调用该 dict 返回值的路径 |
| 64 | `station_untrusted` | 站点不在受控端可信列表 | 否 | — | `mbdsdr_ai/core/controller.py:1043,1217`；桌面零调用 | 不补：服务端协议细项，不进我方 UI |
| 65 | `no_pass` | 服务端当前时间窗无可用过境 | 否 | — | `mbdsdr_ai/core/controller.py:1122`；桌面零调用 | 不补：同上；桌面卫星面板有独立通用提示 |
| 66 | `no_downlink_known` | 服务端查不到该卫星下行频点 | 否 | — | `mbdsdr_ai/core/controller.py:1237`；桌面零调用 | 不补：服务端返回值，不进我方 UI |
| 67 | `sdr_error` | `core.errors.SdrError` 的默认码；异常路径被改写成 `exception:<Type>` | 否 | — | `mbdsdr_ai/core/errors.py:36`；`mbdsdr_ai/tool_registry.py:945` 用 `exception_code(e)` 覆盖 | 不补：兜底类默认码不会作为 UI 文案出现 |
| 68 | `invalid_argument` | `core.errors` 参数校验异常默认码 | 否 | — | `mbdsdr_ai/core/errors.py:158`；同样被 `exception_code` 覆盖 | 不补：同上，异常码统一改写 |
| 69 | `export_path_out_of_root` | 导出路径越界（服务端 / MCP envelope） | 否 | — | `mbdsdr_ai/core/errors.py:173`；桌面零 import | 不补：MCP 服务端安全拒绝码，外部客户端可见 |
| 70 | `decoder_not_available` | 解码器不可用（服务端 envelope） | 否 | — | `mbdsdr_ai/core/errors.py:179`；桌面零 import | 不补：同上 |
| 71 | `not_available` | 后端 / 模块缺失，`core.controller` 返回 dict | 否 | — | `mbdsdr_ai/core/controller.py:737`；桌面零调用 | 不补：服务端字典，桌面不读 |
| 72 | `spill_write_failed` | MCP 大字段落盘失败 | 否 | — | `mbdsdr_ai/core/mcp_server.py:1586` | 不补：MCP 服务端 envelope，桌面不渲染 |
| 73 | `denied_by_policy` | 工具策略拒绝执行（外部 MCP 客户端调用） | 否 | — | `mbdsdr_ai/core/mcp_server.py:1813-1814` | 不补：桌面写操作走自己的确认门，文案自写 |
| 74 | `manual_gate_required` | MCP 服务端缺外部确认 token | 否 | — | `mbdsdr_ai/core/mcp_server.py:1838-1840,1867` | 不补：桌面确认门文案独立，不显示该码 |
| 75 | `internal_error` | MCP 服务端内部异常 envelope | 否 | — | `mbdsdr_ai/core/mcp_server.py:1856,2096` | 不补：服务端内部错误，桌面不读 |
| 76 | `missing_iq` | 解调工具调用未带 `iq_base64` | 否 | — | `mbdsdr_ai/sdr_tools.py:3636-3643`（content=137 字手写说明；`data.error="missing_iq"`）；`desktop/ai_steps.py:162,165,171` chat 路只取 content；`:103-106` 摘要正则只认 `"error"`/`"message"` 键、值截 `summary_chars=200`（`desktop/tokens.py:351`）；`:177-183` 仅工作流/子代理展开；`desktop/ai_panel.py:606-616` worker 路把软件工具标 `skipped` | 不补：实测 `steps_from_chat` 的 summary 是 137 字手写说明全文、`step.error` 为空，不含 code；只有把整个 dict 序列化成 JSON 才会命中 `missing_iq`，而该路只收硬件工具结果 |
| 77 | `unknown_device_type` | MCP 服务端收到未知设备类型 | 否 | — | `mbdsdr_ai/core/mcp_server.py:236-239` | 不补：服务端 envelope，桌面不渲染 |
| 78 | `empty_input` | MCP 解码入口收到空输入 | 否 | — | `mbdsdr_ai/core/mcp_server.py:187,263` | 不补：服务端校验返回，桌面不读 |
| 79 | `file_missing` | 录音库按条目取文件时文件已被移走 | 否 | — | `mbdsdr_ai/recording_library.py:764`；桌面录音走 `desktop/main_window.py:6119-6140`（直接 `open(path,"wb")`）、回放走 `:7597-7607`（`mbdsdr_ai.baseband_io.load_iq`），均不经 `recording_library`；该模块全仓 Python 无调用方 | 不补：面板不走本模块，其 code/message 不上浮到状态栏或弹窗 |
| 80 | `bad_field` | 录音库更新 / 查询字段名不在白名单 | 否 | — | `mbdsdr_ai/recording_library.py:815`；桌面录音走 `desktop/main_window.py:6119-6140`（直接 `open(path,"wb")`）、回放走 `:7597-7607`（`mbdsdr_ai.baseband_io.load_iq`），均不经 `recording_library`；该模块全仓 Python 无调用方 | 不补：面板不走本模块，其 code/message 不上浮到状态栏或弹窗 |
| 81 | `bad_param` | 录音库参数越界 | 否 | — | `mbdsdr_ai/recording_library.py:821`；桌面录音走 `desktop/main_window.py:6119-6140`（直接 `open(path,"wb")`）、回放走 `:7597-7607`（`mbdsdr_ai.baseband_io.load_iq`），均不经 `recording_library`；该模块全仓 Python 无调用方 | 不补：面板不走本模块，其 code/message 不上浮到状态栏或弹窗 |
| 82 | `entry_not_found` | 录音库条目 ID 不存在 | 否 | — | `mbdsdr_ai/recording_library.py:833`；桌面录音走 `desktop/main_window.py:6119-6140`（直接 `open(path,"wb")`）、回放走 `:7597-7607`（`mbdsdr_ai.baseband_io.load_iq`），均不经 `recording_library`；该模块全仓 Python 无调用方 | 不补：面板不走本模块，其 code/message 不上浮到状态栏或弹窗 |
| 83 | `dest_missing` | 录音库移动 / 复制目标不存在 | 否 | — | `mbdsdr_ai/recording_library.py:1400`；桌面录音走 `desktop/main_window.py:6119-6140`（直接 `open(path,"wb")`）、回放走 `:7597-7607`（`mbdsdr_ai.baseband_io.load_iq`），均不经 `recording_library`；该模块全仓 Python 无调用方 | 不补：面板不走本模块，其 code/message 不上浮到状态栏或弹窗 |
| 84 | `not_found` | 录音库索引文件不存在（空库） | 否 | — | `mbdsdr_ai/recording_library.py:421`；桌面录音走 `desktop/main_window.py:6119-6140`（直接 `open(path,"wb")`）、回放走 `:7597-7607`（`mbdsdr_ai.baseband_io.load_iq`），均不经 `recording_library`；该模块全仓 Python 无调用方 | 不补：面板不走本模块，其 code/message 不上浮到状态栏或弹窗 |
| 85 | `library_not_found` | 指到的新目录下没有索引 | 否 | — | `mbdsdr_ai/recording_library.py:1410`；桌面录音走 `desktop/main_window.py:6119-6140`（直接 `open(path,"wb")`）、回放走 `:7597-7607`（`mbdsdr_ai.baseband_io.load_iq`），均不经 `recording_library`；该模块全仓 Python 无调用方 | 不补：面板不走本模块，其 code/message 不上浮到状态栏或弹窗 |
| 86 | `library_busy` | 另一进程占用录音库 | 否 | — | `mbdsdr_ai/recording_library.py:76`；桌面录音走 `desktop/main_window.py:6119-6140`（直接 `open(path,"wb")`）、回放走 `:7597-7607`（`mbdsdr_ai.baseband_io.load_iq`），均不经 `recording_library`；该模块全仓 Python 无调用方 | 不补：面板不走本模块，其 code/message 不上浮到状态栏或弹窗 |
| 87 | `unsupported_kind` | 录音库记录类型不支持 | 否 | — | `mbdsdr_ai/recording_library.py:1637`；桌面录音走 `desktop/main_window.py:6119-6140`（直接 `open(path,"wb")`）、回放走 `:7597-7607`（`mbdsdr_ai.baseband_io.load_iq`），均不经 `recording_library`；该模块全仓 Python 无调用方 | 不补：面板不走本模块，其 code/message 不上浮到状态栏或弹窗 |
| 88 | `io` | 录制文件读写异常（`recorder` 按异常类型归类） | 否 | — | `mbdsdr_ai/recorder.py:60`；`recorder` 只被 `recorder_enhanced.py` / `recording_library.py` 引用，二者桌面零调用；桌面录制在 `main_window.py:6136` 直接 `open(path,"wb")`，失败经 `:6138` → `_report_record_abort:6142-6153` 显示 OSError 原文 | 不补：桌面录制不经 `recorder`，用户看到的是 OSError 原文而非目录 code |
| 89 | `io_error` | 录音库操作 IO 失败 | 否 | — | `mbdsdr_ai/recorder.py:295`；`recorder` 只被 `recorder_enhanced.py` / `recording_library.py` 引用，二者桌面零调用；桌面录制在 `main_window.py:6136` 直接 `open(path,"wb")`，失败经 `:6138` → `_report_record_abort:6142-6153` 显示 OSError 原文 | 不补：桌面录制不经 `recorder`，用户看到的是 OSError 原文而非目录 code |
| 90 | `wav_error` | WAV 写入失败 | 否 | — | `mbdsdr_ai/recorder.py:433`；`recorder` 只被 `recorder_enhanced.py` / `recording_library.py` 引用，二者桌面零调用；桌面录制在 `main_window.py:6136` 直接 `open(path,"wb")`，失败经 `:6138` → `_report_record_abort:6142-6153` 显示 OSError 原文 | 不补：桌面录制不经 `recorder`，用户看到的是 OSError 原文而非目录 code |
| 91 | `missing` | 回放打开时数据文件不存在 | 否 | — | `mbdsdr_ai/playback.py:164`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 92 | `corrupt` | 回放 SigMF 元数据损坏 | 否 | — | `mbdsdr_ai/playback.py:180`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 93 | `truncated` | 回放记录被截断 | 否 | — | `mbdsdr_ai/playback.py:194`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 94 | `empty` | 回放数据文件 0 字节 | 否 | — | `mbdsdr_ai/playback.py:191`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 95 | `datatype` | SigMF 数据类型不支持 | 否 | — | `mbdsdr_ai/playback.py:74`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 96 | `deleted` | 回放文件读取过程中被删除 | 否 | — | `mbdsdr_ai/playback.py:262`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 97 | `eof` | 回放读到文件末尾（未循环） | 否 | — | `mbdsdr_ai/playback.py:301`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 98 | `open_failed` | 回放文件打开失败 | 否 | — | `mbdsdr_ai/playback.py:497`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 99 | `no_data` | 回放源无更多数据 | 否 | — | `mbdsdr_ai/playback.py:513`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 100 | `rate_unknown` | 录制文件未声明采样率，拒绝猜测 | 否 | — | `mbdsdr_ai/playback.py:540`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 101 | `rate_mismatch` | 回放源固定采样率与请求值冲突 | 否 | — | `mbdsdr_ai/playback.py:544-546`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 102 | `playback` | 回放打开失败（`playback:<底层码>`） | 否 | — | `mbdsdr_ai/playback.py:388`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 103 | `playback_failed` | 回放运行期失败 | 否 | — | `mbdsdr_ai/playback.py:384`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 104 | `bad_parameter` | 发射 / 回放参数不合法 | 否 | — | `mbdsdr_ai/playback.py:396`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 105 | `tx_source_deleted` | 发射源文件在发射过程中被删除（playback 发射面） | 否 | — | `mbdsdr_ai/playback.py:442`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 106 | `tx_source_failed` | 发射源在发射过程中失效（playback 发射面） | 否 | — | `mbdsdr_ai/playback.py:443`；`playback` 只被 `experiments/` 引用；桌面回放 `main_window.py:7597-7607` 用 `mbdsdr_ai.baseband_io.load_iq`，失败显示「文件不存在: …」/异常文本 | 不补：桌面回放走 `baseband_io`，`playback` 的 code 不上浮 |
| 107 | `rate_limited` | TLE 源 429 限流 | 否 | — | `mbdsdr_ai/tle_fetch.py:85` 分类进 `failure_kind`；桌面过境面板 `desktop/new_spacetime_panel.py:648` 调 `TLEManager.fetch` → `mbdsdr_ai/new_spacetime_tle.py:166-186`（失败回退旧缓存或 None）→ `new_spacetime_panel.py:640,655` 只显示固定通用提示；`desktop/main_window.py:7917` 状态栏通用文案；`tle_fetch.py:88-90,152` 的 `failure_label`（目录 meaning）桌面零引用 | 不补：code / message / meaning 均不进桌面文案，用户只看到与目录无关的通用提示 |
| 108 | `http_4xx` | TLE 源 4xx 拒绝 | 否 | — | `mbdsdr_ai/tle_fetch.py:85`；桌面过境面板 `desktop/new_spacetime_panel.py:648` 调 `TLEManager.fetch` → `mbdsdr_ai/new_spacetime_tle.py:166-186`（失败回退旧缓存或 None）→ `new_spacetime_panel.py:640,655` 只显示固定通用提示；`desktop/main_window.py:7917` 状态栏通用文案；`tle_fetch.py:88-90,152` 的 `failure_label`（目录 meaning）桌面零引用 | 不补：code / message / meaning 均不进桌面文案，用户只看到与目录无关的通用提示 |
| 109 | `http_5xx` | TLE 源 5xx | 否 | — | `mbdsdr_ai/tle_fetch.py:85`；桌面过境面板 `desktop/new_spacetime_panel.py:648` 调 `TLEManager.fetch` → `mbdsdr_ai/new_spacetime_tle.py:166-186`（失败回退旧缓存或 None）→ `new_spacetime_panel.py:640,655` 只显示固定通用提示；`desktop/main_window.py:7917` 状态栏通用文案；`tle_fetch.py:88-90,152` 的 `failure_label`（目录 meaning）桌面零引用 | 不补：code / message / meaning 均不进桌面文案，用户只看到与目录无关的通用提示 |
| 110 | `network_other` | TLE 拉取时的其它网络错误 | 否 | — | `mbdsdr_ai/tle_fetch.py:85`；桌面过境面板 `desktop/new_spacetime_panel.py:648` 调 `TLEManager.fetch` → `mbdsdr_ai/new_spacetime_tle.py:166-186`（失败回退旧缓存或 None）→ `new_spacetime_panel.py:640,655` 只显示固定通用提示；`desktop/main_window.py:7917` 状态栏通用文案；`tle_fetch.py:88-90,152` 的 `failure_label`（目录 meaning）桌面零引用 | 不补：code / message / meaning 均不进桌面文案，用户只看到与目录无关的通用提示 |
| 111 | `parse_failure` | TLE 响应不含合法两行根数 | 否 | — | `mbdsdr_ai/tle_fetch.py:145`；桌面过境面板 `desktop/new_spacetime_panel.py:648` 调 `TLEManager.fetch` → `mbdsdr_ai/new_spacetime_tle.py:166-186`（失败回退旧缓存或 None）→ `new_spacetime_panel.py:640,655` 只显示固定通用提示；`desktop/main_window.py:7917` 状态栏通用文案；`tle_fetch.py:88-90,152` 的 `failure_label`（目录 meaning）桌面零引用 | 不补：code / message / meaning 均不进桌面文案，用户只看到与目录无关的通用提示 |
| 112 | `empty_response` | TLE 源返回空响应 | 否 | — | `mbdsdr_ai/tle_fetch.py:86`；桌面过境面板 `desktop/new_spacetime_panel.py:648` 调 `TLEManager.fetch` → `mbdsdr_ai/new_spacetime_tle.py:166-186`（失败回退旧缓存或 None）→ `new_spacetime_panel.py:640,655` 只显示固定通用提示；`desktop/main_window.py:7917` 状态栏通用文案；`tle_fetch.py:88-90,152` 的 `failure_label`（目录 meaning）桌面零引用 | 不补：code / message / meaning 均不进桌面文案，用户只看到与目录无关的通用提示 |
| 113 | `tle_unknown` | TLE 刷新失败且调用方没给状态码 / 异常 | 否 | — | `mbdsdr_ai/tle_fetch.py:86`；桌面过境面板 `desktop/new_spacetime_panel.py:648` 调 `TLEManager.fetch` → `mbdsdr_ai/new_spacetime_tle.py:166-186`（失败回退旧缓存或 None）→ `new_spacetime_panel.py:640,655` 只显示固定通用提示；`desktop/main_window.py:7917` 状态栏通用文案；`tle_fetch.py:88-90,152` 的 `failure_label`（目录 meaning）桌面零引用 | 不补：code / message / meaning 均不进桌面文案，用户只看到与目录无关的通用提示 |
| 114 | `unexpected_error` | `error_handler` 结构化信封的默认码（MCP / 启动序列） | 否 | — | `mbdsdr_ai/error_handler.py:240-244`；桌面只 import 具体异常类（`mbdsdr_ai/sdr_backend.py:1860-1865`），不读信封 | 不补：只用于日志与开发排查，用户面不出现 |

## 四、判定口径与复核命令

- 通道取证只看**桌面 UI 真实渲染点**，不看库 API 返回值：
  `desktop/ai_panel.py:701`、`desktop/status_panel.py:273`、
  `desktop/main_window.py:7304`、`desktop/ai_panel.py:1159`、`desktop/ai_panel.py:696-699`。
- 「不可见」的机械证据是 `git grep -n <模块> -- desktop` 为 0：
  `recording_library` / `recorder` / `playback` / `tle_fetch` / `error_handler`（信封）
  在 `desktop/` 均无业务调用方。
- 复跑：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest tests/test_error_remedy_links.py -q
python -c "import sys; sys.path.insert(0,'.'); sys.path.insert(0,'desktop'); import error_remedy; print(error_remedy.mapping_gaps(), error_remedy.mapping_overlaps())"
```

- 边界：本次只改错误映射表（`desktop/troubleshooting.py`）、帮助文案与测试，
  UI 布局与像素零改动。

## 五、三族复核明细（本轮新增）

### 5.1 TLE 细项（7 码：107–113）

- 真实调用链：`desktop/new_spacetime_panel.py:627-655`（用户点「刷新过境」）→
  `:648 self._tle_mgr.fetch(catnr, name)` → `mbdsdr_ai/new_spacetime_tle.py:166-186`
  （`TleFetcher.fetch_record`，失败回退磁盘缓存，无缓存则 `return None`）→
  `new_spacetime_panel.py:640` 观测站未配置时**不发请求**，提示「未配置观测站位置…」→
  `:655` 全部失败时提示「未获取 TLE：请先更新卫星星历（离线或无网络时无法拉取）」。
- 另一条：`desktop/main_window.py:7905-7917` 内置 TLE 在线刷新失败 → 状态栏
  「TLE 在线更新失败，使用内置 TLE」。
- `tle_fetch.py:83-90` 的 `FAILURE_LABELS` 确实取自 `ERRORS[kind].meaning`，
  `:152` 写进 `classify_failure()["label"]`；但该字段只进 `fetch_record` 记录与
  `source_status`，`git grep -- desktop` 对 `FAILURE_LABELS` / `failure_label` /
  `source_status` / `fetch_record` **均为 0**。
- 判定：用户看到的是**与目录无关的固定提示**，7 个 code 及其 message/meaning 都不出现在
  我方 UI → 仍然不补。

### 5.2 录音 / 回放（28 码：79–106）

- 录音面板：`desktop/main_window.py:6226 _start_recording` →
  `:6270 settings.ensure_record_dir()`（失败只会被 `error_remedy.py:164-169` 归为
  `disk_full` / `not_writable`，两者都有精选条目，不在本 114）→
  `:6119-6140 _open_record_segment` 直接 `open(path,"wb")`；打不开时 `:6138` →
  `_report_record_abort:6142-6153` 显示「录音文件打不开（path）: OSError 原文」。
  录制面板**不 import** `mbdsdr_ai/recorder.py`。
- 回放面板：`desktop/main_window.py:7579-7608 _replay_recording` →
  `mbdsdr_ai/baseband_io.load_iq`；失败显示「读取文件失败：{异常}」或
  `rec["error"]`（「文件不存在: …」原文）；**不 import** `mbdsdr_ai/playback.py`。
- 三个底层模块的实际引用面：
  - `recording_library.py`：全仓 **无任何 Python 调用方**（只有测试）；
  - `recorder.py`：只被 `recorder_enhanced.py`、`recording_library.py` 引用，
    这两个模块同样无桌面调用方；
  - `playback.py`：只被 `experiments/`（`common/datasource.py`、`demo_spacetime.py`）引用。
- 判定：28 个 code 的返回路径终点都在上述「无调用方」模块里，错误不会上浮到面板 →
  仍然不补。

### 5.3 `missing_iq` 的截取规则（键名 + 深度 + 长度，均有数字）

- 摘要器 `desktop/ai_steps.py:95-106 summarize_result(text)`：
  1. 输入只有**一个字符串** `text`（`:100`）；
  2. **键名**：正则 `"(?:error|message)"\s*:\s*"([^"]*)"`（`:103`），只认 `error` 与
     `message` 两个键；正则直接在原始字符串上匹配，**不解析 JSON 层级**，因此对嵌套
     深度没有上限，但对非这两个键名一律不命中；
  3. **长度**：命中键 → 取该键的值并截断到 `summary_chars = 200` 字（`:105`，
     `desktop/tokens.py:351`）；未命中 → 把整段截断到 200 字（`:106`）。
- 输入来源：`steps_from_chat` 只传 `ToolResult.content`（`ai_steps.py:162,165,171`），
  **不传 `data`**；`data` 仅在 `:177-183` 且工具名属于 `WORKFLOW_TOOLS` /
  `SUBAGENT_TOOLS` 时才被 `_payload_from_content` 解析成子步骤（Meteor 工具不属于这两组）。
- 实测（2026-11-05）：`_meteor_lrpt_demod_fn_tool({"sample_rate": 72000})` 的
  `content` 长 **137** 字且不含 `missing_iq`；`data.error == "missing_iq"`；
  `summarize_result(content)` 返回该 137 字全文；`steps_from_chat(...)` 得到
  `step.error == ""`、summary 同样是这 137 字 → 用户看不到 code。
- 会显示 code 的反例条件：只有把**整个结果 dict** 交给
  `steps_from_tool_result`（`:190 json.dumps(result)` 整包序列化）时，摘要正则才会命中
  `data.error="missing_iq"` 并显示 `missing_iq`。该路只接收 worker 转发的硬件工具结果，
  而 `missing_iq` 来自软件工具，在 `desktop/ai_panel.py:614-615` 被 `skipped` 直接丢弃。

### 5.4 本轮复核修正了哪几行

| 行号 | 原写法 | 修正后 |
| --- | --- | --- |
| 第 76 行 | 「只落在 data 且摘要截不到」 | 键名 `error`/`message`、任意嵌套、200 字上限；实测 content 137 字、无 code、`step.error` 为空 |
| 第 79–87 行（`recording_library` 9 码） | 「桌面零 import」 | 面板直写 / 走 `baseband_io`；该库全仓无 Python 调用方，错误不上浮 |
| 第 88–90 行（`recorder` 3 码） | 「桌面零 import」 | 桌面 `open()` 直写，失败显示 OSError 原文；`recorder` 只被两个无调用方模块引用 |
| 第 91–106 行（`playback` 16 码） | 「桌面零 import」 | 桌面回放走 `baseband_io.load_iq`；`playback` 只被 `experiments/` 引用 |
| 第 107–113 行（TLE 7 码） | 「桌面零调用，只进 health/日志」 | 纠偏：桌面经 `TLEManager.fetch → TleFetcher` 真实拉取，但只显示固定通用提示，`failure_label` 桌面零引用 |

- **判定翻转：0 行；新增条目：0；修正依据/理由：36 行**。
- `mapping_gaps() == []`，`mapping_overlaps() == []`。

## 六、待归属（本轮只查不改，归属待确认）

| # | 位置 | 现状 | 建议 | 影响 |
| --- | --- | --- | --- | --- |
| 1 | `desktop/mcp_worker.py:229`、`:288-293` | 注释写「软件工具不二次转发（skipped）」，但 AI 侧软件工具**从不进 mcp_worker**（AI 工具在进程内 `ToolRegistry.execute`，结果直接回写模型上下文），这条链只承载 UI 直操命令 | 措辞改为「非硬件名命令不二次转发」 | 仅注释误导，无功能影响 |
| 2 | `desktop/mcp_worker.py:296-300` → `desktop/ai_panel.py:614-615` | UI 直操发出 `sdr_set_gain` 时，若固件 `tools/list` 未报该名且别名表未收录（现状未收录），返回 `skipped` 且步骤卡被直接丢弃，用户零反馈 | 归属 MCP/worker 线确认：补别名，或对 skipped 给一条状态栏提示 | 「用户看不到」而非「AI 看不到」，非 AI 链路缺陷 |

## 七、引用出处更正与本文件提交号核对（2026-11-05）

### 7.1 `desktop/troubleshooting.py:92` 的「2.048 MS/s」

- 更正：该行单位由 `2.048 MHz` 改为 `2.048 MS/s` 的是 **`ff964183`**
  （`fix(copy): 缺值不再渲染 0（APT 统计/卫星表单）与采样率建议改 MS/s`，属**文案审计线**批次），
  **不是** `70429a02`（参数治理线，清单 4 个文件，不含 `troubleshooting.py`）。
- 证据命令：

  ```text
  $ git log -S "2.048 MS/s" --oneline -- desktop/troubleshooting.py
  ff964183 fix(copy): 缺值不再渲染 0（APT 统计/卫星表单）与采样率建议改 MS/s

  $ git status --porcelain -- desktop/troubleshooting.py
  （无输出）

  $ git show ff964183 -- desktop/troubleshooting.py
  -        "在「控制」面板把「采样率」降到设备支持的档位（如 2.048 MHz）；"
  +        "在「控制」面板把「采样率」降到设备支持的档位（如 2.048 MS/s）；"
  ```

  说明：上面的 `-` 行是 `ff964183` 的历史**改前**状态，原样抄录、不修改——
  把它也改成 `MS/s` 会让 diff 两边相同，等于伪造证据。当前代码与 `+` 行一致
  （`git status --porcelain -- desktop/troubleshooting.py` 无输出）。

- 结论：**早已入库、改的人不是参数治理线**；`ff964183` 是 `2b7a71b9` 的祖先
  （`git merge-base --is-ancestor ff964183 2b7a71b9` 退出码 0），故 `2b7a71b9`
  不含该行是正常继承，**不是漏改**。参数治理线的 4 个复位路径与此行无关。

### 7.2 本文件其余提交号逐个核对

| 引用处 | 提交号 | 该提交实际内容 | 判定 |
| --- | --- | --- | --- |
| 一、四类统计；表 #1–#39 | `e95945bd` | 5 文件：`error_remedy.py`、`troubleshooting.py`（+82）、`test_error_remedy_links.py`、`test_planetarium_entry_visibility.py`、`test_troubleshooting_help.py`；39 个可见码补条目 | 一致 |
| 一、四类统计；表 #40–#52 | `0e4b9ef6` | 3 文件：`error_remedy.py`、`troubleshooting.py`（+42）、`test_error_remedy_links.py`；13 个只显示 message 的码补条目 | 一致 |
| —（原误写 `70429a02`，不在本文件正文） | `70429a02` | 4 文件：`main_window.py`、`docs/parameter-unification.md`、`sdr_backend.py`、`test_sample_rate_options.py` | 与 2.048 行无关 |

- 全文件内可核对的提交号共 2 个（`e95945bd`、`0e4b9ef6`），均为 `git show --stat` 实证过的真实提交；
  无其他凭印象引用。

## 八、新增登记的存量野码（15 个）

### 8.1 遗留 stdio MCP 壳（`mbdsdr_ai/core/mcp_server.py`，6 码，不可见）

`tests/test_error_catalog_guard.py::test_no_wild_codes` 报 **8 处引用 / 6 个码**
（`scan_not_found` 与 `decoder_not_found` 各 2 处，其余各 1 处）。
该文件属遗留 stdio MCP 壳：桌面硬件工具走 `desktop/mcp_worker.py` 的
`MBDSDRClient`（连 ai-sdr Mini 的 host:port），**不加载这个壳**；本仓也没有
其它 UI 渲染点，只有 MCP 客户端与测试脚手架会读这些返回值。
按判据「MCP 客户端不算用户可见面」→ 6 个全部**可达但不可见**。

| # | code | 触发条件 | 用户可见 | 可见路径 | 判定依据（文件:行） | 处置 |
| --- | --- | --- | --- | --- | --- | --- |
| 115 | `audio_start_failed` | `start_audio` 已连接但 `ctrl.start_audio()` 返回 False（无声卡/驱动不可用/采样率不支持） | 否 | 无（仅 MCP 协议返回） | `mbdsdr_ai/core/mcp_server.py:395-399`；桌面不加载该壳：`desktop/mcp_worker.py:229-300` 走 `MBDSDRClient` | 已登记目录（`LAYER_TOOL`，可重试）；`NO_ENTRY_REASONS`「遗留 stdio MCP 壳」 |
| 116 | `audio_output_unavailable` | `set_audio_output` 的索引不存在，或 PortAudio 不可用 | 否 | 无 | `mbdsdr_ai/core/mcp_server.py:621-630` | 同上 |
| 117 | `invalid_text` | FST4 编码收到缺呼号/网格的文本（如 `AUDIT`），`pack77` 解析失败 | 否 | 无 | `mbdsdr_ai/core/mcp_server.py:266-274` | 同上 |
| 118 | `scan_not_found` | `stop_scan` / `get_scan_results` 传入不存在或已过期的 handle（2 处引用） | 否 | 无 | `mbdsdr_ai/core/mcp_server.py:891-899`、`:901-906` | 同上 |
| 119 | `decoder_not_found` | `stop_decoder` / `get_decoder_messages` 传入不存在或已过期的 handle（2 处引用） | 否 | 无 | `mbdsdr_ai/core/mcp_server.py:928-936`、`:938-944` | 同上 |
| 120 | `vfo_not_found` | `set_primary_vfo` 传入不存在的 `vfo_id` | 否 | 无 | `mbdsdr_ai/core/mcp_server.py:1110-1117` | 同上 |

### 8.2「解码结果」族：`1e813902` 的 9 个原因码（可见-已补）

这 9 个是 `decode_pipeline` 的 `reject_reason` 经 `decode_router.py:919-921`
提升成 `error_code` 后，由状态面板解码卡渲染（`status_panel.py:252`
取 `error_code`，`:272-273` 输出 `错误码 {code}: {error}`）——**P2 真可见**。
补法：全部进 `troubleshooting.VISIBLE_BY_FAMILY["解码结果"]`，
`DECODE_ERROR_LABELS` 补齐 5 个原先没有中文标签的码（`status_panel.py:198-202`），
「下一步」逐码写具体动作（`troubleshooting.CODE_NEXT_STEPS`）。

| # | code | 触发分支 | 可见路径 | 判定依据（文件:行） | 处置 |
| --- | --- | --- | --- | --- | --- |
| 121 | `crc_failed` | AX.25 帧 CRC-16 不过（弱信号/速率不匹配） | P2 | 抛出：`decode_pipeline.py:563`；提升：`decode_router.py:919-921`；渲染：`status_panel.py:252,272-273` | 已补（解码结果族 + 具体建议） |
| 122 | `truncated_frame` | 帧尾不完整、采集提前结束 | P2 | 抛出：`decode_pipeline.py:567`；同上渲染 | 已补 |
| 123 | `no_frame_recovered` | 有同步但恢复不出完整帧（丢帧占多数） | P2 | 抛出：`decode_pipeline.py:289-290,405,571` | 已补 |
| 124 | `nothing_detected` | 鉴频后无任何同步候选（多半没信号/频点错） | P2 | 抛出：`decode_pipeline.py:520,525`（及 `:289-290`） | 已补 |
| 125 | `preamble_not_found` | ADS-B/APRS 找不到前导模板 | P2 | 抛出：`decode_pipeline.py:369`、`adsb.py:683`、`decode_router.py:344-345` | 已补 |
| 126 | `sample_rate_mismatch` | IQ 采样率低于下限或非整数倍（拒绝静默取整） | P2 | 抛出：`decode_router.py:844-852`、`decode_pipeline.py:210,626,805` | 已补（建议改整数倍档位） |
| 127 | `insufficient_samples` | 样本不足「最短一帧」 | P2 | 抛出：`decode_router.py:854-868`、`decode_pipeline.py:265,344,493` | 已补 |
| 128 | `invalid_wav` | WAV 容器/编码不可解析（非 PCM16 单声道） | P2 | 抛出：`decoders.py:877,1043`；经 `decode_router.py:919-921` 提升 | 已补 |
| 129 | `invalid_input` | 输入/参数组合非法（FT4/WSPR 参数错误） | P2 | 抛出：`decoders.py:973,1091` | 已补 |

- 这 9 个都**可达且可见**（不是「有码不需要建议」），故一个都没进 `NO_ENTRY_REASONS`；
  `NO_ENTRY_REASONS` 本轮只新增了 8.1 那 6 个只走 MCP 协议壳的码。

- 计数不手写：目录总数以 `len(error_catalog.ERRORS)` 为唯一真相源（本轮共 +15）。
- `mapping_gaps() == []`、`mapping_overlaps() == []`；`tests/test_error_remedy_links.py` 转绿。
- 8.1 的 6 个进 `NO_ENTRY_REASONS`「遗留 stdio MCP 壳」；8.2 的 9 个进「解码结果」族
  （各自带具体建议），无一条用笼统理由糊过去。
- 未修改 `mbdsdr_ai/core/mcp_server.py`（没有需要删除的不可达码，故不启用该例外）。

## 九、52 条「怎么办」路径逐条回代码验真（2026-11-06）

做法：`scripts/check_help_paths.py` 解析每条 remedy 的 `→` 分段，每段用
`git grep` 在生产源码里找**字符串字面量**；文档第三节的 P1–P5 渲染点
逐个核行号；文案承诺的操作结果（采样率整数倍、最短一帧、`record_dir`、
`squelch_default_db` 等）以机制锚点核。`tests/test_help_path_truthfulness.py`
含两条反证：把路径词改成源码里不存在的词，检查必须变红。

统计（本节只统计第三节表内的 52 条可见条目）：

| 分类 | 条数 | 说明 |
| --- | --- | --- |
| 路径全对 | 10 | 名称是源码字面量、行号未漂、操作结果有机制 |
| 名字不在源码 | 0 | 85 条文案的全部「」分段均命中生产源码字面量 |
| 行号漂移 | 42（含 5 条同时有操作结果不符） | 触发点/渲染点行号已过期，已按当前源码更新 |
| 操作结果不符 | 5 | 「采样率与**输入格式**到「控制」页签核对」：控制面板没有输入格式控件 |

> 42 = 37 条「仅行号漂移」+ 5 条「行号漂移 + 操作结果不符」；
> 10 + 42 = 52，无重叠漏计。

### 9.1 操作结果不符（5 条，文案改法）

| # | code | 原文案 | 问题 | 改法 |
| --- | --- | --- | --- | --- |
| 35 | `unknown_mode` | 族级：采样率与**输入格式**到「控制」页签核对 | 「控制」面板没有输入格式控件（只有解调模式/采样率/带宽/增益） | 删「与输入格式」 |
| 36 | `missing_sample` | 同上 | 同上 | 同上 |
| 37 | `unsupported_format` | 同上 | 同上 | 同上 |
| 38 | `decode_error` | 同上 | 同上 | 同上 |
| 39 | `not_implemented` | 同上 | 同上 | 同上 |

改法：`desktop/troubleshooting.py` 的 `VISIBLE_NEXT_STEPS["解码结果"]` 应写为
「采样率到「控制」页签核对后重试」，不再提输入格式。该文件此刻由
`接入层健壮性线` 持有 claim，本线这几处改动仍在工作区（未提交），
故本节不给 hash——等该文件落库后按 9.5 复跑即可核对。

### 9.2 行号漂移逐条（42 条）

| # | code | 原引用（已漂） | 现在（源码实位） |
| --- | --- | --- | --- |
| 1 | `tool_not_found` | `mbdsdr_ai/tool_registry.py:917` → `desktop/ai_panel.py:701` | `mbdsdr_ai/tool_registry.py:772-778` → `desktop/ai_panel.py:701` |
| 2 | `tool_unavailable` | `mbdsdr_ai/tool_registry.py:789,917` → `desktop/ai_panel.py:701` | `mbdsdr_ai/tool_registry.py:789-793` → `desktop/ai_panel.py:701` |
| 3 | `invalid_arguments_type` | `mbdsdr_ai/tool_registry.py:289,917` → `desktop/ai_panel.py:701` | `mbdsdr_ai/tool_registry.py:288-289,802-807` → `desktop/ai_panel.py:701` |
| 4 | `invalid_arguments` | `mbdsdr_ai/tool_registry.py:917,959` → `desktop/ai_panel.py:701` | `mbdsdr_ai/tool_registry.py:278-400,875-883` → `desktop/ai_panel.py:701` |
| 5 | `missing_required_params` | 同 #4 | `mbdsdr_ai/tool_registry.py:854-859` → `desktop/ai_panel.py:701` |
| 6 | `param_type_mismatch` | 同 #4 | `mbdsdr_ai/tool_registry.py:306-316` → `desktop/ai_panel.py:701` |
| 7 | `param_out_of_range` | 同 #4 | `mbdsdr_ai/tool_registry.py:324-335` → `desktop/ai_panel.py:701` |
| 8 | `param_not_in_enum` | 同 #4 | `mbdsdr_ai/tool_registry.py:394-396` → `desktop/ai_panel.py:701` |
| 9 | `invalid_json_arguments` | 同 #4 | `mbdsdr_ai/tool_registry.py:1031-1035` → `desktop/ai_panel.py:701` |
| 10 | `invalid_rs_shape` | 同 #4 | `mbdsdr_ai/tool_registry.py:2138-2140` → `desktop/ai_panel.py:701` |
| 11 | `empty_taps` | `mbdsdr_ai/sdr_tools.py:3154` → `desktop/ai_panel.py:701` | `mbdsdr_ai/tool_registry.py:3154-3155` → `desktop/ai_panel.py:701` |
| 15 | `compose_failed` | `mbdsdr_ai/sdr_tools.py:7132` → `desktop/ai_panel.py:701` | `mbdsdr_ai/sdr_tools.py:7152` → `desktop/ai_panel.py:701` |
| 16 | `gimbal_stop_failed` | `mbdsdr_ai/sdr_tools.py:7815` → `desktop/ai_panel.py:701` | `mbdsdr_ai/sdr_tools.py:7835` → `desktop/ai_panel.py:701` |
| 17 | `no_rssi` | `mbdsdr_ai/sdr_tools.py:7800` → `desktop/ai_panel.py:701` | `mbdsdr_ai/sdr_tools.py:7842-7847` → `desktop/ai_panel.py:701` |
| 18 | `offline_or_api_error` | `mbdsdr_ai/sdr_tools.py:7598,7624,7656,7691` → `desktop/ai_panel.py:701` | `mbdsdr_ai/sdr_tools.py:7618,7644,7676,7711` → `desktop/ai_panel.py:701` |
| 19 | `recording_not_found` | `mbdsdr_ai/agent.py:2861` → `desktop/ai_panel.py:701` | `mbdsdr_ai/agent.py:2866` → `desktop/ai_panel.py:701` |
| 20 | `subagent_not_found` | `mbdsdr_ai/agent.py:2683-2685` → `desktop/ai_panel.py:701` | `mbdsdr_ai/agent.py:2688-2690` → `desktop/ai_panel.py:701` |
| 21 | `agent_busy` | `mbdsdr_ai/agent.py:3784` → `desktop/ai_panel.py:1159` | `mbdsdr_ai/agent.py:126,3782-3794` → `desktop/ai_panel.py:1159` |
| 22 | `cancelled` | `mbdsdr_ai/agent.py:3900,3953` → `desktop/ai_panel.py:1159` | `mbdsdr_ai/agent.py:127,3903-3905,3956-3958,4131-4134` → `desktop/ai_panel.py:1159` |
| 24 | `ptt_not_on` | `mbdsdr_ai/sdr_tools.py:6697` → `desktop/ai_panel.py:701` | `mbdsdr_ai/sdr_tools.py:6744` → `desktop/ai_panel.py:701` |
| 25 | `capability_unknown` | `mbdsdr_ai/sdr_tools.py:6280,6304-6306` → `desktop/ai_panel.py:701` | `mbdsdr_ai/sdr_tools.py:6326-6327` → `desktop/ai_panel.py:701` |
| 27 | `invalid_vcdu` | `mbdsdr_ai/fengyun_sat.py:832,900`、`goes_lrit.py:915` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:263` | `mbdsdr_ai/fengyun_sat.py:834,902`、`goes_lrit.py:915` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` |
| 28 | `decode_failed` | `mbdsdr_ai/rtl433_decoder.py:803` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:263` | `mbdsdr_ai/rtl433_decoder.py:803` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` |
| 29 | `unknown_device` | `mbdsdr_ai/rtl433_decoder.py:798` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:263` | `mbdsdr_ai/rtl433_decoder.py:798` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` |
| 30 | `doppler_failed` | `mbdsdr_ai/satnogs_adapter.py:373` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:263` | `mbdsdr_ai/satnogs_adapter.py:373` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` |
| 31 | `ogn_decode_failed` | `mbdsdr_ai/sdrangel_plugins.py:411` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:263` | `mbdsdr_ai/sdrangel_plugins.py:411` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` |
| 32 | `schedule_failed` | `mbdsdr_ai/r2cloud_adapter.py:380` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:263` | `mbdsdr_ai/r2cloud_adapter.py:380` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` |
| 33 | `meta_failed` | `mbdsdr_ai/r2cloud_adapter.py:388` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:263` | `mbdsdr_ai/r2cloud_adapter.py:388` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` |
| 34 | `bad_hex` | `mbdsdr_ai/rds_lite.py:558` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:263` | `mbdsdr_ai/rds_lite.py:558` → `desktop/ai_panel.py:701`；`desktop/status_panel.py:273` |
| 35 | `unknown_mode` | `mbdsdr_ai/decode_router.py:573-578` → `desktop/status_panel.py:244,263` | `mbdsdr_ai/decode_router.py:811-816` → `desktop/status_panel.py:185,273` |
| 36 | `missing_sample` | `mbdsdr_ai/decode_router.py:586-591` → `desktop/status_panel.py:244,263` | `mbdsdr_ai/decode_router.py:827-831` → `desktop/status_panel.py:185,273` |
| 37 | `unsupported_format` | `mbdsdr_ai/decode_router.py:592-601` → `desktop/status_panel.py:244,263` | `mbdsdr_ai/decode_router.py:835-841` → `desktop/status_panel.py:185,273` |
| 38 | `decode_error` | `mbdsdr_ai/decode_router.py:46,13` → `desktop/status_panel.py:244,263` | `mbdsdr_ai/decode_router.py:408,913-921` → `desktop/status_panel.py:185,273` |
| 39 | `not_implemented` | `mbdsdr_ai/decode_router.py:47,14` → `desktop/status_panel.py:244,263` | `mbdsdr_ai/decode_router.py:382-383` → `desktop/status_panel.py:185,273` |
| 40 | `sdr_set_frequency_failed` | `mbdsdr_ai/sdr_backend.py:283,289` → `desktop/main_window.py:5329` | `mbdsdr_ai/sdr_backend.py:283,289` → `desktop/main_window.py:7304` |
| 41 | `sdr_set_sample_rate_failed` | `mbdsdr_ai/sdr_backend.py:368,374` → `desktop/main_window.py:5329` | `mbdsdr_ai/sdr_backend.py:368,374` → `desktop/main_window.py:7304` |
| 42 | `sdr_set_gain_failed` | `mbdsdr_ai/sdr_backend.py:402-413` → `desktop/main_window.py:5329` | `mbdsdr_ai/sdr_backend.py:418-423` → `desktop/main_window.py:7304` |
| 43 | `sdr_set_bandwidth_failed` | `mbdsdr_ai/sdr_backend.py:461,467` → `desktop/main_window.py:5329` | `mbdsdr_ai/sdr_backend.py:471-477` → `desktop/main_window.py:7304` |
| 44 | `doppler_compensation_failed` | `mbdsdr_ai/sdr_backend.py:822,862` → `desktop/main_window.py:5329` | `mbdsdr_ai/sdr_backend.py:832,872` → `desktop/main_window.py:7304` |
| 46 | `llm_rate_limited` | `mbdsdr_ai/model_manager.py:32,37` → `desktop/ai_panel.py:1159` | `mbdsdr_ai/model_manager.py:31-32` → `desktop/ai_panel.py:1159` |
| 47 | `llm_http_4xx` | `mbdsdr_ai/model_manager.py:37` → `desktop/ai_panel.py:1159` | `mbdsdr_ai/model_manager.py:35-36` → `desktop/ai_panel.py:1159` |
| 48 | `llm_http_5xx` | `mbdsdr_ai/model_manager.py:37` → `desktop/ai_panel.py:1159` | `mbdsdr_ai/model_manager.py:33-34` → `desktop/ai_panel.py:1159` |

### 9.3 路径全对（10 条）

| # | code | 判定依据（未变） |
| --- | --- | --- |
| 12 | `handler_error` | `mbdsdr_ai/tool_registry.py:929,955,960` → `desktop/ai_panel.py:701` |
| 13 | `exception` | `mbdsdr_ai/tool_registry.py:945` → `desktop/ai_panel.py:701` |
| 14 | `empty_result_without_reason` | `mbdsdr_ai/tool_registry.py:973-977` → `desktop/ai_panel.py:701` |
| 23 | `device_unavailable` | `mbdsdr_ai/sdr_tools.py:2960` → `desktop/ai_panel.py:701` |
| 26 | `NEED_HW` | `mbdsdr_ai/vna_tools.py:175,179` → `desktop/ai_panel.py:701` |
| 45 | `llm_offline` | `mbdsdr_ai/model_manager.py:268,275` → `desktop/ai_panel.py:1159` |
| 49 | `llm_timeout` | `mbdsdr_ai/model_manager.py:40` → `desktop/ai_panel.py:1159` |
| 50 | `llm_unreachable` | `mbdsdr_ai/model_manager.py:42` → `desktop/ai_panel.py:1159` |
| 51 | `llm_unexpected` | `mbdsdr_ai/model_manager.py:44` → `desktop/ai_panel.py:1159` |
| 52 | `missing_error_detail` | `mbdsdr_ai/tool_registry.py:967` → `desktop/ai_panel.py:696-699` |

### 9.4 表外条目（15 条精选 + 8.2 的 9 个解码码）的 6 处修正

| code | 原文案 | 问题（已回代码核） | 改法 |
| --- | --- | --- | --- |
| `network_timeout` | 「设置」→「AI」把「请求超时（秒）」`ai_timeout` 调大后重试「更新卫星 TLE」 | TLE 拉取用 `TleFetcher` 的固定 15s（`new_spacetime_tle.py`），`ai_timeout` 只写进 `AgentConfig.timeout`，改它对 TLE 无效 | 删除 `ai_timeout` 承诺，改为「确认网络/DNS 与系统代理，再重试；rtl_tcp 超时就在设备对话框选「远程 rtl_tcp 源」」 |
| `sample_rate_mismatch` | 改成整数倍档位，「如 ADS-B 用 2.048 MS/s」 | `decode_router._IQ_RATE_RULES` 要求 fs ≥ 2 MS/s 且为 **1 MS/s 整数倍**；2.048 MS/s 不是整数倍，照做会再次报同一码 | 改为「2 MS/s 或 4 MS/s，即 1 MS/s 的整数倍」 |
| `crc_failed` | 「控制」页签降低**符号速率**/带宽 | 「控制」面板只有解调带宽与增益，没有符号速率控件 | 改为「降低解调带宽并提高增益」 |
| `invalid_input` | 到「控制」页签核对模式与**符号速率** | 同上，控制面板没有符号速率控件 | 改为「核对解调模式」 |
| `insufficient_samples` | 点顶栏「开始/停止录音」 | 「开始/停止录音」在「工具」菜单（Ctrl+R），不在顶栏 | 改为「点「工具」菜单的「开始/停止录音」（Ctrl+R）」 |
| `truncated_frame` | 点「开始/停止录音」 | 未写位置，用户会在顶栏找 | 补「工具」菜单 |

另：`insufficient_samples` / `crc_failed` 里的「APRS ≥5s」不是代码阈值——
`decode_router` 的 APRS `min_duration_seconds=0.2`（最短一帧），5s 是实战余量；
工作区文案写的是「录满一整帧（实战建议 ≥5s）」，不把 5s 说成硬性门槛；
该改动随 `desktop/troubleshooting.py` 一起未提交，hash 待补。

### 9.5 复跑

```text
python scripts/check_help_paths.py            # 退出码 0 = 全绿；--markdown 出逐条表
python -m pytest tests/test_help_path_truthfulness.py -q
# 6 passed
```

# mbdsdr_ai · AI 定义无线电智能内核

> 本文件随 Phase4（P4）缺口清扫同步当前行为：工具面、已摘除项、诚实标注项。
> 基线：远端 main `be1c653`；本批改动未 commit/push（云环境无有效凭据）。
> 许可证：MIT。

## 1. 这是什么

`MBDSDRAgent` 是一个把全部 SDR 能力封装成 LLM 可调用工具（function calling）的 Python 原型内核。
LLM 通过 `tool_registry` 调用约 280 个工具（agent.py 注册 ~112 + sdr_tools.py ~137 + tool_registry 内置 63），
工作流引擎把多个工具调用串成预设多步任务（干扰定位 / NOAA APT 解码 / SSTV / APRS / 基带录制等）。

**云 VM 无硬件（无 USB/串口/声卡）**：所有依赖真实 RTL-SDR / GNSS / 云台 / 声卡的工具在无设备时
返回诚实失败（"当前无连接设备，请先 sdr_connect 或 sdr_open_iq_file"），**不造假数据、不静默 mock**。
真机（RTL-SDR + GNSS + 天线）在用户本地。

## 2. 工具面现状（P4 清扫后）

### 2.1 已从 LLM 工具面摘除（后端类保留，供单测/未来接线）

| 摘除项 | 原因 | 保留的后端 |
|---|---|---|
| `hook_*`（4个） | 生产侧零事件源，LLM 对着空气喊话 | `hooks.py` HookManager 事件总线 |
| `orchestrator_*`（6个） | workflow_engine 的劣化重写，LLM 不知用哪个 | `orchestrator.py` |
| `plugin_*`（6个） | `plugins/` 空目录，无插件生态 | `plugin_manager.py` |
| `learning_learn` / `learning_suggestion` | 无聚合；中文匹配失效且学到的规律从不回灌主循环 | `self_learning.py`（record/experiences/patterns/stats 仍注册） |

### 2.2 诚实标注项（保留工具，但描述不再宣称假能力）

| 工具 | 标注内容 |
|---|---|
| `pose_update_imu` / `pose_update_gps` | **手动喂入**位姿；当前无 BMI260/TMAG5273/ATGM336H 硬件推流，传入数值由调用方提供，仅用于融合算法验证/仿真。接真实硬件需 HAL/serial_gnss 侧补驱动线程。 |
| `pose_get` | 状态由手动喂入累计，不反映设备真实姿态。 |
| `subagent_create` | 非自主 AI 子代理（无 LLM 推理/多步循环）；仅 spectrum_analyzer / satellite_tracker / baseband_recorder 三类有内置默认工具，其余类型须在 input_data 传 `tool_name`。 |

### 2.3 接真项（P4 修复，假闭环变真）

| 项 | 修复 |
|---|---|
| D6 `workflow_execute` 跨步 `{{var}}` 回填 | executor 现返回 `ToolResult` 本体；关键工具在 `.data` sidecar 吐 `recording_path` / `doppler_freq` / `interference_freq_hz` / `rssi_by_azimuth` / `interference_az` / `peak_az(±20)`，workflow_engine 据此回填后续步骤模板。 |
| D10 `scheduler` cron | schema 暴露 `cron` + `cron_expression`；修了 cron 星期映射（cron 周日=0 曾被错配成 Python `weekday()` 周一=0）；add_task 初值走 `_next_cron_run`。 |
| `sdr_set_frequency/sample_rate/demod` | 原实现 success 与 content 条件各调一次 setter（重复副作用），改为只调一次；无设备时返回诚实失败而非 AttributeError。 |
| `sdr_set_bandwidth/gain/agc/squelch/volume` | 无设备 None 保护，返回诚实失败。 |
| `astro_pointing_guidance` | 原 `AntennaParams(beamwidth_deg=)` 触发 TypeError（该字段是推导属性非构造参数）；用户给波束宽度时据此重算 in_beam。 |
| `ntrip_connect` | 原 `onnect()`/`lose()` 拼写导致 NameError，修为 `stream.connect()`/`close()`。 |

## 3. 诚实空态约定（红线）

- 无硬件：返回 `ToolResult(success=False, content="...请先 sdr_connect / sdr_open_iq_file / 配置地面站坐标")`。
- 无录制文件 / 无技能 / 无插件：返回明确空态，不渲染假列表。
- LLM 实验无 API key：返回 `PENDING_ONLINE_RUN`，不伪造在线结果。
- 测试只对真实行为断言；存在性 hasattr 断言不算"测过"。

## 4. 目录要点

- `agent.py`：Agent 装配 + LLM 工具注册（~112 个）。
- `sdr_tools.py`：SDR 主体工具（~137 个，最大文件）。
- `workflow_engine.py`：预设工作流 + `{{var}}` 模板回填。
- `scheduler.py`：定时任务（interval/once/cron）。
- `pose.py` / `gnss_monitor.py` / `serial_gnss.py`：位姿融合（硬件推流待接）。
- 后端保留但不在 LLM 面：`hooks.py` / `orchestrator.py` / `plugin_manager.py` / `self_learning.py`。

## 5. 已知未完成 / 转后续 wave

- 移动端 production 外壳尚未接线录音/回放 building blocks（P3 范围）。
- `satdump_*` 依赖外部 SatDump 二进制，常态"未安装"；`meteor_*` LRO EKF 量纲问题（A4/W2d 范围）。
- evolution 工具默认门控关闭（`config.enable_self_evolution=False`），高风险提案无 apply/confirm 闭环。

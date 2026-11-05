# 错误目录（面向用户失败话术的单一来源）

本表由 `mbdsdr_ai/error_catalog.py` 生成并镜像。**代码取文案，文档只读**：
任何面向用户的失败文案都必须来自本表，禁止在工具、接入层、录制库、TLE、
AI 层各自拼中文。守卫测试 `tests/test_error_catalog_guard.py` 校验两边不漂移。

## 1. 读表约定

- **错误码**：稳定 ASCII 标识；日志、审计、测试只认码，文案改版不改码。
- **用户看到的一句话**：完整句子，可含占位符；缺失占位符保留原样。
- **下一步建议**：用户/模型可直接执行的动作，必须与「可否重试」一致。
- **可否重试**：指*同一条件下*再试一次是否有意义。不可重试的条目，任何层
  都不得建议直接重试，只能建议改变输入/环境或换方法。
- **动态家族**：仅 `exception:<原始异常类型>`（工具/handler 抛出）与
  `playback_<底层码>`（回放打开失败）两组；表中各占一行，守卫按前缀放行。

## 2. 分层

| 层 | 说明 | 典型模块 |
|---|---|---|
| `tool` | 工具层（ToolRegistry / Agent 工具面） | tool_registry.py / sdr_tools.py / agent.py |
| `decode` | 解码层（decode_router / 专用解码器） | decode_router.py / gk2a_lrit.py / rds_lite.py |
| `access` | 接入层（SDR 后端 / 安全门 / 客户端） | sdr_backend.py / rtltcp_client.py / spyserver_client.py / core/ |
| `recorder` | 录制与回放层（recorder / playback / recording_library） | recorder.py / playback.py / recording_library.py |
| `tle` | TLE 层（tle_fetch / 缓存 / 新鲜度） | tle_fetch.py |
| `ai` | AI 层（model_manager / agent 并发） | model_manager.py / agent.py |
| `ui` | 界面/展示层（error_handler） | error_handler.py |
| `common` | 通用（缺上下文兜底） | tool_registry.py / sdr_tools.py / rtltcp_client.py |

## 3. 错误码表

| 错误码 | 层 | 含义 | 用户看到的一句话 | 下一步建议 | 可重试 |
|---|---|---|---|---|---|
| `NEED_HW` | tool | 需要真实硬件 | 本操作需要真实硬件（{detail}），当前为无硬件/离线环境。 | 接入对应设备后重试；无硬件时改用只读/模拟能力并明确标注。 | 否 |
| `capability_unknown` | tool | 设备能力未知 | 设备能力未声明，无法判断是否支持 {capability}。 | 改用已声明能力的设备，或先探测能力再重试。 | 否 |
| `compose_failed` | tool | 报文组装失败 | 报文组装失败：{detail} | 检查输入字段后重试。 | 否 |
| `device_unavailable` | tool | 设备不可用 | 设备不可用（{detail}）。 | 检查连接与驱动，或换一个可用设备。 | 否 |
| `empty_result_without_reason` | tool | 成功但无内容且无原因 | 工具 '{tool}' 返回了空结果且未提供原因（已按失败处理）。 | 检查输入是否为空/过短，或改用能给出诚实空态原因的工具。 | 否 |
| `empty_signal` | tool | 输入信号为空 | 输入信号为空（样本数为 0）；未执行。 | 提供非空采样后重试。 | 否 |
| `empty_taps` | tool | 滤波抽头为空 | 滤波器抽头为空（taps 列表长度为 0）；未执行。 | 提供至少一个非零抽头后重试。 | 否 |
| `exception` | tool | handler 抛异常（exception:<类型>） | 工具 '{tool}' 调用异常（{exc_type}）：{detail} | 同类参数重试不超过 2 次；仍失败则换方法或向用户说明已失败。 | 是 |

| `gimbal_stop_failed` | tool | 云台停止失败 | 云台停止指令未执行（{detail}）。 | 确认云台后端可用后重试。 | 是 |
| `handler_error` | tool | handler 返回失败 | 工具 '{tool}' 执行失败：{detail} | 按报错修正参数或换工具；不要把失败当成已执行。 | 否 |
| `invalid_arguments` | tool | 参数内容不合法 | 工具 '{tool}' 参数不合法：{detail} | 按参数说明修正后重试；不要原样重发同一组坏参数。 | 否 |
| `invalid_arguments_type` | tool | 调用参数不是 JSON 对象 | 工具 '{tool}' 的调用参数必须是 JSON 对象，收到 {actual}；未执行该工具。 | 重新生成键值对形式的 JSON 对象（参数名作键、数值作值，单位按参数说明）；不要传数组、字符串或裸标量。 | 否 |
| `invalid_json_arguments` | tool | 工具调用参数 JSON 非法 | 工具 '{tool}' 的参数不是合法 JSON，未执行。收到原文（前 200 字符）：{preview} | 重新生成完整、闭合的 JSON 对象参数；不要截断字符串，不要用单引号/尾逗号/Python 字面量。 | 否 |
| `invalid_rs_shape` | tool | RS 矩阵形状错误 | 参数 '{param}' 的形状不符合要求：{detail} | 按说明传入二维 RS 矩阵后重试。 | 否 |
| `missing_required_params` | tool | 缺少必填参数 | 工具 '{tool}' 缺少必填参数：{missing}。参数说明：{hints} | 按上面的参数说明补齐必填项后重试；可选参数不确定时省略即可，不要编造取值。 | 否 |
| `no_rssi` | tool | 未采到 RSSI | 本次扫描没有采到任何 RSSI 样本。 | 检查 SDR 连接与增益后重试。 | 是 |
| `not_connected` | tool | 未连接设备 | 尚未连接设备，无法执行 {action}。 | 先调用连接工具，连接成功后再执行。 | 否 |
| `offline_or_api_error` | tool | 联网接口失败或离线 | 联网接口未返回数据（{detail}）。 | 联网后重试；离线时改用本地数据并标注来源。 | 否 |
| `param_not_in_enum` | tool | 参数不在允许取值内 | 参数 '{param}' 不在允许取值 {allowed} 中；未执行该工具。 | 从允许取值中选一个后重试；不要编造枚举值。 | 否 |
| `param_out_of_range` | tool | 参数越界 | 参数 '{param}' 超出允许范围（{detail}）；未执行该工具。 | 把参数改到允许范围内后重试。 | 否 |
| `param_type_mismatch` | tool | 参数类型不符 | 参数 '{param}' 需要 {expected}，收到 {actual}；未执行该工具。 | 按参数类型修正后重试。 | 否 |
| `recording_not_found` | tool | 录像或模板不存在 | 记录/模板 '{name}' 不存在。 | 用列表工具确认名称后重试。 | 否 |
| `subagent_not_found` | tool | 子代理不存在 | 子代理 '{name}' 不存在或已结束。 | 先创建/列出子代理，再用其 ID 调用。 | 否 |
| `tool_not_found` | tool | 工具名未注册 | 工具 '{tool}' 不存在。可用工具：{available} | 用 list_tools 查看全部工具名或按类别检索；确认名称拼写与前缀（如 sdr_/vna_）后重试。 | 否 |
| `tool_unavailable` | tool | 工具存在但当前不可用 | 工具 '{tool}' 当前不可用（设备未连接或功能未启用）。 | 先调用连接/枚举工具（如 sdr_list_devices → sdr_connect，或 vna_connect）；无硬件时改用返回诚实空态的只读工具，不要虚构数据。 | 否 |
| `unknown_tool` | tool | 工具名未知 | 未知工具 '{tool}'。 | 用 list_tools 确认工具名后重试。 | 否 |
| `workflow_executor_missing` | tool | 工作流执行器未接线 | 工作流执行器未设置，无法执行工具步骤。 | 先调用 set_tool_executor() 完成接线。 | 否 |
| `bad_hex` | decode | 十六进制输入非法 | 十六进制输入不合法：{detail} | 确认只含 0-9/a-f 且长度为偶数后重试。 | 否 |
| `crc_failed` | decode | 校验失败 | {mode} 校验失败（CRC/FCS）：{detail} | 确认信号质量与调制参数后重试。 | 否 |
| `decode_error` | decode | 解码失败 | 解码失败：{detail} | 检查样本完整性与参数；同一输入原样重试无用。 | 否 |
| `decode_failed` | decode | 无线电解码失败 | 解码失败（{detail}）；可能是信号质量不足。 | 改善天线/增益/频率后重试。 | 是 |
| `doppler_failed` | decode | 多普勒补偿失败 | 多普勒补偿计算失败：{detail} | 确认 TLE、时刻与频点有效后重试。 | 是 |
| `empty_input` | decode | 输入为空 | 输入为空：{detail} | 提供非空输入后重试。 | 否 |
| `insufficient_samples` | decode | 输入样本不足一帧 | {mode} 输入太短：{rule} 需要 ≥ {required} 样本，实际 {actual}。 | 补足至少一帧的采样后重试（样本长度不足，不是解码失败）。 | 否 |
| `invalid_input` | decode | 输入或参数非法 | 输入或参数不合法：{detail} | 按错误信息修正参数或换用合规输入后重试。 | 否 |
| `invalid_vcdu` | decode | VCDU 帧非法 | VCDU 帧校验/长度不合法：{detail} | 确认输入为完整 CADU/VCDU 帧后重试。 | 否 |
| `invalid_wav` | decode | WAV 不可解析 | WAV 无法作为单声道音频读取：{detail} | 换用 16-bit PCM 单声道 WAV，或修复容器后重试。 | 否 |
| `meta_failed` | decode | 云图元数据获取失败 | 元数据获取失败：{detail} | 检查产品编号后重试。 | 否 |
| `missing_dependency` | decode | 缺少内置依赖模块 | 缺少内置依赖模块 {module}（实现文件 {impl} 不可导入）。 | 安装/恢复该依赖模块后重试。 | 否 |
| `missing_iq` | decode | 缺少 IQ 输入 | 缺少 iq_base64 输入，无法解调。 | 提供 iq_base64 后重试。 | 否 |
| `missing_sample` | decode | 缺少输入样本 | 输入样本不存在或为空：{path}（这是缺样本，不是解码失败）。 | 先录制或指定存在的样本文件后重试。 | 否 |
| `no_frame_recovered` | decode | 未能恢复完整帧 | {mode} 未能恢复完整帧：{detail} | 补足样本或改善信噪比后重试。 | 否 |
| `not_implemented` | decode | 能力未接线 | {mode} 的通用入口未接线（实现文件 {impl}）。 | 改用已接线入口或外部工具；不要宣称已完成解码。 | 否 |
| `nothing_detected` | decode | 未检测到帧 | {mode} 未检测到帧：{detail} | 检查频点、增益与调制参数后重试。 | 否 |
| `ogn_decode_failed` | decode | OGN 报文解码失败 | OGN 报文解码失败：{detail} | 检查信号质量与增益后重试。 | 是 |
| `preamble_not_found` | decode | 未找到前导 | {mode} 未找到前导/同步模板：{detail} | 确认频点与采样率正确、信号存在后重试。 | 否 |
| `sample_rate_mismatch` | decode | 采样率不满足整数倍约束 | {mode} 要求 {rule}，收到 fs={actual} Hz（按当前采样率取整为 sps={sps}，拒绝静默取整）。 | 按解码器要求的整数倍采样率重新录制或重采样后重试。 | 否 |
| `schedule_failed` | decode | 云图排程失败 | 排程失败：{detail} | 检查时间与卫星参数后重试。 | 否 |
| `truncated_frame` | decode | 帧被截断 | {mode} 帧被截断：{detail} | 延长采集时长后重试。 | 否 |
| `unknown_device` | decode | 未知设备 | 未知设备 '{device}'。 | 从支持的设备列表中选择后重试。 | 否 |
| `unknown_device_type` | decode | 未知设备类型 | 未知设备类型 '{device}'。 | 从支持的设备类型中选择后重试。 | 否 |
| `unknown_mode` | decode | 未知数字模式 | 未知数字模式 '{mode}'；可用模式：{available} | 从可用模式中选择后重试。 | 否 |
| `unsupported_format` | decode | 输入格式不支持 | {mode} 需要 {allowed}，收到 {actual}。 | 换用支持的格式，或先转换文件后重试。 | 否 |
| `bad_arguments` | access | 客户端参数校验失败 | 参数校验失败：{detail} | 按参数说明修正后重试。 | 否 |
| `bad_command` | access | 指令不合法 | 指令不合法：{detail} | 从支持的指令列表中选择后重试。 | 否 |
| `bad_config` | access | 配置不合法 | 配置不合法：{detail} | 修正配置项后重试。 | 否 |
| `bad_json` | access | JSON 解析失败 | JSON 解析失败：{detail} | 确认输入为合法 JSON 后重试。 | 否 |
| `config_not_found` | access | 配置文件不存在 | 配置文件不存在：{detail} | 先创建配置或确认路径后重试。 | 否 |
| `decoder_not_available` | access | 解码器不可用 | 解码器不可用：{detail} | 安装对应解码依赖后重试。 | 否 |
| `denied_by_policy` | access | 策略拒绝执行 | denied_by_policy：策略拒绝执行 {tool}（规则集不可被 armed 覆盖）。 | 换用允许的工具或修改策略；不要重复尝试同一个被拒调用。 | 否 |
| `device_busy` | access | 设备被占用 | 设备被其它进程占用：{detail} | 关闭占用进程后重试；不要并发打开同一设备。 | 是 |
| `device_not_found` | access | 找不到设备 | 找不到 SDR 设备：{detail} | 检查 USB/驱动后重新枚举。 | 否 |
| `doppler_compensation_failed` | access | 接收链多普勒补偿失败（预测不可用） | 多普勒补偿失败：{detail}。本块音频已跳过，未按 0 补偿。 | 检查卫星目标/TLE 是否有效、本站位置是否已设置；修复后重开补偿。 | 是 |
| `driver_not_found` | access | 找不到 SDR 驱动 | 找不到 SDR 驱动：{detail} | 安装驱动/DLL 后重启程序。 | 否 |
| `export_path_out_of_root` | access | 导出路径越界 | 导出路径越界，拒绝写入：{detail} | 把路径改到允许根目录内，或先设置 MBDSDR_EXPORT_ROOT。 | 否 |
| `frequency_out_of_range` | access | 频率超出范围 | 频率 {freq} Hz 超出设备范围：{detail} | 改用设备可调范围内的频率。 | 否 |
| `identity_mismatch` | access | 站点身份不匹配 | 站点身份不匹配：{detail} | 确认站点 ID 与数据库一致后重试。 | 否 |
| `internal_error` | access | 内部异常 | 内部错误（{exc_type}）：{detail} | 重试一次；仍失败请收集日志（含异常类型与上下文）反馈开发者。 | 是 |
| `invalid_argument` | access | 参数校验失败 | 参数校验失败：{detail} | 按参数说明修正后重试（本库不做静默钳位）。 | 否 |
| `manual_gate_required` | access | 需要外部确认 | 未执行 {tool}：缺少外部确认（{reason}）。模型自报的 arm_manual 不构成确认。 | 由真人经外部确认通道签发一次性 confirm_token 后重试；token 有有效期且只能消费一次。 | 否 |
| `no_audio_output` | access | 没有可用音频输出 | 没有可用音频输出设备：{detail} | 检查系统音频设备后重试。 | 否 |
| `no_downlink_known` | access | 无已知下行频点 | 没有该卫星的已知下行频点：{detail} | 先补全频点库或显式指定频率。 | 否 |
| `no_pass` | access | 当前无过境 | 当前时间窗内没有可用过境：{detail} | 扩大时间窗或换卫星后重试。 | 否 |
| `not_available` | access | 能力不可用 | 能力不可用：{detail} | 接入所需后端后重试。 | 否 |
| `ptt_not_on` | access | PTT 未打开 | PTT 未打开，发射未执行。 | 确认 PTT 控制可用后再发射。 | 否 |
| `sample_rate_not_supported` | access | 采样率不支持 | 设备不支持采样率 {rate} Hz：{detail} | 改用设备支持的采样率。 | 否 |
| `sdr_error` | access | SDR 通用错误 | SDR 错误：{detail} | 按错误详情处理；必要时重启程序。 | 否 |
| `sdr_set_bandwidth_failed` | access | 设置带宽失败 | 设置带宽失败（{detail}），已回滚到原带宽。 | 确认带宽被设备支持后重试。 | 是 |
| `sdr_set_frequency_failed` | access | 设置频率失败 | 设置频率失败（{detail}），已回滚到原频率。 | 确认频率在设备可调范围内后重试。 | 是 |
| `sdr_set_gain_failed` | access | 设置增益失败 | 设置增益失败（{detail}），已回滚到原增益。 | 确认增益在设备范围内后重试。 | 是 |
| `sdr_set_sample_rate_failed` | access | 设置采样率失败 | 设置采样率失败（{detail}），已回滚到原采样率。 | 确认采样率被设备支持后重试。 | 是 |
| `spill_write_failed` | access | 溢出数据落盘失败 | 大字段落盘失败：{detail} | 检查磁盘/临时目录后重试。 | 是 |
| `station_untrusted` | access | 站点不在可信列表 | 站点不在可信列表（{detail}）。 | 先把该站点加入可信列表，或换用可信站点。 | 否 |
| `unknown_command` | access | 未知指令 | 未知指令 '{detail}'。 | 从支持的指令列表中选择。 | 否 |
| `unknown_server` | access | 未知服务器 | 未知服务器 '{detail}'。 | 从已配置服务器列表中选择。 | 否 |
| `unsupported` | access | 不支持的操作 | 不支持的操作：{detail} | 改用受支持的操作。 | 否 |
| `usb_disconnected` | access | USB 已断开 | USB 设备已断开：{detail} | 重新插拔设备并重新连接。 | 是 |
| `bad_field` | recorder | 字段不合法 | 字段不合法：{detail} | 按字段说明修正后重试。 | 否 |
| `bad_param` | recorder | 参数不合法（录音库） | 参数不合法：{detail} | 修正参数后重试。 | 否 |
| `bad_parameter` | recorder | 参数不合法 | 参数不合法：{detail} | 修正参数后重试。 | 否 |
| `corrupt` | recorder | 文件损坏 | 文件损坏：{detail} | 换一个完整的录制文件。 | 否 |
| `datatype` | recorder | SigMF 数据类型不支持 | SigMF 数据类型不支持：{detail} | 使用 cf32/complex64 录制。 | 否 |
| `deleted` | recorder | 回放文件被删除 | 回放文件在读取过程中被删除或移走。 | 恢复文件或改用其他文件。 | 否 |
| `dest_missing` | recorder | 目标不存在 | 目标不存在：{detail} | 确认目标路径存在后重试。 | 否 |
| `disk_full` | recorder | 磁盘空间不足 | 录制失败：磁盘空间不足（{detail}）。 | 清理磁盘或换目标目录后重新录制。 | 否 |
| `empty` | recorder | 文件为空 | 数据文件为空（0 字节）。 | 重新录制后再回放。 | 否 |
| `entry_not_found` | recorder | 条目不存在 | 录音条目不存在：{detail} | 刷新列表后用有效条目 ID 重试。 | 否 |
| `eof` | recorder | 读到文件末尾 | 已读到文件末尾（未循环）。 | 开启 loop 或重新开始回放。 | 否 |
| `file_missing` | recorder | 录音文件不存在 | 录音文件不存在：{detail} | 刷新录音库或确认文件路径后重试。 | 否 |
| `io` | recorder | 文件读写失败 | 录制文件读写失败（{detail}）。 | 检查磁盘与路径后重试。 | 是 |
| `io_error` | recorder | 录音库操作失败 | 录音库操作失败：{detail} | 检查路径、权限与索引后重试。 | 是 |
| `library_busy` | recorder | 录音库被占用 | 录音库正被另一进程占用（{detail}） | 等对方释放或关闭另一个实例后重试。 | 是 |
| `library_not_found` | recorder | 新目录无索引 | 新目录下没有索引：{detail} | 先在新目录完成扫描，再迁移/切换。 | 否 |
| `missing` | recorder | 文件不存在 | 找不到数据文件：{path} | 确认路径存在后重试。 | 否 |
| `no_data` | recorder | 回放源无更多数据 | 回放源无更多数据：{detail} | 检查文件状态或开启循环。 | 否 |
| `not_found` | recorder | 索引不存在 | 录音库索引不存在：{detail} | 先初始化/扫描录音目录后重试。 | 否 |
| `not_writable` | recorder | 目标不可写 | 录制失败：目标目录不可写（{detail}）。 | 检查权限或只读挂载后重试。 | 否 |
| `open_failed` | recorder | 回放文件打开失败 | 回放文件打开失败：{detail} | 确认数据文件与 meta 完整后重试。 | 是 |
| `playback` | recorder | 回放打开失败（playback:<底层码>） | 回放文件无法打开：{reason} | 确认录制文件与 SigMF 元数据完整后重试。 | 否 |

| `playback_failed` | recorder | 回放失败 | 回放失败：{detail} | 确认文件完整后重试。 | 是 |
| `rate_mismatch` | recorder | 采样率不匹配 | 回放源采样率固定为 {actual} Hz，不能改为 {requested} Hz。 | 使用录制时的采样率。 | 否 |
| `rate_unknown` | recorder | 采样率未知 | 录制文件未声明采样率，拒绝猜测改写。 | 补全 SigMF 元数据后重试。 | 否 |
| `truncated` | recorder | 样本被截断 | 记录已截断：{detail} | 重新录制完整文件。 | 否 |
| `tx_source_deleted` | recorder | 发射源被删除 | 发射源文件在发射过程中被删除。 | 恢复文件后重新发射。 | 否 |
| `tx_source_failed` | recorder | 发射源失效 | 发射源在发射过程中失效：{detail} | 检查文件与设备后重新发射。 | 否 |
| `unsupported_kind` | recorder | 记录类型不支持 | 记录类型不支持：{detail} | 换用支持的类型后重试。 | 否 |
| `wav_error` | recorder | WAV 写入失败 | WAV 写入失败（{detail}）。 | 检查音频参数与磁盘后重试。 | 否 |
| `empty_response` | tle | 空响应 | TLE 源返回空响应。 | 稍后重试或换源。 | 是 |
| `http_4xx` | tle | 4xx 拒绝 | TLE 源拒绝请求（4xx）：{detail} | 确认卫星编号与 URL；不要立即重试。 | 否 |
| `http_5xx` | tle | 5xx 服务端错误 | TLE 源服务端错误（5xx）：{detail} | 按退避时间重试。 | 是 |
| `network_other` | tle | 其他网络错误 | TLE 源网络错误：{detail} | 稍后重试；持续失败换源。 | 是 |
| `network_timeout` | tle | 网络超时 | TLE 源请求超时：{detail} | 稍后重试；持续超时则换网络或换源。 | 是 |
| `network_unreachable` | tle | 网络不可达/DNS 失败 | TLE 源不可达（{detail}） | 检查网络/代理/DNS 后重试。 | 是 |
| `parse_failure` | tle | 返回内容解析失败 | TLE 返回内容不含合法两行根数：{detail} | 重试一次；持续失败换源。 | 是 |
| `rate_limited` | tle | 429 限流 | TLE 源限流（429）：{detail} | {retry_in} 秒后再试。 | 是 |
| `tle_unknown` | tle | 失败缺少上下文 | TLE 刷新失败，但未提供状态码/异常/响应体（调用方缺少上下文）。 | 检查抓取链路日志，补上失败上下文后再判断。 | 否 |
| `agent_busy` | ai | Agent 正忙 | Agent 正在处理另一请求，本次未执行。 | 等当前请求结束或取消后重试。 | 是 |
| `api_key_not_set` | ai | 未设置模型 API Key | 未设置模型 API Key（{detail}），无法调用模型。 | 在配置中填写 api_key 后重试。 | 否 |
| `cancelled` | ai | 请求已取消 | 请求已取消。 | 需要时重新发起。 | 否 |
| `llm_http_4xx` | ai | 模型请求被拒绝 | 模型接口返回 HTTP {status}：{detail} | 检查 API Key、模型名与请求参数；不要原样重试。 | 否 |
| `llm_http_5xx` | ai | 模型服务端错误 | 模型接口返回 HTTP {status}：{detail} | 退避后重试。 | 是 |
| `llm_offline` | ai | 离线模式拒绝联网 | 离线模式（MBDSDR_OFFLINE）：模型 API 需要联网，已拒绝调用（本地无模型可替代） | 关闭离线模式并联网后重试；或改用本地能力。 | 否 |
| `llm_rate_limited` | ai | 模型接口限流 | 模型接口限流（429）。 | {retry_in} 秒后重试。 | 是 |
| `llm_timeout` | ai | 模型请求超时 | 模型请求超时（{timeout} 秒）。 | 稍后重试或缩短上下文。 | 是 |
| `llm_unexpected` | ai | 模型调用异常 | 模型调用异常（{exc_type}）：{detail} | 不要自动重试；把异常类型与请求信息反馈开发者，或换模型/缩短上下文。 | 否 |
| `llm_unreachable` | ai | 模型服务不可达 | 连接失败：连不上模型服务（{detail}） | 检查网络/代理后重试。 | 是 |
| `unexpected_error` | ui | 未预期异常 | 意外错误（{exc_type}）：{detail} | 重试一次；反复出现请把日志（含异常类型与上下文）提交开发者。 | 是 |
| `missing_error_detail` | common | 缺少错误详情 | {context} 未提供错误详情。 | 查看日志，或让上游补上错误码/异常信息。 | 否 |

> 表中 `exception` 一行覆盖 `exception:<原始异常类型>`（如
> `exception:ValueError`）；`playback` 一行覆盖 `playback_<底层码>`。
> 两者都会把异常类型/底层码带进文案，不允许只剩一句「失败」。

## 4. 守卫测试（三条落地方式）

文件：`tests/test_error_catalog_guard.py`。扫描范围：`mbdsdr_ai/` 生产代码
（排除 `tests/`、`__pycache__` 与目录模块自身）。`desktop/` 不在 Python 侧
纪律范围，不扫描。

| 编号 | 约束 | 落地 |
|---|---|---|
| ① | 表内每个码至少被一处代码引用（无死条目） | `test_every_catalog_code_is_used`：对每个码/家族前缀，在所有生产文件中查字面量引用 |
| ② | 代码里出现的码都在表里（无野码） | `test_no_wild_codes`：AST 扫描 `error=`、`error_code=`、`"code":`、`*.error=`、`class code=`、`ERR_*` 常量；只校验小写标识符形状，白名单须写理由 |
| ③ | 面向用户字符串不许出现空话 | `test_no_empty_user_facing_phrases`：扫字符串常量（跳过 docstring），黑名单 `未知错误/未知失败/未知异常/出错了/发生错误` 与整串 `失败`；白名单须写理由 |

另加两条防漂移/防糊弄断言：

- `test_catalog_doc_matches_module`：本表的码集合必须与模块 `ERRORS` 完全一致。
- `test_exception_code_is_traceable` / `test_unknown_code_fallback_is_honest`：
  未登记码的兜底必须带码 + 上下文，exception 家族必须带异常类型。

白名单当前为空。新增白名单必须写明「为什么它不是错误码/为什么这句话不是空话」，
并由 `test_whitelist_entries_have_reasons` 校验理由非空。

## 5. 新增/修改错误码流程

1. 在 `mbdsdr_ai/error_catalog.py` 增加/修改 `ErrorSpec`（码不改，文案可改）。
2. 重新生成本表（保持码集合一致），或在表中同步同一行。
3. 在调用点用 `user_message(code, **fields)` / `suggestion(code)` /
   `retryable(code)`，不再拼中文。
4. 跑 `pytest tests/test_error_catalog_guard.py`：死条目、野码、空话、文档漂移
   任何一条红都说明没收敛干净。

## 6. 本轮收敛记录

- 新建 `mbdsdr_ai/error_catalog.py`（单一来源）与本表。
- 工具层：`tool_registry.py` 的参数校验/缺失参数/异常/空结果全部改为查表；
  `sdr_tools.py` 的「未知错误」兜底改为 `missing_error_detail`。
- 解码层：`decode_router.py` 的 6 个错误码、`gk2a_lrit.py` 的解码失败文案改为查表。
- 录制层：`recorder.py`、`playback.py`、`recording_library.py` 的失败文案改为查表；
  回放状态码 `playback_*` 归入 `playback` 家族。
- TLE 层：`tle_fetch.py` 的失败标签与可重试性改为查表；`unknown` → `tle_unknown`，
  文案明确写出「缺少状态码/异常/响应体」。
- AI 层：`model_manager.py` 的 HTTP/超时/连接/未知异常统一为 `llm_*` 码，
  返回值新增 `error_code`；`llm_unexpected` 明确不可重试（与既有 break 行为一致）。
- 接入层：`sdr_backend.py` 四个 setter 的失败文案改为查表；
  `core/mcp_server.py` 的策略拒绝/人工门/hex 解析改为查表。
- 界面层：`error_handler.format_error` 的「未知错误」标题改为
  `unexpected_error`（必带异常类型与上下文）。
- 面向用户字符串中的「未知错误」已清零（docstring 说明句除外）。

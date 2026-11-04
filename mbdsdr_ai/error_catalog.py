# SPDX-License-Identifier: MIT
"""
MBDSDR AI - 错误码单一来源（面向用户的话术目录）
================================================

本模块是**唯一**的「错误码 → 用户可读一句话 / 下一步建议 / 是否可重试 /
所属层」来源。工具层、接入层、解码层、录制库、TLE、AI 层在构造失败文案时
必须调用 :func:`user_message` / :func:`suggestion` / :func:`retryable`，
不得各自拼中文。

约定
----
- **码**：稳定的 ASCII 标识（如 ``tool_not_found``）。码不随文案改版变化，
  日志/审计/守卫测试都以码为准。
- **exception 家族**：``exception:<原始异常类型>`` 是唯一允许的动态码，
  目录里用 ``exception`` 一行登记，:func:`exception_code` 负责拼装。
- **文案**：``message`` 是给用户/模型看的完整一句话，可含占位符；
  缺失的占位符保留原样，由调用方补齐。``suggestion`` 是下一步动作。
- **可重试**：指「同一条件下再试一次有没有意义」。不可重试的码，
  任何层都不得建议「重试」，只能建议改变输入/环境或换方法。

守卫测试 ``tests/test_error_catalog_guard.py`` 校验：
1. 表内每个码至少被一处生产代码引用（无死条目）；
2. 生产代码里出现的码都登记在表里（无野码）；
3. 面向用户的字符串不出现「未知错误」类空话（白名单须写理由）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

__all__ = [
    "ErrorSpec",
    "ERRORS",
    "FAMILY_PREFIXES",
    "spec",
    "user_message",
    "suggestion",
    "retryable",
    "exception_code",
    "exception_message",
]

# 层级取值：工具 / 接入 / 解码 / 录制 / TLE / AI / 界面
LAYER_TOOL = "tool"
LAYER_ACCESS = "access"
LAYER_DECODE = "decode"
LAYER_RECORDER = "recorder"
LAYER_TLE = "tle"
LAYER_AI = "ai"
LAYER_UI = "ui"
LAYER_COMMON = "common"


@dataclass(frozen=True)
class ErrorSpec:
    """一个错误码的完整语义。"""

    code: str
    layer: str
    retryable: bool
    meaning: str
    message: str
    suggestion: str


#: 码 → 展示用短标签（TLE 失败标签等复用它）。
def _spec(code: str, layer: str, retryable: bool, meaning: str,
          message: str, suggestion: str) -> ErrorSpec:
    return ErrorSpec(code, layer, retryable, meaning, message, suggestion)


_SPECS = (
    # ── 工具层（ToolRegistry / Agent 工具面） ──────────────────────────
    _spec("tool_not_found", LAYER_TOOL, False,
          "工具名未注册",
          "工具 '{tool}' 不存在。可用工具：{available}",
          "用 list_tools 查看全部工具名或按类别检索；确认名称拼写与前缀"
          "（如 sdr_/vna_）后重试。"),
    _spec("tool_unavailable", LAYER_TOOL, False,
          "工具存在但当前不可用",
          "工具 '{tool}' 当前不可用（设备未连接或功能未启用）。",
          "先调用连接/枚举工具（如 sdr_list_devices → sdr_connect，或 "
          "vna_connect）；无硬件时改用返回诚实空态的只读工具，不要虚构数据。"),
    _spec("invalid_arguments_type", LAYER_TOOL, False,
          "调用参数不是 JSON 对象",
          "工具 '{tool}' 的调用参数必须是 JSON 对象，收到 {actual}；未执行该工具。",
          "重新生成键值对形式的 JSON 对象（如 frequency_hz: 145800000）；"
          "不要传数组、字符串或裸标量。"),
    _spec("invalid_arguments", LAYER_TOOL, False,
          "参数内容不合法",
          "工具 '{tool}' 参数不合法：{detail}",
          "按参数说明修正后重试；不要原样重发同一组坏参数。"),
    _spec("missing_required_params", LAYER_TOOL, False,
          "缺少必填参数",
          "工具 '{tool}' 缺少必填参数：{missing}。参数说明：{hints}",
          "按上面的参数说明补齐必填项后重试；可选参数不确定时省略即可，"
          "不要编造取值。"),
    _spec("param_type_mismatch", LAYER_TOOL, False,
          "参数类型不符",
          "参数 '{param}' 需要 {expected}，收到 {actual}；未执行该工具。",
          "按参数类型修正后重试。"),
    _spec("param_out_of_range", LAYER_TOOL, False,
          "参数越界",
          "参数 '{param}' 超出允许范围（{detail}）；未执行该工具。",
          "把参数改到允许范围内后重试。"),
    _spec("param_not_in_enum", LAYER_TOOL, False,
          "参数不在允许取值内",
          "参数 '{param}' 不在允许取值 {allowed} 中；未执行该工具。",
          "从允许取值中选一个后重试；不要编造枚举值。"),
    _spec("invalid_json_arguments", LAYER_TOOL, False,
          "工具调用参数 JSON 非法",
          "工具 '{tool}' 的参数不是合法 JSON，未执行。收到原文（前 200 字符）："
          "{preview}",
          "重新生成完整、闭合的 JSON 对象参数；不要截断字符串，不要用单引号/"
          "尾逗号/Python 字面量。"),
    _spec("handler_error", LAYER_TOOL, False,
          "handler 返回失败",
          "工具 '{tool}' 执行失败：{detail}",
          "按报错修正参数或换工具；不要把失败当成已执行。"),
    _spec("exception", LAYER_TOOL, True,
          "handler 抛异常（exception:<类型>）",
          "工具 '{tool}' 调用异常（{exc_type}）：{detail}",
          "同类参数重试不超过 2 次；仍失败则换方法或向用户说明已失败。"),
    _spec("empty_result_without_reason", LAYER_TOOL, False,
          "成功但无内容且无原因",
          "工具 '{tool}' 返回了空结果且未提供原因（已按失败处理）。",
          "检查输入是否为空/过短，或改用能给出诚实空态原因的工具。"),
    _spec("invalid_rs_shape", LAYER_TOOL, False,
          "RS 矩阵形状错误",
          "参数 '{param}' 的形状不符合要求：{detail}",
          "按说明传入二维 RS 矩阵后重试。"),
    _spec("empty_taps", LAYER_TOOL, False,
          "滤波抽头为空",
          "滤波器抽头为空（taps 列表长度为 0）；未执行。",
          "提供至少一个非零抽头后重试。"),
    _spec("compose_failed", LAYER_TOOL, False,
          "报文组装失败",
          "报文组装失败：{detail}",
          "检查输入字段后重试。"),
    _spec("gimbal_stop_failed", LAYER_TOOL, True,
          "云台停止失败",
          "云台停止指令未执行（{detail}）。",
          "确认云台后端可用后重试。"),
    _spec("no_rssi", LAYER_TOOL, True,
          "未采到 RSSI",
          "本次扫描没有采到任何 RSSI 样本。",
          "检查 SDR 连接与增益后重试。"),
    _spec("empty_signal", LAYER_TOOL, False,
          "输入信号为空",
          "输入信号为空（样本数为 0）；未执行。",
          "提供非空采样后重试。"),
    _spec("subagent_not_found", LAYER_TOOL, False,
          "子代理不存在",
          "子代理 '{name}' 不存在或已结束。",
          "先创建/列出子代理，再用其 ID 调用。"),
    _spec("recording_not_found", LAYER_TOOL, False,
          "录像或模板不存在",
          "记录/模板 '{name}' 不存在。",
          "用列表工具确认名称后重试。"),
    _spec("workflow_executor_missing", LAYER_TOOL, False,
          "工作流执行器未接线",
          "工作流执行器未设置，无法执行工具步骤。",
          "先调用 set_tool_executor() 完成接线。"),
    _spec("NEED_HW", LAYER_TOOL, False,
          "需要真实硬件",
          "本操作需要真实硬件（{detail}），当前为无硬件/离线环境。",
          "接入对应设备后重试；无硬件时改用只读/模拟能力并明确标注。"),
    _spec("unknown_tool", LAYER_TOOL, False,
          "工具名未知",
          "未知工具 '{tool}'。",
          "用 list_tools 确认工具名后重试。"),
    _spec("device_unavailable", LAYER_TOOL, False,
          "设备不可用",
          "设备不可用（{detail}）。",
          "检查连接与驱动，或换一个可用设备。"),
    _spec("ptt_not_on", LAYER_ACCESS, False,
          "PTT 未打开",
          "PTT 未打开，发射未执行。",
          "确认 PTT 控制可用后再发射。"),
    _spec("missing_iq", LAYER_DECODE, False,
          "缺少 IQ 输入",
          "缺少 iq_base64 输入，无法解调。",
          "提供 iq_base64 后重试。"),
    _spec("not_connected", LAYER_TOOL, False,
          "未连接设备",
          "尚未连接设备，无法执行 {action}。",
          "先调用连接工具，连接成功后再执行。"),
    _spec("capability_unknown", LAYER_TOOL, False,
          "设备能力未知",
          "设备能力未声明，无法判断是否支持 {capability}。",
          "改用已声明能力的设备，或先探测能力再重试。"),
    _spec("offline_or_api_error", LAYER_TOOL, False,
          "联网接口失败或离线",
          "联网接口未返回数据（{detail}）。",
          "联网后重试；离线时改用本地数据并标注来源。"),

    # ── 解码层 ─────────────────────────────────────────────────────────
    _spec("unknown_mode", LAYER_DECODE, False,
          "未知数字模式",
          "未知数字模式 '{mode}'；可用模式：{available}",
          "从可用模式中选择后重试。"),
    _spec("missing_dependency", LAYER_DECODE, False,
          "缺少内置依赖模块",
          "缺少内置依赖模块 {module}（实现文件 {impl} 不可导入）。",
          "安装/恢复该依赖模块后重试。"),
    _spec("missing_sample", LAYER_DECODE, False,
          "缺少输入样本",
          "输入样本不存在或为空：{path}（这是缺样本，不是解码失败）。",
          "先录制或指定存在的样本文件后重试。"),
    _spec("unsupported_format", LAYER_DECODE, False,
          "输入格式不支持",
          "{mode} 需要 {allowed}，收到 {actual}。",
          "换用支持的格式，或先转换文件后重试。"),
    _spec("decode_error", LAYER_DECODE, False,
          "解码失败",
          "解码失败：{detail}",
          "检查样本完整性与参数；同一输入原样重试无用。"),
    _spec("not_implemented", LAYER_DECODE, False,
          "能力未接线",
          "{mode} 的通用入口未接线（实现文件 {impl}）。",
          "改用已接线入口或外部工具；不要宣称已完成解码。"),
    _spec("invalid_vcdu", LAYER_DECODE, False,
          "VCDU 帧非法",
          "VCDU 帧校验/长度不合法：{detail}",
          "确认输入为完整 CADU/VCDU 帧后重试。"),
    _spec("decode_failed", LAYER_DECODE, True,
          "无线电解码失败",
          "解码失败（{detail}）；可能是信号质量不足。",
          "改善天线/增益/频率后重试。"),
    _spec("unknown_device", LAYER_DECODE, False,
          "未知设备",
          "未知设备 '{device}'。",
          "从支持的设备列表中选择后重试。"),
    _spec("unknown_device_type", LAYER_DECODE, False,
          "未知设备类型",
          "未知设备类型 '{device}'。",
          "从支持的设备类型中选择后重试。"),
    _spec("empty_input", LAYER_DECODE, False,
          "输入为空",
          "输入为空：{detail}",
          "提供非空输入后重试。"),
    _spec("bad_hex", LAYER_DECODE, False,
          "十六进制输入非法",
          "十六进制输入不合法：{detail}",
          "确认只含 0-9/a-f 且长度为偶数后重试。"),
    _spec("doppler_failed", LAYER_DECODE, True,
          "多普勒补偿失败",
          "多普勒补偿计算失败：{detail}",
          "确认 TLE、时刻与频点有效后重试。"),
    _spec("ogn_decode_failed", LAYER_DECODE, True,
          "OGN 报文解码失败",
          "OGN 报文解码失败：{detail}",
          "检查信号质量与增益后重试。"),
    _spec("schedule_failed", LAYER_DECODE, False,
          "云图排程失败",
          "排程失败：{detail}",
          "检查时间与卫星参数后重试。"),
    _spec("meta_failed", LAYER_DECODE, False,
          "云图元数据获取失败",
          "元数据获取失败：{detail}",
          "检查产品编号后重试。"),
    # ── 接入层（SDR 后端 / 安全门） ────────────────────────────────────
    _spec("bad_arguments", LAYER_ACCESS, False,
          "客户端参数校验失败",
          "参数校验失败：{detail}",
          "按参数说明修正后重试。"),
    _spec("bad_command", LAYER_ACCESS, False,
          "指令不合法",
          "指令不合法：{detail}",
          "从支持的指令列表中选择后重试。"),
    _spec("bad_config", LAYER_ACCESS, False,
          "配置不合法",
          "配置不合法：{detail}",
          "修正配置项后重试。"),
    _spec("bad_json", LAYER_ACCESS, False,
          "JSON 解析失败",
          "JSON 解析失败：{detail}",
          "确认输入为合法 JSON 后重试。"),
    _spec("config_not_found", LAYER_ACCESS, False,
          "配置文件不存在",
          "配置文件不存在：{detail}",
          "先创建配置或确认路径后重试。"),
    _spec("unknown_server", LAYER_ACCESS, False,
          "未知服务器",
          "未知服务器 '{detail}'。",
          "从已配置服务器列表中选择。"),
    _spec("unknown_command", LAYER_ACCESS, False,
          "未知指令",
          "未知指令 '{detail}'。",
          "从支持的指令列表中选择。"),
    _spec("unsupported", LAYER_ACCESS, False,
          "不支持的操作",
          "不支持的操作：{detail}",
          "改用受支持的操作。"),
    _spec("identity_mismatch", LAYER_ACCESS, False,
          "站点身份不匹配",
          "站点身份不匹配：{detail}",
          "确认站点 ID 与数据库一致后重试。"),
    _spec("station_untrusted", LAYER_ACCESS, False,
          "站点不在可信列表",
          "站点不在可信列表（{detail}）。",
          "先把该站点加入可信列表，或换用可信站点。"),
    _spec("no_pass", LAYER_ACCESS, False,
          "当前无过境",
          "当前时间窗内没有可用过境：{detail}",
          "扩大时间窗或换卫星后重试。"),
    _spec("no_downlink_known", LAYER_ACCESS, False,
          "无已知下行频点",
          "没有该卫星的已知下行频点：{detail}",
          "先补全频点库或显式指定频率。"),
    _spec("sdr_error", LAYER_ACCESS, False,
          "SDR 通用错误",
          "SDR 错误：{detail}",
          "按错误详情处理；必要时重启程序。"),
    _spec("driver_not_found", LAYER_ACCESS, False,
          "找不到 SDR 驱动",
          "找不到 SDR 驱动：{detail}",
          "安装驱动/DLL 后重启程序。"),
    _spec("device_not_found", LAYER_ACCESS, False,
          "找不到设备",
          "找不到 SDR 设备：{detail}",
          "检查 USB/驱动后重新枚举。"),
    _spec("device_busy", LAYER_ACCESS, True,
          "设备被占用",
          "设备被其它进程占用：{detail}",
          "关闭占用进程后重试；不要并发打开同一设备。"),
    _spec("usb_disconnected", LAYER_ACCESS, True,
          "USB 已断开",
          "USB 设备已断开：{detail}",
          "重新插拔设备并重新连接。"),
    _spec("no_audio_output", LAYER_ACCESS, False,
          "没有可用音频输出",
          "没有可用音频输出设备：{detail}",
          "检查系统音频设备后重试。"),
    _spec("sample_rate_not_supported", LAYER_ACCESS, False,
          "采样率不支持",
          "设备不支持采样率 {rate} Hz：{detail}",
          "改用设备支持的采样率。"),
    _spec("frequency_out_of_range", LAYER_ACCESS, False,
          "频率超出范围",
          "频率 {freq} Hz 超出设备范围：{detail}",
          "改用设备可调范围内的频率。"),
    _spec("invalid_argument", LAYER_ACCESS, False,
          "参数校验失败",
          "参数校验失败：{detail}",
          "按参数说明修正后重试（本库不做静默钳位）。"),
    _spec("export_path_out_of_root", LAYER_ACCESS, False,
          "导出路径越界",
          "导出路径越界，拒绝写入：{detail}",
          "把路径改到允许根目录内，或先设置 MBDSDR_EXPORT_ROOT。"),
    _spec("decoder_not_available", LAYER_ACCESS, False,
          "解码器不可用",
          "解码器不可用：{detail}",
          "安装对应解码依赖后重试。"),
    _spec("not_available", LAYER_ACCESS, False,
          "能力不可用",
          "能力不可用：{detail}",
          "接入所需后端后重试。"),
    _spec("spill_write_failed", LAYER_ACCESS, True,
          "溢出数据落盘失败",
          "大字段落盘失败：{detail}",
          "检查磁盘/临时目录后重试。"),
    _spec("sdr_set_frequency_failed", LAYER_ACCESS, True,
          "设置频率失败",
          "设置频率失败（{detail}），已回滚到原频率。",
          "确认频率在设备可调范围内后重试。"),
    _spec("sdr_set_sample_rate_failed", LAYER_ACCESS, True,
          "设置采样率失败",
          "设置采样率失败（{detail}），已回滚到原采样率。",
          "确认采样率被设备支持后重试。"),
    _spec("sdr_set_gain_failed", LAYER_ACCESS, True,
          "设置增益失败",
          "设置增益失败（{detail}），已回滚到原增益。",
          "确认增益在设备范围内后重试。"),
    _spec("sdr_set_bandwidth_failed", LAYER_ACCESS, True,
          "设置带宽失败",
          "设置带宽失败（{detail}），已回滚到原带宽。",
          "确认带宽被设备支持后重试。"),
    _spec("denied_by_policy", LAYER_ACCESS, False,
          "策略拒绝执行",
          "denied_by_policy：策略拒绝执行 {tool}（规则集不可被 armed 覆盖）。",
          "换用允许的工具或修改策略；不要重复尝试同一个被拒调用。"),
    _spec("manual_gate_required", LAYER_ACCESS, False,
          "需要人工门确认",
          "手动模式：未执行 {tool}",
          "带 arm_manual=true 执行；或 approve_session=true 让本会话免问。"),
    _spec("internal_error", LAYER_ACCESS, True,
          "内部异常",
          "内部错误（{exc_type}）：{detail}",
          "重试一次；仍失败请收集日志（含异常类型与上下文）反馈开发者。"),

    # ── 录制 / 回放层 ─────────────────────────────────────────────────
    _spec("disk_full", LAYER_RECORDER, False,
          "磁盘空间不足",
          "录制失败：磁盘空间不足（{detail}）。",
          "清理磁盘或换目标目录后重新录制。"),
    _spec("not_writable", LAYER_RECORDER, False,
          "目标不可写",
          "录制失败：目标目录不可写（{detail}）。",
          "检查权限或只读挂载后重试。"),
    _spec("file_missing", LAYER_RECORDER, False,
          "录音文件不存在",
          "录音文件不存在：{detail}",
          "刷新录音库或确认文件路径后重试。"),
    _spec("bad_field", LAYER_RECORDER, False,
          "字段不合法",
          "字段不合法：{detail}",
          "按字段说明修正后重试。"),
    _spec("bad_param", LAYER_RECORDER, False,
          "参数不合法（录音库）",
          "参数不合法：{detail}",
          "修正参数后重试。"),
    _spec("entry_not_found", LAYER_RECORDER, False,
          "条目不存在",
          "录音条目不存在：{detail}",
          "刷新列表后用有效条目 ID 重试。"),
    _spec("dest_missing", LAYER_RECORDER, False,
          "目标不存在",
          "目标不存在：{detail}",
          "确认目标路径存在后重试。"),
    _spec("not_found", LAYER_RECORDER, False,
          "索引不存在",
          "录音库索引不存在：{detail}",
          "先初始化/扫描录音目录后重试。"),
    _spec("library_not_found", LAYER_RECORDER, False,
          "新目录无索引",
          "新目录下没有索引：{detail}",
          "先在新目录完成扫描，再迁移/切换。"),
    _spec("library_busy", LAYER_RECORDER, True,
          "录音库被占用",
          "录音库正被另一进程占用（{detail}）",
          "等对方释放或关闭另一个实例后重试。"),
    _spec("unsupported_kind", LAYER_RECORDER, False,
          "记录类型不支持",
          "记录类型不支持：{detail}",
          "换用支持的类型后重试。"),
    _spec("io", LAYER_RECORDER, True,
          "文件读写失败",
          "录制文件读写失败（{detail}）。",
          "检查磁盘与路径后重试。"),
    _spec("io_error", LAYER_RECORDER, True,
          "录音库操作失败",
          "录音库操作失败：{detail}",
          "检查路径、权限与索引后重试。"),
    _spec("wav_error", LAYER_RECORDER, False,
          "WAV 写入失败",
          "WAV 写入失败（{detail}）。",
          "检查音频参数与磁盘后重试。"),
    _spec("missing", LAYER_RECORDER, False,
          "文件不存在",
          "找不到数据文件：{path}",
          "确认路径存在后重试。"),
    _spec("corrupt", LAYER_RECORDER, False,
          "文件损坏",
          "文件损坏：{detail}",
          "换一个完整的录制文件。"),
    _spec("truncated", LAYER_RECORDER, False,
          "样本被截断",
          "记录已截断：{detail}",
          "重新录制完整文件。"),
    _spec("empty", LAYER_RECORDER, False,
          "文件为空",
          "数据文件为空（0 字节）。",
          "重新录制后再回放。"),
    _spec("datatype", LAYER_RECORDER, False,
          "SigMF 数据类型不支持",
          "SigMF 数据类型不支持：{detail}",
          "使用 cf32/complex64 录制。"),
    _spec("deleted", LAYER_RECORDER, False,
          "回放文件被删除",
          "回放文件在读取过程中被删除或移走。",
          "恢复文件或改用其他文件。"),
    _spec("eof", LAYER_RECORDER, False,
          "读到文件末尾",
          "已读到文件末尾（未循环）。",
          "开启 loop 或重新开始回放。"),
    _spec("open_failed", LAYER_RECORDER, True,
          "回放文件打开失败",
          "回放文件打开失败：{detail}",
          "确认数据文件与 meta 完整后重试。"),
    _spec("no_data", LAYER_RECORDER, False,
          "回放源无更多数据",
          "回放源无更多数据：{detail}",
          "检查文件状态或开启循环。"),
    _spec("rate_unknown", LAYER_RECORDER, False,
          "采样率未知",
          "录制文件未声明采样率，拒绝猜测改写。",
          "补全 SigMF 元数据后重试。"),
    _spec("rate_mismatch", LAYER_RECORDER, False,
          "采样率不匹配",
          "回放源采样率固定为 {actual} Hz，不能改为 {requested} Hz。",
          "使用录制时的采样率。"),
    _spec("playback", LAYER_RECORDER, False,
          "回放打开失败（playback:<底层码>）",
          "回放文件无法打开：{reason}",
          "确认录制文件与 SigMF 元数据完整后重试。"),
    _spec("playback_failed", LAYER_RECORDER, True,
          "回放失败",
          "回放失败：{detail}",
          "确认文件完整后重试。"),
    _spec("bad_parameter", LAYER_RECORDER, False,
          "参数不合法",
          "参数不合法：{detail}",
          "修正参数后重试。"),
    _spec("tx_source_deleted", LAYER_RECORDER, False,
          "发射源被删除",
          "发射源文件在发射过程中被删除。",
          "恢复文件后重新发射。"),
    _spec("tx_source_failed", LAYER_RECORDER, False,
          "发射源失效",
          "发射源在发射过程中失效：{detail}",
          "检查文件与设备后重新发射。"),

    # ── TLE 层 ────────────────────────────────────────────────────────
    _spec("rate_limited", LAYER_TLE, True,
          "429 限流",
          "TLE 源限流（429）：{detail}",
          "{retry_in} 秒后再试。"),
    _spec("http_4xx", LAYER_TLE, False,
          "4xx 拒绝",
          "TLE 源拒绝请求（4xx）：{detail}",
          "确认卫星编号与 URL；不要立即重试。"),
    _spec("http_5xx", LAYER_TLE, True,
          "5xx 服务端错误",
          "TLE 源服务端错误（5xx）：{detail}",
          "按退避时间重试。"),
    _spec("network_timeout", LAYER_TLE, True,
          "网络超时",
          "TLE 源请求超时：{detail}",
          "稍后重试；持续超时则换网络或换源。"),
    _spec("network_unreachable", LAYER_TLE, True,
          "网络不可达/DNS 失败",
          "TLE 源不可达（{detail}）",
          "检查网络/代理/DNS 后重试。"),
    _spec("network_other", LAYER_TLE, True,
          "其他网络错误",
          "TLE 源网络错误：{detail}",
          "稍后重试；持续失败换源。"),
    _spec("parse_failure", LAYER_TLE, True,
          "返回内容解析失败",
          "TLE 返回内容不含合法两行根数：{detail}",
          "重试一次；持续失败换源。"),
    _spec("empty_response", LAYER_TLE, True,
          "空响应",
          "TLE 源返回空响应。",
          "稍后重试或换源。"),
    _spec("tle_unknown", LAYER_TLE, False,
          "失败缺少上下文",
          "TLE 刷新失败，但未提供状态码/异常/响应体"
          "（调用方缺少上下文）。",
          "检查抓取链路日志，补上失败上下文后再判断。"),

    # ── AI 层 ─────────────────────────────────────────────────────────
    _spec("api_key_not_set", LAYER_AI, False,
          "未设置模型 API Key",
          "未设置模型 API Key（{detail}），无法调用模型。",
          "在配置中填写 api_key 后重试。"),
    _spec("agent_busy", LAYER_AI, True,
          "Agent 正忙",
          "Agent 正在处理另一请求，本次未执行。",
          "等当前请求结束或取消后重试。"),
    _spec("cancelled", LAYER_AI, False,
          "请求已取消",
          "请求已取消。",
          "需要时重新发起。"),
    _spec("llm_offline", LAYER_AI, False,
          "离线模式拒绝联网",
          "离线模式（MBDSDR_OFFLINE）：模型 API 需要联网，已拒绝调用"
          "（本地无模型可替代）",
          "关闭离线模式并联网后重试；或改用本地能力。"),
    _spec("llm_rate_limited", LAYER_AI, True,
          "模型接口限流",
          "模型接口限流（429）。",
          "{retry_in} 秒后重试。"),
    _spec("llm_http_4xx", LAYER_AI, False,
          "模型请求被拒绝",
          "模型接口返回 HTTP {status}：{detail}",
          "检查 API Key、模型名与请求参数；不要原样重试。"),
    _spec("llm_http_5xx", LAYER_AI, True,
          "模型服务端错误",
          "模型接口返回 HTTP {status}：{detail}",
          "退避后重试。"),
    _spec("llm_timeout", LAYER_AI, True,
          "模型请求超时",
          "模型请求超时（{timeout} 秒）。",
          "稍后重试或缩短上下文。"),
    _spec("llm_unreachable", LAYER_AI, True,
          "模型服务不可达",
          "连接失败：连不上模型服务（{detail}）",
          "检查网络/代理后重试。"),
    _spec("llm_unexpected", LAYER_AI, False,
          "模型调用异常",
          "模型调用异常（{exc_type}）：{detail}",
          "不要自动重试；把异常类型与请求信息反馈开发者，或换模型/缩短上下文。"),

    # ── 界面/通用 ─────────────────────────────────────────────────────
    _spec("unexpected_error", LAYER_UI, True,
          "未预期异常",
          "意外错误（{exc_type}）：{detail}",
          "重试一次；反复出现请把日志（含异常类型与上下文）提交开发者。"),
    _spec("missing_error_detail", LAYER_COMMON, False,
          "缺少错误详情",
          "{context} 未提供错误详情。",
          "查看日志，或让上游补上错误码/异常信息。"),
)

ERRORS: Dict[str, ErrorSpec] = {s.code: s for s in _SPECS}

#: 动态码家族：目录行 → 实际发出的前缀。exception 家族形如
#: ``exception: ValueError``，守卫测试按前缀放行。
FAMILY_PREFIXES: Mapping[str, str] = {
    "exception": "exception:",
    "playback": "playback_",
}


class _SafeDict(dict):
    """缺失占位符保留 ``{name}``，不抛 KeyError。"""

    def __missing__(self, key: str) -> str:  # type: ignore[override]
        return "{" + key + "}"


def spec(code: str) -> ErrorSpec:
    """取错误码语义；未登记的码按「缺上下文」诚实降级。"""
    found = ERRORS.get(code)
    if found is not None:
        return found
    for family, prefix in FAMILY_PREFIXES.items():
        if code.startswith(prefix):
            return ERRORS[family]
    return _spec(
        code or "missing_error_detail", LAYER_COMMON, False,
        "未登记的错误码",
        "未登记的错误码 '{code}'（请把该码反馈给开发者）；上下文：{context}",
        "不要据此猜测原因；把错误码与上下文交给开发者核查。",
    )


def user_message(code: str, **fields: Any) -> str:
    """按码生成用户可读的一句话；未知码退化为带上下文的自描述。"""
    fields.setdefault("code", code)
    return spec(code).message.format_map(_SafeDict(fields))


def suggestion(code: str, **fields: Any) -> str:
    """按码生成下一步建议。"""
    return spec(code).suggestion.format_map(_SafeDict(fields))


def retryable(code: str) -> bool:
    """同一条件下再试一次是否有意义。"""
    return spec(code).retryable


def exception_code(exc: BaseException) -> str:
    """异常 → 动态码 ``exception:<类型>``。"""
    return FAMILY_PREFIXES["exception"] + type(exc).__name__


def exception_message(tool: str, exc: BaseException, context: str = "") -> str:
    """异常 → 面向用户的失败文案（必带异常类型与最小上下文）。"""
    detail = f"{exc}" if str(exc) else "（异常无文本描述）"
    if context:
        detail = f"{detail}；上下文: {context}"
    return user_message("exception", tool=tool, exc_type=type(exc).__name__,
                        detail=detail)

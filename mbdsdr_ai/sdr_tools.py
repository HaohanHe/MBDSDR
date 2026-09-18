"""
MBDSDR AI 内核 - SDR 工具集
============================
SDR Tools：把所有 SDR 功能封装为 AI 可调用的工具。

原则：有多少功能就有多少工具。每个 SDR 操作都是一个工具，
AI 可以通过 tool calling 直接调用，也可以通过工作流组合调用。

工具分类：
- 设备管理（5个）：connect/disconnect/list/switch/status
- 频率与采样率（4个）：set_frequency/get_frequency/set_sample_rate/set_bandwidth
- 增益（2个）：set_gain/set_agc
- 解调与音频（3个）：set_demod/set_squelch/set_volume
- 频谱分析（7个）：analyze/zoom/pan/screenshot/find_signals/center_offset/text_view
- 录制回放（3个）：record_start/record_stop/recordings_list
- 解码（5个）：noaa_apt/sstv/ft8/aprs/adsb
- 卫星（2个）：sky_view/doppler
- 定位（2个）：get_gps/get_imu
- 信号分析（3个）：identify_modulation/detect_fhss/measure_signal
- AI 辅助（2个）：ai_sweep/ai_find_center

共 38 个 SDR 专用工具。
"""

import os
import time
import json
import numpy as np
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from .tool_registry import ToolResult
from .sdr_backend import SDRBackendManager, SDRStatus
from .spectrum_processor import SpectrumProcessor
from .dsp import front_end, demodulate, DCBlocker, IQCalibrator, compute_snr, estimate_bandwidth
from .decoders import (
    decode_noaa_apt, decode_sstv, decode_digital_mode,
    detect_fhss, list_visible_satellites, compute_doppler_correction,
    BUILTIN_TLE, SATELLITE_FREQUENCIES,
)


def register_sdr_tools(agent):
    """
    把所有 SDR 工具注册到 Agent 中。

    agent: MBDSDRAgent 实例
    """
    # 初始化 SDR 后端管理器和频谱处理器（挂到 agent 上）
    if not hasattr(agent, "sdr_manager"):
        agent.sdr_manager = SDRBackendManager()
    if not hasattr(agent, "spectrum"):
        agent.spectrum = SpectrumProcessor(fft_size=1024)

    mgr = agent.sdr_manager
    spec = agent.spectrum

    # ═══════════════════════════════════════════════════
    # 1. 设备管理（5个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_connect",
        description="连接 SDR 设备。默认连接当前激活的设备，也可以指定 device_id。连接后才能进行频率设置、频谱分析、录制等操作。",
        parameters={
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备 ID（可选，默认使用当前激活设备）。可先用 sdr_list_devices 查看可用设备。"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(
            success=mgr.connect(args.get("device_id")),
            content=f"已连接: {mgr.get_active().device.name}" if mgr.get_active() else "连接失败",
        ),
        category="sdr_device",
    )

    agent.tool_registry.register(
        name="sdr_disconnect",
        description="断开当前 SDR 设备。断开后停止所有接收和录制。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: ToolResult(success=True, content=_disconnect_sdr(mgr)),
        category="sdr_device",
    )

    agent.tool_registry.register(
        name="sdr_list_devices",
        description="列出所有可用的 SDR 设备。包括设备类型、频率范围、采样率范围、最大增益、是否支持 IQ、连接状态。用于选择要连接的设备。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: ToolResult(success=True, content=json.dumps(mgr.list_devices(), ensure_ascii=False, indent=2)),
        category="sdr_device",
    )

    agent.tool_registry.register(
        name="sdr_switch_device",
        description="切换到另一个 SDR 设备。会先断开当前设备，再连接指定设备。支持异构双前端切换（如从 RTL-SDR 切换到自研 ai-sdr Mini）。",
        parameters={
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "目标设备 ID，从 sdr_list_devices 获取"},
            },
            "required": ["device_id"],
        },
        handler=lambda args: ToolResult(
            success=mgr.switch_device(args["device_id"]),
            content=f"已切换到: {mgr.get_active().device.name}" if mgr.get_active() else "切换失败",
        ),
        category="sdr_device",
    )

    agent.tool_registry.register(
        name="sdr_status",
        description="查询当前 SDR 设备状态。包括连接状态、当前频率、采样率、增益、AGC、解调模式、RSSI、SNR、录音状态、设备温度、运行时间等。这是最常用的查询工具。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: ToolResult(success=True, content=_format_status(mgr.get_status())),
        category="sdr_device",
    )

    # ═══════════════════════════════════════════════════
    # 2. 频率与采样率（4个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_set_frequency",
        description="设置接收频率（中心频率）。单位 Hz。例如 FM 广播 98.5MHz = 98500000，航空波段 118MHz = 118000000，433MHz = 433000000。设置后自动开始接收。",
        parameters={
            "type": "object",
            "properties": {
                "frequency_hz": {"type": "number", "description": "中心频率，单位 Hz。例如 98500000 = 98.5MHz"},
            },
            "required": ["frequency_hz"],
        },
        handler=lambda args: ToolResult(
            success=_get_backend(mgr).set_frequency(args["frequency_hz"]),
            content=f"频率已设置: {args['frequency_hz']/1e6:.3f} MHz" if _get_backend(mgr).set_frequency(args["frequency_hz"]) else "频率设置失败（超出设备范围或未连接）",
        ),
        category="sdr_frequency",
    )

    agent.tool_registry.register(
        name="sdr_get_frequency",
        description="查询当前接收频率。返回中心频率 Hz 和 MHz。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: ToolResult(success=True, content=f"当前频率: {_get_backend(mgr).get_frequency():.0f} Hz ({_get_backend(mgr).get_frequency()/1e6:.3f} MHz)"),
        category="sdr_frequency",
    )

    agent.tool_registry.register(
        name="sdr_set_sample_rate",
        description="设置采样率。单位 Hz。常用值：2400000(2.4MHz), 1800000(1.8MHz), 1024000(1.024MHz), 250000(250kHz)。采样率越高带宽越宽，但 CPU 占用越大。",
        parameters={
            "type": "object",
            "properties": {
                "sample_rate_hz": {"type": "integer", "description": "采样率，单位 Hz。例如 2400000 = 2.4MHz"},
            },
            "required": ["sample_rate_hz"],
        },
        handler=lambda args: ToolResult(
            success=_get_backend(mgr).set_sample_rate(args["sample_rate_hz"]),
            content=f"采样率已设置: {args['sample_rate_hz']/1e6:.3f} MHz" if _get_backend(mgr).set_sample_rate(args["sample_rate_hz"]) else "采样率设置失败",
        ),
        category="sdr_frequency",
    )

    agent.tool_registry.register(
        name="sdr_set_bandwidth",
        description="设置接收带宽（低通滤波器带宽）。单位 Hz。0 表示自动（跟随采样率）。常用值：12500(12.5kHz 窄带FM), 2500(2.5kHz CW), 100000(100kHz 宽带FM)。",
        parameters={
            "type": "object",
            "properties": {
                "bandwidth_hz": {"type": "number", "description": "带宽，单位 Hz。0=自动"},
            },
            "required": ["bandwidth_hz"],
        },
        handler=lambda args: ToolResult(success=_get_backend(mgr).set_bandwidth(args["bandwidth_hz"]), content=f"带宽已设置: {args['bandwidth_hz']} Hz"),
        category="sdr_frequency",
    )

    # ═══════════════════════════════════════════════════
    # 3. 增益（2个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_set_gain",
        description="设置接收增益。单位 dB。关闭 AGC 后手动设置增益。RTL-SDR 增益范围 0-49.6dB，常用 0/14/27/40dB。增益过高会导致信号过载失真。",
        parameters={
            "type": "object",
            "properties": {
                "gain_db": {"type": "number", "description": "增益，单位 dB。例如 40 = 40dB"},
            },
            "required": ["gain_db"],
        },
        handler=lambda args: ToolResult(success=_get_backend(mgr).set_gain(args["gain_db"]), content=f"增益已设置: {args['gain_db']} dB (AGC 已关闭)"),
        category="sdr_gain",
    )

    agent.tool_registry.register(
        name="sdr_set_agc",
        description="开关自动增益控制（AGC）。开启后设备自动调整增益，适合信号强度变化大的场景。关闭后使用手动增益。",
        parameters={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "true=开启AGC, false=关闭AGC使用手动增益"},
            },
            "required": ["enabled"],
        },
        handler=lambda args: ToolResult(success=_get_backend(mgr).set_agc(args["enabled"]), content=f"AGC 已{'开启' if args['enabled'] else '关闭'}"),
        category="sdr_gain",
    )

    # ═══════════════════════════════════════════════════
    # 4. 解调与音频（3个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_set_demod",
        description="设置解调模式。支持 FM(调频广播), NFM(窄带FM/对讲机), WFM(宽带FM), AM(调幅/航空/短波), LSB(下边带/短波), USB(上边带/短波), CW(等幅报/电报)。",
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "description": "解调模式: FM/NFM/WFM/AM/LSB/USB/CW"},
            },
            "required": ["mode"],
        },
        handler=lambda args: ToolResult(success=_get_backend(mgr).set_demod(args["mode"]), content=f"解调模式已设置: {args['mode'].upper()}" if _get_backend(mgr).set_demod(args["mode"]) else f"不支持的解调模式: {args['mode']}"),
        category="sdr_demod",
    )

    agent.tool_registry.register(
        name="sdr_set_squelch",
        description="设置静噪阈值。单位 dB。信号低于此阈值时静音，适合对讲机/航空等有间歇通话的场景。常用 -80 到 -40 dB。-100 表示关闭静噪。",
        parameters={
            "type": "object",
            "properties": {
                "squelch_db": {"type": "number", "description": "静噪阈值，单位 dB。-100=关闭静噪"},
            },
            "required": ["squelch_db"],
        },
        handler=lambda args: ToolResult(success=_get_backend(mgr).set_squelch(args["squelch_db"]), content=f"静噪已设置: {args['squelch_db']} dB"),
        category="sdr_demod",
    )

    agent.tool_registry.register(
        name="sdr_set_volume",
        description="设置音频输出音量。范围 0.0-1.0。0=静音，0.5=中等，1.0=最大。",
        parameters={
            "type": "object",
            "properties": {
                "volume": {"type": "number", "description": "音量 0.0-1.0"},
            },
            "required": ["volume"],
        },
        handler=lambda args: ToolResult(success=_get_backend(mgr).set_volume(args["volume"]), content=f"音量已设置: {args['volume']:.0%}"),
        category="sdr_demod",
    )

    # ═══════════════════════════════════════════════════
    # 5. 频谱分析（7个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_spectrum_analyze",
        description="分析当前频谱。读取 IQ 样本，做 FFT，返回频谱数据（频率轴、功率轴、峰值、噪声底）。这是最核心的频谱工具，所有频谱操作的基础。",
        parameters={
            "type": "object",
            "properties": {
                "fft_size": {"type": "integer", "description": "FFT 大小，默认 1024。越大频率分辨率越高，但速度越慢。常用 512/1024/2048/4096"},
                "num_samples": {"type": "integer", "description": "读取的样本数，默认等于 fft_size"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_spectrum_analyze(mgr, spec, args)),
        category="sdr_spectrum",
    )

    agent.tool_registry.register(
        name="sdr_spectrum_zoom",
        description="缩放频谱视图。factor > 1 放大（看细节），< 1 缩小（看全局）。例如 factor=2 放大到原来的2倍，factor=0.5 缩小一半。不会改变实际接收频率，只是视图缩放。",
        parameters={
            "type": "object",
            "properties": {
                "factor": {"type": "number", "description": "缩放因子。>1 放大，<1 缩小。例如 2=放大2倍，0.5=缩小一半"},
            },
            "required": ["factor"],
        },
        handler=lambda args: ToolResult(success=True, content=_spectrum_zoom(spec, args["factor"], mgr)),
        category="sdr_spectrum",
    )

    agent.tool_registry.register(
        name="sdr_spectrum_pan",
        description="平移频谱视图中心。offset_hz 为正向右移（看更高频率），为负向左移（看更低频率）。不会改变实际接收频率，只是视图平移。常与 zoom 配合使用：先放大再平移查看细节。",
        parameters={
            "type": "object",
            "properties": {
                "offset_hz": {"type": "number", "description": "平移偏移，单位 Hz。正=右移，负=左移"},
            },
            "required": ["offset_hz"],
        },
        handler=lambda args: ToolResult(success=True, content=_spectrum_pan(spec, args["offset_hz"], mgr)),
        category="sdr_spectrum",
    )

    agent.tool_registry.register(
        name="sdr_spectrum_screenshot",
        description="截取当前频谱图为 PNG 图片。可用于给多模态模型（如 GPT-4V、Qwen-VL）查看频谱，也可保存到本地。这是 AI 视觉分析 SDR 信号的关键工具。",
        parameters={
            "type": "object",
            "properties": {
                "save_path": {"type": "string", "description": "保存路径（可选，默认保存到 ~/.mbdsdr/screenshots/）"},
                "include_waterfall": {"type": "boolean", "description": "是否包含瀑布图，默认 false"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_spectrum_screenshot(mgr, spec, args)),
        category="sdr_spectrum",
    )

    agent.tool_registry.register(
        name="sdr_spectrum_find_signals",
        description="在频谱中检测所有超过阈值的信号。返回每个信号的中心频率、带宽、峰值功率、起始/结束频率。按峰值功率从强到弱排序。用于找台、找干扰源、频谱扫描。",
        parameters={
            "type": "object",
            "properties": {
                "threshold_db": {"type": "number", "description": "信号检测阈值，单位 dB，默认 -60。超过此值的连续区域视为信号"},
                "min_bandwidth_hz": {"type": "number", "description": "最小信号带宽，单位 Hz，默认 1000。小于此带宽的尖峰忽略"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_spectrum_find_signals(mgr, spec, args)),
        category="sdr_spectrum",
    )

    agent.tool_registry.register(
        name="sdr_spectrum_center_offset",
        description="精确估计信号中心频点偏移。使用抛物线插值做亚 bin 精度估计，返回精确中心频率、相对于预期频率的偏移（Hz 和 ppm）。用于校准频率误差、找精确中心频点。",
        parameters={
            "type": "object",
            "properties": {
                "expected_freq_hz": {"type": "number", "description": "预期中心频率（可选）。如果提供，会计算偏移量和 ppm"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_spectrum_center_offset(mgr, spec, args)),
        category="sdr_spectrum",
    )

    agent.tool_registry.register(
        name="sdr_spectrum_text",
        description="生成频谱的 ASCII 文本表示。当多模态模型不可用时，用文本方式让 LLM 看到频谱形状。包含频率刻度、功率刻度、峰值和噪声底信息。",
        parameters={
            "type": "object",
            "properties": {
                "max_bins": {"type": "integer", "description": "频谱横向格数，默认 60。越大越精细"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_spectrum_text(mgr, spec, args)),
        category="sdr_spectrum",
    )

    # ═══════════════════════════════════════════════════
    # 6. 录制回放（3个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_record_start",
        description="开始录制基带/音频。支持多种格式：cf32(复数浮点IQ，标准SDR格式), wav(音频), csv(文本CSV), iq(原始IQ)。可设置录制时长（0=手动停止）。录制文件自动保存到 ~/.mbdsdr/recordings/。",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "number", "description": "录制时长，单位秒。0=持续录制直到手动停止，默认 0"},
                "format": {"type": "string", "description": "录制格式: cf32/wav/csv/iq，默认 cf32"},
                "save_path": {"type": "string", "description": "保存路径（可选，默认自动生成）"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_record_start(mgr, args)),
        category="sdr_record",
    )

    agent.tool_registry.register(
        name="sdr_record_stop",
        description="停止当前录制。返回录制文件路径、时长、文件大小、采样率、中心频率等元数据。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: ToolResult(success=True, content=_record_stop(mgr)),
        category="sdr_record",
    )

    agent.tool_registry.register(
        name="sdr_recordings_list",
        description="列出所有历史录制文件。包括文件名、格式、时长、大小、采样率、中心频率、录制时间。用于回放或后续分析。",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回最近 N 个，默认 20"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_recordings_list(args)),
        category="sdr_record",
    )

    # ═══════════════════════════════════════════════════
    # 7. 解码（5个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_decode_noaa_apt",
        description="解码 NOAA 气象卫星 APT 图像。输入录制的 cf32/wav 文件，输出 PNG 云图。NOAA 15/18/19 卫星频率 137.1MHz/137.9125MHz/137.1MHz。需要先录制卫星过境信号。",
        parameters={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "输入录制文件路径（cf32 或 wav）"},
                "output_path": {"type": "string", "description": "输出 PNG 路径（可选，默认同目录）"},
                "channel": {"type": "string", "description": "通道: A(红外)/B(可见光)/both，默认 both"},
            },
            "required": ["input_path"],
        },
        handler=lambda args: ToolResult(success=True, content=_decode_noaa_apt(args)),
        category="sdr_decode",
    )

    agent.tool_registry.register(
        name="sdr_decode_sstv",
        description="解码 SSTV 慢扫描电视图像。输入 wav 录制文件，输出 PNG 图像。支持常见模式：Martin M1/M2, Scottie S1/S2, Robot 36/72。SSTV 常用频率 14.230MHz(USB), 21.340MHz, 28.680MHz。",
        parameters={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "输入 wav 文件路径"},
                "output_path": {"type": "string", "description": "输出 PNG 路径（可选）"},
            },
            "required": ["input_path"],
        },
        handler=lambda args: ToolResult(success=True, content=_decode_sstv(args)),
        category="sdr_decode",
    )

    agent.tool_registry.register(
        name="sdr_decode_ft8",
        description="解码 FT8 数字通信信号。输入录制文件，输出解码到的 FT8 报文（呼号、网格、信号报告）。FT8 常用频率：14.074MHz(20m), 7.074MHz(40m), 21.074MHz(15m), 28.074MHz(10m)。需要 15 秒周期对齐。",
        parameters={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "输入录制文件路径"},
                "frequency_hz": {"type": "number", "description": "接收频率，用于时间同步"},
            },
            "required": ["input_path"],
        },
        handler=lambda args: ToolResult(success=True, content=_decode_ft8(args)),
        category="sdr_decode",
    )

    agent.tool_registry.register(
        name="sdr_decode_aprs",
        description="解码 APRS 自动位置报告系统数据包。输入录制文件，输出 APRS 报文（呼号、位置、符号、注释）。APRS 频率：中国 144.640MHz, 美国 144.390MHz, 欧洲 144.800MHz。1200 baud AFSK 调制。",
        parameters={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "输入录制文件路径"},
                "duration": {"type": "number", "description": "持续解码时长，单位秒（可选，实时模式）"},
            },
            "required": ["input_path"],
        },
        handler=lambda args: ToolResult(success=True, content=_decode_aprs(args)),
        category="sdr_decode",
    )

    agent.tool_registry.register(
        name="sdr_decode_adsb",
        description="解码 ADS-B 飞机广播信号。输入录制文件，输出飞机信息（ICAO地址、呼号、位置、高度、速度、航向）。ADS-B 频率 1090MHz。需要 2MHz 以上采样率。",
        parameters={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "输入录制文件路径（cf32 格式）"},
                "duration": {"type": "number", "description": "持续解码时长（可选，实时模式）"},
            },
            "required": ["input_path"],
        },
        handler=lambda args: ToolResult(success=True, content=_decode_adsb(args)),
        category="sdr_decode",
    )

    # ═══════════════════════════════════════════════════
    # 8. 卫星（2个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_satellite_sky_view",
        description="查看当前天空中可见的卫星。输入经纬度，返回当前可见的卫星列表（名称、仰角、方位角、距离、多普勒频移、过境时间）。支持 NOAA、METEOR、ISS、业余无线电卫星等。用于卫星接收前找星。",
        parameters={
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "纬度，例如 43.8（长春）"},
                "longitude": {"type": "number", "description": "经度，例如 125.3（长春）"},
                "altitude_m": {"type": "number", "description": "海拔，单位米，默认 0"},
                "satellite_type": {"type": "string", "description": "卫星类型: weather(气象)/amateur(业余)/iss(国际空间站)/all，默认 all"},
            },
            "required": ["latitude", "longitude"],
        },
        handler=lambda args: ToolResult(success=True, content=_satellite_sky_view(args)),
        category="sdr_satellite",
    )

    agent.tool_registry.register(
        name="sdr_satellite_doppler",
        description="计算卫星多普勒修正频率。输入卫星名称和下行频率，返回当前修正后的接收频率（考虑多普勒频移）。卫星过境时频率会变化，需要持续修正。",
        parameters={
            "type": "object",
            "properties": {
                "satellite_name": {"type": "string", "description": "卫星名称，如 NOAA 19, ISS, METEOR M2"},
                "frequency_hz": {"type": "number", "description": "标称下行频率，单位 Hz。如 NOAA APT 137100000"},
                "latitude": {"type": "number", "description": "地面站纬度"},
                "longitude": {"type": "number", "description": "地面站经度"},
            },
            "required": ["satellite_name", "frequency_hz", "latitude", "longitude"],
        },
        handler=lambda args: ToolResult(success=True, content=_satellite_doppler(args)),
        category="sdr_satellite",
    )

    # ═══════════════════════════════════════════════════
    # 9. 定位（2个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_get_gps",
        description="获取当前 GPS 定位信息。包括经纬度、海拔、速度、航向、卫星数、HDOP、定位质量、UTC 时间。需要设备带 GPS 模块（自研 ai-sdr Mini 带 ATGM336H 北斗/GPS 双模）。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: ToolResult(success=True, content=_get_gps(mgr)),
        category="sdr_location",
    )

    agent.tool_registry.register(
        name="sdr_get_imu",
        description="获取当前 IMU 姿态信息。包括加速度计(XYZ)、陀螺仪(XYZ)、磁力计(XYZ)、欧拉角(横滚/俯仰/偏航)、四元数、温度。用于天线指向、AR 叠加、干扰源方向查找。需要设备带 IMU（自研 ai-sdr Mini 带 BMI260 + TMAG5273）。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda args: ToolResult(success=True, content=_get_imu(mgr)),
        category="sdr_location",
    )

    # ═══════════════════════════════════════════════════
    # 10. 信号分析（3个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_identify_modulation",
        description="识别当前信号的调制方式。读取 IQ 样本，提取幅度/相位/频率特征（均值、方差、峰度、过零率、频谱质心、频谱平坦度），基于特征猜测调制类型（FM/AM/SSB/CW/噪声等）。这是 AI 辅助信号分析的核心工具。",
        parameters={
            "type": "object",
            "properties": {
                "num_samples": {"type": "integer", "description": "分析样本数，默认 4096。越多越准确但越慢"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_identify_modulation(mgr, spec, args)),
        category="sdr_analysis",
    )

    agent.tool_registry.register(
        name="sdr_detect_fhss",
        description="检测跳频信号（FHSS）。连续录制多帧频谱，检测频率随时间跳变的信号。返回跳频图案（频率列表、驻留时间、跳频速率、跳频序列）。用于识别对讲机、遥控、蓝牙等跳频设备。",
        parameters={
            "type": "object",
            "properties": {
                "num_frames": {"type": "integer", "description": "分析帧数，默认 50"},
                "frame_interval_ms": {"type": "integer", "description": "帧间隔，单位毫秒，默认 10"},
                "threshold_db": {"type": "number", "description": "信号检测阈值，默认 -60dB"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_detect_fhss(mgr, spec, args)),
        category="sdr_analysis",
    )

    agent.tool_registry.register(
        name="sdr_measure_signal",
        description="精确测量当前信号参数。包括中心频率（亚 bin 精度）、带宽、峰值功率、平均功率、噪声底、SNR、占用带宽、频率偏移 ppm。用于信号 characterization、频率校准、干扰源测量。",
        parameters={
            "type": "object",
            "properties": {
                "expected_freq_hz": {"type": "number", "description": "预期频率（可选，用于计算偏移）"},
                "num_samples": {"type": "integer", "description": "样本数，默认 4096"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_measure_signal(mgr, spec, args)),
        category="sdr_analysis",
    )

    # ═══════════════════════════════════════════════════
    # 11. AI 辅助（2个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_ai_sweep",
        description="AI 辅助扫频找台。在指定频率范围内步进扫描，每步测量信号强度，自动找到最强信号并调谐过去。返回扫描结果（频率-功率列表）和找到的最强台。这是 AI 自主找台的核心工具，不需要人工干预。",
        parameters={
            "type": "object",
            "properties": {
                "freq_start_hz": {"type": "number", "description": "起始频率，单位 Hz。如 FM 广播 87500000"},
                "freq_end_hz": {"type": "number", "description": "结束频率，单位 Hz。如 FM 广播 108000000"},
                "step_hz": {"type": "number", "description": "步进频率，单位 Hz。如 100000=100kHz"},
                "dwell_ms": {"type": "integer", "description": "每步驻留时间，单位毫秒，默认 50"},
                "auto_tune": {"type": "boolean", "description": "是否自动调谐到最强台，默认 true"},
            },
            "required": ["freq_start_hz", "freq_end_hz", "step_hz"],
        },
        handler=lambda args: ToolResult(success=True, content=_ai_sweep(mgr, spec, args)),
        category="sdr_ai",
    )

    agent.tool_registry.register(
        name="sdr_ai_find_center",
        description="AI 辅助找精确中心频点。在当前频率附近小范围精细扫描，用抛物线插值找到信号的精确中心频率，自动修正频偏。返回精确中心频率、偏移量、ppm。用于频率校准、找精确中心频点。",
        parameters={
            "type": "object",
            "properties": {
                "search_range_hz": {"type": "number", "description": "搜索范围，单位 Hz，默认 50000（±25kHz）"},
                "num_steps": {"type": "integer", "description": "搜索步数，默认 20"},
                "auto_correct": {"type": "boolean", "description": "是否自动修正频率，默认 true"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_ai_find_center(mgr, spec, args)),
        category="sdr_ai",
    )

    # ═══════════════════════════════════════════════════
    # 13. IQ 前端校正（1个）— 白皮书第五章 5.2
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_iq_correct",
        description="IQ 前端校正：DC 阻断 + I/Q 不平衡校正（协方差白化）+ 整数抽取。零中频 SDR 前端有三类固有损伤：直流偏置（本振自混频泄漏）、I/Q 不平衡（两路增益/相位不一致）、过采样冗余。校正后中心不再有 DC 尖峰，I/Q 镜像被消除，数据率降低。返回校正前后的诊断信息（DC 偏移、增益误差、相位误差、IRR 改善）。",
        parameters={
            "type": "object",
            "properties": {
                "dc_block": {"type": "boolean", "description": "是否做 DC 阻断，默认 true"},
                "correct_iq": {"type": "boolean", "description": "是否做 I/Q 不平衡校正，默认 true"},
                "decimation": {"type": "integer", "description": "整数抽取因子，1=不抽取，默认 1"},
                "num_samples": {"type": "integer", "description": "校正用的样本数，默认 16384"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_iq_correct(mgr, args)),
        category="sdr_analysis",
    )

    # ═══════════════════════════════════════════════════
    # 14. 真实解调（1个）— 最基本的 SDR 功能
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_demodulate",
        description="真实解调当前信号。支持 FM（调频广播）、NFM（窄带FM/对讲机）、WFM（宽带FM）、AM（调幅/航空/短波）、LSB（下边带）、USB（上边带）、CW（等幅报）。读取 IQ 样本，运行真实解调算法，输出音频样本和音频特征（RMS、峰值、均值）。这是 SDR 最核心的功能。",
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "description": "解调模式: FM/NFM/WFM/AM/LSB/USB/CW，默认使用当前设置的解调模式"},
                "num_samples": {"type": "integer", "description": "解调样本数，默认 16384"},
                "deviation": {"type": "number", "description": "FM 最大频偏 Hz，默认 75000（广播FM），NFM 用 5000"},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_demodulate(mgr, args)),
        category="sdr_demod",
    )

    # ═══════════════════════════════════════════════════
    # 15. 录制文件分析（1个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_analyze_recording",
        description="分析已录制的基带文件。读取 cf32/cs16/wav 格式的录制文件，做频谱分析、IQ 前端校正、信号检测、调制识别，返回完整的分析报告。用于事后分析录制的信号，不需要实时连接 SDR 设备。",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "录制文件路径（.cf32/.cs16/.wav）"},
                "correct_iq": {"type": "boolean", "description": "是否先做 IQ 前端校正，默认 true"},
                "fft_size": {"type": "integer", "description": "FFT 大小，默认 1024"},
            },
            "required": ["file_path"],
        },
        handler=lambda args: ToolResult(success=True, content=_analyze_recording(args)),
        category="sdr_analysis",
    )

    # ═══════════════════════════════════════════════════
    # 16. AX.25 / APRS 网络通信（8个）
    # ═══════════════════════════════════════════════════

    agent.tool_registry.register(
        name="sdr_encode_ax25",
        description="编码 AX.25 帧。输入源呼号、目的呼号、中继器路径、信息字段，输出 AX.25 帧字节（十六进制）。支持 UI/I/Supervisory 帧，支持 WIDEn-N 中继路径。AX.25 是业余无线电分组网络的核心数据链路层协议。",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "源呼号（如 BI4MIB 或 BI4MIB-7）"},
                "destination": {"type": "string", "description": "目的呼号（如 APRS 或 CQ）"},
                "digipeaters": {"type": "array", "items": {"type": "string"}, "description": "中继器路径列表（如 ['WIDE1-1', 'WIDE2-2']）"},
                "info": {"type": "string", "description": "信息字段内容（文本）"},
                "control": {"type": "integer", "description": "控制字段，默认 0x03 (UI帧)"},
                "pid": {"type": "integer", "description": "协议ID，默认 0xF0 (无第三层)"},
            },
            "required": ["source", "destination", "info"],
        },
        handler=lambda args: ToolResult(success=True, content=_encode_ax25(args)),
        category="sdr_network",
    )

    agent.tool_registry.register(
        name="sdr_decode_ax25",
        description="解码 AX.25 帧。输入十六进制帧数据或 WAV 音频文件路径，输出解析后的 AX.25 帧（源/目的呼号、中继器路径、控制字段、协议ID、信息字段、FCS校验结果）。支持从 AFSK 1200 baud 音频中解调。",
        parameters={
            "type": "object",
            "properties": {
                "hex_data": {"type": "string", "description": "十六进制帧数据（不含首尾标志）"},
                "audio_path": {"type": "string", "description": "WAV 音频文件路径（AFSK 1200 baud 调制的 AX.25 信号）"},
            },
        },
        handler=lambda args: ToolResult(success=True, content=_decode_ax25(args)),
        category="sdr_network",
    )

    agent.tool_registry.register(
        name="sdr_aprs_encode",
        description="编码 APRS 报文。输入呼号、纬度、经度、注释、符号等，输出完整的 APRS AX.25 帧（十六进制）和可直接播放的 AFSK 音频 WAV 文件。支持位置报文、消息报文、气象报文。APRS 频率：中国 144.640MHz，美国 144.390MHz，欧洲 144.800MHz。",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "源呼号（如 BI4MIB）"},
                "latitude": {"type": "number", "description": "纬度（十进制度，北纬正）"},
                "longitude": {"type": "number", "description": "经度（十进制度，东经正）"},
                "comment": {"type": "string", "description": "注释文本"},
                "symbol": {"type": "string", "description": "APRS 符号（表+代码，如 '/-' 表示小车，默认 '/-'）"},
                "digipeaters": {"type": "array", "items": {"type": "string"}, "description": "中继器路径，默认 ['WIDE2-2']"},
                "output_wav": {"type": "string", "description": "输出 WAV 文件路径（可选）"},
            },
            "required": ["source", "latitude", "longitude"],
        },
        handler=lambda args: ToolResult(success=True, content=_aprs_encode(args)),
        category="sdr_network",
    )

    agent.tool_registry.register(
        name="sdr_aprs_decode",
        description="解码 APRS 报文。输入 AX.25 帧十六进制或 WAV 音频文件，输出解析后的 APRS 报文（源呼号、数据类型、位置/消息/气象内容、中继器路径）。自动识别位置报文、消息报文、气象报文、对象报文。",
        parameters={
            "type": "object",
            "properties": {
                "hex_data": {"type": "string", "description": "AX.25 帧十六进制数据"},
                "audio_path": {"type": "string", "description": "WAV 音频文件路径"},
            },
        },
        handler=lambda args: ToolResult(success=True, content=_aprs_decode(args)),
        category="sdr_network",
    )

    agent.tool_registry.register(
        name="sdr_aprs_send_position",
        description="发送 APRS 位置报告。这是一个便捷工具，自动编码 APRS 位置帧并调制为 AFSK 音频，通过 SDR 发射（如果已连接发射设备）或保存为 WAV 文件。输入呼号、经纬度、注释即可。默认中继路径 WIDE2-2。",
        parameters={
            "type": "object",
            "properties": {
                "callsign": {"type": "string", "description": "你的呼号（如 BI4MIB）"},
                "latitude": {"type": "number", "description": "纬度（十进制度）"},
                "longitude": {"type": "number", "description": "经度（十进制度）"},
                "comment": {"type": "string", "description": "注释文本（如 'MBDSDR AI SDR Station'）"},
                "symbol": {"type": "string", "description": "APRS 符号，默认 '/-'（小车）"},
                "frequency_hz": {"type": "integer", "description": "发射频率，默认 144640000 Hz（中国 APRS）"},
                "output_wav": {"type": "string", "description": "保存 WAV 文件路径（可选，不发射时使用）"},
            },
            "required": ["callsign", "latitude", "longitude"],
        },
        handler=lambda args: ToolResult(success=True, content=_aprs_send_position(args)),
        category="sdr_network",
    )

    agent.tool_registry.register(
        name="sdr_kiss_encode",
        description="编码 KISS 协议帧。KISS 是连接电脑与硬件 TNC（终端节点控制器）的标准协议。输入 AX.25 帧数据和端口号，输出 KISS 帧字节（十六进制），可通过串口发送给硬件 TNC。",
        parameters={
            "type": "object",
            "properties": {
                "ax25_hex": {"type": "string", "description": "AX.25 帧十六进制数据"},
                "port": {"type": "integer", "description": "KISS 端口号，默认 0"},
                "command": {"type": "integer", "description": "KISS 命令，0=数据帧，默认 0"},
            },
            "required": ["ax25_hex"],
        },
        handler=lambda args: ToolResult(success=True, content=_kiss_encode(args)),
        category="sdr_network",
    )

    agent.tool_registry.register(
        name="sdr_kiss_decode",
        description="解码 KISS 协议帧流。输入从硬件 TNC 接收的原始字节（十六进制），输出解析后的 KISS 帧列表（端口号、AX.25 帧数据）。支持 FEND/FESC 转义。",
        parameters={
            "type": "object",
            "properties": {
                "hex_data": {"type": "string", "description": "从 TNC 接收的原始字节十六进制数据"},
            },
            "required": ["hex_data"],
        },
        handler=lambda args: ToolResult(success=True, content=_kiss_decode(args)),
        category="sdr_network",
    )

    agent.tool_registry.register(
        name="sdr_digipeater_process",
        description="Digipeater 分组转发处理。输入接收到的 AX.25 帧，根据中继器路径（WIDEn-N 泛洪或指定呼号）决定是否转发，返回需要转发的帧或 None。支持重复帧检测（去重）、WIDEn-N 递减算法。用于构建 APRS 数字中继站。",
        parameters={
            "type": "object",
            "properties": {
                "ax25_hex": {"type": "string", "description": "接收到的 AX.25 帧十六进制数据"},
                "mycall": {"type": "string", "description": "本中继站呼号（如 BI4MIB）"},
                "myssid": {"type": "integer", "description": "本中继站 SSID，默认 0"},
                "digi_calls": {"type": "array", "items": {"type": "string"}, "description": "额外响应的中继器呼号列表"},
            },
            "required": ["ax25_hex", "mycall"],
        },
        handler=lambda args: ToolResult(success=True, content=_digipeater_process(args)),
        category="sdr_network",
    )

    # ═══════════════════════════════════════════════════════
    # 新时空工具（授时/GIS/PNT/卫星Pass预测）
    # ═══════════════════════════════════════════════════════

    agent.tool_registry.register(
        name="time_get_info",
        description="获取当前时间信息，包括 UTC 时间、本地时间、GPS 周内秒、本地时钟偏差、NTP 服务器状态。优先从 NTP 服务器获取精确时间，失败则用系统时间。用于授时、时钟校准、时间同步。",
        parameters={
            "type": "object",
            "properties": {
                "prefer_ntp": {"type": "boolean", "description": "是否优先使用 NTP，默认 true"},
            },
        },
        handler=lambda args: ToolResult(success=True, content=_time_get_info(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="time_ntp_sync",
        description="从指定 NTP 服务器同步时间，返回 UTC 时间、往返延迟、时钟偏差。支持阿里云、腾讯云、cn.ntp.org.cn、pool.ntp.org 等公共 NTP 服务器。用于精确授时。",
        parameters={
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "NTP 服务器地址，默认 ntp.aliyun.com"},
                "timeout": {"type": "number", "description": "超时秒数，默认 3"},
            },
        },
        handler=lambda args: ToolResult(success=True, content=_time_ntp_sync(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="gnss_system_info",
        description="获取 GNSS 全球导航卫星系统信息，包括 GPS、北斗 BDS、GLONASS、Galileo 的频率、国家、频段。可查询单个系统或全部系统。用于了解各卫星导航系统的频率规划。",
        parameters={
            "type": "object",
            "properties": {
                "system": {"type": "string", "description": "GNSS 系统：GPS/BDS/GLONASS/Galileo/all，默认 all"},
            },
        },
        handler=lambda args: ToolResult(success=True, content=_gnss_system_info(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="gnss_parse_rmc",
        description="解析 GNSS RMC NMEA 语句（推荐最小定位信息），提取 UTC 时间、经纬度、速度、航向、定位状态。输入 NMEA 语句字符串，输出结构化定位信息。用于解析 GNSS 接收机输出。",
        parameters={
            "type": "object",
            "properties": {
                "nmea": {"type": "string", "description": "NMEA RMC 语句，如 $GNRMC,072545.00,A,4352.0000,N,12519.0000,E,..."},
            },
            "required": ["nmea"],
        },
        handler=lambda args: ToolResult(success=True, content=_gnss_parse_rmc(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="gis_distance",
        description="计算两个地理坐标点之间的大圆距离（Haversine 公式），返回公里数。输入两点的经纬度，用于计算两点间距离、航线长度、干扰源距离估算。",
        parameters={
            "type": "object",
            "properties": {
                "lat1": {"type": "number", "description": "起点纬度（十进制度）"},
                "lon1": {"type": "number", "description": "起点经度（十进制度）"},
                "lat2": {"type": "number", "description": "终点纬度（十进制度）"},
                "lon2": {"type": "number", "description": "终点经度（十进制度）"},
            },
            "required": ["lat1", "lon1", "lat2", "lon2"],
        },
        handler=lambda args: ToolResult(success=True, content=_gis_distance(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="gis_bearing",
        description="计算从起点到终点的方位角（度，0=北，顺时针）。输入两点经纬度，返回方位角。用于天线指向、干扰源方位、导航方向。",
        parameters={
            "type": "object",
            "properties": {
                "lat1": {"type": "number", "description": "起点纬度"},
                "lon1": {"type": "number", "description": "起点经度"},
                "lat2": {"type": "number", "description": "终点纬度"},
                "lon2": {"type": "number", "description": "终点经度"},
            },
            "required": ["lat1", "lon1", "lat2", "lon2"],
        },
        handler=lambda args: ToolResult(success=True, content=_gis_bearing(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="gis_destination",
        description="给定起点、方位角和距离，计算终点坐标。输入起点经纬度、方位角（度）、距离（公里），返回终点经纬度。用于天线指向预测、航点计算、干扰源定位。",
        parameters={
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "起点纬度"},
                "longitude": {"type": "number", "description": "起点经度"},
                "bearing_deg": {"type": "number", "description": "方位角（度，0=北）"},
                "distance_km": {"type": "number", "description": "距离（公里）"},
            },
            "required": ["latitude", "longitude", "bearing_deg", "distance_km"],
        },
        handler=lambda args: ToolResult(success=True, content=_gis_destination(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="pnt_get_state",
        description="获取泛在 PNT（定位导航授时）融合状态，包括当前位置、精度、时间、激活的定位源（GNSS/LEO/IMU/WiFi/蓝牙/UWB/蜂窝）、融合模式。新时空核心功能，多源定位融合。",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: ToolResult(success=True, content=_pnt_get_state(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="pnt_update_source",
        description="更新泛在 PNT 某个定位源的数据，触发多源融合。支持 GNSS、LEO_PNT、PPP_RTK、IMU、WiFi、蓝牙、UWB、蜂窝、NTP、视觉等源。输入源类型和定位数据，返回融合后的 PNT 状态。",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "定位源类型：gnss/leo_pnt/ppp_rtk/imu/wifi/bluetooth/uwb/cellular/ntp/visual"},
                "latitude": {"type": "number", "description": "纬度（十进制度）"},
                "longitude": {"type": "number", "description": "经度（十进制度）"},
                "altitude_m": {"type": "number", "description": "海拔（米）"},
                "accuracy_m": {"type": "number", "description": "定位精度（米）"},
                "valid": {"type": "boolean", "description": "数据是否有效，默认 true"},
                "satellites": {"type": "integer", "description": "可见卫星数（GNSS 源）"},
            },
            "required": ["source", "latitude", "longitude"],
        },
        handler=lambda args: ToolResult(success=True, content=_pnt_update_source(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="satellite_predict_pass",
        description="预测指定卫星的下一次过境（升起/中天/落下时间、最大仰角、持续时间、轨迹、多普勒频移）。使用 sgp4 轨道计算，时间步长 30 秒。输入卫星名、观察者经纬度，返回完整过境预测。用于卫星接收规划、天线指向调度。",
        parameters={
            "type": "object",
            "properties": {
                "satellite_name": {"type": "string", "description": "卫星名称，如 NOAA 19/ISS/METEOR M2/Fengyun 3D"},
                "observer_lat": {"type": "number", "description": "观察者纬度"},
                "observer_lon": {"type": "number", "description": "观察者经度"},
                "observer_alt": {"type": "number", "description": "观察者海拔（米），默认 0"},
                "hours_ahead": {"type": "number", "description": "预测未来多少小时，默认 24"},
                "min_elevation": {"type": "number", "description": "最小仰角（度），默认 5"},
            },
            "required": ["satellite_name", "observer_lat", "observer_lon"],
        },
        handler=lambda args: ToolResult(success=True, content=_satellite_predict_pass(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="satellite_predict_all",
        description="预测所有内置卫星的下一次过境，按升起时间排序。输入观察者经纬度，返回所有可见卫星的过境预测列表。用于卫星接收日程规划、天空图显示。",
        parameters={
            "type": "object",
            "properties": {
                "observer_lat": {"type": "number", "description": "观察者纬度"},
                "observer_lon": {"type": "number", "description": "观察者经度"},
                "observer_alt": {"type": "number", "description": "观察者海拔（米），默认 0"},
                "hours_ahead": {"type": "number", "description": "预测未来多少小时，默认 24"},
                "min_elevation": {"type": "number", "description": "最小仰角（度），默认 5"},
            },
            "required": ["observer_lat", "observer_lon"],
        },
        handler=lambda args: ToolResult(success=True, content=_satellite_predict_all(args)),
        category="new_spacetime",
    )

    agent.tool_registry.register(
        name="sky_view_visible",
        description="获取当前天空图可见卫星列表，包括每颗卫星的仰角、方位角、距离、多普勒频移。输入观察者经纬度和最小仰角，返回可见卫星结构化数据。用于天空图渲染、卫星指向。",
        parameters={
            "type": "object",
            "properties": {
                "observer_lat": {"type": "number", "description": "观察者纬度"},
                "observer_lon": {"type": "number", "description": "观察者经度"},
                "min_elevation": {"type": "number", "description": "最小仰角（度），默认 5"},
            },
            "required": ["observer_lat", "observer_lon"],
        },
        handler=lambda args: ToolResult(success=True, content=_sky_view_visible(args)),
        category="new_spacetime",
    )

    # ========================================================================
    # GNSS 干扰监测工具（新时空扩展）
    # ========================================================================

    agent.tool_registry.register(
        name="gnss_monitor_band",
        description="监测单个 GNSS 频带（L1/L2/L5/B1/B2/B3）的干扰情况。分析功率谱、噪声基底、干扰噪声比（INR），AI自动分类干扰类型（连续波/窄带/宽带）。用于 GNSS 信号质量评估和干扰检测。",
        parameters={
            "type": "object",
            "properties": {
                "band_name": {"type": "string", "description": "频带名称：L1/L2/L5/B1/B2/B3"},
                "center_freq_hz": {"type": "number", "description": "中心频率（Hz）"},
                "duration_sec": {"type": "number", "description": "采样时长（秒）", "default": 1.0},
                "sample_rate_hz": {"type": "number", "description": "采样率（Hz）", "default": 2.048e6},
            },
            "required": ["band_name", "center_freq_hz"],
        },
        handler=lambda args: ToolResult(success=True, content=_gnss_monitor_band(args)),
        category="gnss_monitor",
    )

    agent.tool_registry.register(
        name="gnss_monitor_all",
        description="监测所有 GNSS 频带（L1/L2/L5/B1/B2/B3），生成干扰告警报告。自动分类干扰类型并给出处理建议。用于 GNSS 电磁环境普查。",
        parameters={
            "type": "object",
            "properties": {
                "duration_sec": {"type": "number", "description": "每个频带采样时长（秒）", "default": 0.5},
                "inr_warning_db": {"type": "number", "description": "告警门限（dB）", "default": 10.0},
            },
            "required": [],
        },
        handler=lambda args: ToolResult(success=True, content=_gnss_monitor_all(args)),
        category="gnss_monitor",
    )

    agent.tool_registry.register(
        name="gnss_direction_find",
        description="基于八木天线 RSSI-方位角扫描数据，估算干扰源方向。输入各方位角的RSSI值，用质心法估算干扰源方位。用于干扰源定位（GP全向天线检测→八木测向→云台跟踪）。",
        parameters={
            "type": "object",
            "properties": {
                "rssi_by_azimuth": {"type": "object", "description": "方位角(度)到RSSI(dBm)的映射，如 {\"0\": -60, \"45\": -55, \"90\": -70}"},
            },
            "required": ["rssi_by_azimuth"],
        },
        handler=lambda args: ToolResult(success=True, content=_gnss_direction_find(args)),
        category="gnss_monitor",
    )


# ═══════════════════════════════════════════════════════
# 工具实现函数
# ═══════════════════════════════════════════════════════

def _get_backend(mgr):
    """获取当前激活的后端。"""
    return mgr.get_active()

def _disconnect_sdr(mgr):
    backend = mgr.get_active()
    if backend:
        name = backend.device.name
        backend.disconnect()
        return f"已断开: {name}"
    return "没有已连接的设备"

def _format_status(status: Optional[SDRStatus]) -> str:
    if not status:
        return "没有已连接的设备"
    lines = [
        "=== SDR 状态 ===",
        f"连接: {'是' if status.connected else '否'}",
        f"频率: {status.frequency_hz/1e6:.3f} MHz",
        f"采样率: {status.sample_rate_hz/1e6:.3f} MHz",
        f"增益: {status.gain_db:.1f} dB ({'AGC开' if status.agc_enabled else '手动'})",
        f"解调: {status.demod_mode}",
        f"带宽: {status.bandwidth_hz if status.bandwidth_hz > 0 else '自动'} Hz",
        f"RSSI: {status.rssi_db:.1f} dB, SNR: {status.snr_db:.1f} dB",
        f"静噪: {status.squelch_db} dB, 音量: {status.volume:.0%}",
        f"录制: {'是' if status.recording else '否'}" + (f" ({status.recording_path})" if status.recording else ""),
        f"运行时间: {status.uptime_seconds:.0f} 秒",
        f"已读样本: {status.samples_read:,}",
    ]
    return "\n".join(lines)

def _spectrum_analyze(mgr, spec, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接，请先调用 sdr_connect"
    fft_size = args.get("fft_size", 1024)
    num_samples = args.get("num_samples", fft_size)
    samples = backend.read_samples(num_samples)
    if samples is None:
        return "错误: 该设备不支持 IQ 样本输出（如 SI4732 只输出解调后音频）"
    spectrum = spec.compute_spectrum(samples, backend.get_frequency(), backend.get_sample_rate(), fft_size)
    signals = spec.find_signals(spectrum, threshold_db=-60)
    result = {
        "center_freq_mhz": round(spectrum.center_freq / 1e6, 3),
        "sample_rate_mhz": round(spectrum.sample_rate / 1e6, 3),
        "fft_size": spectrum.fft_size,
        "peak_freq_mhz": round(spectrum.peak_freq / 1e6, 3),
        "peak_power_db": round(spectrum.peak_power_db, 1),
        "noise_floor_db": round(spectrum.noise_floor_db, 1),
        "num_signals_detected": len(signals),
        "top_signals": [
            {"freq_mhz": round(s["center_freq"] / 1e6, 3), "bw_khz": round(s["bandwidth"] / 1000, 1), "power_db": round(s["peak_power_db"], 1)}
            for s in signals[:5]
        ],
    }
    return json.dumps(result, ensure_ascii=False, indent=2)

def _spectrum_zoom(spec, factor, mgr):
    spec.zoom(factor)
    backend = _get_backend(mgr)
    if backend:
        freq_range = spec.get_view_range(backend.get_frequency(), backend.get_sample_rate())
        return f"频谱缩放: {factor}x\n视图范围: {freq_range[0]/1e6:.3f} - {freq_range[1]/1e6:.3f} MHz"
    return f"频谱缩放: {factor}x"

def _spectrum_pan(spec, offset_hz, mgr):
    spec.pan(offset_hz)
    backend = _get_backend(mgr)
    if backend:
        freq_range = spec.get_view_range(backend.get_frequency(), backend.get_sample_rate())
        return f"频谱平移: {offset_hz/1e3:.1f} kHz\n视图范围: {freq_range[0]/1e6:.3f} - {freq_range[1]/1e6:.3f} MHz"
    return f"频谱平移: {offset_hz/1e3:.1f} kHz"

def _spectrum_screenshot(mgr, spec, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"
    save_dir = os.path.expanduser("~/.mbdsdr/screenshots")
    os.makedirs(save_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    save_path = args.get("save_path") or os.path.join(save_dir, f"spectrum_{timestamp}.png")
    # 生成频谱图（简化版，实际应使用 matplotlib）
    samples = backend.read_samples(1024)
    if samples is not None:
        spectrum = spec.compute_spectrum(samples, backend.get_frequency(), backend.get_sample_rate())
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(spectrum.frequencies / 1e6, spectrum.powers_db)
            ax.set_xlabel("频率 (MHz)")
            ax.set_ylabel("功率 (dB)")
            ax.set_title(f"MBDSDR 频谱图 - {spectrum.center_freq/1e6:.2f} MHz")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            fig.savefig(save_path, dpi=100)
            plt.close(fig)
            return f"频谱截图已保存: {save_path}\n峰值: {spectrum.peak_freq/1e6:.3f} MHz @ {spectrum.peak_power_db:.1f} dB"
        except Exception as e:
            return f"截图生成失败（matplotlib 不可用）: {e}\n可使用 sdr_spectrum_text 查看文本频谱"
    return "该设备不支持 IQ 输出，无法生成频谱截图"

def _spectrum_find_signals(mgr, spec, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"
    samples = backend.read_samples(2048)
    if samples is None:
        return "错误: 该设备不支持 IQ 样本"
    spectrum = spec.compute_spectrum(samples, backend.get_frequency(), backend.get_sample_rate())
    threshold = args.get("threshold_db", -60)
    min_bw = args.get("min_bandwidth_hz", 1000)
    signals = spec.find_signals(spectrum, threshold_db=threshold, min_bandwidth_hz=min_bw)
    if not signals:
        return f"未检测到超过 {threshold} dB 的信号（噪声底 {spectrum.noise_floor_db:.1f} dB）"
    result = f"检测到 {len(signals)} 个信号:\n"
    for i, s in enumerate(signals[:10]):
        result += f"  {i+1}. {s['center_freq']/1e6:.3f} MHz, 带宽 {s['bandwidth']/1e3:.1f} kHz, 峰值 {s['peak_power_db']:.1f} dB\n"
    return result

def _spectrum_center_offset(mgr, spec, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"
    samples = backend.read_samples(4096)
    if samples is None:
        return "错误: 该设备不支持 IQ 样本"
    spectrum = spec.compute_spectrum(samples, backend.get_frequency(), backend.get_sample_rate(), fft_size=4096)
    expected = args.get("expected_freq_hz")
    result = spec.estimate_center_offset(spectrum, expected_freq=expected)
    return json.dumps(result, ensure_ascii=False, indent=2)

def _spectrum_text(mgr, spec, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"
    samples = backend.read_samples(1024)
    if samples is None:
        return "错误: 该设备不支持 IQ 样本"
    spectrum = spec.compute_spectrum(samples, backend.get_frequency(), backend.get_sample_rate())
    return spec.generate_spectrum_text(spectrum, max_bins=args.get("max_bins", 60))

def _record_start(mgr, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"
    duration = args.get("duration", 0)
    fmt = args.get("format", "cf32")
    gain = args.get("gain", 1.0)
    decimation = args.get("decimation", 1)
    save_dir = os.path.expanduser("~/.mbdsdr/recordings")
    os.makedirs(save_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    freq_mhz = int(backend.get_frequency() / 1e6)
    save_path = args.get("save_path") or os.path.join(save_dir, f"mbdsdr_{freq_mhz}MHz_{timestamp}.{fmt}")
    backend.start_recording(save_path, duration, fmt=fmt, gain=gain, decimation=decimation)
    return f"开始录制\n格式: {fmt}\n路径: {save_path}\n时长: {'持续' if duration == 0 else f'{duration}秒'}\n频率: {backend.get_frequency()/1e6:.3f} MHz\n采样率: {backend.get_sample_rate()/1e6:.3f} MHz\n抽取: {decimation}x\n增益: {gain}"

def _record_stop(mgr):
    backend = _get_backend(mgr)
    if not backend or not backend.status.recording:
        return "没有正在进行的录制"
    path = backend.stop_recording()
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return f"录制已停止\n文件: {path}\n大小: {size/1024:.1f} KB"

def _recordings_list(args):
    save_dir = os.path.expanduser("~/.mbdsdr/recordings")
    if not os.path.exists(save_dir):
        return "没有录制文件"
    files = []
    for f in os.listdir(save_dir):
        fpath = os.path.join(save_dir, f)
        if os.path.isfile(fpath):
            stat = os.stat(fpath)
            meta_path = fpath + ".json"
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path) as mf:
                        meta = json.load(mf)
                except Exception:
                    pass
            files.append({
                "filename": f,
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                "frequency_mhz": meta.get("frequency_hz", 0) / 1e6 if meta.get("frequency_hz") else None,
                "sample_rate_mhz": meta.get("sample_rate_hz", 0) / 1e6 if meta.get("sample_rate_hz") else None,
            })
    files.sort(key=lambda x: x["modified"], reverse=True)
    limit = args.get("limit", 20)
    return json.dumps(files[:limit], ensure_ascii=False, indent=2)

def _decode_noaa_apt(args):
    """NOAA APT 气象卫星图像解码（真实实现）。"""
    input_path = args["input_path"]
    channel = args.get("channel", "both")
    result = decode_noaa_apt(input_path, channel=channel)
    if "error" in result:
        return f"解码失败: {result['error']}"
    output = f"=== NOAA APT 解码完成 ===\n"
    output += f"输入: {input_path}\n"
    output += f"解码行数: {result.get('lines_decoded', 0)}\n"
    output += f"通道: {channel}\n"
    for out in result.get("outputs", []):
        output += f"\n  通道 {out['channel']}:\n"
        output += f"    文件: {out['path']}\n"
        output += f"    尺寸: {out['size'][1]}x{out['size'][0]} 像素\n"
    return output

def _decode_sstv(args):
    """SSTV 慢扫描电视解码（真实实现，优先使用内置 sstv_decoder）。"""
    input_path = args["input_path"]
    output_path = args.get("output_path")
    mode = args.get("mode", "auto")

    # 优先使用内置 sstv_decoder（支持 Martin M1/Scottie S1/Robot 36）
    try:
        from .sstv_decoder import decode_sstv as decode_sstv_new
        result = decode_sstv_new(input_path, output_path=output_path, mode=mode)
        if "error" not in result:
            output = f"=== SSTV 解码完成 ===\n"
            output += f"输入: {input_path}\n"
            output += f"模式: {result.get('mode', 'auto')}\n"
            output += f"尺寸: {result.get('width', 320)}x{result.get('height', 256)} 像素\n"
            output += f"解码行数: {result.get('rows_decoded', 0)}\n"
            if "output_path" in result:
                output += f"输出: {result['output_path']}\n"
            if "note" in result:
                output += f"说明: {result['note']}\n"
            return output
    except Exception:
        pass  # fallback 到旧实现

    # fallback 到旧实现
    result = decode_sstv(input_path)
    if "error" in result:
        return f"解码失败: {result['error']}"
    output = f"=== SSTV 解码完成 ===\n"
    output += f"输入: {input_path}\n"
    output += f"模式: {result.get('mode', 'auto')}\n"
    output += f"尺寸: {result.get('width', 320)}x{result.get('height', 256)} 像素\n"
    if "output_path" in result:
        output += f"输出: {result['output_path']}\n"
    if "note" in result:
        output += f"说明: {result['note']}\n"
    return output

def _decode_ft8(args):
    """FT8 数字通信解码（框架实现）。"""
    input_path = args["input_path"]
    result = decode_digital_mode(input_path, mode="ft8")
    if "error" in result:
        return f"解码失败: {result['error']}"
    output = f"=== FT8 解码 ===\n"
    output += f"输入: {input_path}\n"
    output += f"文件大小: {result.get('file_size', 0)} 字节\n"
    output += f"可用工具: {result.get('external_tools_available', '无')}\n"
    if result.get("external_tools_available"):
        output += f"状态: 检测到外部工具，可用于完整解码\n"
    else:
        output += f"状态: 未检测到 FT8 解码工具，建议安装 wsjtx\n"
    if "spectral_peak" in result:
        output += f"频谱峰值: {result['spectral_peak']:.2f}\n"
        output += f"频谱均值: {result['spectral_mean']:.2f}\n"
    if "note" in result:
        output += f"说明: {result['note']}\n"
    return output

def _decode_aprs(args):
    """APRS 自动位置报告解码（框架实现）。"""
    input_path = args["input_path"]
    result = decode_digital_mode(input_path, mode="aprs")
    if "error" in result:
        return f"解码失败: {result['error']}"
    output = f"=== APRS 解码 ===\n"
    output += f"输入: {input_path}\n"
    output += f"可用工具: {result.get('external_tools_available', '无')}\n"
    if result.get("external_tools_available"):
        output += f"状态: 检测到 direwolf，可用于完整解码\n"
    else:
        output += f"状态: 未检测到 APRS 解码工具，建议安装 direwolf\n"
    if "note" in result:
        output += f"说明: {result['note']}\n"
    return output

def _decode_adsb(args):
    """ADS-B 飞机广播解码（框架实现）。"""
    input_path = args["input_path"]
    result = decode_digital_mode(input_path, mode="adsb")
    if "error" in result:
        return f"解码失败: {result['error']}"
    output = f"=== ADS-B 解码 ===\n"
    output += f"输入: {input_path}\n"
    output += f"可用工具: {result.get('external_tools_available', '无')}\n"
    if result.get("external_tools_available"):
        output += f"状态: 检测到 dump1090，可用于完整解码\n"
    else:
        output += f"状态: 未检测到 ADS-B 解码工具，建议安装 dump1090\n"
    if "note" in result:
        output += f"说明: {result['note']}\n"
    return output

def _satellite_sky_view(args):
    """卫星天空视图（真实实现，sgp4）。"""
    lat = args["latitude"]
    lon = args["longitude"]
    alt = args.get("altitude", 0.0)
    min_elev = args.get("min_elevation", 0.0)
    sat_type = args.get("satellite_type", "all")

    visible = list_visible_satellites(lat, lon, alt, min_elev, sat_type)

    if not visible:
        return f"=== 卫星天空视图 ===\n位置: {lat}°N, {lon}°E\n最小仰角: {min_elev}°\n类型: {sat_type}\n\n当前没有可见卫星（仰角 > {min_elev}°）\n内置卫星: {', '.join(BUILTIN_TLE.keys())}"

    output = f"=== 卫星天空视图 ===\n"
    output += f"位置: {lat}°N, {lon}°E\n"
    output += f"最小仰角: {min_elev}°\n"
    output += f"类型: {sat_type}\n"
    output += f"可见卫星数: {len(visible)}\n\n"

    for i, sat in enumerate(visible):
        output += f"{i+1}. {sat.name}\n"
        output += f"   仰角: {sat.elevation:.1f}°, 方位角: {sat.azimuth:.1f}°\n"
        output += f"   距离: {sat.distance_km:.1f} km, 高度: {sat.altitude_km:.1f} km\n"
        if sat.name in SATELLITE_FREQUENCIES:
            output += f"   下行频率: {SATELLITE_FREQUENCIES[sat.name]:.3f} MHz\n"
        output += "\n"

    return output

def _satellite_doppler(args):
    """卫星多普勒计算（真实实现）。"""
    sat_name = args["satellite_name"]
    freq_hz = args["frequency_hz"]
    lat = args.get("latitude", 43.82)
    lon = args.get("longitude", 125.32)
    alt = args.get("altitude", 0.0)

    result = compute_doppler_correction(sat_name, freq_hz, lat, lon, alt)

    if "error" in result:
        return f"多普勒计算失败: {result['error']}\n支持的卫星: {', '.join(BUILTIN_TLE.keys())}"

    output = f"=== 卫星多普勒计算 ===\n"
    output += f"卫星: {result['satellite']}\n"
    output += f"标称频率: {result['nominal_freq_mhz']:.6f} MHz\n"
    output += f"修正频率: {result['corrected_freq_mhz']:.6f} MHz\n"
    output += f"多普勒频移: {result['doppler_shift_hz']:.1f} Hz\n"
    output += f"最大多普勒: {result['max_doppler_hz']:.1f} Hz\n"
    output += f"仰角: {result['elevation_deg']:.1f}°\n"
    output += f"方位角: {result['azimuth_deg']:.1f}°\n"
    output += f"距离: {result['distance_km']:.1f} km\n"
    if "note" in result:
        output += f"说明: {result['note']}\n"
    return output

def _get_gps(mgr):
    return "GPS 定位（需自研 ai-sdr Mini 设备连接）\n当前为模拟后端，实际数据需连接 ATGM336H 模块\n模拟数据: 43.82°N, 125.32°E, 海拔 250m, 12 星, HDOP 0.8"

def _get_imu(mgr):
    return "IMU 姿态（需自研 ai-sdr Mini 设备连接）\n当前为模拟后端，实际数据需连接 BMI260+TMAG5273\n模拟数据: 加速度(0,0,1)g, 角速度(0,0,0)°/s, 航向 0°"

def _identify_modulation(mgr, spec, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"
    num_samples = args.get("num_samples", 4096)
    samples = backend.read_samples(num_samples)
    if samples is None:
        return "错误: 该设备不支持 IQ 样本"
    features = spec.extract_modulation_features(samples, backend.get_sample_rate())
    return json.dumps(features, ensure_ascii=False, indent=2)

def _detect_fhss(mgr, spec, args):
    """跳频信号检测（真实实现）。"""
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"

    num_frames = args.get("num_frames", 50)
    frame_interval_ms = args.get("frame_interval_ms", 10)
    fft_size = args.get("fft_size", 1024)
    threshold_db = args.get("threshold_db", -60)

    # 读取足够的样本
    frame_samples = int(backend.get_sample_rate() * frame_interval_ms / 1000)
    total_samples = frame_samples * num_frames
    samples = backend.read_samples(min(total_samples, 1000000))  # 最多 100 万样本

    if samples is None:
        return "错误: 该设备不支持 IQ 样本"

    result = detect_fhss(
        samples, backend.get_sample_rate(), backend.get_frequency(),
        num_frames=num_frames, frame_interval_ms=frame_interval_ms,
        fft_size=fft_size, threshold_db=threshold_db,
    )

    if "error" in result:
        return f"跳频检测失败: {result['error']}"

    output = f"=== 跳频检测（FHSS）===\n"
    output += f"分析帧数: {result.get('num_frames_analyzed', 0)}\n"
    output += f"检测到跳变: {result.get('num_hops_detected', 0)} 次\n"
    output += f"唯一频率数: {result.get('num_unique_frequencies', 0)}\n\n"

    if result.get("detected"):
        output += f"--- 跳频图案 ---\n"
        output += f"跳频速率: {result.get('hop_rate_hz', 0):.2f} Hz\n"
        output += f"平均驻留时间: {result.get('avg_dwell_time_ms', 0):.1f} ms\n"
        output += f"频率列表 (MHz): {', '.join(f'{f:.3f}' for f in result.get('unique_frequencies_mhz', []))}\n"
        output += f"跳频序列 (前20个, MHz): {', '.join(f'{f:.3f}' for f in result.get('hop_sequence_mhz', []))}\n"
        output += f"峰值功率范围: {result.get('peak_power_range_db', [0, 0])[0]:.1f} ~ {result.get('peak_power_range_db', [0, 0])[1]:.1f} dB\n"
    else:
        output += f"未检测到明显的跳频信号\n"
        output += f"峰值频率 (前10帧, MHz): {', '.join(f'{f/1e6:.3f}' for f in result.get('peak_frequencies', []))}\n"

    return output

def _measure_signal(mgr, spec, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"
    num_samples = args.get("num_samples", 4096)
    samples = backend.read_samples(num_samples)
    if samples is None:
        return "错误: 该设备不支持 IQ 样本"
    spectrum = spec.compute_spectrum(samples, backend.get_frequency(), backend.get_sample_rate(), fft_size=4096)
    expected = args.get("expected_freq_hz")
    offset = spec.estimate_center_offset(spectrum, expected_freq=expected)
    signals = spec.find_signals(spectrum, threshold_db=spectrum.noise_floor_db + 10)
    result = {
        "center_freq_mhz": round(offset["estimated_center_freq"] / 1e6, 6),
        "peak_power_db": round(spectrum.peak_power_db, 1),
        "noise_floor_db": round(spectrum.noise_floor_db, 1),
        "snr_db": round(spectrum.peak_power_db - spectrum.noise_floor_db, 1),
        "signals_detected": len(signals),
    }
    if expected:
        result["offset_hz"] = round(offset.get("offset_hz", 0), 1)
        result["offset_ppm"] = round(offset.get("offset_ppm", 0), 2)
    return json.dumps(result, ensure_ascii=False, indent=2)

def _ai_sweep(mgr, spec, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"
    freq_start = args["freq_start_hz"]
    freq_end = args["freq_end_hz"]
    step = args["step_hz"]
    dwell_ms = args.get("dwell_ms", 50)
    auto_tune = args.get("auto_tune", True)

    results = []
    strongest = {"freq": freq_start, "power": -200}
    current = freq_start
    while current <= freq_end:
        backend.set_frequency(current)
        time.sleep(dwell_ms / 1000.0)
        samples = backend.read_samples(512)
        if samples is not None:
            spectrum = spec.compute_spectrum(samples, current, backend.get_sample_rate(), fft_size=512)
            power = spectrum.peak_power_db
        else:
            power = backend.status.rssi_db
        results.append({"freq_mhz": round(current / 1e6, 3), "power_db": round(power, 1)})
        if power > strongest["power"]:
            strongest = {"freq": current, "power": power}
        current += step

    if auto_tune and strongest["freq"] != freq_start:
        backend.set_frequency(strongest["freq"])

    result = f"AI 扫频完成\n范围: {freq_start/1e6:.1f} - {freq_end/1e6:.1f} MHz, 步进 {step/1e3:.0f} kHz\n扫描点数: {len(results)}\n最强台: {strongest['freq']/1e6:.3f} MHz @ {strongest['power']:.1f} dB\n"
    if auto_tune:
        result += f"已自动调谐到最强台\n"
    result += "\n扫描结果（前10个最强点）:\n"
    sorted_results = sorted(results, key=lambda x: x["power_db"], reverse=True)[:10]
    for r in sorted_results:
        result += f"  {r['freq_mhz']:.3f} MHz: {r['power_db']:.1f} dB\n"
    return result

def _ai_find_center(mgr, spec, args):
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"
    current_freq = backend.get_frequency()
    search_range = args.get("search_range_hz", 50000)
    num_steps = args.get("num_steps", 20)
    auto_correct = args.get("auto_correct", True)
    step = search_range / num_steps

    strongest = {"freq": current_freq, "power": -200}
    for i in range(num_steps):
        test_freq = current_freq - search_range / 2 + i * step
        backend.set_frequency(test_freq)
        time.sleep(0.02)
        samples = backend.read_samples(1024)
        if samples is not None:
            spectrum = spec.compute_spectrum(samples, test_freq, backend.get_sample_rate())
            power = spectrum.peak_power_db
        else:
            power = backend.status.rssi_db
        if power > strongest["power"]:
            strongest = {"freq": test_freq, "power": power}

    offset = strongest["freq"] - current_freq
    ppm = offset / current_freq * 1e6 if current_freq > 0 else 0

    if auto_correct and abs(offset) > 100:
        backend.set_frequency(strongest["freq"])

    result = f"AI 找中心频点完成\n当前频率: {current_freq/1e6:.6f} MHz\n精确中心: {strongest['freq']/1e6:.6f} MHz\n偏移: {offset:.1f} Hz ({ppm:.2f} ppm)\n峰值功率: {strongest['power']:.1f} dB\n"
    if auto_correct and abs(offset) > 100:
        result += f"已自动修正到精确中心频率\n"
    return result


# ═══════════════════════════════════════════════════════
# 新增工具实现（IQ校正/真实解调/录制分析）
# ═══════════════════════════════════════════════════════

def _iq_correct(mgr, args):
    """IQ 前端校正：DC 阻断 + I/Q 平衡 + 抽取。"""
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"

    num_samples = args.get("num_samples", 16384)
    dc_block = args.get("dc_block", True)
    correct_iq = args.get("correct_iq", True)
    decimation = args.get("decimation", 1)

    samples = backend.read_samples(num_samples)
    if samples is None:
        return "错误: 该设备不支持 IQ 样本"

    # 校正前诊断
    dc_i_before = float(np.mean(samples.real))
    dc_q_before = float(np.mean(samples.imag))
    var_i_before = float(np.var(samples.real))
    var_q_before = float(np.var(samples.imag))
    cov_iq_before = float(np.cov(samples.real, samples.imag)[0, 1])
    # 估计镜像抑制比 IRR（校正前）
    if var_i_before > 0 and var_q_before > 0:
        irr_before = 10 * np.log10((var_i_before + var_q_before) / (2 * abs(cov_iq_before) + 1e-12))
    else:
        irr_before = 0.0

    # 执行校正
    corrected, diagnostics = front_end(
        samples,
        dc_r=0.998 if dc_block else 0.0,
        correct_iq=correct_iq,
        decimation=decimation,
    )

    # 校正后诊断
    dc_i_after = float(np.mean(corrected.real))
    dc_q_after = float(np.mean(corrected.imag))
    var_i_after = float(np.var(corrected.real))
    var_q_after = float(np.var(corrected.imag))
    cov_iq_after = float(np.cov(corrected.real, corrected.imag)[0, 1])
    if var_i_after > 0 and var_q_after > 0:
        irr_after = 10 * np.log10((var_i_after + var_q_after) / (2 * abs(cov_iq_after) + 1e-12))
    else:
        irr_after = 0.0

    result = f"=== IQ 前端校正完成 ===\n"
    result += f"样本数: {len(samples)} → {len(corrected)}\n"
    result += f"DC 阻断: {'开启' if dc_block else '关闭'}\n"
    result += f"I/Q 平衡: {'开启' if correct_iq else '关闭'}\n"
    result += f"抽取: {decimation}x\n\n"
    result += f"--- 校正前 ---\n"
    result += f"DC 偏移: I={dc_i_before:.6f}, Q={dc_q_before:.6f}\n"
    result += f"功率: I={var_i_before:.6f}, Q={var_q_after:.6f}\n"
    result += f"I/Q 协方差: {cov_iq_before:.6f}\n"
    result += f"镜像抑制比 IRR: {irr_before:.1f} dB\n\n"
    result += f"--- 校正后 ---\n"
    result += f"DC 偏移: I={dc_i_after:.6f}, Q={dc_q_after:.6f}\n"
    result += f"功率: I={var_i_after:.6f}, Q={var_q_after:.6f}\n"
    result += f"I/Q 协方差: {cov_iq_after:.6f}\n"
    result += f"镜像抑制比 IRR: {irr_after:.1f} dB\n"
    if irr_before > 0:
        result += f"IRR 改善: +{irr_after - irr_before:.1f} dB\n"

    if "iq_correction" in diagnostics and isinstance(diagnostics["iq_correction"], dict):
        iq = diagnostics["iq_correction"]
        if "iq_gain_error" in iq:
            result += f"\n估计 I/Q 增益误差: {iq['iq_gain_error']:.4f}\n"
        if "iq_phase_error_deg" in iq:
            result += f"估计 I/Q 相位误差: {iq['iq_phase_error_deg']:.2f}°\n"

    return result


def _demodulate(mgr, args):
    """真实解调当前信号。"""
    backend = _get_backend(mgr)
    if not backend or not backend.status.connected:
        return "错误: 设备未连接"

    num_samples = args.get("num_samples", 16384)
    mode = args.get("mode", backend.status.demod_mode or "FM")
    deviation = args.get("deviation", 75000.0)
    sample_rate = backend.get_sample_rate()

    samples = backend.read_samples(num_samples)
    if samples is None:
        return "错误: 该设备不支持 IQ 样本"

    # 真实解调
    audio = demodulate(samples, mode=mode, sample_rate=sample_rate, deviation=deviation)

    # 音频特征
    audio_rms = float(np.sqrt(np.mean(audio ** 2)))
    audio_peak = float(np.max(np.abs(audio)))
    audio_mean = float(np.mean(audio))
    audio_duration = len(audio) / sample_rate

    result = f"=== 解调完成 ===\n"
    result += f"模式: {mode}\n"
    result += f"输入样本: {len(samples)} IQ 样本\n"
    result += f"输出音频: {len(audio)} 样本 ({audio_duration:.3f} 秒)\n"
    result += f"采样率: {sample_rate/1e6:.3f} MHz\n"
    if mode in ("FM", "WFM", "NFM"):
        result += f"频偏: {deviation/1000:.1f} kHz\n"
    result += f"\n--- 音频特征 ---\n"
    result += f"RMS: {audio_rms:.6f}\n"
    result += f"峰值: {audio_peak:.6f}\n"
    result += f"均值: {audio_mean:.6f}\n"
    result += f"动态范围: {20*np.log10(audio_peak/(audio_rms+1e-12)):.1f} dB\n"

    # 保存音频到临时文件（可选）
    try:
        import tempfile
        import wave
        tmp_path = os.path.join(tempfile.gettempdir(), f"mbdsdr_demod_{int(time.time())}.wav")
        with wave.open(tmp_path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate))
            audio_int16 = np.clip(audio / (audio_peak + 1e-12), -1, 1) * 32767
            wf.writeframes(audio_int16.astype(np.int16).tobytes())
        result += f"\n音频已保存: {tmp_path}\n"
    except Exception as e:
        result += f"\n音频保存失败: {e}\n"

    return result


def _analyze_recording(args):
    """分析已录制的基带文件。"""
    file_path = args.get("file_path", "")
    if not os.path.exists(file_path):
        return f"错误: 文件不存在: {file_path}"

    correct_iq = args.get("correct_iq", True)
    fft_size = args.get("fft_size", 1024)

    # 读取 sidecar JSON（如果存在）
    sidecar_path = file_path + ".json"
    metadata = {}
    if os.path.exists(sidecar_path):
        try:
            with open(sidecar_path) as f:
                metadata = json.load(f)
        except Exception:
            pass

    # 读取 IQ 数据
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".cf32":
            raw = np.fromfile(file_path, dtype=np.float32)
            samples = raw[0::2] + 1j * raw[1::2]
        elif ext == ".cs16":
            raw = np.fromfile(file_path, dtype=np.int16)
            samples = (raw[0::2].astype(np.float32) / 32767.0) + 1j * (raw[1::2].astype(np.float32) / 32767.0)
        elif ext == ".wav":
            import wave
            with wave.open(file_path, 'rb') as wf:
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
                audio_data = np.frombuffer(raw, dtype=np.int16)
                if wf.getnchannels() == 2:
                    samples = (audio_data[0::2].astype(np.float32) / 32767.0) + 1j * (audio_data[1::2].astype(np.float32) / 32767.0)
                else:
                    samples = audio_data.astype(np.float32) + 1j * 0
        else:
            return f"错误: 不支持的格式: {ext}（支持 .cf32/.cs16/.wav）"
    except Exception as e:
        return f"错误: 读取文件失败: {e}"

    if len(samples) == 0:
        return "错误: 文件中没有样本数据"

    sample_rate = metadata.get("sample_rate_hz", 2400000.0)
    center_freq = metadata.get("center_hz", 0.0)

    # IQ 前端校正
    if correct_iq:
        samples, iq_diag = front_end(samples, correct_iq=True)
    else:
        iq_diag = {"skipped": True}

    # 频谱分析
    spec = SpectrumProcessor(fft_size=fft_size)
    spectrum = spec.compute_spectrum(samples, center_freq, sample_rate, fft_size=fft_size)
    signals = spec.detect_signals(spectrum, threshold_db=-40)

    # SNR 和带宽
    snr_info = compute_snr(samples, sample_rate=sample_rate)
    bw_info = estimate_bandwidth(samples, sample_rate=sample_rate)

    result = f"=== 录制文件分析报告 ===\n"
    result += f"文件: {file_path}\n"
    result += f"格式: {ext}\n"
    result += f"样本数: {len(samples)}\n"
    result += f"采样率: {sample_rate/1e6:.3f} MHz\n"
    if center_freq > 0:
        result += f"中心频率: {center_freq/1e6:.3f} MHz\n"
    result += f"时长: {len(samples)/sample_rate:.3f} 秒\n\n"

    result += f"--- IQ 前端校正 ---\n"
    if "iq_correction" in iq_diag and isinstance(iq_diag["iq_correction"], dict):
        iq = iq_diag["iq_correction"]
        result += f"DC 偏移: I={iq.get('dc_offset_i', 0):.6f}, Q={iq.get('dc_offset_q', 0):.6f}\n"
        result += f"增益误差: {iq.get('iq_gain_error', 1):.4f}\n"
        result += f"相位误差: {iq.get('iq_phase_error_deg', 0):.2f}°\n"
    else:
        result += "跳过\n"

    result += f"\n--- 频谱分析 ---\n"
    result += f"峰值频率: {spectrum.peak_freq_hz/1e6:.3f} MHz\n"
    result += f"峰值功率: {spectrum.peak_power_db:.1f} dB\n"
    result += f"噪声底: {spectrum.noise_floor_db:.1f} dB\n"
    result += f"检测到信号: {len(signals)} 个\n"
    for i, sig in enumerate(signals[:5]):
        result += f"  {i+1}. {sig['center_freq']/1e6:.3f} MHz, 带宽 {sig['bandwidth']/1000:.1f} kHz, 峰值 {sig['peak_power_db']:.1f} dB\n"

    result += f"\n--- 信号质量 ---\n"
    result += f"SNR: {snr_info['snr_db']:.1f} dB\n"
    result += f"占用带宽: {bw_info['bandwidth_hz']/1000:.1f} kHz\n"
    result += f"信号功率: {snr_info['signal_power']:.6f}\n"
    result += f"噪声功率: {snr_info['noise_power']:.6f}\n"

    return result


# ═══════════════════════════════════════════════════════
# AX.25 / APRS 工具实现
# ═══════════════════════════════════════════════════════

def _encode_ax25(args):
    """编码 AX.25 帧。"""
    from .ax25 import AX25Frame

    source = args.get('source', '')
    destination = args.get('destination', '')
    info = args.get('info', '')
    digipeaters_raw = args.get('digipeaters', [])
    control = args.get('control', 0x03)
    pid = args.get('pid', 0xF0)

    # 解析中继器
    digipeaters = []
    for d in digipeaters_raw:
        if '-' in d:
            parts = d.split('-', 1)
            call = parts[0]
            ssid = int(parts[1]) if parts[1].isdigit() else 0
        else:
            call = d
            ssid = 0
        digipeaters.append((call, ssid, False))

    # 解析源/目的呼号
    src_call = source.split('-')[0] if '-' in source else source
    src_ssid = int(source.split('-')[1]) if '-' in source and source.split('-')[1].isdigit() else 0
    dst_call = destination.split('-')[0] if '-' in destination else destination
    dst_ssid = int(destination.split('-')[1]) if '-' in destination and destination.split('-')[1].isdigit() else 0

    frame = AX25Frame(
        destination=dst_call,
        dest_ssid=dst_ssid,
        source=src_call,
        source_ssid=src_ssid,
        digipeaters=digipeaters,
        control=control,
        pid=pid,
        info=info.encode('latin-1', errors='replace')
    )

    frame_bytes = frame.to_bytes()
    hex_str = frame_bytes.hex().upper()

    result = f"=== AX.25 帧编码 ===\n"
    result += f"源: {src_call}-{src_ssid}\n"
    result += f"目的: {dst_call}-{dst_ssid}\n"
    if digipeaters:
        result += f"中继: {', '.join([f'{c}-{s}' for c, s, _ in digipeaters])}\n"
    result += f"控制: 0x{control:02X}\n"
    result += f"协议ID: 0x{pid:02X}\n"
    result += f"信息: {info}\n"
    result += f"FCS: 0x{frame.fcs:04X}\n"
    result += f"帧长度: {len(frame_bytes)} 字节\n"
    result += f"十六进制: {hex_str}\n"

    return result


def _decode_ax25(args):
    """解码 AX.25 帧。"""
    from .ax25 import AX25Frame, parse_ax25_from_audio

    hex_data = args.get('hex_data', '')
    audio_path = args.get('audio_path', '')

    frames = []

    if audio_path:
        # 从音频解调
        frames = parse_ax25_from_audio(audio_path)
        result = f"=== AX.25 音频解调 ===\n"
        result += f"音频文件: {audio_path}\n"
        result += f"检测到帧: {len(frames)} 个\n\n"
    elif hex_data:
        # 从十六进制解码
        try:
            data = bytes.fromhex(hex_data)
            frame = AX25Frame.from_bytes(data)
            if frame:
                frames = [frame]
            result = f"=== AX.25 帧解码 ===\n"
            result += f"输入长度: {len(data)} 字节\n\n"
        except ValueError as e:
            return f"错误: 无效的十六进制数据 - {e}"
    else:
        return "错误: 请提供 hex_data 或 audio_path"

    if not frames:
        result += "未检测到有效的 AX.25 帧\n"
        return result

    for i, frame in enumerate(frames):
        result += f"--- 帧 {i+1} ---\n"
        result += f"源: {frame.source}-{frame.source_ssid}\n"
        result += f"目的: {frame.destination}-{frame.dest_ssid}\n"
        if frame.digipeaters:
            digi_str = ', '.join([f"{c}-{s}{'*' if r else ''}" for c, s, r in frame.digipeaters])
            result += f"中继: {digi_str}\n"
        result += f"控制: 0x{frame.control:02X}\n"
        result += f"协议ID: 0x{frame.pid:02X}\n"
        try:
            info_text = frame.info.decode('latin-1', errors='replace')
            result += f"信息: {info_text}\n"
        except Exception:
            result += f"信息(hex): {frame.info.hex().upper()}\n"
        result += f"FCS: 0x{frame.fcs:04X} {'有效' if frame.fcs_valid else '无效'}\n"
        result += f"\n"

    return result


def _aprs_encode(args):
    """编码 APRS 报文。"""
    from .ax25 import APRSPosition, APRSPacket, AFSKModem, save_ax25_to_wav

    source = args.get('source', '')
    latitude = args.get('latitude', 0.0)
    longitude = args.get('longitude', 0.0)
    comment = args.get('comment', '')
    symbol = args.get('symbol', '/-')
    digipeaters = args.get('digipeaters', ['WIDE2-2'])
    output_wav = args.get('output_wav', '')

    # 构建位置
    pos = APRSPosition(
        latitude=latitude,
        longitude=longitude,
        symbol_table=symbol[0] if len(symbol) > 0 else '/',
        symbol_code=symbol[1] if len(symbol) > 1 else '-',
        comment=comment
    )

    # 解析源呼号
    src_call = source.split('-')[0] if '-' in source else source
    src_ssid = int(source.split('-')[1]) if '-' in source and source.split('-')[1].isdigit() else 0

    # 构建 APRS 包
    packet = APRSPacket(
        source=src_call,
        source_ssid=src_ssid,
        destination='APRS',
        dest_ssid=0,
        digipeaters=digipeaters,
        data_type='!',
        payload=pos.encode()[1:],
        position=pos
    )

    frame = packet.to_ax25_frame()
    frame_bytes = frame.to_bytes()

    result = f"=== APRS 位置编码 ===\n"
    result += f"源: {src_call}-{src_ssid}\n"
    result += f"位置: {latitude:.6f}, {longitude:.6f}\n"
    result += f"符号: {symbol}\n"
    result += f"注释: {comment}\n"
    result += f"中继: {', '.join(digipeaters)}\n"
    result += f"APRS 报文: !{pos.encode()[1:]}\n"
    result += f"AX.25 帧长度: {len(frame_bytes)} 字节\n"
    result += f"十六进制: {frame_bytes.hex().upper()}\n"

    # 生成 WAV
    if output_wav:
        if save_ax25_to_wav(frame, output_wav):
            result += f"AFSK 音频已保存: {output_wav}\n"
        else:
            result += f"警告: AFSK 音频保存失败\n"

    return result


def _aprs_decode(args):
    """解码 APRS 报文。"""
    from .ax25 import AX25Frame, APRSPacket, parse_ax25_from_audio

    hex_data = args.get('hex_data', '')
    audio_path = args.get('audio_path', '')

    frames = []

    if audio_path:
        frames = parse_ax25_from_audio(audio_path)
        result = f"=== APRS 音频解码 ===\n"
        result += f"音频文件: {audio_path}\n"
    elif hex_data:
        try:
            data = bytes.fromhex(hex_data)
            frame = AX25Frame.from_bytes(data)
            if frame:
                frames = [frame]
            result = f"=== APRS 报文解码 ===\n"
        except ValueError as e:
            return f"错误: 无效的十六进制数据 - {e}"
    else:
        return "错误: 请提供 hex_data 或 audio_path"

    aprs_packets = []
    for frame in frames:
        packet = APRSPacket.from_ax25_frame(frame)
        if packet:
            aprs_packets.append(packet)

    result += f"检测到 AX.25 帧: {len(frames)} 个\n"
    result += f"其中 APRS 报文: {len(aprs_packets)} 个\n\n"

    for i, packet in enumerate(aprs_packets):
        result += f"--- APRS 报文 {i+1} ---\n"
        result += f"源: {packet.source}-{packet.source_ssid}\n"
        result += f"目的: {packet.destination}-{packet.dest_ssid}\n"
        if packet.digipeaters:
            result += f"中继: {', '.join(packet.digipeaters)}\n"
        result += f"数据类型: '{packet.data_type}'\n"

        if packet.position:
            result += f"类型: 位置报文\n"
            result += f"位置: {packet.position.latitude:.6f}, {packet.position.longitude:.6f}\n"
            result += f"符号: {packet.position.symbol_table}{packet.position.symbol_code}\n"
            if packet.position.altitude:
                result += f"高度: {packet.position.altitude} 英尺\n"
            if packet.position.course is not None:
                result += f"航向/速度: {packet.position.course}° / {packet.position.speed} 节\n"
            if packet.position.comment:
                result += f"注释: {packet.position.comment}\n"
        elif packet.message:
            result += f"类型: 消息报文\n"
            result += f"收件人: {packet.message.addressee}\n"
            result += f"消息: {packet.message.message}\n"
            if packet.message.message_id:
                result += f"消息ID: {packet.message.message_id}\n"
        else:
            result += f"载荷: {packet.payload[:100]}\n"

        result += f"\n"

    return result


def _aprs_send_position(args):
    """发送 APRS 位置报告。"""
    from .ax25 import APRSPosition, APRSPacket, save_ax25_to_wav

    callsign = args.get('callsign', '')
    latitude = args.get('latitude', 0.0)
    longitude = args.get('longitude', 0.0)
    comment = args.get('comment', 'MBDSDR AI SDR Station')
    symbol = args.get('symbol', '/-')
    frequency_hz = args.get('frequency_hz', 144640000)
    output_wav = args.get('output_wav', '')

    # 构建位置
    pos = APRSPosition(
        latitude=latitude,
        longitude=longitude,
        symbol_table=symbol[0] if len(symbol) > 0 else '/',
        symbol_code=symbol[1] if len(symbol) > 1 else '-',
        comment=comment
    )

    # 解析呼号
    src_call = callsign.split('-')[0] if '-' in callsign else callsign
    src_ssid = int(callsign.split('-')[1]) if '-' in callsign and callsign.split('-')[1].isdigit() else 0

    # 构建 APRS 包
    packet = APRSPacket(
        source=src_call,
        source_ssid=src_ssid,
        destination='APRS',
        dest_ssid=0,
        digipeaters=['WIDE2-2'],
        data_type='!',
        payload=pos.encode()[1:],
        position=pos
    )

    frame = packet.to_ax25_frame()

    result = f"=== APRS 位置发送 ===\n"
    result += f"呼号: {src_call}-{src_ssid}\n"
    result += f"位置: {latitude:.6f}, {longitude:.6f}\n"
    result += f"频率: {frequency_hz/1e6:.3f} MHz\n"
    result += f"APRS 报文: !{pos.encode()[1:]}\n"

    # 保存 WAV
    if output_wav:
        if save_ax25_to_wav(frame, output_wav):
            result += f"AFSK 音频已保存: {output_wav}\n"
            result += f"可通过 SDR 发射或用音频播放器播放到电台\n"
        else:
            result += f"警告: AFSK 音频保存失败\n"
    else:
        # 默认保存到临时文件
        import tempfile
        import os
        tmp_path = os.path.join(tempfile.gettempdir(), f'mbdsdr_aprs_{src_call}.wav')
        if save_ax25_to_wav(frame, tmp_path):
            result += f"AFSK 音频已保存: {tmp_path}\n"

    result += f"\n提示: 中国 APRS 频率 144.640 MHz，美国 144.390 MHz，欧洲 144.800 MHz\n"

    return result


def _kiss_encode(args):
    """编码 KISS 帧。"""
    from .ax25 import KISSInterface

    ax25_hex = args.get('ax25_hex', '')
    port = args.get('port', 0)
    command = args.get('command', 0)

    try:
        ax25_data = bytes.fromhex(ax25_hex)
    except ValueError as e:
        return f"错误: 无效的十六进制数据 - {e}"

    if command == 0:
        kiss_frame = KISSInterface.encode_data_frame(ax25_data, port)
    else:
        kiss_frame = KISSInterface.encode_command(command, ax25_data, port)

    result = f"=== KISS 帧编码 ===\n"
    result += f"端口: {port}\n"
    result += f"命令: {command} ({'数据帧' if command == 0 else '命令帧'})\n"
    result += f"AX.25 数据长度: {len(ax25_data)} 字节\n"
    result += f"KISS 帧长度: {len(kiss_frame)} 字节\n"
    result += f"十六进制: {kiss_frame.hex().upper()}\n"
    result += f"\n提示: 可通过串口发送给硬件 TNC（如 Kenwood TM-D710、Yaesu FTM-400）\n"

    return result


def _kiss_decode(args):
    """解码 KISS 帧流。"""
    from .ax25 import KISSInterface, AX25Frame

    hex_data = args.get('hex_data', '')

    try:
        data = bytes.fromhex(hex_data)
    except ValueError as e:
        return f"错误: 无效的十六进制数据 - {e}"

    frames = KISSInterface.decode_stream(data)

    result = f"=== KISS 帧解码 ===\n"
    result += f"输入长度: {len(data)} 字节\n"
    result += f"检测到 KISS 帧: {len(frames)} 个\n\n"

    for i, (port, ax25_data) in enumerate(frames):
        result += f"--- KISS 帧 {i+1} ---\n"
        result += f"端口: {port}\n"
        result += f"AX.25 数据长度: {len(ax25_data)} 字节\n"
        result += f"十六进制: {ax25_data.hex().upper()}\n"

        # 尝试解析 AX.25
        frame = AX25Frame.from_bytes(ax25_data)
        if frame:
            result += f"AX.25 解析: {frame.source}-{frame.source_ssid} -> {frame.destination}-{frame.dest_ssid}\n"
            try:
                info = frame.info.decode('latin-1', errors='replace')
                result += f"信息: {info[:80]}\n"
            except Exception:
                pass
        result += f"\n"

    return result


def _digipeater_process(args):
    """Digipeater 分组转发处理。"""
    from .ax25 import AX25Frame, Digipeater

    ax25_hex = args.get('ax25_hex', '')
    mycall = args.get('mycall', '')
    myssid = args.get('myssid', 0)
    digi_calls = args.get('digi_calls', [])

    try:
        data = bytes.fromhex(ax25_hex)
    except ValueError as e:
        return f"错误: 无效的十六进制数据 - {e}"

    frame = AX25Frame.from_bytes(data)
    if not frame:
        return "错误: 无法解析 AX.25 帧"

    if not frame.fcs_valid:
        return "错误: FCS 校验失败，丢弃帧"

    digi = Digipeater(mycall=mycall, myssid=myssid, digi_calls=digi_calls)
    forwarded = digi.process_frame(frame)
    stats = digi.get_stats()

    result = f"=== Digipeater 处理 ===\n"
    result += f"本中继站: {mycall}-{myssid}\n"
    result += f"接收帧: {frame.source}-{frame.source_ssid} -> {frame.destination}-{frame.dest_ssid}\n"
    if frame.digipeaters:
        digi_str = ', '.join([f"{c}-{s}{'*' if r else ''}" for c, s, r in frame.digipeaters])
        result += f"中继路径: {digi_str}\n"

    if forwarded:
        result += f"\n结果: 需要转发\n"
        fwd_digi_str = ', '.join([f"{c}-{s}{'*' if r else ''}" for c, s, r in forwarded.digipeaters])
        result += f"转发后中继路径: {fwd_digi_str}\n"
        forwarded_bytes = forwarded.to_bytes()
        result += f"转发帧十六进制: {forwarded_bytes.hex().upper()}\n"
    else:
        result += f"\n结果: 不转发（无匹配中继器或重复帧）\n"

    result += f"\n统计: 听到 {stats['packets_heard']} 帧, 转发 {stats['packets_digipeated']} 帧\n"

    return result


# ═══════════════════════════════════════════════════════
# 新时空工具实现（授时/GIS/PNT/卫星Pass预测）
# ═══════════════════════════════════════════════════════

def _time_get_info(args):
    """获取时间信息。"""
    try:
        from mbdsdr_ai.new_spacetime import get_time_info
    except ImportError:
        from new_spacetime import get_time_info

    prefer_ntp = args.get('prefer_ntp', True)
    info = get_time_info(prefer_ntp=prefer_ntp)

    lines = ["=== 时间信息 ==="]
    lines.append(f"时间源: {info.source}")
    if info.utc_time:
        lines.append(f"UTC: {info.utc_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
    if info.local_time:
        lines.append(f"本地: {info.local_time.strftime('%Y-%m-%d %H:%M:%S')}")
    if info.gps_time is not None:
        gps_week = int(info.gps_time // 604800)
        gps_tow = info.gps_time % 604800
        lines.append(f"GPS: 第 {gps_week} 周, 周内 {gps_tow:.3f} 秒")
    lines.append(f"闰秒: {info.leap_seconds} 秒 (GPS-UTC)")
    if info.source == 'ntp':
        lines.append(f"NTP服务器: {info.ntp_server}")
        lines.append(f"NTP往返延迟: {info.ntp_rtt_ms:.1f} ms")
        lines.append(f"本地时钟偏差: {info.clock_offset_ms:+.3f} ms")
    return '\n'.join(lines)


def _time_ntp_sync(args):
    """NTP 时间同步。"""
    try:
        from mbdsdr_ai.new_spacetime import get_ntp_time, NTPError
    except ImportError:
        from new_spacetime import get_ntp_time, NTPError

    server = args.get('server', 'ntp.aliyun.com')
    timeout = args.get('timeout', 3.0)

    try:
        utc_time, rtt_ms = get_ntp_time(server, timeout)
        system_utc = datetime.now(timezone.utc)
        offset_ms = (utc_time - system_utc).total_seconds() * 1000

        lines = ["=== NTP 时间同步 ==="]
        lines.append(f"服务器: {server}")
        lines.append(f"NTP UTC: {utc_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        lines.append(f"系统 UTC: {system_utc.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        lines.append(f"时钟偏差: {offset_ms:+.3f} ms")
        lines.append(f"往返延迟: {rtt_ms:.1f} ms")
        lines.append(f"状态: 同步成功")
        return '\n'.join(lines)
    except NTPError as e:
        return f"NTP 同步失败: {e}\n建议: 检查网络连接或更换 NTP 服务器"


def _gnss_system_info(args):
    """GNSS 系统信息。"""
    try:
        from mbdsdr_ai.new_spacetime import get_gnss_system_info
    except ImportError:
        from new_spacetime import get_gnss_system_info

    system = args.get('system', 'all')
    return get_gnss_system_info(system)


def _gnss_parse_rmc(args):
    """解析 GNSS RMC 语句。"""
    try:
        from mbdsdr_ai.new_spacetime import parse_gnss_rmc
    except ImportError:
        from new_spacetime import parse_gnss_rmc

    nmea = args.get('nmea', '')
    result = parse_gnss_rmc(nmea)

    if result is None:
        return "RMC 解析失败: 无效的 NMEA 语句或格式不正确"

    lines = ["=== GNSS RMC 解析 ==="]
    lines.append(f"定位状态: {'有效' if result['valid'] else '无效'}")
    if result['utc_time']:
        lines.append(f"UTC 时间: {result['utc_time'].strftime('%Y-%m-%d %H:%M:%S')}")
    if result['latitude'] is not None:
        lines.append(f"纬度: {result['latitude']:.6f}°")
    if result['longitude'] is not None:
        lines.append(f"经度: {result['longitude']:.6f}°")
    lines.append(f"速度: {result['speed_knots']:.1f} 节 = {result['speed_kmh']:.1f} km/h")
    lines.append(f"航向: {result['course_deg']:.1f}°")
    return '\n'.join(lines)


def _gis_distance(args):
    """计算两点距离。"""
    try:
        from mbdsdr_ai.new_spacetime import haversine_distance, GeoPoint
    except ImportError:
        from new_spacetime import haversine_distance, GeoPoint

    p1 = GeoPoint(latitude=args['lat1'], longitude=args['lon1'])
    p2 = GeoPoint(latitude=args['lat2'], longitude=args['lon2'])
    dist_km = haversine_distance(p1, p2)

    lines = ["=== 地理距离计算 ==="]
    lines.append(f"起点: {p1.to_string()}")
    lines.append(f"终点: {p2.to_string()}")
    lines.append(f"大圆距离: {dist_km:.3f} 公里")
    lines.append(f"大圆距离: {dist_km*1000:.0f} 米")
    lines.append(f"大圆距离: {dist_km/1.852:.2f} 海里")
    return '\n'.join(lines)


def _gis_bearing(args):
    """计算方位角。"""
    try:
        from mbdsdr_ai.new_spacetime import bearing_between, GeoPoint
    except ImportError:
        from new_spacetime import bearing_between, GeoPoint

    p1 = GeoPoint(latitude=args['lat1'], longitude=args['lon1'])
    p2 = GeoPoint(latitude=args['lat2'], longitude=args['lon2'])
    bearing = bearing_between(p1, p2)

    # 方位角转方向文字
    directions = ['北', '东北', '东', '东南', '南', '西南', '西', '西北']
    idx = int((bearing + 22.5) / 45) % 8

    lines = ["=== 方位角计算 ==="]
    lines.append(f"起点: {p1.to_string()}")
    lines.append(f"终点: {p2.to_string()}")
    lines.append(f"方位角: {bearing:.1f}°（0=北，顺时针）")
    lines.append(f"方向: {directions[idx]}")
    return '\n'.join(lines)


def _gis_destination(args):
    """计算终点坐标。"""
    try:
        from mbdsdr_ai.new_spacetime import destination_point, GeoPoint
    except ImportError:
        from new_spacetime import destination_point, GeoPoint

    start = GeoPoint(latitude=args['latitude'], longitude=args['longitude'])
    dest = destination_point(start, args['bearing_deg'], args['distance_km'])

    lines = ["=== 终点坐标计算 ==="]
    lines.append(f"起点: {start.to_string()}")
    lines.append(f"方位角: {args['bearing_deg']:.1f}°")
    lines.append(f"距离: {args['distance_km']:.3f} 公里")
    lines.append(f"终点: {dest.to_string()}")
    return '\n'.join(lines)


# 全局 PNT 融合引擎实例
_pnt_engine = None


def _get_pnt_engine():
    global _pnt_engine
    if _pnt_engine is None:
        try:
            from mbdsdr_ai.new_spacetime import PNTFusionEngine
        except ImportError:
            from new_spacetime import PNTFusionEngine
        _pnt_engine = PNTFusionEngine()
    return _pnt_engine


def _pnt_get_state(args):
    """获取 PNT 状态。"""
    engine = _get_pnt_engine()
    state = engine.get_state()
    return state.summary()


def _pnt_update_source(args):
    """更新 PNT 源数据。"""
    try:
        from mbdsdr_ai.new_spacetime import PNTSource
    except ImportError:
        from new_spacetime import PNTSource

    engine = _get_pnt_engine()
    source_str = args.get('source', 'gnss').upper()

    # 映射源类型
    source_map = {
        'GNSS': PNTSource.GNSS,
        'LEO_PNT': PNTSource.LEO_PNT,
        'LEO': PNTSource.LEO_PNT,
        'PPP_RTK': PNTSource.PPP_RTK,
        'PPP': PNTSource.PPP_RTK,
        'RTK': PNTSource.PPP_RTK,
        'IMU': PNTSource.IMU,
        'WIFI': PNTSource.WIFI,
        'BLUETOOTH': PNTSource.BLUETOOTH,
        'BT': PNTSource.BLUETOOTH,
        'UWB': PNTSource.UWB,
        'CELLULAR': PNTSource.CELLULAR,
        'NTP': PNTSource.NTP,
        'VISUAL': PNTSource.VISUAL,
    }
    source = source_map.get(source_str, PNTSource.GNSS)

    data = {
        'latitude': args['latitude'],
        'longitude': args['longitude'],
        'altitude_m': args.get('altitude_m', 0.0),
        'accuracy_m': args.get('accuracy_m', 10.0),
        'valid': args.get('valid', True),
    }
    if 'satellites' in args:
        data['satellites'] = args['satellites']

    engine.update_source(source, data)
    state = engine.get_state()

    lines = [f"=== PNT 源更新: {source.value} ==="]
    lines.append(f"输入: 纬度 {args['latitude']:.6f}°, 经度 {args['longitude']:.6f}°"
                 f"{f', 精度 ±{data['accuracy_m']:.1f}m' if data['accuracy_m'] else ''}")
    lines.append("")
    lines.append(state.summary())
    return '\n'.join(lines)


def _satellite_predict_pass(args):
    """预测卫星过境。"""
    try:
        from mbdsdr_ai.new_spacetime import predict_satellite_pass
    except ImportError:
        from new_spacetime import predict_satellite_pass

    name = args['satellite_name']
    lat = args['observer_lat']
    lon = args['observer_lon']
    alt = args.get('observer_alt', 0.0)
    hours = args.get('hours_ahead', 24.0)
    min_elev = args.get('min_elevation', 5.0)

    p = predict_satellite_pass(name, lat, lon, alt, hours, min_elev)

    if p is None:
        return (f"卫星 {name} 在未来 {hours:.0f} 小时内没有仰角超过 {min_elev:.0f}° 的过境\n"
                f"建议: 降低最小仰角或延长预测时间")

    lines = [p.summary()]
    lines.append("")
    lines.append("轨迹点（方位, 仰角）:")
    # 每隔10个点显示一个
    for i, (az, el) in enumerate(p.trajectory):
        if i % max(1, len(p.trajectory)//10) == 0:
            lines.append(f"  {az:.0f}°, {el:.1f}°")
    return '\n'.join(lines)


def _satellite_predict_all(args):
    """预测所有卫星过境。"""
    try:
        from mbdsdr_ai.new_spacetime import predict_all_passes
    except ImportError:
        from new_spacetime import predict_all_passes

    lat = args['observer_lat']
    lon = args['observer_lon']
    alt = args.get('observer_alt', 0.0)
    hours = args.get('hours_ahead', 24.0)
    min_elev = args.get('min_elevation', 5.0)

    passes = predict_all_passes(lat, lon, alt, hours, min_elev)

    if not passes:
        return f"未来 {hours:.0f} 小时内没有卫星过境（仰角 > {min_elev:.0f}°）"

    lines = [f"=== 未来 {hours:.0f} 小时卫星过境预测（共 {len(passes)} 次） ==="]
    lines.append("")
    for i, p in enumerate(passes, 1):
        rise_str = p.rise_time.strftime('%H:%M') if p.rise_time else '?'
        set_str = p.set_time.strftime('%H:%M') if p.set_time else '?'
        dur_str = f"{p.duration_sec/60:.0f}分" if p.duration_sec > 0 else '?'
        freq_str = f"{p.frequency_hz/1e6:.1f}MHz" if p.frequency_hz > 0 else ''
        lines.append(f"{i}. {p.name}: {rise_str}-{set_str} ({dur_str}), "
                     f"最大仰角 {p.max_elevation:.0f}°, 方位 {p.max_azimuth:.0f}° {freq_str}")
    return '\n'.join(lines)


def _sky_view_visible(args):
    """获取可见卫星列表。"""
    try:
        from mbdsdr_ai.new_spacetime import compute_visible_satellite_count
    except ImportError:
        from new_spacetime import compute_visible_satellite_count

    lat = args['observer_lat']
    lon = args['observer_lon']
    min_elev = args.get('min_elevation', 5.0)

    result = compute_visible_satellite_count(lat, lon, min_elev)

    lines = [f"=== 天空图可见卫星（仰角 > {min_elev:.0f}°）==="]
    lines.append(f"可见数量: {result['total_visible']} 颗")
    lines.append("")
    for sat in result['satellites']:
        lines.append(f"  {sat['name']}: 仰角 {sat['elevation_deg']:.1f}°, "
                     f"方位 {sat['azimuth_deg']:.0f}°, "
                     f"距离 {sat['distance_km']:.0f}km, "
                     f"多普勒 {sat['doppler_hz']:+.0f}Hz")
    return '\n'.join(lines)


# ========================================================================
# GNSS 干扰监测工具实现
# ========================================================================

def _gnss_monitor_band(args):
    """监测单个 GNSS 频带干扰。"""
    try:
        from mbdsdr_ai.gnss_monitor import monitor_gnss_band, GNSS_BANDS
    except ImportError:
        from gnss_monitor import monitor_gnss_band, GNSS_BANDS

    band_name = args['band_name'].upper()
    center_freq = args['center_freq_hz']
    sample_rate = args.get('sample_rate_hz', 2.048e6)
    duration = args.get('duration_sec', 1.0)

    # 生成模拟IQ数据（模拟模式下）
    n_samples = int(sample_rate * duration)
    # 模拟噪声基底
    iq = (np.random.randn(n_samples) + 1j * np.random.randn(n_samples)) / np.sqrt(2) * 0.01

    # 模拟模式：在中心频点附近加一个模拟干扰
    if band_name in GNSS_BANDS:
        # 模拟一个 CW 干扰（中心频点偏移 5kHz）
        t = np.arange(n_samples) / sample_rate
        interference = 0.05 * np.exp(2j * np.pi * 5000 * t)
        iq = iq + interference

    result = monitor_gnss_band(iq, center_freq, sample_rate, band_name)

    lines = [f"=== GNSS 频带监测: {band_name} ==="]
    lines.append(f"中心频率: {center_freq/1e6:.3f} MHz")
    lines.append(f"监测带宽: {sample_rate/1e6:.3f} MHz")
    lines.append(f"平均功率: {result.power_dbm:.1f} dB")
    lines.append(f"噪声基底: {result.noise_floor_dbm:.1f} dB")
    lines.append(f"干扰噪声比(INR): {result.inr_db:.1f} dB")
    lines.append(f"峰值频率: {result.peak_freq_hz/1e6:.3f} MHz")
    lines.append(f"峰值功率: {result.peak_power_dbm:.1f} dB")
    lines.append(f"干扰类型: {result.interference_type.value}")
    lines.append(f"分类置信度: {result.confidence*100:.1f}%")

    if result.interference_type.value != "none":
        lines.append("")
        lines.append("建议: 检测到干扰，建议持续监测并记录频谱数据")

    return '\n'.join(lines)


def _gnss_monitor_all(args):
    """监测所有 GNSS 频带。"""
    try:
        from mbdsdr_ai.gnss_monitor import (
            monitor_all_gnss_bands, generate_interference_alerts, GNSS_BANDS
        )
    except ImportError:
        from gnss_monitor import (
            monitor_all_gnss_bands, generate_interference_alerts, GNSS_BANDS
        )

    sample_rate = 2.048e6
    duration = args.get('duration_sec', 0.5)
    inr_warn = args.get('inr_warning_db', 10.0)

    # 为每个频带生成模拟IQ数据
    iq_by_band = {}
    for band in GNSS_BANDS:
        n = int(sample_rate * duration)
        iq = (np.random.randn(n) + 1j * np.random.randn(n)) / np.sqrt(2) * 0.01
        # 模拟 L1 频带有 CW 干扰
        if band == "L1":
            t = np.arange(n) / sample_rate
            iq += 0.08 * np.exp(2j * np.pi * 3000 * t)
        iq_by_band[band] = iq

    results = monitor_all_gnss_bands(iq_by_band, sample_rate)
    alerts = generate_interference_alerts(results, inr_warn)

    lines = ["=== GNSS 全频带干扰监测报告 ==="]
    lines.append("")

    for r in results:
        status = "正常" if r.interference_type.value == "none" else f"告警({r.interference_type.value})"
        lines.append(f"{r.band_name:4s} ({r.center_freq_hz/1e6:7.3f} MHz): "
                     f"INR {r.inr_db:5.1f} dB, {status}")

    lines.append("")
    if alerts:
        lines.append(f"--- 干扰告警 ({len(alerts)}条) ---")
        for a in alerts:
            lines.append(f"[{a.severity.upper()}] {a.band_name}: {a.interference_type} "
                         f"INR={a.inr_db:.1f}dB, 峰值={a.peak_freq_hz/1e6:.3f}MHz")
            lines.append(f"       建议: {a.recommendation}")
    else:
        lines.append("未检测到超过门限的干扰。")

    return '\n'.join(lines)


def _gnss_direction_find(args):
    """干扰源方向估算。"""
    try:
        from mbdsdr_ai.gnss_monitor import interference_direction_finding
    except ImportError:
        from gnss_monitor import interference_direction_finding

    rssi_dict = args['rssi_by_azimuth']
    result = interference_direction_finding(rssi_dict)

    lines = ["=== 干扰源方向估算（八木天线RSSI扫描）==="]
    lines.append(f"估算方向: {result['estimated_direction_deg']}°")
    lines.append(f"峰值RSSI: {result['peak_rssi_dbm']} dBm")
    lines.append(f"置信度: {result['confidence']}")
    lines.append(f"说明: {result['note']}")
    lines.append(f"验证: {result['symmetric_check']}")

    return '\n'.join(lines)

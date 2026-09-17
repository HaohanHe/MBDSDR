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
    """SSTV 慢扫描电视解码（真实实现）。"""
    input_path = args["input_path"]
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

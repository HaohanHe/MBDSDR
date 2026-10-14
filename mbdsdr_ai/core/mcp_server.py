# SPDX-License-Identifier: MIT
"""
MBDSDR Core - MCP / JSON-RPC 2.0 接口
=======================================

把 :class:`~mbdsdr_ai.core.controller.SDRController` 的能力暴露成 MCP 工具，
供任意 MCP 客户端（Cursor / Claude Desktop / 通用 agent）通过 stdio 调用。

协议：JSON-RPC 2.0 over stdin/stdout。
  * ``initialize``   握手，返回服务端信息
  * ``tools/list``   列出全部工具（name / description / inputSchema）
  * ``tools/call``   调用工具，参数 ``{"name": ..., "arguments": {...}}``

无后端 / 未连接时，工具返回 ``{"error": "not_connected", "suggestion": ...}``，
绝不崩溃、绝不造假数据。

本文件纯标准库（json / sys / threading），绝不 import PySide6/Qt。
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

from .controller import SDRController
from .errors import SDRUserError
from ..tool_policy import ToolPolicy, normalize_key


# JSON Schema 片段复用
_NUM = {"type": "number"}
_INT = {"type": "integer"}
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}


def _schema(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


# ======================================================================
# 工具注册表：每个工具 = (name, description, inputSchema, handler)
# handler 签名 (ctrl, args: dict) -> 可 JSON 序列化的结果
# ======================================================================
def _noop(ctrl: SDRController, args: Dict[str, Any]) -> Any:  # pragma: no cover
    return {"ok": True}


# ======================================================================
# 解码器长尾 handler（P0 #65）
#
# 全部懒加载对应 numpy 模块——保持本文件在不触长尾解码器时的导入轻量，
# 也让任一解码器模块的导入问题退化为工具级诚实错误，而不是拖垮整个 server。
# 每个 handler 只调用该模块的公开纯函数，输入非法/模块缺失时返回 dict 错误，
# 绝不抛裸异常、绝不伪造解码结果。
# ======================================================================
def _hex_bytes(name: str, hexstr: str) -> Any:
    """把 hex 字符串解析成 bytes；失败返回 (None, err_dict)。"""
    try:
        clean = (hexstr or "").strip().replace(" ", "").replace("0x", "")
        if not clean:
            return None, {"error": "empty_input",
                          "suggestion": "请提供 %s 的 hex 字节串。" % name}
        return bytes.fromhex(clean), None
    except ValueError as e:
        return None, {"error": "bad_hex", "detail": str(e),
                      "suggestion": "%s 必须是偶数长度的 hex。" % name}


def _lt_dab_fic(fic_hex: str) -> Any:
    data, err = _hex_bytes("fic_hex", fic_hex)
    if err is not None:
        return err
    from mbdsdr_ai.dab_plus_lite import dab_fic_decode
    out = dab_fic_decode(data)
    out["verify_state"] = "synthetic_pinned"
    out["real_hardware_verified"] = False  # PENDING
    return out


def _lt_nrsc5_parse(data_hex: str) -> Any:
    data, err = _hex_bytes("data_hex", data_hex)
    if err is not None:
        return err
    from mbdsdr_ai.nrsc5_lite import hdradio_frame_parse
    out = hdradio_frame_parse(data)
    out["verify_state"] = "synthetic_pinned"
    out["real_hardware_verified"] = False  # PENDING
    return out


def _lt_rtl433_list() -> Any:
    from mbdsdr_ai.rtl433_decoder import list_devices
    devs = list_devices()
    return {"protocols": [
        {"device_type": d.get("device_type"),
         "name": d.get("name", ""),
         "modulation": d.get("modulation")} for d in devs],
        "count": len(devs),
        "real_hardware_verified": False}  # PENDING


def _lt_rtl433_decode(device_type: str, message_hex: str) -> Any:
    data, err = _hex_bytes("message_hex", message_hex)
    if err is not None:
        return err
    from mbdsdr_ai.rtl433_decoder import get_device
    dec = get_device(device_type)
    if dec is None:
        return {"error": "unknown_device_type",
                "suggestion": "先用 decoder_rtl433_list 查看支持的 device_type。"}
    msg = dec.decode_message(data)
    if msg is None:
        return {"decoded": None, "reason": "message_not_matched",
                "real_hardware_verified": False}
    msg["real_hardware_verified"] = False  # PENDING
    return {"decoded": msg}


def _lt_gk2a_headers(buf_hex: str) -> Any:
    data, err = _hex_bytes("buf_hex", buf_hex)
    if err is not None:
        return err
    from dataclasses import asdict
    from mbdsdr_ai.gk2a_lrit import parse_gk2a_headers
    hdr = parse_gk2a_headers(data)
    out = asdict(hdr)
    out["real_hardware_verified"] = False  # PENDING
    return out


def _lt_fst4_encode(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return {"error": "empty_input",
                "suggestion": "text 形如 'CQ BI4MIB OM74'。"}
    from mbdsdr_ai.fst4_encode import encode_fst4_text
    tones = encode_fst4_text(text)
    return {"tones": [int(t) for t in tones],
            "n_tones": len(tones),
            "real_hardware_verified": False}  # PENDING


def _lt_bladerf_params(model: str, freq_hz: Any, sr_hz: Any, bw_hz: Any) -> Any:
    from mbdsdr_ai.bladerf_params import BladeRFParams
    if model == "bladerf1":
        p = BladeRFParams(
            model="bladerf1",
            min_freq_hz=30_000_000, max_freq_hz=3_800_000_000,
            min_tx_freq_hz=30_000_000,
            min_sr_hz=4_000, max_sr_hz=56_000_000,
            min_bw_hz=0, max_bw_hz=28_000_000)
    else:
        model = "bladerf2_micro"
        p = BladeRFParams(model=model)
    out = {
        "model": p.model,
        "frequency_hz": {"min": p.min_freq_hz, "max": p.max_freq_hz,
                         "min_tx": p.min_tx_freq_hz},
        "sample_rate_hz": {"min": p.min_sr_hz, "max": p.max_sr_hz,
                           "default": p.default_sr_hz},
        "bandwidth_hz": {"min": p.min_bw_hz, "max": p.max_bw_hz},
        "rx_total_gain_db": [p.rx_total_gain_min_db, p.rx_total_gain_max_db],
        "checks": {},
        "real_hardware_verified": False,  # 纯查表；设备连接由 BladeRFBackend 另行判定
    }
    if freq_hz is not None:
        out["checks"]["freq_in_range"] = bool(p.is_valid_frequency(float(freq_hz)))
    if sr_hz is not None:
        out["checks"]["sample_rate_in_range"] = bool(p.is_valid_sample_rate(float(sr_hz)))
    if bw_hz is not None:
        out["checks"]["bandwidth_in_range"] = bool(p.is_valid_bandwidth(float(bw_hz)))
    return out


def _aigui_catalog() -> Any:
    """A2UI 组件白名单目录（agent 侧查询；渲染与布局模型的事实源在 C++ renderer，
    此处不持有布局状态，目录同步由 test_aigui_messages.py 跨语言钉扎）。"""
    from mbdsdr_ai.aigui_messages import (A2UI_VERSION, DEFAULT_SURFACE_ID,
                                          catalog_entries)
    entries = catalog_entries()
    return {
        "a2ui_version": A2UI_VERSION,
        "surface_id": DEFAULT_SURFACE_ID,
        "components": entries,
        "count": len(entries),
        "note": "渲染/布局模型唯一事实源是 C++ renderer"
                "（cpp/src/ui/component_registry.cpp）；本目录仅 agent 编排参考。",
        "real_hardware_verified": False,  # 纯静态目录，无硬件面
    }


def _aigui_validate_message(op: str, components: Any) -> Any:
    """agent 侧 A2UI 消息校验与信封翻译（只读：不动布局、不碰 DSP/音频/硬件）。

    返回官方信封草稿 + 未知 id 清单：renderer 对未知 id 是 placeholder 兜底，
    但 agent 在构造期就应当看见自己引用了什么。
    """
    from mbdsdr_ai.aigui_messages import arrange_to_envelope, validate_components
    v = validate_components(components)
    out: Dict[str, Any] = {
        "valid": v["ok"],
        "unknown_ids": v["unknown_ids"],
        "node_count": v["node_count"],
        "child_refs": v["child_refs"],
        "envelope": arrange_to_envelope(op, components),
    }
    if "error" in v:
        out["error"] = v["error"]
    out["hint"] = ("信封可下发；未知 id 也会被 renderer 诚实占位，"
                   "但最好现在就用 aigui_component_catalog 修正。"
                   if v["ok"] else
                   "存在未注册 id：请先用 aigui_component_catalog 核对可引用 id。")
    out["real_hardware_verified"] = False  # 纯校验，无硬件面
    return out


def _build_tool_table() -> List[Dict[str, Any]]:
    """构造静态工具表（与 SDRController 方法一一对应）。"""
    table: List[Dict[str, Any]] = []

    def add(name: str, desc: str, schema: Dict[str, Any],
            handler: Callable[[SDRController, Dict[str, Any]], Any],
            write: bool = False) -> None:
        table.append({
            "name": name,
            "description": desc,
            "inputSchema": schema,
            "handler": handler,
            "write": write,   # write tools require explicit arm_manual (manual gate)
        })

    # ---- 设备 ----
    add("list_devices", "枚举可见 SDR 设备（无硬件返回空，含 debug 调试源）。",
        _schema({}), lambda c, a: [d.to_dict() if hasattr(d, "to_dict") else {
            "name": getattr(d, "name", str(d)),
            "driver": getattr(d, "driver", ""),
            "serial": getattr(d, "serial", ""),
        } for d in c.list_devices()])

    add("connect", "连接设备。device_id 为序列号/'driver:serial'，或 'debug' 调试信号源。",
        _schema({"device_id": _STR}, ["device_id"]),
        lambda c, a: {"connected": c.connect(a["device_id"])}, write=True)

    add("disconnect", "断开当前设备。", _schema({}),
        lambda c, a: (c.disconnect(), {"connected": False})[1], write=True)

    add("is_connected", "是否已连接后端。", _schema({}),
        lambda c, a: {"connected": c.is_connected()})

    # ---- 接收参数 ----
    add("set_frequency", "调谐中心频率（Hz）。",
        _schema({"hz": _NUM}, ["hz"]),
        lambda c, a: (c.set_frequency(float(a["hz"])), {"ok": True})[1], write=True)

    add("get_frequency", "读取当前中心频率（Hz）。", _schema({}),
        lambda c, a: {"frequency_hz": c.get_frequency()})

    add("set_demod", "设置解调模式（AM/FM/WFM/NFM/USB/LSB/CW）。",
        _schema({"mode": _STR}, ["mode"]),
        lambda c, a: (c.set_demod(a["mode"]), {"ok": True})[1], write=True)

    add("get_demod", "读取当前解调模式。", _schema({}),
        lambda c, a: {"demod": c.get_demod()})

    add("set_gain", "设置总增益（dB）。",
        _schema({"db": _NUM}, ["db"]),
        lambda c, a: (c.set_gain(float(a["db"])), {"ok": True})[1], write=True)

    add("set_sample_rate", "设置基带采样率（Hz）。",
        _schema({"sr": _NUM}, ["sr"]),
        lambda c, a: (c.set_sample_rate(float(a["sr"])), {"ok": True})[1], write=True)

    add("set_bandwidth", "设置信道带宽（Hz）。",
        _schema({"hz": _NUM}, ["hz"]),
        lambda c, a: (c.set_bandwidth(float(a["hz"])), {"ok": True})[1], write=True)

    add("set_squelch", "设置静噪门限（dB）。",
        _schema({"db": _NUM}, ["db"]),
        lambda c, a: (c.set_squelch(float(a["db"])), {"ok": True})[1], write=True)

    # ---- 音频 ----
    add("list_audio_outputs", "枚举音频输出设备。", _schema({}),
        lambda c, a: [d.to_dict() for d in c.list_audio_outputs()])

    add("set_audio_output", "选择音频输出设备索引。",
        _schema({"index": _INT}, ["index"]),
        lambda c, a: {"ok": c.set_audio_output(int(a["index"]))}, write=True)

    add("start_audio", "启动音频播放。", _schema({}),
        lambda c, a: {"ok": c.start_audio()}, write=True)

    add("stop_audio", "停止音频播放。", _schema({}),
        lambda c, a: (c.stop_audio(), {"ok": True})[1], write=True)

    add("set_volume", "设置播放音量（dB）。",
        _schema({"db": _NUM}, ["db"]),
        lambda c, a: (c.set_volume(float(a["db"])), {"ok": True})[1], write=True)

    add("set_mute", "静音/取消静音。",
        _schema({"muted": _BOOL}, ["muted"]),
        lambda c, a: (c.set_mute(bool(a["muted"])), {"ok": True})[1], write=True)

    # ---- DSP ----
    add("read_spectrum", "读一帧功率谱（dB 数组，长度 nfft）。",
        _schema({"nfft": _INT}, ["nfft"]),
        lambda c, a: _ndarray_to_json(c.read_spectrum(int(a["nfft"]))))

    add("read_iq", "读 n 个复基带样本（complex64，转 list）。",
        _schema({"n": _INT}, ["n"]),
        lambda c, a: _ndarray_to_json(c.read_iq(int(a["n"]))))

    add("read_audio", "读 n 个解调后音频样本。",
        _schema({"n": _INT}, ["n"]),
        lambda c, a: _ndarray_to_json(c.read_audio(int(a["n"]))))

    add("start_analyze", "对当前信号做自动调制识别。",
        _schema({"modulation": _STR}),
        lambda c, a: c.start_analyze(a.get("modulation")), write=True)

    # ---- 扫描 ----
    add("start_scan", "启动步进扫频，返回扫描句柄。",
        _schema({"start_hz": _NUM, "stop_hz": _NUM, "step_hz": _NUM},
                ["start_hz", "stop_hz", "step_hz"]),
        lambda c, a: {"handle": c.start_scan(float(a["start_hz"]),
                                             float(a["stop_hz"]),
                                             float(a["step_hz"]))}, write=True)

    add("stop_scan", "停止扫描。",
        _schema({"handle": _STR}, ["handle"]),
        lambda c, a: (c.stop_scan(a["handle"]), {"ok": True})[1], write=True)

    add("get_scan_results", "获取扫描活动段结果。",
        _schema({"handle": _STR}, ["handle"]),
        lambda c, a: c.get_scan_results(a["handle"]))

    # ---- 解码 ----
    add("start_decoder", "启动解码器（adsb/aprs/noaa_apt/meteor），返回句柄。",
        _schema({"decoder_type": _STR, "params": {"type": "object"}},
                ["decoder_type"]),
        lambda c, a: {"handle": c.start_decoder(a["decoder_type"], a.get("params"))}, write=True)

    add("stop_decoder", "停止解码器。",
        _schema({"handle": _STR}, ["handle"]),
        lambda c, a: (c.stop_decoder(a["handle"]), {"ok": True})[1], write=True)

    add("get_decoder_messages", "取走解码器累积报文。",
        _schema({"handle": _STR}, ["handle"]),
        lambda c, a: c.get_decoder_messages(a["handle"]))

    # ---- 卫星 ----
    add("list_satellites", "列出内置可接收卫星。", _schema({}),
        lambda c, a: [s.to_dict() for s in c.list_satellites()])

    add("get_satellite_pass", "预报某 NORAD 编号卫星最近一次过境。",
        _schema({"norad": _INT}, ["norad"]),
        lambda c, a: c.get_satellite_pass(int(a["norad"])))

    add("tune_satellite", "按多普勒调谐到某卫星。",
        _schema({"norad": _INT}, ["norad"]),
        lambda c, a: c.tune_satellite(int(a["norad"])), write=True)

    add("set_ground_station", "设置地面站位置。",
        _schema({"lat": _NUM, "lon": _NUM, "alt_m": _NUM},
                ["lat", "lon"]),
        lambda c, a: (c.set_ground_station(float(a["lat"]), float(a["lon"]),
                                           float(a.get("alt_m", 0.0))),
                      {"ok": True})[1], write=True)

    # ---- 书签 ----
    add("add_bookmark", "添加书签。",
        _schema({"freq_hz": _NUM, "name": _STR, "mode": _STR, "group": _STR},
                ["freq_hz", "name"]),
        lambda c, a: c.add_bookmark(float(a["freq_hz"]), a["name"],
                                    a.get("mode", "FM"), a.get("group", "")), write=True)

    add("list_bookmarks", "列出书签（可按 group 过滤）。",
        _schema({"group": _STR}),
        lambda c, a: c.list_bookmarks(a.get("group")))

    add("delete_bookmark", "按 id（频率 Hz）删除书签。",
        _schema({"id": _INT}, ["id"]),
        lambda c, a: {"ok": c.delete_bookmark(int(a["id"]))}, write=True)

    add("find_nearest_bookmark", "找离给定频率最近的书签。",
        _schema({"freq_hz": _NUM}, ["freq_hz"]),
        lambda c, a: c.find_nearest_bookmark(float(a["freq_hz"])))

    # ---- 录制 ----
    add("start_recording", "开始 IQ 录制（SigMF）。",
        _schema({"path": _STR}, ["path"]),
        lambda c, a: c.start_recording(a["path"]), write=True)

    add("stop_recording", "停止录制。", _schema({}),
        lambda c, a: c.stop_recording(), write=True)

    add("get_recording_status", "录制状态。", _schema({}),
        lambda c, a: c.get_recording_status())

    # ---- 服务 ----
    add("start_remote_control", "启动 GQRX 风格远程控制服务。",
        _schema({"port": _INT}, ["port"]),
        lambda c, a: c.start_remote_control(int(a["port"])), write=True)

    add("stop_remote_control", "停止远程控制服务。", _schema({}),
        lambda c, a: (c.stop_remote_control(), {"ok": True})[1], write=True)

    add("start_web_server", "启动 Web 服务器。",
        _schema({"port": _INT}, ["port"]),
        lambda c, a: c.start_web_server(int(a["port"])), write=True)

    add("stop_web_server", "停止 Web 服务器。", _schema({}),
        lambda c, a: (c.stop_web_server(), {"ok": True})[1], write=True)

    add("get_service_status", "远程/Web 服务状态。", _schema({}),
        lambda c, a: c.get_service_status())

    # ---- ANR ----
    add("set_anr_enabled", "开关自适应降噪。",
        _schema({"enabled": _BOOL}, ["enabled"]),
        lambda c, a: (c.set_anr_enabled(bool(a["enabled"])), {"ok": True})[1], write=True)

    add("set_anr_strength", "设置降噪强度 0-10。",
        _schema({"level": _INT}, ["level"]),
        lambda c, a: (c.set_anr_strength(int(a["level"])), {"ok": True})[1], write=True)

    add("learn_noise_floor", "用当前音频学习噪声底。", _schema({}),
        lambda c, a: c.learn_noise_floor(), write=True)

    # ---- 多 VFO ----
    add("add_vfo", "新建 VFO，返回 id。",
        _schema({"freq_hz": _NUM, "mode": _STR, "bw_hz": _NUM},
                ["freq_hz"]),
        lambda c, a: {"vfo_id": c.add_vfo(float(a["freq_hz"]),
                                          a.get("mode", "FM"),
                                          float(a.get("bw_hz", 12500.0)))}, write=True)

    add("remove_vfo", "删除 VFO。",
        _schema({"vfo_id": _STR}, ["vfo_id"]),
        lambda c, a: (c.remove_vfo(a["vfo_id"]), {"ok": True})[1], write=True)

    add("set_primary_vfo", "设为主听 VFO。",
        _schema({"vfo_id": _STR}, ["vfo_id"]),
        lambda c, a: {"ok": c.set_primary_vfo(a["vfo_id"])}, write=True)

    add("list_vfos", "列出全部 VFO。", _schema({}),
        lambda c, a: [v.to_dict() for v in c.list_vfos()])

    # ---- 系统 ----
    add("get_status", "完整系统状态。", _schema({}),
        lambda c, a: c.get_status().to_dict())

    add("shutdown", "优雅关闭全部资源。", _schema({}),
        lambda c, a: (c.shutdown(), {"ok": True})[1], write=True)

    # ==================================================================
    # 解码器长尾（P0 #65）—— Python 面独立实现、C++ 10 域未覆盖的长尾
    # 协议/硬件参数工具。全部为离线纯函数 handler：不依赖已连接后端，
    # 输入为结构化小数据（hex 字节串 / 文本 / 型号名），输出真实解析结果；
    # 输入非法时返回诚实错误，绝不造假。真机验证状态见
    # docs/learn/phase63/python-decoder-longtail.md（多数为合成钉扎可过、
    # 真机未验 -> PENDING）。
    # ==================================================================
    add("decoder_dab_fic_decode",
        "DAB/DAB+ FIC 解码（ETSI EN 300 400/401 独立实现）。输入 FIC 字节的 hex，"
        "输出 ensemble/服务列表/子信道配置。只做 FIC/FIB/FIG 解析，不做 AAC 音频解码。",
        _schema({"fic_hex": _STR}, ["fic_hex"]),
        lambda c, a: _lt_dab_fic(a.get("fic_hex", "")))

    add("decoder_nrsc5_frame_parse",
        "HD Radio (NRSC-5) 帧解析。输入 HDLC/L3 字节的 hex，输出 PSD 节目文字。"
        "只做成帧/PSD 提取，不做 HDC/AAC 音频解码。",
        _schema({"data_hex": _STR}, ["data_hex"]),
        lambda c, a: _lt_nrsc5_parse(a.get("data_hex", "")))

    add("decoder_rtl433_list",
        "列出 433.92MHz ISM 频段本仓独立支持的解码器协议（天气站/胎压/温湿度）。",
        _schema({}),
        lambda c, a: _lt_rtl433_list())

    add("decoder_rtl433_decode",
        "按指定协议解码一段 433.92MHz 设备消息字节的 hex。"
        "device_type 取 decoder_rtl433_list 返回的 device_type。",
        _schema({"device_type": _STR, "message_hex": _STR},
                ["device_type", "message_hex"]),
        lambda c, a: _lt_rtl433_decode(a.get("device_type", ""),
                                      a.get("message_hex", "")))

    add("decoder_gk2a_parse_headers",
        "GK-2A LRIT 图像文件头解析（CCSDS 独立实现）。输入文件头缓冲 hex，"
        "输出图像列数/行数/位深/分段数等字段。",
        _schema({"buf_hex": _STR}, ["buf_hex"]),
        lambda c, a: _lt_gk2a_headers(a.get("buf_hex", "")))

    add("decoder_fst4_encode_text",
        "FST4 文本消息 -> 160 音调（WSJT 公开协议独立编码器）。"
        "text 形如 'CQ BI4MIB OM74'。",
        _schema({"text": _STR}, ["text"]),
        lambda c, a: _lt_fst4_encode(a.get("text", "")))

    add("device_bladerf_params_query",
        "查询 bladeRF 硬件参数表（纯查表，无设备依赖）。model 取 "
        "'bladerf2_micro'(默认) 或 'bladerf1'；可选传入 freq_hz/sample_rate_hz/"
        "bandwidth_hz 做量程合法性校验。",
        _schema({"model": _STR, "freq_hz": _NUM,
                 "sample_rate_hz": _NUM, "bandwidth_hz": _NUM}),
        lambda c, a: _lt_bladerf_params(
            a.get("model", "bladerf2_micro"),
            a.get("freq_hz"), a.get("sample_rate_hz"), a.get("bandwidth_hz")))

    # ---- 工作台编排（A2UI agent 侧作者工具；渲染/布局模型在 C++ renderer，
    #      本面刻意只做信封构造+白名单校验，不做第二个布局模型）----
    add("aigui_component_catalog",
        "A2UI v1.0 组件白名单目录（23 项：5 结构容器 + 14 tab + 4 center 视图）。"
        "agent 编排工作台前先查可引用的组件 id。目录与 C++ renderer 跨语言钉扎。",
        _schema({}),
        lambda c, a: _aigui_catalog())

    add("aigui_validate_message",
        "agent 侧 A2UI 消息校验与信封翻译：给定 op（createSurface/"
        "updateComponents/deleteSurface，默认 updateComponents）与 components"
        "（扁平邻接表 [{id,component,props,children}]），返回官方信封草稿与"
        "未知 id 清单。只读：不下发、不动布局、不碰 DSP/音频/硬件——真正的"
        "下发由 C++ 面的 arrange_workspace 执行。",
        _schema({"op": _STR,
                 "components": {"type": "array",
                                "items": {"type": "object"}}},
                ["components"]),
        lambda c, a: _aigui_validate_message(a.get("op", "updateComponents"),
                                             a.get("components")))

    return table


# ======================================================================
# 二级域目录（P1 #64）——冻结 10 域，与 C++ 侧 mcp_server.cpp 同名同序
#
# 这是服务器侧分组视图：工具 schema 的唯一事实源仍是上面的
# ``_build_tool_table()``；这里只做“每工具恰好归属一个域”的桶分组。
# Python 面（SDRController）当前没有 VNA 工具，故三个 VNA 域与「记忆编排」
# 在本表面向用户为空卡片（诚实空态）；「工作台编排」现有 agent 侧作者工具
# （catalog/validate，只读，渲染在 C++），布局执行类工具仍以 C++ 为单一事实源；
# 10 域名集合本身保持与 C++ 完全一致，形成跨语言钉扎。
# ======================================================================
FROZEN_DOMAIN_SUMMARIES: Dict[str, str] = {
    "SDR 调谐/VFO": "接收机调谐、解调模式、带宽/静噪/降噪与多 VFO 及频谱读取参数控制。",
    "亚音数字解码": "CTCSS/DCS 亚音与各类数字报文解码器的启停与报文读取。",
    "VNA 测量": "VNA 扫频设置与 S 参数原始测量读取。",
    "VNA 分析": "对 VNA 测量数据做谐振/阻抗匹配等分析。",
    "VNA 校准": "VNA 校准状态与校准流程控制。",
    "导出演制": "IQ 录制、录制状态与音频/频谱数据导出。",
    "书签扫描卫星": "书签管理、步进扫描与卫星过境预报/调谐。",
    "系统设备": "设备连接、音频/服务开关与整体状态。",
    "记忆编排": "本地持久记忆（SQLite FTS5）的写入、覆盖与全文检索，按 user/agent/session 作用域分层。",
    "工作台编排": "AIGUI 三栏工作台布局编排：按编译期白名单调整 splitter 尺寸/右侧 tab 可见性，未知 id 诚实占位，不碰 DSP/音频/硬件。",
}

FROZEN_DOMAIN_NAMES: List[str] = list(FROZEN_DOMAIN_SUMMARIES.keys())

# Python 面扩展域（P0 #65）——不参与与 C++ 的 10 域跨语言冻结钉扎，
# 仅在本 Python MCP server 视图末尾追加。C++ 侧没有对应域；客户端合并视图时
# 应把 C++ 的 10 域与本域并列拼装（两者 schema 各自独立，互不复制）。
PYTHON_EXTENSION_DOMAIN_SUMMARIES: Dict[str, str] = {
    "解码器长尾": "Python 侧独立实现、C++ 10 域尚未覆盖的长尾协议解码器与硬件参数查表"
                 "（DAB+/NRSC-5/433.92MHz ISM/GK-2A LRIT/FST4/bladeRF 参数）。"
                 "离线纯函数，真机验证多为 PENDING。",
}

PYTHON_EXTENSION_DOMAIN_NAMES: List[str] = list(PYTHON_EXTENSION_DOMAIN_SUMMARIES.keys())

# 全域名 = 冻结 10 域（与 C++ 钉扎）+ Python 扩展域。
ALL_DOMAIN_NAMES: List[str] = FROZEN_DOMAIN_NAMES + PYTHON_EXTENSION_DOMAIN_NAMES

# 域 -> summary 合并视图（冻结 10 域原样 + Python 扩展域）。
ALL_DOMAIN_SUMMARIES: Dict[str, str] = {
    **FROZEN_DOMAIN_SUMMARIES, **PYTHON_EXTENSION_DOMAIN_SUMMARIES}

# 本表面工具 -> 域。未列出的工具会被 _grouped_tool_cards 视为“未分配”，
# 测试会据此钉死“每工具恰好一个域”。
_TOOL_DOMAIN: Dict[str, str] = {
    # SDR 调谐/VFO
    "set_frequency": "SDR 调谐/VFO",
    "get_frequency": "SDR 调谐/VFO",
    "set_demod": "SDR 调谐/VFO",
    "get_demod": "SDR 调谐/VFO",
    "set_gain": "SDR 调谐/VFO",
    "set_sample_rate": "SDR 调谐/VFO",
    "set_bandwidth": "SDR 调谐/VFO",
    "set_squelch": "SDR 调谐/VFO",
    "start_analyze": "SDR 调谐/VFO",
    "set_anr_enabled": "SDR 调谐/VFO",
    "set_anr_strength": "SDR 调谐/VFO",
    "learn_noise_floor": "SDR 调谐/VFO",
    "add_vfo": "SDR 调谐/VFO",
    "remove_vfo": "SDR 调谐/VFO",
    "set_primary_vfo": "SDR 调谐/VFO",
    "list_vfos": "SDR 调谐/VFO",
    "read_spectrum": "SDR 调谐/VFO",
    # 亚音数字解码
    "start_decoder": "亚音数字解码",
    "stop_decoder": "亚音数字解码",
    "get_decoder_messages": "亚音数字解码",
    # VNA 测量 / VNA 分析 / VNA 校准：本面无 VNA 工具（空卡片，诚实空态）
    # 导出演制
    "start_recording": "导出演制",
    "stop_recording": "导出演制",
    "get_recording_status": "导出演制",
    "read_iq": "导出演制",
    "read_audio": "导出演制",
    # 书签扫描卫星
    "start_scan": "书签扫描卫星",
    "stop_scan": "书签扫描卫星",
    "get_scan_results": "书签扫描卫星",
    "list_satellites": "书签扫描卫星",
    "get_satellite_pass": "书签扫描卫星",
    "tune_satellite": "书签扫描卫星",
    "set_ground_station": "书签扫描卫星",
    "add_bookmark": "书签扫描卫星",
    "list_bookmarks": "书签扫描卫星",
    "delete_bookmark": "书签扫描卫星",
    "find_nearest_bookmark": "书签扫描卫星",
    # 系统设备
    "list_devices": "系统设备",
    "connect": "系统设备",
    "disconnect": "系统设备",
    "is_connected": "系统设备",
    "list_audio_outputs": "系统设备",
    "set_audio_output": "系统设备",
    "start_audio": "系统设备",
    "stop_audio": "系统设备",
    "set_volume": "系统设备",
    "set_mute": "系统设备",
    "start_remote_control": "系统设备",
    "stop_remote_control": "系统设备",
    "start_web_server": "系统设备",
    "stop_web_server": "系统设备",
    "get_service_status": "系统设备",
    "get_status": "系统设备",
    "shutdown": "系统设备",
    # 解码器长尾（Python 扩展域）
    "decoder_dab_fic_decode": "解码器长尾",
    "decoder_nrsc5_frame_parse": "解码器长尾",
    "decoder_rtl433_list": "解码器长尾",
    "decoder_rtl433_decode": "解码器长尾",
    "decoder_gk2a_parse_headers": "解码器长尾",
    "decoder_fst4_encode_text": "解码器长尾",
    "device_bladerf_params_query": "解码器长尾",
    # A2UI agent 侧作者工具（渲染/布局模型事实源在 C++ renderer，本面只做
    # 信封构造+白名单校验；两个新工具让「工作台编排」域在 Python 面不再空置）
    "aigui_component_catalog": "工作台编排",
    "aigui_validate_message": "工作台编排",
}


def _resolve_domain(query: str) -> Optional[str]:
    """把 params.domain 容错解析成域名（冻结 10 域 + Python 扩展域）；匹配不到返回 None。"""
    if query in ALL_DOMAIN_NAMES:
        return query
    q = query.strip().lower()
    for name in ALL_DOMAIN_NAMES:
        if name.lower() == q:
            return name
    hits = [n for n in ALL_DOMAIN_NAMES
            if q and (n.lower().find(q) >= 0 or q.find(n.lower()) >= 0)]
    if len(hits) == 1:
        return hits[0]
    return None


# tools/detail 三分级（与 C++ mcp_server.cpp 同形状、同合法值）。
TOOL_DETAIL_LEVELS = ("name", "desc", "schema")


def _tool_at_level(t: Dict[str, Any], level: str) -> Any:
    """按 detail 级别投影一条工具表记录：name->裸名串；desc->{name,description}；
    schema->完整 {name,description,inputSchema,write}（write 与 C++ schema 对齐）。"""
    if level == "schema":
        return {"name": t["name"], "description": t["description"],
                "inputSchema": t["inputSchema"],
                "write": bool(t.get("write", False))}
    if level == "desc":
        return {"name": t["name"], "description": t["description"]}
    return t["name"]


def _ndarray_to_json(arr: Any) -> Any:
    """numpy 数组转 JSON；None 表示无数据。"""
    if arr is None:
        return {"data": None,
                "error": "not_connected",
                "suggestion": "请先 connect() 一个设备或调试信号源。"}
    try:
        return {"data": [float(x) for x in arr.tolist()],
                "length": int(arr.size)}
    except Exception as e:  # pragma: no cover
        return {"error": "not_available", "technical_detail": str(e)}


# ======================================================================
# MCPToolSet
# ======================================================================
class MCPToolSet:
    """把 SDRController 方法暴露成 MCP 工具集。

    Parameters
    ----------
    controller :
        共享的 :class:`SDRController`。不传则自建一个（无后端）。
    """

    def __init__(self, controller: Optional[SDRController] = None,
                 policy: Optional[ToolPolicy] = None) -> None:
        self.controller = controller if controller is not None else SDRController()
        self._table = _build_tool_table()
        self._by_name: Dict[str, Dict[str, Any]] = {
            t["name"]: t for t in self._table
        }
        # 工具权限规则集（默认写 ask/读 allow = 既有 arm_manual 语义不变）；
        # 会话批准缓存：approve_session=true 的 (工具, 参数) 本进程免问。
        self.policy = policy if policy is not None else ToolPolicy.load()
        self._session_approvals: set = set()

    # ------------------------------------------------------------------
    def list_tools(self) -> List[Dict[str, Any]]:
        """返回扁平工具列表（name/description/inputSchema，不含 handler）。

        保留为兼容内部调用；对外 ``tools/list`` 走 :meth:`tools_list_result`
        的二级域分组视图。
        """
        return [
            {"name": t["name"], "description": t["description"],
             "inputSchema": t["inputSchema"]}
            for t in self._table
        ]

    # ------------------------------------------------------------------
    # 二级域分组视图（P1 #64，与 C++ mcp_server.cpp 同形状）
    # ------------------------------------------------------------------
    def _bucketed(self, full: bool) -> Dict[str, List[Dict[str, Any]]]:
        """按 _TOOL_DOMAIN 把本表工具分桶。full=True 放完整 schema，否则只放名。"""
        buckets: Dict[str, List[Dict[str, Any]]] = {
            n: [] for n in ALL_DOMAIN_NAMES}
        for t in self._table:
            dom = _TOOL_DOMAIN.get(t["name"])
            if dom is None:
                continue  # 未分配工具：测试会钉死不发生
            if full:
                buckets[dom].append(
                    {"name": t["name"], "description": t["description"],
                     "inputSchema": t["inputSchema"]})
            else:
                buckets[dom].append({"name": t["name"]})
        return buckets

    def tools_list_result(self, params: Optional[Dict[str, Any]] = None):
        """tools/list 的 result 体；带错误串时返回 (None, errmsg)。

        detail 三分级（与 C++ 同形状）：
          * ``"name"``（默认）：每工具仅名字串；
          * ``"desc"``：每工具 ``{name, description}``（无 inputSchema）；
          * ``"schema"``：每工具完整 ``{name, description, inputSchema}``。
        ``detailed:true`` 是 ``detail="schema"`` 的兼容别名；未知 detail 报错。
        """
        params = params or {}
        detailed = bool(params.get("detailed", False))
        dom = params.get("domain")

        detail = params.get("detail")
        explicit_detail = isinstance(detail, str) and bool(detail)
        if explicit_detail:
            if detail not in TOOL_DETAIL_LEVELS:
                return None, ("未知 detail: %s（合法值: %s）"
                              % (detail, "、".join(TOOL_DETAIL_LEVELS)))
            level = detail
        else:
            level = "schema" if detailed else "name"

        if isinstance(dom, str) and dom:
            name = _resolve_domain(dom)
            if name is None:
                return None, ("未知 domain: %s（合法域: %s）"
                              % (dom, "、".join(ALL_DOMAIN_NAMES)))
            # 钻取单域：无显式 detail 时保持旧行为（全 schema）；显式 detail 按级返回。
            eff = level if explicit_detail else "schema"
            entries = [
                _tool_at_level(t, eff)
                for t in self._table if _TOOL_DOMAIN.get(t["name"]) == name]
            return {"tools": entries}, None

        buckets: Dict[str, List[Any]] = {n: [] for n in ALL_DOMAIN_NAMES}
        for t in self._table:
            dom2 = _TOOL_DOMAIN.get(t["name"])
            if dom2 is None:
                continue  # 未分配工具：测试会钉死不发生
            buckets[dom2].append(_tool_at_level(t, level))
        cards = [
            {"domain": n, "summary": ALL_DOMAIN_SUMMARIES[n], "tools": buckets[n]}
            for n in ALL_DOMAIN_NAMES]
        if level == "schema":
            return {"domains": cards}, None
        return {"tools": cards}, None

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """按名调用工具。无后端时安全降级，绝不抛异常。"""
        tool = self._by_name.get(name)
        if tool is None:
            return {"error": "unknown_tool", "tool": name,
                    "suggestion": "用 tools/list 查看可用工具。"}
        args = dict(arguments or {})
        # Write-tool gate: ruleset (allow/ask/deny) + explicit once-approval
        # (arm_manual) + optional session-level approval cache
        # (approve_session). Semantics aligned with the C++ llm_worker gate:
        # real execution only when policy allows or the caller explicitly arms;
        # the arm flags are consumed here and never leak to the handler.
        session_cached = False
        if tool.get("write"):
            approve_session = args.pop("approve_session", False) is True
            armed = args.pop("arm_manual", False) is True
            action = self.policy.action_for(name, True)
            if action == "deny":
                # hard veto: even an explicitly armed call does not execute
                # (aligned with kilocode hard-ruleset veto).
                return {"ok": False,
                        "error": "denied_by_policy：规则集拒绝执行 %s" % name,
                        "denied": True, "gated": True}
            key = normalize_key(name, args)
            session_cached = key in self._session_approvals
            if action == "ask" and not session_cached:
                if not armed:
                    return {"ok": False,
                            "error": "手动模式：未执行 %s" % name,
                            "gated": True,
                            "policy": action,
                            "hint": "带 arm_manual=true 执行；"
                                    "approve_session=true 则本会话免问"}
                if approve_session:
                    self._session_approvals.add(key)
        try:
            result = tool["handler"](self.controller, args)
        except SDRUserError as e:
            return e.to_dict()
        except Exception as e:  # pragma: no cover - 防御
            return {"error": "internal_error", "technical_detail": str(e)}
        if session_cached and isinstance(result, dict):
            # 本会话免问命中：可观测地标注，不隐形放行
            result.setdefault("approval", "session")
        return result

    # ------------------------------------------------------------------
    # JSON-RPC 2.0 分发（纯函数，便于测试）
    # ------------------------------------------------------------------
    def handle_request(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理一条 JSON-RPC 请求。通知（无 id）返回 None。"""
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}

        def ok(result: Any) -> Dict[str, Any]:
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        def err(code: int, message: str) -> Dict[str, Any]:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": code, "message": message}}

        if method == "initialize":
            # 声明 listChanged，允许 MCP client 缓存 tools/list 并在收到
            # notifications/tools/list_changed 时失效重建。工具表当前静态，
            # 服务端不会主动发该通知；未来动态注册时再发。
            return ok({"protocolVersion": "2024-11-05",
                       "capabilities": {"tools": {"listChanged": True}},
                       "serverInfo": {"name": "mbdsdr-core", "version": "0.1.0"}})
        if method == "notifications/initialized":
            return None  # 通知无需回复
        if method == "notifications/tools/list_changed":
            # 缓存失效通知（通常由服务端->客户端）。无 id -> 不回 stdout；
            # 带 id（被误用为请求）-> 诚实回 result:null，不当成未知方法。
            if rid is None:
                return None
            return ok(None)
        if method == "tools/list":
            result, errmsg = self.tools_list_result(params)
            if errmsg is not None:
                return err(-32602, errmsg)
            return ok(result)
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            result = self.call_tool(name, arguments)
            # MCP 约定：结果包在 content 文本里
            text = json.dumps(result, ensure_ascii=False, default=str)
            return ok({"content": [{"type": "text", "text": text}]})
        return err(-32601, f"未知方法: {method}")


# ======================================================================
# stdio 编码启动准备
#
# JSON-RPC 2.0 over stdio 的响应行按 UTF-8 约定传输，但中文 Windows 上
# 进程标准流编码默认落到 ANSI 代码页（cp936/GBK）：用 ensure_ascii=False
# 写出的含中文/µ 响应行不是合法 UTF-8，严格按约定解析的外部客户端会
# 解帧崩溃（本机实测复现）。因此开始读写 stdin/stdout 之前，必须先把
# 输出流切成 UTF-8。
# ======================================================================
def _reconfigure_to_utf8(stream: Any) -> bool:
    """尽力把单个文本流切成 UTF-8（errors="replace"）；返回是否真的切换。

    没有 ``reconfigure`` 的流（Python < 3.7，或被替身换掉的对象）静默
    跳过并返回 False——诚实降级，不谎称已修复。
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return False
    reconfigure(encoding="utf-8", errors="replace")
    return True


def _prepare_stdio_utf8() -> None:
    """stdio MCP server 启动准备：把 sys.stdout / sys.stderr 强制为 UTF-8。

    必须在读写 stdin/stdout 之前调用（见 :func:`run_mcp_server`）。
    stderr 是日志通道、日志同样含中文，一并修正；``errors="replace"``
    保证极端不可编码字符也退化成替换符，而不是抛异常打断协议流。
    """
    _reconfigure_to_utf8(sys.stdout)
    _reconfigure_to_utf8(sys.stderr)


# ======================================================================
# stdio 服务器主循环
# ======================================================================
def run_mcp_server(controller: Optional[SDRController] = None) -> None:
    """启动 stdio MCP 服务器（JSON-RPC 2.0 over stdin/stdout）。

    阻塞运行，逐行读 stdin、逐行写 stdout。错误打到 stderr（不污染协议流）。

    启动第一件事是 :func:`_prepare_stdio_utf8`：中文 Windows 的 ANSI
    代码页会让含中文/µ 的响应行失去 UTF-8 合法性，必须在任何读写前修正。
    """
    _prepare_stdio_utf8()
    toolset = MCPToolSet(controller)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"[mcp] 无法解析 JSON: {e}\n")
            continue
        try:
            resp = toolset.handle_request(req)
        except Exception as e:  # pragma: no cover
            sys.stderr.write(f"[mcp] 处理异常: {e}\n")
            continue
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    run_mcp_server()

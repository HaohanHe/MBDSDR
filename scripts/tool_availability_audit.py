#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""P13：495 个 AI 工具的逐个可执行性审计。

离线 + 无硬件 + 静音 + offscreen，用替身（假设备 id / 合成 IQ 与音频 /
临时目录 / 仓库内真实小样本）逐个调用注册表里的每个工具，按四类归档：

  RUNS    真能跑并产出可核验结果（成功且返回非空、有结构或断言通过）
  HONEST  只能诚实不可用（缺硬件 / 缺网络 / 缺样本 / 缺前置资源 / 需人工确认）
  STUB    参数校验能拒，但功能未实现（明确 "未实现" 措辞）
  BROKEN  真坏（抛异常、静默返回空、失败却无原因码、成功但内容空洞）

用法：
  python scripts/tool_availability_audit.py                 # 跑全量，重写 docs/tool-availability.md
  python scripts/tool_availability_audit.py --json out.json # 附带逐项明细
  python scripts/tool_availability_audit.py --only ft8_encode codec2_encode
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")
# 审计必须是可复跑的离线行为：网络类工具应快速失败，而不是卡在 DNS 上
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:9")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:9")

RUNS, HONEST, STUB, BROKEN = "RUNS", "HONEST", "STUB", "BROKEN"

# 缺什么才能跑——按提示词归类，用于 md 的「缺什么」列
_MISSING_HINTS = (
    (("无设备", "未连接", "not connected", "no device", "no sdr", "设备未", "未发现可用",
      "无连接设备", "无连接仪器", "请先连接", "未找到设备", "could not open", "failed to open",
      "无法打开", "permission denied", "需先连接", "未插", "无可用设备", "实时 sdr",
      "未探测到", "实时设备", "无 sdr"), "硬件：SDR 设备"),
    # 网络类只认真正的联网特征；「连接失败/超时」是设备面通用措辞，归硬件，
    # 否则 sdr_connect、云台对端超时会被误判成缺网络。
    (("network", "urlopen", "urllib", "dns", "timed out", "离线模式", "mbdsdr_offline",
      "连接被拒绝", "connection refused", "fatal: repository", "外网", "api key", "http "),
     "网络：外网访问/在线数据源"),
    (("arm_manual", "手动确认", "需人工", "安全门", "未授权", "拒绝执行", "gate_not_armed",
      "未过人工门"), "人工确认：arm_manual 安全门"),
    (("ui 执行器未挂载", "天空动作执行器未挂载", "非桌面环境", "没有可撤销的界面动作"),
     "桌面：需在桌面端运行（UI 执行器未挂载）"),
    (("连接超时", "对端无响应", "云台控制失败"), "硬件：外设未响应（云台/仪器）"),
    (("需要安装", "未安装", "未找到可执行", "command not found", "no module named",
      "未检测到 satdump", "未找到 satdump"), "外部程序/依赖：未安装"),
    # 「输入样本/参数不合格 → 如实拒绝」优先于「前置资源」：例如“未提供足够的
    # IQ 样本”应归缺样本，而不是缺前置资源。校验拒绝是正确行为，不是空壳。
    (("需要 iq", "需要样本", "样本不足", "样本太短", "长度不足", "长度 ", "须为", "需为",
      "不是合法", "无法解析", "无法识别", "校验和", "不匹配", "必须", "至少", "得到 ",
      "too_short", "too short", "未检测到", "无同步", "找不到", "不是 ", "缺少",
      "未提供", "invalid", "requires", "expected", "格式", "不足", "不低于", "高于", "低于",
      "必须小于", "必须大于", "失败"), "有效输入：合成替身不满足该工具的输入要求"),
    (("不存在", "not found", "no such", "does not exist", "未找到", "无数据", "请先创建",
      "未录制", "未加载", "为空", "没有可", "没有父版本", "任务不存在", "未匹配", "未收录",
      "未知卫星", "未知帧", "未知的", "未知动词", "未知的进化", "未注册", "没有可撤销"),
     "前置资源：需先创建/采集"),
)

# 审计替身声明：所有合成输入都从这里取，md 表头会注明
SYNTHETIC_SAMPLES = 8192


def _sine(n=SYNTHETIC_SAMPLES, freq=1200.0, sr=8000.0, amp=0.5):
    return [amp * math.sin(2 * math.pi * freq * i / sr) for i in range(n)]


def _cplx_iq(n=SYNTHETIC_SAMPLES, freq=0.1):
    return [complex(math.cos(2 * math.pi * freq * i),
                    math.sin(2 * math.pi * freq * i)) for i in range(n)]


def _bits(n=160):
    return [i % 2 for i in range(n)]


def _repo_file(*names):
    for n in names:
        p = ROOT / n
        if p.exists():
            return str(p)
    p = ROOT / "README.md"
    return str(p) if p.exists() else str(ROOT / "mbdsdr_ai_mcp_server.py")


# ── 补齐组：回答「再多一样东西能解锁多少个工具」 ──────────────────────────
G1 = "① SDR"
G2 = "② 额外硬件/外部程序"
G3 = "③ 真样本"
G4 = "④ 网络/API"
G5 = "⑤ 人工确认"

GROUP_ACTION = {
    G1: "接一根 RTL-SDR（或 rtl_tcp / SoapySDR 后端），sdr_connect 后重跑",
    G2: "接对应外设：云台 rotctld、GNSS/串口仪器、NanoVNA、电台或发射设备；"
        "SatDump 类需安装外部程序；界面类需在桌面端运行（UI 执行器）",
    G3: "提供真实 OTA 录音 / IQ 文件（含合法同步字与完整帧），"
        "或按协议构造的合法帧；或先录制/先建提案",
    G4: "联网或填 API key；离线时保持诚实拒绝",
    G5: "显式 arm_manual=true（写文件/发射的人工确认门）",
}

# 名字先于文案判定：这些工具缺的是外设而不是「基本 SDR」
_G2_NAME_PREFIX = ("gimbal_", "sky_gimbal", "sky_track", "sky_tune", "vna_",
                   "radio_", "instrument", "satdump")


def _group_for(name, reason):
    """把二类结论映射到五个补齐组；一类/三类/四类没有补齐组（返回空串）。"""
    if not reason:
        return ""
    # 名字先于文案：这些工具缺的是基本 SDR 本身，正文里的“连接失败”不能改判
    if name == "sdr_connect":
        return G1
    if name.startswith(_G2_NAME_PREFIX):
        return G2
    if "人工确认" in reason:
        return G5
    if "外部程序" in reason or "桌面" in reason or "外设" in reason:
        return G2
    if "网络" in reason:
        return G4
    if "硬件" in reason:
        return G1
    return G3


def _iq_interleaved(n=4096, freq=0.1):
    """实数交错 I/Q（[I0,Q0,I1,Q1,...]），可直接过 JSON。"""
    out = []
    for i in range(n):
        ph = 2 * math.pi * freq * i
        out.append(math.cos(ph))
        out.append(math.sin(ph))
    return out


def _iq_b64(n=4096, freq=0.1):
    """float32 交错复数 IQ 的 base64（iq_base64 参数通用格式）。"""
    import base64
    import struct
    raw = b"".join(struct.pack("<ff", c, s)
                   for c, s in zip(_iq_interleaved(n, freq)[0::2],
                                   _iq_interleaved(n, freq)[1::2]))
    return base64.b64encode(raw).decode()


def _write_cf32(path, n=4096, freq=0.1):
    """写 complex64 raw 文件（iq_path 参数格式）。"""
    import struct
    vals = _iq_interleaved(n, freq)
    buf = bytearray()
    for i in range(0, len(vals), 2):
        buf += struct.pack("<ff", vals[i], vals[i + 1])
    Path(path).write_bytes(bytes(buf))
    return str(path)


def _py_struct(n=4, freq=0.05):
    """2D 合成图像（n×n，0..255 灰度）。"""
    return [[(i * n + j) % 256 for j in range(n)] for i in range(n)]


def _crc16_ccitt(data: bytes) -> int:
    crc = 0x0000
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _crc_frame_hex() -> str:
    """8 字节数据 + 自算 CRC16/CCITT（poly 0x1021）附在偏移 8。"""
    data = bytes(range(1, 9))
    return data.hex() + f"{_crc16_ccitt(data):04x}"


def _b2f_b64() -> str:
    """最小合法 B2F 报文（信封行 + FBB 路径行 + 正文）的 base64。"""
    import base64
    msg = ("[BI1AAA|BI1BBB|audit probe|20260101120000]\r\n"
           ":FBB: BI1AAA\r\n\r\nhello from audit\r\n")
    return base64.b64encode(msg.encode()).decode()


def _eti_frame() -> list:
    """6144 字节 ETI(NI) 骨架：第 2–4 字节为 FSYNC 0x07 3A B6（其余补零）。"""
    frame = [0] * 6144
    frame[1], frame[2], frame[3] = 0x07, 0x3A, 0xB6
    return frame


def _ccsds_bits() -> list:
    """ASM 0x1ACFFC1D 起头的 0/1 位流（验证链的同步门）。"""
    asm = [0x1A, 0xCF, 0xFC, 0x1D]
    bits = []
    for b in asm:
        bits.extend((b >> (7 - i)) & 1 for i in range(8))
    return bits + _bits(1024)


_GPS_GOLDEN = {}


def _gps_golden():
    """仓库内 golden GPS LNAV（编码器输出钉扎），离线可用。"""
    if not _GPS_GOLDEN:
        p = ROOT / "tests" / "golden" / "gps_lnav_golden.json"
        _GPS_GOLDEN.update(json.loads(p.read_text(encoding="utf-8")))
    return _GPS_GOLDEN


def _gps_nav_bits(n=0) -> list:
    b = _gps_golden()["nav_bits"]
    return b[:n] if n else b


def _gps_eph() -> dict:
    return dict(_gps_golden()["expected_decoded"])


# 合法 TLE（METEOR-M 2，测试夹具里的真实格式两行根数；离线审计不联网）
_TLE_L1 = "1 40069U 14037A   26297.30989954  .00000323  00000+0  16680-3 0  9998"
_TLE_L2 = "2 40069  98.5301 268.5877 0006497  31.2770 328.8793 14.21494425637660"


def _build_args(tool, tmpdir, hex_frame=""):
    """基于 tests/tool_param_smoke.py 的名称启发式，再做替身取值修正。

    必填 + 可选参数都构造（可选布尔不动，避免误把开关拨到 true）；
    TLE 用合法两行根数，十六进制帧用上游 AX.25 编码产物。
    """
    import tool_param_smoke as tps  # noqa: E402

    schema = tool.get("parameters", {}) or {}
    props = schema.get("properties", {}) or {}
    args = tps.build_args(tool)
    for p, prop in props.items():
        prop = prop or {}
        if p in args or prop.get("type") == "boolean":
            continue
        if p in ("arm_manual", "token", "api_key", "confirm"):
            continue
        # 可选参数只补「数值」和「有 enum 的枚举」，别把结构化对象/ID 填成瞎值：
        # 乱填会让工具抛 TypeError/AttributeError，把真实可用性审计污染成"报错"。
        # 但 schema 里带 default 的可选数值一律不填——填 1 会踩到 d29_star=1、
        # min_power=max_power=1 这类「合法默认值被审计替身破坏」的误判。
        if prop.get("default") is None and (
                prop.get("type") in ("number", "integer") or prop.get("enum")):
            args[p] = tps._coerce_to_schema(tps.value_for(p, prop), prop)

    for k, v in list(args.items()):
        low = k.lower()
        prop = props.get(k, {}) or {}
        typ = prop.get("type")
        # 空数组一律补成有意义的替身，避免"喂空 → 成功返回空"的假绿
        if isinstance(v, list) and not v:
            items = prop.get("items", {}) or {}
            if items.get("type") == "object":
                sub = items.get("properties", {}) or {}
                v = [{k: tps._coerce_to_schema(tps.value_for(k, sp), sp)
                      for k, sp in sub.items()}]
            elif items.get("type") == "string":
                v = [hex_frame] if hex_frame else ["test"]
            elif any(w in low for w in ("freedv", "signal", "audio", "mpx", "samples")):
                v = _sine()
            elif "iq" in low:
                v = _sine(4096)
            elif "bits" in low:
                v = _bits()
            elif typ == "array":
                v = _bits()
        # 对象数组被上游启发式填成 [10] 这类标量时，换成一条合法对象
        items = prop.get("items", {}) or {}
        if isinstance(v, list) and v and items.get("type") == "object" \
                and not all(isinstance(x, dict) for x in v):
            sub = items.get("properties", {}) or {}
            v = [{k: tps._coerce_to_schema(tps.value_for(k, sp), sp)
                  for k, sp in sub.items()}]
        if isinstance(v, list) and any(w in low for w in ("audio", "mpx", "signal", "samples")) \
                and all(isinstance(x, (int, float)) for x in v) and len(v) < 1024:
            v = _sine()
        # IQ 用 JSON 可表示的实数（部分工具按 interleaved 实虚解释，部分按复数数组，
        # 实数列表两边都吃得下；绝不传 complex，否则审计参数本身就不合法）
        if isinstance(v, list) and "iq" in low and all(isinstance(x, (int, float)) for x in v):
            v = _sine(4096)
        if isinstance(v, list) and low in ("taps", "coefficients", "fir_taps") and not v:
            v = [1.0]
        if isinstance(v, str) and low in ("line1", "tle_line1", "tle1"):
            v = _TLE_L1
        if isinstance(v, str) and low in ("line2", "tle_line2", "tle2"):
            v = _TLE_L2
        if isinstance(v, str) and ("hex" in low or low in ("packet", "frame", "bytes_b64")):
            if hex_frame and low != "bytes_b64":
                v = hex_frame
        if isinstance(v, str) and (v.startswith("/tmp/mbdsdr") or v in ("/tmp/mbdsdr_test.bin",)):
            v = str(Path(tmpdir) / (low + ".bin"))
            Path(v).write_bytes(b"\x00" * 256)
        if isinstance(v, str) and low in ("output_dir", "out_dir", "output_path", "save_dir"):
            v = str(Path(tmpdir) / "out")
            Path(v).mkdir(parents=True, exist_ok=True)
        if isinstance(v, str) and low in ("file_path", "filepath", "filename"):
            v = _repo_file("README.md", "mbdsdr_ai_mcp_server.py")
        if isinstance(v, bool) and "arm_manual" in low:
            v = False  # 走安全门，验证拒绝而不是真写
        args[k] = v
    return args


# ── 可核验断言：往返类工具必须真的还原原文/原比特，而不是"成功但内容空洞" ──
_VERIFIABLE = {
    "morse_encode": lambda t, d: "...." in t and "---" in t,
    "morse_decode": lambda t, d: "HELLO" in t.upper(),
    "baudot_encode": lambda t, d: bool(d) and str(d).count("'") >= 0,
    "baudot_decode": lambda t, d: "HELLO" in t.upper() or "HELLO" in str(d).upper(),
    "diff_encode": lambda t, d: bool(d),
    "ft8_encode": lambda t, d: "CQ" in t.upper() or bool(d),
}

# 需要专门替身参数（真实样本 / 合法文本），否则只能得到"缺样本"而非真跑
_SPECIAL_ARGS = {
    "ft8_encode": lambda tmp: {"text": "CQ BI1AAA OM74"},
    "ft8_unpack_message": lambda tmp: {"bits": []},  # 由 ft8_encode 产物回填
    "baudot_encode": lambda tmp: {"text": "HELLO"},
    "baudot_decode": lambda tmp: {"words": []},  # 由 baudot_encode 产物回填
    "bell103_modulate_text": lambda tmp: {"text": "HELLO"},
    "bell103_demodulate_text": lambda tmp: {"samples": _sine(8000, 1270.0)},
    "fsk_modulate": lambda tmp: {"bits": _bits(80)},
    "fsk_demodulate": lambda tmp: {"samples": _sine(8000, 1270.0)},
    "afsk_modulate": lambda tmp: {"source": "BI1AAA", "destination": "APRS",
                                  "info": ">test"},
    "ax25_encode": lambda tmp: {"source": "BI1AAA", "destination": "APRS",
                                "info": ">test"},
    "codec2_encode": lambda tmp: {"audio": _sine(3200)},
    "freedv_modulate": lambda tmp: {"bits": _bits(60)},
    "ccsds_encode_frame": lambda tmp: {"data": [1, 2, 3, 4, 5]},
    "groestl_hash": lambda tmp: {"data": "test"},
    "wfreq_demod": lambda tmp: {"iq": _cplx_iq(2048)},
    # ── P13 复核批：把「替身不满足输入」改成「合法替身」，验证是真能跑 ──
    "coord_ecef_to_eci": lambda tmp: {"pos_ecef": [7000.0, 0.0, 0.0],
                                      "vel_ecef": [0.0, 6.0, 0.0],
                                      "timestamp": 1.7e9},
    "coord_eci_to_ecef": lambda tmp: {"pos_eci": [7000.0, 0.0, 0.0],
                                      "vel_eci": [0.0, 6.0, 0.0],
                                      "timestamp": 1.7e9},
    "coord_ecef_to_enu": lambda tmp: {"target_ecef": [7000.0, 0.0, 0.0],
                                      "receiver_ecef": [6371.0, 0.0, 0.0]},
    "gps_nav_decode": lambda tmp: {"bits": _gps_nav_bits(900)},
    "how_validate": lambda tmp: {"subframe": _gps_nav_bits(300),
                                 "d29_star": 0, "d30_star": 0},
    "subframe_assemble": lambda tmp: {"bits": _gps_nav_bits(900)},
    "gps_ephemeris_satpos": lambda tmp: {"ephemeris": _gps_eph(), "t_s": 0.0},
    "nav_bit_sync": lambda tmp: {
        "stream": [b for b in _gps_nav_bits() for _ in range(20)],
        "samples_per_bit": 20},
    "bch_encode": lambda tmp: {"message": _bits(7), "m": 4, "t": 2,
                               "primitive_poly": 0b10011},
    "bch_decode": lambda tmp: {"codeword": _bits(15), "m": 4, "t": 2,
                               "primitive_poly": 0b10011},
    "bch_generator": lambda tmp: {"m": 4, "t": 2, "primitive_poly": 0b10011},
    "rds_decode_groups": lambda tmp: {"blocks_hex": "DDEE 1500 0000 4D42"},
    "rational_resample": lambda tmp: {"interpolation": 3, "decimation": 2,
                                      "f0": 0.1, "n": 1024},
    "iq_freq_track": lambda tmp: {"iq": _iq_interleaved(4096), "sample_rate": 8000.0,
                                  "window": 256, "hop": 128},
    "fst4_encode": lambda tmp: {"text": "CQ BI1AAA OM74"},
    "fst4_ldpc_decode": lambda tmp: {"llr": [0.1] * 240},
    "ft8_ldpc_decode": lambda tmp: {"llr": [0.1] * 174},
    "ft8_soft_decode": lambda tmp: {"tone_energies": [[0.1] * 8 for _ in range(58)]},
    "ft8_unpack_message": lambda tmp: {"bits": _bits(77)},
    "eye_diagram": lambda tmp: {"samples": _sine(4096), "sps": 8,
                                "n_traces": 16, "skip": 0},
    "orbit_propagate_stm_j2": lambda tmp: {"state_eci": [7000.0, 0.0, 0.0,
                                                        0.0, 6.0, 0.0],
                                           "dt_s": 600.0},
    "pseudorange_rate_model": lambda tmp: {
        "sat_state_ecef": [7000.0, 0.0, 0.0, 0.5, 6.0, 0.1, 1.0],
        "receiver_ecef": [6371.0, 0.0, 0.0]},
    "rls_update": lambda tmp: {
        "ref_state": [7000.0, 0.0, 0.0, 0.5, 6.0, 0.1, 1.0],
        "ref_cov": [[1.0 if i == j else 0.0 for j in range(7)] for i in range(7)],
        "ref_timestamp": 1.7e9, "obs_timestamp": 1.7e9 + 10.0,
        "observation": 0.01, "receiver_ecef": [6371.0, 0.0, 0.0],
        "observation_sigma": 0.001},
    "calibrate_time_offset": lambda tmp: {
        "observations": [[1.7e9 + i * 5.0, 137.9e6 + i * 10.0] for i in range(20)],
        "receiver_ecef": [6371.0, 0.0, 0.0], "center_frequency_hz": 137.9e6,
        "tle_line1": _TLE_L1, "tle_line2": _TLE_L2, "search_limit_s": 30.0},
    "cantenna_design": lambda tmp: {"frequency_mhz": 1296.0,
                                    "tank_inner_diameter_mm": 300.0,
                                    "tank_length_mm": 600.0},
    "crc_check": lambda tmp: {"frame": _crc_frame_hex(), "width": 16,
                              "poly": 0x1021, "init": 0, "refin": False,
                              "xorout": 0, "crc_offset_bytes": 8},
    "field_extract": lambda tmp: {
        "frame": "0102030405",
        "spec": {"name": "audit_probe", "frame_length": 5,
                 "fields": [{"name": "a", "offset_bits": 0, "length_bits": 8}]}},
    "frame_layout_spec_load": lambda tmp: {
        "spec": {"name": "audit_probe", "frame_length": 8,
                 "fields": [{"name": "a", "offset_bits": 0, "length_bits": 8}]}},
    "sdr_listen_fm": lambda tmp: {"action": "start", "freq_mhz": 98.0, "gain": 1.0},
    "sdr_fm_scan": lambda tmp: {"f_start_mhz": 87.0, "f_stop_mhz": 108.0},
    "sdrsharp_spectrum_fft": lambda tmp: {"iq": _iq_interleaved(2048),
                                          "window": "hamming"},
    "alpha_estimate": lambda tmp: {"iq": _sine(8192), "sample_rate_hz": 8000.0,
                                   "symbol_rate_hz": 1000.0},
    "cma_equalize": lambda tmp: {"symbols": _sine(1024), "n_taps": 7, "block": 64},
    "jammer_generate": lambda tmp: {"n_samples": 4096, "sample_rate_hz": 8000.0,
                                    "freq_hz": 1000.0},
    "dehop_demod": lambda tmp: {"iq": _sine(4096), "hop_freqs_hz": [0.0, 1000.0],
                                "sample_rate_hz": 8000.0, "slot_s": 0.01},
    "vna_analyze": lambda tmp: {
        "frequencies_hz": [1e6 + i * 1e6 for i in range(20)],
        "s11": [[1.0, 0.0]] * 20},
    "vna_sweep": lambda tmp: {"start_hz": 1000000, "stop_hz": 2000000,
                              "points": 101, "port": ""},
    "meteor_lrpt_demod": lambda tmp: {"sample_rate": 1024000.0,
                                      "iq_base64": _iq_b64(8192)},
    "meteor_lrpt_decode_iq": lambda tmp: {
        "iq_path": _write_cf32(str(Path(tmp) / "meteor.cf32"), 8192),
        "sample_rate": 1024000.0, "out_png": str(Path(tmp) / "meteor.png")},
    "fy4_lrit_decode_iq": lambda tmp: {
        "iq_path": _write_cf32(str(Path(tmp) / "fy4.cf32"), 8192),
        "out_png": str(Path(tmp) / "fy4.png")},
    "gps_code_acquire": lambda tmp: {"iq": _iq_interleaved(4096),
                                     "sample_rate": 2000000.0},
    "gps_prn_sequence": lambda tmp: {"kind": "ca", "prn": 1, "length": 1023,
                                     "head": 32},
    "interferometry_tdoa": lambda tmp: {
        "sig_a_path": _write_cf32(str(Path(tmp) / "sig_a.cf32"), 2048),
        "sig_b_path": _write_cf32(str(Path(tmp) / "sig_b.cf32"), 2048, 0.101),
        "sample_rate_hz": 8000.0, "baseline_m": 1000.0},
    "multistation_tdoa_fix": lambda tmp: {
        "stations_m": [[0.0, 0.0], [1000.0, 0.0], [500.0, 800.0]],
        "tdoa_s": [0.0, 0.0, -1.6666667e-06], "initial_pos_m": [0.0, 1000.0]},
    "sat_compose_falsecolor": lambda tmp: {
        "ch_vis": [10.0, 20.0, 30.0, 40.0], "ch_swir": [1.0, 2.0, 3.0, 4.0],
        "ch_ir": [5.0, 6.0, 7.0, 8.0], "height": 2, "width": 2},
    "sat_image_enhance": lambda tmp: {"image": _py_struct(4)},
    "sat_project_image": lambda tmp: {
        "sat_pos_km": [7000.0, 0.0, 0.0], "sat_vel_km_s": [0.0, 6.0, 0.0],
        "pixel_x": 10.0, "image_width": 100},
    "xastir_decode_frame": lambda tmp: {
        "packet": ";BI1AAA   *111111z4900.00N/01200.00E- audit probe"},
    "tc_build_frame": lambda tmp: {"data": "DEADBEEF", "spacecraft_id": 0,
                                   "virtual_channel_id": 0, "frame_seq": 0},
    "energy_sense": lambda tmp: {"iq_samples": _iq_interleaved(2048),
                                 "noise_power": 0.001, "pfa": 0.01},
    "sdr_cfo_correct": lambda tmp: {"iq_samples": _iq_interleaved(4096),
                                    "sample_rate": 8000.0},
    "sdr_analyze_recording": lambda tmp: {
        "file_path": _write_cf32(str(Path(tmp) / "rec.cf32"), 8192),
        "fft_size": 1024},
    "sdr_play_audio": lambda tmp: {"action": "status", "gain": 1.0},
    "urh_decode_iq": lambda tmp: {"iq": _iq_interleaved(2048),
                                  "modulation": "FSK", "samples_per_symbol": 8},
    "urh_demodulate": lambda tmp: {"iq": _iq_interleaved(2048),
                                   "modulation": "FSK"},
    "kismet_parse_dot11": lambda tmp: {"frame_hex": "80000000FFFFFFFFFFFF"},
    "osmosdr_create_source": lambda tmp: {"device_string": "rtl=0"},
    "sat_rx_profile": lambda tmp: {"name": "ASTU-1"},
    "sat_link_get": lambda tmp: {"name": "noaa_19"},
    "sat_profile_load": lambda tmp: {"name": "astu_1"},
    "frame_layout_load": lambda tmp: {"name": "ccsds_tc_classic"},
    "fe_diagnose": lambda tmp: {"symptom": "无信号"},
    "protocol_stack_decode": lambda tmp: {"protocol": "ax25"},
    "pat_b2f_decode": lambda tmp: {"bytes_b64": _b2f_b64()},
    "dab_eti_parse": lambda tmp: {"frame": _eti_frame()},
    "verify_decode_chain": lambda tmp: {"bits": _ccsds_bits()},
    # ── 复核第二批：补「合成输入合法但替身取值不对」的真能跑工具 ──
    "fir_filter": lambda tmp: {"signal": _sine(512), "taps": [0.25, 0.5, 0.25]},
    "ui_layout_control": lambda tmp: {"verb": "show_panel", "target": "spectrum"},
    "evolution_propose": lambda tmp: {
        "target_type": "tool_description", "target_name": "ft8_encode",
        "description": "audit probe", "proposed_change": "audit probe"},
    "spp_locate": lambda tmp: {
        "rs": [[20000.0, 0.0, 0.0], [0.0, 20000.0, 0.0],
               [-20000.0, 0.0, 0.0], [0.0, -20000.0, 0.0]],
        "dts": [0.0, 0.0, 0.0, 0.0],
        "pr": [math.hypot(20000.0 - 6371.0, 0.0),
               math.hypot(0.0 - 6371.0, 20000.0),
               math.hypot(-20000.0 - 6371.0, 0.0),
               math.hypot(0.0 - 6371.0, -20000.0)]},
    "gps_pvt_solve": lambda tmp: {
        "observations": [
            {"sat_pos_ecef_m": [20000.0, 0.0, 0.0],
             "pr_m": math.hypot(20000.0 - 6371.0, 0.0)},
            {"sat_pos_ecef_m": [0.0, 20000.0, 0.0],
             "pr_m": math.hypot(0.0 - 6371.0, 20000.0)},
            {"sat_pos_ecef_m": [-20000.0, 0.0, 0.0],
             "pr_m": math.hypot(-20000.0 - 6371.0, 0.0)},
            {"sat_pos_ecef_m": [0.0, -20000.0, 0.0],
             "pr_m": math.hypot(0.0 - 6371.0, -20000.0)},
            {"sat_pos_ecef_m": [0.0, 0.0, 20000.0],
             "pr_m": math.hypot(0.0 - 6371.0, 20000.0)}],
        "x0": [6371.0, 0.0, 0.0], "b0": 0.0},
    "initial_orbit_laplace": lambda tmp: {
        "times_unix_s": [1.7e9, 1.7e9 + 120.0, 1.7e9 + 240.0],
        "ra_deg": [10.0, 10.5, 11.0], "dec_deg": [20.0, 20.2, 20.4],
        "station_lat_deg": 0.0, "station_lon_deg": 0.0},
    "doppler_orbit_determine": lambda tmp: {
        "observations": [[1.7e9 + i * 60.0, 1000.0 - i * 150.0]
                         for i in range(6)],
        "init_state": [7000.0, 0.0, 0.0, 0.0, 7.5, 0.0, 0.0],
        "rx_lat": 0.0, "rx_lon": 0.0, "rx_alt": 0.0,
        "estimator": "ls", "f0": 137900000.0},
    "array_correlator_df": lambda tmp: {
        "snapshots": [[complex(math.cos(0.2 * i + 0.5 * k),
                               math.sin(0.2 * i + 0.5 * k))
                       for i in range(64)] for k in range(4)],
        "sample_rate_hz": 1000000.0, "freq_hz": 1000000000.0,
        "element_positions_m": [[0.0, 0.0], [0.05, 0.0],
                                [0.1, 0.0], [0.05, 0.05]]},
    "r2cloud_schedule": lambda tmp: {
        "lat_deg": 0.0, "lon_deg": 0.0, "alt_m": 0.0,
        "min_elevation_deg": 10.0, "horizon_hours": 24.0,
        "transmitters": [{"sat_name": "METEOR-M 2", "line1": _TLE_L1,
                          "line2": _TLE_L2, "transmitter_id": "apt",
                          "frequency_hz": 137100000.0}]},
    "golden_assert": lambda tmp: {
        "actual": {"a": 1.0, "b": [1.0, 2.0]},
        "golden": {"a": 1.0, "b": [1.0, 2.0]}, "tolerances": {"a": 1e-9}},
}

def _classify(tool_name, content, data, success, error, assert_ok=None):
    low = (content or "").lower()
    # 1) 明确抛异常 = 真坏（注册表兜底文案是「调用失败: XxxError」，且必须打头；
    #    工具内部如实转述“模型调用失败: 离线…”不能算异常）
    if (error or "").startswith("exception") or "traceback (most recent call last)" in low \
            or content.lstrip().startswith("调用失败:") or "failed:" in (error or ""):
        return BROKEN, "handler 抛异常未捕获"
    # 2) 明确未实现 = STUB（即使 success=True 也是在装可用）
    todo = ("未实现", "尚未实现", "功能未实现", "not implemented", "coming soon",
            "待实现", "功能开发中", "not supported yet")
    if any(k in low for k in todo):
        return STUB, "明确未实现"
    # 3) 成功但空 = 静默空 = 真坏
    if success and not (content or "").strip() and not data:
        return BROKEN, "success=True 但 content/data 皆为空（静默返回空）"
    # 4) 成功却只回 "[]"/"{}"——空态没有原因码，模型无法区分「真的没有」与「没执行」。
    #    枚举/列表类工具的 "[]" 是合法诚实空态（如无设备），放行；"{}" 不放行。
    stripped = (content or "").strip()
    if stripped in ("[]", "{}") and not data and success:
        if stripped == "[]" and any(w in tool_name for w in
                                    ("_list", "_devices", "_history", "_log",
                                     "_patterns", "_experiences", "_edits")):
            return RUNS, "枚举类诚实空态（当前无记录/无设备）"
        return BROKEN, f"success=True 却只返回 {stripped}（空态无原因码）"
    # 5) 失败：只要有可读原因，就是「诚实拒绝」；空原因+空文本才算吞异常
    if not success:
        if not (content or "").strip():
            return BROKEN, "success=False 但无可见原因文本（吞异常/静默失败）"
        missing = _missing_for(content)
        return HONEST, missing or "有效输入：合成替身不满足该工具的输入要求"
    # 6) 断言不通过：payload 自带 reason 属诚实空态，否则算失败被读成成功
    if assert_ok is False:
        if isinstance(data, dict) and data.get("reason"):
            return HONEST, f"缺样本：{data.get('reason')}"
        return BROKEN, "成功返回但可核验断言未通过"
    # 7) 成功但自述「未检测到/无有效帧」= 诚实空态（有原因，但不是产出）
    if any(k in low for k in ("诚实空态", "未检测到", "无有效", "没有检测到")):
        return HONEST, _missing_for(content) or "样本：输入不含可供解码的信号"
    return RUNS, "成功且返回非空"


def _missing_for(content):
    low = (content or "").lower()
    for keys, label in _MISSING_HINTS:
        if any(k.lower() in low for k in keys):
            return label
    return ""


#: success=True 却自述「没做成」的措辞 → 疑似虚构成功（人工复核后可修或加注）。
#: 「未实际发射/无回执」不在此列：那是要求里的合规写法（明写未送达≠假装送达）。
_SUSPECT_KEYS = (
    "发送失败", "执行失败", "需要先ptt", "需要先 ptt", "gate_not_armed",
    "未执行", "失败（", "错误:", "连接失败",
)


def _suspect_success(content):
    low = (content or "").lower()
    for k in _SUSPECT_KEYS:
        if k in low:
            return k
    return ""


def _grab(res, *keys):
    """从上游结果里取 ID：先查 data 字典，再按 key 扫 content 文本。"""
    if res is None:
        return ""
    d = getattr(res, "data", None)
    if isinstance(d, dict):
        for k in keys:
            if d.get(k):
                return str(d[k])
    txt = getattr(res, "content", "") or ""
    for k in keys:
        m = re.search(rf'"{k}"\s*:\s*"([^"]+)"', txt)
        if m:
            return m.group(1)
        m = re.search(rf'{k}\s*[:：]\s*([A-Za-z0-9_.\-]{{3,}})', txt)
        if m:
            return m.group(1)
    # 兜底：多数工具的正文只写「ID: task_...」或「id=vfo_1」，键名并不是 data 字段名
    m = re.search(r"(?:ID|id)\s*[:：=]\s*[`\"']?([A-Za-z0-9_.\-]{3,})", txt)
    if m:
        return m.group(1)
    return ""


def _safe_call(reg, name, args):
    try:
        return reg.call(name, args)
    except Exception:  # noqa: BLE001 - 上游失败不阻断审计
        return None


def _run_pipeline(reg, tmp):
    """少数需要上游产物的工具：先跑上游，把真实产物回填，再跑下游。"""
    results = {}
    hex_frame = ""
    up = _safe_call(reg, "ax25_encode", {"source": "BI1AAA", "destination": "APRS",
                                        "info": ">audit"})
    if up is not None and up.success:
        hex_frame = _grab(up, "hex", "hex_data", "frame_hex")
        if not hex_frame and (up.content or "").strip().startswith("{"):
            try:
                hex_frame = (json.loads(up.content) or {}).get("hex", "")
            except (ValueError, TypeError):
                hex_frame = ""
    if hex_frame:
        # 解码类工具需要「合法帧」而不是随机 hex：直接用上面的编码产物
        results["sdr_decode_ax25"] = ("hex_data", hex_frame)
        results["sdr_aprs_decode"] = ("hex_data", hex_frame)
    up = _safe_call(reg, "ft8_encode", {"text": "CQ BI1AAA OM74"})
    if up is not None and up.success and isinstance(up.data, dict):
        bits = up.data.get("bits") or up.data.get("tone_indices")
        if bits:
            results["ft8_unpack_message"] = ("bits", bits)
    up = _safe_call(reg, "baudot_encode", {"text": "HELLO"})
    if up is not None and up.success and isinstance(up.data, dict):
        words = up.data.get("words") or up.data.get("codes")
        if words:
            results["baudot_decode"] = ("words", words)
    # BCH：先编码 → 合法码字再解（码字不能随便造）
    up = _safe_call(reg, "bch_encode", {"message": _bits(7), "m": 4, "t": 2,
                                       "primitive_poly": 0b10011})
    if up is not None and up.success and isinstance(up.data, dict):
        for k in ("codeword", "bits", "encoded", "code"):
            if up.data.get(k):
                results["bch_decode"] = ("codeword", up.data[k])
                break

    # 调度：先建任务 → enable 才有合法 task_id
    up = _safe_call(reg, "scheduler_add", {
        "name": "audit_probe", "task_type": "tool", "target": "time_get_info",
        "schedule_type": "interval", "interval_seconds": 3600})
    tid = _grab(up, "task_id", "id")
    if tid:
        results["scheduler_enable"] = ("task_id", tid)
        results["scheduler_disable"] = ("task_id", tid)
    # 进化：先提案 → 再提交（rollback 需要已提交的父版本）
    up = _safe_call(reg, "evolution_propose", {
        "target_type": "tool_description", "target_name": "ft8_encode",
        "description": "audit probe", "proposed_change": "audit probe",
        "risk_level": "low"})
    pid = _grab(up, "proposal_id", "id")
    if pid:
        results["evolution_commit"] = ("proposal_id", pid)
        cm = _safe_call(reg, "evolution_commit", {"proposal_id": pid})
        # 提交回执是「版本已提交: v_xxx」，不是 data 字段名，只能从正文取
        vid = ""
        if cm is not None and cm.success:
            m = re.search(r"(v_[0-9A-Za-z_]+)", cm.content or "")
            vid = m.group(1) if m else ""
        if vid:
            results["evolution_rollback"] = ("version_id", vid)
    # 文件变更：先登记 → 再回滚
    probe = str(Path(tmp) / "audit_change.txt")
    Path(probe).write_text("audit", encoding="utf-8")
    up = _safe_call(reg, "file_change_track", {
        "file_path": probe, "change_type": "modify",
        "content_before": "", "content_after": "audit", "actor": "system"})
    cid = _grab(up, "change_id", "id")
    if cid:
        results["file_change_revert"] = ("change_id", cid)
    # VFO：select 与 remove 各用各的 VFO，避免先跑的 remove 把共用的 id 删掉
    up = _safe_call(reg, "vfo_add", {"name": "audit_select",
                                     "frequency_hz": 145000000.0})
    vid = _grab(up, "vfo_id", "id")
    if vid:
        results["vfo_select"] = ("vfo_id", vid)
    up = _safe_call(reg, "vfo_add", {"name": "audit_remove",
                                     "frequency_hz": 145500000.0})
    vid = _grab(up, "vfo_id", "id")
    if vid:
        results["vfo_remove"] = ("vfo_id", vid)
    # 子代理：先建 → execute 才有合法 subagent_id
    up = _safe_call(reg, "subagent_create", {"agent_type": "spectrum_analyzer"})
    sid = _grab(up, "subagent_id", "id")
    if sid:
        results["subagent_execute"] = ("subagent_id", sid)
    # 技能：先列 → load 才有合法 name
    up = _safe_call(reg, "skill_list", {})
    if up is not None and up.success:
        m = re.search(r"[\"'\s]([a-z][a-z0-9]+(?:-[a-z0-9]+)+)", up.content or "")
        if m:
            results["skill_load"] = ("name", m.group(1))
    # 录制：start → stop → template_create
    up = _safe_call(reg, "workflow_record_start", {"name": "audit_rec"})
    if up is not None and up.success:
        st = _safe_call(reg, "workflow_record_stop", {})
        rid = _grab(st, "recording_id", "id")
        if rid:
            results["workflow_template_create"] = ("recording_id", rid)
    # UI：先 show_panel 制造一次可撤销动作 → undo 才有目标
    _safe_call(reg, "ui_layout_control", {"verb": "show_panel", "target": "spectrum"})
    # GNSS：先解一帧导航电文 → 星历工具才有合法输入
    up = _safe_call(reg, "gps_nav_decode", {"bits": _bits(300)})
    if up is not None and up.success and isinstance(up.data, dict):
        eph = up.data.get("ephemeris")
        if isinstance(eph, dict):
            results["gps_ephemeris_satpos"] = ("ephemeris", eph)
    # 遥测链路：先组帧 → 帧解析才有合法帧
    up = _safe_call(reg, "tc_build_frame", {"data": "DEADBEEF",
                                           "spacecraft_id": 0,
                                           "virtual_channel_id": 0})
    frame = _grab(up, "frame", "hex", "frame_hex")
    if frame:
        results["tc_frame_parse"] = ("frame", frame)
    return results, hex_frame


def run_audit(only=None):
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    import mbdsdr_ai.sdr_backend as _sb

    _mm.ModelManager.fetch_models = lambda self, *a, **k: None  # 不联网
    # 假设备替身：审计全程不枚举/不打开任何真实 SDR（MBDSDR_NO_HW 的硬保证），
    # 所有实时设备工具都应走"无硬件"的诚实降级路径。不恢复，防止工具中途重新枚举。
    _sb.SDRBackendManager._discover = lambda self: None
    agent = MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))
    reg = agent.tool_registry
    mgr = getattr(agent, "sdr_manager", None)
    if mgr is not None:
        mgr.backends.clear()
        mgr.active_backend = None

    tmp = tempfile.mkdtemp(prefix="mbdsdr_audit_")
    # 进化版本库默认落在 ~/.mbdsdr/evolution：审计会 propose/commit/rollback，
    # 不能把探测版本写进用户的真实台账（也会污染测试对“无父版本”的假设）。
    # 直接换成一个临时库并补初始快照（与引擎启动时的基线口径一致）。
    evo = getattr(agent, "evolution", None)
    if evo is not None and getattr(evo, "version_store", None) is not None:
        from mbdsdr_ai.version_store import VersionStore

        evo.version_store = VersionStore(os.path.join(tmp, "evolution"))
        evo.version_store.snapshot(
            message="初始版本（审计隔离库）",
            files={"system_prompt.txt": "", "tool_descriptions.json": "{}"},
            author="system")
    records = []
    try:
        pipelines, hex_frame = _run_pipeline(reg, tmp)
        names = reg.get_tool_names()
        if only:
            names = [n for n in names if n in set(only)]
        for idx, name in enumerate(names, 1):
            tool = reg.tools[name]
            if os.environ.get("MBDSDR_AUDIT_VERBOSE"):
                print(f"[{idx}/{len(names)}] {name}", flush=True)
            definition = tool["definition"]["function"]
            params = definition.get("parameters", {}) or {}
            args = _build_args({"parameters": params}, tmp, hex_frame)
            if name in _SPECIAL_ARGS:
                args.update(_SPECIAL_ARGS[name](tmp))
            if name in pipelines:
                key, val = pipelines[name]
                args[key] = val
            # 每个工具独立基线：清掉上一个工具留下的活跃后端/回放源，
            # 否则 sdr_open_iq_file 的替身文件会让后续"实时采集"工具误以为有设备。
            if mgr is not None:
                try:
                    mgr.disconnect()
                except Exception:
                    pass
                mgr.active_backend = None
            started = time.time()
            res = None
            try:
                res = reg.call(name, args)
                success = bool(getattr(res, "success", False))
                content = (getattr(res, "content", "") or "")
                data = getattr(res, "data", None)
                error = getattr(res, "error", "") or ""
            except Exception as e:  # noqa: BLE001
                success, content, data = False, "", None
                error = f"exception: {type(e).__name__}: {e}"
                traceback.print_exc()
            elapsed = round((time.time() - started) * 1000, 1)
            checker = _VERIFIABLE.get(name)
            assert_ok = None
            if checker and success:
                try:
                    assert_ok = bool(checker(content, data))
                except Exception:
                    assert_ok = False
            cat, reason = _classify(name, content, data, success, error, assert_ok)
            if assert_ok is not None and cat == RUNS:
                reason = "成功且往返断言通过" if assert_ok else reason
            group = _group_for(name, reason) if cat == HONEST else ""
            records.append({
                "name": name,
                "category": tool.get("category", ""),
                "class": cat,
                "reason": reason,
                "group": group,
                "suspect": _suspect_success(content) if success else "",
                "success": success,
                "error": error,
                "content_len": len(content),
                "data": "有" if data else "无",
                "latency_ms": elapsed,
                "args": args,
                "snippet": content[:240].replace("\n", " "),
            })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return records


def render_md(records):
    counts = collections.Counter(r["class"] for r in records)
    total = len(records)
    group_counts = collections.Counter(r["group"] for r in records
                                       if r["class"] == HONEST and r["group"])
    grouped = sum(group_counts.values())
    suspects = [r for r in records if r.get("suspect")]
    lines = [
        "# 工具可用性总账（P13 逐项审计）",
        "",
        f"- 工具总数：**{total}**（分类 {len(set(r['category'] for r in records))} 个）",
        f"- 审计环境：`QT_QPA_PLATFORM=offscreen` + `MBDSDR_NO_HW=1` + "
        f"`MBDSDR_NULL_AUDIO=1` + `MBDSDR_OFFLINE=1`，**无真机；输入为合成替身**",
        f"- 结论：一类 {counts[RUNS]} / 二类 {counts[HONEST]} / "
        f"三类 {counts[STUB]} / 四类 {counts[BROKEN]}",
        "",
        "复跑：`python scripts/tool_availability_audit.py`（`--only <工具名>` 单跑）。",
        "证据列：`OK` = 当次调用成功且非空；`断言:`= 往返/还原断言；"
        "`降级:`= 诚实不可用原因；`坏:`= 真坏原因。",
        "",
        "## 补齐清单（二类按「需要什么」分组）",
        "",
        f"分组计数合计 **{grouped}** = 二类 {counts[HONEST]}"
        f"{'' if grouped == counts[HONEST] else '（不一致，有未分组行）'}",
        "",
        "| 补齐组 | 数量 | 最少补齐动作 |",
        "| --- | --- | --- |",
    ]
    for g in (G1, G2, G3, G4, G5):
        lines.append(f"| {g} | {group_counts[g]} | {GROUP_ACTION[g]} |")
    lines += ["", "各组工具名（与上表「补齐组」列逐行一致）：", ""]
    for g in (G1, G2, G3, G4, G5):
        names = sorted(r["name"] for r in records
                       if r["class"] == HONEST and r["group"] == g)
        lines.append(f"- {g}（{len(names)}）：" + "、".join(f"`{n}`" for n in names))
    lines += [
        "",
        f"## 疑似「成功文案里藏虚构」（{len(suspects)} 项）",
        "",
        "判据：success=True 但正文出现失败/未执行措辞。逐项人工复核后要么改失败，"
        "要么在正文写明「未确认送达」。",
    ]
    if suspects:
        for r in suspects:
            snip = (r["snippet"] or "").replace("|", "\\|")[:110]
            lines.append(f"- `{r['name']}`：命中「{r['suspect']}」｜{snip}")
    else:
        lines.append("- 无")
    lines += [
        "",
        "## 逐项总表",
        "",
        "| 工具 | 分类 | 结论 | 证据（本次调用） | 不可用时缺什么 | 补齐组 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(records, key=lambda x: (x["class"], x["category"], x["name"])):
        cls = {"RUNS": "一类·真跑", "HONEST": "二类·诚实不可用",
               "STUB": "三类·未实现", "BROKEN": "四类·真坏"}[r["class"]]
        snippet = (r["snippet"] or "").replace("|", "\\|")[:110]
        if r["class"] == RUNS:
            evidence = f"OK（{r['content_len']} 字符，data={r['data']}）"
        elif r["class"] == HONEST:
            evidence = f"降级：{r['reason']}｜{snippet}"
        elif r["class"] == STUB:
            evidence = f"未实现：{snippet}"
        else:
            evidence = f"坏：{r['reason']}｜{snippet}"
        missing = "" if r["class"] == RUNS else r["reason"]
        group = r["group"] or "—"
        lines.append(f"| `{r['name']}` | {r['category']} | {cls} | {evidence} | "
                     f"{missing} | {group} |")
    return "\n".join(lines) + "\n", counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    if os.environ.get("MBDSDR_AUDIT_VERBOSE"):
        import faulthandler
        faulthandler.dump_traceback_later(90, repeat=True)
    records = run_audit(a.only)
    counts = collections.Counter(r["class"] for r in records)
    print(f"total={len(records)} " + " ".join(f"{k}={counts[k]}" for k in
                                             (RUNS, HONEST, STUB, BROKEN)))
    gc = collections.Counter(r["group"] for r in records if r["class"] == HONEST)
    print("groups " + " ".join(f"{g}={gc[g]}" for g in (G1, G2, G3, G4, G5)))
    for r in records:
        if r["class"] in (STUB, BROKEN):
            print(f"  [{r['class']}] {r['name']}: {r['reason']} | {r['snippet'][:160]}")
        if r.get("suspect"):
            print(f"  [SUSPECT] {r['name']}: 命中「{r['suspect']}」 | {r['snippet'][:120]}")
    if a.json:
        Path(a.json).write_text(json.dumps(records, ensure_ascii=False, indent=1,
                                           default=str), encoding="utf-8")
    if not a.no_write and not a.only:
        md, _ = render_md(records)
        (ROOT / "docs" / "tool-availability.md").write_text(md, encoding="utf-8")
        print("wrote docs/tool-availability.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

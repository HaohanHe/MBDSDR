# SPDX-License-Identifier: MIT
"""数字解码模式能力表 + 数据驱动路由（P5.2）。

单一事实源：所有数字模式的「实现文件 / 依赖模块 / 输入格式 / 输出类型 /
采样率与带宽约束 / 证据等级（真实度）/ 最小采样条件」集中登记在
`DIGITAL_MODE_CAPABILITIES`，`decode_digital_mode` 只做「表查找 → 前置校验
→ 交给已登记的 handler」，不再是一长串 if/elif。

三类诚实错误码（互不混淆）：
  * `unknown_mode`        —— 模式不在能力表（附可用模式清单）；
  * `missing_dependency`  —— 表里登记的内置实现模块导不进来；
  * `missing_sample`      —— 输入捕获文件不存在（缺样本，不是解码失败）。
其余：`unsupported_format`（格式不匹配）、`decode_error`（解码异常）、
`not_implemented`（能力未接线，例如 ft8 通用入口）、
`sample_rate_mismatch`（IQ 模式采样率非整数倍/低于下限，拒绝静默取整）、
`insufficient_samples`（样本不足「最短一帧」，运行期按协议常量校验）、
`invalid_wav`（WAV 容器/编码不可解析）、`param_not_in_enum`（如 POCSAG
非 512/1200/2400 baud 显式拒绝）。APRS 已接线到
`decode_pipeline.run_aprs_pipeline`（NFM 鉴频 → Bell202 AFSK → NRZI →
HDLC → CRC-16 → AX.25），失败原因码沿用流水线
（`nothing_detected`/`truncated_frame`/`crc_failed`/`insufficient_samples`/
`sample_rate_mismatch`）。

证据等级（`evidence`）口径与 P2 真样本盘点一致：
  real      = 有真实 OTA/真值对拍（LRPT/FT8/ADS-B）
  partial   = 合成链自洽 + 部分字段真值，尚无 OTA 实绩
  no_sample = 无任何真录样本，只按标准/协议数学性质钉扎
`min_sample_condition` 写明「补到什么样一段真录才能破零」，供以后取数。

一致性守卫：`tests/test_digital_modes_capability.py` 校验表 ↔ handler 注册
双向齐全、依赖模块可导入、实现文件存在，并与 `core.controller` 的解码器
注册清单对齐（防以后加解码器漏登记）。

审计：`run_digital_mode` 是对外唯一入口，返回前经 `decode_audit.maybe_audit`
按 `MBDSDR_DECODE_AUDIT_DIR` 开关写一行 JSONL 元数据（含前置校验失败），
审计故障不影响解码结果。
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import os
import shutil
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .error_catalog import user_message

# ---- 诚实错误码 ---------------------------------------------------------
ERR_UNKNOWN_MODE = "unknown_mode"
ERR_MISSING_DEPENDENCY = "missing_dependency"
ERR_MISSING_SAMPLE = "missing_sample"
ERR_UNSUPPORTED_FORMAT = "unsupported_format"
ERR_DECODE_ERROR = "decode_error"
ERR_NOT_IMPLEMENTED = "not_implemented"
# 采样率/样本长度约束沿用 P5 线的码，不另造新码。
ERR_SAMPLE_RATE_MISMATCH = "sample_rate_mismatch"
ERR_INSUFFICIENT_SAMPLES = "insufficient_samples"
ERR_INVALID_WAV = "invalid_wav"

# ---- 证据等级（真实度）--------------------------------------------------
EVIDENCE_REAL = "real"            # 真录/真值对拍
EVIDENCE_PARTIAL = "partial"      # 合成链自洽，无 OTA 实绩
EVIDENCE_NO_SAMPLE = "no_sample"  # 无真样本，仅协议数学性质

EVIDENCE_LEVELS = (EVIDENCE_REAL, EVIDENCE_PARTIAL, EVIDENCE_NO_SAMPLE)


@dataclass(frozen=True)
class DigitalModeCapability:
    """一种数字模式的显式能力声明（能力表的一行）。"""
    mode: str
    impl: str                 # 实现文件（相对 mbdsdr_ai/，人读+存在性校验）
    dependency: str           # 内置实现模块导入路径（"" 表示通用入口未接线）
    input_formats: Tuple[str, ...]   # 接受的扩展名；("*",) 表示不设格式门
    output_kind: str          # messages / frames / image / text / note
    sample_rate_rule: str     # 采样率/带宽要求（人读）
    evidence: str             # real / partial / no_sample
    min_sample_condition: str # 最小采样条件（补真样本时按此取数）
    external_tools: Tuple[str, ...] = ()   # 可选外部增强，不虚构其解码结果
    # True = 格式校验由 handler 自己做（窄带入口有自己的 rejected 契约），
    # 路由层不再重复拦截，避免同一错误两套文案。
    delegate_format_check: bool = False
    # 该模式实际会产出的失败码（能力表登记，测试校验都在 error_catalog 里）。
    failure_codes: Tuple[str, ...] = ()
    # 运行期「最短一帧」校验（秒）；0 表示 handler 内部已按自身协议常量校验。
    min_duration_seconds: float = 0.0
    min_duration_rule: str = ""


DIGITAL_MODE_CAPABILITIES: Dict[str, DigitalModeCapability] = {
    "adsb": DigitalModeCapability(
        mode="adsb",
        impl="adsb.py",
        dependency="mbdsdr_ai.adsb",
        input_formats=(".cf32", ".cfile"),
        output_kind="frames",
        sample_rate_rule="fs ≥ 2MHz 且为 1MHz 整数倍（Mode-S 1Mbit/s 前导 8us）",
        evidence=EVIDENCE_REAL,
        min_sample_condition=(
            "1090MHz 下行 .cf32，fs=2MHz 或 4MHz，≥ 3 个干净 DF17 前导"
            "（CRC-24 通过与 dump1090 对拍；多帧扫描见 adsb.decode_baseband）"
        ),
        external_tools=("dump1090", "dump1090-fa"),
        failure_codes=(
            ERR_SAMPLE_RATE_MISMATCH, ERR_INSUFFICIENT_SAMPLES,
            "preamble_not_found", "no_frame_recovered",
            ERR_UNSUPPORTED_FORMAT, ERR_DECODE_ERROR, ERR_MISSING_SAMPLE,
            ERR_MISSING_DEPENDENCY,
        ),
        # 运行期最短一帧：前导 8µs + 112 bit = 120µs。
        min_duration_seconds=120e-6,
        min_duration_rule="前导 8µs + 112 bit 长帧（120 bit @ 1Mbit/s）",
    ),
    "ft4": DigitalModeCapability(
        mode="ft4",
        impl="ft4_modem.py",
        dependency="mbdsdr_ai.ft4_modem",
        input_formats=(".wav",),
        output_kind="messages",
        sample_rate_rule="实音频任意 fs（内部重采样到 375Hz/sym，中心 ~1500Hz）",
        evidence=EVIDENCE_PARTIAL,
        min_sample_condition=(
            "SSB 解调后 12kHz PCM16 单声道 .wav，含完整 FT4 75s 帧且"
            "SNR ≥ 0dB（官方样例库可作真值）"
        ),
        delegate_format_check=True,
        failure_codes=(ERR_UNSUPPORTED_FORMAT, ERR_INVALID_WAV,
                       ERR_INSUFFICIENT_SAMPLES, ERR_DECODE_ERROR,
                       "invalid_input"),
    ),
    "ft8": DigitalModeCapability(
        mode="ft8",
        impl="ft8_lite.py",
        dependency="",       # 通用入口未接线：只说事实，不假装能解
        input_formats=("*",),
        output_kind="note",
        sample_rate_rule="实音频 12kHz 单声道（通用入口未接线，参数不生效）",
        evidence=EVIDENCE_REAL,
        min_sample_condition=(
            "真录已在 test_ft8_* 中对拍；通用 WAV 入口未接线，需调用"
            "ft8_lite.decode_ft8_audio 或走专用工具"
        ),
        external_tools=("jt9", "wsjtx"),
        failure_codes=(ERR_NOT_IMPLEMENTED,),
    ),
    "aprs": DigitalModeCapability(
        mode="aprs",
        impl="decode_pipeline.py",
        dependency="mbdsdr_ai.decode_pipeline",
        input_formats=(".wav", ".cf32", ".cfile"),
        output_kind="frames",
        sample_rate_rule=(
            ".wav：16-bit PCM 单声道，任意 fs ≥ 4800Hz（44.1k/48k/8k 均可），"
            "解码前重采样到 48kHz；.cf32：NFM 复基带 fs ≥ 16000Hz，解码前鉴频"
            "并重采样到 48kHz（40 样/bit）"
        ),
        evidence=EVIDENCE_PARTIAL,
        min_sample_condition=(
            "现有证据仅为**合成验证**（仓内调制 2 帧、逐字节对拍），**无 OTA "
            "证据**；补真录条件：144.640/144.390/144.800MHz NFM 下行复基带 "
            "≥ 5s，含至少 1 个 1200bd Bell202 AX.25 帧（CRC-16 通过）"
        ),
        external_tools=("direwolf", "aprs"),
        failure_codes=(
            ERR_UNSUPPORTED_FORMAT, ERR_INSUFFICIENT_SAMPLES,
            ERR_SAMPLE_RATE_MISMATCH, "nothing_detected", "truncated_frame",
            "crc_failed", ERR_DECODE_ERROR,
        ),
        # 运行期最短样本由 run_aprs_* 按 136 bit HDLC 帧 + 48kHz 重采样校验。
    ),
    "pocsag": DigitalModeCapability(
        mode="pocsag",
        impl="pocsag_decoder.py",
        dependency="mbdsdr_ai.pocsag_decoder",
        input_formats=(".wav", ".cf32", ".cfile"),
        output_kind="messages",
        sample_rate_rule="音频任意 fs；.cf32 走宽带 FM 鉴频（fs ≥ 2×占用带宽）",
        evidence=EVIDENCE_PARTIAL,
        min_sample_condition=(
            "137/153/466MHz NFM 下行音频 ≥ 10s，含 ≥ 2 条 POCSAG 批"
            "（1200bd、±4.5kHz 偏移、BCH(31,21) 可纠）"
        ),
        failure_codes=(
            ERR_UNSUPPORTED_FORMAT, "param_not_in_enum",
            ERR_INSUFFICIENT_SAMPLES, ERR_DECODE_ERROR,
        ),
        # POCSAG 最短：前导 576 bit + 同步字 32 bit + 一个 16 码字批（544 bit）。
        min_duration_seconds=0.0,  # 按 baud 动态计算，见 _min_duration_seconds
        min_duration_rule="前导 576 bit + 同步字 32 bit + 一个 16 码字批（544 bit）",
    ),
    "acars": DigitalModeCapability(
        mode="acars",
        impl="acars_decoder.py",
        dependency="mbdsdr_ai.acars_decoder",
        input_formats=(".wav", ".cf32", ".cfile"),
        output_kind="messages",
        sample_rate_rule="音频任意 fs（内部 12.5kHz 走带）；.cf32 走宽带 FM 鉴频",
        evidence=EVIDENCE_PARTIAL,
        min_sample_condition=(
            "131.55MHz AM 下行解调音频 ≥ 5s，含 ≥ 1 条 2400baud MSK ACARS"
            "报文且 CRC 通过"
        ),
        failure_codes=(
            ERR_UNSUPPORTED_FORMAT, ERR_INSUFFICIENT_SAMPLES,
            ERR_DECODE_ERROR,
        ),
        # 最短 ACARS 报文：16 字符前导 + 16 字符最小数据体（8 bit/字符）。
        min_duration_seconds=0.2,
        min_duration_rule="16 字符前导 + 16 字符最小数据体（8 bit/字符）",
    ),
    "wspr": DigitalModeCapability(
        mode="wspr",
        impl="wspr_modem.py",
        dependency="mbdsdr_ai.wspr_modem",
        input_formats=(".wav",),
        output_kind="messages",
        sample_rate_rule="实音频 375Hz/sym 网格（参考 12kHz），四音间隔 1.464Hz",
        evidence=EVIDENCE_PARTIAL,
        min_sample_condition=(
            "USB 解调后 12kHz PCM16 .wav，含完整 110.6s WSPR 传输且"
            "SNR ≥ -28dB（官方 wsprd 结果可作真值）"
        ),
        external_tools=("wsprd", "wsjtx"),
        delegate_format_check=True,
        failure_codes=(ERR_UNSUPPORTED_FORMAT, ERR_INVALID_WAV,
                       ERR_INSUFFICIENT_SAMPLES, ERR_DECODE_ERROR,
                       "invalid_input"),
        # 完整 162 符号 WSPR 传输 = 162 / 1.4648 baud ≈ 110.6s，见 handler。
        min_duration_seconds=0.0,
        min_duration_rule="完整 162 符号 WSPR 传输（110.6s）",
    ),
    "ais": DigitalModeCapability(
        mode="ais",
        impl="ais_decoder.py",
        dependency="mbdsdr_ai.ais_decoder",
        input_formats=(".cf32", ".cfile"),
        output_kind="messages",
        sample_rate_rule="fs 为 9600 整数倍且 ≥ 19200（GMSK BT=0.4、9600bd）",
        evidence=EVIDENCE_NO_SAMPLE,
        min_sample_condition=(
            "161.975/162.025MHz 信道化复基带 .cf32，fs=192000 或 96000，"
            "≥ 5s 且含 ≥ 3 条 FCS 通过的 AIS 报文（含 Type 1 位置）"
        ),
        failure_codes=(
            ERR_UNSUPPORTED_FORMAT, ERR_SAMPLE_RATE_MISMATCH,
            ERR_INSUFFICIENT_SAMPLES, ERR_DECODE_ERROR,
        ),
        # 最短一帧：168 bit 报文 + 16 bit FCS + 16 bit 标志 = 200 bit。
        min_duration_seconds=200.0 / 9600.0,
        min_duration_rule="168 bit 报文 + 16 bit FCS + 16 bit 标志（200 bit）",
    ),
    "navtex": DigitalModeCapability(
        mode="navtex",
        impl="navtex_decoder.py",
        dependency="mbdsdr_ai.navtex_decoder",
        input_formats=(".wav",),
        output_kind="messages",
        sample_rate_rule="音频任意 fs（100bd FSK，中心 1000Hz，±85Hz）",
        evidence=EVIDENCE_NO_SAMPLE,
        min_sample_condition=(
            "518/490kHz USB 解调后音频 ≥ 60s，含完整 ZCZC 帧与至少 3 行"
            "正文（SITOR-B 相位锁定成立）"
        ),
        failure_codes=(
            ERR_UNSUPPORTED_FORMAT, ERR_INSUFFICIENT_SAMPLES,
            ERR_DECODE_ERROR,
        ),
        # 最短：16 定相字符 + 8 头 + 4 尾部字符，7 bit/字符 × 2 分集。
        min_duration_seconds=(16 + 8 + 4) * 7 * 2 / 100.0,
        min_duration_rule="16 定相 + 8 头 + 4 尾部字符（7 bit/字符，双发分集）",
    ),
    "meteor": DigitalModeCapability(
        mode="meteor",
        impl="meteor_sat.py",
        dependency="mbdsdr_ai.meteor_sat",
        input_formats=(".cf32", ".cfile"),
        output_kind="symbols/bits",
        sample_rate_rule=(
            "fs ≥ 144000 且为 72k 符号率整数倍（LRPT QPSK），"
            "推荐 288000/576000"
        ),
        evidence=EVIDENCE_PARTIAL,
        min_sample_condition=(
            "Meteor-M2 137.9MHz 下行 .cf32 ≥ 20s，Viterbi 可锁且 RS 全纠"
            "帧 ≥ 2（本仓 LRPT 真录链可作真值）"
        ),
        failure_codes=(
            ERR_UNSUPPORTED_FORMAT, ERR_SAMPLE_RATE_MISMATCH,
            ERR_INSUFFICIENT_SAMPLES, ERR_DECODE_ERROR,
        ),
        # 一个完整 Forney 交织块：36 分支 × 35 × 2048 bit = 2,580,480 bit，
        # QPSK 每符号 2 bit → 1,290,240 符号 → 72k 符号率下 17.92s。
        min_duration_seconds=(36 * 35 * 2048) / 2 / 72000.0,
        min_duration_rule="一个完整 Forney 交织块（36×35×2048 bit）",
    ),
}


# =========================================================================
# handler 注册处（能力表的另一侧；两处必须双向齐全）
# =========================================================================
@dataclass
class _Ctx:
    input_path: str
    ext: str
    output_dir: str
    sample_rate: float
    baud: int
    center_frequency_hz: float
    frequency_search_hz: Optional[Tuple[float, float]]
    frequency_offsets_hz: Optional[Sequence[float]]
    time_offsets_seconds: Optional[Sequence[float]]
    available_tools: List[str]
    result: Dict[str, Any]


def _route_adsb(ctx: _Ctx) -> Dict[str, Any]:
    """内置纯 numpy Mode-S DF17 多帧扫描（CRC-24 MSB-first、8us 前导模板）。"""
    from .adsb import decode_baseband

    result = ctx.result
    try:
        import numpy as np
        raw = np.fromfile(ctx.input_path, dtype=np.float32)
        iq = raw[0::2] + 1j * raw[1::2]
        # 真做多帧扫描：每个前导独立判决 + CRC，最多 64 个候选（扫描预算）。
        d = decode_baseband(iq, fs=float(ctx.sample_rate), max_frames=64)
        frames = d.get("frames", [])
        result["found"] = bool(d.get("found", False))
        result["crc_ok"] = bool(d.get("crc_ok", False))
        result["icao_hex"] = d.get("icao")
        result["callsign"] = d.get("callsign")
        result["raw_hex"] = d.get("raw_bytes")
        result["preamble_index"] = d.get("preamble_index")
        result["frames"] = frames
        result["num_frames"] = int(d.get("num_frames", 0))
        result["num_preamble_candidates"] = int(
            d.get("num_preamble_candidates", 0))
        if not result["found"]:
            result["error"] = user_message(
                "preamble_not_found", detail="未找到 8µs 前导模板")
            result["error_code"] = "preamble_not_found"
        result["note"] = (
            "内置纯 numpy Mode-S DF17 多帧扫描（前导模板 + PPM + CRC-24），"
            f"CRC 通过 {result['num_frames']} 帧"
            f"（前导候选 {result['num_preamble_candidates']} 个）"
        )
    except Exception as e:
        result["analysis_error"] = str(e)
    return result


def _route_ft4(ctx: _Ctx) -> Dict[str, Any]:
    from .decoders import _decode_ft4_wav
    return _decode_ft4_wav(
        ctx.input_path, ctx.ext, ctx.result, ctx.center_frequency_hz,
        ctx.frequency_search_hz,
    )


def _route_ft8(ctx: _Ctx) -> Dict[str, Any]:
    """通用入口未接线：只陈述仓内已有能力，不虚构 WAV 解码结果。"""
    result = ctx.result
    result["note"] = (
        "仓内已有 FT8 Costas 同步、LDPC(174,91)、CRC14 与 77 位消息编解码；"
        "ft8_lite.decode_ft8_audio 是独立音频 API，本通用入口尚未接线，"
        "因此不在这里宣称已完成 FT8 WAV 解码。"
    )
    if ctx.available_tools:
        result["note"] += f" 检测到可选外部工具 {ctx.available_tools[0]}。"
    result.update({"ok": False, "status": ERR_NOT_IMPLEMENTED,
                   "error_code": ERR_NOT_IMPLEMENTED})
    return result


def _route_aprs(ctx: _Ctx) -> Dict[str, Any]:
    """APRS：真接流水线；.wav 走 PCM 入口，.cf32/.cfile 走 NFM 入口。"""
    result = ctx.result
    if ctx.ext not in (".cf32", ".cfile", ".wav"):
        result["error"] = user_message(
            "unsupported_format", mode="APRS",
            allowed=".wav（16-bit PCM 单声道）、.cf32/.cfile（NFM 复基带）",
            actual=ctx.ext)
        result["error_code"] = ERR_UNSUPPORTED_FORMAT
        return result

    try:
        if ctx.ext == ".wav":
            from .decode_pipeline import run_aprs_wav_pipeline
            rep = run_aprs_wav_pipeline(ctx.input_path, target_fs=48000.0)
        else:
            from .decode_pipeline import run_aprs_pipeline
            rep = run_aprs_pipeline(ctx.input_path, fs=float(ctx.sample_rate),
                                    target_fs=48000.0)
    except Exception as exc:
        result["error"] = f"APRS 流水线异常: {type(exc).__name__}: {exc}"
        result["error_code"] = ERR_DECODE_ERROR
        return result

    for key in ("ok", "status", "error_code", "reason", "stage", "detail",
                "stages", "content"):
        result[key] = rep.get(key)
    frames = rep.get("content", {}).get("frames", [])
    result["frames"] = frames
    result["num_frames"] = int(rep.get("num_frames", 0))
    result["dropped_frames"] = int(rep.get("dropped_frames", 0))
    result["truncated_fragments"] = int(rep.get("truncated_fragments", 0))
    result["messages"] = []
    result["num_messages"] = 0
    if rep.get("ok"):
        result["note"] = (
            "APRS/AX.25 逐级解码：NFM 鉴频 → Bell202 AFSK 1200bd → NRZI → "
            "HDLC 0x7E/去位填充 → CRC-16 → AX.25；"
            f"解出 {len(frames)} 帧（证据为合成 fixture，非 OTA）。"
        )
    else:
        result["note"] = (
            f"APRS 解码失败于 {rep.get('stage')} 级：{rep.get('detail')}"
            "（合成 fixture 验证；无 OTA 证据）"
        )
    return result


def _route_pocsag(ctx: _Ctx) -> Dict[str, Any]:
    from .decoders import _demod_iq_to_audio, _read_wav_audio
    from .pocsag_decoder import pocsag_decode

    result = ctx.result
    if ctx.baud not in (512, 1200, 2400):
        # 不再静默降级为 512：非法 baud 显式拒绝，并列出合法集。
        result["ok"] = False
        result["status"] = "error"
        result["error_code"] = "param_not_in_enum"
        result["error"] = user_message(
            "param_not_in_enum", param="baud",
            allowed="512/1200/2400", actual=str(ctx.baud))
        return result
    baud = int(ctx.baud)
    if ctx.ext == ".wav":
        audio, a_sr, err = _read_wav_audio(ctx.input_path)
    elif ctx.ext in (".cf32", ".cfile"):
        audio, a_sr, err = _demod_iq_to_audio(ctx.input_path, ctx.sample_rate)
    else:
        audio, a_sr = None, 0.0
        err = f"POCSAG 需 .wav 音频或 .cf32 IQ，收到 {ctx.ext}"
        result["error_code"] = ERR_UNSUPPORTED_FORMAT
    if err:
        result["error"] = err
    else:
        try:
            msgs = pocsag_decode(audio, a_sr, baud)
            result["messages"] = msgs
            result["num_messages"] = len(msgs)
            result["baud"] = baud
            result["note"] = (
                f"内置 multimon-ng 口径 POCSAG 解码（BCH(31,21) 纠错，{baud} baud）；"
                f"解出 {len(msgs)} 条消息。"
            )
        except Exception as e:
            result["error"] = f"POCSAG 解码失败: {e}"
    return result


def _route_acars(ctx: _Ctx) -> Dict[str, Any]:
    from .acars_decoder import ACARSDecoder
    from .decoders import _demod_iq_to_audio, _read_wav_audio

    result = ctx.result
    if ctx.ext == ".wav":
        audio, a_sr, err = _read_wav_audio(ctx.input_path)
    elif ctx.ext in (".cf32", ".cfile"):
        audio, a_sr, err = _demod_iq_to_audio(
            ctx.input_path, ctx.sample_rate, out_sr=12500)
    else:
        audio, a_sr = None, 0.0
        err = f"ACARS 需 .wav 音频或 .cf32 IQ，收到 {ctx.ext}"
        result["error_code"] = ERR_UNSUPPORTED_FORMAT
    if err:
        result["error"] = err
    else:
        try:
            dec = ACARSDecoder(sample_rate=a_sr)
            msgs = dec.decode_audio(audio)
            result["messages"] = [m.to_dict() for m in msgs]
            result["num_messages"] = len(msgs)
            result["crc_ok_count"] = sum(1 for m in msgs if m.crc_ok)
            result["note"] = (
                f"内置 MSK 解调+帧同步+解析（2400 baud），解出 {len(msgs)} 条 ACARS 消息"
                f"（CRC 通过 {sum(1 for m in msgs if m.crc_ok)} 条）。"
            )
        except Exception as e:
            result["error"] = f"ACARS 解码失败: {e}"
    return result


def _route_wspr(ctx: _Ctx) -> Dict[str, Any]:
    from .decoders import _decode_wspr_wav
    return _decode_wspr_wav(
        ctx.input_path, ctx.ext, ctx.result, ctx.center_frequency_hz,
        ctx.frequency_offsets_hz, ctx.time_offsets_seconds,
    )


def _route_ais(ctx: _Ctx) -> Dict[str, Any]:
    from .ais_decoder import ais_decode_iq

    result = ctx.result
    if ctx.ext in (".cf32", ".cfile"):
        try:
            import numpy as np
            raw = np.fromfile(ctx.input_path, dtype=np.float32)
            iq = raw[0::2] + 1j * raw[1::2]
            d = ais_decode_iq(iq, float(ctx.sample_rate))
            result["messages"] = d["messages"]
            result["num_messages"] = d["num_messages"]
            result["dropped_frames"] = d["dropped_frames"]
            result["note"] = (
                "内置 AIS 解码（GMSK 9600bd + HDLC + ITU-R M.1371 "
                "Type 1/2/3/5/18/24A 字段解析，FCS 校验失败帧丢弃并计数"
                f" {d['dropped_frames']}）；解出 {d['num_messages']} 条。"
            )
        except Exception as e:
            result["error"] = f"AIS 解码失败: {e}"
    else:
        result["error"] = user_message(
            "unsupported_format", mode="AIS",
            allowed=".cf32/.cfile 复基带（9600bd GMSK，fs 为 9600 整数倍）",
            actual=ctx.ext)
        result["error_code"] = ERR_UNSUPPORTED_FORMAT
    return result


def _route_navtex(ctx: _Ctx) -> Dict[str, Any]:
    from .decoders import _read_wav_audio
    from .navtex_decoder import navtex_decode_audio

    result = ctx.result
    if ctx.ext == ".wav":
        audio, a_sr, err = _read_wav_audio(ctx.input_path)
        if err:
            result["error"] = err
        else:
            try:
                d = navtex_decode_audio(audio, a_sr)
                result["messages"] = d["messages"]
                result["num_messages"] = d["num_messages"]
                result["phasing_ok"] = d["phasing_ok"]
                result["fec_corrected"] = d["fec_corrected"]
                result["fec_lost"] = d["fec_lost"]
                result["note"] = (
                    "内置 NAVTEX 解码（SITOR-B 100bd FSK + CCIR 476 恒比检错"
                    f"+ 时间分集纠错 {d['fec_corrected']} 字符）；"
                    f"解出 {d['num_messages']} 条报文。"
                )
            except Exception as e:
                result["error"] = f"NAVTEX 解码失败: {e}"
    else:
        result["error"] = user_message(
            "unsupported_format", mode="NAVTEX",
            allowed=".wav 音频（SSB 解调后 100bd FSK，中心 1kHz）",
            actual=ctx.ext)
        result["error_code"] = ERR_UNSUPPORTED_FORMAT
    return result


def _route_meteor(ctx: _Ctx) -> Dict[str, Any]:
    from .meteor_sat import meteor_lrpt_demod

    result = ctx.result
    if ctx.ext in (".cf32", ".cfile"):
        try:
            import numpy as np
            raw = np.fromfile(ctx.input_path, dtype=np.float32)
            iq = raw[0::2] + 1j * raw[1::2]
            d = meteor_lrpt_demod(iq, float(ctx.sample_rate))
            result["lrpt"] = {
                k: d[k] for k in ("symbols", "coded_bits", "decoded_bits", "sps")
            }
            result["note"] = (
                "内置 Meteor LRPT 解调骨架（QPSK→DQPSK 差分→卷积去交织→Viterbi），"
                f"输出 symbols/bits（{d['sps']} sps），不产出图像；"
                "帧解析与图像重组见 meteor_sat 工具面。"
            )
        except Exception as e:
            result["error"] = f"Meteor LRPT 解调失败: {e}"
    else:
        result["error"] = user_message(
            "unsupported_format", mode="Meteor LRPT",
            allowed=".cf32/.cfile 复基带", actual=ctx.ext)
        result["error_code"] = ERR_UNSUPPORTED_FORMAT
    return result


DIGITAL_MODE_HANDLERS: Dict[str, Callable[[_Ctx], Dict[str, Any]]] = {
    "adsb": _route_adsb,
    "ft4": _route_ft4,
    "ft8": _route_ft8,
    "aprs": _route_aprs,
    "pocsag": _route_pocsag,
    "acars": _route_acars,
    "wspr": _route_wspr,
    "ais": _route_ais,
    "navtex": _route_navtex,
    "meteor": _route_meteor,
}


# =========================================================================
# 能力表查询（文档 / UI / 测试共用）
# =========================================================================
def capability_matrix() -> List[Dict[str, Any]]:
    """导出整张能力表（纯数据，供文档生成与一致性测试）。"""
    rows = []
    for mode in sorted(DIGITAL_MODE_CAPABILITIES):
        cap = DIGITAL_MODE_CAPABILITIES[mode]
        rows.append({
            "mode": cap.mode,
            "impl": cap.impl,
            "dependency": cap.dependency,
            "input_formats": list(cap.input_formats),
            "output_kind": cap.output_kind,
            "sample_rate_rule": cap.sample_rate_rule,
            "evidence": cap.evidence,
            "min_sample_condition": cap.min_sample_condition,
            "external_tools": list(cap.external_tools),
            "failure_codes": list(cap.failure_codes),
            "min_duration_seconds": cap.min_duration_seconds,
            "handler_registered": cap.mode in DIGITAL_MODE_HANDLERS,
        })
    return rows


def supported_modes() -> List[str]:
    return sorted(DIGITAL_MODE_CAPABILITIES)


def _find_spec(module_name: str):
    """依赖探测的唯一入口（测试可注入替身，验证缺依赖分支）。"""
    return importlib.util.find_spec(module_name)


def _import_module(module_name: str):
    """真实导入入口：find_spec 只说明「文件在」，导入失败才是真缺依赖。

    可达路径：环境缺 numpy/scipy 等第三方库时，模块文件存在但 import 抛
    ImportError —— 这条真实分支把 `missing_dependency` 从「装饰枚举」变成
    可触发的失败码（不再只能靠 monkeypatch 复现）。
    """
    return importlib.import_module(module_name)


def _missing_dependency(cap: DigitalModeCapability) -> Optional[str]:
    """登记的内置实现模块是否可导入；缺依赖返回模块名（诚实原因）。"""
    if not cap.dependency:
        return None
    try:
        if _find_spec(cap.dependency) is None:
            return cap.dependency
    except (ImportError, ValueError):
        return cap.dependency
    try:
        _import_module(cap.dependency)
    except ImportError:
        return cap.dependency
    return None


def _error_result(cap: Optional[DigitalModeCapability], mode: str,
                  code: str, message: str, **extra: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "mode": mode,
        "ok": False,
        "status": "error",
        "error_code": code,
        "error": message,
        "found": False,
        "messages": [],
        "num_messages": 0,
    }
    if cap is not None:
        result["evidence_level"] = cap.evidence
        result["min_sample_condition"] = cap.min_sample_condition
        result["output_kind"] = cap.output_kind
        result["impl"] = cap.impl
    result.update(extra)
    return result


# ---- 运行期前置校验：采样率整数倍 + 最短一帧 -------------------------------
_IQ_RATE_RULES = {
    "adsb": (2e6, 1e6, "fs ≥ 2MHz 且为 1MHz 整数倍"),
    "ais": (19200.0, 9600.0, "fs ≥ 19200 且为 9600 整数倍"),
    "meteor": (144000.0, 72000.0, "fs ≥ 144000 且为 72k 符号率整数倍"),
}

_DEFAULT_SAMPLE_RATE = {
    "adsb": 2e6,
    "ais": 192000.0,
    "meteor": 288000.0,
    "aprs": 96000.0,
    "ft8": 12000.0,
    "ft4": 12000.0,
    "wspr": 12000.0,
    "navtex": 12000.0,
}


def _default_sample_rate(key: str) -> float:
    """每个模式的合法默认采样率（不再用会违反自身约束的裸 1e6）。"""
    return _DEFAULT_SAMPLE_RATE.get(key, 48000.0)


def _sample_rate_violation(key: str, ext: str, fs: float):
    """IQ 模式的采样率硬约束；返回 (规则, 实际, sps) 或 None。"""
    if ext not in (".cf32", ".cfile"):
        return None
    rule = _IQ_RATE_RULES.get(key)
    if rule is None:
        return None
    min_fs, step, text = rule
    sps = int(round(fs / step))
    if fs < min_fs or abs(fs - sps * step) > 1e-6:
        return text, f"{fs:g}", sps
    return None


def _min_duration_seconds(key: str, cap: DigitalModeCapability,
                          baud: int) -> Tuple[float, str]:
    """该模式「最短一帧」的时长；0 表示 handler 内部已按自身协议常量校验。"""
    if key == "pocsag":
        if baud not in (512, 1200, 2400):
            return 0.0, cap.min_duration_rule   # 非法 baud 由 handler 先拒绝
        # 前导 576 bit + 同步字 32 bit + 一个 16 码字批（16×32 bit）
        return (576 + 32 + 16 * 32) / float(baud), cap.min_duration_rule
    return cap.min_duration_seconds, cap.min_duration_rule


def _input_sample_count(path: str, ext: str, fs: Optional[float]):
    """返回 (样本数, 采样率)；WAV 读头，CF32 按成对 float32 计数。

    WAV 头读不出来时返回 None，交给 handler 报 `invalid_wav`（不在这里
    抢先把容器错误说成样本不足）。
    """
    if ext == ".wav":
        try:
            import wave
            with wave.open(path, "rb") as wf:
                return int(wf.getnframes()), float(wf.getframerate())
        except Exception:
            return None
    if ext in (".cf32", ".cfile") and fs:
        return os.path.getsize(path) // 8, float(fs)
    return None


# =========================================================================
# 路由（原先的 if/elif 链收敛到这里：查表 → 前置校验 → handler）
# =========================================================================
def run_digital_mode(
    input_path: str,
    mode: str,
    output_dir: str = None,
    sample_rate: Optional[float] = None,
    baud: int = 512,
    center_frequency_hz: float = 1500.0,
    frequency_search_hz: Optional[Tuple[float, float]] = None,
    frequency_offsets_hz: Optional[Sequence[float]] = None,
    time_offsets_seconds: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """对外唯一入口：跑一遍路由，写审计，返回结果（审计不改结果）。"""
    result = _run_digital_mode(
        input_path, mode, output_dir=output_dir, sample_rate=sample_rate,
        baud=baud, center_frequency_hz=center_frequency_hz,
        frequency_search_hz=frequency_search_hz,
        frequency_offsets_hz=frequency_offsets_hz,
        time_offsets_seconds=time_offsets_seconds,
    )
    from .decode_audit import maybe_audit
    maybe_audit("decode_digital_mode", result, input_path)
    return result


def _run_digital_mode(
    input_path: str,
    mode: str,
    output_dir: str = None,
    sample_rate: Optional[float] = None,
    baud: int = 512,
    center_frequency_hz: float = 1500.0,
    frequency_search_hz: Optional[Tuple[float, float]] = None,
    frequency_offsets_hz: Optional[Sequence[float]] = None,
    time_offsets_seconds: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    import numpy as np

    key = (mode or "").lower()
    cap = DIGITAL_MODE_CAPABILITIES.get(key)
    if cap is None:
        return _error_result(
            None, key, ERR_UNKNOWN_MODE,
            user_message("unknown_mode", mode=mode,
                         available=", ".join(supported_modes())),
            supported_modes=supported_modes(),
        )
    if sample_rate is None:
        sample_rate = _default_sample_rate(key)
    missing = _missing_dependency(cap)
    if missing:
        return _error_result(
            cap, key, ERR_MISSING_DEPENDENCY,
            user_message("missing_dependency", module=missing, impl=cap.impl),
            missing_dependency=missing,
        )
    if not os.path.exists(input_path):
        return _error_result(
            cap, key, ERR_MISSING_SAMPLE,
            user_message("missing_sample", path=input_path),
            input_file=input_path,
        )
    ext = os.path.splitext(input_path)[1].lower()
    if "*" not in cap.input_formats and not cap.delegate_format_check:
        if ext not in cap.input_formats:
            return _error_result(
                cap, key, ERR_UNSUPPORTED_FORMAT,
                user_message("unsupported_format", mode=key,
                             allowed="/".join(cap.input_formats),
                             actual=ext or "无扩展名"),
                input_file=input_path, format=ext,
            )

    # 采样率硬约束（IQ 模式）：非整数倍/低于下限 → 显式失败，不静默取整。
    violation = _sample_rate_violation(key, ext, float(sample_rate))
    if violation:
        text, actual, sps = violation
        return _error_result(
            cap, key, ERR_SAMPLE_RATE_MISMATCH,
            user_message("sample_rate_mismatch", mode=key, rule=text,
                         actual=actual, sps=sps),
            sample_rate_hz=float(sample_rate), sps=sps,
        )

    # 运行期最短样本校验：不足「一帧」→ insufficient_samples，绝不 ok=true。
    need_sec, basis = _min_duration_seconds(key, cap, int(baud))
    if need_sec > 0:
        info = _input_sample_count(input_path, ext, float(sample_rate))
        if info is not None:
            actual_n, rate = info
            need_n = int(math.ceil(need_sec * rate))
            if actual_n < need_n:
                return _error_result(
                    cap, key, ERR_INSUFFICIENT_SAMPLES,
                    user_message("insufficient_samples", mode=key, rule=basis,
                                 required=need_n, actual=actual_n),
                    required_samples=need_n, actual_samples=actual_n,
                    min_duration_seconds=need_sec,
                )

    if output_dir is None:
        output_dir = os.path.expanduser(f"~/.mbdsdr/{key}")
    os.makedirs(output_dir, exist_ok=True)

    available_tools = [t for t in cap.external_tools if shutil.which(t)]
    result: Dict[str, Any] = {
        "mode": key,
        "input_file": input_path,
        "file_size": os.path.getsize(input_path),
        "format": ext,
        "sample_rate_hz": float(sample_rate),
        "external_tools_available": available_tools,
        "note": "",
        "evidence_level": cap.evidence,
        "min_sample_condition": cap.min_sample_condition,
        "output_kind": cap.output_kind,
        "impl": cap.impl,
    }

    ctx = _Ctx(
        input_path=input_path, ext=ext, output_dir=output_dir,
        sample_rate=sample_rate, baud=baud,
        center_frequency_hz=center_frequency_hz,
        frequency_search_hz=frequency_search_hz,
        frequency_offsets_hz=frequency_offsets_hz,
        time_offsets_seconds=time_offsets_seconds,
        available_tools=available_tools, result=result,
    )
    result = DIGITAL_MODE_HANDLERS[cap.mode](ctx)

    if not result.get("note"):
        if available_tools:
            result["note"] = f"检测到外部工具 {available_tools[0]}，可用于完整解码"
        else:
            result["note"] = (
                f"未检测到 {key} 解码工具，建议安装: "
                f"{list(cap.external_tools) or ['N/A']}"
            )

    # 统一的诚实状态收敛：一切失败都带 error_code，不吞成「看起来成功」
    if "error" in result and "ok" not in result:
        result["ok"] = False
        result["status"] = "error"
        result.setdefault("error_code", ERR_DECODE_ERROR)
    elif "analysis_error" in result and "ok" not in result:
        # 例如内置解码器抛异常被 handler 兜住：不粉饰成成功
        result["ok"] = False
        result["status"] = "error"
        result.setdefault("error_code", ERR_DECODE_ERROR)
    elif result.get("status") == "rejected":
        result.setdefault("error_code",
                          result.get("reject_reason") or ERR_DECODE_ERROR)
    elif "ok" not in result:
        result["ok"] = True
        result["status"] = "decoded"
    result.setdefault("error_code", "")

    # 简化频谱特征（.cf32 才做；窄带入口显式拒绝时不补频谱，维持原契约）
    try:
        if ext == ".cf32" and result.get("status") != "rejected":
            raw = np.fromfile(input_path, dtype=np.float32, count=100000)
            if len(raw) >= 2:
                iq = raw[0::2] + 1j * raw[1::2]
                spectrum = np.fft.fft(iq[:1024])
                powers = np.abs(spectrum) ** 2
                result["spectral_peak"] = float(np.max(powers))
                result["spectral_mean"] = float(np.mean(powers))
                result["samples_analyzed"] = len(iq)
    except Exception as e:
        result["analysis_error"] = str(e)

    return result

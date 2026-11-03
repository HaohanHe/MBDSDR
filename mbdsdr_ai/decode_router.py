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
`not_implemented`（能力未接线，例如 ft8/aprs 通用入口）。

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

import importlib.util
import os
import shutil
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---- 诚实错误码 ---------------------------------------------------------
ERR_UNKNOWN_MODE = "unknown_mode"
ERR_MISSING_DEPENDENCY = "missing_dependency"
ERR_MISSING_SAMPLE = "missing_sample"
ERR_UNSUPPORTED_FORMAT = "unsupported_format"
ERR_DECODE_ERROR = "decode_error"
ERR_NOT_IMPLEMENTED = "not_implemented"

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
            "（CRC-24 通过与 dump1090 对拍）"
        ),
        external_tools=("dump1090", "dump1090-fa"),
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
    ),
    "aprs": DigitalModeCapability(
        mode="aprs",
        impl="ax25.py",
        dependency="",       # AFSK 路由未接线
        input_formats=("*",),
        output_kind="note",
        sample_rate_rule="NFM 解调后 48kHz 音频（通用入口未接线，参数不生效）",
        evidence=EVIDENCE_NO_SAMPLE,
        min_sample_condition=(
            "144.640/144.390/144.800MHz NFM 下行解调音频 ≥ 5s，含至少 1 个"
            "1200bd Bell202 AX.25 帧（CRC-16 通过）"
        ),
        external_tools=("direwolf", "aprs"),
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
    ),
    "meteor": DigitalModeCapability(
        mode="meteor",
        impl="meteor_sat.py",
        dependency="mbdsdr_ai.meteor_sat",
        input_formats=(".cf32", ".cfile"),
        output_kind="image",
        sample_rate_rule="fs ≥ 2×符号率 72k（LRPT QPSK），推荐 288000/576000",
        evidence=EVIDENCE_PARTIAL,
        min_sample_condition=(
            "Meteor-M2 137.9MHz 下行 .cf32 ≥ 20s，Viterbi 可锁且 RS 全纠"
            "帧 ≥ 2（本仓 LRPT 真录链可作真值）"
        ),
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
    """内置纯 numpy Mode-S DF17（CRC-24 MSB-first、8us 前导模板）。"""
    from .adsb import decode_baseband

    result = ctx.result
    try:
        import numpy as np
        raw = np.fromfile(ctx.input_path, dtype=np.float32)
        iq = raw[0::2] + 1j * raw[1::2]
        ratio = ctx.sample_rate / 1e6
        if abs(ratio - round(ratio)) > 1e-6:
            result["note"] = (
                f"内置 Mode-S 解码器要求 fs 为 1MHz 整数倍，当前 {ratio:g} MHz；"
                "请重采样后再解码，或使用 dump1090。"
            )
        else:
            d = decode_baseband(iq, fs=float(ctx.sample_rate))
            result["found"] = bool(d.get("found", False))
            result["crc_ok"] = bool(d.get("crc_ok", False))
            result["icao_hex"] = d.get("icao")
            result["callsign"] = d.get("callsign")
            result["raw_hex"] = d.get("raw_bytes")
            result["preamble_index"] = d.get("preamble_index")
            result["note"] = "内置纯 numpy Mode-S DF17 解码器（已与 pyModeS 交叉验证）"
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
    result = ctx.result
    result["note"] = (
        "APRS 为 1200 波特 AFSK(Bell202) over AX.25；内置 ax25.py 已具备 "
        "FM 鉴频+数字 PLL+CRC-16 能力（见 exp_ax25_performance），direwolf 为可选增强。"
    )
    result.update({"ok": False, "status": ERR_NOT_IMPLEMENTED,
                   "error_code": ERR_NOT_IMPLEMENTED})
    return result


def _route_pocsag(ctx: _Ctx) -> Dict[str, Any]:
    from .decoders import _demod_iq_to_audio, _read_wav_audio
    from .pocsag_decoder import pocsag_decode

    result = ctx.result
    baud = int(ctx.baud) if ctx.baud in (512, 1200, 2400) else 512
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
        result["error"] = (
            f"AIS 需 .cf32/.cfile 复基带（9600bd GMSK，fs 为 9600 整数倍），"
            f"收到 {ctx.ext}")
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
        result["error"] = (
            f"NAVTEX 需 .wav 音频（SSB 解调后 100bd FSK，中心 1kHz），"
            f"收到 {ctx.ext}")
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
                "内置 Meteor LRPT 解调骨架（QPSK→DQPSK 差分→卷积去交织→Viterbi）；"
                "帧解析与图像重组见 meteor_sat 工具面。"
            )
        except Exception as e:
            result["error"] = f"Meteor LRPT 解调失败: {e}"
    else:
        result["error"] = f"Meteor LRPT 需 .cf32/.cfile 复基带，收到 {ctx.ext}"
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
            "handler_registered": cap.mode in DIGITAL_MODE_HANDLERS,
        })
    return rows


def supported_modes() -> List[str]:
    return sorted(DIGITAL_MODE_CAPABILITIES)


def _find_spec(module_name: str):
    """依赖探测的唯一入口（测试可注入替身，验证缺依赖分支）。"""
    return importlib.util.find_spec(module_name)


def _missing_dependency(cap: DigitalModeCapability) -> Optional[str]:
    """登记的内置实现模块是否可导入；缺依赖返回模块名（诚实原因）。"""
    if not cap.dependency:
        return None
    try:
        return None if _find_spec(cap.dependency) else cap.dependency
    except (ImportError, ValueError):
        return cap.dependency


def _error_result(cap: Optional[DigitalModeCapability], mode: str,
                  code: str, message: str, **extra: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "mode": mode,
        "ok": False,
        "status": "error",
        "error_code": code,
        "error": message,
        "messages": [],
        "num_messages": 0,
    }
    if cap is not None:
        result["evidence_level"] = cap.evidence
        result["min_sample_condition"] = cap.min_sample_condition
        result["impl"] = cap.impl
    result.update(extra)
    return result


# =========================================================================
# 路由（原先的 if/elif 链收敛到这里：查表 → 前置校验 → handler）
# =========================================================================
def run_digital_mode(
    input_path: str,
    mode: str,
    output_dir: str = None,
    sample_rate: float = 1e6,
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
    sample_rate: float = 1e6,
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
            f"未知数字模式 '{mode}'；可用模式: {', '.join(supported_modes())}",
            supported_modes=supported_modes(),
        )
    missing = _missing_dependency(cap)
    if missing:
        return _error_result(
            cap, key, ERR_MISSING_DEPENDENCY,
            f"{key} 缺少内置依赖模块 {missing}（实现文件 {cap.impl} 不可导入）",
            missing_dependency=missing,
        )
    if not os.path.exists(input_path):
        return _error_result(
            cap, key, ERR_MISSING_SAMPLE,
            f"输入文件不存在（缺样本，不是解码失败）: {input_path}",
            input_file=input_path,
        )
    if "*" not in cap.input_formats and not cap.delegate_format_check:
        ext = os.path.splitext(input_path)[1].lower()
        if ext not in cap.input_formats:
            return _error_result(
                cap, key, ERR_UNSUPPORTED_FORMAT,
                f"{key} 需 {'/'.join(cap.input_formats)}，收到 {ext or '无扩展名'}",
                input_file=input_path, format=ext,
            )

    if output_dir is None:
        output_dir = os.path.expanduser(f"~/.mbdsdr/{key}")
    os.makedirs(output_dir, exist_ok=True)

    ext = os.path.splitext(input_path)[1].lower()
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

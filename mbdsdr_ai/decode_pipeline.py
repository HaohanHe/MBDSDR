"""mbdsdr_ai/decode_pipeline.py — 解码流水线「端到端可证」的逐级观测。

不是「换个入口再解一遍」，而是把一条真实信号链拆成可量的级，每级给
**输入样本数 / 输出样本数 / 丢帧数 / 耗时**，失败时给**明确的失败级+原因**，
成功时给可与已知真值逐字节对拍的内容。

三条链路：

  AIS（ITU-R M.1371）：
    .cf32 → load_iq → downconvert（混频+多相抽取）→ demod（GMSK 1-bit 差分
    + 位定时扫描 + NRZI）→ sync（HDLC 标志/FCS，丢帧在此计数）→ frame
    （payload 位流→字节）→ content（Type 1/5/18/24 字段）

  ADS-B 1090ES（ICAO Annex 10 Vol IV / RTCA DO-260）：
    .cf32 → load_iq → downconvert → sync（8µs 四脉冲前导模板匹配）
    → demod（PPM 位判决）→ frame（CRC-24 0xFFF409，丢帧在此计数）
    → content（DF/ICAO/呼号/TC 字段）

  APRS / AX.25（TAPR AX.25 + Bell 202 AFSK 1200bd）：
    .cf32 NFM 复基带 → load_iq → downconvert（FM 鉴频 + 4.5kHz 低通 + 重采样
    到 48kHz）→ demod（Bell 202 正交相关 + PLL 位定时 + NRZI 差分）→ sync
    （HDLC 0x7E 标志 + 去位填充，候选帧在此切出）→ frame（CRC-16/X.25 FCS，
    CRC 失败在此计数）→ content（AX.25 地址/控制/PID/信息字段 = 源呼号、
    目的、digipeater 路径、payload）

诚实边界（每一句都必须能复跑，不靠形容词）：
  * **证据范围**：`tests/fixtures/decode_pipeline/` 的 fixture 是「已知帧经
    仓内编码器重新调制」的 **synthetic** 波形，**NOT OTA**——它证明流水线
    各级与已知真值逐字节一致，**不**证明空口接收；摘要里的
    `evidence_scope` 字段会把这句话带出去，禁止把摘要截图当空口结论。
  * 本模块只对 **.cf32 复基带文件**负责；没有采样时钟恢复、没有多径估计。
  * 失败一律 `ok=False` + `content={}`；**绝不允许 ok=True 且 content 为空**。
  * **两种「丢」必须分开说**：
    - `dropped_frames` / `drop_accounting`：只计 FCS/CRC 校验失败的帧；
    - `behind_report` 的 `backlog_samples` / `source_overflow`：跟不跟得上
      导致的样本积压/源端溢出。本模块同步离线处理、无内部队列，按输入样本
      计的积压恒为 0，也观测不到 SDR 缓冲层的溢出——那发生在源端，
      **本模块不丢样本，也不伪造该计数**。
  * 性能口径：`performance_summary` 用「输入复样本吞吐 / 2.4 MS/s」算实时率。
    实时率 <1 不等于报错，也不等于丢帧；它只表示消费侧变慢。
"""

from __future__ import annotations

import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ---- 诚实的失败原因（每一种都能被测试区分） -----------------------------
REASON_INPUT_NOT_FOUND = "input_not_found"
REASON_IQ_EMPTY = "iq_empty"
REASON_IQ_ODD_LENGTH = "iq_odd_length"
REASON_SAMPLE_RATE_MISMATCH = "sample_rate_mismatch"
REASON_INSUFFICIENT_SAMPLES = "insufficient_samples"
REASON_MISSING_DEPENDENCY = "missing_dependency"
REASON_PREAMBLE_NOT_FOUND = "preamble_not_found"
REASON_NOTHING_DETECTED = "nothing_detected"
REASON_NO_FRAME_RECOVERED = "no_frame_recovered"
REASON_TRUNCATED_FRAME = "truncated_frame"
REASON_CRC_FAILED = "crc_failed"
REASON_UNSUPPORTED_FORMAT = "unsupported_format"
REASON_DECODE_ERROR = "decode_error"

# AIS 最短真帧：168 bit 报文 + 16 bit FCS + 16 bit 首尾标志（不含填充）。
AIS_MIN_FRAME_BITS = 200

# APRS/AX.25 最短 HDLC 帧：15 字节（7 目的 + 7 源 + 1 控制 + 2 FCS）
# + 首尾两个 0x7E 标志（各 8 bit）。
APRS_MIN_FRAME_BYTES = 15
APRS_MIN_HDLC_BITS = APRS_MIN_FRAME_BYTES * 8 + 16

# 流水线级名（三种模式共用这组语义；ADS-B 把前导同步放在 demod 之前）。
AIS_STAGE_NAMES = ("load_iq", "downconvert", "demod", "sync", "frame", "content")
ADSB_STAGE_NAMES = ("load_iq", "downconvert", "sync", "demod", "frame", "content")
APRS_STAGE_NAMES = ("load_iq", "downconvert", "demod", "sync", "frame", "content")


# ---------------------------------------------------------------------------
# 逐级记录
# ---------------------------------------------------------------------------
class _Trace:
    """按顺序累积 stage 记录；每级计时由调用方传入耗时。"""

    def __init__(self) -> None:
        self.stages: List[Dict[str, Any]] = []

    def add(self, name: str, unit_in: str, n_in: int, unit_out: str,
            n_out: int, dropped: int = 0, elapsed_ms: float = 0.0,
            note: str = "") -> Dict[str, Any]:
        rec = {
            "name": name,
            "input_samples": int(n_in),
            "output_samples": int(n_out),
            "dropped_frames": int(dropped),
            "elapsed_ms": round(float(elapsed_ms), 3),
            "unit_in": unit_in,
            "unit_out": unit_out,
            "note": note,
        }
        if self.stages:
            rec["cumulative_ms"] = round(
                self.stages[-1]["cumulative_ms"] + rec["elapsed_ms"], 3)
        else:
            rec["cumulative_ms"] = rec["elapsed_ms"]
        self.stages.append(rec)
        return rec


def _fail(trace: _Trace, stage: str, reason: str, detail: str,
          extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "status": "error",
        "error_code": reason,
        "reason": reason,
        "stage": stage,
        "detail": detail,
        "stages": trace.stages,
        "content": {},
        "messages": [],
        "num_messages": 0,
    }
    if extra:
        out.update(extra)
    return out


def _ok(trace: _Trace, content: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": True,
        "status": "decoded",
        "error_code": "",
        "reason": "",
        "stage": "",
        "detail": "",
        "stages": trace.stages,
        "content": content,
        "messages": content.get("messages", []),
        "num_messages": len(content.get("messages", [])),
    }
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# 第 1 级：读 .cf32 → 复样本
# ---------------------------------------------------------------------------
def _load_cf32(trace: _Trace, path: str) -> Tuple[Optional[np.ndarray], Optional[Dict]]:
    t0 = time.perf_counter()
    if not os.path.exists(path):
        trace.add("load_iq", "file", 0, "complex_samples", 0,
                  elapsed_ms=(time.perf_counter() - t0) * 1000,
                  note="输入文件不存在")
        return None, _fail(trace, "load_iq", REASON_INPUT_NOT_FOUND,
                           f"输入文件不存在: {path}",
                           {"input_file": path})
    raw = np.fromfile(path, dtype=np.float32)
    ms = (time.perf_counter() - t0) * 1000
    if raw.size == 0:
        trace.add("load_iq", "float32_values", 0, "complex_samples", 0,
                  elapsed_ms=ms, note="CF32 为空")
        return None, _fail(trace, "load_iq", REASON_IQ_EMPTY,
                           "CF32 文件没有任何 float32 样本（0 字节或空文件）")
    if raw.size % 2 != 0:
        trace.add("load_iq", "float32_values", int(raw.size),
                  "complex_samples", int(raw.size // 2),
                  elapsed_ms=ms, note="I/Q 数组长度为奇数，尾部半样本丢弃")
        return None, _fail(trace, "load_iq", REASON_IQ_ODD_LENGTH,
                           f"CF32 只能按 I/Q 成对存放，当前 {raw.size} 个 float32 为奇数",
                           {"float32_values": int(raw.size)})
    iq = raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64)
    trace.add("load_iq", "float32_values", int(raw.size),
              "complex_samples", int(iq.size), elapsed_ms=ms,
              note="cf32 interleaved I/Q")
    return iq, None


# ---------------------------------------------------------------------------
# 第 2 级：下变频（混频 + 多相抽取）
# ---------------------------------------------------------------------------
def _resample_complex(iq: np.ndarray, fs: float, target_fs: float) -> np.ndarray:
    from math import gcd
    from scipy.signal import resample_poly

    src, dst = int(round(fs)), int(round(target_fs))
    g = gcd(src, dst)
    return resample_poly(iq, dst // g, src // g, window=("kaiser", 5.0))


def _downconvert(trace: _Trace, iq: np.ndarray, fs: float, target_fs: float,
                 center_offset_hz: float, mode: str) -> Tuple[Optional[np.ndarray], Optional[Dict]]:
    t0 = time.perf_counter()
    n_in = int(iq.size)
    if mode == "ais":
        ok_rate = fs >= 19200.0 and abs(fs % 9600.0) < 1e-6
        rule = "AIS 要求 fs ≥ 19200 且为 9600 的整数倍（9600bd GMSK）"
    else:
        ok_rate = fs >= 2e6 and abs(fs % 1e6) < 1e-6
        rule = "ADS-B 要求 fs ≥ 2MHz 且为 1MHz 整数倍（1Mbit/s PPM）"
    if not ok_rate:
        ms = (time.perf_counter() - t0) * 1000
        trace.add("downconvert", "complex_samples", n_in,
                  "complex_samples", 0, elapsed_ms=ms,
                  note=f"采样率不合规: {fs:g} Hz")
        return None, _fail(trace, "downconvert", REASON_SAMPLE_RATE_MISMATCH,
                           f"{rule}；收到 fs={fs:g} Hz",
                           {"sample_rate_hz": float(fs),
                            "target_sample_rate_hz": float(target_fs)})

    out = iq
    applied = False
    if abs(center_offset_hz) > 1e-9:
        n = np.arange(n_in, dtype=np.float64)
        out = out * np.exp(-2.0j * math.pi * center_offset_hz * n / fs)
        applied = True
    if abs(fs - target_fs) > 1e-6:
        try:
            out = _resample_complex(out, fs, target_fs)
        except ImportError:
            trace.add("downconvert", "complex_samples", n_in,
                      "complex_samples", 0, elapsed_ms=0,
                      note="scipy 缺失")
            return None, _fail(trace, "downconvert", REASON_MISSING_DEPENDENCY,
                               "下变频需要 scipy.signal.resample_poly，当前环境缺失")
        applied = True

    ms = (time.perf_counter() - t0) * 1000
    note = (f"applied={applied} (fs {fs:g}→{target_fs:g} Hz, "
            f"offset={center_offset_hz:g} Hz)")
    trace.add("downconvert", "complex_samples", n_in,
              "complex_samples", int(out.size), elapsed_ms=ms, note=note)
    return np.asarray(out, dtype=np.complex128), None


# ---------------------------------------------------------------------------
# AIS 流水线
# ---------------------------------------------------------------------------
def run_ais_pipeline(path: str, fs: float, target_fs: float = 48000.0,
                     center_offset_hz: float = 0.0) -> Dict[str, Any]:
    """AIS：.cf32 → 复基带 → GMSK 解调 → HDLC 帧 → 字段（逐级可观测）。"""
    from .ais_decoder import (ais_demodulate_iq, ais_frames_decode,
                              bits_to_bytes, parse_payload)

    trace = _Trace()
    iq, err = _load_cf32(trace, path)
    if err:
        return err
    iq, err = _downconvert(trace, iq, fs, target_fs, center_offset_hz, "ais")
    if err:
        return err

    # --- 第 3 级：GMSK 差分 + 位定时（符号/位判决） -----------------------
    t0 = time.perf_counter()
    sps = int(round(target_fs / 9600.0))
    required = AIS_MIN_FRAME_BITS * max(sps, 1)
    if iq.size < required:
        ms = (time.perf_counter() - t0) * 1000
        trace.add("demod", "complex_samples", int(iq.size), "bits", 0,
                  elapsed_ms=ms, note="不足一帧")
        return _fail(trace, "demod", REASON_INSUFFICIENT_SAMPLES,
                     f"IQ 长度不足一帧：需要 ≥ {required} 样本"
                     f"（{AIS_MIN_FRAME_BITS} bit × {sps} 样本/符号），"
                     f"实际 {iq.size}", {"required_samples": int(required)})
    try:
        wire = ais_demodulate_iq(iq, target_fs)
    except Exception as exc:                     # 解码异常也要留分类原因
        ms = (time.perf_counter() - t0) * 1000
        trace.add("demod", "complex_samples", int(iq.size), "bits", 0,
                  elapsed_ms=ms, note=f"异常 {type(exc).__name__}")
        return _fail(trace, "demod", REASON_DECODE_ERROR,
                     f"AIS GMSK 解调异常: {type(exc).__name__}: {exc}")
    ms = (time.perf_counter() - t0) * 1000
    trace.add("demod", "complex_samples", int(iq.size), "bits", len(wire),
              elapsed_ms=ms, note="1-bit 差分 + 位定时扫描 + NRZI")

    # --- 第 4 级：HDLC 标志同步 + 去填充 + FCS（丢帧在此计数） -----------
    t0 = time.perf_counter()
    payloads, dropped = ais_frames_decode(wire)
    ms = (time.perf_counter() - t0) * 1000
    trace.add("sync", "bits", len(wire), "frames", len(payloads),
              dropped=dropped, elapsed_ms=ms,
              note="HDLC 0x7E 标志 / bit-stuffing / FCS-16")
    if not payloads:
        reason = (REASON_NO_FRAME_RECOVERED if dropped
                  else REASON_NOTHING_DETECTED)
        return _fail(trace, "sync", reason,
                     f"位流 {len(wire)} bit 中无 FCS 通过的 AIS 帧"
                     f"（坏帧丢弃 {dropped}）。可能是纯噪声/空载或非 AIS 信号。",
                     {"wire_bits": len(wire), "dropped_frames": int(dropped)})

    # --- 第 5 级：payload 位流 → 字节 --------------------------------------
    t0 = time.perf_counter()
    payload_bytes = [bits_to_bytes(p) for p in payloads]
    n_bytes = sum(len(b) for b in payload_bytes)
    ms = (time.perf_counter() - t0) * 1000
    trace.add("frame", "frames", len(payloads), "bytes", n_bytes,
              elapsed_ms=ms, note="payload 位流按 MSB-first 成字节")
    payloads_hex = [b.hex() for b in payload_bytes]

    # --- 第 6 级：字段解析 -------------------------------------------------
    t0 = time.perf_counter()
    try:
        messages = [parse_payload(p) for p in payloads]
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        trace.add("content", "frames", len(payloads), "messages", 0,
                  elapsed_ms=ms, note=f"异常 {type(exc).__name__}")
        return _fail(trace, "content", REASON_DECODE_ERROR,
                     f"AIS 字段解析异常: {type(exc).__name__}: {exc}")
    ms = (time.perf_counter() - t0) * 1000
    trace.add("content", "frames", len(payloads), "messages", len(messages),
              elapsed_ms=ms, note="ITU-R M.1371 字段（未知类型诚实留空）")
    return _ok(trace, {"messages": messages, "payloads_hex": payloads_hex},
               {"num_frames": len(payloads), "dropped_frames": int(dropped)})


# ---------------------------------------------------------------------------
# ADS-B 流水线
# ---------------------------------------------------------------------------
def run_adsb_pipeline(path: str, fs: float, target_fs: float = 4e6,
                      center_offset_hz: float = 0.0) -> Dict[str, Any]:
    """ADS-B：.cf32 → 复基带 → 前导同步 → PPM 位判决 → CRC → 字段。"""
    from .adsb import (DATA_BITS_LONG, PREAMBLE_LEN_US, _find_preamble,
                       bits_to_bytes, decode_frame, mode_s_crc24)

    trace = _Trace()
    iq, err = _load_cf32(trace, path)
    if err:
        return err
    iq, err = _downconvert(trace, iq, fs, target_fs, center_offset_hz, "adsb")
    if err:
        return err

    sps = int(round(target_fs / 1e6))
    frame_samples = (int(PREAMBLE_LEN_US) + DATA_BITS_LONG) * sps
    if iq.size < frame_samples:
        trace.add("sync", "complex_samples", int(iq.size), "preambles", 0,
                  elapsed_ms=0.0, note="不足一帧")
        return _fail(trace, "sync", REASON_INSUFFICIENT_SAMPLES,
                     f"IQ 长度不足一帧：需要 ≥ {frame_samples} 样本"
                     f"（前导 {int(PREAMBLE_LEN_US)}µs + {DATA_BITS_LONG} bit"
                     f" × {sps} 样本/bit），实际 {iq.size}",
                     {"required_samples": int(frame_samples)})

    env = np.abs(iq) ** 2
    peak = float(env.max())
    env = env / max(peak, 1e-12)

    # --- 第 3 级：8µs 前导同步 ---------------------------------------------
    t0 = time.perf_counter()
    starts: List[int] = []
    cursor = 0
    while cursor + frame_samples <= env.size:
        k = _find_preamble(env[cursor:], sps)
        if k is None:
            break
        abs_k = cursor + int(k)
        starts.append(abs_k)
        cursor = abs_k + frame_samples
    ms = (time.perf_counter() - t0) * 1000
    trace.add("sync", "complex_samples", int(env.size), "preambles", len(starts),
              elapsed_ms=ms, note="8µs 四脉冲模板 + 脉冲/保护对比度 ≥1.8")
    if not starts:
        return _fail(trace, "sync", REASON_PREAMBLE_NOT_FOUND,
                     "未找到合法 8µs 前导（纯噪声/全零/非 1090ES 信号都会落到这里）",
                     {"preamble_candidates": 0})

    # --- 第 4 级：PPM 位判决 -----------------------------------------------
    t0 = time.perf_counter()
    chip = max(sps // 2, 1)
    frames_bits: List[List[int]] = []
    for k in starts:
        data_start = k + int(PREAMBLE_LEN_US) * sps
        bits: List[int] = []
        for i in range(DATA_BITS_LONG):
            s = data_start + i * sps
            front = env[s:s + chip].sum()
            back = env[s + chip:s + 2 * chip].sum()
            bits.append(1 if front > back else 0)
        frames_bits.append(bits)
    ms = (time.perf_counter() - t0) * 1000
    trace.add("demod", "complex_samples", int(len(starts) * frame_samples),
              "bits", len(frames_bits) * DATA_BITS_LONG, elapsed_ms=ms,
              note="PPM：前/后半个 bit 能量比较")

    # --- 第 5 级：CRC-24 + 帧字节 ------------------------------------------
    t0 = time.perf_counter()
    valid_raw: List[bytes] = []
    crc_fail = 0
    for bits in frames_bits:
        if mode_s_crc24(bits) == 0:
            valid_raw.append(bits_to_bytes(bits))
        else:
            crc_fail += 1
    ms = (time.perf_counter() - t0) * 1000
    trace.add("frame", "bits", len(frames_bits) * DATA_BITS_LONG,
              "bytes", len(valid_raw) * 14, dropped=crc_fail, elapsed_ms=ms,
              note="CRC-24 多项式 0xFFF409；失败帧丢弃并计数")
    if not valid_raw:
        return _fail(trace, "frame", REASON_NO_FRAME_RECOVERED,
                     f"找到 {len(starts)} 个前导，但没有一帧通过 CRC-24"
                     f"（丢弃 {crc_fail}）",
                     {"preamble_candidates": len(starts),
                      "dropped_frames": int(crc_fail)}
                     )

    # --- 第 6 级：DF/ICAO/呼号字段 -----------------------------------------
    t0 = time.perf_counter()
    content_frames: List[Dict[str, Any]] = []
    for raw in valid_raw:
        fr = decode_frame(raw)
        if fr is None:
            continue
        content_frames.append({
            "icao": fr.icao_hex,
            "callsign": fr.callsign,
            "df": fr.df,
            "tc": fr.tc,
            "hex": raw.hex(),
        })
    ms = (time.perf_counter() - t0) * 1000
    trace.add("content", "frames", len(valid_raw), "frames", len(content_frames),
              elapsed_ms=ms, note="DF/CA/ICAO/TC 字段")
    if not content_frames:
        return _fail(trace, "content", REASON_DECODE_ERROR,
                     "CRC 通过的帧无法解析出任何 DF/ICAO 字段",
                     {"dropped_frames": int(crc_fail)})
    return _ok(trace, {
        "frames": content_frames,
        "frames_hex": [f["hex"] for f in content_frames],
    }, {"num_frames": len(content_frames), "dropped_frames": int(crc_fail)})


# ---------------------------------------------------------------------------
# APRS / AX.25 流水线
# ---------------------------------------------------------------------------
def _aprs_nrzi_decode(levels: np.ndarray) -> List[int]:
    """NRZI 电平 → HDLC 原始位：0 = 电平切换，1 = 电平保持。"""
    raw: List[int] = []
    prev = 1
    for b in levels:
        cur = int(b)
        raw.append(0 if cur != prev else 1)
        prev = cur
    return raw


def _aprs_flag_positions(bits: List[int]) -> List[int]:
    """扫描 HDLC 标志 0x7E（LSB-first：0,1,1,1,1,1,1,0）。"""
    out: List[int] = []
    for i in range(max(0, len(bits) - 7)):
        if (bits[i] == 0 and bits[i + 7] == 0
                and all(bits[i + k] == 1 for k in range(1, 7))):
            out.append(i)
    return out


def _aprs_frame_candidates(raw_bits: List[int]) -> Tuple[List[int], List[List[int]], List[int]]:
    """按 0x7E 标志切候选帧；返回 (标志位置, 候选位段, 末尾残段)。"""
    flags = _aprs_flag_positions(raw_bits)
    candidates: List[List[int]] = []
    for start, end in zip(flags, flags[1:]):
        seg = raw_bits[start + 8:end]
        if len(seg) >= 8:
            candidates.append(seg)
    tail = raw_bits[flags[-1] + 8:] if flags else []
    return flags, candidates, tail


def _aprs_decode_audio(trace: _Trace, audio: np.ndarray, target_fs: float,
                       extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """从 48k 音频开始跑 APRS 的后四级（cf32 与 wav 入口共用）。"""
    from .ax25 import AX25Frame, bits_to_bytes, unstuff_bits

    sps = float(target_fs) / 1200.0
    required_audio = int(math.ceil(APRS_MIN_HDLC_BITS * sps))
    audio = np.asarray(audio, dtype=np.float64)

    # --- 第 3 级：Bell 202 正交相关 + PLL 位定时 + NRZI ---------------------
    t0 = time.perf_counter()
    if audio.size < required_audio:
        ms = (time.perf_counter() - t0) * 1000
        trace.add("demod", "audio_samples", int(audio.size), "bits", 0,
                  elapsed_ms=ms, note="不足一个 HDLC 帧")
        payload = {"required_samples": int(required_audio)}
        if extra:
            payload.update(extra)
        return _fail(trace, "demod", REASON_INSUFFICIENT_SAMPLES,
                     f"音频长度不足一个 HDLC 帧：需要 ≥ {required_audio} 样本"
                     f"（{APRS_MIN_HDLC_BITS} bit × {sps:g} 样本/bit），"
                     f"实际 {audio.size}", payload)
    try:
        from .multimon_decoders import AFSK1200Demod
        levels = AFSK1200Demod(sample_rate=float(target_fs)).demodulate(audio)
        raw_bits = _aprs_nrzi_decode(levels)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        trace.add("demod", "audio_samples", int(audio.size), "bits", 0,
                  elapsed_ms=ms, note=f"异常 {type(exc).__name__}")
        return _fail(trace, "demod", REASON_DECODE_ERROR,
                     f"AFSK 1200bd 解调异常: {type(exc).__name__}: {exc}")
    ms = (time.perf_counter() - t0) * 1000
    trace.add("demod", "audio_samples", int(audio.size), "bits", len(raw_bits),
              elapsed_ms=ms,
              note="Bell 202 正交相关 + PLL 位定时 + NRZI 差分")

    # --- 第 4 级：HDLC 0x7E 标志 + 去位填充（候选帧在此切出） -------------
    t0 = time.perf_counter()
    flags, candidates, tail = _aprs_frame_candidates(raw_bits)
    ms = (time.perf_counter() - t0) * 1000
    trace.add("sync", "bits", len(raw_bits), "frame_candidates",
              len(candidates), elapsed_ms=ms,
              note=f"HDLC 0x7E 标志 {len(flags)} 个；去填充后按标志切帧")
    if not flags:
        return _fail(trace, "sync", REASON_NOTHING_DETECTED,
                     f"位流 {len(raw_bits)} bit 中没有 HDLC 0x7E 标志"
                     "（纯噪声/无 AFSK 信号都会落到这里）",
                     {"wire_bits": len(raw_bits), "flag_count": 0})
    if not candidates and (len(tail) < 8 or 0 not in tail):
        return _fail(trace, "sync", REASON_NOTHING_DETECTED,
                     f"只看到 {len(flags)} 个 HDLC 标志/空闲位，没有帧数据",
                     {"wire_bits": len(raw_bits), "flag_count": len(flags),
                      "tail_bits": len(tail)})

    # --- 第 5 级：去填充 + CRC-16/X.25（失败帧在此计数） -------------------
    t0 = time.perf_counter()
    valid_raw: List[bytes] = []
    valid_frames: List[Any] = []
    crc_fail = 0
    short_fragments = 0
    for seg in candidates:
        unstuffed = unstuff_bits(seg)
        n_bits = (len(unstuffed) // 8) * 8
        raw = bits_to_bytes(unstuffed[:n_bits])
        if len(raw) < APRS_MIN_FRAME_BYTES:
            short_fragments += 1
            continue
        frame = AX25Frame.from_bytes(raw)
        if frame is None:
            short_fragments += 1
        elif frame.fcs_valid:
            valid_frames.append(frame)
            valid_raw.append(raw)
        else:
            crc_fail += 1
    truncated = bool(tail) and len(tail) >= 8 and 0 in tail
    ms = (time.perf_counter() - t0) * 1000
    trace.add("frame", "frame_candidates", len(candidates), "frames",
              len(valid_raw), dropped=crc_fail, elapsed_ms=ms,
              note=(f"CRC-16/X.25 FCS；CRC 失败丢弃 {crc_fail}，"
                    f"非帧碎片 {short_fragments}，尾部残段 {len(tail)} bit"))
    if not valid_raw:
        payload = {"candidate_frames": len(candidates),
                   "dropped_frames": int(crc_fail),
                   "truncated_fragments": int(short_fragments),
                   "tail_bits": int(len(tail))}
        if crc_fail:
            return _fail(trace, "frame", REASON_CRC_FAILED,
                         f"{len(candidates)} 个候选帧全部 CRC-16 失败"
                         f"（丢弃 {crc_fail}）", payload)
        if truncated or short_fragments:
            return _fail(trace, "frame", REASON_TRUNCATED_FRAME,
                         "看到帧起始标志，但没有凑够一个完整 AX.25 帧"
                         f"（碎片 {short_fragments} 个，尾部残段 {len(tail)} bit）",
                         payload)
        return _fail(trace, "frame", REASON_NO_FRAME_RECOVERED,
                     f"{len(candidates)} 个候选帧都未通过 CRC-16 校验", payload)

    # --- 第 6 级：AX.25 地址/控制/PID/信息字段 ----------------------------
    t0 = time.perf_counter()
    frames: List[Dict[str, Any]] = []
    for frame, raw in zip(valid_frames, valid_raw):
        frames.append({
            "source": frame.source,
            "source_ssid": int(frame.source_ssid),
            "destination": frame.destination,
            "dest_ssid": int(frame.dest_ssid),
            "digipeaters": [[c, int(s), bool(r)]
                            for c, s, r in frame.digipeaters],
            "control": int(frame.control),
            "pid": int(frame.pid),
            "info_hex": frame.info.hex(),
            "info_text": frame.info.decode("latin-1", errors="replace"),
            "info_len": len(frame.info),
            "frame_hex": raw.hex(),
            "fcs": int(frame.fcs),
            "fcs_valid": bool(frame.fcs_valid),
        })
    ms = (time.perf_counter() - t0) * 1000
    trace.add("content", "frames", len(valid_raw), "frames", len(frames),
              elapsed_ms=ms,
              note="AX.25 源/目的/digipeater 路径 + 控制/PID + payload")
    return _ok(trace, {
        "frames": frames,
        "payloads_hex": [f["info_hex"] for f in frames],
    }, {"num_frames": len(frames), "dropped_frames": int(crc_fail),
        "truncated_fragments": int(short_fragments)})


def run_aprs_pipeline(path: str, fs: float, target_fs: float = 48000.0,
                      center_offset_hz: float = 0.0) -> Dict[str, Any]:
    """APRS：NFM 复基带 → 鉴频/低通 → AFSK → NRZI → HDLC → CRC-16 → AX.25。

    输入是 1200bd Bell 202 AFSK（mark 1200Hz / space 2200Hz）调制的 NFM
    复基带；所有内容来自随仓合成 fixture（synthetic, NOT OTA）。
    """
    from .ax25 import AX25Frame, bits_to_bytes, unstuff_bits

    trace = _Trace()
    iq, err = _load_cf32(trace, path)
    if err:
        return err

    # --- 第 2 级：FM 鉴频 + 低通 + 重采样到音频 ---------------------------
    t0 = time.perf_counter()
    n_in = int(iq.size)
    if fs < 16000.0:
        ms = (time.perf_counter() - t0) * 1000
        trace.add("downconvert", "complex_samples", n_in, "audio_samples", 0,
                  elapsed_ms=ms, note=f"采样率不合规: {fs:g} Hz")
        return _fail(trace, "downconvert", REASON_SAMPLE_RATE_MISMATCH,
                     "APRS NFM 复基带要求 fs ≥ 16000 Hz（dev≈3kHz + 2.2kHz 音调）；"
                     f"收到 fs={fs:g} Hz",
                     {"sample_rate_hz": float(fs),
                      "target_sample_rate_hz": float(target_fs)})
    sps = target_fs / 1200.0
    required_audio = int(math.ceil(APRS_MIN_HDLC_BITS * sps))
    required_input = int(math.ceil(required_audio * fs / target_fs)) + 1
    if n_in < required_input:
        # 过短输入连滤波器都喂不饱：不抛 scipy padlen 异常，交给 demod 级报
        # insufficient_samples（与 AIS/ADS-B 的短路口径一致）。
        audio = np.zeros(0, dtype=np.float64)
        ms = (time.perf_counter() - t0) * 1000
        trace.add("downconvert", "complex_samples", n_in, "audio_samples", 0,
                  elapsed_ms=ms,
                  note=(f"输入过短（{n_in} < {required_input} 复样本），"
                        "跳过 FM 鉴频"))
    else:
        try:
            from scipy.signal import butter, resample_poly, sosfiltfilt
            z = np.asarray(iq, dtype=np.complex128)
            if abs(center_offset_hz) > 1e-9:
                n = np.arange(z.size, dtype=np.float64)
                z = z * np.exp(-2.0j * math.pi * center_offset_hz * n / fs)
            z = z - z.mean()
            inst = np.angle(z[1:] * np.conj(z[:-1])) * (fs / (2.0 * math.pi))
            sos = butter(4, min(4500.0, fs / 2.0 - 1.0) / (fs / 2.0),
                         btype="low", output="sos")
            audio = sosfiltfilt(sos, inst)
            if abs(fs - target_fs) > 1e-6:
                src, dst = int(round(fs)), int(round(target_fs))
                from math import gcd
                g = gcd(src, dst)
                audio = resample_poly(audio, dst // g, src // g,
                                      window=("kaiser", 5.0))
        except ImportError:
            trace.add("downconvert", "complex_samples", n_in, "audio_samples", 0,
                      elapsed_ms=(time.perf_counter() - t0) * 1000,
                      note="scipy 缺失")
            return _fail(trace, "downconvert", REASON_MISSING_DEPENDENCY,
                         "APRS 鉴频需要 scipy.signal（butter/resample_poly），当前环境缺失")
        except Exception as exc:
            trace.add("downconvert", "complex_samples", n_in, "audio_samples", 0,
                      elapsed_ms=(time.perf_counter() - t0) * 1000,
                      note=f"异常 {type(exc).__name__}")
            return _fail(trace, "downconvert", REASON_DECODE_ERROR,
                         f"APRS FM 鉴频异常: {type(exc).__name__}: {exc}")
        audio = np.asarray(audio, dtype=np.float64)
        ms = (time.perf_counter() - t0) * 1000
        trace.add("downconvert", "complex_samples", n_in, "audio_samples",
                  int(audio.size), elapsed_ms=ms,
                  note=(f"FM 鉴频 + 4.5kHz 低通 + 重采样 {fs:g}→{target_fs:g} Hz "
                        f"(offset={center_offset_hz:g} Hz)"))

    return _aprs_decode_audio(trace, audio, target_fs,
                              extra={"required_input_samples": required_input})


class _WavFormatError(Exception):
    """WAV 容器/编码不受支持；detail 会原样进入失败原因。"""

    def __init__(self, message: str, info: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.info = info or {}


def _probe_wav_header(path: str) -> Dict[str, Any]:
    """解析 RIFF/WAVE 头，给「不支持」失败提供声道/位深/编码的准确原因。

    只读头部与 fmt 块，不把整段 PCM 读进内存（真录音可能很长）。
    """
    import struct

    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise _WavFormatError("不是 RIFF/WAVE 文件")
        fmt = None
        fmt_body = b""
        data_len = 0
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid = hdr[:4]
            size = struct.unpack("<I", hdr[4:8])[0]
            if cid == b"fmt ":
                fmt_body = f.read(size)
                if len(fmt_body) >= 16:
                    fmt = struct.unpack("<HHIIHH", fmt_body[:16])
                if size & 1:
                    f.seek(1, os.SEEK_CUR)
                continue
            if cid == b"data":
                data_len = size
            f.seek(size + (size & 1), os.SEEK_CUR)
    if fmt is None:
        raise _WavFormatError("WAV 缺少 fmt 块，无法判断编码")

    audio_format, channels, sample_rate, byte_rate, block_align, bits = fmt
    if audio_format == 0xFFFE and len(fmt_body) >= 26:
        audio_format = struct.unpack("<H", fmt_body[24:26])[0]
    codec = {1: "PCM", 3: "IEEE float", 6: "A-law", 7: "mu-law"}.get(
        audio_format, f"format={audio_format}")
    meta = {"codec": codec, "audio_format": int(audio_format),
            "channels": int(channels), "bits_per_sample": int(bits),
            "sample_rate_hz": float(sample_rate), "data_bytes": int(data_len)}
    if audio_format != 1:
        raise _WavFormatError(
            f"不支持的编码: {codec}（仅支持 16-bit PCM）", meta)
    if channels != 1:
        raise _WavFormatError(
            f"不支持的声道数: {channels}（仅支持单声道，不做静默下混）", meta)
    if bits != 16:
        raise _WavFormatError(
            f"不支持的位深: {bits}-bit（仅支持 16-bit PCM）", meta)
    if data_len <= 0:
        raise _WavFormatError("WAV 没有 data 块或数据为空", meta)
    return meta


def run_aprs_wav_pipeline(path: str, target_fs: float = 48000.0) -> Dict[str, Any]:
    """APRS：16-bit PCM 单声道 .wav → 重采样 48k → AFSK → AX.25。

    只接受 16-bit PCM 单声道；立体声/8-bit/24-bit/浮点 WAV 一律
    `unsupported_format`，并给出声道数/位深/编码的具体原因，不静默下混。
    格式合法但信号解不出时，原因码来自后面的流水线（如 `nothing_detected`），
    不让格式门背锅。
    """
    import wave

    trace = _Trace()
    t0 = time.perf_counter()
    try:
        meta = _probe_wav_header(path)
    except FileNotFoundError:
        trace.add("load_iq", "pcm_frames", 0, "audio_samples", 0,
                  elapsed_ms=0.0, note="输入文件不存在")
        return _fail(trace, "load_iq", REASON_INPUT_NOT_FOUND,
                     f"输入文件不存在: {path}")
    except _WavFormatError as exc:
        trace.add("load_iq", "pcm_frames", 0, "audio_samples", 0,
                  elapsed_ms=(time.perf_counter() - t0) * 1000,
                  note=str(exc))
        return _fail(trace, "load_iq", REASON_UNSUPPORTED_FORMAT, str(exc),
                     dict(exc.info))
    except Exception as exc:
        trace.add("load_iq", "pcm_frames", 0, "audio_samples", 0,
                  elapsed_ms=(time.perf_counter() - t0) * 1000,
                  note=f"WAV 头解析异常 {type(exc).__name__}")
        return _fail(trace, "load_iq", REASON_UNSUPPORTED_FORMAT,
                     f"WAV 头解析失败: {type(exc).__name__}: {exc}")

    try:
        with wave.open(path, "rb") as w:
            n_frames = w.getnframes()
            pcm = w.readframes(n_frames)
    except Exception as exc:
        trace.add("load_iq", "pcm_frames", 0, "audio_samples", 0,
                  elapsed_ms=(time.perf_counter() - t0) * 1000,
                  note=f"PCM 读取失败 {type(exc).__name__}")
        return _fail(trace, "load_iq", REASON_UNSUPPORTED_FORMAT,
                     f"无法读取 16-bit PCM 数据: {type(exc).__name__}: {exc}",
                     {"codec": meta["codec"], "channels": meta["channels"],
                      "bits_per_sample": meta["bits_per_sample"]})
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    sample_rate = float(meta["sample_rate_hz"])
    trace.add("load_iq", "pcm_frames", int(n_frames), "audio_samples",
              int(audio.size), elapsed_ms=(time.perf_counter() - t0) * 1000,
              note=(f"16-bit PCM mono {sample_rate:g}Hz "
                    f"(channels={meta['channels']}, bits={meta['bits_per_sample']})"))

    # --- 重采样到流水线需要的 48k -----------------------------------------
    t0 = time.perf_counter()
    n_in = int(audio.size)
    if sample_rate < 4800.0:
        trace.add("downconvert", "audio_samples", n_in, "audio_samples", 0,
                  elapsed_ms=(time.perf_counter() - t0) * 1000,
                  note=f"采样率过低: {sample_rate:g} Hz")
        return _fail(trace, "downconvert", REASON_SAMPLE_RATE_MISMATCH,
                     "WAV 采样率过低：Bell 202 space=2200Hz 要求 fs ≥ 4800Hz；"
                     f"收到 {sample_rate:g} Hz",
                     {"sample_rate_hz": sample_rate,
                      "target_sample_rate_hz": float(target_fs)})
    try:
        from scipy.signal import resample_poly
        if abs(sample_rate - target_fs) > 1e-6:
            src, dst = int(round(sample_rate)), int(round(target_fs))
            from math import gcd
            g = gcd(src, dst)
            audio = resample_poly(audio, dst // g, src // g,
                                  window=("kaiser", 5.0))
    except ImportError:
        trace.add("downconvert", "audio_samples", n_in, "audio_samples", 0,
                  elapsed_ms=(time.perf_counter() - t0) * 1000, note="scipy 缺失")
        return _fail(trace, "downconvert", REASON_MISSING_DEPENDENCY,
                     "WAV 重采样需要 scipy.signal.resample_poly，当前环境缺失")
    except Exception as exc:
        trace.add("downconvert", "audio_samples", n_in, "audio_samples", 0,
                  elapsed_ms=(time.perf_counter() - t0) * 1000,
                  note=f"异常 {type(exc).__name__}")
        return _fail(trace, "downconvert", REASON_DECODE_ERROR,
                     f"WAV 重采样异常: {type(exc).__name__}: {exc}")
    audio = np.asarray(audio, dtype=np.float64)
    trace.add("downconvert", "audio_samples", n_in, "audio_samples",
              int(audio.size), elapsed_ms=(time.perf_counter() - t0) * 1000,
              note=f"PCM audio 重采样 {sample_rate:g}→{target_fs:g} Hz")

    return _aprs_decode_audio(trace, audio, target_fs)


def decode_aprs_audio(audio: np.ndarray, target_fs: float = 48000.0) -> Dict[str, Any]:
    """对已在目标采样率上的浮点音频直接跑 APRS 后四级（无容器/无 I/O）。

    供灵敏度扫描等「同一帧反复加噪重解」的场景使用；音频必须已归一化
    （16-bit PCM 口径为 int16/32768）。容器与格式检查不在本入口职责内。
    """
    trace = _Trace()
    arr = np.asarray(audio, dtype=np.float64)
    trace.add("load_iq", "audio_samples", int(arr.size), "audio_samples",
              int(arr.size), note="内存浮点音频（无容器）")
    trace.add("downconvert", "audio_samples", int(arr.size), "audio_samples",
              int(arr.size), note=f"已是目标采样率 {target_fs:g} Hz（无重采样）")
    return _aprs_decode_audio(trace, arr, float(target_fs))


# ---------------------------------------------------------------------------
# 性能口径
# ---------------------------------------------------------------------------
def performance_summary(report: Dict[str, Any],
                        nominal_sample_rate: float = 2.4e6) -> Dict[str, Any]:
    """把逐级耗时折算成单帧耗时与实时率（对 2.4 MS/s 的输入速率）。

    实时率 = 输入复样本吞吐 / nominal_sample_rate。>1 表示单线程能跟上；
    <1 表示跟不上。跟不上时的行为是**同步累积延迟**，不卡死、也不伪造丢帧
    —— `dropped_frames` 只统计 FCS/CRC 失败的帧。
    """
    stages = report.get("stages") or []
    total_ms = sum(float(s.get("elapsed_ms", 0.0)) for s in stages)
    in_samples = int(stages[0].get("output_samples", 0)) if stages else 0

    if report.get("ok"):
        n_frames = int(stages[-1].get("output_samples", 0)) if stages else 0
    else:
        n_frames = 0
    per_frame_ms = (total_ms / n_frames) if n_frames else float(total_ms)
    throughput = (in_samples / (total_ms / 1000.0)) if total_ms > 0 else 0.0
    ratio = throughput / float(nominal_sample_rate) if nominal_sample_rate else 0.0

    return {
        "n_frames": n_frames,
        "input_complex_samples": in_samples,
        "total_elapsed_ms": round(total_ms, 3),
        "per_frame_ms": round(per_frame_ms, 3),
        "throughput_samples_per_s": round(throughput, 1),
        "nominal_sample_rate": float(nominal_sample_rate),
        "realtime_ratio_vs_2_4msps": round(ratio, 4),
        "keeps_up_with_2_4msps": bool(ratio >= 1.0),
        "evidence_scope": (
            "synthetic fixtures, NOT OTA：这些数字来自仓内编码器重调的已知帧，"
            "只能证明本实现与已知真值一致，不能当空口接收/天线性能结论。"),
        "drop_accounting": (
            "dropped_frames 只计 FCS/CRC 校验失败，不含「跟不上导致样本没被处理」"
            "的那种丢；后者发生在 SDR 缓冲层，本模块观测不到，也不伪造计数。"),
        "behavior_when_behind": (
            "同步离线处理，无内部队列：耗时超过到达速率时不会卡死，也不会静默"
            "丢帧（dropped_frames 只计 CRC/FCS 失败）；落后的样本只能由调用方"
            "丢弃或降采样，本入口不伪造丢帧数。"),
    }


def benchmark_pipeline(mode: str, path: str, fs: float,
                       target_fs: Optional[float] = None,
                       center_offset_hz: float = 0.0,
                       repeats: int = 3,
                       nominal_sample_rate: float = 2.4e6) -> Dict[str, Any]:
    """预热后跑 ``repeats`` 次，以**实测中位数**做实时率判定。

    首次调用包含 import/scipy 滤波预热（实测 AIS 首跑 0.75×、稳态 2.7×），
    用首跑回答「能不能跟上 2.4 MS/s」会给出误导性的「否」。故这里**明确**
    丢一次预热、只统计后续运行，并取中位数（不用最快一次的乐观值），
    同时把每跑比值一起留下，方便复核波动。
    """
    runner = run_ais_pipeline if mode == "ais" else run_adsb_pipeline
    if mode not in ("ais", "adsb"):
        raise ValueError(f"未知流水线模式: {mode!r}（可选 ais/adsb）")
    if repeats < 1:
        raise ValueError("repeats 必须 ≥ 1")

    reports: List[Dict[str, Any]] = []
    for _ in range(repeats + 1):                    # +1 = 预热跑
        r = runner(path, fs=fs,
                   target_fs=target_fs if target_fs is not None else (
                       48000.0 if mode == "ais" else 4e6),
                   center_offset_hz=center_offset_hz)
        if not r.get("ok"):
            perf = performance_summary(r, nominal_sample_rate)
            perf.update({"ok": False, "reason": r.get("reason"),
                         "stage": r.get("stage"), "detail": r.get("detail")})
            return perf
        reports.append(r)

    measured = sorted(
        reports[1:], key=lambda r: sum(s["elapsed_ms"] for s in r["stages"]))
    median = measured[len(measured) // 2]
    perf = performance_summary(median, nominal_sample_rate)
    perf.update({
        "ok": True,
        "mode": mode,
        "warmup_runs": 1,
        "measured_runs": len(reports) - 1,
        "ratio_per_run": [
            performance_summary(r, nominal_sample_rate)[
                "realtime_ratio_vs_2_4msps"] for r in reports],
    })
    return perf


def behind_report(report: Dict[str, Any], nominal_sample_rate: float = 2.4e6,
                  speed_factor: float = 1.0) -> Dict[str, Any]:
    """回答「跟不上时会怎样」：瓶颈级、积压样本、源端溢出、两种丢的区分。

    ``speed_factor`` 模拟消费侧限速（1.0=按实测速度跑，<1=更慢）。之所以要
    这个参数：判定「必然跟不上」不能在真机上靠运气等一个恰好 <1 的采样，
    而要把「消费侧变慢」显式建模，结果才可复现。

    本模块是**同步、无内部队列**的离线处理，所以：
      * `backlog_samples` 恒为 0（本模块不积压、也不丢样本）；
      * `source_overflow` 恒为 False（SDR 缓冲层溢出发生在源端，本模块
        观测不到，不能编一个数字出来）；
      * `bottleneck_stage` 指耗时占比最大的级——消费侧变慢时，延迟从这级
        开始累积，排障先看它。
    """
    if speed_factor <= 0:
        raise ValueError("speed_factor 必须 > 0")
    stages = report.get("stages") or []
    total_ms = sum(float(s.get("elapsed_ms", 0.0)) for s in stages)
    in_samples = int(stages[0].get("output_samples", 0)) if stages else 0
    scaled_ms = total_ms / float(speed_factor)
    throughput = (in_samples / (scaled_ms / 1000.0)) if scaled_ms > 0 else 0.0
    ratio = throughput / float(nominal_sample_rate) if nominal_sample_rate else 0.0

    rows = []
    for s in stages:
        ms = float(s.get("elapsed_ms", 0.0))
        rows.append({
            "name": s.get("name"),
            "elapsed_ms": round(ms, 3),
            "scaled_elapsed_ms": round(ms / float(speed_factor), 3),
            "share_pct": round((ms / total_ms * 100.0) if total_ms > 0 else 0.0, 2),
        })
    bottleneck = max(rows, key=lambda x: x["share_pct"]) if rows else None

    return {
        "keeps_up": bool(ratio >= 1.0),
        "realtime_ratio": round(ratio, 4),
        "nominal_sample_rate": float(nominal_sample_rate),
        "speed_factor": float(speed_factor),
        "bottleneck_stage": bottleneck["name"] if bottleneck else "",
        "bottleneck_share_pct": bottleneck["share_pct"] if bottleneck else 0.0,
        "stages": rows,
        "backlog_samples": 0,
        "source_overflow": False,
        "backlog_interpretation": (
            "本模块不丢样本：同步离线处理、无内部队列，按输入样本计的积压为 0；"
            "丢样本发生在 SDR 缓冲层（源端溢出），本模块观测不到。"),
        "drop_accounting": (
            "dropped_frames 只计 FCS/CRC 失败，与「跟不上导致样本没被处理」"
            "是两种不同的丢，一个词不能覆盖两种丢。"),
    }


def sweep_frequency_offset(mode: str, path: str, fs: float,
                           target_fs: Optional[float],
                           offsets_hz: List[float],
                           nominal_sample_rate: float = 2.4e6) -> List[Dict[str, Any]]:
    """逐档显式混频（downconvert 的 center_offset_hz）跑同一份输入。

    用途：给「频偏容差」一个可复跑的数，而不是「未覆盖频偏」四个字。
    注意这里测的是**显式混频后**的容差（包络检波对相位不敏感），
    不代表自动频偏估计——本模块没有自动频偏估计。
    """
    if mode not in ("ais", "adsb"):
        raise ValueError(f"未知流水线模式: {mode!r}（可选 ais/adsb）")
    runner = run_ais_pipeline if mode == "ais" else run_adsb_pipeline
    tfs = target_fs if target_fs is not None else (
        48000.0 if mode == "ais" else 4e6)
    rows = []
    for off in offsets_hz:
        r = runner(path, fs=fs, target_fs=tfs, center_offset_hz=float(off))
        rows.append({
            "offset_hz": float(off),
            "ok": bool(r.get("ok")),
            "frames": int(r.get("num_frames", 0)),
            "reason": r.get("reason", ""),
            "per_frame_ms": round(performance_summary(
                r, nominal_sample_rate)["per_frame_ms"], 3),
        })
    return rows

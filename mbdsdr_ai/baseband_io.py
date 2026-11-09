# SPDX-License-Identifier: MIT
"""
MBDSDR Baseband 录制 / 回放（纯 numpy + 二进制）
================================================
IQ 块存盘为紧凑二进制 + sidecar 元数据，读回。SDR++ 的录制功能。

支持格式（按文件扩展名自动识别）：
- .cf32 / .iq：裸 float32 交织 I/Q（无损，round-trip 精确）
- .wav：立体声 int16 WAV（左=I，右=Q；幅度归一化到 int16 满幅，
        sidecar 里存 wav_scale 以便 load_iq 反归一化）

支持两种输入：
- complex 数组（本地 Python）
- 交错实数列表 [re0, im0, re1, im1, ...]（LLM/JSON 唯一可行格式）

sidecar（<path>.json）字段：
    center_freq_hz, sample_rate, samples, duration_s, format,
    start_time（ISO 8601）, gain_db, device, driver, note
"""
from __future__ import annotations
import numpy as np
import logging
import os
import json
import struct
import tempfile
import wave as _wave
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _to_complex(iq) -> np.ndarray:
    """把输入统一成 complex128 数组，自动识别交错实数格式。"""
    arr = np.array(iq)
    if np.iscomplexobj(arr):
        return arr.astype(np.complex128)
    # 实数列表：偶数长度，当成交错 [re, im, re, im]
    flat = arr.astype(np.float64).ravel()
    if len(flat) % 2 != 0:
        raise ValueError("IQ 实数列表长度必须为偶数（交错 re/im）")
    return flat[0::2] + 1j * flat[1::2]


def _format_from_path(path: str) -> Optional[str]:
    """按扩展名判定录制格式；无法判定时返回 None（由调用方如实报错）。

    ``cu8`` 是录制器的**默认格式**（rtl_sdr / SDR# / GQRX 通用 unsigned8
    交错，2 字节/样本）。旧式把 cu8 一律回落 cf32（8 字节/样本），文件被
    读成 1/4 样本且数值全乱，还不报错。无扩展名仍按历史惯例当 cf32。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        return "wav"
    if ext == ".cs16":
        return "cs16"
    if ext in (".cu8", ".u8", ".bin"):
        return "cu8"
    if ext in (".cf32", ".cfile", ".iq", ""):
        return "cf32"
    return None


#: load_iq 支持的格式（与录制器 sdr_backend 的写出格式对齐）。
SUPPORTED_IQ_FORMATS = ("wav", "cs16", "cf32", "cu8")


#: SigMF ``core:datatype`` → load_iq 内部格式名。
#: 只映射**小端**、且与录制器写出量纲一致的写法；``*_be`` 需要先转字节序，
#: 本模块不做——宁可如实拒绝，也不按错字节序读出一份垃圾 IQ。
_SIGMF_DATATYPE_TO_FMT = {
    "cf32_le": "cf32",
    "ci16_le": "cs16",
    "cs16_le": "cs16",
    "cu8": "cu8",
}


def _sigmf_meta_global(data_path: str) -> Dict[str, Any]:
    """从同名 ``.sigmf-meta`` 的 global 段取回放需要的信息。

    录制器（``recorder`` / ``sdr_backend``）写 ``<name>.sigmf-data`` +
    ``<name>.sigmf-meta``，**不写** ``.json`` sidecar。旧 ``load_iq`` 只认
    ``.json``，于是自己录出来的文件自己放不了——2026-11-09 真机七环 R4
    实测报 ``不支持的 IQ 格式 None``。这里按 SigMF 规范读
    ``global.core:datatype`` / ``core:sample_rate``。

    meta 缺失/损坏/未知 datatype 一律返回空 dict，由调用方如实报错，
    绝不猜一个格式顶上。
    """
    if not data_path.endswith(".sigmf-data"):
        return {}
    meta_path = data_path[: -len(".sigmf-data")] + ".sigmf-meta"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("SigMF meta 读取失败 %s: %s: %s",
                       meta_path, type(e).__name__, e)
        return {}
    if not isinstance(meta, dict):
        logger.warning("SigMF meta 顶层不是对象（%s），按未知处理", meta_path)
        return {}
    g = meta.get("global") or {}
    if not isinstance(g, dict):
        return {}
    datatype = str(g.get("core:datatype") or "").strip().lower()
    out: Dict[str, Any] = {
        "datatype": datatype,
        "format": _SIGMF_DATATYPE_TO_FMT.get(datatype),
    }
    try:
        rate = float(g.get("core:sample_rate") or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate > 0:
        out["sample_rate"] = rate
    return out


def write_sidecar(path: str, metadata: Dict) -> str:
    """把录制元数据写到 <path>.json（与 IQ 文件同目录同名，仅扩展名不同）。

    Parameters
    ----------
    path : str
        IQ 数据文件路径。
    metadata : dict
        要落盘的元数据字段（中心频率、采样率、开始时间、时长、样本数、
        格式、增益、设备名、驱动等）。

    Returns
    -------
    str
        sidecar JSON 文件路径。
    """
    spath = path + ".json"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(spath, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return spath


def _write_cf32(path: str, arr: np.ndarray) -> int:
    """写裸 float32 交织 I/Q。返回文件字节数。"""
    interleaved = np.empty(len(arr) * 2, dtype=np.float32)
    interleaved[0::2] = arr.real.astype(np.float32)
    interleaved[1::2] = arr.imag.astype(np.float32)
    interleaved.tofile(path)
    return int(os.path.getsize(path))


def _write_wav(path: str, arr: np.ndarray, sample_rate: float) -> tuple:
    """写立体声 int16 WAV（左=I，右=Q），归一化到 int16 满幅。

    Returns (size_bytes, wav_scale)。wav_scale 用于 load_iq 反归一化。
    """
    peak = float(max(np.max(np.abs(arr.real)), np.max(np.abs(arr.imag)), 1e-12))
    scale = peak / 32767.0
    pcm_i = np.clip(arr.real / scale, -32767.0, 32767.0).astype(np.int16)
    pcm_q = np.clip(arr.imag / scale, -32767.0, 32767.0).astype(np.int16)
    stereo = np.empty(len(arr) * 2, dtype=np.int16)
    stereo[0::2] = pcm_i
    stereo[1::2] = pcm_q
    with _wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(stereo.tobytes())
    return int(os.path.getsize(path)), scale


def save_iq(iq, path: str, sample_rate: float, center_freq_hz: float = 0.0,
            note: str = "",
            start_time: Optional[str] = None,
            gain_db: float = 0.0,
            device: str = "",
            driver: str = "") -> Dict:
    """把复数 IQ 存盘，并写 .json sidecar 元数据。

    Parameters
    ----------
    iq : complex 数组或交错实数列表
        真实 SDR IQ 样本。
    path : str
        输出文件路径。扩展名决定格式：.wav -> 立体声 int16 WAV；
        .cf32/.iq/其他 -> 裸 float32 交织 I/Q。
    sample_rate : float
        采样率 Hz。
    center_freq_hz : float
        中心频率 Hz。
    note : str
        备注。
    start_time : str, optional
        录制开始时间（ISO 8601）。缺省用当前 UTC 时间。
    gain_db : float
        录制时的射频增益 dB。
    device : str
        设备名称。
    driver : str
        驱动名称（如 SoapySDR/RTL-SDR/HackRF）。

    Returns
    -------
    dict
        落盘信息（含 sidecar 路径与字节数）。
    """
    arr = _to_complex(iq)
    fmt = _format_from_path(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    wav_scale: Optional[float] = None
    if fmt == "wav":
        size_bytes, wav_scale = _write_wav(path, arr, sample_rate)
    else:
        size_bytes = _write_cf32(path, arr)
        fmt = "cf32"

    if not start_time:
        start_time = datetime.now(timezone.utc).isoformat()

    meta: Dict = {
        "center_freq_hz": float(center_freq_hz),
        "sample_rate": float(sample_rate),
        "start_time": start_time,
        "duration_s": round(len(arr) / (sample_rate or 1), 3),
        "samples": int(len(arr)),
        "format": fmt,
        "gain_db": float(gain_db),
        "device": str(device),
        "driver": str(driver),
        "note": note,
    }
    if wav_scale is not None:
        meta["wav_scale"] = wav_scale
    sidecar = write_sidecar(path, meta)
    return {**meta, "path": path, "sidecar": sidecar, "size_bytes": size_bytes}


def _normalize_segments(manifest):
    """把 sidecar 的 ``segments`` 清单归一成段表；字段不全就返回 None。

    段表是写入端分段落盘的权威数据（每段一个独立 .iq + 恒定采样率），
    这里只做核对，不猜缺失字段。每个文件只是其中一段，不能用本文件
    样本数去核对会话总样本。
    """
    if not isinstance(manifest, list) or not manifest:
        return None
    segments = []
    expected_start = 0
    try:
        for i, seg in enumerate(manifest):
            samples = int(seg["samples"])
            sr = float(seg["sample_rate"])
            start = int(seg.get("start_sample", expected_start))
            if samples < 0 or sr <= 0 or start != expected_start:
                return None
            segments.append({
                "path": str(seg.get("path") or ""),
                "segment_index": int(seg.get("segment_index", i + 1)),
                "start_sample": start,
                "samples": samples,
                "sample_rate": sr,
                "duration_s": round(samples / sr, 3),
                "start_time": str(seg.get("start_time") or ""),
            })
            expected_start = start + samples
    except (KeyError, TypeError, ValueError):
        return None
    return segments


def _segments_from_changes(changes, total_samples: int):
    """按 ``sample_rate_changes`` 重建段表；只有边界/初始速率明确时才成功。

    旧版写入端把最终采样率写进 sidecar、变更表只留新速率，初始速率和
    样本边界都已丢失——这种文件返回 None，由调用方如实标“时长未知”，
    绝不按末次速率编造一个 duration_s。
    """
    if not isinstance(changes, list) or not changes:
        return None
    try:
        first_rate = float(changes[0]["from_sample_rate"])
        bounds = []
        rates = [first_rate]
        prev = 0
        for ch in changes:
            start = int(ch["start_sample"])
            sr = float(ch["sample_rate"])
            if first_rate <= 0 or sr <= 0 or start <= prev or start >= total_samples:
                return None
            bounds.append(start)
            rates.append(sr)
            prev = start
    except (KeyError, TypeError, ValueError):
        return None
    bounds.append(total_samples)
    segments = []
    for i, sr in enumerate(rates):
        start = 0 if i == 0 else bounds[i - 1]
        end = bounds[i]
        segments.append({
            "start_sample": start,
            "samples": end - start,
            "sample_rate": sr,
            "duration_s": round((end - start) / sr, 3),
        })
    duration = round(sum(s["samples"] / s["sample_rate"] for s in segments), 3)
    return segments, duration


def _iq_file_samples(path: str, fmt: str) -> int:
    """按格式推算文件里的完整样本数（尾部不足一个样本的字节不计）。"""
    if fmt == "wav":
        with _wave.open(path, "rb") as wf:
            return int(wf.getnframes())
    size = os.path.getsize(path)
    bytes_per_sample = {"cs16": 4, "cu8": 2, "cf32": 8}.get(fmt, 8)
    return int(size // bytes_per_sample)


def _read_iq_stream(path: str, fmt: str, wav_scale: float, chunk: int,
                    max_samples: Optional[int],
                    progress: Optional[callable],
                    should_cancel: Optional[callable]):
    """分块读 IQ；返回 (complex64 数组, 文件样本数, 是否被取消)。

    每块只把「本块」的 int16/float32 原样转成 complex64，绝不把整个文件
    先转 float64。``max_samples`` 限制本次分配上限，超出部分不读、如实告知。
    """
    file_samples = _iq_file_samples(path, fmt)
    limit = file_samples
    if max_samples is not None:
        try:
            limit = max(0, min(file_samples, int(max_samples)))
        except (TypeError, ValueError):
            limit = file_samples
    out = np.empty(limit, dtype=np.complex64)
    loaded = 0
    cancelled = False
    wf = _wave.open(path, "rb") if fmt == "wav" else None
    fh = open(path, "rb") if wf is None else None
    try:
        while loaded < limit:
            n = min(chunk, limit - loaded)
            if should_cancel is not None and should_cancel():
                cancelled = True
                break
            if fmt == "wav":
                raw = np.frombuffer(wf.readframes(n), dtype=np.int16)
                if raw.size < 2:
                    break
                n = raw.size // 2
                seg = raw.astype(np.float32)
                seg = (seg[0::2] + 1j * seg[1::2]) * np.float32(wav_scale)
            elif fmt == "cs16":
                # complex signed 16-bit I/Q 交错；之前被当 float32 读=误码。
                # 归一化到 [-1,1]（32768 满幅），与 cf32 分支同量纲。
                raw = np.fromfile(fh, dtype=np.int16, count=2 * n)
                if raw.size < 2:
                    break
                n = raw.size // 2
                seg = (raw[:2 * n].astype(np.float32) / np.float32(32768.0))
                seg = seg[0::2] + 1j * seg[1::2]
            elif fmt == "cu8":
                # unsigned 8-bit 交错，中心 127.5（与录制器
                # sdr_backend.py 的 `z*127.5+127.5` 严格互逆）。
                raw = np.fromfile(fh, dtype=np.uint8, count=2 * n)
                if raw.size < 2:
                    break
                n = raw.size // 2
                seg = ((raw[:2 * n].astype(np.float32) - np.float32(127.5))
                       / np.float32(127.5))
                seg = seg[0::2] + 1j * seg[1::2]
            else:
                raw = np.fromfile(fh, dtype=np.float32, count=2 * n)
                if raw.size < 2:
                    break
                n = raw.size // 2
                seg = raw[0::2] + 1j * raw[1::2]
            out[loaded:loaded + n] = seg
            loaded += n
            if progress is not None:
                progress(loaded, limit)
    finally:
        if wf is not None:
            wf.close()
        if fh is not None:
            fh.close()
    if loaded < limit:
        # 截断/取消都只返回真实读到的部分；不补零、不假装读全
        out = out[:loaded].copy()
    return out, file_samples, cancelled


def load_iq(path: str, sample_rate: float = None, *,
            max_samples: Optional[int] = None,
            chunk_samples: int = 1 << 20,
            progress: Optional[callable] = None,
            should_cancel: Optional[callable] = None) -> Dict:
    """读回 IQ 文件为复数 IQ。优先读 sidecar 拿采样率/格式。

    内存口径（S6）：
      * 分块读取，单块默认 104 万样本；不再整文件 ``astype(np.float64)``
        （旧实现 1.1 GB cf32 的峰值可达文件大小 6 倍以上）；
      * 输出统一为 ``complex64``（8 字节/样本，与 cf32 文件同量纲），
        峰值约为「文件大小 + 2×单块」；
      * ``max_samples`` 给出本次加载上限，超出部分如实标
        ``truncated``/``truncation_reason``，绝不假装读全；
      * ``progress(loaded, total)`` 每块回调一次（供后台线程推进度条）；
      * ``should_cancel()`` 每块调用一次，返回真则提前收尾并标
        ``cancelled``。

    分段语义（S4）：
      * ``segments`` 清单（分段落盘的录音）：返回每段 path/速率/样本/时长，
        并给本段的精确 ``duration_s`` 与会话总时长 ``session_duration_s``；
      * ``sample_rate_changes`` 且带 ``from_sample_rate``/``start_sample``：
        按变更边界重建段表，``duration_s`` 为整段录制的真实总和；
      * 旧版只有 ``time_s``/``sample_rate`` 的混率文件：初始速率与样本边界
        不可重建，``duration_s`` 返回 None 并带 ``duration_unknown_reason``，
        不按末次速率编造数字。
    """
    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}
    meta: Dict = {}
    if os.path.exists(path + ".json"):
        with open(path + ".json", encoding="utf-8") as f:
            meta = json.load(f)
    # SigMF 录制没有 .json sidecar：类型/速率在 .sigmf-meta 的 global 段。
    # 不读它等于「自己录的自己放不了」（2026-11-09 真机 R4 实测）。
    # 显式 sidecar 优先：它才是用户/工具亲手写的期望值。
    sig_global = _sigmf_meta_global(path)
    if sig_global:
        if not meta.get("format"):
            meta["format"] = sig_global.get("format")
        if not meta.get("sample_rate"):
            meta["sample_rate"] = sig_global.get("sample_rate")
    rate_assumed = False
    if sample_rate is None:
        sample_rate = meta.get("sample_rate")
        rate_source = "sigmf-meta" if sig_global else "sidecar"
        if sample_rate is None:
            # 没有有效 sidecar 时仍要能起播（回放定时器需要速率），但必须
            # 把「这是假设值」写进返回值，不许让调用方当成实测元数据。
            sample_rate, rate_source, rate_assumed = 2.4e6, "default", True
    else:
        rate_source = "argument"

    fmt = meta.get("format") or _format_from_path(path)
    if fmt not in SUPPORTED_IQ_FORMATS:
        # 旧式 `fmt = "cf32"` 把 cu8 / 未知格式全部静默按 8 字节/样本解读：
        # 录完回放得到垃圾 IQ 且没有一句报错。宁可明确拒绝。
        return {"error": (
            f"不支持的 IQ 格式 {fmt!r}（{path}）；支持 "
            f"{'/'.join(SUPPORTED_IQ_FORMATS)}。旧实现会静默按 cf32 解析并"
            "把数据整体读错，现在如实拒绝。若是原始 IQ 流，请补 sidecar 的 "
            "format 字段或改用已识别扩展名（.cf32/.cfile/.cu8/.cs16/.wav）；"
            "SigMF 录制请确保同名 .sigmf-meta 的 global.core:datatype 是 "
            "cf32_le / ci16_le / cu8（*_be 必须先转字节序，本模块不做）。"
            + (f" 本次读到 datatype={sig_global.get('datatype')!r}。"
               if sig_global else ""))}
    wav_scale = float(meta.get("wav_scale", 1.0))
    chunk = max(1, int(chunk_samples))
    iq, file_samples, cancelled = _read_iq_stream(
        path, fmt, wav_scale, chunk, max_samples, progress, should_cancel)

    total = int(len(iq))
    result = {
        "path": path,
        # numpy 数组直接返回：.tolist() 会为每个样本造一个 Python complex，
        # 12 万样本即可放大到几十 MB，长录音在回放线程里就是 OOM（S6）。
        "iq": iq,
        "sample_rate": float(sample_rate),
        "sample_rate_source": rate_source,
        "sample_rate_assumed": rate_assumed,
        "metadata_missing": not bool(meta),
        "center_freq_known": "center_freq_hz" in meta,
        "gain_known": "gain_db" in meta,
        "center_freq_hz": meta.get("center_freq_hz", 0),
        "start_time": meta.get("start_time", ""),
        "gain_db": meta.get("gain_db", 0.0),
        "device": meta.get("device", ""),
        "driver": meta.get("driver", ""),
        "samples": total,
        "duration_s": round(total / (sample_rate or 1), 3),
        "file_samples": int(file_samples),
    }
    if file_samples > total:
        result["truncated"] = True
        result["truncation_reason"] = (
            f"文件共 {file_samples} 样本，本次只读前 {total} 样本"
            f"（加载上限 max_samples={max_samples}）")
        result["file_duration_s"] = round(file_samples / (sample_rate or 1), 3)
    else:
        result["truncated"] = False
    if cancelled:
        result["cancelled"] = True
        result["cancel_reason"] = f"读取被取消，只完成 {total}/{file_samples} 样本"

    # 截断/取消时只返回部分样本：段表描述的是整个文件，混在一起会给出
    # 与本次数据不符的时长，直接不返回段表（调用方按 truncated 提示）。
    if result["truncated"] or cancelled:
        return result

    manifest = meta.get("segments")
    changes = meta.get("sample_rate_changes")
    segments = _normalize_segments(manifest)
    if segments:
        result["segments"] = segments
        result["session_samples"] = sum(s["samples"] for s in segments)
        result["session_duration_s"] = round(
            sum(s["samples"] / s["sample_rate"] for s in segments), 3)
        result["mixed_sample_rates"] = len(
            {s["sample_rate"] for s in segments}) > 1
        return result

    if isinstance(changes, list) and changes:
        rebuilt = _segments_from_changes(changes, total)
        if rebuilt:
            segments, duration = rebuilt
            result["segments"] = segments
            result["sample_rate"] = segments[0]["sample_rate"]
            result["duration_s"] = duration
            result["session_samples"] = total
            result["session_duration_s"] = duration
            result["sample_rate_changes"] = changes
            result["mixed_sample_rates"] = len(
                {s["sample_rate"] for s in segments}) > 1
        else:
            result["sample_rate_changes"] = changes
            result["mixed_sample_rates"] = True
            result["duration_s"] = None
            result["duration_unknown_reason"] = (
                "录制中改过采样率，但 sidecar 缺少 from_sample_rate/"
                "start_sample，初始速率与分段边界不可重建")
    return result


# ========================================================================
# RF64 立体声 WAV（>4GB / 64 位尺寸；SatDump 长录制的互操作格式）
# ========================================================================
# 规范口径（公开 RIFF 变体，非任何 GPL 源码）：
#   头部 magic 为 "RF64"（而非 "RIFF"），form type 为 "WAVE"；
#   随后依次是 ds64 块（含 riffSize/dataSize/sampleCount 三个 64 位字段，
#   不足时以 0xFFFFFFFFFFFFFFFF 表示"未知/流式"）与 fmt/data 块；
#   data 块长度为 32 位，故超 4GiB 时以 ds64.dataSize 为准，块内长度为
#   0xFFFFFFFF（W64 风格哨兵）。写入端在 <4GiB 时同样输出合法 RF64
#   （尺寸确定），读取端对 RIFF/RF64 均兼容。
_RF64_DS64_BODY = 24          # 'ds64' + size(4) + riffSize(8)+dataSize(8)+sampleCount(8)
_RF64_HEADER_BYTES = 12 + (8 + _RF64_DS64_BODY) + (8 + 16) + 8


def _write_rf64_wav(path: str, arr, sample_rate: float):
    """写立体声 int16 RF64 WAV（左=I，右=Q，归一化同 _write_wav）。

    Returns (size_bytes, wav_scale)。
    """
    peak = float(max(np.max(np.abs(arr.real)), np.max(np.abs(arr.imag)), 1e-12))
    scale = peak / 32767.0
    pcm_i = np.clip(arr.real / scale, -32767.0, 32767.0).astype(np.int16)
    pcm_q = np.clip(arr.imag / scale, -32767.0, 32767.0).astype(np.int16)
    stereo = np.empty(len(arr) * 2, dtype=np.int16)
    stereo[0::2] = pcm_i
    stereo[1::2] = pcm_q
    payload = stereo.tobytes()
    frames = int(len(arr))
    with open(path, "wb") as f:
        f.write(b"RF64")
        f.write(struct.pack("<I", 0xFFFFFFFF))
        f.write(b"WAVE")
        # ds64：riffSize/dataSize/sampleCount 均已知（小文件亦按确定值写）
        f.write(b"ds64")
        f.write(struct.pack("<I", _RF64_DS64_BODY))
        riff_size = 4 + (8 + _RF64_DS64_BODY) + (8 + 16) + 8 + len(payload)
        f.write(struct.pack("<Q", riff_size))
        f.write(struct.pack("<Q", len(payload)))
        f.write(struct.pack("<Q", frames))
        # fmt 16 字节（PCM）
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<HHIIHH", 1, 2, int(sample_rate),
                            int(sample_rate) * 4, 4, 16))
        # data 32 位长度；载荷大 4GiB 时用哨兵（本实现按实际长度写，
        # 超过 0xFFFFFFFF 才退化为哨兵——真实超过 4GiB 的会话由 ds64 兜底）
        f.write(b"data")
        f.write(struct.pack("<I", len(payload) if len(payload) < 0xFFFFFFFF
                            else 0xFFFFFFFF))
        f.write(payload)
    return int(os.path.getsize(path)), scale


def _read_rf64_wav(path: str):
    """读 RF64 WAV -> (int16 交错数组, sample_rate)。RIFF 头亦兼容。"""
    with open(path, "rb") as f:
        head = f.read(_RF64_HEADER_BYTES)
        if len(head) < _RF64_HEADER_BYTES or head[:4] not in (b"RF64", b"RIFF"):
            raise ValueError("不是 RF64/RIFF WAV 文件")
        magic = head[:4]
        i = 12
        sample_rate = 0
        payload = b""
        while i + 8 <= len(head):
            cid = head[i:i + 4]
            size = struct.unpack_from("<I", head, i + 4)[0]
            body = i + 8
            if cid == b"ds64":
                riff, dsize, _sc = struct.unpack_from("<QQQ", head, body)
                payload_len = dsize
                i = body + _RF64_DS64_BODY
                continue
            if cid == b"fmt ":
                (_fmt, _ch, sr, _br, _align, _bits) = struct.unpack_from(
                    "<HHIIHH", head, body)
                sample_rate = sr
                i = body + size
                continue
            if cid == b"data":
                if size == 0xFFFFFFFF and magic == b"RF64":
                    # 超 4GiB：读文件剩余全部
                    rest = f.read()
                    payload = rest
                else:
                    payload = f.read(size)
                break
            i = body + size
        if not payload:
            payload = f.read()
    arr = np.frombuffer(payload, dtype=np.int16).astype(np.float64)
    return arr, float(sample_rate)


# ========================================================================
# ZIQ 基带格式（SatDump 压缩录制互操作；官方 docs/pages/ZIQ.md 口径）
# ========================================================================
# 头部（公开规格，非任何 GPL 源码）：
#   "ZIQ_"                4 字节签名
#   is_compressed         1 字节（0=裸样本，1=ZSTD 压缩流）
#   bits_per_sample       1 字节（8=int8 交错，16=int16 LE，32=f32 LE）
#   samplerate            8 字节 uint64
#   annotation_length     8 字节 uint64
#   annotation_str         变长 ASCII（JSON 注解，长度以 annotation_length 为准）
# 载荷直接附在头后；ZSTD 为流式，损坏可自同步。
#
# 本实现的诚实边界：
#   - 无 zstandard 包时 is_compressed=1 的写/读均**不可用**——不拿裸数据冒充
#     压缩流，也不把压缩流当裸样本解析；安装依赖后自动启用。
#   - 不实现 ZIQ2 变体（SatDump 另有 VITA-49 风格扩展），按未做项登记。
ZIQ_MAGIC = b"ZIQ_"


def _ziq_dtype(bits: int) -> str:
    if bits == 8:
        return "i1"
    if bits == 16:
        "<i2"
        return "<i2"
    if bits == 32:
        return "<f4"
    raise ValueError(f"ZIQ 仅支持 8/16/32 bit，收到 {bits}")


def write_ziq(path: str, iq, sample_rate: float, annotation: Optional[Dict] = None,
              bits: int = 16, compress: bool = True) -> Dict:
    """写 ZIQ 文件。compress=True 且装有 zstandard 时走 ZSTD，否则诚实报错。

    Returns 落盘信息（含压缩开关与字节数）。
    """
    arr = _to_complex(iq)
    if bits not in (8, 16, 32):
        raise ValueError(f"ZIQ 仅支持 8/16/32 bit，收到 {bits}")
    annotation = dict(annotation or {})
    annotation.setdefault("sample_rate", float(sample_rate))
    annotation.setdefault("samples", int(len(arr)))
    ann = json.dumps(annotation, ensure_ascii=False).encode("ascii")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    payload: bytes
    compressed = False
    if compress:
        try:
            import zstandard as zstd
        except ImportError as e:
            raise OSError(
                "ZIQ 压缩写入需要 zstandard 包（未安装）——不改写裸数据冒充压缩流"
            ) from e
        payload = zstd.ZstdCompressor().compress(_interleave_for_bits(arr, bits))
        compressed = True
    else:
        payload = _interleave_for_bits(arr, bits)

    with open(path, "wb") as f:
        f.write(ZIQ_MAGIC)
        f.write(struct.pack("<B", 1 if compressed else 0))
        f.write(struct.pack("<B", bits))
        f.write(struct.pack("<Q", int(sample_rate)))
        f.write(struct.pack("<Q", len(ann)))
        f.write(ann)
        f.write(payload)
    return {"path": path, "format": "ziq", "compressed": compressed,
            "bits": bits, "samples": int(len(arr)),
            "size_bytes": int(os.path.getsize(path))}


def _interleave_for_bits(arr, bits: int) -> bytes:
    """按位宽把复数 IQ 交织为字节（8/16 有符号，32 为 float）。"""
    if bits == 8:
        # int8 满幅量化（幅度超出饱和，不自动缩放——与 cu8 口径一致）
        qi = np.clip(np.round(arr.real * 127.0), -128, 127).astype("<i1")
        qq = np.clip(np.round(arr.imag * 127.0), -128, 127).astype("<i1")
        out = np.empty(len(arr) * 2, dtype="<i1")
        out[0::2] = qi
        out[1::2] = qq
        return out.tobytes()
    if bits == 16:
        qi = np.clip(np.round(arr.real * 32767.0), -32768, 32767).astype("<i2")
        qq = np.clip(np.round(arr.imag * 32767.0), -32768, 32767).astype("<i2")
        out = np.empty(len(arr) * 2, dtype="<i2")
        out[0::2] = qi
        out[1::2] = qq
        return out.tobytes()
    out = np.empty(len(arr) * 2, dtype="<f4")
    out[0::2] = arr.real.astype("<f4")
    out[1::2] = arr.imag.astype("<f4")
    return out.tobytes()


def read_ziq(path: str) -> Dict:
    """读 ZIQ -> (iq, 头字段)。坏签名/坏位宽/压缩不可解均诚实抛错。"""
    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) < 22 or blob[:4] != ZIQ_MAGIC:
        raise ValueError("不是 ZIQ 文件（缺 ZIQ_ 签名）")
    off = 4
    is_compressed = blob[off]; off += 1
    bits = blob[off]; off += 1
    (sample_rate,) = struct.unpack_from("<Q", blob, off); off += 8
    (ann_len,) = struct.unpack_from("<Q", blob, off); off += 8
    ann_raw = blob[off:off + ann_len]; off += ann_len
    if len(ann_raw) != ann_len:
        raise ValueError("ZIQ 注解字段被截断")
    annotation = {}
    if ann_raw:
        try:
            annotation = json.loads(ann_raw.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"ZIQ 注解非合法 JSON: {e}") from e

    payload = blob[off:]
    if is_compressed == 1:
        try:
            import zstandard as zstd
        except ImportError as e:
            raise OSError(
                "ZIQ 压缩读取需要 zstandard 包（未安装）——不把压缩流当裸样本解析"
            ) from e
        try:
            payload = zstd.ZstdDecompressor().decompress(payload)
        except Exception as e:
            raise ValueError(f"ZIQ 压缩载荷解压失败: {e}") from e
    elif is_compressed != 0:
        raise ValueError(f"ZIQ is_compressed={is_compressed} 非 0/1（未知格式版本）")

    dt = np.dtype(_ziq_dtype(bits))
    raw = np.frombuffer(payload, dtype=dt).astype(np.float64)
    if len(raw) % 2:
        raw = raw[:-1]          # 奇数尾部按半样本丢弃并如实体现于 samples
    n = len(raw) // 2
    iq = raw[0::2] + 1j * raw[1::2]
    if bits == 8:
        iq = iq / 127.0
    elif bits == 16:
        iq = iq / 32767.0
    # 同 load_iq：返回 numpy 数组，不逐样本转 Python complex（S6）
    return {"iq": iq, "sample_rate": float(sample_rate),
            "bits": bits, "compressed": bool(is_compressed),
            "annotation": annotation, "samples": int(n)}


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    iq = (rng.standard_normal(1000) + 1j * rng.standard_normal(1000))
    # 模拟 LLM 发交错格式
    inter = np.empty(2000)
    inter[0::2] = iq.real
    inter[1::2] = iq.imag
    demo_path = os.path.join(tempfile.gettempdir(), "test.iq")
    info = save_iq(inter.tolist(), demo_path, 2.4e6,
                   center_freq_hz=100e6, note="test",
                   gain_db=20.5, device="RTL-SDR #0", driver="rtlsdr")
    print("存:", info["samples"], "samples, sidecar:", info["sidecar"])
    back = load_iq(demo_path)
    print("读回:", back["samples"], "samples, sr:", back["sample_rate"],
          "gain:", back["gain_db"], "dev:", back["device"])
    # 验证虚部没丢
    print("虚部非零:", bool(np.any(np.imag(back["iq"][:5]))))

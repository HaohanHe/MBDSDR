#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""把一次观测档案导出成头显可读的离线观测包（``mbdsdr.spatial/1``）。

用法：
    python scripts/export_spatial_bundle.py <archive.observation.json> -o <outdir>
        [--frames 48] [--bins 128]
        [--stars-json PATH | --no-stars]
        [--stars-limit 2000] [--max-mag 6.5] [--no-spectrum]
        [--audio-out PATH] [--demod wfm|nfm|am|usb|lsb|cw]
        [--passes-hours N] [--passes-json PATH]

频谱帧是**真实录制 IQ 的能量**：在录制时长内均匀取 ``--frames`` 个时刻，
每处 ``seek`` 后读 8192 样本，Hann 窗 FFT 转 dB，再按 ``--bins`` 等宽
分箱取均值。读不到就把原因写进 ``blockers``，绝不合成帧。

``--audio-out`` 把首个数据产物按 SigMF 记录的采样率经**仓内既有解调链**
（``dsp.wfm_broadcast_demod`` / ``dsp.demodulate`` + ``audio_to_playback``）
解调成 48 kHz 16-bit PCM 单声道 WAV，并以 ``label="demod:<mode>"``、
``utc=record.started_utc`` 挂进 ``bundle["audio"]``。解调失败或没有数据
时不写音频条目，把真实原因（「解调失败：…」）写进 ``blockers``。

星表默认取随仓 HYG v4.1 全天星表最亮的 ``--stars-limit`` 颗（可选
``--max-mag`` 过滤），``sky.source="hyg"``；``--stars-json`` 用外部 JSON
（``sky.source="external"``，优先于默认星表）；``--no-stars`` 不附星
（``sky.source="none"``）。

过境数据：``--passes-json`` 从外部 JSON 读（优先）；``--passes-hours N``
从档案站址与观测时刻起算 N 小时内过境，只对 ``orbit.tle_cache_dir()`` 里
**已有 TLE 缓存**的卫星调用仓内 ``sat_passes.predict_passes``，绝不联网或编造。
无站址/无缓存/无观测时刻时 ``sky.passes=[]`` 并把真实原因写进 ``blockers``。
自动计算出的每条过境附 ``tle_age_days``（观测时刻 − TLE 历元）；bundle 层
据此与历时/仰角/起止时刻一起打 ``suspect`` 标记（只标注，不删条目）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.observation_record import load_record, replay_plan  # noqa: E402
from mbdsdr_ai.spatial_bundle import build_spatial_bundle  # noqa: E402
from mbdsdr_ai.time_format import iso_utc_z  # noqa: E402

#: 每次取帧读取的样本数（约 3.4 ms @ 2.4 MS/s，足够给出稳定谱形）。
FRAME_SAMPLES = 8192

#: 支持的解调模式（复用仓内既有实现，不另写 DSP）。
DEMOD_MODES = ("wfm", "nfm", "am", "usb", "lsb", "cw")

#: 窄带模式整理为可播放音频时的音频低通（与 ``sdr_tools._demodulate`` 同口径）。
NARROW_CUTOFF_HZ = {"nfm": 3500.0, "am": 4500.0,
                    "usb": 3000.0, "lsb": 3000.0, "cw": 1200.0}

#: 解调输出音频采样率（仓内解调链统一 48 kHz）。
AUDIO_SR = 48000

#: 过境工具没有站址/缓存/时刻时的诚实原因（不写假过境）。
NO_SITE_FOR_PASSES = "没有站址，无法计算过境"
NO_TLE_CACHE = "没有 TLE 缓存，无法计算过境"
NO_TIME_FOR_PASSES = "没有观测时刻，无法计算过境"


def _read_stars(path: str) -> List[Dict[str, Any]]:
    """读外部星表 JSON：星对象列表，或 ``{"stars": [...]}`` 包装。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("stars")
    if not isinstance(data, list):
        raise ValueError("星表 JSON 必须是星对象列表")
    return data


def _read_passes_json(path: str) -> List[Dict[str, Any]]:
    """读外部过境 JSON：必须是 ``{"passes": [{...}, ...]}``。

    结构不对（不是对象、缺 ``passes`` 键、``passes`` 不是列表、条目不是
    对象）一律抛 ``ValueError``，由调用方按退出 2 处理，不静默当空表。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "passes" not in data:
        raise ValueError('过境 JSON 必须是 {"passes": [...]} 结构')
    passes = data["passes"]
    if not isinstance(passes, list):
        raise ValueError("过境 JSON 的 passes 必须是列表")
    for i, p in enumerate(passes):
        if not isinstance(p, dict):
            raise ValueError(f"passes[{i}] 不是对象")
    return passes


def _utc_unix(value: Any) -> Optional[float]:
    """ISO8601 → Unix 秒；缺时区按 UTC；解析不出返回 ``None``。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _observation_unix(record) -> Optional[float]:
    """档案观测时刻（``started_utc`` 优先，否则时间轴最早 ``utc``）。"""
    unix = _utc_unix(getattr(record, "started_utc", None))
    if unix is not None:
        return unix
    stamps = [_utc_unix(e.get("utc")) for e in (record.timeline or [])
              if isinstance(e, dict)]
    stamps = [s for s in stamps if s is not None]
    return min(stamps) if stamps else None


def _site_for_passes(record) -> Optional[Tuple[float, float, float]]:
    """档案站址 ``(lat, lon, alt_m)``；不可用时 ``None``（不按 0,0 算）。"""
    site = getattr(record, "site", None)
    if not isinstance(site, dict) or site.get("source") not in ("gnss",
                                                               "manual"):
        return None
    try:
        lat = float(site.get("lat_deg"))
        lon = float(site.get("lon_deg"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    try:
        alt = float(site.get("alt_m"))
    except (TypeError, ValueError):
        alt = 0.0
    return lat, lon, alt if math.isfinite(alt) else 0.0


def _read_tle_cache() -> List[Dict[str, Any]]:
    """读 ``orbit.tle_cache_dir()`` 里已有的 ``*.tle``（不联网、不补星）。

    只认「名称 + 1/2 行且编号一致」的完整缓存文件；坏文件直接跳过。
    """
    from mbdsdr_ai.orbit import tle_cache_dir

    cache_dir = tle_cache_dir()
    if not os.path.isdir(cache_dir):
        return []
    out: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(cache_dir)):
        if not name.endswith(".tle"):
            continue
        try:
            with open(os.path.join(cache_dir, name), "r", encoding="utf-8",
                      errors="replace") as fh:
                lines = [ln.strip()
                         for ln in fh.read().splitlines() if ln.strip()]
        except OSError:
            continue
        if len(lines) < 3:
            continue
        title, line1, line2 = lines[0], lines[1], lines[2]
        if not (line1.startswith("1 ") and line2.startswith("2 ")):
            continue
        if line1[2:7].strip() != line2[2:7].strip():
            continue
        try:
            catnr: Optional[int] = int(line2[2:7])
        except ValueError:
            catnr = None
        out.append({"catnr": catnr, "name": title,
                    "line1": line1, "line2": line2})
    return out


def _compute_passes(record, hours: float,
                    blockers: List[str]) -> List[Dict[str, Any]]:
    """按档案站址/时刻，对已有 TLE 缓存预测 ``hours`` 内过境（不联网）。

    每条过境附 ``tle_age_days``（观测时刻 − TLE 历元，单位天），历元解析
    失败时不写该键（bundle 层据此不判「TLE 过旧」）。
    """
    from mbdsdr_ai.sat_passes import GroundStation, predict_passes
    from mbdsdr_ai.time_basis import tle_epoch_utc

    site = _site_for_passes(record)
    if site is None:
        blockers.append(NO_SITE_FOR_PASSES)
        return []
    start_unix = _observation_unix(record)
    if start_unix is None:
        blockers.append(NO_TIME_FOR_PASSES)
        return []
    tles = _read_tle_cache()
    if not tles:
        blockers.append(NO_TLE_CACHE)
        return []

    station = GroundStation(lat_deg=site[0], lon_deg=site[1], alt_m=site[2])
    out: List[Dict[str, Any]] = []
    for tle in tles:
        try:
            events = predict_passes(
                [tle["name"], tle["line1"], tle["line2"]], station,
                hours=hours, start_unix=start_unix)
        except Exception as e:  # noqa: BLE001 - 原因必须原样进 blockers
            blockers.append(f"过境预测失败（{tle['name']}）："
                            f"{type(e).__name__}: {e}")
            continue
        epoch = tle_epoch_utc(tle["line1"])
        tle_age_days = (None if epoch is None
                        else (start_unix - epoch.timestamp()) / 86400.0)
        for p in events:
            entry: Dict[str, Any] = {
                "norad_id": tle["catnr"],
                "name": p.sat_name or tle["name"],
                "start_utc": iso_utc_z(p.rise_time.utc_datetime().timestamp()),
                "end_utc": iso_utc_z(p.set_time.utc_datetime().timestamp()),
                "max_alt_deg": round(float(p.max_alt), 1),
                "duration_s": round(float(p.duration), 1),
            }
            if tle_age_days is not None:
                entry["tle_age_days"] = tle_age_days
            out.append(entry)
    return out


def _product_hints(record,
                   data_path: str) -> Dict[str, Any]:
    """档案里对应产物的采样率/中心频率/样本数/格式。

    桌面端 UI 录音写成 ``.iq`` + ``.iq.json``，不是 SigMF；IQPlayback 只能
    从 SigMF 读采样率。这些字段同一档案已经写明，用它把「采样率未知」
    变成可回放，不额外猜数值。
    """
    target = os.path.abspath(str(data_path or ""))
    for product in (getattr(record, "products", None) or []):
        if not isinstance(product, dict):
            continue
        raw = product.get("data_path") or product.get("path") or ""
        if os.path.abspath(str(raw)) != target:
            continue
        return {key: product.get(key) for key in
                ("sample_rate_hz", "center_freq_hz", "samples", "format")}
    return {}


def _is_raw_cf32(hints: Dict[str, Any]) -> bool:
    fmt = str((hints or {}).get("format") or "").strip().lower()
    return fmt in ("cf32", "complex64")


def _cf32_spectrum_frames(data_path: str, frames_n: int, bins: int,
                          rate: float, center: Optional[float],
                          samples: int) -> Tuple[List[Dict[str, Any]],
                                                 Optional[str],
                                                 Optional[float],
                                                 Optional[float],
                                                 Optional[float]]:
    """非 SigMF 的桌面录音：按 cf32 直接取帧（采样率/样本数来自档案）。"""
    import numpy as np

    data = np.memmap(data_path, dtype=np.complex64, mode="r")
    total = int(data.size)
    if samples and 0 < samples < total:
        total = int(samples)
    if total <= 0 or rate <= 0:
        return [], "cf32 数据为空或采样率无效", center, None, None
    duration = total / rate
    frames: List[Dict[str, Any]] = []
    for i in range(frames_n):
        at = duration * (i + 0.5) / frames_n
        start = int(at * rate)
        block = np.asarray(data[start:start + FRAME_SAMPLES],
                           dtype=np.complex64)
        if block.size == 0:
            return (frames,
                    f"频谱帧读取失败：{at:.6f}s 处没有可读样本",
                    center, rate, duration)
        windowed = block * np.hanning(block.size)
        spec = np.fft.fftshift(np.fft.fft(windowed))
        db = 20.0 * np.log10(np.abs(spec) / block.size + 1e-12)
        n_bins = max(1, min(int(bins), int(db.size)))
        parts = np.array_split(db, n_bins)
        frames.append({"at_s": float(at),
                       "db": [float(np.mean(p)) for p in parts if p.size]})
    return frames, None, center, rate, duration


def _spectrum_frames(data_path: str, frames_n: int,
                     bins: int,
                     hints: Optional[Dict[str, Any]] = None,
                     ) -> Tuple[List[Dict[str, Any]],
                                Optional[str],
                                Optional[float],
                                Optional[float],
                                Optional[float]]:
    """从真实 IQ 文件均匀取帧；返回 (帧, 错误原因, 中心频率, 采样率, 时长)。"""
    import numpy as np

    from mbdsdr_ai.playback import IQPlayback

    hints = hints or {}
    hint_rate = float(hints.get("sample_rate_hz") or 0.0)
    hint_center = float(hints.get("center_freq_hz") or 0.0) or None
    hint_samples = int(hints.get("samples") or 0)

    # 桌面 UI 录音（``.iq`` + ``.iq.json``）不是 SigMF；档案里已有采样率，
    # 直接按 cf32 读，不再让 IQPlayback 报「缺 core:sample_rate」。
    if (hint_rate > 0 and _is_raw_cf32(hints)
            and not str(data_path).lower().endswith(".sigmf-data")):
        return _cf32_spectrum_frames(data_path, frames_n, bins,
                                     hint_rate, hint_center, hint_samples)

    pb = IQPlayback(loop=False)
    opened = pb.open(data_path)
    center = (pb.center_freq_hz if opened and pb.center_freq_hz > 0
              else hint_center)
    rate = pb.sample_rate if opened and pb.sample_rate > 0 else None
    duration = pb.get_duration() if opened else 0.0

    if (not opened or rate is None or duration <= 0) \
            and hint_rate > 0 and _is_raw_cf32(hints):
        pb.close()
        return _cf32_spectrum_frames(data_path, frames_n, bins,
                                     hint_rate, hint_center, hint_samples)
    if not opened:
        return [], (pb.last_error or "无法打开 IQ 数据文件"), center, None, None
    if duration <= 0:
        pb.close()
        return [], "采样率未知，无法在时长内均匀取帧", center, rate, None

    frames: List[Dict[str, Any]] = []
    error: Optional[str] = None
    try:
        for i in range(frames_n):
            at = duration * (i + 0.5) / frames_n
            pb.seek(at)
            block = pb.read_samples(FRAME_SAMPLES, copy=True)
            n = int(block.size)
            if n == 0:
                error = (f"频谱帧读取失败：{at:.6f}s 处没有可读样本"
                         f"（{pb.last_error or pb.state}）")
                break
            windowed = np.asarray(block, dtype=np.complex64) * np.hanning(n)
            spec = np.fft.fftshift(np.fft.fft(windowed))
            db = 20.0 * np.log10(np.abs(spec) / n + 1e-12)
            n_bins = max(1, min(int(bins), int(db.size)))
            parts = np.array_split(db, n_bins)
            frames.append({"at_s": float(at),
                           "db": [float(np.mean(p)) for p in parts if p.size]})
    finally:
        pb.close()
    return frames, error, center, rate, duration


def _demod_audio(data_path: str,
                 mode: str,
                 hints: Optional[Dict[str, Any]] = None,
                 ) -> Tuple[Optional[Any], int, Optional[str]]:
    """按录制采样率把 IQ 解调成 48 kHz 单声道音频。

    复用仓内既有解调链（与 ``sdr_tools._demodulate`` 同一条路）：
    ``wfm`` 走 ``dsp.wfm_broadcast_demod``（信道低通→鉴频→降采样→去加重），
    其余模式走 ``dsp.demodulate`` + ``audio_to_playback``。

    Returns:
        ``(audio_float32, 48000, None)``；失败返回 ``(None, 0, 真实原因)``，
        不合成任何音频。
    """
    import numpy as np

    from mbdsdr_ai.playback import IQPlayback

    hints = hints or {}
    hint_rate = float(hints.get("sample_rate_hz") or 0.0)
    if (hint_rate > 0 and _is_raw_cf32(hints)
            and not str(data_path).lower().endswith(".sigmf-data")):
        mm = np.memmap(data_path, dtype=np.complex64, mode="r")
        samples = int(hints.get("samples") or mm.size)
        block = np.asarray(mm[:min(samples, mm.size)], dtype=np.complex64)
        rate = hint_rate
        return _demod_audio_block(block, rate, mode)

    pb = IQPlayback(loop=False)
    opened = pb.open(data_path)
    rate = float(pb.sample_rate) if opened else 0.0
    if rate > 0:
        try:
            block = pb.read_samples(pb.n_samples, copy=True)
        finally:
            pb.close()
    else:
        pb.close()
        hint_rate = float(hints.get("sample_rate_hz") or 0.0)
        if hint_rate <= 0 or not _is_raw_cf32(hints):
            return None, 0, "SigMF 元数据未记录采样率，无法解调"
        mm = np.memmap(data_path, dtype=np.complex64, mode="r")
        samples = int(hints.get("samples") or mm.size)
        block = np.asarray(mm[:min(samples, mm.size)], dtype=np.complex64)
        rate = hint_rate
    if block is None or int(np.asarray(block).size) == 0:
        return None, 0, "IQ 数据文件没有可读样本"
    return _demod_audio_block(block, rate, mode)


def _demod_audio_block(block: Any, rate: float,
                       mode: str) -> Tuple[Optional[Any], int, Optional[str]]:
    """把一棵 IQ 样本按 ``mode`` 解调成 48 kHz 单声道（不读盘）。"""
    import numpy as np

    from mbdsdr_ai.dsp import (audio_to_playback, demodulate,
                               wfm_broadcast_demod)

    if mode == "wfm":
        audio = wfm_broadcast_demod(block, rate, audio_sr=AUDIO_SR,
                                    deemph_us=50.0)
    else:
        raw = demodulate(block, mode=mode.upper(), sample_rate=rate)
        audio = audio_to_playback(raw, rate, NARROW_CUTOFF_HZ[mode], AUDIO_SR)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return None, 0, "解调输出为空（输入样本不足或没有可用信号）"
    return audio, AUDIO_SR, None


def _write_wav_mono(path: str, audio: Any, sample_rate: int) -> None:
    """写 16-bit PCM 单声道 WAV（量化口径与 ``recorder`` 的 16 bit 一致）。"""
    import wave

    import numpy as np

    x = np.clip(np.asarray(audio, dtype=np.float64).reshape(-1), -1.0, 1.0)
    pcm = (x * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm.tobytes())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="把观测档案导出成 mbdsdr.spatial/1 离线观测包")
    ap.add_argument("archive", help="*.observation.json 路径")
    ap.add_argument("-o", "--outdir", required=True, help="输出目录")
    ap.add_argument("--frames", type=int, default=48, help="频谱帧数")
    ap.add_argument("--bins", type=int, default=128, help="每帧 dB 频点数")
    ap.add_argument("--max-stars", type=int, default=2000, dest="max_stars",
                    help="星点上限（按星等截断）")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--stars-json", default=None, dest="stars_json",
                     help="外部星表 JSON（[{name,ra_deg,dec_deg,mag}]）")
    src.add_argument("--no-stars", action="store_true", dest="no_stars",
                     help="不附星表（sky.stars=[]，source=none）")
    ap.add_argument("--stars-limit", type=int, default=2000,
                    dest="stars_limit",
                    help="从随仓星表取最亮的 N 颗（默认 2000）")
    ap.add_argument("--max-mag", type=float, default=None, dest="max_mag",
                    help="只取 V 星等 <= 该值的星（默认不设限）")
    ap.add_argument("--no-spectrum", action="store_true", dest="no_spectrum",
                    help="跳过频谱帧生成")
    ap.add_argument("--audio-out", default=None, dest="audio_out",
                    metavar="PATH",
                    help="把首个 IQ 产物解调成 16-bit PCM 单声道 WAV 并挂进 "
                         "bundle.audio")
    ap.add_argument("--demod", default="wfm",
                    help=f"解调模式（{'/'.join(DEMOD_MODES)}，默认 wfm）")
    ap.add_argument("--passes-hours", type=float, default=0.0,
                    dest="passes_hours", metavar="N",
                    help="从档案站址与时刻起算 N 小时内过境（默认 0 = 不算）")
    ap.add_argument("--passes-json", default=None, dest="passes_json",
                    metavar="PATH",
                    help='外部过境 JSON（{"passes": [...]}，优先于自动计算）')
    args = ap.parse_args(argv)

    demod = (args.demod or "").strip().lower()
    if demod not in DEMOD_MODES:
        print(f"不支持的解调模式：{args.demod!r}"
              f"（可用：{', '.join(DEMOD_MODES)}）", file=sys.stderr)
        return 2
    if args.passes_hours < 0:
        print("--passes-hours 不能为负数", file=sys.stderr)
        return 2

    record, err = load_record(args.archive)
    if record is None:
        print(f"读取档案失败：{err}", file=sys.stderr)
        return 2

    stars: Optional[List[Dict[str, Any]]] = None
    stars_total: Optional[int] = None
    stars_source = "none"
    if args.stars_json:
        try:
            stars = _read_stars(args.stars_json)
        except (OSError, ValueError) as e:
            print(f"读取星表失败：{type(e).__name__}: {e}", file=sys.stderr)
            return 2
        stars_source = "external"
    elif args.no_stars:
        stars = []
        stars_total = 0
    else:
        from mbdsdr_ai.skyengine import star_catalog as sc  # noqa: PLC0415

        try:
            catalog = sc.get_catalog()
            stars = sc.brightest_stars(args.stars_limit, args.max_mag)
        except FileNotFoundError as e:
            print(f"加载随仓星表失败：{e}", file=sys.stderr)
            return 2
        stars_total = (catalog.count if args.max_mag is None
                       else catalog.count_below(args.max_mag))
        stars_source = "hyg"

    passes: List[Dict[str, Any]] = []
    pass_blockers: List[str] = []
    if args.passes_json:
        try:
            passes = _read_passes_json(args.passes_json)
        except (OSError, ValueError) as e:
            print(f"读取过境数据失败：{type(e).__name__}: {e}",
                  file=sys.stderr)
            return 2
    elif args.passes_hours > 0:
        passes = _compute_passes(record, args.passes_hours, pass_blockers)

    tuning = record.tuning if isinstance(record.tuning, dict) else {}
    plan = replay_plan(record)
    if args.no_spectrum:
        spectrum: Optional[Dict[str, Any]] = {
            "frames": [],
            "center_freq_hz": tuning.get("center_freq_hz"),
            "sample_rate_hz": tuning.get("sample_rate_hz"),
            "duration_s": None,
            "error": "未生成频谱帧（--no-spectrum）",
        }
    elif not plan["data_paths"]:
        spectrum = {
            "frames": [],
            "center_freq_hz": tuning.get("center_freq_hz"),
            "sample_rate_hz": tuning.get("sample_rate_hz"),
            "duration_s": None,
            "error": "没有可回放的 IQ 数据",
        }
    else:
        if args.frames <= 0 or args.bins <= 0:
            print("--frames 与 --bins 必须是正整数", file=sys.stderr)
            return 2
        frames, serr, center, rate, duration = _spectrum_frames(
            plan["data_paths"][0], args.frames, args.bins,
            hints=_product_hints(record, plan["data_paths"][0]))
        spectrum = {
            "frames": frames,
            "center_freq_hz": center,
            "sample_rate_hz": rate,
            "duration_s": duration,
        }
        if serr:
            spectrum["error"] = serr

    audio_entries: List[Dict[str, Any]] = []
    audio_error: Optional[str] = None
    if args.audio_out:
        if not plan["data_paths"]:
            audio_error = "解调失败：没有可回放的 IQ 数据文件"
        else:
            try:
                audio, audio_sr, derr = _demod_audio(
                    plan["data_paths"][0], demod,
                    hints=_product_hints(record, plan["data_paths"][0]))
            except Exception as e:  # noqa: BLE001 - 原因必须原样进 blockers
                audio, audio_sr, derr = None, 0, f"{type(e).__name__}: {e}"
            if derr:
                audio_error = f"解调失败：{derr}"
            else:
                try:
                    os.makedirs(
                        os.path.dirname(os.path.abspath(args.audio_out)),
                        exist_ok=True)
                    _write_wav_mono(args.audio_out, audio, audio_sr)
                except OSError as e:
                    audio_error = (f"解调音频写入失败：{type(e).__name__}: {e}")
                else:
                    audio_entries.append({
                        "path": args.audio_out,
                        "label": f"demod:{demod}",
                        "utc": record.started_utc,
                    })

    bundle = build_spatial_bundle(record, stars=stars, passes=passes,
                                  spectrum=spectrum,
                                  audio=audio_entries,
                                  max_stars=args.max_stars,
                                  stars_total=stars_total,
                                  stars_source=stars_source)
    if audio_error:
        bundle["blockers"].append(audio_error)
    bundle["blockers"].extend(pass_blockers)

    try:
        os.makedirs(args.outdir, exist_ok=True)
        path = os.path.join(args.outdir, "bundle.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        print(f"写入失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    for b in bundle["blockers"]:
        print(f"  - {b}", file=sys.stderr)
    sky = bundle["sky"]
    summary = (f"blockers={len(bundle['blockers'])} "
               f"frames={len(bundle['spectrum']['frames'])} "
               f"stars={len(sky['stars'])} "
               f"passes={len(sky['passes'])} "
               f"audio={len(bundle['audio'])}")
    if any("alt_deg" in s for s in sky["stars"]):
        visible = sum(1 for s in sky["stars"] if s.get("visible"))
        summary += f" visible={visible}"
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())

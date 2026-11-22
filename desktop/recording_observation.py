# SPDX-License-Identifier: MIT
"""Desktop cf32 sidecars -> observation archives, without Qt or IQ reads.

Only the explicitly supplied, finalized segments are inspected. A bad segment
fails independently; callers must display ``errors`` even when ``archives`` is
nonempty, and must not replace an existing recording failure with export success.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import Any
from uuid import uuid4

from mbdsdr_ai.observation_record import MAX_TIMELINE_EVENTS, ObservationRecord

__all__ = ["export_desktop_observations"]

_MAX_SIDECAR_BYTES = 1 << 20
_SOURCE = "desktop_sidecar"


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} 必须是有限数值")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{field} 必须是{'正' if positive else '有限'}数值")
    return result


def _count(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} 必须是 >= {minimum} 的整数")
    return value


def _datetime(value: Any, field: str, *, aware: bool = False) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and ("T" in value or " " in value):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field} 缺失或不是 ISO 日期时间")
    if aware and dt.utcoffset() is None:
        raise ValueError(f"{field} 必须带明确时区，不能把本地时间当成 UTC")
    return dt


def _file_state(path: Path) -> tuple:
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"不是普通文件：{path}")
    return st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino


def _reject_constant(value: str):
    raise ValueError(f"sidecar 含非法 JSON 数值：{value}")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"sidecar 含重复字段：{key}")
        result[key] = value
    return result


def _read_segment(segment: Mapping) -> tuple:
    raw_path = segment.get("path")
    raw_sidecar = segment.get("sidecar")
    if not raw_path or not raw_sidecar:
        raise ValueError("缺少 IQ path 或 sidecar（未成功落盘的元数据不能重建冒充）")
    path = Path(raw_path).resolve(strict=True)
    sidecar = Path(raw_sidecar).resolve(strict=True)
    if sidecar != Path(str(path) + ".json").resolve(strict=True):
        raise ValueError("sidecar 不是该 IQ 的 <path>.json")
    data_state = _file_state(path)
    meta_state = _file_state(sidecar)
    if meta_state[0] > _MAX_SIDECAR_BYTES:
        raise ValueError("sidecar 过大，拒绝在桌面导出路径读取")
    with sidecar.open("r", encoding="utf-8") as stream:
        # Bounded even if another writer grows the file after stat().
        text = stream.read(_MAX_SIDECAR_BYTES + 1)
    if len(text) > _MAX_SIDECAR_BYTES:
        raise ValueError("sidecar 过大，拒绝在桌面导出路径读取")
    meta = json.loads(text, parse_constant=_reject_constant,
                      object_pairs_hook=_unique_keys)
    if not isinstance(meta, dict):
        raise ValueError("sidecar 必须是 JSON 对象")
    if meta.get("format") != "cf32":
        raise ValueError("sidecar format 不是 cf32")
    for label, source in (("segment", segment), ("sidecar", meta)):
        if source.get("complete") is not True or source.get("aborted_reason"):
            raise ValueError(f"{label} 不完整：{source.get('aborted_reason') or 'complete 不是 true'}")
    samples = _count(meta.get("samples"), "sidecar.samples", minimum=1)
    rate = _number(meta.get("sample_rate"), "sidecar.sample_rate", positive=True)
    index = _count(meta.get("segment_index"), "sidecar.segment_index", minimum=1)
    if _count(segment.get("samples"), "segment.samples", minimum=1) != samples:
        raise ValueError("segment 与 sidecar 的 samples 不一致")
    if _number(segment.get("sample_rate"), "segment.sample_rate", positive=True) != rate:
        raise ValueError("segment 与 sidecar 的 sample_rate 不一致")
    if "segment_index" in segment and _count(
            segment["segment_index"], "segment.segment_index", minimum=1) != index:
        raise ValueError("segment 与 sidecar 的 segment_index 不一致")
    if "start_time" in segment and segment["start_time"] != meta.get("start_time"):
        raise ValueError("segment 与 sidecar 的 start_time 不一致")
    size = samples * 8
    if data_state[0] != size:
        raise ValueError(f"IQ stat 字节数 {data_state[0]} 与 cf32 samples*8={size} 不一致")
    if "size_bytes" in segment and _count(segment["size_bytes"], "size_bytes") != size:
        raise ValueError("segment.size_bytes 与 IQ stat 不一致")
    duration = samples / rate
    if not math.isfinite(duration):
        raise ValueError("样本数/采样率给出的时长不是有限值")
    for label, source in (("segment", segment), ("sidecar", meta)):
        if "duration_s" in source:
            reported = _number(source["duration_s"], f"{label}.duration_s")
            # Desktop sidecars round to milliseconds, including short segments.
            if reported < 0 or not math.isclose(reported, duration, rel_tol=0, abs_tol=0.000501):
                raise ValueError(f"{label}.duration_s 与样本数/采样率不一致")
    return path, sidecar, meta, samples, rate, index, data_state, meta_state


def _frequency_history(meta: dict, samples: int, index: int) -> tuple:
    final = _number(meta.get("center_freq_hz"), "center_freq_hz", positive=True)
    changes = meta.get("center_freq_changes", [])
    if not isinstance(changes, list):
        raise ValueError("center_freq_changes 必须是列表")
    if len(changes) + 2 > MAX_TIMELINE_EVENTS:
        raise ValueError("center_freq_changes 超出时间轴上限，拒绝静默截断")
    initial = final
    previous_sample = -1
    previous_freq = None
    validated = []
    for i, change in enumerate(changes):
        if not isinstance(change, dict):
            raise ValueError(f"center_freq_changes[{i}] 不是对象")
        sample = _count(change.get("start_sample"), "start_sample")
        if sample > samples or sample < previous_sample:
            raise ValueError("center_freq_changes 样本越界或顺序倒退")
        before = _number(change.get("from_hz"), "from_hz", positive=True)
        after = _number(change.get("to_hz"), "to_hz", positive=True)
        _datetime(change.get("at_time"), "center_freq_changes.at_time")
        if "segment_index" in change and _count(
                change["segment_index"], "center_freq_changes.segment_index", minimum=1) != index:
            raise ValueError("center_freq_changes 引用了其他段")
        if "actual_hz" in change and _number(
                change["actual_hz"], "actual_hz", positive=True) != after:
            raise ValueError("center_freq_changes actual_hz 与 to_hz 不一致")
        if "target_hz" in change:
            _number(change["target_hz"], "target_hz", positive=True)
        if previous_freq is not None and before != previous_freq:
            raise ValueError("center_freq_changes 频率历史不连续")
        if i == 0:
            initial = before
        previous_sample, previous_freq = sample, after
        validated.append((sample, change))
    if changes and previous_freq != final:
        raise ValueError("center_freq_changes 末频与 center_freq_hz 不一致")
    return initial, validated


def _build_record(meta: dict, path: Path, sidecar: Path, samples: int,
                  rate: float, index: int, snapshot: dict,
                  ended_utc: str | None) -> ObservationRecord:
    initial, changes = _frequency_history(meta, samples, index)
    start = _datetime(meta.get("start_time"), "sidecar.start_time")
    notes = [
        "产物与调谐信息来源 desktop_sidecar；未读取 IQ 内容或计算哈希。",
        "站址取自调用方保存的起录快照；未知站址保持未知。",
        "UTC 时间轴以该段 start_time 为基点，按实样本索引/采样率推算；"
        "不是硬件授时或实测事件墙钟，不代表没有读取间断。",
        "sidecar 未记录增益变更、读取间断或解码命中；未生成这些事件。",
    ]
    # 起录快照自带的备注（例如「GNSS 定位与配置站址不同，站址按配置值记录」）
    # 必须原样带进档案；否则运维看档案时不知道当时两处坐标并不一致。
    _extra = snapshot.get("recording_notes")
    if isinstance(_extra, (list, tuple)):
        notes.extend(str(n) for n in _extra if str(n).strip())
    start_source = "desktop_sidecar.start_time"
    if start.utcoffset() is None:
        anchor = _datetime(snapshot.get("recording_started_at"),
                           "snapshot.recording_started_at（naive start_time 需要起录时区）",
                           aware=True)
        # Never use the machine's timezone at export time, nor a GNSS engine's
        # last-update timestamp. The caller supplies the recording's wall zone.
        start = start.replace(tzinfo=timezone(anchor.utcoffset()))
        start_source += "+snapshot.recording_started_at.offset"
        notes.append(
            "sidecar.start_time 无时区；使用起录系统时间 recording_started_at 的明确 UTC 偏移，"
            "假定该段仍在同一偏移下。跨夏令时/系统时区变更的录音须提供带时区的 sidecar；"
            "该推算不具备 GNSS 精度。")
    start = start.astimezone(timezone.utc)
    if ended_utc is not None and _datetime(ended_utc, "ended_utc", aware=True) < start:
        raise ValueError("ended_utc 早于该段起录时刻")
    stop = start + timedelta(seconds=samples / rate)
    site = snapshot.get("site") or {}
    if not isinstance(site, Mapping):
        raise ValueError("snapshot.site 必须是对象")
    site = {key: site.get(key) for key in ("source", "lat_deg", "lon_deg", "alt_m", "reason")}
    if site["source"] not in ("manual", "gnss"):
        site.update(source="none", lat_deg=None, lon_deg=None, alt_m=None)
        site["reason"] = site["reason"] or "起录快照没有可用站址"
    elif site["lat_deg"] is None or site["lon_deg"] is None:
        site.update(source="none", lat_deg=None, lon_deg=None, alt_m=None,
                    reason="起录快照站址缺少坐标")
    else:
        lat = _number(site["lat_deg"], "site.lat_deg")
        lon = _number(site["lon_deg"], "site.lon_deg")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("snapshot.site 坐标越界")
    gain = meta.get("gain_db")
    if gain is not None:
        gain = _number(gain, "gain_db")
    tuning = {"source": _SOURCE, "center_freq_hz": initial,
              "sample_rate_hz": rate, "gain_db": gain}

    def event(kind: str, sample: int, detail: dict) -> dict:
        return {"kind": kind, "samples": sample,
                "utc": (start + timedelta(seconds=sample / rate)).isoformat(),
                "detail": {**detail, "source": _SOURCE,
                           "utc_source": start_source if sample == 0 else "sample_offset"}}

    timeline = [event("record_start", 0, {**tuning, "start_time": meta["start_time"]})]
    for sample, change in changes:
        timeline.append(event("tune", sample, dict(change)))
    timeline.append(event("record_stop", samples, {"samples": samples, "complete": True}))
    product = {"path": str(sidecar), "kind": _SOURCE, "source": _SOURCE,
               "data_path": str(path), "format": "cf32", "samples": samples,
               "bytes": samples * 8, "sample_rate_hz": rate,
               "center_freq_hz": initial, "final_center_freq_hz": meta["center_freq_hz"],
               "gain_db": gain, "driver": meta.get("driver"), "device": meta.get("device"),
               "segment_index": index, "complete": True,
               "started_utc": start.isoformat(), "sha256": None}
    return ObservationRecord(
        observation_id=f"desktop-{uuid4().hex}",
        started_utc=start.isoformat(), ended_utc=stop.isoformat(),
        time={"source": "system", "time_source": "desktop_system_clock",
              "precision": "sidecar 墙钟（精度未验证）；样本偏移推算",
              "start_utc_source": start_source, "end_utc_source": "sample_offset",
              "recording_started_at": snapshot.get("recording_started_at").isoformat()
              if isinstance(snapshot.get("recording_started_at"), datetime)
              else snapshot.get("recording_started_at"),
              "session_ended_utc": ended_utc,
              "session_end_source": "caller_system_clock" if ended_utc else None,
              "runtime_snapshot_time": deepcopy(snapshot.get("time") or {})},
        site=site,
        device={"source": _SOURCE, "driver": meta.get("driver"),
                "name": meta.get("device"), "serial": None,
                "reason": "设备身份取自 desktop_sidecar；未记录序列号"},
        tuning=tuning, products=[product],
        result={"summary": f"完整 cf32 段 #{index}，{samples} 样本；未验证接收内容"},
        evidence="unverified", notes=notes, timeline=timeline,
    )


def _publish(record: ObservationRecord, destination: Path,
             states: tuple) -> None:
    if os.path.lexists(destination):
        raise FileExistsError(f"档案已存在，未覆盖：{destination}")
    # Reuse the model's validation and atomic JSON writer in a private same-FS
    # directory. link() publishes atomically WITHOUT replacing another file,
    # even if one appears after the existence check. No bespoke serialization.
    with tempfile.TemporaryDirectory(prefix=".desktop-observation-",
                                     dir=destination.parent) as staging:
        staged = Path(staging) / destination.name
        ok, error = record.write_json(str(staged))
        if not ok:
            raise OSError(error or "观测档案写入失败")
        for source, before in states:
            if _file_state(source) != before:
                raise ValueError(f"导出期间源文件发生变化，未发布档案：{source}")
        os.link(staged, destination)


def export_desktop_observations(
    segments: Sequence[Mapping[str, Any]], snapshot: Mapping[str, Any] | None,
    *, ended_utc: str | datetime | None = None,
) -> dict[str, list[str]]:
    """Export one ``<IQ stem>.observation.json`` per valid, complete cf32 segment.

    ``segments`` are finalized desktop segment info dictionaries (including
    ``path, sidecar, samples, sample_rate, complete``). No directory discovery.
    ``snapshot`` is a COPY of RuntimeSources.snapshot() captured at start. For
    naive desktop timestamps, the caller must add ``recording_started_at`` from
    ``datetime.now().astimezone().isoformat()`` AT START, retaining the local UTC
    offset (do not replace it with a UTC-only timestamp on a non-UTC host).
    Each segment uses its OWN start_time, rate, count and frequency history.

    ``ended_utc`` is an optional timezone-aware SYSTEM session-stop timestamp;
    preserved as session metadata, not applied to the end of every segment.
    Segment ends and event UTCs are explicitly sample-derived estimates. GNSS
    runtime time state is retained separately, never claimed for desktop clocks.
    No input mutation, IQ reads, hashing, or overwrite of existing archives.
    Returns absolute ``archives`` paths and user-visible per-segment ``errors``.
    """
    result: dict[str, list[str]] = {"archives": [], "errors": []}
    try:
        if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)):
            raise ValueError("segments 必须是已收尾段的列表")
        if not segments:
            raise ValueError("没有已落盘的录音段，拒绝导出空档案")
        if snapshot is not None and not isinstance(snapshot, Mapping):
            raise ValueError("snapshot 必须是起录快照对象")
        snap = deepcopy(dict(snapshot or {}))
        end = (_datetime(ended_utc, "ended_utc", aware=True)
               .astimezone(timezone.utc).isoformat()) if ended_utc is not None else None
    except (TypeError, ValueError, OverflowError) as exc:
        result["errors"].append(str(exc))
        return result
    for ordinal, segment in enumerate(segments, 1):
        label = str(segment.get("path") or f"段 #{ordinal}") if isinstance(segment, Mapping) else f"段 #{ordinal}"
        try:
            if not isinstance(segment, Mapping):
                raise ValueError("segment 必须是对象")
            path, sidecar, meta, samples, rate, index, data_state, meta_state = _read_segment(segment)
            record = _build_record(meta, path, sidecar, samples, rate, index, snap, end)
            destination = path.with_suffix(".observation.json")
            _publish(record, destination, ((path, data_state), (sidecar, meta_state)))
            result["archives"].append(str(destination))
        except (OSError, TypeError, ValueError, OverflowError, RecursionError) as exc:
            result["errors"].append(f"{label}：{type(exc).__name__}: {exc}")
    return result

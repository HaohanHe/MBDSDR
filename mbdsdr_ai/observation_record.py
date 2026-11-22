# SPDX-License-Identifier: MIT
"""观测档案：把「一次观测」落成可复算的 JSON / CSV（PRD P1-08）。

诚实口径
--------
* **无产物不导出**：一次观测若没有任何录音/解码产物，``validate_for_export``
  返回错误，调用方必须拒绝导出，而不是导出一份「什么也没收到」的空档案。
* **未知即未知**：每个字段要么有值且带来源，要么 ``None`` + 原因；不用默认
  值填充（站址不写 0,0，频率不写 0）。
* **证据分级**：``evidence`` 只取 ``real_hw`` / ``self_test`` / ``unverified``，
  与解码器证据分级（D2）同一口径。
* **来源可追**：时间、站址、设备、频率四组各带自己的 ``source``，不混用。
  时间来源复用 ``runtime_sources`` 的 ``gnss`` / ``system``；站址来源复用
  ``gnss`` / ``manual`` / ``none``。

本模块纯数据 + 文件 IO，不依赖 Qt；桌面端、CLI、工具面共用同一份序列化。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "SCHEMA",
    "EVIDENCE_LEVELS",
    "MAX_TIMELINE_EVENTS",
    "TIMELINE_KINDS",
    "make_event",
    "normalize_timeline",
    "ObservationRecord",
    "product_from_sigmf",
    "record_from_snapshot",
    "replay_context",
    "replay_plan",
]

SCHEMA = "mbdsdr.observation/1"

#: 证据强度。与 D2 解码器分级同词表，便于能力表与档案共用一套判定。
EVIDENCE_LEVELS = ("real_hw", "self_test", "unverified")

#: 单次观测时间轴的硬上限。超限后只追加一条 ``timeline_truncated``，
#: 不静默丢弃也不无限增长——档案的体积必须有界。
MAX_TIMELINE_EVENTS = 2000

#: 已知事件种类（只用于展示/文档提示，**不做校验**：时间轴允许扩展）。
TIMELINE_KINDS = ("record_start", "record_stop", "tune", "gain",
                  "sample_rate", "decode_hit", "read_gap", "sample_loss",
                  "timeline_truncated")


def make_event(kind: str, detail=None, utc: Optional[str] = None) -> Dict[str, Any]:
    """构造一条时间轴事件：``{"utc": ISO8601, "kind": str, "detail": dict}``。"""
    return {
        "utc": utc if utc else datetime.now(timezone.utc).isoformat(),
        "kind": str(kind),
        "detail": dict(detail or {}),
    }


def normalize_timeline(events) -> List[Dict[str, Any]]:
    """只保留 dict 条目，按 ``utc`` 字符串稳定升序；不修补缺字段的条目。

    缺 ``utc`` 的条目排在前面（排序键取空串），字段原样保留——修补成
    当前时间等于凭空编造一个没发生过的时刻，交给 ``validate_for_export``
    如实报错。
    """
    items = [dict(e) for e in (events or []) if isinstance(e, dict)]

    def _key(e: Dict[str, Any]) -> str:
        value = e.get("utc")
        return value if isinstance(value, str) else ""

    return sorted(items, key=_key)


#: CSV 列顺序固定；新增列只能追加，不能重排（否则旧表读取方会错位）。
CSV_COLUMNS = (
    "observation_id",
    "started_utc",
    "ended_utc",
    "time_source",
    "time_precision",
    "site_source",
    "lat_deg",
    "lon_deg",
    "alt_m",
    "device_driver",
    "device_serial",
    "target_name",
    "target_norad",
    "center_freq_hz",
    "sample_rate_hz",
    "gain_db",
    "doppler_residual_hz",
    "product_count",
    "product_bytes",
    "result_summary",
    "evidence",
)


def _iso_utc(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _sha256_file(path: str, chunk: int = 1 << 20) -> Optional[str]:
    """流式计算哈希。文件不可读时返回 None，不伪造。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    except OSError:
        return None


@dataclass
class ObservationRecord:
    """一次观测的完整档案。

    构造后先调 :meth:`validate_for_export`；有错误就必须拒绝导出并把错误
    原文交给用户，不得降级成「部分导出」。
    """

    observation_id: str
    started_utc: Optional[str] = None
    ended_utc: Optional[str] = None
    time: Dict[str, Any] = field(default_factory=dict)
    site: Dict[str, Any] = field(default_factory=dict)
    device: Dict[str, Any] = field(default_factory=dict)
    target: Optional[Dict[str, Any]] = None
    tuning: Dict[str, Any] = field(default_factory=dict)
    products: List[Dict[str, Any]] = field(default_factory=list)
    result: Dict[str, Any] = field(default_factory=dict)
    evidence: str = "unverified"
    notes: List[str] = field(default_factory=list)
    #: 观测期间的事件序列（按 UTC 升序）。这是「一次观测 = 时间轴」的最小载体：
    #: 至少包含录制起止；解码命中/调谐变更等由上层按需追加。空表示没有事件。
    timeline: List[Dict[str, Any]] = field(default_factory=list)

    # -- 校验 ---------------------------------------------------------------
    def validate_for_export(self) -> List[str]:
        """返回阻塞导出的错误列表；空列表表示可导出。

        这里只做「导出是否成立」的判定，不检查字段是否完整——缺失的字段
        会在档案里如实留 ``None``，而不是阻止导出（一次只有频率没有站址的
        观测仍然是有价值的记录，前提是它真的产出了东西）。
        """
        errors: List[str] = []
        if not str(self.observation_id or "").strip():
            errors.append("缺少 observation_id")
        if self.evidence not in EVIDENCE_LEVELS:
            errors.append(
                f"证据强度 {self.evidence!r} 不在 {EVIDENCE_LEVELS} 内")
        if not self.products:
            errors.append("没有观测产物：无录音/解码输出，拒绝导出空档案")
        for i, p in enumerate(self.products):
            if not isinstance(p, dict) or not p.get("path"):
                errors.append(f"产物 #{i} 缺少 path")
        for i, e in enumerate(self.timeline or []):
            if not isinstance(e, dict):
                errors.append(f"时间轴事件 #{i} 不是对象")
                continue
            if not e.get("utc"):
                errors.append(f"时间轴事件 #{i} 缺少 utc")
            if not e.get("kind"):
                errors.append(f"时间轴事件 #{i} 缺少 kind")
        return errors

    # -- 序列化 -------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "observation_id": self.observation_id,
            "started_utc": self.started_utc,
            "ended_utc": self.ended_utc,
            "time": dict(self.time),
            "site": dict(self.site),
            "device": dict(self.device),
            "target": dict(self.target) if self.target else None,
            "tuning": dict(self.tuning),
            "products": [dict(p) for p in self.products],
            "result": dict(self.result),
            "evidence": self.evidence,
            "notes": list(self.notes),
            "timeline": normalize_timeline(self.timeline),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_csv_row(self) -> Dict[str, Any]:
        """CSV 单行。产物多于一个时用总数与总字节概括，明细留 JSON。"""
        total_bytes = 0
        for p in self.products:
            try:
                total_bytes += int(p.get("bytes") or 0)
            except (TypeError, ValueError):
                pass
        return {
            "observation_id": self.observation_id,
            "started_utc": self.started_utc,
            "ended_utc": self.ended_utc,
            "time_source": self.time.get("source"),
            "time_precision": self.time.get("precision"),
            "site_source": self.site.get("source"),
            "lat_deg": self.site.get("lat_deg"),
            "lon_deg": self.site.get("lon_deg"),
            "alt_m": self.site.get("alt_m"),
            "device_driver": self.device.get("driver"),
            "device_serial": self.device.get("serial"),
            "target_name": (self.target or {}).get("name"),
            "target_norad": (self.target or {}).get("norad_id"),
            "center_freq_hz": self.tuning.get("center_freq_hz"),
            "sample_rate_hz": self.tuning.get("sample_rate_hz"),
            "gain_db": self.tuning.get("gain_db"),
            "doppler_residual_hz": self.tuning.get("doppler_residual_hz"),
            "product_count": len(self.products),
            "product_bytes": total_bytes,
            "result_summary": self.result.get("summary"),
            "evidence": self.evidence,
        }

    # -- 落盘（原子写，与 iq_sigmf 同一范式）--------------------------------
    def write_json(self, path: str) -> Tuple[bool, Optional[str]]:
        errs = self.validate_for_export()
        if errs:
            return False, "；".join(errs)
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True, None
        except OSError as e:
            try:
                if os.path.exists(path + ".tmp"):
                    os.remove(path + ".tmp")
            except OSError:
                pass
            return False, f"写入失败：{type(e).__name__}: {e}"

    def write_csv(self, path: str) -> Tuple[bool, Optional[str]]:
        """写单行 CSV（含表头）。追加场景由调用方用 ``append_csv``。"""
        errs = self.validate_for_export()
        if errs:
            return False, "；".join(errs)
        import csv

        row = self.to_csv_row()
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                w.writeheader()
                w.writerow(row)
            os.replace(tmp, path)
            return True, None
        except OSError as e:
            try:
                if os.path.exists(path + ".tmp"):
                    os.remove(path + ".tmp")
            except OSError:
                pass
            return False, f"写入失败：{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# 构造入口
# ---------------------------------------------------------------------------

def record_from_snapshot(
    observation_id: str,
    snapshot: Dict[str, Any],
    *,
    started_utc: Optional[str] = None,
    ended_utc: Optional[str] = None,
    target: Optional[Dict[str, Any]] = None,
    tuning: Optional[Dict[str, Any]] = None,
    products: Optional[List[Dict[str, Any]]] = None,
    result: Optional[Dict[str, Any]] = None,
    evidence: str = "unverified",
    notes: Optional[List[str]] = None,
    timeline: Optional[List[Dict[str, Any]]] = None,
) -> ObservationRecord:
    """从 ``RuntimeSources.snapshot()`` 组装档案。

    只搬运快照里已有的字段；快照报 ``source=system`` 时时间不冒充 GNSS，
    站址 ``source=none`` 时经纬度保持 ``None`` 而不是 0.0。
    """
    snap = snapshot or {}
    t = snap.get("time") or {}
    s = snap.get("site") or {}
    sdr = snap.get("sdr") or {}
    # ``RuntimeSources`` 的 ``sdr.device`` 是**设备名字符串**（见
    # runtime_sources.py 的 ``info["device"] = str(device)``），不是字典。
    # 早期版本按 ``dev.get("name")`` 解析，一连真机就 AttributeError。
    dev_raw = sdr.get("device")
    if isinstance(dev_raw, dict):
        dev = dev_raw
        dev_name_hint = None
    else:
        dev = {}
        dev_name_hint = str(dev_raw) if dev_raw else None

    # 设备身份有两个真实来源：当前快照（设备在线时最准）与产物 SigMF meta
    # （录制那一刻写下的，设备断开后仍然有效）。快照为空时用产物回填，
    # 并在 notes 里写明来源——这不是猜，是两个真来源里的一个。
    prods = list(products or [])
    prod_hint = next(
        (p for p in prods if p.get("driver") or p.get("device")), None)
    snap_driver = dev.get("driver") or sdr.get("driver")
    snap_name = dev.get("name") or sdr.get("name") or dev_name_hint
    out_device = {
        "driver": snap_driver or (prod_hint or {}).get("driver"),
        "serial": dev.get("serial") or sdr.get("serial"),
        "name": snap_name or (prod_hint or {}).get("device"),
        "connected": sdr.get("connected"),
    }
    out_notes = list(notes or [])
    if prod_hint is not None:
        filled = []
        if not snap_driver and prod_hint.get("driver"):
            filled.append("driver")
        if not snap_name and prod_hint.get("device"):
            filled.append("name")
        if filled:
            out_notes.append(
                f"设备信息中的 {'/'.join(filled)} 取自产物 SigMF meta"
                "（导出时运行时快照无该字段）")

    return ObservationRecord(
        observation_id=str(observation_id),
        started_utc=started_utc,
        ended_utc=ended_utc,
        time={
            "source": t.get("source"),
            "time_source": t.get("time_source"),
            "precision": t.get("precision"),
            "following_system": t.get("following_system"),
            "last_update_utc": t.get("last_update_utc"),
        },
        site={
            "source": s.get("source"),
            "lat_deg": s.get("lat_deg"),
            "lon_deg": s.get("lon_deg"),
            "alt_m": s.get("alt_m"),
        },
        device=out_device,
        target=dict(target) if target else None,
        tuning=dict(tuning or {}),
        products=prods,
        result=dict(result or {}),
        # 非法证据强度**原样保留**，由 validate_for_export 拒绝导出。
        # 静默降级成 "unverified" 会让一份「来源不明」的档案装成合法档案，
        # 违反「不许静默」。
        evidence=evidence,
        notes=out_notes,
        timeline=[dict(e) for e in (timeline or [])],
    )


def product_from_sigmf(meta_path: str,
                       include_hash: bool = True) -> Dict[str, Any]:
    """从一份 SigMF meta 读出产物条目；读不出就如实标 ``error``。

    字段来源：``core:frequency`` / ``core:sample_rate`` /
    ``core:total_num_samples`` / ``core:datetime`` 与 ``mbdsdr:complete``
    （``recorder.py`` 写入口径），不另造一套名字。
    """
    out: Dict[str, Any] = {"path": meta_path, "kind": "sigmf-meta"}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError) as e:
        out["error"] = f"读取 meta 失败：{type(e).__name__}: {e}"
        return out

    g = meta.get("global") or {}
    caps = meta.get("captures") or [{}]
    out["center_freq_hz"] = g.get("core:frequency")
    out["sample_rate_hz"] = g.get("core:sample_rate")
    out["samples"] = g.get("core:total_num_samples")
    out["gain_db"] = g.get("mbdsdr:gain_db")
    out["device"] = g.get("mbdsdr:device")
    out["driver"] = g.get("mbdsdr:driver")
    out["started_utc"] = (caps[0] or {}).get("core:datetime") or None
    out["complete"] = bool(g.get("mbdsdr:complete"))
    if not out["complete"] and g.get("mbdsdr:incomplete_reason"):
        out["incomplete_reason"] = g.get("mbdsdr:incomplete_reason")

    data_path = g.get("core:dataset")
    if data_path:
        if not os.path.isabs(data_path):
            data_path = os.path.join(os.path.dirname(meta_path), data_path)
        out["data_path"] = data_path
        try:
            out["bytes"] = os.path.getsize(data_path)
        except OSError:
            out["bytes"] = None
        if include_hash:
            out["sha256"] = _sha256_file(data_path)
    return out


def load_record(path: str) -> Tuple[Optional["ObservationRecord"], Optional[str]]:
    """读回一份档案 JSON；schema 不匹配时如实拒绝，不半解析。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"读取失败：{type(e).__name__}: {e}"
    if not isinstance(data, dict):
        return None, "档案不是 JSON 对象"
    schema = data.get("schema")
    if schema != SCHEMA:
        return None, f"schema 不匹配：档案为 {schema!r}，期望 {SCHEMA!r}"
    return ObservationRecord(
        observation_id=str(data.get("observation_id") or ""),
        started_utc=data.get("started_utc"),
        ended_utc=data.get("ended_utc"),
        time=dict(data.get("time") or {}),
        site=dict(data.get("site") or {}),
        device=dict(data.get("device") or {}),
        target=data.get("target"),
        tuning=dict(data.get("tuning") or {}),
        products=list(data.get("products") or []),
        result=dict(data.get("result") or {}),
        evidence=str(data.get("evidence") or "unverified"),
        notes=list(data.get("notes") or []),
        timeline=list(data.get("timeline") or []),
    ), None


def replay_context(record: ObservationRecord) -> Dict[str, Any]:
    """提取「重演一次观测」所需的最小上下文（只读，不驱动任何硬件）。

    ``blockers`` 逐条列出缺什么；``can_replay`` 只有在没有任何阻塞项时为真。
    这是回放器的输入契约，不是回放器本身——真正的重演要由 UI/工具按这份
    上下文去设置时间、站址、调谐并载入 IQ，本函数不碰设备。
    """
    blockers: List[str] = []
    at_utc = record.started_utc
    if not at_utc:
        for e in record.timeline:
            if isinstance(e, dict) and e.get("utc"):
                at_utc = e["utc"]
                break
    if not at_utc:
        blockers.append("没有观测开始时刻（started_utc 与 timeline 均为空）")

    site_source = record.site.get("source")
    if site_source not in ("gnss", "manual"):
        blockers.append(f"没有可用站址（source={site_source}）")

    if record.tuning.get("center_freq_hz") is None:
        blockers.append("没有中心频率")

    data_paths: List[str] = []
    for p in record.products:
        if not isinstance(p, dict):
            continue
        dp = p.get("data_path")
        if not dp:
            # recorder.py 写的 meta 没有 core:dataset，按命名约定推导同名数据文件。
            mp = str(p.get("path") or "")
            if mp.endswith(".sigmf-meta"):
                dp = mp[: -len(".sigmf-meta")] + ".sigmf-data"
        if not dp:
            continue
        if os.path.exists(dp):
            data_paths.append(dp)
        else:
            # 明确区分「没有产物」与「产物登记了但文件不在了」——后者是可修的。
            blockers.append(f"数据文件不存在：{dp}")
    if not data_paths and not any("数据文件不存在" in b for b in blockers):
        blockers.append("没有可回放的 IQ 数据文件")

    return {
        "observation_id": record.observation_id,
        "at_utc": at_utc,
        "site": dict(record.site),
        "tuning": dict(record.tuning),
        "data_paths": data_paths,
        "timeline": [dict(e) for e in record.timeline],
        "evidence": record.evidence,
        "blockers": blockers,
        "can_replay": not blockers,
    }


# ---------------------------------------------------------------------------
# 时间轴 → 可执行步骤
# ---------------------------------------------------------------------------

def _parse_utc(value: Any) -> Optional[datetime]:
    """把 ISO8601 字符串解析成 UTC 时刻；解析不出返回 None（不猜）。"""
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
    return dt.astimezone(timezone.utc)


def _num(value: Any) -> Optional[float]:
    """数值化；``None``/布尔/不可解析都返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _count(value: Any) -> Optional[int]:
    """样本计数非负整数化；缺失或负值返回 None。"""
    n = _num(value)
    if n is None or n < 0:
        return None
    return int(n)


def replay_plan(record: ObservationRecord) -> Dict[str, Any]:
    """把档案时间轴展开成「在什么样本位置、机器应处于什么状态」的步骤表。

    纯函数：不打开 IQ、不碰设备。样本索引优先取事件自带的 ``samples``；
    缺失时用 ``(utc - 最早可用 utc) * sample_rate_hz`` 换算并标记
    ``index_source="wall"``；两者都没有就计入 ``skipped_events``，不猜。

    ``blockers`` 在 :func:`replay_context` 的基础上追加时间轴自身的阻塞项
    （缺采样率 / 事件越界 / 时间轴为空），只有为空时 ``can_replay`` 才为真。
    """
    ctx = replay_context(record)
    rate = _num(record.tuning.get("sample_rate_hz"))
    rate_ok = rate is not None and rate > 0

    total_samples: Optional[int] = None
    if record.products:
        counts = []
        for p in record.products:
            counts.append(_count(p.get("samples"))
                          if isinstance(p, dict) else None)
        # 任一产物缺样本数就不报总数：部分之和不是「总样本数」。
        if counts and all(c is not None for c in counts):
            total_samples = int(sum(counts))

    duration_s: Optional[float] = None
    if total_samples is not None and rate_ok:
        duration_s = total_samples / rate

    events = [dict(e) for e in (record.timeline or []) if isinstance(e, dict)]

    earliest: Optional[datetime] = None
    for e in events:
        dt = _parse_utc(e.get("utc"))
        if dt is not None and (earliest is None or dt < earliest):
            earliest = dt

    raw_steps: List[Dict[str, Any]] = []
    skipped_events = 0
    for e in events:
        kind = e.get("kind")
        kind = str(kind) if kind is not None else ""
        detail_raw = e.get("detail")
        detail = dict(detail_raw) if isinstance(detail_raw, dict) else {}

        sample = _count(e.get("samples"))
        index_source = "samples"
        if sample is None:
            dt = _parse_utc(e.get("utc"))
            if dt is not None and earliest is not None and rate_ok:
                sample = max(0, int(round(
                    (dt - earliest).total_seconds() * rate)))
                index_source = "wall"
            else:
                # 既无 samples 也无可用 utc（或没有采样率可换算）：跳过。
                skipped_events += 1
                continue

        utc = e.get("utc")
        raw_steps.append({
            "at_s": (sample / rate) if rate_ok else 0.0,
            "sample": int(sample),
            "utc": utc if isinstance(utc, str) else None,
            "kind": kind,
            "detail": detail,
            "index_source": index_source,
        })

    raw_steps.sort(key=lambda s: s["sample"])

    state = {
        "center_freq_hz": _num(record.tuning.get("center_freq_hz")),
        "sample_rate_hz": rate,
        "gain_db": _num(record.tuning.get("gain_db")),
    }
    steps: List[Dict[str, Any]] = []
    for s in raw_steps:
        if s["kind"] == "tune" and "to_hz" in s["detail"]:
            state["center_freq_hz"] = _num(s["detail"].get("to_hz"))
        elif s["kind"] == "gain" and "to_db" in s["detail"]:
            state["gain_db"] = _num(s["detail"].get("to_db"))
        elif s["kind"] == "sample_rate" and "to_hz" in s["detail"]:
            state["sample_rate_hz"] = _num(s["detail"].get("to_hz"))
        s["state"] = dict(state)
        steps.append(s)

    blockers: List[str] = list(ctx.get("blockers") or [])
    if not rate_ok:
        blockers.append("缺少采样率，无法把样本索引换算成时间")
    if total_samples is not None:
        for s in steps:
            if s["sample"] > total_samples:
                blockers.append(
                    "时间轴事件超出录制长度："
                    f"kind={s['kind']} sample={s['sample']} > {total_samples}")
    if not steps:
        blockers.append("时间轴为空，没有可重演的步骤")

    return {
        "observation_id": str(record.observation_id),
        "sample_rate_hz": rate,
        "total_samples": total_samples,
        "duration_s": duration_s,
        "steps": steps,
        "skipped_events": skipped_events,
        "blockers": blockers,
        "can_replay": not blockers,
        "data_paths": list(ctx.get("data_paths") or []),
    }

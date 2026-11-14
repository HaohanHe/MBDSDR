# SPDX-License-Identifier: MIT
"""environment_evidence.py — 环境层证据快照：落盘 + 同一时点只读复算。

为什么要这一层：环境输入（Kp/F10.7、实测气象、广播电离层参数）都是「当天
当时」的量，拿不到就会被当成可再取。证据快照把**输入 + 结论**一起钉在
``captured_at_utc`` 上，复算时以该时刻为参考时间（而不是「现在」），
因此同一份快照隔天再跑结果逐字段一致。

三条规矩：

  1. ``capture`` 只接受带 ``captured_at_utc`` 的快照；缺参考时刻直接报错，
     不拿当前时间冒充采集时刻。
  2. 输入缺项不是崩溃：对应层显式 ``uncorrected`` 并写进 ``status``，
     结论里写明缺什么；**绝不回填典型值**。
  3. ``replay`` 只吃快照，不联网、不重新取数；快照结构缺项即判失败，
     复算结果与记录不一致也判失败。

复算链路：``EnvironmentSnapshot`` → 空间天气收紧 → TLE 可信时长
（:mod:`mbdsdr_ai.tle_integrity` 的环境层）→ GNSS 误差预算
（:mod:`mbdsdr_ai.gnss_environment`）。
"""
from __future__ import annotations

import json
import math
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .environment_facts import (
    EnvironmentSnapshot,
    SpaceWeatherCorrectionTable,
    load_space_weather_table,
    parse_utc,
    space_weather_correction,
)
from .gnss_environment import GnssAtmosphere, IonosphereParams
from .tle_integrity import accuracy_with_environment, freshness_with_environment

__all__ = [
    "EVIDENCE_SCHEMA",
    "capture",
    "recompute",
    "save",
    "load",
    "replay",
    "diff_payloads",
]

EVIDENCE_SCHEMA = "mbdsdr.environment_evidence/1"

_REQUIRED_KEYS = ("schema", "captured_at_utc", "inputs", "result")


# ---------------------------------------------------------------------------
# 采集（纯计算，无 I/O）
# ---------------------------------------------------------------------------
def capture(snapshot: EnvironmentSnapshot,
            tle_line1: Optional[str] = None,
            ionosphere: Optional[IonosphereParams] = None,
            station: Optional[Mapping[str, float]] = None,
            satellites: Optional[Sequence[Mapping[str, float]]] = None,
            t_gpst_s: Optional[float] = None,
            table: Optional[SpaceWeatherCorrectionTable] = None
            ) -> Dict[str, Any]:
    """把一组环境输入与据此得到的结论固化成可回放证据。

    ``captured_at_utc`` 是唯一参考时刻：空间天气年龄、TLE 年龄、气象年龄
    都以它判定。缺该字段直接 ``ValueError``（结构性缺项，不许用「现在」
    冒充）。
    """
    ref_dt = parse_utc(snapshot.captured_at_utc)
    if ref_dt is None:
        raise ValueError("环境证据快照缺 captured_at_utc（或无法解析）："
                         "无法确定参考时刻，拒绝用当前时间冒充")
    ref = ref_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tbl = table if table is not None else load_space_weather_table()
    corr = space_weather_correction(snapshot, now=ref_dt, table=tbl)

    tle_result: Optional[Dict[str, Any]] = None
    if tle_line1:
        fresh = freshness_with_environment(
            tle_line1, now=ref_dt, environment=corr)
        tle_result = {
            "freshness": fresh,
            "accuracy": accuracy_with_environment(fresh),
        }

    gnss_result = _gnss_budget(snapshot, ref, ionosphere, station,
                               satellites, t_gpst_s)

    inputs = {
        "environment": snapshot.to_dict(),
        "ionosphere": ionosphere.to_dict() if ionosphere is not None else None,
        "station": dict(station) if station is not None else None,
        "satellites": [dict(s) for s in satellites] if satellites else None,
        "t_gpst_s": float(t_gpst_s) if t_gpst_s is not None else None,
        "tle_line1": tle_line1 or None,
        # 表系数随快照一起落盘：改表后旧快照仍按当时的表复算
        "correction_table": _table_payload(tbl),
    }
    status = _status(corr, tle_result, gnss_result)
    return {
        "schema": EVIDENCE_SCHEMA,
        "captured_at_utc": ref,
        "inputs": inputs,
        "result": {
            "correction": corr.to_dict(),
            "tle": tle_result,
            "gnss": gnss_result,
        },
        "status": status,
    }


def _table_payload(tbl: SpaceWeatherCorrectionTable) -> Dict[str, Any]:
    """修正表的完整内容（系数随快照固化，不引用磁盘当前版本）。"""
    return {
        "schema": tbl.schema,
        "model": tbl.model,
        "disclaimer": tbl.disclaimer,
        "combine": tbl.combine,
        "min_factor": tbl.min_factor,
        "path": tbl.path,
        "kp_bins": [b.to_dict() for b in tbl.kp_bins],
        "f107_bins": [b.to_dict() for b in tbl.f107_bins],
    }


def _gnss_budget(snapshot: EnvironmentSnapshot, ref: Optional[str],
                 ionosphere: Optional[IonosphereParams],
                 station: Optional[Mapping[str, float]],
                 satellites: Optional[Sequence[Mapping[str, float]]],
                 t_gpst_s: Optional[float]) -> Optional[Dict[str, Any]]:
    """逐星几何 + 站址齐全时才预算；缺项返回 None（由 status 明说）。"""
    if not satellites or station is None:
        return None
    atm = GnssAtmosphere(snapshot, ionosphere, now=ref)
    lat = math.radians(float(station["lat_deg"]))
    lon = math.radians(float(station["lon_deg"]))
    height = float(station["height_m"])
    iono_delays = []
    tropo_delays = []
    for sat in satellites:
        az = math.radians(float(sat["az_deg"]))
        el = math.radians(float(sat["el_deg"]))
        iono_delays.append(atm.iono_delay(lat, lon, az, el, t_gpst_s))
        tropo_delays.append(atm.tropo_delay(lat, height, el))
    return atm.summary(iono_delays, tropo_delays)


def _status(corr, tle_result: Optional[Dict[str, Any]],
            gnss_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """逐层列出「哪些结论是未校正、为什么」——不许静默。"""
    uncorrected: List[str] = []
    if corr.status != "corrected":
        uncorrected.append(f"space_weather: {corr.note}")
    if tle_result is None:
        uncorrected.append("tle: 未提供 TLE line1，无法判定可信时长")
    else:
        env = tle_result["freshness"]["environment"]
        if env.get("status") != "corrected":
            uncorrected.append(f"tle: {env.get('note', '环境层未校正')}")
    if gnss_result is None:
        uncorrected.append("gnss: 未提供站址或逐星几何，无法给出误差预算")
    elif gnss_result.get("status") != "corrected":
        uncorrected.append(f"gnss: {gnss_result.get('note', '')}")
    return {
        "complete": not uncorrected,
        "uncorrected": uncorrected,
    }


# ---------------------------------------------------------------------------
# 落盘 / 读取
# ---------------------------------------------------------------------------
def save(path: Any, evidence: Mapping[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(evidence, ensure_ascii=False, indent=2,
                            sort_keys=True), encoding="utf-8")
    return p


def load(path: Any) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("环境证据根节点必须是对象")
    return payload


# ---------------------------------------------------------------------------
# 只读复算对拍
# ---------------------------------------------------------------------------
def recompute(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """只吃快照里的输入，以 ``captured_at_utc`` 为参考时刻重算一遍。"""
    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("证据快照缺 inputs")
    env = inputs.get("environment")
    if not isinstance(env, Mapping):
        raise ValueError("证据快照缺 inputs.environment")
    snapshot = EnvironmentSnapshot.from_dict(env)
    iono_payload = inputs.get("ionosphere")
    ionosphere = (IonosphereParams.from_dict(iono_payload)
                  if isinstance(iono_payload, Mapping) else None)
    table_payload = inputs.get("correction_table")
    table = (SpaceWeatherCorrectionTable.from_dict(
        table_payload, path=str(table_payload.get("path") or ""))
        if isinstance(table_payload, Mapping) else None)
    return capture(
        snapshot,
        tle_line1=inputs.get("tle_line1"),
        ionosphere=ionosphere,
        station=inputs.get("station"),
        satellites=inputs.get("satellites"),
        t_gpst_s=inputs.get("t_gpst_s"),
        table=table,
    )


def diff_payloads(actual: Any, expected: Any, where: str = "",
                  tol: float = 1e-9) -> List[str]:
    """递归对拍（数字按极小容差；JSON 往返不改变浮点值）。"""
    problems: List[str] = []
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        for key in sorted(set(actual) | set(expected)):
            path = f"{where}.{key}" if where else str(key)
            if key not in actual:
                problems.append(f"{path}: 记录有、复算无")
            elif key not in expected:
                problems.append(f"{path}: 复算有、记录无")
            else:
                problems.extend(diff_payloads(actual[key], expected[key],
                                              path, tol))
        return problems
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            problems.append(f"{where}: 条数 {len(actual)} != {len(expected)}")
        for i, (a, e) in enumerate(zip(actual, expected)):
            problems.extend(diff_payloads(a, e, f"{where}[{i}]", tol))
        return problems
    if isinstance(actual, bool) or isinstance(expected, bool):
        if actual != expected:
            problems.append(f"{where}: {actual!r} != {expected!r}")
        return problems
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        if not math.isclose(float(actual), float(expected),
                            rel_tol=0.0, abs_tol=tol):
            problems.append(f"{where}: {actual!r} != {expected!r}")
        return problems
    if actual != expected:
        problems.append(f"{where}: {actual!r} != {expected!r}")
    return problems


def replay(payload: Mapping[str, Any]) -> Tuple[List[str], str]:
    """返回 ``(问题列表, 摘要)``；问题非空即判失败（缺项/被改动）。"""
    problems: List[str] = []
    if not isinstance(payload, Mapping):
        return ["根节点必须是对象"], "快照不可读"
    for key in _REQUIRED_KEYS:
        if key not in payload:
            problems.append(f"缺必需字段 {key}")
    if problems:
        return problems, "快照缺项，拒绝复算（缺项即红）"
    if payload["schema"] != EVIDENCE_SCHEMA:
        problems.append(
            f"schema={payload['schema']!r} != {EVIDENCE_SCHEMA!r}")
    inputs = payload.get("inputs") or {}
    env = (inputs.get("environment") or {}) \
        if isinstance(inputs, Mapping) else {}
    if env.get("captured_at_utc") != payload["captured_at_utc"]:
        problems.append("captured_at_utc 与 inputs.environment 不一致")
    if problems:
        return problems, "快照自相矛盾，拒绝复算"

    try:
        recomputed = recompute(payload)
    except (ValueError, KeyError, TypeError) as exc:
        return [f"复算失败：{exc}"], "快照输入不可用"

    problems.extend(diff_payloads(recomputed.get("result"),
                                  payload.get("result"), "result"))
    problems.extend(diff_payloads(recomputed.get("status"),
                                  payload.get("status"), "status"))
    problems.extend(diff_payloads(recomputed.get("captured_at_utc"),
                                  payload.get("captured_at_utc"),
                                  "captured_at_utc"))
    status = payload.get("status") or {}
    unc = status.get("uncorrected") or []
    summary = (f"参考时刻 {payload['captured_at_utc']}；"
               f"未校正层 {len(unc)} 个；对拍字段见 problems")
    return problems, summary

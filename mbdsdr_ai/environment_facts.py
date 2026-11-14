# SPDX-License-Identifier: MIT
"""environment_facts.py — 「当天实际环境条件」事实层

定位：给时空轴补一层**可注入、可回放、缺数据即 unknown** 的环境输入，
供 GNSS 误差预算与 TLE 可信时长使用。本模块只做事实与修正系数，不碰
定位/轨道算法本身。

三条硬规矩（对应验收判据）：

  1. 每条事实必须同时具备 ``value`` + ``at_utc`` + ``source`` 才算已知；
     缺任一项、时刻无法解析、超过自身 ``max_age_s``，一律 ``unknown`` /
     ``stale``，**绝不拿典型值冒充当天值**。
  2. 空间天气修正系数只存在于数据文件
     ``data/space_weather_correction.json``，代码里不写死任何档位系数；
     Kp 档与 F10.7 档各自携带 ``source`` 与 ``applicability``。
  3. 修正是**经验模型，非实测密度**；factor 恒在 (0, 1]，只收紧、不放宽。
     缺任一环境输入时回落到未校正基线（factor=1.0）并显式标注，不按
     最坏值猜。

快照：:class:`EnvironmentSnapshot` 可 ``save``/``load`` 成 JSON，同一
``captured_at`` 下能独立重算，作为 D4 可回放证据的基础。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

__all__ = [
    "FACT_KP_INDEX",
    "FACT_F107",
    "FACT_PRESSURE",
    "FACT_TEMPERATURE",
    "FACT_HUMIDITY",
    "STATUS_KNOWN",
    "STATUS_UNKNOWN",
    "STATUS_STALE",
    "ENV_SNAPSHOT_SCHEMA",
    "SPACE_WEATHER_SCHEMA",
    "DEFAULT_TABLE_PATH",
    "EnvironmentFact",
    "EnvironmentSnapshot",
    "CorrectionBin",
    "SpaceWeatherCorrectionTable",
    "EnvironmentCorrection",
    "CorrectionTableError",
    "parse_utc",
    "load_space_weather_table",
    "space_weather_correction",
]

# ---------------------------------------------------------------------------
# 事实名（跨模块共用同一组字符串，避免各处另拼）
# ---------------------------------------------------------------------------
FACT_KP_INDEX = "kp_index"
FACT_F107 = "f107_sfu"
FACT_PRESSURE = "pressure_hpa"
FACT_TEMPERATURE = "temperature_c"
FACT_HUMIDITY = "humidity_pct"

STATUS_KNOWN = "known"
STATUS_UNKNOWN = "unknown"
STATUS_STALE = "stale"

ENV_SNAPSHOT_SCHEMA = "mbdsdr.environment_snapshot/1"
SPACE_WEATHER_SCHEMA = "mbdsdr.space_weather_correction/1"

DEFAULT_TABLE_PATH = (
    Path(__file__).resolve().parent / "data" / "space_weather_correction.json")


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------
def parse_utc(value: Any) -> Optional[datetime]:
    """把 ISO8601 / datetime / Unix 秒解析为带 UTC 时区的 datetime。

    无法解析返回 ``None``（调用方按 unknown 处理，不猜时刻）。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _as_utc(value: Any) -> datetime:
    """参考时刻归一化；缺省取当前 UTC（仅用于「现在是否过期」判定）。"""
    dt = parse_utc(value)
    return dt if dt is not None else datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 单条环境事实
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EnvironmentFact:
    """一条环境输入：值 + 采集时刻 + 来源。

    ``max_age_s`` 为该类数据的最大可接受年龄（如 Kp 6 小时、F10.7 48 小时）；
    ``None`` 表示不按年龄判过期，但 ``value``/``at_utc``/``source`` 仍必须齐全。
    """

    name: str
    value: Optional[float] = None
    unit: str = ""
    at_utc: Optional[str] = None
    source: str = ""
    max_age_s: Optional[float] = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.value is not None:
            try:
                v = float(self.value)
            except (TypeError, ValueError):
                raise ValueError(f"环境事实 {self.name} 的 value 非数值: {self.value!r}")
            if not math.isfinite(v):
                raise ValueError(f"环境事实 {self.name} 的 value 非有限数: {self.value!r}")
            object.__setattr__(self, "value", v)

    # ---- 状态判定 -------------------------------------------------------
    def at(self) -> Optional[datetime]:
        return parse_utc(self.at_utc)

    def age_s(self, now: Any = None) -> Optional[float]:
        at = self.at()
        if at is None:
            return None
        return (_as_utc(now) - at).total_seconds()

    def status(self, now: Any = None) -> str:
        if self.value is None:
            return STATUS_UNKNOWN
        if self.at() is None:
            return STATUS_UNKNOWN
        if not str(self.source or "").strip():
            return STATUS_UNKNOWN
        age = self.age_s(now)
        if self.max_age_s is not None and age is not None \
                and age > float(self.max_age_s):
            return STATUS_STALE
        return STATUS_KNOWN

    def usable(self, now: Any = None) -> bool:
        return self.status(now) == STATUS_KNOWN

    def detail(self, now: Any = None) -> str:
        """为什么可用/不可用——结果里要能说明「依据哪一刻的什么数据」。"""
        status = self.status(now)
        if status == STATUS_UNKNOWN:
            if self.value is None:
                return f"{self.name}: 缺测量值（未采集到）"
            if self.at() is None:
                return f"{self.name}: 缺/坏采集时刻 at_utc（无法说明是何时数据）"
            return f"{self.name}: 缺来源 source（无法追溯数据出处）"
        age = self.age_s(now)
        if status == STATUS_STALE:
            return (f"{self.name}: 数据过期（采集于 {self.at_utc}，"
                    f"年龄 {age / 3600.0:.1f} h > 上限 {self.max_age_s / 3600.0:.1f} h）")
        return (f"{self.name}: {self.value} {self.unit}".strip()
                + f"，采集于 {self.at_utc}，来源 {self.source}")

    def state(self, now: Any = None) -> Dict[str, Any]:
        """快照/结果字段用的完整描述。"""
        return {
            "name": self.name,
            "status": self.status(now),
            "value": self.value,
            "unit": self.unit,
            "at_utc": self.at_utc,
            "source": self.source,
            "max_age_s": self.max_age_s,
            "age_s": None if self.age_s(now) is None else round(self.age_s(now), 1),
            "detail": self.detail(now),
            "note": self.note,
        }

    # ---- 序列化 ---------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "at_utc": self.at_utc,
            "source": self.source,
            "max_age_s": self.max_age_s,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EnvironmentFact":
        return cls(
            name=str(payload.get("name") or ""),
            value=payload.get("value"),
            unit=str(payload.get("unit") or ""),
            at_utc=payload.get("at_utc"),
            source=str(payload.get("source") or ""),
            max_age_s=payload.get("max_age_s"),
            note=str(payload.get("note") or ""),
        )


# ---------------------------------------------------------------------------
# 快照（可落盘、可回放）
# ---------------------------------------------------------------------------
@dataclass
class EnvironmentSnapshot:
    """一组环境事实 + 捕获时刻。``save``/``load`` 保证同一时点可重算。"""

    facts: Dict[str, EnvironmentFact] = field(default_factory=dict)
    captured_at_utc: Optional[str] = None
    schema: str = ENV_SNAPSHOT_SCHEMA

    def add(self, fact: EnvironmentFact) -> "EnvironmentSnapshot":
        self.facts[fact.name] = fact
        return self

    def get(self, name: str) -> Optional[EnvironmentFact]:
        return self.facts.get(str(name))

    def value(self, name: str, now: Any = None) -> Optional[float]:
        """只在事实可用（known）时返回值，否则 ``None``——不静默降级。"""
        fact = self.get(name)
        return fact.value if fact is not None and fact.usable(now) else None

    def states(self, names: Optional[Iterable[str]] = None,
               now: Any = None) -> Dict[str, Dict[str, Any]]:
        keys = list(names) if names is not None else sorted(self.facts)
        return {
            key: (self.facts[key].state(now) if key in self.facts else {
                "name": key, "status": STATUS_UNKNOWN, "value": None,
                "unit": "", "at_utc": None, "source": "",
                "max_age_s": None, "age_s": None,
                "detail": f"{key}: 快照中缺该事实", "note": "",
            })
            for key in keys
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "captured_at_utc": self.captured_at_utc,
            "facts": {name: fact.to_dict()
                      for name, fact in sorted(self.facts.items())},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EnvironmentSnapshot":
        facts: Dict[str, EnvironmentFact] = {}
        raw = payload.get("facts") or {}
        if not isinstance(raw, Mapping):
            raise ValueError("环境快照 facts 必须是对象")
        for name, item in raw.items():
            if not isinstance(item, Mapping):
                raise ValueError(f"环境快照事实 {name} 必须是对象")
            fact = EnvironmentFact.from_dict({**item, "name": item.get("name", name)})
            facts[fact.name or str(name)] = fact
        return cls(
            facts=facts,
            captured_at_utc=payload.get("captured_at_utc"),
            schema=str(payload.get("schema") or ENV_SNAPSHOT_SCHEMA),
        )

    @classmethod
    def capture(cls, facts: Iterable[EnvironmentFact],
                captured_at: Any = None) -> "EnvironmentSnapshot":
        snap = cls(captured_at_utc=_iso(_as_utc(captured_at)))
        for fact in facts:
            snap.add(fact)
        return snap

    @classmethod
    def from_space_weather(cls, payload: Mapping[str, Any]
                           ) -> "EnvironmentSnapshot":
        """桥接 :mod:`mbdsdr_ai.space_weather` 的采集快照。

        只取 ``kp`` / ``f107`` 两项；采集失败（``value=None``）也保留成
        unknown 事实并带原 ``error``，让下游能说明「取不到」而不是静默
        变成「没这条」。
        """
        samples = payload.get("samples") or {}
        if not isinstance(samples, Mapping):
            raise ValueError("空间天气快照 samples 必须是对象")
        limits = {FACT_KP_INDEX: 6 * 3600.0, FACT_F107: 48 * 3600.0}
        units = {FACT_KP_INDEX: "Kp", FACT_F107: "sfu"}
        key_map = (("kp", FACT_KP_INDEX), ("f107", FACT_F107))
        facts: list[EnvironmentFact] = []
        for raw_key, name in key_map:
            item = samples.get(raw_key)
            if not isinstance(item, Mapping):
                continue
            facts.append(EnvironmentFact(
                name=name,
                value=item.get("value"),
                unit=str(item.get("unit") or units[name]),
                at_utc=item.get("observed_at") or None,
                source=str(item.get("source") or item.get("url") or ""),
                max_age_s=limits[name],
                note=str(item.get("error") or ""),
            ))
        return cls.capture(facts, captured_at=payload.get("taken_at"))

    def save(self, path: Any) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: Any) -> "EnvironmentSnapshot":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("环境快照根节点必须是对象")
        return cls.from_dict(payload)


# ---------------------------------------------------------------------------
# 经验修正表（数据文件驱动）
# ---------------------------------------------------------------------------
class CorrectionTableError(ValueError):
    """修正表结构/取值违约（缺来源、factor>1、档位不递增等）。"""


@dataclass(frozen=True)
class CorrectionBin:
    """一个环境档位：上限（含）+ 收紧系数 + 来源 + 适用条件。"""

    max_value: float
    factor: float
    source: str
    applicability: str
    label: str = ""

    def covers(self, value: float) -> bool:
        return float(value) <= float(self.max_value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_value": self.max_value,
            "factor": self.factor,
            "label": self.label,
            "source": self.source,
            "applicability": self.applicability,
        }


@dataclass(frozen=True)
class SpaceWeatherCorrectionTable:
    """Kp/F10.7 分档收紧表。只收紧：每档 factor ∈ (0, 1]。"""

    schema: str
    model: str
    disclaimer: str
    kp_bins: Tuple[CorrectionBin, ...]
    f107_bins: Tuple[CorrectionBin, ...]
    combine: str = "multiply"
    min_factor: float = 0.05
    path: str = ""

    # ---- 校验与构造 -----------------------------------------------------
    @classmethod
    def from_dict(cls, payload: Mapping[str, Any],
                  path: str = "") -> "SpaceWeatherCorrectionTable":
        if not isinstance(payload, Mapping):
            raise CorrectionTableError("修正表根节点必须是对象")
        schema = str(payload.get("schema") or "")
        if schema != SPACE_WEATHER_SCHEMA:
            raise CorrectionTableError(
                f"修正表 schema 必须是 {SPACE_WEATHER_SCHEMA}，实际 {schema!r}")
        model = str(payload.get("model") or "")
        if model != "empirical":
            raise CorrectionTableError(
                f"修正表 model 必须是 empirical（经验模型），实际 {model!r}")
        disclaimer = str(payload.get("disclaimer") or "").strip()
        if not disclaimer:
            raise CorrectionTableError("修正表必须写明 disclaimer（经验模型，非实测密度）")
        combine = str(payload.get("combine") or "multiply")
        if combine != "multiply":
            raise CorrectionTableError(f"不支持的 combine 策略: {combine!r}")
        max_factor_floor = float(payload.get("min_factor", 0.05))
        if not 0.0 < max_factor_floor <= 1.0:
            raise CorrectionTableError("min_factor 必须落在 (0, 1]")
        kp_bins = cls._parse_bins(payload.get("kp_bins"), "kp_bins")
        f107_bins = cls._parse_bins(payload.get("f107_bins"), "f107_bins")
        return cls(
            schema=schema, model=model, disclaimer=disclaimer,
            kp_bins=kp_bins, f107_bins=f107_bins, combine=combine,
            min_factor=max_factor_floor, path=path,
        )

    @staticmethod
    def _parse_bins(raw: Any, name: str) -> Tuple[CorrectionBin, ...]:
        if not isinstance(raw, list) or not raw:
            raise CorrectionTableError(f"{name} 必须是非空数组")
        bins: list[CorrectionBin] = []
        last_max = -math.inf
        for i, item in enumerate(raw):
            if not isinstance(item, Mapping):
                raise CorrectionTableError(f"{name}[{i}] 必须是对象")
            try:
                max_value = float(item["max_value"])
                factor = float(item["factor"])
            except (KeyError, TypeError, ValueError):
                raise CorrectionTableError(
                    f"{name}[{i}] 缺 max_value/factor 或非数值")
            if not math.isfinite(max_value) or not math.isfinite(factor):
                raise CorrectionTableError(f"{name}[{i}] 含非有限数")
            if max_value <= last_max:
                raise CorrectionTableError(f"{name} 的 max_value 必须严格递增")
            # 只收紧、不放宽：系数越界直接拒绝，而不是运行期静默钳位。
            if not 0.0 < factor <= 1.0:
                raise CorrectionTableError(
                    f"{name}[{i}] factor={factor} 越界：只允许 (0, 1]（只收紧不放宽）")
            source = str(item.get("source") or "").strip()
            applicability = str(item.get("applicability") or "").strip()
            if not source:
                raise CorrectionTableError(f"{name}[{i}] 缺 source（档位依据不可追溯）")
            if not applicability:
                raise CorrectionTableError(f"{name}[{i}] 缺 applicability（适用条件）")
            bins.append(CorrectionBin(
                max_value=max_value, factor=factor, source=source,
                applicability=applicability,
                label=str(item.get("label") or ""),
            ))
            last_max = max_value
        return tuple(bins)

    @classmethod
    def load(cls, path: Any = None) -> "SpaceWeatherCorrectionTable":
        p = Path(path) if path is not None else DEFAULT_TABLE_PATH
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CorrectionTableError(f"修正表读取失败: {p}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise CorrectionTableError(f"修正表不是合法 JSON: {p}: {exc}") from exc
        return cls.from_dict(payload, path=str(p))

    # ---- 查档 -----------------------------------------------------------
    @staticmethod
    def _bin_for(bins: Tuple[CorrectionBin, ...], value: float
                 ) -> Optional[CorrectionBin]:
        for item in bins:
            if item.covers(value):
                return item
        return None

    def bin_for_kp(self, kp: float) -> Optional[CorrectionBin]:
        return self._bin_for(self.kp_bins, float(kp))

    def bin_for_f107(self, f107: float) -> Optional[CorrectionBin]:
        return self._bin_for(self.f107_bins, float(f107))

    # ---- 主入口 ---------------------------------------------------------
    def correction(self, kp_fact: Optional[EnvironmentFact],
                   f107_fact: Optional[EnvironmentFact],
                   now: Any = None) -> "EnvironmentCorrection":
        """由两条事实得到收紧结论。

        任一输入不可用（缺值/缺时刻/缺来源/过期）→ ``uncorrected``，
        factor=1.0，并列出缺失原因；超出表适用范围同样回落，不按最坏档猜。
        """
        missing: list[str] = []
        inputs: Dict[str, Any] = {}
        for key, fact in ((FACT_KP_INDEX, kp_fact), (FACT_F107, f107_fact)):
            if fact is None:
                missing.append(f"{key}: 未提供")
                inputs[key] = None
            elif not fact.usable(now):
                missing.append(fact.detail(now))
                inputs[key] = fact.state(now)
            else:
                inputs[key] = fact.state(now)

        if missing:
            return EnvironmentCorrection(
                status="uncorrected", factor=1.0, kp=None, f107=None,
                missing=tuple(missing),
                note=("缺环境输入，回落未校正基线（factor=1.0）；不按最坏值猜。"
                      + "；".join(missing)),
                disclaimer=self.disclaimer, model=self.model,
                table_path=self.path, inputs=inputs,
            )

        kp_bin = self.bin_for_kp(float(kp_fact.value))
        f107_bin = self.bin_for_f107(float(f107_fact.value))
        if kp_bin is None or f107_bin is None:
            out_of_range = []
            if kp_bin is None:
                out_of_range.append(f"Kp={kp_fact.value} 超出表内档位")
            if f107_bin is None:
                out_of_range.append(f"F10.7={f107_fact.value} 超出表内档位")
            return EnvironmentCorrection(
                status="uncorrected", factor=1.0, kp=None, f107=None,
                missing=tuple(out_of_range),
                note=("环境输入超出修正表适用范围，回落未校正基线"
                      "（不按最坏档外推）：" + "；".join(out_of_range)),
                disclaimer=self.disclaimer, model=self.model,
                table_path=self.path, inputs=inputs,
            )

        factor = min(1.0, float(kp_bin.factor) * float(f107_bin.factor))
        factor = max(float(self.min_factor), factor)
        kp_payload = {**inputs[FACT_KP_INDEX], **kp_bin.to_dict()}
        f107_payload = {**inputs[FACT_F107], **f107_bin.to_dict()}
        return EnvironmentCorrection(
            status="corrected", factor=factor, kp=kp_payload, f107=f107_payload,
            missing=(),
            note=(f"{self.disclaimer}；Kp={kp_fact.value}（{kp_bin.label or '档位'}）"
                  f"× F10.7={f107_fact.value} sfu（{f107_bin.label or '档位'}）"
                  f" → 收紧系数 {factor:.3f}"),
            disclaimer=self.disclaimer, model=self.model,
            table_path=self.path, inputs=inputs,
        )


# ---------------------------------------------------------------------------
# 修正结论
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EnvironmentCorrection:
    """一次环境修正的结论。``factor`` 恒 ≤ 1，只收紧。"""

    status: str
    factor: float
    kp: Optional[Dict[str, Any]]
    f107: Optional[Dict[str, Any]]
    missing: Tuple[str, ...]
    note: str
    disclaimer: str = "经验模型，非实测密度"
    model: str = "empirical"
    table_path: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        return self.status == "corrected"

    def tighten_seconds(self, base_seconds: float) -> float:
        """按系数收紧时长；``factor`` 再兜一层 min(1)，任何输入不放宽。"""
        base = float(base_seconds)
        if base < 0.0:
            raise ValueError("base_seconds 不能为负")
        return base * min(1.0, float(self.factor))

    def tighten_hours(self, base_hours: float) -> float:
        return self.tighten_seconds(float(base_hours) * 3600.0) / 3600.0

    def effective_thresholds(self, base: Mapping[str, float]) -> Dict[str, float]:
        """把基线阈值整体收紧（键不变，值只小不大）。"""
        return {str(k): self.tighten_hours(float(v)) for k, v in base.items()}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "applied": self.applied,
            "factor": self.factor,
            "model": self.model,
            "disclaimer": self.disclaimer,
            "note": self.note,
            "missing": list(self.missing),
            "kp": self.kp,
            "f107": self.f107,
            "inputs": self.inputs,
            "table_path": self.table_path,
        }


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------
def load_space_weather_table(path: Any = None) -> SpaceWeatherCorrectionTable:
    """加载修正表。没有隐式缓存：测试改表后必须能看到新值。"""
    return SpaceWeatherCorrectionTable.load(path)


def space_weather_correction(snapshot: EnvironmentSnapshot, now: Any = None,
                             table: Optional[SpaceWeatherCorrectionTable] = None
                             ) -> EnvironmentCorrection:
    """从环境快照取 Kp/F10.7 并给出收紧结论（缺数据即 uncorrected）。"""
    tbl = table if table is not None else load_space_weather_table()
    return tbl.correction(
        snapshot.get(FACT_KP_INDEX), snapshot.get(FACT_F107), now=now)

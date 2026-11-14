# SPDX-License-Identifier: MIT
"""gnss_environment.py — GNSS 误差预算：当天环境条件下的电离层/对流层改正

两层输入，各有采集时刻与来源：

  * 电离层：GPS 广播 Klobuchar 参数（alpha/beta），由 :class:`IonosphereParams`
    携带 ``at_utc`` 与 ``source``；缺参数/过期即 uncorrected（A1 的延续）。
  * 对流层：本地实测气压/气温/湿度（三件套），由 :class:`EnvironmentSnapshot`
    携带各自的 ``at_utc``/``source``；缺任一项即 uncorrected。

结果里 ``error_budget`` 逐项写明：用了什么模型、依据哪一刻的什么数据、
逐星延迟均值/最大值、以及经验剩余不确定度。缺项时 ``residual_sigma_m``
为 ``None``——不把「没算」写成 0。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .environment_facts import (
    FACT_HUMIDITY,
    FACT_PRESSURE,
    FACT_TEMPERATURE,
    STATUS_KNOWN,
    STATUS_STALE,
    STATUS_UNKNOWN,
    EnvironmentFact,
    EnvironmentSnapshot,
    parse_utc,
)

__all__ = [
    "IONO_RESIDUAL_FRACTION",
    "TROPO_RESIDUAL_FRACTION",
    "IonosphereParams",
    "GnssAtmosphere",
    "saastamoinen_delay_m",
    "uncorrected_error_budget",
]

#: Klobuchar 广播模型相对误差的经验保守比例（非实测不确定度）
IONO_RESIDUAL_FRACTION = 0.30
#: 实测气象 Saastamoinen 的湿项/映射残差经验比例（非实测不确定度）
TROPO_RESIDUAL_FRACTION = 0.05

_IONO_MODEL = "klobuchar_broadcast"
_TROPO_MODEL = "saastamoinen_measured_weather"


# ---------------------------------------------------------------------------
# 电离层广播参数（8 个数 + 采集时刻 + 来源）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IonosphereParams:
    """GPS 广播电离层参数 alpha0..3 / beta0..3。

    ``at_utc`` 与 ``source`` 必须同时给出才算 known；缺任一项或超过
    ``max_age_s`` 即 unknown/stale，由 :class:`GnssAtmosphere` 标 uncorrected。
    """

    alpha: Tuple[float, float, float, float]
    beta: Tuple[float, float, float, float]
    at_utc: Optional[str] = None
    source: str = ""
    max_age_s: Optional[float] = 24 * 3600.0

    def __post_init__(self) -> None:
        for name, seq in (("alpha", self.alpha), ("beta", self.beta)):
            if len(seq) != 4:
                raise ValueError(f"电离层 {name} 必须是 4 个数，收到 {len(seq)}")
            if not all(math.isfinite(float(v)) for v in seq):
                raise ValueError(f"电离层 {name} 含非有限数")
            object.__setattr__(self, name, tuple(float(v) for v in seq))

    @property
    def params(self) -> List[float]:
        return list(self.alpha) + list(self.beta)

    def at(self):
        return parse_utc(self.at_utc)

    def age_s(self, now: Any = None) -> Optional[float]:
        at = self.at()
        if at is None:
            return None
        dt = parse_utc(now)
        if dt is None:
            from datetime import datetime, timezone
            dt = datetime.now(timezone.utc)
        return (dt - at).total_seconds()

    def status(self, now: Any = None) -> str:
        if self.at() is None or not str(self.source or "").strip():
            return STATUS_UNKNOWN
        age = self.age_s(now)
        if self.max_age_s is not None and age is not None \
                and age > float(self.max_age_s):
            return STATUS_STALE
        return STATUS_KNOWN

    def detail(self, now: Any = None) -> str:
        status = self.status(now)
        if status == STATUS_UNKNOWN:
            if self.at() is None:
                return "电离层: 缺广播参数采集时刻 at_utc"
            return "电离层: 缺广播参数来源 source"
        if status == STATUS_STALE:
            return (f"电离层: 广播参数过期（采集于 {self.at_utc}，"
                    f"年龄 {self.age_s(now) / 3600.0:.1f} h > "
                    f"{self.max_age_s / 3600.0:.1f} h）")
        return f"电离层: 广播参数采集于 {self.at_utc}，来源 {self.source}"

    def state(self, now: Any = None) -> Dict[str, Any]:
        return {
            "status": self.status(now),
            "alpha": list(self.alpha),
            "beta": list(self.beta),
            "at_utc": self.at_utc,
            "source": self.source,
            "max_age_s": self.max_age_s,
            "detail": self.detail(now),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alpha": list(self.alpha),
            "beta": list(self.beta),
            "at_utc": self.at_utc,
            "source": self.source,
            "max_age_s": self.max_age_s,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IonosphereParams":
        alpha = payload.get("alpha")
        beta = payload.get("beta")
        if not isinstance(alpha, (list, tuple)) or not isinstance(beta, (list, tuple)):
            raise ValueError("电离层参数 alpha/beta 必须是长度为 4 的数组")
        return cls(
            alpha=tuple(alpha), beta=tuple(beta),
            at_utc=payload.get("at_utc"),
            source=str(payload.get("source") or ""),
            max_age_s=payload.get("max_age_s", 24 * 3600.0),
        )


# ---------------------------------------------------------------------------
# 对流层延迟（实测气象）
# ---------------------------------------------------------------------------
def saastamoinen_delay_m(pressure_hpa: float, temperature_c: float,
                         humidity_pct: float, lat_rad: float,
                         height_m: float, el_rad: float) -> Optional[float]:
    """Saastamoinen 对流层延迟（m）。

    输入必须是**当天实测**气压/气温/相对湿度；任一项非有限或湿度越界
    返回 ``None``（调用方按 uncorrected 处理，不代填标准大气）。
    简化映射函数：``1 / (sin el + 0.00143 / (tan el + 0.0445))``。
    """
    vals = (pressure_hpa, temperature_c, humidity_pct, lat_rad, height_m, el_rad)
    if not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in vals):
        return None
    p = float(pressure_hpa)
    tc = float(temperature_c)
    rh = float(humidity_pct)
    if p <= 0.0 or not 0.0 <= rh <= 100.0:
        return None
    t_k = tc + 273.15
    if t_k <= 0.0:
        return None
    if el_rad <= 0.0:
        return 0.0

    # 水汽分压 e（hPa），Magnus 公式
    e_hpa = (rh / 100.0) * 6.112 * math.exp(17.67 * tc / (tc + 243.04))
    h_km = max(float(height_m), 0.0) / 1000.0
    zhd = 0.0022768 * p / (1.0 - 0.00266 * math.cos(2.0 * float(lat_rad))
                         - 0.00028 * h_km)
    zwd = 0.002277 * (1255.0 / t_k + 0.05) * e_hpa
    sin_el = math.sin(float(el_rad))
    tan_el = math.tan(float(el_rad))
    mapping = 1.0 / (sin_el + 0.00143 / (tan_el + 0.0445))
    return float((zhd + zwd) * mapping)


# ---------------------------------------------------------------------------
# 组合环境：电离层 + 对流层
# ---------------------------------------------------------------------------
class GnssAtmosphere:
    """一次解算所用的环境上下文（可注入、可随结果回放）。"""

    def __init__(self, snapshot: Optional[EnvironmentSnapshot] = None,
                 ionosphere: Optional[IonosphereParams] = None,
                 now: Any = None) -> None:
        self.snapshot = snapshot if snapshot is not None else EnvironmentSnapshot()
        self.ionosphere = ionosphere
        # 回放时以快照捕获时刻为参考，而不是「现在」，否则同一快照会因时间流逝变 stale
        self.reference_time = (now if now is not None
                               else self.snapshot.captured_at_utc)
        self.iono_status = (ionosphere.status(self.reference_time)
                            if ionosphere is not None else STATUS_UNKNOWN)
        self.weather: Dict[str, Optional[EnvironmentFact]] = {
            FACT_PRESSURE: self.snapshot.get(FACT_PRESSURE),
            FACT_TEMPERATURE: self.snapshot.get(FACT_TEMPERATURE),
            FACT_HUMIDITY: self.snapshot.get(FACT_HUMIDITY),
        }
        weather_status = [f.status(self.reference_time) if f is not None
                          else STATUS_UNKNOWN for f in self.weather.values()]
        if all(s == STATUS_KNOWN for s in weather_status):
            self.tropo_status = STATUS_KNOWN
        elif any(s == STATUS_STALE for s in weather_status):
            self.tropo_status = STATUS_STALE
        else:
            self.tropo_status = STATUS_UNKNOWN
        self.iono_enabled = self.iono_status == STATUS_KNOWN
        self.tropo_enabled = self.tropo_status == STATUS_KNOWN

    # ---- 单星延迟 -------------------------------------------------------
    def iono_delay(self, lat_rad: float, lon_rad: float, az_rad: float,
                   el_rad: float, t_gpst: Optional[float]) -> Optional[float]:
        if not self.iono_enabled or t_gpst is None:
            return None
        # 延迟 import：rtklib_adapter 在模块级依赖本模块的类型做 SPP 集成，
        # 模块级互相 import 会成环；这里只借 Klobuchar 一个纯函数。
        from .rtklib_adapter import klobuchar_ion_delay
        return klobuchar_ion_delay(float(t_gpst), self.ionosphere.params,
                                   float(lat_rad), float(lon_rad),
                                   float(az_rad), float(el_rad))

    def tropo_delay(self, lat_rad: float, height_m: float,
                    el_rad: float) -> Optional[float]:
        if not self.tropo_enabled:
            return None
        pressure = self.weather[FACT_PRESSURE]
        temperature = self.weather[FACT_TEMPERATURE]
        humidity = self.weather[FACT_HUMIDITY]
        return saastamoinen_delay_m(
            pressure.value, temperature.value, humidity.value,
            lat_rad, height_m, el_rad)

    # ---- 预算汇总 -------------------------------------------------------
    @staticmethod
    def _delay_stats(values: Iterable[Optional[float]]) -> Dict[str, Any]:
        vals = [float(v) for v in values if v is not None]
        if not vals:
            return {"applied": False, "n": 0,
                    "delay_m_mean": None, "delay_m_max": None}
        return {
            "applied": True,
            "n": len(vals),
            "delay_m_mean": sum(vals) / len(vals),
            "delay_m_max": max(vals),
        }

    def summary(self, iono_delays: Iterable[Optional[float]] = (),
                tropo_delays: Iterable[Optional[float]] = ()) -> Dict[str, Any]:
        """逐星延迟 → 结果里的 ``error_budget``。"""
        iono_stats = self._delay_stats(iono_delays)
        tropo_stats = self._delay_stats(tropo_delays)
        iono_applied = bool(self.iono_enabled and iono_stats["applied"])
        tropo_applied = bool(self.tropo_enabled and tropo_stats["applied"])
        if iono_applied and tropo_applied:
            status = "corrected"
        elif iono_applied or tropo_applied:
            status = "partial"
        else:
            status = "uncorrected"

        sigma = None
        if iono_applied and tropo_applied:
            sigma = math.sqrt(
                (IONO_RESIDUAL_FRACTION * iono_stats["delay_m_mean"]) ** 2
                + (TROPO_RESIDUAL_FRACTION * tropo_stats["delay_m_mean"]) ** 2)

        iono_state = (self.ionosphere.state(self.reference_time)
                      if self.ionosphere is not None else {
                          "status": STATUS_UNKNOWN, "alpha": None, "beta": None,
                          "at_utc": None, "source": "",
                          "detail": "电离层: 未注入广播参数"})
        tropo_inputs = {
            name: (fact.state(self.reference_time) if fact is not None else {
                "name": name, "status": STATUS_UNKNOWN, "value": None,
                "unit": "", "at_utc": None, "source": "",
                "detail": f"{name}: 快照中缺该气象事实"})
            for name, fact in self.weather.items()
        }
        missing = [name for name, state in tropo_inputs.items()
                   if state["status"] != STATUS_KNOWN]
        note_parts: List[str] = []
        if not iono_applied:
            if self.iono_enabled:
                note_parts.append("电离层未校正（缺 t_gpst 或逐星几何为空）")
            else:
                note_parts.append("电离层未校正（" + iono_state["detail"] + "）")
        if not tropo_applied:
            if self.tropo_enabled:
                note_parts.append("对流层未校正（逐星几何为空）")
            else:
                detail = ("缺气象事实: " + ",".join(missing)) if missing \
                    else (f"对流层: 实测气象状态 {self.tropo_status}")
                note_parts.append("对流层未校正（" + detail + "）")
        if sigma is None:
            note_parts.append("缺项不报 0：residual_sigma_m=null")
        else:
            note_parts.append(
                f"residual_sigma_m 按经验比例（电离层 {IONO_RESIDUAL_FRACTION:.0%}、"
                f"对流层 {TROPO_RESIDUAL_FRACTION:.0%}）估算，非实测不确定度")
        return {
            "status": status,
            "model": f"{_IONO_MODEL}+{_TROPO_MODEL}",
            "reference_time_utc": self.reference_time,
            "ionosphere": {**iono_state, **iono_stats,
                           "status": self.iono_status},
            "troposphere": {"status": self.tropo_status,
                            "model": _TROPO_MODEL,
                            "inputs": tropo_inputs, **tropo_stats},
            "residual_sigma_m": sigma,
            "note": "；".join(note_parts),
            "disclaimer": "电离层为广播 Klobuchar 模型、对流层为实测气象 "
                          "Saastamoinen，均为模型改正，非实测延迟。",
        }

    def provenance(self) -> Dict[str, Any]:
        """RTK 等双差场景：只报环境状态与来源，不另加改正。"""
        iono_state = (self.ionosphere.state(self.reference_time)
                      if self.ionosphere is not None else {
                          "status": STATUS_UNKNOWN, "alpha": None, "beta": None,
                          "at_utc": None, "source": "",
                          "detail": "电离层: 未注入广播参数"})
        tropo_inputs = {
            name: (fact.state(self.reference_time) if fact is not None else {
                "name": name, "status": STATUS_UNKNOWN, "value": None,
                "unit": "", "at_utc": None, "source": "",
                "detail": f"{name}: 快照中缺该气象事实"})
            for name, fact in self.weather.items()
        }
        status = ("corrected" if self.iono_enabled and self.tropo_enabled
                  else "partial" if self.iono_enabled or self.tropo_enabled
                  else "uncorrected")
        return {
            "status": status,
            "model": f"{_IONO_MODEL}+{_TROPO_MODEL}+double_difference",
            "reference_time_utc": self.reference_time,
            "ionosphere": {**iono_state, "status": self.iono_status,
                           "applied": False},
            "troposphere": {"status": self.tropo_status, "model": _TROPO_MODEL,
                            "inputs": tropo_inputs, "applied": False},
            "residual_sigma_m": None,
            "note": "RTK 双差消去共模电离层/对流层项，未另加单差改正；"
                    "环境状态仅用于溯源与数据质量标注。",
        }


# ---------------------------------------------------------------------------
# 缺省（未注入）结果字段
# ---------------------------------------------------------------------------
def uncorrected_error_budget(reason: str,
                             reference_time_utc: Any = None) -> Dict[str, Any]:
    """没有环境输入时的诚实结果：显式 uncorrected，不填典型值。"""
    return {
        "status": "uncorrected",
        "model": None,
        "reference_time_utc": reference_time_utc,
        "ionosphere": {"status": STATUS_UNKNOWN, "applied": False,
                       "detail": f"电离层: {reason}"},
        "troposphere": {"status": STATUS_UNKNOWN, "applied": False,
                        "detail": f"对流层: {reason}"},
        "residual_sigma_m": None,
        "note": f"{reason}；电离层/对流层均未校正，residual_sigma_m=null",
    }

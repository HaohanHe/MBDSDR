# SPDX-License-Identifier: MIT
"""TLE 目录真实性守卫：编号 ↔ 名称 ↔ 来源 三方校验。

事故背景：目录把 CATNR 44016 声明为「METEOR M2」，而该编号的真实对象是
「ATLAS 5 CENTAUR DEB」（半人马上面级残骸，历元 2026-09-08）；54234 被声明
为「FENGYUN 3D」，实为「NOAA 21 (JPSS-2)」。错轨道卫星算出来的过境看起来
一切正常，却与天空里那颗星毫无关系——这是最危险的静默错误。

本模块的立场：

  * 声明的名称必须与数据侧对象名（TLE line0，来自缓存/用户文件）归一化后
    **完全相等**才算「已核实」；否则降级为「未核实」，移出可选列表，绝不用
    来算过境或跟踪；
  * 缺数据（无该编号 TLE）单独归类为 no_data，不算错配，也不冒充核实；
  * 多源冲突逐条留痕，不静默择一。

归一化与 desktop 的 ``_norm_sat_name`` 同口径（大写 + 只留字母数字），
"METEOR-M 2" / "METEOR M2" 归一为 "METEORM2"，而 "METEOR-M2 2" 归一为
"METEORM22"——串号必须判不匹配。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .environment_facts import EnvironmentCorrection
from .tle_epoch import TLE_FUTURE_TOL_DAYS, parse_tle_epoch

__all__ = [
    "CatalogEntry",
    "SOURCE_PRIORITY",
    "FRESH_MAX_HOURS",
    "USABLE_MAX_DAYS",
    "EXPIRED_MAX_DAYS",
    "DEAD_STALE_MAX_DAYS",
    "TIER_LABELS",
    "AGE_BAND_LABELS",
    "AGE_BAND_SEVERITY",
    "age_band",
    "band_note",
    "normalize_name",
    "names_match",
    "verify_identity",
    "freshness",
    "accuracy",
    "freshness_with_environment",
    "accuracy_with_environment",
    "TRACKING_LEVELS",
    "TRACKING_OPERATIONS",
    "TRACKING_MODES",
    "tracking_policy",
    "parse_tle_blocks",
    "user_entries",
    "cache_entries",
    "builtin_entries",
    "merge_sources",
    "scan_catalog",
    "scan_sources",
    "scan_live",
]

# 新鲜度分级阈值（按 TLE 历元年龄，不是缓存文件年龄）：
#   fresh   ≤ 24h      —— 可直接用于过境/跟踪
#   usable  ≤ 7d       —— 可用，但预报时刻已开始漂移
#   expired ≤ 30d      —— 可显示/可查，但默认不用于过境与跟踪
#   dead    > 30d      —— 失效，默认不可用；只能由调用方显式放行
FRESH_MAX_HOURS = 24.0
USABLE_MAX_DAYS = 7.0
EXPIRED_MAX_DAYS = 30.0
# 失效再分两档：31–90 天 / >90 天（>90 天是最强警示档；365 天必须命中它）
DEAD_STALE_MAX_DAYS = 90.0

TIER_LABELS: Dict[str, str] = {
    "fresh": "新鲜",
    "usable": "可用",
    "expired": "过期",
    "dead": "失效",
    "unknown": "未知",
}

# 年龄判定表：tier 只有 4 档（fresh/usable/expired/dead），展示需要把 dead
# 拆成「31–90 天」与「>90 天」两档——轨道误差随年龄单调放大，用户看到的
# 话必须随之升级，不能 31 天和 365 天说同一句。
AGE_BAND_LABELS: Dict[str, str] = {
    "fresh": "新鲜（≤24 小时）",
    "usable": "可用（≤7 天）",
    "expired": "过期（7–30 天）",
    "stale": "失效（31–90 天档）",
    "dead": "失效（超过 90 天）",
    "unknown": "失效/年龄未知",
}

# 严重度：给调用方排序用（越大越严重），不随文案变化。
AGE_BAND_SEVERITY: Dict[str, int] = {
    "fresh": 0, "usable": 1, "expired": 2, "stale": 3,
    "dead": 4, "unknown": 4,
}

_TIME_PRECISION_NOTE = (
    "time_precision_s=60 是预测时刻精度约 60 秒，不是卫星位置精度")


def _age_text(age_days: Optional[float]) -> str:
    """年龄人话（<1 天保留一位小数，否则取整）。"""
    if age_days is None:
        return ""
    if age_days < 1.0:
        return f"{age_days:.1f} 天"
    return f"{age_days:.0f} 天"


def age_band(age_days: Optional[float]) -> str:
    """年龄 → 展示档位（唯一判定表）。

    ≤24h fresh / ≤7d usable / ≤30d expired / ≤90d stale / >90d dead /
    无法解析 unknown。tier 语义不变（stale 与 dead 的 tier 都是 ``dead``），
    展示层据本函数升级文案。
    """
    if age_days is None:
        return "unknown"
    if age_days <= FRESH_MAX_HOURS / 24.0:
        return "fresh"
    if age_days <= USABLE_MAX_DAYS:
        return "usable"
    if age_days <= EXPIRED_MAX_DAYS:
        return "expired"
    if age_days <= DEAD_STALE_MAX_DAYS:
        return "stale"
    return "dead"


def band_note(band: str, age_days: Optional[float] = None) -> str:
    """每个档位的**确切文案**（面板/文档/测试共用，不许各处另写一份）。"""
    age = _age_text(age_days)
    if band == "fresh":
        return f"TLE 新鲜（{age}，≤24 小时）：过境时刻按秒级精度给出。"
    if band == "usable":
        return (f"TLE 可用（{age}，≤7 天）：过境时刻可能有数十秒级漂移，"
                "临近时建议刷新 TLE。")
    if band == "expired":
        return (f"TLE 已过期（{age}）：传播误差可达数分钟，过境时刻仅按"
                f"分钟级给出，不可默认用于精确跟踪（{_TIME_PRECISION_NOTE}）。")
    if band == "stale":
        return (f"TLE 已失效（{age}，31–90 天档）：轨道已不可信，过境时刻"
                f"仅供占位，必须刷新星历后才能跟踪（{_TIME_PRECISION_NOTE}）。")
    if band == "dead":
        return (f"TLE 已失效（{age}，超过 90 天）：轨道已严重不可信，过境"
                "时刻仅供占位，禁止用于跟踪/调谐，必须刷新星历"
                f"（{_TIME_PRECISION_NOTE}）。")
    return (f"TLE 年龄未知（历元无法解析）：无法判断星历是否新鲜，轨道"
            f"不可信，必须刷新星历后才能跟踪（{_TIME_PRECISION_NOTE}）。")

# 默认允许用于过境/跟踪的分级
_DEFAULT_USABLE_TIERS = ("fresh", "usable")

# 多源优先级：用户文件 > Celestrak（缓存/在线） > 内置表。
# 数字越小优先级越高。内置表只是「编号目录声明」，不携带真实数据。
SOURCE_PRIORITY: Dict[str, int] = {
    "user": 0,
    "celestrak": 1,
    "cache": 1,
    "builtin": 2,
}

# 默认缓存目录（与 orbit.fetch_tle 的磁盘缓存同一处）
DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".mbdsdr", "tle_cache")


def normalize_name(name: Any) -> str:
    """卫星名归一化：大写 + 只保留字母数字（与 desktop._norm_sat_name 同口径）。"""
    return "".join(ch for ch in str(name).upper() if ch.isalnum())


def names_match(declared: str, object_name: str) -> bool:
    """目录声明名与数据对象名是否语义一致（归一化后完全相等）。"""
    a = normalize_name(declared)
    b = normalize_name(object_name)
    return bool(a) and a == b


@dataclass
class CatalogEntry:
    """目录条目（声明）或数据条目（带 TLE）。

    ``name`` 是「目录声明的名字」（来源 user/builtin）；
    ``object_name`` 是数据侧真实对象名（TLE line0，来自 user/cache/celestrak）。
    两者缺一不可做三方校验。
    """

    name: str
    catnr: int
    source: str = "builtin"
    line1: str = ""
    line2: str = ""
    object_name: str = ""
    data_source: str = ""
    fetched_at: Optional[float] = None
    origin: str = ""
    refresh_status: str = "unknown"   # memory/cache/network/not_updated
    refresh_error: str = ""

    @property
    def has_tle(self) -> bool:
        return bool(self.line1 and self.line2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "catnr": int(self.catnr),
            "source": self.source,
            "data_source": self.data_source,
            "line1": self.line1,
            "line2": self.line2,
            "object_name": self.object_name,
            "fetched_at": self.fetched_at,
            "origin": self.origin,
            "refresh_status": self.refresh_status,
            "refresh_error": self.refresh_error,
        }

    def priority(self) -> int:
        return SOURCE_PRIORITY.get(self.source, 3)


def verify_identity(declared_name: str, object_name: str) -> Dict[str, Any]:
    """三方校验的「名称 ↔ 数据对象」一环。

    返回 ``{"status": "verified"|"mismatch"|"no_data", ...}``：
      - verified：归一化后完全相等；
      - mismatch：有数据但名字不符（必须降级，绝不用它算过境）；
      - no_data ：数据侧无对象名（缺数据，单独归类，不冒充核实）。
    """
    if not str(object_name or "").strip():
        return {
            "status": "no_data",
            "verified": False,
            "declared_name": str(declared_name),
            "object_name": "",
            "reason": "无数据对象名（缺该编号的 TLE），无法核实",
        }
    if names_match(declared_name, object_name):
        return {
            "status": "verified",
            "verified": True,
            "declared_name": str(declared_name),
            "object_name": str(object_name),
            "reason": "名称与数据对象名归一化后一致",
        }
    return {
        "status": "mismatch",
        "verified": False,
        "declared_name": str(declared_name),
        "object_name": str(object_name),
        "reason": (
            f"声明名「{declared_name}」与数据对象名「{object_name}」不符："
            "编号被映射到了另一颗/另一类对象，必须降级"
        ),
    }


def freshness(line1: str, fetched_at: Optional[float] = None,
              now: Optional[Any] = None) -> Dict[str, Any]:
    """TLE 新鲜度分级（按历元年龄）+ 缓存时间。

    返回 ``{"tier", "label", "age_days", "age_days_signed", "epoch_utc",
    "cache_age_hours", "pass_eligible", "tracking_eligible",
    "epoch_in_future", "epoch_implausible", "thresholds"}``。

    分级口径：fresh ≤24h / usable ≤7d / expired ≤30d / dead >30d /
    unknown（历元无法解析）。过期与失效条目仍可显示与查询，但
    ``pass_eligible=False``——默认不用于过境与跟踪，显式放行才降精度使用。

    ``age_days`` 是展示口径（未来历元钳到 0，避免界面出现「-3 天前」）；
    ``age_days_signed`` 是有符号的真实年龄，供判定与交叉核对使用——它与
    ``time_basis.tle_epoch_report()["age_days"]`` 必须逐值相等，否则同一份
    TLE 在两个出口会给出不同年龄。
    """
    if now is None:
        ref = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        ref = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    else:
        ref = datetime.fromtimestamp(float(now), tz=timezone.utc)
    epoch = parse_tle_epoch(line1)
    cache_age_hours: Optional[float] = None
    if fetched_at is not None:
        cache_age_hours = round((ref.timestamp() - float(fetched_at)) / 3600.0, 2)
    thresholds = {
        "fresh_max_hours": FRESH_MAX_HOURS,
        "usable_max_days": USABLE_MAX_DAYS,
        "expired_max_days": EXPIRED_MAX_DAYS,
    }
    if epoch is None:
        return {
            "tier": "unknown", "label": TIER_LABELS["unknown"],
            "age_days": None, "age_days_signed": None, "epoch_utc": None,
            "cache_age_hours": cache_age_hours,
            "pass_eligible": False, "tracking_eligible": False,
            "epoch_in_future": False, "epoch_implausible": False,
            "thresholds": thresholds,
            "reason": "TLE 历元无法解析（缺/坏数据），年龄未知",
        }
    age_days = (ref - epoch).total_seconds() / 86400.0
    in_future = age_days < 0.0
    # 未来历元的可信度判定必须与 time_basis.tle_epoch_report 同源同阈值：
    # 同一份 TLE 不能在一个出口「新鲜可用」、在另一个出口「时钟可疑」。
    implausible = bool(in_future and abs(age_days) > TLE_FUTURE_TOL_DAYS)
    age = max(0.0, age_days)
    if age <= FRESH_MAX_HOURS / 24.0:
        tier = "fresh"
    elif age <= USABLE_MAX_DAYS:
        tier = "usable"
    elif age <= EXPIRED_MAX_DAYS:
        tier = "expired"
    else:
        tier = "dead"
    usable = tier in _DEFAULT_USABLE_TIERS
    return {
        "tier": tier,
        "label": TIER_LABELS[tier],
        "age_days": round(age, 3),
        "age_days_signed": round(age_days, 3),
        "epoch_utc": epoch.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cache_age_hours": cache_age_hours,
        "pass_eligible": usable,
        "tracking_eligible": usable,
        "epoch_in_future": in_future,
        "epoch_implausible": implausible,
        "thresholds": thresholds,
        "reason": (
            "历元在将来（本机时钟可能不准），按新鲜处理但需核对时钟"
            if in_future else
            f"TLE 历元 {age:.2f} 天前，分级 {TIER_LABELS[tier]}"
        ),
    }


def accuracy(fresh: Dict[str, Any]) -> Dict[str, Any]:
    """把新鲜度映射成「过境/跟踪精度风险」的人话结论。

    过期/失效时调用方必须降精度（默认到分钟）并显式告警，不许照常输出
    精确到秒的时刻——TLE 的历元年龄直接决定传播误差的量级。

    文案来自 :func:`band_note` 的判定表（fresh/usable/expired/stale/dead/
    unknown），调用方不要再各写一份。
    """
    tier = str(fresh.get("tier", "unknown"))
    age = fresh.get("age_days")
    band = age_band(age)
    if band == "fresh":
        risk, precision = "low", 1
    elif band == "usable":
        risk, precision = "moderate", 1
    elif band == "expired":
        risk, precision = "high", 60
    else:
        risk, precision = "unusable", 60
    return {
        "risk": risk,
        "time_precision_s": precision,
        "note": band_note(band, age),
        "tier": tier,
        "band": band,
        "band_label": AGE_BAND_LABELS[band],
        "band_severity": AGE_BAND_SEVERITY[band],
        "age_days": age,
        "epoch_utc": fresh.get("epoch_utc"),
        "pass_eligible": bool(fresh.get("pass_eligible")),
    }


# ---------------------------------------------------------------------------
# 环境收紧层：在 freshness / accuracy 之上按当天 Kp / F10.7 只收紧
#
# 既有四档阈值（FRESH_MAX_HOURS/USABLE_MAX_DAYS/EXPIRED_MAX_DAYS/
# DEAD_STALE_MAX_DAYS）与 TRACKING_MODES 是基线事实源，一个字不改；
# 本层只把基线阈值乘以环境收紧系数（factor ∈ (0, 1]）后再判一次档，
# 结果取「基线档」与「收紧档」中更严格的那个——任何输入组合都不会让
# 可信时长变长。缺环境输入 → 回落未校正基线并显式标注，不按最坏值猜。
# ---------------------------------------------------------------------------
#: 基线阈值（小时口径），仅由上面四个常量派生；只读。
_BASE_THRESHOLD_HOURS: Dict[str, float] = {
    "fresh_max_hours": FRESH_MAX_HOURS,
    "usable_max_hours": USABLE_MAX_DAYS * 24.0,
    "expired_max_hours": EXPIRED_MAX_DAYS * 24.0,
    "dead_stale_max_hours": DEAD_STALE_MAX_DAYS * 24.0,
}

#: 档位严格度（越大越严格），用于「只能更严格」的比较。
_TIER_SEVERITY: Dict[str, int] = {
    "fresh": 0, "usable": 1, "expired": 2, "dead": 3, "unknown": 4,
}

#: 精度风险等级（越大越差），用于「风险不得降低」的兜底断言。
_RISK_ORDER: Dict[str, int] = {
    "low": 0, "moderate": 1, "high": 2, "unusable": 3,
}


def _uncorrected_environment(reason: str) -> EnvironmentCorrection:
    """未注入/缺输入时的显式 unsolved 结论（factor=1.0，绝不猜档）。"""
    return EnvironmentCorrection(
        status="uncorrected", factor=1.0, kp=None, f107=None,
        missing=(reason,),
        note=f"{reason}；回落未校正基线（factor=1.0），不按最坏值猜。",
    )


def _tier_from_thresholds(age_days: float,
                          thresholds: Dict[str, float]) -> str:
    """用（可能已收紧的）小时阈值重判年龄档位。"""
    age_hours = float(age_days) * 24.0
    if age_hours <= thresholds["fresh_max_hours"]:
        return "fresh"
    if age_hours <= thresholds["usable_max_hours"]:
        return "usable"
    if age_hours <= thresholds["expired_max_hours"]:
        return "expired"
    return "dead"  # 31–90 天与 >90 天的 tier 都是 dead（展示层再拆档）


def _stricter_tier(a: str, b: str) -> str:
    """取更严格的一档（顺序 fresh < usable < expired < dead < unknown）。"""
    return a if _TIER_SEVERITY.get(a, 4) >= _TIER_SEVERITY.get(b, 4) else b


def _band_from_thresholds(age_days: Optional[float],
                          thresholds: Dict[str, float]) -> str:
    """用收紧后的阈值映射展示档位（stale 与 dead 都是 tier=dead）。"""
    if age_days is None:
        return "unknown"
    age_hours = float(age_days) * 24.0
    if age_hours <= thresholds["fresh_max_hours"]:
        return "fresh"
    if age_hours <= thresholds["usable_max_hours"]:
        return "usable"
    if age_hours <= thresholds["expired_max_hours"]:
        return "expired"
    if age_hours <= thresholds["dead_stale_max_hours"]:
        return "stale"
    return "dead"


def freshness_with_environment(line1: str, fetched_at: Optional[float] = None,
                               now: Optional[Any] = None,
                               environment: Optional[EnvironmentCorrection] = None
                               ) -> Dict[str, Any]:
    """基线 :func:`freshness` + 当天空间天气收紧（只收紧）。

    ``environment`` 是 :class:`~mbdsdr_ai.environment_facts.EnvironmentCorrection`
    （通常由 ``space_weather_correction(snapshot)`` 求出）。返回基线结果的全部
    字段（``thresholds`` 仍是四档基线原值，不改写），并追加：

      * ``base_tier`` / ``base``：未收紧前的基线结论；
      * ``effective_thresholds``：基线阈值 × factor（小时，只小不大）；
      * ``environment``：修正结论（status/factor/Kp/F10.7 的 value/at_utc/
        source/note/disclaimer）；
      * ``tightened``：档位是否因环境输入而变严格；
      * ``tier``/``label``/``pass_eligible``/``tracking_eligible``/``reason``：
        收紧后的最终结论（只会比基线严格）。

    缺环境输入、输入过期或超表范围时，``environment.status="uncorrected"``
    且最终结论逐字段等于基线。
    """
    base = freshness(line1, fetched_at=fetched_at, now=now)
    corr = environment if isinstance(environment, EnvironmentCorrection) \
        else _uncorrected_environment("未注入环境修正（缺 Kp/F10.7 输入）")
    base_thresholds = dict(_BASE_THRESHOLD_HOURS)
    effective = corr.effective_thresholds(base_thresholds)

    out: Dict[str, Any] = dict(base)
    out["base_tier"] = base["tier"]
    out["base"] = dict(base)
    out["effective_thresholds"] = effective
    out["environment"] = {
        **corr.to_dict(),
        "base_thresholds_hours": dict(base_thresholds),
        "effective_thresholds_hours": dict(effective),
    }
    out["tightened"] = False
    if base["tier"] == "unknown" or base.get("age_days") is None:
        out["reason"] = (
            f"{base.get('reason', '')}；环境层：历元未知，无法按年龄收紧"
            f"（{corr.note}）")
        return out

    age = float(base["age_days"])
    eff_tier = _tier_from_thresholds(age, effective)
    tier = _stricter_tier(eff_tier, base["tier"])
    usable = tier in _DEFAULT_USABLE_TIERS
    tightened_note = "" if tier == base["tier"] else \
        f"（档位由 {base['tier']} 收紧为 {tier}）"
    out.update({
        "tier": tier,
        "label": TIER_LABELS[tier],
        "pass_eligible": usable,
        "tracking_eligible": usable,
        "tightened": tier != base["tier"],
        "reason": f"{base.get('reason', '')}；环境层：{corr.note}{tightened_note}",
    })
    # 只收紧的硬约束：最终档位不得比基线宽松，通过资格也不得放宽。
    assert _TIER_SEVERITY[tier] >= _TIER_SEVERITY[base["tier"]]
    assert not (out["pass_eligible"] and not base["pass_eligible"])
    return out


def accuracy_with_environment(fresh_env: Dict[str, Any]) -> Dict[str, Any]:
    """:func:`accuracy` 的环境收紧版：风险不降低、时刻精度不提高。

    ``fresh_env`` 是 :func:`freshness_with_environment` 的返回值。缺环境
    输入（``environment.status="uncorrected"``）时逐字段等于基线
    :func:`accuracy` 的结果。
    """
    data: Dict[str, Any] = fresh_env if isinstance(fresh_env, dict) else {}
    base_fresh = data.get("base") if isinstance(data.get("base"), dict) else data
    base = accuracy(base_fresh)
    corr = data.get("environment") or {}
    applied = bool(corr.get("applied"))
    thresholds = dict(data.get("effective_thresholds")
                      or _BASE_THRESHOLD_HOURS)
    age = data.get("age_days")
    band = _band_from_thresholds(age, thresholds) if applied else base["band"]
    if AGE_BAND_SEVERITY.get(band, 4) < AGE_BAND_SEVERITY.get(base["band"], 4):
        band = base["band"]  # 兜底：任何情况下不得比基线更宽松
    if band == "fresh":
        risk, precision = "low", 1
    elif band == "usable":
        risk, precision = "moderate", 1
    elif band == "expired":
        risk, precision = "high", 60
    else:
        risk, precision = "unusable", 60
    out = dict(base)
    out.update({
        "risk": risk,
        "time_precision_s": max(int(base["time_precision_s"]), int(precision)),
        "tier": data.get("tier", base["tier"]),
        "band": band,
        "band_label": AGE_BAND_LABELS[band],
        "band_severity": AGE_BAND_SEVERITY[band],
        "pass_eligible": bool(data.get("pass_eligible",
                                       base["pass_eligible"])),
        "note": (band_note(band, age) if applied else base["note"]),
        "base_risk": base["risk"],
        "base_band": base["band"],
        "tightened": bool(data.get("tightened")),
        "environment": corr,
        "effective_thresholds": thresholds,
    })
    assert _RISK_ORDER[risk] >= _RISK_ORDER[base["risk"]]
    return out


# ---------------------------------------------------------------------------
# 跟踪闸门：四档 × 四操作的唯一判定表
#
# 这是「档位 → 能不能写硬件」的唯一事实源（P0-01）。desktop 侧只读本函数
# 给出的布尔值，不得再自行比较 ``tier ==``——否则 AI 工具链与 CLI 会各写
# 一份规则，正是「48.9° 旧根数照常调谐」的形态。
#
# 操作 × 模式：
#   allow   = 直接允许
#   confirm = 允许，但必须先经用户二次确认（确认框须写明年龄与后果）
#   deny    = 禁止（dead 保留只读演示：照常算并显示将下发的频率，不写设备）
# ---------------------------------------------------------------------------
TRACKING_LEVELS: Tuple[str, ...] = ("fresh", "usable", "expired", "dead", "unknown")
TRACKING_OPERATIONS: Tuple[str, ...] = ("pass_list", "tune", "gimbal", "tool_write")

TRACKING_MODES: Dict[str, Dict[str, str]] = {
    "fresh":   {"pass_list": "allow", "tune": "allow",
                "gimbal": "allow", "tool_write": "allow"},
    "usable":  {"pass_list": "allow", "tune": "allow",
                "gimbal": "allow", "tool_write": "allow"},
    "expired": {"pass_list": "allow", "tune": "confirm",
                "gimbal": "deny", "tool_write": "confirm"},
    "dead":    {"pass_list": "deny", "tune": "deny",
                "gimbal": "deny", "tool_write": "deny"},
    "unknown": {"pass_list": "deny", "tune": "deny",
                "gimbal": "deny", "tool_write": "deny"},
}

TRACKING_LEVEL_LABELS: Dict[str, str] = {
    "fresh": "星历新鲜",
    "usable": "星历可用",
    "expired": "星历过期",
    "dead": "星历失效",
    "unknown": "星历不可用",
}

TRACKING_NEXT_ACTIONS: Dict[str, str] = {
    "fresh": "",
    "usable": "建议更新星历（临近过境前刷新可减小轨道漂移）",
    "expired": "确认星历年龄后可调谐；云台与自动工具写操作仍被禁止",
    "dead": "更新或导入 TLE 后重试",
    "unknown": "刷新或导入 TLE（当前历元无法解析）",
}

#: 显示用短标签与提示（桌面层只读，不许再自己拼/比较档位）。
#: 改措辞只改这里——desktop 不改。
TRACKING_HINTS: Dict[str, str] = {
    "fresh": "",
    "usable": "建议更新星历",
    "expired": "需确认后调谐（云台禁用）",
    "dead": "星历失效：请更新",
    "unknown": "星历不可用：请更新",
}

TRACKING_SEVERITY: Dict[str, str] = {
    "fresh": "normal",
    "usable": "normal",
    "expired": "warning",
    "dead": "danger",
    "unknown": "danger",
}


def _age_text(age: Optional[float]) -> str:
    """年龄人话：<1 天留一位小数，否则取整（显示与标签共用一份）。"""
    if age is None:
        return ""
    return f"{age:.1f}" if float(age) < 1.0 else f"{float(age):.0f}"


def _display_tag(level: str, age: Optional[float], forced: bool) -> str:
    """过境行/卫星名后的陈旧标签（空串 = 不标注）。"""
    if forced:
        return "（TLE 身份不符）"
    age_text = _age_text(age)
    if level == "expired":
        return f"（TLE 过期 {age_text} 天）" if age_text else "（TLE 过期）"
    if level == "dead":
        return f"（TLE 失效 {age_text} 天）" if age_text else "（TLE 失效）"
    if level == "unknown":
        return "（TLE 年龄未知）"
    return ""


def _short_tag(level: str, age: Optional[float]) -> str:
    """下拉项后缀（不带括号）：空串 = 不标注。"""
    age_text = _age_text(age)
    if level == "usable":
        return f"{age_text} 天前" if age_text else ""
    if level == "expired":
        return f"过期 {age_text} 天" if age_text else "过期"
    if level == "dead":
        return "失效"
    if level == "unknown":
        return "年龄未知"
    return ""


def tracking_policy(fresh: Optional[Dict[str, Any]],
                    identity_status: str = "verified") -> Dict[str, Any]:
    """档位 → 操作许可（纯函数，无 I/O，desktop/AI 共用）。

    ``fresh`` 是 :func:`freshness` 的返回值；``identity_status`` 是
    :func:`verify_identity` 的 ``status``（verified/mismatch/no_data）。

    身份不符（``mismatch``，44016/54234 事故形态）**无视年龄一律按 dead
    处理**——错误的对象比过期的对象更危险；``no_data`` 不冒充核实，按
    ``unknown`` 走同一套禁止策略（缺数据的轨道同样不可信）。

    返回键：``level``/``tier``/``identity_status``/``age_days``/``band``/
    ``allow_pass_list``/``allow_tune``/``require_confirm``/``allow_gimbal``/
    ``allow_tool_write``/``tool_require_confirm``/``command_allowed``/
    ``label``/``hint``/``display_tag``/``short_tag``/``severity``/``stale``/
    ``age_text``/``reason``/``next_action``。

    显示口径（``label``/``hint``/``display_tag``/``short_tag``/``severity``/
    ``stale``）也只在这里生成：桌面层读字段，不再自行比较 ``tier ==`` 或
    另写一份档位文案——改措辞只改本模块。

    语义分工：
      - ``allow_tune`` = 可以下发调谐（``require_confirm`` 为真时先确认）；
      - ``command_allowed`` = 是否**存在**写硬件的合法路径（dead/unknown 为
        假：只读演示，任何入口都不得下发）；
      - ``require_confirm`` = 调谐前必须二次确认；``tool_require_confirm``
        对应 AI 工具写操作。
    """
    data: Dict[str, Any] = fresh if isinstance(fresh, dict) else {}
    tier = str(data.get("tier") or "unknown")
    if tier not in TRACKING_MODES:
        tier = "unknown"
    ident = str(identity_status or "verified")
    forced = ident == "mismatch"
    if forced:
        level = "dead"
    elif ident == "no_data":
        level = "unknown"
    else:
        level = tier
    modes = TRACKING_MODES[level]
    age = data.get("age_days")
    band = age_band(age)

    if forced:
        label = "星历身份不符（按失效处理）"
        reason = ("卫星声明名与轨道数据对象名不符（编号被映射到另一颗对象）："
                  "无视星历年龄一律按失效处理")
        next_action = "用正确编号的 TLE 更新或导入后重试"
    else:
        label = TRACKING_LEVEL_LABELS[level]
        if level == "unknown":
            reason = "星历历元缺失或无法解析，无法判断轨道是否可信"
        else:
            reason = str(data.get("reason") or band_note(band, age))
        next_action = TRACKING_NEXT_ACTIONS[level]

    tune_mode = modes["tune"]
    tool_mode = modes["tool_write"]
    return {
        "level": level,
        "tier": tier,
        "identity_status": ident,
        "identity_forced": forced,
        "age_days": age,
        "band": band,
        "modes": dict(modes),
        "allow_pass_list": modes["pass_list"] != "deny",
        "allow_tune": tune_mode != "deny",
        "require_confirm": tune_mode == "confirm",
        "allow_gimbal": modes["gimbal"] == "allow",
        "allow_tool_write": tool_mode != "deny",
        "tool_require_confirm": tool_mode == "confirm",
        "command_allowed": tune_mode != "deny",
        "label": label,
        "hint": TRACKING_HINTS[level],
        "display_tag": _display_tag(level, age, forced),
        "short_tag": _short_tag(level, age),
        "severity": TRACKING_SEVERITY[level],
        "stale": level in ("expired", "dead", "unknown"),
        "age_text": _age_text(age),
        "reason": reason,
        "next_action": next_action,
    }


def _catnr_from_line1(line1: str) -> Optional[int]:
    try:
        return int(line1[2:7].strip())
    except (ValueError, IndexError):
        return None


def parse_tle_blocks(text: str, source: str = "user",
                     origin: str = "", fetched_at: Optional[float] = None,
                     ) -> List[CatalogEntry]:
    """解析 TLE 文本（名称 + 1 + 2 三行块；也容忍缺名称的两行块）。

    只做结构自洽检查：line2 的编号必须与 line1 一致，否则跳过（不自洽的
    数据不进目录）。校验和由数据源侧负责（缓存/网络入口已有校验）。
    """
    lines = [ln.strip() for ln in str(text).splitlines()]
    n = len(lines)
    out: List[CatalogEntry] = []
    i = 0
    while i < n:
        if not lines[i].startswith("1 "):
            i += 1
            continue
        if i + 1 >= n or not lines[i + 1].startswith("2 "):
            i += 1
            continue
        line1, line2 = lines[i], lines[i + 1]
        catnr = _catnr_from_line1(line1)
        catnr2 = _catnr_from_line1(line2)
        if catnr is None or catnr2 is None or catnr != catnr2:
            i += 2
            continue
        name = ""
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j >= 0 and not lines[j].startswith(("1 ", "2 ")):
            name = lines[j]
        if not name:
            name = f"NORAD {catnr}"
        out.append(CatalogEntry(
            name=name, catnr=catnr, source=source,
            line1=line1, line2=line2, object_name=name,
            data_source=source, fetched_at=fetched_at, origin=origin,
        ))
        i += 2
    return out


def user_entries(text: str, origin: str = "<user>",
                 fetched_at: Optional[float] = None) -> List[CatalogEntry]:
    """用户导入的 TLE 文件 → 条目（source=user，优先级最高）。"""
    return parse_tle_blocks(text, source="user", origin=origin,
                            fetched_at=fetched_at)


def cache_entries(cache_dir: Optional[str] = None,
                  now: Optional[float] = None) -> List[CatalogEntry]:
    """扫描 Celestrak 磁盘缓存（每个 CATNR 一个三行 .tle 文件）。

    ``fetched_at`` 取文件 mtime——这是「缓存时间」，与 TLE 历元（轨道数据
    自身的时刻）不是一回事，两者都保留。
    """
    d = cache_dir or DEFAULT_CACHE_DIR
    ref = now if now is not None else time.time()
    out: List[CatalogEntry] = []
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".tle"):
            continue
        path = os.path.join(d, fn)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = [ln.strip() for ln in fh.read().splitlines()]
            lines = [ln for ln in lines if ln]
            if len(lines) < 3 or not lines[1].startswith("1 ") \
                    or not lines[2].startswith("2 "):
                continue
            name, line1, line2 = lines[0], lines[1], lines[2]
            catnr = _catnr_from_line1(line1)
            if catnr is None or catnr != _catnr_from_line1(line2):
                continue
            out.append(CatalogEntry(
                name=name, catnr=catnr, source="cache",
                line1=line1, line2=line2, object_name=name,
                data_source="cache",
                fetched_at=os.path.getmtime(path), origin=path))
        except OSError:
            continue
    return out


def builtin_entries(sat_map: Optional[Dict[str, int]] = None) -> List[CatalogEntry]:
    """内置目录声明（名称 + CATNR），无 TLE 数据（数据由缓存/在线补）。

    默认取 ``orbit.BUILTIN_SATS``（延迟导入避免循环依赖）；测试可注入替身。
    """
    if sat_map is None:
        from .orbit import BUILTIN_SATS
        sat_map = BUILTIN_SATS
    return [CatalogEntry(name=str(name), catnr=int(catnr), source="builtin")
            for name, catnr in sat_map.items()]


def _declaration_conflicts(entries: Sequence[CatalogEntry]) -> List[Dict[str, Any]]:
    """声明侧冲突：同名不同号、同号不同名——全部列出，不静默择一。"""
    conflicts: List[Dict[str, Any]] = []
    by_name: Dict[str, List[CatalogEntry]] = {}
    by_catnr: Dict[int, List[CatalogEntry]] = {}
    for e in entries:
        key = normalize_name(e.name)
        if key:
            by_name.setdefault(key, []).append(e)
        by_catnr.setdefault(int(e.catnr), []).append(e)
    for key, group in by_name.items():
        catnrs = sorted({e.catnr for e in group})
        if len(catnrs) > 1:
            conflicts.append({
                "type": "name_catnr_conflict",
                "name": group[0].name,
                "catnrs": catnrs,
                "sources": [e.source for e in group],
                "resolution": "按来源优先级取用户声明，其余保留为冲突记录",
            })
    for catnr, group in by_catnr.items():
        names = sorted({normalize_name(e.name) for e in group})
        if len(names) > 1:
            conflicts.append({
                "type": "catnr_name_conflict",
                "catnr": catnr,
                "names": [e.name for e in group],
                "sources": [e.source for e in group],
                "resolution": "保留各声明并降级名称不符者",
            })
    return conflicts


def _data_for(catnr: int,
              data_pool: Sequence[CatalogEntry]) -> Optional[CatalogEntry]:
    """按编号取数据：优先级 user > celestrak/cache > builtin；同级取最新缓存。"""
    candidates = [d for d in data_pool if d.catnr == catnr and d.has_tle]
    if not candidates:
        return None
    candidates.sort(key=lambda d: (d.priority(), -(d.fetched_at or 0.0)))
    return candidates[0]


def scan_catalog(declarations: Sequence[CatalogEntry],
                 data_pool: Optional[Sequence[CatalogEntry]] = None,
                 now: Optional[Any] = None,
                 extra_conflicts: Optional[Sequence[Dict[str, Any]]] = None,
                 ) -> Dict[str, Any]:
    """全目录扫描：每条声明都做「编号 ↔ 名称 ↔ 来源」三方校验 + 新鲜度分级。

    返回 ``{"total", "passed", "degraded", "mismatch", "no_data", "stale",
    "reasons", "rows", "conflicts"}``。``passed`` = 已核实且 TLE 新鲜度
    默认可用于过境/跟踪；``degraded`` = 未核实/缺数据/过期/失效，仍可显示
    与查询，但默认不得参与过境或跟踪。
    """
    pool = list(data_pool or [])
    conflicts = _declaration_conflicts(list(declarations))
    if extra_conflicts:
        conflicts = conflicts + list(extra_conflicts)
    rows: List[Dict[str, Any]] = []
    reasons: Dict[str, List[Dict[str, Any]]] = {
        "identity_mismatch": [], "no_data": [], "stale": [],
    }
    passed = 0
    for decl in declarations:
        data = _data_for(int(decl.catnr), pool) if not decl.has_tle else decl
        object_name = data.object_name if data is not None else ""
        identity = verify_identity(decl.name, object_name)
        fresh = freshness(
            data.line1 if data is not None else "",
            data.fetched_at if data is not None else None,
            now=now)
        usable = bool(identity["status"] == "verified" and fresh["pass_eligible"])
        if identity["status"] != "verified":
            degrade_reason = identity["status"]
        elif not fresh["pass_eligible"]:
            degrade_reason = f"tle_{fresh['tier']}"
        else:
            degrade_reason = ""
        cache_time_utc = (
            datetime.fromtimestamp(float(data.fetched_at), tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
            if data is not None and data.fetched_at is not None else None)
        row = {
            "name": decl.name,
            "catnr": int(decl.catnr),
            "declared_source": decl.source,
            "data_source": (data.data_source or data.source
                            if data is not None else ""),
            "data_origin": (data.origin if data is not None else ""),
            "object_name": object_name,
            "identity": identity,
            "freshness": fresh,
            "epoch_utc": fresh.get("epoch_utc"),
            "cache_time_utc": cache_time_utc,
            "refresh_status": (data.refresh_status if data is not None
                               else "unknown"),
            "displayable": True,
            "selectable": usable,
            "pass_eligible": usable,
            "tracking_eligible": usable,
            "degraded": not usable,
            "degrade_reason": degrade_reason,
        }
        if usable:
            passed += 1
        else:
            if identity["status"] == "no_data":
                key = "no_data"
            elif identity["status"] == "mismatch":
                key = "identity_mismatch"
            else:
                key = "stale"
            reasons[key].append({
                "name": decl.name, "catnr": int(decl.catnr),
                "object_name": object_name,
                "tier": fresh["tier"],
                "detail": (identity["reason"] if key != "stale"
                           else fresh["reason"]),
            })
        # 把与该条目相关的声明冲突附在行上（按名或号命中）
        row["conflicts"] = [
            c for c in conflicts
            if (c["type"] == "name_catnr_conflict"
                and normalize_name(c["name"]) == normalize_name(decl.name))
            or (c["type"] == "catnr_name_conflict" and c["catnr"] == int(decl.catnr))
        ]
        rows.append(row)
    degraded = len(rows) - passed
    return {
        "total": len(rows),
        "passed": passed,
        "degraded": degraded,
        "mismatch": len(reasons["identity_mismatch"]),
        "no_data": len(reasons["no_data"]),
        "stale": len(reasons["stale"]),
        "reasons": reasons,
        "rows": rows,
        "conflicts": conflicts,
        "now": (now if isinstance(now, (int, float)) else
                (now.timestamp() if isinstance(now, datetime) else time.time())),
    }


def merge_sources(groups: Any, now: Optional[Any] = None) -> Dict[str, Any]:
    """三源合并：用户文件 > Celestrak 缓存/在线 > 内置表。

    ``groups`` 可以是 ``{"user": [...], "cache": [...], "builtin": [...]}``
    或任意 ``CatalogEntry`` 序列（按 ``entry.source`` 分组）。

    规则：
      * 同名同号重复声明：高优先级胜出，落选者进 ``shadowed``（不静默丢）；
      * 同名不同号 / 同号不同名：两种声明都保留（交给三方校验把名字不符者
        降级），并记入 ``conflicts``；
      * 同号多份不同 TLE：记录 ``tle_data_conflict``（含历元与来源），
        采用优先级最高、缓存最新的那份，其余保留为冲突记录。

    返回 ``{"entries", "conflicts", "data_conflicts", "shadowed",
    "source_priority"}``。
    """
    if isinstance(groups, dict):
        items: List[Tuple[str, CatalogEntry]] = []
        for src, entries in groups.items():
            for e in entries:
                items.append((str(src), e if e.source == src
                              else replace(e, source=str(src))))
    else:
        items = [(e.source, e) for e in groups]

    conflicts = _declaration_conflicts([e for _s, e in items])
    data_conflicts: List[Dict[str, Any]] = []
    data_by_catnr: Dict[int, List[Tuple[str, CatalogEntry]]] = {}
    for src, e in items:
        if e.has_tle:
            data_by_catnr.setdefault(int(e.catnr), []).append((src, e))

    for catnr, ds in sorted(data_by_catnr.items()):
        names = {normalize_name(d.object_name or d.name) for _s, d in ds}
        if len(names) > 1:
            data_conflicts.append({
                "type": "data_name_conflict",
                "catnr": catnr,
                "names": [(d.object_name or d.name) for _s, d in ds],
                "sources": [s for s, _d in ds],
                "resolution": "各声明保留，名称不符者按三方校验降级",
            })
        line1s = {d.line1 for _s, d in ds}
        if len(line1s) > 1:
            best_src = sorted(
                ds, key=lambda se: (SOURCE_PRIORITY.get(se[0], 3),
                                    -(se[1].fetched_at or 0.0)))[0][0]
            epochs = []
            for _s, d in ds:
                ep = parse_tle_epoch(d.line1)
                epochs.append({
                    "source": _s,
                    "epoch_utc": (ep.strftime("%Y-%m-%dT%H:%M:%SZ")
                                  if ep is not None else None),
                    "object_name": d.object_name or d.name,
                })
            data_conflicts.append({
                "type": "tle_data_conflict",
                "catnr": catnr,
                "epochs": epochs,
                "sources": [s for s, _d in ds],
                "chosen_source": best_src,
                "chosen_epoch_utc": next(
                    (x["epoch_utc"] for x in epochs if x["source"] == best_src),
                    None),
                "resolution": "取优先级最高且缓存最新的 TLE；其余留存于此冲突记录",
            })

    grouped: Dict[Tuple[str, int], List[Tuple[str, CatalogEntry]]] = {}
    for src, e in items:
        grouped.setdefault((normalize_name(e.name), int(e.catnr)), []).append(
            (src, e))

    entries: List[CatalogEntry] = []
    shadowed: List[Dict[str, Any]] = []
    for key, group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda se: (
            SOURCE_PRIORITY.get(se[0], 3), -(se[1].fetched_at or 0.0)))
        winner_src, winner = ordered[0]
        for src, e in ordered[1:]:
            shadowed.append({
                "name": e.name, "catnr": int(e.catnr), "source": src,
                "shadowed_by": winner_src,
                "reason": "同名同号重复声明，按来源优先级取舍",
            })
        data = winner if winner.has_tle else None
        if data is None:
            pool = sorted(data_by_catnr.get(int(winner.catnr), []),
                          key=lambda se: (SOURCE_PRIORITY.get(se[0], 3),
                                          -(se[1].fetched_at or 0.0)))
            data = pool[0][1] if pool else None
            data_src = pool[0][0] if pool else ""
        else:
            data_src = winner_src
        if data is None:
            merged = replace(winner, source=winner_src, data_source="",
                             object_name="")
        else:
            merged = replace(
                winner, source=winner_src,
                line1=data.line1, line2=data.line2,
                object_name=data.object_name or data.name,
                data_source=data.data_source or data_src,
                fetched_at=data.fetched_at,
                origin=data.origin,
                refresh_status=data.refresh_status,
                refresh_error=data.refresh_error,
            )
        entries.append(merged)

    return {
        "entries": entries,
        "conflicts": conflicts + data_conflicts,
        "data_conflicts": data_conflicts,
        "shadowed": shadowed,
        "source_priority": dict(SOURCE_PRIORITY),
    }


def scan_sources(groups: Any, now: Optional[Any] = None) -> Dict[str, Any]:
    """三源合并 + 全目录扫描：一行内同时给出身份、口径、来源与冲突。"""
    merged = merge_sources(groups, now=now)
    rep = scan_catalog(merged["entries"], now=now,
                       extra_conflicts=merged["data_conflicts"])
    rep["shadowed"] = merged["shadowed"]
    rep["source_priority"] = merged["source_priority"]
    return rep


def scan_live(cache_dir: Optional[str] = None,
              sat_map: Optional[Dict[str, int]] = None,
              now: Optional[Any] = None) -> Dict[str, Any]:
    """对「内置声明 + 本机 Celestrak 缓存」做全目录扫描（离线可用）。"""
    decls = builtin_entries(sat_map)
    pool = cache_entries(cache_dir=cache_dir, now=now)
    return scan_catalog(decls, pool, now=now)

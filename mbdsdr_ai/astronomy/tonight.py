# SPDX-License-Identifier: MIT
"""今夜可看性：把「环境快照」变成一句能核对的人话。

为什么要单独一层
================

「今晚能看吗」是用户最先问、也最容易被编造的问题。常见做法是查一个
天气接口，缺测时按「晴」处理——于是无网络时也给出「适合观测」。
这和本仓库的诚实口径冲突：**没取到云量就是没取到，不许按晴天假设。**

本模块只做判定，不联网：输入是 :mod:`mbdsdr_ai.space_weather` 已经采到的
快照（含观测时刻、来源 URL、逐项 error），输出三个可分开核对的结论：

* ``sky``    ：云量决定的「能不能看见天」；
* ``aurora`` ：Kp 与地磁纬度决定的「极光大概看不看得到」；
* ``verdict``：只说云量这一件已经被证实的事。

两条边界必须写在返回里，不能只写在文档里：

1. 云量取的是**采集时刻的实况**，不是整夜预报。后半夜转云本模块不知道。
2. 极光只用 Kp + 地磁纬度的经验门槛粗判，**不是极光椭圆模型**；
   真实可见性还取决于 Bz、太阳风速度与本地云量。

数据口径
--------
* 云量阈值 20% / 60% 是观测习惯，不是物理定律。
* 偶极磁北极取 IGRF 一阶近似的惯用值 (80.65°N, 72.68°W)。
* Kp → 极光边缘地磁纬度用 NOAA 公开的经验表（整数档线性插值）。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from .meteor_showers import active_showers, describe

__all__ = [
    "TONIGHT_SCHEMA",
    "CLOUD_GOOD_MAX_PCT",
    "CLOUD_FAIR_MAX_PCT",
    "KP_TO_MLAT_EDGE",
    "geomagnetic_latitude",
    "kp_needed_for_mlat",
    "assess_tonight",
    "stargazing_index",
    "format_tonight_section",
]

#: 判定结果 schema。快照契约改字段时同步加一。
#: v2：新增 ``meteor_showers``（观测日期 → 处于活动期的流星雨）。
#: v3：新增 ``index``（云量 + 月光的综合观星指数，见 :func:`stargazing_index`）。
TONIGHT_SCHEMA = 3

#: 云量分档上限（百分比）。以下两档为「大体可用 / 有云缝」。
CLOUD_GOOD_MAX_PCT = 20.0
CLOUD_FAIR_MAX_PCT = 60.0

#: 偶极磁北极（一阶近似）。只服务极光粗判，不用于地磁台站计算。
MAGNETIC_NORTH_LAT_DEG = 80.65
MAGNETIC_NORTH_LON_DEG = -72.68

#: NOAA 经验表：Kp → 极光椭圆边缘大致到达的地磁纬度（度）。
KP_TO_MLAT_EDGE = {
    0: 66.5, 1: 64.5, 2: 62.4, 3: 60.4, 4: 58.3,
    5: 56.3, 6: 54.2, 7: 52.2, 8: 50.1, 9: 48.1,
}

#: 采集失败时的统一措辞：不补默认值，也不说「天气晴」。
_NO_GUESS = "未取到，不按晴天假设"


def geomagnetic_latitude(lat_deg: float, lon_deg: float) -> float:
    """地理坐标 → 地磁纬度（偶极近似，单位度）。

    公式是标准偶极子近似：绕磁轴的角度由磁北极与测站的球面夹角给出。
    对「极光大概在不在你的纬线上」够用；不要拿它做地磁台站级计算。
    """
    lat = math.radians(float(lat_deg))
    lon = math.radians(float(lon_deg))
    pole_lat = math.radians(MAGNETIC_NORTH_LAT_DEG)
    pole_lon = math.radians(MAGNETIC_NORTH_LON_DEG)
    cos_theta = (math.sin(lat) * math.sin(pole_lat)
                 + math.cos(lat) * math.cos(pole_lat)
                 * math.cos(lon - pole_lon))
    cos_theta = max(-1.0, min(1.0, cos_theta))
    return 90.0 - math.degrees(math.acos(cos_theta))


def kp_needed_for_mlat(mlat_deg: float) -> float:
    """反查：要在给定地磁纬度看到极光边缘，大致需要多大的 Kp。

    返回值是连续量（两档之间线性插值），便于给「差多少」而不是只给
    一个粗糙的「能/不能」。高纬端截断到 Kp=0（Kp 不能为负）；低纬端
    按表的斜率**外推**而不是截断到 9——否则北京（磁纬约 30°）会被
    算成「Kp=9 刚好够」，这正是本模块要避免的假阳性。
    """
    mlat = float(mlat_deg)
    if mlat >= KP_TO_MLAT_EDGE[0]:
        return 0.0
    if mlat <= KP_TO_MLAT_EDGE[9]:
        slope = ((KP_TO_MLAT_EDGE[0] - KP_TO_MLAT_EDGE[9]) / 9.0)
        return 9.0 + (KP_TO_MLAT_EDGE[9] - mlat) / slope
    for kp in range(9):
        hi = KP_TO_MLAT_EDGE[kp]
        lo = KP_TO_MLAT_EDGE[kp + 1]
        if lo <= mlat <= hi:
            return kp + (hi - mlat) / (hi - lo)
    return 9.0


def _sample(snapshot: Optional[Dict[str, Any]], key: str) -> Dict[str, Any]:
    return ((snapshot or {}).get("samples") or {}).get(key) or {}


def _value(sample: Dict[str, Any]) -> Optional[float]:
    v = sample.get("value")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _cloud_pct(sample: Dict[str, Any]) -> Optional[float]:
    """云量只认 measurements.cloud_cover_pct——weather 的 value 是降水量。"""
    meas = sample.get("measurements") or {}
    v = meas.get("cloud_cover_pct")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _assess_sky(sample: Dict[str, Any]) -> Dict[str, Any]:
    pct = _cloud_pct(sample)
    if pct is None:
        why = sample.get("error") or "快照里没有 cloud_cover"
        return {
            "known": False,
            "cloud_cover_pct": None,
            "grade": "unknown",
            "text": f"云量{_NO_GUESS}（{why}）",
            "reason": f"云量{_NO_GUESS}（{why}）",
            "observed_at": sample.get("observed_at") or "",
            "source": sample.get("source") or "",
            "url": sample.get("url") or "",
        }
    pct = max(0.0, min(100.0, pct))
    if pct <= CLOUD_GOOD_MAX_PCT:
        grade, text = "good", f"云量 {pct:.0f}%，天空大体可用"
    elif pct <= CLOUD_FAIR_MAX_PCT:
        grade, text = "fair", f"云量 {pct:.0f}%，有云缝，看运气"
    else:
        grade, text = "poor", f"云量 {pct:.0f}%，基本被云挡住"
    return {
        "known": True,
        "cloud_cover_pct": pct,
        "grade": grade,
        "text": text,
        "reason": "",
        "observed_at": sample.get("observed_at") or "",
        "source": sample.get("source") or "",
        "url": sample.get("url") or "",
    }


def _assess_aurora(kp_sample: Dict[str, Any], lat: Optional[float],
                   lon: Optional[float]) -> Dict[str, Any]:
    kp = _value(kp_sample)
    if kp is None:
        why = kp_sample.get("error") or "快照里没有 Kp"
        return {"known": False, "chance": "unknown", "kp": None,
                "reason": f"未取到 Kp（{why}），不猜极光",
                "observed_at": kp_sample.get("observed_at") or ""}
    if lat is None or lon is None:
        return {"known": False, "chance": "unknown", "kp": kp,
                "reason": "没有站址，算不出地磁纬度，不猜极光",
                "observed_at": kp_sample.get("observed_at") or ""}

    mlat = geomagnetic_latitude(float(lat), float(lon))
    need = kp_needed_for_mlat(mlat)
    margin = kp - need
    if margin >= 1.0:
        chance = "likely"
    elif margin >= 0.0:
        chance = "possible"
    elif margin >= -1.0:
        chance = "marginal"
    else:
        chance = "unlikely"
    return {
        "known": True,
        "chance": chance,
        "kp": kp,
        "geomagnetic_lat_deg": round(mlat, 1),
        "kp_needed": round(need, 1),
        "margin": round(margin, 2),
        "observed_at": kp_sample.get("observed_at") or "",
        "note": ("只按 Kp 与地磁纬度粗判，不是极光椭圆预报；"
                 "真实可见性还取决于 Bz、太阳风速度与本地云量；"
                 "Kp 是实况不是预报。"),
    }


def _assess_moon(moon: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """单时刻月光采样 → 干扰档位。

    采样由调用方给（本地历表算的照亮比例 + 高度角），本函数只做分档，
    不自己算位置、不猜整夜。缺采样就说缺，不按「无月」处理。
    """
    base = {"known": False, "interference": "unknown", "illumination": None,
            "alt_deg": None, "at": "", "source": "", "reason": "",
            "text": ""}
    if not moon:
        base["reason"] = "没有月球采样，不猜月光干扰"
        return base
    try:
        ill = float(moon.get("illumination"))
        alt = float(moon.get("alt_deg"))
    except (TypeError, ValueError):
        base["reason"] = "月球采样缺 illumination / alt_deg"
        return base
    if not (math.isfinite(ill) and math.isfinite(alt)):
        base["reason"] = "月球采样不是有限数"
        return base
    ill = min(max(ill, 0.0), 1.0)
    base.update({"known": True, "illumination": round(ill, 3),
                 "alt_deg": round(alt, 1),
                 "at": str(moon.get("at") or ""),
                 "source": str(moon.get("source") or "")})
    if alt < 0.0:
        base.update({"interference": "none",
                     "text": "月亮在地平线下（采样时刻），无直射月光"})
    elif ill >= 0.65 and alt >= 20.0:
        base.update({"interference": "strong",
                     "text": (f"月光强：照亮 {ill * 100:.0f}%，高度 {alt:.0f}°，"
                              "暗弱天体与银河受影响")})
    elif ill >= 0.25 and alt >= 10.0:
        base.update({"interference": "moderate",
                     "text": (f"月光中等：照亮 {ill * 100:.0f}%，高度 {alt:.0f}°，"
                              "亮星与行星影响小")})
    else:
        base.update({"interference": "weak",
                     "text": (f"月光弱：照亮 {ill * 100:.0f}%，高度 {alt:.0f}°")})
    return base


def _assess_showers(at: Optional[datetime]) -> Dict[str, Any]:
    """观测日期 → 处于活动期的流星雨。

    只用本仓静态年历（``meteor_showers``）做「现在算不算活动期 / 离峰值多远」，
    不联网、不给「今晚每小时 N 颗」。日期按 UTC 判定；跨时区的本地夜晚
    可能差一天，但活动包络变化缓慢，结论不会被 ±1 天翻转。
    """
    base: Dict[str, Any] = {"known": False, "at": "", "items": [],
                            "reason": "", "text": ""}
    if at is None:
        base["reason"] = "未提供观测时刻，不判断今晚流星雨"
        return base
    when = at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    items = [{"code": s.code, "name_zh": s.name_zh,
              "fraction": round(f, 3), "zhr_peak": s.zhr_peak,
              "text": describe(s, f)}
             for s, f in active_showers(when)]
    base.update({"known": True, "at": when.strftime("%Y-%m-%d"),
                 "items": items})
    if items:
        base["text"] = "；".join(it["text"] for it in items)
    else:
        base["text"] = "12 场主要流星雨都不在活动期"
    return base


#: 综合观星指数的经验结构：云量是「能不能看见」的乘法门，月光最多扣 35%。
#: **不是任何外部产品的算法**，是本项目按「云挡不挡得住」>「月光冲不冲淡
#: 暗弱天体」的常识定的。
_STARGAZING_WEIGHTS = {"cloud_gate": 0.65, "moon_penalty": 0.35}

#: 指数分档（下限, 机器档, 中文档），从高到低匹配。
_INDEX_LEVELS = (
    (80.0, "excellent", "极佳"),
    (60.0, "good", "良好"),
    (40.0, "fair", "一般"),
    (20.0, "poor", "较差"),
    (0.0, "bad", "不适合"),
)


def stargazing_index(sky: Dict[str, Any],
                     moon: Dict[str, Any]) -> Dict[str, Any]:
    """云量 + 月光 → 0–100 综合观星指数（纯函数，经验权重）。

    诚实性约束：

    * **没有云量就不给分数**——不按晴天假设，返回 ``known=False`` 与原因。
    * 月光缺失时只按云量给分，``moon_counted=False`` 并在文本里写明
      「未计入月光」，不假装月黑。
    * 结构：``分数 = 云量分 × (0.65 + 0.35 × 月光分/100)``——云是完全遮挡时
      直接归零，月光最多扣 35%。0.65/0.35 是本项目的经验值，不对应任何
      外部产品算法，也不作科学依据；只代表**采样时刻**，不外推整夜。
    """
    base: Dict[str, Any] = {
        "schema": "mbdsdr.stargazing-index/1",
        "known": False,
        "score": None,
        "level": "unknown",
        "level_zh": "未知",
        "cloud_score": None,
        "moon_score": None,
        "moon_counted": False,
        "weights": dict(_STARGAZING_WEIGHTS),
        "at": "",
        "text": "",
        "reason": "",
    }

    cloud_pct = None
    if isinstance(sky, dict) and sky.get("known"):
        cloud_pct = sky.get("cloud_cover_pct")
    if cloud_pct is None:
        base["reason"] = "缺少云量实况，不给观星指数（不按晴天假设）"
        return base
    try:
        cloud_pct = float(cloud_pct)
    except (TypeError, ValueError):
        base["reason"] = "云量不是数值，不给观星指数"
        return base
    if not math.isfinite(cloud_pct):
        base["reason"] = "云量不是有限数，不给观星指数"
        return base

    cloud_pct = min(max(cloud_pct, 0.0), 100.0)
    cloud_score = 100.0 - cloud_pct
    gate = _STARGAZING_WEIGHTS["cloud_gate"]
    penalty = _STARGAZING_WEIGHTS["moon_penalty"]

    parts = [f"云量 {cloud_pct:.0f}%"]
    illuminated = altitude = None
    if isinstance(moon, dict) and moon.get("known"):
        try:
            illuminated = float(moon.get("illumination"))
            altitude = float(moon.get("alt_deg"))
        except (TypeError, ValueError):
            illuminated = altitude = None
    if illuminated is not None and altitude is not None and \
            math.isfinite(illuminated) and math.isfinite(altitude):
        if altitude < 0.0:
            moon_score = 100.0
            parts.append("月亮在地平线下")
        else:
            # 高度越高、照亮越大，冲淡暗弱天体越明显；60° 以上按 60° 计。
            alt_weight = min(max(altitude, 0.0), 60.0) / 60.0
            moon_score = 100.0 - 60.0 * illuminated * alt_weight
            parts.append(f"月光 照亮 {illuminated * 100:.0f}% / 高度 {altitude:.0f}°")
        score = cloud_score * (gate + penalty * (moon_score / 100.0))
        moon_counted = True
    else:
        moon_score = None
        score = cloud_score
        moon_counted = False
        parts.append("未计入月光（缺月球采样）")

    score = round(min(max(score, 0.0), 100.0), 1)
    level, level_zh = "bad", "不适合"
    for floor, machine, zh in _INDEX_LEVELS:
        if score >= floor:
            level, level_zh = machine, zh
            break

    base.update({
        "known": True,
        "score": score,
        "level": level,
        "level_zh": level_zh,
        "cloud_score": round(cloud_score, 1),
        "moon_score": None if moon_score is None else round(moon_score, 1),
        "moon_counted": moon_counted,
        "at": str(sky.get("observed_at") or ""),
        "text": "；".join(parts),
    })
    if not moon_counted:
        base["reason"] = "月光未计入，分数只反映云量"
    return base


def assess_tonight(snapshot: Optional[Dict[str, Any]],
                   lat_deg: Optional[float] = None,
                   lon_deg: Optional[float] = None,
                   moon: Optional[Dict[str, Any]] = None,
                   at: Optional[datetime] = None) -> Dict[str, Any]:
    """环境快照 → 今夜可看性。纯函数：同一快照重复调用结果完全相同。

    ``known`` 只表示**云量这一条路**能不能给出结论。极光可能单独未知
    （没有 Kp），此时 ``aurora.chance == "unknown"`` 并给原因，不影响
    ``sky``；反过来也一样。``moon`` 是调用方给的**单时刻**采样，
    不参与云量判定，也不外推整夜。``at`` 是观测时刻，只喂给流星雨
    活动期判定（按 UTC 日期），缺省则该条如实「未判断」。
    """
    notes = [
        "云量取的是采集时刻的实况，不是整夜预报；后半夜转云本模块不知道。",
        "极光判断只用 Kp 与地磁纬度的经验门槛，不是极光椭圆模型。",
        "月光只按采样时刻算，不覆盖整夜；月亮可能后半夜升起或落下。",
        "流星雨只给活动期与峰值 ZHR，不给「今晚每小时几颗」；按 UTC 日期判定。",
    ]
    if not snapshot:
        sky = _assess_sky({})
        moon_assessed = _assess_moon(moon)
        return {
            "schema": TONIGHT_SCHEMA,
            "known": False,
            "verdict": "今夜可看性未知（原因：没有环境快照，未联网，不猜）",
            "sky": sky,
            "aurora": {"known": False, "chance": "unknown", "kp": None,
                       "reason": "没有环境快照，不猜极光"},
            "moon": moon_assessed,
            "index": stargazing_index(sky, moon_assessed),
            "meteor_showers": _assess_showers(at),
            "notes": notes,
        }

    station = snapshot.get("station") or {}
    lat = lat_deg if lat_deg is not None else station.get("lat")
    lon = lon_deg if lon_deg is not None else station.get("lon")

    sky = _assess_sky(_sample(snapshot, "weather"))
    aurora = _assess_aurora(_sample(snapshot, "kp"), lat, lon)
    moon_assessed = _assess_moon(moon)

    if sky["known"]:
        verdict = sky["text"]
    else:
        verdict = "今夜可看性未知（" + sky["reason"] + "）"

    return {
        "schema": TONIGHT_SCHEMA,
        "known": bool(sky["known"]),
        "taken_at": snapshot.get("taken_at") or "",
        "verdict": verdict,
        "sky": sky,
        "aurora": aurora,
        "moon": moon_assessed,
        "index": stargazing_index(sky, moon_assessed),
        "meteor_showers": _assess_showers(at),
        "notes": notes,
    }


_CHANCE_ZH = {
    "likely": "很可能",
    "possible": "有可能",
    "marginal": "处于边缘",
    "unlikely": "基本看不到",
    "unknown": "未知",
}


def format_tonight_section(report: Dict[str, Any]) -> str:
    """把判定渲染成多行文本（含时刻与来源，缺测时写明缺什么）。"""
    lines = ["今夜可看性：" + str(report.get("verdict", ""))]
    index = report.get("index") or {}
    if index.get("known"):
        lines.append(f"- 观星指数 {index.get('score'):g}/100"
                     f"（{index.get('level_zh')}）：{index.get('text')}"
                     "（经验权重，非权威评分，只代表采样时刻）")
    elif index.get("reason"):
        lines.append("- 观星指数：未知（" + str(index["reason"]) + "）")
    sky = report.get("sky") or {}
    if sky.get("known"):
        when = sky.get("observed_at") or "时刻缺失"
        lines.append(f"- 云量 = {sky.get('cloud_cover_pct'):g}%"
                     f"（观测 {when}，来源 {sky.get('source')} {sky.get('url')}）")
    elif sky.get("reason"):
        lines.append("- " + str(sky["reason"]))

    aurora = report.get("aurora") or {}
    if aurora.get("known"):
        lines.append(
            f"- 极光：{_CHANCE_ZH.get(aurora.get('chance'), '未知')}"
            f"（Kp={aurora.get('kp'):g}，地磁纬度 "
            f"{aurora.get('geomagnetic_lat_deg'):g}°，"
            f"门槛 Kp≈{aurora.get('kp_needed'):g}）")
    elif aurora.get("reason"):
        lines.append("- 极光：" + str(aurora["reason"]))

    moon = report.get("moon") or {}
    if moon.get("known"):
        when = moon.get("at") or "时刻缺失"
        lines.append(f"- 月光：{moon.get('text')}"
                     f"（采样 {when}，来源 {moon.get('source')}）")
    elif moon.get("reason"):
        lines.append("- 月光：" + str(moon["reason"]))

    showers = report.get("meteor_showers") or {}
    if showers.get("known"):
        when = showers.get("at") or "日期缺失"
        lines.append(f"- 流星雨（UTC {when}）：{showers.get('text')}")
    elif showers.get("reason"):
        lines.append("- 流星雨：" + str(showers["reason"]))

    for n in report.get("notes") or []:
        lines.append("注：" + n)
    return "\n".join(lines)

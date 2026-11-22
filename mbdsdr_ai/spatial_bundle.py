# SPDX-License-Identifier: MIT
"""离线观测包（``mbdsdr.spatial/1``）：一次观测档案的头显可读快照。

诚实口径
--------
* **包是档案的引用 + 派生资产**，不改档案 schema；时间轴原样搬运
  （含 ``samples``），不在此处换算 ``at_s``——那是 ``replay_plan`` 的职责。
* **未知即未知**：站址未知时三个数值一律 ``None``（绝不写 0,0）；频率/
  采样率缺失保持 ``None``。
* **纯函数**：除 ``audio`` 条目的存在性检查外不读盘、不抛栈、不碰设备。
  是否有可回放的 IQ 由调用方通过 ``spectrum`` 入参表达（CLI 读盘后传入），
  或用档案里已登记的产物路径判断。
* **阻塞项直说**：缺站址 / 空时间轴 / 无 IQ / 录音文件不存在逐条进
  ``blockers``；``passes`` 为空不算阻塞（只是没有过境数据）。

星表：本模块不内置星表，也不凭空生成星点。``stars`` 由调用方给出
（CLI 默认用 ``skyengine`` 单例星表的 ``brightest_stars``，或 ``--stars-json``
从外部 JSON 读）。``sky.source`` 如实标注来源：``"hyg"``（随仓 HYG v4.1
星表）/ ``"external"``（外部 JSON）/ ``"none"``（不附星）。

星点地平坐标
------------
站址（``gnss``/``manual``）与观测时刻都可用时，每颗星附 ``alt_deg`` /
``az_deg`` / ``visible``。这两个角是**几何**值（``sky.altaz_source =
"geometric"``，``sky.altaz_refraction = false``）：不做大气折射，方位角以
**北 = 0°、东 = 90°** 递增（即从北向东量），取值 ``[0, 360)``。缺站址或
缺时刻时不写这些键，``altaz_source`` 为 ``"unavailable"``。

自行：星条目带 ``hyg_id`` 且自行侧车可用时，先按 ``propagate_radec_many``
把 J2000 赤经/赤纬传播到观测时刻，再算 alt/az；查不到/NaN 的星按静态处理。
``sky.altaz_pm_applied`` / ``sky.altaz_pm_missing`` 如实记录参与地平计算的
星里传播成功 / 未传播的条数（站址或时刻不可用时都为 0）。**输出的
``ra_deg``/``dec_deg`` 仍是 J2000**；只有 alt/az 含自行，且与桌面
``star_catalog.select_for_view`` 同源同口径（几何、不含折射）。
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .astronomy import jd_to_lst, unix_to_jd
from .skyengine.proper_motion import (
    load_pm_table,
    pm_for_hyg_ids,
    propagate_radec_many,
)

__all__ = ["SCHEMA", "build_spatial_bundle"]

SCHEMA = "mbdsdr.spatial/1"

#: 可采信站址的来源（与 ``observation_record.replay_plan`` 同一口径）。
_SITE_SOURCES = ("gnss", "manual")

#: 过境历时上限：近地目标单次过境通常 < 15 min，>5 h 已无可信轨道解释。
_MAX_PASS_DURATION_S = 5 * 3600

#: TLE 历元年龄上限：超过 30 天即视为过旧（位置误差已不足以支撑过境时刻）。
_MAX_TLE_AGE_DAYS = 30.0

#: 默认站址不足时的提示（spec「未知站址」）。
_NO_SITE = "没有可用站址（source={source}）"
_NO_TIME_FOR_ALTAZ = "没有观测时刻，无法换算 alt/az"
_NO_IQ = "没有可回放的 IQ 数据"
_NO_TIMELINE = "时间轴为空，没有可重演的事件"
_NO_AUDIO = "录音文件不存在：{path}"


def _num(value: Any) -> Optional[float]:
    """数值化：``None``/布尔/不可解析/非有限值一律 ``None``（不猜）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _text(value: Any) -> str:
    """字符串化：``None`` → 空串；其它类型用 ``str``（不臆造内容）。"""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _opt_text(value: Any) -> Optional[str]:
    """可空字符串：非字符串/``None`` 返回 ``None``。"""
    return value if isinstance(value, str) else None


def _count(value: Any) -> Optional[int]:
    """整数计数：``None``/布尔/不可解析一律 ``None``（不猜 0）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_utc(events: Any) -> Optional[str]:
    """时间轴里最早的合法 ``utc``（真实记录值，不编造）。"""
    stamps = [e.get("utc") for e in (events or [])
              if isinstance(e, dict) and isinstance(e.get("utc"), str)
              and e.get("utc").strip()]
    return min(stamps) if stamps else None


def _unix_of_utc(value: Any) -> Optional[float]:
    """ISO8601 字符串 → Unix 秒；缺时区按 UTC；解析不出返回 ``None``。"""
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


def _altaz_geometric(ra_deg: float, dec_deg: float, lat_deg: float,
                     lon_deg: float, jd_ut1: float) -> Tuple[float, float]:
    """赤道→地平的**几何**换算（不做大气折射）。

    方位角以**北 = 0°、东 = 90°** 递增，返回 ``(alt_deg, az_deg)``，
    ``az_deg`` 归一到 ``[0, 360)``。天顶附近方位角数学上退化（取 0）。
    """
    lst = jd_to_lst(jd_ut1, lon_deg)
    ha = lst - math.radians(ra_deg)
    lat = math.radians(lat_deg)
    dec = math.radians(dec_deg)

    sin_alt = (math.sin(lat) * math.sin(dec)
               + math.cos(lat) * math.cos(dec) * math.cos(ha))
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_deg = math.degrees(math.asin(sin_alt))

    # 北=0、东=90：y 轴指向东、x 轴指向北的天顶投影。
    y = -math.cos(dec) * math.sin(ha)
    x = (math.cos(lat) * math.sin(dec)
         - math.sin(lat) * math.cos(dec) * math.cos(ha))
    az_deg = math.degrees(math.atan2(y, x)) % 360.0
    return alt_deg, az_deg


def _iq_declared(record: Any) -> bool:
    """档案是否登记了 IQ 产物（只看已落档的元数据，不探磁盘）。"""
    for p in getattr(record, "products", None) or []:
        if not isinstance(p, dict):
            continue
        if p.get("data_path"):
            return True
        # recorder.py 的 meta 不带 core:dataset，按命名约定推同名数据文件
        # （与 ``replay_context`` 同一规则）。
        if str(p.get("path") or "").endswith(".sigmf-meta"):
            return True
    return False


def _stars_payload(stars: Any, max_stars: Any) -> Tuple[List[Dict[str, Any]],
                                                       int, bool]:
    """按星等升序取前 ``max_stars`` 颗；返回 (条目, 截断前总数, 是否截断)。

    每个条目固定带 ``name/ra_deg/dec_deg/mag/hyg_id``；``hyg_id`` 缺失或
    非法时为 ``None``（外部 ``--stars-json`` 常没有它，此时按静态星处理）。
    """
    items: List[Dict[str, Any]] = []
    for s in stars or []:
        if not isinstance(s, dict):
            continue
        items.append({
            "name": _text(s.get("name")),
            "ra_deg": _num(s.get("ra_deg")),
            "dec_deg": _num(s.get("dec_deg")),
            "mag": _num(s.get("mag")),
            "hyg_id": _count(s.get("hyg_id")),
        })
    # 缺星等的排最后（未知不等于最暗），同星等保持输入顺序。
    items.sort(key=lambda s: (s["mag"] is None, s["mag"] or 0.0))
    try:
        limit = max(0, int(max_stars))
    except (TypeError, ValueError):
        limit = 2000
    total = len(items)
    return items[:limit], total, total > limit


def _pass_payload(passes: Any) -> List[Dict[str, Any]]:
    """规整过境条目：六字段 + ``tle_age_days`` / ``suspect`` / ``suspect_reasons``。

    六字段缺失即 ``None``（不补 0、不猜时刻）；``tle_age_days`` 为可选输入，
    有则保留（溯源用），缺失写作 ``None``。疑点只打标、**不删条目**，排序
    仍只按 ``start_utc`` 升序（缺 ``start_utc`` 排最后）。
    """
    items: List[Dict[str, Any]] = []
    for p in passes or []:
        if not isinstance(p, dict):
            continue
        start = _opt_text(p.get("start_utc"))
        end = _opt_text(p.get("end_utc"))
        max_alt = _num(p.get("max_alt_deg"))
        duration = _num(p.get("duration_s"))
        tle_age = _num(p.get("tle_age_days"))

        reasons: List[str] = []
        if duration is not None and duration > _MAX_PASS_DURATION_S:
            reasons.append(
                f"历时异常（>{int(_MAX_PASS_DURATION_S // 3600)} h）")
        if tle_age is not None and tle_age > _MAX_TLE_AGE_DAYS:
            reasons.append(f"TLE 过旧（{tle_age:.1f} 天）")
        if max_alt is None:
            reasons.append("缺少最大仰角")
        if duration is not None and duration <= 0:
            reasons.append("历时非正")
        if start is None or end is None:
            reasons.append("缺少起止时刻")

        items.append({
            "norad_id": _count(p.get("norad_id")),
            "name": _opt_text(p.get("name")),
            "start_utc": start,
            "end_utc": end,
            "max_alt_deg": max_alt,
            "duration_s": duration,
            "tle_age_days": tle_age,
            "suspect": bool(reasons),
            "suspect_reasons": reasons,
        })
    items.sort(key=lambda x: (x["start_utc"] is None, x["start_utc"] or ""))
    return items


def _spectrum_payload(record: Any, spectrum: Any,
                      blockers: List[str]) -> Dict[str, Any]:
    """规整频谱段；无帧时把「为什么没有」写进 blockers。

    ``source`` 只在确有帧时为 ``"recorded"``（帧来自真实录制 IQ）；
    0 帧时为 ``"none"``，避免头显在空态旁声称来源是「录制回放」。
    """
    spec_in = spectrum if isinstance(spectrum, dict) else {}
    frames: List[Dict[str, Any]] = []
    for f in spec_in.get("frames") or []:
        if not isinstance(f, dict):
            continue
        db: List[float] = []
        for v in f.get("db") or []:
            x = _num(v)
            if x is not None:
                db.append(x)
        frames.append({"at_s": _num(f.get("at_s")), "db": db})

    error = _opt_text(spec_in.get("error"))
    if error and error.strip():
        blockers.append(error.strip())
    elif not frames and not _iq_declared(record):
        blockers.append(_NO_IQ)

    tuning = getattr(record, "tuning", None) or {}
    center = _num(spec_in.get("center_freq_hz"))
    if center is None:
        center = _num(tuning.get("center_freq_hz"))
    rate = _num(spec_in.get("sample_rate_hz"))
    if rate is None:
        rate = _num(tuning.get("sample_rate_hz"))
    # 总样本数：入参优先，否则取已登记产物的 samples 之和（只加非 None）。
    total_samples = _count(spec_in.get("total_samples"))
    if total_samples is None:
        counts = [_count(p.get("samples"))
                  for p in (getattr(record, "products", None) or [])
                  if isinstance(p, dict)]
        counts = [c for c in counts if c is not None]
        total_samples = sum(counts) if counts else None
    return {
        "center_freq_hz": center,
        "sample_rate_hz": rate,
        "frames": frames,
        "duration_s": _num(spec_in.get("duration_s")),
        "total_samples": total_samples,
        # 有帧才算「录制回放」；0 帧时写 "none"，避免头显在空态旁声称来源是录制回放。
        "source": "recorded" if frames else "none",
    }


def _audio_payload(audio: Any, blockers: List[str]) -> List[Dict[str, Any]]:
    """原样搬运音频条目；文件不在时保留条目并如实记阻塞。"""
    out: List[Dict[str, Any]] = []
    for a in audio or []:
        if not isinstance(a, dict):
            continue
        path = _text(a.get("path"))
        out.append({
            "path": path,
            "label": _text(a.get("label")),
            "utc": _opt_text(a.get("utc")),
        })
        if not path or not os.path.exists(path):
            blockers.append(_NO_AUDIO.format(path=path))
    return out


def build_spatial_bundle(record, *, stars=None, passes=None, spectrum=None,
                         audio=None, max_stars=2000, stars_total=None,
                         stars_source=None) -> Dict[str, Any]:
    """把一次观测档案导出成 ``mbdsdr.spatial/1`` 观测包（纯函数）。

    Args:
        record: :class:`mbdsdr_ai.observation_record.ObservationRecord`。
        stars: ``[{"name","ra_deg","dec_deg","mag","hyg_id"}]``；按星等升序
            截断。``hyg_id`` 可选（缺失/非法写 ``None``），有且自行侧车可用
            时 alt/az 会先传播 J2000 赤经/赤纬；条目里的 ``ra_deg``/``dec_deg``
            始终是 J2000。
        passes: 过境条目列表；为空不算阻塞。每项统一为
            ``norad_id / name / start_utc / end_utc / max_alt_deg /
            duration_s``，缺失字段保留 ``None``（不补 0），按 ``start_utc``
            升序排列。可选 ``tle_age_days`` 原样保留（溯源用）；每项另附
            ``suspect`` / ``suspect_reasons``（历时异常、TLE 过旧等只打标，
            不删条目）。
        spectrum: ``{"frames","center_freq_hz","sample_rate_hz","duration_s",
            "total_samples"}``；可选 ``"error"`` 说明为什么没有帧（如读盘失败
            的真实原因）。``total_samples`` 缺省时取 ``record.products`` 里
            所有非 None ``samples`` 之和，全缺则为 ``None``。
        audio: ``[{"path","label","utc"}]``；存在性在此检查。
        max_stars: 星点上限，默认 2000（spec 性能预算）。
        stars_total: 截断前的星数（如星表命中总数）；None 时取 ``stars``
            输入条数。给出时 ``sky.truncated`` 按「总数 > 实际条数」判定。
        stars_source: ``"hyg"`` / ``"external"`` / ``"none"``；None 时按
            ``stars`` 是否非空推成 ``"external"`` / ``"none"``。

    Returns:
        观测包 dict，键名严格按 spec「离线观测包契约」。
    """
    blockers: List[str] = []

    site_raw = getattr(record, "site", None) or {}
    if not isinstance(site_raw, dict):
        site_raw = {}
    source = site_raw.get("source")
    site_source = "" if source is None else str(source)
    if site_source in _SITE_SOURCES:
        lat = _num(site_raw.get("lat_deg"))
        lon = _num(site_raw.get("lon_deg"))
        alt = _num(site_raw.get("alt_m"))
    else:
        # 站址不可用时三个数值保持 None：不按 0,0 渲染。
        lat = lon = alt = None
        blockers.append(_NO_SITE.format(source=source))

    time_raw = getattr(record, "time", None) or {}
    if not isinstance(time_raw, dict):
        time_raw = {}
    timeline_raw = getattr(record, "timeline", None) or []
    started = getattr(record, "started_utc", None)
    utc = started if isinstance(started, str) and started.strip() else None
    if utc is None:
        utc = _first_utc(timeline_raw)

    timeline = [dict(e) for e in timeline_raw if isinstance(e, dict)]
    if not timeline:
        blockers.append(_NO_TIMELINE)

    star_items, total_stars, truncated = _stars_payload(stars, max_stars)

    # 地平坐标：站址与观测时刻都可用才算（几何值，不含折射）。
    altaz_jd: Optional[float] = None
    if utc is None:
        blockers.append(_NO_TIME_FOR_ALTAZ)
    elif lat is not None and lon is not None:
        unix = _unix_of_utc(utc)
        if unix is None:
            blockers.append(_NO_TIME_FOR_ALTAZ)
        else:
            altaz_jd = unix_to_jd(unix)
    altaz_pm_applied = 0
    altaz_pm_missing = 0
    if altaz_jd is None:
        altaz_source = "unavailable"
    else:
        altaz_source = "geometric"
        # 自行侧车只在确有带 hyg_id 的星时加载；不可用则全体静态。
        pm_table = None
        if any(s["hyg_id"] is not None for s in star_items):
            pm_table = load_pm_table()
        for s in star_items:
            if s["ra_deg"] is None or s["dec_deg"] is None:
                continue
            ra = s["ra_deg"]
            dec = s["dec_deg"]
            applied = False
            if s["hyg_id"] is not None and pm_table is not None:
                pmra, pmdec = pm_for_hyg_ids([s["hyg_id"]], pm_table)
                if math.isfinite(float(pmra[0])) and \
                        math.isfinite(float(pmdec[0])):
                    ra, dec = propagate_radec_many(
                        ra, dec, float(pmra[0]), float(pmdec[0]), altaz_jd)
                    applied = True
            if applied:
                altaz_pm_applied += 1
            else:
                altaz_pm_missing += 1
            alt, az = _altaz_geometric(ra, dec, lat, lon, altaz_jd)
            s["alt_deg"] = alt
            s["az_deg"] = az
            s["visible"] = alt > 0.0

    if stars_total is not None:
        try:
            total_stars = max(0, int(stars_total))
        except (TypeError, ValueError):
            total_stars = len(star_items)
        # 总数不能小于实际条目数（否则报「没截断」却少给了星）。
        total_stars = max(total_stars, len(star_items))
        truncated = total_stars > len(star_items)
    source = (stars_source if isinstance(stars_source, str) and stars_source
              else ("external" if star_items else "none"))
    pass_items = _pass_payload(passes)

    spectrum_out = _spectrum_payload(record, spectrum, blockers)
    audio_out = _audio_payload(audio, blockers)

    return {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "observation_id": _text(getattr(record, "observation_id", "")),
        "site": {
            "lat_deg": lat,
            "lon_deg": lon,
            "alt_m": alt,
            "source": site_source,
        },
        "time": {
            "utc": utc,
            "source": _opt_text(time_raw.get("source")),
            "precision": _opt_text(time_raw.get("precision")),
        },
        "sky": {
            "stars": star_items,
            "passes": pass_items,
            "total_stars": total_stars,
            "truncated": truncated,
            "source": source,
            "altaz_source": altaz_source,
            "altaz_refraction": False,
            "altaz_pm_applied": altaz_pm_applied,
            "altaz_pm_missing": altaz_pm_missing,
        },
        "spectrum": spectrum_out,
        "audio": audio_out,
        "timeline": timeline,
        "evidence": _text(getattr(record, "evidence", "")),
        "blockers": blockers,
    }

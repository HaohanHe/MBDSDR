# SPDX-License-Identifier: MIT
"""Pure adapter from astronomy recommendations to desktop search-result rows.

No widgets, settings, weather, hardware, or current-clock reads. Coordinates use
exactly the sky renderer's J2000 -> alt/az chain, without atmospheric refraction.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from numbers import Real

from mbdsdr_ai.celestial_geometry import (
    GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec,
)


@dataclass(frozen=True)
class TargetResults:
    """Rows in source order (planets/moon first), diagnostics, and fixed scope."""

    rows: list[dict[str, object]]
    errors: tuple[str, ...] = ()
    caveat: str = field(
        default="所选时刻的计算候选；非整夜预报，天气、遮挡和器材条件未评估",
        init=False,
    )


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name}必须提供有限数值（不默认站址）")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name}超出有限数值范围") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name}必须为有限数值")
    return number


def build_target_results(
    jd: float,
    lat: float,
    lon: float,
    *,
    planet_labels: Mapping[str, str] | None = None,
    limit: int = 12,
) -> TargetResults:
    """Build search rows at the selected JD and explicit east-positive site.

    Backend thresholds stay at their defaults: planets/moon 15 degrees, DSO
    20 degrees. Recheck those thresholds in the renderer's coordinate frame.
    ``planet_labels`` comes from the caller's existing planet styles; missing
    labels fall back to the backend key. RA/Dec (degrees, J2000) and magnitude
    retain source values; az_deg/alt_deg are geometric, not measured angles.

    Invalid/missing coordinates or a non-integer/negative limit return no rows
    and nonempty errors. Zero coordinates and limit=0 are legal. Source runtime
    and data errors retain the other source's rows; initialization failure can
    affect both because astronomy eagerly loads its catalog. Empty rows with
    errors must NOT be presented as "no matches". Type/attribute/key/assertion
    programming errors propagate. Both sources are checked even at limit=0.
    """
    try:
        jd = _finite_number(jd, "儒略日")
        lat = _finite_number(lat, "纬度")
        lon = _finite_number(lon, "经度")
        if not -90.0 <= lat <= 90.0:
            raise ValueError("纬度必须在 [-90, 90] 度内")
        if not -180.0 <= lon <= 180.0:
            raise ValueError("经度必须在 [-180, 180] 度内")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit 必须为非负整数")
    except ValueError as exc:
        return TargetResults(rows=[], errors=(str(exc),))

    # The astronomy package eagerly validates its bundled DSO catalog. Defer
    # that work until after input validation; broken local data must yield a
    # diagnostic result rather than make this desktop adapter unimportable.
    try:
        from mbdsdr_ai.astronomy import az_to_compass, dec_to_dms, dso, planets
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        return TargetResults(rows=[], errors=(
            f"天文候选来源初始化失败：{type(exc).__name__}: {exc}",
        ))

    station = GroundStation(longitude_deg=lon, latitude_deg=lat)
    labels = planet_labels if planet_labels is not None else {}
    rows = []
    errors = []
    sources = (
        ("planet", "行星/月球", planets.recommend_targets, 15.0),
        ("dso", "深空天体", dso.recommend_dso, 20.0),
    )
    for kind, source_label, recommend, threshold in sources:
        # Catch only source runtime/data failures. Row construction stays outside
        # this handler so broken field names/types and adapter bugs are visible.
        try:
            entries = recommend(jd, lat, observer_lon_deg=lon)
        except (OSError, ValueError, RuntimeError, ArithmeticError) as exc:
            errors.append(f"{source_label}候选获取失败：{type(exc).__name__}: {exc}")
            continue
        for entry in entries:
            try:
                ra = _finite_number(entry.ra_deg, "赤经")
                dec = _finite_number(entry.dec_deg, "赤纬")
                az, alt = azalt_from_vec(j2000_to_altaz(
                    vec_from_radec(ra, dec), station, jd))
                if not math.isfinite(az) or not math.isfinite(alt):
                    raise ValueError("转换后的方位/高度非有限数值")
            except (ValueError, ArithmeticError) as exc:
                errors.append(f"{source_label} {entry.name} 坐标不可用：{exc}")
                continue
            if alt < threshold:
                continue
            row = {
                "kind": kind,
                "key": entry.name,
                "ra": entry.ra_deg,
                "dec": entry.dec_deg,
                "magnitude": entry.magnitude,
                "az_deg": az,
                "alt_deg": alt,
                "sub": f"{az_to_compass(az)} · 高度{dec_to_dms(alt)}（几何）",
            }
            if kind == "planet":
                zh = labels.get(entry.name) or entry.name
                row.update(kind_label="行星", label=zh, zh=zh)
            else:
                row.update(kind_label="深空天体", label=entry.zh_name or entry.name,
                           dso_type=entry.dso_type, con=entry.constellation)
            rows.append(row)
    return TargetResults(rows=rows[:limit], errors=tuple(errors))

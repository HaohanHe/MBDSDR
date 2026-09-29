# SPDX-License-Identifier: MIT
"""MBDSDR 卫星追踪与过境预测。

依据公开轨道/跟踪方法独立实现：
  * SGP4 传播 — Spacetrack Report #3（经 sgp4 库）。
  * 坐标链 TEME→ECEF(GMST)→站心 ENU，给出仰角/方位/距离/视线速度。
  * 过境预测 — 粗扫定位仰角跨越阈值 → 二分收敛 → 细采样找最大仰角。
  * 多普勒 — 接收频率偏移 = -v_r/c · f_carrier。

本模块在 mbdsdr_ai/orbit.py 的坐标链上封装：
  * SatelliteTracker  — 单星 SGP4 传播、实时仰角/方位/距离/视线速度/多普勒
  * PassPredictor      — 给定 TLE+地面站，预测未来 N 小时所有过境
  * doppler_shift_hz   -v_r/c * f_carrier（实时接收频率偏移）

真实 TLE 在线拉取 + 磁盘缓存（orbit.fetch_tle）；无网用缓存；无缓存则显空。
SatDump、gpredict 等开源项目仅作技术参考与致谢，本仓未包含其源代码。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sgp4.api import Satrec

# 复用上级包 orbit.py 的坐标链（SGP4/GMST 标准实现）
from .. import orbit  # mbdsdr_ai.orbit


# --------------------------------------------------------------------------- #
# 地面站
# --------------------------------------------------------------------------- #
@dataclass
class GroundStation:
    """地面站（QTH）。单位：度 / km。"""
    lat: float
    lon: float
    alt_km: float = 0.0
    name: str = "QTH"

    def as_tuple(self) -> Tuple[float, float, float]:
        return (self.lat, self.lon, self.alt_km)


# 已知下行频率（Hz）— NOAA/MetOp/FY 公开下行频率表
DOWNLINK_FREQUENCIES: Dict[str, float] = {
    "NOAA-15": 137.6200e6,
    "NOAA-18": 137.9125e6,
    "NOAA-19": 137.1000e6,
    "METOP-A": 1701.300e6,
    "METOP-B": 1707.000e6,
    "METOP-C": 1707.000e6,
    "FENGYUN-3B": 1704.500e6,
    "FENGYUN-3C": 1704.500e6,
    "FENGYUN-3D": 1704.500e6,
}


def unix_to_jd(t_unix: float) -> float:
    """Unix 秒 → UTC 儒略日（与 orbit.py 一致）。"""
    return t_unix / 86400.0 + 2440587.5


# --------------------------------------------------------------------------- #
# SatelliteTracker
# --------------------------------------------------------------------------- #
class SatelliteTracker:
    """单星追踪器。封装 sgp4 Satrec + 站心观测。

    用法:
        tr = SatelliteTracker("NOAA-19", line1, line2)
        st = tr.position_at(time.time(), gs)
        dop = tr.doppler_at(time.time(), gs, 137.1e6)
    """

    def __init__(self, name: str, line1: str, line2: str,
                 norad: int = 0):
        self.name = name
        self.line1 = line1
        self.line2 = line2
        self.norad = norad
        self.sat = Satrec.twoline2rv(line1, line2)
        self.epoch = line1[18:32].strip()

    # ---- 构造辅助 ----
    @classmethod
    def from_norad(cls, norad: int, name: str = "",
                   max_age_hours: float = 24.0) -> "SatelliteTracker":
        """按 NORAD 编号在线取 TLE（带缓存）。无网/无缓存抛异常。"""
        l1, l2 = orbit.fetch_tle(norad, max_age_hours=max_age_hours)
        return cls(name or f"NORAD-{norad}", l1, l2, norad=norad)

    # ---- 核心：任意时刻站心状态 ----
    def position_at(self, t_unix: float, gs: GroundStation) -> Optional[Dict[str, Any]]:
        """在 t_unix(unix 秒) 给出卫星相对 gs 的观测状态。

        返回 dict: elevation_deg / azimuth_deg / range_km / range_rate_kms /
                   altitude_km / ecef_km (x,y,z)。传播失败返回 None。
        """
        jd = unix_to_jd(t_unix)
        st = orbit._state_from_satrec(
            self.sat, self.name, jd, gs.lat, gs.lon, gs.alt_km,
            epoch=self.epoch)
        return st

    def ecef_at(self, t_unix: float) -> Optional[Tuple[float, float, float]]:
        """卫星 ECEF 坐标 (km)。用于与第三方库交叉验证。"""
        jd_utc = unix_to_jd(t_unix)
        jd_int = int(jd_utc)
        fr = jd_utc - jd_int
        e, r_teme, _v = self.sat.sgp4(jd_int, fr)
        if e != 0:
            return None
        gmst = orbit._gmst_days(jd_utc)
        cg, sg = math.cos(-gmst), math.sin(-gmst)
        x = cg * r_teme[0] - sg * r_teme[1]
        y = sg * r_teme[0] + cg * r_teme[1]
        return (x, y, r_teme[2])

    # ---- 多普勒 ----
    def doppler_at(self, t_unix: float, gs: GroundStation,
                   f_carrier_hz: float) -> Optional[Dict[str, float]]:
        """实时多普勒。接收频率偏移 = -v_los/c * f_carrier（远离为负）。"""
        st = self.position_at(t_unix, gs)
        if st is None:
            return None
        v_los = st["range_rate_kms"]            # km/s, 远离为正
        shift_hz = -f_carrier_hz * v_los / orbit.C_LIGHT
        return {
            "t_unix": t_unix,
            "range_rate_kms": v_los,
            "doppler_shift_hz": shift_hz,
            "tuned_freq_hz": f_carrier_hz + shift_hz,
            "elevation_deg": st["elevation"],
            "azimuth_deg": st["azimuth"],
        }

    def is_visible(self, t_unix: float, gs: GroundStation,
                   min_el_deg: float = 0.0) -> bool:
        st = self.position_at(t_unix, gs)
        return bool(st and st["elevation"] >= min_el_deg)


# --------------------------------------------------------------------------- #
# PassPredictor
# --------------------------------------------------------------------------- #
class PassPredictor:
    """预测未来 N 小时过境。

    方法：「粗扫 60s 定位仰角跨越阈值 → 二分收敛到 0.25s → pass 内 2s 细采样
    找 max_el」，输出任务书要求的字段。
    """

    def __init__(self, tracker: SatelliteTracker, gs: GroundStation):
        self.tracker = tracker
        self.gs = gs

    def _el(self, t_unix: float) -> Optional[float]:
        st = self.tracker.position_at(t_unix, self.gs)
        return st["elevation"] if st else None

    def predict(self, hours: float = 4.0, min_el_deg: float = 0.0,
                t_start: Optional[float] = None,
                carrier_hz: Optional[float] = None) -> List[Dict[str, Any]]:
        """返回未来 hours 小时所有 max_el>=min_el 的过境列表。

        每项: {aos, los, max_el, max_el_t, doppler_at_aos, aos_az, los_az,
               duration_s}（unix 秒 + 度）。
        """
        t0 = t_start if t_start is not None else time.time()
        t_end = t0 + hours * 3600.0
        coarse_step = 60.0
        fine_step = 2.0

        # 粗扫
        coarse: List[Tuple[float, Optional[float]]] = []
        t = t0
        while t <= t_end:
            coarse.append((t, self._el(t)))
            t += coarse_step
        if all(e is None for _, e in coarse):
            return []

        passes: List[Dict[str, Any]] = []
        i = 0
        n = len(coarse)
        while i < n:
            ti, ei = coarse[i]
            if ei is None or ei < min_el_deg:
                i += 1
                continue
            j = i
            while j < n and coarse[j][1] is not None and coarse[j][1] >= min_el_deg:
                j += 1
            # AOS: 在 coarse[i-1] 与 coarse[i] 间二分
            if i > 0 and coarse[i - 1][1] is not None:
                aos = orbit._bisect_threshold(self._el, coarse[i - 1][0], ti, min_el_deg)
            else:
                aos = ti
            # LOS: 在 coarse[j-1] 与 coarse[j] 间二分
            if j < n and coarse[j][1] is not None:
                los = orbit._bisect_threshold(self._el, coarse[j - 1][0], coarse[j][0], min_el_deg)
            else:
                los = coarse[j - 1][0]

            # pass 内细采样找最大仰角
            best_el = -90.0
            best_t = aos
            ts = aos
            while ts <= los + 1e-6:
                el = self._el(ts)
                if el is not None and el > best_el:
                    best_el = el
                    best_t = ts
                ts += fine_step

            st_aos = self.tracker.position_at(aos, self.gs) or {}
            st_los = self.tracker.position_at(los, self.gs) or {}
            dop_aos: Optional[float] = None
            if carrier_hz is not None:
                d = self.tracker.doppler_at(aos, self.gs, carrier_hz)
                dop_aos = d["doppler_shift_hz"] if d else None

            passes.append({
                "satellite": self.tracker.name,
                "aos": aos,
                "los": los,
                "max_el": best_el,
                "max_el_t": best_t,
                "aos_az": st_aos.get("azimuth", float("nan")),
                "los_az": st_los.get("azimuth", float("nan")),
                "duration_s": los - aos,
                "doppler_at_aos": dop_aos,
            })
            i = j
        return sorted(passes, key=lambda p: p["aos"])

    def next_pass_countdown(self, hours: float = 6.0,
                            min_el_deg: float = 0.0) -> Optional[Dict[str, Any]]:
        """下一次过境倒计时（秒）。无过境返回 None。"""
        now = time.time()
        upcoming = [p for p in self.predict(hours=hours, min_el_deg=min_el_deg)
                    if p["los"] > now]
        if not upcoming:
            return None
        nxt = min(upcoming, key=lambda p: p["aos"])
        return {
            "seconds_to_aos": max(0.0, nxt["aos"] - now),
            "pass": nxt,
        }


# --------------------------------------------------------------------------- #
# 增强：AI 自动选星 + 与 rf_sky_view 联动
# --------------------------------------------------------------------------- #
def auto_detect_satellite(freq_hz: float, tol_hz: float = 15e3) -> Optional[Dict[str, Any]]:
    """按下行频率识别卫星类型，返回 {name, norad, decoder, freq}。

    MetOp/FY 目前只返回元数据，解码器后续接入。
    """
    table = [
        ("NOAA-15", 25338, "noaa_apt", 137.6200e6),
        ("NOAA-18", 28654, "noaa_apt", 137.9125e6),
        ("NOAA-19", 33591, "noaa_apt", 137.1000e6),
        ("METOP-A", 38771, "metop_ahrpt", 1701.300e6),
        ("METOP-B", 49998, "metop_ahrpt", 1707.000e6),
        ("METOP-C", 76109, "metop_ahrpt", 1707.000e6),
        ("FENGYUN-3D", 54234, "fengyun_mpt", 1704.500e6),
    ]
    for name, norad, decoder, f in table:
        if abs(freq_hz - f) <= tol_hz:
            return {"name": name, "norad": norad,
                    "decoder": decoder, "freq_hz": f}
    return None


def tune_command(tracker: SatelliteTracker, gs: GroundStation,
                 t_unix: Optional[float] = None) -> Dict[str, Any]:
    """与 rf_sky_view 联动：选中卫星 → 返回应调谐频率（含实时多普勒）。"""
    if t_unix is None:
        t_unix = time.time()
    f_down = DOWNLINK_FREQUENCIES.get(tracker.name)
    if f_down is None:
        return {"error": f"未知卫星 {tracker.name} 的下行频率"}
    d = tracker.doppler_at(t_unix, gs, f_down)
    if d is None:
        return {"error": "轨道传播失败"}
    return {
        "satellite": tracker.name,
        "base_freq_hz": f_down,
        "doppler_shift_hz": d["doppler_shift_hz"],
        "tuned_freq_hz": d["tuned_freq_hz"],
        "elevation_deg": d["elevation_deg"],
        "azimuth_deg": d["azimuth_deg"],
    }

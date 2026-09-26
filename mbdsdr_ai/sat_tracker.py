"""
MBDSDR AI 内核 - 卫星闭环自动跟踪器
====================================
在 :mod:`mbdsdr_ai.sat_passes`（过境预测）之上，增加"实时闭环跟踪"能力：

  选一颗卫星 -> 实时计算方位/仰角/斜距/多普勒 -> 自动把 SDR 频率调到
  下行频率（含多普勒校正）-> 仰角 < 5° 提示过境结束。

与 sat_passes 的分工：
  - sat_passes.predict_passes：未来若干小时的过境事件预报（离线、批量）。
  - 本模块 SatelliteTracker：选中单颗卫星后，按当前时刻逐点给出
    az/el/range/doppler，供 UI 500ms 轮询驱动自动调谐。

坐标/传播链与 sat_passes 完全一致（skyfield EarthSatellite + Topos），
多普勒符号约定沿用 gSatWrapper.cpp:177 / Satellite.cpp:942：

    range_rate > 0  卫星远离地面站 -> 接收频率降低 -> doppler_hz < 0
    range_rate < 0  卫星接近地面站 -> 接收频率升高 -> doppler_hz > 0
    f_corrected = f_downlink + doppler_hz = f_downlink * (1 - v_range/c)

TLE 来源（不造假）：
  1) 内置 6 颗常用气象/业余卫星的真实 TLE（Celestrak 抓取，见 BUILTIN_TLE_DATE）；
  2) 用户 .tle 文件；
  3) "更新 TLE" 从 Celestrak active.txt 在线刷新内置目录。
下载失败时保留内置 TLE，绝不编造轨道根数。
"""
from __future__ import annotations

import os
from typing import Optional, List, Dict, Sequence

from skyfield.api import EarthSatellite, Time

# 复用 sat_passes 已经过验证的核心：地面站数据类、过境预测、TLE 解析、
# 站心方位/仰角/斜距/径向速度计算、光速常量。本模块不重写坐标数学。
from .sat_passes import (
    GroundStation,
    SatellitePass,
    predict_passes,
    make_timescale,
    _parse_tle,
    _range_and_rate,
    C_LIGHT_MPS,
)

__all__ = ["SatelliteTracker", "BUILTIN_DOWNLINK_HZ", "BUILTIN_TLE_DATE"]


# ---------------------------------------------------------------------------
# 卫星下行频率表（科学/业余频段标准值）
#
# 这是卫星本身的标称下行（信标/转发器接收）频率，不是任何"地区电台"，
# 可安全内置。单位 Hz。用于多普勒校正后的接收频率计算：
#   f_corrected = f_downlink + doppler_hz
# ---------------------------------------------------------------------------
BUILTIN_DOWNLINK_HZ: Dict[str, float] = {
    "NOAA 15": 137.620e6,    # APT 气象云图
    "NOAA 18": 137.9125e6,   # APT 气象云图
    "NOAA 19": 137.100e6,    # APT 气象云图
    "ISS (ZARYA)": 145.800e6,  # ARISS 业余中继/数据包下行
    "FUNBCUBE-1 (AO-73)": 145.935e6,  # FUNcube 遥测下行（BPSK）
    "RADFXSAT (FOX-1B)": 145.960e6,   # AO-91 Fox-1B 下行
}

# 内置 TLE 的抓取日期（标注在 UI 上，提醒用户过期后点"更新 TLE"）。
BUILTIN_TLE_DATE = "2026-09-25"

# 内置默认 TLE（Celestrak 真实抓取，历元 2026-09-25，NORAD active 目录）。
# 每项为 3 行：标题行 + line1 + line2。
_BUILTIN_TLES: List[List[str]] = [
    [
        "NOAA 15",
        "1 25338U 98030A   26268.86268050  .00000085  00000+0  52080-4 0  9991",
        "2 25338  98.5049 286.7672 0011219  56.3023 303.9227 14.27170971475651",
    ],
    [
        "NOAA 18",
        "1 28654U 05018A   26268.90422616  .00000041  00000+0  45056-4 0  9991",
        "2 28654  98.8049 347.0872 0013570 221.3821 138.6324 14.13744708100547",
    ],
    [
        "NOAA 19",
        "1 33591U 09005A   26268.85039999  .00000028  00000+0  38838-4 0  9994",
        "2 33591  98.9431 339.7518 0014811 104.9296 255.3517 14.13486977908701",
    ],
    [
        "ISS (ZARYA)",
        "1 25544U 98067A   26268.43198945  .00011731  00000+0  21853-3 0  9991",
        "2 25544  51.6316 163.9608 0004776 179.9710 180.1280 15.49288785587293",
    ],
    [
        "FUNBCUBE-1 (AO-73)",
        "1 39444U 13066AE  26268.90734682  .00006282  00000+0  37820-3 0  9993",
        "2 39444  97.8394 242.6132 0034052 218.3055 141.5755 15.10704629694860",
    ],
    [
        "RADFXSAT (FOX-1B)",
        "1 43017U 17073E   26268.57795708  .00010034  00000+0  42505-3 0  9991",
        "2 43017  97.4532 133.7415 0146377 342.2183  17.3955 15.14053388480574",
    ],
]

# 默认过境结束阈值（仰角，度）。
DEFAULT_MIN_ELEVATION_DEG = 5.0


def _split_tle_blocks(text: str) -> List[List[str]]:
    """把多行 TLE 文本切分成 [(name, line1, line2), ...] 块。

    兼容 Celestrak active.txt 的标准 3 行格式；跳过空行与无法成对
    line1/line2 的残块。解析不出的块直接丢弃，不造假。
    """
    lines = [l.rstrip() for l in text.splitlines()]
    blocks: List[List[str]] = []
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].startswith("1 "):
            # 往前找标题行（line1 前面紧邻的非 "2 " 行）
            title = ""
            j = i - 1
            while j >= 0 and not lines[j].startswith("2 ") and not lines[j].startswith("1 "):
                title = lines[j].strip()
                break
            if i + 1 < n and lines[i + 1].startswith("2 "):
                l1, l2 = lines[i], lines[i + 1]
                blocks.append([title or ("NORAD " + l2[2:7].strip()), l1, l2])
            i += 2
        else:
            i += 1
    return blocks


class SatelliteTracker:
    """单颗卫星的实时闭环跟踪器。

    Parameters
    ----------
    ground_station:
        观测者位置（lat/lon/alt）。为 None 时表示"未定位"，
        此时不做任何轨道计算，:meth:`current_position` 返回 valid=False，
        调用方据此提示"需要观测者位置"，绝不编造坐标。
    """

    def __init__(self, ground_station: Optional[GroundStation] = None):
        self.ground_station = ground_station
        self._ts = make_timescale()
        # 目录：[{name, norad_id, line1, line2}]
        self._catalog: List[Dict[str, object]] = []
        self._selected_name: Optional[str] = None
        self._sat: Optional[EarthSatellite] = None
        self._diff = None           # (sat - topo) 的几何对象
        self._downlink_freq_hz: float = 0.0
        self._tle_date: str = BUILTIN_TLE_DATE

        # 启动即加载内置真实 TLE（失败不崩：单条无效跳过）。
        for tle in _BUILTIN_TLES:
            self.load_tle(tle)

    # ------------------------------------------------------------------ TLE
    @property
    def tle_date(self) -> str:
        """当前内置/最近一次更新 TLE 的日期标注。"""
        return self._tle_date

    def load_tle(self, tle_lines: Sequence[str]) -> bool:
        """加载一条 TLE（标题行可选 + line1 + line2）。成功返回 True。"""
        parsed = _parse_tle(tle_lines)
        if parsed is None:
            return False
        name, line1, line2 = parsed
        norad_id = line2[2:7].strip()
        # 同名/同 NORAD 编号视为刷新旧条目
        self._catalog = [
            c for c in self._catalog
            if c["name"] != name and str(c["norad_id"]) != norad_id
        ]
        self._catalog.append({
            "name": name,
            "norad_id": norad_id,
            "line1": line1,
            "line2": line2,
        })
        # 若刷新的是当前选中星，重建传播器
        if self._selected_name == name:
            self._build_satellite(name)
        return True

    def load_satellites_from_file(self, path: str) -> int:
        """从文件加载 TLE（单星 3 行 或 多星 block 均可）。返回成功加载条数。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return 0
        count = 0
        for block in _split_tle_blocks(text):
            if self.load_tle(block):
                count += 1
        return count

    def update_tle_from_celestrak(self, timeout: float = 5.0) -> int:
        """从 Celestrak active.txt 在线刷新内置目录的 TLE。

        只刷新目录里已有 NORAD 编号对应的条目（保持下拉列表规模可控）。
        网络失败/超时/返回异常时静默保留内置 TLE，返回 0，绝不崩、绝不编造。
        """
        try:
            import requests
        except Exception:
            return 0
        try:
            resp = requests.get(
                "https://celestrak.com/NORAD/elements/active.txt",
                timeout=timeout,
            )
            resp.raise_for_status()
            text = resp.text
        except Exception:
            return 0

        # norad_id -> (name, line1, line2)
        fresh: Dict[str, List[str]] = {}
        for block in _split_tle_blocks(text):
            name, l1, l2 = block[0], block[1], block[2]
            nid = l2[2:7].strip()
            fresh[nid] = [name, l1, l2]

        updated = 0
        for entry in self._catalog:
            nid = str(entry["norad_id"])
            if nid in fresh:
                name, l1, l2 = fresh[nid]
                entry["name"] = name
                entry["line1"] = l1
                entry["line2"] = l2
                updated += 1
        if updated > 0:
            from datetime import datetime, timezone
            self._tle_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            # 重建当前选中星的传播器（TLE 已变）
            if self._selected_name is not None:
                self._build_satellite(self._selected_name)
        return updated

    # ------------------------------------------------------------- 目录查询
    def list_satellites(self) -> List[Dict[str, object]]:
        """返回已加载卫星列表：[{name, norad_id, tle: [line1, line2]}]。"""
        return [
            {
                "name": c["name"],
                "norad_id": c["norad_id"],
                "tle": [c["line1"], c["line2"]],
            }
            for c in self._catalog
        ]

    def downlink_freq_for(self, name: str) -> float:
        """按卫星名查内置下行频率；未知卫星返回 0.0。"""
        return BUILTIN_DOWNLINK_HZ.get(name, 0.0)

    # ------------------------------------------------------------- 选中卫星
    def _build_satellite(self, name: str) -> bool:
        """根据 name 找目录条目，构造 skyfield 传播器与站心几何对象。"""
        entry = next((c for c in self._catalog if c["name"] == name), None)
        if entry is None:
            return False
        try:
            sat = EarthSatellite(entry["line1"], entry["line2"], entry["name"], self._ts)
        except Exception:
            self._sat = None
            self._diff = None
            return False
        self._sat = sat
        if self.ground_station is not None:
            from skyfield.api import Topos
            topo = Topos(
                latitude_degrees=self.ground_station.lat_deg,
                longitude_degrees=self.ground_station.lon_deg,
                elevation_m=self.ground_station.alt_m,
            )
            self._diff = sat - topo
        else:
            self._diff = None
        self._selected_name = name
        # 自动套用内置下行频率（若有）
        f = self.downlink_freq_for(name)
        if f > 0:
            self._downlink_freq_hz = f
        return True

    def select_satellite(self, name_or_id: str) -> bool:
        """按名称或 NORAD 编号选中一颗卫星，创建传播器。成功返回 True。"""
        name_or_id = str(name_or_id).strip()
        entry = next(
            (c for c in self._catalog
             if c["name"] == name_or_id or str(c["norad_id"]) == name_or_id),
            None,
        )
        if entry is None:
            return False
        return self._build_satellite(entry["name"])

    @property
    def selected_name(self) -> Optional[str]:
        return self._selected_name

    def set_downlink_freq(self, freq_hz: float) -> None:
        """设置卫星下行（标称接收）频率，Hz。多普勒据此计算。"""
        self._downlink_freq_hz = float(freq_hz)

    @property
    def downlink_freq_hz(self) -> float:
        return self._downlink_freq_hz

    # ------------------------------------------------------------- 实时位置
    def current_position(self) -> Dict[str, object]:
        """计算当前时刻的 az/el/斜距/多普勒/校正后频率。

        无观测者位置、未选卫星或传播失败时返回 valid=False（各数值字段为 None），
        调用方据此空转，绝不抛异常、绝不编造。
        """
        empty: Dict[str, object] = {
            "valid": False,
            "azimuth": None,
            "elevation": None,
            "range_km": None,
            "range_rate_km_s": None,
            "doppler_hz": None,
            "doppler_rate_hz_s": None,
            "corrected_freq_hz": None,
            "is_visible": False,
        }
        if self.ground_station is None or self._diff is None or self._sat is None:
            return empty

        t = self._ts.now()
        r = _range_and_rate(self._diff, t)
        if r is None:
            return empty
        az, el, rng_km, rr_kms, _ = r

        # Satellite.cpp:942: f_d = -freq * (rangeRate_mps / c)
        # rr_kms>0 远离 -> doppler_hz<0；rr_kms<0 接近 -> doppler_hz>0
        dop = -rr_kms * 1000.0 / C_LIGHT_MPS * self._downlink_freq_hz

        # 多普勒变化率：与 1 秒后比较（Hz/s），供 AFC 参考
        dop_rate = 0.0
        try:
            t2 = self._ts.tt_jd(t.tt + 1.0 / 86400.0)
            r2 = _range_and_rate(self._diff, t2)
            if r2 is not None:
                dop2 = -r2[3] * 1000.0 / C_LIGHT_MPS * self._downlink_freq_hz
                dop_rate = dop2 - dop
        except Exception:
            dop_rate = 0.0

        return {
            "valid": True,
            "azimuth": float(az),
            "elevation": float(el),
            "range_km": float(rng_km),
            "range_rate_km_s": float(rr_kms),
            "doppler_hz": float(dop),
            "doppler_rate_hz_s": float(dop_rate),
            "corrected_freq_hz": float(self._downlink_freq_hz + dop),
            "is_visible": float(el) >= DEFAULT_MIN_ELEVATION_DEG,
        }

    def is_visible(self, min_elevation: float = DEFAULT_MIN_ELEVATION_DEG) -> bool:
        """当前仰角是否 >= 阈值（默认 5°）。"""
        pos = self.current_position()
        if not pos["valid"] or pos["elevation"] is None:
            return False
        return float(pos["elevation"]) >= min_elevation

    # ------------------------------------------------------------- 下一次过境
    def next_pass(self, min_alt: float = DEFAULT_MIN_ELEVATION_DEG,
                  hours: float = 24.0) -> Optional[SatellitePass]:
        """预测选中卫星未来 ``hours`` 小时内的下一次过境（AOS→中天→LOS）。

        未选卫星/无观测者位置/无过境返回 None。
        """
        if self.ground_station is None or self._selected_name is None:
            return None
        entry = next((c for c in self._catalog if c["name"] == self._selected_name), None)
        if entry is None:
            return None
        try:
            passes = predict_passes(
                [entry["line1"], entry["line2"]],
                self.ground_station,
                hours=hours,
                min_alt=min_alt,
            )
        except Exception:
            return None
        return passes[0] if passes else None

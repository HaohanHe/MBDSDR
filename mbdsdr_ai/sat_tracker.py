# SPDX-License-Identifier: MIT
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
多普勒符号约定（远离为正）：

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
from datetime import datetime, timezone
from typing import Optional, List, Dict, Sequence

try:
    from skyfield.api import EarthSatellite, Time
    _HAS_SKYFIELD = True
except ImportError:  # skyfield 属 satellite 可选依赖组；缺它模块仍可导入
    EarthSatellite = None  # type: ignore[assignment,misc]
    Time = None  # type: ignore[assignment,misc]
    _HAS_SKYFIELD = False

from .sat_passes import _require_skyfield  # noqa: E402  (统一的诚实报错口径)
from .tle_integrity import (  # noqa: E402
    accuracy,
    freshness,
    tracking_policy,
    verify_identity,
)
from .tle_fetch import classify_failure, failure_copy  # noqa: E402

# 复用 sat_passes 已经过验证的核心：地面站数据类、过境预测、TLE 解析、
# 站心方位/仰角/斜距/径向速度计算、光速常量。本模块不重写坐标数学。
from .sat_passes import (
    GroundStation,
    SatellitePass,
    PASS_MIN_ALT_DEG,
    VISIBLE_MIN_ALT_DEG,
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

# 可见/跟踪退出阈值（仰角，度）。**不是过境列表的筛选门**：
# 列表用 sat_passes.PASS_MIN_ALT_DEG（10°）。见 sat_passes.py 常量段。
DEFAULT_MIN_ELEVATION_DEG = VISIBLE_MIN_ALT_DEG

# TLE 刷新：请求（DNS+连接+读）墙钟硬截止，覆盖 requests timeout 管不到的
# getaddrinfo 阶段（黑洞网络下 DNS 可无限阻塞）。
_TLE_HARD_DEADLINE_MARGIN_S = 5.0


def _bounded_requests_get(url: str, timeout_s: float, hard_deadline_s: float):
    """daemon 线程内 requests.get，join 硬截止；超时抛 TimeoutError。

    ``timeout=(connect, read)`` 只管 socket 阶段，DNS 解析不在其内，故外层
    再用墙钟硬截止兜底，保证调用方在有限时间内拿到明确的「超时」失败，
    而不是挂住整个 UI/进程。
    """
    import threading

    import requests

    box: Dict[str, object] = {}

    def _run() -> None:
        try:
            box["r"] = requests.get(url, timeout=(timeout_s, timeout_s))
        except Exception as e:  # noqa: BLE001 - 原样上抛给调用方
            box["e"] = e

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(hard_deadline_s)
    if worker.is_alive():
        raise TimeoutError(
            f"联网刷新超时（>{hard_deadline_s:.1f}s，含 DNS 解析）：{url}")
    if "e" in box:
        raise box["e"]  # type: ignore[misc]
    return box["r"]


def _engine_now_unix() -> Optional[float]:
    """全局 TimeEngine 当前 Unix 秒；不可用（扁平导入）时返回 None 走墙钟。"""
    try:
        from .new_spacetime import get_time_engine
    except ImportError:
        try:
            from new_spacetime import get_time_engine
        except ImportError:
            return None
    try:
        return float(get_time_engine().now_unix())
    except Exception:  # noqa: BLE001 - 时间引擎异常时退墙钟，不让跟踪崩
        return None


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
        # P10：刷新结果留痕（取新失败不静默）与身份冲突清单
        self._last_refresh_error: str = ""
        # 结构化失败（code/label/reason/action）：状态栏只该展示这份文案，
        # 不许再写与 code 无关的固定句子。
        self._last_refresh_failure: Optional[Dict[str, object]] = None
        self._identity_conflicts: List[Dict[str, object]] = []

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
            "identity_status": "no_data" if not name else "verified",
            "source": "builtin_or_user",
        })
        self._annotate_entry(self._catalog[-1])
        # 若刷新的是当前选中星，重建传播器
        if self._selected_name == name:
            self._build_satellite(name)
        return True

    @staticmethod
    def _annotate_entry(entry: Dict[str, object]) -> None:
        """按 TLE 历元给目录条目打新鲜度/精度标记（显示可，默认跟踪需谨慎）。"""
        fresh = freshness(str(entry.get("line1", "")),
                          entry.get("fetched_at") if isinstance(entry.get("fetched_at"), (int, float)) else None)
        acc = accuracy(fresh)
        entry["tle_tier"] = fresh["tier"]
        entry["tle_tier_label"] = fresh["label"]
        entry["tle_age_days"] = fresh["age_days"]
        entry["tle_epoch_utc"] = fresh.get("epoch_utc")
        entry["pass_eligible"] = fresh["pass_eligible"]
        entry["accuracy_risk"] = acc["risk"]
        entry["accuracy_note"] = acc["note"]

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

    def update_tle_from_celestrak(self, timeout: float = 5.0,
                                  hard_deadline_s: Optional[float] = None) -> int:
        """从 Celestrak active.txt 在线刷新内置目录的 TLE。

        只刷新目录里已有 NORAD 编号对应的条目（保持下拉列表规模可控）。
        网络失败/超时/返回异常时保留旧值并记入 ``last_refresh_error``
        （不静默），返回 0。编号下的对象名与目录声明不符时**不采用**新数据，
        标记 ``identity_status="mismatch"`` 并记入 ``identity_conflicts``。

        超时是三层：连接/读由 requests 的 ``(connect, read)`` 元组管，
        「整体墙钟」由 ``hard_deadline_s``（默认 timeout+5s）管，覆盖
        requests 管不到的 DNS 阶段——黑洞网络下也必然在上限内返回明确失败。

        P12：``MBDSDR_OFFLINE=1`` 时**不发起任何网络请求**，直接如实报
        「离线模式，未刷新」——离线语义只免联网，不冒充成功。
        """
        from .orbit import _network_disabled
        if _network_disabled():
            detail = "离线模式（MBDSDR_OFFLINE），未联网刷新（沿用旧 TLE）"
            self._last_refresh_error = detail
            self._last_refresh_failure = failure_copy("offline", detail)
            return 0
        try:
            import requests  # noqa: F401 - 可用性探测放在真正发起请求前
        except Exception:
            detail = "requests 不可用，未刷新（沿用旧 TLE）"
            self._last_refresh_error = detail
            self._last_refresh_failure = failure_copy("tle_unknown", detail)
            return 0
        deadline = (float(hard_deadline_s) if hard_deadline_s is not None
                    else float(timeout) + _TLE_HARD_DEADLINE_MARGIN_S)
        # 数据源：CelesTrak GP 分组接口（celestrak.org 域名），按需只取几组，
        # 不再拖 active.txt 全表（几 MB、含全部在轨对象）。
        # 分组覆盖本目录用到的星：业余（ISS/AMSAT）、气象（NOAA/METEOR/FY）、
        # 空间站。换星只改这里的分组名，不改代码结构。
        _groups = ("amateur", "weather", "stations")
        chunks: List[str] = []
        try:
            for _g in _groups:
                resp = _bounded_requests_get(
                    f"https://celestrak.org/NORAD/elements/gp.php"
                    f"?GROUP={_g}&FORMAT=tle",
                    float(timeout), deadline)
                resp.raise_for_status()
                if resp.text:
                    chunks.append(resp.text)
            text = "\n".join(chunks)
            if not text.strip():
                raise ValueError("GP 接口返回空响应")
        except Exception as e:  # noqa: BLE001 - 网络失败如实留痕
            self._last_refresh_error = \
                f"联网刷新失败（{type(e).__name__}: {e}），沿用旧 TLE"
            classified = classify_failure(e)
            self._last_refresh_failure = failure_copy(
                str(classified.get("kind") or "tle_unknown"),
                str(classified.get("detail") or self._last_refresh_error),
                classified.get("status"))
            return 0

        # norad_id -> (name, line1, line2)
        fresh: Dict[str, List[str]] = {}
        for block in _split_tle_blocks(text):
            name, l1, l2 = block[0], block[1], block[2]
            nid = l2[2:7].strip()
            fresh[nid] = [name, l1, l2]

        if not fresh:
            # HTTP 成功但响应体没有可解析 TLE：这不是「0 颗更新」，是解析失败/
            # 空响应，必须带码留痕，否则状态栏会把坏数据说成「未匹配」。
            failure = classify_failure(body=text)
            self._last_refresh_failure = failure_copy(
                str(failure.get("kind") or "parse_failure"),
                str(failure.get("detail") or ""), failure.get("status"))
            self._last_refresh_error = (
                f"响应无可解析 TLE：{self._last_refresh_failure['reason']}")
            return 0

        updated = 0
        self._last_refresh_error = ""
        self._last_refresh_failure = None
        for entry in self._catalog:
            nid = str(entry["norad_id"])
            if nid not in fresh:
                continue
            name, l1, l2 = fresh[nid]
            ident = verify_identity(str(entry["name"]), name)
            if ident["status"] == "mismatch":
                # 编号实际指向另一颗/另一类对象：保留旧值但降级、留痕，不采用
                entry["identity_status"] = "mismatch"
                entry["identity_detail"] = ident["reason"]
                entry["object_name"] = name
                self._identity_conflicts.append({
                    "declared_name": str(entry["name"]),
                    "object_name": name,
                    "norad_id": nid,
                    "reason": ident["reason"],
                })
                self._annotate_entry(entry)
                continue
            entry["name"] = name
            entry["line1"] = l1
            entry["line2"] = l2
            entry["identity_status"] = "verified"
            entry["identity_detail"] = ident["reason"]
            entry["object_name"] = name
            entry["source"] = "celestrak"
            self._annotate_entry(entry)
            updated += 1
        if updated > 0:
            from datetime import datetime, timezone
            self._tle_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            # 重建当前选中星的传播器（TLE 已变）
            if self._selected_name is not None:
                self._build_satellite(self._selected_name)
        return updated

    @property
    def last_refresh_error(self) -> str:
        """最近一次联网刷新的失败原因（空串 = 成功或未尝试）。"""
        return self._last_refresh_error

    @property
    def last_refresh_failure(self) -> Optional[Dict[str, object]]:
        """最近一次联网刷新的结构化失败（``code/label/reason/action``）。

        成功或未尝试返回 None。桌面状态栏只渲染这份文案，确保错误码从
        抓取链路一路传到用户眼前，不在中途被固定句子替换。
        """
        return dict(self._last_refresh_failure) if self._last_refresh_failure else None

    @property
    def identity_conflicts(self) -> List[Dict[str, object]]:
        """刷新时发现的编号↔名称不符清单（空列表 = 无冲突）。"""
        return list(self._identity_conflicts)

    # ------------------------------------------------------------- 目录查询
    def list_satellites(self) -> List[Dict[str, object]]:
        """返回已加载卫星列表（含真实性/新鲜度标记，供面板如实展示）。"""
        return [
            {
                "name": c["name"],
                "norad_id": c["norad_id"],
                "tle": [c["line1"], c["line2"]],
                "identity_status": c.get("identity_status", "no_data"),
                "tle_tier": c.get("tle_tier", "unknown"),
                "tle_tier_label": c.get("tle_tier_label", "未知"),
                "tle_age_days": c.get("tle_age_days"),
                "tle_epoch_utc": c.get("tle_epoch_utc"),
                "pass_eligible": c.get("pass_eligible", False),
                "accuracy_risk": c.get("accuracy_risk", "unusable"),
                "accuracy_note": c.get("accuracy_note", ""),
                "degraded": c.get("identity_status") == "mismatch",
            }
            for c in self._catalog
        ]

    def downlink_freq_for(self, name: str) -> float:
        """按卫星名查内置下行频率；未知卫星返回 0.0。"""
        return BUILTIN_DOWNLINK_HZ.get(name, 0.0)

    def tracking_policy_for(self, name: Optional[str] = None) -> Dict[str, object]:
        """跟踪闸门的**唯一对外入口**（P0-01）。

        规则实现只有 ``tle_integrity.tracking_policy()`` 一处；本方法只做
        「目录条目 → (新鲜度, 身份状态)」的取数，不复制任何档位判断。
        面板、天空动作总线、AI 工具都应读这里的 ``allow_tune`` /
        ``allow_gimbal`` / ``command_allowed``，不得自行比较 ``tle_tier``。

        ``name`` 缺省取当前选中星；目录里没有该条目（或未选星）时按
        ``unknown`` 返回全禁止——缺数据不冒充可信。
        """
        target = name if name is not None else self._selected_name
        entry = (next((c for c in self._catalog if c["name"] == target), None)
                 if target else None)
        if entry is None:
            return tracking_policy(None, "no_data")
        fresh = {
            "tier": entry.get("tle_tier", "unknown"),
            "age_days": entry.get("tle_age_days"),
            "reason": entry.get("accuracy_note") or "",
        }
        return tracking_policy(fresh, str(entry.get("identity_status", "no_data")))

    # ------------------------------------------------------------- 选中卫星
    def _build_satellite(self, name: str) -> bool:
        """根据 name 找目录条目，构造 skyfield 传播器与站心几何对象。"""
        entry = next((c for c in self._catalog if c["name"] == name), None)
        if entry is None:
            return False
        if entry.get("identity_status") == "mismatch":
            # 红线：编号指向另一颗对象，拒绝拿错轨道做跟踪
            self._sat = None
            self._diff = None
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
    def current_position(self, at_unix: Optional[float] = None) -> Dict[str, object]:
        """计算当前时刻的 az/el/斜距/多普勒/校正后频率（严格/控制口径）。

        ``at_unix`` 显式注入 Unix UTC 秒（测试/回放钉死时钟）；缺省取全局
        TimeEngine（未做时间穿越时等于系统时钟），与过境预测、天空视图同一时间源。

        无观测者位置、未选卫星或传播失败时返回 valid=False（各数值字段为 None），
        调用方据此空转，绝不抛异常、绝不编造。

        本入口被面板显示、RX 调谐、云台跟随与 TX 预偏共用，保持严格口径；
        纯只读展示请用 :meth:`current_position_for_calculation`。
        """
        return self._position(at_unix, self.station_status())

    def current_position_for_calculation(
            self, at_unix: Optional[float] = None) -> Dict[str, object]:
        """只读/计算口径的实时位置（显式 (0,0)/海上坐标放行并带 warning）。"""
        return self._position(at_unix, self.station_status_for_calculation())

    def _position(self, at_unix: Optional[float],
                  st: Dict[str, object]) -> Dict[str, object]:
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
            "station_untrusted": None,
            "station_reasons": [],
            "station_warning": "",
            "clock_warning": "",
            # P0-01：闸门随位置一起透出，调用方只读布尔，不再自判档位
            "policy": self.tracking_policy_for(),
        }
        if self.ground_station is None or self._diff is None or self._sat is None:
            return empty
        # P11 红线：严格口径下站址不可信（0,0/海上/缺失）时不跟踪、不输出方位
        if not st["trusted"]:
            empty["station_untrusted"] = True
            empty["station_reasons"] = st["reasons"]
            return empty
        try:
            from .time_basis import get_clock_guard
            cguard = get_clock_guard().observe()
        except Exception:
            cguard = {"type": "ok"}
        clock_warning = (cguard.get("warning", "")
                         if cguard.get("type") != "ok" else "")

        entry = next((c for c in self._catalog
                      if c["name"] == self._selected_name), {})
        if at_unix is None:
            at_unix = _engine_now_unix()
        if at_unix is None:
            t = self._ts.now()
        else:
            t = self._ts.utc(datetime.fromtimestamp(float(at_unix),
                                                    tz=timezone.utc))
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
            # P10：新鲜度/精度风险随位置一起透出（过期 TLE 不可盲目跟踪）
            "tle_tier": entry.get("tle_tier", "unknown"),
            "tle_age_days": entry.get("tle_age_days"),
            "accuracy_risk": entry.get("accuracy_risk", "unusable"),
            "accuracy_note": entry.get("accuracy_note", ""),
            "pass_eligible": entry.get("pass_eligible", False),
            "refresh_status": self._last_refresh_error or "ok",
            # P0-01：闸门随位置一起透出（dead 仍给出预测频率，但不许写硬件）
            "policy": self.tracking_policy_for(),
            "station_untrusted": False,
            "station_reasons": [],
            "station_warning": st.get("station_warning") or "",
            "clock_warning": clock_warning,
        }

    def _station_fix(self):
        """取站址数据（不含任何可信度判定，判定统一在 station.py）。"""
        from .station import StationFix
        gs = self.ground_station
        if gs is None:
            return StationFix(source="none")
        return StationFix(
            lat=gs.lat_deg, lon=gs.lon_deg, alt_m=gs.alt_m, source="user",
            origin="GroundStation")

    def station_status(self) -> Dict[str, object]:
        """当前观测者站址可信度（严格/控制口径；对外语义不变）。"""
        from .station import evaluate_station
        return evaluate_station(self._station_fix())

    def station_status_for_calculation(self) -> Dict[str, object]:
        """只读/计算口径的站址可信度（显式 (0,0)/海上坐标降为 warning）。"""
        from .station import trust_for_calculation
        return trust_for_calculation(self._station_fix())

    def is_visible(self, min_elevation: float = DEFAULT_MIN_ELEVATION_DEG,
                   at_unix: Optional[float] = None) -> bool:
        """当前仰角是否 >= 阈值（默认 5°）。``at_unix`` 可显式钉死时刻。

        只读判断，用计算口径（显式 (0,0)/海上坐标照常算）。
        """
        pos = self.current_position_for_calculation(at_unix=at_unix)
        if not pos["valid"] or pos["elevation"] is None:
            return False
        return float(pos["elevation"]) >= min_elevation

    # ------------------------------------------------------------- 下一次过境
    def next_pass(self, min_alt: float = PASS_MIN_ALT_DEG,
                  hours: float = 24.0,
                  start_unix: Optional[float] = None) -> Optional[SatellitePass]:
        """预测选中卫星未来 ``hours`` 小时内的下一次过境（AOS→中天→LOS）。

        默认门 = 对外承诺的**过境**门 ``PASS_MIN_ALT_DEG``（10°）。要按
        可见门（5°）算，必须显式传 ``VISIBLE_MIN_ALT_DEG`` 并注明用途——
        历史 bug 就是这里默认拿了 5° 去调默认 10° 的 ``predict_passes``，
        使"下一次过境"与直接预测给出两个门。

        未选卫星/无观测者位置/无过境返回 None。``start_unix`` 显式钉死起点
        （测试/回放）；缺省取 TimeEngine，与 current_position 同一时间源。
        """
        if self.ground_station is None or self._selected_name is None:
            return None
        if not self.station_status_for_calculation()["trusted"]:
            return None   # P11：只读口径下仍要求「真配了」站址（缺省/缺失）
        entry = next((c for c in self._catalog if c["name"] == self._selected_name), None)
        if entry is None:
            return None
        try:
            passes = predict_passes(
                [entry["line1"], entry["line2"]],
                self.ground_station,
                hours=hours,
                min_alt=min_alt,
                start_unix=start_unix,
            )
        except Exception:
            return None
        return passes[0] if passes else None

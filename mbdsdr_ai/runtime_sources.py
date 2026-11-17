# SPDX-License-Identifier: MIT
"""运行时数据源快照（SDR / GNSS / 时间 / 站址）。

本模块只回答「现在这些数据源各自处于什么状态、这份时间与站址是从哪来的」。
默认的 :meth:`RuntimeSources.snapshot` **只读**已有对象：不启动线程、不打开
串口、不联网、不改时间引擎；读不到就给 ``reason``，绝不填假值。

GNSS → 时间/站址的接线有两条显式入口：读监视器的
:meth:`RuntimeSources.sync_gnss`（或 :func:`apply_gnss_fix`），以及接收
天空图 fix 字典的 :meth:`RuntimeSources.record_fix`。进程级默认实例由
:func:`get_runtime_sources` 提供，桌面天空图与控制器共用同一份，因此
``controller.get_status()["runtime_sources"]`` 能看到天空入口刚写入的状态。
这里不偷偷连串口、不自动重连——串口的开关仍由
``RealGNSSMonitor`` / ``SerialGNSSReader`` 的显式 start/stop 决定。

诚实口径
--------
* ``gnss.time_source`` 复用 :mod:`mbdsdr_ai.gnss_timestamps` 的
  ``pps`` / ``nmea`` / ``ntp`` / ``none`` / ``unknown``；只有真正把 GNSS UTC
  写进时间引擎后才报 ``nmea``（本仓无 PPS 接线，故不会出现 ``pps``）。
* GNSS 断开或无有效 fix 时：时间引擎回退系统时钟、站址回退到接线前的手动/
  配置站址；``time.source`` 报 ``system``，``site.source`` 报 ``manual`` /
  ``none``，绝不把回退值标成 ``gnss``。
* 只有时刻没有日期的 GGA/``hhmmss`` 无法锚定 UTC 日期，此时**不写授时**，
  也不拿接收时刻冒充 GNSS 时间。
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

try:
    from .gnss_timestamps import (
        TIME_SOURCE_NMEA,
        TIME_SOURCE_NONE,
        TIME_SOURCE_PPS,
        TIME_SOURCE_PRECISION,
    )
except ImportError:  # 包外直接运行时降级
    from gnss_timestamps import (  # type: ignore
        TIME_SOURCE_NMEA,
        TIME_SOURCE_NONE,
        TIME_SOURCE_PPS,
        TIME_SOURCE_PRECISION,
    )

__all__ = [
    "RuntimeSources",
    "apply_gnss_fix",
    "get_runtime_sources",
    "release_gnss",
    "reset_runtime_sources",
    "snapshot",
    "utc_now_iso",
]

_TIME_SOURCES = ("pps", "nmea", "ntp", "none", "unknown")
_FIX_SOURCES = ("real", "gnss", "fix", "valid")
# 天线状态（GPTXT）：只认这三种明确状态，其余一律 unknown。
_ANTENNA_STATES = ("open", "short", "ok")

# SDR 重连状态（Task 75）：与 connected 分开，只描述"自动重连"这件事。
# ``source``（Task 78）标明 opt-in 开关来源：env / config / default / manual。
_RECONNECT_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "attempts": 0,
    "max_attempts": 0,
    "next_retry_in_s": 0.0,
    "last_error": "",
    "stopped_reason": "",
    "source": "default",
}


def _finite(value: Any) -> Optional[float]:
    """转成有限 float；非数值/NaN/Inf 返回 None。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _normalize_antenna(value: Any) -> str:
    """天线状态收敛为 ``open``/``short``/``ok``/``unknown``（不猜）。"""
    s = str(value or "").strip().lower()
    return s if s in _ANTENNA_STATES else "unknown"


def _iso_utc(ts: Any) -> Optional[str]:
    """Unix 秒 → ``YYYY-MM-DDTHH:MM:SSZ``；非法/缺失返回 None。"""
    v = _finite(ts)
    if v is None:
        return None
    return datetime.fromtimestamp(v, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def utc_now_iso() -> str:
    """当前系统 UTC（ISO Z）。仅用于状态时间戳，不参与天文计算。"""
    return _iso_utc(time.time()) or ""


def _now(now_utc: Optional[float]) -> float:
    v = _finite(now_utc)
    return time.time() if v is None else v


def _call_or_value(obj: Any, name: str) -> Any:
    """读属性；属性是可调用时调一次。任何异常都返回 None。"""
    if obj is None:
        return None
    try:
        attr = getattr(obj, name)
    except Exception:
        return None
    if callable(attr):
        try:
            return attr()
        except Exception:
            return None
    return attr


def _read_connected(obj: Any) -> bool:
    """统一读连接态：``is_connected()`` / ``is_connected`` / ``connected``。"""
    if obj is None:
        return False
    for name in ("is_connected", "connected"):
        try:
            attr = getattr(obj, name)
        except Exception:
            continue
        if callable(attr):
            try:
                attr = attr()
            except Exception:
                continue
        if attr is not None:
            return bool(attr)
    status = getattr(obj, "status", None)
    if status is not None:
        return bool(getattr(status, "connected", False))
    return False


def _read_reconnect(sdr: Any) -> Dict[str, Any]:
    """读取 SDR 自动重连状态；无监督器时给出完整空态（不伪造 enabled）。"""
    out = dict(_RECONNECT_DEFAULTS)
    getter = getattr(sdr, "sdr_reconnect_status", None)
    if not callable(getter):
        return out
    try:
        raw = getter()
    except Exception:
        return out
    if not isinstance(raw, Mapping):
        return out
    out["enabled"] = bool(raw.get("enabled", False))
    for key in ("attempts", "max_attempts"):
        try:
            out[key] = max(0, int(raw.get(key, 0)))
        except (TypeError, ValueError):
            out[key] = 0
    value = _finite(raw.get("next_retry_in_s"))
    out["next_retry_in_s"] = round(max(0.0, value), 3) if value is not None else 0.0
    out["last_error"] = str(raw.get("last_error") or "")
    out["stopped_reason"] = str(raw.get("stopped_reason") or "")
    source = str(raw.get("source") or "default")
    out["source"] = (source if source in ("env", "config", "default", "manual")
                     else "unknown")
    return out


def _as_mapping(obj: Any) -> Dict[str, Any]:
    """把 dict / dataclass 快照统一成平铺 dict（只读，不猜字段）。"""
    if isinstance(obj, Mapping):
        return dict(obj)
    if obj is None:
        return {}
    out: Dict[str, Any] = {}
    aliases = {
        "source": ("source",),
        "lat_deg": ("lat_deg", "lat", "latitude"),
        "lon_deg": ("lon_deg", "lon", "longitude"),
        "alt_m": ("alt_m", "alt", "altitude_m"),
        "accuracy_m": ("accuracy_m",),
        "timestamp": ("timestamp", "captured_at"),
        "utc_time": ("utc_time", "utc", "datetime_utc", "utc_datetime"),
        "valid": ("valid",),
        "has_pps": ("has_pps", "pps"),
        "antenna": ("antenna",),
    }
    for key, names in aliases.items():
        for name in names:
            try:
                if hasattr(obj, name):
                    out[key] = getattr(obj, name)
                    break
            except Exception:
                continue
    return out


def _map_site_source(source: Any) -> str:
    """站址来源收敛为 ``gnss`` / ``manual`` / ``none`` 三档。"""
    s = str(source or "").strip().lower()
    if s == "gnss":
        return "gnss"
    if s in ("user", "config", "place", "manual", "default"):
        return "manual"
    return "none"


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """把 datetime / ISO 串转成 tz-aware UTC；无法确定时返回 None。"""
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc)
                if value.tzinfo is None else value.astimezone(timezone.utc))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (dt.replace(tzinfo=timezone.utc)
                if dt.tzinfo is None else dt.astimezone(timezone.utc))
    return None


def _combine_nmea(utc_time: Any, date: Any) -> Optional[datetime]:
    """NMEA ``hhmmss.ss`` + ``ddmmyy`` → UTC datetime；缺任一/非法返回 None。"""
    if not isinstance(utc_time, str) or not isinstance(date, str):
        return None
    try:
        try:
            from .gnss_timestamps import combine_nmea_time_date
        except ImportError:  # 包外直接运行时降级
            from gnss_timestamps import combine_nmea_time_date  # type: ignore
        return _coerce_datetime(combine_nmea_time_date(utc_time, date))
    except Exception:
        return None


def _fix_datetime(data: Mapping[str, Any]) -> Optional[datetime]:
    """fix 字典里的完整 UTC（datetime / 带日期的 ISO 串）；仅 hhmmss 返回 None。

    优先级：``utc_datetime``（完整 UTC）→ ``datetime_utc`` →
    ``utc_time`` + ``date``（RMC 的 hhmmss+ddmmyy）→ 旧口径 ``utc``/``utc_time``
    里直接放完整 ISO。只有当天时刻没有任何日期时一律 None，绝不猜日期。
    """
    for key in ("utc_datetime", "datetime_utc"):
        dt = _coerce_datetime(data.get(key))
        if dt is not None:
            return dt
    dt = _combine_nmea(data.get("utc_time"), data.get("date"))
    if dt is not None:
        return dt
    for key in ("utc", "utc_time"):
        dt = _coerce_datetime(data.get(key))
        if dt is not None:
            return dt
    return None


def _rmc_datetime(rmc: str) -> Optional[datetime]:
    """RMC 原文 → UTC datetime（要求有效 A 状态且带日期）。"""
    try:
        from .gnss_timestamps import nmea_log_to_utc_datetime
        dt = nmea_log_to_utc_datetime([rmc])
        if dt is not None:
            return _coerce_datetime(dt)
    except Exception:
        pass
    try:
        from .new_spacetime import parse_gnss_rmc
        info = parse_gnss_rmc(rmc)
        if info and info.get("valid"):
            return _coerce_datetime(info.get("utc_time"))
    except Exception:
        pass
    return None


def _gnss_datetime(monitor: Any) -> Optional[datetime]:
    """从 GNSS 监视器取**可锚定**的 UTC 时刻（没有日期就不返回）。"""
    for name in ("get_utc_datetime", "utc_datetime"):
        dt = _coerce_datetime(_call_or_value(monitor, name))
        if dt is not None:
            return dt
    rmc = _call_or_value(monitor, "get_last_rmc")
    if isinstance(rmc, str) and rmc.strip():
        dt = _rmc_datetime(rmc.strip())
        if dt is not None:
            return dt
    pos = _call_or_value(monitor, "get_position")
    if pos is not None:
        d = _as_mapping(pos)
        for key in ("utc_time",):
            dt = _coerce_datetime(d.get(key))
            if dt is not None:
                return dt
    zda = _call_or_value(monitor, "get_last_zda")
    if isinstance(zda, str) and zda.strip():
        try:
            from .gnss_timestamps import nmea_log_to_utc_datetime
            dt = nmea_log_to_utc_datetime([zda.strip()])
        except Exception:
            dt = None
        if dt is not None:
            return _coerce_datetime(dt)
    return None


def _has_pps(monitor: Any, position: Mapping[str, Any]) -> bool:
    for name in ("has_pps", "pps"):
        val = _call_or_value(monitor, name)
        if isinstance(val, bool):
            return val
    return bool(position.get("has_pps"))


class RuntimeSources:
    """SDR / GNSS / 时间 / 站址的运行时快照与显式 GNSS 接线。

    Parameters
    ----------
    sdr :
        控制器（``SDRController``）或裸后端；用于读取连接态/设备名/错误原因。
    gnss :
        ``RealGNSSMonitor`` / ``SerialGNSSReader`` 或任意提供
        ``is_connected`` / ``get_position`` / ``get_last_rmc`` 的对象。
    time_engine :
        显式注入的时间引擎；None 时懒取 ``get_time_engine()``。
    station :
        控制器（含 ``set_ground_station_from_gnss``）或站址 dict/``StationFix``。
    """

    def __init__(
        self,
        *,
        sdr: Any = None,
        gnss: Any = None,
        time_engine: Any = None,
        station: Any = None,
    ) -> None:
        self._sdr = sdr
        self._gnss = gnss
        self._time_engine = time_engine
        self._station = station
        self._lock = threading.Lock()
        # GNSS 授时/站址的应用记录（只有在 sync_gnss 真正写成功后才非 none）
        self._time_applied = False
        self._time_source = TIME_SOURCE_NONE
        self._time_precision = TIME_SOURCE_PRECISION[TIME_SOURCE_NONE]
        self._last_time_utc: Optional[str] = None
        self._last_sync_utc: Optional[str] = None
        self._last_sync_reason = ""
        self._prev_station: Optional[Dict[str, Any]] = None
        # 天空图入口最近一次 fix（无监视器时的快照来源）
        self._fix_record: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # 显式绑定（不启动任何资源）
    # ------------------------------------------------------------------
    def set_sdr(self, sdr: Any) -> None:
        self._sdr = sdr

    def set_gnss(self, gnss: Any) -> None:
        self._gnss = gnss
        if gnss is not None:
            # 显式接入的监视器是权威来源，丢弃天空图入口的旧记录。
            with self._lock:
                self._fix_record = None

    def set_time_engine(self, time_engine: Any) -> None:
        self._time_engine = time_engine

    def set_station(self, station: Any) -> None:
        with self._lock:
            if station is not self._station:
                # 换了站址对象：旧记录/旧回退值不再属于它。
                self._fix_record = None
                self._prev_station = None
            self._station = station

    def _resolve_engine(self) -> Any:
        if self._time_engine is not None:
            return self._time_engine
        try:
            from .new_spacetime import get_time_engine
            return get_time_engine()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 读取各数据源（只读）
    # ------------------------------------------------------------------
    def _read_sdr(self, now: float) -> Dict[str, Any]:
        sdr = self._sdr
        info: Dict[str, Any] = {
            "connected": False,
            "device": None,
            "reason": "",
            "last_update_utc": None,
            "reconnect": dict(_RECONNECT_DEFAULTS),
            # 后端读停顿（设备读超时/无样本）：与 connected 独立——
            # socket/USB 还在（connected=True）也可能读不出数据。
            "stalled": False,
            "last_read_reason": "",
        }
        if sdr is None:
            info["reason"] = "未接入 SDR 控制器/后端"
            return info

        connected = _read_connected(sdr)
        info["connected"] = connected
        # 重连是独立维度：enabled/attempts 等不参与 connected 的判定。
        info["reconnect"] = _read_reconnect(sdr)
        backend = getattr(sdr, "_backend", None)
        if backend is None:
            backend = sdr
        status = getattr(backend, "status", None)

        # 读停顿只读后端自己的状态；没有该字段的后端照实给 False/空串。
        # ``last_read_reason`` 在快照里只带一句人话，完整 code/timeout_s
        # 仍留在后端的结构化原因里，避免快照形态随后端实现漂移。
        try:
            info["stalled"] = bool(getattr(backend, "stalled", False))
        except Exception:
            info["stalled"] = False
        try:
            raw_reason = getattr(backend, "last_read_reason", None)
        except Exception:
            raw_reason = None
        if isinstance(raw_reason, Mapping):
            info["last_read_reason"] = str(
                raw_reason.get("message") or raw_reason.get("code") or "")
        elif raw_reason:
            info["last_read_reason"] = str(raw_reason)

        device = getattr(sdr, "_device_name", None)
        if not device:
            dev_obj = getattr(backend, "device", None)
            if isinstance(dev_obj, str):
                device = dev_obj
            elif dev_obj is not None:
                device = (getattr(dev_obj, "name", None)
                          or getattr(dev_obj, "device_id", None))
            if not device:
                device = getattr(backend, "name", None)
        info["device"] = str(device) if device else None

        err = ""
        if status is not None:
            err = str(getattr(status, "error", "") or "")
        if not err:
            err = str(getattr(backend, "last_error", "") or "")
        if err:
            info["reason"] = err
        elif not connected:
            info["reason"] = "SDR 未连接（无可用后端）"

        age = _finite(getattr(status, "last_data_age_s", None))
        if connected and age is not None:
            info["last_update_utc"] = _iso_utc(now - age)
        if info["last_update_utc"] is None:
            info["last_update_utc"] = (
                getattr(sdr, "_sdr_last_update_utc", None)
                or getattr(sdr, "last_update_utc", None))
        return info

    def _read_gnss(self) -> Dict[str, Any]:
        obj = self._gnss
        if obj is None:
            with self._lock:
                rec = dict(self._fix_record) if self._fix_record else None
            if rec is not None:
                return self._read_fix_record(rec)
        info: Dict[str, Any] = {
            "connected": False,
            "port": None,
            "fix": False,
            "lat_deg": None,
            "lon_deg": None,
            "alt_m": None,
            "accuracy_m": None,
            "timestamp": None,
            "antenna": "unknown",
            "reason": "",
        }
        if obj is None:
            info["reason"] = "未接入 GNSS 监视器（需显式启用）"
            return info

        info["connected"] = _read_connected(obj)
        port = _call_or_value(obj, "port")
        if port is None:
            port = _call_or_value(getattr(obj, "_reader", None), "port")
        info["port"] = str(port) if port else None

        pos = _call_or_value(obj, "get_position")
        data: Dict[str, Any] = {}
        if pos is not None:
            data = _as_mapping(pos)
        lat = _finite(data.get("lat_deg"))
        lon = _finite(data.get("lon_deg"))
        src = str(data.get("source") or "").strip().lower()
        has_src = data.get("source") is not None
        valid_src = (not has_src) or src in ("real", "gnss", "fix", "valid")
        fix = bool(lat is not None and lon is not None and valid_src)
        info["fix"] = fix
        info["lat_deg"] = lat
        info["lon_deg"] = lon
        info["alt_m"] = _finite(data.get("alt_m"))
        info["accuracy_m"] = _finite(data.get("accuracy_m"))
        info["timestamp"] = _finite(data.get("timestamp"))
        info["antenna"] = _normalize_antenna(data.get("antenna"))

        if not info["connected"]:
            info["reason"] = "GNSS 未连接（时间回退系统时钟，站址回退手动/配置）"
        elif fix:
            info["reason"] = "GNSS 已连接且定位有效"
        else:
            info["reason"] = "GNSS 已连接，等待有效定位"
        return info

    @staticmethod
    def _read_fix_record(rec: Mapping[str, Any]) -> Dict[str, Any]:
        """把天空图入口记录的 fix 投影成与 ``_read_gnss`` 同形的快照。"""
        valid = bool(rec.get("valid"))
        connected = bool(rec.get("connected"))
        info: Dict[str, Any] = {
            "connected": connected,
            "port": rec.get("port"),
            "fix": valid,
            "lat_deg": rec.get("lat_deg") if valid else None,
            "lon_deg": rec.get("lon_deg") if valid else None,
            "alt_m": rec.get("alt_m") if valid else None,
            "accuracy_m": rec.get("accuracy_m") if valid else None,
            "timestamp": rec.get("timestamp"),
            "antenna": _normalize_antenna(rec.get("antenna")),
            "reason": "",
        }
        if valid:
            info["reason"] = "GNSS 已连接且定位有效（天空图入口）"
        elif connected:
            info["reason"] = "GNSS 已连接，等待有效定位"
        else:
            info["reason"] = "GNSS 无有效定位（时间回退系统时钟，站址回退手动/配置）"
        return info

    def _read_station(self) -> Dict[str, Any]:
        st = self._station
        info: Dict[str, Any] = {
            "source": "none",
            "raw_source": "none",
            "lat_deg": None,
            "lon_deg": None,
            "alt_m": None,
            "accuracy_m": None,
        }
        if st is None:
            # 没有控制器/站址对象时，天空图入口记录的有效 fix 就是当前站址。
            # 记录整体替换、不原地修改，这里直接读引用即可（避免嵌套加锁）。
            rec = dict(self._fix_record) if self._fix_record else None
            if rec and rec.get("valid"):
                info.update({
                    "source": "gnss",
                    "raw_source": "gnss",
                    "lat_deg": rec.get("lat_deg"),
                    "lon_deg": rec.get("lon_deg"),
                    "alt_m": rec.get("alt_m"),
                    "accuracy_m": rec.get("accuracy_m"),
                })
            return info
        raw = getattr(st, "_gs_source", None)
        if raw is not None:
            info["raw_source"] = str(raw or "none")
            info["source"] = _map_site_source(raw)
            info["lat_deg"] = _finite(getattr(st, "_gs_lat", None))
            info["lon_deg"] = _finite(getattr(st, "_gs_lon", None))
            info["alt_m"] = _finite(getattr(st, "_gs_alt", None))
            info["accuracy_m"] = _finite(getattr(st, "_gs_accuracy_m", None))
            return info
        data = _as_mapping(st)
        info["raw_source"] = str(data.get("source") or "none")
        info["source"] = _map_site_source(data.get("source"))
        info["lat_deg"] = _finite(data.get("lat_deg"))
        info["lon_deg"] = _finite(data.get("lon_deg"))
        info["alt_m"] = _finite(data.get("alt_m"))
        info["accuracy_m"] = _finite(data.get("accuracy_m"))
        return info

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------
    def snapshot(self, now_utc: Optional[float] = None) -> Dict[str, Any]:
        """只读快照。字段口径见模块文档。"""
        now = _now(now_utc)
        with self._lock:
            time_applied = self._time_applied
            time_source = self._time_source
            time_precision = self._time_precision
            last_time_utc = self._last_time_utc
            last_sync_reason = self._last_sync_reason

        sdr = self._read_sdr(now)
        gnss_info = self._read_gnss()
        station = self._read_station()

        site = self._site_block(station, gnss_info)
        engine = self._resolve_engine()
        following = None
        try:
            if engine is not None:
                following = bool(engine.is_following_system())
        except Exception:
            following = None

        if time_applied and time_source in (TIME_SOURCE_PPS, TIME_SOURCE_NMEA):
            time_block = {
                "source": "gnss",
                "time_source": time_source,
                "precision": time_precision,
                "following_system": following,
                "last_update_utc": last_time_utc,
            }
        else:
            time_block = {
                "source": "system",
                "time_source": TIME_SOURCE_NONE,
                "precision": TIME_SOURCE_PRECISION[TIME_SOURCE_NONE],
                "following_system": following,
                "last_update_utc": None,
            }

        applied_level = (time_source if time_applied else TIME_SOURCE_NONE)
        if applied_level not in _TIME_SOURCES:
            applied_level = "unknown"
        reason = gnss_info["reason"]
        if last_sync_reason:
            reason = f"{reason}；{last_sync_reason}" if reason else last_sync_reason

        gnss = {
            "connected": gnss_info["connected"],
            "port": gnss_info["port"],
            "fix": gnss_info["fix"],
            "time_source": applied_level,
            "precision": TIME_SOURCE_PRECISION.get(
                applied_level, TIME_SOURCE_PRECISION[TIME_SOURCE_NONE]),
            "site_source": site["source"],
            "lat_deg": gnss_info["lat_deg"],
            "lon_deg": gnss_info["lon_deg"],
            "antenna": gnss_info["antenna"],
            "last_update_utc": _iso_utc(gnss_info["timestamp"]),
            "reason": reason,
        }
        return {
            "sdr": sdr,
            "gnss": gnss,
            "time": time_block,
            "site": site,
        }

    @staticmethod
    def _site_block(station: Mapping[str, Any],
                    gnss: Mapping[str, Any]) -> Dict[str, Any]:
        source = station.get("source", "none")
        if source == "gnss":
            if gnss.get("fix") and gnss.get("lat_deg") is not None:
                return {
                    "source": "gnss",
                    "lat_deg": gnss.get("lat_deg"),
                    "lon_deg": gnss.get("lon_deg"),
                    "reason": "",
                }
            # GNSS 站址已失去定位：不拿旧坐标继续冒充「当前站址」。
            return {
                "source": "none",
                "lat_deg": None,
                "lon_deg": None,
                "reason": "GNSS 无有效定位，站址不可用（未回退到手动站址）",
            }
        if source == "manual":
            return {
                "source": "manual",
                "lat_deg": station.get("lat_deg"),
                "lon_deg": station.get("lon_deg"),
                "reason": "",
            }
        return {
            "source": "none",
            "lat_deg": None,
            "lon_deg": None,
            "reason": "未设置站址（无 GNSS 定位且未手动配置）",
        }

    # ------------------------------------------------------------------
    # 显式 GNSS → 时间/站址接线
    # ------------------------------------------------------------------
    def sync_gnss(self, now_utc: Optional[float] = None) -> Dict[str, Any]:
        """把当前 GNSS fix/UTC 显式推给时间引擎与站址，然后返回快照。

        无有效 fix 时回退：时间引擎 ``set_time_now()``；站址恢复接线前的手动/
        配置值（若有），否则置为 ``source="none"``。回退不会标成 GNSS。
        """
        now = _now(now_utc)
        gnss_info = self._read_gnss()
        engine = self._resolve_engine()
        reason = ""

        if gnss_info["connected"] and gnss_info["fix"]:
            dt = _gnss_datetime(self._gnss)
            level = (TIME_SOURCE_PPS if _has_pps(self._gnss, gnss_info)
                     else TIME_SOURCE_NMEA)
            if dt is not None and engine is not None:
                try:
                    engine.set_unix(dt.timestamp())
                    # set_unix 会进入仿真时刻；保持 1x 前进，避免时钟被冻住。
                    resume = getattr(engine, "resume", None)
                    if callable(resume):
                        resume(1.0)
                except Exception as e:  # noqa: BLE001 - 失败要如实标注
                    reason = f"时间引擎更新失败（{type(e).__name__}: {e}）"
                else:
                    with self._lock:
                        self._time_applied = True
                        self._time_source = level
                        self._time_precision = TIME_SOURCE_PRECISION[level]
                        self._last_time_utc = _iso_utc(dt.timestamp())
                    reason = f"GNSS UTC 已写入时间引擎（{level}）"
            elif dt is None:
                reason = ("GNSS 已定位但缺完整 UTC（需 RMC/ZDA 日期），"
                          "未改授时；时间源保持系统时钟")
            else:
                reason = "无时间引擎可用，未写授时"

            setter = getattr(self._station, "set_ground_station_from_gnss", None)
            if callable(setter):
                with self._lock:
                    if self._prev_station is None:
                        self._prev_station = self._station_snapshot()
                try:
                    setter(gnss_info["lat_deg"], gnss_info["lon_deg"],
                           gnss_info["alt_m"] or 0.0,
                           accuracy_m=gnss_info["accuracy_m"])
                except Exception as e:  # noqa: BLE001
                    reason = (f"{reason}；站址更新失败（{type(e).__name__}: {e}）"
                              if reason else
                              f"站址更新失败（{type(e).__name__}: {e}）")
        else:
            reason = self._release_locked(
                engine, "GNSS 无有效定位，回退系统时钟/手动站点")

        with self._lock:
            self._last_sync_utc = _iso_utc(now)
            self._last_sync_reason = reason
        return self.snapshot(now_utc=now)

    def record_fix(self, fix: Mapping[str, Any]) -> Dict[str, Any]:
        """记录天空图入口的 fix，并在有效时写时间引擎/站址，返回快照。

        ``fix`` 即 ``desktop/main_window._poll_gnss`` 组装的字典
        （``source/latitude/longitude/altitude_m/utc_time/utc_datetime/date/
        timestamp``）。

        * 有效 real fix + 完整 UTC（``utc_datetime``，或 ``utc_time``+``date``）
          → ``set_unix`` 写时间引擎，快照标 ``time_source=nmea``；
        * 有效经纬度 → 写站址（``source=gnss``）；
        * 无 fix / 无效 → 时间回系统时钟、站址回接线前的手动/配置值，
          快照 ``time_source=none`` / ``site_source=manual|none``，绝不再标
          ``gnss``；用户手动配置的站址不会被清掉。
        * 只有 ``hhmmss`` 没有日期时无法锚定 UTC，**不写授时**（不拿接收
          时刻冒充 GNSS 时间）；若此前 GNSS 授时已生效则原样保留，不被
          这一轮只带时刻的 fix 清回系统时钟。
        """
        data: Dict[str, Any] = dict(fix) if isinstance(fix, Mapping) else {}
        source = str(data.get("source") or "").strip().lower()
        lat = _finite(data.get("latitude", data.get("lat")))
        lon = _finite(data.get("longitude", data.get("lon")))
        alt = _finite(data.get("altitude_m", data.get("alt_m")))
        acc = _finite(data.get("accuracy_m"))
        utc = _fix_datetime(data)
        ts = _finite(data.get("timestamp"))
        valid = source in _FIX_SOURCES and lat is not None and lon is not None

        with self._lock:
            self._fix_record = {
                "valid": valid,
                "connected": (bool(data.get("connected"))
                              if "connected" in data
                              # SerialGNSSReader 在收到语句但未定位时仍报
                              # source="real"：能收到语句即说明串口在线。
                              else source in _FIX_SOURCES),
                # 有效 fix 才有「从哪个口来的」可言；断开/过期时不留旧端口。
                "port": (str(data.get("port"))
                         if valid and data.get("port") else None),
                "lat_deg": lat if valid else None,
                "lon_deg": lon if valid else None,
                "alt_m": alt if valid else None,
                "accuracy_m": acc if valid else None,
                "timestamp": ts,
                "antenna": _normalize_antenna(data.get("antenna")),
            }

        engine = self._resolve_engine()
        if not valid:
            reason = self._release_locked(
                engine, "GNSS 无有效定位，回退系统时钟/手动站点")
        else:
            if utc is not None and engine is not None:
                try:
                    engine.set_unix(utc.timestamp())
                    # set_unix 会进入仿真时刻；保持 1x 前进，避免时钟被冻住。
                    resume = getattr(engine, "resume", None)
                    if callable(resume):
                        resume(1.0)
                except Exception as e:  # noqa: BLE001 - 失败要如实标注
                    reason = self._clear_time(
                        engine,
                        f"时间引擎更新失败（{type(e).__name__}: {e}）")
                else:
                    with self._lock:
                        self._time_applied = True
                        self._time_source = TIME_SOURCE_NMEA
                        self._time_precision = TIME_SOURCE_PRECISION[TIME_SOURCE_NMEA]
                        self._last_time_utc = _iso_utc(utc.timestamp())
                    reason = f"GNSS UTC 已写入时间引擎（{TIME_SOURCE_NMEA}）"
            elif utc is None:
                # 每秒轮询的顺序是：完整 fix（含 utc_datetime）先写授时，
                # 随后天空图入口又用只带 hhmmss 的 fix 调一次 record_fix。
                # 后者不能把前一步刚生效的 GNSS 授时清回系统时钟。
                with self._lock:
                    keep = self._time_applied and self._time_source in (
                        TIME_SOURCE_PPS, TIME_SOURCE_NMEA)
                if keep:
                    reason = "本次 fix 未带完整 UTC，保留已生效授时"
                else:
                    reason = self._clear_time(
                        engine,
                        "GNSS 已定位但缺完整 UTC（仅 hhmmss 无日期），未改授时；"
                        "时间源保持系统时钟")
            else:
                reason = self._clear_time(engine, "无时间引擎可用，未写授时")

            setter = getattr(self._station, "set_ground_station_from_gnss", None)
            if callable(setter):
                with self._lock:
                    if self._prev_station is None:
                        self._prev_station = self._station_snapshot()
                try:
                    setter(lat, lon, alt or 0.0, accuracy_m=acc)
                except Exception as e:  # noqa: BLE001
                    reason = (f"{reason}；站址更新失败（{type(e).__name__}: {e}）"
                              if reason else
                              f"站址更新失败（{type(e).__name__}: {e}）")

        with self._lock:
            self._last_sync_utc = _iso_utc(_now(None))
            self._last_sync_reason = reason
        return self.snapshot()

    def release(self, reason: str = "显式释放 GNSS 授时/站址") -> Dict[str, Any]:
        """显式释放：时间回系统时钟，站址恢复接线前的手动/配置值。"""
        engine = self._resolve_engine()
        msg = self._release_locked(engine, reason)
        with self._lock:
            self._last_sync_reason = msg
            self._last_sync_utc = utc_now_iso()
        return self.snapshot()

    def _station_snapshot(self) -> Dict[str, Any]:
        """记录接线前的站址（含原始来源），用于断开时原样恢复。"""
        info = self._read_station()
        if info.get("source") == "gnss":
            # 构造时就已是 GNSS 站址：没有可回退的手动值。
            return {"source": "none", "raw_source": "none",
                    "lat_deg": None, "lon_deg": None, "alt_m": None,
                    "accuracy_m": None}
        return info

    def _clear_time(self, engine: Any, reason: str) -> str:
        """只把授时状态降回系统时钟（不动站址）。"""
        with self._lock:
            applied = self._time_applied
            self._time_applied = False
            self._time_source = TIME_SOURCE_NONE
            self._time_precision = TIME_SOURCE_PRECISION[TIME_SOURCE_NONE]
            self._last_time_utc = None

        if applied and engine is not None:
            try:
                set_now = getattr(engine, "set_time_now", None)
                if callable(set_now):
                    set_now()
            except Exception as e:  # noqa: BLE001
                reason = f"{reason}；时间引擎回退失败（{type(e).__name__}: {e}）"
        return reason

    def _release_locked(self, engine: Any, reason: str) -> str:
        reason = self._clear_time(engine, reason)
        with self._lock:
            prev = self._prev_station
            self._prev_station = None

        if prev is not None and self._station is not None:
            try:
                lat, lon = prev.get("lat_deg"), prev.get("lon_deg")
                raw = str(prev.get("raw_source") or "none")
                if prev.get("source") == "manual" and lat is not None and lon is not None:
                    self._station.set_ground_station(
                        lat, lon, prev.get("alt_m") or 0.0,
                        source=raw if raw in ("user", "config", "place", "manual")
                        else "user",
                        accuracy_m=prev.get("accuracy_m"))
                else:
                    # 没有手动站址可回退：显式清空，避免旧 GNSS 坐标残留。
                    self._station.set_ground_station(0.0, 0.0, 0.0,
                                                     source="none")
            except Exception as e:  # noqa: BLE001
                reason = f"{reason}；站址回退失败（{type(e).__name__}: {e}）"
        return reason


# ---- 进程级默认实例（天空图入口与控制器共用同一份） --------------------
_default_sources: Optional[RuntimeSources] = None
_default_lock = threading.Lock()


def get_runtime_sources() -> RuntimeSources:
    """取进程级默认 :class:`RuntimeSources`（显式注入替身时不用它）。"""
    global _default_sources
    with _default_lock:
        if _default_sources is None:
            _default_sources = RuntimeSources()
        return _default_sources


def reset_runtime_sources() -> None:
    """测试/重置用：丢弃进程级默认实例（下一次调用重建）。"""
    global _default_sources
    with _default_lock:
        _default_sources = None


def snapshot(
    *,
    sdr: Any = None,
    gnss: Any = None,
    time_engine: Any = None,
    station: Any = None,
    now_utc: Optional[float] = None,
) -> Dict[str, Any]:
    """一次性只读快照（等价于临时 ``RuntimeSources(...).snapshot()``）。"""
    return RuntimeSources(sdr=sdr, gnss=gnss, time_engine=time_engine,
                          station=station).snapshot(now_utc=now_utc)


def apply_gnss_fix(
    gnss: Any,
    *,
    time_engine: Any = None,
    station: Any = None,
    now_utc: Optional[float] = None,
) -> Dict[str, Any]:
    """显式把 GNSS 当前 fix 写入时间引擎/站址，返回快照。"""
    return RuntimeSources(gnss=gnss, time_engine=time_engine,
                          station=station).sync_gnss(now_utc=now_utc)


def release_gnss(
    *,
    time_engine: Any = None,
    station: Any = None,
    reason: str = "显式释放 GNSS 授时/站址",
) -> Dict[str, Any]:
    """显式释放 GNSS 授时/站址并返回快照。"""
    return RuntimeSources(time_engine=time_engine,
                          station=station).release(reason=reason)

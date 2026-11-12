# SPDX-License-Identifier: MIT
"""设备连接请求参数（采样率 / 增益 / PPM / 带宽）的唯一持有者。

问题（docs/redundancy-register.md §5.11.2 P2）：

    设备面板与模块面板各自把用户填的参数写进自己那份 ``dev`` 字典的
    ``_sample_rate / _gain / _ppm / _bandwidth``。两个面板互不可见——
    在设备面板把采样率改成 2.048 MS/s，再从模块面板点连接，实际下发的是
    模块面板那份（默认档），设备面板显示的值成了没人读的死副本。

治理（用户选定的方案 A「以请求存储为准」）：

  * 本模块按**设备身份键**持有连接请求，是唯一的可写持有者；
  * 面板只在选中设备时渲染本模块的值、用户改动时写回本模块，
    不再各自持有可写副本；
  * 连接载荷由 :func:`fill_payload` 统一写入，面板不直接写
    ``_sample_rate / _gain / _ppm / _bandwidth``。

身份键的硬约束（用户 2026-11-12 要求）：

  * 键必须**证明稳定且唯一**。序列号 / URI 视为稳定身份；
  * 没有序列号的设备只能按枚举序号（或显示名）暂记 → 标记
    ``PROVISIONAL`` 并**显式告警**，绝不静默当成稳定身份；
  * 一次枚举里两台设备映射到同一键 → **显式告警**并按枚举顺序加 ``#n``
    区分，绝不静默共用一份请求。

导入约定：本模块只许以**裸名** ``import connect_request_store`` 导入
（与 ``constants`` 同约定，desktop/ 在 sys.path 上）。包名导入会产生第二份
模块实例 = 第二份请求存储，正是本模块要消灭的东西。
"""
from __future__ import annotations

import logging
import weakref
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional

try:  # 裸名导入：与面板/常量的既有约定一致
    import constants
    CONNECT_SAMPLE_RATE_KEY = constants.CONNECT_SAMPLE_RATE_KEY
except Exception:  # pragma: no cover - 单独打包/工具脚本下的安全回退
    constants = None  # type: ignore[assignment]
    CONNECT_SAMPLE_RATE_KEY = "_sample_rate"


logger = logging.getLogger(__name__)

#: 身份键质量：来自设备自报的持久标识（serial / uri / rtl_tcp host:port）。
STABLE = "stable"
#: 身份键质量：只能按枚举序号或显示名暂记，重枚举后可能变。
PROVISIONAL = "provisional"

_UNSET = object()


@dataclass(frozen=True)
class DeviceIdentity:
    """一台设备在本次枚举里的身份结论。"""

    key: str
    quality: str
    warning: str = ""


@dataclass(frozen=True)
class ConnectRequest:
    """一台设备的连接请求（未设置的字段为 None，表示沿用面板预填）。"""

    sample_rate_hz: Optional[float] = None
    gain_db: Optional[float] = None
    ppm: Optional[int] = None
    bandwidth_hz: Optional[float] = None


_requests: Dict[str, ConnectRequest] = {}
#: 第三档「会话占用态」（gpredict 语义）：key -> 会话开始时冻结的生效值。
#: 与 ``_requests`` 分开存：请求是「想要什么」，会话快照是「这次实际下发了什么」。
_sessions: Dict[str, ConnectRequest] = {}
#: 订阅者用弱引用持有：面板销毁后不会因为“监听列表还拽着它”而泄漏，
#: 也不会在 C++ 对象已删后仍被回调。
_listeners: List[Any] = []
_warned: set = set()
_recent_warnings: List[str] = []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _identity_of(device: Dict[str, Any]) -> DeviceIdentity:
    """从设备字典派生身份键；拿不到持久标识时如实降级为 PROVISIONAL。"""
    driver = _text(device.get("driver")) or "?"
    serial = _text(device.get("serial"))
    args = device.get("device_args") or {}
    uri = _text(device.get("uri")) or _text(args.get("uri"))
    label = _text(device.get("label")) or driver

    if serial:
        # rtl_tcp / network IQ 的 serial 是枚举阶段合成的稳定定位串，
        # 与真实序列号一样在重枚举后不变。
        return DeviceIdentity(f"{driver}|serial:{serial}", STABLE)
    if uri:
        return DeviceIdentity(f"uri:{uri}", STABLE)

    idx = args.get("index", device.get("index"))
    if idx is not None and _text(idx) != "":
        return DeviceIdentity(
            f"{driver}|index:{_text(idx)}", PROVISIONAL,
            f"设备「{label}」没有序列号，按枚举序号 {_text(idx)} 暂记连接参数；"
            "拔插或重枚举后序号可能变化，参数不会跟随设备。")

    return DeviceIdentity(
        f"{driver}|label:{label}", PROVISIONAL,
        f"设备「{label}」既无序列号也无枚举序号，按显示名暂记连接参数；"
        "同名设备的参数可能互相覆盖。")


def _fingerprint(device: Dict[str, Any]) -> tuple:
    args = device.get("device_args") or {}
    return (str(device.get("driver") or ""), str(device.get("serial") or ""),
            str(device.get("uri") or ""), str(args.get("index", "")),
            str(device.get("label") or ""))


def _warn_once(text: str) -> None:
    if text in _warned:
        return
    _warned.add(text)
    _recent_warnings.append(text)
    logger.warning("连接请求身份告警：%s", text)


def resolve(devices) -> List[DeviceIdentity]:
    """给本次枚举的每台设备派生身份键（顺序与入参一一对应）。

    撞键时：不稳定地共用一份请求是必须禁止的——这里按指纹排序后加
    ``#n`` 区分，并显式告警「身份键冲突」。
    """
    items = list(devices or [])
    base = [_identity_of(d) for d in items]
    groups: Dict[str, List[int]] = {}
    for i, ident in enumerate(base):
        groups.setdefault(ident.key, []).append(i)

    out: List[Optional[DeviceIdentity]] = [None] * len(items)
    for key, idxs in groups.items():
        if len(idxs) == 1:
            i = idxs[0]
            ident = base[i]
            out[i] = ident
            if ident.warning:
                _warn_once(ident.warning)
            continue

        warning = (f"设备身份键冲突：{len(idxs)} 台设备都映射到「{key}」；"
                   "已按枚举顺序加 #n 临时区分，拔插后参数可能错位。")
        _warn_once(warning)
        for n, i in enumerate(sorted(idxs, key=lambda j: _fingerprint(items[j])), 1):
            quality = base[i].quality
            out[i] = DeviceIdentity(f"{key}#{n}", quality, warning)

    return [ident for ident in out if ident is not None]


def intent_for(key: str) -> ConnectRequest:
    """最近一次写入的意图值（不受会话占用态影响；诊断/测试用）。"""
    return _requests.get(key, ConnectRequest())


def request_for(key: str) -> ConnectRequest:
    """渲染读侧：无记录时各字段为 None。

    会话占用期间返回**冻结的会话生效值**，不返回最新意图——面板与
    ``fill_payload`` 都经这里取值，这样另一面板在会话中改了 store，也
    无法把正在生效的值改回去（gpredict 第三档读侧门禁）。会话结束后
    恢复正常，最新意图立即可见。
    """
    locked = _sessions.get(key)
    if locked is not None:
        return locked
    return _requests.get(key, ConnectRequest())


# ---------------------------------------------------------------- 会话占用态
# 第三档（gpredict 「占用即置灰，退出占用才写回」）的**数据源**：
# 这里只回答「这台设备是否在会话中 / 会话生效值是什么」，不决定 UI 怎么
# 显示、也不决定退出时是否回滚。显示方案见
# docs/research/2026-11-12-gpredict-occupancy-decision.md。
def begin_session(key: str) -> ConnectRequest:
    """登记设备会话开始，冻结当前请求值作为「本次会话生效值」。

    重复调用**不覆盖**首次快照：会话期间 store 被另一面板改过，也不能把
    正在生效的值换掉。返回不可变快照。
    """
    if key not in _sessions:
        _sessions[key] = intent_for(key)
    return _sessions[key]


def end_session(key: str) -> Optional[ConnectRequest]:
    """登记会话结束；返回被冻结的生效值（未占用返回 None）。

    只清占用态，不写回 ``_requests``——退出时是否回滚/写回属产品决策。
    """
    return _sessions.pop(key, None)


def session_values(key: str) -> Optional[ConnectRequest]:
    """会话占用期间实际生效的参数快照；未占用返回 None。"""
    return _sessions.get(key)


def is_occupied(key: str) -> bool:
    """该设备是否处于会话占用态（第三档）。"""
    return key in _sessions


def update(key: str, *, sample_rate_hz=_UNSET, gain_db=_UNSET,
           ppm=_UNSET, bandwidth_hz=_UNSET) -> ConnectRequest:
    """写回某台设备的连接请求——全仓唯一写入口。

    非法值（非数/NaN/<=0 的采样率）保持原值不变并记日志，不把 0 Hz 这类
    会让后端崩掉的假值存进来。

    写侧以**意图值**为底，不以渲染读侧为底：会话占用期间写入照常记录，
    但 ``request_for`` 继续返回冻结快照，直到 ``end_session`` 才可见。
    """
    current = intent_for(key)
    changes: Dict[str, Any] = {}

    if sample_rate_hz is not _UNSET:
        try:
            hz = float(sample_rate_hz)
        except (TypeError, ValueError):
            hz = 0.0
        if hz > 0.0 and hz == hz and hz != float("inf"):
            changes["sample_rate_hz"] = hz
        else:
            logger.warning("拒绝采样率请求 %r（必须为正的有限数）", sample_rate_hz)

    if gain_db is not _UNSET:
        try:
            db = float(gain_db)
        except (TypeError, ValueError):
            db = None
        if db is not None and db == db and abs(db) != float("inf"):
            changes["gain_db"] = db
        else:
            logger.warning("拒绝增益请求 %r（必须为有限数）", gain_db)

    if ppm is not _UNSET:
        try:
            changes["ppm"] = int(ppm)
        except (TypeError, ValueError):
            logger.warning("拒绝 PPM 请求 %r（必须为整数）", ppm)

    if bandwidth_hz is not _UNSET:
        try:
            bw = float(bandwidth_hz)
        except (TypeError, ValueError):
            bw = -1.0
        if bw >= 0.0 and bw == bw and bw != float("inf"):
            changes["bandwidth_hz"] = bw
        else:
            logger.warning("拒绝带宽请求 %r（必须为非负有限数）", bandwidth_hz)

    if not changes:
        return current

    updated = replace(current, **changes)
    if updated == current:
        return current
    _requests[key] = updated
    for listener in _live_listeners():
        try:
            listener(key)
        except Exception:  # noqa: BLE001 - 一个面板渲染失败不该拖垮另一侧
            logger.exception("连接请求变更通知失败")
    return updated


def fill_payload(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    """把该设备的请求写进连接载荷（主窗口读侧协议保持不变）。

    采样率仍走 ``constants.set_connect_sample_rate`` 这个唯一写入口；
    其余字段只在请求里真的设置过时才写，没设置就沿用主窗口的既有默认。
    """
    req = request_for(key)
    if constants is not None:
        constants.set_connect_sample_rate(payload, req.sample_rate_hz)
    else:  # pragma: no cover - 无常量模块时仍保证键存在
        payload[CONNECT_SAMPLE_RATE_KEY] = req.sample_rate_hz
    if req.gain_db is not None:
        payload["_gain"] = req.gain_db
    if req.ppm is not None:
        payload["_ppm"] = req.ppm
    if req.bandwidth_hz is not None:
        payload["_bandwidth"] = req.bandwidth_hz
    return payload


def _live_listeners() -> List[Callable[[str], None]]:
    live = []
    for ref in list(_listeners):
        callback = ref()
        if callback is None:
            try:
                _listeners.remove(ref)
            except ValueError:
                pass
        else:
            live.append(callback)
    return live


def subscribe(listener: Callable[[str], None]) -> None:
    """订阅请求变更（参数是发生变化的设备身份键）。"""
    if getattr(listener, "__self__", None) is not None:
        ref = weakref.WeakMethod(listener)
    else:
        ref = (lambda: listener)
    for existing in _live_listeners():
        if existing == listener:
            return
    _listeners.append(ref)


def unsubscribe(listener: Callable[[str], None]) -> None:
    for ref in list(_listeners):
        callback = ref()
        if callback is None or callback == listener:
            try:
                _listeners.remove(ref)
            except ValueError:
                pass


def recent_warnings() -> List[str]:
    """最近一次进程内出现过的身份告警（测试与诊断用）。"""
    return list(_recent_warnings)


def reset() -> None:
    """清空进程级状态（测试隔离用；产品路径不调用）。"""
    _requests.clear()
    _sessions.clear()
    _warned.clear()
    _recent_warnings.clear()

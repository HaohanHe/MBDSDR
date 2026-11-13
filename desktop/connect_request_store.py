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

写门（2026-11-13 追加）：会话占用期间 :func:`update` **拒绝**写入并返回
可读原因——不静默忽略（用户会以为改成功了），也不排队延后（会话结束时
凭空多出一次写入更难解释）。拒绝时返回的 ``request`` 仍是冻结快照，
调用方据此渲染就不会停在被拒的值上。设备面板的断开写回是唯一在
``end_session`` 之前发生的写入，因此它先结束占用、再走同一个 ``update``。

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

# 身份键实现只有一份：统一入口在 ``mbdsdr_ai/device_identity``（桌面端与 AI
# 必须算出同一个字符串；键的字符串规则再委托给 ``sdr_backend``）。
from mbdsdr_ai.device_identity import (  # noqa: E402
    IDENTITY_PROVISIONAL as _SB_PROVISIONAL,
    IDENTITY_STABLE as _SB_STABLE,
    describe as _describe_identity,
    describe_all as _describe_identities,
)


logger = logging.getLogger(__name__)

#: 身份键质量：来自设备自报的持久标识（serial / uri / rtl_tcp host:port）。
STABLE = _SB_STABLE
#: 身份键质量：只能按枚举序号或显示名暂记，重枚举后可能变。
PROVISIONAL = _SB_PROVISIONAL

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


@dataclass(frozen=True)
class WriteResult:
    """写门结果：**拒绝 + 可读原因**，调用方不必再猜该显示哪份值。

    ``request`` 永远是当前权威值：会话占用期间就是冻结快照，未占用就是
    最新意图。``rejected=True`` 时 store 一个字段都没改。
    """

    request: ConnectRequest
    rejected: bool = False
    reason: str = ""

    def __bool__(self) -> bool:
        """``if store.update(...)`` 只在没被拒时为真。"""
        return not self.rejected


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
    """从设备字典派生身份键（唯一入口 ``device_identity.describe``）。

    AI 侧 ``device_id`` 与这里的键必须逐字相同，否则「桌面端占着、AI 被拒」
    这条互斥永远不成立。规范实现放 ``mbdsdr_ai/device_identity``（纯 Python、
    无 Qt）；这里只做类型转换，不另写一套口径。``warning`` 用 canonical
    措辞（参数记忆要的是「参数会不会跟丢」，不是热插拔的等级说明）。
    """
    ident = _describe_identity(device)
    return DeviceIdentity(ident.key, ident.quality, ident.warning)


def _warn_once(text: str) -> None:
    if text in _warned:
        return
    _warned.add(text)
    _recent_warnings.append(text)
    logger.warning("连接请求身份告警：%s", text)


def resolve(devices) -> List[DeviceIdentity]:
    """给本次枚举的每台设备派生身份键（顺序与入参一一对应）。

    撞键时：不稳定地共用一份请求是必须禁止的——规范实现按指纹排序后加
    ``#n`` 区分，并显式告警「身份键冲突」。
    """
    items = list(devices or [])
    out: List[DeviceIdentity] = []
    for ident in _describe_identities(items):
        if ident.warning:
            _warn_once(ident.warning)
        out.append(DeviceIdentity(ident.key, ident.quality, ident.warning))
    return out


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
def _notify(key: str) -> None:
    """通知订阅者重渲染（写成功后、拒绝后都发：拒绝也必须把读数拉回冻结值）。"""
    for listener in _live_listeners():
        try:
            listener(key)
        except Exception:  # noqa: BLE001 - 一个面板渲染失败不该拖垮另一侧
            logger.exception("连接请求变更通知失败")


def _session_reject_reason(key: str) -> str:
    """会话占用期间拒绝写入的可读原因（面板状态行与 AI 工具共用同一句）。"""
    return (f"设备会话进行中（{key}）：本次改动未写入，"
            "采样率/增益/PPM/带宽保持本次会话的生效值。"
            "请先断开设备（结束会话）再修改。")


def session_reject_reason(key: str) -> str:
    """公开别名：UI 显示会话占用原因时用它，保证与 ``update`` 拒绝同一句。"""
    return _session_reject_reason(key)


def begin_session(key: str,
                  initial: Optional[ConnectRequest] = None) -> ConnectRequest:
    """登记设备会话开始，冻结本次会话的**生效值**（不等于意图值）。

    ``initial``：连接时实际要下发的载荷。给了就与当前意图叠加、经同一套
    校验后作为快照；**不写意图**——意图是「用户想设什么」，快照是「这次
    实际生效什么」，两者只在断开写回时合一（gpredict 第三档）。

    为什么选「建会话时收下初始载荷」而不是「先下发后建会话」：
    ``_apply_connect_params`` 在 ``_panels_set_sdr_connected(True)`` 之后才
    执行（main_window 2026-11-13 的 L6720 vs L6647），把建会话挪到它后面
    要改主窗口两段时序、中间还夹着 UI 通知；让本函数收下初始载荷，则
    「起点值」成为会话的一部分，此后门关闭，**连接流程自身零拒绝**。

    重复调用**不覆盖**首次快照：会话期间 store 被另一入口改过，也不能把
    正在生效的值换掉。返回不可变快照。
    """
    if key in _sessions:
        return _sessions[key]
    snap = intent_for(key)
    if initial is not None:
        changes = _normalize_values(
            sample_rate_hz=(initial.sample_rate_hz
                            if initial.sample_rate_hz is not None else _UNSET),
            gain_db=(initial.gain_db
                     if initial.gain_db is not None else _UNSET),
            ppm=(initial.ppm if initial.ppm is not None else _UNSET),
            bandwidth_hz=(initial.bandwidth_hz
                          if initial.bandwidth_hz is not None else _UNSET),
        )
        if changes:
            snap = replace(snap, **changes)
    _sessions[key] = snap
    return snap


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


def _normalize_values(*, sample_rate_hz=_UNSET, gain_db=_UNSET,
                      ppm=_UNSET, bandwidth_hz=_UNSET) -> Dict[str, Any]:
    """把原始请求值校验/归一化成可落库字段——校验规则只有这一份。

    非法值不落库（保持原值）并记日志：0 Hz 这类假值能让后端直接崩。
    ``update``（意图）与 ``begin_session``（会话初始载荷）都走这里。
    """
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

    return changes


def update(key: str, *, sample_rate_hz=_UNSET, gain_db=_UNSET,
           ppm=_UNSET, bandwidth_hz=_UNSET) -> WriteResult:
    """写回某台设备的连接请求——全仓唯一写入口（会话占用期间是**写门**）。

    非法值（非数/NaN/<=0 的采样率）保持原值不变并记日志，不把 0 Hz 这类
    会让后端崩掉的假值存进来。

    **会话占用期间直接拒绝**：不改 ``_requests``、不给硬件下发任何东西，
    返回 ``rejected=True`` 与可读原因（见 :func:`_session_reject_reason`）。
    这是有意的取舍——静默忽略会让用户以为改成功了；排队延后则会在会话
    结束时凭空多出一次写入，事后无从解释。断开写回由调用方先
    ``end_session`` 再调本函数，因此不需要任何绕过标志。
    """
    locked = _sessions.get(key)
    if locked is not None:
        reason = _session_reject_reason(key)
        logger.info("会话占用期间拒绝写入请求 %s", key)
        # 拒绝也通知读侧：控件必须回到冻结值，不能停在被拒的新值上。
        _notify(key)
        return WriteResult(locked, True, reason)

    current = intent_for(key)
    changes = _normalize_values(
        sample_rate_hz=sample_rate_hz, gain_db=gain_db,
        ppm=ppm, bandwidth_hz=bandwidth_hz)

    if not changes:
        return WriteResult(current)

    updated = replace(current, **changes)
    if updated == current:
        return WriteResult(current)
    _requests[key] = updated
    _notify(key)
    return WriteResult(updated)


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
    """退订请求变更（幂等；订阅方销毁/重建时必须调用）。

    订阅用弱引用持有，但面板被 Qt 销毁后 Python 包装对象仍可能存活，
    弱引用不会自动失效——不显式退订的话，通知会打到已删除的控件上。
    因此订阅方在 ``destroyed`` 时必须调这里，用例见
    ``tests/test_connect_request_store.py::test_unsubscribe_stops_callbacks``。
    """
    for ref in list(_listeners):
        callback = ref()
        if callback is None or callback == listener:
            try:
                _listeners.remove(ref)
            except ValueError:
                pass


def unsubscribe_owner(owner: object) -> None:
    """按订阅方对象退订，供 Qt ``destroyed`` 钩子使用。

    ``destroyed`` 触发时对象已处于销毁中：PySide6 会把绑方法连接先行拆掉，
    且拿销毁中的对象去比较绑方法并不可靠。这里按 ``__self__`` 认出订阅方，
    不碰控件属性，因此可以在销毁回调里安全执行。
    """
    for ref in list(_listeners):
        callback = ref()
        if callback is None or getattr(callback, "__self__", None) is owner:
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

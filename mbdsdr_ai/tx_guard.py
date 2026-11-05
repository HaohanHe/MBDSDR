# SPDX-License-Identifier: MIT
"""
发射安全网关（接入层唯一事实源）
================================

面向所有可能产生真实射频输出的路径：TC 上行（tc_uplink）、任意 IQ/数字模式
发射（sdr_transmit_iq / sdr_transmit_cw）、录音回放发射（playback）、以及
底层 SoapySDR/设备驱动的 ``write_tx``。红线（AGENTS §2/§3）：

* **人工门不可绕**：任何发射必须经过 :class:`TxGate`。门未开、已过期、被撤销
  分别给出可区分的拒绝码（``gate_not_armed`` / ``gate_expired`` /
  ``gate_revoked`` / ``gate_unknown_token`` / ``gate_token_mismatch``），
  且拒绝信封不谎报成功。
* **底层直调同样拦**：脚本绕过工具层直接 ``backend.write_tx()`` 时，没有
  授权上下文一律拒绝（返回 False 并记录 ``last_tx_reject``），不静默发波。
* 工具层、脚本层、驱动层共用本模块，不在别处再写一套门语义。
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── 门拒绝码（可区分，供 UI/日志/测试判定）────────────────────────────────
GATE_OK = "ok"
GATE_NOT_ARMED = "gate_not_armed"
GATE_EXPIRED = "gate_expired"
GATE_REVOKED = "gate_revoked"
GATE_UNKNOWN_TOKEN = "gate_unknown_token"
GATE_TOKEN_MISMATCH = "gate_token_mismatch"
#: token 已被消费（一次性授权不得复用）。
GATE_TOKEN_USED = "gate_token_used"
#: token 绑定的动作摘要与本次调用不符（换参数/换工具重放都会被拒）。
GATE_ACTION_MISMATCH = "gate_action_mismatch"
#: 模型自报 ``arm_manual=true``：不再构成人工确认。
GATE_MANUAL_UNTRUSTED = "gate_arm_manual_untrusted"

#: 门参数：由门消费、绝不下发给 handler，也不参与动作摘要。
GATE_PARAMS = ("arm_manual", "confirm_token", "tx_token", "approve_session")

#: 单次授权默认有效期（秒）。过期后必须重新 arm（人工再次确认）。
DEFAULT_TTL_S = 120.0


@dataclass(frozen=True)
class GateDecision:
    """一次人工门判定结果（只读）。"""

    allowed: bool
    code: str
    reason: str
    state: str = ""
    token: str = ""
    action: str = ""
    issued_at: float = 0.0
    expires_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "state": self.state,
            "token": self.token,
            "action": self.action,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


class TxGate:
    """进程级人工门（发射 + 任意写工具的唯一事实源）。

    语义（S13 红线）：
      * ``arm(action=...)`` 只能由**外部确认通道**（真人弹窗/测试注入）调用，
        签发一次性 token：绑定动作摘要、带签发时刻、有 TTL。
      * token 在 TTL 内有效，成功消费一次后立即作废（重放 → ``gate_token_used``）。
      * ``arm_manual=True`` 是模型可自报的普通参数，**不再构成确认**，一律拒绝
        （``gate_arm_manual_untrusted``）。
      * ``revoke()`` 撤销授权：撤销后 token 与任何自报参数都拒绝。
    """

    def __init__(self, default_ttl_s: float = DEFAULT_TTL_S) -> None:
        self._lock = threading.RLock()
        self._state = "disarmed"      # disarmed / armed / expired / revoked
        self._token = ""
        self._armed_at = 0.0
        self._ttl_s = float(default_ttl_s)
        self._revoked_reason = ""
        self._generation = 0
        self._action = ""
        self._used = False
        self._single_use = True
        self._last_decision: Optional[GateDecision] = None

    # ------------------------------------------------------------------
    def arm(self, *, ttl_s: Optional[float] = None, note: str = "",
            action: str = "", single_use: bool = True,
            now: Optional[float] = None) -> str:
        """外部确认通道签发一次性 token。

        Parameters
        ----------
        ttl_s : float
            有效期（秒），必须为正。
        note : str
            人工确认备注（审计用）。
        action : str
            动作摘要（:func:`action_digest` 的结果）。token 只对同一动作有效。
        single_use : bool
            成功消费一次后是否作废；默认 True。
        now : float
            测试注入的单调时钟（不读墙钟）。
        """
        with self._lock:
            ttl = self._ttl_s if ttl_s is None else float(ttl_s)
            if ttl <= 0:
                raise ValueError("ttl_s 必须为正数（授权必须有有效期）")
            self._state = "armed"
            self._token = uuid.uuid4().hex
            self._armed_at = time.monotonic() if now is None else float(now)
            self._ttl_s = ttl
            self._revoked_reason = ""
            self._action = str(action or "")
            self._used = False
            self._single_use = bool(single_use)
            self._generation += 1
            d = GateDecision(True, GATE_OK,
                             f"人工门已开（{ttl:.0f}s 有效，一次性）" +
                             (f"：{note}" if note else ""),
                             self._state, self._token, self._action,
                             self._armed_at, self._armed_at + ttl)
            self._last_decision = d
            logger.info("TxGate armed: ttl=%.1fs single_use=%s action=%s note=%s",
                        ttl, self._single_use, self._action[:12], note)
            return self._token

    def revoke(self, *, reason: str = "人工门被撤销") -> None:
        """撤销发射授权；撤销后所有发射路径立即拒绝（不等待 TTL）。"""
        with self._lock:
            self._state = "revoked"
            self._revoked_reason = str(reason or "人工门被撤销")
            self._last_decision = GateDecision(
                False, GATE_REVOKED, self._revoked_reason, self._state)
            logger.warning("TxGate revoked: %s", self._revoked_reason)

    def disarm(self) -> None:
        """关闭门（等价于未武装），吊销现有 token。"""
        with self._lock:
            self._state = "disarmed"
            self._token = ""
            self._revoked_reason = ""
            self._last_decision = None
            self._generation += 1

    # ------------------------------------------------------------------
    def _expired(self, now: float) -> bool:
        return (self._state == "armed"
                and self._armed_at + self._ttl_s <= now)

    def authorize(self, *, arm_manual: bool = False,
                  token: Optional[str] = None,
                  action: Optional[str] = None,
                  consume: bool = True,
                  now: Optional[float] = None) -> GateDecision:
        """判定本次写/发射是否被允许。

        只有**外部确认通道签发的 token** 能放行：
          * ``arm_manual=True``（模型自报）→ 直接拒绝；
          * token 未知/不匹配/过期/已消费/动作摘要不符 → 各自可区分拒绝。

        consume=True 时成功即消费 token（一次性）；传输会话中逐块复核用
        consume=False（同一动作仍在执行，不重复消费）。
        """
        with self._lock:
            now = time.monotonic() if now is None else float(now)
            state = self._state
            if state == "revoked":
                d = GateDecision(False, GATE_REVOKED,
                                 f"人工门已被撤销：{self._revoked_reason}", state)
            elif token:
                if not self._token:
                    d = GateDecision(False, GATE_UNKNOWN_TOKEN,
                                     "授权 token 不存在：门从未打开或已吊销", state)
                elif token != self._token:
                    d = GateDecision(False, GATE_TOKEN_MISMATCH,
                                     "授权 token 与当前门不匹配", state)
                elif self._expired(now):
                    self._state = "expired"
                    d = GateDecision(False, GATE_EXPIRED,
                                     f"人工门已过期（有效期 {self._ttl_s:.0f}s）",
                                     self._state)
                elif self._used and consume:
                    d = GateDecision(False, GATE_TOKEN_USED,
                                     "授权 token 已被消费（一次性授权不得复用）",
                                     state, self._token, self._action)
                elif (action is not None and self._action
                      and str(action) != self._action):
                    d = GateDecision(False, GATE_ACTION_MISMATCH,
                                     "授权 token 的动作摘要与本次调用不匹配",
                                     state, self._token, self._action)
                else:
                    if consume and self._single_use:
                        self._used = True
                    d = GateDecision(True, GATE_OK,
                                     "人工门 token 有效（一次性）",
                                     state, self._token, self._action,
                                     self._armed_at, self._armed_at + self._ttl_s)
            elif arm_manual is True:
                # 模型可自报的参数不再构成人工确认（S13 红线）。
                d = GateDecision(False, GATE_MANUAL_UNTRUSTED,
                                 "arm_manual=true 不构成人工确认：需外部确认通道"
                                 "签发的一次性 token", state)
            else:
                d = GateDecision(False, GATE_NOT_ARMED,
                                 "未过人工门：需外部确认通道签发的一次性 token",
                                 state)

            if not d.allowed:
                logger.warning("TxGate 拒绝(%s): %s", d.code, d.reason)
            self._last_decision = d
            return d

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            remaining = 0.0
            if self._state == "armed":
                remaining = max(0.0, self._armed_at + self._ttl_s - now)
            return {
                "state": self._state,
                "token": self._token,
                "ttl_s": self._ttl_s,
                "remaining_s": remaining,
                "generation": self._generation,
                "revoked_reason": self._revoked_reason,
                "action": self._action,
                "used": bool(self._used),
                "single_use": bool(self._single_use),
                "issued_at": self._armed_at if self._state == "armed" else 0.0,
                "expires_at": (self._armed_at + self._ttl_s
                               if self._state == "armed" else 0.0),
                "last_decision": self._last_decision.to_dict()
                if self._last_decision else None,
            }


_GATE = TxGate()


def get_tx_gate() -> TxGate:
    """返回进程级人工门（唯一实例）。"""
    return _GATE


def reset_tx_gate() -> TxGate:
    """测试用：把进程级门复位为未武装的干净实例。"""
    global _GATE
    _GATE = TxGate()
    return _GATE


# ── 动作摘要 + 外部确认通道 ──────────────────────────────────────────────
def _json_default(obj: Any) -> Any:
    """把 numpy / bytes 等参数值规范成可序列化形式（同进程内稳定）。"""
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if isinstance(obj, (bytes, bytearray)):
        return list(obj)
    return repr(obj)


def action_digest(tool_name: str, args: Optional[Dict[str, Any]]) -> str:
    """计算一次写动作的摘要（工具名 + 全部非门参数，键排序）。

    门参数（arm_manual / confirm_token / tx_token / approve_session）刻意排除：
    它们只承载授权，不属于动作本身，不能影响摘要。
    """
    clean = {k: v for k, v in dict(args or {}).items()
             if k not in GATE_PARAMS}
    payload = json.dumps({"tool": str(tool_name), "args": clean},
                         sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), default=_json_default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def issue_confirmation(tool_name: str, args: Optional[Dict[str, Any]],
                       *, ttl_s: float = DEFAULT_TTL_S, note: str = "",
                       now: Optional[float] = None) -> str:
    """外部确认通道：为一次具体写动作签发一次性 token。

    只有真人确认面（桌面弹窗 / 测试注入 / 受信任脚本）可以调用本函数。
    模型永远拿不到调用本函数的能力——它只能拿到 opaque token 字符串。
    """
    digest = action_digest(tool_name, args)
    return get_tx_gate().arm(ttl_s=ttl_s, note=note, action=digest, now=now)


def resolve_write_authorization(tool_name: str,
                                args: Optional[Dict[str, Any]]) -> GateDecision:
    """handler 层复用门判定（单一事实源仍是 :class:`TxGate`）。

    优先使用上层 ``ToolCallGate`` 已建立的一次性授权上下文（避免重复消费
    token）；没有上下文时才用 args 里的 opaque token 现场校验。
    模型自报的 ``arm_manual`` 不参与放行判定，仅用于给出可区分的
    ``gate_arm_manual_untrusted`` 拒绝码（显式说明"该参数已不支持"）。
    """
    ctx = current_tx_decision()
    if ctx is not None and ctx.allowed:
        return ctx
    token = (args or {}).get("confirm_token") or (args or {}).get("tx_token")
    return get_tx_gate().authorize(
        arm_manual=(args or {}).get("arm_manual") is True,
        token=token, action=action_digest(tool_name, args))


# ── 授权上下文：底层 write_tx 只认这个，不认调用者口头声明 ─────────────────
_TX_AUTH: contextvars.ContextVar[Optional[GateDecision]] = contextvars.ContextVar(
    "mbdsdr_tx_authorization", default=None)


def tx_authorized() -> bool:
    """当前执行上下文是否持有有效发射授权。"""
    d = _TX_AUTH.get()
    return bool(d is not None and d.allowed)


def current_tx_decision() -> Optional[GateDecision]:
    return _TX_AUTH.get()


@contextmanager
def tx_authorization(decision: GateDecision) -> Iterator[GateDecision]:
    """在授权上下文内执行真实写波；离开上下文即失效（不可跨线程复用）。"""
    if decision is None or not decision.allowed:
        code = getattr(decision, "code", GATE_NOT_ARMED)
        reason = getattr(decision, "reason", "未过人工门")
        raise PermissionError(f"{code}: {reason}")
    token = _TX_AUTH.set(decision)
    try:
        yield decision
    finally:
        _TX_AUTH.reset(token)


def note_tx_reject(backend: Any, code: str, reason: str) -> None:
    """在底层后端记录一次发射拒绝（供脚本/测试读取，不吞掉原因）。"""
    try:
        backend.last_tx_reject = {"code": code, "reason": reason}
        backend.tx_rejects = int(getattr(backend, "tx_rejects", 0)) + 1
    except Exception:  # noqa: BLE001 - 记录失败不影响拒绝本身
        pass
    logger.warning("底层 write_tx 被拒(%s): %s", code, reason)


def reject_unless_authorized(backend: Any) -> bool:
    """底层写波入口的统一检查。返回 True 表示可继续写。"""
    if tx_authorized():
        return True
    d = get_tx_gate().authorize(arm_manual=False)
    note_tx_reject(backend, d.code, d.reason)
    return False


# ═════════════════════════════════════════════════════════════════════════
# 二、发射参数越界防护（设备能力 + 可选法规范围）
# ═════════════════════════════════════════════════════════════════════════

LIMIT_OK = "ok"
LIMIT_BAD_PARAM = "bad_parameter"
LIMIT_FREQ = "frequency_out_of_range"
LIMIT_BANDWIDTH = "bandwidth_out_of_range"
LIMIT_GAIN = "gain_out_of_range"
LIMIT_DURATION = "duration_out_of_range"
LIMIT_CAP_UNKNOWN = "capability_unknown"
LIMIT_REGULATORY = "regulatory_out_of_range"
LIMIT_REG_UNCONFIGURED = "regulatory_unconfigured"


@dataclass(frozen=True)
class RegulatoryBand:
    """法规允许频段（由操作员/配置提供，不由本仓臆造）。"""

    lo_hz: float
    hi_hz: float
    label: str = ""
    basis: str = ""


@dataclass(frozen=True)
class RegulatoryPolicy:
    """法规约束：允许频段 + 可选最长连续发射时长（占空比约束）。"""

    name: str
    bands: Sequence[RegulatoryBand]
    max_tx_duration_s: Optional[float] = None
    basis: str = ""


@dataclass(frozen=True)
class TxCapabilities:
    """已知/未知的 TX 设备能力。未知字段必须给 None，不许用默认值冒充。"""

    known: bool = False
    freq_range_hz: Optional[Tuple[float, float]] = None
    bandwidth_range_hz: Optional[Tuple[float, float]] = None
    gain_range_db: Optional[Tuple[float, float]] = None
    max_duration_s: Optional[float] = None
    duplex: str = "unknown"        # half / full / unknown
    device_name: str = ""
    source: str = ""               # 能力依据（参数表/API 枚举/用户配置）


@dataclass
class TxLimitCheck:
    """越界检查结果；violations 里逐项给出字段、值、允许区间与依据。"""

    ok: bool
    code: str
    reason: str
    field_status: Dict[str, str] = field(default_factory=dict)
    violations: List[Dict[str, Any]] = field(default_factory=list)
    basis: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "reason": self.reason,
            "field_status": dict(self.field_status),
            "violations": list(self.violations),
            "basis": list(self.basis),
        }


#: 已知设备的 TX 能力（数值来自本仓参数表，来源字符串即依据）。
_DEVICE_TX_PROFILES: Dict[str, Dict[str, Any]] = {
    "hackrf": {
        "freq": (1_000_000.0, 6_000_000_000.0),  # hackrf_params.min/max_freq_hz
        "gain": (0.0, 47.0),                     # TXVGA 0-47 dB（hackrf.c:2081）
        "duplex": "half",
        "source": "mbdsdr_ai.hackrf_params（HackRF One 官方量程）",
    },
    "bladerf": {
        "freq": (47_000_000.0, 6_000_000_000.0),  # bladerf2_common.h:558
        "gain": (-23.75, 66.0),                   # bladerf2_common.h:455-457
        "duplex": "full",                         # bladeRF 独立 RX/TX 通道
        "source": "mbdsdr_ai.bladerf_params（bladeRF2 官方量程）",
    },
    "limesdr": {
        "freq": (100_000.0, 3_800_000_000.0),     # lms7_device.cpp:1384
        "gain": (0.0, 73.0),                      # LimeSuite.h:382
        "duplex": "unknown",                      # 同通道收发时序由驱动配置决定
        "source": "mbdsdr_ai.limesuite_params（LimeSuite 官方量程）",
    },
    "plutosdr": {
        # AD9361 325MHz-3.8GHz（hal.py DRIVER_MAP / 官方 datasheet）
        "freq": (325_000_000.0, 3_800_000_000.0),
        # TX 增益是衰减器语义，不能把 RX gain 当 TX 增益——保持未知
        "duplex": "unknown",                      # TDD/FDD 由固件配置决定
        "source": "mbdsdr_ai.hal DRIVER_MAP（AD9361 325MHz-3.8GHz）",
    },
}


def _driver_key(device_type: str) -> str:
    """把 device_type/驱动名归一为参数表键（soapy_hackrf → hackrf）。"""
    value = (device_type or "").lower()
    for key in _DEVICE_TX_PROFILES:
        if key in value:
            return key
    return ""


def capabilities_from_device(device: Any) -> TxCapabilities:
    """从 ``sdr_backend.SDRDevice`` 或 ``hal.DeviceInfo`` 推导 TX 能力。

    只使用设备声明的量程与仓库内参数表；推不出来就 ``known=False``，
    让调用方如实报「能力未知，需用户确认」，绝不假设。
    """
    if device is None:
        return TxCapabilities(known=False, source="无设备")
    # hal.DeviceInfo：有 tx_range
    tx_range = getattr(device, "tx_range", None)
    if tx_range and float(tx_range[1]) > 0:
        key = _driver_key(str(getattr(device, "driver", "")))
        prof = _DEVICE_TX_PROFILES.get(key, {})
        return TxCapabilities(
            known=True,
            freq_range_hz=(float(tx_range[0]), float(tx_range[1]),
                           ) if len(tx_range) >= 2 else None,
            bandwidth_range_hz=None,   # 未声明，保持未知
            gain_range_db=prof.get("gain"),
            duplex=prof.get("duplex", "unknown"),
            device_name=str(getattr(device, "name", "")),
            source=prof.get("source", "设备枚举 tx_range"),
        )
    # sdr_backend.SDRDevice：supports_tx + frequency_range
    if bool(getattr(device, "supports_tx", False)):
        dtype = str(getattr(device, "device_type", ""))
        prof = _DEVICE_TX_PROFILES.get(_driver_key(dtype), {})
        freq = getattr(device, "frequency_range", None)
        if prof and freq and float(freq[1]) > 0:
            return TxCapabilities(
                known=True,
                freq_range_hz=tuple(prof["freq"]),       # TX 专用量程
                bandwidth_range_hz=None,
                gain_range_db=tuple(prof["gain"]),
                duplex=str(prof.get("duplex", "unknown")),
                device_name=str(getattr(device, "name", dtype)),
                source=str(prof.get("source", "")),
            )
    return TxCapabilities(
        known=False,
        device_name=str(getattr(device, "name", "")),
        source="设备能力未知（无 TX 量程声明）",
    )


_HAL_DRIVER_KEYS = {
    "soapyhackrf": "hackrf",
    "soapybladerf": "bladerf",
    "soapylms7": "limesdr",
    "soapylime": "limesdr",
}


def capabilities_from_hal_backend(backend: Any) -> TxCapabilities:
    """从 ``hal.SDRBackendBase`` 推导 TX 能力（惰性导入避免循环依赖）。"""
    if backend is None:
        return TxCapabilities(known=False, source="无设备")
    try:
        info = backend.get_info()
    except Exception:  # noqa: BLE001
        info = None
    key = _driver_key(getattr(info, "driver", "") if info else "")
    if not key:
        key = _HAL_DRIVER_KEYS.get(str(getattr(info, "driver", "")).lower(), "")
    if not key:
        # Soapy 的 get_info().driver 常为 "soapy"，真实硬件名在 name/args 里。
        key = _driver_key(str(getattr(info, "name", "")))
    if not key:
        key = _driver_key(str(getattr(backend, "device_args", "")))
    prof = _DEVICE_TX_PROFILES.get(key)
    if not prof:
        return TxCapabilities(
            known=False,
            device_name=str(getattr(info, "name", backend.__class__.__name__)),
            source="SoapySDR 未声明 TX 量程，能力未知")
    return TxCapabilities(
        known=True,
        freq_range_hz=tuple(prof["freq"]),
        bandwidth_range_hz=None,
        gain_range_db=tuple(prof["gain"]),
        duplex=str(prof.get("duplex", "unknown")),
        device_name=str(getattr(info, "name", backend.__class__.__name__)),
        source=str(prof.get("source", "")),
    )


def policy_from_env(env: Optional[Dict[str, str]] = None) -> Optional[RegulatoryPolicy]:
    """从 ``MBDSDR_TX_BANDS``（如 "144000000-148000000,430000000-440000000"）
    读取法规允许频段；未配置返回 None（调用方必须如实标注未配置）。
    """
    import os as _os
    src = env or _os.environ
    raw = (src.get("MBDSDR_TX_BANDS") or "").strip()
    if not raw:
        return None
    bands: List[RegulatoryBand] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            lo, hi = part.split("-")
            bands.append(RegulatoryBand(float(lo), float(hi),
                                        label=part,
                                        basis=f"MBDSDR_TX_BANDS={part}"))
        except ValueError:
            logger.warning("MBDSDR_TX_BANDS 片段无法解析，已忽略: %s", part)
    if not bands:
        return None
    max_dur = None
    if src.get("MBDSDR_TX_MAX_DURATION_S"):
        try:
            max_dur = float(src["MBDSDR_TX_MAX_DURATION_S"])
        except ValueError:
            logger.warning("MBDSDR_TX_MAX_DURATION_S 非数值，已忽略")
    return RegulatoryPolicy(name="env:MBDSDR_TX_BANDS", bands=bands,
                            max_tx_duration_s=max_dur,
                            basis="操作员通过环境变量声明")


def check_tx_request(
    caps: Optional[TxCapabilities],
    *,
    freq_hz: Optional[float],
    bandwidth_hz: Optional[float] = None,
    gain_db: Optional[float] = None,
    duration_s: Optional[float] = None,
    regulatory: Optional[RegulatoryPolicy] = None,
    require_regulatory: bool = False,
) -> TxLimitCheck:
    """逐项检查频率/带宽/增益/时长；越界或能力未知一律拒绝，不静默截断。"""
    caps = caps or TxCapabilities(known=False, source="未提供设备能力")
    violations: List[Dict[str, Any]] = []
    basis: List[str] = []
    field_status: Dict[str, str] = {}

    def _bad(field_name: str, value: Any, reason: str) -> None:
        violations.append({"field": field_name, "value": value, "reason": reason})

    if freq_hz is None or not float(freq_hz) > 0:
        return TxLimitCheck(False, LIMIT_BAD_PARAM,
                            "频点 freq_hz 缺失或非正——安全关键参数不猜",
                            {"frequency": "bad_parameter"})
    freq_hz = float(freq_hz)
    field_status["frequency"] = "ok"

    # 频率：设备能力
    if not caps.known or caps.freq_range_hz is None:
        field_status["frequency"] = "capability_unknown"
        _bad("frequency", freq_hz, "设备 TX 频率范围未知，需用户确认，不能假设")
    else:
        lo, hi = caps.freq_range_hz
        if not (lo <= freq_hz <= hi):
            field_status["frequency"] = "out_of_range"
            _bad("frequency", freq_hz, f"超出设备 TX 频率范围 {lo:.0f}-{hi:.0f} Hz")
        basis.append(f"设备 TX 频率范围 {lo:.0f}-{hi:.0f} Hz：{caps.source}")

    # 带宽：只有声明了区间才可比；请求了带宽但能力未知 → 诚实拒绝
    if bandwidth_hz is not None:
        bandwidth_hz = float(bandwidth_hz)
        if not float(bandwidth_hz) > 0:
            field_status["bandwidth"] = "bad_parameter"
            _bad("bandwidth", bandwidth_hz, "带宽须为正数")
        elif caps.bandwidth_range_hz is None:
            field_status["bandwidth"] = "capability_unknown"
            _bad("bandwidth", bandwidth_hz, "设备 TX 带宽范围未知，需用户确认，不能假设")
        else:
            lo, hi = caps.bandwidth_range_hz
            if not (lo <= bandwidth_hz <= hi):
                field_status["bandwidth"] = "out_of_range"
                _bad("bandwidth", bandwidth_hz,
                     f"超出设备 TX 带宽范围 {lo:.0f}-{hi:.0f} Hz")
            else:
                field_status["bandwidth"] = "ok"
            basis.append(f"设备 TX 带宽范围 {lo:.0f}-{hi:.0f} Hz")

    # 增益
    if gain_db is not None:
        gain_db = float(gain_db)
        if caps.gain_range_db is None:
            field_status["gain"] = "capability_unknown"
            _bad("gain", gain_db, "设备 TX 增益范围未知，需用户确认，不能假设")
        else:
            lo, hi = caps.gain_range_db
            if not (lo <= gain_db <= hi):
                field_status["gain"] = "out_of_range"
                _bad("gain", gain_db, f"超出设备 TX 增益范围 {lo:g}-{hi:g} dB")
            else:
                field_status["gain"] = "ok"
            basis.append(f"设备 TX 增益范围 {lo:g}-{hi:g} dB：{caps.source}")

    # 时长：设备上限或法规占空比
    if duration_s is not None:
        duration_s = float(duration_s)
        if not duration_s > 0:
            field_status["duration"] = "bad_parameter"
            _bad("duration", duration_s, "发射时长须为正数")
        else:
            limit = caps.max_duration_s
            reg_limit = regulatory.max_tx_duration_s if regulatory else None
            allowed_max = min([v for v in (limit, reg_limit) if v is not None],
                              default=None)
            if allowed_max is None:
                field_status["duration"] = "unknown"
            elif duration_s > float(allowed_max):
                field_status["duration"] = "out_of_range"
                _bad("duration", duration_s,
                     f"超出允许发射时长上限 {float(allowed_max):g}s")
                basis.append(f"发射时长上限 {float(allowed_max):g}s")
            else:
                field_status["duration"] = "ok"

    # 法规频段
    if regulatory is not None:
        hit = [b for b in regulatory.bands
               if float(b.lo_hz) <= freq_hz <= float(b.hi_hz)]
        if not hit:
            field_status["regulatory"] = "out_of_range"
            _bad("regulatory", freq_hz,
                 "不在操作员声明的允许发射频段内")
            basis.append(
                "允许频段：" + ", ".join(b.label or
                f"{b.lo_hz:.0f}-{b.hi_hz:.0f}Hz" for b in regulatory.bands))
        else:
            field_status["regulatory"] = "ok"
            basis.append(f"命中允许频段 {hit[0].label}（{regulatory.basis}）")
    elif require_regulatory:
        field_status["regulatory"] = "unconfigured"
        _bad("regulatory", freq_hz,
             "法规允许频段未配置，需用户确认（不假设合规）")

    if violations:
        codes = {_field_code(v["field"]) for v in violations}
        code = (LIMIT_CAP_UNKNOWN
                if field_status.get("frequency") == "capability_unknown"
                or "capability_unknown" in field_status.values()
                else (LIMIT_REGULATORY
                      if "regulatory" in codes else sorted(codes)[0]))
        reason = "；".join(v["reason"] for v in violations)
        return TxLimitCheck(False, code, reason, field_status, violations, basis)
    return TxLimitCheck(True, LIMIT_OK, "参数在设备/法规允许范围内",
                        field_status, [], basis)


def _field_code(field_name: str) -> str:
    return {
        "frequency": LIMIT_FREQ,
        "bandwidth": LIMIT_BANDWIDTH,
        "gain": LIMIT_GAIN,
        "duration": LIMIT_DURATION,
        "regulatory": LIMIT_REGULATORY,
    }.get(field_name, LIMIT_BAD_PARAM)


# ═════════════════════════════════════════════════════════════════════════
# 三、发射状态机：取消 / 设备掉线 / 缓冲欠载 / 门撤销 立即停发
# ═════════════════════════════════════════════════════════════════════════

TX_IDLE = "idle"
TX_ARMED = "armed"
TX_TRANSMITTING = "transmitting"
TX_STOPPING = "stopping"
TX_COMPLETED = "completed"
TX_CANCELLED = "cancelled"
TX_ABORTED = "aborted"
TX_REJECTED = "rejected"
TX_FAILED = "failed"

TX_CANCEL_REQUESTED = "tx_cancel_requested"
TX_DEVICE_LOST = "tx_device_lost"
TX_UNDERRUN = "tx_underrun"
TX_GATE_REVOKED = "tx_gate_revoked"
TX_GATE_EXPIRED = "tx_gate_expired"
TX_WRITE_FAILED = "tx_write_failed"
TX_NO_BACKEND = "tx_no_backend"


class TxSession:
    """一次发射的状态机。

    ``transmit()`` 在每个分块前检查四种中止条件（取消/掉线/欠载/撤门），
    命中即停写、调用 backend.stop_tx()（若存在）并落如实状态；不会留下
    持续输出的循环。门在发射中途被撤销同样在下一块前生效。
    """

    def __init__(self, backend: Any, *, gate: Optional[TxGate] = None,
                 cancel_event: Optional[threading.Event] = None) -> None:
        self.backend = backend
        self.gate = gate or get_tx_gate()
        self.cancel_event = cancel_event or threading.Event()
        self.state = TX_IDLE
        self.reason_code = ""
        self.reason = ""
        self.sent_samples = 0
        self.total_samples = 0
        self.token = ""
        self._cancel_reason = "用户取消发射"
        self._gate_reason = ""
        self._action = ""
        self.stop_calls = 0
        self._duplex_coord: Optional[TxRxCoordinator] = None
        self._duplex_info: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    def cancel(self, reason: str = "用户取消发射") -> None:
        self._cancel_reason = str(reason or "用户取消发射")
        self.cancel_event.set()

    def status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "sent_samples": int(self.sent_samples),
            "total_samples": int(self.total_samples),
            "gate": self.gate.snapshot(),
            "duplex": dict(self._duplex_info) if self._duplex_info else None,
        }

    # ------------------------------------------------------------------
    def _stop_backend(self) -> None:
        try:
            stop = getattr(self.backend, "stop_tx", None)
            if callable(stop):
                stop()
                self.stop_calls += 1
        except Exception:  # noqa: BLE001 - 停发失败不掩盖原中止原因
            logger.warning("TxSession: backend.stop_tx() 调用失败", exc_info=True)

    def _device_alive(self) -> bool:
        checker = getattr(self.backend, "device_alive", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:  # noqa: BLE001
                return False
        status = getattr(self.backend, "status", None)
        connected = getattr(status, "connected", None)
        if connected is not None:
            return bool(connected)
        return True

    def _underrun(self) -> bool:
        checker = getattr(self.backend, "is_underrun", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:  # noqa: BLE001
                return True
        return bool(getattr(self.backend, "underrun", False))

    def _gate_still_valid(self, decision: GateDecision) -> Optional[str]:
        snap = self.gate.snapshot()
        if snap["state"] == "revoked":
            self._gate_reason = snap.get("revoked_reason", "")
            return TX_GATE_REVOKED
        if self.token:
            d = self.gate.authorize(token=self.token, action=self._action,
                                    consume=False)
            if not d.allowed:
                self._gate_reason = d.reason
                return (TX_GATE_EXPIRED if d.code == GATE_EXPIRED
                        else TX_GATE_REVOKED)
        return None

    def _release_duplex(self) -> None:
        if self._duplex_coord is None:
            return
        resume_s = self._duplex_coord.release()
        info = dict(self._duplex_info or {})
        info["resume_time_s"] = float(resume_s)
        info["total_switch_time_s"] = float(
            info.get("switch_time_s", 0.0) + resume_s)
        self._duplex_info = info
        self._duplex_coord = None

    def _abort(self, state: str, code: str, reason: str) -> Dict[str, Any]:
        self.state = TX_STOPPING
        self.reason_code = code
        self.reason = reason
        self._stop_backend()
        self._release_duplex()
        self.state = state
        logger.warning("TxSession %s(%s): %s（已发 %d/%d 样本）",
                       state, code, reason, self.sent_samples, self.total_samples)
        return self.status()

    # ------------------------------------------------------------------
    def transmit(
        self,
        iq_blocks: Any,
        *,
        freq_hz: Optional[float],
        arm_manual: bool = False,
        tx_token: Optional[str] = None,
        action: Optional[str] = None,
        gain_db: Optional[float] = None,
        bandwidth_hz: Optional[float] = None,
        caps: Optional[TxCapabilities] = None,
        regulatory: Optional[RegulatoryPolicy] = None,
        duration_s: Optional[float] = None,
        chunk_samples: int = 4096,
        rx_control: Any = None,
        rx_active: bool = False,
        duplex: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行一次受控发射，返回 :meth:`status` 同构的如实报告。

        授权来源只有两类：外部确认通道签发的 token（``tx_token`` + ``action``），
        或上层门已建立的一次性授权上下文（``tx_authorization``）。
        ``arm_manual`` 仅保留参数位，**不参与放行**（模型自报不算确认）。
        """
        ctx = current_tx_decision()
        if ctx is not None and ctx.allowed and (
                tx_token is None or not ctx.token or ctx.token == tx_token):
            decision = ctx
        else:
            decision = self.gate.authorize(
                arm_manual=False, token=tx_token,
                action=action if action is not None else None)
        if not decision.allowed:
            self.state = TX_REJECTED
            self.reason_code = decision.code
            self.reason = decision.reason
            return self.status()
        self.token = decision.token
        self._action = decision.action
        self.state = TX_ARMED
        self.sent_samples = 0

        if self.backend is None or not callable(
                getattr(self.backend, "write_tx", None)):
            return self._abort(TX_REJECTED, TX_NO_BACKEND,
                               "没有可用的 TX 后端")
        supports = getattr(self.backend, "supports_tx", None)
        if callable(supports) and not supports():
            return self._abort(TX_REJECTED, TX_NO_BACKEND,
                               "当前后端不支持 TX 发射")

        if caps is None:
            caps = capabilities_from_hal_backend(self.backend)
        chk = check_tx_request(caps, freq_hz=freq_hz, bandwidth_hz=bandwidth_hz,
                               gain_db=gain_db, duration_s=duration_s,
                               regulatory=regulatory)
        if not chk.ok:
            self.state = TX_REJECTED
            self.reason_code = chk.code
            self.reason = chk.reason
            return self.status()

        # 收发互斥：语义统一在这里，半双工暂停接收并如实报切换耗时。
        duplex_mode = (duplex or caps.duplex or DUPLEX_UNKNOWN).lower()
        coord = TxRxCoordinator()
        dd = coord.acquire(
            duplex=duplex_mode,
            rx_active=bool(rx_active),
            pause_rx=getattr(rx_control, "pause_rx", None),
            resume_rx=getattr(rx_control, "resume_rx", None),
        )
        self._duplex_info = dd.to_dict()
        if not dd.allowed:
            self.state = TX_REJECTED
            self.reason_code = dd.code
            self.reason = dd.reason
            return self.status()
        self._duplex_coord = coord

        setter = getattr(self.backend, "set_frequency", None)
        if callable(setter):
            setter(float(freq_hz))

        blocks = self._iter_blocks(iq_blocks, chunk_samples)
        self.state = TX_TRANSMITTING
        try:
            for block in blocks:
                if self.cancel_event.is_set():
                    return self._abort(TX_CANCELLED, TX_CANCEL_REQUESTED,
                                       self._cancel_reason)
                code = self._gate_still_valid(decision)
                if code:
                    return self._abort(TX_ABORTED, code,
                                       f"发射中人工门失效：{self._gate_reason}")
                if not self._device_alive():
                    return self._abort(TX_ABORTED, TX_DEVICE_LOST,
                                       "发射中设备掉线/句柄失效，已立即停发")
                if self._underrun():
                    return self._abort(TX_ABORTED, TX_UNDERRUN,
                                       "发射缓冲欠载，已立即停发")
                with tx_authorization(decision):
                    ok = self.backend.write_tx(block)
                if not ok:
                    return self._abort(TX_FAILED, TX_WRITE_FAILED,
                                       "设备 write_tx 返回失败，已停发")
                self.sent_samples += int(np_size(block))
        except Exception as e:  # noqa: BLE001 - 驱动异常同样必须停发
            return self._abort(TX_FAILED, TX_WRITE_FAILED,
                               f"写波异常，已停发: {e}")
        self.state = TX_COMPLETED
        self.reason_code = "ok"
        self.reason = "发射完成"
        self._release_duplex()
        return self.status()

    @staticmethod
    def _iter_blocks(iq_blocks: Any, chunk_samples: int) -> Iterator[Any]:
        if hasattr(iq_blocks, "shape"):
            n = int(getattr(iq_blocks, "size", 0))
            step = max(1, int(chunk_samples))
            for start in range(0, n, step):
                yield iq_blocks[start:start + step]
            return
        for block in iq_blocks:
            yield block


def np_size(block: Any) -> int:
    size = getattr(block, "size", None)
    if size is not None:
        return int(size)
    try:
        return len(block)
    except TypeError:
        return 0


# ═════════════════════════════════════════════════════════════════════════
# 四、收发互斥：语义只在本模块实现，半双工切换时间实测上报
# ═════════════════════════════════════════════════════════════════════════

DUPLEX_FULL = "full"
DUPLEX_HALF = "half"
DUPLEX_UNKNOWN = "unknown"

RX_IDLE = "rx_idle"
RX_PAUSED = "rx_paused"
RX_KEPT = "rx_kept"
RX_CONFLICT = "rx_conflict"
DUPLEX_CAPABILITY_UNKNOWN = "duplex_unknown"


@dataclass
class DuplexDecision:
    """收发互斥判定：接收是暂停、保留、本就空闲，还是明确拒绝。"""

    allowed: bool
    code: str
    reason: str
    mode: str
    switch_time_s: float = 0.0
    basis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "mode": self.mode,
            "switch_time_s": float(self.switch_time_s),
            "basis": self.basis,
        }


class TxRxCoordinator:
    """统一收发互斥语义。

    * 半双工 + 接收活跃 → 暂停接收；切换耗时实测后如实返回。
    * 全双工 → 保留接收（不暂停）。
    * 双工能力未知 + 接收活跃 → 明确拒绝发射（不假设可以边收边发）。
    * 接收本就空闲 → 直接放行（``rx_idle``）。
    暂停/恢复不可用或失败 → ``rx_conflict`` 明确报因，绝不静默丢弃接收。
    """

    def __init__(self, clock: Any = None) -> None:
        self._clock = clock or time.monotonic
        self._paused = False
        self._resume_cb = None
        self._decision: Optional[DuplexDecision] = None

    def acquire(self, *, duplex: str, rx_active: bool,
                pause_rx: Any = None, resume_rx: Any = None) -> DuplexDecision:
        duplex = (duplex or DUPLEX_UNKNOWN).lower()
        if not rx_active:
            dd = DuplexDecision(True, RX_IDLE, "接收本就空闲，无需切换",
                                RX_IDLE, 0.0, f"duplex={duplex}")
        elif duplex == DUPLEX_FULL:
            dd = DuplexDecision(True, RX_KEPT, "全双工：发射时保留接收",
                                RX_KEPT, 0.0, "设备声明全双工（独立 RX/TX 通道）")
        elif duplex == DUPLEX_HALF:
            if not callable(pause_rx):
                dd = DuplexDecision(
                    False, RX_CONFLICT,
                    "半双工设备正在接收，但接入层无法暂停接收——拒绝发射，"
                    "不静默丢弃接收数据", RX_CONFLICT, 0.0, "duplex=half")
            elif not callable(resume_rx):
                dd = DuplexDecision(
                    False, RX_CONFLICT,
                    "半双工设备只能暂停接收、无法恢复——拒绝发射",
                    RX_CONFLICT, 0.0, "duplex=half")
            else:
                t0 = self._clock()
                try:
                    ok = pause_rx()
                except Exception as e:  # noqa: BLE001
                    dd = DuplexDecision(False, RX_CONFLICT,
                                        f"暂停接收失败，拒绝发射: {e}",
                                        RX_CONFLICT, 0.0, "duplex=half")
                    self._decision = dd
                    return dd
                dt = max(0.0, self._clock() - t0)
                if ok is False or ok is None:
                    dd = DuplexDecision(False, RX_CONFLICT,
                                        "暂停接收返回失败，拒绝发射",
                                        RX_CONFLICT, dt, "duplex=half")
                else:
                    self._paused = True
                    self._resume_cb = resume_rx
                    dd = DuplexDecision(
                        True, RX_PAUSED,
                        f"半双工：已暂停接收，切换耗时 {dt * 1e3:.1f} ms",
                        RX_PAUSED, dt, "duplex=half")
        else:
            dd = DuplexDecision(
                False, DUPLEX_CAPABILITY_UNKNOWN,
                "设备双工能力未知且接收活跃——需用户确认，不能假设可边收边发",
                DUPLEX_CAPABILITY_UNKNOWN, 0.0, f"duplex={duplex}")
        self._decision = dd
        return dd

    def release(self) -> float:
        """发射结束恢复接收；返回恢复耗时（秒），未暂停则为 0。"""
        if not self._paused or not callable(self._resume_cb):
            return 0.0
        t0 = self._clock()
        try:
            self._resume_cb()
        except Exception:  # noqa: BLE001
            logger.warning("TxRxCoordinator: 恢复接收失败", exc_info=True)
        dt = max(0.0, self._clock() - t0)
        self._paused = False
        self._resume_cb = None
        return dt

    def snapshot(self) -> Dict[str, Any]:
        return {
            "decision": self._decision.to_dict() if self._decision else None,
            "rx_paused": self._paused,
        }




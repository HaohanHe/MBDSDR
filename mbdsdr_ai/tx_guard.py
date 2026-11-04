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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "state": self.state,
            "token": self.token,
        }


class TxGate:
    """进程级发射人工门。

    语义：
      * ``arm(ttl_s)`` 由人显式调用，签发一次性 token；token 在 TTL 内有效。
      * ``revoke()`` 撤销授权：此后连 ``arm_manual=True`` 也拒绝，直到重新 arm。
      * 不提供任何"永久放行"开关；``arm_manual=True`` 视为本次调用的人工确认，
        但显式撤销状态下仍然无效。
    """

    def __init__(self, default_ttl_s: float = DEFAULT_TTL_S) -> None:
        self._lock = threading.RLock()
        self._state = "disarmed"      # disarmed / armed / expired / revoked
        self._token = ""
        self._armed_at = 0.0
        self._ttl_s = float(default_ttl_s)
        self._revoked_reason = ""
        self._generation = 0
        self._last_decision: Optional[GateDecision] = None

    # ------------------------------------------------------------------
    def arm(self, *, ttl_s: Optional[float] = None,
            note: str = "", now: Optional[float] = None) -> str:
        """人工确认发射。返回本次授权 token（TTL 内可复用）。"""
        with self._lock:
            ttl = self._ttl_s if ttl_s is None else float(ttl_s)
            if ttl <= 0:
                raise ValueError("ttl_s 必须为正数（授权必须有有效期）")
            self._state = "armed"
            self._token = uuid.uuid4().hex
            self._armed_at = time.monotonic() if now is None else float(now)
            self._ttl_s = ttl
            self._revoked_reason = ""
            self._generation += 1
            d = GateDecision(True, GATE_OK,
                             f"人工门已开（{ttl:.0f}s 有效）" +
                             (f"：{note}" if note else ""),
                             self._state, self._token)
            self._last_decision = d
            logger.info("TxGate armed: ttl=%.1fs note=%s", ttl, note)
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
                  now: Optional[float] = None) -> GateDecision:
        """判定本次发射是否被允许。token 路径与 ``arm_manual`` 都走这里。"""
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
                else:
                    d = GateDecision(True, GATE_OK,
                                     "人工门 token 有效", self._state, self._token)
            elif arm_manual is True:
                # 本次调用显式人工确认：等价于本次一次性 arm。
                d = GateDecision(True, GATE_OK,
                                 "本次调用显式 arm_manual=true", state)
            else:
                d = GateDecision(False, GATE_NOT_ARMED,
                                 "未过人工门：需显式 arm_manual=true 或有效 token",
                                 state)

            if not d.allowed:
                logger.warning("TxGate 拒绝发射(%s): %s", d.code, d.reason)
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


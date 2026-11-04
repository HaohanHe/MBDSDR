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
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

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

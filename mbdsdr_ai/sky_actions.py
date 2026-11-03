# SPDX-License-Identifier: MIT
"""
MBDSDR - 天空动作执行总线（模型 → 界面，小范围真做）
============================================================

能力协商的最后一环：模型在会话里判断「这轮能做什么」后，通过原子工具
``sky_tune_satellite`` / ``sky_track_satellite`` / ``sky_gimbal_follow``
把执行意图交给本总线；主窗口在 UI 线程排水，落到既有硬件动作上。

设计上刻意与 ``ui_actions`` 分开：
  - ``ui_actions`` 是 A2UI 契约（动词白名单被契约测试钉死，不扩张）；
  - 本总线只承载天空动作，动词是卫星目标语义，不是布局语义。

边界：
  - 只允许白名单动作；未知动作/空目标 → 诚实 PARAM_ERROR；
  - 执行器未挂载（非桌面环境）→ 诚实 UNAVAILABLE，不假装硬件动了；
  - 这里不做「模型是否该执行」的判断——那是模型基于会话上下文决定的。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

SKY_ACTIONS = ("tune", "track", "gimbal_follow")


def validate_sky_action(action: Dict[str, Any]) -> Optional[str]:
    """校验一条天空动作信封；合法返回 None，非法返回原因。"""
    kind = str(action.get("action", ""))
    if kind not in SKY_ACTIONS:
        return f"未知天空动作 {kind!r}：白名单 {'/'.join(SKY_ACTIONS)}"
    if not str(action.get("satellite", "")).strip():
        return "天空动作需要非空 satellite（目标卫星名）"
    if kind in ("track", "gimbal_follow"):
        on = action.get("on", True)
        if not isinstance(on, bool):
            return f"{kind} 的 on 必须是布尔值，实际 {on!r}"
    freq = action.get("frequency_hz")
    if freq is not None:
        try:
            if float(freq) <= 0:
                return "frequency_hz 必须为正数"
        except (TypeError, ValueError):
            return f"frequency_hz 不是数值：{freq!r}"
    return None


@dataclass
class SkyActionBus:
    """进程级天空动作总线：校验 → 排队 → UI 线程排水执行。"""

    executor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _queue: List[Dict[str, Any]] = field(default_factory=list)
    # 最近一次执行结果（供会话回执/诊断，不参与决策）
    last_result: Dict[str, Any] = field(default_factory=dict)

    def enqueue(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """校验并入队（AIWorker 后台线程安全）。"""
        err = validate_sky_action(action)
        if err is not None:
            return {"ok": False, "status": "PARAM_ERROR", "reason": err}
        if self.executor is None:
            return {"ok": False, "status": "UNAVAILABLE",
                    "reason": "天空动作执行器未挂载（非桌面环境），硬件未动作"}
        with self._lock:
            self._queue.append(dict(action))
        return {"ok": True, "status": "QUEUED",
                "action": dict(action),
                "reason": "已提交到界面执行队列，由主窗口在 UI 线程落地"}

    def drain(self) -> List[Dict[str, Any]]:
        """UI 线程排水：执行全部排队动作，返回每条的执行结果。"""
        with self._lock:
            batch, self._queue = self._queue, []
        results: List[Dict[str, Any]] = []
        for action in batch:
            if self.executor is None:
                res: Dict[str, Any] = {
                    "ok": False, "status": "UNAVAILABLE",
                    "reason": "天空动作执行器未挂载"}
            else:
                try:
                    res = self.executor(action) or {}
                except Exception as e:  # noqa: BLE001 —— 执行失败诚实回执
                    res = {"ok": False, "status": "ERROR",
                           "reason": f"{type(e).__name__}: {e}"}
            result = {"action": dict(action), **res}
            self.last_result = dict(result)
            results.append(result)
        return results

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)


# ---------------------------------------------------------------------------
# 进程级单例（agent 工具面与 desktop 主窗口共用）
# ---------------------------------------------------------------------------
_BUS: Optional[SkyActionBus] = None


def get_sky_bus() -> SkyActionBus:
    """返回进程级天空动作总线单例。"""
    global _BUS
    if _BUS is None:
        _BUS = SkyActionBus()
    return _BUS


def _reset_sky_bus_for_tests() -> None:
    """测试专用：清空总线（进程内全局态会跨测试串扰）。"""
    global _BUS
    _BUS = None

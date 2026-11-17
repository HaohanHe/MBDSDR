# SPDX-License-Identifier: MIT
"""
MBDSDR - 天空动作执行总线（模型 → 界面，小范围真做）
============================================================

能力协商的最后一环：模型在会话里判断「这轮能做什么」后，通过原子工具
``sky_tune_satellite`` / ``sky_track_satellite`` / ``sky_gimbal_follow``
把执行意图交给本总线；主窗口在 UI 线程排水，落到既有硬件动作上。

``sky_look_at`` 是唯一**不碰硬件**的动作：只把天空视图的视轴转到给定
(方位, 仰角)，可选同时设定视场。自然语言「看向天顶偏南」因此可执行，
而不是只能被解释成一次查询。

设计上刻意与 ``ui_actions`` 分开：
  - ``ui_actions`` 是 A2UI 契约（动词白名单被契约测试钉死，不扩张）；
  - 本总线只承载天空动作，动词是卫星目标语义，不是布局语义。

边界：
  - 只允许白名单动作；未知动作/空目标 → 诚实 PARAM_ERROR；
  - 执行器未挂载（非桌面环境）→ 诚实 UNAVAILABLE，不假装硬件动了；
  - 这里不做「模型是否该执行」的判断——那是模型基于会话上下文决定的。
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

SKY_ACTIONS = ("tune", "track", "gimbal_follow", "look_at")


def validate_sky_action(action: Dict[str, Any]) -> Optional[str]:
    """校验一条天空动作信封；合法返回 None，非法返回原因。"""
    kind = str(action.get("action", ""))
    if kind not in SKY_ACTIONS:
        return f"未知天空动作 {kind!r}：白名单 {'/'.join(SKY_ACTIONS)}"
    if kind == "look_at":
        # 视图动作只认角度，不需要卫星：给「看向天顶偏南」这类指令留通路。
        for key in ("az_deg", "alt_deg"):
            try:
                val = float(action.get(key))
            except (TypeError, ValueError):
                return f"look_at 需要数值 {key}，实际 {action.get(key)!r}"
            if not math.isfinite(val):
                return f"look_at 的 {key} 不是有限数：{action.get(key)!r}"
            if key == "alt_deg" and not -90.0 <= val <= 90.0:
                return f"look_at 的 alt_deg 必须在 -90..90，实际 {val}"
        fov = action.get("fov_deg")
        if fov is not None:
            try:
                fov = float(fov)
            except (TypeError, ValueError):
                return f"look_at 的 fov_deg 不是数值：{fov!r}"
            if not 0.0 < fov <= 180.0:
                return f"look_at 的 fov_deg 必须在 (0,180]，实际 {fov}"
        return None
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


def apply_look_at(sky_view: Any, action: Dict[str, Any]) -> Dict[str, Any]:
    """把 ``look_at`` 动作落到天空视图（必须在 UI 线程调用）。

    只改视角状态，不下发任何硬件指令——这是它与 tune/track/gimbal_follow
    的根本区别。天空视图未打开时诚实返回 UNAVAILABLE，不假装转了视角。
    """
    if sky_view is None:
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": "天空视图未打开，视角未改变"}
    view = getattr(sky_view, "view_state", None)
    if view is None or not hasattr(view, "look_at"):
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": "天空视图没有可用的视角状态，视角未改变"}
    err = validate_sky_action(action)
    if err is not None:
        return {"ok": False, "status": "PARAM_ERROR", "reason": err}
    view.look_at(float(action["az_deg"]), float(action["alt_deg"]))
    if action.get("fov_deg") is not None and hasattr(view, "zoom_to"):
        view.zoom_to(float(action["fov_deg"]))
    repaint = getattr(sky_view, "update", None)
    if callable(repaint):
        repaint()
    return {"ok": True, "status": "DONE",
            "az_deg": float(view.center_az),
            "alt_deg": float(view.center_alt),
            "fov_deg": float(view.fov_deg)}


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

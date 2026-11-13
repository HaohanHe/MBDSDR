# SPDX-License-Identifier: MIT
"""
MBDSDR - AI 动态 UI 动作总线（模型 → 界面，小范围真做）
================================================================

背景（审计）：A2UI 协议与 HTML 渲染器真实，但 Python 桌面零消费——模型
说「打开频谱」界面纹丝不动。本模块是最小真闭环：

  模型（LLM 工具调用 ``ui_layout_control``）
    → 本总线校验 + 排队（AIWorker 后台线程安全）
    → 主窗口 UI 线程定时排水执行（desktop/main_window 挂载执行器）
    → 可撤销（每个动作入栈逆操作，``ui_layout_undo`` 弹出）

动词白名单（本轮三个动词族，不铺满）：
  - ``show_panel`` / ``hide_panel``   显示/隐藏面板（target=面板 id）
  - ``select_tab`` / ``focus_panel``  切换/聚焦到指定页签（target=面板 id）
  - ``set_layout_ratio``              调整三栏比例
                                      （value="left:center:right"，如 "2:2:1"）

安全边界：
  - 界面动作**不**走 arm_manual 写工具人工门（不动硬件/文件/发射），
    但**必须可撤销**：每个动作执行前记录逆操作；
  - 未知动词/未知目标/坏比例 → 诚实拒绝（PARAM_ERROR），不静默猜；
  - 执行器未挂载（非桌面环境）→ 诚实 UNAVAILABLE，不假装界面变了。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

UI_ACTION_VERBS = (
    "show_panel", "hide_panel", "select_tab", "focus_panel", "set_layout_ratio",
)


def parse_layout_ratio(value: Any) -> Optional[List[int]]:
    """把 ``'左:中:右'`` 解析成三个非负整数；非法返回 None。

    校验器（``UiActionBus.validate``）与桌面执行器（``_execute_ui_action``）
    共用这一份：执行器被直连（绕过 validate）时也不能拿到两档/四档比例，
    更不能拿 ``'0:0:0'`` 去整除（ZeroDivisionError）。
    """
    parts = str(value or "").split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if sum(nums) == 0:
        return None
    return nums


@dataclass
class UiActionBus:
    """进程级 UI 动作总线（校验→排队→UI 线程排水→逆栈可撤销）。"""

    executor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _queue: List[Dict[str, Any]] = field(default_factory=list)
    _undo_stack: List[Dict[str, Any]] = field(default_factory=list)

    # ---------------------------------------------------------------- 校验
    def validate(self, action: Dict[str, Any]) -> Optional[str]:
        """校验动作信封；合法返回 None，非法返回原因（诚实拒绝）。"""
        verb = str(action.get("verb", ""))
        if verb not in UI_ACTION_VERBS:
            return (f"未知动词 {verb!r}：白名单 "
                    f"{'/'.join(UI_ACTION_VERBS)}")
        if verb == "set_layout_ratio":
            if parse_layout_ratio(action.get("value")) is None:
                return ("set_layout_ratio 需要 value='左:中:右' 三个非负整数"
                        "且不全为 0（如 '2:2:1'），"
                        f"实际 {action.get('value')!r}")
        else:
            if not str(action.get("target", "")).strip():
                return f"动词 {verb} 需要非空 target（面板 id）"
        return None

    # ---------------------------------------------------------------- 入队
    def enqueue(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """校验并入队（AIWorker 线程安全）。非法/无执行器诚实拒绝。"""
        err = self.validate(action)
        if err is not None:
            return {"ok": False, "status": "PARAM_ERROR", "reason": err}
        if self.executor is None:
            return {"ok": False, "status": "UNAVAILABLE",
                    "reason": "UI 执行器未挂载（非桌面环境），界面未改变"}
        with self._lock:
            self._queue.append(dict(action))
        return {"ok": True, "status": "QUEUED", "action": dict(action)}

    # ---------------------------------------------------------------- 排水
    def drain(self) -> List[Dict[str, Any]]:
        """UI 线程排水：执行全部排队动作并记录逆操作。返回执行结果列表。"""
        with self._lock:
            batch, self._queue = self._queue, []
        results = []
        for action in batch:
            try:
                res = self.executor(action) if self.executor else {
                    "ok": False, "status": "UNAVAILABLE",
                    "reason": "UI 执行器未挂载"}
            except Exception as e:  # noqa: BLE001 —— 执行失败诚实回执
                res = {"ok": False, "status": "ERROR",
                       "reason": f"{type(e).__name__}: {e}"}
            if res.get("ok") and res.get("inverse") is not None:
                with self._lock:
                    self._undo_stack.append(res["inverse"])
            results.append({"action": dict(action), **res})
        return results

    # ---------------------------------------------------------------- 撤销
    def undo(self) -> Dict[str, Any]:
        """弹出最近一次界面动作并执行其逆操作。空栈诚实空态。"""
        with self._lock:
            if not self._undo_stack:
                return {"ok": False, "status": "EMPTY",
                        "reason": "没有可撤销的界面动作"}
            inverse = self._undo_stack.pop()
        if self.executor is None:
            # 撤销执行不了就把逆操作放回，不假装撤销了
            with self._lock:
                self._undo_stack.append(inverse)
            return {"ok": False, "status": "UNAVAILABLE",
                    "reason": "UI 执行器未挂载，无法撤销"}
        try:
            res = self.executor(inverse)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._undo_stack.append(inverse)
            return {"ok": False, "status": "ERROR",
                    "reason": f"{type(e).__name__}: {e}"}
        return {"ok": bool(res.get("ok")), "status": res.get("status", "DONE"),
                "undone": inverse}


# ---------------------------------------------------------------------------
# 进程级单例（agent 工具面与 desktop 主窗口共用）
# ---------------------------------------------------------------------------
_BUS: Optional[UiActionBus] = None


def get_ui_bus() -> UiActionBus:
    """返回进程级 UI 动作总线单例。"""
    global _BUS
    if _BUS is None:
        _BUS = UiActionBus()
    return _BUS


def _reset_ui_bus_for_tests() -> None:
    """测试专用：清空总线（进程内全局态泄漏会跨测试串扰）。"""
    global _BUS
    _BUS = None

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-10 独立复现脚本：Offset Tuning 是否「假生效」（勾上就显示开，硬件没动）。

roadmap 行 P0-10 的断言（原文）：
    「Offset Tuning 假生效：勾选＋状态栏双确认，实际未下发」
    证据等级：仅静态推断，未实测。

做法：不需要真机。用 ``object.__new__(RTLSDRBackend)`` 绕过硬件初始化，
只注入 ``_sdr``（驱动桩）与 ``_offset_tuning``，然后直接调产品方法
``supports_offset_tuning()`` / ``set_offset_tuning()`` / ``offset_tuning``。
驱动桩分三种：没有该方法（本机 pyrtlsdr 实际情况）、有且成功、有但抛错。

判定：
  * 驱动没有该方法时，``set_offset_tuning(True)`` 必须返回 False 且状态仍为 False；
    若返回 True 并把状态写成 True，就是「勾上了、硬件没动」的假生效 → 复现。
  * 下发抛错时必须返回 False 且状态保持上一次真正生效值。

退出码（三态）：
    0  复现成功   —— 至少一种桩出现「返回 True / 状态变开」而硬件未接受
    1  未复现     —— 三种桩行为都符合「没生效不写状态」
    2  无法判定   —— 环境不满足 / 反证对照组失效

反证对照组：按修复前实现（先写 ``_offset_tuning`` 再探测能力、探测不到就
只记 debug 并 return True）原样重放，必须得到「返回 True + 状态开 + 驱动
调用 0 次」。对照组失效则本脚本不可用于判定。

用法：
    python scripts/repro/2026-11-11-p0-10-offset-fake-effective.py
"""
from __future__ import annotations

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


class _DriverNoOffset:
    """本机 pyrtlsdr 的 RtlSdrAio：没有 set_offset_tuning。"""


class _DriverWithOffset:
    def __init__(self):
        self.calls = []

    def set_offset_tuning(self, enabled):
        self.calls.append(bool(enabled))


class _DriverThatFails:
    def __init__(self):
        self.fail = True

    def set_offset_tuning(self, enabled):
        if self.fail:
            raise OSError("usb timeout")
        return True


def _legacy_set_offset_tuning(backend, enabled: bool) -> bool:
    """修复前实现（按 0aaa6afe 提交说明与 docstring 复原）。

    先写状态，再探测能力；探测不到只记 debug 并 return True。
    驱动抛错时也只记 warning 并 return False——但状态已经被改掉了。
    """
    logger = logging.getLogger("legacy")
    backend._offset_tuning = bool(enabled)          # ← 旧实现：先写状态
    if not hasattr(backend._sdr, "set_offset_tuning"):
        logger.debug("驱动无 set_offset_tuning，按不支持处理")
        return True                                  # ← 旧实现：假装成功
    try:
        backend._sdr.set_offset_tuning(bool(enabled))
    except Exception as exc:  # noqa: BLE001
        logger.warning("下发失败: %s", exc)
        return False
    return True


def main() -> int:
    try:
        from mbdsdr_ai.sdr_backend import RTLSDRBackend
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] 无法导入 mbdsdr_ai.sdr_backend: {exc!r}")
        return 2

    def _make(driver):
        backend = object.__new__(RTLSDRBackend)   # 绕过硬件初始化
        backend._sdr = driver
        backend._offset_tuning = False
        return backend

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"被测类 : mbdsdr_ai.sdr_backend.RTLSDRBackend（object.__new__ 构造，无硬件）")

    fake_hits = []

    _hr("实测 A：驱动没有 set_offset_tuning（本机 pyrtlsdr 实际情况）")
    drv_a = _DriverNoOffset()
    be_a = _make(drv_a)
    sup_a = be_a.supports_offset_tuning()
    ret_a = be_a.set_offset_tuning(True)
    state_a = be_a.offset_tuning
    print("  请求：set_offset_tuning(True)")
    print(f"  实际：supports={sup_a} 返回值={ret_a} 状态={state_a} "
          f"驱动被调用={hasattr(drv_a, 'set_offset_tuning')}")
    print("  期望：supports=False 返回值=False 状态=False")
    if ret_a or state_a:
        fake_hits.append("A: 不支持的驱动仍报成功/状态变开")

    _hr("实测 B：驱动有该方法且成功（正例，不能被一刀切拦掉）")
    drv_b = _DriverWithOffset()
    be_b = _make(drv_b)
    sup_b = be_b.supports_offset_tuning()
    ret_b = be_b.set_offset_tuning(True)
    state_b = be_b.offset_tuning
    print("  请求：set_offset_tuning(True)")
    print(f"  实际：supports={sup_b} 返回值={ret_b} 状态={state_b} "
          f"驱动收到={drv_b.calls}")
    print("  期望：supports=True 返回值=True 状态=True 驱动收到=[True]")
    if not (sup_b and ret_b and state_b and drv_b.calls == [True]):
        fake_hits.append("B: 支持的驱动没有被真正下发")

    _hr("实测 C：能调用但下发失败（USB 掉线），状态不得改成新值")
    drv_c = _DriverThatFails()
    be_c = _make(drv_c)
    drv_c.fail = False
    ok_on = be_c.set_offset_tuning(True)
    drv_c.fail = True
    ret_c = be_c.set_offset_tuning(False)
    state_c = be_c.offset_tuning
    print("  请求：先 True（成功）→ 再 False（抛 OSError）")
    print(f"  实际：首次={ok_on} 第二次返回值={ret_c} 状态={state_c}")
    print("  期望：首次=True 第二次=False 状态=True（保持上次生效值）")
    if not (ok_on is True and ret_c is False and state_c is True):
        fake_hits.append("C: 下发失败仍改写状态")

    _hr("反证对照组：重放修复前实现（先写状态、探测不到就 return True）")
    drv_l = _DriverNoOffset()
    be_l = _make(drv_l)
    ret_l = _legacy_set_offset_tuning(be_l, True)
    state_l = be_l._offset_tuning
    legacy_reproduced = bool(ret_l) and bool(state_l) and not hasattr(drv_l, "set_offset_tuning")
    print("  请求：legacy_set_offset_tuning(True)")
    print(f"  实际：返回值={ret_l} 状态={state_l} 驱动有方法={hasattr(drv_l, 'set_offset_tuning')}")
    print(f"  对照组复现「假生效」: {legacy_reproduced}")

    _hr("面板读数（可选，无 Qt 时跳过）")
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, os.path.join(ROOT, "desktop"))
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        from status_panel import StatusPanel
        panel = StatusPanel()
        be_panel = _make(_DriverNoOffset())
        panel.update_from_backend(be_panel)
        print(f"  StatusPanel.rx_offset = {panel.rx_offset.text()!r}（期望「关」）")
        if panel.rx_offset.text() != "关":
            fake_hits.append("D: 面板把未生效显示成开")
    except Exception as exc:  # noqa: BLE001
        print(f"  跳过（Qt/StatusPanel 不可用）：{type(exc).__name__}: {exc}")

    _hr("结论")
    current_reproduced = bool(fake_hits)
    print(f"当前实现复现 P0-10 : {current_reproduced}（命中={fake_hits or '无'}）")
    print(f"反证对照组可复现旧行为: {legacy_reproduced}")

    if not legacy_reproduced:
        print("\n[无法判定] 反证对照组没能复现「假生效」，本脚本不可用于判定。")
        return 2
    if current_reproduced:
        print("\n[复现成功] 存在「硬件没接受但界面/状态报开」的路径。")
        return 0
    print("\n[未复现] 不支持/下发失败都不写状态；面板读数为「关」。")
    return 1


if __name__ == "__main__":
    sys.exit(main())

# 补丁计划：`SatTrackPanel` 已销毁后 0 ms 单发定时器仍回调（UI 生命周期缺陷）

> owner：**UI 操作逻辑线**（`desktop/main_window.py` 在其手里；本线只出计划、不改产品代码）
> 发现：2026-11-06 接入层终局跑（HEAD `fc1f8076`）M4 首轮 teardown 打印
> `RuntimeError: libshiboken: Internal C++ object (SatTrackPanel) already deleted`。
> 本线用独立夹具 10×10 稳定复现（不是偶发观察项）。

## 1. 结论

| 项 | 内容 |
|---|---|
| 缺陷位置 | [desktop/sat_track_panel.py:79](file:///d:/mbdsdr/MBDSDR/desktop/sat_track_panel.py#L79) `QTimer.singleShot(0, self._apply_narrow_defaults)` |
| 崩溃行 | [desktop/sat_track_panel.py:218](file:///d:/mbdsdr/MBDSDR/desktop/sat_track_panel.py#L218) `if self.window().width() < 1440:` |
| 性质 | **产品缺陷**（QObject 的 C++ 对象被父级销毁后，0 ms 单发定时器仍持有 Python 绑定方法并回调） |
| 触发条件 | 面板**从未 show**（藏在非当前页签），且在事件循环第一次空闲前被销毁（关窗、切页、测试 teardown 都会） |
| 不在触发条件内 | 已 show 过的面板：`showEvent` 同步把 `_collapse_applied` 置 True，后续定时器回调提前 return，因此不崩 |
| 影响 | PySide6 打印 traceback 并继续；不会直接崩进程，但属真实的“访问已删除对象”，且会污染验收日志、掩盖真正的 UI 异常 |

## 2. 复现（完整夹具，可直接跑）

```python
# 保存为 repro_satpanel_timer.py，在仓库根执行：
#   $env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH='d:\mbdsdr\MBDSDR'
#   python repro_satpanel_timer.py
import os, sys, traceback
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"d:\mbdsdr\MBDSDR\desktop")

import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from sat_track_panel import SatTrackPanel

APP = QApplication.instance() or QApplication(sys.argv)
captured = []
sys.excepthook = lambda t, v, tb: captured.append(
    "".join(traceback.format_exception(t, v, tb)))


def run_case(label, make, n=10, show=True):
    captured.clear()
    for _ in range(n):
        host = QWidget()
        lay = QVBoxLayout(host)
        make(host, lay)
        if show:
            host.show()
        # 关键：刻意不 processEvents，让 0 ms 定时器仍挂在队列里
        shiboken6.delete(host)      # 父级立即销毁 → 子面板 C++ 对象被删
        APP.processEvents()         # 定时器开火 → 回调已删除对象
    print("%s 次数=%d 异常=%d" % (label, n, len(captured)))
    if captured:
        print(captured[0])
    return len(captured)


def case_panel(host, lay):
    lay.addWidget(SatTrackPanel())


def case_no_receiver(host, lay):
    class P(QWidget):
        def touch(self):
            _ = self.width()   # 与 self.window() 同类：访问已删 C++ 对象
    p = P(); lay.addWidget(p)
    QTimer.singleShot(0, p.touch)      # 旧写法：无 receiver


def case_with_receiver(host, lay):
    class P(QWidget):
        def touch(self):
            _ = self.width()
    p = P(); lay.addWidget(p)
    QTimer.singleShot(0, p, p.touch)   # 修复写法：绑定 receiver 生命周期


print("A_shown =", run_case("A_shown", case_panel, show=True))
print("A_hidden=", run_case("A_hidden", case_panel, show=False))
print("B_norec =", run_case("B_norec", case_no_receiver))
print("C_receiver=", run_case("C_receiver", case_with_receiver))
```

## 3. 实测（2026-11-06，HEAD `fc1f8076`，PySide6 6.12.0 / Windows / offscreen）

| 场景 | 条件 | 10 次中异常数 | 结果 |
|---|---|---|---|
| A_shown | 面板 `show()` 后父级销毁 | **0 / 10** | 不触发：`showEvent` 已置 `_collapse_applied=True` |
| A_hidden | 面板**未 show**，父级销毁再 `processEvents()` | **10 / 10** | **稳定复现** |
| B_norec | 最小 `QWidget` + `singleShot(0, bound_method)` | **10 / 10** | 机制确认：无 receiver 绑定 |
| C_receiver | 最小 `QWidget` + `singleShot(0, receiver, bound_method)` | **0 / 10** | 修复方向有效（PySide6 6.12 支持三参重载） |

A_hidden 首条完整 traceback：

```
Traceback (most recent call last):
  File "d:\mbdsdr\MBDSDR\desktop\sat_track_panel.py", line 218, in _apply_narrow_defaults
    if self.window().width() < 1440:
       ~~~~~~~~~~~^^
RuntimeError: libshiboken: Internal C++ object (SatTrackPanel) already deleted.
```

B_norec 首条：

```
Traceback (most recent call last):
  File "repro_satpanel_timer.py", line 43, in touch
    _ = self.width()
RuntimeError: libshiboken: Internal C++ object (Probe) already deleted.
```

## 4. 修复方案（交 UI 操作逻辑线执行）

**首选（一行）**：把 [sat_track_panel.py:79](file:///d:/mbdsdr/MBDSDR/desktop/sat_track_panel.py#L79) 改为

```python
QTimer.singleShot(0, self, self._apply_narrow_defaults)
```

`singleShot` 的三参重载把 receiver 绑定到 `self`：receiver 的 C++ 对象销毁时定时器自动取消，
不会回调到失效对象。已用场景 C 验证（10/10 无异常）。

**备选**：`self._narrow_timer = QTimer(self); self._narrow_timer.setSingleShot(True);
self._narrow_timer.timeout.connect(self._apply_narrow_defaults);
self._narrow_timer.start(0)` —— 同样把定时器挂在 `self` 下。

**不建议**只加 `if not shiboken6.isValid(self): return` 这类守卫：它能消音，但定时器仍然
在对象销毁后开火，属于掩盖而非修复；可作为二道防线，不作为主修。

## 5. 验收与反证

1. **夹具验收**：同一夹具跑 10 轮，`A_hidden` 必须 **0 异常**（当前 10/10）；
   `B_norec` 仍应 10/10（证明夹具本身还能咬住旧写法）。
2. **行为不回退**：新建面板 → 不 show → `processEvents()`，窄屏（<1440）下
   `_collapsible_boxes[1:]` 仍应 `setChecked(False)`；宽屏保持展开。
   必须补一条离屏用例守住“隐藏页签首次空闲判定”这个原始目的。
3. **反证**：把第 79 行改回两参写法 → 新用例必须红（`10/10` 异常复现）；
   修复后同一用例必须绿。
4. **回归**：`python -m pytest tests/test_reconnection.py tests/test_reconnection_e2e.py
   tests/test_reconnect_zero_freq.py -q` → 仍为 `18 passed`；桌面 UI 相关用例全绿。
5. 建议同时在 `tests/` 增加一条生命周期用例（创建→不 show→销毁父级→`processEvents`），
   把 `sys.excepthook` 捕获到的异常数断言为 0，防止同类定时器再犯。

## 6. 备注

- 本次终局跑首轮出现的 `SatTrackPanel already deleted` 与 M4 的 18 passed 不冲突：
  它发生在 teardown，未致测试失败；但按本单结论，它**不是**“偶发观察”，而是稳定缺陷。
- 本线未修改 `desktop/main_window.py` 或 `desktop/sat_track_panel.py`；
  计划落地由 UI 操作逻辑线执行。

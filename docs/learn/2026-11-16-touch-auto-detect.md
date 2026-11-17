# 触屏适配为什么成了"一个模式"——根因与自动检测修法（2026-11-16）

> 用户原话："为什么有一个触屏模式？我们的适配不应该是同时就能检测到是否是触屏吗？"
> 答案：本意就是自动检测，但旧实现用的 API 在 Qt6 里根本不存在，自动路径恒为
> `False`，用户只能去菜单里手选"强制触屏"——于是看起来像"必须选一个模式"。

## 1. 根因：`QTouchDevice` 在 Qt6 已移除

旧代码：

```python
from PySide6.QtGui import QTouchDevice   # Qt6 没有这个类
...
QTouchDevice.devices()                   # ImportError 分支 → 永远返回 []
```

实测：

```
PySide6 6.x → import QTouchDevice 失败（except 分支）
           → detect_touch_devices() 恒为 []
           → auto 模式恒为 False
```

所以自动检测是**死代码**，只有 `touch_mode="on"` 的强制覆盖能生效。

## 2. 三层检测（现在的实现，`desktop/touch_manager.py`）

1. **Qt 指针设备枚举**
   - Qt6：`QPointingDevice.devices()`，过滤 `DeviceType.TouchScreen`；
   - Qt5 兜底：`QTouchDevice.devices()`。
2. **Win32 数字化仪标志**
   - `GetSystemMetrics(SM_DIGITIZER=94) & 0xC0`（内置/外接触摸数字化仪）；
   - `GetSystemMetrics(SM_MAXIMUMTOUCHES=95) > 0`。
   - 仅在真实平台生效；`offscreen/minimal` 直接返回 `False`（否则测试结果会
     随开发机有没有触屏而漂移）。
3. **运行时补测**：`note_touch_event()`
   - 主窗口 `eventFilter` 收到 `QEvent.TouchBegin` 时调用；
   - 第一次真实触摸就把能力缓存置 `True`，并（在 auto 模式下）发
     `touch_mode_changed(True)`，全局热区立刻变成 ≥44px。
   - 手动"强制鼠标"是排障逃生口，运行时补测不会覆盖它。

判定优先级：`手动覆盖 > 硬件检测/运行时补测`。

## 3. 实测（本机）

```
SM_DIGITIZER = 0, SM_MAXIMUMTOUCHES = 0
QPointingDevice.devices() → 0 个
→ auto 正确判为"无触屏"，不假装支持
```

有触屏的机器上：`SM_DIGITIZER` 的 0x40/0x80 位会被置位，auto 直接开启；
即使某些 Windows 版本枚举不到，第一次触摸也会补上。

## 4. UI 语义调整

- 菜单从「触屏模式」改为「触屏适配」，首项「自动检测（推荐）」并带 tooltip；
- 强制项标注"排障用"，不再是日常路径；
- 设置页 `touch_mode` 仍保留 `auto/on/off`，但默认 `auto`。

## 5. 回归守卫（tests/test_touch_adaptation.py）

- `test_auto_detect_uses_os_capability`：伪造 Win32 标志 → auto 必须为 True
  （旧实现恒 False，此用例必红）；
- `test_runtime_touch_event_upgrades_auto_mode`：枚举为空 + `note_touch_event()`
  → 自动升级并只发一次信号；
- `test_manual_override_beats_runtime_detection`：强制鼠标不被运行时补测改写；
- fixture 现在同时还原 `_mode_override` 与 `_cached_touch_available`，
  避免单例缓存跨用例污染。

## 6. 注意

- 不要用"有没有鼠标"反推触屏：二合一设备两者都有；
- 不要在没有触控事件通道的平台（offscreen/无 GUI 服务）上开启触控热区；
- `WA_AcceptTouchEvents` 仍由触屏模式统一设置，鼠标模式行为不变。

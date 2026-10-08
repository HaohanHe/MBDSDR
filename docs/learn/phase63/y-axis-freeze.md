# Y 轴量程冻结落地（phase63）

## 语义

**关自动 = 定格当前量程**。当用户点击"自动"按钮关闭 dB 轴自动量程时，画布定格当前屏幕上正在显示的 ceiling/floor 值，而非跳回 spinbox 上次手动设置的旧值。后续无论输入更强还是更弱的帧，量程都保持锁定不变，直到用户再次调整 spinbox 或重新开启自动。

## 改动 file:line

### spectrum_display.cpp — 定格核心

`cpp/src/ui/spectrum_display.cpp:492-510` — `setAutoRangeOn(bool on)` 分支：

旧行为（跳回旧值）：
```cpp
if (!on) {
    dbFloorDb_ = manualFloorDb_;   // 跳回 spinbox 旧值
    dbCeilDb_  = manualCeilDb_;
    materialiseHistory();
}
```

新行为（定格当前量程）：
```cpp
if (!on) {
    // Freeze the CURRENT on-screen range as the new manual baseline.
    // Turning auto off = "lock the range you're looking at".
    manualCeilDb_  = dbCeilDb_;    // 当前 eased ceiling → 新 manual ceiling
    manualFloorDb_ = dbFloorDb_;   // floor 本来就等于 manualFloorDb_，对称复制
    // dbCeilDb_ / dbFloorDb_ 已经等于冻结值，无视觉跳变
    materialiseHistory();
}
```

设计要点：
- **ceiling 是关键**：自动模式下 ceiling 每帧缓动（kAutoEasePerFrameDb=1.5 dB），会偏离 manualCeilDb_；floor 始终钉死在 manualFloorDb_（见 setSpectrum auto-range 块 `dbFloorDb_ = manualFloorDb_;`）。
- **无视觉跳变**：冻结后 manualCeilDb_ = dbCeilDb_，所以 `dbCeilDb_ = manualCeilDb_` 是同值赋值，屏幕上量程不变。
- **历史重着色**：materialiseHistory() 用冻结后的量程重新映射瀑布图颜色。

### spectrum_widget.cpp — spinbox 回同步

`cpp/src/ui/spectrum_widget.cpp:295-314` — "自动"按钮 toggled 回调：

新增关自动时的 spinbox 同步：
```cpp
if (!on) {
    const int ceilVal = static_cast<int>(std::round(canvas_->currentDbCeil()));
    const int floorVal = static_cast<int>(std::round(canvas_->currentDbFloor()));
    dbMinSpin_->blockSignals(true);
    dbMaxSpin_->blockSignals(true);
    dbMinSpin_->setValue(floorVal);
    dbMaxSpin_->setValue(ceilVal);
    dbMinSpin_->blockSignals(false);
    dbMaxSpin_->blockSignals(false);
}
```

设计要点：
- **blockSignals**：避免编程式 setValue 触发 valueChanged → applyDb → setDbRange 的递归调用。
- **round 而非截断**：ceiling 经过 easing 后可能是非整数值（如 -23.5），round 到最近整数 spinbox 显示。
- **零新 UI 元素**：复用既有"自动"复选框按钮和既有 dB min/max spinbox。

## 测试

文件：`cpp/tests/test_spectrum_autorange.cpp`

| 测试用例 | 验证内容 |
|---|---|
| `manualToggleFreezesCurrentRange` | 自动模式灌强帧拉低 ceiling → 关自动 → ceiling/floor 等于当前显示值（不跳回 0 dB） |
| `freezeThenStrongFrameDoesNotMoveCeiling` | 弱信号定格低 ceiling → 关自动 → 灌强帧 → ceiling 不上升 |
| `freezeThenWeakFrameDoesNotMoveCeiling` | 强信号定格高 ceiling → 关自动 → 灌弱帧 → ceiling 不下降 |
| `freezeHonestEmptyState` | 无帧时关自动 → 保持默认值 → 灌帧后量程锁定不变（诚实空态） |

**测试计数**：
- test_spectrum_autorange：**12 passed, 0 failed**（4 新增冻结语义 + 8 既有回归）
- test_spectrum_display：**28 passed, 0 failed**（无回归）
- test_spectrum_interaction：**8 passed, 0 failed**（无回归）

## 快照核查

快照通道：`MBD_YFREEZE=1`（ui_screenshot_narrow.cpp 新增），先例 MBD_PEAKSHOT。

| 宽度 | 文件 | 核查结论 |
|---|---|---|
| 960px | `docs/learn/phase63/y-freeze-960.png` | 0 裁切，0 叠字；dB 轴定格在 -10/-100，spinbox 同步显示 -100/-10 |
| 1920px | `docs/learn/phase63/y-freeze-1920.png` | 0 裁切，0 叠字；工具条一行排开，量程清晰可读 |

## 判定记录：另两候选非缺口

侦察阶段评估了三个候选方案，最终选定"关自动=定格当前量程"：

1. **候选 A（已选）：关自动时冻结当前量程** — 语义最直观：用户看到什么就锁什么。SDR 类软件的通用交互惯例。改动最小（~15 行），零新 UI 元素。

2. **候选 B：关自动时跳回 spinbox 旧值（现状）** — 已实现但体验割裂：自动模式把 ceiling 缓动到了 -30 dB，用户关自动想"停在这里"，结果画面突然跳回 0 dB，trace 瞬间缩到画面下方一小块。这就是本次要修的缺口。

3. **候选 C：新增"锁定"按钮** — 额外 UI 元素，用户认知负担增加，且与既有"自动"按钮语义重叠。违反最小改动原则。

结论：候选 A 是唯一真正补齐"关自动=定格"语义的方案，候选 B 是缺陷本身，候选 C 过度设计。

## 改动文件清单

| 文件 | 改动类型 |
|---|---|
| `cpp/src/ui/spectrum_display.cpp` | setAutoRangeOn(false) 分支语义修改（+6 行注释/逻辑） |
| `cpp/src/ui/spectrum_widget.cpp` | 自动按钮 toggled 回调加 spinbox 同步（+13 行）；加 `<cmath>` include |
| `cpp/tests/test_spectrum_autorange.cpp` | 旧测试 manualToggleRestoresFixedRange → 重写为 manualToggleFreezesCurrentRange + 新增 3 个冻结测试 |
| `cpp/tests/ui_screenshot_narrow.cpp` | 新增 MBD_YFREEZE=1 快照通道（+40 行） |

## 红线自查

- [x] 无「比赛/competition」字样
- [x] 无 GPL 相关表述（项目 MIT）
- [x] 未跟踪隔离文件 `cpp/tests/ui_diag_freeze.cpp` 未动
- [x] 未执行 git add/commit/push
- [x] 无 ghp_ token 泄露

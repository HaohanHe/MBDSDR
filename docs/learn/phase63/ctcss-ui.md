# CTCSS 亚音静噪 UI 面（Phase 63）

落地文件：`cpp/src/ui/main_window.{h,cpp}`。本页只描述**解调参数 UI 面**：
开关 + 音调输入、与已冻结 DSP 核心（`SpectrumEngine`，见
[ctcss-dsp.md](./ctcss-dsp.md)）的接线、以及如实状态显示语义。DSP 检测机制、
tokens 阈值（`tokens.h:365-379`）不在此重复。

## 1. 控件位置与创建

控件全部复用既有的「接收参数 / 解调参数」表单 `gRxLay`（QFormLayout），
**零新增 groupbox**——在 `带宽` / `抽取` / `声道` / `强制单声道` 之后追加两行：

| 行标签 | 控件 | objectName | 位置 |
|---|---|---|---|
| `亚音` | `QCheckBox` 开关 | `ctcssCheck` | `main_window.cpp:652` |
| `音调` | `QHBoxLayout` = `QDoubleSpinBox` + 状态 `QLabel` | `ctcssFreqSpin` / `ctcssBadge` | `main_window.cpp:664` / `:682`（行 `:691`） |

- 频率输入用 `QDoubleSpinBox`，范围直接取 tokens 合法 PL 域
  `kCtcssToneHzMin=67.0` … `kCtcssToneHzMax=254.1`，步进 `0.1`，1 位小数，
  后缀 ` Hz`，默认 `kCtcssToneHzDefault=88.5`（`main_window.cpp:664-671`）。
  **非法输入不可达**：spinbox 把输入约束在合法域内，UI 层不需要、也不会
  自行 clamp 后"发明"一个值。
- 状态徽标 `ctcssBadge_` 初始 `--`，靠右放在 spinbox 同一行。

## 2. 接线（UI → 引擎）

- 开关 `ctcssCheck_->toggled` → `engine_->setCtcssEnabled(on)`，并 `scheduleSave()`
  与刷新徽标（`main_window.cpp:655-661`）。
- 音调 `ctcssFreqSpin_->valueChanged(double)` → `engine_->setCtcssFreqHz(hz)`，
  并 `scheduleSave()`（`main_window.cpp:672-678`）。
- 两个 setter 均为已实现的引擎接口（`spectrum_engine.h:162-163`），UI 只转发，
  不缓存、不重算。

## 3. 持久化往返

镜像 `rx/demodMode` / `rx/fftSize` 的既有做法，键定义在 tokens.h（与
`kSettingsKeyWfDepth` / `kSettingsKeyMaxHoldDecay` 同列）：

- `kSettingsKeyCtcssEnabled = "rx/ctcssEnabled"`（`tokens.h:863`）
- `kSettingsKeyCtcssToneHz  = "rx/ctcssToneHz"`（`tokens.h:864`）

写入在 `saveSettings()`（`main_window.cpp:4326-4327`）；恢复在
`restoreUiState()`（`main_window.cpp:4651-4672`）：恢复时 block 信号，把音调
`std::clamp` 进合法域后 `setValue`，再一次性 `setCtcssFreqHz` /
`setCtcssEnabled` 派发到引擎，避免每个 `set()` 重复触发 handler。

## 4. 如实状态语义（`ctcssBadge_`）

引擎只暴露 `ctcssPresent()` 真值读回（无信号），所以 UI 用一个 250 ms 的
`ctcssPollTimer_`（`main_window.cpp:2429-2433`）周期采样并绘制
`updateCtcssBadge()`（`main_window.cpp:5487`）：

| 条件 | badge 文本 | 颜色 | 说明 |
|---|---|---|---|
| 未开启（`!ctcssEnabled()`） | `--` | 次级灰 | **不显示任何假读数** |
| 已开启、无真音调 | `未检测到` | 次级灰 | 离线/无信号时的诚实空态 |
| 已开启、`ctcssPresent()==true` | `检测到` | 成功绿 | **仅当引擎真值命中** |

红线：badge 从不凭"已开启"就显示"检测到"。离线测试源无亚音时永远是
`未检测到`——这正是测试 `ctcssUiWiresEngineAndPersistsRoundTrip` 断言的
语义（`test_ui_integration.cpp`）。

## 5. 快照门控

`tests/ui_screenshot_narrow.cpp` 增加 `MBD_CTCSS=<hz>` 门控（`:130-146`）：在
MainWindow 恢复前把 `rx/ctcssEnabled=true` + `rx/ctcssToneHz=<hz>` 写入一次性
QSettings，走真实持久化往返（非事后 poke）。拍图：

```
MBD_CTCSS=88.5 MBD_SCROLL=ctcssCheck MBD_W=960  MBD_H=640  ./ui_shot_narrow
MBD_CTCSS=88.5 MBD_SCROLL=ctcssCheck MBD_W=1920 MBD_H=900  ./ui_shot_narrow
```

`MBD_SCROLL=ctcssCheck` 把左侧滚动轨滚到该控件（复用既有 ensureWidgetVisible
路径）。核查结论：960/1920 两档下 `亚音 ☑` 开关与 `音调 88.5 Hz` 输入均完整
可见、0 裁切、0 叠字；1920 宽轨下 badge 如实显示 `未检测到`。

## 6. 测试

扩展既有 `test_ui_integration`（构建完整 app、offscreen）：
`ctcssUiWiresEngineAndPersistsRoundTrip()`（`test_ui_integration.cpp:1230`）断言：

1. spinbox 最小/最大值 == tokens 合法域、步进 == 0.1（非法输入不可达）；
2. 默认关闭；勾选 → `engine->ctcssEnabled()` 真为真；
3. 改音调 → `engine->ctcssFreqHz()` 读回一致；
4. 已开启但离线无音调 → badge 文本 == `未检测到`（绝不假 `检测到`）；
5. 持久化往返：窗口1 落盘 `rx/ctcssEnabled` + `rx/ctcssToneHz`，窗口2 恢复
   勾选态 + 音调并派发进引擎。

DSP 检测器本身的真值（噪声不误报、错频拒收、关闭保持 false）由并行会话的
`test_squelch_gate`（`ctcssFalseOnNoiseOnly` / `ctcssRejectsWrongFrequency` /
`ctcssDisabledStaysFalse`）覆盖。

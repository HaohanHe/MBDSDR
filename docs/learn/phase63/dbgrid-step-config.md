# dB 参考格线密度可配置（dB grid step tier）

HEAD `57ee5f7`。本 phase 把频谱 trace 的水平 dB 参考格线（及 y 轴刻度）间距从一个写死的
具名常量（`kDbGridStep = 20 dB`）升级为用户可选的「10 / 20 / 40 dB」三档，并走 QSettings
持久化往返。全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`），无 GUI 操控；只接真实
数据源、诚实空态、零 mock。

## 1. 机制（改前）

水平 dB 格线 + y 轴刻度的绘制原本是：

```
for (db = ceil(dbFloor/20)*20; db <= dbCeil; db += 20) drawLine + drawText
```

- `kDbGridStep = 20` 是 `tokens.h:302` 的具名常量（不再是裸数字），但**没有任何 UI 入口、
  没有持久化键**——用户无法让 y 轴标尺更密（10 dB）或更疏（40 dB）。
- 改前全仓 grep `kDbGridStep` 仅命中两处：`tokens.h:302`（定义）与 `spectrum_display.cpp:904-905`
  （应用）。这是本仓库相对成熟 SDR UI 的真实缺口。

## 2. 三候选判定表

| # | 候选 | 判定 | 证据 |
|---|------|------|------|
| 1 | dB 参考格线密度可配置 | **真实缺口，已落地** | 改前 `kDbGridStep=20` 仅 `tokens.h:302` 定义、`spectrum_display.cpp:904-905` 应用，全仓 grep 无 UI 下拉 / 无 settings 键 |
| 2 | 音频输出设备选择复核 | **非缺口（已完备）** | 变更热更新：`main_window.cpp:2774-2797`（`cfg.audioDevice` 变更→`QMediaDevices::audioOutputs()` 枚举→按 `description()` 匹配→`audioOutput()->setDevice(d)`，`"default"` 走空 `QAudioDevice()` = 默认设备）；启动恢复：`main_window.cpp:3215-3227`（读 `cfg.audioDevice`，非 `"default"` 时枚举并 `setDevice`，headless 空列表则 no-op） |
| 3 | 解调后基带视图（post-demod view）评估 | **非缺口，仅评估不落地** | MBDSDR 无 post-demod 基带视图。`calibration_dialog.cpp:551` 的 `cfg.expectedBasebandHz` 是**频率校准**参考位置（非视图）；`main_window.cpp:766-769` 的 `recTargetCombo_`（"基带 IQ (SigMF)"）是**录制目标**（非视图）。现有 UI 部件 `src/ui/` 中无可视化解调后基带频谱/瀑布的 widget（见 §5 评估） |

结论：仅候选 1 是真缺口并落地；候选 2 给出证据、候选 3 给出评估结论，均不重复实现。

## 3. 落地 file:line

**tokens（具名 token，不写裸值）** — `cpp/src/core/tokens.h`
- 档位常量 `kDbGridStep10 = 10`（密）`:310`、`kDbGridStep20 = 20`（默认）`:311`、
  `kDbGridStep40 = 40`（疏）`:312`；
- 档位集合 `kDbGridStepChoices[]` `:313-314`；
- 缺省回退 `kDbGridStepDefault = kDbGridStep20` `:315`；
- 持久化键 `kSettingsKeyDbGridStep = "view/dbGridStep"` `:316`。

**canvas（读取配置步长，替换常量）** — `cpp/src/ui/spectrum_display.{h,cpp}`
- 匿名命名空间校验 `legalDbGridStep(int)` `spectrum_display.cpp:123-128`：只认三档，
  其余回默认（不做连续区间 clamp——y 轴标尺间距没有可连续夹取的物理意义）；
- 自读 `loadRequestedDbGridStep()` `spectrum_display.cpp:131-139`：缺键 / 非数值 → 默认 20；
- ctor 首帧生效 `dbGridStepDb_ = loadRequestedDbGridStep();` `spectrum_display.cpp:158`；
- 应用处读成员而非常量 `const float gridStep = (float)dbGridStepDb_; …`
  `spectrum_display.cpp:939-941`（原 `tokens::kDbGridStep` 两处替换为 `gridStep`）；
- 新 setter `setDbGridStepDb(int)` `spectrum_display.cpp:663-670`（校验后即 `update()`，
  下一帧重画格线）；公开接口 `spectrum_display.h:117-118`；测试缝 `dbGridStepForTest()`
  `spectrum_display.h:118`；成员 `dbGridStepDb_ = 20` `spectrum_display.h:455`
  （类内字面量仅为 ctor 前安全值，避免把 tokens.h 拉进头文件，镜像 `maxHoldDecayDb_`）。

**widget（零新按钮，并入既有 dB 轴行）** — `cpp/src/ui/spectrum_widget.cpp`
- 在 `自动` dB 量程按钮之后插入「格线」标签 + `QComboBox` `spectrum_widget.cpp:356-391`；
  `objectName = "dbGridStepCombo"` `:366`；
- 三项 `10 dB / 20 dB / 40 dB`，`itemData` 承载真实步长（int）`:368-369`；
- 镜像 `decayCombo` 先例：`setCurrentIndex` 在 `connect` 之前（`:373-382`），构造期不会
  对尚不存在的 `canvas_` 发信号；
- 变更时 `QSettings` 写键 + `canvas_->setDbGridStepDb()` + `emit viewChanged()` `:383-389`；
- canvas ctor 自读持久值，故首帧即生效，combo 只是实时镜像 + 变更入口（与 wfDepth /
  maxHoldDecay 同构）。

**快照门控** — `cpp/tests/ui_screenshot_narrow.cpp`
- 新增 `MBD_DBGRID=10/20/40` 门控 `:86-97`：在 MainWindow 恢复前把步长写入一次性
  QSettings（throwaway 路径），真实持久化往返，不污染用户数据；先例同 `MBD_MHDECAY`。

## 4. 诚实语义

- 档位标签用「10 dB / 20 dB / 40 dB」，数值即真实格线间距（相邻水平格线与 y 轴刻度间隔），
  不撒谎；tooltip 说明语义。
- 非法 / 缺失持久值 → 回退默认 20，不静默夹取、不发明档位。
- 全程真实离线测试信号（`isTestSignal`）驱动，空态时 combo 仍在、空态标签不变。

## 5. 解调后基带视图（post-demod view）评估记录（候选 3，不落地）

**现状**：MBDSDR 没有「解调后基带视图」。对照 SDR++ 的 post-demod view（对选中 VFO 解调后
的基带 IQ / MPX 再画一张小频谱或瀑布）：
- `expectedBasebandHz`（`frequency_calibrator.h:92`，`calibration_dialog.cpp:551`）服务于
  **频率校准**——把测得的基带峰位与预期参考位置比对出 ppm 修正，是一次性校准向导，不是常驻视图；
- `recTargetCombo_ = "基带 IQ (SigMF)"`（`main_window.cpp:766-769`，`spectrum_engine.h:612`）
  是**录制目标选择**——把真实基带 IQ 落盘成 SigMF，不是屏幕视图；
- 现有 `src/ui/` 部件：`constellation_view`（IQ 星座图）、`spectrum_display`（宽频 RF 频谱+
  瀑布）、`rssi_trend`（窄带 RSSI 折线）、`s_meter`——无一展示解调后基带的频谱/瀑布。

**成本 vs 收益**：
- 成本：需要一条新数据路径——`vfo_manager` 在 `ch.demod->process(baseband)`
  （`vfo_manager.cpp:502-503`）之后额外 tap 一路解调后基带块，按块做小型 PSD，再推给一个
  新 widget；涉及 engine→UI 新信号、新 widget、新 tab/面板、新空态、新持久化（带宽/位置）。
  属于「新数据路径 + 新视图」，非简单加控件。
- 收益：对 NFM/WFM 监听者，能直接看到解调后基带（如 RDS 57 kHz 副载波、Morse/FSK 频谱形状）
  有调试价值；但 MBDSDR 已有：宽频 RF 频谱（看信道）、星座图（看数字解调）、RSSI 趋势、
  以及 RDS/A PTY 等解码器的文本输出。post-demod 视图对现有解码链路是「锦上添花的调试窗」，
  而非缺口——用户判断信道选 RF 频谱，判断解码对错看解码器输出。

**结论**：收益中等、成本偏高（新数据路径 + 新视图），本轮不落地。若后续要做，建议方案：
在 `vfo_manager` 的模拟解调分支后 tap 一路 48 kHz 基带，做 256~512 点 PSD，新增一个小面板
（复用 `spectrum_display` 的绘制套路但喂基带帧），带宽/位置持久化走 tokens.h 具名键；空态
诚实（无解调时不画）。留作后续轮候选。

## 6. 测试（offscreen 真实计数）

扩展既有 `cpp/tests/test_spectrum_display.cpp`（直接驱动 `ui::SpectrumDisplay` 测试缝，
不经过 paint；未新增 CMake 目标）：
- `dbGridStepDefaultsTo20()`：无持久键 → ctor 自读默认 20；
- `dbGridStepSetterHonoursTier()`：`setDbGridStepDb(10/20/40)` 逐档断言；并按固定 [-100,0]
  范围复算格线数 `11 > 6 > 3`，证明密档格线数严格多于疏档；
- `dbGridStepPersistsRoundTrip()`：写 10 / 40 入 QSettings，新构造 canvas 自读档位
  （写后移除键，不残留用户配置）；
- `dbGridStepInvalidFallsBack()`：写 `99`（非档位）与非数值 `"bogus-grid"`，断言回退默认 20；
- `initTestCase`/`cleanupTestCase` 把 `kSettingsKeyDbGridStep` 纳入既有 saved_ 键备份/恢复清单，
  不污染用户配置。

运行结果（`QT_QPA_PLATFORM=offscreen`）：

```
Totals: 36 passed, 0 failed, 0 skipped, 0 blacklisted   (改前 32 passed → 现 36 passed)
```

回归（`test_spectrum_maxhold`，确认 tokens.h 改动无副作用）：

```
Totals: 8 passed, 0 failed, 0 skipped, 0 blacklisted
```

## 7. 快照核查结论

`MBD_DBGRID=10` / `MBD_DBGRID=40`，`MBD_W=960/1920 MBD_H=720`，offscreen grab：
- **1920**：工具行「… 自动 | 格线 10 dB | 门限 15 dB」下拉清晰可见，选中项为「10 dB」
  （证明持久化往返：环境注入 10 → combo 落「10 dB」），无裁切、无叠字；
- **960**：FlowLayout 优雅换行，「格线 10 dB」位于「自动」之后，无裁切、无叠字、文字可读；
- **40 dB 对照**：combo 落「40 dB」，trace 格线由 10 dB 档的 11 条（0,-10,…,-100）变为
  3 条（0,-40,-80），密度真实对应步长。

两档宽度均 0 裁切 0 叠字，combo 在 dB 轴行内，未新增按钮。

## 8. 未完成 / 诚实声明

- 未做「连续 dB 间距滑杆」——刻意只给三档具名档位，避免把 y 轴标尺间距暴露成无物理意义的
  连续区间；与 waterfall depth 只认 `{128,256,512}`、maxHold 只认 `{0.5,1.5,3.0}` 的取舍一致。
- 候选 2（音频输出设备选择）经核实已完备（热更新 + 启动恢复），未重复实现。
- 候选 3（post-demod 基带视图）经评估为收益中等、成本偏高，本轮不落地，方案留作后续轮。

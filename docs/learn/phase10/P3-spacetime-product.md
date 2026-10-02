# P3 时空产品化：从"演示"到"可用"的接线补齐

> 目标：把桌面"时空视图"tab 与移动端天空页的四格状态，从演示占位接到**真实
> engine/GNSS/radio 链路**上；无硬件时全部落到诚实空态，绝不伪造 RSSI / 多普勒 /
> GNSS 授时数字。
>
> 范围红线：只动 `cpp/src/ui/`、`cpp/tests/`、`mobile/lib/`、`mobile/test/`。
> ctest 基线 94、flutter 基线 298 不破，新增全绿。

## 1. 现状复查结论（先读后改）

读码后确认三格已有/缺失情况：

| 格 | 改前状态 | 处理 |
|---|---|---|
| 信号 RSSI/SNR | `refreshSpacetimeView` 直接喂 `lastRssi_`/`lastSnr_`，而这俩默认 `-200`/`0`（非 NaN，供扫描门限/S-meter 用）——**无硬件会渲染成假读数**。 | 新增时空专用 NaN 缓存，遥测到才写。 |
| 多普勒补偿 | 1Hz 循环已从 range-rate 算出真实 `liveFd` 并回写 VFO，但**没缓存**，时空行传 NaN，只显示"补偿中"无数值。 | 缓存真实 `liveFd`，门控显示。 |
| 时间源 | 已真实接线：串口 up 且 NMEA 带 UTC 才认 `gnss`，否则 `system`（诚实告诫），与 `updateClockBiasLabel` 同口径。 | 复查通过，无需改逻辑。 |

## 2. 桌面接线补齐（file:line）

**新增成员缓存（`cpp/src/ui/main_window.h:267-271`）**
```cpp
float  lastSpRssi_ = NaN;   // NaN = 还没收到真实 RSSI 遥测
float  lastSpSnr_  = NaN;
double lastSpDopplerHz_ = NaN;  // range-rate 推出的真实补偿值
```
不复用 `lastRssi_`/`lastSnr_`：那俩默认值供别处用，非 NaN，直接接会在无硬件时出假数。

**信号格接真实遥测**
- `onRssiLevel`：`main_window.cpp:3767` 写 `lastSpRssi_ = dbfs` 并 `refreshSpacetimeView()`。
- `onSnrLevel`：`main_window.cpp:3780` 写 `lastSpSnr_ = snrDb` 并刷新。
- 渲染：`main_window.cpp:4794` `spTileSignal(lastSpRssi_, lastSpSnr_)`。
- 行为：没收到遥测 → 双 NaN → 诚实 `--`（Neutral）；收到 → `−45.2 dBFS · SNR 12.3 dB`（Info）。

**多普勒补偿真实接入**
- 真实值来源：1Hz 循环 `updateLiveSatellite` 里 `liveFd = dsp::dopplerHz(f0, rangeRate)`。
- 写入：`main_window.cpp:4659` `lastSpDopplerHz_ = liveFd`（仅在捕获过境 + 补偿开启 + 有下行频率时）。
- 复位（诚实空态）：新捕获 `:4473`、补偿开关 on/off `:4513/:4517` 一律置 NaN。
- 门控渲染：`main_window.cpp:4838` 仅当 `compOn && hasTarget` 才把缓存值传进格式化函数，否则 NaN。
- 格式化：`cpp/src/ui/spacetime_format.h` 把第三参语义从"残差"更正为"补偿值"（我们没有环路误差测量，只有 range-rate 推出的开环补偿值），非有限则省略数字——不编。

**时间源（复查通过）**：`main_window.cpp:4819-4827`，`gnssUp && hasUtc && utc.isValid()` 才 `gnss`，否则 `system（本机时钟，非 GNSS 授时）` Warn，绝不把 system 升级成 gnss。

## 3. Flutter 端时空状态卡片

**新增 `mobile/lib/pages/spacetime_status.dart`**
- 纯 Dart 视图模型 `SpacetimeStatus.fromServices({fix, radioConnected, freqHz, targetName, dopplerHz})`：与桌面同款"缺路即空态"。
  - 时间源：`fix.source=='real' && utcTime` → GNSS（Ok）；否则 `system（本机时钟…）`（Warn）。
  - GNSS：`fix.hasFix` → 坐标+星数；否则 `无 fix`。
  - 接收目标：真实选中目标 > 连接中频率 > `无接收目标`。
  - 多普勒：**仅当有真实 `dopplerHz` 且有目标**才显示 `补偿值 X Hz`；否则 `未补偿（无目标）`。
- 薄 widget `SpacetimeStatusCard`：SpRole → tokens 色板，2×2 网格。

**嵌入天空页 `mobile/lib/pages/sky_page.dart`**：侧栏刷新条下方插入卡片，接 `widget.radio` 的真实连接态/频率与 `_c.selectedName`。GNSS fix 流与实时多普勒在移动端尚未接线 → 诚实空态。

## 4. 真实 / 空态两分支行为

| 场景 | 信号格 | 时间源 | 多普勒行 | GNSS 格 |
|---|---|---|---|---|
| 无硬件（云内默认） | `--` | system（Warn 告诫） | `未补偿（无目标）` | `无 fix` |
| 已连 SDR 收到遥测 | 真实 dBFS·SNR | system（本机钟） | `未补偿（无目标）` | `无 fix` |
| + GNSS 串口出 UTC | 真实读数 | **gnss**（Ok） | 同上 | 有 fix→坐标 |
| + 捕获过境并开补偿 | 真实读数 | gnss | `补偿中 · 补偿值 −12.5 Hz`（真实 range-rate） | 坐标 |

## 5. 测试结果

- C++：`test_spacetime_format` 10/10（新增"有补偿值/无补偿值空态"用例）；全量 ctest 94/94 绿。
- Flutter：新增 `test/spacetime_status_test.dart` 6/6（空态/真实两分支 + widget）；全量 flutter 套件 298 + 6 = 304 绿；`flutter analyze` 0 issue。

## 6. 真机待验项（云内无硬件，未验）

1. 接真实 RTL-SDR 后，信号格是否随播频/信号出现 `dBFS·SNR`（而非恒 `--`）。
2. 接真实 GNSS 串口：时间源是否从 system 翻成 gnss、GNSS 格是否出坐标。
3. 捕获一个真实过境并开"多普勒自动补偿"：多普勒行是否出现真实 `补偿值 ±N Hz`，且随仰角变化。
4. 移动端：rtl_tcp 连接后"接收目标"是否显示当前频率；GNSS 串口流与移动端实时 Doppler 尚未接线（本端诚实空态），后续接 `GnssFixMerger` 流后补。
5. 桌面断连/关补偿后：各格是否正确回落空态（不留旧读数残留）。

# Phase49 块3 — 移动侧实时多普勒补偿（Flutter 对称闭环）

## 判定：(a) 对称做（成本小、风险可控）

结论：在移动侧补齐了**与桌面 DopplerStepLimiter 语义对称**的实时多普勒自动
闭环，但保持 **opt-in（开关默认 off）**——未开启时移动端仍只显值，与
Phase39 的诚实边界一致。

### 为什么不是 (b) 只显值

闭环所需原语在移动侧**全部已存在并被验证**，接线成本小：

- `rangeRateAt` / `dopplerShiftFromRangeRateHz`（lib/astro/coordinates.dart:136）
  与 `capturePass`（lib/services/satellite_capture.dart:99）**同一符号约定**：
  目标 VFO = 标称下行 + 实时多普勒。
- `satelliteDownlink(catalogNumber)` 提供真实标称载频；目录外诚实 null。
- `RadioApi.setFrequencyHz(int)` 实测可改频（G3 / scan 已大量使用）。
- 桌面本轮的 `DopplerStepLimiter`（cpp/src/core/sat_capture.h:90）是纯逻辑，
  可一比一翻成 Dart。

rtl_tcp 连续闭环的风险评估：1 Hz `set_freq` 与既有范围扫描循环同量级
（scan 已高频下发 setFrequencyHz），TCP 命令无新压力；步进限幅 ≤2000 Hz/拍
（对齐桌面 `kDopplerMaxStepHz=2000`）防止抖动调谐器；开关默认 off，对现有
366 个测试零行为回归。

## 实现（只动 mobile/lib + mobile/test + docs）

- `lib/app/tokens.dart`：新增具名 token `dopplerAutoMaxStepHz=2000.0`、
  `dopplerAutoTickPeriod=1s`（对齐桌面 kDopplerMaxStepHz，禁裸数）。
- `lib/services/doppler_step_limiter.dart`（新）：纯 Dart 限幅器，镜像桌面
  reset/advance/disarm 语义，无 IO。
- `lib/pages/sky_page.dart`：
  - `SkyController` 加 `dopplerAuto` / `setDopplerAuto(bool)` / `_dopplerTick()` /
    `dopplerCompensating` / `dopplerAppliedHz`。开关默认 off；开启即从当前
    VFO `reset` 并立即拍一拍，之后 1 Hz 周期。
  - `_dopplerTick()` 多重门控：未开 / 未连接 / 无选中目标 / 无本站 / 无下行目录 /
    SGP4 失效 / 时间预览中 → 诚实空转，绝不改频、绝不编数。
  - UI：`_DopplerAutoControl` 开关行（minHeight = AppTokens.touchMin = 44），
    未连接/无目标/目录外/预览态时禁用并如实说明。
- `lib/pages/spacetime_status.dart`：多普勒格升级三态——
  「补偿中·累计 xxx Hz」（开闭环）＞「补偿值 x Hz」（仅显值）＞「未补偿（无目标）」。

## 测试（确定性，+11）

- `test/doppler_step_limiter_test.dart`：限幅/一步可达/大步有界收敛/disarm 重绑。
- `test/sky_doppler_auto_test.dart`：开关默认 off；开启但无目标绝不改频；关闭停表；
  radio=null 不崩溃不改频。
- `test/spacetime_status_test.dart`：开闭环升级「补偿中·累计」、关闭回退仅显值、
  开闭环但无目标仍诚实空态。

## 验证

- `flutter analyze`：No issues found。
- `flutter test`：全量 **377 passed**（基线 366 + 新增 11），无回归。

## 诚实边界（务必保留）

- 开关默认 off；未开启时多普勒仍只是只读读数，绝不自动改频。
- 目录外卫星（无标称下行载频）开关禁用、闭环空转。
- 时间预览滑条拖动期间不闭环改频（不改一个未来时刻的频率）。
- 关闭开关**不把频率拉回**，保留当前 VFO（诚实）。
- 不预置 TLE / 不预置活动数据 / 无假数据；未接射频时降级为只读。

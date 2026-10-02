<!--
SPDX-License-Identifier: MIT
-->

# Q4 · 桌面四项收尾复查（第六阶段 P3 交付对照）

> 范围：对照第六阶段 P3「对标缺口」交付（`docs/learn/phase6/_PHASE6_SPEC.md` §P3）
> 与第四阶段 B4 审计（`docs/learn/phase4/audits/B4-gap-learning.md`），逐项核对桌面端
> 四项：**热插拔 UI 提示 / 声卡链接健康 / 分段增益 / VFO 出声**。
>
> 方法：真读 `cpp/src/dsp/*`、`cpp/src/ui/main_window.cpp*`、`cpp/tests/*`，并以
> `QT_QPA_PLATFORM=offscreen ctest` 在 `cpp/build` 实跑（**91/91 全绿**）。
> 环境事实：云 VM 无硬件、无 librtlsdr 物理设备；凡物理拔插 / 真实声卡出声 /
> 真实档表读回一律标「真机待验」，不把云内 stub 验证写成真机已验。

---

## 0. 复查总表

| 项 | B4 当时结论 | 现状实现（file:line） | 云内测试（ctest #） | 诚实空态 | 判定 |
|---|---|---|---|---|---|
| ① 热插拔 UI 提示 | 真缺（无插入/拔出检测） | `device_lister.{h,cpp}` + `device_presence_notifier.{h,cpp}` + main_window.cpp:1758-1768 | #48 device_lister（6）、#49 presence_notifier（3） | 无设备→空 baseline 不发误报 | ✅ 已落地，物理 auto-open 真机待验 |
| ② 声卡链接健康 | 半实现（无 underrun/断开检测） | `audio_link_health.h` + `qt_audio_sink.cpp:96-219` + main_window.cpp:3674 | #50 audio_link_health（4） | 无头无设备→available_=false 不假出声 | ✅ 已落地，真拔插/重连真机待验 |
| ③ 分段增益 | 半实现（未对齐离散档表） | `tuner_gain_table.{h,cpp}` + `gain_control_model.h` + `rtl_sdr_source.cpp:164-201` + main_window.cpp:3746-3782 | #46 tuner_gain_table（8）、#51 gain_control_model（3） | 无表→连续滑条；无硬件→禁用带原因 | ✅ 已落地，真档表/每级增益真机待验 |
| ④ VFO 出声 | 已完整（仅选中 VFO 出声，写定取舍） | `vfo_manager.{h,cpp}` + main_window.cpp:2989-2990 `[出声]` 标签 | #52 vfo_audible（1） | 未选中 VFO 只扇出解调，不同时出声 | ✅ 已落地；多 VFO 同时听未做 |

---

## 1. 热插拔 UI 提示（device_presence_notifier）

**实现存在性**：真链路，非空壳。
- `cpp/src/dsp/device_lister.{h,cpp}`：拉取式枚举 seam（`IRtlDeviceEnumerator`），
  `HAVE_RTLSDR` 下 `RtlSdrDeviceEnumerator` 包 `rtlsdr_get_device_count()/get_device_name()`
  （机制学自 librtlsdr GPLv2 拉取模型，干净室不照抄）。`poll()` 首跑只建 baseline、
  之后 diff `devicesAdded/devicesRemoved`（device_lister.h:52-84）。
- `cpp/src/dsp/device_presence_notifier.{h,cpp}`：把 diff 事件映射成**逐字文案**
  `deviceAddedNotice`="RTL-SDR 已连接：…" / `deviceRemovedNotice`="RTL-SDR 已移除：…"，
  经 `presenceNotice(text, Kind)` 直供状态栏（device_presence_notifier.cpp:20-44）。
- 接线 `cpp/src/ui/main_window.cpp:1758-1768`：构造 `DeviceLister`+`DevicePresenceNotifier`，
  接 1 Hz UI 定时器 `pollOnce()`，并立即 `poll()` 建 baseline。

**测试覆盖**：ctest #48 `device_lister`（firstPollIsBaseline / deviceAdded /
deviceRemoved / unchangedEmitsOnlyListChanged / renameIsRemovePlusAdd / emptyToEmpty）、
#49 `device_presence_notifier`（pureMappingAddRemove / plugThenUnplugNoticeText /
baselinePollNoNotice）。均绿。用脚本化假 enumerator 驱动，无 QWidget/无 librtlsdr。

**诚实空态**：enumerator 为空（无设备 / 无 HAVE_RTLSDR）时 baseline 即空，
空→空不发误报；本类**从不 open 设备**。

**真机待验项**：
- 真实 USB 拔插能否被 `RtlSdrDeviceEnumerator` 枚举出差异；
- 拔出后**自动 `rtlsdr_open` 重连**（B4 §④ 明确 auto-open 是真机项）——云内只验
  "列表 diff 状态机 + 文案"，不验物理恢复。
- 备注：断流关闭走另一条独立通道 `RtlSdrSource::readIQ` 失败 → readWatchdog 闩死 →
  `rtlsdr_close` + `isConnected()=false`（rtl_sdr_source.cpp:224-240），经引擎
  `sourceDropped` 信号；与 `DeviceLister` 的枚举 diff 是**两条独立通道**，真机上二者是否
  总是一致尚未真机观察（云内不臆断，列待验）。

---

## 2. 声卡链接健康（audio_link_health）

**实现存在性**：纯状态机 + 真实喂入。
- `cpp/src/dsp/audio_link_health.h`：状态机 `Healthy/Underrun/Dead/GivenUp`；
  `onWrite(ok)` / `onUnderrun()` / `onDeviceError()` / `tickReconnect()`（节流 + 上限，
  退避耗尽诚实转 GivenUp）/ `reset()`。header-only 以避免给 ~20 个截图目标加 .cpp。
- 喂入 `cpp/src/dsp/qt_audio_sink.cpp`：`feedAudioWrite` → `onWrite`（:210）、
  buffer 抽空 → `onUnderrun`（:217）、QAudioSink fatal/IO → `onDeviceError`（:154, :219）；
  `ensureReady` 在 Dead 态 `tickReconnect()` 触发有界重建（:96-99）。
- 接线 `cpp/src/ui/main_window.cpp:3674-3675`：`sbAudio_` = `audioOutput()->audioHealthStatus()`
  （声卡正常 / 欠载 / 断开重连中 / 不可用）。

**测试覆盖**：ctest #50 `audio_link_health`（healthyUntilUnderrunThreshold /
fatalErrorLatchesDead / reconnectIsThrottledAndCapped / resetAfterSuccessfulRebuild）。均绿。

**诚实空态**：无头 CI 无设备时 `QtAudioSink::available_` 保持 false、输出丢弃，
health 不伪造"正常出声"。

**真机待验项**：真实声卡拔插自动重连、真实延迟测量（B4 §④）。

**遗留（非本批范围，诚实记录）**：B4 专题1④建议把线性重采样换成项目已有的
多相 FIR `audio_resampler` 以消除高频镜像——**仍未接入** `qt_audio_sink.cpp:169`
`resampleToDevice`，保持朴素线性插值。这是 B4 P2 项，本批未做，不宣称已完成。

---

## 3. 分段增益（gain_control_model / tuner_gain_table）

**实现存在性**：生产路径真读档表，非仅单测。
- `cpp/src/dsp/tuner_gain_table.{h,cpp}`：注入离散档表（0.1 dB），`snap` 就近吸附、
  越界 clamp、半档向上；空表透传；`setActualGainDb` 读回驱动真值。
- 生产填充 `cpp/src/dsp/rtl_sdr_source.cpp:164-201`：`start()` 后两次
  `rtlsdr_get_tuner_gains(dev,…)` 取档数再填表（`gainTable_.setTable`），
  `setGain` 先 `snap` 再下发（:28-30），下发后 `rtlsdr_get_tuner_gain` 读回真值（:184）。
- UI 决策 `cpp/src/ui/gain_control_model.h`：`decide(table, hw)` →
  `DiscreteCombo`（有表）/ `ContinuousSlider`（无表）+ `enabled` + 诚实 reason。
- 接线 `cpp/src/ui/main_window.cpp:3746-3782` `rebuildGainUi`：有表→显 `gainCombo_`
  隐 `gainSlider_`、按 `lastGainDb_` 就近选中；无表→连续滑条；无硬件→禁用并 tooltip 原因。

**测试覆盖**：ctest #46 `tuner_gain_table`（emptyIsPassthrough / snapsToNearest /
belowMinClamps / aboveMaxClamps / exactStepUnchanged / halfStepTieGoesUp /
availableGainsShape / actualGainReadback，8 例）、#51 `gain_control_model`
（tablePresentWithHardware / emptyTableStaysSlider / noHardwareDisables，3 例）。均绿。

**诚实空态**：无档表（rtl_tcp / 离线测试源读不到 librtlsdr 档数组）→ 保留连续滑条，
**绝不臆造 combo**；无硬件 → 禁用 + reason「无设备：增益档不可用」。

**真机待验项**：真实档表来自硬件（`rtlsdr_get_tuner_gains`），云内用注入假表测吸附；
真·LNA/mixer/VGA 每级独立增益 = 真机 + SoapySDR，B4 标注不做。

---

## 4. VFO 出声（selected VFO = 唯一出声）

**实现存在性**：B4 专题2 判定"已有且相当完整"，本批补的是**出声标签明确化**。
- `cpp/src/dsp/vfo_manager.{h,cpp}`：每 VFO 独立 `Channelizer`+`IDemod`+`AudioResampler`；
  头文件写定取舍——squelch/AGC/声卡/录音只作用**选中 VFO**，其余只扇出解调+标记。
- UI 标签 `cpp/src/ui/main_window.cpp:2989-2990`：选中行追加 `  [出声]`，
  状态栏 `sbVfo_` 显示选中 VFO 名 + 频率。

**测试覆盖**：ctest #52 `vfo_audible`（`audibleFollowsSelection`）。绿。

**诚实空态**：未选中 VFO **不**同时出声——这是写定取舍（非 bug），UI 用 `[出声]`
标签把"谁在响"显式化。

**真机待验项**：多 VFO **同时监听**（B4 P3 monitor mix 总线）未做，标注为后续可选项。

---

## 5. 复查结论

1. 四项均**实现存在、均有云内确定性测试（ctest 91/91 绿）、均有诚实空态**，
   与第六阶段 P3 交付声明一致，无"空壳冒充完成"。
2. 真机待验项（云内不可验，诚实列出，不写成已验）：
   - ① 真实 USB 拔插枚举差异 + 拔出后自动 open 重连；
   - ② 真实声卡拔插重连、真实延迟测量；线性重采样换多相 FIR 仍未做；
   - ③ 真实档表读回、真·每级增益（SoapySDR）；
   - ④ 多 VFO 同时监听 mix 总线。
3. 下一步建议（按价值 ÷ 云内可验性）：
   - 高：真机上跑一次"拔插 → 枚举 diff / 断流关闭"双通道一致性核对，把 ① 的两条通道
     在真机上的实际行为回填本文；
   - 中：把 `qt_audio_sink` 线性重采样切到 `audio_resampler` 多相 FIR（正弦阻带衰减可云内测）；
   - 低：多 VFO monitor mix（MemoryAudioSink 可云内断言双路，但属体验增强）。

> 验收：`QT_QPA_PLATFORM=offscreen ctest`（cpp/build）= **91/91 通过**；
> 未改 C++ 源码（本批只复查 + 文档），无基线破坏。

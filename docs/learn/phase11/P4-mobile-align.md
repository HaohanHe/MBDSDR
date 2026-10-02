# P4 移动端对齐清单（Flutter ↔ 桌面第十阶段时空语义）

> 范围：桌面第十阶段「时空产品化」语义逐项对照 Flutter 现状，给判定（该移植 / 架构性不移植 / 真机待验）。
> 基线（本批跑前实测）：flutter **304 passed**、`flutter analyze` **No issues**。
> 云内无硬件——凡涉真机行为一律标注「真机待验」，不描述为已验证。
> 可写范围仅限 `mobile/lib`、`mobile/test`、本目录；`mobile/android`、`mobile/ios` 本批只读不改。

对照锚点：
- 桌面纯函数：`cpp/src/ui/spacetime_format.h`
- 桌面接线：`cpp/src/ui/main_window.cpp:4788` `refreshSpacetimeView()`
- Flutter 纯模型：`mobile/lib/pages/spacetime_status.dart`
- Flutter 接线：`mobile/lib/pages/sky_page.dart:419`（`SpacetimeStatusCard`）

---

## 0. 结论速览

| # | 对齐项 | 判定 | 本批动作 |
|---|---|---|---|
| 1 | 时间源显示口径 | ✅ 核心语义已对齐；展示层差异 | 不移植（理由见 §1） |
| 2 | 多普勒门控 | ⚠️ 主门控已对齐；「armed 无目标」Warn 为桌面专有 | 架构性不移植（§2） |
| 3 | 空态文案 | ✅ 已对齐（GNSS 无 fix / 无目标 / 未补偿 逐条一致）；HDOP 补齐 | **已移植 HDOP**（§3） |
| 4 | 增益回退 25.4dB | 架构性差异（rtl_tcp 客户端 + AGC 默认开） | 不移植，诚实记录（§4） |
| 5 | 快捷键 | 架构性（触屏无物理键盘） | 不移植（§5） |
| 6 | AndroidManifest 权限/版本 | ⚠️ USB host 已声明；**缺定位 uses-permission（真实缺口，超可写范围）** | 记录 + 建议（§6.1） |
| 7 | Info.plist 权限/版本 | ✅ 定位权限已声明；无麦克风权限（有意） | 不改（§6.2） |

---

## 1. 时间源显示口径

**桌面语义**
- `spacetime_format.h:116-122` `spLineTimeSource(source, utcIso)`：
  - `source=="gnss"` → `时间源 GNSS · {utc} UTC`（Ok）
  - 否则 → `时间源 system（本机时钟，非 GNSS 授时）· {utc}`（Warn）
  - 红线（`:14-15`、`:113-115`）：绝不把 system 升级成 gnss；system 必带「本机时钟，非 GNSS 授时」告诫。
- 接线 `main_window.cpp:4821-4827`：`gnssClock = gnssRx_ && connected() && hasUtc && utc.isValid()`；`utcIso` 在 gnssClock 时取 GNSS UTC，否则取 `currentDateTimeUtc()`（即 system 态也把本机钟读数附在行尾）。

**Flutter 现状**
- `spacetime_status.dart:57-65`：`hasUtc = fix.source=='real' && utcTime 非空` → `GNSS {utcTime}`（Ok）；否则 `system（本机时钟，非 GNSS 授时）`（Warn）。
- `fix.source=='real'` 由 `GnssFixMerger` 在 10s 无新 NMEA 后退化为 `'none'`（`gnss/gnss_fix.dart:132-137`）——等价于「链路在产新鲜 NMEA」，对齐桌面 `gnssUp && hasUtc`。

**差异 / 判定**
- 核心诚实语义（不升级 system→gnss、system 必带告诫）：**已对齐**。
- 唯一差异：桌面在 system 态行尾仍附本机钟读数；Flutter 不附。
- **判定：不移植。** 理由：移动端天空页上方有独立 1Hz 本机时钟条（`sky_page.dart:351-353` 秒级 tick、`_StatusBar` `:461-501`），与时间源格同屏且更直观；再把钟读数塞进格子是冗余展示。口径（gnss=Ok / system=Warn 告诫）一致，仅读数落点不同，属展示层等效。

---

## 2. 多普勒门控（compOn && hasTarget）

**桌面语义** `spacetime_format.h:137-149` `spLineDoppler(armed, hasTarget, appliedHz)`：
- `armed && hasTarget` → Ok `补偿中（轨道传播实时微调）· 补偿值 {hz} Hz`（appliedHz 非有限则不附数字）
- `armed && !hasTarget` → **Warn** `补偿开但无目标（状态不一致）`
- 否则 → Neutral `未补偿（无目标）`
- 接线 `main_window.cpp:4836-4841`：`armed = dopplerCompChk_->isChecked()`（一个显式「实时补偿」勾选框）；`dopHz` 仅在 `compOn && hasTarget` 时取真实 range-rate `lastSpDopplerHz_`，否则 NaN（不编残差数字）。

**Flutter 现状**
- `spacetime_status.dart:97-106`：`dopplerHz != null && hasTarget` → Ok `补偿值 {hz} Hz`；否则 Neutral `未补偿（无目标）`。
- 接线 `sky_page.dart:421-426`：`dopplerHz` **恒不传（null）**——移动端暂无 SGP4 range-rate 实时补偿引擎，注释明示「绝不编造」。

**差异 / 判定**
- 主门控「无真实补偿值 + 有目标也不显示数字」：**已对齐**（Flutter 要求 `hasTarget` 且 `dopplerHz!=null` 才出数字）。
- 桌面三态里的 `armed && !hasTarget → Warn` 是**桌面专有**：它依赖一个「实时补偿」勾选框（`dopplerCompChk_`），移动端无此开关，也无在跑的补偿引擎。
- **判定：架构性不移植。** 移动端没有 compOn 开关，「补偿开但无目标」这一不一致态在本端无触发源；强行加一个永不触发的 Warn 分支是死代码。待未来移动端真接 range-rate 引擎并加开关时再补该 Warn 分支（届时 `fromServices` 增 `bool dopplerArmed` 入参即可）。
- **真机待验**：移动端实时多普勒补偿引擎本身未接线（诚实 null 空态），属既定设计，非缺陷。

---

## 3. 空态文案 & GNSS 格（含本批已移植项）

**桌面语义** `spacetime_format.h`：
- GNSS 无 fix → `无 fix`（Neutral，`:102-103`）
- GNSS 有 fix → `{lat},{lon} · 星{sats} · HDOP {hdop}`（Ok，`:104-108`，HDOP 保留 1 位小数）
- 目标无名 → `当前接收目标：无`（Neutral，`:126-127`）
- 多普勒无补偿 → `未补偿（无目标）`（Neutral，`:148`）

**Flutter 现状（移植前）** `spacetime_status.dart`：
- GNSS 无 fix → `无 fix`（Neutral，`:76`）✅ 逐字一致
- 目标无名 → `无接收目标`（Neutral，`:93`）✅ 语义一致（措辞略简，角色一致）
- 多普勒 → `未补偿（无目标）`（Neutral，`:104-106`）✅ 逐字一致
- GNSS 有 fix → `{lat},{lon} · 星{sats}`（移植前 `:68-75`）——**漏了 HDOP**，尽管 `GnssFix.hdop` 已由 NMEA 解析（`gnss_fix.dart:34`）。

**本批移植（已落地）**
- `mobile/lib/pages/spacetime_status.dart`：GNSS 格在 `fix.hdop != null` 时追加 ` · HDOP {hdop.toStringAsFixed(1)}`，与桌面 `:104-108` 同口径；`hdop == null` 时不追加该段（绝不编造精度因子）。
- 测试：`mobile/test/spacetime_status_test.dart` 新增「真实 fix 带 HDOP → 展示 `HDOP 0.8`」用例；旧用例补断言传 `isNot(contains('HDOP'))` 守护向后兼容。
- 这是**纯增量**：用的是移动端已经解析好的真实 NMEA HDOP，不引入任何新数据源/新假设。

---

## 4. 增益回退语义（桌面空表 25.4dB vs Flutter）

**桌面语义**
- Python 服务端 `mbdsdr_ai/sdr_backend.py`：`_UNKNOWN_TUNER_FALLBACK_GAIN_DB = 25.4`（空增益表时的 R820T 系安全中点），`_maybe_apply_first_gain_midpoint()` 仅在「首启且 gain_db==0.0（用户从未手动设过）」时把增益拉到增益表中点（表空→25.4）。目的：「确保首启绝不留 0 dB 聋棒」（对齐 gqrx/SDR++ 初始化）。
- C++ 前端 `cpp/src/dsp/rtl_sdr_source.h:68` 默认 `gainDb_=20.0`；空表时 `setGain` 诚实透传不臆造档位（`rtl_sdr_source.cpp:23-36`、`tuner_gain_table.cpp:17-19`）；`gain_control_model.h:44-45` 空表 → 连续滑杆「未读到离散档位表」。

**Flutter 现状** `mobile/lib/services/radio_controller.dart`
- `_gainDb = 20`（`:170`）、`_autoGain = true`（`:171`）。
- 建链 `_establish()`（`:300-307`）顺序：采样率 → `setGainMode(auto:_autoGain)` → `setAgcMode(on:_autoGain)` → 仅当 `!_autoGain` 才下发手动增益。即**默认 AGC 开，首连根本不下发 0 dB**。
- 移动端是 **rtl_tcp 客户端**（`rtl_tcp_client.dart`）：协议无「查询离散增益档表」的命令，本端拿不到 librtlsdr 的增益档数组，因此**没有「增益档表」这一概念**。`set_gain` AI 工具（`ai_tools.dart:141-184`）只按连续 `gain_db` 下发，范围 `AppTokens.gainMinDb..gainMaxDb`（0–49.6，`tokens.dart:189-190`）。

**差异 / 判定：架构性差异，不移植。**
1. 25.4dB 回退是**服务端首连初始化**关心的事（`sdr_backend.py` 直接开本机 RTL 设备时执行）；移动端连的是**远端** rtl_tcp 服务端，增益档表/首连中点由那个服务端负责，客户端无从也不应代设。
2. 移动端默认 `autoGain=true`，**从机制上消除了「停在 0 dB 聋棒」这一失效模式**——AGC 连续自适应，比「拉到固定中点」更稳。
3. 若在客户端臆造一张离散增益档表去 snap，等于伪造远端调谐器从未上报的档位，违反一贯诚实模型。
4. 手动默认 `_gainDb=20` 与 C++ `RtlSdrSource::gainDb_=20.0` 一致（`:68`）——切手动时落点一致。
- 结论：**意图对齐（首启绝不聋棒），机制不同（AGC 默认开 vs 固定中点回退）**，属 rtl_tcp 瘦客户端架构的必然差异，记录不移植。

---

## 5. 快捷键语义

**桌面** `cpp/src/ui/shortcuts_catalog.h:35-48`：←/→ 步进频率、Shift 细调、+/- 增益档、Space 静音、Ctrl+R 录制等；纯算术在 `nudgeFreqHz`/`stepGainDb`（`:54-97`）单测。

**Flutter**：移动端无物理键盘。调谐走频谱手势/输入框，增益走滑杆，静噪/录制/捕获走按钮（`sky_page.dart` `_PassList` 捕获、radio 面板录制）。

**判定：架构性不移植。** 触屏无快捷键概念；桌面那套步进算术在移动端由连续控件 + 真实下发承担。记录，不移植。

---

## 6. 平台声明完备性

### 6.1 Android（`mobile/android/app/src/main/AndroidManifest.xml`）
- ✅ `uses-feature android.hardware.usb.host required="false"`（`:3`）——外部 USB GNSS/USB-SDR 可用，无 host 设备仍可装。
- ✅ `queries` PROCESS_TEXT（`:41-46`）——Flutter 引擎文本插件需要。
- ⚠️ **真实缺口：无任何 `<uses-permission>`。** 应用用 geolocator 取测站经纬度（`location_service.dart` GeolocatorLocationService），而 `geolocator_android-5.1.1` 自带清单**只**合并 `FOREGROUND_SERVICE_LOCATION`（其 `android/src/main/AndroidManifest.xml:5`），**不含** `ACCESS_FINE_LOCATION`/`ACCESS_COARSE_LOCATION`——危险运行时权限必须由 app 清单声明。当前缺失 ⇒ 真机上 `requestPermission()/getCurrentPosition()` 拿不到定位权限，天空过境预测默认链路会停在 denied。
  - **建议修复（超本批可写范围，未动）**：在 `AndroidManifest.xml` `<application>` 之前加
    `<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>`
    （需要精确测站坐标做过境预测；若仅粗定位可用 COARSE，但对星指向建议 FINE）。
  - **真机待验**：补声明后权限弹窗是否按预期出现、`GeolocatorLocationService` 是否回到 `available`——云内无设备无法验证。
- SDK 版本：`android/app/build.gradle` 用 `flutter.compileSdk/minSdk/targetSdkVersion`（`:24,37-38`）跟随 Flutter 3.47.5 stable 默认，未硬编码——随 Flutter 升级自动对齐，无过时硬编码风险。
- 无 `RECORD_AUDIO`：**有意**。移动端只写解调后 PCM/WAV 落盘（`radio_controller.dart` FileRecordingSink），不采集麦克风，故不声明、也不应声明。

### 6.2 iOS（`mobile/ios/Runner/Info.plist`）
- ✅ `NSLocationWhenInUseUsageDescription`（`:29-30`）已声明，文案与用途一致（测站经纬度/对星/GNSS 授时参考）。
- 无 `NSMicrophoneUsageDescription`：**有意**——播放/写 WAV 不需麦克风权限。
- `UISupportedInterfaceOrientations` 已含竖屏 + 左右横屏（`:58-63`），与移动端横屏/竖屏双布局（`sky_page.dart:396-444`）一致。
- IMU（`sensors_plus`）取设备朝向：Core Motion device motion 在 iOS 不需要 usage 描述字符串，无需补 `NSMotionUsageDescription`。

---

## 7. 本批落地清单与验证数字

**代码改动（均在可写范围）**
1. `mobile/lib/pages/spacetime_status.dart` —— GNSS 格对齐桌面补 HDOP 段。
2. `mobile/test/spacetime_status_test.dart` —— 新增 HDOP 用例 + 旧用例向后兼容断言。

**验证**
- `flutter test`：**305 passed**（跑前基线 304，新增 +1；无回归）。
- `flutter analyze`：**No issues found**（0）。
- 工具链：`/home/user/tools/flutter/bin/flutter`（3.47.5 stable）。

**未完成 / 未做（诚实披露）**
- Android `ACCESS_FINE_LOCATION` 缺失缺口：**未修**（`mobile/android/` 在本批可写范围之外），仅记录 + 给修复建议；需真机验证权限弹窗。
- 移动端实时多普勒 range-rate 引擎、GNSS fix 流与卡片接线：既定诚实空态，本批不接（无硬件）。
- 未 commit / push（云内无凭据，按红线）。

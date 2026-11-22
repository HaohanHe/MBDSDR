# MBDSDR 手机端（Flutter）

原生 App（Android / iOS 一套代码），不是 WebView 壳。
定位：把手机变成 AI 的「眼睛和手臂」——GPS 给位置、罗盘给朝向、AI 据此算出
卫星仰角/方位角，反向指挥人把天线/手机转到正对卫星；同时通过 rtl_tcp
直连一台外置 RTL-SDR，让 AI 用 function calling 真正完成调谐。

## 验证状态：analyze / test 已实测通过（2026-11-22）

- 工具链：**Flutter 3.50.0 / Dart 3.14.0**（仓库外 `D:\mbdsdr\toolchain\flutter`），
  Android SDK / Gradle 缓存同在 D 盘，均不随仓库分发。
- `flutter pub get`：**成功**（依赖从 pub 缓存/镜像解析，未离线失败；24 个包有更新但受约束锁定）。
- `flutter analyze`（2026-11-22 实测，152 个源码 Dart 文件 = lib 82 + test 70）：
  **0 error / 0 warning / 64 info**，耗时 1.1 s。info 为 `deprecated_member_use`
  （Flutter 3.50 起 `package:flutter/material.dart` 提示改用 `material_ui`）与 2 条测试内
  `avoid_dynamic_calls`，不影响编译，未批量改。原始输出：`D:\mbdsdr\output\flutter_analyze_ab52.log`。
- `flutter test`（2026-11-22 实测）：**545 passed / 0 failed**，耗时 22 s
  （含天空投影对拍 84 点、反投影往返、四态裁剪、投影有效性域/跨域裁剪 4 条、
  指天视图 21 条、星表资产 4 条、asterism overlay 行为 11 条、首次成功端到端 5 条）。
  历史锚点：539（首次模拟器验证轮）→ 541（设置页「输入即落盘」1 条 +
  频谱页「竖屏主操作置顶 / 宽屏仍在底部」1 条）→ 543（鉴频输出归一化 2 条：
  NFM 幅度/不越界 + WFM 不越界）→ 545（抽取型 FIR 与旧口径逐点等价 1 条、
  offset=0 跳过混频 1 条）。
- `flutter build apk --debug`（2026-11-22 实测）：**成功**；DSP 优化后复跑 Gradle
  `assembleDebug` 42.6 s，产物 `build/app/outputs/flutter-apk/app-debug.apk`
  （190,120,531 B；历史：190,119,009 B / 190,118,996 B / 190,116,539 B / 首次 190,110,524 B）；
  `flutter build apk --profile` 也可出包（`app-profile.apk` 74.8 MB）。
  仅有两条工具链前瞻告警（AGP 9.1.1 将不再支持，建议 ≥9.3.1；Kotlin 2.4.10 建议 ≥2.4.20），
  不影响本次出包，未擅自升级（升级需单独回归）。
- **模拟器安装与运行（2026-11-22 首次验证）**：AVD `mbdsdr35`（Android 15 / API 35，
  `google_apis;x86_64`，720×1280，4 GB RAM，`-gpu swiftshader_indirect -no-window`）
  上 `adb install -r app-debug.apk` → **Success**；`am start -W -n mbdsdr.app/.MainActivity`
  → `Status: ok`、冷启动 8.8 s；应用进程存活，五个页签（频谱/天空/AI/录音/设置）全部渲染，
  天空页给出「定位服务未开启。过境预测需要您的位置。」的诚实空态，设置页可显示 rtl_tcp
  主机/端口与 ControlHub 分组。截图：`D:\mbdsdr\output\_shots\mobile_emulator_*.png`。
  备注：Android 36.1 Play Store 镜像在 `surfaceflinger` 的 `readFromHost` 断言
  （`hasReadColorBufferDma`）崩溃、用户停在 BOOTING，改用 API 35 后正常；旧版
  avdmanager 生成的 AVD 默认 `hw.ramSize=96M`，必须手改为 4096 才能启动。
- **模拟器 → 真实 SDR（2026-11-22）**：宿主跑最小 `rtl_tcp` 服务（真实 RTL2832U，
  绑定 0.0.0.0:1234），模拟器内 App 填 `10.0.2.2:1234` 并点「连接」：服务端记录
  `rate=2048000 / gain_mode=0(auto) / freq=144000000`，持续送出 **1750 块 / 114,688,000 B
  （约 114 MB）** 后由我们主动停止；App 页头显示 **`rtl_tcp 已连接`**，状态行
  `144.000000 MHz（设定值） NFM · RSSI -31.1 dBFS（实测峰值） · 2.048 MS/s（设备报告） ·
  自动增益 · S2`。这是**模拟器内 App 经真实 rtl_tcp 拿到真实硬件数据**，不是宿主 Dart VM。
- 仍未验证：**物理手机安装与运行**（本机无 Android 真机）；真机罗盘/IMU 在户外的实际指向
  精度、星表在真机上的渲染帧率、扬声器出声与音频路由均未实测。不得据此宣称真机可运行或已验收。
- **上一轮发现的两个小缺陷已修并复验（2026-11-22）**：
  1. 设置页 rtl_tcp / ControlHub 主机与端口改为 `onChanged` 即静默落盘（值未变不写、
     端口越界/半截输入不写），不再依赖键盘「完成」键；另加 `onTapOutside` 收起键盘。
     模拟器实测：输入 `10.0.2.9` 后只按系统返回键（不按完成）→ 回频谱页按钮直接变成
     `连接 10.0.2.9:1234`；改回 `10.0.2.2` 同样即时生效。
  2. 竖屏（窄屏 <600 dp）把「连接/断开」主操作提到控制面板顶部，宽屏保持底部。
     模拟器实测：面板首项即 `连接 10.0.2.2:1234`，其下才是 `144.000000 MHz` 读数，
     不必再滚过整列滑杆。回归测试：`settings_page_commit_test.dart`、
     `spectrum_panel_audit_test.dart`（竖屏置顶 / 宽屏置底）。
- **顶部主操作连真实 SDR（2026-11-22 复验）**：同上的模拟器 + 宿主最小 `rtl_tcp`，
  点面板顶部按钮连接后页头 `rtl_tcp 已连接`，状态行 `RSSI -31.2 dBFS（实测峰值） ·
  2.048 MS/s（设备报告）`；服务端两个连接会话分别送出 **3800 块 / 249,036,800 B** 与
  **1250 块 / 81,920,000 B**，随后点「断开」→ 页头回 `rtl_tcp 未连接`、空态
  「当前无实时 IQ，不显示模拟数据。」且 5 s 静置无自动重连。截图：
  `D:\mbdsdr\output\_shots\mobile_fix_*.png`。
  （注：软件渲染下瀑布高频刷新时 `uiautomator dump` 偶发 `null root node`，
  属采集工具限制，以截图与宿主服务端日志为证。）
- **DSP 实时性优化（2026-11-22）**：`Channelizer` 从「每个输入样点算完整 129/161 抽头
  卷积、随后丢掉 39/40 的结果」改为**抽取型 FIR**（只在输出时刻卷积，
  O(N·fs/D)）；offsetHz=0 时跳过逐样点 sin/cos；NFM/WFM 音频帧改预分配
  （去掉逐样本 `List.add` 与 Float64→Float32 整块拷贝）。等价性由
  `filters_test` 钉死：decim=8 的每个输出都等于 decim=1 结果每隔 8 个取样
  （逐点 1e-12）。
- **模拟器交互 ANR：根因已定位并修复（2026-11-22）**：现象是 API 35 + swiftshader、
  2.048 MS/s 实时流下点面板后弹「MBDSDR isn't responding」，logcat 为
  `Input dispatching timed out`（>5 s），debug 与 profile 都复现。根因不在 Dart：
  `MainActivity.kt` 的 `mbdsdr/audio` MethodChannel 回调跑在 **Android 主线程**，
  里面直接调阻塞的 `AudioTrack.write`；模拟器 `-no-audio` 时下沉很慢，主线程被卡住，
  输入分发超时。
  修复：有界队列（3 帧）+ 专用写线程 `mbdsdr-audio-writer`，`writeStream` 只入队
  立即返回，队列满丢最旧帧（实时性优先）；每代流独立队列，旧写线程自然退出，
  不会把旧帧写进新轨道。
  验证：连续送流 20 s 后**首次点击**频率读数即弹对话框；随后完成 88.1 MHz 调谐、
  16.6 s WFM 录音、录音页签刷新，全程未再出现 ANR。native 改动无单元测试，
  证据是交互实测；**真机出声与音频延迟仍未验证**。
- **设备内录制（2026-11-22 模拟器复验，含一处真缺陷修复）**：连接真实 SDR 后滚到
  「录制」→ 红点 `录制中…` → 停止 → 设备目录落 `.wav` + sidecar；录音页签同步列出
  `144.0000 MHz · NFM · 20s / 25s`（索引刷新已实测）。
  - **修复**：首录 25 s 的 WAV 里 **39.32% 样本贴满 ±32767**（RMS -2.5 dBFS）——鉴频输出是
    「弧度/样点」（±π），却直接喂给契约要求 [-1,1] 的 `floatTo16BitPcm`，静默削顶失真。
    `demod.dart` 的 `_discriminate` 统一除以 π（NFM/WFM 共用出口）。
  - **复录验证**：同机同频点同增益再录 20.04 s，削顶 **3/961,741 样本 = 0.0003%**，
    RMS 13,195（-7.9 dBFS）、峰值 32,767、均值 -405（前值：39.32% / 24,456 / -1,634）。
    证据：`D:\mbdsdr\output\_shots\mobile_rec.wav`（前）、`mobile_rec_norm.wav`（后）。
  - 未验证：扬声器出声/音频路由、听感与失真测量（只做了样本统计）。
- **WFM 真实广播（2026-11-22 模拟器 + 真实 RTL-SDR）**：88.1 MHz 收到真实调频广播，
  频谱 `PK 88.002 MHz / RSSI -14.1 dBFS / SNR 36 dB / BW 2.048M`，瀑布可见多台信号。
  面板录制 16.57 s → `1795339681003_88100000_wfm.wav`（795,453 帧，48 kHz mono 16-bit）：
  **削顶 0 样本（0.0000%）**、峰值 26,333（0.80 FS）、RMS -5.4 dBFS，10 段 RMS
  16,072–20,609（有节目动态，不是恒定噪声）。录音页签同步列出 `88.1000 MHz · WFM · 17s`。
  证据：`D:\mbdsdr\output\_shots\mobile_wfm_881.wav` / `.json`。
  仍未验证：真的听到声音（本机无音频路由/真机）、立体声导频与 RDS、音频延迟。
- 真机数据通路（2026-11-22，**宿主 Dart VM**）：本机 `rtl_tcp` + 真实 RTL-SDR，
  移动端 `RtlTcpClient` 连 127.0.0.1:1237、下发 100.100 MHz / 2.400 MS/s / 自动增益，
  收到 **183 个 IQ 块 / 7,208,960 样本（3 s）**。这验证的是移动端协议与解码代码
  对真实 SDR 可用，**不是手机安装运行**。

## 架构

```
lib/
├── app/                    # 外壳层
│   ├── tokens.dart         # 唯一设计 token（颜色/圆角/字体/硬件范围），冻结
│   ├── theme.dart          # 深色 Material3 主题（主题名「默认」）
│   ├── home_shell.dart     # 响应式导航：宽屏 NavigationRail / 窄屏 BottomNavigationBar
│   └── ai_tools.dart       # 把 RadioApi 包装成 AI 可调用的工具集
├── models/                 # 纯数据模型：radio_state、satellite、chat_message
├── services/               # 服务层
│   ├── rtl_tcp_client.dart# 依公开协议用 Dart 重写的 rtl_tcp 客户端（Socket + IQ 流）
│   ├── radio_controller.dart # rtl_tcp + FFT + NFM/WFM 解调的统一控制器（ChangeNotifier）
│   ├── ai_client.dart      # 硅基流动（OpenAI 兼容）流式 + function-calling 客户端
│   └── settings_service.dart # 设置持久化（KvStore / SecureStore 缝）
├── dsp/                    # 信号处理：IQ 类型、FFT（fftea）、滤波、NFM/WFM 解调
├── astro/                  # TLE 拉取（Celestrak）、SGP4、坐标换算
├── pages/                  # 四个页面：频谱 / 天空 / AI / 设置
└── widgets/                # 通用件：EmptyState、ConnectionStatusLine、频谱显示
```

状态管理用 [provider](https://pub.dev/packages/provider)：
启动时构造 `SettingsService` 与 `RadioController` 两个 `ChangeNotifier`，
经 `MultiProvider` 注入；设置写入即落盘并通知，射频状态变化驱动 UI 刷新。

## 四个页面

### 1. 频谱（Spectrum）
- rtl_tcp 直连真实 IQ 流 → `FftProcessor`（fftea）做真实 FFT；
- 实时频谱柱状图 + 瀑布图，NFM / WFM 解调模式可切；
- 顶部 AppBar 的安静状态行实时显示 rtl_tcp 连接状态（已连接/连接中/掉线等待重插/真实失败原因）；
  运行中掉线由控制器按指数退避**真实重连**（新建 Socket 握手），用户手动断开后停止重试。

### 2. 天空（Sky Pointing）
- Celestrak 拉 TLE，SGP4 算卫星位置，坐标换算到本站（手动三坐标或 GPS）；
- 罗盘 + 传感器给手机朝向，引导人把天线转到目标卫星方位/仰角；
- 右上「指天视图」进入姿态驱动天球页（`pages/pointing_page.dart`）：透视视场随
  航向/俯仰/横滚实时转，星点与星官线按当前站址/时刻现算，目标卫星给
  「向左/右转多少度、抬头/低头多少度」；没有航向角就明说「罗盘不可用」，
  不用 0° 假装朝北。状态条给近 2 s 航向波动（样本不足时明说），
  并可「左/右 5°」手动对齐；进入 ±5° 只说「已进入目标天区」。星表/星官来自
  `assets/sky/`（`stars.bin` + `asterisms.json`），
  读不到显示真实原因，不退回空星表。

### 3. AI 对话（Chat）
- 硅基流动 OpenAI 兼容接口，SSE 流式输出；
- 注册的工具是**真正调谐**而不是演戏：
  - `set_frequency`（frequency_hz / frequency_mhz 二选一，范围 24–1700 MHz）
  - `set_mode`（nfm / wfm）
  - `set_gain`（auto 或手动 0–49.6 dB）
  - `set_sample_rate`（限定支持的采样率档位）
  - `get_status`（读回连接/频率/模式/增益/采样率）
- 参数缺失或越界统一返回 `{ok:false,error:...}`，不让会话崩掉。
- 设置页可切「AI 接管 / 手动」：AI 接管时上述工具真正执行；手动模式下
  写动作仅返回「手动模式：未执行」并在对话流标注（只读 `get_status` 放行）。

### 4. 设置（Settings）
- rtl_tcp 主机/端口（端口校验 1–65535）；
- AI API key **只写系统安全存储**（Keystore / Keychain），普通 KV 里没有该键；
- 模型名、本站经纬度/海拔（可一键自动定位）、外观（当前仅「默认」深色）。

## 构建

```bash
flutter pub get
flutter analyze
flutter test
flutter run
```

**本机实测口径**（Windows，工具链在 D 盘）：构建前必须把 Android SDK 指向
D 盘那份，否则 Gradle 会挑到 WinGet 装的 C 盘 SDK 并以「NDK 许可未接受」失败：

```powershell
$env:ANDROID_HOME = "D:\mbdsdr\toolchain\android-sdk"
$env:ANDROID_SDK_ROOT = "D:\mbdsdr\toolchain\android-sdk"
$env:GRADLE_USER_HOME = "D:\mbdsdr\toolchain\gradle-home"
flutter build apk --debug
```

## 已知限制（诚实声明）

- **物理手机未验证**：本机没有连接 Android 真机；安装与运行证据来自 API 35 模拟器
  （含真实 SDR 连接/录制）。真机罗盘/IMU 的户外指向精度、星表渲染帧率、
  扬声器出声/音频路由与延迟都未实测。
- 解调音频链路：`PlatformPcmSink` → MethodChannel `mbdsdr/audio`；**Android 已接线**
  （`android/app/src/main/kotlin/mbdsdr/app/MainActivity.kt`，`AudioTrack` `MODE_STREAM`，
  另含文件回放），Dart 侧契约与 Float32→16-bit 量化有单测。
- **iOS 仅契约级**：`ios/Runner/AppDelegate.swift` 的 `AudioEngineSwift` 整类仍在块注释内，
  `attach` 注册点保持注释，需在 Xcode 下接线——不宣称 iOS 可用。
- **真机出声未验证**：本机没有 Android/iOS 设备，扬声器出声、音量/音频路由、
  48 kHz 采样率匹配均未实测；因此不对外宣称「已能听」。
- SGP4 为公开参考实现移植，**忽略章动与极移**，对低仰角指向有约 0.1° 量级误差。
- 指天显示航向近 2 s 波动并可手动对齐（±5°，会话内、不落盘）；真机指向精度仍未验证。
  波动是观测统计，不是误差上界，也不是传感器标称精度。
- 不内置任何 FM 电台频率、地理位置或密钥；所有连接参数与 key 都由用户自己填写。
- 手机读数的来源标注覆盖 sky / spectrum；真机上的 GNSS/罗盘来源仍未实测。

许可证见根目录 LICENSE（MIT），第三方归属见 NOTICE.md。

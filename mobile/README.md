# MBDSDR 手机端（Flutter）

原生 App（Android / iOS 一套代码），不是 WebView 壳。
定位：把手机变成 AI 的「眼睛和手臂」——GPS 给位置、罗盘给朝向、AI 据此算出
卫星仰角/方位角，反向指挥人把天线/手机转到正对卫星；同时通过 rtl_tcp
直连一台外置 RTL-SDR，让 AI 用 function calling 真正完成调谐。

## 验证状态：已编译、已跑测（2026-11-19）

- 工具链：**Flutter 3.50.0 / Dart 3.14.0**（仓库外 `D:\mbdsdr\toolchain\flutter`），
  Android SDK / Gradle 缓存同在 D 盘，均不随仓库分发。
- `flutter analyze`：**0 error / 0 warning**（info 仅包废弃与风格提示，不影响编译）。
- `flutter test`：**527 passed**（含天空投影对拍 84 点、反投影往返、四态裁剪、
  指天视图 21 条、星表资产 4 条、asterism overlay 行为 11 条、首次成功端到端 5 条）。
- `flutter build apk --debug`：**成功**，产物 `build/app/outputs/flutter-apk/app-debug.apk`。
- 仍未验证：**真机安装与运行**（未连接设备）；真机罗盘/IMU 在户外的实际指向
  精度、以及星表资产在真机上的渲染帧率未测。不得据此宣称真机可运行或已验收。

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

- **真机未验证**：本机没有连接 Android 设备，APK 能出包但**未安装运行**；
  真机罗盘/IMU 的户外指向精度、星表在真机上的渲染帧率都未实测。
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

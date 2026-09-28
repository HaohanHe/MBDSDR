# MBDSDR 手机端（Flutter）

原生 App（Android / iOS 一套代码），不是 WebView 壳。
定位：把手机变成 AI 的「眼睛和手臂」——GPS 给位置、罗盘给朝向、AI 据此算出
卫星仰角/方位角，反向指挥人把天线/手机转到正对卫星；同时通过 rtl_tcp
直连一台外置 RTL-SDR，让 AI 用 function calling 真正完成调谐。

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
└── widgets/                # 通用件：EmptyState、StatusChip、频谱显示
```

状态管理用 [provider](https://pub.dev/packages/provider)：
启动时构造 `SettingsService` 与 `RadioController` 两个 `ChangeNotifier`，
经 `MultiProvider` 注入；设置写入即落盘并通知，射频状态变化驱动 UI 刷新。

## 四个页面

### 1. 频谱（Spectrum）
- rtl_tcp 直连真实 IQ 流 → `FftProcessor`（fftea）做真实 FFT；
- 实时频谱柱状图 + 瀑布图，NFM / WFM 解调模式可切；
- 顶部 AppBar 的 StatusChip 实时显示 rtl_tcp 连接状态（已连接/连接中/失败/未连接）。

### 2. 天空（Sky Pointing）
- Celestrak 拉 TLE，SGP4 算卫星位置，坐标换算到本站（手动三坐标或 GPS）；
- 罗盘 + 传感器给手机朝向，引导人把天线转到目标卫星方位/仰角。

### 3. AI 对话（Chat）
- 硅基流动 OpenAI 兼容接口，SSE 流式输出；
- 注册的工具是**真正调谐**而不是演戏：
  - `set_frequency`（frequency_hz / frequency_mhz 二选一，范围 24–1700 MHz）
  - `set_mode`（nfm / wfm）
  - `set_gain`（auto 或手动 0–49.6 dB）
  - `set_sample_rate`（限定支持的采样率档位）
  - `get_status`（读回连接/频率/模式/增益/采样率）
- 参数缺失或越界统一返回 `{ok:false,error:...}`，不让会话崩掉。

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

## 已知限制（诚实声明）

- **本仓库云端没有 Android/iOS 构建工具链**，本版未做真机构建，只保证 Dart 侧
  编译与单元测试通过；真机表现需在本地开发机验证。
- 解调音频本版只产出 Float32 音频流，**尚未接扬声器**（音频路由/采样率匹配
  留待后续）。
- SGP4 为公开参考实现移植，**忽略章动与极移**，对低仰角指向有约 0.1° 量级误差。
- 指向精度取决于手机罗盘/加速度计校准质量，强干扰环境下需手动修正。
- 不内置任何 FM 电台频率、地理位置或密钥；所有连接参数与 key 都由用户自己填写。

许可证见根目录 LICENSE（GPL-3.0-or-later），第三方归属见 NOTICE.md。

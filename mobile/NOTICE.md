# 归属与许可证声明（NOTICE）

`mobile/` 目录沿用本仓库根目录的 **GPL-3.0-or-later** 许可证（与 cpp/、desktop/
等模块一致）；本目录**不另建 LICENSE 文件**。

## 第三方组件与灵感来源

### rtl_tcp 协议
- 协议参考自 Osmocom 项目的 `rtl_tcp`（原属 rtl-sdr 工具集，GPL-2.0+）。
- 本端并非直接使用其代码，而是按公开的 rtl_tcp 线协议在
  `lib/services/rtl_tcp_client.dart` 中用 Dart 重新实现；协议本身是开放的
  事实标准，重新实现不修改上游代码。

### SDR++（GPL-3.0）
- 瀑布图配色、频谱统一几何与车机式深色视觉气质参考了 SDR++ 的设计，
  未复制其源代码。

### fftea（MIT）
- 真实 FFT 计算使用 [fftea](https://pub.dev/packages/fftea)（MIT 许可）。

### SGP4
- 轨道预报移植自 David Vallado 等人发布的公开 SGP4/SDP4 参考实现
  （public domain / 学术公开许可），代码位于 `lib/astro/sgp4.dart`。

### Flutter 生态插件
- [shared_preferences](https://pub.dev/packages/shared_preferences)（BSD-3）
- [flutter_secure_storage](https://pub.dev/packages/flutter_secure_storage)（BSD-3）
- [provider](https://pub.dev/packages/provider)（MIT）
- [geolocator](https://pub.dev/packages/geolocator)（MIT）
- [flutter_compass](https://pub.dev/packages/flutter_compass)（MIT）
- [sensors_plus](https://pub.dev/packages/sensors_plus)（BSD-3）

上述插件各自保留其原始许可证与版权声明。

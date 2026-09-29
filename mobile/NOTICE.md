# 归属与许可证声明（NOTICE）

`mobile/` 目录的自有代码以仓库根目录的 **MIT 许可证**发布（与 cpp/、desktop/ 等模块
一致）；本目录不另建 LICENSE 文件。下列功能均依据公开标准或开放协议独立实现，所提及
的第三方项目仅作为技术参考或致谢，未包含其源代码。

## 第三方组件与参考来源

### rtl_tcp 协议
- 协议参考自 Osmocom 项目的 `rtl_tcp`（rtl-sdr 工具集）。rtl_tcp 线协议是开放的事实
  标准；本端未使用其代码，而是在 `lib/services/rtl_tcp_client.dart` 中按公开线协议用
  Dart 独立实现。

### 频谱与瀑布显示
- 频谱统一几何、瀑布配色与深色视觉风格参考了 SDR++ 的设计思路（几何与数据流属通用
  思想），未复制其源代码；相关绘制代码为本项目独立实现。

### fftea（MIT）
- FFT 计算使用 [fftea](https://pub.dev/packages/fftea)（MIT 许可），作为第三方依赖
  引入，保留其许可与版权声明。

### SGP4
- 轨道预报依据 Spacetrack Report #3 及 Vallado 等人公开的 SGP4/SDP4 算法（公有领域/
  学术公开）在 `lib/astro/sgp4.dart` 中独立实现，未复制参考实现代码。

### Flutter 生态插件（各自保留其原始许可证与版权声明）
- [shared_preferences](https://pub.dev/packages/shared_preferences)（BSD-3）
- [flutter_secure_storage](https://pub.dev/packages/flutter_secure_storage)（BSD-3）
- [provider](https://pub.dev/packages/provider)（MIT）
- [geolocator](https://pub.dev/packages/geolocator)（MIT）
- [flutter_compass](https://pub.dev/packages/flutter_compass)（MIT）
- [sensors_plus](https://pub.dev/packages/sensors_plus)（BSD-3）
- [intl](https://pub.dev/packages/intl)（BSD-3）

仅测试期使用：
- [fake_async](https://pub.dev/packages/fake_async)（BSD-3）——驱动重连退避状态机的
  虚拟时钟。

上述插件与第三方组件各自保留其原始许可证与版权声明。

# MBDSDR 手机端（Flutter）

原生 App（Android / iOS 一套代码），不是 WebView 壳。

## 定位
把手机变成 AI 的"眼睛和手臂"：
- GPS → 位置，AI 据此算卫星仰角/方位角
- 指南针 → 手机朝向，AI 指挥你把天线/手机转到正对卫星
- IMU → 姿态，辅助云台/手持指向

## 构建
```bash
cd mobile
flutter pub get
# 插手机（开 USB 调试）或起模拟器
flutter run
```

需要：Flutter SDK >=3.3、Android SDK（本地 Android Studio 装齐即可）。

## 协议
WebSocket 连电脑端（默认 `ws://<电脑IP>:8765`）：
- 上线发 `handshake`，声明 capabilities: gps/compass/imu/camera
- 周期发 `telemetry`（kind: fix/heading/imu）
- 收 `satellite_passes`（卫星过境列表）、`ai_command`（AI 指挥人动作）

## 电脑端待补
电脑侧还没有对应 WebSocket server（P2），后续在 mbdsdr_ai 里起一个：
接收手机 telemetry，结合 TLE 算过境，把"把天线转到方位角 X°、仰角 Y°"作为 ai_command 推给手机。

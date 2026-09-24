# MBDSDR 手机端（Flutter）

原生 App（Android / iOS 一套代码），不是 WebView 壳。

## 定位
把手机变成 AI 的"眼睛和手臂"：
- GPS → 位置，AI 据此算卫星仰角/方位角
- 指南针 → 手机朝向，AI 指挥你把天线/手机转到正对卫星
- IMU → 姿态，辅助云台/手持指向
- 实时频谱 → 手机屏当外接频谱显示器
- AI 对话 → 手机端直接和电脑端 AI 交互，指挥 SDR 操作

## 功能页面

底部导航三个标签页：

### 1. 频谱（Spectrum）
- 从电脑端 WebSocket 接收 FFT 幅度数组，`CustomPainter` 逐 bin 画柱状频谱
- 柱体蓝灰→橙色渐变，深色背景，三条水平参考线
- 顶部显示中心频率 / 带宽，底部频率轴（左低右高）
- 帧间 alpha=0.3 指数平滑，避免闪烁
- 未连接时显示占位提示

### 2. 指向（Sky Pointing）
- 顶部罗盘圆环：N/E/S/W 方位刻度，每 30° 一格
- 双箭头：红色=当前手机朝向（固定指上），橙色=目标卫星方位（相对偏移）
- 文字引导：`左转 X°` / `右转 Y°` / `方位已对准 ✓`，偏差 < 5° 判定对准
- 俯仰角只读滑块（0–90°），实时显示目标仰角
- 卫星过境列表：显示卫星名、最高仰角、方位、升起时间，点击选中高亮

### 3. AI 对话（Chat）
- 气泡式对话：用户消息右侧橙色气泡，AI 回复左侧白色卡片气泡
- 每条消息带时间戳（HH:mm），自动滚动到底部
- 底部输入框（支持多行），发送按钮调用 `connection.sendChat()`
- 未连接时输入框禁用
- 订阅电脑端 `ai_command` 流，AI 回复实时追加

## 架构

```
mobile/lib/
├── main.dart              # 入口 + Provider + 底部导航 + 连接设置弹窗
├── connection.dart        # WebSocket 客户端（自动重连 / 心跳 / 传感器采集 / 广播流）
├── theme.dart             # 日式低饱和主题（米白 #F5F3EF / 蓝灰 #5B7B8C / 橙 #C4845C）
└── pages/
    ├── spectrum_page.dart # 实时频谱
    ├── sky_page.dart      # 卫星指向引导
    └── chat_page.dart     # AI 对话
```

`ConnectionService`（`ChangeNotifier`）统一管理：
- WebSocket 连接，断线后每 3 秒自动重连
- 心跳 ping/pong（15 秒间隔，5 秒超时触发重连）
- GPS / 指南针 / IMU 采集与周期上报
- 通过 `Stream` 广播：`fftStream` / `passesStream` / `pointingStream` / `aiCommandStream`

## 构建

```bash
cd mobile
flutter pub get
# 插手机（开 USB 调试）或起模拟器
flutter run
```

需要：Flutter SDK >=3.3、Android SDK（本地 Android Studio 装齐即可）。

## 协议

WebSocket 连电脑端（默认 `ws://<电脑IP>:8765`），点击右上角"连接设置"输入地址。

### 上行（手机 → 电脑）

| type | 说明 | payload |
|------|------|---------|
| `handshake` | 上线握手 | `{device, capabilities:[gps,compass,imu,camera]}` |
| `telemetry` | 传感器上报 | `kind: fix/heading/imu` + 对应数据 |
| `chat` | 用户发送文本 | `{text}` |
| `ping` | 心跳 | — |

### 下行（电脑 → 手机）

| type | 说明 | payload |
|------|------|---------|
| `fft` | 频谱数据 | `{bins:[...], center_freq_mhz, span_mhz}` |
| `satellite_passes` | 卫星过境列表 | `{passes:[{name,max_el,azimuth,rise_time}]}` |
| `pointing` | 指向目标 | `{satellite, azimuth, elevation}` |
| `ai_command` | AI 回复/指令 | `{text}` |
| `pong` | 心跳响应 | — |

## 电脑端待补

电脑侧 WebSocket server 需在 `mbdsdr_ai` 中实现：接收手机 telemetry，结合 TLE 算过境，推送 `fft` / `satellite_passes` / `pointing` / `ai_command`。当前移动端已按上述协议就绪，可独立编译运行（未连接时各页面显示占位状态）。

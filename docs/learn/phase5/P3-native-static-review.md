# P3 原生静态审查清单（MainActivity.kt / AppDelegate.swift）

> 范围：`mobile/android/.../MainActivity.kt`、`mobile/ios/Runner/AppDelegate.swift`，
> 以及 `AndroidManifest.xml`、`ios/Runner/Info.plist`。
> 环境事实（2026-10-01）：云 VM **无 Android SDK / 无 Xcode**，下列原生代码**从未真编译、
> 未链接、未真机运行**。本清单只做「无法真编译下的逐项静态审查」：
> 能静态判定的给 PASS/FAIL，不能的一律标 **待真机编译**，绝不把未编译代码说成已验证。
> 契约来源：`lib/audio/platform_pcm_sink.dart`、`lib/audio/file_player.dart`、
> `lib/gnss/usb_serial_port.dart`、`docs/learn/phase4/audits/B3-mobile-native.md`。
> 红线：本轮**不修改原生代码**（未发现可静态确证的必改 bug；存疑项全部转真机待验）。

---

## 0. 结论摘要（TL;DR）

| 平台 | 通道注册点 vs Dart 契约 | 权限/Manifest/Plist | API 用法 | 编译预扫结论 |
|---|---|---|---|---|
| **Android** | ✅ PASS（方法名/参数全对齐） | ✅ PASS（usb.host 已加；RECORD_AUDIO 有意不加） | ⚠️ 基本正确，2 项待真机 | **待真机编译**（逻辑 PASS，个别 API 级别需 SDK 确认） |
| **iOS** | ❌ **FAIL（无 handler）**：`AudioEngineSwift` 整体在 `/* */` 注释里，attach 也被注释 | ✅ PASS（定位描述已补）；音频无需权限 | ⚠️ 注册脚手架类型疑似不存在 | **FAIL/待真机编译**：大概率编译不过 + 即便解开也不注册任何 channel |

- **最关键诚实披露**：iOS 侧 `mbdsdr/audio` **当前没有任何 MethodChannel handler**
  （实时出声与文件回放都不会发生）。Android 侧 handler 已按契约写好，但云未编译。
- 本轮 Flutter 生产录制/回放接线（P3-②）在 **Dart 侧**闭环并单测通过；原生能否真出声
  仍属「真机待验」，UI 未宣称原生已可用。

---

## 1. Android — MainActivity.kt

### 1.1 通道注册点与 Dart 契约核对（`configureFlutterEngine`）

| Dart 契约（方法/参数） | Kotlin 处理 | 结论 |
|---|---|---|
| channel `mbdsdr/audio`（platform_pcm_sink.dart:66 / file_player.dart:59） | `MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "mbdsdr/audio")` | ✅ PASS 名称一致 |
| `start{sampleRate,channels}` | `call.argument<Int>("sampleRate") ?: 48000` / `"channels" ?: 1` → `audioEngine.startStream` | ✅ PASS |
| `write{pcm:Uint8List}` | `call.argument<ByteArray>("pcm")` → `writeStream` | ✅ PASS（Dart Uint8List 经标准编解码落到 Kotlin `ByteArray`，类型提取正确） |
| `setVolume{volume:double}` | `argument<Double>("volume").toFloat()` → `setVolume` | ✅ PASS |
| `setMuted{muted:bool}` | `argument<Boolean>("muted")` → `setMuted` | ✅ PASS |
| `dispose` | `stopStream()` | ✅ PASS |
| `startFile{path,sampleRate,channels}`（file_player.dart） | 三参提取 → `audioEngine.startFile(path,sr,ch,audioChannel)` | ✅ PASS |
| `pauseFile`/`resumeFile`/`stopFile` | 直通 | ✅ PASS |
| `filePosition` → `{positionMs,durationMs,playing}` | `result.success(audioEngine.filePosition())` | ✅ PASS |
| channel `mbdsdr/usb_serial`（usb_serial_port.dart:73） | 同引擎注册第二个 MethodChannel | ✅ PASS 名称一致 |
| USB `listDevices` | 返回 `List<Map>{deviceId,productName,manufacturerName,vidPid}` | ✅ PASS（与 `UsbDeviceInfo.fromMap` 键名一致） |
| USB `open{deviceId,baud}` | `argument<Int>` → `usb.open` | ✅ PASS |
| USB `close` | 直通 | ✅ PASS |
| 原生→Dart 事件 `onData{bytes}` | `channel.invokeMethod("onData", mapOf("bytes" to buf.copyOf(n)))` | ✅ PASS（与 `_onNativeCall` 一致） |
| 原生→Dart 事件 `onPosition`/`onComplete` | 文件回放线程里 invoke | ✅ PASS（参数键 positionMs/durationMs/playing 对齐 file_player.dart） |

> 未覆盖：Dart 期望的 `onAttached`/`onDetached` 热插拔事件，Android 未发（Dart 侧 `_onNativeCall`
> 对二者可选、不发不崩）。属**功能缺口**而非编译错误，转真机待验。

### 1.2 AndroidManifest.xml

| 项 | 现状 | 结论 |
|---|---|---|
| `<uses-feature android.hardware.usb.host required="false"/>` | 已声明 | ✅ PASS（与 B3 §4 建议一致；required=false 无 USB host 设备仍可装） |
| `RECORD_AUDIO` | **未声明** | ✅ PASS（有意：本应用不录麦克风，只把解调后 PCM 写盘，无需录音权限——B3 §4 明确） |
| `MODIFY_AUDIO_SETTINGS` | 未声明 | ✅ PASS（可选，播放不强制需要） |
| launcher activity / flutterEmbedding meta | 完整 | ✅ PASS |

### 1.3 Kotlin API 用法与编译陷阱预扫

| 项 | 静态判断 | 结论 |
|---|---|---|
| `AudioTrack(STREAM_MUSIC, sr, chMask, ENCODING_PCM_16BIT, buf, MODE_STREAM)` | 已废弃构造器（API21 起推荐 Builder），但**仍是合法可编译 API**（仅 deprecation 警告） | ✅ PASS（警告级） |
| `makeTrack` 的 `if SDK>=LOLLIPOP ... else ...` 两分支**代码完全相同** | 死分支，不报错；`@Suppress("DEPRECATION")` 挂在无废弃调用的分支上 | ⚠️ 待真机编译（逻辑冗余，非错误；建议真机清理） |
| `AudioTrack.write(ByteArray,offset,size)` | 该重载自 **API 21** 起存在；`minSdk = flutter.minSdkVersion`（本工程未钉死） | ⚠️ **待真机编译**：需确认 flutter.minSdkVersion ≥ 21（近版 Flutter 默认即 ≥21，预期 PASS） |
| `AudioTrack.setVolume(Float)` / `play()` / `stop()` / `release()` | 均为既有 API | ✅ PASS |
| `RandomAccessFile` 扩展 `readIntLe()`（文件末尾顶层 private fun） | 扩展接收者写全限定名 `java.io.RandomAccessFile`，同文件内可用 | ✅ PASS（语法合法） |
| WAV 头扫描循环：读 chunk id(4)+size(4,LE)，命中 `data` 取偏移，否则 `seek(+size+(size&1))` 16-bit 对齐 | 字节序/对齐逻辑自洽，与 `wav_writer.dart` 44 字节头契约一致 | ✅ PASS（逻辑）；真机读盘待验 |
| `PendingIntent.getBroadcast(..., FLAG_MUTABLE on S+)` | Android 12+ 强制显式 mutability，已正确处理 | ✅ PASS |
| `Intent(act.ACTION_USB_PERMISSION)` | 即 `new Intent(String action)`，构造器存在 | ✅ PASS |
| `dev.getInterface(0).endpoint(0)`（`interfaceCount.let{i->...}` 中 `i` 未用） | `UsbInterface.endpoint(int)` 存在；`i` 未用仅警告 | ✅ PASS（警告级） |
| USB 波特率初始化 `controlTransfer` | 文件内 `TODO(真机待验)` 占位 | ⚠️ **功能缺口**：CP2102/CH340/FTDI 初始化序列未写，真机无法设波特率（作者已标注） |
| **USB 权限广播接收者未注册** | `requestPermission` 发 `ACTION_USB_PERMISSION` 广播，但全文无 `registerReceiver` | ⚠️ **运行期缺口**（非编译错误）：权限结果无人接收，`open` 只能靠用户重试；真机需补 BroadcastReceiver |
| 未用 import（`Handler`/`Looper`/`IntentFilter`） | 仅 lint 警告 | ✅ PASS（建议真机清理） |

---

## 2. iOS — AppDelegate.swift

### 2.1 通道注册点与 Dart 契约核对

| 检查 | 现状 | 结论 |
|---|---|---|
| `mbdsdr/audio` channel 是否注册 handler | **否**：`didInitializeImplicitFlutterEngine` 里 attach 两行被注释；`AudioEngineSwift` 整个类在 `/* … */` 块注释内 | ❌ **FAIL**：iOS 上无 handler，`start/write/startFile…` 全部 `MissingPluginException`，实时出声与文件回放均不发生 |
| 注册脚手架类型 `FlutterImplicitEngineDelegate` / `FlutterImplicitEngineBridge` / `engineBridge.pluginRegistry` / `engineBridge.engine?.binaryMessenger` | 非 Flutter 公开标准 API（标准脚手架是 `FlutterAppDelegate` + `didFinishLaunchingWithOptions` + `rootViewController as! FlutterViewController`） | ❌ **待真机编译**：高度疑似编译失败（类型不存在）。云无 Xcode，静态无法确证，标 FAIL-疑似 |
| 解开注释后 `write`/`startFile` 实现 | 两个 case 仅 `result(nil)` + TODO，未真包 `AVAudioPCMBuffer`/未 `scheduleFile` | ⚠️ 即便解开也是**桩**，不出声 |
| `AVAudioSession.setCategory(.playback)` / `AVAudioFormat(pcmFormatInt16…)!` | 用法方向正确；`!` 强解包在 48k/单声道下 unlikely-nil | ✅ PASS（写法）；真机出声待验 |

> 说明：iOS 当前是「**有意注释 + 明确标注云未编译**」状态，与 B3 方案 B 的契约一致。本轮按红线
> **不重写**（无法在无 Xcode 下判定正确的注册点），只把「无 handler / 脚手架疑似不编译」如实上报。

### 2.2 Info.plist

| 项 | 现状 | 结论 |
|---|---|---|
| `NSLocationWhenInUseUsageDescription` | 已补（「用于确定测站经纬度…」） | ✅ PASS（B3 第 5 条隐患已修） |
| 音频播放 | `AVAudioSession .playback` 无需 Info.plist 权限 | ✅ PASS |
| `UIBackgroundModes=audio` | 未加 | ✅ PASS（本期不做后台播放） |
| USB-serial | iOS 本就不实现 | ✅ PASS（B3 方案 C：iOS 诚实降级内置/蓝牙 GNSS） |

---

## 3. 编译错误预扫结论

- **Android**：静态可判定项 **PASS**（通道/方法/参数/Manifest/常用 API）；
  不能静态确证的只有 2 项 → **待真机编译**：① `AudioTrack.write` 重载的 API 级别（取决于 `flutter.minSdkVersion`）；
  ② 废弃构造器在目标 SDK 下的告警是否被 `-Werror` 当错误（本工程 release 用 debug 签名、未开 `-Werror`，预期不致命）。
- **iOS**：**FAIL（疑似编译不过）** —— `FlutterImplicitEngineDelegate/Bridge` 等脚手架类型疑似不存在；
  且即便编译通过，`AudioEngineSwift` 被注释 = **无任何 channel handler**。两项都转真机/Xcode 待验。

## 4. 原生待真机清单（交真机联调）

1. Android：`flutter.minSdkVersion` 实值确认 `AudioTrack.write(byte[],int,int)` 可用；
2. Android：USB 权限 `BroadcastReceiver` 未注册 → 补注册，否则 `requestPermission` 后无法自动续 `open`；
3. Android：USB-TTL（CP2102/CH340/FTDI）`controlTransfer` 设波特率序列补全（当前 TODO 占位）；
4. Android：真机验证 ①实时流 `start/write` 出声 ②文件 `startFile` 播完 `onComplete`/进度 `onPosition` ③USB 枚举+NMEA 首帧；
5. iOS：用真实 Xcode 脚手架重定注册点（`rootViewController as! FlutterViewController`），解开并补全 `write`/`startFile` 桩；
6. iOS：真机验证实时出声 + 文件回放回调；
7. 双方：UI 在真机验证前**不得宣称**「实时收听/文件回放/USB-GNSS 已可用」。

## 5. 本轮原生改动
- **无**。未发现可在无工具链下确证的必改 bug；存疑项全部如上转真机待验，符合「不把未编译代码描述为已验证」。

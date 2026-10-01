# B3 移动原生能力缺口审计与可执行契约（只读）

> 范围：`mobile/`（根 `/home/user/Doubao/chats/38438160041798146/mobile`）。
> 红线：纯只读审计，仅产出本报告；不改代码、不 git。云 VM 无 Android/iOS 工具链，
> 所有原生（Kotlin/Swift）代码均为**待实现契约，云未编译、真机待验**，不得描述为已验证。
> 拿不准处标「推断」。所有断言带 `file:line`。审计日期 2026-10-01。

---

## 1. 现状图：PCM / 音频 / 录音 / 回放 / 定位数据流

### 1.1 音频链路（解调 → sink），真实现部分在 Dart 侧
```
rtl_tcp IqBlock
  └─ RadioController._onIqBlock            mobile/lib/services/radio_controller.dart:369
       └─ FmDemod.process(Float64x2List)   radio_controller.dart:386  (lib/dsp/demod.dart)
            └─ Float32List audio → _audioCtrl.add(audio)  radio_controller.dart:388
                 （broadcast StreamController，radio_controller.dart:179/211）
                 └─ _audioSub.listen(_onAudioFrame)  radio_controller.dart:116
                      └─ SquelchGate.process 静噪门  radio_controller.dart:398-403
                           ├─ 开门 → _sink.write(frame)        radio_controller.dart:405
                           └─ 关门 → _sink.write(_silence)     radio_controller.dart:410
```
- `_sink` 类型 `PcmSink`（抽象 `mobile/lib/audio/pcm_sink.dart:22`），构造注入，默认 `NoOpSink`（`radio_controller.dart:113`）。
- 生产注入点：`mobile/lib/main.dart:32` 建 `PlatformPcmSink()` → `main.dart:33` 注入 `RadioController(sink:)`。
- sink 生命周期：连接成功 `await _sink.start(sampleRateHz: audioSampleRateHz)`（`radio_controller.dart:281`，常量 48000 在 `:125`）；断开/dispose `radio_controller.dart:457` 与 `:543`。

### 1.2 三个 PcmSink 实现（真实现 vs 内存/空态）
| 实现 | 文件:行 | 性质 |
|---|---|---|
| `NoOpSink` | `audio/null_pcm_sink.dart:12` | **真实现但空转**：丢帧、不报错，作默认/测试注入 |
| `RecordingPcmSink` | `audio/recording_pcm_sink.dart:16` | **仅内存**：`List<Float32List> _recorded` 深拷贝逐帧（`:47`），不写盘、不出声，单测用 |
| `PlatformPcmSink` | `audio/platform_pcm_sink.dart:62` | **Dart 侧真、原生侧空**：见 1.3 |

### 1.3 平台通道 `mbdsdr/audio`（Dart 契约就绪，原生无 handler）
- 通道常量 `MethodChannel('mbdsdr/audio')`：`platform_pcm_sink.dart:66`。
- 方法表（契约注释）：`start{sampleRate,channels}` / `write{pcm:Uint8List}` / `setVolume{volume}` / `setMuted{muted}` / `dispose`，见 `platform_pcm_sink.dart:27-33`。
- Dart 侧 `write()`：未 start 直接丢（`:104`）；`floatTo16BitPcm` 量化（`:107`）；`unawaited(invokeMethod('write',{pcm}))` fire-and-forget（`:110`）。
- 量化器 `floatTo16BitPcm`（`audio/pcm_codec.dart:17`）：纯函数，Float32→有符号 16-bit **小端**，clamp 到 `[-32767,32767]`（`:33-34`），muted 整块零（`:25`）。
- **关键事实**：原生 `MainActivity.kt` 全文仅 `class MainActivity : FlutterActivity()`（`android/.../MainActivity.kt:5`），**未注册任何 MethodChannel handler**。因此真机上 `invokeMethod` 会抛 `MissingPluginException`——即"真实现出声"目前在真机上**并不出声**，Dart 侧只是把消息发向一个没有接收者的通道。`platform_pcm_sink.dart:59-61` 已诚实标注"真机待验"。

### 1.4 录音（全部为空态/就绪索引，无文件）
- 元数据值对象 `RecordingMeta`（`models/recording.dart:12`）：`startedAtEpochMs/frequencyHz/mode/note`，`toJson`（`:38`）/`fromJson`（`:46`，缺字段或 `frequencyHz<=0` 返回 null）。
- 持久化索引在 `SettingsService`（`services/settings_service.dart:379-421`）：`_recordings` 列表，`addRecording`（`:395`）/`clearRecordings`（`:404`）/`removeRecording`（`:412`，按时刻+频率定位），`_persistRecordings`（`:387`）写 SharedPreferences JSON。
- **生产代码无任何地方调用 `addRecording`**（grep 全仓仅 `settings_service.dart:395` 定义与 `recordings_page.dart`/测试读取）。故真机录音列表恒空，`recording.dart:7` 与 `recordings_page.dart:13-21` 均已诚实声明"无真实文件录制"。
- `recordings_page.dart`：读 `settings.recordings`（`:86`），空态文案"移动端暂未实现真实文件录制"（`:110`）；删除真实（`:54/:77`）；**不渲染回放按钮**（`:19-20` 注释明说无文件播放器）。

### 1.5 定位（手机内置 GNSS 已接，外部 GNSS 无）
- `services/location_service.dart:46` `GeolocatorLocationService` 基于 `geolocator`：权限/服务检查（`:68-86`）、`getCurrentPosition`+`getPositionStream`（`:89-100`），映射为 `Station(lat,lon,alt)`（`:107`）。无权限/服务关时不造假坐标（`:24`）。
- pubspec 依赖 `geolocator: ^14.1.1`（`pubspec.yaml:14`）。
- **外部 GNSS（USB 串口 NMEA）在 mobile 侧完全不存在**：无 Dart NMEA 解析、无 USB-serial 通道。

### 1.6 原生工程现状（已真读）
- Android：`MainActivity.kt` 空壳（见 1.3）；`AndroidManifest.xml` **零权限声明**（无 `RECORD_AUDIO`、无 `MODIFY_AUDIO_SETTINGS`、无 `<uses-feature android.hardware.usb.host>`），仅 launcher activity 与 flutterEmbedding meta（`AndroidManifest.xml:2-45`）。`applicationId=com.example.mbdsdr_mobile`、`minSdk=flutter.minSdkVersion`（未钉死），见 `android/app/build.gradle.kts`。
- iOS：`ios/Runner/AppDelegate.swift` 空壳 `didFinishLaunchingWithOptions` 仅 `super`（`:6-11`），**未注册任何 FlutterMethodChannel**；`Info.plist` **缺 `NSLocationWhenInUseUsageDescription`**（geolocator 在 iOS 必需，缺失会运行期崩溃/拒权限——既有潜在缺口），也无音频后台模式。

---

## 2. 三个补齐方案（文件级契约）

### 方案 A：真实录音落盘（纯 Dart，云可测）

目标：把"喂给 sink 的解调 Float32"同时写成本地 WAV + sidecar JSON，与现有 `RecordingMeta`/`RecordingPcmSink` 衔接。**全部 dart:io，无需原生，云内可单测。**

**新文件 `mobile/lib/audio/wav_writer.dart`**
- `class WavWriter`：
  - `WavWriter.create(RandomAccessFile out, {int sampleRateHz=48000, int channels=1, int bitsPerSample=16})`：先占位写 44 字节 RIFF/WAVE 头（dataSize 暂写 0），记录 `out` 与 `_dataBytes=0`。
  - `void write16BitPcm(Uint8List pcm)`：`out.writeFromSync(pcm); _dataBytes += pcm.length;`（喂入前用现有 `floatTo16BitPcm` 量化，writer 只吃 16-bit 小端字节，与 `platform_pcm_sink.dart:107` 同一路径，保证落盘与出声同格式）。
  - `Future<void> close()`：回写头——`out.setPositionSync(4)` 写 `chunkSize=36+_dataBytes`，`out.setPositionSync(40)` 写 `subchunk2Size=_dataBytes`，再 close。
- WAV 头字节契约（小端，必须自洽）：
  - `RIFF`(4) + fileLen-8 u32 + `WAVE`；
  - `fmt `(4) + `16` u32 + PCM 格式 `1` u16 + channels u16 + sampleRate u32 + byteRate=sampleRate*channels*bits/8 u32 + blockAlign=channels*bits/8 u16 + bitsPerSample=16 u16；
  - `data`(4) + dataBytes u32 + 采样数据。
  - 单声道 16-bit：byteRate=`sampleRate*2`，blockAlign=2。
- 错误/空态：`write16BitPcm` 在 `close()` 后调用抛 `StateError`；空录音（`_dataBytes==0`）允许 close，产出 44 字节合法头 dataSize=0。

**新文件 `mobile/lib/audio/file_recording_sink.dart`**
- `class FileRecordingSink implements PcmSink`（实现 `audio/pcm_sink.dart:22` 全接口）：
  - 构造 `FileRecordingSink({required Directory dir, required RecordingMeta Function() metaFactory, PcmSink? delegate})`。
  - `start(...)`：开时间戳文件名 `<epochms>_<freq>_<mode>.wav`，建 `WavWriter`；若 `delegate!=null` 同时 `delegate.start(...)`（录音不打断实时收听）。
  - `write(Float32List frame)`：`writer.write16BitPcm(floatTo16BitPcm(frame))`；若有 delegate 也 `delegate.write(frame)`（注意：与 `RecordingPcmSink` 一样**在此不应用 volume/muted**，录"干净解调音频"，音量只影响外放——与 `recording_pcm_sink.dart:14-15` 的既有约定一致）。
  - `setVolume/setMuted`：转发 delegate（若有），对落盘无影响。
  - `dispose()`：`await writer.close()`，把 `metaFactory()` 增补 `sampleCount/_dataBytes/2`、`deviceSource`，写 sidecar `<stem>.json`，返回 `Future<RecordingMeta>`（供上层 `settings.addRecording`）。

**sidecar JSON 格式**（与 `RecordingMeta.toJson` 兼容并扩展）：
```json
{ "startedAtEpochMs": 0, "frequencyHz": 0, "mode": "nfm", "note": "",
  "sampleRateHz": 48000, "channels": 1, "bitsPerSample": 16,
  "sampleCount": 0, "durationMs": 0,
  "wavFileName": "....wav", "deviceSource": "connected" }
```
- `deviceSource` 枚举字符串：`"connected"`（真实 rtl_tcp 收音）/ `"test_signal"`（内部测试源）。**无第三类**，禁止空字符串冒充。
- 向后兼容：`RecordingMeta.fromJson`（`recording.dart:46`）只读旧 4 键，新增键被忽略；建议在 `RecordingMeta` 增可选字段 `sampleCount?/deviceSource?`（改文件，扩展式、不改既有构造签名）。

**新文件 `mobile/lib/services/recording_store.dart`**
- `class RecordingStore`：依赖 `path_provider`（见 §5）。
  - `Future<Directory> recordingsDir()`：`getApplicationDocumentsDirectory()/recordings/`，不存在则 `create(recursive:true)`。
  - `Future<RecordingMeta> finalize(...)`：列目录读全部 `*.json` sidecar，`RecordingMeta.fromJson` 过滤坏项，按 `startedAtEpochMs` 倒序（与 `settings_service.dart:398` 排序一致）。
  - `Future<void> delete(RecordingMeta m)`：按 sidecar 里的 `wavFileName` 删 `.wav` 与 `.json`（配对删除；任一不存在不报错）。
- 与 `SettingsService` 衔接：**录制结束**调 `settings.addRecording(meta)`（既有方法 `settings_service.dart:395`）；列表 UI 已就绪（`recordings_page.dart:113`），无需改页。删除可二选一：继续走 `settings.removeRecording`（索引）+ `RecordingStore.delete`（文件），避免"索引删了文件还在"。

**接线改动（最小）**：`main.dart:32-33` 处把 `PlatformPcmSink` 包成 `FileRecordingSink(delegate: PlatformPcmSink())`；录音开关由后续 W2 在 `RadioApi`（`radio_controller.dart:~80`）加 `startRecording()/stopRecording()`，内部用一个可空 `_recSink` 包装。本审计不写实现，仅给契约。

---

### 方案 B：原生文件回放（云写不编译，真机待验）

复用/扩展现有通道 `mbdsdr/audio`（`platform_pcm_sink.dart:66`），**不新增大包**。新增"从文件回放"方法，与实时 `write` 流并列。

**Dart→原生方法表（在 `platform_pcm_sink.dart:27-33` 表上追加）**：
| method | 参数 | 返回 | 说明 |
|---|---|---|---|
| `startFile` | `{"path": String, "sampleRate": int, "channels": int}` | `{}` | 打开 WAV/裸 PCM 准备播放 |
| `pauseFile`/`resumeFile` | 无 | `{}` | 暂停/继续 |
| `stopFile` | 无 | `{}` | 停止并释放 |
| `filePosition` | 无 | `{"positionMs":int,"durationMs":int,"playing":bool}` | 进度查询 |
- 事件回传：原生通过 `channel.setMethodCallHandler` 或新增 `mbdsdr/audio/events` 通道向 Dart 推 `onComplete`（文件播完）/`onPosition`。**推断**：Flutter 官方单向 MethodChannel 不支持原生主动回调，需在 Dart 侧 `MethodChannel.setMethodCallHandler` 接收原生 `invokeMethod('onPosition',...)`。
- Dart 侧新类 `FilePlayer`（新文件 `mobile/lib/audio/file_player.dart`）：持有同一 `MethodChannel('mbdsdr/audio')`，封装上述方法与 `Stream<PlaybackState> onState`。

**Android Kotlin 清单（在 `MainActivity.kt` 注册；云未编译、真机待验）**
- 现状 `MainActivity.kt` 仅 5 行无注册。需在 `configureFlutterEngine(engine)` 里：
  ```kotlin
  val channel = MethodChannel(engine.dartExecutor.binaryMessenger, "mbdsdr/audio")
  channel.setMethodCallHandler { call, result -> ... }
  ```
- 实时流播放（补齐既有 `start/write/...`，契约来自 `platform_pcm_sink.dart:38-46`）：
  - `AudioTrack( AudioManager.STREAM_MUSIC, 48000, CHANNEL_OUT_MONO, ENCODING_PCM_16BIT, bufSize, MODE_STREAM )`；`start`→`play()`；`write`→`write(bytes,0,bytes.size,WRITE_BLOCKING)`；`setVolume`→`setVolume(gain)`；`dispose`→`stop()+release()`。
- 文件回放：`startFile` 读 WAV 头解析出采样率/数据偏移，建**第二个** `AudioTrack`（MODE_STREAM），开线程按块 `read` 入队；`stopFile` 停线程并 `release()`；`filePosition` 用已写字节数/采样率换算 ms。
- 注册点文件：`mobile/android/app/src/main/kotlin/com/example/mbdsdr_mobile/MainActivity.kt`。
- 权限：播放本身无需危险权限（`platform_pcm_sink.dart:46` 已述）；可选 `MODIFY_AUDIO_SETTINGS`。

**iOS Swift 清单（在 `AppDelegate.swift` 注册；云未编译、真机待验）**
- 现状 `AppDelegate.swift` 仅 `super`。需在 `didFinishLaunchingWithOptions` 取 `controller.window?.rootViewController as! FlutterViewController`，建 `FlutterMethodChannel(flutterViewController.binaryMessenger, name:"mbdsdr/audio")`。
- 实时流（契约来自 `platform_pcm_sink.dart:48-57`）：`AVAudioSession` 设 `.playback`+`setActive(true)`；`AVAudioEngine`+`AVAudioPlayerNode`，格式 `AVAudioFormat(commonFormat:.pcmFormatInt16, sampleRate:48000, channels:1, interleaved:false)`；`write` 把 `FlutterStandardTypedData` 包 `AVAudioPCMBuffer` 后 `scheduleBuffer`；`dispose`→stop node/engine、deactivate session。
- 文件回放：`startFile` 用 `AVAudioFile(forReading:)` 解析头，`playerNode.scheduleFile(...,looping:false)`，完成回调里 `invokeMethod("onComplete")`；`stopFile`→`stop(...)`；`filePosition` 读 `playerNode.lastRenderTime`。
- 注册点文件：`mobile/ios/Runner/AppDelegate.swift`。

> 以上 Kotlin/Swift 均为契约级代码清单，**云环境未编译、未链接、未真机出声**；W2 实现后须在真机验证：实时流有声、文件播完回调、进度准确。

---

### 方案 C：外部 GNSS 串口（Dart NMEA 可测；原生通道未编译）

蓝本：`mbdsdr_ai/serial_gnss.py`（自有 MIT）。Dart 侧移植解析器，云内全离线单测。

**新文件 `mobile/lib/gnss/nmea_parser.dart`（纯 Dart、零平台依赖）**
- `int nmeaChecksum(String body)`：`$` 与 `*` 之间逐字符码点 XOR（移植 `serial_gnss.py:90`）。
- 主入口(`NmeaRecord? parse(String line)`)：移植 `serial_gnss.py:148-196`：trim、必须 `$` 开头、必须含 `*`、切 `body/*HH`、hex 校验和比对、`head=fields[0]`、`talker=head[0:2]`、`stype=head[2:5]`；talker 白名单 `{GP,GL,GA,GB,BD,GN}`（移植 `:68`）；非法/坏校验和返回 null，不抛。
- 语句结果（sealed class 或带 `sentence` 字段的可识别类）：
  - `Gga`：utcTime、latitude/longitude（`_ddmmToDeg` 移植 `:101`：纬度 `ddmm.mmmm`、经度 `dddmm.mmmm`，S/W 取负）、fixQuality(0/1/2/4/5)、satellites、hdop、altitudeM（移植 `:200-216`）。
  - `Rmc`：status A/V、valid、lat/lon、speedKnots、speedKmh=knots*1.852、courseDeg、date（移植 `:220-239`）。
  - `Gsa`：mode、fixType(1/2/3)、satellitesUsed[3..15]、pdop/hdop/vdop（移植 `:243-254`）。
  - `Gsv`：**多帧聚合**——按 `(talker,totalMessages)` 缓冲，末帧合并返回完整 sats（每 4 字段 id/elev/az/snr），中间帧返回 null；超时（2s，注入时钟可测）丢弃半截缓冲（移植 `:265-321`）。北斗 PRN>32 按 int 解析。
  - `Vtg`：courseDeg、speedKnots、speedKmh（移植 `:369-382`）。
  - `Zda`：由 `hhmmss.ss`+`dd,mm,yyyy` 拼 `DateTime.utc`（移植 `:386-399`）。
  - 可选 `Gll/Gst/Txt`（移植 `:325-365`）。
- **no-fix 诚实态**：新文件 `mobile/lib/gnss/gnss_fix.dart`：
  ```dart
  class GnssFix { String source; // "none"|"real"
    double? lat,lon,altM; int? satellites; double? hdopKmh?; ... }
  ```
  融合器 `GnssFixMerger`（移植 `SerialGNSSReader._merge` `:710` 与 `get_fix` `:752`）：收到合法语句 `source="real"` 但 fixQuality=0/RMC status=V 时坐标保持 null，**绝不返回 (0,0)**；超 10s 无新语句显式退化为 `source="none"`。

**USB-serial 平台通道契约（原生未编译）**
- 新通道 `mbdsdr/usb_serial`。Dart 侧 `UsbSerialPort` 抽象（新文件 `mobile/lib/gnss/usb_serial_port.dart`）：
  - `Future<List<UsbDeviceInfo>> listDevices()`；
  - `Future<bool> open({int baud=9600})`；`Stream<Uint8List> readBytes()`；`Future<void> close()`；`Stream<bool> get hotplugState()`。
  - 实现注入便于测试：`FakeUsbSerialPort` 喂预制 NMEA 字节行。
- Android Kotlin（云未编译、真机待验）：直接用 Android USB host API（`android.hardware.usb.*`）：`UsbManager` 枚举 `getDeviceList` → `UsbDeviceConnection` → `UsbInterface`/`UsbEndpoint`(bulk IN) → `controlTransfer` 设波特率（CP2102/CH340/FTDI 各自初始化序列）→ bulk 读循环。注册点同 `MainActivity.kt`。**Manifest 须加** `<uses-feature android:name="android.hardware.usb.host"/>` 与 USB 权限 `PendingIntent` 申请。
- iOS 限制（说明，不实现）：iOS 无通用 USB host 串口直读；外接 GNSS 一般走 MFi/CoreLocation 外部配件或蓝牙 SPP/Bluetooth LE。**iOS 本期不实现 USB-serial**，`UsbSerialPort` 在 iOS 上 `listDevices()` 诚实返回空并提示"iOS 仅支持内置 GNSS / 蓝牙 GNSS"。

**内置 GNSS（geolocator）与外部 GNSS 的 UI 分工**
- 内置 `GeolocatorLocationService`（`location_service.dart:46`）：负责**测站经纬度**（对星/AR 指向用），中等精度即可，保留现状。
- 外部 USB GNSS：负责**授时（ZDA/RMC 日期）+ 高精度定位 + 天空图 GSV/GSA**（卫星 SNR/仰角，内置 GNSS 不暴露）。
- UI 分层：`Station` 坐标优先用外部 fix（若 `source=="real"`），否则回落内置；天空图页用外部 GSV/GSA；状态栏显示定位源标签「内置/USB-GNSS/无」。**推断**：具体落点在 `sky_page.dart`/`location_service` 上层，本审计不改动。

---

## 3. 测试清单（Dart 侧确定性离线，云内可跑）

WAV / sidecar / store：
- WAV 头魔数 `RIFF`/`WAVE`/`fmt `/`data` 正确；单声道 16-bit 下 byteRate=sampleRate*2、blockAlign=2（对照 `pcm_sink_test.dart` 既有小端读法 `:9-10`）。
- 字节自洽：`RIFF chunkSize == 36 + dataBytes`、`data chunkSize == 实际写样本数*2`；回写后用 `setPosition` 重读断言。
- 已知样本落盘：喂 `[1.0,-1.0,0.0]`，文件 body 字节应为 `FF 7F 01 80 00 00`（复用 `pcm_sink_test.dart:58-68` 的已知向量）。
- sidecar JSON round-trip：写→读→`RecordingMeta.fromJson` 还原；缺字段/非法 frequency 被丢弃（`recording.dart:46` 既有语义）。
- `RecordingStore`：用 `Directory.systemTemp`（云可写）建临时目录，测列目录倒序、配对删除 wav+json、坏 sidecar 跳过。
- `FileRecordingSink`：不应用 volume/muted（录干净信号）；delegate 转发正确性；dispose 后再 write 抛 StateError。
- 回放通道 mock：用 `TestDefaultBinaryMessenger` 拦截 `mbdsdr/audio` 的 `startFile/filePosition/stopFile` invoke，断言参数 map 与事件顺序（`flutter_test` 内置，无需真机）。

NMEA（移植 `tests/test_serial_gnss.py` 用例，校验和一律 `nmeaChecksum` 动态生成、不手写）：
- GGA：lat=43+52/60、lon=125+19/60、satellites=9、fixQuality=1（移植 `:954-958`）。
- 坏校验和、无 `*`、非 `$` 开头、hex 非法 → 全返回 null。
- RMC 南纬 S/西经 W 取负、speedKmh=knots*1.852、status=V→valid=false。
- GSA fixType/satellitesUsed/pdop-hdop-vdop 字段位；VTG  course/speed；ZDA 拼出 UTC DateTime。
- GSV 多帧：中间帧 null、末帧聚合 sats 数、按 talker 分组（GB 北斗 PRN 211>32）、超时丢弃半截（注入假时钟，移植 `:1036-1050`）。
- 多星座 talker：GP/GL/GA/GB/BD/GN 全收，未知 talker 拒。
- no-fix：GGA fixQuality=0 → merger `source="real"` 但 lat/lon 为 null；10s 无语句 → `source="none"`（移植 `:1063-1068`）。

**原生不可测部分的诚实标注模板**（写入对应 W2 实现文件头）：
```
// [云未编译·真机待验] 本文件 Kotlin/Swift 由 B3 契约生成，云 VM 无 Android/iOS
// 工具链，未编译、未链接、未真机运行。待真机：①实时流出声 ②文件播完回调
// ③USB 枚举与 NMEA 首帧。未在真机验证前不得在 UI 宣称"已可用"。
```

---

## 4. 权限与 pubspec 变更建议（最小化）

**pubspec 新增（仅 1 个，成熟度评估）**
- `path_provider`（^2.x，Flutter 官方维护、成熟稳定）：方案 A 的应用文档目录**硬依赖**，当前 `pubspec.yaml` 无任何目录包。这是最小且必需的新增。
- USB-serial：**优先原生自写通道（方案 C）**，不引入 `flutter_libserialport`/`usb_serial` 包——前者需 NDK 编译（云不可测），后者多为 0.x 低成熟（与 `platform_pcm_sink.dart:14-21` 弃用 `flutter_pcm_player` 同一理由）。回放亦不引入播放包，复用自有 `mbdsdr/audio` 通道。
- **推断**：若 W2 团队评估 USB host 工作量过大，可退而评估 `flutter_libserialport`，但须接受"云不可编译"成本，与本审计红线一致。

**Android（AndroidManifest.xml，当前零权限）**
- 回放/播放：无新增必要权限（`platform_pcm_sink.dart:46`）。
- USB host：加 `<uses-feature android:name="android.hardware.usb.host" android:required="false"/>` + 运行时 USB 权限弹窗（无需普通 `<uses-permission>`）。
- 不申请 `RECORD_AUDIO`（本应用不录音进麦克风，只解调后写盘）。

**iOS（Info.plist，当前缺定位描述）**
- **既有缺口必修**：补 `NSLocationWhenInUseUsageDescription`（geolocator 在 iOS 缺此会直接崩溃/无法授权）——与本次三方案无关，但属真读发现的原生隐患。
- 回放：`AVAudioSession .playback` 无需 Info.plist 权限；若需后台播放再议 `UIBackgroundModes=audio`（本期不加）。
- USB-serial：iOS 不实现，无对应描述。

---

## 5. 五条最关键发现
1. **原生通道是"空管道"**：`mbdsdr/audio` 的 Dart 契约完整（`platform_pcm_sink.dart:27-33,66`），但 `MainActivity.kt:5` 与 `AppDelegate.swift` 均空壳、零 MethodChannel 注册——真机上连"实时收听出声"都尚未发生，不是"待优化"而是"未接线"。
2. **录音链路完全无文件落点**：`RecordingPcmSink` 只进内存（`recording_pcm_sink.dart:17,47`），生产无人调 `addRecording`（`settings_service.dart:395`），列表恒空是**有意的诚实空态**；方案 A 用纯 Dart WAV writer 即可在云内闭环，无需原生。
3. **缺 `path_provider` 是落盘前置硬依赖**：当前 `pubspec.yaml` 无任何目录包，"应用文档目录"无法获得；这是方案 A 唯一必需的新增成熟包。
4. **iOS Info.plist 缺定位权限描述**：`location_service.dart` 已接 `geolocator`，但 `Info.plist` 无 `NSLocationWhenInUseUsageDescription`——真机定位一调用即可能被系统拒/崩，属既有原生隐患（非本次范围但须修）。
5. **外部 GNSS 在 mobile 是从零**：Dart 侧无 NMEA、无 USB-serial；`serial_gnss.py`（GGA/RMC/GSA/GSV/VTG/ZDA+多星座+校验和）是可直接移植的自有蓝本，Dart 解析器云内可全测；Android 可走 USB host API、iOS 无通用 USB 串口须诚实降级为"内置/蓝牙 GNSS"。

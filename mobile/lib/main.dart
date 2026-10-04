import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app/home_shell.dart';
import 'app/theme.dart';
import 'audio/file_player.dart';
import 'audio/platform_pcm_sink.dart';
import 'services/radio_controller.dart';
import 'services/recording_store.dart';
import 'services/settings_service.dart';

// ============================================================================
// MBDSDR 移动端入口
//   * 启动：先初始化绑定 → SharedPreferences 取普通 KV → 读设置 → 建射频控制器；
//   * 提供者：SettingsService / RadioController 两个 ChangeNotifier；
//   * 音频：PlatformPcmSink（MethodChannel mbdsdr/audio）注入 RadioController，
//     由控制器在 connect 成功后 start、断开/dispose 时释放；
//   * 会话恢复：启动时把上次频率/模式/音量/静音通过 applySession 灌给控制器；
//   * 唯一主题：深色「默认」。
// ============================================================================

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final SharedPreferences prefs = await SharedPreferences.getInstance();
  final SettingsService settings = SettingsService(
    kv: SharedPreferencesKvStore(prefs),
    secure: FlutterSecureStorageStore(),
  );
  await settings.load();

  // 真机出声 sink：MethodChannel → 原生 AudioTrack/AVAudioEngine（原生端待验）。
  final PlatformPcmSink pcmSink = PlatformPcmSink();
  // 录音文件库（应用文档目录/recordings/）与文件回放器（复用 mbdsdr/audio 通道）。
  final RecordingStore recordingStore = RecordingStore();
  final FilePlayer filePlayer = FilePlayer();
  final RadioController radioController = RadioController(
    sink: pcmSink,
    recordingsDirProvider: () => recordingStore.recordingsDir(),
  );

  // 录制结束：把真实落盘的 .wav 元数据加进录音列表索引（真实录制才触发）。
  radioController.onRecordingFinalized = (meta) =>
      settings.addRecording(meta);

  // 真实信号活动：静噪门开门（真实解调音频 RMS 过门限/出声）即记一条活动日志。
  // 数据全部来自真实控制器回读（频率/模式/电平），无连接/无观察时不产生条目。
  // 真实信号活动：静噪门开门 / 范围扫描命中，把真实观察写进活动日志。
  // 数据全部来自真实控制器回读（频率/模式/电平/来源），无连接/无观察时不产生条目。
  radioController.onSignalActivity = ({
    required int frequencyHz,
    required String mode,
    required double levelDbfs,
    String source = 'squelch',
  }) {
    settings.addActivity(
      frequencyHz: frequencyHz,
      mode: mode,
      levelDbfs: levelDbfs,
      source: source,
    );
  };

  // connect 之前恢复上次的调谐与音频设置。
  await radioController.applySession(
    freqHz: settings.lastFreqHz,
    mode: settings.demodModeEnum,
    volume: settings.volume,
    muted: settings.muted,
  );

  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider<SettingsService>.value(value: settings),
        ChangeNotifierProvider<RadioController>.value(value: radioController),
        Provider<RecordingStore>.value(value: recordingStore),
        Provider<FilePlayer>.value(value: filePlayer),
      ],
      child: const MbdsdrApp(),
    ),
  );
}

class MbdsdrApp extends StatelessWidget {
  const MbdsdrApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'MBDSDR',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.data,
      home: const HomeShell(),
    );
  }
}

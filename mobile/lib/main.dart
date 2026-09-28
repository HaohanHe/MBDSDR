import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app/home_shell.dart';
import 'app/theme.dart';
import 'audio/platform_pcm_sink.dart';
import 'services/radio_controller.dart';
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
  final RadioController radioController = RadioController(sink: pcmSink);

  // connect 之前恢复上次的调谐与音频设置。
  await radioController.applySession(
    freqHz: settings.lastFreqHz,
    mode: settings.demodModeEnum,
    volume: settings.volume,
    muted: settings.muted,
  );

  runApp(
    MultiProvider(
      providers: <ChangeNotifierProvider<ChangeNotifier>>[
        ChangeNotifierProvider<SettingsService>.value(value: settings),
        ChangeNotifierProvider<RadioController>.value(value: radioController),
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

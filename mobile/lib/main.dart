import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app/home_shell.dart';
import 'app/theme.dart';
import 'services/radio_controller.dart';
import 'services/settings_service.dart';

// ============================================================================
// MBDSDR 移动端入口
//   * 启动：先初始化绑定 → SharedPreferences 取普通 KV → 读设置 → 建射频控制器；
//   * 提供者：SettingsService / RadioController 两个 ChangeNotifier；
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

  final RadioController radioController = RadioController();

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

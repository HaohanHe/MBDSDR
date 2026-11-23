import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../app/tokens.dart';
import '../l10n/gen/app_localizations.dart';
import '../services/device_gateway_client.dart';
import '../services/radio_controller.dart';
import '../services/settings_service.dart';
import '../widgets/device_info_card.dart';
import '../widgets/timing_panel.dart';

// ============================================================================
// 设置页：rtl_tcp / 桌面 ControlHub / 设备信息 / 时间与授时 / 音频 /
//        AI 助手 / 本站位置 / 语言 / 外观
// 保存即时生效（失焦提交即写回 SettingsService），不硬编码任何坐标或 key。
// 文案全部走 gen-l10n（AppLocalizations）；语言选择写 uiLocaleCode 即持久化。
// ============================================================================

class SettingsPage extends StatefulWidget {
  const SettingsPage({
    required this.settings,
    required this.radio,
    this.gatewayClientBuilder,
    super.key,
  });

  final SettingsService settings;

  /// 收音机接口：用于「设备信息」分区真实回读连接态/采样率/频率。
  final RadioApi radio;

  /// 多设备网关客户端工厂（host, port）→ 客户端。
  ///
  /// 默认指向真实桌面网关；测试注入 fake 传输即可离线验证配对/全景路径。
  final DeviceGatewayClient Function(String host, int port)? gatewayClientBuilder;

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late final TextEditingController _hostCtrl;
  late final TextEditingController _portCtrl;
  late final TextEditingController _chHostCtrl;
  late final TextEditingController _chPortCtrl;
  late final TextEditingController _keyCtrl;
  late final TextEditingController _modelCtrl;
  late final TextEditingController _latCtrl;
  late final TextEditingController _lonCtrl;
  late final TextEditingController _altCtrl;
  late final TextEditingController _gwPortCtrl;
  late final TextEditingController _gwNameCtrl;
  late final TextEditingController _gwCodeCtrl;

  bool _obscureKey = true;
  bool _keyMasked = true;
  bool _locating = false;

  /// 网关配对/全景请求在途：禁用按钮，避免重复点击打爆失败锁定计数。
  bool _gwBusy = false;

  /// 最近一次取回的全景摘要（null = 还没取过）。
  String? _gwPanoramaText;

  /// 最近一次取回的名册摘要（null = 还没取过）。
  String? _gwRosterText;

  String get _apiKey => widget.settings.apiKey;

  static String _masked(String key) {
    if (key.isEmpty) return '';
    final tail = key.length <= 4 ? key : key.substring(key.length - 4);
    return '••••$tail';
  }

  @override
  void initState() {
    super.initState();
    final SettingsService s = widget.settings;
    _hostCtrl = TextEditingController(text: s.rtlHost);
    _portCtrl = TextEditingController(text: '${s.rtlPort}');
    _chHostCtrl = TextEditingController(text: s.controlHubHost);
    _chPortCtrl = TextEditingController(text: '${s.controlHubPort}');
    _keyCtrl = TextEditingController(text: _masked(s.apiKey));
    _modelCtrl = TextEditingController(text: s.apiModel);
    _latCtrl = TextEditingController(text: _d2s(s.stationLat));
    _lonCtrl = TextEditingController(text: _d2s(s.stationLon));
    _altCtrl = TextEditingController(text: _d2s(s.stationAlt));
    _gwPortCtrl = TextEditingController(text: '${s.deviceGatewayPort}');
    _gwNameCtrl = TextEditingController(text: s.deviceGatewayName);
    _gwCodeCtrl = TextEditingController();
  }

  @override
  void dispose() {
    _hostCtrl.dispose();
    _portCtrl.dispose();
    _chHostCtrl.dispose();
    _chPortCtrl.dispose();
    _keyCtrl.dispose();
    _modelCtrl.dispose();
    _latCtrl.dispose();
    _lonCtrl.dispose();
    _altCtrl.dispose();
    _gwPortCtrl.dispose();
    _gwNameCtrl.dispose();
    _gwCodeCtrl.dispose();
    super.dispose();
  }

  static String _d2s(double? v) => v == null ? '' : _trimDouble(v);

  static String _trimDouble(double v) {
    final String s = v.toString();
    return s.contains('.') ? s.replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '') : s;
  }

  void _toast(String message, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: error ? AppTokens.danger : AppTokens.bgBar,
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  // ------------------------------------------------------------ 各字段保存
  void _saveHost() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    widget.settings.rtlHost = _hostCtrl.text;
    _toast(l10n.toastHostSaved);
  }

  void _savePort() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    final int? port = int.tryParse(_portCtrl.text.trim());
    if (port == null) {
      _toast(l10n.toastPortInvalid, error: true);
      return;
    }
    try {
      widget.settings.rtlPort = port;
      _toast(l10n.toastPortSaved);
    } on FormatException catch (e) {
      _toast(e.message, error: true);
    }
  }

  void _saveControlHubHost() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    widget.settings.controlHubHost = _chHostCtrl.text;
    _toast(l10n.toastChHostSaved);
  }

  void _saveControlHubPort() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    final int? port = int.tryParse(_chPortCtrl.text.trim());
    if (port == null) {
      _toast(l10n.toastPortInvalid, error: true);
      return;
    }
    try {
      widget.settings.controlHubPort = port;
      _toast(l10n.toastChPortSaved);
    } on FormatException catch (e) {
      _toast(e.message, error: true);
    }
  }

  // 输入即静默落盘（不弹 toast）：Android 返回键收起键盘不会触发
  // onSubmitted / onEditingComplete，只在提交时保存会丢掉刚输入的地址
  // （实测缺陷：填完按返回键，频谱页仍显示「设置 rtl_tcp 地址」）。
  // 显式提交仍走 _saveX 给 toast 反馈；这里值没变就什么都不做。
  void _commitHost() {
    final String v = _hostCtrl.text.trim();
    if (v != widget.settings.rtlHost) widget.settings.rtlHost = v;
  }

  void _commitPort() {
    final int? port = int.tryParse(_portCtrl.text.trim());
    // 越界/半截输入不落盘（setter 会抛 FormatException），等用户改完整。
    if (port == null || port < 1 || port > 65535) return;
    if (port != widget.settings.rtlPort) widget.settings.rtlPort = port;
  }

  void _commitControlHubHost() {
    final String v = _chHostCtrl.text.trim();
    if (v != widget.settings.controlHubHost) widget.settings.controlHubHost = v;
  }

  void _commitControlHubPort() {
    final int? port = int.tryParse(_chPortCtrl.text.trim());
    if (port == null || port < 1 || port > 65535) return;
    if (port != widget.settings.controlHubPort) widget.settings.controlHubPort = port;
  }

  // ------------------------------------------------------------ 多设备网关
  /// 角色短标签（页面内文案，全部走 l10n）；未知角色原样显示，不硬塞成手持。
  String _roleLabel(AppLocalizations l10n, String role) {
    return switch (role) {
      'lens' => l10n.settingsGatewayRoleLens,
      'eyepiece' => l10n.settingsGatewayRoleEyepiece,
      'handheld' => l10n.settingsGatewayRoleHandheld,
      'headset' => l10n.settingsGatewayRoleHeadset,
      _ => role,
    };
  }

  /// 端口即输即存；越界/半截先不落盘。
  void _commitGatewayPort() {
    final int? port = int.tryParse(_gwPortCtrl.text.trim());
    if (port == null || port < 1 || port > 65535) return;
    if (port != widget.settings.deviceGatewayPort) {
      widget.settings.deviceGatewayPort = port;
    }
  }

  void _saveGatewayPort() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    final int? port = int.tryParse(_gwPortCtrl.text.trim());
    if (port == null) {
      _toast(l10n.toastPortInvalid, error: true);
      return;
    }
    try {
      widget.settings.deviceGatewayPort = port;
      _toast(l10n.toastPortSaved);
    } on FormatException catch (e) {
      _toast(e.message, error: true);
    }
  }

  DeviceGatewayClient _gatewayClient() {
    final String host = widget.settings.controlHubHost.trim();
    final SettingsService s = widget.settings;
    final DeviceGatewayClient Function(String, int)? builder =
        widget.gatewayClientBuilder;
    return builder != null
        ? builder(host, s.deviceGatewayPort)
        : DeviceGatewayClient(host: host, port: s.deviceGatewayPort);
  }

  /// 用配对码把本机绑定为所选角色；成功即把 token 落安全存储。
  Future<void> _pairGateway() async {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    if (_gwBusy) return;
    final SettingsService s = widget.settings;
    if (s.controlHubHost.trim().isEmpty) {
      _toast(l10n.settingsGatewayHostMissing, error: true);
      return;
    }
    final String name = _gwNameCtrl.text.trim();
    if (name.isEmpty) {
      _toast(l10n.toastGatewayNameRequired, error: true);
      return;
    }
    final String code = _gwCodeCtrl.text.trim();
    if (!RegExp(r'^\d{6}$').hasMatch(code)) {
      _toast(l10n.toastGatewayCodeInvalid, error: true);
      return;
    }
    setState(() => _gwBusy = true);
    try {
      final Map<String, dynamic> out = await _gatewayClient().pair(
        code: code,
        deviceName: name,
        role: s.deviceGatewayRole,
      );
      final Object? token = out['token'];
      final Object? device = out['device'];
      final String deviceId =
          device is Map ? '${device['id'] ?? ''}'.trim() : '';
      s.deviceGatewayName = name;
      s.deviceGatewayToken = token is String ? token : '';
      s.deviceGatewayDeviceId = deviceId;
      _gwCodeCtrl.clear();
      if (!mounted) return;
      _toast(l10n.toastGatewayPaired);
    } on DeviceGatewayException catch (e) {
      if (!mounted) return;
      _toast(l10n.toastGatewayPairFailed(e.message), error: true);
    } finally {
      if (mounted) setState(() => _gwBusy = false);
    }
  }

  /// 解除配对：清掉本机 token（桌面名册侧由权威端自行清理）。
  void _unpairGateway() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    widget.settings.deviceGatewayToken = '';
    setState(() {
      _gwPanoramaText = null;
      _gwRosterText = null;
    });
    _toast(l10n.toastGatewayUnpaired);
  }

  /// 取回权威端名册：只显示角色/设备名，不展示任何凭据。
  Future<void> _fetchRoster() async {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    if (_gwBusy) return;
    final SettingsService s = widget.settings;
    if (s.controlHubHost.trim().isEmpty) {
      _toast(l10n.settingsGatewayHostMissing, error: true);
      return;
    }
    setState(() => _gwBusy = true);
    try {
      final Map<String, dynamic> data =
          await _gatewayClient().roster(s.deviceGatewayToken);
      final Object? rawDevices = data['devices'];
      final List<Object?> devices =
          rawDevices is List ? rawDevices : const <Object?>[];
      String text;
      if (devices.isEmpty) {
        text = l10n.settingsGatewayRosterEmpty;
      } else {
        final List<String> items = <String>[];
        for (final Object? raw in devices) {
          if (raw is! Map) continue;
          final String role = _roleLabel(l10n, '${raw['role'] ?? ''}');
          final String name = '${raw['name'] ?? ''}'.trim();
          items.add(name.isEmpty ? role : '$role · $name');
        }
        text = l10n.settingsGatewayRosterSummary(
            '${devices.length}', items.join('，'));
      }
      if (!mounted) return;
      setState(() => _gwRosterText = text);
    } on DeviceGatewayException catch (e) {
      if (!mounted) return;
      _toast(l10n.toastGatewayPairFailed(e.message), error: true);
    } finally {
      if (mounted) setState(() => _gwBusy = false);
    }
  }

  /// 取回全景摘要：站址 / 太阳高度 / 缺项如实展示，缺项不猜。
  Future<void> _fetchPanorama() async {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    if (_gwBusy) return;
    final SettingsService s = widget.settings;
    if (s.controlHubHost.trim().isEmpty) {
      _toast(l10n.settingsGatewayHostMissing, error: true);
      return;
    }
    setState(() => _gwBusy = true);
    try {
      final Map<String, dynamic> data =
          await _gatewayClient().panorama(s.deviceGatewayToken);
      String siteText = '—';
      final Object? site = data['site'];
      if (site is Map) {
        final Object? lat = site['lat_deg'];
        final Object? lon = site['lon_deg'];
        if (lat is num && lon is num) {
          siteText =
              '${lat.toStringAsFixed(2)}, ${lon.toStringAsFixed(2)}';
        }
      }
      String sunAlt = '—';
      final Object? sun = data['sun'];
      if (sun is Map && sun['alt_deg'] is num) {
        sunAlt = (sun['alt_deg'] as num).toStringAsFixed(1);
      }
      final Object? missingRaw = data['missing'];
      final List<String> missing = missingRaw is List
          ? missingRaw.map((Object? e) => e.toString()).toList()
          : const <String>[];
      final String summary = l10n.settingsGatewayPanoramaSummary(
        siteText,
        sunAlt,
        missing.isEmpty ? '—' : missing.join(', '),
      );
      if (!mounted) return;
      setState(() => _gwPanoramaText = summary);
    } on DeviceGatewayException catch (e) {
      if (!mounted) return;
      _toast(l10n.toastGatewayPairFailed(e.message), error: true);
    } finally {
      if (mounted) setState(() => _gwBusy = false);
    }
  }

  void _saveApiKey() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    final String value = _keyMasked ? _apiKey : _keyCtrl.text.trim();
    widget.settings.apiKey = value;
    setState(() {
      _keyMasked = true;
      _obscureKey = true;
      _keyCtrl.text = _masked(value);
    });
    _toast(value.isEmpty ? l10n.toastApiKeyCleared : l10n.toastApiKeySaved);
  }

  void _saveModel() {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    widget.settings.apiModel = _modelCtrl.text.trim();
    _toast(l10n.toastModelSaved);
  }

  void _saveCoordinate(TextEditingController ctrl, void Function(double?) set) {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    final String raw = ctrl.text.trim();
    if (raw.isEmpty) {
      set(null);
      _toast(l10n.toastCoordCleared);
      return;
    }
    final double? v = double.tryParse(raw);
    if (v == null) {
      _toast(l10n.toastCoordInvalid(raw), error: true);
      return;
    }
    set(v);
    _toast(l10n.toastCoordSaved);
  }

  /// 输入即静默落盘：不按键盘「完成」、直接切走也不丢坐标。
  ///
  /// 空串/半截输入（如「-」「40.」）先不写，也不弹 toast；等用户输入完整
  /// 数字后再落盘。清空仍走 [_saveCoordinate]（提交时空值 = 清除坐标）。
  void _saveCoordinateSilent(
      TextEditingController ctrl, void Function(double?) set) {
    final String raw = ctrl.text.trim();
    if (raw.isEmpty) return;
    final double? v = double.tryParse(raw);
    if (v == null) return;
    set(v);
  }

  // ------------------------------------------------------------ 自动定位
  Future<void> _locate() async {
    // 文案在异步间隙之前取定（use_build_context_synchronously 口径）。
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    setState(() => _locating = true);
    try {
      final bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
      if (!serviceEnabled) {
        _toast(l10n.toastLocationServiceOff, error: true);
        return;
      }
      LocationPermission permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        _toast(l10n.toastLocationDenied, error: true);
        return;
      }
      final Position pos = await Geolocator.getCurrentPosition();
      if (!mounted) return;
      widget.settings.stationLat = pos.latitude;
      widget.settings.stationLon = pos.longitude;
      widget.settings.stationAlt = pos.altitude;
      _latCtrl.text = _d2s(pos.latitude);
      _lonCtrl.text = _d2s(pos.longitude);
      _altCtrl.text = _d2s(pos.altitude);
      _toast(l10n.toastLocationSaved);
    } catch (e) {
      _toast(l10n.toastLocationFailed(e.toString()), error: true);
    } finally {
      if (mounted) setState(() => _locating = false);
    }
  }

  // ------------------------------------------------------------ 音频
  Widget _audioRow(SettingsService s) {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    final bool muted = s.muted;
    final double shown = muted ? 0.0 : s.volume;
    return Row(
      children: <Widget>[
        IconButton(
          tooltip: muted ? l10n.settingsUnmute : l10n.settingsMute,
          icon: Icon(
            muted || s.volume == 0
                ? Icons.volume_off_outlined
                : Icons.volume_up_outlined,
          ),
          onPressed: () => setState(() => s.muted = !muted),
        ),
        Expanded(
          child: Slider(
            value: shown,
            min: 0,
            max: 1,
            divisions: 20,
            label: '${(shown * 100).round()}%',
            onChanged: (double v) {
              setState(() {
                if (muted && v > 0) s.muted = false;
                s.volume = v;
              });
            },
          ),
        ),
        // 音量百分比定宽列：3 位数字右对齐所需宽度（≥ touchMin 的读位列，
        // 取触控最小宽 token 派生，禁裸写 44）。
        SizedBox(
          width: AppTokens.touchMin,
          child: Text(
            '${(shown * 100).round()}%',
            style: AppTokens.auxiliary,
            textAlign: TextAlign.right,
          ),
        ),
      ],
    );
  }

  Widget _muteSwitch(SettingsService s) {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    return Row(
      children: <Widget>[
        Expanded(
          child: Text(l10n.settingsMute, style: AppTokens.body),
        ),
        Switch(
          value: s.muted,
          onChanged: (bool v) => setState(() => s.muted = v),
        ),
      ],
    );
  }

  // ------------------------------------------------- 本站位置摘要 / 语言名
  /// 本站位置生效摘要（坐标方向后缀在本地拼好，交给文案模板排版）。
  String _coordinateText(AppLocalizations l10n) {
    final SettingsService s = widget.settings;
    if (!s.hasManualStation) {
      return l10n.settingsCoordUnset;
    }
    final String latDir = s.stationLat! >= 0 ? 'N' : 'S';
    final String lonDir = s.stationLon! >= 0 ? 'E' : 'W';
    return l10n.settingsCoordActive(
      '${s.stationLat!.abs().toStringAsFixed(4)}°$latDir',
      '${s.stationLon!.abs().toStringAsFixed(4)}°$lonDir',
      _trimDouble(s.stationAlt!),
    );
  }

  /// 语言名按「各语言自称」显示（简体中文 / English / 日本語），不随 UI 语言翻译。
  String _languageName(AppLocalizations l10n, String code) {
    return switch (code) {
      'zh' => l10n.languageNameZh,
      'en' => l10n.languageNameEn,
      'ja' => l10n.languageNameJa,
      _ => code,
    };
  }

  Widget _section({required String title, required List<Widget> children}) {
    return Container(
      margin: const EdgeInsets.symmetric(
        horizontal: AppTokens.spacingL,
        vertical: AppTokens.spacingM,
      ),
      padding: const EdgeInsets.all(AppTokens.spacingL),
      decoration: AppTokens.cardDecoration(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: AppTokens.sectionTitle),
          const SizedBox(height: AppTokens.spacingM),
          ...children,
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final AppLocalizations l10n = AppLocalizations.of(context)!;
    return ListView(
      padding: const EdgeInsets.symmetric(vertical: AppTokens.spacingL),
      children: <Widget>[
        // ---------------------------------------------------- rtl_tcp
        _section(
          title: l10n.settingsSectionRtlTcp,
          children: <Widget>[
            TextField(
              controller: _hostCtrl,
              decoration: InputDecoration(
                labelText: l10n.settingsHostLabel,
                hintText: l10n.settingsHostHint,
                prefixIcon: const Icon(Icons.lan_outlined),
              ),
              keyboardType: TextInputType.url,
              onChanged: (_) => _commitHost(),
              onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
              onEditingComplete: _saveHost,
              onSubmitted: (_) => _saveHost(),
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _portCtrl,
              decoration: InputDecoration(
                labelText: l10n.settingsPortLabel,
                hintText: '1234',
                prefixIcon: const Icon(Icons.numbers),
              ),
              keyboardType: TextInputType.number,
              onChanged: (_) => _commitPort(),
              onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
              onEditingComplete: _savePort,
              onSubmitted: (_) => _savePort(),
            ),
            const SizedBox(height: AppTokens.spacingS),
            Text(
              l10n.settingsRtlRunHint,
              style: AppTokens.auxiliary,
            ),
          ],
        ),

        // ---------------------------------------------------- 桌面 ControlHub（远程只读解码）
        // 与本机 rtl_tcp 相互独立：移动端只读查看桌面引擎的 POCSAG/M17/VOR 解码结果。
        // 默认 127.0.0.1:50732（与桌面回环端点一致，本机查看器开箱可达）；
        // 主机留空 = 不启用，频谱页远程解码面板退化为「去设置」空态，不假连。
        _section(
          title: l10n.settingsSectionControlHub,
          children: <Widget>[
            TextField(
              controller: _chHostCtrl,
              decoration: InputDecoration(
                labelText: l10n.settingsHostLabel,
                hintText: l10n.settingsChHostHint,
                prefixIcon: const Icon(Icons.dns_outlined),
              ),
              keyboardType: TextInputType.url,
              onChanged: (_) => _commitControlHubHost(),
              onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
              onEditingComplete: _saveControlHubHost,
              onSubmitted: (_) => _saveControlHubHost(),
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _chPortCtrl,
              decoration: InputDecoration(
                labelText: l10n.settingsPortLabel,
                hintText: l10n.settingsChPortHint,
                prefixIcon: const Icon(Icons.numbers),
              ),
              keyboardType: TextInputType.number,
              onChanged: (_) => _commitControlHubPort(),
              onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
              onEditingComplete: _saveControlHubPort,
              onSubmitted: (_) => _saveControlHubPort(),
            ),
            const SizedBox(height: AppTokens.spacingS),
            Text(
              l10n.settingsChHelp,
              style: AppTokens.auxiliary,
            ),
          ],
        ),

        // ---------------------------------------------------- 多设备网关（配对）
        // 桌面「多设备网关」的客户端：只读名册/全景，配对后按角色授权。
        // 主机与 ControlHub 分区共用（同一台桌面机），本机不发调谐/录音/改站址。
        _section(
          title: l10n.settingsSectionDeviceGateway,
          children: <Widget>[
            Text(
              widget.settings.controlHubHost.trim().isEmpty
                  ? l10n.settingsGatewayHostMissing
                  : l10n.settingsGatewayEndpoint(
                      '${widget.settings.controlHubHost.trim()}:'
                      '${widget.settings.deviceGatewayPort}'),
              style: AppTokens.auxiliary,
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _gwPortCtrl,
              decoration: InputDecoration(
                labelText: l10n.settingsPortLabel,
                hintText: l10n.settingsGatewayPortHint,
                prefixIcon: const Icon(Icons.settings_ethernet),
              ),
              keyboardType: TextInputType.number,
              onChanged: (_) => _commitGatewayPort(),
              onTapOutside: (_) =>
                  FocusManager.instance.primaryFocus?.unfocus(),
              onEditingComplete: _saveGatewayPort,
              onSubmitted: (_) => _saveGatewayPort(),
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _gwNameCtrl,
              decoration: InputDecoration(
                labelText: l10n.settingsGatewayDeviceName,
                hintText: l10n.settingsGatewayDeviceNameHint,
                prefixIcon: const Icon(Icons.badge_outlined),
              ),
              onChanged: (String v) {
                if (v.trim() != widget.settings.deviceGatewayName) {
                  widget.settings.deviceGatewayName = v;
                }
              },
              onTapOutside: (_) =>
                  FocusManager.instance.primaryFocus?.unfocus(),
            ),
            const SizedBox(height: AppTokens.spacingM),
            InputDecorator(
              decoration: InputDecoration(
                labelText: l10n.settingsGatewayRole,
                prefixIcon: const Icon(Icons.assignment_ind_outlined),
              ),
              child: DropdownButtonHideUnderline(
                child: DropdownButton<String>(
                  isExpanded: true,
                  value: widget.settings.deviceGatewayRole,
                  items: kDeviceRoles
                      .map((String role) => DropdownMenuItem<String>(
                            value: role,
                            child: Text(_roleLabel(l10n, role)),
                          ))
                      .toList(),
                  onChanged: (String? v) {
                    if (v == null) return;
                    setState(() => widget.settings.deviceGatewayRole = v);
                  },
                ),
              ),
            ),
            const SizedBox(height: AppTokens.spacingM),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Expanded(
                  child: TextField(
                    controller: _gwCodeCtrl,
                    decoration: InputDecoration(
                      labelText: l10n.settingsGatewayCode,
                      prefixIcon: const Icon(Icons.pin_outlined),
                    ),
                    keyboardType: TextInputType.number,
                    maxLength: 6,
                    onTapOutside: (_) =>
                        FocusManager.instance.primaryFocus?.unfocus(),
                  ),
                ),
                const SizedBox(width: AppTokens.spacingS),
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: FilledButton(
                    onPressed: _gwBusy ? null : _pairGateway,
                    child: Text(l10n.settingsGatewayPair),
                  ),
                ),
              ],
            ),
            if (widget.settings.deviceGatewayPaired) ...<Widget>[
              Wrap(
                spacing: AppTokens.spacingS,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: <Widget>[
                  Text(
                    l10n.settingsGatewayPaired(
                        _roleLabel(l10n, widget.settings.deviceGatewayRole)),
                    style: AppTokens.auxiliary,
                  ),
                  TextButton(
                    onPressed: _gwBusy ? null : _unpairGateway,
                    child: Text(l10n.settingsGatewayUnpair),
                  ),
                  OutlinedButton(
                    onPressed: _gwBusy ? null : _fetchPanorama,
                    child: Text(l10n.settingsGatewayPanorama),
                  ),
                  OutlinedButton(
                    onPressed: _gwBusy ? null : _fetchRoster,
                    child: Text(l10n.settingsGatewayRoster),
                  ),
                ],
              ),
              if (_gwPanoramaText != null)
                Padding(
                  padding: const EdgeInsets.only(top: AppTokens.spacingS),
                  child: Text(_gwPanoramaText!, style: AppTokens.auxiliary),
                ),
              if (_gwRosterText != null)
                Padding(
                  padding: const EdgeInsets.only(top: AppTokens.spacingS),
                  child: Text(_gwRosterText!, style: AppTokens.auxiliary),
                ),
            ] else
              Text(
                l10n.settingsGatewayNotPaired,
                style: AppTokens.auxiliary,
              ),
            const SizedBox(height: AppTokens.spacingS),
            Text(
              l10n.settingsGatewayHelp,
              style: AppTokens.auxiliary,
            ),
          ],
        ),

        // ---------------------------------------------------- 设备信息（真实回读）
        // 与桌面端「设备管理」对称：连接态/后端/采样率/当前频率全部来自 RadioApi，
        // 未连接时诚实空态「—」。监听 RadioController(ChangeNotifier) 实时刷新。
        _section(
          title: l10n.settingsSectionDeviceInfo,
          children: <Widget>[
            ListenableBuilder(
              listenable: widget.radio as Listenable,
              builder: (BuildContext context, _) =>
                  DeviceInfoCard(radio: widget.radio),
            ),
          ],
        ),

        // ---------------------------------------------------- 时间与授时（默认折叠）
        // 系统钟 + GNSS 授时三态属底层机制，不占观测主界面；本分区默认收起，
        // 展开才真正创建 TimingPanel，收起即销毁（maintainState: false）——
        // 不展开就不跑 1 Hz 计时器，与「不展开就不跑」同一条规则。
        Container(
          margin: const EdgeInsets.symmetric(
            horizontal: AppTokens.spacingL,
            vertical: AppTokens.spacingM,
          ),
          decoration: AppTokens.cardDecoration(),
          clipBehavior: Clip.antiAlias,
          child: ExpansionTile(
            maintainState: false,
            shape: const Border(),
            collapsedShape: const Border(),
            title: Text(
              l10n.settingsSectionTiming,
              style: AppTokens.sectionTitle,
            ),
            childrenPadding: const EdgeInsets.fromLTRB(
              AppTokens.spacingL,
              0,
              AppTokens.spacingL,
              AppTokens.spacingL,
            ),
            children: <Widget>[
              const TimingPanel(),
              const SizedBox(height: AppTokens.spacingS),
              Text(
                l10n.settingsTimingHelp,
                style: AppTokens.auxiliary,
              ),
            ],
          ),
        ),

        // ---------------------------------------------------- 音频
        _section(
          title: l10n.settingsSectionAudio,
          children: <Widget>[
            _audioRow(widget.settings),
            const SizedBox(height: AppTokens.spacingS),
            _muteSwitch(widget.settings),
            const SizedBox(height: AppTokens.spacingS),
            Text(
              l10n.settingsAudioHelp,
              style: AppTokens.auxiliary,
            ),
          ],
        ),

        // ---------------------------------------------------- AI 助手
        _section(
          title: l10n.settingsSectionAi,
          children: <Widget>[
            TextField(
              controller: _keyCtrl,
              obscureText: _obscureKey,
              decoration: InputDecoration(
                labelText: 'API key',
                hintText: l10n.settingsApiKeyHint,
                prefixIcon: const Icon(Icons.key_outlined),
                suffixIcon: IconButton(
                  tooltip: _obscureKey ? l10n.settingsKeyShow : l10n.settingsKeyHide,
                  icon: Icon(
                    _obscureKey ? Icons.visibility_off_outlined : Icons.visibility_outlined,
                  ),
                  onPressed: () => setState(() => _obscureKey = !_obscureKey),
                ),
              ),
              keyboardType: TextInputType.visiblePassword,
              onTap: () {
                if (_keyMasked) {
                  setState(() {
                    _keyMasked = false;
                    _keyCtrl.text = _apiKey;
                  });
                }
              },
              onEditingComplete: _saveApiKey,
              onSubmitted: (_) => _saveApiKey(),
            ),
            const SizedBox(height: AppTokens.spacingS),
            Text(
              l10n.settingsKeySecurityNote,
              style: AppTokens.auxiliary,
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _modelCtrl,
              decoration: InputDecoration(
                labelText: l10n.settingsModelLabel,
                prefixIcon: const Icon(Icons.psychology_outlined),
              ),
              keyboardType: TextInputType.text,
              onEditingComplete: _saveModel,
              onSubmitted: (_) => _saveModel(),
            ),
            const SizedBox(height: AppTokens.spacingS),
            // AI 接管 / 手动 二态开关。
            //   * AI 接管（on）：AI 的调谐/模式/增益等动作真正下发接收机；
            //   * 手动（off）：AI 仍可对话，工具动作仅记录不执行。
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(l10n.settingsAiTakeover, style: AppTokens.body),
                ),
                Switch(
                  value: !widget.settings.aiManualMode,
                  onChanged: (bool on) =>
                      setState(() => widget.settings.aiManualMode = !on),
                ),
              ],
            ),
            const SizedBox(height: AppTokens.spacingS),
            Text(
              widget.settings.aiManualMode
                  ? l10n.settingsAiManual
                  : l10n.settingsAiTakeoverOn,
              style: AppTokens.auxiliary,
            ),
          ],
        ),

        // ---------------------------------------------------- 本站位置
        _section(
          title: l10n.settingsSectionStation,
          children: <Widget>[
            Row(
              children: <Widget>[
                FilledButton.icon(
                  onPressed: _locating ? null : _locate,
                  icon: _locating
                      // 按钮内联 16px 发丝进度圈，与文字按钮等高。
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.my_location),
                  label: Text(_locating ? l10n.settingsLocating : l10n.settingsLocate),
                ),
              ],
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _latCtrl,
              decoration: InputDecoration(labelText: l10n.settingsLatLabel),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              // 失焦/返回键收起键盘不触发提交：输入即静默落盘兜底。
              onChanged: (_) => _saveCoordinateSilent(
                  _latCtrl, (double? v) => widget.settings.stationLat = v),
              onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
              onEditingComplete: () => _saveCoordinate(_latCtrl, (double? v) => widget.settings.stationLat = v),
              onSubmitted: (_) => _saveCoordinate(_latCtrl, (double? v) => widget.settings.stationLat = v),
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _lonCtrl,
              decoration: InputDecoration(labelText: l10n.settingsLonLabel),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              onChanged: (_) => _saveCoordinateSilent(
                  _lonCtrl, (double? v) => widget.settings.stationLon = v),
              onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
              onEditingComplete: () => _saveCoordinate(_lonCtrl, (double? v) => widget.settings.stationLon = v),
              onSubmitted: (_) => _saveCoordinate(_lonCtrl, (double? v) => widget.settings.stationLon = v),
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _altCtrl,
              decoration: InputDecoration(labelText: l10n.settingsAltLabel),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              onChanged: (_) => _saveCoordinateSilent(
                  _altCtrl, (double? v) => widget.settings.stationAlt = v),
              onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
              onEditingComplete: () => _saveCoordinate(_altCtrl, (double? v) => widget.settings.stationAlt = v),
              onSubmitted: (_) => _saveCoordinate(_altCtrl, (double? v) => widget.settings.stationAlt = v),
            ),
            const SizedBox(height: AppTokens.spacingS),
            Text(_coordinateText(l10n), style: AppTokens.auxiliary),
          ],
        ),

        // ---------------------------------------------------- 语言（gen-l10n，选择即持久化）
        _section(
          title: l10n.settingsSectionLanguage,
          children: <Widget>[
            Wrap(
              spacing: AppTokens.spacingM,
              children: <Widget>[
                for (final String code in kSupportedUiLocales)
                  ChoiceChip(
                    label: Text(_languageName(l10n, code)),
                    selected: widget.settings.uiLocaleCode == code,
                    onSelected: (bool selected) {
                      if (!selected || widget.settings.uiLocaleCode == code) {
                        return;
                      }
                      setState(() => widget.settings.uiLocaleCode = code);
                      _toast(l10n.toastLanguageSaved);
                    },
                  ),
              ],
            ),
          ],
        ),

        // ---------------------------------------------------- 外观
        _section(
          title: l10n.settingsSectionAppearance,
          children: <Widget>[
            Wrap(
              spacing: AppTokens.spacingM,
              children: <Widget>[
                ChoiceChip(
                  label: Text(l10n.settingsThemeDefault),
                  selected: widget.settings.themeName == kDefaultThemeName,
                  onSelected: (_) {},
                ),
              ],
            ),
          ],
        ),
      ],
    );
  }
}

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../app/tokens.dart';
import '../services/settings_service.dart';

// ============================================================================
// 设置页：rtl_tcp / AI 助手 / 本站位置 / 外观
// 保存即时生效（失焦提交即写回 SettingsService），不硬编码任何坐标或 key。
// ============================================================================

class SettingsPage extends StatefulWidget {
  const SettingsPage({required this.settings, super.key});

  final SettingsService settings;

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late final TextEditingController _hostCtrl;
  late final TextEditingController _portCtrl;
  late final TextEditingController _keyCtrl;
  late final TextEditingController _modelCtrl;
  late final TextEditingController _latCtrl;
  late final TextEditingController _lonCtrl;
  late final TextEditingController _altCtrl;

  bool _obscureKey = true;
  bool _keyMasked = true;
  bool _locating = false;

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
    _keyCtrl = TextEditingController(text: _masked(s.apiKey));
    _modelCtrl = TextEditingController(text: s.apiModel);
    _latCtrl = TextEditingController(text: _d2s(s.stationLat));
    _lonCtrl = TextEditingController(text: _d2s(s.stationLon));
    _altCtrl = TextEditingController(text: _d2s(s.stationAlt));
  }

  @override
  void dispose() {
    _hostCtrl.dispose();
    _portCtrl.dispose();
    _keyCtrl.dispose();
    _modelCtrl.dispose();
    _latCtrl.dispose();
    _lonCtrl.dispose();
    _altCtrl.dispose();
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
    widget.settings.rtlHost = _hostCtrl.text;
    _toast('已保存主机地址');
  }

  void _savePort() {
    final int? port = int.tryParse(_portCtrl.text.trim());
    if (port == null) {
      _toast('端口必须是 1–65535 的整数', error: true);
      return;
    }
    try {
      widget.settings.rtlPort = port;
      _toast('已保存端口');
    } on FormatException catch (e) {
      _toast(e.message, error: true);
    }
  }

  void _saveApiKey() {
    final String value = _keyMasked ? _apiKey : _keyCtrl.text.trim();
    widget.settings.apiKey = value;
    setState(() {
      _keyMasked = true;
      _obscureKey = true;
      _keyCtrl.text = _masked(value);
    });
    _toast(value.isEmpty ? '已清除 API key' : '已保存 API key（仅写入系统安全存储）');
  }

  void _saveModel() {
    widget.settings.apiModel = _modelCtrl.text.trim();
    _toast('已保存模型名');
  }

  void _saveCoordinate(TextEditingController ctrl, void Function(double?) set) {
    final String raw = ctrl.text.trim();
    if (raw.isEmpty) {
      set(null);
      _toast('已清除该坐标');
      return;
    }
    final double? v = double.tryParse(raw);
    if (v == null) {
      _toast('坐标格式无效：$raw', error: true);
      return;
    }
    set(v);
    _toast('已保存坐标');
  }

  // ------------------------------------------------------------ 自动定位
  Future<void> _locate() async {
    setState(() => _locating = true);
    try {
      final bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
      if (!serviceEnabled) {
        _toast('请先开启系统定位服务', error: true);
        return;
      }
      LocationPermission permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        _toast('定位权限被拒绝，可在系统设置中开启', error: true);
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
      _toast('已写入当前定位');
    } catch (e) {
      _toast('定位失败：$e', error: true);
    } finally {
      if (mounted) setState(() => _locating = false);
    }
  }

  // ------------------------------------------------------------ 外观
  String get _coordinateText {
    final SettingsService s = widget.settings;
    if (!s.hasManualStation) {
      return '未设置坐标 —— 天空页将优先使用手机自动定位';
    }
    final String latDir = s.stationLat! >= 0 ? 'N' : 'S';
    final String lonDir = s.stationLon! >= 0 ? 'E' : 'W';
    return '当前生效：${s.stationLat!.abs().toStringAsFixed(4)}°$latDir，'
        '${s.stationLon!.abs().toStringAsFixed(4)}°$lonDir · '
        '海拔 ${_trimDouble(s.stationAlt!)} m';
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
    return ListView(
      padding: const EdgeInsets.symmetric(vertical: AppTokens.spacingL),
      children: <Widget>[
        // ---------------------------------------------------- rtl_tcp
        _section(
          title: 'rtl_tcp 接收机',
          children: <Widget>[
            TextField(
              controller: _hostCtrl,
              decoration: const InputDecoration(
                labelText: '主机地址',
                hintText: '如 192.168.1.10',
                prefixIcon: Icon(Icons.lan_outlined),
              ),
              keyboardType: TextInputType.url,
              onEditingComplete: _saveHost,
              onSubmitted: (_) => _saveHost(),
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _portCtrl,
              decoration: const InputDecoration(
                labelText: '端口',
                hintText: '1234',
                prefixIcon: Icon(Icons.numbers),
              ),
              keyboardType: TextInputType.number,
              onEditingComplete: _savePort,
              onSubmitted: (_) => _savePort(),
            ),
            const SizedBox(height: AppTokens.spacingS),
            const Text(
              '在电脑或树莓派上运行：rtl_tcp -a 0.0.0.0',
              style: AppTokens.auxiliary,
            ),
          ],
        ),

        // ---------------------------------------------------- AI 助手
        _section(
          title: 'AI 助手',
          children: <Widget>[
            TextField(
              controller: _keyCtrl,
              obscureText: _obscureKey,
              decoration: InputDecoration(
                labelText: 'API key',
                hintText: '输入云端推理 API key',
                prefixIcon: const Icon(Icons.key_outlined),
                suffixIcon: IconButton(
                  tooltip: _obscureKey ? '显示' : '隐藏',
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
            const Text(
              '仅保存在系统安全存储（Keystore / Keychain），不会写入仓库或上传',
              style: AppTokens.auxiliary,
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _modelCtrl,
              decoration: const InputDecoration(
                labelText: '模型名',
                prefixIcon: Icon(Icons.psychology_outlined),
              ),
              keyboardType: TextInputType.text,
              onEditingComplete: _saveModel,
              onSubmitted: (_) => _saveModel(),
            ),
          ],
        ),

        // ---------------------------------------------------- 本站位置
        _section(
          title: '本站位置',
          children: <Widget>[
            Row(
              children: <Widget>[
                FilledButton.icon(
                  onPressed: _locating ? null : _locate,
                  icon: _locating
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.my_location),
                  label: Text(_locating ? '定位中…' : '自动定位'),
                ),
              ],
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _latCtrl,
              decoration: const InputDecoration(labelText: '纬度（度，-90 ~ 90）'),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              onEditingComplete: () => _saveCoordinate(_latCtrl, (double? v) => widget.settings.stationLat = v),
              onSubmitted: (_) => _saveCoordinate(_latCtrl, (double? v) => widget.settings.stationLat = v),
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _lonCtrl,
              decoration: const InputDecoration(labelText: '经度（度，-180 ~ 180）'),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              onEditingComplete: () => _saveCoordinate(_lonCtrl, (double? v) => widget.settings.stationLon = v),
              onSubmitted: (_) => _saveCoordinate(_lonCtrl, (double? v) => widget.settings.stationLon = v),
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _altCtrl,
              decoration: const InputDecoration(labelText: '海拔（米）'),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              onEditingComplete: () => _saveCoordinate(_altCtrl, (double? v) => widget.settings.stationAlt = v),
              onSubmitted: (_) => _saveCoordinate(_altCtrl, (double? v) => widget.settings.stationAlt = v),
            ),
            const SizedBox(height: AppTokens.spacingS),
            Text(_coordinateText, style: AppTokens.auxiliary),
          ],
        ),

        // ---------------------------------------------------- 外观
        _section(
          title: '外观',
          children: <Widget>[
            Wrap(
              spacing: AppTokens.spacingM,
              children: <Widget>[
                ChoiceChip(
                  label: const Text('默认'),
                  selected: widget.settings.themeName == '默认',
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

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../app/tokens.dart';
import '../services/radio_controller.dart';
import '../services/settings_service.dart';
import '../widgets/device_info_card.dart';

// ============================================================================
// 设置页：rtl_tcp / AI 助手 / 本站位置 / 外观
// 保存即时生效（失焦提交即写回 SettingsService），不硬编码任何坐标或 key。
// ============================================================================

class SettingsPage extends StatefulWidget {
  const SettingsPage({required this.settings, required this.radio, super.key});

  final SettingsService settings;

  /// 收音机接口：用于「设备信息」分区真实回读连接态/采样率/频率。
  final RadioApi radio;

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
    _chHostCtrl = TextEditingController(text: s.controlHubHost);
    _chPortCtrl = TextEditingController(text: '${s.controlHubPort}');
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
    _chHostCtrl.dispose();
    _chPortCtrl.dispose();
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

  void _saveControlHubHost() {
    widget.settings.controlHubHost = _chHostCtrl.text;
    _toast('已保存桌面 ControlHub 地址');
  }

  void _saveControlHubPort() {
    final int? port = int.tryParse(_chPortCtrl.text.trim());
    if (port == null) {
      _toast('端口必须是 1–65535 的整数', error: true);
      return;
    }
    try {
      widget.settings.controlHubPort = port;
      _toast('已保存 ControlHub 端口');
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

  // ------------------------------------------------------------ 音频
  Widget _audioRow(SettingsService s) {
    final bool muted = s.muted;
    final double shown = muted ? 0.0 : s.volume;
    return Row(
      children: <Widget>[
        IconButton(
          tooltip: muted ? '取消静音' : '静音',
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
    return Row(
      children: <Widget>[
        const Expanded(
          child: Text('静音', style: AppTokens.body),
        ),
        Switch(
          value: s.muted,
          onChanged: (bool v) => setState(() => s.muted = v),
        ),
      ],
    );
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

        // ---------------------------------------------------- 桌面 ControlHub（远程只读解码）
        // 与本机 rtl_tcp 相互独立：移动端只读查看桌面引擎的 POCSAG/M17/VOR 解码结果。
        // 默认 127.0.0.1:50732（与桌面回环端点一致，本机查看器开箱可达）；
        // 主机留空 = 不启用，频谱页远程解码面板退化为「去设置」空态，不假连。
        _section(
          title: '桌面 ControlHub（远程解码）',
          children: <Widget>[
            TextField(
              controller: _chHostCtrl,
              decoration: const InputDecoration(
                labelText: '主机地址',
                hintText: '默认 127.0.0.1；留空 = 不启用，如 192.168.1.20',
                prefixIcon: Icon(Icons.dns_outlined),
              ),
              keyboardType: TextInputType.url,
              onEditingComplete: _saveControlHubHost,
              onSubmitted: (_) => _saveControlHubHost(),
            ),
            const SizedBox(height: AppTokens.spacingM),
            TextField(
              controller: _chPortCtrl,
              decoration: const InputDecoration(
                labelText: '端口',
                hintText: '默认 50732',
                prefixIcon: Icon(Icons.numbers),
              ),
              keyboardType: TextInputType.number,
              onEditingComplete: _saveControlHubPort,
              onSubmitted: (_) => _saveControlHubPort(),
            ),
            const SizedBox(height: AppTokens.spacingS),
            const Text(
              '仅只读拉取 /status、/pocsag_messages、/m17_calls、/vor_radial；'
              '解码在桌面引擎进行，移动端为查看器。',
              style: AppTokens.auxiliary,
            ),
          ],
        ),

        // ---------------------------------------------------- 设备信息（真实回读）
        // 与桌面端「设备管理」对称：连接态/后端/采样率/当前频率全部来自 RadioApi，
        // 未连接时诚实空态「—」。监听 RadioController(ChangeNotifier) 实时刷新。
        _section(
          title: '设备信息',
          children: <Widget>[
            ListenableBuilder(
              listenable: widget.radio as Listenable,
              builder: (BuildContext context, _) =>
                  DeviceInfoCard(radio: widget.radio),
            ),
          ],
        ),

        // ---------------------------------------------------- 音频
        _section(
          title: '音频',
          children: <Widget>[
            _audioRow(widget.settings),
            const SizedBox(height: AppTokens.spacingS),
            _muteSwitch(widget.settings),
            const SizedBox(height: AppTokens.spacingS),
            const Text(
              '音量与静音仅作用于本机播放，不随 rtl_tcp 上送。',
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
            const SizedBox(height: AppTokens.spacingS),
            // AI 接管 / 手动 二态开关。
            //   * AI 接管（on）：AI 的调谐/模式/增益等动作真正下发接收机；
            //   * 手动（off）：AI 仍可对话，工具动作仅记录不执行。
            Row(
              children: <Widget>[
                const Expanded(
                  child: Text('AI 接管', style: AppTokens.body),
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
                  ? '手动模式：AI 可对话，但调谐动作不会真正执行'
                  : 'AI 接管：AI 的调谐/模式/增益动作会直接生效',
              style: AppTokens.auxiliary,
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
                      // 按钮内联 16px 发丝进度圈，与文字按钮等高。
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

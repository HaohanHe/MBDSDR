import 'dart:async';

import 'package:flutter/material.dart';

import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/astro/passes.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/models/satellite_downlink.dart';
import 'package:mbdsdr_mobile/services/location_service.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/satellite_capture.dart';
import 'package:mbdsdr_mobile/services/tle_client.dart';
import 'package:mbdsdr_mobile/widgets/compass_dial.dart';

/// 天空页逻辑收口：注入 TleClient / LocationService / OrientationService，
/// 便于 widget 测试灌 fake。绝不伪造坐标/姿态。
class SkyController extends ChangeNotifier {
  SkyController({
    required TleClient tleClient,
    required LocationService locationService,
    required OrientationService orientationService,
    this.manualStation,
    this.radio,
    DateTime Function()? clock,
  })  : _tle = tleClient,
        _loc = locationService,
        _ori = orientationService,
        _clock = clock ?? DateTime.now;

  final TleClient _tle;
  final LocationService _loc;
  final OrientationService _ori;
  final DateTime Function() _clock;

  /// 外壳手填本站位置（非 null 时跳过定位）。
  final Station? manualStation;

  /// 可选射频接口：非 null 时过境行可「捕获」真实调谐。
  /// 测试注入 fake；缺省（null）时捕获入口退化为诚实提示。
  final RadioApi? radio;

  StreamSubscription<Station>? _locSub;
  StreamSubscription<DeviceOrientation>? _oriSub;

  TleGroup _group = TleGroup.stations;
  Station? _station;
  LocationStatus _locStatus = LocationStatus.disabled;
  List<Tle> _tles = const [];
  List<SatVisibility> _visible = const [];
  List<Pass> _passes = const [];
  DeviceOrientation _orientation = DeviceOrientation.unavailable;
  String? _selectedName;
  bool _refreshing = false;
  String? _error;
  DateTime? _lastUpdated;
  DateTime? _geometryTime;

  // ---- 时间预览滑条（具名常量）----
  /// 允许在「实时」上向前/向后预览的分钟数窗口（±）。
  static const double previewRangeMinutes = 30;

  /// 拖拽期间几何重算的节流间隔：连续 onChanged 在此间隔内合并，
  /// 只取最新偏移做一次真实 SGP4 重算（trailing 限频）。
  static const Duration previewThrottle = Duration(milliseconds: 80);

  /// 预览偏移（相对实时 now）；Duration.zero = 实时。
  Duration _previewOffset = Duration.zero;
  Duration _pendingOffset = Duration.zero;
  Timer? _previewTimer;

  // ---- 只读状态 ----
  TleGroup get group => _group;
  Station? get station => _station;
  LocationStatus get locStatus => _locStatus;
  List<SatVisibility> get visible => List.unmodifiable(_visible);
  List<Pass> get passes => List.unmodifiable(_passes);
  DeviceOrientation get orientation => _orientation;
  String? get selectedName => _selectedName;
  bool get refreshing => _refreshing;
  String? get error => _error;
  DateTime? get lastUpdated => _lastUpdated;

  /// 几何计算所依据的时刻（UTC）；与极坐标图上的点/弧同源。
  DateTime? get geometryTime => _geometryTime;

  /// 是否处于拖拽预览态（非实时）。
  bool get isPreview => _previewOffset != Duration.zero;

  /// 当前预览偏移（负=过去，正=未来；实时为 zero）。
  Duration get previewOffset => _previewOffset;

  /// 是否已载入可用 TLE（决定时间滑条是否可用）。
  bool get hasTle => _tles.isNotEmpty;

  /// 当前选中的卫星几何。
  SatVisibility? get selectedVisibility {
    if (_selectedName == null) return null;
    for (final v in _visible) {
      if (v.name == _selectedName) return v;
    }
    return null;
  }

  /// 启动监听（位置 + 姿态流）。
  void start() {
    _locSub = _loc.stationStream.listen((s) {
      _station = s;
      _recomputeGeometry();
      notifyListeners();
    });
    _oriSub = _ori.stream.listen((o) {
      _orientation = o;
      notifyListeners();
    });
  }

  void select(String name) {
    _selectedName = name;
    notifyListeners();
  }

  /// 该过境是否有真实下行频率（用于决定「捕获」按钮是否可用）。
  bool hasDownlink(Pass p) => satelliteDownlink(p.catalogNumber) != null;

  /// 捕获某过境：按真实下行频率 + 一次性预测多普勒调谐。
  ///
  /// 无射频接口 / 无下行频率时诚实返回不可用结果，绝不猜频率。
  Future<CaptureOutcome> capture(Pass p) async {
    final r = radio;
    if (r == null) {
      return const CaptureUnavailable(reason: '接收机未连接，无法捕获');
    }
    Tle? tle;
    for (final t in _tles) {
      if (t.catalogNumber == p.catalogNumber) {
        tle = t;
        break;
      }
    }
    return capturePass(
      radio: r,
      pass: p,
      tle: tle,
      station: _station,
      now: _clock(),
    );
  }

  void setGroup(TleGroup g) {
    _group = g;
    refresh();
  }

  /// 先定站（或用手填站），再拉 TLE，再算可见/过境。
  Future<void> refresh() async {
    _refreshing = true;
    _error = null;
    notifyListeners();
    try {
      if (manualStation != null) {
        _station = manualStation;
        _locStatus = LocationStatus.available;
      } else {
        _locStatus = await _loc.resolve();
        if (_locStatus != LocationStatus.available || _loc.station == null) {
          _refreshing = false;
          notifyListeners();
          return;
        }
        _station = _loc.station;
      }

      final fetched = await _tle.fetch(_group);
      _tles = fetched;
      _lastUpdated = _tle.lastUpdated(_group);
      _recomputeGeometry();
    } on TleFetchException catch (e) {
      _error = '$e';
    } catch (e) {
      _error = '刷新失败：$e';
    }
    _refreshing = false;
    notifyListeners();
  }

  void _recomputeGeometry() {
    final st = _station;
    if (st == null || _tles.isEmpty) return;
    final now = _clock().toUtc();
    _geometryTime = now;
    _visible = visibleAt(now, _tles, st);
    _passes = predictPasses(_tles, st, hours: 24, stepSeconds: 60, startTime: now);
    // 选中失效则清空。
    if (_selectedName != null &&
        !_visible.any((v) => v.name == _selectedName)) {
      _selectedName = null;
    }
  }

  /// 拖拽时间滑条：进入/更新预览时刻。
  ///
  /// 用移动端真实 SGP4 在「now + offset」重算全部可见卫星位置；
  /// 连续拖拽按 [previewThrottle] 合并/限频（trailing），窗口内只取最新
  /// 偏移做一次传播，避免每帧传播。仅重算可见星位，不重算 24h 过境表。
  void seekPreview(Duration offset) {
    final clamped = _clampPreview(offset);
    _pendingOffset = clamped;
    _previewTimer ??= Timer(previewThrottle, () {
      _previewTimer = null;
      _applyPreview(_pendingOffset);
    });
  }

  Duration _clampPreview(Duration o) {
    final max = Duration(minutes: previewRangeMinutes.round());
    if (o > max) return max;
    if (o < -max) return -max;
    return o;
  }

  void _applyPreview(Duration offset) {
    _previewOffset = offset;
    final st = _station;
    if (st == null || _tles.isEmpty) {
      notifyListeners();
      return;
    }
    final t = _clock().toUtc().add(offset);
    _geometryTime = t;
    _visible = visibleAt(t, _tles, st);
    // 预览时刻选中星已落到地平线下则清空，不画假点。
    if (_selectedName != null &&
        !_visible.any((v) => v.name == _selectedName)) {
      _selectedName = null;
    }
    notifyListeners();
  }

  /// 松手：退出预览，回到实时（完整重算可见星 + 24h 过境）。
  void endPreview() {
    _previewTimer?.cancel();
    _previewTimer = null;
    _pendingOffset = Duration.zero;
    if (_previewOffset == Duration.zero) return;
    _previewOffset = Duration.zero;
    _recomputeGeometry();
    notifyListeners();
  }

  /// 请求系统定位（无权限空态按钮用）。
  Future<void> openLocationSettings() => _loc.openSettings();

  @override
  void dispose() {
    _locSub?.cancel();
    _oriSub?.cancel();
    _previewTimer?.cancel();
    super.dispose();
  }
}

/// 天空页。[manualStation] 非空时使用手填本站、跳过定位。
///
/// 可选的服务注入仅供测试使用（widget 测试灌 fake）；生产构造只传 manualStation。
class SkyPage extends StatefulWidget {
  const SkyPage({
    super.key,
    this.manualStation,
    @visibleForTesting TleClient? tleClient,
    @visibleForTesting LocationService? locationService,
    @visibleForTesting OrientationService? orientationService,
    this.radio,
    @visibleForTesting DateTime Function()? clock,
  })  : _tleClient = tleClient,
        _locationService = locationService,
        _orientationService = orientationService,
        _clock = clock;

  final Station? manualStation;
  final TleClient? _tleClient;
  final LocationService? _locationService;
  final OrientationService? _orientationService;

  /// 真实/测试注入的射频接口：用于过境「捕获」。生产由外壳传入。
  final RadioApi? radio;
  final DateTime Function()? _clock;

  @override
  State<SkyPage> createState() => _SkyPageState();
}

class _SkyPageState extends State<SkyPage> {
  late final SkyController _c;
  Timer? _clockTicker;
  DateTime _wallClock = DateTime.now();

  @override
  void initState() {
    super.initState();
    _c = SkyController(
      tleClient: widget._tleClient ?? TleClient(),
      locationService:
          widget._locationService ?? GeolocatorLocationService(),
      orientationService:
          widget._orientationService ?? ImuOrientationService(),
      manualStation: widget.manualStation,
      radio: widget.radio,
      clock: widget._clock,
    );
    _c.start();
    _c.addListener(_onChange);
    // 诚实时钟：始终显示设备真实本地时间，秒级刷新。
    _clockTicker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _wallClock = DateTime.now());
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => _c.refresh());
  }

  void _onChange() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _clockTicker?.cancel();
    _c.removeListener(_onChange);
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppTokens.bgMain,
      appBar: AppBar(
        backgroundColor: AppTokens.bgBar,
        title: const Text('天空过境', style: AppTokens.appTitle),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh, color: AppTokens.accent),
            onPressed: _c.refreshing ? null : _c.refresh,
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_c.locStatus != LocationStatus.available &&
        widget.manualStation == null) {
      return _LocationEmpty(status: _c.locStatus, onEnable: _c.openLocationSettings);
    }
    return _buildMain();
  }

  Widget _buildMain() {
    final isLandscape =
        MediaQuery.orientationOf(context) == Orientation.landscape;
    final radar = SkyRadar(
      visible: _c.visible,
      selectedName: _c.selectedName,
      onSelect: _c.select,
      station: _c.station,
      now: _c.geometryTime,
    );
    final side = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _GroupChips(
          group: _c.group,
          onChanged: _c.setGroup,
          lastUpdated: _c.lastUpdated,
        ),
        if (_c.error != null) _ErrorBanner(error: _c.error!, onRetry: _c.refresh),
        // 顶部刷新指示：2px 发丝进度条，不抢视觉。
        if (_c.refreshing)
          const LinearProgressIndicator(minHeight: 2, color: AppTokens.accent),
        if (_c.selectedVisibility != null)
          Expanded(child: _GuidanceCard(controller: _c)),
        Expanded(flex: 2, child: _PassList(passes: _c.passes, controller: _c)),
      ],
    );
    final body = isLandscape
        ? Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(flex: 3, child: Center(child: radar)),
              Expanded(flex: 2, child: side),
            ],
          )
        : Column(children: [
            Expanded(flex: 3, child: radar),
            Expanded(flex: 4, child: side),
          ]);
    return Column(children: [
      _StatusBar(clock: _wallClock, station: _c.station),
      _TimeScrubber(
        enabled: _c.station != null && _c.hasTle,
        geometryTime: _c.geometryTime,
        isPreview: _c.isPreview,
        onSeek: _c.seekPreview,
        onEnd: _c.endPreview,
      ),
      Expanded(child: body),
    ]);
  }
}

/// 诚实时态条：左侧设备真实本地时钟（秒级刷新），右侧本站位置。
/// 无定位/无手填坐标时如实显示「未定位」，绝不编造经纬度。
class _StatusBar extends StatelessWidget {
  const _StatusBar({required this.clock, required this.station});

  final DateTime clock;
  final Station? station;

  String _two(int n) => n.toString().padLeft(2, '0');

  @override
  Widget build(BuildContext context) {
    final t = clock.toLocal();
    final timeText =
        '${_two(t.hour)}:${_two(t.minute)}:${_two(t.second)}';
    final stationText = station == null
        ? '未定位'
        : '本站 ${station!.lat}, ${station!.lon}';
    return Container(
      padding: const EdgeInsets.symmetric(
          horizontal: AppTokens.spacingM, vertical: AppTokens.spacingS),
      decoration: const BoxDecoration(
        color: AppTokens.card1,
        border: Border(bottom: BorderSide(color: AppTokens.cardEdge)),
      ),
      child: Row(children: [
        const Icon(Icons.schedule, size: AppTokens.iconSizeInline, color: AppTokens.textSecondary),
        const SizedBox(width: AppTokens.spacingS),
        Text(timeText, style: AppTokens.mono),
        const Spacer(),
        const Icon(Icons.place, size: AppTokens.iconSizeInline, color: AppTokens.textSecondary),
        const SizedBox(width: AppTokens.spacingS),
        Flexible(
          child: Text(
            stationText,
            style: AppTokens.auxiliary,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ]),
    );
  }
}

/// 时间预览滑条：默认实时；拖拽进入「预览」（真实 SGP4 重算星位）；松手回实时。
/// 无本站/无 TLE 时禁用并诚实空态，绝不画假时间。
class _TimeScrubber extends StatefulWidget {
  const _TimeScrubber({
    required this.enabled,
    required this.geometryTime,
    required this.isPreview,
    required this.onSeek,
    required this.onEnd,
  });

  final bool enabled;
  final DateTime? geometryTime;
  final bool isPreview;
  final ValueChanged<Duration> onSeek;
  final VoidCallback onEnd;

  @override
  State<_TimeScrubber> createState() => _TimeScrubberState();
}

class _TimeScrubberState extends State<_TimeScrubber> {
  /// 本地拖拽值：手指拖动时立即跟随，避免受控 value 滞后把 thumb 拽回中心。
  double _dragMin = 0;
  bool _scrubbing = false;

  String _two(int n) => n.toString().padLeft(2, '0');

  @override
  Widget build(BuildContext context) {
    if (!widget.enabled) {
      return Container(
        padding: const EdgeInsets.symmetric(
            horizontal: AppTokens.spacingM, vertical: AppTokens.spacingS),
        decoration: const BoxDecoration(
          color: AppTokens.card1,
          border: Border(bottom: BorderSide(color: AppTokens.cardEdge)),
        ),
        child: const Row(children: [
          Icon(Icons.history,
              size: AppTokens.iconSizeInline, color: AppTokens.textSecondary),
          SizedBox(width: AppTokens.spacingS),
          Expanded(
            child: Text('需要本站与 TLE 后可拖拽预览时间',
                style: AppTokens.auxiliary),
          ),
        ]),
      );
    }

    final value = _scrubbing ? _dragMin : 0.0;
    final t = widget.geometryTime?.toLocal();
    final timeText = t == null
        ? '--:--:--'
        : '${_two(t.hour)}:${_two(t.minute)}:${_two(t.second)}';
    final offsetMin = _scrubbing ? _dragMin.round() : 0;
    final (String badge, Color badgeColor) = switch (offsetMin) {
      0 => ('实时', AppTokens.textSecondary),
      final m when m > 0 => ('预览 +$m 分', AppTokens.warning),
      final m => ('预览 $m 分', AppTokens.warning),
    };

    return Container(
      padding: const EdgeInsets.fromLTRB(AppTokens.spacingM,
          AppTokens.spacingS, AppTokens.spacingM, AppTokens.spacingS),
      decoration: const BoxDecoration(
        color: AppTokens.card1,
        border: Border(bottom: BorderSide(color: AppTokens.cardEdge)),
      ),
      child: Row(children: [
        const Icon(Icons.history,
            size: AppTokens.iconSizeInline, color: AppTokens.textSecondary),
        const SizedBox(width: AppTokens.spacingS),
        Expanded(
          child: SliderTheme(
            data: SliderTheme.of(context).copyWith(
              trackHeight: 2,
              thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 8),
              overlayShape: const RoundSliderOverlayShape(overlayRadius: 16),
              activeTrackColor: AppTokens.accent,
              inactiveTrackColor: AppTokens.textAt(0.15),
              thumbColor: _scrubbing ? AppTokens.warning : AppTokens.accent,
              overlayColor: AppTokens.accent.withValues(alpha: 0.2),
            ),
            child: Slider(
              min: -SkyController.previewRangeMinutes,
              max: SkyController.previewRangeMinutes,
              divisions: (SkyController.previewRangeMinutes * 2).round(),
              value: value.clamp(-SkyController.previewRangeMinutes,
                  SkyController.previewRangeMinutes),
              onChanged: (v) {
                setState(() {
                  _dragMin = v;
                  _scrubbing = true;
                });
                widget.onSeek(Duration(minutes: v.round()));
              },
              onChangeEnd: (_) {
                setState(() {
                  _dragMin = 0;
                  _scrubbing = false;
                });
                widget.onEnd();
              },
            ),
          ),
        ),
        const SizedBox(width: AppTokens.spacingS),
        Text(timeText, style: AppTokens.mono),
        const SizedBox(width: AppTokens.spacingS),
        Container(
          padding: const EdgeInsets.symmetric(
              horizontal: AppTokens.spacingS, vertical: 2),
          decoration: BoxDecoration(
            color: badgeColor.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(AppTokens.radiusSmall),
          ),
          child: Text(badge,
              style: AppTokens.auxiliary.copyWith(color: badgeColor)),
        ),
      ]),
    );
  }
}

class _GroupChips extends StatelessWidget {
  const _GroupChips({
    required this.group,
    required this.onChanged,
    required this.lastUpdated,
  });
  final TleGroup group;
  final ValueChanged<TleGroup> onChanged;
  final DateTime? lastUpdated;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(AppTokens.spacingM),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: AppTokens.spacingS,
            runSpacing: AppTokens.spacingS,
            children: TleGroup.values
                .map((g) => ChoiceChip(
                      label: Text(g.query),
                      selected: g == group,
                      onSelected: (_) => onChanged(g),
                    ))
                .toList(),
          ),
          if (lastUpdated != null)
            Padding(
              padding: const EdgeInsets.only(top: AppTokens.spacingS),
              child: Text(
                '更新于 ${lastUpdated!.toLocal().toString().substring(11, 19)}',
                style: AppTokens.auxiliary,
              ),
            ),
        ],
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.error, required this.onRetry});
  final String error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.all(AppTokens.spacingM),
      padding: const EdgeInsets.all(AppTokens.spacingM),
      decoration: AppTokens.cardDecoration(color: AppTokens.danger.withValues(alpha: 0.12)),
      child: Row(children: [
        const Icon(Icons.cloud_off, color: AppTokens.danger, size: AppTokens.iconSizeInlineLg),
        const SizedBox(width: AppTokens.spacingM),
        Expanded(child: Text(error, style: AppTokens.body)),
        TextButton(onPressed: onRetry, child: const Text('重试')),
      ]),
    );
  }
}

class _LocationEmpty extends StatelessWidget {
  const _LocationEmpty({required this.status, required this.onEnable});
  final LocationStatus status;
  final VoidCallback onEnable;

  @override
  Widget build(BuildContext context) {
    final msg = switch (status) {
      LocationStatus.disabled => '定位服务未开启。过境预测需要您的位置。',
      LocationStatus.denied => '定位权限被拒绝，无法计算可见过境。',
      LocationStatus.deniedForever => '定位权限被永久拒绝，请在系统设置中开启。',
      LocationStatus.available => '',
    };
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppTokens.spacingL),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          // 定位空态插画图标（40px 略小于通用空态 48px，保持单栏内克制）。
          const Icon(Icons.location_off, color: AppTokens.warning, size: 40),
          const SizedBox(height: AppTokens.spacingL),
          Text(msg, style: AppTokens.body, textAlign: TextAlign.center),
          const SizedBox(height: AppTokens.spacingL),
          FilledButton.icon(
            onPressed: onEnable,
            icon: const Icon(Icons.settings),
            label: const Text('开启定位'),
          ),
        ]),
      ),
    );
  }
}

/// 指向引导卡：目标 az/el 对比设备 heading/pitch。
class _GuidanceCard extends StatelessWidget {
  const _GuidanceCard({required this.controller});
  final SkyController controller;

  @override
  Widget build(BuildContext context) {
    final v = controller.selectedVisibility!;
    final o = controller.orientation;
    const tol = 6.0;

    String azText;
    String elText;
    var aligned = false;
    if (o.heading == null) {
      azText = '方位 ${v.az.toStringAsFixed(0)}°（罗盘不可用）';
    } else {
      var d = (v.az - o.heading!) % 360.0;
      if (d > 180) d -= 360;
      aligned = d.abs() < tol;
      azText = d >= 0 ? '右转 ${d.abs().toStringAsFixed(0)}°' : '左转 ${d.abs().toStringAsFixed(0)}°';
    }
    if (o.pitch == null) {
      elText = '仰角 ${v.el.toStringAsFixed(0)}°（姿态不可用）';
    } else {
      final d = v.el - o.pitch!;
      aligned = aligned && d.abs() < tol;
      elText = d >= 0 ? '抬高手机 ${d.toStringAsFixed(0)}°' : '降低手机 ${(-d).toStringAsFixed(0)}°';
    }

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: AppTokens.spacingM),
      padding: const EdgeInsets.all(AppTokens.spacingM),
      decoration: AppTokens.cardDecoration(),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Row(children: [
          Text(v.name, style: AppTokens.sectionTitle),
          const Spacer(),
          if (aligned)
            const Text('已对准 ✓',
                style: TextStyle(color: AppTokens.success, fontWeight: FontWeight.w600)),
        ]),
        const SizedBox(height: AppTokens.spacingS),
        Text(azText, style: AppTokens.body),
        Text(elText, style: AppTokens.body),
        Text(
          '目标 az ${v.az.toStringAsFixed(0)}° / el ${v.el.toStringAsFixed(0)}°',
          style: AppTokens.mono,
        ),
      ]),
    );
  }
}

class _PassList extends StatelessWidget {
  const _PassList({required this.passes, required this.controller});
  final List<Pass> passes;
  final SkyController controller;

  @override
  Widget build(BuildContext context) {
    if (passes.isEmpty) {
      return const Center(
        child: Text('未来 24h 无可见过境', style: AppTokens.auxiliary),
      );
    }
    return ListView.builder(
      itemCount: passes.length,
      itemBuilder: (context, i) {
        final p = passes[i];
        final t = p.riseTime.toLocal();
        final hh = t.hour.toString().padLeft(2, '0');
        final mm = t.minute.toString().padLeft(2, '0');
        final hasDl = controller.hasDownlink(p);
        return ListTile(
          dense: true,
          leading: const Icon(Icons.flight,
              color: AppTokens.accent, size: AppTokens.iconSizeInlineLg),
          title: Text(p.name, style: AppTokens.body),
          subtitle: Text(
            '$hh:$mm 升起 · 最高仰角 ${p.maxEl.toStringAsFixed(0)}° · '
            '持续 ${p.duration.inMinutes} 分 · 升起方位 ${p.riseAz.toStringAsFixed(0)}°',
            style: AppTokens.auxiliary,
          ),
          // 有真实下行频率才可捕获；无频率者禁用并诚实提示。
          trailing: hasDl
              ? TextButton(
                  onPressed: () => _onCapture(context, p),
                  child: const Text('捕获'),
                )
              : const Tooltip(
                  message: '无下行频率数据',
                  child: Text(
                    '无下行频率数据',
                    style: AppTokens.auxiliary,
                  ),
                ),
          onTap: () => controller.select(p.name),
        );
      },
    );
  }

  Future<void> _onCapture(BuildContext context, Pass p) async {
    final messenger = ScaffoldMessenger.of(context);
    final outcome = await controller.capture(p);
    final String msg = switch (outcome) {
      CaptureApplied() => outcome.describe(),
      CaptureUnavailable() => outcome.reason,
    };
    messenger
      ..clearSnackBars()
      ..showSnackBar(SnackBar(content: Text(msg, style: AppTokens.auxiliary)));
  }
}

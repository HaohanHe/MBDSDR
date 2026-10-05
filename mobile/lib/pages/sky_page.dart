import 'dart:async';

import 'package:flutter/material.dart';

import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/astro/coordinates.dart';
import 'package:mbdsdr_mobile/astro/nav_satellites.dart';
import 'package:mbdsdr_mobile/astro/passes.dart';
import 'package:mbdsdr_mobile/astro/sgp4.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/astro/tle_freshness.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/models/satellite_downlink.dart';
import 'package:mbdsdr_mobile/pages/spacetime_status.dart';
import 'package:mbdsdr_mobile/services/doppler_step_limiter.dart';
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
  // GNSS 导航星历（尽力单独拉取）；空表示未拉取到，UI 走诚实空态。
  List<Tle> _navTles = const [];
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

  // ---- 实时多普勒自动补偿（开关默认 off；镜像桌面 DopplerStepLimiter 闭环）----
  // 诚实边界：这是 opt-in 的连续闭环。仅当用户显式开启、接收机已连接、选中
  // 目标在下行目录内、且非时间预览态时，才以 1 Hz 拍 setFrequencyHz；步进由
  // [DopplerStepLimiter] 限幅（≤ AppTokens.dopplerAutoMaxStepHz/拍），绝不抖动。
  bool _dopplerAuto = false;
  Timer? _dopplerTimer;
  final DopplerStepLimiter _dopplerLimiter = DopplerStepLimiter();

  /// 当前闭环实际施加的 VFO 偏移（= 末拍 VFO − 标称下行，Hz）；未在补偿为 null。
  double? _dopplerAppliedHz;

  // ---- 只读状态 ----
  TleGroup get group => _group;
  Station? get station => _station;
  LocationStatus get locStatus => _locStatus;
  List<SatVisibility> get visible => List.unmodifiable(_visible);
  List<Pass> get passes => List.unmodifiable(_passes);

  /// 在视导航卫星（SGP4 预测，非实时接收）；无导航星历时为空。
  List<SatVisibility> get visibleNav {
    final st = _station;
    final t = _geometryTime;
    if (st == null || t == null || _navTles.isEmpty) {
      return const <SatVisibility>[];
    }
    return visibleNavSats(t, _navTles, st);
  }

  /// 是否已载入导航星历（决定是否显示空态提示）。
  bool get hasNavTle => _navTles.isNotEmpty;
  DeviceOrientation get orientation => _orientation;
  String? get selectedName => _selectedName;
  bool get refreshing => _refreshing;
  String? get error => _error;
  DateTime? get lastUpdated => _lastUpdated;

  /// TLE 新鲜度标注（基于真实 epoch）；无 TLE 为 null（诚实空态）。
  String? get freshnessLabel =>
      tleFreshnessLabel(_tles, DateTime.now().toUtc());

  /// 是否存在过期 TLE（用于状态着色）。
  bool get tleStale {
    final double? a = oldestTleAgeDaysUtc(_tles, DateTime.now().toUtc());
    return a != null && isTleStaleAgeDays(a);
  }

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

  /// 当前选中目标的**瞬时多普勒频移读数**（Hz）。
  ///
  /// 诚实边界（务必保留）：
  ///   * 这是「当前几何时刻」单点 SGP4 传播得到的估算读数，**仅用于显示**。
  ///   * **绝不据此自动改频**——移动端没有随时间连续传播并微调调谐的轨道 loop，
  ///     自动改频有把人带偏的风险；这一步保守只显值。要真正补偿请用过境
  ///     「捕获」的一次性预测偏置，或手动在频谱上微调（与桌面勾选实时补偿不同）。
  ///   * 无选中目标 / 无本站 / 无标称下行频率（目录外卫星）/ SGP4 失效时为 null，
  ///     走卡片诚实空态，绝不编一个多普勒数字。
  double? get dopplerHz {
    final SatVisibility? v = selectedVisibility;
    final Station? st = _station;
    final DateTime? gt = _geometryTime;
    if (v == null || st == null || gt == null) return null;
    final SatDownlink? dl = satelliteDownlink(v.tle.catalogNumber);
    if (dl == null) return null; // 无标称下行频率 → 无参考载频，诚实 null
    try {
      final double vr = rangeRateAt(Sgp4(v.tle), gt.toUtc(), st);
      return dopplerShiftFromRangeRateHz(
        downlinkHz: dl.downlinkHz,
        rangeRateKmS: vr,
      );
    } on Sgp4Exception {
      return null;
    }
  }

  // ---- 实时多普勒自动补偿状态（opt-in，默认 off）----
  /// 开关是否开启（默认 off）。仅开启后才可能闭环改频。
  bool get dopplerAuto => _dopplerAuto;

  /// 是否正在真实闭环补偿（已成功下发过至少一拍）。用于状态卡文案「补偿中」。
  bool get dopplerCompensating => _dopplerAppliedHz != null;

  /// 闭环当前施加的 VFO 偏移（Hz）；未在补偿为 null。
  double? get dopplerAppliedHz => _dopplerAppliedHz;

  /// 开关自动多普勒补偿。开启即从当前 VFO 平滑起步拍一拍；关闭即停表、解绑，
  /// 不把频率拉回（离开时保留当前 VFO，诚实）。未连接/无目标/无下行目录时
  /// 开关仍记录意图但 tick 空转（见 [_dopplerTick] 门控），绝不猜频率。
  void setDopplerAuto(bool on) {
    _dopplerAuto = on;
    if (on) {
      final r = radio;
      if (r != null && r.status == ConnectionStatus.connected) {
        _dopplerLimiter.reset(r.freqHz.toDouble());
      }
      _dopplerTimer?.cancel();
      _dopplerTimer = Timer.periodic(
          AppTokens.dopplerAutoTickPeriod, (_) => _dopplerTick());
      _dopplerTick(); // 开启立即拍一拍，不空等一个节拍
    } else {
      _dopplerTimer?.cancel();
      _dopplerTimer = null;
      _dopplerLimiter.disarm();
      _dopplerAppliedHz = null;
    }
    notifyListeners();
  }

  /// 闭环一拍：用「当前几何时刻」单点 SGP4 得径向速度 → 目标 VFO = 标称下行 +
  /// 实时多普勒 → 限幅器向目标步进 → 真实 setFrequencyHz。
  ///
  /// 任一前置缺失（未开/未连接/无目标/无站/无下行目录/SGP4 失效/时间预览中）
  /// 都诚实空转，不改频、不编数。每拍先清 [_dopplerAppliedHz]，成功下发才回填。
  void _dopplerTick() {
    _dopplerAppliedHz = null;
    if (!_dopplerAuto) return;
    final r = radio;
    if (r == null || r.status != ConnectionStatus.connected) return;
    final v = selectedVisibility;
    final st = _station;
    if (v == null || st == null) return;
    if (isPreview) return; // 时间预览中不改频
    final SatDownlink? dl = satelliteDownlink(v.tle.catalogNumber);
    if (dl == null) return; // 目录外卫星无参考载频，诚实空转
    final double doppler;
    try {
      doppler = dopplerShiftFromRangeRateHz(
        downlinkHz: dl.downlinkHz,
        rangeRateKmS: rangeRateAt(Sgp4(v.tle), _clock().toUtc(), st),
      );
    } on Sgp4Exception {
      return;
    }
    final targetHz = dl.downlinkHz + doppler;
    final steppedHz = _dopplerLimiter.advance(targetHz);
    unawaited(r.setFrequencyHz(steppedHz.round()));
    _dopplerAppliedHz = steppedHz - dl.downlinkHz;
    notifyListeners();
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
      // 尽力单独拉取 GNSS 导航星历：失败不影响主列表（保持空态）。
      try {
        _navTles = await _tle.fetch(TleGroup.gnss);
      } catch (_) {
        // 导航星历拉取失败：保持 _navTles 为空，UI 显示诚实空态。
        _navTles = const [];
      }
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
    _dopplerTimer?.cancel();
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

  /// 自动补偿开关是否可用：已连接接收机 + 选中目标 + 该目标在下行目录内
  /// （有标称载频可参考）+ 非时间预览态。任一不满足则禁用并诚实说明。
  bool _dopplerSwitchEnabled() {
    final r = widget.radio;
    if (r == null || r.status != ConnectionStatus.connected) return false;
    final v = _c.selectedVisibility;
    if (v == null) return false;
    if (satelliteDownlink(v.tle.catalogNumber) == null) return false;
    return !_c.isPreview;
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
      navVisible: _c.visibleNav,
    );
    final side = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _GroupChips(
          group: _c.group,
          onChanged: _c.setGroup,
          lastUpdated: _c.lastUpdated,
          freshnessLabel: _c.freshnessLabel,
          stale: _c.tleStale,
        ),
        if (_c.error != null) _ErrorBanner(error: _c.error!, onRetry: _c.refresh),
        // 顶部刷新指示：2px 发丝进度条，不抢视觉。
        if (_c.refreshing)
          const LinearProgressIndicator(minHeight: 2, color: AppTokens.accent),
        // 时空状态四格：复用真实 radio 连接/频率与选中目标；GNSS fix 流本端未接线
        // -> 走诚实空态（无 fix），绝不编造。
        // G6：已接通选中目标的瞬时多普勒读数（仅显值）；无目标/无下行频率时为 null。
        // P49：在用户显式开启「实时多普勒补偿」后，该格升级为「补偿中·累计 xxx Hz」
        // （1 Hz 闭环真实改频，步进限幅）；未开启时保持仅显值，绝不自动改频。
        SpacetimeStatusCard(
          radioConnected: widget.radio?.status == ConnectionStatus.connected,
          freqHz: widget.radio?.freqHz,
          targetName: _c.selectedName,
          dopplerHz: _c.dopplerHz,
          dopplerActive: _c.dopplerCompensating,
          dopplerAppliedHz: _c.dopplerAppliedHz,
        ),
        _DopplerAutoControl(
          enabled: _dopplerSwitchEnabled(),
          value: _c.dopplerAuto,
          onChanged: _c.setDopplerAuto,
        ),
        if (_c.selectedVisibility != null)
          Expanded(child: _GuidanceCard(controller: _c)),
        Expanded(flex: 2, child: _PassList(passes: _c.passes, controller: _c)),
        Expanded(flex: 2, child: _NavSatList(controller: _c)),
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

/// 实时多普勒补偿开关：opt-in 闭环改频入口（默认 off）。
///
/// 诚实边界：未连接/无目标/目录外卫星/时间预览态时禁用；开启后由
/// SkyController 以 1 Hz 限幅闭环改频，关闭即停。整行最小触控高 ≥44
/// （AppTokens.touchMin），不做更小触控区。
class _DopplerAutoControl extends StatelessWidget {
  const _DopplerAutoControl({
    required this.enabled,
    required this.value,
    required this.onChanged,
  });

  final bool enabled;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    final String sub = enabled
        ? '1 Hz 限幅闭环改频（对齐桌面）'
        : '需已连接接收机并选中目录内目标';
    return Container(
      constraints: const BoxConstraints(minHeight: AppTokens.touchMin),
      margin: const EdgeInsets.fromLTRB(AppTokens.spacingM, 0,
          AppTokens.spacingM, AppTokens.spacingS),
      padding: const EdgeInsets.symmetric(
          horizontal: AppTokens.spacingM, vertical: AppTokens.spacingS),
      decoration: AppTokens.cardDecoration(),
      child: Row(children: [
        Icon(value ? Icons.sync : Icons.tune,
            size: AppTokens.iconSizeInlineLg,
            color: value ? AppTokens.success : AppTokens.textSecondary),
        const SizedBox(width: AppTokens.spacingS),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('实时多普勒补偿', style: AppTokens.body),
              Text(sub,
                  style: AppTokens.auxiliary.copyWith(
                    fontSize: AppTokens.annotationFontSize,
                    color: AppTokens.textAt(AppTokens.textAlphaTertiary),
                  )),
            ],
          ),
        ),
        Switch(value: value, onChanged: enabled ? onChanged : null),
      ]),
    );
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
    this.freshnessLabel,
    this.stale = false,
  });
  final TleGroup group;
  final ValueChanged<TleGroup> onChanged;
  final DateTime? lastUpdated;

  /// TLE 新鲜度标注（基于真实 epoch）；无 TLE 为 null。
  final String? freshnessLabel;

  /// 是否过期（warning 着色）。
  final bool stale;

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
          if (lastUpdated != null || freshnessLabel != null)
            Padding(
              padding: const EdgeInsets.only(top: AppTokens.spacingS),
              child: Wrap(
                spacing: AppTokens.spacingM,
                runSpacing: AppTokens.spacingS,
                children: [
                  if (lastUpdated != null)
                    Text(
                      '更新于 ${lastUpdated!.toLocal().toString().substring(11, 19)}',
                      style: AppTokens.auxiliary.copyWith(
                        fontSize: AppTokens.annotationFontSize,
                        color: AppTokens.textAt(AppTokens.textAlphaTertiary),
                      ),
                    ),
                  if (freshnessLabel != null)
                    Text(
                      freshnessLabel!,
                      style: AppTokens.auxiliary.copyWith(
                        fontSize: AppTokens.annotationFontSize,
                        color: stale
                            ? AppTokens.warning
                            : AppTokens.textAt(AppTokens.textAlphaTertiary),
                      ),
                    ),
                ],
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
                style: TextStyle(
                    color: AppTokens.success,
                    fontWeight: AppTokens.weightSemi)),
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

/// 在视导航卫星（GNSS）列表：SGP4 预测，非本机正在接收。
///
/// 诚实边界：所有条目以「预:」前缀与真实接收/过境标记区分；无导航星历
/// 时显示空态，绝不编造卫星。
class _NavSatList extends StatelessWidget {
  const _NavSatList({required this.controller});
  final SkyController controller;

  @override
  Widget build(BuildContext context) {
    final nav = controller.visibleNav;
    return Container(
      margin: const EdgeInsets.all(AppTokens.spacingS),
      padding: const EdgeInsets.all(AppTokens.spacingS),
      decoration: BoxDecoration(
        color: AppTokens.card1,
        borderRadius: BorderRadius.circular(AppTokens.radiusSmall),
        border: Border.all(color: AppTokens.cardEdge),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            '在视导航卫星（预测·SGP4 传播，非实时接收）',
            style: AppTokens.auxiliary.copyWith(
              fontSize: AppTokens.annotationFontSize,
              color: AppTokens.textAt(AppTokens.textAlphaTertiary),
            ),
          ),
          const SizedBox(height: AppTokens.spacingS),
          Expanded(
            child: !controller.hasNavTle
                ? const Center(
                    child: Text(
                      '无导航星历·需联网拉取 GNSS TLE',
                      style: AppTokens.auxiliary,
                      textAlign: TextAlign.center,
                    ),
                  )
                : nav.isEmpty
                    ? const Center(
                        child: Text('当前无在地平线上的导航卫星',
                            style: AppTokens.auxiliary),
                      )
                    : ListView.builder(
                        itemCount: nav.length,
                        itemBuilder: (context, i) {
                          final v = nav[i];
                          return ListTile(
                            dense: true,
                            visualDensity: VisualDensity.compact,
                            leading: const Icon(Icons.satellite_alt,
                                color: AppTokens.warning,
                                size: AppTokens.iconSizeInlineLg),
                            title: Text('预: ${v.name}', style: AppTokens.body),
                            subtitle: Text(
                              '方位 ${v.az.toStringAsFixed(0)}° · '
                              '仰角 ${v.el.toStringAsFixed(0)}° · '
                              '距离 ${v.range.toStringAsFixed(0)} km',
                              style: AppTokens.auxiliary,
                            ),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}

/// 指天视图：把手机举起来，天空按真实朝向铺在屏幕上。
///
/// 与「天空过境」页的分工
/// ----------------------
/// * 过境页回答「什么时候、朝哪个方向」——列表 + 雷达 + 时间滑条；
/// * 本页回答「我现在正对着什么」——透视视场随航向/俯仰/横滚实时转，
///   星点与星官线按当前站址/时刻现算，目标卫星给「向左/右转多少度」。
///
/// 口径
/// ----
/// * 投影、坐标链、叠加全部复用 `astro/` 下已有的唯一实现
///   （`sky_projection` / `sky_transform` / `asterism_overlay`），不另写一套；
/// * 朝向只来自真实传感器：没有航向角就**不画天空**（不用 0° 假装朝北）；
///   只有俯仰缺失时按水平视角显示并在状态条写明；
/// * 罗盘只承诺「大致天区」：状态条给最近 20 个航向样本的**观测波动**
///   （不是标称精度），并提供会话内手动对齐（+/-5°，退出即清零、不落盘）；
/// * 星表/星官目录来自打包资产，读不到就报真实原因（`SkyAssetException`）。
///
/// 性能
/// ----
/// 整帧重算约 9 千颗星的坐标换算，按传感器事件节流到 ~14 Hz（70 ms），
/// 不做空间索引——优化要等实测数字说话。
library;

import 'dart:async';
import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../astro/asterism_overlay.dart';
import '../astro/sky_assets.dart';
import '../astro/sky_projection.dart';
import '../astro/sky_transform.dart';
import '../services/orientation_service.dart';
import '../widgets/empty_state.dart';

/// 三档视场（度，按短边）：广角找星 / 常规 / 望远镜视场。
const List<double> kPointingFovs = <double>[60, 25, 8];

/// 航向波动窗口：最近 20 个姿态事件（约 1.4–2 s，取决于传感器事件率）。
const int kHeadingWindowSize = 20;

/// `pointingGuidanceText` 在方位与仰角都进入 ±5° 时返回的原样文案。
const String kPointingAlignedGuidance = '方位已对准 · 仰角已对准';

/// 航向观测波动（度），用圆周统计而不是线性极差。
///
/// 口径：把每个航向角转成单位向量后求平均矢量，合矢量长度
/// `R = |Σe^{iθ}| / n`，取圆周标准差近似 `spread = sqrt(-2·ln R)`
/// （小角时 ≈ 样本标准差），再折算成度。跨 0°/360° 的样本因此不会被
/// 当成大波动。
///
/// 这是**观测到的波动**，不是传感器标称精度，也不承诺指向误差上界；
/// 样本数 < 5 或含非有限值时返回 null（不拿一两个点编数字）。
double? headingSpreadDeg(List<double> recentHeadings) {
  if (recentHeadings.length < 5) return null;
  double sumSin = 0;
  double sumCos = 0;
  for (final double deg in recentHeadings) {
    if (!deg.isFinite) return null;
    final double rad = deg * math.pi / 180.0;
    sumSin += math.sin(rad);
    sumCos += math.cos(rad);
  }
  final int n = recentHeadings.length;
  final double resultant = math.sqrt(sumSin * sumSin + sumCos * sumCos) / n;
  if (resultant >= 1.0) return 0.0;
  // R→0 时对数发散；夹到极小值，并把结果限制在物理上可能的最大值 180°。
  final double safe = math.max(resultant, 1e-9);
  final double spreadRad = math.sqrt(-2.0 * math.log(safe));
  return (spreadRad * 180.0 / math.pi).clamp(0.0, 180.0).toDouble();
}

/// 目标方位/仰角与当前姿态的差，折算成一句人能执行的指令。
///
/// 纯函数，便于单测：正方位差 = 向右（顺时针）转，正仰角差 = 抬头。
String pointingGuidanceText({
  required double headingDeg,
  required double pitchDeg,
  required double targetAzDeg,
  required double targetElDeg,
  double toleranceDeg = 5,
}) {
  if (targetElDeg < 0) {
    return '目标在地平线下（仰角 ${targetElDeg.toStringAsFixed(0)}°），等它升起';
  }
  final double deltaAz =
      (targetAzDeg - headingDeg + 540.0) % 360.0 - 180.0; // [-180, 180)
  final double deltaEl = targetElDeg - pitchDeg;
  final List<String> parts = <String>[];
  if (deltaAz.abs() <= toleranceDeg) {
    parts.add('方位已对准');
  } else if (deltaAz > 0) {
    parts.add('向右转 ${deltaAz.toStringAsFixed(0)}°');
  } else {
    parts.add('向左转 ${(-deltaAz).toStringAsFixed(0)}°');
  }
  if (deltaEl.abs() <= toleranceDeg) {
    parts.add('仰角已对准');
  } else if (deltaEl > 0) {
    parts.add('抬头 ${deltaEl.toStringAsFixed(0)}°');
  } else {
    parts.add('低头 ${(-deltaEl).toStringAsFixed(0)}°');
  }
  return parts.join(' · ');
}

/// 指天视图页面。
///
/// [site] 为空 → 诚实空态（没有站址就无法把 J2000 星表换算到地平）；
/// [orientationStream] / [assetSource] / [clock] 供测试注入。
class PointingPage extends StatefulWidget {
  const PointingPage({
    super.key,
    this.site,
    this.orientationStream,
    this.assetSource,
    this.targetAzDeg,
    this.targetElDeg,
    this.targetLabel,
    this.clock,
    this.minFrame = const Duration(milliseconds: 70),
    this.sensorTimeout = const Duration(milliseconds: 1500),
  });

  final SkySite? site;
  final Stream<DeviceOrientation>? orientationStream;
  final SkyAssetSource? assetSource;
  final double? targetAzDeg;
  final double? targetElDeg;
  final String? targetLabel;
  final DateTime Function()? clock;

  /// 整帧重算节流（测试传 Duration.zero 关掉）。
  final Duration minFrame;

  /// 多久没收到任何姿态事件就判定「传感器不可用」并显示真实原因。
  final Duration sensorTimeout;

  @override
  State<PointingPage> createState() => _PointingPageState();
}

class _PointingPageState extends State<PointingPage> {
  SkyAssets? _assets;
  SkyOverlayEngine? _engine;
  String? _assetError;

  DeviceOrientation _orientation = DeviceOrientation.unavailable;
  StreamSubscription<DeviceOrientation>? _sub;
  OrientationService? _ownedService;

  /// 是否收到过任何姿态事件（区分「还没读到」与「确实没有传感器」）。
  bool _gotOrientation = false;
  bool _sensorTimedOut = false;
  Timer? _sensorTimer;

  double _fov = kPointingFovs.first;
  DateTime _lastFrame = DateTime.fromMillisecondsSinceEpoch(0);

  /// 最近 [kHeadingWindowSize] 个航向样本（滚动窗口，仅用于算观测波动）。
  final List<double> _recentHeadings = <double>[];

  /// 手动对齐偏移（度，会话内有效，页面退出即清零，不落盘）。
  double _manualOffsetDeg = 0;

  @override
  void initState() {
    super.initState();
    _bindOrientation();
    _loadAssets();
    _sensorTimer = Timer(widget.sensorTimeout, () {
      if (!mounted || _gotOrientation) return;
      setState(() => _sensorTimedOut = true);
    });
  }

  void _bindOrientation() {
    final Stream<DeviceOrientation>? injected = widget.orientationStream;
    if (injected != null) {
      _sub = injected.listen(_onOrientation);
      return;
    }
    final OrientationService service = ImuOrientationService();
    _ownedService = service;
    _sub = service.stream.listen(_onOrientation);
  }

  void _onOrientation(DeviceOrientation value) {
    final DateTime now = DateTime.now();
    if (now.difference(_lastFrame) < widget.minFrame) return;
    _lastFrame = now;
    if (!mounted) return;
    setState(() {
      _orientation = value;
      _gotOrientation = true;
      final double? heading = value.heading;
      if (heading == null) {
        // 罗盘掉了：窗口清空，波动必须重新从样本不足开始，不沿用旧数据。
        _recentHeadings.clear();
      } else {
        _recentHeadings.add(heading);
        if (_recentHeadings.length > kHeadingWindowSize) {
          _recentHeadings.removeAt(0);
        }
      }
    });
  }

  /// 原始航向 + 手动偏移，归一到 [0, 360)。
  double _effectiveHeading(double rawHeadingDeg) =>
      (rawHeadingDeg + _manualOffsetDeg) % 360.0;

  /// 手动对齐只改本页会话内的偏移，**不写** settings/prefs。
  void _nudgeHeading(double deltaDeg) {
    setState(() {
      double next = (_manualOffsetDeg + deltaDeg) % 360.0;
      if (next > 180.0) next -= 360.0;
      if (next < -180.0) next += 360.0;
      _manualOffsetDeg = next;
    });
  }

  void _resetManualOffset() => setState(() => _manualOffsetDeg = 0);

  Future<void> _loadAssets() async {
    try {
      final SkyAssets assets =
          await (widget.assetSource ?? const BundleSkyAssetSource()).load();
      if (!mounted) return;
      setState(() {
        _assets = assets;
        _engine =
            SkyOverlayEngine(stars: assets.stars, catalog: assets.catalog);
      });
    } on SkyAssetException catch (e) {
      if (!mounted) return;
      setState(() => _assetError = e.toString());
    } catch (e) {
      if (!mounted) return;
      setState(() => _assetError = '星表资产读取失败：$e');
    }
  }

  @override
  void dispose() {
    _sensorTimer?.cancel();
    _sub?.cancel();
    _ownedService?.dispose();
    super.dispose();
  }

  void _cycleFov() {
    final int index = kPointingFovs.indexOf(_fov);
    setState(() => _fov = kPointingFovs[(index + 1) % kPointingFovs.length]);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppTokens.bgMain,
      appBar: AppBar(
        backgroundColor: AppTokens.bgBar,
        title: const Text('指天视图', style: AppTokens.appTitle),
        actions: <Widget>[
          TextButton.icon(
            key: const Key('pointing-fov-button'),
            onPressed: _cycleFov,
            icon: const Icon(Icons.zoom_out_map, color: AppTokens.accent, size: 18),
            label: Text(
              '视场 ${_fov.toStringAsFixed(0)}°',
              style: AppTokens.auxiliary,
            ),
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (widget.site == null) {
      return const EmptyState(
        icon: Icons.location_disabled_outlined,
        title: '未设置本站坐标',
        message: '星表要换算到“你头顶的天空”必须先有经纬度。'
            '请在设置里填写本站坐标（或打开定位）后再进来。',
      );
    }
    final String? assetError = _assetError;
    if (assetError != null) {
      return EmptyState(
        icon: Icons.error_outline,
        title: '星表资产不可用',
        message: assetError,
      );
    }
    final SkyAssets? assets = _assets;
    final SkyOverlayEngine? engine = _engine;
    if (assets == null || engine == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_orientation.heading == null && !_gotOrientation && !_sensorTimedOut) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            CircularProgressIndicator(),
            SizedBox(height: AppTokens.spacingL),
            Text('正在读取罗盘/姿态…', style: AppTokens.auxiliary),
          ],
        ),
      );
    }
    if (_orientation.heading == null) {
      return EmptyState(
        icon: Icons.explore_off_outlined,
        title: '罗盘不可用',
        message: _orientation.hasCompass
            ? '罗盘有事件但没有有效航向角，无法把星图转到你面向的方向。'
            : '本机没有可用的罗盘/磁力计。到户外、校准系统罗盘后重试；'
                '本页不会用 0° 假装朝北。',
      );
    }
    return _buildSky(assets, engine);
  }

  Widget _buildSky(SkyAssets assets, SkyOverlayEngine engine) {
    final double rawHeading = _orientation.heading!;
    final double heading = _effectiveHeading(rawHeading);
    final double? pitch = _orientation.pitch;
    final double roll = _orientation.roll ?? 0.0;
    final double? spreadDeg = headingSpreadDeg(_recentHeadings);
    final SkySite site = widget.site!;
    final DateTime now = (widget.clock ?? DateTime.now)().toUtc();
    final double jd = julianDateFromUtc(now);

    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final double width = constraints.maxWidth;
        final double height = constraints.maxHeight;
        final SkyView view = SkyView(
          centerAzDeg: heading,
          centerAltDeg: pitch ?? 0.0,
          fovDeg: _fov,
          width: width,
          height: height,
          rotationDeg: -roll,
        );
        final OverlayResult overlay = engine.compute(
          site: site,
          jdUt1: jd,
          view: view,
        );
        final Map<int, SkyStar> byHip = <int, SkyStar>{
          for (final SkyStar s in assets.stars)
            if (s.hip != 0) s.hip: s,
        };
        final String? guidance =
            (widget.targetAzDeg == null || widget.targetElDeg == null)
                ? null
                : _finalGuidance(
                    pointingGuidanceText(
                      headingDeg: heading,
                      pitchDeg: pitch ?? 0.0,
                      targetAzDeg: widget.targetAzDeg!,
                      targetElDeg: widget.targetElDeg!,
                    ),
                    spreadDeg,
                  );
        return Stack(
          fit: StackFit.expand,
          children: <Widget>[
            CustomPaint(
              painter: PointingPainter(
                overlay: overlay,
                view: view,
                byHip: byHip,
                targetAzDeg: widget.targetAzDeg,
                targetElDeg: widget.targetElDeg,
                targetLabel: widget.targetLabel,
              ),
            ),
            Positioned(
              left: AppTokens.spacingM,
              right: AppTokens.spacingM,
              top: AppTokens.spacingM,
              child: _StatusRow(
                heading: heading,
                pitch: pitch,
                fov: _fov,
                starCount: overlay.starScreenPoints.length,
                segmentCount: overlay.segmentCount,
                license: assets.license,
                spreadDeg: spreadDeg,
                manualOffsetDeg: _manualOffsetDeg,
              ),
            ),
            Positioned(
              left: AppTokens.spacingM,
              right: AppTokens.spacingM,
              bottom: AppTokens.spacingL,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  if (guidance != null) _GuidanceBanner(text: guidance),
                  const SizedBox(height: AppTokens.spacingS),
                  _AlignControls(
                    onNudge: _nudgeHeading,
                    onReset: _resetManualOffset,
                  ),
                ],
              ),
            ),
          ],
        );
      },
    );
  }

  /// 对准时把纯函数的「已对准」换成 P3 口径：只承诺天区，并交代观测波动。
  String _finalGuidance(String guidance, double? spreadDeg) {
    if (guidance != kPointingAlignedGuidance) return guidance;
    final String spreadText = spreadDeg == null
        ? '罗盘波动未知'
        : '罗盘波动 ±${spreadDeg.toStringAsFixed(0)}°';
    return '已进入目标天区（±5°），$spreadText';
  }
}

/// 顶部状态：方位/仰角/视场/星数/线数/罗盘波动/手动对齐；许可缺失时明说。
class _StatusRow extends StatelessWidget {
  const _StatusRow({
    required this.heading,
    required this.pitch,
    required this.fov,
    required this.starCount,
    required this.segmentCount,
    required this.license,
    required this.spreadDeg,
    required this.manualOffsetDeg,
  });

  final double heading;
  final double? pitch;
  final double fov;
  final int starCount;
  final int segmentCount;
  final String license;

  /// 航向观测波动（度）；样本不足为 null。
  final double? spreadDeg;

  /// 手动对齐偏移（度，会话内）。
  final double manualOffsetDeg;

  @override
  Widget build(BuildContext context) {
    final double? spread = spreadDeg;
    final String manualText = manualOffsetDeg == 0
        ? '未手动对齐'
        : '手动对齐 ${manualOffsetDeg > 0 ? '+' : ''}'
            '${manualOffsetDeg.toStringAsFixed(0)}°';
    final List<Widget> chips = <Widget>[
      // 方位/仰角来自传感器实时读数（观测），不是目标几何预测值。
      _chip('方位 ${heading.toStringAsFixed(0)}°（罗盘观测）'),
      _chip(pitch == null
          ? '仰角未知（按水平显示）'
          : '仰角 ${pitch!.toStringAsFixed(0)}°（姿态观测）'),
      _chip('视场 ${fov.toStringAsFixed(0)}°'),
      _chip(spread == null
          ? '罗盘精度：样本不足'
          : '罗盘 ±${spread.toStringAsFixed(0)}°（近 2 s 波动）'),
      _chip(manualText),
      _chip('星 $starCount · 线 $segmentCount'),
      if (license.isEmpty) _chip('许可缺失', warn: true),
    ];
    return Wrap(
      spacing: AppTokens.spacingM,
      runSpacing: AppTokens.spacingS,
      children: chips,
    );
  }

  Widget _chip(String text, {bool warn = false}) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppTokens.spacingM,
        vertical: AppTokens.spacingS,
      ),
      decoration: BoxDecoration(
        color: AppTokens.card2,
        borderRadius: BorderRadius.circular(AppTokens.radiusPill),
        border: Border.all(
          color: warn ? AppTokens.warning : AppTokens.cardEdge,
        ),
      ),
      child: Text(
        text,
        style: AppTokens.auxiliary.copyWith(
          color: warn ? AppTokens.warning : AppTokens.textSecondary,
        ),
      ),
    );
  }
}

/// 底部一行可执行指令（向左/右转、抬头/低头）。
class _GuidanceBanner extends StatelessWidget {
  const _GuidanceBanner({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppTokens.spacingL,
        vertical: AppTokens.spacingM,
      ),
      decoration: BoxDecoration(
        color: AppTokens.card3,
        borderRadius: BorderRadius.circular(AppTokens.radiusCard),
        border: Border.all(color: AppTokens.cardEdge),
      ),
      child: Text(
        text,
        textAlign: TextAlign.center,
        style: AppTokens.body.copyWith(color: AppTokens.textPrimary),
      ),
    );
  }
}

/// 手动对齐按钮：只偏移本页会话内的航向，不退化成「校准档案」。
class _AlignControls extends StatelessWidget {
  const _AlignControls({required this.onNudge, required this.onReset});

  final ValueChanged<double> onNudge;
  final VoidCallback onReset;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.spacingM),
      decoration: BoxDecoration(
        color: AppTokens.card3,
        borderRadius: BorderRadius.circular(AppTokens.radiusPill),
        border: Border.all(color: AppTokens.cardEdge),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          TextButton(
            key: const Key('pointing-align-left'),
            onPressed: () => onNudge(-5),
            child: const Text('左 5°', style: AppTokens.auxiliary),
          ),
          TextButton(
            key: const Key('pointing-align-right'),
            onPressed: () => onNudge(5),
            child: const Text('右 5°', style: AppTokens.auxiliary),
          ),
          TextButton(
            key: const Key('pointing-align-reset'),
            onPressed: onReset,
            child: const Text('复位', style: AppTokens.auxiliary),
          ),
        ],
      ),
    );
  }
}

/// 把一帧叠加结果画出来：背景 → 地平线 → 星官线 → 星点 → 方位标 → 准星 → 目标。
class PointingPainter extends CustomPainter {
  PointingPainter({
    required this.overlay,
    required this.view,
    required this.byHip,
    this.targetAzDeg,
    this.targetElDeg,
    this.targetLabel,
  });

  final OverlayResult overlay;
  final SkyView view;
  final Map<int, SkyStar> byHip;
  final double? targetAzDeg;
  final double? targetElDeg;
  final String? targetLabel;

  @override
  void paint(Canvas canvas, Size size) {
    _paintBackground(canvas, size);
    _paintHorizon(canvas);
    _paintAsterisms(canvas);
    _paintStars(canvas);
    _paintCardinals(canvas);
    _paintCrosshair(canvas, size);
    _paintTarget(canvas);
  }

  void _paintBackground(Canvas canvas, Size size) {
    final Rect rect = Offset.zero & size;
    final Paint paint = Paint()
      ..shader = const RadialGradient(
        center: Alignment(0, -0.25),
        radius: 0.9,
        colors: <Color>[Color(0xFF101828), AppTokens.bgMain],
      ).createShader(rect);
    canvas.drawRect(rect, paint);
  }

  /// 地平线：alt=0 的一圈，按 3° 采样连成折线。
  void _paintHorizon(Canvas canvas) {
    final Paint paint = Paint()
      ..color = AppTokens.accent.withValues(alpha: 0.45)
      ..strokeWidth = 1.2
      ..style = PaintingStyle.stroke;
    ScreenPoint? previous;
    for (double az = 0; az <= 360; az += 3) {
      final ScreenPoint? current = projectAzAlt(az % 360.0, 0, view);
      if (previous != null && current != null) {
        if (isOnCanvas(previous, view.width, view.height, margin: 40) ||
            isOnCanvas(current, view.width, view.height, margin: 40)) {
          canvas.drawLine(previous.toOffset(), current.toOffset(), paint);
        }
      }
      previous = current;
    }
  }

  void _paintAsterisms(Canvas canvas) {
    final Paint paint = Paint()
      ..color = AppTokens.accent.withValues(alpha: 0.55)
      ..strokeWidth = 1.0
      ..style = PaintingStyle.stroke;
    for (final AsterismPresence presence in overlay.asterisms) {
      for (final OverlaySegment seg in presence.segments) {
        canvas.drawLine(seg.a.toOffset(), seg.b.toOffset(), paint);
      }
    }
    // 只给最靠前的几官标名，避免整屏文字。
    final int labels = math.min(4, overlay.asterisms.length);
    for (int i = 0; i < labels; i++) {
      final AsterismPresence presence = overlay.asterisms[i];
      if (presence.segments.isEmpty) continue;
      double sx = 0, sy = 0;
      int n = 0;
      for (final OverlaySegment seg in presence.segments) {
        sx += seg.a.x + seg.b.x;
        sy += seg.a.y + seg.b.y;
        n += 2;
      }
      final Offset center = Offset(sx / n, sy / n);
      if (!_inside(center, margin: 0)) continue;
      _text(canvas, presence.name, center + const Offset(0, -14),
          color: AppTokens.textSecondary, size: 10);
    }
  }

  void _paintStars(Canvas canvas) {
    final Paint paint = Paint()..style = PaintingStyle.fill;
    for (int i = 0; i < overlay.starScreenPoints.length; i++) {
      final ScreenPoint p = overlay.starScreenPoints[i];
      final int hip = overlay.starHips[i];
      final SkyStar? star = byHip[hip];
      final double mag = star?.vmag ?? 6.0;
      final double radius = (2.6 - 0.24 * mag).clamp(0.6, 2.6);
      paint.color = _starColor(star?.bv);
      canvas.drawCircle(p.toOffset(), radius, paint);
    }
  }

  void _paintCardinals(Canvas canvas) {
    const List<(double, String)> names = <(double, String)>[
      (0, 'N'),
      (90, 'E'),
      (180, 'S'),
      (270, 'W'),
    ];
    for (final (double az, String label) in names) {
      final ScreenPoint? p = projectAzAlt(az, 0, view);
      if (p == null) continue;
      if (!_inside(p.toOffset(), margin: 16)) continue;
      _text(canvas, label, p.toOffset() + const Offset(-4, -20),
          color: AppTokens.textPrimary, size: 12, bold: true);
    }
  }

  void _paintCrosshair(Canvas canvas, Size size) {
    final Offset center = Offset(size.width / 2, size.height / 2);
    final Paint paint = Paint()
      ..color = AppTokens.textPrimary.withValues(alpha: 0.55)
      ..strokeWidth = 1.0;
    canvas.drawLine(center - const Offset(14, 0),
        center - const Offset(4, 0), paint);
    canvas.drawLine(center + const Offset(4, 0),
        center + const Offset(14, 0), paint);
    canvas.drawLine(center - const Offset(0, 14),
        center - const Offset(0, 4), paint);
    canvas.drawLine(center + const Offset(0, 4),
        center + const Offset(0, 14), paint);
    canvas.drawCircle(
      center,
      2.0,
      Paint()..color = AppTokens.traceColor.withValues(alpha: 0.9),
    );
  }

  void _paintTarget(Canvas canvas) {
    final double? az = targetAzDeg;
    final double? el = targetElDeg;
    if (az == null || el == null) return;
    final ScreenPoint? p = projectAzAlt(az, el, view);
    if (p == null) {
      // 目标在地平线下或背对相机：只在画面底部说明，不画假点。
      final String label = targetLabel ?? '目标';
      _text(canvas, '$label 不在视场内（仰角 ${el.toStringAsFixed(0)}°）',
          Offset(view.width / 2, view.height - 56),
          color: AppTokens.warning, size: 11, center: true);
      return;
    }
    final Paint ring = Paint()
      ..color = AppTokens.success
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.6;
    canvas.drawCircle(p.toOffset(), 14, ring);
    canvas.drawLine(p.toOffset() - const Offset(20, 0),
        p.toOffset() - const Offset(16, 0), ring);
    canvas.drawLine(p.toOffset() + const Offset(16, 0),
        p.toOffset() + const Offset(20, 0), ring);
    canvas.drawLine(p.toOffset() - const Offset(0, 20),
        p.toOffset() - const Offset(0, 16), ring);
    canvas.drawLine(p.toOffset() + const Offset(0, 16),
        p.toOffset() + const Offset(0, 20), ring);
    if (targetLabel != null) {
      _text(canvas, targetLabel!, p.toOffset() + const Offset(0, 22),
          color: AppTokens.success, size: 11, center: true);
    }
  }

  Color _starColor(double? bv) {
    if (bv == null || !bv.isFinite) return const Color(0xFFFFFFFF);
    if (bv < 0.3) return const Color(0xFFBFD8FF);
    if (bv < 0.8) return const Color(0xFFF4F6FF);
    if (bv < 1.2) return const Color(0xFFFFE8C8);
    return const Color(0xFFFFD0A0);
  }

  bool _inside(Offset p, {required double margin}) =>
      p.dx >= -margin &&
      p.dy >= -margin &&
      p.dx <= view.width + margin &&
      p.dy <= view.height + margin;

  void _text(
    Canvas canvas,
    String text,
    Offset at, {
    required Color color,
    double size = 11,
    bool bold = false,
    bool center = false,
  }) {
    final TextPainter painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          color: color,
          fontSize: size,
          fontWeight: bold ? FontWeight.w600 : FontWeight.w400,
          shadows: const <Shadow>[
            Shadow(color: Color(0xCC000000), blurRadius: 3),
          ],
        ),
      ),
      textDirection: ui.TextDirection.ltr,
    )..layout();
    final Offset origin =
        center ? at - Offset(painter.width / 2, 0) : at;
    painter.paint(canvas, origin);
  }

  @override
  bool shouldRepaint(covariant PointingPainter oldDelegate) => true;
}

/// `Offset` 便捷扩展：屏幕点转绘制坐标。
extension on ScreenPoint {
  Offset toOffset() => Offset(x, y);
}

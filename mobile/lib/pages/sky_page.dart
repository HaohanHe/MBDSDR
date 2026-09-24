import 'dart:math';
import 'package:flutter/material.dart';
import '../connection.dart';
import '../theme.dart';

// ============================================================================
// 卫星指向引导页面
// ----------------------------------------------------------------------------
// 顶部罗盘圆环：CustomPainter 绘制，外圈 N/E/S/W（0/90/180/270），每 30° 一刻度。
// 红/橙色三角形固定指向"上"代表手机当前朝向；目标方位角箭头按相对偏差旋转。
// 圆环下方给出 左转/右转/已对准 文字引导，以及俯仰角只读滑块。
// 下半部分为 satellite_passes 流下发的过境卫星列表，点击可本地高亮选中。
// ============================================================================
class SkyPage extends StatefulWidget {
  final ConnectionService connection;
  const SkyPage({super.key, required this.connection});

  @override
  State<SkyPage> createState() => _SkyPageState();
}

class _SkyPageState extends State<SkyPage> {
  /// 本地选中的卫星名（仅用于高亮，真正目标由电脑端 pointing 决定）
  String? _selectedSatellite;

  @override
  Widget build(BuildContext context) {
    // heading 是 ConnectionService 的属性，它本身是 ChangeNotifier，
    // 用 ListenableBuilder 监听 notifyListeners 以刷新罗盘。
    return ListenableBuilder(
      listenable: widget.connection,
      builder: (context, _) {
        final heading = widget.connection.heading;
        return StreamBuilder<Map<String, dynamic>>(
          stream: widget.connection.pointingStream,
          initialData: widget.connection.lastPointing,
          builder: (context, pSnap) {
            final pointing = pSnap.data;
            return StreamBuilder<List<dynamic>>(
              stream: widget.connection.passesStream,
              builder: (context, passSnap) {
                final passes = passSnap.data ?? const [];
                return _buildBody(context, heading, pointing, passes);
              },
            );
          },
        );
      },
    );
  }

  Widget _buildBody(
    BuildContext context,
    double heading,
    Map<String, dynamic>? pointing,
    List<dynamic> passes,
  ) {
    double? targetAz;
    double? elevation;
    String? satName;
    if (pointing != null) {
      targetAz = (pointing['azimuth'] as num?)?.toDouble();
      elevation = (pointing['elevation'] as num?)?.toDouble();
      satName = pointing['satellite'] as String?;
    }

    // 相对偏差：-180 ~ 180。负 = 左转，正 = 右转。
    double? biasDeg;
    if (targetAz != null) {
      biasDeg = (targetAz - heading + 540) % 360 - 180;
    }

    return Scaffold(
      backgroundColor: AppTheme.bg,
      appBar: AppBar(
        title: const Text('卫星指向引导'),
        backgroundColor: AppTheme.bg,
        foregroundColor: AppTheme.text,
        elevation: 0,
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          // ---------- 罗盘 ----------
          Center(
            child: SizedBox(
              width: 280,
              height: 280,
              child: CustomPaint(
                painter: _CompassPainter(
                  heading: heading,
                  biasDeg: biasDeg,
                  hasTarget: pointing != null,
                ),
              ),
            ),
          ),
          const SizedBox(height: 8),
          // 当前朝向读数
          Center(
            child: Text(
              '当前朝向 ${heading.toStringAsFixed(0)}°',
              style: const TextStyle(
                color: AppTheme.textSecondary,
                fontSize: 13,
              ),
            ),
          ),
          const SizedBox(height: 16),
          // ---------- 文字引导 ----------
          _buildGuidance(biasDeg, elevation, satName),
          const SizedBox(height: 16),
          // ---------- 俯仰角可视化 ----------
          _buildElevationGauge(elevation),
          const SizedBox(height: 24),
          // ---------- 过境列表标题 ----------
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text(
                '卫星过境列表',
                style: TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.bold,
                  color: AppTheme.text,
                ),
              ),
              Text(
                '${passes.length} 颗',
                style: const TextStyle(
                  color: AppTheme.textSecondary,
                  fontSize: 12,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          // ---------- 过境列表 ----------
          if (passes.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 24),
              child: Center(
                child: Text(
                  '暂无过境数据',
                  style: TextStyle(color: AppTheme.textSecondary),
                ),
              ),
            )
          else
            ..._buildPassesTiles(passes),
        ],
      ),
    );
  }

  Widget _buildGuidance(double? biasDeg, double? elevation, String? satName) {
    String line;
    Color lineColor;
    if (biasDeg == null) {
      line = '等待电脑端指向指令...';
      lineColor = AppTheme.textSecondary;
    } else if (biasDeg.abs() < 5) {
      line = '方位已对准 ✓';
      lineColor = AppTheme.success;
    } else if (biasDeg < 0) {
      line = '左转 ${biasDeg.abs().toStringAsFixed(0)}°';
      lineColor = AppTheme.danger;
    } else {
      line = '右转 ${biasDeg.toStringAsFixed(0)}°';
      lineColor = AppTheme.accent;
    }

    final elevText = elevation != null
        ? '俯仰角 ${elevation.toStringAsFixed(0)}°'
        : '俯仰角 --';

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 16),
      decoration: BoxDecoration(
        color: AppTheme.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppTheme.border),
      ),
      child: Column(
        children: [
          if (satName != null)
            Text(
              '目标：$satName',
              style: const TextStyle(
                color: AppTheme.textSecondary,
                fontSize: 13,
              ),
            ),
          if (satName != null) const SizedBox(height: 6),
          Text(
            line,
            style: TextStyle(
              color: lineColor,
              fontSize: 22,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            elevText,
            style: const TextStyle(color: AppTheme.textSecondary, fontSize: 14),
          ),
        ],
      ),
    );
  }

  Widget _buildElevationGauge(double? elevation) {
    final v = (elevation ?? 0).clamp(0.0, 90.0);
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      decoration: BoxDecoration(
        color: AppTheme.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppTheme.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text(
                '仰角',
                style: TextStyle(color: AppTheme.textSecondary, fontSize: 13),
              ),
              Text(
                elevation != null
                    ? '${elevation.toStringAsFixed(0)}° / 90°'
                    : '-- / 90°',
                style: const TextStyle(
                  color: AppTheme.text,
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          // 只读滑块：onChanged 传 null 即禁用手动拖动
          SliderTheme(
            data: SliderTheme.of(context).copyWith(
              trackHeight: 4,
              thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 10),
              overlayShape:
                  const RoundSliderOverlayShape(overlayRadius: 16),
            ),
            child: Slider(
              value: v,
              min: 0,
              max: 90,
              onChanged: null,
              activeColor: AppTheme.accent,
              inactiveColor: AppTheme.border,
            ),
          ),
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 2),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('0°',
                    style: TextStyle(
                        color: AppTheme.textSecondary, fontSize: 11)),
                Text('45°',
                    style: TextStyle(
                        color: AppTheme.textSecondary, fontSize: 11)),
                Text('90°',
                    style: TextStyle(
                        color: AppTheme.textSecondary, fontSize: 11)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _buildPassesTiles(List<dynamic> passes) {
    return passes.map((e) {
      final m = e is Map ? e : const {};
      final name = (m['name'] ?? '未知卫星').toString();
      final maxEl = (m['max_el'] as num?)?.toDouble();
      final az = (m['azimuth'] as num?)?.toDouble();
      final riseTime = (m['rise_time'] ?? '').toString();
      final selected = _selectedSatellite == name;

      return Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Material(
          color: selected ? AppTheme.accent.withOpacity(0.12) : AppTheme.card,
          borderRadius: BorderRadius.circular(10),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
            side: BorderSide(
              color: selected ? AppTheme.accent : AppTheme.border,
              width: selected ? 1.2 : 1,
            ),
          ),
          child: ListTile(
            dense: true,
            leading: Icon(
              selected ? Icons.satellite_alt : Icons.satellite_outlined,
              color: selected ? AppTheme.accent : AppTheme.primary,
            ),
            title: Text(
              name,
              style: TextStyle(
                fontWeight: selected ? FontWeight.bold : FontWeight.w500,
                color: AppTheme.text,
              ),
            ),
            subtitle: Text(
              [
                if (maxEl != null) '最高仰角 ${maxEl.toStringAsFixed(0)}°',
                if (az != null) '方位 ${az.toStringAsFixed(0)}°',
                if (riseTime.isNotEmpty) '升起 $riseTime',
              ].join('  ·  '),
              style: const TextStyle(
                  color: AppTheme.textSecondary, fontSize: 12),
            ),
            trailing: selected
                ? const Icon(Icons.check_circle,
                    color: AppTheme.accent, size: 20)
                : const Icon(Icons.chevron_right,
                    color: AppTheme.textSecondary, size: 20),
            onTap: () {
              setState(() {
                _selectedSatellite = selected ? null : name;
              });
            },
          ),
        ),
      );
    }).toList();
  }
}

// ============================================================================
// 罗盘 CustomPainter
// ----------------------------------------------------------------------------
// 坐标系：canvas 默认 y 轴向下，rotate(正) = 顺时针。
// 我们把"屏幕顶部"作为 0° 顺时针参考方向。
//   - 绝对方位 A（0=N,90=E,180=S,270=W）在屏幕上的顺时针角 = (A - heading) mod 360
//   - 目标相对偏差 biasDeg 直接就是相对顶部的顺时针偏移（正=右，负=左）
// ============================================================================
class _CompassPainter extends CustomPainter {
  final double heading;
  final double? biasDeg;
  final bool hasTarget;

  _CompassPainter({
    required this.heading,
    required this.biasDeg,
    required this.hasTarget,
  });

  /// 把"从顶部顺时针 clockwiseDeg"转成 canvas 旋转弧度（canvas 0° = 右，正=顺时针）
  double _cwFromTopToRad(double clockwiseDegFromTop) {
    return (clockwiseDegFromTop % 360) * pi / 180 - pi / 2;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = size.width / 2 - 18;

    // ---------- 外环 ----------
    final ringPaint = Paint()
      ..color = AppTheme.primary
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    canvas.drawCircle(center, radius, ringPaint);

    // 内圈淡底
    final bgPaint = Paint()
      ..color = AppTheme.bgAlt.withValues(alpha: 0.4)
      ..style = PaintingStyle.fill;
    canvas.drawCircle(center, radius, bgPaint);

    // ---------- 刻度：每 30° ----------
    final tickPaint = Paint()
      ..color = AppTheme.primary.withValues(alpha: 0.7)
      ..strokeWidth = 1.5;
    final majorTickPaint = Paint()
      ..color = AppTheme.primary
      ..strokeWidth = 2.5;

    for (int a = 0; a < 360; a += 30) {
      // 绝对方位 a，随 heading 旋转
      final screenCw = (a - heading) % 360;
      final rad = _cwFromTopToRad(screenCw);
      final isMajor = a % 90 == 0;
      final outer = Offset(
        center.dx + radius * cos(rad),
        center.dy + radius * sin(rad),
      );
      final innerR = isMajor ? radius - 10 : radius - 6;
      final inner = Offset(
        center.dx + innerR * cos(rad),
        center.dy + innerR * sin(rad),
      );
      canvas.drawLine(inner, outer, isMajor ? majorTickPaint : tickPaint);
    }

    // ---------- N/E/S/W 文字 ----------
    const labels = {0: 'N', 90: 'E', 180: 'S', 270: 'W'};
    labels.forEach((az, text) {
      final screenCw = (az - heading) % 360;
      final rad = _cwFromTopToRad(screenCw);
      final labelR = radius - 24;
      final pos = Offset(
        center.dx + labelR * cos(rad),
        center.dy + labelR * sin(rad),
      );
      final tp = TextPainter(
        text: TextSpan(
          text: text,
          style: TextStyle(
            color: az == 0 ? AppTheme.danger : AppTheme.primary,
            fontSize: 14,
            fontWeight: FontWeight.bold,
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, pos - Offset(tp.width / 2, tp.height / 2));
    });

    // ---------- 当前朝向箭头（红色，固定指向上方 = 手机顶部） ----------
    _drawArrow(
      canvas,
      center,
      angleRad: -pi / 2, // 指向屏幕顶部
      color: AppTheme.danger,
      length: radius * 0.55,
      halfWidth: 11,
    );

    // ---------- 目标方位箭头（橙色，按相对偏差旋转） ----------
    if (hasTarget && biasDeg != null) {
      final rad = _cwFromTopToRad(biasDeg!);
      _drawArrow(
        canvas,
        center,
        angleRad: rad,
        color: AppTheme.accent,
        length: radius * 0.75,
        halfWidth: 13,
      );
    }
  }

  /// 从圆心出发，沿 angleRad（canvas 坐标，0=右，正=顺时针）画一个三角形箭头。
  void _drawArrow(
    Canvas canvas,
    Offset center, {
    required double angleRad,
    required Color color,
    required double length,
    required double halfWidth,
  }) {
    final tip = Offset(
      center.dx + length * cos(angleRad),
      center.dy + length * sin(angleRad),
    );
    final baseCenter = Offset(
      center.dx + (length - 16) * cos(angleRad),
      center.dy + (length - 16) * sin(angleRad),
    );
    // 垂直方向
    final perp = Offset(-sin(angleRad), cos(angleRad));
    final left = baseCenter + perp * halfWidth;
    final right = baseCenter - perp * halfWidth;

    final path = Path()
      ..moveTo(tip.dx, tip.dy)
      ..lineTo(left.dx, left.dy)
      ..lineTo(right.dx, right.dy)
      ..close();

    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.fill;
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _CompassPainter oldDelegate) {
    return oldDelegate.heading != heading ||
        oldDelegate.biasDeg != biasDeg ||
        oldDelegate.hasTarget != hasTarget;
  }
}

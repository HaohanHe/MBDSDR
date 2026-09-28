import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';

/// 天空雷达（heading-up 极坐标）：外圈=地平线(el 0°)，中心=天顶(el 90°)。
///
/// 方位刻度随设备 [heading] 旋转（指北针向上），可见卫星按 az/el 落点。
class SkyRadar extends StatelessWidget {
  const SkyRadar({
    super.key,
    required this.heading,
    required this.visible,
    required this.selectedName,
    required this.onSelect,
  });

  /// 设备航向（度，北=0）；为 null 时按真北固定（0°在上方）。
  final double? heading;

  final List<SatVisibility> visible;
  final String? selectedName;
  final ValueChanged<String> onSelect;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final side = math.min(constraints.maxWidth, constraints.maxHeight);
        return GestureDetector(
          onTapUp: (d) => _handleTap(d.localPosition, side),
          child: CustomPaint(
            size: Size.square(side),
            painter: _RadarPainter(
              heading: heading ?? 0.0,
              visible: visible,
              selectedName: selectedName,
              textStyle: AppTokens.auxiliary,
            ),
          ),
        );
      },
    );
  }

  void _handleTap(Offset pos, double side) {
    final center = Offset(side / 2, side / 2);
    final r = (pos - center).distance / (side / 2);
    if (r > 1.05) return;
    // 仰角：边缘0°、中心90°。
    final el = (1.0 - r) * 90.0;
    // 屏幕角（自顶顺时针）-> 方位（heading-up）。
    final screenAngle =
        (math.atan2(pos.dx - center.dx, center.dy - pos.dy) * 180 / math.pi);
    final az = (screenAngle + (heading ?? 0.0)) % 360.0;

    // 找最近卫星（el 差 < 10° 且 az 差 < 15°）。
    SatVisibility? best;
    var bestD = 30.0;
    for (final v in visible) {
      final dEl = (v.el - el).abs();
      var dAz = (v.az - az).abs() % 360.0;
      if (dAz > 180) dAz = 360 - dAz;
      final d = math.sqrt(dEl * dEl + (dAz * 0.3) * (dAz * 0.3));
      if (d < bestD) {
        bestD = d;
        best = v;
      }
    }
    if (best != null) onSelect(best.name);
  }
}

class _RadarPainter extends CustomPainter {
  _RadarPainter({
    required this.heading,
    required this.visible,
    required this.selectedName,
    required this.textStyle,
  });

  final double heading;
  final List<SatVisibility> visible;
  final String? selectedName;
  final TextStyle textStyle;

  @override
  void paint(Canvas canvas, Size size) {
    final c = Offset(size.width / 2, size.height / 2);
    final R = size.width / 2;
    final grid = Paint()
      ..color = AppTokens.textAt(0.14)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.0;

    // 同心圈：el=30 / 60 / 90(中心)。外圈即 el=0。
    for (final el in const [30.0, 60.0, 90.0]) {
      canvas.drawCircle(c, R * (1 - el / 90), grid);
    }
    // 十字线（指向 N/E/S/W，随 heading 旋转）。
    for (final a in const [0.0, 90.0, 180.0, 270.0]) {
      final rad = (a - heading) * math.pi / 180.0;
      final p = c + Offset(math.sin(rad), -math.cos(rad)) * R;
      canvas.drawLine(c, p, grid);
    }

    // 方位标签。
    const labels = ['N', 'E', 'S', 'W'];
    for (var i = 0; i < 4; i++) {
      final a = (i * 90.0 - heading) * math.pi / 180.0;
      final lp = c + Offset(math.sin(a), -math.cos(a)) * (R - 14);
      final tp = TextPainter(
        text: TextSpan(text: labels[i], style: textStyle),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, lp - Offset(tp.width / 2, tp.height / 2));
    }

    // 卫星点。
    for (final v in visible) {
      if (v.el < 0) continue;
      final rad = (v.az - heading) * math.pi / 180.0;
      final rr = R * (1 - v.el.clamp(0, 90) / 90);
      final p = c + Offset(math.sin(rad), -math.cos(rad)) * rr;
      final selected = v.name == selectedName;
      final dot = Paint()
        ..color = selected ? AppTokens.success : AppTokens.accent
        ..style = PaintingStyle.fill;
      canvas.drawCircle(p, selected ? 6 : 4, dot);
      final tp = TextPainter(
        text: TextSpan(
          text: v.name,
          style: textStyle.copyWith(
            color: selected ? AppTokens.success : AppTokens.textPrimary,
            fontSize: 11,
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout(maxWidth: 90);
      tp.paint(canvas, p + const Offset(8, -8));
    }
  }

  @override
  bool shouldRepaint(covariant _RadarPainter old) =>
      old.heading != heading ||
      old.selectedName != selectedName ||
      old.visible != visible;
}

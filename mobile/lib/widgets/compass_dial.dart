import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'package:mbdsdr_mobile/app/tokens.dart';
import 'package:mbdsdr_mobile/astro/coordinates.dart';
import 'package:mbdsdr_mobile/astro/sgp4.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';

// ============================================================================
// Stellarium 式方位/仰极坐标图（north-up）。
//   * 圆心 = 天顶（仰角 90°），外圆 = 地平线（仰角 0°）。
//   * 方位角自北顺时针 0..360°；正北固定在屏幕上方。
// 本文件唯一公开的几何契约是 [polarPoint]，单测直接对它做落点校验。
// ============================================================================

/// 极坐标 → 画布偏移（纯函数，可单测）。
///
/// [center] 圆心；[radius] 外圆半径 = 地平线(el 0°)。
/// [azDeg] 方位角（度，自北顺时针，0=北）；[elDeg] 仰角（度，0=地平，90=天顶）。
/// 仰角会被夹到 [0,90]；方位角按模 360 归一。
Offset polarPoint(Offset center, double radius, double azDeg, double elDeg) {
  var az = azDeg % 360.0;
  if (az < 0.0) az += 360.0;
  final theta = az * math.pi / 180.0;
  final el = elDeg.clamp(0.0, 90.0);
  final frac = (90.0 - el) / 90.0; // 地平=1（外圆），天顶=0（圆心）
  final r = radius * frac;
  return center + Offset(math.sin(theta), -math.cos(theta)) * r;
}

/// 画布偏移 → 方位/仰角（与 [polarPoint] 互逆；点选卫星用）。
({double az, double el}) polarPointInverse(
    Offset pos, Offset center, double radius) {
  final d = pos - center;
  var r = d.distance / radius;
  if (r > 1.2) r = 1.2;
  final el = (1.0 - r) * 90.0;
  var az = math.atan2(d.dx, -d.dy) * 180.0 / math.pi;
  az = az % 360.0;
  if (az < 0.0) az += 360.0;
  return (az: az, el: el);
}

/// 一条未来过境轨迹弧（采样点，az/el 度）。
class _PassArc {
  const _PassArc({required this.name, required this.samples});
  final String name;
  final List<({double az, double el})> samples;
}

/// 选中卫星轨迹：以当前几何时刻为中心，前后各采样的分钟数（真实传播）。
const double kTrajectoryHalfWindowMinutes = 10;

/// 选中轨迹采样步长（秒）。
const int kTrajectoryStepSeconds = 30;

/// 天空极坐标图。[station] 与 [now]（UTC）非空时，为每颗可见卫星绘制
/// 未来一小段过境弧；缺失则只画当前点，绝不编造轨迹。
class SkyRadar extends StatelessWidget {
  const SkyRadar({
    super.key,
    required this.visible,
    required this.selectedName,
    required this.onSelect,
    this.station,
    this.now,
  });

  final List<SatVisibility> visible;
  final String? selectedName;
  final ValueChanged<String> onSelect;

  /// 本站（用于推算未来过境弧）。null 时不画轨迹。
  final Station? station;

  /// 几何参考时刻（UTC）；与可见点同源。null 时不画轨迹。
  final DateTime? now;

  /// 为一颗可见卫星采样未来一段弧（直到落地或 20 分钟上限）。
  List<({double az, double el})> _sampleArc(Tle tle, DateTime nowUtc) {
    final st = station;
    if (st == null) return const [];
    final List<({double az, double el})> out = [];
    try {
      final sat = Sgp4(tle);
      var t = nowUtc;
      final end = t.add(const Duration(minutes: 20));
      while (t.isBefore(end)) {
        t = t.add(const Duration(seconds: 30));
        final a = azElAt(sat, t, st);
        out.add((az: a.az, el: a.el));
        if (a.el <= 0.0) break;
      }
    } on Sgp4Exception {
      return const [];
    }
    return out;
  }

  /// 选中卫星的过境前后真实传播轨迹：[nowUtc] 前后各
  /// [kTrajectoryHalfWindowMinutes] 分钟，按 [kTrajectoryStepSeconds] 采样。
  /// 只保留仰角为正的点（落地即断段），绝不画假轨迹。
  List<({double az, double el})> _sampleSelectedTrajectory(
      Tle tle, DateTime nowUtc) {
    final st = station;
    if (st == null) return const [];
    final List<({double az, double el})> out = [];
    try {
      final sat = Sgp4(tle);
      final start = nowUtc.subtract(
          Duration(minutes: kTrajectoryHalfWindowMinutes.round()));
      final end = nowUtc.add(
          Duration(minutes: kTrajectoryHalfWindowMinutes.round()));
      var t = start;
      while (!t.isAfter(end)) {
        final a = azElAt(sat, t, st);
        if (a.el > 0.0) out.add((az: a.az, el: a.el));
        t = t.add(const Duration(seconds: kTrajectoryStepSeconds));
      }
    } on Sgp4Exception {
      return const [];
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final station = this.station;
    final nowUtc = now?.toUtc();
    final arcs = <_PassArc>[];
    List<({double az, double el})> selectedTrajectory = const [];
    if (station != null && nowUtc != null) {
      for (final v in visible) {
        if (v.el <= 0.0) continue;
        final s = _sampleArc(v.tle, nowUtc);
        if (s.length >= 2) arcs.add(_PassArc(name: v.name, samples: s));
      }
      // 选中卫星：过境前后 ±10 分钟真实传播轨迹（独立采样，绿色高亮）。
      if (selectedName != null) {
        for (final v in visible) {
          if (v.name == selectedName) {
            selectedTrajectory = _sampleSelectedTrajectory(v.tle, nowUtc);
            break;
          }
        }
      }
    }

    return LayoutBuilder(
      builder: (context, constraints) {
        final side = math.min(constraints.maxWidth, constraints.maxHeight);
        return GestureDetector(
          onTapUp: (d) => _handleTap(d.localPosition, side),
          child: CustomPaint(
            size: Size.square(side),
            painter: _PolarPainter(
              visible: visible,
              selectedName: selectedName,
              arcs: arcs,
              selectedTrajectory: selectedTrajectory,
            ),
          ),
        );
      },
    );
  }

  void _handleTap(Offset pos, double side) {
    final center = Offset(side / 2, side / 2);
    final R = side / 2 - _PolarPainter.outerMargin;
    final g = polarPointInverse(pos, center, R);
    SatVisibility? best;
    var bestD = 30.0;
    for (final v in visible) {
      if (v.el <= 0.0) continue;
      final dEl = (v.el - g.el).abs();
      var dAz = (v.az - g.az).abs() % 360.0;
      if (dAz > 180.0) dAz = 360.0 - dAz;
      final d = math.sqrt(dEl * dEl + (dAz * 0.3) * (dAz * 0.3));
      if (d < bestD) {
        bestD = d;
        best = v;
      }
    }
    if (best != null) onSelect(best.name);
  }
}

class _PolarPainter extends CustomPainter {
  _PolarPainter({
    required this.visible,
    required this.selectedName,
    required this.arcs,
    required this.selectedTrajectory,
  });

  final List<SatVisibility> visible;
  final String? selectedName;
  final List<_PassArc> arcs;

  /// 选中卫星过境前后 ±10 分钟的真实传播轨迹（az/el 采样）。
  final List<({double az, double el})> selectedTrajectory;

  /// 外圆之外留给方位字母/刻度的边距（含文字半高，保证不被裁切）。
  static const double outerMargin = 26;

  static const List<double> _altitudeRings = [30.0, 60.0];

  @override
  void paint(Canvas canvas, Size size) {
    final side = math.min(size.width, size.height);
    final c = Offset(size.width / 2, size.height / 2);
    final R = side / 2 - outerMargin;
    if (R <= 8) return;

    _paintGrid(canvas, c, R);
    _paintArcs(canvas, c, R);
    _paintSelectedTrajectory(canvas, c, R);
    _paintSatellites(canvas, c, R, size);
  }

  // ---- 网格：极淡，不喧宾夺主 --------------------------------------------
  void _paintGrid(Canvas canvas, Offset c, double R) {
    final faint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.0;

    // 方位辐条：每 30°。
    for (var az = 0; az < 360; az += 30) {
      final cardinal = az % 90 == 0;
      final p = polarPoint(c, R, az.toDouble(), 0);
      faint.color = AppTokens.textAt(cardinal ? 0.12 : 0.06);
      faint.strokeWidth = cardinal ? 1.1 : 1.0;
      canvas.drawLine(c, p, faint);
    }

    // 仰角圈：30 / 60（外圆 0 单独画）。
    for (final el in _altitudeRings) {
      final r = R * (90.0 - el) / 90.0;
      faint.color = AppTokens.textAt(0.08);
      faint.strokeWidth = 1.0;
      canvas.drawCircle(c, r, faint);
    }

    // 外圆（地平线 el=0）稍清晰。
    faint.color = AppTokens.textAt(0.18);
    faint.strokeWidth = 1.2;
    canvas.drawCircle(c, R, faint);

    // 外圆上每 30° 的小刻度。
    for (var az = 0; az < 360; az += 30) {
      final theta = az * math.pi / 180.0;
      final dir = Offset(math.sin(theta), -math.cos(theta));
      canvas.drawLine(c + dir * (R - 3), c + dir * (R + 3), faint);
    }

    _paintAzimuthLabels(canvas, c, R);
    _paintAltitudeLabels(canvas, c, R);
  }

  void _paintAzimuthLabels(Canvas canvas, Offset c, double R) {
    const cardinal = {0: 'N', 90: 'E', 180: 'S', 270: 'W'};
    for (var az = 0; az < 360; az += 30) {
      final theta = az * math.pi / 180.0;
      final dir = Offset(math.sin(theta), -math.cos(theta));
      final isCardinal = cardinal.containsKey(az);
      final style = isCardinal
          ? AppTokens.auxiliary.copyWith(fontSize: 12, fontWeight: FontWeight.w600)
          : TextStyle(
              fontSize: 8.5,
              color: AppTokens.textAt(0.45),
            );
      final tp = TextPainter(
        text: TextSpan(text: cardinal[az] ?? '$az°', style: style),
        textDirection: TextDirection.ltr,
      )..layout();
      final at = c + dir * (R + outerMargin - 10);
      tp.paint(canvas, at - Offset(tp.width / 2, tp.height / 2));
    }
  }

  void _paintAltitudeLabels(Canvas canvas, Offset c, double R) {
    // 在西侧（270°）辐条上标注 30°/60° 仰角圈。
    for (final el in _altitudeRings) {
      final p = polarPoint(c, R, 270.0, el);
      final tp = TextPainter(
        text: TextSpan(
          text: '${el.round()}°',
          style: TextStyle(fontSize: 8, color: AppTokens.textAt(0.4)),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, p + Offset(3, -tp.height - 1));
    }
  }

  // ---- 未来过境弧：淡色平滑曲线 ------------------------------------------
  void _paintArcs(Canvas canvas, Offset c, double R) {
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2
      ..color = AppTokens.accent.withValues(alpha: 0.28)
      ..strokeJoin = StrokeJoin.round
      ..strokeCap = StrokeCap.round;
    for (final arc in arcs) {
      if (arc.samples.length < 2) continue;
      final path = Path();
      double? prevAz;
      for (final s in arc.samples) {
        final p = polarPoint(c, R, s.az, s.el);
        if (prevAz == null) {
          path.moveTo(p.dx, p.dy);
        } else {
          // 方位跳变过大（跨过 0/360 或弧端落地）则断开，避免横穿整圆。
          var dAz = (s.az - prevAz).abs();
          if (dAz > 180.0) dAz = 360.0 - dAz;
          if (dAz > 90.0) {
            path.moveTo(p.dx, p.dy);
          } else {
            path.lineTo(p.dx, p.dy);
          }
        }
        prevAz = s.az;
      }
      canvas.drawPath(path, paint);
    }
  }

  // ---- 选中卫星轨迹：真实传播采样，绿色高亮 -------------------------------
  void _paintSelectedTrajectory(Canvas canvas, Offset c, double R) {
    final samples = selectedTrajectory;
    if (samples.length < 2) return;
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.6
      ..color = AppTokens.success.withValues(alpha: 0.85)
      ..strokeJoin = StrokeJoin.round
      ..strokeCap = StrokeCap.round;
    final path = Path();
    double? prevAz;
    for (final s in samples) {
      final p = polarPoint(c, R, s.az, s.el);
      if (prevAz == null) {
        path.moveTo(p.dx, p.dy);
      } else {
        var dAz = (s.az - prevAz).abs();
        if (dAz > 180.0) dAz = 360.0 - dAz;
        // 方位跳变过大则断开，避免横穿整圆。
        if (dAz > 90.0) {
          path.moveTo(p.dx, p.dy);
        } else {
          path.lineTo(p.dx, p.dy);
        }
      }
      prevAz = s.az;
    }
    canvas.drawPath(path, paint);
  }

  // ---- 卫星点 + 避让标签 --------------------------------------------------
  void _paintSatellites(Canvas canvas, Offset c, double R, Size size) {
    final items = visible.where((v) => v.el > 0.0).toList();
    if (items.isEmpty) return;

    // 仰角最高（最接近过顶）者用 accent 高亮。
    SatVisibility? top;
    for (final v in items) {
      if (top == null || v.el > top.el) top = v;
    }

    // 先画点。
    for (final v in items) {
      final p = polarPoint(c, R, v.az, v.el);
      final isSelected = v.name == selectedName;
      final isTop = identical(v, top);
      final color = isSelected
          ? AppTokens.success
          : (isTop ? AppTokens.accent : AppTokens.textPrimary);
      final dot = Paint()..color = color;
      canvas.drawCircle(p, isSelected || isTop ? 4.5 : 3.0, dot);
      if (isTop && !isSelected) {
        canvas.drawCircle(
            p, 7.5, Paint()..color = AppTokens.accent.withValues(alpha: 0.25)..style = PaintingStyle.stroke..strokeWidth = 1.0);
      }
    }

    // 标签避让：仰角高的优先；8 向试位，重叠/越界则放弃（宁可稀疏不叠字）。
    items.sort((a, b) => b.el.compareTo(a.el));
    final occupied = <Rect>[];
    const pad = 3.0;
    for (final v in items) {
      final p = polarPoint(c, R, v.az, v.el);
      final isSelected = v.name == selectedName;
      final tp = TextPainter(
        text: TextSpan(
          text: v.name,
          style: TextStyle(
            fontSize: 10.5,
            color: isSelected ? AppTokens.success : AppTokens.textSecondary,
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout(maxWidth: 96);

      Rect? chosen;
      Offset? anchor;
      for (var k = 0; k < 8; k++) {
        final ang = k * math.pi / 4.0;
        final lp = p + Offset(math.cos(ang), math.sin(ang)) * 15.0;
        final rect = Rect.fromCenter(
            center: lp, width: tp.width + pad * 2, height: tp.height + pad * 2);
        // 不越出画布。
        if (rect.left < 2 ||
            rect.right > size.width - 2 ||
            rect.top < 2 ||
            rect.bottom > size.height - 2) {
          continue;
        }
        // 不与已放置标签重叠。
        if (occupied.any((r) => r.overlaps(rect))) continue;
        chosen = rect;
        anchor = lp;
        break;
      }
      if (chosen == null || anchor == null) continue;
      occupied.add(chosen);

      // 点与标签距离稍远时画一根细引线。
      if ((anchor - p).distance > 18) {
        canvas.drawLine(
            p,
            anchor - Offset(tp.width / 2, tp.height / 2) * 0.4,
            Paint()
              ..color = AppTokens.textAt(0.25)
              ..strokeWidth = 0.8);
      }
      tp.paint(canvas, anchor - Offset(tp.width / 2, tp.height / 2));
    }
  }

  @override
  bool shouldRepaint(covariant _PolarPainter old) =>
      old.selectedName != selectedName ||
      !identical(old.visible, visible) ||
      !identical(old.arcs, arcs) ||
      !identical(old.selectedTrajectory, selectedTrajectory);
}

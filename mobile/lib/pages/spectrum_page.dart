import 'package:flutter/material.dart';
import '../connection.dart';
import '../theme.dart';

// ============================================================================
// 实时频谱页面
// ----------------------------------------------------------------------------
// 订阅 ConnectionService.fftStream 拿到 FFT 幅度数组，用 CustomPainter 逐 bin
// 画柱状频谱；顶部显示中心频率 / 带宽 / 连接状态；底部为频率轴（左低右高）。
// 新帧与上一帧做 alpha=0.3 的指数滑动平均，避免高频闪烁。
// ============================================================================

class SpectrumPage extends StatefulWidget {
  /// 直接传入连接服务引用（不经过 Provider）。
  final ConnectionService connection;

  const SpectrumPage({super.key, required this.connection});

  @override
  State<SpectrumPage> createState() => _SpectrumPageState();
}

class _SpectrumPageState extends State<SpectrumPage> {
  /// 新帧权重（旧帧占 1 - alpha）。
  static const double _smoothAlpha = 0.3;

  /// 平滑后的最近一帧（长度变化时重置）。
  List<double> _smoothed = const [];

  /// 把原始帧与缓存帧做一阶低通混合，结果写回 _smoothed。
  void _blendInto(List<double> raw) {
    if (raw.isEmpty) return;
    if (_smoothed.length != raw.length) {
      _smoothed = List<double>.unmodifiable(raw);
      return;
    }
    final next = List<double>.generate(
      raw.length,
      (i) => _smoothed[i] * (1 - _smoothAlpha) + raw[i] * _smoothAlpha,
      growable: false,
    );
    _smoothed = List<double>.unmodifiable(next);
  }

  @override
  Widget build(BuildContext context) {
    // 外层监听 conn（ChangeNotifier），保证连接状态变化时顶部信息条刷新。
    return AnimatedBuilder(
      animation: widget.connection,
      builder: (context, _) {
        final conn = widget.connection;
        final lowFreq = conn.centerFreqMhz - conn.spanMhz / 2;
        final highFreq = conn.centerFreqMhz + conn.spanMhz / 2;

        return Scaffold(
          backgroundColor: const Color(0xFF1A1A1A),
          body: SafeArea(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _buildHeader(conn),
                Expanded(
                  child: StreamBuilder<List<double>>(
                    stream: conn.fftStream,
                    builder: (context, snap) {
                      final hasFrame = snap.hasData && snap.data!.isNotEmpty;
                      if (hasFrame) _blendInto(snap.data!);

                      return Stack(
                        fit: StackFit.expand,
                        children: [
                          // 频谱主体（未连接时给出占位提示）
                          if (!hasFrame && !conn.isConnected)
                            const Center(
                              child: Text(
                                '等待电脑端频谱数据...',
                                style: TextStyle(
                                  color: Colors.white54,
                                  fontSize: 15,
                                  letterSpacing: 1.2,
                                ),
                              ),
                            )
                          else
                            CustomPaint(
                              size: Size.infinite,
                              painter: _SpectrumPainter(
                                bins: _smoothed,
                                barBottomPadding: 26,
                                primary: AppTheme.primary,
                                accent: AppTheme.accent,
                              ),
                            ),

                          // 底部频率轴：左低频 / 右高频
                          Positioned(
                            left: 12,
                            right: 12,
                            bottom: 6,
                            child: Row(
                              mainAxisAlignment: MainAxisAlignment.spaceBetween,
                              children: [
                                Text(
                                  '${lowFreq.toStringAsFixed(1)} MHz',
                                  style: const TextStyle(
                                    color: Colors.white38,
                                    fontSize: 11,
                                  ),
                                ),
                                Text(
                                  '${highFreq.toStringAsFixed(1)} MHz',
                                  style: const TextStyle(
                                    color: Colors.white38,
                                    fontSize: 11,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _buildHeader(ConnectionService conn) {
    final connected = conn.isConnected;
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 10),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: Colors.white12, width: 0.5)),
      ),
      child: Row(
        children: [
          // 中心频率 / 带宽
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '中心频率  ${conn.centerFreqMhz.toStringAsFixed(1)} MHz',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 15,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  '带宽  ${conn.spanMhz.toStringAsFixed(1)} MHz',
                  style: const TextStyle(
                    color: Colors.white60,
                    fontSize: 12,
                  ),
                ),
              ],
            ),
          ),
          // 连接状态
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: BoxDecoration(
              color: (connected ? AppTheme.success : AppTheme.danger)
                  .withOpacity(0.15),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 7,
                  height: 7,
                  decoration: BoxDecoration(
                    color: connected ? AppTheme.success : AppTheme.danger,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  connected ? '已连接' : '未连接',
                  style: TextStyle(
                    color:
                        connected ? AppTheme.success : AppTheme.danger,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
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

// ============================================================================
// 频谱柱状图 Painter
// ----------------------------------------------------------------------------
// 每根柱子对应一个 FFT bin，柱宽自适应可用宽度，柱间留 1px 间隙；
// 柱体从底部向上，颜色由 primary（底）渐变到 accent（顶）。
// ============================================================================
class _SpectrumPainter extends CustomPainter {
  final List<double> bins;

  /// 距画布底部预留高度（给频率轴文字让位）。
  final double barBottomPadding;

  final Color primary;
  final Color accent;

  _SpectrumPainter({
    required this.bins,
    required this.barBottomPadding,
    required this.primary,
    required this.accent,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (bins.isEmpty) return;

    // ---- 1. 归一化到 [0,1]（兼容 0~1 与带负号的 dB 量纲）----
    double minV = bins.first;
    double maxV = bins.first;
    for (final v in bins) {
      if (v < minV) minV = v;
      if (v > maxV) maxV = v;
    }
    double range = maxV - minV;
    if (range <= 0) range = 1;

    // ---- 2. 淡色水平参考线（1/4、1/2、3/4 高度）----
    final gridPaint = Paint()
      ..color = Colors.white.withOpacity(0.08)
      ..strokeWidth = 0.5;
    for (final f in const [0.25, 0.5, 0.75]) {
      final y = size.height * f;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }

    // ---- 3. 逐 bin 画柱子 ----
    const gap = 1.0;
    final n = bins.length;
    final bw = (size.width - gap * (n - 1)) / n;
    if (bw <= 0) return;

    final double baseline = size.height - barBottomPadding;

    for (var i = 0; i < n; i++) {
      var t = (bins[i] - minV) / range;
      if (t < 0) t = 0;
      if (t > 1) t = 1;

      final h = t * baseline;
      if (h < 0.5) continue;

      final left = i * (bw + gap);
      final top = baseline - h;

      final rect = RRect.fromRectAndRadius(
        Rect.fromLTWH(left, top, bw, h),
        const Radius.circular(1),
      );

      final paint = Paint()
        ..shader = LinearGradient(
          begin: Alignment.bottomCenter,
          end: Alignment.topCenter,
          colors: [primary, accent],
        ).createShader(Rect.fromLTWH(left, top, bw, h));

      canvas.drawRRect(rect, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _SpectrumPainter old) {
    return !identical(old.bins, bins) ||
        old.primary != primary ||
        old.accent != accent;
  }
}

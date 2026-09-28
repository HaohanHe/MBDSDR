// 统一频谱显示：频谱轨迹 + 共享频率刻度条 + 瀑布，左右边严格对齐。
//
// 设计目标：桌面级科研仪器质感（对齐 SDR++ / SDRConsole 的克制深色风）。
// 几何（竖屏 Column，全部用 token 间距派生，不写死像素）：
//   频谱 Expanded(flex:5) → 频率条(fixed) → 瀑布 Expanded(flex:4)
// 每一行 Row：[左 dB gutter 数字] [绘图区] [右 dB gutter 刻度线]，三行 gutter 对齐。
// 无数据时本 widget 不画任何东西（由外层页面给诚实空态）。
//
// 性能：瀑布用原始 RGBA 像素缓冲 + ui.Image 逐帧下移（底部最新帧），
// 颜色由 AppTokens.waterfallColorFor 连续插值（非离散色块）。
library;

import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../dsp/fft_processor.dart';

class SpectrumDisplay extends StatefulWidget {
  /// 最新一帧频谱；为 null 时本 widget 不绘制（外层空态负责提示）。
  final SpectrumFrame? frame;

  /// 当前解调信道带宽（Hz），在频谱中央画一条细竖带。
  final double channelBandwidthHz;

  /// 点按某个 bin 时回调其中心频率（Hz），用于真实改频率。
  final ValueChanged<double>? onTapFrequency;

  const SpectrumDisplay({
    super.key,
    required this.frame,
    this.channelBandwidthHz = 12500,
    this.onTapFrequency,
  });

  @override
  State<SpectrumDisplay> createState() => _SpectrumDisplayState();
}

class _SpectrumDisplayState extends State<SpectrumDisplay> {
  // 瀑布像素缓冲（RGBA8888）。
  Uint8List? _rgba;
  int _w = 0;
  int _h = 0;
  ui.Image? _image;
  SpectrumFrame? _lastFrame;

  // gutter / 频率条高度（由 token 间距派生，不写死魔法像素）。
  double get _leftGutterW => AppTokens.spacingL * 3.0;
  double get _rightGutterW => AppTokens.spacingL * 1.5;
  double get _freqStripH => AppTokens.spacingL * 2.4;

  @override
  void didUpdateWidget(covariant SpectrumDisplay oldWidget) {
    super.didUpdateWidget(oldWidget);
    final f = widget.frame;
    if (f != null && !identical(f, _lastFrame)) {
      _lastFrame = f;
      _ingestFrame(f);
    }
  }

  void _ingestFrame(SpectrumFrame frame) {
    final rgba = _rgba;
    if (rgba == null || _w == 0 || _h == 0) return;
    // 把已有内容上移一行（丢最旧行），新帧写到底部（最新）。
    final rowBytes = _w * 4;
    rgba.setRange(0, rgba.length - rowBytes, rgba, rowBytes);
    final db = frame.db;
    final n = db.length;
    final rowBase = (_h - 1) * rowBytes;
    for (var x = 0; x < _w; x++) {
      // 列 → bin（居中映射）。
      final bin = (x / _w * n).floor().clamp(0, n - 1);
      final c = AppTokens.waterfallColorFor(db[bin]);
      final o = rowBase + x * 4;
      rgba[o] = (c.r * 255).round();
      rgba[o + 1] = (c.g * 255).round();
      rgba[o + 2] = (c.b * 255).round();
      rgba[o + 3] = 255;
    }
    ui.decodeImageFromPixels(
      Uint8List.fromList(rgba),
      _w,
      _h,
      ui.PixelFormat.rgba8888,
      (img) {
        if (!mounted) return;
        _image?.dispose();
        setState(() => _image = img);
      },
    );
  }

  void _onTapPlotTapUp(TapUpDetails d, Size plotSize, SpectrumFrame frame) {
    final onTap = widget.onTapFrequency;
    if (onTap == null) return;
    final frac = (d.localPosition.dx / plotSize.width).clamp(0.0, 1.0);
    final bin = (frac * frame.fftSize).floor();
    onTap(frame.binToHz(bin));
  }

  @override
  Widget build(BuildContext context) {
    final frame = widget.frame;
    if (frame == null) return const SizedBox.shrink();

    return Column(
      children: [
        // ------------------------------------------------ 频谱轨迹
        Expanded(
          flex: 5,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SizedBox(
                width: _leftGutterW,
                child: const CustomPaint(painter: _LeftDbGutterPainter()),
              ),
              Expanded(
                child: LayoutBuilder(
                  builder: (context, constraints) {
                    return GestureDetector(
                      onTapUp: (d) => _onTapPlotTapUp(d, constraints.biggest, frame),
                      child: CustomPaint(
                        size: constraints.biggest,
                        painter: _SpectrumPainter(
                          frame: frame,
                          channelBandwidthHz: widget.channelBandwidthHz,
                        ),
                      ),
                    );
                  },
                ),
              ),
              SizedBox(
                width: _rightGutterW,
                child: const CustomPaint(painter: _RightDbGutterPainter()),
              ),
            ],
          ),
        ),
        // ------------------------------------------------ 频率刻度条
        SizedBox(
          height: _freqStripH,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SizedBox(width: _leftGutterW),
              Expanded(
                child: CustomPaint(
                  painter: _FreqStripPainter(frame: frame),
                ),
              ),
              SizedBox(width: _rightGutterW),
            ],
          ),
        ),
        // ------------------------------------------------ 瀑布
        Expanded(
          flex: 4,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SizedBox(width: _leftGutterW),
              Expanded(
                child: LayoutBuilder(
                  builder: (context, constraints) {
                    _ensureBuffer(constraints.biggest);
                    return CustomPaint(
                      size: constraints.biggest,
                      painter: _WaterfallPainter(image: _image),
                    );
                  },
                ),
              ),
              SizedBox(width: _rightGutterW),
            ],
          ),
        ),
      ],
    );
  }

  void _ensureBuffer(Size size) {
    final dpr = View.of(context).devicePixelRatio;
    final w = (size.width * dpr).round().clamp(1, 4096);
    final h = (size.height * dpr).round().clamp(1, 256);
    if (w == _w && h == _h && _rgba != null) return;
    _w = w;
    _h = h;
    _image?.dispose();
    _image = null;
    _rgba = Uint8List(w * h * 4); // 默认黑底。
  }
}

/// dB → 绘图区纵向坐标（顶部=0 dB，底部=lower）。
double _dbToY(double db, double plotH) {
  const lower = AppTokens.dbLowerDefault;
  const upper = AppTokens.dbUpperDefault;
  final c = db.clamp(lower, upper);
  return (1 - (c - lower) / (upper - lower)) * plotH;
}

/// 左侧 dB gutter：每 dbGridStep 一条网格标签（mono、右对齐、淡）。
class _LeftDbGutterPainter extends CustomPainter {
  const _LeftDbGutterPainter();

  @override
  void paint(Canvas canvas, Size size) {
    const lower = AppTokens.dbLowerDefault;
    const upper = AppTokens.dbUpperDefault;
    const step = AppTokens.dbGridStep;
    final tp = TextPainter(
      textDirection: TextDirection.ltr,
      textAlign: TextAlign.right,
    );
    for (var db = lower; db <= upper + 0.01; db += step) {
      final y = _dbToY(db, size.height);
      tp.text = TextSpan(
        text: '${db.toInt()}',
        style: AppTokens.mono.copyWith(
          fontSize: 10,
          color: AppTokens.textSecondary,
        ),
      );
      tp.layout();
      final ly = y.clamp(tp.height / 2, size.height - tp.height / 2);
      tp.paint(canvas, Offset(size.width - tp.width - 3, ly - tp.height / 2));
    }
  }

  @override
  bool shouldRepaint(covariant _LeftDbGutterPainter oldDelegate) => false;
}

/// 右侧 dB gutter：只画短刻度线（与网格对齐），不重复数字，避免与曲线打架。
class _RightDbGutterPainter extends CustomPainter {
  const _RightDbGutterPainter();

  @override
  void paint(Canvas canvas, Size size) {
    const lower = AppTokens.dbLowerDefault;
    const upper = AppTokens.dbUpperDefault;
    const step = AppTokens.dbGridStep;
    final paint = Paint()
      ..color = AppTokens.textAt(AppTokens.textAlphaFaint)
      ..strokeWidth = 1;
    for (var db = lower; db <= upper + 0.01; db += step) {
      final y = _dbToY(db, size.height);
      canvas.drawLine(Offset(size.width - 5, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(covariant _RightDbGutterPainter oldDelegate) => false;
}

/// 频谱轨迹：背景 + dB 网格 + 中央信道带宽竖带 + 平滑轨迹 + VFO 竖线 + BW 角标。
class _SpectrumPainter extends CustomPainter {
  final SpectrumFrame frame;
  final double channelBandwidthHz;

  _SpectrumPainter({required this.frame, required this.channelBandwidthHz});

  @override
  void paint(Canvas canvas, Size size) {
    // 背景。
    canvas.drawRect(
      Offset.zero & size,
      Paint()..color = AppTokens.spectrumBg,
    );

    const lower = AppTokens.dbLowerDefault;
    const upper = AppTokens.dbUpperDefault;
    const step = AppTokens.dbGridStep;

    // 横向 dB 网格线。
    final grid = Paint()
      ..color = AppTokens.textAt(0.08)
      ..strokeWidth = 1;
    for (var db = lower; db <= upper + 0.01; db += step) {
      final y = _dbToY(db, size.height);
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }
    // 中央纵向参考线（弱）。
    canvas.drawLine(
      Offset(size.width / 2, 0),
      Offset(size.width / 2, size.height),
      grid,
    );

    final db = frame.db;
    final n = db.length;
    double yFor(double v) => _dbToY(v, size.height);

    // 解调信道带宽竖带（居中 ±bw/2）。
    final halfBw = channelBandwidthHz / 2;
    final binHz = frame.binWidthHz;
    final halfBins = (halfBw / binHz).round();
    final centerBin = n ~/ 2;
    final bandL = (centerBin - halfBins) / n * size.width;
    final bandR = (centerBin + halfBins) / n * size.width;
    canvas.drawRect(
      Rect.fromLTRB(bandL, 0, bandR, size.height),
      Paint()..color = AppTokens.accent.withValues(alpha: 0.08),
    );

    // ---- 采样到像素列 + 轻量平滑（3-tap 均值），去锯齿折线 ----
    final samples = (size.width / 2).clamp(120.0, 500.0).round();
    final xs = List<double>.generate(samples, (k) => k / (samples - 1) * size.width);
    final ys = List<double>.generate(samples, (k) {
      final bin = (k / (samples - 1) * (n - 1)).round().clamp(0, n - 1);
      return yFor(db[bin]);
    });
    for (var k = 1; k < samples - 1; k++) {
      ys[k] = (ys[k - 1] + ys[k] + ys[k + 1]) / 3;
    }

    final trace = Path()..moveTo(xs[0], ys[0]);
    final fill = Path()..moveTo(xs[0], size.height);
    for (var k = 0; k < samples; k++) {
      trace.lineTo(xs[k], ys[k]);
      fill.lineTo(xs[k], ys[k]);
    }
    fill
      ..lineTo(size.width, size.height)
      ..close();

    canvas.drawPath(
      fill,
      Paint()..color = AppTokens.accent.withValues(alpha: 0.10),
    );
    canvas.drawPath(
      trace,
      Paint()
        ..color = AppTokens.accent
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.4
        ..isAntiAlias = true,
    );

    // VFO 光标线（中央，清晰 accent）。
    final vfo = Paint()
      ..color = AppTokens.accentHover
      ..strokeWidth = 1;
    canvas.drawLine(
      Offset(size.width / 2, 0),
      Offset(size.width / 2, size.height),
      vfo,
    );
    // 顶部小三角 VFO 标记。
    final tri = Path()
      ..moveTo(size.width / 2 - 4, 0)
      ..lineTo(size.width / 2 + 4, 0)
      ..lineTo(size.width / 2, 6)
      ..close();
    canvas.drawPath(tri, Paint()..color = AppTokens.accentHover);

    // 右上角带宽角标（mono、淡）。
    final tp = TextPainter(textDirection: TextDirection.ltr);
    tp.text = TextSpan(
      text: 'BW ${(frame.sampleRateHz / 1e6).toStringAsFixed(3)}M',
      style: AppTokens.mono.copyWith(
        fontSize: 10,
        color: AppTokens.textAt(0.65),
      ),
    );
    tp.layout();
    tp.paint(canvas, Offset(size.width - tp.width - 6, 4));
  }

  @override
  bool shouldRepaint(covariant _SpectrumPainter oldDelegate) =>
      !identical(oldDelegate.frame, frame) ||
      oldDelegate.channelBandwidthHz != channelBandwidthHz;
}

/// 频率刻度条：按可用宽度自适应选步长（100k/250k/500k/1M/2M/5M/10M），
/// 标签不重叠、不裁切；中央 VFO 中心频率高亮。
class _FreqStripPainter extends CustomPainter {
  final SpectrumFrame frame;
  _FreqStripPainter({required this.frame});

  // (步长 Hz, MHz 小数位) —— 从小到大，选第一个能放下的步长。
  static const List<(double, int)> _steps = [
    (1e5, 2),
    (2.5e5, 2),
    (5e5, 2),
    (1e6, 1),
    (2e6, 1),
    (5e6, 0),
    (1e7, 0),
  ];

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(
      Offset.zero & size,
      Paint()..color = AppTokens.spectrumBg,
    );
    final span = frame.sampleRateHz;
    final center = frame.centerFreqHz;
    final left = center - span / 2;
    final right = center + span / 2;
    double xFor(double f) => (f - left) / span * size.width;

    // 选步长：相邻刻度间距 >= minGap，标签才不重叠。
    const minGap = 64.0;
    var step = _steps.last.$1;
    var decimals = _steps.last.$2;
    for (final s in _steps) {
      if (s.$1 / span * size.width >= minGap) {
        step = s.$1;
        decimals = s.$2;
        break;
      }
    }

    final edge = Paint()
      ..color = AppTokens.cardEdge
      ..strokeWidth = 1;
    // 顶部分隔线。
    canvas.drawLine(const Offset(0, 0), Offset(size.width, 0), edge);

    final tp = TextPainter(textDirection: TextDirection.ltr);

    // 普通网格刻度。
    var tickF = (left / step).ceil() * step;
    while (tickF <= right + 1) {
      final x = xFor(tickF);
      canvas.drawLine(Offset(x, 0), Offset(x, 6), edge);
      tp.text = TextSpan(
        text: (tickF / 1e6).toStringAsFixed(decimals),
        style: AppTokens.mono.copyWith(
          fontSize: 10,
          color: AppTokens.textAt(0.60),
        ),
      );
      tp.layout();
      var lx = x - tp.width / 2;
      lx = lx.clamp(0.0, size.width - tp.width); // 不裁切边缘标签
      tp.paint(canvas, Offset(lx, size.height * 0.42));
      tickF += step;
    }

    // VFO 中心频率高亮刻度（中央）。
    final cx = size.width / 2;
    canvas.drawLine(
      Offset(cx, 0),
      Offset(cx, 10),
      Paint()
        ..color = AppTokens.accent
        ..strokeWidth = 1.2,
    );
    tp.text = TextSpan(
      text: (center / 1e6).toStringAsFixed(decimals),
      style: AppTokens.mono.copyWith(
        fontSize: 10,
        color: AppTokens.accent,
        fontWeight: FontWeight.w600,
      ),
    );
    tp.layout();
    var clx = cx - tp.width / 2;
    clx = clx.clamp(0.0, size.width - tp.width);
    tp.paint(canvas, Offset(clx, size.height * 0.42));
  }

  @override
  bool shouldRepaint(covariant _FreqStripPainter oldDelegate) =>
      !identical(oldDelegate.frame, frame);
}

/// 瀑布：把累积的 RGBA 像素缓冲画成一张逐帧下移的连续色带图。
class _WaterfallPainter extends CustomPainter {
  final ui.Image? image;
  _WaterfallPainter({required this.image});

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(
      Offset.zero & size,
      Paint()..color = AppTokens.spectrumBg,
    );
    // 中央 VFO 细线（与上方轨迹对齐）。
    canvas.drawLine(
      Offset(size.width / 2, 0),
      Offset(size.width / 2, size.height),
      Paint()..color = AppTokens.textAt(0.12),
    );
    final img = image;
    if (img == null) return;
    canvas.drawImageRect(
      img,
      Rect.fromLTWH(0, 0, img.width.toDouble(), img.height.toDouble()),
      Offset.zero & size,
      Paint()..filterQuality = FilterQuality.low,
    );
  }

  @override
  bool shouldRepaint(covariant _WaterfallPainter oldDelegate) =>
      !identical(oldDelegate.image, image);
}

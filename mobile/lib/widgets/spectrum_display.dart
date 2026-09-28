// 统一频谱显示：频谱轨迹 + 共享频率刻度条 + 瀑布，左右边严格对齐。
//
// 几何（竖屏 Column）：
//   频谱 Expanded(flex:5) → 约 28dp 频率条 → 瀑布 Expanded(flex:4)
// 左侧统一 gutter 宽度画 dB 网格标签；瀑布用原始 RGBA 像素缓冲 + ui.Image 高效滚动。
// 无数据时本 widget 不画任何东西（由外层页面给空态）。
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

  // gutter 宽度（由 token 间距派生，不写死魔法像素）。
  double get _gutterW => AppTokens.spacingL * 3;
  double get _freqStripH => AppTokens.spacingL * 1.75;

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
    // 把已有内容上移一行（丢最旧行）。
    final rowBytes = _w * 4;
    rgba.setRange(0, (rgba.length - rowBytes), rgba, rowBytes);
    // 写最新一行到底部。
    final db = frame.db;
    final n = db.length;
    for (var x = 0; x < _w; x++) {
      // 列 → bin（居中映射）。
      final bin = (x / _w * n).floor().clamp(0, n - 1);
      final c = AppTokens.waterfallColorFor(db[bin]);
      final o = (_h - 1) * rowBytes + x * 4;
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
                width: _gutterW,
                child: const CustomPaint(painter: _DbGutterPainter()),
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
            ],
          ),
        ),
        // ------------------------------------------------ 频率刻度条
        SizedBox(
          height: _freqStripH,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SizedBox(width: _gutterW),
              Expanded(
                child: CustomPaint(
                  painter: _FreqStripPainter(frame: frame),
                ),
              ),
            ],
          ),
        ),
        // ------------------------------------------------ 瀑布
        Expanded(
          flex: 4,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SizedBox(width: _gutterW),
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
            ],
          ),
        ),
      ],
    );
  }

  void _ensureBuffer(Size size) {
    final dpr = View.of(context).devicePixelRatio;
    final w = (size.width * dpr).round().clamp(1, 4096);
    final h = ((size.height * dpr).round()).clamp(1, 256);
    if (w == _w && h == _h && _rgba != null) return;
    _w = w;
    _h = h;
    _image?.dispose();
    _image = null;
    _rgba = Uint8List(w * h * 4); // 默认黑底。
  }
}

/// 左侧 dB gutter：每 20dB 一条网格线 + 标签。
class _DbGutterPainter extends CustomPainter {
  const _DbGutterPainter();
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
      final y = (1 - (db - lower) / (upper - lower)) * size.height;
      tp.text = TextSpan(
        text: '${db.toInt()}',
        style: AppTokens.mono.copyWith(fontSize: 9, color: AppTokens.textAt(AppTokens.textAlphaFaint)),
      );
      tp.layout();
      tp.paint(canvas, Offset(size.width - tp.width - 2, y - tp.height / 2));
    }
  }

  @override
  bool shouldRepaint(covariant _DbGutterPainter oldDelegate) => false;
}

/// 频谱轨迹：网格 + accent 亮线 + 下方淡填充 + 中央信道带宽竖带。
class _SpectrumPainter extends CustomPainter {
  final SpectrumFrame frame;
  final double channelBandwidthHz;

  _SpectrumPainter({required this.frame, required this.channelBandwidthHz});

  @override
  void paint(Canvas canvas, Size size) {
    const lower = AppTokens.dbLowerDefault;
    const upper = AppTokens.dbUpperDefault;
    const step = AppTokens.dbGridStep;

    // 横向网格。
    final grid = Paint()
      ..color = AppTokens.textAt(0.08)
      ..strokeWidth = 1;
    for (var db = lower; db <= upper + 0.01; db += step) {
      final y = (1 - (db - lower) / (upper - lower)) * size.height;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }

    final db = frame.db;
    final n = db.length;
    double yFor(double v) {
      final clamped = v.clamp(lower, upper);
      return (1 - (clamped - lower) / (upper - lower)) * size.height;
    }

    // 信道带宽竖带（居中 ±bw/2）。
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

    // 轨迹线 + 填充。
    final path = Path();
    final fill = Path()..moveTo(0, size.height);
    for (var i = 0; i < n; i++) {
      final x = i / (n - 1) * size.width;
      final y = yFor(db[i]);
      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
      fill.lineTo(x, y);
    }
    fill.lineTo(size.width, size.height);
    fill.close();

    canvas.drawPath(
      fill,
      Paint()..color = AppTokens.accent.withValues(alpha: 0.12),
    );
    canvas.drawPath(
      path,
      Paint()
        ..color = AppTokens.accent
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2,
    );
  }

  @override
  bool shouldRepaint(covariant _SpectrumPainter oldDelegate) =>
      !identical(oldDelegate.frame, frame) ||
      oldDelegate.channelBandwidthHz != channelBandwidthHz;
}

/// 中间频率刻度条：5 个刻度，mono 字体，与频谱 gutter 对齐。
class _FreqStripPainter extends CustomPainter {
  final SpectrumFrame frame;
  _FreqStripPainter({required this.frame});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = AppTokens.cardEdge
      ..strokeWidth = 1;
    const ticks = 5;
    final tp = TextPainter(textDirection: TextDirection.ltr);
    for (var k = 0; k < ticks; k++) {
      final frac = k / (ticks - 1);
      final x = frac * size.width;
      canvas.drawLine(Offset(x, 0), Offset(x, size.height * 0.4), paint);
      final hz = frame.centerFreqHz + (frac - 0.5) * frame.sampleRateHz;
      final mhz = hz / 1e6;
      tp.text = TextSpan(
        text: mhz.toStringAsFixed(3),
        style: AppTokens.mono.copyWith(fontSize: 9),
      );
      tp.layout();
      tp.paint(canvas, Offset(x - tp.width / 2, size.height * 0.45));
    }
  }

  @override
  bool shouldRepaint(covariant _FreqStripPainter oldDelegate) =>
      !identical(oldDelegate.frame, frame);
}

/// 瀑布：把累积的 RGBA 像素缓冲画成一张滚动图。
class _WaterfallPainter extends CustomPainter {
  final ui.Image? image;
  _WaterfallPainter({required this.image});

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(
      Offset.zero & size,
      Paint()..color = AppTokens.spectrumBg,
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

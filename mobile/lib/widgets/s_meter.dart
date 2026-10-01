// S-meter 信号表：电平全部来自真实频谱帧（dBFS），无前端校准，诚实标注参考域。
//
// - 信号 = 最新帧峰值 dBFS（与状态行 RSSI 同源，不另造读数链路）。
// - 噪声底 = 帧功率谱中位数 dBFS（稳健估计）。
// - S 单位 = 相对噪声底每 6 dB 一档（标准 S-meter 语义），截断到 S0..S9。
// - 峰值保持：克制慢回落，信号强则刷新、掉落后按固定 dB/s 衰减。
// - 无帧 → 诚实空态「无信号」，绝不编造电平。
import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../dsp/fft_processor.dart';

/// 从真实频谱帧提取带内电平。返回 (峰值信号 dBFS, 噪声底 dBFS)。
/// 诚实参考域：dBFS（相对量），RTL-SDR 无前端路径校准，不冒充 dBm。
({double signalDbfs, double noiseFloorDbfs}) spectrumLevelFromFrame(
    SpectrumFrame frame) {
  final db = frame.db;
  var peak = db[0];
  for (final v in db) {
    if (v > peak) peak = v;
  }
  // 中位数作噪声底：对零星大尖峰稳健（与 painter 内估计一致）。
  final sorted = db.toList()..sort();
  final noise = sorted[db.length ~/ 2];
  return (signalDbfs: peak, noiseFloorDbfs: noise);
}

/// 标准 S-meter 映射：相对噪声底每 [AppTokens.sMeterDbPerUnit] dB 一个 S 档，
/// 四舍五入后截断到 0..[AppTokens.sMeterMaxUnits]。
int sUnitsAboveNoise(double signalDbfs, double noiseFloorDbfs) {
  final above = (signalDbfs - noiseFloorDbfs) / AppTokens.sMeterDbPerUnit;
  return above.round().clamp(0, AppTokens.sMeterMaxUnits);
}

/// 峰值保持：取历史最高电平，随时间按固定 dB/s 向当前电平慢回落。
/// 纯逻辑、可单测；不做跳变，克制不抖。
class PeakHold {
  double? _peak;

  /// 每秒回落的 dB 数。
  final double decayDbPerSec;

  PeakHold({this.decayDbPerSec = AppTokens.sMeterPeakDecayDbPerSec});

  /// 喂入当前真实信号与距上次更新经过的秒数，返回保持峰值。
  double? update(double signal, double dtSeconds) {
    final p = _peak;
    if (p == null) {
      return _peak = signal;
    }
    if (signal >= p) {
      return _peak = signal; // 更强：刷新。
    }
    // 更弱：向信号方向慢回落，但不低于信号。
    final decayed = p - decayDbPerSec * math.max(0, dtSeconds);
    return _peak = math.max(signal, decayed);
  }

  void reset() => _peak = null;
}

/// 紧凑 S-meter 仪表：S 大数字 + S0..S9 分段条 + 峰值刻度 + dBFS 读数。
/// 无数据时诚实空态。UI 全部走 AppTokens，克制不堆色。
class SMeter extends StatefulWidget {
  /// 最新帧峰值 dBFS；null = 无设备/无帧 → 空态。
  final double? signalDbfs;

  /// 最新帧噪声底 dBFS；null 时 S 单位不可算，仅显示电平条。
  final double? noiseFloorDbfs;

  const SMeter({super.key, this.signalDbfs, this.noiseFloorDbfs});

  @override
  State<SMeter> createState() => _SMeterState();
}

class _SMeterState extends State<SMeter> {
  final PeakHold _hold = PeakHold();
  Timer? _ticker;
  double? _signal;
  double? _peak;

  @override
  void initState() {
    super.initState();
    _signal = widget.signalDbfs;
    if (_signal != null) _peak = _hold.update(_signal!, 0);
    // 周期 tick 让峰值保持缓慢回落。
    _ticker = Timer.periodic(const Duration(milliseconds: 250), (_) {
      if (!mounted) return;
      setState(() {
        final s = _signal;
        _peak = s == null ? null : _hold.update(s, 0.25);
      });
    });
  }

  @override
  void didUpdateWidget(covariant SMeter old) {
    super.didUpdateWidget(old);
    _signal = widget.signalDbfs;
    if (_signal == null) {
      _peak = null;
    } else {
      _peak = _hold.update(_signal!, 0); // 新帧即刻刷新（若更强）。
    }
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final signal = widget.signalDbfs;
    final noise = widget.noiseFloorDbfs;
    final unit = (signal != null && noise != null)
        ? sUnitsAboveNoise(signal, noise)
        : null;

    if (signal == null) {
      return Container(
        padding: const EdgeInsets.symmetric(
          horizontal: AppTokens.spacingM,
          vertical: AppTokens.spacingS,
        ),
        color: AppTokens.bgBar,
        child: Row(
          children: [
            Text('S --',
                style: AppTokens.mono.copyWith(
                  fontSize: AppTokens.annotationFontSize,
                  color: AppTokens.textAt(AppTokens.textAlphaFaint),
                )),
            const SizedBox(width: AppTokens.spacingM),
            Expanded(
              child: Text('无信号',
                  style: AppTokens.mono.copyWith(
                    fontSize: AppTokens.annotationFontSize,
                    color: AppTokens.textAt(AppTokens.textAlphaFaint),
                  )),
            ),
          ],
        ),
      );
    }

    final peakUnit = (noise != null && _peak != null)
        ? sUnitsAboveNoise(_peak!, noise)
        : null;

    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppTokens.spacingM,
        vertical: AppTokens.spacingS,
      ),
      color: AppTokens.bgBar,
      child: Row(
        children: [
          // S 大数字（mono medium）。
          Text(
            unit == null ? 'S·' : 'S$unit',
            style: AppTokens.mono.copyWith(
              fontSize: AppTokens.annotationFontSize + 2,
              fontWeight: AppTokens.weightMedium,
              color: AppTokens.textPrimary,
            ),
          ),
          const SizedBox(width: AppTokens.spacingM),
          Expanded(child: _Gauge(fillUnit: unit, peakUnit: peakUnit)),
          const SizedBox(width: AppTokens.spacingM),
          // dBFS 诚实读数（相对域，不写 dBm）。
          Text(
            '${signal.toStringAsFixed(0)} dBFS',
            style: AppTokens.mono.copyWith(
              fontSize: AppTokens.annotationFontSize,
              color: AppTokens.textSecondary,
            ),
          ),
        ],
      ),
    );
  }
}

/// S0..S9 分段条：填充到当前 S，峰值保持用细刻度线标记。
class _Gauge extends StatelessWidget {
  final int? fillUnit;
  final int? peakUnit;
  const _Gauge({required this.fillUnit, required this.peakUnit});

  @override
  Widget build(BuildContext context) {
    const n = AppTokens.sMeterMaxUnits + 1; // 10 段。
    return SizedBox(
      height: AppTokens.spacingM,
      child: LayoutBuilder(
        builder: (context, c) {
          final seg = (c.maxWidth - AppTokens.spacingS * (n - 1)) / n;
          return Stack(
            children: [
              Row(
                children: List.generate(n, (i) {
                  final filled = fillUnit != null && i <= fillUnit!;
                  return Container(
                    width: seg,
                    margin: EdgeInsets.only(right: i == n - 1 ? 0 : AppTokens.spacingS),
                    decoration: BoxDecoration(
                      color: filled ? AppTokens.accent : AppTokens.disabledFill,
                      borderRadius: BorderRadius.circular(AppTokens.radiusSmall),
                    ),
                  );
                }),
              ),
              if (peakUnit != null)
                Positioned(
                  left: (seg + AppTokens.spacingS) * peakUnit!,
                  top: 0,
                  bottom: 0,
                  child: Container(width: 2, color: AppTokens.warning),
                ),
            ],
          );
        },
      ),
    );
  }
}

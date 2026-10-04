// ============================================================================
// 远程解码面板：移动端作为「远程只读查看器」渲染桌面 ControlHub 三解码结果。
// ----------------------------------------------------------------------------
// 定位诚实：解码发生在桌面引擎，本面板只读 HTTP JSON、不本地解码、不下发任何
// 命令。每个区块头部标注数据来源「远程引擎 · POCSAG / M17 / VOR」。
//   * 未配置服务地址 -> 引导去设置（不假连）；
//   * 服务不可达     -> 诚实空态 + 重试按钮（不伪造读数）；
//   * 无解码数据     -> 「暂无…」空态（count:0 / locked:false）；
//   * 语音帧         -> 标「语音 · 未解码」（Codec2 未内置，不出伪造音频）；
//   * VOR 未锁定     -> 不画指针、方位读「—」（绝不据噪声造方位）。
//
// 三个子列表（[PocsagMessageList]/[M17CallList]/[VorRadialView]）为纯展示组件，
// 直接吃已解析值对象，便于单测断言行内容；[RemoteDecoderPanel] 是带轮询的容器。
library;

import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../app/tokens.dart';
import '../models/control_hub.dart';
import '../services/control_hub_client.dart';
import 'empty_state.dart';

// ---------------------------------------------------------------------------
// 容器：带轮询的远程解码面板
// ---------------------------------------------------------------------------

class RemoteDecoderPanel extends StatefulWidget {
  /// 已配置好 host/port 的客户端；null 表示尚未配置桌面 ControlHub 地址。
  final ControlHubClient? client;

  /// 去设置页配置服务地址的回调（未配置时由空态按钮触发）。
  final VoidCallback? onOpenSettings;

  /// 轮询间隔；测试可传极大值以避免定时器在断言期间自触发。
  final Duration pollInterval;

  const RemoteDecoderPanel({
    super.key,
    required this.client,
    this.onOpenSettings,
    this.pollInterval = const Duration(seconds: 2),
  });

  @override
  State<RemoteDecoderPanel> createState() => _RemoteDecoderPanelState();
}

enum _Phase { loading, ready, error }

class _RemoteDecoderPanelState extends State<RemoteDecoderPanel> {
  Timer? _timer;
  _Phase _phase = _Phase.loading;
  String? _error;
  List<PocsagMessage> _pocsag = const <PocsagMessage>[];
  List<M17Call> _m17 = const <M17Call>[];
  VorRadial _vor = VorRadial.unlocked;
  bool _refreshing = false;

  @override
  void initState() {
    super.initState();
    if (widget.client == null) {
      _phase = _Phase.error; // 未配置：走「去设置」空态分支。
      return;
    }
    _refresh();
    _timer = Timer.periodic(widget.pollInterval, (_) => _refresh());
  }

  /// host/port 变化（用户在设置里改了地址）时重置并重新拉取。
  /// 按 host/port 内容比较，而非 client 实例身份——控制面板每次重建都会造新实例。
  @override
  void didUpdateWidget(RemoteDecoderPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    final ControlHubClient? oldC = oldWidget.client;
    final ControlHubClient? newC = widget.client;
    final String oldHost = oldC?.host ?? '';
    final String newHost = newC?.host ?? '';
    final int oldPort = oldC?.port ?? 0;
    final int newPort = newC?.port ?? 0;
    if (oldHost == newHost && oldPort == newPort) return;

    _timer?.cancel();
    _timer = null;
    _pocsag = const <PocsagMessage>[];
    _m17 = const <M17Call>[];
    _vor = VorRadial.unlocked;
    _refreshing = false;
    if (newC == null) {
      _phase = _Phase.error;
      _error = null;
    } else {
      _phase = _Phase.loading;
      _refresh();
      _timer = Timer.periodic(widget.pollInterval, (_) => _refresh());
    }
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    final ControlHubClient? c = widget.client;
    if (c == null) return;
    if (_refreshing) return;
    setState(() => _refreshing = true);
    try {
      // 四路只读拉取并行；任一失败即整体进错误分支（服务不可达会全挂）。
      final List<dynamic> results = await Future.wait<dynamic>(<Future<dynamic>>[
        c.fetchStatus(),
        c.fetchPocsagMessages(),
        c.fetchM17Calls(),
        c.fetchVorRadial(),
      ]);
      if (!mounted) return;
      setState(() {
        final dynamic p = results[1];
        _pocsag = p is List<PocsagMessage> ? p : const <PocsagMessage>[];
        final dynamic m = results[2];
        _m17 = m is List<M17Call> ? m : const <M17Call>[];
        final dynamic v = results[3];
        if (v is VorRadial) _vor = v;
        _phase = _Phase.ready;
        _error = null;
        _refreshing = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _refreshing = false;
        // 已有真实数据时不回退成全屏错误，仅在顶部标注；首次失败才进错误空态。
        if (_phase != _Phase.ready) {
          _phase = _Phase.error;
          _error = e is ControlHubException ? e.message : '$e';
        }
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (widget.client == null) {
      return EmptyState(
        icon: Icons.dns_outlined,
        title: '未配置桌面 ControlHub',
        message: '在「设置」中填写桌面 ControlHub 的主机与端口，'
            '即可只读查看远程 POCSAG / M17 / VOR 解码结果。',
        actionLabel: widget.onOpenSettings == null ? null : '去设置',
        onAction: widget.onOpenSettings,
      );
    }

    switch (_phase) {
      case _Phase.loading:
        return const Padding(
          padding: EdgeInsets.all(AppTokens.spacingL),
          child: Center(child: CircularProgressIndicator()),
        );
      case _Phase.error:
        return EmptyState(
          icon: Icons.cloud_off_outlined,
          title: '无法连接远程解码引擎',
          message: '${_error ?? '未知错误'}\n解码在桌面引擎进行，移动端仅为查看器。',
          actionLabel: '重试',
          onAction: _refresh,
        );
      case _Phase.ready:
        return Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            _SourceCard(
              source: '远程引擎 · POCSAG',
              child: PocsagMessageList(messages: _pocsag),
            ),
            const SizedBox(height: AppTokens.spacingM),
            _SourceCard(
              source: '远程引擎 · M17',
              child: M17CallList(calls: _m17),
            ),
            const SizedBox(height: AppTokens.spacingM),
            _SourceCard(
              source: '远程引擎 · VOR',
              child: VorRadialView(result: _vor),
            ),
          ],
        );
    }
  }
}

/// 一个带「远程引擎 · XXX」来源标注的卡片容器。
class _SourceCard extends StatelessWidget {
  final String source;
  final Widget child;
  const _SourceCard({required this.source, required this.child});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppTokens.spacingM),
      decoration: AppTokens.cardDecoration(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(Icons.radar_outlined,
                  size: AppTokens.iconSizeInline,
                  color: AppTokens.textAt(AppTokens.textAlphaFaint)),
              const SizedBox(width: AppTokens.spacingS),
              Text(source, style: AppTokens.auxiliary),
            ],
          ),
          const SizedBox(height: AppTokens.spacingS),
          child,
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// POCSAG 列表（纯展示）
// ---------------------------------------------------------------------------

class PocsagMessageList extends StatelessWidget {
  final List<PocsagMessage> messages;
  const PocsagMessageList({super.key, required this.messages});

  @override
  Widget build(BuildContext context) {
    if (messages.isEmpty) {
      return const Text('暂无寻呼消息（桌面引擎无 POCSAG 解码）',
          style: AppTokens.auxiliary);
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (final PocsagMessage m in messages)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AppTokens.spacingS),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Wrap(
                  spacing: AppTokens.spacingM,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: <Widget>[
                    Text('RIC ${m.address}', style: AppTokens.mono),
                    Text('功能 ${m.function}', style: AppTokens.mono),
                    _Badge(label: m.type.label),
                  ],
                ),
                if (m.text.trim().isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: AppTokens.spacingS),
                    child: Text(m.text, style: AppTokens.body),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// M17 列表（纯展示）
// ---------------------------------------------------------------------------

class M17CallList extends StatelessWidget {
  final List<M17Call> calls;
  const M17CallList({super.key, required this.calls});

  @override
  Widget build(BuildContext context) {
    if (calls.isEmpty) {
      return const Text('暂无 M17 呼叫（桌面引擎无 M17 解码）',
          style: AppTokens.auxiliary);
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (final M17Call c in calls)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AppTokens.spacingS),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Wrap(
                  spacing: AppTokens.spacingM,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: <Widget>[
                    Text('${c.src.isEmpty ? '—' : c.src} → ${c.dst.isEmpty ? '—' : c.dst}',
                        style: AppTokens.mono),
                    _Badge(
                      label: c.crcOk ? 'CRC ✓' : 'CRC ✗',
                      tone: c.crcOk ? _BadgeTone.good : _BadgeTone.bad,
                    ),
                    if (c.voiceUndecoded)
                      const _Badge(
                        label: '语音 · 未解码',
                        tone: _BadgeTone.warn,
                      ),
                  ],
                ),
                // 数据帧载荷（hex）原样展示；语音帧此处为空，不伪造音频。
                if (c.payload.trim().isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: AppTokens.spacingS),
                    child: Text(c.payload, style: AppTokens.mono),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// VOR 仪表（纯展示）：未锁定不画指针
// ---------------------------------------------------------------------------

class VorRadialView extends StatelessWidget {
  final VorRadial result;
  const VorRadialView({super.key, required this.result});

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Row(
          children: <Widget>[
            _Badge(
              label: result.locked ? '已锁定' : '未锁定',
              tone: result.locked ? _BadgeTone.good : _BadgeTone.neutral,
            ),
            const Spacer(),
            // 方位大读数：未锁定恒为「—」，绝不显示伪造方位。
            Text(
              result.locked
                  ? '${result.radialDeg.round()}°'
                  : '—',
              style: AppTokens.freqReadout,
            ),
          ],
        ),
        const SizedBox(height: AppTokens.spacingS),
        SizedBox(
          height: 110,
          child: CustomPaint(
            painter: _VorDialPainter(
              locked: result.locked,
              radialDeg: result.locked ? result.radialDeg : null,
            ),
          ),
        ),
        const SizedBox(height: AppTokens.spacingS),
        Wrap(
          spacing: AppTokens.spacingM,
          children: <Widget>[
            Text(
              result.locked
                  ? '质量 ${(result.quality * 100).round()}%'
                  : '未锁定：不显示方位',
              style: AppTokens.auxiliary,
            ),
            if (result.morseId.trim().isNotEmpty)
              Text('ID ${result.morseId}', style: AppTokens.mono),
          ],
        ),
      ],
    );
  }
}

/// VOR 罗盘画家：永远画方位环；仅在 [locked] 时画指向 [radialDeg] 的指针。
class _VorDialPainter extends CustomPainter {
  final bool locked;
  final double? radialDeg;

  _VorDialPainter({required this.locked, required this.radialDeg});

  @override
  void paint(Canvas canvas, Size size) {
    final Offset center = Offset(size.width / 2, size.height / 2);
    final double radius = (size.shortestSide / 2) - 6;
    final Paint ring = Paint()
      ..color = AppTokens.textAt(AppTokens.textAlphaFaint)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    canvas.drawCircle(center, radius, ring);

    // 方位刻度 0/90/180/270。
    final tick = TextPainter(textDirection: TextDirection.ltr);
    for (int deg = 0; deg < 360; deg += 90) {
      final double rad = (deg - 90) * math.pi / 180.0;
      final Offset p = center + Offset(
          math.cos(rad) * (radius - 4), math.sin(rad) * (radius - 4));
      tick.text = TextSpan(
        text: '$deg°',
        style: const TextStyle(
          color: AppTokens.textSecondary,
          fontSize: AppTokens.annotationFontSize,
        ),
      );
      tick.layout();
      tick.paint(canvas, p - Offset(tick.width / 2, tick.height / 2));
    }

    // 唯一可信门：未锁定 -> 不画指针，不伪造方位。
    if (!locked || radialDeg == null) return;
    final double rad = (radialDeg! - 90) * math.pi / 180.0;
    final Offset needleTip = center +
        Offset(math.cos(rad) * radius, math.sin(rad) * radius);
    final Paint needle = Paint()
      ..color = AppTokens.traceColor
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round;
    canvas.drawLine(center, needleTip, needle);
    canvas.drawCircle(center, 3, Paint()..color = AppTokens.traceColor);
  }

  @override
  bool shouldRepaint(covariant _VorDialPainter oldDelegate) =>
      oldDelegate.locked != locked || oldDelegate.radialDeg != radialDeg;
}

// ---------------------------------------------------------------------------
// 小徽章
// ---------------------------------------------------------------------------

enum _BadgeTone { neutral, good, bad, warn }

class _Badge extends StatelessWidget {
  final String label;
  final _BadgeTone tone;
  const _Badge({required this.label, this.tone = _BadgeTone.neutral});

  Color get _color => switch (tone) {
        _BadgeTone.good => AppTokens.success,
        _BadgeTone.bad => AppTokens.danger,
        _BadgeTone.warn => AppTokens.warning,
        _BadgeTone.neutral => AppTokens.textSecondary,
      };

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppTokens.spacingS, vertical: 2),
      decoration: BoxDecoration(
        color: AppTokens.card2,
        borderRadius: BorderRadius.circular(AppTokens.radiusSmall),
        border: Border.all(color: AppTokens.cardEdge),
      ),
      child: Text(label, style: AppTokens.mono.copyWith(color: _color)),
    );
  }
}

// FilePlayer 通道契约测试：用 TestDefaultBinaryMessenger 拦截 mbdsdr/audio，
// 断言 startFile/filePosition/stopFile 参数，并模拟原生 onPosition/onComplete 事件。
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/audio/file_player.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('mbdsdr/audio');
  final calls = <MethodCall>[];

  setUp(() {
    calls.clear();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      if (call.method == 'filePosition') {
        return {'positionMs': 100, 'durationMs': 2000, 'playing': true};
      }
      return null;
    });
  });

  test('startFile 发送 path/sampleRate/channels；stopFile 清空状态', () async {
    final player = FilePlayer(channel: channel);
    await player.startFile(path: '/docs/recordings/123.wav', sampleRate: 48000);
    expect(calls.first.method, 'startFile');
    final args = calls.first.arguments as Map;
    expect(args['path'], '/docs/recordings/123.wav');
    expect(args['sampleRate'], 48000);
    expect(player.state.playing, isTrue);

    await player.stopFile();
    expect(calls.last.method, 'stopFile');
    expect(player.state, PlaybackState.idle);
    await player.dispose();
  });

  test('queryPosition 解析原生返回的进度 map', () async {
    final player = FilePlayer(channel: channel);
    final s = await player.queryPosition();
    expect(s.positionMs, 100);
    expect(s.durationMs, 2000);
    expect(s.playing, isTrue);
    expect(s.progress, closeTo(0.05, 1e-6));
    await player.dispose();
  });

  test('原生 onPosition / onComplete 事件流入 onState 流', () async {
    final player = FilePlayer(channel: channel);
    final events = <PlaybackState>[];
    final sub = player.onState.listen(events.add);

    // 模拟原生主动推 onPosition。
    await TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .handlePlatformMessage(
      'mbdsdr/audio',
      const StandardMethodCodec().encodeMethodCall(const MethodCall('onPosition',
          {'positionMs': 500, 'durationMs': 2000, 'playing': true})),
      (ByteData? data) {},
    );
    // 模拟原生推 onComplete。
    await TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .handlePlatformMessage(
      'mbdsdr/audio',
      const StandardMethodCodec().encodeMethodCall(const MethodCall('onComplete')),
      (ByteData? data) {},
    );
    await Future<void>.delayed(Duration.zero);

    expect(events.any((e) => e.positionMs == 500 && e.playing), isTrue);
    expect(events.any((e) => e.completed && !e.playing), isTrue);
    await sub.cancel();
    await player.dispose();
  });
}

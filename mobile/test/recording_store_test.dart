// RecordingStore + FileRecordingSink 端到端：内存 PCM → 真 WAV → sidecar 索引 → 读回校验
// → 配对删除。全部用 Directory.systemTemp（云可写），不碰 path_provider 真机目录。
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/audio/file_recording_sink.dart';
import 'package:mbdsdr_mobile/audio/null_pcm_sink.dart';
import 'package:mbdsdr_mobile/models/recording.dart';
import 'package:mbdsdr_mobile/services/recording_store.dart';

void main() {
  group('RecordingStore 空态与端到端', () {
    test('空目录 list() 返回空（诚实空态，不预置假条目）', () async {
      final dir = await Directory.systemTemp.createTemp('recstore-empty');
      final store = RecordingStore(dir: dir);
      expect(await store.list(), isEmpty);
      await dir.delete(recursive: true);
    });

    test('端到端：FileRecordingSink 写 WAV+sidecar → store.list 读回 → 配对删除', () async {
      final dir = await Directory.systemTemp.createTemp('recstore-e2e');
      final store = RecordingStore(dir: dir);

      // 模拟一段解调音频落盘。
      final sink = FileRecordingSink(
        dir: dir,
        metaFactory: () => RecordingMeta(
          startedAtEpochMs: DateTime.utc(2026, 10, 1, 7, 25, 45)
              .millisecondsSinceEpoch,
          frequencyHz: 145800000,
          mode: 'wfm',
          deviceSource: 'connected',
        ),
      );
      await sink.start(sampleRateHz: 48000, channels: 1);
      sink.write(Float32List.fromList([1.0, -1.0, 0.0, 0.5]));
      sink.write(Float32List.fromList([-0.5, 0.25]));
      await sink.dispose();

      final meta = sink.result!;
      expect(meta.sampleCount, 6);
      expect(meta.wavFileName, endsWith('.wav'));
      expect(meta.deviceSource, 'connected');
      expect(meta.durationMs, greaterThanOrEqualTo(0));

      // WAV 文件真实存在且头自洽（dataSize = 6 样本 * 2 = 12 字节）。
      final wavFile = File('${dir.path}/${meta.wavFileName}');
      expect(wavFile.existsSync(), isTrue);
      final wavBytes = await wavFile.readAsBytes();
      expect(ByteData.sublistView(wavBytes).getUint32(40, Endian.little), 12);
      expect(wavBytes.length, 44 + 12);

      // sidecar JSON 真实落盘且可 round-trip。
      final jsonName = meta.wavFileName!.replaceAll(RegExp(r'\.wav$'), '.json');
      final sidecar = File('${dir.path}/$jsonName');
      expect(sidecar.existsSync(), isTrue);
      final decoded = jsonDecode(await sidecar.readAsString()) as Map;
      expect(decoded['sampleCount'], 6);
      expect(decoded['deviceSource'], 'connected');
      expect(decoded['frequencyHz'], 145800000);

      // store.list 读回一条。
      final list = await store.list();
      expect(list.length, 1);
      expect(list.single.frequencyHz, 145800000);
      expect(list.single.sampleCount, 6);

      // 配对删除：wav 与 json 都消失。
      await store.delete(list.single);
      expect(wavFile.existsSync(), isFalse);
      expect(sidecar.existsSync(), isFalse);
      expect(await store.list(), isEmpty);

      await dir.delete(recursive: true);
    });

    test('list 按开始时间倒序；坏 sidecar 跳过', () async {
      final dir = await Directory.systemTemp.createTemp('recstore-order');
      final store = RecordingStore(dir: dir);

      Future<void> writeOne(int stamp, int hz) async {
        final s = FileRecordingSink(
          dir: dir,
          metaFactory: () => RecordingMeta(
              startedAtEpochMs: stamp, frequencyHz: hz, mode: 'nfm'),
        );
        await s.start();
        s.write(Float32List.fromList([0.0]));
        await s.dispose();
      }

      await writeOne(1000, 144000000);
      await writeOne(3000, 145000000);
      await writeOne(2000, 146000000);

      // 注入一个损坏 sidecar（非法频率），应被跳过。
      await File('${dir.path}/9999_bad.json').writeAsString('{"startedAtEpochMs":9999,"frequencyHz":0}');
      // 注入一个 JSON 语法损坏文件。
      await File('${dir.path}/8888_broken.json').writeAsString('{not json');

      final list = await store.list();
      // 只保留 3 条合法的，坏的被跳过。
      expect(list.length, 3);
      expect(list.map((m) => m.startedAtEpochMs).toList(), [3000, 2000, 1000]);
      await dir.delete(recursive: true);
    });

    test('delete 未命中静默返回，不删目录内其他文件', () async {
      final dir = await Directory.systemTemp.createTemp('recstore-delmiss');
      final store = RecordingStore(dir: dir);
      final s = FileRecordingSink(
        dir: dir,
        metaFactory: () => const RecordingMeta(
            startedAtEpochMs: 1, frequencyHz: 1000, mode: 'nfm'),
      );
      await s.start();
      s.write(Float32List.fromList([0.0]));
      await s.dispose();
      // 删一条不存在的（时刻/频率不匹配）。
      await store.delete(const RecordingMeta(
          startedAtEpochMs: 99999, frequencyHz: 1, mode: 'nfm'));
      expect(await store.list(), hasLength(1));
      await dir.delete(recursive: true);
    });
  });

  group('FileRecordingSink 行为', () {
    test('录干净信号：不应用 volume/muted；delegate 正常转发', () async {
      final dir = await Directory.systemTemp.createTemp('recsink-behavior');
      final delegate = NoOpSink(); // 不报错即可
      final s = FileRecordingSink(
        dir: dir,
        metaFactory: () => const RecordingMeta(
            startedAtEpochMs: 1, frequencyHz: 1000, mode: 'nfm'),
        delegate: delegate,
      );
      await s.start();
      s.setVolume(0.1);
      s.setMuted(true); // 外放静音，但落盘仍应是满幅干净信号
      s.write(Float32List.fromList([1.0, -1.0]));
      await s.dispose();

      final wav = File('${dir.path}/${s.result!.wavFileName}');
      final bytes = await wav.readAsBytes();
      // 落盘 = 1.0,-1.0 → FF 7F 01 80（未被 muted 清零）。
      expect(bytes.sublist(44, 48), [0xFF, 0x7F, 0x01, 0x80]);
      await dir.delete(recursive: true);
    });
  });
}

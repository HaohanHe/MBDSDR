// 录音文件库：在应用文档目录下的 recordings/ 里真实读写 .wav + sidecar .json。
//
// 职责（对齐 B3 方案 A）：
//  - [recordingsDir]：应用文档目录/recordings/（不存在则递归创建）；测试可注入临时目录；
//  - [list]：读全部 *.json sidecar，RecordingMeta.fromJson 过滤坏项，按开始时间倒序；
//  - [delete]：按 sidecar 里的配对关系删 .wav 与 .json；任一不存在不报错。
//
// 安全红线：删除只发生在本库录音目录内——配对 .wav 的路径由 sidecar 文件名推导，
// 绝不引用目录外的路径。坏 sidecar（JSON 损坏/非法字段）被跳过，不抛、不崩。
library;

import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import '../models/recording.dart';

class RecordingStore {
  /// 测试可直接注入一个临时 [dir]；真机留空则用 path_provider 应用文档目录。
  RecordingStore({Directory? dir})
      : _injectedDir = dir;

  final Directory? _injectedDir;

  Future<Directory> get _baseDir async => _injectedDir ??
      Directory('${(await getApplicationDocumentsDirectory()).path}/recordings');

  /// 录音目录（recordings/），不存在则递归创建。
  Future<Directory> recordingsDir() async {
    final d = await _baseDir;
    if (!d.existsSync()) d.createSync(recursive: true);
    return d;
  }

  /// 列出全部真实录音（按开始时间倒序）。无 sidecar 时返回空列表——诚实空态，
  /// 不预置假条目。
  Future<List<RecordingMeta>> list() async {
    final dir = await recordingsDir();
    final out = <RecordingMeta>[];
    for (final entity in dir.listSync()) {
      if (entity is! File) continue;
      if (!entity.path.endsWith('.json')) continue;
      RecordingMeta? meta;
      try {
        meta = RecordingMeta.fromJson(jsonDecode(await entity.readAsString()));
      } catch (_) {
        meta = null; // 损坏 sidecar：跳过。
      }
      if (meta != null) out.add(meta);
    }
    out.sort((a, b) => b.startedAtEpochMs - a.startedAtEpochMs);
    return out;
  }

  /// 按「开始时刻 + 频率」定位 sidecar，配对删除其 .wav 与 .json。
  ///
  /// 只删本库录音目录内的文件；未命中静默返回；任一文件缺失不报错。
  Future<void> delete(RecordingMeta m) async {
    final dir = await recordingsDir();
    for (final entity in dir.listSync()) {
      if (entity is! File || !entity.path.endsWith('.json')) continue;
      RecordingMeta? cand;
      try {
        cand = RecordingMeta.fromJson(jsonDecode(await entity.readAsString()));
      } catch (_) {
        continue;
      }
      if (cand == null) continue;
      if (cand.startedAtEpochMs != m.startedAtEpochMs ||
          cand.frequencyHz != m.frequencyHz) {
        continue;
      }
      // sidecar 文件名即配对 stem：xxx.json → xxx.wav。两者都在录音目录内。
      final stem = entity.uri.pathSegments.last.replaceAll(RegExp(r'\.json$'), '');
      final wav = File('${dir.path}/$stem.wav');
      if (await entity.exists()) await entity.delete();
      if (await wav.exists()) await wav.delete();
      return;
    }
  }
}

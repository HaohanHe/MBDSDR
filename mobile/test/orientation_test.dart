import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';

/// 姿态纯函数离线测试：设备平放/竖直/倾斜的合成向量。
void main() {
  group('attitudeFromVectors', () {
    test('平放屏朝上：pitch≈0、roll≈0', () {
      final r = attitudeFromVectors(
        accel: const SensorVec(0, 0, 9.8),
        mag: null,
      );
      expect(r.pitch, closeTo(0, 1e-6));
      expect(r.roll, closeTo(0, 1e-6));
      expect(r.heading, isNull);
    });

    test('竖直（顶端朝上，+y 方向重力反号约定）：pitch≈±90', () {
      // 设备竖直、屏朝观察者：重力沿 -y。
      final r = attitudeFromVectors(
        accel: const SensorVec(0, -9.8, 0),
        mag: null,
      );
      expect(r.pitch, closeTo(0, 2));
      expect(r.roll.abs(), closeTo(90, 2));
    });

    test('无磁力计：只给 pitch/roll', () {
      final r = attitudeFromVectors(
        accel: const SensorVec(0, 0, 9.8),
        mag: null,
      );
      expect(r.heading, isNull);
    });

    test('带磁力计：heading 落在 [0,360)', () {
      // 磁指向 +x（东向）。
      final r = attitudeFromVectors(
        accel: const SensorVec(0, 0, 9.8),
        mag: const SensorVec(20, 0, 10),
      );
      expect(r.heading, isNotNull);
      expect(r.heading!, inInclusiveRange(0.0, 360.0));
    });

    test('零向量不崩溃', () {
      final r = attitudeFromVectors(
        accel: const SensorVec(0, 0, 0),
        mag: const SensorVec(0, 0, 0),
      );
      expect(r.pitch, 0.0);
      expect(r.roll, 0.0);
    });
  });

  test('DeviceOrientation.unavailable 全空', () {
    expect(DeviceOrientation.unavailable.heading, isNull);
    expect(DeviceOrientation.unavailable.hasCompass, isFalse);
    expect(DeviceOrientation.unavailable.hasImu, isFalse);
  });
}

import 'dart:async';
import 'dart:math' as math;

import 'package:flutter_compass/flutter_compass.dart';
import 'package:sensors_plus/sensors_plus.dart';

/// 设备姿态（度）。所有字段在对应传感器不可用时为 null，且通过
/// [hasCompass] / [hasImu] 诚实标注，绝不伪造。
class DeviceOrientation {
  const DeviceOrientation({
    required this.heading,
    required this.pitch,
    required this.roll,
    required this.hasCompass,
    required this.hasImu,
  });

  /// 航向角（度，北=0，顺时针）；无罗盘为 null。
  final double? heading;

  /// 俯仰角（度，手机抬头为正）；无 IMU 为 null。
  final double? pitch;

  /// 横滚角（度）；无 IMU 为 null。
  final double? roll;

  final bool hasCompass;
  final bool hasImu;

  /// 全空态：无任何传感器。
  static const DeviceOrientation unavailable = DeviceOrientation(
    heading: null,
    pitch: null,
    roll: null,
    hasCompass: false,
    hasImu: false,
  );

  @override
  String toString() =>
      'DeviceOrientation(head=$heading pitch=$pitch roll=$roll imu=$hasImu)';
}

/// 三维向量（传感器原始轴）。
class SensorVec {
  const SensorVec(this.x, this.y, this.z);
  final double x, y, z;
}

/// 由加速度计（重力）+ 磁力计向量解算姿态的纯函数（可离线单测）。
///
/// 约定（设备系，Android/Flutter 惯用）：
///   * 设备平放屏朝上：重力 ≈ (0, 0, +9.8)。
///   * pitch 抬头为正，roll 右倾为正，heading 自北顺时针。
/// 返回的 heading/pitch/roll 单位为度；mag 为 null 时仅返回 pitch/roll。
({double? heading, double pitch, double roll}) attitudeFromVectors({
  required SensorVec accel,
  required SensorVec? mag,
}) {
  final ax = accel.x;
  final ay = accel.y;
  final az = accel.z;
  final norm = math.sqrt(ax * ax + ay * ay + az * az);
  if (norm < 1e-3) {
    return (heading: mag == null ? null : 0.0, pitch: 0.0, roll: 0.0);
  }
  // 由重力向量求 pitch/roll。
  final pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) *
      180.0 /
      math.pi;
  final roll = math.atan2(ay, az) * 180.0 / math.pi;

  double? heading;
  if (mag != null) {
    // 倾斜补偿后的磁航向（Mahony 简化一阶）。
    final p = pitch * math.pi / 180.0;
    final r = roll * math.pi / 180.0;
    final cosP = math.cos(p);
    final sinP = math.sin(p);
    final cosR = math.cos(r);
    final sinR = math.sin(r);
    final bx = mag.x * cosP + mag.z * sinP;
    final by = mag.x * sinR * sinP + mag.y * cosR - mag.z * sinR * cosP;
    var hd = math.atan2(by, bx) * 180.0 / math.pi;
    hd = (hd + 360.0) % 360.0;
    heading = hd;
  }
  return (heading: heading, pitch: pitch, roll: roll);
}

/// 姿态服务抽象：便于注入 fake 做 widget 测试。
abstract class OrientationService {
  Stream<DeviceOrientation> get stream;
  void dispose();
}

/// 基于 flutter_compass + sensors_plus 的真实实现。
class ImuOrientationService implements OrientationService {
  ImuOrientationService() {
    _start();
  }

  final _controller = StreamController<DeviceOrientation>.broadcast();
  StreamSubscription<CompassEvent>? _compassSub;
  StreamSubscription<AccelerometerEvent>? _accelSub;
  StreamSubscription<MagnetometerEvent>? _magSub;

  double? _heading;
  SensorVec? _accel;
  SensorVec? _mag;
  var _hasCompass = false;
  var _hasImu = false;

  @override
  Stream<DeviceOrientation> get stream => _controller.stream;

  void _emit() {
    double? pitch;
    double? roll;
    if (_hasImu && _accel != null) {
      final a = attitudeFromVectors(accel: _accel!, mag: _mag);
      pitch = a.pitch;
      roll = a.roll;
      // 优先用磁力计+加速度解算航向；否则退化为罗盘。
      if (a.heading != null && _mag != null) {
        _heading = a.heading;
      }
    }
    _controller.add(DeviceOrientation(
      heading: _hasCompass ? _heading : (_mag != null ? _heading : null),
      pitch: pitch,
      roll: roll,
      hasCompass: _hasCompass,
      hasImu: _hasImu,
    ));
  }

  void _start() {
    try {
      _compassSub = FlutterCompass.events?.listen((e) {
        if (e.heading != null) {
          _hasCompass = true;
          _heading = (e.heading! + 360.0) % 360.0;
          _emit();
        }
      });
    } catch (_) {
      // 罗盘不可用：hasCompass 保持 false。
    }

    try {
      _accelSub = accelerometerEventStream().listen((e) {
        _hasImu = true;
        _accel = SensorVec(e.x, e.y, e.z);
        _emit();
      });
      _magSub = magnetometerEventStream().listen((e) {
        _mag = SensorVec(e.x, e.y, e.z);
        _emit();
      });
    } catch (_) {
      // IMU 不可用。
    }
  }

  @override
  void dispose() {
    _compassSub?.cancel();
    _accelSub?.cancel();
    _magSub?.cancel();
    _controller.close();
  }
}

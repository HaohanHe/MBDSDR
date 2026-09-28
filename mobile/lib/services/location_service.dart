import 'dart:async';

import 'package:geolocator/geolocator.dart';

import 'package:mbdsdr_mobile/models/satellite.dart';

/// 定位服务状态。
enum LocationStatus {
  /// 系统定位服务关闭。
  disabled,

  /// 权限被拒绝（可再次请求）。
  denied,

  /// 权限被永久拒绝（只能引导用户去系统设置）。
  deniedForever,

  /// 可用。
  available,
}

/// 定位服务抽象：便于 SkyController 注入 fake 做 widget 测试。
///
/// 实现必须诚实——无权限/服务关闭时不伪造坐标。
abstract class LocationService {
  /// 当前状态。
  LocationStatus get status;

  /// 最近一次定位到的测站；不可用为 null。
  Station? get station;

  /// 位置流（持续更新）。
  Stream<Station> get stationStream;

  /// 检查服务、请求权限并取一次当前位置。返回更新后的状态。
  Future<LocationStatus> resolve();

  /// 打开系统定位设置页（用户永久拒绝后引导用）。
  Future<void> openSettings();

  /// 释放订阅。
  void dispose();
}

/// 基于 geolocator 的真实定位实现。
class GeolocatorLocationService implements LocationService {
  GeolocatorLocationService({
    this.desiredAccuracy = LocationAccuracy.medium,
  });

  final LocationAccuracy desiredAccuracy;
  StreamSubscription<Position>? _sub;
  final _controller = StreamController<Station>.broadcast();

  LocationStatus _status = LocationStatus.disabled;
  Station? _station;

  @override
  LocationStatus get status => _status;

  @override
  Station? get station => _station;

  @override
  Stream<Station> get stationStream => _controller.stream;

  @override
  Future<LocationStatus> resolve() async {
    final serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) {
      _status = LocationStatus.disabled;
      return _status;
    }

    var perm = await Geolocator.checkPermission();
    if (perm == LocationPermission.denied) {
      perm = await Geolocator.requestPermission();
    }
    if (perm == LocationPermission.denied) {
      _status = LocationStatus.denied;
      return _status;
    }
    if (perm == LocationPermission.deniedForever) {
      _status = LocationStatus.deniedForever;
      return _status;
    }

    try {
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: LocationSettings(accuracy: desiredAccuracy),
      );
      _station = _toStation(pos);
      _status = LocationStatus.available;
      _controller.add(_station!);
      _sub ??= Geolocator.getPositionStream(
        locationSettings: LocationSettings(accuracy: desiredAccuracy),
      ).listen((p) {
        _station = _toStation(p);
        _controller.add(_station!);
      });
    } catch (_) {
      _status = LocationStatus.denied;
    }
    return _status;
  }

  static Station _toStation(Position p) => Station(
        lat: p.latitude,
        lon: p.longitude,
        alt: (p.altitude / 1000.0).clamp(0.0, 10.0),
      );

  @override
  Future<void> openSettings() => Geolocator.openAppSettings();

  @override
  void dispose() {
    _sub?.cancel();
    _controller.close();
  }
}

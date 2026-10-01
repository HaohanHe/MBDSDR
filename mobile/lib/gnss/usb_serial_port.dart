// USB-serial 抽象：外部 GNSS 模块（USB-TTL / USB-CDC 吐 NMEA-0183）的读通道。
//
// ---------------------------------------------------------------
// 平台通道契约（channel = `mbdsdr/usb_serial`）——原生未编译、真机待验
// ---------------------------------------------------------------
// | method       | 参数                              | 返回 |
// |--------------|-----------------------------------|------|
// | `listDevices`| 无                                | List<Map{deviceId,productName,manufacturerName,vidPid}> |
// | `open`       | `{"deviceId":int,"baud":int}`     | bool（是否成功打开） |
// | `close`      | 无                                | {}   |
// | `readBytes`  | 无（原生经 readBytes/onData 推字节流） | ——  |
//
// 原生 → Dart 事件：`onData` `{bytes:Uint8List}`、`onAttached`/`onDetached` `{}`。
//
// [云未编译·真机待验]：Android 用 android.hardware.usb host API（MainActivity.kt
// 已按契约写入），iOS 无通用 USB host 串口——`listDevices()` 在 iOS 上诚实返回空，
// 外接 GNSS 走 MFi / 蓝牙（本仓不实现）。
library;

import 'dart:async';

import 'package:flutter/services.dart';

/// 一个 USB 串口设备的描述信息。
class UsbDeviceInfo {
  const UsbDeviceInfo({
    required this.deviceId,
    required this.productName,
    required this.manufacturerName,
    required this.vidPid,
  });

  final int deviceId;
  final String productName;
  final String manufacturerName;
  final String vidPid;

  static UsbDeviceInfo? fromMap(Object? m) {
    if (m is! Map) return null;
    return UsbDeviceInfo(
      deviceId: (m['deviceId'] as num?)?.toInt() ?? 0,
      productName: m['productName'] as String? ?? '',
      manufacturerName: m['manufacturerName'] as String? ?? '',
      vidPid: m['vidPid'] as String? ?? '',
    );
  }
}

/// 串口抽象：便于测试注入 [FakeUsbSerialPort]。
abstract class UsbSerialPort {
  /// 枚举当前可打开的 USB 串口设备。无设备返回空列表（不抛）。
  Future<List<UsbDeviceInfo>> listDevices();

  /// 打开指定设备（[baud] 默认 9600）。成功返回 true。
  Future<bool> open({required int deviceId, int baud = 9600});

  /// 原始字节流（NMEA 帧可能跨包，由上层按 \n 切分）。
  Stream<Uint8List> get readBytes;

  /// 热插拔状态流：true=已连接。
  Stream<bool> get hotplugState;

  /// 关闭串口。
  Future<void> close();
}

/// 基于 MethodChannel('mbdsdr/usb_serial') 的真机实现。
///
/// iOS 诚实降级：无通用 USB host 串口，[listDevices] 由原生返回空，
/// 应用层据此提示「iOS 仅支持内置/蓝牙 GNSS」。
class PlatformUsbSerialPort implements UsbSerialPort {
  PlatformUsbSerialPort({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel('mbdsdr/usb_serial') {
    _channel.setMethodCallHandler(_onNativeCall);
  }

  final MethodChannel _channel;
  final _readCtrl = StreamController<Uint8List>.broadcast();
  final _hotplugCtrl = StreamController<bool>.broadcast();

  Future<dynamic> _onNativeCall(MethodCall call) async {
    switch (call.method) {
      case 'onData':
        final a = call.arguments;
        if (a is Map && a['bytes'] is Uint8List) {
          _readCtrl.add(a['bytes'] as Uint8List);
        }
      case 'onAttached':
        _hotplugCtrl.add(true);
      case 'onDetached':
        _hotplugCtrl.add(false);
    }
  }

  @override
  Future<List<UsbDeviceInfo>> listDevices() async {
    final r = await _channel.invokeMethod<List<Object?>>('listDevices');
    return (r ?? const [])
        .map(UsbDeviceInfo.fromMap)
        .whereType<UsbDeviceInfo>()
        .toList();
  }

  @override
  Future<bool> open({required int deviceId, int baud = 9600}) async {
    final r = await _channel.invokeMethod<bool>('open', {
      'deviceId': deviceId,
      'baud': baud,
    });
    return r == true;
  }

  @override
  Stream<Uint8List> get readBytes => _readCtrl.stream;

  @override
  Stream<bool> get hotplugState => _hotplugCtrl.stream;

  @override
  Future<void> close() async {
    await _channel.invokeMethod<void>('close');
  }

  /// 释放内部流控制器（应用退出时调用）。
  Future<void> dispose() async {
    await _readCtrl.close();
    await _hotplugCtrl.close();
  }
}

/// 测试用假串口：按脚本喂入预制字节，不触碰平台通道。
class FakeUsbSerialPort implements UsbSerialPort {
  FakeUsbSerialPort({this.devices = const []});

  final List<UsbDeviceInfo> devices;
  final StreamController<Uint8List> _readCtrl = StreamController<Uint8List>();
  final StreamController<bool> _hotplugCtrl = StreamController<bool>();
  bool _open = false;

  /// 测试向 readBytes 流推入一段字节（如一组 NMEA 行）。
  void emit(Uint8List bytes) => _readCtrl.add(bytes);

  int lastBaud = -1;
  int? lastDeviceId;

  @override
  Future<List<UsbDeviceInfo>> listDevices() async => List.of(devices);

  @override
  Future<bool> open({required int deviceId, int baud = 9600}) async {
    _open = true;
    lastDeviceId = deviceId;
    lastBaud = baud;
    return true;
  }

  @override
  Stream<Uint8List> get readBytes => _readCtrl.stream;

  @override
  Stream<bool> get hotplugState => _hotplugCtrl.stream;

  @override
  Future<void> close() async {
    _open = false;
  }

  bool get isOpen => _open;

  /// 测试结束时释放流控制器。
  Future<void> dispose() async {
    await _readCtrl.close();
    await _hotplugCtrl.close();
  }
}

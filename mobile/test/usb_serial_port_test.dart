// USB-serial 抽象测试：FakeUsbSerialPort 枚举/打开/字节流/关闭，不碰平台通道。
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/gnss/usb_serial_port.dart';

void main() {
  test('FakeUsbSerialPort：list/open/emit/close', () async {
    const dev = UsbDeviceInfo(
      deviceId: 1,
      productName: 'CH340',
      manufacturerName: 'QinHeng',
      vidPid: '1A86:7523',
    );
    final port = FakeUsbSerialPort(devices: [dev]);

    final list = await port.listDevices();
    expect(list.single.productName, 'CH340');
    expect(list.single.vidPid, '1A86:7523');

    expect(await port.open(deviceId: 1, baud: 9600), isTrue);
    expect(port.lastBaud, 9600);
    expect(port.isOpen, isTrue);

    // 订阅后推入一段 NMEA 字节，断言流收到。
    final bytes = <int>[];
    final sub = port.readBytes.listen(bytes.addAll);
    port.emit(Uint8List.fromList('\$GNGGA*hh\r\n'.codeUnits));
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(bytes, isNotEmpty);

    await sub.cancel();
    await port.close();
    expect(port.isOpen, isFalse);
  });

  test('UsbDeviceInfo.fromMap 容错（缺字段/null）', () {
    expect(UsbDeviceInfo.fromMap(null), isNull);
    expect(UsbDeviceInfo.fromMap({'deviceId': 5})!.deviceId, 5);
    expect(UsbDeviceInfo.fromMap({'deviceId': 5})!.productName, '');
  });
}

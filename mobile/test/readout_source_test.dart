// 读数来源后缀工具（P2）：空态不叠后缀、正常追加、重复调用不叠加。
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/app/readout_source.dart';

void main() {
  group('withSource', () {
    test('正常读数追加中文来源后缀', () {
      expect(withSource('145.000000 MHz', ReadoutSources.setpoint),
          '145.000000 MHz（设定值）');
      expect(withSource('12:00:00', ReadoutSources.deviceClock),
          '12:00:00（设备时钟（未校准））');
    });

    test('空串与「—」保持原样，不把空态包装成读数', () {
      expect(withSource('', ReadoutSources.measuredPeak), '');
      expect(withSource('—', ReadoutSources.measuredPeak), '—');
    });

    test('来源为空时不追加', () {
      expect(withSource('2.048 MS/s', ''), '2.048 MS/s');
      expect(withSource('2.048 MS/s', '   '), '2.048 MS/s');
    });

    test('同一来源重复调用不叠加后缀', () {
      final once = withSource('20.0 dB', ReadoutSources.deviceReport);
      expect(withSource(once, ReadoutSources.deviceReport), once);
      expect(once, '20.0 dB（设备报告）');
    });

    test('文本本身等于来源时不包装', () {
      expect(withSource('自动', '自动'), '自动');
    });
  });
}

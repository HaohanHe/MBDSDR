// 卫星过境捕获：目录命中真实调谐 / 目录外诚实禁用 / 一次性多普勒预测。
import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/passes.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/models/satellite_downlink.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/satellite_capture.dart';

/// 记录 setFrequencyHz/setMode 调用的最小 fake。
class _FakeRadio implements RadioApi {
  int? tunedHz;
  DemodMode? tunedMode;
  int connectCalls = 0;

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError(invocation.memberName.toString());

  @override
  Future<void> setFrequencyHz(int hz) async {
    tunedHz = hz;
  }

  @override
  void setMode(DemodMode m) {
    tunedMode = m;
  }
}

Pass _passOf(int catalog, String name) => Pass(
      name: name,
      catalogNumber: catalog,
      riseTime: DateTime.utc(2024, 1, 1, 5),
      riseAz: 20,
      setTime: DateTime.utc(2024, 1, 1, 6),
      setAz: 300,
      maxEl: 40,
      maxTime: DateTime.utc(2024, 1, 1, 5, 30),
    );

void main() {
  group('下行频率目录', () {
    test('ISS 编目号命中真实下行，退役 NOAA 与未知星返回 null', () {
      expect(satelliteDownlink(25544)!.downlinkHz, 145.8000e6);
      expect(satelliteDownlink(25544)!.mode, DemodMode.nfm);
      // NOAA POES 星座 2025 年全部退役：目录里不能再留它们的 APT 频率。
      expect(satelliteDownlink(33591), isNull, reason: 'NOAA 19 已退役（2025-08-13）');
      expect(satelliteDownlink(99999), isNull, reason: '目录外 -> 诚实禁用');
    });
  });

  group('capturePass', () {
    test('目录内卫星：真实 setFrequencyHz + setMode(nfm)', () async {
      final radio = _FakeRadio();
      final out = await capturePass(
        radio: radio,
        pass: _passOf(25544, 'ISS (ZARYA)'),
        tle: null, // 无 TLE -> 多普勒偏置 0，按标称频率
        station: null,
        now: DateTime.utc(2024),
      );
      expect(out, isA<CaptureApplied>());
      expect(radio.tunedHz, 145800000, reason: '标称 145.8000 MHz');
      expect(radio.tunedMode, DemodMode.nfm);
    });

    test('目录外卫星：诚实「无下行频率数据」，不碰接收机', () async {
      final radio = _FakeRadio();
      final out = await capturePass(
        radio: radio,
        pass: _passOf(99999, 'UNKNOWN SAT'),
        tle: null,
        station: null,
        now: DateTime.utc(2024),
      );
      expect(out, isA<CaptureUnavailable>());
      expect((out as CaptureUnavailable).reason, '无下行频率数据');
      expect(radio.tunedHz, isNull, reason: '绝不为无频率星猜频率');
      expect(radio.tunedMode, isNull);
    });

    test('有 TLE + 站：多普勒偏置有限且把中心频率拉到下行附近', () async {
      // ISS TLE（目录内，25544 = 145.800 MHz）。
      const l1 =
          '1 25544U 98067A   08264.51782472  .00016717  00000-0  10270-3 0  0864';
      const l2 =
          '2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563537';
      final iss = Tle.fromLines(l1, l2);
      const station = Station(lat: 40, lon: -100);
      final radio = _FakeRadio();
      final out = await capturePass(
        radio: radio,
        pass: _passOf(25544, 'ISS (ZARYA)'),
        tle: iss,
        station: station,
        now: iss.epoch.add(const Duration(minutes: 30)),
      );
      expect(out, isA<CaptureApplied>());
      final applied = out as CaptureApplied;
      // LEO 径向速度量级 ~±7 km/s -> 145.8 MHz 多普勒 ~±3.4 kHz。
      expect(applied.dopplerHz.abs(), lessThan(5e3),
          reason: '多普勒偏置应在 kHz 量级，实得 ${applied.dopplerHz} Hz');
      // 中心频率仍在 145.8 MHz 下行附近。
      expect((applied.centerHz / 1e6), inInclusiveRange(145.7, 145.9));
    });
  });
}

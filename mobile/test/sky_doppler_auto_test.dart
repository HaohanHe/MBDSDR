// SPDX-License-Identifier: MIT
// 实时多普勒自动补偿：开关门控（默认 off / 无目标绝不改频 / 关闭停表）。
// 确定性：用 fake 连接态 radio 记录 setFrequencyHz 调用；不依赖 SGP4 是否真有可见星。
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:mbdsdr_mobile/astro/tle.dart';
import 'package:mbdsdr_mobile/models/radio_state.dart';
import 'package:mbdsdr_mobile/models/satellite.dart';
import 'package:mbdsdr_mobile/pages/sky_page.dart';
import 'package:mbdsdr_mobile/services/location_service.dart';
import 'package:mbdsdr_mobile/services/orientation_service.dart';
import 'package:mbdsdr_mobile/services/radio_controller.dart';
import 'package:mbdsdr_mobile/services/tle_client.dart';

/// 记录 setFrequencyHz 调用的最小 fake（其余接口未用到即抛错）。
class _FakeRadio implements RadioApi {
  @override
  ConnectionStatus status = ConnectionStatus.connected;
  @override
  int freqHz = 137100000;
  final List<int> tuned = <int>[];

  @override
  Future<void> setFrequencyHz(int hz) async {
    tuned.add(hz);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError(invocation.memberName.toString());
}

class _EmptyLocation implements LocationService {
  @override
  LocationStatus get status => LocationStatus.available;
  @override
  Station? get station => null;
  @override
  Stream<Station> get stationStream => const Stream.empty();
  @override
  Future<LocationStatus> resolve() async => LocationStatus.available;
  @override
  Future<void> openSettings() async {}
  @override
  void dispose() {}
}

class _NoOrientation implements OrientationService {
  @override
  Stream<DeviceOrientation> get stream =>
      Stream.value(DeviceOrientation.unavailable);
  @override
  void dispose() {}
}

class _EmptyTle extends TleClient {
  @override
  Future<List<Tle>> fetch(TleGroup group) async => const [];
  @override
  DateTime? lastUpdated(TleGroup group) => null;
}

void main() {
  SkyController buildController(_FakeRadio radio) => SkyController(
        tleClient: _EmptyTle(),
        locationService: _EmptyLocation(),
        orientationService: _NoOrientation(),
        manualStation: const Station(lat: 40, lon: -100),
        radio: radio,
      );

  test('开关默认 off，且未补偿', () {
    final c = buildController(_FakeRadio());
    expect(c.dopplerAuto, isFalse);
    expect(c.dopplerCompensating, isFalse);
    expect(c.dopplerAppliedHz, isNull);
    c.dispose();
  });

  test('开启但无选中目标 -> 闭环空转，绝不改频（诚实门控）', () {
    final radio = _FakeRadio();
    final c = buildController(radio);
    c.setDopplerAuto(true);
    expect(c.dopplerAuto, isTrue);
    // 无 selectedVisibility -> 每拍门控返回，不产生任何 setFrequencyHz。
    expect(radio.tuned, isEmpty, reason: '无目标绝不自动改频');
    expect(c.dopplerCompensating, isFalse);
    expect(c.dopplerAppliedHz, isNull);
    c.setDopplerAuto(false);
    c.dispose();
  });

  test('关闭开关 -> 停表并清补偿态，不再改频', () {
    final radio = _FakeRadio();
    final c = buildController(radio);
    c.setDopplerAuto(true);
    c.setDopplerAuto(false);
    expect(c.dopplerAuto, isFalse);
    expect(c.dopplerCompensating, isFalse);
    expect(radio.tuned, isEmpty);
    c.dispose();
  });

  test('radio 为 null 时开启开关 -> 不崩溃、不改频（诚实降级）', () {
    final c = SkyController(
      tleClient: _EmptyTle(),
      locationService: _EmptyLocation(),
      orientationService: _NoOrientation(),
      manualStation: const Station(lat: 40, lon: -100),
      radio: null, // 未接射频
    );
    c.setDopplerAuto(true);
    expect(c.dopplerAuto, isTrue);
    expect(c.dopplerCompensating, isFalse);
    c.setDopplerAuto(false);
    c.dispose();
  });
}

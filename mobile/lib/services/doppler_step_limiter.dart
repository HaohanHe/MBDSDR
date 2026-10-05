// SPDX-License-Identifier: MIT
//
// 实时多普勒步进限幅器：雷达式收敛节流，镜像桌面
// cpp/src/core/sat_capture.h `DopplerStepLimiter`（kDopplerMaxStepHz = 2000 Hz）。
//
// 诚实边界：本类是**纯逻辑**——只算「这一拍该把 VFO 设到多少」，不碰硬件、
// 不做任何 IO。连续闭环（定时调 setFrequencyHz）由 SkyController 在开关开启后
// 驱动；开关默认 off，未开启时本类永不被调用。
//
// 语义：reset(vfo) 绑定起始频率一次；之后每拍 advance(target) 返回一个新频率，
// 它从当前值向 target 迈进、每拍最多 [maxStepHz] Hz。缓慢漂移（几十 Hz/s）
// 一拍即落；大步跳变（过境起始/捕获）被切成有界小片，避免调谐器抖动。
library;

import '../app/tokens.dart';

class DopplerStepLimiter {
  DopplerStepLimiter({double maxStepHz = AppTokens.dopplerAutoMaxStepHz})
      : _maxStepHz = maxStepHz;

  final double _maxStepHz;
  double _current = 0;
  bool _armed = false;

  /// 每拍最大步进（Hz）。
  double get maxStepHz => _maxStepHz;

  /// 当前限幅器内的 VFO 频率（Hz）；未绑定前无意义。
  double get currentHz => _current;

  /// 是否已绑定起始 VFO。
  bool get armed => _armed;

  /// 绑定起始 VFO 频率（开关开启/捕获时调用一次，不产生步进）。
  void reset(double vfoHz) {
    _current = vfoHz;
    _armed = true;
  }

  /// 解绑（开关关闭/过境结束）；下次 advance() 会重新绑定，不把频率拉回。
  void disarm() => _armed = false;

  /// 从当前值向 [targetHz] 迈进，每拍最多 _maxStepHz；返回本拍应设置的 VFO。
  double advance(double targetHz) {
    if (!_armed) {
      _current = targetHz;
      _armed = true;
      return _current;
    }
    final diff = targetHz - _current;
    if (diff.abs() <= _maxStepHz) {
      _current = targetHz; // 一步可达：精确落在目标
    } else {
      _current += diff > 0 ? _maxStepHz : -_maxStepHz; // 有界步进
    }
    return _current;
  }
}

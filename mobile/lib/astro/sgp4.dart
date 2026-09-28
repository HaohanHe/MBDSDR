/// SGP4 近地传播器——Dart 移植自 Vallado 公开参考实现。
///
/// 移植来源（代码注释与数值归属）：
///   * D. Vallado et al., "Revisiting Spacetrack Report #3",
///     AIAA 2006-6730 (2006)，配套 sgp4unit/sgp4.cpp（WGS-72）。
///   * 本文件逐行对齐 python-sgp4 `sgp4/propagation.py`（brandon-rhodes）
///     中 `sgp4init` / `initl` / `sgp4` 三条近地主路径；
///     常数取自 `getgravconst('wgs72')`：
///       mu=398600.8 km³/s², radiusearthkm=6378.135,
///       xke=60/sqrt(r³/mu), j2=1.082616e-3, j3=-2.53881e-6, j4=-1.65597e-6。
///
/// 已知限制（诚实声明）：
///   * 仅实现近地（period<225min）SGP4 路径。深空（>225min，如 GEO/闪电）
///     的日月摄动、共振项（dscom/dsinit/dspace/dpper）未移植；对深空 TLE
///     仍按近地模型传播，长期精度下降，但在 48h 窗口内对 GEO 级目标
///     （经 GMST 转 ECEF 后）方位/仰角仍近似稳定。
///   * 输出为 TEME 坐标；坐标转换（TEME→ECEF）见 coordinates.dart，
///     忽略章动/极移。
library;

import 'dart:math' as math;

import 'package:mbdsdr_mobile/astro/tle.dart';

/// 三维向量（km 或 km/s）。
class Vec3 {
  const Vec3(this.x, this.y, this.z);

  final double x;
  final double y;
  final double z;

  double get norm => math.sqrt(x * x + y * y + z * z);

  Vec3 scale(double s) => Vec3(x * s, y * s, z * s);

  Vec3 operator -(Vec3 o) => Vec3(x - o.x, y - o.y, z - o.z);
  Vec3 operator +(Vec3 o) => Vec3(x + o.x, y + o.y, z + o.z);

  @override
  String toString() => '($x, $y, $z)';
}

/// SGP4 传播失效时抛出。[code] 对齐 Spacetrack 错误码。
class Sgp4Exception implements Exception {
  Sgp4Exception(this.code, this.message);

  final int code;
  final String message;

  @override
  String toString() => 'Sgp4Exception($code): $message';
}

/// 不可变的 WGS-72 引力常数集。
class _Wgs72 {
  static const double mu = 398600.8; // km^3/s^2
  static const double radiusearthkm = 6378.135;
  static final double xke = 60.0 /
      math.sqrt(radiusearthkm * radiusearthkm * radiusearthkm / mu);
  static const double j2 = 0.001082616;
  static const double j3 = -0.00000253881;
  static const double j4 = -0.00000165597;
  static const double j3oj2 = j3 / j2;
}

/// 由 TLE 初始化并传播单个卫星。
class Sgp4 {
  Sgp4(this.tle) {
    _init();
  }

  final Tle tle;

  // ---- 历元原始根数（弧度 / 归一化单位）----
  late double _noKozai; // rad/min (Kozai)
  late double _inclo;
  late double _nodeo;
  late double _argpo;
  late double _mo;
  late double _ecco;
  late double _bstar;

  // ---- initl 派生量 ----
  late double _noUnkozai;
  late double _ao;
  late double _invAo;
  late double _con41;
  late double _cosio;
  late double _cosio2;
  late double _eccsq;
  late double _omeosq;
  late double _posq;
  late double _rp;
  late double _rteosq;
  late double _sinio;

  // ---- 近地系数 ----
  int _isimp = 0;
  double _eta = 0;
  double _aycof = 0;
  double _cc1 = 0;
  double _cc4 = 0;
  double _cc5 = 0;
  double _d2 = 0;
  double _d3 = 0;
  double _d4 = 0;
  double _delmo = 0;
  double _argpdot = 0;
  double _omgcof = 0;
  double _sinmao = 0;
  double _t2cof = 0;
  double _t3cof = 0;
  double _t4cof = 0;
  double _t5cof = 0;
  double _x1mth2 = 0;
  double _x7thm1 = 0;
  double _mdot = 0;
  double _nodedot = 0;
  double _xlcof = 0;
  double _xmcof = 0;
  double _nodecf = 0;

  static const double _deg2rad = math.pi / 180.0;
  static const double _xpdotp = 1440.0 / (2.0 * math.pi);
  static const double _twopi = 2.0 * math.pi;

  void _init() {
    _ecco = tle.eccentricity;
    _bstar = tle.bstar;
    _inclo = tle.inclinationDeg * _deg2rad;
    _nodeo = tle.raanDeg * _deg2rad;
    _argpo = tle.argPerigeeDeg * _deg2rad;
    _mo = tle.meanAnomalyDeg * _deg2rad;
    _noKozai = tle.meanMotionRevDay / _xpdotp; // rad/min

    // ndot / nddot 归一化
    // (io.py: ndot /= xpdotp*1440 ; nddot /= xpdotp*1440*1440)
    // 这些量近地路径不直接使用，但保留以对齐参考。

    // ---------------- initl（un-Kozai 平均运动）----------------
    const x2o3 = 2.0 / 3.0;
    _eccsq = _ecco * _ecco;
    _omeosq = 1.0 - _eccsq;
    _rteosq = math.sqrt(_omeosq);
    _cosio = math.cos(_inclo);
    _cosio2 = _cosio * _cosio;

    final ak = math.pow(_Wgs72.xke / _noKozai, x2o3).toDouble();
    final d1 = 0.75 * _Wgs72.j2 * (3.0 * _cosio2 - 1.0) /
        (_rteosq * _omeosq);
    var del = d1 / (ak * ak);
    final adel = ak * (1.0 -
        del * del -
        del * (1.0 / 3.0 + 134.0 * del * del / 81.0));
    del = d1 / (adel * adel);
    _noUnkozai = _noKozai / (1.0 + del);

    _ao = math.pow(_Wgs72.xke / _noUnkozai, x2o3).toDouble();
    _sinio = math.sin(_inclo);
    final po = _ao * _omeosq;
    final con42 = 1.0 - 5.0 * _cosio2;
    _con41 = -con42 - _cosio2 - _cosio2;
    _invAo = 1.0 / _ao;
    _posq = po * po;
    _rp = _ao * (1.0 - _ecco);

    // ---------------- 近地系数 ----------------
    const ss = 78.0 / _Wgs72.radiusearthkm + 1.0;
    var sfour = ss;
    const qzms2ttemp = (120.0 - 78.0) / _Wgs72.radiusearthkm;
    var qzms24 = qzms2ttemp * qzms2ttemp * qzms2ttemp * qzms2ttemp;

    _isimp = 0;
    if (_rp < 220.0 / _Wgs72.radiusearthkm + 1.0) {
      _isimp = 1;
    }
    final perige = (_rp - 1.0) * _Wgs72.radiusearthkm;
    if (perige < 156.0) {
      sfour = perige - 78.0;
      if (perige < 98.0) {
        sfour = 20.0;
      }
      final qtemp = (120.0 - sfour) / _Wgs72.radiusearthkm;
      qzms24 = qtemp * qtemp * qtemp * qtemp;
      sfour = sfour / _Wgs72.radiusearthkm + 1.0;
    }

    final pinvsq = 1.0 / _posq;
    final tsi = 1.0 / (_ao - sfour);
    _eta = _ao * _ecco * tsi;
    final etasq = _eta * _eta;
    final eeta = _ecco * _eta;
    final psisq = 1.0 - etasq;
    final coef = qzms24 * math.pow(tsi, 4.0).toDouble();
    final coef1 = coef / math.pow(psisq, 3.5).toDouble();
    final cc2 = coef1 *
        _noUnkozai *
        (_ao * (1.0 + 1.5 * etasq + eeta * (4.0 + etasq)) +
            0.375 *
                _Wgs72.j2 *
                tsi /
                psisq *
                _con41 *
                (8.0 + 3.0 * etasq * (8.0 + etasq)));
    _cc1 = _bstar * cc2;
    var cc3 = 0.0;
    if (_ecco > 1.0e-4) {
      cc3 = -2.0 *
          coef *
          tsi *
          _Wgs72.j3oj2 *
          _noUnkozai *
          _sinio /
          _ecco;
    }
    _x1mth2 = 1.0 - _cosio2;
    _cc4 = 2.0 *
        _noUnkozai *
        coef1 *
        _ao *
        _omeosq *
        (_eta * (2.0 + 0.5 * etasq) +
            _ecco * (0.5 + 2.0 * etasq) -
            _Wgs72.j2 * tsi / (_ao * psisq) *
                (-3.0 *
                        _con41 *
                        (1.0 - 2.0 * eeta + etasq * (1.5 - 0.5 * eeta)) +
                    0.75 *
                        _x1mth2 *
                        (2.0 * etasq - eeta * (1.0 + etasq)) *
                        math.cos(2.0 * _argpo)));
    _cc5 = 2.0 *
        coef1 *
        _ao *
        _omeosq *
        (1.0 + 2.75 * (etasq + eeta) + eeta * eeta);
    final cosio4 = _cosio2 * _cosio2;
    final temp1 = 1.5 * _Wgs72.j2 * pinvsq * _noUnkozai;
    final temp2 = 0.5 * temp1 * _Wgs72.j2 * pinvsq;
    final temp3 = -0.46875 * _Wgs72.j4 * pinvsq * pinvsq * _noUnkozai;
    _mdot = _noUnkozai +
        0.5 * temp1 * _rteosq * _con41 +
        0.0625 *
            temp2 *
            _rteosq *
            (13.0 - 78.0 * _cosio2 + 137.0 * cosio4);
    _argpdot = -0.5 * temp1 * con42 +
        0.0625 * temp2 * (7.0 - 114.0 * _cosio2 + 395.0 * cosio4) +
        temp3 * (3.0 - 36.0 * _cosio2 + 49.0 * cosio4);
    final xhdot1 = -temp1 * _cosio;
    _nodedot = xhdot1 +
        (0.5 * temp2 * (4.0 - 19.0 * _cosio2) +
                2.0 * temp3 * (3.0 - 7.0 * _cosio2)) *
            _cosio;
    final xpidot = _argpdot + _nodedot;
    _omgcof = _bstar * cc3 * math.cos(_argpo);
    _xmcof = 0.0;
    if (_ecco > 1.0e-4) {
      _xmcof = -(2.0 / 3.0) * coef * _bstar / eeta;
    }
    _nodecf = 3.5 * _omeosq * xhdot1 * _cc1;
    _t2cof = 1.5 * _cc1;
    const temp4 = 1.5e-12;
    if ((_cosio + 1.0).abs() > 1.5e-12) {
      _xlcof = -0.25 *
          _Wgs72.j3oj2 *
          _sinio *
          (3.0 + 5.0 * _cosio) /
          (1.0 + _cosio);
    } else {
      _xlcof = -0.25 *
          _Wgs72.j3oj2 *
          _sinio *
          (3.0 + 5.0 * _cosio) /
          temp4;
    }
    _aycof = -0.5 * _Wgs72.j3oj2 * _sinio;
    final delmotemp = 1.0 + _eta * math.cos(_mo);
    _delmo = delmotemp * delmotemp * delmotemp;
    _sinmao = math.sin(_mo);
    _x7thm1 = 7.0 * _cosio2 - 1.0;

    // 深空初始化（dscom/dsinit/dpper）不实现——近地模型；method 恒为 'n'。
    // 对 period>=225min 的目标仍走近地路径（见文件头已知限制）。

    // d2/d3/d4（仅非简化阻力模型时）。
    if (_isimp != 1) {
      final cc1sq = _cc1 * _cc1;
      _d2 = 4.0 * _ao * tsi * cc1sq;
      final temp = _d2 * tsi * _cc1 / 3.0;
      _d3 = (17.0 * _ao + sfour) * temp;
      _d4 = 0.5 * temp * _ao * tsi * (221.0 * _ao + 31.0 * sfour) * _cc1;
      _t3cof = _d2 + 2.0 * cc1sq;
      _t4cof = 0.25 *
          (3.0 * _d3 + _cc1 * (12.0 * _d2 + 10.0 * cc1sq));
      _t5cof = 0.2 * (3.0 * _d4 +
          12.0 * _cc1 * _d3 +
          6.0 * _d2 * _d2 +
          15.0 * cc1sq * (2.0 * _d2 + cc1sq));
    }
    // 引用避免告警（xpidot 仅深空路径使用）。
    assert(xpidot.isFinite);
    assert(_invAo.isFinite);
  }

  /// 传播到时刻 [t]（UTC），返回 TEME 位置(km) 与速度(km/s)。
  /// 失效（衰减到地表以下等）时抛 [Sgp4Exception]。
  ({Vec3 r, Vec3 v}) propagate(DateTime t) {
    final utc = t.isUtc ? t : t.toUtc();
    // 1 分钟 = 60,000,000 微秒。
    final tsinceMin =
        utc.difference(tle.epoch).inMicroseconds / 60000000.0;
    return _propagateMinutes(tsinceMin);
  }

  ({Vec3 r, Vec3 v}) _propagateMinutes(double tsince) {
    final t = tsince;

    // ---- 长期重力 + 大气阻力 ----
    final xmdf = _mo + _mdot * t;
    final argpdf = _argpo + _argpdot * t;
    final nodedf = _nodeo + _nodedot * t;
    var argpm = argpdf;
    var mm = xmdf;
    final t2 = t * t;
    var nodem = nodedf + _nodecf * t2;
    var tempa = 1.0 - _cc1 * t;
    var tempe = _bstar * _cc4 * t;
    var templ = _t2cof * t2;

    if (_isimp != 1) {
      final delomg = _omgcof * t;
      final delmtemp = 1.0 + _eta * math.cos(xmdf);
      final delm =
          _xmcof * (delmtemp * delmtemp * delmtemp - _delmo);
      final temp = delomg + delm;
      mm = xmdf + temp;
      argpm = argpdf - temp;
      final t3 = t2 * t;
      final t4 = t3 * t;
      tempa = tempa - _d2 * t2 - _d3 * t3 - _d4 * t4;
      tempe = tempe + _bstar * _cc5 * (math.sin(mm) - _sinmao);
      templ = templ +
          _t3cof * t3 +
          t4 * (_t4cof + t * _t5cof);
    }

    var nm = _noUnkozai;
    var em = _ecco;
    final inclm = _inclo;
    // 深空 dspace 不实现。

    if (nm <= 0.0) {
      throw Sgp4Exception(2, 'mean motion $nm <= 0');
    }

    final am = math.pow(_Wgs72.xke / nm, 2.0 / 3.0).toDouble() * tempa * tempa;
    nm = _Wgs72.xke / math.pow(am, 1.5).toDouble();
    em = em - tempe;

    if (em >= 1.0 || em < -0.001) {
      throw Sgp4Exception(1, 'eccentricity $em out of range');
    }
    if (em < 1.0e-6) {
      em = 1.0e-6;
    }
    mm = mm + _noUnkozai * templ;

    nodem = _mod2pi(nodem);
    argpm = _mod2pi(argpm);
    // xlm 不再单独模，直接用如下方式还原 mm。

    final sinim = math.sin(inclm);
    final cosim = math.cos(inclm);

    // 扰动根数（近地：= 平均根数）。
    final ep = em;
    final xincp = inclm;
    final argpp = argpm;
    final nodep = nodem;
    final mp = mm;
    final sinip = sinim;
    final cosip = cosim;

    final axnl = ep * math.cos(argpp);
    var temp = 1.0 / (am * (1.0 - ep * ep));
    final aynl = ep * math.sin(argpp) + temp * _aycof;
    final xl = mp + argpp + nodep + temp * _xlcof * axnl;

    // ---- 开普勒方程 ----
    final u = _mod2pi(xl - nodep);
    var eo1 = u;
    var tem5 = 9999.9;
    var ktr = 1;
    double sineo1;
    double coseo1;
    while (tem5.abs() >= 1.0e-12 && ktr <= 10) {
      sineo1 = math.sin(eo1);
      coseo1 = math.cos(eo1);
      tem5 = 1.0 - coseo1 * axnl - sineo1 * aynl;
      tem5 = (u - aynl * coseo1 + axnl * sineo1 - eo1) / tem5;
      if (tem5.abs() >= 0.95) {
        tem5 = tem5 > 0.0 ? 0.95 : -0.95;
      }
      eo1 += tem5;
      ktr++;
    }
    sineo1 = math.sin(eo1);
    coseo1 = math.cos(eo1);

    // ---- 短周期预备量 ----
    final ecose = axnl * coseo1 + aynl * sineo1;
    final esine = axnl * sineo1 - aynl * coseo1;
    final el2 = axnl * axnl + aynl * aynl;
    final pl = am * (1.0 - el2);
    if (pl < 0.0) {
      throw Sgp4Exception(4, 'semi-latus rectum $pl < 0');
    }

    final rl = am * (1.0 - ecose);
    final rdotl = math.sqrt(am) * esine / rl;
    final rvdotl = math.sqrt(pl) / rl;
    final betal = math.sqrt(1.0 - el2);
    temp = esine / (1.0 + betal);
    final sinu = am / rl * (sineo1 - aynl - axnl * temp);
    final cosu = am / rl * (coseo1 - axnl + aynl * temp);
    var su = math.atan2(sinu, cosu);
    final sin2u = (cosu + cosu) * sinu;
    final cos2u = 1.0 - 2.0 * sinu * sinu;
    temp = 1.0 / pl;
    final temp1 = 0.5 * _Wgs72.j2 * temp;
    final temp2 = temp1 * temp1;

    final mrt = rl * (1.0 - 1.5 * temp2 * betal * _con41) +
        0.5 * temp1 * _x1mth2 * cos2u;
    su = su - 0.25 * temp2 * _x7thm1 * sin2u;
    final xnode = nodep + 1.5 * temp2 * cosip * sin2u;
    final xinc = xincp + 1.5 * temp2 * cosip * sinip * cos2u;
    final mvt = rdotl - nm * temp1 * _x1mth2 * sin2u / _Wgs72.xke;
    final rvdot = rvdotl +
        nm * temp1 * (_x1mth2 * cos2u + 1.5 * _con41) / _Wgs72.xke;

    // ---- 方向向量 ----
    final sinsu = math.sin(su);
    final cossu = math.cos(su);
    final snod = math.sin(xnode);
    final cnod = math.cos(xnode);
    final sini = math.sin(xinc);
    final cosi = math.cos(xinc);
    final xmx = -snod * cosi;
    final xmy = cnod * cosi;
    final ux = xmx * sinsu + cnod * cossu;
    final uy = xmy * sinsu + snod * cossu;
    final uz = sini * sinsu;
    final vx = xmx * cossu - cnod * sinsu;
    final vy = xmy * cossu - snod * sinsu;
    final vz = sini * cossu;

    final mr = mrt * _Wgs72.radiusearthkm;
    final r = Vec3(mr * ux, mr * uy, mr * uz);
    final vkmpersec = _Wgs72.radiusearthkm * _Wgs72.xke / 60.0;
    final v = Vec3(
      (mvt * ux + rvdot * vx) * vkmpersec,
      (mvt * uy + rvdot * vy) * vkmpersec,
      (mvt * uz + rvdot * vz) * vkmpersec,
    );

    // 衰减判断。
    if (mrt < 1.0) {
      throw Sgp4Exception(6, 'mrt=$mrt < 1, satellite has decayed');
    }
    // argpp/nodep 仅供阅读，避免未用告警。
    assert(argpp.isFinite && nodep.isFinite);
    return (r: r, v: v);
  }

  double _mod2pi(double x) {
    var r = x % _twopi;
    if (r < 0) r += _twopi;
    return r;
  }

  // ------------------------------------------------------------------
  // 时间工具
  // ------------------------------------------------------------------

  /// UTC DateTime -> 儒略日（UT1 近似，忽略 UT1-UTC）。
  static double julianDate(DateTime utc) {
    final y = utc.year;
    final m = utc.month;
    final dayFloat = utc.day +
        (utc.hour +
                (utc.minute +
                        (utc.second +
                            utc.millisecond / 1000.0 +
                                utc.microsecond / 1000000.0) /
                            60.0) /
                    60.0) /
            24.0;
    final a = (14 - m) ~/ 12;
    final yyy = y + 4800 - a;
    final mmm = m + 12 * a - 3;
    return dayFloat +
        (153 * mmm + 2) / 5.0 +
        365 * yyy +
        yyy ~/ 4 -
        yyy ~/ 100 +
        yyy ~/ 400 -
        32045.0;
  }

  /// 格林尼治恒星时（rad），Vallado eq.3-45。
  static double gmst(double jdUt1) {
    const deg2rad = math.pi / 180.0;
    final tut1 = (jdUt1 - 2451545.0) / 36525.0;
    var temp = -6.2e-6 * tut1 * tut1 * tut1 +
        0.093104 * tut1 * tut1 +
        (876600.0 * 3600 + 8640184.812866) * tut1 +
        67310.54841; // 秒
    temp = (temp * deg2rad / 240.0) % _twopi;
    if (temp < 0.0) temp += _twopi;
    return temp;
  }
}

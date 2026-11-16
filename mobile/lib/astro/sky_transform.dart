/// 恒星坐标链：J2000 赤道 ↔ 当前赤道 ↔ 地平。
///
/// 为什么手机端要自己算
/// -------------------
/// 打包的星表是 **J2000 赤道坐标**（见 `scripts/build_mobile_sky_assets.py`）。
/// 存成地平坐标等于把某一刻的天焊死——"指哪算哪"就没了。所以每次显示都必须
/// 用**当前时刻 + 当前站址**现算地平坐标。
///
/// 口径
/// ----
/// 与桌面星象仪同一套（`mbdsdr_ai/celestial_geometry.py`），不是另造一份：
///
/// * GMST：Capitaine 2003（该模块的 `gmst`），**不是** IAU 1982 折叠式。
///   两者是不同物理模型，实测差 0.0145″–0.1261″；星象仪的 IAU 2006 岁差链
///   必须配这一个，混用会让整张星图相对地平线缓慢漂移。
/// * 岁差：IAU 2006 P03 三角度（ζ_A / z_A / θ_A），标准右手系三项式。
///   桌面用矩阵形式（含左右手基变换），本文件用等价的三角度闭式——
///   数学同一件事，差在舍入级。
/// * 章动：**不做**。IAU 2000B 主项量级 ±9″，比手机罗盘误差（±3–5°）小
///   三个数量级；做了是浪费，不做要写明白。
/// * ΔT：忽略（与桌面同口径）。约 69 s 的 TT−UT1 差对应 GMST 漂移 0.0048°，
///   小于罗盘误差。
/// * 折射：**不做**。地平线附近真实折射可达 34′，但那里的星本来就被大气消光
///   吃掉大半；手机端不使用折射的结果必须让上层知道（见 `SkyFix.refracted`）。
///
/// 全部函数是纯函数，可离线单测；不依赖 Flutter、不依赖网络。
///
/// 未验证声明
/// ----------
/// **本机无 Flutter 工具链**（`docs/parameter-unification.md:453` 已记录同一事实），
/// 本文件从未跑过 `flutter analyze` / `flutter test`。
/// 算式本身的正确性由 `tests/test_mobile_sky_algorithms.py` 在 Python 侧
/// 逐行复现并与桌面实现对拍来保证（alt/az 差 <15″，即章动量级）；
/// 但"这个 Dart 文件能编译、类型正确、运行时行为一致"**尚未验证**。
///
/// 在有 Flutter 的机器上必须跑：
///
///     flutter analyze
///     flutter test test/sky_transform_test.dart
library;

import 'dart:math' as math;

/// J2000.0 儒略日。
const double j2000Jd = 2451545.0;

/// 度 → 弧度。
double _rad(double deg) => deg * math.pi / 180.0;

/// 弧度 → 度。
double _deg(double rad) => rad * 180.0 / math.pi;

/// 把角度归一化到 [0, 360)。
double normalizeDeg(double deg) {
  final v = deg % 360.0;
  return v < 0 ? v + 360.0 : v;
}

/// 由 UTC 时刻求儒略日（UT1 近似）。
///
/// 用 Unix 纪元偏移而不是逐月累加：后者是闰年 bug 的常见来源。
double julianDateFromUtc(DateTime utc) {
  final u = utc.toUtc();
  // Unix 纪元 1970-01-01T00:00:00Z = JD 2440587.5
  final epochMs = DateTime.utc(1970).millisecondsSinceEpoch;
  final deltaMs = u.millisecondsSinceEpoch - epochMs;
  return 2440587.5 + deltaMs / 86400000.0;
}

/// 格林尼治平恒星时（度，[0,360)）。Capitaine 2003，与桌面星象仪同式。
///
/// 注意 `s += (t - tu) * 307.4771013` 这一项：它是 TT 与 UT1 的分歧项，
/// 漏掉会在高精度岁差链里引入系统性偏差。
double gmstDegCapitaine(double jdUt1) {
  // 与桌面实现一致：忽略 ΔT，TT 直接用 UT1 代入。
  final jdTt = jdUt1;
  final t = (jdTt - j2000Jd) / 36525.0;
  final tu = (jdUt1 - j2000Jd) / 36525.0;

  // UT1 当日秒数。
  final ut1Sec = (jdUt1 - jdUt1.floorToDouble() + 0.5) * 86400.0;

  var s = (((-0.000000002454 * t - 0.00000199708) * t - 0.0000002926) * t +
          0.092772110) *
      t *
      t;
  s += (t - tu) * 307.4771013;
  s += 8640184.79447825 * tu + 24110.5493771;
  s += ut1Sec;

  // 秒 → 度：1 秒时间 = 1/240 度。
  return normalizeDeg(s / 240.0);
}

/// 本地恒星时（度，[0,360)）。
double localSiderealDeg(double jdUt1, double longitudeDeg) =>
    normalizeDeg(gmstDegCapitaine(jdUt1) + longitudeDeg);

/// IAU 2006 P03 岁差角（角秒）。返回 (ζ_A, z_A, θ_A)。
///
/// 桌面 `precession_matrix` 用的是同一组多项式（Capitaine et al. 2003）；
/// 这里只取角度、不组矩阵，因为三点式闭式不需要矩阵。
({double zetaA, double zA, double thetaA}) precessionAnglesArcsec(double jdTt) {
  final t = (jdTt - j2000Jd) / 36525.0;
  final t2 = t * t;
  final t3 = t2 * t;
  final t4 = t3 * t;
  final t5 = t4 * t;

  final zetaA = 2.650545 +
      2306.083227 * t +
      0.2988499 * t2 +
      0.01801828 * t3 -
      0.000005971 * t4 -
      0.0000003173 * t5;

  final zA = -2.650545 +
      2306.077181 * t +
      1.0927348 * t2 +
      0.01826898 * t3 -
      0.000028596 * t4 -
      0.0000002904 * t5;

  final thetaA = 2004.191903 * t -
      0.4269350 * t2 -
      0.04183110 * t3 -
      0.000006059 * t4 -
      0.00000014543 * t5;

  return (zetaA: zetaA, zA: zA, thetaA: thetaA);
}

/// J2000 赤道坐标 → 当前（当日）赤道坐标。IAU 2006 岁差三角度闭式。
///
/// 这是标准的旋转三步：先绕天极转 ζ_A、再绕节点转 θ_A、再绕天极转 z_A。
/// 写成向量式比矩阵式少一次基变换，数值上也更稳（不会把左手系混进来）。
({double raDeg, double decDeg}) precessJ2000ToDate(
    double raDeg, double decDeg, double jdTt) {
  final a = precessionAnglesArcsec(jdTt);
  final zeta = _rad(a.zetaA / 3600.0);
  final z = _rad(a.zA / 3600.0);
  final theta = _rad(a.thetaA / 3600.0);

  final ra = _rad(raDeg);
  final dec = _rad(decDeg);

  final cosDec = math.cos(dec);
  final sinDec = math.sin(dec);
  final raZeta = ra + zeta;
  final cosRaZeta = math.cos(raZeta);
  final sinRaZeta = math.sin(raZeta);

  final bigA = cosDec * sinRaZeta;
  final bigB = math.cos(theta) * cosDec * cosRaZeta - math.sin(theta) * sinDec;
  final bigC = math.sin(theta) * cosDec * cosRaZeta + math.cos(theta) * sinDec;

  final raOut = _deg(math.atan2(bigA, bigB)) + _deg(z);
  final decOut = _deg(math.asin(bigC.clamp(-1.0, 1.0)));
  return (raDeg: normalizeDeg(raOut), decDeg: decOut);
}

/// 当前赤道坐标 → J2000 赤道坐标（岁差逆变换）。
///
/// 逆变换不是简单取负角：三个角的顺序要整体反转且都取负。
({double raDeg, double decDeg}) precessDateToJ2000(
    double raDeg, double decDeg, double jdTt) {
  final a = precessionAnglesArcsec(jdTt);
  final zeta = _rad(a.zetaA / 3600.0);
  final z = _rad(a.zA / 3600.0);
  final theta = _rad(a.thetaA / 3600.0);

  final ra = _rad(raDeg - _deg(z));
  final dec = _rad(decDeg);
  final cosDec = math.cos(dec);
  final sinDec = math.sin(dec);
  final cosRa = math.cos(ra);
  final sinRa = math.sin(ra);

  final bigA = cosDec * sinRa;
  final bigB = math.cos(theta) * cosDec * cosRa + math.sin(theta) * sinDec;
  final bigC = -math.sin(theta) * cosDec * cosRa + math.cos(theta) * sinDec;

  final raOut = _deg(math.atan2(bigA, bigB)) - _deg(zeta);
  final decOut = _deg(math.asin(bigC.clamp(-1.0, 1.0)));
  return (raDeg: normalizeDeg(raOut), decDeg: decOut);
}

/// 地平坐标结果。
class AzAlt {
  const AzAlt({required this.azDeg, required this.altDeg});

  /// 方位角（度，自北向东顺时针，[0,360)）。
  final double azDeg;

  /// 地平高度（度，地平线以上为正）。未做折射修正。
  final double altDeg;
}

/// J2000 赤道坐标 → 地平坐标（观测者站址 + 时刻）。
///
/// 链路：岁差（J2000→当日）→ 时角（LST − RA）→ 球面三角。
/// 与桌面 `j2000_to_altaz_many` 同一条链，差别只有章动（见文件头）。
AzAlt altAzFromJ2000({
  required double raDeg,
  required double decDeg,
  required double latDeg,
  required double lonDeg,
  required double jdUt1,
}) {
  final ofDate = precessJ2000ToDate(raDeg, decDeg, jdUt1);
  return altAzFromDate(
    raDeg: ofDate.raDeg,
    decDeg: ofDate.decDeg,
    latDeg: latDeg,
    lonDeg: lonDeg,
    jdUt1: jdUt1,
  );
}

/// 当日赤道坐标 → 地平坐标（不做岁差）。
AzAlt altAzFromDate({
  required double raDeg,
  required double decDeg,
  required double latDeg,
  required double lonDeg,
  required double jdUt1,
}) {
  final lst = _rad(localSiderealDeg(jdUt1, lonDeg));
  final ra = _rad(raDeg);
  final dec = _rad(decDeg);
  final lat = _rad(latDeg);

  final hourAngle = lst - ra;
  final sinAlt = math.sin(dec) * math.sin(lat) +
      math.cos(dec) * math.cos(lat) * math.cos(hourAngle);
  final alt = math.asin(sinAlt.clamp(-1.0, 1.0));

  // 方位角自北向东：用 atan2 而不是 acos，避免 acos 在 0/180° 附近丢符号，
  // 也省掉"东侧还是西侧"的分支判断。
  final y = -math.cos(dec) * math.sin(hourAngle);
  final x = math.sin(dec) * math.cos(lat) -
      math.cos(dec) * math.sin(lat) * math.cos(hourAngle);
  final az = math.atan2(y, x);

  return AzAlt(azDeg: normalizeDeg(_deg(az)), altDeg: _deg(alt));
}

/// 地平坐标 → 当日赤道坐标（逆变换）。用于往返自检与点星反查。
({double raDeg, double decDeg}) dateFromAltAz({
  required double azDeg,
  required double altDeg,
  required double latDeg,
  required double lonDeg,
  required double jdUt1,
}) {
  final az = _rad(azDeg);
  final alt = _rad(altDeg);
  final lat = _rad(latDeg);

  final sinDec = math.sin(alt) * math.sin(lat) +
      math.cos(alt) * math.cos(lat) * math.cos(az);
  final dec = math.asin(sinDec.clamp(-1.0, 1.0));

  final y = -math.cos(alt) * math.sin(az);
  final x = math.sin(alt) * math.cos(lat) -
      math.cos(alt) * math.sin(lat) * math.cos(az);
  final hourAngle = math.atan2(y, x);

  final lst = _rad(localSiderealDeg(jdUt1, lonDeg));
  final ra = lst - hourAngle;
  return (raDeg: normalizeDeg(_deg(ra)), decDeg: _deg(dec));
}

/// 地平坐标 → J2000 赤道坐标（往返逆，含岁差逆）。
({double raDeg, double decDeg}) j2000FromAltAz({
  required double azDeg,
  required double altDeg,
  required double latDeg,
  required double lonDeg,
  required double jdUt1,
}) {
  final ofDate = dateFromAltAz(
    azDeg: azDeg,
    altDeg: altDeg,
    latDeg: latDeg,
    lonDeg: lonDeg,
    jdUt1: jdUt1,
  );
  return precessDateToJ2000(ofDate.raDeg, ofDate.decDeg, jdUt1);
}

/// 两点的球面中心角（度）。用于角距判定与"视场里有没有这颗"。
double angularSeparationDeg(AzAlt a, AzAlt b) {
  final alt1 = _rad(a.altDeg);
  final alt2 = _rad(b.altDeg);
  final dAz = _rad(b.azDeg - a.azDeg);
  final cosSep = math.sin(alt1) * math.sin(alt2) +
      math.cos(alt1) * math.cos(alt2) * math.cos(dAz);
  return _deg(math.acos(cosSep.clamp(-1.0, 1.0)));
}

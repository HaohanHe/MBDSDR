/// 天球 → 屏幕的透视投影（针孔相机）。
///
/// 为什么是透视，不是等距方位
/// -------------------------
/// 桌面全天球模式用等距方位投影（AzimuthalEquidistant），因为它要装下 160° 的天空。
/// 但"举着手机指天"是**看向一个窗口**：视场中心无畸变、视场外被裁掉、抬头低头的
/// 感觉像透过一块玻璃看——这就是针孔透视。用户原话"像手电筒照到哪里亮哪里"，
/// 说的也是这件事。
///
/// 口径
/// ----
/// 与 `mbdsdr_ai/sky_interaction.py` 的透视路径**逐式对齐**，不是另写一套：
///
/// * 旋转：`build_view_rotation`（视中心 → 视图系 (0,0,-1)）
/// * 投影：`PerspectiveProjection._forward`（`x_n = v[0]/-v[2]`）
/// * 缩放：`PerspectiveProjection.screen_scale`（`(min_dim/2)/tan(fov/2)`）
/// * 落屏：`_to_screen_arrays`（`px = cx + y_n·ppr`，`py = cy - x_n·ppr`）
///
/// 桌面那份是唯一真值；本文件是它在 Dart 侧的移植。两边一旦出现分歧，
/// 以桌面为准，改本文件。
///
/// 验证记录（2026-11-18）
/// ----------
/// 算法正确性已用 Python 侧同式实现与桌面矩阵实现对拍（alt/az 差 <15″）；
/// Dart 侧亦已实测：
///
///     Flutter 3.50.0 / Dart 3.14.0
///     flutter analyze                            -> 0 error
///     flutter test test/sky_projection_test.dart -> 20 passed
///     投影对拍 84 点，最大像素偏差 0.0000
///
/// 2026-11-22 增补：`isDirectionDrawable` / `clipSegmentToSkyDomain` 与桌面
/// `is_valid_vec` / `clip_segment_to_domain` 同口径（投影有效域 + 大圆二分），
/// 该文件用例数随之为 20。
///
/// 工具链在 `D:/mbdsdr/toolchain/flutter`，不在仓库内。
library;

import 'dart:math' as math;

import 'package:mbdsdr_mobile/astro/sky_transform.dart';

/// 视图状态：看向哪、看多宽、画布多大。
class SkyView {
  const SkyView({
    required this.centerAzDeg,
    required this.centerAltDeg,
    required this.fovDeg,
    required this.width,
    required this.height,
    this.rotationDeg = 0.0,
  });

  /// 视中心方位角（度）。
  final double centerAzDeg;

  /// 视中心地平高度（度）。
  final double centerAltDeg;

  /// 视场角（度）——按**短边**度量，与桌面 `StelProjectorParams.fov` 同义。
  final double fovDeg;

  final double width;
  final double height;

  /// 画面旋转（度）。手机横竖屏切换或望远镜正像镜造成的整体旋转。
  final double rotationDeg;

  /// 短边像素。视场角按它度量，所以宽屏不会让视场竖向变窄。
  double get minDim => math.min(width, height);

  double get centerX => width / 2.0;
  double get centerY => height / 2.0;

  /// 每弧度多少像素：`(min_dim/2) / tan(fov/2)`。
  double get pixelPerRad {
    if (minDim <= 0) return 1.0;
    final half = fovDeg * math.pi / 180.0 * 0.5;
    final t = math.tan(half);
    if (t.abs() < 1e-12) return 1.0;
    return (minDim * 0.5) / t;
  }
}

/// 屏幕点（像素）。
class ScreenPoint {
  const ScreenPoint(this.x, this.y);
  final double x;
  final double y;

  @override
  String toString() => 'ScreenPoint($x, $y)';
}

/// 视图系单位向量。
class _Vec3 {
  const _Vec3(this.x, this.y, this.z);
  final double x, y, z;
}

/// 视图旋转矩阵（行优先：三行分别是 x_v / y_v / z_v）。
///
/// 与桌面 `build_view_rotation` 同式，包括天顶邻域那一支——天顶附近
/// "屏幕上方向"由整体姿态决定，不是重力，这一支不写会在大仰角时翻滚。
List<_Vec3> _viewRotation(double centerAzDeg, double centerAltDeg) {
  final caz = centerAzDeg * math.pi / 180.0;
  final calt = centerAltDeg * math.pi / 180.0;

  if (math.cos(calt).abs() < 1e-3) {
    return [
      _Vec3(math.cos(caz), math.sin(caz), 0.0),
      _Vec3(-math.sin(caz), math.cos(caz), 0.0),
      _Vec3(0.0, 0.0, 1.0),
    ];
  }

  final sAlt = math.sin(calt);
  final cAlt = math.cos(calt);
  final xv = _Vec3(
      -sAlt * math.cos(caz), -sAlt * math.sin(caz), -cAlt);
  final zv = _Vec3(
      -cAlt * math.cos(caz), -cAlt * math.sin(caz), sAlt);
  // y_v = x_v × z_v
  final yv = _Vec3(
    xv.y * zv.z - xv.z * zv.y,
    xv.z * zv.x - xv.x * zv.z,
    xv.x * zv.y - xv.y * zv.x,
  );
  return [xv, yv, zv];
}

double _dot(_Vec3 a, _Vec3 b) => a.x * b.x + a.y * b.y + a.z * b.z;

/// 地平坐标 → 屏幕像素。返回 null 表示不可见（在相机背面）。
///
/// 注意：返回非 null **不代表在画布内**——画布裁剪由调用方按 `SkyView` 边界做。
/// 分开的理由是箭头、指向线这类元素需要"画到画布外"的坐标才能正确裁到边界。
ScreenPoint? projectAzAlt(double azDeg, double altDeg, SkyView view) {
  final az = azDeg * math.pi / 180.0;
  final alt = altDeg * math.pi / 180.0;
  final ca = math.cos(alt);
  // 地平系：+x=北, +y=东, +z=天底
  final v = _Vec3(ca * math.cos(az), ca * math.sin(az), -math.sin(alt));

  final rot = _viewRotation(view.centerAzDeg, view.centerAltDeg);
  final vx = _dot(rot[0], v);
  final vy = _dot(rot[1], v);
  final vz = _dot(rot[2], v);

  final denom = -vz;
  // 相机背面：透视除法无意义。返回 null 而不是 inf，
  // 让"不可见"成为一种显式状态，调用方没法忘记处理。
  if (denom <= 1e-12) return null;

  final xn = vx / denom;
  final yn = vy / denom;

  final ppr = view.pixelPerRad;
  var px = view.centerX + yn * ppr;
  var py = view.centerY - xn * ppr;

  if (view.rotationDeg.abs() > 1e-9) {
    final ang = view.rotationDeg * math.pi / 180.0;
    final cs = math.cos(ang);
    final sn = math.sin(ang);
    final dx = px - view.centerX;
    final dy = py - view.centerY;
    px = view.centerX + dx * cs - dy * sn;
    py = view.centerY + dx * sn + dy * cs;
  }
  return ScreenPoint(px, py);
}

/// 屏幕像素 → 地平坐标（反投影）。用于点星拾取与"我指到了哪"。
///
/// 返回 null 表示画布上这个点对应不到天空（理论上不会发生，除非 FOV 非法）。
AzAlt? unprojectToAzAlt(double px, double py, SkyView view) {
  final ppr = view.pixelPerRad;
  if (ppr.abs() < 1e-12) return null;

  var dx = px - view.centerX;
  var dy = py - view.centerY;

  // 先撤销画面旋转，再撤销投影缩放。
  if (view.rotationDeg.abs() > 1e-9) {
    final ang = -view.rotationDeg * math.pi / 180.0;
    final cs = math.cos(ang);
    final sn = math.sin(ang);
    final rx = dx * cs - dy * sn;
    final ry = dx * sn + dy * cs;
    dx = rx;
    dy = ry;
  }

  final yn = dx / ppr;
  final xn = -dy / ppr;

  // 反投影：d = sqrt(1+x²+y²); v_view = (x/d, y/d, -1/d)
  final d = math.sqrt(1.0 + xn * xn + yn * yn);
  final vv = _Vec3(xn / d, yn / d, -1.0 / d);

  // 视图基是正交的，逆旋转 = 转置：v = Σ (basis_i · v_view) * basis_i
  final rot = _viewRotation(view.centerAzDeg, view.centerAltDeg);
  final v = _Vec3(
    rot[0].x * vv.x + rot[1].x * vv.y + rot[2].x * vv.z,
    rot[0].y * vv.x + rot[1].y * vv.y + rot[2].y * vv.z,
    rot[0].z * vv.x + rot[1].z * vv.y + rot[2].z * vv.z,
  );

  final alt = math.asin((-v.z).clamp(-1.0, 1.0)) * 180.0 / math.pi;
  final az = math.atan2(v.y, v.x) * 180.0 / math.pi;
  return AzAlt(azDeg: normalizeDeg(az), altDeg: alt);
}

/// 画布边界裁剪：线段 (p1,p2) 裁到矩形内（Liang–Barsky）。
///
/// 为什么必须自己有这一份
/// ---------------------
/// Qt 的 `QPainter` 会自动裁掉画布外的部分，所以桌面端"线到边界为止"是免费的。
/// 但**手机端没有这一步**：Flutter 的 `Canvas.drawLine` 同样会裁，可是星官线是
/// 一整条 `Path`，一旦某个端点落在极端坐标（透视下 x_n 可以到 1e6），
/// 浮点精度会把整条线画花。所以先在数据层裁掉，画的时候只有画布内的段。
///
/// 返回 null 表示这段完全在画布外（不该画）。
({ScreenPoint a, ScreenPoint b})? clipSegmentToRect(
    ScreenPoint p1, ScreenPoint p2, double width, double height) {
  var x0 = p1.x, y0 = p1.y, x1 = p2.x, y1 = p2.y;

  // 完全非有限 = 不可见，不是"在很远处"。
  if (!x0.isFinite || !y0.isFinite || !x1.isFinite || !y1.isFinite) {
    return null;
  }

  final dx = x1 - x0;
  final dy = y1 - y0;

  // Liang–Barsky 参数区间 [t0, t1]
  var t0 = 0.0;
  var t1 = 1.0;

  bool clip(double p, double q) {
    if (p.abs() < 1e-12) {
      // 平行于该边界：若在外侧则整段丢弃。
      return q >= 0;
    }
    final r = q / p;
    if (p < 0) {
      if (r > t1) return false;
      if (r > t0) t0 = r;
    } else {
      if (r < t0) return false;
      if (r < t1) t1 = r;
    }
    return true;
  }

  if (!clip(-dx, x0)) return null; // 左
  if (!clip(dx, width - x0)) return null; // 右
  if (!clip(-dy, y0)) return null; // 上
  if (!clip(dy, height - y0)) return null; // 下

  if (t0 > t1) return null;

  return (
    a: ScreenPoint(x0 + t0 * dx, y0 + t0 * dy),
    b: ScreenPoint(x0 + t1 * dx, y0 + t1 * dy),
  );
}

/// 粗判：这个屏幕点是否落在画布内（留 [margin] 像素余量）。
bool isOnCanvas(ScreenPoint p, double width, double height,
    {double margin = 0.0}) {
  return p.x >= -margin &&
      p.x <= width + margin &&
      p.y >= -margin &&
      p.y <= height + margin;
}

// ---------------------------------------------------------------------------
// 投影有效性域与跨域裁剪（spec Task 10.2）
//
// 参考 aladin-lite `src/core/src/math/projection/domain/`（LGPLv3）的
// **ProjDef + SDF 思路**：先判「这个方向在不在投影有效域内」，再把越界
// 端点推到边界。代码全部重写，未复制其源码。
//
// 手机端只有透视：有效域 = 相机前方（`-v_z > 0` 的全平面），域外只有
// 「相机背后」。桌面同口径见 `mbdsdr_ai/celestial_geometry.py` 的
// `is_valid_vec` 与 `mbdsdr_ai/sky_interaction.py` 的
// `clip_segment_to_domain`。
// ---------------------------------------------------------------------------

/// 该地平方向在透视投影下是否有屏幕坐标（相机背后 -> false）。
bool isDirectionDrawable(double azDeg, double altDeg, SkyView view) {
  final az = azDeg * math.pi / 180.0;
  final alt = altDeg * math.pi / 180.0;
  final ca = math.cos(alt);
  final v = _Vec3(ca * math.cos(az), ca * math.sin(az), -math.sin(alt));
  final rot = _viewRotation(view.centerAzDeg, view.centerAltDeg);
  return -_dot(rot[2], v) > 1e-12;
}

_Vec3 _unitFromAzAlt(double azDeg, double altDeg) {
  final az = azDeg * math.pi / 180.0;
  final alt = altDeg * math.pi / 180.0;
  final ca = math.cos(alt);
  return _Vec3(ca * math.cos(az), ca * math.sin(az), -math.sin(alt));
}

_Vec3 _normalize(_Vec3 v) {
  final n = math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
  if (n <= 0.0 || !n.isFinite) return const _Vec3(0.0, 0.0, -1.0);
  return _Vec3(v.x / n, v.y / n, v.z / n);
}

/// 单位向量球面线性插值；对跖点走一条确定的垂面路径，不产生 NaN。
_Vec3 _slerp(_Vec3 a, _Vec3 b, double t) {
  final e = t.clamp(0.0, 1.0);
  final dot = _dot(a, b).clamp(-1.0, 1.0);
  final theta = math.acos(dot);
  if (theta < 1e-9) return e >= 1.0 ? b : a;
  if (math.pi - theta < 1e-6) {
    final axis = a.x.abs() < 0.9
        ? const _Vec3(1.0, 0.0, 0.0)
        : const _Vec3(0.0, 1.0, 0.0);
    final u = _normalize(_Vec3(
      a.y * axis.z - a.z * axis.y,
      a.z * axis.x - a.x * axis.z,
      a.x * axis.y - a.y * axis.x,
    ));
    return _normalize(_Vec3(
      a.x * math.cos(e * math.pi) + u.x * math.sin(e * math.pi),
      a.y * math.cos(e * math.pi) + u.y * math.sin(e * math.pi),
      a.z * math.cos(e * math.pi) + u.z * math.sin(e * math.pi),
    ));
  }
  final s = math.sin(theta);
  final wa = math.sin((1.0 - e) * theta) / s;
  final wb = math.sin(e * theta) / s;
  return _normalize(_Vec3(
    a.x * wa + b.x * wb,
    a.y * wa + b.y * wb,
    a.z * wa + b.z * wb,
  ));
}

/// 把一条天球大圆段裁到调用方给定的有效域，返回 0 或 2 个屏幕点。
///
/// [drawable] 默认只判投影有效域；调用方要叠地平余量时传自己的谓词
/// （例如 `alt > -altMarginDeg && isDirectionDrawable(...)`）。
/// 两端都在域内 -> 原样两点；一端在域外 -> 域内端点 + 边界点（保持原段
/// 方向顺序）；两端都在域外 -> 空。相邻星官段很短，域解析，用大圆二分
/// 求边界即可，不需要逐点 ray marching。
List<ScreenPoint> clipSegmentToSkyDomain(
  double az0Deg,
  double alt0Deg,
  double az1Deg,
  double alt1Deg,
  SkyView view, {
  bool Function(double azDeg, double altDeg)? drawable,
  int iterations = 12,
}) {
  final inside =
      drawable ?? (double az, double alt) => isDirectionDrawable(az, alt, view);
  final d0 = inside(az0Deg, alt0Deg);
  final d1 = inside(az1Deg, alt1Deg);
  if (d0 && d1) {
    final p0 = projectAzAlt(az0Deg, alt0Deg, view);
    final p1 = projectAzAlt(az1Deg, alt1Deg, view);
    if (p0 == null || p1 == null) return const <ScreenPoint>[];
    return <ScreenPoint>[p0, p1];
  }
  if (!d0 && !d1) return const <ScreenPoint>[];

  final flipped = d1;
  final azIn = flipped ? az1Deg : az0Deg;
  final altIn = flipped ? alt1Deg : alt0Deg;
  final azOut = flipped ? az0Deg : az1Deg;
  final altOut = flipped ? alt1Deg : alt0Deg;

  final vIn = _unitFromAzAlt(azIn, altIn);
  final vOut = _unitFromAzAlt(azOut, altOut);
  var lo = 0.0;
  var hi = 1.0;
  for (var i = 0; i < iterations; i++) {
    final mid = 0.5 * (lo + hi);
    final vm = _slerp(vIn, vOut, mid);
    final az = math.atan2(vm.y, vm.x) * 180.0 / math.pi;
    final alt = math.asin((-vm.z).clamp(-1.0, 1.0)) * 180.0 / math.pi;
    if (inside(az, alt)) {
      lo = mid;
    } else {
      hi = mid;
    }
  }

  final vb = _slerp(vIn, vOut, lo);
  final azB = math.atan2(vb.y, vb.x) * 180.0 / math.pi;
  final altB = math.asin((-vb.z).clamp(-1.0, 1.0)) * 180.0 / math.pi;
  final edge = projectAzAlt(azIn, altIn, view);
  final boundary = projectAzAlt(azB, altB, view);
  if (edge == null || boundary == null) return const <ScreenPoint>[];
  return flipped
      ? <ScreenPoint>[boundary, edge]
      : <ScreenPoint>[edge, boundary];
}


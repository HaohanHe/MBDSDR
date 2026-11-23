// ============================================================================
// PoseReporter —— 手机把当前朝向作为 `sky-pose/1` 上报给桌面权威端（M3.1）。
// ----------------------------------------------------------------------------
// * 只有 lens/eyepiece 且已配对、已拿到 device id 时才可用；其它角色/未配对
//   一律本地拒绝（不拿手持机去冒充镜头/目镜）。
// * 朝向来自真实传感器：航向缺失或俯仰缺失就拒绝上报，不用 0° 顶替。
// * `accuracy_deg` 由调用方给（通常是本页观测到的航向波动）；取不到时用
//   保守的 10°，并在界面上说明这是估计值，不是标定结果。
// * 客户端可注入，单测完全离线。
// ============================================================================

import 'device_gateway_client.dart';
import 'settings_service.dart';

/// 上报时未提供精度时的保守估计（度）。不是标定值，只用于让融合权重可算。
const double kDefaultPoseAccuracyDeg = 10.0;

/// 目镜识别结果（只随 eyepiece 角色的位姿上报；不参与粗指向融合）。
class PoseIdentification {
  const PoseIdentification({
    this.name,
    this.hip,
    this.separationDeg,
  });

  /// 星名（可为空；与 [hip] 至少给一个）。
  final String? name;

  /// HIP 编号。
  final int? hip;

  /// 与视场中心的角距（度）。
  final double? separationDeg;

  bool get isEmpty =>
      (name == null || name!.trim().isEmpty) &&
      hip == null &&
      separationDeg == null;

  Map<String, dynamic> toJson() => <String, dynamic>{
        if (name != null && name!.trim().isNotEmpty) 'name': name!.trim(),
        if (hip != null) 'hip': hip,
        if (separationDeg != null) 'separation_deg': separationDeg,
      };
}

class PoseReporter {
  PoseReporter({
    required SettingsService settings,
    DeviceGatewayClient Function(String host, int port)? clientBuilder,
    DateTime Function()? clock,
  })  : _settings = settings,
        _clientBuilder = clientBuilder,
        _clock = clock ?? DateTime.now;

  final SettingsService _settings;
  final DeviceGatewayClient Function(String host, int port)? _clientBuilder;
  final DateTime Function() _clock;

  SettingsService get settings => _settings;

  /// 是否具备上报条件：已配对 + 角色可上报 + 有 device id + 有桌面主机。
  bool get available => unavailableReason == null;

  /// 不可用的具体原因（可用时为 null）；界面直接显示，不猜。
  String? get unavailableReason {
    final SettingsService s = _settings;
    if (!s.deviceGatewayPaired) return '未配对：先在设置页完成多设备网关配对';
    if (!kPoseRoles.contains(s.deviceGatewayRole)) {
      return '当前角色为 ${s.deviceGatewayRole}（只读），只有 lens/eyepiece 能上报位姿';
    }
    if (s.deviceGatewayDeviceId.isEmpty) {
      return '缺少设备 id：请重新配对一次';
    }
    if (s.controlHubHost.trim().isEmpty) return '未填写桌面主机地址';
    return null;
  }

  /// 上报一条位姿。参数为 null 表示对应传感器缺失——直接抛错，不补 0。
  ///
  /// [identification] 仅在角色为 eyepiece 时允许（镜头机不冒充认星结果）；
  /// 内容为空对象也视为非法输入，不发送空识别块。
  Future<Map<String, dynamic>> report({
    required double? headingDeg,
    required double? pitchDeg,
    double? rollDeg,
    double? accuracyDeg,
    PoseIdentification? identification,
  }) async {
    final String? why = unavailableReason;
    if (why != null) throw DeviceGatewayException(why);
    if (headingDeg == null || !headingDeg.isFinite) {
      throw DeviceGatewayException('没有罗盘航向，不能上报位姿');
    }
    if (pitchDeg == null || !pitchDeg.isFinite) {
      throw DeviceGatewayException('没有 IMU 俯仰角，不能上报位姿');
    }
    if (pitchDeg < -90.0 || pitchDeg > 90.0) {
      throw DeviceGatewayException('俯仰角越界（$pitchDeg°），拒绝上报');
    }
    if (identification != null) {
      if (_settings.deviceGatewayRole != 'eyepiece') {
        throw DeviceGatewayException('只有目镜角色可以携带识别结果');
      }
      if (identification.isEmpty) {
        throw DeviceGatewayException('识别结果为空，拒绝上报空识别块');
      }
    }
    final double az = ((headingDeg % 360.0) + 360.0) % 360.0;
    final double roll = (rollDeg != null && rollDeg.isFinite) ? rollDeg : 0.0;
    final double rawAcc =
        (accuracyDeg != null && accuracyDeg.isFinite) ? accuracyDeg : kDefaultPoseAccuracyDeg;
    // 网关接受 (0, 30]；这里夹到 [1, 30]，既不送 0 也不送离谱大数。
    final double acc = rawAcc.clamp(1.0, 30.0);
    final int t = (_clock().millisecondsSinceEpoch / 1000).floor();

    final SettingsService s = _settings;
    final DeviceGatewayClient client = _clientBuilder != null
        ? _clientBuilder(s.controlHubHost.trim(), s.deviceGatewayPort)
        : DeviceGatewayClient(
            host: s.controlHubHost.trim(), port: s.deviceGatewayPort);
    return client.postPose(
      s.deviceGatewayToken,
      deviceId: s.deviceGatewayDeviceId,
      role: s.deviceGatewayRole,
      tUtcSeconds: t.toDouble(),
      azDeg: az,
      altDeg: pitchDeg,
      accuracyDeg: acc,
      rollDeg: roll,
      identification: identification?.toJson(),
    );
  }
}

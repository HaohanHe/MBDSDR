"""
MBDSDR AI - 云台/旋转器控制模块
==================================

手自一体的天线指向控制，三种可插拔后端：

1. board_pwm  — ai-sdr-mini 板载 ESP32 LEDC 直驱 2 轴舵机云台
                （方位 IO14 / 俯仰 IO15，+5V 供电）
2. rotctld    — Hamlib rotctld 协议，兼容专业业余无线电旋转器
                （大型八木/抛物面旋转器，TCP 端口 4533）
3. manual     — 无电机，AI 给出方位/俯仰指令，人工转动，
                IMU+磁力计回读实际姿态做闭环引导

闭环逻辑（所有后端通用）：
    卫星方位角/仰角（sgp4）
        → 下发目标角度
        → 执行机构转动（或人工）
        → IMU+磁力计回读天线实际姿态
        → 偏差收敛到波束宽度内
        → 通知"已对准"

模型为大：本模块只提供"能力/工具"，是否跟踪、跟踪哪颗卫星
由对话中的 AI 决定，不在 UI 上写死"找卫星"按钮。
"""

from __future__ import annotations

import math
import socket
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple


# ========================================================================
# 数据结构
# ========================================================================

class GimbalMode(str, Enum):
    BOARD_PWM = "board_pwm"    # 板载 ESP32 舵机
    ROTCTLD = "rotctld"        # Hamlib 旋转器
    MANUAL = "manual"          # 人工+AR引导
    OFFLINE = "offline"        # 未连接/模拟


@dataclass
class GimbalPose:
    """天线姿态（度）。azimuth=方位角[0,360)，elevation=仰角[-90,90]。"""
    azimuth: float = 0.0
    elevation: float = 0.0
    timestamp: float = 0.0

    def angular_error(self, target: "GimbalPose") -> Tuple[float, float]:
        """返回到目标的（方位误差, 俯仰误差），单位度，带符号。"""
        az_err = (target.azimuth - self.azimuth + 180.0) % 360.0 - 180.0
        el_err = target.elevation - self.elevation
        return az_err, el_err

    def aligned(self, target: "GimbalPose",
                az_tolerance: float = 5.0, el_tolerance: float = 5.0) -> bool:
        az_err, el_err = self.angular_error(target)
        return abs(az_err) <= az_tolerance and abs(el_err) <= el_tolerance


@dataclass
class GimbalStatus:
    mode: GimbalMode = GimbalMode.OFFLINE
    connected: bool = False
    target: Optional[GimbalPose] = None
    current: Optional[GimbalPose] = None
    moving: bool = False
    backend_info: str = ""
    # 舵机标定（角度→脉宽）
    pwm_min_us: float = 500.0     # 0° 对应脉宽
    pwm_max_us: float = 2500.0    # 180° 对应脉宽
    # 机械限位
    az_min: float = 0.0
    az_max: float = 360.0
    el_min: float = 0.0
    el_max: float = 90.0


# ========================================================================
# 角度/脉宽换算（板载舵机）
# ========================================================================

def angle_to_servo_us(angle_deg: float,
                      pwm_min_us: float = 500.0,
                      pwm_max_us: float = 2500.0,
                      angle_min: float = 0.0,
                      angle_max: float = 180.0) -> float:
    """角度转舵机脉宽（us）。ESP32 LEDC 用。"""
    angle_deg = max(angle_min, min(angle_max, angle_deg))
    ratio = (angle_deg - angle_min) / (angle_max - angle_min)
    return pwm_min_us + ratio * (pwm_max_us - pwm_min_us)


def clamp_angle(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ========================================================================
# 后端：Hamlib rotctld
# ========================================================================

class RotctldClient:
    """
    Hamlib rotctld 协议客户端（TCP，默认端口 4533）。

    协议（文本，每行一条）：
      P <az> <el>   设置位置
      p             查询位置 → 返回 az\\nel\\n
      \\chkstop      停止
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 4533, timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def _command(self, cmd: str, n_lines: int = 1) -> List[str]:
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
            s.sendall((cmd + "\n").encode("ascii"))
            buf = b""
            # rotctld 回复以换行分隔，读够 n_lines 或超时
            s.settimeout(self.timeout)
            try:
                while buf.count(b"\n") < n_lines:
                    chunk = s.recv(256)
                    if not chunk:
                        break
                    buf += chunk
            except socket.timeout:
                pass
        return [line.strip() for line in buf.decode("ascii", errors="replace").splitlines() if line.strip()]

    def get_position(self) -> GimbalPose:
        lines = self._command("p", n_lines=2)
        az = float(lines[0])
        el = float(lines[1])
        return GimbalPose(azimuth=az % 360.0, elevation=clamp_angle(el, -90, 90))

    def set_position(self, az: float, el: float) -> bool:
        az = az % 360.0
        el = clamp_angle(el, 0, 90)
        lines = self._command(f"P {az:.2f} {el:.2f}", n_lines=1)
        # rotctld 成功返回 RPRT 0
        return any("0" in ln for ln in lines)

    def stop(self) -> bool:
        lines = self._command("S", n_lines=1)
        return any("0" in ln for ln in lines)

    def ping(self) -> bool:
        try:
            self.get_position()
            return True
        except Exception:
            return False


# ========================================================================
# 后端：板载 ESP32（通过 WebSocket MCP / HTTP，运行在 mbdsdr_mcp_client 之上）
# ========================================================================

class BoardGimbalClient:
    """
    ai-sdr-mini 板载舵机控制。

    ESP32 固件暴露 gimbal_set / gimbal_get MCP 方法，
    本客户端通过一个可注入的 send_callable 发送命令
    （桌面端/CLI 传入 WebSocket 发送函数，避免硬耦合）。
    """

    def __init__(self, send_callable: Optional[Callable[[str, dict], dict]] = None):
        # send_callable(method, params) -> result_dict
        self._send = send_callable

    def set_send_callable(self, send_callable: Callable[[str, dict], dict]):
        self._send = send_callable

    def set_position(self, az: float, el: float) -> dict:
        if self._send is None:
            return {"ok": False, "error": "未连接板子（无发送通道）"}
        return self._send("gimbal_set", {"az": az % 360.0, "el": clamp_angle(el, 0, 90)})

    def get_position(self) -> Optional[GimbalPose]:
        if self._send is None:
            return None
        r = self._send("gimbal_get", {})
        if r.get("ok"):
            return GimbalPose(azimuth=r.get("az", 0), elevation=r.get("el", 0))
        return None


# ========================================================================
# 统一控制器（手自一体）
# ========================================================================

class GimbalController:
    """统一云台控制器，后端可插拔。"""

    def __init__(self):
        self.status = GimbalStatus()
        self.rotctld: Optional[RotctldClient] = None
        self.board = BoardGimbalClient()
        # 姿态回读源（由 pose.py 的 IMU+磁力计融合结果注入）
        self._pose_provider: Optional[Callable[[], GimbalPose]] = None

    # ---- 后端管理 ----
    def use_manual(self) -> GimbalStatus:
        self.status.mode = GimbalMode.MANUAL
        self.status.connected = True
        self.status.backend_info = "人工模式：AI 指引，IMU 闭环"
        return self.status

    def use_board(self, send_callable: Optional[Callable] = None) -> GimbalStatus:
        if send_callable:
            self.board.set_send_callable(send_callable)
        self.status.mode = GimbalMode.BOARD_PWM
        self.status.connected = self.board._send is not None
        self.status.backend_info = "板载 ESP32 LEDC：方位 IO14 / 俯仰 IO15"
        return self.status

    def use_rotctld(self, host: str = "127.0.0.1", port: int = 4533) -> GimbalStatus:
        self.rotctld = RotctldClient(host, port)
        self.status.mode = GimbalMode.ROTCTLD
        self.status.connected = self.rotctld.ping()
        self.status.backend_info = f"Hamlib rotctld {host}:{port}"
        return self.status

    def set_pose_provider(self, provider: Callable[[], GimbalPose]):
        """注入 IMU+磁力计姿态回读函数。"""
        self._pose_provider = provider

    # ---- 读姿态 ----
    def read_pose(self) -> GimbalPose:
        """
        读取天线当前姿态。
        优先用 IMU+磁力计融合姿态（板子/手机），
        其次用旋转器回读角度。
        """
        if self._pose_provider is not None:
            try:
                pose = self._pose_provider()
                self.status.current = pose
                return pose
            except Exception:
                pass
        if self.status.mode == GimbalMode.ROTCTLD and self.rotctld:
            try:
                pose = self.rotctld.get_position()
                self.status.current = pose
                return pose
            except Exception:
                pass
        return self.status.current or GimbalPose()

    # ---- 运动控制 ----
    def point_to(self, az: float, el: float) -> Dict:
        """指向目标方位角/仰角。返回执行结果与人工引导信息。"""
        target = GimbalPose(azimuth=az % 360.0, elevation=clamp_angle(el, 0, 90))
        self.status.target = target
        result: Dict = {"target_az": target.azimuth, "target_el": target.elevation}

        if self.status.mode == GimbalMode.BOARD_PWM:
            r = self.board.set_position(target.azimuth, target.elevation)
            result["dispatched"] = r.get("ok", False)
            result["backend"] = "board_pwm"

        elif self.status.mode == GimbalMode.ROTCTLD and self.rotctld:
            ok = self.rotctld.set_position(target.azimuth, target.elevation)
            result["dispatched"] = ok
            result["backend"] = "rotctld"

        elif self.status.mode == GimbalMode.MANUAL:
            result["dispatched"] = False
            result["backend"] = "manual"

        else:
            result["dispatched"] = False
            result["backend"] = "offline"

        # 无论哪种模式，都给出基于当前姿态的引导
        cur = self.read_pose()
        az_err, el_err = cur.angular_error(target)
        result["current_az"] = round(cur.azimuth, 1)
        result["current_el"] = round(cur.elevation, 1)
        result["az_error_deg"] = round(az_err, 1)
        result["el_error_deg"] = round(el_err, 1)
        result["aligned"] = cur.aligned(target)
        result["guidance"] = self._guidance_text(az_err, el_err)
        self.status.moving = not result["aligned"]
        return result

    def point_to_satellite(self, sat_az: float, sat_el: float,
                           beamwidth_deg: float = 10.0) -> Dict:
        """指向卫星，波束宽度决定对准容差。"""
        r = self.point_to(sat_az, sat_el)
        tol = max(2.0, beamwidth_deg / 2.0)
        cur = self.read_pose()
        r["aligned"] = cur.aligned(
            GimbalPose(azimuth=sat_az, elevation=sat_el),
            az_tolerance=tol, el_tolerance=tol,
        )
        r["beamwidth_deg"] = beamwidth_deg
        return r

    def stop(self) -> Dict:
        if self.status.mode == GimbalMode.ROTCTLD and self.rotctld:
            ok = self.rotctld.stop()
            return {"ok": ok, "backend": "rotctld"}
        if self.status.mode == GimbalMode.BOARD_PWM:
            r = self.board.set_position(-1, -1)  # 固件约定负值=停
            return {"ok": r.get("ok", False), "backend": "board_pwm"}
        return {"ok": True, "backend": "manual"}

    @staticmethod
    def _guidance_text(az_err: float, el_err: float) -> str:
        """生成人工转动引导（中文，罗盘方位）。"""
        dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]

        def compass(delta_az: float) -> str:
            if abs(delta_az) <= 3:
                return "方位已对准"
            turn = "顺时针（向右）" if delta_az > 0 else "逆时针（向左）"
            return f"方位{turn}转 {abs(delta_az):.0f}°"

        def tilt(delta_el: float) -> str:
            if abs(delta_el) <= 3:
                return "仰角已对准"
            d = "抬高" if delta_el > 0 else "压低"
            return f"仰角{d} {abs(delta_el):.0f}°"

        return f"{compass(az_err)}；{tilt(el_err)}"


# ========================================================================
# 卫星跟踪辅助
# ========================================================================

def az_el_to_rotator_angles(sat_az: float, sat_el: float,
                            mount_tilt_deg: float = 0.0) -> Tuple[float, float]:
    """
    把卫星方位角/仰角换算成旋转器角度。
    mount_tilt_deg：赤道仪式倾斜安装时的极轴角（0=水平方位-俯仰座架）。
    普通 AZ/EL 旋转器直接透传。
    """
    if mount_tilt_deg == 0.0:
        return sat_az % 360.0, clamp_angle(sat_el, 0, 90)
    # 倾斜座架坐标变换（简化，用于极轴式安装）
    tilt = math.radians(mount_tilt_deg)
    az = math.radians(sat_az)
    el = math.radians(sat_el)
    # 绕水平轴旋转极轴角（近似变换）
    new_el = math.asin(math.sin(el) * math.cos(tilt) +
                       math.cos(el) * math.sin(tilt) * math.cos(az))
    new_az_num = math.sin(az) * math.cos(el)
    new_az_den = math.cos(el) * math.cos(tilt) * math.cos(az) - math.sin(el) * math.sin(tilt)
    new_az = math.degrees(math.atan2(new_az_num, new_az_den)) % 360.0
    return new_az, clamp_angle(math.degrees(new_el), 0, 90)

# SPDX-License-Identifier: MIT
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
import time
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
    #: 连接≠启用：rotctld 运动下发是否已显式启用（engage_rotctld）
    engaged: bool = False


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
    """Hamlib rotctld 协议客户端（TCP，默认端口 4533）。

    协议（文本，每行一条；所有应答以 ``RPRT x`` 收尾，0=成功）：
      P <az> <el>     设置位置
      p               查询位置 → 返回 az\\nel\\n
      S               停止（手册 stop；旧类注释误写 ``\\chkstop``，已纠正）
      K               归位（park）
      1 / \\dump_caps  能力/限位查询 → key:value 多行 + RPRT 0

    安全闸（连接≠启用）：新建客户端默认 ``engaged=False``。``set_position``
    与 ``park`` 在未启用时直接拒绝（**零流量**，不发任何运动指令），须显式
    ``engage()`` 后才下发。``get_position`` / ``stop`` / ``ping`` / ``dump_caps``
    不受闸限——读姿态、停机、能力查询永远允许。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 4533, timeout: float = 2.0,
                 simulated: bool = False):
        self.host = host
        self.port = port
        self.timeout = timeout
        #: 对端是模拟服务器（scripts/hw_acceptance/rotctld_sim.py）时置 True。
        #: 协议层仍是 rotctld 读回，但语义是「模拟执行」，不能当机械精度。
        self.simulated = bool(simulated)
        #: 最近一次命令失败原因（超时/无应答/连接失败/未启用），供上层诚实上报
        self.last_error = ""
        #: 连接≠启用：默认不自动下发运动指令，须显式 engage()
        self.engaged = False
        #: 能力/限位查询状态：'ok'（解析成功）/ 'unknown'（未查或解析失败）
        self.caps_state = "unknown"
        #: 解析出的限位（caps_state=='ok' 时有效）：
        #: {min_az,max_az,min_el,max_el,can_park}；None=用默认钳位
        self.caps: Optional[Dict] = None
        #: 最近一次 set_position 经限位归一后实际下发的角度，供上层诚实上报
        self.last_clamped: Optional[Dict] = None

    def _command(self, cmd: str, n_lines: int = 1) -> List[str]:
        self.last_error = ""
        try:
            with socket.create_connection((self.host, self.port),
                                          timeout=self.timeout) as s:
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
                    self.last_error = f"超时（{self.timeout:.1f}s 未收齐应答）"
        except (socket.timeout, TimeoutError):
            self.last_error = f"连接/应答超时（{self.timeout:.1f}s）"
            return []
        except OSError as e:
            self.last_error = f"连接失败：{e}"
            return []
        lines = [line.strip() for line in
                 buf.decode("ascii", errors="replace").splitlines() if line.strip()]
        if not lines and not self.last_error:
            self.last_error = "无应答（连接被关闭或返回空）"
        return lines

    def _command_until_rprt(self, cmd: str) -> List[str]:
        """发命令并读到 ``RPRT x`` 收尾行（用于 \\dump_caps 这类不定长应答）。"""
        self.last_error = ""
        try:
            with socket.create_connection((self.host, self.port),
                                          timeout=self.timeout) as s:
                s.sendall((cmd + "\n").encode("ascii"))
                buf = b""
                s.settimeout(self.timeout)
                try:
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                        decoded = buf.decode("ascii", errors="replace")
                        if any(ln.strip().startswith("RPRT")
                               for ln in decoded.splitlines()):
                            break
                except socket.timeout:
                    self.last_error = f"超时（{self.timeout:.1f}s 未收齐应答）"
        except (socket.timeout, TimeoutError):
            self.last_error = f"连接/应答超时（{self.timeout:.1f}s）"
            return []
        except OSError as e:
            self.last_error = f"连接失败：{e}"
            return []
        lines = [ln.strip() for ln in
                 buf.decode("ascii", errors="replace").splitlines() if ln.strip()]
        if not lines and not self.last_error:
            self.last_error = "无应答（连接被关闭或返回空）"
        return lines

    @staticmethod
    def _reply_ok(lines: List[str]) -> bool:
        """严格解析 rotctld 应答：仅 'RPRT 0' 为成功。

        旧实现 any("0" in ln) 会把 "RPRT 20"/"RPRT 10" 等错误码也判成成功
        （含字符 "0"），属假成功；必须按协议解析错误码。
        """
        for ln in lines:
            if ln.startswith("RPRT"):
                try:
                    return int(ln.split()[1]) == 0
                except (IndexError, ValueError):
                    return False
        return False

    # ---- 启用闸 ----
    def engage(self) -> bool:
        """显式启用运动下发（连接≠启用）。返回启用状态。"""
        self.engaged = True
        return self.engaged

    def disengage(self) -> bool:
        """停用运动下发；之后 set_position/park 拒绝（零流量）。"""
        self.engaged = False
        return self.engaged

    # ---- 能力/限位查询 ----
    def dump_caps(self) -> bool:
        """查询旋转器能力/限位（``\\dump_caps``，手册别名 ``1``）。

        成功（RPRT 0 且四限位标签齐全且合法）→ caps_state='ok'、caps 填实；
        任何失败/缺字段/非法值 → caps_state='unknown'、caps=None、last_error
        记原因，**保持默认钳位**（不静默成功）。返回是否成功解析。
        """
        lines = self._command_until_rprt("\\dump_caps")
        if not self._reply_ok(lines):
            self.caps_state = "unknown"
            self.caps = None
            if not self.last_error:
                self.last_error = "dump_caps 未返回 RPRT 0"
            return False
        vals: Dict[str, str] = {}
        for ln in lines:
            if ":" not in ln:
                continue
            key, _, val = ln.partition(":")
            vals[key.strip()] = val.strip()
        try:
            min_az = float(vals["Min Azimuth"])
            max_az = float(vals["Max Azimuth"])
            min_el = float(vals["Min Elevation"])
            max_el = float(vals["Max Elevation"])
        except (KeyError, ValueError):
            self.caps_state = "unknown"
            self.caps = None
            self.last_error = "dump_caps 缺少限位字段或值非数字"
            return False
        if not all(math.isfinite(v) for v in (min_az, max_az, min_el, max_el)):
            self.caps_state = "unknown"
            self.caps = None
            self.last_error = "dump_caps 限位含非有限值"
            return False
        if not (min_az < max_az and min_el < max_el):
            self.caps_state = "unknown"
            self.caps = None
            self.last_error = "dump_caps 限位 min>=max"
            return False
        if (max_az - min_az) > 720.0 or not (-90.0 <= min_el <= max_el <= 90.0):
            self.caps_state = "unknown"
            self.caps = None
            self.last_error = "dump_caps 限位超出物理范围"
            return False
        can_park: Optional[bool] = None
        if "Can Park" in vals:
            cp = vals["Can Park"].upper()
            can_park = True if cp == "Y" else (False if cp == "N" else None)
        self.caps = {"min_az": min_az, "max_az": max_az,
                     "min_el": min_el, "max_el": max_el, "can_park": can_park}
        self.caps_state = "ok"
        self.last_error = ""
        return True

    @property
    def can_park(self) -> Optional[bool]:
        """旋转器是否支持归位（K）：caps 解析成功返回 Y/N 布尔，未知返回 None。

        None ≠ False：未查/解析失败时「不知道」支不支持，既不能当成不支持
        而拒绝归位，也不能当成支持而盲目下发——park() 只在明确 False 时拒绝。
        """
        if self.caps_state == "ok" and self.caps:
            return self.caps.get("can_park")
        return None

    # ---- 限位归一 ----
    def normalize(self, az: float, el: float) -> Tuple[float, float, Dict]:
        """按真实限位（caps_state=='ok'）或默认范围归一化目标角。

        返回 ``(az, el, clamped)``。clamped 记录被钳位的轴（请求/实际/限位）。
        - 方位跨度≥360（多圈旋转器）：``[min_az, min_az+360)`` 内原样保留
          （不强行 %360，否则多圈位置被抹掉）；范围外才环形归一。
        - 方位跨度<360（如 0..180）：直接钳位到 ``[min_az,max_az]``，**不做
          环形归一**（否则 270 被绕成 90，而真实限位只到 180）。
        - 仰角：钳位到 ``[min_el,max_el]``（默认 [0,90]）。
        """
        az = float(az)
        el = float(el)
        req_az, req_el = az, el
        clamped: Dict = {}
        if self.caps_state == "ok" and self.caps:
            min_az = self.caps["min_az"]
            max_az = self.caps["max_az"]
            min_el = self.caps["min_el"]
            max_el = self.caps["max_el"]
        else:
            min_az, max_az, min_el, max_el = 0.0, 360.0, 0.0, 90.0
        span = max_az - min_az
        if span >= 360.0:
            if az < min_az or az >= min_az + 360.0:
                az = min_az + (az - min_az) % 360.0
        else:
            az = max(min_az, min(max_az, az))
        el = max(min_el, min(max_el, el))
        if abs(az - req_az) > 1e-6:
            clamped["azimuth"] = {"requested": req_az, "applied": az,
                                  "limits": [min_az, max_az]}
        if abs(el - req_el) > 1e-6:
            clamped["elevation"] = {"requested": req_el, "applied": el,
                                    "limits": [min_el, max_el]}
        return az, el, clamped

    # ---- 运动 ----
    def get_position(self) -> GimbalPose:
        lines = self._command("p", n_lines=2)
        az = float(lines[0])
        el = float(lines[1])
        return GimbalPose(azimuth=az % 360.0, elevation=clamp_angle(el, -90, 90))

    def set_position(self, az: float, el: float) -> bool:
        if not self.engaged:
            self.last_error = "未启用（连接≠启用：先 engage() 再下发运动指令）"
            self.last_clamped = None
            return False
        naz, nel, clamped = self.normalize(az, el)
        self.last_clamped = {"az": naz, "el": nel, "clamped": clamped or None}
        lines = self._command(f"P {naz:.2f} {nel:.2f}", n_lines=1)
        # rotctld 成功返回 RPRT 0
        return self._reply_ok(lines)

    def stop(self) -> bool:
        lines = self._command("S", n_lines=1)
        return self._reply_ok(lines)

    def park(self) -> bool:
        """归位（K）。未启用拒绝；can_park 明确为 False 时拒绝（零流量）。"""
        if not self.engaged:
            self.last_error = "未启用（连接≠启用：先 engage() 再归位）"
            return False
        if (self.caps_state == "ok" and self.caps
                and self.caps.get("can_park") is False):
            self.last_error = "旋转器不支持归位（Can Park: N）"
            return False
        lines = self._command("K", n_lines=1)
        return self._reply_ok(lines)

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
        # 固件约定负值=停：负值原样透传，不能做 %360/clamp
        # （否则 -1/-1 被归一化成 az=359,el=0，停机指令变移动指令）
        if az < 0 or el < 0:
            return self._send("gimbal_set", {"az": float(az), "el": float(el)})
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

def _park_on_stop_from_config() -> bool:
    """从 ai_config 读 gimbal_park_on_stop（严格 bool；缺失/坏值/异常→False）。

    严格 ``is True``：字符串 "true"、整数 1 都不算开启（防配置脏值误归位）。
    读盘/解析任何异常一律回落 False（停后归位是可选动作，失败不该炸停机）。
    """
    try:
        from mbdsdr_ai.config import ConfigStore
        val = ConfigStore().read().get("gimbal_park_on_stop", False)
        return val is True
    except Exception:
        return False


class GimbalController:
    """统一云台控制器，后端可插拔。"""

    def __init__(self):
        self.status = GimbalStatus()
        self.rotctld: Optional[RotctldClient] = None
        self.board = BoardGimbalClient()
        # 姿态回读源（由 pose.py 的 IMU+磁力计融合结果注入）
        self._pose_provider: Optional[Callable[[], GimbalPose]] = None
        # RSSI 回读源（SDR 后端注入，返回当前信号强度读数，单位随后端：
        # IQ 链为 dBFS，ai-sdr mini 为 dBµV；不是 dBm）
        self._rssi_provider: Optional[Callable[[], float]] = None
        # 是否拿到过真实姿态回读；没有则 (0,0) 只是占位，不能当"已对准"依据
        self._pose_valid = False
        #: 本次姿态从哪来：imu / rotctld / ""（无回读）。上层据此区分
        #: 「真实回读」与「假后端的指令回声」，禁止把两者混成一个误差字段。
        self._pose_source = ""

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

    def use_rotctld(self, host: str = "127.0.0.1", port: int = 4533,
                    simulated: bool = False) -> GimbalStatus:
        self.rotctld = RotctldClient(host, port, simulated=simulated)
        self.status.mode = GimbalMode.ROTCTLD
        self.status.connected = self.rotctld.ping()
        self.status.engaged = False  # 连接≠启用：连上也不自动下发
        # 连接成功后尽力查一次限位（失败标 unknown，不静默、不阻断连接）
        if self.status.connected:
            self.rotctld.dump_caps()
        self.status.backend_info = (
            f"Hamlib rotctld {host}:{port}"
            + ("（模拟服务器：指令链模型，非真机）" if simulated else ""))
        return self.status

    def engage_rotctld(self) -> bool:
        """显式启用 rotctld 运动下发（须先连接）。返回是否已启用。"""
        if self.status.mode != GimbalMode.ROTCTLD or not self.rotctld:
            return False
        if not self.status.connected:
            return False
        self.rotctld.engage()
        self.status.engaged = True
        return True

    def disengage_rotctld(self) -> bool:
        """停用 rotctld 运动下发；之后 point_to/park 拒绝下发。"""
        if self.rotctld:
            self.rotctld.disengage()
        self.status.engaged = False
        return True

    def park(self) -> Dict:
        """手动归位（仅 rotctld 支持；其余后端诚实报不支持）。"""
        if self.status.mode == GimbalMode.ROTCTLD and self.rotctld:
            ok = self.rotctld.park()
            return {"ok": ok, "backend": "rotctld",
                    "error": "" if ok else (self.rotctld.last_error or "归位失败")}
        return {"ok": False, "backend": self.status.mode.value,
                "error": "当前后端不支持归位（仅 rotctld）"}

    def set_pose_provider(self, provider: Callable[[], GimbalPose]):
        """注入 IMU+磁力计姿态回读函数。"""
        self._pose_provider = provider

    def set_rssi_provider(self, provider: Callable[[], float]):
        """注入 RSSI 回读函数（返回当前信号强度读数，单位随后端，不是 dBm）。"""
        self._rssi_provider = provider

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
                self._pose_valid = True
                self._pose_source = "imu"
                return pose
            except Exception:
                pass
        if self.status.mode == GimbalMode.ROTCTLD and self.rotctld:
            try:
                pose = self.rotctld.get_position()
                self.status.current = pose
                self._pose_valid = True
                # 模拟服务器：协议上是 rotctld 读回，但来源必须标 simulated，
                # 否则会被误当机械精度（见 limitations-register L43）。
                self._pose_source = ("simulated" if self.rotctld.simulated
                                     else "rotctld")
                return pose
            except Exception:
                pass
        # 本次读姿态失败 → 旧值可能已过期，必须撤销 pose_valid，
        # 否则上层会拿过期的"已对准"当真（假成功）。
        self._pose_valid = False
        self._pose_source = ""
        return self.status.current or GimbalPose()

    # ---- 运动控制 ----
    def point_to(self, az: float, el: float) -> Dict:
        """指向目标方位角/仰角。返回执行结果与人工引导信息。

        越界处理是**显式**的（不静默）：rotctld 按真实限位（dump_caps）归一，
        其余后端仰角夹到 0–90°、方位环形归一到 0–360°，都写进 ``clamped`` 与
        ``note``，调用方必须把 note 呈现给用户，否则用户以为请求原样执行了。
        """
        raw_az = float(az)
        raw_el = float(el)
        clamped: Dict[str, Dict] = {}
        notes: List[str] = []
        if self.status.mode == GimbalMode.ROTCTLD and self.rotctld:
            naz, nel, clamped = self.rotctld.normalize(raw_az, raw_el)
            target = GimbalPose(azimuth=naz, elevation=nel)
            if "azimuth" in clamped:
                notes.append(f"方位 {raw_az:.1f}° 超出旋转器限位，已夹到 {naz:.1f}°")
            if "elevation" in clamped:
                notes.append(f"仰角 {raw_el:.1f}° 超出旋转器限位，已夹到 {nel:.1f}°")
        else:
            target = GimbalPose(azimuth=raw_az % 360.0,
                                elevation=clamp_angle(raw_el, 0, 90))
            if abs(raw_el - target.elevation) > 1e-6:
                clamped["elevation"] = {"requested": raw_el,
                                        "applied": target.elevation,
                                        "limits": [0.0, 90.0]}
                notes.append(f"仰角 {raw_el:.1f}° 超出机械范围 0–90°，"
                             f"已夹到 {target.elevation:.1f}°")
            if raw_az < 0.0 or raw_az >= 360.0:
                clamped["azimuth"] = {"requested": raw_az,
                                      "applied": target.azimuth,
                                      "limits": "0–360（环形归一）"}
                notes.append(f"方位 {raw_az:.1f}° 已归一到 {target.azimuth:.1f}°")
        self.status.target = target
        result: Dict = {"target_az": target.azimuth, "target_el": target.elevation,
                        "requested_az": raw_az, "requested_el": raw_el,
                        "clamped": clamped or None,
                        "note": "；".join(notes),
                        "command_mode": "absolute_az_el"}

        if self.status.mode == GimbalMode.BOARD_PWM:
            r = self.board.set_position(target.azimuth, target.elevation)
            result["dispatched"] = r.get("ok", False)
            result["backend"] = "board_pwm"
            if not result["dispatched"]:
                result["error"] = r.get("error", "板载通道未确认执行")

        elif self.status.mode == GimbalMode.ROTCTLD and self.rotctld:
            ok = self.rotctld.set_position(target.azimuth, target.elevation)
            result["dispatched"] = ok
            result["backend"] = "rotctld"
            if not ok:
                result["error"] = (self.rotctld.last_error
                                   or "rotctld 未返回 RPRT 0")

        elif self.status.mode == GimbalMode.MANUAL:
            result["dispatched"] = False
            result["backend"] = "manual"
            result["error"] = "manual 模式不自动下发指令"

        else:
            result["dispatched"] = False
            result["backend"] = "offline"
            result["error"] = "云台后端离线"

        # 无论哪种模式，都给出基于当前姿态的引导
        cur = self.read_pose()
        az_err, el_err = cur.angular_error(target)
        result["current_az"] = round(cur.azimuth, 1)
        result["current_el"] = round(cur.elevation, 1)
        result["az_error_deg"] = round(az_err, 1)
        result["el_error_deg"] = round(el_err, 1)
        # 没有真实姿态回读时 (0,0) 只是占位：绝不能报"已对准"（假成功）
        result["pose_valid"] = self._pose_valid
        # 姿态来源随结果返回：imu / rotctld = 真实回读；"" = 无回读。
        result["pose_source"] = self._pose_source if self._pose_valid else ""
        result["aligned"] = bool(cur.aligned(target)) if self._pose_valid else False
        if self._pose_valid:
            result["guidance"] = self._guidance_text(az_err, el_err)
        else:
            result["guidance"] = "无姿态回读（IMU/旋转器均未提供），无法判定对准与否"
        self.status.moving = not result["aligned"]
        return result

    def point_to_satellite(self, sat_az: float, sat_el: float,
                           beamwidth_deg: float = 10.0) -> Dict:
        """指向卫星，波束宽度决定对准容差。"""
        r = self.point_to(sat_az, sat_el)
        tol = max(2.0, beamwidth_deg / 2.0)
        cur = self.read_pose()
        if r.get("pose_valid"):
            r["aligned"] = cur.aligned(
                GimbalPose(azimuth=sat_az, elevation=sat_el),
                az_tolerance=tol, el_tolerance=tol,
            )
        # 无姿态回读时保持 aligned=False（point_to 已置），不得用零位姿假判对准
        r["beamwidth_deg"] = beamwidth_deg
        return r

    def sweep_azimuth(self, az_start: float = 0.0, az_end: float = 360.0,
                      step: float = 30.0, elevation: float = 20.0,
                      settle_sec: float = 0.8,
                      rssi_provider: Optional[Callable[[], float]] = None) -> Dict:
        """
        方位角步进测向扫描：八木/定向天线在云台上逐方位转动，每步读 RSSI。

        - board_pwm / rotctld：自动转动、等待 settle、读 RSSI。
        - manual：不自动转，返回每步人工引导清单（RSSI 留空，由人/AI 补采）。

        返回 {samples:[{az,el,rssi}], peak_az, manual, guidance_steps}。
        RSSI 为相对读数（dB，单位随后端：dBFS 或 dBµV），值越大信号越强。
        它不是 dBm：没有接收链/天线增益校准就不得标 dBm。
        """
        provider = rssi_provider or self._rssi_provider
        if not (step > 0):
            # step<=0 会让 while 死循环（0=不动、负=反向永远到不了终点）
            raise ValueError(f"step 必须为正数，实得 {step}")
        angles: List[float] = []
        a = az_start
        while a < az_end - 1e-6:
            angles.append(round(a, 1))
            a += step

        samples: List[Dict] = []
        manual = self.status.mode == GimbalMode.MANUAL

        for az in angles:
            entry: Dict = {"az": az, "el": elevation}
            if manual:
                # 人工模式：给出转到该方位的引导，不读 RSSI
                tgt = GimbalPose(azimuth=az, elevation=elevation)
                cur = self.read_pose()
                az_err, el_err = cur.angular_error(tgt)
                entry["rssi"] = None
                entry["guidance"] = self._guidance_text(az_err, el_err)
            else:
                self.point_to(az, elevation)
                time.sleep(max(0.0, settle_sec))
                rssi = None
                if provider is not None:
                    try:
                        rssi = float(provider())
                    except Exception:
                        rssi = None
                entry["rssi"] = round(rssi, 1) if rssi is not None else None
            samples.append(entry)

        valid = [(s["az"], s["rssi"]) for s in samples if s["rssi"] is not None]
        peak_az = max(valid, key=lambda x: x[1])[0] if valid else None

        return {
            "samples": samples,
            "peak_az": peak_az,
            "manual": manual,
            "step": step,
            "elevation": elevation,
            "note": ("人工模式：按 guidance 逐方位转动八木，读 RSSI 后回填，"
                     "再用 gnss_direction_find 质心定位" if manual
                     else "自动扫描完成，peak_az 为最强信号方位；"
                          "精细定位请把 samples 交给 gnss_direction_find"),
        }

    def stop(self, park_on_stop: Optional[bool] = None) -> Dict:
        if self.status.mode == GimbalMode.ROTCTLD and self.rotctld:
            ok = self.rotctld.stop()
            result: Dict = {"ok": ok, "backend": "rotctld"}
            # None=读配置；True=停后归位；False=不归位
            if park_on_stop is None:
                park_on_stop = _park_on_stop_from_config()
            if park_on_stop:
                parked = self.rotctld.park()
                result["parked"] = parked
                if not parked:
                    result["park_error"] = self.rotctld.last_error or "归位失败"
            return result
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

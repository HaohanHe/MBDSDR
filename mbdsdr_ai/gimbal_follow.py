# SPDX-License-Identifier: MIT
"""
MBDSDR - 云台跟随的速率整形（纯逻辑，无 Qt / 无硬件依赖）
==============================================================

把「目标卫星 az/el → 云台指令」之间的整形逻辑独立出来，便于用纯函数
单测锁定行为（不依赖渲染或真实硬件）：

  - 死区（deadband）：目标变化小于阈值时不再下发，避免原地抖动指令风暴；
  - 最小间隔（min_interval）：两拍之间太近不下发，500ms 跟踪环也压不爆；
  - 速率上限（max_rate）：单拍指令增量不超过 ``max_rate_deg_s * dt``，
    大角度跳变被拆成多拍平滑跟踪；
  - 复位/目标丢失：调用方 ``reset()`` 后下一次一定是完整指向（不做插值）。

这是整形器，不做「能不能跟随」的判断——硬件可用性与目标有效性由调用方
（主窗口）负责，缺失时诚实解锁并提示。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def angular_separation_deg(az1: float, el1: float,
                           az2: float, el2: float) -> float:
    """两天球方向的球面大圆角距（度），自动处理 359°→1° 环绕。

    用球面余弦定理而不是简单的 az/el 差：高仰角时 az 差 1° 的实际角距
    远小于 1°，按分量差会系统性高估误差。
    """
    p1 = math.radians(float(el1))
    p2 = math.radians(float(el2))
    da = math.radians(float(az2) - float(az1))
    c = (math.sin(p1) * math.sin(p2)
         + math.cos(p1) * math.cos(p2) * math.cos(da))
    c = max(-1.0, min(1.0, c))
    return math.degrees(math.acos(c))


def _wrap_deg(delta: float) -> float:
    """把角度差归一化到 (-180, 180]，避免 359° → 1° 被当成 -358° 跳变。"""
    while delta > 180.0:
        delta -= 360.0
    while delta <= -180.0:
        delta += 360.0
    return delta


@dataclass
class GimbalFollowLimiter:
    """把卫星 az/el 整形为稀疏、限速的云台指令。"""

    min_interval_s: float = 1.0
    deadband_deg: float = 2.0
    max_rate_deg_s: float = 20.0
    _last_cmd: Optional[Tuple[float, float]] = field(default=None, init=False)
    _last_time: float = field(default=0.0, init=False)
    _dispatched: int = field(default=0, init=False)

    def reset(self) -> None:
        self._last_cmd = None
        self._last_time = 0.0
        self._dispatched = 0

    @property
    def dispatched(self) -> int:
        return self._dispatched

    @property
    def last_command(self) -> Optional[Tuple[float, float]]:
        """最近一次**已下发**的绝对角；没有则 None。"""
        return self._last_cmd

    def plan(self, azimuth_deg: float, elevation_deg: float,
             now: Optional[float] = None) -> Optional[Tuple[float, float]]:
        """返回本拍应下发的 (az, el)；None 表示本拍不下发（避免指令风暴）。

        第一次调用给完整目标（建立基线），之后按间隔/死区/速率上限整形。
        """
        try:
            az = float(azimuth_deg)
            el = float(elevation_deg)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(az) and math.isfinite(el)):
            return None
        t = time.monotonic() if now is None else float(now)

        if self._last_cmd is None or self._last_time <= 0.0:
            self._last_cmd = (az, el)
            self._last_time = t
            self._dispatched += 1
            return az, el

        dt = t - self._last_time
        if dt < self.min_interval_s:
            return None

        last_az, last_el = self._last_cmd
        d_az = _wrap_deg(az - last_az)
        d_el = el - last_el
        if max(abs(d_az), abs(d_el)) < self.deadband_deg:
            # 已在目标附近：不下发（做完即停），只推进时间基线
            self._last_time = t
            return None

        max_step = max(0.01, self.max_rate_deg_s * dt)
        step_az = max(-max_step, min(max_step, d_az))
        step_el = max(-max_step, min(max_step, d_el))
        cmd_az = (last_az + step_az) % 360.0
        cmd_el = max(-90.0, min(90.0, last_el + step_el))
        self._last_cmd = (cmd_az, cmd_el)
        self._last_time = t
        self._dispatched += 1
        return cmd_az, cmd_el


@dataclass
class PointingErrorRecorder:
    """过境全过程的指向误差记录（指令角 vs 卫星真角，可选姿态回读角）。

    - ``cmd_*``：云台**指令角**与卫星真角的球面角距。整形器限速/死区时
      指令会滞后，这个值就是可解释的跟踪滞后误差。
    - ``pose_*``：云台**实际指向**回读角与真角的角距；后端不提供姿态时为 None。
    """

    max_samples: int = 5000
    _cmd_err: List[float] = field(default_factory=list, init=False)
    _pose_err: List[float] = field(default_factory=list, init=False)
    _elapsed_s: float = field(default=0.0, init=False)

    def reset(self) -> None:
        self._cmd_err.clear()
        self._pose_err.clear()
        self._elapsed_s = 0.0

    def record(self, truth_az: float, truth_el: float,
               cmd_az: float, cmd_el: float,
               pose_az: Optional[float] = None,
               pose_el: Optional[float] = None,
               elapsed_s: float = 0.0) -> Dict[str, float]:
        cmd_err = angular_separation_deg(truth_az, truth_el, cmd_az, cmd_el)
        if len(self._cmd_err) >= self.max_samples:
            self._cmd_err.pop(0)
            if self._pose_err:
                self._pose_err.pop(0)
        self._cmd_err.append(cmd_err)
        pose_err: Optional[float] = None
        if pose_az is not None and pose_el is not None:
            pose_err = angular_separation_deg(
                truth_az, truth_el, float(pose_az), float(pose_el))
            self._pose_err.append(pose_err)
        else:
            self._pose_err.append(float("nan"))
        try:
            self._elapsed_s += max(0.0, float(elapsed_s))
        except (TypeError, ValueError):
            pass
        return {"cmd_error_deg": cmd_err, "pose_error_deg": pose_err}

    def stats(self) -> Dict[str, Optional[float]]:
        n = len(self._cmd_err)
        if not n:
            return {"samples": 0, "cmd_max_deg": None, "cmd_mean_deg": None,
                    "cmd_rms_deg": None, "pose_max_deg": None,
                    "pose_mean_deg": None, "pose_samples": 0,
                    "last_cmd_deg": None, "last_pose_deg": None,
                    "elapsed_total_s": 0.0}
        cmds = self._cmd_err
        poses = [x for x in self._pose_err if not math.isnan(x)]
        cmd_max = max(cmds)
        cmd_mean = sum(cmds) / n
        cmd_rms = math.sqrt(sum(x * x for x in cmds) / n)
        return {
            "samples": n,
            "cmd_max_deg": cmd_max,
            "cmd_mean_deg": cmd_mean,
            "cmd_rms_deg": cmd_rms,
            "pose_max_deg": (max(poses) if poses else None),
            "pose_mean_deg": (sum(poses) / len(poses) if poses else None),
            "pose_samples": len(poses),
            "last_cmd_deg": cmds[-1],
            "last_pose_deg": (poses[-1] if poses else None),
            "elapsed_total_s": self._elapsed_s,
        }

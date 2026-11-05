#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""可注入的 rotctld 模拟服务器（Hamlib 文本协议子集）+ 模拟过境驱动。

用途
----
在没有真旋转器/真云台时，用本机 TCP 端口跑一条**可复现**的过境，量出
「指令链 + 量化 + 延迟」的误差，而不是拿真机数字冒充。

能模拟什么（**只**这些，多的一点也不装）：
  * ``latency_s``   指令到达后多久电机才开始动（延迟档 50/200/500ms）；
  * ``quantize_deg`` 回读/执行角度的量化步进（旋转器编码器典型 1°/0.1°）；
  * ``jitter_deg``  每次回读的随机噪声（固定 seed，可复现）；
  * ``drop_every``  每第 N 条运动指令丢弃一次（返回 ``RPRT 20`` 或直接关连接）；
  * ``lie_deg``     回读系统性偏移（模拟编码器谎报/装反）。

**模拟 ≠ 真机（口径边界，不得越过）**：
本服务器是「理想电机 + 传输延迟 + 量化」，**不含**机械齿隙、风力负载、
电机加减速曲线、结构形变、磁力计/IMU 噪声。因此它给出的数字只能证明
指令链与量化误差，**不能**证明机械跟随精度：
``mechanical_accuracy_claimable`` 在模拟后端下**永远是 False**——
控制器把模拟后端回读标成 ``pose_source="simulated"``，误差进
``pose_error_by_source.simulated`` 桶，不进 ``pose_*``。
"""
from __future__ import annotations

import math
import random
import socket
import threading
import time
from typing import Any, Dict, List, Optional


class FakeClock:
    """可推进的单调时钟（测试/模拟过境用；默认时间线由调用方掌控）。"""

    def __init__(self, start: float = 1000.0):
        self._t = float(start)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._t

    def advance(self, dt_s: float) -> float:
        with self._lock:
            self._t += max(0.0, float(dt_s))
            return self._t


def _wrap180(delta: float) -> float:
    while delta > 180.0:
        delta -= 360.0
    while delta <= -180.0:
        delta += 360.0
    return delta


class SimRotctldServer:
    """rotctld 协议子集服务器：``P az el`` / ``p`` / ``S``。

    每条指令一个 TCP 连接（与 ``RotctldClient`` 的实现一致），
    后台单线程顺序处理，避免并发改写姿态造成不可复现。
    """

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0,
                 latency_s: float = 0.0, quantize_deg: float = 0.1,
                 jitter_deg: float = 0.0, rate_deg_s: float = 90.0,
                 drop_every: int = 0, drop_mode: str = "rprt",
                 lie_deg: float = 0.0, seed: int = 1,
                 initial_az: float = 0.0, initial_el: float = 0.0,
                 clock=None):
        self.latency_s = max(0.0, float(latency_s))
        self.quantize_deg = max(0.0, float(quantize_deg))
        self.jitter_deg = max(0.0, float(jitter_deg))
        self.rate_deg_s = max(1e-3, float(rate_deg_s))
        self.drop_every = int(drop_every or 0)
        self.drop_mode = drop_mode
        self.lie_deg = float(lie_deg)
        self._clock = clock or time.monotonic
        self._rng = random.Random(seed)

        self._lock = threading.Lock()
        self._az = float(initial_az) % 360.0
        self._el = float(initial_el)
        self._target_az = self._az
        self._target_el = self._el
        self._effective_at = 0.0
        self._last_update = self._clock()

        #: 可观测的执行台账：每条运动指令一条记录（含丢弃）
        self.commands: List[Dict[str, Any]] = []
        self.counters = {"P": 0, "p": 0, "S": 0, "dropped": 0}

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, int(port)))
        self._sock.listen(8)
        self._sock.settimeout(0.1)
        self.port = int(self._sock.getsockname()[1])
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- 生命周期 ----
    def start(self) -> "SimRotctldServer":
        self._thread = threading.Thread(target=self._serve, name="rotctld-sim",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass  # 关闭监听口失败不影响已完成的统计
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()

    # ---- 协议 ----
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # 监听口已关闭 = 正常收尾
            try:
                with conn:
                    conn.settimeout(1.0)
                    try:
                        raw = conn.recv(256)
                    except (socket.timeout, OSError):
                        continue
                    line = raw.decode("ascii", errors="replace").strip()
                    reply = self._handle(line)
                    if reply is None:
                        continue  # 丢弃模式：不回包，直接关连接
                    try:
                        conn.sendall((reply + "\n").encode("ascii"))
                    except OSError:
                        continue  # 客户端提前断开：本条指令已处理完
            except OSError:
                continue

    def _handle(self, line: str) -> Optional[str]:
        if line == "p":
            with self._lock:
                self.counters["p"] += 1
                self._advance()
                az, el = self._report()
            return f"{az:.2f}\n{el:.2f}"
        if line.startswith("P"):
            parts = line.split()
            try:
                az = float(parts[1]) % 360.0
                el = max(-90.0, min(90.0, float(parts[2])))
            except (IndexError, ValueError):
                return "RPRT -1"
            with self._lock:
                self.counters["P"] += 1
                # 先按**旧目标**把到本指令为止的运动结算掉，再换目标；
                # 否则新指令的生效时刻会追溯抹掉上一段本该走过的行程。
                self._advance()
                idx = self.counters["P"]
                drop = bool(self.drop_every) and idx % self.drop_every == 0
                self.commands.append({"t": self._clock(), "az": az, "el": el,
                                      "dropped": drop})
                if drop:
                    self.counters["dropped"] += 1
                    return None if self.drop_mode == "close" else "RPRT 20"
                self._target_az = az
                self._target_el = el
                self._effective_at = self._clock() + self.latency_s
            return "RPRT 0"
        if line == "S":
            with self._lock:
                self.counters["S"] += 1
                self._advance()
                self._target_az = self._az
                self._target_el = self._el
            return "RPRT 0"
        return "RPRT -1"  # 未实现的指令：明确报错码，不假装成功

    # ---- 电机模型 ----
    def _advance(self) -> None:
        now = self._clock()
        start = max(self._last_update, self._effective_at)
        dt = now - start
        self._last_update = now
        if dt <= 0.0:
            return  # 延迟窗口内电机不动
        step = self.rate_deg_s * dt
        d_az = _wrap180(self._target_az - self._az)
        d_el = self._target_el - self._el
        dist = math.hypot(d_az, d_el)
        if dist <= step or dist <= 1e-9:
            self._az, self._el = self._target_az, self._target_el
            return
        self._az = (self._az + d_az / dist * step) % 360.0
        self._el = self._el + d_el / dist * step

    def _report(self) -> tuple[float, float]:
        az = self._az + self.lie_deg
        el = self._el
        if self.jitter_deg:
            az += self._rng.gauss(0.0, self.jitter_deg / 3.0)
            el += self._rng.gauss(0.0, self.jitter_deg / 3.0)
        if self.quantize_deg:
            q = self.quantize_deg
            az = round(az / q) * q
            el = round(el / q) * q
        return az % 360.0, max(-90.0, min(90.0, el))


# ---------------------------------------------------------------------------
# 模拟过境：驱动 → 指令 → 执行 → 回读 → 误差
# ---------------------------------------------------------------------------
def run_simulated_pass(*, latency_s: float = 0.0, steps: int = 24,
                       dt_s: float = 1.0, sample_dt_s: float = 0.25,
                       sat_rate_deg_s: float = 2.0,
                       start_az: float = 60.0, elevation: float = 35.0,
                       rate_deg_s: float = 5.0,
                       quantize_deg: float = 0.1, jitter_deg: float = 0.0,
                       drop_every: int = 0, drop_mode: str = "rprt",
                       lie_deg: float = 0.0, seed: int = 1,
                       clock=None) -> Dict[str, Any]:
    """跑一条合成过境，返回**按来源分栏**的误差与执行台账。

    时间线由 ``clock``（默认 ``FakeClock``）掌控：每 ``dt_s`` 下一次指令，
    指令后 ``sample_dt_s`` 采样回读。采样点必须落在「延迟窗口未过 / 电机
    还在追」的区间内，延迟才会真实体现成滞后角（``lag_cmd_vs_pose``）；
    采样放到 ``dt_s`` 末尾，慢速电机早就追平，三档延迟会全部读成 0。

    ``rate_deg_s`` 是模拟电机的转动速率（模型参数，**不是真机实测**）。
    """
    from mbdsdr_ai.gimbal import GimbalController
    from mbdsdr_ai.gimbal_follow import (GimbalFollowLimiter,
                                         PointingErrorRecorder,
                                         angular_separation_deg)

    clk = clock or FakeClock()
    srv = SimRotctldServer(latency_s=latency_s, quantize_deg=quantize_deg,
                           jitter_deg=jitter_deg, drop_every=drop_every,
                           drop_mode=drop_mode, lie_deg=lie_deg, seed=seed,
                           rate_deg_s=rate_deg_s,
                           initial_az=start_az, initial_el=elevation,
                           clock=clk).start()
    try:
        gc = GimbalController()
        st = gc.use_rotctld("127.0.0.1", srv.port, simulated=True)
        if not st.connected:
            raise RuntimeError("模拟服务器不可达（ping 失败）")
        limiter = GimbalFollowLimiter()
        rec = PointingErrorRecorder()
        lags: List[float] = []
        stopped = False
        error = ""
        for i in range(int(steps)):
            cmd_az = (start_az + sat_rate_deg_s * dt_s * i) % 360.0
            cmd = limiter.plan(cmd_az, elevation, now=clk())
            if cmd is None:
                continue
            res = gc.point_to_satellite(cmd[0], cmd[1])
            if not bool(res.get("dispatched", False)):
                # 丢弃/拒绝指令：走与主窗口一致的停止语义，不继续下发
                stopped = True
                error = str(res.get("error") or "无应答/超时")
                break
            clk.advance(sample_dt_s)
            pose = gc.read_pose()
            lags.append(angular_separation_deg(cmd[0], cmd[1],
                                               pose.azimuth, pose.elevation))
            # 采样时刻的真角（含采样间隔内的目标前进量，不另算一条时间线）
            truth_az = (start_az + sat_rate_deg_s * (dt_s * i + sample_dt_s)) % 360.0
            rec.record(truth_az, elevation, cmd[0], cmd[1],
                       pose_az=pose.azimuth, pose_el=pose.elevation,
                       pose_source="simulated", elapsed_s=dt_s)
            clk.advance(max(0.0, dt_s - sample_dt_s))
        stats = rec.stats()
        by_src = stats.get("pose_error_by_source") or {}
        readback_n = int((by_src.get("readback") or {}).get("samples", 0))
        sim_n = int((by_src.get("simulated") or {}).get("samples", 0))
        lag_row = ({"max_deg": max(lags),
                    "mean_deg": sum(lags) / len(lags),
                    "rms_deg": math.sqrt(sum(x * x for x in lags) / len(lags))}
                   if lags else {"max_deg": None, "mean_deg": None, "rms_deg": None})
        return {
            "backend": "rotctld_sim",
            "latency_s": float(latency_s),
            "steps": int(steps),
            "dt_s": float(dt_s),
            "sample_dt_s": float(sample_dt_s),
            "sat_rate_deg_s": float(sat_rate_deg_s),
            "motor_rate_deg_s": float(rate_deg_s),
            "commands_sent": srv.counters["P"],
            "dropped": srv.counters["dropped"],
            "stopped": stopped,
            "error": error,
            "cmd_vs_truth": {
                "samples": stats.get("samples", 0),
                "max_deg": stats.get("cmd_max_deg"),
                "mean_deg": stats.get("cmd_mean_deg"),
                "rms_deg": stats.get("cmd_rms_deg"),
            },
            "pose_error_by_source": by_src,
            "pose_stats": {
                "samples": stats.get("pose_samples", 0),
                "max_deg": stats.get("pose_max_deg"),
                "mean_deg": stats.get("pose_mean_deg"),
                "rms_deg": stats.get("pose_rms_deg"),
            },
            "lag_cmd_vs_pose": lag_row,
            "pose_source_summary": {
                "readback_samples": readback_n,
                "simulated_samples": sim_n,
                "mechanical_accuracy_claimable": readback_n > 0,
                "note": ("模拟后端（rotctld_sim）：只证指令链与量化误差；"
                         "无机械齿隙/风力/电机曲线，机械精度不可报"),
            },
        }
    finally:
        srv.stop()

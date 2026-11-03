# SPDX-License-Identifier: MIT
"""
MBDSDR - 多普勒链路补偿唯一真相源（RX 调谐预偏 / TX 发射预偏共用）
================================================================

背景与契约
----------
- 卫星相对地面站有径向速度 → 收发都带多普勒。补偿必须长在**链路**上：
  存在卫星目标（锁定/选中）时，接收调谐中心 = 标称下行 + 预测 Δ；
  发射上行频率 = 标称上行 − 预测 Δ（用**发射时刻**的预测值，不是录制时刻）。
- **唯一真相源**：RX 与 TX 的预测值都从本模块的 `DopplerLinkCompensator`
  取，任何第二处（Python 基带混频 / C++ 1Hz ZOH）不得与它同时生效，
  否则双倍偏移。本模块用 ``backend_comp_active`` 标志实现互斥：
  后端（C++/基带）声明补偿活动时，Python 侧拒绝开启并如实给冲突原因。
- **诚实回落**：目标丢失 / TLE 失效 / 无观测站 / 仰角<0 → 状态转 invalid、
  补偿自动关闭并带原因，绝不静默沿用过期偏移。
- 符号约定（与 sat_tracker / Satellite.cpp 一致，远离为正）：
    range_rate > 0（远离）→ doppler_hz < 0（接收频率降低）
    range_rate < 0（接近）→ doppler_hz > 0（接收频率升高）
    f_rx = f_downlink + doppler_hz
    f_tx = f_uplink − doppler_hz_uplink（上行预偏反向：
      目标接近（Δ>0）时发射频率压低，卫星收到的才是标称值）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

C_LIGHT_MPS = 299_792_458.0


@dataclass
class DopplerLinkState:
    """链路补偿当前状态（展示层只读：标称 / 实际 / Δ + 开关 + 原因）。"""
    target_name: str = ""
    downlink_hz: float = 0.0
    enabled: bool = False                    # 补偿开关（用户语义）
    valid: bool = False                      # 当前预测是否有效
    doppler_hz: Optional[float] = None       # 预测 Δ（RX 调谐偏移，Hz）
    corrected_freq_hz: Optional[float] = None  # 实际调谐中心 = 标称 + Δ
    range_rate_km_s: Optional[float] = None
    elevation_deg: Optional[float] = None
    reason: str = ""                         # 失效/冲突原因（诚实提示）
    backend_comp_active: bool = False        # 后端（C++/基带）补偿活动标志


@dataclass
class DopplerLinkCompensator:
    """RX/TX 共用的多普勒预测与互斥仲裁（单一真相源）。

    tracker 由调用方注入（desktop/main_window 的闭环跟踪器同源：
    skyfield EarthSatellite + Topos，真实 TLE）。本对象不联网、不造假：
    预测不出来就 invalid，绝不沿用旧值。
    """

    tracker: object = None                   # mbdsdr_ai.sat_tracker.SatelliteTracker
    state: DopplerLinkState = field(default_factory=DopplerLinkState)

    # ------------------------------------------------------------------ 目标
    def arm_target(self, name: str, downlink_hz: float) -> bool:
        """绑定补偿目标（选定/锁定卫星）。返回是否绑定成功。"""
        if self.tracker is None or not name or float(downlink_hz) <= 0:
            self.clear("无跟踪器或目标/频率非法")
            return False
        self.state.target_name = str(name)
        self.state.downlink_hz = float(downlink_hz)
        self.state.enabled = True
        return self.refresh()

    def clear(self, reason: str = "") -> None:
        """清除目标并关闭补偿（诚实回落，不保留过期偏移）。"""
        self.state.target_name = ""
        self.state.enabled = False
        self.state.valid = False
        self.state.doppler_hz = None
        self.state.corrected_freq_hz = None
        self.state.reason = reason

    # ------------------------------------------------------------------ 开关
    def set_enabled(self, on: bool) -> bool:
        """用户补偿开关。后端补偿活动时拒绝开启（防双补偿，返回 False）。"""
        if on and self.state.backend_comp_active:
            self.state.enabled = False
            self.state.reason = ("后端（C++/基带）多普勒补偿已活动，"
                                 "Python 侧保持关闭以防双倍偏移")
            return False
        self.state.enabled = bool(on)
        if on:
            return self.refresh()
        return True

    def set_backend_compensation_active(self, active: bool) -> None:
        """后端（C++ 1Hz ZOH / Python 基带混频）补偿活动声明。

        声明活动时若 Python 侧已开启，立即强制关闭并记录冲突原因——
        链路里同时只允许一侧做补偿，这是「不许双补偿」的硬约束。
        """
        self.state.backend_comp_active = bool(active)
        if active and self.state.enabled:
            self.state.enabled = False
            self.state.reason = ("后端补偿已接管，Python 侧补偿自动关闭"
                                 "（防止双倍偏移）")

    # ------------------------------------------------------------------ 预测
    def refresh(self, pos: Optional[dict] = None) -> bool:
        """用当前时刻重算预测多普勒。失效→invalid+关补偿+原因。

        pos 可选：调用方已算好的 current_position() 结果（跟踪环每拍喂入，
        避免重复 sgp4 传播）；None 时本方法自己向 tracker 现算（TX 发射时刻）。
        """
        st = self.state
        if self.tracker is None or not st.target_name:
            st.valid = False
            st.doppler_hz = None
            st.corrected_freq_hz = None
            st.reason = "未绑定卫星目标"
            return False
        if pos is None:
            try:
                pos = self.tracker.current_position()
            except Exception as e:  # noqa: BLE001 —— 失效必须诚实，不抛不穿
                pos = None
        if not pos or not pos.get("valid"):
            st.valid = False
            st.doppler_hz = None
            st.corrected_freq_hz = None
            st.enabled = False
            st.reason = "目标轨道数据失效（TLE 不可用/无观测站）"
            return False
        el = pos.get("elevation")
        if el is None or float(el) < 0.0:
            st.valid = False
            st.doppler_hz = None
            st.corrected_freq_hz = None
            st.enabled = False
            st.reason = f"目标落入地平线下（仰角 {el}°），补偿自动关闭"
            return False
        dop = float(pos["doppler_hz"])
        st.valid = True
        st.doppler_hz = dop
        st.corrected_freq_hz = st.downlink_hz + dop
        st.range_rate_km_s = float(pos.get("range_rate_km_s") or 0.0)
        st.elevation_deg = float(el)
        st.reason = ""
        return True

    # ------------------------------------------------------------------ 应用
    def rx_freq(self) -> Optional[float]:
        """RX 调谐中心 = 标称下行 + Δ（有效且开启时）；否则 None（不补偿）。"""
        if self.state.enabled and self.state.valid:
            return self.state.corrected_freq_hz
        return None

    def tx_freq(self, uplink_hz: float) -> Optional[float]:
        """TX 发射频率 = 标称上行 − Δ_tx（发射时刻预测，反向预偏）。

        Δ_tx 与 RX 同形（−range_rate/c·f）：目标接近（Δ>0）时发射频率压低，
        卫星实际收到的才是标称上行。后端补偿活动 / 未开启 / 预测失效 → None。
        """
        if self.state.backend_comp_active:
            return None
        if not (self.state.enabled and self.state.valid):
            return None
        rr = self.state.range_rate_km_s
        if rr is None:
            return None
        dop_tx = -float(rr) * 1000.0 / C_LIGHT_MPS * float(uplink_hz)
        return float(uplink_hz) - dop_tx


# ---------------------------------------------------------------------------
# 进程级单例（desktop 主窗口与 tc_uplink 工具面共用同一真相源）
# ---------------------------------------------------------------------------
_SINGLETON: Optional[DopplerLinkCompensator] = None


def get_compensator() -> DopplerLinkCompensator:
    """返回进程级补偿器单例（首次创建为空态，arm_target 后生效）。"""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = DopplerLinkCompensator()
    return _SINGLETON


def _reset_compensator_for_tests() -> None:
    """测试专用：清空单例（进程内全局态泄漏会跨测试串扰）。"""
    global _SINGLETON
    _SINGLETON = None

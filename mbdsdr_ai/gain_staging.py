"""
增益分级控制（LNA / Mixer / VGA 三档）
======================================

把接收机增益拆成三级独立设置：
- LNA   射频低噪声放大器（前端第一级）
- Mixer 混频器级增益（IF 第一级，常叫 "IF" / "Mixer"）
- VGA   基带可变增益放大器（后端最后一级，常叫 "VGA" / "BB" / "Baseband"）

移植自上游：
- gqrx/src/receivers/nbrx.cpp:47-49
    nbrx 信号链 filter → meter → sql → agc；增益分级在设备源（osmosdr）侧，
    GQRX 通过 set_gain_stage(LNA/Mixer/VGA) 分别下发。
- gr-osmosdr/lib/osmosdr_source_c.cc
    set_gain_mode / set_gain(gain, name) —— name 即增益元素名。
- SoapySDR include/SoapySDR/Device.hpp:
    - listGains(direction, channel) -> vector<string>   （Device.hpp:695 附近）
    - getGainRange(direction, channel, name) -> argRange (min,max,step)
    - setGain(direction, channel, value, name)          （Device.hpp:725 附近）

红线：
- 增益范围**从设备查询**（getGainRange），绝不硬编码 LNA=0..40 之类。
  不同设备（RTL-SDR/E4000、HackRF MAX2837+RFFC5072、bladeRF LMS6002D、
  Airspy HF+）每档元素名与范围都不同。
- 设备不支持某一档时，该档 set_* 返回 False 并记录 unsupported，不造假成功。
- 可注入 mock 设备做单测（只要 duck-type 出上述四个方法即可）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# 逻辑档 → 设备元素名候选别名（按优先级尝试）。
# 不同驱动叫法不同：
#   RTL-SDR (E4000):  "LNA", "IF"
#   HackRF:           "LNA", "VGA"      （RFFC5072=LNA, MAX5864=VGA）
#   bladeRF:          "LNA", "VGA1", "VGA2"
#   SoapyAirspy:      "LNA", "MIX", "VGA"
#   PlutoSDR:         "RF_BAL_LPF", "TIA", "BB"
# 我们不在此硬编码范围，只给「名字候选」让设备自报家门。
LOGICAL_STAGE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "LNA":   ("LNA", "RF", "RF_GAIN", "TIA", "LNA_GAIN"),
    "Mixer": ("Mixer", "MIX", "IF", "IF_GAIN", "MIX_GAIN", "RF_BAL_LPF"),
    "VGA":   ("VGA", "VGA1", "VGA2", "BB", "BASEBAND", "BB_GAIN", "VGA_GAIN"),
}


@dataclass
class StageInfo:
    """单档增益的运行时信息（范围从设备查询得到）。"""
    logical: str                 # "LNA"/"Mixer"/"VGA"
    elem_name: str               # 设备实际元素名
    min_db: float = 0.0
    max_db: float = 0.0
    supported: bool = True
    current_db: float = 0.0

    def clamp(self, db: float) -> float:
        return max(self.min_db, min(self.max_db, float(db)))


class GainStager:
    """三档增益分级控制器。

    Parameters
    ----------
    device : object
        SoapySDR.Device 兼容对象（mock 亦可）。需提供：
        - listGains(direction, channel) -> list[str]
        - getGainRange(direction, channel, name) -> (min, max) 或带 .minimum/.maximum
        - setGain(direction, channel, value, name)
        - getGain(direction, channel, name) -> float   （可选，用于回读）
    direction : int
        SoapySDR 方向常量（RX=0 通常；传 0 即可，RX 常量在 SoapySDR 里就是 0）。
    channel : int
        通道号，默认 0。
    """

    def __init__(self, device: Any, direction: int = 0, channel: int = 0):
        self.device = device
        self.direction = direction
        self.channel = channel
        self.stages: Dict[str, StageInfo] = {}
        self._bind_stages()

    # ------------------------------------------------------------------
    # 初始化：从设备查询可用增益元素并映射到 LNA/Mixer/VGA
    # ------------------------------------------------------------------
    def _query_elems(self) -> List[str]:
        """从设备查可用增益元素名；设备不支持时返回空列表。"""
        if self.device is None:
            return []
        try:
            elems = self.device.listGains(self.direction, self.channel)
            return [str(e) for e in (elems or [])]
        except Exception as e:
            logger.warning("listGains 失败（设备可能不支持分档）: %s", e)
            return []

    def _query_range(self, elem: str) -> Optional[Tuple[float, float]]:
        """查询某元素的 (min_db, max_db)。设备不支持时返回 None。"""
        if self.device is None:
            return None
        try:
            r = self.device.getGainRange(self.direction, self.channel, elem)
            # SoapySDR argRange 对象有 .minimum / .maximum 属性；
            # 也可能是 (min,max) 元组或 [min,max] 列表。
            if hasattr(r, "minimum") and hasattr(r, "maximum"):
                return (float(r.minimum), float(r.maximum))
            if isinstance(r, (tuple, list)) and len(r) >= 2:
                return (float(r[0]), float(r[1]))
        except Exception as e:
            logger.warning("getGainRange(%s) 失败: %s", elem, e)
        return None

    def _bind_stages(self) -> None:
        """把 LNA/Mixer/VGA 三个逻辑档绑到设备实际元素名。

        遍历 LOGICAL_STAGE_ALIASES，按别名优先级找设备里真实存在的元素；
        找不到就标记 supported=False（不硬编码默认范围）。
        """
        available = self._query_elems()
        avail_lower = {a.lower(): a for a in available}
        for logical, aliases in LOGICAL_STAGE_ALIASES.items():
            chosen: Optional[str] = None
            for alias in aliases:
                hit = avail_lower.get(alias.lower())
                if hit is not None:
                    chosen = hit
                    break
            if chosen is None:
                # 设备没这一档：不造假，标记不支持
                self.stages[logical] = StageInfo(
                    logical=logical, elem_name="",
                    supported=False)
                logger.info("增益档 %s：设备无对应元素（available=%s）",
                            logical, available)
                continue
            rng = self._query_range(chosen)
            if rng is None:
                lo, hi = (0.0, 0.0)
            else:
                lo, hi = rng
            self.stages[logical] = StageInfo(
                logical=logical, elem_name=chosen,
                min_db=lo, max_db=hi, supported=True)
            logger.info("增益档 %s -> 设备元素 '%s' 范围 [%.1f, %.1f] dB",
                        logical, chosen, lo, hi)

    # ------------------------------------------------------------------
    # 公共 API：三档独立设置
    # ------------------------------------------------------------------
    def _set_stage(self, logical: str, db: float) -> bool:
        """设置某一档增益。范围从设备查询得到后 clamp，绝不越界。"""
        info = self.stages.get(logical)
        if info is None:
            return False
        if not info.supported:
            logger.debug("增益档 %s 不被设备支持，忽略 set(%.1f)", logical, db)
            return False
        clamped = info.clamp(db)
        try:
            self.device.setGain(self.direction, self.channel,
                                float(clamped), info.elem_name)
            info.current_db = clamped
            return True
        except Exception as e:
            logger.warning("setGain(%s=%.1f) 失败: %s",
                           info.elem_name, clamped, e)
            return False

    def set_lna(self, db: float) -> bool:
        """设置射频 LNA 增益 dB。"""
        return self._set_stage("LNA", db)

    def set_mixer(self, db: float) -> bool:
        """设置混频/IF 级增益 dB。"""
        return self._set_stage("Mixer", db)

    def set_vga(self, db: float) -> bool:
        """设置基带 VGA 增益 dB。"""
        return self._set_stage("VGA", db)

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """返回三档当前状态（供 UI/AI 展示）。"""
        out: Dict[str, Any] = {}
        for logical, info in self.stages.items():
            out[logical] = {
                "element": info.elem_name,
                "supported": info.supported,
                "min_db": info.min_db,
                "max_db": info.max_db,
                "current_db": info.current_db,
            }
        return out

    def stage_info(self, logical: str) -> Optional[StageInfo]:
        return self.stages.get(logical)

    def is_supported(self, logical: str) -> bool:
        info = self.stages.get(logical)
        return bool(info and info.supported)

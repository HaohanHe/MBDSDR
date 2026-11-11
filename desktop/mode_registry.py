# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端解调模式注册表
============================

集中声明所有解调模式（FM/WFM/NFM/AM/USB/LSB/CW/RAW/DIG）的元数据：
显示名、默认 VFO 带宽、音频截止频率、解调函数路径、频偏、分类。

设计原则：
  - 声明式：新增解调模式 = 写一个 DemodMode + register() 一行，
    UI 模式下拉、解调调度、带宽默认值自动出现，不改核心 if-else。
  - 懒加载解调函数：demod_func 是 "module.path:func_name" 字符串，
    真正解调时才 import，避免注册表 import 触发 numpy/scipy。
  - 只读查询：get/list 不修改全局状态。
  - 向后兼容：MODE_VFO_BANDWIDTH 旧字典仍可从注册表导出，
    未迁移的代码暂时不受影响。

红线：注册表只描述"有哪些模式、默认参数、解调函数在哪"，不造任何业务数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# 运行时注册表的唯一持有者是 `mode_registry_store`。它只按裸名导入：无论本模块
# 以裸名（面板）还是包名（测试/工具）被执行，`import mode_registry_store` 都命中
# 同一个 sys.modules 条目，两份模块实例共享同一份状态。
# 不合并模块名（那要 sys.modules 别名），只把可变状态收口到一个持有者；
# 本模块对外只提供读函数，不暴露注册表字典。
import mode_registry_store as _store


@dataclass(frozen=True)
class DemodMode:
    """一个解调模式的完整描述符。"""

    mode: str                    # 模式唯一标识，如 "WFM" / "NFM" / "AM"
    display_name: str            # UI 显示名，如 "WFM 广播" / "窄带 FM"
    category: str = "analog"     # 分类：analog / digital / raw
    default_bandwidth_hz: float = 12_500.0  # 默认 VFO 信道带宽
    audio_cutoff_hz: float = 4000.0         # 解调后音频低通截止（回退路径用）
    # 解调函数懒加载路径："module.path:func_name"
    # 函数签名：func(iq: np.ndarray, **kwargs) -> np.ndarray
    demod_func: str = ""
    # 传给解调函数的额外参数（如 deviation=5000.0, tone_freq=700.0）
    demod_kwargs: Dict[str, Any] = field(default_factory=dict)
    # 是否需要 VFO 信道滤波（WFM 跳过 VFO 直接全带宽鉴频）
    needs_vfo: bool = True
    # 是否输出音频（RAW/DIG 不解调不输出）
    outputs_audio: bool = True
    description: str = ""

    def load_demod(self) -> Optional[Callable[..., Any]]:
        """懒加载解调函数，失败返回 None。"""
        if not self.demod_func:
            return None
        try:
            module_path, func_name = self.demod_func.split(":", 1)
            import importlib
            mod = importlib.import_module(module_path)
            return getattr(mod, func_name, None)
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────
# 内置解调模式表
# ─────────────────────────────────────────────────────────────────────

_BUILTIN_MODES: Dict[str, DemodMode] = {
    "WFM": DemodMode(
        mode="WFM",
        display_name="WFM 广播",
        category="analog",
        default_bandwidth_hz=180_000.0,
        audio_cutoff_hz=15_000.0,
        demod_func="mbdsdr_ai.dsp:wfm_broadcast_demod",
        demod_kwargs={"audio_sr": 48000},
        needs_vfo=False,
        outputs_audio=True,
        description="广播调频立体声，全带宽鉴频，跳过 VFO",
    ),
    "NFM": DemodMode(
        mode="NFM",
        display_name="窄带 FM",
        category="analog",
        default_bandwidth_hz=12_500.0,
        audio_cutoff_hz=3_000.0,
        demod_func="mbdsdr_ai.dsp:fm_demod",
        demod_kwargs={"deviation": 5000.0},
        needs_vfo=True,
        outputs_audio=True,
        description="窄带调频语音，频偏 5kHz",
    ),
    "FM": DemodMode(
        mode="FM",
        display_name="FM",
        category="analog",
        default_bandwidth_hz=12_500.0,
        audio_cutoff_hz=15_000.0,
        demod_func="mbdsdr_ai.dsp:fm_demod",
        demod_kwargs={"deviation": 75000.0},
        needs_vfo=True,
        outputs_audio=True,
        description="调频语音（宽频偏 75kHz）",
    ),
    "AM": DemodMode(
        mode="AM",
        display_name="AM",
        category="analog",
        default_bandwidth_hz=6_000.0,
        audio_cutoff_hz=4_000.0,
        demod_func="mbdsdr_ai.dsp:am_demod",
        demod_kwargs={},
        needs_vfo=True,
        outputs_audio=True,
        description="幅度调制",
    ),
    "USB": DemodMode(
        mode="USB",
        display_name="USB",
        category="analog",
        default_bandwidth_hz=2_400.0,
        audio_cutoff_hz=3_000.0,
        demod_func="mbdsdr_ai.dsp:ssb_demod",
        demod_kwargs={"mode": "USB"},
        needs_vfo=True,
        outputs_audio=True,
        description="上边带语音",
    ),
    "LSB": DemodMode(
        mode="LSB",
        display_name="LSB",
        category="analog",
        default_bandwidth_hz=2_400.0,
        audio_cutoff_hz=3_000.0,
        demod_func="mbdsdr_ai.dsp:ssb_demod",
        demod_kwargs={"mode": "LSB"},
        needs_vfo=True,
        outputs_audio=True,
        description="下边带语音",
    ),
    "CW": DemodMode(
        mode="CW",
        display_name="CW",
        category="analog",
        default_bandwidth_hz=500.0,
        audio_cutoff_hz=1_000.0,
        demod_func="mbdsdr_ai.dsp:cw_demod",
        demod_kwargs={"tone_freq": 700.0},
        needs_vfo=True,
        outputs_audio=True,
        description="等幅报，700Hz 音调",
    ),
    "RAW": DemodMode(
        mode="RAW",
        display_name="RAW",
        category="raw",
        default_bandwidth_hz=12_500.0,
        audio_cutoff_hz=0.0,
        demod_func="",
        needs_vfo=True,
        outputs_audio=False,
        description="原始 IQ 直通，不解调不输出音频",
    ),
    "DIG": DemodMode(
        mode="DIG",
        display_name="DIG",
        category="digital",
        default_bandwidth_hz=12_500.0,
        audio_cutoff_hz=0.0,
        demod_func="",
        needs_vfo=True,
        outputs_audio=False,
        description="数字模式直通，由后台解码器处理",
    ),
}


# ─────────────────────────────────────────────────────────────────────
# 运行时注册表（可动态追加）
# ─────────────────────────────────────────────────────────────────────

_store.init_defaults(_BUILTIN_MODES)


def register(mode: DemodMode) -> None:
    """注册/覆盖一个解调模式。"""
    _store.put(mode)


def unregister(mode_name: str) -> None:
    """移除一个运行时注册的模式（内置模式也可移除；主要供测试清理）。"""
    _store.discard(mode_name)


def get(mode: str) -> Optional[DemodMode]:
    """按模式名获取描述符，不存在返回 None。"""
    return _store.get(mode)


def list_modes(category: Optional[str] = None) -> List[DemodMode]:
    """列出所有模式；可选按分类过滤。按内置顺序返回。"""
    if category is None:
        return _store.values()
    return [m for m in _store.values() if m.category == category]


def mode_names(category: Optional[str] = None) -> List[str]:
    """返回模式名列表（用于填充 QComboBox）。"""
    return [m.mode for m in list_modes(category)]


def default_bandwidth(mode: str) -> float:
    """获取模式的默认 VFO 带宽，未知模式回退 12.5kHz。"""
    m = get(mode)
    if m is not None:
        return m.default_bandwidth_hz
    return 12_500.0


# 滤波宽窄预设三元组 (窄, 正常, 宽)（gqrx filter_preset_table 惯例，
# 键盘 , / . 步进档位的数据源；未列出的模式按默认值 ±50% 派生）。
_BANDWIDTH_PRESETS = {
    "WFM": (120_000.0, 180_000.0, 250_000.0),
    "NFM": (7_500.0, 12_500.0, 25_000.0),
    "FM":  (12_500.0, 25_000.0, 50_000.0),
    "AM":  (4_000.0, 6_000.0, 10_000.0),
    "USB": (1_800.0, 2_400.0, 3_000.0),
    "LSB": (1_800.0, 2_400.0, 3_000.0),
    "CW":  (300.0, 500.0, 1_000.0),
}


def bandwidth_presets(mode: str) -> Tuple[float, float, float]:
    """模式的 (窄, 正常, 宽) 滤波带宽预设；未知模式按默认值派生。"""
    key = (mode or "").upper()
    if key in _BANDWIDTH_PRESETS:
        return _BANDWIDTH_PRESETS[key]
    d = default_bandwidth(mode)
    return (d * 0.5, d, d * 2.0)


def next_bandwidth_preset(mode: str, current_hz: float, direction: int) -> float:
    """在当前预设档位表中按 direction(+1/-1) 步进一档并回绕。

    当前带宽不在表内时，先吸附到最近档再步进。返回新带宽 (Hz)。
    """
    narrow, _normal, wide = bandwidth_presets(mode)
    table = [narrow, _normal, wide]
    cur = float(current_hz)
    # 最近档索引
    idx = min(range(3), key=lambda i: abs(table[i] - cur))
    if abs(table[idx] - cur) > 1e-9:
        # 不在档上：先吸附（gqrx 手感：按一下到最近档）
        return table[idx]
    idx = (idx + (1 if direction > 0 else -1)) % 3
    return table[idx]


def audio_cutoff(mode: str) -> float:
    """获取模式的音频截止频率，未知模式回退 4kHz。"""
    m = get(mode)
    if m is not None:
        return m.audio_cutoff_hz
    return 4_000.0


def demodulate(mode: str, iq, sample_rate: float = 48000.0,
               **kwargs) -> Optional[Any]:
    """
    便捷入口：按模式名解调 IQ 数据。

    参数:
        mode:        模式名
        iq:          complex64 IQ 数组
        sample_rate: 采样率 (Hz)，传给解调函数
        **kwargs:    覆盖或追加解调参数

    返回:
        解调后的音频数组；模式无解调函数或加载失败返回 None
    """
    m = get(mode)
    if m is None:
        return None
    fn = m.load_demod()
    if fn is None:
        return None
    call_kwargs = dict(m.demod_kwargs)
    call_kwargs.update(kwargs)
    call_kwargs.setdefault("sample_rate", sample_rate)
    try:
        return fn(iq, **call_kwargs)
    except TypeError:
        # 某些解调函数不接受 sample_rate 参数（如 am_demod），去掉重试
        call_kwargs.pop("sample_rate", None)
        return fn(iq, **call_kwargs)


# ─────────────────────────────────────────────────────────────────────
# 向后兼容：导出旧 MODE_VFO_BANDWIDTH 字典
# ─────────────────────────────────────────────────────────────────────

def to_bandwidth_dict() -> Dict[str, float]:
    """导出 {mode: default_bandwidth_hz} 字典，供未迁移代码兼容使用。"""
    return {m.mode: m.default_bandwidth_hz for m in _store.values()}

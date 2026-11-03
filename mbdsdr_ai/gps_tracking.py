# SPDX-License-Identifier: MIT
"""GPS C/A 单通道 DLL / Costas PLL 跟踪（纯计算、外部注入参考码）。

本模块只处理已由捕获环节给出粗略码相位和多普勒的**单星、单通道**
复基带 IQ。参考 C/A 码由调用方显式注入（标准码可用
``mbdsdr_ai.gps_ca.ca_code(prn)`` 取得——IS-GPS-200 Table 3-I 已内置并
钉扎）；本模块自身不从 PRN 号猜测码序列。输出是逐毫秒相关与环路状态，
不做绝对伪距、PVT，也不构成真实 OTA 信号验证。

符号与采样约定
---------------
* 输入信号的正多普勒为 ``exp(+2j*pi*f*t)``，本地载波 NCO 以
  ``exp(-2j*pi*f_hat*t)`` 混频；因此正残余相位产生正 Costas 鉴别器，
  下一拍载波频率/相位向正方向修正。
* ``initial_code_phase_chips`` 是码时延（不是本地码读指针）：输入为
  ``ca_code[floor(R_ca*t - delay) mod 1023]`` 时，应传 ``delay``。
  捕获器返回的非分数码时延可直接作为初值。输出的 ``code_phase_chips``
  仍是同一口径的时延估计，范围为 ``[0, 1023)``。
* 本地早码读指针 = 准码读指针 + spacing，晚码 = 准码读指针 - spacing。
  早晚能量差 ``(|E|-|L|)/(|E|+|L|)>0`` 表示本地码过晚，时延估计减小。
* 第 m 个毫秒块使用采样 ``[start_m, stop_m)``；边界为
  ``floor(m*fs/1000 + 0.5)``，支持非整数 samples/ms。所有环路系数均乘
  该块的真实 ``dt=(stop-start)/fs``，不是把采样间隔误当作环路 dt。
* 相关值是块内平均。``prompt_i`` 是 Costas 环后的实数软判决，可按
  ``samples_per_bit=20`` 直接送入现有 ``gps_bit_sync.nav_bit_sync``。
  Costas BPSK 环存在固有的 pi 相位模糊，后续电文前导/奇偶校验须解决
  可能的整体反相；本模块不伪造该消歧结果。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Sequence

import numpy as np

__all__ = ["TrackingConfig", "track_gps_ca"]

CA_CHIP_RATE = 1.023e6
CA_CODE_LENGTH = 1023
_TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class TrackingConfig:
    """DLL/PLL 与锁定检测参数。

    两个带宽均为二阶环的等效噪声带宽（Hz）。连续域自然频率采用
    ``wn = 8*damping*BW/(4*damping**2 + 1)``，比例/积分增益为
    ``kp = 2*damping*wn``、``ki = wn**2``。离散更新使用每个相关块的真实
    ``dt``。DLL 驱动码 NCO 相位（chip）和码速率（chip/s），PLL 驱动载波
    NCO 相位（rad）和频率（Hz）。
    """

    dll_bandwidth_hz: float = 2.0
    pll_bandwidth_hz: float = 10.0
    damping: float = math.sqrt(0.5)
    early_late_spacing_chips: float = 0.5
    lock_code_ratio_db: float = 4.0
    lock_carrier_min: float = 0.65
    lock_dll_max: float = 0.35
    lock_window_ms: int = 20
    lock_confirm_ms: int = 20
    loss_confirm_ms: int = 10

    def __post_init__(self) -> None:
        for name in ("dll_bandwidth_hz", "pll_bandwidth_hz"):
            value = _finite_float(getattr(self, name), name)
            if value <= 0.0:
                raise ValueError(f"{name} 必须 > 0")
        damping = _finite_float(self.damping, "damping")
        if damping <= 0.0:
            raise ValueError("damping 必须 > 0")
        spacing = _finite_float(
            self.early_late_spacing_chips, "early_late_spacing_chips")
        if not 0.0 < spacing <= 1.0:
            raise ValueError("early_late_spacing_chips 必须在 (0, 1] 内")
        ratio = _finite_float(self.lock_code_ratio_db, "lock_code_ratio_db")
        if ratio < 0.0:
            raise ValueError("lock_code_ratio_db 不能为负")
        carrier = _finite_float(self.lock_carrier_min, "lock_carrier_min")
        if not 0.0 <= carrier <= 1.0:
            raise ValueError("lock_carrier_min 必须在 [0, 1] 内")
        dll_max = _finite_float(self.lock_dll_max, "lock_dll_max")
        if not 0.0 < dll_max <= 1.0:
            raise ValueError("lock_dll_max 必须在 (0, 1] 内")
        for name in ("lock_window_ms", "lock_confirm_ms", "loss_confirm_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise ValueError(f"{name} 必须为正整数")
            if int(value) < 1:
                raise ValueError(f"{name} 必须为正整数")


def _finite_float(value: Any, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} 必须为有限实数") from exc
    if not math.isfinite(out):
        raise ValueError(f"{name} 必须为有限实数")
    return out


def _loop_gains(bandwidth_hz: float, damping: float) -> tuple[float, float]:
    wn = 8.0 * damping * bandwidth_hz / (4.0 * damping * damping + 1.0)
    return 2.0 * damping * wn, wn * wn


def _reference_code(ca_code: Sequence[float]) -> np.ndarray:
    raw = np.asarray(ca_code)
    if raw.ndim != 1 or raw.size != CA_CODE_LENGTH:
        raise ValueError(f"ca_code 必须是一维 {CA_CODE_LENGTH} chip 外部参考码")
    if np.iscomplexobj(raw):
        if np.any(np.imag(raw) != 0.0):
            raise ValueError("ca_code 必须为实数 ±1 序列")
        raw = np.real(raw)
    try:
        code = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("ca_code 必须为实数 ±1 序列") from exc
    if not np.all(np.isfinite(code)) or not np.all(np.abs(code) == 1.0):
        raise ValueError("ca_code 只能含 ±1；请由调用方注入已核验的 C/A 码")
    return code


def _input_iq(iq: Sequence[complex]) -> np.ndarray:
    try:
        x = np.asarray(iq, dtype=np.complex128)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("iq 必须是一维有限复数采样") from exc
    if x.ndim != 1 or x.size == 0:
        raise ValueError("iq 必须是一维非空复数采样")
    if not np.all(np.isfinite(x)):
        raise ValueError("iq 含 NaN/Inf")
    return x


def _costas_bpsk_error(prompt: complex) -> float:
    """二倍角 Costas BPSK 相位误差，消除 0/pi 数据符号模糊。"""
    i, q = float(prompt.real), float(prompt.imag)
    if i == 0.0 and q == 0.0:
        return 0.0
    return 0.5 * math.atan2(2.0 * i * q, i * i - q * q)


def _wrap_phase(value: float) -> float:
    return (value + math.pi) % _TWO_PI - math.pi


def _code_delay(nco_phase: float, time_s: float) -> float:
    return (CA_CHIP_RATE * time_s - nco_phase) % CA_CODE_LENGTH


def track_gps_ca(
    iq: Sequence[complex],
    sample_rate_hz: float,
    ca_code: Sequence[float],
    initial_code_phase_chips: float,
    initial_doppler_hz: float,
    *,
    initial_carrier_phase_rad: float = 0.0,
    doppler_rate_hz_s: float = 0.0,
    config: TrackingConfig | None = None,
) -> Dict[str, Any]:
    """跟踪单通道 GPS C/A 信号并返回逐毫秒测量。

    参数
    ----
    iq : 一维复基带采样。正频率约定见模块 docstring。
    sample_rate_hz : 采样率（Hz），不要求 samples/ms 为整数。
    ca_code : 外部提供的 1023 chip、实数 ±1 参考码，顺序与输入信号一致。
    initial_code_phase_chips : 初始码时延，范围 [0, 1023)，允许分数 chip。
    initial_doppler_hz : 捕获给出的初始载波频偏（Hz）。
    initial_carrier_phase_rad : 第一个采样处的本地载波 NCO 初相（rad）。
    doppler_rate_hz_s : 可选的已知多普勒斜率前馈（Hz/s）；未知时传 0，
        二阶 PLL 可跟踪有限斜率但会有相应动态应力残差。
    config : 显式 DLL/PLL、早晚间隔与锁定门限。

    返回
    ----
    字典中的 ``prompt``/``early``/``late`` 为逐毫秒复相关均值；
    ``prompt_i`` 可直接作为 ``nav_bit_sync(..., samples_per_bit=20)`` 的输入。
    ``code_phase_chips``、``carrier_frequency_hz``、``carrier_phase_rad`` 是
    每块环路更新后的块末估计。``dll_discriminator`` 无量纲，
    ``pll_discriminator_rad`` 为 Costas 相位误差。``code_lock_db`` 是准码
    对早晚平均相关幅度的 dB 比，``carrier_lock`` 是窗口内二倍角相位相干度。
    ``locked_per_ms`` 应用连续确认/失锁滞回，末尾 ``locked``/``lost_lock``
    等字段给出汇总状态。

    码片采用零阶保持采样映射；当初始码差小于一个采样间隔时，合成矩形码
    可能落在同一采样码相位 bin 内而无法继续细分。本模块不把这种采样等价
    误报为任意亚采样时延精度，也不对带限模拟前端作未经验证的补偿。

    输入末尾不足一个完整毫秒块的采样不静默计入相关，而以
    ``n_samples_trailing`` 如实报告。
    """
    x = _input_iq(iq)
    fs = _finite_float(sample_rate_hz, "sample_rate_hz")
    if fs <= 0.0:
        raise ValueError("sample_rate_hz 必须 > 0")
    code = _reference_code(ca_code)
    delay0 = _finite_float(initial_code_phase_chips, "initial_code_phase_chips")
    if not 0.0 <= delay0 < CA_CODE_LENGTH:
        raise ValueError("initial_code_phase_chips 必须在 [0, 1023) 内")
    carrier_freq = _finite_float(initial_doppler_hz, "initial_doppler_hz")
    carrier_phase = _finite_float(
        initial_carrier_phase_rad, "initial_carrier_phase_rad")
    doppler_rate = _finite_float(doppler_rate_hz_s, "doppler_rate_hz_s")
    cfg = TrackingConfig() if config is None else config
    if not isinstance(cfg, TrackingConfig):
        raise ValueError("config 必须是 TrackingConfig")

    # 构造毫秒采样边界。尾采样不进入任何相关块。
    boundaries = [0]
    m = 1
    while True:
        stop = int(math.floor(m * fs / 1000.0 + 0.5))
        if stop > x.size:
            break
        if stop <= boundaries[-1]:
            raise ValueError("sample_rate_hz 过低，无法形成非空 1 ms 相关块")
        boundaries.append(stop)
        m += 1
    n_ms = len(boundaries) - 1
    if n_ms == 0:
        raise ValueError("iq 不足一个完整 1 ms 相关块")

    dll_kp, dll_ki = _loop_gains(cfg.dll_bandwidth_hz, cfg.damping)
    pll_kp, pll_ki = _loop_gains(cfg.pll_bandwidth_hz, cfg.damping)
    spacing = float(cfg.early_late_spacing_chips)
    code_nco_phase = -delay0
    code_rate = CA_CHIP_RATE
    carrier_phase_history: list[float] = []
    good_ms = 0
    bad_ms = 0
    is_locked = False
    ever_locked = False
    lock_events = 0
    loss_events = 0

    out: Dict[str, Any] = {
        "sample_start": [], "sample_stop": [], "time_s": [], "dt_s": [],
        "early": [], "prompt": [], "late": [],
        "prompt_i": [], "prompt_q": [],
        "code_phase_chips": [], "code_rate_chips_s": [],
        "carrier_frequency_hz": [], "carrier_phase_rad": [],
        "dll_discriminator": [], "pll_discriminator_rad": [],
        "code_lock_db": [], "carrier_lock": [], "lock_point": [],
        "locked_per_ms": [],
    }

    for block in range(n_ms):
        start, stop = boundaries[block], boundaries[block + 1]
        count = stop - start
        dt = count / fs
        rel_t = np.arange(count, dtype=np.float64) / fs

        # 本地载波： exp(-j*NCO)，正输入频偏对应正 initial_doppler_hz。
        nco_phase = (carrier_phase + _TWO_PI * carrier_freq * rel_t
                     + math.pi * doppler_rate * rel_t * rel_t)
        baseband = x[start:stop] * np.exp(-1j * nco_phase)

        # 本地码读指针与“正码时延”状态符号相反。
        prompt_phase = code_nco_phase + code_rate * rel_t
        prompt_idx = np.floor(prompt_phase).astype(np.int64) % CA_CODE_LENGTH
        early_idx = np.floor(prompt_phase + spacing).astype(np.int64) % CA_CODE_LENGTH
        late_idx = np.floor(prompt_phase - spacing).astype(np.int64) % CA_CODE_LENGTH
        prompt = complex(np.mean(baseband * code[prompt_idx]))
        early = complex(np.mean(baseband * code[early_idx]))
        late = complex(np.mean(baseband * code[late_idx]))

        early_amp, late_amp = abs(early), abs(late)
        el_sum = early_amp + late_amp
        dll_error = ((early_amp - late_amp) / el_sum) if el_sum > 0.0 else 0.0
        pll_error = _costas_bpsk_error(prompt)
        el_mean = 0.5 * el_sum
        if abs(prompt) > 0.0 and el_mean > 0.0:
            code_db = 20.0 * math.log10(abs(prompt) / el_mean)
            code_db = max(-200.0, min(200.0, code_db))
        else:
            code_db = -200.0

        # 先把 NCO 推进到块末，再以本块真实 dt 应用环路滤波。
        code_nco_phase += code_rate * dt
        code_nco_phase += dll_kp * dll_error * dt
        code_rate += dll_ki * dll_error * dt
        carrier_phase += (_TWO_PI * carrier_freq * dt
                          + math.pi * doppler_rate * dt * dt)
        carrier_freq += doppler_rate * dt
        carrier_phase += pll_kp * pll_error * dt
        carrier_freq += pll_ki * pll_error * dt / _TWO_PI
        carrier_phase = _wrap_phase(carrier_phase)

        carrier_phase_history.append(math.cos(2.0 * pll_error)
                                     if abs(prompt) > 0.0 else 0.0)
        carrier_lock = float(np.mean(
            carrier_phase_history[-int(cfg.lock_window_ms):]))
        raw_lock = bool(code_db >= cfg.lock_code_ratio_db
                        and carrier_lock >= cfg.lock_carrier_min
                        and abs(dll_error) <= cfg.lock_dll_max)
        if raw_lock:
            good_ms += 1
            bad_ms = 0
            if not is_locked and good_ms >= int(cfg.lock_confirm_ms):
                is_locked = True
                ever_locked = True
                lock_events += 1
        else:
            bad_ms += 1
            good_ms = 0
            if is_locked and bad_ms >= int(cfg.loss_confirm_ms):
                is_locked = False
                loss_events += 1

        block_time = stop / fs
        out["sample_start"].append(start)
        out["sample_stop"].append(stop)
        out["time_s"].append(block_time)
        out["dt_s"].append(dt)
        out["early"].append(early)
        out["prompt"].append(prompt)
        out["late"].append(late)
        out["prompt_i"].append(float(prompt.real))
        out["prompt_q"].append(float(prompt.imag))
        out["code_phase_chips"].append(_code_delay(code_nco_phase, block_time))
        out["code_rate_chips_s"].append(float(code_rate))
        out["carrier_frequency_hz"].append(float(carrier_freq))
        out["carrier_phase_rad"].append(float(carrier_phase))
        out["dll_discriminator"].append(float(dll_error))
        out["pll_discriminator_rad"].append(float(pll_error))
        out["code_lock_db"].append(float(code_db))
        out["carrier_lock"].append(carrier_lock)
        out["lock_point"].append(raw_lock)
        out["locked_per_ms"].append(bool(is_locked))

    out.update({
        "n_ms": n_ms,
        "n_samples_used": int(boundaries[-1]),
        "n_samples_trailing": int(x.size - boundaries[-1]),
        "locked": bool(is_locked),
        "ever_locked": bool(ever_locked),
        "lost_lock": bool(ever_locked and not is_locked),
        "lock_events": int(lock_events),
        "loss_events": int(loss_events),
        "config": asdict(cfg),
        "sign_convention": {
            "carrier": "x*exp(+2j*pi*f*t); local NCO uses exp(-j*phase)",
            "code": "ca_code[floor(R_ca*t-delay) mod 1023]; output is delay",
            "early_late": "early=prompt+spacing; late=prompt-spacing",
        },
        "note": ("单通道合成/已捕获 IQ 的 DLL/Costas PLL 测量；不含绝对伪距、"
                 "PVT 或 OTA 验证。prompt_i 可接 nav_bit_sync(20 samples/bit)；"
                 "BPSK 的 pi 相位模糊须由后续电文校验消歧。"),
    })
    return out

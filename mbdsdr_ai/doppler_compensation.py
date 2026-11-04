# SPDX-License-Identifier: MIT
"""
MBDSDR - 多普勒频移补偿（NCO 数字下混频，确定性实现）
================================================================

过境卫星相对地面站有径向速度 → 接收 IQ 被乘上一个时变复指数（多普勒）。
本模块用数字振荡器（NCO）逐样本累积相位后混频，把多普勒**逆运算**回基带：

    接收模型  r(t) = s(t) · exp(+j 2π ∫ f_d(τ) dτ)
    补偿      s̃(t) = r(t) · exp(-j 2π ∫ f_d(τ) dτ)

其中 f_d(t) 既可以来自 TLE/轨道预报（:mod:`mbdsdr_ai.sat_passes.compute_doppler_curve`），
也可以是直接给定的逐样本频移序列（实测 AFC 输出）。

设计要点：
- **确定性**：全程 float64、无全局随机态；``apply_doppler_shift`` 与
  ``remove_doppler_shift`` 用同一套相位累积，成对调用精确回原（仅浮点舍入）。
- **可注入频移**：``doppler_hz`` 支持
    * 标量（恒定频偏）；
    * 等长数组（逐样本瞬时频移 Hz）；
    * callable(t_seconds) -> Hz（任意连续轨迹，如线性 chirp / SGP4 曲线）。
- **无硬件诚实空态**：本模块只做数学混频，不联网、不读串口；频移序列由调用方
  负责给出（预报或实测），不给定时就是恒等（零补偿），绝不"猜"多普勒。

公开方法学：数字下混频 = 复振荡器混频（标准 SDR DSP，与 gr-fir / UHD dsp 同原理）；
NCO 相位累积 phase[k] = 2π/fs · Σ f[j]，矩形近似积分。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "remove_doppler_shift", "apply_doppler_shift", "doppler_phase_radians",
    "DopplerCompensationError", "DopplerCompensationStage",
]

# doppler_hz 允许的类型：标量 / 逐样本数组 / t->Hz 可调用
DopplerSpec = Union[float, int, np.ndarray, Callable[[float], float]]


def _resolve_doppler_vector(doppler_hz: DopplerSpec, n: int, fs: float) -> np.ndarray:
    """把标量/数组/callable 统一成长度 n 的 float64 逐样本频移向量。"""
    if callable(doppler_hz):
        t = np.arange(n, dtype=np.float64) / float(fs)
        f = np.empty(n, dtype=np.float64)
        for k in range(n):
            f[k] = float(doppler_hz(float(t[k])))
        return f
    arr = np.asarray(doppler_hz, dtype=np.float64)
    if arr.ndim == 0 or arr.size == 1:
        return np.full(n, float(arr.flat[0]), dtype=np.float64)
    if arr.size != n:
        raise ValueError(
            f"doppler_hz 长度 {arr.size} 与样本数 n={n} 不一致；"
            f"应给标量、等长数组或 callable(t)->Hz")
    return arr


def doppler_phase_radians(doppler_hz: DopplerSpec, n: int, fs: float) -> np.ndarray:
    """逐样本累积多普勒相位（弧度）：phase[k] = 2π/fs · Σ_{j=0..k} f[j]。

    矩形近似积分；返回长度 n 的 float64 相位向量。
    """
    f = _resolve_doppler_vector(doppler_hz, n, fs)
    return (2.0 * np.pi / float(fs)) * np.cumsum(f)


def remove_doppler_shift(iq: np.ndarray, fs: float,
                         doppler_hz: DopplerSpec) -> np.ndarray:
    """补偿 IQ 上的多普勒：乘以 exp(-j·phase)，把信号拉回基带。

    参数:
        iq:        复基带样本（complex64/128）。
        fs:        采样率 Hz。
        doppler_hz: 施加在 IQ 上的多普勒频移序列（标量/逐样本/callable）。
                    这是**接收时实际叠加**的 f_d(t)；本函数逆运算消除它。
                    给 0 → 恒等（不补偿，诚实空态）。

    返回:
        补偿后的复样本，dtype 与输入一致。
    """
    iq = np.asarray(iq)
    n = iq.size
    if n == 0:
        return iq
    phase = doppler_phase_radians(doppler_hz, n, fs)
    out = iq * np.exp(-1j * phase)
    return out.astype(iq.dtype, copy=False)


def apply_doppler_shift(iq: np.ndarray, fs: float,
                        doppler_hz: DopplerSpec) -> np.ndarray:
    """正方向叠加多普勒：乘以 exp(+j·phase)。

    仅用于**合成/测试**——在干净基带上人为叠加多普勒频移，模拟接收链路。
    与 :func:`remove_doppler_shift` 成对，逆运算精确回原。真实链路里不需要它。
    """
    iq = np.asarray(iq)
    n = iq.size
    if n == 0:
        return iq
    phase = doppler_phase_radians(doppler_hz, n, fs)
    out = iq * np.exp(+1j * phase)
    return out.astype(iq.dtype, copy=False)


# --------------------------------------------------------------------------- #
# 有状态补偿级（接收链接入用）
# --------------------------------------------------------------------------- #
class DopplerCompensationError(RuntimeError):
    """补偿已开启但预测/应用失败。

    绝不静默退化为「0 补偿」：调用方必须把它当作真实失败报给用户，
    本块音频宁可跳过，也不要假装补偿过。
    """


class DopplerCompensationStage:
    """接收链里的有状态多普勒补偿级（NCO 去偏移）。

    与无状态的 :func:`remove_doppler_shift` 不同，本类跨块保存：

    * **相位累加器**：块边界不重置相位 → 不产生相位跳变/爆音；
    * **频移渐变**：目标切换/开关切换时用 ``ramp_s`` 线性过渡，瞬时频移
      连续变化，不会出现频跳；
    * **预测来源**：只从单一真相源（``doppler_link.DopplerLinkCompensator``）
      读取 ``doppler_hz``，本类**不做第二份轨道/多普勒数学**；
      为保持预测新鲜，每 ``source_refresh_s`` 采样调用一次源的 ``refresh()``。

    诚实语义：

    * 无目标/目标失效 → 补偿量回 0，状态里写「无目标，未补偿」及源原因；
    * 开启但预测源抛异常 / 返回非有限数 → 抛 :class:`DopplerCompensationError`；
    * 关闭后相位保持连续（残留常量相位旋转），频谱与幅度不受影响。

    Parameters
    ----------
    source : object, optional
        预测源。需提供 ``reading() -> dict``（或带 ``state`` 属性）与可选
        ``refresh()`` / ``set_enabled()`` / ``set_application()``。
    ramp_s : float
        目标/开关切换时的线性渐变时长（秒），默认 20 ms。
    source_refresh_s : float
        预测刷新间隔（秒，按样本数折算，不读墙钟），默认 0.5 s。
    """

    def __init__(self, source: Any = None, *, ramp_s: float = 0.020,
                 source_refresh_s: float = 0.5) -> None:
        if ramp_s < 0:
            raise ValueError("ramp_s 不能为负")
        if source_refresh_s < 0:
            raise ValueError("source_refresh_s 不能为负")
        self._source = source
        self._ramp_s = float(ramp_s)
        self._source_refresh_s = float(source_refresh_s)

        self._enabled = False
        self._phase = 0.0                 # NCO 相位（弧度，跨块连续）
        self._applied = 0.0               # 当前实际施加的补偿量（Hz）
        self._target: Optional[float] = None
        self._ramp_from = 0.0
        self._ramp_to = 0.0
        self._ramp_progress = 1.0
        self._samples_since_refresh = 0
        self._reason = "未启用多普勒补偿"
        self._last_error = ""
        self._last_reading: Dict[str, Any] = {}
        self._blocks = 0
        self._samples = 0

    # ------------------------------------------------------------------ 配置
    def bind(self, source: Any) -> None:
        """绑定/更换预测源（不重置相位，避免切换源时爆音）。"""
        self._source = source
        self._last_reading = {}
        self._samples_since_refresh = 0

    def set_enabled(self, on: bool) -> bool:
        """显式开关。返回是否接受（后端已接管补偿时拒绝，防双倍偏移）。"""
        on = bool(on)
        src = self._source
        if on:
            state = _source_state(src)
            if state.get("backend_comp_active"):
                self._enabled = False
                self._reason = ("后端（C++/基带）多普勒补偿已活动，"
                                "接收链补偿拒绝开启（防止双倍偏移）")
                return False
            self._enabled = True
            # 先声明应用点为基带，再开链路开关，避免调谐器同时预偏（双补偿）
            _call_optional(src, "set_application", "baseband")
            _call_optional(src, "set_enabled", True)
            # set_enabled 可能因「无目标」拒绝并把开关置 False；本级的显式开关
            # 仍保持打开，等目标出现后自动开始补偿（状态如实标注未补偿）。
            _call_optional(src, "set_application", "baseband")
            reading = self._read_source()
            self._last_reading = reading
            if _target_from_reading(reading, None) is None:
                self._reason = self._no_target_reason()
            else:
                self._reason = ""
            return True
        self._enabled = False
        _call_optional(src, "set_enabled", False)
        _call_optional(src, "set_application", "tuner")
        self._reason = "补偿关闭（用户关闭）"
        return True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def reset(self) -> None:
        """清空相位/渐变/读数（换源或重建接收链时调用）。"""
        self._phase = 0.0
        self._applied = 0.0
        self._target = None
        self._ramp_from = 0.0
        self._ramp_to = 0.0
        self._ramp_progress = 1.0
        self._samples_since_refresh = 0
        self._last_reading = {}
        self._last_error = ""
        self._blocks = 0
        self._samples = 0
        self._reason = "未启用多普勒补偿" if not self._enabled else self._reason

    # ------------------------------------------------------------------ 处理
    def process(self, iq: np.ndarray, fs: float) -> np.ndarray:
        """对一块复 IQ 做去多普勒；返回与输入同 dtype 的新数组。

        关闭且补偿量已回零时是**零开销直通**（不读预测源、不做乘性运算）。
        """
        x = np.asarray(iq)
        n = x.size
        fs = float(fs)
        if not np.isfinite(fs) or fs <= 0:
            raise ValueError(f"采样率必须是正有限数（收到 {fs!r}）")
        if n == 0:
            return x
        if not np.iscomplexobj(x):
            raise TypeError(
                "多普勒补偿要求复基带 IQ（complex64/complex128），"
                f"收到 dtype={x.dtype}")
        self._blocks += 1
        self._samples += n

        target: Optional[float] = None
        if self._enabled:
            self._maybe_refresh_source(fs)
            reading = self._read_source()
            self._last_reading = reading or {}
            target = _target_from_reading(reading, self)
            self._reason = "" if target is not None else self._no_target_reason()
        else:
            self._reason = "补偿关闭（用户关闭）"
        self._target = target

        applied_vec = self._applied_vector(n, fs, target)
        if not np.any(applied_vec):
            # 频移为 0：保留相位（相位连续，避免关闭瞬间的相位跳变）
            if self._phase == 0.0:
                return x
            return (x * np.exp(-1j * self._phase)).astype(x.dtype, copy=False)

        phase = self._phase + (2.0 * np.pi / fs) * np.cumsum(applied_vec)
        self._phase = float(phase[-1] % (2.0 * np.pi))
        out = x * np.exp(-1j * phase)
        return out.astype(x.dtype, copy=False)

    def status(self) -> Dict[str, Any]:
        """当前补偿量读数（界面/日志照抄；字段只增不改）。"""
        r = self._last_reading
        settling = self._ramp_progress < 1.0
        active = bool(self._enabled and self._target is not None
                      and abs(self._applied) > 1e-9)
        return {
            "enabled": self._enabled,
            "active": active,
            "applied_hz": float(self._applied),
            "target_doppler_hz": self._target,
            "settling": settling,
            "target_name": r.get("target_name", ""),
            "predicted_at_unix": r.get("predicted_at_unix"),
            "doppler_rate_hz_s": r.get("doppler_rate_hz_s"),
            "tle_tier": r.get("tle_tier", ""),
            "tle_age_days": r.get("tle_age_days"),
            "application": "baseband",
            "reason": self._reason,
            "error": self._last_error,
            "blocks": self._blocks,
            "samples": self._samples,
        }

    # ------------------------------------------------------------------ 内部
    def _maybe_refresh_source(self, fs: float) -> None:
        if self._source is None or self._source_refresh_s <= 0:
            return
        threshold = max(1, int(self._source_refresh_s * fs))
        if self._samples_since_refresh < threshold:
            return
        self._samples_since_refresh = 0
        try:
            # set_enabled(True) 内部先 refresh 再置开关：目标失效时源会自动
            # 关闭（valid=False），恢复可见后本拍自动重新启用并继续补偿。
            fn = getattr(self._source, "set_enabled", None)
            if callable(fn):
                fn(True)
                return
            fn = getattr(self._source, "refresh", None)
            if callable(fn):
                fn()
        except Exception as e:  # noqa: BLE001 - 预测失败必须报错，不许静默当 0
            msg = (f"多普勒补偿失败：刷新目标预测异常"
                   f"（{type(e).__name__}: {e}）")
            self._last_error = msg
            raise DopplerCompensationError(msg) from e

    def _read_source(self) -> Dict[str, Any]:
        if self._source is None:
            return {}
        try:
            reading = self._source.reading() if hasattr(self._source, "reading") \
                else _reading_from_state(getattr(self._source, "state", None))
            return dict(reading or {})
        except Exception as e:  # noqa: BLE001
            msg = (f"多普勒补偿失败：读取目标预测异常"
                   f"（{type(e).__name__}: {e}）")
            self._last_error = msg
            raise DopplerCompensationError(msg) from e

    def _no_target_reason(self) -> str:
        r = self._last_reading
        if r.get("backend_comp_active"):
            return ("后端多普勒补偿已接管，接收链补偿未施加"
                    "（防止双倍偏移）")
        if r.get("application") not in (None, "baseband"):
            return "调谐器侧补偿生效，接收链基带补偿未施加（防止双倍偏移）"
        if not r.get("target_name"):
            return "无目标，未补偿"
        detail = r.get("reason") or "目标失效"
        return f"无目标，未补偿（{detail}）"

    def _applied_vector(self, n: int, fs: float,
                        target: Optional[float]) -> np.ndarray:
        """生成本块逐样本施加频移向量（线性渐变，相位连续）。"""
        desired = 0.0 if target is None else float(target)
        if abs(desired - self._ramp_to) > 1e-9:
            # 目标变化：从当前实际值平滑过渡到新目标（不产生频跳）
            self._ramp_from = self._applied
            self._ramp_to = desired
            self._ramp_progress = 0.0

        ramp_n = max(1, int(round(self._ramp_s * fs)))
        if self._ramp_progress >= 1.0:
            vec = np.full(n, self._ramp_to, dtype=np.float64)
        else:
            k = np.arange(1, n + 1, dtype=np.float64)
            prog = np.minimum(1.0, self._ramp_progress + k / float(ramp_n))
            vec = self._ramp_from + (self._ramp_to - self._ramp_from) * prog
            self._ramp_progress = float(prog[-1])
        self._applied = float(vec[-1])
        return vec


def _call_optional(src: Any, name: str, *args: Any) -> Any:
    fn = getattr(src, name, None)
    if fn is None or not callable(fn):
        return None
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        logger.debug("多普勒源 %s 调用失败: %s", name, e)
        return None


def _reading_from_state(state: Any) -> Dict[str, Any]:
    if state is None:
        return {}
    if isinstance(state, dict):
        return dict(state)
    out: Dict[str, Any] = {}
    for name in ("target_name", "enabled", "valid", "doppler_hz",
                 "doppler_rate_hz_s", "reason", "predicted_at_unix",
                 "tle_tier", "tle_age_days", "backend_comp_active"):
        if hasattr(state, name):
            out[name] = getattr(state, name)
    return out


def _source_state(src: Any) -> Dict[str, Any]:
    if src is None:
        return {}
    if hasattr(src, "reading"):
        try:
            return dict(src.reading() or {})
        except Exception:
            return {}
    return _reading_from_state(getattr(src, "state", None))


def _target_from_reading(
    reading: Optional[Dict[str, Any]],
    stage: Optional[DopplerCompensationStage] = None,
) -> Optional[float]:
    """把预测读数翻译成目标频移；非法/无效返回 None，非有限数抛错。"""
    if not reading:
        return None
    if not reading.get("enabled"):
        return None
    # 单一施加点：读数声明应用点不是 baseband（例如调谐器已预偏）时，
    # 基带侧不再叠加，避免双倍补偿。
    app = reading.get("application")
    if app is not None and app != "baseband":
        return None
    if not reading.get("valid") or reading.get("doppler_hz") is None:
        return None
    try:
        dop = float(reading["doppler_hz"])
    except (TypeError, ValueError) as e:
        msg = f"多普勒补偿失败：预测值无法转成数值（{reading['doppler_hz']!r}）"
        if stage is not None:
            stage._last_error = msg
        raise DopplerCompensationError(msg) from e
    if not np.isfinite(dop):
        msg = f"多普勒补偿失败：预测值不是有限数（{dop!r}），拒绝按 0 补偿"
        if stage is not None:
            stage._last_error = msg
        raise DopplerCompensationError(msg)
    return dop

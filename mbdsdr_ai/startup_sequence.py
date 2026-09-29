# SPDX-License-Identifier: MIT
"""
MBDSDR AI - RTL-SDR 真机启动序列状态机
========================================

真机（Windows + RTL-SDR）启动时最常见的两类线上事故：

1. **漏步骤**：没先注册 DLL 目录就 ``import rtlsdr``，Py3.8+ 直接
   ``FileNotFoundError: Could not find module ...rtlsdr.dll``；
2. **顺序错**：没枚举就 open、没 connect 就 start_stream、没拿到音频设备就
   start_audio，导致一串模糊异常。

本模块把"接 RTL-SDR 后必须按顺序做的 8 步"固化为一个显式状态机：

    1. setup_drivers()          Windows DLL 加载（windows_setup），Linux 跳过
    2. enumerate_devices()       枚举设备（device_manager）
    3. select_device(index)      选定设备
    4. connect()                 打开设备（失败区分 占用 / 驱动 / USB 断开）
    5. configure(freq, sr, gain) 下发频率 / 采样率 / 增益（含范围校验）
    6. start_stream()             启动 IQ 流（读一小段验证数据流真的通）
    7. start_audio()             启动音频输出（无输出设备 -> 明确报错）
    8. start_receive_chain()     初始化 DSP 接收链

每一步都有显式状态（pending/running/done/failed）和可操作错误
（:mod:`mbdsdr_ai.error_handler`）。所有协作者都可注入，测试无需真机。

红线：
- 绝不造假数据；任何一步失败立即停下并返回结构化结果；
- 不修改既有共享文件，只通过鸭子类型调用 ``windows_setup`` /
  ``device_manager`` / ``AudioPlayer`` / ``ReceiveChain``。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from .error_handler import (
    DeviceBusyError,
    DeviceNotFoundError,
    DriverNotFoundError,
    FrequencyOutOfRangeError,
    NoAudioOutputError,
    SampleRateNotSupportedError,
    SDRUserError,
    USBDisconnectedError,
    classify_connect_failure,
)

logger = logging.getLogger(__name__)

#: RTL-SDR 典型可调频率范围（Hz），设备信息未声明范围时用它兜底
RTL_DEFAULT_MIN_FREQ_HZ = 24.0e6
RTL_DEFAULT_MAX_FREQ_HZ = 1.766e9

#: 启动步骤名（顺序敏感）
STEP_SETUP_DRIVERS = "setup_drivers"
STEP_ENUMERATE = "enumerate_devices"
STEP_SELECT = "select_device"
STEP_CONNECT = "connect"
STEP_CONFIGURE = "configure"
STEP_STREAM = "start_stream"
STEP_AUDIO = "start_audio"
STEP_CHAIN = "start_receive_chain"

STEP_ORDER: List[str] = [
    STEP_SETUP_DRIVERS,
    STEP_ENUMERATE,
    STEP_SELECT,
    STEP_CONNECT,
    STEP_CONFIGURE,
    STEP_STREAM,
    STEP_AUDIO,
    STEP_CHAIN,
]


# ----------------------------------------------------------------------
# 步骤状态
# ----------------------------------------------------------------------
@dataclass
class StepState:
    name: str
    status: str = "pending"  # pending / running / done / failed
    detail: str = ""
    error: Optional[SDRUserError] = None

    def done(self, detail: str = "") -> None:
        self.status = "done"
        self.detail = detail
        self.error = None

    def fail(self, error: SDRUserError, detail: str = "") -> None:
        self.status = "failed"
        self.error = error
        self.detail = detail or error.message


# ----------------------------------------------------------------------
# 音频输出设备枚举（sounddevice 可选依赖，缺了就降级为空列表）
# ----------------------------------------------------------------------
def list_audio_outputs(sd_module: Any = None) -> List[str]:
    """枚举系统音频输出设备名列表。无输出 / sounddevice 未装时返回 []。

    不抛异常：任何探测失败都视为"无输出设备"，由上层决定如何报错。
    """
    if sd_module is None:
        try:
            import sounddevice as sd  # type: ignore
            sd_module = sd
        except Exception:
            return []
    try:
        devices = sd_module.query_devices()
    except Exception:
        return []
    out: List[str] = []
    try:
        for i, d in enumerate(devices):
            if int(getattr(d, "max_output_channels", 0) or 0) > 0:
                out.append(f"[{i}] {getattr(d, 'name', f'device {i}')}")
    except Exception:
        return []
    return out


# ----------------------------------------------------------------------
# 启动序列
# ----------------------------------------------------------------------
class StartupSequence:
    """接 RTL-SDR 的正确启动顺序状态机。

    所有协作者都可注入（测试用 mock，真机用默认值）：

    Parameters
    ----------
    setup_drivers_fn : callable, optional
        返回 ``{"ok": bool, "message": str, ...}``；默认调
        :func:`mbdsdr_ai.windows_setup.setup_rtlsdr_windows`。
    enumerate_fn : callable, optional
        返回设备信息列表（元素需有 ``name/driver/serial/sample_rates`` 属性）；
        默认用 :class:`mbdsdr_ai.device_manager.DeviceManager`。
    backend_factory : callable, optional
        ``backend_factory(index, device_info) -> backend``；后端需满足鸭子协议：
        ``connect()/disconnect()/set_frequency/set_sample_rate/set_gain/
        read_samples/get_status()``。默认创建 ``RTLSDRBackend``。
    audio_player_factory : callable, optional
        ``audio_player_factory(sample_rate) -> player``；player 需有
        ``start() -> bool`` / ``stop()``。默认 :class:`mbdsdr_ai.audio_out.AudioPlayer`。
    chain_factory : callable, optional
        ``chain_factory(fs_in, mode) -> chain``；默认 :class:`mbdsdr_ai.receive_chain.ReceiveChain`。
    list_audio_outputs_fn : callable, optional
        返回音频输出设备名列表；默认 :func:`list_audio_outputs`。
    audio_sample_rate : int
        音频输出采样率，默认 48000。
    """

    def __init__(
        self,
        setup_drivers_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        enumerate_fn: Optional[Callable[[], List[Any]]] = None,
        backend_factory: Optional[Callable[[int, Any], Any]] = None,
        audio_player_factory: Optional[Callable[[int], Any]] = None,
        chain_factory: Optional[Callable[[float, str], Any]] = None,
        list_audio_outputs_fn: Optional[Callable[[], List[str]]] = None,
        audio_sample_rate: int = 48000,
    ) -> None:
        self._setup_drivers_fn = setup_drivers_fn or self._default_setup_drivers
        self._enumerate_fn = enumerate_fn or self._default_enumerate
        self._backend_factory = backend_factory or self._default_backend_factory
        self._audio_factory = audio_player_factory or self._default_audio_factory
        self._chain_factory = chain_factory or self._default_chain_factory
        self._list_audio = list_audio_outputs_fn or list_audio_outputs
        self._audio_sr = int(audio_sample_rate)

        self.steps: Dict[str, StepState] = {
            name: StepState(name=name) for name in STEP_ORDER
        }
        #: 成功跑完后产出的对象（后端 / 音频播放器 / 接收链）
        self.backend: Any = None
        self.player: Any = None
        self.chain: Any = None
        self.device_info: Any = None
        self.device_index: int = 0
        self.config: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 默认协作者（懒导入，避免无硬件环境 import 重型依赖）
    # ------------------------------------------------------------------
    @staticmethod
    def _default_setup_drivers() -> Dict[str, Any]:
        try:
            from .windows_setup import setup_rtlsdr_windows
        except Exception as exc:  # pragma: no cover - 包内一定有，兜底
            return {"ok": False, "message": f"windows_setup 导入失败: {exc}"}
        return setup_rtlsdr_windows()

    @staticmethod
    def _default_enumerate() -> List[Any]:
        try:
            from .device_manager import DeviceManager
        except Exception as exc:  # pragma: no cover
            logger.warning("DeviceManager 导入失败: %s", exc)
            return []
        try:
            return DeviceManager().list_devices()
        except Exception as exc:
            logger.warning("枚举设备失败: %s", exc)
            return []

    @staticmethod
    def _default_backend_factory(index: int, device_info: Any) -> Any:
        from .sdr_backend import RTLSDRBackend
        return RTLSDRBackend(device_index=index)

    @staticmethod
    def _default_audio_factory(sample_rate: int) -> Any:
        from .audio_out import AudioPlayer
        return AudioPlayer(sample_rate=sample_rate)

    @staticmethod
    def _default_chain_factory(fs_in: float, mode: str) -> Any:
        from .receive_chain import ReceiveChain
        return ReceiveChain(fs_in=fs_in, mode=mode)

    # ------------------------------------------------------------------
    # 各步骤
    # ------------------------------------------------------------------
    def setup_drivers(self) -> None:
        step = self.steps[STEP_SETUP_DRIVERS]
        step.status = "running"
        # Linux/macOS：本步本来就是 Windows 专属，跳过不算错
        if sys.platform != "win32":
            step.done(f"非 Windows 平台（{sys.platform}），跳过 DLL 注册")
            return
        try:
            res = self._setup_drivers_fn()
        except Exception as exc:
            step.fail(DriverNotFoundError(technical_detail=str(exc)))
            raise
        if not res.get("ok"):
            step.fail(
                DriverNotFoundError(technical_detail=str(res.get("message", ""))),
                detail=str(res.get("message", "")),
            )
            raise self.steps[STEP_SETUP_DRIVERS].error
        step.done(str(res.get("message", "驱动就绪")))

    def enumerate_devices(self) -> None:
        step = self.steps[STEP_ENUMERATE]
        step.status = "running"
        try:
            devices = list(self._enumerate_fn() or [])
        except Exception as exc:
            step.fail(DeviceNotFoundError(technical_detail=str(exc)))
            raise
        if not devices:
            step.fail(DeviceNotFoundError())
            raise step.error
        self._devices = devices  # type: ignore[attr-defined]
        step.done(f"发现 {len(devices)} 台设备: "
                  + ", ".join(str(getattr(d, "name", d)) for d in devices[:3]))

    def select_device(self, index: int = 0) -> None:
        step = self.steps[STEP_SELECT]
        step.status = "running"
        devices = getattr(self, "_devices", None) or []
        if not devices:
            step.fail(DeviceNotFoundError(message="尚未枚举到任何设备"))
            raise step.error
        if index < 0 or index >= len(devices):
            step.fail(
                DeviceNotFoundError(
                    message=f"设备序号 {index} 超出范围（共 {len(devices)} 台）",
                )
            )
            raise step.error
        self.device_index = index
        self.device_info = devices[index]
        step.done(f"已选择: {getattr(self.device_info, 'name', devices[index])}")

    def connect(self) -> None:
        step = self.steps[STEP_CONNECT]
        step.status = "running"
        backend = self._backend_factory(self.device_index, self.device_info)
        self.backend = backend
        exc: Optional[BaseException] = None
        ok = False
        try:
            ok = bool(backend.connect())
        except Exception as e:  # connect 内部一般吞异常，这里再兜一层
            exc = e
            ok = False
        if not ok:
            status_err = ""
            try:
                status_err = str(getattr(getattr(backend, "status", None), "error", ""))
            except Exception:
                pass
            err = classify_connect_failure(exc, status_err)
            step.fail(err, detail=status_err)
            raise err
        step.done("设备已连接")

    def configure(self, freq_hz: float, sample_rate_hz: float, gain_db: float) -> None:
        step = self.steps[STEP_CONFIGURE]
        step.status = "running"
        info = self.device_info

        # 1) 采样率范围校验
        supported = list(getattr(info, "sample_rates", None) or [])
        if supported:
            hit = any(abs(float(sr) - float(sample_rate_hz)) / float(sr) < 0.01
                      for sr in supported)
            if not hit:
                err = SampleRateNotSupportedError(
                    message=f"采样率 {int(round(sample_rate_hz))} Hz 不被支持",
                    supported_rates=supported,
                )
                step.fail(err)
                raise err

        # 2) 频率范围校验
        fmin, fmax = self._freq_bounds(info)
        if not (fmin <= float(freq_hz) <= fmax):
            err = FrequencyOutOfRangeError(
                message=f"频率 {freq_hz / 1e6:.3f} MHz 超出设备范围",
                min_freq=fmin,
                max_freq=fmax,
            )
            step.fail(err)
            raise err

        # 3) 下发
        be = self.backend
        try:
            if not bool(be.set_sample_rate(float(sample_rate_hz))):
                raise SampleRateNotSupportedError(
                    message=f"设备拒绝采样率 {int(round(sample_rate_hz))} Hz",
                    supported_rates=supported or None,
                )
            if not bool(be.set_frequency(float(freq_hz))):
                raise FrequencyOutOfRangeError(
                    message=f"设备拒绝频率 {freq_hz / 1e6:.3f} MHz",
                    min_freq=fmin, max_freq=fmax,
                )
            if not bool(be.set_gain(float(gain_db))):
                raise SDRUserError(
                    message=f"设备拒绝增益 {gain_db} dB",
                    suggestion="请改用 AGC 或选择支持的增益档位",
                )
        except SDRUserError:
            raise
        except Exception as e:
            err = SDRUserError(
                message="下发设备参数失败",
                technical_detail=str(e),
                suggestion="请重试；若反复失败，重新插拔 USB",
            )
            step.fail(err)
            raise err

        self.config = {
            "frequency_hz": float(freq_hz),
            "sample_rate_hz": float(sample_rate_hz),
            "gain_db": float(gain_db),
        }
        step.done(
            f"freq={freq_hz / 1e6:.3f}MHz sr={int(round(sample_rate_hz))}Hz "
            f"gain={gain_db}dB"
        )

    def start_stream(self, probe_samples: int = 1024) -> None:
        step = self.steps[STEP_STREAM]
        step.status = "running"
        try:
            iq = self.backend.read_samples(int(probe_samples))
        except Exception as exc:
            err = USBDisconnectedError(technical_detail=str(exc))
            step.fail(err)
            raise err
        if iq is None or len(iq) == 0:
            err = USBDisconnectedError(
                message="IQ 流未启动：设备未返回样点",
                technical_detail="read_samples() returned empty",
            )
            step.fail(err)
            raise err
        step.done(f"IQ 流已启动（{len(iq)} 样点探测成功）")

    def start_audio(self) -> None:
        step = self.steps[STEP_AUDIO]
        step.status = "running"
        try:
            outputs = list(self._list_audio() or [])
        except Exception:
            outputs = []
        if not outputs:
            err = NoAudioOutputError()
            step.fail(err)
            raise err
        player = self._audio_factory(self._audio_sr)
        self.player = player
        ok = False
        try:
            ok = bool(player.start())
        except Exception as exc:
            err = NoAudioOutputError(technical_detail=str(exc))
            step.fail(err)
            raise err
        if not ok:
            err = NoAudioOutputError(
                message="未找到音频输出设备，声卡无法启动",
                technical_detail="AudioPlayer.start() returned False",
            )
            step.fail(err)
            raise err
        step.done(f"音频输出已启动（{len(outputs)} 个输出设备可用）")

    def start_receive_chain(self, mode: str = "FM") -> None:
        step = self.steps[STEP_CHAIN]
        step.status = "running"
        fs_in = float(self.config.get("sample_rate_hz") or 2_048_000.0)
        try:
            self.chain = self._chain_factory(fs_in, mode)
        except Exception as exc:
            err = SDRUserError(
                message="DSP 接收链初始化失败",
                technical_detail=str(exc),
                suggestion="请重启程序；若反复出现，把日志提交给开发者",
            )
            step.fail(err)
            raise err
        step.done(f"接收链已初始化（mode={mode}, fs_in={int(fs_in)}Hz）")

    # ------------------------------------------------------------------
    # 一键跑完
    # ------------------------------------------------------------------
    def run_all(
        self,
        freq_hz: float,
        sample_rate_hz: float,
        gain_db: float,
        device_index: int = 0,
        mode: str = "FM",
    ) -> Dict[str, Any]:
        """按顺序跑完 8 步；任何一步失败立即停下，返回结构化结果。

        Returns
        -------
        dict
            ``{ok, failed_step, error, steps: {name: StepState}, ...}``。
        """
        try:
            self.setup_drivers()
            self.enumerate_devices()
            self.select_device(device_index)
            self.connect()
            self.configure(freq_hz, sample_rate_hz, gain_db)
            self.start_stream()
            self.start_audio()
            self.start_receive_chain(mode)
        except SDRUserError as exc:
            return {
                "ok": False,
                "failed_step": self._failed_step_name(),
                "error": exc,
                "steps": self.steps,
            }
        return {
            "ok": True,
            "failed_step": None,
            "error": None,
            "steps": self.steps,
        }

    def _failed_step_name(self) -> Optional[str]:
        for name in STEP_ORDER:
            if self.steps[name].status == "failed":
                return name
        return None

    # ------------------------------------------------------------------
    # 前置条件检查（不打开设备）
    # ------------------------------------------------------------------
    def validate(self) -> Dict[str, Any]:
        """检查所有前置条件，不真正打开设备。

        Returns
        -------
        dict
            ``{ok: bool, missing: [str], warnings: [str]}``。
            ``missing`` 是必须解决的问题（缺驱动 / 无设备 / 无音频输出）；
            ``warnings`` 是建议项。
        """
        missing: List[str] = []
        warnings: List[str] = []

        # 1) 驱动（Windows 才检查 DLL；Linux 跳过但不报错）
        if sys.platform == "win32":
            try:
                res = self._setup_drivers_fn()
                if not res.get("ok"):
                    missing.append(f"SDR 驱动: {res.get('message', 'rtlsdr.dll 缺失')}")
            except Exception as exc:
                missing.append(f"SDR 驱动探测异常: {exc}")
        else:
            warnings.append(f"非 Windows 平台（{sys.platform}）：跳过 DLL 注册检查")

        # 2) 设备枚举
        try:
            devices = list(self._enumerate_fn() or [])
        except Exception as exc:
            devices = []
            warnings.append(f"设备枚举异常: {exc}")
        if not devices:
            missing.append("SDR 设备: 未检测到 SDR 设备，请检查 USB 连接和驱动")

        # 3) 音频输出
        try:
            outputs = list(self._list_audio() or [])
        except Exception:
            outputs = []
        if not outputs:
            missing.append("音频输出: 未找到音频输出设备，请检查声卡")

        return {
            "ok": not missing,
            "missing": missing,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """停止音频 / 断开设备。可重复调用，绝不抛异常。"""
        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
        if self.backend is not None:
            try:
                self.backend.disconnect()
            except Exception:
                pass

    # ------------------------------------------------------------------
    @staticmethod
    def _freq_bounds(info: Any) -> tuple:
        fr = getattr(info, "frequency_range", None)
        if fr is not None and len(fr) >= 2:
            try:
                return float(fr[0]), float(fr[1])
            except Exception:
                pass
        return RTL_DEFAULT_MIN_FREQ_HZ, RTL_DEFAULT_MAX_FREQ_HZ


__all__ = [
    "StartupSequence",
    "StepState",
    "list_audio_outputs",
    "STEP_ORDER",
    "STEP_SETUP_DRIVERS",
    "STEP_ENUMERATE",
    "STEP_SELECT",
    "STEP_CONNECT",
    "STEP_CONFIGURE",
    "STEP_STREAM",
    "STEP_AUDIO",
    "STEP_CHAIN",
    "RTL_DEFAULT_MIN_FREQ_HZ",
    "RTL_DEFAULT_MAX_FREQ_HZ",
]

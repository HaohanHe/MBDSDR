# SPDX-License-Identifier: MIT
"""
MBDSDR AI - 音频输出设备枚举与选择
====================================

在真机上，用户常接多个音频输出设备（主板声卡、USB  DAC、蓝牙耳机）。
GQRX/SDR++ 都需要在设置里手动挑输出声卡。本模块用 ``sounddevice``
(PortAudio) 枚举系统输入/输出设备，并允许：

- ``list_output_devices()`` / ``list_input_devices()``：枚举可用设备；
- ``set_output_device(index)``：切换默认输出设备并重启 AudioPlayer；
- ``get_default_output()``：读取系统默认输出；
- ``test_device(index)``：播放 1 秒 1 kHz 测试音（幅度 0.1），验证设备可用；
- ``start_monitor(callback)``：后台轮询设备列表，热插拔时回调。

安全红线：
- sounddevice 未安装 / PortAudio 无设备时，枚举返回**空列表**，绝不造假设备；
- 所有 PortAudio 调用都包 try/except，无音频设备不崩溃。

测试友好：构造时可注入 ``sd_module``（mock sounddevice）与 ``player``，
测试音生成本身为纯 numpy 函数，单测无需真实声卡。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

# sounddevice 是可选依赖：导入失败时整体降级，不抛异常（与 audio_out.py 同款策略）
try:  # pragma: no cover - 取决于运行环境
    import sounddevice as sd  # type: ignore
    _SD_AVAILABLE = True
except Exception:  # pragma: no cover
    sd = None  # type: ignore
    _SD_AVAILABLE = False

logger = logging.getLogger(__name__)

#: 测试/显式禁用用的哨兵：区分"调用方没传 sd_module"（用全局 sd）与
#: "显式传 None"（明确表示无音频后端，枚举必须返回空）。
_SENTINEL = object()

#: 测试音默认参数：1 kHz、0.1 幅度、1 秒（任务要求）
TEST_TONE_FREQ = 1000.0
TEST_TONE_AMPLITUDE = 0.1
TEST_TONE_DURATION = 1.0
TEST_SAMPLE_RATE = 48000.0


def make_test_tone(
    sample_rate: float = TEST_SAMPLE_RATE,
    duration: float = TEST_TONE_DURATION,
    freq: float = TEST_TONE_FREQ,
    amplitude: float = TEST_TONE_AMPLITUDE,
) -> np.ndarray:
    """生成 1 秒 1 kHz 测试音（float32, 单声道）。纯函数，便于单测。"""
    n = max(1, int(round(sample_rate * duration)))
    t = np.arange(n, dtype=np.float64) / float(sample_rate)
    wave = amplitude * np.sin(2.0 * np.pi * freq * t)
    return wave.astype(np.float32)


def _device_to_dict(index: int, d: Any) -> Optional[Dict[str, Any]]:
    """把 sounddevice 设备描述 dict 规范化为我们的输出结构。

    返回 None 表示该设备项不合法/不可用（跳过）。
    """
    try:
        name = d.get("name", f"device-{index}")
        sr = float(d.get("default_samplerate", 0.0) or 0.0)
        out_ch = int(d.get("max_output_channels", 0) or 0)
        in_ch = int(d.get("max_input_channels", 0) or 0)
    except Exception:
        return None
    return {
        "index": int(index),
        "name": str(name),
        "default_samplerate": sr,
        "output_channels": out_ch,
        "input_channels": in_ch,
    }


class AudioDeviceManager:
    """枚举并选择 sounddevice 音频设备。

    Parameters
    ----------
    sd_module : module, optional
        注入的 sounddevice 模块（测试用）；默认用全局导入的 ``sd``。
    player : AudioPlayer, optional
        可选的 :class:`mbdsdr_ai.audio_out.AudioPlayer` 引用。
        ``set_output_device`` 切换后会 stop/start 它以应用新设备。
    """

    def __init__(self, sd_module: Any = _SENTINEL, player: Any = None) -> None:
        # 未传参 -> 用全局导入的 sd；显式传 None -> 明确无音频后端
        if sd_module is _SENTINEL:
            self._sd: Any = sd if _SD_AVAILABLE else None
        else:
            self._sd = sd_module
        self._player = player

        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop: Optional[threading.Event] = None
        self._last_signature: Optional[Set[Tuple]] = None

    @property
    def available(self) -> bool:
        """sounddevice 是否可用（False 时枚举一律返回空列表）。"""
        return self._sd is not None

    # ------------------------------------------------------------------
    # 枚举
    # ------------------------------------------------------------------
    def _query_all(self) -> List[Any]:
        if self._sd is None:
            return []
        try:
            devs = self._sd.query_devices()
        except Exception as exc:
            logger.warning("sounddevice.query_devices() 失败: %s", exc)
            return []
        try:
            return list(devs)
        except Exception:
            # DeviceList 可能不直接迭代；退回按索引取
            try:
                n = len(devs)
                return [devs[i] for i in range(n)]
            except Exception:
                return []

    def list_output_devices(self) -> List[Dict[str, Any]]:
        """枚举所有带输出通道的设备。无设备时返回空列表（不造假）。"""
        result: List[Dict[str, Any]] = []
        for i, d in enumerate(self._query_all()):
            info = _device_to_dict(i, d)
            if info is None:
                continue
            if info["output_channels"] > 0:
                result.append({
                    "index": info["index"],
                    "name": info["name"],
                    "default_samplerate": info["default_samplerate"],
                    "channels": info["output_channels"],
                })
        return result

    def list_input_devices(self) -> List[Dict[str, Any]]:
        """枚举所有带输入通道的设备。无设备时返回空列表（不造假）。"""
        result: List[Dict[str, Any]] = []
        for i, d in enumerate(self._query_all()):
            info = _device_to_dict(i, d)
            if info is None:
                continue
            if info["input_channels"] > 0:
                result.append({
                    "index": info["index"],
                    "name": info["name"],
                    "default_samplerate": info["default_samplerate"],
                    "channels": info["input_channels"],
                })
        return result

    # ------------------------------------------------------------------
    # 默认设备 / 选择
    # ------------------------------------------------------------------
    def get_default_output(self) -> Optional[Dict[str, Any]]:
        """返回系统默认输出设备信息；无默认/不可用时返回 None。"""
        if self._sd is None:
            return None
        try:
            default = self._sd.default.device  # (input_index, output_index)
            out_idx = default[1]
            if out_idx is None or int(out_idx) < 0:
                return None
            d = self._sd.query_devices(int(out_idx))
            info = _device_to_dict(int(out_idx), d)
            if info is None:
                return None
            return {
                "index": info["index"],
                "name": info["name"],
                "default_samplerate": info["default_samplerate"],
                "channels": info["output_channels"],
            }
        except Exception as exc:
            logger.warning("读取默认输出设备失败: %s", exc)
            return None

    def set_output_device(self, index: int) -> bool:
        """把默认输出设备切到 ``index``，并重启已挂载的 AudioPlayer。

        Returns
        -------
        bool
            是否成功设置。失败（sd 不可用/索引非法）返回 False，不抛异常。
        """
        if self._sd is None:
            return False
        try:
            current = self._sd.default.device  # (in, out)
            self._sd.default.device = (int(current[0]), int(index))
        except Exception as exc:
            logger.warning("设置默认输出设备=%s 失败: %s", index, exc)
            return False

        # 重启 AudioPlayer 以绑定新设备（若已挂载）
        if self._player is not None:
            try:
                self._player.stop()
                self._player.start()
            except Exception as exc:
                logger.warning("切换输出设备后重启 AudioPlayer 失败: %s", exc)
        return True

    # ------------------------------------------------------------------
    # 测试音
    # ------------------------------------------------------------------
    def _play_blocking(self, index: int, sample_rate: float, samples: np.ndarray) -> None:
        """用指定设备阻塞播放一段 float32 音频（真实 sounddevice 路径）。

        抽成方法便于测试时替换为 mock，不触发真实声卡。
        """
        stream = self._sd.OutputStream(
            samplerate=float(sample_rate),
            channels=1,
            device=int(index),
            dtype="float32",
        )
        stream.start()
        try:
            stream.write(np.ascontiguousarray(samples, dtype=np.float32).reshape(-1, 1))
        finally:
            try:
                stream.stop()
            finally:
                stream.close()

    def test_device(self, index: int) -> bool:
        """在 ``index`` 设备上播放 1 秒 1 kHz（0.1 幅度）测试音。

        Returns
        -------
        bool
            播放流程是否成功。设备不存在/打开失败/sd 不可用都返回 False。
        """
        if self._sd is None:
            return False
        try:
            d = self._sd.query_devices(int(index))
            sr = float(d.get("default_samplerate", TEST_SAMPLE_RATE) or TEST_SAMPLE_RATE)
            tone = make_test_tone(sample_rate=sr)
            self._play_blocking(int(index), sr, tone)
            return True
        except Exception as exc:
            logger.warning("测试音播放到设备 %s 失败: %s", index, exc)
            return False

    # ------------------------------------------------------------------
    # 设备热插拔监控
    # ------------------------------------------------------------------
    def _signature(self) -> Set[Tuple]:
        """当前设备列表的可比较指纹：{(name, in_ch, out_ch), ...}。"""
        sig: Set[Tuple] = set()
        for d in self._query_all():
            info = _device_to_dict(-1, d)
            if info is not None:
                sig.add((info["name"], info["input_channels"], info["output_channels"]))
        return sig

    def start_monitor(
        self,
        callback: Callable[[Set[Tuple], Set[Tuple]], None],
        interval: float = 1.0,
    ) -> bool:
        """后台轮询设备列表，变化时回调 ``callback(added, removed)``。

        Parameters
        ----------
        callback : callable
            ``(added: set, removed: set)``，元素为 ``(name, in_ch, out_ch)`` 元组。
            回调内部异常被吞掉，不影响监控线程。
        interval : float
            轮询间隔秒。

        Returns
        -------
        bool
            是否成功启动（sd 不可用返回 False）。
        """
        if self._sd is None:
            return False
        self.stop_monitor()
        self._monitor_stop = threading.Event()
        self._last_signature = self._signature()

        stop = self._monitor_stop

        def loop() -> None:
            while not stop.wait(max(0.1, float(interval))):
                try:
                    sig = self._signature()
                except Exception:
                    continue
                if sig == self._last_signature:
                    continue
                old = self._last_signature or set()
                added = sig - old
                removed = old - sig
                self._last_signature = sig
                try:
                    callback(added, removed)
                except Exception as exc:
                    logger.warning("设备热插拔回调异常: %s", exc)

        self._monitor_thread = threading.Thread(
            target=loop, name="audio-device-monitor", daemon=True
        )
        self._monitor_thread.start()
        return True

    def stop_monitor(self) -> None:
        """停止设备监控线程。可重复调用，安全。"""
        if self._monitor_stop is not None:
            self._monitor_stop.set()
        t = self._monitor_thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._monitor_thread = None
        self._monitor_stop = None


__all__ = [
    "AudioDeviceManager",
    "make_test_tone",
    "TEST_TONE_FREQ",
    "TEST_TONE_AMPLITUDE",
    "TEST_TONE_DURATION",
]

"""
MBDSDR AI - USB 拔出监控
============================

USB SDR 被物理拔出时，ctypes/libusb 后续读操作会抛异常（LIBUSB_ERROR_NO_DEVICE /
PipeException / 断开管道）。本模块用一个后台线程周期性"探活"，在拔出瞬间：

- 立即停止读取线程、停止音频，避免回调线程在已失效的设备上反复抛错；
- 触发 ``on_device_removed`` 回调（通常交给 ReconnectionManager 开始重试）；
- 设备重新插回时触发 ``on_device_added``。

跨平台策略：
- Windows：无 udev，只能轮询（本模块默认路径）；
- Linux：可监听 udev（可选增强，本模块不强制依赖 pyudev；未安装时退回轮询）。

健壮性红线：
- 所有读/探活操作都包 try/except；设备拔出时**优雅降级、绝不崩溃**；
- 探活失败一律视为"设备可能已拔出"，不区分具体错误码（驱动差异太大）。

与 ReconnectionManager 的配合（典型接法）::

    usb = USBWatchdog(backend, on_device_removed=lambda: recon.start(backend),
                      stop_reading=reader.stop, stop_audio=player.stop)
    usb.start()
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 2.0


def _safe_callback(fn: Optional[Callable[..., None]], *args: Any) -> None:
    if fn is None:
        return
    try:
        fn(*args)
    except Exception as exc:
        logger.warning("USB 看门狗回调异常: %s", exc)


class USBWatchdog:
    """周期性探活后端，检测 USB 设备拔出/插回。

    Parameters
    ----------
    backend : object
        待监控的 SDR 后端。
    interval : float
        探活周期秒（Windows 轮询，默认 2s）。
    on_device_removed : callable, optional
        检测到设备拔出时调用（无参）。
    on_device_added : callable, optional
        检测到设备重新插回时调用（无参）。
    stop_reading : callable, optional
        拔出时停止读取线程（无参）。
    stop_audio : callable, optional
        拔出时停止音频输出（无参）。
    alive_fn : callable, optional
        自定义探活函数 ``alive_fn(backend) -> bool``；不传则按下面顺序自动探测：
        ``backend.is_alive()`` -> ``backend.read_rx(64)`` 能否返回非空 ->
        ``backend.get_status().connected``。
    """

    def __init__(
        self,
        backend: Any,
        interval: float = DEFAULT_INTERVAL,
        on_device_removed: Optional[Callable[[], None]] = None,
        on_device_added: Optional[Callable[[], None]] = None,
        stop_reading: Optional[Callable[[], None]] = None,
        stop_audio: Optional[Callable[[], None]] = None,
        alive_fn: Optional[Callable[[Any], bool]] = None,
    ) -> None:
        self._backend = backend
        self._interval = max(0.1, float(interval))
        self.on_device_removed = on_device_removed
        self.on_device_added = on_device_added
        self._stop_reading = stop_reading
        self._stop_audio = stop_audio
        self._alive_fn = alive_fn

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # 启动时假定设备在位；拔出->插回才触发事件
        self._was_alive = True
        self._removed_once = False

    # ------------------------------------------------------------------
    # 探活（全部容错，绝不向外抛）
    # ------------------------------------------------------------------
    def check_alive(self) -> bool:
        """探测后端是否还活着。任何异常都返回 False（视为可能已拔出）。"""
        backend = self._backend
        if backend is None:
            return False
        try:
            if self._alive_fn is not None:
                return bool(self._alive_fn(backend))
        except Exception as exc:
            logger.debug("自定义探活异常: %s", exc)
            return False

        # 1) 后端自带 is_alive
        try:
            if hasattr(backend, "is_alive"):
                return bool(backend.is_alive())
        except Exception as exc:
            logger.debug("is_alive() 异常（视为拔出）: %s", exc)
            return False

        # 2) 尝试读一小段样点；拔出时这里会抛
        try:
            if hasattr(backend, "read_rx"):
                samples = backend.read_rx(64)
                return samples is not None and len(samples) > 0
        except Exception as exc:
            logger.info("read_rx 探活失败（设备可能已拔出）: %s", exc)
            return False

        # 3) 退回 get_status().connected
        try:
            status = backend.get_status()
            return bool(getattr(status, "connected", False))
        except Exception as exc:
            logger.debug("get_status() 异常: %s", exc)
            return False

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            alive = True
            try:
                alive = self.check_alive()
            except Exception as exc:  # 双保险：探活本身也包一层
                logger.warning("探活循环异常，按拔出处理: %s", exc)
                alive = False

            if alive and not self._was_alive:
                # 插回
                self._was_alive = True
                _safe_callback(self.on_device_added)
            elif not alive and self._was_alive:
                # 拔出：优雅停止读线程与音频，再上报
                self._was_alive = False
                self._removed_once = True
                _safe_callback(self._stop_reading)
                _safe_callback(self._stop_audio)
                _safe_callback(self.on_device_removed)

            if self._stop.wait(self._interval):
                break

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动看门狗线程。"""
        self.stop()
        self._stop = threading.Event()
        self._was_alive = True
        self._removed_once = False
        self._thread = threading.Thread(
            target=self._run, name="usb-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """停止看门狗。可重复调用，安全。"""
        if self._stop is not None:
            self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_alive(self) -> bool:
        return self._was_alive

    @property
    def platform(self) -> str:
        """当前平台标识（Windows 轮询；Linux 可选 udev）。"""
        return sys.platform


#: Linux udev 实时监控为可选增强：未装 pyudev 时上面的轮询路径已足够。
#: 保留这个常量供上层查询能力，不强制实现线程。
UDEV_SUPPORTED = sys.platform.startswith("linux")


__all__ = [
    "USBWatchdog",
    "DEFAULT_INTERVAL",
    "UDEV_SUPPORTED",
]

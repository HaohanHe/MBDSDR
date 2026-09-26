"""
MBDSDR 桌面端运行参数持久化
============================

把上次退出时的 SDR 运行参数（频率/增益/解调模式/带宽/采样率/呼号/GNSS 串口/
AGC/主题）以**明文 JSON** 落到磁盘，下次启动读回。设计取舍：

- 不用 QSettings：用户要求明文 JSON，方便跨平台直接查看/备份/手改。
- 路径遵循平台规范：Linux/macOS 用 ``~/.config/mbdsdr/desktop_settings.json``，
  Windows 用 ``%APPDATA%/mbdsdr/desktop_settings.json``。
- 防抖写盘：``set()`` 只在内存里改值并重启一个 500ms 的 QTimer，连续拖动滑杆
  只落最后一次，避免每个像素都写盘。QTimer 在主线程事件循环里触发写盘，
  与 ``set()`` 同源（都在 UI 主线程），无需加锁。
- 退出时 ``flush()`` 立即落盘，保证关机前不丢。
- 文件缺失/损坏/权限错误一律回退默认值，绝不抛异常拖垮 GUI。
"""

import json
import os
import platform
from typing import Any, Dict

from PySide6.QtCore import QTimer


#: 全部可持久化字段及其默认值。新字段先在这里登记，load/set 自动校验。
DEFAULTS: Dict[str, Any] = {
    "frequency_hz": 98_000_000.0,   # 中心频率 Hz
    "gain_db": 20.0,                # LNA 增益 dB
    "demod_mode": "FM",             # 解调模式：FM/WFM/AM/USB/LSB/CW
    "bandwidth_hz": 0.0,            # VFO 带宽 Hz；0 = 随模式自动
    "sample_rate_hz": 2_048_000.0,  # 采样率 Hz
    "callsign": "",                 # 操作员呼号；空 = 未设置
    "gnss_port": "",                # GNSS 串口设备路径；空 = 自动探测
    "gnss_baudrate": 9600,          # GNSS 串口波特率
    "agc_enabled": True,            # 自动增益开关
    "theme": "default",             # 主题名
}

#: 数值字段（写盘前做类型归一，防止 JSON 里混入字符串导致下游类型错误）
_FLOAT_KEYS = ("frequency_hz", "gain_db", "bandwidth_hz", "sample_rate_hz")
_INT_KEYS = ("gnss_baudrate",)
_BOOL_KEYS = ("agc_enabled",)


def default_config_path() -> str:
    """按平台返回 desktop_settings.json 的绝对路径。

    - Windows：``%APPDATA%/mbdsdr/desktop_settings.json``（expandvars 展开）。
    - Linux/macOS：``~/.config/mbdsdr/desktop_settings.json``。
    """
    if platform.system() == "Windows":
        base = os.path.expandvars("%APPDATA%")
        if not base or base.startswith("%"):  # expandvars 失败时兜底
            base = os.path.expanduser("~")
        return os.path.join(base, "mbdsdr", "desktop_settings.json")
    return os.path.join(os.path.expanduser("~"), ".config",
                        "mbdsdr", "desktop_settings.json")


def _coerce(key: str, value: Any) -> Any:
    """按字段类型把读回的 JSON 值归一；失败时退回默认值。"""
    try:
        if key in _FLOAT_KEYS:
            return float(value)
        if key in _INT_KEYS:
            return int(value)
        if key in _BOOL_KEYS:
            return bool(value)
        return str(value)
    except (TypeError, ValueError):
        return DEFAULTS.get(key, value)


class DesktopSettings:
    """轻量 JSON 配置：运行参数持久化（防抖写盘 + 退出 flush）。

    典型用法::

        settings = DesktopSettings.load()      # 启动：读回上次参数
        settings.set("frequency_hz", 100e6)    # 改值：标记 dirty，500ms 后落盘
        freq = settings.get("frequency_hz")    # 读
        settings.flush()                        # 退出：立即落盘
    """

    DEFAULTS = DEFAULTS

    def __init__(self, path: "str | None" = None, parent: "QObject | None" = None):  # noqa: F821
        self._path = path or default_config_path()
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self._dirty = False
        # 防抖写盘定时器：singleShot 语义，每次 set() 都重启，连续改动只写最后一次。
        # 挂在主线程（QTimer 必须在创建它的线程里 start），与 set() 同源，无需加锁。
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._write_to_disk)

    # ------------------------------------------------------------------
    # 构造/加载
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: "str | None" = None,
             parent: "QObject | None" = None) -> "DesktopSettings":  # noqa: F821
        """从 JSON 读回配置；文件不存在或损坏时返回默认值（不崩）。"""
        inst = cls(path=path, parent=parent)
        try:
            if os.path.exists(inst._path):
                with open(inst._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if k in DEFAULTS:
                            inst._data[k] = _coerce(k, v)
        except Exception:
            # 文件损坏/不可读：静默回退全默认，绝不让配置文件搞挂 GUI
            inst._data = dict(DEFAULTS)
        return inst

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        """读取一个字段；未知 key 返回 default（缺省再退到 DEFAULTS）。"""
        if key in self._data:
            return self._data[key]
        if default is not None:
            return default
        return DEFAULTS.get(key)

    def set(self, key: str, value: Any) -> None:
        """写一个字段并标记 dirty，防抖 500ms 后落盘。未知 key 忽略。"""
        if key not in DEFAULTS:
            return
        self._data[key] = _coerce(key, value)
        self._dirty = True
        try:
            self._timer.start()  # 重启 500ms 防抖窗口
        except RuntimeError:
            # timer 已销毁（退出中）：直接同步写一次，不丢最后一次改动
            self._write_to_disk()

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------
    def flush(self) -> None:
        """立即把脏数据落盘（程序退出时调用）。"""
        try:
            self._timer.stop()
        except RuntimeError:
            pass
        if self._dirty:
            self._write_to_disk()

    def _write_to_disk(self) -> None:
        """实际写盘：先写 .tmp 再 os.replace，避免半截文件损坏 JSON。"""
        try:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp_path = self._path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._path)
            self._dirty = False
        except Exception:
            # 磁盘满/权限不足/只读：不抛，下次 set 再试；退出 flush 也不崩
            pass

    # ------------------------------------------------------------------
    # 上下文管理器：with DesktopSettings.load() as s: ... 退出自动 flush
    # ------------------------------------------------------------------
    def __enter__(self) -> "DesktopSettings":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.flush()

    # 便于调试/查看
    def __repr__(self) -> str:  # pragma: no cover
        return f"DesktopSettings(path={self._path!r}, dirty={self._dirty})"

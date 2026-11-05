# SPDX-License-Identifier: MIT
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
import math
import os
import platform
import re
import stat
import sys
from datetime import datetime
from typing import Any, Dict, Optional

from PySide6.QtCore import QTimer

try:  # 包内导入
    from config_schema import SCHEMA, defaults, validate_value
except ImportError:  # pragma: no cover - 兼容 desktop 目录直接上 sys.path
    from .config_schema import SCHEMA, defaults, validate_value


#: 全部可持久化字段及其默认值——**唯一来源是 config_schema.py**。
#: 这里只做派生，不再手写第二份默认值（防 UI/配置层默认值漂移）。
DEFAULTS: Dict[str, Any] = defaults("settings")

#: 类型分组由 schema.kind 派生，避免手写清单漏登记（漏登记会被 _coerce 落成
#: 字符串，下游 int() 再炸一次）。
_FLOAT_KEYS = tuple(k for k, it in SCHEMA.items()
                    if it.storage == "settings" and it.kind == "float")
_INT_KEYS = tuple(k for k, it in SCHEMA.items()
                  if it.storage == "settings" and it.kind == "int")
_BOOL_KEYS = tuple(k for k, it in SCHEMA.items()
                   if it.storage == "settings" and it.kind == "bool")


# ----------------------------------------------------------------------
# 音量量纲换算（唯一换算点）：UI/持久化统一 0–100 人类量纲；硬件面
# （AISDR Mini 的 SI4732 音量寄存器 / 旧 set_volume {"volume"} 协议）是
# 0–63 寄存器量纲。换算只发生在这两个纯函数里，UI 层不许再散写公式。
# ----------------------------------------------------------------------
def volume_pct_to_hw(pct: float) -> int:
    """0–100 人类量纲 → 0–63 硬件寄存器量纲（下发边界用）。"""
    return int(round(max(0, min(100, float(pct))) * 63 / 100.0))


def volume_hw_to_pct(hw: float) -> int:
    """0–63 硬件寄存器量纲 → 0–100 人类量纲（读回/迁移用）。"""
    return int(round(max(0, min(63, float(hw))) * 100 / 63.0))


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


def project_root() -> str:
    """仓库/程序根目录（desktop/ 的上一级）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: 受保护安装根目录：普通用户默认写不进去（Program Files / Windows / ProgramData）。
_PROTECTED_INSTALL_ROOT = re.compile(
    r"^[A-Za-z]:\\(?:Program Files(?: \(x86\))?|Windows|ProgramData)(?:\\|$)",
    re.IGNORECASE)


def _dir_writable(directory: str) -> bool:
    """启动期可写性判断（**不做真实写入探测**）。

    不用 ``tempfile.mkstemp`` 试写：在 ACL 显式 deny-write 的目录上，
    Windows 的创建文件调用会卡住数十秒（实测 faulthandler 停在
    ``tempfile._mkstemp_inner``），启动期不能冒这个风险。
    这里只看「已存在目录的只读属性」+「是否位于受保护安装根」。
    ACL 级只读（非 Program Files 的自定义目录）由 ensure_record_dir
    在真正写盘时给出可行动错误，而不是在启动期猜。
    """
    parent = directory if os.path.isdir(directory) else os.path.dirname(directory)
    if not os.path.isdir(parent):
        return False
    if _PROTECTED_INSTALL_ROOT.match(os.path.abspath(parent)):
        return False
    try:
        mode = os.stat(parent).st_mode
    except OSError:
        return False
    return bool(mode & stat.S_IWRITE)


def _first_writable_record_dir(primary: str) -> str:
    """默认目录不可写时的回退顺序：非 C 盘 → %LOCALAPPDATA% → 家目录。"""
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.isdir(root):
            candidate = os.path.join(root, "mbdsdr", "recordings")
            if _dir_writable(candidate):
                return candidate
    appdata = os.path.expandvars("%LOCALAPPDATA%")
    if appdata and not appdata.startswith("%"):
        candidate = os.path.join(appdata, "MBDSDR", "recordings")
        if _dir_writable(candidate):
            return candidate
    candidate = os.path.join(os.path.expanduser("~"), ".mbdsdr", "recordings")
    if _dir_writable(candidate):
        return candidate
    return primary


#: 默认录音目录按程序根目录缓存：安装目录只读时不必每次调用都做写探测。
_DEFAULT_RECORD_DIR_CACHE: Dict[str, str] = {}


def default_record_dir() -> str:
    """录音目录默认值：程序根目录的**同级** ``recordings``。

    本机项目在 ``D:\\mbdsdr\\MBDSDR``，故默认是 ``D:\\mbdsdr\\recordings``——
    刻意不写 ``C:\\Users\\...`` 用户目录（该目录只放少量配置，不堆大文件）。

    例外：程序装在 ``C:\\Program Files`` 等只读目录、且机器上没有非 C 盘时，
    同级 ``recordings`` 永远写不进去。此时（且仅在用户**没手选**目录时）
    回退到第一个可写位置，避免「一装好就默认不能录音」。
    """
    primary = os.path.join(os.path.dirname(project_root()), "recordings")
    cached = _DEFAULT_RECORD_DIR_CACHE.get(primary)
    if cached:
        return cached
    chosen = primary
    if not _dir_writable(primary):
        chosen = _first_writable_record_dir(primary)
        if chosen != primary:
            print(f"[settings] 程序目录不可写，录音默认目录回退到 {chosen}",
                  file=sys.stderr)
    _DEFAULT_RECORD_DIR_CACHE[primary] = chosen
    return chosen


def resolve_record_dir(value: Any) -> str:
    """把设置里的录音目录归一为绝对路径；留空/None → :func:`default_record_dir`。"""
    raw = "" if value is None else str(value).strip()
    if raw:
        return os.path.expanduser(os.path.expandvars(raw))
    return default_record_dir()


# ----------------------------------------------------------------------
# 过境筛选读取入口（A1 / A2 / A9）
# ----------------------------------------------------------------------
#: 消费点在卫星/星空线（`new_spacetime_panel.refresh_passes` /
#: `rf_sky_view._refresh_passes`）。两条线**只调用下面四个函数**，不各自读 JSON，
#: 保证「时空面板列表」与「天空图过境列表」永远同一组门槛/窗口。
#: 默认/范围优先取 config_schema（进 schema 后自动以 schema 为准）。
_A1_ITEM = SCHEMA.get("pass_min_elevation_deg")
_A2_ITEM = SCHEMA.get("pass_window_hours")

PASS_MIN_ELEVATION_DEFAULT_DEG = float(_A1_ITEM.default) if _A1_ITEM else 10.0
PASS_WINDOW_HOURS_DEFAULT = int(_A2_ITEM.default) if _A2_ITEM else 24
PASS_MIN_ELEVATION_RANGE_DEG = (
    (float(_A1_ITEM.minimum), float(_A1_ITEM.maximum)) if _A1_ITEM else (0.0, 45.0))
PASS_WINDOW_RANGE_HOURS = (
    (int(_A2_ITEM.minimum), int(_A2_ITEM.maximum)) if _A2_ITEM else (1, 72))

#: 「低仰角过境」的物理下限：低于 5° 基本被地形/大气挡住，再低没有观测意义。
PASS_LOW_ELEVATION_FLOOR_DEG = 5.0


def _read_setting(settings: Any, key: str, default: Any) -> Any:
    """从 DesktopSettings / dict / None 读一个键；缺值或坏容器一律回 default。"""
    if settings is None:
        return default
    getter = getattr(settings, "get", None)
    if callable(getter):
        try:
            value = getter(key, default)
        except TypeError:          # 只收一个参数的旧 get()
            value = getter(key)
        return default if value is None else value
    if isinstance(settings, dict):
        value = settings.get(key, default)
        return default if value is None else value
    return default


def effective_pass_min_elevation_deg(settings: Any = None) -> float:
    """A1：过境列表要求的峰值仰角下限（度），越界夹到 0–45。"""
    low, high = PASS_MIN_ELEVATION_RANGE_DEG
    try:
        value = float(_read_setting(settings, "pass_min_elevation_deg",
                                    PASS_MIN_ELEVATION_DEFAULT_DEG))
    except (TypeError, ValueError):
        return PASS_MIN_ELEVATION_DEFAULT_DEG
    if not math.isfinite(value):
        return PASS_MIN_ELEVATION_DEFAULT_DEG
    return max(low, min(high, value))


def effective_pass_window_hours(settings: Any = None) -> int:
    """A2：向未来搜索过境的小时数，越界夹到 1–72。"""
    low, high = PASS_WINDOW_RANGE_HOURS
    try:
        value = int(_read_setting(settings, "pass_window_hours",
                                  PASS_WINDOW_HOURS_DEFAULT))
    except (TypeError, ValueError):
        return PASS_WINDOW_HOURS_DEFAULT
    return max(low, min(high, value))


def effective_show_low_elevation_passes(settings: Any = None) -> bool:
    """A9：是否把 5°–A1 的低仰角过境也列出来（默认否）。"""
    value = _read_setting(settings, "show_low_elevation_passes", False)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def effective_pass_visibility_gate_deg(settings: Any = None) -> float:
    """`compute_passes` 实际该用的峰值仰角门。

    A9 关：等于 A1；A9 开：降到 ``min(A1, 5°)``（除非用户把 A1 设得比 5° 还低）。
    """
    gate = effective_pass_min_elevation_deg(settings)
    if effective_show_low_elevation_passes(settings):
        return min(gate, PASS_LOW_ELEVATION_FLOOR_DEG)
    return gate


def _coerce(key: str, value: Any, notes: Optional[list] = None) -> Any:
    """按 schema 把读回的 JSON 值归一；越界夹范围、坏值回默认。

    ``validate_value`` 本身是纯函数且已全类型兜底（永不抛），这里不再套
    catch-all——配置层最忌"静默吞编程错误"，多一层兜底只会掩盖 schema 缺陷。

    未知键保持旧行为（原样返回），避免把调用方的临时值意外转成字符串。
    传入 ``notes`` 时把回落原因记进去（旧配置里的非法档位不许静默消失）。
    """
    item = SCHEMA.get(key)
    if item is None:
        return value
    norm, reason = validate_value(item, value)
    if reason and notes is not None:
        notes.append(f"{key}: {reason}")
    return norm


def backup_corrupt_config(path: str) -> Optional[str]:
    """把损坏的配置文件改名为 ``*.corrupt-时间戳``，返回备份路径（失败 None）。

    与 gui_config 既有机制同一套命名（main_window.py:7674-7685），复用它而不是
    另起一套；先留证据再回默认值，绝不静默覆盖用户原文件。
    只捕获文件系统边界（OSError）；编程错误照常抛出。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.corrupt-{stamp}"
    try:
        os.replace(path, backup)
        return backup
    except OSError:
        return None


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
        #: 上次 load 时若原文件损坏，这里是备份路径（None = 未损坏）
        self.corrupt_backup: Optional[str] = None
        #: 最近一次读/写失败的用户可读说明（None = 无错误）。
        #: 设置面板订阅 on_error 后能在状态栏显示"改了没保存"这类真相。
        self.last_error: Optional[str] = None
        #: 写盘失败回调（签名 (message) -> None）；由 UI 注入，避免配置层依赖 Qt。
        self.on_error: Optional[Any] = None
        #: 上次文件里**真实出现**的键（用于一次性迁移提示：缺键 ≠ 用户选择了默认值）
        self._file_keys: set = set()
        #: 启动读回时因越界/非法档位而回落的字段及原因（不静默丢改动）
        self.migration_notes: Optional[list] = None
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
                if not isinstance(raw, dict):
                    raise ValueError("顶层不是 JSON 对象")
                inst._file_keys = set(raw)
                inst.migration_notes = []
                for k, v in raw.items():
                    if k in DEFAULTS:
                        inst._data[k] = _coerce(k, v, inst.migration_notes)
                # 音量量纲一次性迁移（0–63 硬件量纲 → 0–100 人类量纲）：
                # 旧文件有 volume 但没有 volume_scale_v2 标记。判据是
                # 「原始文件里的键」，不靠数值猜量纲——48 在两套量纲下
                # 都合法。迁移后随下次落盘写入标记，幂等。
                if "volume" in raw and "volume_scale_v2" not in raw:
                    try:
                        old = int(raw.get("volume", 30))
                    except (TypeError, ValueError):
                        old = 30
                    inst._data["volume"] = volume_hw_to_pct(old)
                    inst._data["volume_scale_v2"] = True
        except (OSError, ValueError, TypeError) as exc:
            # 文件损坏/不可读：先备份证据（*.corrupt-时间戳）再回默认值。
            # 必须留用户可见的错误说明——"读失败静默给默认值"会让用户以为
            # 自己上次保存的参数还在，实际已被默认值顶掉。
            inst.corrupt_backup = backup_corrupt_config(inst._path)
            inst.last_error = (
                f"配置读取失败（{exc.__class__.__name__}）：{inst._path}；"
                f"原文件已备份为 {inst.corrupt_backup or '（备份失败）'}，"
                "本次以默认值启动")
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

    def reset(self, key: str) -> Any:
        """把单项恢复到 schema 默认值；返回恢复后的值（未知 key 返回 None）。"""
        if key not in DEFAULTS:
            return None
        self.set(key, DEFAULTS[key])
        return self._data[key]

    def reset_all(self) -> None:
        """恢复全部默认值（一次落盘）。"""
        self._data = dict(DEFAULTS)
        self._dirty = True
        try:
            self._timer.start()
        except RuntimeError:
            self._write_to_disk()

    def values(self) -> Dict[str, Any]:
        """返回当前全部设置的浅拷贝（供导出/审计）。"""
        return dict(self._data)

    def has_file_key(self, key: str) -> bool:
        """该键是否**真实存在于上次磁盘文件**（区分"用户选了默认值"与"从未有过"）。

        用于一次性迁移提示：旧文件没有新键时，用户可以被告知默认值变了；
        文件里已有该键则说明用户已表达过意图，不再提示。
        """
        return key in self._file_keys

    @property
    def path(self) -> str:
        return self._path

    @property
    def record_dir(self) -> str:
        """录音目录的**公开读取入口**（消费方照此取，禁止再写死路径）。

        设置里留空时返回默认位置（程序目录同级 ``recordings``）；是否创建目录
        由消费方在真正要写文件时调用 :meth:`ensure_record_dir` 决定。
        """
        return resolve_record_dir(self.get("record_dir", ""))

    def ensure_record_dir(self) -> "tuple[str, str | None]":  # noqa: F821
        """确保录音目录存在；返回 ``(路径, 错误说明)``，错误为 None 表示可用。

        失败（权限/磁盘/非法路径）不抛异常、不静默：调用方必须把错误说明
        显示给用户，而不是假装录音还能继续。
        """
        directory = self.record_dir
        try:
            os.makedirs(directory, exist_ok=True)
            return directory, None
        except OSError as exc:
            return directory, (
                f"录音目录不可用（{directory}）：{exc}。"
                "请检查该磁盘/网盘是否已连接，或在「设置 → 存储与日志」"
                "重新选择目录。")

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
            self.last_error = None
        except (OSError, TypeError, ValueError) as exc:
            # 磁盘满/权限不足/只读/不可序列化：不抛（退出 flush 也不崩），
            # 但必须让用户看见——"改设置写失败静默"等于下次开机改动凭空消失。
            self.last_error = f"设置保存失败（{exc.__class__.__name__}）：{exc}"
            if callable(self.on_error):
                self.on_error(self.last_error)

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

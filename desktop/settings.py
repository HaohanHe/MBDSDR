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
import logging
import math
import os
import platform
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, Optional

from PySide6.QtCore import QTimer

try:  # 包内导入
    from config_schema import SCHEMA, defaults, validate_value
except ImportError:  # pragma: no cover - 兼容 desktop 目录直接上 sys.path
    from .config_schema import SCHEMA, defaults, validate_value


#: 全部可持久化字段及其默认值——**唯一来源是 config_schema.py**。
#: 这里只做派生，不再手写第二份默认值（防 UI/配置层默认值漂移）。
DEFAULTS: Dict[str, Any] = defaults("settings")

logger = logging.getLogger(__name__)

#: 区分"磁盘上没有这个键"与"键的值是 None"（JSON 里 None 是合法值）。
_MISSING = object()

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


def program_install_dir() -> str:
    """程序安装根：冻结版 = 含 exe 的 ``<dist>/MBDSDR``；源码直跑 = 仓库根。

    PyInstaller onedir 下 ``sys._MEIPASS = <dist>/MBDSDR/_internal``，
    :func:`project_root` 正好就是它，上一级才是「用户会整个删掉」的目录。
    """
    root = project_root()
    if getattr(sys, "frozen", False):
        return os.path.dirname(root)
    return root


def _is_under(path: str, root: str) -> bool:
    try:
        p = os.path.normcase(os.path.abspath(path))
        r = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([p, r]) == r
    except ValueError:  # Windows 跨盘符
        return False


def record_dir_inside_program(directory: str) -> bool:
    """该目录是否位于程序目录内（删/升级程序目录会连录音一起删）。"""
    return _is_under(directory, program_install_dir())


def record_dir_needs_confirmation(directory: str, acked: bool) -> bool:
    """程序目录内的录音目录且从未确认过 → 首次录音必须先弹窗。"""
    return (not acked) and record_dir_inside_program(directory)


def default_record_dir() -> str:
    """录音目录默认值：用户目录下 ``MBDSDR/recordings``。

    历史（0.2.0 及更早）默认是「程序目录同级 ``recordings``」——便携包按
    「删目录即卸载」分发时，删程序目录会把用户录音一起删掉。改为用户目录后，
    删/升级程序目录不再影响录音；旧配置的迁移见 :func:`resolve_record_dir`。
    """
    return os.path.join(os.path.expanduser("~"), "MBDSDR", "recordings")


def resolve_record_dir(value: Any) -> str:
    """把设置里的录音目录归一为绝对路径；留空/None → :func:`default_record_dir`。

    旧配置可能存相对路径（旧语义 = 程序目录旁 ``recordings``）。相对路径一律
    按旧根（程序目录的上一级）解析，不随进程 cwd 漂移；解析结果仍可能落在
    程序目录内，由 :func:`record_dir_needs_confirmation` 在首次录音时拦一次。
    """
    raw = "" if value is None else str(value).strip()
    if not raw:
        return default_record_dir()
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if not os.path.isabs(expanded):
        expanded = os.path.join(os.path.dirname(project_root()), expanded)
    return expanded


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
    # 0 Hz / 负频率不是"可调谐频点"，是"没读过"。旧版测试曾把 0 写进真实
    # 配置文件，启动时 validate_value 会把它夹到调谐下界 10 kHz，于是界面
    # 显示 000.010.000、后端却回退 98.5 MHz —— 两处读数对不上（2026-11-10
    # 真机实证）。这里在契约层直接回默认值，别让"没读过"冒充一个频点。
    if key == "frequency_hz":
        try:
            _f = float(value)
        except (TypeError, ValueError):
            _f = 0.0
        if not math.isfinite(_f) or _f <= 0.0:
            if notes is not None:
                notes.append(
                    "frequency_hz 为 0/非法值（不是可调谐频点），已回退默认 98 MHz")
            return item.default
    norm, reason = validate_value(item, value)
    if reason and notes is not None:
        notes.append(f"{key}: {reason}")
    return norm


#: 跨进程写锁的等待上限（秒）。配置写盘是毫秒级操作，等 2 秒仍拿不到锁
#: 说明别的进程卡在 IO；此时仍走原子替换，但把"锁超时"记进 last_error。
LOCK_TIMEOUT_S = 2.0


@contextmanager
def _exclusive_lock(lock_path: str, timeout: float = LOCK_TIMEOUT_S) -> Iterator[bool]:
    """跨进程互斥（Windows msvcrt / POSIX flock），yield 是否拿到锁。

    锁文件自身常驻（不删）：删锁文件会让"锁到另一个 inode"竞态重现。
    进程被杀时内核自动释放字节锁，不需要过期判定。
    """
    directory = os.path.dirname(lock_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    acquired = False
    fh = None
    deadline = time.monotonic() + timeout
    try:
        # Windows 下两个进程同时以 a+b 打开同一个 .lock 会抛 PermissionError。
        # 它和字节锁竞争一样是「暂时拿不到锁」，必须重试到超时后按
        # 「没拿到锁」降级（写锁等待文案），不能冒泡成「设置保存失败」，
        # 否则用户只看到一串文件路径，看不到「别的进程正在写」这个原因。
        while True:
            try:
                fh = open(lock_path, "a+b")
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
        if fh is None:
            yield False
            return
        if os.fstat(fh.fileno()).st_size == 0:
            fh.write(b"\0")
            fh.flush()
        while True:
            try:
                fh.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
        yield acquired
    finally:
        if acquired and fh is not None:
            try:
                fh.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        if fh is not None:
            fh.close()


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
        #: 自上次成功落盘以来改过的键。写盘时只把这些键覆盖到磁盘现值上，
        #: 避免两个实例各自 load 旧快照后整份覆盖、丢掉对方刚写的字段。
        self._dirty_keys: set = set()
        #: 本实例最近一次"看到"的磁盘值（load 读回 / 上次成功写入）。只有
        #: 磁盘现值偏离这个基线、且不是本次要写的值，才说明**别的实例**动过
        #: 这个键；单实例自己改自己的值不算并发覆盖。
        self._disk_snapshot: Dict[str, Any] = {}
        #: 已就哪些键报过"被并发覆盖"，避免每轮防抖写盘都刷同一条 warning。
        self._overwrite_warned: set = set()
        #: 上次 load 时若原文件损坏，这里是备份路径（None = 未损坏）
        self.corrupt_backup: Optional[str] = None
        #: 最近一次读/写失败的用户可读说明（None = 无错误）。
        #: 设置面板订阅 on_error 后能在状态栏显示"改了没保存"这类真相。
        self.last_error: Optional[str] = None
        #: 写盘失败回调（签名 (message) -> None）；由 UI 注入，避免配置层依赖 Qt。
        self.on_error: Optional[Any] = None
        #: 值变更订阅（签名 (key, value) -> None）。用纯 Python 回调而非 Qt
        #: Signal：配置层不依赖 QObject，测试里的 fake settings 也能挂。
        #: 用途是让「设置页改了值」能即时推给主界面（例如呼号改了要刷新
        #: 天空角标、专注模式改了要收起工程读数），而不必等重启。
        self._change_listeners: list = []
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
                # 并发覆盖告警的基线：load 时磁盘上真实存在的、归一后的值。
                # 必须取在音量迁移**之前**——迁移改的是内存值，磁盘还是旧值，
                # 落盘时不该被误判成"别的实例覆盖了我们"。
                inst._disk_snapshot = {k: inst._data[k] for k in raw
                                       if k in DEFAULTS}
                # 未知键（可能是更新版本写的，或是笔误）：不静默丢。这里只
                # 记录说明，文件里的原始未知键在写盘时会被保留（见
                # _merged_payload），避免旧版本把新版本的设置吃掉。
                unknown = sorted(set(raw) - set(DEFAULTS))
                if unknown:
                    shown = "、".join(repr(k) for k in unknown[:8])
                    more = f" 等 {len(unknown)} 个" if len(unknown) > 8 else ""
                    inst.migration_notes.append(
                        f"忽略未知键 {shown}{more}（不在当前 schema；仍保留在文件中）")
                # 音量量纲一次性迁移（0–63 硬件量纲 → 0–100 人类量纲）：
                # 旧文件有 volume 但没有 volume_scale_v2 标记。判据是
                # 「原始文件里的键」，不靠数值猜量纲——48 在两套量纲下
                # 都合法。迁移后随下次落盘写入标记，幂等。
                if "volume" in raw and "volume_scale_v2" not in raw:
                    try:
                        old = int(raw.get("volume", 30))
                    except (TypeError, ValueError):
                        old = 30
                        inst.migration_notes.append(
                            "volume: 旧格式值无法解析，按寄存器中值 30 迁移")
                    clamped = max(0, min(63, old))
                    if clamped != old:
                        inst.migration_notes.append(
                            f"volume: 旧格式值 {old} 超出 0–63 寄存器范围，"
                            f"已夹到 {clamped}")
                    new = volume_hw_to_pct(clamped)
                    inst.migration_notes.append(
                        f"volume: 旧格式 0–63 量纲 {clamped} 已换算为 "
                        f"0–100 量纲 {new}（写入 volume_scale_v2 标记）")
                    inst._data["volume"] = new
                    inst._data["volume_scale_v2"] = True
                    # 迁移结果要落盘（否则每次启动都重新换算）：标记为脏，
                    # 下次 flush（含退出时的）写入这两项。
                    inst._dirty_keys.update({"volume", "volume_scale_v2"})
                    inst._dirty = True
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
        """写一个字段并标记 dirty，防抖 500ms 后落盘。未知 key 忽略。

        值真的变了才通知订阅者：既避免滑杆每像素刷一次主界面，也让
        「listener 里再 set 同一个值」天然收敛、不会递归。
        """
        if key not in DEFAULTS:
            return
        new_value = _coerce(key, value)
        old_value = self._data.get(key)
        self._data[key] = new_value
        self._dirty_keys.add(key)
        self._dirty = True
        try:
            self._timer.start()  # 重启 500ms 防抖窗口
        except RuntimeError:
            # timer 已销毁（退出中）：直接同步写一次，不丢最后一次改动
            self._write_to_disk()
        if old_value != new_value:
            self._emit_change(key, new_value)

    def reset(self, key: str) -> Any:
        """把单项恢复到 schema 默认值；返回恢复后的值（未知 key 返回 None）。"""
        if key not in DEFAULTS:
            return None
        self.set(key, DEFAULTS[key])
        return self._data[key]

    # ---------------------------------------------------------------- 变更订阅
    def add_change_listener(self, fn: Any) -> None:
        """注册 ``(key, value) -> None`` 回调；重复注册忽略。"""
        if fn is not None and fn not in self._change_listeners:
            self._change_listeners.append(fn)

    def remove_change_listener(self, fn: Any) -> None:
        try:
            self._change_listeners.remove(fn)
        except ValueError:
            pass

    def _emit_change(self, key: str, value: Any) -> None:
        for fn in tuple(self._change_listeners):
            try:
                fn(key, value)
            except Exception as exc:  # 一个订阅者坏了不拖垮配置层
                logging.getLogger(__name__).warning(
                    "设置变更订阅者异常 key=%s: %s", key, exc)

    def reset_all(self) -> None:
        """恢复全部默认值（一次落盘）。"""
        changed = [k for k in DEFAULTS
                   if self._data.get(k) != DEFAULTS[k]]
        self._data = dict(DEFAULTS)
        self._dirty_keys = set(DEFAULTS)
        self._dirty = True
        try:
            self._timer.start()
        except RuntimeError:
            self._write_to_disk()
        for k in changed:
            self._emit_change(k, DEFAULTS[k])

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

    def _merged_payload(self) -> Dict[str, Any]:
        """把"本次改动的键"合并到磁盘现值上（读-改-写，锁内调用）。

        规则：
          * 磁盘不存在/不可读 → 写自己完整视图（首次落盘不能只写一个键）；
          * 磁盘可读 → 以磁盘为底（保留未知键），只覆盖本次改过的键；
          * 未改动的键若磁盘值正是我们内存值的归一结果（如手改越界值被夹取），
            写回归一值，让修复落盘；否则视为别的实例写了更新值，尊重磁盘。
        """
        payload: Dict[str, Any] = {}
        disk_ok = False
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                if isinstance(disk, dict):
                    payload = dict(disk)
                    disk_ok = True
            except (OSError, ValueError):
                payload = {}
        if not disk_ok:
            return dict(self._data)

        for key in DEFAULTS:
            if key in self._dirty_keys:
                disk_value = payload.get(key, _MISSING)
                if disk_value is not _MISSING:
                    item = SCHEMA.get(key)
                    disk_norm = (validate_value(item, disk_value)[0]
                                 if item is not None else disk_value)
                    baseline = self._disk_snapshot.get(key, _MISSING)
                    # 只有"磁盘现值偏离我们上次看到的基线"才说明别的实例写过；
                    # 单实例把值从 A 改成 B 时磁盘仍是 A，属于正常编辑，不是覆盖。
                    changed_by_other = (
                        baseline is _MISSING
                        or disk_norm != baseline)
                    if (changed_by_other and disk_norm != self._data[key]
                            and key not in self._overwrite_warned):
                        # 同名键并发写：本次赢，但对方刚写的值被覆盖。不引入
                        # 冲突解决（无法判断谁"对"），至少把覆盖事实记下来。
                        logger.warning(
                            "设置键 %s 被并发写入覆盖：磁盘值 %r → 本次值 %r",
                            key, disk_value, self._data[key])
                        self._overwrite_warned.add(key)
                payload[key] = self._data[key]
                continue
            if key in payload:
                item = SCHEMA.get(key)
                disk_value = payload[key]
                if item is not None:
                    norm, _ = validate_value(item, disk_value)
                    if norm == self._data.get(key):
                        payload[key] = norm      # 我们的归一结果，落盘固化
                continue
            # 旧版本文件缺这个键：按当前默认/内存值补齐，保证文件完整。
            payload[key] = self._data.get(key, DEFAULTS.get(key))
        return payload

    def _write_to_disk(self) -> None:
        """实际写盘：跨进程锁内读-合并-写唯一临时文件，再 os.replace。

        为什么不是直接 json.dump 到 ``路径.tmp``：
          * 固定临时名会让两个进程同时写同一个 .tmp，Windows 下直接
            PermissionError（实测 100 轮并发写有 63 次失败）；
          * 整份覆盖会丢另一个实例刚写的字段（实测 A 的 frequency/callsign
            被 B 的旧快照顶回默认）。
        锁 + 按脏键合并 + 唯一临时文件三件一起，才是生产侧的并发答案。
        """
        tmp_path = f"{self._path}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp"
        try:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with _exclusive_lock(self._path + ".lock", LOCK_TIMEOUT_S) as acquired:
                if not acquired:
                    # 没拿到锁就**不写**：此时读不到可靠的磁盘现值，整份覆盖
                    # 会丢别的实例刚写的字段。保留 dirty 标记，下次 flush
                    # （防抖定时器/退出）重试；同时把情况告诉用户。
                    self.last_error = (
                        f"配置写锁等待超过 {LOCK_TIMEOUT_S:g}s，可能有其他进程"
                        "正在写；本次未写入，稍后重试")
                    if callable(self.on_error):
                        self.on_error(self.last_error)
                    return
                payload = self._merged_payload()
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self._path)
            self._dirty = False
            self._dirty_keys.clear()
            self._overwrite_warned.clear()
            # 落盘成功后磁盘现值就是刚写的 payload：刷新并发检测基线。
            self._disk_snapshot = {
                k: _coerce(k, payload[k]) for k in DEFAULTS if k in payload}
            self.last_error = None
        except (OSError, TypeError, ValueError) as exc:
            # 磁盘满/权限不足/只读/不可序列化：不抛（退出 flush 也不崩），
            # 但必须让用户看见——"改设置写失败静默"等于下次开机改动凭空消失。
            self.last_error = f"设置保存失败（{exc.__class__.__name__}）：{exc}"
            if callable(self.on_error):
                self.on_error(self.last_error)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)          # 不留半截临时文件
            except OSError:
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

# SPDX-License-Identifier: MIT
"""MBDSDR 运行期 USB 热插拔监听（纯 Python，无 Qt）
==================================================

为什么需要它
------------
SigDigger / URH / ka9q-radio 都只在启动时枚举设备，运行期拔插靠下游读失败
去猜。项目里原先的两个 watchdog 因此被删：``device_watchdog`` 的探活被
``_mark_link_lost`` 覆盖，``usb_watchdog`` 不校验设备身份——第二台同类设备
拔出会把第一台也停掉。删掉之后，运行中拔掉设备，界面状态仍然只能靠下游
报错猜。本模块补的就是这一条，且只做「观测与归因」，不碰重连。

身份分级（这是与 watchdog 的根本区别）
--------------------------------------
1. ``serial`` —— ``driver|serial:<序列号>``。跟着设备走，换口、换插拔顺序都
   不变，是唯一能据此判定「这一台拔出了」的身份。
2. ``usb_path`` —— ``uri:usb:<bus>.<port>``（libiio 的 USB URI 就是**端口
   路径**，不是序列号）。只在那一个 USB 口上有效：**换口会被当成另一台
   设备**。这条限制会出现在连接时 tooltip 与相关事件文案里。
3. ``endpoint`` —— 网络 / 文件端点（``uri:tcp:...`` 等）。没有物理插拔，
   只能按枚举结果判断。
4. ``provisional`` —— 无序列号时退回 ``driver|index:<n>`` 或
   ``driver|label:<name>``。**枚举序号会随插拔顺序前移、显示名可能重名**，
   所以这类身份只报歧义（``ambiguous``），绝不据此判定某台设备被拔出。

单台拔出只影响单台
------------------
``observe()`` 按身份逐台出事件，调用方只处理与「当前连接那台」匹配的
事件。两台同型号设备拔掉一台，另一台不会收到任何事件（见
``tests/test_usb_hotplug.py`` 里带反证的两台用例）。

unplug 与「在位但打不开」
------------------------
枚举消失 ≠ 物理拔出：设备可能只是被别的进程占用、驱动拒绝打开，读不到
序列号后换了身份键。因此拔出判定优先问**物理在位探针**（Windows
SetupAPI / Linux sysfs，均不 open 设备）：

* 探针说还在 → ``present_unopenable``（不断开）；
* 探针说不在 → ``unplugged``（verified=True）；
* 探针不可用（权限/失败）→ ``disappeared_unverified``（**不**断开），
  直到拿到物理证据；
* 平台没有探针 → 显式 ``probe_problem(unsupported)``，之后按枚举连续
  多拍消失判定，并在文案里写明「按枚举判定」。

监听失败本身可归因
------------------
``poll_problem`` 区分 ``poll_failed`` / ``permission`` / ``unsupported``；
探针问题用 ``probe_problem`` 单独上报。三者不会都表现成「什么都没有发生」。
枚举失败时**保留上一次快照**，绝不当成「设备全没了」。

与重连的关系
------------
本模块不 import 任何重连实现、不产出连接动作，只产出事件；日志/UI 层
自行决定是否断开或提示。避免监听与重连互相抢状态。
"""
from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 身份分级
GRADE_SERIAL = "serial"
GRADE_USB_PATH = "usb_path"
GRADE_ENDPOINT = "endpoint"
GRADE_PROVISIONAL = "provisional"

# ------------------------------------------------------------------ 探针状态
PROBE_OK = "ok"
PROBE_UNSUPPORTED = "unsupported"
PROBE_PERMISSION = "permission"
PROBE_FAILED = "failed"

# ------------------------------------------------------------------ 轮询失败归因
REASON_POLL_FAILED = "poll_failed"
REASON_PERMISSION = "permission"
REASON_UNSUPPORTED = "unsupported"

# ------------------------------------------------------------------ 事件类型
EVENT_PLUGGED = "plugged"
EVENT_UNPLUGGED = "unplugged"
EVENT_PRESENT_UNOPENABLE = "present_unopenable"
EVENT_AMBIGUOUS = "ambiguous"
EVENT_DISAPPEARED_UNVERIFIED = "disappeared_unverified"
EVENT_POLL_PROBLEM = "poll_problem"
EVENT_PROBE_PROBLEM = "probe_problem"
EVENT_RECOVERED = "recovered"


@dataclass(frozen=True)
class HotplugIdentity:
    """一台设备在热插拔判定里使用的身份。"""

    key: str
    grade: str
    note: str = ""


@dataclass(frozen=True)
class HotplugEvent:
    """一次观测的结论。``kind`` 见 ``EVENT_*``。"""

    kind: str
    key: str = ""
    grade: str = ""
    label: str = ""
    #: 是否有「物理/枚举」证据支撑；None = 与具体设备无关的监听状态事件。
    verified: Optional[bool] = None
    user_text: str = ""
    detail: str = ""
    device: Optional[Dict[str, Any]] = None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _label(device: Optional[Dict[str, Any]]) -> str:
    device = device or {}
    return _text(device.get("label")) or _text(device.get("driver")) or "未知设备"


def _strip_ordinal(key: str) -> str:
    """去掉 ``resolve_device_identities`` 为撞键加的 ``#n``。"""
    return re.sub(r"#\d+$", "", str(key or ""))


def serial_from_key(key: str) -> str:
    """从 ``driver|serial:<serial>`` 里取出序列号；取不到返回空串。"""
    base = _strip_ordinal(key)
    marker = "|serial:"
    if marker not in base:
        return ""
    return base.split(marker, 1)[1].strip()


def usb_port_path(value: str) -> Optional[str]:
    """把 libiio 的 ``usb:<bus>.<port>`` / 身份键转成 sysfs 风格路径 ``<bus>-<port>``。

    ``usb:1.2.5`` → ``1-2.5``。拿不到端口拓扑时返回 None（不做猜测）。
    """
    v = _text(value)
    if not v:
        return None
    if v.startswith("uri:"):
        v = v[4:]
    low = v.lower()
    if low.startswith("usb:"):
        v = v[4:]
    elif "|" in v:
        return None
    if "." not in v:
        return None
    bus, port = v.split(".", 1)
    if not bus or not port:
        return None
    return f"{bus}-{port}"


def hotplug_grade(device: Dict[str, Any]) -> Tuple[str, str]:
    """``(grade, note)``。note 是给用户看的「这个身份为什么会变」。"""
    device = device or {}
    driver = _text(device.get("driver")) or "?"
    label = _label(device)
    serial = _text(device.get("serial"))
    if serial:
        return GRADE_SERIAL, ""
    args = device.get("device_args") or {}
    uri = _text(device.get("uri")) or _text(args.get("uri"))
    if uri:
        if uri.lower().startswith("usb:"):
            return (GRADE_USB_PATH,
                    f"设备「{label}」没有序列号，按 USB 口路径 {uri} 识别；"
                    "换到另一个 USB 口会被当成另一台设备，旧口那台会被判定为已拔出。")
        return (GRADE_ENDPOINT,
                f"设备「{label}」按网络/文件地址 {uri} 识别；地址变了就是另一台。")
    index = _text(args.get("index", device.get("index")))
    if index:
        return (GRADE_PROVISIONAL,
                f"设备「{label}」没有序列号，按枚举序号 {index} 识别；"
                "插拔同类设备或换口后序号会前移，可能被当成另一台。"
                "这类设备只报歧义，不判定拔出。")
    return (GRADE_PROVISIONAL,
            f"设备「{label}」既无序列号也无枚举序号，按显示名识别；"
            "同名设备无法区分。这类设备只报歧义，不判定拔出。")


def resolve_hotplug_identities(devices: Sequence[Dict[str, Any]]
                               ) -> List[HotplugIdentity]:
    """整份枚举逐台算身份。键与 ``connect_request_store.resolve`` 逐字一致。

    撞键时沿用 ``resolve_device_identities`` 的 ``#n`` 口径，保证面板里
    「连接时记的键」和这里「每拍算的键」是同一种字符串。
    """
    items = list(devices or [])
    try:
        from .sdr_backend import resolve_device_identities
    except Exception as exc:  # noqa: BLE001 - 内核不可用时仍要如实降级
        logger.warning("无法导入统一身份解析，热插拔身份全部降级为 provisional：%s", exc)
        return [HotplugIdentity(f"unknown|index:{i}", GRADE_PROVISIONAL,
                                "统一身份解析不可用，本拍身份无法核对。")
                for i in range(len(items))]
    base = resolve_device_identities(items)
    out: List[HotplugIdentity] = []
    for device, (key, _quality, warning) in zip(items, base):
        grade, note = hotplug_grade(device)
        out.append(HotplugIdentity(key, grade, note or warning))
    return out


# ------------------------------------------------------------------ 在位探针
@dataclass(frozen=True)
class Presence:
    """一次物理在位查询的结果。"""

    status: str
    serials: frozenset = field(default_factory=frozenset)
    ports: frozenset = field(default_factory=frozenset)
    #: 探针是否能回答「USB 口路径」。Windows SetupAPI 只能给序列号。
    ports_known: bool = False
    #: 结果是否可用来判「不在」。枚举到 0 台 USB 设备时视为不可信。
    authoritative: bool = True
    detail: str = ""

    def has_serial(self, serial: str) -> bool:
        return _text(serial).lower() in self.serials

    def reason_text(self) -> str:
        if self.status == PROBE_UNSUPPORTED:
            return f"当前平台没有 USB 物理在位探针（{self.detail or '不支持'}）"
        if self.status == PROBE_PERMISSION:
            return f"读取 USB 设备列表被拒绝（权限不足：{self.detail or '拒绝访问'}）"
        if self.status == PROBE_FAILED:
            return f"USB 在位探针调用失败（{self.detail or '未知错误'}）"
        return ""


def _usb_serials_from_instance_ids(instance_ids: Sequence[str]) -> frozenset:
    """从 ``USB\\VID_xxxx&PID_xxxx\\<serial>`` 取末段。

    末段含 ``&`` 的是系统生成的实例 ID（设备没有 iSerialNumber），不是序列号。
    """
    out = set()
    for iid in instance_ids:
        last = _text(iid).rsplit("\\", 1)[-1].lower()
        if last and "&" not in last:
            out.add(last)
    return frozenset(out)


class WindowsSetupApiProbe:
    """Windows：SetupAPI 枚举在位 USB 设备实例（不 open 设备）。

    只读注册表层的设备实例：设备被别的进程占用时**仍然枚举得到**，这正是
    区分「拔出」与「在位但打不开」需要的证据。代价是拿不到 USB 口拓扑，
    因此 ``ports_known=False``。
    """

    name = "windows-setupapi"

    def probe(self) -> Presence:
        if not sys.platform.startswith("win"):
            return Presence(PROBE_UNSUPPORTED, detail=f"sys.platform={sys.platform}")
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

            class _SP_DEVINFO_DATA(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", _GUID),
                            ("DevInst", wintypes.DWORD),
                            ("Reserved", ctypes.c_void_p)]

            setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
            setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
            setupapi.SetupDiGetClassDevsW.argtypes = (
                ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD)
            setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
            setupapi.SetupDiEnumDeviceInfo.argtypes = (
                ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(_SP_DEVINFO_DATA))
            setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
            setupapi.SetupDiGetDeviceInstanceIdW.argtypes = (
                ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA),
                wintypes.LPWSTR, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD))
            setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
            setupapi.SetupDiDestroyDeviceInfoList.argtypes = (ctypes.c_void_p,)

            digcf_present = 0x02
            digcf_allclasses = 0x04
            error_no_more_items = 259
            invalid_handle = ctypes.c_void_p(-1).value

            hdev = setupapi.SetupDiGetClassDevsW(
                None, "USB", None, digcf_present | digcf_allclasses)
            if not hdev or hdev == invalid_handle:
                err = int(ctypes.get_last_error() or 0)
                status = PROBE_PERMISSION if err == 5 else PROBE_FAILED
                return Presence(status, authoritative=False,
                                detail=f"SetupDiGetClassDevs 失败，GetLastError={err}")
            instance_ids: List[str] = []
            try:
                index = 0
                while True:
                    info = _SP_DEVINFO_DATA()
                    info.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
                    if not setupapi.SetupDiEnumDeviceInfo(
                            hdev, index, ctypes.byref(info)):
                        err = int(ctypes.get_last_error() or 0)
                        if err in (0, error_no_more_items):
                            break
                        status = PROBE_PERMISSION if err == 5 else PROBE_FAILED
                        return Presence(status, authoritative=False,
                                        detail=f"SetupDiEnumDeviceInfo 失败，"
                                               f"GetLastError={err}")
                    buf = ctypes.create_unicode_buffer(1024)
                    need = wintypes.DWORD()
                    if setupapi.SetupDiGetDeviceInstanceIdW(
                            hdev, ctypes.byref(info), buf, len(buf),
                            ctypes.byref(need)) and buf.value:
                        instance_ids.append(buf.value)
                    index += 1
            finally:
                setupapi.SetupDiDestroyDeviceInfoList(hdev)

            return Presence(PROBE_OK,
                            serials=_usb_serials_from_instance_ids(instance_ids),
                            ports=frozenset(), ports_known=False,
                            authoritative=bool(instance_ids),
                            detail=f"SetupAPI 枚举到 {len(instance_ids)} 个 USB 实例")
        except Exception as exc:  # noqa: BLE001 - 探针绝不能向上抛
            return Presence(PROBE_FAILED, authoritative=False,
                            detail=f"{type(exc).__name__}: {exc}")


def scan_sysfs_usb(root: str) -> Presence:
    """扫描 sysfs 设备目录（独立成函数，便于离线用临时目录验证）。"""
    try:
        names = os.listdir(root)
    except PermissionError as exc:
        return Presence(PROBE_PERMISSION, authoritative=False, detail=str(exc))
    except OSError as exc:
        return Presence(PROBE_FAILED, authoritative=False, detail=str(exc))
    ports = set()
    serials = set()
    permission_error = ""
    for name in names:
        if "-" not in name or ":" in name:
            continue        # 跳过根 hub 与接口目录
        ports.add(name)
        path = os.path.join(root, name, "serial")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                serial = fh.read().strip().lower()
        except FileNotFoundError:
            continue        # 没有 iSerialNumber 的设备没有这个文件
        except PermissionError as exc:
            permission_error = str(exc)
            continue
        if serial:
            serials.add(serial)
    if permission_error and not ports:
        return Presence(PROBE_PERMISSION, authoritative=False,
                        detail=permission_error)
    return Presence(PROBE_OK, serials=frozenset(serials),
                    ports=frozenset(ports), ports_known=True,
                    authoritative=bool(ports),
                    detail=f"sysfs 枚举到 {len(ports)} 个 USB 设备")


class LinuxSysfsProbe:
    """Linux：读 ``/sys/bus/usb/devices``（不 open 设备）。

    sysfs 的设备目录名就是口路径（``1-2.5``），目录里的 ``serial`` 文件是
    iSerialNumber。二者都能在设备被占用时读到。
    """

    name = "linux-sysfs"

    def __init__(self, root: str = "/sys/bus/usb/devices"):
        self._root = str(root)

    def probe(self) -> Presence:
        if not sys.platform.startswith("linux"):
            return Presence(PROBE_UNSUPPORTED, detail=f"sys.platform={sys.platform}")
        return scan_sysfs_usb(self._root)


class UnsupportedProbe:
    """显式「本平台没有实现」——不静默降级成「什么都没发生」。"""

    def __init__(self, detail: str = ""):
        self._detail = detail or f"sys.platform={sys.platform}"

    def probe(self) -> Presence:
        return Presence(PROBE_UNSUPPORTED, authoritative=False, detail=self._detail)


def default_presence_probe():
    """按平台选在位探针；没有实现的平台返回显式的 UnsupportedProbe。"""
    if sys.platform.startswith("win"):
        return WindowsSetupApiProbe()
    if sys.platform.startswith("linux"):
        return LinuxSysfsProbe()
    return UnsupportedProbe()


# ------------------------------------------------------------------ 轮询归因
_PERMISSION_HINTS = (
    "access denied", "access_denied", "permission denied", "permissionerror",
    "errno 13", "拒绝访问", "权限不足", "libusb_error_access",
)
_UNSUPPORTED_HINTS = (
    "modulenotfounderror", "no module named", "importerror",
    "dll load failed", "依赖缺失",
)


def classify_poll_error(error: str) -> str:
    """把枚举失败文本归到 ``REASON_*``。归不到的按普通轮询失败算。"""
    low = _text(error).lower()
    if any(h in low for h in _PERMISSION_HINTS):
        return REASON_PERMISSION
    if any(h in low for h in _UNSUPPORTED_HINTS):
        return REASON_UNSUPPORTED
    return REASON_POLL_FAILED


def _poll_problem_event(reason: str, error: str) -> HotplugEvent:
    if reason == REASON_PERMISSION:
        user_text = "热插拔轮询被拒绝（权限不足） · 设备状态暂不更新"
    elif reason == REASON_UNSUPPORTED:
        user_text = "热插拔轮询不可用（枚举依赖缺失） · 设备状态暂不更新"
    else:
        user_text = "热插拔轮询失败 · 设备状态暂不更新"
    return HotplugEvent(
        kind=EVENT_POLL_PROBLEM, key="", verified=None,
        user_text=user_text,
        detail=(f"本次设备枚举返回错误：{error}\n"
                "这不等于设备已拔出：保留上一次状态，下一拍重试。"))


# ------------------------------------------------------------------ 监听状态机
class HotplugMonitor:
    """按稳定身份做运行期拔插判定；只出事件，不做连接/重连动作。

    ``identity_fn`` 默认用 ``resolve_hotplug_identities``。测试可注入
    index 版身份做反证（见 ``tests/test_usb_hotplug.py``）。

    Parameters
    ----------
    probe : 可选，``probe() -> Presence``。None 表示用平台默认探针。
    verified_threshold : 有物理证据时连续多少拍才判定拔出（默认 1：
                         物理探针的「不在」本身就是直接证据）。
    unverified_threshold : 没有物理证据时连续多少拍才按枚举判定（默认 2）。
    """

    def __init__(self, probe=None, identity_fn=None,
                 verified_threshold: int = 1,
                 unverified_threshold: int = 2) -> None:
        self._probe = probe
        self._identity_fn = identity_fn or resolve_hotplug_identities
        self._verified_threshold = max(1, int(verified_threshold))
        self._unverified_threshold = max(1, int(unverified_threshold))
        self._seen: Dict[str, Tuple[HotplugIdentity, Dict[str, Any]]] = {}
        self._missing: Dict[str, int] = {}
        self._ambiguous_reported: set = set()
        self._present_unopenable: set = set()
        self._unverified_reported: set = set()
        self._poll_problem: Optional[Tuple[str, str]] = None
        self._probe_problem: Optional[Tuple[str, str]] = None
        self._baseline_done = False

    # --------------------------------------------------------------- 外部注入
    def set_probe(self, probe) -> None:
        self._probe = probe
        self._probe_problem = None

    def expect(self, key: str, device: Optional[Dict[str, Any]] = None) -> None:
        """登记「这台正在用」：首拍枚举里没有它时，也能判定为拔出。

        主窗口在连接成功时调用（面板把它接到 ``set_connected``）。只登记
        身份，不产生事件，也不改变已经建立的基准。
        """
        key = _text(key)
        if not key:
            return
        grade, note = hotplug_grade(device or {}) if device else (GRADE_PROVISIONAL, "")
        self._seen[key] = (HotplugIdentity(key, grade, note), dict(device or {}))
        self._missing.pop(key, None)
        self._ambiguous_reported.discard(key)
        self._present_unopenable.discard(key)
        self._unverified_reported.discard(key)

    # --------------------------------------------------------------- 观测入口
    def observe(self, devices: Optional[Sequence[Dict[str, Any]]],
                error: str = "") -> List[HotplugEvent]:
        """吃一拍枚举结果，返回本拍结论（可能为空）。

        ``error`` 非空时只报轮询问题并保留快照——绝不把「枚举失败」当成
        「设备全没了」。
        """
        events: List[HotplugEvent] = []
        if error:
            reason = classify_poll_error(error)
            if self._poll_problem != (reason, error):
                self._poll_problem = (reason, error)
                events.append(_poll_problem_event(reason, error))
            return events
        if self._poll_problem is not None:
            self._poll_problem = None
            events.append(HotplugEvent(
                kind=EVENT_RECOVERED, user_text="热插拔轮询已恢复",
                detail="上一拍的枚举失败已恢复；设备状态继续更新。"))

        items = list(devices or [])
        idents = self._identity_fn(items)
        now: Dict[str, Tuple[HotplugIdentity, Dict[str, Any]]] = {
            ident.key: (ident, device)
            for ident, device in zip(idents, items)}
        prev_keys = set(self._seen)
        now_keys = set(now)
        first = not self._baseline_done

        if first:
            self._baseline_done = True
            for key, value in now.items():
                self._seen.setdefault(key, value)

        for key in now_keys:
            self._ambiguous_reported.discard(key)
            self._present_unopenable.discard(key)
            self._unverified_reported.discard(key)
            self._missing.pop(key, None)

        absent = sorted(prev_keys - now_keys)
        presence = None
        if absent or first:
            if first or any(self._needs_probe(self._seen[k][0]) for k in absent):
                presence = self._probe_now()
                events.extend(self._report_probe_problem(presence))

        for key in absent:
            ident, device = self._seen[key]
            events.extend(self._handle_absent(ident, device, presence))

        for key in now_keys:
            self._seen[key] = now[key]

        if not first:
            for key in sorted(now_keys - prev_keys):
                ident, device = now[key]
                events.append(HotplugEvent(
                    kind=EVENT_PLUGGED, key=key, grade=ident.grade,
                    label=_label(device),
                    verified=(ident.grade != GRADE_PROVISIONAL),
                    user_text=f"发现新设备：{_label(device)}",
                    detail=ident.note, device=device))
        return events

    # --------------------------------------------------------------- 内部
    @staticmethod
    def _needs_probe(ident: HotplugIdentity) -> bool:
        return ident.grade in (GRADE_SERIAL, GRADE_USB_PATH)

    def _probe_now(self) -> Optional[Presence]:
        if self._probe is None:
            return None
        try:
            result = self._probe.probe()
        except Exception as exc:  # noqa: BLE001 - 探针异常按「不可用」上报
            return Presence(PROBE_FAILED, authoritative=False,
                            detail=f"{type(exc).__name__}: {exc}")
        if not isinstance(result, Presence):
            return Presence(PROBE_FAILED, authoritative=False,
                            detail=f"探针返回了 {type(result).__name__}，不是 Presence")
        return result

    def _report_probe_problem(self, presence: Optional[Presence]
                              ) -> List[HotplugEvent]:
        if presence is None:
            return []
        if presence.status == PROBE_OK:
            if self._probe_problem is not None:
                self._probe_problem = None
                return [HotplugEvent(
                    kind=EVENT_RECOVERED, user_text="USB 在位探针已恢复",
                    detail="物理在位查询恢复正常；拔插判定重新有物理证据。")]
            return []
        token = (presence.status, presence.detail)
        if self._probe_problem == token:
            return []
        self._probe_problem = token
        if presence.status == PROBE_UNSUPPORTED:
            user_text = "本平台没有 USB 物理在位探针 · 拔插只能按枚举判断"
            detail = (f"{presence.detail or '平台不支持'}。"
                      "拔插判定会退化为「枚举里连续消失」，"
                      "无法区分「在位但被占用」与「已拔出」。"
                      "这是能力限制，不代表没有发生热插拔。")
        elif presence.status == PROBE_PERMISSION:
            user_text = "USB 在位探针不可用（权限不足） · 不会据此断定设备已拔出"
            detail = (f"{presence.detail or '读取被拒绝'}。"
                      "热插拔仍按枚举更新，但在拿到物理证据前"
                      "不会把设备判成已拔出（避免把权限问题误判成拔掉）。")
        else:
            user_text = "USB 在位探针调用失败 · 不会据此断定设备已拔出"
            detail = (f"{presence.detail or '未知错误'}。"
                      "热插拔仍按枚举更新，但在拿到物理证据前"
                      "不会把设备判成已拔出。")
        return [HotplugEvent(kind=EVENT_PROBE_PROBLEM, verified=False,
                             user_text=user_text, detail=detail)]

    def _presence_state(self, ident: HotplugIdentity,
                        presence: Optional[Presence]) -> Tuple[str, str]:
        """``(present|absent|unknown|enum_only, 说明)``。"""
        if ident.grade == GRADE_ENDPOINT:
            return "enum_only", "该身份是网络/文件端点，没有物理在位探针"
        if presence is None:
            return "unknown", "这一拍没有拿到 USB 在位探针结果"
        if presence.status == PROBE_UNSUPPORTED:
            # 平台没有探针：不假装有物理证据，退回「连续多拍枚举消失」，
            # 并在文案里写明是枚举判定（不静默降级成什么都没发生）。
            return "enum_only", presence.reason_text()
        if presence.status != PROBE_OK:
            return "unknown", presence.reason_text()
        if not presence.authoritative:
            return "unknown", "探针没有枚举到任何 USB 设备，结果不可信"
        if ident.grade == GRADE_SERIAL:
            serial = serial_from_key(ident.key)
            if not serial:
                return "unknown", "身份键里没有可用序列号"
            if presence.has_serial(serial):
                return "present", f"探针在 USB 设备表中找到了序列号 {serial}"
            return "absent", f"探针在 USB 设备表中没有序列号 {serial}"
        if ident.grade == GRADE_USB_PATH:
            if not presence.ports_known:
                return "unknown", "该平台的探针只能读序列号，读不到 USB 口路径"
            path = usb_port_path(ident.key)
            if not path:
                return "unknown", "身份键里没有可解析的 USB 口路径"
            if path in presence.ports:
                return "present", f"探针在 USB 拓扑中找到了口 {path}"
            return "absent", f"探针在 USB 拓扑中没有口 {path}"
        return "unknown", "该设备的身份不足以做物理核对"

    def _handle_absent(self, ident: HotplugIdentity,
                       device: Dict[str, Any],
                       presence: Optional[Presence]) -> List[HotplugEvent]:
        key = ident.key
        if ident.grade == GRADE_PROVISIONAL:
            if key in self._ambiguous_reported:
                return []
            self._ambiguous_reported.add(key)
            return [HotplugEvent(
                kind=EVENT_AMBIGUOUS, key=key, grade=ident.grade,
                label=_label(device), verified=False,
                user_text="设备列表里少了一台，但无法确认是哪一台",
                detail=(ident.note + " 这类身份没有跟着设备走的东西，"
                        "枚举序号又会随插拔前移；本次只报歧义，"
                        "不判定拔出，也不断开连接。"),
                device=device)]

        state, reason = self._presence_state(ident, presence)
        if state == "present":
            if key in self._present_unopenable:
                return []
            self._present_unopenable.add(key)
            return [HotplugEvent(
                kind=EVENT_PRESENT_UNOPENABLE, key=key, grade=ident.grade,
                label=_label(device), verified=True,
                user_text="设备仍在，但枚举里读不到它",
                detail=(f"{reason}。这是「在位但打不开/读不到」，不是拔出；"
                        "不会断开连接。"),
                device=device)]
        if state == "unknown":
            if key in self._unverified_reported:
                return []
            self._unverified_reported.add(key)
            return [HotplugEvent(
                kind=EVENT_DISAPPEARED_UNVERIFIED, key=key, grade=ident.grade,
                label=_label(device), verified=False,
                user_text="设备从枚举中消失，但无法确认是否已拔出",
                detail=(f"{reason}。在拿到物理证据前不会判定拔出，"
                        "避免把「被占用 / 权限不足」误判成拔掉。"),
                device=device)]

        self._missing[key] = self._missing.get(key, 0) + 1
        threshold = (self._verified_threshold if state == "absent"
                     else self._unverified_threshold)
        if self._missing[key] < threshold:
            return []
        self._seen.pop(key, None)
        self._missing.pop(key, None)
        verified = state == "absent"
        if verified:
            user_text = "设备已拔出"
            detail = f"{reason}。已按物理在位探针确认拔出。"
        elif ident.grade == GRADE_ENDPOINT:
            user_text = "设备已从枚举中消失（网络端点，按枚举判定）"
            detail = (f"{reason}。连续 {threshold} 拍枚举都没有这个端点；"
                      "没有物理插拔可验。")
        else:
            user_text = ("设备已从枚举中消失"
                         "（本平台无物理在位探针，按枚举判定）")
            detail = (f"{reason}。连续 {threshold} 拍枚举都没有这台设备；"
                      "不能排除「在位但被占用 / 驱动拒绝枚举」。")
        return [HotplugEvent(kind=EVENT_UNPLUGGED, key=key, grade=ident.grade,
                             label=_label(device), verified=verified,
                             user_text=user_text, detail=detail,
                             device=device)]


__all__ = [
    "GRADE_SERIAL", "GRADE_USB_PATH", "GRADE_ENDPOINT", "GRADE_PROVISIONAL",
    "PROBE_OK", "PROBE_UNSUPPORTED", "PROBE_PERMISSION", "PROBE_FAILED",
    "REASON_POLL_FAILED", "REASON_PERMISSION", "REASON_UNSUPPORTED",
    "EVENT_PLUGGED", "EVENT_UNPLUGGED", "EVENT_PRESENT_UNOPENABLE",
    "EVENT_AMBIGUOUS", "EVENT_DISAPPEARED_UNVERIFIED", "EVENT_POLL_PROBLEM",
    "EVENT_PROBE_PROBLEM", "EVENT_RECOVERED",
    "HotplugIdentity", "HotplugEvent", "Presence",
    "HotplugMonitor", "WindowsSetupApiProbe", "LinuxSysfsProbe",
    "scan_sysfs_usb", "UnsupportedProbe", "default_presence_probe",
    "hotplug_grade", "resolve_hotplug_identities",
    "serial_from_key", "usb_port_path", "classify_poll_error",
]

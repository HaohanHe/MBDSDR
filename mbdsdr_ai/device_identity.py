# SPDX-License-Identifier: MIT
"""设备身份的**唯一入口**（纯 Python，无 Qt）
=============================================

仓里原先有四套各自算身份的地方。多套身份最危险的后果不是「不一致」，而是
**两边都算得出一个看着合理的值，却指着不同的物理设备**——比如热插拔认出
A、占用登记认出 B，「把设备让给另一个进程」就会让错那一台。

四套的测绘（同一份枚举条目，各自算出什么）
------------------------------------------

================  ==========================================  ==========================  ==================  ====================  ==================
系统              入口                                        有 serial                   只有 ``uri:usb:a.b``  只有 index/label      参与跨进程比较？
================  ==========================================  ==========================  ==================  ====================  ==================
跨进程占用        ``device_identity.device_identity_key``       ``driver|serial:<sn>``      ``uri:usb:a.b``      ``driver|index:n``    是（主键 + 别名）
                  ``+ device_identity_aliases``                stable，别名不参与          stable               provisional，别名参与
热插拔            ``usb_hotplug.resolve_hotplug_identities``   ``driver|serial:<sn>``      ``uri:usb:a.b``      ``driver|index:n``    否（只出事件）
                  （键同上，另带 grade/port_key）              grade=serial                grade=usb_path       grade=provisional
面板 / 请求存储   ``connect_request_store.resolve``            ``driver|serial:<sn>``      ``uri:usb:a.b``      ``driver|index:n``    是（与占用同键）
DeviceManager     ``DeviceInfo.device_key``                    ``driver:serial``（拼写不同） 无 uri 概念 → index    ``driver|index:n``    否
================  ==========================================  ==========================  ==================  ====================  ==================

分歧点与裁定
------------

1. **``uri:usb:<bus>.<port>`` 的质量判定必须不同。** 占用/面板把它当
   ``stable``（同一个口只能是同一台，用作互斥键不会双开）；热插拔把它当
   ``usb_path``——libiio 的 USB URI 是**端口路径**，换口就是另一台。两者问的
   不是同一个问题：占用问「是不是同一台」，热插拔问「还在不在这条链路上」。
   本模块同时给出 ``quality``（canonical 口径）与 ``grade``（热插拔口径），
   两方各取所需，不再各算一套。
2. **``DeviceManager.device_key`` 的拼写（``driver:serial``）不收敛。** 它是
   面向用户/CLI 的标识（``--device rtlsdr:SN`` 已被脚本使用），且
   ``DeviceManager`` 不参与跨进程占用比较——拼写不同不会让「同一台被当成
   两台去抢设备」。拼写定义收进 :func:`manager_key` 一处，本模块的
   ``manager_key`` 字段供对照与测试，防止再散落成第二份 f-string。
3. **index/label 兜底只能当 provisional。** 枚举序号会随插拔前移、显示名会
   重名；占用侧允许别名匹配（宁可多拦），热插拔侧只报歧义、绝不判拔出。

统一入口
--------

* :func:`describe` —— 一台设备 → :class:`DeviceIdentity`
  （稳定身份键、端口身份、是否 provisional、歧义原因、别名、展示名）。
* :func:`describe_all` —— 整份枚举逐台算（撞键的 ``#n`` 依赖整份上下文，
  逐台算永远对不上）。

稳定身份键的字符串实现仍然只有一份：本模块自己实现
``device_identity_key`` / ``resolve_device_identities``（2026-11-13 从
``sdr_backend`` 下沉，原处再导出），不复制规则。占用/参数记忆用 ``key``；
热插拔用 ``grade`` + ``port_key``。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: 身份分级（热插拔口径，与 ``usb_hotplug.GRADE_*`` 同值）。
GRADE_SERIAL = "serial"
GRADE_USB_PATH = "usb_path"
GRADE_ENDPOINT = "endpoint"
GRADE_PROVISIONAL = "provisional"

#: 身份质量（占用/参数记忆口径，与 ``sdr_backend.IDENTITY_*`` 同值）。
IDENTITY_STABLE = "stable"
IDENTITY_PROVISIONAL = "provisional"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _args(device: Dict[str, Any]) -> Dict[str, Any]:
    return (device or {}).get("device_args") or {}


def device_driver(device: Dict[str, Any]) -> str:
    return _text((device or {}).get("driver")) or "?"


def device_serial(device: Dict[str, Any]) -> str:
    return _text((device or {}).get("serial"))


def device_uri(device: Dict[str, Any]) -> str:
    device = device or {}
    return _text(device.get("uri")) or _text(_args(device).get("uri"))


def device_label(device: Dict[str, Any]) -> str:
    device = device or {}
    return (_text(device.get("label")) or _text(device.get("driver"))
            or "未知设备")


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


def port_key_of(device: Dict[str, Any]) -> str:
    """端口身份 ``<bus>-<port>``；枚举没给端口时返回空串（不猜）。

    来源只有两类，且与 ``device_identity_aliases`` 读同一批字段：
    ``uri:usb:<bus>.<port>`` 或显式的 ``bus``/``port``（``device_args`` 内亦可）。
    """
    device = device or {}
    path = usb_port_path(device_uri(device))
    if path:
        return path
    args = _args(device)
    bus = _text(device.get("bus") or device.get("usb_bus")
                or args.get("bus") or args.get("usb_bus"))
    port = _text(device.get("port") or device.get("usb_port")
                 or args.get("usb_port"))
    if bus and port:
        return f"{bus}-{port}"
    return ""


def manager_key(driver: str, serial: str, enum_index: int = -1) -> str:
    """``DeviceManager.DeviceInfo.device_key`` 的拼写**唯一定义**。

    与 canonical 键不是同一种字符串（``rtlsdr:SN`` vs ``rtlsdr|serial:SN``），
    原因见模块文档第 2 条；这里只保证不再出现第二份 f-string。
    """
    name = _text(driver) or "?"
    serial = _text(serial)
    if serial:
        return f"{name}:{serial}"
    try:
        index = int(enum_index)
    except (TypeError, ValueError):
        index = -1
    return f"{name}|index:{index}"


def manager_key_of(device: Dict[str, Any]) -> str:
    """枚举条目 → ``DeviceManager`` 口径的键（仅供对照/测试，不参与互斥）。"""
    device = device or {}
    raw = _text(_args(device).get("index", device.get("index")))
    try:
        index = int(raw) if raw else -1
    except (TypeError, ValueError):
        index = -1
    return manager_key(device_driver(device), device_serial(device), index)


# ─────────────────── 稳定身份键（桌面端与 AI 共用同一口径） ───────────────────
# 桌面端按身份键存连接参数与会话占用（desktop/connect_request_store.py），
# AI 按 device_id 持有后端。两边必须**对同一台物理设备算出同一个字符串**，
# 否则「两个进程抢同一根 USB 棒」的互斥只是各说各话：桌面端说占着 A，
# AI 说连的是 B，谁也没拦住谁。
#
# 语法逐字沿用桌面端既有实现，改动会让已存的连接请求失配：
#   driver|serial:<serial> > uri:<uri> > driver|index:<index> > driver|label:<label>
# 2026-11-13 从 ``mbdsdr_ai.sdr_backend`` 下沉到本模块（原处再导出）。
def device_identity_key(device: Dict[str, Any]) -> Tuple[str, str, str]:
    """``(键, 质量, 告警文案)``；质量见 ``IDENTITY_*``。

    只有序列号/URI 这类**跟着设备走**的标识算稳定；枚举序号拔插会变、
    显示名可能重名，都如实标 provisional 并给出可读告警。
    """
    driver = _text(device.get("driver")) or "?"
    serial = _text(device.get("serial"))
    args = device.get("device_args") or {}
    uri = _text(device.get("uri")) or _text(args.get("uri"))
    label = _text(device.get("label")) or driver
    if serial:
        return f"{driver}|serial:{serial}", IDENTITY_STABLE, ""
    if uri:
        return f"uri:{uri}", IDENTITY_STABLE, ""
    index_text = _text(args.get("index", device.get("index")))
    if index_text:
        return (f"{driver}|index:{index_text}", IDENTITY_PROVISIONAL,
                f"设备「{label}」没有序列号，按枚举序号 {index_text} 暂记连接参数；"
                "拔插或重新枚举后序号可能变化，参数不会跟随设备。")
    return (f"{driver}|label:{label}", IDENTITY_PROVISIONAL,
            f"设备「{label}」既无序列号也无枚举序号，按显示名暂记连接参数；"
            "同名设备的参数可能互相覆盖。")


def _identity_fingerprint(device: Dict[str, Any]) -> Tuple[str, ...]:
    # 与桌面端历史实现逐字一致（不 strip）：只影响撞键时 #n 的分配顺序。
    args = device.get("device_args") or {}
    return (str(device.get("driver") or ""), str(device.get("serial") or ""),
            str(device.get("uri") or ""), str(args.get("index", "")),
            str(device.get("label") or ""))


def resolve_device_identities(devices: List[Dict[str, Any]]
                              ) -> List[Tuple[str, str, str]]:
    """对**整份枚举**逐台算键；撞键按指纹排序加 ``#n``（与桌面端逐字一致）。

    键必须整份算：``#n`` 的去重结果依赖同一批设备的相对顺序，逐台算永远
    对不上（AI 旧的逐台 _keep 就拿不到这份上下文）。
    """
    items = list(devices or [])
    base = [device_identity_key(d) for d in items]
    groups: Dict[str, List[int]] = {}
    for i, (key, _quality, _warning) in enumerate(base):
        groups.setdefault(key, []).append(i)
    out: List[Tuple[str, str, str]] = [base[i] for i in range(len(items))]
    for key, idxs in groups.items():
        if len(idxs) < 2:
            continue
        warning = (f"设备身份键冲突：{len(idxs)} 台设备都映射到「{key}」；"
                   "已按枚举顺序加 #n 临时区分，拔插后参数可能错位。")
        order = sorted(idxs, key=lambda j: _identity_fingerprint(items[j]))
        for n, i in enumerate(order, 1):
            out[i] = (f"{key}#{n}", base[i][1], warning)
    return out


def device_identity_aliases(device: Dict[str, Any]) -> List[str]:
    """同一台设备可被认出的全部键（主键在前），用于跨进程占用匹配。

    为什么需要别名（2026-11-12 端到端实测发现）：**占着设备的那一方能读到
    序列号，来抢的那一方读不到**——RTL-SDR 被别的进程打开时，枚举拿不到
    serial（libusb 打不开），只能退到 ``driver|index:n``。于是持有者登记在
    ``rtlsdr|serial:...``，竞争者算出 ``rtlsdr|index:0``，两边对不上，互斥
    形同虚设（实测 AI 直接去 open，拿到的是 LIBUSB_ERROR_ACCESS）。

    所以登记时把「不依赖打开设备就能算出的其它身份」一并记下，判定占用时
    任一命中即视为同一台设备。
    """
    primary = device_identity_key(device)
    out = [primary[0]]
    driver = _text(device.get("driver")) or "?"
    args = device.get("device_args") or {}
    index_text = _text(args.get("index", device.get("index")))
    if index_text:
        out.append(f"{driver}|index:{index_text}")
    # USB 总线位置：设备被占用时**不需要 open 也读得到**（libusb 枚举即可），
    # 是 serial 读不到时最接近「物理那一台」的替代依据。只在枚举方提供了
    # bus/port 时才存在：原生 RTL 的 librtlsdr 不导出该信息，而 Python 侧的
    # pyusb/libusb 绑定**不在本项目依赖里**（requirements.txt 无此项、运行期
    # import 也失败）。为「插两根同型号」这一个场景新增运行期依赖，代价大于
    # 收益，故不引入——同型号多台时只能告警歧义、无法分辨是哪一台，
    # 该限制写在 docs/limitations-register.md 附 T 并出现在占用冲突文案里。
    bus = _text(device.get("bus") or device.get("usb_bus")
                or args.get("bus") or args.get("usb_bus"))
    port = _text(device.get("port") or device.get("usb_port")
                 or args.get("usb_port"))
    if bus and port:
        out.append(f"{driver}|bus:{bus}|port:{port}")
    label = _text(device.get("label"))
    if label:
        out.append(f"{driver}|label:{label}")
    seen, deduped = set(), []
    for key in out:
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def grade_of(device: Dict[str, Any]) -> Tuple[str, str]:
    """``(grade, 歧义/后果说明)``。说明是给用户看的「这个身份为什么会变」。"""
    device = device or {}
    label = device_label(device)
    if device_serial(device):
        return GRADE_SERIAL, ""
    uri = device_uri(device)
    if uri:
        if uri.lower().startswith("usb:"):
            return (GRADE_USB_PATH,
                    f"设备「{label}」没有序列号，按 USB 口路径 {uri} 识别；"
                    "换到另一个 USB 口会被当成另一台设备，旧口那台会被判定为已拔出。")
        return (GRADE_ENDPOINT,
                f"设备「{label}」按网络/文件地址 {uri} 识别；地址变了就是另一台。")
    index = _text(_args(device).get("index", device.get("index")))
    if index:
        return (GRADE_PROVISIONAL,
                f"设备「{label}」没有序列号，按枚举序号 {index} 识别；"
                "插拔同类设备或换口后序号会前移，可能被当成另一台。"
                "这类设备只报歧义，不判定拔出。")
    return (GRADE_PROVISIONAL,
            f"设备「{label}」既无序列号也无枚举序号，按显示名识别；"
            "同名设备无法区分。这类设备只报歧义，不判定拔出。")


@dataclass(frozen=True)
class DeviceIdentity:
    """一台设备身份结论。四个字段回答四个不同的问题，不可互相替代。

    ``key``      互斥/参数记忆用：跟着设备走（serial > uri > index > label）。
    ``port_key`` 热插拔换口检测用：这台设备此刻插在哪个 USB 口（空=不知道）。
    ``grade``    这个身份有多强（serial/usb_path/endpoint/provisional）。
    ``ambiguity`` 给用户的歧义或后果说明（空=没有需要提醒的）；等级说明优先。
    ``warning``  canonical 口径的告警（与 ``device_identity_key`` 逐字一致，
                 参数记忆/占用登记照抄这一条，避免出现第二套措辞）。
    """

    key: str
    quality: str
    grade: str
    port_key: str
    ambiguity: str
    warning: str
    aliases: Tuple[str, ...]
    label: str
    driver: str
    serial: str
    manager_key: str

    @property
    def provisional(self) -> bool:
        """只靠枚举序号/显示名：不能据此判「这一台拔出了」。"""
        return self.quality != IDENTITY_STABLE


def _canonical_key(device: Dict[str, Any]) -> Tuple[str, str, str]:
    """按本模块的稳定身份键算 canonical 键；解析失败如实降级，不抛。"""
    try:
        key, quality, warning = device_identity_key(dict(device or {}))
    except Exception as exc:  # noqa: BLE001
        return ("", IDENTITY_PROVISIONAL,
                f"身份解析失败（{type(exc).__name__}: {exc}）。")
    return (str(key or ""), str(quality or IDENTITY_PROVISIONAL),
            str(warning or ""))


def _aliases(device: Dict[str, Any]) -> Tuple[str, ...]:
    try:
        return tuple(device_identity_aliases(dict(device or {})))
    except Exception:  # noqa: BLE001 - 别名只是占用匹配的补充
        return ()


def _build(device: Dict[str, Any], key: str, quality: str,
           warning: str) -> DeviceIdentity:
    grade, note = grade_of(device)
    return DeviceIdentity(
        key=str(key or ""),
        quality=str(quality or IDENTITY_PROVISIONAL),
        grade=grade,
        port_key=port_key_of(device),
        ambiguity=note or str(warning or ""),
        warning=str(warning or ""),
        aliases=_aliases(device),
        label=device_label(device),
        driver=device_driver(device),
        serial=device_serial(device),
        manager_key=manager_key_of(device),
    )


def describe(device: Dict[str, Any], key: str = "",
             warning: str = "") -> DeviceIdentity:
    """**统一入口**：一台枚举条目 → 稳定身份 + 端口身份 + provisional + 歧义。

    ``key`` / ``warning`` 可传入已有结论（例如面板在连接时算好的键），避免
    单台重算与整份枚举的 ``#n`` 口径不一致。
    """
    base_key, quality, base_warning = _canonical_key(device)
    return _build(device, key or base_key, quality, warning or base_warning)


def describe_all(devices: Sequence[Dict[str, Any]]) -> List[DeviceIdentity]:
    """整份枚举逐台算（撞键 ``#n`` 依赖整份上下文）。顺序与入参一一对应。"""
    items = list(devices or [])
    try:
        resolved = resolve_device_identities([dict(d or {}) for d in items])
    except Exception as exc:  # noqa: BLE001 - 解析失败时全部如实降级
        logger.warning("统一身份解析失败，全部降级为 provisional：%s", exc)
        return [_build(device, f"unknown|index:{i}", IDENTITY_PROVISIONAL,
                       "统一身份解析不可用，本拍身份无法核对。")
                for i, device in enumerate(items)]
    return [_build(device, key, quality, warning)
            for device, (key, quality, warning) in zip(items, resolved)]


__all__ = [
    "GRADE_SERIAL", "GRADE_USB_PATH", "GRADE_ENDPOINT", "GRADE_PROVISIONAL",
    "IDENTITY_STABLE", "IDENTITY_PROVISIONAL",
    "DeviceIdentity", "describe", "describe_all",
    "grade_of", "port_key_of", "usb_port_path", "serial_from_key",
    "manager_key", "manager_key_of",
    "device_identity_key", "device_identity_aliases",
    "resolve_device_identities",
    "device_driver", "device_serial", "device_uri", "device_label",
]

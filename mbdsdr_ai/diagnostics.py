# SPDX-License-Identifier: MIT
"""只读诊断数据层（路线图 W106「诊断入口」）。

一个无参函数取全三块，供只读诊断页直接渲染：

1. **损坏备份清单**（含理由全文）——``corrupt_evidence`` 的清点结果；
2. **日志路径**——``desktop/startup_paths`` 的实际落点、候选顺序、内存缓冲；
3. **各模块最近一次持久化错误**——模块级 ``last_error()`` + 实例弱引用注册表。

三条红线（工单约束）：

* **只读**：不删备份、不建目录、不调 ``startup_log_path()``（它会 ``makedirs``）；
* **不改口径**：只认 ``*.reason.txt`` 成对证据。裸 ``*.corrupt-*`` 与站址
  ``.bak`` 不计入——前者是旧版本遗留、说不出原因，后者是冲突快照不是损坏，
  并进来会让「一个坏文件」在页面上出现两次；
* **不编造**：实例类对象（DesktopSettings / LayoutStore / BookmarkManager /
  FrequencyManager / VersionStore）在诊断页拿到窗口之前取不到，清单里明确
  标 ``unwired``，不拿空值冒充「无错误」。
"""

import importlib
import weakref
from typing import Any, Dict, List, Optional

from .corrupt_evidence import (
    REASON_SUFFIX, default_scan_directories, scan_corruption_evidence)

#: 模块级取数：``(展示名, 模块名, 取值函数名)``。
#: 这些模块自己持有最近一次错误，不需要窗口实例。
_MODULE_SOURCES = (
    ("config", "mbdsdr_ai.config", "last_error"),
    ("station_sync", "desktop.station_sync", "last_error"),
    ("command_palette", "desktop.command_palette", "recent_error"),
)

#: 由实例弱引用注册的对象（``Agent.__init__`` 里登记）。
_REGISTERED_NAMES = ("memory", "scheduler", "guardian", "workflow_engine")

#: 目前只有活动窗口能拿到实例、数据层还取不到的对象（如实标出来）。
_UNWIRED = (
    ("desktop_settings", "desktop/settings.py::DesktopSettings.last_error"),
    ("layout_store", "desktop/layout_store.py::LayoutStore.last_error"),
    ("bookmarks", "mbdsdr_ai/bookmark_manager.py::BookmarkManager.last_error"),
    ("frequency_manager", "mbdsdr_ai/frequency_manager.py::FrequencyManager.load_error"),
    ("version_store", "mbdsdr_ai/version_store.py::VersionStore.last_error"),
)

_REGISTERED: Dict[str, List["weakref.ref"]] = {}


def register_store(name: str, store: Any) -> None:
    """登记一个带 ``last_error`` 的长生命周期对象（弱引用，不接管生命周期）。

    取的永远是最近登记的存活实例；同一进程里旧实例已被回收时不报错。
    """
    try:
        ref = weakref.ref(store)
    except TypeError:      # 少数不可弱引用的替身对象（测试用）直接跳过
        return
    _REGISTERED.setdefault(name, []).append(ref)


def _last_registered(name: str) -> Optional[Any]:
    refs = [r for r in _REGISTERED.get(name, ()) if r() is not None]
    _REGISTERED[name] = refs
    return refs[-1]() if refs else None


def _scan_directories() -> List[str]:
    """清点目录：桌面设置所在目录 + 数据根与已知子目录。

    单独抽成函数便于测试替换；本身只算路径，不建目录。
    ``desktop.settings`` 不可用（无 Qt 的进程）时退回数据根目录，
    只会少扫设置目录，不会误报。
    """
    settings_path = ""
    try:
        from desktop.settings import default_config_path
        settings_path = default_config_path()
    except Exception:      # noqa: BLE001 - 无 Qt / 模块缺失都按「没有设置路径」处理
        settings_path = ""
    return default_scan_directories(settings_path)


def _collect_backups() -> Dict[str, Any]:
    directories = _scan_directories()
    report = scan_corruption_evidence(directories)
    items = []
    for reason_file in report["recent"]:
        try:
            with open(reason_file, "r", encoding="utf-8", errors="replace") as f:
                reason = f.read().strip()
        except OSError:
            reason = ""
        backup = (reason_file[:-len(REASON_SUFFIX)]
                  if reason_file.endswith(REASON_SUFFIX) else reason_file)
        items.append({"backup": backup, "reason_file": reason_file,
                      "reason": reason})
    return {
        "directories": directories,
        "total": report["total"],
        "stale": report["stale"],
        "items": items,
        "unreadable": report["unreadable"],
    }


def _collect_log() -> Dict[str, Any]:
    try:
        from desktop import startup_paths as sp
    except Exception:      # noqa: BLE001
        return {"path": "", "candidates": [], "buffered": [],
                "notice": "启动日志模块不可用"}
    path = sp.last_log_path()
    buffered = sp.buffered_log()
    if path:
        notice = ""
    elif buffered:
        notice = sp.persistent_log_note()
    else:
        notice = "本次进程尚未写入启动日志（不代表没有落点）"
    return {
        "path": path,
        "candidates": sp.log_candidates(),
        "buffered": buffered[-20:],
        "notice": notice,
    }


def _collect_errors() -> Dict[str, Any]:
    found = []
    for name, module_name, attr in _MODULE_SOURCES:
        try:
            module = importlib.import_module(module_name)
            value = getattr(module, attr)()
        except Exception:      # noqa: BLE001 - 模块未加载时跳过，不编造空错误
            continue
        if value:
            found.append({"name": name, "error": str(value), "source": "module"})

    for name in _REGISTERED_NAMES:
        store = _last_registered(name)
        value = getattr(store, "last_error", None) if store is not None else None
        if value:
            found.append({"name": name, "error": str(value), "source": "registry"})

    return {
        "items": found,
        "unwired": [{"name": name, "source": where} for name, where in _UNWIRED],
    }


def collect_diagnostics() -> Dict[str, Any]:
    """无参、只读：返回 ``{"backups", "log", "errors"}`` 三块完整结构。

    任何一块取数失败都不抛异常——诊断页拿到空块也总比整页打不开有用。
    """
    return {
        "backups": _collect_backups(),
        "log": _collect_log(),
        "errors": _collect_errors(),
    }

# SPDX-License-Identifier: MIT
"""桌面侧只读诊断取数（路线图 W106「诊断入口」）。

所属层：desktop —— 取数要碰 ``desktop.settings``（设置文件落点）、
``desktop.startup_paths``（启动日志）以及桌面模块的模块级 ``last_error``，
这些都是界面层事实；原先写在 ``mbdsdr_ai.diagnostics`` 里构成了
mbdsdr_ai -> desktop 的反向依赖（D1）。核心层只保留脱敏与注册表，
本模块负责把三块数据拼起来（守卫见 ``tests/test_import_direction_contract.py``）。

一个无参函数取全三块，供只读诊断页直接渲染：

1. **损坏备份清单**（含理由全文）——``corrupt_evidence`` 的清点结果；
2. **日志路径**——``desktop/startup_paths`` 的实际落点、候选顺序、内存缓冲；
3. **各模块最近一次持久化错误**——模块级 ``last_error()`` + 实例弱引用注册表。

红线（工单约束）：

* **只读**：不删备份、不建目录、不调 ``startup_log_path()``（它会 ``makedirs``）；
* **不改口径**：只认 ``*.reason.txt`` 成对证据。裸 ``*.corrupt-*`` 与站址
  ``.bak`` 不计入——前者是旧版本遗留、说不出原因，后者是冲突快照不是损坏，
  并进来会让「一个坏文件」在页面上出现两次；
* **不编造**：实例类对象（DesktopSettings / LayoutStore / BookmarkManager /
  FrequencyManager / VersionStore）在诊断页拿到窗口之前取不到，清单里明确
  标 ``unwired``，不拿空值冒充「无错误」；
* **不泄露密钥**：返回结构统一交给 ``mbdsdr_ai.diagnostics.redact_tree``
  脱敏，面板 / 日志 / 导出 / 截图共用同一份已脱敏结构。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List

try:  # 包上下文（测试 / 其它 desktop 模块）
    from . import startup_paths as sp
    from .settings import default_config_path
except ImportError:  # 脚本上下文（python desktop/main.py）
    import startup_paths as sp  # type: ignore[no-redef]
    from settings import default_config_path  # type: ignore[no-redef]

from mbdsdr_ai.corrupt_evidence import (
    REASON_SUFFIX, default_scan_directories, scan_corruption_evidence)
from mbdsdr_ai.diagnostics import (
    last_registered, redact_tree)

#: 模块级取数：``(展示名, 模块名, 取值函数名)``。
#: 这些模块自己持有最近一次错误，不需要窗口实例。
_MODULE_SOURCES = (
    ("config", "mbdsdr_ai.config", "last_error"),
    ("station_sync", "desktop.station_sync", "last_error"),
    ("command_palette", "desktop.command_palette", "recent_error"),
)

#: 由实例弱引用注册的对象（``Agent.__init__`` 里登记）。
_REGISTERED_NAMES = ("memory", "scheduler", "guardian", "workflow_engine")

#: 目前只有活动窗口能拿到实例、取数层还取不到的对象（如实标出来）。
_UNWIRED = (
    ("desktop_settings", "desktop/settings.py::DesktopSettings.last_error"),
    ("layout_store", "desktop/layout_store.py::LayoutStore.last_error"),
    ("bookmarks", "mbdsdr_ai/bookmark_manager.py::BookmarkManager.last_error"),
    ("frequency_manager", "mbdsdr_ai/frequency_manager.py::FrequencyManager.load_error"),
    ("version_store", "mbdsdr_ai/version_store.py::VersionStore.last_error"),
)


def scan_directories() -> List[str]:
    """清点目录：桌面设置所在目录 + 数据根与已知子目录。

    公共接缝：测试替换本函数即可换扫描根；本身只算路径，不建目录。
    """
    return default_scan_directories(default_config_path())


def _collect_backups() -> Dict[str, Any]:
    directories = scan_directories()
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
        store = last_registered(name)
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
    返回前在**数据层**统一脱敏：面板、日志、导出、截图都走同一份结构，
    不依赖调用方各自记得遮。
    """
    return redact_tree({
        "backups": _collect_backups(),
        "log": _collect_log(),
        "errors": _collect_errors(),
    })

# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端用户布局持久化（JSON）
=====================================

把用户自定义布局（整套停靠/页签/比例状态）落到用户配置目录的 ``layouts.json``，
跨平台自动定位：

  * Linux   ``~/.config/MBDSDR/layouts.json``
  * Windows ``%APPDATA%/MBDSDR/layouts.json``
  * macOS   ``~/Library/Application Support/MBDSDR/layouts.json``

定位优先用 ``QStandardPaths.AppConfigLocation``，失败时按平台手动兜底。

文件结构::

    {
      "version": 1,
      "active": "我的布局",
      "presets": {
        "专注":   {"builtin": true,  "data": {...}},
        "我的布局": {"builtin": false, "created": "2026-09-27T...", "data": {...}}
      }
    }

``data`` 是 :class:`DockLayoutManager.capture_layout()` 返回的可 JSON 序列化字典
（QMainWindow.saveState / QSplitter.saveState 的 QByteArray 已转 base64 字符串，
外加中央 Tab 页签顺序 / 当前索引 / 三栏 splitter sizes / 面板可见性）。

红线：读写失败绝不崩溃，一律返回 False 并打 warning；不造业务数据。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QByteArray, QStandardPaths

logger = logging.getLogger("mbdsdr.layout_store")

#: 内置预设（中文显示名，与工具栏下拉一致）。不可删除。
BUILTIN_LAYOUTS: List[str] = ["专注", "分析", "网格"]

_VERSION = 1


def _qbyte_to_b64(ba: Optional[QByteArray]) -> str:
    """QByteArray -> base64 ascii 字符串（None/空 -> ""）。"""
    if not ba:
        return ""
    try:
        return base64.b64encode(bytes(ba)).decode("ascii")
    except Exception:
        return ""


def _b64_to_qbyte(s: str) -> QByteArray:
    """base64 ascii 字符串 -> QByteArray（空串/失败 -> 空 QByteArray）。"""
    if not s:
        return QByteArray()
    try:
        return QByteArray(base64.b64decode(s.encode("ascii")))
    except Exception:
        return QByteArray()


class LayoutStore:
    """用户布局 JSON 持久化仓库（可注入路径，便于 offscreen 测试）。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or self._default_path()

    # ------------------------------------------------------------------
    # 路径定位
    # ------------------------------------------------------------------
    @staticmethod
    def _default_path() -> str:
        """跨平台定位 layouts.json（QStandardPaths.AppConfigLocation 优先）。"""
        base = ""
        try:
            base = QStandardPaths.writableLocation(
                QStandardPaths.AppConfigLocation) or ""
        except Exception:
            base = ""
        if base:
            base = base.rstrip("/\\")
            # 确保落在 MBDSDR 子目录（QStandardPaths 可能只返回到 App 名那层）
            if os.path.basename(base) != "MBDSDR":
                base = os.path.join(base, "MBDSDR")
            return os.path.join(base, "layouts.json")
        # 手动兜底
        if sys.platform == "win32":
            root = os.environ.get("APPDATA") or os.path.expanduser("~")
            d = os.path.join(root, "MBDSDR")
        elif sys.platform == "darwin":
            d = os.path.expanduser("~/Library/Application Support/MBDSDR")
        else:
            d = os.path.expanduser("~/.config/MBDSDR")
        return os.path.join(d, "layouts.json")

    @property
    def path(self) -> str:
        return self._path

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    def _load(self) -> Dict[str, Any]:
        """读 JSON；文件不存在/损坏时返回空骨架，不抛异常。"""
        empty: Dict[str, Any] = {"version": _VERSION, "active": BUILTIN_LAYOUTS[0],
                                  "presets": {}}
        if not os.path.exists(self._path):
            return empty
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if not isinstance(obj, dict) or not isinstance(obj.get("presets"), dict):
                return empty
            obj.setdefault("active", BUILTIN_LAYOUTS[0])
            obj.setdefault("version", _VERSION)
            return obj
        except Exception as e:  # noqa: BLE001
            logger.warning("layout_store 读取失败 %s: %s", self._path, e)
            return empty

    def _save(self, obj: Dict[str, Any]) -> bool:
        """写 JSON（先写临时文件再替换，避免半写损坏）。失败返回 False。"""
        try:
            d = os.path.dirname(self._path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("layout_store 写入失败 %s: %s", self._path, e)
            return False

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def list_presets(self) -> List[str]:
        """所有布局名：内置 + 用户（内置在前，顺序固定）。"""
        obj = self._load()
        presets = obj.get("presets", {})
        names: List[str] = []
        for b in BUILTIN_LAYOUTS:
            names.append(b)
        for name in presets:
            if name not in names:
                names.append(name)
        return names

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_LAYOUTS

    def get_preset_data(self, name: str) -> Optional[Dict[str, Any]]:
        """取某布局的 data 字典（内置若无 data 则 None，由 manager 走构建逻辑）。"""
        obj = self._load()
        entry = obj.get("presets", {}).get(name)
        if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
            return entry["data"]
        return None

    def save_preset(self, name: str, data: Dict[str, Any]) -> bool:
        """保存（覆盖）一个布局。内置名允许被同名自定义覆盖。"""
        if not name:
            return False
        obj = self._load()
        presets = obj.setdefault("presets", {})
        existing = presets.get(name)
        builtin = bool(self.is_builtin(name))
        entry: Dict[str, Any] = {"builtin": builtin, "data": data}
        # 用户自定义保留 created；内置若被用户覆盖则降级为自定义（builtin=False）
        if builtin:
            entry["builtin"] = False
        elif isinstance(existing, dict) and existing.get("created"):
            entry["created"] = existing["created"]
        else:
            entry["created"] = datetime.now().isoformat(timespec="seconds")
        presets[name] = entry
        obj["active"] = name
        return self._save(obj)

    def load_preset(self, name: str) -> Optional[Dict[str, Any]]:
        """别名：取布局 data。"""
        return self.get_preset_data(name)

    def delete_preset(self, name: str) -> bool:
        """删除用户布局；内置不可删。active 若指向被删项则回退到首个内置。"""
        if self.is_builtin(name):
            logger.warning("内置布局不可删除: %s", name)
            return False
        obj = self._load()
        presets = obj.get("presets", {})
        if name not in presets:
            return False
        presets.pop(name, None)
        if obj.get("active") == name:
            obj["active"] = BUILTIN_LAYOUTS[0]
        return self._save(obj)

    def rename_preset(self, old: str, new: str) -> bool:
        """重命名用户布局（内置不可改名；new 不能为空/不可撞内置）。"""
        if self.is_builtin(old) or not new or self.is_builtin(new):
            return False
        obj = self._load()
        presets = obj.get("presets", {})
        if old not in presets or new in presets:
            return False
        presets[new] = presets.pop(old)
        if obj.get("active") == old:
            obj["active"] = new
        return self._save(obj)

    def set_active(self, name: str) -> bool:
        """记录当前激活布局名（不要求该布局已存在 data）。"""
        obj = self._load()
        obj["active"] = name
        return self._save(obj)

    def get_active(self) -> str:
        obj = self._load()
        active = obj.get("active") or BUILTIN_LAYOUTS[0]
        if active not in self.list_presets():
            active = BUILTIN_LAYOUTS[0]
        return active

    def reset_to_factory(self) -> bool:
        """清除所有用户预设，active 回到首个内置。"""
        obj: Dict[str, Any] = {"version": _VERSION,
                               "active": BUILTIN_LAYOUTS[0], "presets": {}}
        return self._save(obj)


# ----------------------------------------------------------------------
# 模块级单例（默认路径）
# ----------------------------------------------------------------------
_default_store: Optional[LayoutStore] = None


def layout_store() -> LayoutStore:
    """返回默认路径的全局 LayoutStore 单例。"""
    global _default_store
    if _default_store is None:
        _default_store = LayoutStore()
    return _default_store


def reset_layout_store_singleton() -> None:
    """测试用：丢弃单例，下次 layout_store() 重新读默认路径。"""
    global _default_store
    _default_store = None

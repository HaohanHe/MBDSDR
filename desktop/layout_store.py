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

try:  # 常规：仓库根在 sys.path 上（main.py / 测试都这么进来）
    from mbdsdr_ai.corrupt_evidence import write_reason
except ImportError:  # pragma: no cover - 兼容只把 desktop/ 放进 sys.path 的调用方
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from mbdsdr_ai.corrupt_evidence import write_reason

logger = logging.getLogger("mbdsdr.layout_store")

#: 内置预设（中文显示名，与工具栏下拉一致）。不可删除。
BUILTIN_LAYOUTS: List[str] = ["专注", "分析", "网格"]

_VERSION = 1


def backup_corrupt_layout(path: str) -> Optional[str]:
    """把损坏的布局文件改名成 ``*.corrupt-时间戳``，返回备份路径（失败 None）。

    与书签 / 桌面配置同一套口径：只改名不删除，原件字节原样留在备份里；
    改名失败返回 None，调用方据此拒绝写盘，避免空骨架覆盖唯一证据。

    这是损坏留证的**公共接缝**：测试模拟「备份失败」时替换本函数返回
    ``None``，不再改模块私有名。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.corrupt-{stamp}"
    try:
        os.replace(path, backup)
    except OSError as exc:
        logger.error("layout_store 损坏文件备份失败：%s（%s）", path, exc)
        return None
    return backup


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
        #: 上次读取失败的人话说明（None = 正常）；调用方/状态栏据此提示。
        self.last_error: Optional[str] = None
        #: 损坏文件的备份路径（None = 未损坏或备份失败）。
        self.corrupt_backup: Optional[str] = None
        #: 读盘失败或损坏且备份失败时拒绝写盘。每个公开操作都会重新 _load()，
        #: 所以这个状态必须挂在实例上，不能只活在单次 _load() 的局部变量里。
        self._save_blocked = False

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
    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {"version": _VERSION, "active": BUILTIN_LAYOUTS[0], "presets": {}}

    def _load(self) -> Dict[str, Any]:
        """读 JSON，不抛异常；必须分清「文件缺失」「文件损坏」「读不了」。

          * 缺失 —— 首次启动的正常路径，给空骨架，不算错误，也不阻塞写入；
          * 损坏（解析失败 / 顶层结构非法）—— 先备份原件、留人话 ``last_error``；
            备份失败则置 ``_save_blocked``，拒绝用空骨架覆盖唯一证据；
          * OSError（被占用 / 是目录 / 无权限）—— 不是损坏，不备份、不写入；
            同样拒绝写盘，否则一次临时读失败就会把它当成空文件整份覆盖。
        """
        if not os.path.exists(self._path):
            return self._empty()
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except OSError as exc:
            self.last_error = (
                f"布局文件读取失败（{exc.__class__.__name__}）：{self._path}；"
                "本次不写入，避免覆盖可能仍可恢复的文件")
            self._save_blocked = True
            logger.warning("layout_store 读取失败（非损坏，不备份）：%s", exc)
            return self._empty()
        except ValueError as exc:
            self._mark_corrupt(f"解析失败（{exc.__class__.__name__}：{exc}）")
            return self._empty()
        if not isinstance(obj, dict) or not isinstance(obj.get("presets"), dict):
            self._mark_corrupt(f"顶层结构非法（{type(obj).__name__}）")
            return self._empty()
        obj.setdefault("active", BUILTIN_LAYOUTS[0])
        obj.setdefault("version", _VERSION)
        # 读到合法文件 = 上一次的读失败/损坏已经过去，解除阻塞。
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False
        return obj

    def _mark_corrupt(self, reason: str) -> None:
        """损坏文件：先备份原件再留人话错误；备份失败则禁止后续写盘。"""
        self.corrupt_backup = backup_corrupt_layout(self._path)
        self._save_blocked = self.corrupt_backup is None
        message = (
            f"布局文件读取失败（{reason}）：{self._path}；"
            f"原文件已备份为 {self.corrupt_backup or '（备份失败）'}，"
            "本次按空布局启动")
        # 备份已成功 → 才写理由；理由写失败只记日志，不影响备份本身。
        if self.corrupt_backup:
            write_reason(self.corrupt_backup, message, logger)
        self.last_error = message
        logger.warning("布局文件不可用（%s）：%s；备份=%s", self._path, reason,
                       self.corrupt_backup or "（备份失败）")

    def _save(self, obj: Dict[str, Any]) -> bool:
        """写 JSON（先写临时文件再替换，避免半写损坏）。失败返回 False。"""
        if self._save_blocked:
            self.last_error = (
                f"布局未保存：{self._path} 读取失败或已损坏且无法备份；"
                "为避免覆盖唯一证据，本次不写入。"
                "请检查目录权限或手动移走该文件后重试")
            logger.error("layout_store 写入被拒绝（文件不可用且未备份）：%s",
                         self._path)
            return False
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

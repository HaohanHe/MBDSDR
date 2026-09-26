"""
mbdsdr_ai/plugin_manager.py — 插件系统
=======================================

移植自 SDRangel（repos/sdrangel/sdrbase/plugin/）：
  - plugininterface.h:23  PluginDescriptor      → Plugin 基类 + plugin.json
  - pluginmanager.cpp:222 loadPluginsDir      → PluginManager.discover/load
  - 坏插件跳过、不致命                          → discover/load 捕获异常进 failed[]

插件描述文件 plugin.json：
  {
    "name": "am_demod",
    "version": "1.0.0",
    "type": "demod",          // source | channel | demod | tool
    "entry_point": "mod:AMDemodPlugin"
  }

我们的增强：
  * Python 插件**热重载**：reload() 删除 sys.modules 缓存后重新 import，改代码不重启
    （SDRangel 的 .so 必须重启）。
  * 每个插件可挂载任意 api 对象（如 WebServer / SDRBackend）。
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PLUGIN_TYPES = ("source", "channel", "demod", "tool")


# --------------------------------------------------------------------------- #
class Plugin:
    """插件基类（对照 SDRangel PluginInterface）。"""

    #: 插件类型，见 PLUGIN_TYPES
    kind: str = "tool"
    name: str = "unnamed"
    version: str = "0.0.0"

    def init(self, api: Any = None) -> None:
        """注入宿主 api（WebServer / SDRBackend 等）。"""

    def start(self) -> None:
        """启动插件工作。"""

    def stop(self) -> None:
        """停止并释放资源。"""

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "version": self.version, "type": self.kind}


class PluginLoadError(Exception):
    """插件加载失败（被 PluginManager 捕获，不致命）。"""


@dataclass
class PluginSpec:
    path: str          # plugin.json 绝对路径
    directory: str     # 插件目录
    name: str
    version: str
    type: str
    entry_point: str   # "module:ClassName"


# --------------------------------------------------------------------------- #
class PluginManager:
    """扫描目录、动态 import、注册、枚举、热重载。线程安全。"""

    def __init__(self, search_dirs: Optional[List[str]] = None):
        self._dirs: List[str] = list(search_dirs or [])
        self._lock = threading.RLock()
        self._specs: Dict[str, PluginSpec] = {}
        self._instances: Dict[str, Plugin] = {}
        self._failed: Dict[str, str] = {}
        # 记录我们按文件路径加载进来的模块名，便于热重载时清缓存
        self._loaded_modules: Dict[str, str] = {}

    # -- discovery -------------------------------------------------------- #
    def add_dir(self, directory: str) -> None:
        with self._lock:
            if directory not in self._dirs:
                self._dirs.append(directory)

    def discover(self) -> Dict[str, PluginSpec]:
        """扫描所有 search_dirs，读取 plugin.json；无效的记入 failed，不抛。"""
        with self._lock:
            self._specs.clear()
            self._failed.clear()
            for d in self._dirs:
                self._scan_dir(d)
            return dict(self._specs)

    def _scan_dir(self, directory: str) -> None:
        if not os.path.isdir(directory):
            return
        for entry in sorted(os.listdir(directory)):
            sub = os.path.join(directory, entry)
            manifest = os.path.join(sub, "plugin.json")
            if not os.path.isdir(sub) or not os.path.isfile(manifest):
                continue
            try:
                with open(manifest, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                name = meta["name"]
                ptype = meta["type"]
                if ptype not in PLUGIN_TYPES:
                    raise PluginLoadError(f"invalid type '{ptype}'")
                spec = PluginSpec(
                    path=manifest,
                    directory=sub,
                    name=name,
                    version=str(meta.get("version", "0.0.0")),
                    type=ptype,
                    entry_point=meta["entry_point"],
                )
                self._specs[name] = spec
            except Exception as e:  # noqa: BLE001 —— 坏插件跳过
                self._failed[entry or manifest] = f"{type(e).__name__}: {e}"
                logger.warning("skip invalid plugin %s: %s", manifest, e)

    # -- loading ---------------------------------------------------------- #
    def _resolve_entry(self, spec: PluginSpec):
        """按 spec.entry_point 加载模块并取插件类。"""
        if ":" not in spec.entry_point:
            raise PluginLoadError(f"entry_point must be 'module:Class', got {spec.entry_point!r}")
        mod_name, cls_name = spec.entry_point.split(":", 1)
        mod_file = os.path.join(spec.directory, mod_name + ".py")

        if os.path.isfile(mod_file):
            # 按文件路径加载（插件自带 .py）；记录模块名供热重载
            unique = f"mbdsdr_plugin_{spec.name}_{os.path.basename(spec.directory)}"
            if unique in sys.modules:
                del sys.modules[unique]
            spec_obj = importlib.util.spec_from_file_location(unique, mod_file)
            if spec_obj is None or spec_obj.loader is None:
                raise PluginLoadError(f"cannot load {mod_file}")
            module = importlib.util.module_from_spec(spec_obj)
            sys.modules[unique] = module
            spec_obj.loader.exec_module(module)
            self._loaded_modules[spec.name] = unique
        else:
            # 退化为 sys.path 上的普通模块
            module = importlib.import_module(mod_name)

        if not hasattr(module, cls_name):
            raise PluginLoadError(f"{mod_name} has no {cls_name}")
        cls = getattr(module, cls_name)
        if not inspect.isclass(cls) or not issubclass(cls, Plugin):
            raise PluginLoadError(f"{cls_name} is not a Plugin subclass")
        return cls

    def load(self, name: str, api: Any = None) -> Plugin:
        """加载并实例化一个已 discover 的插件。失败记入 failed。"""
        with self._lock:
            if name in self._instances:
                return self._instances[name]
            spec = self._specs.get(name)
            if spec is None:
                raise PluginLoadError(f"unknown plugin '{name}' (run discover() first)")
            try:
                cls = self._resolve_entry(spec)
                inst = cls()
                # 允许插件被 manifest 覆盖 name/version/type
                inst.name = spec.name
                inst.version = spec.version
                inst.kind = spec.type
                inst.init(api)
                self._instances[name] = inst
                self._failed.pop(name, None)
                return inst
            except Exception as e:  # noqa: BLE001
                self._failed[name] = f"{type(e).__name__}: {e}"
                logger.warning("plugin %s failed to load: %s", name, e)
                raise

    def load_all(self, api: Any = None) -> None:
        for name in list(self._specs.keys()):
            try:
                self.load(name, api=api)
            except PluginLoadError:
                pass

    # -- lifecycle -------------------------------------------------------- #
    def start(self, name: str) -> None:
        with self._lock:
            inst = self._instances.get(name)
            if inst is None:
                raise PluginLoadError(f"plugin '{name}' not loaded")
            inst.start()

    def stop(self, name: str) -> None:
        with self._lock:
            inst = self._instances.get(name)
            if inst is not None:
                inst.stop()

    def unload(self, name: str) -> None:
        with self._lock:
            inst = self._instances.pop(name, None)
            if inst is not None:
                try:
                    inst.stop()
                except Exception:  # pragma: no cover
                    logger.exception("plugin %s stop() raised", name)

    def reload(self, name: str, api: Any = None) -> Plugin:
        """热重载：stop + 卸载模块缓存 + 重新 import。改代码不重启宿主。"""
        with self._lock:
            self.unload(name)
            mod = self._loaded_modules.pop(name, None)
            if mod and mod in sys.modules:
                del sys.modules[mod]
            return self.load(name, api=api)

    # -- queries ---------------------------------------------------------- #
    def get(self, name: str) -> Optional[Plugin]:
        with self._lock:
            return self._instances.get(name)

    def list(self, ptype: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for name, inst in sorted(self._instances.items()):
                if ptype is not None and inst.kind != ptype:
                    continue
                out.append(inst.describe())
            return out

    def discovered(self) -> List[str]:
        with self._lock:
            return sorted(self._specs.keys())

    def failed(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._failed)

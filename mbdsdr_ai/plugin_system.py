"""
MBDSDR AI 内核 - 模块化插件系统
================================
PluginSystem：动态加载第三方插件，支持即插即用。

对照白皮书第九章 9.3 模块化插件系统。

核心概念：
- Plugin：插件，包含元数据 + 工具 + 钩子 + 子代理
- PluginManager：插件管理器，加载/卸载/启用/禁用插件
- PluginManifest：插件清单（元数据）

插件类型：
- SDR 后端插件：支持新的 SDR 硬件
- 解码器插件：支持新的数字模式解码
- DSP 插件：新的信号处理算法
- AI 插件：新的 AI 模型/工具
- UI 插件：新的界面组件
- 工作流插件：预设的工作流模板

插件格式：
- 目录结构：plugin_name/
  - manifest.json（元数据）
  - __init__.py（入口）
  - tools.py（工具定义）
  - hooks.py（钩子定义）
- 或者单个 .py 文件

用途：
- 用户可以自己写插件扩展功能
- 社区贡献的插件可以即插即用
- 类似创意工坊的玩法（用户投稿 → 专家委员会审查 → 合入）
"""

import json
import time
import os
import importlib
import importlib.util
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable, Type
from enum import Enum


class PluginStatus(str, Enum):
    """插件状态。"""
    LOADED = "loaded"  # 已加载
    ENABLED = "enabled"  # 已启用
    DISABLED = "disabled"  # 已禁用
    ERROR = "error"  # 加载错误
    UNLOADED = "unloaded"  # 已卸载


class PluginType(str, Enum):
    """插件类型。"""
    SDR_BACKEND = "sdr_backend"  # SDR 后端
    DECODER = "decoder"  # 解码器
    DSP = "dsp"  # 信号处理
    AI_TOOL = "ai_tool"  # AI 工具
    WORKFLOW = "workflow"  # 工作流
    UI = "ui"  # 界面
    GENERAL = "general"  # 通用


@dataclass
class PluginManifest:
    """插件清单（元数据）。"""
    name: str  # 插件名
    version: str  # 版本
    description: str = ""  # 描述
    author: str = ""  # 作者
    plugin_type: PluginType = PluginType.GENERAL  # 类型
    entry_point: str = ""  # 入口模块/函数
    dependencies: List[str] = field(default_factory=list)  # 依赖
    tags: List[str] = field(default_factory=list)
    min_mbdsdr_version: str = "0.1.0"  # 最低 MBDSDR 版本
    license: str = ""  # 许可证
    homepage: str = ""  # 主页

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "plugin_type": self.plugin_type.value,
            "entry_point": self.entry_point,
            "dependencies": self.dependencies,
            "tags": self.tags,
            "min_mbdsdr_version": self.min_mbdsdr_version,
            "license": self.license,
            "homepage": self.homepage,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginManifest":
        return cls(
            name=data["name"],
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            plugin_type=PluginType(data.get("plugin_type", "general")),
            entry_point=data.get("entry_point", ""),
            dependencies=data.get("dependencies", []),
            tags=data.get("tags", []),
            min_mbdsdr_version=data.get("min_mbdsdr_version", "0.1.0"),
            license=data.get("license", ""),
            homepage=data.get("homepage", ""),
        )


@dataclass
class Plugin:
    """插件实例。"""
    manifest: PluginManifest
    status: PluginStatus = PluginStatus.LOADED
    module: Any = None  # 加载的 Python 模块
    tools: List[Dict[str, Any]] = field(default_factory=list)  # 注册的工具
    hooks: List[Dict[str, Any]] = field(default_factory=list)  # 注册的钩子
    subagents: List[Dict[str, Any]] = field(default_factory=list)  # 注册的子代理
    error: str = ""  # 加载错误信息
    loaded_at: float = 0.0
    enabled_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "status": self.status.value,
            "tools_count": len(self.tools),
            "hooks_count": len(self.hooks),
            "subagents_count": len(self.subagents),
            "error": self.error,
            "loaded_at": self.loaded_at,
            "enabled_at": self.enabled_at,
        }


class PluginManager:
    """
    插件管理器。

    负责加载、卸载、启用、禁用插件。
    插件可以注册工具、钩子、子代理。
    """

    def __init__(
        self,
        plugin_dirs: List[str] = None,
        tool_registry=None,
        hook_manager=None,
        subagent_manager=None,
    ):
        if plugin_dirs is None:
            plugin_dirs = [
                os.path.expanduser("~/.mbdsdr/plugins"),
                os.path.join(os.path.dirname(__file__), "plugins"),
            ]
        self.plugin_dirs = plugin_dirs
        self.tool_registry = tool_registry
        self.hook_manager = hook_manager
        self.subagent_manager = subagent_manager

        self.plugins: Dict[str, Plugin] = {}  # name -> plugin
        self._plugin_counter = 0

        # 确保插件目录存在
        for d in plugin_dirs:
            os.makedirs(d, exist_ok=True)

    def discover_plugins(self) -> List[str]:
        """
        发现所有可用插件（扫描插件目录）。

        返回插件名称列表。
        """
        discovered = []
        for plugin_dir in self.plugin_dirs:
            if not os.path.exists(plugin_dir):
                continue
            for item in os.listdir(plugin_dir):
                item_path = os.path.join(plugin_dir, item)
                # 目录插件（含 manifest.json）
                if os.path.isdir(item_path):
                    manifest_path = os.path.join(item_path, "manifest.json")
                    if os.path.exists(manifest_path):
                        discovered.append(item)
                # 单文件插件（.py）
                elif item.endswith('.py') and not item.startswith('_'):
                    discovered.append(item[:-3])  # 去掉 .py
        return list(set(discovered))

    def load_plugin(self, plugin_name: str, plugin_path: str = None) -> Plugin:
        """
        加载一个插件。

        plugin_name: 插件名
        plugin_path: 插件路径（可选，自动搜索）
        """
        if plugin_name in self.plugins and self.plugins[plugin_name].status != PluginStatus.UNLOADED:
            return self.plugins[plugin_name]

        # 查找插件路径
        if plugin_path is None:
            plugin_path = self._find_plugin_path(plugin_name)

        if plugin_path is None:
            raise ValueError(f"插件未找到: {plugin_name}")

        # 加载清单
        manifest = self._load_manifest(plugin_name, plugin_path)

        # 加载 Python 模块
        module = None
        error = ""
        try:
            module = self._load_module(plugin_name, plugin_path)
        except Exception as e:
            error = f"模块加载失败: {type(e).__name__}: {str(e)}"

        plugin = Plugin(
            manifest=manifest,
            status=PluginStatus.ERROR if error else PluginStatus.LOADED,
            module=module,
            error=error,
            loaded_at=time.time(),
        )

        self.plugins[plugin_name] = plugin
        return plugin

    def enable_plugin(self, plugin_name: str) -> bool:
        """
        启用插件：注册其工具、钩子、子代理。
        """
        plugin = self.plugins.get(plugin_name)
        if not plugin or plugin.status == PluginStatus.ERROR:
            return False

        if plugin.status == PluginStatus.ENABLED:
            return True

        try:
            # 调用插件的 register 函数
            if plugin.module and hasattr(plugin.module, 'register'):
                register_result = plugin.module.register(
                    tool_registry=self.tool_registry,
                    hook_manager=self.hook_manager,
                    subagent_manager=self.subagent_manager,
                )
                if register_result:
                    plugin.tools = register_result.get('tools', [])
                    plugin.hooks = register_result.get('hooks', [])
                    plugin.subagents = register_result.get('subagents', [])

            plugin.status = PluginStatus.ENABLED
            plugin.enabled_at = time.time()
            return True
        except Exception as e:
            plugin.error = f"启用失败: {type(e).__name__}: {str(e)}"
            plugin.status = PluginStatus.ERROR
            return False

    def disable_plugin(self, plugin_name: str) -> bool:
        """
        禁用插件：注销其工具、钩子、子代理。
        """
        plugin = self.plugins.get(plugin_name)
        if not plugin or plugin.status != PluginStatus.ENABLED:
            return False

        try:
            # 调用插件的 unregister 函数
            if plugin.module and hasattr(plugin.module, 'unregister'):
                plugin.module.unregister(
                    tool_registry=self.tool_registry,
                    hook_manager=self.hook_manager,
                    subagent_manager=self.subagent_manager,
                )

            plugin.status = PluginStatus.DISABLED
            plugin.tools = []
            plugin.hooks = []
            plugin.subagents = []
            return True
        except Exception as e:
            plugin.error = f"禁用失败: {type(e).__name__}: {str(e)}"
            return False

    def unload_plugin(self, plugin_name: str) -> bool:
        """卸载插件。"""
        plugin = self.plugins.get(plugin_name)
        if not plugin:
            return False

        if plugin.status == PluginStatus.ENABLED:
            self.disable_plugin(plugin_name)

        plugin.status = PluginStatus.UNLOADED
        plugin.module = None
        return True

    def load_all_plugins(self, auto_enable: bool = True) -> List[str]:
        """
        加载并启用所有发现的插件。

        返回成功加载的插件名列表。
        """
        discovered = self.discover_plugins()
        loaded = []
        for name in discovered:
            try:
                plugin = self.load_plugin(name)
                if plugin.status != PluginStatus.ERROR:
                    if auto_enable:
                        self.enable_plugin(name)
                    loaded.append(name)
            except Exception:
                pass
        return loaded

    def list_plugins(self) -> List[Dict[str, Any]]:
        """列出所有插件。"""
        return [p.to_dict() for p in self.plugins.values()]

    def get_plugin(self, plugin_name: str) -> Optional[Plugin]:
        return self.plugins.get(plugin_name)

    def get_stats(self) -> Dict[str, Any]:
        """获取插件管理器统计信息。"""
        total = len(self.plugins)
        enabled = sum(1 for p in self.plugins.values() if p.status == PluginStatus.ENABLED)
        disabled = sum(1 for p in self.plugins.values() if p.status == PluginStatus.DISABLED)
        errors = sum(1 for p in self.plugins.values() if p.status == PluginStatus.ERROR)
        total_tools = sum(len(p.tools) for p in self.plugins.values())
        total_hooks = sum(len(p.hooks) for p in self.plugins.values())

        by_type = {}
        for p in self.plugins.values():
            t = p.manifest.plugin_type.value
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "total_plugins": total,
            "enabled": enabled,
            "disabled": disabled,
            "errors": errors,
            "total_tools_registered": total_tools,
            "total_hooks_registered": total_hooks,
            "plugins_by_type": by_type,
            "plugin_dirs": self.plugin_dirs,
        }

    def install_plugin_from_path(self, source_path: str, plugin_name: str = None) -> str:
        """
        从路径安装插件（复制到插件目录）。

        这是"创意工坊"玩法的核心：用户投稿 → 安装到本地。
        """
        if plugin_name is None:
            plugin_name = os.path.basename(source_path.rstrip('/'))

        target_dir = os.path.join(self.plugin_dirs[0], plugin_name)
        os.makedirs(target_dir, exist_ok=True)

        # 复制文件（简化版，实际应该用 shutil.copytree）
        import shutil
        if os.path.isdir(source_path):
            for item in os.listdir(source_path):
                src = os.path.join(source_path, item)
                dst = os.path.join(target_dir, item)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                elif os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
        elif os.path.isfile(source_path):
            shutil.copy2(source_path, os.path.join(target_dir, os.path.basename(source_path)))

        return plugin_name

    def _find_plugin_path(self, plugin_name: str) -> Optional[str]:
        """查找插件路径。"""
        for plugin_dir in self.plugin_dirs:
            # 目录插件
            dir_path = os.path.join(plugin_dir, plugin_name)
            if os.path.isdir(dir_path):
                return dir_path
            # 单文件插件
            file_path = os.path.join(plugin_dir, f"{plugin_name}.py")
            if os.path.isfile(file_path):
                return file_path
        return None

    def _load_manifest(self, plugin_name: str, plugin_path: str) -> PluginManifest:
        """加载插件清单。"""
        manifest_path = None
        if os.path.isdir(plugin_path):
            manifest_path = os.path.join(plugin_path, "manifest.json")

        if manifest_path and os.path.exists(manifest_path):
            with open(manifest_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return PluginManifest.from_dict(data)

        # 没有 manifest，创建默认的
        return PluginManifest(
            name=plugin_name,
            version="0.1.0",
            description=f"自动发现的插件: {plugin_name}",
            entry_point=plugin_name,
        )

    def _load_module(self, plugin_name: str, plugin_path: str) -> Any:
        """加载 Python 模块。"""
        if os.path.isdir(plugin_path):
            # 目录插件：加载 __init__.py 或 entry_point 指定的模块
            init_path = os.path.join(plugin_path, "__init__.py")
            if os.path.exists(init_path):
                spec = importlib.util.spec_from_file_location(
                    f"mbdsdr_plugin_{plugin_name}", init_path
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
        else:
            # 单文件插件
            spec = importlib.util.spec_from_file_location(
                f"mbdsdr_plugin_{plugin_name}", plugin_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        raise ImportError(f"无法加载插件模块: {plugin_path}")

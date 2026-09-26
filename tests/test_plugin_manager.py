"""
test_plugin_manager.py — PluginManager 注册/加载/枚举/热重载 单测。

对照 SDRangel sdrbase/plugin/pluginmanager.cpp:222 loadPluginsDir：
  * 坏插件（坏 JSON / 缺 entry_point / 类不存在 / 不是 Plugin 子类）安全跳过
  * discover() 扫描 plugin.json
  * load() 动态 import 并实例化
  * reload() 热重载（我们的增强）
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.plugin_manager import Plugin, PluginManager, PluginLoadError


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _make_good_plugin(root, name="demo_demod", version="1.0.0"):
    d = os.path.join(root, name)
    _write(os.path.join(d, "mod.py"), '''
from mbdsdr_ai.plugin_manager import Plugin

class DemoPlugin(Plugin):
    def __init__(self):
        self.started = False
    def start(self):
        self.started = True
    def stop(self):
        self.started = False
''')
    _write(os.path.join(d, "plugin.json"), json.dumps({
        "name": name,
        "version": version,
        "type": "demod",
        "entry_point": "mod:DemoPlugin",
    }))
    return d


def test_discover_load_list(tmp_path):
    root = str(tmp_path)
    _make_good_plugin(root, name="alpha")
    _make_good_plugin(root, name="beta", version="2.0.0")

    pm = PluginManager(search_dirs=[root])
    specs = pm.discover()
    assert set(specs.keys()) == {"alpha", "beta"}

    pm.load_all()
    names = sorted(p["name"] for p in pm.list())
    assert names == ["alpha", "beta"]
    demods = [p["name"] for p in pm.list(ptype="demod")]
    assert set(demods) == {"alpha", "beta"}

    a = pm.get("alpha")
    assert isinstance(a, Plugin)
    assert a.kind == "demod"
    a.start()
    assert a.started is True
    pm.stop("alpha")
    assert a.started is False


def test_invalid_plugins_are_skipped(tmp_path):
    root = str(tmp_path)
    # 1) 坏 JSON
    bad_json = os.path.join(root, "badjson")
    _write(os.path.join(bad_json, "plugin.json"), "{ not json ")
    # 2) 缺 entry_point
    no_ep = os.path.join(root, "noep")
    _write(os.path.join(no_ep, "plugin.json"), json.dumps({"name": "noep", "type": "tool"}))
    # 3) entry_point 指向不存在的类
    no_cls = os.path.join(root, "nocls")
    _write(os.path.join(no_cls, "mod.py", ), "class Whatever:\n    pass\n")
    _write(os.path.join(no_cls, "plugin.json"), json.dumps({
        "name": "nocls", "type": "tool", "entry_point": "mod:Nope",
    }))
    # 4) 不是 Plugin 子类
    not_sub = os.path.join(root, "notsub")
    _write(os.path.join(not_sub, "mod.py"), "class NotAPlugin:\n    pass\n")
    _write(os.path.join(not_sub, "plugin.json"), json.dumps({
        "name": "notsub", "type": "tool", "entry_point": "mod:NotAPlugin",
    }))

    pm = PluginManager(search_dirs=[root])
    pm.discover()
    # badjson / noep 在 discover 阶段就被拒
    assert "badjson" not in pm.discovered()
    assert "noep" not in pm.discovered()
    # nocls / notsub 通过 discover（json 合法），但 load 失败进 failed
    assert set(pm.discovered()) >= {"nocls", "notsub"}

    with pytest.raises(PluginLoadError):
        pm.load("nocls")
    with pytest.raises(PluginLoadError):
        pm.load("notsub")
    failed = pm.failed()
    assert "nocls" in failed and "notsub" in failed
    # 失败不影响其它插件
    _make_good_plugin(root, name="good")
    pm.discover()
    pm.load("good")
    assert pm.get("good") is not None


def test_hot_reload(tmp_path):
    root = str(tmp_path)
    d = _make_good_plugin(root, name="hot", version="1.0.0")
    pm = PluginManager(search_dirs=[root])
    pm.discover()
    inst = pm.load("hot")
    assert inst.version == "1.0.0"

    # 改插件代码（升版本），不重启宿主
    _write(os.path.join(d, "mod.py"), '''
from mbdsdr_ai.plugin_manager import Plugin

class DemoPlugin(Plugin):
    REV = 2
    def start(self):
        self.started = True
''')
    inst2 = pm.reload("hot")
    assert inst2 is not inst
    assert getattr(inst2, "REV", None) == 2
    # 旧实例已 stop
    assert inst.started is False

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""配置落盘的两条硬约束（2026-11-11 测试隔离系统修复配套）。

1. 同值 ``set()`` 不得重写文件：内容与盘上逐字符相同时不 ``os.replace``，
   ``mtime_ns`` / ``sha256`` 都不变。否则每个「构造了 MainWindow 的测试」
   都会把用户真实配置重写一遍（内容不变、只有 mtime 变），会话级守卫和用户
   侧备份/同步工具都会误判「配置变了」。
2. 用例**不请求** isolated_profile 也必须被兜住：conftest 的会话级 autouse
   ``_isolate_backing_config`` 把 ``default_config_path()`` 指到 tmp。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_settings_write_isolation.py -v
"""
import hashlib
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import settings as settings_mod  # noqa: E402
# 模块导入发生在所有 fixture 之前：拿到的是未被打桩的原始实现，
# 供下面单独验证 MBDSDR_SETTINGS_DIR 覆盖点。
from settings import default_config_path as _real_default_config_path  # noqa: E402


def _state(path):
    st = os.stat(path)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return (st.st_size, digest, st.st_mtime_ns)


def test_same_value_set_does_not_rewrite_file(tmp_path, monkeypatch):
    """同值 set() → flush() 不得触发 os.replace，mtime/sha256 逐位不变。"""
    path = tmp_path / "desktop_settings.json"
    s = settings_mod.DesktopSettings.load(str(path))
    s.set("touch_mode", s.get("touch_mode"))
    s.flush()
    assert path.exists(), "首次落盘应当把默认值写出来"

    before = _state(path)
    replaced = []
    real_replace = os.replace

    def _spy(src, dst, *args, **kwargs):
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", _spy)

    s.set("touch_mode", s.get("touch_mode"))   # 与原值相同
    s.flush()

    assert replaced == [], f"同值 set() 仍重写了配置：{replaced}"
    assert _state(path) == before, "同值 set() 改变了文件（size/sha256/mtime 有变）"


def test_changed_value_still_writes(tmp_path):
    """真改值时必须照常落盘（别把「不写」做成「永不写」）。"""
    path = tmp_path / "desktop_settings.json"
    s = settings_mod.DesktopSettings.load(str(path))
    s.set("touch_mode", s.get("touch_mode"))
    s.flush()
    assert path.exists(), "首次落盘应当把默认值写出来"
    before = _state(path)

    current = s.get("touch_mode")
    other = "on" if current != "on" else "off"   # choices: auto/on/off
    s.set("touch_mode", other)
    s.flush()

    assert _state(path) != before, "改值后文件没变——写入被误吞了"
    assert settings_mod.DesktopSettings.load(str(path)).get("touch_mode") == other


def test_env_override_redirects_config_path(monkeypatch, tmp_path):
    """MBDSDR_SETTINGS_DIR 是产品侧的最小覆盖点（测试/CI 用）。"""
    monkeypatch.setenv("MBDSDR_SETTINGS_DIR", str(tmp_path))
    assert _real_default_config_path() == str(
        tmp_path / "desktop_settings.json")


def test_autouse_fixture_isolates_without_isolated_profile():
    """不请求 isolated_profile 的用例也拿不到真实配置路径（兜底生效）。"""
    real = os.path.normcase(os.path.abspath(_real_default_config_path()))
    got = os.path.normcase(os.path.abspath(settings_mod.default_config_path()))
    assert got != real, (
        "会话级 autouse 兜底没生效：default_config_path() 仍指向真实配置 %s" % real)
    assert os.path.basename(got) == "desktop_settings.json"


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)

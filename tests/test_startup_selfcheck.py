# SPDX-License-Identifier: MIT
"""启动自检（缺件硬门槛）单元测试。

覆盖 desktop/main.py 的 _startup_dependency_problems：
  * 必需依赖全在 → 空清单；
  * 必需依赖缺一项 → 清单里写明「缺什么 / 缺了会怎样 / 怎么修」；
  * 必需数据件缺失 → 同样进清单；
  * 可选依赖缺失 → 只记日志，不进清单、不拦启动。
"""
import importlib
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_desktop_main = importlib.import_module("main")


def test_selfcheck_passes_when_required_items_present():
    assert _desktop_main._startup_dependency_problems() == [], \
        "当前开发环境必需项齐全，自检必须放行"


def test_selfcheck_reports_missing_required_with_fix_instructions(monkeypatch):
    monkeypatch.setattr(
        _desktop_main, "_probe_import",
        lambda name: name != "scipy.signal")
    problems = _desktop_main._startup_dependency_problems()
    assert len(problems) == 1
    text = problems[0]
    assert "scipy.signal" in text, "必须点名缺了哪个模块"
    assert "缺了会怎样" in text and "怎么修" in text, \
        "每条缺失项必须写清后果和修复方式"


def test_selfcheck_reports_missing_required_data_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        _desktop_main, "_required_data_path",
        lambda rel: str(tmp_path / "missing.json"))
    problems = _desktop_main._startup_dependency_problems()
    assert len(problems) == 1
    assert "dso_catalog.json" in problems[0]
    assert "缺了会怎样" in problems[0] and "怎么修" in problems[0]


def test_optional_dependency_missing_does_not_block_startup(monkeypatch):
    monkeypatch.setattr(
        _desktop_main, "_probe_import",
        lambda name: name not in ("sounddevice", "sgp4"))
    problems = _desktop_main._startup_dependency_problems()
    assert problems == [], "可选依赖缺失只能记日志，不得拦启动"

# SPDX-License-Identifier: MIT
"""可操控性锚点：控制台入口 mbdsdr-ai 声明真实可达。

pyproject [project.scripts] 的每个入口都必须解析到真实 callable，
且 ``--help`` 子进程零退出码——声明了不存在的入口是专业性硬伤
（pip install 后用户拿到一条死命令）。
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _scripts() -> dict:
    with open(_PYPROJECT, "rb") as f:
        return tomllib.load(f)["project"].get("scripts", {})


def test_scripts_table_declares_mbdsdr_ai():
    scripts = _scripts()
    assert "mbdsdr-ai" in scripts


def test_entrypoint_resolves_to_callable_main():
    target = _scripts()["mbdsdr-ai"]
    mod_name, _, attr = target.partition(":")
    assert mod_name and attr
    module = importlib.import_module(mod_name)
    fn = getattr(module, attr)
    assert callable(fn)


def test_cli_help_subprocess_zero_exit():
    import os
    # 子进程钉 UTF-8：Windows 下 --help 走 locale(GBK) 编码，
    # text=True+utf-8 读取会 UnicodeDecodeError（与 test_fill_evidence 同坑）。
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    out = subprocess.run(
        [sys.executable, "-m", "mbdsdr_ai", "--help"],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO_ROOT), timeout=120, env=env,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip()

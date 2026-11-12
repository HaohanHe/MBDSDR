# SPDX-License-Identifier: MIT
"""``release_check.py`` 的 log 步判据自检：只认产品日志，双向反证。

2026-11-13 收紧前的判据是「路径含 mbdsdr 且文件名含 startup / mbdsdr_desktop」，
把别的会话从 ``%TEMP%`` 复制的 ``mbdsdr_ai/startup_sequence.py`` 误报成产品日志。
收紧后必须同时钉住两头：

* (a) 真产品日志写到用户目录 → **必须报**（``*.log`` / 轮转 ``*.log.N``）；
* (b) 从 ``%TEMP%`` 复制的同名源码 → **不许报**；
* (c) 非产品目录里的普通 ``.log`` 与窗口外的旧日志 → 不许报。

缺任何一头都不是收紧，而是把判据换个方向弄错。
"""
from __future__ import annotations

import importlib.util
import os
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "release_check.py")


def _load():
    spec = importlib.util.spec_from_file_location(
        "release_check_logscan", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("x\n")
    return path


def _scan(tmp_path, since: float | None = None):
    rc = _load()
    return rc._scan_userprofile_log_writes(
        time.time() - 5 if since is None else since, home=str(tmp_path))


def test_product_log_in_user_dir_is_reported(tmp_path):
    hit = _touch(os.path.join(str(tmp_path), "AppData", "Local", "MBDSDR",
                              "logs", "mbdsdr_startup.log"))
    hits, scanned = _scan(tmp_path)
    assert hit in hits, f"真产品日志没被报：{hits}"
    assert scanned >= 1


def test_rotated_product_log_is_reported(tmp_path):
    hit = _touch(os.path.join(str(tmp_path), "AppData", "Roaming", "mbdsdr",
                              "logs", "desktop.log.2"))
    hits, _ = _scan(tmp_path)
    assert hit in hits, f"轮转产品日志没被报：{hits}"


def test_same_named_source_copy_in_temp_is_not_reported(tmp_path):
    _touch(os.path.join(str(tmp_path), "AppData", "Local", "Temp", "axfinal",
                        "mbdsdr_ai", "startup_sequence.py"))
    hits, _ = _scan(tmp_path)
    assert hits == [], f"%TEMP% 里的源码副本被误报成产品日志：{hits}"


def test_unrelated_user_log_is_not_reported(tmp_path):
    _touch(os.path.join(str(tmp_path), "AppData", "Local", "Temp", "other",
                        "chrome_debug.log"))
    hits, _ = _scan(tmp_path)
    assert hits == [], f"非产品日志被误报：{hits}"


def test_old_product_log_outside_window_is_not_reported(tmp_path):
    hit = _touch(os.path.join(str(tmp_path), "AppData", "Local", "MBDSDR",
                              "logs", "old.log"))
    old = time.time() - 3600
    os.utime(hit, (old, old))
    hits, _ = _scan(tmp_path)
    assert hits == [], f"窗口外的旧日志被算到本次运行头上：{hits}"

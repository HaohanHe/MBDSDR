# SPDX-License-Identifier: MIT
"""``release_check.py`` 的 log 步判据自检：明确落点 + 只认 ``*.log``。

2026-11-13 第一次收紧把「路径含 mbdsdr 且文件名含 startup」换成了
「``*.log`` 后缀 + 产品归属」；第二次收紧把扫描根从全盘 ``C:\\Users``
（1,029,800 个文件 / 76–100s）收窄到**产品日志声明落点**。两头都要钉住：

* (a) 真产品日志写到用户目录的声明落点 → **必须报**
      （``*.log`` / 轮转 ``*.log.N``）；
* (b) 从 ``%TEMP%`` 复制的同名源码 → **不许报**（不在声明根下，连遍历都没有）；
* (c) 声明根之外（Desktop、Temp）的普通 ``.log`` 与窗口外的旧日志 → 不许报；
* (d) 声明根本身必须是明确位置，不能退化成 ``home`` 或 ``Temp``。

缺任何一头都不是收紧，而是把判据换个方向弄错。
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
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


def _roots(tmp_path) -> list[str]:
    """测试专用的两个声明根，避免扫到真实仓库/用户目录。"""
    return [
        os.path.join(str(tmp_path), "AppData", "Local", "MBDSDR", "logs"),
        os.path.join(str(tmp_path), "AppData", "Roaming", "MBDSDR", "logs"),
    ]


def _scan(tmp_path, since: float | None = None):
    rc = _load()
    return rc._scan_userprofile_log_writes(
        time.time() - 5 if since is None else since,
        home=str(tmp_path), roots=_roots(tmp_path))


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
    # Windows 的 os.walk 回填磁盘上的真实大小写（MBDSDR），比较前统一 normcase
    assert os.path.normcase(hit) in [os.path.normcase(h) for h in hits], \
        f"轮转产品日志没被报：{hits}"


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


def test_declared_roots_are_explicit_product_dirs(tmp_path):
    rc = _load()
    roots = rc._product_log_roots(home=str(tmp_path))
    norm = [os.path.normcase(r) for r in roots]
    assert os.path.normcase(os.path.join(
        str(tmp_path), "AppData", "Local", "MBDSDR", "logs")) in norm
    assert os.path.normcase(os.path.join(
        str(tmp_path), "AppData", "Roaming", "MBDSDR", "logs")) in norm
    # 不能退化成整个 home / 系统临时目录；每个根必须是 logs 叶子
    assert os.path.normcase(str(tmp_path)) not in norm
    temp_root = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
    assert temp_root not in norm
    assert all(os.path.basename(r) == "logs" for r in norm), \
        f"扫描根不是明确的 logs 目录：{roots}"


def test_log_outside_declared_roots_is_not_scanned(tmp_path):
    _touch(os.path.join(str(tmp_path), "Desktop", "mbdsdr.log"))
    _touch(os.path.join(str(tmp_path), "AppData", "Local", "Temp",
                        "mbdsdr.log"))
    hits, scanned = _scan(tmp_path)
    assert hits == [], f"声明根之外的日志被扫进来：{hits}"
    assert scanned == 0, f"声明根不存在时不该遍历任何文件，实际 {scanned}"


# ---------------------------------------------------------------- 全盘口径
# 日常闸收窄到声明落点，盲区是「不在任何声明落点里」的写入（例：别的会话把
# 源码复制到 %TEMP% 或落到 Desktop）。发版前必须另有全盘命令补上，两条互补。

def _scan_full(tmp_path, since: float | None = None):
    rc = _load()
    return rc._scan_userprofile_log_writes(
        time.time() - 5 if since is None else since,
        home=str(tmp_path), roots=None, full=True)


def test_full_scan_catches_log_outside_declared_roots(tmp_path):
    hit = _touch(os.path.join(str(tmp_path), "Desktop", "mbdsdr.log"))
    narrow_hits, narrow_scanned = _scan(tmp_path)
    assert narrow_hits == [], f"日常闸不该扫到 Desktop：{narrow_hits}"
    assert narrow_scanned == 0
    hits, scanned = _scan_full(tmp_path)
    assert hit in hits, f"全盘扫没抓到声明落点之外的日志（盲区仍在）：{hits}"
    assert scanned >= 1


def test_full_scan_still_filters_by_log_suffix(tmp_path):
    # 全盘 ≠ 什么都报：非 *.log 的源码副本仍然不许报
    _touch(os.path.join(str(tmp_path), "AppData", "Local", "Temp", "axfinal",
                        "mbdsdr_ai", "startup_sequence.py"))
    hits, _ = _scan_full(tmp_path)
    assert hits == [], f"全盘扫把源码副本误报成日志：{hits}"


def test_full_scan_honours_time_window(tmp_path):
    hit = _touch(os.path.join(str(tmp_path), "Desktop", "old.log"))
    old = time.time() - 3600
    os.utime(hit, (old, old))
    hits, _ = _scan_full(tmp_path)
    assert hits == [], f"全盘扫把窗口外的旧日志算到本次运行头上：{hits}"


def test_full_scan_does_not_need_declared_roots(tmp_path):
    # full=True 时 roots 参数必须被忽略：声明根不存在也要能遍历用户目录
    hit = _touch(os.path.join(str(tmp_path), "Documents", "x.log"))
    hits, scanned = _scan_full(tmp_path)
    assert hit in hits and scanned >= 1, \
        f"全盘模式仍被声明根限制：hits={hits} scanned={scanned}"


def test_full_scan_product_attribution_ignores_foreign_logs():
    # 全盘口径没有声明根，归属必须显式判；否则别人家的 .log 会把闸门钉成红
    rc = _load()
    assert rc._is_product_attributed(r"C:\Users\x\Desktop\mbdsdr.log")
    assert rc._is_product_attributed(r"D:\mbdsdr\logs\mbdsdr_startup.log")
    assert rc._is_product_attributed(r"C:\Users\x\AppData\Local\MBDSDR\logs\a.log")
    assert not rc._is_product_attributed(
        r"C:\Users\x\AppData\Roaming\Trae CN\logs\20261113\main.log")
    assert not rc._is_product_attributed(
        r"C:\Users\x\AppData\Roaming\Tencent\QQMusic\WNS\log\19.log")

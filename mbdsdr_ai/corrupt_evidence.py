# SPDX-License-Identifier: MIT
"""配置损坏证据（``*.corrupt-*`` 备份）的理由文件与只读清点。

背景：书签 / FM 预设 / 桌面配置在损坏时会先把原件改名成
``<文件>.corrupt-<stamp>`` 再降级。备份保住了数据，但用户不知道发生过
什么，且内存里的 ``last_error`` 进程一退就没了。这里把「为什么坏」写成
与备份成对的 ``*.reason.txt``，并提供一个**只读**清点函数给启动日志。

三条约束：

1. **顺序固定**：先备份成功，再写理由；理由写失败只记一行日志，
   绝不影响已经落好的备份，也绝不抛异常拖垮启动。
2. **只读清点**：``scan_corruption_evidence`` 只读目录/时间，不删、
   不改、不创建任何文件。超过保留期的备份由用户手动清理。
3. **无 Qt**：本模块不导入 PySide6，可在没有 QApplication 的测试里直接跑。
"""

import os
import time
from typing import Any, Callable, Dict, List, Optional

#: 理由文件后缀：接在备份路径后面，例如
#: ``bookmarks.json.corrupt-20261112-213000.reason.txt``。
REASON_SUFFIX = ".reason.txt"

#: 超过这么多天的备份算「可手动清理」（只是清点，不执行删除）。
STALE_DAYS = 30

#: 单次扫描最多读取多少条理由文件（防止病态目录拖慢启动）。
MAX_ENTRIES = 200


def reason_path_for(backup_path: str) -> str:
    """备份路径 → 对应的理由文件路径（不检查存在性）。"""
    return str(backup_path) + REASON_SUFFIX


def write_reason(backup_path: Optional[str], reason: str,
                 logger=None) -> Optional[str]:
    """把损坏原因写成备份旁的一行文本；失败只记日志并返回 None。

    必须在备份**成功之后**调用：调用方拿到非空的 ``backup_path`` 才说明
    原件已经安全改名，此时目录一定可写；理由写失败不影响备份本身。
    """
    if not backup_path:
        return None
    path = reason_path_for(backup_path)
    line = " ".join(str(reason or "").split())     # 压成一行，便于日志比对
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(line + "\n")
        return path
    except OSError as exc:
        if logger is not None:
            logger.warning("损坏理由写入失败：%s（%s）", path, exc)
        return None


def scan_corruption_evidence(directories, now: Optional[float] = None,
                             stale_days: int = STALE_DAYS,
                             max_entries: int = MAX_ENTRIES) -> Dict[str, Any]:
    """只读清点若干目录里的 ``*.reason.txt``，返回结论（不删任何文件）。

    返回::

        {
          "total": int,            # 找到的理由文件数
          "recent": [path, ...],   # 未超过 stale_days 的
          "stale": int,            # 超过 stale_days 的（可手动清理）
          "unreadable": [{"dir": 路径或文件, "error": 原因}],
          "message": str,          # 给启动日志的一行；无事可报时为空串
        }

    目录不存在 → 直接跳过（不算错误）；目录存在但读不了 → 进
    ``unreadable`` 并在 ``message`` 里写明「未检查 <dir>（原因）」，
    不许静默。
    """
    moment = time.time() if now is None else now
    cutoff = moment - stale_days * 86400
    total = 0
    recent: List[str] = []
    stale = 0
    unreadable: List[Dict[str, str]] = []
    seen_dirs = set()

    for directory in directories or ():
        directory = os.path.abspath(os.path.expanduser(str(directory)))
        key = os.path.normcase(directory)
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        if not os.path.isdir(directory):
            continue
        try:
            names = sorted(os.listdir(directory))
        except OSError as exc:
            unreadable.append({
                "dir": directory,
                "error": f"{exc.__class__.__name__}: {exc}",
            })
            continue
        for name in names:
            if total >= max_entries:
                break
            if not (name.endswith(REASON_SUFFIX) and ".corrupt-" in name):
                continue
            full = os.path.join(directory, name)
            try:
                mtime = os.stat(full).st_mtime
            except OSError as exc:
                unreadable.append({
                    "dir": full,
                    "error": f"{exc.__class__.__name__}: {exc}",
                })
                continue
            total += 1
            if mtime >= cutoff:
                recent.append(full)
            else:
                stale += 1

    parts: List[str] = []
    if recent:
        newest = max(recent, key=lambda p: os.stat(p).st_mtime)
        parts.append(f"最近 {stale_days} 天内有 {len(recent)} 个配置文件损坏"
                     f"并已备份，最近一次：{newest}")
    if stale:
        parts.append(f"有 {stale} 个超过 {stale_days} 天的损坏备份，"
                     "可手动清理（见帮助 › 诊断）")
    for item in unreadable:
        parts.append(f"未检查 {item['dir']}（{item['error']}）")

    return {
        "total": total,
        "recent": recent,
        "stale": stale,
        "unreadable": unreadable,
        "message": "；".join(parts),
    }


def default_scan_directories(settings_path: str) -> List[str]:
    """启动时要清点的目录：书签/FM 预设默认目录 + 桌面配置所在目录。

    只做路径推导、不碰文件系统，便于在没有 GUI 的进程里断言。
    """
    directories = [os.path.join(os.path.expanduser("~"), ".mbdsdr")]
    settings_dir = os.path.dirname(str(settings_path or ""))
    if settings_dir:
        directories.append(settings_dir)
    return directories


def corruption_startup_report(directories, append_line: Optional[Callable] = None,
                              now: Optional[float] = None,
                              stale_days: int = STALE_DAYS) -> Dict[str, Any]:
    """清点后把结论写进启动日志（只写日志，不碰任何界面）。

    ``append_line`` 由调用方注入（生产是 ``startup_paths.append_startup_log``），
    这样测试不需要启动 GUI，也能钉住「日志不可写时退化成内存缓冲」。
    """
    report = scan_corruption_evidence(directories, now=now,
                                      stale_days=stale_days)
    if report["message"] and callable(append_line):
        append_line("[配置] " + report["message"])
    return report

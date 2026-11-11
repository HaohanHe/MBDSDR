# SPDX-License-Identifier: MIT
"""历史 JSON 的进程级只读快照缓存（启动期 IO 优化）。

`FileTracker` / `SelfLearning` 每次构造都把整个历史目录 open + json.load
一遍。桌面端、MCP 服务、测试 fixture 在同一进程里会反复构造这些对象，
用户历史越攒越多，启动就越慢。

这里只缓存**已解析的原始 dict**：目录签名（路径 + mtime_ns + size）不变
就直接复用，新增/改写/删除任意一个文件都会让签名变化并重读。每个调用方
仍用自己的 dict 重建 dataclass 对象，不共享可变状态；缓存里的 dict 约定
只读（调用方只取字段，不写回）。

不缓存反例（保持与优化前一致的静默跳过）：坏 JSON、非 dict 内容、无法
stat 的路径。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Dict, List, Tuple

_LOCK = threading.RLock()
#: key = 归一化后的路径元组哈希；value = (内容签名, 已解析 dict 元组)
_CACHE: Dict[str, Tuple[str, Tuple[Tuple[str, Dict[str, Any]], ...]]] = {}
_MAX_ENTRIES = 16


def _signature(paths: Tuple[str, ...]) -> str:
    """路径 + mtime_ns + size 组成内容签名；stat 失败的文件会让签名不稳定，
    直接标记为不可缓存（返回空串），每次都老实重读。"""
    h = hashlib.sha256()
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            return ""
        h.update(f"{path}|{st.st_mtime_ns}|{st.st_size}\n".encode("utf-8"))
    return h.hexdigest()


def load_json_files(paths: List[str]) -> List[Tuple[str, Dict[str, Any]]]:
    """按路径读一组 .json；命中缓存时跳过 open + json.load。

    返回 (路径, 已解析 dict) 列表，路径保留给调用方做「哪条坏记录」的
    诚实告警。返回的是**新 list**，元组里的 dict 来自缓存，约定只读
    （调用方只取字段构造自己的对象，不写回）。
    """
    ordered = tuple(sorted(paths))
    if not ordered:
        return []
    key = hashlib.sha256("\n".join(ordered).encode("utf-8")).hexdigest()
    sig = _signature(ordered)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and sig and hit[0] == sig:
            return list(hit[1])

    rows: List[Tuple[str, Dict[str, Any]]] = []
    for path in ordered:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, UnicodeError):
            continue
        if isinstance(data, dict):
            rows.append((path, data))

    if sig:
        with _LOCK:
            if len(_CACHE) >= _MAX_ENTRIES:
                _CACHE.clear()
            _CACHE[key] = (sig, tuple(rows))
    return list(rows)


def clear_cache() -> None:
    """清空缓存（测试 / 显式失效用）。"""
    with _LOCK:
        _CACHE.clear()

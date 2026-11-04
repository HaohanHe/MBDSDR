# SPDX-License-Identifier: MIT
"""
截图基线受控更新
================

基线不允许"无脑覆盖"：
  * 必须带 ``reason``（人工确认的原因）；
  * 几何（归一化尺寸 + 宽高比）必须与旧基线一致，否则**即使带 reason 也拒绝**；
  * 每次更新都输出差异摘要：尺寸变化、粗/细色块漂移、变化像素占比。

供 ``tests/test_ui_layout_locks.py``（更新分支）与
``tests/test_baseline_update_guard.py`` 复用。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

_SIG_COLS, _SIG_ROWS = 32, 24


class BaselineUpdateRefused(RuntimeError):
    """基线更新被拒绝（缺原因 / 几何漂移）。"""


def signature(img: QImage, cols: int = _SIG_COLS, rows: int = _SIG_ROWS):
    """把图缩到 cols×rows 后取每格 RGB（与布局锁同一套色块签名）。"""
    small = img.scaled(cols, rows, Qt.IgnoreAspectRatio,
                       Qt.SmoothTransformation)
    cells = []
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            cells.append((c.red(), c.green(), c.blue()))
    return cells


def diff_stats(cur, old):
    """逐格最大通道差 / 平均通道差。"""
    diffs = [max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))
             for a, b in zip(cur, old)]
    return max(diffs), sum(diffs) / len(diffs)


def changed_pixel_ratio(cur: QImage, old: QImage, threshold: int = 8) -> float:
    """RGB 任一通道差 > threshold 的像素占比（0~1）。"""
    if cur.width() != old.width() or cur.height() != old.height():
        raise BaselineUpdateRefused("像素统计要求尺寸一致")
    total = cur.width() * cur.height()
    changed = 0
    for y in range(cur.height()):
        for x in range(cur.width()):
            a = QColor(cur.pixel(x, y))
            b = QColor(old.pixel(x, y))
            if max(abs(a.red() - b.red()), abs(a.green() - b.green()),
                   abs(a.blue() - b.blue())) > threshold:
                changed += 1
    return changed / total if total else 0.0


def summarize(old: QImage, new: QImage) -> dict:
    """一次更新的可复核摘要（尺寸 / 两档色块 / 像素占比）。"""
    if old.width() != new.width() or old.height() != new.height():
        raise BaselineUpdateRefused(
            f"尺寸不一致：旧 {old.width()}x{old.height()} "
            f"新 {new.width()}x{new.height()}")
    coarse = diff_stats(signature(new, 8, 6), signature(old, 8, 6))
    fine = diff_stats(signature(new), signature(old))
    ratio = changed_pixel_ratio(new, old)
    return {
        "size": (old.width(), old.height(), new.width(), new.height()),
        "coarse": coarse,
        "fine": fine,
        "pixel_ratio": ratio,
        "text": (f"size {old.width()}x{old.height()}→{new.width()}x{new.height()}；"
                 f"8×6 worst={coarse[0]}/mean={coarse[1]:.1f}；"
                 f"32×24 worst={fine[0]}/mean={fine[1]:.1f}；"
                 f"变化像素 {ratio * 100:.1f}%"),
    }


def guarded_update(path: str, image: QImage, reason: str,
                   max_height_delta: int = 2, allow_create: bool = True) -> dict:
    """受控写入基线；返回差异摘要（调用方负责打印与复核）。

    几何判定与布局锁一致：宽度必须相同、高度允许 ±2px、宽高比误差 <1%。
    几何不符时抛 BaselineUpdateRefused——带 reason 也不放行。
    """
    if not reason or not reason.strip():
        raise BaselineUpdateRefused(
            "基线更新必须提供 --reason \"<原因>\"（人工确认后才允许覆盖）")
    if image.isNull():
        raise BaselineUpdateRefused("新截图为空")

    old = QImage(path) if os.path.exists(path) else None
    if old is not None and not old.isNull():
        if image.width() != old.width() or \
                abs(image.height() - old.height()) > max_height_delta:
            raise BaselineUpdateRefused(
                f"几何漂移，拒绝更新：旧 {old.width()}x{old.height()} "
                f"新 {image.width()}x{image.height()}")
        if old.height() and abs(image.width() / image.height() -
                                old.width() / old.height()) >= 0.01:
            raise BaselineUpdateRefused(
                f"宽高比漂移，拒绝更新：旧 {old.width() / old.height():.4f} "
                f"新 {image.width() / image.height():.4f}")
        summary = summarize(old, image)
    else:
        if not allow_create:
            raise BaselineUpdateRefused(f"基线缺失且禁止新建: {path}")
        summary = {
            "size": (0, 0, image.width(), image.height()),
            "coarse": (0, 0.0),
            "fine": (0, 0.0),
            "pixel_ratio": 1.0,
            "text": f"新建基线 {image.width()}x{image.height()}",
        }

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not image.save(path, "PNG"):
        raise BaselineUpdateRefused(f"基线写入失败: {path}")
    summary["reason"] = reason.strip()
    return summary

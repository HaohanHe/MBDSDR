# SPDX-License-Identifier: MIT
"""
截图基线受控更新
================

基线不允许"无脑覆盖"：
  * 必须带 ``reason``（人工确认的原因）；
  * 默认只允许颜色漂移：尺寸/宽高比（以及显式传入的刻度标签几何）
    变化即使带 reason 也拒绝；
  * 确属本次设计变更时，必须同时给出
    ``accept_geometry_change=True`` + 点名已声明设计变更的 reason，
    并打印几何 diff（尺寸/宽高比/变化行带/标签位置与尺寸）；
  * 每次更新都输出差异摘要：尺寸变化、粗/细色块漂移、变化像素占比。

供 ``tests/test_ui_layout_locks.py``（更新分支）与
``tests/test_baseline_update_guard.py`` 复用。
"""

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

_SIG_COLS, _SIG_ROWS = 32, 24

# 已声明的设计变更清单：--accept-geometry-change 的 reason 必须点名其中之一。
# 新增项时同步在提交说明里点名（否则更新后的提交无法追溯几何变化来源）。
DESIGN_CHANGES = {
    "spectrum-axis-nice-steps":
        "频谱频轴/dB 轴刻度改 1/2/5×10ⁿ 阶梯 + 按量程自动单位（MHz 右端单标）",
}


class BaselineUpdateRefused(RuntimeError):
    """基线更新被拒绝（缺原因 / 几何漂移 / 未声明设计变更）。"""


def matching_design_change(reason: str) -> str | None:
    """返回 reason 命中的已声明设计变更 token；未命中返回 None。"""
    text = (reason or "").strip().lower()
    if not text:
        return None
    for token in DESIGN_CHANGES:
        if token.lower() in text:
            return token
    return None


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


def _changed_regions(cur: QImage, old: QImage, threshold: int = 8):
    """尺寸不一致时把旧图缩到新图尺寸再统计；返回
    (变化像素占比, bbox, 变化行带)。行带用于定位刻度/标签带。"""
    if (cur.width(), cur.height()) == (old.width(), old.height()):
        ref = old
    else:
        ref = old.scaled(cur.width(), cur.height(), Qt.IgnoreAspectRatio,
                         Qt.SmoothTransformation)
    total = cur.width() * cur.height()
    changed = 0
    x0, y0, x1, y1 = cur.width(), cur.height(), -1, -1
    hit_rows = []
    for y in range(cur.height()):
        row_hit = False
        for x in range(cur.width()):
            a = QColor(cur.pixel(x, y))
            b = QColor(ref.pixel(x, y))
            if max(abs(a.red() - b.red()), abs(a.green() - b.green()),
                   abs(a.blue() - b.blue())) > threshold:
                changed += 1
                row_hit = True
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
        if row_hit:
            hit_rows.append(y)
    bands = []
    for y in hit_rows:
        if bands and y - bands[-1][1] <= 1:
            bands[-1][1] = y
        else:
            bands.append([y, y])
    ratio = changed / total if total else 0.0
    bbox = (x0, y0, x1, y1) if changed else None
    return ratio, bbox, [tuple(b) for b in bands]


def geometry_diff(old: QImage, new: QImage) -> dict:
    """打印/返回几何差异：尺寸、宽高比、变化像素、变化行带、bbox。"""
    ratio, bbox, bands = _changed_regions(new, old)
    ow, oh = old.width(), old.height()
    nw, nh = new.width(), new.height()
    oa = ow / oh if oh else 0.0
    na = nw / nh if nh else 0.0
    bbox_txt = ("无" if bbox is None
                else f"({bbox[0]},{bbox[1]})-({bbox[2]},{bbox[3]})")
    band_txt = ("无" if not bands else
                "、".join(f"y{a}-{b}" for a, b in bands))
    text = (f"size {ow}x{oh}→{nw}x{nh}（Δ{nh - oh:+d}h,{nw - ow:+d}w）；"
            f"宽高比 {oa:.4f}→{na:.4f}（Δ{na - oa:+.4f}）；"
            f"变化像素 {ratio * 100:.2f}%；bbox={bbox_txt}；"
            f"变化行带 {len(bands)} 段：{band_txt}")
    return {
        "size": (ow, oh, nw, nh),
        "aspect": (oa, na),
        "pixel_ratio": ratio,
        "bbox": bbox,
        "bands": bands,
        "text": text,
    }


def _label_item(item):
    """标签项统一成 (group, text, x0, x1, y0, y1)；dict 与序列都支持。"""
    if isinstance(item, dict):
        vals = (item.get("group", ""), item["text"],
                item["x0"], item["x1"], item["y0"], item["y1"])
    else:
        vals = ("",) + tuple(item)
    return ((str(vals[0]), str(vals[1]))
            + tuple(round(float(v), 1) for v in vals[2:]))


def _grouped(labels):
    groups = {}
    for item in labels or []:
        key = _label_item(item)
        groups.setdefault(key[0], []).append(key)
    return groups


def label_diff(old_labels, new_labels, tol: float = 1.0) -> dict:
    """刻度/标签几何 diff：数量、增删、按序位置变化、尺寸变化。

    ``labels`` 每项为 ``{"group", "text", "x0", "x1", "y0", "y1"}``
    （逻辑像素）。频轴与 dB 轴分组比较，避免两条轴互相错配；
    文本集合用多重集比较，按序配对给"位置变化几处/最大位移"。
    """
    old_g = _grouped(old_labels)
    new_g = _grouped(new_labels)
    old = [x for g in sorted(old_g) for x in old_g[g]]
    new = [x for g in sorted(new_g) for x in new_g[g]]
    old_ms, new_ms = {}, {}
    for it in old:
        k = (it[0], it[1])
        old_ms[k] = old_ms.get(k, 0) + 1
    for it in new:
        k = (it[0], it[1])
        new_ms[k] = new_ms.get(k, 0) + 1
    removed = sorted(t for (g, t), n in old_ms.items()
                     for _ in range(n - new_ms.get((g, t), 0)))
    added = sorted(t for (g, t), n in new_ms.items()
                   for _ in range(n - old_ms.get((g, t), 0)))
    moves, resized, per_group = [], [], []
    for g in sorted(set(old_g) | set(new_g)):
        # 按几何位置（先 y 后 x）配对，避免绘制顺序变化被误报成位移。
        o_items = sorted(old_g.get(g, []), key=lambda it: (it[4], it[2]))
        n_items = sorted(new_g.get(g, []), key=lambda it: (it[4], it[2]))
        g_moves = 0
        for i in range(min(len(o_items), len(n_items))):
            o, n = o_items[i], n_items[i]
            ocx, ncx = (o[2] + o[3]) / 2, (n[2] + n[3]) / 2
            ocy, ncy = (o[4] + o[5]) / 2, (n[4] + n[5]) / 2
            dx, dy = ncx - ocx, ncy - ocy
            if max(abs(dx), abs(dy)) > tol:
                g_moves += 1
                moves.append((f"{g}#{i}", o[1], n[1], round(dx, 1),
                              round(dy, 1)))
            dw = (n[3] - n[2]) - (o[3] - o[2])
            dh = (n[5] - n[4]) - (o[5] - o[4])
            if max(abs(dw), abs(dh)) > tol:
                resized.append((f"{g}#{i}", o[1], n[1], round(dw, 1),
                                round(dh, 1)))
        per_group.append(f"{g or '标签'} {len(o_items)}→{len(n_items)}，"
                         f"位置变化 {g_moves} 处")
    max_dx = max((abs(m[3]) for m in moves), default=0.0)
    max_dy = max((abs(m[4]) for m in moves), default=0.0)
    text = (f"{'；'.join(per_group)}；移除 {len(removed)} {removed}；"
            f"新增 {len(added)} {added}；位置变化合计 {len(moves)} 处"
            f"（最大 Δx={max_dx:.1f}px/Δy={max_dy:.1f}px）；"
            f"尺寸变化 {len(resized)} 处")
    return {
        "count": (len(old), len(new)),
        "removed": removed,
        "added": added,
        "moves": moves,
        "resized": resized,
        "text": text,
    }


def _labels_path(path: str) -> str:
    """刻度标签几何 sidecar：与 PNG 同名放在同目录。"""
    return os.path.splitext(path)[0] + ".labels.json"


def load_labels(path: str):
    """读取基线旁的刻度标签几何 sidecar；缺失返回 None。"""
    lp = _labels_path(path)
    if not os.path.exists(lp):
        return None
    try:
        with open(lp, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("labels")
    except (OSError, ValueError, AttributeError):
        return None


def save_labels(path: str, labels) -> str:
    lp = _labels_path(path)
    with open(lp, "w", encoding="utf-8") as f:
        json.dump({"labels": list(labels)}, f, ensure_ascii=False, indent=2)
    return lp


def _geometry_changed(old: QImage, new: QImage,
                      max_height_delta: int) -> bool:
    if new.width() != old.width() or \
            abs(new.height() - old.height()) > max_height_delta:
        return True
    if old.height() and abs(new.width() / new.height() -
                            old.width() / old.height()) >= 0.01:
        return True
    return False


def summarize(old: QImage, new: QImage, allow_geometry: bool = False) -> dict:
    """一次更新的可复核摘要（尺寸 / 两档色块 / 像素占比）。"""
    if (old.width(), old.height()) != (new.width(), new.height()):
        if not allow_geometry:
            raise BaselineUpdateRefused(
                f"尺寸不一致：旧 {old.width()}x{old.height()} "
                f"新 {new.width()}x{new.height()}")
        ref = old.scaled(new.width(), new.height(), Qt.IgnoreAspectRatio,
                         Qt.SmoothTransformation)
    else:
        ref = old
    coarse = diff_stats(signature(new, 8, 6), signature(ref, 8, 6))
    fine = diff_stats(signature(new), signature(ref))
    ratio, bbox, bands = _changed_regions(new, old)
    return {
        "size": (old.width(), old.height(), new.width(), new.height()),
        "coarse": coarse,
        "fine": fine,
        "pixel_ratio": ratio,
        "bbox": bbox,
        "bands": bands,
        "text": (f"size {old.width()}x{old.height()}→{new.width()}x{new.height()}；"
                 f"8×6 worst={coarse[0]}/mean={coarse[1]:.1f}；"
                 f"32×24 worst={fine[0]}/mean={fine[1]:.1f}；"
                 f"变化像素 {ratio * 100:.1f}%"),
    }


def guarded_update(path: str, image: QImage, reason: str,
                   max_height_delta: int = 2, allow_create: bool = True,
                   accept_geometry_change: bool = False,
                   label_geometry=None) -> dict:
    """受控写入基线；返回差异摘要（调用方负责打印与复核）。

    默认判定与布局锁一致：宽度必须相同、高度允许 ±2px、宽高比误差 <1%，
    且显式传入的刻度标签几何必须一致。几何不符时抛
    BaselineUpdateRefused——**即使带 reason 也不放行**。

    确属本次设计变更时走第二通道：``accept_geometry_change=True`` 且
    reason 必须点名 ``DESIGN_CHANGES`` 里的声明 token；此时打印几何 diff
    （尺寸/宽高比/变化行带 + 标签位置/尺寸变化）并写入摘要。
    """
    if not reason or not reason.strip():
        raise BaselineUpdateRefused(
            "基线更新必须提供 --reason \"<原因>\"（人工确认后才允许覆盖）")
    if image.isNull():
        raise BaselineUpdateRefused("新截图为空")

    old = QImage(path) if os.path.exists(path) else None
    if old is not None and not old.isNull():
        old_labels = load_labels(path)
        geom_changed = _geometry_changed(old, image, max_height_delta)
        labels_changed = False
        labels_diff = None
        if label_geometry is not None and old_labels is not None:
            labels_diff = label_diff(old_labels, label_geometry)
            labels_changed = bool(labels_diff["removed"] or
                                  labels_diff["added"] or
                                  labels_diff["moves"] or
                                  labels_diff["resized"])

        if geom_changed or labels_changed:
            what = "几何漂移" if geom_changed else "刻度标签几何变化"
            if not accept_geometry_change:
                raise BaselineUpdateRefused(
                    f"{what}，拒绝更新：旧 {old.width()}x{old.height()} "
                    f"新 {image.width()}x{image.height()}；"
                    f"确属本次设计变更须加 --accept-geometry-change 且 "
                    f"reason 点名已声明变更（{', '.join(DESIGN_CHANGES)}）")
            token = matching_design_change(reason)
            if token is None:
                raise BaselineUpdateRefused(
                    "--accept-geometry-change 的 reason 必须点名已声明设计变更："
                    + "、".join(f"{k}（{v}）" for k, v in DESIGN_CHANGES.items())
                    + f"；当前 reason={reason.strip()!r}")
            gdiff = geometry_diff(old, image)
            print(f"[baseline][geometry] 已声明 {token}: {gdiff['text']}",
                  flush=True)
            if labels_diff is not None:
                print(f"[baseline][labels] {labels_diff['text']}", flush=True)
            for i, ot, nt, dx, dy in (labels_diff["moves"]
                                      if labels_diff else []):
                print(f"[baseline][labels] #{i} {ot!r}→{nt!r} "
                      f"Δx={dx:+.1f}px Δy={dy:+.1f}px", flush=True)
            summary = summarize(old, image, allow_geometry=True)
            summary["geometry"] = gdiff
            summary["labels"] = labels_diff
            summary["text"] += ("；几何变更已声明 " + token
                                + "：" + gdiff["text"]
                                + ("；" + labels_diff["text"]
                                   if labels_diff else ""))
            summary["design_change"] = token
        else:
            summary = summarize(old, image)
            summary["geometry"] = None
            summary["labels"] = None
    else:
        if not allow_create:
            raise BaselineUpdateRefused(f"基线缺失且禁止新建: {path}")
        summary = {
            "size": (0, 0, image.width(), image.height()),
            "coarse": (0, 0.0),
            "fine": (0, 0.0),
            "pixel_ratio": 1.0,
            "geometry": None,
            "labels": None,
            "text": f"新建基线 {image.width()}x{image.height()}",
        }

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not image.save(path, "PNG"):
        raise BaselineUpdateRefused(f"基线写入失败: {path}")
    if label_geometry is not None:
        save_labels(path, label_geometry)
    summary["reason"] = reason.strip()
    return summary

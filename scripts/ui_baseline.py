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
    "narrow-bottom-dock-74-to-48":
        "窄屏收高：底坞定高 74→48（Δ26）。窗口高度不变时三栏 splitter 得到 "
        "+26px；ADS-B 面板宽恒 300（归一化不缩放）故四尺寸均匀 +26；"
        "频谱面板归一化高 = 320×H/W，Δ = 320×26/W（W=390/670/990/1310，"
        "实测 +21/+12/+9/+6）",
}

# 已登记「旧基线记录缺陷态」的修正清单：同样是几何变化，但这不是设计变更，
# 而是把"缺陷时期拍下的错误基线"按控件真实布局纠正。reason 必须点名 token；
# 与设计变更分开登记，避免以后拿缺陷修正当设计变更的挡箭牌。
DEFECT_BASELINE_FIXES = {
    "freq-readout-bookmark-blowout":
        "旧 freq_readout 基线记录的是书签长名撑爆右栏的缺陷态："
        "FreqDisplay 被内容最小宽撑到 ~960px，归一化后成 320x20；"
        "右栏 300px 契约下的真实布局是 278x60（高 60 是控件 setMinimumHeight）",
    "ui-baseline-userdata-isolation":
        "旧基线绑定跑它的人：书签药丸来自开发者 ~/.mbdsdr/bookmarks.json"
        "（干净账号 worst=141>100 必红）、主题取自开发者 DesktopSettings"
        "（figma 与出厂 default 整图色值不同）、窗口状态取自 QSettings/"
        "layouts.json；且 QComboBox 首次 show 的 sizeHint 让同进程第 1 个"
        "窗口与后续窗口交替停在 270px/325px 两种稳态。夹具改为隔离用户目录"
        "（expanduser/APPDATA/layout_store/QSettings）+ 出厂默认主题 + "
        "拍前全量重抛光后按真实布局重建",
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


def matching_defect_baseline(reason: str) -> str | None:
    """返回 reason 命中的已登记缺陷基线修正 token；未命中返回 None。"""
    text = (reason or "").strip().lower()
    if not text:
        return None
    for token in DEFECT_BASELINE_FIXES:
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


#: 归一化截图的几何量化误差上界（像素）。推导见 geometry_tolerance()。
GEOMETRY_PX_TOL = 2.0


def geometry_tolerance(width: int, height: int) -> tuple:
    """由量化误差上界推出的 ``(像素容差, 比例容差)``，不是拍的常量。

    像素容差 2px 的推导（为什么是 2 而不是 3）：

      * ``grab()`` 把逻辑尺寸落到整数像素，每边误差 ≤0.5 raw px；
        除以 DPR（125%/150%/200%）后仍 ≤0.5 逻辑 px；
      * ``_normalize()`` 的 ``round(tw)`` / ``round(th)`` 再引入
        ≤0.5 逻辑 px；高度先算 ``th = tw * lh / lw``，其中 ``tw/lw ≤ 1``
        不会放大第一步的误差（放大系数 ≤1）；
      → 单张图归一化误差 ≤1.0 px；被测图与基线各一张，最坏 **2.0 px**。
      3px 已无法由取整解释，只可能来自 ≥1px 的真实布局变化，所以不放宽。

    比例容差用同一 2px 预算的最坏组合（宽、高各偏满、方向相反）::

        Δ(w/h) ≤ px/h + w * px/h² = px * (1/h + w/h²)

    旧实现的固定 1% 会误杀纯取整误差：320×240 的图高差 2px 就有
    Δ(w/h)=0.011 > 1%，把「尺子精度」记成了「产品漂移」。
    """
    if height <= 0:
        return GEOMETRY_PX_TOL, 0.0
    ratio = GEOMETRY_PX_TOL * (1.0 / height + width / float(height) ** 2)
    return GEOMETRY_PX_TOL, ratio


def geometry_within_tolerance(new_w: int, new_h: int,
                              old_w: int, old_h: int,
                              px_tol: float = GEOMETRY_PX_TOL) -> bool:
    """绝对像素 + 比例双条件：两条都满足才算同一几何。"""
    if (new_w, new_h) == (old_w, old_h):
        return True
    if old_h <= 0 or new_h <= 0 or old_w <= 0 or new_w <= 0:
        return False
    px, ratio = geometry_tolerance(old_w, old_h)
    px = max(px, float(px_tol))
    if abs(new_w - old_w) > px or abs(new_h - old_h) > px:
        return False
    return abs(new_w / new_h - old_w / old_h) <= ratio


def _geometry_changed(old: QImage, new: QImage,
                      max_height_delta: int) -> bool:
    return not geometry_within_tolerance(
        new.width(), new.height(), old.width(), old.height(),
        px_tol=max_height_delta)


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

    默认判定与布局锁一致：几何同时满足「宽高各 ±2px（量化误差上界，
    推导见 :func:`geometry_tolerance`）」与「比例在由同一 2px 预算推出的
    容差内」，且显式传入的刻度标签几何必须一致。几何不符时抛
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
                    f"reason 点名已声明变更（{', '.join(DESIGN_CHANGES)}）；"
                    f"旧基线记录缺陷态则点名已登记修正"
                    f"（{', '.join(DEFECT_BASELINE_FIXES)}）")
            token = matching_design_change(reason)
            defect = matching_defect_baseline(reason)
            if token is None and defect is None:
                raise BaselineUpdateRefused(
                    "--accept-geometry-change 的 reason 必须点名已声明的设计变更"
                    "或已登记的缺陷基线修正："
                    + "、".join(f"{k}（{v}）" for k, v in DESIGN_CHANGES.items())
                    + "；缺陷基线修正 "
                    + "、".join(f"{k}（{v}）"
                               for k, v in DEFECT_BASELINE_FIXES.items())
                    + f"；当前 reason={reason.strip()!r}")
            gdiff = geometry_diff(old, image)
            if token is not None:
                print(f"[baseline][geometry] 已声明 {token}: {gdiff['text']}",
                      flush=True)
            else:
                print(f"[baseline][defect-fix] 已登记缺陷基线 {defect}: "
                      f"{gdiff['text']}", flush=True)
            if labels_diff is not None:
                print(f"[baseline][labels] {labels_diff['text']}", flush=True)
            for i, ot, nt, dx, dy in (labels_diff["moves"]
                                      if labels_diff else []):
                print(f"[baseline][labels] #{i} {ot!r}→{nt!r} "
                      f"Δx={dx:+.1f}px Δy={dy:+.1f}px", flush=True)
            summary = summarize(old, image, allow_geometry=True)
            summary["geometry"] = gdiff
            summary["labels"] = labels_diff
            if token is not None:
                summary["design_change"] = token
                summary["defect_baseline"] = None
                summary["text"] += ("；几何变更已声明 " + token
                                    + "：" + gdiff["text"]
                                    + ("；" + labels_diff["text"]
                                       if labels_diff else ""))
            else:
                summary["design_change"] = None
                summary["defect_baseline"] = defect
                summary["text"] += ("；缺陷基线修正已登记 " + defect
                                    + "：" + gdiff["text"]
                                    + ("；" + labels_diff["text"]
                                       if labels_diff else ""))
        else:
            # ±2px 取整差不算几何变更，但尺寸确实不相等（缩图比色块需要
            # allow_geometry=True，否则 summarize 会按"尺寸不一致"拒绝）。
            summary = summarize(old, image, allow_geometry=True)
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

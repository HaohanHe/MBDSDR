# SPDX-License-Identifier: MIT
"""
截图基线"受控更新"守卫
=======================

锁定 scripts/ui_baseline.guarded_update 的三条硬规则：
  1. 没有 --reason（人工确认）不许覆盖；
  2. 只允许颜色漂移；尺寸/宽高比这类几何漂移即使带 reason 也红；
  3. 每次更新必须产出可复核摘要（尺寸/粗细色块/变化像素占比）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_baseline_update_guard.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from ui_baseline import (  # noqa: E402
    BaselineUpdateRefused, geometry_tolerance, geometry_within_tolerance,
    guarded_update,
)

app = QApplication.instance() or QApplication(sys.argv)


def _make(path, w, h, fill, patch=None):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(fill))
    if patch:
        color, x, y, pw, ph = patch
        for py in range(y, min(y + ph, h)):
            for px in range(x, min(x + pw, w)):
                img.setPixelColor(px, py, QColor(color))
    img.save(path, "PNG")
    return img


def test_update_requires_reason(tmp_path):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    img = _make(str(tmp_path / "new.png"), 320, 192, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="reason"):
        guarded_update(path, img, "")


def test_color_only_update_allowed_and_summarized(tmp_path):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    img = _make(str(tmp_path / "new.png"), 320, 192, "#F5F3EF",
                patch=("#4F6B79", 40, 60, 80, 40))
    summary = guarded_update(path, img, "文字色对比度修正（只改颜色）")
    # 摘要必须可复核：尺寸/两档色块/像素占比都在
    assert summary["size"][:2] == (320, 192) and summary["size"][2:] == (320, 192)
    assert summary["coarse"][0] > 0, "颜色变了但粗色块没检测到差异"
    assert summary["pixel_ratio"] > 0.0
    assert "size " in summary["text"] and "8×6" in summary["text"] \
        and "变化像素" in summary["text"]
    # 真的写入了
    assert QColor(QImage(path).pixelColor(60, 80)).name().lower() \
        == QColor("#4F6B79").name().lower()


def test_geometry_size_change_refused_even_with_reason(tmp_path):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    wider = _make(str(tmp_path / "new.png"), 336, 192, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="几何"):
        guarded_update(path, wider, "只是重建基线")


def test_geometry_height_change_refused_even_with_reason(tmp_path):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    taller = _make(str(tmp_path / "new.png"), 320, 204, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="几何"):
        guarded_update(path, taller, "只是重建基线")


# ---------------------------------------------------------------------------
# 几何判据边界：±1/±2px 是取整误差、±3px 必须红（不许只改常量不测边界）
# ---------------------------------------------------------------------------
_BASE_W, _BASE_H = 320, 240   # 与 spectrum_1000x600 同型（比值差最容易误杀）


@pytest.mark.parametrize("delta,ok", [(1, True), (2, True), (3, False)])
def test_geometry_height_delta_boundary(tmp_path, delta, ok):
    """合成图高差 ±1/±2px 通过、±3px 失败——两个方向都要测。"""
    base = _make(str(tmp_path / "base.png"), _BASE_W, _BASE_H, "#1E1E20")
    for sign in (1, -1):
        cur = _make(str(tmp_path / f"cur_{sign}.png"),
                    _BASE_W, _BASE_H + sign * delta, "#1E1E20")
        got = geometry_within_tolerance(cur.width(), cur.height(),
                                        base.width(), base.height())
        assert got is ok, f"高度差 {sign * delta}px 判定 {got}，期望 {ok}"


@pytest.mark.parametrize("delta,ok", [(1, True), (2, True), (3, False)])
def test_geometry_width_delta_boundary(tmp_path, delta, ok):
    base = _make(str(tmp_path / "base.png"), _BASE_W, _BASE_H, "#1E1E20")
    for sign in (1, -1):
        cur = _make(str(tmp_path / f"cur_{sign}.png"),
                    _BASE_W + sign * delta, _BASE_H, "#1E1E20")
        got = geometry_within_tolerance(cur.width(), cur.height(),
                                        base.width(), base.height())
        assert got is ok, f"宽度差 {sign * delta}px 判定 {got}，期望 {ok}"


def test_ratio_tolerance_is_derived_from_pixel_budget():
    """320×240 高差 2px 的比值差 0.011 > 固定 1%——旧阈值会误杀取整误差。"""
    base_aspect = _BASE_W / _BASE_H
    assert abs(_BASE_W / (_BASE_H + 2) - base_aspect) > 0.01, \
        "该基线不是会误杀 2px 取整差的几何，边界用例失去意义"
    px, ratio = geometry_tolerance(_BASE_W, _BASE_H)
    assert px == 2.0
    assert ratio > abs(_BASE_W / (_BASE_H + 2) - base_aspect), \
        "比例容差没有覆盖 2px 像素预算的最坏组合"
    assert geometry_within_tolerance(_BASE_W, _BASE_H + 2, _BASE_W, _BASE_H)


@pytest.mark.parametrize("delta,ok", [(1, True), (2, True), (3, False)])
def test_guarded_update_pixel_delta_boundary(tmp_path, delta, ok):
    """更新通道同样放行 ±1/±2px、拒绝 ±3px（且不需要几何变更 token）。"""
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, _BASE_W, _BASE_H, "#1E1E20")
    cur = _make(str(tmp_path / "new.png"), _BASE_W, _BASE_H + delta, "#1E1E20")
    if ok:
        summary = guarded_update(path, cur, "纯取整差，无设计变更")
        assert summary["geometry"] is None, "±2px 取整差被当成几何变更"
        assert QImage(path).height() == _BASE_H + delta
    else:
        with pytest.raises(BaselineUpdateRefused, match="几何"):
            guarded_update(path, cur, "纯取整差，无设计变更")


def test_missing_baseline_can_be_created_with_reason(tmp_path):
    path = str(tmp_path / "panel_1920x1080.png")
    img = _make(str(tmp_path / "new.png"), 320, 180, "#1E1E20")
    summary = guarded_update(path, img, "补测新面板基线")
    assert "新建基线" in summary["text"]
    assert os.path.exists(path)


# ---------------------------------------------------------------------------
# 第二条合法通道：--accept-geometry-change（有意几何变更）
# ---------------------------------------------------------------------------
def test_geometry_change_refused_without_accept_flag(tmp_path):
    """带声明 token 的 reason 也不够：没开通道照样拒绝。"""
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    wider = _make(str(tmp_path / "new.png"), 336, 192, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="几何"):
        guarded_update(path, wider,
                       "spectrum-axis-nice-steps: 频轴刻度 1/2/5 阶梯")


def test_accept_geometry_without_reason_refused(tmp_path):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    wider = _make(str(tmp_path / "new.png"), 336, 192, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="reason"):
        guarded_update(path, wider, "", accept_geometry_change=True)


def test_accept_geometry_with_undeclared_reason_refused(tmp_path):
    """带了参数但原因没点名当次设计变更：仍红。"""
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    wider = _make(str(tmp_path / "new.png"), 336, 192, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="设计变更"):
        guarded_update(path, wider, "只是重建基线",
                       accept_geometry_change=True)


def test_accept_geometry_with_declared_change_passes(tmp_path, capsys):
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    wider = _make(str(tmp_path / "new.png"), 336, 192, "#F5F3EF")
    summary = guarded_update(
        path, wider,
        "spectrum-axis-nice-steps: 频轴改 1/2/5 阶梯 + 按量程自动单位",
        accept_geometry_change=True)
    # 必须同时打印几何 diff（尺寸/宽高比/变化行带）
    out = capsys.readouterr().out
    assert "[baseline][geometry]" in out and "→" in out
    assert summary["design_change"] == "spectrum-axis-nice-steps"
    assert summary["provenance"]["source_commit"] == "d602eabb"
    assert summary["geometry"]["size"] == (320, 192, 336, 192)
    assert summary["geometry"]["text"] and "宽高比" in summary["geometry"]["text"]
    assert "几何变更已声明" in summary["text"]
    assert QImage(path).width() == 336, "几何通道放行后应真的写入"


def test_accept_geometry_with_defect_baseline_reason_passes(tmp_path, capsys):
    """第三条合法通道：旧基线本身记录缺陷态（不是设计变更）。

    freq_readout 的 320x20 是书签长名撑爆右栏时期的归一化产物；按控件真实
    布局重建时必须点名已登记的缺陷修正 token，且摘要与打印都要能追溯。
    """
    path = str(tmp_path / "freq_readout_1000x600.png")
    _make(path, 320, 20, "#1E1E20")
    real = _make(str(tmp_path / "new.png"), 278, 60, "#1E1E20")
    with pytest.raises(BaselineUpdateRefused, match="缺陷基线"):
        guarded_update(path, real, "只是重建基线", accept_geometry_change=True)
    summary = guarded_update(
        path, real,
        "freq-readout-bookmark-blowout: 旧基线记录了已知缺陷（书签长名撑爆"
        "右栏），非本次设计变更",
        accept_geometry_change=True)
    out = capsys.readouterr().out
    assert "[baseline][defect-fix]" in out and "→" in out
    assert summary["defect_baseline"] == "freq-readout-bookmark-blowout"
    assert summary["design_change"] is None
    assert "缺陷基线修正已登记" in summary["text"]
    assert summary["geometry"]["size"] == (320, 20, 278, 60)
    assert QImage(path).width() == 278, "登记缺陷修正后应真的写入"


def test_label_geometry_change_needs_accept_flag(tmp_path):
    """尺寸不变但刻度标签位置变了：默认拒绝，声明后放行并打印标签 diff。"""
    path = str(tmp_path / "panel_1000x600.png")
    base = _make(str(tmp_path / "base.png"), 320, 192, "#F5F3EF")
    labels1 = [{"text": "98.0", "x0": 10, "x1": 44, "y0": 170, "y1": 186},
               {"text": "98.5", "x0": 140, "x1": 174, "y0": 170, "y1": 186}]
    guarded_update(path, base, "首建基线 + 刻度几何 sidecar",
                   label_geometry=labels1)
    same = _make(str(tmp_path / "same.png"), 320, 192, "#F5F3EF")
    labels2 = [{"text": "98.0", "x0": 20, "x1": 54, "y0": 170, "y1": 186},
               {"text": "98.50", "x0": 140, "x1": 180, "y0": 170, "y1": 186}]
    with pytest.raises(BaselineUpdateRefused, match="标签几何"):
        guarded_update(path, same, "只是重建基线", label_geometry=labels2)
    with pytest.raises(BaselineUpdateRefused, match="设计变更"):
        guarded_update(path, same, "只是重建基线",
                       accept_geometry_change=True, label_geometry=labels2)
    summary = guarded_update(
        path, same,
        "spectrum-axis-nice-steps: 频轴刻度阶梯与自动单位",
        accept_geometry_change=True, label_geometry=labels2)
    assert summary["labels"]["moves"], "标签位置变化必须出现在几何 diff 里"
    assert summary["labels"]["count"] == (2, 2)
    assert "标签 2→2" in summary["labels"]["text"]


# ---------------------------------------------------------------------------
# 第四条硬规则：放行 token 必须带 provenance（谁授权/算式/源码提交/时间/实测）
# ---------------------------------------------------------------------------
def test_geometry_change_without_provenance_refused(tmp_path, monkeypatch):
    """token 未登记 provenance：几何变更同样拒绝（不许「人写期望值」）。"""
    import ui_baseline
    monkeypatch.setitem(ui_baseline.DESIGN_CHANGES,
                        "token-without-provenance", "临时：未登记 provenance")
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    wider = _make(str(tmp_path / "new.png"), 336, 192, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="provenance"):
        guarded_update(path, wider, "token-without-provenance: 临时",
                       accept_geometry_change=True)


def test_provenance_missing_field_refused(tmp_path, monkeypatch):
    """provenance 缺任一字段（这里清空 formula）：同样拒绝。"""
    import ui_baseline
    prov = dict(ui_baseline.BASELINE_PROVENANCE["narrow-bottom-dock-74-to-48"])
    prov["formula"] = ""
    monkeypatch.setitem(ui_baseline.BASELINE_PROVENANCE,
                        "narrow-bottom-dock-74-to-48", prov)
    path = str(tmp_path / "panel_1000x600.png")
    _make(path, 320, 192, "#F5F3EF")
    wider = _make(str(tmp_path / "new.png"), 336, 192, "#F5F3EF")
    with pytest.raises(BaselineUpdateRefused, match="provenance"):
        guarded_update(path, wider,
                       "narrow-bottom-dock-74-to-48: 底坞高度变更",
                       accept_geometry_change=True)

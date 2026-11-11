#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
展开末节后滚动上限滞后（2026-11-11 用户裁定的独立件）反证
========================================================

（本件不在 roadmap §7 现有工单号里，由用户当次口述立项：`_sync_container_height`
滞后、末几节顶不到视口顶部。roadmap 单写者未分配编号，此处只写件名不借用 W 号。）

现象（改前 HEAD 实测，offscreen，320×300 视口、8 节、每节正文 500 px）：

  * 展开末节后，容器 `minimumHeight` 从 714 变成 1214，但
    `verticalScrollBar().maximum()` 停在 **414**（应为 1214−300=914）；
  * 把滚动值设到末节标题的内容坐标（689）只能落到 414，末节标题停在
    视口 y=**275**，末几节永远顶不到视口顶部；
  * 各节高度被按旧尺寸挤压（两条 525 px 的节被压成 275，正文肉眼被裁）。

三条根因（都写在 `RightColumn._sync_container_height` 的 docstring 里）：
布局 sizeHint 缓存未失效、容器拉高后未重排、QScrollArea 不监听容器的
minimumHeight 变化。

判据（全部是具体数值，不断言「调用了某个函数」）：

1. 展开末节后 `maximum()` 必须**大于**展开前，且 ≥ 末节标题的内容坐标；
2. `setValue(该坐标)` 后 `value()` 等于该值，末节标题在视口内 **y == 0**；
3. 末节拿到正文完整高度（`body().height() == 500`），没有被挤压；
4. 再调一次 `_sync_container_height()` 幂等：上限、标题坐标、各节高度不变。

改前复跑（`git show HEAD:desktop/right_column.py` 单独放到临时目录，用它
跑同一场景，实测输出 `max_before=414 target_y=689 / max_after=414 value=414
标题视口 y=275`）：前两条判据均 FAIL，所以本用例在 HEAD 上必红。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_right_column_scroll_range.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QPoint, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_VIEW_W = 320
_VIEW_H = 300
_SECTION_COUNT = 8
_BODY_H = 500


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _pump(app, ms=80):
    QTimer.singleShot(ms, app.quit)
    app.exec()


def _build_column(qapp):
    from right_column import RightColumn
    col = RightColumn()
    col.resize(_VIEW_W, _VIEW_H)
    for i in range(_SECTION_COUNT):
        body = QWidget()
        body.setFixedHeight(_BODY_H)
        col.addTab(body, f"节{i}")
    col.show()
    _pump(qapp, 100)
    return col


def _header_y(col, index):
    """节标题在滚动容器坐标系里的 y。"""
    section = col._sections[index]
    return section.header.mapTo(col._container, QPoint(0, 0)).y()


def test_expanding_last_section_grows_scroll_range_and_reaches_top(
        qapp, isolated_profile):
    """展开最后一节：上限要涨、要能把它滚到标题 y=0、正文不能被裁。"""
    col = _build_column(qapp)
    try:
        bar = col.verticalScrollBar()
        last_index = _SECTION_COUNT - 1
        last = col._sections[last_index]
        assert not last.is_expanded(), "前置条件不成立：末节应默认折叠"

        max_before = bar.maximum()
        target_y = _header_y(col, last_index)
        assert max_before > 0, "前置条件不成立：长列没有可滚区间"
        assert target_y > max_before, (
            f"前置条件不成立：末节标题（y={target_y}）没有落在旧上限"
            f"（{max_before}）之外，场景不会触发滞后")

        last.header.click()          # 用户展开末节
        _pump(qapp, 120)
        assert last.is_expanded(), "前置条件不成立：末节没有展开"

        max_after = bar.maximum()
        assert max_after > max_before, (
            f"展开末节后滚动上限没涨：{max_before} -> {max_after}")
        assert max_after >= target_y, (
            f"滚动上限（{max_after}）仍够不到末节标题（y={target_y}），"
            "末节永远顶不到视口顶部")

        bar.setValue(target_y)
        _pump(qapp, 60)
        assert bar.value() == target_y, (
            f"设滚动值 {target_y} 实际落到 {bar.value()}（上限没跟上）")
        y = last.header.mapTo(col.viewport(), QPoint(0, 0)).y()
        assert y == 0, f"末节标题没顶到视口顶：y={y}"
        assert last.header.height() <= col.viewport().height(), (
            "末节标题比视口还高，不完整")

        # 正文完整：没有按旧尺寸被挤压。
        assert last.body().height() == _BODY_H, (
            f"末节正文被裁：{last.body().height()} != {_BODY_H}")
        assert last.height() >= last.header.height() + _BODY_H

        # 幂等：再同步一次，落点与各节高度都不应变化（改前正是二次不同步）。
        col._sync_container_height()
        _pump(qapp, 60)
        assert bar.maximum() == max_after, (
            f"二次同步把上限改了：{max_after} -> {bar.maximum()}")
        assert _header_y(col, last_index) == target_y, "二次同步后标题坐标漂移"
        assert last.body().height() == _BODY_H
    finally:
        col.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

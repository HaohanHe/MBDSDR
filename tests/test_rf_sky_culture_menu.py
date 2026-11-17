# SPDX-License-Identifier: MIT
"""天空视图内右键菜单开关天空文化（Task 87）的离屏契约。

守五件事：

1. 菜单项跟随 ``constellation.system_ids()``，不按文化写死；
2. 默认只有 IAU 勾选，其它文化未勾选；
3. 触发某文化 → 打开；再次触发 → 关闭，勾选与视图状态同步；
4. 「关闭全部天空文化」清空集合且全部取消勾选；
5. 构建菜单本身不改变视图状态（只有触发才写）。

运行:
    python -m pytest tests/test_rf_sky_culture_menu.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()


def _new_view() -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(FIXED_UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(700, 700)
    return v


def _culture_actions(menu):
    """菜单里按文化分的 action（数据为 system_id）；最后一个不是。"""
    return [a for a in menu.actions() if isinstance(a.data(), str)]


def _action(menu, system_id):
    for act in _culture_actions(menu):
        if act.data() == system_id:
            return act
    raise AssertionError(f"菜单缺少文化项: {system_id}")


def _close_all_action(menu):
    for act in menu.actions():
        if act.text() == RFSkyView.CULTURE_CLOSE_ALL_TEXT:
            return act
    raise AssertionError("菜单缺少「关闭全部天空文化」")


# ---------------------------------------------------------------------------
# 1. 结构
# ---------------------------------------------------------------------------
def test_menu_actions_follow_system_ids():
    v = _new_view()
    menu = v._build_culture_menu()
    ids = _constel.system_ids()
    assert menu.title() == "天空文化"
    assert len(menu.actions()) == len(ids) + 1
    assert [a.data() for a in _culture_actions(menu)] == list(ids)
    assert menu.actions()[-1].text() == RFSkyView.CULTURE_CLOSE_ALL_TEXT
    for sid in ids:
        info = _constel.system_info(sid)
        text = _action(menu, sid).text()
        assert info["display_name_zh"] in text
        assert info["tag"] in text


# ---------------------------------------------------------------------------
# 2. 默认状态
# ---------------------------------------------------------------------------
def test_default_only_iau_checked():
    v = _new_view()
    menu = v._build_culture_menu()
    assert _action(menu, _constel.SYSTEM_IAU).isChecked()
    for sid in _constel.system_ids():
        if sid == _constel.SYSTEM_IAU:
            continue
        assert not _action(menu, sid).isChecked()
    assert v.constellation_systems() == (_constel.SYSTEM_IAU,)


# ---------------------------------------------------------------------------
# 3. 单项开关
# ---------------------------------------------------------------------------
def test_trigger_japanese_toggles_view_state():
    v = _new_view()
    menu = v._build_culture_menu()
    act = _action(menu, "japanese")

    act.trigger()
    assert "japanese" in v.constellation_systems()
    assert act.isChecked()

    act.trigger()
    assert "japanese" not in v.constellation_systems()
    assert not act.isChecked()


# ---------------------------------------------------------------------------
# 4. 关闭全部
# ---------------------------------------------------------------------------
def test_close_all_clears_every_culture():
    v = _new_view()
    v.set_constellation_systems([_constel.SYSTEM_IAU, "japanese"])
    menu = v._build_culture_menu()

    _close_all_action(menu).trigger()
    assert v.constellation_systems() == ()
    for act in _culture_actions(menu):
        assert not act.isChecked()


# ---------------------------------------------------------------------------
# 5. 构建无副作用
# ---------------------------------------------------------------------------
def test_build_does_not_change_view_state():
    v = _new_view()
    before = v.constellation_systems()
    menu = v._build_culture_menu()
    assert v.constellation_systems() == before
    # aboutToShow 只刷新勾选镜像，同样不改状态
    menu.aboutToShow.emit()
    assert v.constellation_systems() == before

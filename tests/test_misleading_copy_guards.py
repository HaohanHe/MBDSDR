# -*- coding: utf-8 -*-
"""误导文案门禁：失败态不得伪装成等待/成功，空态不得指向不存在的入口。

每条只钉一个可观察事实：
  * 旧文案在真实触发条件（或对应的纯函数分支）下存在；
  * 改后同条件不再出现旧文案，且给出可执行下一步。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_satellite_failure_is_not_waiting_text():
    """解码失败不能显示「等待卫星下行信号」。"""
    import satellite_image_panel as sip

    assert sip.SAT_FAILED_TEXT != sip.SAT_WAIT_TEXT
    assert "等待" not in sip.SAT_FAILED_TEXT
    assert "失败" in sip.SAT_FAILED_TEXT


def test_spacetime_observer_hint_points_at_existing_control():
    """「未设置观测地点」必须指向真实存在的「更改地点…」，不再提已删除的输入框。"""
    import new_spacetime_panel as nsp

    text = nsp._NO_OBSERVER_HINT
    assert "更改地点…" in text
    assert "输入框" not in text
    assert "地面站坐标" not in text

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


def test_location_download_zero_success_never_claims_offline():
    """ok=0 时不得出现「可离线查看」。"""
    import location_panel as lp

    zero = lp.download_summary(0, 3)
    assert "可离线" not in zero
    assert "没有缓存" in zero

    partial = lp.download_summary(2, 1)
    assert "可离线" in partial and "1 张失败" in partial

    assert "可离线" in lp.download_summary(5, 0)
    assert "可离线" not in lp.download_summary(0, 0)


def test_settings_start_failure_gives_port_action():
    """端口占用要报端口号 + 改端口；其他异常不甩裸 errno 文本。"""
    import errno

    from settings_panel import ServiceSettingsPanel

    busy = ServiceSettingsPanel._start_failure_text(
        "远程控制", OSError(errno.EADDRINUSE, "Address already in use"), 7356)
    assert "7356" in busy and "被占用" in busy and "改端口" in busy

    other = ServiceSettingsPanel._start_failure_text(
        "Web 服务", OSError(errno.EACCES, "Permission denied"), 8080)
    assert "8080" in other and "检查端口" in other
    assert "[Errno" not in other


def test_help_planetarium_points_at_real_entry():
    """帮助不能再写不存在的「顶栏」按钮，必须写左栏页签 / 视图菜单。"""
    import help_panel as hp

    entry = next(t for t in hp.ONBOARDING_STEPS if t[0] == "view.planetarium")
    text = entry[2]
    assert "顶栏" not in text
    assert "星象仪模式" in text
    assert "视图" in text


def test_scanner_empty_state_distinguishes_zero_hits():
    """已连接且扫完 0 段时，空态应指向门限/步进，而不是让用户去连设备。"""
    import scanner_panel as sp

    done_empty = sp.empty_hint_text(True, True, True)
    assert "连上设备" not in done_empty
    assert "门限" in done_empty

    not_started = sp.empty_hint_text(False, True, True)
    assert "连上设备" in not_started

    no_kernel = sp.empty_hint_text(True, True, False)
    assert "连上设备" in no_kernel

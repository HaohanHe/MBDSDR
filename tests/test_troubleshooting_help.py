# SPDX-License-Identifier: MIT
"""
帮助面板「怎么办」页守门
========================

钉住的红线：
  * 15 条精选 + 桌面 UI 可见码条目，现象/原因逐字来自
    ``mbdsdr_ai.error_catalog``（不是手抄错误清单）；
  * 每条「下一步」≥8 字、不含空话、必须点到具体控件名或设置项键名；
  * 搜「没声音」「静噪」都能命中；面板非模态、可搜索；
  * 反证：塞一条「请检查配置」的假条目 → ``validate_remedies`` 必须返回错误。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.error_catalog import ERRORS  # noqa: E402
from troubleshooting import (  # noqa: E402
    CURATED_CODES, VISIBLE_CODES, Remedy, _ui_keys_and_terms, build_remedies,
    concrete_refs, search_remedies, validate_remedies,
)


@pytest.fixture(scope="module")
def remedies():
    return build_remedies()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_remedies_are_real_catalog_entries(remedies):
    expected = set(CURATED_CODES) | set(VISIBLE_CODES)
    assert len(remedies) == len(expected)
    assert len({r.code for r in remedies}) == len(expected)
    assert {r.code for r in remedies} == expected
    for r in remedies:
        spec = ERRORS[r.code]
        assert r.symptom == spec.message, (
            f"{r.code} 的现象不是 error_catalog 原文")
        assert r.cause == spec.meaning, f"{r.code} 的原因不是 error_catalog 原文"
        assert r.group.strip()


def test_every_next_step_is_concrete(remedies):
    problems = validate_remedies(remedies)
    assert problems == [], "「怎么办」条目不合规:\n" + "\n".join(problems)

    keys, terms = _ui_keys_and_terms()
    for r in remedies:
        assert len(r.next_step.strip()) >= 8, f"{r.code} 的下一步太短"
        assert concrete_refs(r.next_step, keys, terms), (
            f"{r.code} 的下一步没有具体控件名/设置项键名")


def test_empty_talk_is_rejected():
    """反证：空话/太短/无具体引用的下一步必须红。"""
    bad_empty = Remedy("not_connected", "连接", "现象", "原因", "请检查配置。")
    bad_short = Remedy("not_connected", "连接", "现象", "原因", "重试")
    bad_vague = Remedy("not_connected", "连接", "现象", "原因",
                       "请重新连接一次看看是否恢复。")
    good = Remedy("not_connected", "连接", "现象", "原因",
                  "点「连接设备...」重新选设备。")
    assert validate_remedies([bad_empty])
    assert validate_remedies([bad_short])
    assert validate_remedies([bad_vague])
    assert validate_remedies([good]) == []


def test_search_hits_user_phrases(remedies):
    assert search_remedies(remedies, "没声音"), "搜「没声音」没有命中"
    quiet = search_remedies(remedies, "静噪")
    assert quiet, "搜「静噪」没有命中"
    assert any(r.code == "empty_signal" for r in quiet)
    assert len(search_remedies(remedies, "")) == len(remedies)
    assert search_remedies(remedies, "zzz_不存在") == []


def test_help_panel_has_searchable_troubleshooting_tab(qapp, remedies):
    from help_panel import HelpPanel
    dlg = HelpPanel()
    assert not dlg.isModal()
    titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert titles == ["三步上手", "能力目录", "怎么办"]
    assert dlg.visible_remedy_count() == len(remedies)

    dlg.tabs.setCurrentIndex(2)
    dlg.trouble_search_edit.setText("没声音")
    assert 0 < dlg.visible_remedy_count() < len(remedies)

    dlg.trouble_search_edit.setText("静噪")
    assert dlg.visible_remedy_count() >= 1

    dlg.trouble_search_edit.setText("zzz_不存在")
    assert dlg.visible_remedy_count() == 0
    assert not dlg.trouble_empty_label.isHidden()

    dlg.search_edit.setText("频谱")
    assert dlg.visible_row_count() > 0
    dlg.deleteLater()


def test_help_panel_initial_tab_can_open_troubleshooting(qapp):
    from help_panel import HelpPanel
    dlg = HelpPanel(initial_tab=2)
    assert dlg.tabs.currentIndex() == 2
    dlg.deleteLater()

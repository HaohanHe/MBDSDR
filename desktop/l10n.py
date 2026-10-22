# SPDX-License-Identifier: MIT
"""desktop i18n（Qt Linguist）加载层。

与 C++ 桌面端（core/l10n + cpp/i18n/*.ts）同一套纪律：
  * 源语言 = 简体中文（tr() 源文即中文，缺翻译时诚实回源文）；
  * en/ja 译文走 Qt Linguist：``desktop/i18n/desktop_{en,ja}.ts`` 经
    ``pyside6-lrelease`` 产出 ``.qm``（构建脚本 desktop/i18n/build_qm.py）；
  * ``ui_language`` 设置值域 zh/en/ja，未知值诚实回退 zh，绝不猜；
  * .qm 缺失（未构建）时保持源语言并提示一次，不装半坏翻译。

本模块的 normalize 部分是纯逻辑（可单测）；install 部分依赖 PySide6。
"""

from __future__ import annotations

import os
from typing import Optional

#: 支持的 UI 语言（自称不翻译）。顺序即设置面板展示顺序。
SUPPORTED_UI_LANGUAGES = ("zh", "en", "ja")

#: 语言自称（设置面板选项文案：各语言用自己的名字，不翻译）。
LANGUAGE_SELF_NAMES = {"zh": "简体中文", "en": "English", "ja": "日本語"}

_warned_missing_qm = False


def normalize_ui_language(value: object) -> str:
    """设置值 -> 语言标签。未知/空 -> zh（源语言），大小写/空白宽容。"""
    v = str(value or "").strip().lower()
    if v.startswith("en"):
        return "en"
    if v.startswith("ja"):
        return "ja"
    return "zh"


def qm_search_dirs() -> list:
    """.qm 搜索目录：desktop/i18n（包内）优先。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return [os.path.join(here, "i18n")]


def install_translator(app, tag: str):
    """按语言标签给 QApplication 装翻译器；源语言 zh 或 .qm 缺失时返回 None。

    必须在任何窗口构造之前调用（Qt 的 tr() 查询只看已安装翻译器）。
    失败路径诚实：返回 None + 一次性 qWarning，绝不装半坏翻译。
    """
    global _warned_missing_qm
    tag = normalize_ui_language(tag)
    if tag == "zh":
        return None   # 源语言：tr() 直接回源文

    from PySide6.QtCore import QTranslator, qWarning

    translator = QTranslator(app)
    for d in qm_search_dirs():
        if translator.load(f"desktop_{tag}", d):
            app.installTranslator(translator)
            return translator
    if not _warned_missing_qm:
        _warned_missing_qm = True
        qWarning(
            f"[mbdsdr] i18n: desktop_{tag}.qm 未找到（未构建）——"
            f"保持源语言 zh；构建方法见 desktop/i18n/build_qm.py")
    return None

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

import logging
import os
from typing import Optional

#: 支持的 UI 语言（自称不翻译）。顺序即设置面板展示顺序。
SUPPORTED_UI_LANGUAGES = ("zh", "en", "ja")

#: 语言自称（设置面板选项文案：各语言用自己的名字，不翻译）。
LANGUAGE_SELF_NAMES = {"zh": "简体中文", "en": "English", "ja": "日本語"}

#: 本应用语言标签 → Qt 自带翻译的语言目录名（``qtbase_<locale>.qm``）。
#: Qt 只提供 zh_CN/zh_TW，简体中文固定取 zh_CN；en 是 Qt 的源语言（见下）。
QT_LOCALE_BY_TAG = {"zh": "zh_CN", "ja": "ja"}

logger = logging.getLogger(__name__)

_warned_missing_qm = False

#: 最近一次 Qt 自带翻译的加载结果（None = 尚未尝试）。
#: 结构：``{"status": "loaded"|"not_found"|"skipped", "locale", "path", "summary"}``。
#: summary 是给人看的一句话，启动日志原样输出——「已加载/未找到/已跳过」必须
#: 能从中直接读出，不允许只留一个布尔值。
_qt_status: Optional[dict] = None

#: 持有 QTranslator 实例（防止被 Python GC 回收；app 的 C++ 父对象也会保活，
#: 两重保险，代价是一个模块级引用）。
_qt_translator = None


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


def qt_translations_dirs() -> list:
    """Qt 自带翻译（``qtbase_*.qm``）的搜索目录，按优先级去重。

    唯一来源是 Qt 自己的 ``QLibraryInfo.TranslationsPath``（PySide6 安装目录下
    ``translations/``）；``MBDSDR_QT_TRANSLATIONS_DIR`` 仅供测试把搜索路径指向
    临时目录（生产进程不设，行为不变）。目录不存在不算错误——调用方按
    「未找到」如实降级，而不是假装加载成功。
    """
    dirs: list = []
    override = (os.environ.get("MBDSDR_QT_TRANSLATIONS_DIR") or "").strip()
    if override:
        dirs.append(override)
    try:
        from PySide6.QtCore import QLibraryInfo
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    except (ImportError, AttributeError):  # pragma: no cover - 无 PySide6 的裁剪环境
        path = ""
    if path:
        dirs.append(path)
    out: list = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def install_qt_translator(app, tag: str) -> dict:
    """加载 Qt 自带的 ``qtbase_<locale>.qm``，让标准控件文案跟随界面语言。

    背景（QGIS `QgsApplication::setTranslation` 同款机制）：我们只翻了应用
    自己的字符串，``QMessageBox`` 的 Yes/No、``QFileDialog`` 的按钮、输入框
    右键菜单仍是 Qt 源语言英文，中文界面里会中英混排。

    与 :func:`install_translator` 同一套诚实纪律：
      * 找不到 `.qm` **不装半坏翻译**，返回 ``status="not_found"`` 并在
        summary 里写明找过哪些目录；
      * ``en`` 是 Qt 的源语言（内置文案本来就是英文），不装，返回
        ``status="skipped"``；
      * 加载成功返回 ``status="loaded"`` 与真实文件路径。

    返回的 dict 同时存进模块状态，:func:`qt_translator_status` 可回读；
    启动日志按 ``summary`` 原样打印一行，用户/支持人员据此判断状态。
    """
    global _qt_translator, _qt_status
    tag = normalize_ui_language(tag)
    if tag == "en":
        _qt_status = {
            "status": "skipped",
            "locale": "en",
            "path": None,
            "summary": "Qt 自带翻译已跳过（English 是 Qt 源语言，内置按钮文案本就是英文）",
        }
        logger.info("[i18n] %s", _qt_status["summary"])
        return _qt_status

    locale = QT_LOCALE_BY_TAG.get(tag)
    if not locale:  # pragma: no cover - SUPPORTED_UI_LANGUAGES 扩展时的兜底
        _qt_status = {
            "status": "skipped",
            "locale": tag,
            "path": None,
            "summary": f"Qt 自带翻译已跳过（语言 {tag!r} 没有对应的 qtbase 目录名）",
        }
        logger.info("[i18n] %s", _qt_status["summary"])
        return _qt_status

    dirs = qt_translations_dirs()
    from PySide6.QtCore import QTranslator

    translator = QTranslator(app)
    for d in dirs:
        if translator.load(f"qtbase_{locale}", d):
            app.installTranslator(translator)
            _qt_translator = translator
            path = os.path.join(d, f"qtbase_{locale}.qm")
            _qt_status = {
                "status": "loaded",
                "locale": locale,
                "path": path,
                "summary": f"Qt 自带翻译已加载（qtbase_{locale}.qm：{path}）",
            }
            logger.info("[i18n] %s", _qt_status["summary"])
            return _qt_status

    searched = "、".join(dirs) if dirs else "（无可用目录：PySide6 安装未带 translations/）"
    _qt_status = {
        "status": "not_found",
        "locale": locale,
        "path": None,
        "summary": (
            f"Qt 自带翻译未找到（qtbase_{locale}.qm 不在 {searched}）；"
            "Qt 内置按钮/菜单文案保持英文"),
    }
    logger.warning("[i18n] %s", _qt_status["summary"])
    return _qt_status


def qt_translator_status() -> Optional[dict]:
    """最近一次 Qt 自带翻译的加载结果；未尝试过返回 None。"""
    return dict(_qt_status) if _qt_status is not None else None

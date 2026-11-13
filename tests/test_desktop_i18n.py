# SPDX-License-Identifier: MIT
"""desktop i18n（Qt Linguist）契约测试。

与 C++（test_l10n）/ Flutter（l10n_locale_test）同一套纪律：
  * 语言标签归一（未知值诚实回退 zh，不猜）；
  * ui_language 设置登记与落盘往返（DesktopSettings 明文 JSON）；
  * **真实管线**：pyside6-lrelease 从入库 .ts 构建 .qm，QTranslator 加载后
    双语译文逐字命中（en/ja）；zh 源语言不装翻译器、tr() 直接回源文；
  * .qm 缺失时诚实回源语言（install_translator 返回 None），不装半坏翻译。
"""
import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import desktop  # noqa: F401  # 触发 desktop/__init__ 双上下文垫片（sys.path 注入）
import l10n  # noqa: E402
from settings import DEFAULTS, DesktopSettings  # noqa: E402


def _normalize_cases():
    assert l10n.normalize_ui_language("zh") == "zh"
    assert l10n.normalize_ui_language("zh_CN") == "zh"
    assert l10n.normalize_ui_language("en") == "en"
    assert l10n.normalize_ui_language("EN_US") == "en"
    assert l10n.normalize_ui_language("ja") == "ja"
    assert l10n.normalize_ui_language("ja_JP") == "ja"
    # 未知/空：诚实回源语言 zh，不猜。
    assert l10n.normalize_ui_language("") == "zh"
    assert l10n.normalize_ui_language(None) == "zh"
    assert l10n.normalize_ui_language("klingon") == "zh"
    # 支持清单与自称表齐全（语言自称不翻译）。
    assert l10n.SUPPORTED_UI_LANGUAGES == ("zh", "en", "ja")
    for tag in l10n.SUPPORTED_UI_LANGUAGES:
        assert l10n.LANGUAGE_SELF_NAMES[tag]


def _settings_roundtrip():
    assert DEFAULTS.get("ui_language") == "zh"
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "desktop_settings.json")
        s = DesktopSettings.load(path=path)
        s.set("ui_language", "ja")
        s.flush()
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        assert raw.get("ui_language") == "ja"
        s2 = DesktopSettings.load(path=path)
        assert s2.get("ui_language") == "ja"
        # 未知语言写盘后读回归一为 zh（诚实回退）。
        s2.set("ui_language", "klingon")
        s2.flush()
        assert l10n.normalize_ui_language(
            DesktopSettings.load(path=path).get("ui_language")) == "zh"


def _build_qm(tmpdir, tag):
    """用官方 pyside6-lrelease 从入库 .ts 构建 .qm 到 tmpdir（真管线）。"""
    ts = os.path.join(_ROOT, "desktop", "i18n", f"desktop_{tag}.ts")
    qm = os.path.join(tmpdir, f"desktop_{tag}.qm")
    r = subprocess.run(["pyside6-lrelease", ts, "-qm", qm],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"lrelease failed: {r.stdout}{r.stderr}"
    assert os.path.exists(qm) and os.path.getsize(qm) > 0
    return qm


def _translator_pipeline():
    from PySide6.QtCore import QTranslator
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for tag, connect, disconnect in (("en", "Connect", "Disconnect"),
                                         ("ja", "接続", "切断")):
            qm = _build_qm(td, tag)
            tr = QTranslator()
            assert tr.load(qm[:-3], td), f"{tag}: .qm must load"
            # context = 类名（pyside6-lupdate 口径，探针实测）；译文逐字命中。
            assert tr.translate("MainWindow", "连接") == connect, tag
            assert tr.translate("MainWindow", "断开") == disconnect, tag
        # zh 源语言：不装翻译器，translate 直接回源文。
        tr_zh = QTranslator()
        assert not tr_zh.load("desktop_zh", td)


def _install_translator_paths():
    import tempfile
    # .qm 缺失：诚实返回 None（不装半坏翻译）。
    orig = l10n.qm_search_dirs
    try:
        with tempfile.TemporaryDirectory() as td:
            l10n.qm_search_dirs = lambda: [td]
            assert l10n.install_translator(app, "ja") is None
            # zh：无需翻译器。
            assert l10n.install_translator(app, "zh") is None
            # .qm 就位：en 真装载（返回翻译器）。
            _build_qm(td, "en")
            translator = l10n.install_translator(app, "en")
            assert translator is not None
            app.removeTranslator(translator)
    finally:
        l10n.qm_search_dirs = orig


def _qt_builtin_button_texts():
    """取 Qt 内置标准按钮的实际文本（QMessageBox 的 OK/Cancel）。"""
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox()
    box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
    try:
        return (box.button(QMessageBox.Ok).text(),
                box.button(QMessageBox.Cancel).text())
    finally:
        box.deleteLater()


def _available_qtbase_qm(locale):
    for d in l10n.qt_translations_dirs():
        path = os.path.join(d, f"qtbase_{locale}.qm")
        if os.path.isfile(path):
            return path
    return None


def _qt_builtin_translation_changes_text():
    """真管线：加载 qtbase_zh_CN.qm 后，Qt 内置按钮文本必须真的变中文。

    不是「调了 load() 就算」——取同一个内置字符串在加载前后的实际文本，
    断言不同；before 必须是 Qt 源语言英文（没有别的翻译器混进来）。
    """
    import pytest
    if _available_qtbase_qm("zh_CN") is None:
        pytest.skip("本机 PySide6 未带 qtbase_zh_CN.qm，无法验证真实文案变化"
                    "（诚实跳过，不用假翻译冒充）")
    before = _qt_builtin_button_texts()
    assert before == ("OK", "Cancel"), (
        f"加载前 Qt 内置按钮应为源语言英文，实际 {before!r}"
        "（若有其他翻译器残留，先查测试隔离）")
    try:
        status = l10n.install_qt_translator(app, "zh")
        assert status["status"] == "loaded", status
        assert status["path"] and os.path.isfile(status["path"]), status
        assert l10n.qt_translator_status() == status
        after = _qt_builtin_button_texts()
    finally:
        translator = getattr(l10n, "_qt_translator", None)
        if translator is not None:
            app.removeTranslator(translator)
            l10n._qt_translator = None
        l10n._qt_status = None
    assert after != before, f"加载 qtbase_zh_CN.qm 后按钮文本未变化：{before!r}"
    assert after[0] != "OK" and after[1] != "Cancel", (
        f"Qt 内置按钮仍是英文，说明翻译没生效：{after!r}")


def _qt_missing_qm_degrades_honestly():
    """找不到 qtbase_*.qm：如实报「未找到」且不装翻译器（不静默通过）。"""
    import tempfile
    orig = l10n.qt_translations_dirs
    try:
        with tempfile.TemporaryDirectory() as td:
            l10n.qt_translations_dirs = lambda: [td]
            before = _qt_builtin_button_texts()
            status = l10n.install_qt_translator(app, "ja")
            after = _qt_builtin_button_texts()
            assert status["status"] == "not_found", status
            assert "未找到" in status["summary"], status
            assert status["path"] is None, status
            # 搜索目录必须写进摘要：说清「找过哪里」，不是只说一句失败。
            assert td in status["summary"], status["summary"]
            assert l10n.qt_translator_status() == status
            assert after == before, "找不到 .qm 时不得装任何翻译（文案不许变）"
    finally:
        l10n.qt_translations_dirs = orig
        l10n._qt_status = None


def _qt_english_is_skipped():
    """en 是 Qt 源语言：明确标「已跳过」，不去装一个同义空翻译。"""
    before = _qt_builtin_button_texts()
    status = l10n.install_qt_translator(app, "en")
    after = _qt_builtin_button_texts()
    assert status["status"] == "skipped", status
    assert "已跳过" in status["summary"], status
    assert after == before, "en 跳过时不得改动内置文案"


def test_normalize_ui_language():
    _normalize_cases()


def test_settings_roundtrip():
    _settings_roundtrip()


def test_translator_pipeline():
    _translator_pipeline()


def test_install_translator_paths():
    _install_translator_paths()


def test_qt_builtin_translation_changes_text():
    _qt_builtin_translation_changes_text()


def test_qt_missing_qm_degrades_honestly():
    _qt_missing_qm_degrades_honestly()


def test_qt_english_is_skipped():
    _qt_english_is_skipped()

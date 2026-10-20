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


def test_normalize_ui_language():
    _normalize_cases()


def test_settings_roundtrip():
    _settings_roundtrip()


def test_translator_pipeline():
    _translator_pipeline()


def test_install_translator_paths():
    _install_translator_paths()

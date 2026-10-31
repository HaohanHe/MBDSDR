# SPDX-License-Identifier: MIT
"""国际化锚点：i18n 原子 + ToolRegistry 语言缝 + gps_bit_sync 首接线。

诚实纪律断言：
  * 缺目录/缺键必须回退原文（返回 key 本身或 base description），
    绝不编造译文；
  * 不传 language 时 list_tools / get_tool_definitions 与接线前
    行为字节级一致（默认路径零扰动）；
  * 传 language 时文件目录优先于内联 description_i18n，内联优先原文；
  * get_tool_definitions 的语言覆盖不得污染注册态（浅拷贝）。
"""
from __future__ import annotations

import pytest

from mbdsdr_ai import i18n
from mbdsdr_ai.gps_bit_sync import register_tool_registry as reg_gbs
from mbdsdr_ai.tool_registry import ToolRegistry


@pytest.fixture(autouse=True)
def _isolate_lang(monkeypatch):
    monkeypatch.delenv(i18n._ENV_VAR, raising=False)
    i18n._cache.clear()
    yield
    i18n._cache.clear()


def test_missing_catalog_returns_key_honestly():
    assert i18n.tr("no.such.key", lang="fr") == "no.such.key"


def test_en_catalog_loads_and_resolves():
    text = i18n.tr("tool.nav_bit_sync.description", lang="en")
    assert text != "tool.nav_bit_sync.description"
    assert "bit synchronization" in text


def test_env_var_selects_language(monkeypatch):
    monkeypatch.setenv(i18n._ENV_VAR, "en")
    assert i18n.get_language() == "en"
    assert i18n.tr("tool.how_validate.description").startswith("Per-field")


def test_set_language_rejects_garbage():
    with pytest.raises(ValueError):
        i18n.set_language("..bad;;")


def test_format_kwargs_and_failure_fallback():
    i18n._cache["test"] = {"hi": "Hello {name}"}
    assert i18n.tr("hi", lang="test", name="X") == "Hello X"
    # 占位缺参：回退目录原文，不炸 UI
    assert i18n.tr("hi", lang="test") == "Hello {name}"


def test_catalog_stats_reports_loaded():
    i18n.load_catalog("en")
    stats = i18n.catalog_stats()
    assert stats["en"]["keys"] == 3


def _dummy(name: str = "t"):
    def h(args):
        return None
    return h


def test_list_tools_default_path_byte_identical():
    r = ToolRegistry()
    r.register("t", "中文描述", {"type": "object"}, _dummy())
    before = r.list_tools()
    r2 = ToolRegistry()
    r2.register("t", "中文描述", {"type": "object"}, _dummy())
    assert before == r2.list_tools()
    assert before[0]["description"] == "中文描述"


def test_language_resolution_order_catalog_then_inline_then_base():
    r = ToolRegistry()
    r.register("t", "原文", {"type": "object"}, _dummy(),
               description_i18n={"en": "inline-en", "ja": "inline-ja"})
    i18n._cache["en"] = {"tool.t.description": "catalog-en"}
    en = {t["name"]: t["description"] for t in r.list_tools(language="en")}
    ja = {t["name"]: t["description"] for t in r.list_tools(language="ja")}
    fr = {t["name"]: t["description"] for t in r.list_tools(language="fr")}
    assert en["t"] == "catalog-en"      # 文件目录优先
    assert ja["t"] == "inline-ja"       # 内联其次
    assert fr["t"] == "原文"             # 缺失诚实回退原文


def test_get_tool_definitions_language_does_not_mutate():
    r = ToolRegistry()
    r.register("t", "原文", {"type": "object"}, _dummy())
    defs_en = r.get_tool_definitions(language="en")
    assert defs_en[0]["function"]["description"] == "原文"  # 无目录/内联 → 原文
    assert r.tools["t"]["definition"]["function"]["description"] == "原文"
    i18n._cache["en"] = {"tool.t.description": "catalog-en"}
    defs_en2 = r.get_tool_definitions(language="en")
    assert defs_en2[0]["function"]["description"] == "catalog-en"
    assert r.tools["t"]["definition"]["function"]["description"] == "原文"


def test_gps_bit_sync_tools_translated_via_catalog():
    """首接线：locale/en.json 数据驱动，注册代码零改动。"""
    r = ToolRegistry()
    reg_gbs(r)
    zh = {t["name"]: t["description"] for t in r.list_tools()}
    en = {t["name"]: t["description"] for t in r.list_tools(language="en")}
    for name in ("nav_bit_sync", "subframe_assemble", "how_validate"):
        assert name in zh and name in en
        assert en[name] != zh[name]
        assert not any("\u4e00" <= c <= "\u9fff" for c in en[name])

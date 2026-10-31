# SPDX-License-Identifier: MIT
"""国际化原子：stdlib-only 文案查找（零第三方依赖）。

设计原则（诚实纪律优先于覆盖度）：
  * 目录是 ``mbdsdr_ai/locale/<lang>.json`` 的扁平键值 JSON；缺失文件、
    缺失键一律**回退原文**，绝不编造译文（没有目录就等于现状行为）；
  * 语言选择顺序：显式参数 > 环境变量 ``MBDSDR_LANG`` > 默认 "zh"；
  * 目录按需惰性加载并缓存；``catalog_stats`` 如实上报各语言覆盖数，
    供健康检查与 CI 锚点使用。

接线方式（见 tool_registry.ToolRegistry.register 的 description_i18n）：
注册工具时携带 ``description_i18n={"en": ...}``，``list_tools(language="en")``
时以目录/内联译文覆盖默认描述；不传 language 行为与未接线前字节级一致。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

_LOCALE_DIR = Path(__file__).resolve().parent / "locale"
_ENV_VAR = "MBSDR_LANG"
_DEFAULT_LANG = "zh"

_cache: Dict[str, Dict[str, str]] = {}


def get_language() -> str:
    """当前生效语言（环境变量可读实时改，便于测试与进程级覆盖）。"""
    return os.environ.get(_ENV_VAR, _DEFAULT_LANG)


def normalize_lang(tag: str) -> str:
    """BCP-47 风格语言码 → 目录主子标签（'zh-CN'/'zh_CN' → 'zh'）。

    目录按主子标签组织（en/zh）；未知标签原样返回——load_catalog
    对缺失文件返回空表，tr/描述解析诚实回退原文。
    """
    return (tag or "").strip().replace("_", "-").split("-")[0].lower()


def set_language(lang: str) -> None:
    """设置当前语言（进程内；等价于设置 MBDSDR_LANG）。"""
    if not lang or not lang.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"语言码非法: {lang!r}")
    os.environ[_ENV_VAR] = lang


def load_catalog(lang: str) -> Dict[str, str]:
    """加载语言目录（惰性 + 缓存）。文件缺失返回空表——回退原文。"""
    if lang in _cache:
        return _cache[lang]
    path = _LOCALE_DIR / f"{lang}.json"
    if not path.is_file():
        _cache[lang] = {}
        return _cache[lang]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError(f"语言目录 {path} 必须是 str→str 扁平 JSON")
    _cache[lang] = data
    return _cache[lang]


def tr(key: str, lang: Optional[str] = None, **kwargs: Any) -> str:
    """查键；缺失时原样返回 key（诚实回退，不编造）。

    kwargs 用于 ``str.format`` 占位（目录值含 ``{name}`` 之类时）；
    格式化失败同样回退原文，绝不让 UI 因文案炸掉。
    """
    lang = lang or get_language()
    text = load_catalog(lang).get(key)
    if text is None:
        return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def catalog_stats() -> Dict[str, Dict[str, Any]]:
    """各语言目录覆盖统计（已加载的语言；文件缺失记 keys=0）。"""
    stats: Dict[str, Dict[str, Any]] = {}
    for lang in sorted(_cache):
        stats[lang] = {"keys": len(_cache[lang])}
    return stats

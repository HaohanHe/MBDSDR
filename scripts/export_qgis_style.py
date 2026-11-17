#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""为 ``export_geojson.py`` 的三个图层生成 QGIS 3.x QML 样式。

为什么要有这个脚本
==================
GeoJSON 落盘之后，QGIS 里默认是随机配色的细线小点，台址读不出「科研/公众」
的区别，轨迹和覆盖圈也分不清主次。手工在 QGIS 里调一遍样式，换台机器就
丢了。QML 是纯文本，可以和 GeoJSON 一起入仓/随产物分发。

三个图层（与 ``export_geojson.py`` 一一对应）：

* ``observing_sites.qml``：Point，圆点符号 + ``name_zh`` 标注，
  按 ``access``（research/public/unknown）分类配色；
* ``ground_track.qml``：LineString，单符号描边；
* ``footprint.qml``：Polygon + 跨日界线时的 LineString + 星下点 Point，
  QGIS 用规则渲染按真实字段 ``role`` 分派：面半透明填充、边界线描边、
  星下点小圆点。

字段口径
========
QML 只引用 GeoJSON 里真实存在的字段（见 ``mbdsdr_ai/astronomy/
observing_sites.py`` 与 ``mbdsdr_ai/ground_track.py``）。坐标是几何本身，
不是属性；海拔字段名是 ``alt_m``（可能为 null），不编造 ``elevation``。

诚实边界
========
本机没有安装 QGIS。脚本只保证 QML 是合法 XML、字段名与几何类型对得上，
**渲染效果未在 QGIS 内验证**（见 ``docs/qgis-import.md``）。

用法
====
    python scripts/export_qgis_style.py
    python scripts/export_qgis_style.py --out D:\\mbdsdr\\output
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT.parent / "output"

#: QGIS 3.x 能读的 QML 版本标记；不是硬依赖，QGIS 会按自己的版本解析。
QGIS_VERSION = "3.28.0-Firenze"

#: ``access`` → (标签, RGBA)。顺序即图例顺序。
ACCESS_CATEGORIES: Tuple[Tuple[str, str, str], ...] = (
    ("research", "科研台址（不对外开放）", "217,95,2,255"),
    ("public", "公众观星点", "27,158,119,255"),
    ("unknown", "性质未标注", "117,112,179,255"),
)

#: 图层键 → 输出文件名。与 GeoJSON 同名同目录，QGIS 添加图层时会自动找同名 QML。
FILES: Dict[str, str] = {
    "observing_sites": "observing_sites.qml",
    "ground_track": "ground_track.qml",
    "footprint": "footprint.qml",
}

#: 图层键 → 人类可读标题，仅用于终端报告。
TITLES: Dict[str, str] = {
    "observing_sites": "观测台址与公众观星点（Point）",
    "ground_track": "卫星地面轨迹（LineString）",
    "footprint": "卫星覆盖圈（Polygon/LineString + Point）",
}

# 固定 key，保证重复运行字节一致；QGIS 只要求唯一，不要求随机。
_RULE_KEYS = (
    "{5b5c3f0e-2a1d-4a0e-9d43-6f3a2f8d1c01}",
    "{5b5c3f0e-2a1d-4a0e-9d43-6f3a2f8d1c02}",
    "{5b5c3f0e-2a1d-4a0e-9d43-6f3a2f8d1c03}",
)
_RULES_KEY = "{5b5c3f0e-2a1d-4a0e-9d43-6f3a2f8d1c00}"


def _sub(parent: ET.Element, tag: str, *,
         text: str | None = None, **attrs: str) -> ET.Element:
    """``SubElement`` + 属性转字符串，省掉到处写 ``str()``。"""
    element = ET.SubElement(parent, tag)
    for key, value in attrs.items():
        element.set(key, value)
    if text is not None:
        element.text = text
    return element


def _option_map(parent: ET.Element,
                pairs: Sequence[Tuple[str, str]]) -> ET.Element:
    """QGIS 符号层用 ``<Option type="Map">`` 存参数。"""
    option = _sub(parent, "Option", type="Map")
    for name, value in pairs:
        _sub(option, "Option", type="QString", name=name, value=value)
    return option


def _data_defined(parent: ET.Element) -> None:
    """QGIS 每个符号层都带一段空的数据定义占位。"""
    option = _sub(parent, "Option", type="Map")
    _sub(option, "Option", type="QString", name="name", value="")
    _sub(option, "Option", name="properties")
    _sub(option, "Option", type="QString", name="type", value="collection")


def _marker_symbol(symbols: ET.Element, index: int, color_rgba: str,
                   size_mm: str = "4.6") -> ET.Element:
    symbol = _sub(symbols, "symbol", type="marker", name=str(index),
                  alpha="1", clip_to_extent="1", frame_rate="10",
                  force_rhr="0", is_animated="0")
    layer = _sub(symbol, "layer", **{"class": "SimpleMarker", "enabled": "1",
                                     "pass": "0", "locked": "0"})
    _option_map(layer, (
        ("name", "circle"),
        ("color", color_rgba),
        ("outline_color", "255,255,255,255"),
        ("outline_style", "solid"),
        ("outline_width", "0.6"),
        ("outline_width_unit", "MM"),
        ("size", size_mm),
        ("size_unit", "MM"),
        ("angle", "0"),
        ("offset", "0,0"),
        ("offset_unit", "MM"),
        ("horizontal_anchor_point", "1"),
        ("vertical_anchor_point", "1"),
    ))
    _data_defined(layer)
    return symbol


def _line_symbol(symbols: ET.Element, index: int, color_rgba: str,
                 width_mm: str = "1.4") -> ET.Element:
    symbol = _sub(symbols, "symbol", type="line", name=str(index),
                  alpha="1", clip_to_extent="1", frame_rate="10",
                  force_rhr="0", is_animated="0")
    layer = _sub(symbol, "layer", **{"class": "SimpleLine", "enabled": "1",
                                     "pass": "0", "locked": "0"})
    _option_map(layer, (
        ("line_color", color_rgba),
        ("line_style", "solid"),
        ("line_width", width_mm),
        ("line_width_unit", "MM"),
        ("offset", "0"),
        ("offset_unit", "MM"),
        ("capstyle", "round"),
        ("joinstyle", "round"),
        ("use_custom_dash", "0"),
        ("dash_pattern", "5;2"),
        ("align_dash_pattern", "0"),
        ("draw_inside_polygon", "0"),
    ))
    _data_defined(layer)
    return symbol


def _fill_symbol(symbols: ET.Element, index: int, color_rgba: str,
                 outline_rgba: str) -> ET.Element:
    symbol = _sub(symbols, "symbol", type="fill", name=str(index),
                  alpha="1", clip_to_extent="1", frame_rate="10",
                  force_rhr="0", is_animated="0")
    layer = _sub(symbol, "layer", **{"class": "SimpleFill", "enabled": "1",
                                     "pass": "0", "locked": "0"})
    _option_map(layer, (
        ("color", color_rgba),
        ("style", "solid"),
        ("outline_color", outline_rgba),
        ("outline_style", "solid"),
        ("outline_width", "0.8"),
        ("outline_width_unit", "MM"),
        ("joinstyle", "round"),
        ("offset", "0,0"),
        ("offset_unit", "MM"),
    ))
    _data_defined(layer)
    return symbol


def _point_labeling(root: ET.Element) -> None:
    """点层标注：``name_zh`` 字段，带白色描边，压在圆点上方。"""
    labeling = _sub(root, "labeling", type="simple")
    settings = _sub(labeling, "settings", calloutType="simple")
    style = _sub(settings, "text-style",
                 fieldName="name_zh", isExpression="0",
                 fontFamily="Microsoft YaHei", fontSize="10",
                 fontSizeUnit="Point", fontWeight="50", fontItalic="0",
                 fontUnderline="0", fontStrikeout="0",
                 textColor="30,30,30,255", textOpacity="1",
                 namedStyle="Regular", textOrientation="horizontal")
    _sub(style, "text-buffer", bufferDraw="1", bufferSize="1",
         bufferSizeUnits="MM", bufferOpacity="0.85",
         bufferColor="255,255,255,255", bufferJoinStyle="128")
    _sub(style, "text-format", multilineHeight="1", wrapChar="")
    _sub(settings, "placement", placement="0", dist="1.8", distUnits="MM",
         offsetType="0", quadOffset="4", xOffset="0", yOffset="0")
    _sub(settings, "rendering", scaleVisibility="0",
         fontLimitPixelSize="0", fontMinPixelSize="3", fontMaxPixelSize="10000",
         upsidedownLabels="0")


def _root() -> ET.Element:
    return ET.Element("qgis", {
        "version": QGIS_VERSION,
        "styleCategories": "Symbology|Labeling",
    })


def build_observing_sites() -> ET.Element:
    """点层：按真实字段 ``access`` 分类，圆点 + ``name_zh`` 标注。"""
    root = _root()
    renderer = _sub(root, "renderer-v2", type="categorizedSymbol",
                    attr="access", symbollevels="0", forceraster="0",
                    enableorderby="0", referencescale="-1")
    categories = _sub(renderer, "categories")
    for index, (value, label, _color) in enumerate(ACCESS_CATEGORIES):
        _sub(categories, "category", value=value, label=label,
             symbol=str(index), render="true")
    symbols = _sub(renderer, "symbols")
    for index, (_value, _label, color) in enumerate(ACCESS_CATEGORIES):
        _marker_symbol(symbols, index, color)
    _point_labeling(root)
    return root


def build_ground_track() -> ET.Element:
    """线层：单符号描边。轨迹按段拆成多个 LineString，单符号即可覆盖。"""
    root = _root()
    renderer = _sub(root, "renderer-v2", type="singleSymbol",
                    symbollevels="0", forceraster="0", enableorderby="0",
                    referencescale="-1")
    symbols = _sub(renderer, "symbols")
    _line_symbol(symbols, 0, "56,132,255,255")
    return root


def build_footprint() -> ET.Element:
    """覆盖圈层：按真实字段 ``role`` 分派面/线/点（该层是混合几何）。"""
    root = _root()
    renderer = _sub(root, "renderer-v2", type="RuleRenderer",
                    symbollevels="0", forceraster="0", enableorderby="0",
                    referencescale="-1")
    rules = _sub(renderer, "rules", key=_RULES_KEY)
    for index, (role, label) in enumerate((
            ("footprint", "覆盖圈（面，半透明填充）"),
            ("footprint_boundary", "覆盖圈跨日界线边界（线）"),
            ("footprint_subpoint", "星下点（点）"),
    )):
        _sub(rules, "rule", key=_RULE_KEYS[index],
             filter=f""""role" = '{role}'""", label=label,
             symbol=str(index), checkstate="0")
    symbols = _sub(renderer, "symbols")
    # 半透明填充：alpha=90/255，能看见底图又读得清范围。
    _fill_symbol(symbols, 0, "255,153,0,90", "214,124,0,255")
    _line_symbol(symbols, 1, "214,124,0,255", "1.2")
    _marker_symbol(symbols, 2, "214,39,40,255", size_mm="3.6")
    return root


BUILDERS: Dict[str, Callable[[], ET.Element]] = {
    "observing_sites": build_observing_sites,
    "ground_track": build_ground_track,
    "footprint": build_footprint,
}

_DOCTYPE = "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"


def build_qml_text(layer: str) -> str:
    """生成指定图层的 QML 文本（UTF-8，带 QGIS DOCTYPE）。"""
    try:
        builder = BUILDERS[layer]
    except KeyError:
        raise ValueError(f"未知图层：{layer}（可选：{', '.join(sorted(BUILDERS))}）")
    root = builder()
    ET.indent(root, space=" ")
    return _DOCTYPE + ET.tostring(root, encoding="unicode") + "\n"


def write_text_atomic(path: Path, text: str) -> None:
    """同目录临时文件 + ``os.replace``，失败不留半截样式。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(
        description="为 export_geojson.py 的三个图层生成 QGIS 3.x QML 样式")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"输出目录（默认 {DEFAULT_OUT}）")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        sizes: List[Tuple[str, int]] = []
        for layer in FILES:
            text = build_qml_text(layer)
            path = out_dir / FILES[layer]
            write_text_atomic(path, text)
            sizes.append((FILES[layer], path.stat().st_size))
    except (OSError, ValueError) as exc:
        print(f"样式生成失败：{exc}", file=sys.stderr)
        return 1

    print(f"输出目录：{out_dir}")
    for layer, (filename, size) in zip(FILES, sizes):
        print(f"  {filename}：{size} 字节 —— {TITLES[layer]}")
    print("说明：QML 只经 XML 与字段校验，本机未安装 QGIS，渲染效果未验证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

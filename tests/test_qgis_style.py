# SPDX-License-Identifier: MIT
"""QGIS QML 样式导出：XML 合法、几何类型对、字段不编造。

背景
----
``scripts/export_qgis_style.py`` 给三个 GeoJSON 图层配样式。本机没有
QGIS，渲染效果无法自动验收，但下面这些可以机械钉住：

1. 三个 QML 都能被 ``xml.etree.ElementTree`` 解析（QGIS 读不了坏 XML）；
2. 根节点是 ``qgis``，点/线/面的符号类型分别是 marker/line/fill；
3. 点层确实按 ``access`` 分类，并用 ``name_zh`` 标注；
4. QML 引用的字段必须出现在同一图层真实导出的 GeoJSON 属性里——
   防止把 ``elevation`` 之类不存在的字段写进样式，加载后静默失效；
5. ``--out`` 可覆盖目录，失败非零退出且不留半截文件。
"""

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STYLE_SCRIPT = ROOT / "scripts" / "export_qgis_style.py"
GEOJSON_SCRIPT = ROOT / "scripts" / "export_geojson.py"

TLE = "experiments/satellite_rx/data/iss.tle"
START = "2026-11-17T00:00:00Z"

STYLES = {
    "observing_sites": "observing_sites.qml",
    "ground_track": "ground_track.qml",
    "footprint": "footprint.qml",
}


def _run(script, *args):
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180)


def _build_styles(tmp_path):
    out = tmp_path / "styles"
    r = _run(STYLE_SCRIPT, "--out", str(out))
    assert r.returncode == 0, r.stderr
    return {name: ET.parse(out / filename).getroot()
            for name, filename in STYLES.items()}, out


def _export_geojson(tmp_path, layer):
    out = tmp_path / f"{layer}.geojson"
    if layer == "observing-sites":
        args = [str(out)]
    else:
        args = [str(out), "--layer", layer, "--tle", TLE,
                "--sat", "ISS", "--start", START]
    r = _run(GEOJSON_SCRIPT, *args)
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def _referenced_fields(root):
    """收集 QML 里真正引用到的字段名：分类/标注属性 + 规则过滤表达式。"""
    fields = set()
    for element in root.iter():
        for attr in ("fieldName", "attr"):
            value = element.get(attr)
            if value:
                fields.add(value)
        expression = element.get("filter")
        if expression:
            fields.update(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"',
                                     expression))
    return fields


def _symbol_types(root):
    return [symbol.get("type")
            for symbol in root.iter("symbol")]


class TestQmlStructure:
    def test_all_three_parse_as_valid_xml(self, tmp_path):
        roots, out = _build_styles(tmp_path)
        assert set(roots) == set(STYLES)
        for layer, root in roots.items():
            assert root.tag == "qgis", f"{layer} 根节点必须是 qgis"
            assert root.get("version", "").startswith("3."), \
                f"{layer} 应声明 QGIS 3.x 版本"
            assert "Symbology" in root.get("styleCategories", ""), \
                f"{layer} 必须包含 Symbology 样式类"
            assert (out / STYLES[layer]).stat().st_size > 0
        leftovers = [p.name for p in out.iterdir() if p.suffix == ".tmp"]
        assert leftovers == [], "失败/成功都不能留下 .tmp 残片"

    def test_out_flag_controls_directory(self, tmp_path):
        custom = tmp_path / "elsewhere"
        r = _run(STYLE_SCRIPT, "--out", str(custom))
        assert r.returncode == 0, r.stderr
        for filename in STYLES.values():
            assert (custom / filename).exists()
            assert filename in r.stdout
            assert "字节" in r.stdout


class TestGeometryTypes:
    def test_point_layer_uses_marker_symbols(self, tmp_path):
        roots, _ = _build_styles(tmp_path)
        root = roots["observing_sites"]
        renderer = root.find("renderer-v2")
        assert renderer is not None
        assert renderer.get("type") == "categorizedSymbol"
        assert renderer.get("attr") == "access"
        types = set(_symbol_types(root))
        assert types == {"marker"}, f"点层只该有 marker 符号，实际 {types}"

    def test_line_layer_uses_line_symbol(self, tmp_path):
        roots, _ = _build_styles(tmp_path)
        root = roots["ground_track"]
        renderer = root.find("renderer-v2")
        assert renderer is not None
        assert renderer.get("type") == "singleSymbol"
        assert set(_symbol_types(root)) == {"line"}

    def test_footprint_uses_semi_transparent_fill(self, tmp_path):
        roots, _ = _build_styles(tmp_path)
        root = roots["footprint"]
        renderer = root.find("renderer-v2")
        assert renderer is not None
        assert renderer.get("type") == "RuleRenderer", \
            "混合几何必须按 role 用规则渲染分派"
        types = set(_symbol_types(root))
        assert {"fill", "line", "marker"} <= types

        fill = next(sym for sym in root.iter("symbol")
                    if sym.get("type") == "fill")
        color = next(option.get("value")
                     for option in fill.iter("Option")
                     if option.get("name") == "color")
        red, green, blue, alpha = (int(part) for part in color.split(","))
        assert 0 < alpha < 255, "面层必须是半透明填充"
        assert (red, green, blue) != (0, 0, 0)

    def test_footprint_rules_match_real_roles(self, tmp_path):
        roots, tmp_path = _build_styles(tmp_path)
        root = roots["footprint"]
        rules = root.findall("./renderer-v2/rules/rule")
        assert len(rules) == 3
        expressions = " ".join(rule.get("filter", "") for rule in rules)
        for role in ("footprint", "footprint_boundary", "footprint_subpoint"):
            assert role in expressions


class TestPointLayerFields:
    def test_access_categories_cover_real_values(self, tmp_path):
        roots, _ = _build_styles(tmp_path)
        root = roots["observing_sites"]
        categories = root.findall("./renderer-v2/categories/category")
        values = {category.get("value") for category in categories}
        assert values == {"research", "public", "unknown"}
        labels = " ".join(category.get("label", "")
                          for category in categories)
        assert "科研" in labels and "公众" in labels

    def test_name_zh_is_the_label_field(self, tmp_path):
        roots, _ = _build_styles(tmp_path)
        root = roots["observing_sites"]
        styles = root.findall("./labeling/settings/text-style")
        assert len(styles) == 1
        assert styles[0].get("fieldName") == "name_zh"
        assert styles[0].get("isExpression") == "0", \
            "字段引用不能用表达式，否则改了字段名也不会报错"
        placement = root.find("./labeling/settings/placement")
        assert placement is not None, "标注必须有放置规则"


class TestNoFabricatedFields:
    def test_qml_fields_exist_in_real_geojson(self, tmp_path):
        roots, _ = _build_styles(tmp_path)
        exports = {
            "observing_sites": _export_geojson(tmp_path, "observing-sites"),
            "ground_track": _export_geojson(tmp_path, "ground-track"),
            "footprint": _export_geojson(tmp_path, "footprint"),
        }
        for layer, root in roots.items():
            real = set()
            for feature in exports[layer]["features"]:
                real.update(feature["properties"])
            used = _referenced_fields(root)
            assert used <= real, (
                f"{layer} 引用了 GeoJSON 里不存在的字段："
                f"{sorted(used - real)}（真实字段：{sorted(real)}）")

    def test_no_made_up_elevation_field(self, tmp_path):
        roots, _ = _build_styles(tmp_path)
        for layer, root in roots.items():
            used = _referenced_fields(root)
            assert "elevation" not in used, \
                f"{layer} 引用了不存在的 elevation 字段（真实字段是 alt_m）"
            assert "lat" not in used and "lon" not in used, \
                f"{layer} 坐标是几何，不是属性字段"
        # 结构级兜底：QML 是 XML，按元素属性查，不做子串匹配
        for filename in STYLES.values():
            root_el = ET.parse(tmp_path / "styles" / filename).getroot()
            assert not any(el.get("name") == "elevation" for el in root_el.iter())
            assert not any(el.get("fieldName") == "elevation"
                           for el in root_el.iter())


class TestFailureMode:
    def test_out_pointing_at_file_fails_loudly(self, tmp_path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        r = _run(STYLE_SCRIPT, "--out", str(blocker))
        assert r.returncode == 1
        assert "样式生成失败" in r.stderr
        assert blocker.read_text(encoding="utf-8") == "x", \
            "失败不能破坏已有文件"

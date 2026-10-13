# SPDX-License-Identifier: MIT
"""aigui_messages 测试：agent 侧 A2UI 信封构造 + 白名单跨语言钉扎。

关键用例 test_catalog_matches_cpp_registry：直接解析 C++ 事实源
cpp/src/ui/component_registry.cpp，逐项比对 Python 目录（23 项）——
C++ 增删组件而此处未同步时本测试必须红（漂移防护，与仓库既有
mobile/catalog 三方比对同一文化）。
"""
import os
import re

from mbdsdr_ai.aigui_messages import (
    A2UI_VERSION,
    CATALOG_SIZE,
    COMPONENT_CATALOG,
    arrange_to_envelope,
    catalog_entries,
    is_registered,
    message_create_surface,
    message_delete_surface,
    message_update_components,
    message_update_data_model,
    unknown_component_ids,
    validate_components,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CPP_REGISTRY = os.path.join(REPO_ROOT, "cpp", "src", "ui", "component_registry.cpp")


def _parse_cpp_registry():
    """解析 C++ ComponentInfo 表 -> {id: (category, caps_tuple, min_size)}。"""
    with open(CPP_REGISTRY, "r", encoding="utf-8") as f:
        src = f.read()
    entry_re = re.compile(
        r'QStringLiteral\("([^"]+)"\)\s*,\s*QStringLiteral\("([^"]+)"\)\s*,\s*'
        r'\{([^}]*)\}\s*,\s*(\d+)',
        re.S)
    out = {}
    for m in entry_re.finditer(src):
        cid, cat, caps_blob, min_size = m.group(1), m.group(2), m.group(3), int(m.group(4))
        caps = tuple(re.findall(r'QStringLiteral\("([^"]+)"\)', caps_blob))
        out[cid] = (cat, caps, min_size)
    return out


def test_cpp_registry_file_exists():
    assert os.path.isfile(CPP_REGISTRY), "C++ 事实源丢失？路径: %s" % CPP_REGISTRY


def test_catalog_size_is_23():
    assert CATALOG_SIZE == 23
    assert len(COMPONENT_CATALOG) == 23
    assert len(_parse_cpp_registry()) == 23


def test_catalog_matches_cpp_registry():
    """跨语言钉扎：Python 目录必须与 C++ component_registry.cpp 逐项一致。

    C++ 侧增删/改名组件而此处未同步 -> 本测试红（漂移不许发生）。
    """
    cpp = _parse_cpp_registry()
    py = {cid: (cat, caps, ms) for cid, (cat, caps, ms) in COMPONENT_CATALOG.items()}
    assert py == cpp, "Python 目录与 C++ registry 不一致: %s" % (
        {k: (py.get(k), cpp.get(k)) for k in set(py) | set(cpp) if py.get(k) != cpp.get(k)},)


def test_is_registered_and_whitelist_gate():
    assert is_registered("splitter")
    assert is_registered("tab:VNA")
    assert is_registered("center:时空视图")
    assert not is_registered("tab:不存在的页")
    assert not is_registered("")
    assert not is_registered("root")  # 官方 root 节点不是 MBDSDR 组件 id


def test_catalog_entries_shape():
    entries = catalog_entries()
    assert len(entries) == 23
    one = next(e for e in entries if e["id"] == "right_tabs")
    assert one["category"] == "tab_container"
    assert one["capabilities"] == ["resize", "select", "show", "hide"]
    assert one["min_size"] == 160


# ---------------------------------------------------------------------------
# 信封构造（对齐官方 a2ui.org v1.0 消息结构）
# ---------------------------------------------------------------------------

def test_create_surface_envelope_shape():
    msg = message_create_surface()
    assert msg["version"] == A2UI_VERSION
    assert set(msg.keys()) == {"version", "createSurface"}
    assert msg["createSurface"]["surfaceId"] == "workspace"
    assert "dataModel" not in msg["createSurface"]
    assert "sendDataModel" not in msg["createSurface"]


def test_create_surface_with_components_and_data_model():
    comps = [{"id": "splitter", "component": "container"}]
    msg = message_create_surface(components=comps, data_model={"freq": 435e6},
                                 send_data_model=True)
    assert msg["createSurface"]["sendDataModel"] is True
    assert msg["createSurface"]["components"] == comps
    assert msg["createSurface"]["dataModel"] == {"freq": 435e6}


def test_update_components_envelope_shape():
    msg = message_update_components(components=[])
    assert set(msg.keys()) == {"version", "updateComponents"}
    assert msg["updateComponents"]["surfaceId"] == "workspace"
    assert msg["updateComponents"]["components"] == []


def test_update_data_model_path_semantics():
    root = message_update_data_model({"a": 1})
    assert "path" not in root["updateDataModel"]  # "/" 省略（官方默认根替换）
    sub = message_update_data_model("x", path="/user/name")
    assert sub["updateDataModel"]["path"] == "/user/name"
    assert sub["updateDataModel"]["value"] == "x"
    delete = message_update_data_model(None, path="/user")
    assert delete["updateDataModel"]["value"] is None  # value=null 即删键


def test_delete_surface_envelope():
    msg = message_delete_surface("ws2")
    assert msg == {"version": A2UI_VERSION, "deleteSurface": {"surfaceId": "ws2"}}


# ---------------------------------------------------------------------------
# arrange 参数 -> 官方信封翻译
# ---------------------------------------------------------------------------

def test_arrange_create_surface():
    comps = [{"id": "splitter", "component": "container",
              "props": {"sizes": [120, 800, 280]}}]
    env = arrange_to_envelope("createSurface", comps)
    assert "createSurface" in env
    assert env["createSurface"]["components"] == comps


def test_arrange_update_components_default():
    comps = [{"id": "tab:VNA", "component": "tab"}]
    env = arrange_to_envelope("updateComponents", comps)
    assert "updateComponents" in env
    assert env["updateComponents"]["components"] == comps


def test_arrange_unknown_op_falls_back_to_update():
    env = arrange_to_envelope("totally-unknown-op", [])
    assert "updateComponents" in env  # 与 C++ absent->updateComponents 语义对齐


def test_arrange_delete_surface():
    env = arrange_to_envelope("deleteSurface", None)
    assert env == {"version": A2UI_VERSION, "deleteSurface": {"surfaceId": "workspace"}}


# ---------------------------------------------------------------------------
# agent 侧校验（未知 id 在构造期显形）
# ---------------------------------------------------------------------------

def test_validate_clean_components():
    comps = [
        {"id": "splitter", "component": "container"},
        {"id": "right_tabs", "component": "tab_container",
         "children": ["tab:VNA", "tab:FT8"]},
        {"id": "tab:VNA", "component": "tab"},
        {"id": "tab:FT8", "component": "tab"},
    ]
    r = validate_components(comps)
    assert r["ok"] is True
    assert r["unknown_ids"] == []
    assert r["node_count"] == 4
    assert r["child_refs"] == 2


def test_validate_unknown_id_and_child_ref():
    comps = [
        {"id": "splitter", "component": "container"},
        {"id": "tab:不存在", "component": "tab"},
        {"id": "right_tabs", "component": "tab_container",
         "children": ["tab:VNA", "center:幽灵"]},
    ]
    r = validate_components(comps)
    assert r["ok"] is False
    assert r["unknown_ids"] == ["tab:不存在", "center:幽灵"]


def test_validate_malformed_components_is_error_not_empty_ok():
    r = validate_components("not-a-list")
    assert r["ok"] is False
    assert r["error"]
    assert r["node_count"] == 0
    assert validate_components(None)["ok"] is False


def test_unknown_component_ids_dedupe_and_order():
    comps = [
        {"id": "tab:不存在", "component": "tab"},
        {"id": "tab:不存在", "component": "tab"},
        {"id": "right_tabs", "component": "tab_container",
         "children": ["tab:不存在", "tab:VNA"]},
    ]
    assert unknown_component_ids(comps) == ["tab:不存在"]

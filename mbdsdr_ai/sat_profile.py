# SPDX-License-Identifier: MIT
"""卫星参数库 / 帧布局库（sat_profile v1）——「算法进工具、皮进配置」的地基。

设计原则：目标换星换体制是常态，能力必须可复用；所有卫星具体数字
（频点/符号率/同步字/帧布局/调制参数）一律**配置注入**，代码里不出现
任何一颗卫星的数值。本模块就是那张配置表 + 加载工具：

  * ``data/sat_profiles.json``   频点 / 符号率 / 调制 / alpha / 增益 / 信道编码
  * ``data/frame_layouts.json`` 同步字 / 字段布局 / CRC 方式

诚实纪律：
  * 未知星名/布局名**如实报错**并给出可用名单，不猜、不静默回退默认值；
  * 布局表里逐条标注 honesty（哪些是公开标准、哪些是示例布局）；
  * 表里每个数值带 source（材料名/公开标准名），可追溯到公开来源。
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Sequence

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

__all__ = [
    "list_sat_profiles", "load_sat_profile", "list_frame_layouts",
    "load_frame_layout", "profile_to_rx_chain_args", "register_tool_registry",
]


def _load_json(filename: str) -> Dict[str, Any]:
    path = os.path.join(_DATA_DIR, filename)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _strip_meta(table: Dict[str, Any]) -> List[str]:
    return sorted(k for k in table if not k.startswith("_"))


def list_sat_profiles() -> List[str]:
    """参数库里的全部星名（不含 _ 开头的元字段）。"""
    return _strip_meta(_load_json("sat_profiles.json"))


def load_sat_profile(name: str) -> Dict[str, Any]:
    """按名取卫星参数剖面；未知星名如实报错并列出可用名单。"""
    table = _load_json("sat_profiles.json")
    key = str(name).strip()
    if key not in table or key.startswith("_"):
        raise ValueError(
            f"未知卫星参数剖面: {name}（可用: {', '.join(_strip_meta(table))}；"
            "新增卫星请往 mbdsdr_ai/data/sat_profiles.json 增条目，不改代码）")
    prof = dict(table[key])
    prof["_profile_name"] = key
    return prof


def list_frame_layouts() -> List[str]:
    """帧布局库里的全部布局名（含派生变体，按表内顶层键计）。"""
    return _strip_meta(_load_json("frame_layouts.json"))


def _check_duplicate_fields(fields: Sequence[Dict[str, Any]],
                            layout_name: str) -> None:
    """同一份 fields 列表内字段重名 → 直接报错（不静默后者覆盖前者）。"""
    names = [str(spec.get("name")) for spec in fields]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(
            f"帧布局 {layout_name} 的 fields 内字段重名: {', '.join(dup)}"
            "（同一列表内不允许重名，避免静默覆盖）")


def _merge_fields(parent_fields: Sequence[Dict[str, Any]],
                  child_fields: Sequence[Dict[str, Any]],
                  layout_name: str) -> List[Dict[str, Any]]:
    """父字段为基底合并子字段：同名整体覆盖（子胜、位置不变），新名追加。

    这条优先级是派生机制的唯一定义，改动必须同步改
    ``mbdsdr_ai/tests/test_frame_layout_variants.py`` 的钉扎用例。
    """
    _check_duplicate_fields(child_fields, layout_name)
    merged = [copy.deepcopy(spec) for spec in parent_fields]
    index = {str(spec.get("name")): i for i, spec in enumerate(merged)}
    for spec in child_fields:
        name = str(spec.get("name"))
        if name in index:  # 冲突：子胜，保留父布局里的位置
            merged[index[name]] = copy.deepcopy(spec)
        else:              # 子布局独有：追加到末尾
            index[name] = len(merged)
            merged.append(copy.deepcopy(spec))
    return merged


def _require_own_source(entry: Dict[str, Any], layout_name: str) -> None:
    """派生布局必须自带公开出处（URL + 抓取时间），无出处不入库。"""
    src = entry.get("source")
    if not isinstance(src, dict):
        raise ValueError(
            f"派生帧布局 {layout_name} 缺 source（须给公开文档/规范 URL + "
            "retrieved_at 抓取时间；无出处不入库）")
    missing = [k for k in ("url", "retrieved_at")
               if not str(src.get(k, "")).strip()]
    if missing:
        raise ValueError(
            f"派生帧布局 {layout_name} 的 source 缺 {'/'.join(missing)}"
            "（须给公开文档/规范 URL + 抓取时间；无出处不入库）")


def _resolve_layout(table: Dict[str, Any], name: str,
                    _stack: Sequence[str] = ()) -> Dict[str, Any]:
    """按名解析布局；``derived_from`` 声明的派生布局在此展开为完整字段表。

    派生优先级（写死）：
      1. 递归解析父布局；``derived_from`` 成环 → ValueError；
      2. fields：父字段按原序为基底，子字段同名整体覆盖（子胜、位置不变），
         子字段新名追加到末尾；同一 fields 列表内重名 → ValueError；
      3. 子条目的 sync/crc/honesty/source 覆盖父条目；fields 只按名合并；
      4. 派生布局必须自带 source.url + source.retrieved_at，缺失拒载。
    无 ``derived_from`` 的条目原样返回（与 v1 行为逐字一致，只多
    ``_layout_name``），旧 JSON 不受影响。
    """
    table_names = _strip_meta(table)
    key = str(name).strip()
    if key not in table or key.startswith("_"):
        raise ValueError(
            f"未知帧布局: {name}（可用: {', '.join(table_names)}；"
            "新布局请往 mbdsdr_ai/data/frame_layouts.json 增条目）")
    if key in _stack:
        raise ValueError(
            "帧布局派生成环: " + " -> ".join(list(_stack) + [key]))
    entry = table[key]
    if not isinstance(entry, dict):
        raise ValueError(f"帧布局 {key} 条目必须是对象")
    _check_duplicate_fields(entry.get("fields", []) or [], key)
    parent_name = str(entry.get("derived_from") or "").strip()
    if not parent_name:
        layout = copy.deepcopy(entry)
        layout["_layout_name"] = key
        return layout
    _require_own_source(entry, key)
    parent = _resolve_layout(table, parent_name, tuple(_stack) + (key,))
    layout = copy.deepcopy(parent)
    layout["fields"] = _merge_fields(parent.get("fields", []),
                                     entry.get("fields", []) or [], key)
    for k, v in entry.items():
        if k in ("fields", "derived_from"):
            continue
        layout[k] = copy.deepcopy(v)
    layout["_layout_name"] = key
    layout["_derived_from"] = parent_name
    return layout


def load_frame_layout(name: str) -> Dict[str, Any]:
    """按名取帧布局；未知布局名如实报错并列出可用名单。

    布局名可以是派生变体（表内带 ``derived_from`` 的顶层条目），
    解析在 :func:`_resolve_layout` 内完成，调用方无需传额外参数。
    """
    return _resolve_layout(_load_json("frame_layouts.json"), name)


def profile_to_rx_chain_args(profile: Dict[str, Any]) -> Dict[str, Any]:
    """把卫星剖面翻译成 CCSDS 接收链工具的具名参数（ccsds_tools 口径）。

    剖面里没有的键不编造：返回值只包含剖面明确给出的项，缺的由链路工具
    自己按「不猜默认」纪律报错。
    """
    dl = profile.get("downlink") or {}
    coding = dl.get("coding") or {}
    args: Dict[str, Any] = {}
    if "modulation" in dl:
        args["scheme"] = {"bpsk": "bpsk", "dbpsk": "dbpsk"}.get(
            str(dl["modulation"]), str(dl["modulation"]))
    if "rrc_alpha" in dl:
        args["alpha"] = float(dl["rrc_alpha"])
    if "sps_at_48k" in dl:
        args["sps"] = int(dl["sps_at_48k"])
    if "asm_word_hex" in coding:
        args["asm_word"] = int(str(coding["asm_word_hex"]), 16)
    if "viterbi" in coding:
        args["use_viterbi"] = True
        vit = coding["viterbi"] or {}
        if "g1_oct" in vit:
            args["g1"] = int(str(vit["g1_oct"]), 8)
        if "g2_oct" in vit:
            args["g2"] = int(str(vit["g2_oct"]), 8)
    if "scrambler" in coding:
        args["use_descramble"] = bool(coding["scrambler"])
    return args


# ═══════════════════════════════════════════════════════════
# 工具层
# ═══════════════════════════════════════════════════════════
def register_tool_registry(registry) -> None:
    """注册参数库积木：查名单 / 取剖面 / 取帧布局（全只读，纯数据）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _list_profiles(args):
        names = list_sat_profiles()
        return ToolResult(
            success=True,
            content=f"卫星参数库共 {len(names)} 个剖面: {', '.join(names)}",
            data={"profiles": names})

    def _load_profile(args):
        try:
            prof = load_sat_profile(str(args.get("name", "")))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, content=f"取卫星参数失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=_json.dumps(prof, ensure_ascii=False, indent=2),
            data=prof)

    def _list_layouts(args):
        names = list_frame_layouts()
        return ToolResult(
            success=True,
            content=f"帧布局库共 {len(names)} 个布局: {', '.join(names)}",
            data={"layouts": names})

    def _load_layout(args):
        try:
            layout = load_frame_layout(str(args.get("name", "")))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, content=f"取帧布局失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=_json.dumps(layout, ensure_ascii=False, indent=2),
            data=layout)

    registry.register(
        name="sat_profile_list",
        description=(
            "列出卫星参数库全部星名（频点/符号率/调制/编码参数的配置表）。"
            "换星换体制只换配置表，代码零改动——本工具是参数注入面的发现入口。"
            "副作用：纯只读。能力边界：只读库内容，不做任何计算。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_list_profiles, category="sat_profile",
    )
    registry.register(
        name="sat_profile_load",
        description=(
            "按星名取卫星参数剖面（频点/符号率/调制/RRC alpha/采样链/信道编码），"
            "输出可直接喂给接收链工具。前置条件：星名须在库内（先 sat_profile_list）。"
            "失败恢复：未知星名如实报错并列出可用名单（不猜不静默回退）。"
            "副作用：纯只读。"
        ),
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "星名（如 astu_1）"},
        }, "required": ["name"]},
        handler=_load_profile, category="sat_profile",
    )
    registry.register(
        name="frame_layout_list",
        description=(
            "列出帧布局库全部布局名（同步字/字段布局/CRC 方式）。"
            "帧内字段（偏移/长度/类型/标度/单位）全部由布局表注入。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_list_layouts, category="sat_profile",
    )
    registry.register(
        name="frame_layout_load",
        description=(
            "按名取帧布局（同步字/字段布局/CRC），输出喂给帧同步/字段抽取/验证链工具。"
            "每条布局带 honesty 标注（公开标准 vs 示例布局）。"
            "失败恢复：未知布局名如实报错并列出可用名单。副作用：纯只读。"
        ),
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "布局名（如 astu_1_tm_example）"},
        }, "required": ["name"]},
        handler=_load_layout, category="sat_profile",
    )

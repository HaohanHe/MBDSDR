# SPDX-License-Identifier: MIT
"""知名卫星下行链路目录（sat_links v1）——「点星即听」的数据底座。

本项目把卫星当作当代星官来读：星空页签里点一颗星，要知道两件事——
它现在在哪（sat_passes 轨道）、该把收音机拧到哪（本表）。本表只收
**公开常识级下行频点**（收听侧），价值与纪律：

  * 数据全在 ``data/sat_links.json``，源码零频点字面量（与 sat_profile
    同一套「算法进工具、皮进配置」红线，grep 级钉扎在测试里）；
  * 每条带 source 可追溯，每条带一句话 culture 星性注记；
  * 只含 downlink：任何上行（发射）一律走 tc_uplink 的外部确认 token
    门，本表永不收录上行频点；
  * 反向查询 identify() 回答「频谱上这条竖线是谁」——这是把瀑布图和
    星空图缝起来的那根针。

诚实纪律：未知星名如实报错并给出可用名单；频率命中失败如实返回空
列表并说明容差，不猜最近的。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import sat_links_fetch

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

__all__ = [
    "list_link_sats", "load_sat_link", "identify_by_freq",
    "find_tuning_for", "register_tool_registry",
    "machine_targets", "machine_status", "machine_status_text",
    "machine_entry_text", "machine_only_links", "refresh_machine_links",
    "start_machine_refresh",
]


def _load_table() -> Dict[str, Any]:
    path = os.path.join(_DATA_DIR, "sat_links.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _names(table: Dict[str, Any]) -> List[str]:
    return sorted(k for k in table if not k.startswith("_"))


def list_link_sats() -> List[str]:
    """链路目录里的全部星名（不含 _ 开头的元字段）。"""
    return _names(_load_table())


def load_sat_link(name: str) -> Dict[str, Any]:
    """按名取卫星链路条目；未知星名如实报错并列出可用名单。"""
    table = _load_table()
    key = str(name).strip()
    if key not in table or key.startswith("_"):
        raise ValueError(
            f"未知卫星链路: {name}（可用: {', '.join(_names(table))}；"
            "新增请往 mbdsdr_ai/data/sat_links.json 增条目）")
    entry = dict(table[key])
    entry["_link_name"] = key
    # 机器库只做增量（low/high/status/service + _machine 说明），
    # freq_hz/mode/baud/note 永远保留人工值；无缓存时 _machine.state=absent。
    return sat_links_fetch.merge_entry(entry)


def identify_by_freq(freq_hz: float, tolerance_hz: float = 3000.0) -> List[Dict[str, Any]]:
    """反向查询：哪个卫星的下行频点落在 freq_hz ± tolerance_hz 内。

    命中返回条目列表（带 _link_name 与命中的 link）；无命中返回空列表
    （诚实空态，调用方自己决定怎么呈现）。tolerance 必须为正，否则报错。

    同一频点可能被多颗星共用（本表实测：137.100 MHz 同属 NOAA-19 APT 与
    METEOR-M N2 LRPT），命中按列表**并列返回**、不强行裁决——**只取第一条
    的调用方会漏掉其余命中**，展示/调谐建议必须遍历整个列表。

    机器库独有、人工表未收录的发射机**不自动注入本函数**（避免把 ISS 的
    50 条机器记录变成频谱反查噪音）；需要时用 :func:`machine_only_links` 取。
    """
    freq = float(freq_hz)
    tol = float(tolerance_hz)
    if not (tol > 0.0):
        raise ValueError(f"容差必须为正: {tolerance_hz}")
    hits: List[Dict[str, Any]] = []
    for name in list_link_sats():
        entry = load_sat_link(name)
        for link in entry.get("links", []):
            if _link_contains(link, freq, tol):
                merged = dict(entry)
                merged["_link_name"] = name
                merged["_matched_link"] = dict(link)
                merged["_matched_source"] = (
                    "satnogs" if link.get("low_hz") is not None else "manual")
                hits.append(merged)
    return hits


def _link_contains(link: Dict[str, Any], freq: float, tol: float) -> bool:
    """带宽优先：机器给了 low/high 就按区间判，否则退回人工频点 ±tol。"""
    low = link.get("low_hz")
    high = link.get("high_hz")
    if low is not None:
        low = float(low)
        if high is not None:
            return (low - tol) <= freq <= (float(high) + tol)
        return abs(low - freq) <= tol
    return abs(float(link["freq_hz"]) - freq) <= tol


def _norm_name(s: str) -> str:
    """名称归一：大写 + 空白/连字符折叠，对齐各种 TLE 拼法。"""
    return "".join(ch for ch in str(s).upper()
                   if ch not in " -_")


def find_tuning_for(sat_name: str) -> Optional[Dict[str, Any]]:
    """按 TLE 名找收听建议：返回 (条目, 第一条 downlink)。

    匹配面 = norad_name + tle_aliases，归一化后对齐（"NOAA-19" /
    "NOAA 19"、"METEOR M2" / "METEOR-M 2" 都能命中）。找不到返回 None
    （诚实空态，不猜）。
    """
    needle = _norm_name(sat_name)
    if not needle:
        return None
    for name in list_link_sats():
        entry = load_sat_link(name)
        candidates = [entry.get("norad_name", "")]
        candidates.extend(entry.get("tle_aliases", []))
        if any(_norm_name(c) == needle for c in candidates if c):
            links = [l for l in entry.get("links", [])
                     if l.get("direction") == "downlink"]
            if links:
                return {"entry": entry, "downlink": links[0]}
    return None


# ------------------------------------------------- 机器数据源（W92，SatNOGS）
def machine_targets() -> List[Dict[str, Any]]:
    """人工目录里声明了 ``norad_cat_id`` 的星（拉库目标，去重保序）。"""
    out: List[Dict[str, Any]] = []
    for name in list_link_sats():
        entry = _load_table().get(name) or {}
        norad = entry.get("norad_cat_id")
        if norad is None:
            continue
        out.append({"norad_cat_id": int(norad), "key": name,
                    "norad_name": entry.get("norad_name")})
    return out


def refresh_machine_links(now: Optional[float] = None,
                          path: Optional[str] = None,
                          opener=None) -> Dict[str, Any]:
    """同步拉库（测试/工具用）；界面请走 :func:`start_machine_refresh`。"""
    return sat_links_fetch.refresh(machine_targets(), path=path,
                                   opener=opener, now=now)


def start_machine_refresh(path: Optional[str] = None) -> None:
    """后台拉库，立即返回；失败只记日志，状态由 :func:`machine_status_text` 读。"""
    sat_links_fetch.start_background_refresh(machine_targets(), path=path)


def machine_status(now: Optional[float] = None,
                   path: Optional[str] = None) -> Dict[str, Any]:
    """机器缓存状态（只读，零网络）：state/age_s/stale/text 等。"""
    return sat_links_fetch.catalog_status(path=path, now=now)


def machine_status_text(now: Optional[float] = None,
                        path: Optional[str] = None) -> str:
    """界面唯一一处状态文案（失败也只在这里说一句，不弹窗）。"""
    return machine_status(now=now, path=path)["text"]


def machine_only_links(name: str) -> List[Dict[str, Any]]:
    """机器库有、人工表未收录的发射机（只读；不写回目录、不进 identify）。"""
    return sat_links_fetch.machine_only_links(load_sat_link(name))


def machine_entry_text(entry: Dict[str, Any]) -> str:
    """单颗星的数据龄文案（信息卡用）；无机器数据时如实说未取到。"""
    machine = entry.get("_machine") or {}
    state = str(machine.get("state") or "absent")
    if state == "ok":
        age = sat_links_fetch.age_text(machine.get("age_s"))
        return f"SatNOGS（{age}前）"
    if state == "empty":
        return "SatNOGS：该星无发射机记录"
    detail = str(machine.get("detail") or "无机器缓存")
    return f"未取到（{detail}）"


def register_tool_registry(registry) -> None:
    """注册链路目录工具：查名单 / 取条目 / 频率反查（全只读，纯数据）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _list(args):
        names = list_link_sats()
        return ToolResult(
            success=True,
            content=f"链路目录共 {len(names)} 颗星: {', '.join(names)}",
            data={"sats": names})

    def _get(args):
        try:
            entry = load_sat_link(str(args.get("name", "")))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, content=f"取卫星链路失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=_json.dumps(entry, ensure_ascii=False, indent=2),
            data=entry)

    def _identify(args):
        try:
            freq = float(args.get("freq_hz", 0.0))
            tol = float(args.get("tolerance_hz", 3000.0))
            hits = identify_by_freq(freq, tol)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, content=f"频率反查失败: {clean_exc(e)}")
        if not hits:
            return ToolResult(
                success=True,
                content=(f"{freq/1e6:.4f} MHz ±{tol/1e3:.0f} kHz 内无已知卫星"
                         "下行频点（可调大 tolerance_hz 再试）"),
                data={"hits": []})
        names = [h["_link_name"] for h in hits]
        return ToolResult(
            success=True,
            content=(f"{freq/1e6:.4f} MHz ±{tol/1e3:.0f} kHz 命中: "
                     f"{', '.join(names)}"),
            data={"hits": hits})

    def _tuning(args):
        sat = str(args.get("norad_name", ""))
        found = find_tuning_for(sat)
        if found is None:
            return ToolResult(
                success=True,
                content=(f"链路目录里没有 {sat} 的下行频点"
                         "（如实空态：不猜频率）"),
                data={"found": False})
        entry, link = found["entry"], found["downlink"]
        return ToolResult(
            success=True,
            content=(f"{entry['display_name']}：拧到 "
                     f"{link['freq_hz']/1e6:.4f} MHz（{link['mode']}）"
                     f"——{entry.get('culture', '')}"),
            data={"found": True, "entry": entry, "downlink": link})

    registry.register(
        name="sat_link_list",
        description=(
            "列出链路目录全部星名（知名卫星下行频点表：点星即听的数据底座）。"
            "副作用：纯只读。能力边界：只读目录，不做计算。"),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_list, category="satellite")

    registry.register(
        name="sat_link_get",
        description=(
            "按名取卫星链路条目：下行频点/体制/一句话星性注记/来源。"
            "未知星名如实报错并列名单。副作用：纯只读。"),
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "目录键名"}},
            "required": ["name"]},
        handler=_get, category="satellite")

    registry.register(
        name="sat_link_identify",
        description=(
            "频率反查：给定频谱上观察到的频率，返回 ±容差内所有已知卫星"
            "下行频点——回答「这条竖线是谁」。无命中如实返回空列表。"
            "副作用：纯只读。"),
        parameters={
            "type": "object",
            "properties": {
                "freq_hz": {"type": "number", "description": "观察到的频率 Hz"},
                "tolerance_hz": {"type": "number",
                                 "description": "容差 Hz（默认 3000）"},
            },
            "required": ["freq_hz"]},
        handler=_identify, category="satellite")

    registry.register(
        name="sat_link_tuning",
        description=(
            "按 TLE 星名取收听建议：norad_name 对齐轨道工具的卫星名，"
            "返回第一条下行频点与星性注记。目录里没有就如实说没有，不猜。"
            "副作用：纯只读。"),
        parameters={
            "type": "object",
            "properties": {"norad_name": {"type": "string",
                                          "description": "TLE 里的卫星名"}},
            "required": ["norad_name"]},
        handler=_tuning, category="satellite")

# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端「能力目录」数据层
================================

帮助面板（desktop/help_panel.py）的「能做什么」清单一律从本模块取，
本模块**不写任何手抄的能力清单**：

  * 面板        ← desktop/panel_registry.py（id/名称/停靠区/一句话说明）
  * 解调模式    ← desktop/mode_registry.py（显示名/说明/带宽）
  * 数字解码    ← mbdsdr_ai/decode_router.py 的 DIGITAL_MODE_CAPABILITIES
  * 长尾解码器  ← mbdsdr_ai/decoder_registry.py（含「未接主调度」的诚实标注）
  * AI 工具域   ← mbdsdr_ai/core/mcp_server.py 的域摘要 + 工具→域映射
  * 快捷操作    ← desktop/shortcuts.py（键位与菜单标签，标题直接用 label）
  * 静态能力    ← 直接用 code 符号校验（PTT 按钮、天空搜索框）
  * 设备硬件能力 ← docs/backend-capability-matrix.md（生成物；逐后端类调用
    `supports_*` 得出，这里只给指针，不抄一份判据）

新增/删除任何一侧的能力时，目录自动跟着变；``tests/test_discoverability_help.py``
还会逐条断言 source 在注册表里真实存在、不可用项必须写原因，防止目录漂移。

红线：这里只描述「已存在且能触发」的能力。触发不了的一律 status="unavailable"
并在 note 里写明原因——不许把未接线的入口写成可用。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from panel_registry import registry as _panels  # noqa: E402
import mode_registry as _modes  # noqa: E402
from shortcuts import BY_ID as _SHORTCUTS  # noqa: E402
from mbdsdr_ai import decoder_registry as _decoders  # noqa: E402
from mbdsdr_ai.decode_router import (  # noqa: E402
    DIGITAL_MODE_CAPABILITIES, EVIDENCE_REAL, EVIDENCE_PARTIAL,
)
from mbdsdr_ai.core import mcp_server as _mcp  # noqa: E402
from mbdsdr_ai.core.mcp_server import (  # noqa: E402
    ALL_DOMAIN_NAMES, ALL_DOMAIN_SUMMARIES,
)

# 目录分类（顺序即界面上的分组顺序）
CATEGORIES: Tuple[str, ...] = (
    "接收", "解码", "卫星", "天空", "AI", "录制", "发射", "设置",
)

#: 面板 id → 目录分类（只做归类；面板本身的存在性仍由注册表决定）
PANEL_CATEGORY: Dict[str, str] = {
    "spectrum": "接收", "control": "接收", "module": "接收",
    "weather": "天空", "rf_sky": "天空", "adsb": "天空",
    "sat_track": "卫星", "new_spacetime": "卫星",
    "aprs": "解码", "sat_image": "卫星",
    "ai": "AI", "status": "接收",
}

#: MCP 工具域 → 目录分类（只做归类，工具数量/名字全部实时统计）
DOMAIN_CATEGORY: Dict[str, str] = {
    "SDR 调谐/VFO": "接收",
    "亚音数字解码": "解码",
    "解码器长尾": "解码",
    "VNA 测量": "接收",
    "VNA 分析": "接收",
    "VNA 校准": "接收",
    "导出演制": "录制",
    "书签扫描卫星": "卫星",
    "卫星链路": "卫星",
    "系统设备": "设置",
    "记忆编排": "AI",
    "人机问答": "AI",
    "外部工具": "AI",
    "工作台编排": "AI",
}

#: 快捷操作 → 目录条目（标题取 shortcut.label，键位取 shortcut.key，均不手抄）
SHORTCUT_CAPABILITIES: Tuple[Tuple[str, str, str], ...] = (
    ("file.connect", "接收", "枚举并连接真实 SDR 设备；枚举不到就如实空态，不造假设备"),
    ("tools.sweep", "接收", "在 FM 广播段自动扫频，找出当前有信号的频点"),
    ("tools.record", "录制", "把当前 IQ 落盘为文件，供离线解码或回放"),
    ("vfo.replay", "录制", "选择已录制的 .iq 文件离线回放，回放时保持原频率"),
    ("tools.ntrip", "设置", "配置 NTRIP 差分数据源（RTK 定位用）"),
)

#: 无注册表可查、但代码里确有实现的能力：source 由测试用 AST/源码存在性校验
STATIC_CAPABILITIES: Tuple[Tuple[str, str, str, str, str, str, str], ...] = (
    # category, title, description, trigger, status, note, source
    ("天空", "搜索天体",
     "按名称、别名、NORAD 编号或梅西耶编号搜索恒星/行星/卫星/深空天体，"
     "选中即居中并弹出信息卡",
     "星象仪模式顶部搜索框（输入 ISS / Vega / M42）",
     "available", "", "module:desktop/rf_sky_view.py:_build_search_widgets"),
    ("发射", "发射 / PTT",
     "按下开始发射，再次按下停止；只有具备发射能力的设备才会亮起",
     "控制面板「发射」按钮（每次发射都要人工确认）",
     "conditional",
     "需硬件支持发射；AI 工具面确实带发射工具（radio_ptt / radio_send_cw），"
     "每次执行都要外部确认通道签发的一次性 confirm_token（tx_guard 硬门），"
     "模型无法自备授权",
     "module:desktop/main_window.py:_tx_block_reason"),
    ("接收", "本设备支持哪些硬件能力",
     "按后端类逐项列出 AGC / 增益 / PPM / Offset / 采样率区间 / 增益区间："
     "哪些设备真支持、哪些不支持，以及区间多大、是否要连接后才确定",
     "查看 docs/backend-capability-matrix.md"
     "（数值由脚本直接调用各后端的 supports_* 生成，文档不另抄判据；"
     "发版前由生成物对账自动校验）",
     "available",
     "表是逐类静态对照；实例级差异（例如 RTL-SDR 的 Offset 需连接后按驱动"
     "判定）已在表内逐项标注",
     "doc:docs/backend-capability-matrix.md"),
)

#: 证据等级 → 人话标注（数字解码状态说明）
_EVIDENCE_NOTE = {
    EVIDENCE_REAL: "真录/真值对拍已验证",
    EVIDENCE_PARTIAL: "合成链自洽，尚无真实空中样本",
}
_DEFAULT_EVIDENCE_NOTE = "无真样本，仅按协议数学性质钉扎"


@dataclass(frozen=True)
class Capability:
    """能力目录的一行。status: available / conditional / unavailable。"""

    category: str
    title: str
    description: str
    trigger: str
    source: str
    status: str = "available"
    note: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available"


def _panel_area_text(area: str) -> str:
    return {
        "center": "中央页签", "left": "左栏", "right": "右侧面板坞",
        "bottom": "底部", "float": "悬浮窗",
    }.get(area, area)


def _panel_capabilities() -> List[Capability]:
    out: List[Capability] = []
    for spec in _panels().list():
        status, note = "available", ""
        if spec.id == "adsb":
            # 面板入口存在 ≠ 当前设备收得到：ADS-B 在 1090 MHz，FC0012 类
            # tuner 实测上限 948.5 MHz，需覆盖 1090 的前端才可用。
            status = "conditional"
            note = ("需覆盖 1090 MHz 的前端；本机 FC0012 类 tuner 实测上限约 "
                    "948.5 MHz，在该设备上收不到（面板会如实显示无法接收）")
        out.append(Capability(
            category=PANEL_CATEGORY.get(spec.id, "接收"),
            title=spec.name,
            description=spec.summary or f"{spec.name}面板",
            trigger=f"「面板」菜单 → {spec.name}（勾选显示/隐藏，位置：{_panel_area_text(spec.area)}）",
            source=f"panel_registry:{spec.id}",
            status=status,
            note=note,
        ))
    return out


def _mode_capabilities() -> List[Capability]:
    out: List[Capability] = []
    for m in _modes.list_modes():
        out.append(Capability(
            category="接收",
            title=m.display_name,
            description=m.description or f"解调模式 {m.mode}",
            trigger=(f"顶栏「模式」下拉选「{m.display_name}」；"
                     f"或 AI 工具 set_demod(mode=\"{m.mode}\")"),
            source=f"mode_registry:{m.mode}",
        ))
    return out


def _digital_capabilities() -> List[Capability]:
    out: List[Capability] = []
    for key in sorted(DIGITAL_MODE_CAPABILITIES):
        cap = DIGITAL_MODE_CAPABILITIES[key]
        note = _EVIDENCE_NOTE.get(cap.evidence, _DEFAULT_EVIDENCE_NOTE)
        if key == "adsb":
            # 对拍数据来自 dump1090 官方 OTA 实录：只证明解码/重采样路径，
            # 不构成「本机前端能覆盖 1090 MHz」的能力证据。
            note += ("；解码对拍用 dump1090 官方 OTA 实录，只证明重采样/解码"
                     "路径等价，不代表本机前端能覆盖 1090 MHz")
        status, trigger = "available", ""
        if cap.dependency:
            trigger = (f"命令行：python -m mbdsdr_ai --decode-file <文件> "
                       f"--mode {cap.mode}")
        else:
            status = "unavailable"
            trigger = "无（通用解码入口未接线，桌面/AI 界面暂无入口）"
            note += "；通用入口未接线，直接走 decode_digital_mode 会返回 not_implemented"
        out.append(Capability(
            category="解码",
            title=cap.mode.upper(),
            description=(f"输入 {'/'.join(cap.input_formats)}，输出 {cap.output_kind}；"
                         f"采样条件：{cap.sample_rate_rule}"),
            trigger=trigger,
            source=f"decode_router:{key}",
            status=status,
            note=note,
        ))
    return out


def _decoder_registry_capabilities() -> List[Capability]:
    """长尾解码器注册表——POCSAG/ACARS 已由 decode_router 接主调度，VOR 无路由。"""
    out: List[Capability] = []
    for d in _decoders.list_decoders():
        # POCSAG/ACARS 另有 CLI 文件解码入口；VOR 没有通用路由。
        if d.mode.upper() in ("POCSAG", "ACARS"):
            trigger = (f"命令行：python -m mbdsdr_ai --decode-file <文件> "
                       f"--mode {d.mode.lower()}（桌面界面/AI 工具面未直接暴露）")
            status = "available"
            note = ("decode_router 已接 POCSAG / ACARS 专用路由；"
                    "桌面/AI 工具面未直接暴露该入口，走 CLI --decode-file")
        else:
            trigger = "无（该解码器未接通用解码路由，桌面/AI 界面无入口）"
            status = "unavailable"
            note = "解码器已实现并登记，但通用路由与界面都未接线"
        out.append(Capability(
            category="解码",
            title=d.mode,
            description=d.description,
            trigger=trigger,
            source=f"decoder_registry:{d.mode}",
            status=status,
            note=note,
        ))
    return out


def _domain_capabilities() -> List[Capability]:
    # 域内工具名一律从工具表实时取（不读手抄映射直接当事实），
    # 这样 AI 工具面新增工具时目录跟着变；未登记域的工具单独列成诚实空态。
    tools_by_domain: Dict[str, List[str]] = _mcp.tool_domain_view()
    out: List[Capability] = []
    for dom in ALL_DOMAIN_NAMES:
        names = sorted(tools_by_domain.get(dom, []))
        summary = ALL_DOMAIN_SUMMARIES.get(dom, "")
        preview = "、".join(names[:6]) + ("…" if len(names) > 6 else "")
        if names:
            trigger = (f"AI 助手 / MCP 客户端：按域「{dom}」调用"
                       f"（{len(names)} 个工具：{preview}）")
            status = "available"
            note = ""
        else:
            trigger = "无（本 Python 工具面该域为空，没有可调用工具）"
            status = "unavailable"
            note = "域是空的（诚实空态）；不列任何不存在的工具"
        out.append(Capability(
            category=DOMAIN_CATEGORY.get(dom, "AI"),
            title=f"AI 工具域：{dom}",
            description=summary or "（无域说明）",
            trigger=trigger,
            source=f"mcp:{dom}",
            status=status,
            note=note,
        ))
    stray = _mcp.unmapped_tool_names()
    if stray:
        out.append(Capability(
            category="AI",
            title="AI 工具域：未归类（注册表有工具未分域）",
            description="这些工具真实存在于工具注册表，但没有域归属，"
                        "MCP 客户端按域取工具时看不到它们",
            trigger="无（需在 mcp_server._TOOL_DOMAIN 补登记域）",
            source="mcp:unmapped",
            status="unavailable",
            note="、".join(stray),
        ))
    return out


def _shortcut_capabilities() -> List[Capability]:
    out: List[Capability] = []
    for sid, category, description in SHORTCUT_CAPABILITIES:
        s = _SHORTCUTS[sid]
        key = s.key or "无快捷键"
        menu = {"file": "文件", "tools": "工具", "vfo": "顶栏"}.get(
            sid.split(".")[0], "菜单")
        out.append(Capability(
            category=category,
            title=s.label,
            description=description,
            trigger=f"{menu}菜单 → {s.label}（{key}）",
            source=f"shortcut:{sid}",
        ))
    return out


def _static_capabilities() -> List[Capability]:
    return [
        Capability(category=c, title=t, description=d, trigger=tr,
                   source=s, status=st, note=n)
        for (c, t, d, tr, st, n, s) in STATIC_CAPABILITIES
    ]


def build_catalog() -> List[Capability]:
    """返回全部能力条目（按分类顺序，分类内按标题排序）。"""
    items = (_panel_capabilities() + _mode_capabilities()
             + _digital_capabilities() + _decoder_registry_capabilities()
             + _domain_capabilities() + _shortcut_capabilities()
             + _static_capabilities())
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return sorted(items, key=lambda x: (order.get(x.category, len(order)),
                                        x.title))


def search_capabilities(items: Sequence[Capability], query: str,
                        category: Optional[str] = None) -> List[Capability]:
    """按关键词/分类过滤。空关键词返回该分类全部条目。"""
    q = (query or "").strip().lower()
    out = []
    for it in items:
        if category and category != "全部" and it.category != category:
            continue
        if q and q not in (it.title + " " + it.description + " "
                           + it.trigger + " " + it.note
                           + " " + it.category).lower():
            continue
        out.append(it)
    return out


def catalog_stats(items: Optional[Sequence[Capability]] = None) -> Dict[str, int]:
    """条目统计：总数 / 可用 / 有条件 / 不可用 / 分类数。"""
    items = list(items if items is not None else build_catalog())
    return {
        "total": len(items),
        "available": sum(1 for x in items if x.status == "available"),
        "conditional": sum(1 for x in items if x.status == "conditional"),
        "unavailable": sum(1 for x in items if x.status == "unavailable"),
        "categories": len({x.category for x in items}),
    }


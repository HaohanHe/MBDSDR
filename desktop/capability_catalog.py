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
                  （MCP 面按域汇总）
  * AI 工具全量 ← ToolRegistry 实取（``tool_registry_capabilities``，一人一行，
                  域/读写/可用性逐项标注；这是模型真正可见的全部注册工具）
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
    DIGITAL_MODE_CAPABILITIES, EVIDENCE_NO_SAMPLE, EVIDENCE_OTA_GOLDEN,
    EVIDENCE_SYNTHETIC_ONLY,
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
    "scanner": "接收", "modulation": "解码", "bookmark": "设置",
    "anr": "接收", "constellation": "接收", "device": "设置",
    "gain": "接收", "adsb_list": "天空",
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
    ("天空", "今夜可看性",
     "按云量、地磁活动、采样时刻月光与流星雨活动期粗判今晚能不能看；"
     "缺测就说缺测，不按晴天假设",
     "AI 助手：sky_tonight（工具，需联网取云量/空间天气）",
     "available",
     "MBDSDR_OFFLINE 或无快照时明确「未联网，不猜」；极光按 Kp 与地磁纬度"
     "经验门槛粗判，低纬外推避免 Kp=9 假阳性；流星雨只给活动期与峰值 ZHR，"
     "不折算「今晚每小时几颗」",
     "module:mbdsdr_ai/astronomy/tonight.py:assess_tonight"),
    ("天空", "附近台址与公众观星点",
     "内置 4 个科研台址 + 3 个公众点，全部带官方坐标与来源 URL；"
     "按站址算球面距离",
     "AI 助手：sky_observing_sites（工具）；卫星地面轨迹地图上也画出这批点",
     "available",
     "research 是科研台址、不对外开放；public 也需预约/开放日，不是旅游推荐；"
     "官方页没给海拔的记 null，不用 0 顶替",
     "module:mbdsdr_ai/astronomy/observing_sites.py:nearest_sites"),
    ("AI", "本地模型端点体检",
     "只打 GET /v1/models，区分未启动 / 无模型 / 模型名不对 / 认证失败 / 离线",
     "AI 面板 → 配置 → 检测端点",
     "available",
     "不加载模型、不生成文本、不改配置；离线模式一次网络都不打。"
     "空 API Key + 本机/内网端点（Ollama 等）可直接使用，不用为了过校验乱填 Key",
     "module:desktop/ai_panel.py:_probe_local_endpoint"),
    ("AI", "IQ 样本清单体检与切分方案",
     "校验 JSONL 清单必需字段并查结构硬伤（单类别、同文件跨标签、重复 ID），"
     "再按 iq_path 分组给出可复现的训练/验证切分名单",
     "AI 助手：iq_manifest_audit / iq_split_plan（只读工具，读磁盘上的清单）",
     "available",
     "只是数据契约层：不读样本内容、不写文件、不训练；本机无 HF 运行时与"
     "真实多类样本，structural_ready 不代表能训出可用模型",
     "module:mbdsdr_ai/iq_tools.py:register_iq_tools"),
)

#: 证据等级 → 人话标注（数字解码状态说明）
_EVIDENCE_NOTE = {
    EVIDENCE_OTA_GOLDEN: "真实空口金标对拍已验证",
    EVIDENCE_SYNTHETIC_ONLY: "合成链自洽，尚无真实空中样本",
}
_DEFAULT_EVIDENCE_NOTE = "无样本，仅按协议数学性质钉扎（不宣称已支持）"


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
    # 数字解码条目的证据级别（ota_golden/synthetic_only/no_sample）；
    # 非解码条目留空。no_sample 的 status 不得为 available。
    evidence: str = ""
    # registry 工具的域归属（MCP 中文域名优先，否则用 registry 分类；
    # 两者都没有时 = 「未分组」）。非工具条目留空。
    domain: str = ""
    # registry 工具的读写标记：read / write；非工具条目留空。
    access: str = ""

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
        # 可关闭面板在「面板」菜单里勾选；右栏固定节没有关闭按钮，
        # 入口是右栏页签本身——两类不能共用一句会骗人的「菜单」文案。
        if spec.closable:
            trigger = (f"「面板」菜单 → {spec.name}"
                       f"（勾选显示/隐藏，位置：{_panel_area_text(spec.area)}）")
        else:
            trigger = (f"右栏固定页签「{spec.name}」"
                       f"（位置：{_panel_area_text(spec.area)}）")
        out.append(Capability(
            category=PANEL_CATEGORY.get(spec.id, "接收"),
            title=spec.name,
            description=spec.summary or f"{spec.name}面板",
            trigger=trigger,
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
        if not cap.dependency:
            status = "unavailable"
            trigger = "无（通用解码入口未接线，桌面/AI 界面暂无入口）"
            note += "；通用入口未接线，直接走 decode_digital_mode 会返回 not_implemented"
        elif cap.evidence == EVIDENCE_NO_SAMPLE:
            # 有代码不等于「已支持」：无样本时只给入口与条件，明确标注未验证。
            status = "conditional"
            trigger = (f"命令行：python -m mbdsdr_ai --decode-file <文件> "
                       f"--mode {cap.mode}")
            note += "；无样本，未验证，不宣称已支持"
        else:
            trigger = (f"命令行：python -m mbdsdr_ai --decode-file <文件> "
                       f"--mode {cap.mode}")
        out.append(Capability(
            category="解码",
            title=cap.mode.upper(),
            description=(f"输入 {'/'.join(cap.input_formats)}，输出 {cap.output_kind}；"
                         f"采样条件：{cap.sample_rate_rule}"),
            trigger=trigger,
            source=f"decode_router:{key}",
            status=status,
            note=note,
            evidence=cap.evidence,
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


#: registry 分类前缀 → 帮助面板分类（粗分，仅决定界面分组顺序；
#: 域字段 ``Capability.domain`` 仍保留 registry 原分类，不丢细粒度）。
#: 长的具体前缀在前，避免 ``sdr_decode`` 被 ``sdr`` 抢走。
_TOOL_CATEGORY_RULES: Tuple[Tuple[str, str], ...] = (
    ("sdr_decode", "解码"), ("sdr_demod", "解码"), ("sdr_protocol", "解码"),
    ("decode", "解码"), ("digital", "解码"), ("demod", "解码"),
    ("modulation", "解码"), ("protocol", "解码"), ("fec", "解码"),
    ("modem", "解码"), ("encode", "解码"), ("broadcast", "解码"),
    ("multimon", "解码"), ("voice", "解码"), ("amr", "解码"),
    ("wireless", "解码"), ("rds", "解码"), ("ham_modes", "解码"),
    ("ssdv", "解码"), ("acquisition", "解码"),
    ("sdr_satellite", "卫星"), ("sat", "卫星"), ("orbit", "卫星"),
    ("gnss", "卫星"), ("gps", "卫星"), ("pointing", "卫星"),
    ("new_spacetime", "卫星"),
    ("sky", "天空"), ("astro", "天空"),
    ("sdr_record", "录制"), ("record", "录制"), ("capture", "录制"),
    ("file_tracker", "录制"),
    ("radio_control", "发射"), ("tx", "发射"),
    ("sdr_ui", "设置"), ("ui", "设置"), ("meta", "设置"), ("camera", "设置"),
    ("sdr", "接收"), ("spectrum", "接收"), ("signal", "接收"),
    ("dsp", "接收"), ("hal", "接收"), ("vna", "接收"),
    ("doppler", "接收"), ("pose", "接收"), ("sync", "接收"),
)


def _tool_catalog_category(category: str) -> str:
    """registry 分类 → 帮助面板分类；认不出的一律进 AI（工具面）。"""
    c = (category or "").strip().lower()
    for prefix, cat in _TOOL_CATEGORY_RULES:
        if c.startswith(prefix):
            return cat
    return "AI"


def tool_registry_capabilities(registry) -> List[Capability]:
    """把 ToolRegistry 里的每个工具变成一行能力（一个不丢）。

    与 :func:`_domain_capabilities`（MCP 面按域汇总）互补：这里覆盖
    模型真正可见的全部注册工具，逐项标注域/读写/可用性。域取 MCP 中文域名
    （若有），否则用 registry 分类；两者都没有则落「未分组」——不许丢弃。

    ``registry`` 只需提供 ``tools`` 映射（ToolRegistry 或同形替身）。
    """
    mcp_domain = {name: dom
                  for dom, names in _mcp.tool_domain_view().items()
                  for name in names}
    out: List[Capability] = []
    for name, tool in sorted(registry.tools.items()):
        category = str(tool.get("category") or "").strip()
        domain = mcp_domain.get(name) or category or "未分组"
        write = bool(tool.get("write"))
        if tool.get("available", True):
            status = "available"
            trigger = (f"AI 助手：调用工具 {name}"
                       + ("（写操作，执行前需外部确认 token）" if write else "（只读）"))
            note = ("写工具：执行走 tx_guard 一次性 confirm_token，"
                    "模型不能自备授权" if write else "")
        else:
            status = "unavailable"
            trigger = "无（注册表标记为不可用）"
            note = "注册表 available=False：入口未接线或依赖缺失"
        desc = (tool.get("definition", {}).get("function", {})
                .get("description") or "").strip()
        out.append(Capability(
            category=_tool_catalog_category(category),
            title=name,
            description=desc or f"工具 {name}",
            trigger=trigger,
            source=f"tool_registry:{name}",
            status=status,
            note=note,
            domain=domain,
            access="write" if write else "read",
        ))
    return out


def build_full_catalog(registry) -> List[Capability]:
    """完整目录 = 既有目录（面板/解码/MCP 域/快捷/静态）+ registry 全量工具。"""
    items = build_catalog() + tool_registry_capabilities(registry)
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return sorted(items, key=lambda x: (order.get(x.category, len(order)),
                                        x.title))


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


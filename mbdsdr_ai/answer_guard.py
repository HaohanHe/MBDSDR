# SPDX-License-Identifier: MIT
"""回答侧诚实护栏：低参数模型把 unknown 说成「未连接/未检测到」时纠正。

为什么需要它
------------
系统提示词已经写明「unknown ≠ 未连接」，但实测 `Qwen3-VL-8B-Thinking`
仍会在能力状态为 unknown 时回答"未检测到 IMU/GNSS"。对用户来说，
这会把「没探测」误报成「没有硬件」，直接违反诚实红线。

这里只做**极窄**的确定性改写：
* 只有该槽位在会话账本里确为 ``unknown`` 才改写；
* 只改「未检测到 / 未连接 / 没有 / 无 + 槽位名」这类短语；
* 槽位为 ``missing`` 时保留模型原话（说没有是对的）；
* 非硬件对象（如「未检测到信号」）不动。

流式路径用 :class:`StreamGuard` 在句末做同样改写；非流式在
``agent._chat_impl`` 返回前统一改写。
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, List, Pattern

#: 账本槽位 → 回答里可能出现的叫法（长词优先匹配）。
SLOT_ALIASES = {
    "sdr": ("SDR", "接收设备"),
    "gimbal": ("云台", "旋转器"),
    "gnss": ("GNSS", "北斗", "定位模块"),
    "imu": ("IMU", "姿态传感器"),
    "antenna": ("定向天线",),
}

_PREFIX_WORDS = r"(?:未检测到|没有检测到|未连接|未发现|没有|无)"
_SUFFIX_WORDS = r"(?:未连接|未检测到|没有连接|无法连接)"

#: 可以跟着槽位名的硬件名词（限定它们才允许一起改写）。
_SUFFIX_NOUNS = ("定位模块", "姿态传感器", "接收设备", "接收器",
                 "传感器", "模块", "设备", "天线")
#: 这些是「信号/定位结果」而不是「硬件不存在」：不得改写成「尚未确认」。
#: 「定位模块」单独放行（见上面的名词表）。
_NON_HW_FOLLOW = r"(?!信号|数据|卫星|载波|样本|定位(?!模块))"


def _alias_body(alias: str) -> str:
    return (re.escape(alias) + r"\s*" + _NON_HW_FOLLOW
            + r"(?:" + "|".join(_SUFFIX_NOUNS) + r")?")


def _build_patterns(slots: Iterable[str]) -> List[Pattern[str]]:
    aliases: List[str] = []
    for slot in slots:
        aliases.extend(SLOT_ALIASES.get(slot, ()))
    # 长别名优先，避免「GNSS」被更短的词抢走。
    aliases.sort(key=len, reverse=True)
    patterns: List[Pattern[str]] = []
    for alias in aliases:
        body = _alias_body(alias)
        patterns.append(re.compile(_PREFIX_WORDS + r"\s*(" + body + ")"))
        patterns.append(re.compile("(" + body + ")" + _SUFFIX_WORDS))
    return patterns


def sanitize_unknown_claims(text: str, unknown_slots: Iterable[str]) -> str:
    """把「未检测到/未连接 + unknown 槽位」改写为「<槽位>尚未确认」。

    幂等：改写后的文本不再命中原模式。
    """
    if not text:
        return text
    out = text
    for pat in _build_patterns(unknown_slots):
        out = pat.sub(lambda m: m.group(1) + "尚未确认", out)
    return out


#: 实测 8B 编造过的、本产品并不存在的 UI 细节。只列已核实为假的短语，
#: 不做通用「UI 词」拦截（那会误伤真实存在的准星/状态条/手动对齐）。
_FAKE_UI_CLAIMS: tuple[tuple[Pattern[str], str], ...] = (
    (re.compile(r"绿色光晕|彩色光晕"), "实际界面效果"),
    (re.compile(r"拖动(?:屏幕)?(?:可)?调整(?:视角)?"), "按实际界面提示操作"),
    (re.compile(r"点击屏幕中央"), "按实际界面提示"),
    (re.compile(r"长按屏幕"), "按实际界面提示"),
    (re.compile(r"双指缩放"), "按实际界面提示缩放"),
    # 实测 8B 幻觉「左右滑动屏幕手动对齐5°」；pointing_page.dart 的
    # _AlignControls（约 636-673 行）只有底部「左 5°」「右 5°」「复位」三个
    # TextButton，没有滑动手势。只命中「滑动屏幕」类手势，不碰「转动手机/
    # 转动身体」这类真实需要的动作。
    (re.compile(
        r"(?:左右)?滑动(?:手机)?屏幕"
        r"(?:[，,、]?\s*(?:手动|来|去|以|进行|调整|对齐|微调"
        r"|5\s*(?:°|度)|方位)){0,4}"),
     "按界面上的「左 5°」「右 5°」按钮调整"),
    # 实测 8B 幻觉「手动输入方位/方位角（如"朝北"）」；pointing_page.dart
    # 没有任何方位输入框，手动对齐只有底部「左 5°」「右 5°」「复位」三个按钮。
    # 只约束「输入」动词；无括号时结尾负向断言避免误伤「方位角数据」等名词。
    (re.compile(
        r"(?:手动)?输入(?:一个)?方位(?:角)?"
        r"(?:[（(][^）)\n]{0,16}[）)]|(?![\u4e00-\u9fff]))"),
     "按界面上的「左 5°」「右 5°」按钮调整"),
)


def sanitize_unverified_ui_claims(text: str) -> str:
    """把已核实的「本产品没有的 UI 交互」换成中性表述。"""
    if not text:
        return text
    out = text
    for pat, replacement in _FAKE_UI_CLAIMS:
        out = pat.sub(replacement, out)
    return out


def _norm_label(label: str) -> str:
    """去掉标签内空白，便于比对「左/右 5°」与「左/右5°」。"""
    return re.sub(r"\s+", "", label)


#: 已按 mobile 代码核实的「控件标签 → 允许方位」白名单。
#: 只写代码里真的摆在那里的组合；同一标签的其它方位仍按未核实处理，
#: 因此这里不会变成需要无限增长的短语黑名单。
#:
#: 来源：
#: * ``mobile/lib/pages/pointing_page.dart`` 636-673 ``_AlignControls``：
#:   「左 5°」「右 5°」「复位」是 ``Positioned(bottom: ...)`` 里的底部按钮行；
#: * 同文件 269-278 AppBar actions 的「视场 N°」按钮、516-583 ``_StatusRow``
#:   （``Positioned(top: ...)``）的「方位/仰角/视场」状态 chips 在顶部；
#: * ``mobile/lib/pages/sky_page.dart`` 540-544：AppBar actions 的
#:   「指天视图」入口（右上）。
#:
#: 注意：「准星下方」「顶部状态」这类真实关系没有方位词绑定，不经过本规则。
_VERIFIED_UI_POSITIONS: dict[str, frozenset[str]] = {
    "左 5°": frozenset({"底部"}),
    "右 5°": frozenset({"底部"}),
    "左/右 5°": frozenset({"底部"}),
    "左 5°/右 5°": frozenset({"底部"}),
    "复位": frozenset({"底部"}),
    "视场": frozenset({"顶部", "右上角"}),
    "指天视图": frozenset({"顶部", "右上角"}),
}
_VERIFIED_UI_POSITIONS_NORM = {
    _norm_label(label): allowed
    for label, allowed in _VERIFIED_UI_POSITIONS.items()
}

#: 「视场 60°」「视场 25°」等是真实按钮/状态标签（视场值随档位变）。
_VERIFIED_FOV_LABEL = re.compile(r"^视场\d+(?:\.\d+)?°$")

_POSITION_WORDS = ("左上角", "右上角", "左下角", "右下角",
                   "顶部", "底部", "左侧", "右侧", "中央",
                   "两侧", "四周", "角落", "正中")
_UI_SCOPE = r"(?:屏幕|页面|界面|画面)?"
_POSITION_TOKEN = _UI_SCOPE + r"(?P<pos>" + "|".join(_POSITION_WORDS) + r")"
_LABEL_SPAN = r"(?P<label>「[^」\n]{1,24}」|“[^”\n]{1,24}”)"
_UI_SCOPE_STRIP = re.compile(r"^(?:屏幕|页面|界面|画面)?")

#: 「屏幕右下角「左/右 5°」按钮」：位置在标签前。
_POSITION_BEFORE_LABEL = re.compile(
    _POSITION_TOKEN + r"(?:的|有|是|为|处|上|中|里)?\s*" + _LABEL_SPAN)
#: 「「复位」按钮位于屏幕右下角」：位置在标签后。
_POSITION_AFTER_LABEL = re.compile(
    _LABEL_SPAN + r"(?P<ctl>按钮|控件|入口|开关)?"
    r"(?:就|则)?(?:位于|在|放在|处于|显示在|显示于)\s*"
    + _POSITION_TOKEN)


def _verified_positions(label: str) -> frozenset[str] | None:
    """返回该标签允许的方位；空标签/未核实标签返回 None。"""
    key = _norm_label(label)
    allowed = _VERIFIED_UI_POSITIONS_NORM.get(key)
    if allowed is not None:
        return allowed
    if _VERIFIED_FOV_LABEL.fullmatch(key):
        return _VERIFIED_UI_POSITIONS["视场"]
    return None


def _neutralize_ui_position(match: re.Match[str]) -> str:
    """只去掉未核实的位置断言；已核实标签和真实位置原样保留。"""
    span = match.group("label")
    label = span[1:-1]
    allowed = _verified_positions(label)
    ctl = match.groupdict().get("ctl") or ""
    token = _UI_SCOPE_STRIP.sub("", match.group("pos") or "")
    if allowed is not None and token in allowed:
        return match.group(0)
    if allowed is not None:
        return span + ctl
    if ctl == "控件":          # 「对应控件控件」读起来像 bug
        ctl = ""
    return "对应控件" + ctl


def sanitize_unverified_ui_positions(text: str) -> str:
    """中性化「未核实位置 + UI 标签」的绑定，保留已核实标签。

    实测 8B 会把真实存在的底部按钮说成「屏幕右下角「左/右 5°」按钮」。
    本规则只处理同一分句内、位置词与「…」/“…”标签紧邻的断言：
    位置已在白名单中则原样保留；位置未核实则只删位置；标签本身也不在
    已核实集合时换成「对应控件」。编号清单与无位置断言的文本不受影响。
    幂等：改写后不再有位置词与标签紧邻。
    """
    if not text:
        return text
    out = _POSITION_BEFORE_LABEL.sub(_neutralize_ui_position, text)
    return _POSITION_AFTER_LABEL.sub(_neutralize_ui_position, out)


def sanitize_answer(text: str, unknown_slots: Iterable[str]) -> str:
    """对模型回答统一施加的诚实护栏（unknown 断言 + 未核实 UI 事实）。"""
    return sanitize_unverified_ui_positions(
        sanitize_unverified_ui_claims(
            sanitize_unknown_claims(text, unknown_slots)))


def _deliver(sink: Callable[[str], None], text: str) -> None:
    """把改写后的文本交给调用方。

    单独提成模块函数是为了让符号检查能看懂：``StreamGuard`` 里的回调是
    构造时注入的**属性**，不是本类方法，直接写 ``self._emit(...)`` 会被
    静态检查误判成「缺失方法」。
    """
    if text:
        sink(text)


class StreamGuard:
    """流式输出护栏：按句缓冲，句末改写未知槽位断言。

    只按句末标点/换行切分；无标点时每 80 字符强制放行，避免长段不吐字。
    """

    def __init__(self, emit: Callable[[str], None],
                 unknown_slots: Iterable[str]):
        self._sink = emit
        self._slots = tuple(unknown_slots)
        self._buf = ""

    def feed(self, piece: str) -> None:
        if not piece:
            return
        self._buf += piece
        while True:
            m = re.search(r"[。！？\n]", self._buf)
            if not m:
                break
            end = m.end()
            seg, self._buf = self._buf[:end], self._buf[end:]
            _deliver(self._sink, sanitize_answer(seg, self._slots))
        if len(self._buf) >= 80:
            seg, self._buf = self._buf, ""
            _deliver(self._sink, sanitize_answer(seg, self._slots))

    def flush(self) -> None:
        if self._buf:
            seg, self._buf = self._buf, ""
            _deliver(self._sink, sanitize_answer(seg, self._slots))

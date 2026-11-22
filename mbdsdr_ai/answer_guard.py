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


class StreamGuard:
    """流式输出护栏：按句缓冲，句末改写未知槽位断言。

    只按句末标点/换行切分；无标点时每 80 字符强制放行，避免长段不吐字。
    """

    def __init__(self, emit: Callable[[str], None],
                 unknown_slots: Iterable[str]):
        self._emit = emit
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
            self._emit(sanitize_unknown_claims(seg, self._slots))
        if len(self._buf) >= 80:
            seg, self._buf = self._buf, ""
            self._emit(sanitize_unknown_claims(seg, self._slots))

    def flush(self) -> None:
        if self._buf:
            seg, self._buf = self._buf, ""
            self._emit(sanitize_unknown_claims(seg, self._slots))

# SPDX-License-Identifier: MIT
"""
统一搜索的 provider 层（QGIS locator 思路）
===========================================

命令面板不再硬编码「四类入口」，而是遍历模块底部声明的 ``PROVIDERS``：
新增一类搜索源只需在这里加一行注册项，搜索框、结果渲染、键盘路径都不用改。

每个 provider 只做两件事：

  * ``status(window)``   —— 这一类**现在能不能用**；不能用时给出人话原因
    （例如「卫星库未加载：TLE 未就绪」），而不是返回空列表让用户以为搜错了。
  * ``commands(window, prior)`` —— 产出 ``Command``；不可用时返回空列表。
    ``prior`` 是前面 provider 已产出的条目（快捷键 provider 用它去重）。

数据来源仍是各自的唯一事实源，本模块只做**薄包装**：
菜单/页签/面板/能力/快捷键复用 ``command_palette`` 的既有构建函数，
设置项读 ``settings_panel.PANEL_SECTIONS`` + ``config_schema.SCHEMA``，
卫星读主窗口 ``_loop_sat_tracker.list_satellites()``。

依赖方向：``search_providers`` → ``command_palette`` 单向；反向 import
放在 ``command_palette.build_index`` 函数体内，避免循环导入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from command_palette import (
    Command,
    _capability_commands,
    _jump_to_right,
    _menu_commands,
    _panel_commands,
    _shortcut_commands,
    _tab_commands,
)

__all__ = [
    "PROVIDERS",
    "ProviderStatus",
    "SearchProvider",
    "SettingsProvider",
    "SatelliteProvider",
]

#: 「这一类现在不可用」的统一说法：空结果与不可用必须分开表达。
SATELLITE_UNAVAILABLE = "卫星库未加载：TLE 未就绪"


@dataclass(frozen=True)
class ProviderStatus:
    """一类搜索源的可用性；``reason`` 只在不可用时写。"""

    available: bool = True
    reason: str = ""


class SearchProvider:
    """搜索源基类：声明分组名 + 可用性 + 条目生成。"""

    #: 结果分组名（必须登记在 command_palette.GROUPS）。
    group = ""

    def status(self, window) -> ProviderStatus:
        return ProviderStatus()

    def commands(self, window, prior: Sequence[Command]) -> List[Command]:
        raise NotImplementedError


class _MenuProvider(SearchProvider):
    group = "菜单"

    def commands(self, window, prior):
        return _menu_commands(window)


class _TabProvider(SearchProvider):
    group = "页签"

    def commands(self, window, prior):
        return _tab_commands(window)


class _PanelProvider(SearchProvider):
    group = "面板"

    def commands(self, window, prior):
        return _panel_commands(window)


class _CapabilityProvider(SearchProvider):
    group = "能力"

    def commands(self, window, prior):
        return _capability_commands(window)


class _ShortcutProvider(SearchProvider):
    group = "快捷键"

    def commands(self, window, prior):
        # 菜单动作已覆盖的同名同键条目不重复刷屏（沿用原有去重口径）。
        return _shortcut_commands(window, prior)


class SettingsProvider(SearchProvider):
    """设置项：搜到的是**那一行**，不是「设置」页签本身。"""

    group = "设置项"

    def status(self, window) -> ProviderStatus:
        if window is None:
            return ProviderStatus()          # 无窗口 = 不适用，不算「不可用」
        if getattr(window, "settings_dock", None) is None:
            return ProviderStatus(False, "设置面板未创建：当前无法定位设置项")
        return ProviderStatus()

    def commands(self, window, prior):
        if not self.status(window).available:
            return []
        # 数据层不建 UI：直接读面板的声明式分组与 schema 标签。
        from config_schema import SCHEMA
        from settings_panel import PANEL_SECTIONS

        out: List[Command] = []
        for section_title, _purpose, keys in PANEL_SECTIONS:
            for key in keys:
                item = SCHEMA.get(key)
                if item is None or not item.label:
                    continue
                out.append(Command(
                    title=item.label,
                    group=self.group,
                    description=f"设置项（{section_title}）：{item.help}",
                    trigger=f"设置 · {section_title} · {key}",
                    source=f"settings:{key}",
                    run=lambda w=window, k=key, t=item.label:
                        _reveal_setting(w, k, t),
                ))
        return out


def _reveal_setting(window, key: str, label: str) -> dict:
    """展开该设置项所在分组、清掉过滤态，并把焦点落到那一行的控件。"""
    panel = getattr(window, "settings_dock", None)
    if panel is None:
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": "设置面板未创建：当前无法定位设置项"}
    ensure = getattr(panel, "_ensure_ui_built", None)
    if callable(ensure):
        ensure()
    search = getattr(panel, "search_edit", None)
    if search is not None and search.text():
        # 过滤态会把未命中的行隐藏起来，目标行 setFocus 会被 Qt 忽略。
        search.clear()
    group = getattr(panel, "_key_section", {}).get(key)

    def prepare(_window) -> None:
        section = getattr(panel, "_group_sections", {}).get(group)
        if section is None:
            return
        if not section.is_expanded():
            section.set_expanded(True)
        reveal = getattr(panel, "_reveal_group", None)
        if callable(reveal):
            reveal(section, True)

    return _jump_to_right(
        window, "settings", label,
        resolve=lambda _w: getattr(panel, "_rows", {}).get(key),
        prepare=prepare,
    )


class SatelliteProvider(SearchProvider):
    """卫星：输入名字/编号跳到该卫星；目录没加载时说明原因。"""

    group = "卫星"

    @staticmethod
    def _catalog(window) -> List[dict]:
        tracker = getattr(window, "_loop_sat_tracker", None)
        if tracker is None:
            return []
        try:
            return list(tracker.list_satellites() or [])
        except Exception:       # 目录实现异常也按「未就绪」如实上报
            return []

    def status(self, window) -> ProviderStatus:
        if window is None:
            return ProviderStatus()
        if getattr(window, "sat_track_panel", None) is None:
            return ProviderStatus(False, "卫星跟踪面板未创建：无法跳转")
        if not self._catalog(window):
            return ProviderStatus(False, SATELLITE_UNAVAILABLE)
        return ProviderStatus()

    def commands(self, window, prior):
        if not self.status(window).available:
            return []
        out: List[Command] = []
        for sat in self._catalog(window):
            name = str(sat.get("name") or "").strip()
            if not name:
                continue
            norad = sat.get("norad_id")
            tier = str(sat.get("tle_tier_label") or "").strip()
            trigger = f"NORAD {norad}" if norad else "卫星目录"
            if tier:
                trigger += f" · {tier}"
            out.append(Command(
                title=name,
                group=self.group,
                description=("卫星跟踪：跳到该卫星并让「卫星」下拉选中它"
                             + (f"（星历：{tier}）" if tier else "")),
                trigger=trigger,
                source=f"sat:{norad or name}",
                run=lambda w=window, n=name, no=norad:
                    _reveal_satellite(w, n, no),
            ))
        return out


def _reveal_satellite(window, name: str, norad=None) -> dict:
    """切到「卫星跟踪」页签，把下拉框选到该星；下拉里没有就如实说明。"""
    panel = getattr(window, "sat_track_panel", None)
    if panel is None:
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": "卫星跟踪面板未创建：无法跳转"}
    combo = getattr(panel, "sat_combo", None)
    if combo is None:
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": "卫星跟踪面板没有卫星下拉框：无法跳转"}
    if combo.findData(name) < 0 and combo.findText(name) < 0:
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": (f"卫星下拉框里还没有「{name}」："
                           "目录刚更新或面板未刷新，先打开「卫星跟踪」页签再看")}

    def prepare(_window) -> None:
        idx = combo.findData(name)
        if idx < 0:
            idx = combo.findText(name)
        if idx >= 0:
            # 不屏蔽信号：与用户手选同一路径，主窗口据此回填下行频率。
            combo.setCurrentIndex(idx)

    return _jump_to_right(window, "sat_track", name,
                          resolve=lambda _w: combo, prepare=prepare)


#: 声明式注册表：新增一类搜索源 = 在这里加一个 provider，搜索框不用改。
PROVIDERS: Tuple[SearchProvider, ...] = (
    _MenuProvider(),
    _TabProvider(),
    _PanelProvider(),
    _CapabilityProvider(),
    _ShortcutProvider(),
    SettingsProvider(),
    SatelliteProvider(),
)

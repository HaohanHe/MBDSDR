# SPDX-License-Identifier: MIT
"""
全局命令面板（统一索引 + 搜索 + 执行）
======================================

一个搜索框找到四类入口，不做第二份手抄清单：

  * **菜单命令** ← 主窗口 ``menuBar()`` 里真实存在的 QAction（含键位）
  * **页签定位** ← 左/右栏 ``QTabWidget`` 的 tabData（如 settings_dock）
  * **能力**     ← ``desktop/capability_catalog.py``（53 项 / 8 类，注册表实时生成）
  * **快捷键**   ← ``desktop/shortcuts.py``（与菜单已重复的条目自动去重）

红线：
  * 不可用项必须灰显并写原因（菜单项置灰取 tooltip，能力项取 note），
    不许出现「点了没反应」的条目；
  * 面板本身不造数据：搜索只过滤真实索引，执行只调用真实接缝
    （``QAction.trigger()`` / ``MainWindow.select_tab(tabData)`` / 打开能力目录定位）。

数据层（``build_index`` / ``search_commands``）不依赖 QApplication，测试可离线断言；
Qt 类在 ``from command_palette import CommandPalette`` 时才构造（PEP 562）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from shortcuts import SHORTCUTS  # noqa: E402
from capability_catalog import build_catalog  # noqa: E402

try:  # 常规：仓库根在 sys.path 上（main.py / 测试都这么进来）
    from mbdsdr_ai.corrupt_evidence import write_reason
except ImportError:  # pragma: no cover - 兼容只把 desktop/ 放进 sys.path 的调用方
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from mbdsdr_ai.corrupt_evidence import write_reason

logger = logging.getLogger(__name__)

#: 分组顺序（界面上按此顺序排；搜索命中后按相关度重排）
GROUPS = ("菜单", "页签", "面板", "能力", "快捷键")

#: 「最近使用」落盘文件名（与 desktop_settings.json 同目录，独立小文件不碰用户设置）
RECENT_FILENAME = "command_palette_recent.json"

#: 最近使用最多保留条数（再多也排不进首屏）
RECENT_LIMIT = 12

#: 模糊（子序列）匹配的起始分档：10 起，越紧凑越小
_FUZZY_BASE = 10

_RECENT: Optional[List[str]] = None

#: 上次读取失败的人话说明（None = 正常）。目前没有界面消费：命令面板只列
#: 「可执行动作」，把这条错误硬塞进搜索框或结果列表会干扰语义。接线等
#: main_window.py 的状态栏/诊断页。
_LAST_ERROR: Optional[str] = None

#: 最近使用文件读不了且无法备份时拒绝写盘：remember_recent 会覆盖同一个文件。
_SAVE_BLOCKED = False


def _recent_path() -> Path:
    """最近使用文件的绝对路径；测试可用 monkeypatch 覆盖本函数。"""
    import settings as _desktop_settings  # 延迟导入，数据层测试不必起 Qt

    return Path(_desktop_settings.default_config_path()).parent / RECENT_FILENAME


def recent_error() -> Optional[str]:
    """最近使用文件的读取/写入错误（None = 正常）。"""
    return _LAST_ERROR


def backup_corrupt_recent(path: Path) -> Optional[str]:
    """把损坏的最近使用文件改名成 ``*.corrupt-时间戳``，返回备份路径（失败 None）。

    这是损坏留证的**公共接缝**：测试模拟「备份失败」时替换本函数返回
    ``None``，不再改模块私有名。
    """
    backup = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(path, backup)
    except OSError as exc:
        logger.error("命令面板最近使用文件损坏备份失败：%s（%s）", path, exc)
        return None
    return backup


def _mark_corrupt_recent(path: Path, reason: str) -> None:
    """损坏：先备份原件再留人话错误；备份失败则拒绝后续写盘。"""
    global _LAST_ERROR, _SAVE_BLOCKED
    backup = backup_corrupt_recent(path)
    if backup is None:
        _LAST_ERROR = (
            f"命令面板最近使用文件损坏（{reason}）：{path}；"
            "备份失败，本次不写入，避免覆盖唯一证据")
        _SAVE_BLOCKED = True
        return
    _LAST_ERROR = (
        f"命令面板最近使用文件损坏（{reason}）：{path}；"
        f"原件已备份为 {backup}，本次按空列表启动")
    _SAVE_BLOCKED = False
    write_reason(backup, _LAST_ERROR, logger)


def _read_recent_file() -> List[str]:
    """读最近使用：缺失 = 空（首次启动）；损坏先备份；读不了不写。

    这个文件只存入口 source，丢了不影响任何功能；但「当成空、下一次
    remember_recent 覆盖」会连排查线索一起抹掉，所以仍按统一口径留证据。
    """
    global _LAST_ERROR, _SAVE_BLOCKED
    path = _recent_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _LAST_ERROR = None
        _SAVE_BLOCKED = False
        return []
    except OSError as exc:
        _LAST_ERROR = (
            f"命令面板最近使用读取失败（{type(exc).__name__}）：{path}；"
            "本次不写入，避免覆盖可能仍可恢复的列表")
        _SAVE_BLOCKED = True
        logger.warning("命令面板最近使用读取失败（非损坏，不备份）：%s", exc)
        return []
    except ValueError as exc:
        _mark_corrupt_recent(path, f"解析失败（{type(exc).__name__}: {exc}）")
        return []
    if not isinstance(raw, list):
        _mark_corrupt_recent(path, "根节点不是列表")
        return []
    if any(not isinstance(x, str) for x in raw):
        _mark_corrupt_recent(path, "条目不是字符串")
        return []
    _LAST_ERROR = None
    _SAVE_BLOCKED = False
    return raw[:RECENT_LIMIT]


def _write_recent_file(items: Sequence[str]) -> None:
    global _LAST_ERROR
    if _SAVE_BLOCKED:
        _LAST_ERROR = (
            "命令面板最近使用未保存：读不了且无法备份；"
            "为避免覆盖唯一证据，本次不写入")
        logger.error("命令面板最近使用写入被拒绝（文件不可用且未备份）")
        return
    path = _recent_path()
    tmp_path = f"{path}.tmp"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再原子替换：写到一半失败不会把原文件截成半截 JSON。
        Path(tmp_path).write_text(json.dumps(list(items), ensure_ascii=False),
                                  encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError as cleanup_exc:
            logger.warning("命令面板最近使用临时文件清理失败：%s（%s）",
                           tmp_path, cleanup_exc)
        _LAST_ERROR = (
            f"命令面板最近使用写入失败（{type(exc).__name__}）：{path}；"
            "本次未写入，不影响本次执行")
        logger.debug("%s", _LAST_ERROR)


def load_recent() -> List[str]:
    """读最近使用（进程内缓存；首次访问才读盘）。"""
    global _RECENT
    if _RECENT is None:
        _RECENT = _read_recent_file()
    return _RECENT


def remember_recent(source: str, *, persist: bool = True) -> None:
    """把一条入口置顶为「最近使用」；``persist=False`` 只改内存（测试用）。"""
    if not source:
        return
    items = load_recent()
    if source in items:
        items.remove(source)
    items.insert(0, source)
    del items[RECENT_LIMIT:]
    if persist:
        _write_recent_file(items)


def reset_recent() -> None:
    """清空进程内缓存与错误状态（测试隔离用；不动磁盘文件）。"""
    global _RECENT, _LAST_ERROR, _SAVE_BLOCKED
    _RECENT = None
    _LAST_ERROR = None
    _SAVE_BLOCKED = False


#: 状态 → 界面后缀
_STATUS_SUFFIX = {
    "available": "",
    "conditional": "（需条件）",
    "unavailable": "（不可用）",
}


@dataclass(frozen=True)
class Command:
    """统一索引的一行。

    ``run`` 返回 ``{"ok": bool, "status": str, "reason": str}``；``None`` 表示
    该条目只在目录里展示（不可用项由 ``reason`` 说明为什么）。
    """

    title: str
    group: str
    description: str
    trigger: str = ""
    status: str = "available"
    reason: str = ""
    source: str = ""
    run: Optional[Callable[[], dict]] = field(default=None, compare=False)

    @property
    def available(self) -> bool:
        return self.status != "unavailable" and self.run is not None


# ---------------------------------------------------------------------------
# 数据层：四类索引
# ---------------------------------------------------------------------------
def _trigger_text(action) -> str:
    from PySide6.QtGui import QKeySequence
    seq = action.shortcut()
    if seq.isEmpty():
        return ""
    return seq.toString(QKeySequence.NativeText)


def _trigger_action(action, label: str) -> dict:
    if not action.isEnabled():
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": f"「{label}」当前不可用"}
    action.trigger()
    return {"ok": True, "status": "DONE", "reason": f"已执行「{label}」"}


def _walk_menu(menu, path: List[str], out: List[Command]) -> None:
    for act in menu.actions():
        if act.isSeparator():
            continue
        sub = act.menu()
        label = act.text().replace("&", "").strip()
        if sub is not None:
            _walk_menu(sub, path + [label], out)
            continue
        if not label:
            continue
        key = _trigger_text(act)
        enabled = act.isEnabled()
        tip = (act.toolTip() or act.statusTip()).strip()
        if tip == label:
            tip = ""  # Qt 未显式设 tooltip 时会回落到动作文字，不拿它当说明
        reason = "" if enabled else (
            tip or "当前不可用：需要先连接设备或选中目标")
        out.append(Command(
            title=label,
            group="菜单",
            description=tip or f"「{' → '.join(path)}」菜单里的命令",
            trigger=f"{' → '.join(path)}（{key}）" if key else " → ".join(path),
            status="available" if enabled else "unavailable",
            reason=reason,
            source=f"menu:{' → '.join(path)}:{label}",
            run=(lambda a=act, t=label: _trigger_action(a, t)) if enabled else None,
        ))


def _menu_commands(window) -> List[Command]:
    out: List[Command] = []
    bar = getattr(window, "menuBar", None)
    if not callable(bar):
        return out
    _walk_menu(bar(), [], out)
    return out


def _tab_commands(window) -> List[Command]:
    out: List[Command] = []
    for area, tab in (("左栏", getattr(window, "left_tab", None)),
                      ("右栏", getattr(window, "right_tab", None))):
        if tab is None:
            continue
        for i in range(tab.count()):
            title = tab.tabText(i)
            key = tab.tabBar().tabData(i) or f"tab:{i}"
            out.append(Command(
                title=title,
                group="页签",
                description=f"切到{area}「{title}」页签；找不到面板入口时用这个",
                trigger=f"{area}页签 · tabData={key}",
                source=f"tab:{key}",
                run=lambda k=key: _select_tab(window, k),
            ))
    return out


def _select_tab(window, tab_key: str) -> dict:
    select = getattr(window, "select_tab", None)
    if not callable(select):
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": "主窗口没有 select_tab 定位接口"}
    return select(tab_key)


def _open_catalog_entry(window, cap_title: str) -> dict:
    from help_panel import HelpPanel
    dlg = HelpPanel(window, initial_tab=1)
    dlg.search_edit.setText(cap_title)
    dlg.show()
    return {"ok": True, "status": "DONE",
            "reason": f"已在能力目录里定位「{cap_title}」"}


def _capability_commands(window) -> List[Command]:
    out: List[Command] = []
    for cap in build_catalog():
        unavailable = cap.status == "unavailable"
        out.append(Command(
            title=cap.title,
            group="能力",
            description=cap.description,
            trigger=cap.trigger,
            status=cap.status,
            reason=cap.note,
            source=cap.source,
            run=None if unavailable else (
                lambda t=cap.title: _open_catalog_entry(window, t)),
        ))
    return out


def _open_shortcut_entry(window, label: str) -> dict:
    from shortcuts_panel import ShortcutsPanel
    dlg = ShortcutsPanel(window)
    dlg.search_edit.setText(label)
    dlg.show()
    return {"ok": True, "status": "DONE",
            "reason": f"已在快捷键一览里定位「{label}」"}


def _focus_spectrum(window) -> dict:
    res = _select_tab(window, "spectrum")
    if not res.get("ok"):
        return res
    plot = getattr(getattr(window, "spectrum", None), "_plot", None)
    if plot is not None:
        plot.setFocus()
    return {"ok": True, "status": "DONE",
            "reason": "已切到频谱页签并让谱面获得焦点，现在按该键即可"}


def _shortcut_commands(window, existing: Sequence[Command]) -> List[Command]:
    """快捷键条目；菜单命令已覆盖的同名同键条目自动去重。"""
    out: List[Command] = []
    covered = {(c.title, c.trigger) for c in existing}
    for s in SHORTCUTS:
        if not s.key:
            continue  # 无键条目本身就是菜单项/低频入口，已由菜单命令覆盖
        if any(c.title == s.label and s.key in c.trigger for c in existing):
            continue  # 菜单动作已携带同一键位，不重复刷屏
        if (s.label, s.key) in covered:
            continue
        desc = s.note or f"快捷键 {s.key}；{s.label}"
        if s.scope == "spectrum":
            status, reason = "conditional", "只在频谱控件获焦时生效；点击会切到频谱并聚焦"
            run = lambda w=window: _focus_spectrum(w)
        elif s.scope == "planetarium":
            status = "conditional"
            reason = "只在星象仪模式下生效；先按 Ctrl+Shift+P 进入"
            run = lambda w=window, t=s.label: _open_shortcut_entry(w, t)
        else:
            status, reason = "available", ""
            run = lambda w=window, t=s.label: _open_shortcut_entry(w, t)
        out.append(Command(
            title=s.label,
            group="快捷键",
            description=desc,
            trigger=s.key,
            status=status,
            reason=reason,
            source=f"shortcut:{s.id}",
            run=run,
        ))
    return out


def _expand_fold(panel, title: str) -> bool:
    """展开面板内某个可折叠分组（如控制面板的「接收」）。找到返回 True。"""
    for header, _group, name in getattr(panel, "_fold_sections", ()):
        if title in name:
            header.setChecked(True)   # checkable 头 → 触发展开 + 手风琴收起其它组
            return True
    return False


def _attr_target(panel_attr: str, widget_attr: str):
    """按「面板属性 + 控件属性」取目标控件；纯读取，构建索引时调用无副作用。"""
    def resolve(window):
        panel = getattr(window, panel_attr, None)
        return getattr(panel, widget_attr, None) if panel is not None else None
    return resolve


def _fold_opener(panel_attr: str, title: str):
    """执行时展开面板内的折叠组（手风琴），否则组内控件不可见也落不到焦点。"""
    def prepare(window):
        panel = getattr(window, panel_attr, None)
        if panel is not None:
            _expand_fold(panel, title)
    return prepare


def _jump_to_right(window, tab_key: str, label: str, *, resolve,
                   prepare=None) -> dict:
    """切到右栏目标节、按需展开折叠组，并把焦点落到指定控件。

    窄窗折叠态先开临时浮层：此时右栏在 QStackedLayout 的非当前页里，
    不搬出来 setFocus 也落不到可见控件上。
    """
    if window is None:
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": "没有主窗口，无法定位右栏"}
    if getattr(window, "_right_collapsed", False):
        opener = getattr(window, "_open_right_overlay", None)
        if callable(opener):
            opener()
    res = _select_tab(window, tab_key)
    if not res.get("ok"):
        return res
    if prepare is not None:
        prepare(window)
    target = resolve(window)
    if target is None:
        return {"ok": False, "status": "UNAVAILABLE",
                "reason": f"「{label}」所在面板当前不可用"}

    def _focus() -> None:
        try:
            if not target.isVisible():
                return
            target.setFocus()
            # setCurrentIndex 的 reveal 只会滚到节标题，节内深处的控件会留在
            # 视口外（如「设置 → 数据与操作」的导出按钮）；落焦后再滚一次。
            w = target.parentWidget()
            while w is not None:
                ensure = getattr(w, "ensureWidgetVisible", None)
                if callable(ensure):
                    ensure(target, 0, 24)
                    break
                w = w.parentWidget()
        except RuntimeError:     # 面板已销毁
            pass

    # run_command 成功后会隐藏面板并把焦点还给「打开前那颗」；下一帧再落目标，
    # 否则这次定位刚设的焦点会被返还逻辑覆盖。
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, _focus)
    return {"ok": True, "status": "DONE",
            "reason": f"已展开右栏并聚焦「{label}」"}


#: 设置面板里「导出配置」按钮所在的分组（与 settings_panel.PANEL_SECTIONS 同名）。
_SETTINGS_EXPORT_GROUP = "数据与操作"


def _settings_first_control(window):
    """设置节里的第一个可操作控件：搜索框（可直接打字过滤任一项）。"""
    panel = getattr(window, "settings_dock", None)
    return getattr(panel, "search_edit", None)


def _settings_export_button(window):
    panel = getattr(window, "settings_dock", None)
    return getattr(panel, "export_btn", None)


def _reveal_settings_export(window) -> None:
    """展开「数据与操作」组并把它滚进视口，否则导出按钮不可见、焦点落不上。"""
    panel = getattr(window, "settings_dock", None)
    if panel is None:
        return
    group = getattr(panel, "_group_sections", {}).get(_SETTINGS_EXPORT_GROUP)
    if group is None:
        return
    if not group.is_expanded():
        group.set_expanded(True)
    reveal = getattr(panel, "_reveal_group", None)
    if callable(reveal):
        reveal(group, True)


#: 「跳到/打开」入口的声明：(标题, 页签身份, 显示名, 取目标控件, 执行前准备,
#: 说明, 触发方式)
_PANEL_JUMPS = (
    ("跳到设备", "device", "设备", _attr_target("device_panel", "dev_combo"),
     None,
     "展开右栏「设备」节并聚焦设备下拉，省去 Tab 穿过整条右栏",
     "命令面板 · 右栏「设备」"),
    ("跳到增益", "control", "增益滑杆",
     _attr_target("control_panel", "gain_slider"),
     _fold_opener("control_panel", "接收"),
     "展开右栏「控制 → 接收」并聚焦增益滑杆，省去先找折叠组",
     "命令面板 · 右栏「控制 → 接收」"),
    ("打开设置", "settings", "设置搜索框", _settings_first_control, None,
     "展开右栏「设置」并聚焦设置搜索框，可直接打字过滤任一项",
     "命令面板 · 右栏「设置」"),
    ("导出", "settings", "导出按钮", _settings_export_button,
     _reveal_settings_export,
     "展开右栏「设置 → 数据与操作」并聚焦「导出配置」按钮",
     "命令面板 · 右栏「设置 → 数据与操作」"),
)


def _panel_commands(window) -> List[Command]:
    """右栏深处的直接入口（设备/增益/设置/导出）。

    不新增全局快捷键（要过冲突检查，且键位越多越难记）：命令面板已有
    Ctrl+K 与模糊搜索，这几条把「右栏定位」接成可执行入口，可发现性更好。
    """
    if window is None:
        return []
    out: List[Command] = []
    for title, key, label, resolve, prepare, desc, trig in _PANEL_JUMPS:
        ok = resolve(window) is not None
        out.append(Command(
            title=title,
            group="面板",
            description=desc,
            trigger=trig,
            status="available" if ok else "unavailable",
            reason="" if ok else f"「{label}」所在面板当前不可用",
            source=f"panel:{key}",
            run=(lambda w=window, k=key, l=label, r=resolve, p=prepare:
                 _jump_to_right(w, k, l, resolve=r, prepare=p)) if ok else None,
        ))
    return out


def build_index(window=None) -> List[Command]:
    """构建统一索引：菜单动作 + 页签 + 右栏定位 + 能力目录 + 快捷键。

    无窗口时只有能力与快捷键两类（菜单/页签/右栏定位都需要真实窗口）。
    """
    menu = _menu_commands(window)
    commands = (menu + _tab_commands(window) + _panel_commands(window)
                + _capability_commands(window))
    commands += _shortcut_commands(window, commands)
    return commands


def _fuzzy_score(text: str, q: str) -> Optional[int]:
    """子序列匹配（不必连续、按顺序）；返回越紧凑越小的分数，不匹配返回 None。

    单字符子序列等价于子串，已由前缀/包含档位覆盖，这里只处理 ≥2 字符，
    避免把「随便一个字命中」的噪声项推上前排。
    """
    if len(q) < 2:
        return None
    start = -1
    end = -1
    pos = 0
    for ch in q:
        idx = text.find(ch, pos)
        if idx < 0:
            return None
        if start < 0:
            start = idx
        end = idx
        pos = idx + 1
    span = end - start + 1
    return _FUZZY_BASE + min(span - len(q), 9)


def _relevance(cmd: Command, q: str) -> Optional[int]:
    """分档：逐字 > 前缀 > 标题包含 > 说明/触发命中 > 标题模糊 > 说明模糊。"""
    title = cmd.title.lower()
    if title == q:
        return 0
    if title.startswith(q):
        return 1
    if q in title:
        return 2
    hay = " ".join((cmd.description, cmd.trigger, cmd.reason,
                    cmd.group)).lower()
    if q in hay:
        return 3
    fuzzy = _fuzzy_score(title, q)
    if fuzzy is not None:
        return fuzzy                     # 10–19
    fuzzy = _fuzzy_score(hay, q)
    if fuzzy is not None:
        return 20 + (fuzzy - _FUZZY_BASE)  # 20–29
    return None


def _recent_rank(recent: Optional[Sequence[str]]) -> dict:
    rank: dict = {}
    for i, src in enumerate(recent or ()):
        rank.setdefault(src, i)
    return rank


def search_commands(commands: Sequence[Command], query: str,
                    recent: Optional[Sequence[str]] = None) -> List[Command]:
    """过滤并排序：相关度 > 最近使用 > 索引原序。

    空查询不返回「全部（含不可用）」——那是点了没反应的一堆灰条；
    只给**可执行动作清单**，最近用过的排前。
    """
    q = (query or "").strip().lower()
    rank = _recent_rank(recent)
    if not q:
        order = sorted(
            enumerate(c for c in commands if c.available),
            key=lambda t: (rank.get(t[1].source, 10 ** 6), t[0]))
        return [cmd for _i, cmd in order]
    scored = []
    for order, cmd in enumerate(commands):
        score = _relevance(cmd, q)
        if score is not None:
            scored.append((score, rank.get(cmd.source, 10 ** 6), order, cmd))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return [cmd for _s, _r, _o, cmd in scored]


# ---------------------------------------------------------------------------
# Qt 层（PEP 562：只在取 CommandPalette 时构造）
# ---------------------------------------------------------------------------
def _build_dialog_class():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import (
        QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
    )

    def _first_focusable(root):
        """主窗焦点链上第一个可聚焦、可见、可用的控件（兜底落点）。

        打开面板前那颗控件若已被销毁（设置页重建、面板被隐藏等），不能
        把焦点丢给系统——那等于让键盘用户从头数 Tab。这里按 Qt 自己的
        焦点链顺序取第一颗能停的控件，结果稳定、与面板内部实现无关。
        """
        chain = root.nextInFocusChain()
        for _ in range(4096):
            if chain is None or chain is root:
                return None
            try:
                usable = (chain.isVisible() and chain.isEnabled()
                          and int(chain.focusPolicy()) & int(Qt.TabFocus))
            except RuntimeError:      # C++ 对象已销毁
                usable = False
            if usable:
                return chain
            chain = chain.nextInFocusChain()
        return None

    class CommandPalette(QDialog):
        """全局命令面板（非模态；Esc 关闭，回车执行选中项）。"""

        def __init__(self, window=None, parent=None):
            super().__init__(parent if parent is not None else window)
            self.setWindowTitle("命令面板")
            self.resize(760, 520)
            self.setModal(False)
            self._window = window
            # 打开前的焦点控件：执行成功后要还回去，不能让用户重新数 Tab。
            self._return_focus = self._capture_return_focus()
            self._commands = build_index(window)
            self._results: List[Command] = []
            self.last_result: dict = {}

            root = QVBoxLayout(self)
            self.search_edit = QLineEdit(self)
            self.search_edit.setPlaceholderText(
                "搜索菜单命令 / 面板页签 / 能力 / 快捷键（例如：设置、全天、FT8、Ctrl+K）")
            self.search_edit.textChanged.connect(self._refresh)
            self.search_edit.returnPressed.connect(self._on_return_pressed)
            self.search_edit.installEventFilter(self)
            root.addWidget(self.search_edit)

            self.list_widget = QListWidget(self)
            self.list_widget.itemActivated.connect(self._on_item_activated)
            self.list_widget.itemClicked.connect(self._on_item_clicked)
            root.addWidget(self.list_widget, stretch=1)

            self.reason_label = QLabel(self)
            self.reason_label.setWordWrap(True)
            root.addWidget(self.reason_label)

            self._refresh("")
            self.search_edit.setFocus()

        # ---------------- 焦点返还 ----------------
        def _capture_return_focus(self):
            """记下打开面板时的焦点控件；取不到就交给主窗的兜底落点。"""
            from PySide6.QtWidgets import QApplication
            cur = QApplication.focusWidget()
            if cur is None or cur is self or self.isAncestorOf(cur):
                cur = self._window.focusWidget() if self._window is not None else None
            if cur is None or cur is self or self.isAncestorOf(cur):
                return None
            return cur

        @staticmethod
        def _still_usable(widget) -> bool:
            """控件还在、可见、可用（面板开着时设置页可能已重建）。"""
            try:
                import shiboken6
                if not shiboken6.isValid(widget):
                    return False
            except ImportError:      # 没有 shiboken 时退回 Qt 侧判断
                pass
            try:
                return widget.isVisible() and widget.isEnabled()
            except RuntimeError:     # C++ 对象已销毁
                return False

        def _restore_return_focus(self) -> None:
            """把焦点还回打开前那颗控件；它没了就落到主窗确定的第一颗。"""
            target, self._return_focus = self._return_focus, None
            if target is not None and self._still_usable(target):
                # 非模态窗口关闭后系统不一定把主窗重新激活（实测 offscreen
                # 平台就停在「无活动窗口」），所以这里显式把目标所在窗口拉回
                # 前台，再落焦点。
                try:
                    target.window().activateWindow()
                except RuntimeError:     # 目标窗口已销毁
                    return
                target.setFocus(Qt.OtherFocusReason)
                return
            if self._window is None:
                return
            try:
                self._window.activateWindow()
                fallback = _first_focusable(self._window)
            except RuntimeError:     # 主窗已销毁
                return
            if fallback is not None:
                fallback.setFocus(Qt.OtherFocusReason)

        # ---------------- 列表 ----------------
        def _refresh(self, text: str) -> None:
            self._results = search_commands(self._commands, text,
                                            recent=load_recent())
            self.list_widget.clear()
            self.reason_label.clear()
            for cmd in self._results:
                item = QListWidgetItem(self._item_text(cmd))
                item.setData(Qt.UserRole, cmd)
                if cmd.status == "unavailable":
                    item.setForeground(Qt.gray)
                elif cmd.status == "conditional":
                    item.setForeground(Qt.darkYellow)
                self.list_widget.addItem(item)
            if self._results:
                self.list_widget.setCurrentRow(0)
                if not (text or "").strip():
                    self.reason_label.setText(
                        "可执行动作（最近使用优先）：回车执行首条，"
                        "↑↓ 选择，输入关键词过滤，Esc 关闭。")
                return
            self.reason_label.setText(self._no_match_hint(text))

        def _no_match_hint(self, text: str) -> str:
            """没匹配也给下一步：拿索引里真实存在的条目举例，不写死样例。"""
            recent = set(load_recent())
            ranked = sorted(self._commands,
                            key=lambda c: (c.source not in recent,))
            samples: List[str] = []
            for cmd in ranked:
                if cmd.available and cmd.title not in samples:
                    samples.append(cmd.title)
                if len(samples) >= 3:
                    break
            tried = " ".join(f"「{t}」" for t in samples)
            head = (f"没有匹配「{(text or '').strip()}」。"
                    if (text or "").strip() else "当前没有可执行的入口。")
            return (f"{head}试试：{tried}；按 ↑↓ 选择，Enter 执行，Esc 关闭。")

        @staticmethod
        def _item_text(cmd: Command) -> str:
            head = f"[{cmd.group}] {cmd.title}"
            if cmd.trigger:
                head += f"  ·  {cmd.trigger}"
            head += _STATUS_SUFFIX.get(cmd.status, "")
            if cmd.status == "unavailable" and cmd.reason:
                head += f"  —  {cmd.reason}"
            return head

        # ---------------- 执行 ----------------
        def run_command(self, cmd: Command) -> dict:
            """执行一条命令；只有成功才关闭面板，失败必须留在原地给原因。"""
            if cmd.run is None or cmd.status == "unavailable":
                result = {"ok": False, "status": "UNAVAILABLE",
                          "reason": cmd.reason or f"「{cmd.title}」当前不可用"}
                self.reason_label.setText(result["reason"])
                self.last_result = result
                return result
            result = cmd.run()
            self.last_result = result
            if result.get("ok"):
                remember_recent(cmd.source)
                self.hide()
                # 命令本身可能把焦点挪走（如「设置」会 tab.setFocus()），
                # 关闭后要还回用户原来所在的位置。
                self._restore_return_focus()
            else:
                self.reason_label.setText(
                    str(result.get("reason") or f"「{cmd.title}」执行失败"))
            return result

        def eventFilter(self, obj, event):
            """↓ 从搜索框进入结果列表（回车仍由 returnPressed 负责）。"""
            if obj is self.search_edit and event.type() == QEvent.KeyPress:
                if event.key() == Qt.Key_Down and self.list_widget.count():
                    self.list_widget.setCurrentRow(0)
                    self.list_widget.setFocus()
                    return True
            return super().eventFilter(obj, event)

        def _on_return_pressed(self) -> None:
            """回车直接执行当前（默认第一条）结果，不必再 Tab 进列表。"""
            item = self.list_widget.currentItem()
            if item is None:
                self.reason_label.setText(
                    "没有可执行的条目：先输入关键词，或在列表里选中一行。")
                return
            self.run_command(item.data(Qt.UserRole))

        def run_first(self, query: str) -> dict:
            """测试/自动化接缝：按关键词取第一条并执行。"""
            self.search_edit.setText(query)
            if not self._results:
                result = {"ok": False, "status": "EMPTY",
                          "reason": f"没有匹配「{query}」的入口"}
                self.last_result = result
                return result
            return self.run_command(self._results[0])

        def _on_item_activated(self, item: QListWidgetItem) -> None:
            self.run_command(item.data(Qt.UserRole))

        def _on_item_clicked(self, item: QListWidgetItem) -> None:
            cmd = item.data(Qt.UserRole)
            if cmd.status in ("unavailable", "conditional") and cmd.reason:
                self.reason_label.setText(cmd.reason)

        def visible_count(self) -> int:
            return self.list_widget.count()

        def visible_commands(self) -> List[Command]:
            return list(self._results)

    return CommandPalette


def __getattr__(name: str):
    """PEP 562：取 ``CommandPalette`` 时才构造 Qt 类，数据层测试不必起 Qt。"""
    if name == "CommandPalette":
        return _build_dialog_class()
    raise AttributeError(name)

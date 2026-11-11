# SPDX-License-Identifier: MIT
"""命令/QAction 重复注册检查
==========================

同一命令可以有多个「入口」（菜单项、快捷键登记、面板菜单勾选项），这是正常的；
真正的问题是**同一命令被多个 QAction 实例驱动**——状态（启用/勾选）容易不同步，
用户会看到一处置灰另一处还能点。

本脚本实例化真实主窗，按命令标识聚合对象图：

* ``shortcut:<id>`` —— QAction 上的 ``mbdsdr_shortcut_id`` 属性 / ``_shortcut_actions``
* ``panel:<id>``    —— ``panel_registry`` 的 id（经 ``_panel_actions`` 反查）
* ``menu:<路径>``   —— 未绑定上述两类标识的菜单项，自身即命令

输出两栏：**同一实例多入口（正常）** 与 **多个实例驱动同一命令（真重复）**，
并附带多入口动作的 ``objectName`` 欠账清单。真重复存在时返回 1。

用法::

    python scripts/check_action_duplication.py            # 真机对象图盘点
    python scripts/check_action_duplication.py --json     # 机器可读
    python scripts/check_action_duplication.py --self-test  # 反证：注入/移除假重复
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@dataclass(frozen=True)
class Entry:
    """一条「某命令可以从这里触达」的入口记录。"""
    key: str
    action: object
    path: str


@dataclass
class Group:
    """同一命令标识下的全部入口。"""
    key: str
    entries: List[Entry] = field(default_factory=list)

    @property
    def instances(self) -> List[object]:
        seen: Dict[int, object] = {}
        for e in self.entries:
            seen.setdefault(id(e.action), e.action)
        return list(seen.values())

    @property
    def enabled_sync(self) -> bool:
        return len({bool(a.isEnabled()) for a in self.instances}) <= 1


def _shortcut_id(action) -> str:
    sid = action.property("mbdsdr_shortcut_id")
    return str(sid) if sid else ""


def _menu_walk(menu, prefix: List[str], out: List[Entry]) -> None:
    for act in menu.actions():
        if act.isSeparator():
            continue
        sub = act.menu()
        label = (act.text() or "").replace("&", "").replace("...", "").strip()
        if sub is not None:
            _menu_walk(sub, prefix + [label], out)
            continue
        out.append(Entry(key="", action=act, path="/".join(prefix + [label])))


def collect_entries(window) -> List[Entry]:
    """把主窗里所有 QAction 入口摊平成 Entry（key 在 aggregate 里补）。"""
    raw: List[Entry] = []
    for top in window.menuBar().actions():
        sub = top.menu()
        if sub is None:
            continue
        _menu_walk(sub, [(top.text() or "").replace("&", "").strip()], raw)

    # 未挂进菜单的快捷键动作（例如只在 QShortcut 表里登记的）也要算入口
    for sid, act in getattr(window, "_shortcut_actions", {}).items():
        raw.append(Entry(key="", action=act, path=f"shortcut-registry/{sid}"))

    # 面板菜单：registry id → QAction
    panel_actions = getattr(window, "_panel_actions", {})
    panel_rev = {id(a): pid for pid, a in panel_actions.items()}
    shortcut_rev = {id(a): sid for sid, a in
                    getattr(window, "_shortcut_actions", {}).items()}

    out: List[Entry] = []
    for e in raw:
        sid = _shortcut_id(e.action)
        if sid:
            key = f"shortcut:{sid}"
        elif id(e.action) in panel_rev:
            key = f"panel:{panel_rev[id(e.action)]}"
        elif id(e.action) in shortcut_rev:
            key = f"shortcut:{shortcut_rev[id(e.action)]}"
        else:
            key = f"menu:{e.path}"
        out.append(Entry(key=key, action=e.action, path=e.path))
    return out


def aggregate(entries: Sequence[Entry]) -> Dict[str, Group]:
    groups: Dict[str, Group] = {}
    for e in entries:
        groups.setdefault(e.key, Group(key=e.key)).entries.append(e)
    return groups


def classify(groups: Dict[str, Group]):
    """返回 (单实例多入口, 多实例真重复, 单实例单入口)。"""
    paired: List[Group] = []
    duplicated: List[Group] = []
    singles: List[Group] = []
    for g in groups.values():
        n = len(g.instances)
        if n > 1:
            duplicated.append(g)
        elif len(g.entries) > 1:
            paired.append(g)
        else:
            singles.append(g)
    paired.sort(key=lambda g: g.key)
    duplicated.sort(key=lambda g: g.key)
    return paired, duplicated, singles


def _label(action) -> str:
    return (action.text() or "").replace("&", "").replace("...", "").strip()


def same_label(groups: Dict[str, Group]) -> Dict[str, List[str]]:
    """同名不同标识：疑似「同一功能两套注册」（如 视图/星象仪 vs 面板/rf_sky）。"""
    by_label: Dict[str, set] = {}
    for g in groups.values():
        for act in g.instances:
            lab = _label(act)
            if lab:
                by_label.setdefault(lab, set()).add(g.key)
    return {lab: sorted(keys) for lab, keys in sorted(by_label.items())
            if len(keys) > 1}


def _fmt_instance(action) -> str:
    return (f"objectName={action.objectName()!r} "
            f"enabled={action.isEnabled()}")


def report(paired: List[Group], duplicated: List[Group],
           singles: List[Group], empty_names: List[Group],
           label_conflicts: Dict[str, List[str]]) -> str:
    lines: List[str] = []
    lines.append(f"入口总数 {sum(len(g.entries) for g in paired + duplicated + singles)}，"
                 f"命令 {len(paired) + len(duplicated) + len(singles)} 个")
    lines.append("")
    lines.append(f"== 同一实例多入口（正常配对）: {len(paired)} ==")
    for g in paired:
        names = {a.objectName() for a in g.instances}
        lines.append(f"- {g.key}  入口 {len(g.entries)} 个  "
                     f"objectName={sorted(names)!r}  "
                     f"路径: {[e.path for e in g.entries]}")
    lines.append("")
    lines.append(f"== 多个实例驱动同一命令（真重复）: {len(duplicated)} ==")
    for g in duplicated:
        sync = "一致" if g.enabled_sync else "不同步"
        lines.append(f"- {g.key}  实例 {len(g.instances)} 个  启用状态: {sync}")
        for a in g.instances:
            paths = [e.path for e in g.entries if e.action is a]
            lines.append(f"    {_fmt_instance(a)}  入口: {paths}")
    lines.append("")
    lines.append(f"== 多入口但 objectName 为空（可维护性欠账）: {len(empty_names)} ==")
    for g in empty_names:
        lines.append(f"- {g.key}  {[e.path for e in g.entries]}")
    lines.append("")
    lines.append(f"== 同名但命令标识不同（疑似同一功能两套注册）: "
                 f"{len(label_conflicts)} ==")
    for lab, keys in label_conflicts.items():
        lines.append(f"- {lab}: {keys}")
    lines.append("")
    lines.append(f"（单实例单入口命令 {len(singles)} 个，未展开）")
    return "\n".join(lines)


def _bootstrap_qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # 真实用户配置零写入：连同日志/布局都指到临时目录
    tmp = tempfile.mkdtemp(prefix="mbdsdr_action_dup_")
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[k] = tmp
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


def _self_test() -> int:
    """反证：在真实菜单里造「两实例同一命令」，必须报出；移除后必须消失。"""
    app = _bootstrap_qt()
    from PySide6.QtGui import QAction
    from main_window import MainWindow

    window = MainWindow()
    menus = [a.menu() for a in window.menuBar().actions() if a.menu() is not None]
    menu_a, menu_b = menus[0], menus[1]

    # ① 同一命令、两个 QAction 实例（真重复，且状态不同步）挂进真实菜单
    a1 = QAction("假重复命令", window)
    a1.setProperty("mbdsdr_shortcut_id", "fake.duplicate")
    a1.setObjectName("fake_dup_1")
    a2 = QAction("假重复命令", window)
    a2.setProperty("mbdsdr_shortcut_id", "fake.duplicate")
    a2.setObjectName("fake_dup_2")
    a2.setEnabled(False)
    # ② 同一实例挂两个菜单（正常配对，不该被误报为真重复）
    shared = QAction("假配对命令", window)
    shared.setProperty("mbdsdr_shortcut_id", "fake.paired")
    for m in (menu_a, menu_b):
        m.addAction(a1 if m is menu_a else a2)
        m.addAction(shared)

    paired, duplicated, _singles = classify(aggregate(collect_entries(window)))
    dup_key, pair_key = "shortcut:fake.duplicate", "shortcut:fake.paired"
    dup_keys = [g.key for g in duplicated]
    paired_keys = [g.key for g in paired]
    ok_dup = dup_key in dup_keys
    ok_pair = pair_key in paired_keys
    ok_sync = any(g.key == dup_key and g.enabled_sync is False
                  for g in duplicated)
    text_before = report(paired, duplicated, [], [], {})
    ok_render = dup_key in text_before.split("真重复")[1]

    for m in (menu_a, menu_b):
        m.removeAction(a1)
        m.removeAction(a2)
        m.removeAction(shared)
    _p2, dup2, _s2 = classify(aggregate(collect_entries(window)))
    gone = dup_key not in [g.key for g in dup2]

    print("== 反证（真实菜单注入/移除）==")
    print(f"注入后：真重复列表含 fake.duplicate -> {ok_dup}（{dup_keys}）")
    print(f"注入后：同实例双入口归入正常配对 -> {ok_pair}（{paired_keys}）")
    print(f"注入后：两实例启用状态判为不同步 -> {ok_sync}")
    print(f"注入后：报告「真重复」栏出现 fake.duplicate -> {ok_render}")
    print(f"移除后：fake.duplicate 从真重复列表消失 -> {gone}")
    ok = ok_dup and ok_pair and ok_sync and ok_render and gone
    print("SELFTEST", "PASS" if ok else "FAIL")
    window.close()
    app.quit()
    return 0 if ok else 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="命令/QAction 重复注册检查")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--self-test", action="store_true",
                        help="反证：注入假重复，必须被报出；移除后消失")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    app = _bootstrap_qt()
    from main_window import MainWindow

    window = MainWindow()
    try:
        entries = collect_entries(window)
        groups = aggregate(entries)
        paired, duplicated, singles = classify(groups)
        empty_names = [g for g in paired + duplicated
                       if any(not a.objectName() for a in g.instances)]
        label_conflicts = same_label(groups)

        # panel_registry 有声明、面板菜单里却没有 QAction 的 id
        from panel_registry import registry
        missing = [s.id for s in registry().list()
                   if s.closable and s.id not in getattr(window, "_panel_actions", {})]

        if args.json:
            print(json.dumps({
                "paired": [{"key": g.key,
                            "entries": [e.path for e in g.entries],
                            "objectNames": sorted({a.objectName() for a in g.instances})}
                           for g in paired],
                "duplicates": [{"key": g.key,
                                "instances": [
                                    {"objectName": a.objectName(),
                                     "enabled": a.isEnabled(),
                                     "entries": [e.path for e in g.entries
                                                 if e.action is a]}
                                    for a in g.instances]}
                               for g in duplicated],
                "empty_object_name": [g.key for g in empty_names],
                "same_label_different_key": label_conflicts,
                "panel_registry_without_action": missing,
            }, ensure_ascii=False, indent=2))
        else:
            print(report(paired, duplicated, singles, empty_names,
                         label_conflicts))
            if missing:
                print(f"\n== panel_registry 有声明但面板菜单无 QAction: "
                      f"{len(missing)} ==\n- " + "\n- ".join(missing))
    finally:
        window.close()

    if duplicated:
        print(f"\nFAIL: {len(duplicated)} 个命令由多个 QAction 实例驱动")
        app.quit()
        return 1
    print("\nOK: 没有多实例驱动的命令")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())

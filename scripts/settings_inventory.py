#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
设置 schema ↔ UI 双向一致性盘点（scripts/settings_inventory.py）
================================================================

为什么需要这个脚本：`desktop/config_schema.py` 是设置的唯一事实源，但
「schema 有字段」不等于「用户能改到」，「UI 读写某个键」也不等于「schema
认这个键」。两者漂移过一次（demod_mode 曾原样接受任意字符串），所以把
一致性做成脚本 + 文档表 + 测试守卫：

  * 正向：SCHEMA 每个字段必须有
      - 真实 UI 入口（设置面板按 schema 渲染的控件 / 服务面板控件 /
        内部键在 desktop/ 里的具体控件锚点），或
      - 明确理由（如仅迁移标记、无用户意图）。
  * 反向：desktop/*.py 里所有 `settings.get("k")` / `_settings["k"]` /
    `settings.set("k", ...)` 的**字面键**必须在 SCHEMA 声明；未声明即
    「幽灵键」（配置漂移的源头）。
  * 计数：字段数 / 有 UI 入口数 / 无 UI 但有理由数 / 缺失数 / 幽灵键数，
    由本脚本产出，写进 docs/settings-inventory.md 的标记块。
    手改块内数字 → `test_inventory_table_matches_script` 红。

用法::

    python scripts/settings_inventory.py            # 打印三张表
    python scripts/settings_inventory.py --write    # 更新文档块
    python scripts/settings_inventory.py --check    # 只比对，不一致返回 1

边界（刻意不做的事）：只扫 `settings` 语义的接收者（`settings` /
`self.settings` / `_settings`），不把 gui_config / ai_config 的普通 dict
（`cfg` / `config`）算进来——它们不是 DesktopSettings，不在本守卫范围。
`desktop/settings.py` 是存储实现本身（docstring 里有用法示例），也排除，
否则会把自己的说明文字当成消费点。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(ROOT, "desktop")
for _p in (ROOT, _DESKTOP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config_schema  # noqa: E402

DOC_PATH = os.path.join(ROOT, "docs", "settings-inventory.md")
BLOCK_BEGIN = "<!-- settings-guard:begin -->"
BLOCK_END = "<!-- settings-guard:end -->"

#: 反向扫描的接收者模式。要求前面不是标识符字符，从而命中
#: `settings.get(...)` / `self.settings.get(...)` / `self._settings.get(...)`。
_REVERSE_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("settings.get", re.compile(r'(?<![A-Za-z0-9_])settings\.get\(\s*"([a-z_]+)"')),
    ("settings.set", re.compile(r'(?<![A-Za-z0-9_])settings\.set\(\s*"([a-z_]+)"')),
    ("settings[...]", re.compile(r'(?<![A-Za-z0-9_])settings\[\s*"([a-z_]+)"\s*\]')),
    ("_settings.get", re.compile(r'_settings\.get\(\s*"([a-z_]+)"')),
    ("_settings.set", re.compile(r'_settings\.set\(\s*"([a-z_]+)"')),
    ("_settings[...]", re.compile(r'_settings\[\s*"([a-z_]+)"\s*\]')),
)

#: 反向扫描跳过的文件：事实源本身 + 存储实现（其 docstring 含用法示例）。
_REVERSE_SKIP = {"config_schema.py", "settings.py"}

#: 内部键（exposed=False）的真实控件锚点：file → 锚点字面量。
#: 锚点必须能在文件里原样找到；删掉/改名控件 → 守卫红（这就是漂移检测）。
INTERNAL_UI_ANCHORS: Dict[str, Tuple[str, str]] = {
    "frequency_hz": ("desktop/control_panel.py", "self.freq_input = QLineEdit"),
    "gain_db": ("desktop/control_panel.py", "self.gain_slider = QSlider"),
    "demod_mode": ("desktop/control_panel.py", "self.mode_combo = QComboBox"),
    "bandwidth_hz": ("desktop/control_panel.py", "self.vfo_bw_combo = QComboBox"),
    "sample_rate_hz": ("desktop/control_panel.py", "self.sample_rate_combo = _CapabilitySampleRateCombo"),
    "callsign": ("desktop/main_window.py", "self.callsign_edit = QLineEdit()"),
    "gnss_port": ("desktop/main_window.py", "port_edit = QLineEdit(self._gnss_port)"),
    "gnss_baudrate": ("desktop/main_window.py", "baud_combo.addItem(str(b), b)"),
    "agc_enabled": ("desktop/control_panel.py", 'self.agc_check = QCheckBox("硬件 AGC")'),
    "agc_speed": ("desktop/control_panel.py", "self.agc_speed_combo = QComboBox()"),
    "volume": ("desktop/control_panel.py", "self.volume_slider = QSlider"),
    "app_mode": ("desktop/main_window.py", 'planetarium_action = QAction("星象仪模式", self)'),
}

#: 无 UI 入口但有明确理由的字段（理由必须是"消费点不存在"或"无用户意图"，
#: 不许写"以后再说"）。理由会原样出现在盘点表里。
INTERNAL_REASONS: Dict[str, str] = {
    "volume_scale_v2": (
        "旧 volume 量纲（0–63 硬件寄存器）一次性迁移标记；只由 "
        "settings.py:load 在旧文件缺少该键时写入，用户没有可表达的意图"),
    "record_dir_inside_program_ack": (
        "录制目录落在程序目录内的一次性确认标记；只由 "
        "main_window.py:8290 的运行时对话框在用户点「仍用当前目录」时写入，"
        "不是可在设置里表达的长期偏好，故无设置控件；用户入口是该对话框本身"),
    "settings_expanded_groups": (
        "设置页 6 个用途分组的手风琴展开态，持久化的是「上次展开了哪几组」这一"
        "界面状态，不是可枚举的长期偏好：用户的表达方式就是点分组标题本身，"
        "再给一个设置项会形成两个真相源，且没有任何用户意图需要通过它表达。"
        "写入点 settings_panel.py:_persist_expanded_state，读取点 "
        "_restore_expanded_state（坏值/未知标题一律回落「只展开首组」）"),
    "right_column_expanded_section": (
        "右栏 20 节手风琴当前展开节的稳定 id（由 right_column.py 的 section_key "
        "生成），与 settings_expanded_groups 同类：这是「上次看到哪一节」的界面"
        "状态记忆，用户的表达方式就是点节标题本身；把内部 id 做成设置页文本项"
        "既不可读也不该由人手填，故不暴露。读取点 right_column.py:_restore_expanded"
        "（坏值/未知 id 回落只展开首节），本键只负责持久化"),
}

#: 服务页签（独立 ServiceSettingsPanel）里的键 → 控件属性名。
SERVICE_WIDGETS: Dict[str, str] = {
    "rc_enabled": "rc_check",
    "rc_port": "rc_port",
    "web_enabled": "web_check",
    "web_port": "web_port",
}

#: 设置面板把每个暴露项登记进 `_rows[item.key] = control`——正向的渲染证据。
_PANEL_ROW_ANCHOR = "self._rows[item.key] = control"
_PANEL_FILE = "desktop/settings_panel.py"


@dataclass
class UiEvidence:
    kind: str          # panel | service | control | reason
    location: str      # 人读位置（file:line 或理由）
    detail: str = ""


@dataclass
class ReverseHit:
    key: str
    path: str
    line: int
    access: str
    in_schema: bool


@dataclass
class Report:
    fields: int = 0
    ui_entries: int = 0
    reasons: int = 0
    missing: List[str] = field(default_factory=list)
    ghosts: List[ReverseHit] = field(default_factory=list)
    forward_rows: List[Tuple[str, str, str]] = field(default_factory=list)
    reverse_rows: List[ReverseHit] = field(default_factory=list)
    source_commit: str = "unknown"


def desktop_sources() -> Dict[str, str]:
    """relpath -> 源码文本（只取 desktop/*.py）。"""
    out: Dict[str, str] = {}
    for name in sorted(os.listdir(_DESKTOP)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(_DESKTOP, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                out[f"desktop/{name}"] = f.read()
        except OSError:
            continue
    return out


def _line_of(text: str, needle: str) -> Optional[int]:
    pos = text.find(needle)
    if pos < 0:
        return None
    return text.count("\n", 0, pos) + 1


def source_commit() -> str:
    """schema 事实源的最近一次提交；取不到（无 git）时记 unknown。"""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h", "--", "desktop/config_schema.py"],
            cwd=ROOT, capture_output=True, text=True, timeout=10, check=False)
        value = (out.stdout or "").strip()
        return value or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def ui_evidence(item, sources: Dict[str, str]) -> Optional[UiEvidence]:
    """给一个 schema 字段找 UI 入口或理由；找不到返回 None（=缺失，红）。"""
    panel_src = sources.get(_PANEL_FILE, "")
    if item.exposed:
        if item.group == "服务与远程访问":
            attr = SERVICE_WIDGETS.get(item.key)
            if attr is None:
                return None
            widget = f"self.{attr} = "
            line = _line_of(panel_src, widget)
            if line is None or f'"{item.key}"' not in panel_src:
                return None
            return UiEvidence(
                "service",
                f"{_PANEL_FILE}:{line}（ServiceSettingsPanel.{attr}，键 {item.key}）",
                "独立「服务设置」页签")
        if item.group not in config_schema.GROUP_ORDER:
            return None
        line = _line_of(panel_src, _PANEL_ROW_ANCHOR)
        if line is None:
            return None
        return UiEvidence(
            "panel",
            f"{_PANEL_FILE}:{line}（_rows[{item.key!r}]，分组「{item.group}」）",
            "设置面板按 schema 渲染")

    anchor = INTERNAL_UI_ANCHORS.get(item.key)
    if anchor is not None:
        relpath, needle = anchor
        text = sources.get(relpath, "")
        line = _line_of(text, needle)
        if line is None:
            return None                      # 锚点没了 = 控件被删/改名，红
        return UiEvidence(
            "control", f"{relpath}:{line}（{needle}）", "内部键，控件在既有面板")

    reason = INTERNAL_REASONS.get(item.key)
    if reason is not None:
        return UiEvidence("reason", reason, "无 UI 入口，有明确理由")
    return None


def reverse_scan(sources: Dict[str, str]) -> List[ReverseHit]:
    """扫 desktop/ 里 settings 的字面键读写；返回逐条命中（带是否在 schema）。"""
    hits: List[ReverseHit] = []
    for relpath, text in sources.items():
        if os.path.basename(relpath) in _REVERSE_SKIP:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for tag, rx in _REVERSE_PATTERNS:
                m = rx.search(line)
                if not m:
                    continue
                key = m.group(1)
                hits.append(ReverseHit(key, relpath, lineno, tag,
                                       key in config_schema.SCHEMA))
    return hits


def measure() -> Report:
    """完整盘点：正向覆盖 + 反向幽灵键 + 计数。"""
    rep = Report(source_commit=source_commit())
    sources = desktop_sources()
    for item in config_schema.ALL_ITEMS:
        rep.fields += 1
        ev = ui_evidence(item, sources)
        if ev is None:
            rep.missing.append(item.key)
            rep.forward_rows.append((item.key, "（缺失）", "无 UI 入口且无理由"))
            continue
        if ev.kind == "reason":
            rep.reasons += 1
        else:
            rep.ui_entries += 1
        rep.forward_rows.append((item.key, ev.location, ev.detail))

    rep.reverse_rows = reverse_scan(sources)
    rep.ghosts = [h for h in rep.reverse_rows if not h.in_schema]
    return rep


def render_forward_table(rep: Report) -> str:
    lines = ["| 字段 | UI 入口 / 理由 | 说明 |", "|---|---|---|"]
    lines += [f"| `{k}` | {loc} | {detail} |" for k, loc, detail in rep.forward_rows]
    return "\n".join(lines)


def render_reverse_table(rep: Report) -> str:
    lines = ["| 键 | 位置 | 访问 | 在 schema |", "|---|---|---|---|"]
    for h in rep.reverse_rows:
        lines.append(
            f"| `{h.key}` | `{h.path}:{h.line}` | {h.access} | "
            f"{'是' if h.in_schema else '**否（幽灵键）**'} |")
    return "\n".join(lines)


def render_block(rep: Report) -> str:
    """文档里唯一的「数字表」块；测试与 --check 都按这段整块比对。"""
    return "\n".join([
        BLOCK_BEGIN,
        "",
        "| 指标 | 数值 | 口径 |",
        "|---|---|---|",
        f"| schema 字段数 | **{rep.fields}** | `config_schema.ALL_ITEMS` 全量 |",
        f"| 有 UI 入口 | **{rep.ui_entries}** | 设置面板渲染 + 服务页签控件 + 内部键控件锚点 |",
        f"| 无 UI 但有明确理由 | **{rep.reasons}** | 理由见「字段 → 位置」表的「说明」列 |",
        f"| 缺失（无入口且无理由） | **{len(rep.missing)}** | >0 即守卫红 |",
        f"| 幽灵键（未在 schema 声明） | **{len(rep.ghosts)}** | 扫描 desktop/*.py 的 settings 字面键 |",
        f"| source_commit | `{rep.source_commit}` | `git log -1 --format=%h -- desktop/config_schema.py` |",
        "",
        BLOCK_END,
    ])


def extract_block(doc_text: str) -> Optional[str]:
    start = doc_text.find(BLOCK_BEGIN)
    end = doc_text.find(BLOCK_END)
    if start < 0 or end < 0 or end < start:
        return None
    return doc_text[start:end + len(BLOCK_END)]


def write_doc(rep: Report) -> None:
    """把生成的块写回文档；标记缺失时在文末补一节。"""
    with open(DOC_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    block = render_block(rep)
    current = extract_block(text)
    if current is None:
        section = (
            "\n## 17. schema ↔ UI 双向一致性守卫（脚本生成，手改即红）\n\n"
            "`tests/test_settings_ui_coverage.py` 钉住：正向（每个字段有 UI 入口或"
            "明确理由）、反向（desktop/ 的 settings 字面键都在 schema）、往返"
            "（每字段 `set → flush → load`，且归一说明留痕）。下表由\n"
            "`python scripts/settings_inventory.py --write` 生成；手改块内数字会被\n"
            "`test_inventory_table_matches_script` 判红。\n\n"
            + block + "\n")
        text = text.rstrip("\n") + "\n" + section
    else:
        text = text.replace(current, block)
    with open(DOC_PATH, "w", encoding="utf-8") as f:
        f.write(text)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="设置 schema ↔ UI 一致性盘点")
    parser.add_argument("--write", action="store_true", help="更新文档块")
    parser.add_argument("--check", action="store_true", help="只比对文档块，不一致返回 1")
    args = parser.parse_args(argv)

    rep = measure()
    if args.write:
        write_doc(rep)
        print(f"已写入 {os.path.relpath(DOC_PATH, ROOT)}")
        return 0
    if args.check:
        with open(DOC_PATH, "r", encoding="utf-8") as f:
            current = extract_block(f.read())
        expected = render_block(rep)
        if current != expected:
            print("文档块与脚本生成结果不一致，请运行 --write", file=sys.stderr)
            return 1
        print("文档块一致")
        return 0

    print("== 正向：字段 → UI 入口 ==")
    print(render_forward_table(rep))
    print()
    print("== 反向：键 → 位置 → 是否在 schema ==")
    print(render_reverse_table(rep))
    print()
    print(f"字段 {rep.fields} / 有 UI 入口 {rep.ui_entries} / "
          f"有理由 {rep.reasons} / 缺失 {len(rep.missing)} / "
          f"幽灵键 {len(rep.ghosts)} / source_commit {rep.source_commit}")
    return 1 if (rep.missing or rep.ghosts) else 0


if __name__ == "__main__":
    sys.exit(main())

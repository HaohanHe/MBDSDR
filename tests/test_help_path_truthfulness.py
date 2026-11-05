# SPDX-License-Identifier: MIT
"""
帮助条目的界面路径 ↔ 源码字面量守门
====================================

帮助文案里写了「设置 → 存储与日志」「顶栏点连接」这类路径。控件一改名、
面板一搬家，文案就变成假话，而用户照着做会一直找不到。

这里把每个「」里的界面词钉到真正实现它的源码文件：
  * 词必须在映射文件里作为字面量出现；
  * `→` 链的每一段单独成立（一段找不到就红）；
  * 「设置 → X」的 X 必须真的是 ``config_schema.GROUP_ORDER`` 里的分组；
  * 文案里新增了没登记的词 → 直接红，逼作者补登记（防止假话悄悄进来）。

只读源码文本，不启动 Qt、不改 UI。
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import config_schema  # noqa: E402
import troubleshooting as ts  # noqa: E402

_DESKTOP = os.path.join(_ROOT, "desktop")

#: 帮助文案里的界面词 → 必须含该字面量的源码文件（可多个，任一命中即可）。
#: 新增文案词没登记会红——登记即声明「用户在哪儿能看到它」。
SOURCE_OF = {
    "连接": ("main_window.py",),              # 工具栏按钮
    "连接设备...": ("main_window.py",),       # 文件菜单项
    "文件": ("main_window.py",),
    "控制": ("panel_registry.py",),
    "AI 助手": ("panel_registry.py",),
    "状态": ("panel_registry.py",),
    "频谱": ("panel_registry.py",),
    "设备": ("main_window.py",),              # 右侧停靠页签
    "设置": ("main_window.py",),
    "存储与日志": ("config_schema.py",),
    "频谱与解码": ("config_schema.py",),
    "AI": ("config_schema.py",),
    "录音目录": ("config_schema.py",),
    "调谐步进": ("config_schema.py",),
    "请求超时（秒）": ("config_schema.py",),
    "音频输出": ("device_panel.py",),
    "输出设备": ("device_panel.py",),
    "系统默认": ("device_panel.py",),
    "开始/停止录音": ("main_window.py",),
    "更新卫星 TLE": ("main_window.py",),
    "远程 rtl_tcp 源": ("main_window.py",),
    "能力目录": ("help_panel.py",),
    "解码结果": ("status_panel.py",),
    "静噪门限": ("control_panel.py",),
    "采样率": ("control_panel.py",),
    "自动": ("control_panel.py",),
    "频率 −1 格": ("shortcuts.py",),
    "配置": ("ai_panel.py",),
}


def _help_texts():
    """所有用户可见的帮助条目：15 条精选 + 9 族共享下一步。"""
    texts = dict(ts.NEXT_STEPS)
    texts.update({"族:" + k: v for k, v in ts.VISIBLE_NEXT_STEPS.items()})
    return texts


def _ui_terms():
    """(条目 key, 界面词) —— 界面词即「」里的原文。"""
    for key, text in _help_texts().items():
        for token in re.findall(r"「([^」]+)」", text):
            yield key, token


def _segments(token):
    """`音频输出 → 输出设备` / `设置 → 存储与日志` → 每段单独校验。"""
    return [s.strip() for s in token.split("→") if s.strip()]


def _source(name):
    with open(os.path.join(_DESKTOP, name), encoding="utf-8") as f:
        return f.read()


def _is_ui_literal(seg, text):
    """界面词必须作为**引号里的字符串字面量**出现，注释里的同名不算。"""
    return re.search(r"[\"'][^\"'\n]*" + re.escape(seg) + r"[^\"'\n]*[\"']",
                     text) is not None


def test_every_path_segment_is_a_real_ui_literal():
    """帮助里点名的每个控件/页签，都必须在对应源码里作为字面量存在。"""
    cache = {}
    problems = []
    for key, token in _ui_terms():
        for seg in _segments(token):
            files = SOURCE_OF.get(seg)
            if not files:
                problems.append(f"{key}: 界面词「{seg}」未登记来源文件")
                continue
            for name in files:
                if name not in cache:
                    cache[name] = _source(name)
                if not _is_ui_literal(seg, cache[name]):
                    problems.append(
                        f"{key}: 「{seg}」在 {name} 里不是字符串字面量（文案路径已失真）")
    assert not problems, "\n".join(problems)


def test_settings_path_depth_matches_real_groups():
    """「设置」→「X」：X 必须是设置面板真实分页（GROUP_ORDER 成员）。"""
    groups = set(config_schema.GROUP_ORDER)
    problems = []
    for key, text in _help_texts().items():
        for group in re.findall(r"「设置」\s*→\s*「([^」]+)」", text):
            if group not in groups:
                problems.append(f"{key}: 设置里没有「{group}」这一页")
    assert not problems, "\n".join(problems)

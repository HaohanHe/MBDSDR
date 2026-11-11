# SPDX-License-Identifier: MIT
"""themes 双实例回归（运行时多真值源）
=====================================

缺陷：``themes``（main.py / main_window.py 的裸导入）与 ``desktop.themes``
（测试与工具的包式导入）会各执行一遍模块，``_FONT_SCALE_OVERRIDE`` /
``_FONT_SCALE_NOTE`` 分裂成两份——**写 A 读 B，字体降档提示永不显示**。

这里用**子进程**分别固定两种导入顺序，断言：
  1. 两个名字指向同一个模块对象；
  2. 一边写（override / 降档说明），另一边读得到；
  3. 失败信息里带 ``same object:`` 行，能区分「不是同一对象」与「同一对象但
     变量仍为空」。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_themes_single_instance.py -q
"""
import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ORDERS = {
    # 生产入口的顺序：desktop 目录在 sys.path，main_window.py 里是 `import themes`
    "bare_first": """
        import sys
        sys.path[:0] = [{root!r}, {desktop!r}]
        import themes
        import desktop.themes
        print("same object:", themes is desktop.themes)
        # 写 A 读 B：主窗口那条线写降档说明，设置页那条线读。
        themes._set_font_scale_note(1.5, 1.25)      # 唯一写入点
        themes.set_font_scale_override(1.25)
        print("note:", desktop.themes.font_scale_note())
        print("override:", desktop.themes.font_scale())
    """,
    # 测试/工具的顺序：仓库根在 sys.path，`import desktop.themes`
    "package_first": """
        import sys
        sys.path[:0] = [{root!r}, {desktop!r}]
        import desktop.themes
        import themes
        print("same object:", desktop.themes is themes)
        desktop.themes._set_font_scale_note(1.5, 1.25)
        desktop.themes.set_font_scale_override(1.25)
        print("note:", themes.font_scale_note())
        print("override:", themes.font_scale())
    """,
}

EXPECTED_NOTE = "当前屏幕不足以按 150% 渲染，已按 125% 显示。"


def _run(order: str) -> str:
    code = textwrap.dedent(_ORDERS[order]).format(
        root=ROOT, desktop=os.path.join(ROOT, "desktop"))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, f"子进程失败：\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


@pytest.mark.parametrize("order", sorted(_ORDERS))
def test_themes_is_one_module_and_note_is_visible(order):
    out = _run(order)
    assert "same object: True" in out, (
        f"两种导入顺序拿到了两个模块对象，降档提示会分裂：\n{out}")
    assert f"note: {EXPECTED_NOTE}" in out, (
        f"降档说明写了但另一边读不到（提示永不显示）：\n{out}")
    assert "override: 1.25" in out, (
        f"font_scale 的 override 没有互通：\n{out}")

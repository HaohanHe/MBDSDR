# SPDX-License-Identifier: MIT
"""5° 可见门单一来源：引擎 / 地面轨迹 / 覆盖圈地图 / 跟踪面板 / 主窗口跟踪环。

唯一事实源是 ``mbdsdr_ai.sat_passes.VISIBLE_MIN_ALT_DEG``；其余位置只许
import 这个名字，不许再写 5.0 / 6.0 之类的字面量。

反证：任意一处改回字面量（5.0 或 6.0），本文件的 AST 用例必红；把源常量
改成 6.0 后，子进程用例里三条消费者链必须同步变成 6.0，否则也红。
"""
import ast
import inspect
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mbdsdr_ai import sat_passes  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _tree(rel_path):
    with open(os.path.join(_ROOT, rel_path), encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=rel_path)


def _is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def test_ground_track_alias_is_imported_not_rewritten():
    from mbdsdr_ai import ground_track

    assert ground_track.DEFAULT_MIN_ELEVATION_DEG == \
        sat_passes.VISIBLE_MIN_ALT_DEG
    tree = _tree("mbdsdr_ai/ground_track.py")
    imports = [a.name for n in ast.walk(tree)
               if isinstance(n, ast.ImportFrom) and n.module == "sat_passes"
               for a in n.names]
    assert "VISIBLE_MIN_ALT_DEG" in imports, "ground_track 必须 import 单一来源"
    assigns = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "DEFAULT_MIN_ELEVATION_DEG"
                       for t in n.targets)]
    assert len(assigns) == 1, f"别名应只赋值一次，实际 {len(assigns)}"
    assert _is_name(assigns[0], "VISIBLE_MIN_ALT_DEG"), (
        f"DEFAULT_MIN_ELEVATION_DEG 被写成了 {ast.unparse(assigns[0])}，"
        "应为 VISIBLE_MIN_ALT_DEG")


def test_ground_map_default_is_the_constant():
    from sat_ground_map import SatelliteGroundMap

    default = inspect.signature(
        SatelliteGroundMap.update_satellite).parameters[
            "min_elevation_deg"].default
    assert default == sat_passes.VISIBLE_MIN_ALT_DEG

    tree = _tree("desktop/sat_ground_map.py")
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "update_satellite")
    pos = [a.arg for a in fn.args.args]
    idx = pos.index("min_elevation_deg")
    node = fn.args.defaults[idx - (len(pos) - len(fn.args.defaults))]
    assert _is_name(node, "VISIBLE_MIN_ALT_DEG"), (
        f"地图形参默认值被写成了 {ast.unparse(node)}，应为 VISIBLE_MIN_ALT_DEG")


def test_track_panel_spin_initial_value_is_the_constant(qapp, isolated_profile):
    from desktop.sat_track_panel import SatTrackPanel

    panel = SatTrackPanel()
    assert panel.ground_elev_spin.value() == sat_passes.VISIBLE_MIN_ALT_DEG

    tree = _tree("desktop/sat_track_panel.py")
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "setValue"
             and isinstance(n.func.value, ast.Attribute)
             and n.func.value.attr == "ground_elev_spin"]
    assert len(calls) == 1, f"ground_elev_spin.setValue 应只有一处，实际 {len(calls)}"
    assert _is_name(calls[0].args[0], "VISIBLE_MIN_ALT_DEG"), (
        f"控件初值被写成了 {ast.unparse(calls[0].args[0])}，"
        "应为 VISIBLE_MIN_ALT_DEG")


def test_main_window_tracking_loop_uses_the_constant():
    tree = _tree("desktop/main_window.py")
    compared_with_constant = False
    literal_gates = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = ast.unparse(node.left)
        if "elevation" not in left:
            continue
        for comp in node.comparators:
            if _is_name(comp, "VISIBLE_MIN_ALT_DEG"):
                compared_with_constant = True
            if isinstance(comp, ast.Constant) and comp.value in (5.0, 6.0):
                literal_gates.append(ast.unparse(node))
    assert compared_with_constant, "主窗口跟踪环必须用 VISIBLE_MIN_ALT_DEG 判可见"
    assert not literal_gates, f"跟踪环仍写死可见门：{literal_gates}"


def test_source_value_propagates_to_consumers():
    """把源常量改成 6.0 后，地面轨迹/地图/跟踪面板必须同步变成 6.0。"""
    code = (
        "import sys\n"
        "sys.path[:0] = ['.', 'desktop']\n"
        "import mbdsdr_ai.sat_passes as sp\n"
        "sp.VISIBLE_MIN_ALT_DEG = 6.0\n"
        "import inspect\n"
        "import mbdsdr_ai.ground_track as gt\n"
        "from sat_ground_map import SatelliteGroundMap\n"
        "from PySide6.QtWidgets import QApplication\n"
        "import sat_track_panel as stp\n"
        "app = QApplication([])\n"
        "panel = stp.SatTrackPanel()\n"
        "print(gt.DEFAULT_MIN_ELEVATION_DEG)\n"
        "print(inspect.signature(SatelliteGroundMap.update_satellite)"
        ".parameters['min_elevation_deg'].default)\n"
        "print(panel.ground_elev_spin.value())\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", code], cwd=_ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    values = [float(line) for line in proc.stdout.splitlines() if line.strip()]
    assert values == [6.0, 6.0, 6.0], (
        f"改源常量后三条消费者链应全为 6.0，实际 {values}；stderr={proc.stderr[-800:]}")

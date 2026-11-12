# SPDX-License-Identifier: MIT
"""
状态栏静噪读数：单位与来源契约
==============================

`squelch_db` 是**余量刻度**（-100≈0 dB、-50≈6 dB、-20≈16 dB），不是绝对
dBFS。旧实现把它渲染成 "-50 dBFS"，等于把两套语义混成一个数字；用户也无法
分辨「现在是桌面余量在生效，还是 C++ 内核在生效」。

契约：
  1. 只有 `squelch_threshold_dbfs`（真正下发的绝对门）才允许标 dBFS；
  2. desktop-margin → "-50（6 dB 余量，桌面生效）"；
  3. cpp-auto → "自动（底噪 +8 dB，C++ 内核）"；
  4. cpp-manual → 有回读门限才标 dBFS，否则明确「门限未回读」；
  5. none/未知来源 → "—"，不给数字戴任何单位帽子。
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _fmt():
    from status_panel import _fmt_squelch
    return _fmt_squelch


def test_desktop_margin_shows_margin_not_dbfs():
    f = _fmt()
    out = f(-50.0, "desktop-margin", None)
    assert out == "-50（6 dB 余量，桌面生效）", out
    assert "dBFS" not in out, f"余量刻度不许标 dBFS: {out}"


def test_desktop_margin_endpoints():
    f = _fmt()
    assert f(-20.0, "desktop-margin", None) == "-20（16 dB 余量，桌面生效）"
    assert f(-100.0, "desktop-margin", None) == "-100（0 dB 余量，桌面生效）"


def test_cpp_auto_and_manual():
    f = _fmt()
    auto = f(-50.0, "cpp-auto", None)
    assert "C++ 内核" in auto and "dBFS" not in auto, auto
    manual = f(-50.0, "cpp-manual", -42.0)
    assert "dBFS" in manual and "C++ 手动门" in manual, manual
    manual_unknown = f(-50.0, "cpp-manual", None)
    assert "dBFS" not in manual_unknown, manual_unknown


def test_no_source_never_gets_a_unit():
    f = _fmt()
    for applied_by in ("none", None, "", "foo"):
        assert f(-50.0, applied_by, None) == "—", applied_by
    assert f(None, "desktop-margin", None) == "—"
    assert f("bad", "desktop-margin", None) == "—"


def _owner_file():
    """按类名定位真正定义 ``SDRStatus`` 的源文件，不写死路径。

    历史：``bb7dda23`` 把 ``SDRStatus`` 从 ``mbdsdr_ai/sdr_backend.py`` 下沉到
    ``mbdsdr_ai/sdr_status.py``，守卫却继续 grep 旧文件——从迁移那天起它就没
    在检查任何东西。这里改成让 Python 自己回答「类定义在哪个文件」。
    """
    import inspect

    from mbdsdr_ai import sdr_backend

    path = inspect.getsourcefile(sdr_backend.SDRStatus)
    assert path and os.path.isfile(path), (
        f"按类名定位 SDRStatus 失败：{path!r}")
    return path


def _panel_margin_dbfs_problems(source: str):
    """margin 渲染处出现裸 dBFS 的问题清单（空列表 = 合规）。"""
    tree = ast.parse(source)
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "_fmt_squelch"), None)
    if fn is None:
        return ["status_panel 里找不到 _fmt_squelch"]
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)
               and n.value is not None]
    margin = [n for n in returns if "余量" in ast.unparse(n.value)]
    if not margin:
        return ["找不到 margin 渲染返回值，守卫已失效"]
    return [f":{n.lineno} margin 返回值带 dBFS：{ast.unparse(n.value)}"
            for n in margin if "dBFS" in ast.unparse(n.value)]


def _squelch_field_problems(source: str):
    """``SDRStatus.squelch_db`` 字段语义说明的问题清单（空列表 = 合规）。"""
    tree = ast.parse(source)
    lines = source.splitlines()
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef)
                and n.name == "SDRStatus"), None)
    if cls is None:
        return ["源码里找不到 class SDRStatus（可能又被迁移了，守卫目标需复核）"]
    ann = next((n for n in cls.body if isinstance(n, ast.AnnAssign)
                and getattr(n.target, "id", "") == "squelch_db"), None)
    if ann is None:
        return ["SDRStatus 里找不到 squelch_db（字段被改名/删除？）"]
    head = "\n".join(lines[max(0, ann.lineno - 8):ann.lineno])
    if "余量" not in head:
        return ["squelch_db 字段说明必须写明「余量」语义"]
    return []


def test_static_guard_no_bare_dbfs_on_margin():
    src = open(os.path.join(_ROOT, "desktop", "status_panel.py"),
               encoding="utf-8").read()
    assert "_fmt_squelch(" in src
    assert "squelch_applied_by" in src
    assert "squelch_threshold_dbfs" in src, (
        "DBFS 只允许来自 squelch_threshold_dbfs")
    assert _panel_margin_dbfs_problems(src) == [], (
        "margin 渲染处出现了裸 dBFS；余量刻度必须写「dB 余量」")

    owner = _owner_file()
    owner_src = open(owner, encoding="utf-8").read()
    assert "class SDRStatus" in owner_src, (
        f"按类名定位到的 {owner} 里没有 SDRStatus 定义")
    assert _squelch_field_problems(owner_src) == [], (
        "squelch_db 字段说明必须写明「余量」语义")
    # 反向锚：定义已不在旧文件里，守卫若还指向旧路径必然「查了个空」
    old = os.path.join(_ROOT, "mbdsdr_ai", "sdr_backend.py")
    assert os.path.normcase(old) != os.path.normcase(owner), (
        "SDRStatus 又回到 sdr_backend.py 了，解析规则需复核")


def test_guard_goes_red_when_margin_render_gains_dbfs():
    """反证：临时给 margin 返回值加一处裸 dBFS，守卫必须红。"""
    src = open(os.path.join(_ROOT, "desktop", "status_panel.py"),
               encoding="utf-8").read()
    bad = src.replace("dB 余量，桌面生效", "dBFS 余量，桌面生效")
    assert bad != src, "注入点不存在，反证失效"
    problems = _panel_margin_dbfs_problems(bad)
    assert problems, "margin 返回值被改成 dBFS 后守卫没红"


def test_guard_goes_red_on_bad_field_doc_and_rejects_old_module():
    """反证：字段说明去掉「余量」必须红；把旧文件当目标也必须红。"""
    owner_src = open(_owner_file(), encoding="utf-8").read()
    bad = owner_src.replace("语义固定为「余量刻度」", "语义固定为 dBFS 刻度")
    assert bad != owner_src, "注入点不存在，反证失效"
    assert _squelch_field_problems(bad), "字段说明丢失「余量」后守卫没红"

    old = os.path.join(_ROOT, "mbdsdr_ai", "sdr_backend.py")
    old_src = open(old, encoding="utf-8").read()
    assert "class SDRStatus" not in old_src, (
        "旧文件又开始定义 SDRStatus，需复核守卫目标")
    assert _squelch_field_problems(old_src), (
        "把旧路径当守卫目标时必须报错，不能静默通过")


def test_update_from_backend_uses_new_formatter(qapp):
    """后端刷新必须走来源感知的格式化：面板文本 == _fmt_squelch 的输出。

    行为断言：用假后端（只提供 get_status()）把六种来源/门限组合推给真实
    StatusPanel，逐一回读 rx_squelch 文本，必须与纯格式化函数的输出完全一致。
    若 update_from_backend 绕开该函数自己拼字符串，下面必有分支对不上。
    """
    from types import SimpleNamespace

    from status_panel import StatusPanel, _fmt_squelch

    panel = StatusPanel()
    try:
        cases = [
            (-50.0, "desktop-margin", None),
            (-20.0, "desktop-margin", None),
            (-50.0, "cpp-auto", None),
            (-50.0, "cpp-manual", -42.0),
            (-50.0, "cpp-manual", None),
            (-50.0, "none", None),
            (None, "desktop-margin", None),
        ]
        for squelch, applied_by, threshold in cases:
            status = SimpleNamespace(
                squelch_db=squelch,
                squelch_applied_by=applied_by,
                squelch_threshold_dbfs=threshold)
            backend = SimpleNamespace(get_status=lambda: status)
            panel.update_from_backend(backend)
            expected = _fmt_squelch(squelch, applied_by, threshold)
            assert panel.rx_squelch.text() == expected, (
                "后端刷新没走来源感知格式化："
                f"({squelch!r}, {applied_by!r}, {threshold!r}) -> "
                f"{panel.rx_squelch.text()!r}，期望 {expected!r}")
    finally:
        panel.deleteLater()

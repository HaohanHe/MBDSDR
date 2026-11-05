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


def test_static_guard_no_bare_dbfs_on_margin():
    src = open(os.path.join(_ROOT, "desktop", "status_panel.py"),
               encoding="utf-8").read()
    assert 'f"{squelch:.0f} dBFS"' not in src, (
        "余量刻度又被直接标成 dBFS 了")
    assert "_fmt_squelch(" in src
    assert "squelch_applied_by" in src
    assert "squelch_threshold_dbfs" in src, (
        "DBFS 只允许来自 squelch_threshold_dbfs")
    # 字段说明必须写明是余量（防止后端把语义改回去）
    backend = open(os.path.join(_ROOT, "mbdsdr_ai", "sdr_backend.py"),
                   encoding="utf-8").read()
    tree = ast.parse(backend)
    lines = backend.splitlines()
    ann = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and \
                getattr(node.target, "id", "") == "squelch_db":
            ann = node
            break
    assert ann is not None, "SDRStatus 里找不到 squelch_db"
    head = "\n".join(lines[max(0, ann.lineno - 8):ann.lineno])
    assert "余量" in head, "squelch_db 字段说明必须写明「余量」语义"


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

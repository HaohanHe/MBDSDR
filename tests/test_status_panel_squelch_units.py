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


def test_update_from_backend_uses_new_formatter():
    src = open(os.path.join(_ROOT, "desktop", "status_panel.py"),
               encoding="utf-8").read()
    start = src.index("def update_from_backend")
    body = src[start:src.index("\n    def ", start + 10)]
    assert "_fmt_squelch(" in body, "状态栏刷新必须走来源感知的格式化"

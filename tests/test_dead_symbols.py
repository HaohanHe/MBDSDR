"""死符号检测器的三条反证：方向是「白名单不生效就必须误报」。

(a) 只被 `getattr` 拼名调用的方法不得判死；
(b) 只被 Qt `connectSlotsByName` 按名连接的槽不得判死；
(c) 真死函数必须判死。
另加一条「白名单确实是承重的」：把 `_dynamic_reason` 短路掉之后，(a)(b) 两处
必须变成 dead——否则上面两条通过只能说明断言恒真。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

lint = importlib.import_module("scripts.lint_dead_symbols")

FIXTURE = '''\
import sys


def _by_getattr():
    return 1


def _never_called():
    return 2


class _Panel:
    def on_ok_clicked(self):
        return 3

    def _plain_dead_method(self):
        return 4


def wire():
    getattr(sys.modules[__name__], "_by_getattr")()
'''


def _project(tmp_path: Path) -> Path:
    pkg = tmp_path / "mbdsdr_ai"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(FIXTURE, encoding="utf-8")
    return tmp_path


def _verdicts(result: dict) -> dict[str, str]:
    out = {}
    for kind in ("dead", "dynamic", "reserved", "used"):
        for row in result[kind]:
            out[row["qualname"]] = kind
    return out


def test_getattr_only_method_is_not_dead(tmp_path):
    result = lint.run(_project(tmp_path))
    verds = _verdicts(result)
    assert verds["_by_getattr"] == "dynamic", "只被 getattr 拼名调用的函数被判死了"
    row = next(r for r in result["dynamic"] if r["qualname"] == "_by_getattr")
    assert row["reason"].startswith("D1"), row["reason"]


def test_signal_connected_slot_is_not_dead(tmp_path):
    result = lint.run(_project(tmp_path))
    verds = _verdicts(result)
    assert verds["_Panel.on_ok_clicked"] == "dynamic", "按名连接的槽被判死了"
    row = next(r for r in result["dynamic"] if r["qualname"] == "_Panel.on_ok_clicked")
    assert row["reason"].startswith("D3"), row["reason"]


def test_truly_dead_function_is_reported(tmp_path):
    result = lint.run(_project(tmp_path))
    verds = _verdicts(result)
    assert verds["_never_called"] == "dead"
    assert verds["_Panel._plain_dead_method"] == "dead"


def test_whitelist_is_load_bearing(tmp_path, monkeypatch):
    """把动态可达判据短路，前两条的符号必须立刻变成 dead（反证不是恒真）。"""
    root = _project(tmp_path)
    before = _verdicts(lint.run(root))
    assert before["_by_getattr"] == "dynamic"
    assert before["_Panel.on_ok_clicked"] == "dynamic"

    monkeypatch.setattr(lint.Scanner, "_dynamic_reason", lambda self, d: None)
    after = _verdicts(lint.run(root))
    assert after["_by_getattr"] == "dead", "白名单被短路后仍不判死，说明它没在起作用"
    assert after["_Panel.on_ok_clicked"] == "dead"

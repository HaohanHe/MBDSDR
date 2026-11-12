"""死符号检测器的反证：方向是「白名单不生效就必须误报」。

(a) 只被 `getattr` 拼名调用的方法不得判死；
(b) 只被 Qt `connectSlotsByName` 按名连接的槽不得判死；
(c) 真死函数必须判死；
(d) D5a：名字出现在**代码按路径读过**的配置里 → 必须判活；
(e) D5b：名字只出现在 `.md` 文字里 / 只在没人读的 JSON 里 → 必须判死；
(f) BOM 文件必须进引用源（解析失败=静默缺口，会压低真死数）。
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


# ---------------------------------------------------------------- D5a / D5b 拆分
D5_FIXTURE = '''\
def run_from_config():
    return 1


def doc_only_helper():
    return 2


def ghost_helper():
    return 3


def _config_path():
    return "commands.json"
'''


def _d5_project(tmp_path: Path) -> Path:
    root = _project(tmp_path)
    (root / "mbdsdr_ai" / "mod.py").write_text(D5_FIXTURE, encoding="utf-8")
    # 代码真按路径读的配置：名字必须判活（D5a）。
    (root / "commands.json").write_text(
        '{"entry": "run_from_config"}', encoding="utf-8")
    # 没有任何代码引用的 JSON（死真值源）：名字不得因此判活（D5b）。
    (root / "orphan_data.json").write_text(
        '{"entry": "ghost_helper"}', encoding="utf-8")
    # 只被 .md 文字提到：不算可达（D5b）。
    (root / "docs").mkdir()
    (root / "docs" / "NOTES.md").write_text(
        "调用 `doc_only_helper` 即可，见 `ghost_helper`。", encoding="utf-8")
    return root


def test_config_key_lookup_is_dynamic(tmp_path):
    """D5a：代码按路径读过的配置里的名字 → 运行时可能被按名取到，必须判活。"""
    result = lint.run(_d5_project(tmp_path))
    verds = _verdicts(result)
    assert verds["run_from_config"] == "dynamic", "被配置键按名取的函数被判死了"
    row = next(r for r in result["dynamic"] if r["qualname"] == "run_from_config")
    assert row["reason"].startswith("D5a"), row["reason"]


def test_md_only_mention_is_dead(tmp_path):
    """D5b：只被 .md 提及不算可达，必须落回真死候选。"""
    result = lint.run(_d5_project(tmp_path))
    verds = _verdicts(result)
    assert verds["doc_only_helper"] == "dead", "只被文档提到的函数没被判死"
    row = next(r for r in result["dead"] if r["qualname"] == "doc_only_helper")
    assert row["reason"].startswith("D5b"), row["reason"]


def test_unread_config_key_does_not_rescue(tmp_path):
    """没有代码引用的 JSON（死真值源）整份不收：名字不得因此判活。"""
    result = lint.run(_d5_project(tmp_path))
    verds = _verdicts(result)
    assert verds["ghost_helper"] == "dead", (
        "没有任何代码读取的 JSON 里的名字被当成了运行时可达")


def test_bom_file_enters_reference_source(tmp_path):
    """带 UTF-8 BOM 的文件必须正常解析：解析失败是静默缺口，会压低真死数。"""
    root = _project(tmp_path)
    bom = "\ufeffdef _bom_target():\n    return 1\n\n\ndef call_it():\n    _bom_target()\n"
    (root / "mbdsdr_ai" / "bom_mod.py").write_text(bom, encoding="utf-8")
    result = lint.run(root)
    assert result["parse_errors"] == [], result["parse_errors"]
    assert result["scanned"] == 3, "BOM 文件没被计入引用源（mod/__init__/bom_mod）"
    assert _verdicts(result)["_bom_target"] == "used"

"""版本派生清单与全仓字面量守卫的钉子。

对应 ``docs/packaging.md`` §9.1：
* 派生点固定为 **11 个**，每个文件必须存在且命中「读取 VERSION / 引用共享常量」
  的正则（失配即 FAIL，不许删位置置换绿）；
* 全仓守卫只要出现 canonical 字面量就 FAIL；豁免只有 §9.1 登记的 5 处，
  新增豁免必须同时改文档与清单（本用例把清单钉死，防止现场加白名单）。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_manifest as rm  # noqa: E402

#: 从唯一手抄点取，测试源码里不再写第二遍版本字面量（否则全仓守卫自己就红）。
CANONICAL = (ROOT / "mbdsdr_ai" / "VERSION").read_text(encoding="utf-8-sig").strip()


def test_derivations_are_exactly_eleven_and_all_match():
    assert len(rm.VERSION_DERIVATIONS) == 11
    for rel, rules, _note, _strict in rm.VERSION_DERIVATIONS:
        path = ROOT / rel
        assert path.exists(), f"派生点 {rel} 不存在"
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for pattern, desc in rules:
            assert re.search(pattern, text, re.MULTILINE), f"{rel} 未命中「{desc}」"


def test_exemptions_are_the_five_registered_ones():
    assert {rel for rel, _reason in rm.VERSION_LITERAL_EXEMPT} == {
        "mbdsdr_ai/VERSION",
        "README.md",
        "docs/learn/nanovna/protocol-study.md",
        "mobile/pubspec.yaml",
        "docs/packaging.md",
    }


def test_guard_hits_handwritten_literal(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text(f'VERSION = "{CANONICAL}"\n', encoding="utf-8")
    hits = rm.scan_version_literals(tmp_path, CANONICAL, ["pkg/app.py"])
    assert hits == ["pkg/app.py:1"]


def test_guard_allows_registered_exemptions_and_derivations(tmp_path):
    (tmp_path / "README.md").write_text(f"v{CANONICAL}\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "packaging.md").write_text(f"{CANONICAL}\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "package_windows.ps1").write_text(f"{CANONICAL}\n", encoding="utf-8")
    hits = rm.scan_version_literals(
        tmp_path, CANONICAL,
        ["README.md", "docs/packaging.md", "scripts/package_windows.ps1"])
    assert hits == []


def test_guard_skips_binary(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01" + CANONICAL.encode())
    hits = rm.scan_version_literals(tmp_path, CANONICAL, ["blob.bin"])
    assert hits == []

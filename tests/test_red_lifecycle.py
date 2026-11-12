#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""红项生命周期机检的反证：没举证就判红。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_red_lifecycle.py"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / "docs" / "placeholder.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "docs/placeholder.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return root


def _write(root: Path, name: str, text: str) -> str:
    path = root / "docs" / name
    path.write_text(text, encoding="utf-8")
    return f"docs/{name}"


def _sha(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root: Path, files: dict, message: str = "change") -> str:
    """提交指定文件，返回该提交 sha（R6 要求 hash 真的碰到被引用对象）。"""
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", relative], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
    return _sha(root)


def test_fixed_without_hash_is_red(tmp_path: Path) -> None:
    """反证 (a)：写「已修复」但没有 commit hash → R1 FAIL。"""
    root = _repo(tmp_path)
    path = _write(root, "a.md", "已修复：书签删除确认框不再早退。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R1]" in proc.stdout


def test_bad_hash_is_red(tmp_path: Path) -> None:
    """反证 (b)：hash 打错一个字符 → R2 FAIL。"""
    root = _repo(tmp_path)
    path = _write(root, "b.md", "已修 `abc1234`：频谱上限拆分。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R2]" in proc.stdout
    assert "abc1234" in proc.stdout


def test_not_verified_without_reason_is_red(tmp_path: Path) -> None:
    """反证 (c)：标 `not_verified` 但没写为什么没验/缺什么 → R4 FAIL。"""
    root = _repo(tmp_path)
    path = _write(root, "c.md", "频谱 8 槽：`not_verified`。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R4]" in proc.stdout


def test_cross_doc_status_conflict_is_red(tmp_path: Path) -> None:
    """反证 (d)：同一测试名在两份文档里状态打架 → R5 FAIL。"""
    root = _repo(tmp_path)
    sha = _commit(root, {"tests/test_foo.py": "def test_foo():\n    assert True\n"})
    good = _write(
        root,
        "d1.md",
        f"| `test_foo` | 已修 `{sha}` | 复跑 `pytest tests/test_foo.py` |\n",
    )
    bad = _write(root, "d2.md", "`test_foo` 标 `not_verified`，缺真机，无法复验。\n")
    proc = _run(root, "--paths", good, bad)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R5]" in proc.stdout
    assert "test_foo" in proc.stdout
    assert "d1.md" in proc.stdout and "d2.md" in proc.stdout


def test_well_formed_table_passes(tmp_path: Path) -> None:
    """真表口径：已修 + 有效 hash + 复跑命令 → PASS（WARN 不阻断）。"""
    root = _repo(tmp_path)
    sha = _commit(
        root,
        {"cpp/tests/test_spectrum_display.cpp": "TEST(Display, reRenderHistoryOnPaletteSwitch) {}\n"},
    )
    path = _write(
        root,
        "good.md",
        "| 用例 | 状态 | 复跑 |\n"
        "|---|---|---|\n"
        f"| `display::reRenderHistoryOnPaletteSwitch` | 已修 `{sha}`（1 passed） | "
        '`ctest -R "^(spectrum_display|spectrum_autorange)$"` |\n',
    )
    proc = _run(root, "--paths", path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout
    assert "R1 实修 1 条 / SNAPSHOT 0 条 / 历史横幅 0 条" in proc.stdout


def test_json_output_shape(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = _write(root, "j.md", "已修复：没有 hash。\n")
    proc = _run(root, "--paths", path, "--json")
    payload = json.loads(proc.stdout)
    assert payload["fail_count"] >= 1
    assert payload["findings"][0]["rule"] == "R1"
    assert payload["findings"][0]["path"] == "docs/j.md"


def _write_at(root: Path, relative: str, text: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative


def test_exemption_skips_listed_file_loudly(tmp_path: Path) -> None:
    """豁免生效：列明 + 机制可验证 → SKIP 打印出来（不静默）并 rc0。"""
    root = _repo(tmp_path)
    # 行内容 hash 台账：指纹是 12 位 hex，不是提交 hash。
    _write_at(
        root,
        "docs/claim-waivers.md",
        "| # | 文档 | 指纹 | 断言摘要 | 暂不补证理由 |\n"
        "|---|---|---|---|---|\n"
        "| 1 | docs/X.md | c7357a8b2b36 | 已修复：某断言。 | 无 hash |\n",
    )
    _write_at(
        root,
        "docs/red-lifecycle-exemptions.md",
        "| 路径 | 机制 | 理由 |\n"
        "|---|---|---|\n"
        "| `docs/claim-waivers.md` | `content-hash-ledger` | 第三列是行内容 hash，不是提交 hash |\n",
    )
    proc = _run(root, "--paths", "docs/claim-waivers.md")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SKIP" in proc.stdout, proc.stdout
    assert "docs/claim-waivers.md" in proc.stdout


def test_exemption_does_not_cover_ordinary_doc(tmp_path: Path) -> None:
    """反证：往豁免清单里塞普通文档 → 该文件仍按 R1/X0 判红。"""
    root = _repo(tmp_path)
    _write(root, "plain.md", "已修复：书签删除确认框不再早退。\n")
    _write_at(
        root,
        "docs/red-lifecycle-exemptions.md",
        "| 路径 | 机制 | 理由 |\n"
        "|---|---|---|\n"
        "| `docs/plain.md` | `content-hash-ledger` | 乱加的普通文档 |\n",
    )
    proc = _run(root, "--paths", "docs/plain.md")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    # 机制对不上 → X0；即便退回扫描也应有 R1。
    assert "[X0]" in proc.stdout or "[R1]" in proc.stdout, proc.stdout


def test_historical_banner_downgrades_to_hist_warn(tmp_path: Path) -> None:
    """历史目录 + 横幅 → R1 降为 HIST-WARN，不阻断。"""
    root = _repo(tmp_path)
    path = _write_at(
        root,
        "docs/learn/phase1/a.md",
        "# 历史阶段记录\n\n"
        "> **历史快照，不作现状依据**\n\n"
        "已修复：当时的频谱上限拆分。\n",
    )
    proc = _run(root, "--paths", path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "HIST-WARN" in proc.stdout


def test_historical_without_banner_still_red(tmp_path: Path) -> None:
    """反证：删掉横幅 → 同一历史文件仍按 R1 FAIL。"""
    root = _repo(tmp_path)
    path = _write_at(
        root,
        "docs/learn/phase1/a.md",
        "# 历史阶段记录\n\n已修复：当时的频谱上限拆分。\n",
    )
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R1]" in proc.stdout


def test_banner_on_modern_doc_is_rejected(tmp_path: Path) -> None:
    """反证：现代文档加历史横幅 → X1 FAIL（横幅只对历史目录有效）。"""
    root = _repo(tmp_path)
    path = _write(root, "modern.md", "# 现代文档\n\n> **历史快照，不作现状依据**\n\n正常内容。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[X1]" in proc.stdout


def test_section_group_conflict_is_red(tmp_path: Path) -> None:
    """反证：同文档 §7.3.1 B 组「未复核」与「已修」并存 → R5 FAIL。"""
    root = _repo(tmp_path)
    sha = _commit(
        root,
        {"cpp/tests/test_spectrum_display.cpp": "TEST(Display, reRenderHistoryOnPaletteSwitch) {}\n"},
    )
    path = _write(
        root,
        "ledger.md",
        "#### 7.3.1 红项账本\n"
        "\n"
        "> 复核说明：B 的 8 个 C++ 槽因构建阻塞\n"
        "> `main_window.cpp:5644` **未复核**（`not_verified`）。\n"
        "\n"
        "## A. Python\n"
        "\n"
        "## B. C++：8 槽\n"
        "\n"
        "| 用例 | 状态 |\n"
        "|---|---|\n"
        f"| display::reRenderHistoryOnPaletteSwitch | 已修 `{sha}` |\n",
    )
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R5]" in proc.stdout
    assert "§7.3.1 B" in proc.stdout


def test_r3_auto_maps_pytest_by_test_name(tmp_path: Path) -> None:
    """R3 自动映射：测试函数名唯一 → pytest <file>::<name>，不再记 WARN。"""
    root = _repo(tmp_path)
    sha = _commit(root, {"tests/test_sample.py": "def test_alpha():\n    assert True\n"})
    path = _write(root, "r3.md", f"已修复 `{sha}`，`test_alpha` 已绿（1 passed）。\n")
    proc = _run(root, "--paths", path, "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["warn_count"] == 0
    assert payload["r3_skips"] == []


def test_r3_ctest_slot_maps_to_ctest(tmp_path: Path) -> None:
    """R3 自动映射：C++ 槽名只在唯一测试源文件出现 → ctest -R 该测试。"""
    root = _repo(tmp_path)
    sha = _commit(
        root,
        {
            "cpp/tests/test_spectrum_display.cpp": "TEST(FooSlot) {}\n",
            "cpp/build/CTestTestfile.cmake": 'add_test(spectrum_display "D:/build/test_spectrum_display.exe")\n',
        },
    )
    path = _write(root, "r3c.md", f"| display::FooSlot | 已修 `{sha}`（1 passed） |\n")
    proc = _run(root, "--paths", path, "--json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["r3_skips"] == []


def test_r3_unmapped_prints_skip(tmp_path: Path) -> None:
    """无法映射 → 记 SKIP 并逐条打印，不许静默。"""
    root = _repo(tmp_path)
    sha = _commit(root, {"docs/target.md": "当时的记录。\n"})
    path = _write(root, "r3u.md", f"已修复 `{sha}`：`docs/target.md` 当时的记录。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SKIP 1（R3 无复跑命令" in proc.stdout
    assert "docs/r3u.md:1" in proc.stdout


def test_r6_unrelated_hash_is_red(tmp_path: Path) -> None:
    """反证 (e)：hash 真实存在，但提交改的是 docs/beta.md，本条说 docs/alpha.md → R6 FAIL。"""
    root = _repo(tmp_path)
    sha = _commit(root, {"docs/beta.md": "beta\n"})
    path = _write(root, "alpha.md", f"已修 `{sha}`：`docs/alpha.md` 的说明已更新。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R6]" in proc.stdout
    assert sha[:7] in proc.stdout


def test_r6_related_hash_passes(tmp_path: Path) -> None:
    """同一条改成引用被该提交改到的文件 → R6 通过。"""
    root = _repo(tmp_path)
    sha = _commit(root, {"docs/alpha.md": "alpha\n"})
    path = _write(root, "alpha-ok.md", f"已修 `{sha}`：`docs/alpha.md` 的说明已更新。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[R6]" not in proc.stdout


def test_r6_without_any_reference_is_red(tmp_path: Path) -> None:
    """反证：条目没有任何可比对象（只有 hash）→ R6 FAIL，不许蒙混。"""
    root = _repo(tmp_path)
    sha = _commit(root, {"docs/alpha.md": "alpha\n"})
    path = _write(root, "noref.md", f"已修复 `{sha}`，详见当时的记录。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R6]" in proc.stdout
    assert "没有可比的改动对象" in proc.stdout


def test_snapshot_downgrades_red_items(tmp_path: Path) -> None:
    """有效快照头：红项转 SNAPSHOT 计数，不再计 FAIL，但总账里看得见。"""
    root = _repo(tmp_path)
    path = _write(
        root,
        "snap.md",
        "# 当时状态\n\n"
        "> **本文为当时状态快照**：以下为当时账面状态。\n"
        "> 未逐条复核；不作现状依据。\n"
        "> 共 2 条未复核。\n\n"
        "已修复：书签删除确认框不再早退。\n"
        "已修复：频谱上限拆分。\n",
    )
    proc = _run(root, "--paths", path, "--json")
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert payload["fail_count"] == 0
    assert payload["snapshot_count"] == 2
    assert payload["snapshot_files"] == ["docs/snap.md"]
    assert payload["ledger"] == "R1 实修 0 条 / SNAPSHOT 2 条 / 历史横幅 0 条"


def test_snapshot_without_required_wording_is_red(tmp_path: Path) -> None:
    """反证：快照头缺「未逐条复核 / 不作现状依据」→ R7 FAIL，且红项照常判红。"""
    root = _repo(tmp_path)
    path = _write(
        root,
        "snap-bad.md",
        "> 本文为当时状态快照。\n> 共 1 条未复核。\n\n已修复：频谱上限拆分。\n",
    )
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R7]" in proc.stdout
    assert "[R1]" in proc.stdout


def test_snapshot_count_is_rechecked_after_file_is_rewritten(tmp_path: Path) -> None:
    """回归：转 T 后同一文件再被改写、N 未同步 → 复跑必须 R7 FAIL。

    不是「转 T 时核对一次」：第一次 N=2 且实际 2 条 → 过；随后在同一文件
    追加第 3 条红项、快照头仍写「共 2 条」→ 再次运行必须判红。
    """
    root = _repo(tmp_path)
    path = _write(
        root,
        "snap-rewrite.md",
        "> 本文为当时状态快照。\n> 未逐条复核；不作现状依据。\n> 共 2 条未复核。\n\n"
        "已修复：书签删除确认框不再早退。\n"
        "已修复：频谱上限拆分。\n",
    )
    first = _run(root, "--paths", path)
    assert first.returncode == 0, first.stdout + first.stderr

    # 文件后续被改写：多出第 3 条红项，但快照头的 N 仍是 2。
    target = root / "docs" / "snap-rewrite.md"
    target.write_text(
        target.read_text(encoding="utf-8") + "已修复：面板接线判定改为行为断言。\n",
        encoding="utf-8",
    )
    second = _run(root, "--paths", path)
    assert second.returncode == 1, second.stdout + second.stderr
    assert "[R7]" in second.stdout
    assert "声明 2 条，实际 3 条" in second.stdout
    assert "[R1]" in second.stdout


def _binding_doc(sha: str, target: str, status: str = "已转绿") -> str:
    return (
        f"## K19 — 样例（{status} `{sha}`）\n\n"
        f"- **再检绑定**：验证提交 `{sha}`；文件集合 `{target}`\n"
        "- **验证（实跑）**：`1 passed`。\n"
    )


def test_r8_later_change_after_verification_goes_pending(tmp_path: Path) -> None:
    """反证 (f)：验证 sha 之后同一文件又被改 → R8 待复核，并点名文件与提交。"""
    root = _repo(tmp_path)
    sha_a = _commit(
        root, {"tests/test_pin.py": "def test_pin():\n    assert True\n"}, "A"
    )
    sha_b = _commit(
        root,
        {"tests/test_pin.py": "def test_pin():\n    assert True\n# 后继改动\n"},
        "B 又改了同一个文件",
    )
    path = _write(root, "r8-later.md", _binding_doc(sha_a, "tests/test_pin.py"))
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R8]" in proc.stdout
    assert "待复核" in proc.stdout
    assert "tests/test_pin.py" in proc.stdout
    assert sha_b[:7] in proc.stdout
    assert "B 又改了同一个文件" in proc.stdout


def test_r8_directory_binding_names_the_changed_file(tmp_path: Path) -> None:
    """目录绑定（tests/）：后继提交碰目录内任一文件 → 点名那个文件。"""
    root = _repo(tmp_path)
    sha_a = _commit(
        root, {"tests/test_one.py": "def test_one():\n    assert True\n"}, "A"
    )
    sha_b = _commit(
        root, {"tests/test_two.py": "def test_two():\n    assert True\n"}, "B 新增"
    )
    path = _write(root, "r8-dir.md", _binding_doc(sha_a, "tests/"))
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R8]" in proc.stdout
    assert "tests/test_two.py" in proc.stdout
    assert sha_b[:7] in proc.stdout


def test_r8_deleted_bound_file_goes_pending(tmp_path: Path) -> None:
    """反证 (g)：声称已绿的文件被删掉 → R8 必须报。"""
    root = _repo(tmp_path)
    sha = _commit(
        root, {"tests/test_pin.py": "def test_pin():\n    assert True\n"}, "A"
    )
    path = _write(root, "r8-gone.md", _binding_doc(sha, "tests/test_gone.py"))
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R8]" in proc.stdout
    assert "tests/test_gone.py" in proc.stdout
    assert "已不存在" in proc.stdout


def test_green_without_commit_hash_uses_the_record_wording(tmp_path: Path) -> None:
    """反证 (h)：「已转绿」没有任何提交号 → R1，文案固定为可复跑记录要求。"""
    root = _repo(tmp_path)
    path = _write(root, "green-no-sha.md", "## K20 — 样例（已转绿）\n\n正文。\n")
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R1]" in proc.stdout
    assert "已修必须带一次可复跑的验证记录（命令 + 实际输出）" in proc.stdout


def test_r8_clean_binding_passes(tmp_path: Path) -> None:
    """正例：验证 sha 之后文件没再变 → 不报 R8，待复核计数为 0。"""
    root = _repo(tmp_path)
    sha = _commit(
        root, {"tests/test_pin.py": "def test_pin():\n    assert True\n"}, "A"
    )
    path = _write(root, "r8-clean.md", _binding_doc(sha, "tests/test_pin.py"))
    proc = _run(root, "--paths", path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[R8]" not in proc.stdout
    assert "再检：待复核/缺绑定 0 条" in proc.stdout


def test_green_entry_without_binding_is_red(tmp_path: Path) -> None:
    """反证 (i)：账本里「已转绿」但没有再检绑定 → R8 FAIL。"""
    root = _repo(tmp_path)
    sha = _commit(
        root, {"tests/test_pin.py": "def test_pin():\n    assert True\n"}, "A"
    )
    path = _write(
        root,
        "known-red-register.md",
        f"## K20 — 样例（已转绿 `{sha}`）\n\n"
        "复跑：`pytest tests/test_pin.py`（1 passed）。\n",
    )
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R8]" in proc.stdout
    assert "没有「再检绑定」" in proc.stdout


def test_fixed_with_command_but_no_output_is_red(tmp_path: Path) -> None:
    """反证 (j)：已修 + hash + 命令，但没写实际输出 → R9 FAIL（表头规矩机检）。"""
    root = _repo(tmp_path)
    sha = _commit(
        root, {"tests/test_pin.py": "def test_pin():\n    assert True\n"}, "A"
    )
    path = _write(
        root,
        "known-red-register.md",
        f"已修复 `{sha}`：复跑 `pytest tests/test_pin.py`。\n",
    )
    proc = _run(root, "--paths", path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[R9]" in proc.stdout
    assert "缺「实际输出」" in proc.stdout

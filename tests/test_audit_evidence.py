"""文件级 E4 横幅的钉子：``audit_evidence`` 的 K23 收敛机制（R1）。

三件反证：
1. 文件头有横幅 → 整份文件记「已显式标未验证」，不必逐条贴标记；
2. 没有横幅 → 照旧记「无证据」（横幅只按显式写法生效，不按目录名猜）；
3. 本行自带原始件 → 仍按 E1 算，横幅不是免检通行证。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_evidence as ae  # noqa: E402


def test_banner_at_file_head_marks_whole_file() -> None:
    text = (
        "# 历史快照\n\n"
        "> 证据状态：E4——本文件数字未验证，仅作历史记录。\n\n"
        "| 项目 | 数值 |\n|---|---|\n| 增益 | 12.5 dB |\n"
    )
    assert ae.FILE_BANNER_RE.search("\n".join(text.splitlines()[:80]))
    assert ae._classify("增益 12.5 dB", "| 增益 | 12.5 dB |", True) == (
        "已显式标未验证", "文件级 E4 横幅（本文件数字未验证）")


def test_without_banner_stays_unverified() -> None:
    col, why = ae._classify("增益 12.5 dB", "| 增益 | 12.5 dB |", False)
    assert col == "无证据", why


def test_row_with_own_artifact_beats_banner() -> None:
    # 用仓内既有工件：PATH_RE 只认 ``a/b.ext`` 形态，绝对路径不在窗口判据里。
    col, why = ae._classify("实测 12.5 dB，见 docs/axioms-accepted.json",
                            "实测 12.5 dB，见 docs/axioms-accepted.json", True)
    assert col == "有证据", why


def test_run_applies_banner(tmp_path: Path) -> None:
    snap = tmp_path / "snap.md"
    snap.write_text("> 本文件全部数字未验证（E4）\n\n实测增益 12.5 dB。\n",
                    encoding="utf-8")
    rep = ae.run([str(snap)])
    assert rep["file_banners"] == [str(snap)]
    assert rep["counts"].get("已显式标未验证", 0) >= 1
    assert rep["counts"].get("无证据", 0) == 0

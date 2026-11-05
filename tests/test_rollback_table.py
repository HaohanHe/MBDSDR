"""§6.10.2 回退事件表机检的反证。

覆盖：
- 真表全过（提交号存在、修复提交存在、计数与撤回文件数一致）；
- 提交号改一个字 → 必红；
- 计数写错 → 必红。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_rollback_table.py"
DOC = ROOT / "docs" / "COORDINATION.md"


def _run(doc_path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--doc", str(doc_path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_real_table_passes():
    proc = _run(DOC)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "PASS" in out
    assert "FAIL" not in out


def test_bad_commit_hash_goes_red(tmp_path):
    text = DOC.read_text(encoding="utf-8")
    bad = text.replace("| `928bd78f`（01:58:13） |", "| `928bd78e`（01:58:13） |")
    assert bad != text, "反证未生效：找不到目标行"
    target = tmp_path / "COORDINATION_bad_hash.md"
    target.write_text(bad, encoding="utf-8")

    proc = _run(target)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "FAIL" in out
    assert "928bd78e" in out


def test_wrong_count_goes_red(tmp_path):
    text = DOC.read_text(encoding="utf-8")
    bad = text.replace("| `16f075f3`（01:55:02） | 26 |", "| `16f075f3`（01:55:02） | 25 |")
    assert bad != text, "反证未生效：找不到目标行"
    target = tmp_path / "COORDINATION_bad_count.md"
    target.write_text(bad, encoding="utf-8")

    proc = _run(target)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "计数不符" in out

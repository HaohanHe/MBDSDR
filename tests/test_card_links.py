"""速查卡锚点防漂移的反证：改错行号 → 必红。"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_card_links.py"
CARD = ROOT / "docs" / "COMMIT-INCIDENT-CARD.md"


def _run(card_path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--card", str(card_path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_real_card_links_pass():
    proc = _run(CARD)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "PASS" in out


def test_bad_anchor_line_goes_red(tmp_path):
    text = CARD.read_text(encoding="utf-8")
    bad = text.replace("#L1178", "#L1179")
    assert bad != text, "反证未生效：找不到目标锚点"
    target = tmp_path / "CARD_bad_anchor.md"
    target.write_text(bad, encoding="utf-8")

    proc = _run(target)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "FAIL" in out
    assert "1179" in out

"""发布产物凭据门禁的钉子：``release_manifest`` 第 5 项 + ``secret_scan --artifact``。

反证是用户点名的场景：往待打包目录塞一个含假 PAT 的文件 → 检查必须红并
**点名文件**；删掉即绿。另钉两条：

* 二进制（exe/dll/pyc）不能跳过——凭据可能被嵌进去，所以按字节扫；
* 历史扫描的「已知良性」只对「登记过的 sha + 登记过的规则 + 不可达」生效，
  可达 blob 或另一个 sha 照旧报（解除条件不能靠人记）。
"""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_manifest as rm  # noqa: E402
import secret_scan as ss  # noqa: E402

FAKE = "ghp_" + "B" * 36


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "sub").mkdir(parents=True)
    (dist / "main.exe.txt").write_text("clean\n", encoding="utf-8")
    return dist


def test_artifact_gate_blocks_names_file_and_goes_green_when_removed(
    tmp_path: Path, capsys
) -> None:
    dist = _dist(tmp_path)
    leak = dist / "sub" / "upload_notes.txt"
    leak.write_text(f'token = "{FAKE}"\n', encoding="utf-8")

    hits, stats = ss.scan_artifact(dist)
    assert [rel for rel, _rule in hits] == ["sub/upload_notes.txt"]
    assert stats["artifact_files"] == 2

    assert ss.main(["--artifact", str(dist)]) == 1
    out = capsys.readouterr().out
    assert "BLOCK sub/upload_notes.txt github-pat-classic" in out
    assert FAKE not in out, "产物门禁也不许回显命中的值"

    items = rm.check_secrets(dist)
    assert any(
        it["status"] == rm.FAIL and "sub/upload_notes.txt" in it["text"] for it in items
    )

    leak.unlink()
    assert ss.scan_artifact(dist)[0] == []
    assert ss.main(["--artifact", str(dist)]) == 0
    items = rm.check_secrets(dist)
    assert [it["status"] for it in items] == [rm.PASS], items


def test_artifact_gate_scans_binaries_by_bytes(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    (dist / "payload.dll").write_bytes(b"MZ\x00\x00" + FAKE.encode() + b"\x00\x01")

    hits, _stats = ss.scan_artifact(dist)
    assert ("payload.dll", "github-pat-classic") in hits


def test_artifact_allowlist_needs_path_sha_rule_and_unexpired_review(
    tmp_path: Path, monkeypatch
) -> None:
    """二进制误报登记只对「同路径 + 同 sha256 + 同规则 + 未过期」放行。"""
    dist = _dist(tmp_path)
    payload = dist / "vendor" / "lib.dll"
    payload.parent.mkdir()
    payload.write_bytes(b"MZ" + b"ghp_" + b"C" * 36)
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    entry = {
        "path": "vendor/lib.dll",
        "sha256": digest,
        "rules": ("github-pat-classic",),
        "reason": "测试用假二进制",
        "review_by": "2999-12-31",
    }
    monkeypatch.setattr(rm, "ARTIFACT_SECRET_ALLOWLIST", (entry,))
    statuses = [it["status"] for it in rm.check_secrets(dist)]
    assert rm.FAIL not in statuses and rm.PASS in statuses

    # 文件变了（sha 不符）→ 重新红
    payload.write_bytes(payload.read_bytes() + b"x")
    assert any(it["status"] == rm.FAIL for it in rm.check_secrets(dist))

    # 登记过期 → 重新红
    payload.write_bytes(b"MZ" + b"ghp_" + b"C" * 36)
    monkeypatch.setattr(rm, "ARTIFACT_SECRET_ALLOWLIST",
                        ({**entry, "review_by": "2000-01-01"},))
    assert any(it["status"] == rm.FAIL for it in rm.check_secrets(dist))

    # 规则不符 → 重新红
    monkeypatch.setattr(rm, "ARTIFACT_SECRET_ALLOWLIST",
                        ({**entry, "rules": ("aws-access-key",)},))
    assert any(it["status"] == rm.FAIL for it in rm.check_secrets(dist))


def test_known_benign_requires_registered_sha_rule_and_unreachable() -> None:
    registered = next(iter(ss.KNOWN_BENIGN_HISTORY))
    rule = ss.KNOWN_BENIGN_HISTORY[registered]

    # 登记过、规则一致、不可达 → 记良性
    assert ss._split_known_benign(registered, [rule], []) == ([rule], [])
    # 一旦被提交引用（可达）→ 照旧报
    assert ss._split_known_benign(registered, [rule], ["a" * 40]) == ([], [rule])
    # 另一个 sha → 照旧报
    assert ss._split_known_benign("f" * 40, [rule], []) == ([], [rule])
    # 同一 blob 命中别的规则 → 照旧报
    assert ss._split_known_benign(registered, ["aws-access-key"], []) == (
        [],
        ["aws-access-key"],
    )

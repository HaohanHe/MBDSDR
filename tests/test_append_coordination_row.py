"""`scripts/append_coordination_row.py`：§6.1 追加行的窄锁语义。

钉住三条契约：

① **并发不丢行**：两条线各追加 10 行，最终恰好 20 行、线内顺序不乱。
   这条必须真起两个进程——同进程里顺序调两次函数证明不了锁有用。
   （把 `_acquire_lock`/`_release_lock` 改成空实现后，本文件必须变红。）
② **校验不放宽**：owner 与台账不一致、路径不存在/越界、note 为空或含 `|`、
   claim 已过期——一律拒绝，且**文件一个字节都不动**。
③ **只追加、不重写**：LF 文档追加完仍是 LF。Python 默认 `open(..., "w")` 在
   Windows 上会把 `\\n` 翻成 CRLF，那等于把整本 370KB 文档重写一遍。

窄锁与人工 claim 的分工写在脚本 docstring 里：大段编辑仍整文件 claim，
追加登记行走本工具，两条路并存。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
#: 反证用：把脚本复制到别处并拆掉锁，再用 MBDSDR_APPEND_SCRIPT 指过去。
SCRIPT = Path(
    os.environ.get("MBDSDR_APPEND_SCRIPT")
    or (REPO / "scripts" / "append_coordination_row.py")
)

DOC_TEMPLATE = """# 台账

## 六、共享文件占用锁

### 6.1 当前占用

| 文件 | 线名 | 开始时间 | 预计提交 hash |
|---|---|---|---|
| _（空）_ | | | |

### 6.1.1 下一节

这一节的正文不能被追加行挤掉。
"""


def _make_doc(root: Path, pad: int = 0) -> Path:
    text = DOC_TEMPLATE
    if pad:
        # 真实文档 4769 行 / 376KB；并发用例把它垫到同量级，
        # 否则读-写临界区只有几十微秒，无锁也撞不上。
        filler = "".join(
            f"- 填充 {i}：让文档与真实台账同量级，扩大读-写竞争窗口。\n"
            for i in range(pad)
        )
        text = text.replace("### 6.1.1 下一节", filler + "### 6.1.1 下一节")
    doc = root / "docs" / "COORDINATION.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    with doc.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return doc


def _make_files(root: Path, *rel: str) -> None:
    for name in rel:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")


def _write_claims(locks: Path, pairs: dict[str, tuple[str, float]]) -> None:
    """直接写台账 + 占位文件（与 file_claim.py 的落盘结构一致）。

    活跃判据在闸与工具两侧都读**占位文件**（心跳 + TTL），台账 ``current``
    只是旁证；只写台账会得到「台账有 claim、工具说过期」的假红。
    """
    now = time.time()
    claims = {}
    marker_dir = locks / "claims"
    marker_dir.mkdir(parents=True, exist_ok=True)
    for path, (owner, age) in pairs.items():
        key = path.replace("\\", "/").lower()
        claims[key] = {
            "path": path,
            "current": {
                "path": path,
                "key": key,
                "owner": owner,
                "acquired_at": now - age,
                "heartbeat_at": now - age,
                "ttl_min": 10.0,
                "pid": os.getpid(),
            },
            "history": [],
        }
        (marker_dir / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}.claim"
         ).write_text(json.dumps(claims[key]["current"], ensure_ascii=False),
                      encoding="utf-8")
    locks.mkdir(parents=True, exist_ok=True)
    (locks / "claims.json").write_text(
        json.dumps({"version": 1, "claims": claims}, ensure_ascii=False), encoding="utf-8"
    )


def _env(locks: Path) -> dict:
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env.pop("PYTHONPATH", None)
    return env


def _run(root: Path, locks: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        cwd=REPO,
        env=_env(locks),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


#: 一条线的驱动进程：顺序追加 10 行（每行一次 CLI，和真人用法一致）。
DRIVER = """
import subprocess, sys
script, root, path, owner, tag = sys.argv[1:6]
for i in range(10):
    r = subprocess.run(
        [sys.executable, script, "--path", path, "--owner", owner,
         "--note", f"{tag}-{i:02d}", "--root", root],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        sys.exit(1)
"""


def test_append_inserts_at_table_end(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    locks = tmp_path / "locks"
    _make_files(root, "scripts/a.py")
    doc = _make_doc(root)
    _write_claims(locks, {"scripts/a.py": ("线A/0001", 0.0)})

    res = _run(root, locks, "--path", "scripts/a.py", "--owner", "线A/0001",
               "--note", "测试一行")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "APPEND OK" in res.stdout

    lines = doc.read_text(encoding="utf-8").splitlines()
    idx = next(i for i, ln in enumerate(lines) if "`scripts/a.py`" in ln)
    assert lines[idx - 1].startswith("| _（空）_"), "追加行应落在 §6.1 表格末尾"
    assert lines[idx + 1] == ""
    assert lines[idx + 2].startswith("### 6.1.1"), "不能挤掉下一节"


def test_malformed_rows_rejected_without_touching_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    locks = tmp_path / "locks"
    _make_files(root, "scripts/a.py")
    doc = _make_doc(root)
    _write_claims(locks, {
        "scripts/a.py": ("线A/0001", 0.0),
        # 已过期：心跳 20 分钟前、TTL 10 分钟
        "scripts/stale.py": ("线A/0001", 1200.0),
    })
    _make_files(root, "scripts/stale.py")
    before = doc.read_bytes()

    cases = [
        (["--path", "scripts/a.py", "--owner", "线B/0002", "--note", "抢别人的文件"],
         "owner 不一致"),
        (["--path", "scripts/missing.py", "--owner", "线A/0001", "--note", "x"],
         "路径不存在"),
        (["--path", "scripts/a.py", "--owner", "线A/0001", "--note", "含|竖线"],
         "--note 不能含"),
        (["--path", "scripts/a.py", "--owner", "线A/0001", "--note", "   "],
         "--note 不能为空"),
        (["--path", "../outside.py", "--owner", "线A/0001", "--note", "越界"],
         "相对路径"),
        (["--path", "scripts/stale.py", "--owner", "线A/0001", "--note", "过期 claim"],
         "没有活跃 claim"),
        (["--path", "scripts/a.py", "--owner", "无斜杠", "--note", "x"],
         "--owner 必须形如"),
    ]
    for args, needle in cases:
        res = _run(root, locks, *args)
        assert res.returncode == 1, f"{args} -> {res.returncode}\n{res.stdout}{res.stderr}"
        assert "APPEND DENIED" in res.stderr, res.stderr
        assert needle in res.stderr, f"{args} 的拒绝理由不含 {needle!r}：{res.stderr}"
        assert doc.read_bytes() == before, f"{args} 被拒绝却改了文件"


def test_two_lines_append_ten_rows_each_without_loss(tmp_path: Path) -> None:
    """反证 ①/③ 的本体：两个真进程各追加 10 行，20 行一条不丢。

    拆掉窄锁后本用例必须变红（两进程读同一份内容再各自写回，互相覆盖）。
    """
    root = tmp_path / "repo"
    locks = tmp_path / "locks"
    _make_files(root, "scripts/a.py", "scripts/b.py")
    doc = _make_doc(root, pad=2400)
    _write_claims(locks, {
        "scripts/a.py": ("线A/0001", 0.0),
        "scripts/b.py": ("线B/0002", 0.0),
    })

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", DRIVER, str(SCRIPT), str(root),
             path, owner, tag],
            cwd=REPO, env=_env(locks),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        for path, owner, tag in (
            ("scripts/a.py", "线A/0001", "A"),
            ("scripts/b.py", "线B/0002", "B"),
        )
    ]
    for proc in procs:
        out, err = proc.communicate(timeout=300)
        assert proc.returncode == 0, f"驱动进程失败：\n{out}{err}"

    text = doc.read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.startswith("| `scripts/")]
    assert len(rows) == 20, f"期望 20 行，实际 {len(rows)} 行（丢行了）"

    for tag in ("A", "B"):
        marks = [re.search(rf"（({tag}-\d\d)）", ln).group(1)
                 for ln in rows if f"（{tag}-" in ln]
        assert marks == [f"{tag}-{i:02d}" for i in range(10)], f"{tag} 线内顺序乱了：{marks}"

    # 下一节与占位行都还在：追加不能吃掉正文
    assert "### 6.1.1 下一节" in text
    assert "| _（空）_ | | | |" in text


def test_append_keeps_lf_line_endings(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    locks = tmp_path / "locks"
    _make_files(root, "scripts/a.py")
    doc = _make_doc(root, pad=200)
    _write_claims(locks, {"scripts/a.py": ("线A/0001", 0.0)})
    assert b"\r\n" not in doc.read_bytes()

    res = _run(root, locks, "--path", "scripts/a.py", "--owner", "线A/0001",
               "--note", "行尾保持 LF")
    assert res.returncode == 0, res.stdout + res.stderr
    raw = doc.read_bytes()
    assert b"\r\n" not in raw, "追加把整本文档的行尾翻成了 CRLF"
    assert raw.count(b"\n") == DOC_TEMPLATE.count("\n") + 200 + 1

"""分块下载 HYG v4.1 全天星表（raw.githubusercontent.com 支持 Range）。

单次大文件会被网关掐断，按 2 MiB 分块下载、逐块校验长度再拼接。
产物 data/hygdata_v41.csv，附 SHA256 到 data/hygdata_v41.sha256。
来源与许可：astronexus/HYG-Database · HYG v4.1 · CC BY-SA 4.0
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

URL = ("https://raw.githubusercontent.com/astronexus/HYG-Database/"
       "main/hyg/CURRENT/hygdata_v41.csv")
TOTAL = 33_932_548
CHUNK = 2 * 1024 * 1024
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT
OUT = ROOT / "data"
CSV = OUT / "hygdata_v41.csv"
PARTS = OUT / "parts"


def fetch(start: int, end: int, dest: Path) -> bool:
    for attempt in range(4):
        r = subprocess.run(
            ["curl", "-sS", "-L", "--max-time", "60", "-r", f"{start}-{end}",
             URL, "-o", str(dest)], capture_output=True, text=True)
        if r.returncode == 0 and dest.is_file() and dest.stat().st_size == end - start + 1:
            return True
        print(f"  retry {start}-{end} attempt {attempt + 1}: "
              f"rc={r.returncode} size={dest.stat().st_size if dest.is_file() else -1}")
    return False


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    PARTS.mkdir(exist_ok=True)
    blobs = []
    pos = 0
    while pos < TOTAL:
        end = min(TOTAL - 1, pos + CHUNK - 1)
        part = PARTS / f"{pos:09d}.part"
        if not (part.is_file() and part.stat().st_size == end - pos + 1):
            if not fetch(pos, end, part):
                raise RuntimeError(f"分块下载失败：{pos}-{end}")
        blobs.append(part.read_bytes())
        print(f"  {pos:>9}-{end:<9} {end - pos + 1:>8} bytes  ok", flush=True)
        pos = end + 1

    data = b"".join(blobs)
    assert len(data) == TOTAL, f"拼接长度不符：{len(data)} != {TOTAL}"
    assert data.startswith(b'"id","hip"'), "CSV 头部不符"
    assert data.count(b"\n") > 100_000, f"行数异常：{data.count(b'\\n')}"
    CSV.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (OUT / "hygdata_v41.sha256").write_text(
        f"{digest}  hygdata_v41.csv\n"
        f"source: {URL}\nlicense: CC BY-SA 4.0 (astronexus / HYG v4.1)\n",
        encoding="utf-8")
    print(f"OK {CSV} {len(data)} bytes lines={data.count(b'\\n')} sha256={digest}")
    for p in PARTS.glob("*.part"):
        p.unlink()
    PARTS.rmdir()


if __name__ == "__main__":
    main()

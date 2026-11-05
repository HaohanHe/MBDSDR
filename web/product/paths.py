"""MBDSDR Web 产品工程 · 路径解析（可移植）。

产品既能放在开发目录 `D:/mbdsdr/output/mbdsdr-product/`，也能落在仓库
`web/product/`。所有脚本一律从这里取 ROOT / REPO，禁止再写死绝对路径：
  - ROOT = 本文件所在目录（产品工程根）
  - REPO = 原仓库根（含 mbdsdr_ai/core/mcp_server.py 的那一层）

仓库位置自动探测顺序：环境变量 MBDSDR_REPO → ROOT.parent.parent
（仓库内 web/product 的情形）→ ROOT.parent → ROOT.parent/MBDSDR
→ 开发机默认路径。探测不到就明确报错，不静默改用别的源。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _detect_repo() -> Path:
    env = os.environ.get("MBDSDR_REPO")
    candidates = [Path(env)] if env else []
    candidates += [
        ROOT.parent.parent,
        ROOT.parent,
        ROOT.parent / "MBDSDR",
        Path("D:/mbdsdr/MBDSDR"),
    ]
    for candidate in candidates:
        try:
            if (candidate / "mbdsdr_ai" / "core" / "mcp_server.py").is_file():
                return candidate.resolve()
        except OSError:
            continue
    raise RuntimeError(
        "找不到 MBDSDR 仓库（需含 mbdsdr_ai/core/mcp_server.py）；"
        "请设置环境变量 MBDSDR_REPO 指向仓库根目录"
    )


REPO = _detect_repo()

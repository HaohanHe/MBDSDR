# -*- coding: utf-8 -*-
"""构建 MBDSDR 产品前端：
1) 把 docs/**/*.md 编译成 src/70-docs-data.js（含三语译本合并）
2) 把 CSS + 分层 JS 内联成自包含单文件 dist/index.html

用法：python build/build.py
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO
DOCS = ROOT / "docs"
SRC = ROOT / "src"
DIST = ROOT / "dist"

GROUP_OF = {
    "README.md": "spec", "01-product-spec.md": "spec", "02-user-guide.md": "spec",
    "03-architecture.md": "arch", "04-i18n.md": "arch", "05-design-language.md": "arch",
    "06-compliance.md": "arch", "07-roadmap.md": "arch",
    "08-engine-mechanism-notes.md": "arch", "09-repo-alignment.md": "arch",
    "10-sky-catalog.md": "arch",
    "wiki/README.md": "wiki", "wiki/glossary.md": "ref", "wiki/faq.md": "ref",
}
GROUP_ORDER = {"spec": 0, "arch": 1, "wiki": 2, "scene": 3, "ref": 4}
GROUP_TITLE = {
    "spec": {"zh": "产品与规格", "ja": "製品・仕様", "en": "Product & Spec"},
    "arch": {"zh": "工程与架构", "ja": "エンジニアリング", "en": "Engineering"},
    "wiki": {"zh": "能力百科", "ja": "能力百科", "en": "Capability Wiki"},
    "scene": {"zh": "场景", "ja": "シナリオ", "en": "Scenarios"},
    "ref": {"zh": "参考与术语", "ja": "リファレンス", "en": "Reference"},
}


def title_of(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def group_of(rel: str) -> str:
    if rel in GROUP_OF:
        return GROUP_OF[rel]
    if rel.startswith("scenarios/"):
        return "scene"
    if rel.startswith("wiki/capabilities/"):
        return "wiki"
    return "ref"


def compile_docs():
    # 先跑生成器，保证 wiki 与 CAPS 元数据同步
    subprocess.run([sys.executable, str(ROOT / "build/gen_docs.py")], check=True)
    entries = []
    for path in sorted(DOCS.rglob("*.md")):
        rel = path.relative_to(DOCS).as_posix()
        if rel.endswith((".en.md", ".ja.md")):
            continue                              # 译本并入主条目
        stem = rel[:-3]
        en_path = DOCS / (stem + ".en.md")
        ja_path = DOCS / (stem + ".ja.md")
        zh = path.read_text(encoding="utf-8")
        en = en_path.read_text(encoding="utf-8") if en_path.is_file() else None
        ja = ja_path.read_text(encoding="utf-8") if ja_path.is_file() else None
        gid = group_of(rel)
        entries.append({
            "id": stem.replace("/", "-"),
            "group": gid,
            "path": rel,
            "title": {
                "zh": title_of(zh, stem),
                "ja": title_of(ja, stem) if ja else title_of(zh, stem),
                "en": title_of(en, stem) if en else title_of(zh, stem),
            },
            "body": {"zh": zh, **({"en": en} if en else {}), **({"ja": ja} if ja else {})},
        })
    entries.sort(key=lambda e: (GROUP_ORDER.get(e["group"], 9),
                                0 if e["id"] == "README" else 1, e["id"]))
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    payload = re.sub(r"</script", r"<\/script", payload, flags=re.I)  # 内联安全
    out = ("/* 由 build.py 生成：请勿手工编辑。源文档在 docs/。 */\n"
           "window.MBDSDR_DOC_DATA = " + payload + ";\n")
    (SRC / "70-docs-data.js").write_text(out, encoding="utf-8")
    print(f"[docs] {len(entries)} 篇文档 -> 70-docs-data.js ({len(out)} bytes)")
    return len(entries)


def build_dist():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    css = (SRC / "style.css").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="src/style.css">',
                        "<style>\n" + css + "\n</style>")
    scripts = re.findall(r'<script src="src/([^"]+)"></script>', html)
    if not scripts:
        raise RuntimeError("index.html 未找到分层脚本标签")
    for name in scripts:
        code = (SRC / name).read_text(encoding="utf-8")
        code = re.sub(r"</script", r"<\/script", code, flags=re.I)
        html = html.replace(f'<script src="src/{name}"></script>',
                            f"<script>\n{code}\n</script>")
    banner = ("<!-- MBDSDR product build · 自包含单文件 · "
              + datetime.now().strftime("%Y-%m-%d %H:%M") + " -->\n")
    DIST.mkdir(exist_ok=True)
    (DIST / "index.html").write_text(banner + html, encoding="utf-8")
    print(f"[dist] {len(scripts)} 个模块内联 -> dist/index.html "
          f"({(DIST / 'index.html').stat().st_size} bytes)")


if __name__ == "__main__":
    # 事实源优先：只读原仓库代码，刷新域/工具/令牌/端口/卫星目录
    subprocess.run([sys.executable, str(ROOT / "build/sync_from_repo.py")], check=True)
    # 全天星表事实源：HYG v4.1 本地缓存 + 仓库命名/连线/DSO，缺一即报错
    subprocess.run([sys.executable, str(ROOT / "build/sync_sky_catalog.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "build/scaffold.py")], check=True)
    compile_docs()
    build_dist()

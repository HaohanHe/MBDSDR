"""MBDSDR Web 产品工程 · 一键复现验收入口。

    /c/Users/xxhzb/AppData/Local/Programs/Python/Python314/python.exe run_all.py

全链：原仓库事实源同步 → 骨架/文档生成 → 单文件构建 → 静态守卫
      → 无头运行时 → 浏览器六态夹具 → 生成 VERIFY-REPORT.md。

只读原仓库（由 paths.py 探测或 MBDSDR_REPO 指定），不修改其中任何文件；
全部产物写在 output/mbdsdr-product/ 内。失败即停并如实报错。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from paths import ROOT, REPO
PY = sys.executable
NODE = shutil.which("node")
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")

STEPS = [
    ("仓库事实源同步", [PY, "build/sync_from_repo.py"]),
    ("全天星表同步", [PY, "build/sync_sky_catalog.py"]),
    ("骨架与样式", [PY, "build/scaffold.py"]),
    ("文档/wiki 生成", [PY, "build/gen_docs.py"]),
    ("单文件构建", [PY, "build/build.py"]),
    ("静态守卫", [PY, "tests/verify_product.py"]),
    ("天球投影自检", [NODE, "tests/proj_selftest.js"] if NODE else None),
    ("无头运行时", [NODE, "tests/verify_runtime.js"] if NODE else None),
    ("视觉夹具", [PY, "tests/make_visual_fixture.py"]),
]


def run(name: str, cmd: list[str] | None) -> tuple[bool, str]:
    if cmd is None or any(c is None for c in cmd):
        return False, "依赖缺失（node 未找到）"
    p = subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out.strip()


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    for name, cmd in STEPS:
        ok, out = run(name, cmd)
        tail = "\n".join(out.splitlines()[-4:])
        results.append((name, ok, tail))
        print(("  OK   " if ok else "  FAIL ") + name)
        if not ok:
            print(tail)
            break

    # 浏览器六态截图（可选：没有 Edge 就跳过，不算失败）
    shots: list[str] = []
    if EDGE.is_file() and all(ok for _, ok, _ in results):
        for v in ("sky", "skywide", "skyzoom", "skyback", "skytop", "skyground",
                  "pro", "ja", "docs", "ai", "caps", "caps2"):
            fixture = ROOT / "tests" / f"visual_{v}.html"
            dest = ROOT / "tests" / f"verify_{v}.png"
            if not fixture.is_file():
                continue
            subprocess.run([str(EDGE), "--headless=new", "--disable-gpu",
                            "--no-sandbox", "--hide-scrollbars",
                            "--virtual-time-budget=6000",
                            "--window-size=1440,900",
                            f"--screenshot={dest}", fixture.as_uri()],
                           capture_output=True, timeout=120)
            if dest.is_file():
                shots.append(dest.name)

    facts = json.loads((ROOT / "build/repo-facts.json").read_text(encoding="utf-8"))
    cat = json.loads((ROOT / "build/sky-catalog.json").read_text(encoding="utf-8"))
    cm = cat["meta"]
    # 投影切换阈值直接读自源码常量，报告不另存一份手写数字
    _cel = (ROOT / "src/31-celestial.js").read_text(encoding="utf-8")
    _m = re.search(r"PERSP_MAX_FOV\s*=\s*([\d.]+)", _cel)
    _persp_max = _m.group(1) if _m else "?"
    dist = ROOT / "dist/index.html"
    raw = dist.read_bytes()
    docs = sorted((ROOT / "docs").rglob("*.md"))

    lines = [
        "# MBDSDR Web 产品 · 一键复现验收报告",
        "",
        f"- 生成时间：{_dt.datetime.now().isoformat(timespec='seconds')}",
        f"- 事实源：{REPO}（只读，未修改）",
        f"- 单文件产物：`dist/index.html` {len(raw)} bytes · sha256 `{hashlib.sha256(raw).hexdigest()}`",
        f"- 工具域 {len(facts['domains'])} 个 · 工具→域映射 {facts.get('toolDomainCount', '?')} 条 · "
        f"写工具模式 {len(facts.get('writePatterns', []))} 条",
        f"- ControlHub 端口 `{facts['channels']['controlHub']['port']}` · "
        f"时间倍率 {len(facts['timeRates']['values'])} 档 · "
        f"中性站点 {facts['site']['default']['lat']}, {facts['site']['default']['lon']}"
        f"（trusted={facts['site']['default']['trusted']}）",
        f"- **全天星表 HYG v4.1**：{cm['starCount']:,} 颗（V ≤ {cm['hyg']['cutoff']}），"
        f"J2000，CC BY-SA 4.0 · sha256 `{cm['hyg']['sha256'][:16]}…`",
        f"- 天球：球心相机 → 视图空间 → 全屏平面投影（≤{_persp_max}° 针孔透视 / 以上立体投影鱼眼）"
        f" / 窗口矩形裁剪 / 地景绕 360° 方位 / 星位固定 J2000 / 缩放改 FOV / LST 刚性旋转",
        f"- 文档 {len(docs)} 篇 · i18n 三语 · 视觉截图 {len(shots)} 张",
        "",
        "## 步骤结果",
        "",
        "| 步骤 | 结果 | 末行输出 |",
        "|---|---|---|",
    ]
    for name, ok, tail in results:
        one = tail.replace("|", "\\|").replace("\n", " / ")[:180]
        lines.append(f"| {name} | {'PASS' if ok else 'FAIL'} | `{one}` |")
    lines += [
        "", "## 视觉态", "",
        "\n".join(f"- `{s}`" for s in shots) or "- （未生成：Edge 不可用或前序失败）",
        "",
        "## not_verified（明确未验证，勿当作已上线）", "",
        "- **真实硬件链路未接通**：本产品不直接读写 SDR；`ControlHub` 与 `arm_manual`"
        " 人工门只按原仓库代码常量建模为契约，未连接真实 C++ headless / Python 内核。",
        "- **频谱与瀑布无 IQ 输入**：专业模式显示诚实空态，未展示任何实时或合成谱线。",
        "- **卫星目录为离线演示条目**：TLE 与下行频率取自 `sat_tracker.py`；"
        "合成/凑整 TLE 不用于精确过境，站址未校准时不给可信过境结论。",
        "- **规划能力位**：6DOF 视觉引导 / 视觉校准 / VR·XR 在原仓库为设计项，"
        "UI 中标注「规划 · 未验证」且禁用。",
        "- **星表数据是真的**（HYG v4.1 下载 + SHA256），但坐标链为 J2000 直算 LST，"
        "未接 `celestial_geometry.py` 的完整岁差/章动/自行链。",
        "- **地景是示意轮廓**：仓库与产品都没有真实 360° 全景数据，地平高度场是确定性"
        "示意曲线（非实测地形、非照片），UI 已明写「示意轮廓」，不冒充实拍。",
        "- **太阳位置是低精度解析式**（Meeus 简化，约 ±0.5°）：只用于昼夜/晨昏蒙影判定，"
        "不用于日出时刻表或日食计算。",
        "- **FOV→极限星等**是自研视觉适应显示门限（设计项，未验证），"
        "非望远镜口径增益，也不代表该时刻肉眼可见下限。",
        "- **桌面端未联调**：未与 Qt6 天空模式 `rf_sky_view.py` 做端到端对拍；"
        "设计令牌与时间倍率为代码事实源同步，非运行时连线。",
        "- **截图为 headless 静态态**：用于版式验收，不代表真机交互或信号接收结果。",
        "- 许可证：UI 与文档 MIT；机制学习自 Stellarium Web Engine（AGPL-3.0），"
        "**未引入其任何代码**。",
        "",
        "## 复跑", "",
        "```", "python run_all.py", "```",
        "（在本目录执行，需 Python 3.14；Node.js 用于运行时烟测，Edge 仅用于截图）",
        "前置：Python 3.14（含标准库）、Node.js（运行时烟测）、"
        f"原仓库 `{REPO}` 可读；Edge 仅用于截图，缺失时跳过。",
        "", "任何一步失败即停；报告只记录真实执行结果，不伪造通过。",
    ]
    (ROOT / "VERIFY-REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    bad = [n for n, ok, _ in results if not ok]
    print("\nREPORT:", ROOT / "VERIFY-REPORT.md")
    if bad:
        print("FAILED:", ", ".join(bad))
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

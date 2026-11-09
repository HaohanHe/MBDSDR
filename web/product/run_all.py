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
    ("卫星轨迹同步", [PY, "build/sync_sat_tracks.py"]),
    ("天文框架金标准", [PY, "build/gen_astro_golden.py"]),
    ("恒星自行金标准", [PY, "build/gen_proper_motion_golden.py"]),
    ("骨架与样式", [PY, "build/scaffold.py"]),
    ("文档/wiki 生成", [PY, "build/gen_docs.py"]),
    ("单文件构建", [PY, "build/build.py"]),
    ("静态守卫", [PY, "tests/verify_product.py"]),
    ("性能基准", [NODE or "node", "tests/perf_bench.js"]),
    ("天文框架跨语言自检", [NODE, "tests/astro_selftest.js"] if NODE else None),
    ("恒星自行跨语言自检", [NODE, "tests/proper_motion_selftest.js"] if NODE else None),
    ("天球投影自检", [NODE, "tests/proj_selftest.js"] if NODE else None),
    ("无头运行时", [NODE, "tests/verify_runtime.js"] if NODE else None),
    ("视觉夹具", [PY, "tests/make_visual_fixture.py"]),
]


def run(name: str, cmd: list[str] | None) -> tuple[bool, str]:
    if cmd is None or any(c is None for c in cmd):
        return False, "依赖缺失（node 未找到）"
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        # 单步卡死（例如并发测试把 CPU 抢光）应记为 FAIL，不能让整条流水线
        # 抛异常崩掉——否则后面的步骤根本不跑，也看不到明确失败原因。
        return False, f"TIMEOUT after 300s: {' '.join(str(c) for c in cmd)}"
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
        f" / 窗口矩形裁剪 / 地景绕 360° 方位 / 星位 J2000 + HYG 实测自行推进到当前历元 / 缩放改 FOV / LST 刚性旋转",
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
        "- **真实硬件链路未接通**：本产品不直接读写 SDR；`ControlHub`（C++ 宿主写开关 "
        "`writeEnabled_`）与 Python 内核的一次性 `confirm_token` 门只按原仓库代码常量建模"
        "为契约，未连接真实 C++ headless / Python 内核，也未接入可签发 token 的外部确认通道。",
        "- **阶段 B 宿主注入未接通**：`mbdsdr_ai/web_bridge.py` 已把 `ToolRegistry`"
        " 映射成 `window.mbdsdrBridge` 形状并做 16 条单测对拍，但 QWebChannel / JS 全局注入、"
        " `confirm_channel` 接到 `mcp_server.call_tool` 均未实现；写工具恒返回 `gated`。",
        "- **频谱与瀑布无 IQ 输入**：专业模式显示诚实空态，未展示任何实时或合成谱线。",
        "- **卫星轨迹是真实 SGP4 传播**：TLE 取自 `sat_tracker.py::_BUILTIN_TLES`"
        "（静态快照），经仓库 IAU2006 逆岁差转 J2000；地心方向未做站址视差，"
        "站址未校准时不给可信过境结论。",
        "- **规划能力位**：6DOF 视觉引导 / 视觉校准 / VR·XR 在原仓库为设计项，"
        "UI 中标注「规划 · 未验证」且禁用。",
        "- **星表数据是真的**（HYG v4.1 下载 + SHA256）；坐标链已接仓库 "
        "`celestial_geometry.py` 的 IAU2006 岁差 + IAU2000B 简化章动 + Capitaine GMST，"
        "由 `build/gen_astro_golden.py` 与 `tests/astro_selftest.js` 跨语言对拍（3.3e-16）。"
        "**恒星自行已接 HYG 实测 pmra/pmdec**（线性 RA/Dec 近似推进到当前历元），"
        "由 `build/gen_proper_motion_golden.py`（skyfield 严格空间运动，独立无星历基准）"
        "与 `tests/proper_motion_selftest.js` 对拍：JS↔Python 线性紧钉扎 1e-9°，"
        "对 skyfield 残差实测 worst 记录于金标准 meta（显示级 ≤2″，亚像素）；"
        "线性近似忽略透视加速度/视差长期项，章动非完整 77 项，忽略 ΔT/大气折射/站址视差。",
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

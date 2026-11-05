# -*- coding: utf-8 -*-
"""生成可视化验收夹具：在 dist 单文件后追加一段按 ?v= 触发交互的脚本，
用 Edge/Chrome 无头模式逐个截图，验证第二形态、语言切换与文档中心。

用法：python tests/make_visual_fixture.py
"""
from pathlib import Path
import re
import subprocess

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO
dist = (ROOT / "dist/index.html").read_text(encoding="utf-8")
inject = """<script>
/* 可视化验收注入：仅本夹具使用，发布产物 dist/index.html 不含此段 */
(function () {
  const v = new URLSearchParams(location.search).get("v") || "sky";
  /* 无头截图会在 CSS 过渡中途取帧，导致两个形态半透明叠在一起；
     夹具里禁用过渡，只看最终稳定态（发布产物不受影响）。 */
  const st = document.createElement("style");
  st.textContent = ("*{animation:none !important;transition:none !important}"
                    + ".form,#aiDrawer,#capDrawer,#satCard,#sidebar{transition:none !important}");
  document.head.appendChild(st);
  setTimeout(function () {
    const lang = document.querySelector("#langSwitch");
    if (v === "pro") document.querySelector('[data-form="pro"]').click();
    if (v === "ja") lang.click();
    if (v === "en") { lang.click(); lang.click(); }
    if (v === "docs") document.querySelector("#docOpen").click();
    if (v === "caps") document.querySelector("#capHead").click();
    if (v === "caps2") {
      document.querySelector("#capHead").click();
      setTimeout(function () {
        document.querySelector("#capBody").scrollTop = 99999;
      }, 120);
    }
    if (v === "ai") {
      /* 程序化 .click() 在 pointer-events:none 的卡片上不冒泡，
         夹具直接走产品 API 打开协商（与用户点击同一条路径）。 */
      const MB = window.MBDSDR;
      if (MB && MB.dialog) { MB.sky.lockSat(MB.SATS[2]); MB.dialog.open("receive", MB.SATS[2]); }
    }
    /* 天球几何三态：全天空鱼眼 / 小视场针孔 / 转身看背后。
       用来证明「天空是球、屏幕是平面、地景绕 360°」，不是圆盘贴图。 */
    if (v === "skywide" || v === "skyzoom" || v === "skyback" ||
        v === "skytop" || v === "skyground") {
      const C = window.MBDSDR.celestial;
      if (v === "skywide") { C.V.fov = C.V.fovTarget = 158; }
      if (v === "skyzoom") { C.V.fov = C.V.fovTarget = 22; C.V.alt = C.V.altTarget = 6; }
      if (v === "skyback") { C.V.az = C.V.azTarget = 180; C.V.fov = C.V.fovTarget = 100; }
      /* 朝天顶：地平线在屏幕上是闭合大圆，中间天、四周地——
         这是「逐列只找上沿」会把边界拉成竖直硬边的现场。 */
      if (v === "skytop") {
        C.V.az = C.V.azTarget = 234; C.V.alt = C.V.altTarget = 89;
        C.V.fov = C.V.fovTarget = 160;
      }
      /* 朝地面：整屏都应是地景，天空一块都不该漏 */
      if (v === "skyground") {
        C.V.az = C.V.azTarget = 234; C.V.alt = C.V.altTarget = -35;
        C.V.fov = C.V.fovTarget = 100;
      }
    }
  }, 600);
})();
</script>
"""
out = dist.replace("</body>", inject + "</body>")
(ROOT / "tests/visual_check.html").write_text(out, encoding="utf-8")
print("fixture:", ROOT / "tests/visual_check.html", len(out), "bytes")

# file:// 下 Edge 对 ?v= 的处理不可靠（曾截到全是 sky 的假阴性）。
# 为每一态生成硬编码 v 的独立夹具，截图时不带任何 query。
needle = 'const v = new URLSearchParams(location.search).get("v") || "sky";'
assert needle in out, "v anchor not found"
for v in ("sky", "skywide", "skyzoom", "skyback", "skytop", "skyground",
          "pro", "ja", "en", "docs", "ai", "caps", "caps2"):
    page = out.replace(needle, f'const v = "{v}";', 1)
    (ROOT / "tests" / f"visual_{v}.html").write_text(page, encoding="utf-8")
print("standalone fixtures: visual_{sky,pro,ja,en,docs,ai,caps}.html")

# 自检：注入脚本必须在 node 下语法通过。曾经把 Python 的隐式字符串拼接
# 写进 JS（相邻字符串字面量）导致整段注入静默失效，六张截图全是首屏假阴性。
last_open = out.rfind("<script>")
last_close = out.rfind("</script>")
inject_js = out[last_open + len("<script>"):last_close]
check_path = ROOT / "tests" / "_fixture_inject.js"
check_path.write_text(inject_js, encoding="utf-8")
r = subprocess.run(["node", "--check", str(check_path)], capture_output=True, text=True)
if r.returncode:
    raise SystemExit("fixture inject script syntax error:\n" + r.stderr)
print("fixture inject script: node --check OK")

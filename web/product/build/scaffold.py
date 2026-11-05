# -*- coding: utf-8 -*-
"""从 v1 单文件原型抽取 CSS / HTML 骨架，生成 v2 产品工程开发态入口。

只做机械抽取与少量结构性插入；所有可见文案保留中文原文，由 20-i18n.js
在运行时按反查表替换（零文案丢失）。
"""
from pathlib import Path
import json

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO

# v1 原型骨架随产品工程一起入库（仓库内 web/product/prototype/），
# 不再依赖仓库外的 output/ui-prototype/。
SRC = ROOT / "prototype/index.html"

# 设计令牌单源：desktop/tokens.py 的 figma 主题，由 sync_from_repo.py 抽取
FACTS = json.loads((ROOT / "build/repo-facts.json").read_text(encoding="utf-8"))
FIG = FACTS["tokens"]["figma"]
RAD = FACTS["tokens"]["radius"]
SIZE = FACTS["tokens"].get("size", {})

html = SRC.read_text(encoding="utf-8")
css = html.split("<style>", 1)[1].split("</style>", 1)[0]
body = html.split("<body>", 1)[1].split("<script>", 1)[0]

# --- 搜索胶囊：从 skyForm 挪进 header ---
# .form 的 transform 会创建层叠上下文，胶囊 z-index 再高也盖不过 fixed 顶栏的
# backdrop-filter，占位文字会被整块模糊掉。放进 header 才是真正的顶中层。
pill = '''    <!-- 搜索胶囊 -->
    <div class="search-pill">
      <span class="ico">⌕</span>
      <input id="searchInput" placeholder="搜索卫星 / 天体…（试试 METEOR 或 ISS）">
    </div>
'''
assert pill in body, "search pill anchor not found"
body = body.replace(pill, "", 1)
hdr_anchor = '  <div class="hdr-right">'
assert hdr_anchor in body, "header anchor not found"
body = body.replace(hdr_anchor, pill.replace("    ", "  ", 1) + hdr_anchor, 1)

# --- 顶栏：语言切换 + 文档中心入口 ---
old_hdr = '    <span class="clock" id="clock">--:--:--</span>\n  </div>'
new_hdr = '''    <span class="clock" id="clock">--:--:--</span>
    <button class="hdr-btn" id="langSwitch" title="Language / 语言 / 言語">中文</button>
    <button class="hdr-btn" id="docOpen" title="文档 / Wiki">? 文档</button>
  </div>'''
assert old_hdr in body, "header anchor not found"
body = body.replace(old_hdr, new_hdr, 1)

# --- 侧栏：文档 / Wiki 入口 ---
old_sb = '      <div class="sb-item"><i>ⓘ</i>关于</div>'
new_sb = '''      <div class="sb-item" data-doc-open><i>▤</i>文档 / Wiki</div>
      <div class="sb-item"><i>ⓘ</i>关于</div>'''
assert old_sb in body, "sidebar anchor not found"
body = body.replace(old_sb, new_sb, 1)

# 能力矩阵默认折叠：展开态浮层不得压住左上信息卡
old_cap = '    <div id="capDrawer">'
assert old_cap in body, "capDrawer anchor not found"
body = body.replace(old_cap, '    <div id="capDrawer" class="folded">', 1)

# --- 原项目事实对齐：清掉初始假数据 / 中性站点 / 真实工具名 ---
# 旧骨架在 JS 启动前就显示「RTL-SDR 已连接 / 16 VCDU / 合成频谱」，违反
# AGENTS.md §2 诚实文化。容器保留，内容一律交给 JS 按真实数据源渲染。
FIX = [
    ('placeholder="搜索卫星 / 天体…（试试 METEOR 或 ISS）"',
     'placeholder="搜索卫星 / 天体…（试试 NOAA 或 ISS）"'),
    ('<div class="name" id="scName">NORAD 40069</div>',
     '<div class="name" id="scName"></div>'),
    ('<div class="sub" id="scSub">气象卫星 · METEOR-M2</div>',
     '<div class="sub" id="scSub"></div>'),
    ('<div class="sc-alias" id="scAlias">又称 <b>METEOR-M2</b> <b>Метеор-М №2</b> <b>2014-037A</b></div>',
     '<div class="sc-alias" id="scAlias"></div>'),
    ('<div class="corner-chip loc">丰满区 · 吉林</div>',
     '<div class="corner-chip loc">示例站点 · 0.0, 0.0</div>'),
    ('◧ 能力矩阵（模型可调用）', '◧ 能力矩阵（原仓库工具域）'),
    ('时间流速：1× → 60× → 600× → 静止', '时间流速：⏸ / ▶ / ×60 / ×300 / ×1800'),
    ('非固定流程 · 可开关', '事实源自动生成'),
    # 专业模式：设备/链路不再高亮成「已连接」，静态读数一律标演示口径
    ('<div class="tree-i on"><i>▦</i>RTL-SDR · 137 MHz</div>',
     '<div class="tree-i"><i>▦</i>RTL-SDR（未连接）</div>'),
    ('<div class="tree-i on"><i>≋</i>LRPT · QPSK 72k</div>',
     '<div class="tree-i"><i>≋</i>LRPT · QPSK 72k</div>'),
    ('<div class="spec-tag">频谱 · 瀑布</div>',
     '<div class="spec-tag">频谱 · 瀑布 <em>演示参数 · 非实时读数</em></div>'),
    ('<span>RMS <b id="rmsVal">-78.2</b> dB</span>',
     '<span>RMS <b id="rmsVal">—</b> dB</span>'),
    ('<span>CFO <b id="cfoVal">+0.31</b> Hz</span>',
     '<span>CFO <b id="cfoVal">—</b> Hz</span>'),
    ('<span>符号率 <b>72.0</b> ksym/s</span>',
     '<span>符号率 <b>—</b> ksym/s</span>'),
    ('          <div class="tree-i"><i>✦</i>get_satellite_pass</div>\n'
     '          <div class="tree-i"><i>✦</i>tune_sdr / point_gimbal</div>\n'
     '          <div class="tree-i"><i>✦</i>demodulate / decode_image</div>',
     '          <div class="tree-i"><i>✦</i>get_status / list_devices</div>\n'
     '          <div class="tree-i"><i>✦</i>set_frequency / read_spectrum</div>\n'
     '          <div class="tree-i"><i>✦</i>get_satellite_pass / sat_link_identify</div>'),
    ('''        <div id="proStream">
          <div class="msg model"><div class="who">MBDSDR 模型</div>
            专业模式就绪。当前链路：RTL-SDR → 137.100 MHz → LRPT QPSK 72k → Costas/Viterbi/RS。
            我可以接管调谐、解调、过境规划与追踪——你需要什么？</div>
          <div class="toolcard">tune_sdr(freq_hz: 137100000)<span class="st ok">可用</span></div>
          <div class="toolcard">point_gimbal(az, el)<span class="st off">无云台</span></div>
        </div>''', '        <div id="proStream"></div>'),
    ('''    <div class="status-bar">
      <span class="ok">● RTL-SDR 已连接</span><span class="sep">│</span>
      <span>解调 LRPT · Viterbi 0.12 s/帧</span><span class="sep">│</span>
      <span>RS 全纠正 16 VCDU · ASM 91</span><span class="sep">│</span>
      <span>能力矩阵 5/9 在位</span><span class="sep">│</span>
      <span id="cpuLine">CPU 12%</span><span class="sep">│</span>
      <span>MBDSDR · 能力而非流程</span>
    </div>''', '    <div class="status-bar"></div>'),
    # 星象仪 = 时空主导：真实全天星表 + 球面投影 + 坐标/时刻/FOV/极限星等读数。
    # 银河按钮改为「无数据·不提供」（原仓库 limitations L25/L28），不再画装饰带。
    ('''    <div class="sky-toolbar" id="skyToolbar">
      <button class="tb on" data-tb="orbits" title="轨道线">🛰</button>
      <button class="tb on" data-tb="labels" title="目标标签">A</button>
      <button class="tb on" data-tb="milkyway" title="银河">☁</button>
      <button class="tb on" data-tb="atmosphere" title="大气 / 地平辉光">☀</button>
      <button class="tb on" data-tb="landscape" title="地景（固定在地平系，不随时间转）">⛰</button>
      <button class="tb" data-tb="grid" title="赤纬网格">◎</button>
      <button class="tb" data-tb="night" title="夜视模式">☾</button>
      <button class="tb on" data-tb="sats" title="卫星层">✦</button>
      <button class="tb" data-tb="time" title="时间流速：⏸ / ▶ / ×60 / ×300 / ×1800">⏱<span id="rateTag" style="font-size:8px;margin-left:2px">1×</span></button>
      <button class="tb" data-tb="fullscreen" title="全屏">⛶</button>
    </div>''',
     '''    <div class="sky-toolbar" id="skyToolbar">
      <button class="tb on" data-tb="orbits" title="轨道线">🛰</button>
      <button class="tb on" data-tb="labels" title="目标标签">A</button>
      <button class="tb" data-tb="milkyway" title="银河（无数据·不提供）">☁</button>
      <button class="tb on" data-tb="atmosphere" title="大气 / 地平辉光">☀</button>
      <button class="tb on" data-tb="landscape" title="地景（固定在地平系，不随时间转）">⛰</button>
      <button class="tb on" data-tb="constellations" title="星座连线">⋈</button>
      <button class="tb on" data-tb="dso" title="DSO 类型符号">◈</button>
      <button class="tb" data-tb="grid" title="方位网格">◎</button>
      <button class="tb" data-tb="eqgrid" title="赤道网格">⊕</button>
      <button class="tb" data-tb="night" title="夜视模式">☾</button>
      <button class="tb on" data-tb="sats" title="卫星层">✦</button>
      <button class="tb on" data-tb="hud" title="时空读数">◷</button>
      <button class="tb" data-tb="time" title="时间流速：⏸ / ▶ / ×60 / ×300 / ×1800">⏱<span id="rateTag" style="font-size:8px;margin-left:2px">1×</span></button>
      <button class="tb" data-tb="fullscreen" title="全屏">⛶</button>
    </div>
    <div class="sky-hud" id="skyHud"></div>'''),
]
for old, new in FIX:
    assert old in body, "alignment anchor not found: " + old[:60]
    body = body.replace(old, new, 1)

# --- 全局文档中心抽屉（两种形态共用） ---
doc_panel = '''
<!-- ═══════════ 文档中心（wiki / 产品规格 / 场景） ═══════════ -->
<div id="docPanel">
  <div class="doc-side">
    <div class="doc-brand">MBDSDR<sup>DOCS</sup></div>
    <div class="doc-hint">产品文档 · 能力百科</div>
    <div id="docNav"></div>
  </div>
  <div class="doc-main">
    <div class="doc-top">
      <span id="docCrumb">文档中心</span>
      <button class="hdr-btn" id="docLang">中文</button>
      <button class="hdr-btn" id="docClose">✕</button>
    </div>
    <article id="docBody"></article>
  </div>
</div>
'''
body = body + doc_panel

# --- 追加样式（沿用 .figma token；色板由 desktop/tokens.py 单源注入） ---
css_vars = (
    ":root{\n"
    f"  --repo-bg:{FIG.get('bg', '#000000')};\n"
    f"  --repo-bg-alt:{FIG.get('bg_alt', '#1f1f1f')};\n"
    f"  --repo-card:{FIG.get('card', '#1f1f1f')};\n"
    f"  --repo-text:{FIG.get('text', '#FFFFFF')};\n"
    f"  --repo-text2:{FIG.get('text_secondary', '#CFCFCF')};\n"
    f"  --repo-border:{FIG.get('border', '#404040')};\n"
    f"  --repo-button:{FIG.get('button', '#2C2C2C')};\n"
    f"  --repo-primary:{FIG.get('primary', '#D9D9D9')};\n"
    f"  --repo-card-lg:{RAD.get('card_lg', 24)}px;\n"
    f"  --repo-card-sm:{RAD.get('card_sm', 8)}px;\n"
    f"  --repo-dock-icon:{RAD.get('dock_icon', 13)}px;\n"
    "}\n"
)
extra_css = css_vars + '''
  /* ===== v2：语言切换 / 文档中心（沿用 .figma 设计语言） ===== */
  .hdr-btn{font-family:var(--sans);font-size:11px;letter-spacing:.12em;color:var(--txt2);
    background:rgba(28,28,28,.7);border:1px solid var(--line);border-radius:84px;
    padding:6px 14px;cursor:pointer;transition:all .3s ease}
  .hdr-btn:hover{background:#fff;color:#000;border-color:#fff}
  #docPanel{position:fixed;inset:0;z-index:120;display:none;background:rgba(0,0,0,.94);
    backdrop-filter:blur(18px);grid-template-columns:270px 1fr}
  #docPanel.open{display:grid}
  .doc-side{border-right:1px solid var(--line);padding:26px 18px;overflow:auto;
    background:#0a0a0a}
  .doc-brand{font-size:17px;font-weight:700;letter-spacing:.16em}
  .doc-brand sup{font-size:8px;letter-spacing:.2em;color:var(--txt3);margin-left:4px}
  .doc-hint{font-size:10px;color:var(--txt3);letter-spacing:.24em;margin:8px 0 24px}
  .doc-group{font-size:9.5px;color:var(--txt3);letter-spacing:.28em;margin:18px 0 8px 6px}
  .doc-link{display:block;width:100%;text-align:left;font-family:var(--sans);font-size:12px;
    color:var(--txt2);background:transparent;border:0;border-radius:13px;padding:9px 12px;
    cursor:pointer;transition:all .25s ease}
  .doc-link:hover,.doc-link.on{background:var(--chip);color:#fff}
  .doc-main{display:flex;flex-direction:column;min-width:0}
  .doc-top{display:flex;align-items:center;gap:10px;height:56px;padding:0 26px;
    border-bottom:1px solid var(--line);font-size:11px;color:var(--txt3);letter-spacing:.2em}
  .doc-top span{margin-right:auto}
  #docBody{flex:1;overflow:auto;padding:34px 8vw 90px;max-width:1000px;line-height:1.85;
    font-size:13.5px;color:var(--txt2)}
  #docBody h1{font-size:26px;color:#fff;letter-spacing:.06em;margin:0 0 6px}
  #docBody h2{font-size:17px;color:#fff;margin:34px 0 12px;padding-top:18px;
    border-top:1px solid var(--line);letter-spacing:.05em}
  #docBody h3{font-size:14px;color:#fff;margin:22px 0 8px}
  #docBody p{margin:10px 0}
  #docBody ul,#docBody ol{margin:10px 0 10px 22px}
  #docBody li{margin:5px 0}
  #docBody code{font-family:var(--mono);font-size:12px;color:#fff;background:#202020;
    padding:2px 6px;border-radius:3px}
  #docBody pre{background:#141414;border:1px solid var(--line);border-radius:13px;
    padding:14px 16px;overflow:auto;font-family:var(--mono);font-size:11.5px;line-height:1.6}
  #docBody pre code{background:transparent;padding:0}
  #docBody blockquote{border-left:2px solid var(--txt3);padding-left:16px;margin:14px 0;
    color:var(--txt3)}
  #docBody table{border-collapse:collapse;width:100%;margin:14px 0;font-size:12px}
  #docBody th,#docBody td{border:1px solid var(--line);padding:7px 10px;text-align:left}
  #docBody th{color:#fff;background:#171717}
  #docBody a{color:#fff;text-decoration:underline;text-underline-offset:3px}
  #docBody hr{border:0;border-top:1px solid var(--line);margin:26px 0}
  #docBody .doc-meta{font-size:10px;color:var(--txt3);letter-spacing:.2em;margin-bottom:24px}
  @media(max-width:820px){#docPanel{grid-template-columns:1fr}.doc-side{display:none}}

  /* ===== v2 布局补丁（幂等覆盖）=====
     注意：src/style.css 由本脚本生成，定制规则必须写在这里，
     直接改生成物会在下次重跑 scaffold 时被冲掉。 */
  .search-pill input::placeholder{color:rgba(255,255,255,.72)}
  /* 顶栏 z-index 60 会压暗居中搜索胶囊；抬到其上（胶囊已移入 header DOM） */
  .search-pill{z-index:70}
  body:not(.sky-immersive) .search-pill{display:none}
  .sc-actions .btn:last-child{grid-column:span 2}
  #capDrawer{left:auto;right:18px}
  #capDrawer.folded{width:236px}
  #capDrawer.folded .cap-head{white-space:nowrap}
  #capDrawer.folded .cap-head .badge{display:none}
  @media(max-width:820px){
    #capDrawer{right:12px;left:12px;width:auto}
    #capDrawer.folded{width:auto}
  }

  /* ===== 原仓库事实对齐后的能力矩阵 ===== */
  #capDrawer{background:var(--repo-card);border:1px solid var(--repo-border)}
  .doc-side{background:var(--repo-bg-alt)}
  #capBody{max-height:56vh;overflow:auto}
  .cap .tag{display:inline-block;margin-left:8px;padding:1px 6px;font-size:9px;
    letter-spacing:.1em;color:var(--repo-text2);border:1px solid var(--repo-border);
    border-radius:var(--repo-dock-icon);vertical-align:middle}
  .cap.planned{opacity:.62;cursor:not-allowed}
  .cap.planned .sw{opacity:.35}
  .cap .n{white-space:normal;line-height:1.35}
  .cap .m{white-space:normal;word-break:break-all}
  .spec-tag em{font-style:normal;font-size:9.5px;letter-spacing:.16em;
    color:var(--repo-text2);margin-left:10px;opacity:.72}
  /* 顶栏高来自 desktop/tokens.py SIZE.topbar_h：抽屉从顶栏下方展开，头像不被盖 */
  #aiDrawer{top:__TOPBAR__px}
  /* 工具卡的「离线 · 未伪装成功」是诚实口径的核心证据，不能用 opacity 层级糊掉 */
  .toolcard{border-color:rgba(255,255,255,.42);color:rgba(255,255,255,.9)}
  .toolcard .st.off{color:rgba(255,255,255,.78);border-color:rgba(255,255,255,.42)}
  /* 时空主导 HUD：星象仪模式的坐标/时刻/FOV/极限星等读数 */
  .sky-hud{position:absolute;top:__TOPBAR2__px;right:18px;width:286px;z-index:22;
    padding:13px 16px;border-radius:24px;border:1px solid var(--line);
    background:rgba(16,16,16,.76);backdrop-filter:blur(19px);box-shadow:var(--shadow);
    color:var(--txt2);font:10px/1.62 var(--mono);white-space:pre-line;
    letter-spacing:.02em;pointer-events:none}
  .sky-hud::first-line{color:#fff;font-weight:600;letter-spacing:.14em}
  @media(max-width:820px){.sky-hud{display:none}}
''' .replace("__TOPBAR__", str(SIZE.get("topbar_h", 56))).replace("__TOPBAR2__", str(SIZE.get("topbar_h", 56) + 16))

(ROOT / "src").mkdir(exist_ok=True)
(ROOT / "src/style.css").write_text(
    "/* 由 build/scaffold.py 生成；定制补丁写在脚本的 extra_css，勿直接改本文件 */\n"
    + css.rstrip() + "\n" + extra_css, encoding="utf-8")

scripts = [
    # 12 必须在 31 之前（天球引擎读 MB.SKYCAT）；31 必须在 30 之前（绘制层读 MB.celestial）
    "00-core.js", "05-repo-facts.js", "10-data.js", "12-sky-catalog.js",
    "13-sat-tracks.js",
    "20-i18n.js", "31-celestial.js", "30-sky.js",
    "40-workbench.js", "50-dialog.js",
    "70-docs-data.js", "70-docs.js", "60-app.js",
]
script_tags = "\n".join(f'<script src="src/{s}"></script>' for s in scripts)
head = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MBDSDR · 时空操作台 — 能力，而非流程</title>
<link rel="stylesheet" href="src/style.css">
</head>
<body>
'''
tail = "\n" + script_tags + "\n</body>\n</html>\n"
(ROOT / "index.html").write_text(head + body + tail, encoding="utf-8")
print("style.css bytes:", len(css) + len(extra_css))
print("index.html bytes:", len(head + body + tail))
print("scripts:", ", ".join(scripts))

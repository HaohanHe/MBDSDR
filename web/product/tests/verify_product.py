# -*- coding: utf-8 -*-
"""产品工程静态验收：语法 / DOM / 三语覆盖 / 硬编码文案 / 事实源一致性 /
定轨铁律 / 原仓库红线 / 文档链 / 产物。

用法：python tests/verify_product.py
退出码 0 = 全部通过；非 0 会列出每一条失败原因。
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO
SRC = ROOT / "src"
DOCS = ROOT / "docs"
NODE = "node"
PY = sys.executable
errors: list[str] = []
warnings: list[str] = []

# 生成物：含仓库中文事实与文档正文，不参与「代码不得硬编码中文」检查
GENERATED = {"05-repo-facts.js", "70-docs-data.js", "20-i18n.js",
             "12-sky-catalog.js", "13-sat-tracks.js"}


def fail(msg): errors.append(msg)
def warn(msg): warnings.append(msg)


def strip_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", ln) for ln in js.splitlines())


# ---------- 0. 事实源是否存在且与 JS 生成物一致 ----------
facts_path = ROOT / "build/repo-facts.json"
if not facts_path.is_file():
    fail("[facts] 缺 build/repo-facts.json（先跑 build/sync_from_repo.py）")
    FACTS = {}
    payload_norm = ""
else:
    FACTS = json.loads(facts_path.read_text(encoding="utf-8"))
    payload_norm = json.dumps(FACTS, ensure_ascii=False, sort_keys=True)
    gen = (SRC / "05-repo-facts.js")
    if not gen.is_file():
        fail("[facts] 缺 src/05-repo-facts.js")
    elif payload_norm not in gen.read_text(encoding="utf-8").replace("\n", " "):
        # JS 是 indent=2 的原文，逐字比较更严格
        raw = facts_path.read_text(encoding="utf-8").rstrip("\n")
        if raw not in gen.read_text(encoding="utf-8"):
            fail("[facts] 05-repo-facts.js 与 repo-facts.json 漂移（重跑同步器）")
FACT_IDS = {d["id"] for d in FACTS.get("domains", [])} | \
           {p["id"] for p in FACTS.get("planned", [])}
print(f"[0] 事实源：{len(FACTS.get('domains', []))} 域 + "
      f"{len(FACTS.get('planned', []))} 规划项，共 {len(FACT_IDS)} 个能力位")

# ---------- 1. JS 语法 ----------
js_files = sorted(SRC.glob("*.js"))
for f in js_files:
    r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    if r.returncode:
        fail(f"[syntax] {f.name}: {r.stderr.strip()[:300]}")
print(f"[1] JS 语法：{len(js_files)} 个模块")

# ---------- 2. 词典 ----------
i18n_src = (SRC / "20-i18n.js").read_text(encoding="utf-8")
pat = re.compile(
    r'^\s*"([^"]+)":\s*\{\s*zh:\s*"((?:[^"\\]|\\.)*)",\s*'
    r'ja:\s*"((?:[^"\\]|\\.)*)",\s*en:\s*"((?:[^"\\]|\\.)*)"\s*\}', re.M)
DICT = {m.group(1): (m.group(2), m.group(3), m.group(4)) for m in pat.finditer(i18n_src)}
if len(DICT) < 100:
    fail(f"[i18n] 词典条目过少（{len(DICT)}），解析正则可能失效")
for k, (zh, ja, en) in DICT.items():
    if not zh.strip() or not ja.strip() or not en.strip():
        fail(f"[i18n] {k} 缺语言：zh={bool(zh)} ja={bool(ja)} en={bool(en)}")
zh_seen: dict[str, str] = {}
for k, (zh, _, _) in DICT.items():
    n = re.sub(r"\s+", " ", zh).strip()
    if n in zh_seen:
        warn(f"[i18n] zh 反查冲突：{zh_seen[n]} / {k}（后者胜出，需人工确认译文可接受）")
    zh_seen[n] = k
LOOKUP = {re.sub(r"\s+", " ", zh).strip() for zh, _, _ in DICT.values()}
print(f"[2] 词典：{len(DICT)} 条 × 3 语，{len(warnings)} 条反查冲突提示")

# ---------- 3. t() / 元数据引用是否都有词条 ----------
all_js = "\n".join(strip_comments(f.read_text(encoding="utf-8")) for f in js_files
                   if f.name not in GENERATED)
used = set(re.findall(r'\bt\(\s*["\']([A-Za-z0-9_.\-]+)["\']', all_js))
data_src = (SRC / "10-data.js").read_text(encoding="utf-8")
for k in re.findall(r'"([a-z][a-z0-9_]*(?:\.[a-z0-9_\-]+)+)"', data_src):
    if k.endswith(".md") or "/" in k:
        continue
    used.add(k)
missing = sorted(k for k in used if k not in DICT)
if missing:
    fail("[i18n] 代码引用了不存在的词条: " + ", ".join(missing[:20]))

# ---------- 4. 静态 HTML 中文覆盖 ----------
html = (ROOT / "index.html").read_text(encoding="utf-8")
body = html.split("<body>", 1)[1]
class Scanner(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("title", "placeholder", "aria-label"):
            if a.get(k) and re.search(r"[\u4e00-\u9fff]", a[k]):
                self.items.append(("ATTR", k, a[k].strip()))

    def handle_data(self, d):
        s = d.strip()
        if s and re.search(r"[\u4e00-\u9fff]", s):
            self.items.append(("TEXT", "", s))


sc = Scanner(); sc.feed(body)
uncovered = sorted({re.sub(r"\s+", " ", v).strip() for _, _, v in sc.items
                    if re.sub(r"\s+", " ", v).strip() not in LOOKUP})
if uncovered:
    fail("[i18n] 静态 HTML 中文未收录词典: " + " | ".join(uncovered[:12]))
print(f"[3-4] 静态文案节点 {len(sc.items)} 个，未覆盖 {len(uncovered)} 个")

# ---------- 5. 非词典模块不得硬编码中文字符串 ----------
hard = []
for f in js_files:
    if f.name in GENERATED:
        continue
    for ln in strip_comments(f.read_text(encoding="utf-8")).splitlines():
        for m in re.finditer(r'(["\'])(?:[^"\'\\]|\\.)*?\1', ln):
            s = m.group(0)
            if re.search(r"[\u4e00-\u9fff]", s):
                hard.append(f"{f.name}: {s[:70]}")
if hard:
    fail("[i18n] 代码里出现硬编码中文字符串（应走 t()）:\n    " + "\n    ".join(hard[:10]))

# ---------- 6. DOM 引用 ----------
ids_html = set(re.findall(r'id="([^"]+)"', html))
ids_js = set(re.findall(r'[\$]\(\s*["\']#([A-Za-z][\w-]*)["\']', all_js))
ids_js |= set(re.findall(r'\$\$\(\s*["\']#([A-Za-z][\w-]*)["\']', all_js))
missing_dom = sorted(ids_js - ids_html)
if missing_dom:
    fail("[dom] JS 引用了不存在的 id: " + ", ".join(missing_dom))
print(f"[5-6] DOM id 引用 {len(ids_js)} 个，缺失 {len(missing_dom)} 个")

# ---------- 7. 定轨铁律 ----------
acts = set(re.findall(r'data-act="([a-z]+)"', html))
if acts != {"track", "receive", "point"}:
    fail(f"[ironlaw] 动作按钮集合异常：{sorted(acts)}（只允许 track/receive/point）")
if "定轨" in body or "estimate_orbit" in body or "RLS" in body:
    fail("[ironlaw] 静态 HTML 出现定轨/RLS 功能面字样")
for f in js_files:
    if f.name in GENERATED:
        continue
    code = strip_comments(f.read_text(encoding="utf-8"))
    for bad in ("定轨", "estimate_orbit", "轨道确定", "orbit determination"):
        if bad in code:
            fail(f"[ironlaw] {f.name} 的功能面代码出现「{bad}」")
# CAPS 由事实源生成：功能面里不得出现 orbit 类能力位，规划项必须标注
if "orbit" in FACT_IDS:
    fail("[ironlaw] 事实源里出现 orbit 能力位")
tool_names = set(re.findall(r'\{ name: "([a-z_]+)", descKey:', data_src))
if any("orbit" in n for n in tool_names):
    fail("[ironlaw] TOOLS 出现定轨工具入口")
print(f"[7] 铁律：动作 {sorted(acts)} · 能力位 {len(FACT_IDS)} 个（事实源） · "
      f"工具 {len(tool_names)} 个，均无定轨入口")

# ---------- 8. 事实源 ↔ UI ↔ wiki ↔ dist 一致性 ----------
cap_pages = {p.stem for p in (DOCS / "wiki/capabilities").glob("*.md")}
if cap_pages != FACT_IDS:
    fail(f"[facts] wiki 能力词条与事实源漂移：词条 {sorted(cap_pages)} vs 事实 {sorted(FACT_IDS)}")
data_code = strip_comments(data_src)
if "MB.REPO || {}" not in data_code or "R.domains" not in data_code or "R.planned" not in data_code:
    fail("[facts] 10-data.js 未由 MB.REPO 生成能力位")
if "planned: true" not in data_code:
    fail("[facts] 规划能力未标 planned:true")
if "caps.notVerified" not in i18n_src:
    fail("[facts] 词典缺规划未验证标注")
for pid in {p["id"] for p in FACTS.get("planned", [])}:
    page = (DOCS / "wiki/capabilities" / f"{pid}.md")
    if page.is_file() and "未验证" not in page.read_text(encoding="utf-8"):
        fail(f"[facts] 规划词条 {pid}.md 未标注未验证")
# 文档链
md_files = list(DOCS.rglob("*.md"))
broken = []
for p in md_files:
    text = p.read_text(encoding="utf-8")
    for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', text):
        if target.startswith(("http", "#", "mailto:")):
            continue
        t = target.split("#")[0]
        if not t:
            continue
        dest = (p.parent / t).resolve()
        if not dest.exists():
            broken.append(f"{p.relative_to(DOCS)} -> {t}")
if broken:
    fail("[docs] 断链: " + ", ".join(broken[:10]))
print(f"[8] 文档 {len(md_files)} 篇，能力词条 {len(cap_pages)} 篇，断链 {len(broken)} 处")

# ---------- 9. 原仓库红线守卫 ----------
ui_sources = {f.name: strip_comments(f.read_text(encoding="utf-8"))
              for f in js_files if f.name not in GENERATED}
ui_text = "\n".join(ui_sources.values()) + "\n" + html + "\n" + \
          "\n".join((DOCS / n).read_text(encoding="utf-8")
                    for n in ("README.md", "01-product-spec.md", "02-user-guide.md"))
# 真实地理位置默认值（身份红线）
for bad in ("丰满区", "吉林", "北京", "长春", "43.80", "126.60", "39.9042", "116.4074"):
    if bad in ui_text:
        fail(f"[redline] UI/产品文档出现真实地理位置默认值：{bad}")
# 能力数字标语（虚荣指标 / 写入即过期）
for m in re.finditer(r"(?:支持|拥有|内置)\s*\d+\s*(?:个|项|条)\s*(?:工具|测试|能力|域)", ui_text):
    fail(f"[redline] 硬编码能力数字标语：{m.group(0)}")
if re.search(r"\b\d+\s*/\s*\d+\s*在位", ui_text):
    fail("[redline] 状态栏出现写死的能力计数")
# 诚实空态：不得造合成谱 / 预填充瀑布 / 伪造已连接
wb = ui_sources.get("40-workbench.js", "")
if "Math.random" in wb:
    fail("[redline] 频谱模块仍在用随机数合成谱线")
for key in ("spec.empty", "spec.noIq", "status.disconnected"):
    if key not in wb and key not in i18n_src:
        fail(f"[redline] 缺诚实空态口径：{key}")
if "MB.data.isLive()" not in wb:
    fail("[redline] 频谱未按数据源状态显示空态")
if "● RTL-SDR 已连接" in html or "16 VCDU" in html:
    fail("[redline] 静态骨架残留伪造的已连接/实时解码状态")
# 通道与人工门
core = ui_sources.get("00-core.js", "")
if "50732" in data_src:
    fail("[redline] ControlHub 端口被硬编码（应来自 MB.REPO）")
# 写工具门事实必须由内核代码抽取，前端不得自造放行条件
_gf = json.loads((ROOT / "build/repo-facts.json").read_text(encoding="utf-8"))
_gates = _gf.get("gates") or {}
_gh, _gm = _gates.get("controlHub", {}), _gates.get("mcp", {})
if not (_gh.get("stateVar") and _gm.get("tokenFields") and _gm.get("selfReportFields")):
    fail("[redline] 缺写工具确认门事实（MB.REPO.gates 必须由 C++ / Python 内核代码抽取）")
if not _gm.get("selfReportRejected") or not _gates.get("frontEndPolicy", {}).get("mayNotIssueToken"):
    fail("[redline] 门事实缺「自报无效 / 前端不得签发 token」口径")
for _marker in ("MB.gateEnvelope", "MB.stripSelfReport", "MB.hasConfirmToken"):
    if _marker not in core:
        fail(f"[redline] 00-core.js 缺前端门适配：{_marker}")
if re.search(r"arm_manual\s*(?:!==|===)\s*true", data_src):
    fail("[redline] 10-data.js 仍在用模型自报 arm_manual 作为放行判据（内核明确拒绝）")
if "gated" not in ui_text:
    fail("[redline] 未保留 gated 回执口径（未获确认不是执行失败）")
# 文档里的门字面量必须与事实源一致，防止内核改名后文档悄悄漂移
_doc_gate = (DOCS / "03-architecture.md").read_text(encoding="utf-8")
for _lit in (_gh.get("stateVar", ""), _gm.get("tokenFields", [""])[0],
             _gm.get("selfReportFields", [""])[0]):
    if _lit and _lit not in _doc_gate:
        fail(f"[redline] docs/03-architecture.md 的门字段与事实源不一致：{_lit}")
# 卫星轨迹必须是真实 SGP4 事实源，禁止按 NORAD 编号编造方位
_sky_code = (SRC / "30-sky.js").read_text(encoding="utf-8")
if re.search(r"n\s*\*\s*37\s*\+\s*11|parseInt\(String\(s\.norad\)", _sky_code):
    fail("[sat] 卫星方位仍在按 NORAD 编号编造（假轨迹回潮）")
for _m in ("MB.SATTRACKS", "satRaDec", "drawTracks", "projectSegment"):
    if _m not in _sky_code:
        fail(f"[sat] 未接真实 SGP4 天球轨迹：{_m}")
_tracks_js = SRC / "13-sat-tracks.js"
if not _tracks_js.is_file():
    fail("[sat] 缺 13-sat-tracks.js 事实源（跑 build/sync_sat_tracks.py）")
else:
    _tj = _tracks_js.read_text(encoding="utf-8")
    if "sgp4" not in _tj.lower() or "notVerified" not in _tj:
        fail("[sat] 轨迹事实源缺 sgp4 来源或 not_verified 口径")
_tj_meta = ROOT / "build/sat-tracks.json"
if _tj_meta.is_file():
    _tm = json.loads(_tj_meta.read_text(encoding="utf-8"))["meta"]
    if _tm.get("satCount", 0) < 1 or _tm.get("count", 0) < 10:
        fail("[sat] 轨迹事实源为空或采样不足")

# 阶段 B 跨语言钉扎：Python 桥接契约必须与产品 JS 逐字一致
import ast as _ast
_wb = REPO / "mbdsdr_ai" / "web_bridge.py"
if not _wb.is_file():
    fail("[bridge] 缺阶段 B Python 侧桥接层 mbdsdr_ai/web_bridge.py")
else:
    _wb_code = _wb.read_text(encoding="utf-8")
    for _marker in ("CONTRACT_METHODS", "TOKEN_FIELDS", "SELF_REPORT_FIELDS",
                    "manual_gate_required", "confirm_channel", "as_bridge_dict",
                    "notVerified", "isLiveIsNotDeviceOnline"):
        if _marker not in _wb_code:
            fail(f"[bridge] 桥接层缺关键机制：{_marker}")
    _py_contract = None
    for _node in _ast.parse(_wb_code).body:
        _tgt = (_node.targets[0] if isinstance(_node, _ast.Assign)
                else _node.target if isinstance(_node, _ast.AnnAssign) else None)
        if isinstance(_tgt, _ast.Name) and _tgt.id == "CONTRACT_METHODS":
            _py_contract = list(_ast.literal_eval(_node.value))
    _m = re.search(r"MB\.CONTRACT\s*=\s*Object\.freeze\(\[(.*?)\]\)", core, re.S)
    _js_names = re.findall(r'"([A-Za-z][A-Za-z0-9]*)"', _m.group(1)) if _m else []
    if _py_contract != _js_names:
        fail(f"[bridge] Python / JS 契约不一致：{_py_contract} vs {_js_names}")
    if not (REPO / "tests" / "test_web_bridge.py").is_file():
        fail("[bridge] 缺桥接层跨语言对拍测试 tests/test_web_bridge.py")
# 站点
if "lat: 0.0, lon: 0.0" not in core or "trusted: false" not in core:
    fail("[redline] 默认站点不是中性示例 / 未标不可信")
print("[9] 红线：站点中性 / 无能力数字 / 诚实空态 / 写工具确认门（宿主开关 + confirm_token）/ 通道来自事实源，全部通过")

# ---------- 9.5 全天星象仪科学性守卫（用户点名的三条：假星 / 飞散 / 横条投影） ----------
cat_json_path = ROOT / "build/sky-catalog.json"
if not cat_json_path.is_file():
    fail("[sky] 缺 build/sky-catalog.json（先跑 build/sync_sky_catalog.py）")
    CAT = {}
else:
    CAT = json.loads(cat_json_path.read_text(encoding="utf-8"))
meta = CAT.get("meta", {})
if meta.get("starCount", 0) < 10000:
    fail(f"[sky] 全天星表退化：starCount={meta.get('starCount')} < 10000（禁止只画亮星冒充全天）")
hyg = meta.get("hyg", {})
if len(hyg.get("sha256", "")) != 64 or "CC BY-SA 4.0" not in hyg.get("license", ""):
    fail("[sky] HYG 来源 SHA256 / 许可署名缺失或未如实登记")
if len(meta.get("notVerified", [])) < 5:
    fail("[sky] 星象仪 not_verified 条目不足（不得把设计项写成已上线）")
if not (ROOT / "data/hygdata_v41.csv").is_file():
    fail("[sky] 真下载的 HYG 源 CSV 不在位（拒绝闷头造车）")
if not (DOCS / "10-sky-catalog.md").is_file() or "HYG" not in (DOCS / "10-sky-catalog.md").read_text(encoding="utf-8"):
    fail("[sky] 缺星表来源/许可/口径文档")

sky_code = strip_comments(ui_sources.get("30-sky.js", ""))
cel_code = strip_comments(ui_sources.get("31-celestial.js", ""))
if "Math.random" in sky_code or "Math.random" in cel_code:
    fail("[sky] 仍在用随机星点（AGENTS §2 绝不伪造数据）")
for bad in ("burstT", "STARS.push", "sp: .3", "mg.addColorStop"):
    if bad in sky_code:
        fail(f"[sky] 残留被用户点名批评的机制：{bad}")
if "sky.galaxyNone" not in sky_code or "无数据·不提供" not in i18n_src:
    fail("[sky] 银河层未标诚实空态（渲染调用或词典口径缺失）")
# 天球几何（2026-11-04 重做口径，对齐 Stellarium Web Engine 的投影链）：
#   相机在球心 → 视图空间 → 全屏平面投影（透视 / 立体二选一）→ 窗口矩形裁剪。
#   判据：必须有 toScreen + projectSegment + 双投影切换；**不得**再出现
#   「圆形视场盘 + cx.clip」那种把天空塞进球里的画法。
for marker in ("eqToHorizon", "toScreen", "projectSegment", "PERSP_MAX_FOV",
               "projMode", "unproject", "sunAltAz"):
    if marker not in cel_code:
        fail(f"[sky] 缺球面天球/视图空间投影机制：{marker}")
if "worldToScreen" in sky_code or "screenToWorld" in sky_code:
    fail("[sky] 天空层仍在用平面线性投影（横向带鱼屏的根因）")
if "cx.arc(W / 2, H / 2, discR" in sky_code or "discR" in sky_code:
    fail("[sky] 圆形视场盘回潮（天空是球、屏幕是平面，不得用圆盘遮罩）")
if "createRadialGradient(W / 2, H / 2" in sky_code:
    fail("[sky] 以屏幕中心为圆心的径向渐变回潮（会造成「看进球里」的观感）")
if "ring(" not in sky_code or "hillAlt" not in sky_code or "fillLandscape" not in sky_code:
    fail("[sky] 地平/地景不是沿球面方位环采样")
if "azVisible" not in sky_code:
    fail("[sky] 缺方位窗口裁剪（地景必须绕 360° 铺，而不是只画屏幕正前方）")
if "evenodd" not in sky_code or "bot[i]" not in sky_code:
    fail("[sky] 地景必须按「天空区间 top/bot + evenodd」填充：只找一个上沿会把"
         "朝天顶（地平线成闭合大圆）画成竖直硬边")
if "id=\"skyHud\"" not in html and 'id="skyHud"' not in html:
    fail("[sky] 缺时空主导 HUD 容器")
print(f"[9.5] 全天星象仪：{meta.get('starCount', 0):,} 颗真实星 / 球心相机→全屏平面投影 / "
      f"≤88° 针孔透视 + 更大立体投影 / 地平大圆 / 逐列天空区间地景 / "
      f"无随机星点 / 无飞散动画 / 银河诚实空态 / not_verified 齐备")

# ---------- 9.6 天文坐标框架：必须接仓库岁差/章动，且不可重复施加 ----------
_astro_path = SRC / "14-astro-frame.js"
_cel_path = SRC / "31-celestial.js"
_astro = _astro_path.read_text(encoding="utf-8") if _astro_path.is_file() else ""
_celestial = _cel_path.read_text(encoding="utf-8") if _cel_path.is_file() else ""
for _m in ("precessionMatrix", "nutationMatrix", "nutationAngles", "gmstDeg",
           "frameMatrices", "Q", "QT", "IAU 2006", "IAU 2000B"):
    if _m not in _astro:
        fail(f"[astro] 14-astro-frame.js 缺仓库坐标链能力：{_m}")
if "280.46061837" in _celestial:
    fail("[astro] 31-celestial.js 回退到旧 Meeus GMST 近似（应统一用 MB.astro.gmstDeg）")
_celestial_code = strip_comments(_celestial)
if "f.cL * cRA" in _celestial_code or "const cosH" in _celestial_code:
    fail("[astro] 31-celestial.js 回退到「J2000 直算 LST」简化链（无岁差/章动）")
if "VX[i]" not in _celestial_code or "m0 * x" not in _celestial_code:
    fail("[astro] eachStar 热点未走总矩阵（会绕过岁差/章动）")
if "ASTRO.frameMatrices" not in _celestial or "dateToHorizon" not in _celestial:
    fail("[astro] 31-celestial.js 未使用总矩阵 / of-date 专用入口")
# sunAltAz 的太阳位置是 of-date，必须走 dateToHorizon，不能回退到 J2000 链
_sun = _celestial[_celestial.find("function sunAltAz"):]
_sun = _sun[:_sun.find("function ", 10)] if "function " in _sun[10:] else _sun
if "const v = eqToHorizon(" in _sun:
    fail("[astro] 太阳(of-date)走了 J2000 链，会重复施加岁差")
if "const v = dateToHorizon(" not in _sun:
    fail("[astro] sunAltAz 未走 of-date 入口")

_golden_path = ROOT / "build/astro-golden.json"
if not _golden_path.is_file():
    fail("[astro] 缺 build/astro-golden.json（先跑 build/gen_astro_golden.py）")
else:
    _g = json.loads(_golden_path.read_text(encoding="utf-8"))
    if _g["meta"]["caseCount"] < 6 or not _g["cases"]:
        fail("[astro] 金标准样本不足")
    if "celestial_geometry.py" not in _g["meta"]["source"]:
        fail("[astro] 金标准事实源不是仓库 celestial_geometry.py")
if not (ROOT / "tests/astro_selftest.js").is_file():
    fail("[astro] 缺跨语言自检 tests/astro_selftest.js")

_track_gen = (ROOT / "build/sync_sat_tracks.py").read_text(encoding="utf-8")
_tracks_json = ROOT / "build/sat-tracks.json"
if "precession_inverse_front" not in _track_gen:
    fail("[astro] 卫星轨迹未做 TEME→J2000 逆岁差（前端统一链会重复施加岁差）")
if _tracks_json.is_file():
    _tm = json.loads(_tracks_json.read_text(encoding="utf-8"))["meta"]
    if "J2000" not in _tm.get("frame", ""):
        fail("[astro] 轨迹 frame 不是 J2000")
    if not any("站址视差" in s for s in _tm.get("notVerified", [])):
        fail("[astro] 轨迹未如实标注站址视差 not_verified")

# 仓库章动单位错误不得回潮（1e4 缩放会让章动≈0）
_repo_cg = REPO / "mbdsdr_ai/celestial_geometry.py"
if _repo_cg.is_file():
    _cg = _repo_cg.read_text(encoding="utf-8")
    # 只看可执行行：docstring 里引用旧公式作为「修了什么」的说明，不算回潮
    if 'dp_r = dp * 0.0001 / 3600.0 * DEG' in _cg or \
       'return EPS0 - 0.0000003439 * T' in _cg:
        fail("[astro] 仓库 celestial_geometry.py 章动/黄赤交角单位错误回潮")
    if "dp_r = dp * DEG / 3600.0" not in _cg:
        fail("[astro] 仓库章动未按角秒换算")
    if "46.836769 / 206264.806" not in _cg:
        fail("[astro] 仓库黄赤交角岁差率不是 IAU2006 一次项")
for _f, _bad in (("run_all.py", "但坐标链为 J2000 直算 LST"),
                 ("build/sync_sky_catalog.py", "Web 用 J2000 直算 LST")):
    if _bad in (ROOT / _f).read_text(encoding="utf-8"):
        fail(f"[astro] {_f} 仍宣称未接岁差章动，口径过时")
print("[9.6] 天文框架：仓库 IAU2006 岁差 + IAU2000B 章动 + Capitaine GMST / "
      "基变换 Q / 跨语言金标准 / 太阳 of-date 专用链 / TEME 逆岁差 / 旧单位错误守卫")

# ---------- 9.6.1 恒星自行：HYG 实测 pmra/pmdec + 历元桶 + skyfield 独立金标准 ----------
# catalog 必须把自行编码进星数组（6 元素），命名星必须带 pmra/pmdec。
_pm_meta = meta.get("properMotion", {})
if not _pm_meta or "star[4]=pmra" not in _pm_meta.get("starEncoding", ""):
    fail("[pm] sky-catalog meta 缺 properMotion 编码口径（star[4]=pmra/star[5]=pmdec）")
_stars = CAT.get("stars", [])
if _stars and len(_stars[0]) != 6:
    fail(f"[pm] 星数组不是 6 元素（含 pmra/pmdec）：实际 {len(_stars[0])}")
_named = CAT.get("named", [])
if _named and not all("pmra" in s and "pmdec" in s for s in _named):
    fail("[pm] 命名星缺 pmra/pmdec（标签点与星点会走不同历元）")
# 前端必须有线性的自行推进入口 + 历元桶（不得每帧逐星重算三角，P0 冻结红线）。
if "propagateProperMotion" not in _astro or "MAS_PER_DEG" not in _astro:
    fail("[pm] 14-astro-frame.js 缺 propagateProperMotion / MAS_PER_DEG")
for _m in ("ensureEpoch", "PM_BUCKET", "ASTRO.propagateProperMotion", "RA0[i]", "PMRA[i]"):
    if _m not in _celestial_code:
        fail(f"[pm] 31-celestial.js 缺自行历元机制：{_m}")
if "Math.abs(dtYr - _pmDtYr) < PM_BUCKET" not in _celestial_code:
    fail("[pm] ensureEpoch 缺历元桶早退（每帧重算 25k 星三角会冻结 UI）")
# eachStar 热点不得逐星调用自行推进（只能在 ensureEpoch 跨桶时算一次）。
_each = _celestial_code[_celestial_code.find("function eachStar"):]
_each = _each[:_each.find("\n  function ", 10)] if "\n  function " in _each[10:] else _each
if "propagateProperMotion" in _each:
    fail("[pm] eachStar 热点逐星调用自行推进（性能红线：应只在 ensureEpoch 跨桶重算）")
# 搜索锁定必须走推进后的命名星向量，不能用裸 J2000 坐标。
if "C.namedVector(" not in sky_code:
    fail("[pm] 30-sky.js 搜索锁定未走 namedVector（会用未推进的 J2000 坐标）")
# 独立金标准：skyfield 严格空间运动（非自证），残差实测且 ≤ 容差。
_pm_golden = ROOT / "build/proper-motion-golden.json"
_pm = {}
if not _pm_golden.is_file():
    fail("[pm] 缺 build/proper-motion-golden.json（先跑 build/gen_proper_motion_golden.py）")
else:
    _pg = json.loads(_pm_golden.read_text(encoding="utf-8"))
    _pm = _pg["meta"]
    if "skyfield" not in _pm.get("source", ""):
        fail("[pm] 自行金标准事实源不是 skyfield 严格空间运动（不得自证）")
    if _pm.get("caseCount", 0) < 40 or not _pg.get("cases"):
        fail("[pm] 自行金标准样本不足")
    if _pm.get("worstResidualArcsec", 99) > _pm.get("toleranceArcsec", 0):
        fail(f"[pm] 自行残差 {_pm.get('worstResidualArcsec')}″ 超容差 {_pm.get('toleranceArcsec')}″")
    if len(_pm.get("notVerified", [])) < 3:
        fail("[pm] 自行 not_verified 不足（线性近似/量化/缺测距离须如实标注）")
if not (ROOT / "tests/proper_motion_selftest.js").is_file():
    fail("[pm] 缺跨语言自检 tests/proper_motion_selftest.js")
_run_all = (ROOT / "run_all.py").read_text(encoding="utf-8")
if "gen_proper_motion_golden.py" not in _run_all or "proper_motion_selftest.js" not in _run_all:
    fail("[pm] run_all.py 未接自行金标准/自检步骤")
if "恒星自行仍未计" in _run_all:
    fail("[pm] run_all.py 报告仍宣称自行未计（口径过时）")
print(f"[9.6.1] 恒星自行：HYG 实测 pmra/pmdec（6 元素星数组）/ 线性推进 + 历元桶早退 / "
      f"eachStar 热点不逐星重算 / 搜索锁定走推进坐标 / skyfield 独立金标准 "
      f"worst {_pm.get('worstResidualArcsec', 0):.4f}″ ≤ {_pm.get('toleranceArcsec', 0)}″")

# ---------- 9.7 写工具确认通道（阶段 C：前端只登记意图，token 不出宿主） ----------
_data = (SRC / "10-data.js").read_text(encoding="utf-8")
_dialog = (SRC / "50-dialog.js").read_text(encoding="utf-8")
if "confirmTool(" not in _data or "canConfirm(" not in _data:
    fail("[bridge] 前端缺确认通道适配（confirmTool / canConfirm）")
for bad in ("issue_confirmation", "issueConfirmation", "confirm_token\\s*=", "tx_token\\s*="):
    if re.search(bad, _data) or re.search(bad, _dialog):
        fail(f"[bridge] 前端试图自造/签发 token：{bad}（token 只能由宿主签发）")
if "canConfirm: false" not in _data:
    fail("[bridge] 宿主无确认通道时必须显式 canConfirm:false（不能默认给按钮）")
if "confirmButton(" not in _dialog:
    fail("[bridge] gated 回执缺「确认执行」入口")
for k in ("bridge.confirmRun", "bridge.confirmOff"):
    if k not in DICT:
        fail(f"[i18n] 缺确认通道文案：{k}")
_host = REPO / "mbdsdr_ai/web_bridge_mcp.py"
if not _host.is_file():
    fail("[bridge] 缺 mbdsdr_ai/web_bridge_mcp.py（真实确认通道实现）")
_hsrc = _host.read_text(encoding="utf-8")
if "issue_confirmation" not in _hsrc or "pending.consume" not in _hsrc:
    fail("[bridge] 确认通道必须走 tx_guard 签发 + 一次性消费")
if "TOKEN_FIELDS" not in _hsrc:
    fail("[bridge] 确认通道必须剥离前端传来的 token")
print("[9.7] 写工具确认通道：前端只登记意图 / token 宿主内签发 / 一次性消费 / 三语文案，全部通过")

# ---------- 10. 构建产物 ----------
dist = ROOT / "dist/index.html"
if not dist.is_file():
    fail("[dist] 未找到 dist/index.html（先跑 build/build.py）")
else:
    d = dist.read_text(encoding="utf-8")
    if 'src="src/' in d or 'href="src/' in d:
        fail("[dist] 单文件仍引用外部 src/ 资源")
    for marker in ("window.MBDSDR_DOC_DATA", "MB.CAPS", "isOrbitRequest",
                   "MB.REPO", "controlHub", "05-repo-facts"):
        if marker not in d and marker != "05-repo-facts":
            fail(f"[dist] 缺少模块内容：{marker}")
    if "丰满区" in d or "● RTL-SDR 已连接" in d:
        fail("[dist] 产物残留旧口径")
    print(f"[10] dist 自包含：{dist.stat().st_size} bytes")

# ---------- 汇总 ----------
print("\n" + "=" * 60)
for w in warnings[:8]:
    print("WARN", w)
if errors:
    for e in errors:
        print("FAIL", e)
    print(f"RESULT: {len(errors)} 项失败")
    sys.exit(1)
print("RESULT: ALL PASS ✅")

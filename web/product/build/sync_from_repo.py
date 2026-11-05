# -*- coding: utf-8 -*-
"""原仓库事实源同步器（只读）。

从 MBDSDR 原仓库（由 `paths.py` 探测 / `MBDSDR_REPO` 指定）的代码中抽取「唯一事实源」，生成：
  build/repo-facts.json        —— 结构化事实（Python/JS 共用）
  src/05-repo-facts.js         —— 前端 MB.REPO（由 JSON 直出）
  docs/09-repo-alignment.md    —— 对齐说明与一键复跑命令

纪律：
  * 绝不 import 仓库代码（避免 Qt/numpy 依赖），只用 ast.literal_eval + 正则；
  * 绝不修改原仓库任何文件（别的会话有在途改动）；
  * 域名/工具名/令牌/端口/倍率全部来自代码，不手抄文档（docs/mcp_tools.md
    标记区已滞后，README 的域数也已漂移——见 docs/09-repo-alignment.md）；
  * 译名表与规划项标注属于「产品侧本地化层」，在脚本里显式注明来源。

用法：python build/sync_from_repo.py
"""
from __future__ import annotations
import ast
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO
BUILD = ROOT / "build"
SRC = ROOT / "src"
DOCS = ROOT / "docs"

# ---------- 产品侧本地化层（不是仓库事实，显式标注） ----------
# key = 仓库里的中文域名（事实），value = 产品的稳定 id / 三语译名 / 图标。
DOMAIN_I18N = {
    "SDR 调谐/VFO": ("sdr-vfo", "SDR 同調 / VFO", "SDR Tuning / VFO", "≋"),
    "亚音数字解码": ("subaudio", "サブトーン・デジタル復号", "Subtone & Digital Decode", "⌁"),
    "VNA 测量": ("vna-measure", "VNA 測定", "VNA Measurement", "∿"),
    "VNA 分析": ("vna-analyze", "VNA 解析", "VNA Analysis", "◭"),
    "VNA 校准": ("vna-calib", "VNA 較正", "VNA Calibration", "✛"),
    "导出演制": ("export", "録音・エクスポート", "Recording & Export", "⇩"),
    "书签扫描卫星": ("sat-scan", "ブックマーク / 走査 / 衛星", "Bookmarks, Scan & Satellites", "◉"),
    "系统设备": ("system", "システム / デバイス", "System & Devices", "⚙"),
    "记忆编排": ("memory", "メモリ編成", "Memory Orchestration", "▤"),
    "工作台编排": ("workbench", "ワークベンチ編成", "Workbench Orchestration", "▦"),
    "解码器长尾": ("decoder-tail", "デコーダ長尾", "Decoder Long Tail", "⇶"),
    "人机问答": ("qa", "対話質問", "Human Q&A", "?"),
    "外部工具": ("external-tools", "外部ツール", "External Tools", "⇄"),
    "卫星链路": ("sat-link", "衛星リンク", "Satellite Links", "↗"),
}

# 规划 / 未验证能力：来自 07-卫星指向与AR系统.md §9 Roadmap 与
# docs/audit_r2/27_gimbal_pose.md 的审计结论。产品里必须标注 not_verified，
# 不得画成已上线能力（AGENTS.md §2 诚实文化）。
PLANNED = [
    dict(id="vis6dof", icon="✦", zh="6DOF 视觉引导", ja="6DOF 視覚誘導",
         en="6DOF Visual Guidance",
         why="位姿融合 L0–L3 为规划；pose.py 的 IMU 修正算出来即丢弃，无串口/IMU 驱动",
         source="07-卫星指向与AR系统.md §9 · docs/audit_r2/27_gimbal_pose.md"),
    dict(id="calib", icon="✛", zh="视觉校准", ja="視覚キャリブレーション",
         en="Visual Calibration",
         why="相机-天线轴系标定为规划；gimbal.py 的 read_pose() 在 manual/board 模式恒返回 0°",
         source="docs/audit_r2/27_gimbal_pose.md"),
    dict(id="vrxr", icon="◌", zh="VR / XR", ja="VR / XR", en="VR / XR",
         why="OpenXR / 头显 6DOF 为 feature:xr-sky 规划，代码里不存在",
         source="07-卫星指向与AR系统.md §9"),
]


# ---------- AST 辅助：找模块级 / self 属性的字面量赋值 ----------
def literal_assign(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            tgt = node.target
            ok = (isinstance(tgt, ast.Name) and tgt.id == name) or \
                 (isinstance(tgt, ast.Attribute) and tgt.attr == name)
            if ok and node.value is not None:
                try:
                    return ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                ok = (isinstance(tgt, ast.Name) and tgt.id == name) or \
                     (isinstance(tgt, ast.Attribute) and tgt.attr == name)
                if ok:
                    try:
                        return ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    return None


def read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def parse(path: str) -> ast.AST:
    return ast.parse(read(path), filename=path)


# ---------- 1. 工具域与工具面 ----------
mcp = parse("mbdsdr_ai/core/mcp_server.py")
frozen = literal_assign(mcp, "FROZEN_DOMAIN_SUMMARIES") or {}
ext = literal_assign(mcp, "PYTHON_EXTENSION_DOMAIN_SUMMARIES") or {}
tool_domain = literal_assign(mcp, "_TOOL_DOMAIN") or {}
assert frozen and ext and tool_domain, "mcp_server.py 域常量解析失败"

# FROZEN_DOMAIN_NAMES = list(FROZEN_DOMAIN_SUMMARIES.keys())（非字面量，按 dict 顺序取）
frozen_names = list(frozen.keys())
ext_names = list(ext.keys())
summaries = {**frozen, **ext}

by_domain: dict[str, list[str]] = {n: [] for n in frozen_names + ext_names}
for tool, dom in tool_domain.items():
    by_domain.setdefault(dom, []).append(tool)

domains = []
for i, name in enumerate(frozen_names + ext_names):
    slug, ja, en, icon = DOMAIN_I18N.get(name, (f"dom{i}", name, name, "·"))
    tools = sorted(by_domain.get(name, []))
    domains.append(dict(
        id=slug, name=name, ja=ja, en=en, icon=icon,
        group="frozen" if i < len(frozen_names) else "python",
        summary=summaries.get(name, ""),
        tools=tools, sample=tools[:6], empty=not tools,
        source="mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN",
    ))

# ---------- 2. 写工具名模式（真实人工门判定依据） ----------
reg_tree = parse("mbdsdr_ai/tool_registry.py")
write_patterns = literal_assign(reg_tree, "WRITE_TOOL_NAME_PATTERNS") or []

# ---------- 3. 桌面端设计令牌（figma 主题单源） ----------
tok = parse("desktop/tokens.py")
themes = literal_assign(tok, "THEME_COLORS") or {}
figma = themes.get("figma", {})
radius = literal_assign(tok, "RADIUS") or {}
font = literal_assign(tok, "FONT") or {}
alpha = literal_assign(tok, "TEXT_ALPHA") or {}
size = literal_assign(tok, "SIZE") or {}
assert figma and radius, "desktop/tokens.py 令牌解析失败"

# ---------- 4. 天空模式时间倍率（直接抽按钮元组） ----------
sky_src = read("desktop/rf_sky_view.py")
m = re.search(r"for label, rate in \((.*?)\):", sky_src, re.S)
assert m, "rf_sky_view.py 时间倍率按钮未找到"
rates = [dict(label=str(a), rate=float(b)) for a, b in ast.literal_eval("[" + m.group(1) + "]")]

# ---------- 5. ControlHub 真实通道 ----------
tok_h = read("cpp/src/core/tokens.h")
port = int(re.search(r"kControlHttpDefaultPort\s*=\s*(\d+)", tok_h).group(1))
env_var = re.search(r'kControlHttpPortEnvVar\s*=\s*"([^"]+)"', tok_h)
settings_key = re.search(r'kControlHttpPortSettingsKey\s*=\s*"([^"]+)"', tok_h)
hub_cpp = read("cpp/src/control/control_http_server.cpp")
# 从发现文档里抓路径字面量（形如 "/status"），去重但保持源码顺序
paths = []
for p in re.findall(r'"(/(?:[a-z_][a-z0-9_?=]*)?)"', hub_cpp):
    if p not in paths:
        paths.append(p)

# ---------- 5b. 写工具确认门（两条真实通道，各有各的门，前端都不许自造放行） ----------
# 事实源必须是代码本身：早期版本把 arm_manual 当成「写工具放行口令」，
# 但 C++ ControlHub 的门是宿主写开关，Python 内核的门是外部签发的
# confirm_token；两边都不认前端自报的 arm_manual。抽不到就 assert 失败，
# 绝不静默退回手抄常量。
hub_src = read("cpp/src/control/control_hub.cpp")
mcp_src = read("mbdsdr_ai/core/mcp_server.py")
err_src = read("mbdsdr_ai/error_catalog.py")

gate_guard = re.search(r"row->write\s*&&\s*!(\w+)\.load\(\)", hub_src)
assert gate_guard, "ControlHub 写开关守卫未找到（cpp/src/control/control_hub.cpp）"
hub_state_var = gate_guard.group(1)
hub_gate_err = re.search(r"写入被禁止（([^）]+)）：未执行", hub_src)
assert hub_gate_err, "ControlHub gated 文案未找到"
assert 'o["gated"] = true' in hub_src, "ControlHub gated 回执字段未找到"

flags_m = re.search(r"gate_flags\s*=\s*\(([^)]*)\)", mcp_src)
assert flags_m, "mcp_server.py gate_flags 未找到"
gate_flags = list(ast.literal_eval("(" + flags_m.group(1) + ")"))
token_m = re.search(
    r'args\.pop\("(confirm_token)", None\)\s*or\s*args\.pop\("(tx_token)", None\)',
    mcp_src)
assert token_m, "confirm_token / tx_token 消费链未找到"
token_fields = [token_m.group(1), token_m.group(2)]
assert '"manual_gate_required"' in mcp_src, "manual_gate_required 错误码未找到"
assert "模型自报 arm_manual 不算确认" in mcp_src, "自报拒绝口径未找到"
self_report_m = re.search(r'\w+_seen\s*=\s*\("(\w+)" in args\)', mcp_src)
assert self_report_m, "模型自报标记（arm_manual_seen）未找到"
self_report_fields = [self_report_m.group(1)]
assert "由真人经外部确认通道签发一次性 confirm_token 后重试" in err_src, \
    "error_catalog 门建议文案未找到"

gates = dict(
    controlHub=dict(
        owner="C++ ControlHub 宿主写开关（不由 Web 前端签发）",
        stateVar=hub_state_var,
        guard=f"row->write && !{hub_state_var}.load()",
        resultField="gated",
        errorContains=hub_gate_err.group(1),
        frontEndAction="只透传 args；gated:true 如实回显，不自行放行",
        source="cpp/src/control/control_hub.cpp: gatedResult / execute",
    ),
    mcp=dict(
        owner="外部确认通道签发的一次性 token（Python 内核消费）",
        tokenFields=token_fields,
        consumedGateFlags=gate_flags,
        selfReportFields=self_report_fields,
        errorCode="manual_gate_required",
        selfReportRejected=True,
        hint="由真人经外部确认通道签发一次性 confirm_token 后重试；token 有有效期且只能消费一次。",
        frontEndAction="只透传已签发的 token；不生成、不把 arm_manual 当放行",
        source="mbdsdr_ai/core/mcp_server.py · mbdsdr_ai/error_catalog.py",
    ),
    frontEndPolicy=dict(
        mayNotIssueToken=True,
        mustStripSelfReport=True,
        gatedIsNotFailure=True,
        note="前端不得签发 token、不得因自报 arm_manual 放行；gated 与执行失败严格区分",
    ),
)

# ---------- 6. 卫星目录与演示数据口径（事实源：sat_tracker.py） ----------
sat_tree = parse("mbdsdr_ai/sat_tracker.py")
downlink = literal_assign(sat_tree, "BUILTIN_DOWNLINK_HZ") or {}
tle_date = literal_assign(sat_tree, "BUILTIN_TLE_DATE") or ""
builtin_tles = literal_assign(sat_tree, "_BUILTIN_TLES") or []
sat_list = []
for entry in builtin_tles:
    if not isinstance(entry, list) or len(entry) < 3:
        continue
    name, line1 = entry[0], entry[1]
    norad = line1[2:7].strip() if len(line1) >= 7 else ""
    sat_list.append(dict(name=name, norad=norad, downlinkHz=downlink.get(name),
                         tleEpoch=tle_date, tleLines=len(entry) - 1))
meta_tree = parse("mbdsdr_ai/builtin_tle_meta.py")
tle_policy = literal_assign(meta_tree, "SYNTHETIC_TLE_POLICY") or {}

# ---------- 7. 仓库基线（只标识，不写死能力数字） ----------
try:
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, timeout=20).stdout.strip()
except Exception:
    head = "unknown"

facts = dict(
    generatedAt=datetime.now().astimezone().isoformat(timespec="seconds"),
    repo=dict(root=str(REPO), head=head,
              note="只读抽取；工作树可能含其它会话在途改动，HEAD 仅标识基线"),
    domains=domains,
    frozenCount=len(frozen_names), pythonExtensionCount=len(ext_names),
    toolDomainCount=len(tool_domain),
    writePatterns=list(write_patterns),
    gates=gates,
    planned=PLANNED,
    tokens=dict(figma=figma, radius=radius, font=font, textAlpha=alpha, size=size,
                source="desktop/tokens.py · THEME_COLORS['figma'] / RADIUS / FONT / TEXT_ALPHA / SIZE"),
    timeRates=dict(values=rates, source="desktop/rf_sky_view.py 底部倍率按钮"),
    channels=dict(
        controlHub=dict(
            host="127.0.0.1", port=port,
            envVar=env_var.group(1) if env_var else "",
            settingsKey=settings_key.group(1) if settings_key else "",
            endpoints=paths,
            postCommand=dict(path="/command", body={"tool": "<name>", "args": {}}),
            writeGate=f"{hub_state_var} (C++ host switch, not arm_manual)",
            source="cpp/src/core/tokens.h · cpp/src/control/control_http_server.cpp · docs/CHROMEOS_CROSTINI_SDR.md",
        ),
        mcp=dict(serverName="mbdsdr-core", protocolVersion="2024-11-05",
                 methods=["initialize", "tools/list", "tools/call"],
                 source="mbdsdr_ai/core/mcp_server.py · docs/mcp_tools.md"),
        headless=dict(controller="SDRController",
                      source="docs/headless_api.md · mbdsdr_ai/core/"),
    ),
    site=dict(default=dict(lat=0.0, lon=0.0, alt=0.0, trusted=False),
              guard="mbdsdr_ai/station.py", source="AGENTS.md §1 · mbdsdr_ai/station.py",
              note="中性示例站点；不可信即拒绝锁定与过境预报"),
    satellites=dict(items=sat_list, tleDate=tle_date, downlink=downlink,
                    policy=tle_policy,
                    source="mbdsdr_ai/sat_tracker.py · mbdsdr_ai/builtin_tle_meta.py",
                    note="内置 TLE 仅供离线演示；合成/凑整条目禁止用于精确过境"),
    redlines=dict(
        noRealLocationDefault=True,
        hardcodedCapabilityNumbers=False,
        noMockFakeData=True,
        writeToolsNeedExternalConfirmation=True,
        frontEndMustNotSelfArm=True,
        orbitDeterminationNotAFeature=True,
        license="MIT · AGPL 引擎只学机制不引代码",
        source="AGENTS.md §1–§3 · README.md · LICENSING.md · NOTICE.md",
    ),
    driftNotes=[
        "docs/mcp_tools.md 末尾标记区为自动生成且已滞后（无 sat_link_* 工具），不可手抄",
        "README.md 的域数描述与代码 ALL_DOMAIN_NAMES 不一致，以 mcp_server.py 为准",
        "07-卫星指向与AR系统.md 是历史设计稿：北京默认坐标、GPL-3.0 声明、/api/pointing/* 均未落地",
    ],
)

# ---------- 输出 ----------
BUILD.mkdir(parents=True, exist_ok=True)
SRC.mkdir(parents=True, exist_ok=True)
DOCS.mkdir(parents=True, exist_ok=True)
payload = json.dumps(facts, ensure_ascii=False, indent=2)
(BUILD / "repo-facts.json").write_text(payload + "\n", encoding="utf-8")

js = ("/* 由 build/sync_from_repo.py 生成，勿手改。事实源：原仓库代码。 */\n"
      "(function (global) {\n"
      '  "use strict";\n'
      "  const MB = global.MBDSDR = global.MBDSDR || {};\n"
      "  MB.REPO = " + payload + ";\n"
      "})(typeof window !== \"undefined\" ? window : globalThis);\n")
(SRC / "05-repo-facts.js").write_text(js, encoding="utf-8")

rows = "\n".join(
    f"| `{d['id']}` | {d['name']} / {d['ja']} / {d['en']} | "
    f"{'冻结' if d['group']=='frozen' else 'Python 扩展'} | "
    f"{'、'.join('`'+x+'`' for x in d['sample']) or '**（诚实空态）该面无工具**'} |"
    for d in domains)
planned_rows = "\n".join(
    f"| `{p['id']}` | {p['zh']} | {p['why']} | {p['source']} |" for p in PLANNED)

doc = f"""# 原仓库对齐说明（事实源与自动同步）

> 本页由 `build/sync_from_repo.py` 生成，勿手改。产品工程在仓库外，
> 但**能力面、通道、设计令牌、站点口径全部来自原仓库代码**，不是手抄的设计稿。
> 生成时间：{facts['generatedAt']} · 仓库基线：`{head}`（{facts['repo']['note']}）。

## 为什么要有这一层

早期版本的产品原型把能力位、工具名、桥接通道、站点、时间倍率都写成了自说自话的演示值，
与 MBDSDR 原仓库的真实实现脱节。现在的规则是：**凡仓库里有事实的东西，一律由脚本
只读抽取，产品从 JSON 长出来**；产品侧只保留译名、图标、场景组合这类本地化层。

一键复跑：

```bash
python build/sync_from_repo.py     # 只读原仓库 → repo-facts.json / 05-repo-facts.js / 本页
python build/build.py              # 顺带自动跑同步 + wiki 生成 + 单文件构建
python tests/verify_product.py     # 校验事实源与 UI/文档一致
```

## 1. 工具域（事实源：`mbdsdr_ai/core/mcp_server.py`）

- 冻结域与 Python 扩展域同名同序常量在 C++ `cpp/src/agentapi/mcp_server.cpp` 有跨语言钉扎；
  `FROZEN_DOMAIN_NAMES` / `PYTHON_EXTENSION_DOMAIN_NAMES` 由 summary 字典的键顺序生成。
- 每个域的工具清单来自 `_TOOL_DOMAIN`（工具 → 域），不是文档里的精选子集。
- **空域必须保留**：VNA 测量、记忆编排在 Python 面本就没有工具，仓库注释明确写「诚实空态」；
  产品里照样显示为「该面无工具」，不塞假工具凑数。

| id | 域名（三语） | 组 | 代表工具（代码抽取） |
|---|---|---|---|
{rows}

产品侧稳定 id 与三语译名在 `sync_from_repo.py` 的 `DOMAIN_I18N` 里显式维护（属于本地化层，
不是仓库事实）；图标同理。

## 2. 规划 / 未验证能力（诚实标注）

以下三项来自历史设计稿与审计结论，**代码里没有落地实现**，产品里标 `not_verified`、
开关禁用，不画成已上线功能（AGENTS.md §2 诚实文化）：

| id | 能力 | 为什么未验证 | 依据 |
|---|---|---|---|
{planned_rows}

## 3. 内核通道（事实源：`cpp/src/core/tokens.h` / `control_http_server.cpp`）

- **ControlHub**：`http://{facts['channels']['controlHub']['host']}:{port}/`，只绑回环、无鉴权、
  跨机走 SSH 隧道；端口环境变量 `{facts['channels']['controlHub']['envVar']}`，
  设置键 `{facts['channels']['controlHub']['settingsKey']}`。
- 发现文档 `GET /`、状态 `GET /status`、命令 `POST /command`（body `{{"tool":...,"args":{{...}}}}`）。
  代码里抓到的路径：{('、'.join('`'+p+'`' for p in paths[:12])) or '（见源码）'}。
- **两条通道、两道门，前端一道都不签发**（事实源 `MB.REPO.gates`，全部由代码抽取）：
  - **ControlHub（C++）**：写命令在 `{gates['controlHub']['guard']}` 被拦，回执
    `{{"ok":false,"gated":true}}` +「写入被禁止（{gates['controlHub']['errorContains']}）」。
    开关 `{gates['controlHub']['stateVar']}` 属于宿主桌面端，Web 前端**无权打开**；
    产品只透传 `args`，如实回显 `gated`，不自行放行。
  - **Python 内核 / MCP**：写工具由外部确认通道签发的一次性
    `{gates['mcp']['tokenFields'][0]}`（别名 `{gates['mcp']['tokenFields'][1]}`）放行；
    门内消费 `{'、'.join(gates['mcp']['consumedGateFlags'])}` 且不下发 handler。缺 token
    返回 `{gates['mcp']['errorCode']}`；**模型自报 `arm_manual` 明确拒绝**——
    `{gates['mcp']['hint']}`
  - **产品前端**：`mayNotIssueToken` / `mustStripSelfReport` / `gatedIsNotFailure`。
    `gated:true` 是「未获确认」，**不是执行失败、更不是设备故障**，UI 必须严格区分。
- 写工具名模式来自 `mbdsdr_ai/tool_registry.py: WRITE_TOOL_NAME_PATTERNS`（{len(write_patterns)} 条）。
- 另有 MCP `tools/list` / `tools/call`（`mbdsdr-core`，协议 `{facts['channels']['mcp']['protocolVersion']}`）
  与 Headless `SDRController`（`docs/headless_api.md`）。三通道行为一致。
- **不要沿用旧端口**：历史设计稿里的 `7878`、`81` 等 Python 后端已不存在；`7356` 是 rigctl、
  `8000` 是 Python `start_web_server`、`4533` 是 rotctld，都不是 ControlHub。

### 3.1 阶段 B 运行时桥接（仓库 Python 侧）

`mbdsdr_ai/web_bridge.py` 把 `mbdsdr_ai/tool_registry.py` 的工具面映射成产品
`MB.CONTRACT` 的 8 个方法，`as_bridge_dict()` 给出 `window.mbdsdrBridge` 形状，
`manifest()` 是可对拍的 JSON 契约；`tests/test_web_bridge.py` 把 Python / JS 契约
与本页的门事实钉在一起（16 条用例）。**写工具绝不走裸 `registry.call()`**：
内核的门在 `mcp_server.call_tool` 层，缺 `confirm_token` 返回
`manual_gate_required`，未接 `confirm_channel` 时恒 gated。宿主注入、
确认通道接线与真实硬件实测**尚未完成**，见 `manifest().notVerified`。

## 4. 设计令牌（事实源：`desktop/tokens.py`）

产品 CSS 变量由 `THEME_COLORS["figma"]` 生成：`bg {figma.get('bg')}`、
`card {figma.get('card')}`、`button {figma.get('button')}`、`border {figma.get('border')}`、
`primary {figma.get('primary')}`、`text {figma.get('text')}`；圆角与字号沿用
`RADIUS` / `FONT`（`card_lg {radius.get('card_lg')}px`、`dock_icon {radius.get('dock_icon')}px` 等）。
映射表见 `docs/design_tokens_mapping.md`。**外观令牌只描述外观，不塞业务数值**。

## 5. 站点与红线

- 默认站点 = 中性示例 `0.0, 0.0`、`trusted=false`；`mbdsdr_ai/station.py` 的守卫在站址不可信时
  拒绝过境计算，产品复刻同一行为（AGENTS.md §1 身份红线：禁止真实地理位置默认值）。
- 不硬编码能力数字（工具数/测试数/N 域）；能力用可复现命令证明，不用数字标语。
- 无硬件 → 显式空态或 `{{"data":null,"error":"not_connected","suggestion":...}}`，
  **绝不合成 IQ 冒充测量**；调试源若存在必须显式标「非真实设备」。
- 定轨不是功能：UI 上零入口，只有用户在对话里说出口才触发（用户 2026-11-04 铁律）。
- 全仓 MIT；Stellarium Web Engine 是 AGPL-3.0，**只学机制、不引代码 / 贴图 / 星表**。

## 6. 已知文档漂移（不要拿旧文档当事实源）

""" + "\n".join(f"- {x}" for x in facts["driftNotes"]) + """

## 7. 一致性保证

`tests/verify_product.py` 会核对：`repo-facts.json` ↔ `src/05-repo-facts.js` ↔
wiki 域词条文件集合 ↔ `dist/index.html` 内联数据；并扫描真实地名、能力数字标语、
规划项标注、诚实空态与定轨零入口。任何一处漂移即失败。
"""
(DOCS / "09-repo-alignment.md").write_text(doc, encoding="utf-8")

print(f"synced: {len(domains)} domains, {facts['toolDomainCount']} tool->domain entries, "
      f"{len(write_patterns)} write patterns, {len(rates)} time rates, "
      f"ControlHub :{port}, themes from {facts['tokens']['source']}")
print("wrote: build/repo-facts.json, src/05-repo-facts.js, docs/09-repo-alignment.md")

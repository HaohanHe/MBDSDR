# -*- coding: utf-8 -*-
"""生成 wiki 词条 / 术语表 / FAQ / 场景页 / 三语 README 译本。

事实源：build/repo-facts.json（由 sync_from_repo.py 只读抽取原仓库代码）。
域名、工具、卫星目录、通道、时间倍率均不手抄；wiki 词条文件集合与
UI 的 MB.CAPS 由同一份 JSON 生成，tests/verify_product.py 核对漂移。
"""
from __future__ import annotations
import json
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO
DOCS = ROOT / "docs"
CAPDIR = DOCS / "wiki/capabilities"
FACTS = json.loads((ROOT / "build/repo-facts.json").read_text(encoding="utf-8"))
DOMAINS = FACTS["domains"]
PLANNED = FACTS["planned"]
CAPS = DOMAINS + [dict(p, sample=[], empty=True, group="planned",
                       summary=p["why"], tools=[]) for p in PLANNED]
BY_ID = {c["id"]: c for c in CAPS}

# 写工具确认门：事实源 gates 由 sync_from_repo.py 从 C++ / Python 内核代码抽取。
# 文档里的 token 名、自报标记、宿主开关变量一律插值，不手抄，内核改名即同步跟随。
GATES = FACTS["gates"]
GH, GM = GATES["controlHub"], GATES["mcp"]
_TOKEN = GM["tokenFields"][0]
_ALIAS = GM["tokenFields"][1]
_SELF = GM["selfReportFields"][0]
_HUBVAR = GH["stateVar"]
assert _TOKEN == "confirm_token" and _SELF == "arm_manual" and _HUBVAR == "writeEnabled_", \
    "内核确认门字段已改名，请复核 docs/build 的门文案（不要静默沿用旧值）"

GATE_ZH = (
    f"写工具确认门由宿主 / 外部确认通道持有（事实源 `MB.REPO.gates`）：ControlHub 是 C++ "
    f"宿主写开关 `{_HUBVAR}`，Python 内核是外部签发的一次性 `{_TOKEN}`（别名 `{_ALIAS}`）。"
    f"**模型自报 `{_SELF}` 无效**；`gated:true` 表示**未获确认**，不是执行失败、更不是设备故障。"
    f"前端只透传、不签发 token、不自判放行。离线时返回 "
    '`{"ok":false,"error":"not_connected","suggestion":"..."}`，不伪装成功。'
)
GATE_GLOSSARY = (
    f"宿主写开关 `{_HUBVAR}` / 外部签发的一次性 `{_TOKEN}` 把关；自报 `{_SELF}` 无效，"
    f"`gated:true` 是未获确认而非执行失败"
)
GATE_FAQ = (
    f"写工具的确认门**不在前端**：ControlHub 由 C++ 宿主写开关 `{_HUBVAR}` 决定，"
    f"Python 内核只认外部确认通道签发的一次性 `{_TOKEN}`（别名 `{_ALIAS}`）。"
    f"**模型自报 `{_SELF}` 明确无效**，前端会剥离它；缺 token 时返回 "
    f"`{GM['errorCode']}` / `gated:true`，UI 显示「未获确认（gated · 非失败）」，"
    f"与执行失败严格区分。前端不签发 token、不自行放行。"
)
GATE_EN = (
    f"Offline by default: no fake IQ, no fake successes. Writes are gated by the C++ host "
    f"switch (`{_HUBVAR}`) for ControlHub, or a one-shot `{_TOKEN}` issued by an external "
    f"confirmation channel for the Python kernel; a model self-reporting `{_SELF}` is rejected. "
    f"The front end only forwards arguments, never issues tokens, and distinguishes "
    f"`gated` from a real failure."
)
GATE_JA = (
    f"既定はオフライン：偽の IQ も偽の成功もありません。書き込みは ControlHub では C++ "
    f"ホストの `{_HUBVAR}`、Python カーネルでは外部確認チャネルが発行する一回限りの "
    f"`{_TOKEN}` でゲートされます。モデルが `{_SELF}` を自己申告しても拒否されます。"
    f"フロントエンドは転送のみでトークンを発行せず、`gated` と失敗を厳密に区別します。"
)


def domain_page(d: dict) -> str:
    tools = "、".join(f"`{x}`" for x in d["tools"]) or "**本面无工具（诚实空态）**"
    sample = "、".join(f"`{x}`" for x in d["sample"])
    group = "冻结域（与 C++ 同名同序钉扎）" if d["group"] == "frozen" else "Python 扩展域"
    return f"""# 工具域词条 · {d['name']} `{d['id']}`

> {group} · 图标 `{d['icon']}` · 事实源 `{d['source']}`

| 语言 | 名称 |
|---|---|
| 简体中文 | {d['name']} |
| 日本語 | {d['ja']} |
| English | {d['en']} |

## 这个域解决什么

{d['summary']}

## 域内的真实工具

{tools}

代表工具：{sample or '（该面当前无工具）'}

工具名来自 `mbdsdr_ai/core/mcp_server.py` 的 `_TOOL_DOMAIN`（工具 → 域映射），
由 `build/sync_from_repo.py` 只读抽取。**不要手工往这里加工具**——工具面以运行时
`tools/list` 为准，文档清单可能滞后。

## 能力而非流程

这个域描述模型可调用的能力集合，**不规定调用顺序**。模型按现场设备、站址可信度、
用户回答动态装配任务；没有一键完成任务的黑盒工具（原仓库 `capability-map.md` 的设计红线）。

## 调用通道

- ControlHub：`POST http://127.0.0.1:{FACTS['channels']['controlHub']['port']}/command`，body `{{"tool": "...", "args": {{...}}}}`
- MCP：`tools/call`（`{FACTS['channels']['mcp']['serverName']}`）
- Headless：`SDRController` 方法面

{GATE_ZH}

## 现状与 not_verified

- 工具名与域归属：已由代码抽取并钉扎（本页即证据）。
- 本 UI 是否接线：默认离线（未连接 ControlHub），需 `?hub=1` 或宿主注入桥才启用；
  接线后的逐工具实测口径以 `docs/tool-availability.md` 的审计为准，本页不宣称已验证。
- 空域保持空态，不塞假工具。

## 相关

- [能力百科索引](../README.md)
- [原仓库对齐说明](../../09-repo-alignment.md)
- [分层架构与内核桥](../../03-architecture.md)
- [术语表](../glossary.md)
"""


def planned_page(p: dict) -> str:
    return f"""# 规划能力词条 · {p['zh']} `{p['id']}`

> **状态：规划 / 未验证（not_verified）** · 图标 `{p['icon']}`

| 语言 | 名称 |
|---|---|
| 简体中文 | {p['zh']} |
| 日本語 | {p['ja']} |
| English | {p['en']} |

## 为什么不是已上线能力

{p['why']}

依据：`{p['source']}`。原项目 07 文档的 §9 Roadmap 把 6DOF 位姿融合、AR 实景叠加、
OpenXR 头显列为规划；`docs/audit_r2/27_gimbal_pose.md` 的审计结论是
`pose.py` 的 IMU 修正算出来即丢弃、无串口/IMU 驱动，`gimbal.py` 的闭环层是空壳。

## 产品里的呈现

- 能力矩阵里带「规划 · 未验证」徽标，开关**禁用**，不能点成「在位」；
- 模型现场协商时若用户提到，模型如实说明未验证，不把它当可执行工具调用；
- 这是产品愿景的一部分（科教教培 / 科学研究 / 通联都能用上），但按
  `AGENTS.md §2` 诚实文化，**不包装成已实现**。

## 相关

- [能力百科索引](../README.md)
- [原仓库对齐说明](../../09-repo-alignment.md)
- [路线图](../../07-roadmap.md)
"""


CAPDIR.mkdir(parents=True, exist_ok=True)
expected = {c["id"] + ".md" for c in CAPS}
for old in CAPDIR.glob("*.md"):           # 清掉上一版虚构能力位词条
    if old.name not in expected:
        old.unlink()
for c in CAPS:
    page = planned_page(c) if c["group"] == "planned" else domain_page(c)
    (CAPDIR / f"{c['id']}.md").write_text(page, encoding="utf-8")

# 引擎机制研究笔记并入文档体系（源笔记随产品工程入库，不再指向仓库外）
notes_src = ROOT / "docs/source/swe-study-notes.md"
if notes_src.is_file():
    body = notes_src.read_text(encoding="utf-8")
    header = ("> 来源：原型阶段的引擎机制研究，现已并入产品文档体系。\n"
              "> 许可证纪律见 [06-compliance.md](06-compliance.md)：只记数学、几何与算法，\n"
              "> 不含 AGPL 源码原文；产品代码全部独立重写。\n\n")
    (DOCS / "08-engine-mechanism-notes.md").write_text(header + body, encoding="utf-8")

dom_rows = "\n".join(
    f"| `{d['id']}` | {d['name']} / {d['ja']} / {d['en']} | "
    f"{'冻结' if d['group']=='frozen' else 'Python 扩展'} | "
    f"{'、'.join('`'+x+'`' for x in d['sample']) or '诚实空态 · 该面无工具'} | "
    f"[{d['name']}](capabilities/{d['id']}.md) |" for d in DOMAINS)
plan_rows = "\n".join(
    f"| `{p['id']}` | {p['zh']} / {p['ja']} / {p['en']} | 规划 · 未验证 | "
    f"[{p['zh']}](capabilities/{p['id']}.md) |" for p in PLANNED)

wiki_index = f"""# 能力百科 · 索引

> **能力而非流程**。本页的条目不是向导步骤，是模型可调用的能力集合。
> 工具域来自原仓库 `mcp_server.py` 的代码事实（由 `build/sync_from_repo.py` 抽取），
> 规划能力来自设计稿与审计结论并明确标注未验证。

## 一、已落地的工具域

| id | 域名（三语） | 组 | 代表工具 | 词条 |
|---|---|---|---|---|
{dom_rows}

## 二、规划 / 未验证（not_verified）

| id | 名称（三语） | 状态 | 词条 |
|---|---|---|---|
{plan_rows}

## 一致性保证

- 本页 id 集合与 `build/repo-facts.json` 的 `domains + planned` 一一对应；
  `tests/verify_product.py` 会核对 UI、词典、词条与构建产物，漂移即失败。
- 域名的中文是仓库事实，日/英译名与图标是产品侧本地化层
  （`build/sync_from_repo.py` 的 `DOMAIN_I18N` / `PLANNED`）。
- 工具名来自 `_TOOL_DOMAIN`，**不手抄** `docs/mcp_tools.md` 的自动生成标记区（已滞后）。

## 其它

- [术语表（三语）](glossary.md) · [常见问题](faq.md)
- [原仓库对齐说明](../09-repo-alignment.md)
- [产品规格铁律](../01-product-spec.md) · [场景页](../scenarios/)
"""
(DOCS / "wiki/README.md").write_text(wiki_index, encoding="utf-8")

glossary = """# 术语表（zh-CN / 日本語 / English）

| 简体中文 | 日本語 | English | 说明 |
|---|---|---|---|
| AI 定义无线电 | AI 定義無線 | AI-Defined Radio (AIDR) | 以人在回路为前提、SDR 功能工具化为本质差异的无线电系统 |
| 人在回路 | 人間がループ内 | Human in the loop | 写动作需人工确认，不宣称 AI 完全自主 |
| 工具面 | ツール面 | Tool surface | 运行时 `tools/list` 给出的可调用工具集合，数量不写死 |
| 工具域 | ツールドメイン | Tool domain | `_TOOL_DOMAIN` 的分组；冻结域与 C++ 跨语言钉扎 |
| ControlHub | ControlHub | ControlHub | C++ 桌面引擎的回环 HTTP 控制通道（无鉴权、只绑 127.0.0.1） |
| MCP | MCP | Model Context Protocol | 外部 agent 通过 `tools/list` / `tools/call` 调用工具的协议 |
| 三通道 | 三チャネル | Agent / ControlHub / HTTP | 行为一致、写动作分别由宿主写开关 / 外部确认 token 把关 |
| 写工具确认门 | 書き込み確認ゲート | Write confirmation gate | <<GATE_GLOSSARY>> |
| 诚实空态 | 正直な空状態 | Honest empty state | 无硬件/无信号显式报空，绝不 mock 假数据 |
| 真机实证 | 実機検証 | Real-hardware evidence | 有实测记录才写「已验证」，云测/仿真不算 |
| 内置 TLE 策略 | 内蔵 TLE 方針 | Built-in TLE policy | `decoders.BUILTIN_TLE` 合成/凑整占位值禁止精确过境；产品轨迹另用 `sat_tracker._BUILTIN_TLES`（Celestrak 真实快照） |
| 站址可信度 | 観測地点の信頼性 | Site trust | 中性示例站点 `0.0, 0.0` 未校准时拒绝锁定与过境 |
| 星象仪模式 | プラネタリウムモード | Planetarium Mode | 打开即全天空展开的形态 |
| 专业模式 | プロフェッショナルモード | Professional Mode | 传统 SDR 工具化 + 模型协作栏 |
| 能力，而非流程 | 機能ではなく能力 | Capability, not workflow | 产品第一原则 |
| 模型现场能力协商 | モデルによるその場の能力確認 | On-the-spot capability negotiation | 模型问设备，按回答装配任务 |
| 摄像机锁定 | カメラ固定 | Camera lock | 天动星静：卫星钉画面中心 |
| 地景 | 地景 / ランドスケープ | Landscape | 固定地平系，随相机走、不随时间转 |
| 周日视运动 | 日周運動 | Diurnal motion | 星空随时间绕极旋转 |
| 时间倍率 | 時間倍率 | Time rate | ⏸ / ▶ / ×60 / ×300 / ×1800，与桌面端一致 |
| 视场角 | 視野角 | FOV | 缩放改的是它，不是相机推拉 |
| 抓天拖拽 | 天空をつかむドラッグ | grab-the-sky | 按下点钉在光标下 |
| 定轨（仅对话） | 軌道決定（対話のみ） | Orbit determination (dialog only) | **UI 无入口**，只有用户说出口才触发 |
| 规划 · 未验证 | 計画 · 未検証 | Planned · unverified | 6DOF / AR / VR 等设计愿景的诚实标注 |
| 金样 | ゴールデンテスト | Golden test | 外部真值对拍，防自证 |
| not_verified | 未検証 | not verified | 明示未验证项 |
| 数据源契约 | データソース契約 | DataSource contract | UI ↔ 内核唯一接缝 |
| 灰阶热力 | グレースケールヒートマップ | Greyscale heatmap | .figma 设计语言下的瀑布色彩 |
"""
(DOCS / "wiki/glossary.md").write_text(
    glossary.replace("<<GATE_GLOSSARY>>", GATE_GLOSSARY), encoding="utf-8")

faq = """# 常见问题

## 能力矩阵里的条目是哪来的？

由 `build/sync_from_repo.py` **只读解析原仓库代码**生成：域名与工具来自
`mbdsdr_ai/core/mcp_server.py` 的 `FROZEN_DOMAIN_SUMMARIES` /
`PYTHON_EXTENSION_DOMAIN_SUMMARIES` / `_TOOL_DOMAIN`，设计令牌来自 `desktop/tokens.py`，
ControlHub 端口来自 `cpp/src/core/tokens.h`，时间倍率来自 `desktop/rf_sky_view.py`。
不是手抄设计稿——`docs/mcp_tools.md` 的自动生成标记区和 README 的域数都已经滞后。
详见 [原仓库对齐说明](../09-repo-alignment.md)。

## 为什么有的域写着「该面无工具」？

因为原仓库在该工具面上确实没有工具（VNA 测量、记忆编排），源码注释明确要求
「诚实空态」。产品保留空域、不塞假工具凑数。

## 为什么 6DOF / AR / VR 的开关点不动？

它们是**规划 / 未验证**能力：07 设计稿的 §9 Roadmap 与 `docs/audit_r2/27_gimbal_pose.md`
审计结论表明位姿闭环层是空壳、头显接入不存在。按 `AGENTS.md §2` 诚实文化，
能力矩阵里标徽标并禁用开关，不包装成已上线功能。

## 为什么专业模式的频谱是空的？

对齐 `desktop/spectrum_widget.py`：无 SDR / 无 IQ 时**只画网格 + 「未连接 SDR」提示**。
早期版本预填充过合成瀑布历史，属于伪造数据，已删除。接上 ControlHub 后谱行来自
`read_spectrum` 的真实回执；内核没返回数据就继续显示空态。

## 默认站点为什么是 `0.0, 0.0`？

`AGENTS.md §1` 身份红线禁止真实地理位置默认值。默认站址是中性示例且
`trusted=false`，复刻 `mbdsdr_ai/station.py` 的守卫：站址不可信时拒绝锁定与过境预报。
两份数据别混：`decoders.BUILTIN_TLE` 是历史凑整占位值（`builtin_tle_meta.py` 策略：禁止精确过境）；产品卫星天球轨迹用的是 `sat_tracker.py::_BUILTIN_TLES`，`BUILTIN_TLE_DATE` 标注的 Celestrak 真实抓取快照。轨迹按真实 SGP4 传播，但仍是**静态快照 + 地心方向**：未做站址视差，不给升落/过境结论。

## 状态栏为什么不再显示 Viterbi / RS / ASM 数字？

那些是仓库真录金样的历史口径，不是页面正在解码的实时值；把它们摆在状态栏
等于暗示实时链路，也违反「不硬编码能力数字」。需要看金样结果请查文档与可复跑命令。

## 怎么连上真实内核？

- **ControlHub**：桌面引擎启动后访问 `dist/index.html?hub=1`（或设 `window.MBDSDR_USE_HUB`），
  UI 走 `POST http://127.0.0.1:50732/command`。注意该通道只绑回环、无鉴权；
  从 `file://` 直连可能被浏览器 CORS 拦，正式部署建议由引擎同源托管或走宿主注入桥。
- **宿主注入桥**：Qt/WebEngine 注入 `window.mbdsdrBridge`（`invoke` / `satellites` 等）即可。
- **MCP**：外部 agent 走 `tools/list` / `tools/call`。

<<GATE_FAQ>>

## 为什么界面上找不到「定轨」？

因为**定轨不是功能**。用户明确要求：UI 上不得出现任何按钮、能力位、工具入口或推销话术。
唯一触发方式是**在对话里跟模型说**（AI 抽屉底部输入框 / 专业模式协作栏）。对话流里的
工具调用不算功能面。见 [01-product-spec.md](../01-product-spec.md) 铁律 4。

## 地景到底是动还是不动？

- **随相机**：拖拽方位、缩放、俯仰，地景都跟着动（长在世界坐标上）。
- **随时间**：不动。星空是 ICRF，随周日视运动转；地景固定在 OBSERVED 地平系。
- 时间倍率按钮（⏸ / ▶ / ×60 / ×300 / ×1800）来自桌面端同一套档位，点两下就能看清区别。

## 能不能直接用 Stellarium Web Engine 的代码？

不能。它是 AGPL-3.0，我们产品是 MIT：**只学机制、不引代码**。地景坐标系、FOV 缩放、
grab-the-sky、加法大气、呼吸指针都是思想层的数学与几何，全部独立重写；贴图/星表也未复用。
详见 [06-compliance.md](../06-compliance.md)。

## 语言切换后为什么有些文档还是中文？

文档有**独立**语言钮。主文档以中文为主，仅 README 提供英文/日文译本；缺译时文档中心
显示中文原文并明确标注「暂无该语言译本」，不假装已本地化。界面文案与对话模板是三语全量。
"""
(DOCS / "wiki/faq.md").write_text(
    faq.replace("<<GATE_FAQ>>", GATE_FAQ), encoding="utf-8")

SCENES = [
    ("education", "科教教培", "课堂可复现的天球、频谱与解码演示",
     ["sdr-vfo", "sat-scan", "decoder-tail"],
     "把『天在动、地不动』、周日视运动、大气辉光、时间倍率与内置演示目录变成一节课里能当场看见的东西；频谱无 IQ 时保持诚实空态。",
     "时间倍率五档（与桌面端一致）、锁定后摄像机语义、内置 TLE 的「不可用于精确过境」标注、解码长尾工具域词条。",
     "模型可能调用 `read_spectrum`、`get_satellite_pass`、解码器长尾域工具；组合由课堂现场设备决定，不预设步骤。"),
    ("research", "科学研究", "可追溯数据链、金样对拍与误差留痕",
     ["sdr-vfo", "system", "export", "decoder-tail"],
     "数据从采样到帧都要能复算：时间戳基准、软判决质量、FEC 纠错数、失败原因都要留痕，未验证项写进 not_verified。",
     "外部真值金样（WSJT-X / dump1090 / Meteor-M2 真录）、C++/Python 跨实现对拍、工具可用性审计台账。",
     "模型可能调用 `get_status`、`start_recording`、`read_iq`、解码链工具；按观测目标动态装配。"),
    ("comms", "通联 / 应急", "过境窗口、指向与现场降级方案",
     ["sdr-vfo", "sat-scan", "sat-link", "system"],
     "设备条件不稳定时仍要能干活：有执行机构走闭环，没有就固定天线 + 高仰角窗口，没有 SDR 就退到预报与记录计划；站址未校准时拒绝给可信结论。",
     "内置下行频率表与 TLE 历元、站址可信度状态、写工具人工门回执、降级方案的诚实回执。",
     "模型可能调用 `get_satellite_pass`、`sat_link_tuning`、`set_frequency`；是否存在这些能力由现场提问决定。"),
]
scenedir = DOCS / "scenarios"
scenedir.mkdir(parents=True, exist_ok=True)
for sid, name, sub, caps, desc, evidence, tools in SCENES:
    caplinks = "、".join(
        f"[{BY_ID[c]['name'] if c in BY_ID else c}](../wiki/capabilities/{c}.md)" for c in caps)
    text = f"""# 场景 · {name}

> {sub}

## 目标

{desc}

## 可用的能力组合（真实工具域）

{caplinks}

**能力而非流程**：以上是该场景常用的能力位，不是执行顺序。模型现场问设备，按回答装配任务；
同一个场景在不同现场会长成不同的样子。

## 可出示的证据

{evidence}

## 模型可能调用的工具

{tools}

## 诚实边界

- 无 TLE / 站址未校准时，不给出可信过境结论；
- 内核未连接时工具返回离线失败回执；
- 规划 / 未验证项逐条留档，不包装成已验证；
- 能力用可复跑命令证明，不用数字标语。

## 相关

- [产品规格](../01-product-spec.md) · [能力百科](../wiki/README.md)
- [原仓库对齐说明](../09-repo-alignment.md) · [路线图](../07-roadmap.md)
"""
    (scenedir / f"{sid}.md").write_text(text, encoding="utf-8")

(DOCS / "README.en.md").write_text("""# MBDSDR Spacetime Console · Product README (English)

One system, two forms: **Professional Mode** (traditional SDR tooling plus a model collaboration panel)
and **Planetarium Mode** (the whole sky opens at once; camera-locked satellites stay still while the sky moves).

Everything on screen is a **model-callable capability**, not a prebuilt workflow. Track / Receive / Point
only open a dialogue where the model asks what hardware is actually on site (SDR, gimbal, GNSS, camera…).
Orbit determination is deliberately **not a UI feature**: it can only be triggered by the user saying it to the model.

The capability matrix, tool names, satellite catalogue, ControlHub port, time-rate presets and design
tokens are **generated from the repository source** by `build/sync_from_repo.py` — not hand-copied from
a design draft. Empty tool domains stay empty; 6DOF / AR / VR are marked planned / unverified.
<<GATE_EN>>

- Languages: 中文 / 日本語 / English, switchable at runtime and persisted.
- Design language: the `.figma` export mapped through `desktop/tokens.py` (pure black, greyscale, MiSans, large radii).
- Compliance: MIT self-written code; the AGPL-3.0 Stellarium Web Engine was studied for **mechanisms only**.
- Quick start: open `dist/index.html` (single file). Build: `python build/build.py`.
- Verify: `python tests/verify_product.py` and `node tests/verify_runtime.js`.

Full documentation (Chinese original): `README.md`. The in-product Docs Centre marks missing translations honestly.
""".replace("<<GATE_EN>>", GATE_EN), encoding="utf-8")

(DOCS / "README.ja.md").write_text("""# MBDSDR 時空コンソール · 製品 README（日本語）

一つのシステム、二つの形態：**プロフェッショナルモード**（従来の SDR ツール化＋モデル連携パネル）と
**プラネタリウムモード**（開くと全天空が一気に広がり、カメラ固定で衛星は静止、天が動く）。

画面上のすべては**モデルが呼び出せる能力**であり、固定の手順ではありません。追跡／受信／指向は
対話を開くだけで、モデルがその場で現場の機器（SDR・雲台・GNSS・カメラ…）を尋ねます。
軌道決定は意図的に **UI の機能にしていません** — ユーザーがモデルに話しかけたときだけ動きます。

能力マトリクス・ツール名・衛星カタログ・ControlHub ポート・時間倍率・デザイントークンは
`build/sync_from_repo.py` が**リポジトリのコードから自動生成**します（設計稿の手写しではありません）。
ツールのないドメインは空状態のまま。6DOF / AR / VR は「計画・未検証」と明記します。
<<GATE_JA>>

- 言語：中文 / 日本語 / English、実行時に切り替え・設定を保持。
- デザイン：`.figma` 書き出しを `desktop/tokens.py` 経由で適用（純黒・グレースケール・MiSans・大きな角丸）。
- ライセンス：本成果物は MIT の自作コード。AGPL-3.0 の Stellarium Web Engine は**仕組みのみを学習**。
- 起動：`dist/index.html`（単一ファイル）。ビルド：`python build/build.py`。
- 検証：`python tests/verify_product.py` / `node tests/verify_runtime.js`。

完全なドキュメントは中国語オリジナル `README.md`。未翻訳は製品内ドキュメントセンターが明示します。
""".replace("<<GATE_JA>>", GATE_JA), encoding="utf-8")

print(f"generated: {len(DOMAINS)} domain pages + {len(PLANNED)} planned pages, "
      f"3 scenarios, glossary, faq, wiki index, README.en/ja")

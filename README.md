# MBDSDR · AI 定义无线电（AI-defined SDR） v0.2.0

> **30 秒读懂**：这是一套把示波器/矢网/软件无线电（SDR）的每个动作封装成
> LLM 可调用工具的系统——外部 AI agent 通过 MCP 协议直接遥控真实硬件：
> 测天线、收信号、解调、解码、抓星历。**没有 mock、没有假数据**：没插硬件
> 就报空，未校准就标口径。本页「快速开始」的离线命令在隔离环境实测过，
> 实测口径与自动化契约见 [§4 离线复验](#4-离线复验本页命令的实测口径) 与 [`scripts/verify_readme.py`](scripts/verify_readme.py)。
>
> **真机实证（2026-10）**：NanoVNA-H 实测自制天线谐振与馈线时延、
> RTL-SDR 实抄 435MHz 业余频段载波、外部 agent 经 MCP 遥控收发全链路、
> 设备屏幕截图回传——全部见 [`docs/learn/agent-onboarding-runbook.md`](docs/learn/agent-onboarding-runbook.md)。

把 SDR 的接收 / 解调 / 解码 / 卫星过境 / AR 指向，全部封装成 LLM 可调用的工具（function calling），
由智能体编排成"一键工作流"。本仓库是同一套体系在**桌面（两套实现）、手机端、Python 智能体内核，
外加论文实验管线与真机工具**上的落地；不写死组件数量，实时清单以本节表格为准。

| 组件 | 位置 | 是什么 |
|---|---|---|
| Python/PySide6 桌面客户端 | [`desktop/`](desktop/README.md) | 持续维护中的完整桌面端：频谱/瀑布、星象仪全天球、AI 对话栏、真机接收链。`python desktop/main.py` 直接启动、无需编译；无硬件时默认显示诚实「未连接」空状态 |
| 原生 C++/Qt 桌面端 | [`cpp/`](cpp/README.md) | C++/Qt 原生 SDR 接收软件（UI 与 DSP 同进程，不依赖 Python 运行时）。AI 助手工具面（read/write 双态、写动作在手动模式被门拦截；**数量随版本漂移，实时口径以 `cpp/src/ai/tool_schema.cpp` 注册表为准**），并内置 ControlHub HTTP 控制通道 |
| 手机端 | [`mobile/`](mobile/README.md) | Flutter 一套代码（Android/iOS），手机作 AI 的"眼睛和手臂"：定位+罗盘指向+rtl_tcp 直连外置 SDR；经 ControlHub HTTP 只读对接桌面引擎 |
| Python 智能体内核 | [`mbdsdr_ai/`](mbdsdr_ai/README.md) | **研究原型**：注册大量 LLM 可调用工具（数量随版本漂移，实时口径见该 README §1 与运行时 `tool_registry.list_tools()`）+ 工作流引擎。这是原型期工具面，**不是**随桌面交付的工具集 |
| 论文实验管线 | [`experiments/`](experiments/README.md) | 可复现实验（Eb/N0 解码成功率 / 多普勒定轨 / AMR 基线 / LLM 基线），固定种子、逐图标口径 |
| 真机工具 | [`tools/`](tools/) | 一键只读自检 `hw_selfcheck` + 联调向导 `onboarding` |
| 重复实现台账 | [`docs/learn/dedup-ledger.md`](docs/learn/dedup-ledger.md) | 同主题多份实现的状态台账（已收敛/有意分层/待收敛）+ 成对工具自洽审计方法与已修缺陷 | 
| 能力地图 | [`docs/learn/capability-map.md`](docs/learn/capability-map.md) | 任务→积木编排对照表（气象对星接收/多普勒定轨/ASTU-1遥测/前端搭建/链路自测）+ 新积木参数速查 + 诚实边界 |
| 学习与阶段规格 | [`docs/learn/`](docs/learn/) | 上游真实源码精读（sdrpp/gnuradio/librtlsdr…）+ phase3–6 实现规格与审计 |

许可证：**MIT**（见 [`LICENSE`](LICENSE)、第三方归属 [`NOTICE.md`](NOTICE.md)）。

**工程口径**：全仓 **MIT 发布**，第三方上游项目与许可归属见 [`NOTICE.md`](NOTICE.md)；
协议栈（FT8/LRPT/CCSDS 等）按公开标准与协议事实独立实现 · LLM 工具三通道（Agent / ControlHub /
HTTP）行为一致且有测试钉住，写动作一律过人工门 · 已对 SDR++、SatDump、
nanovna-saver、NanoVNA-App、kimi-code、kilocode 六个上游项目做过源码级
克隆审计（见 [`docs/learn/`](docs/learn/)）。

---

<details open>
<summary><b>日本語 / For Japanese readers</b></summary>

**MBDSDR は「AI が計測器を直接操作するシステム」です。** SDR（ソフトウェア無線）と
ベクトル・ネットワーク・アナライザ（NanoVNA）の操作を LLM から呼び出せる
ツールとして実装し、MCP（Model Context Protocol）経由で外部エージェントが
実機を操作できます。専用機器の操作習熟コストを AI に引き渡すことが出発点です。

**実機検証済み（2026 年 10 月、ダミーデータなし）**：
NanoVNA-H による自作アンテナの共振周波数・給線路遅延の実測 / RTL-SDR による
435 MHz 帯アマチュア周波数の実キャリア受信・FM 復調 / 外部エージェントからの
MCP 遠隔操作（list_devices → connect → set_frequency → get_status）。

**ライセンスと門**：リポジトリ全体が MIT（独立実装、GPL コードの流用なし）·
LLM ツールは Agent / ControlHub / HTTP の 3 チャネルで動作が一致し、書き込みには
手動承認ゲートが必須 · SDR++・SatDump・NanoVNA-App 等の主要 OSS をクローンして
ソース監査済み。

ハードウェアなしで試す：
`python3 -m mbdsdr_ai.aigui_demo --out demo.html`（AI 編排 UI の一巡）、
`python3 -m pytest tools/hw_selfcheck/test_selfcheck.py tools/onboarding/test_onboarding.py -q`
（オフライン決定性テスト）。どちらも実機を必要としません。

</details>

---

## 快速开始

### 1. 桌面端（两套实现，按工具链选）

**Python / PySide6 桌面客户端**——无需 C++ 工具链，当前持续维护的 UI 主界面：

```bash
pip install -r requirements.txt
python desktop/main.py                                     # 无硬件时显示诚实「未连接」空状态
python desktop/main.py --theme dark                        # 可选：default / dark / high_contrast
python desktop/main.py --import-probe desktop.main_window  # 只做导入自检，不建窗口
```

依赖、打包与其它启动方式见 [`desktop/README.md`](desktop/README.md)。
不插硬件时**不默认造合成 IQ**；`--import-probe` 是在无显示环境里验证安装的最快一条命令。

**原生 C++/Qt 桌面端**——需 Qt 6.8 / librtlsdr 2.x / CMake 3.16+ 与 C++ 编译工具链：

```bash
cd cpp
cmake -S . -B build && cmake --build build -j"$(nproc)"
./build/mbdsdr        # 无硬件且未加载离线捕获文件时，显示诚实"未连接"空状态
                      # 需要合成调试信号时必须显式 --test-source / MBDSDR_TEST_SOURCE=1
                      # 设计红线：never invent synthetic IQ by default, no fake hardware is ever shown
```

依赖、测试与完整功能清单见 [`cpp/README.md`](cpp/README.md)。
两套桌面端并存、各有独立测试；没有 C++ 工具链的机器直接走上面的 Python 端，不必先编 `cpp/`。

**原生 C++/Qt 桌面端**启动后在**回环 `127.0.0.1:50732`** 起一条 ControlHub HTTP 控制通道
（具名常量 `cpp/src/core/tokens.h` 的 `kControlHttpDefaultPort`，QSettings/env 可配）：
本机浏览器开 `http://127.0.0.1:50732/` 即可查看状态 / 下命令；端点只绑回环，跨机控制走 SSH 隧道（如 `ssh -N -L 50732:127.0.0.1:50732 user@接收机`）。
Chromebook / Crostini 当远程控制端的完整拓扑与隧道步骤见 [`docs/CHROMEOS_CROSTINI_SDR.md`](docs/CHROMEOS_CROSTINI_SDR.md)。
Python/PySide6 桌面端不走这条 ControlHub 通道，别按上面的地址去找它。

### 2. 真机自检 + 联调（一条命令）

真机（RTL-SDR + GNSS + 天线）在**用户本地**；云开发环境无任何硬件透传：

```bash
# 只读自检：USB/udev、rtl_test tuner、rtl_sdr 实读丢包、声卡、GNSS 串口、Python 依赖
python3 tools/hw_selfcheck/selfcheck.py

# 一条命令从"插好 RTL-SDR"走到"出图/出报文"（ADS-B 飞机报文，1090 MHz）
python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb
```

- 两条命令都可加 `--json` 输出结构化结果；**无硬件时每步明确失败原因，绝不 mock、绝不造假数据**。
- 真机用户跑完后，按 [`docs/learn/phase6/P2-hw-report-format.md`](docs/learn/phase6/P2-hw-report-format.md)
  的回传格式把 JSON 贴回。
- 用法与 FAQ 见 [`tools/onboarding/README.md`](tools/onboarding/README.md)、
  [`tools/hw_selfcheck/README.md`](tools/hw_selfcheck/README.md)。

### 3. Python 内核与论文实验

```bash
# 工具自测（离线、确定性、不碰真机）
python3 -m pytest tools/hw_selfcheck/test_selfcheck.py tools/onboarding/test_onboarding.py -q

# 复现一个仿真实验（固定种子，产物落被 gitignore 的 paper/experiments/）
python3 experiments/exp_ebno_decode.py --trials 50 --seed 20261001
```

见 [`mbdsdr_ai/README.md`](mbdsdr_ai/README.md)（工具面 / 摘除项 / 诚实标注项）与
[`experiments/README.md`](experiments/README.md)（口径红线与全部复现命令）。

### 4. 离线复验（本页命令的实测口径）

下列命令在**隔离 HOME/APPDATA、无硬件透传**的环境里真跑过（`MBDSDR_OFFLINE=1`、
`MBDSDR_NO_HW=1`、`QT_QPA_PLATFORM=offscreen`）。结果按命令性质分两类，不写会过期的通过数：

| 命令 | 无硬件时的真实行为 |
|---|---|
| `python3 tools/hw_selfcheck/selfcheck.py --json` | 退出码 1；JSON `summary` 给出 PASS/WARN/FAIL/SKIP 计数与结论「未检测到 RTL-SDR 真机设备」，逐项附 evidence 与 fix，不补假设备 |
| `python3 tools/onboarding/onboard.py --json --step detect` | 退出码 1；`detect` 步报「未检测到 RTL-SDR 设备（0bda:2838/2832）」并列修复建议，不进入采集 |
| `python3 -m pytest tools/hw_selfcheck/test_selfcheck.py tools/onboarding/test_onboarding.py -q` | 退出码 0；离线确定性用例通过，无硬件专项诚实 SKIP |
| `python3 experiments/exp_ebno_decode.py --trials 50 --seed 20261001` | 退出码 0；输出 Eb/N0 扫描与成功率，写 `paper/experiments/` 下 CSV、PNG、manifest，口径标 `synthetic`（仿真，非实测信号） |
| `python3 -m mbdsdr_ai.aigui_demo --out demo.html` | 退出码 0；生成静态 HTML 编排演示页，不碰硬件 |
| `python3 desktop/main.py --import-probe desktop.main_window` | 退出码 0；打印 `[probe] OK desktop.main_window`，不建窗口 |

**在 Windows 上**，`hw_selfcheck` 的 USB 段会走降级路径（`lsusb` 不可用 → 尝试扫 `/sys`，
而 Windows 并无该目录），
结论仍如实说明真机待办；这属于环境差异，不是脚本失败。真机接入后按 §2 重跑才有真机结论。

六条命令可一键复跑：`python3 scripts/repro_readme_offline.py`
（隔离 HOME/APPDATA、不碰真机；真机自检按预期返回 1 并如实打印，不粉饰成通过）。
复跑脚本为缩短耗时把仿真实验的 `--trials` 从上表的 50 降到 10，其余命令逐字一致；
固定种子不变，曲线形态可对照，别把两次样本量不同的数字直接比。

本页的链接、路径、命令参数与版本号由 [`scripts/verify_readme.py`](scripts/verify_readme.py)
自动校验（`python3 scripts/verify_readme.py --json`），测试钉在 `tests/test_readme_contract.py`。
文档改了、代码没跟上，守卫会直接红。

---

## 真机与云测状态（诚实声明）

- **云测环境无硬件**：无 USB / 串口 / 声卡 / GNSS。所有依赖真机的工具在云内返回诚实空态或失败
  （"请先 sdr_connect / sdr_open_iq_file / 配置地面站坐标"），不静默 mock、不补假点。
- C++ / Flutter / Python 三端的**真实硬件联调均需在用户本地真机完成后回传结果**；
  真机步骤与回传格式见 [`docs/learn/phase6/P2-hw-report-format.md`](docs/learn/phase6/P2-hw-report-format.md)。
- 云内可验证的部分（构建 offscreen + 测试信号、离线确定性测试、仿真口径实验）已在各端自测通过；
  真机专项（真实拔插、出声、USB-GNSS）标注为"待真机"，未宣称已验证。
- 三端测试基线与回归锚点见 [`AGENTS.md`](AGENTS.md) §5，随每轮工程实测更新；验证能力以**可复现命令**
  （见上方命令区）与云端 CI（`.github/workflows/ci.yml`）为准，不设数字标语——数字随时间浮动，
  写入即过期（AGENTS.md §1）。无硬件环境下的用例以诚实 SKIP 结算，换机 / 真机后需重测，不代表真机已验。
- 未解决项总账（逐项带阶段溯源）见 [`docs/learn/phase41/open-items.md`](docs/learn/phase41/open-items.md)。
- 「快速开始」里**不依赖真机**的六条命令已在隔离环境实跑，逐条真实行为见 §4；
  两条真机自检返回非零退出码是设计行为（诚实失败），不是脚本坏了。

## LLM 基线（诚实说明）

实验管线里的 LLM 基线（`experiments/exp_llm_baseline.py`）**仅在配置 API key 时才真调在线模型**。
云内 / 无 key 环境下：经典规则与 KNN 在本地真算，**LLM 整列输出 `PENDING_ONLINE_RUN`**——
不伪造数值、不联网、不花钱。key 只从环境变量读（`MBDSDR_LLM_API_KEY`），代码与文档中无任何 key 字面量。

桌面 / 移动端的 AI 聊天（`cpp/src/ai/llm_worker`）同理：无 key 或 `401/403` 时**诚实报 PENDING、不 mock 输出**；
transport 层已透传真实 HTTP 状态码（`cpp/src/ai/llm_client.cpp` 读 `HttpStatusCodeAttribute`），按码分类
（`429/5xx` 退避重试、`4xx` 终态如实报错）。但**真实在线重试 e2e 从未在带 key / 联网环境跑过**——
分类与退避路径由离线纯函数测试钉住，在线一次实跑仍属未决（见总账）。

## 受保护编码器（不内置）

DMR / P25 / C4FM 等涉及专利或许可受限的语音编码器**不内置**本仓库。需要时由用户自行上传合法的外部
解码器/二进制；系统在检测到外部组件时接线、没有时诚实标注不可用。本仓库为 **MIT 独立实现**实现，
上游项目（sdrpp / gnuradio / librtlsdr 等）的协议事实与交互范式是实现依据，
项目与许可归属见 [`NOTICE.md`](NOTICE.md)。

---

## 目录导览

```
cpp/        原生 C++/Qt 桌面 SDR（引擎/解调/录制/解码/卫星过境/世界地图/AI 助手）
mobile/     Flutter 手机端（Android/iOS）：rtl_tcp 客户端 + 解调 + 录音/回放 building blocks + GNSS
desktop/    Python/PySide6 桌面客户端（频谱/星象仪/AI 对话栏/真机链路，持续维护；启动见 desktop/README.md）
mbdsdr_ai/  Python 智能体内核：agent.py 装配、sdr_tools.py 工具面、workflow_engine、scheduler、pose
experiments/论文实验管线（common/ 公共库 + exp_*.py 脚本 + tests/）
tools/      hw_selfcheck（真机只读自检）、onboarding（联调向导）
docs/learn/ 上游精读笔记 + phase3–41 实现规格与审计（交付状态横幅见各 _PHASE*_SPEC.md 顶部；总账见 phase41/open-items.md）
paper/      论文与实验产物（被 .gitignore，生成物由脚本一键再生成）
repos/      克隆的上游源码（仅供学习，不入产品路径）
```

根目录另存历史设计稿（白皮书、IEEE 论文初稿、卫星指向雷达图）与硬件工程
（KiCad、BOM、接线表），均为阶段性交付物，不参与三端构建。

## 30 秒上手：NanoVNA 真机 dump
1. 接线：被测件接 PORT 1（S11），USB 接电脑（VID:PID 0x0483:0x5740，Windows 用 COMx）。
2. 跑：
   ```
   python3 -m mbdsdr_ai.nanovna_dump --port /dev/ttyACM0 --json-pretty --out sweep.json
   ```
3. 回传 sweep.json（+ 连接日志 version/info/cal）即可比对。
无设备可先 `--selftest` 跑通流程（合成演示，非真实测量）。详见 docs/learn/nanovna/hardware-quickstart.md。

## 工具能力（不在 README 复制浮动清单）

工具名、读写标记与域分组随版本变化，抄进首页当天就开始过期；本仓库**不在这里复刻表**。
唯一事实源与查看方式：

| 想看什么 | 去哪看 |
|---|---|
| 运行时实时清单 | Python `ToolRegistry.list_tools()` / C++ `registeredToolSpecs()`（写动作一律过手动 gate） |
| 自动生成的文档快照 | [`docs/learn/phase31/agent-tool-documentation.md`](docs/learn/phase31/agent-tool-documentation.md)（固定字段：name / description / JSON Schema / 读写标记 / 错误示例） |
| Python 内核工具面与诚实标注项 | [`mbdsdr_ai/README.md`](mbdsdr_ai/README.md) §2 |
| 任务→积木编排对照 | [`docs/learn/capability-map.md`](docs/learn/capability-map.md) |
| 域分组口径 | `docs/learn/phase31/agent-tool-documentation.md` 与 `docs/learn/phase31/_WAVE2_AGENT_LANDING.md` |

三通道（Agent / ControlHub / HTTP）行为一致且有测试钉住，写动作一律过人工门；
「没设备就报空、没校准就标口径」对所有通道同样成立。

## MCP 服务器（C++ mbdsdr-mcp 与 Python mbdsdr_ai/core/mcp_server.py）合并视图

两套 MCP server 同仓并存、互为镜像：

- **域分组一致**：两 server 的 `tools/list` 均按域分组（C++ 10 域、Python 11 域——Python 前 10 域与 C++ 同集合，另含「解码器长尾」扩展域；钉扎测试保证不漂移）。
- **C++ `mbdsdr-mcp`**：面向外部第三方 agent 的 stdio 接入面（接入细节见 `docs/learn/phase63/third-party-agent-mcp.md`），工具本体与桌面 Agent 同一套 `registeredToolSpecs()`，不另立实现。
- **Python `mbdsdr_ai/core/mcp_server.py`**：面向脚本/嵌入式控制器的本地调用面，便于在测试台与自动化脚本里直接驱动同一工具语义。
- **写工具一致门控**：两 server 的写工具统一走 `arm_manual` / 手动 gate，人在环确认，不会因接入面不同而绕过。

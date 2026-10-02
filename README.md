# MBDSDR · AI 定义无线电（AI-defined SDR）

把 SDR 的接收 / 解调 / 解码 / 卫星过境 / AR 指向，全部封装成 LLM 可调用的工具（function calling），
由智能体编排成"一键工作流"。本仓库是同一套体系在**三个端 + 一套论文实验 + 一套真机工具**上的落地。

| 组件 | 位置 | 是什么 |
|---|---|---|
| 原生桌面端 | [`cpp/`](cpp/README.md) | C++/Qt 原生 SDR 接收软件（UI 与 DSP 同进程，不依赖 Python 运行时） |
| 手机端 | [`mobile/`](mobile/README.md) | Flutter 一套代码（Android/iOS），手机作 AI 的"眼睛和手臂"：定位+罗盘指向+rtl_tcp 直连外置 SDR |
| Python 智能体内核 | [`mbdsdr_ai/`](mbdsdr_ai/README.md) | 约 280 个 LLM 可调用工具 + 工作流引擎的 Python 原型 |
| 论文实验管线 | [`experiments/`](experiments/README.md) | 可复现实验（Eb/N0 解码成功率 / 多普勒定轨 / AMR 基线 / LLM 基线），固定种子、逐图标口径 |
| 真机工具 | [`tools/`](tools/) | 一键只读自检 `hw_selfcheck` + 联调向导 `onboarding` |
| 学习与阶段规格 | [`docs/learn/`](docs/learn/) | 上游真实源码精读（sdrpp/gnuradio/librtlsdr…）+ phase3–6 实现规格与审计 |

许可证：**MIT**（见 [`LICENSE`](LICENSE)、第三方归属 [`NOTICE.md`](NOTICE.md)）。

---

## 快速开始

### 1. 桌面端（原生 C++/Qt）

```bash
cd cpp
cmake -S . -B build && cmake --build build -j"$(nproc)"
./build/mbdsdr        # 无 RTL-SDR 时自动切"测试信号（非硬件）"，界面明确标注，不造假
```

依赖（Qt 6.8 / librtlsdr 2.x / CMake 3.16+）、测试与完整功能清单见 [`cpp/README.md`](cpp/README.md)。

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

---

## 真机与云测状态（诚实声明）

- **云测环境无硬件**：无 USB / 串口 / 声卡 / GNSS。所有依赖真机的工具在云内返回诚实空态或失败
  （"请先 sdr_connect / sdr_open_iq_file / 配置地面站坐标"），不静默 mock、不补假点。
- C++ / Flutter / Python 三端的**真实硬件联调均需在用户本地真机完成后回传结果**；
  真机步骤与回传格式见 [`docs/learn/phase6/P2-hw-report-format.md`](docs/learn/phase6/P2-hw-report-format.md)。
- 云内可验证的部分（构建 offscreen + 测试信号、离线确定性测试、仿真口径实验）已在各端自测通过；
  真机专项（真实拔插、出声、USB-GNSS）标注为"待真机"，未宣称已验证。

## LLM 基线（诚实说明）

实验管线里的 LLM 基线（`experiments/exp_llm_baseline.py`）**仅在配置 API key 时才真调在线模型**。
云内 / 无 key 环境下：经典规则与 KNN 在本地真算，**LLM 整列输出 `PENDING_ONLINE_RUN`**——
不伪造数值、不联网、不花钱。key 只从环境变量读（`MBDSDR_LLM_API_KEY`），代码与文档中无任何 key 字面量。

## 受保护编码器（不内置）

DMR / P25 / C4FM 等涉及专利或许可受限的语音编码器**不内置**本仓库。需要时由用户自行上传合法的外部
解码器/二进制；系统在检测到外部组件时接线、没有时诚实标注不可用。本仓库为 **MIT 干净室**实现，
上游 GPL 项目（sdrpp / gnuradio / librtlsdr 等）仅作机制学习、不逐字复制（归属见 [`NOTICE.md`](NOTICE.md)）。

---

## 目录导览

```
cpp/        原生 C++/Qt 桌面 SDR（引擎/解调/录制/解码/卫星过境/世界地图/AI 助手）
mobile/     Flutter 手机端（Android/iOS）：rtl_tcp 客户端 + 解调 + 录音/回放 building blocks + GNSS
desktop/    早期 Python/Qt 桌面 GUI 原型（面板式，非当前主推原生端）
mbdsdr_ai/  Python 智能体内核：agent.py 装配、sdr_tools.py 工具面、workflow_engine、scheduler、pose
experiments/论文实验管线（common/ 公共库 + exp_*.py 脚本 + tests/）
tools/      hw_selfcheck（真机只读自检）、onboarding（联调向导）
docs/learn/ 上游精读笔记 + phase3/4/5/6 实现规格与审计（交付状态见各 _PHASE*_SPEC.md 顶部）
paper/      论文与实验产物（被 .gitignore，生成物由脚本一键再生成）
repos/      克隆的上游源码（仅供学习，不入产品路径）
```

根目录另存历史设计稿（白皮书、IEEE 论文初稿、卫星指向雷达图）与硬件工程
（KiCad、BOM、接线表），均为阶段性交付物，不参与三端构建。

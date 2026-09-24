# MBDSDR

[中文](#中文) ｜ [日本語](#日本語) ｜ [English](#english)

---

## 中文

MBDSDR 是一套「AI 定义无线电」（AI-Defined Radio, AIDR）整机项目。它把传统 SDR 的全部操作——调谐、找信号、解调、录制、卫星指向——抽象成一组可被大模型通过 MCP 协议调用的工具，让人用自然语言指挥接收机，而不是自己去点频谱仪上的旋钮。

仓库同时包含三部分：Python 软件框架（AI 内核 + MCP 服务端/客户端 + 桌面 GUI）、KiCad 硬件设计（AI-SDR-Mini 主板）、ESP32 固件。硬件还在迭代中，软件部分可以脱离真实硬件跑起来：自带一个 WebSocket 设备模拟器，MCP 工具可以在没有 SDR 前端的情况下被 AI Host 调用和测试。

### 解决什么问题

今天 SDR 硬件已经便宜到 20 美元一块，但软件门槛仍然很高：GNU Radio、SDR++ 要求使用者懂信号处理，找信号、认调制、判断干扰源靠的是多年经验，调谐→录制→回放→分析这条链路每一步都要人手操作。MBDSDR 的思路是把这些重复、不需要人动脑的环节交给 AI：人说一句「帮我扫一下 144–146 MHz 有没有跳频信号」，AI 自己调频率、采频谱、跑检测、把结论报回来。

概念框架见 [`AI定义无线电-概念定义与框架-v2.1.md`](AI定义无线电-概念定义与框架-v2.1.md)，里面给了 AIDR 的正式定义、与认知无线电/AI 辅助 SDR 的边界，以及「辅助调控→自主分析→自进化」的演进路线。

### 核心功能

软件框架侧（`mbdsdr_ai/`）已经实现的模块：

- **接收机控制**：频率、增益、采样率、带宽、解调方式切换，设备枚举与切换（`sdr_backend.py`、`radio_control.py`、`frequency_manager.py`）。
- **频谱感知**：频谱采集、缩放、平移、信号自动检测与截图（`spectrum_processor.py`、`spectrum_sensing.py`、`signal_spectrum.py`）。
- **AI 辅助分析**：AI 扫频找中心频点、调制方式识别、跳频信号检测、IQ 前端校正（`cfo.py`、`analog_demod.py`、`digital_modes.py`）。
- **数字模式解码**：FT8（含 LDPC 编解码、callsign 解包）、SSTV、NOAA APT 气象云图、ADS-B、AX.25/APRS、CW、RDS、WFM 立体声（`ft8_*.py`、`sstv_decoder.py`、`noaa_apt_lite.py`、`adsb*.py`、`ax25.py`、`cw_decoder.py`、`rds_lite.py`、`wfm_stereo_lite.py`）。
- **基带录制**：IQ 文件录制、列表、回放分析（`baseband_io.py`、`file_tracker.py`）。
- **卫星与天文**：基于 SGP4/SDP4 的轨道传播、多普勒计算、天线指向辅助、天空图（`orbit.py`、`astronomy.py`、`meteor_sat.py`、`gnss_monitor.py`）。
- **Agent 内核**：LLM 编排、上下文管理、工具注册表、钩子、自学习、自进化（可读写代码、跑测试、git commit、一键回滚）（`agent.py`、`orchestrator.py`、`context_manager.py`、`tool_registry.py`、`skill_registry.py`、`self_evolution.py`、`guardian.py`、`sandbox.py`）。

MCP 服务端（`mbdsdr_ai_mcp_server.py`）通过 stdio 上的 JSON-RPC 2.0 把上述能力暴露给 Claude Desktop、Cursor、Cline、Kilo Code、OpenCode 等 AI Host。工具按 sdr / spectrum / ai_sdr / decode / record / satellite / sensor / self_evolution / agent_core 分组，完整清单见 [`mcp_server.example.json`](mcp_server.example.json)。

硬件侧（`ai-sdr-mini-kicad/`）：

- KiCad 工程：`ai-sdr-mini.kicad_pro` / `.kicad_sch` / `.kicad_pcb`，网表 `ai-sdr-mini.net`。
- BOM：`ai-sdr-mini-bom.xml`、`BOM-MBDSDR-Mini-v0.7-数据手册修正版.csv`。
- ESP32 固件：`ai_sdr_mini_firmware_v0.5_WebOTA.ino`，支持 Web OTA 升级，协议头文件 `mbdsdr_protocol.h`。
- 接线表与原理图连接表见根目录 `ai-sdr-mini-接线表-v1.0-匹配0917工程.md` 和 `原理图连接表-v0.7-匹配立创工程.md`。

### 目录结构

```
MBDSDR/
├── mbdsdr_ai/                  # AI 内核：DSP、解码、agent、卫星、工具
├── mbdsdr_ai_mcp_server.py     # MCP 服务端（stdio，给 AI Host 调用）
├── mbdsdr_mcp_client.py        # MCP 客户端（连 ai-sdr-mini 硬件，WebSocket）
├── mbdsdr_sim_server.py        # 无硬件时的设备模拟器
├── mbdsdr-mobile.html          # 移动端网页控制面板
├── mcp_server.example.json     # MCP Host 接入配置示例
├── desktop/                    # PySide6 桌面 GUI
│   ├── main.py / main_window.py
│   ├── spectrum_widget.py / rf_sky_view.py
│   └── requirements.txt
├── ai-sdr-mini-kicad/          # KiCad 硬件工程 + ESP32 固件
├── scripts/                    # rtl_fm 监听、自检、Chromebook 安装脚本
├── experiments/                # 算法实验脚本
├── tests/                      # 集成与工具自检
├── docs/                       # 额外文档（ChromeOS Crostini SDR 等）
└── requirements.txt
```

### 安装

需要 Python 3.10+。

```bash
git clone https://github.com/HaohanHe/MBDSDR.git
cd MBDSDR
python -m pip install -r requirements.txt
```

核心依赖是 numpy、requests、websocket-client、websockets、sgp4、Pillow。scipy、skyfield、pysstv 是可选的，缺了会自动降级到内置简化实现。pyserial 和 SoapySDR 只在接真实硬件时需要，按系统情况单独装。

桌面 GUI 依赖 PySide6，单独装：

```bash
python -m pip install -r desktop/requirements.txt
```

### 运行

**MCP 服务端自测**（不需要硬件）：

```bash
python3 mbdsdr_ai_mcp_server.py --cli list_tools
python3 mbdsdr_ai_mcp_server.py --cli call_tool sdr_status '{}'
```

**接 AI Host**：把 [`mcp_server.example.json`](mcp_server.example.json) 里的 `mbdsdr` 段复制到 Claude Desktop / Cursor 的 MCP 配置里，把 `args` 改成你本机 MBDSDR 目录的绝对路径。API Key 不要写进配置文件，用环境变量 `MBDSDR_API_KEY` 或 `~/.mbdsdr/config.json`。示例配置默认指向 SiliconFlow 上的 Qwen 模型，你可以在 `env` 里改成任何 OpenAI 兼容的 endpoint。

**桌面 GUI**：

```bash
python3 desktop/main.py
```

**无硬件时跑模拟器**：

```bash
python3 mbdsdr_sim_server.py        # 起一个 WebSocket 假设备
python3 mbdsdr_mcp_client.py        # 客户端连上去
```

### 状态说明

- 概念框架 v2.1 是草案，待同行评审。
- AI-SDR-Mini 硬件当前是 v0.7 原理图、v0.5 固件，BOM 和接线表在持续更新，不要直接打板就上 SDR 前端，先按接线表核对。
- 部分高级信号处理路径（scipy/skyfield）在缺失时走 numpy 简化版，精度和性能会下降。

### 许可证

GPL-3.0，见 [LICENSE](LICENSE)。

---

## 日本語

MBDSDR は「AI 定義無線」（AI-Defined Radio, AIDR）のシステムプロジェクトです。従来の SDR の操作——同調、信号探し、復調、録画、衛星ポインティング——をすべて MCP プロトコル経由で LLM から呼び出せるツールとして抽象化し、ユーザが自然言語で受信機を指示できるようにすることを目指しています。スペアナのつまみを自分で回すのではなく、「次に何をするか」を AI に伝える使い方です。

リポジトリには三つの部分が同梱されています。Python 製のソフトウェアフレームワーク（AI コア + MCP サーバ/クライアント + デスクトップ GUI）、KiCad によるハードウェア設計（AI-SDR-Mini マザーボード）、そして ESP32 ファームウェアです。ハードウェアはまだ反復開発中ですが、ソフトウェア部分は実機がなくても動きます。WebSocket 製のデバイスシミュレータが付属しており、SDR フロントエンドがなくても MCP ツールを AI Host から呼び出して試せます。

### 解決しようとしている問題

いまや SDR ハードウェアは 20 ドル程度で入手できますが、ソフトウェアの敷居は依然として高いままです。GNU Radio や SDR++ を使うには信号処理の知識が要りますし、信号を見つけ、変調方式を判別し、妨害波の原因を推測するには経験が必要です。同調→録画→再生→分析という一連の作業は、すべて人手で段取りしなければなりません。MBDSDR は、こうした反復的で判断を要しない作業を AI に肩代わりさせる設計です。「144 から 146 MHz の間に周波数ホッピング信号がないか調べて」と一言指示すれば、AI が周波数を動かし、スペクトラムを取り、検出を走らせ、結果をまとめて返してきます。

コンセプトフレームワークの全文は [`AI定义无线电-概念定义与框架-v2.1.md`](AI定义无线电-概念定义与框架-v2.1.md) にあります。AIDR の正式な定義、認知無線や AI 支援 SDR との境界、そして「支援調整→自律分析→自己進化」という能力ロードマップがまとめてあります。

### 主要な機能

ソフトウェアフレームワーク（`mbdsdr_ai/`）に実装済みのモジュール：

- **受信機制御**：周波数、ゲイン、サンプリングレート、帯域幅、復調方式の切り替え、デバイス列挙と切り替え（`sdr_backend.py`、`radio_control.py`、`frequency_manager.py`）。
- **スペクトラムセンシング**：スペクトラム取得、ズーム、パン、信号の自動検出とスクリーンショット（`spectrum_processor.py`、`spectrum_sensing.py`、`signal_spectrum.py`）。
- **AI 支援分析**：AI によるスイープで中心周波数を探し、変調を識別し、周波数ホッピングを検出し、IQ フロントエンドの校正を行う（`cfo.py`、`analog_demod.py`、`digital_modes.py`）。
- **デジタルモードデコード**：FT8（LDPC 符号復号、コールサインアンパック含む）、SSTV、NOAA APT 気象画像、ADS-B、AX.25/APRS、CW、RDS、WFM ステレオ（`ft8_*.py`、`sstv_decoder.py`、`noaa_apt_lite.py`、`adsb*.py`、`ax25.py`、`cw_decoder.py`、`rds_lite.py`、`wfm_stereo_lite.py`）。
- **ベースバンド録画**：IQ ファイルの録画、一覧、再生と分析（`baseband_io.py`、`file_tracker.py`）。
- **衛星と天文**：SGP4/SDP4 による軌道伝播、ドップラー計算、アンテナポインティング支援、スカイビュー（`orbit.py`、`astronomy.py`、`meteor_sat.py`、`gnss_monitor.py`）。
- **エージェントコア**：LLM オーケストレーション、コンテキスト管理、ツールレジストリ、フック、自己学習、自己進化（コードの読み書き、テスト実行、git commit、ワンクリックロールバックまで）（`agent.py`、`orchestrator.py`、`context_manager.py`、`tool_registry.py`、`skill_registry.py`、`self_evolution.py`、`guardian.py`、`sandbox.py`）。

MCP サーバ（`mbdsdr_ai_mcp_server.py`）は、stdio 上の JSON-RPC 2.0 経由で上記の機能を Claude Desktop、Cursor、Cline、Kilo Code、OpenCode といった AI Host に公開します。ツールは sdr / spectrum / ai_sdr / decode / record / satellite / sensor / self_evolution / agent_core のグループに分かれており、一覧は [`mcp_server.example.json`](mcp_server.example.json) にあります。

ハードウェア（`ai-sdr-mini-kicad/`）：

- KiCad プロジェクト：`ai-sdr-mini.kicad_pro` / `.kicad_sch` / `.kicad_pcb`、ネットリスト `ai-sdr-mini.net`。
- BOM：`ai-sdr-mini-bom.xml`、`BOM-MBDSDR-Mini-v0.7-数据手册修正版.csv`。
- ESP32 ファームウェア：`ai_sdr_mini_firmware_v0.5_WebOTA.ino`。Web OTA アップデート対応、プロトコルヘッダは `mbdsdr_protocol.h`。
- 配線表と原理接続表は、リポジトリ直下の `ai-sdr-mini-接线表-v1.0-匹配0917工程.md` と `原理图连接表-v0.7-匹配立创工程.md` を参照してください。

### ディレクトリ構成

```
MBDSDR/
├── mbdsdr_ai/                  # AI コア：DSP、デコード、エージェント、衛星、ツール
├── mbdsdr_ai_mcp_server.py     # MCP サーバ（stdio、AI Host から呼ぶ）
├── mbdsdr_mcp_client.py        # MCP クライアント（ai-sdr-mini と WebSocket で接続）
├── mbdsdr_sim_server.py        # 実機がないときのデバイスシミュレータ
├── mbdsdr-mobile.html          # モバイル Web コントロールパネル
├── mcp_server.example.json     # MCP Host 接続設定の例
├── desktop/                    # PySide6 デスクトップ GUI
│   ├── main.py / main_window.py
│   ├── spectrum_widget.py / rf_sky_view.py
│   └── requirements.txt
├── ai-sdr-mini-kicad/          # KiCad ハードウェア + ESP32 ファームウェア
├── scripts/                    # rtl_fm リッスン、セルフチェック、Chromebook セットアップ
├── experiments/                # アルゴリズム実験スクリプト
├── tests/                      # 結合テストとツール自己チェック
├── docs/                       # 追加ドキュメント（ChromeOS Crostini SDR など）
└── requirements.txt
```

### インストール

Python 3.10 以上が必要です。

```bash
git clone https://github.com/HaohanHe/MBDSDR.git
cd MBDSDR
python -m pip install -r requirements.txt
```

コア依存は numpy、requests、websocket-client、websockets、sgp4、Pillow です。scipy、skyfield、pysstv はオプションで、インストールされていないと内蔵の簡易実装に自動でフォールバックします。pyserial と SoapySDR は実機を繋ぐ場合だけ必要で、環境に合わせて個別に導入してください。

デスクトップ GUI は PySide6 を別途入れます。

```bash
python -m pip install -r desktop/requirements.txt
```

### 実行

**MCP サーバのセルフテスト**（ハードウェア不要）：

```bash
python3 mbdsdr_ai_mcp_server.py --cli list_tools
python3 mbdsdr_ai_mcp_server.py --cli call_tool sdr_status '{}'
```

**AI Host に接続する**：[`mcp_server.example.json`](mcp_server.example.json) の `mbdsdr` セクションを Claude Desktop や Cursor の MCP 設定にコピーし、`args` をローカルの MBDSDR ディレクトリへの絶対パスに書き換えてください。API キーは設定ファイルに書かず、環境変数 `MBDSDR_API_KEY` または `~/.mbdsdr/config.json` を使います。例では SiliconFlow 上の Qwen モデルを指していますが、`env` を書き換えれば OpenAI 互換の任意のエンドポイントに差し替えられます。

**デスクトップ GUI**：

```bash
python3 desktop/main.py
```

**実機なしでシミュレータを動かす**：

```bash
python3 mbdsdr_sim_server.py        # WebSocket の疑似デバイスを起動
python3 mbdsdr_mcp_client.py        # クライアントを接続
```

### 現状について

- コンセプトフレームワーク v2.1 はドラフトで、査読待ちの段階です。
- AI-SDR-Mini ハードウェアは現状 v0.7 回路図、v0.5 ファームウェアで、BOM と配線表は更新が続いています。基板をそのまま製造して SDR フロントエンドを載せる前に、配線表との突き合わせを行ってください。
- scipy や skyfield が入っていない環境では、一部の高度な信号処理が numpy ベースの簡易版に切り替わり、精度と性能が低下します。

### ライセンス

GPL-3.0 です。詳細は [LICENSE](LICENSE) を参照してください。

---

## English

MBDSDR is an end-to-end AI-Defined Radio (AIDR) project. It takes the operations of a traditional SDR tuner, signal finding, demodulation, recording, and satellite pointing, and exposes them as a set of callable tools that a large model can drive over MCP. The idea is that you tell the receiver what you want in natural language instead of turning knobs on a spectrum analyzer.

The repository holds three things together: a Python software framework (AI kernel plus MCP server and client plus a desktop GUI), KiCad hardware design for the AI-SDR-Mini board, and ESP32 firmware. The hardware is still under iteration, but the software side runs without real hardware. A WebSocket device simulator is included so the MCP tools can be exercised from an AI host with no SDR front end attached.

### What problem this solves

SDR hardware now costs around twenty US dollars, but the software remains hard to use. GNU Radio and SDR++ assume working knowledge of signal processing, and finding signals, classifying modulation, and judging interference sources takes years of experience. The chain tune, record, replay, analyze still has to be staged by hand at every step. MBDSDR hands the repetitive, low-judgment parts to AI. Ask it to sweep 144 to 146 MHz for frequency-hopping signals and the AI moves the frequency, samples the spectrum, runs detection, and reports back.

The full concept paper is in [`AI定义无线电-概念定义与框架-v2.1.md`](AI定义无线电-概念定义与框架-v2.1.md). It gives a formal definition of AIDR, draws the line between AIDR, cognitive radio, and AI-assisted SDR, and lays out the evolution path from assisted tuning to autonomous analysis to self-evolving behavior.

### Implemented features

Inside the software framework (`mbdsdr_ai/`):

- **Receiver control**: frequency, gain, sample rate, bandwidth, demodulator switching, device enumeration and selection (`sdr_backend.py`, `radio_control.py`, `frequency_manager.py`).
- **Spectrum sensing**: capture, zoom, pan, automatic signal detection, screenshots (`spectrum_processor.py`, `spectrum_sensing.py`, `signal_spectrum.py`).
- **AI-assisted analysis**: AI sweep to find the center frequency, modulation classification, FHSS detection, IQ front-end correction (`cfo.py`, `analog_demod.py`, `digital_modes.py`).
- **Digital mode decoding**: FT8 including LDPC coding and callsign unpacking, SSTV, NOAA APT weather images, ADS-B, AX.25/APRS, CW, RDS, WFM stereo (`ft8_*.py`, `sstv_decoder.py`, `noaa_apt_lite.py`, `adsb*.py`, `ax25.py`, `cw_decoder.py`, `rds_lite.py`, `wfm_stereo_lite.py`).
- **Baseband recording**: IQ file recording, listing, replay and analysis (`baseband_io.py`, `file_tracker.py`).
- **Satellite and astronomy**: SGP4/SDP4 propagation, Doppler, antenna pointing guidance, sky view (`orbit.py`, `astronomy.py`, `meteor_sat.py`, `gnss_monitor.py`).
- **Agent core**: LLM orchestration, context management, tool registry, hooks, self-learning, and self-evolution that can read and write code, run tests, git commit, and roll back with one click (`agent.py`, `orchestrator.py`, `context_manager.py`, `tool_registry.py`, `skill_registry.py`, `self_evolution.py`, `guardian.py`, `sandbox.py`).

The MCP server (`mbdsdr_ai_mcp_server.py`) exposes these over stdio JSON-RPC 2.0 to AI hosts such as Claude Desktop, Cursor, Cline, Kilo Code, and OpenCode. Tools are grouped under sdr, spectrum, ai_sdr, decode, record, satellite, sensor, self_evolution, and agent_core. The full list is in [`mcp_server.example.json`](mcp_server.example.json).

On the hardware side (`ai-sdr-mini-kicad/`):

- KiCad project files: `ai-sdr-mini.kicad_pro`, `.kicad_sch`, `.kicad_pcb`, netlist `ai-sdr-mini.net`.
- BOM: `ai-sdr-mini-bom.xml` and `BOM-MBDSDR-Mini-v0.7-数据手册修正版.csv`.
- ESP32 firmware: `ai_sdr_mini_firmware_v0.5_WebOTA.ino`, with Web OTA update support. Protocol header is `mbdsdr_protocol.h`.
- Wiring notes and schematic connectivity lists live at the repository root as `ai-sdr-mini-接线表-v1.0-匹配0917工程.md` and `原理图连接表-v0.7-匹配立创工程.md`.

### Layout

```
MBDSDR/
├── mbdsdr_ai/                  # AI kernel: DSP, decoders, agent, satellites, tools
├── mbdsdr_ai_mcp_server.py     # MCP server (stdio, called by AI hosts)
├── mbdsdr_mcp_client.py        # MCP client (talks to ai-sdr-mini over WebSocket)
├── mbdsdr_sim_server.py        # Hardware simulator for offline testing
├── mbdsdr-mobile.html          # Mobile web control panel
├── mcp_server.example.json     # Example MCP host configuration
├── desktop/                    # PySide6 desktop GUI
│   ├── main.py / main_window.py
│   ├── spectrum_widget.py / rf_sky_view.py
│   └── requirements.txt
├── ai-sdr-mini-kicad/          # KiCad hardware project + ESP32 firmware
├── scripts/                    # rtl_fm listen, self-check, Chromebook setup
├── experiments/                # Algorithm experiment scripts
├── tests/                      # Integration and tool self-checks
├── docs/                       # Extra docs (ChromeOS Crostini SDR, etc.)
└── requirements.txt
```

### Install

Python 3.10 or newer is required.

```bash
git clone https://github.com/HaohanHe/MBDSDR.git
cd MBDSDR
python -m pip install -r requirements.txt
```

Core dependencies are numpy, requests, websocket-client, websockets, sgp4, and Pillow. scipy, skyfield, and pysstv are optional; if they are missing the code falls back to lighter built-in implementations. pyserial and SoapySDR are only needed when you attach real hardware, install them separately for your platform.

The desktop GUI needs PySide6:

```bash
python -m pip install -r desktop/requirements.txt
```

### Run

**Self-test the MCP server** (no hardware needed):

```bash
python3 mbdsdr_ai_mcp_server.py --cli list_tools
python3 mbdsdr_ai_mcp_server.py --cli call_tool sdr_status '{}'
```

**Connect an AI host**: copy the `mbdsdr` block from [`mcp_server.example.json`](mcp_server.example.json) into your Claude Desktop or Cursor MCP config, and change `args` to the absolute path of your local MBDSDR checkout. Do not put the API key in the config file. Use the `MBDSDR_API_KEY` environment variable or `~/.mbdsdr/config.json`. The example points at a Qwen model on SiliconFlow; edit `env` to use any OpenAI-compatible endpoint.

**Desktop GUI**:

```bash
python3 desktop/main.py
```

**Run the simulator with no hardware**:

```bash
python3 mbdsdr_sim_server.py        # start a fake WebSocket device
python3 mbdsdr_mcp_client.py        # connect a client to it
```

### Status notes

- The concept framework v2.1 is a draft under peer review.
- The AI-SDR-Mini hardware is at schematic v0.7 and firmware v0.5. The BOM and wiring notes are still moving. Do not fab a board and mount the SDR front end without cross-checking against the wiring tables.
- Where scipy or skyfield is absent, some advanced signal processing paths use numpy-only fallbacks, which are less accurate and slower.

### License

GPL-3.0, see [LICENSE](LICENSE).

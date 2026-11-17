# 本机 AI 资源清单与 MBDSDR 对接评估（2026-11-17）

> 范围：只读调研。未改项目源码、未跑 pytest、未启动 GUI、未联网下载/安装。
> 所有事实均给出可复现的只读命令；无法用命令直接证明的写「未证实」。
> 环境：Windows，主解释器 `C:\Users\xxhzb\AppData\Local\Programs\Python\Python314\python.exe`。

---

## 1. 事实清单

| 组件 | 是否存在 | 版本 | 证据命令与输出摘要 |
| --- | --- | --- | --- |
| 主 Python | 是 | 3.14.0 | `python -V` → `Python 3.14.0` |
| numpy / scipy | 是 | 2.4.6 / 1.18.0 | `python -c "import numpy,scipy;print(numpy.__version__,scipy.__version__)"` |
| PySide6 | 是 | 6.12.0 | `python -c "import PySide6;print(PySide6.__version__)"` |
| requests | 是 | 2.34.2 | `python -c "import requests;print(requests.__version__)"` |
| skyfield / sgp4 / sounddevice / pyrtlsdr | 是 | 1.54 / 2.27 / 0.5.6 / 0.2.93 | `python -c "import importlib.metadata as m;print(m.version('skyfield'))"` 等 |
| torch（主环境） | **否** | — | `python -c "import torch"` → `ModuleNotFoundError: No module named 'torch'` |
| transformers（主环境） | **否** | — | 同上 → `ModuleNotFoundError: No module named 'transformers'` |
| huggingface_hub（主环境） | **否** | — | `ModuleNotFoundError` |
| onnxruntime / onnx | **否** | — | `ModuleNotFoundError: No module named 'onnxruntime'`；`onnx` 同样缺失 |
| sentencepiece | **否** | — | `ModuleNotFoundError` |
| llama_cpp | **否** | — | `ModuleNotFoundError` |
| faster-whisper / whisper | **否** | — | 两者均 `ModuleNotFoundError` |
| soundfile（主环境） | **否** | — | `ModuleNotFoundError`（自带 Python 3.10 的副本里有 0.12.1，见下） |
| HF 模型缓存 | **否** | — | `Test-Path "$env:USERPROFILE\.cache\huggingface"` → `False`；`~/.cache` 下无 huggingface/torch/whisper |
| 本地 LLM 权重（gguf/safetensors/ggml） | **否** | — | `Get-ChildItem d:\mbdsdr -Recurse -Include *.gguf,*.safetensors,*.ggml` → 0 个；`D:\` 深度 6 扫描同样 0 个 |
| 本地推理服务 | **否** | — | `Test-NetConnection 127.0.0.1 -Port 11434/1234/8000/8080/5000/7860/5001/8888/9000` → 全部 `False`；`Get-NetTCPConnection -State Listen` 未见这些端口 |
| ONNX 模型 A：DeepCW | 是 | 42 类 CTC，采样 3200 Hz | `D:\mbdsdr\scratch\refs\sdroxide\crates\sdroxide-deepcw\assets\model.onnx`，15,139,839 B；`model.onnx.json`：`blank_index=41`、`400–1200 Hz`、`65` 频点 |
| ONNX 模型 B：Piper 英文女声 TTS | 是 | medium，22.05 kHz | `D:\mbdsdr\scratch\refs\sdroxide\assets\voices\en_US-hfc_female-medium.onnx`，63,201,294 B；`MODEL_CARD` 标 CC BY-NC-SA 4.0 |
| `deepseek-harness-master` | 是（源码） | `@deepseek-ai/dsh-root` 0.2.2-rc.1 | 目录含 `packages/ apps/ native/ python/`；`package.json` 为 Node 工作区；`node_modules` **缺失**；无 `pnpm`；无任何 `.bin/.pt/.pth/.ckpt` 权重；`AGENTS.md:128` 说明需 `DEEPSEEK_API_KEY` / 可选 `DEEPSEEK_BASE_URL` |
| Node 运行时 | 是 | v24.15.0 | `node -v`；满足 `engines: node >=24.9.0`，但缺 pnpm 与依赖，仍不可直接运行 |
| `D:\BaiduNetdiskDownload\some-windows-v0.0.1` | 是（第三方应用） | SOME（Singing-Oriented MIDI Extractor） | 自带 Python 3.10.11 + `torch 2.1.0+cpu`（`cuda False`，`_C.cp310-win_amd64.pyd`）；含 470,801,953 B 的 `.ckpt`；`fairseq/onnx/lightning/h5py` 均缺失 → 不可运行；MIT |
| MBDSDR 配置 `~/.mbdsdr/config.json` | 是 | — | `base_url=https://api.siliconflow.cn/v1`、`model=Qwen/Qwen3.6-35B-A3B`、`api_key` 为空（`len=0`） |
| 离线星表数据 | 是 | — | `python -B -c "...from mbdsdr_ai.skyengine.star_catalog import get_catalog; print(type(get_catalog()).__name__)"` → `StarCatalog` |

补充事实：

- 主环境 `python -m pip list` 无任何 ML 推理运行时；也没有离线 wheel 可装：
  `Get-ChildItem "$env:LOCALAPPDATA\pip\Cache" -Recurse -Filter *.whl -ErrorAction SilentlyContinue | Where-Object {$_.Name -match 'onnx|torch|transformers|whisper'}` → 0 行。
- `deepseek-harness` 是 **agent harness（客户端/框架）**，不是模型权重；其 DeepSeek 适配默认端点为公网 `https://api.deepseek.com/anthropic`（`.agents/notes/.../2026-06-19-real-api-e2e-ci.zh.md:52`）。`Get-Command pnpm -ErrorAction SilentlyContinue` → 空。
- 两个 ONNX 均位于 `scratch/refs/sdroxide`（第三方 Rust 项目参考源码），不属于 MBDSDR 构建产物；仓库内没有用它们推理的 Python 代码。

---

## 2. 可用能力矩阵

| 能力 | 依赖 | 是否需要联网 | 当前是否可用 | 验证命令 |
| --- | --- | --- | --- | --- |
| 离线星表/星官查询 | numpy（+skyfield 可选） | 否 | **可用** | `python -B -c "import sys;sys.path.insert(0,r'd:\mbdsdr\MBDSDR');from mbdsdr_ai.skyengine.star_catalog import get_catalog;print(type(get_catalog()).__name__)"` |
| 规则式信号/调制分类 | numpy | 否 | **可用** | `python -B -c "import sys;sys.path.insert(0,r'd:\mbdsdr\MBDSDR');from mbdsdr_ai.channel_analyzer import classify_signal;print(classify_signal(150,20,0.3))"` → `CW/窄带` |
| ANR 谱减降噪 | numpy | 否 | **可用** | `python -B -c "import sys,numpy as np;sys.path.insert(0,r'd:\mbdsdr\MBDSDR');from mbdsdr_ai.anr import SpectralSubtractionANR;o=SpectralSubtractionANR().process(np.zeros(4096,dtype=np.float32));print(o.dtype,o.shape)"` → `float32 (4096,)` |
| 星图自然语言交互 | 外部 OpenAI 兼容 LLM | **是** | **不可用**（无本地服务、`api_key` 空；`MBDSDR_OFFLINE` 下会直接拒） | `Test-NetConnection 127.0.0.1 -Port 11434 -InformationLevel Quiet` → `False`；`Test-NetConnection 127.0.0.1 -Port 1234 -InformationLevel Quiet` → `False` |
| 本地 LLM 推理（Ollama/LM Studio/vLLM） | 权重 + 推理服务 | 否（装好后） | **不可用**（无权重、无服务） | 同上端口探测；`Get-ChildItem Env:OLLAMA*` → 空 |
| ONNX 推理（DeepCW / Piper） | onnxruntime 或 Rust rten | 安装需联网 | **不可用**（运行时缺失、无离线 wheel） | `python -c "import onnxruntime"` → `ModuleNotFoundError` |
| 语音转写 ASR | faster-whisper/whisper + 权重 | 下载需联网 | **不可用** | `python -c "import faster_whisper"` → `ModuleNotFoundError` |
| 语音合成 TTS | Piper onnx + 运行时 + CC BY-NC-SA | 运行时需联网 | **不可用**（且许可证限制商用） | `Test-Path "D:\mbdsdr\scratch\refs\sdroxide\assets\voices\en_US-hfc_female-medium.onnx"` → `True`，但无运行时 |
| 运行 deepseek-harness | pnpm + node_modules + API Key | **是** | **不可用** | `Test-Path "d:\mbdsdr\deepseek-harness-master\deepseek-harness-master\node_modules"` → `False` |

### 2.1 不联网、不下载即可验证的最小路径

按顺序执行（均为只读；`-B` 避免写 `__pycache__`）：

```powershell
# 1) 确认没有本地 LLM 运行时/推理库
python -c "import torch"            # 期望 ModuleNotFoundError
python -c "import onnxruntime"      # 期望 ModuleNotFoundError

# 2) 确认没有本地推理服务（任一为 True 才需要继续查）
Test-NetConnection 127.0.0.1 -Port 11434 -InformationLevel Quiet   # Ollama
Test-NetConnection 127.0.0.1 -Port 1234  -InformationLevel Quiet   # LM Studio

# 3) 确认项目自带的纯 numpy 能力仍可用（不联网）
python -B -c "import sys;sys.path.insert(0,r'd:\mbdsdr\MBDSDR');from mbdsdr_ai.channel_analyzer import classify_signal;print(classify_signal(150,20,0.3))"
python -B -c "import sys;sys.path.insert(0,r'd:\mbdsdr\MBDSDR');from mbdsdr_ai.anr import SpectralSubtractionANR;print(SpectralSubtractionANR().enabled)"

# 4) 确认模型权重到底有没有（0 行 = 没有）
Get-ChildItem d:\mbdsdr -Recurse -Include *.gguf,*.safetensors,*.ggml -File -ErrorAction SilentlyContinue
```

本次实测输出：步骤 1 两个 `ModuleNotFoundError`；步骤 2 均 `False`；步骤 3 分别 `CW/窄带`、`False`；步骤 4 无输出。

---

## 3. 与 MBDSDR 的对接点（只指位置，不改代码）

### 3.1 频谱 / 信号分类

- 现成规则出口有两个，且都明确是「规则先行」：
  - `mbdsdr_ai/channel_analyzer.py:53-64` `classify_signal()`，函数注释写明「可替换为 ML 模型」；
  - `mbdsdr_ai/analysis/modulation_classifier.py:175-193` `ModulationClassifier.classify()`（特征字典在 `151-168`）。
- 调用现场：`mbdsdr_ai/core/controller.py:827` 已在跑 `ModulationClassifier().classify(iq, sample_rate)`；工具面在 `mbdsdr_ai/blind_param_est.py:165`（`modulation_classify`，注册于 `:460`）。
- 结论：**要接本地模型，最省事的接缝是 `ModulationClassifier.classify()` 末尾的旁路**（特征已算好，模型只需吃 `features` 字典），而不是改 DSP 链。当前无模型/无 onnxruntime，只能先做「规则 + 轻量统计」，不能声称已接 ML。

### 3.2 解调后语音：降噪 / 转写

- 降噪已实现并已接线：`mbdsdr_ai/anr.py:46`（`SpectralSubtractionANR`）、`:130`（`process`）；音频泵 `mbdsdr_ai/core/controller.py:675-681`，控制入口 `:1610-1625`（开关/强度/学习噪声底）。
- 转写：**全仓无 ASR 代码、无 ASR 依赖、无 ASR 权重**。若未来接 faster-whisper，合理接点仍是 `controller.py:675-681` 音频泵（ANR 之后、送声卡之前/并行 tap），但需要下载依赖与权重——不属于本次「不联网」范围。
- 注意：主环境无 `soundfile`；不要照搬第三方示例里的 `sf.write`，应沿用项目现有音频写盘模块。

### 3.3 星图侧自然语言交互

- LLM 通道是标准 OpenAI 兼容客户端：`mbdsdr_ai/model_manager.py:114-133`（`base_url/model/api_key` 可配），由 `mbdsdr_ai/agent.py:252-260` 用 `~/.mbdsdr/config.json` 构造；默认值在 `mbdsdr_ai/config.py:26-30`。
- 离线门已存在：`mbdsdr_ai/net_policy.py:16` + `model_manager.py:265-276`（`MBDSDR_OFFLINE` 下直接返回 `llm_offline`，不会重试到超时）。构造期不拉 `/v1/models`（`agent.py:262-267`），所以无网启动不会卡。
- 星图只读工具：`mbdsdr_ai/sky_tools.py:199`（注册）、`:333`（`sky_whats_here`，必须显式给 `az_deg/alt_deg/fov_deg`）、`:440`（`sky_object_lookup`）、`:541`（`sky_asterism_lookup`）；卫星可见列表 `mbdsdr_ai/sdr_tools.py:2353`（`sky_view_visible`）。
- 硬件动作工具：`agent.py:2544/2568/2595`（`sky_tune_satellite/sky_track_satellite/sky_gimbal_follow`），由 `mbdsdr_ai/sky_actions.py:25` 的白名单校验——硬件类 `tune/track/gimbal_follow` 每条都要求非空 `satellite`（`sky_actions.py:33-34`）；另有视图类 `sky_look_at`（`look_at`，不要求卫星，见 §2）。
- **关键缺口（可复现）**：没有任何工具/动作能设置「天空视图中心指向」。底层 UI 能力其实有：
  - `mbdsdr_ai/sky_interaction.py:257` `ViewState.look_at(az, alt)`；
  - `desktop/rf_sky_view.py:3316-3340`（`reset_view` 天顶 90°、`enter_horizon_view`）、`:4396`（双击 goto）。
  但以上都只由鼠标/按钮触发，未暴露成 agent 动作。因此「看向天顶偏南」这类自然语言指令**今天只能被模型解释成查询，不能真正改视图**；要落地，需要在 `sky_actions.py` 增加一个「只改视图、不下发硬件」的动作并接到 `rf_sky_view` 的 UI 线程执行器。

  > 补记（2026-11-17）：此条已按 §2 落地——`sky_actions.SKY_ACTIONS` 加 `look_at`，
  > `apply_look_at()` 负责 UI 线程落点，工具名 `sky_look_at`。上面这段保留为缺口原貌。
- 若将来有本地 OpenAI 兼容服务（如 Ollama 的 `/v1`），**无需改代码**，只改 `~/.mbdsdr/config.json` 的 `base_url/model` 即可；风险是小模型 tool-calling 质量，`model_manager.py:612-647` 已有「从正文兜底解析 tool_call」的降级路径。

---

## 4. 明确不可用 / 不可信

1. **「本地的 DeepSeek」不存在。** 全盘无 DeepSeek 权重；`deepseek-harness` 只是 agent 框架源码，默认连公网 `api.deepseek.com`，且本机未装 `node_modules`/`pnpm`，离线跑不起来。
2. **Hugging Face 只是「包名」，不是本地缓存。** 主环境没有 `huggingface_hub`，`~/.cache/huggingface` 不存在；任何 `from_pretrained` 都会联网（未实测，因为本次不联网）。
3. **两个 ONNX 模型目前都不可执行。** 无 `onnxruntime`、无离线 wheel、无 `onnx`；Rust 侧 `rten` 只在 `sdroxide-deepcw/Cargo.toml:18` 声明，本机没有已构建产物（`sdroxide*.exe` 搜索为空）。
4. **许可证风险（未证实可规避，按高风险处理）：**
   - DeepCW 模型与移植代码：`crates/sdroxide-deepcw/Cargo.toml:5` 与 `src/lib.rs:7-12` 明写 **AGPL-3.0-only**，并提示 §13 网络分发义务；MBDSDR 是 MIT，按 AGPL 的通常理解，链接/合并分发会让组合作品受 AGPL 约束（具体法律判断未证实，按高风险处理）。
   - Piper 声音 `hfc_female`：`MODEL_CARD` 标 **CC BY-NC-SA 4.0**（禁止商用）。
5. **`some-windows-v0.0.1` 不是可用 AI 资源。** 其 `torch 2.1.0+cpu` 是 Python 3.10 应用内私有副本（`_C.cp310-win_amd64.pyd`），不能给主 3.14 用；`fairseq/onnx/lightning/h5py` 缺失，470 MB ckpt 无法加载；任务本身是「歌声转 MIDI」，与 SDR 无关。它的存在也说明「有 torch」不等于「有可用的推理栈」。
6. **端口探测只代表此刻。** 现在 11434/1234 等未监听；是否有计划安装 Ollama/LM Studio，**未证实**。
7. **星表可加载 ≠ 星图全链路可用。** 本次只验证 `get_catalog()` 能离线构造；卫星/过境依赖 TLE，仍可能需联网（未验证）。

---

## 5. 下一步三条（按收益排序）

> **方向纠正（2026-11-17，用户明确）**：不做本地模型部署，不引入 Ollama
> 作为新组件，不把 Hugging Face 运行时/数据集托管当成开发目标。下文提到
> 「本地 Ollama / vLLM / LM Studio」「HF 运行时」的段落只是**环境事实盘点**
> 与兼容性记录（空 Key 放行、清单契约），不是立项依据。用户要求的
> 「参考 DeepSeek」指参考 DeepSeek harness 的**运行时机制**（工具管线、
> 审计接缝、spill、裁剪），不是把模型权重搬到本机。克隆项目的价值在于
> 机制与协议，不在于再起一套服务。

### 1）先做「规则 + 特征」的信号分类增强（零下载、可回滚，收益最高）

- 位置：`mbdsdr_ai/analysis/modulation_classifier.py:175-193`，用 `151-168` 的现成特征做轻量判别（如质心/最近邻 + 置信度），或增强 `channel_analyzer.py:53` 的阈值树。
- 验证：用 `recordings/` 与 `tests/fixtures` 里已有样本跑前后对比；不引入新依赖。
- 为什么第一：这是唯一「当前环境立刻能做、且直接改善 SDR 主流程」的一条。

**第一步落地记录（2026-11-17）：先堵「把噪声当信号」的假阳性**

实测发现的假分类（未改代码前）：

| 输入 | 旧输出 | 实际 |
|---|---|---|
| 复高斯噪声，幅度 0.02/0.05 | `OOK 0.85`（`zero_frac≈0.23`） | 纯噪声 |
| 复高斯噪声，幅度 0.2 | `AM 0.75` | 纯噪声 |
| `modulation_classify()` 吃全零样本 | `cw 0.95`（`amp_std=0` 落入恒包络分支） | 静默/未连接 |
| `modulation_classify()` 吃白噪声 | `8psk 0.85`（`μ4≈0`） | 纯噪声 |

改动（两处规则，各带一个可量化的分界，不引入任何依赖）：

1. `modulation_classifier.py::_decide()` 最前面加**谱平坦度门**：
   `spectral_flatness > 0.6 → NOISE 0.7`。实测五类合成信号平坦度 ≤0.30，
   复高斯噪声 ≈0.85；`_suggest()` 同时给噪声档两条可操作建议。
2. `blind_param_est.py::_score_class()` 最前面加**占用带宽门**：
   `bw_fraction > 0.85 → noise 0.9`。实测白噪声 ≈1.00、全零样本 1.00，
   而 CW/AM/FM/2FSK/BPSK/QPSK/8PSK/16QAM 全部 ≤0.20
   （最高是 FM 0.168）。

验收：`tests/test_modulation_classifier.py` 47 passed（含新增
`test_elevated_noise_is_not_reported_as_modulation` 3 档 +
`test_noise_structure_gate_does_not_swallow_real_signals`）；
`mbdsdr_ai/tests/test_blind_param_est.py` 23 passed（含新增
`test_classify_pure_noise_is_noise`、`test_classify_silence_is_noise_not_cw`）。

为什么先做这一条：分类器报 `OOK 0.85` 比报 `UNKNOWN` 更糟——它会驱动
「应用」按钮切到错误的解调链，属于把噪声当信号。置信度也只给 0.7，
不冒充「我确定这是噪声」。

**同源第二处（2026-11-17 续）：`channel_analyzer.classify_signal` 的同类假阳性**

§3.1 里那个「规则阈值树」也有同一个病：只要段越过分段阈值（中位底 +6 dB）
就给类型，纯噪声单帧被标「FM/模拟」。实测把门定在 **14 dB**：

- 复高斯噪声 1200 个单帧（fft 512/1024/2048）共 22,340 个越限凸起，峰值
  抬升最大 **13.21 dB**，全部只能是「噪声/未定」；
- 真实 OTA 样本 `tests/fixtures/real_fm_88m1`（511 帧平均）：真广播 18.7 dB
  过门，三个残余凸起 6.2/7.3/6.5 dB 不给类型（旧实现全标「FM/模拟」）；
- 顺带修掉退化：谱平坦度改到**线性功率域**（旧 dB 域恒 ≈1.0，形状判据
  退化成「带宽 < 20 kHz 即 FM」）；每条检测结果新增 `snr_db` /
  `spectral_flatness`，判据可复核。
- 验收：`tests/test_channel_analyzer_noise_gate.py` + `tests/test_channel_analyzer.py`
  → 15 passed。细节见 `docs/COORDINATION.md` §22。

**同源第三处（面板版）：`new_spacetime_amr` 粗分辨率纯噪声 720/720 假检测**

`_detect_and_features` 的「宽度判据」用 `ceil(100 Hz / bin_hz)` 当 bin 下限：
fs 2.4 MHz / n 2048 时 bin_hz=1172 Hz → 下限退化成 **1 bin**，任何噪声毛刺
都算宽信号。实测单帧噪声连续段最长 18 bin、峰值抬升最大 18.7 dB，于是把
bin 地板钉在 19、峰值门钉在噪声上界之上；同时修掉「标签说未检测到、标志
说已检测」的自相矛盾（面板会把噪声段的带宽/峰数当测量值展示），并给
USB/LSB 加 6 kHz 带宽上限（真实 OTA FM 186 kHz 曾被判成 USB）。

改前/改后：噪声假检测 720/720 → 0/420（频谱路径）、30/30 → 0/30（IQ 路径）；
真实 FM 仍检出（34.1 dB / 186 kHz）且不再判 SSB。
验收：`tests/test_new_spacetime_amr.py` 等 5 个文件 → 50 passed。
已知边界：该分类器对真实宽带 FM 给 `OFDM/数字`（置信 0.40）——需要真实
多类样本重训，不用规则硬掰。细节见 `docs/COORDINATION.md` §23。

### 2）补齐星图「视图指向」闭环（让自然语言指令真正可执行）——**已按本条落地（2026-11-17）**

- 位置：`mbdsdr_ai/sky_actions.py:25` 增加只改视图的动作；执行器接 `desktop/rf_sky_view.py:3316-3340` 的 `view_state.look_at()`（UI 线程，和 `sky_interaction.py:257` 同一原语）。
- 实做：`SKY_ACTIONS` 加 `look_at`；`validate_sky_action` 对 `look_at` 只查 `az_deg/alt_deg`（`alt` 限 −90..90）、可选 `fov_deg` 限 (0,180]，**不要求 satellite**；`apply_look_at(sky_view, action)` 是 UI 线程执行体，视图未打开返回 `UNAVAILABLE`；agent 侧注册 `sky_look_at`；`MainWindow._execute_sky_action` 在卫星一致性门**之前**分流。
- 验收：`tests/test_sky_capability_negotiation.py::TestLookAtAction`（6 条纯逻辑）+ `tests/test_sky_offscreen_overlay.py::test_look_at_action_really_moves_the_view`（真 `RFSkyView`，总线排水后 `view_state` 真的到 90°/10°/30°）。
- 收益：不依赖本地大模型，现有云端/未来本地 LLM 都能把「看向天顶偏南」变成真动作；与 `sky_whats_here`（`sky_tools.py:333`）形成「先看再问」的闭环。
- 注意：这是写 UI 状态，不是写硬件，不应套 `tx_guard`；但仍需保持「无执行器诚实 UNAVAILABLE」的既有语义（`sky_actions.py:16`）。

### 3）语音转写 / 本地 LLM 暂缓（等有合法模型与运行时再谈）

- 现状：无 ASR 依赖/权重、无本地 LLM、ONNX 无运行时；唯一现成的语音权重还是 CC BY-NC-SA 的 TTS 与 AGPL 的 CW 模型。
- 若启动，最小改动路径：`config.json` 的 `base_url` 指向本地 OpenAI 兼容端点；ASR 则在 `controller.py:675-681` 音频泵后挂 tap。两者都需要先解决「从哪下载、什么许可证、CPU 实时性是否够」——目前都**未证实**。
- **补记（2026-11-17）**：原文说「代码已支持」只对了一半。实测发现空 API Key
  会同时卡住 `fetch_models`、`MBDSDRAgent.chat` 与 AI 面板保存（本地 Ollama /
  vLLM / LM Studio 默认无鉴权，于是「体检可用 → 一发消息就报 Key 未设置」）。
  已新增 `model_manager.is_local_endpoint` 并在四处放行「空 Key + 本机/内网
  端点」，云端端点行为不变、离线门优先；证据
  `tests/test_local_endpoint_keyless.py` 26 passed（127.0.0.1 桩服务跑通
  `/v1/models`、非流式与 SSE 流式对话）。**仍无权重、无本地推理服务**，
  真实模型效果未验证，见 `docs/COORDINATION.md` §34。

### 4）HF 方向：清单体检/切分已可达（2026-11-17）

- 新增 `mbdsdr_ai/iq_tools.py`，把 `iq_sample_manifest` 的契约校验、
  结构体检与按 `iq_path` 分组的切分方案注册成只读工具
  （`iq_manifest_audit` / `iq_split_plan`），模型可直接回答「这批样本
  能不能开训、怎么切才不泄漏」。
- 边界不变：不读样本内容、不写文件、不训练；`structural_ready` 不代表
  样本足够或质量合格；本机仍无 HF 运行时与真实多类样本。
- 证据：`tests/test_iq_tools.py` 13 passed；见 `docs/COORDINATION.md` §37。

### 5）克隆项目线：清单 → SigMF 元数据已打通（2026-11-17）

- 读 `D:\mbdsdr\reference\sigmf\sigmf-schema.json`（v1.2.6）确认三件事：
  顶层必须有 `global`/`captures`/`annotations`；`global` 必须有
  `core:datatype` + `core:version`；每个 capture/annotation 必须有
  `core:sample_start`。其中 **`core:datatype` 只能由录制方声明**，猜不出来。
- 新增 `mbdsdr_ai/iq_sigmf.py` + `scripts/export_sigmf.py`：清单里没写
  `datatype` 的样本不导出；只从清单取采样率/频率/license；数据文件不是
  `<stem>.sigmf-data` 时用 `core:dataset` 如实标成 Non-Conforming Dataset，
  不改名不复制。
- 两个容易踩的坑（都已处理）：
  1. `core:license` 要求是 license 文档 URI，`CC-BY-4.0` 这种短标识塞进去
     就不合规——短标识改记 `mbdsdr:license` 并在报告里说明；
  2. PowerShell 5.1 / 记事本存 UTF-8 会带 BOM，普通 `utf-8` 读会让第一行
     `json.loads` 报 "Unexpected UTF-8 BOM"，把好清单误判成坏清单；
     清单读取改用 `utf-8-sig`。
- 真实数据实测结论：仓库唯一的真实空口样本 `real_fm_88m1` 没有声明
  license，按契约**被拒**——这不是 bug，是「来源不清不进数据集」在起作用。
  见 `D:\mbdsdr\output\sigmf\export_report_real_fm.json` 与
  `docs/COORDINATION.md` §41。
- 反向也打通了（`read_meta` + `scripts/import_sigmf.py`）：读别人的
  `.sigmf-meta` → 清单条目，兼容早期把频率写在 `global` 的写法，并用
  「文件字节数 ÷ 单样本字节数」检查录制是否被截断。类别必须由使用者用
  `--label` 声明——annotation 通常是时间段描述，不能当分类标签。
  见 `docs/COORDINATION.md` §43。


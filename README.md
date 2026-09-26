# MBDSDR — AI 定义的软件定义无线电接收机

原生 PySide6 桌面端 · 多模数字/模拟解调 · GPL-3.0

---

## 1. 项目定位

MBDSDR 是一个**用纯 Python（NumPy / SciPy）实现基带处理、用 PySide6 构建桌面 GUI** 的软件定义无线电（SDR）接收机框架。它的目标不是复刻 GNU Radio 的全部图形流图能力，而是在一个可直接双击运行的桌面应用里，把常见的模拟语音、数字语音、短数据业务、气象卫星图像和弱信号数字模式收下来、解出来，并在其上叠加一个**可对话的 AI 辅助层**（自动识别信号模式、建议解调参数、汇总解码日志）。

几个需要诚实说明的边界：

- **不依赖 GNU Radio**。基带解调全部用 NumPy / SciPy 手写；`gnuradio_blocks.py` 仅作为可选的块级兼容参考，不是运行时依赖。
- **"AI" 指的是一个本地编排层**（工具注册 + LLM 函数调用循环），用来辅助选模、调参和日志解读；信号本身的解调仍是经典 DSP，不是神经网络盲解。
- **当前版本以合成信号自证（roundtrip）为主**：大部分解码器通过"合成编码 → 加噪信道 → 解码还原"的软件闭环验证算法正确性，**真实天线接收的端到端验证仍在进行中**（见第 6 节）。
- 支持的硬件后端以 RTL-SDR 为第一公民，HackRF / BladeRF / LimeSDR / PlutoSDR / USRP 通过 SoapySDR 参数层适配，另支持 `.wav/.cf32/.iq` 录制文件离线回放。

---

## 2. 已实现的接收 / 解码清单

下表遍历 `mbdsdr_ai/` 下的解码相关模块，按业务类别分组。

列说明：

- **模块文件**：`mbdsdr_ai/` 下的 Python 文件。
- **合成自证**：`tests/` 下是否存在对应的 roundtrip 测试（合成信号 → 解码 → 比对）。
- **真机验证**：截至本版本，**全部标注为"待完成"**——即便个别文件注释里提到"真机"字样，也只是参数/极性提示，并未在代码库中沉淀端到端实测记录。

### 2.1 模拟语音

| 业务 | 模块文件 | 合成自证测试 | 真机验证 |
|---|---|---|---|
| FM 宽带 / AM / SSB 通用解调 | `analog_demod.py`, `demod.py` | `tests/ham_modes_roundtrip.py`（部分） | 待完成 |
| FM 立体声解调（MPX → L/R） | `wfm_stereo_lite.py` | 经 `ham_modes_roundtrip.py` / `protocol_spectrum_roundtrip.py` 间接覆盖 | 待完成 |
| CW（摩尔斯电报）解调 | `cw_decoder.py` | `tests/ham_modes_roundtrip.py` | 待完成 |

### 2.2 数字语音

| 业务 | 模块文件 | 合成自证测试 | 真机验证 |
|---|---|---|---|
| DMR / dPMR 等 4FSK 数字语音（基带级，仅数据层，语音编解码不在内——专利） | `dmr_demod.py` | `tests/test_dmr_demod_roundtrip.py` | 待完成 |
| DSD 系列软解码轻量参考（DMR/YSF/P25 比特流层，仅数据层） | `dsdcc_lite.py` | `tests/dsdcc_test.py`, `tests/digital_voice_roundtrip.py` | 待完成 |
| Codec2 声码器轻量解 | `codec2_lite.py` | `tests/codec2_freedv_roundtrip.py`（部分） | 待完成 |
| FreeDV 数字语音 | `freedv_modem.py` | `tests/codec2_freedv_roundtrip.py` | 待完成 |
| DAB+ 广播 | `dab_plus_lite.py` | `tests/dab_plus_test.py` | 待完成 |
| HD Radio（NRSC-5） | `nrsc5_lite.py` | `tests/nrsc5_roundtrip.py` | 待完成 |
| M17 数字语音（外部工具适配器） | `m17_adapter.py` | 无 | 待完成 |
| **YSF（Yaesu System Fusion）C4FM 解调器** | `ysf_demod.py` | `tests/test_ysf_demod_roundtrip.py` | 待完成（数据层，不含语音——专利） |

### 2.3 数据业务与短报文

| 业务 | 模块文件 | 合成自证测试 | 真机验证 |
|---|---|---|---|
| ADS-B 1090ES | `adsb.py`, `adsb_lite.py`, `adsb_map.py` | `tests/adsb_real_roundtrip.py`（合成闭环） | 待完成 |
| APRS（AX.25 / 1200 波特 AFSK） | `ax25.py`, `aprs_parser.py` | `tests/ax25_aprs_roundtrip.py` | 待完成 |
| POCSAG 寻呼 | `pocsag_decoder.py` | `tests/test_pocsag_roundtrip.py` | 待完成 |
| ACARS（航空 VHF 数据链） | `acars_decoder.py`, `acars_protocol.py` | `tests/test_acars_roundtrip.py` | 待完成 |
| VOR 导航接收机 | `vor_decoder.py` | `tests/test_vor_roundtrip.py` | 待完成 |
| FM RDS 广播数据 | `rds_lite.py` | `tests/rds_real_roundtrip.py`（合成闭环） | 待完成 |
| ISM 433/315 MHz 通用遥控 / 传感器数据 | `rtl433_decoder.py`（外部 `rtl_433` 适配层） | `tests/rtl433_roundtrip.py` | 待完成 |
| GNSS RTCM3 差分电文 / RTK 解算适配 | `rtcm3_decoder.py`, `rtk_solver.py`, `gnss_monitor.py` | `tests/test_rtcm3.py`, `tests/rtklib_test.py` | 待完成 |
| multimon-ng 系（POCSAG / FLEX / EAS 等外部适配） | `multimon_decoders.py` | `tests/multimon_roundtrip.py` | 待完成 |
| FLDigi 多模式（RTTY / PSK / Olivia 等） | `fldigi_modes.py` | `tests/fldigi_roundtrip.py` | 待完成 |
| MFSK / 窄带数据外部适配 | `minimodem_adapter.py`, `js8call_adapter.py`, `ardop_adapter.py` | 无 | 待完成 |

### 2.4 气象 / 对地观测卫星图像

| 业务 | 模块文件 | 合成自证测试 | 真机验证 |
|---|---|---|---|
| NOAA APT（137 MHz 自动图像传输） | `noaa_apt_lite.py` | `tests/apt_meteor_roundtrip.py`（部分） | 待完成 |
| Meteor-M2 LRPT | `meteor_sat.py` | `tests/apt_meteor_roundtrip.py` | 待完成 |
| 风云系列（FY-3 等） | `fengyun_sat.py` | `tests/test_fengyun.py`, `tests/test_fengyun_dvbs2.py` | 待完成 |
| GOES LRIT/HRIT | `goes_lrit.py` | `tests/goes_lrit_test.py` | 待完成 |
| GK-2A LRIT | `gk2a_lrit.py` | `tests/test_gk2a_lrit.py`, `tests/test_gk2a_jp2.py` | 待完成 |
| 卫星图像后处理 / 辐射校正 | `sat_image_processing.py` | `tests/test_sat_image_processing.py` | 待完成 |

### 2.5 弱信号数字模式

| 业务 | 模块文件 | 合成自证测试 | 真机验证 |
|---|---|---|---|
| FT8（含 Costas 同步 / LDPC / 呼号解包） | `ft8_encode.py`, `ft8_decode.py`, `ft8_lite.py`, `ft8_costas.py`, `ft8_ldpc.py`, `ft8_callsign.py`, `ft8_unpack.py` | `tests/ft8_roundtrip.py`, `tests/ft8_real_roundtrip.py`（合成闭环） | 待完成 |
| FST4（LDPC 编解码） | `fst4_encode.py`, `fst4_ldpc.py` | 无（复用 FT8 链路测试） | 待完成 |
| FT4 / JT9 / JT65 / QRA64 等模式注册 | `digital_modes.py`（模式清单与参数表） | `tests/test_ft8_modes_benchmark.py` | 待完成 |
| WSPR（外部 `wsprd` / WSJT-X 适配） | `decoders.py` 中 `wspr` 适配项 | 无 | 待完成 |

### 2.6 SSTV 慢扫描电视

| 业务 | 模块文件 | 合成自证测试 | 真机验证 |
|---|---|---|---|
| SSTV（Robot36 / Martin / Scottie 等） | `sstv_decoder.py` | 无独立 roundtrip 测试 | 待完成 |

### 2.7 轨道与天线指向（支撑模块）

| 功能 | 模块文件 | 测试 |
|---|---|---|
| SGP4 轨道 / 过境预报 | `sat_tracker.py`, `orbit.py`, `sat_passes.py`, `new_spacetime*.py` | `tests/test_sat_tracker.py`, `tests/test_orbit_determination.py`, `tests/test_new_spacetime_tle.py` |
| 天体几何 / 多普勒 | `celestial_geometry.py`, `astronomy.py`, `atmosphere.py` | `tests/test_solar_system_ephemeris.py` |
| Gpredict 外部适配 | `gpredict_adapter.py` | `tests/gpredict_test.py` |
| 卫星流水线编排 | `sat_pipeline_runner.py`, `sat_pipeline_params.py` | `tests/test_sat_pipeline.py`, `tests/sat_groundstation_roundtrip.py` |

> **统计**：上表共列出约 40 个解码/解算相关模块文件（不含纯适配层和轨道支撑模块）；其中约 20 个有对应的 roundtrip 合成自证测试。所有模块的**真机接收验证均标注为"待完成"**。

---

## 3. 架构分层

```
┌──────────────────────────────────────────────────────────────┐
│  UI 层  desktop/                                             │
│  main.py / main_window.py / spectrum_widget.py / 各 *panel  │
│  (PySide6 窗口、频谱瀑布、音量、模式选择、AI 对话面板)         │
└───────────────┬──────────────────────────────┬──────────────┘
                │ 信号流                       │ 对话/工具调用
                ▼                              ▼
┌──────────────────────────┐      ┌────────────────────────────┐
│  解码层  mbdsdr_ai/       │      │  AI 编排层                  │
│  analog_demod / dmr_demod │      │  agent.py   (LLM 主循环)    │
│  ft8_* / adsb / noaa_apt  │◀────▶│  orchestrator.py (任务图)   │
│  pocsag / acars / sstv…   │ 工具 │  tool_registry.py           │
│  decoder_registry.py 注册  │ 结果 │  model_manager.py / memory  │
└──────────────┬───────────┘      └────────────────────────────┘
               │ IQ 复数流 / 解调后音频 / 解码帧
               ▼
┌──────────────────────────────────────────────────────────────┐
│  硬件抽象层  sdr_backend.py / hal.py / rtlsdr_params.py       │
│  hackrf_params.py / bladerf_params.py / limesuite_params.py  │
│  osmosdr_source.py / cfo.py / dsp_stream.py / baseband_io.py  │
└───────────────┬──────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────────────────┐
│  数据源                                                       │
│  RTL-SDR (pyrtlsdr) │ HackRF / BladeRF / LimeSDR / PlutoSDR  │
│  (SoapySDR 参数层)  │ 自研 ai-sdr Mini (WebSocket/MCP)        │
│  离线文件回放 (.wav / .cf32 / .iq)                            │
└──────────────────────────────────────────────────────────────┘
```

要点说明：

- **自底向上**：硬件层交付统一的复数 IQ 流（可带中心频率、采样率、CFO 估计）；解码层按"模式"注册到 `decoder_registry.py`，每个解码器输入 IQ 帧、输出解码后的文本 / 图像 / 音频；UI 层订阅这些输出并渲染频谱与日志。
- **AI 层如何介入**：`agent.py` 是一个标准的 LLM 工具调用循环——它把"列出可用模式""调节中心频率""启动某个解码器""读取最近 N 条解码日志""查过境预报"都注册成工具；用户在 AI 面板用自然语言描述需求时，Agent 决定调用哪些工具、按什么顺序（由 `orchestrator.py` 的任务图编排），并把结果汇总回对话。AI 层**不直接改写 IQ 数据流**，它只通过工具间接控制接收机。
- **无硬件原则**：`sdr_backend.py` 在未连接真实设备时 `active_backend` 为 `None`，**不会**偷偷实例化一个模拟后端并假装在收信号；唯一合法的离线数据源是用户显式选择的录制文件（FileIQBackend）。

---

## 4. 怎么跑

### 4.1 Windows 一键启动

- 首次安装 / 想看到安装日志：双击 **`scripts/run_windows.bat`**。它会自动找 Python 3.10+、创建 `.venv`、安装 `requirements.txt` 和 `desktop/requirements.txt`、尝试可选安装 SoapySDR，最后启动 `desktop\main.py`。
- 日常快速启动（无黑框）：双击 **`MBDSDR.vbs`**。它隐藏控制台调用 `scripts/launch_mbdsdr.bat`，由后者用 `pythonw` 启动桌面端。

### 4.2 源码运行（任意平台）

```bash
# 1. 建议虚拟环境
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. 安装依赖
python -m pip install -r requirements.txt
python -m pip install -r desktop/requirements.txt

# 3. 启动桌面端
python desktop/main.py
```

> 需要 Python 3.10+。`pyrtlsdr` 固定 0.2.93（与 Windows 官方 osmocom DLL 兼容；新版需要 rtlsdr-blog 分支的 `rtlsdr_set_dithering`，import 即崩）。
> HackRF / BladeRF / LimeSDR / PlutoSDR 等设备除 pip 包外，还需在系统层安装对应 SoapySDR 驱动；装不上时 RTL-SDR 仍可独立工作。

### 4.3 关于"无硬件时的行为"

未连接任何 SDR 设备时，接收面板显示"未连接 / 无数据"，频谱与解码日志保持空白——**这是正确行为，不是缺陷**。本项目不内置合成信号演示流，也不会在无硬件时伪造 IQ 数据。

合法的离线使用方式只有两种：

- **录制文件回放**：用户主动选择本地 IQ 文件（`.wav` / `.cf32` / `.iq`），走 FileIQBackend 链路回放已录下的真实空中信号。
- **算法自证测试**：`python -m pytest tests/ -k roundtrip` 跑合成编码 → 解码闭环，用于在开发环境验证算法内部一致性；这类测试不通过 GUI 展示。

---

## 5. 开源协议与致谢

本项目以 **GPL-3.0** 发布，完整协议文本见根目录 `LICENSE`。分发、修改和再分发须遵守 GPL-3.0 条款。

MBDSDR 在协议格式、解调思路和工程实践上参考了下列开源项目（按"代码中实际 import、封装适配或直接参考协议文档"筛选）：

| 项目 | 用途 |
|---|---|
| [librtlsdr / pyrtlsdr](https://github.com/rtlsdrblog/rtl-sdr-blog) | RTL-SDR 硬件 IQ 采样 |
| [SoapySDR](https://github.com/pothosware/SoapySDR) | HackRF / BladeRF / LimeSDR / PlutoSDR / USRP 统一参数层 |
| [PySide6](https://doc.qt.io/qtforpython/) | 桌面 GUI |
| [NumPy](https://numpy.org) / [SciPy](https://scipy.org) | 基带 DSP、滤波、重采样、频谱 |
| [MMDVMHost](https://github.com/g4klx/MMDVMHost) | DMR / YSF 时隙与帧结构参考 |
| [DSDcc](https://github.com/f4exb/dsdcc) | 数字语音比特流软解码思路参考 |
| [op25](https://github.com/boatbod/op25) | 4FSK / C4FM 解调与帧同步参考 |
| [dump1090](https://github.com/antirez/dump1090) | ADS-B 1090ES 前导检测与曼彻斯特解码参考 |
| [direwolf](https://github.com/wb2osz/direwolf) | AX.25 / AFSK 1200 APRS 解调参考 |
| [multimon-ng](https://github.com/EliasOenal/multimon-ng) | POCSAG / FLEX / EAS 等寻呼与广播数据 |
| [redsea](https://github.com/windytan/redsea) | FM RDS 组同步与块解码参考 |
| [nrsc5](https://github.com/theori-io/nrsc5) | HD Radio / NRSC-5 物理层参考 |
| [goestools](https://github.com/pietern/goestools) | GOES LRIT/HRIT 图像格式参考 |
| [satdump](https://github.com/SatDump/SatDump) | 气象卫星 LRPT/LRIT 解码流水线参考 |
| [gpredict](https://github.com/csete/gpredict) | 卫星过境与多普勒外部协调适配 |
| [RTKLIB](https://github.com/rtklibexplorer/RTKLIB) | GNSS 差分 / RTK 解算参考 |
| [WSJT-X / wsprd](https://www.physics.princeton.edu/pulsar/k1jt/wsjtx.html) | FT8 / WSPR 弱信号模式外部适配 |

上述项目的版权归各自作者所有；本项目对它们的引用属于协议层面的互操作与参考，未复制其源码进入本仓库（除明确标注的轻量重写实现外）。

---

## 6. 专利与合规

数字对讲机语音链路涉及受专利保护的声码器，本项目在这一问题上采取明确立场：

- **DMR（Tier 1/2/3）语音编解码使用 AMBE+2**，该声码器由摩托罗拉 / DVSI 等持有专利。本软件**不包含、不链接、不重实现** AMBE+2 编解码器；`dmr_demod.py` 与 `dsdcc_lite.py` 仅实现**数据层**——4FSK 解调、前导与同步、时隙划分、色码（CC）、链路控制 LC、呼叫标识（Talkgroup）与源/目的呼号解包，输出的是"谁、在哪个时隙、用哪个色码、呼叫哪个通话组"这类元信息，**不是语音**。如需听到 DMR 话音，用户需自备合法授权的声码器硬件（如 DVSI AMBE-3000 芯片板）或已获得相应授权的软件声码器。
- **YSF / C4FM 语音使用 AMBE / IMBE 声码器**，同样受专利保护。本软件的 YSF 解调器（`ysf_demod.py`）同样**仅实现数据层**——C4FM 解调、帧同步、FICH 控制信道、下行/上行呼号、DSQ（Data Sequence）等数据字段解析，**不包含语音编解码**。
- **本项目不做、也不会做专利声码器的逆向工程或重实现**。任何以"免费解出 DMR/YSF 语音"为目的的二次开发都应自行解决专利授权问题，与本仓库无关。
- 模拟语音（FM/AM/SSB）、CW、以及开放协议数据业务（ADS-B、APRS、POCSAG、ACARS、RDS、SSTV、FT8/WSPR 等）不涉及上述专利声码器限制。

---

## 7. 诚实声明与已知限制

**本版本的定位是"算法自证阶段"，不是一个经过严格外场标定的接收机，也不是一个能播出数字对讲机话音的终端。**

### 7.1 我们明确没做的事

- **专利声码器**：DMR 的 AMBE+2、YSF/C4FM 的 AMBE/IMBE，本仓库内**没有任何实现**，也不计划实现；DMR/YSF 路线只到数据层（见第 6 节）。
- **真机端到端验证**：全部解码器"接真实天线 → 解真实空中信号"这一步**均未完成**，第 2 节"真机验证"列全部为"待完成"。
- **实时数字语音播放**：因为不含声码器，DMR/YSF/M17 等模式在本软件里**不会播出话音**；这是合规选择，不是功能 bug。模拟语音（FM/AM/SSB）与录制文件回放不受此限。
- **不伪造信号**：无硬件时面板显示"未连接/无数据"，不播放合成流（见 4.3）。

### 7.2 以合成信号自证为主

第 2 节中标注"合成自证"的测试，验证方式是"本项目自己编码 → 加噪/衰落 → 本项目解码"。这种闭环能证明算法内部一致，但**不能替代真实空中信号的端到端验证**——真实信道有多径、邻道干扰、AGC 非线性和发射机非理想，这些在合成测试里都没有完全建模。仓库根目录下保留了若干人工检视用的样本图（如 SSTV、卫星 APT），仅供开发参考，不构成验收依据。

### 7.3 已知限制（非穷举）

1. DMR / YSF / P25 等数字链路目前是**基带级、仅数据层**的实验实现（前导/帧同步/比特解出），容错率和丢包处理与 MMDVMHost、DSDcc 等成熟实现相比尚未对齐；语音链路因专利原因不做。
2. ADS-B 解码器为基带级轻量实现，在弱信号、多径和带外镜像下的鲁棒性低于 dump1090。
3. 气象卫星图像（NOAA/Meteor/GOES/GK-2A）在**天线指向精度、多普勒残留校正、信道衰落**方面强依赖外部 TLE 与硬件前端，纯软件解码在低仰角过境时可能出现条带或丢帧。
4. GNSS / RTK 部分以电文解析和参数适配为主，整周模糊度固定等核心解算仍依赖 RTKLIB 外部能力。
5. WSPR、M17、JS8Call、ARDOP、minimodem 等模式当前是**外部工具适配器**，需要系统里装好对应可执行程序才能工作，并非纯 Python 实现。
6. AI 编排层依赖外部 LLM API；离线环境下会降级为关键词规则，能力受限。
7. 硬件后端的优先级是 RTL-SDR → SoapySDR 通用层；HackRF / BladeRF / LimeSDR 的参数（增益表、带宽、偏移）尚未逐机标定。

如果您在研究、教学或野外测试中使用本项目并发现问题，欢迎提 Issue 附上 IQ 录制样本（脱敏后），这是当前阶段最有价值的反馈。

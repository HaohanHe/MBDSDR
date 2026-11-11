# 解码器 × UI 能力矩阵（只读缺口调研）

- 日期：2026-11-11
- 范围：`mbdsdr_ai/decode_router.py`、各解码器后端、`desktop` 侧解码面板与 `satellite_image_panel` / `weather_panel`
- 方式：只读静态核查（grep/read），未改任何产品代码，未运行真机
- 口径：
  - 「后端能解」= 仓内存在可调用的解码实现；
  - 「用户能用」= 普通用户不写代码即可在桌面 UI 里完成入口→参数→看结果；
  - 矩阵符号：● 有 / ◐ 部分 / ○ 无；无依据写「无」
  - 严重度：P0 假成功或核心入口缺失且被误报可用；P1 主功能不可用/误导/结果留不住；P2 体验、可发现性、可读性
  - 成本为工程量级估计（低 ≤1d，中 1–5d，高 >5d），非排期承诺

## 0. 结论摘要

1. **后端能力远大于 UI 能力**：`decode_router` 可解 10 类数字模式，但桌面只有 ADS-B、APRS 两个独立数字解码页签，卫星类经卫星云图/气象云图面板呈现；FT8/FT4/WSPR/ACARS/POCSAG/AIS/NAVTEX 只有 CLI，其中 FT8 连 CLI 通用入口都返回 `not_implemented`。
2. **最危险的不是「没有」，而是「假成功」**：MCP/控制器的 `start_decoder` 接受 10 类解码器，底层只实现了 ADS-B 与 RDS，其余 8 类线程空转、永远返回 0 条，还会附一句「可能仍在等待信号」。
3. **WAV 路径的 0 帧被当成成功**：`signal_present_no_frame` 只在 `.cf32/.cfile` 判定；ACARS 缺 `SYN,SYN,SOH` 前导、POCSAG/NAVTEX/FT4/WSPR 解 0 帧时统一 `ok=True`，用户拿不到失败结论。
4. **同一能力存在两套口径**：ADS-B 地图面板校验 1090±1.5MHz，右栏列表面板不校验；APRS 不校验 NFM/144.390。两处异常都被静默吞掉。
5. **两条「真」链没接到用户**：`docs/learn/decoder-capability-matrix.md` 声明 LRPT 真录链为「真实」，但卫星云图面板走的是 `meteor_sat` 合成级链；`lrpt_pipeline` 只有测试和 CADU 级工具入口。FT8 同理：外部真值已对拍，桌面无入口。

## 1. 解码器 × UI 能力矩阵

> 列说明：入口(UI) = 桌面可见入口；配置 = 面板内可调参数；结果视图 = 表格/地图/图像/终端；空态区分 = 「无信号」与「等待」是否分开；失败区分 = 异常/错误码是否呈现；导出 = 用户可另存；历史 = 关窗后仍可回看。

| 解码器 | 入口(UI) | 配置 | 结果视图 | 空态区分 | 失败区分 | 导出 | 历史 |
|---|---|---|---|---|---|---|---|
| ADS-B（右栏列表） | ◐ 右栏页签 `main_window.py:10019-10025`；仅 dock 可见时喂数 `main_window.py:8735-8740` | ○ 面板内无；不校验中心频率 `adsb_panel.py:296-305` | ● 表格 `adsb_panel.py:265` | ● 「等待 1090MHz 数据」/「未连接」`adsb_panel.py:64,335-344` | ○ `except` 直接吞 `adsb_panel.py:303-305` | ○ 无 | ○ 内存 TTL 30s `adsb_panel.py:75-76,324` |
| ADS-B（中央地图） | ● 中央面板 `main_window.py:1897`；注册表 `panel_registry.py:92` | ◐ 自动门控 1090±1.5MHz `adsb_map_panel.py:226-240` | ● 地图+轨迹 `adsb_map_panel.py:80,163` | ● `adsb_map_panel.py:80` | ● 诊断文本 `adsb_map_panel.py:205-217` | ○ 无 | ◐ 轨迹仅内存，无落盘 |
| APRS | ◐ 右栏页签 `main_window.py:10029-10035`；可见才喂 `main_window.py:9303-9309` | ○ 不校验 NFM/144.390 `aprs_panel.py:152-160` | ● 表格 `aprs_panel.py:112` | ● 「等待 144.390MHz 数据包」`aprs_panel.py:52,259-262` | ○ 异常吞掉 `aprs_panel.py:152-160`；FCS 错静默丢 `aprs_panel.py:177-178` | ○ 无 | ○ 内存上限 500 `aprs_panel.py:54,191` |
| ACARS | ○ 无面板；CLI `cli.py:222-243`；AI 工具 `agent.py:1412` | ◐ CLI `--mode/--sample-rate` | ◐ 终端 JSON；状态卡仅 MCP 结果 `status_panel.py:213-221` | ○ WAV 0 帧 `ok=True` `decode_router.py:1031-1045` | ◐ 有码无 UI `decode_router.py:952-956` | ○ 无 | ○ 无 |
| POCSAG | ○ 无面板；CLI `cli.py:235-243` | ◐ CLI `--mode/--baud` | ◐ 终端 JSON | ○ WAV 0 帧 `ok=True` `decode_router.py:1031-1045` | ◐ 兜底 `decode_error` `decode_router.py:1009-1013` | ○ 无 | ○ 无 |
| AIS | ○ 无面板；CLI `cli.py:235-243` | ◐ CLI `--mode ais`，仅 cf32 | ◐ 终端 JSON | ◐ cf32 有 `signal_present_no_frame` `decode_router.py:1031`，无 UI 展示 | ◐ 格式错码 `decode_router.py:603` | ○ 无 | ○ 无 |
| NAVTEX | ○ 无面板；CLI `cli.py:235-243` | ◐ CLI `--mode navtex` | ◐ 终端 JSON | ○ WAV 0 帧 `ok=True` `decode_router.py:1031-1045` | ◐ 格式错码 `decode_router.py:636` | ○ 无 | ○ 无 |
| FT8 | ○ 无面板；CLI 通用入口 `not_implemented` `decode_router.py:432-444` `cli.py:235-243`；仅 AI 工具 `agent.py:738` | ○ AI 需传原始 samples | ◐ AI 返回 JSON | ○ 无 UI | ◐ AI 工具错误 | ○ 无 | ○ 无 |
| FT4 | ○ 无面板；CLI `cli.py:235-243` | ◐ CLI `--mode ft4/--sample-rate` | ◐ 终端 JSON | ○ WAV 0 帧 `ok=True` `decode_router.py:1031-1045` | ◐ 有错误码 | ○ 无 | ○ 无 |
| WSPR | ○ 无面板；CLI `cli.py:235-243` | ◐ CLI `--mode wspr/--sample-rate` | ◐ 终端 JSON | ○ WAV 0 帧 `ok=True` `decode_router.py:1031-1045` | ◐ 有错误码 | ○ 无 | ○ 无 |
| NOAA APT | ● 卫星云图面板 `satellite_image_panel.py:209-223`；气象面板 family=noaa `weather_panel.py:85-88` | ● 卫星下拉+采样率 `satellite_image_panel.py:187` | ● 图像+统计 `satellite_image_panel.py:393-412` | ● 「无 APT 信号」`satellite_image_panel.py:383-389` | ● 「解码失败」`satellite_image_panel.py:380-382` | ● 保存 PNG `satellite_image_panel.py:131,400` | ● `~/.mbdsdr/images` 最近 50 `satellite_image_panel.py:62,428` |
| METEOR LRPT | ● 卫星云图 LRPT 模式 `satellite_image_panel.py:185-213,271-287` | ● 采样率 `satellite_image_panel.py:187` | ● 图像+帧数 `satellite_image_panel.py:331` | ● 「未出图」`satellite_image_panel.py:370-373` | ● Worker 错误 `weather_panel.py:279-355` | ● PNG | ● 最近 50 `satellite_image_panel.py:428` |
| GOES / GK-2A / FY-4 / FY-3 | ● 气象云图 `weather_panel.py:72-97` | ● 卫星下拉+采样率 | ◐ PNG；FY-3 只帧同步不出图 `weather_panel.py:410-415` | ● 「未出图」`weather_panel.py:739-757` | ● 同一入口 `weather_panel.py:739-757` | ○ 无另存，写源码树 `weather_panel.py:651-656` | ○ 无 |
| DAB / DAB+ | ○ 无面板；MCP FIC hex `mcp_server.py:1138`；工具 `tool_registry.py:2631` | ○ 无 | ◐ FIC/ETI JSON | ○ 无 | ◐ 工具错误 | ○ 无 | ○ 无 |
| SSTV | ◐ 仅 CLI `cli.py:230-234` | ◐ `--output-dir` | ◐ 图像文件 | ◐ 解码器返回 | ◐ 解码器返回 | ● 输出目录 | ○ 无 |
| RDS | ○ 无面板；控制器流式 `controller.py:984-987`；AI 工具 `agent.py` | ○ 无 | ○ 无展示 | ○ 无 | ○ 无 | ○ 无 | ○ 无 |
| VOR | ○ 无（已登记但无路由/UI）`capability_catalog.py:203-216` | ○ 无 | ○ 无 | ○ 无 | ○ 无 | ○ 无 | ○ 无 |

## 2. 「后端能解」与「用户能用」对照

### A. 后端真、用户能用（有 UI）

| 能力 | 后端 | 用户路径 | 诚实边界 |
|---|---|---|---|
| ADS-B | `mbdsdr_ai/adsb.py`、`adsb_lite.py` | 中央地图 + 右栏列表 | 解码对拍为真，但本机 FC0012 类 tuner 上限约 948.5MHz，收不到 1090（`capability_catalog.py:141-145`） |
| APRS | `decode_pipeline` | 右栏 APRS 页签 | 仅合成 2 帧逐字节对拍，无 OTA 证据（`docs/learn/decoder-capability-matrix.md:24`） |
| NOAA APT | `noaa_apt_lite.decode_apt` | 卫星云图面板（实时音频） | 需正确频点 + AM，面板不校验 |
| METEOR LRPT | `meteor_sat`（UI）/ `lrpt_pipeline`（测试） | 卫星云图 LRPT 离线 IQ | UI 走合成级 `meteor_sat`，真录 golden 链未接（见 G6） |
| GOES / GK-2A / FY-4 | `goes_lrit` / `gk2a_lrit` / `fengyun_sat` | 气象云图离线 IQ | FY-4 仅经典 CCSDS，真实 DVB-S2 不在链路内（`weather_panel.py:367-368`） |
| SSTV | `decoders.decode_sstv` | 仅 CLI | 无面板 |

### B. 后端能解、用户不能用或很难用

| 能力 | 后端证据 | 现状 | 原因 |
|---|---|---|---|
| FT8 | WSJT-X 官方样例 5/14 命中（`docs/learn/decoder-capability-matrix.md:26`） | 无面板；CLI `--mode ft8` → `not_implemented`；仅 AI 工具吃原始 samples | 通用 WAV 入口未接线（`decode_router.py:432-444`） |
| ACARS | `acars_decoder` | 仅 CLI；AI 工具 `agent.py:1412` | 无面板；WAV 0 帧假成功 |
| POCSAG | `pocsag_decoder` | 仅 CLI | 无面板 |
| AIS | `ais_decoder` | 仅 CLI，cf32 | 无面板 |
| NAVTEX | `navtex_decoder` | 仅 CLI | 无面板 |
| FT4 | `ft4_modem` | 仅 CLI | 无面板 |
| WSPR | `wspr_modem` | 仅 CLI | 无面板 |
| RDS | `rds_lite`（`controller.py:984-987`）+ AI 工具 | 无展示 | 消息只进 handle，无 UI |
| DAB/DAB+ | `dab_plus_lite` 的 ETI/FIC 解析 | MCP 只暴露 FIC hex | 无 OFDM 射频解调（`dab_plus_lite.py:544`） |

### C. 已登记但实际不可用

| 能力 | 证据 | 状态 |
|---|---|---|
| VOR | `decoder_registry` 登记；`capability_catalog.py:203-216` 标 `unavailable` | 无通用路由、无 UI |
| FY-3D HRPT | 下拉可选 `weather_panel.py:93-96`；只做帧同步 `weather_panel.py:410-415` | 有入口，无出图链路 |

### D. 假成功路径（最需要优先修）

`start_decoder` 接受 10 类：`controller.py:46-47`。实际 `_resolve_decode_fn` 只实现 ADS-B 与 RDS：`controller.py:971-990`。其余 8 类线程空转，`get_decoder_messages` 返回 `[]` 并附「可能仍在等待信号」：`mcp_server.py:939-948`。

## 3. 缺口清单

### G1（P0）`start_decoder` 对 8 类解码器静默空跑

- 证据：`mbdsdr_ai/core/controller.py:46-47`、`controller.py:946-948`、`controller.py:971-990`、`mbdsdr_ai/core/mcp_server.py:917-927`、`mcp_server.py:939-948`
- 现象：`SUPPORTED_DECODERS` 含 `adsb/aprs/noaa_apt/meteor/rds/sstv/ft8/wspr/pocsag/acars`；`_resolve_decode_fn` 只返回 `adsb` 与 `rds`，其余返回 `None`，线程仍启动并 `wait`。
- 影响：AI/MCP/脚本调用方拿到 `ok=True` 与句柄，轮询永远 0 条，还会被告知「可能仍在等待信号」，把「没实现」误判为「没信号」。
- 成本：低。要么接 `decode_digital_mode`，要么在 `start_decoder` 直接返回 `decoder_not_streamable` + 建议 CLI。

### G2（P0/P1）WAV 路径解 0 帧被当成成功

- 证据：`mbdsdr_ai/decode_router.py:1031-1045`
- 现象：`signal_present_no_frame` 仅在输入 `.cf32/.cfile` 且 RMS 超阈值时判定；`.wav` 路径 0 条消息时直接落到 `ok=True`、`status=decoded`。
- 影响：ACARS 缺 `SYN,SYN,SOH` 前导（已知问题）、POCSAG/NAVTEX/FT4/WSPR 无信号时都返回「解码成功，0 条」，用户无法区分「没信号」「前导不对」「算法失败」。
- 成本：中。给每类 WAV 解码器补「同步/前导/帧证据」字段，再统一映射 `signal_present_no_frame` / `preamble_not_found` / `nothing_detected`。

### G3（P1）FT8 有真后端、无用户入口

- 证据：`mbdsdr_ai/decode_router.py:432-444`、`mbdsdr_ai/core/cli.py:235-243`、`mbdsdr_ai/agent.py:738`、`docs/learn/decoder-capability-matrix.md:26`
- 现象：官方 WSJT-X 样例已 5/14 命中；桌面无面板；CLI `--mode ft8` 返回 `not_implemented`；只有 AI 工具 `ft8_decode_audio` 能吃原始 samples。
- 影响：软件对外宣称的强项能力普通用户用不到。
- 成本：中。先接 `--decode-file --mode ft8` 到 `ft8_lite.decode_ft8_audio`，再做面板/表格。

### G4（P1）ADS-B 列表与 APRS 不校验频率/解调模式

- 证据：`desktop/main_window.py:8735-8740`、`desktop/main_window.py:9303-9309`、`desktop/adsb_panel.py:296-305`、`desktop/aprs_panel.py:152-160`、`desktop/adsb_map_panel.py:226-240`
- 现象：只要 dock 可见就把当前 IQ 喂给 ADS-B、把当前解调音频喂给 APRS；标题却写「等待 1090MHz / 144.390MHz」。地图面板有 1090±1.5MHz 门控，列表面板没有。
- 影响：用户在任意频点/任意解调模式下看到「等待信号」，误以为设备没问题；异常被 `except: pass` 吞掉，没有任何诊断。
- 成本：低。照搬 `adsb_map_panel.py:226-240` 的门控，并在面板显示「当前中心频率 X，不是 1090」/「当前模式 X，不是 NFM」。

### G5（P1）解码错误码在 UI 不可读，面板层完全不显示

- 证据：`desktop/status_panel.py:185-203`、`desktop/error_remedy.py:35-130`、`mbdsdr_ai/error_catalog.py`（无 `signal_present_no_frame`）、`desktop/main_window.py:8735-8740`
- 现象：`signal_present_no_frame`、`address_not_recovered` 等没有中文标签与补救建议；已有映射也只服务 MCP 工具结果卡，ADS-B/APRS/卫星面板不接。
- 影响：用户看到原始英文码，或什么都看不到；同一个「没解出来」在不同面板表现不一致。
- 成本：低（补映射）+ 中（面板层接 `error_code`）。

### G6（P1）METEOR 真录图像链未接 UI

- 证据：`desktop/weather_panel.py:279-355`（走 `meteor_sat.demodulate_lrpt`/`compose_visible_image`）；`mbdsdr_ai/lrpt_pipeline.py` 仅测试引用 `tests/test_lrpt_pipeline.py:11`；`mbdsdr_ai/tool_registry.py:2043` 只到 CADU
- 现象：文档把 LRPT 真录链标为「真实」，UI 实际使用合成级 `meteor_sat`；`lrpt_pipeline` 没有 UI，也没有完整图像工具入口。
- 影响：「后端真」与「用户能用」脱节；用户拿到的 Meteor 图不一定来自被验证的那条链。
- 成本：中。把 `lrpt_pipeline` 接到卫星云图 LRPT 离线 IQ 分支，保留旧链作回退。

### G7（P1）气象面板「实时 SDR」入口与实现不符

- 证据：`desktop/weather_panel.py:464-465`（下拉含「实时 SDR」，Tooltip 写「边收边解」）、`weather_panel.py:667-682`（点击后仅弹窗引导先录音，再切回录制文件）
- 现象：实时分支为避免与主窗口 IQ 轮询抢环形缓冲，已改为「先录制、再解码」；但下拉仍保留「实时 SDR」，Tooltip 仍承诺边收边解。
- 影响：入口承诺的是实时，实际是一次性录制；用户会以为软件能边收边出图。
- 成本：低（改文案/置灰为「录制后解码」）或高（做独立过境录制通道）。

### G8（P1/P2）DAB 只有 ETI/FIC，无射频解调

- 证据：`mbdsdr_ai/dab_plus_lite.py:544`、`mbdsdr_ai/tool_registry.py:2631`、`mbdsdr_ai/core/mcp_server.py:1138`
- 现象：`dab_decode_iq` 名字暗示 IQ，实际吃 ETI 字节；MCP 只暴露 `decoder_dab_fic_decode` 的 hex FIC。
- 影响：用户会以为能收 DAB 电台，实际必须手工准备 ETI/FIC 数据。
- 成本：高。建议先把能力目录与面板文案改为「DAB FIC/ETI 解析」，不要宣称可收台。

### G9（P2）FY-3D 入口可选但只做帧同步

- 证据：`desktop/weather_panel.py:93-96`、`weather_panel.py:410-415`
- 现象：下拉有 FY-3D HRPT，执行后返回「帧同步命中 N 帧；出图链路在本面板未接通」。
- 影响：死在最后一步，用户拿到 0 图。
- 成本：高（AVHRR 软比特→出图）；短期建议置灰并注明。

### G10（P2）APRS 无地图视图

- 证据：`desktop/aprs_panel.py:112`、`desktop/main_window.py:10032`
- 现象：只有表格，经纬度未上图；同仓 ADS-B 已有地图。
- 影响：APRS 最直观的位置价值缺失。
- 成本：中。复用 `adsb_map_panel` 的底图与投影。

### G11（P2）APRS/ADS-B 无导出、无持久历史

- 证据：`desktop/aprs_panel.py:54,191`、`desktop/adsb_panel.py:75-76,324`
- 现象：APRS 仅内存 500 条；ADS-B 30 秒 TTL；均无 CSV/JSON；关窗即失。
- 影响：无法复盘、上报或二次分析。
- 成本：低。加 `QFileDialog` 导出 + 可选落盘。

### G12（P2）APRS / 卫星云图不在面板注册表，难被发现

- 证据：`desktop/panel_registry.py:66-107`（只有 adsb/weather 等，无 aprs/sat_image）；`desktop/capability_catalog.py` 从注册表生成
- 现象：两者只在右栏作为页签存在，能力目录也列不到。
- 影响：新用户不知道软件有 APRS 与卫星云图。
- 成本：低。补 `PanelSpec` 与 summary。

### G13（P2）模块面板声称可调解码链路，实际是固定流图

- 证据：`desktop/panel_registry.py:83-85`、`desktop/module_panel.py:652-659`、`module_panel.py:305`
- 现象：summary 写「源设备 → 解调 → 解码 → 输出，逐段调参数」；实际默认流图固定 `Source → WFM Demod → RDS → Audio Sink`，解调下拉只有 WFM/NFM/AM/USB/LSB，没有解码器选择。
- 影响：用户以为能在这里切换解码器。
- 成本：中（接真实流图）或低（改文案）。

### G14（P2）长尾解码器只有 CLI，且参数门槛高

- 证据：`mbdsdr_ai/core/cli.py:222-243`、`desktop/capability_catalog.py:182-189`
- 现象：POCSAG/ACARS/AIS/NAVTEX/FT4/WSPR 需用户自备文件、知道 `--mode/--sample-rate/--baud`。
- 影响：能力存在但普通用户不可达。
- 成本：中。每类做轻量「打开文件→选模式→表格」面板即可。

### G15（P2，推断）双 ADS-B 解码并行重复，CPU 可能翻倍

- 证据：`desktop/main_window.py:8735-8740`（列表每帧全量解码）、`desktop/main_window.py:1897`、`desktop/adsb_map_panel.py:226`（地图 200ms 一次）
- 现象：两个 ADS-B 面板同时可见时，同一 IQ 被两套解码器各跑一次；列表面板无节流。
- 影响：CPU 占用上升，且两处结果口径可能不一致。
- 成本：低（共享一次解码结果 / 给列表加节流）。
- 备注：CPU 增幅未实测，标「推断」。

### G16（P2）气象面板产物写进源码树，无另存/历史

- 证据：`desktop/weather_panel.py:651-656`、`weather_panel.py:739-757`
- 现象：出图写 `mbdsdr_ai/artifacts/`，面板无「另存为」、无历史列表。
- 影响：升级或清理源码目录时丢图；用户不知道文件在哪。
- 成本：低。迁到 `~/.mbdsdr/images` 并补保存按钮。

## 4. 严重度排序表（最多 20 行）

| # | 严重度 | 缺口 | 关键证据 | 用户影响 | 成本 |
|---|---|---|---|---|---|
| 1 | P0 | `start_decoder` 8 类静默空跑 | `controller.py:46-47,971-990`；`mcp_server.py:917-948` | 假成功，永远 0 条还提示「等待信号」 | 低 |
| 2 | P0 | WAV 0 帧当成功 | `decode_router.py:1031-1045` | ACARS 前导缺失等被当成解码成功 | 中 |
| 3 | P1 | FT8 无用户入口 | `decode_router.py:432-444`；`cli.py:235-243` | 真能力用不到 | 中 |
| 4 | P1 | ADS-B 列表/APRS 不校验频率与模式 | `main_window.py:8735-8740,9303-9309` | 空等，误判设备/信号 | 低 |
| 5 | P1 | 解码错误码 UI 不可读、面板不显示 | `status_panel.py:185-203`；`error_catalog.py` 缺码 | 无法判断失败原因 | 低+中 |
| 6 | P1 | METEOR 真录链未接 UI | `weather_panel.py:279-355`；`lrpt_pipeline` 仅测试 | 看到的图不一定来自真录验证链 | 中 |
| 7 | P1 | 气象「实时 SDR」入口与实现不符 | `weather_panel.py:464-465,667-682` | 承诺实时、实际只引导录音 | 低/高 |
| 8 | P1 | DAB 无射频解调却像能收台 | `dab_plus_lite.py:544`；`tool_registry.py:2631` | 误解能力边界 | 高 |
| 9 | P2 | ACARS/POCSAG/AIS/NAVTEX/FT4/WSPR 仅 CLI | `cli.py:222-243` | 普通用户不可达 | 中 |
| 10 | P2 | APRS 无地图 | `aprs_panel.py:112` | 位置价值缺失 | 中 |
| 11 | P2 | APRS/ADS-B 无导出与持久历史 | `aprs_panel.py:54,191`；`adsb_panel.py:75-76` | 无法复盘 | 低 |
| 12 | P2 | APRS/卫星云图不在面板注册表 | `panel_registry.py:66-107` | 功能不可发现 | 低 |
| 13 | P2 | 模块面板流图与实际能力不符 | `panel_registry.py:83-85`；`module_panel.py:652-659` | 误以为可切解码器 | 低/中 |
| 14 | P2 | FY-3D 可选但只帧同步 | `weather_panel.py:93-96,410-415` | 0 图 | 高 |
| 15 | P2 | 气象产物写源码树、无另存 | `weather_panel.py:651-656,739-757` | 丢图/找不到 | 低 |
| 16 | P2 | 双 ADS-B 并行重复解码（推断） | `main_window.py:8735-8740,1897`；`adsb_map_panel.py:226` | CPU 上升、口径不一 | 低 |

## 5. 核查方法与未核实项

- 方法：全仓 `grep` 解码模式名与函数名、读取面板/路由/控制器/MCP 注册表、对照 `docs/learn/decoder-capability-matrix.md` 与 `desktop/capability_catalog.py`。未启动 SDR、未跑真机、未改产品代码。
- 未核实：
  - G15 的 CPU 增幅为静态推断，未做 `perf_probe` 实测。
  - ADS-B 本机收不到 1090 为既有登记（`capability_catalog.py:141-145`），本次未复测设备上界。
  - GOES/GK-2A 的成像质量未做新对拍，仅确认调用链存在。
  - 「卫星云图」与「气象云图」两个入口的 NOAA 离线 WAV 能力边界未逐一实测，仅按代码路径记录。

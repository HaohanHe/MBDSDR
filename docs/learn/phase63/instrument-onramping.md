# 仪器上手门槛消融：Agent 对话内「一句话测驻波」

> 产品内真实功能（非独立 demo）。用户在 AI 对话框直接问天线驻波，App 经真实
> NanoVNA 三通道工具执行，按真实串口状态驱动回复；未连接/未校准给真实可达引导。
> 全仓 MIT；Replay 仅作测试夹具（标注合成），产品路径面对真实串口、不内置假数据。

## 1. 链路（file:line）

- UI 输入：`main_window.cpp` aiInput_ → sendBtn → `agent_->sendMessage()`（见线 B 笔记）。
- 离线确定性路径：未配 API Key 时 `Agent::localCommand(input)`（`cpp/src/ai/agent.cpp`）。
  新增「驻波/VSWR/天线」意图分支：识别即调 `runVswrOnramp(engine, input)`。
- 编排：`cpp/src/ai/vna_onramp.cpp runVswrOnramp`：
  1. `executeTool("get_vna_status")`（真实工具，留审计轨迹）；
  2. 连通性以 `engine->vnaClient().isConnected()` 为准（注：该工具 JSON 的 `connected`
     字段被 addSourceFields 覆盖为 SDR 源连接，不可直接信）；
  3. 已连接则 `readCalStatus()` 主动刷新校准，无 `cal'ed` 即未校准；
  4. 否则 `planSweep` 围绕目标频率 → `set_vna_sweep` → `get_vna_data` → 在
     freqs/vswr 平行数组取目标最近点 → `interpretVswr` 判定 → `renderReport` 回复。
- 纯函数：`cpp/src/ai/vna_onramp.{h,cpp}`——`isVswrIntent/parseTargetMhz/classifyVswr/
  planSweep/nearestIndex/interpretVswr/renderReport`。阈值 `kVswrGoodMax=1.5 / kVswrOkMax=2.0`。

## 2. 四分支行为

| 状态 | 触发 | 回复要点 |
|---|---|---|
| disconnected | 未插 USB | 引导：插 PORT 1/S11、VID:PID 0483:5740、Linux dialout 组/udev |
| uncalibrated | 已连无 cal | 提示先做 OSL（open/short/load）校准 |
| good | VSWR<1.5 | 读数 + 匹配良好 |
| ok | 1.5–2.0 | 可用，提示接头/环境 |
| bad | >2.0 | 驻波偏高，给排查顺序 |

目标频率从问题解析（如 438.5MHz），不硬编码默认；无频率则反问。

## 3. 测试

`cpp/tests/test_vna_onramp.cpp`：自造 ReplayTransport 注入 `engine.vnaClient().attachTransport()`，
覆盖四分支 + 目标频点选取 + 纯函数阈值 + 无频率反问。**11 passed**。
`test_agent` 38 passed（无回归）。回放数据为合成协议文本，非真实测量。

## 4. 待办（下一批，依赖线 B vna_rf）

- 晶体谐振 fr/Q、TDR 电缆长度/损耗、LC 等「一句话」意图，待线 B 的
  `analyze_vna_resonance`/`vna_tdr_cable` 工具落地后，在 localCommand 经 executeTool 接入。
- VNA tab 数值读数（fr/Q/TDR）与 Smith/Polar/TDR 曲线留后续批次。
- 真机未在环：connectSerial 握手/取数待用户插 H/H4 回传。

## 5. CMake 待加入清单（交统一编辑）

- core 源：`src/ai/vna_onramp.cpp`（紧随 `src/ai/agent_tools.cpp` 后）。
- 测试目标：`test_vna_onramp tests/test_vna_onramp.cpp`，链 `mbdsdr_core` +
  `Qt6::Test/Core/Network/Widgets/Multimedia/Concurrent`，`add_test(NAME vna_onramp ...)`。
  （线 B 已在统一 CMake 接好 vna_onramp.cpp 与 test_vna_onramp。）

## 6. 统一 VNA 意图入口（本批扩展）

`isVnaOnrampIntent` 取代单一驻波分支，`runVswrOnramp` 内部分发 `VnaIntent`：

- **驻波/匹配**：现有四分支（接线/OSL/good/ok/bad）。
- **谐振/晶体**：有标称频率 → 窄扫 ±10kHz → `analyze_vna_resonance` → fr/Q/BW/ESR/串并点；
  无标称 → 请用户给出标称频率。
- **TDR 电缆**：识别电缆类型取速度因子（常量表 RG-58/213≈0.66、RG-8X≈0.78、LMR-400≈0.85）；
  未指定 → 列选项不硬编码假设；指定后 `vna_tdr_cable` → 电缆长度/故障距离。
- **L/C**：目标频点由 S11 虚部经 `vna::reactanceToHenries/Farads` 派生（感性 nH/容性 pF）。

诚实空态贯穿：未连接/未校准/无效分析（点数不足、span=0、无反射峰）均给真实引导，不伪造。
测试：`test_vna_onramp` 15 passed；`test_agent` 38 passed 无回归。

## 7. 未做（诚实标注）

- Smith/Polar/滤波器一句话意图与曲线：待 analyze_vna_filter 交付后下轮。
- 真机未在环，待用户插 H/H4 回传。

## 8. 第③批：VNA tab 数值读数区 + 快照 + 数值钉扎

- 读数区：`vna_panel.{h,cpp}` 新增 `analysisLabel_`；`applyReadout` 在真实扫频上跑
  `vna::analyzeResonance(freqs,s11)`，经纯函数 `vnaAnalysisText`（vna_panel_format.cpp）
  显示 fr/Q/BW/ESR；无效（点数不足/span=0）诚实显示 "--"，不伪造。
- 快照：MBD_VNASHOT（MBD_TAB=VNA）三档 OCR——1920 完整显示
  "谐振 fr 14.0500 MHz · Q 0.5 · BW 2900000 Hz · ESR 67.65 Ω"，0 裁切 0 叠字；
  960 清晰；640 触最小宽 floor，文本换行/右侧略溢出（诚实标注，不崩）。
- 数值钉扎：`test_vna_onramp resonanceRlcTheoryPin`——已知串联 RLC（L=1µH,C=1pF,Rs=10Ω）
  理论 fr≈159.15 MHz，实测 fr 在 ±0.5 MHz 内、ESR≈10Ω±2Ω。
- 测试：test_vna_onramp 16 passed（含数值钉扎）；test_vna_panel_format 7 passed。
- 注：execGetVnaData 现同时读 s21（data 1），回放脚本已对齐。

## 9. 第④批：滤波器意图 + SDR 听手台对照

- **滤波器意图**：`classifyVnaIntent` 增 `Filter`（vna_onramp.cpp:52）；runVswrOnramp 在当前
  扫频上真实调 `analyze_vna_filter`，报形态(bandpass/bandstop/highpass/lowpass)/-3dB 边沿/
  带宽/插损/阻带衰减；平坦或点数不足诚实说明并提示先设扫频范围。
- **SDR 听手台**：`agent.cpp localCommand` 增 listen 分支（:81 起）——"我想听 409.750MHz
  手台" → 真实 `onSetCenterFreq` 调谐 + `setDemodMode("NFM")`，附源未启动/udev/dialout 与
  CTCSS/静噪提示；复用既有引擎方法，未新增工具。
- 测试：test_vna_onramp 17 passed（含滤波器空态分支 + 意图分类 Filter）；test_agent 38 passed。
- 诚实空态：滤波器未给扫频范围提示先设置；SDR 未连源给真实引导，不伪造声音。

## 10. 第⑤批：ppm 协议核验 + CDCSS/DCS 收尾核验

- **ppm**：无协议依据（详见 docs/learn/nanovna/protocol-study.md「参考晶体 ppm 协议核验」）。
  H `freq` 仅设频点（main.c:621），V2 寄存器表无 ref clock/ppm。不落地命令，不硬编字段。
- **CDCSS/DCS 已闭环**：Golay(23,12) 干净室实现（dsp/cdcss.cpp:34-81，生成多项式 0xC75，
  权≤2 纠错表）；104 码 3-of-8 恒权码表（cdcss.cpp:102 `kDcsCodes`）；
  latch 去抖（cdcss.cpp:126-127，kLatchHits=3/kLatchMisses=5，应用于 214-225）；
  确定性合成位流测试在 test_squelch_gate.cpp（cdcssDetectsConfiguredCode/
  cdcssFalseOnWrongCode/cdcssCorrectsBitFlips，合成 DCS 位流 + 翻转 1/2 bit 纠错）。
  三通道 set_cdcss/get_cdcss_status 与 UI 开关已在。无 DSP/UI 缺口。
- 测试：test_squelch_gate 13 passed（含 3 个 CDCSS 用例）。

## 11. 第⑨批：CDCSS 外显码值 + dump USB 异常分支
- CDCSS UI：badge 原本只显"检测到"，现补外显 on-air 解码 3 位八进制码（main_window.cpp
  updateCdcssBadge）；engine 新增只读外显 cdcssDecodedCode()（spectrum_engine.h:116，
  复用 CdcssDecoder::lastCode，无信号 -1 诚实空态）。DSP 层闭环（Golay/104 码表/latch/
  三通道/开关）已于第⑤批核验。
- dump USB 分支：nanovna_dump.py run_dump 捕 (OSError, NanoVNADriverError) → 明确报错+
  端口存在/权限/占用三排查；pytest 3 passed（replay 形状 + VSWR 钉扎 + 不存在端口 SystemExit）。

## 12. 第⑪批：NRW 标注 + dump --selftest + SDR 侦察
- NRW：permeabilityNRW 公式核对为标准一阶 NRW（X=(S11²-S21²+1)/2S21；μr=(1+Γ)/(1-Γ)·波导项），
  vna_rf.cpp:384 加 out.note 诚实标注——薄板近似、小 μ'(≪1) 为薄样/低反射量级伪影，非标定测量。
- dump --selftest：nanovna_dump.py _selftest_script + --selftest 旗标，无设备跑通
  连接→sweep→data 形状校验，明确标注合成演示；pytest 4 passed。
- SDR 下一机制侦察：FFT 尺寸/AGC 手动/静噪滑块/频段下拉/WFM 立体声/书签扫描均已在
  main_window，无新硬缺口，按宁缺毋滥不落地。

## 13. 第⑭批：CDCSS 三通道终检
- set_cdcss/get_cdcss_status 三通道三处全在：tool_schema.cpp:608/638、agent_tools.cpp:1954/1955
  （exec :886/:931）、control_hub.cpp:120/163。无缺口，计数无变化。
- UI badge 八进制外显已就绪（第⑨批）。合成位流 bit-flip 纠错测试已在 test_squelch_gate 13 passed。
- offscreen 快照不可注入（定案见第⑩批），真机步骤已入 quickstart。
- pytest nanovna_dump 5 passed 复核全绿；PENDING 列字段对齐延续第⑬批。

## 14. 第⑮批：发布前健康检查 + 预案演练
- LICENSE：MIT，LICENSING.md 有 GPL 边界处理、repos/ gitignore；ci.yml 无硬编码密钥
  （仅注释提及 secrets 需维护者配置）。
- README：30 秒上手已加；工具计数口径并存（README 原型 ~63 / 交付 47 / phase31 65），
  README 已声明原型面≠交付集，不强行统一。
- 预案演练：mbdsdr_ai/fill_evidence_example.py 走「dump -> 复算最低 VSWR 点 -> 填充行」，
  输出带 _MARKER=SYNTHETIC_PLACEHOLDER 标注；真机替换 sweep.json 即完成。

## 15. 第⑯批：fill 扩展 fr/Q/ESR
- fill_evidence_example.py 增 analyze_resonance_py（Python 独立实现，与 C++ vna_rf 同公式：
  |Z|极小=fr、半功率带宽 BW、|Z|谷=ESR）；输出加 谐振_fr_MHz/Q/ESR 列，仍带 SYNTHETIC 标注。
- test_fill_evidence 1 passed（自造谷值钉扎 fr/ESR）。
- SDR++ 侦察：候选（FFT/AGC/静噪/频段/WFM/书签）均已在，无新硬缺口，宁缺毋滥不落地。

## 16. 第⑰批：fill 桥接 TDR/LC/滤波器列
- fill_evidence_example.py 增 tdr_length_py（vf=0.66 默认）、reactance_lc_py（X->nH/pF）、
  filter_shape_py（四形态判定）——Python 独立实现，与 C++ vna_rf 同公式；demo 输出补
  电缆长度/L·C/滤波器形态列，仍带 SYNTHETIC 标注。
- test_fill_evidence 1->4 passed（谐振谷 + TDR 正长度 + LC=1uH 钉扎 + 形态判定）。
- SDR++ 侦察：候选均已在，无新硬缺口，不落地。

## 17. 第⑱批：engine FFT 访问器 + 真实轨迹导出
- spectrum_engine.h 增只读访问器 fftDb()/fftCenterHz()（:58，锁 latestFrame_，无帧诚实空态）；
  spectrum_engine.cpp:1415 emit 处同步存 latestFrame_。
- execExportSpectrumSnapshot（agent_tools.cpp:1360）接真实 bins：逐点画 dBFS 轨迹线 + 峰值标记
  + 中心频点竖线；空 bins 仅画框（诚实）。test_agent 38 passed 无回归。
- UI 导出按钮：未加（频谱工具栏锚定风险大，offscreen 环境不硬塞）；继续用工具调用导出。
- fill 六列核对：VSWR/回损/谐振/TDR/LC/滤波器齐全，SYNTHETIC 标注在；TDR/LC 为 Python 近似，真机以 C++ vna_rf 为准。
- SDR++ 侦察：无新硬缺口。

## 18. 第⑲批：Python 客户端核验
- NanoVNAClient/SerialTransport/ReplayTransport/read_cal_status 均在；补 read_cal_status 边界测试，
  test_nanovna_client 16->17 passed。既有覆盖握手序列/sweep/data0·1/坏行容错/无设备报错。
- SDR++ 侦察：无新硬缺口。

## 19. 第⑳批：真机预案最终化
- dump 输出增 summary（min_vswr + at_hz 摘要行）；pytest 6 passed。
- quickstart 补双平台示例（Linux /dev/ttyACM0+dialout / Windows COMx）+ 回传填表 checklist。
- SDR++ 侦察：无新硬缺口。

## 20. 第㉑批：维护性深检 + --cal 直显
- LICENSING/NOTICE 核对：NanoVNA 协议事实 vs 干净室实现边界表述准确（GPL 上游只学机制、
  WTFPL 参考源、官方 docs 优先），repos/ 仍 .gitignore:91 未入仓。无需修。
- dump --cal 旗标：stderr 直显校准行（open/short/load/thru）；pytest 6 passed。
- SDR++ 侦察：无新硬缺口。

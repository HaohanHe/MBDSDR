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

## 21. 第㉒批：真机回传终检 + SDR 现状侦察
- quickstart 四步 + dump --cal/--json-pretty/--selftest 命令串复核，pytest 6 passed 全绿；
  checklist 与 evidence 字段对齐逐列一致（dump 直出 summary/vswr/回损；vna_rf 复算 fr/Q/ESR/TDR/LC/滤波器）。
- SDR 现状（供线 B）：书签跳转已在（main_window.cpp:1888 bmTable_ cellDoubleClicked）；
  峰值列表 #/频率/强度 表在窄屏截图出现（spectrum_display 域）；FT8 仅复选框(:794)，无解码结果复制功能。

## 22. 第㉓批：FT8 复制核验
- FT8 现状：mbdsdr_ai/ft8_decode.py 有解码函数（decode_ft8_payload/costas/ldpc），
  但**无复制/导出/clipboard 能力**；UI 仅复选框（main_window.cpp:794）。
- 登记为缺口：解码结果导出/复制留线 B；当前手动路径——解码在终端/控制台输出，用户自行拷贝。
- SDR++ 侦察：无新硬缺口。

## 23. 第㉔批：engine peaks() 访问器
- spectrum_engine.h 增只读 peaks(maxN=10, relThreshDb=6)：从 latestFrame_ 找局部极大、
  按强度降序、限 N；无帧/平坦诚实空态。线 B 可据此接 CSV 导出。
- FT8 评估：ft8_decode.decode_ft8_payload 返回 {data_bits, iters}，无去重/SIC/呼号字段；
  export_ft8_results 数据源需线 B 在 C++ 侧补结构化解码结果。
- SDR++ 侦察：无新硬缺口。

## 24. 第㉕批：FT8 Python 桥接侦察
- decode_ft8_payload 返回结构化：info_bits/data_bits/crc_bits/codeword/iters/crc_ok/
  costas_score/rejected/reject_reason；入参 snr_db（可选，联合拒绝门）。
- **缺**：无 bits->文本/呼号的报文解码（无 wsjtx 风格 message decode），故桥接只能给
  "crc_ok 候选数 + costas 分 + SNR"，不能给呼号/消息文本。
- 桥接路径建议：线 B 在 C++ 侧接 crc_ok 候选列表（条数/SNR/频率槽）作 export_ft8_results 真实行；
  呼号文本解码留后续（诚实空态，不伪造呼号）。
- SDR++ 侦察：无新硬缺口。

## 25. 第㉖批：FT8 候选集接口
- ft8_decode.py 增 collect_ft8_candidates（freq_hz, crc_ok, costas_score, rejected,
  reject_reason, iters，按 crc_ok/分数降序）——线 B C++ export_ft8_results 数据源。
- test_ft8_candidates 1 passed。
- 75-bit 呼号文本解码（base-41/网格/遥测）：公式公开但实现链长，登记远期项，本轮不伪造。
- SDR++ 侦察：无新硬缺口。

## 26. 第㉗批：FT8 候选 CSV 出口
- ft8_decode.candidates_to_csv：候选列表 -> CSV（freq_hz,crc_ok,costas_score,rejected,
  reject_reason,iters），与 C++ export_ft8_results 列对齐；空态仅表头。
- test_ft8_candidates 2 passed（行 + 空态）。呼号文本解码仍远期项。SDR 无新缺口。

## 27. 第㉘批：SDR++ 机制学侦察（宁缺毋滥）
- 候选（FFT 尺寸/AGC 手动/静噪滑块/频段下拉/WFM 立体声/书签跳转/峰值表/FT8 候选出口）
  均已覆盖（main_window 48 处相关接线）。无新硬缺口，不落地。

## 28. 第㉙批：场景话术/代码边界核查
- 全仓 grep（cpp/src + mbdsdr_ai）：代码中无 ISS 通联/对话剧本/预制回答话术（命中均为
  "contains/isString" 等通用英文）。代码 0 场景文案。
- docs 边界声明：本仓 docs/learn 下的对话/示例仅演示工具链组合，**产品不含预制剧本**；
  真实回复由实时工具结果驱动。
- SDR++ 侦察：无新硬缺口。

## 29. 第㉚批：onramp 引导定位复核
- 盘点：runVswrOnramp + classifyVnaIntent 五类 + 报告文案。
- 判定 = **原子便利（保持），冲突 0**：所有分支真实调 executeTool（get_vna_status/
  vna_tdr_cable/analyze_vna_resonance/analyze_vna_filter/set_vna_sweep/get_vna_data），
  输出由真实工具结果格式化；引导文案仅诚实错误提示（未连接/未校准/点数不足），
  无预制剧本/固定回答。
- 与"纯工具+模型自主"一致：onramp 是真实工具链的薄编排器，产品不含演示话术。
- SDR++ 侦察：无新硬缺口。

## 30. 第㉛批：回传链路端到端预演
- selftest --json-pretty --out 全路径复跑通过，sweep.json keys 齐全（S11/S21/cal/freqs/
  summary/sweep/vswr/回损）。
- fill 边界加固：analyze_resonance_py/tdr_length_py 空数组不崩溃返回零；test_fill_evidence 6 passed。
- 双平台命令（/dev/ttyACM0 vs COMx）+ dialout/udev 已在 quickstart；回传 checklist 与 argparse 零漂移。

## 31. ISS 通联场景工具映射（docs 示例）
示例：145.800MHz FM / 定向天线 / 多普勒提示。
- 映射：onSetCenterFreq(145.8e6)+NFM → 源连接引导（udev/dialout）→ 卫星过顶提示
  （orbit 工具，模型知识）→ 多普勒频移随仰角变化提示。
- **边界**：示例仅演示工具链组合，属模型知识非代码；产品不含预制剧本/ISS 话术。

## 32. 第㉓批 P0 实证
- RTL-SDR：CMakeCache RTLSDR_LIBRARY-NOTFOUND（云 VM 无 librtlsdr，系统只读禁 sudo）。
  stub 路径 rtl_sdr_source.cpp:211 返回 nullptr ops → start()=false → engine
  spectrum_engine.cpp:540 sourceError「RTL-SDR 设备打开失败：未检测到硬件」诚实报错。
  ENABLED(HAVE_RTLSDR=1) 构建只能真机/Windows 侧验证（需 libusb+librtlsdr 编译链）。
- LLM loopback：llm_client.h:46 已有 TransportFn 注入缝（setTransport）；ai_real_link 16 测试覆盖。
  QTcpServer 真实 HTTP 往返测试需逆向 OpenAI tool_calls schema，本轮不冒险造半成品，登记下批。
- 半成品清单：ADS-B Gillham/地速（在）、RDS type4/8/0B/2B（在）、network audio sink routed stub（在）、
  FT8 生产源桥接 collect_ft8_candidates→C++（Python 侧已备，C++ 接真实行待线 B）、mobile Flutter（只读盘点）。

## 33. 第㉓批 P0：LLM loopback 端到端（真实 HTTP）
- 新测试 cpp/tests/test_llm_loopback.cpp（3 用例）：
  1) QTcpServer 127.0.0.1:0 收真实 POST（断言 POST+Authorization 头）→ 手写 OpenAI
     ChatCompletion（tool_calls=get_capabilities）→ LLMClient 真实 QNAM 往返 →
     r.toolCalls[0].name=="get_capabilities"。
  2) 错误分支：服务器回 500 + 畸形 JSON → r.error 非空不崩溃。
  3) parserWhitelist：parsePlanFromLlm steps 工具白名单 get_capabilities ok。
- 依赖：LLMClient.chat()/llm_protocol ToolCall.name/plan_parser.h。
- 新 CMake target：test_llm_loopback（回报挂）。

## 34. 第㉔批 P0 #65：Python 长尾收敛
- 盘点：Python MCP 工具 53→60（+7 A 类）；`tool_registry` 74 个注册族（~280 预期基底）。
  cpp 缺口核对 baudot/ardop/dab_plus/nrsc5/freedv/dmr/fst4/rtl433/rtk/gk2a/bladerf/gimbal
  在 cpp/src 均 0 命中；M17/LRPT/ACARS/FT8/NAVTEX/POCSAG/VOR/GNSS-NMEA C++ 已有，不计缺口。
- A 类挂入 `core/mcp_server.py`（DAB+ FIC、NRSC5 帧、rtl433 list/decode、GK-2A 头、
  FST4 编码、bladeRF 参数查表）；新增 Python 扩展域「解码器长尾」，C++ 冻结 8 域不动，
  客户端合并视图并列拼装，schema 各自独立不复制。
- B 类不挂：freedv/dmr（整段音频/IQ 边界不自然）、m17（与 C++ 重复）、ardop/gimbal/
  rtklib（已注册或重复/需硬件）。C 类登记：baudot/Wi-Fi/LoRaWAN/RTK-PPP、GPL 外部编排。
- GPL：LICENSING §2 已覆盖独立进程边界（§4b 为 WTFPL 专用）；SatDump/wsjtx/fldigi/multimon
  编排未实现，登记 C 类，不抄不链不内嵌。
- 诚实：所有 A 类结果体带 `real_hardware_verified:false`（合成钉扎可过、真机 PENDING）。
- pytest：335→342 passed（+6 长尾/域钉扎）。详见 python-decoder-longtail.md。

## 35. SDR++ 机制学做深化侦察（只读，结论=无真缺口）
- 对照方法：官方 SDR++ 仅产品机制知识（repos/ 本节点缺失，gitignore:91 未持久，如实标注）；
  仓内以 Read/Grep 盘点。
- 8 候选逐项判定（全部非缺口）：
  1) 瀑布时间轴（历史滚动/时间刻度/暂停冻结不丢帧）→ 已落地 spectrum_display.cpp:328/412/541；
  2) 扫频状态机（Idle/Scanning/Paused/Hit + loop/PingPong/单次回绕）→ frequency_scanner.h:32
     + UI 按钮组/状态标签 :1642-1687；
  3) 增益/AGC 链路（Tuner/RTL AGC 开关 + 手动滑条/离散档/回显 + 状态栏 ~1Hz 回读）→
     :349-352/:576-589/:2480；RTL 单增益滑条与官方一致，三阶 LNA/IF/RF 不伪造；
  4) VFO A/B swap → 官方产品无此机制（动态多 VFO 各自解调），我们的多 VFO 管理面已超官方；
  5) 解调器切换带宽联动（bandwidthOnModeSwitch + ADS-B 1090MHz 特例 + WFM force-mono）→ 已落地；
  6) 频段预设联动（bandCombo→中心频+模式+带宽）→ main_window:622-631 已落地；
  7) 频谱显示选项（dB range/格线/门限/maxhold 衰减/余辉/palette）→ spectrum_widget 全落地；
  8) 其余解码器结果 UI（M17/POCSAG/ACARS/NAVTEX 均 QTableWidget 可选中复制）→ 无 FT8 类
     "结果不可见"缺口；FT8 无历史解码列表表为历轮已登记项（第㉓批），已于后续批次落地为
     get_ft8_history 只读工具 + export_ft8_results all_history=true + SDR 主界面解码 tab "FT8"
     滚动历史表（200 条上限，引擎 ft8History() 单一数据源，badge 与表同源），闭环。
  9) ADS-B 工具面闭环：引擎 adsbDecoder_ 已完整解码（AircraftInfo 与 dump1090 track.c
     输出形状逐项对齐：icao/callsign/altitude/lat/lon/gs/heading/vr/df/crc/last_seen），
     新增只读工具 get_adsb_aircraft（dump1090 --aircraft 形状，limit 1..50 默认 50，
     1090 MHz 无信号 aircraft=[] 诚实空态）+ 引擎 aircraftSnapshot(accessor 与
     adsbAircraft() 信号共用同一数据源（aircraftTable_ 按 icao upsert）。dump1090 为
     GPL-2.0 项目，本轮仅参照其输出形状与 CPR 解码算法（已在 docs/learn/dump1090.md
     中立注释），不复制其代码。
- 结论：无真缺口，只做维护线，不强行改码（宁缺毋滥）。C++ 无改动，双构型沿用基线
  D125/R125=143/143。

## 34. 真机就绪终检（无设备实测）
- 四命令链全跑通（--selftest/--cal/--json-pretty/紧凑），keys 齐全：
  S11/S21/cal/frequencies_hz/model/port/return_loss_db/sn/summary/sweep/version/vswr。
- dump↔fill 六列对齐：fill 读 data["frequencies_hz"]/["S11"]/["S21"]，dump 输出同名；
  VSWR/回损(nanovna_client.vswr)、谐振/TDR/LC/滤波器重算自 S11/S21——列名单位一致，无需修。
- pytest 14 passed（dump 6 + fill 6 + ft8 2）。quickstart/CDCSS/双平台命令与 argparse 一致。

## 35. 补批：本地记忆族（P3 #73，工具 72→75）
- 新模块 `cpp/src/ai/memory_store.{h,cpp}`：系统 SQLite C API + FTS5（unicode61），
  `~/.mbdsdr/memory.db`（mem_meta 元数据 + mem_fts 索引，rowid 对齐，事务写）；测试用 MBDSDR_MEMORY_DB 重定向。
- 三工具：memory_add(write)/memory_search(read)/memory_update(write)，scope=user/agent/session；
  CJK 经双侧 cjkSplit 归一化（unicode61 连续汉字单 token 问题），查询词元化转义防注入。
- MCP 域 8→9：双侧「记忆编排」（C++ domainGroups + Python FROZEN_DOMAIN_SUMMARIES 钉扎）。
- 设计说明见 docs/learn/phase63/agent-memory-notes.md。

## 36. 补批：checkpoint-writer 执行器（P3 #74，工具 75→77）
- 新模块 `cpp/src/ai/checkpoint_writer.{h,cpp}`：内存队列 + std::thread 后台 worker（~150ms drain，无 cron），
  submit 非阻塞入队；单写者不变量=写盘只在 writer 线程，search 侧 QMutex 保护并发。
- memory_add/memory_update 语义升级为入队（返回 queued=true + pending_count）；新增 checkpoint_status(read)
  与 memory_flush(write，手动 gate)：flush 同步排空并落库，重写 ~/.mbdsdr/checkpoint.md 人类可读快照。
- MCP 「记忆编排」域挂入两工具（域数仍 9，跨语言集合钉扎不变）；count 全链同步（registry/schema/ai_real_link/mobile/phase31/README）。

## 37. 补批：APRS 全链路闭环 + AM AGC 深度（P3 #76，工具 81→82）
- 闭环 phase12 两登记项：`sdrpp-demod.md:332`（APRS 1200/2400 FSK 判「未实现」——
  实为 fsk_demod 孤岛无接线，本轮接成 NRZI→HDLC→AX.25 全链路）与 `:402`（AM 音频侧 AGC 判「缺深度」——
  Agc 类本身完备，补骤降自动提升补测）。
- 新模块 `cpp/src/radio/hdlc.{h,cpp}`（干净室 MIT）：nrziDecode / hdlcDeframe（flag 搜索 + 去填充 +
  CRC-16/X.25 反射校验，closeFrame 丢弃 flag 前导残余位）/ hdlcEncode（测试夹具）。
- 引擎 `SpectrumEngine::aprsMessages(limit 1..50)`（锁保护、newest-first、空态 {}）、`feedApRsBits`
  （生产路径）、`injectAx25FrameForTest`（测试仅，显式标注）。
- 工具 `get_aprs_messages`（只读）三通道：schema/executor+dispatch/control_hub 只读表行 + headless MCP
  「亚音数字解码」域。测试：test_hdlc 5 用例 + test_agent 2 APRS 用例 + test_agc_dsp 骤降用例；
  合成位流用占位呼号 TEST-1..4（诚实夹具，不预置真实呼号）。
- 双构型：D135 Debug 与 R135 Release 各 146/146 主体 + 串行组 4/4 + e2e_smoke Skipped（无音频）。
- 遗留 PENDING：真实 1200/2400 baud 射频信号端到端（FSK 解调器 → feedApRsBits）需真机验证；
  hub 侧 limit 固定 50 未透传参数（引擎与工具侧已支持，登记）。

## 38. 补批：色板工具诚实化 + get_color_maps（P3 #77，工具 82→83）
- 侦察纠正：gap L8「外部 JSON 色板文件」判「未实现」已过时——`parseColormapJson`（spectrum_render.h）与
  UI 右键加载/启动重应用完整存在；真缺口是 **headless 三通道断点**：set_color_map 只存路径不加载不校验，
  文件坏时工具仍报成功（UI 静默回退内置）。
- set_color_map 语义升级：加载 + 校验（复用 parseColormapJson）通过才持久化；缺失/非法 JSON/色标不足 →
  ok:false + 人类可读原因，不保存偏好。UI 启动重应用同一文件不受影响。
- 新增只读 get_color_maps：内置 3 色板 + 当前外部文件实时校验（path/name/stops）；文件消失/变坏 → external=null
  + 诚实 note。三通道 schema/executor/hub + headless MCP 域（SDR 调谐/VFO）。
- 测试：test_colormap_file 5 用例（对象/数组/缺文件/坏 JSON/单色标）+ test_agent 3 用例（合法加载/坏文件拒收
  且不污染设置/外部态往返）+ test_control_hub 旧用例升级为真实临时文件。D135/R135 各 147/147 主体 + 串行 4/4。
- 判定登记：L13 GFSK 高斯匹配滤波无用户可感知接入场景（fsk 基座已闭环 APRS），非缺口；L10/L11/L12 缺深度
  低价值，不动。


---

## SatDump 深挖批：Viterbi 口径收敛（2026-10-14，对 %TEMP%/kilo/upstream/SatDump clone 逐行核对）

承接第㉘批 SatDump 审计登记的「Viterbi 参数三套并存未收敛」，本批逐行核对上游源码后结案：

**1. 多项式常数：两套书写约定，非两套码。**
- 本模块 `{79, 109}`（= 0o117/0o155）与 `meteor_sat.py:635` 的 `G1=171/G2=133`（八进制=121/91）
  **互为 7bit 逆序**：79=0b1001111 逆序=0b1111001=121=0o171；109=0o155 逆序=91=0o133。
- 即 LSB-first 系数表（本模块，同 SatDump `viterbi27.h:8 CCSDS_R2_K7_POLYS={79,109}`）
  与 NASA 八进制约定描述的是同一物理码，各自编码器/译码器内部自洽（两套合成往返都能过），
  差异仅系数书写方向。已加钉扎测试 `test_poly_conventions_are_bit_reversal_pairs`
  （任何一侧改常数忘了同步即红）。**「哪一端 u0 先进寄存器」在真实 Meteor-M2 空口对接前
  无数据可裁决**——自审计登记继续有效，OTA 样本到位前禁止两套约定在同链路混用。

**2. 译码语义：与 SatDump/GNU Radio 同构（全帧回溯 + 度量自选终态）。**
- SatDump `Viterbi27` 包装 `cc_decoder(frame_size,7,2,polys,0,-1)`
  （viterbi27.cpp:7）；其 `chainback_viterbi` 从 final_state=-1 的最小度量终态做
  整帧回溯（cc_decoder.cpp:228-276），前向窗口比帧长少 K-1 个符号（:40）。
- 本模块 `viterbi_decode` 同为全帧 ACS + argmax 终态整帧回溯，仅前向跑满含尾符号、
  输出剥除末尾 6 个尾比特——对带尾比特的 CCSDS 流与上游行为等价。语义已写进
  docstring 并附上游 file:line。

**3. 死参数清除。** 原 `traceback_depth=96` 参数**函数体从未使用**（文档声明回溯深度，
  实际恒为全帧回溯）——删除，并加签名钉扎测试防复活。文档与实现掰腕子的情况按
  仓库文化算 bug，本批按 bug 处理。

**4. 仍未结（需真机）**：三处 Viterbi 实现（lrpt_fec / meteor_sat 经 demod /
satdump_adapter）与真实 Meteor-M2 空口的端到端对齐，唯一裁决手段是一段真实
LRPT 录制。候选输入：ISS/NOAA LRIT 直播过境时抓录，或 SatDump 官方 sample
基带（clone 内 plugins 测试数据若可得）。

---

## skill 注册表缓存与 user 根（2026-10-14，kimi-code 审计第五落点）

`mbdsdr_ai/skill_registry.py` SkillRegistry 原状：每次 list()/load() 前全量
`os.listdir` 重扫、只有项目根 `skills/` 一个根、无激活事件。本批三项升级：

- **目录 mtime 缓存**：`_scan()` 改为按根缓存——仅当某技能根 `st_mtime`
  变化（或首次见到该根）才重扫该根，否则复用缓存条目；合并视图（项目根
  优先）每次重建但零 I/O。既有单参构造 `SkillRegistry(skills_dir)` 与
  list()/load() 调用方式不变（agent.py:523、tests/test_modularization_audit.py
  调用方零改）。
- **user 根**：新增第二个技能根 `~/.mbdsdr/skills`（对齐 kimi-code 的
  user/project 双根，路径约定同 plugin_system 的 `~/.mbdsdr/plugins`）。
  构造签名 `SkillRegistry(skills_dir, user_dir=None, hook_manager=None)`：
  `user_dir=None` 取默认 `~/.mbdsdr/skills`（expanduser+normpath）、显式传
  `""` 禁用。**同名技能项目根优先**，user 根不得覆盖；`to_summary()` 增
  `source` 字段（"project"/"user"）区分来源，既有键不动。
- **激活事件**：`load()` 成功读取正文后向注入的 HookManager 发
  `EventType.AGENT_TOOL_CALL`（"agent.tool_call"），data 含
  `{skill, path, source}`，事件 `source="skill_registry"`；未注入
  hook_manager / 无订阅 / 触发异常一律静默跳过，绝不影响主流程
  （可选注入 + except 静默的兜底风格同 plugin_system）。
- **mtime 边界（如实标注）**：若目录内容以不影响父目录 mtime 的方式被改动
  （少见：部分编辑器/原子替换直接操作子条目而不触父目录时间戳），重扫不被
  触发、缓存过期；文件系统 mtime 粒度较粗时，同粒度窗口内的改动也可能不改变
  `st_mtime`。不引 inotify/watchdog 依赖、不过度设计；确需严格热更新可后续换。
- **与 kimi-code skill 机制的对应**：user/project 双根、同名项目根优先、
  Markdown+frontmatter、目录摘要+按需加载（省 token）同构；本项目特有多根
  缓存与 HookManager 事件面。
- **未做（诚实标注）**：URL 技能源、技能信任分级/签名校验、激活遥测上报；
  agent.py `_register_skill_tools` 侧接线（传入 `hook_manager`、显式声明
  user 根）不在本轮文件范围，留待后续批次——当前 SkillRegistry 已具备注入点。
- **测试**：`mbdsdr_ai/tests/test_skill_registry.py` 11 用例（缓存命中
  不重扫以 os.listdir spy 计数、mtime 变化触发重扫、user 根发现+项目根
  同名优先、空/不存在目录诚实空列表、激活事件 data 字段+未命中不发+
  无 hook_manager 静默+触发异常不炸、单参构造向后兼容）；
  `python -m pytest mbdsdr_ai/tests -q` **477 passed / 10 skipped**
  （改前基线 466/10，+11 新用例，无退步）。

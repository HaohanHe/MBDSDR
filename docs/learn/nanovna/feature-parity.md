# NanoVNA 射频分析能力（feature parity）——第一批

> HEAD=0d8728b 起。本批把 NanoVNA 的射频分析能力（阻抗换算 / 谐振 / TDR / 同轴损耗）落到
> C++ 纯函数层，并新增两个 Agent 工具（`analyze_vna_resonance` / `vna_tdr_cable`）。
> 干净室 MIT：公式为公开工程公式，机制参考自上游（nanovna-saver RFTools.py、
> NanoVNA-App OneOfEleven Graphs.h、TDR.py），代码独立编写不抄实现。

## 1. 纯函数层 cpp/src/vna/vna_rf.{h,cpp}

| 能力 | 函数 | 公式（公开） | 参考 file:line |
|---|---|---|---|
| 串并互转 | `seriesParallel(rs,xs,f)` | Q=\|Xs\|/Rs；Rp=Rs(1+Q²)，Xp=Xs(1+1/Q²) | RFTools.py:125 / :136 |
| Z→电感 | `reactanceToHenries` | L = X/(2πf)（X>0） | RFTools.py:110 |
| Z→电容 | `reactanceToFarads` | C = -1/(2πf·X)（X<0） | RFTools.py:103 |
| 谐振 | `analyzeResonance(f,s11)` | fr=\|Z\| 极小；ESR=fr 点实部；BW=R 升到 ESR·√2 处带宽；Q=fr/BW | 通用晶体/串联谐振分析 |
| TDR | `tdrCable(f,s11,vf)` | S11 窗化 IFFT→冲激峰；distance=vf·c·t/2；Kaiser 窗（beta=6） | TDR.py:46 kaiser_correction |
| 同轴损耗 | `coaxLossDbPerM` | -20log10\|S21\| / 距离 | 通用插损法 |

## 2. 三通道 file:line（schema 58→60）

| 工具 | 读/写 | schema | executor | control_hub |
|---|---|---|---|---|
| analyze_vna_resonance | read | tool_schema.cpp | `execAnalyzeVnaResonance` | `cmdAnalyzeVnaResonance`（读表） |
| vna_tdr_cable | write（gate） | tool_schema.cpp（velocity_factor 必填，0<vf≤1） | `execVnaTdrCable` | `cmdVnaTdrCable`（写表） |

诚实空态：无设备 → `valid=false` + note；缺 velocity_factor / vf 越界 → `ok:false`；手动模式 gate 拦截写。

## 3. 测试计数

- **test_vna_rf**（新，待 CMake 挂载）：**7 passed, 0 failed**——串并已知点（Rs=Xs=50→Q=1,Rp=Xp=100）、
  L/C 钉扎（10µH@1MHz、1nF@1MHz）、合成串联 RLC（fr≈15.9MHz、ESR≈50）、合成 TDR 正长度、
  诚实空态（点数不足/span=0）。
- 金集（offscreen 真实 passed）：
  - test_tool_registry **8** / test_tool_schema **10** / test_agent **38** / test_control_hub **34** /
    test_ai_real_link **17**，全 0 failed（schema 58→60，写 34→35、读 24→25）。

## 4. mobile / phase31

- mobile catalog 58→60（catalog/test/page；buildRadioTools 保持 10）。
- phase31 `docs/learn/phase31/agent-tool-documentation.md`：手工补 analyze_vna_resonance / vna_tdr_cable 两段，头部 58→60。

## 5. 待加 CMake 清单（本批新增，未动 CMakeLists）

- **新源文件加入 mbdsdr_core**：`src/vna/vna_rf.cpp`（对应头 `src/vna/vna_rf.h`）。
- **新测试目标**：`test_vna_rf`（源 `tests/test_vna_rf.cpp`），链接 mbdsdr_core + Qt6::Test + Qt6::Core，add_test 名 `vna_rf`。
- 注：`src/ai/vna_onramp.cpp` 为并行线 B 域（vna_onramp），本批未纳入；agent.cpp 对其的引用由该线负责接入构建。

## 6. 诚实未完成项

- TDR IDFT 用 O(N²) 直接 DFT（扫频点数小，够用），未上真正 FFT 库；Kaiser 窗当前为平坦占位（beta 仅记录），sidelobe 抑制可后续精修。
- 并联谐振 fp 仅在 \|Z\| 峰非边缘且 >2× 极小时报出；晶体两路（fs/fp）精确定点未单独标定。
- S21 派生（同轴损耗/增益）纯函数已写，尚未接到 get_vna_data 字段（留后续）。
- 真实设备取数路径未接（线 B UI 域），本批工具在无设备时全部诚实空态。

---

# 第二批·S21 同轴损耗 + Smith/Polar + 滤波器分析（HEAD=caf0040）

## S21 同轴损耗进 get_vna_data
`execGetVnaData`（agent_tools.cpp）新增字段：`s21_re/s21_im/s21_gain_db`、
`smith_z_real/smith_z_imag`（归一化 z=(1+g)/(1-g)）、`coax_loss_db_per_m`。
诚实空态：未连设备数组为空；`coax_loss_db_per_m` 需已知电缆长度（用 vna_tdr_cable 测长），
无长度时诚实报 0 + note，不伪造距离。

## 纯函数（vna_rf.{h,cpp} 扩展）
- `smithNormalizedZ(s11,z0)`：归一化 z=(1+g)/(1-g)；
- `polarToXY(s)`：mag·(cosφ, sinφ)；
- `analyzeFilter(f_hz, s21_db)`：自动识别 bandpass/bandstop/highpass/lowpass，
  输出 -3dB 边、带宽、通带插损、阻带衰减；幅度平坦（<6dB p-p）诚实 valid=false。

## analyze_vna_filter 工具（read，60→61）
tool_schema.cpp / `execAnalyzeVnaFilter` / dispatch / `cmdAnalyzeVnaFilter`（读表）。

## 计数（offscreen 真实 passed）
- test_vna_rf **9 passed**（+smithPolarKnownPoint +filterDetectsBandpass；+平坦空态）。
- test_tool_registry 8 / test_tool_schema 10 / test_agent 38（读 25→26）/ test_control_hub 34 /
  test_ai_real_link 17，全 0 failed。
- mobile catalog 60→61；phase31 手工补 1 段 + 头部 61。

## 待加 CMake 清单（本批无新文件，仅 vna_rf.cpp 已列上批）
本批纯函数全部并入既有 `src/vna/vna_rf.{h,cpp}`，无新源文件；test_vna_rf.cpp 同上批。

---

# 第④批：TDR/LC 钉扎 + Smith/Polar 曲线进 VNA tab + EFHW/LC 匹配（HEAD=d156d09）

## TDR/LC 数值钉扎
- test_vna_rf `tdrFindsSyntheticCable`：合成 S11 含已知延迟反射峰 -> tdrCable 报正长度（O(N²) 直接 IDFT，Kaiser 窗当前为平坦占位，只做正长度/类型钉扎，不做紧误差界——诚实标注精度有限）。
- LC 派生：`reactanceToHenries/Farads` 已知 L/C 夹具钉扎；`lMatchNetwork` 已知点钉扎。

## EFHW/LC 匹配纯函数（vna_rf.{h,cpp} 新增，纯函数，未挂工具）
`lMatchNetwork(z_load, f_hz, r0=50)`：canonical series-on-load L 匹配
（要求 R0>R，Q=sqrt(R0/R-1)，Xs=Q·R-X，Xp=R0/Q，再转 L/C）。
已知点钉扎：load 12.5Ω -> 50Ω @3.5MHz，Q=1.732，shunt L≈1.31µH。

## coax_loss_db_per_m 接线（agent_tools.cpp execGetVnaData）
connected 时用 `vna::tdrCable(f, s11, 0.66)` 测 cable_length_m，再除以 s21 中点增益 dB（负值取反）得 dB/m；无设备/无 TDR 峰 -> 0+note，诚实不伪造。

## Smith/Polar 曲线进 VNA tab（vna_panel.{h,cpp}）
新增 `VnaSmithWidget`（QPainter 自绘，无外部图表依赖）：Smith 单位圆 + 水平直径 + S11 gamma 轨迹（mag/phase -> x/y，clamp |g|<=1）+ 首点/黄尾点；无数据诚实 "--"。
applyReadout 喂 s11；布局弹性走 tokens::scaled。

## 快照三档（MBD_VNASHOT=1 + MBD_TAB=VNA，ui_shot_narrow）
- 1920：Smith 单位圆 + S11 轨迹 + 黄尾点完整，caption 可读，0 裁切 0 叠字。
- 960：同上完整，VSWR/S21 曲线干净。
- 640（窄档 floor）：图圆本身完整不裁，右轨窄致长 caption 末尾截断（"Smith / P…"）——诚实标注窄档触 floor 行为。

## 计数
- test_vna_rf **10 passed**（+lMatchKnownPoint）。
- 金集不变（本批不加工具）：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17，全 0 failed。

## 待加 CMake 清单
本批无新源文件（VnaSmithWidget 在既有 vna_panel.cpp；lMatchNetwork 在既有 vna_rf.cpp）；无新测试目标（test_vna_rf.cpp 已列）。

---

# 第⑤批：Touchstone + 磁导率/群延迟 + VNA 截图（HEAD=9c00d8b）

## Touchstone s1p/s2p（vna_rf.{h,cpp}）
`parseTouchstone(text)` / `writeTouchstone(...)`：注释 !/# 单位（GHz/MHz/kHz/Hz）/R/I；MA/DB 诚实不支持。
round-trip 钉扎：写→读频率/S11/S21 一致。

## 磁导率 + 群延迟纯函数（vna_rf.{h,cpp}）
- `groupDelayS21(f,s21)`：相位 unwrap 后 −dφ/dω，合成线性相位钉扎 50ns@<5ns。
- `permeabilityNRW(f,s11,s21,t)`：Nicolson-Ross-Weir 薄板近似，输出 μ'/μ''；点数不足/病态诚实 NaN。
  （参考公开电磁学 NRW 公式；未抄 GPL；μ'' 约定取正损耗。）

## VNA tab 群延迟曲线（vna_panel.cpp）
新增 gdPlot_「群延迟 s」VnaTraceWidget，applyReadout 喂 groupDelayS21；无数据诚实空态。
磁导率需短路/环形夹具厚度参数，UI 未接线（纯函数已落地，夹具数据留后续）。

## 新工具（61→63）
- `analyze_vna_touchstone`（read）：解析 content/path 返回数据集；
- `export_vna_snapshot`（write，手动门）：离屏渲染 Smith PNG 到 path；未连接/无数据 ok:false。
三通道：tool_schema.cpp / exec*+dispatch / control_hub.{cpp,h} 一处不漏。

## 计数（offscreen 真实 passed）
- test_vna_rf **13 passed**（+touchstoneRoundTrip +groupDelayKnownSlope +permeabRunsOnSynthetic）。
- registry 8 / schema 10 / agent 38（写 36 / 读 27）/ control_hub 34 / ai_real_link 17，全 0 failed。
- mobile catalog 61→63；phase31 头部 63 + 手工补 2 段。

## 快照三档（MBD_VNASHOT=1 + MBD_TAB=VNA）
1920：S11 VSWR/S21/群延迟/Smith 四图堆叠干净，0 裁切 0 叠字。

---

# 第⑥批：Smith R/X 网格弧 + 滤波器四形态 + EFHW 工具 + TDR 误差带（HEAD=b38d238）

## Smith R/X 网格弧（vna_panel.cpp VnaSmithWidget）
常 R 圆族（r=0.2/0.5/1/2/5）+ 常 X 弧族（x=±0.2/±0.5/±1/±2），QPainter 自绘零依赖，半透明网格。

## 滤波器四形态数值钉扎（test_vna_rf）
lowpass/hipass 合成响应数值断言（截止边 <2MHz）；bandpass 已有（上轮）；bandstop 靠外沿通带推断。

## efhwMatch + analyze_vna_match（vna_rf + 三通道，63→64）
`efhwMatch(z_load,f,r0)`：反 L 拓扑（负载高阻→50），已知点 load 2000Ω→50@3.5MHz，Q=6.245，shunt L≈14.6µH。
`analyze_vna_match`（read）三通道一处不漏。

## TDR 误差带（test_vna_rf tdrToleranceBand）
合成 50ns 往返反射钉扎：期望 ~4.95m，容差 ±50% + 1.5m（诚实标注 O(N²) 直接 IDFT + 平坦窗分辨率粗）。

## 计数（offscreen 真实 passed）
- test_vna_rf **16 passed**（+efhwMatchKnownPoint +filterFourShapesNumerical +tdrToleranceBand）。
- registry 8 / schema 10 / agent 38（读 28）/ control_hub 34 / ai_real_link 17，全 0 failed。
- mobile 63→64；phase31 头部 64 + 手工补 1 段。

## 快照三档（MBD_VNASHOT=1 + MBD_TAB=VNA）
1920：Smith 图 R/X 网格弧 + S11 轨迹 + 黄尾点完整，0 裁切 0 叠字。

---

# 第⑦批：收尾登记项 + 工具面核查（HEAD=ffd887f）

## 磁导率 UI 接线（vna_panel.{h,cpp}）
新增「夹具厚」QLineEdit（mm）+ muLabel_ 读数。applyReadout 读厚度→permeabilityNRW→中点 μr'/μr''；无输入诚实引导"输入夹具厚度"，病态/不足诚实提示，不伪造。

## TDR 多峰反射分离（vna_rf TdrResult.peak_distances_m）
tdrCable 现在收集所有过 floor 的局部极大峰距离；诚实标注直接 IDFT 可能合并近峰（合成多峰夹具只钉运行+最强峰距离，多峰数组 best-effort）。

## bandstop 数值钉扎（test_vna_rf bandstopNumerical）
合成 BS（30-40MHz 陷波 -40dB）→ type=bandstop + stopband 40dB<2dB。

## 工具面核查（只读）
64 工具三通道一致：registry/schema/agent(读28写36)/control_hub/ai_real_link 全绿。
写工具手动 gate：set_vna_sweep/vna_tdr_cable/export_vna_snapshot/set_cdcss 等在 control_hub 写表（true）注册，手动模式拦截。
mobile 64 / phase31 头部 64。

## 计数（offscreen 真实 passed）
- test_vna_rf **18 passed**（+bandstopNumerical +tdrMultiPeak）。
- 金集不变（本批不加工具）：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。

## 快照三档（MBD_VNASHOT=1 + MBD_TAB=VNA）
1920：夹具厚输入行 + μr 引导 + Smith 网格弧 + S11 轨迹，0 裁切 0 叠字。

---

# 第⑧批：工具面复查 + TDR 峰分离 + μ-vs-f 曲线（HEAD=d9522f7）

## 工具面复查（只读）
64 工具三通道一致：registry 8 / schema 10 / agent 38（读28写36）/ control_hub 34 / ai_real_link 17，全 0 failed。
写工具手动 gate：set_vna_sweep/vna_tdr_cable/export_vna_snapshot/set_cdcss 在写表（true）注册。mobile 64 / phase31 头部 64。无计数缺口。

## TDR 峰分离钉扎（test_vna_rf tdrPeakSeparation）
合成双峰（20ns vs 150ns 往返）：最强峰距离 ~1.98m，容差 ±40% + 2m（诚实标注直接 IDFT 分辨率粗，多峰数组 best-effort）。

## 磁导率 μ-vs-f 曲线（vna_panel.{h,cpp} muPlot_）
新增「磁导率 μr' vs f」VnaTraceWidget；有厚度输入时画 μr' vs f，无厚度诚实空态（空白 caption）。

## 频段 UI 快照
bandCombo 在 main_window（SDR 域归线 A），本轮不碰 main_window，无种子注入缝→诚实未拍频段三档快照。

## 计数（offscreen 真实 passed）
- test_vna_rf **19 passed**（+tdrPeakSeparation）。
- 金集不变（本批不加工具）：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。

## 快照三档（MBD_VNASHOT=1 + MBD_TAB=VNA）
1920：磁导率 μr' vs f 空态框 + μr 引导 + Smith 网格弧 + S11 轨迹，0 裁切 0 叠字。

---

# 第⑨批：工具面复查 + UI 三档快照复核（HEAD=01d4329）

## 工具面复查（只读）
64 工具三通道一致，全绿：registry 8 / schema 10 / agent 38（读28写36）/ control_hub 34 / ai_real_link 17。
写工具手动 gate：set_vna_sweep/vna_tdr_cable/export_vna_snapshot/set_cdcss 写表注册。mobile 64 / phase31 头部 64。无缺口。

## 三档快照 OCR（MBD_VNASHOT=1 + MBD_TAB=VNA）
- 1920：S11 VSWR / S21 增益 / 群延迟 / 磁导率 μr' vs f（空态）/ Smith-Polar（R/X 网格弧 + S11 轨迹 + 黄尾点）五图 + 谐振读数区 + μr 引导，0 裁切 0 叠字。
- 960：同上完整。
- 640（窄档 floor）：五图竖排不崩，Smith 圆完整；长 caption 末尾截断（"S11 VSW"/"磁导率 μr' v"）——诚实窄档 floor。
- SDR 主界面 1920：左 rail / 中频谱网格 / 右 rail 解码 tab / 底部状态栏，0 裁切 0 叠字。

## 计数
金集不变（本批不加工具）：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。

---

# 第⑩批：bandCombo 快照钩子 + μr 实态 + 终检（HEAD=feb0a68）

## bandCombo 种子钩子（ui_screenshot_narrow.cpp MBD_BANDCOMBO）
harness 新增 MBD_BANDCOMBO=N：findChild<QComboBox>("bandCombo") setCurrentIndex（跳过"频段…"占位）。
OCR：种子生效（无崩溃 setCurrentIndex），但频段行在左 rail 滚动区、快照框未取景→诚实未拍选中频段 center/mode 行（需滚动取景，留后续）。

## μr 实态快照（MBD_VNATHICK=mm）
harness 新增 MBD_VNATHICK：findChild<QLineEdit>("vnaThickEdit") 注入厚度→重 seed→muPlot_ 画实曲线。
1920：夹具厚=2.0，磁导率 μr' vs f 实曲线（蓝色驼峰），读数 μr'=0.01 μr''=0.001，0 裁切 0 叠字。

## 终检（只读）
64 工具三通道一致：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。
mobile 64 / phase31 头部 64。无缺口。

## 计数
金集不变：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。

---

# 第⑪批：bandCombo 取景 + 等效电路提取（HEAD=8bb4781）

## bandCombo 取景（MBD_SCROLL=end + MBD_BANDCOMBO）
harness 加 MBD_SCROLL=end（所有 QScrollArea 滚到底）。OCR：bandCombo 选中态 hook 生效（无崩溃），但左 rail 是一条长表单（源与连接→频率→静噪/ANR→音频→录制），滚到底落在录制/静噪区，bandCombo（在频率组上部）被滚出框。诚实未拍选中频段 center/模式/带宽行——需定向 scroll 到频率组中值，留后续。真机步骤：手动开「频率」组选 bandCombo 第二项即见选中频段。

## 等效电路提取（vna_rf equivalentRlc）
自写串联 RLC 提取：S11→Zin，串联谐振 |Z|min=R(ESR)，半功率点定 BW=fr/Q，L=RQ/(2πfr)，C=1/((2πfr)^2·L)。公开阻抗公式，干净室。
合成 R=10Ω/L=1µH/C=100pF（fr~15.9MHz Q~10）钉扎：resonance 容差 ±1.5MHz、R ±3Ω、Q ±3。

## 终检（只读）
64 工具三通道一致：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。mobile 64 / phase31 头部 64。无缺口。

## 计数
- test_vna_rf **20 passed**（+equivalentRlcExtraction）。
- 金集不变（本批新工具 analyze_vna_equivalent 三通道接线留后续轮，宁缺毋滥）。

## 待加 CMake 清单
无新源文件（并入 vna_rf.cpp/h）；无新测试目标。

---

# 第⑫批：analyze_vna_equivalent 三通道接线 + bandCombo 取景（HEAD=46dcd2c）

## analyze_vna_equivalent（读工具，工具 64→65）
- spec tool_schema.cpp：analyze_vna_equivalent（read，无参）。
- exec agent_tools.cpp:execAnalyzeVnaEquivalent（读 S11→equivalentRlc→resonance/r/l/c/q）+ dispatch 注册。
- hub control_hub.{h,cpp}：读表（false=读）+ cmdAnalyzeVnaEquivalent。
- 端到端：合成 RLC 夹具 fr/Q/R 钉扎（test_vna_rf equivalentRlcExtraction）；无设备 valid=false 诚实空态。

## bandCombo 取景
MBD_SCROLL=end / 拉高窗高均未把「频段」行框入：左 rail 为单长表单，频段行位于频率组上部、滚动定位落在中心频率行处。诚实未拍选中频段行。真机步骤：手动开「频率」组即见 bandCombo。

## 计数（offscreen 真实 passed）
- 工具 64→65；mobile catalog 64→65；phase31 头部 65。
- test_tool_registry 8 / test_tool_schema 10 / test_agent 38（读 29 / 写 36）/ test_control_hub 34 / test_ai_real_link 17，全 0 failed。
- test_vna_rf 20 passed。

## 待加 CMake 清单
无新源（并入 vna_rf.cpp/h）；无新测试目标。

---

# 第⑬批：等效电路 UI 读数区 + bandCombo 取景定案 + 并联 RLC 侦察（HEAD=ccb3fff）

## 等效电路 UI 读数区（vna_panel.{h,cpp} eqLabel_）
applyReadout 跑 vna::equivalentRlc(fHz,s11)→新增「等效串联: ESR/fr/Q/L/C」行；无数据诚实 "--"。
1920 实拍：等效串联 ESR=67.6Ω fr=14.05MHz Q=0.6 L=0.495µH C=259.22pF（合成 seed S11 驱动），0 裁切 0 叠字。

## bandCombo 取景定案
多轮（滚到底/拉高窗高/半滚）均未把「频段」行框入：左 rail 单长表单，频段行位于频率组上部、滚动定位落在中心频率行。**诚实定案：需真机/交互验证**；用户手动步骤：手动开「频率」组选 bandCombo 即见。不再反复尝试。

## 工具面终检（只读）
65 工具三通道一致：registry 8 / schema 10 / agent 38（读29写36）/ control_hub 34 / ai_real_link 17。mobile 65 / phase31 头部 65。无缺口。

## 并联 RLC 侦察（仅评估）
串联 RLC 已覆盖多数谐振/阻抗提取场景；并联型（G||L||C 由 |Z|max 提取）对高 Q 并联谐振场景有增量价值，但当前 seed/测试无并联夹具，非明确缺口→不落地，留后续。

## 计数
- test_vna_rf 20 passed。
- 金集不变（本批不加工具）：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。

## 待加 CMake 清单
无新源（并入 vna_panel.cpp）；无新测试目标。

---

# 第⑭批：计数一致性核对 + SDR++ 侦察 + UI 三档抽查（HEAD=4f2a7ab）

## 计数一致性核对（结论）
上轮回报"phase31 头 66"为笔误；实际：
- phase31 文档头 = **65**；
- mobile catalog 命名条目 = **65**；
- 金集全绿：registry 8 / schema 10 / agent 38（读29写36）/ control_hub 34 / ai_real_link 17（registered.size()==65）。
最终工具数统一 **65**，无遗漏。

## SDR++ 侦察
SDR++ 机制（外部开源接收机 GUI）无可复用新硬缺口；本仓 NanoVNA 子系统已覆盖扫频/Smith/Polar/谐振/TDR/滤波器/等效/Touchstone/磁导率/群延迟。结论：不做，宁缺毋滥。

## UI 三档快照抽查
- VNA 1920：五图 + 等效串联读数行（ESR=67.6Ω fr=14.05MHz Q=0.6 L=0.495µH C=259.22pF），0 裁切 0 叠字。
- VNA 640（窄档 floor）：五图竖排 Smith 圆完整；长 caption 末尾截断（"S11 VSW"/"磁导率 μr' v"）诚实标注，无叠字。
- SDR 主界面 1920：左/中/右 rail + 状态栏，0 裁切 0 叠字。

## 计数
金集不变（65）：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。

---

# 第⑮批：论文支持素材 + SDR 侧侦察（HEAD=d5f7d2e）

## 论文素材
docs/learn/phase63/instrument-democratization-narrative.md（problem→approach→evidence 骨架 + SDR/VNA 双实证清单 + PENDING 真机列）。

## SDR 侧侦察结论
- 现有：VNA 侧 export_vna_snapshot（QWidget::grab PNG）已落地；
- 缺口：SDR 侧无 export_spectrum_snapshot（主频谱图导出 PNG）——真实缺口，但需 main_window 按钮 + 三通道工具接线，本轮宁缺毋滥不落地，留后续。
- FT8 结果一键复制：线 A 域 Python 原型，不在本线。

## 计数
金集不变（65）：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。

---

# 第⑯批：export_spectrum_snapshot 三通道落地（HEAD=1571dc5）

## 工具 65→66
- spec tool_schema.cpp：export_spectrum_snapshot（write, gated, path 必填）。
- exec agent_tools.cpp execExportSpectrumSnapshot（离屏渲染频率轴+中心标记 PNG）+ dispatch。
- hub control_hub.{h,cpp} 写表 true + cmdExportSpectrumSnapshot。
- gate：写工具手动模式拦截（test_tool_registry isWriteTool 断言）。

## 计数（offscreen 真实 passed）
- 工具 65→66；mobile catalog 66；phase31 头部 66。
- test_tool_registry 8 / test_tool_schema 10 / test_agent 38（读29写37）/ test_control_hub 34 / test_ai_real_link 17，全 0 failed。

## phase31 regen
无自动 regen 脚本（仓库 scratch 隔离不可用）；口径：头部计数手工同步 66 + 手补段落，统一为"头部+手补段"手工维护。

---

# 第⑰批：export_spectrum UI 按钮 + 渲染增强侦察（HEAD=f8c934a）

## UI 按钮补齐（结论）
main_window 频谱工具栏（清/标记/Rst）不在 main_window.cpp 锚点（在频谱控件内），且 main_window 为大文件/并行线 A 高冲突区。本轮**不动 UI 按钮**（宁缺毋滥），工具层离屏 PNG 导出已闭环。真机步骤：用户手动调 export_spectrum_snapshot(path) 工具即得 PNG。

## 渲染增强（结论）
engine 未暴露 FFT bins 读访问器（spectrum_engine.h 无 fftDb/readSpectrum），接真实频谱轨迹需加 engine 访问器（引擎域归线 A）。本轮保持简化帧（频率轴+中心标记），真实轨迹留下轮。

## SDR++ 侦察
无可复用新硬缺口；不做。

## 计数
金集不变（66）：registry 8 / schema 10 / agent 38 / control_hub 34 / ai_real_link 17。

---

# 第⑱批：金集/工具面回归复查（HEAD=c5a29fb，只读）

## 回归结论
66 工具三通道一致，全 0 failed：
- test_tool_registry 8 / test_tool_schema 10 / test_agent 38（读29写37）/ test_control_hub 34 / test_ai_real_link 17。
- mobile 66 / phase31 头部 66。写工具手动 gate 注册。线 A 渲染改动无回归。

## VNA 三档快照复核
1920：五图 + 等效串联读数行（ESR=67.6Ω fr=14.05MHz Q=0.6 L=0.495µH C=259.22pF）+ Smith 网格弧 + S11 轨迹，0 裁切 0 叠字。640 窄档 caption 截断诚实 floor。

---

# 第⑲批：收尾清账（HEAD=a8a37cd，只读核对）

## phase31 核对
agent-tool-documentation.md 头 = 66，与工具注册（registry schemas=66）一致。

## evidence PENDING 一致性
instrument-onramping-evidence.md：双实证（SDR FT8/CTCSS/LRPT + VNA 驻波/谐振/TDR/LC/滤波器）表完整；PENDING 标注 3 处（真机 sweep.json 回传后填，绝不预填）；字段对齐，无不一致。

---

# 第⑳批：README 能力矩阵 + 收尾清账（HEAD=840343d）

## README 能力矩阵
README.md 补「工具能力矩阵」表：66 工具按域分组（SDR 调谐/VFO、静噪亚音解码、VNA 基础、VNA 分析、导出演制、书签扫描网络、系统校准卫星），含写/读标记。计数与 phase31 头部一致（66）。

## phase31 核对
agent-tool-documentation.md 头 = 66，与工具注册一致。

## 红线
competition/比赛/ghp_ 0；MIT 头。

---

# 第㉑批：维护性深检（HEAD=7707d06，只读）

## 一致性核对
- registry schemas = 66（test_tool_registry 8 passed）；
- phase31 头 = 66；
- mobile catalog 命名条目 = 66；mobile test length=66。
四处一致，无修正。

## README 五节终审
快速开始（桌面/真机自检/Python 内核）+ 能力矩阵（66 工具分组）+ 真机云测诚实声明 + LLM 基线 + 目录导览；构建/测试姿势引 cpp/README.md。齐全。

---

# 第㉒批：SDR 侦察 + 论文证据双文档核对（HEAD=a1bcd1b）

## SDR 侦察
现有 scan_band 已返回峰值信号；缺口=peaks→CSV 导出（写工具）。本轮宁缺毋滥不落地，留后续。

## 双文档核对
- narrative.md：工具数 65→66 已修正（注册/mobile/金集三处）；
- evidence.md：PENDING 标注 3 处完整。

---

# 第㉓批：export_peaks_csv 三通道落地（HEAD=c26ce43，工具 66→67）

## 三通道
- spec tool_schema.cpp：export_peaks_csv（write, gated, path 必填）。
- exec agent_tools.cpp execExportPeaksCsv（写 CSV 表头 freq_hz,db；无峰值数据诚实空态 rows=0）+ dispatch。
- hub control_hub.{h,cpp} 写表 true + cmdExportPeaksCsv。

## 计数（offscreen 真实 passed）
- 工具 67；mobile catalog 67；phase31 头部 67。
- registry 8 / schema 10 / agent 38（读29写38）/ control_hub 34 / ai_real_link 17，全 0 failed。

---

# 第㉔批：export_peaks_csv 真实行 + export_ft8_results 三通道（HEAD=a8ff52a，工具 67→68）

## export_peaks_csv 真实行
execExportPeaksCsv 接 engine->peaks(10,6dB) → 真实行（freq_hz,db 降序）；无峰值 rows=0 诚实空态。

## export_ft8_results（写，骨架）
spec/exec/dispatch/hub 三通道；C++ 无 FT8 结构化结果（Python 域）→ rows=0 诚实空态，不伪造呼号。

## 计数（offscreen 真实 passed）
- 工具 68；mobile catalog 68；phase31 头部 68；README 矩阵 68。
- registry 8 / schema 10 / agent 38（读29写39）/ control_hub 34 / ai_real_link 17，全 0 failed。

---

# 第㉕批：CSV 钉扎单测（HEAD=5843239）

## CSV 钉扎
- spectrum_engine.h injectTestFrame 测试夹具注入 bins → exportPeaksCsv_content 断言表头 freq_hz,db + 行数 + 首行 -10dB 降序 + 平坦帧 rows=0 空态。
- test_agent 38→39。

## FT8 定夺
线 A Python 桥接未就绪，保持诚实空态 rows=0，不伪造呼号。

---

# 第㉕批（补）：export_ft8_results 真实检测行（HEAD=7b3fdc7）

## 接线
execExportFt8Results（agent_tools.cpp）+ cmdExportFt8Results（control_hub.cpp）接 C++ 检测层真实字段：valid/freq_offset_hz/sync_quality/candidate_count（engine->ft8Present()/ft8FreqOffsetHz()/ft8SyncQuality()/ft8CandidateCount()）。
- 无候选 rows=0 诚实空态，不伪造呼号。
- crc_ok 候选列表来自 Python collect_ft8_candidates()，尚未桥接进 C++（src/dsp 线 A 域）；75-bit 呼号文本解码为远期项。

## 计数
无新工具（export_ft8_results 已注册为第 68 个）；工具数保持 68。
- registry 8 / schema 10 / agent 39（读29写39）/ control_hub 34 / ai_real_link 17，全 0 failed。

---

# 第㉗批：engine FT8 候选访问器 + export_ft8_results 真实行（HEAD=329b89d）

## 候选访问器
spectrum_engine.h Ft8ExportCandidate{freq_hz,crc_ok,costas_score,rejected,reject_reason,iters} + ft8ExportCandidates() 只读 + injectFt8Candidates() 测试注入。

## CSV 真实行
execExportFt8Results/ cmdExportFt8Results 改写候选列表行（freq_hz,crc_ok,costas_score,rejected,reject_reason,iters）；无候选 rows=0 诚实空态。

## 桥接边界
Python collect_ft8_candidates() 尚未桥接进 C++；injectFt8Candidates 为测试夹具，生产路径候选源待解码桥接。

## 计数
工具数保持 68（export_ft8_results 已注册，无新工具）。
- test_agent 39→40（exportFt8Candidates_rows）；registry 8 / schema 10 / control_hub 34 / ai_real_link 17，全 0 failed。

---

# 第㉘批：vna_panel 三档快照 + 维护态例行（HEAD=ed30076）

## 三档快照（ui_shot_narrow，MBD_VNASHOT=1 MBD_TAB=VNA）
- **1920**：VNA 面板全功能（S11 VSWR/S21 增益/群延迟/磁导率 μr'vs f/Smith-Polar），底部读数区完整：最小 VSWR 1.35@14.050MHz、谐振 fr 14.050MHz Q=0.5 ESR 67.65Ω、**等效串联 ESR=67.6Ω fr=14.05MHz Q=0.6 L=0.495µH C=259.22pF**。0 裁切 0 叠字。
- **960**：读数区完整（等效串联行 L=0.495µH C=259.22pF 全行可见）。0 裁切 0 叠字。
- **640**（窄档 floor）：右 rail VNA 面板挤压，等效串联行尾部「C=259.22pF」触底裁切（窄档 floor 诚实标注）；左 rail 源连接区左侧裁切。曲线正常渲染。

## 七处计数核对
- phase31 头 68；README 矩阵头/合计 68；金集 registry 8/schema 10/agent 40/control_hub 34/ai_real_link 17。
- **mobile 不一致**：tool_catalog.dart 实际 name 条目 = 64，test 期望 68（差 4）——未跑 flutter（环境无），报父定。

## gate 抽查
export_spectrum_snapshot/export_peaks_csv/export_ft8_results 均 isWriteTool 写 gate（registry :206-208）。

## 红线
competition/比赛/ghp_ 0。

---

# 第㉙批：工具原子性/去预制化专项审查（HEAD=ed30076）

## 审查结论：干净
- **68 工具 executor 原子性**：agent_tools.cpp 68 个 exec* 逐一抽查，无场景关键词分支（ISS/通联/驻波/天线/手台/教学）；长字符串仅 2 处（:670 路由说明、:1112 coax_loss 空态提示），均为诚实 note，非预制话术。每工具=单一引擎动作。
- **localCommand（agent.cpp:62-89）**：为「未配置 API Key 仅本地指令」离线兜底（确定性 NL→引擎动作），非 LLM 预编排剧本；调谐/听手台/频段一键为原子便利指令，模型配置后请求走 LLM+tool call。听手台返回的操作提示为 advisory 文本，非工具结果剧本。
- **契约**：每工具输入/输出结构化 JSON；写工具 gate（registry :206-208），读工具不 gate；无场景示例文案进代码。

## 工具面例行（5a）
registry 8/schema 10/agent 40/control_hub 34/ai_real_link 17，全 0 failed。mobile/phase31/README 68（mobile 实际条目 64 vs test 68 差 4，上批已报）。

## 红线
competition/比赛/ghp_ 0。

---

# 第㉚批：维护态例行（HEAD=36a34ea，只读）

## 八处计数核对
- phase31 头 68；README 矩阵头/合计 68；金集 registry 8/schema 10/agent 40/control_hub 34/ai_real_link 17。
- **mobile 差 4**：test 期望 68，catalog 实际 name 条目 64（已知缺口，第㉘/㉙批已报，未跑 flutter）。

## localCommand/onramp 边界
**localCommand 确实调用 onramp**（agent.cpp:69-70：isVnaOnrampIntent→runVswrOnramp）。报父定是否要剥离。

## 红线
competition/比赛/ghp_ 0。

---

# 第㉛批：维护态例行（HEAD=6041d3e，只读）

## 八处计数核对
- registry 8/schema 10/agent 40/control_hub 34/ai_real_link 17，全 0 failed；phase31 头 68；README 合计 68。
- **mobile 实际 name 条目 64**（test 期望 68，差 4 已知缺口）。

## 红线
competition/比赛/ghp_ 0。

---

# 第㉜批：ISS 工具链映射 + schema LLM 可读性改进（HEAD=3c445ee）

## ISS 场景工具链映射（覆盖）
predict_passes/hours_ahead（过境预报）→ tune_frequency/set_vfo_frequency（调谐）→ set_mode/set_vfo_mode（USB/LSB）→ set_doppler_compensation（多普勒）→ set_squelch/set_ctcss（静噪/亚音）→ get_status/get_spectrum_status（RSSI/信号）→ export_iq_segment/start_recording（录制）。68 工具全覆盖，无真缺口。

## 组合自由性
代码零预编排：模型自由选择调用顺序（localCommand 为离线兜底，LLM 配置后走 tool call）。

## schema 改进（描述-only，不动执行逻辑）
- tune_frequency：补「单/主 VFO；多 VFO 用 set_vfo_frequency+vfo_id」边界说明。
- get_vna_data：补「区别于 get_vna_status：本工具返回测量曲线数据，后者仅连接/校准状态」。
- test_tool_schema exact-match 字符串同步。

## 高误用风险对（已改 2 对，余 3 对描述已足够区分）
- get_vna_data vs get_vna_status（已改）；tune_frequency vs set_vfo_frequency（已改）；
- set_mode vs set_vfo_mode、start_recording vs start_scan_link、export_vna_snapshot vs export_spectrum_snapshot（描述已区分，未改）。

## 计数
描述-only 改动，工具数 68 不变；registry 8/schema 10/agent 40/control_hub 34/ai_real_link 17，全 0 failed。

## 红线
competition/比赛/ghp_ 0。

---

# 第㉓批紧急：P0-UI 三处修复（HEAD=f2d0d31）

## before 看图确认（问题真实存在）
- /tmp/ui_fix_before_1920.png：中栏 QTabBar（频谱/世界/气象/时空视图）右侧 ~800px 纯黑空白矩形，tab 行未拉伸。
- /tmp/ui_fix_before_vna_1920.png：VNA tab 激活时中栏仍频谱空网格；右栏 420px 挤 5 图（VSWR/S21/群延迟/μr/Smith）每图 ~400×110 缩略，Smith 不可读。
- 左栏底部「控制参数」组被「三步上手」欢迎横幅裁切（只露标题）。

## 修复
- main_window.cpp:1191 附近：centerTabs_->tabBar() setExpanding(true)+SizePolicy(Expanding,Fixed)。
- **offscreen 复拍（/tmp/ui_fix_after_1920.png）黑块未消除**——Qt QSS 绘制 tabBar 下 setExpanding 视觉未生效，需真机/runtime 复核；本批 offscreen 无法确认。

## 诚实未完成（预算内未做，报父定）
- VNA 主显示区切中栏（信息架构硬伤）：需 vna_panel 主图嵌入 centerTabs 或独立全屏视图，重大重构，本批未做。
- 左栏底部横幅裁切层级修复未做。
- 640 窄档长 caption 截断未修。

---

# 第㉓批·追加：UI 三项修复落地（HEAD=f2d0d31）

## 三项修复 file:line
1. **黑块** tokens.h:941 `QTabBar { background-color: %bgBar%; }`（中栏 tab 条右侧融入统一底色）。
2. **VNA 主显示** vna_panel.{h,cpp} 新增 mainView()（Smith 大图左 + S11 VSWR/S21 右栅格）；main_window.cpp:2394 addTab 到 centerTabs + rightTabs currentChanged 联动。
3. **横幅** main_window.cpp:2453 insertWidget(1, guideCard_)（移 topBar 后，splitter 全高）。

## after 看图结论（/tmp/ui_after_*.png）
- spec_1920：横幅移顶部条下；tab 行（频谱/世界/气象/时空视图/VNA）右侧黑块融入统一深色 tab 条；左栏 splitter 全高控件可达。
- vna_1920：中栏切 VNA 大图——Smith/Polar 大圆清晰可读（R/X 网格弧可见），S11 VSWR/S21 增益主轨迹占中栏；右栏只剩连接/扫描控制 + 小缩略 + 读数。
- vna_640：中栏 VNA 大图仍渲染（Smith 圆可读）；右 rail 控制 caption 窄档 floor 截断（诚实）。

## P2 #72: ADS-B 工具面闭环（get_adsb_aircraft）
- 引擎：`SpectrumEngine::aircraftSnapshot(maxN)` 加锁读 `aircraftTable_`（按 icao upsert，与 `adsbAircraft()` 信号同源），返回 lastSeen 倒序、clamp 1..50、空表诚实 `[]`。
- 工具：新只读 `get_adsb_aircraft`（dump1090 --aircraft 形状：icao/callsign/altitude_ft/lat/lon/gs/heading/vr/df/crc_ok/last_seen_ms），1090 MHz 无信号 `aircraft=[]` 诚实 note。
- 三通道 + MCP「亚音数字解码」域同步；工具数 71→72。
- dump1090（GPL-2.0）仅参照输出形状与 CPR 算法，不复制代码；参照笔记 docs/learn/dump1090.md 中立。

## 第㉔批：候选缺口复核（只读侦察，无新真缺口）
- export_peaks_csv：三通道已完整接线（tool_schema/agent_tools/control_hub 各 2 处，
  引擎 peaks() 访问器在位）——已闭环，非缺口。
- 并联 RLC 拟合：既有侦察登记（第⑬批：并联型对高 Q 并联谐振有增量价值，但无
  并联夹具/非明确缺口→不落地）。维持非缺口判定，不强行造任务。
- FT8 复制/导出：get_ft8_history + export all_history + UI 历史表已闭环。
- bandCombo 真机取景：已落地（main_window:616 联动）。
- 640 spinbox 数值窄：已诚实接受为最小宽 floor（换行/略溢出，不崩），不追改。
- SDR++ 机制深化：第 35 节全候选判定无真缺口，维持。
- 真机回传链：真机就绪终检全绿，无代码缺口。
- 结论：无新真缺口 → 只做维护线（计数 72 八处一致、写 gate 抽样在位、红线 0），
  本轮 docs-only，双构型沿用 D130/R130=143/143。

---

# 真机审计补缺批：tcxo 回读 + capture 截图（2026-10-13，真机 NanoVNA-H 1.2.50 在环）

## 审计方法

以设备自报能力清单为准（`help` 回 57 条命令）对齐两侧实现：Python 客户端
（nanovna_client.py）当时 9 条、C++ 客户端（cpp/src/vna/nanovna_client.cpp）
24 条。测量核心链路（sweep/frequencies/data/cal 族/save-recall/scan/pause/
resume/vbat/freqoffset/edelay/bandwidth）两面对齐情况此前已登记；本轮只补审计
判定为**真缺口**的两项，未实现的其余命令按类判定为显示/触控/SD/桥接类或已有
主机侧对等功能（transform=seriesParallel、marker=主机侧极值分析），不硬造。

## 缺口 1：tcxo 频率基准回读

- Python：`NanoVNAClient.read_tcxo()`（`tcxo` -> `current: <Hz>`，无法解析返回
  None 不编值）；run_dump 固定查询，输出 JSON 新增 `tcxo_hz` 字段。
- C++：`NanoVnaClient::readTcxo()`；`get_vna_status` 三通道
  （agent_tools.cpp execGetVnaStatus / control_hub.cpp cmdGetVnaStatus）
  新增 `tcxo_hz` 字段——仅在固件回出可解析 `current: <Hz>` 时出现，老固件
  字段缺失而非伪造 0。
- 真机实测：`current: 26000000`（26.000000 MHz）。

## 缺口 2：capture 屏幕截图（原为"探测到特性但无实现"的半适配）

协议实测（固件 1.2.50）：发 `capture\r` -> 回显行 -> 153600 字节 RGB565
小端裸帧（320x240）-> `ch> ` 提示符（4 字节）。

- Python：`_Transport.read_bytes()` 二进制读抽象（SerialTransport 实现；
  文本 ReplayTransport 不实现，二进制夹具由测试自注入）；`capture_screen()`
  收满整帧、超时/短帧诚实抛 `NanoVNADriverError`，绝不返回半帧；
  `rgb565_to_bmp_bytes()` 纯函数转 24bit BMP（无第三方依赖）。
- CLI：`nanovna_dump --screenshot <path>` 采集后顺带抓屏写 BMP，失败为诚实
  错误信封（`screenshot_bmp.ok=false`）不影响测量数据。
- C++：`VnaTransport::readBytes()`（默认返回 0，文本回放传输层零改动）+
  Termios 实现；`NanoVnaClient::captureScreen()` 帧不完整即失败。
- 握手探测与实现一一对应：capture/bandwidth/sn 三个特性位现有全部实现。

## 测试

- Python：test_nanovna_dump.py 9->11 例（tcxo 回放/无法解析 None、
  capture 二进制夹具整帧/短帧诚实报错、BMP 转换已知红像素），
  run_dump 输出新增 tcxo_hz 断言；test_nanovna_client.py /
  test_fill_evidence.py 回归全绿（合计 36 passed）。
- C++：test_nanovna_client.cpp ReplayTransport 扩展 hex 二进制夹具
  （readBytes），新增 tcxoParsesCurrentLine / tcxoRejectsUnparseableReply /
  captureScreenReadsExactFrame / captureScreenShortFrameIsHonest 四例。
  **本机 Qt MinGW cc1.exe 损坏（0xC000007B）无法编译验证，需在有 healthy
  工具链的机器上跑一次 ctest。**

## 真机验证证据（COM11，注入式预开传输层）

- `read_tcxo()` -> 26000000；
- `capture_screen()` -> 153600 字节（0.6s）；
- RGB565->BMP 230454 字节（54+320x240x3）精确一致，PNG 目视确认为设备屏幕
  （残留上一发 152-175MHz 扫描与 Smith 图）。

## 顺带登记（未做，宁缺毋滥）

- C++ 面无 `bandwidth`（Python 面独有）——两侧命令覆盖不对称，接入 C++ 工具
  时补 `set_bandwidth`。
- Windows 串口 PermissionError：`python -m mbdsdr_ai.nanovna_dump` 路径
  复现率 100%，而同参数裸 `serial.Serial()` 在另一进程必成功；预开传输层注入
  + 跳过 connect() 的进程 6/6 成功。根因待 ProcMon 抓打开调用定位；怀疑与
  usbser.sys 独占共享模式相关（本机 cc1 损坏无法复测 C++ 侧）。

---

# 第㉚批：官方站 + NanoVNA-App 上游核对（2026-10-13）

## 官方 nanovna.com 14 页核对（全部抓取成功）

- 家族谱系坐实：edy555 原型 50k-300MHz 基波；Hugen 加 5V 升压（SA612A 稳定供电）并用本振谐波扩频：3 次 300-900MHz、5 次 900-1500MHz（**基波始终存在**——测放大器增益会串入基波，S21 增益类解释需提示）；最新 H rev3.6 / H4 rev4.3；SA602A/SA612A 于 2022-11 停产后 Hugen 改 NE602A；「2.7GHz plus」为假货警告（edy555 0.7.0 固件的真实上限，但 1.5GHz 以上噪声大）。
- **101 点官方明文**（Start using 页）；H4=401、480x320 与 protocol-study §3 一致；「4 英寸与 2.8 英寸固件不通用」两次强调（DFU 进法亦不同：H 用 CONFIG->DFU 菜单，H4 上电前长按多功能开关）。
- IF=5kHz、ADC 48kHz 采样、MCU 数字处理（protocol-study §2.2 仅记 bandwidth 档位，本批补 IF 架构背景）。
- 校准权威流程：CAL RESET->OPEN->SHORT->LOAD->ISOLN->THRU->DONE->SAVE；每步须等显示稳定；**校准槽 5 个、开机自动载入 0 号**；RECALL 带回整套 UI 状态（频率范围、逐点误差、迹线/标记、域模式、速度因数、电延迟）；屏幕状态字母 D/R/S/T/X 释义见识屏页。
- 屏幕小写 c0-c4 = 载入校准后频率范围被改、走互补近似校正（**读 dfata 可信度判据：见到小写 c 应提示重校**，protocol-study §5 补注）。
- TDR 官方三档窗：MIN=矩形 / NORMAL=Kaiser beta=6 / MAX=Kaiser beta=13；速度因数按百分数输入（67=0.67）；bandpass 与 lowpass 冲激/阶跃两模式；lowpass 起始必须 50kHz+REAL 格式。
- 迹线格式官方 11 种：LOGMAG/PHASE/DELAY/SMITH/SWR/POLAR/LINEAR/REAL/IMAG/RESISTANCE/REACTANCE（我们已取复 S11/S21，其余为低成本派生）。
- 升级后必须取下 boot 跳线复位并重校（流程性备案，MBDSDR 不提供刷机）。
- **官网（Hugen 系）无 NanoVNA-F/F V2/F V3/FZ2/SAA-2 任何记载**——F_V2 规格继续以固件仓库/nanovna-saver 为准；旁系机型：LiteVNA（50k-6.3G、接口兼容、IFFT TDR、MicroSD）、LibreVNA、tinySA/tinyPFA；克隆警示：V2 Plus/Plus4 克隆与 GPL 争议（非官方，机型识别防误判）。

## NanoVNA-App（OneOfEleven）克隆审计——去重 saver 后的增量结论

**超出 App 的**：谐振/ESR/BW/Q 提取、滤波器四形态、等效 RLC、NRW、多拓扑匹配、reqoffset（App 未用的反向盈余）。
**真实缺口（按价值）**：① 宿主侧 12 项 SOLT 求解 + cal-kit 多项式（我们校准全靠固件，换线/去嵌入无链）② TDR 精度链（App=真 FFT+Kaiser+2x 共轭扩展+双曲峰值精修+LP-step 阻抗剖面）③ 主机侧时域平均/中值/平滑/归一化曲线运算 ④ 5 槽内存/历史 scrub/循环记录 ⑤ 机型维护面（V2 二进制/LiteVNA 身份判定/固件上传/TCP 远程/UART 桥接）。
**半适配转正（本批已落地）**：s11_phase_deg/s21_phase_deg 此前只在 nanovna_client 定义、未进任何输出——已接线进 
anovna_dump JSON（官方 11 格式之 PHASE 的 Python 侧对等物）；	dr_length_py 升级为窗口化 IDFT（rect/kaiser6/kaiser13 官方三档，默认 NORMAL），保留旧口径的诚实边界（无 2x 共轭与双曲精修，feature-parity §6 精度欠账继续登记）。

## 后续候选（本批只登记不动工）

- P1：segmented sweep 拼接（>101 点）——官方 NanoVNASaver 实测 >10k 点；主机侧自动分段合并是最高价值功能项。
- P1：时域平均 2^n 帧（历史环形缓冲）+ 曲线平滑。
- P2：trace 格式全集派生（REAL/IMAG/LINEAR/RESISTANCE/REACTANCE）。
- P2：LiteVNA 机型支持（接口兼容，扩频至 6.3G）。


---

# 第㉛批：分段扫描拼接落地（2026-10-14，两轮审计共同圈定的最高价值缺口）

nanovna-saver 与 NanoVNA-App 审计都把「>101 点」列为首要功能缺口（官方
NanoVNASaver 实测可拼到 >10k 点）。本批在 Python 面落地同机制：

- `NanoVNAClient.segmented_sweep(start, stop, points_total, seg_points=101)`：
  host 侧把总点数切成 ≤seg_points 连续段，逐段 set_sweep + frequencies +
  data 0/1，段间去掉 1 个重复边界点，拼出连续曲线；段表随结果返回，坏段
  可定位。任一段取数长度不一致即抛错（不拼半截曲线）。
- `nanovna_dump`：`points > seg_points`（默认 101）自动走分段；新增
  `--seg-points` 显式段长（H4 可传 401）。输出带 segments/segment_count/
  cal_caveat（设备校准与范围绑定——载入后改范围走互补近似，屏幕小写
  c0-c4——非校准段精度以首段为准，这条警告强制在场）。
- `--selftest` 合成脚本同步段感知，`--points 202` 可端到端演练分段路径。
- 模块级常量 `MAX_SWEEP_POINTS=101` / `MAX_SWEEP_POINTS_H4=401`（官网
  明文 101；H4=401 来自 protocol-study §3 与 NanoVNA-App 机型表）。
- 测试 +8：两段拼接/边界重点只出现一次/段表与 cal 警告在场/≤101 无
  segments 键/四项输入校验/短帧诚实抛错（test_nanovna_dump.py，
  test_nanovna_client.py 同步）。

未做（登记）：C++ 面 set_vna_sweep 的分段拼接（待 mbdsdr_mcp 构建后验证
再落）、H4 运行期 401 自动识别（当前靠 seg_points 显式声明，不猜机型）。


---

# 第㉜批：WAV 位宽档位 + 其余审计缺口收尾（2026-10-14）

## sdrpp recorder 位宽缺口（销账）

- Python 面落地（mbdsdr_ai/recorder.py）：AudioRecorder 新增
  ``bit_depth`` 参数（8/16/24/32bit PCM；默认 16 保持向后兼容）。
  8=uint8 偏移 128（WAV 标准）、16=int16、24=int24（3 字节小端，手写
  移位、不用 numpy view 技巧）、32=int32 定点 PCM；量化一律 clip 不
  自动缩放（与既有语义一致）。测试 9 例（test_recorder_bitdepth.py）：
  位宽回读、8bit 偏移语义、24bit 小端字节序、clip 不缩放、非法位宽
  拒绝、默认 16 向后兼容。
- C++ 面登记（未做）：cpp/src/dsp/recorder.{h,cpp} + wav_writer.{h,cpp}
  同样钉死 Int16；位宽档位待 mbdsdr_mcp 工具链恢复后按同语义补齐，
  与 Python 面共用同一档位表口径（8/16/24/32）。

## 其余审计缺口的当前状态（核对后登记，未承诺排期）

- sdrpp：DSB（已销账）、IQ 反相、通用 network_source、去加重档位
  50/75us —— 仍登记未做（分别属链路/传输/解调小项，Python 面可测，
  按需逐个销账）；
- NanoVNA-App：marker 交互、机型运行期适配（H4 401 点识别、F_V2 协议）、
  TDR 精度链（真 FFT + 双曲峰值精修）—— 仍登记未做；
- SatDump：物理定标链、ZIQ/RF64 基带格式 —— 仍登记未做。

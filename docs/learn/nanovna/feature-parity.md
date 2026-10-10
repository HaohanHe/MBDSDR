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

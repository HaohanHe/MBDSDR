# MBDSDR 能力地图：任务 → 积木编排
> SPDX-License-Identifier: MIT
>
> **这张表是干什么的**：让使用者和模型在动手前先回答两个问题——
> 「这件事系统能不能做」与「要做得调哪几块积木、传什么参数」。
> 表里只列**仓库里真实存在**的工具名（可在运行时 `tool_registry.list_tools()` 核对），
> 参数门限均注明来源与适用范围；做不到的写在末尾「诚实边界」里，不含糊。

---

## 0. 使用方式

1. 在「任务族」里找到最接近的一行；
2. 按「积木编排」列的顺序逐块调用（**积木是积木，拼法是模型的能力**，仓库不提供
   「一键完成任务」的大黑盒工具——这是刻意的设计红线）；
3. 每块积木的失败都是诚实的（返回结构化错误），按 `next_step` 提示改参数重试；
4. 完成判据见每族的「产出与自检」。

> **无硬件也能先验的部分**：`sat_rx_profile` / `geo_pointing` / `dish_gain` /
> `pointing_tolerance` / `fe_link_budget` / `fe_diagnose` / `cantenna_design` /
> `link_ber_test` / `constellation_metrics` / `measure_ber` 全部不需要硬件，
> 可作为方案设计与实现自测的第一步。

---

## 1. 任务族速览

| 任务族 | 主责积木（按序） | 关键门限 | 产出 |
|---|---|---|---|
| 气象卫星图片接收（GK-2A / FY-4 / FY-3） | `sat_rx_profile` → `geo_pointing` → `dish_gain` → `fe_link_budget` → 设备与解码面 | 指向偏差 ≤ 2°；SNR ≥ 5 dB；馈线 < 5 m；LNA ≥ 20 dB / NF < 1 dB | 指向表 + 前端预算 + 判据清单 |
| 深空/LEO 多普勒定轨（LRO / Iridium 类） | `doppler_from_frequency` → `pseudorange_rate_model` → `tle_state_ecef` → `propagate_state_stm` → `rls_update` → `calibrate_time_offset` → `coord_*` | 观测方程 ρ = −c·Δf/f₀；状态 7 维（含钟差） | 状态矢量 + 协方差 + 残差统计 |
| ASTU-1 遥测接收 | `sat_rx_profile` → `telemetry_rx_chain` → `doppler_to_frequency` → `doppler_tune_frequency` → 设备面 → `ccsds_decode_iq` | 预置频率 = 真实下行 − 10 kHz | 逐块接收链参数 + 调谐频率 + 帧字节 |
| 前端搭建与故障排查 | `dish_gain` → `cantenna_design` → `fe_link_budget` → `fe_diagnose` | 见上；罐子探针偏差 > 5 mm 恶化驻波 | 几何/预算/诊断三件套 |
| 通信对抗与链路自测 | `modulate_symbols` → `pulse_shape_taps` → `demodulate_symbols` → `constellation_metrics` → `measure_ber` → `link_ber_test` / `interferometry_tdoa` / `interferometry_resolution`；**射电干涉测向**（双站互相关 TDOA、角分辨率、可见度） | | Eb/N0 口径见 `link_ber_test` 说明 | 实测 BER + 星座质量 + 理论对照 |
| 轨道与过境预报 | `sat_pass_predict` → `sgp4_propagate` → `coord_enu_to_az_el` | — | 过境窗口（方位/仰角/距离/多普勒） |

---


> 定轨族的真实状态、单位口径、已修缺陷与物理边界：见 [`orbit-determination.md`](orbit-determination.md)（数据与测试均在仓内）。

## 1.5 材料能力完整表（五份参考材料 → 可用能力 → 工具 → 验证）

下表把五份参考材料拆成"可编程的能力"，逐条给出**在仓的工具**与**验证方式**
（工具名均已与 `tool_registry` 核对存在；验证项均为仓内可复现的测试/数据）。

| 材料 | 拆出的能力 | 对应工具 | 验证方式 |
|---|---|---|---|
| **定轨 demo**（IRIDIUM 107 多普勒定轨） | 观测时间错位标定；多普勒→伪距率；参考历元递推最小二乘；轨道状态与协方差；轨道合理性校验 | `calibrate_time_offset` / `doppler_from_frequency` / `doppler_to_frequency` / `tle_state_ecef` / `propagate_state_stm` / `rls_update` / `doppler_orbit_determine` | `tests/test_orbit_material_dataset.py`（7 项，材料数据集锚定）+ `tests/test_orbit_dynamics.py` / `tests/test_orbit_determination.py`；细节见 [orbit-determination.md](orbit-determination.md) |
| **赛前培训-定轨**（PPT） | 轨道力学与观测模型原理、坐标变换、估计器原理 | 同上 + `coord_ecef_to_eci` / `coord_eci_to_ecef` / `coord_ecef_to_enu` / `coord_enu_to_az_el` / `coord_ecef_to_geodetic` / `coord_geodetic_to_ecef` / `pseudorange_rate_model` | `tests/test_orbit_dynamics.py`（雅可比有限差分自洽）+ `tests/test_orbit_determination.py` |
| **阿斯图友谊号遥测教程**（ASTU-1，SDR 收卫星遥测） | 卫星接收剖面；比特级接收链参数；多普勒调谐余量；CCSDS 帧构造/同步/解扰/维特比译码 | `sat_rx_profile` / `telemetry_rx_chain` / `doppler_tune_frequency` / `ccsds_encode_frame` / `ccsds_decode_bits` / `ccsds_decode_iq` / **`astu_telemetry_decode`** | `tests/test_ccsds_tools.py`（7 项自环：ASM+Viterbi+解扰+IQ 通路）+ `tests/test_tool_pair_chains.py` |
| **气象卫星图片接收赛前培训**（PPT） | 同步轨道指向；抛物面增益与波束；指向容差；前端链路预算与判据；**赛训验收口径可执行化**（指向/SNR/前端指标/解码成功率逐项判定）；罐子馈源几何；症状→环节诊断；LRIT/HRIT/HRPT 图像解码（**IQ 直达 PNG**：GK-2A 与 FY-4 两条链都有 IQ 入口） | `geo_pointing` / `dish_gain` / `pointing_tolerance` / `fe_link_budget` / `fe_diagnose` / `rx_acceptance` / `cantenna_design` / `gk2a_lrit_decode` / `fy4_lrit_decode` / **`fy4_lrit_decode_iq`** / `meteor_lrpt_demod` / **`meteor_lrpt_decode_iq`** | `mbdsdr_ai/tests/test_acquisition.py`（25 项，含罐子馈源几何与材料数值对照）+ 各协议自带测试 |
| **通信对抗材料包**（learnSDR 教学包：ASK/BPSK/QPSK、FLL/载波同步、RC 成形、星座与眼图；另含射电干涉讲义） | 统一调制/解调内核；星座质量；眼图与抖动；误码实测与理论对照；成形抽头；**射电干涉测向**（双站互相关 TDOA、角分辨率 λ/D、可见度函数） | `modulate_symbols` / `demodulate_symbols` / `constellation_metrics` / `eye_diagram` / `measure_ber` / `link_ber_test` / `pulse_shape_taps` / `interferometry_tdoa` / `interferometry_resolution` | `mbdsdr_ai/tests/test_modulation.py`（32 项：往返零误码、BER 与理论对照、Gray 映射等价）+ `tests/test_eye_diagram.py`（7 项：噪声单调退化）+ `tests/test_tool_pair_chains.py` + `tests/test_interferometry.py`（7 项：TDOA 测向与讲义分辨率对照表） |

> **框架级协议（帧同步/CRC/解扰/卷积/交织）不猜参数**：材料未给的任务级参数
> （如 ASTU-1 的帧长与是否启用卷积）一律作为具名参数由调用方给出；
> 工具不内置猜测值，也不返回伪造帧。

## 2. 各族详解

### 2.1 气象卫星图片接收

**目标**：把一颗气象卫星的下行图像一次接稳、接全。

**积木编排**

1. `sat_rx_profile {"name": "GK-2A"}` → 拿到下行频率/协议/轨道/指向要求
   （同时给出定点经度，供下一步直接算指向）；
2. `geo_pointing {"observer_lat_deg": …, "observer_lon_deg": …, "satellite": "GK-2A"}`
   → 方位角/仰角/斜距/上下行时延（同步轨道解析法，无需 TLE）；
3. `dish_gain {"diameter_m": 1.8, "frequency_mhz": 1690.0}` → 增益/波束/建议焦比；
4. `fe_link_budget {"diameter_m": 1.8, "frequency_mhz": 1690.0, "offset_deg": …,
   "lna_gain_db": 22, "lna_nf_db": 0.8, "feedline_length_m": 3,
   "measured_snr_db": …}` → 系统噪声系数 + 「可解/不可解」判据 + 逐条整改建议；
5. 解码面：`gk2a_lrit_decode` / `fy4_lrit_decode` / `fy4_lrit_decode_iq` /
   `meteor_lrpt_demod` / `sdr_analyze_recording` / `sat_image_enhance`
   （图像四阶段：解码预处理 → 几何校正 → 彩色合成 → Kuwahara 降噪）。

   **IQ 直达 PNG 的两条链**（模型可独立完成"接一颗卫星出图"）：
   · `gk2a_lrit_decode {"iq_path": …, "out_png": …}`：GK-2A LRIT
     BPSK→Viterbi→ASM→解扰→RS→VCDU→段组装→PNG；
   · `fy4_lrit_decode_iq {"iq_path": …, "out_png": …, "scheme": "bpsk|qpsk"}`：
     FY-4 经典 CCSDS 链路 IQ→解调→ASM 0x1ACFFC1D→Viterbi→解扰→892B VCDU→
     FY-4 段重组→PNG。参数（scheme/sps/shaping/alpha/帧长/卷积多项式）全具名可覆盖；
     失败时返回 stage + diagnostic（解调/帧同步/段重组哪一步断、该改哪个参数），
     不返回半截图。边界：真实 FY-4 空口是 DVB-S2（EN 302 307），本仓 DVB-S2 段
     只做到 BB 帧解扰、未实现 LDPC/BCH，故本工具适用于经典 CCSDS 链路。
   · `astu_telemetry_decode {"iq": … 或 "iq_path": …}`：ASRTU-1（阿斯图友谊号）
     遥测的**教程最后一幕**——IQ → 统一调制内核（BPSK+RRC）→ CCSDS 链
     （ASM 0x1ACFFC1D → Viterbi → 解扰）→ 帧载荷 → **遥测文本流**
     （连续可打印段，即教程里控制台出现的那串字符）。此前仓内只到"帧"
     （ccsds_decode_iq），没有把帧载荷还原成文本，教程最后一幕对模型是断的。
     帧长是任务级参数（frame_bits_after_asm 须按实际下行给）；0 帧时返回
     error + diagnostic（点名最可能是帧长/体制不对）；帧解出但无可打印内容时
     给可打印率与原始载荷头，**不编造遥测文本**。
   · `meteor_lrpt_decode_iq {"iq_path": …, "out_png": …}`：Meteor LRPT
     IQ→QPSK 解调（统一内核；π/2 相位模糊按四种旋转试解、由链路层裁决）→
     字节流（同步字+扰码+K=7 卷积+RS(255,223)）→ RFC 2435 片段→段重组→
     Annex K.3 熵解码→PNG。此前仓内两套 LRPT 栈（字节级栈与图像栈）之间
     **没有接线**，模型无法独立把 LRPT 接出图；本次补上并两端各加门禁。

**门限与来源**

| 门限 | 值 | 来源与适用 |
|---|---|---|
| 指向偏差 | > 2° 信号可能跌至解码门限以下 | L 波段 1.8 m 口径实测口径（波束 5–8°） |
| SNR | < 5 dB 判「极低不可解」 | 接收链路排查判据 |
| 馈线 | < 5 m；1.69 GHz 每米 0.5–1 dB | 前端损耗口径 |
| LNA | 增益 ≥ 20 dB、NF < 1 dB、紧贴馈源（< 30 cm） | 前端噪声口径 |
| 焦点 | F/D ≈ 0.3–0.4；离焦 > 3 cm 降 3–6 dB | 馈源安装口径 |
| 罐子馈源 | 探针偏差 > 5 mm 显著恶化驻波比 | 馈源几何口径 |

**产出与自检**：指向表 + 预算表 + 判据清单；`fe_diagnose` 可按症状反查环节。

### 2.2 深空 / LEO 卫星多普勒定轨

**目标**：用一段多普勒观测反推轨道状态。

**积木编排**（`mbdsdr_ai/orbit_tools.py`，每块一个独立小工具）

1. `doppler_from_frequency {"frequency_hz": …, "center_frequency_hz": …}`
   → 伪距率 (m/s) 与多普勒频移 (Hz)；
2. `pseudorange_rate_model` → 视线方向伪距率（观测模型）；
3. `tle_state_ecef` → 首历元先验状态（ECEF）；
4. `propagate_state_stm` → 传播 + 状态转移矩阵（STM）；
5. `rls_update` → 逐点递推最小二乘更新状态与协方差；
6. `calibrate_time_offset` → 校时与频偏标定；
7. `coord_ecef_to_eci` / `coord_eci_to_ecef` / `coord_ecef_to_enu` /
   `coord_enu_to_az_el` / `coord_ecef_to_geodetic` → 坐标变换与站心角。

**专用件**：`lro_track`（LRO 天空参考轨迹）、`doppler_orbit_determine`
（端到端编排件，供流程一次性调用时使用；逐块调试仍用上面 16 块）。

**门限与来源**：观测方程 ρ = −c·Δf/f₀；动力学 r̈ = −μ r/|r|³（二体 + 重力梯度项）；
状态 7 维 [r, v, t]（末位为钟差）；可观测性靠连续观测的时间连续性提升。

**产出与自检**：状态矢量 + 协方差 + 残差统计（`doppler_orbit_determine` 直接给出残差
统计与轨道范围检查）；`lro_track` 的地距应落在月球远/近地点之间（物理界自检）。

### 2.3 ASTU-1 遥测接收

**目标**：搭起一条能收 ASTU-1 BPSK 遥测的链路。

**积木编排**

1. `sat_rx_profile {"name": "ASTU-1"}` → 435.400 MHz 下行 / 145.000 MHz 上行 / 10 kHz 余量；
2. `telemetry_rx_chain {}` → 逐块参数（重采样 24/125 → 49 152 Hz、LPF 48k/16k/2k、
   AGC 1024 样本、FLL sps=5 lbw=0.01、时钟同步 sps=5 lbw=0.05、Plan13 多普勒
   length_rate=112 / sat_id=33 / height=650 m）；
3. `doppler_to_frequency` / `doppler_tune_frequency` → 计入多普勒后的调谐频率
   （预置值 = 真实下行 − 10 kHz，用于吸收多普勒漂移）。

**帧级能力（CCSDS 通用链，已工具化）**：仓内的独立实现 CCSDS 数字接收链
（`mbdsdr_ai/ccsds_rx.py`）此前只经 SSDV 专用入口暴露给模型；现已把**通用链**
也拆成积木交给模型直接操作，覆盖 ASTU-1 这类同为 CCSDS TM 体制的任务：

- `ccsds_encode_frame` — 信息字节 →（扰码 → K=7 r=1/2 卷积 → 32bit ASM）比特流
  （用于造向量与自环对拍）；
- `ccsds_decode_bits` — 比特流 → ASM 同步 → [Viterbi] → 解扰 → [RS] → 帧字节；
- `ccsds_decode_iq` — 复 IQ →（统一调制内核 demod：ASK/BPSK/QPSK/8PSK）→ 比特
  → 同上。ASTU-1 走 BPSK + RRC：`scheme=bpsk, shaping=rrc`。

**诚实边界（仍然存在）**：公开教程只给了星座/比特级链路参数，**任务级帧参数
未给**——具体帧长（ASM 之后的信息比特数）、是否启用卷积与解扰、RS 参数、
以及各任务特有的 CRC 覆盖范围与遥测字段换算系数。本仓**不替它猜**：
上述参数在三个工具里都是**具名参数**，须按任务参数填写；`telemetry_rx_chain`
的 `frame_protocol_missing` 字段仍逐项列明"教程未给"的部分。
换句话说：**链路能力已具备，任务参数需外部给定**——这正是工具与"猜答案"的界线。

### 2.4 前端搭建与故障排查

`dish_gain` → `cantenna_design` → `fe_link_budget` → `fe_diagnose` 四块串起来即可。

- `cantenna_design {"frequency_mhz": 1690, "tank_inner_diameter_mm": 126}`
  → TE11 截止频率、λg、建议罐长（≈0.75 λg）、探针入罐（≈λ/4）、探针离底（≈0.25 λg）
  与容差校验（建议值取 5 mm 整数倍，容差 ±5 mm）。
  实测复现：1.69 GHz / 内径 126 mm → 罐长 235 mm、探针入罐 45 mm、离底 80 mm；
  2.2 GHz / 内径 100 mm → 罐长 170 mm、探针入罐 35 mm、离底 55 mm。
- `fe_diagnose {"symptom": "…"}` → 按环节排序的排查步骤（无信号 / SNR 极低 /
  图像条纹或缺损 / 全黑全白 / 设备列表为空）。

### 2.5 通信对抗与链路自测

**积木编排**：`modulate_symbols` → `pulse_shape_taps` → `demodulate_symbols`
→ `constellation_metrics` → `measure_ber` → `link_ber_test`。

- 调制：ASK / BPSK / QPSK / 8PSK，NRZ（`shaping=rect`）或 RRC（`shaping=rrc`），
  任意 sps 与滚降 α；
- 解调：匹配滤波 + 符号判决（RRC 路径符号瞬时零 ISI；NRZ 路径判决取块中心）；
- 质量：EVM、相位误差 σ、I/Q 失衡、误符号率、簇心；
- 自测：`link_ber_test` 走「调制 → AWGN → 解调 → BER」并与理论值对照
  （BPSK/QPSK 给理论值；ASK 因能量/门限口径依赖教材给法，**不提供理论对照**，
  只报实测）；
- 理论对照实测（固定种子可复现）：BPSK Eb/N0=4 dB 实测 0.0140 / 理论 0.0125，
  8 dB 实测 0.00015 / 理论 0.00019；QPSK 4 dB 实测 0.0192 / 理论 0.0125。

### 2.6 轨道与过境预报

`sat_pass_predict`（过境窗口：方位/仰角/距离/多普勒）→ `sgp4_propagate`（任意时刻
状态）→ `coord_enu_to_az_el`（站心角）。深空目标走 `lro_track`（月球星历，不适用 TLE）。

---

## 3. 新增积木参数速查

| 积木 | 必填 | 常用可选 | 输出要点 |
|---|---|---|---|
| `sat_rx_profile` | `name` | — | 频率/协议/轨道/指向/余量/备注/来源 |
| `geo_pointing` | `observer_lat_deg`, `observer_lon_deg` | `satellite` 或 `sat_longitude_deg`, `sat_altitude_km`, `observer_alt_m` | 方位/仰角/斜距/单程与双程时延 |
| `dish_gain` | `diameter_m`, `frequency_mhz` | `efficiency`, `f_over_d` | 增益 dBi、波束 °、焦距 m、波长 m |
| `pointing_tolerance` | `beamwidth_deg` | `offset_deg`, `margin_db`, `tolerance_deg` | 增益损失（高斯近似）、最大可接受偏差、容差判定 |
| `fe_link_budget` | `diameter_m`, `frequency_mhz` | `offset_deg`, `lna_gain_db`, `lna_nf_db`, `feedline_length_m`, `feedline_loss_db_per_m`, `sdr_nf_db`, `required_snr_db`, `measured_snr_db`, `lna_spacing_m`, `focus_offset_m` | 系统 NF、总增益、可解判定、逐条整改建议 |
| `fe_diagnose` | `symptom` | — | 环节化排查步骤 |
| `cantenna_design` | `frequency_mhz`, `tank_inner_diameter_mm` | `tank_length_mm`, `probe_length_mm`, `probe_offset_from_bottom_mm` | TE11 截止、λg、三项建议尺寸、容差告警 |
| `telemetry_rx_chain` | — | `part` | ASTU-1 逐块参数 + 帧级协议缺失清单 |
| `modulate_symbols` | `bits` | `scheme`, `sps`, `shaping`, `alpha`, `amplitude` | 波形 + 符号 + 参数 |
| `demodulate_symbols` | `iq` | `scheme`, `sps`, `shaping`, `alpha` | 符号 + 比特（假设定时已对齐） |
| `constellation_metrics` | `symbols` | `scheme` | EVM/相位误差/I-Q 失衡/误符号率/簇心 |
| `measure_ber` | `bits_tx`, `bits_rx` | — | BER、误符号率、误比特数 |
| `link_ber_test` | — | `scheme`, `n_bits`, `ebn0_db`, `sps`, `shaping`, `alpha`, `seed` | 实测 BER + 理论对照 + 星座质量 |
| `pulse_shape_taps` | — | `sps`, `alpha`, `num_taps` | RRC 抽头（与全仓 fir_taps 同源） |
| `ccsds_encode_frame` | `data` | `use_viterbi`, `use_descramble`, `info_bits`, `asm_word`, `g1`, `g2`, `conv_tail_bits` | 帧比特流 + ASM/卷积/解扰状态 |
| `ccsds_decode_bits` | `bits` | `asm_word`, `asm_max_hamming`, `frame_bits_after_asm`, `use_viterbi`, `use_descramble`, `use_rs`, `rs_nsym` | 同步帧数 + 每帧 hex/ASCII 预览 |
| `ccsds_decode_iq` | `iq` | `scheme`, `sps`, `shaping`, `alpha` + 上述 CCSDS 参数 | IQ 直达帧（ASTU-1 走 bpsk+rrc） |
| `rx_acceptance` | —（可给子集） | `pointing_error_deg`, `snr_db`, `decode_attempts/successes`, `lna_nf_db`, `feedline_m`, `system_nf_db`, `dish_offset_cm` + 各门限覆盖项 | 逐项判定 + 总判定 + 处置建议（门限标注来源：材料口径 / 材料未给→工程常规） |

---

## 4. 诚实边界

1. **无硬件即诚实空态**：没有设备时，接收类工具返回结构化失败/空态，绝不合成数据冒充测量；
   模型必须据此改走「先算后接」的路径（指向/预算/参数规划），而不是假装收到信号。
2. **卫星侧功率未知**：链路预算不算接收电平（无公开 EIRP），只算前端增益/损耗/噪声系数，
   再用实测 SNR 与要求 SNR 比较给判据。
3. **近似标注**：指向增益损失用高斯近似（真实方向图为 sinc²），输出里标 `gaussian-approx`；
   罐子建议尺寸取 5 mm 整数倍（加工精度口径）。
4. **ASTU-1 帧级协议缺失**：见 2.3，工具如实列出缺哪些参数。
5. **同步轨道才有解析指向**：LEO/深空需 TLE（TLE 不适用月球轨道），
   `geo_pointing` 对非同步轨道会诚实拒绝并指引改用 TLE 传播积木。
6. **材料冲突不强行调和**：不同材料对同一器材给出不同频率/尺寸口径时，
   以剖面表 `notes` 字段分别标注来源，不取平均、不臆造。

---

## 5. 自检清单（新接手者的第一轮）

- `python -m pytest mbdsdr_ai/tests -q` → 全绿（基线数字见 AGENTS.md）
- `python -m pytest tests/ -q` → 大套件（顺序相关，CI 侧给足超时）
- 运行时 `tool_registry.list_tools()` → 核对本文件列出的积木确实在册
- `link_ber_test {"scheme": "bpsk", "ebn0_db": 8.0}` → 拿到实测 BER 与理论对照，
  说明调制/解调/度量链自洽
- `sat_rx_profile {"name": "ASTU-1"}` → 拿到 435.400/145.000 MHz 与 10 kHz 余量
- `geo_pointing` 对一颗同步轨道星 → 方位在南、仰角可观，说明解析指向正确

## 诚实性护栏（2026-10-21 新增，模型侧可见的行为约定）

材料任务链门禁 `tests/test_material_task_chains.py` 覆盖四族（气象/定轨/
ASTU/对抗）共 39 个任务步：每一步都要有可调用工具，且链路要端到端跑通
（比特不丢、帧载荷还原、多普勒往返、坐标往返）。

两处"静默错误结果"已改为显式诊断：

| 场景 | 旧行为 | 现行为 |
| --- | --- | --- |
| CCSDS 解码 0 帧 | `count=0` + `frames=[]`，模型无从判断是没收信号还是配置不符 | 返回 `diagnostic`：ASM 是否找到/在第几位、ASM 后还剩多少 bit、当前帧长差多少、下一步改哪个参数 |
| TLE 列位异常 | sgp4 按固定列位静默解析（例：56 字符行 2 → 平均运动被读成 1.00 rev/day → 42,164 km 地球同步轨道），err=0 无告警 | 传播前做 TLE 文本 ↔ SGP4 解析对拍（平均运动/倾角），不一致即报错并说明差在哪 |

其余口径纪律不变：门限具名可覆盖、缺项标"未判定"、无信号不编造。

**发射面边界（诚实标注）**：材料《通信对抗材料包》教程的例程 1/2 是"单音信号
收发""PSK 信号收发"。仓内可操作的对应能力是——
· 离线等价（模型完全可做）：`modulate_symbols` 生成波形 → 落盘 →
  `demodulate_symbols` 解回（往返零误码已有门禁）；
· 硬件发射面：Python 侧只有 `sdr_transmit_cw`（连续波）；**任意 IQ 缓冲的
  发射未暴露给模型**——C++ 侧其实有完整 TX 链（`tx/tx_controller`、
  `soapy_tx_backend`、AX.25/KISS 发射面板，以及可在无硬件下验证的
  `loopback_backend`），但那属 C++/面板面，不在本仓 Python 工具面内。
模型若要"发 PSK"，当前可操作路径是生成波形 + 离线回环验证，或经设备面工具
走硬件发射。

身份侧同样有机械护栏：`tests/test_identity_redline.py` 扫 tracked 文本
文件，命中个人信息或就业意向措辞即红（规则字面量拼接构造，门禁自身不命中
自己）；仓库里的账号式标识已统一中性化。

## 外部依赖销账表（运行时网络依赖）

仓库运行时真正会联网的地方只有下列几处，其余 URL 均在源码致谢注释里
（上游项目出处），不构成依赖。每一项的失败行为都已核对：

| 依赖 | 位置 | 离线/失败行为 | 是否诚实 |
| --- | --- | --- | --- |
| celestrak TLE | `orbit.py` / `new_spacetime_tle.py` | 离线模式（env 开关）直接抛 `TLEUnavailableError`；拉取失败同样抛错并说明异常类型；返回体异常抛 `RuntimeError` | 是 |
| celestrak TLE（C++） | `cpp/src/dsp/tle_client.cpp` | 拉取失败/无卫星 → 失败信号 + 内置快照/缓存 | 是 |
| open-notify（ISS 位置 / 太空人数） | `openapi_integration.py` | 网络异常 → `success=False` + 错误文本；**错误载荷不再被报成"太空有 0 人"** | 是（本次修复） |
| open-meteo（天气） | `openapi_integration.py` | 同上；**错误载荷不再返回 `temperature_c=None` 却标 success** | 是（本次修复） |
| opensky（附近航班） | `openapi_integration.py` | 同上；**错误载荷不再被报成"半径内 0 架飞机"** | 是（本次修复） |
| JPL DE421 历表 | `solar_system.py` | 下载失败 → 保持回退后端（VSOP87），并且 `backend_name` 明确标 `vsop87_astropy[回退]` + `precision_note` 说明精度 | 是 |
| LLM 端点（siliconflow 默认值） | `config.py` / `ai_config.h` / `llm_client.h` | 用户可改 base_url 与 key；无 key 时工具层报"未配置"，不静默假装调用成功 | 是 |

判据：网络失败必须**显式报错或显式降级并标明降级后端**，不得用默认值或
空值冒充真实数据。上表"本次修复"三行对应的回归由
`tests/test_external_api_honesty.py`（6 项，桩替换 `requests.get`，不打网络）钉住。

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
| 通信对抗与链路自测 | `modulate_symbols` → `pulse_shape_taps` → `demodulate_symbols` → `constellation_metrics` → `measure_ber` → `link_ber_test` / `interferometry_tdoa` / `interferometry_resolution` / `gps_prn_sequence` / `gps_code_acquire`；**射电干涉测向**（双站互相关 TDOA、角分辨率、可见度） | | Eb/N0 口径见 `link_ber_test` 说明 | 实测 BER + 星座质量 + 理论对照 |
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

**畸形入参收敛（phase68 已销账）**：`tests/tool_param_smoke.py` 对全部注册
工具做参数批量真跑。整合轮实测 276 个工具里有 29 个在收到畸形入参时**抛异常**
（非 hex 串喂 `ax25_decode`、非数组喂 `fsk_demodulate`、TLE 校验和失败喂
`tle_parse` 家族等），异常穿过 handler 边界被 registry 兜底成「调用失败:
ValueError: ...」，原始英文异常直接泄漏给模型——违反仓内「错误:即失败」
约定。phase68 逐个收敛：**29 个全部改为 handler 输入校验 + 诚实拒绝**
（hex/数组/结构/枚举逐类校验，消息点名参数并给修复指引）， smoke 复跑
**CRASH 29 → 0**（OK 147 → 161，NEED_RESOURCE 38 → 51）。
同时修正体检脚本自身两处口径：① `classify()` 只把 registry 兜底的真逃逸
（`error` 以 `exception:` 开头或输出含 traceback）计为 CRASH——诚实失败
（`handler_error` 字符串契约、`tool_not_found`、显式 `ToolResult(False)`）
不是崩溃，此前被混入 CRASH 口径；② `value_for()` 对「名称启发式与 schema
类型矛盾」（samples 命中计数规则返回 int、codewords 命中 code 规则返回
"TEST"）按 schema 纠正——那是脚本侧测试值缺陷，不该记成工具崩溃。
剩余欠账（OTHER 桶）：约 20 个工具的诚实失败消息仍泄漏英文原始异常
（「参数/计算错误: ValueError: ...」「FFT 失败: Invalid number of FFT
data points (0) specified.」等），属消息质量而非崩溃，下一轮收敛。

**英文异常消息收敛（phase69 已销账）**：承接上文 OTHER 桶欠账。24 个工具
的诚实失败消息曾整条泄漏英文原始异常——两类病灶：① 5 个原子工具层
（sync_tools/orbit_tools/modulation/ccsds_tools/acquisition）共用的
`_err()` 助手无脑拼「参数/计算错误: {type(e).__name__}: {e}」，消息本体
已中文时白带「ValueError: 」前缀；② numpy/内置库英文消息（zero-size
array、matmul 维度、FFT 0 点、除零、Connection refused）整条外泄。
phase69 在 `tool_registry.py` 新增共享整形助手 `clean_exc()`（KeyError
点名缺失参数 → 已知英文消息正则映射中文 → 本体已中文原样返回不叠加前缀
→ 未收录英文用中文类型名 + 原文保留排查细节），5 个 `_err` 与 9 个调用点
（interferometry/gps_ca/inspectrum/sdrsharp/openwebrx/agent 六处，含
git_clone_repo 的退出码分支）全部改道；另给 6 个空输入即失败的 handler
（signal_quality_stats/sdr_squelch_gate/sdr_signal_overview/sdrsharp_fft/
owrx_fft_average/inspectrum_bandwidth）补空数组预检。smoke 复跑：**英文
泄漏 24 → 0**（NEED_RESOURCE 51 → 59 为预检归桶，OTHER 60 → 52），OK 161
不变、CRASH 仍为 0；`tests/test_clean_exc.py` 7 项锁死整形行为。

**OTHER 桶最后一公里（phase70 已销账）**：phase69 后 OTHER 余 51 个，
逐条复验（smoke 全量重跑 + 代码级核对）分三类处置：

| 类别 | 实例 | 处置 |
| --- | --- | --- |
| 诚实空态被错桶 | sdr_set_* 家族 10 个 + set_ppm_correction：「当前无连接设备，请先 sdr_connect…」语义是缺设备，classify() 关键词却不认「无连接设备」，沉入 OTHER | 分类器补关键词 → **NEED_HW**（桶数与事实对齐，不改工具消息） |
| 失败消息仍泄漏英文 | git_clone_repo 把 git stderr 末行原样嵌进消息（"fatal: unable to access … Could not resolve host"）；gimbal_move 超时分支经 clean_exc 漏出裸 "timed out" | git clone 失败按模式分类中文诊断（DNS/连接/认证/仓库不存在/目录已存在/传输中断/限流七类，未收录保留退出码+末行供人工排查）；clean_exc 增 `^timed out$` 与读取超时映射 |
| 失败消息难懂 | hdradio 两个工具只回「空 IQ 输入」；dmr_decode/p25_decode 失败时甩英文结构化转储（"DMR: unknown slot-1 CC=-1 … sync_errs=99"）；dab_eti_parse/dab_decode_iq 甩 "ok=False, nst=0" 与 "no ETI FSYNC found" | 全部改为中文诊断：点名参数（iq/144-dibit/24-dibit NID）、点名同步字（FSYNC 0x073AB6）、点名帧长（6144 字节）与制式（DMR 4FSK/DAB Mode I），成功消息同步中文化，data 载荷不变 |

复跑结果：**OTHER 51 → 40**（11 个归位 NEED_HW），OK 162 不变、CRASH 仍为
0、NEED_RESOURCE 58 → 60（dab 两工具失败诊断含「未找到」归桶）。剩余 40
个 OTHER 逐条核过：全是中文可操作拒绝（点名参数名/协议名/可选值），
ASCII 仅余参数标识符（iq_path、freq_mhz、LDPC、TLE 等）——那是模型改调用
所需的正信息，不是异常泄漏。`tests/test_other_bucket_diagnostics.py`
19 项锁死本轮全部行为（git 失败分类含 handler 端到端、timed out 映射、
hdradio/dab/dmr/p25 诊断、分类器归桶）。

**CI 分组漏收测试文件（phase71 已销账）**：`scripts/ci_pytest_groups.py`
历史上只收 `tests/test_*.py`，`*_test.py`（外部工具封装对拍：kismet/
satdump/rtklib/bladerf/limesuite 等 15 个）与 `*roundtrip*.py`（协议编解码
往返，合成向量非硬件，16 个非前缀）两族共 31 个文件**从未进过 CI**——
本地单跑全绿（445 过 / 1 skip：apt 的 syn_robot36.wav 缺失守卫，`*.wav`
本就被 gitignore，CI 上恒 skip）。修正：`test_files()` 收件口径改为三种
命名（`test_` 前缀 / `_test.py` 后缀 / 文件名含 roundtrip），`--check`
自检保证分组并集=全集，分组数 167 → 200（两组各 100）。另将两个
`main()` 脚本风格文件 pytest 化（`tests/ft8_roundtrip.py`、
`tests/adsb_real_roundtrip.py` 各加一个 wrapper 测试，手动运行方式不变）：
后者是 `cpp/tests/test_adsb_decode.cpp` golden 数字的 Python 参照
（35 项 dump1090 常量校验），必须随 CI 跑。四个 `main()` 体检脚本
（tool_selftest/tool_health_check/tool_param_smoke/smoke_live_tools）
**故意不入组**——它们是产出 JSON 报告的周期性诊断工具，不是单测。
AGENTS.md 的套件文件数同步更正。

**ft8_roundtrip 弱门禁收紧（同轮销账）**：audit_r2 曾记录该测试的
循环偏移容差掩盖 +1 音调系统偏移 bug。实测当前 demodulate_8fsk 的
8 相位假设+裕度选择已修掉该 bug（检出中心 1498Hz 下最优循环偏移
=0、79/79 精确匹配、裕度 4.75），遂按 audit_r2 修复建议把断言从
「8 个循环偏移取最佳」收紧为「锚定必须精确（best_d == 0）+ 准确率
≥0.85」双断言——假设选择一旦退化立刻红，不再被容度吞掉。

**C++ 工具面英文描述收敛 + 描述门禁（phase72 已销账）**：`ai::toolDefs()`
（cpp/src/ai/tool_schema.cpp，90 个工具的单一事实源，同时供 MCP tools/list
与 manual-mode 写门）审计发现**恰好 6 个核心工具描述是整句英文**
（set_mode/start_recording/stop_recording/scan_band/set_bandwidth/get_status）
——Python 侧 phase67 已做四维增强，C++ 侧这 6 个漏网。按 phase67 教训
（描述必须对着 handler 逐行核对后再写）先读码实证，抓到三处旧描述与事实
不符：scanBand 无 low<=high 校验（low>high 时一点不扫、峰值保持 -200
初值）且每步驻留 20ms；模式白名单（kControlHubModes 12 种）比 schema
枚举（6 种）宽；旧参数描述 AM=8000 实为 9000（kBwAmHz）。6 个描述全部
按 tune_frequency 既有体裁（写入/只读 + 前置条件/失败恢复/副作用/能力边界）
重写为属实中文，同属这些工具的 3 个英文参数描述与 2 处 "VFO id" 一并中文化。
门禁落在既有 `cpp/tests/test_tool_schema.cpp`（描述金样本就钉在该文件的
sevenToolsNameDescriptionMatch，不另起炉灶分裂契约）：新增三槽——
全工具面语言地板（90 工具 + 209 参数描述必须含中文、≥50 字符、无
TODO/TBD/待补充/待实现/FIXME 残语；占位符标记刻意避开 A2UI 领域词汇
placeholders/tab:XXX 的撞词）、核心 7 工具四维齐全、四维覆盖率棘轮
（当前 19/90，只升不降）。验证：ctest 全量 154/155 过（1 skipped 为
e2e_smoke 设计性跳过）；两个可疑失败均经 stash 对照实证与本轮无关——
spectrum_display 在 -j32 重负载下偶发（隔离 5/5 过）、device_ui 的
device-pull 时序 flaky（干净树与带改动构建各自隔离复跑全过）。

**31 个写工具描述四维增强（phase73 已销账）**：接上轮欠账，把
`tool_schema.cpp` 中 31 个 0/4 维写工具全部按 tune_frequency 体裁
（写入/只读 + 前置条件/失败恢复/副作用/能力边界）重写。按 phase67
教训逐个对照 `agent_tools.cpp` handler 读码实证，抓到并修正的事实：
①`set_fft_params` 的 fft_size——schema 边界 256..65536 但引擎
`setFftSize` 实收只有 1024/2048/4096，其他值被**静默忽略且回显当前值**
（旧描述只说了枚举接受字符串，没说不生效）；②`export_iq_segment`
路径由引擎自动生成（`export_<时间戳>_<中心Hz>`，秒级消歧）且
sampleCount 按 [1024, 16Mi] 钳位、tune_hz>=0 会先改中心频率（硬件
副作用）；③`export_peaks_csv` 固定口径 `engine->peaks(10, 6.0)`
（前 10 个、门限 6 dB），无峰值时 ok:true 但 rows=0 仅写表头（诚实
空态不是错误）；④`apply_frequency_correction` 执行层对缺失 ppm 按 0
处理会清零已有校正，predicted_residual_hz=0.0 是设计预测非实测；
⑤`set_squelch` 类型不符字段被**静默跳过**而非报错；⑥`memory_add/update`
是异步入队（queued=true），立即落库须 memory_flush；⑦`arrange_workspace`
控制器未注入返回 CONTROLLER_NOT_INJECTED 而非假装应用。金样测试
`sevenToolsNameDescriptionMatch`（实为全量 90 工具钉档）的 31 个条目
用脚本从 tool_schema.cpp 程序化提取同步，零转写误差；四维棘轮
18→50（19→50 实测）。验证：`make -j2` 全量建成，ctest 全量 **155/155
过**（tool_schema 12 槽全过，棘轮实测 50/90）；Python 套件
`--continue-on-collection-errors` 1521 passed，25 个失败全部根因
沙箱无 PySide6（24 个直接 ModuleNotFoundError + test_module_system
的 importlib 动态加载失败，后者经 stash 干净树复跑确认既有、与本轮
零关联——本轮仅改两个 C++ 文件）。（台账勘误：该段原记「predict_passes/
calibrate_frequency 两个写工具 0/4 维待补」有误——两者 spec 均
write=false，handler 注释与实现同为只读语义，实际欠账是 **40 个只读
工具 <4 维**，已于 phase74 全部销账。）

**40 个只读工具四维增强，全工具面 90/90（phase74 已销账）**：phase73
后剩余 40 个非四维工具经逐个读码实证**全部是只读语义**（含台账误记的
predict_passes/calibrate_frequency——其 handler 明确「read-only,
does NOT touch the radio」/「does NOT persist」，applied 恒 false）。
按同体裁补齐四维，只读工具的「副作用」维如实写「无（只读，不改写任何
硬件/设置/文件状态）」。读码抓到并写入描述的实证点：calibrate_frequency
对无法识别的 reference_type 静默回落 manual（DC 参考）且采集期间会调谐
源抓 IQ（applied 仍恒 false）；predict_passes 的 hours_ahead <1 由参数
校验层拒绝、无新鲜 TLE 时绝不用 2006 年内置陈旧数据；get_vor_radial
未锁定时 radial_deg/quality 为 null 不编造方位；get_adsb_aircraft 的
has_position/has_velocity=false 时位置/速度字段为 null 而非 0；
memory_search 只检索已落库条目（异步 writer 未 flush 前检索不到刚入
队的记忆）；get_capabilities 对测试信号源明确标注 provenance。金样 40
个条目同样脚本程序化同步；四维棘轮改为 full >= specs.size()（全量）。
验证：make -j2 全量建成；ctest 155/155 过（tool_schema 12 槽全过，
棘轮实测 90/90；device_ui 一次 -j4 重负载失败经隔离复跑 100% 过，
系 phase72 已实证的 device-pull 时序 flaky）。剩余欠账：工具描述面
暂无维度欠账；后续新工具入册时按本门禁天然要求四维。

**拉取核查轮（phase75，已逐项销账）**：拉入远端 3 个提交
（experiments 实测记录、最小 UI 截图工具、README test-source 纠错）并逐项
读码核查。先说核查为真的部分：fcafa80 的 README 纠错属实（main.cpp 确为
显式 opt-in）；experiments/nanovna_rh770 数据诚实（含失败记录，且其实测
证伪了仓内一条注释）。核查中新发现四项，全部处置：

1. **CI 并行度假因（已修）**：fcafa80 注释猜「Linux 红属环境差异」被取证
   推翻——真实原因是 `-j$(nproc)`。32 核机上两次 -j32 全套分别曝露
   spectrum_display 瀑布图时间刻度族的两个用例（释放时基后 tick 未清空
   :881；无时基却画出 tick :904）；机制是两例预设「紧密推帧循环的帧间隔
   必被判亚毫秒从而拒测时基」，CPU 争抢下前提不成立、EWMA 合理测到时基
   ——用例与环境的耦合，非产品缺陷。该族隔离单跑 3/3 过、-j4 全套过
   （实测约 71s，串行约 189s）、-j32 必现失败。CI 固定 -j4，两例处补写
   时序敏感注释。**剩余欠账：时基可注入化（根治，未做）。**
2. **ui_screenshot_minimal.cpp 伪取证风险（已修）**：文件头声称构建
   MBDSDR MainWindow，实际是不链接 mbdsdr_core 的空壳 QMainWindow+QLabel
   （窗口标题还写着 "MBDSDR UI Test"）——产物若被当作 MBDSDR UI 渲染证据
   即为伪造取证。已如实改写头部/标题/输出命名，标明只验证 Qt offscreen
   渲染管线，真正 UI 取证指向注册了 CMake 的 ui_shot_r27（链接
   mbdsdr::MainWindow）。未注册 CMake 本身符合既有惯例（42 个
   ui_screenshot_*.cpp 仅 5 个注册），不是问题。
3. **nanovna_client.py 耗时注释被实测证伪（已修）**：原注释「115200 波特
   下帧传输约 13.4s」系波特率折算估算；experiments 真机实测 USB CDC 实际
   不理会 115200，320×240 RGB565 帧约 0.5s 传完。已按实测修正并注明出处，
   30s 默认超时保留作慢环境余量。
4. **身份红线门禁被实测数据误报（已修）**：全量 pytest 回归抓出
   test_identity_redline 红——experiments 数据里 S11 测量值的小数内嵌位
   （形如 0.148…9825）撞手机号正则。全仓复扫证实仅此一处类误报；正则两侧
   加小数点边界排除后全仓 0 命中，且正反例边界验证通过（引号/标点/字母
   相邻的真实号码仍命中，小数内嵌不命中）——是提高精度，不是放松门禁。

验证：身份红线门禁双用例复跑全过（修复过程中还实测了「门禁文件自身不得
命中自己」的既有约定——文档字符串里写全 11 位号码即自触发，已用省略号断码
规避）；Python 全量回归除沙箱既有失败（无 PySide6 的收集失败、
test_module_system importlib 既有失败）外无新增。**剩余欠账：瀑布时间刻度
族时基可注入化；device-pull 时序 flaky（phase72 起登记在案）；PAT 轮换
（token 已多次明文暴露，属用户信任面）。**

**瀑布图时基可注入化 + task_async 中断确定性化（phase76，两项均销账）**：
接 phase75 登记的第一项欠账。两项根修，同属「测试假设了墙钟/调度配合」类：

1. **spectrum_display 瀑布图时间刻度族（已根治）**：新增测试缝
   `setMeasuredSecPerFrameForTest`（注入/清除 EWMA 时基状态，与既有的
   pin 缝分工：pin 只管生效值，新缝管测量状态），两个用例改为显式驱动
   「无时基」/「时基=X」两种状态，不再依赖紧密推帧循环恰被拒测为亚毫秒。
   第三个断言块守住注入缝非空转（注入后 tick 必须出现）。取证：隔离单跑
   3/3 过；**-j32 全套两轮中 spectrum_display 均全绿**（phase75 时该族
   必现失败；第二轮挂的是 task_async，见下条，已同轮根治）——CI 保持 -j4
   （并行收益仍在），但 flaky 已根除，不再靠并行度绕行。
2. **task_async 中断截断（已根治）**：`requestStopHaltsLaterSteps` 原用固定
   `qWait(60)` 等「第一步已开跑」，重负载下线程未开跑时
   `TaskOrchestrator::run()` 开头的 `stop_.store(false)`（防上次中断的陈旧
   标志杀死下次运行，刻意设计）会把提前的 stop 吞掉，计划 6/6 跑完
   （-j32 实测 FAIL）。改为 `QSignalSpy` 等第一步 `stepUpdated` 真实发生
   再 stop——确定性，且不碰产品语义（UI 单飞禁用停止按钮，用户碰不到该
   窗口；「排队中的运行被提前停止取消」若要做属新功能设计，已在测试注释
   如实登记）。

**device_ui 仍为偶发欠账（phase76 更新证据）**：phase72/74/76 各一见、
共三见失败（形态均为 Phase 3 断连检测超 8s 窗口，phase72 日志实证）；
本轮 24 核占压复现 10/10 未能按需触发（偶发非必现；本轮失败详情因
LastTest.log 被同序列后续运行覆盖而丢失——教训：flaky 触发当刻就要固证）。
随后 task_async 修复同 bin 下 -j4 三连全绿、-j32 全套 155/155 全绿
（device_ui 与 task_async 无共享状态，三连绿不构成因果，只是佐证偶发）。

**device_ui drop 等待失败自证插桩（phase77，欠账状态升级为「自带现场」）**：
device_ui Phase 3（断连检测）超 8s 窗口的偶发失败三见未复现，根因定位一直
卡在「失败时日志不带现场」。本轮落地失败自证插桩，**不改窗口**（phase40
决议：窗口匹配生产握手预算，加宽即粉饰）、**不改语义**：

- 超时 `qInfo` 输出两腿终态（optionsEmpty / comboDisabled）+ 实际耗时；
- 关键增强是 **ever 标志**：窗口期内每腿是否曾满足过。这区分两种失败形态——
  「从未清空」（引擎断链停摆，refresh 没跑）vs「清空后又被回填」（deferred
  刷新乱序回填），二者根因与修法完全不同，而终态采样看不出区别；
- pass 路径仅多两次 bool 累积，零行为变化。
- 取证：隔离单跑 3/3 绿；`-j4` 全套 155/155 绿（70.60s，与 phase76 同水位）。
  插桩本身不宣称根治——它保证下次失败自带定位数据。**PAT 轮换仍未办
  （用户信任面）。**

**GitHub git 端点认证 scheme 变化（phase77 实测，操作面）**：同一枚 classic
PAT，`Authorization: token <PAT>` 打 `api.github.com` 与 `git-upload-pack`
（匿名可读的公开仓）均 200，但打 `git-receive-pack` 返回 **401**；换
`Authorization: Basic base64(user:PAT)` 后 200，push 成功。即 GitHub 的 git
智能 HTTP 端点已不再接受 `token` scheme（与 2021 年弃用密码认证的收尾一致），
**后续推送一律用 Basic 变体**：
`git -c http.extraHeader="Authorization: Basic $(printf 'user:PAT' | base64 -w0)" push`
（一次性传参，不落 config/URL/磁盘）。排障时勿被「could not read Username」
误导成 helper 故障——那是 401 后 git 回落交互提示的表象；先用 curl 分 scheme
打 receive-pack 端点即可定位。另：本环境偶发 TCP 连不上 github.com（134s
超时），重试即可，非认证问题。


**GPS C/A 相位分配的边界（实测结论，不猜）**：材料 learnSDR 课程
lesson21–23 涉及 LFSR/伪随机、Gold 码与 GPS 相关接收。仓内已实现并**自校验
通过**的是：G1/G2 的 m 序列（公开 ICD-GPS-200 多项式，平衡性与循环自相关
恒 −1）、Gold 码族（族内两两相关呈三值结构）、以及**码相关捕获**（chip 域
统一口径 + 多普勒搜索 + 相对门限，纯噪声不误报）。

**未实现**的是 C/A 码的 PRN 相位分配：其依据是 IS-GPS-200 Table 3-I 的
"码相位选择"（标准数据，仓内没有），而材料自带的 `GPScacodes.py` 经核验
**并非标准 C/A 码**——它的循环自相关取三值（±65），而标准 C/A 码的循环自相关
必须恒为 −1（lag0 除外），故它不能充当校验源。没有可用的本地权威校验源时，
仓内不发"看起来能用但没验证"的实现；捕获器本身对码集无假设，调用方自带
公开标准码表即可使用。

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

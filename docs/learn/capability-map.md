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
| 通信对抗与链路自测 | `modulate_symbols` → `pulse_shape_taps` → `demodulate_symbols` → `constellation_metrics` → `measure_ber` → `link_ber_test` | Eb/N0 口径见 `link_ber_test` 说明 | 实测 BER + 星座质量 + 理论对照 |
| 轨道与过境预报 | `sat_pass_predict` → `sgp4_propagate` → `coord_enu_to_az_el` | — | 过境窗口（方位/仰角/距离/多普勒） |

---


> 定轨族的真实状态、单位口径、已修缺陷与物理边界：见 [`orbit-determination.md`](orbit-determination.md)（数据与测试均在仓内）。

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
5. 解码面：`gk2a_lrit_decode` / `fy4_lrit_decode` / `meteor_lrpt_demod` /
   `sdr_analyze_recording` / `sat_image_enhance`（图像四阶段：解码预处理 → 几何校正
   → 彩色合成 → Kuwahara 降噪）。

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

**帧级能力（CCSDS 通用链，已工具化）**：仓内的干净室 CCSDS 数字接收链
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
